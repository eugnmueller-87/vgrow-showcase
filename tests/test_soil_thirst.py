"""The soil sensor as a check of the Durst-Index (Thirst.sensor_check): dry makes a due pot measured, moist and wet keep
the pump away, and a sensor without a pot, without a setting or with one odd reading writes nothing."""

from __future__ import annotations

import json
from typing import Any

from vgrow import thirst as th

T0 = 1_793_900_000.0
HOUR = 3600
STAGE = {"key": "veg", "label": "Veg", "since": "2026-11-01"}


def ledger_at(clock: dict[str, float]) -> th.Thirst:
    thirst = th.Thirst(None, demo=True, clock=lambda: clock["now"], stage_source=lambda: STAGE)
    thirst.set_pot({"volume_l": 11.4, "drain": True})
    return thirst


def checks(thirst: th.Thirst) -> list[dict[str, Any]]:
    """What the sensor wrote, from the log itself: the card's list keeps only the last few lines."""
    rows = [json.loads(line) for line in thirst.log_path().read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row["kind"] == "check"]


def reading(state: str | None) -> dict[str, Any]:
    return {"state": state, "percent": 30.0, "relative": 60.0}


def test_an_answer_counts_after_two_readings_and_is_a_measured_check() -> None:
    clock = {"now": T0}
    thirst = ledger_at(clock)
    thirst.sensor_check(reading("low"))
    assert checks(thirst) == []  # one odd reading writes nothing
    clock["now"] += 60
    thirst.sensor_check(reading("low"))
    found = checks(thirst)
    assert len(found) == 1 and found[0]["how"] == "sensor"
    ledger = thirst.ledger(stage=STAGE)
    assert [e["origin"] for e in ledger.events if e["kind"] == "check"] == ["sensor"]
    assert ledger.measured == {"kind": "sensor", "at": clock["now"], "answer": "dry"}
    assert ledger.ti == 100  # dry at an unknown index is due, the same as a dry finger


def test_moist_caps_the_index_and_wet_blocks() -> None:
    clock = {"now": T0}
    thirst = ledger_at(clock)
    for _ in range(2):
        thirst.sensor_check(reading("ok"))
        clock["now"] += 60
    assert thirst.ledger(stage=STAGE).moist_until is not None
    for _ in range(2):
        thirst.sensor_check(reading("high"))
        clock["now"] += 60
    assert thirst.ledger(stage=STAGE).wet_until is not None


def test_an_unchanged_answer_repeats_only_every_six_hours() -> None:
    clock = {"now": T0}
    thirst = ledger_at(clock)
    for _ in range(10):
        thirst.sensor_check(reading("ok"))
        clock["now"] += 60
    assert len(checks(thirst)) == 1
    clock["now"] += 6 * HOUR
    thirst.sensor_check(reading("ok"))
    assert len(checks(thirst)) == 2


def test_a_change_of_answer_is_written_at_once_after_its_second_reading() -> None:
    clock = {"now": T0}
    thirst = ledger_at(clock)
    for state in ("ok", "ok", "low", "low"):
        thirst.sensor_check(reading(state))
        clock["now"] += 60
    assert [e["answer"] for e in checks(thirst)] == ["moist", "dry"]


def test_no_setting_writes_nothing() -> None:
    clock = {"now": T0}
    thirst = ledger_at(clock)
    for _ in range(3):
        thirst.sensor_check(reading(None))
    assert checks(thirst) == []


def test_no_pot_writes_nothing() -> None:
    clock = {"now": T0}
    bare = th.Thirst(None, demo=True, clock=lambda: clock["now"], stage_source=lambda: STAGE)
    for _ in range(3):
        bare.sensor_check(reading("low"))
    assert not bare.log_path().exists()


def test_two_dry_readings_of_the_sensor_never_lift_a_wet_hold_early() -> None:
    clock = {"now": T0 - 3 * 86400}
    thirst = ledger_at(clock)
    clock["now"] = T0
    thirst.log({"kind": "weigh", "kg": 8.4, "full": True}, at=clock["now"] - 2 * 86400)
    thirst.log({"kind": "water", "litres": 2.0, "origin": "hand"}, at=clock["now"])  # far more than the pot was missing
    assert thirst.ledger(stage=STAGE).hold is not None
    for _ in range(3):
        clock["now"] += 60
        thirst.sensor_check(reading("low"))
    assert thirst.ledger(stage=STAGE).hold is not None


def test_the_cards_list_shows_a_sensor_answer_once_until_it_changes() -> None:
    clock = {"now": T0}
    thirst = ledger_at(clock)
    thirst.log({"kind": "check", "answer": "moist", "how": "sensor", "depth_cm": 5.0}, at=clock["now"])
    thirst.log({"kind": "check", "answer": "moist", "how": "sensor", "depth_cm": 5.0}, at=clock["now"] + 6 * HOUR)
    thirst.log({"kind": "check", "answer": "dry", "how": "sensor", "depth_cm": 5.0}, at=clock["now"] + 7 * HOUR)
    clock["now"] += 8 * HOUR
    shown = [e["answer"] for e in thirst.ledger(stage=STAGE).events if e["kind"] == "check"]
    assert shown == ["dry", "moist"]  # newest first; the repeat is in the log, not on the card


def test_the_view_counts_what_was_given_this_week() -> None:
    clock = {"now": T0 - 10 * 86400}
    thirst = ledger_at(clock)
    clock["now"] = T0
    thirst.log({"kind": "water", "litres": 0.5, "origin": "hand"}, at=clock["now"] - 8 * 86400)
    thirst.log({"kind": "water", "litres": 0.75, "origin": "hand"}, at=clock["now"] - 86400)
    view = thirst.view({"ports": {}, "climate": {}}, "de")
    assert view["given_week_l"] == 0.75 and view["given_week_n"] == 1


def test_the_event_text_names_the_sensor() -> None:
    text = th.event_text({"kind": "check", "how": "sensor", "answer": "dry", "depth_cm": 5.0}, "de")
    assert text == "Bodensensor: trocken"
    assert th.event_text({"kind": "check", "how": "sensor", "answer": "moist", "depth_cm": 5.0}, "en") == "Soil sensor: moist"
