"""The autopilot (vgrow/autopilot.py): every rule, lock and limit, manual override, port guard, command history,
restart, the undo button, the recorded day in observation mode and a closed-loop toy box.

A fake clock drives everything; one "poll" is 90 s like the real box (box.POLL_SECONDS), the reading 60 s old.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import autopilot as ap
from vgrow import automation as am
from vgrow import config
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation
from vgrow.autopilot import Autopilot, AutopilotHub
from vgrow.box import CommandRefused
from vgrow.budget import CommandBudget
from vgrow.plant import PlantLog
from vgrow.portguard import PortGuard
from vgrow.server import COOKIE, make_app

REPO = Path(__file__).resolve().parent.parent
CARD = json.loads((REPO / "kartei" / "stages" / "autoflower.json").read_text(encoding="utf-8"))
STAGES = {s["key"]: s for s in CARD["stages"]}
POLL = 90
EM_DASH = chr(0x2014)
# One event loop for the whole module: a simulated day is about a thousand polls, and arun() per poll opens a
# new loop with its own loopback socket pair each time. On Windows that used up the ephemeral ports (TIME_WAIT) and
# stalled the run, and every other program on the machine with it.
LOOP = asyncio.new_event_loop()


def arun(coro: Any) -> Any:
    return LOOP.run_until_complete(coro)


@pytest.fixture(autouse=True)
def no_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thousands of simulated polls: plain writes instead of temp file, flush and replace, or a simulated day takes
    minutes on Windows (the virus scanner looks at every new temp file). The atomic write itself is config's own
    tested code; the file contents are the same."""
    monkeypatch.setattr(config.os, "fsync", lambda fd: None)

    def plain_write(path: Path, text: str) -> None:  # one file operation instead of three per simulated poll
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(config, "atomic_write", plain_write)


def stage(key: str = "early_flower", set_by: str = "manual") -> dict[str, Any]:
    return {**STAGES[key], "set_by": set_by}


def vpd(temp: float, humidity: float) -> float:
    return round(0.6108 * math.exp(17.27 * temp / (temp + 237.3)) * (1 - humidity / 100), 2)


class FakeBox:
    """Reports what it is told. Commands land at once, unless `silent` (the box never confirms)."""

    def __init__(self) -> None:
        self.light = {"level": 50, "mode": 0, "in_plan": False}
        self.dfan = {"step": 5, "mode": 0}
        self.cfan = {"step": 10, "natural_wind": True, "mode": 0}
        self.ports = {"usb1": {"on": True, "mode": 0, "in_plan": 0, "accessory": 0, "can_write": True},
                      "usb2": {"on": False, "mode": 0, "in_plan": 0, "accessory": 0, "can_write": True}}
        self.climate: dict[str, float | None] = {"probe_temp": 23.0, "probe_humidity": 52.0, "probe_vpd": 1.2,
                                                 "humidity": 45.0, "temp": 24.0}
        self.sensors_age: float | None = 60
        self.mqtt = True
        self.silent = False
        self.refuse = False
        self.sent: list[tuple[str, Any]] = []

    def set_climate(self, temp: float, humidity: float, box_humidity: float | None = None) -> None:
        self.climate.update(probe_temp=temp, probe_humidity=humidity, probe_vpd=vpd(temp, humidity),
                            humidity=humidity - 8 if box_humidity is None else box_humidity)

    def snapshot(self) -> dict[str, Any]:
        return {"device": "VGrow Test", "online": True, "mqtt": self.mqtt, "sensors_age": self.sensors_age,
                "climate": dict(self.climate), "light": dict(self.light), "dfan": dict(self.dfan),
                "cfan": dict(self.cfan), "ports": json.loads(json.dumps(self.ports)), "pending": {}}

    async def _cmd(self, name: str, value: Any, apply: Callable[[], None]) -> None:
        if self.refuse:
            raise CommandRefused("no live connection")
        self.sent.append((name, value))
        if not self.silent:
            apply()

    async def set_light(self, level: int) -> None:
        await self._cmd("light", level, lambda: self.light.update(level=level))

    async def set_dfan(self, step: int) -> None:
        await self._cmd("dfan", step, lambda: self.dfan.update(step=step))

    async def set_cfan(self, step: int | None, natural_wind: bool = False) -> None:
        await self._cmd("cfan", step, lambda: self.cfan.update(step=step))

    async def set_port(self, port: str, on: bool) -> None:
        await self._cmd(f"port.{port}", on, lambda: self.ports[port].update(on=on))


class Rig:
    """A pilot, its box and a clock; run() polls every 90 s."""

    def __init__(self, *, st: dict[str, Any] | None = None, mode: str = "auto", lamp: bool = False,
                 box: FakeBox | None = None, start: float = 1_792_652_580.0, budget: CommandBudget | None = None,
                 humidifier: Any = None, box_id: str = "box-1") -> None:
        self.clock = [start]
        self.box_id = box_id  # two rigs in one test need two boxes: one server regulates one box (the lock)
        self.box = box or FakeBox()
        self.stage = st if st is not None else stage()
        self.budget = budget or CommandBudget(clock=lambda: self.clock[0])
        self.log = PlantLog()
        self.pilot = self.make(humidifier)
        if mode != "off":
            self.pilot.set_mode(mode)
        if lamp:
            self.pilot.confirm_lamp()

    def make(self, humidifier: Any = None) -> Autopilot:
        return Autopilot(self.box, box_id=self.box_id, clock=lambda: self.clock[0], stage_source=lambda: (
            None if self.stage.get("set_by") == "manual" else "stage", self.stage), plant_log=self.log,
            budget=self.budget, humidifier=humidifier)

    def poll(self) -> list[dict[str, Any]]:
        self.clock[0] += POLL
        return arun(self.pilot.check(self.box.snapshot()))

    def run(self, minutes: float, each: Callable[[Rig], None] | None = None) -> list[dict[str, Any]]:
        out = []
        for _ in range(int(minutes * 60 // POLL)):
            if each is not None:
                each(self)
            out += self.poll()
        return out

    def kinds(self, *kinds: str) -> list[dict[str, Any]]:
        return [e for e in self.pilot.entries(5000) if e.get("kind") in kinds]


# ---------- modes, defaults, kill switch ----------

def test_default_is_off_and_off_does_nothing() -> None:
    rig = Rig(mode="off")
    assert rig.pilot.state["mode"] == "off"
    rig.box.set_climate(30.0, 75.0)  # far too warm and humid
    assert rig.run(120) == [] and rig.box.sent == []
    assert rig.pilot.view()["status"] == "aus"


def test_observe_logs_what_it_would_do_and_sends_nothing() -> None:
    rig = Rig(mode="observe")
    rig.box.set_climate(23.0, 66.0)  # humidity above 60 + 2
    out = rig.run(60)
    assert rig.box.sent == []
    would = [e for e in out if e["kind"] == "would"]
    assert would and would[0]["device"] == "dfan" and (would[0]["from"], would[0]["to"]) == (5, 6)
    view = rig.pilot.view(lang="de")
    line = next(e for e in view["log"] if e["kind"] == "would" and e["from"] == 5)
    assert line["text"].startswith("Autopilot würde: Abluft 5 auf 6, weil die Feuchte am Fühler mit")
    assert line["undo"] is False  # nothing was sent, nothing to undo
    en = next(e for e in rig.pilot.view(lang="en")["log"] if e["kind"] == "would" and e["from"] == 5)
    assert en["text"].startswith("Autopilot would: Exhaust fan 5 to 6, because the probe humidity is")
    assert rig.pilot.view(lang="de")["status"] == "beobachtet 3 von 3 Geräten und schaltet nichts"


def test_auto_sends_and_the_box_confirms() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    assert rig.box.sent == [("dfan", 6)]
    step = rig.kinds("step")[0]
    assert step["box"] == "box-1" and step["status"] == "sent"
    rig.poll()
    item = next(e for e in rig.pilot.view()["log"] if e["kind"] == "step")
    assert item["status"] == "ok" and item["undo"] is True


def test_nothing_in_the_first_two_polls_and_ten_minutes_hold() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.poll()
    rig.poll()
    assert rig.pilot.view()["status"] == "startet: die ersten zwei Abfragen schaltet er nichts"
    rig.run(9)  # median of 3+ readings, then 10 minutes: not yet
    assert rig.box.sent == []
    rig.run(6)
    assert rig.box.sent == [("dfan", 6)]


def test_env_kill_switch_forces_off_and_refuses_on(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()
    monkeypatch.setenv("VGROW_AUTOPILOT", "0")
    again = rig.make()
    assert again.state["mode"] == "off" and again.forced_off
    with pytest.raises(CommandRefused):
        again.set_mode("auto")
    monkeypatch.delenv("VGROW_AUTOPILOT")
    config.ENV_FILE.write_text("VGROW_AUTOPILOT=0\n", encoding="utf-8")
    assert rig.make().forced_off


def test_off_touches_only_the_autopilot_and_the_failsafe_still_watches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1, M4 and review 2, M1: off used to switch the humidifier rule off too, and with the rule off its
    fail-safe never ran: a running humidifier stayed on with nothing watching it once the data went stale."""
    box, hum = humidifier_rig(monkeypatch)
    box.climate["probe_humidity"] = 20.0
    assert arun(hum.check(box.snapshot())) == "humidifier_on"
    rig = Rig(box=box, humidifier=hum)
    rig.pilot.set_mode("off")
    assert rig.pilot.state["mode"] == "off" and hum.state["enabled"] is True  # one call, the autopilot only
    hum.set_enabled(False)  # the owner switches the rule off as well: the fail-safe watches all the same
    box.sensors_age = 5000
    assert arun(hum.check(box.snapshot())) == "failsafe_off" and box.ports["usb2"]["on"] is False
    assert box.sent == [("port.usb2", True), ("port.usb2", False)]


# ---------- data ----------

def test_stale_or_missing_data_means_no_change() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.box.sensors_age = 700
    rig.run(60)
    assert rig.box.sent == [] and "älter als 10 Minuten" in rig.pilot.view()["status"]
    rig.box.sensors_age = 60
    rig.box.dfan["step"] = None  # type: ignore[assignment]  # no reported device value
    rig.run(60)
    assert rig.box.sent == []
    rig.box.dfan["step"] = 5
    rig.box.mqtt = False
    rig.run(60)
    assert rig.box.sent == []


def test_a_gap_restarts_the_median_and_the_hold() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(9)
    rig.clock[0] += 6 * 60  # six minutes without a reading: what was held so far does not count
    rig.run(12)  # three readings for the median, then 7.5 of the 10 minutes
    assert rig.box.sent == []
    rig.run(6)
    assert rig.box.sent == [("dfan", 6)]


def test_only_a_confirmed_stage_enables_regulation() -> None:
    rig = Rig(st=stage(set_by="estimate"))
    rig.box.set_climate(23.0, 66.0)
    rig.run(60)
    assert rig.box.sent == [] and rig.pilot.view()["status"] == "wartet auf eine bestätigte Phase"
    rig.stage = stage("harvest")
    rig.run(60)
    assert rig.box.sent == []


# ---------- exhaust fan ----------

def test_exhaust_keeps_its_timing_and_bounds_without_a_daily_command_quota() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 70.0)  # stays humid: the open loop, nothing reacts
    rig.run(24 * 60)
    steps = [e for e in rig.kinds("step") if e["device"] == "dfan"]
    times = [e["at"] for e in steps]
    assert all(b - a >= ap.DFAN_SAME_GAP for a, b in zip(times, times[1:]))
    assert max(e["to"] for e in steps) <= 8  # base 5 + 3, never beyond
    # two steps up, no effect: the windup rule takes it back to the base and rests the humidity rule for 6 h
    assert [(e["from"], e["to"]) for e in steps][:3] == [(5, 6), (6, 7), (7, 5)]
    assert len(steps) > 8  # the old daily command quota no longer stops later cycles


def test_mist_lock_humidity_and_vpd_do_not_drive_the_exhaust() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 80.0, box_humidity=48.0)  # probe 32 points above the box: mist
    rig.run(120)
    assert rig.box.sent == []
    assert any("Nebel am Fühler" in e["text"] for e in rig.pilot.view()["log"])
    rig.box.set_climate(28.0, 80.0, box_humidity=48.0)  # the temperature still counts
    rig.run(30)
    assert rig.box.sent == [("dfan", 6)]


def test_cold_and_humid_holds_instead_of_flipping() -> None:
    rig = Rig()
    rig.box.light["level"] = 0  # night: band 18-22, no VPD votes
    rig.box.set_climate(17.2, 66.0)
    rig.run(6 * 60)
    assert rig.box.sent == []
    assert any("Zu kalt und zu feucht" in e["text"] for e in rig.pilot.view()["log"])


def test_vpd_counts_only_with_the_lights_on() -> None:
    rig = Rig()
    rig.box.light["level"] = 0
    rig.box.set_climate(19.0, 58.0)  # VPD about 0.9, below 1.0 - 0.05; temperature and humidity in band
    rig.run(120)
    assert rig.box.sent == []
    rig.box.light["level"] = 50
    rig.box.set_climate(20.5, 61.5)  # VPD about 0.93; temperature and humidity still in the day band
    rig.run(30)
    assert rig.box.sent == [("dfan", 6)]


def test_app_automation_is_left_alone_unless_the_owner_allows_it() -> None:
    rig = Rig()
    rig.box.dfan["mode"] = 1  # VIVOSUN's own exhaust automation runs
    rig.box.set_climate(23.0, 66.0)
    rig.run(60)
    assert rig.box.sent == []
    assert "Automatik der VIVOSUN-App" in next(d for d in rig.pilot.view()["devices"] if d["id"] == "dfan")["status"]
    rig.pilot.set_device("dfan", app=True)
    rig.run(30)
    assert rig.box.sent == [("dfan", 6)]


def test_manual_level_and_suggest() -> None:
    rig = Rig()
    rig.pilot.set_device("dfan", "manual")
    rig.box.set_climate(23.0, 66.0)
    rig.run(60)
    assert rig.box.sent == [] and not rig.kinds("would", "suggest")
    rig.pilot.set_device("dfan", "suggest")
    rig.run(30)
    assert rig.box.sent == [] and rig.kinds("suggest")[0]["to"] == 6


# ---------- circulation fan ----------

def test_circulation_fan_takes_the_stage_preset_once_and_never_with_natural_wind() -> None:
    rig = Rig()
    rig.stage = stage("mid_flower")  # a stage change
    rig.run(30)
    assert rig.box.sent == []  # natural wind on
    rig2 = Rig(box_id="box-2")
    rig2.box.cfan.update(natural_wind=False, step=5)
    rig2.run(30)
    assert rig2.box.sent == []  # switching on is no stage change: the step the owner runs is his (review 2, S6)
    rig2.stage = stage("mid_flower")
    rig2.run(30)
    assert ("cfan", 10) in rig2.box.sent  # the next stage: its preset, once
    rig2.box.cfan["step"] = 6  # the owner sets it in the app: never set back
    rig2.run(24 * 60)
    assert rig2.box.sent.count(("cfan", 10)) == 1


def test_a_manual_circulation_step_before_the_preset_ran_is_never_overridden() -> None:
    """Review 1, S4: the owner's step from the cockpit, and after the hour's pause the preset sent 10 anyway."""
    rig = Rig()
    rig.box.cfan.update(natural_wind=False, step=6)
    rig.stage = stage("mid_flower")  # the preset would be due now ...
    rig.pilot.manual("cfan", 6)  # ... but the owner sets 6 first
    rig.run(3 * 60)
    assert ("cfan", 10) not in rig.box.sent
    rig.box.cfan["step"] = 4  # and the same for a change in the app
    rig.stage = stage("late_flower")
    rig.poll()
    rig.run(3 * 60)
    assert ("cfan", 10) not in rig.box.sent


# ---------- light ----------

def test_light_ramp_needs_the_lamp_confirmation_and_goes_25_per_day() -> None:
    rig = Rig()
    rig.run(3 * 60)
    assert not [s for s in rig.box.sent if s[0] == "light"]
    assert rig.pilot.view()["needs"]["lamp"] is True
    rig.pilot.confirm_lamp()
    rig.run(60)
    assert [s for s in rig.box.sent if s[0] == "light"] == [("light", 75)]
    rig.run(20 * 60)
    assert [s for s in rig.box.sent if s[0] == "light"] == [("light", 75)]  # 24 h after the last change
    rig.run(5 * 60)
    assert [s for s in rig.box.sent if s[0] == "light"] == [("light", 75), ("light", 100)]
    rig.run(26 * 60)
    assert max(v for d, v in rig.box.sent if d == "light") == 100  # never above the preset


def test_light_never_from_zero_never_above_preset_and_held_while_warm() -> None:
    rig = Rig(lamp=True)
    rig.box.set_climate(20.5, 52.0)  # inside the night band and the day band
    rig.box.light["level"] = 0
    rig.run(3 * 60)
    assert rig.box.sent == []
    rig.box.light["level"] = 90  # on again, at another level than before: the owner's change
    rig.poll()
    rig.pilot.confirm_lamp()
    rig.clock[0] += 25 * 3600
    rig.run(3 * 60)
    assert [s for s in rig.box.sent if s[0] == "light"] == [("light", 100)]  # 90 + 25 stops at the preset
    warm = Rig(lamp=True, box_id="box-2")
    warm.box.set_climate(25.5, 58.0)  # less than 1 °C below the day maximum 26, VPD in band
    warm.run(4 * 60)
    assert not [s for s in warm.box.sent if s[0] == "light"]
    assert "30 Minuten unter 25,0 °C" in warm.pilot.view()["devices"][0]["status"]
    dry = Rig(lamp=True, box_id="box-3")
    dry.box.set_climate(24.0, 35.0)  # VPD about 1.9, above 1.4
    dry.run(4 * 60)
    assert not [s for s in dry.box.sent if s[0] == "light"]
    assert "VPD im Band" in dry.pilot.view()["devices"][0]["status"]


def test_light_only_in_the_window_after_lights_on_and_never_in_a_plan() -> None:
    rig = Rig(lamp=True)
    rig.box.set_climate(20.5, 52.0)
    rig.box.light["level"] = 0
    rig.run(60)
    rig.box.light["level"] = 50  # lights on, observed: the light cycle, not a manual change
    rig.run(57)
    assert rig.box.sent == []  # within the first hour
    rig.run(20)
    assert rig.box.sent == [("light", 75)]
    assert not rig.kinds("manual")
    plan = Rig(lamp=True, box_id="box-2")
    plan.box.light["in_plan"] = True
    plan.run(3 * 60)
    assert plan.box.sent == [] and "Plan der VIVOSUN-App" in plan.pilot.view()["devices"][0]["status"]


def test_a_manual_light_change_ends_the_ramp_until_the_lamp_is_confirmed_again() -> None:
    rig = Rig(lamp=True)
    rig.poll()
    rig.box.light["level"] = 60  # changed in the app
    rig.run(3 * 60)
    assert not [s for s in rig.box.sent if s[0] == "light"]
    rig.clock[0] += 25 * 3600
    rig.run(3 * 60)
    assert not [s for s in rig.box.sent if s[0] == "light"]  # the series stays stopped
    rig.pilot.confirm_lamp()
    rig.run(2 * 60)
    assert [s for s in rig.box.sent if s[0] == "light"] == [("light", 85)]


def test_light_goes_back_after_its_own_increase_with_a_large_command_history() -> None:
    rig = Rig(lamp=True)
    rig.run(90)
    assert rig.box.sent == [("light", 75)]
    for _ in range(30):
        rig.budget.record("humidifier", "port.usb2")
    rig.box.set_climate(27.8, 45.0)  # more than day max 26 + 1.5
    rig.run(45)  # the fan can now act too; allow its settling time before the light's heat hold
    assert rig.box.sent == [("light", 75), ("dfan", 6), ("light", 50)]
    assert [s for s in rig.kinds("step") if s["device"] == "light"][-1]["safety"] is True


# ---------- manual always wins ----------

def test_a_change_in_the_app_pauses_the_device_and_the_new_value_becomes_the_base() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    assert rig.box.sent == [("dfan", 6)]
    rig.box.dfan["step"] = 3  # the owner turns it down in the VIVOSUN app
    rig.run(59)
    assert rig.box.sent == [("dfan", 6)]
    manual = rig.kinds("manual")[0]
    assert manual["source"] == "app" and (manual["from"], manual["to"]) == (6, 3)
    rig.run(3)
    assert rig.pilot.state["base"] == 3  # regulates from the owner's value, never back to a stored one
    rig.run(30)
    assert rig.box.sent[-1] == ("dfan", 4) and ("dfan", 6) not in rig.box.sent[1:]


def test_observing_does_not_use_up_the_real_circulation_preset() -> None:
    """Found on the demo screenshots: the would-be preset step in observation marked the stage as done, so
    Automatic never set the circulation fan."""
    rig = Rig(mode="observe")
    rig.box.cfan.update(natural_wind=False, step=5)
    rig.stage = stage("mid_flower")
    rig.run(6)
    assert rig.kinds("would")[0]["device"] == "cfan" and rig.box.sent == []
    rig.pilot.set_mode("auto")
    rig.run(3)
    assert rig.box.sent == [("cfan", 10)]


def test_its_own_step_is_never_taken_for_the_owners_after_a_mode_change() -> None:
    """Found on the demo screenshots: a step, then off and observe before the next poll, booked the autopilot's own
    change as one from the VIVOSUN app."""
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(15)
    assert rig.box.sent == [("dfan", 6)]
    rig.pilot.set_mode("off")
    rig.pilot.set_mode("observe")
    rig.run(10)
    assert not rig.kinds("manual")
    assert next(e for e in rig.pilot.view()["log"] if e["kind"] == "step")["status"] == "ok"


def test_a_cockpit_command_pauses_only_that_device_for_an_hour() -> None:
    rig = Rig()
    rig.pilot.manual("dfan", 7)
    rig.box.dfan["step"] = 7
    rig.box.set_climate(23.0, 66.0)
    rig.run(58)
    assert [s for s in rig.box.sent if s[0] == "dfan"] == []
    assert not rig.kinds("manual")[1:]  # the cockpit's own change is not booked a second time as an app change
    assert rig.pilot.view()["devices"][0]["id"] == "light" and rig.pilot.view()["devices"][1]["paused_until"]
    rig.run(40)
    assert ("dfan", 8) in rig.box.sent


def test_a_manual_change_between_decision_and_send_wins() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(13)
    real = rig.box.snapshot

    def moved() -> dict[str, Any]:  # the owner moves it right as the autopilot is about to send
        rig.box.dfan["step"] = 4
        return real()

    rig.box.snapshot = moved  # type: ignore[method-assign]
    arun(rig.pilot._act({"device": "dfan", "frm": 5, "to": 6, "dir": 1, "virtual": False,
                                "reason": ap.text("ap.reason.hum_high", value=1, limit=2)}, real(), rig.clock[0]))
    assert rig.box.sent == []


# ---------- failures ----------

def test_one_retry_then_a_pause_and_three_in_a_row_switch_it_off() -> None:
    rig = Rig()
    rig.box.silent = True
    rig.box.set_climate(23.0, 66.0)
    rig.run(20)
    assert rig.box.sent == [("dfan", 6), ("dfan", 6)]  # one retry
    assert rig.kinds("failed") and rig.pilot.state["paused"]["dfan"]
    rig.pilot.state["fails_in_row"] = 2
    rig.pilot.state["paused"] = {}
    rig.run(60)
    assert rig.pilot.state["mode"] == "off"


def test_a_refused_send_is_no_attempt() -> None:
    rig = Rig()
    rig.box.refuse = True
    rig.box.set_climate(23.0, 66.0)
    rig.run(60)
    assert rig.pilot.state["in_flight"] == {} and not rig.kinds("failed") and rig.budget.counts()["auto_day"] == 0


# ---------- command history without count quotas ----------

def test_account_history_does_not_block_the_autopilot() -> None:
    rig = Rig()
    for _ in range(30):
        rig.budget.record("humidifier", "port.usb2")
    rig.box.set_climate(23.0, 66.0)
    rig.run(20)
    assert rig.box.sent == [("dfan", 6)]
    assert rig.budget.counts()["auto_day"] == 31
    assert rig.pilot.view()["budget"]["limited"] is False
    assert not any("Befehlsgrenze" in e["text"] for e in rig.pilot.view()["log"])


def test_real_and_virtual_device_histories_impose_no_command_quotas() -> None:
    rig = Rig()
    now = rig.clock[0]
    for prefix in ("", "v_"):
        rig.pilot._virtual = bool(prefix)
        rig.pilot.state[prefix + "history"]["dfan"] = [now - i * 60 for i in range(30)]
        rig.pilot.state[prefix + "last_step"]["dfan"] = {"at": now - ap.DFAN_SAME_GAP - 1, "dir": 1}
        assert rig.pilot._limits_ok("dfan", 1, now, False) is None
        rig.pilot.state[prefix + "last_step"]["dfan"]["at"] = now
        assert rig.pilot._limits_ok("dfan", 1, now, False)["key"] == "ap.hold.gap"


def test_the_humidifier_failsafe_retries_with_backoff_and_is_never_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(am, "CONFIRM_SECONDS", 0)
    profile = {"plant": {"start_date": "2026-08-31"}, "stages": [{"key": "bloom", "from_day": 0, "to_day": 999,
               "targets": [{"metric": "probe_humidity", "min": 40, "max": 55}]}]}
    monkeypatch.setattr(am, "load_profile", lambda: profile)
    clock = [1000.0]
    monkeypatch.setattr(am.time, "time", lambda: clock[0])
    box = FakeBox()
    box.silent = True
    box.ports["usb2"]["on"] = True
    budget = CommandBudget(clock=lambda: clock[0])
    for _ in range(30):
        budget.record("autopilot", "dfan")
    log = PlantLog()
    log.set_stage("bloom")
    hum = HumidifierAutomation(box, None, log, budget=budget)
    hum.set_enabled(True)
    box.sensors_age = 2000
    for _ in range(40):  # an hour of polls
        arun(hum.check(box.snapshot()))
        clock[0] += POLL
    assert box.sent[0] == ("port.usb2", False)
    # at 0 s, 90 s, then +3, +6, +12 and every 15 min: 7 commands in the hour instead of 40, none held by the cap
    assert len(box.sent) == 7
    assert budget.counts(1000 + 3550)["safety_hour"] == 7


def test_manual_commands_are_counted_never_blocked() -> None:
    async def go() -> None:
        from vgrow.demo import DemoBox

        box = DemoBox()
        client = TestClient(TestServer(make_app(box, Alerts(), "k")))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})
        try:
            for step in (3, 4, 5, 6, 7):
                assert (await client.post("/api/cmd", json={"action": "dfan", "step": step})).status == 200
        finally:
            await client.close()

    asyncio.run(go())
    from vgrow.budget import BUDGET

    assert BUDGET.counts()["manual_hour"] == 5 and BUDGET.counts()["auto_hour"] == 0


# ---------- the port guard ----------

def write_ports(tmp: Path, monkeypatch: pytest.MonkeyPatch, usb2: dict[str, Any]) -> None:
    path = tmp / "ports.json"
    path.write_text(json.dumps({"usb1": {"label": "Kamera", "device": "camera", "always_on": True}, "usb2": usb2}),
                    encoding="utf-8")
    monkeypatch.setattr(config, "PORTS_FILE", path)
    monkeypatch.setattr(config, "_ports_good", None)


def humidifier_rig(monkeypatch: pytest.MonkeyPatch, *, named: str | None = "humidifier",
                   clock: list[float] | None = None) -> tuple[FakeBox, HumidifierAutomation]:
    """The humidifier rule on USB 2 of a FakeBox, the owner having named `named` on USB 2 (None: nobody did)."""
    profile = {"plant": {"start_date": "2026-08-31"}, "stages": [{"key": "bloom", "from_day": 0, "to_day": 999,
               "targets": [{"metric": "probe_humidity", "min": 40, "max": 55}]}]}
    monkeypatch.setattr(am, "load_profile", lambda: profile)
    monkeypatch.setattr(am, "CONFIRM_SECONDS", 0)
    if clock is not None:
        monkeypatch.setattr(am.time, "time", lambda: clock[0])
    box = FakeBox()
    log = PlantLog()
    log.set_stage("bloom")
    hum = HumidifierAutomation(box, None, log, budget=CommandBudget())
    if named is not None:
        assert hum.guard.assign("usb2", named, box.snapshot())
    hum.set_enabled(True)
    return box, hum


def test_naming_the_humidifier_where_ports_json_names_the_pump_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's real ports.json since 23.10.2026: USB 1 is the drip pump and locks nothing. "An USB 1 hängt:
    Befeuchter" would be stored and change nothing (ports.json vetoes it), so the route answers 409; naming the pump
    there, or "other" on USB 2, stays allowed (it only ever stops the humidifier rule)."""
    rules = config.port_rules()
    assert rules is not None and rules["usb1"]["device"] == "pump" and not rules["usb1"].get("always_on")
    box, hum = humidifier_rig(monkeypatch)
    hub = AutopilotHub([Rig(box=box).pilot])

    async def go() -> None:
        from vgrow.demo import DemoBox

        class Same(DemoBox):
            def snapshot(self) -> dict[str, Any]:
                return box.snapshot()

        client = TestClient(TestServer(make_app(Same(), Alerts(), "k", autopilot=hub)))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})
        try:
            r = await client.post("/api/cmd", json={"action": "port_assign", "port": "usb1", "device": "humidifier"})
            assert r.status == 409 and "Tropfbewässerung (Pumpe)" in (await r.json())["error"]
            r = await client.post("/api/cmd", json={"action": "port_assign", "port": "usb1", "device": "humidifier"},
                                  headers={"X-Lang": "en"})
            assert r.status == 409 and "Drip irrigation (pump)" in (await r.json())["error"]
            r = await client.post("/api/cmd", json={"action": "port_assign", "port": "usb1", "device": "pump"})
            assert r.status == 200
        finally:
            await client.close()

    asyncio.run(go())
    assert hum.guard.owner_device("usb1") == "pump"


def test_a_pump_on_usb2_is_never_switched_by_humidity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_ports(tmp_path, monkeypatch, {"label": "Pumpe", "label_en": "Pump", "device": "pump"})
    box, hum = humidifier_rig(monkeypatch)
    box.climate["probe_humidity"] = 20.0  # far too dry: the humidifier rule would switch on
    assert arun(hum.check(box.snapshot())) == "paused_port_not_humidifier"
    box.sensors_age = 5000  # and the fail-safe does not switch it either
    box.ports["usb2"]["on"] = True
    assert arun(hum.check(box.snapshot())) == "paused_port_not_humidifier"
    assert box.sent == []


def test_a_changed_port_assignment_pauses_all_automation_until_confirmed(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    write_ports(tmp_path, monkeypatch, {"label": "Luftbefeuchter", "label_en": "Humidifier", "device": "humidifier"})
    box, hum = humidifier_rig(monkeypatch)
    box.climate["probe_humidity"] = 20.0
    assert arun(hum.check(box.snapshot())) == "humidifier_on"  # the owner named the humidifier: confirmed
    box.ports["usb2"]["on"] = False
    hum.state["last_switch"] = None
    rig = Rig(box=box)
    rig.box.set_climate(23.0, 66.0)
    box.ports["usb2"]["accessory"] = 7  # something else reports on USB 2
    box.ports["usb2"]["on"] = False
    box.climate["probe_humidity"] = 20.0
    assert arun(hum.check(box.snapshot())) == "paused_port_changed"
    rig.box.set_climate(23.0, 66.0)
    rig.run(60)
    assert [s for s in box.sent if s[0] == "dfan"] == []
    assert rig.pilot.view()["needs"]["ports"] is not None
    assert PortGuard().confirm(box.snapshot()) == ["usb2"]  # confirmed: the owner's word for USB 2 is gone (S8)
    rig.run(30)
    assert ("dfan", 6) in box.sent
    box.climate["probe_humidity"] = 20.0
    hum.state["last_switch"] = None  # its own 15 minutes between switches are not what this test is about
    assert arun(hum.check(box.snapshot())) == "paused_port_unassigned"  # whatever hangs there now: not by humidity
    assert PortGuard().assign("usb2", "humidifier", box.snapshot())  # the owner names it again
    assert arun(hum.check(box.snapshot())) == "humidifier_on"


# ---------- restart, lock ----------

def test_restart_keeps_mode_pauses_and_lamp_and_sees_changes_made_while_down() -> None:
    rig = Rig(lamp=True)
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    rig.pilot.release()
    rig.box.dfan["step"] = 4  # changed while the server was down
    rig.pilot = rig.make()
    assert rig.pilot.state["mode"] == "auto" and rig.pilot.state["lamp"]
    sent = len(rig.box.sent)
    rig.poll()
    rig.poll()
    assert len(rig.box.sent) == sent  # warmup
    assert rig.kinds("manual")[-1]["to"] == 4 and rig.pilot.state["paused"]["dfan"]


def test_an_unreadable_state_file_means_off() -> None:
    rig = Rig()
    rig.pilot.state_path.write_text("{ not json", encoding="utf-8")
    rig.pilot.release()
    assert rig.make().state["mode"] == "off"


def test_two_servers_never_regulate_one_box() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.poll()
    second = rig.make()
    second.set_mode("auto")
    rig.clock[0] += POLL
    assert arun(second.check(rig.box.snapshot())) == [] and second.locked_out
    rig.clock[0] += ap.LOCK_STALE + 1  # the first one is gone: its heartbeat stopped
    assert arun(second.check(rig.box.snapshot())) == [] and not second.locked_out


# ---------- the day ----------

def test_daily_summary_at_midnight() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(26 * 60)
    summaries = rig.kinds("summary")
    assert summaries
    text = rig.pilot.view(lang="de")["summary"]["text"]
    assert re.search(r"Schritte \d+, hätte getan \d+, rückgängig 0, Handgriffe \d+, Fehlschläge 0\. Im Ziel: "
                     r"Temperatur \d+ %, Feuchte \d+ %, VPD \d+ % der Zeit\.", text), text


# ---------- the page: state, routes, undo ----------

def test_state_route_and_undo_button() -> None:
    from vgrow.demo import DemoBox

    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    hub = AutopilotHub([rig.pilot])

    async def go() -> None:
        client = TestClient(TestServer(make_app(DemoBox(), Alerts(), "k", autopilot=hub)))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})
        try:
            state = await (await client.get("/api/state", headers={"X-Lang": "en"})).json()
            box = state["autopilot"]["boxes"][0]
            assert box["id"] == "box-1" and box["mode"] == "auto"
            step = next(e for e in box["log"] if e["kind"] == "step")
            assert step["undo"] is True and step["text"].startswith("Autopilot: Exhaust fan 5 to 6, because")
            same = await (await client.get("/api/autopilot")).json()
            assert same["boxes"][0]["id"] == "box-1"
            r = await client.post("/api/cmd", json={"action": "autopilot_undo", "id": step["id"]})
            assert r.status == 200 and (await r.json())["undone"] == {"device": "dfan", "to": 5}
            assert rig.box.sent[-1] == ("dfan", 5) and rig.pilot.state["paused"]["dfan"]
            r = await client.post("/api/cmd", json={"action": "autopilot_undo", "id": step["id"]})
            assert r.status == 409  # once
            r = await client.post("/api/cmd", json={"action": "autopilot_mode", "mode": "off"})
            assert r.status == 200 and rig.pilot.state["mode"] == "off"
            r = await client.post("/api/cmd", json={"action": "autopilot_mode", "mode": "observe"})
            assert r.status == 200 and rig.pilot.state["mode"] == "observe"
            r = await client.post("/api/cmd", json={"action": "autopilot_device", "device": "light", "level": "manual"})
            assert r.status == 200 and rig.pilot.state["devices"]["light"]["level"] == "manual"
            r = await client.post("/api/cmd", json={"action": "autopilot_mode", "mode": "fly"})
            assert r.status == 400
            r = await client.post("/api/cmd", json={"action": "dfan", "step": 3})  # the page's own slider
            assert r.status == 200 and rig.pilot.state["pause_why"]["dfan"] == "cockpit"
        finally:
            await client.close()

    asyncio.run(go())


def test_undo_is_gone_after_30_minutes() -> None:
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    step = rig.kinds("step")[0]
    rig.clock[0] += ap.UNDO_WINDOW + 1
    with pytest.raises(CommandRefused):
        arun(rig.pilot.undo(step["id"]))


def test_the_card_has_the_switch_the_exceptions_and_the_undo_button() -> None:
    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    js = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    for needle in ('id="ap-boxes"',):
        assert needle in html
    for needle in ('"autopilot_mode"', '"autopilot_undo"', '"autopilot_device"', '"autopilot_lamp"',
                   '"ports_confirm"', "feed-more"):
        assert needle in js, needle
    # Review 1, S9: allowing a device against a VIVOSUN plan takes two taps and says the lamp then burns day and
    # night; review 2, N3: only Automatik takes two taps; review 2, S2 and S4: a pause shows folded, the humidifier is
    # a row of the exceptions and the old row and log hide; review 2, N2: an unchanged answer is not drawn again.
    for needle in ('apTwoTap(key, () => apSend(box, { action: "autopilot_device", device: d.id, app: true }))',
                   'if (mode !== "auto") { apSend(box, { action: "autopilot_mode", mode }); return; }',
                   '"pilot.devPaused"', '"pilot.pausedUntil"', "apHumRow(box, hum)", "hum.hidden = pilotShown",
                   "if (!force && drawn === apDrawn) return;", "AP_LOG_KINDS"):
        assert needle in js, needle
    i18n_js = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
    assert "die Lampe brennt danach Tag und Nacht durch" in i18n_js and "the lamp then burns day and night" in i18n_js


# ---------- the boundary: the language model never switches ----------

def test_the_regulator_lives_next_to_automation_and_hank_never_imports_it() -> None:
    hank = REPO / "vgrow" / "hank"
    assert (REPO / "vgrow" / "autopilot.py").exists() and not (hank / "autopilot.py").exists()
    imports = re.compile(r"^\s*(?:from\s+(?:\.\.|vgrow\.)(?:autopilot|budget|portguard)\b|import\s+vgrow\."
                         r"(?:autopilot|budget|portguard)\b)|vgrow\.(?:autopilot|budget|portguard)\b", re.M)
    for path in hank.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not imports.search(source), path.name
        for name in (".confirm_lamp(", ".set_device(", ".undo(", "autopilot_mode", "autopilot_undo"):
            assert name not in source, (path.name, name)
    own = (REPO / "vgrow" / "autopilot.py").read_text(encoding="utf-8")
    assert "hank" not in re.findall(r"^from \.(\w+)", own, flags=re.M) and "import hank" not in own


def test_new_files_are_utf8_lf_without_bom_and_without_em_dash() -> None:
    for rel in ("vgrow/autopilot.py", "vgrow/budget.py", "vgrow/portguard.py", "tests/test_autopilot.py",
                "ports.json", "web/app.js", "web/i18n.js", "web/index.html", "web/style.css"):
        raw = (REPO / rel).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw, rel
        assert EM_DASH not in raw.decode("utf-8"), rel


# ---------- the recorded day, observation mode ----------

HISTORY = json.loads((REPO / "tests" / "fixtures" / "autopilot" / "history-2026-10-22.json").read_text(encoding="utf-8"))


def replay(st: dict[str, Any] | None = None, gaps: list[tuple[int, int]] | None = None, lamp: bool = True) -> Rig:
    """The recorded minutes, each poll 60 s after its reading, start values of 23.10. (light 50, exhaust 5,
    circulation 10 with natural wind). Inside a gap (row indices) the polls go on but no new reading arrives."""
    rows = HISTORY["rows"]
    rig = Rig(mode="observe", start=rows[0]["time"], st=st, lamp=lamp)
    last = rows[0]
    for i, row in enumerate(rows):
        rig.clock[0] = row["time"] + 60
        if any(a <= i < b for a, b in gaps or []):
            rig.box.sensors_age = rig.clock[0] - last["time"]  # the cloud delivers nothing new: the reading ages
        else:
            last = row
            rig.box.sensors_age = 60
            rig.box.climate.update(probe_temp=row["pTemp"], probe_humidity=row["pHumi"], probe_vpd=row["pVpd"],
                                   humidity=row["bHumi"], temp=row["bTemp"])
        arun(rig.pilot.check(rig.box.snapshot()))
    return rig


def worst_hour(times: list[float]) -> int:
    return max((sum(1 for u in times if 0 <= u - s < 3600) for s in times), default=0)


def reversals(entries: list[dict[str, Any]], within: float) -> int:
    seq = [e for e in entries if e["device"] == "dfan"]
    return sum(1 for a, b in zip(seq, seq[1:])
               if (a["to"] - a["from"]) * (b["to"] - b["from"]) < 0 and b["at"] - a["at"] < within)


def test_recorded_day_in_observation_mode() -> None:
    rig = replay()
    would = rig.kinds("would")
    hours = (HISTORY["rows"][-1]["time"] - HISTORY["rows"][0]["time"]) / 3600
    assert rig.box.sent == []  # observation sends nothing
    assert len(would) * 24 / hours <= 4
    assert reversals(would, ap.DFAN_REVERSE_GAP) == 0
    assert worst_hour([e["at"] for e in would]) <= 2
    hump = [e for e in would if e["device"] == "dfan" and 1792653480 <= e["at"] <= 1792666080]  # 09:18 to 12:48
    assert hump == []  # the mist lock holds during the humidity hump of 22.10.
    assert any(e["text"]["key"] == "ap.ev.mist" for e in rig.kinds("event"))


def test_recorded_day_with_a_20_minute_gap_sends_nothing_in_it() -> None:
    rows = HISTORY["rows"]
    gaps = [(start, start + 20) for start in range(60, len(rows) - 40, 120)]  # 20 minutes every two hours
    rig = replay(gaps=gaps)
    for a, b in gaps:
        # Once the last reading is older than 300 s, nothing until 10 minutes after the next one (three readings for
        # the median, then the hold starts over). In the first 5 minutes of a gap the readings still count.
        inside = [e for e in rig.kinds("would") if rows[a - 1]["time"] + 300 < e["at"] < rows[b]["time"] + 600]
        assert inside == [], (a, inside)


# ---------- the closed loop: a toy box that reacts ----------

class ToyBox(FakeBox):
    """Each exhaust step away from 5 lowers probe temperature by k_t and humidity by k_h, with a first-order lag."""

    def __init__(self, temp: Callable[[float], float], hum: Callable[[float], float], k_t: float, k_h: float,
                 lag_min: float) -> None:
        super().__init__()
        self.temp, self.hum, self.k_t, self.k_h, self.lag = temp, hum, k_t, k_h, lag_min * 60
        self.effect = 0.0
        self.t = 0.0

    def advance(self, dt: float) -> None:
        self.t += dt
        self.effect += ((self.dfan["step"] or 5) - 5 - self.effect) * (1 - math.exp(-dt / self.lag))
        temp = self.temp(self.t) - self.k_t * self.effect
        hum = self.hum(self.t) - self.k_h * self.effect
        self.set_climate(round(temp, 2), round(hum, 2), box_humidity=round(hum - 10, 2))


def closed_loop(temp: Callable[[float], float], hum: Callable[[float], float], k_t: float, k_h: float, lag: float,
                lights: int = 0, hours: float = 24) -> list[dict[str, Any]]:
    box = ToyBox(temp, hum, k_t, k_h, lag)
    box.light["level"] = lights
    rig = Rig(box=box)
    rig.run(hours * 60, each=lambda r: box.advance(POLL))
    return [e for e in rig.kinds("step") if e["device"] == "dfan"]


def direction_changes(steps: list[dict[str, Any]]) -> int:
    dirs = [1 if e["to"] > e["from"] else -1 for e in steps]
    return sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)


def evening_turning_cold(t: float) -> float:
    """Night (lights off, band 18-22): 19.5 °C for 3 h, then down to 16 °C by hour 9, then flat."""
    h = t / 3600
    return 19.5 if h < 3 else max(16.0, 19.5 - (h - 3) * 3.5 / 6)


@pytest.mark.parametrize("k_h", [1.0, 3.0, 6.0])
@pytest.mark.parametrize("k_t", [0.2, 0.5, 1.0])
@pytest.mark.parametrize("lag", [10, 20, 40])
def test_closed_loop_cold_and_humid_night_does_not_oscillate(k_h: float, k_t: float, lag: float) -> None:
    steps = closed_loop(evening_turning_cold, lambda t: 64.0, k_t, k_h, lag)
    assert len(steps) <= 4, [(e["from"], e["to"]) for e in steps]
    assert direction_changes(steps) <= 1, [(e["from"], e["to"]) for e in steps]


@pytest.mark.parametrize("k_h", [1.0, 3.0, 6.0])
@pytest.mark.parametrize("lag", [10, 20, 40])
def test_closed_loop_steady_humid_day_gives_up_instead_of_cycling(k_h: float, lag: float) -> None:
    """Lights on, 64 % all day. An exhaust that works settles in at most two steps; one too weak to bring the humidity
    down (1 point per step) tries twice, goes back to its base, and after the second try rests a full day."""
    steps = closed_loop(lambda t: 23.0, lambda t: 64.0, 0.5, k_h, lag, lights=75, hours=48)
    pairs = [(e["from"], e["to"]) for e in steps]
    first_day = [e for e in steps if e["at"] - steps[0]["at"] < 86400] if steps else []
    assert len(first_day) <= 6, pairs
    dirs = [(e["at"], 1 if e["to"] > e["from"] else -1) for e in steps]
    assert all(b[0] - a[0] >= ap.DFAN_REVERSE_GAP for a, b in zip(dirs, dirs[1:]) if a[1] != b[1]), pairs
    if k_h >= 3:
        assert len(steps) <= 2 and direction_changes(steps) == 0, pairs  # it works: no way back, no cycle
    assert len(steps) <= 9, pairs  # two days: two tries, then a day of rest


# ---------- the two safety reviews of 23.10.2026: every finding as a test ----------

def test_no_real_command_after_switching_to_observe_or_suggest_or_manual() -> None:
    """Review 1, M1: an unconfirmed command was retried for real after the mode or the device level changed."""
    for change in ("observe", "suggest", "manual"):
        rig = Rig(box_id=f"box-{change}")
        rig.box.silent = True
        rig.box.set_climate(23.0, 66.0)
        rig.run(16)
        assert rig.box.sent == [("dfan", 6)]
        if change == "observe":
            rig.pilot.set_mode("observe")
        else:
            rig.pilot.set_device("dfan", change)
        rig.run(10)
        assert rig.box.sent == [("dfan", 6)], (change, rig.box.sent)
        assert any(e.get("status") == "cancelled" for e in rig.kinds("outcome"))


def test_no_retry_once_vivosun_automation_or_a_pause_took_the_device() -> None:
    rig = Rig()
    rig.box.silent = True
    rig.box.set_climate(23.0, 66.0)
    rig.run(16)
    rig.box.dfan["mode"] = 1  # the owner switches VIVOSUN's exhaust automation on, the step unchanged
    rig.run(10)
    assert rig.box.sent == [("dfan", 6)]


def test_a_restart_never_sends_an_old_decision_again() -> None:
    """Review 1, M2: the in-flight command was kept on disk and sent again after the restart, hours later."""
    rig = Rig()
    rig.box.silent = True
    rig.box.set_climate(23.0, 66.0)
    rig.run(15)
    assert rig.box.sent == [("dfan", 6)] and rig.pilot.state["in_flight"]
    rig.pilot.release()
    rig.clock[0] += 5 * 3600  # down for five hours
    rig.box.silent = False
    rig.box.set_climate(22.0, 50.0)  # in the band now: nothing calls for more exhaust
    rig.pilot = rig.make()
    rig.run(30)
    assert rig.box.sent == [("dfan", 6)], rig.box.sent
    assert rig.pilot.state["in_flight"] == {}


def test_a_retry_only_repeats_a_young_decision() -> None:
    rig = Rig()
    rig.box.silent = True
    rig.box.set_climate(23.0, 66.0)
    rig.run(15)
    flight = rig.pilot.state["in_flight"]["dfan"]
    flight["at"] -= ap.RETRY_MAX_AGE + 1  # e.g. the cap held the retry, or the Mac slept between two polls
    rig.poll()
    assert rig.box.sent == [("dfan", 6)] and rig.kinds("failed")  # no late repeat, but counted as a failure


def test_an_unreported_mode_is_never_taken_as_manual() -> None:
    """Review 1, M3: mode None (and the light's plan flag None) counted as manual, so a write could end a plan."""
    rig = Rig()
    rig.box.dfan["mode"] = None
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    assert rig.box.sent == []
    assert "meldet nicht" in next(d for d in rig.pilot.view()["devices"] if d["id"] == "dfan")["status"]
    rig.pilot.set_device("dfan", app=True)  # the exception for a VIVOSUN automation never allows an unknown mode
    rig.run(30)
    assert rig.box.sent == []
    light = Rig(lamp=True, box_id="box-2")
    light.box.light = {"level": 50, "mode": 0, "in_plan": None}
    light.run(3 * 60)
    assert light.box.sent == []
    snap = light.box.snapshot()
    snap["light"] = {"level": 50, "mode": 0, "in_plan": False, "in_plan_known": False}  # the real box's flag
    assert Autopilot._app_mode(snap, "light") == "unknown"


def test_switching_off_during_a_poll_stops_its_next_command() -> None:
    """Review 1, S2: off landed while the poll's first command was awaited; its second command still went out."""
    rig = Rig(lamp=True)
    rig.run(30)
    rig.box.sent.clear()
    p = rig.pilot
    p._decide_light = lambda cur, *a: {"to": 50, "dir": -1, "metric": "probe_temp", "kind": "heat", "safety": True,
                                       "reason": ap.text("ap.reason.light_heat", value=1, limit=2)}
    p._decide_dfan = lambda cur, *a: {"to": cur + 1, "dir": 1, "metric": "probe_temp", "metric_value": 30,
                                      "kind": "rule", "reason": ap.text("ap.reason.temp_high", value=1, limit=2)}
    p._decide_cfan = lambda *a: None
    orig = rig.box.set_light

    async def light_then_off(level: int) -> None:
        await orig(level)
        p.set_mode("off")  # the owner's one tap lands while the light command is awaited

    rig.box.set_light = light_then_off  # type: ignore[method-assign]
    rig.poll()
    assert p.state["mode"] == "off" and rig.box.sent == [("light", 50)]


def test_the_owners_command_pauses_the_device_before_it_is_sent() -> None:
    """Review 1, S3: the page sent first and paused afterwards; a poll in between sent the autopilot's own value."""
    from vgrow.demo import DemoBox

    rig = Rig()
    hub = AutopilotHub([rig.pilot])
    seen: list[Any] = []

    class Box(DemoBox):
        async def set_dfan(self, step: int) -> None:
            seen.append(rig.pilot.state["paused"].get("dfan"))  # what a poll running now would see
            await super().set_dfan(step)

    async def go() -> None:
        client = TestClient(TestServer(make_app(Box(), Alerts(), "k", autopilot=hub)))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})
        try:
            assert (await client.post("/api/cmd", json={"action": "dfan", "step": 3})).status == 200
        finally:
            await client.close()

    asyncio.run(go())
    assert seen and seen[0] is not None


def test_a_mode_change_keeps_the_real_rhythm_and_the_heat_step() -> None:
    """Review 1, S5 and review 2, S5: a mode toggle wiped the safety step that takes back the autopilot's own light
    increase, the exhaust's rhythm, and made the current step the new base."""
    rig = Rig(lamp=True)
    rig.run(90)
    assert rig.box.sent == [("light", 75)]
    rig.pilot.set_mode("observe")
    rig.pilot.set_mode("auto")
    rig.box.set_climate(28.5, 45.0)  # above the day maximum 26 + 1.5
    rig.run(40)
    assert ("light", 50) in rig.box.sent
    fan = Rig(box_id="box-2")
    fan.box.set_climate(23.0, 66.0)
    fan.run(15)
    assert fan.box.sent == [("dfan", 6)]
    fan.pilot.set_mode("off")
    fan.pilot.set_mode("auto")
    assert fan.pilot.state["base"] == 5  # the base stays (not off for a day)
    fan.run(14)
    assert fan.box.sent == [("dfan", 6)]  # the 30 minutes between two steps still hold


def test_a_change_while_off_is_the_owners_and_becomes_the_base() -> None:
    rig = Rig()
    rig.pilot.set_mode("off")
    rig.run(3)
    rig.box.dfan["step"] = 3  # the owner turns it down in the app while the autopilot is off
    rig.run(3)
    rig.pilot.set_mode("auto")
    assert rig.pilot.state["base"] == 3


def test_the_mist_lock_holds_without_a_box_humidity_value() -> None:
    """Review 1, S6: with no box sensor value the mist lock could not tell, and humidity drove the exhaust."""
    rig = Rig()
    rig.box.set_climate(23.0, 80.0, box_humidity=48.0)
    rig.box.climate["humidity"] = None
    rig.run(60)
    assert rig.box.sent == []
    assert "Boxfeuchte fehlt" in next(d for d in rig.pilot.view()["devices"] if d["id"] == "dfan")["status"]


def test_mist_that_comes_and_goes_is_added_up_and_pushed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2, S3: twelve short mist episodes a day gave 24 log lines and never a push."""
    pushes: list[tuple[str, str]] = []

    class Push:
        async def push(self, title: str, body: str, priority: int = 3) -> bool:
            pushes.append((title, body))
            return True

    rig = Rig()
    rig.pilot.alerts = Push()

    def flap(r: Rig) -> None:  # 15 minutes of mist in every hour
        minute = ((r.clock[0] - 1_792_652_580.0) // 60) % 60
        r.box.set_climate(23.0, 80.0 if minute < 15 else 52.0, box_humidity=48.0)

    rig.run(12 * 60, each=flap)
    mist = [e for e in rig.kinds("event") if e["text"]["key"] in ("ap.ev.mist", "ap.ev.mist_end")]
    assert len(mist) <= 4, len(mist)  # at most once per 6 hours, start and end
    assert len([p for p in pushes if p[0] == "Fühler im Nebel"]) == 1
    assert rig.box.sent == []


def test_resting_humidity_never_holds_back_an_urgent_temperature() -> None:
    """Review 2, M2: "the exhaust does not work here" fired on the temperature and rested even rank-1 votes."""
    rig = Rig()
    rig.pilot.state["rest_until"]["probe_temp"] = rig.clock[0] + 6 * 3600
    rig.pilot.state["rest_until"]["probe_humidity"] = rig.clock[0] + 6 * 3600
    rig.box.set_climate(28.5, 66.0)  # more than 2 °C over the day maximum: rank 1
    rig.run(20)
    assert rig.box.sent == [("dfan", 6)]
    calm = Rig(box_id="box-2")
    calm.pilot.state["rest_until"]["probe_humidity"] = calm.clock[0] + 6 * 3600
    calm.box.set_climate(23.0, 66.0)
    calm.run(30)
    assert calm.box.sent == []
    assert "ruht bis" in next(d for d in calm.pilot.view()["devices"] if d["id"] == "dfan")["status"]


def hot_day(t: float) -> float:
    """Lights on, the day heat rising from 23 to 29 °C over 6 hours and staying."""
    return min(29.0, 23.0 + t / 3600)


@pytest.mark.parametrize("k_t", [0.2, 0.5, 1.0])
@pytest.mark.parametrize("lag", [10, 40])
def test_closed_loop_rising_day_heat_never_steps_the_exhaust_down_while_too_warm(k_t: float, lag: float) -> None:
    """Review 2, M2 (the hot-day toy run): the rising heat beat two exhaust steps, the windup rule took the exhaust
    back to the base at 27.3 °C, and the box climbed to 29.1 °C."""
    box = ToyBox(hot_day, lambda t: 50.0, k_t, 3.0, lag)
    box.light["level"] = 75
    rig = Rig(box=box)
    downs = []

    def each(r: Rig) -> None:
        box.advance(POLL)

    for _ in range(int(10 * 3600 // POLL)):
        each(rig)
        before = len(box.sent)
        rig.poll()
        for name, value in box.sent[before:]:
            if name == "dfan" and box.climate["probe_temp"] > 26.0 and value < max(
                    [5] + [v for n, v in box.sent[:before] if n == "dfan"][-1:]):
                downs.append((value, box.climate["probe_temp"]))
    assert downs == [], downs


def test_corrupt_port_record_never_confirms_what_is_plugged_in(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1, S7: a truncated port-assignment.json was read as "nothing confirmed yet" and confirmed anew."""
    write_ports(tmp_path, monkeypatch, {"label": "Luftbefeuchter", "device": "humidifier"})
    box, hum = humidifier_rig(monkeypatch)
    box.climate["probe_humidity"] = 20.0
    hum.guard.path().write_text("{", encoding="utf-8")
    assert arun(hum.check(box.snapshot())) == "paused_port_changed"
    assert hum.guard.status(box.snapshot()) == "changed" and box.sent == []
    assert hum.guard.changed()["unreadable"] is True
    rig = Rig(box=box)
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    assert box.sent == []  # the autopilot pauses too


def test_first_seen_is_never_the_owners_word(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2, M3: the first assignment seen was taken as confirmed, so a pump on USB 2 got switched by humidity
    unless someone edited ports.json in the repository."""
    write_ports(tmp_path, monkeypatch, {"label": "Luftbefeuchter", "device": "humidifier"})
    box, hum = humidifier_rig(monkeypatch, named=None)
    box.climate["probe_humidity"] = 20.0
    assert arun(hum.check(box.snapshot())) == "paused_port_unassigned" and box.sent == []
    assert hum.guard.assign("usb2", "pump", box.snapshot())  # the owner says: a pump
    assert arun(hum.check(box.snapshot())) == "paused_port_not_humidifier"
    box.ports["usb2"]["on"] = True
    box.sensors_age = 5000  # not even the fail-safe touches a pump
    assert arun(hum.check(box.snapshot())) == "paused_port_not_humidifier" and box.sent == []
    assert hum.view(box.snapshot())["port_device"] == "pump"


def test_a_label_alone_never_makes_a_humidifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1, N5: any label with "befeuchter" counted as the humidifier ("Pumpe Befeuchter-Tank")."""
    from vgrow.portguard import configured_device

    write_ports(tmp_path, monkeypatch, {"label": "Pumpe Befeuchter-Tank"})
    assert configured_device("usb2") is None


def test_the_port_guard_never_blocks_switching_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1, S1: a changed port assignment held the fail-safe, so a running humidifier stayed on."""
    write_ports(tmp_path, monkeypatch, {"label": "Luftbefeuchter", "device": "humidifier"})
    box, hum = humidifier_rig(monkeypatch)
    box.climate["probe_humidity"] = 20.0
    assert arun(hum.check(box.snapshot())) == "humidifier_on"
    box.ports["usb2"]["accessory"] = 7
    box.sensors_age = 5000
    assert arun(hum.check(box.snapshot())) == "failsafe_off" and box.ports["usb2"]["on"] is False
    box.sensors_age = 60  # and humidity over the band switches off too, while "on" stays held
    box.ports["usb2"]["on"] = True
    box.climate["probe_humidity"] = 70.0
    hum.state["last_switch"] = None
    assert arun(hum.check(box.snapshot())) == "humidifier_off"


def test_the_plausibility_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2, M3, part 3: after "on" the probe must rise 3 points within 20 minutes, else off and ask."""
    import time

    clock = [time.time()]
    box, hum = humidifier_rig(monkeypatch, clock=clock)
    box.climate["probe_humidity"] = 30.0
    assert arun(hum.check(box.snapshot())) == "humidifier_on"
    for _ in range(14):  # 21 minutes, the humidity does not move: a pump, or no water
        clock[0] += POLL
        result = arun(hum.check(box.snapshot()))
    assert result == "implausible_off" and box.ports["usb2"]["on"] is False
    clock[0] += 3600
    assert arun(hum.check(box.snapshot())) == "paused_implausible"  # no more "on" ...
    assert hum.view(box.snapshot())["implausible"] is True
    clock[0] += 1
    assert hum.guard.assign("usb2", "humidifier", box.snapshot())  # ... until the owner names it again
    assert arun(hum.check(box.snapshot())) == "humidifier_on"
    for _ in range(5):
        clock[0] += POLL
        box.climate["probe_humidity"] += 1.0  # now it works
        assert arun(hum.check(box.snapshot())) in (None, "humidifier_off")
    assert hum.state["on_probe"] is None and box.ports["usb2"]["on"] is True


def test_confirming_a_change_forgets_the_owners_word_for_changed_ports(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1, S8: confirming "the code on USB 2 changed" put whatever hangs there now back under the humidity
    rule. The route answers which ports are unnamed now, and the page names old and new."""
    write_ports(tmp_path, monkeypatch, {"label": "Luftbefeuchter", "device": "humidifier"})
    box, hum = humidifier_rig(monkeypatch)
    box.ports["usb2"]["accessory"] = 7
    assert hum.guard.status(box.snapshot()) == "changed"
    rig = Rig(box=box)
    hub = AutopilotHub([rig.pilot])

    async def go() -> None:
        from vgrow.demo import DemoBox

        class Same(DemoBox):
            def snapshot(self) -> dict[str, Any]:
                return box.snapshot()

        client = TestClient(TestServer(make_app(Same(), Alerts(), "k", autopilot=hub)))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})
        try:
            r = await client.post("/api/cmd", json={"action": "ports_confirm"})
            assert r.status == 200 and (await r.json())["unnamed"] == ["usb2"]
            r = await client.post("/api/cmd", json={"action": "port_assign", "port": "usb1", "device": "humidifier"})
            assert r.status == 409  # this helper's USB 1 is locked always on (a mechanism test, not the real ports.json)
            r = await client.post("/api/cmd", json={"action": "port_assign", "port": "usb2", "device": "humidifier"})
            assert r.status == 200
        finally:
            await client.close()

    asyncio.run(go())
    assert hum.guard.owner_device("usb2") == "humidifier"
    js = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    assert '"pilot.portChange"' in js and '"pilot.portsHum"' in js and '"port_assign"' in js


def test_offline_box_means_no_change() -> None:
    """Review 1, N2."""
    rig = Rig()
    rig.box.set_climate(23.0, 66.0)
    real = rig.box.snapshot
    rig.box.snapshot = lambda: {**real(), "online": False}  # type: ignore[method-assign]
    rig.run(30)
    assert rig.box.sent == []


def test_observe_status_says_the_humidifier_rule_still_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1, N4."""
    box, hum = humidifier_rig(monkeypatch)
    rig = Rig(box=box, humidifier=hum, mode="observe")
    rig.run(9)
    assert rig.pilot.view()["status"].endswith("die Befeuchter-Automatik schaltet weiter")


def test_a_safety_flood_switches_off_and_pushes() -> None:
    """Review 2, N1."""
    pushes = []

    class Push:
        async def push(self, title: str, body: str, priority: int = 3) -> bool:
            pushes.append(title)
            return True

    rig = Rig()
    rig.pilot.alerts = Push()
    for _ in range(25):
        rig.budget.record("humidifier", "port.usb2", safety=True)
    rig.run(6)
    assert rig.pilot.state["mode"] == "off" and pushes == ["Autopilot hat sich ausgeschaltet"]


def test_history_clock_jumps_and_corruption_never_create_a_command_quota() -> None:
    clock = [10_000.0]
    budget = CommandBudget(clock=lambda: clock[0])
    for _ in range(30):
        budget.record("autopilot", "dfan")
    clock[0] -= 600  # the clock goes back ten minutes
    budget.check("autopilot")
    assert budget.counts()["auto_day"] == 30
    budget.path().write_text("{ torn", encoding="utf-8")
    clock[0] += 7200
    budget.check("autopilot")
    assert budget.view()["recording_error"] is True
    budget.check("humidifier", safety=True)
    budget.record("humidifier", "port.usb2", safety=True)
    budget.check("autopilot")
    assert budget.view()["recording_error"] is False
    data = json.loads(budget.path().read_text())
    data["held_until"] = clock[0] + 86400  # an old installation's persisted quota hold
    budget.path().write_text(json.dumps(data))
    recovered = CommandBudget(clock=lambda: clock[0])
    for origin in ("autopilot", "humidifier", "watering", "manual"):
        recovered.check(origin)
    assert recovered.view()["limited"] is False and recovered.free_at() is None
    assert recovered.view()["per_hour"] is None and recovered.view()["per_day"] is None


def test_hank_reads_the_autopilot_and_drops_cards_it_regulates() -> None:
    """Review 2, M4: with the autopilot on Automatik, "Kannst du das beheben?" still offered "Licht auf 100 %" and
    asked for the owner's tap; "Was hat der Autopilot gemacht?" got "Da bin ich der Falsche"."""
    from vgrow.hank import fix, pilot_report
    from vgrow.server import HankBox

    rig = Rig(lamp=True)
    rig.box.set_climate(23.0, 66.0)
    rig.run(30)
    snap = HankBox(rig.box, AutopilotHub([rig.pilot])).snapshot()
    brief = snap["autopilot"]["boxes"][0]
    assert brief["mode"] == "auto" and brief["recent"] and brief["recent"][0]["text"]["de"].startswith("Autopilot:")
    assert set(pilot_report.regulated({"snapshot": snap})) == {"light", "dfan"}  # natural wind: not the cfan
    pilot_report.register()
    lines = pilot_report.lines({"snapshot": snap}, "de")
    assert lines[0].startswith("Der Autopilot steht auf Automatik") and "Zuletzt:" in lines
    assert pilot_report.answer({"snapshot": snap}, "en", answer_id="a-1").link == {
        "href": "#autopilot", "label": "To the Autopilot card"}
    assert fix.EM_DASH not in "".join(pilot_report.DE.values()) + "".join(pilot_report.EN.values())
    assert pilot_report.light_step_at({"snapshot": snap}) is None  # no light step up yet
    rig.clock[0] += 25 * 3600
    rig.box.set_climate(20.5, 52.0)
    rig.run(3 * 60)
    snap = HankBox(rig.box, AutopilotHub([rig.pilot])).snapshot()
    assert pilot_report.light_step_at({"snapshot": snap}) == rig.pilot.state["light"]["auto_up_at"]
