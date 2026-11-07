"""Hank WP7: the recommendation ledger, the suggestion cards and the traffic light (spec 5.5, 6, 7, 9.2; addendum N4,
N5, N6). Everything with a fake clock: the ledger, the cards and the light read the time only from it. The stores
have their own file, tests/test_hank_stores.py."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import hank_live_kit as kit
from hank_eval import fixtures, report
from vgrow import plant
from vgrow.hank import compose, context, ledger, livestate, nudges, proposals, slots, stores, texts_live, verdicts
from vgrow.hank.livestate import LiveState
from vgrow.hank.types import Proposal

REPO = Path(__file__).resolve().parent.parent
WP7_FILES = [REPO / "vgrow" / "hank" / f"{name}.py" for name in ("ledger", "proposals", "nudges", "stores")]
WP7_FILES += [REPO / "vgrow" / "hank" / "data" / "ledger_rules.json",
              REPO / "tests" / "test_hank_ledger.py", REPO / "tests" / "test_hank_stores.py"]
CASES = {c["id"]: c for c in report.load_cases()}
EXPECTED = report.load_expected()
T0 = fixtures.F22_NOW.timestamp()
MIN, HOUR = 60.0, 3600.0
HUMID = ("early_flower", "probe_humidity", "high")


class Clock:
    """A fake clock: epoch seconds, moved by hand."""

    def __init__(self, start: float = T0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@pytest.fixture(autouse=True)
def frozen(monkeypatch):  # type: ignore[no-untyped-def]
    fixtures.freeze(monkeypatch)
    texts_live.register()


@pytest.fixture
def base() -> fixtures.Live:
    return fixtures.build("F22")


@pytest.fixture
def clock() -> Clock:
    return Clock()


def live_at(base: fixtures.Live, clock: Clock | float | None = None, *, probe: float | None = ...,  # type: ignore[assignment]
            temp: float | None = ..., light: int | None = ..., dfan: int | None = ..., usb2: bool | None = ...,  # type: ignore[assignment]
            age: int | None = ..., alerts: tuple[str, ...] = (), journal: list[dict[str, Any]] | None = None,
            auto: dict[str, Any] | None = None) -> LiveState:
    """F22 with some readings changed, the check rows computed again from the changed snapshot."""
    snap = copy.deepcopy(base.snapshot)
    if probe is not ...:
        snap["climate"]["probe_humidity"] = probe
    if temp is not ...:
        snap["climate"]["probe_temp"] = temp
    if light is not ...:
        snap["light"]["level"] = light
    if dfan is not ...:
        snap["dfan"]["step"] = dfan
    if usb2 is not ...:
        snap["ports"]["usb2"]["on"] = usb2
    if age is not ...:
        snap["sensors_age"] = age
        snap["shadow_age"] = age
    view = copy.deepcopy(base.plant_view)
    view["checks"] = plant.evaluate(base.stage, snap)
    rules = [dict(a, active=a["name"] in alerts) for a in base.alerts_view]
    when = clock() if callable(clock) else (clock if clock is not None else T0)
    sources = {**base.sources(), "snapshot": snap, "plant_view": view, "alerts_view": rules,
               "automation_view": {**base.automation_view, **(auto or {})}, "journal": journal or [],
               "now": datetime.fromtimestamp(when).isoformat(timespec="seconds")}
    return livestate.collect(sources)


def humid(state: LiveState) -> verdicts.VerdictSet:
    return verdicts.build(state, "de", metrics=["probe_humidity"], statuses=("high",))


def dfan_card(state: LiveState, clock: Clock) -> Proposal:
    (card,) = [c for c in proposals.from_checks(humid(state), state, now=clock()) if c.kind == "dfan"]
    return card


# ---------- the ledger ----------

def test_ledger_lifecycle_open_applied_resolved(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    card = dfan_card(state, clock)
    (item,) = book.record(humid(state), live=state, answer_id="a-1", proposals=[card])
    assert item["state"] == "open" and item["id"] == "r-0001"
    assert item["baseline"] == {"value": 79.0, "usb2_on": False, "dfan": 3}  # spec 6.1 example
    assert item["recommendation"] == {"card": "stages/autoflower#early_flower.probe_humidity.all", "kind": "dfan_up",
                                      "proposal": {"action": "dfan", "step": 4}, "proposal_id": card.id}
    assert book.record(humid(state), live=state, answer_id="a-2") == []  # at most one open entry per key

    clock.advance(10 * MIN)
    assert book.tick(live_at(base, clock), clock()) == []  # nothing moved yet, still inside the grace time
    clock.advance(5 * MIN)
    events = book.tick(live_at(base, clock, dfan=4), clock())  # the exhaust went up: applied
    assert [e.kind for e in events] == ["applied"] and book.items()[0]["state"] == "applied"
    clock.advance(30 * MIN)
    book.tick(live_at(base, clock, dfan=4, probe=58.0), clock())  # back in the band
    done = book.items()[0]
    assert done["state"] == "resolved"
    assert [h["event"] for h in done["history"]] == ["created", "applied", "resolved"]

    # a tapped card applies its entry at once (spec 6.2)
    state = live_at(base, clock)
    card = dfan_card(state, clock)
    (second,) = book.record(humid(state), live=state, answer_id="a-3", proposals=[card])
    assert book.accepted(card.id)["state"] == "applied"
    assert book.get(second["id"])["applied_at"] is not None
    assert book.accepted("p-unknown") is None


def test_not_applied_after_grace(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    book.record(humid(state), live=state, answer_id="a-1", proposals=[dfan_card(state, clock)])
    clock.advance(29 * MIN)
    assert book.tick(live_at(base, clock), clock()) == []
    clock.advance(1 * MIN)  # grace_minutes 30 (spec 6.2 start value)
    (event,) = book.tick(live_at(base, clock), clock())
    assert event.kind == "not_applied" and book.items()[0]["state"] == "not_applied"
    clock.advance(10 * MIN)
    assert book.tick(live_at(base, clock), clock()) == []  # said once, stays not_applied
    # a device moved the other way is no carrying out of the advice
    other = ledger.Ledger(store=stores.JsonDoc("other-ledger.json", ledger.DEFAULT), clock=clock)
    state = live_at(base, clock)
    other.record(humid(state), live=state, answer_id="a-2")
    clock.advance(31 * MIN)
    other.tick(live_at(base, clock, dfan=2), clock())
    assert other.items()[0]["state"] == "not_applied"
    # stale readings change nothing (spec 4.3)
    third = ledger.Ledger(store=stores.JsonDoc("third-ledger.json", ledger.DEFAULT), clock=clock)
    state = live_at(base, clock)
    third.record(humid(state), live=state, answer_id="a-3")
    assert third.record(humid(state), live=live_at(base, clock, age=1500), answer_id="a-4") == []
    clock.advance(31 * MIN)
    assert third.tick(live_at(base, clock, age=1500), clock()) == []
    assert third.items()[0]["state"] == "open"


def test_open_entries_expire_after_48h_and_on_stage_change(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    book.record(humid(state), live=state, answer_id="a-1")
    book.tick(state, clock())
    clock.advance(48 * HOUR)
    (event,) = book.tick(live_at(base, clock, age=1500), clock())  # expiry by time runs on stale readings too
    assert event.kind == "expired" and book.items()[0]["state"] == "expired"

    state = live_at(base, clock)
    book.record(humid(state), live=state, answer_id="a-2")
    book.mark_told([HUMID], state)
    assert book.is_told(*HUMID)
    moved = livestate.collect({**base.sources(), "plant_view": {**base.plant_view, "stage": {
        **base.plant_view["stage"], "key": "mid_flower", "label": "Mitte der Blüte"}}})
    kinds = [e.kind for e in book.tick(moved, clock.advance(MIN))]
    assert kinds == ["expired", "reset"]  # addendum N4: a stage change expires and resets "once"
    assert not book.is_told(*HUMID) and book.load()["stage"] == "mid_flower"


def test_consequence_told_once_with_source(base, clock) -> None:  # type: ignore[no-untyped-def]
    """live-06 from the ledger itself: advised, not done, asked again; the consequence comes once with S17, and
    "once" holds per stage, metric and direction, also for a later new entry (addendum N4)."""
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    book.record(humid(state), live=state, answer_id="a-1", proposals=[dfan_card(state, clock)])
    clock.advance(45 * MIN)
    state = live_at(base, clock)
    book.tick(state, clock())
    case = CASES["live-06"]
    route = kit.route(case["question"], "de", EXPECTED["live-06"]["hits"])

    def answer() -> compose.Composed:
        return compose.build(context.build(route, state, book.items(), "de", question=case["question"]))

    first = answer()
    told = f"{first.text} {first.more}"  # the bubble opens the message, the details are folded away
    assert "Mein Rat war: Abluft auf Stufe 4." in first.text and "Das ist deine Entscheidung." in first.text
    assert "Soweit ich sehe, ist das noch nicht umgesetzt." in told
    assert "71 %" in told and "S17" in [s.id for s in first.sources + first.more_sources]
    assert first.told == [HUMID]
    book.mark_told(first.told, state)
    assert book.items()[0]["consequence_told"] is True and book.items()[0]["told"]["deviation"] == 19.0
    again = answer()
    assert "71 %" not in f"{again.text} {again.more}" and again.told == []

    # back on target, then off again with a new entry: still told (the key, not the entry, carries "once")
    clock.advance(HOUR)
    book.tick(live_at(base, clock, probe=55.0), clock())
    clock.advance(HOUR)
    state = live_at(base, clock)
    (new,) = book.record(humid(state), live=state, answer_id="a-2")
    assert new["id"] == "r-0002" and book.get("r-0002")["consequence_told"] is True

    # the decline message of spec 6.3 for a key not told yet: once with the source, then respect
    fresh = ledger.Ledger(store=stores.JsonDoc("fresh-ledger.json", ledger.DEFAULT), clock=clock)
    card = dfan_card(state, clock)
    fresh.record(humid(state), live=state, answer_id="a-3", proposals=[card])
    item = fresh.declined(card, live=state)
    assert item["state"] == "declined"
    told = fresh.tell(item, state, "de")
    assert told.told and told.gap is None
    assert told.sentences[0] == "Du hast den Vorschlag abgelehnt."
    assert "die Blütenmasse sank um 71 %" in told.text and re.search(r"71 %.*\[1\]", told.text)
    assert [s.id for s in told.sources] == ["S17"]
    assert told.text.endswith("Das ist deine Entscheidung. Ich melde mich nur wieder, wenn es schlimmer wird.")
    second = fresh.tell(item, state, "de")
    assert not second.told and "71" not in second.text and second.sources == []
    stale = ledger.Ledger(store=stores.JsonDoc("stale-ledger.json", ledger.DEFAULT), clock=clock)
    quiet = stale.tell(item, live_at(base, clock, age=1500), "de")  # no judgement from old readings (spec 4.3)
    assert not quiet.told and quiet.gap is None and "Folgen" not in quiet.text
    assert not stale.is_told(*HUMID)


def test_no_consequence_field_says_so_and_logs_gap(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    light = verdicts.build(state, "de", metrics=["light_percent"])
    (card,) = [c for c in proposals.from_checks(light, state, now=clock()) if c.kind == "light"]
    book.record(light, live=state, answer_id="a-1", proposals=[card])
    item = book.declined(card, live=state)
    told = book.tell(item, state, "de")
    assert "Zu den Folgen steht noch nichts in meiner Kartei." in told.text
    assert told.sources == [] and not re.search(r"\d", told.text)  # nothing invented
    assert told.gap == {"kind": "consequence_missing", "stage": "early_flower", "metric": "light_percent",
                        "status": "low"}
    gaps = stores.GapStore(clock)
    gaps.add(told.gap)
    gaps.add(told.gap)
    (gap,) = gaps.all()
    assert gap["kind"] == "consequence_missing" and gap["count"] == 2 and gap["question"] is None


def test_raise_again_only_when_worse_with_cooldown(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    book.record(humid(state), live=state, answer_id="a-1")
    book.mark_told([HUMID], state)  # told at 79 %: 19 points above the band
    clock.advance(HOUR)
    events = book.tick(live_at(base, clock, probe=90.0), clock())
    assert "worse" not in [e.kind for e in events] and book.is_told(*HUMID)  # much worse, inside the 6 h cooldown
    clock.advance(6 * HOUR)
    events = book.tick(live_at(base, clock, probe=83.0), clock())  # +4 points: not more than worse_delta 5
    assert "worse" not in [e.kind for e in events] and book.is_told(*HUMID)
    events = book.tick(live_at(base, clock, probe=85.0), clock())  # +6 points and the cooldown over
    (worse,) = [e for e in events if e.kind == "worse"]
    assert worse.key == "early_flower|probe_humidity|high" and worse.detail["why"] == "delta"
    assert not book.is_told(*HUMID) and book.items()[0]["consequence_told"] is False

    # an alarm rule that newly turns active counts as worse too (spec 6.3)
    state = live_at(base, clock)
    book.mark_told([HUMID], state)
    book.tick(state, clock())
    clock.advance(7 * HOUR)
    events = book.tick(live_at(base, clock, alerts=("humidity_high",)), clock())
    assert [e.detail["why"] for e in events if e.kind == "worse"] == ["alert"]
    clock.advance(7 * HOUR)
    book.mark_told([HUMID], state)
    clock.advance(7 * HOUR)
    still = book.tick(live_at(base, clock, alerts=("humidity_high",)), clock())  # active before: not new
    assert "worse" not in [e.kind for e in still]


def test_declined_proposal_not_offered_again(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    cards = proposals.from_checks(verdicts.build(state, "de", statuses=("low", "high")), state, now=clock())
    dfan = next(c for c in cards if c.kind == "dfan")
    book.record(humid(state), live=state, answer_id="a-1", proposals=cards)
    book.declined(dfan, live=state)
    again = proposals.from_checks(verdicts.build(state, "de", statuses=("low", "high")), state, now=clock())
    kept = book.offerable(again, state)
    assert "dfan" not in [c.kind for c in kept] and {"light", "plant_stage"} <= {c.kind for c in kept}
    wish = slots.Slots(devices=["dfan"], switch="up", verb=True)
    asked = proposals.from_request("Stell die Abluft höher", wish, state, now=clock())
    assert [c.body for c in book.offerable(asked, state)] == [{"action": "dfan", "step": 4}]  # asked for: offered
    clock.advance(2 * HOUR)
    book.tick(live_at(base, clock, probe=86.0), clock())  # worse, but inside the cooldown
    assert "dfan" not in [c.kind for c in book.offerable(again, state)]
    clock.advance(5 * HOUR)
    events = book.tick(live_at(base, clock, probe=86.0), clock())
    assert "worse" in [e.kind for e in events]
    assert "dfan" in [c.kind for c in book.offerable(again, state)]


# ---------- the cards ----------

def test_proposal_allowlist_and_ranges(base, clock) -> None:  # type: ignore[no-untyped-def]
    state = live_at(base, clock)  # light 60, exhaust step 3, circulation fan without a step
    for body in ({"action": "spectrum", "name": "bloom"}, {"action": "refresh"}, {"action": "set_lang", "lang": "en"},
                 {"action": "record_start"}, {"action": "plant_water"}, {"action": "feeding_log", "liters": 1},
                 {"action": "plant_analyze"}, {"action": "light", "level": 101}, {"action": "light", "level": -1},
                 {"action": "light", "level": "80"}, {"action": "light", "level": 86}, {"action": "dfan", "step": 11},
                 {"action": "dfan", "step": 5}, {"action": "dfan", "step": True}, {"action": "cfan", "step": -1},
                 {"action": "port", "port": "usb9", "on": True}, {"action": "port", "port": "usb2", "on": "off"},
                 {"action": "plant_stage", "key": "flowering"}, {"action": "auto_humidifier", "on": False},
                 {"action": "auto_humidifier"}, "light"):
        with pytest.raises(proposals.Refused):
            proposals.check_body(body, state)  # type: ignore[arg-type]
    assert proposals.check_body({"action": "light", "level": 85, "extra": 1}, state) == {"action": "light",
                                                                                         "level": 85}
    assert proposals.check_body({"action": "light", "level": 0}, state) == {"action": "light", "level": 0}
    assert proposals.check_body({"action": "dfan", "step": 2}, state) == {"action": "dfan", "step": 2}
    assert proposals.check_body({"action": "cfan", "step": 7}, state) == {"action": "cfan", "step": 7}
    dark = live_at(base, clock, light=0)
    assert proposals.check_body({"action": "light", "level": 50}, dark)["level"] == 50  # from 0: at most 50
    with pytest.raises(proposals.Refused):
        proposals.check_body({"action": "light", "level": 60}, dark)

    taps = {
        "port off": ({"action": "port", "port": "usb2", "on": False}, True),
        "port on": ({"action": "port", "port": "usb2", "on": True}, False),
        "stage": ({"action": "plant_stage", "key": "early_flower"}, True),
        "apply": ({"action": "plant_apply"}, True),
        "automation on": ({"action": "auto_humidifier", "on": True}, True),
        "light": ({"action": "light", "level": 85}, False),
        "fan": ({"action": "dfan", "step": 4}, False),
    }
    for name, (body, two) in taps.items():  # addendum N5: two taps as on the page
        card = proposals.make(body, state, "de", now=clock())
        assert card.two_taps is two, name
        assert card.expires == clock() + 30 * MIN and card.id.startswith("p-")
    assert proposals.make({"action": "port", "port": "usb2", "on": True}, state).label == "Luftbefeuchter (USB 2) an"
    assert proposals.make({"action": "auto_humidifier", "on": True}, state, "en").label == "Humidifier automation on"

    # "automation off" is never a card, not even when asked for (addendum N5)
    off = slots.Slots(devices=["automation"], switch="off", verb=True)
    assert proposals.from_request("Schalte die Automatik aus", off, state) == []
    on = slots.Slots(devices=["automation"], switch="on", verb=True)
    assert [c.body for c in proposals.from_request("Schalte die Automatik an", on, state)] == [
        {"action": "auto_humidifier", "on": True}]
    # fans: one step per card, toward the wish
    wish = slots.Slots(devices=["dfan"], step=8.0, verb=True)
    assert [c.body for c in proposals.from_request("Stell die Abluft auf Stufe 8", wish, state)] == [
        {"action": "dfan", "step": 4}]
    top = live_at(base, clock, dfan=10)
    up = slots.Slots(devices=["dfan"], switch="up", verb=True)
    assert proposals.from_request("Abluft höher", up, top) == []
    # a card that would change nothing is left out: the humidifier is already off
    assert [c.kind for c in proposals.from_checks(humid(state), state)] == ["dfan", "plant_stage"]
    running = live_at(base, clock, usb2=True)
    assert [c.body for c in proposals.from_checks(humid(running), running)][:2] == [
        {"action": "port", "port": "usb2", "on": False}, {"action": "dfan", "step": 4}]
    assert proposals.from_checks(humid(state), live_at(base, clock, age=1500)) == []  # stale: no card


def test_proposal_refuses_always_on_off(clock, locked_ports) -> None:  # type: ignore[no-untyped-def]
    # The lock against a ports file that locks USB 1 (tests/fixtures/ports-locked.json): the real one locks no port
    # since 23.10.2026. F22 is built after the patch, so its ports are the locked file's too.
    state = live_at(fixtures.build("F22"), clock)
    with pytest.raises(proposals.Refused, match="locked"):
        proposals.check_body({"action": "port", "port": "usb1", "on": False}, state)
    assert proposals.check_body({"action": "port", "port": "usb1", "on": True}, state)["on"] is True
    with pytest.raises(proposals.Refused, match="ports.json"):  # unreadable ports.json: no port off at all
        proposals.check_body({"action": "port", "port": "usb2", "on": False}, state, rules=None)
    camera = slots.Slots(devices=["growcam"], switch="off", verb=True)
    assert proposals.from_request("Mach die Kamera aus", camera, state) == []
    usb1 = slots.Slots(switch="off", verb=True)
    assert proposals.from_request("Schalte USB 1 aus", usb1, state) == []
    humidifier = slots.Slots(devices=["humidifier"], switch="on", verb=True)
    (card,) = proposals.from_request("Mach den Befeuchter an", humidifier, state)
    assert card.body == {"action": "port", "port": "usb2", "on": True} and not card.two_taps
    running = live_at(fixtures.build("F22"), clock, usb2=True)
    (card,) = proposals.from_request("Mach den Luftbefeuchter aus", slots.Slots(switch="off", verb=True), running)
    assert card.body == {"action": "port", "port": "usb2", "on": False} and card.two_taps


def test_real_ports_pump_on_usb1_gets_no_lock_and_the_camera_no_port(base, clock) -> None:  # type: ignore[no-untyped-def]
    """The owner's real ports.json since 23.10.2026: USB 1 is the drip pump, not locked; the camera needs its own power.
    An off card for the pump stays an ordinary port card (two taps); an on card is refused, since one tap would water
    without end, also when the wish names the water ("Wasser" is in "Tropfbewässerung"). A camera wish gives no port
    card."""
    state = live_at(base, clock)
    off = proposals.check_body({"action": "port", "port": "usb1", "on": False}, state)
    assert off == {"action": "port", "port": "usb1", "on": False} and proposals.two_taps(off)
    with pytest.raises(proposals.Refused, match="pump"):
        proposals.check_body({"action": "port", "port": "usb1", "on": True}, state)
    on = slots.Slots(switch="on", verb=True)
    assert proposals.from_request("Schalte die Pumpe an", on, state) == []
    assert proposals.from_request("Schalte das Wasser an", on, state) == []
    assert proposals.from_request("Switch the drip irrigation on", on, state, lang="en") == []
    assert proposals.check_body({"action": "port", "port": "usb2", "on": True}, state)["on"] is True  # humidifier
    camera = slots.Slots(devices=["growcam"], switch="off", verb=True)
    assert proposals.from_request("Mach die Kamera aus", camera, state) == []


def _names() -> slots.Names:
    data = json.loads((REPO / "kartei" / "_hank" / "vocab.json").read_text(encoding="utf-8"))
    return slots.Names(data["entries"])


def test_light_request_steps_at_most_25(base, clock) -> None:  # type: ignore[no-untyped-def]
    names = _names()
    question = CASES["live-03"]["question"]  # "Dreh bitte das Licht auf 100 Prozent hoch."
    wish = slots.read(question, "de", names=names)
    assert wish.percent == 100 and wish.verb
    (card,) = proposals.from_request(question, wish, live_at(base, clock))
    assert card.body == {"action": "light", "level": 85} and card.label == "Licht auf 85 %"  # 60 + 25, not 100
    assert [c.body["level"] for c in proposals.from_request(question, wish, live_at(base, clock, light=0))] == [50]
    assert [c.body["level"] for c in proposals.from_request(question, wish, live_at(base, clock, light=90))] == [100]
    assert proposals.from_request(question, wish, live_at(base, clock, light=100)) == []
    up = slots.read("Mach das Licht heller, dreh es hoch", "de", names=names)
    assert [c.body["level"] for c in proposals.from_request("Mach das Licht hoch", up, live_at(base, clock))] == [85]
    down = slots.read("Stell das Licht auf 40 Prozent", "de", names=names)
    assert [c.body["level"] for c in proposals.from_request("Stell das Licht auf 40 Prozent", down,
                                                            live_at(base, clock))] == [40]  # down: straight there
    # addendum N5: the next step up at the earliest 24 h after an accepted one
    accepted = [{"time": datetime.fromtimestamp(T0 - 23 * HOUR).isoformat(timespec="seconds"),
                 "origin": "hank_accepted", "action": "light", "body": {"action": "light", "level": 60}}]
    assert proposals.from_request(question, wish, live_at(base, clock, journal=accepted)) == []
    light_rows = verdicts.build(live_at(base, clock, journal=accepted), "de", metrics=["light_percent"])
    assert "light" not in [c.kind for c in proposals.from_checks(light_rows, live_at(base, clock, journal=accepted))]
    by_hand = [dict(accepted[0], origin="human")]
    assert len(proposals.from_request(question, wish, live_at(base, clock, journal=by_hand))) == 1
    clock.advance(2 * HOUR)  # 25 h after the accepted step
    assert [c.body["level"] for c in proposals.from_request(question, wish, live_at(base, clock,
                                                                                     journal=accepted))] == [85]


def test_card_registry_valid_30_minutes(base, clock) -> None:  # type: ignore[no-untyped-def]
    state = live_at(base, clock)
    registry = proposals.Registry(clock)
    (card,) = registry.add([proposals.make({"action": "dfan", "step": 4}, state, now=clock())])
    assert registry.get(card.id) is card and len(registry) == 1
    clock.advance(30 * MIN)
    assert registry.get(card.id) is None and len(registry) == 0
    (card,) = registry.add([proposals.make({"action": "dfan", "step": 4}, state, now=clock())])
    assert registry.take(card.id) is card and registry.take(card.id) is None


# ---------- the traffic light ----------

def green(base: fixtures.Live, clock: Clock, **kw: Any) -> LiveState:
    return live_at(base, clock, probe=kw.pop("probe", 50.0), light=kw.pop("light", 100),
                   auto={"needs_stage_confirmation": False}, **kw)


def test_traffic_light_levels_and_debounce(base, clock) -> None:  # type: ignore[no-untyped-def]
    store = stores.JsonDoc("nudge-test.json", {"version": 1})
    first = nudges.tick(live_at(base, clock), None, clock(), store=store)
    assert first.level == "yellow" and first.text["de"].startswith("Genauer hinschauen: Luftfeuchte am Fühler: 79 %")
    assert first.text["en"].startswith("Take a closer look: Humidity at the probe: 79%")
    assert [(m["level"], m["op"]) for m in first.messages] == [("yellow", "new")]  # a note, as a chat message (N6)
    assert first.light("de")["sources"] and first.light("de")["proposal"] is None

    assert nudges.tick(green(base, clock), None, clock.advance(MIN), store=store).level == "yellow"  # waits
    assert nudges.tick(live_at(base, clock), None, clock.advance(MIN), store=store).level == "yellow"
    start = clock.advance(MIN)
    assert nudges.tick(green(base, clock), None, start, store=store).level == "yellow"
    assert nudges.tick(green(base, clock), None, start + 299, store=store).level == "yellow"
    calm = nudges.tick(green(base, clock), None, start + 300, store=store)  # held five minutes: green
    assert calm.level == "green" and calm.text["de"] == "Alles im Ziel." and calm.messages == []
    assert calm.since == start + 300
    clock.now = start + 360
    back = nudges.tick(live_at(base, clock), None, clock(), store=store)
    assert back.level == "yellow" and back.messages == []  # at once, but the same note waits out its 6 h lock

    stage = nudges.tick(live_at(base, clock, probe=50.0, light=100), None, clock.advance(10 * MIN), store=store)
    assert stage.raw == "yellow" and stage.text["de"].startswith("Die Phase ist nur geschätzt.")
    # a new reason at the same level updates the live note in place, it is no new note (owner, 23.10.2026: no flood)
    assert [(m["reason"], m["op"]) for m in stage.messages] == [("stage", "update")]
    red = nudges.tick(live_at(base, clock, alerts=("humidity_high",)), None, clock.advance(MIN), store=store)
    assert red.level == "red" and red.text["de"] == "Jetzt handeln: Zu feucht (Fühler)."
    assert red.text["en"] == "Act now: Too humid (probe)."
    assert nudges.tick(live_at(base, clock), None, clock.advance(MIN), store=store).level == "red"  # debounced
    assert nudges.assess(live_at(base, clock, probe=None, light=100, auto={"needs_stage_confirmation": False})
                         ).level == "green"  # no probe value: unknown, not off target


def test_stale_data_is_red_after_15_min(base, clock) -> None:  # type: ignore[no-untyped-def]
    assert nudges.assess(live_at(base, clock, age=600)).reason.startswith("check:")
    old = nudges.assess(live_at(base, clock, age=601))
    assert old.level == "yellow" and old.text["de"] == "Die Messwerte sind 10 Minuten alt."
    assert nudges.assess(live_at(base, clock, age=900)).level == "yellow"
    offline = nudges.assess(live_at(base, clock, age=901))
    assert offline.level == "red" and offline.text["de"] == "Seit 15 Minuten kommen keine frischen Messwerte."
    alt = nudges.assess(livestate.collect(fixtures.build("F22-alt").sources()))  # live-05: 25 minutes
    assert alt.level == "red" and alt.text["en"] == "No fresh readings for 25 minutes."
    none = nudges.assess(live_at(base, clock, age=None))
    assert none.level == "yellow" and none.text["de"] == "Noch keine Messwerte."
    state = nudges.tick(live_at(base, clock, age=901), None, clock(), store=stores.JsonDoc("n.json", {}))
    assert state.level == "red" and state.proposal is None and state.scene == "point"


def test_scene_choice_no_jokes_at_red(base, clock) -> None:  # type: ignore[no-untyped-def]
    assert nudges.scene(live_at(base, clock), "red") == "point"  # too humid, but red: no umbrella
    assert nudges.scene(live_at(base, clock, light=0), "red") == "point"
    assert nudges.scene(live_at(base, clock), "yellow") == "umbrella"
    assert nudges.scene(live_at(base, clock, light=0), "yellow") == "sleep"
    assert nudges.scene(live_at(base, clock, probe=50.0, temp=29.0), "yellow") == "fan"
    assert nudges.scene(live_at(base, clock, probe=50.0, temp=15.0), "yellow") == "scarf"
    assert nudges.scene(green(base, clock), "green") == "idle"
    assert nudges.scene(live_at(base, clock), "yellow", "warnings") is None
    assert nudges.scene(live_at(base, clock, age=1500), "yellow") == "idle"  # no judgement from stale readings
    hot = nudges.tick(live_at(base, clock, alerts=("humidity_high",)), None, clock(),
                      store=stores.JsonDoc("hot.json", {}))
    assert hot.scene == "point" and hot.scene_dict("de") == {"id": "point", "text": "Hank zeigt auf die Ampel."}


def test_red_note_carries_one_card_and_a_ledger_entry(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    store = stores.JsonDoc("red.json", {})
    red = nudges.tick(live_at(base, clock, alerts=("humidity_high",)), book, clock(), store=store)
    assert red.level == "red" and red.proposal is not None
    assert red.proposal.body == {"action": "dfan", "step": 4}  # the humidifier is off already
    assert red.light("en")["proposal"]["label"] == "Exhaust fan to step 4"
    (note,) = red.messages
    assert note["proposal"] == red.proposal.id
    assert note["text"]["de"] == "Jetzt handeln: Zu feucht (Fühler). Mein Vorschlag: Abluft auf Stufe 4. Du " \
                                 "bestätigst ihn mit einem Tipp."
    (item,) = book.items()
    assert item["recommendation"]["proposal_id"] == red.proposal.id and item["answer_id"].startswith("n-")
    later = nudges.tick(live_at(base, clock, alerts=("humidity_high",)), book, clock.advance(MIN), store=store)
    assert later.proposal.id == red.proposal.id and later.messages == []  # the same card, no second note
    book.declined(later.proposal, live=live_at(base, clock))
    after = nudges.tick(live_at(base, clock, alerts=("humidity_high",)), book, clock.advance(MIN), store=store)
    assert after.proposal is None  # declined: not offered again (spec 6.3)


def test_worse_event_becomes_a_note(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    store = stores.JsonDoc("worse.json", {})
    state = live_at(base, clock)
    book.record(humid(state), live=state, answer_id="a-1")
    book.mark_told([HUMID], state)
    nudges.tick(state, book, clock(), store=store)
    clock.advance(7 * HOUR)
    out = nudges.tick(live_at(base, clock, probe=86.0), book, clock(), store=store)
    worse = [m for m in out.messages if m["reason"].startswith("worse:")]
    assert len(worse) == 1 and worse[0]["text"]["de"].startswith("Es ist schlimmer geworden: Luftfeuchte am Fühler: 86")
    assert worse[0]["op"] == "raise"  # the live note comes back to the bottom, unread (spec 6.3), still one note
    again = nudges.tick(live_at(base, clock, probe=86.0), book, clock.advance(MIN), store=store)
    assert not [m for m in again.messages if m["reason"].startswith("worse:")]


def test_mode_off_does_nothing_and_warnings_keep_the_light(base, clock) -> None:  # type: ignore[no-untyped-def]
    book = ledger.Ledger(clock=clock)
    state = live_at(base, clock)
    book.record(humid(state), live=state, answer_id="a-1")
    clock.advance(HOUR)
    off = nudges.tick(live_at(base, clock), book, clock(), mode="off")
    assert off.hidden and off.light("de") is None and off.messages == [] and off.scene is None
    assert book.items()[0]["state"] == "open"  # spec 7: in "off" the ledger rests too
    assert not (stores.hank_dir() / "nudge-state.json").exists()
    warn = nudges.tick(live_at(base, clock), book, clock(), mode="warnings")
    assert warn.level == "yellow" and warn.scene is None and warn.messages
    assert book.items()[0]["state"] == "not_applied"
    with pytest.raises(ValueError):
        nudges.tick(state, book, clock(), mode="loud")


# ---------- files ----------

def test_ledger_rules_file_shape() -> None:
    rules = ledger.load_rules()
    assert rules["version"] == 1 and rules["grace_minutes"] == 30 and rules["open_hours"] == 48
    assert rules["cooldown_hours"] == 6
    assert rules["worse_delta"]["probe_humidity"] == 5 and rules["worse_delta"]["probe_temp"] == 2
    assert rules["worse_delta"]["probe_vpd"] == 0.3 and rules["worse_delta"]["light_percent"] == 15
    for metric, table in rules["actions"].items():
        assert set(table) <= {"low", "high"}, metric
        for move in table["low"] + table["high"]:
            assert move["action"] in proposals.KINDS and move["action"] != "auto_humidifier", metric
            assert move.get("dir") in (None, "up", "down")


def test_wp7_files_have_no_em_dash_bom_or_actuator_call() -> None:
    for path in WP7_FILES:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r\n" not in raw, path.name
        body = raw.decode("utf-8")
        assert chr(0x2014) not in body, path.name
        if path.parent.name == "hank":
            assert not re.search(r"\bset_(light|port|cfan|dfan|spectrum|enabled|stage)\b|_send\b|refresh_shadow",
                                 body), path.name
