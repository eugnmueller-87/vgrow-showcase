"""VPD too high (23.10.2026): the humidifier rule's effective floor, the autopilot's "VPD too high" vote, and the
measured case as a replay.

Measured live on 23.10.2026 at 21:20: probe at leaf height 25.06 °C, 45.5 %, VPD 1.73 kPa (the box's own sensor 22.6 °C,
43.9 %, 1.53 kPa), lights on at 75 % without a light plan, stage early flower. The early flower card says humidity 45 to
60 % and VPD 1.0 to 1.4 kPa; above about 22 °C the two bands contradict each other, and nothing reacted.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

import pytest

from vgrow import automation as am
from vgrow import autopilot as ap
from vgrow import config
from vgrow.automation import HumidifierAutomation, humidity_floor
from vgrow.budget import BudgetExceeded, CommandBudget
from vgrow.plant import PlantLog

from test_autopilot import POLL, FakeBox, Rig, arun, stage

REPO = Path(__file__).resolve().parent.parent
CARD = json.loads((REPO / "kartei" / "stages" / "autoflower.json").read_text(encoding="utf-8"))
PROFILE = {"plant": {"start_date": "2026-08-31"}, "stages": CARD["stages"]}
EARLY = stage("early_flower")  # humidity 45 to 60 %, VPD 1.0 to 1.4 kPa, day temperature 20 to 26 °C
MEASURED = {"probe_temp": 25.06, "probe_humidity": 45.5, "probe_vpd": 1.73, "temp": 22.6, "humidity": 43.9,
            "vpd": 1.53}


@pytest.fixture(autouse=True)
def no_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    """As in test_autopilot: plain writes instead of the atomic write for the simulated polls."""
    monkeypatch.setattr(config.os, "fsync", lambda fd: None)

    def plain_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(config, "atomic_write", plain_write)


def es(temp: float) -> float:
    """Tetens, written out here on its own: the test does not trust the code's formula."""
    return 0.6108 * math.exp(17.27 * temp / (temp + 237.3))


def snap(temp: float | None = 25.06, humidity: float = 45.5, *, light: int | None = 75, age: float | None = 60,
         vpd: float | None = None) -> dict[str, Any]:
    climate = {"probe_temp": temp, "probe_humidity": humidity, "humidity": humidity - 2,
               "probe_vpd": vpd if vpd is not None else (None if temp is None else
                                                          round(es(temp) * (1 - humidity / 100), 2))}
    return {"climate": climate, "light": {"level": light}, "sensors_age": age,
            "ports": {"usb2": {"on": False, "can_write": True}}}


# ---------- the effective floor ----------

def test_the_floor_at_25_degrees_is_the_humidity_where_the_vpd_meets_its_maximum() -> None:
    floor = humidity_floor(EARLY, snap(25.06))
    expected = round(100 * (1 - 1.4 / es(25.06)), 1)
    assert 55.9 <= expected <= 56.0  # the measured case: about 56 % instead of the card's 45 %
    assert floor["floor"] == expected and floor["reason"] == "vpd"
    assert floor["temp"] == 25.06 and floor["vpd_max"] == 1.4 and floor["card_min"] == 45 and not floor["clamped"]
    # and at that humidity the air VPD is the maximum again
    assert round(es(25.06) * (1 - floor["floor"] / 100), 2) == 1.4


def test_the_floor_at_21_degrees_is_the_card_minimum() -> None:
    assert 100 * (1 - 1.4 / es(21.0)) < 45  # 43.7 %: the card's own minimum is the higher one
    floor = humidity_floor(EARLY, snap(21.0))
    assert floor["floor"] == 45 and floor["reason"] == "card"


def test_the_floor_at_27_degrees_is_cut_to_the_maximum_minus_three() -> None:
    assert 100 * (1 - 1.4 / es(27.0)) > 57  # 60.7 %: above the band's top
    floor = humidity_floor(EARLY, snap(27.0))
    assert floor["floor"] == 60 - am.FLOOR_HEADROOM == 57 and floor["reason"] == "vpd" and floor["clamped"]


@pytest.mark.parametrize("change", [
    {"light": 0},  # lights off
    {"light": None},  # lights unknown
    {"temp": None},  # no probe temperature
    {"age": 900},  # readings too old
    {"age": None},  # no readings at all
])
def test_without_lights_on_and_a_fresh_probe_temperature_the_card_minimum_holds(change: dict[str, Any]) -> None:
    kw: dict[str, Any] = {"temp": 25.06, "light": 75, "age": 60, **change}
    floor = humidity_floor(EARLY, snap(kw.pop("temp"), **kw))
    assert floor["floor"] == 45 and floor["reason"] == "card"


def test_without_a_vpd_band_or_with_a_narrow_band_the_card_minimum_holds() -> None:
    no_vpd = {"key": "x", "set_by": "manual", "targets": [{"metric": "probe_humidity", "min": 45, "max": 60}]}
    assert humidity_floor(no_vpd, snap(25.06))["floor"] == 45
    narrow = {"key": "x", "set_by": "manual", "targets": [{"metric": "probe_humidity", "min": 45, "max": 47},
                                                          {"metric": "probe_vpd", "min": 1.0, "max": 1.4}]}
    assert humidity_floor(narrow, snap(25.06))["floor"] == 45  # never below the minimum, even when max - 3 is
    assert humidity_floor(None, snap(25.06))["floor"] is None


# ---------- the humidifier rule ----------

def rule(monkeypatch: pytest.MonkeyPatch, *, owner_stage: bool = True, named: bool = True,
         clock: list[float] | None = None) -> tuple[FakeBox, HumidifierAutomation, list[float]]:
    """The humidifier rule on USB 2 of a FakeBox with the real stage card, the owner's stage early flower."""
    clock = clock or [1_792_700_000.0]
    # a state folder of its own per rule, so one case's port assignment never leaks into the next
    rule.n = getattr(rule, "n", 0) + 1  # type: ignore[attr-defined]
    monkeypatch.setattr(config, "STATE_DIR", config.STATE_DIR / f"rule-{rule.n}")  # type: ignore[attr-defined]
    monkeypatch.setattr(am, "load_profile", lambda: PROFILE)
    monkeypatch.setattr(am, "CONFIRM_SECONDS", 0)
    monkeypatch.setattr(am.time, "time", lambda: clock[0])
    box = FakeBox()
    box.light["level"] = 75
    box.climate.update(MEASURED)
    log = PlantLog()
    if owner_stage:
        log.set_stage("early_flower")
    else:
        PROFILE_EST = {**PROFILE, "stage_estimate": {"key": "early_flower", "since": "2026-10-22"}}
        monkeypatch.setattr(am, "load_profile", lambda: PROFILE_EST)
    hum = HumidifierAutomation(box, None, log, budget=CommandBudget(clock=lambda: clock[0]))
    if named:
        assert hum.guard.assign("usb2", "humidifier", box.snapshot())
    hum.set_enabled(True)
    return box, hum, clock


def lines(hum: HumidifierAutomation) -> list[dict[str, Any]]:
    return [json.loads(x) for x in hum.log_path.read_text(encoding="utf-8").splitlines()]


def test_the_measured_case_switches_the_humidifier_on_because_the_vpd_is_too_high(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, hum, _ = rule(monkeypatch)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"  # 45.5 % is above the card's 45 %
    assert box.ports["usb2"]["on"] is True
    on = lines(hum)[-1]
    floor = round(100 * (1 - 1.4 / es(25.06)), 1)
    assert on["action"] == "humidifier_on" and on["reason"] == "vpd_high"
    assert on["floor"] == floor and on["temp"] == 25.06 and on["value"] == 45.5 and on["vpd"] == 1.73
    assert hum.state["last_action"]["reason"] == "vpd_high"
    view = hum.view(box.snapshot())
    assert view["recent"][0]["reason"] == "vpd_high" and view["recent"][0]["floor"] == floor
    assert view["recent"][0]["temp"] == 25.06


def test_below_the_card_minimum_the_reason_is_too_dry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, hum, _ = rule(monkeypatch)
    box.climate.update(probe_humidity=43.0)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    assert lines(hum)[-1]["reason"] == "too_dry"


def test_between_the_floor_and_the_maximum_the_state_holds_and_off_reaches_the_maximum(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, hum, clock = rule(monkeypatch)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    for rh in (52.0, 57.0, 59.9):  # below the card's maximum: it keeps running
        clock[0] += 20 * 60
        box.climate.update(probe_humidity=rh)
        assert asyncio.run(hum.check(box.snapshot())) is None, rh
    assert box.ports["usb2"]["on"] is True
    clock[0] += 20 * 60
    box.climate.update(probe_humidity=60.0)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_off"  # exactly at the target, without overshoot
    clock[0] += 20 * 60
    box.climate.update(probe_humidity=57.0)  # off now: above the floor, so it stays off
    assert asyncio.run(hum.check(box.snapshot())) is None and box.ports["usb2"]["on"] is False


@pytest.mark.parametrize("temp,light", [(25.06, 75), (27.0, 75), (21.0, 75), (25.06, 0)])
def test_full_cycle_stops_on_the_next_poll_and_restarts_only_after_hysteresis_and_rest(
    monkeypatch: pytest.MonkeyPatch, temp: float, light: int,
) -> None:
    box, hum, clock = rule(monkeypatch)
    box.ports["usb1"]["on"] = False
    box.light["level"] = light
    box.climate.update(probe_temp=temp, probe_humidity=40.0)
    floor = hum.view(box.snapshot())["floor"]["floor"]
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    clock[0] += 90  # target reached well before the old 15-minute switch timer
    box.climate.update(probe_humidity=60.0)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_off"
    off_at = clock[0]
    assert box.ports["usb2"]["on"] is False
    box.climate.update(probe_humidity=floor - 0.1)
    clock[0] = off_at + am.MIN_SWITCH_SECONDS - 1
    assert asyncio.run(hum.check(box.snapshot())) is None  # dry again, but too soon to restart
    # A process restart must preserve the rest, rather than starting another on/off cycle.
    hum = HumidifierAutomation(box, None, hum.plant_log, budget=hum.budget)
    assert asyncio.run(hum.check(box.snapshot())) is None
    clock[0] = off_at + am.MIN_SWITCH_SECONDS
    box.climate.update(probe_humidity=floor)
    assert asyncio.run(hum.check(box.snapshot())) is None  # equality at the lower boundary holds off
    box.climate.update(probe_humidity=59.9)
    assert asyncio.run(hum.check(box.snapshot())) is None  # dipping just below the target does not restart it
    box.climate.update(probe_humidity=floor - 0.1)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    assert box.sent == [("port.usb2", True), ("port.usb2", False), ("port.usb2", True)]
    assert box.ports["usb1"]["on"] is False


@pytest.mark.parametrize("gate", ["manual", "app", "stale", "pump", "disabled"])
def test_target_off_still_respects_owner_and_device_guards(monkeypatch: pytest.MonkeyPatch, gate: str) -> None:
    box, hum, clock = rule(monkeypatch)
    box.ports["usb2"]["on"] = True
    box.climate.update(probe_humidity=60.0)
    hum.state["last_switch"] = clock[0] - 90
    if gate == "manual":
        hum.manual_switch()
    elif gate == "app":
        box.ports["usb2"]["can_write"] = False
    elif gate == "stale":
        box.sensors_age = am.STALE_SECONDS + 1  # not yet old enough for the independent fail-safe
    elif gate == "pump":
        hum.guard.assign("usb2", "pump", box.snapshot())
    else:
        hum.set_enabled(False)
    asyncio.run(hum.check(box.snapshot()))
    assert box.ports["usb2"]["on"] is True and box.sent == []


def test_an_unconfirmed_target_off_is_retried_instead_of_waiting_fifteen_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box, hum, clock = rule(monkeypatch)
    box.ports["usb2"]["on"] = True
    box.climate.update(probe_humidity=60.0)
    box.silent = True
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_off"
    assert box.ports["usb2"]["on"] is True  # the command alone is not a report
    clock[0] += 90
    box.silent = False
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_off"
    assert box.ports["usb2"]["on"] is False
    assert box.sent == [("port.usb2", False), ("port.usb2", False)]


def test_lights_off_brings_back_the_card_minimum(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, hum, _ = rule(monkeypatch)
    box.light["level"] = 0
    assert asyncio.run(hum.check(box.snapshot())) is None and box.ports["usb2"]["on"] is False


def test_every_gate_still_holds_the_vpd_on(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # an estimated stage: never on
    box, hum, _ = rule(monkeypatch, owner_stage=False)
    assert asyncio.run(hum.check(box.snapshot())) == "paused_unconfirmed_stage" and box.ports["usb2"]["on"] is False
    # the owner has not named the humidifier on USB 2: never on
    box, hum, _ = rule(monkeypatch, named=False)
    assert asyncio.run(hum.check(box.snapshot())) == "paused_port_unassigned" and box.ports["usb2"]["on"] is False
    # the rule off: nothing
    box, hum, _ = rule(monkeypatch)
    hum.set_enabled(False)
    assert asyncio.run(hum.check(box.snapshot())) is None and box.ports["usb2"]["on"] is False
    # stale readings: never on
    box, hum, _ = rule(monkeypatch)
    box.sensors_age = 900
    assert asyncio.run(hum.check(box.snapshot())) is None and box.ports["usb2"]["on"] is False
    # the manual pause
    box, hum, clock = rule(monkeypatch)
    hum.manual_switch()
    assert asyncio.run(hum.check(box.snapshot())) is None and box.ports["usb2"]["on"] is False
    clock[0] += am.MANUAL_PAUSE_SECONDS + 1
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    # restarting still waits at least 15 minutes after the last automatic switch
    box, hum, clock = rule(monkeypatch)
    hum.state["last_switch"] = clock[0] - 5 * 60
    assert asyncio.run(hum.check(box.snapshot())) is None
    clock[0] += 11 * 60
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"


def test_an_adapter_backoff_holds_an_on_but_never_an_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, hum, clock = rule(monkeypatch)

    async def full(*args: Any, safety: bool = False, **kw: Any) -> None:
        if not safety:
            raise BudgetExceeded("auto", clock[0] + 600)
        await args[2]()

    monkeypatch.setattr(hum.budget, "send", full)
    assert asyncio.run(hum.check(box.snapshot())) == "budget_wait" and box.ports["usb2"]["on"] is False
    box.ports["usb2"]["on"] = True
    box.climate.update(probe_humidity=61.0)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_off"  # the off is a safety command


def test_full_command_history_does_not_block_humidifier_cycles(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, hum, clock = rule(monkeypatch)
    for _ in range(30):
        hum.budget.record("autopilot", "dfan")
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    assert box.ports["usb2"]["on"] is True
    box.climate.update(probe_humidity=61.0)
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_off"
    box.climate.update(probe_humidity=45.5)
    assert asyncio.run(hum.check(box.snapshot())) is None  # the existing restart timer still applies
    clock[0] += am.MIN_SWITCH_SECONDS + 1
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    assert hum.budget.counts()["auto_day"] == 32


def test_the_view_shows_the_floor_its_temperature_and_why(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, hum, _ = rule(monkeypatch)
    view = hum.view(box.snapshot())
    assert view["floor"]["reason"] == "vpd" and view["floor"]["temp"] == 25.06 and view["need"] == "vpd_high"
    assert view["vpd"] == {"value": 1.73, "band": [1.0, 1.4], "high": True}
    box.climate.update(probe_humidity=40.0)
    assert hum.view(box.snapshot())["need"] == "too_dry"
    box.climate.update(probe_humidity=58.0)
    assert hum.view(box.snapshot())["need"] is None
    box.sensors_age = 900
    view = hum.view(box.snapshot())
    assert view["need"] is None and view["floor"]["reason"] == "card" and view["vpd"]["value"] is None


# ---------- the autopilot's "VPD too high" vote ----------

def vpd_rig(temp: float = 24.0, humidity: float = 45.5, *, st: dict[str, Any] | None = None, mode: str = "auto",
            vpd: float | None = None) -> Rig:
    rig = Rig(st=st or stage("early_flower"), mode=mode)
    rig.box.light["level"] = 75
    rig.box.set_climate(temp, humidity, box_humidity=humidity - 2)
    if vpd is not None:
        rig.box.climate["probe_vpd"] = vpd
    return rig


def dfan_holds(rig: Rig) -> str:
    return (rig.pilot.holds.get("dfan") or {}).get("key", "")


def test_vpd_too_high_steps_the_exhaust_down_after_the_hold() -> None:
    rig = vpd_rig(24.0)  # 1.63 kPa, 2 °C below the day maximum
    rig.run(8)
    assert rig.box.sent == []  # held like every other vote: ten minutes first
    rig.run(10)
    assert rig.box.sent == [("dfan", 4)]
    step = rig.kinds("step")[-1]
    assert step["text"]["params"]["reason"]["key"] == "ap.reason.vpd_high"


def test_vpd_too_high_votes_only_with_the_lights_on_and_no_mist() -> None:
    rig = vpd_rig(21.0, 30.0)  # 1.74 kPa at night: the night band 18 to 22 °C holds, no VPD vote
    rig.box.light["level"] = 0
    rig.run(60)
    assert rig.box.sent == []
    rig = vpd_rig(24.0)
    rig.box.climate["humidity"] = 20.0  # the probe 25 points above the box sensor: mist
    rig.run(60)
    assert rig.box.sent == []


def test_vpd_too_high_waits_for_a_degree_of_room_below_the_day_maximum() -> None:
    rig = vpd_rig(25.06)  # 0.94 °C below 26: less exhaust would make it warmer
    rig.run(60)
    assert rig.box.sent == []
    assert dfan_holds(rig) == "ap.hold.vpd_no_room"
    assert "25" in ap.render(rig.pilot.holds["dfan"], "de")


def test_vpd_too_high_does_not_vote_with_the_lights_unknown_or_without_the_box_humidity() -> None:
    rig = vpd_rig(24.0)
    rig.box.light["level"] = None  # no light reading: the lights are unknown
    rig.run(60)
    assert rig.box.sent == []
    rig = vpd_rig(24.0)
    rig.box.climate["humidity"] = None  # no box sensor: the mist lock cannot be checked, so it holds
    rig.run(60)
    assert rig.box.sent == []
    assert dfan_holds(rig) == "ap.hold.box_humidity_missing"


def test_vpd_too_high_has_the_lowest_rank() -> None:
    """Too cool and too dry: both want one step down, the temperature (rank 3) names the step, not the VPD (rank 4)."""
    rig = vpd_rig(19.0, 20.0)  # below 20 - 0.5, and 1.76 kPa
    rig.run(20)
    assert rig.box.sent == [("dfan", 4)]
    assert rig.kinds("step")[-1]["text"]["params"]["reason"]["key"] == "ap.reason.temp_low"


def test_vpd_too_high_waits_after_a_too_warm_vote() -> None:
    """Too warm steps the exhaust up; when it cools down but stays dry, less exhaust waits COLD_HUMID_WINDOW after the
    last too warm vote, so the two do not flip the exhaust at the day maximum (review 23.10.2026, S2)."""
    rig = vpd_rig(27.0, 50.0)  # above 26 + 0.5: too warm, rank 3
    rig.run(30)
    assert rig.box.sent == [("dfan", 6)]
    rig.box.set_climate(24.0, 40.0, box_humidity=38.0)  # 1.79 kPa, 2 °C below the day maximum
    rig.run(60)
    assert rig.box.sent == [("dfan", 6)]
    assert dfan_holds(rig) == "ap.hold.vpd_heat"
    assert ap.render(rig.pilot.holds["dfan"], "en").startswith("holding: VPD")
    rig.run(3 * 60)
    assert rig.box.sent[1] == ("dfan", 5)  # after the wait, the VPD steps down again
    down = next(e for e in rig.kinds("step") if e["to"] == 5)
    assert down["text"]["params"]["reason"]["key"] == "ap.reason.vpd_high"
    assert down["at"] - rig.pilot.state["cold_humid"]["temp_high_at"] >= ap.COLD_HUMID_WINDOW


def test_vpd_too_high_without_a_temperature_band_says_so() -> None:
    st = json.loads(json.dumps(stage("early_flower")))
    st["targets"] = [t for t in st["targets"] if t["metric"] != "probe_temp"]
    rig = vpd_rig(24.0, st=st)
    rig.run(60)
    assert rig.box.sent == []
    assert dfan_holds(rig) == "ap.hold.vpd_no_temp"


def test_vpd_too_high_never_steps_down_into_humid_air() -> None:
    rig = vpd_rig(24.0, 62.0, vpd=1.6)  # the humidity above its band: "too humid" wins, never a step down
    rig.run(60)
    assert ("dfan", 4) not in rig.box.sent


def test_vpd_too_high_never_goes_below_the_flower_minimum() -> None:
    rig = vpd_rig(24.0)
    rig.box.dfan["step"] = 4
    rig.pilot.state["base"] = 4  # flower: never below 4
    rig.run(60)
    assert rig.box.sent == []
    assert dfan_holds(rig) == "ap.hold.at_bound"


def test_vpd_too_high_counts_in_the_exhaust_does_not_work_here() -> None:
    """Veg (VPD 0.8 to 1.2, bounds 2..8): two steps down and the VPD does not move, so the exhaust goes back to its
    base step and the VPD rule rests."""
    rig = vpd_rig(24.0, 50.0, st=stage("veg"))  # 1.49 kPa, the day band 22 to 28 °C
    rig.run(4 * 60)
    steps = [(e["from"], e["to"]) for e in rig.kinds("step")]
    assert steps[:2] == [(5, 4), (4, 3)]
    assert (3, 5) in steps  # the way back to the base step
    assert rig.pilot.state["rest_until"].get("probe_vpd", 0) > rig.clock[0]


def test_settled_needs_the_vpd_inside_its_band_by_the_inner_margin() -> None:
    """No way back to the base step while the VPD is above its maximum minus the inner margin (1.4 - 0.15)."""
    for humidity, back in ((48.0, False), (53.0, True)):  # 21.6 °C: 1.34 kPa (no vote, not settled), 1.21 kPa
        rig = vpd_rig(21.6, humidity)
        rig.box.dfan["step"] = 4  # the base stays 5
        rig.run(3 * 60)
        assert (("dfan", 5) in rig.box.sent) is back, humidity


def test_observe_logs_the_vpd_step_and_sends_nothing() -> None:
    rig = vpd_rig(24.0, mode="observe")
    rig.run(30)
    assert rig.box.sent == []
    assert any(e["to"] == 4 for e in rig.kinds("would"))


# ---------- the measured case, replayed ----------

def test_replay_of_the_measured_case(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """25.06 °C, 45.5 %, 1.73 kPa, lights on at 75 %, the owner's stage early flower, autopilot and humidifier rule on:
    the humidifier rule switches on because the VPD is too high, the light is held, and the exhaust waits for a degree
    of room (25.06 °C is 0.94 °C below the day maximum of 26 °C). The same air one tenth of a degree cooler than
    25.0 °C: the exhaust goes one step down as well."""
    rig = Rig(st=stage("early_flower"), lamp=True)
    rig.box.light["level"] = 75
    rig.box.climate.update(MEASURED)
    _, hum, _ = rule(monkeypatch, clock=rig.clock)
    hum.box = rig.box
    assert hum.guard.assign("usb2", "humidifier", rig.box.snapshot())
    did = []
    for _ in range(int(15 * 60 // POLL)):  # the toy box does not get wetter: no plausibility stop in the picture
        rig.poll()
        did.append(arun(hum.check(rig.box.snapshot())))
    assert did.count("humidifier_on") == 1 and rig.box.ports["usb2"]["on"] is True
    assert lines(hum)[-1]["reason"] == "vpd_high"
    rig.run(105)
    assert ("dfan", 4) not in rig.box.sent and dfan_holds(rig) == "ap.hold.vpd_no_room"
    assert not any(name == "light" for name, _ in rig.box.sent)
    assert (rig.pilot.holds.get("light") or {}).get("key") == "ap.hold.too_warm"
    rig.box.climate.update(probe_temp=24.9)
    rig.run(20)
    assert ("dfan", 4) in rig.box.sent
    assert not any(name == "light" for name, _ in rig.box.sent)


# ---------- the demo's VPD-high climate (for screenshots) ----------

def test_the_demo_can_show_the_measured_vpd_high_case(monkeypatch: pytest.MonkeyPatch) -> None:
    from vgrow import demo

    monkeypatch.setenv(demo.DEMO_CLIMATE_ENV, "vpd_high")
    box = demo.DemoBox()
    climate = box.snapshot()["climate"]
    assert {k: climate[k] for k in MEASURED} == MEASURED and box.ports["usb2"] is False
    monkeypatch.delenv(demo.DEMO_CLIMATE_ENV)
    assert demo.DemoBox().snapshot()["climate"]["probe_vpd"] < 1.4  # unset: the made-up day inside the band
