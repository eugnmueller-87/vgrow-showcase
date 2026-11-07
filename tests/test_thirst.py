"""The Durst-Index (vgrow/thirst.py): the index, its anchors and the wet-hold, the guardrails, the decision, the stage
plan and the forecast, USB power without water flow, the need gate on the watering controller's schedule (it only skips
or shortens, never starts or lengthens, and fails closed), the DST change, Hank's watering answers, the inputs, the
server, the demo scenes, the grep tests (a full tank never triggers anything) and the pump OFF path."""

from __future__ import annotations

import ast
import asyncio
import codecs
import copy
import json
import random
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import box as box_mod
from vgrow import config
from vgrow import thirst as th
from vgrow.alerts import Alerts
from vgrow.box import CommandRefused, check_port_lock
from vgrow.budget import CommandBudget
from vgrow.portguard import PortGuard
from vgrow.watering import WateringController

REPO = Path(__file__).resolve().parent.parent
MODEL = th.load_model()
# the surnames of the read papers' authors, rot13: checked absent from titles, never written out here
AUTHORS = tuple(codecs.decode(n, "rot_13") for n in ("Ovyqreonpx", "Pbyynqb", "Znffhryn", "Hcunz", "Punccryy", "Ebpnzben",
                                                     "Ureaáaqrm"))
HOUR, DAY = 3600.0, 86400.0
BERLIN = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc).timestamp()  # a Tuesday morning, Berlin summer time
STAGE = {"key": "early_flower", "set_by": "manual", "label": "Frühe Blüte"}
RATE_L_H = 0.0125  # 12.5 ml/h, 0.3 l a day: a made-up constant use, so the arithmetic below stays readable
# The owner's pot (his entry of 27.10.2026): 3 US gallons, 3 x 3.785 l, about 11.4 l. Only his pot is this size: the
# cockpit has no default pot, every other pot is entered by its owner.
OWNER_POT_L = 11.4
D_TRIG = 0.5 * 0.3 * OWNER_POT_L  # f_trig x a_w x 11.4 l = 1.71 l


def local(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0, fold: int = 0) -> float:
    return datetime(y, mo, d, h, mi, s, tzinfo=BERLIN, fold=fold).timestamp()


def pot(volume: float | None = OWNER_POT_L, at: float = T0 - 10 * DAY, **kw: Any) -> th.Pot:
    return th.Pot(volume_l=volume, set_at=at, id="pot-t", **kw)


def series(start: float = T0 - 10 * DAY, end: float = T0 + 19 * DAY, rate_l_h: float = RATE_L_H) -> th.Series:
    """A made-up constant use from start to end; the series keeps 30 days (th.SERIES_KEEP_S), so this stays inside."""
    assert end - start <= th.SERIES_KEEP_S
    return th.constant_series(rate_l_h / 3600, start, end)


def ev(kind: str, at: float, **kw: Any) -> dict[str, Any]:
    return {"id": kw.pop("id", f"{kind}-{int(at)}"), "kind": kind, "at": at, **kw}


def fold(events: list[dict[str, Any]], now: float, the_pot: th.Pot | None = None, stage: str = "early_flower",
         ser: th.Series | None = None) -> th.Ledger:
    return th.fold(the_pot or pot(), events, ser or series(), now, MODEL, stage_key=stage)


def deep(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep(out[key], value)
        else:
            out[key] = value
    return out


def snap(**extra: Any) -> dict[str, Any]:
    base = {"online": True, "mqtt": True, "shadow_age": 1, "sensors_age": 60, "pending": {},
            "climate": {"temp": 24.0, "humidity": 50.0, "probe_temp": 25.0, "probe_humidity": 55.0, "probe_vpd": 1.2,
                        "water_kind": "wet_dry", "water_state": "wet", "water_since": T0 - DAY,
                        "water_run_s": 8 * HOUR},
            "light": {"level": 75},
            "ports": {"usb1": {"on": False, "mode": 0, "in_plan": False, "accessory": 0, "can_write": True},
                      "usb2": {"on": False, "mode": 0, "in_plan": False, "accessory": 0, "can_write": True}}}
    return deep(base, extra)


def decide(ledger: th.Ledger, now: float, s: dict[str, Any] | None = None, *, stage: dict[str, Any] | None = STAGE,
           coverage: float | None = 1.0, asked: float | None = None, blocked: float | None = None,
           pump: list[str] | None = None) -> th.Decision:
    found = th.blocks(s or snap(), ledger, now=now, model=MODEL, stage=stage, pump=pump or ["usb1"],
                      coverage=coverage, tz=BERLIN)
    return th.trigger(ledger, found, now, MODEL, stage_key=(stage or {}).get("key"), asked_since=asked,
                      blocked_since=blocked, tz=BERLIN)


def full_then(hours_ago: float, now: float) -> list[dict[str, Any]]:
    """The pot weighed full and drained (the reference) `hours_ago` before now."""
    return [ev("weigh", now - hours_ago * HOUR, kg=8.4, full=True)]


def keys(found: th.Decision) -> set[str]:
    return {b["key"] for b in found.blocks}


# ---------- the model file ----------

def test_every_constant_has_a_source_or_an_assumption_tag_and_no_personal_name() -> None:
    raw = (REPO / "kartei" / "water_model.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    sources = data["sources"]
    sources_md_keys = {"U1", "B05", "PU", "C24", "M23", "CA07", "BL", "KS", "CH", "VT", "VK26", "SOZV", "FAO", "MAN"}
    assert set(sources) <= sources_md_keys  # the keys of the reviewed sources.md, nothing invented
    for key, src in sources.items():
        assert src.get("url", "").startswith("https://") or (REPO / src.get("kartei", "-")).exists(), key
        assert not re.search(r"vivosun\.com|amazon\.", json.dumps(src)), key  # never their pages (hard rule)
        # titles without personal names: the authors of the read papers are left out (the repo's rule); the names
        # are written as rot13, so this file holds none itself
        for author in AUTHORS:
            assert author not in src["title"], (key, author)
    assert data["substrate_class"] == "soil" and len(data["constants"]) >= 60
    for name, const in data["constants"].items():
        assert "value" in const, name
        tags = [k for k in ("source", "code", "assumption") if const.get(k)]
        assert tags, f"{name} has no source, code or assumption tag"
        for ref in const.get("source") or []:
            assert ref in sources, (name, ref)
        for ref in const.get("context_sources") or []:
            assert ref in sources, (name, ref)  # context only: such a constant is still an assumption
            assert const.get("source") is None and "calibrated by" in const.get("assumption", ""), name
        if const.get("code"):
            path, _, symbol = const["code"].partition(":")
            assert symbol in (REPO / path).read_text(encoding="utf-8"), (name, const["code"])
        if const.get("local"):
            assert const.get("assumption"), name  # a local observation is no source on its own
        if isinstance(const.get("assumption"), str):
            assert const["assumption"].strip(), name
        if not const.get("source") and not const.get("code"):
            # an own number says what calibrates it ("assumption, calibrated by ..."), never only that it is own
            assert "calibrated by" in const["assumption"], name
    assert data["conflicts"] and all(c["sources"] and set(c["sources"]) <= set(sources) for c in data["conflicts"])
    # no first name of the people behind the box, written as rot13 so this file holds none itself
    for word in (codecs.decode("Rhtra", "rot_13"), codecs.decode("Yhxnf", "rot_13")):
        assert word not in raw


def test_the_code_constants_of_the_model_are_the_codes_own() -> None:
    from vgrow import autopilot

    assert MODEL("mist_gap_points") == autopilot.MIST_GAP
    assert MODEL("probe_stale_s") == autopilot.STALE_SECONDS
    assert MODEL("tank_stale_s") == box_mod.WATER_STALE_SECONDS
    assert MODEL("poll_edge_s") == box_mod.POLL_SECONDS


def test_k0_sits_inside_the_frame_of_the_read_studies() -> None:
    """20 ml/h is 0.49 l a day at 24 h light, 75 %, VPD 1.73 kPa and S 1, inside 0.05 (M23) to 0.71 (C24)."""
    rate = th.et_rate_ml_h(MODEL, light=75, vpd=1.73, height_cm=61)
    assert round(rate * 24 / 1000, 2) == 0.49
    assert MODEL("et_frame_low_l_day") <= rate * 24 / 1000 <= MODEL("et_frame_high_l_day")
    night = th.et_rate_ml_h(MODEL, light=0, vpd=1.73, height_cm=61, last_lit=75)
    assert night == pytest.approx(0.1 * rate)  # the night term: 10 % of the lit rate (CA07: 5 to 15 %)
    assert th.et_rate_ml_h(MODEL, light=75, vpd=9.0, height_cm=61) == th.et_rate_ml_h(MODEL, light=75, vpd=2.5,
                                                                                         height_cm=61)  # clamped
    assert th.et_rate_ml_h(MODEL, light=75, vpd=1.2, height_cm=200) == th.et_rate_ml_h(MODEL, light=75, vpd=1.2,
                                                                                         height_cm=91.5)  # S at 1.5


def test_the_stage_table_gives_each_stage_its_trigger_share_and_cap() -> None:
    shares = MODEL("stage_f_trig")
    caps = MODEL("stage_caps_ml")
    assert set(shares) == set(caps) == set(th.STAGE_KEYS)
    assert shares["seedling"] == 0.4 and all(shares[k] == 0.5 for k in th.STAGE_KEYS if k != "seedling")
    assert (caps["seedling"], caps["veg"], caps["early_flower"]) == (200, 1000, 1400)  # the stage card's presets
    assert th.f_trig_for(MODEL, "late_flower", pot(dryback=True)) == 0.6  # the owner's dry-back (review S8)
    assert th.f_trig_for(MODEL, "mid_flower", pot(dryback=True)) == 0.5  # only in late flower


# ---------- the metric and its anchors ----------

def test_the_index_is_the_deficit_over_the_trigger() -> None:
    now = T0 + 66 * HOUR
    ledger = fold(full_then(68.4, now), now)  # 68.4 h at 12.5 ml/h: 0.855 l, half of D_trig = 1.71 l
    assert ledger.d_trig_l == pytest.approx(D_TRIG) == pytest.approx(1.71)
    assert ledger.d_l == pytest.approx(68.4 * RATE_L_H)  # 0.855 l since the full weighing
    assert ledger.ti == pytest.approx(50.0)
    assert ledger.measured is None  # the weighing is 68 h old: only the model speaks now
    later = fold([*full_then(66, now), ev("weigh", now - HOUR, kg=8.4 - 0.9)], now)
    assert later.d_l == pytest.approx(0.9 + RATE_L_H) and later.measured["kind"] == "weigh"
    assert later.band == pytest.approx(5.4, abs=0.05) and later.confidence == "high"


def test_finger_answers_follow_the_rules_and_never_claim_high_confidence() -> None:
    now = T0 + 5 * DAY
    at75 = now - 0.75 * D_TRIG / RATE_L_H * HOUR
    dry = fold([ev("weigh", at75, kg=8.4, full=True), ev("check", now, answer="dry", depth_cm=5)], now)
    assert dry.ti == pytest.approx(100) and dry.measured["kind"] == "finger"
    assert dry.band == pytest.approx(MODEL("band_finger_ti")) and dry.confidence == "medium"  # review S1
    finger = [ev("weigh", at75, kg=8.4, full=True), ev("check", now, answer="dry", depth_cm=5)]
    assert fold(finger, now + 11 * HOUR).confidence == "medium"  # 25 plus 10 a day: medium for half a day
    assert fold(finger, now + 13 * HOUR).confidence == "low"
    at50 = now - 0.5 * D_TRIG / RATE_L_H * HOUR
    low = fold([ev("weigh", at50, kg=8.4, full=True), ev("check", now, answer="dry", depth_cm=5)], now)
    assert low.ti == pytest.approx(50) and low.dry_below and decide(low, now).reason == "weigh_or_wait"
    at95 = now - 0.95 * D_TRIG / RATE_L_H * HOUR
    moist = fold([ev("weigh", at95, kg=8.4, full=True), ev("check", now, answer="moist", depth_cm=5)], now + 10 * HOUR)
    assert moist.ti == pytest.approx(85)
    after = fold([ev("weigh", at95, kg=8.4, full=True), ev("check", now, answer="moist", depth_cm=5)], now + 30 * HOUR)
    assert after.ti == pytest.approx(85 + 6 * RATE_L_H / D_TRIG * 100)
    wet = fold([ev("weigh", at95, kg=8.4, full=True), ev("check", now, answer="wet", depth_cm=5)], now + HOUR)
    assert wet.wet_until == pytest.approx(now + DAY) and decide(wet, now + HOUR).reason == "wet"
    first = fold([ev("check", now, answer="dry", depth_cm=5)], now)
    assert first.ti == pytest.approx(100) and first.first_cycle and first.w_ref is None
    found = th.dose(first, MODEL, now=now, stage_key="early_flower")
    assert found and found["litres"] <= 0.5 * D_TRIG


def test_a_hard_dry_down_halves_the_dose_whether_weighed_or_fingered() -> None:
    """Review S2: the halving follows the index above 130, not the way it was measured."""
    now = T0 + 6 * DAY
    weighed = fold([ev("weigh", now - 5 * DAY, kg=8.4, full=True), ev("weigh", now - 60, kg=8.4 - 1.4 * D_TRIG)], now)
    assert weighed.ti > 130 and weighed.hard_drydown
    normal = fold([ev("weigh", now - 5 * DAY, kg=8.4, full=True), ev("weigh", now - 60, kg=8.4 - 1.05 * D_TRIG)], now)
    assert not normal.hard_drydown
    assert th.dose(weighed, MODEL, now=now, stage_key="early_flower")["capped_by"] == "half"


def test_water_on_a_pot_without_anchor_counts_it_as_full() -> None:
    """Review S5 (safety): every event re-anchors; water poured before any check makes the pot full, wide band."""
    now = T0 + 2 * DAY
    ledger = fold([ev("water", now - 2 * HOUR, litres=3.0, origin="hand")], now)
    assert ledger.d_l == pytest.approx(2 * RATE_L_H) and ledger.band >= MODEL("band_unknown_amount_ti")
    assert ledger.anchor["kind"] == "water_unanchored" and ledger.confidence == "low"


def test_a_reference_weight_comes_from_a_full_pot() -> None:
    now = T0 + 3 * DAY
    full = now - 30 * HOUR
    ledger = fold([ev("saucer", full, water=True), ev("weigh", now - 25 * HOUR, kg=7.9)], now)
    assert ledger.w_ref == pytest.approx(7.9 + 5 * RATE_L_H)
    assert ledger.hold is not None  # water in the saucer: an overshoot of unknown amount, the wet-hold runs


def test_the_wet_hold_overshoot_then_dry_at_70_is_blocked() -> None:
    """Overshoot, 24 h later "trocken" at TI 70: BLOCKED (the reviewed design, 4.8 and 13)."""
    rate = 0.7 * D_TRIG / 24
    ser = series(rate_l_h=rate)
    water_at = T0 + 2 * DAY
    events = [ev("weigh", T0, kg=8.4, full=True), ev("water", water_at, litres=3.5, origin="hand")]
    now = water_at + 24 * HOUR
    before = fold(events, now, ser=ser)
    assert before.overshoot["ml"] == pytest.approx((3.5 - 48 * rate) * 1000) and before.hold is not None
    assert before.ti == pytest.approx(70)
    answered = fold([*events, ev("check", now, answer="dry", depth_cm=5)], now, ser=ser)
    assert answered.hold is not None and answered.measured is None  # in a hold a finger never measures
    assert answered.ti == pytest.approx(70)  # and never raises the index (outside a hold: 100)
    found = decide(answered, now)
    assert found.kind == "BLOCKED" and found.reason == "hold_dry" and found.dose is None
    assert "wet_hold" in keys(found)
    outside = fold([ev("weigh", now - 25 * HOUR, kg=8.4, full=True), ev("check", now, answer="dry", depth_cm=5)], now,
                   ser=ser)
    assert outside.hold is None and outside.ti == pytest.approx(100)


def test_the_wet_hold_lifts_with_two_dry_answers_12_h_apart_once_the_model_reaches_100() -> None:
    water_at = local(2026, 10, 20, 9)
    events = [ev("weigh", water_at - 2 * DAY, kg=8.4, full=True), ev("water", water_at, litres=2.0, origin="hand")]
    reach = water_at + D_TRIG / RATE_L_H * HOUR
    first, second = reach - 13 * HOUR, reach - HOUR
    answers = [ev("check", first, answer="dry", depth_cm=5), ev("check", second, answer="dry", depth_cm=5)]
    now = local(2026, 10, 26, 10)
    assert now > reach

    def run(extra: list[dict[str, Any]]) -> th.Ledger:
        return fold([*events, *extra], now)

    lifted = run(answers)
    assert lifted.hold is None and lifted.measured and lifted.ti >= 100
    assert decide(lifted, now).kind == "PROPOSE"
    assert run([answers[1]]).hold is not None  # one answer is not enough
    assert run([ev("check", second - 2 * HOUR, answer="dry", depth_cm=5), answers[1]]).hold is not None
    assert run([ev("check", first, answer="dry", depth_cm=2.5), answers[1]]).hold is not None  # 5 cm only
    early = run([ev("check", water_at + DAY, answer="dry", depth_cm=5),
                 ev("check", water_at + 2 * DAY, answer="dry", depth_cm=5)])
    assert early.hold is not None  # 24 h apart, but the model from the overshoot is not at 100 yet


def test_after_one_dry_answer_in_a_hold_the_card_asks_again_12_h_later() -> None:
    """Review S3 (safety): the second dry answer is asked for when it can count, not 24 h of silence."""
    water_at = local(2026, 10, 20, 9)
    events = [ev("weigh", water_at - 2 * DAY, kg=8.4, full=True), ev("water", water_at, litres=2.0, origin="hand")]
    near = water_at + 0.9 * D_TRIG / RATE_L_H * HOUR  # the hold's own index near 90
    said = ev("check", near, answer="dry", depth_cm=5)
    soon = decide(fold([*events, said], near + HOUR), near + HOUR)
    assert soon.kind == "BLOCKED" and soon.reason == "hold_dry" and soon.next_look == pytest.approx(near + 12 * HOUR)
    assert th.check_text(soon, "de").startswith("Finger 5 cm tief")
    later = decide(fold([*events, said], near + 12.5 * HOUR), near + 12.5 * HOUR)
    assert later.kind == "ASK" and later.reason == "hold_check"


def test_water_in_the_saucer_is_an_overshoot_and_lowers_the_next_dose() -> None:
    water_at = T0 + DAY
    events = [ev("weigh", T0 - DAY, kg=8.4, full=True), ev("water", water_at, litres=0.4, origin="hand", id="w1"),
              ev("saucer", water_at + 600, water=True, event="w1")]
    ledger = fold(events, water_at + HOUR)
    assert ledger.overshoot["unknown"] and ledger.hold is not None
    assert ledger.saucer_less and ledger.drains_without == 0
    ledger.hold, ledger.d_l = None, 1.6
    small = th.dose(ledger, MODEL, now=water_at + 5 * DAY, stage_key="early_flower")
    ledger.saucer_less = False
    plain = th.dose(ledger, MODEL, now=water_at + 5 * DAY, stage_key="early_flower")
    assert small["litres"] <= 0.75 * plain["litres"] + 0.05


def test_a_saucer_on_the_first_watering_lowers_the_pot_one_step() -> None:
    events = [ev("check", T0, answer="dry", depth_cm=5), ev("water", T0 + 600, litres=0.8, origin="hand", id="w1"),
              ev("saucer", T0 + 1200, water=True, event="w1")]
    ledger = fold(events, T0 + HOUR)
    assert ledger.v_pot_l == 7 and ledger.d_trig_l == pytest.approx(0.5 * 0.3 * 7)  # 11.4 l: the next size is 7 l
    assert th._step_down(MODEL, 11.0) == 7 and th._step_down(MODEL, 13.0) == 11 and th._step_down(MODEL, 3.0) == 3


def test_a_pump_run_of_unknown_amount_counts_the_pot_as_full() -> None:
    now = T0 + 2 * DAY
    events = [*full_then(40, now - 6 * HOUR),
              ev("water", now - 6 * HOUR, litres=None, origin="schedule", minutes=30, port="usb1")]
    ledger = fold(events, now)
    assert ledger.d_l == pytest.approx(6 * RATE_L_H) and ledger.unknown_amount
    assert ledger.overshoot == {"unknown": True, "at": now - 6 * HOUR} and ledger.hold is not None
    assert ledger.band >= MODEL("band_unknown_amount_ti")
    text = th.event_text(ledger.events[0], "de")
    assert "unbekannte Menge" in text and "30" in text and "USB 1" in text
    assert "unknown amount" in th.event_text(ledger.events[0], "en")
    arrived_no = fold([*events, ev("arrived", now - 5 * HOUR, event=events[-1]["id"], answer="no")], now)
    assert arrived_no.unknown_amount is False and arrived_no.hold is None  # "Nein": that run's water is 0


def test_only_a_water_event_says_who_watered(caplog) -> None:  # type: ignore[no-untyped-def]
    """Seen 06.11.2026: every /api/state logged "Missing text thirst.origin.sensor", though a check never shows its origin."""
    assert th.event_text({"kind": "check", "answer": "wet", "how": "sensor", "origin": "sensor"}, "en") == "Soil sensor: wet"
    assert "by hand" in th.event_text({"kind": "water", "litres": 0.5}, "en")
    for origin in ("hand", "feed", "schedule", "once", "app", "other"):
        text = th.event_text({"kind": "water", "litres": None, "origin": origin, "port": "usb1", "minutes": 3}, "en")
        assert f"thirst.origin.{origin}" not in text
    assert "Missing text" not in caplog.text


def test_the_feeding_log_litres_count_as_water(isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow import feeding

    ledger = th.Thirst(None, demo=True, clock=lambda: T0 + 2 * DAY, stage_source=lambda: STAGE)
    config.atomic_write(ledger.pot_path(), json.dumps(pot().as_dict()) + "\n")
    feeding.log_path().parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(T0 + DAY, BERLIN).replace(tzinfo=None).isoformat(timespec="seconds")
    feeding.log_path().write_text(json.dumps({"time": stamp, "liters": 1.25, "items": []}) + "\n", encoding="utf-8")
    found = ledger.ledger(stage=STAGE)
    assert [(x["origin"], x["litres"], x["at"]) for x in found.waterings] == [("feed", 1.25, T0 + DAY)]


# ---------- a full tank never triggers anything ----------

TANK_KEYS = {"tank_dry", "tank_settle", "tank_unknown"}


def test_a_full_tank_never_triggers_property() -> None:
    """Flipping the tank between wet and dry changes only the block list (by the tank's own keys): a dry tank adds a
    block, a full one adds nothing, so the decision with a full tank is the decision without any tank."""
    rng = random.Random(26102026)
    seen: set[str] = set()
    for _ in range(400):
        now = T0 + rng.uniform(2, 9) * DAY
        events = [ev("weigh", now - rng.uniform(1, 160) * HOUR, kg=8.4, full=True)]
        if rng.random() < 0.6:
            events.append(ev("check", now - rng.uniform(0, 30) * HOUR, answer=rng.choice(["dry", "moist", "wet"]),
                             depth_cm=5))
        if rng.random() < 0.3:
            events.append(ev("water", now - rng.uniform(1, 50) * HOUR, litres=rng.uniform(0.1, 2.5), origin="hand"))
        ledger = fold(events, now)
        stage = rng.choice([STAGE, STAGE, {"key": "early_flower", "set_by": "estimate"}])
        snaps = {"wet": snap(), "dry": snap(climate={"water_state": "dry"}),
                 "level": snap(climate={"water_kind": "level", "water_level": 100.0})}
        found = {k: th.blocks(s, ledger, now=now, model=MODEL, stage=stage, pump=["usb1"], coverage=1.0, tz=BERLIN)
                 for k, s in snaps.items()}
        other = [[b for b in found[k] if b["key"] not in TANK_KEYS] for k in ("wet", "dry", "level")]
        assert other[0] == other[1] == other[2]
        assert {b["key"] for b in found["wet"]} & TANK_KEYS == set() == {b["key"] for b in found["level"]} & TANK_KEYS
        assert "tank_dry" in {b["key"] for b in found["dry"]}

        def decide_with(block_list: list[dict[str, Any]]) -> th.Decision:
            return th.trigger(ledger, block_list, now, MODEL, stage_key=stage["key"], tz=BERLIN)

        neutral, wet, dry, level = (decide_with(other[0]), decide_with(found["wet"]), decide_with(found["dry"]),
                                    decide_with(found["level"]))
        assert (wet.kind, wet.reason) == (neutral.kind, neutral.reason) == (level.kind, level.reason)
        assert dry.kind != "PROPOSE"
        if neutral.kind == "PROPOSE":
            assert (dry.kind, dry.reason) == ("BLOCKED", "tank")
        seen.add(neutral.kind)
    assert {"PROPOSE", "ASK", "WAIT"} <= seen


def _function(source: str, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_grep_the_tank_is_read_only_inside_blocks() -> None:
    """The grep test: water_state and watering_allowed appear in vgrow/thirst.py inside blocks() and nowhere else, Hank's
    watering lines never read them, and trigger() and the gate take no tank: it reaches them only as a block key."""
    source = (REPO / "vgrow" / "thirst.py").read_text(encoding="utf-8")
    inside = _function(source, "blocks")
    for number, line in enumerate(source.splitlines(), 1):
        if "water_state" in line or "watering_allowed" in line or "water_level" in line:
            assert inside.lineno <= number <= (inside.end_lineno or 0), (number, line)
    hank = (REPO / "vgrow" / "hank" / "thirst_report.py").read_text(encoding="utf-8")
    assert "water_state" not in hank and "watering_allowed" not in hank and "water_level" not in hank
    trig = _function(source, "trigger")
    params = [a.arg for a in [*trig.args.args, *trig.args.kwonlyargs]]
    assert params[:2] == ["ledger", "block_list"]
    assert not {"snap", "snapshot", "climate", "tank", "water", "box"} & set(params)
    for name in ("trigger", "_verdict", "dose"):
        fn = _function(source, name)
        body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) else fn.body
        nodes = [n for stmt in body for n in ast.walk(stmt)]
        used = {n.id for n in nodes if isinstance(n, ast.Name)} | {n.attr for n in nodes if isinstance(n, ast.Attribute)}
        assert not {"climate", "water_state", "watering_allowed", "box_mod"} & used, name
    # nothing anywhere in the cockpit starts watering because of the tank: every caller of watering_allowed only refuses
    for path in (REPO / "vgrow").rglob("*.py"):
        if "vivosun" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "watering_allowed(" in line and "def watering_allowed" not in line:
                assert re.search(r"not (?:box_mod\.)?watering_allowed\(", line), (path.name, number, line)


def test_the_gate_with_a_full_tank_is_the_gate_without_a_tank(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    rig.flow()
    rig.measured_due()
    wet, level = rig.verdict(), rig.verdict(climate={"water_kind": "level", "water_level": 100.0})
    assert wet["run"] and level["run"] and wet["text"] == level["text"]
    dry = rig.verdict(climate={"water_state": "dry"})
    assert not dry["run"] and "trocken" in dry["text"]["de"]


# ---------- the guardrails, each one ----------

def due_ledger(now: float) -> th.Ledger:
    """A pot measured due: weighed full long ago, dry at the finger a minute ago."""
    return fold([ev("weigh", now - 6 * DAY, kg=8.4, full=True), ev("check", now - 60, answer="dry", depth_cm=5)], now)


IN_WINDOW = local(2026, 10, 22, 10)


def test_a_measured_due_pot_with_every_block_clear_gets_a_suggestion() -> None:
    found = decide(due_ledger(IN_WINDOW), IN_WINDOW)
    assert found.kind == "PROPOSE" and found.dose["litres"] > 0 and not found.blocks
    text = th.decision_text(found, due_ledger(IN_WINDOW), IN_WINDOW, "de", BERLIN)
    assert "Vorschlag" in text and "Fingerprobe trocken" in text
    assert found.dose["portions"] >= 1 and found.dose["soak_min"] == 10


@pytest.mark.parametrize("extra,key", [
    ({"climate": {"water_state": "dry"}}, "tank_dry"),
    ({"sensors_age": 900}, "stale"),
    ({"sensors_age": 400}, "tank_unknown"),
    ({"online": False}, "offline"),
    ({"climate": {"water_run_s": 600}}, "tank_settle"),
    ({"ports": {"usb1": {"on": True}}}, "pump_running"),
    ({"ports": {"usb1": {"in_plan": True}}}, "app_plan"),
    ({"ports": {"usb1": {"can_write": False}}}, "app_plan"),
])
def test_each_guardrail_blocks(extra: dict[str, Any], key: str) -> None:
    found = decide(due_ledger(IN_WINDOW), IN_WINDOW, snap(**extra))
    assert found.kind in ("BLOCKED", "ALERT") and key in keys(found), found


def test_stage_ports_coverage_spacing_and_week_block() -> None:
    ledger = due_ledger(IN_WINDOW)
    assert "stage" in keys(decide(ledger, IN_WINDOW, stage={"key": "early_flower", "set_by": "estimate"}))
    assert "gaps" in keys(decide(ledger, IN_WINDOW, coverage=0.5))
    found = th.blocks(snap(), ledger, now=IN_WINDOW, model=MODEL, stage=STAGE, pump=["usb1"], coverage=None,
                      ports_status="changed")
    assert "ports" in {b["key"] for b in found}
    for ago, key in ((HOUR, "settle"), (10 * HOUR, "interval")):
        spaced = fold([ev("weigh", IN_WINDOW - 6 * DAY, kg=8.4, full=True),
                       ev("water", IN_WINDOW - ago, litres=0.2, origin="hand"),
                       ev("check", IN_WINDOW - 60, answer="dry", depth_cm=5)], IN_WINDOW)
        assert key in keys(decide(spaced, IN_WINDOW)), key
    week = fold([ev("weigh", IN_WINDOW - 6 * DAY, kg=8.4, full=True),
                 *[ev("water", IN_WINDOW - d * DAY, litres=1.5, origin="hand") for d in (2, 3, 4, 5)],
                 ev("check", IN_WINDOW - 60, answer="dry", depth_cm=5)], IN_WINDOW)
    assert "week" in keys(decide(week, IN_WINDOW))  # 6 l in a week against at least 5 l


def test_one_event_per_local_day() -> None:
    now = local(2026, 10, 23, 23, 30)
    early = local(2026, 10, 23, 1, 0)
    ledger = fold([ev("weigh", now - 6 * DAY, kg=8.4, full=True), ev("water", early, litres=0.3, origin="hand"),
                   ev("check", now - 60, answer="dry", depth_cm=5)], now)
    assert "today" in keys(decide(ledger, now))


def test_outside_the_round_window_a_measured_due_pot_waits_for_the_round() -> None:
    night = local(2026, 10, 22, 23)
    found = decide(due_ledger(night), night)
    assert found.kind == "WAIT" and found.reason == "round" and found.next_look == local(2026, 10, 23, 9)


def test_a_model_index_asks_and_never_suggests() -> None:
    now = IN_WINDOW
    ledger = fold(full_then(0.95 * D_TRIG / RATE_L_H, now), now)
    assert ledger.measured is None and 94 < ledger.ti < 96
    found = decide(ledger, now)
    assert found.kind == "ASK" and found.reason == "check" and found.dose is None
    over = fold(full_then(1.3 * D_TRIG / RATE_L_H, now), now)
    assert decide(over, now).kind == "ASK"
    assert decide(over, now, asked=now - HOUR).kind == "ALERT"


def test_the_check_is_the_finger_by_the_pot_or_lifting_when_it_lifts() -> None:
    now = IN_WINDOW
    small = fold(full_then(90, now), now, the_pot=pot(diameter_cm=15, height_cm=15))
    assert th.check_of(small, MODEL) == {"how": "finger", "depth_cm": 2.5}
    big = fold(full_then(90, now), now, the_pot=pot(diameter_cm=26, height_cm=24))
    assert th.check_of(big, MODEL) == {"how": "finger", "depth_cm": 5.0}
    assert th.check_of(fold(full_then(90, now), now, the_pot=pot(lifts=True)), MODEL)["how"] == "lift"


def test_no_dose_above_the_caps_property() -> None:
    rng = random.Random(42)
    for _ in range(400):
        volume = rng.choice([3, 5, 7, 11, 15, 19, 25])
        stage = rng.choice(th.STAGE_KEYS)
        now = T0 + 10 * DAY
        given = [ev("water", now - rng.uniform(1, 160) * HOUR, litres=rng.uniform(0.05, 1.5), origin="hand")
                 for _ in range(rng.randint(0, 4))]
        ref = [ev("weigh", now - 9 * DAY, kg=8.0, full=True)] if rng.random() < 0.5 else []
        ledger = fold([*ref, *given], now, the_pot=pot(volume, at=now - 9.5 * DAY), stage=stage)
        ledger.d_l = rng.uniform(0, 3) * ledger.d_trig_l
        ledger.hard_drydown = rng.random() < 0.2
        found = th.dose(ledger, MODEL, now=now, stage_key=stage)
        if found is None:
            continue
        litres = found["litres"]
        aw_cap = 0.6 * 0.3 * volume * (0.75 if ledger.w_ref is None else 1)
        stage_cap = MODEL("stage_caps_ml")[stage] / 1000
        day = sum(x["litres"] for x in given if now - x["at"] < DAY)
        week_left = th.weekly_cap_l(ledger, MODEL) - th.weekly_used_l(ledger, now)
        assert litres <= 0.9 * ledger.d_l + 1e-9
        assert litres <= min(aw_cap, stage_cap) + 1e-9
        assert litres + day <= min(aw_cap, stage_cap) + 1e-9
        assert litres <= week_left + 1e-9
        if ledger.first_cycle:
            assert litres <= 0.5 * ledger.d_trig_l + 1e-9


# ---------- the Thirst rig: a box, the watering controller's settings, a clock ----------

class Box:
    """Just enough box: a snapshot, reported USB fragments for the port listeners, a set_port the gate never calls."""

    def __init__(self) -> None:
        self.snap = snap()
        self.port_listeners: list[Any] = []
        self.listeners: list[Any] = []
        self.calls: list[bool] = []

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.snap)

    def report(self, on: bool, at: float) -> None:
        self.snap["ports"]["usb1"]["on"] = on
        for listener in self.port_listeners:
            listener(self, {"usb1": on}, at)

    async def set_port(self, port: str, on: bool) -> None:
        assert port == "usb1"
        self.calls.append(on)
        self.report(on, CLOCK["now"])


CLOCK = {"now": T0}


class Settings:
    """The watering controller as the Durst-Index reads it: its settings and its running cycle, read only."""

    def __init__(self, **cfg: Any) -> None:
        self.cfg = {"enabled": False, "duration_s": 120, "times": [], "every_days": 1, "first_date": None,
                    "timezone": "Europe/Berlin", "power_confirmed": False, **cfg}
        self.active: dict[str, Any] | None = None

    def slots(self, after: float, until: float) -> list[tuple[float, str]]:
        return []


class Rig:
    def __init__(self, *, power: bool = False, now: float = IN_WINDOW) -> None:
        CLOCK["now"] = now
        self.box = Box()
        self.guard = PortGuard("box-1")
        self.watering = Settings(power_confirmed=power)
        self.alerts = FakeAlerts()
        self.thirst = th.Thirst(self.box, guard=self.guard, demo=True, clock=lambda: CLOCK["now"], tz=BERLIN,
                                stage_source=lambda: STAGE, watering=self.watering, alerts=self.alerts)
        config.atomic_write(self.thirst.pot_path(), json.dumps(pot(at=now - 30 * DAY).as_dict()) + "\n")
        self.rate = th.et_rate_ml_h(MODEL, light=75, vpd=1.2, height_cm=None) / 1000 / 3600
        # 29 days of made-up history up to now (the series keeps 30); every poll of a test adds to it from there
        self.thirst.series = th.constant_series(self.rate, now - 29 * DAY, now)
        self.thirst.state["inputs"] = {"at": now, "rate_l_s": self.rate, "light": 75, "vpd": 1.2, "last_lit": 75}

    def name_pump(self, at: float) -> None:
        self.guard.assign("usb1", "pump", self.box.snapshot())
        record = json.loads(self.guard.path().read_text(encoding="utf-8"))
        record["devices_at"]["usb1"] = at
        config.atomic_write(self.guard.path(), json.dumps(record) + "\n")

    def weigh_full(self, hours_ago: float) -> None:
        self.thirst.log({"kind": "weigh", "kg": 8.4, "full": True}, at=CLOCK["now"] - hours_ago * HOUR)

    def measured_due(self) -> None:
        self.weigh_full(6 * 24)
        self.thirst.log({"kind": "check", "answer": "dry", "depth_cm": 5}, at=CLOCK["now"] - 60)

    def at_ti(self, ti: float, *, weigh: bool = True) -> None:
        """Weighed full long ago and weighed again a minute ago at the deficit of `ti` (measured)."""
        self.weigh_full(6 * 24)
        if weigh:
            self.thirst.log({"kind": "weigh", "kg": round(8.4 - ti / 100 * D_TRIG, 4)}, at=CLOCK["now"] - 60)

    def flow(self, ml_s: float = 0.75) -> None:
        for _ in range(2):
            self.thirst.log({"kind": "flow", "seconds": 60, "ml": 60 * ml_s}, at=CLOCK["now"] - HOUR)

    def verdict(self, duration_s: int = 120, **extra: Any) -> dict[str, Any]:
        self.box.snap = snap(**extra)
        return self.thirst.gate(CLOCK["now"], duration_s)

    def polls(self, pattern: list[bool], step: float = 60) -> None:
        for on in pattern:
            self.box.snap["ports"]["usb1"]["on"] = on
            asyncio.run(self.thirst.check(self.box.snapshot()))
            CLOCK["now"] += step


class FakeAlerts:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.sent.append({"title": title, "message": message, "priority": priority})
        return True


# ---------- USB power is not water flow ----------

def test_usb_power_without_a_confirmed_pump_switch_counts_no_water(isolated_state) -> None:  # type: ignore[no-untyped-def]
    """The live fact of 26.10.2026: USB 1 reported on for more than 23 h with the tank wet. Without the owner's pump
    switch confirmation a run is never water; it makes the check before it stale and widens the band."""
    rig = Rig(power=False)
    rig.name_pump(CLOCK["now"] - DAY)
    rig.measured_due()
    rig.polls([False, *([True] * 20), False])
    entries = [e for e in rig.thirst.entries() if e["kind"] in ("water", "pump_seen")]
    assert [(e["kind"], e.get("why")) for e in entries] == [("pump_seen", "power")]
    ledger = rig.thirst.ledger(stage=STAGE)
    assert ledger.waterings == [] and ledger.measured is None and ledger.unverified is not None
    assert ledger.band > MODEL("band_unknown_amount_ti") and ledger.confidence == "low"
    view = rig.thirst.view(rig.box.snapshot(), "de")
    assert "Wasserfluss nicht bestätigt: nicht gezählt" in view["events"][0]["text"]
    assert view["pump"]["counts"] == "nothing" and view["pump"]["power_text"]


def test_a_day_of_usb_power_is_said_and_blocks_but_counts_nothing(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=False)
    rig.name_pump(CLOCK["now"] - 2 * DAY)
    rig.at_ti(60)
    rig.polls([False, True], step=23.5 * HOUR)
    rig.box.snap["ports"]["usb1"]["on"] = True
    view = rig.thirst.view(rig.box.snapshot(), "de")
    assert "seit 24 h Strom" in view["pump"]["on_text"] and "nichts" in view["pump"]["on_text"]
    assert "pump_running" in {b["key"] for b in view["blocks"]}
    assert rig.thirst.ledger(stage=STAGE).waterings == []


def test_confirmed_power_without_a_flow_is_an_unknown_amount(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    rig.name_pump(CLOCK["now"] - DAY)
    rig.at_ti(90)
    rig.polls([False, *([True] * 20), False])
    water = [e for e in rig.thirst.entries() if e["kind"] == "water"]
    assert len(water) == 1 and water[0]["litres"] is None and water[0]["counted"] == "unknown"
    assert 19 <= water[0]["minutes"] <= 21
    ledger = rig.thirst.ledger(stage=STAGE)
    assert ledger.unknown_amount and ledger.hold is not None and ledger.ti < 5


def test_confirmed_power_and_a_calibrated_flow_count_litres_on_the_long_side(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    rig.name_pump(CLOCK["now"] - DAY)
    rig.flow(0.75)
    rig.at_ti(90)
    rig.polls([False, *([True] * 20), False])
    water = [e for e in rig.thirst.entries() if e["kind"] == "water"]
    # longest possible run: from the last poll seen off to the first seen off after it, 21 min; never minutes x a guess
    assert water[0]["counted"] == "flow" and water[0]["litres"] == pytest.approx(21 * 60 * 0.75 * 1.1 / 1000, abs=1e-3)
    ledger = rig.thirst.ledger(stage=STAGE)
    assert ledger.unknown_amount is False and ledger.hold is None and ledger.d_l < 0.9 * D_TRIG


def test_a_run_counts_only_from_the_owners_word_on(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    rig.at_ti(90)
    rig.polls([False, *([True] * 10), False])  # nobody named the pump: seen, not counted
    seen = [e for e in rig.thirst.entries() if e["kind"] == "pump_seen"]
    assert len(seen) == 1 and seen[0]["why"] == "device"
    rig.name_pump(CLOCK["now"] + 5 * 60)  # named five minutes into the next run
    rig.polls([False, *([True] * 15), False])
    water = [e for e in rig.thirst.entries() if e["kind"] == "water"]
    assert len(water) == 1 and 9 <= water[0]["minutes"] <= 11


def test_a_reported_usb_fragment_tracks_the_run_between_polls(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    rig.name_pump(CLOCK["now"] - DAY)
    rig.flow(1.0)
    rig.thirst.start(rig.box)
    asyncio.run(rig.thirst.check(rig.box.snapshot()))  # seen off at the poll
    start = CLOCK["now"]
    rig.box.report(True, start + 30)
    rig.box.report(False, start + 90)  # a short run reported between two polls
    water = [e for e in rig.thirst.entries() if e["kind"] == "water"]
    assert len(water) == 1 and water[0]["litres"] == pytest.approx(90 * 1.0 * 1.1 / 1000, abs=1e-3)
    rig.thirst.stop()
    assert rig.thirst.observe not in rig.box.port_listeners and rig.thirst.poll not in rig.box.listeners


# ---------- the flow calibration ----------

def test_the_flow_is_calibrated_by_two_jug_runs_within_10_percent(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig()
    assert rig.thirst.flow()["calibrated"] is False
    rig.thirst.flow_test({"seconds": 60, "ml": 45})
    assert not rig.thirst.flow()["calibrated"] and rig.thirst.flow()["one"] == pytest.approx(0.75)
    rig.thirst.flow_test({"seconds": "60", "ml": "60"})  # 33 % apart
    assert not rig.thirst.flow()["calibrated"] and rig.thirst.flow()["far"]
    rig.thirst.flow_test({"seconds": 60, "ml": 58})  # the last two: 60 and 58 ml
    found = rig.thirst.flow()
    assert found["calibrated"] and found["ml_s"] == pytest.approx(59 / 60)
    rig.thirst.flow_test({"reset": True})
    assert not rig.thirst.flow()["calibrated"]
    rig.thirst.flow_test({"seconds": 60, "ml": 45})
    rig.thirst.flow_test({"seconds": 60, "ml": 46})
    assert rig.thirst.flow()["calibrated"]
    CLOCK["now"] += 31 * DAY
    assert not rig.thirst.flow()["calibrated"]  # older than 30 days: measure again
    from vgrow.i18n import UserError
    with pytest.raises(UserError):
        rig.thirst.flow_test({"seconds": 2, "ml": 10})


# ---------- the need gate ----------

def test_the_gate_skips_without_a_pot_without_a_measurement_and_while_not_due(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig()
    rig.thirst.pot_path().unlink()
    assert rig.verdict() == {**rig.verdict(), "run": False, "reason": "no_pot"}
    rig = Rig()
    assert rig.verdict()["reason"] == "unknown"
    rig.weigh_full(4 * 24)  # only modelled since
    found = rig.verdict()
    assert found["run"] is False and found["reason"] == "unmeasured"
    rig = Rig()
    rig.at_ti(61.6)
    found = rig.verdict()
    assert found["run"] is False and found["reason"] == "not_due" and found["max_s"] is None
    assert found["text"]["de"] == "Topf noch nicht fällig: Durst-Index 62 %."
    assert found["text"]["en"] == "Pot not due yet: thirst index 62%."


@pytest.mark.parametrize("calibration", ["missing", "one", "expired", "unverified"])
def test_the_gate_skips_a_due_pot_without_current_verified_flow(isolated_state, calibration) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    if calibration == "one":
        rig.thirst.flow_test({"seconds": 60, "ml": 45})
    elif calibration == "expired":
        for _ in range(2):
            rig.thirst.log({"kind": "flow", "seconds": 60, "ml": 45}, at=CLOCK["now"] - 31 * DAY)
    elif calibration == "unverified":
        rig.flow(20)  # two identically mistyped legacy entries cannot authorise a run
    rig.measured_due()
    found = rig.verdict(600)
    assert found["run"] is False and found["max_s"] is None and found["reason"] == "no_flow"
    assert "kalibrieren" in found["text"]["de"] and "calibrate" in found["text"]["en"]


@pytest.mark.parametrize("seconds,ml", [(3600, 5), (5, 5000)])
def test_unusual_flow_needs_confirmation_for_each_measurement(isolated_state, seconds, ml) -> None:  # type: ignore[no-untyped-def]
    rig = Rig()
    for n in range(2):
        before = len(rig.thirst.entries())
        result = rig.thirst.flow_test({"seconds": seconds, "ml": ml})
        assert result["ok"] is False and result["ask"] == "flow"
        assert len(rig.thirst.entries()) == before  # asking never saves a calibration
        result = rig.thirst.flow_test({"seconds": seconds, "ml": ml, "confirm": True})
        assert result["ok"] and result["flow"]["calibrated"] is (n == 1)
    rig.thirst.flow_test({"reset": True})
    assert not rig.thirst.flow()["calibrated"]


def test_the_gate_requires_confirmed_power_and_skips_sub_confirmation_runs(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig()
    rig.flow()
    rig.measured_due()
    assert rig.verdict()["reason"] == "no_power"
    rig.watering.cfg["power_confirmed"] = True
    assert rig.verdict(19)["reason"] == "too_short"
    assert rig.verdict(20)["run"]


def test_the_gate_shortens_to_the_dose_with_a_calibrated_flow_and_never_lengthens(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    rig.flow(0.75)
    rig.at_ti(110)
    ledger = rig.thirst.ledger(stage=STAGE)
    litres = th.dose(ledger, MODEL, now=CLOCK["now"], stage_key="early_flower")["litres"]
    assert litres == pytest.approx(1.4)  # the early-flower cap of the stage card, 1400 ml
    long = rig.verdict(3600)
    expected = int(litres * 1000 / (0.75 * 1.1) - MODEL("stop_margin_s"))
    assert long["run"] and long["max_s"] == expected and long["reason"] == "shortened"
    assert "Höchstmenge der Phase" in long["text"]["de"]
    short = rig.verdict(60)
    assert short["run"] and short["max_s"] is None and short["reason"] == "full"  # never lengthened past 60 s


def test_the_gate_skips_in_a_wet_hold_and_for_every_guardrail(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True)
    rig.measured_due()
    assert not rig.verdict(sensors_age=900)["run"]
    assert not rig.verdict(online=False)["run"]
    rig.thirst._stage_source = lambda: {"key": "early_flower", "set_by": "estimate"}
    assert not rig.verdict()["run"] and "Phase" in rig.verdict()["text"]["de"]
    rig.thirst._stage_source = lambda: STAGE
    rig.thirst.log({"kind": "saucer", "water": True}, at=CLOCK["now"] - 30)
    found = rig.verdict()
    assert not found["run"] and found["reason"] == "hold"


def test_a_failing_gate_skips_the_slot_and_the_closed_gate_skips_everything(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig()

    def broken() -> dict[str, Any]:
        raise RuntimeError("broken")

    rig.box.snapshot = broken  # type: ignore[method-assign]
    found = rig.thirst.gate(CLOCK["now"], 60)
    assert found["run"] is False and found["reason"] == "error"
    assert th.closed_gate(0, 60)["run"] is False


# ---------- the gate on the watering controller's schedule (vgrow/watering.py, through its one hook) ----------

class PumpBox(Box):
    """The box of the controller tests: a confirmed report for every command."""

    def __init__(self) -> None:
        super().__init__()
        self.snap["ports"]["usb1"]["accessory"] = 0
        self.snap["ports"]["usb2"]["accessory"] = 0


def controller(rig: Rig) -> tuple[WateringController, list[float]]:
    mono = [100.0]
    rig.box.__class__ = PumpBox
    rig.name_pump(CLOCK["now"] - DAY)
    ctl = WateringController(rig.box, rig.guard, CommandBudget(lambda: CLOCK["now"]), clock=lambda: CLOCK["now"],
                             monotonic=lambda: mono[0])
    rig.box.port_listeners.append(ctl.observe)
    rig.watering = ctl
    rig.thirst.watering = ctl
    ctl.need_gate = rig.thirst.gate
    return ctl, mono


async def scheduled(ctl: WateringController, minute_ahead: int = 1, duration: int = 600) -> None:
    slot = datetime.fromtimestamp(CLOCK["now"], BERLIN) + timedelta(minutes=minute_ahead)
    await ctl.configure({"duration_s": duration, "times": [f"{slot:%H:%M}"], "every_days": 1,
                         "first_date": slot.date().isoformat(), "power_confirmed": True, "enabled": True})


def test_a_slot_of_a_pot_not_due_is_skipped_with_its_reason_and_never_caught_up(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True, now=local(2026, 10, 26, 10, 0, 30))
    ctl, mono = controller(rig)
    rig.at_ti(61.6)

    async def go() -> None:
        await scheduled(ctl)
        CLOCK["now"] += 60
        mono[0] += 60
        await ctl.tick()
        assert rig.box.calls == []
        skipped = ctl.events[-1]
        assert skipped["kind"] == "skipped" and skipped["reason"] == "thirst"
        assert skipped["detail"]["de"] == "Topf noch nicht fällig: Durst-Index 62 %."
        # the pot becomes due later the same day: the skipped slot is never caught up
        rig.thirst.log({"kind": "check", "answer": "dry", "depth_cm": 5}, at=CLOCK["now"])
        for _ in range(5):
            CLOCK["now"] += 600
            mono[0] += 600
            await ctl.tick()
        assert rig.box.calls == []
        await ctl.close()

    asyncio.run(go())


def test_a_due_pot_runs_its_slot_shortened_to_the_dose_and_the_watchdog_stops_it(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True, now=local(2026, 10, 26, 10, 0, 30))
    ctl, mono = controller(rig)
    rig.flow(0.75)
    rig.at_ti(110)

    async def go() -> None:
        await scheduled(ctl, duration=3600)
        CLOCK["now"] += 60
        mono[0] += 60
        await ctl.tick()
        assert rig.box.calls == [True] and ctl.active is not None
        allowed = ctl.active["duration_s"]
        assert allowed < 3600 and ctl.events[-1]["kind"] == "start" and "gekürzt" in ctl.events[-1]["detail"]["de"]
        CLOCK["now"] += allowed - 1
        mono[0] += allowed - 1
        await ctl.tick()
        assert rig.box.calls == [True]
        CLOCK["now"] += 1
        mono[0] += 1
        await ctl.tick()
        assert rig.box.calls == [True, False] and ctl.events[-1]["reason"] == "duration"
        await ctl.close()

    asyncio.run(go())


def test_uncalibrated_scheduled_slot_never_sends_on_or_catches_up(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True, now=local(2026, 10, 26, 10, 0, 30))
    ctl, mono = controller(rig)
    rig.measured_due()

    async def go() -> None:
        await scheduled(ctl)
        CLOCK["now"] += 60
        mono[0] += 60
        await ctl.tick()
        assert rig.box.calls == [] and ctl.active is None
        skipped = ctl.events[-1]
        assert skipped["kind"] == "skipped" and "calibrate" in skipped["detail"]["en"]
        rig.flow()
        await ctl.tick()
        assert rig.box.calls == [] and ctl.events[-1] == skipped
        await ctl.close()

    asyncio.run(go())


@pytest.mark.parametrize("answer", [{"run": True, "max_s": 7200}, {"run": True, "max_s": "x"}, {"run": True, "max_s": 0},
                                    {"run": "yes"}, {}, None])
def test_the_gate_can_never_lengthen_or_start_a_run_and_garbage_skips(isolated_state, answer) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True, now=local(2026, 10, 26, 10, 0, 30))
    ctl, mono = controller(rig)

    def gate(slot_at: float, duration_s: int) -> Any:
        if answer is None:
            raise RuntimeError("no answer")
        return answer

    ctl.need_gate = gate

    async def go() -> None:
        await scheduled(ctl, duration=120)
        CLOCK["now"] += 60
        mono[0] += 60
        await ctl.tick()
        if answer == {"run": True, "max_s": 7200}:
            assert rig.box.calls == [True] and ctl.active["duration_s"] == 120  # not lengthened
        else:
            assert rig.box.calls == [] and ctl.events[-1] == {**ctl.events[-1], "kind": "skipped", "reason": "thirst"}
        await ctl.close()

    asyncio.run(go())


def test_einmal_giessen_stays_the_owners_and_is_never_asked(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(power=True, now=local(2026, 10, 26, 10, 0, 30))
    ctl, mono = controller(rig)
    rig.at_ti(20)
    asked: list[float] = []
    ctl.need_gate = lambda slot, duration: asked.append(slot) or {"run": False}

    async def go() -> None:
        await ctl.configure({"duration_s": 30, "times": [], "every_days": 1, "first_date": None,
                             "power_confirmed": True, "enabled": False})
        await ctl.start()
        assert rig.box.calls == [True] and asked == []
        await ctl.stop()
        await ctl.close()

    asyncio.run(go())
    note = rig.thirst.view(rig.box.snapshot(), "de")["manual"]
    assert note["warn"] and "nicht fällig" in note["text"] and "20 %" in note["text"]


# ---------- the forecast and the plan by stage ----------

def test_the_forecast_says_when_and_how_much_with_its_uncertainty(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig(now=local(2026, 10, 22, 18))  # 0.68 l to go at 17 ml/h: due Saturday about 10:15, inside the round
    rig.at_ti(60)
    view = rig.thirst.view(rig.box.snapshot(), "de")
    forecast = view["forecast"]
    hours_left = 0.4 * D_TRIG / (rig.rate * 3600)
    assert forecast["kind"] == "due" and forecast["due_at"] == pytest.approx(CLOCK["now"] + hours_left * HOUR,
                                                                             abs=th.FORECAST_STEP_S)
    assert forecast["water_at"] == forecast["due_at"]  # inside the round: watered when due
    assert forecast["early_at"] < forecast["due_at"] < forecast["late_at"]
    assert forecast["litres"] == pytest.approx(1.4)
    assert forecast["text"].startswith("Nächstes Gießen voraussichtlich Samstag, 24.10., morgens, etwa 1,4 l.")
    assert "frühestens" in forecast["text"] and "spätestens" in forecast["text"]
    assert "Davor prüfen: Finger 5 cm tief" in forecast["text"]
    en = rig.thirst.view(rig.box.snapshot(), "en")["forecast"]["text"]
    assert en.startswith("Next watering expected Saturday, Oct 24, in the morning, about 1.4 l.")


def test_a_pot_due_at_night_is_watered_at_the_next_round(isolated_state) -> None:  # type: ignore[no-untyped-def]
    """A farmer does not water at midnight: due outside the daily round, the forecast names the round after it."""
    rig = Rig(now=local(2026, 10, 22, 8))  # due Saturday about 00:15, 24 h light, no light timer: the round is 09:00
    rig.at_ti(60)
    forecast = rig.thirst.view(rig.box.snapshot(), "de")["forecast"]
    assert forecast["water_at"] == local(2026, 10, 24, 9) > forecast["due_at"]
    assert forecast["text"].startswith("Fällig voraussichtlich Samstag, 24.10., nachts. Gießen dann zur nächsten "
                                       "Runde, Samstag, 24.10., morgens, etwa 1,4 l.")
    en = rig.thirst.view(rig.box.snapshot(), "en")["forecast"]["text"]
    assert en.startswith("Due about Saturday, Oct 24, at night. Then water at the next round, Saturday, Oct 24, in "
                         "the morning, about 1.4 l.")

    # with the light timer the round is lights-on plus 1 h by its own switch times, 12 h long, across the DST change
    timer = {"enabled": True, "on_time": "06:00", "off_time": "00:00", "level": 75, "timezone": "Europe/Berlin"}
    plan = th.LightPlan(timer, 75, last_seen=75, start=CLOCK["now"], end=CLOCK["now"] + 5 * DAY, tz=BERLIN)
    assert plan.kind == "timer"
    assert rig.thirst._water_at(MODEL, plan, local(2026, 10, 24, 3)) == local(2026, 10, 24, 7)
    assert rig.thirst._water_at(MODEL, plan, local(2026, 10, 24, 11)) == local(2026, 10, 24, 11)
    assert rig.thirst._water_at(MODEL, plan, local(2026, 10, 24, 20)) == local(2026, 10, 25, 7)  # the 25 h day


def test_the_forecast_counts_the_light_timer_nights(isolated_state) -> None:  # type: ignore[no-untyped-def]
    held = Rig(now=local(2026, 10, 22, 8))
    held.at_ti(60)
    lit = held.thirst.view(held.box.snapshot(), "de")["forecast"]["due_at"]

    class Timer:
        cfg = {"enabled": True, "on_time": "06:00", "off_time": "18:00", "level": 75, "timezone": "Europe/Berlin"}

    timed = Rig(now=local(2026, 10, 22, 8))
    timed.thirst.light = Timer()
    timed.at_ti(60)
    view = timed.thirst.view(timed.box.snapshot(), "de")
    assert view["forecast"]["due_at"] > lit + 12 * HOUR  # 12 h nights at a tenth of the rate
    assert view["forecast"]["light"] == "timer"
    assert any("Lichttimer 06:00 bis 18:00" in b["text"] and b["measured"] for b in view["plan"]["basis"])


def test_the_light_timer_edges_follow_the_dst_change() -> None:
    cfg = {"on_time": "02:30", "off_time": "20:00"}
    edges = th.light_edges(cfg, local(2026, 10, 24, 12), local(2026, 10, 26, 12), BERLIN)
    on_25 = [at for at, on in edges if on and th.local_day(at, BERLIN) == date(2026, 10, 25)]
    assert on_25 == [local(2026, 10, 25, 2, 30, fold=0)]  # the doubled 02:30 counts at its first occurrence
    spring = th.light_edges(cfg, local(2026, 3, 28, 12), local(2026, 3, 30, 12), BERLIN)
    assert not [at for at, on in spring if on and th.local_day(at, BERLIN) == date(2026, 3, 29)]  # 02:30 is skipped


def test_a_measured_due_pot_forecasts_now_and_a_hold_forecasts_the_drying(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig()
    rig.measured_due()
    forecast = rig.thirst.view(rig.box.snapshot(), "de")["forecast"]
    assert forecast["kind"] == "measured" and "gemessen fällig" in forecast["text"] and "Davor prüfen" not in forecast["text"]
    wet = Rig()
    wet.at_ti(40)  # weighed a minute ago: 0.68 l missing
    wet.thirst.log({"kind": "water", "litres": 2.0, "origin": "hand"}, at=CLOCK["now"] - 30)  # 1.3 l too much
    forecast = wet.thirst.view(wet.box.snapshot(), "de")["forecast"]
    assert forecast["kind"] == "hold" and forecast["text"].startswith("Erst muss der Topf abtrocknen")
    assert "frühestens" in forecast["text"] and "spätestens" in forecast["text"]
    forecast_en = wet.thirst.view(wet.box.snapshot(), "en")["forecast"]
    assert "earliest" in forecast_en["text"] and "latest" in forecast_en["text"]
    assert any(not b["measured"] and "today's height and light plan" in b["text"]
               for b in wet.thirst.view(wet.box.snapshot(), "en")["plan"]["basis"])


def test_the_plan_by_stage_follows_the_stage_card_and_says_what_is_measured(isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow.plant import PlantLog

    now = local(2026, 10, 26, 23, 20)
    log = PlantLog()
    log.set_stage("early_flower", since="2026-10-24")  # the owner's stage of 24.10.2026
    rig = Rig(now=now)
    rig.thirst.plant_log = log
    rig.thirst._stage_source = None
    rig.at_ti(40)
    rig.box.snap = snap(light={"level": 100}, climate={"probe_vpd": 1.73})
    plan = rig.thirst.view(rig.box.snapshot(), "de")["plan"]
    weeks = plan["weeks"]
    assert [w["stage"] for w in weeks] == ["early_flower", "early_flower", "mid_flower", "mid_flower", "late_flower",
                                           "late_flower", "harvest"]
    assert weeks[0]["now"] and not weeks[0]["stage_estimated"] and weeks[2]["stage_estimated"]
    assert weeks[0]["from"] == "2026-10-26" and weeks[2]["from"] == "2026-11-09"
    assert all(w["d_trig_l"] == pytest.approx(D_TRIG) for w in weeks)  # 0.5 x 0.3 x 11.4 l in every flower week
    assert all(w["cap_l"] == pytest.approx(1.4) and w["cap_by"] == "stage" for w in weeks)
    assert weeks[0]["vpd"] == 1.73 and weeks[0]["vpd_measured"]  # now: the probe
    assert weeks[2]["vpd"] == pytest.approx(1.4)  # later: the middle of mid flower's band (1.2 to 1.6, MAN)
    use = th.et_rate_ml_h(MODEL, light=100, vpd=1.73, height_cm=None) * 24 / 1000
    assert weeks[0]["use_l_day"] == pytest.approx(use, rel=0.01)
    assert weeks[0]["every_days"] == pytest.approx(D_TRIG / use, abs=0.1)
    measured = {b["text"] for b in plan["basis"] if b["measured"]}
    estimated = {b["text"] for b in plan["basis"] if not b["measured"]}
    assert "Topf 11,4 l (eingetragen)" in measured and "Licht 100 % rund um die Uhr (gemessen, kein Lichttimer)" in measured
    assert any(text.startswith("Phasenwechsel nach den Tagen der Phasenkarte ab 7.11.") for text in estimated)
    assert any("Startwert 20 ml" in text for text in estimated)


def test_the_plan_takes_the_seedling_and_the_late_dryback_from_the_model(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = Rig()
    rig.thirst._stage_source = lambda: {"key": "seedling", "set_by": "manual", "since": "2026-10-20"}
    rig.at_ti(10)
    plan = rig.thirst.view(rig.box.snapshot(), "de")["plan"]
    assert plan["weeks"][0]["d_trig_l"] == pytest.approx(0.4 * 0.3 * OWNER_POT_L)
    assert plan["weeks"][0]["cap_l"] == pytest.approx(0.2)  # the seedling preset, 200 ml
    late = Rig()
    late.thirst._stage_source = lambda: {"key": "late_flower", "set_by": "manual", "since": "2026-10-20"}
    config.atomic_write(late.thirst.pot_path(), json.dumps(pot(at=CLOCK["now"] - 30 * DAY, dryback=True).as_dict()) + "\n")
    late.at_ti(10)
    weeks = late.thirst.view(late.box.snapshot(), "de")["plan"]["weeks"]
    assert weeks[0]["f_trig"] == 0.6 and weeks[0]["d_trig_l"] == pytest.approx(0.6 * 0.3 * OWNER_POT_L)


# ---------- DST and epoch timing ----------

def test_dst_a_25_hour_and_a_23_hour_day() -> None:
    assert local(2026, 10, 26, 0) - local(2026, 10, 25, 0) == 25 * HOUR
    assert local(2026, 3, 30, 0) - local(2026, 3, 29, 0) == 23 * HOUR
    before = th.next_round(local(2026, 10, 24, 8), MODEL, lights_edge=None, tz=BERLIN)
    after = th.next_round(local(2026, 10, 25, 8), MODEL, lights_edge=None, tz=BERLIN)
    assert (before, after) == (local(2026, 10, 24, 9), local(2026, 10, 25, 9)) and after - before == 25 * HOUR
    spring = th.next_round(local(2026, 3, 29, 8), MODEL, lights_edge=None, tz=BERLIN) - local(2026, 3, 28, 9)
    assert spring == 23 * HOUR >= MODEL("min_interval_s")
    water = local(2026, 10, 24, 9)
    ledger = fold([ev("weigh", water - 6 * DAY, kg=8.4, full=True), ev("water", water, litres=0.5, origin="hand"),
                   ev("check", local(2026, 10, 25, 9, 5), answer="dry", depth_cm=5)], local(2026, 10, 25, 9, 10))
    assert not {"interval", "today", "settle"} & keys(decide(ledger, local(2026, 10, 25, 9, 10)))
    ser = series(start=local(2026, 10, 24, 0), end=local(2026, 10, 27, 0))
    litres, _ = ser.between(local(2026, 10, 25, 0), local(2026, 10, 26, 0))
    assert litres == pytest.approx(25 * RATE_L_H)


def test_dst_replay_across_25_10_polls_keep_the_index_smooth(isolated_state) -> None:  # type: ignore[no-untyped-def]
    clock = {"now": local(2026, 10, 24, 20)}
    ledger = th.Thirst(None, demo=True, clock=lambda: clock["now"], tz=BERLIN, stage_source=lambda: STAGE)
    config.atomic_write(ledger.pot_path(), json.dumps(pot(at=clock["now"] - DAY).as_dict()) + "\n")
    ledger.log({"kind": "weigh", "kg": 8.4, "full": True}, at=clock["now"] - HOUR)
    last_ti, steps = None, []
    end = local(2026, 10, 25, 8)
    while clock["now"] < end:
        asyncio.run(ledger.check(snap(light={"level": 75})))
        ti = ledger.ledger(stage=STAGE).ti
        if last_ti is not None:
            steps.append(ti - last_ti)
        last_ti = ti
        clock["now"] += 90
    assert max(steps) < 0.2 and min(steps) >= 0
    hours = (end - local(2026, 10, 24, 20)) / HOUR
    assert hours == 13  # 20:00 to 08:00 across the change is 13 hours, not 12
    rate = th.et_rate_ml_h(MODEL, light=75, vpd=1.2, height_cm=None) / 1000
    assert ledger.ledger(stage=STAGE).d_l == pytest.approx(rate * (hours + 1), rel=0.02)


def test_mist_on_the_probe_moves_the_modelled_et_less_than_5_percent(isolated_state) -> None:  # type: ignore[no-untyped-def]
    def run(mist: bool) -> float:
        clock = {"now": T0}
        ledger = th.Thirst(None, demo=True, clock=lambda: clock["now"], stage_source=lambda: STAGE)
        for minute in range(0, 4 * 60, 2):
            misty = mist and 60 <= minute < 180
            climate = {"probe_humidity": 85.0, "probe_vpd": 0.45} if misty else {"probe_humidity": 55.0,
                                                                                 "probe_vpd": 1.2}
            asyncio.run(ledger.check(snap(climate={**climate, "humidity": 50.0})))
            clock["now"] += 120
        return ledger.series.between(T0, clock["now"])[0]

    plain, misty = run(False), run(True)
    assert abs(misty - plain) / plain < 0.05


def test_an_unknown_light_level_keeps_the_last_one_and_is_a_gap(isolated_state) -> None:  # type: ignore[no-untyped-def]
    """Review N3: no light report is never night; it holds the last level and widens the band."""
    clock = {"now": T0}
    ledger = th.Thirst(None, demo=True, clock=lambda: clock["now"], stage_source=lambda: STAGE)
    for _ in range(10):
        asyncio.run(ledger.check(snap(light={"level": 75})))
        clock["now"] += 90
    lit = ledger.series.between(T0, clock["now"])[0]
    start = clock["now"]
    for _ in range(10):
        asyncio.run(ledger.check(snap(light={"level": None})))
        clock["now"] += 90
    unknown = ledger.series.between(start, clock["now"])[0]
    assert unknown == pytest.approx(lit, rel=0.05) and ledger.series.gaps


# ---------- escalation ----------

def escalation_rig(ti: float, *, measured: bool = False) -> Rig:
    rig = Rig(now=IN_WINDOW)
    full = IN_WINDOW - ti / 100 * D_TRIG / rig.rate
    rig.thirst.log({"kind": "weigh", "kg": 8.4, "full": True}, at=full)
    if measured:
        rig.thirst.log({"kind": "check", "answer": "dry", "depth_cm": 5}, at=IN_WINDOW - 60)
    return rig


def test_no_answer_at_100_pushes_after_3_h(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = escalation_rig(101)
    for _ in range(int(3 * HOUR / 90) + 2):
        asyncio.run(rig.thirst.check(snap()))
        CLOCK["now"] += 90
    asks = [a for a in rig.alerts.sent if a["priority"] == 3]
    assert len(asks) == 1 and "Seit 3" in asks[0]["message"] and "Finger 5 cm" in asks[0]["message"]


def test_never_a_push_on_a_model_index_without_a_check_request_first(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = escalation_rig(125)
    decision = asyncio.run(rig.thirst.check(snap()))
    assert decision.kind == "ASK" and rig.alerts.sent == []
    CLOCK["now"] += 90
    decision = asyncio.run(rig.thirst.check(snap()))
    assert decision.kind == "ALERT" and decision.reason == "ti_high"
    assert [a["priority"] for a in rig.alerts.sent] == [4] and "Der Topf braucht dich" in rig.alerts.sent[0]["message"]


def test_a_block_at_an_index_above_100_for_12_h_is_an_alert_and_silence_never_waters(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = escalation_rig(101, measured=True)
    dry = snap(climate={"water_state": "dry"})
    first = asyncio.run(rig.thirst.check(dry))
    assert first.kind == "BLOCKED" and first.reason == "tank"
    CLOCK["now"] += 12 * HOUR + 60
    later = asyncio.run(rig.thirst.check(dry))
    assert later.kind == "ALERT" and later.reason == "blocked_long"
    assert len([a for a in rig.alerts.sent if "gesperrt" in a["message"]]) == 1
    assert rig.box.calls == []  # nothing was switched, ever


def test_the_blocked_alert_counts_its_hours_from_the_block_not_from_the_measurement(isolated_state) -> None:  # type: ignore[no-untyped-def]
    rig = escalation_rig(101)
    rig.thirst.log({"kind": "check", "answer": "dry", "depth_cm": 5}, at=IN_WINDOW - 5 * HOUR)
    dry = snap(climate={"water_state": "dry"})
    assert asyncio.run(rig.thirst.check(dry)).reason == "tank"
    CLOCK["now"] += 12 * HOUR + 60
    later = asyncio.run(rig.thirst.check(dry))
    assert later.reason == "blocked_long" and later.since == pytest.approx(IN_WINDOW)
    ledger = rig.thirst.ledger(stage=STAGE)
    assert "Seit 12 h" in th.decision_text(later, ledger, CLOCK["now"], "de")  # the block, not the check 17 h ago
    assert "for 12 h" in th.decision_text(later, ledger, CLOCK["now"], "en")


# ---------- the owner's inputs ----------

def test_the_inputs_validate_and_ask_twice_where_due(isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow.i18n import UserError

    clock = {"now": T0}
    ledger = th.Thirst(None, demo=True, clock=lambda: clock["now"])
    with pytest.raises(UserError):
        ledger.check_answer({"answer": "dry"})
    asked = ledger.set_pot({"volume_l": "11,4", "diameter_cm": "40", "height_cm": "40"})
    assert asked["ask"] == "mismatch" and "20 %" in asked["text"]
    assert ledger.set_pot({"volume_l": "11,4", "diameter_cm": "26", "height_cm": "24", "saucer": True})["ok"]
    first = ledger.pot()
    clock["now"] += 600
    kept = ledger.set_pot({"volume_l": "11,4", "diameter_cm": "26", "height_cm": "24", "saucer": True, "wick": True})
    assert kept["ok"] and kept["kept"] and ledger.pot().id == first.id and ledger.pot().set_at == first.set_at  # S4
    assert ledger.pot().wick is True
    assert ledger.set_pot({"volume_l": "15"})["ask"] == "reset"
    assert ledger.set_pot({"volume_l": "15", "confirm": True})["ok"] and ledger.pot().volume_l == 15
    with pytest.raises(UserError):
        ledger.set_pot({"volume_l": "0,2", "confirm": True})
    assert ledger.manual({"litres": "2,5"})["ask"] == "large"
    assert ledger.manual({"litres": "2,5", "large": True})["ok"]
    with pytest.raises(UserError):
        ledger.manual({"litres": "0,5", "at": clock["now"] + 2 * HOUR})
    with pytest.raises(UserError):
        ledger.manual({"litres": "0,5", "at": clock["now"] - 8 * DAY})
    with pytest.raises(UserError):
        ledger.check_answer({"answer": "soggy"})
    assert ledger.check_answer({"answer": "dry", "depth_cm": 5})["ok"]
    with pytest.raises(UserError):
        ledger.arrived({"event": "nope", "answer": "no"})
    with pytest.raises(UserError):
        ledger.saucer({"water": True, "event": "nope"})  # review N4: an event id must exist
    with pytest.raises(UserError):
        ledger.weigh({"kg": "150"})
    assert ledger.weigh({"kg": "8,4", "full": True})["ok"] and ledger.refilled({"litres": 2, "feed": True})["ok"]
    assert ledger.saucer({"water": False})["ok"]
    kinds = [e["kind"] for e in ledger.entries()]
    assert kinds == ["pot", "pot", "water", "check", "weigh", "refill", "saucer"]


def test_every_action_maps_to_a_method_and_collides_with_no_existing_action() -> None:
    server = (REPO / "vgrow" / "server.py").read_text(encoding="utf-8")
    existing = set(re.findall(r'action (?:==|in) \(?"(\w+)"', server)) | set(re.findall(r'"(\w+)"', "".join(
        re.findall(r"action in \(([^)]*)\)", server))))
    assert existing and not existing & set(th.ACTIONS)
    for action, method in th.ACTIONS.items():
        assert action.startswith("thirst_") and callable(getattr(th.Thirst, method)), action
    js = (REPO / "web" / "thirst.js").read_text(encoding="utf-8")
    assert set(re.findall(r'action: "(\w+)"', js)) == set(th.ACTIONS)
    assert th.check_catalog() == []


# ---------- the server ----------

def test_the_server_answers_thirst_and_takes_the_inputs(isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow.demo import DemoBox
    from vgrow.server import COOKIE, make_app

    async def run() -> None:
        box = DemoBox()
        ledger = th.Thirst(box, demo=True)
        app = make_app(box, Alerts(), "k", thirst=ledger)
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            first = await (await client.get("/api/thirst")).json()
            assert first["pot"] is None and first["decision"]["reason"] == "no_pot" and first["manual"]["warn"]
            state = await (await client.get("/api/state")).json()
            assert state["thirst"]["decision"]["kind"] == "ASK"
            resp = await client.post("/api/cmd", json={"action": "thirst_pot", "volume_l": OWNER_POT_L})
            assert resp.status == 200 and (await resp.json())["ok"]
            again = await client.post("/api/cmd", json={"action": "thirst_pot", "volume_l": 12})
            body = await again.json()
            assert again.status == 409 and body["ask"] == "reset" and body["error"]
            for action, payload in (("thirst_check", {"answer": "dry"}), ("thirst_weigh", {"kg": 8.4, "full": True}),
                                    ("thirst_manual", {"litres": 0.3}), ("thirst_refill", {"litres": 1}),
                                    ("thirst_saucer", {"water": False}), ("thirst_flow", {"seconds": 60, "ml": 45})):
                resp = await client.post("/api/cmd", json={"action": action, **payload})
                assert resp.status == 200, (action, await resp.text())
            bad = await client.post("/api/cmd", json={"action": "thirst_weigh", "kg": 500})
            assert bad.status == 400
            en = await (await client.get("/api/thirst", headers={"X-Lang": "en"})).json()
            assert en["decision"]["text"] and "Durst" not in en["decision"]["text"]
            assert box.ports["usb1"] is False  # nothing switched

    asyncio.run(run())


def test_hankbox_hands_the_brief_and_the_server_without_thirst_still_serves(isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow.demo import DemoBox
    from vgrow.server import COOKIE, HankBox, make_app

    box = DemoBox()
    brief = HankBox(box, None, thirst=th.Thirst(box, demo=True)).snapshot()["thirst"]
    assert brief["configured"] is False and brief["text"]["de"] and brief["text"]["en"]

    async def run() -> None:
        async with TestClient(TestServer(make_app(box, Alerts(), "k"))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            assert (await client.get("/api/thirst")).status == 404
            assert "thirst" not in await (await client.get("/api/state")).json()
            refused = await client.post("/api/cmd", json={"action": "thirst_check", "answer": "dry"})
            assert refused.status == 409

    asyncio.run(run())


# ---------- Hank ----------

def test_hank_imports_nothing_of_the_ledger_and_the_ledger_nothing_of_hank() -> None:
    pattern = re.compile(r"vgrow\.thirst\b|from \.\.thirst\b|from vgrow import .*\bthirst\b")
    for path in (REPO / "vgrow" / "hank").rglob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name
    own = (REPO / "vgrow" / "thirst.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import) .*\bhank\b", own, re.M)
    assert not re.search(r"^\s*(from|import) .*\bwatering\b", own, re.M)  # the timed controller is only handed in


def hank_live(brief: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = snap()
    if brief is not None:
        snapshot["thirst"] = brief
    return {"snapshot": snapshot, "plant_view": {"plant": {"strain": "Ice Cream Cake Automatic"}}, "ports": {},
            "now": datetime.now().isoformat()}


def brief_of(kind: str, reason: str = "check", **extra: Any) -> dict[str, Any]:
    return {"configured": True, "ti": 92.0, "kind": kind, "reason": reason, "measured": False,
            "text": {"de": "Durst-Index 92 %. Bald fällig.", "en": "Thirst index 92%. Due soon."},
            "why": {"de": ["Nur gerechnet."], "en": ["Only modelled."]},
            "check": {"de": "Finger 5 cm tief.", "en": "Finger 5 cm deep."}, "blocks": [],
            "forecast": {"de": "Nächstes Gießen voraussichtlich morgen, mittags, etwa 1,4 l.",
                         "en": "Next watering expected tomorrow, around midday, about 1.4 l."},
            "planned_l": 1.4, "cap_l": 1.4, "pump_amount": "unknown", "schedule_note": {"de": None, "en": None},
            **extra}


def test_hank_answers_the_four_watering_questions_from_the_brief() -> None:
    from vgrow.hank import thirst_report as tr
    from vgrow.hank.livestate import collect

    state = collect(hank_live(brief_of("ASK")))
    for question in ("Muss ich gießen?", "Wann gieße ich?", "Wie viel gieße ich?", "Der Tank ist voll, soll ich gießen?",
                     "Soll ich heute gießen?", "Braucht die Pflanze Wasser?", "Do I need to water?",
                     "When should I water?", "How much should I water?", "The tank is full, should I water?",
                     "Muss ich giesen?", "Muss ich gieen?", "Wann giese ich?", "The tank is full, sholud I water?"):
        assert tr.asks(question, (), state), question
    for question in ("Wie gieße ich richtig?", "How do I water properly?", "Was ist der Durst-Index?",
                     "Wie wird der Durst-Index berechnet?", "How much water does the tank hold?",
                     "Wie oft sol ich giesen wenn die erde trocken is?", "Habe ich zu viel gegossen?",
                     "Wurde heute schon gegossen?", "War die Pumpe heute schon an?", "Ist die Luftfeuchte okay?",
                     "Muss ich nach Gießen fahren?", "Should I tip the waiter?"):
        assert not tr.asks(question, (), state), question

    class Name:
        def __init__(self, entity: str) -> None:
            self.entity, self.kind = entity, "plant"

    assert not tr.asks("Wie oft muss ich meine Gurke gießen?", [Name("cucumber")], state)
    assert tr.asks("Muss ich die Ice Cream Cake gießen?", [Name("ice_cream_cake")], state)
    lines = tr.answer_text(state, "de", "Der Tank ist voll, soll ich gießen?").splitlines()
    assert lines[0] == "Ein voller Tank ist für mich nie ein Grund zu gießen: ob gegossen wird, sagt der Topf."
    assert lines[1] == ("Erst prüfen. Durst-Index 92 % (wie viel Wasser dem Topf fehlt; 100 heißt fällig). Bald "
                        "fällig.")  # the word explained where it first appears (review S5)
    assert lines[2].startswith("Wann: Nächstes Gießen voraussichtlich morgen")
    assert lines[-1] == "Die Antwort ist ein Tipp auf der Karte Durst-Index unter Automatik."  # ASK asked already
    when = tr.answer_text(state, "de", "Wann gieße ich?").splitlines()
    assert when[1].startswith("Wann: ")
    amount = tr.answer_text(state, "de", "Wie viel gieße ich?")
    assert ("Wie viel: das sagt erst die Prüfung. Voraussichtlich etwa 1,4 l, so viel erlaubt die Phase je Gießen "
            "höchstens.") in amount
    below = tr.answer_text(collect(hank_live(brief_of("ASK", planned_l=0.85))), "de", "Wie viel gieße ich?")
    assert "Voraussichtlich etwa 0,85 l, höchstens 1,4 l je Gießen." in below
    waiting = tr.answer_text(collect(hank_live(brief_of("WAIT", "not_due"))), "de", "Muss ich gießen?")
    assert waiting.startswith("Nein, noch nicht.") and waiting.endswith("Selbst prüfen, ein Tipp genügt. Finger 5 cm "
                                                                         "tief.")
    assert "Mit der Pumpe ist die Menge unbekannt" in amount
    en = tr.answer_text(collect(hank_live(brief_of("ASK"))), "en", "The tank is full, should I water?")
    assert en.startswith("A full tank is never a reason for me to water") and "Thirst index 92% (how much" in en
    assert tr.answer_text(collect(hank_live(None)), "de").startswith("Der Durst-Index meldet sich gerade nicht")
    # measured due, every block clear, outside the round: yes, at the round (review N8), never "no" before the time
    round_brief = brief_of("WAIT", "round", text={"de": "Durst-Index 104 %, gemessen. Gießen zur nächsten Runde, "
                                                        "morgen 09:00.", "en": "Thirst index 104%, measured. Water at "
                                                                               "the next round, tomorrow 9:00 AM."})
    said = tr.answer_text(collect(hank_live(round_brief)), "de", "Muss ich gießen?")
    assert said.startswith("Ja, zur nächsten Runde. Durst-Index 104 %")
    assert tr.answer_text(collect(hank_live(round_brief)), "en", "Do I need to water?").startswith(
        "Yes, at the next round. Thirst index 104%")
    assert tr.check() == []


def test_the_router_sends_the_four_questions_to_thirst_and_keeps_the_pump_history(isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow.hank import core

    async def go() -> list[tuple[str, str]]:
        hank = core.Hank(live=lambda lang: hank_live(brief_of("ASK")), index=core.shared_index())
        try:
            out = []
            for question in ("Muss ich gießen?", "Wann gieße ich?", "Wie viel gieße ich?",
                             "Der Tank ist voll, soll ich gießen?", "Wurde heute schon gegossen?"):
                got = await hank.ask_full(question, client_id="t", lang="de", model=False)
                out.append((got.route.name, got.answer.text))
            return out
        finally:
            await hank.stop()

    found = asyncio.run(go())
    assert [route for route, _ in found] == ["thirst", "thirst", "thirst", "thirst", "pump_history"]
    assert found[0][1].startswith("Erst prüfen. Durst-Index 92 %") and found[3][1].startswith("Ein voller Tank")


def test_hank_today_line_traffic_light_and_the_next_slot_note() -> None:
    from vgrow.hank import nudges, pump_report, today
    from vgrow.hank import thirst_report as tr

    asking = hank_live(brief_of("ASK"))
    assert tr.today_lines(asking, "de") == ["Gießen: Durst-Index 92 %. Bald fällig."]
    assert tr.today_lines(hank_live(brief_of("WAIT", "not_due")), "de") == []
    assert tr.light(asking, nudges.Assessment).level == "yellow"
    assert tr.light(hank_live(brief_of("WAIT", "not_due")), nudges.Assessment) is None
    lines, _ = today.lines(asking, "de")
    assert "Gießen: Durst-Index 92 %. Bald fällig." in lines
    note = {"de": "Nächster Gießtermin heute 09:00: entfällt, wenn sich bis dahin nichts ändert. Topf noch nicht "
                  "fällig: Durst-Index 62 %.", "en": "Next watering slot today 9:00 AM: skipped."}
    live = hank_live(brief_of("WAIT", "not_due", schedule_note=note))
    live["snapshot"]["watering"] = {"status": "scheduled", "settings": {"enabled": True, "duration_s": 120},
                                    "next_at": T0 + HOUR, "remaining_s": None}
    pump_report.register()
    text = pump_report.answer(live, "de", answer_id="a").text
    assert "Nächster geplanter Lauf" in text and "Bedarfsprüfung: Nächster Gießtermin heute 09:00" in text


# ---------- the demo, the glossary, the files ----------

@pytest.mark.parametrize("scene,kind,reason", [
    ("nopot", "ASK", "no_pot"), ("pot", "ASK", "first_check"), ("waiting", "WAIT", "not_due"),
    ("ask", "ASK", "check"), ("due", "PROPOSE", "hand"), ("wethold", "WAIT", "hold"),
    ("skipped", "WAIT", "not_due"), ("shortened", "PROPOSE", "hand"),
])
def test_the_demo_seeds_each_scene_of_the_card(scene: str, kind: str, reason: str, isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow.demo import DemoBox, seed_demo_thirst
    from vgrow.light_schedule import LightSchedule
    from vgrow.plant import PlantLog

    async def go() -> dict[str, Any]:
        box = DemoBox()
        guard = PortGuard("box-1")
        watering = WateringController(box, guard)
        thirst = th.Thirst(box, plant_log=PlantLog(), guard=guard, demo=True, watering=watering,
                           light=LightSchedule(box))
        assert await seed_demo_thirst(thirst, box, watering, guard, scene=scene) == scene
        await thirst.check(box.snapshot())
        view = thirst.view(box.snapshot(), "de")
        if scene in ("skipped", "shortened"):
            assert watering.cfg["enabled"] and watering.cfg["power_confirmed"]
            verdict = thirst.gate(0, watering.cfg["duration_s"])
            assert verdict["reason"] == ("not_due" if scene == "skipped" else "shortened")
        await watering.close()
        return view

    view = asyncio.run(go())
    assert view["decision"]["kind"] == kind and view["decision"]["reason"] == reason
    assert (view["pot"] is None) == (scene == "nopot")


def test_the_glossary_explains_durst_index_vpd_and_et_in_plain_german() -> None:
    data = json.loads((REPO / "kartei" / "glossary" / "glossary.json").read_text(encoding="utf-8"))
    by_key = {t["key"]: t for t in data["terms"]}
    assert "wie viel Wasser dem Topf fehlt" in by_key["durst-index"]["text"]
    assert "Gegossen wird erst nach einer Messung" in by_key["durst-index"]["text"]  # review S4: no "100 heißt gießen"
    assert "Verdunstung über Blätter und Erde" in by_key["et"]["text"]
    assert "Dampfdruckdefizit" in by_key["vpd"]["text"]
    for key in ("G34", "G35", "G36", "G37"):
        for author in AUTHORS:
            assert author not in data["sources"][key].split("https://")[0], (key, author)
    texts = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
    for line in ("Dampfdruckdefizit (VPD)", "Verdunstung (ET)", "wie viel Wasser dem Topf fehlt"):
        assert line in texts, line


NEW_FILES = ("vgrow/thirst.py", "vgrow/hank/thirst_report.py", "kartei/water_model.json", "web/thirst.js",
             "web/thirst.css", "tests/test_thirst.py", "docs/durst-index.md", "scripts/thirst_screens.py")
TOUCHED = ("vgrow/watering.py", "vgrow/server.py", "vgrow/demo.py", "vgrow/box.py", "vgrow/config.py",
           "vgrow/hank/router.py", "vgrow/hank/core.py", "vgrow/hank/types.py", "vgrow/hank/today.py",
           "vgrow/hank/nudges.py", "vgrow/hank/pump_report.py", "web/cockpit.js", "web/index.html", "web/i18n.js",
           "kartei/glossary/glossary.json", "README.md", "docs/README.md", "docs/operations.md", "docs/architecture.md",
           "tests/hank_eval/cases.json", "tests/hank_eval/expected.json", "tests/hank_eval/fixtures.py",
           "tests/test_hank_eval_offline.py", "tests/test_hank_router.py")


def test_new_and_touched_files_are_utf8_lf_without_bom_and_without_em_dash() -> None:
    for rel in (*NEW_FILES, *TOUCHED):
        raw = (REPO / rel).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw, rel
        assert chr(0x2014) not in raw.decode("utf-8"), rel


def test_nothing_is_written_into_the_repository(isolated_state) -> None:  # type: ignore[no-untyped-def]
    from vgrow.demo import DemoBox, seed_demo_thirst
    from vgrow.plant import PlantLog

    def listing() -> list[str]:
        return sorted(str(p.relative_to(REPO)) for p in REPO.rglob("*")
                      if ".git" not in p.parts and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts)

    before = listing()

    async def go() -> None:
        box = DemoBox()
        guard = PortGuard("box-1")
        watering = WateringController(box, guard)
        thirst = th.Thirst(box, plant_log=PlantLog(), guard=guard, demo=True, watering=watering)
        await seed_demo_thirst(thirst, box, watering, guard, scene="skipped")
        await thirst.check(box.snapshot())
        thirst.view(box.snapshot(), "en")
        thirst.gate(0, 120)
        await watering.close()

    asyncio.run(go())
    assert listing() == before
    written = {p.name for p in (isolated_state / "boxes" / "box-1").iterdir()}
    assert {"thirst-pot.json", "thirst-state.json", "thirst-log.jsonl", "watering.json"} <= written
    assert not {"pot.json", "watering-state.json", "watering-log.jsonl"} & written  # no name of the old ledger


# ---------- the pump OFF path ----------

def test_a_pumps_off_is_never_refused_even_with_ports_json_unreadable(tmp_path, monkeypatch, isolated_state) -> None:  # type: ignore[no-untyped-def]
    broken = tmp_path / "ports.json"
    broken.write_text('{"usb1": {"device": "pump",}, "usb2": {"label": "Befeuchter"}}', encoding="utf-8")
    monkeypatch.setattr(config, "PORTS_FILE", broken)
    monkeypatch.setattr(config, "_ports_good", None)
    assert config.port_rules() is None
    check_port_lock("usb1", False)  # the raw text names the pump: its OFF goes out
    with pytest.raises(CommandRefused):
        check_port_lock("usb2", False)  # every other port fails closed, as before
    named = tmp_path / "named.json"
    named.write_text('{"usb2": {"label": "x",}}', encoding="utf-8")
    monkeypatch.setattr(config, "PORTS_FILE", named)
    with pytest.raises(CommandRefused):
        check_port_lock("usb2", False)
    PortGuard("box-1").assign("usb2", "pump", snap())
    check_port_lock("usb2", False)  # the owner's word on the page is enough


def test_always_on_is_dropped_on_a_pump_and_the_rest_of_ports_json_stays(tmp_path, monkeypatch, isolated_state, caplog) -> None:  # type: ignore[no-untyped-def]
    both = tmp_path / "both.json"
    both.write_text(json.dumps({"usb1": {"device": "pump", "always_on": True},
                                "usb2": {"label": "Kamera", "always_on": True}}), encoding="utf-8")
    monkeypatch.setattr(config, "PORTS_FILE", both)
    monkeypatch.setattr(config, "_ports_good", None)
    rules = config.port_rules()
    assert rules is not None and "always_on" not in rules["usb1"] and rules["usb2"]["always_on"] is True
    check_port_lock("usb1", False)
    with pytest.raises(CommandRefused):
        check_port_lock("usb2", False)  # a camera port locked always on stays locked
    assert caplog.text.count("always_on dropped") == 1
    config.port_rules()
    assert caplog.text.count("always_on dropped") == 1  # said once


@pytest.mark.parametrize("box_id", ["box-1", "box-2"])
def test_owner_named_pump_off_overrides_conflicting_readable_lock(tmp_path, monkeypatch, isolated_state, box_id) -> None:  # type: ignore[no-untyped-def]
    locked = tmp_path / "ports.json"
    locked.write_text(json.dumps({"usb1": {"device": "camera", "always_on": True}}), encoding="utf-8")
    monkeypatch.setattr(config, "PORTS_FILE", locked)
    monkeypatch.setattr(config, "_ports_good", None)
    with pytest.raises(CommandRefused):
        check_port_lock("usb1", False, box_id=box_id)
    PortGuard(box_id).assign("usb1", "pump", snap())
    check_port_lock("usb1", False, box_id=box_id)
    from types import SimpleNamespace

    real_box = box_mod.Box("test@example.test", "unused", box_id=box_id)
    real_box.device = SimpleNamespace(client_id="vivosun-VSCTLY18-acct-dev1")
    real_box.reported = {"usb1": {"mode": 0, "inPlan": 0, "lv": 100, "manu": {"lv": 100}}}
    sent = []

    async def record(field, payload, target):
        sent.append((field, target))

    real_box._send = record
    asyncio.run(real_box.set_port("usb1", False))
    assert sent == [("port.usb1", False)]
    other = "box-2" if box_id == "box-1" else "box-1"
    with pytest.raises(CommandRefused):
        check_port_lock("usb1", False, box_id=other)


def test_the_real_ports_file_never_has_always_on_with_a_pump() -> None:
    rules = json.loads((REPO / "ports.json").read_text(encoding="utf-8"))
    assert not [k for k, v in rules.items() if isinstance(v, dict) and v.get("always_on") and v.get("device") == "pump"]
