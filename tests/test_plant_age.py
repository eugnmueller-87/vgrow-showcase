"""The plant's start and age (owner, 23.10.2026: "meine Pflanze ist 5 Wochen alt"): the start the owner gives on the
plant card (a date, or an age in weeks or days, two taps) lives in the plant log under STATE_DIR, never in
plant.json; it counts before plant.json's start_date; it moves the day count, the plant week and the stage card's
fallback windows, never a dose. The age functions compare a stated age with the stored start."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hank_eval import fixtures
from vgrow import config, i18n, plant
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.i18n import UserError
from vgrow.plant import PlantLog, age_check, age_info, age_view, current_stage, effective_start, load_profile
from vgrow.server import COOKIE, make_app

REPO = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)  # 22.10.2026 for date.today() in plant and feeding


def profile() -> dict[str, Any]:
    found = load_profile()
    assert found is not None
    return found


def test_set_start_date_is_stored_in_the_plant_log_and_plant_json_is_untouched() -> None:
    before = (REPO / "plant.json").read_bytes()
    log = PlantLog()
    assert log.set_start_date("2026-09-18", today=TODAY, run_start=date(2026, 9, 10)) == "2026-09-18"
    stored = json.loads((config.STATE_DIR / "plant-log.json").read_text(encoding="utf-8"))["start_date"]
    assert (stored["date"], stored["from"], stored["run"]) == ("2026-09-18", "date", "2026-09-10") and stored["set_at"]
    assert (REPO / "plant.json").read_bytes() == before
    assert PlantLog().data["start_date"]["date"] == "2026-09-18"  # read back from the state folder


def test_set_start_date_input_rules() -> None:
    log = PlantLog()
    for kwargs in ({}, {"value": "2026-09-18", "age_weeks": 5}, {"age_weeks": 5, "age_days": 35}):
        with pytest.raises(UserError, match="Genau eins angeben"):
            log.set_start_date(today=TODAY, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(UserError, match="Kein Datum: 18.09.2026"):
        log.set_start_date("18.09.2026", today=TODAY)
    with pytest.raises(UserError, match="nicht in der Zukunft"):
        log.set_start_date("2026-10-24", today=TODAY)
    with pytest.raises(UserError, match="mehr als 400 Tage"):
        log.set_start_date("2025-09-01", today=TODAY)
    with pytest.raises(UserError, match="zwischen 0 und 52"):
        log.set_start_date(age_weeks=53, today=TODAY)
    for bad in ("fünf", "5.5", -1, True):
        with pytest.raises(UserError):
            log.set_start_date(age_weeks=bad, today=TODAY)
    assert log.set_start_date(age_weeks=5, today=TODAY) == "2026-09-18"
    assert log.data["start_date"]["from"] == "age_weeks"
    assert log.set_start_date(age_days="36", today=TODAY) == "2026-09-17"
    log.data["stages"] = [{"key": "veg", "since": "2026-09-15"}]
    token = i18n.REQUEST_LANG.set("de")
    try:
        with pytest.raises(UserError, match=r"Am 15\.09\.2026 steht ein Eintrag von dir"):
            log.set_start_date("2026-09-18", today=TODAY, current=date(2026, 9, 10))
    finally:
        i18n.REQUEST_LANG.reset(token)


def test_the_start_may_not_cross_an_owner_entry() -> None:
    log = PlantLog()
    log.data["stages"] = [{"key": "veg", "since": "2026-09-15"}]
    with pytest.raises(UserError):  # later: the stage of 15.09. would silently drop out
        log.set_start_date("2026-09-18", today=TODAY, current=date(2026, 9, 10))
    log.data["stages"] = [{"key": "veg", "since": "2026-09-09"}]  # an entry before the current start
    with pytest.raises(UserError):  # earlier: it would count again
        log.set_start_date("2026-09-08", today=TODAY, current=date(2026, 9, 10))
    log.data["stages"] = [{"key": "early_flower", "since": "2026-10-22"}]
    assert log.set_start_date("2026-09-18", today=TODAY, current=date(2026, 9, 10)) == "2026-09-18"  # not crossed
    log.data["bloom_start"] = {"date": "2026-09-20", "set_at": "x"}
    with pytest.raises(UserError):
        log.set_start_date("2026-09-25", today=TODAY, current=date(2026, 9, 18))


def test_a_bloom_start_before_the_new_start_is_refused_by_the_start_not_dropped() -> None:
    log = PlantLog()
    log.set_bloom_start("2026-10-01", start=date(2026, 9, 10))
    with pytest.raises(UserError):
        log.set_start_date("2026-10-05", today=TODAY, current=date(2026, 9, 10))
    assert plant.owner_bloom_start(profile(), log) == date(2026, 10, 1)  # still counts


def test_effective_start_owner_beats_plan_and_moves_day_and_fallback_windows() -> None:
    log = PlantLog()
    assert effective_start(profile(), log) == (date(2026, 9, 10), "plan")
    view = plant.view(profile(), log, {}, "de")
    assert view["day"] == 42 and view["bloom"]["suggest"]["date"] == "2026-10-01"  # windows from 10.09.
    log.set_start_date("2026-09-18", run_start=date(2026, 9, 10))
    assert effective_start(profile(), log) == (date(2026, 9, 18), "owner")
    moved = plant.view(profile(), log, {}, "de")
    assert moved["day"] == 34 and moved["plant"]["start_date"] == "2026-09-18"
    assert moved["plant"]["start_by"] == "owner" and moved["bloom"]["suggest"]["date"] == "2026-10-09"
    assert moved["bloom"]["min"] == "2026-09-18"
    other_run = {**profile(), "plant": {**profile()["plant"], "start_date": "2027-03-01"}}
    assert effective_start(other_run, log) == (date(2027, 3, 1), "plan")  # a new run does not inherit it
    assert plant.with_owner_start(plant.with_owner_start(profile(), log), log)["plant"]["start_date"] == "2026-09-18"


def test_age_info() -> None:
    assert age_info(date(2026, 9, 18), TODAY) == {"days": 35, "weeks": 5, "extra_days": 0, "life_week": 6}
    assert age_info(date(2026, 9, 10), TODAY) == {"days": 43, "weeks": 6, "extra_days": 1, "life_week": 7}
    assert age_info(TODAY, TODAY) == {"days": 0, "weeks": 0, "extra_days": 0, "life_week": 1}
    assert age_info(None, TODAY) is None and age_info(date(2026, 10, 24), TODAY) is None


@pytest.mark.parametrize("stored_days,consistent", [(31, False), (32, True), (35, True), (41, True), (42, False),
                                                    (43, False)])
def test_age_check_weeks(stored_days: int, consistent: bool) -> None:
    stored = date.fromordinal(TODAY.toordinal() - stored_days)
    got = age_check(stored, "weeks", 5, TODAY)
    assert got["consistent"] is consistent and got["implied_start"] == "2026-09-18"
    assert got["diff_days"] == stored_days - 35


def test_age_check_days_life_week_and_no_start() -> None:
    stored = date(2026, 9, 18)  # 35 days
    assert [age_check(stored, "days", n, TODAY)["consistent"] for n in (33, 34, 35, 36, 37)] == [
        False, True, True, True, False]
    assert age_check(stored, "days", 36, TODAY)["implied_start"] == "2026-09-17"
    life = [age_check(date.fromordinal(TODAY.toordinal() - d), "life_week", 5, TODAY)["consistent"]
            for d in (27, 28, 34, 35)]
    assert life == [False, True, True, False]
    assert age_check(stored, "life_week", 6, TODAY)["implied_start"] is None
    unknown = age_check(None, "weeks", 5, TODAY)
    assert unknown["consistent"] is None and unknown["implied_start"] == "2026-09-18"
    worked = age_check(date(2026, 9, 10), "weeks", 5, TODAY)  # the design's example: day 43 against "5 weeks"
    assert (worked["consistent"], worked["implied_start"], worked["diff_days"]) == (False, "2026-09-18", 8)


def test_age_view_states() -> None:
    log = PlantLog()
    approx = age_view(profile(), log, "de", today=TODAY)  # plant.json says "etwa 10.09."
    assert (approx["state"], approx["by"], approx["approx"], approx["ask"]) == ("known", "plan", True,
                                                                               {"reason": "approx"})
    assert approx["line"] == ("Die Pflanze ist etwa 6 Wochen alt (Pflanzenwoche 7), Start etwa 10.09.2026, "
                             "aus plant.json.")  # whole weeks only on a rough start (N4)
    assert approx["ask_text"].startswith("Der Start 10.09.2026 ist nur ungefähr")
    log.set_start_date("2026-09-18", today=TODAY, run_start=date(2026, 9, 10))
    own = age_view(profile(), log, "de", today=TODAY)
    assert (own["by"], own["approx"], own["ask"], own["life_week"]) == ("owner", False, None, 6)
    assert own["line"] == ("Die Pflanze ist 5 Wochen alt (Tag 35, Pflanzenwoche 6), Start 18.09.2026, "
                           "von dir eingetragen.")
    en = age_view(profile(), log, "en", today=TODAY)
    assert en["line"] == "The plant is 5 weeks old (day 35, plant week 6), start Sep 18, 2026, entered by you."
    log.set_start_date(age_weeks=5, today=TODAY, run_start=date(2026, 9, 10))
    weeks = age_view(profile(), log, "de", today=TODAY)
    assert weeks["approx"] is True and weeks["ask"] is None and "als Alter in Wochen" in weeks["line"]
    bare = {**profile(), "plant": {"strain": "x"}}
    none = age_view(bare, PlantLog(config.STATE_DIR / "other.json"), "de", today=TODAY)
    assert (none["state"], none["ask"], none["days"]) == ("unknown", {"reason": "unknown"}, None)
    assert none["ask_text"].startswith("Wann hat die Pflanze gekeimt?")
    assert plant.view(profile(), log, {}, "de")["age"]["by"] == "owner"


def test_the_stage_follows_the_owner_start_only_through_the_day_windows() -> None:
    log = PlantLog()
    bare = {k: v for k, v in profile().items() if k != "stage_estimate"}  # no camera estimate: the day windows
    before = current_stage(bare, log)
    log.set_start_date("2026-10-01", run_start=date(2026, 9, 10))
    after = current_stage(bare, log)
    assert before is not None and after is not None and before["set_by"] == after["set_by"] == "days"
    assert before["key"] != after["key"]  # day 42 against day 21: another fallback window


def test_plant_start_through_the_server() -> None:
    log = PlantLog()

    async def run() -> None:
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), "k", plant_log=log))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})

            async def cmd(body: dict, lang: str = "de") -> tuple[int, dict]:  # type: ignore[type-arg]
                resp = await client.post("/api/cmd", json=body, headers={"X-Lang": lang})
                return resp.status, await resp.json()

            async def age(query: str, lang: str = "de") -> tuple[int, dict]:
                resp = await client.get(f"/api/plant/age?{query}", headers={"X-Lang": lang})
                return resp.status, await resp.json()

            config.STATE_DIR.mkdir(parents=True, exist_ok=True)
            status, preview = await age("weeks=5")
            assert status == 200 and preview["implied_start"] == "2026-09-17" and preview["would_move_days"] == 7
            assert preview["consistent"] is False and preview["stored_start"] == "2026-09-10"
            assert preview["text"] == ("Das wäre der 17.09.2026, 7 Tage später als bisher (10.09.2026): 5 Wochen alt, "
                                       "Pflanzenwoche 6. Die Düngermengen ändern sich dadurch nicht.")
            assert not (config.STATE_DIR / "plant-log.json").exists()  # a preview writes nothing
            status, en = await age("date=2026-09-10", "en")
            assert status == 200 and en["would_move_days"] == 0 and en["text"].startswith("That is the start")
            assert (await age("life_week=7"))[1]["text"] is None
            for bad in ("", "weeks=x", "weeks=60", "date=2027-01-01", "weeks=5&days=3"):
                assert (await age(bad))[0] == 400, bad
            status, res = await cmd({"action": "plant_start", "age_weeks": 5})
            assert status == 200 and res == {"ok": True, "date": "2026-09-17"}
            assert log.data["start_date"]["run"] == "2026-09-10"
            status, res = await cmd({"action": "plant_start", "date": "2026-09-18"})
            assert status == 200 and res["date"] == "2026-09-18"
            assert (await cmd({"action": "plant_start", "age_days": 36}))[1]["date"] == "2026-09-16"
            status, res = await cmd({"action": "plant_start", "date": "2027-01-01"}, "en")
            assert status == 400 and res["error"] == "The start cannot be in the future"
            assert (await cmd({"action": "plant_start"}))[0] == 400
            state = (await (await client.get("/api/state")).json())["plant"]
            assert state["plant"]["start_date"] == "2026-09-16" and state["age"]["by"] == "owner"
            assert state["feeding"]["age"]["start"] == "2026-09-16"  # the feeding plan gets the same age

    asyncio.run(run())
