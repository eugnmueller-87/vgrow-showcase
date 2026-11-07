"""The plant card's bloom start and one height per day (owner, 23.10.2026).

"Stage ist bestimmt nicht ein Tag": the camera put the bloom start on 22.10. only because the photo reading began that
day; the autoflower is on day 43 and has very likely flowered longer, which also moves the feeding week. And "Height
measured muss immer auf den Tag geupdatet werden": the log held three identical 61 cm entries for 22.10.

Built: PlantLog.set_bloom_start (the field "Blütebeginn", two taps on the page, action plant_bloom, into the plant log
under STATE_DIR, never into plant.json), which counts before the estimate for the stage, the stage day count, the
feeding week and Hank; PlantLog.add_height replaces an entry of the same day.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hank_eval import fixtures
from vgrow import config, feeding, plant
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.i18n import UserError
from vgrow.plant import PlantLog, current_stage, load_profile
from vgrow.server import COOKIE, make_app

TODAY = fixtures.F22_DATE  # 22.10.2026, the plant is on day 42


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def profile() -> dict[str, Any]:
    found = load_profile()
    assert found is not None
    return found


# ---------- one height per day ----------

def test_a_second_height_on_the_same_day_replaces_the_first() -> None:
    log = PlantLog()
    log.add_height(60, "2026-10-21")
    log.add_height(61, "2026-10-22")
    log.add_height(61.5, "2026-10-22")
    assert log.data["heights"] == [{"cm": 60, "date": "2026-10-21"}, {"cm": 61.5, "date": "2026-10-22"}]
    on_disk = json.loads((config.STATE_DIR / "plant-log.json").read_text(encoding="utf-8"))
    assert on_disk["heights"] == log.data["heights"]


def test_three_identical_entries_of_one_day_load_as_one() -> None:
    path = config.STATE_DIR / "plant-log.json"
    config.atomic_write(path, json.dumps({"heights": [{"cm": 61, "date": "2026-10-22"}] * 3 + [
        {"cm": 58, "date": "2026-10-20"}]}))
    log = PlantLog()
    assert log.data["heights"] == [{"cm": 58, "date": "2026-10-20"}, {"cm": 61, "date": "2026-10-22"}]
    view = plant.view(profile(), log, {}, "de")
    assert view["height"] == {"cm": 61, "date": "2026-10-22"} and view["height_age"] == 0


# ---------- the bloom start ----------

def test_bloom_start_is_checked_and_stored_in_the_plant_log() -> None:
    log = PlantLog()
    start = date(2026, 9, 10)
    with pytest.raises(UserError):
        log.set_bloom_start("gestern", start=start)
    with pytest.raises(UserError):
        log.set_bloom_start("2026-10-30", start=start)  # in the future
    with pytest.raises(UserError):
        log.set_bloom_start("2026-09-01", start=start)  # before this plant's start
    assert log.set_bloom_start("2026-10-01", start=start) == "2026-10-01"
    stored = json.loads((config.STATE_DIR / "plant-log.json").read_text(encoding="utf-8"))["bloom_start"]
    assert stored["date"] == "2026-10-01" and stored["set_at"]
    assert not (config.REPO / "plant.json").read_text(encoding="utf-8").count("2026-10-01")  # never into the repo


def test_without_the_owner_date_the_stage_and_bloom_start_are_the_cameras_estimate() -> None:
    stage = current_stage(profile(), PlantLog())
    assert stage is not None and stage["key"] == "early_flower" and stage["set_by"] == "estimate"
    assert stage["since"] == "2026-10-22" and stage["bloom_by"] == "estimate" and stage["flower_day"] == 0
    bloom = plant.view(profile(), PlantLog(), {}, "de")["bloom"]
    assert bloom["estimated"] is True and bloom["date"] == "2026-10-22"
    assert bloom["note"] == "geschätzt aus dem Kamerabild" and bloom["max"] == "2026-10-22"
    # the camera's date is an upper bound (the first analysed image already showed white pistils), and the stage
    # card's day windows give a rough earlier day (review 1, MUST 8): day 42, early flower from day 21 = 1.10.,
    # today in the mid flower window since day 35 = 15.10.
    assert bloom["latest"] is True
    assert bloom["suggest"] == {"date": "2026-10-01", "stage": "Mitte der Blüte", "stage_key": "mid_flower",
                                "stage_since": "2026-10-15"}


def test_no_suggestion_once_the_owner_gave_the_date_or_before_the_first_flowering_window() -> None:
    log = PlantLog()
    log.set_bloom_start("2026-10-10")
    assert plant.view(profile(), log, {}, "de")["bloom"]["suggest"] is None
    young = profile()
    young["plant"] = {**young["plant"], "start_date": "2026-10-10"}  # day 12: still before early flower (day 21)
    assert plant.bloom_suggestion(young, TODAY) is None


def test_the_estimate_note_in_plant_json_says_upper_bound() -> None:
    setup = profile()["feeding"]
    assert setup["bloom_start_estimate_note"].startswith("Obergrenze, kein Beginn")
    assert "an diesem Tag oder früher" in setup["bloom_start_estimate_note"]
    assert "on that day or earlier" in setup["bloom_start_estimate_note_en"]


def test_a_date_alone_never_makes_the_stage_harvest() -> None:
    log = PlantLog()
    log.set_bloom_start("2026-09-10")  # the run's own start: a slip in the picker (September for October)
    stage = current_stage(profile(), log)
    assert stage is not None and stage["key"] == "late_flower" and stage["set_by"] == "estimate"


def test_the_owner_date_calibrates_the_stage_it_stays_an_estimate() -> None:
    log = PlantLog()
    log.set_bloom_start("2026-10-01")
    stage = current_stage(profile(), log)
    assert stage is not None
    # 21 days of flowering on the card's windows (early flower 21 to 35, mid flower 35 to 49): mid flower
    assert stage["key"] == "mid_flower" and stage["since"] == "2026-10-15" and stage["flower_day"] == 21
    assert stage["set_by"] == "estimate" and stage["estimate_from"] == "bloom" and stage["bloom_by"] == "owner"
    view = plant.view(profile(), log, {}, "de")
    assert view["stage"]["estimate_note"] == ("Geschätzt aus deinem Blütebeginn am 01.10.2026: Mitte der Blüte. "
                                              "Bitte bestätigen oder die Phase setzen.")
    assert view["bloom"] == {"date": "2026-10-01", "by": "owner", "flower_day": 21, "estimated": False,
                             "latest": False, "suggest": None, "min": "2026-09-10", "max": "2026-10-22",
                             "note": "von dir eingetragen"}
    en = plant.view(profile(), log, {}, "en")
    assert en["stage"]["estimate_note"].startswith("Estimated from your flowering start on Oct 1, 2026: ")
    log.set_bloom_start("2026-10-15")  # a few days of flowering: early flower, since that day
    stage = current_stage(profile(), log)
    assert stage is not None and stage["key"] == "early_flower" and stage["since"] == "2026-10-15"


def test_the_owners_own_stage_wins_over_the_bloom_start() -> None:
    log = PlantLog()
    log.set_stage("early_flower", "2026-10-20")
    log.set_bloom_start("2026-10-01")
    stage = current_stage(profile(), log)
    assert stage is not None and stage["key"] == "early_flower" and stage["set_by"] == "manual"
    assert stage["bloom_start"] == "2026-10-01" and stage["bloom_by"] == "owner"


def test_an_owner_date_from_an_earlier_plant_counts_for_nothing() -> None:
    log = PlantLog()
    log.data["bloom_start"] = {"date": "2026-08-01", "set_at": "2026-08-01T10:00:00"}
    stage = current_stage(profile(), log)
    assert stage is not None and stage["bloom_by"] == "estimate"


def test_the_feeding_week_counts_from_the_owner_date() -> None:
    log = PlantLog()
    log.set_bloom_start("2026-10-01")
    prof = profile()
    stage = current_stage(prof, log)
    view = feeding.view(feeding.setups_of(prof)["biobizz"], "de", today=TODAY, stage=stage,
                        owner_stages=plant.owner_stages(prof, log))
    assert view["week_source"]["kind"] == "owner" and view["week_source"]["confirmed"] is True
    assert view["bloom_start"] == "2026-10-01" and view["bloom_estimate_note"] is None
    assert view["week"]["bloom_week"] == 4  # 21 days: 21 // 7 + 1
    # without the owner date the week is the estimate's first one, and no amounts follow
    fresh = PlantLog(config.STATE_DIR / "another-log.json")
    estimate = feeding.view(feeding.setups_of(prof)["biobizz"], "de", today=TODAY,
                            stage=current_stage(prof, fresh))
    assert estimate["week_source"]["kind"] == "estimate" and estimate["week"]["bloom_week"] == 1


def test_the_owner_date_gives_amounts_once_the_card_is_confirmed() -> None:
    prof = profile()
    setup = feeding.setups_of(prof)["biobizz"]
    card = feeding.load_card(setup["card"])
    stage = {"key": "mid_flower", "set_by": "estimate", "bloom_start": "2026-10-01", "bloom_by": "owner"}
    plan = feeding.evaluate(setup, card, True, TODAY, stage)
    assert plan["state"] == "ok" and plan["source"]["kind"] == "owner" and plan["week"]["bloom_week"] == 4
    before = {"key": "veg", "set_by": "manual", "bloom_start": None, "bloom_by": None}
    assert feeding.evaluate(setup, card, True, TODAY, before)["week"]["phase"] == "veg"  # a stage before flowering


def test_plant_bloom_action_through_the_server() -> None:
    log = PlantLog()

    async def run() -> tuple[int, Any, int, Any]:
        app = make_app(DemoBox(), Alerts(), "k", plant_log=log)
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            ok = await client.post("/api/cmd", json={"action": "plant_bloom", "date": "2026-10-01"})
            bad = await client.post("/api/cmd", json={"action": "plant_bloom", "date": "2999-01-01"},
                                    headers={"X-Lang": "en"})
            return ok.status, await ok.json(), bad.status, await bad.json()

    status, body, bad_status, bad = asyncio.run(run())
    assert status == 200 and body == {"ok": True, "date": "2026-10-01"}
    assert bad_status == 400 and bad["error"] == "The flowering start cannot be in the future"
    assert log.data["bloom_start"]["date"] == "2026-10-01"


def test_plant_bloom_is_refused_under_a_stage_before_flowering_the_owner_set() -> None:
    """Review 2, SHOULD 1: the save said "saved" and the card then showed no bloom start."""
    log = PlantLog()
    log.set_stage("veg", "2026-10-20")

    async def run() -> tuple[int, Any]:
        app = make_app(DemoBox(), Alerts(), "k", plant_log=log)
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            got = await client.post("/api/cmd", json={"action": "plant_bloom", "date": "2026-10-01"},
                                    headers={"X-Lang": "en"})
            return got.status, await got.json()

    status, body = asyncio.run(run())
    assert status == 409 and body["error"] == ("You set the stage Vegetative. Set a flowering stage first, then the "
                                               "flowering start.")
    assert log.data["bloom_start"] is None


def test_an_owner_date_stored_under_a_stage_before_flowering_warns_on_the_feeding_card() -> None:
    log = PlantLog()
    log.set_bloom_start("2026-10-01")
    log.set_stage("veg", "2026-10-20")  # set after the date: the date is ignored, and the card says why
    prof = profile()
    stage = current_stage(prof, log)
    assert stage is not None and stage["key"] == "veg" and stage["owner_bloom_start"] == "2026-10-01"
    view = feeding.view(feeding.setups_of(prof)["biobizz"], "de", today=TODAY, stage=stage,
                        owner_stages=plant.owner_stages(prof, log))
    assert view["week_source"]["kind"] == "stage"
    assert view["warning"].startswith("Deine Phase ist Wachstum, in der Pflanzenkarte steht aber ein Blütebeginn am")


def test_the_bloom_start_errors_write_the_date_as_the_page_does() -> None:
    with pytest.raises(UserError, match=r"\(10\.09\.2026\)"):
        PlantLog().set_bloom_start("2026-09-01", start=date(2026, 9, 10))


def test_setting_the_bloom_start_is_journaled() -> None:
    from vgrow.hank import core

    assert "plant_bloom" in core.JOURNAL_ACTIONS
