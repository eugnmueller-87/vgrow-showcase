"""Choosing a plant (vgrow/plant_run.py): the wiki's varieties in the plant form, what a pick brings to the parameters, and
the step that makes a plant record the cockpit's run. A plant record alone changes nothing."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, plant, plant_run, workspace
from vgrow.alerts import Alerts
from vgrow.box import CommandRefused
from vgrow.demo import DemoBox
from vgrow.light_schedule import automatic_plan
from vgrow.plant import PlantLog, current_stage, load_profile
from vgrow.server import make_app

ON = ["cannabis"]
PHOTO = {"source": "lexicon", "key": "barney-s-farm-afghan-hash-plant-photo"}


def lexicon_row(flowering: str) -> dict[str, Any]:
    return next(r for r in plant_run.options("de", ON) if r["source"] == "lexicon" and r["flowering"] == flowering)


def seed_workspace() -> None:
    """The first read of the page: the box and the imported plant exist."""
    workspace.initialize("VGrow Y18", load_profile(), PlantLog())


def create(**body: Any) -> str:
    revision = workspace.view("de")["revision"]
    return workspace.mutate({"action": "plant_create", "box": "box-1", "name": "Neue", "revision": revision, **body})


# ---------- what the databases offer ----------

def test_the_list_holds_both_databases_and_never_thc() -> None:
    rows = plant_run.options("de", ON)
    sources = {r["source"] for r in rows}
    assert sources == {"lexicon", "encyclopedia"}
    assert len([r for r in rows if r["source"] == "lexicon"]) >= 200
    assert len([r for r in rows if r["source"] == "encyclopedia"]) >= 150
    assert "thc" not in json.dumps(rows).lower()
    assert [r["label"].casefold() for r in rows] == sorted(r["label"].casefold() for r in rows)


def test_a_lexicon_row_carries_type_bloom_and_fit() -> None:
    row = plant_run.find(PHOTO, "de", ON)
    assert row["flowering"] == "photoperiod" and row["bloom"] == [50, 60] and row["height"] == [80, 100]
    assert row["fit"] == "lst" and row["box_cm"] == 92 and row["by"] == "Barney's Farm"


def test_the_encyclopedia_does_not_claim_a_flowering_type() -> None:
    rows = [r for r in plant_run.options("de", ON) if r["source"] == "encyclopedia"]
    assert {r["flowering"] for r in rows} == {None}
    assert any(r["bloom"] for r in rows)


def test_a_package_that_is_off_offers_nothing() -> None:
    assert [r for r in plant_run.options("de", []) if r["source"] == "lexicon"] == []
    assert plant_run.find(PHOTO, "de", []) is None


def test_find_is_none_for_what_is_not_there() -> None:
    assert plant_run.find({"source": "lexicon", "key": "nope"}, "de", ON) is None
    assert plant_run.find(None, "de", ON) is None


# ---------- the record ----------

def test_a_record_keeps_the_pick_and_changes_no_run() -> None:
    seed_workspace()
    before = load_profile()["plant"]["strain"]
    ident = create(variety="X", variety_ref=lexicon_row("auto") | {"junk": 1}, flowering="auto")
    found = next(p for p in workspace.view("de")["plants"] if p["id"] == ident)
    assert found["variety_ref"]["source"] == "lexicon" and found["flowering"] == "auto"
    assert found["variety_facts"]["flowering"] == "auto"
    assert found["linked_profile"] is False
    assert load_profile()["plant"]["strain"] == before


def test_the_form_refuses_a_pick_that_does_not_exist_and_a_wrong_type() -> None:
    seed_workspace()
    for body in ({"variety_ref": {"source": "lexicon", "key": "nope"}}, {"variety_ref": "x"}, {"flowering": "sometimes"}):
        with pytest.raises(workspace.Invalid):
            create(**body)


def test_free_text_still_works() -> None:
    seed_workspace()
    ident = create(variety="Omas Sorte")
    found = next(p for p in workspace.view("de")["plants"] if p["id"] == ident)
    assert found["variety"] == "Omas Sorte" and found["variety_ref"] is None and found["variety_facts"] is None


def test_an_update_without_a_pick_leaves_it_alone_and_with_one_changes_it() -> None:
    seed_workspace()
    ident = create(variety_ref=PHOTO, flowering="photoperiod")
    revision = workspace.view("de")["revision"]
    workspace.mutate({"action": "plant_update", "id": ident, "box": "box-1", "name": "Neu", "revision": revision})
    assert workspace.plant_record(ident)["variety_ref"] == PHOTO
    revision = workspace.view("de")["revision"]
    workspace.mutate({"action": "plant_update", "id": ident, "box": "box-1", "name": "Neu", "revision": revision,
                      "variety_ref": None, "flowering": None})
    assert workspace.plant_record(ident)["variety_ref"] is None


# ---------- the run ----------

def activate(ident: str, log: PlantLog, stage: str | None = "veg") -> dict[str, Any]:
    return plant_run.activate(workspace.plant_record(ident), log, stage=stage)


def test_until_a_plant_is_chosen_plant_json_runs() -> None:
    assert plant.run_path() == plant.PLANT_FILE
    assert plant_run.active_record() is None
    assert load_profile()["strain_card"] == "ice-cream-cake-auto"


def test_choosing_a_plant_moves_the_whole_cockpit_to_it() -> None:
    seed_workspace()
    log = PlantLog()
    log.set_stage("early_flower", "2026-10-24")
    log.add_height(62.0, "2026-10-27")
    ident = create(name="Purple", variety_ref=PHOTO, flowering="photoperiod", started="2026-10-20", approximate=True)
    assert activate(ident, log)["new_plant"] is True
    assert plant.run_path() == config.STATE_DIR / plant.RUN_FILE
    profile = load_profile()
    assert profile["plant"] == {"strain": "Purple", "start_date": "2026-10-20", "start_approx": True, "record": ident}
    assert profile["strain"]["name"] == "Afghan Hash Plant"
    assert current_stage(profile, log)["key"] == "veg" and current_stage(profile, log)["set_by"] == "manual"
    # what plant.json keeps for every plant comes along, what belonged to the last plant does not
    original = json.loads(plant.PLANT_FILE.read_text(encoding="utf-8"))
    assert profile["medium"] == original["medium"] and profile["feeding"]["setups"] == original["feeding"]["setups"]
    assert profile["feeding"]["dose_factor"] == original["feeding"]["dose_factor"]
    assert "bloom_start_estimate" not in profile["feeding"] and profile["feeding"]["bloom_start"] is None
    assert "stage_estimate" not in profile and "strain_card" not in profile
    # the last plant's log is set aside, and no height of it counts for this one
    assert log.data["heights"] == [] and [s["key"] for s in log.data["stages"]] == ["veg"]
    archived = list((config.STATE_DIR / plant_run.LOG_ARCHIVE).glob("plant-json-*.json"))
    kept = json.loads(archived[0].read_text(encoding="utf-8"))
    assert kept["stages"][0]["key"] == "early_flower" and kept["heights"][0]["cm"] == 62.0


def test_the_workspace_follows_the_choice() -> None:
    seed_workspace()
    ident = create(variety_ref=PHOTO, flowering="photoperiod")
    assert workspace.validate_context("box-1", ident) is False
    activate(ident, PlantLog())
    data = workspace.view("de")
    assert data["linked_plant"] == ident
    assert next(p for p in data["plants"] if p["id"] == ident)["linked_profile"] is True
    assert workspace.validate_context("box-1", ident) is True
    assert workspace.validate_context("box-1", workspace.NATIVE_PLANT) is False
    with pytest.raises(workspace.Invalid):  # the plant the cockpit runs on is not archived from under it
        workspace.mutate({"action": "plant_archive", "id": ident, "archived": True, "revision": data["revision"]})


def test_the_same_plant_again_keeps_its_log() -> None:
    seed_workspace()
    log = PlantLog()
    ident = create(name="Alt", flowering="auto")
    activate(ident, log)
    log.add_height(20.0, "2026-11-01")
    revision = workspace.view("de")["revision"]
    workspace.mutate({"action": "plant_update", "id": ident, "box": "box-1", "name": "Neuer Name", "revision": revision})
    assert activate(ident, log, stage=None)["new_plant"] is False
    assert load_profile()["plant"]["strain"] == "Neuer Name"
    assert log.data["heights"][0]["cm"] == 20.0 and [s["key"] for s in log.data["stages"]] == ["veg"]


def test_a_stage_the_card_does_not_know_changes_nothing() -> None:
    seed_workspace()
    log = PlantLog()
    log.set_stage("early_flower", "2026-10-24")
    ident = create(flowering="auto")
    with pytest.raises(CommandRefused):
        activate(ident, log, stage="blossom")
    assert plant_run.active_record() is None and log.data["stages"][0]["key"] == "early_flower"


# ---------- what the flowering type does ----------

def plan_for(flowering: str | None, stage: str) -> dict[str, Any] | None:
    seed_workspace()
    log = PlantLog()
    activate(create(flowering=flowering), log, stage=stage)
    profile = load_profile()
    return automatic_plan(profile, current_stage(profile, log), {})


def test_an_autoflower_gets_the_cards_18_6() -> None:
    assert plan_for("auto", "veg")["hours_on"] == 18


def test_a_photoperiodic_plant_grows_on_18_6_and_never_on_the_autoflower_20_4() -> None:
    plan = plan_for("photoperiod", "veg")
    assert plan["hours_on"] == 18 and plan["settings"]["level"] == 80
    assert [s["id"] for s in plan["sources"]] == ["L1", "S1"]
    assert plan["sources"][0]["url"] == "https://www.growweedeasy.com/light-schedules"


def test_a_photoperiodic_plant_flowers_on_12_12() -> None:
    assert plan_for("photoperiod", "early_flower")["hours_on"] == 12


def test_the_light_plan_follows_the_stage_by_itself() -> None:
    """The owner's wish of 06.11.2026: on and off by themselves, fitted to the plant's stage. The timer on "Automatisch" asks
    for the plan on every tick, so a new stage changes hours and brightness without a tap, keeping the owner's start time."""
    seed_workspace()
    log = PlantLog()
    activate(create(flowering="photoperiod"), log, stage="veg")
    cfg = {"enabled": True, "on_time": "22:00", "off_time": "16:00", "level": 80, "from_requirements": True}
    profile = load_profile()
    veg = automatic_plan(profile, current_stage(profile, log), cfg)["settings"]
    assert (veg["on_time"], veg["off_time"], veg["level"]) == ("22:00", "16:00", 80)
    log.set_stage("early_flower")
    flower = automatic_plan(profile, current_stage(profile, log), cfg)["settings"]
    assert (flower["on_time"], flower["off_time"], flower["level"]) == ("22:00", "10:00", 100)


def test_an_unknown_type_gets_no_automatic_plan() -> None:
    assert plan_for(None, "veg") is None


def test_a_photoperiodic_plant_never_flowers_by_its_age() -> None:
    seed_workspace()
    log = PlantLog()
    activate(create(flowering="photoperiod", started="2026-01-01"), log, stage=None)
    assert current_stage(load_profile(), log)["key"] == "veg"


def test_an_autoflower_still_goes_by_its_age_until_the_owner_says() -> None:
    seed_workspace()
    log = PlantLog()
    activate(create(flowering="auto", started="2026-01-01"), log, stage=None)
    assert current_stage(load_profile(), log)["key"] == "harvest"


def test_warnings_name_the_height_and_the_open_type() -> None:
    seed_workspace()
    log = PlantLog()
    activate(create(variety_ref=PHOTO, flowering="photoperiod"), log)
    strain = load_profile()["strain"]
    assert "80 bis 100 cm" in strain["warnings"][0] and "92 cm" in strain["warnings"][0] and "Barney's Farm" in strain["warnings"][0]
    assert "12 Stunden" in strain["warnings"][1] and "28 cm" in strain["warnings"][1]
    assert "80 to 100 cm" in strain["warnings_en"][0] and "28 cm" in strain["warnings_en"][1]


def test_a_free_text_autoflower_has_no_warnings_and_a_name() -> None:
    seed_workspace()
    activate(create(name="Omas Auto", flowering="auto"), PlantLog())
    strain = load_profile()["strain"]
    assert strain["name"] == "Omas Auto" and "warnings" not in strain


def test_the_wiki_marks_the_chosen_strain_as_the_current_one() -> None:
    from vgrow import wiki

    seed_workspace()
    activate(create(variety_ref=PHOTO, flowering="photoperiod"), PlantLog())
    assert wiki.current_strain() == PHOTO["key"]


# ---------- over the page's routes ----------

def client_call(steps: Any) -> None:
    async def main() -> None:
        log = PlantLog()
        app = make_app(DemoBox(), Alerts(), "k" * 32, plant_log=log)
        async with TestClient(TestServer(app)) as client:
            await client.get("/?key=" + "k" * 32, allow_redirects=False)
            await steps(client, log)

    asyncio.run(main())


def test_the_routes_offer_the_list_and_take_the_choice() -> None:
    async def steps(client: TestClient, log: PlantLog) -> None:
        rows = (await (await client.get("/api/workspace/varieties")).json())["rows"]
        assert any(r["key"] == PHOTO["key"] for r in rows)
        data = await (await client.get("/api/workspace")).json()
        written = await client.post("/api/workspace", json={"action": "plant_create", "box": "box-1", "name": "Neue",
                                                            "variety_ref": PHOTO, "flowering": "photoperiod",
                                                            "revision": data["revision"]})
        ident = (await written.json())["created"]
        answer = await client.post("/api/cmd", json={"action": "run_activate", "plant": ident, "stage": "veg"})
        assert answer.status == 200 and (await answer.json())["new_plant"] is True
        assert log.data["stages"][-1]["key"] == "veg"
        state = await (await client.get("/api/state")).json()
        assert state["plant"]["plant"]["strain"] == "Neue" and state["plant"]["stage"]["key"] == "veg"
        wrong = await client.post("/api/cmd", json={"action": "run_activate", "plant": ident, "stage": "blossom"})
        assert wrong.status == 409
        missing = await client.post("/api/cmd", json={"action": "run_activate", "plant": "nope"})
        assert missing.status == 400

    client_call(steps)


def test_another_box_cannot_become_the_run() -> None:
    async def steps(client: TestClient, _log: PlantLog) -> None:
        data = await (await client.get("/api/workspace")).json()
        written = await client.post("/api/workspace", json={"action": "box_create", "name": "Zweite", "revision": data["revision"]})
        box = (await written.json())["created"]
        data = await (await client.get("/api/workspace")).json()
        written = await client.post("/api/workspace", json={"action": "plant_create", "box": box, "name": "Dort", "revision": data["revision"]})
        answer = await client.post("/api/cmd", json={"action": "run_activate", "plant": (await written.json())["created"]})
        assert answer.status == 409 and plant_run.active_record() is None

    client_call(steps)
