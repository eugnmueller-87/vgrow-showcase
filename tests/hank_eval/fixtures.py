"""The live states of the eval set: F22 (22.10.2026, fresh readings) and F22-alt (the same, readings 25 minutes old).

What F22 fixes comes from the spec (docs/hank-v1-spec.md 10.4, the example in 4.2 and the ledger example in 6.1) and
from the eval set's coverage note: Ice Cream Cake Automatic, day 42 since 10.09., height 61 cm, stage early_flower by
stage_estimate (not confirmed), light 60 % with the bloom spectrum (constructed), probe 79 %, box 47 %, water 75 %, no
temperature and no VPD, USB 2 humidifier off, the humidifier automation on and paused for the unconfirmed stage,
exhaust fan step 3, readings 60 s old. USB 1: on 22.10.2026 the camera's power, on and locked; since 23.10.2026 the
drip pump (ports.json, read when the fixture is built), which runs only by hand, so F22 shows USB 1 off and the
camera on a power supply of its own (it needs one since then). F22-alt: the same with readings 1500 s old.
F22-veg (v1.4, the method cards before flowering): F22 with the owner's stage "veg" in the plant view, so a method
answer adds no window line. F22-nocannabis (v1.4): F22 with the cannabis package switched off in settings.json; the
next other fixture takes that switch out again (only the one this fixture wrote, marked PACKAGES_MARK, never one a
test set itself), so a run's cases do not leak into each other.
Feeding v2 (23.10.2026): the feeding view is built as Hank's wiring builds it (feeding.context, every unit the page
shows). F22 doses BioBizz (the owner fed it on 22.10.). F22-vivosun: VIVOSUN Base A + B chosen and confirmed, the
owner's stage early flower since 22.10. (CONSTRUCTED), so the amounts are grams with spoons. F22-nochoice: two
fertilizers and none chosen (the state of a new install). Both put the plant log back as it was after building, so
the next case of a run starts from F22 again.
F22-vpdhigh (23.10.2026, VPD too high): F22 with the climate measured live on 23.10.2026 at 21:20, probe 25.06 °C,
45.5 %, VPD 1.73 kPa, the box's own sensor 22.6 °C, 43.9 %, 1.53 kPa, the light at 75 % without a light plan, and the
autopilot's brief in the snapshot as the server adds it (mode off, the default). The stage stays F22's estimate (early
flower from the camera, not confirmed), the humidifier rule on and paused for it, nobody has named the humidifier on
USB 2 on the page: every block Hank has to name. The hour stays F22's (CONSTRUCTED: the day is frozen).
F22-thirst (the Durst-Index, 26.10.2026): F22 with the Durst-Index's brief in the snapshot as server.HankBox adds
it: the owner's pot as he entered it on 27.10.2026 (3 US gallons, about 11.4 l), weighed full and drained, not
checked since, so the modelled thirst index stands at THIRST_TI and the decision is the check request. The stage stays
F22's estimate (not confirmed). CONSTRUCTED: the weighing and its time; the ET history is the model's own rate for
F22's light and climate, held back to the weighing, at F22's hour.

Values the spec leaves open are marked CONSTRUCTED where they are set. plant.json, ports.json, alerts.json and the
cards are read when a fixture is built, not copied (spec 12.1 item 11): the fixture follows the repo. The objects
handed out are the ones wiring.py reads (spec 4.2): box.snapshot() shape, plant.view, automation.view, alerts.view,
feeding.view, a camera status and vision.view (only busy).

Building writes the plant log and the automation state into config.STATE_DIR, so call it with an isolated state
folder (the tests' conftest does that; scripts/hank_eval.py uses a temporary one) and with the day frozen (freeze()
or frozen_day()).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from vgrow import config, feeding, plant
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation
from vgrow.plant import PlantLog, current_stage, evaluate, load_profile

F22_DATE = date(2026, 10, 22)
F22_NOW = datetime(2026, 10, 22, 14, 48)  # CONSTRUCTED: the spec fixes the day, not the hour; lights on at 14:48
F22_UTC_OFFSET = timedelta(hours=2)  # Berlin summer time on 22.10.2026 (it ends on 25.10.2026)
FRESH_SECONDS = 60  # spec 10.4: readings fresh, 60 s
STALE_SECONDS = 1500  # spec 10.4: F22-alt, readings 1500 s (25 minutes) old
HEIGHT_CM = 61  # spec 10.4
LIGHT_PERCENT = 60  # spec 10.4: constructed in the eval set, nowhere measured
PROBE_HUMIDITY = 79.0
BOX_HUMIDITY = 47.0
WATER_LEVEL = 75.0  # spec 4.2, example "STAND" for F22
DFAN_STEP = 3  # spec 6.1, the ledger example of this very case (baseline dfan 3)
NAMES = ("F22", "F22-alt", "F22-veg", "F22-nocannabis", "F22-vivosun", "F22-nochoice", "F22-vpdhigh", "F22-thirst")
THIRST_TI = 92  # F22-thirst: the modelled thirst index of the fixture's pot (CONSTRUCTED)
# F22-vpdhigh: measured live on 23.10.2026, 21:20 (probe at leaf height, the box's own sensor)
VPD_HIGH_CLIMATE = {"probe_temp": 25.06, "probe_humidity": 45.5, "probe_vpd": 1.73, "temp": 22.6, "humidity": 43.9,
                    "vpd": 1.53}
VPD_HIGH_LIGHT = 75
VIVOSUN_STAGE = ("early_flower", "2026-10-22")  # F22-vivosun: the owner's stage and its day (CONSTRUCTED)
PACKAGES_MARK = "_eval_fixture_packages"  # settings.json: the package switch below was written by F22-nocannabis
VEG_STAGE = {"key": "veg", "set_by": "manual"}  # F22-veg: the stage the owner set, before flowering (CONSTRUCTED)


def _frozen_date_class(day: date) -> type[date]:
    class Day(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return day

    return Day


def freeze(monkeypatch: Any, day: date = F22_DATE) -> None:
    """Pin plant's date.today() and feeding.local_today() to the fixture's day (spec 10.4), with pytest's monkeypatch."""
    monkeypatch.setattr(plant, "date", _frozen_date_class(day))
    monkeypatch.setattr(feeding, "local_today", lambda: day)


@contextlib.contextmanager
def frozen_day(day: date = F22_DATE) -> Iterator[None]:
    """The same as freeze(), for code without pytest (scripts/hank_eval.py); restored on exit."""
    saved = plant.date, feeding.local_today
    plant.date = _frozen_date_class(day)  # type: ignore[misc]
    feeding.local_today = lambda: day
    try:
        yield
    finally:
        plant.date, feeding.local_today = saved  # type: ignore[misc]


def snapshot(sensors_age: int = FRESH_SECONDS) -> dict[str, Any]:
    """F22 in the shape of box.snapshot(). Every key the real one has is present."""
    server_time = (F22_NOW - F22_UTC_OFFSET).replace(tzinfo=timezone.utc).isoformat()
    return {
        "device": None,  # CONSTRUCTED: the spec names no device name
        "online": True,
        "mqtt": True,
        "shadow_age": sensors_age,  # CONSTRUCTED: the spec gives one age; the shadow is as old as the readings
        "sensors_age": sensors_age,
        "climate": {
            "temp": None, "humidity": BOX_HUMIDITY, "vpd": None,
            "probe_temp": None, "probe_humidity": PROBE_HUMIDITY, "probe_vpd": None,
            "water_level": WATER_LEVEL, "water_raw": None, "water_temp": None,
            "room_temp": None, "room_humidity": None,
        },
        "aux_device": None,
        "light": {"level": LIGHT_PERCENT, "mode": 0, "in_plan": False, "spectrum": "bloom", "spectrum_writable": True},
        # CONSTRUCTED: no circulation fan step in the spec; None reads as "no value"
        "cfan": {"step": None, "natural_wind": False, "nw_writable": False, "mode": 0},
        "dfan": {"step": DFAN_STEP, "mode": 0},
        "ports": {
            # the drip pump since 23.10.2026 (ports.json): off, it runs only by hand (the camera's power was on here)
            "usb1": {"on": False, "mode": 0, "in_plan": False, "accessory": None, "can_write": True},
            "usb2": {"on": False, "mode": 0, "in_plan": False, "accessory": None, "can_write": True},
        },
        "plan": None,
        "pending": {},
        "error": None,
        "server_time": server_time,
    }


def camera_status() -> dict[str, Any]:
    """The camera.status() shape. CONSTRUCTED: the spec only says the camera's power was on (USB 1 on 22.10.2026;
    a power supply of its own since 23.10.2026, which it needs), so it is reachable."""
    return {"configured": True, "searching": False, "name": None, "ip": None, "error": None, "reachable": True,
            "offline_seconds": None, "recording": None, "max_minutes": 120}


def ledger_live06(now: datetime = F22_NOW) -> list[dict[str, Any]]:
    """live-06: Hank recommended "exhaust fan up" earlier; the readings show it was not done (probe still 79 %).
    The shape is spec 6.1. CONSTRUCTED: the entry is 45 minutes old, past the 30-minute grace (spec 6.2 start value),
    and still "open", so the ledger itself has to find out that it was not applied."""
    created = (now - timedelta(minutes=45)).isoformat(timespec="seconds")
    return [{
        "id": "r-0001", "created": created, "answer_id": "a-0001",
        "stage_key": "early_flower", "metric": "probe_humidity", "status": "high",
        "baseline": {"value": PROBE_HUMIDITY, "dfan": DFAN_STEP, "usb2_on": False},
        "recommendation": {"card": "stages/autoflower#early_flower.probe_humidity.all", "kind": "dfan_up",
                           "proposal": {"action": "dfan", "step": DFAN_STEP + 1}},
        "state": "open",
        "applied_at": None, "declined_at": None,
        "consequence_told": False, "told": {"at": None, "deviation": None},
        "worst": {"value": PROBE_HUMIDITY, "at": created},
        "history": [{"t": created, "event": "created"}],
    }]


class _Box:
    """Just enough box for HumidifierAutomation.view(): it hands out the fixture's snapshot, it never switches."""

    def __init__(self, snap: dict[str, Any]) -> None:
        self._snap = snap

    def snapshot(self) -> dict[str, Any]:
        return dict(self._snap)


def _write_automation(now: datetime) -> None:
    """The automation as F22 has it: enabled, paused for the unconfirmed stage (the humidifier was already off, so the
    pause switched nothing). State keys and log lines in automation.py's own format. CONSTRUCTED: switched on three
    hours before the fixture's time, the pause five minutes later."""
    state = {"enabled": True, "stage_pause_done": True, "stage_pause_pushed": True}
    config.atomic_write(config.STATE_DIR / "automation-state.json", json.dumps(state, indent=2) + "\n")
    on = now - timedelta(hours=3)
    lines = [
        {"time": on.isoformat(timespec="seconds"), "action": "automation_on"},
        {"time": (on + timedelta(minutes=5)).isoformat(timespec="seconds"), "action": "paused_unconfirmed_stage",
         "value": None, "switched_off": False, "reason": "unconfirmed_stage", "stage": "early_flower",
         "set_by": "estimate"},
    ]
    with (config.STATE_DIR / "automation-log.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


@dataclass
class Live:
    """One live state, as the live collector (wiring.py, spec 4.2) reads it, plus the ledger for live-06."""

    name: str
    lang: str
    now: datetime
    snapshot: dict[str, Any]
    profile: dict[str, Any] | None
    stage: dict[str, Any] | None
    checks: list[dict[str, Any]]
    plant_view: dict[str, Any]
    automation_view: dict[str, Any]
    alerts_view: list[dict[str, Any]]
    feeding_view: dict[str, Any]
    camera: dict[str, Any]
    vision: dict[str, Any]
    ports: dict[str, dict[str, object]] | None
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def check(self, metric: str) -> dict[str, Any] | None:
        """The first plant.evaluate() row for a metric."""
        return next((row for row in self.checks if row["metric"] == metric), None)

    def sources(self) -> dict[str, Any]:
        """The raw objects, by the names spec 4.2 uses: what the eval bridge hands to the pipeline."""
        return {"snapshot": self.snapshot, "camera": self.camera, "plant_view": self.plant_view,
                "automation_view": self.automation_view, "alerts_view": self.alerts_view,
                "feeding_view": self.feeding_view, "vision": self.vision, "ports": self.ports,
                "ledger": self.ledger, "now": self.now.isoformat(timespec="seconds")}


def build(name: str = "F22", *, lang: str = "de", ledger: str | None = None) -> Live:
    """F22, F22-alt, F22-veg or F22-nocannabis. Call with an isolated config.STATE_DIR and the day frozen. ledger
    "live-06" adds its entry."""
    if name not in NAMES:
        raise ValueError(f"unknown fixture {name!r}, known: {', '.join(NAMES)}")
    if ledger not in (None, "live-06"):
        raise ValueError(f"unknown ledger {ledger!r}")
    _packages(name)
    if name == "F22-veg":
        return _veg(build("F22", lang=lang, ledger=ledger))
    if name == "F22-vpdhigh":
        return _vpd_high(build("F22", lang=lang, ledger=ledger), lang)
    if name == "F22-thirst":
        return _thirst(build("F22", lang=lang, ledger=ledger))
    now = F22_NOW
    snap = snapshot(STALE_SECONDS if name == "F22-alt" else FRESH_SECONDS)
    log = PlantLog()
    if not log.data["heights"]:
        log.add_height(HEIGHT_CM, F22_DATE.isoformat())
    _write_automation(now)
    profile = load_profile()
    setups = feeding.setups_of(profile)
    kept = json.loads(json.dumps(log.data))  # F22-vivosun and F22-nochoice put the log back after building
    if len(setups) > 1 and name != "F22-nochoice":
        # 22.10.2026 the owner fed BioBizz; since 23.10. plant.json holds two fertilizers and one must be chosen.
        chosen = "vivosun" if name == "F22-vivosun" else "biobizz"
        if (log.data.get("feeding_choice") or {}).get("setup") != chosen:
            log.set_feeding_choice(chosen, setups)
    elif name == "F22-nochoice":
        log.data.pop("feeding_choice", None)
    if name == "F22-vivosun":
        log.set_stage(VIVOSUN_STAGE[0], since=VIVOSUN_STAGE[1])
        _confirm(feeding.setup_of(profile, log))
    stage = current_stage(profile or {}, log)
    automation = HumidifierAutomation(_Box(snap), None, log)
    live = Live(
        name=name, lang=lang, now=now, snapshot=snap, profile=profile, stage=stage,
        checks=evaluate(stage, snap),
        plant_view=plant.view(profile, log, snap, lang),
        automation_view=automation.view(snap),
        alerts_view=Alerts(None).view(stage),
        feeding_view=feeding.view(feeding.setup_of(profile, log), lang, today=F22_DATE,
                                  amount_units=feeding.PAGE_UNITS, **feeding.context(profile, log, lang)),
        camera=camera_status(),
        vision={"busy": False},
        ports=config.port_rules(),
        ledger=ledger_live06(now) if ledger == "live-06" else [],
    )
    if name in ("F22-vivosun", "F22-nochoice"):
        log.data = kept
        log._save()
    return live


def _confirm(setup: dict[str, Any] | None) -> None:
    """The owner compared the chart with the bottles (two taps on the page): the card's own fingerprint."""
    view = feeding.view(setup, "de", today=F22_DATE, amount_units=feeding.PAGE_UNITS)
    if not view.get("confirmed") and view.get("card"):
        feeding.confirm(setup, view["card"]["id"], view["fingerprint"])


def _veg(live: Live) -> Live:
    """F22 with the owner's stage "veg" in the plant view (what Hank reads the stage from), label from plant.json."""
    stage = dict(live.plant_view.get("stage") or {})
    labels = {s.get("key"): s.get("label") for s in live.plant_view.get("stages") or [] if isinstance(s, dict)}
    stage.update(VEG_STAGE, label=labels.get("veg") or "veg")
    live.plant_view = {**live.plant_view, "stage": stage}
    live.name = "F22-veg"
    return live


def _vpd_high(live: Live, lang: str) -> Live:
    """F22 with the measured VPD-high climate and the light at 75 %, every view built again from that snapshot, and the
    autopilot's brief (the real Autopilot, read only: off, its default) under "autopilot" as server.HankBox adds it."""
    from vgrow.autopilot import Autopilot  # the test side may read the regulator; Hank's package never imports it

    snap = json.loads(json.dumps(live.snapshot))
    snap["climate"].update(VPD_HIGH_CLIMATE)
    snap["light"].update(level=VPD_HIGH_LIGHT, mode=0, in_plan=False, in_plan_known=True)
    log = PlantLog()
    profile = load_profile()
    stage = current_stage(profile or {}, log)
    snap["autopilot"] = {"boxes": [Autopilot(_Box(snap), plant_log=log).brief(snap)]}
    live.snapshot = snap
    live.stage = stage
    live.checks = evaluate(stage, snap)
    live.plant_view = plant.view(profile, log, snap, lang)
    live.automation_view = HumidifierAutomation(_Box(snap), None, log).view(snap)
    live.name = "F22-vpdhigh"
    return live


def _thirst(live: Live) -> Live:
    """F22 with the Durst-Index's brief under "thirst", as server.HankBox adds it (the real ledger, read only, at F22's
    hour): the owner's pot weighed full long enough ago that the modelled index stands at THIRST_TI, nothing checked
    since. Its files go into the isolated state folder like the plant log."""
    from vgrow import thirst  # the test side may read the ledger; Hank's package never imports it
    from vgrow.demo import DEMO_FULL_KG, DEMO_POT

    now = (F22_NOW - F22_UTC_OFFSET).replace(tzinfo=timezone.utc).timestamp()
    snap = json.loads(json.dumps(live.snapshot))
    ledger = thirst.Thirst(_Box(snap), plant_log=PlantLog(), clock=lambda: now, demo=True)
    model = thirst.load_model()
    level = float(snap["light"]["level"])
    rate = thirst.et_rate_ml_h(model, light=level, vpd=snap["climate"].get("probe_vpd"), height_cm=HEIGHT_CM,
                               last_lit=level) / 1000 / 3600
    d_trig = float(model("f_trig")) * float(model("a_w")) * float(DEMO_POT["volume_l"])
    full = now - THIRST_TI / 100 * d_trig / rate
    pot = thirst.Pot(**DEMO_POT, set_at=full - 3600, id="pot-eval")
    config.atomic_write(ledger.pot_path(), json.dumps(pot.as_dict()) + "\n")
    ledger.log({"kind": "pot", "pot": pot.id, "litres": pot.volume()}, at=pot.set_at)
    ledger.log({"kind": "weigh", "kg": DEMO_FULL_KG, "full": True}, at=full)
    ledger.series = thirst.constant_series(rate, pot.set_at, now)
    ledger.state["inputs"] = {"at": now, "rate_l_s": rate, "light": level, "last_lit": level}
    ledger.save()
    snap["thirst"] = ledger.brief(snap)
    live.snapshot = snap
    live.name = "F22-thirst"
    return live


def _packages(name: str) -> None:
    """F22-nocannabis switches the cannabis package off in settings.json; another fixture removes that switch again,
    and only that one (PACKAGES_MARK): a switch a test wrote itself stays."""
    path = config.STATE_DIR / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    if name == "F22-nocannabis":
        data.update({"packages": [], PACKAGES_MARK: True})
    elif data.pop(PACKAGES_MARK, None):
        data.pop("packages", None)
    else:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
