"""Addendum N7: the 20 acceptance cases written after the calibration (tests/hank_eval/acceptance.json), and the
holdout list of today wordings added with the review of 23.10.2026.

These tests check the file and the runner, never the routes: the cases are for the acceptance report only
(scripts/hank_acceptance.py), and nothing may be tuned on them. A test that asserted their outcomes would be exactly
that tuning.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from hank_eval import report
from vgrow.hank import gates as gates_mod
from vgrow.hank.types import ROUTES

REPO = Path(__file__).resolve().parent.parent
FILE = REPO / "tests" / "hank_eval" / "acceptance.json"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
sys.path.insert(0, str(REPO / "scripts"))

import hank_acceptance  # noqa: E402  (needs the path above)


def data() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def test_twenty_cases_in_the_asked_mix() -> None:
    cases = data()["cases"]
    assert len(cases) == 20 and len({c["id"] for c in cases}) == 20
    assert Counter(c["category"] for c in cases) == hank_acceptance.CATEGORIES
    for case in cases:
        assert case["lang"] in ("de", "en") and case["question"].strip(), case["id"]
        assert case["expected_route"] in ROUTES and case["expected_route"] in case["accept"], case["id"]
        assert set(case["accept"]) <= set(ROUTES), case["id"]
        if case["category"] in ("grow", "live"):
            assert "offtopic" not in case["accept"], case["id"]  # a growing question is never turned down
    safety = sorted(c["expected_route"] for c in cases if c["category"] == "safety")
    assert safety == ["health", "inject", "sale"]


def test_holdout_is_ten_today_wordings_and_live_cases_carry_an_opening() -> None:
    holdout = data()["holdout"]
    assert len(holdout) == 10 and len({c["id"] for c in holdout}) == 10
    assert not {c["id"] for c in holdout} & {c["id"] for c in data()["cases"]}
    assert {c["category"] for c in holdout} == {"today"} and {c["lang"] for c in holdout} == {"de", "en"}
    for case in holdout:
        assert case["accept"] == ["today"] and case["expected_route"] == "today", case["id"]
    for case in data()["cases"]:
        if case["category"] == "live":
            assert case.get("opening") and all(isinstance(w, str) and w for w in case["opening"]), case["id"]


def test_fix_regression_list_is_separate_and_well_formed() -> None:
    """The fix wish of 23.10.2026 as a regression list beside the N7 cases (never counted among the 20)."""
    rows = data()["fix_regression"]
    assert data()["fix_regression_about"] and len({c["id"] for c in rows}) == len(rows) == 12
    assert not {c["id"] for c in rows} & {c["id"] for c in data()["cases"] + data()["holdout"]}
    assert Counter(c["category"] for c in rows) == {"fix": 8, "fix_near": 4}
    for case in rows:
        assert set(case["accept"]) <= set(ROUTES) and case["expected_route"] in case["accept"], case["id"]
        assert (case["accept"] == ["fix"]) == (case["category"] == "fix"), case["id"]


def test_opening_reads_the_first_sentence_only() -> None:
    case = {"opening": ["Umluft"]}
    assert hank_acceptance.opening_ok(case, "Umluft: Stufe 3, läuft. Luftfeuchte 79 %.") is True
    assert hank_acceptance.opening_ok(case, "Luftfeuchte 79 %, zu hoch. Umluft: Stufe 3.") is False
    assert hank_acceptance.opening_ok(case, "Luftfeuchte 79 %\nUmluft: Stufe 3.") is False
    assert hank_acceptance.opening_ok({}, "egal") is None
    assert hank_acceptance.first_sentence("VPD 1.4 kPa, ok. Rest.") == "VPD 1.4 kPa, ok."


def test_the_wordings_are_unseen() -> None:
    cases = data()["cases"] + data()["holdout"]
    known = {c["question"] for c in report.load_cases()}
    off = json.loads((REPO / "vgrow" / "hank" / "data" / "offtopic.json").read_text(encoding="utf-8"))
    gates = json.loads((REPO / "vgrow" / "hank" / "data" / "gates.json").read_text(encoding="utf-8"))
    for section in gates.values():
        if isinstance(section, dict) and isinstance(section.get("examples"), dict):
            known |= {s for sentences in section["examples"].values() for s in sentences}
    off_sentences = [s for sentences in off["examples"].values() for s in sentences]
    known |= set(off_sentences)
    tests = "".join(p.read_text(encoding="utf-8") for p in (REPO / "tests").glob("test_*.py") if p.name != Path(
        __file__).name)
    bank = gates_mod.ExampleBank.of("off", off["examples"])
    for case in cases:
        assert case["question"] not in known, case["id"]
        assert case["question"] not in tests, case["id"]
        if case["category"] == "offtopic":  # "not similar to offtopic.json": far below row 4b's 0.75
            score, near = bank.best(gates_mod.gram_set(case["question"]))
            assert score < 0.5, (case["id"], score, near)


def test_file_is_clean() -> None:
    raw = FILE.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw and EM_DASH not in raw.decode("utf-8")
    assert "dense pending" in data()["about"]


def test_runner_reports_rows_without_grading_more_than_the_route() -> None:
    cases = [c for c in data()["cases"] if c["category"] == "safety"]
    rows = hank_acceptance.run(cases)
    assert [r["id"] for r in rows] == [c["id"] for c in cases]
    for row in rows:
        assert set(row) >= {"id", "category", "expected", "route", "reason", "route_ok", "opening", "ok", "dense",
                            "text"}
        assert row["dense"] == "pending" and isinstance(row["ok"], bool) and row["opening"] is None
    assert "Abnahme N7" in hank_acceptance.table(rows)
