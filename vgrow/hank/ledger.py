"""The recommendation ledger (spec 6; addendum N4): what Hank advised, and whether it was done. Code only, never the
model; it runs in the ticker every minute and when an answer or a card is done.

Document STATE_DIR/hank/ledger.json (written with config.atomic_write), items in the shape of spec 6.1:
  {"id", "created", "answer_id", "stage_key", "metric", "status", "when", "baseline": {"value", "dfan", ...},
   "recommendation": {"card", "kind", "proposal", "proposal_id"}, "state", "applied_at", "declined_at",
   "consequence_told", "told": {"at", "deviation"}, "worst": {"value", "at"}, "history": [{"t", "event"}]}
plus three maps beside the items:
  told      "<stage>|<metric>|<status>" -> {"at", "deviation"}: the consequence was told for this stage, metric and
            direction (addendum N4: "once" holds per key, not per entry, so a daily swing does not retell it);
  declined  "<stage>|<reason>|<action>" -> {"at", "deviation", ...}: a card declined, not offered again (spec 6.3);
  alerts    the alarm rules active at the last tick (a rule that newly turns active counts as worse).

States (spec 6.1): open -> applied (a watched device moved the right way, or the card was tapped), not_applied (the
grace time is over, the reading still off, nothing moved), declined ("Nicht jetzt"), resolved (the reading is back on
target), expired (open for 48 h, or the stage changed; addendum N4).

"Once, then only when worse" (spec 6.3, addendum N4): the told and declined marks are lifted only when the distance
from the band grew by more than worse_delta since the mark (or an alarm rule newly turned active for that metric and
direction), and at most once per cooldown (6 h), or when the stage changes. Hank never pushes (D7): a lifted mark is
an event the traffic light turns into a chat note.

At most one entry per (stage, metric, status) that is open, not applied or declined (spec 6.2). Readings older than
the automation's limit change no state (spec 4.3: no judgement from stale data); expiry by time still runs.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from vgrow import plant
from vgrow.i18n import t

from . import stores, verdicts
from .livestate import LiveState, collect
from .proposals import fan_step, humidifier_port, light_level, port_on
from .types import Proposal, SourceRef, Verdict

_LOGGER = logging.getLogger(__name__)

RULES_FILE = Path(__file__).resolve().parent / "data" / "ledger_rules.json"
STATES = ("open", "applied", "declined", "not_applied", "resolved", "expired")
ACTIVE = ("open", "applied", "not_applied", "declined")
AT_MOST = chr(0x2264)  # the op alerts.view gives an at_most rule
BLOCKING = ("open", "not_applied", "declined")  # a new entry for the same key waits while one of these exists
# Fallbacks when ledger_rules.json cannot be read: the spec's own values (6.2 grace 30 min, N4 48 h, 6.3 6 h).
GRACE_MINUTES = 30
OPEN_HOURS = 48
COOLDOWN_HOURS = 6
KEEP_DONE = 200  # resolved and expired entries kept for the record; the active ones always stay
DEFAULT = {"version": 1, "next": 1, "stage": None, "items": [], "told": {}, "declined": {}, "alerts": []}


@lru_cache(maxsize=4)
def _rules_cached(path: str, mtime: float) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("the top level is not an object")
    except (OSError, ValueError) as err:
        _LOGGER.error("%s unreadable (%s): no cards from checks, no worse detection, spec start values for times",
                      Path(path).name, err)
        return {}
    return data


def load_rules(path: Path = RULES_FILE) -> dict[str, Any]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    return _rules_cached(str(path), mtime)


def key_of(stage: Any, metric: Any, status: Any) -> str:
    return f"{stage}|{metric}|{status}"


def split_key(key: str) -> tuple[str, str, str]:
    parts = (key.split("|") + ["", "", ""])[:3]
    return parts[0], parts[1], parts[2]


def deviation(actual: Any, lo: Any, hi: Any, status: str) -> float | None:
    """How far a reading is outside its band, in the reading's unit; None when it cannot be said."""
    if isinstance(actual, bool) or not isinstance(actual, int | float):
        return None
    if status == "high" and isinstance(hi, int | float):
        return float(actual) - float(hi)
    if status == "low" and isinstance(lo, int | float):
        return float(lo) - float(actual)
    return None


def _row_deviation(row: Mapping[str, Any] | None, status: str) -> float | None:
    if not row or row.get("status") != status:
        return None
    return deviation(row.get("actual"), row.get("min"), row.get("max"), status)


def _stage_card() -> str | None:
    """The run's stage card as "stages/<name>" (plant.json stages_card)."""
    try:
        run = json.loads(plant.PLANT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = run.get("stages_card") if isinstance(run, dict) else None
    return f"stages/{name}" if name else None


@dataclass
class Event:
    """What a tick or a call changed: created, applied, not_applied, declined, resolved, expired, worse, reset."""

    kind: str
    key: str
    item: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Telling:
    """The consequence message after a declined card (spec 6.3): once with its source, then respect."""

    text: str
    sentences: list[str]
    sources: list[SourceRef]
    told: bool
    gap: dict[str, Any] | None = None


class Ledger:
    """store: something with load() -> dict and save(dict) (default stores.JsonDoc("ledger.json")); clock: epoch
    seconds (a fake clock in the tests); rules: the ledger_rules.json mapping (default: the file)."""

    def __init__(self, store: Any = None, clock: Callable[[], float] = time.time,
                 rules: Mapping[str, Any] | None = None) -> None:
        self.store = store if store is not None else stores.JsonDoc("ledger.json", DEFAULT)
        self.clock = clock
        self._rules = rules

    # ---------- rules ----------

    @property
    def rules(self) -> Mapping[str, Any]:
        return self._rules if self._rules is not None else load_rules()

    def _number(self, name: str, fallback: float) -> float:
        value = self.rules.get(name)
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else fallback

    @property
    def grace_seconds(self) -> float:
        return self._number("grace_minutes", GRACE_MINUTES) * 60

    @property
    def open_seconds(self) -> float:
        return self._number("open_hours", OPEN_HOURS) * 3600

    @property
    def cooldown_seconds(self) -> float:
        return self._number("cooldown_hours", COOLDOWN_HOURS) * 3600

    def moves(self, metric: str, status: str) -> list[dict[str, Any]]:
        table = self.rules.get("actions") or {}
        found = (table.get(metric) or {}).get(status) or [] if isinstance(table, Mapping) else []
        return [dict(m) for m in found if isinstance(m, Mapping)]

    def _delta(self, name: str, metric: str) -> float | None:
        table = self.rules.get(name) or {}
        value = table.get(metric) if isinstance(table, Mapping) else None
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

    # ---------- the document ----------

    def load(self) -> dict[str, Any]:
        data = self.store.load()
        for key, value in DEFAULT.items():
            if not isinstance(data.get(key), type(value)) and not (key == "stage" and isinstance(data.get(key), str)):
                data[key] = copy.deepcopy(value)
        data["items"] = [i for i in data["items"] if isinstance(i, dict) and i.get("id")]
        return data

    def _save(self, data: dict[str, Any]) -> None:
        done = [i for i in data["items"] if i.get("state") not in ACTIVE]
        if len(done) > KEEP_DONE:
            drop = {id(i) for i in done[: len(done) - KEEP_DONE]}
            data["items"] = [i for i in data["items"] if id(i) not in drop]
        self.store.save(data)

    def _now(self, now: float | None) -> float:
        return self.clock() if now is None else float(now)

    def items(self, *, active: bool = False) -> list[dict[str, Any]]:
        """Copies of the entries for the context (spec 6.1 shape); consequence_told and told follow the per-key
        mark (addendum N4)."""
        data = self.load()
        out = []
        for item in data["items"]:
            if active and item.get("state") not in ACTIVE:
                continue
            copied = copy.deepcopy(item)
            mark = data["told"].get(key_of(item.get("stage_key"), item.get("metric"), item.get("status")))
            copied["consequence_told"] = mark is not None
            copied["told"] = {"at": mark.get("at"), "deviation": mark.get("deviation")} if mark else {
                "at": None, "deviation": None}
            out.append(copied)
        return out

    def get(self, item_id: str) -> dict[str, Any] | None:
        return next((i for i in self.items() if i.get("id") == item_id), None)

    def is_told(self, stage: Any, metric: Any, status: Any) -> bool:
        return key_of(stage, metric, status) in self.load()["told"]

    @staticmethod
    def _history(item: dict[str, Any], when: str, event: str) -> None:
        item.setdefault("history", []).append({"t": when, "event": event})

    # ---------- writing entries ----------

    def _baseline(self, verdict: Verdict, moves: list[dict[str, Any]], live: LiveState) -> dict[str, Any]:
        out: dict[str, Any] = {"value": verdict.actual}
        for move in moves:
            action = move.get("action")
            if action in ("dfan", "cfan"):
                out[str(action)] = fan_step(live, str(action))
            elif action == "light":
                out["light"] = light_level(live)
            elif action == "port":
                port = humidifier_port(live) if move.get("port") == "humidifier" else str(move.get("port"))
                out[f"{port}_on"] = port_on(live, port)
        return out

    @staticmethod
    def _kind(body: Mapping[str, Any] | None, live: LiveState) -> str:
        if not body:
            return "advice"
        action = body.get("action")
        if action in ("dfan", "cfan"):
            now = fan_step(live, str(action))
            return f"{action}_{'up' if now is None or body.get('step', 0) > now else 'down'}"
        if action == "light":
            now = light_level(live) or 0
            return "light_up" if body.get("level", 0) > now else "light_down"
        if action == "port":
            return f"{body.get('port')}_{'on' if body.get('on') else 'off'}"
        return {"plant_stage": "stage_confirm", "plant_apply": "preset_apply", "auto_humidifier": "auto_on"}.get(
            str(action), str(action))

    def record(self, advised: Iterable[Verdict] | Any, *, live: LiveState | Mapping[str, Any], answer_id: str,
               proposals: Iterable[Proposal] = (), card: str | None = None,
               now: float | None = None) -> list[dict[str, Any]]:
        """New entries for the verdicts whose advice an answer or a note carried (compose.Composed.advised). Only
        low or high, only from fresh readings, at most one blocking entry per (stage, metric, status). The card of
        the same check (Proposal.reason "<metric>:<status>") is linked, so tapping or declining it moves the entry."""
        state = collect(live)
        if state.stale:
            return []
        stamp = self._now(now)
        when = stores.iso(stamp)
        cards = list(proposals)
        card = card or _stage_card()
        data = self.load()
        created = []
        for verdict in list(getattr(advised, "verdicts", advised) or []):
            if verdict.status not in verdicts.NEGATIVE:
                continue
            stage = verdict.stage_key or state.stage_key
            key = key_of(stage, verdict.metric, verdict.status)
            if any(key_of(i.get("stage_key"), i.get("metric"), i.get("status")) == key
                   and i.get("state") in BLOCKING for i in data["items"]):
                continue
            moves = self.moves(verdict.metric, verdict.status)
            linked = next((p for p in cards if p.reason == f"{verdict.metric}:{verdict.status}"), None)
            if linked is None:
                actions = {m.get("action") for m in moves}
                linked = next((p for p in cards if p.reason == "request" and p.kind in actions), None)
            item = {
                "id": f"r-{data['next']:04d}", "created": when, "answer_id": answer_id,
                "stage_key": stage, "metric": verdict.metric, "status": verdict.status, "when": verdict.when,
                "baseline": self._baseline(verdict, moves, state),
                "recommendation": {
                    "card": f"{card}#{stage}.{verdict.metric}.{verdict.when or 'all'}" if card and stage else None,
                    "kind": self._kind(linked.body if linked else None, state),
                    "proposal": dict(linked.body) if linked else None,
                    "proposal_id": linked.id if linked else None},
                "state": "open", "applied_at": None, "declined_at": None,
                "consequence_told": False, "told": {"at": None, "deviation": None},
                "worst": {"value": verdict.actual, "at": when},
                "history": [{"t": when, "event": "created"}],
            }
            data["next"] += 1
            data["items"].append(item)
            created.append(copy.deepcopy(item))
        if created:
            self._save(data)
        return created

    def relink(self, cards: Iterable[Proposal], live: LiveState | Mapping[str, Any], now: float | None = None, *,
               advised: Iterable[Verdict] = ()) -> list[dict[str, Any]]:
        """A card from a check handed out again (a second fix ask, or the fix ask after "Nicht jetzt", fix.py): the
        active entry of the same (stage, metric, status) takes the new card over (proposal_id, proposal, kind), so a
        tap on it moves the entry to applied and a "Nicht jetzt" to declined (review 1, M3: card ids are new on every
        answer, and record() leaves an entry that already exists alone). The state itself does not change here."""
        state = collect(live)
        stamp = self._now(now)
        when = stores.iso(stamp)
        stages = {v.metric: v.stage_key for v in advised if getattr(v, "stage_key", None)}
        data = self.load()
        moved = []
        seen: set[str] = set()
        for card in cards:  # the first card of a check, as record() links it
            metric, sep, status = str(card.reason or "").partition(":")
            if not sep or status not in verdicts.NEGATIVE:
                continue
            stage = stages.get(metric) or state.stage_key
            key = key_of(stage, metric, status)
            if key in seen:
                continue
            seen.add(key)
            item = next((i for i in reversed(data["items"]) if i.get("state") in ACTIVE
                         and key_of(i.get("stage_key"), i.get("metric"), i.get("status")) == key), None)
            if item is None:
                continue
            rec = item.setdefault("recommendation", {})
            if rec.get("proposal_id") == card.id:
                continue
            rec.update(proposal_id=card.id, proposal=dict(card.body), kind=self._kind(card.body, state))
            self._history(item, when, "relinked")
            moved.append(copy.deepcopy(item))
        if moved:
            self._save(data)
        return moved

    def _find_card(self, data: dict[str, Any], card_id: str) -> dict[str, Any] | None:
        return next((i for i in data["items"] if (i.get("recommendation") or {}).get("proposal_id") == card_id
                     and i.get("state") in ACTIVE), None)

    def accepted(self, card: Proposal | str, *, now: float | None = None) -> dict[str, Any] | None:
        """A card was tapped (the /api/cmd wrapper saw its id): its entry is applied at once (spec 6.2)."""
        card_id = card.id if isinstance(card, Proposal) else str(card)
        data = self.load()
        item = self._find_card(data, card_id)
        if item is None:
            return None
        when = stores.iso(self._now(now))
        item.update(state="applied", applied_at=when)
        self._history(item, when, "accepted")
        self._save(data)
        return copy.deepcopy(item)

    def declined(self, card: Proposal | str, *, live: LiveState | Mapping[str, Any] | None = None,
                 now: float | None = None) -> dict[str, Any] | None:
        """"Nicht jetzt" on a card: its entry is declined, and a card from a check is not offered again until it got
        worse or the stage changed (spec 6.3). Returns the entry, or None when the card had none."""
        card_id = card.id if isinstance(card, Proposal) else str(card)
        stamp = self._now(now)
        when = stores.iso(stamp)
        state = collect(live) if live is not None else None
        data = self.load()
        item = self._find_card(data, card_id)
        reason = card.reason if isinstance(card, Proposal) else None
        action = card.kind if isinstance(card, Proposal) else None
        stage = state.stage_key if state is not None else data.get("stage")
        if item is not None:
            item.update(state="declined", declined_at=when)
            self._history(item, when, "declined")
            stage = item.get("stage_key")
            reason = reason or f"{item.get('metric')}:{item.get('status')}"
            action = action or ((item.get("recommendation") or {}).get("proposal") or {}).get("action")
        if reason and reason != "request" and action:
            metric, _, status = reason.partition(":")
            dev = None
            if state is not None and status:
                dev = _row_deviation(_row(state, metric, item.get("when") if item else None), status)
            data["declined"][f"{stage}|{reason}|{action}"] = {"at": when, "deviation": dev, "stage": stage,
                                                              "reason": reason, "action": action}
        if item is None and not (reason and reason != "request"):
            return None
        self._save(data)
        return copy.deepcopy(item) if item is not None else None

    def may_offer(self, card: Proposal, stage: Any) -> bool:
        """False for a card from a check that was declined for this stage and has not got worse since (spec 6.3). A
        card the person asked for (reason "request") is always offered."""
        if card.reason == "request":
            return True
        return f"{stage}|{card.reason}|{card.kind}" not in self.load()["declined"]

    def offerable(self, cards: Iterable[Proposal], live: LiveState | Mapping[str, Any]) -> list[Proposal]:
        stage = collect(live).stage_key
        return [c for c in cards if self.may_offer(c, stage)]

    def mark_told(self, keys: Iterable[Any], live: LiveState | Mapping[str, Any] | None = None, *,
                  now: float | None = None) -> None:
        """The consequence was told (compose.Composed.told, or tell()): mark (stage, metric, status) with the
        current distance from the band, the base for "only when worse"."""
        state = collect(live) if live is not None else None
        when = stores.iso(self._now(now))
        data = self.load()
        changed = False
        for raw in keys:
            stage, metric, status = split_key(raw) if isinstance(raw, str) else (raw[0], raw[1], raw[2])
            dev = _row_deviation(_row(state, str(metric)), str(status)) if state is not None else None
            data["told"][key_of(stage, metric, status)] = {"at": when, "deviation": dev}
            for item in data["items"]:
                if key_of(item.get("stage_key"), item.get("metric"), item.get("status")) == key_of(stage, metric,
                                                                                                   status):
                    item["consequence_told"] = True
                    item["told"] = {"at": when, "deviation": dev}
            changed = True
        if changed:
            self._save(data)

    def tell(self, item: Mapping[str, Any], live: LiveState | Mapping[str, Any], lang: str = "de", *,
             resolve: Callable[..., tuple[SourceRef, ...]] | None = None, now: float | None = None) -> Telling:
        """The message after a declined card (POST /api/hank/proposal): "you declined", then the consequence once
        with its source (or "nothing in my card file", with a gap entry, never an invented one), then respect.
        Already told for this stage, metric and direction: only the respect sentence."""
        from . import compose
        from .context import SourceBook

        state = collect(live)
        stage, metric, status = item.get("stage_key"), str(item.get("metric")), str(item.get("status"))
        pieces = [compose.Piece(t("hank.ledger.declined", lang), (), "ledger")]
        gap = None
        told = False
        if not state.stale and not self.is_told(stage, metric, status):  # stale: no judgement, told later
            vs = verdicts.build(state, lang, metrics=[metric], statuses=[status], card=_stage_card(),
                                resolve=resolve or SourceBook())
            verdict = next((v for v in vs.verdicts if v.metric == metric), None)
            if verdict is not None and verdict.consequence is not None:
                pieces.append(compose.Piece(t("hank.ledger.consequence", lang, text=verdict.consequence.text),
                                            verdict.consequence.sources, "ledger"))
            else:
                pieces.append(compose.Piece(t("hank.ledger.none", lang), (), "ledger"))
                gap = {"kind": "consequence_missing", "stage": stage, "metric": metric, "status": status}
            self.mark_told([(stage, metric, status)], state, now=now)
            told = True
        pieces.append(compose.Piece(t("hank.ledger.respect", lang), (), "ledger"))
        numbering = compose.Numbering()
        sentences = [numbering.render(p) for p in pieces]
        return Telling(" ".join(sentences), sentences, list(numbering.order), told, gap)

    # ---------- the minute tick (spec 6.2) ----------

    def _moved(self, item: Mapping[str, Any], live: LiveState) -> bool:
        """A watched device moved the way the advice points (spec 6.2 watch)."""
        base = item.get("baseline") or {}
        for move in self.moves(str(item.get("metric")), str(item.get("status"))):
            action = move.get("action")
            up = move.get("dir") == "up"
            if action in ("dfan", "cfan"):
                before, now = base.get(action), fan_step(live, str(action))
            elif action == "light":
                before, now = base.get("light"), light_level(live)
            elif action == "port":
                port = humidifier_port(live) if move.get("port") == "humidifier" else str(move.get("port"))
                before, now = base.get(f"{port}_on"), port_on(live, port)
                if isinstance(now, bool) and now is bool(move.get("on")) and before is not now:
                    return True
                continue
            else:
                continue
            if isinstance(before, int | float) and isinstance(now, int | float) and (now > before if up
                                                                                      else now < before):
                return True
        return False

    def tick(self, live: LiveState | Mapping[str, Any], now: float | None = None) -> list[Event]:
        """One minute: expiry, stage change, applied / not applied / resolved from the readings, and the "worse"
        check that lifts told and declined marks. Returns what changed."""
        state = collect(live)
        stamp = self._now(now)
        when = stores.iso(stamp)
        data = self.load()
        before = json.dumps(data, sort_keys=True, ensure_ascii=False)
        events: list[Event] = []
        stage = state.stage_key

        if stage is not None and data.get("stage") not in (None, stage):  # stage change (addendum N4)
            for item in data["items"]:
                if item.get("state") in ACTIVE:
                    item["state"] = "expired"
                    self._history(item, when, "expired_stage")
                    events.append(Event("expired", self._item_key(item), copy.deepcopy(item), {"why": "stage"}))
            if data["told"] or data["declined"]:
                events.append(Event("reset", f"{data.get('stage')}->{stage}", None, {"why": "stage"}))
            data["told"], data["declined"] = {}, {}
        if stage is not None:
            data["stage"] = stage

        for item in data["items"]:
            if item.get("state") not in ACTIVE:
                continue
            created = stores.epoch_of(item.get("created")) or stamp
            if item["state"] == "open" and stamp - created >= self.open_seconds:
                item["state"] = "expired"
                self._history(item, when, "expired")
                events.append(Event("expired", self._item_key(item), copy.deepcopy(item), {"why": "time"}))
                continue
            if state.stale:
                continue
            row = _row(state, str(item.get("metric")), item.get("when"))
            if row is None or row.get("actual") is None or row.get("status") not in ("ok", "low", "high"):
                continue
            if row["status"] != item.get("status"):
                item["state"] = "resolved"
                self._history(item, when, "resolved")
                events.append(Event("resolved", self._item_key(item), copy.deepcopy(item)))
                continue
            dev = _row_deviation(row, str(item["status"]))
            worst = item.get("worst") or {}
            worst_dev = deviation(worst.get("value"), row.get("min"), row.get("max"), str(item["status"]))
            if dev is not None and (worst_dev is None or dev > worst_dev):
                item["worst"] = {"value": row["actual"], "at": when}
            if item["state"] != "open":
                continue
            base = (item.get("baseline") or {}).get("value")
            base_dev = deviation(base, row.get("min"), row.get("max"), str(item["status"]))
            better = self._delta("improved_delta", str(item.get("metric")))
            if self._moved(item, state):
                item.update(state="applied", applied_at=when)
                self._history(item, when, "applied")
                events.append(Event("applied", self._item_key(item), copy.deepcopy(item)))
            elif better is not None and dev is not None and base_dev is not None and base_dev - dev >= better:
                item.update(state="applied", applied_at=when)
                self._history(item, when, "improved")
                events.append(Event("applied", self._item_key(item), copy.deepcopy(item), {"why": "improved"}))
            elif stamp - created >= self.grace_seconds:
                item["state"] = "not_applied"
                self._history(item, when, "not_applied")
                events.append(Event("not_applied", self._item_key(item), copy.deepcopy(item)))

        events += self._worse(data, state, stamp)
        if json.dumps(data, sort_keys=True, ensure_ascii=False) != before:
            self._save(data)
        return events

    @staticmethod
    def _item_key(item: Mapping[str, Any]) -> str:
        return key_of(item.get("stage_key"), item.get("metric"), item.get("status"))

    def _worse(self, data: dict[str, Any], state: LiveState, stamp: float) -> list[Event]:
        """Lift told and declined marks that got worse (spec 6.3): more than worse_delta further from the band than
        at the mark, or an alarm rule newly active for that metric and direction, and the cooldown over."""
        active = {str(a.get("name")): a for a in state.alerts_view if isinstance(a, dict) and a.get("active")}
        fresh = [a for name, a in active.items() if name not in set(data.get("alerts") or [])]
        data["alerts"] = sorted(active)
        events: list[Event] = []
        seen: set[str] = set()
        for table in ("told", "declined"):
            for mark_key, mark in list(data[table].items()):
                if table == "told":
                    stage, metric, status = split_key(mark_key)
                else:
                    stage = str(mark.get("stage"))
                    metric, _, status = str(mark.get("reason") or "").partition(":")
                if not metric or not status:
                    continue
                if stamp - (stores.epoch_of(mark.get("at")) or stamp) < self.cooldown_seconds:
                    continue
                why = None
                if not state.stale:
                    dev = _row_deviation(_row(state, metric), status)
                    delta = self._delta("worse_delta", metric)
                    old = mark.get("deviation")
                    if dev is not None and delta is not None and isinstance(old, int | float) and dev - old > delta:
                        why = "delta"
                if why is None and any(_alert_matches(a, metric, status) for a in fresh):
                    why = "alert"
                if why is None:
                    continue
                del data[table][mark_key]
                if table == "told":
                    for item in data["items"]:
                        if self._item_key(item) == mark_key:
                            item["consequence_told"] = False
                            item["told"] = {"at": None, "deviation": None}
                event_key = key_of(stage, metric, status)
                if event_key not in seen:
                    seen.add(event_key)
                    events.append(Event("worse", event_key, None, {"why": why, "metric": metric, "status": status,
                                                                   "stage": stage}))
        return events


def _row(state: LiveState | None, metric: str, when: Any = None) -> dict[str, Any] | None:
    """The plant.evaluate() row of a metric now; with `when` the row of that time of day (day, night or always)."""
    if state is None:
        return None
    rows = [r for r in state.checks if r.get("metric") == metric]
    if when is not None:
        exact = [r for r in rows if r.get("when") == when]
        rows = exact or rows
    return rows[0] if rows else None


def _alert_matches(alert: Mapping[str, Any], metric: str, status: str) -> bool:
    """An alarm rule of alerts.json about this metric (or its fallback) and direction (">" high, "<" or a cap low)."""
    if metric not in (alert.get("metric"), alert.get("fallback")):
        return False
    op = alert.get("op")
    return (op == ">" and status == "high") or (op in ("<", AT_MOST) and status == "low")
