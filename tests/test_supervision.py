"""The supervision's readers (vgrow/supervision.py): one scenario per signal (fresh, just before and just past its
limit, quiet on purpose, not configured), the grace after a start and after a gap, the parents (one cause, one push),
the expect latches, the incident of November 2026 on a real Durst-Index, the watering slots, the loop beats, the tasks,
the declared task names, the device coverage, the mutes, the "was away" notice and the state file.

The incident (15.11.2026): the pot set up on 06.11., the soil sensor's setting empty since then, three waterings by
hand the index never saw, and nine days without a measurement that nothing reported.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from vgrow import config, device_check, light_schedule, soil, supervision, thirst
from vgrow.alerts import PUSH_RUN_GAP_S, Alerts
from vgrow.fan_stick import FanStick
from vgrow.hank import core as hank_core
from vgrow.i18n import UserError
from vgrow.supervision import OFF, OK, SILENT, STARTING, UNCONFIGURED, UNKNOWN, WAITING, Supervisor

REPO = Path(__file__).resolve().parent.parent
RULES = config.alert_rules()["rules"]
HOUR, DAY = 3600.0, 86400.0
T0 = 2_000_000_000.0  # May 2033: every file this test writes is older, so no real mtime lands in its future
BERLIN = ZoneInfo("Europe/Berlin")
STAGE = {"key": "early_flower", "set_by": "manual", "label": "Frühe Blüte"}


# ---------- the fakes ----------

class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Task:
    """What the supervision asks of an asyncio task."""

    def __init__(self, name: str, *, done: bool = False, cancelled: bool = False) -> None:
        self.name, self._done, self._cancelled = name, done or cancelled, cancelled

    def get_name(self) -> str:
        return self.name

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self._cancelled


class FakeBox:
    def __init__(self) -> None:
        self.polled_at: float | None = None
        self.water_seen_at: float | None = None
        self.tasks = {name: Task(name) for name in ("vgrow_supervise", "vgrow_poll", "vgrow_shadow_refresh")}

    def loops(self) -> dict[str, Task]:
        return dict(self.tasks)


class Pushes(Alerts):
    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[tuple[str, str, int]] = []
        self.answer = True

    async def push(self, title: str, message: str, *, priority: int = 3, click: str | None = None) -> bool:
        self.pushed.append((title, message, priority))
        return self.answer


class Ctl:
    """The watering controller as the supervision reads it."""

    def __init__(self, clock: Clock, *, enabled: bool = True) -> None:
        self.task = Task("watering_watchdog")
        self.ticked_at: float | None = clock.now
        self.cfg = {"enabled": enabled}
        self.events: list[dict[str, Any]] = []

    def slots(self, after: float, until: float) -> list[tuple[float, str]]:
        return [(after + 1, "slot")] if self.cfg["enabled"] else []


class FakeThirst:
    def __init__(self, clock: Clock, **data: Any) -> None:
        self.checked_at: float | None = clock.now
        self.data = {"pot_set_at": clock.now - 30 * DAY, "measured_at": clock.now - 60, "look_at": clock.now - 10,
                     "valid_s": DAY, "schedule_on": True, **data}

    def supervision(self) -> dict[str, Any]:
        return {**self.data, "checked_at": self.checked_at}


class Health:
    def __init__(self, cached: dict[str, Any] | None) -> None:
        self.cached = cached


class FakeHank:
    def __init__(self, clock: Clock, health: Health | None = None) -> None:
        self.task = Task("hank")
        self.ticked_at: float | None = clock.now
        self.health = health


class Plan:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


class Pest:
    def __init__(self, checks: list[dict[str, Any]], camera: Any = "cam") -> None:
        self._checks, self._camera = checks, camera

    def camera(self) -> Any:
        return self._camera

    def checks(self) -> list[dict[str, Any]]:
        return self._checks


class Collector:
    def __init__(self, last_ok: float | None, saved: float | None = None) -> None:
        self.last_ok, self.saved = last_ok, saved
        self.task = Task("measurements_collect")

    def load_state(self) -> dict[str, Any]:
        return {"last_ok": self.saved}


class Rig:
    """The supervision over fakes, past its start grace unless a test asks for it."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or Clock()
        self.box = FakeBox()
        self.alerts = Pushes()
        self.parts: dict[str, Any] = {}
        self.running: list[Task] = [Task("hank_protocol")]
        self.sup = Supervisor(self.box, self.alerts, self.parts, clock=self.clock, tasks=lambda: list(self.running))
        self.sup.grace_until = 0.0

    def read(self, snap: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.sup.read(snap if snap is not None else {})["signals"]

    def state(self, sig: str, snap: dict[str, Any] | None = None) -> str:
        return self.read(snap)[sig]["state"]

    def soil_sensor(self, *, percent: float | None = 41.0, heard: float | None = 30.0) -> soil.SoilSensor:
        sensor = soil.SoilSensor(None, "10.0.0.9", clock=self.clock)  # type: ignore[arg-type]
        sensor.readings = [soil.Reading(1, percent)] if percent is not None else []
        sensor.heard_at = None if heard is None else self.clock.now - heard
        self.parts["soil"] = sensor
        return sensor


def box_snap(**changes: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {"online": True, "mqtt": True, "shadow_age": 30, "sensors_age": 40, "aux_device": 9,
                            "climate": {"temp": 22.0, "humidity": 50.0, "probe_temp": 23.0, "probe_humidity": 55.0,
                                        "water_kind": "wet_dry", "water_state": "wet", "water_level": None},
                            "camera": {"configured": True, "reachable": True},
                            "light_day": {"lit_hours": 12.0, "dark_hours": 12.0, "measured_hours": 24.0}}
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(snap.get(key), dict):
            snap[key] = {**snap[key], **value}
        else:
            snap[key] = value
    return snap


def calibrate(sensor: soil.SoilSensor, wet: float | None) -> None:
    data = {} if wet is None else {"wet": {"percent": wet, "at": T0 - DAY}}
    config.atomic_write(sensor.calibration_path(), json.dumps(data) + "\n")


def news_state(clock: Clock, last_success: float | None) -> None:
    path = config.STATE_DIR / "news" / "state.json"
    data = {} if last_success is None else {"last_success": datetime.fromtimestamp(last_success).astimezone().isoformat()}
    config.atomic_write(path, json.dumps(data) + "\n")


# ---------- one scenario per signal ----------

@dataclass
class Scenario:
    """fresh: ok. before and past: proof one second within and beyond the limit (or one fault fewer and enough for a
    count). quiet: a case quiet on purpose and its state. unconfigured: the part not set up. None: the case does not
    exist for this signal (it is always configured, or never quiet)."""
    fresh: Callable[[Rig], dict[str, Any]]
    before: Callable[[Rig], dict[str, Any]]
    past: Callable[[Rig], dict[str, Any]]
    quiet: Callable[[Rig], tuple[dict[str, Any], str]] | None
    unconfigured: Callable[[Rig], dict[str, Any]] | None


def _seen_then(rig: Rig, seconds: float, present: dict[str, Any], absent: dict[str, Any]) -> dict[str, Any]:
    rig.read(present)  # the proof, stamped in memory
    rig.clock.advance(seconds)
    return absent


def _poll(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.box.polled_at = rig.clock.now - age
        return {}
    return make


def _no_loops(rig: Rig) -> dict[str, Any]:
    rig.sup.box = object()
    return {}


def _failing(n: int) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        for _ in range(n):
            rig.sup.step_finished("Thirst.check", KeyError("x"))
        return {}
    return make


def _watcher(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.sup.task = Task("vgrow_supervision")  # type: ignore[assignment]
        rig.sup.watch_beat = rig.clock.now - age
        return {}
    return make


def _tasks(**task: Any) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        sensor = rig.soil_sensor()
        sensor.task = Task("soil_sensor", **task)  # type: ignore[assignment]
        return {}
    return make


def _ctl(rig: Rig) -> Ctl:
    return Ctl(rig.clock)


def _fake_hank(rig: Rig) -> FakeHank:
    return FakeHank(rig.clock)


def _beat(part: str, make_part: Callable[[Rig], Any], age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        obj = make_part(rig)
        obj.ticked_at = rig.clock.now - age
        rig.parts[part] = obj
        return {}
    return make


def _timer(rig: Rig, *, enabled: bool = True) -> light_schedule.LightSchedule:
    timer = light_schedule.LightSchedule(None, clock=rig.clock)
    timer.cfg = light_schedule.settings({"enabled": enabled, "on_time": "06:00", "off_time": "00:00", "level": 80})
    timer.task = Task("light_schedule")  # type: ignore[assignment]
    timer.ticked_at = rig.clock.now
    return timer


def _quiet_timer(rig: Rig) -> tuple[dict[str, Any], str]:
    rig.parts["light_schedule"] = _timer(rig, enabled=False)
    return {}, OFF


def _shadow(age: float) -> Callable[[Rig], dict[str, Any]]:
    return lambda rig: box_snap(shadow_age=age)


def _climate(keys: tuple[str, ...], age: float) -> Callable[[Rig], dict[str, Any]]:
    """The proof is the reading's own time, sensors_age before the poll: the age counts from there."""
    gone = {key: None for key in keys}
    return lambda rig: _seen_then(rig, age - box_snap()["sensors_age"], box_snap(), box_snap(climate=gone))


def _expect_seen(sig: str, keys: tuple[str, ...]) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.alerts.config = copy.deepcopy(rig.alerts.config)
        rig.alerts.config["rules"][sig]["expect"] = "seen"  # one word: a part that never reported itself
        return box_snap(climate={key: None for key in keys})
    return make


def _tank(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.box.water_seen_at = rig.clock.now - age
        return box_snap(climate={"water_state": None})
    return make


def _soil(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.soil_sensor(heard=0.0)
        rig.read(box_snap())
        rig.clock.advance(age)  # the gateway's answer is that old now
        return box_snap()
    return make


def _uncalibrated(set_ago: float, wet: float | None = None) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        calibrate(rig.soil_sensor(), wet)
        os.utime(rig.parts["soil"].calibration_path(), (rig.clock.now - 40 * DAY, rig.clock.now - 40 * DAY))
        rig.parts["thirst"] = FakeThirst(rig.clock, pot_set_at=rig.clock.now - set_ago)
        return box_snap()
    return make


def _quiet_uncalibrated(rig: Rig) -> tuple[dict[str, Any], str]:
    _uncalibrated(2 * DAY)(rig)
    rig.parts["soil"].heard_at = rig.clock.now - 700  # no value: soil_silent owns it
    return box_snap(), WAITING


def _no_pot(rig: Rig) -> dict[str, Any]:
    rig.soil_sensor()
    rig.parts["thirst"] = FakeThirst(rig.clock, pot_set_at=None)
    return box_snap()


def _engine(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.parts["thirst"] = FakeThirst(rig.clock)
        rig.parts["thirst"].checked_at = rig.clock.now - age
        return {}
    return make


def _input(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.parts["thirst"] = FakeThirst(rig.clock, measured_at=rig.clock.now - age)
        return box_snap()
    return make


def _quiet_input(rig: Rig) -> tuple[dict[str, Any], str]:
    rig.parts["thirst"] = FakeThirst(rig.clock, measured_at=rig.clock.now - 3 * DAY)
    rig.parts["thirst"].checked_at = None  # no check ran to its end yet: thirst_engine owns that
    return box_snap(), WAITING


def _by_hand(rig: Rig) -> dict[str, Any]:
    rig.parts["thirst"] = FakeThirst(rig.clock, schedule_on=False, measured_at=rig.clock.now - 3 * DAY)
    return box_snap()  # neither plan nor sensor relies on the pot


def _look(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.soil_sensor()
        rig.parts["thirst"] = FakeThirst(rig.clock, look_at=rig.clock.now - age)
        return box_snap()
    return make


def _quiet_look(rig: Rig) -> tuple[dict[str, Any], str]:
    _look(3 * HOUR)(rig)
    rig.parts["soil"].heard_at = rig.clock.now - 700  # the gateway is silent: soil_silent speaks
    return box_snap(), WAITING


def _no_soil(rig: Rig) -> dict[str, Any]:
    rig.parts["thirst"] = FakeThirst(rig.clock, look_at=rig.clock.now - 3 * HOUR)
    rig.parts["soil"] = soil.DemoSoil()  # the demo's made-up sensor is no part
    return box_snap()


def slot(*outcome: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"at": T0, "kind": "due", "reason": None}, *outcome]


START = {"at": T0, "kind": "start", "reason": None}
NO_FLOW = {"at": T0, "kind": "skipped", "reason": "thirst", "gate": "no_flow"}
NOT_DUE = {"at": T0, "kind": "skipped", "reason": "thirst", "gate": "not_due"}


def _skips(*slots: list[dict[str, Any]]) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        ctl = Ctl(rig.clock)
        ctl.events = [event for one in slots for event in one]
        rig.parts["watering"] = ctl
        return {}
    return make


def _quiet_skips(rig: Rig) -> tuple[dict[str, Any], str]:
    _skips(slot(NO_FLOW), slot(NO_FLOW))(rig)
    rig.parts["watering"].cfg["enabled"] = False
    return {}, OFF


def _push(failing_for: float | None) -> Callable[[Rig], dict[str, Any]]:
    """A run of failed sends, its newest try 30 s ago: the engine retries an undelivered alarm on every poll."""
    def make(rig: Rig) -> dict[str, Any]:
        rig.alerts.target = ("https://ntfy.example", "t")
        rig.alerts._push_record().update(failed_since=None if failing_for is None else rig.clock.now - failing_for,
                                         failed_at=None if failing_for is None else rig.clock.now - 30)
        return {}
    return make


def _ruler(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.parts["grow_plan"] = Plan({"height": {"cm": 41.0, "at": rig.clock.now - age}})
        return box_snap()
    return make


def _quiet_ruler(rig: Rig) -> tuple[dict[str, Any], str]:
    _ruler(2 * DAY)(rig)
    return box_snap(camera={"hidden": True}), OFF


def _stick(rig: Rig, *, age: float | None, auto: bool = True) -> FanStick:
    stick = FanStick(None, clock=rig.clock)
    stick.device = object()  # found in the account
    stick.cfg = {**stick.cfg, "auto": auto}
    stick.shadow_at = None if age is None else rig.clock.now - age
    rig.parts["fan_stick"] = stick
    return stick


def _fan(age: float) -> Callable[[Rig], dict[str, Any]]:
    return lambda rig: (_stick(rig, age=age), box_snap())[1]  # read beside a fresh box, as every poll does


def _quiet_fan(rig: Rig) -> tuple[dict[str, Any], str]:
    _stick(rig, age=2 * HOUR, auto=False)
    return {}, OFF


def _no_stick(rig: Rig) -> dict[str, Any]:
    stick = _stick(rig, age=2 * HOUR)
    stick.device = None  # no stick in the account
    return {}


def _pest(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.parts["pest"] = Pest([{"at": rig.clock.now - 3 * DAY, "result": {"ok": 1}},
                                  {"at": rig.clock.now - age, "result": {"ok": 1}},
                                  {"at": rig.clock.now - 60, "result": None, "error": "timeout"}])
        return box_snap()
    return make


def _quiet_pest(rig: Rig) -> tuple[dict[str, Any], str]:
    _pest(2 * DAY)(rig)
    return box_snap(light_day={"lit_hours": 2.0}), WAITING  # a dark day has no check


def _store(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.parts.update(store=object(), collector=Collector(rig.clock.now - age), light_day=object())
        return box_snap()
    return make


def _quiet_store(rig: Rig) -> tuple[dict[str, Any], str]:
    _store(3 * HOUR)(rig)
    return box_snap(online=False), WAITING  # an offline box gives the collector nothing


def _news(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.parts["news"] = object()
        news_state(rig.clock, rig.clock.now - age)
        return {}
    return make


def _ollama(age: float) -> Callable[[Rig], dict[str, Any]]:
    def make(rig: Rig) -> dict[str, Any]:
        rig.parts["hank"] = FakeHank(rig.clock, Health({"ollama": "ok", "checked": rig.clock.now - age}))
        return {}
    return make


def _quiet_ollama(rig: Rig) -> tuple[dict[str, Any], str]:
    _ollama(2 * HOUR)(rig)
    hank_core.write_mode("off")  # his tick returns before the probe: a stale cache on purpose
    return {}, OFF


def _no_ollama(rig: Rig) -> dict[str, Any]:
    rig.parts["hank"] = FakeHank(rig.clock, None)  # no Ollama client
    return {}


def _starts(n: int) -> Callable[[Rig], dict[str, Any]]:
    """n starts in the last 24 h, this process's own among them, and an older one that no longer counts."""
    def make(rig: Rig) -> dict[str, Any]:
        rig.sup.state["starts"] = [rig.clock.now - DAY - 60] + [rig.clock.now - 600 * i for i in range(n)]
        return {}
    return make


SCENARIOS: dict[str, Scenario] = {
    "poll_stalled": Scenario(_poll(10), _poll(299), _poll(301), None, _no_loops),
    "supervision_failing": Scenario(_failing(0), _failing(2), _failing(3), None, None),
    "supervision_silent": Scenario(_watcher(10), _watcher(179), _watcher(181), None, None),
    "tasks_dead": Scenario(_tasks(), _tasks(cancelled=True), _tasks(done=True), None, None),
    "watering_watchdog": Scenario(_beat("watering", _ctl, 1), _beat("watering", _ctl, 29),
                                  _beat("watering", _ctl, 31), None, lambda rig: {}),
    "light_timer": Scenario(_beat("light_schedule", _timer, 1), _beat("light_schedule", _timer, 29),
                            _beat("light_schedule", _timer, 31), _quiet_timer, lambda rig: {}),
    "hank_silent": Scenario(_beat("hank", _fake_hank, 10), _beat("hank", _fake_hank, 299),
                            _beat("hank", _fake_hank, 301), None, lambda rig: {}),
    "device_link": Scenario(_shadow(30), _shadow(479), _shadow(481),
                            lambda rig: (box_snap(online=False), WAITING), None),
    "probe_silent": Scenario(lambda rig: box_snap(), _climate(("probe_temp", "probe_humidity"), 899),
                             _climate(("probe_temp", "probe_humidity"), 901),
                             lambda rig: (box_snap(sensors_age=400), WAITING),
                             _expect_seen("probe_silent", ("probe_temp", "probe_humidity"))),
    "builtin_silent": Scenario(lambda rig: box_snap(), _climate(("temp", "humidity"), 899),
                               _climate(("temp", "humidity"), 901), lambda rig: (box_snap(online=False), WAITING),
                               _expect_seen("builtin_silent", ("temp", "humidity"))),
    "tank_sensor_silent": Scenario(lambda rig: box_snap(), _tank(899), _tank(901),
                                   lambda rig: (box_snap(sensors_age=400), WAITING),
                                   lambda rig: box_snap(aux_device=None, climate={"water_state": None})),
    "soil_silent": Scenario(_soil(0), _soil(599), _soil(601), None, lambda rig: box_snap()),
    "soil_uncalibrated": Scenario(_uncalibrated(30 * DAY, wet=45.0), _uncalibrated(DAY - 1), _uncalibrated(DAY + 1),
                                  _quiet_uncalibrated, _no_pot),
    "thirst_engine": Scenario(_engine(10), _engine(299), _engine(301), None, lambda rig: {}),
    "thirst_input": Scenario(_input(60), _input(DAY - 1), _input(DAY + 1), _quiet_input, _by_hand),
    "thirst_sensor_look": Scenario(_look(10), _look(1079), _look(1081), _quiet_look, _no_soil),
    "watering_skips": Scenario(_skips(slot(START), slot(START)), _skips(slot(NOT_DUE), slot(NO_FLOW)),
                               _skips(slot(NO_FLOW), slot(NO_FLOW)), _quiet_skips, lambda rig: {}),
    "push_broken": Scenario(_push(None), _push(899), _push(901), None, None),
    "ruler_silent": Scenario(_ruler(HOUR), _ruler(97199), _ruler(97201), _quiet_ruler, lambda rig: box_snap()),
    "fan_stick_silent": Scenario(_fan(60), _fan(899), _fan(901), _quiet_fan, _no_stick),
    "ollama_silent": Scenario(_ollama(60), _ollama(1799), _ollama(1801), _quiet_ollama, _no_ollama),
    "pest_watch_silent": Scenario(_pest(600), _pest(DAY - 1), _pest(DAY + 1), _quiet_pest, lambda rig: box_snap()),
    "store_silent": Scenario(_store(60), _store(2699), _store(2701), _quiet_store, lambda rig: box_snap()),
    "news_silent": Scenario(_news(HOUR), _news(2 * DAY - 1), _news(2 * DAY + 1), None, lambda rig: {}),
    "restarts": Scenario(_starts(2), _starts(supervision.RESTARTS_24H - 1), _starts(supervision.RESTARTS_24H), None,
                         None),
}


def test_every_signal_has_a_scenario() -> None:
    assert sorted(SCENARIOS) == sorted(supervision.BY_ID)  # a new signal without its scenario fails here


@pytest.mark.parametrize("sig", sorted(SCENARIOS))
def test_the_scenario(sig: str, isolated_state: Path) -> None:
    scenario = SCENARIOS[sig]
    for case, wanted in (("fresh", OK), ("before", OK), ("past", SILENT)):
        rig = Rig()
        snap = getattr(scenario, case)(rig)
        found = rig.read(snap)[sig]
        assert found["state"] == wanted, (case, found)
        if wanted == SILENT:
            assert supervision.reading(found, "de") and "{" not in supervision.reading(found, "en")
    if scenario.quiet is not None:
        rig = Rig()
        snap, wanted = scenario.quiet(rig)
        assert rig.state(sig, snap) == wanted
    if scenario.unconfigured is not None:
        rig = Rig()
        assert rig.state(sig, scenario.unconfigured(rig)) == UNCONFIGURED


# ---------- the grace, the clock, a reader that raises ----------

def test_grace_resume_and_negative_age(isolated_state: Path) -> None:
    rig = Rig()
    rig.sup.grace_until = rig.clock.now + supervision.GRACE_S  # a fresh start
    rig.parts["thirst"] = FakeThirst(rig.clock)
    rig.parts["thirst"].checked_at = rig.clock.now - 400
    assert rig.state("thirst_engine") == STARTING  # nothing is silent in the first 300 s
    rig.clock.advance(supervision.GRACE_S + 1)
    assert rig.state("thirst_engine") == SILENT
    # a gap of the watcher over 180 s (a Mac that slept): the grace again
    rig.sup.watch_beat = rig.clock.now - 200
    asyncio.run(rig.sup.tick())
    assert rig.state("thirst_engine") == STARTING
    rig.clock.advance(supervision.GRACE_S + 1)
    assert rig.state("thirst_engine") == SILENT
    # persisted proof resumes without resetting a clock: the pot's measurement from the log, a new process
    fresh = Rig(Clock(rig.clock.now))
    fresh.parts["thirst"] = FakeThirst(fresh.clock, measured_at=fresh.clock.now - 2 * DAY)
    assert fresh.state("thirst_input", box_snap()) == SILENT
    # a proof in the future (the clock set back) is age 0, never silent
    later = Rig()
    later.parts["thirst"] = FakeThirst(later.clock, measured_at=later.clock.now + DAY)
    found = later.read(box_snap())["thirst_input"]
    assert found["state"] == OK and found["age"] == 0
    # a reader that raises is unknown, and the others are unaffected
    broken = Rig()

    class Raises:
        def supervision(self) -> dict[str, Any]:
            raise RuntimeError("ledger")

        checked_at = T0

    broken.parts["thirst"] = Raises()
    found = broken.read(box_snap())
    assert found["thirst_input"]["state"] == UNKNOWN and found["thirst_input"]["params"] == {"error": "RuntimeError"}
    assert found["thirst_engine"]["state"] == OK and found["probe_silent"]["state"] == OK
    assert "ledger" not in json.dumps(found)  # only the class, never the text


def test_never_seen_counts_from_the_start(isolated_state: Path) -> None:
    rig = Rig()
    snap = box_snap(climate={"probe_temp": None, "probe_humidity": None})
    assert rig.state("probe_silent", snap) == STARTING  # no proof yet, so no ok either
    rig.clock.advance(901)
    assert rig.state("probe_silent", snap) == SILENT


# ---------- the parents ----------

def test_parents_make_one_cause(isolated_state: Path) -> None:
    rig = Rig()
    offline = box_snap(online=False)
    rig.parts.update(store=object(), collector=Collector(None), light_day=object())
    found = rig.read(offline)
    assert {sig: found[sig]["state"] for sig in ("device_link", "probe_silent", "builtin_silent",
                                                 "tank_sensor_silent", "store_silent")} == dict.fromkeys(
        ("device_link", "probe_silent", "builtin_silent", "tank_sensor_silent", "store_silent"), WAITING)
    # soil_silent or soil_uncalibrated makes thirst_input wait
    rig = Rig()
    rig.soil_sensor(heard=700.0)
    rig.parts["thirst"] = FakeThirst(rig.clock, measured_at=rig.clock.now - 3 * DAY)
    rig.read(box_snap())
    rig.clock.advance(601)
    found = rig.read(box_snap())
    assert found["soil_silent"]["state"] == SILENT and found["thirst_input"]["state"] == WAITING
    assert found["thirst_sensor_look"]["state"] == WAITING
    rig = Rig()
    calibrate(rig.soil_sensor(), None)
    os.utime(rig.parts["soil"].calibration_path(), (rig.clock.now - 40 * DAY, rig.clock.now - 40 * DAY))
    rig.parts["thirst"] = FakeThirst(rig.clock, measured_at=rig.clock.now - 3 * DAY)
    found = rig.read(box_snap())
    assert found["soil_uncalibrated"]["state"] == SILENT and found["thirst_input"]["state"] == WAITING
    # a parent the owner muted gates nothing: the child's own consequence is then the alarm
    rig.sup.mute("soil_uncalibrated", 3, "Sensor liegt im Schrank")
    assert rig.state("thirst_input", box_snap()) == SILENT
    # ollama_silent makes the pest watch wait
    rig = Rig()
    rig.parts["hank"] = FakeHank(rig.clock, Health({"ollama": "failed", "checked": rig.clock.now}))
    rig.parts["pest"] = Pest([{"at": rig.clock.now - 3 * DAY, "result": {"ok": 1}}])
    rig.clock.advance(1801)
    rig.parts["hank"].ticked_at = rig.clock.now
    found = rig.read(box_snap())
    assert found["ollama_silent"]["state"] == SILENT and found["pest_watch_silent"]["state"] == WAITING
    # a hidden or offline GrowCam: the ruler waits or is off, never silent
    rig = Rig()
    rig.parts["grow_plan"] = Plan({"height": {"at": rig.clock.now - 3 * DAY}})
    assert rig.state("ruler_silent", box_snap(camera={"reachable": False})) == WAITING
    assert rig.state("ruler_silent", box_snap(camera={"searching": True})) == WAITING
    assert rig.state("ruler_silent", box_snap(camera={"hidden": True})) == OFF
    assert rig.state("ruler_silent", box_snap()) == SILENT


# ---------- expect and the latch ----------

def test_expect_and_latch(isolated_state: Path) -> None:
    rig = Rig()
    dead = box_snap(climate={"probe_temp": None, "probe_humidity": None, "temp": None, "humidity": None})
    rig.read(dead)
    rig.clock.advance(901)
    found = rig.read(dead)  # never seen in this process, expected always: a dead one is silent after 900 s
    assert found["probe_silent"]["state"] == SILENT and found["builtin_silent"]["state"] == SILENT
    # the tank sensor is expected only once it was seen
    none = box_snap(aux_device=None, climate={"water_state": None})
    rig = Rig()
    assert rig.state("tank_sensor_silent", none) == UNCONFIGURED
    rig.read(box_snap())  # type 9 seen: latched
    asyncio.run(rig.sup.save())
    again = Rig(Clock(rig.clock.now + 2 * HOUR))  # a new process on the same STATE_DIR
    assert "tank_sensor_silent" in again.sup.state["seen"]
    assert again.state("tank_sensor_silent", none) == STARTING  # stays expected: no proof yet in this process
    again.clock.advance(901)
    assert again.state("tank_sensor_silent", none) == SILENT


# ---------- the incident, replayed on a real Durst-Index ----------

def test_the_incident_replay_on_a_real_thirst_object(isolated_state: Path) -> None:
    clock = Clock(datetime(2026, 11, 15, 10, 0, tzinfo=BERLIN).timestamp())
    set_at = clock.now - 9 * DAY  # the pot of 06.11.2026
    plan = Ctl(clock)
    index = thirst.Thirst(None, demo=True, clock=clock, tz=BERLIN, stage_source=lambda: STAGE, watering=plan)
    config.atomic_write(index.pot_path(), json.dumps(thirst.Pot(volume_l=11.4, set_at=set_at, id="pot").as_dict()) + "\n")
    rig = Rig(clock)
    sensor = rig.soil_sensor(percent=42.0, heard=30.0)
    calibrate(sensor, None)  # the reset: an empty calibration.json since 06.11.
    os.utime(sensor.calibration_path(), (set_at, set_at))
    rows = []
    for days_ago, low, high in ((8, 20.0, 45.0), (3, 22.0, 52.0), (1, 25.0, 50.0)):  # three waterings by hand
        at = clock.now - days_ago * DAY
        rows += [{"at": at - 600, "channel": 1, "percent": low}, {"at": at, "channel": 1, "percent": high},
                 {"at": at + 4 * HOUR, "channel": 1, "percent": 42.0}]
    sensor.log_path().write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    index.soil_history = sensor.history
    sensor.on_reading = index.sensor_check
    rig.parts.update(thirst=index, watering=plan)
    snap = box_snap()
    asyncio.run(index.check(snap))
    found = rig.read(snap)
    assert found["soil_uncalibrated"]["state"] == SILENT  # at the first evaluation, not a day after the deploy
    assert supervision.reading(found["soil_uncalibrated"], "de").startswith("Seit 9 Tagen liefert der Sensor Werte")
    assert found["thirst_input"]["state"] == WAITING  # behind it: one cause, one push
    # the sensor's look finds the three waterings: water of unknown amount, which is no measurement
    index.sensor_check(sensor.view())
    waters = [e for e in index.entries() if e.get("kind") == "water" and e.get("origin") == "sensor"]
    assert len(waters) == 3 and index.look_at == clock.now
    asyncio.run(index.check(snap))
    assert index.measured_at is None  # a water event never resets thirst_input's clock
    # the owner sets the sensor: the uncalibrated alarm ends, and the missing measurement speaks for itself
    clock.advance(60)
    sensor.heard_at = clock.now
    sensor.calibrate({"point": "wet"})
    found = rig.read(snap)
    assert found["soil_uncalibrated"]["state"] == OK and found["thirst_input"]["state"] == SILENT
    # two calibrated readings with the same answer are a sensor check: within minutes both are fine
    for _ in range(2):
        clock.advance(60)
        sensor.heard_at = clock.now
        index.sensor_check(sensor.view())
    asyncio.run(index.check(snap))
    assert index.measured_at is not None and clock.now - index.measured_at <= 60
    found = rig.read(snap)
    assert found["thirst_input"]["state"] == OK and found["soil_uncalibrated"]["state"] == OK
    # a pot with neither schedule nor sensor is nobody's to watch
    plan.cfg["enabled"] = False
    del rig.parts["soil"]
    assert rig.state("thirst_input", snap) == UNCONFIGURED


def test_soil_causes_and_uncalibrated_clock(isolated_state: Path) -> None:
    rig = Rig()
    sensor = rig.soil_sensor(percent=None, heard=0.0)  # the gateway answers without the WH51's channel
    rig.read(box_snap())
    rig.clock.advance(601)
    sensor.heard_at = rig.clock.now
    found = rig.read(box_snap())["soil_silent"]
    assert found["state"] == SILENT and found["cause"] == "sensor"
    sensor.heard_at = rig.clock.now - 700
    found = rig.read(box_snap())["soil_silent"]
    assert found["state"] == SILENT and found["cause"] == "gateway"
    # the uncalibrated clock: max(the pot's set-up, calibration.json's mtime), so a reset starts it again
    rig = Rig()
    calibrate(rig.soil_sensor(), None)
    rig.parts["thirst"] = FakeThirst(rig.clock, pot_set_at=rig.clock.now - 5 * DAY)
    os.utime(rig.parts["soil"].calibration_path(), (rig.clock.now - 2 * HOUR, rig.clock.now - 2 * HOUR))
    assert rig.state("soil_uncalibrated", box_snap()) == OK  # reset two hours ago: within its day
    os.utime(rig.parts["soil"].calibration_path(), (rig.clock.now - 3 * DAY, rig.clock.now - 3 * DAY))
    assert rig.state("soil_uncalibrated", box_snap()) == SILENT
    rig.parts["thirst"].data["pot_set_at"] = None
    assert rig.state("soil_uncalibrated", box_snap()) == UNCONFIGURED  # no pot
    assert Rig().state("soil_uncalibrated", box_snap()) == UNCONFIGURED  # no ECOWITT_IP: no sensor at all


def test_thirst_engine_and_sensor_look(isolated_state: Path) -> None:
    rig = Rig()
    rig.parts["thirst"] = None  # start_thirst failed
    found = rig.read({})["thirst_engine"]
    assert found["state"] == SILENT and found["cause"] == "missing"
    clock = Clock()
    index = thirst.Thirst(None, demo=True, clock=clock, tz=BERLIN, stage_source=lambda: STAGE, watering=Ctl(clock))
    config.atomic_write(index.pot_path(), json.dumps(thirst.Pot(volume_l=11.4, set_at=clock.now - DAY,
                                                                id="p").as_dict()) + "\n")
    rig = Rig(clock)
    rig.parts["thirst"] = index
    asyncio.run(index.check(box_snap()))
    assert rig.state("thirst_engine") == OK
    # the look's early returns are no look: an open pump run, no soil history
    sensor = rig.soil_sensor()
    index.soil_history = sensor.history
    index.state["runs"] = {"usb1": {"from": clock.now}}
    assert index.sensor_waterings() == [] and index.look_at is None
    clock.advance(1081)
    sensor.heard_at = clock.now
    index.checked_at = clock.now
    assert rig.state("thirst_sensor_look", box_snap()) == SILENT  # a stuck look shows after 1080 s
    index.state["runs"] = {}
    index.sensor_waterings()
    assert index.look_at == clock.now and rig.state("thirst_sensor_look", box_snap()) == OK
    index.soil_history = None
    clock.advance(1081)
    sensor.heard_at = clock.now
    index.checked_at = clock.now  # the poll goes on: an engine that stopped would hold the look (its parent)
    index.sensor_check(sensor.view())  # no history: no look
    assert rig.state("thirst_sensor_look", box_snap()) == SILENT


# ---------- the watering slots ----------

@pytest.mark.parametrize("fault", [
    {"kind": "skipped", "reason": "setup"}, {"kind": "skipped", "reason": "thirst", "gate": "no_flow"},
    {"kind": "skipped", "reason": "thirst", "gate": "no_power"}, {"kind": "skipped", "reason": "thirst", "gate": "error"},
    {"kind": "skipped", "reason": "thirst", "gate": "model"}, {"kind": "skipped", "reason": "port"}])
def test_two_fault_skips_in_a_row_are_silent(fault: dict[str, Any], isolated_state: Path) -> None:
    rig = Rig()
    _skips(slot(fault), slot(fault))(rig)
    found = rig.read({})["watering_skips"]
    assert found["state"] == SILENT
    assert supervision.reading(found, "de").startswith("Die letzten 2 Gießtermine liefen nicht: ")


@pytest.mark.parametrize("benign", [
    {"kind": "skipped", "reason": "thirst", "gate": gate} for gate in ("not_due", "hold", "blocked", "cap",
                                                                        "unmeasured", "too_short")] + [
    {"kind": "skipped", "reason": reason} for reason in ("connection", "supply", "busy", "missed")] + [
    {"kind": "skipped", "reason": "thirst"}])  # an event without a gate code (before 15.11.2026)
def test_benign_skips_break_the_chain(benign: dict[str, Any], isolated_state: Path) -> None:
    rig = Rig()
    _skips(slot(NO_FLOW), slot(benign), slot(NO_FLOW))(rig)
    assert rig.state("watering_skips") == OK


def test_watering_skips_classes(isolated_state: Path) -> None:
    publish = [START, {"at": T0, "kind": "stopped", "reason": "publish"}, {"at": T0, "kind": "skipped", "reason": "publish"}]
    rig = Rig()
    _skips(slot(*publish), slot(*publish))(rig)
    assert rig.state("watering_skips") == SILENT  # a start that ended in "skipped publish"
    rig = Rig()
    _skips(slot(NO_FLOW), slot(START, {"at": T0, "kind": "stopped", "reason": "duration"}))(rig)
    assert rig.state("watering_skips") == OK  # a start resets
    manual = [START, {"at": T0, "kind": "stopped", "reason": "manual"}]  # the owner's run is no slot
    rig = Rig()
    _skips(slot(NO_FLOW), manual, slot(NO_FLOW))(rig)
    assert rig.state("watering_skips") == SILENT
    assert supervision.slot_outcomes([{"kind": "due", "at": 1}]) == [{"at": 1, "outcome": None}]  # not ended yet


def test_the_gate_code_reaches_the_event_through_the_real_controller(isolated_state: Path) -> None:
    from vgrow.budget import CommandBudget
    from vgrow.portguard import PortGuard
    from vgrow.watering import WateringController

    clock = Clock(datetime(2026, 11, 15, 10, 0, 30, tzinfo=BERLIN).timestamp())

    class Box:
        port_listeners: list[Any] = []

        def snapshot(self) -> dict[str, Any]:
            return {"online": True, "mqtt": True, "shadow_age": 1, "sensors_age": 20, "pending": {},
                    "climate": {"water_kind": "wet_dry", "water_state": "wet"},
                    "ports": {"usb1": {"on": False, "mode": 0, "in_plan": 0, "accessory": 0, "can_write": True}}}

    box = Box()
    guard = PortGuard()
    guard.assign("usb1", "pump", box.snapshot())
    ctl = WateringController(box, guard, CommandBudget(lambda: clock.now), clock=clock, monotonic=lambda: 100.0)
    answers = iter([{"run": False, "reason": "no_flow", "text": {"de": "Erst den Durchfluss messen.", "en": "x"}},
                    {"run": False, "reason": "no_flow", "text": {"de": "Erst den Durchfluss messen.", "en": "x"}}])
    ctl.need_gate = lambda slot_at, duration: next(answers)

    async def go() -> None:
        first = datetime.fromtimestamp(clock.now, BERLIN)
        await ctl.configure({"duration_s": 60, "times": [f"{first.hour:02d}:{first.minute + 1:02d}"], "every_days": 1,
                             "first_date": first.date().isoformat(), "power_confirmed": True, "enabled": True})
        for _ in range(2):
            clock.advance(60)
            await ctl.tick()
            clock.advance(DAY - 60)
        await ctl.close()

    asyncio.run(go())
    skipped = [e for e in ctl.events if e["kind"] == "skipped"]
    assert [e.get("gate") for e in skipped] == ["no_flow", "no_flow"] and skipped[0]["reason"] == "thirst"
    rig = Rig(clock)
    rig.parts["watering"] = ctl
    assert rig.state("watering_skips") == SILENT
    assert Rig().state("watering_skips") == UNCONFIGURED


# ---------- the light timer and the loop beats ----------

def test_light_timer_and_loop_beats(isolated_state: Path) -> None:
    rig = Rig()
    timer = _timer(rig)
    rig.parts["light_schedule"] = timer
    assert rig.state("light_timer") == OK
    timer.failed = light_schedule.boundaries(timer.cfg, rig.clock.now)[0]["at"]  # two unconfirmed attempts
    found = rig.read({})["light_timer"]
    assert found["state"] == SILENT and found["cause"] == "unconfirmed"
    timer.override_until = rig.clock.now + HOUR  # the owner's change until the next edge: not this signal's
    assert rig.state("light_timer") == OK
    timer.override_until = None
    timer.cfg = {**timer.cfg, "enabled": False}
    assert rig.state("light_timer") == OFF
    timer.cfg = {**timer.cfg, "enabled": True}
    timer.failed = None
    rig.clock.advance(31)  # no tick of its loop for 31 s
    assert rig.read({})["light_timer"]["cause"] == "beat" and rig.state("light_timer") == SILENT
    timer.task = Task("light_schedule", done=True)
    timer.ticked_at = rig.clock.now
    assert rig.state("light_timer") == SILENT  # a dead loop at once
    # the pump's watchdog: the same
    ctl = Ctl(rig.clock)
    rig.parts["watering"] = ctl
    assert rig.state("watering_watchdog") == OK
    ctl.task = Task("watering_watchdog", done=True)
    assert rig.state("watering_watchdog") == SILENT


def test_the_loops_set_their_beats(isolated_state: Path) -> None:
    from vgrow.budget import CommandBudget
    from vgrow.watering import WateringController

    class Quiet:
        port_listeners: list[Any] = []

        def snapshot(self) -> dict[str, Any]:
            return {"online": False, "light": {"level": 0, "mode": 0, "in_plan": False}}

    async def go() -> tuple[Any, Any]:
        ctl = WateringController(Quiet(), budget=CommandBudget(lambda: T0))
        timer = light_schedule.LightSchedule(Quiet())
        ctl.launch()
        timer.launch()
        for _ in range(100):
            if ctl.ticked_at is not None and timer.ticked_at is not None:
                break
            await asyncio.sleep(0.02)
        await ctl.close()
        await timer.close()
        return ctl, timer

    ctl, timer = asyncio.run(go())
    assert ctl.ticked_at is not None and timer.ticked_at is not None


def test_hank_beats_in_mode_off(isolated_state: Path) -> None:
    hank_core.write_mode("off")

    async def go() -> float | None:
        hank = hank_core.Hank(live=None, tick_seconds=0.02, index=hank_core.shared_index())
        await hank.start()
        for _ in range(750):
            if hank.ticked_at is not None:
                break
            await asyncio.sleep(0.02)
        await hank.stop()
        return hank.ticked_at

    assert asyncio.run(go()) is not None  # tick() returns early in mode off, and that still counts
    rig = Rig()
    rig.parts["hank"] = None  # build_hank failed
    found = rig.read({})["hank_silent"]
    assert found["state"] == SILENT and found["cause"] == "missing"


# ---------- the box's signals ----------

def test_device_link_probe_builtin_tank(isolated_state: Path) -> None:
    rig = Rig()
    assert rig.state("device_link", box_snap(shadow_age=None)) == STARTING  # no shadow yet in this process
    rig.clock.advance(481)
    assert rig.state("device_link", box_snap(shadow_age=None)) == SILENT
    rig = Rig()
    rig.read(box_snap())
    rig.clock.advance(481)
    found = rig.read(box_snap(mqtt=False))["device_link"]
    assert found["state"] == SILENT and found["cause"] == "link"
    assert rig.state("device_link", box_snap(mqtt=False, sensors_age=400)) == WAITING  # stale: the offline rule's
    # the tank: a type-10 level is a reading too
    rig = Rig()
    level = box_snap(aux_device=10, climate={"water_kind": "level", "water_state": None, "water_level": 75.0})
    assert rig.state("tank_sensor_silent", level) == OK
    rig.clock.advance(901)
    assert rig.state("tank_sensor_silent", box_snap(aux_device=10, climate={"water_state": None})) == SILENT
    # the fan stick, found and with its rule on; the ruler and the pest watch gated by the lit hours
    rig = Rig()
    _stick(rig, age=None)
    assert rig.state("fan_stick_silent", box_snap()) == STARTING
    rig = Rig()
    rig.parts["grow_plan"] = Plan({"height": {"at": rig.clock.now - 3 * DAY},
                                   "height_problem": {"why": "no_strip", "since": rig.clock.now - HOUR}})
    assert rig.state("ruler_silent", box_snap()) == WAITING  # the plan's own push after 24 h owns that
    assert rig.state("ruler_silent", box_snap(light_day=None)) == WAITING
    rig = Rig()
    rig.parts["pest"] = Pest([{"at": rig.clock.now - 3 * DAY, "result": {"ok": 1}}], camera=None)
    assert rig.state("pest_watch_silent", box_snap()) == WAITING  # no camera: the camera rules speak


def test_the_store_and_the_measured_day(isolated_state: Path) -> None:
    rig = Rig()
    rig.parts.update(store=object(), collector=Collector(None, saved=rig.clock.now - 60), light_day=object())
    assert rig.state("store_silent", box_snap()) == OK  # collector.json while the collector holds nothing yet
    rig.clock.advance(601)
    rig.parts["collector"].last_ok = rig.clock.now
    no_day = box_snap(light_day=None)
    no_day.pop("light_day")
    found = rig.read(no_day)["store_silent"]
    assert found["state"] == SILENT and found["cause"] == "day"  # the day unreadable for DAY_REFRESH
    assert rig.state("store_silent", box_snap()) == OK
    rig.parts["collector"] = None  # its start failed
    found = rig.read(box_snap())["store_silent"]
    assert found["state"] == SILENT and found["cause"] == "missing"


def test_news_is_read_without_touching_a_broken_file(isolated_state: Path) -> None:
    rig = Rig()
    rig.parts["news"] = object()
    path = config.STATE_DIR / "news" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    assert rig.state("news_silent") == STARTING
    assert path.read_text(encoding="utf-8") == "{broken" and len(list(path.parent.iterdir())) == 1  # left where it is
    rig = Rig()
    rig.parts["news"] = object()
    config.atomic_write(path, json.dumps({"first_run": datetime.fromtimestamp(rig.clock.now - 3 * DAY)
                                          .astimezone().isoformat()}) + "\n")
    assert rig.state("news_silent") == SILENT  # never a success since the first run three days ago


# ---------- the tasks ----------

def test_tasks_dead(isolated_state: Path) -> None:
    rig = Rig()
    sensor = rig.soil_sensor()
    sensor.task = Task("soil_sensor", done=True)  # type: ignore[assignment]
    found = rig.read({})["tasks_dead"]
    assert found["state"] == SILENT and found["params"]["names"] == ["soil_sensor"]
    sensor.task = Task("soil_sensor", cancelled=True)  # type: ignore[assignment]  # stopped at shutdown
    assert rig.state("tasks_dead") == OK
    sensor.task = None  # type: ignore[assignment]
    camera = type("Cam", (), {"watch_task": Task("growcam_watch"), "search_task": Task("growcam_search", done=True)})()
    rig.parts["camera"] = camera
    assert rig.state("tasks_dead") == OK  # the search ends by design
    rig.parts["grow_plan"] = None  # its start failed
    assert rig.read({})["tasks_dead"]["params"]["names"] == ["grow_plan"]
    rig.parts["grow_plan"] = Plan({})
    # the owned names are another signal's
    rig.box.tasks["vgrow_poll"] = Task("vgrow_poll", done=True)
    rig.parts["watering"] = Ctl(rig.clock)
    rig.parts["watering"].task = Task("watering_watchdog", done=True)
    found = rig.read({})
    assert found["tasks_dead"]["state"] == OK
    assert found["poll_stalled"]["state"] == SILENT and found["watering_watchdog"]["state"] == SILENT
    # hank_protocol has no handle: its name among the running tasks
    rig.running = []
    assert rig.read({})["tasks_dead"]["params"]["names"] == ["hank_protocol"]
    # nothing inside the grace
    rig.sup.grace_until = rig.clock.now + 10
    assert rig.read({})["tasks_dead"]["params"]["names"] == []
    # a running task nobody declared is listed, the unnamed and Hank's made names are not, nor the MQTT client's loops,
    # which every real run with MQTT up has (their link reports them: device_link, fan_stick_silent)
    rig.running = [Task("hank_protocol"), Task("new_part"), Task("Task-12"), Task("hank_tick"), Task("growcam_search"),
                   Task("vivosun_mqtt_receive"), Task("vivosun_mqtt_keepalive"), Task("hank-jev-watch")]
    assert rig.sup.unwatched() == ["new_part"]


def test_declared_tasks_ast_scan() -> None:
    """Every asyncio.create_task with a constant name in vgrow/ is declared: watched (TASKS), another signal's (OWNED),
    ending by design (TRANSIENT) or the vendored MQTT client's, watched through its link (EXTERNAL). The vendored code
    is scanned too, so an upstream update that names a new task fails here. Dynamic and unnamed calls and the
    create_task methods of hank_protocol.py are not asyncio's and stay out."""
    found: dict[str, str] = {}
    for path in sorted((REPO / "vgrow").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_task" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "asyncio"):
                continue
            name = next((kw.value for kw in node.keywords if kw.arg == "name"), None)
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                found[name.value] = f"{path.relative_to(REPO)}:{node.lineno}"
    declared = {*supervision.TASKS, *supervision.OWNED, *supervision.TRANSIENT, *supervision.EXTERNAL}
    assert {name: where for name, where in found.items() if name not in declared} == {}
    assert {"soil_sensor", "vgrow_poll", "vgrow_supervision", "daily_photo"} <= set(found)  # the scan sees them
    assert set(supervision.EXTERNAL) <= set(found)  # and the vendored client's loops
    assert set(supervision.OWNED.values()) <= set(supervision.BY_ID)
    assert {sig for sigs in supervision.EXTERNAL.values() for sig in sigs} <= set(supervision.BY_ID)
    assert not set(supervision.TASKS) & set(supervision.OWNED)
    assert set(supervision.TASK_OF) <= set(supervision.BY_ID)
    assert set(supervision.TASK_OF.values()) <= set(supervision.TASKS)


def test_coverage_of_device_check_devices() -> None:
    assert set(supervision.COVERAGE) == set(device_check.DEVICES)
    known = set(supervision.BY_ID) | set(RULES)
    for device, watched in supervision.COVERAGE.items():
        assert watched and set(watched) <= known, device


# ---------- mutes, the "was away" notice, the state file, peaks ----------

def test_mute_and_away_and_persistence(isolated_state: Path) -> None:
    rig = Rig()
    for signal, days, why in (("soil_silent", 3, ""), ("soil_silent", 3, "   "), ("soil_silent", 15, "weg"),
                              ("soil_silent", 0, "weg"), ("soil_silent", True, "weg"), ("soil_silent", 2.5, "weg"),
                              ("nope", 3, "weg"), (None, 3, "weg")):
        with pytest.raises(UserError):
            rig.sup.mute(signal, days, why)
    muted = rig.sup.mute("soil_silent", 3, "Gateway abgebaut")
    assert muted["until"] == rig.clock.now + 3 * DAY and muted["why"] == "Gateway abgebaut"
    _soil(601)(rig)
    assert rig.state("soil_silent", box_snap()) == "muted"
    listed = rig.sup.view({}, "de")["muted"]
    assert [m["signal"] for m in listed] == ["soil_silent"] and listed[0]["why"] == "Gateway abgebaut"
    asyncio.run(rig.sup.save())
    again = Supervisor(rig.box, rig.alerts, rig.parts, clock=rig.clock, tasks=lambda: [])
    again.grace_until = 0.0
    assert again.read(box_snap())["signals"]["soil_silent"]["state"] == "muted"  # survives a restart
    rig.clock.advance(3 * DAY + 1)
    assert again.read(box_snap())["signals"]["soil_silent"]["state"] == SILENT  # it ended by itself
    assert again.view({}, "de")["muted"] == []
    assert again.unmute("soil_silent") is False and again.mute("soil_silent", 1, "x") and again.unmute("soil_silent")
    # the "was away" notice: a gap over 300 s since alive_at, pushed once, tried again until accepted
    path = config.STATE_DIR / supervision.STATE_FILE
    config.atomic_write(path, json.dumps({"alive_at": rig.clock.now - 38 * 60}) + "\n")
    rig.alerts.answer = False
    away = Supervisor(rig.box, rig.alerts, {"watering": Ctl(rig.clock)}, clock=rig.clock, tasks=lambda: [])
    asyncio.run(away.tick())
    titles = [p[0] for p in rig.alerts.pushed if p[0] == "VGrow lief nicht"]
    assert len(titles) == 1 and away.state["notices"]  # not accepted: still pending
    rig.alerts.answer = True
    rig.clock.advance(60)
    asyncio.run(away.tick())
    notices = [p for p in rig.alerts.pushed if p[0] == "VGrow lief nicht"]
    assert len(notices) == 2 and away.state["notices"] == [] and notices[-1][2] == supervision.AWAY_PRIORITY
    assert "In der Zeit liefen weder Alarme noch Automatik." in notices[-1][1] and "nicht nachgegossen" in notices[-1][1]
    asyncio.run(away.tick())
    assert len([p for p in rig.alerts.pushed if p[0] == "VGrow lief nicht"]) == 2  # once
    assert away.view({}, "de")["process"]["last_gap"]["to"] == away.started
    # a deploy-sized gap says nothing
    config.atomic_write(path, json.dumps({"alive_at": rig.clock.now - 20}) + "\n")
    deploy = Supervisor(rig.box, rig.alerts, {}, clock=rig.clock, tasks=lambda: [])
    assert deploy.state["notices"] == [] and len(deploy.state["starts"]) == 1
    # a tick gap of a Mac that slept does
    deploy.watch_beat = rig.clock.now - 400
    before = len(rig.alerts.pushed)
    asyncio.run(deploy.tick())
    assert [p[0] for p in rig.alerts.pushed[before:]].count("VGrow lief nicht") == 1
    # a corrupt state file is tolerated
    path.write_text("{not json", encoding="utf-8")
    assert Supervisor(rig.box, rig.alerts, {}, clock=rig.clock, tasks=lambda: []).state["mutes"] == {}
    path.write_text(json.dumps({"mutes": [1], "seen": "x", "peaks": {"probe_silent": "y"}, "starts": 3}),
                    encoding="utf-8")
    tolerant = Supervisor(rig.box, rig.alerts, {}, clock=rig.clock, tasks=lambda: [])
    assert tolerant.state["seen"] == {} and tolerant.state["peaks"] == {}


def test_peaks_keep_fourteen_days(isolated_state: Path) -> None:
    rig = Rig()
    rig.parts["thirst"] = FakeThirst(rig.clock)
    for day in range(20):
        rig.parts["thirst"].checked_at = rig.clock.now - 100 - day
        rig.read({})
        rig.clock.advance(DAY)
    days = rig.sup.state["peaks"]["thirst_engine"]
    assert len(days) == supervision.PEAK_DAYS and rig.sup.peak("thirst_engine") == 119


def test_the_view_holds_what_the_pc_reads(isolated_state: Path) -> None:
    rig = Rig()
    rig.box.polled_at = rig.clock.now - 41
    rig.sup.watch_beat = rig.clock.now - 10
    rig.sup.task = Task("vgrow_supervision")  # type: ignore[assignment]
    _uncalibrated(9 * DAY)(rig)
    rig.parts["soil"].task = Task("soil_sensor")
    rig.read(box_snap())
    rig.sup.watchdog_snapshot()
    view = rig.sup.view({}, "de")
    assert {"poll", "watcher", "loops", "signals", "active", "muted", "unwatched", "process", "push", "device_tests",
            "disk"} <= set(view)
    assert view["poll"]["age_s"] == 41 and view["poll"]["limit_s"] == 300 and view["watcher"]["age_s"] == 10
    assert view["loops"]["vgrow_poll"] == "running" and view["loops"]["soil_sensor"] == "running"
    uncal = view["signals"]["soil_uncalibrated"]
    assert uncal["state"] == SILENT and uncal["level"] == "yellow" and uncal["text"].startswith("Seit 9 Tagen")
    assert uncal["text"].endswith(RULES["soil_uncalibrated"]["hint"]) and uncal["limit_s"] == DAY
    assert view["signals"]["poll_stalled"]["state"] == OK  # the watchdog's own block
    assert json.dumps(view)


# ---------- the review of 15.11.2026: false alarms, one cause one push, parts nobody saw ----------

def test_the_light_timer_reads_the_lamp_and_waits_across_an_edge(isolated_state: Path) -> None:
    """A failed edge stayed silent, with a reminder every 6 h, for a lamp switched in the VIVOSUN app or reported late,
    because the timer itself never looks again; and the next edge gave an all-clear before its own switch was
    confirmed, so a standing fault was one alarm and one all-clear per edge."""
    rig = Rig()
    timer = _timer(rig)  # on 06:00 at 80 %, off 00:00; T0 is 05:33 in Berlin, so the OFF edge of 00:00 holds
    rig.parts["light_schedule"] = timer
    edge = light_schedule.boundaries(timer.cfg, rig.clock.now)[0]
    previous = light_schedule.boundaries(timer.cfg, edge["at"] - 1)[0]
    assert (edge["level"], previous["level"]) == (0, 80) and rig.clock.now - edge["at"] > HOUR
    stuck = 60  # a level neither edge wants

    def lamp(level: Any) -> dict[str, Any]:
        return {"light": {"level": level}}

    timer.failed = edge["at"]
    assert rig.state("light_timer", lamp(previous["level"])) == SILENT  # the lamp still where it was
    assert rig.state("light_timer", lamp(None)) == SILENT  # an unknown level never stands
    assert rig.state("light_timer", lamp(edge["level"])) == OK  # switched in the app, or applied late
    # an earlier edge failed: while the timer tries this one, the signal waits, it gives no all-clear
    timer.failed = previous["at"]
    timer.pending = {"boundary": edge["at"], "target": edge["level"], "at": rig.clock.now - 20, "attempts": 1}
    assert rig.state("light_timer", lamp(stuck)) == WAITING
    assert rig.state("light_timer", lamp(edge["level"])) == OK  # it stands: no wait for the timer's own word
    timer.pending = None  # long after the edge and no attempt (a blocked timer): not this signal's business
    assert rig.state("light_timer", lamp(stuck)) == OK
    # just after an edge, before the timer's first attempt: waiting too
    near = Rig(Clock(edge["at"] + 20))
    early = _timer(near)
    early.failed = previous["at"]
    near.parts["light_schedule"] = early
    assert near.state("light_timer", lamp(previous["level"])) == WAITING
    early.completed = edge["at"]  # the timer confirmed this edge: its own verdict, as before
    assert near.state("light_timer", lamp(previous["level"])) == OK


def test_a_storage_fault_is_silent_never_the_owners_off(isolated_state: Path) -> None:
    """An unreadable light-schedule.json or watering.json comes up disabled with error "storage", which read as the
    owner's own off; an enabled timer whose save failed is blocked for good while its loop still beats."""
    from vgrow.budget import CommandBudget
    from vgrow.watering import WateringController

    folder = config.STATE_DIR / "boxes" / "box-1"
    folder.mkdir(parents=True)
    (folder / "light-schedule.json").write_text("{broken", encoding="utf-8")
    (folder / "watering.json").write_text("{broken", encoding="utf-8")
    rig = Rig()
    timer = light_schedule.LightSchedule(None, clock=rig.clock)
    ctl = WateringController(object(), budget=CommandBudget(lambda: T0), clock=rig.clock)
    assert timer.error == ctl.error == "storage" and not timer.cfg["enabled"] and not ctl.cfg["enabled"]
    timer.ticked_at = rig.clock.now
    rig.parts.update(light_schedule=timer, watering=ctl)
    found = rig.read({})
    assert (found["light_timer"]["state"], found["light_timer"]["cause"]) == (SILENT, "storage")
    assert (found["watering_skips"]["state"], found["watering_skips"]["cause"]) == (SILENT, "storage")
    light = supervision.reading(found["light_timer"], "de")
    assert light.startswith("Der Lichttimer kann seine Einstellungen nicht sicher")
    assert "„Lichttimer speichern“" in light
    assert "„Gießplan speichern“" in supervision.reading(found["watering_skips"], "de")
    assert "“Save watering schedule”" in supervision.reading(found["watering_skips"], "en")
    # an enabled timer whose save failed: silent although its beat is fresh
    working = Rig()
    enabled = _timer(working)
    enabled.error = "storage"
    working.parts["light_schedule"] = enabled
    assert working.state("light_timer") == SILENT
    # the owner's own off carries no error: quiet as before
    enabled.error = None
    enabled.cfg = {**enabled.cfg, "enabled": False}
    assert working.state("light_timer") == OFF


def test_push_broken_counts_only_while_the_attempts_still_fail(isolated_state: Path) -> None:
    """One failed push nobody retries (an all-clear, a one-shot notice) kept failed_since set, and 20 minutes later
    push_broken said every push fails, over a working ntfy."""
    rig = Rig()
    rig.alerts.target = ("https://ntfy.example", "t")
    record = rig.alerts._push_record()
    record.update(failed_since=rig.clock.now - 1000, failed_at=rig.clock.now - 1000)  # one failure, never retried
    assert rig.state("push_broken") == OK
    record.update(failed_at=rig.clock.now - PUSH_RUN_GAP_S - 1)
    assert rig.state("push_broken") == OK
    record.update(failed_at=None)  # a run without its newest try proves nothing about now
    assert rig.state("push_broken") == OK
    record.update(failed_at=rig.clock.now - 30)  # still tried, still failing: the outage is real
    found = rig.read({})["push_broken"]
    assert (found["state"], found["cause"]) == (SILENT, "failing")
    assert supervision.reading(found, "de") == "Seit 16 Minuten scheitert jeder Push."


def test_a_schedule_switched_on_again_starts_its_slots_afresh(isolated_state: Path) -> None:
    """watering.json keeps its events while the schedule is off: two failed slots of last week made watering_skips
    silent at the first poll after the owner fixed the cause and switched the schedule on again. Only the slots after
    the switch-on count (WateringController.enabled_at, kept in the file)."""
    from vgrow.budget import CommandBudget
    from vgrow.portguard import PortGuard
    from vgrow.watering import WateringController

    clock = Clock(datetime(2026, 11, 15, 10, 0, 30, tzinfo=BERLIN).timestamp())

    class Box:
        port_listeners: list[Any] = []

        def snapshot(self) -> dict[str, Any]:
            return {"online": True, "mqtt": True, "shadow_age": 1, "sensors_age": 20, "pending": {},
                    "climate": {"water_kind": "wet_dry", "water_state": "wet"},
                    "ports": {"usb1": {"on": False, "mode": 0, "in_plan": 0, "accessory": 0, "can_write": True}}}

    box = Box()
    guard = PortGuard()
    guard.assign("usb1", "pump", box.snapshot())
    ctl = WateringController(box, guard, CommandBudget(lambda: clock.now), clock=clock, monotonic=lambda: 100.0)
    ctl.need_gate = lambda slot_at, duration: {"run": False, "reason": "no_flow", "text": {"de": "x", "en": "x"}}
    first = datetime.fromtimestamp(clock.now, BERLIN)
    plan = {"duration_s": 60, "times": [f"{first.hour:02d}:{first.minute + 1:02d}"], "every_days": 1,
            "first_date": first.date().isoformat(), "power_confirmed": True, "enabled": True}

    async def slots(n: int) -> None:  # n days, each with its slot at the minute after the clock's
        for _ in range(n):
            clock.advance(60)
            await ctl.tick()
            clock.advance(DAY - 60)

    asyncio.run(ctl.configure(plan))
    assert ctl.enabled_at == clock.now  # switched on from the plan the controller starts with
    asyncio.run(slots(2))
    rig = Rig(clock)
    rig.parts["watering"] = ctl
    assert rig.state("watering_skips") == SILENT
    asyncio.run(ctl.configure({**plan, "enabled": False}))  # the owner switches it off and fixes the flow
    assert rig.state("watering_skips") == OFF
    clock.advance(DAY)  # a day later, at the same time, so the next slot is due within its grace
    asyncio.run(ctl.configure(plan))  # and on again: the old failed slots are history
    assert ctl.enabled_at == clock.now and rig.state("watering_skips") == OK
    assert WateringController(box, guard, CommandBudget(lambda: clock.now), clock=clock).enabled_at == clock.now
    switched_on = ctl.enabled_at
    asyncio.run(ctl.configure({**plan, "duration_s": 90}))  # a change while on is no new switch-on
    assert ctl.enabled_at == switched_on
    asyncio.run(slots(1))
    assert rig.state("watering_skips") == OK  # one new failed slot is a blip
    asyncio.run(slots(1))
    assert rig.state("watering_skips") == SILENT  # two after the switch-on are a pattern again


def test_the_tank_sensor_waits_behind_the_device_link(isolated_state: Path) -> None:
    """A restart while the cloud MQTT stays away leaves aux35Dev unknown, so no row decodes: the latched tank sensor
    counted from the start and blamed its plug beside device_link, for one cause."""
    rig = Rig()
    rig.read(box_snap())  # type 9 seen: latched
    asyncio.run(rig.sup.save())
    again = Rig(Clock(rig.clock.now + HOUR))  # a new process, and the shadow never comes
    no_shadow = box_snap(mqtt=False, shadow_age=None, aux_device=None,
                         climate={"water_kind": None, "water_state": None})
    assert again.state("tank_sensor_silent", no_shadow) == STARTING
    again.clock.advance(901)
    found = again.read(no_shadow)
    assert found["device_link"]["state"] == SILENT and found["tank_sensor_silent"]["state"] == WAITING
    assert found["tank_sensor_silent"]["found"] == SILENT  # its own finding stays beside the final state
    # with the shadow back the sensor is judged on its own: a dead one is silent at once
    found = again.read(box_snap(climate={"water_state": None}))
    assert found["device_link"]["state"] == OK and found["tank_sensor_silent"]["state"] == SILENT


def test_the_fan_stick_and_the_look_wait_for_their_cause(isolated_state: Path) -> None:
    """The stick reads its shadow with the box's AWS session: a cloud outage is the offline rule's. A thirst ledger that
    fails on every poll is thirst_engine's, not a second push about hand waterings."""
    rig = Rig()
    _stick(rig, age=2 * HOUR)
    assert rig.state("fan_stick_silent", box_snap()) == SILENT
    assert rig.state("fan_stick_silent", box_snap(online=False)) == WAITING
    assert rig.state("fan_stick_silent", box_snap(sensors_age=400)) == WAITING
    rig = Rig()
    _look(3 * HOUR)(rig)
    rig.parts["thirst"].checked_at = rig.clock.now - 400
    found = rig.read(box_snap())
    assert found["thirst_engine"]["state"] == SILENT and found["thirst_sensor_look"]["state"] == WAITING
    rig.parts["thirst"].checked_at = rig.clock.now
    assert rig.state("thirst_sensor_look", box_snap()) == SILENT  # the look's own silence, once the engine runs


def test_a_dead_task_holds_its_parts_own_rule(isolated_state: Path) -> None:
    """A dead soil loop pushed "restart VGrow" (tasks_dead) and half an hour later "Gateway: Netzteil und WLAN
    prüfen" (soil_silent): two pushes for one cause, the second with the wrong step."""
    rig = Rig()
    sensor = rig.soil_sensor(heard=0.0)
    rig.read(box_snap())
    rig.clock.advance(601)
    sensor.task = Task("soil_sensor", done=True)  # type: ignore[assignment]
    found = rig.read(box_snap())
    assert found["tasks_dead"]["state"] == SILENT and found["tasks_dead"]["params"]["names"] == ["soil_sensor"]
    assert found["soil_silent"]["state"] == WAITING and found["soil_silent"]["found"] == SILENT
    # only its own task explains its silence: another dead one leaves it as it is
    sensor.task = Task("soil_sensor")  # type: ignore[assignment]
    rig.parts["news"] = type("News", (), {"task": Task("news_watch", done=True)})()
    found = rig.read(box_snap())
    assert found["tasks_dead"]["params"]["names"] == ["news_watch"] and found["soil_silent"]["state"] == SILENT
    # a dead task the owner muted gates nothing, as a muted parent
    rig.sup.mute("tasks_dead", 1, "Nachrichten bewusst aus")
    sensor.task = Task("soil_sensor", done=True)  # type: ignore[assignment]
    assert rig.state("soil_silent", box_snap()) == SILENT


def test_a_restart_loop_is_said_and_needs_no_grace(isolated_state: Path) -> None:
    """A process that dies every few minutes to hours leaves no gap over GAP_S, resets the evidence in memory at every
    start and hides the rest behind the grace: only the starts of 24 h say it. A deploy's own start is no such restart
    (the Mac saw 31 pushes on 22.10.2026): install_mac.sh leaves its mark just before it stops the server."""
    clock = Clock()

    def start() -> Supervisor:  # one process: built, saved by its watcher, and dead ten minutes later
        sup = Supervisor(FakeBox(), Pushes(), {}, clock=clock, tasks=lambda: [])
        asyncio.run(sup.save())
        clock.advance(600)
        return sup

    for _ in range(supervision.RESTARTS_24H - 1):
        start()
    config.atomic_write(config.STATE_DIR / supervision.DEPLOY_FILE, f"{int(clock.now) - 5}\n")  # install_mac.sh
    deployed = start()
    assert deployed.read({})["signals"]["restarts"]["state"] == OK  # 20 starts, one of them the deploy's
    process = deployed.view({}, "de")["process"]
    assert (process["starts_24h"], process["deploys_24h"]) == (20, 1)
    clock.advance(supervision.DEPLOY_WINDOW_S)
    sup = Supervisor(FakeBox(), Pushes(), {}, clock=clock, tasks=lambda: [])  # well after the mark: a crash
    found = sup.read({})["signals"]["restarts"]  # inside its own grace, yet silent: the count is whole at the start
    assert found["state"] == SILENT and found["params"]["n"] == supervision.RESTARTS_24H
    assert supervision.reading(found, "de") == ("VGrow startete in den letzten 24 Stunden 20 Mal neu, ohne dass "
                                                "ausgeliefert wurde.")
    clock.advance(DAY)  # a day later the old starts have gone
    assert Supervisor(FakeBox(), Pushes(), {}, clock=clock, tasks=lambda: []).read({})["signals"]["restarts"][
        "state"] == OK
    # a broken mark is no mark, and never stops the supervision from starting
    for broken in (b"\xff\xfe garbage", b"soon", b""):
        (config.STATE_DIR / supervision.DEPLOY_FILE).write_bytes(broken)
        assert Supervisor(FakeBox(), Pushes(), {}, clock=clock, tasks=lambda: []).state["deploys"] == []


def test_the_view_text_follows_the_readers_finding(isolated_state: Path) -> None:
    """A rule stays active while the grace after a start or a silent parent turns its signal into starting or waiting,
    and its card showed only the hold time then ("länger als 10 min")."""
    rig = Rig()
    _uncalibrated(9 * DAY)(rig)
    rig.parts["thirst"].data["measured_at"] = rig.clock.now - 3 * DAY
    rig.sup.grace_until = rig.clock.now + supervision.GRACE_S  # the second start, the rule still active
    rig.read(box_snap())
    signals = rig.sup.view({}, "de")["signals"]
    uncal, child = signals["soil_uncalibrated"], signals["thirst_input"]
    assert uncal["state"] == STARTING and uncal["text"].startswith("Seit 9 Tagen liefert der Sensor Werte")
    assert uncal["text"].endswith(RULES["soil_uncalibrated"]["hint"])
    assert child["state"] == WAITING and child["text"].startswith("Seit 3 Tagen hat der Durst-Index keine Messung")
    assert child["text"].endswith(RULES["thirst_input"]["hint"])
    rig = Rig()
    _stick(rig, age=None)
    rig.read(box_snap())  # the reader's own starting: no proof yet, no sentence; the page says it is being checked
    stick = rig.sup.view({}, "de")["signals"]["fan_stick_silent"]
    assert stick["state"] == STARTING and stick["text"] is None
