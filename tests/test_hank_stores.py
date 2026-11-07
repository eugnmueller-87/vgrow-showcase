"""Hank WP7: the stores under STATE_DIR/hank (spec 8, rule 0.3.7; addendum N4, N7, N8): gap list, learned
spellings, feedback and eval candidates, own experience, journal, chat ring, measurement log."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from vgrow import config
from vgrow.hank import stores, typo

REPO = Path(__file__).resolve().parent.parent
VOCAB = json.loads((REPO / "kartei" / "_hank" / "vocab.json").read_text(encoding="utf-8"))["entries"]
T0 = datetime(2026, 10, 22, 14, 48).timestamp()


class Clock:
    def __init__(self, start: float = T0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_state_lives_under_state_dir_hank_read_at_call_time(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first = stores.hank_dir()
    assert first == config.STATE_DIR / "hank"
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "elsewhere")
    assert stores.hank_dir() == tmp_path / "elsewhere" / "hank" != first
    stores.ChatRing().add({"role": "user", "text": "Hallo"})
    assert (tmp_path / "elsewhere" / "hank" / "chat.json").exists()


def test_stores_atomic_and_tolerate_truncated_line(caplog) -> None:  # type: ignore[no-untyped-def]
    chat = stores.ChatRing(Clock())
    chat.add({"role": "user", "text": "Wie feucht ist es?"})
    folder = stores.hank_dir()
    assert [p.name for p in folder.iterdir()] == ["chat.json"]  # no temp file left behind
    raw = (folder / "chat.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    assert json.loads(raw)["messages"][0]["text"] == "Wie feucht ist es?"

    (folder / "chat.json").write_text('{"version": 1, "messages": [', encoding="utf-8")  # a broken document
    assert chat.messages() == []  # the empty default, logged
    assert "does not parse" in caplog.text
    chat.add({"role": "user", "text": "Neu"})
    assert [m["text"] for m in chat.messages()] == ["Neu"]  # the next write replaces it

    feedback = stores.FeedbackStore(Clock())
    feedback.add("a-1", "up", question="Erste Frage")
    with feedback.path.open("a", encoding="utf-8") as f:
        f.write('{"answer_id": "a-2", "vote": "do')  # a crash cut the last line
    assert [e["answer_id"] for e in feedback.all()] == ["a-1"]
    feedback.add("a-3", "down", question="Dritte Frage")  # starts on a fresh line
    assert [e["answer_id"] for e in feedback.all()] == ["a-1", "a-3"]
    assert stores.read_lines(folder / "missing.jsonl") == []


def test_journal_and_turns_keep_the_last_lines(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(stores, "KEEP_LINES", 10)
    journal = stores.Journal(Clock())
    for i in range(25):
        journal.add("dfan", "human", body={"action": "dfan", "step": i % 10})
    lines = journal.all()
    assert 10 <= len(lines) <= 11 and lines[-1]["body"]["step"] == 4
    with pytest.raises(ValueError):
        journal.add("dfan", "somebody")
    turns = stores.TurnLog(Clock())
    for i in range(12):
        turns.add({"route": "grow", "ms": {"route": i}})
    assert len(turns.all()) <= 11 and turns.all()[-1]["ms"]["route"] == 11


def test_turn_log_never_keeps_the_question() -> None:
    turns = stores.TurnLog(Clock())
    entry = turns.add({"route": "grow", "q": "Wie warm ist es?", "question": "Wie warm ist es?", "prompt": "...",
                       "messages": [{"content": "x"}], "eval_count": 80, "model": "gemma4:12b", "cold": False})
    assert set(entry) == {"time", "route", "eval_count", "model", "cold"}
    assert "Wie warm" not in turns.path.read_text(encoding="utf-8")


def test_gap_dedupe_merges_similar() -> None:
    clock = Clock()
    gaps = stores.GapStore(clock)
    first = gaps.add_question("Wie gieße ich Tomaten richtig?", "de", term="Tomate")
    clock.now += 60
    same = gaps.add_question("Wie giesse ich Tomaten richtig", "de")  # other spelling, same word set
    assert same["key"] == first["key"] and same["count"] == 2 and same["question"] == "Wie gieße ich Tomaten richtig?"
    words_a = stores.question_words("Wie gieße ich die Tomaten richtig im Topf?")
    words_b = stores.question_words("Wie gieße ich Tomaten richtig?")
    assert stores.dice(words_a, words_b) >= 0.8
    near = gaps.add_question("Wie gieße ich die Tomaten richtig im Topf?", "de")
    assert near["key"] == first["key"] and near["count"] == 3  # Dice >= 0.8: merged
    other = gaps.add_question("Welche Erde brauchen Gurken?", "de", term="Gurke")
    assert other["key"] != first["key"]
    assert [g["count"] for g in gaps.top(5)] == [3, 1]

    note = gaps.note("twin_missing", passage="stages/autoflower#notes.2")
    again = gaps.note("twin_missing", passage="stages/autoflower#notes.2")
    assert note["key"] == again["key"] and again["count"] == 2
    gaps.add_many([{"kind": "beginner_mistake_missing", "topic": "watering"}, {"kind": "no_such_kind"}])
    assert len(gaps.all()) == 4  # the unknown kind is logged and skipped

    done = gaps.resolve(lambda gap: "Gurken" in gap["question"])
    assert [g["status"] for g in done] == ["answered"]
    assert {g["question"] for g in gaps.open() if g["question"]} == {"Wie gieße ich Tomaten richtig?"}
    exported = gaps.export()
    assert exported["version"] == 1 and all("words" not in g for g in exported["gaps"])
    with pytest.raises(ValueError):
        gaps.add_question("x", kind="mystery")


def test_gap_log_compacts_to_one_line_per_gap() -> None:
    gaps = stores.GapStore(Clock())
    for _ in range(250):
        gaps.add_question("Wie gieße ich Tomaten richtig?", "de")
    (gap,) = gaps.all()
    assert gap["count"] == 250
    assert len(gaps.path.read_text(encoding="utf-8").splitlines()) < 200


def test_alias_confirmation_paths() -> None:
    aliases = stores.AliasStore(Clock())
    assert typo.correct("Paradeiser", VOCAB, aliases.tables()) is None  # unknown word, nothing learned yet
    learned = aliases.confirm("Paradeiser", "Tomate", "tomato")  # an explicit "yes" to "Meinst du Tomate?"
    assert learned["term"] == "Tomate" and learned["count"] == 1
    fix = typo.correct("Paradeiser", VOCAB, aliases.tables())
    assert fix is not None and fix.entity == "tomato" and fix.mode == "auto"
    assert aliases.learned("paradeiser") == "Tomate"

    auto = typo.correct("Tomahte", VOCAB, aliases.tables())
    assert auto is not None and auto.term == "Tomate"
    aliases.reject("Tomahte", "Tomate")  # "no": never offered again (spec 3.4)
    assert aliases.is_rejected("Tomahte", "Tomate")
    assert typo.correct("Tomahte", VOCAB, aliases.tables()) is None
    aliases.confirm("Tomahte", "Tomate")  # a later "yes" lifts the rejection
    assert not aliases.is_rejected("Tomahte", "Tomate")
    aliases.reject("Paradeiser", "Tomate")  # "no" to a learned one forgets it
    assert aliases.learned("Paradeiser") is None

    # a thumbs up never teaches a spelling (addendum N7)
    before = aliases.path.read_bytes()
    stores.FeedbackStore(Clock()).add("a-9", "up", route="grow", question="Wie viel Licht für Tomahten?")
    assert aliases.path.read_bytes() == before
    with pytest.raises(ValueError):
        aliases.confirm("", "Tomate")


def test_feedback_to_eval_writes_candidates_only() -> None:
    cases = REPO / "tests" / "hank_eval" / "cases.json"
    before = hashlib.sha256(cases.read_bytes()).hexdigest()
    feedback = stores.FeedbackStore(Clock())
    feedback.add("a-1", "down", route="offtopic", question="Wie dünge ich Chili?", hits=[], answer="Da bin ich ...",
                 lang="de")
    feedback.add("a-2", "up", route="grow", question="Is the humidity okay right now?",
                 hits=["stages/autoflower#early_flower.probe_humidity.all"], answer="Humidity ...", lang="en")
    feedback.add("a-1", "up", route="offtopic", question="Wie dünge ich Chili?", lang="de")  # changed its mind
    with pytest.raises(ValueError):
        feedback.add("a-3", "sideways")
    written = feedback.to_eval()
    assert [c["id"] for c in written] == ["fb-a-1", "fb-a-2"]
    first = written[0]
    assert set(first) >= {"id", "lang", "question", "expected_route", "needs_live_state", "source_refs",
                          "must_mention", "must_not", "note", "typo_fix"}  # the shape of cases.json
    assert first["must_mention"] == [] and first["must_not"] == [] and first["candidate"]["vote"] == "up"
    assert written[1]["lang"] == "en" and written[1]["candidate"]["hits"] == [
        "stages/autoflower#early_flower.probe_humidity.all"]
    assert feedback.to_eval() == []  # not written twice
    assert [c["id"] for c in stores.read_lines(feedback.candidates_path)] == ["fb-a-1", "fb-a-2"]
    assert feedback.candidates_path.parent == stores.hank_dir()
    assert hashlib.sha256(cases.read_bytes()).hexdigest() == before  # the eval set is never touched (spec 8.3)


def test_experience_is_own_and_kept_apart() -> None:
    experience = stores.ExperienceStore(Clock())
    entry = experience.add("Bei 65 % Feuchte in der späten Blüte gab es keinen Schimmel.", stage="late_flower", run=1)
    assert entry["source"] == "own" and entry["id"].startswith("e-") and entry["run"] == 1
    assert experience.items() == [entry]
    with pytest.raises(ValueError):
        experience.add("   ")


def test_journal_imports_automation_lines_once() -> None:  # addendum N4: the automation switches inside the server
    log = Path(config.STATE_DIR) / "automation-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    on = datetime(2026, 10, 22, 11, 48)
    lines = [{"time": on.isoformat(timespec="seconds"), "action": "automation_on"},
             {"time": (on + timedelta(minutes=5)).isoformat(timespec="seconds"), "action": "humidifier_off",
              "value": 79.0}]
    log.write_text("".join(json.dumps(line) + "\n" for line in lines) + '{"time": "2026-10-22T12', encoding="utf-8")
    journal = stores.Journal(Clock())
    journal.add("dfan", "human", body={"action": "dfan", "step": 4})
    assert journal.import_automation() == 2
    assert journal.import_automation() == 0  # nothing new
    with log.open("a", encoding="utf-8") as f:
        f.write("\n" + json.dumps({"time": (on + timedelta(hours=1)).isoformat(timespec="seconds"),
                                   "action": "humidifier_on", "value": 44.0}) + "\n")
    assert journal.import_automation() == 1
    auto = [e for e in journal.all() if e["origin"] == "automation"]
    assert [e["action"] for e in auto] == ["automation_on", "humidifier_off", "humidifier_on"]
    assert auto[1]["time"] == "2026-10-22T11:53:00" and auto[1]["body"] == {"value": 79.0}
    assert [e["origin"] for e in journal.recent(3, exclude=("automation",))] == ["human"]
    assert journal.last("humidifier_on", "automation")["body"] == {"value": 44.0}


def test_chat_ring_keeps_50_and_counts_unread_notes() -> None:
    chat = stores.ChatRing(Clock())
    for i in range(55):
        chat.add({"role": "hank", "kind": "nudge" if i % 5 == 0 else "answer", "text": f"m{i}"})
    messages = chat.messages()
    assert len(messages) == 50 and messages[0]["text"] == "m5" and messages[-1]["id"] == 55
    assert chat.unread() == 10  # only Hank's notes count (addendum N6: the avatar button's counter)
    assert chat.ack(46) == 46 and chat.unread() == 1  # the note with id 51
    assert chat.ack(10) == 46  # never backwards
    assert chat.ack(999) == 55 and chat.unread() == 0


def test_stores_bundle_shares_one_clock() -> None:
    clock = Clock()
    bundle = stores.Stores(clock)
    assert bundle.dir == stores.hank_dir()
    assert bundle.gaps.clock is clock and bundle.journal.clock is clock and bundle.chat.clock is clock
    bundle.ledger.save({"version": 1, "items": []})
    assert bundle.ledger.load() == {"version": 1, "items": []}
    assert stores.safe(lambda: 1 / 0, "division") is None
    assert os.path.basename(str(bundle.nudge.path)) == "nudge-state.json"
