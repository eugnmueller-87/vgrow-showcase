"""The fix answer (owner, 23.10.2026): "Kannst du das beheben?", "Fix it", "What should I do about it?", and a tap on
the traffic-light line or on a note's "Beheben" button.

The owner's words that morning: "if he's giving me a signal to take a closer look, then I need to be able to click on
it. And also to fix the problem." He had four notes in a row and asked "can you fix those things?"; Hank greeted him.

The router sends the fix wish to the route "fix" (data/gates.json "fix", router.py); the page sends the same wish with
the problem keys of the line or the note it was tapped on (POST /api/hank/ask {fix: [...]}, see core.ask_full). The
answer comes from code alone, from the same read-only moment the traffic light reads (livestate.collect): it never
calls the model and never switches anything. A card is still only a suggestion the owner taps, through the cockpit's
own /api/cmd with every guard, and the rules of proposals.py hold unchanged (two taps where the cockpit needs two,
light at most +25 per step and the next step 24 h after an accepted one at the earliest, never "Automatik aus").

What it says, from the verdicts off target (the same ones the traffic light shows: verdicts.build, low and high):
1. a lead line; then, most urgent first, one line per problem: the reading with its verdict and target, the first
   sentence of the card's advice (what helps), and the label of the card below when the card rules have a device
   action for it (proposals.from_checks: light step, fan step, humidifier port where it is not locked); a light card
   held back by the 24 h rule says so;
2. "most urgent": a check with an active alarm rule first, then the climate before the light (v1.2, owner 23.10.2026:
   humidity, temperature, VPD, the rest, light last; verdicts.urgency), then the one furthest from its band, relative
   to the bound it broke (an own ordering rule without a sourced weight);
3. the stage, when the humidifier automation waits for a confirmed stage: the light's own sentence and a confirm card
   (two taps on the page);
4. one closing sentence: Hank switches nothing himself, the card waits for the owner's confirmation;
5. readings too old (or none): that sentence alone, no card (no step from old readings, review 3, M7);
6. nothing off target: "Alles im Ziel, da gibt es nichts zu beheben."

Problem keys (nudges.Assessment.problems): "<metric>:<status>" for a check, "stage", "alert:<name>", "offline",
"age", "no_readings". With keys, the answer covers exactly those problems that are still off; when none of them is
off any more, it says so and names what is off now instead. No keys or an empty list (a note stored before the keys
existed, the green line) means every problem, and never "back on target" (review 1, M1).

A card the owner asks for this way is offered even after an earlier "Nicht jetzt" (ledger.may_offer offers a card
the person asked for, reason "request", always; the fix wish is such a request). The ledger records the advice and
links the cards (core._fix), so a tapped card moves its entry to applied as on any other answer; a card handed out
again (a second ask, or after "Nicht jetzt") takes its entry over (ledger.Ledger.relink, review 1, M3). A tapped card
is checked again on the moment of the tap before /api/cmd runs (core.Hank.precheck_command, review 2, S2); the texts
of that check are here too (hank.cardcheck.*).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from vgrow import i18n
from vgrow.i18n import t

from . import nudges, pilot_report, proposals, verdicts
from .livestate import LiveState, collect, minutes
from .today import one_line
from .types import Answer, Proposal, SourceRef, Verdict

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
KEY_STAGE = "stage"
STALE_KEYS = ("offline", "age", "no_readings")

DE: dict[str, str] = {
    "hank.fix.lead_one": "Das hilft:",
    "hank.fix.lead_many": "Das hilft, das Dringendste zuerst:",
    "hank.fix.high": "{label}: {value}, zu hoch ({target}).",
    "hank.fix.low": "{label}: {value}, zu niedrig ({target}).",
    "hank.fix.urgent": "Jetzt handeln: {line}",
    "hank.fix.card": "Vorschlag unten: {labels}.",
    "hank.fix.light_wait": "Den nächsten Lichtschritt biete ich erst 24 h nach dem letzten angenommenen an.",
    "hank.fix.light_hold": "Mehr Licht bringt mehr Wärme: den Lichtschritt biete ich erst an, wenn es nicht mehr zu "
                           "warm oder zu trocken ist.",
    "hank.fix.stale": "Ohne frische Messwerte schlage ich nichts vor.",
    "hank.fix.resolved": "Was der Hinweis genannt hat, ist wieder im Ziel.",
    "hank.fix.close_one": "Im Chat schalte ich nichts ohne dich: die Karte unten wartet auf deine Bestätigung.",
    "hank.fix.close_many": "Im Chat schalte ich nichts ohne dich: jede Karte unten wartet auf deine Bestätigung.",
    "hank.fix.close_none": "Im Chat schalte ich nichts ohne deinen Tipp, und dafür habe ich keine Karte zum "
                           "Antippen.",
    "hank.fix.none": "Alles im Ziel, da gibt es nichts zu beheben.",
    # v1.2: a typed "Kannst du das beheben?" about the chat's last topic (asking.fix_scope)
    "hank.fix.about_none": "{label} misst die Box nicht, da kann ich nichts beheben.",
    "hank.fix.about_unknown": "Für {label} liefert die Box gerade keinen Messwert, da kann ich nichts beheben.",
    "hank.fix.about_ok": "{label} ist gerade im Ziel, da gibt es nichts zu beheben.",
    "hank.fix.about_untargeted": "Für {label} hat die Phase kein Ziel, da gibt es nichts zu beheben.",
    "hank.fix.lead_other_one": "Bei dem, was sonst nicht im Ziel ist, hilft das:",
    "hank.fix.lead_other_many": "Bei dem, was sonst nicht im Ziel ist, hilft das, das Dringendste zuerst:",
    "hank.fix.rest_ok": "Sonst ist alles im Ziel.",
    # a card checked again at the tap (core.precheck_command, api.journal_wrap answers 409 with it)
    "hank.cardcheck.gone": "Diese Karte gilt nicht mehr. Frag mich neu, dann rechne ich mit den Werten von jetzt.",
    "hank.cardcheck.changed": "Diese Karte wurde verändert, so schalte ich sie nicht. Frag mich neu.",
    "hank.cardcheck.stale": "Die Messwerte sind nicht frisch, deshalb läuft die Karte jetzt nicht.",
    "hank.cardcheck.refused": "Diese Karte passt nicht mehr zu den Werten von jetzt ({reason}). Frag mich neu.",
    "hank.cardcheck.light_wait": "Den nächsten Lichtschritt gibt es erst 24 h nach dem letzten angenommenen.",
}

EN: dict[str, str] = {
    "hank.fix.lead_one": "What helps:",
    "hank.fix.lead_many": "What helps, most urgent first:",
    "hank.fix.high": "{label}: {value}, too high ({target}).",
    "hank.fix.low": "{label}: {value}, too low ({target}).",
    "hank.fix.urgent": "Act now: {line}",
    "hank.fix.card": "Card below: {labels}.",
    "hank.fix.light_wait": "I offer the next light step only 24 h after the last accepted one.",
    "hank.fix.light_hold": "More light means more heat: I offer the light step only once the air is no longer too "
                           "warm or too dry.",
    "hank.fix.stale": "Without fresh readings I suggest nothing.",
    "hank.fix.resolved": "What the note named is back on target.",
    "hank.fix.close_one": "In the chat I switch nothing without you: the card below waits for your confirmation.",
    "hank.fix.close_many": "In the chat I switch nothing without you: each card below waits for your confirmation.",
    "hank.fix.close_none": "In the chat I switch nothing without your tap, and I have no card to tap for this.",
    "hank.fix.none": "Everything is on target, nothing to fix.",
    # v1.2: a typed "Can you fix it?" about the chat's last topic (asking.fix_scope)
    "hank.fix.about_none": "The box does not measure {label}, so there is nothing I can fix.",
    "hank.fix.about_unknown": "Right now the box gives no reading for {label}, so there is nothing I can fix.",
    "hank.fix.about_ok": "{label} is on target right now, nothing to fix.",
    "hank.fix.about_untargeted": "The stage has no target for {label}, nothing to fix.",
    "hank.fix.lead_other_one": "For what else is off target, this helps:",
    "hank.fix.lead_other_many": "For what else is off target, this helps, most urgent first:",
    "hank.fix.rest_ok": "Everything else is on target.",
    # a card checked again at the tap (core.precheck_command, api.journal_wrap answers 409 with it)
    "hank.cardcheck.gone": "This card is no longer valid. Ask me again and I will work from the readings of now.",
    "hank.cardcheck.changed": "This card was changed, so I will not run it. Ask me again.",
    "hank.cardcheck.stale": "The readings are not fresh, so the card does not run now.",
    "hank.cardcheck.refused": "This card no longer fits the readings of now ({reason}). Ask me again.",
    "hank.cardcheck.light_wait": "The next light step comes only 24 h after the last accepted one.",
}


def check() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash."""
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        de, en = i18n.placeholders(DE[key]), i18n.placeholders(EN[key])
        if de != en:
            problems.append(f"placeholders differ: {key} {sorted(de)} {sorted(en)}")
        if "lang" in de | en:
            problems.append(f"placeholder lang: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    """Check parity and put the texts into the server catalogs (i18n.DE, i18n.EN). Safe to call more than once."""
    problems = check()
    if problems:
        raise ValueError("fix: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


@dataclass
class Fix:
    """The fix answer before it becomes an Answer: its lines, its cards, the verdicts whose advice it carries (for the
    ledger) and the range sources of those verdicts."""

    lines: list[str] = field(default_factory=list)
    cards: list[Proposal] = field(default_factory=list)
    advised: list[Verdict] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    nothing: bool = False  # nothing off target (the line "Alles im Ziel"): a bare "Was soll ich tun?" asks today.py
    link: dict[str, str] | None = None  # "#autopilot" under the bubble, when the autopilot is part of the answer


def key_of(verdict: Verdict) -> str:
    return f"{verdict.metric}:{verdict.status}"


def _alarmed(state: LiveState) -> set[str]:
    """The metrics of the active alarm rules (alerts.view rows: metric and fallback)."""
    out: set[str] = set()
    for alert in state.alerts_view:
        if isinstance(alert, dict) and alert.get("active"):
            out.update(str(m) for m in (alert.get("metric"), alert.get("fallback")) if m)
    return out


def _relative(verdict: Verdict) -> float:
    """How far a reading is outside its band, relative to the bound it broke (0 when it cannot be said)."""
    actual = verdict.actual
    bound = verdict.hi if verdict.status == "high" else verdict.lo
    if isinstance(actual, bool) or not isinstance(actual, int | float) or not isinstance(bound, int | float):
        return 0.0
    distance = float(actual) - float(bound) if verdict.status == "high" else float(bound) - float(actual)
    return distance / abs(float(bound)) if bound else distance


def ordered(found: Iterable[Verdict], state: LiveState) -> list[Verdict]:
    """Most urgent first: an active alarm rule first, then climate before light (verdicts.urgency), then the furthest
    from its band (relative); ties keep the order of the card's checks (the order of the traffic light's sentence)."""
    alarmed = _alarmed(state)
    items = list(found)
    return sorted(items, key=lambda v: (v.metric not in alarmed, verdicts.urgency(v.metric), -_relative(v)))


def _about_line(state: LiveState, about: Mapping[str, Any], lang: str) -> str:
    """Why the topic itself needs no fixing: the box does not measure it, has no reading, has no target for it in
    this stage, or it is on target."""
    labels = about.get("label") or {}
    label = labels.get(lang) or labels.get("de") or about.get("term") or about.get("metric") or "?"
    metric = about.get("metric")
    if not metric:
        return t("hank.fix.about_none", lang, label=label)
    row = next((r for r in state.checks if r.get("metric") == metric), None)
    if row is None:
        value = state.climate(metric)
        return t("hank.fix.about_unknown" if value is None else "hank.fix.about_untargeted", lang, label=label)
    if row.get("status") in ("unknown", None) or row.get("actual") is None:
        return t("hank.fix.about_unknown", lang, label=label)
    return t("hank.fix.about_ok", lang, label=label)


def _stale_lines(state: LiveState, lang: str) -> list[str]:
    age = state.sensors_age
    if age is None:
        first = t("hank.digest.age_none", lang)
        first = first[0].upper() + first[1:] + "." if first else first
    elif age > nudges.OFFLINE_SECONDS:
        first = t("hank.nudge.red_offline", lang, minutes=minutes(age))
    else:
        first = t("hank.nudge.yellow_age", lang, minutes=minutes(age))
    return [first, t("hank.fix.stale", lang)]


def _stage_waits(state: LiveState) -> str | None:
    """The estimated stage's key while the humidifier automation waits for a confirmed stage, else None."""
    auto = state.automation_view
    if not (auto.get("enabled") and auto.get("needs_stage_confirmation")):
        return None
    estimate = auto.get("estimate") if isinstance(auto.get("estimate"), dict) else {}
    key = estimate.get("key") or (state.stage_key if state.stage_set_by != "manual" else None)
    return str(key) if key else None


def _labels(cards: Iterable[Proposal]) -> str:
    return "; ".join(c.label for c in cards)


def compose(live: LiveState | Mapping[str, Any], lang: str, *, keys: Iterable[str] | None = None,
            now: float | None = None, about: Mapping[str, Any] | None = None) -> Fix:
    """The fix answer's lines and cards for the moment `live`. keys: the problems of the line or note it was tapped
    on (None or empty: every problem the traffic light would show now). about: the chat's topic a typed "Kannst du
    das beheben?" refers to ({metric, term, label}, asking.fix_scope); when the topic itself is not off target, the
    answer says so in one sentence and then covers what else is off."""
    lang = lang if lang in ("de", "en") else "de"
    state = collect(live)
    # an empty list (a note stored before the keys existed, the green line) means every problem, never "resolved"
    keys = list(keys) if keys is not None else None
    wanted = {str(k) for k in keys} if keys else None
    if about is not None and wanted is None:  # a topic without a metric ("PPFD"): nothing of it can be off
        wanted = set()
    if state.stale:
        return Fix(lines=_stale_lines(state, lang))
    if wanted == {"humidifier_tank"}:
        from . import humidifier_report
        return Fix(lines=humidifier_report.lines(state.automation_view, lang))
    found = verdicts.build(state, lang, statuses=verdicts.NEGATIVE, card=nudges._stage_card(),
                           resolve=nudges._resolver())
    every = list(found.verdicts)
    chosen = every if wanted is None else [v for v in every if key_of(v) in wanted]
    stage_key = _stage_waits(state)
    stage_wanted = stage_key is not None and (wanted is None or KEY_STAGE in wanted)
    lead_lines: list[str] = []
    other = False
    if about is not None and not chosen and not stage_wanted:
        # "can u fix it?" about VPD, and VPD is not off: say why in one sentence, then what else is off
        lead_lines.append(_about_line(state, about, lang))
        if not every and not stage_key:
            return Fix(lines=[*lead_lines, t("hank.fix.rest_ok", lang)], nothing=True)
        chosen = every
        stage_wanted = stage_key is not None
        other = True
    elif wanted is not None and not chosen and not stage_wanted and (every or stage_key):
        # what the note named is back on target, something else is off now: that is what the answer is about
        lead_lines.append(t("hank.fix.resolved", lang))
        chosen = every
        stage_wanted = stage_key is not None
    if not chosen and not stage_wanted:
        return Fix(lines=[t("hank.fix.none", lang)], nothing=True)
    chosen = ordered(chosen, state)
    alarmed = _alarmed(state)
    cards = proposals.from_checks(chosen, state, lang=lang, now=now)
    # The autopilot regulates a device itself (Automatik, device on "auto", not paused, no VIVOSUN plan or automation):
    # no card for it, a tap would only pause it for an hour and end its light ramp. The line says what it is doing
    # instead (review 2, M4: "Kannst du das beheben?" with the autopilot on still ended in "wartet auf deine
    # Bestätigung").
    regulated = pilot_report.regulated(state)
    by_pilot: dict[str, list[str]] = {}
    for card in cards:
        if card.kind in regulated:
            by_pilot.setdefault(card.reason, []).append(card.kind)
    cards = [c for c in cards if c.kind not in regulated]
    # More light means more heat: no light step while the air is already too warm or too dry (live test 23.10.2026
    # offered "Licht auf 75 %" at 25.5 °C and a VPD of 1.53 kPa, which the step would have pushed further up).
    heat = any(v.metric in ("probe_temp", "probe_vpd") and v.status == "high" for v in every)
    held_light = heat and any(c.kind == "light" for c in cards)
    if held_light:
        cards = [c for c in cards if c.kind != "light"]
    if stage_wanted and not any(c.kind == "plant_stage" for c in cards):
        try:
            cards.append(proposals.make({"action": "plant_stage", "key": stage_key}, state, lang, reason="stage",
                                        now=now))
        except proposals.Refused:
            pass
    # the humidity rule of from_checks brings the confirm card along (case live-04): then the stage gets its line
    stage_wanted = stage_wanted or any(c.kind == "plant_stage" for c in cards)
    problems = len(chosen) + (1 if stage_wanted else 0)
    lead = ("hank.fix.lead_other_many" if problems > 1 else "hank.fix.lead_other_one") if other else (
        "hank.fix.lead_many" if problems > 1 else "hank.fix.lead_one")
    lines = [*lead_lines, t(lead, lang)]
    light_wait = proposals.light_wait_until(state) is not None
    for verdict in chosen:
        target = verdicts.target_text(verdict.lo, verdict.hi, verdict.unit, verdict.when, lang)
        line = t(f"hank.fix.{verdict.status}", lang, label=verdict.label, value=verdicts.value_text(verdict, lang),
                 target=target)
        advice = one_line(verdict.advice)
        own = [c for c in cards if c.reason == key_of(verdict)]
        piloted = not own and (key_of(verdict) in by_pilot or verdict.metric == "light_percent"
                               and verdict.status == "low" and "light" in regulated)
        if advice and not piloted:  # the card's advice is for doing it by hand; the autopilot does it (07.11.2026)
            line = f"{line} {advice}"
        if own:
            line = f"{line} {t('hank.fix.card', lang, labels=_labels(own))}"
        elif key_of(verdict) in by_pilot:
            kinds = dict.fromkeys(by_pilot[key_of(verdict)])
            shown = "; ".join(pilot_report.device_status(regulated[k], lang) for k in kinds)
            line = f"{line} {t('hank.fix.pilot', lang, status=shown)}"
        elif verdict.metric == "light_percent" and verdict.status == "low" and "light" in regulated:
            line = f"{line} {t('hank.fix.pilot', lang, status=pilot_report.device_status(regulated['light'], lang))}"
        elif verdict.metric == "light_percent" and verdict.status == "low" and held_light:
            line = f"{line} {t('hank.fix.light_hold', lang)}"
        elif verdict.metric == "light_percent" and verdict.status == "low" and light_wait:
            wait_key = "hank.fix.light_wait_pilot" if proposals.light_wait_by_pilot(state) else "hank.fix.light_wait"
            line = f"{line} {t(wait_key, lang)}"
        if verdict.metric in alarmed:
            line = t("hank.fix.urgent", lang, line=line)
        lines.append(line)
    if stage_wanted:
        line = t("hank.nudge.yellow_stage", lang)
        own = [c for c in cards if c.kind == "plant_stage"]
        if own:
            line = f"{line} {t('hank.fix.card', lang, labels=_labels(own))}"
        lines.append(line)
    if not cards and by_pilot:
        close = "hank.fix.close_pilot"
    else:
        close = "hank.fix.close_none" if not cards else ("hank.fix.close_one" if len(cards) == 1
                                                         else "hank.fix.close_many")
    device_cards = any(c.kind in pilot_report.DEVICES for c in cards) or held_light
    pilot_mode = pilot_report.mode(state)
    link = None
    if pilot_mode in ("off", "observe") and device_cards:
        lines.append(t("hank.fix.pilot_off", lang))  # where it could run by itself; Hank never switches it on
        link = pilot_report.link(lang)
    elif by_pilot:
        link = pilot_report.link(lang)
    lines.append(t(close, lang))
    refs: list[SourceRef] = []
    for index, verdict in enumerate(found.verdicts):
        if verdict in chosen:
            refs += [r for r in found.sources.get(index, ()) if r not in refs]
    return Fix(lines=lines, cards=cards, advised=chosen, sources=refs, link=link)


def answer(fix: Fix, lang: str, *, answer_id: str) -> Answer:
    """The Answer of the route "fix": final, code only, the cards attached."""
    lang = lang if lang in ("de", "en") else "de"
    return Answer(id=answer_id, route="fix", lang=lang, text="\n".join(fix.lines), sources=list(fix.sources),
                  proposals=list(fix.cards), clarify=None, chips=[], draft_only=False, note=None, timing={},
                  gap=False, link=fix.link)


__all__ = ["DE", "EN", "Fix", "answer", "check", "compose", "key_of", "ordered", "register"]
