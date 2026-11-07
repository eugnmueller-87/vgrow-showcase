"""Hank's local stores (spec 8, rule 0.3.7; addendum N4, N7, N8). Everything lives under STATE_DIR/hank/ and never
leaves the computer.

Two file kinds, two rules:
- JSON documents (chat.json, aliases-learned.json, ledger.json, nudge-state.json) are written in one step with
  config.atomic_write. A document that does not parse falls back to its empty default and is logged (spec 12.1
  item 12); it is never "repaired" in place, the next write replaces it.
- JSONL logs (gaps.jsonl, feedback.jsonl, experience.jsonl, actions.jsonl, turns.jsonl, eval-candidates.jsonl) are
  appended. Readers skip a line that does not parse, so a last line cut by a crash costs that line only, and the next
  append starts on a fresh line. actions.jsonl and turns.jsonl keep their last 5000 lines (spec 8.5): when a log
  grows past that by a tenth, it is rewritten in one step with its newest lines.

config.STATE_DIR is read at call time, never at import (spec 10.1 rule 3: the tests move it per test).

Who writes what (spec 8): the gap list (router and ledger, through the caller), learned spellings (only an explicit
"yes" to "Meinst du X?", addendum N7, never a thumbs up), feedback (POST /feedback), own experience (CLI), the
journal (the /api/cmd wrapper, and the ticker for the automation's own switches, addendum N4), the chat ring, the
measurement log (no question text in it, spec 8.5) and the eval candidates (feedback_to_eval, never the eval set
itself, spec 8.3).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from vgrow import config

from . import text

_LOGGER = logging.getLogger(__name__)

DIR_NAME = "hank"
KEEP_LINES = 5000  # spec 8, 8.5: actions.jsonl and turns.jsonl keep their last 5000 lines
CHAT_KEEP = 50  # spec 8: the last 50 messages, hints included
DICE_MERGE = 0.8  # spec 8.1: similar questions (Dice >= 0.8) merge into one gap
GAP_KINDS = ("no_card", "consequence_missing", "consequence_source_missing", "twin_missing",
             "beginner_mistake_missing", "low_conf")
GAP_STATUSES = ("open", "answered")
ORIGINS = ("human", "automation", "hank_accepted", "hank_declined")  # spec 8: who switched
VOTES = ("up", "down")
# Keys a measurement line never keeps (spec 8.5: turns.jsonl holds no question text).
TURN_DROP = ("q", "question", "query", "prompt", "messages", "message", "content", "answer_text")

Clock = Callable[[], float]


def hank_dir() -> Path:
    """STATE_DIR/hank, read at call time."""
    return config.STATE_DIR / DIR_NAME


def iso(epoch: float) -> str:
    """Local time to the second, the form automation.py and the ledger example (spec 6.1) use."""
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def epoch_of(value: Any) -> float | None:
    """Epoch seconds from an epoch number or an ISO text; None for anything else."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


# ---------- the two file kinds ----------

def read_doc(path: Path, default: Any) -> Any:
    """A JSON document, or a fresh copy of `default` when it is missing or does not parse (logged)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return copy.deepcopy(default)
    except OSError as err:
        _LOGGER.error("%s unreadable (%s): starting empty", path.name, err)
        return copy.deepcopy(default)
    try:
        data = json.loads(raw)
    except ValueError as err:
        _LOGGER.error("%s does not parse (%s): starting empty, the next write replaces it", path.name, err)
        return copy.deepcopy(default)
    if not isinstance(data, type(default)):
        _LOGGER.error("%s has the wrong shape: starting empty", path.name)
        return copy.deepcopy(default)
    return data


def write_doc(path: Path, data: Any) -> None:
    config.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def read_lines(path: Path) -> list[dict[str, Any]]:
    """Every line of a JSONL log that parses as an object; a cut or broken line is skipped."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a line a crash cut off
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _ends_open(path: Path) -> bool:
    """True when the file's last byte is not a newline (a crash cut the last line)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            if f.tell() == 0:
                return False
            f.seek(-1, 2)
            return f.read(1) != b"\n"
    except OSError:
        return False


def append_line(path: Path, entry: Mapping[str, Any], *, keep: int | None = None) -> None:
    """Append one JSON line; with keep, rewrite the log with its newest `keep` lines once it is a tenth longer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(entry), ensure_ascii=False)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        if _ends_open(path):
            f.write("\n")
        f.write(line + "\n")
    if keep is not None:
        _rotate(path, keep)


def _rotate(path: Path, keep: int) -> None:
    try:
        lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return
    lines = [ln for ln in lines if ln.strip()]
    if len(lines) <= keep + keep // 10:
        return
    config.atomic_write(path, "\n".join(lines[-keep:]) + "\n")


class JsonDoc:
    """One JSON document under STATE_DIR/hank (the ledger's and the traffic light's store)."""

    def __init__(self, name: str, default: Mapping[str, Any] | None = None) -> None:
        self.name = name
        self.default = dict(default or {})

    @property
    def path(self) -> Path:
        return hank_dir() / self.name

    def load(self) -> dict[str, Any]:
        return read_doc(self.path, self.default)

    def save(self, data: Mapping[str, Any]) -> None:
        write_doc(self.path, dict(data))


# ---------- the gap list (spec 8.1) ----------

def _key(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def question_words(question: str, lang: str = "de") -> list[str]:
    """The word set a gap key is the hash of: stemmed search words without stop words, sorted, no duplicates. A
    question of stop words only keeps its plain folded words."""
    words = set(text.tokens(question, lang=lang)) or set(text.tokens(question, stem=False, stop=False))
    return sorted(words)


def dice(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    if not left and not right:
        return 1.0
    return 2 * len(left & right) / (len(left) + len(right))


class GapStore:
    """gaps.jsonl. Each line is the full record of one gap after a change; the newest line per key wins. Records:
    key, kind, question (the first one asked), lang, count, first, last, term (the corrected name), best (the best
    hit), status (open | answered), words (the question's word set), detail (for gaps without a question)."""

    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock

    @property
    def path(self) -> Path:
        return hank_dir() / "gaps.jsonl"

    def all(self) -> list[dict[str, Any]]:
        """Every gap, newest record per key, in the order first seen."""
        records: dict[str, dict[str, Any]] = {}
        for line in read_lines(self.path):
            key = line.get("key")
            if isinstance(key, str) and key:
                records[key] = line
        return list(records.values())

    def get(self, key: str) -> dict[str, Any] | None:
        return next((g for g in self.all() if g.get("key") == key), None)

    def open(self) -> list[dict[str, Any]]:
        return [g for g in self.all() if g.get("status", "open") == "open"]

    def top(self, n: int = 20) -> list[dict[str, Any]]:
        """The open gaps asked most, the most recent first among equals (CLI `gaps --top 20`)."""
        recent_first = sorted(self.open(), key=lambda g: str(g.get("last") or ""), reverse=True)
        return sorted(recent_first, key=lambda g: -int(g.get("count") or 0))[:n]

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        append_line(self.path, record)
        self._compact()
        return record

    def _compact(self) -> None:
        """Rewrite the log with one line per gap once it holds far more lines than gaps."""
        lines = read_lines(self.path)
        if len(lines) < 200:
            return
        records = self.all()
        if len(lines) > 4 * len(records):
            config.atomic_write(self.path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))

    def add_question(self, question: str, lang: str = "de", *, kind: str = "no_card", term: str | None = None,
                     best: str | None = None) -> dict[str, Any]:
        """A question Hank could not answer from the card file. A similar open question of the same kind (Dice of
        the word sets >= 0.8) counts up instead of making a new gap."""
        if kind not in GAP_KINDS:
            raise ValueError(f"unknown gap kind {kind!r}")
        now = iso(self.clock())
        words = question_words(question, lang)
        same = [g for g in self.open() if g.get("kind") == kind and isinstance(g.get("words"), list)]
        match = max(same, key=lambda g: dice(words, g["words"]), default=None)
        if match is not None and dice(words, match["words"]) >= DICE_MERGE:
            record = {**match, "count": int(match.get("count") or 0) + 1, "last": now}
            if term and not record.get("term"):
                record["term"] = term
            if best and not record.get("best"):
                record["best"] = best
            return self._write(record)
        record = {"key": _key(kind, " ".join(words)), "kind": kind, "question": question, "lang": lang, "count": 1,
                  "first": now, "last": now, "term": term, "best": best, "status": "open", "words": words,
                  "detail": None}
        return self._write(record)

    def note(self, kind: str, **detail: Any) -> dict[str, Any]:
        """A gap without a question (consequence_missing, twin_missing, beginner_mistake_missing, ...), one gap per
        kind and detail; noting it again counts up."""
        if kind not in GAP_KINDS:
            raise ValueError(f"unknown gap kind {kind!r}")
        now = iso(self.clock())
        clean = {k: v for k, v in sorted(detail.items()) if v is not None}
        key = _key(kind, json.dumps(clean, sort_keys=True, ensure_ascii=False))
        old = self.get(key)
        if old is not None:
            return self._write({**old, "count": int(old.get("count") or 0) + 1, "last": now,
                                "status": old.get("status", "open")})
        return self._write({"key": key, "kind": kind, "question": None, "lang": None, "count": 1, "first": now,
                            "last": now, "term": None, "best": None, "status": "open", "words": None,
                            "detail": clean})

    def add(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        """A gap entry as context.Context.gaps or the router hands it on: with a "question" a question gap, else a
        gap by kind and detail ({"kind": "consequence_missing", "stage": ..., "metric": ..., "status": ...})."""
        data = dict(entry)
        kind = str(data.pop("kind", "no_card"))
        question = data.pop("question", None)
        if question:
            return self.add_question(str(question), str(data.get("lang") or "de"), kind=kind, term=data.get("term"),
                                     best=data.get("best"))
        return self.note(kind, **data)

    def add_many(self, entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for entry in entries:
            try:
                out.append(self.add(entry))
            except ValueError as err:  # an unknown kind from a caller: logged, never a crash of the answer
                _LOGGER.warning("gap entry skipped: %s", err)
        return out

    def export(self) -> dict[str, Any]:
        """The open gaps as one JSON object for the research (CLI `gaps export`, GET /api/hank/gaps)."""
        return {"version": 1, "exported": iso(self.clock()),
                "gaps": [{k: v for k, v in g.items() if k != "words"} for g in self.open()]}

    def resolve(self, answered: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
        """Route every open question gap again (CLI `gaps resolve`, after a new card); answered(gap) is the caller's
        router and says whether it is now a hit. Those become "answered". Gaps without a question stay."""
        done = []
        now = iso(self.clock())
        for gap in self.open():
            if not gap.get("question"):
                continue
            try:
                hit = bool(answered(gap))
            except Exception:  # one bad question must not stop the others
                _LOGGER.exception("gap %s could not be routed again", gap.get("key"))
                continue
            if hit:
                done.append(self._write({**gap, "status": "answered", "answered": now}))
        return done


# ---------- learned spellings (spec 8.2, addendum N7) ----------

def _compact(word: str) -> str:
    from .typo import compact  # the typo matcher's own key, so a learned form is found where it looks

    return compact(word)


class AliasStore:
    """aliases-learned.json, in the form typo.correct() reads: {"learned": {wrong: {term, entity, count, at}},
    "rejected": {wrong: [term, ...]}}. Learned only from an explicit "yes" to "Meinst du X?" (addendum N7); a "no"
    rejects the pairing so it is never offered again (spec 3.4). A spelling only ever matches names, it never makes a
    fact (spec 8.2)."""

    DEFAULT = {"version": 1, "learned": {}, "rejected": {}}

    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock

    @property
    def path(self) -> Path:
        return hank_dir() / "aliases-learned.json"

    def load(self) -> dict[str, Any]:
        data = read_doc(self.path, self.DEFAULT)
        data.setdefault("learned", {})
        data.setdefault("rejected", {})
        return data

    def tables(self) -> dict[str, Any]:
        """What typo.correct(token, vocab, aliases) takes."""
        data = self.load()
        return {"learned": data["learned"], "rejected": data["rejected"]}

    def confirm(self, token: str, term: str, entity: str | None = None) -> dict[str, Any]:
        """The person said "yes" to "Meinst du {term}?" for `token`: learn it, and lift an earlier rejection."""
        data = self.load()
        wrong = _compact(token)
        if not wrong or not term.strip():
            raise ValueError("a learned spelling needs a word and a term")
        old = data["learned"].get(wrong) if isinstance(data["learned"].get(wrong), dict) else {}
        data["learned"][wrong] = {"term": term.strip(), "entity": entity or old.get("entity"),
                                  "count": int(old.get("count") or 0) + 1, "at": iso(self.clock())}
        rejected = [t for t in data["rejected"].get(wrong, []) if _compact(t) != _compact(term)]
        if rejected:
            data["rejected"][wrong] = rejected
        else:
            data["rejected"].pop(wrong, None)
        write_doc(self.path, data)
        return data["learned"][wrong]

    def reject(self, token: str, term: str) -> None:
        """The person said "no": never offer `term` for `token` again, and forget it if it had been learned."""
        data = self.load()
        wrong = _compact(token)
        terms = list(data["rejected"].get(wrong, []))
        if all(_compact(t) != _compact(term) for t in terms):
            terms.append(term.strip())
        data["rejected"][wrong] = terms
        learned = data["learned"].get(wrong)
        if isinstance(learned, dict) and _compact(str(learned.get("term", ""))) == _compact(term):
            data["learned"].pop(wrong, None)
        write_doc(self.path, data)

    def learned(self, token: str) -> str | None:
        found = self.load()["learned"].get(_compact(token))
        return found.get("term") if isinstance(found, dict) else None

    def is_rejected(self, token: str, term: str) -> bool:
        return any(_compact(t) == _compact(term) for t in self.load()["rejected"].get(_compact(token), []))


# ---------- feedback and eval candidates (spec 8.3) ----------

class FeedbackStore:
    """feedback.jsonl: answer id, route, vote, question, top hit ids, answer text. A vote never teaches a spelling
    (addendum N7) and never goes into the eval set by itself (spec 8.3)."""

    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock

    @property
    def path(self) -> Path:
        return hank_dir() / "feedback.jsonl"

    @property
    def candidates_path(self) -> Path:
        return hank_dir() / "eval-candidates.jsonl"

    def add(self, answer_id: str, vote: str, *, route: str | None = None, question: str | None = None,
            hits: Iterable[str] = (), answer: str | None = None, lang: str | None = None) -> dict[str, Any]:
        if vote not in VOTES:
            raise ValueError(f"vote must be one of {VOTES}, not {vote!r}")
        entry = {"time": iso(self.clock()), "answer_id": answer_id, "route": route, "vote": vote, "lang": lang,
                 "question": question, "hits": list(hits), "answer": answer}
        append_line(self.path, entry)
        return entry

    def all(self) -> list[dict[str, Any]]:
        return read_lines(self.path)

    def to_eval(self) -> list[dict[str, Any]]:
        """CLI `feedback-to-eval`: one candidate per answer with feedback, in the shape of tests/hank_eval/cases.json,
        appended to STATE_DIR/hank/eval-candidates.jsonl. must_mention and must_not stay empty: a person fills them
        and copies a case into the eval set by hand. An answer already written as a candidate is not written again;
        the newest vote of an answer counts."""
        done = {line.get("candidate", {}).get("answer_id") for line in read_lines(self.candidates_path)}
        newest: dict[str, dict[str, Any]] = {}
        for entry in self.all():
            if entry.get("answer_id") and entry.get("question"):
                newest[str(entry["answer_id"])] = entry
        out = []
        for answer_id, entry in newest.items():
            if answer_id in done:
                continue
            case = {
                "id": f"fb-{answer_id}", "lang": entry.get("lang") or text.detect_lang(entry["question"]),
                "question": entry["question"], "expected_route": entry.get("route"), "needs_live_state": None,
                "source_refs": [], "must_mention": [], "must_not": [],
                "note": f"candidate from feedback (thumbs {entry['vote']}) of {entry.get('time')}; check the route, "
                        "fill must_mention and must_not by hand before it goes into the eval set",
                "typo_fix": None,
                "candidate": {"answer_id": answer_id, "vote": entry["vote"], "route": entry.get("route"),
                              "hits": entry.get("hits") or [], "answer": entry.get("answer")},
            }
            append_line(self.candidates_path, case)
            out.append(case)
        return out


# ---------- own experience (spec 8.4) ----------

class ExperienceStore:
    """experience.jsonl: the grower's own notes, source "own", kept apart from sourced values (spec 8.4)."""

    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock

    @property
    def path(self) -> Path:
        return hank_dir() / "experience.jsonl"

    def add(self, note: str, *, stage: str | None = None, run: Any = None, text_en: str | None = None,
            **extra: Any) -> dict[str, Any]:
        """extra: where a lesson came from (vgrow/lessons.py: the plant's record and its variety)."""
        note = note.strip()
        if not note:
            raise ValueError("an experience note needs a text")
        now = self.clock()
        entry = {"id": "e-" + _key(note, repr(now))[:8], "text": note, "text_en": text_en, "stage": stage,
                 "run": run, "time": iso(now), "source": "own", **extra}
        append_line(self.path, entry)
        return entry

    def items(self) -> list[dict[str, Any]]:
        return [e for e in read_lines(self.path) if e.get("id") and e.get("text")]

    def change(self, ident: str, note: str) -> dict[str, Any] | None:
        """The owner's new words for a note, in place: Hank's index sees the file change. None for an unknown id."""
        entries = read_lines(self.path)
        found = next((e for e in entries if e.get("id") == ident), None)
        if found is None:
            return None
        found.update(text=note.strip(), text_en=None)
        self._rewrite(entries)
        return found

    def remove(self, ident: str) -> bool:
        """The note is gone from the file, not hidden. False for an unknown id."""
        entries = read_lines(self.path)
        kept = [e for e in entries if e.get("id") != ident]
        if len(kept) == len(entries):
            return False
        self._rewrite(kept)
        return True

    def _rewrite(self, entries: list[dict[str, Any]]) -> None:
        config.atomic_write(self.path, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


# ---------- journal (spec 8; addendum N4) ----------

class Journal:
    """actions.jsonl: every action with its origin (human, automation, hank_accepted, hank_declined), the last 5000.
    The /api/cmd wrapper writes the human and Hank lines (WP9); import_automation() takes the automation's own switches
    from automation-log.jsonl, because the automation switches inside the server and never through /api/cmd."""

    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock

    @property
    def path(self) -> Path:
        return hank_dir() / "actions.jsonl"

    def add(self, action: str, origin: str, *, body: Mapping[str, Any] | None = None, summary: str | None = None,
            proposal: str | None = None, ok: bool | None = None, at: float | None = None) -> dict[str, Any]:
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, not {origin!r}")
        entry = {"time": iso(self.clock() if at is None else at), "origin": origin, "action": action,
                 "body": dict(body) if body is not None else None, "summary": summary, "proposal": proposal, "ok": ok}
        append_line(self.path, entry, keep=KEEP_LINES)
        return entry

    def all(self) -> list[dict[str, Any]]:
        return read_lines(self.path)

    def recent(self, n: int = 3, *, exclude: Iterable[str] = ()) -> list[dict[str, Any]]:
        """The newest n lines, oldest first (the order livestate reads); exclude: origins to leave out."""
        skip = set(exclude)
        return [e for e in self.all() if e.get("origin") not in skip][-n:] if n > 0 else []

    def last(self, action: str | Iterable[str], origin: str | None = None) -> dict[str, Any] | None:
        wanted = {action} if isinstance(action, str) else set(action)
        for entry in reversed(self.all()):
            if entry.get("action") in wanted and (origin is None or entry.get("origin") == origin):
                return entry
        return None

    def import_automation(self, log_path: Path | None = None) -> int:
        """Take the new lines of automation-log.jsonl into the journal with origin "automation" (addendum N4). "New"
        means newer than the newest automation line the journal already holds; the line keeps its own time. Returns
        how many lines were taken."""
        path = log_path or config.STATE_DIR / "automation-log.jsonl"
        newest = None
        seen: set[tuple[Any, Any]] = set()
        for entry in self.all():
            if entry.get("origin") == "automation":
                when = epoch_of(entry.get("time"))
                if when is not None and (newest is None or when > newest):
                    newest = when
                seen.add((entry.get("time"), entry.get("action")))
        taken = 0
        for line in read_lines(path):
            when = epoch_of(line.get("time"))
            action = line.get("action")
            if when is None or not isinstance(action, str):
                continue
            if newest is not None and when <= newest or (iso(when), action) in seen:
                continue
            rest = {k: v for k, v in line.items() if k not in ("time", "action")}
            self.add(action, "automation", body=rest or None, at=when)
            seen.add((iso(when), action))
            taken += 1
        return taken


# ---------- chat ring (spec 8; addendum N6: notes are chat messages) ----------

class ChatRing:
    """chat.json: the last 50 messages, Hank's notes included. Each message gets an id (counting up) and a time.
    unread() counts Hank's notes (kind "nudge") after the last acknowledged id (POST /api/hank/ack)."""

    DEFAULT = {"version": 1, "next": 1, "ack": 0, "messages": []}

    def __init__(self, clock: Clock = time.time, keep: int = CHAT_KEEP) -> None:
        self.clock = clock
        self.keep = keep

    @property
    def path(self) -> Path:
        return hank_dir() / "chat.json"

    def load(self) -> dict[str, Any]:
        data = read_doc(self.path, self.DEFAULT)
        if not isinstance(data.get("messages"), list):
            data["messages"] = []
        data["next"] = int(data.get("next") or 1)
        data["ack"] = int(data.get("ack") or 0)
        return data

    def add(self, message: Mapping[str, Any], *, collapse: bool = False, replace: int | None = None) -> dict[str, Any]:
        """A new message at the end. collapse (Hank's notes, owner 23.10.2026: no flood): every earlier note leaves
        the ring, read or unread (review 1, M2: read notes stacked up again when the owner read each one), and so
        does the note `replace` whatever its kind; their ids go into the new note's "replaces", so the page removes
        them as well."""
        data = self.load()
        entry = {**dict(message), "id": data["next"], "at": iso(self.clock())}
        if collapse:
            gone = [m for m in data["messages"] if m.get("kind") == "nudge"
                    or (replace is not None and m.get("id") == replace)]
            if gone:
                ids = {m.get("id") for m in gone}
                data["messages"] = [m for m in data["messages"] if m.get("id") not in ids]
                entry["replaces"] = sorted({int(i) for i in ids if i is not None}
                                           | {int(i) for m in gone for i in m.get("replaces") or []})
        data["next"] += 1
        data["messages"] = [*data["messages"], entry][-self.keep:]
        write_doc(self.path, data)
        return entry

    def live_note(self) -> dict[str, Any] | None:
        """The newest of Hank's notes (kind "nudge"), the one a note update changes in place, or None."""
        notes = [m for m in self.load()["messages"] if m.get("kind") == "nudge"]
        return notes[-1] if notes else None

    def update(self, message_id: int, fields: Mapping[str, Any]) -> dict[str, Any] | None:
        """Change a message in place (same id, read or unread as before); its revision "rev" counts up so the page
        draws it again. None when the id is no longer in the ring."""
        data = self.load()
        for index, message in enumerate(data["messages"]):
            if message.get("id") == message_id:
                changed = {**message, **{k: v for k, v in fields.items() if k not in ("id", "at", "replaces")},
                           "rev": int(message.get("rev") or 0) + 1, "updated": iso(self.clock())}
                data["messages"][index] = changed
                write_doc(self.path, data)
                return changed
        return None

    def messages(self) -> list[dict[str, Any]]:
        return self.load()["messages"]

    def unread(self) -> int:
        data = self.load()
        return sum(1 for m in data["messages"] if m.get("kind") == "nudge" and int(m.get("id") or 0) > data["ack"])

    def ack(self, upto: int) -> int:
        data = self.load()
        data["ack"] = max(data["ack"], min(int(upto), data["next"] - 1))
        write_doc(self.path, data)
        return data["ack"]


# ---------- measurement log (spec 8, 8.5) ----------

class TurnLog:
    """turns.jsonl: route, times, tokens, validator report, cold or warm, model; never the question (spec 8.5)."""

    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock

    @property
    def path(self) -> Path:
        return hank_dir() / "turns.jsonl"

    def add(self, record: Mapping[str, Any]) -> dict[str, Any]:
        entry = {"time": iso(self.clock()), **{k: v for k, v in record.items() if k not in TURN_DROP}}
        append_line(self.path, entry, keep=KEEP_LINES)
        return entry

    def all(self) -> list[dict[str, Any]]:
        return read_lines(self.path)


# ---------- all of them ----------

class Stores:
    """Every store of one Hank, sharing one clock. ledger and nudge are the documents of ledger.py and nudges.py."""

    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock
        self.gaps = GapStore(clock)
        self.aliases = AliasStore(clock)
        self.feedback = FeedbackStore(clock)
        self.experience = ExperienceStore(clock)
        self.journal = Journal(clock)
        self.chat = ChatRing(clock)
        self.turns = TurnLog(clock)
        self.ledger = JsonDoc("ledger.json", {"version": 1, "items": []})
        self.nudge = JsonDoc("nudge-state.json", {"version": 1})

    @property
    def dir(self) -> Path:
        return hank_dir()


def safe(call: Callable[[], Any], what: str) -> Any:
    """Run a store call; a failure is logged and gives None (spec 10.1 rule 7: Hank never stops the cockpit)."""
    try:
        return call()
    except Exception:
        _LOGGER.exception("hank store: %s failed", what)
        return None
