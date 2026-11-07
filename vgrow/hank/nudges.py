"""Hank's traffic light, his notes and the scene choice (spec 6.3, 7, 9.2; addendum N6), from code every minute.

tick(live, ledger, now) runs the ledger's minute (ledger.tick) and then sets the light. The WP8 ticker calls this
one function; it never calls ledger.tick on its own as well.

The light (one line above the plant card, addendum N6), first match wins:
  red    readings older than OFFLINE_SECONDS (15 min, the automation's fail-safe age)      hank.nudge.red_offline
  red    an alarm rule of alerts.json is active                                              hank.nudge.red_alert
  yellow no readings yet                                                                     hank.digest.age_none
  yellow readings older than automation.STALE_SECONDS (10 min, no verdicts from them)        hank.nudge.yellow_age
  yellow a check row off target (low or high)                                                hank.nudge.yellow_check
  yellow the humidifier automation is on and waits for a confirmed stage                     hank.nudge.yellow_stage
  green  otherwise                                                                           hank.nudge.green
Red uses only the alarm rules, never a verdict alone: the alarm limits are the cockpit's own thresholds.

Debounce: a worse level shows at once; a better one only after it held DEBOUNCE_SECONDS (start value, spec 0: not
measured), so a reading swinging around a limit does not make the light blink.

Notes are chat messages (addendum N6: no inbox), never pushes (D7). No flood (owner, 23.10.2026: four "Take a closer
look" notes in a row with nearly the same content), in the spirit of spec 6.3:
- a new note ("op": "new") only when the shown level rises (green to yellow, yellow to red, or the first tick) or a
  new red problem appears (an alarm rule or the offline state that the live note did not name yet), and never for a
  reason already noted within the cooldown of the ledger rules (6 h, like remind_hours in alerts.json): the same set
  is never noted twice;
- while the problem set changes at the same level, the one live note is updated in place ("op": "update", same chat
  id, its revision counts up), and so is the live note when a level rise repeats a set already noted;
- a ledger "worse" event (spec 6.3, hank.nudge.worse) moves the live note to the bottom as unread ("op": "raise"),
  still one note;
- a new or raised note collapses the older unacknowledged notes into itself (ChatRing.add(collapse=True): their ids
  go into "replaces", and the page removes them).
The caller (core.tick) applies the ops to the chat ring. Every note carries the problem keys of its moment ("fix"),
so the page's "Beheben" button asks for exactly those (fix.py); the light carries them too ("fix" in light()).
At red with an alarm, the note and the light carry one card for that check (spec 9.2), the card id stays the same
while the card does; a note with a card is a ledger entry (spec 6.2: a note that carries a check's advice).

Scene (spec 9.2): point at red (no jokes at red), sleep with the light off, umbrella too humid, fan too warm, scarf too
cold, else idle; none in the modes "warnings" and "off". Mode "off": nothing at all, the ledger rests too (spec 7).

State STATE_DIR/hank/nudge-state.json: the shown level and since when, a better level waiting out the debounce, the
texts shown, the locks per reason, the current red card.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vgrow import automation, config
from vgrow.i18n import localize, t

from . import humidifier_report, proposals, stores, thirst_report, verdicts
from .livestate import LiveState, collect, minutes
from .types import Proposal, SourceRef

_LOGGER = logging.getLogger(__name__)

LANGS = ("de", "en")
LEVELS = ("green", "yellow", "red")
RANK = {level: i for i, level in enumerate(LEVELS)}
MODES = ("on", "warnings", "off")
OFFLINE_SECONDS = automation.FAILSAFE_SECONDS  # 15 min: the age at which the automation's fail-safe acts
DEBOUNCE_SECONDS = 5 * 60  # start value (spec 0), not measured: a better level must hold this long
NOTE_COOLDOWN_SECONDS = 6 * 60 * 60  # fallback when the ledger rules cannot be read (spec 6.3, remind_hours)
SCENES = ("sleep", "umbrella", "fan", "scarf", "point", "idle")
HUMID = ("probe_humidity", "humidity")
WARM = ("probe_temp", "temp")


@dataclass
class Assessment:
    """The raw light of one moment, in both languages."""

    level: str
    reason: str
    text: dict[str, str]
    sources: dict[str, list[SourceRef]] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    negative: list[Any] = field(default_factory=list)  # the verdicts off target (German set)
    problems: list[str] = field(default_factory=list)  # the problem keys of fix.py ("probe_humidity:high", "stage")


@dataclass
class NudgeState:
    """What the page shows, from GET /api/hank/state (appendix B: light {level, text, since, proposal, sources})."""

    level: str | None
    reason: str | None = None
    text: dict[str, str] = field(default_factory=dict)
    since: float | None = None
    proposal: Proposal | None = None
    labels: dict[str, str] = field(default_factory=dict)  # the card's label per language
    sources: dict[str, list[SourceRef]] = field(default_factory=dict)
    scene: str | None = None
    raw: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)  # note ops for the chat ring (new, update, raise)
    events: list[Any] = field(default_factory=list)  # the ledger's events of this tick
    mode: str = "on"
    problems: list[str] = field(default_factory=list)  # the shown level's problem keys (fix.py)

    @property
    def hidden(self) -> bool:
        return self.level is None

    def light(self, lang: str) -> dict[str, Any] | None:
        if self.level is None:
            return None
        card = None
        if self.proposal is not None:
            card = {"id": self.proposal.id, "kind": self.proposal.kind, "body": dict(self.proposal.body),
                    "label": self.labels.get(lang) or self.proposal.label, "two_taps": self.proposal.two_taps,
                    "expires": self.proposal.expires}
        return {"level": self.level, "text": self.text.get(lang) or self.text.get("de", ""), "since": self.since,
                "proposal": card,
                "sources": [{"id": s.id, "title": s.title, "url": s.url} for s in self.sources.get(lang, [])],
                "fix": list(self.problems)}

    def scene_dict(self, lang: str) -> dict[str, str] | None:
        return None if self.scene is None else {"id": self.scene, "text": t(f"hank.scene.{self.scene}", lang)}


def _sentence(line: str) -> str:
    line = line.strip()
    if not line:
        return line
    line = line[0].upper() + line[1:]
    return line if line.endswith((".", "!", "?")) else line + "."


def _alert_label(alert: Mapping[str, Any], lang: str) -> str:
    """An alarm rule's name in this language (alerts.json label and label_en); the view's label as fallback."""
    try:
        raw = (config.alert_rules().get("rules") or {}).get(alert.get("name"))
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict):
        return str(localize(raw, lang).get("label") or alert.get("label") or alert.get("name"))
    return str(alert.get("label") or alert.get("name"))


def _resolver() -> Any:
    from .context import SourceBook

    return SourceBook()


def _stage_card() -> str | None:
    from .ledger import _stage_card as card

    return card()


def assess(live: LiveState | Mapping[str, Any]) -> Assessment:
    """The raw level and its sentence in both languages (no debounce)."""
    state = collect(live)
    age = state.sensors_age
    if age is not None and age > OFFLINE_SECONDS:
        return Assessment("red", "offline", {lang: t("hank.nudge.red_offline", lang, minutes=minutes(age))
                                             for lang in LANGS}, problems=["offline"])
    alerts = [a for a in state.alerts_view if isinstance(a, dict) and a.get("active")]
    if alerts:
        names = "+".join(sorted(str(a.get("name")) for a in alerts))
        metrics = {a.get("metric") for a in alerts} | {a.get("fallback") for a in alerts}
        checks = [f"{v.metric}:{v.status}" for v in verdicts.build(state, "de", statuses=("low", "high")).verdicts
                  if v.metric in metrics]
        return Assessment("red", f"alert:{names}", {
            lang: _sentence(t("hank.nudge.red_alert", lang, detail=", ".join(_alert_label(a, lang) for a in alerts)))
            for lang in LANGS}, alerts=alerts,
            problems=[*(f"alert:{a.get('name')}" for a in sorted(alerts, key=lambda a: str(a.get("name")))), *checks])
    if age is None:
        return Assessment("yellow", "no_readings", {lang: _sentence(t("hank.digest.age_none", lang))
                                                    for lang in LANGS}, problems=["no_readings"])
    if state.stale:
        return Assessment("yellow", "age", {lang: t("hank.nudge.yellow_age", lang, minutes=minutes(age))
                                            for lang in LANGS}, problems=["age"])
    if humidifier_report.warning(state.automation_view, "de"):
        return Assessment("yellow", "humidifier_tank", {
            lang: humidifier_report.warning(state.automation_view, lang) or "" for lang in LANGS},
            problems=["humidifier_tank"])
    card = _stage_card()
    resolve = _resolver()
    sets = {lang: verdicts.by_urgency(verdicts.build(state, lang, statuses=("low", "high"), card=card,
                                                     resolve=resolve))
            for lang in LANGS}  # climate first, light last (v1.2)
    if sets["de"].verdicts:
        metrics = "+".join(sorted(f"{v.metric}:{v.status}" for v in sets["de"].verdicts))
        sources: dict[str, list[SourceRef]] = {}
        for lang, vs in sets.items():
            refs: list[SourceRef] = []
            for index in range(len(vs.verdicts)):
                refs += [r for r in vs.sources.get(index, ()) if r not in refs]
            sources[lang] = refs
        return Assessment("yellow", f"check:{metrics}", {
            lang: t("hank.nudge.yellow_check", lang, detail=" ".join(vs.sentences(lang))) for lang, vs in sets.items()},
            sources=sources, negative=list(sets["de"].verdicts),
            problems=[f"{v.metric}:{v.status}" for v in sets["de"].verdicts])
    auto = state.automation_view
    if auto.get("enabled") and auto.get("needs_stage_confirmation"):
        return Assessment("yellow", "stage", {lang: t("hank.nudge.yellow_stage", lang) for lang in LANGS},
                          problems=["stage"])
    try:  # the Durst-Index: yellow while the watering asks, is due, is blocked or alerts (thirst_report.py)
        water = thirst_report.light(state, Assessment)
    except Exception:  # noqa: BLE001 (a malformed brief never breaks the light)
        _LOGGER.exception("Hank nudges: the thirst brief could not be read")
        water = None
    if water is not None:
        return water
    return Assessment("green", "green", {lang: t("hank.nudge.green", lang) for lang in LANGS})


def scene(live: LiveState | Mapping[str, Any], level: str | None, mode: str = "on") -> str | None:
    """The scene id (spec 9.2), or None outside the mode "on". At red always "point": no jokes at red."""
    if mode != "on" or level is None:
        return None
    if level == "red":
        return "point"
    state = collect(live)
    if proposals.light_level(state) == 0:
        return "sleep"
    if state.stale:
        return "idle"
    off = {(row.get("metric"), row.get("status")) for row in state.checks}
    if any((m, "high") in off for m in HUMID):
        return "umbrella"
    if any((m, "high") in off for m in WARM):
        return "fan"
    if any((m, "low") in off for m in WARM):
        return "scarf"
    return "idle"


def _epoch(now: Any, state: LiveState) -> float:
    if now is None:
        return state.now.timestamp()
    if isinstance(now, datetime):
        return now.timestamp()
    return float(now)


def _red_card(found: Assessment, state: LiveState, ledger: Any, doc: dict[str, Any],
              stamp: float) -> tuple[Proposal | None, dict[str, str]]:
    """The one card at red with an alarm: the first card for the alarm's check, the same id while it is the same."""
    metrics = {a.get("metric") for a in found.alerts} | {a.get("fallback") for a in found.alerts}
    vs = verdicts.build(state, "de", statuses=("low", "high"))
    wanted = [v for v in vs.verdicts if v.metric in metrics]
    cards = proposals.from_checks(wanted, state, lang="de", now=stamp)
    cards = [c for c in cards if c.reason != "stage"]
    if ledger is not None:
        cards = ledger.offerable(cards, state)
    if not cards:
        doc.pop("proposal", None)
        return None, {}
    card = cards[0]
    found.negative = [v for v in wanted if f"{v.metric}:{v.status}" == card.reason]  # the check the card is for
    old = doc.get("proposal") if isinstance(doc.get("proposal"), dict) else None
    if old and old.get("body") == card.body and float(old.get("expires") or 0) > stamp:
        card = Proposal(id=str(old["id"]), kind=card.kind, body=card.body, label=card.label, reason=card.reason,
                        expires=float(old["expires"]), two_taps=card.two_taps)
    doc["proposal"] = {"id": card.id, "body": card.body, "expires": card.expires, "reason": card.reason}
    return card, {lang: proposals.label(card.body, state, lang) for lang in LANGS}


def tick(live: LiveState | Mapping[str, Any], ledger: Any = None, now: Any = None, *, mode: str = "on",
         store: Any = None) -> NudgeState:
    """One minute of Hank's side of the page: the ledger's tick, the light with its debounce, new notes, the scene.
    live: a LiveState or the collector's mapping; ledger: a ledger.Ledger (None: no ledger); now: epoch seconds or a
    datetime (default: the live state's time); store: the nudge-state document (default nudge-state.json)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    state = collect(live)
    if mode == "off":
        return NudgeState(level=None, mode=mode)
    stamp = _epoch(now, state)
    events = []
    if ledger is not None:
        try:
            events = ledger.tick(state, stamp)
        except Exception:  # the light must not die with the ledger (spec 10.1 rule 7)
            _LOGGER.exception("ledger tick failed")
    store = store if store is not None else stores.JsonDoc("nudge-state.json", {"version": 1})
    doc = store.load()
    found = assess(state)
    shown = doc.get("level") if doc.get("level") in LEVELS else None
    pending = doc.get("pending") if isinstance(doc.get("pending"), dict) else None
    note = doc.get("note") if isinstance(doc.get("note"), dict) else {}  # the live note, as this module last set it
    op: str | None = None  # what the chat's live note gets this tick: new, update, or nothing

    def adopt(since: float | None) -> None:
        doc.update(level=found.level, reason=found.reason, text=found.text, problems=list(found.problems),
                   sources={k: [[s.id, s.title, s.url] for s in v] for k, v in found.sources.items()})
        if since is not None:
            doc["since"] = since
        doc.pop("pending", None)

    if shown is None or RANK[found.level] > RANK[shown]:  # worse (or the first tick ever): at once, a new note
        adopt(stamp)
        op = "new" if found.level != "green" else None
    elif found.level == shown:  # same level: current numbers, maybe another set of problems
        changed = found.reason != doc.get("reason")
        adopt(None)
        if changed and found.level != "green":
            said = set(note.get("problems") or []) if note.get("level") == "red" else set()
            red_new = found.level == "red" and any(p not in said for p in found.problems
                                                   if p.startswith(("alert:", "offline")))
            op = "new" if red_new else "update"  # a new red problem is a new note; anything else updates the live one
    elif pending and pending.get("level") == found.level:  # better, and it held
        if stamp - float(pending.get("since") or stamp) >= DEBOUNCE_SECONDS:
            adopt(stamp)
    else:  # better: wait it out
        doc["pending"] = {"level": found.level, "since": stamp}
    level = str(doc["level"])

    card, labels = (None, {})
    if level == "red" and found.level == "red" and found.alerts:
        card, labels = _red_card(found, state, ledger, doc, stamp)
    else:
        doc.pop("proposal", None)

    cooldown = getattr(ledger, "cooldown_seconds", None) or NOTE_COOLDOWN_SECONDS
    locks = doc.get("locks") if isinstance(doc.get("locks"), dict) else {}
    messages: list[dict[str, Any]] = []
    if op == "new" and stamp - float(locks.get(found.reason) or 0) < cooldown:
        op = "update"  # this set was noted within the cooldown: never the same set twice, the live note shows it
    if op == "update" and note.get("reason") == found.reason:
        op = None  # the live note already says exactly this
    if op is not None and level != "green":
        texts = dict(found.text)
        if card is not None:
            texts = {lang: f"{texts[lang]} {t('hank.proposal.sentence', lang, label=labels[lang])}" for lang in LANGS}
        messages.append({"kind": "nudge", "op": op, "level": level, "reason": found.reason, "text": texts,
                         "proposal": card.id if card is not None else None, "fix": list(found.problems)})
        if op == "new":
            locks[found.reason] = stamp
        note = {"reason": found.reason, "level": level, "problems": list(found.problems)}
        if card is not None and ledger is not None and found.negative:
            try:
                ledger.record(found.negative, live=state, answer_id=f"n-{int(stamp)}", proposals=[card], now=stamp)
            except Exception:
                _LOGGER.exception("ledger record of a note failed")
    worse: dict[str, list[str]] = {lang: [] for lang in LANGS}
    worse_reason = None
    for event in events:
        if getattr(event, "kind", None) != "worse":
            continue
        reason = f"worse:{event.key}"
        if stamp - float(locks.get(reason) or 0) < cooldown:
            continue
        metric, status = event.detail.get("metric"), event.detail.get("status")
        for lang in LANGS:
            vs = verdicts.build(state, lang, metrics=[metric], statuses=[status])
            worse[lang].append(" ".join(vs.sentences(lang)) or str(metric))
        worse_reason = worse_reason or reason
        locks[reason] = stamp
    if worse_reason is not None:  # spec 6.3: Hank speaks again; the live note comes back to the bottom, unread
        problems = [str(p) for p in doc.get("problems") or []]
        messages.append({"kind": "nudge", "op": "raise", "level": level, "reason": worse_reason,
                         "text": {lang: t("hank.nudge.worse", lang, detail=" ".join(worse[lang])) for lang in LANGS},
                         "proposal": None, "fix": problems})
        note = {"reason": worse_reason, "level": level, "problems": problems}
    doc["note"] = note
    doc["locks"] = locks
    store.save(doc)

    sources = {lang: [SourceRef(*row) for row in rows] for lang, rows in (doc.get("sources") or {}).items()
               if isinstance(rows, list)}
    return NudgeState(level=level, reason=str(doc.get("reason")), text=dict(doc.get("text") or {}),
                      since=float(doc.get("since") or stamp), proposal=card, labels=labels, sources=sources,
                      scene=scene(state, level, mode), raw=found.level, messages=messages, events=events, mode=mode,
                      problems=[str(p) for p in doc.get("problems") or []])
