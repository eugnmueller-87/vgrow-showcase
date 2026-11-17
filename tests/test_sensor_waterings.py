"""Waterings the soil sensor saw (Thirst.sensor_waterings, th.sensor_rises): water nobody entered reaches the
Durst-Index as water of unknown amount. On 15.11.2026 the card still counted from a pump run of 06.11. because the
waterings by hand of 07.11., 12.11. and 15.11. never reached it, while the sensor's log showed every one of them."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from vgrow import thirst as th

BERLIN = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 11, 15, 6, 0, tzinfo=BERLIN).timestamp()
MIN, HOUR, DAY = 60, 3600, 86400
STAGE = {"key": "veg", "label": "Veg", "since": "2026-11-01", "set_by": "manual"}
MODEL = th.load_model()


def drying(start: float, hours: float, percent: float, per_hour: float = 0.5) -> list[dict[str, Any]]:
    """A reading every 15 minutes (soil.py logs at least that often) while the pot dries."""
    return [{"at": start + i * 15 * MIN, "percent": round(percent - per_hour * i / 4)} for i in range(int(hours * 4))]


def watering(at: float, low: float, high: float, drained: float) -> list[dict[str, Any]]:
    """The owner's watering of 15.11.2026 in shape: two readings up, then the drain-down over two hours."""
    rows = [{"at": at, "percent": (low + high) / 2 + 5}, {"at": at + MIN, "percent": high}]
    steps = 8
    rows += [{"at": at + MIN + (i + 1) * 15 * MIN, "percent": round(high - (high - drained) * (i + 1) / steps)}
             for i in range(steps)]
    return rows


# The sensor's log on 15.11.2026 in shape: 34 % in the morning, water at 09:32 (34 to 66, drained 47), drying to 43,
# then the owner moved the blade at 17:44 and it read 35.
WATER_AT = T0 + 3 * HOUR + 32 * MIN
DAY_ROWS = (drying(T0, 3.5, 34, per_hour=0) + watering(WATER_AT, 34, 66, 47)
            + drying(WATER_AT + 2 * HOUR + 15 * MIN, 6, 47, per_hour=0.67)
            + [{"at": T0 + 11 * HOUR + 44 * MIN, "percent": 35.0}] + drying(T0 + 12 * HOUR, 2, 35, per_hour=0))


def owner_pot(clock: dict[str, float]) -> th.Thirst:
    thirst = th.Thirst(None, demo=True, clock=lambda: clock["now"], stage_source=lambda: STAGE)
    set_at = clock["now"]
    clock["now"] = T0 - 2 * DAY
    thirst.set_pot({"volume_l": 11.4, "drain": True})
    clock["now"] = set_at
    return thirst


def with_sensor(thirst: th.Thirst, rows: list[dict[str, Any]], clock: dict[str, float]) -> list[float]:
    """The sensor's history as soil.py hands it over, and a record of every look."""
    looks: list[float] = []

    def history(hours: float) -> list[dict[str, Any]]:
        looks.append(hours)
        return [r for r in rows if clock["now"] - hours * HOUR <= r["at"] <= clock["now"]]

    thirst.soil_history = history
    return looks


def logged(thirst: th.Thirst) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in thirst.log_path().read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row["kind"] == "water"]


def text_now(thirst: th.Thirst, now: float) -> str:
    ledger = thirst.ledger(now=now, stage=STAGE)
    snap = {"online": True, "mqtt": True, "shadow_age": 1, "sensors_age": 60, "pending": {}, "light": {"level": 75},
            "climate": {"temp": 24.0, "humidity": 50.0, "probe_vpd": 1.2, "water_kind": "wet_dry", "water_state": "wet",
                        "water_since": now - DAY, "water_run_s": 8 * HOUR},
            "ports": {"usb1": {"on": False, "mode": 0, "in_plan": False, "accessory": 0, "can_write": True}}}
    found = th.blocks(snap, ledger, now=now, model=MODEL, stage=STAGE, pump=["usb1"], coverage=1.0, tz=BERLIN)
    decision = th.trigger(ledger, found, now, MODEL, stage_key="veg", tz=BERLIN)
    return th.decision_text(decision, ledger, now, "en", tz=BERLIN)


# ---------- the rises ----------

def test_the_owners_day_is_one_watering_and_the_moved_blade_is_none() -> None:
    found = th.sensor_rises(DAY_ROWS)
    assert found == [{"at": WATER_AT, "low": 34, "high": 66}]


def test_the_drain_down_and_a_second_can_on_top_belong_to_the_same_watering() -> None:
    rows = drying(T0, 2, 30, per_hour=0) + watering(T0 + 2 * HOUR, 30, 60, 45)
    rows.append({"at": T0 + 2 * HOUR + 40 * MIN, "percent": 70.0})  # more water within the window: the same watering
    found = th.sensor_rises(sorted(rows, key=lambda r: r["at"]))
    assert len(found) == 1 and found[0]["high"] == 70


def test_two_waterings_days_apart_are_two() -> None:
    rows = (drying(T0, 2, 34, per_hour=0) + watering(T0 + 2 * HOUR, 34, 66, 47)
            + drying(T0 + 5 * HOUR, 60, 47, per_hour=0.3) + watering(T0 + 66 * HOUR, 29, 75, 50))
    assert [round(f["low"]) for f in th.sensor_rises(sorted(rows, key=lambda r: r["at"]))] == [34, 29]


def test_a_reading_after_a_gap_counts_against_the_last_one_within_twelve_hours() -> None:
    before = drying(T0, 1, 30, per_hour=0)
    short = before + [{"at": T0 + 6 * HOUR, "percent": 52.0}]  # server down 5 h, the pot was watered meanwhile
    long = before + [{"at": T0 + 20 * HOUR, "percent": 52.0}]  # down for 19 h: nothing to compare with
    assert len(th.sensor_rises(short)) == 1
    assert th.sensor_rises(long) == []


def test_slow_drying_and_the_sensors_wobble_are_no_water() -> None:
    rows = [{"at": T0 + i * 15 * MIN, "percent": 40 + (i % 2) - i * 0.1} for i in range(200)]
    assert th.sensor_rises(rows) == []


# ---------- into the ledger ----------

def test_a_watering_nobody_entered_is_logged_once_as_unknown_water() -> None:
    clock = {"now": T0 + 13 * HOUR}
    thirst = owner_pot(clock)
    with_sensor(thirst, DAY_ROWS, clock)
    found = thirst.sensor_waterings()
    assert [(e["at"], e["origin"], e["litres"], e["rise"]) for e in found] == [(WATER_AT, "sensor", None, [34, 66])]
    assert thirst.sensor_waterings() == []  # the next look finds it in the log
    assert len(logged(thirst)) == 1


def test_the_ledger_counts_it_full_with_a_hold_that_says_amount_unknown() -> None:
    clock = {"now": T0 + 13 * HOUR}
    thirst = owner_pot(clock)
    with_sensor(thirst, DAY_ROWS, clock)
    thirst.sensor_waterings()
    ledger = thirst.ledger(stage=STAGE)
    assert ledger.hold is not None and ledger.hold["since"] == WATER_AT and ledger.hold["cause"] == "unknown"
    assert ledger.unknown_amount and ledger.unknown_origin == "sensor"
    assert ledger.anchor == {"kind": "unknown_amount", "at": WATER_AT}
    said = text_now(thirst, clock["now"])
    assert said.startswith("Watered ") and "amount unknown" in said and "too wet" not in said.lower()
    event = next(e for e in ledger.events if e["kind"] == "water")
    assert th.event_text(event, "en") == "Watered, seen by the soil sensor (34 to 66%), amount unknown"


def test_water_of_a_known_overshoot_still_says_too_wet() -> None:
    now = T0 + 2 * DAY
    the_pot = th.Pot(volume_l=11.4, set_at=T0 - DAY, id="pot-t")
    series = th.constant_series(0.0125 / 3600, T0 - DAY, now)
    events = [{"id": "w1", "kind": "water", "at": T0, "litres": 0.5, "origin": "hand"},
              {"id": "w2", "kind": "water", "at": T0 + HOUR, "litres": 2.0, "origin": "hand"}]
    ledger = th.fold(the_pot, events, series, now, MODEL, stage_key="veg")
    assert ledger.hold is not None and ledger.hold["cause"] == "overshoot"
    found = th.blocks({"online": True, "mqtt": True}, ledger, now=now, model=MODEL, stage=STAGE, pump=["usb1"],
                      coverage=1.0, tz=BERLIN)
    hold = next(b for b in found if b["key"].startswith("wet_hold"))
    assert hold["key"] == "wet_hold" and th.block_text(hold, "en", BERLIN).startswith("too wet since")


def test_a_watering_in_the_log_covers_the_rise() -> None:
    for entry in ({"kind": "water", "litres": 0.8, "origin": "hand"},  # entered by hand, an hour late
                  {"kind": "water", "litres": None, "origin": "once", "port": "usb1", "from": WATER_AT - 10 * MIN}):
        clock = {"now": T0 + 13 * HOUR}
        thirst = owner_pot(clock)
        thirst.log(entry, at=WATER_AT + HOUR)
        with_sensor(thirst, DAY_ROWS, clock)
        assert thirst.sensor_waterings() == []


def test_an_entry_made_after_the_sensor_logged_replaces_it_in_the_ledger() -> None:
    clock = {"now": T0 + 13 * HOUR}
    thirst = owner_pot(clock)
    with_sensor(thirst, DAY_ROWS, clock)
    thirst.sensor_waterings()
    thirst.log({"kind": "water", "litres": 0.8, "origin": "hand"}, at=WATER_AT + 2 * HOUR)
    ledger = thirst.ledger(stage=STAGE)
    assert [(w["origin"], w["litres"]) for w in ledger.waterings] == [("hand", 0.8)]
    assert not ledger.unknown_amount


def test_no_water_means_the_rise_counts_nothing() -> None:
    clock = {"now": T0 + 13 * HOUR}
    thirst = owner_pot(clock)
    with_sensor(thirst, DAY_ROWS, clock)
    entry = thirst.sensor_waterings()[0]
    view = thirst._open_arrival(thirst.ledger(stage=STAGE), clock["now"])
    assert view == {"event": entry["id"], "at": WATER_AT, "pump": False, "sensor": True}
    thirst.arrived({"event": entry["id"], "answer": "no"})
    ledger = thirst.ledger(stage=STAGE)
    assert ledger.hold is None and not ledger.unknown_amount
    assert thirst.sensor_waterings() == []  # answered "no", and still not logged again


def test_a_young_rise_and_an_open_pump_run_wait() -> None:
    clock = {"now": WATER_AT + 20 * MIN}  # the rise is 20 minutes old: a pump run may still be logged
    thirst = owner_pot(clock)
    with_sensor(thirst, DAY_ROWS, clock)
    assert thirst.sensor_waterings() == []
    clock["now"] = T0 + 13 * HOUR
    thirst.state["runs"] = {"usb1": {"first_on": clock["now"] - MIN, "last_on": clock["now"], "origin": "once"}}
    assert thirst.sensor_waterings() == []
    thirst.state["runs"] = {}
    assert len(thirst.sensor_waterings()) == 1


def test_the_sensor_hook_looks_without_a_setting_and_only_every_few_minutes() -> None:
    clock = {"now": T0 + 13 * HOUR}
    thirst = owner_pot(clock)
    looks = with_sensor(thirst, DAY_ROWS, clock)
    uncalibrated = {"state": None, "percent": 35.0, "relative": None}
    thirst.sensor_check(uncalibrated)
    thirst.sensor_check(uncalibrated)
    assert len(looks) == 1 and len(logged(thirst)) == 1
    clock["now"] += th.SENSOR_WATER_LOOK_S
    thirst.sensor_check(uncalibrated)
    assert len(looks) == 2 and len(logged(thirst)) == 1


def test_no_pot_or_no_history_logs_nothing() -> None:
    clock = {"now": T0 + 13 * HOUR}
    bare = th.Thirst(None, demo=True, clock=lambda: clock["now"], stage_source=lambda: STAGE)
    with_sensor(bare, DAY_ROWS, clock)
    assert bare.sensor_waterings() == []
    thirst = owner_pot(clock)
    assert thirst.sensor_waterings() == []  # no soil_history: the demo and a box without the gateway
