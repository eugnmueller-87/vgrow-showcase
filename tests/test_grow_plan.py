"""The grow plan (vgrow/grow_plan.py) on the owner's chosen run (vgrow/plant_run.py): the light of the day, the strip's marks, the
feeding date, the water watch, and the plant's record. The light and the pump are real outputs, so every hold and every "never
by itself" has a test."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, feeding, grow_plan, plant_run, workspace
from vgrow.alerts import Alerts
from vgrow.box import CommandRefused
from vgrow.budget import CommandBudget
from vgrow.demo import DemoBox
from vgrow.grow_plan import GrowPlan, water_slots
from vgrow.i18n import UserError
from vgrow.light_schedule import LightSchedule, ZONE, automatic_plan, settings
from vgrow.plant import PlantLog, current_stage, load_profile
from vgrow.portguard import PortGuard
from vgrow.server import COOKIE, make_app
from vgrow.watering import WateringController

REPO = Path(__file__).resolve().parent.parent


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=ZONE).timestamp()


def chosen_run(flowering: str = "photoperiod", record: str = "plant-test") -> None:
    """The run as the owner's choice writes it (plant_run.activate): a photoperiodic plant, started on 06.11.2026."""
    run = plant_run.build_run({"id": record, "name": "Purple Punch", "started": "2026-11-06", "approximate": 0, "flowering": flowering,
                               "variety_ref": {"source": "lexicon", "key": "hanfgarten-purple-punch-photo"}})
    config.atomic_write(plant_run.run_file(), json.dumps(run, ensure_ascii=False))


class Box:
    demo = True

    def __init__(self) -> None:
        self.snap: dict[str, Any] = {
            "online": True, "mqtt": True, "shadow_age": 1, "sensors_age": 1,
            "light": {"level": 40, "mode": 0, "in_plan": False, "in_plan_known": True, "spectrum": "bloom", "spectrum_writable": True},
            "climate": {"probe_temp": 25.0, "probe_humidity": 60.0, "probe_vpd": 1.0, "water_kind": "wet_dry", "water_state": "wet"},
            "ports": {"usb1": {"on": False, "can_write": True, "in_plan": 0, "accessory": 0, "mode": 0},
                      "usb2": {"on": False, "can_write": True, "in_plan": 0, "accessory": 0, "mode": 0}},
        }
        self.spectrums: list[str] = []
        self.lights: list[int] = []
        self.ports: list[tuple[str, bool]] = []
        self.refuse_spectrum = False

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.snap)

    async def set_light(self, level: int) -> None:
        self.lights.append(level)
        self.snap["light"]["level"] = level

    async def set_spectrum(self, name: str) -> None:
        if self.refuse_spectrum:
            raise CommandRefused("manual only")
        self.spectrums.append(name)
        self.snap["light"]["spectrum"] = name

    async def set_port(self, port: str, on: bool) -> None:
        self.ports.append((port, on))
        self.snap["ports"][port]["on"] = on


class Store:
    """The measurement store, as much as the plan reads of it."""

    def __init__(self) -> None:
        self.temp, self.vpd, self.n = 25.0, 1.0, 600

    def rows(self, start: float, end: float) -> list[dict[str, Any]]:
        return [{"pTemp": self.temp, "pVpd": self.vpd} for _ in range(self.n)]


class Pushes:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def push(self, title: str, message: str, *, priority: int = 3, click: str | None = None) -> bool:
        self.sent.append(message)
        return True


class Soil:
    def __init__(self) -> None:
        self.rows: list[dict[str, float]] = []

    def history(self, hours: float) -> list[dict[str, float]]:
        return list(self.rows)


class Camera:
    configured = True
    reachable = True

    async def photo(self) -> bytes:
        return b"jpeg"


@pytest.fixture
def rig():
    chosen_run()
    clock = [at("2026-11-06T11:00:00")]
    box, log, pushes, store, soil = Box(), PlantLog(), Pushes(), Store(), Soil()
    log.set_stage("veg", "2026-11-06")
    log.set_feed_from("2026-11-20")
    budget = CommandBudget(lambda: clock[0])
    timer = LightSchedule(box, budget, lambda: clock[0])
    plan = GrowPlan(box, log, alerts=pushes, budget=budget, timer=timer, soil=soil, measurements=store,
                    camera_of=lambda: Camera(), clock=lambda: clock[0], guard=PortGuard("box-1"))
    plan.settings({"light_goal": 100}, box.snapshot())

    def requirements():
        profile = load_profile()
        return profile, plan.light_stage(profile, current_stage(profile, log))

    timer.requirements_source = requirements

    async def start_light() -> None:
        await timer.configure({"enabled": False, "on_time": "20:00", "off_time": "16:00", "level": 40})
        auto = timer.automatic(box.snapshot())
        assert auto["available"], auto
        await timer.resume_automatic(auto["plan"]["fingerprint"])

    class Rig:
        pass

    out = Rig()
    out.clock, out.box, out.log, out.pushes, out.store, out.soil, out.timer, out.plan = clock, box, log, pushes, store, soil, timer, plan
    out.start_light = start_light
    return out


def go(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------- the run ----------

def test_a_photoperiodic_plant_may_grow_on_twenty_hours_and_flowers_on_twelve() -> None:
    chosen_run()
    profile = load_profile()
    by_key = {s["key"]: {**s, "set_by": "manual", "since": "2026-11-06"} for s in profile["stages"]}
    saved = settings({"on_time": "20:00", "off_time": "16:00", "level": 40})
    veg = automatic_plan(profile, by_key["veg"], saved)
    assert veg["hours_on"] == 20 and veg["settings"]["off_time"] == "16:00"
    assert automatic_plan(profile, by_key["veg"], settings({"on_time": "20:00", "off_time": "10:00", "level": 40}))["hours_on"] == 18
    flower = automatic_plan(profile, by_key["early_flower"], veg["settings"])
    assert flower["hours_on"] == 12 and flower["settings"]["on_time"] == "20:00" and flower["settings"]["off_time"] == "08:00"


def test_a_new_run_takes_none_of_the_last_plants_feeding_dates() -> None:
    chosen_run()
    feed = json.loads(plant_run.run_file().read_text(encoding="utf-8"))["feeding"]
    assert feed["bloom_start"] is None and "bloom_start_estimate" not in feed and "bloom_start_estimate_note" not in feed
    assert "Autoflower" not in feed["dose_factor_note"] and feed["dose_factor_sources"] == ["N6"]
    chosen_run("auto")
    feed = json.loads(plant_run.run_file().read_text(encoding="utf-8"))["feeding"]
    assert "Autoflower" in feed["dose_factor_note"]  # an autoflower keeps the note written for it


def test_the_flip_height_leaves_the_lamp_its_distance() -> None:
    """It doubles after the switch: from 46 cm it would reach the lamp. (92 - 35) / 2, and the strip prints 25 to 30."""
    chosen_run()
    warnings = load_profile()["strain"]["warnings"]
    assert any("etwa 28 cm" in w for w in warnings) and not any("46 cm" in w for w in warnings)


# ---------- feeding ----------

def feed_view(log: PlantLog, today: str) -> dict[str, Any]:
    profile = load_profile()
    return feeding.view(feeding.setup_of(profile, log), "de", today=datetime.fromisoformat(today).date(), **feeding.context(profile, log, "de"))


def confirmed_log() -> PlantLog:
    chosen_run()
    log = PlantLog()
    log.set_stage("veg", "2026-11-06")
    log.set_feeding_choice("biobizz", feeding.setups_of(load_profile()).keys())
    log.set_feed_from("2026-11-20")
    setup = feeding.setup_of(load_profile(), log)
    feeding.confirm(setup, setup["card"], feeding.fingerprint(setup, feeding.load_card(setup["card"])))
    return log


def test_plain_water_until_the_owners_date_then_the_chart() -> None:
    log = confirmed_log()
    before = feed_view(log, "2026-11-19")
    assert before["state"] == "water_only" and not before["doses"] and before["not_before"] == "2026-11-20"
    assert "20.11.2026" in before["warning"]
    after = feed_view(log, "2026-11-20")
    assert after["state"] == "ok" and after["doses"] and after["not_before"] is None


def test_without_a_date_the_chart_counts_at_once() -> None:
    log = confirmed_log()
    log.set_feed_from(None)
    assert feed_view(log, "2026-11-07")["state"] == "ok"


def test_the_date_belongs_to_the_run() -> None:
    log = confirmed_log()
    log.start_new_run(config.STATE_DIR / "archive.json")
    assert log.data["feed_from"] is None


def test_hank_names_the_date_not_a_week_of_the_chart() -> None:
    from vgrow.hank import feedplan
    from vgrow.hank.livestate import LiveState

    log = confirmed_log()
    lines = feedplan.plan_lines(LiveState(feeding_view=feed_view(log, "2026-11-10")), "de")
    assert lines == ["Dünger erst ab 20.11.2026: bis dahin nur Wasser, das ist deine Ansage."]


def test_the_owners_confirmation_of_his_fertilizer_card_survives_the_new_run() -> None:
    """Same card, same page, same factor: the numbers he compared with the bottles did not change."""
    example = json.loads((REPO / "plant.json").read_text(encoding="utf-8"))
    chosen_run()
    old = feeding.setups_of({"feeding": example["feeding"]})["biobizz"]
    new = feeding.setups_of(load_profile())["biobizz"]
    card = feeding.load_card(old["card"])
    assert feeding.fingerprint(old, card) == feeding.fingerprint(new, card)


# ---------- the owner's settings ----------

def test_the_goal_starts_from_the_level_running_then(rig: Any) -> None:
    assert rig.plan.state["light"]["level"] == 40 and rig.plan.state["light"]["goal"] == 100
    assert rig.plan.state["log"][-1]["key"] == "gp.log.start"


def test_a_lower_goal_takes_the_level_down_and_none_gives_the_card_back(rig: Any) -> None:
    rig.plan.state["light"]["level"] = 90
    rig.plan.settings({"light_goal": 60}, rig.box.snapshot())
    assert rig.plan.state["light"]["level"] == 60 and rig.plan.state["light"]["goal"] == 60
    rig.plan.settings({"light_goal": None}, rig.box.snapshot())
    profile = load_profile()
    stage = current_stage(profile, rig.log)
    assert rig.plan.light_stage(profile, stage)["preset"]["light_percent"] == 80


@pytest.mark.parametrize("bad", [10, 101, "100", True, 55.5])
def test_a_goal_out_of_range_is_refused(rig: Any, bad: Any) -> None:
    with pytest.raises(CommandRefused):
        rig.plan.settings({"light_goal": bad}, rig.box.snapshot())


def test_a_bad_feed_date_is_refused_and_a_new_one_pushes_again(rig: Any) -> None:
    with pytest.raises(UserError):
        rig.plan.settings({"feed_from": "soon"}, rig.box.snapshot())
    rig.plan.state["feed_pushed"] = 1
    rig.plan.settings({"feed_from": "2026-12-01"}, rig.box.snapshot())
    assert rig.log.data["feed_from"] == "2026-12-01" and "feed_pushed" not in rig.plan.state


# ---------- light ----------

def test_the_timer_gets_the_plans_level_not_the_cards_preset(rig: Any) -> None:
    go(rig.start_light())
    assert rig.timer.cfg["from_requirements"] is True
    assert rig.timer.cfg["level"] == 40 and rig.timer.cfg["off_time"] == "16:00"
    assert rig.box.lights == []  # 40 was running already: nothing to send


def test_without_a_goal_the_cards_preset_stands() -> None:
    chosen_run()
    box, log = Box(), PlantLog()
    plan = GrowPlan(box, log)
    profile = load_profile()
    log.set_stage("veg", "2026-11-06")
    assert plan.light_stage(profile, current_stage(profile, log))["preset"]["light_percent"] == 80


def test_a_cool_light_period_raises_one_step_for_the_next_lights_on(rig: Any) -> None:
    async def run() -> None:
        await rig.start_light()
        await rig.plan.check(rig.box.snapshot())  # in the light: nothing yet
        assert rig.plan.state["light"]["level"] == 40
        rig.clock[0] = at("2026-11-06T16:01:00")
        await rig.timer.tick()
        assert rig.box.snapshot()["light"]["level"] == 0
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 50 and rig.plan.state["light"]["from"] == 40
        await rig.plan.check(rig.box.snapshot())  # one decision per dark phase
        assert rig.plan.state["light"]["level"] == 50
        await rig.timer.tick()
        assert rig.box.snapshot()["light"]["level"] == 0  # the new level waits for lights on
        rig.clock[0] = at("2026-11-06T20:00:01")
        await rig.timer.tick()
        assert rig.box.snapshot()["light"]["level"] == 50

    rig.log.add_height(16, "2026-11-06", estimated=True, camera="growcam", method="camera_strip")
    go(run())


def test_it_climbs_to_the_goal_and_stops_there(rig: Any) -> None:
    async def run() -> None:
        await rig.start_light()
        rig.plan.settings({"light_goal": 60}, rig.box.snapshot())
        for day, want in (("06", 50), ("07", 60), ("08", 60)):
            rig.clock[0] = at(f"2026-11-{day}T16:01:00")
            await rig.timer.tick()
            await rig.plan.check(rig.box.snapshot())
            assert rig.plan.state["light"]["level"] == want
            rig.clock[0] = at(f"2026-11-{day}T20:00:01")
            await rig.timer.tick()
            await rig.plan.check(rig.box.snapshot())

    rig.log.add_height(16, "2026-11-06", estimated=True, camera="growcam", method="camera_strip")
    go(run())


@pytest.mark.parametrize("temp,vpd,n,height,hold", [
    (28.6, 1.0, 600, 16, "warm"),  # above the band's 28 °C
    (25.0, 1.6, 600, 16, "dry"),  # VPD band ends at 1.2
    (25.0, 1.0, 5, 16, "no_data"),
    (25.0, 1.0, 600, None, "no_height"),
    (25.0, 1.0, 600, 60, "lamp"),  # 92 - 60 is less than the 35 cm to the lamp
])
def test_a_reason_holds_the_light_and_says_so(rig: Any, temp: float, vpd: float, n: int, height: float | None, hold: str) -> None:
    rig.store.temp, rig.store.vpd, rig.store.n = temp, vpd, n
    if height:
        rig.log.add_height(height, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        rig.clock[0] = at("2026-11-06T16:01:00")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())

    go(run())
    assert rig.plan.state["light"]["level"] == 40 and rig.plan.state["light"]["hold"] == hold
    assert rig.plan.view(rig.box.snapshot(), "de")["light"]["text"].startswith("Bleibt bei 40 %")


def test_the_owners_level_stays_until_he_says_otherwise(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        rig.timer.manual()  # what the server does for the owner's level, before it sends it
        await rig.box.set_light(50)
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 50 and rig.plan.state["light"]["hold"] == "owner"
        rig.clock[0] = at("2026-11-06T16:01:00")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 50  # no step up over his word
        rig.clock[0] = at("2026-11-06T20:00:01")
        await rig.timer.tick()
        assert rig.box.snapshot()["light"]["level"] == 50  # and the timer comes back to his level, not the plan's old one
        rig.plan.resume_light()
        rig.clock[0] = at("2026-11-07T16:01:00")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 60

    go(run())


def test_off_is_the_owners_the_plan_never_switches_the_timer_on(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        rig.timer.stop()
        await rig.box.set_light(0)
        rig.clock[0] = at("2026-11-06T16:01:00")
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] = at("2026-11-06T20:00:01")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())

    go(run())
    assert rig.box.snapshot()["light"]["level"] == 0
    assert rig.plan.state["light"]["level"] == 40
    assert "startet erst" in rig.plan.view(rig.box.snapshot(), "de")["light"]["text"]


def test_its_own_step_goes_back_when_it_gets_too_hot(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        rig.clock[0] = at("2026-11-06T16:01:00")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] = at("2026-11-06T20:00:01")
        await rig.timer.tick()
        rig.box.snap["climate"]["probe_temp"] = 29.6  # band's 28 plus 1.5, and a bit
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 50  # not at the first reading
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 40 and rig.plan.state["light"]["hold"] == "heat"
        await rig.timer.tick()
        assert rig.box.snapshot()["light"]["level"] == 40
        rig.clock[0] = at("2026-11-07T16:01:00")
        rig.box.snap["climate"]["probe_temp"] = 25.0
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 40  # no new try the next day

    go(run())
    assert any("Zu warm" in message for message in rig.pushes.sent)


def high_light(rig: Any, level: int = 80, exhaust: int = 10) -> None:
    rig.plan.state["light"]["level"] = level
    rig.box.snap["dfan"] = {"step": exhaust}
    rig.box.snap["climate"]["probe_temp"] = 29.0  # over the band's 28 plus 0.5


def test_the_light_comes_down_when_the_box_stays_too_warm_and_goes_no_lower_than_the_floor(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        high_light(rig, level=60)
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 60  # not at the first reading
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 50 and rig.plan.state["light"]["hold"] == "climate"
        await rig.timer.tick()
        assert rig.box.snapshot()["light"]["level"] == 50  # at once, not at the next lights on
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 50  # the last step gets time to show
        rig.clock[0] += 5 * 60
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 40
        rig.clock[0] += 3 * 3600
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 40  # the floor

    go(run())
    assert "Licht 40" in rig.plan.view(rig.box.snapshot(), "de")["light"]["text"].replace(" %", "").replace("\xa0%", "")


def test_a_step_up_needs_room_under_the_band(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")
    rig.store.temp = 27.9  # in the band, but a step of 10 would carry it over

    async def run() -> None:
        await rig.start_light()
        rig.clock[0] = at("2026-11-06T16:01:00")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())

    go(run())
    assert rig.plan.state["light"]["level"] == 40 and rig.plan.state["light"]["hold"] == "warm"


def test_too_dry_brings_the_light_down_as_well(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        high_light(rig)
        rig.box.snap["climate"].update(probe_temp=26.0, probe_vpd=1.6)  # the band ends at 1.2, the line is 0.3 above it
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 70

    go(run())


def test_the_exhaust_goes_first_while_the_autopilot_can_still_raise_it(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        high_light(rig, exhaust=6)
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 80
        rig.clock[0] += 40 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 70

    go(run())


def test_a_reading_back_in_range_starts_the_wait_over(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        high_light(rig)
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] += 15 * 60
        rig.box.snap["climate"]["probe_temp"] = 27.0
        await rig.plan.check(rig.box.snapshot())
        rig.box.snap["climate"]["probe_temp"] = 29.0
        rig.clock[0] += 10 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 80

    go(run())


def test_the_owners_level_is_not_lowered_for_the_climate(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        rig.timer.manual()
        await rig.box.set_light(90)
        high_light(rig, level=40)
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["hold"] == "owner"
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 90

    go(run())


def test_after_a_step_down_it_tries_again_two_days_later_one_small_step_up(rig: Any) -> None:
    rig.log.add_height(16, "2026-11-06")

    async def run() -> None:
        await rig.start_light()
        high_light(rig)
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] += 21 * 60
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 70
        rig.box.snap["climate"]["probe_temp"] = 25.0
        rig.clock[0] = at("2026-11-07T16:01:00")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 70  # no new try the next day
        rig.clock[0] = at("2026-11-08T16:01:00")
        await rig.timer.tick()
        await rig.plan.check(rig.box.snapshot())
        assert rig.plan.state["light"]["level"] == 80 and rig.plan.state["light"]["hold"] is None

    go(run())


class Tank:
    def __init__(self) -> None:
        self.warning: str | None = None
        self.filled = 1000.0

    def view(self, *, now: float | None = None) -> dict[str, Any]:
        return {"warning": self.warning, "refilled_at": self.filled, "remaining_ml_estimate": 140}


def test_the_humidifier_tank_gets_one_message_per_fill(rig: Any) -> None:
    rig.plan.tank = Tank()

    def messages() -> int:
        go(rig.plan.check(rig.box.snapshot()))
        return sum("Befeuchter" in message for message in rig.pushes.sent)

    assert messages() == 0
    rig.plan.tank.warning = "estimated_low"
    assert messages() == 1
    assert messages() == 1
    rig.plan.tank.filled = 2000.0  # filled, and low again
    assert messages() == 2


def test_a_humidifier_that_stopped_gets_one_message_until_it_runs_again(rig: Any) -> None:
    rig.plan.tank = Tank()

    def messages() -> int:
        go(rig.plan.check(rig.box.snapshot()))
        return sum("Feuchte an der Pflanze" in message for message in rig.pushes.sent)

    rig.plan.tank.warning = "no_response"
    assert messages() == 1
    assert messages() == 1
    rig.plan.tank.warning = None
    assert messages() == 1
    rig.plan.tank.warning = "no_response"
    assert messages() == 2


def test_a_new_plant_starts_a_new_plan(rig: Any) -> None:
    go(rig.plan.check(rig.box.snapshot()))
    assert rig.plan.state["run"] == "plant-test"
    chosen_run(record="plant-next")
    rig.plan.light_stage(load_profile(), None)
    assert rig.plan.state["run"] == "plant-next" and not rig.plan.state.get("light")


# ---------- spectrum ----------

def test_the_stages_spectrum_once_and_the_owners_change_stays(rig: Any) -> None:
    async def run() -> None:
        await rig.plan.check(rig.box.snapshot())
        assert rig.box.spectrums == ["veg"]
        rig.box.snap["light"]["spectrum"] = "bloom"  # the owner, in the app
        await rig.plan.check(rig.box.snapshot())
        assert rig.box.spectrums == ["veg"]
        rig.log.set_stage("early_flower", "2026-11-20")
        rig.clock[0] = at("2026-11-20T12:00:00")
        rig.box.snap["light"]["spectrum"] = "veg"
        await rig.plan.check(rig.box.snapshot())
        assert rig.box.spectrums == ["veg", "bloom"]

    go(run())


def test_a_refused_spectrum_is_tried_a_few_times_and_then_said(rig: Any) -> None:
    rig.box.refuse_spectrum = True

    async def run() -> None:
        for _ in range(7):
            await rig.plan.check(rig.box.snapshot())

    go(run())
    assert rig.box.spectrums == []
    assert [e["key"] for e in rig.plan.state["log"]].count("gp.log.spectrum_failed") == 1


# ---------- height ----------

def good(cm: float) -> dict[str, Any]:
    return {"ok": True, "cm": cm, "top_y": 0.47, "top_x": 0.55, "pixels": 30000, "px_per_cm": 15.9}


def take(rig: Any, cm: float | None, why: str = "no_strip") -> None:
    result = good(cm) if cm is not None else {"ok": False, "why": why}
    rig.plan._ensure_run(load_profile())
    go(rig.plan._took(result, b"jpeg", load_profile(), rig.clock[0]))


def test_a_reading_goes_into_the_log_as_the_cameras_estimate(rig: Any) -> None:
    take(rig, 16.2)
    last = rig.log.data["heights"][-1]
    assert last["cm"] == 16.2 and last["date"] == "2026-11-06" and last["estimated"] is True and last["method"] == "camera_strip"
    assert rig.plan.photo_path().read_bytes() == b"jpeg"
    tile = rig.plan.view(rig.box.snapshot(), "de")["tiles"][0]
    assert tile["value"] == "≈16" and "76 cm" in tile["sub"]


def test_the_days_value_is_the_median_of_its_readings(rig: Any) -> None:
    for cm in (16.0, 16.4, 19.0):
        take(rig, cm)
    assert rig.log.data["heights"][-1]["cm"] == 16.4


def test_a_jump_waits_for_the_next_reading_to_agree(rig: Any) -> None:
    take(rig, 16.0)
    rig.clock[0] += 3600
    take(rig, 30.0)
    assert rig.log.data["heights"][-1]["cm"] == 16.0
    assert rig.plan.state["suspect"]["cm"] == 30.0
    rig.clock[0] += 3600
    take(rig, 30.5)
    assert rig.log.data["heights"][-1]["cm"] == 30.5


def test_a_height_the_owner_typed_stays_and_corrects_the_readings_after_it(rig: Any) -> None:
    take(rig, 16.0)
    rig.log.add_height(20.0, "2026-11-06")
    rig.clock[0] += 3600
    take(rig, 16.2)
    assert rig.log.data["heights"][-1]["cm"] == 20.0 and "method" not in rig.log.data["heights"][-1]
    assert rig.plan.state["offset"] == pytest.approx(3.8)
    rig.clock[0] = at("2026-11-07T12:00:00")
    take(rig, 17.0)
    assert rig.log.data["heights"][-1]["cm"] == pytest.approx(20.8)


def test_the_real_height_under_the_picture_wins_and_sets_the_offset_at_once(rig: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(grow_plan.ruler, "read_jpeg", lambda _: good(16.2))
    take(rig, 16.0)
    rig.clock[0] += 3600
    go(rig.plan.correct_height(20.0))
    assert rig.log.data["heights"][-1]["cm"] == 20.0 and "method" not in rig.log.data["heights"][-1]
    assert rig.plan.state["offset"] == pytest.approx(3.8)
    assert rig.plan.state["height"]["cm"] == pytest.approx(20.0)  # no jump to wait out: it is the owner's own number
    go(rig.plan.correct_height(21.0))  # typed twice in a day: the second one counts
    assert rig.plan.state["offset"] == pytest.approx(4.8)


def test_a_height_outside_the_range_changes_nothing(rig: Any) -> None:
    take(rig, 16.0)
    before = copy.deepcopy(rig.log.data["heights"])
    with pytest.raises(UserError):
        go(rig.plan.correct_height(500.0))
    assert rig.log.data["heights"] == before and "offset" not in rig.plan.state


def test_a_camera_that_cannot_read_says_why_and_pushes_after_a_day(rig: Any) -> None:
    take(rig, None)
    assert "Streifen" in rig.plan.view(rig.box.snapshot(), "de")["tiles"][0]["sub"]
    assert rig.pushes.sent == []
    rig.clock[0] += 25 * 3600
    take(rig, None)
    take(rig, None)
    assert len(rig.pushes.sent) == 1 and "Streifen" in rig.pushes.sent[0]
    take(rig, 16.0)
    assert "height_problem" not in rig.plan.state


def test_height_is_read_only_with_the_lamp_on_and_not_too_often(rig: Any) -> None:
    calls = []

    async def measure() -> dict[str, Any]:
        calls.append(rig.clock[0])
        rig.plan.state["height_try"] = rig.clock[0]
        return {}

    rig.plan.measure = measure

    async def run() -> None:
        rig.box.snap["light"]["level"] = 0
        await rig.plan.height_tick()
        assert calls == []
        rig.box.snap["light"]["level"] = 40
        await rig.plan.height_tick()
        await rig.plan.height_tick()
        assert len(calls) == 1
        rig.clock[0] += grow_plan.HEIGHT_EVERY_S + 1
        await rig.plan.height_tick()
        assert len(calls) == 2

    go(run())


# ---------- the strip's marks ----------

def test_each_mark_pushes_once_when_the_plant_reaches_it(rig: Any) -> None:
    take(rig, 14.0)
    assert rig.pushes.sent == []
    rig.clock[0] += 86400
    take(rig, 16.0)
    assert len(rig.pushes.sent) == 1 and "Netz einhängen" in rig.pushes.sent[0]
    take(rig, 16.1)
    assert len(rig.pushes.sent) == 1
    rig.clock[0] += 86400
    take(rig, 21.0)
    assert "Toppen" in rig.pushes.sent[-1]
    rig.clock[0] += 86400
    take(rig, 26.0)
    assert "auf Blüte umstellen" in rig.pushes.sent[-1] and "12/12" in rig.pushes.sent[-1]
    rig.clock[0] += 86400
    take(rig, 31.0)
    assert "noch nicht auf Blüte" in rig.pushes.sent[-1]


def test_no_flower_push_once_the_plant_flowers(rig: Any) -> None:
    take(rig, 16.0)
    rig.log.set_stage("early_flower", "2026-11-07")
    rig.clock[0] = at("2026-11-10T12:00:00")
    take(rig, 28.0)
    take(rig, 28.0)
    rig.clock[0] = at("2026-11-11T12:00:00")
    take(rig, 32.0)
    assert not any("Blüte" in message for message in rig.pushes.sent)
    marks = {m["id"]: m for m in rig.plan.view(rig.box.snapshot(), "de")["marks"]}
    assert marks["flowering"]["state"] == "done"


def test_the_flowering_mark_offers_the_switch_only_when_it_is_time(rig: Any) -> None:
    take(rig, 16.0)
    marks = {m["id"]: m for m in rig.plan.view(rig.box.snapshot(), "de")["marks"]}
    assert marks["trellis"]["state"] == "now" and marks["flowering"]["buttons"] == [] and marks["topping"]["state"] == "next"
    rig.clock[0] = at("2026-11-10T12:00:00")
    take(rig, 26.0)
    marks = {m["id"]: m for m in rig.plan.view(rig.box.snapshot(), "de")["marks"]}
    button = marks["flowering"]["buttons"][0]
    assert button["action"] == "plant_stage" and button["key"] == "early_flower" and button["armed"] is True


def test_an_autoflower_has_no_topping_and_no_flowering_mark() -> None:
    chosen_run("auto")
    assert [m["id"] for m in GrowPlan(Box(), PlantLog()).view(Box().snapshot(), "de")["marks"]] == ["trellis", "lollipop"]


def test_too_close_to_the_lamp_pushes_once(rig: Any) -> None:
    go(rig.plan._mark_pushes(load_profile(), 60.0))
    go(rig.plan._mark_pushes(load_profile(), 61.0))
    assert sum("bis zur Lampe" in message for message in rig.pushes.sent) == 1


# ---------- feeding push ----------

def test_the_feed_day_pushes_once_from_the_morning_on(rig: Any) -> None:
    async def run() -> None:
        rig.clock[0] = at("2026-11-19T23:00:00")
        await rig.plan.check(rig.box.snapshot())
        rig.clock[0] = at("2026-11-20T08:00:00")
        await rig.plan.check(rig.box.snapshot())
        assert not any("Dünger" in m for m in rig.pushes.sent)
        rig.clock[0] = at("2026-11-20T09:01:00")
        await rig.plan.check(rig.box.snapshot())
        await rig.plan.check(rig.box.snapshot())

    go(run())
    assert sum("Dünger" in m for m in rig.pushes.sent) == 1


# ---------- the water watch ----------

class FakeThirst:
    model_path = None

    def __init__(self) -> None:
        self.flow_value: dict[str, Any] = {"calibrated": False, "ml_s": None}
        self.jugs: list[float] = []

    def flow(self, _now: float | None = None) -> dict[str, Any]:
        return self.flow_value

    def jug_run(self, seconds: float) -> None:
        self.jugs.append(seconds)


@pytest.fixture
def watch(rig: Any):
    guard = PortGuard("box-1")
    watering = WateringController(rig.box, guard, CommandBudget(lambda: rig.clock[0]), clock=lambda: rig.clock[0])
    rig.plan.watering, rig.plan.thirst, rig.plan.guard = watering, FakeThirst(), guard
    rig.watering = watering
    return rig


def test_the_jug_test_names_the_pump_runs_a_minute_and_tells_the_index(watch: Any) -> None:
    async def run() -> None:
        await watch.plan.pump_test()
        assert watch.box.ports == [("usb1", True)]
        assert watch.watering.cfg["duration_s"] == grow_plan.PUMP_TEST_S and watch.watering.cfg["power_confirmed"] is True
        assert watch.plan.guard.owner_device("usb1") == "pump"
        assert watch.plan.thirst.jugs == [grow_plan.PUMP_TEST_S]
        assert watch.watering.cfg["enabled"] is False
        await watch.watering.close()

    go(run())


def test_a_jug_run_is_seen_and_never_counts_as_water_in_the_pot() -> None:
    from vgrow import thirst as th

    box = Box()
    clock = {"now": time.time() + 10}  # the owner's word on the pump (the guard's own clock) comes before the runs
    guard = PortGuard("box-1")
    guard.assign("usb1", "pump", box.snapshot())

    class Watering:
        cfg = {"power_confirmed": True}

    thirst = th.Thirst(box, guard=guard, demo=True, clock=lambda: clock["now"], watering=Watering(),
                       stage_source=lambda: {"key": "veg", "set_by": "manual"})
    thirst.set_pot({"volume_l": 11.4, "drain": True})
    clock["now"] += 100
    thirst.jug_run(10)
    thirst._close_run("usb1", {"first_on": clock["now"], "last_on": clock["now"] + 10, "origin": "manual"}, clock["now"] + 11)
    rows = [json.loads(line) for line in thirst.log_path().read_text(encoding="utf-8").splitlines()]
    assert [r["why"] for r in rows if r["kind"] == "pump_seen"] == ["jug"]
    assert not [r for r in rows if r["kind"] == "water"]
    clock["now"] += 200  # the jug word is used up: the next run is a watering again
    thirst._close_run("usb1", {"first_on": clock["now"], "last_on": clock["now"] + 10, "origin": "manual"}, clock["now"] + 11)
    rows = [json.loads(line) for line in thirst.log_path().read_text(encoding="utf-8").splitlines()]
    assert [r for r in rows if r["kind"] == "water"]


def test_the_watch_needs_the_measured_flow(watch: Any) -> None:
    with pytest.raises(CommandRefused):
        go(watch.plan.watch_on({"key": "veg"}))
    with pytest.raises(CommandRefused):
        go(watch.plan.water_now())
    assert watch.box.ports == []


def calibrated(watch: Any) -> None:
    watch.plan.thirst.flow_value = {"calibrated": True, "ml_s": 1.0}
    watch.plan.guard.assign("usb1", "pump", watch.box.snapshot())
    go(watch.start_light())


def test_the_watch_checks_after_lights_on_and_its_run_time_comes_from_the_stage(watch: Any) -> None:
    calibrated(watch)

    async def run() -> None:
        await watch.plan.watch_on({"key": "veg"})
        cfg = watch.watering.cfg
        assert cfg["enabled"] is True and cfg["times"] == ["09:00", "21:00"]
        assert cfg["duration_s"] == 1000  # the veg cap of 1000 ml at 1 ml/s; the need gate shortens it to the dose
        assert watch.box.ports == []  # switching it on starts nothing
        await watch.watering.close()

    go(run())


def test_the_slots_follow_the_light_plan_when_the_stage_flips_to_twelve_hours(watch: Any) -> None:
    calibrated(watch)

    async def run() -> None:
        await watch.plan.watch_on({"key": "veg"})
        watch.log.set_stage("early_flower", "2026-11-20")
        watch.clock[0] = at("2026-11-20T12:00:00")
        await watch.timer.tick()
        assert watch.timer.cfg["off_time"] == "08:00"
        await watch.plan.check(watch.box.snapshot())
        assert watch.watering.cfg["times"] == ["05:00", "21:00"]
        assert watch.watering.cfg["duration_s"] == 1400  # the flower cap
        await watch.watering.close()

    go(run())


def test_a_schedule_the_owner_changed_is_his(watch: Any) -> None:
    calibrated(watch)

    async def run() -> None:
        await watch.plan.watch_on({"key": "veg"})
        await watch.watering.configure({**watch.watering.cfg, "times": ["12:00"]})
        await watch.plan.check(watch.box.snapshot())
        assert watch.watering.cfg["times"] == ["12:00"]
        assert watch.plan.state["water"]["auto"] is False
        await watch.watering.close()

    go(run())


def run_seen(watch: Any, seconds: float) -> None:
    start = watch.clock[0]
    watch.watering.events = [{"at": start, "kind": "start"}, {"at": start + seconds, "kind": "stopped", "reason": "duration"}]


def test_a_run_the_sensor_felt_is_noted_a_run_it_did_not_feel_gets_a_push(watch: Any) -> None:
    watch.soil.rows = [{"at": watch.clock[0] - 600, "percent": 14.0}, {"at": watch.clock[0] + 900, "percent": 31.0}]
    run_seen(watch, 300)

    async def run() -> None:
        await watch.plan.check(watch.box.snapshot())
        assert watch.plan.state["water"]["pending"]
        watch.clock[0] += 301 + grow_plan.WATCH_S
        await watch.plan.check(watch.box.snapshot())

    go(run())
    assert watch.plan.state["log"][-1]["key"] == "gp.log.arrived" and watch.pushes.sent == []
    watch.soil.rows = [{"at": watch.clock[0] - 600, "percent": 14.0}, {"at": watch.clock[0] + 900, "percent": 15.0}]
    run_seen(watch, 300)

    async def again() -> None:
        await watch.plan.check(watch.box.snapshot())
        watch.clock[0] += 301 + grow_plan.WATCH_S
        await watch.plan.check(watch.box.snapshot())
        await watch.plan.check(watch.box.snapshot())

    go(again())
    assert len(watch.pushes.sent) == 1 and "kam nichts an" in watch.pushes.sent[0]


def test_a_short_run_and_a_silent_sensor_say_nothing(watch: Any) -> None:
    run_seen(watch, 10)  # the jug test

    async def run() -> None:
        await watch.plan.check(watch.box.snapshot())
        assert not watch.plan.state["water"].get("pending")
        run_seen(watch, 300)
        watch.watering.events[0]["at"] = watch.clock[0] + 100
        watch.watering.events[1]["at"] = watch.clock[0] + 400
        await watch.plan.check(watch.box.snapshot())
        watch.clock[0] += 500 + grow_plan.WATCH_S
        await watch.plan.check(watch.box.snapshot())

    go(run())
    assert watch.pushes.sent == []


def test_the_plan_never_starts_the_pump_on_a_poll(watch: Any) -> None:
    calibrated(watch)

    async def run() -> None:
        await watch.plan.watch_on({"key": "veg"})
        for _hour in range(0, 48, 3):
            watch.clock[0] += 3 * 3600
            await watch.plan.check(watch.box.snapshot())
        await watch.watering.close()

    go(run())
    assert watch.box.ports == []


def test_slots_for_the_owners_cycles() -> None:
    assert water_slots({"on_time": "20:00", "off_time": "16:00"}) == ["09:00", "21:00"]  # 20/4: after lights on, and 13 h later
    assert water_slots({"on_time": "20:00", "off_time": "08:00"}) == ["05:00", "21:00"]  # 12/12: 3 h before lights off
    assert water_slots({"on_time": "06:00", "off_time": "10:00"}) == ["07:00"]  # too short for a second one
    assert water_slots({"on_time": None, "off_time": None}) == ["09:00"]


# ---------- the card ----------

def test_the_steps_only_hands_can_do_read_their_own_truth(watch: Any) -> None:
    snap = watch.box.snapshot()
    snap.update(thirst={"pump": {"power_confirmed": False, "flow": {"calibrated": False}, "flow_runs": 1}},
                soil={"percent": 14.0, "calibrated": False}, watering={"settings": {"enabled": False}, "active": None})
    steps = {s["id"]: s for s in watch.plan.view(snap, "de")["todo"]}
    assert not any(s["done"] for s in steps.values())
    assert steps["pump"]["runs"] == "Messung 2 von 2" and steps["soak"]["can_water"] is False and steps["watch"]["enabled"] is False
    snap["thirst"]["pump"] = {"power_confirmed": True, "flow": {"calibrated": True, "ml_s": 0.8}, "flow_runs": 2}
    snap["soil"]["calibrated"] = True
    snap["watering"]["settings"]["enabled"] = True
    watch.plan.state["probe"] = {"at": 1, "cm": 16}
    steps = {s["id"]: s for s in watch.plan.view(snap, "de")["todo"]}
    assert all(s["done"] for s in steps.values())
    assert steps["pump"]["done_text"] == "48 ml/min"


def pump_of(watch: Any, **changes: Any) -> dict[str, Any]:
    snap = watch.box.snapshot()
    snap.update(thirst={"pump": {"power_confirmed": False, "flow": {"calibrated": False}}, "decision": {"text": "Noch nicht fällig."}},
                watering={"settings": {"enabled": False}, "active": None, "blocked": "setup"})
    for key, value in changes.items():
        snap[key] = {**snap.get(key, {}), **value}
    return watch.plan.view(snap, "de")["pump"]


def test_the_pump_stands_beside_the_other_devices_and_says_what_it_waits_for(watch: Any) -> None:
    measured = {"pump": {"power_confirmed": True, "flow": {"calibrated": True, "ml_s": 1.0}}, "decision": {"text": "Noch nicht fällig."}}
    first = pump_of(watch)
    assert first["id"] == "pump" and first["port"] == "usb1" and first["name"] == "Wasserpumpe"
    assert first["control_state"] == "needs_setup" and first["short"] == "Pumpe messen"
    assert pump_of(watch, thirst=measured)["short"] == "Gießwache aus"
    on = pump_of(watch, thirst=measured, watering={"settings": {"enabled": True, "times": ["09:00", "21:00"]}, "blocked": None})
    assert on["control_state"] == "ready" and on["short"] == "Gießwache an"
    assert "09:00, 21:00" in on["status"] and "Noch nicht fällig." in on["status"]
    dry = pump_of(watch, thirst=measured, watering={"settings": {"enabled": True, "times": ["09:00"]}, "blocked": "supply"})
    assert dry["control_state"] == "waiting" and "Wasser am Sensor" in dry["status"]
    assert pump_of(watch, watering={"active": {"phase": "running"}})["short"] == "Gießt gerade"
    watch.box.snap["ports"]["usb1"]["on"] = True
    alone = pump_of(watch)
    assert alone["control_state"] == "needs_setup" and alone["short"] == "Läuft ohne Gießplan"


def test_no_ports_reported_means_no_pump_tile(rig: Any) -> None:
    rig.box.snap["ports"] = {}
    assert rig.plan.view(rig.box.snapshot(), "de")["pump"] is None


def test_the_probe_step_comes_back_when_the_plant_has_grown(rig: Any) -> None:
    take(rig, 16.0)
    rig.plan.probe_hung()
    assert rig.plan.view(rig.box.snapshot(), "de")["todo"][0]["done"] is True
    rig.clock[0] = at("2026-11-12T12:00:00")
    take(rig, 32.0)
    take(rig, 32.0)
    assert rig.plan.view(rig.box.snapshot(), "de")["todo"][0]["done"] is False


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_view_is_plain_data_in_one_language(rig: Any, lang: str) -> None:
    take(rig, 16.2)
    go(rig.start_light())
    view = rig.plan.view(rig.box.snapshot(), lang)
    json.dumps(view)
    assert view["configured"] is True and view["title"] == "Purple Punch"
    assert view["stage"] == {"key": "veg", "confirmed": True}
    assert view["settings"] == {"light_goal": 100, "feed_from": "2026-11-20"}
    feed = next(t for t in view["tiles"] if t["id"] == "feed")
    assert feed["value"] == "14"
    assert ("20.11.2026" in feed["sub"]) == (lang == "de") and ("Nov 20, 2026" in feed["sub"]) == (lang == "en")
    assert view["photo"]["top_y"] == 0.47


def test_a_stage_that_is_not_set_is_said() -> None:
    chosen_run()
    view = GrowPlan(Box(), PlantLog()).view(Box().snapshot(), "de")
    assert view["stage"]["confirmed"] is False and "Phase noch nicht gesetzt" in view["warning"]


def test_the_plan_survives_a_restart(rig: Any) -> None:
    take(rig, 16.2)
    rig.plan._save()
    again = GrowPlan(rig.box, rig.log, clock=lambda: rig.clock[0])
    assert again.state["height"]["cm"] == 16.2 and again.state["light"]["goal"] == 100


# ---------- the plant's record ----------

def active_record(log: PlantLog | None = None) -> str:
    """The owner's way: a record in the box, then the step that makes it the cockpit's run."""
    log = log or PlantLog()
    workspace.initialize("Box", load_profile(), log)
    view = workspace.view()
    ident = workspace.mutate({"revision": view["revision"], "action": "plant_create", "box": workspace.NATIVE_BOX, "name": "Purple Punch",
                              "variety": "Purple Punch", "phase": "", "started": "2026-11-05"})
    plant_run.activate(workspace.plant_record(ident), log)
    return ident


def row_of(ident: str, lang: str = "de") -> dict[str, Any]:
    return next(p for p in workspace.view(lang)["plants"] if p["id"] == ident)


def test_the_camera_writes_on_the_record_and_a_typed_height_stays() -> None:
    ident = active_record()
    assert workspace.camera_height("2026-11-05", 16.2) is True
    row = row_of(ident)["heights"][0]
    assert row["cm"] == 16.2 and row["method"] == "camera_strip" and row["estimated"] is True
    assert workspace.camera_height("2026-11-05", 16.8) is True  # the day's median moved
    workspace.mutate({"revision": workspace.view()["revision"], "action": "height", "plant": ident, "day": "2026-11-05", "cm": 20})
    assert workspace.camera_height("2026-11-05", 17) is False
    assert row_of(ident)["heights"][0]["cm"] == 20


def test_the_owners_real_height_reaches_the_record_and_the_camera_then_leaves_it() -> None:
    ident = active_record()
    assert workspace.camera_height("2026-11-05", 16.2) is True
    assert workspace.owner_height("2026-11-05", 20.0) is True
    row = row_of(ident)["heights"][0]
    assert row["cm"] == 20.0 and "method" not in row and "estimated" not in row
    assert workspace.camera_height("2026-11-05", 17) is False
    assert row_of(ident)["heights"][0]["cm"] == 20.0


def test_the_owners_stage_shows_on_the_record() -> None:
    ident = active_record()
    assert workspace.sync_stage("Wachstum", "Vegetative", "2026-11-05") is True
    row = row_of(ident)
    assert row["phase"] == "Wachstum" and row["phase_since"] == "2026-11-05"
    assert row_of(ident, "en")["phase"] == "Vegetative"


def test_without_an_active_record_the_camera_and_the_stage_write_nothing() -> None:
    log = PlantLog()
    workspace.initialize("Box", load_profile(), log)
    view = workspace.view()
    workspace.mutate({"revision": view["revision"], "action": "plant_finish", "id": workspace.NATIVE_PLANT, "ended": "2026-11-05",
                      "confirmed": True, "summary": ""})
    assert workspace.camera_height("2026-11-05", 16) is False
    assert workspace.sync_stage("Wachstum", "Vegetative", "2026-11-05") is False


def test_the_stage_reaches_a_record_that_comes_later(rig: Any) -> None:
    go(rig.plan.check(rig.box.snapshot()))
    assert rig.plan.state["stage_seen"]["record"] is False  # plant-test has no record yet
    ident = active_record(rig.log)
    rig.log.set_stage("veg", "2026-11-06")
    go(rig.plan.check(rig.box.snapshot()))
    assert rig.plan.state["stage_seen"]["record"] is True
    assert row_of(ident)["phase"] == "Wachstum"


# ---------- the server ----------

def test_state_and_commands_reach_the_plan() -> None:
    chosen_run()
    box = DemoBox()
    log = PlantLog()
    log.set_stage("veg", "2026-11-06")
    plan = GrowPlan(box, log)

    async def run() -> None:
        async with TestClient(TestServer(make_app(box, Alerts(), "k", plant_log=log, grow_plan=plan))) as client:
            assert (await client.post("/api/cmd", json={"action": "grow_plan_probe"})).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            state = await (await client.get("/api/state")).json()
            assert state["grow_plan"]["title"] == "Purple Punch"
            answer = await client.post("/api/cmd", json={"action": "grow_plan_probe"})
            assert answer.status == 200 and plan.state["probe"]
            saved = await client.post("/api/cmd", json={"action": "grow_plan_settings", "light_goal": 100, "feed_from": "2026-11-20"})
            assert saved.status == 200 and plan.state["light"]["goal"] == 100 and log.data["feed_from"] == "2026-11-20"
            assert (await client.post("/api/cmd", json={"action": "grow_plan_settings", "light_goal": 5})).status == 409
            bad = await client.post("/api/cmd", json={"action": "grow_plan_mark", "id": "nope"})
            assert bad.status == 409
            assert (await client.post("/api/cmd", json={"action": "grow_plan_mark", "id": "trellis"})).status == 200
            assert plan.state["marks"]["trellis"]["done"] is True
            assert (await client.get("/api/grow_plan/photo")).status == 404
            plan.photo_path().parent.mkdir(parents=True, exist_ok=True)
            plan.photo_path().write_bytes(b"\xff\xd8jpeg")
            photo = await client.get("/api/grow_plan/photo")
            assert photo.status == 200 and photo.content_type == "image/jpeg"

    go(run())


def test_a_cockpit_without_the_plan_has_no_card() -> None:
    box = DemoBox()

    async def run() -> None:
        async with TestClient(TestServer(make_app(box, Alerts(), "k", plant_log=PlantLog()))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            assert "grow_plan" not in await (await client.get("/api/state")).json()
            assert (await client.post("/api/cmd", json={"action": "grow_plan_probe"})).status == 409

    go(run())


def test_the_glance_and_the_automation_list_show_the_pump() -> None:
    cockpit = (REPO / "web" / "cockpit.js").read_text(encoding="utf-8")
    app = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    assert "s.grow_plan?.pump" in cockpit and 'd.id === "humidifier" || d.id === "pump"' in cockpit
    assert "rows.push(s.grow_plan.pump)" in app


def test_before_the_owners_date_the_overview_names_the_date_not_the_charts_next_step() -> None:
    cockpit = (REPO / "web" / "cockpit.js").read_text(encoding="utf-8")
    app = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    assert "p?.feeding?.not_before ? p.feeding.warning" in cockpit
    assert 'choosing || f.not_before ? "" : feedNextChange(f)' in app


def test_the_page_loads_the_plans_files() -> None:
    from vgrow.server import PAGE_FILES

    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="grow-plan"' in html and "/static/plan.js" in html and "/static/plan.css" in html
    assert "plan.js" in PAGE_FILES
