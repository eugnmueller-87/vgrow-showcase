"""The today answer (WP11 follow-up, 23.10.2026): "Was ist heute zu tun?", "What should I do today?".

The router sends these questions to the route "today" (data/gates.json "today", router.py). The answer comes from
code alone, from the same read-only moment every other answer sees (livestate.collect) and from the cards Hank has
handed out and that are still open (proposals.Registry.open). It never calls the model and never switches anything:
a card is still only a suggestion the owner taps.

At most MAX_LINES short lines, most urgent first:
1. red: readings older than the fail-safe age (nudges.OFFLINE_SECONDS), an active alarm rule with the one-line advice
   of its check;
2. yellow: no readings yet, readings too old for verdicts, a check off target (low or high) with its one-line advice
   (the first sentence of the card's advice, without source marks);
   While the VPD at the probe is above its band (vpd.py, 23.10.2026), its line says why in plain words (too dry for
   this temperature, with the humidity it needs), what already runs and what blocks the humidifier rule and the
   autopilot, with their two-tap cards or the place on the page; the light line says why the light waits, and a
   light without a plan is named once, with the stage card's own light-cycle note;
2b. "Tank auffüllen" while the wet/dry water sensor reports dry, with the alarm's own debounce (tank_confirmed),
   unless the tank alarm's red line says it already;
3. a stage that is only estimated (confirm it), with what waits for it (the humidifier automation, the amounts), and,
   while the bloom start is only the camera's estimate, the question for the real date (v1.2, owner 23.10.2026),
   asked once per estimated date, then a short reminder (asking.bloom_line, core.Hank._today);
   The plant's start shares that line (feeding v2, spec 4.10): while it is unknown or only approximate, the question
   for it, asked once per stored value, then a short reminder (feedplan.age_line, core.Hank._today: age_asked);
3b. "Höhe heute eintragen" when the newest height is older than two days (v1.2, asking.height_due);
4. two fertilizers and none chosen: "Dünger wählen", once, with the reason (feeding v2: no amounts until then); else a
   feeding card the owner has not confirmed (or must confirm again);
5. the feeding of this week, only when card and stage are confirmed: the amounts per litre exactly as feeding.view
   computed them, in the active card's unit (grams with spoons for VIVOSUN, ml for BioBizz), with the dose factor
   note of addendum N5 in short (factor included, never more than the chart) and the date of the next change; in the
   plain-water week that sentence;
6. the open suggestion cards (their labels; the cards themselves go with the answer, so one tap applies them).
Nothing open: "Alles im Ziel, heute nichts zu tun.", followed by the feeding line when there is one.

The answer is final, without a polish job: five lines of code facts gain nothing from the model and would wait 45 to
83 s for it (WP11). Texts: the catalog below, registered into i18n like texts.py and texts_live.py; keys of those two
catalogs are reused where the wording already exists (nudges, proposal sentences, plain-water week).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from vgrow import i18n
from vgrow.i18n import fmt_num, t

from . import asking, feedplan, nudges, pilot_report, proposals, thirst_report, verdicts, vpd
from .livestate import LiveState, collect, minutes
from .types import Answer, Proposal

MAX_LINES = 6  # v1.2: one more for the height; the bloom question shares the stage line
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
_SOURCE_MARK = re.compile(r"\s*\((?:[SN]\d+)(?:\s*,\s*[SN]\d+)*\)")  # "(S1)" in a card's advice
_FIRST_SENTENCE = re.compile(r"(.+?[.!?])(?=\s|$)")

DE: dict[str, str] = {
    "hank.today.alert": "Jetzt handeln, {label}: {advice}",
    "hank.today.alert_plain": "Jetzt handeln: {label}.",
    "hank.today.high": "{label}: {value}, zu hoch ({target}).",
    "hank.today.low": "{label}: {value}, zu niedrig ({target}).",
    "hank.today.stage": "Phase bestätigen: ich schätze {stage}.",
    "hank.today.stage_auto": "Bis dahin schaltet die Befeuchter-Automatik nicht ein.",
    "hank.today.stage_feed": "Die Düngermengen rechnet das Cockpit erst danach.",
    "hank.today.feed_card": "Düngerkarte bestätigen: bis dahin rechnet das Cockpit keine Mengen.",
    "hank.today.feed_reconfirm": "Düngerkarte neu bestätigen: die Zahlen haben sich geändert, bis dahin keine "
                                 "Mengen.",
    "hank.today.feed_dose": "Wenn du heute gießt, je Liter ({week}): {items}. Faktor {factor} ist eingerechnet, nie "
                            "mehr als die Tabelle.",
    "hank.today.none": "Alles im Ziel, heute nichts zu tun.",
    # the tank (type-9 wet/dry water sensor): while it is dry
    "hank.today.tank": "Tank auffüllen. Mit dem Docht zieht die Pflanze ihr Wasser aus diesem Tank.",
}

EN: dict[str, str] = {
    "hank.today.alert": "Act now, {label}: {advice}",
    "hank.today.alert_plain": "Act now: {label}.",
    "hank.today.high": "{label}: {value}, too high ({target}).",
    "hank.today.low": "{label}: {value}, too low ({target}).",
    "hank.today.stage": "Confirm the stage: my estimate is {stage}.",
    "hank.today.stage_auto": "Until then the humidifier automation does not switch on.",
    "hank.today.stage_feed": "The cockpit computes the feeding amounts only after that.",
    "hank.today.feed_card": "Confirm the feeding card: until then the cockpit computes no amounts.",
    "hank.today.feed_reconfirm": "Confirm the feeding card again: its numbers changed, no amounts until then.",
    "hank.today.feed_dose": "If you water today, per litre ({week}): {items}. Factor {factor} is included, never more "
                            "than the chart.",
    "hank.today.none": "All on target, nothing to do today.",
    # the tank (type-9 wet/dry water sensor): while it is dry
    "hank.today.tank": "Refill the tank. With the wick the plant draws its water from this tank.",
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
        raise ValueError("today: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sentence(line: str) -> str:
    line = line.strip()
    if not line:
        return line
    line = line[0].upper() + line[1:]
    return line if line.endswith((".", "!", "?")) else line + "."


def one_line(advice: Any) -> str:
    """The first sentence of a card's advice, without source marks: "Zu feucht für die Blüte: Luftbefeuchter (USB 2)
    aus, Abluft höher." from the advice of the humidity check."""
    plain = _SOURCE_MARK.sub("", str(advice or "")).strip()
    if not plain:
        return ""
    found = _FIRST_SENTENCE.match(plain)
    return _sentence(found.group(1) if found else plain)


def _alarms(state: LiveState, negative: list[Any], lang: str) -> tuple[list[str], set[str]]:
    """Red: stale beyond the fail-safe age, or the active alarm rules with their check's advice."""
    age = state.sensors_age
    if age is not None and age > nudges.OFFLINE_SECONDS:
        return [t("hank.nudge.red_offline", lang, minutes=minutes(age))], set()
    lines: list[str] = []
    said: set[str] = set()
    for alert in state.alerts_view:
        if not isinstance(alert, dict) or not alert.get("active"):
            continue
        name = nudges._alert_label(alert, lang)
        metrics = {alert.get("metric"), alert.get("fallback")}
        advice = next((one_line(v.advice) for v in negative if v.metric in metrics and one_line(v.advice)), "")
        if alert.get("metric") == TANK_METRIC:
            advice = t("hank.today.tank", lang)  # the tank alarm says what to do: refill
        said.update(v.metric for v in negative if v.metric in metrics)
        lines.append(t("hank.today.alert", lang, label=name, advice=advice) if advice
                     else t("hank.today.alert_plain", lang, label=name))
    return lines, said


def _checks(state: LiveState, negative: list[Any], said: set[str], lang: str,
            extra: Mapping[str, str] | None = None) -> list[str]:
    """Yellow: no readings, readings too old, a check off target with its one-line advice, or what the autopilot does
    when it regulates that device itself; extra: more sentences for the line of one metric (the VPD explanation)."""
    if state.sensors_age is None:
        return [_sentence(t("hank.digest.age_none", lang))]
    if state.stale:
        return [t("hank.nudge.yellow_age", lang, minutes=minutes(state.sensors_age))]
    regulated = pilot_report.regulated(state)
    lines = []
    for verdict in negative:
        if verdict.metric in said:
            continue
        value = verdicts.value_text(verdict, lang)
        target = verdicts.target_text(verdict.lo, verdict.hi, verdict.unit, verdict.when, lang)
        line = t(f"hank.today.{verdict.status}", lang, label=verdict.label, value=value, target=target)
        device = PILOT_DEVICE.get((verdict.metric, verdict.status))
        if device in regulated:  # the card's advice is for doing it by hand; the autopilot does it (07.11.2026)
            line = f"{line} {t('hank.fix.pilot', lang, status=pilot_report.device_status(regulated[device], lang))}"
        else:
            advice = one_line(verdict.advice)
            line = f"{line} {advice}" if advice else line
        more = (extra or {}).get(verdict.metric)
        lines.append(f"{line} {more}" if more else line)
    return lines


TANK_METRIC = "water_state"  # the tank alarm's metric (alerts.json tank_empty, vgrow/alerts.py)
# The autopilot device that does a check's advice itself (the votes of vgrow/autopilot.py): the exhaust for the
# temperature and for too humid air, the light ramp for too little light. Too dry is the humidifier rule's, whose own
# line says what runs and what blocks (vpd.py).
PILOT_DEVICE = {("probe_temp", "high"): "dfan", ("probe_temp", "low"): "dfan", ("temp", "high"): "dfan",
                ("temp", "low"): "dfan", ("probe_humidity", "high"): "dfan", ("humidity", "high"): "dfan",
                ("probe_vpd", "low"): "dfan", ("vpd", "low"): "dfan", ("light_percent", "low"): "light"}


def _tank(state: LiveState, lang: str) -> list[str]:
    """ "Tank auffüllen" while the wet/dry sensor reports dry on fresh readings for the alarm's own minutes (the same
    debounce as the push, vgrow/alerts.py tank_confirmed), unless its alarm line says it."""
    from vgrow.alerts import tank_confirmed  # here, not at the top: Hank's modules load without the alarm module

    if state.stale or state.tank_state != "dry" or not tank_confirmed(state.snapshot):
        return []
    if any(isinstance(a, dict) and a.get("active") and a.get("metric") == TANK_METRIC for a in state.alerts_view):
        return []
    return [t("hank.today.tank", lang)]


def _stage(state: LiveState, lang: str, *, bloom_asked: bool = False, age_asked: bool = False) -> str | None:
    """The estimated stage to confirm, with what waits for it, and the bloom start question while that date is only
    the camera's estimate (one line: both are about the same estimate); the plant's start question shares the line
    (feeding v2), so it displaces nothing."""
    stage = state.stage
    bloom = asking.bloom_line(state, lang, asked=bloom_asked)
    age = feedplan.age_line(state, lang, asked=age_asked)
    if not stage or stage.get("set_by") == "manual":
        return " ".join(part for part in (bloom, age) if part) or None
    parts = [t("hank.today.stage", lang, stage=verdicts.stage_name(stage, lang))]
    auto = state.automation_view
    if auto.get("enabled") and auto.get("needs_stage_confirmation"):
        parts.append(t("hank.today.stage_auto", lang))
    if state.feeding_view.get("state") == "needs_stage":
        parts.append(t("hank.today.stage_feed", lang))
    if bloom:
        parts.append(bloom)
    if age:
        parts.append(age)
    return " ".join(parts)


def _feed_card(state: LiveState, lang: str) -> str | None:
    """The feeding card to deal with: the choice between two fertilizers first (nothing doses until then), else a
    card that is not confirmed."""
    choice = feedplan.choice_line(state, lang)
    if choice:
        return choice
    if state.feeding_view.get("state") != "draft":
        return None
    return t("hank.today.feed_reconfirm" if state.feeding_view.get("reconfirm") else "hank.today.feed_card", lang)


def _feeding(state: LiveState, lang: str) -> str | None:
    """This week's amounts per litre, only as feeding.view computed them (state ok: card and stage confirmed)."""
    view = state.feeding_view
    week = _dict(view.get("week"))
    if view.get("state") == "water_only" and view.get("not_before"):  # the owner's date for this run
        return str(view.get("warning") or "") or None
    if view.get("state") == "water_only" and week.get("label"):
        return t("hank.fact.feed_water_only", lang, week=week["label"])
    if view.get("state") == "label_ends":  # the table has ended: no amount, the owner decides (feedplan's words)
        return feedplan.plan_lines(state, lang)[0]
    if view.get("state") != "ok" or not view.get("doses") or not week.get("label"):
        return None
    # never "X ? g": a dose without a number says nothing (feeding.view always gives one in ok); the unit is the
    # active card's (feeding v2: grams with spoons for VIVOSUN, ml for BioBizz)
    items = feedplan.amount_items(view["doses"], feedplan.unit_of(view), lang)
    factor = view.get("dose_factor")
    if not items or not _number(factor):
        return None
    line = t("hank.today.feed_dose", lang, week=week["label"], items=", ".join(items), factor=fmt_num(factor, lang))
    change = feedplan.next_change_text(view, lang)
    return f"{line} {change}" if change else line


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _open_cards(cards: Iterable[Proposal], state: LiveState, lang: str) -> list[Proposal]:
    """The open cards that still pass their checks now, labelled in the answer's language (a card keeps its id)."""
    out = []
    for card in cards:
        try:
            proposals.check_body(card.body, state)
        except proposals.Refused:
            continue
        out.append(replace(card, label=proposals.label(card.body, state, lang)))
    return out


def lines(live: LiveState | Mapping[str, Any], lang: str, *, cards: Iterable[Proposal] = (),
          bloom_asked: bool = False, age_asked: bool = False) -> tuple[list[str], list[Proposal]]:
    """The lines of the today answer (at most MAX_LINES, most urgent first) and the open cards that go with it.
    bloom_asked: the bloom start question was asked before for this estimate (then a short reminder); age_asked: the
    same for the plant's start (feedplan.age_asked_key)."""
    state = collect(live)
    negative = [] if state.stale else verdicts.by_urgency(
        verdicts.build(state, lang, statuses=("low", "high"))).verdicts  # climate first, light last (v1.2)
    red, said = _alarms(state, negative, lang)
    stage = _stage(state, lang, bloom_asked=bloom_asked, age_asked=age_asked)
    found = vpd.explain(state, lang)
    extra: dict[str, str] = {}
    if found is not None:
        # The VPD line says why, what runs and what blocks; the stage sentence only when no stage line says it.
        estimated = bool(state.stage) and state.stage_set_by != "manual"
        said_lines = found.lines(without_stage=estimated, without_head=True)
        light_low = any(v.metric == "light_percent" and v.status == "low" for v in negative)
        extra[vpd.METRIC] = " ".join(said_lines[:len(said_lines) - len(found.light)] if light_low else said_lines)
        if light_low and found.light:
            extra["light_percent"] = " ".join(found.light)
    from . import humidifier_report
    maintenance = humidifier_report.warning(state.automation_view, lang)
    todo = [*red, *([maintenance] if maintenance else []), *_checks(state, negative, said, lang, extra)]
    todo += _tank(state, lang)
    todo += thirst_report.today_lines(state, lang)  # the Durst-Index, only when something is to do
    todo += [line for line in (stage, asking.height_due(state, lang), _feed_card(state, lang)) if line]
    open_cards = _open_cards(cards, state, lang)
    if found is not None:  # the cards that clear the humidifier rule's blocks (two taps each), once per body
        open_cards += [c for c in found.cards if all(c.body != have.body for have in open_cards)]
    listed = [c for c in open_cards if not (stage and c.kind == "plant_stage")]  # the stage line says it already
    if listed:
        labels = [c.label for c in listed]
        todo.append(t("hank.proposal.sentence", lang, label=labels[0]) if len(labels) == 1
                    else t("hank.proposal.sentence_many", lang, labels="; ".join(labels)))
    feeding = _feeding(state, lang)
    if not todo:
        out = [t("hank.today.none", lang), *([feeding] if feeding else [])]
    else:
        cards_line = todo.pop() if listed else None  # the cards come last, after the feeding line
        out = [*todo, *([feeding] if feeding else []), *([cards_line] if cards_line else [])]
    return out[:MAX_LINES], open_cards


def answer(live: LiveState | Mapping[str, Any], lang: str, *, cards: Iterable[Proposal] = (),
           answer_id: str, bloom_asked: bool = False, age_asked: bool = False) -> Answer:
    """The Answer of the route "today": final, code only, the open cards attached."""
    lang = lang if lang in ("de", "en") else "de"
    found, open_cards = lines(live, lang, cards=cards, bloom_asked=bloom_asked, age_asked=age_asked)
    high = vpd.explain(live, lang)  # the VPD explanation names the Autopilot card: its link goes under the bubble
    return Answer(id=answer_id, route="today", lang=lang, text="\n".join(found), sources=[], proposals=open_cards,
                  clarify=None, chips=[], draft_only=False, note=None, timing={}, gap=False,
                  link=high.link if high is not None else None)


__all__ = ["DE", "EN", "MAX_LINES", "answer", "check", "lines", "one_line", "register"]
