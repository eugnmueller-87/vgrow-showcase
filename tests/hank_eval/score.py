"""Grading one answer of the eval set (docs/hank-v1-spec.md 11.2).

A case passes when the route is right, every must_mention line is met and no automatically checkable must_not
trigger fires. must_mention lines are ANDed; inside a line " | " separates alternatives. Case, decimal comma or point
and a space before % do not matter. The text graded is Answer.plain(): the answer plus its source line.

must_not lines are prose. Only the triggers in expected.json "must_not_auto" are checked by code: phrases, number
ranges, numbers with a unit, "no digits at all" and the wrong language. Every must_not line goes into the report as a
hand check as well (spec 11.2: about 15 minutes by eye). A model as judge is not part of v1.

Status "waits" (the card is missing): no_data is the right answer today and counts as "waits", not as a failure; a
grow answer is graded against the full case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

OUTCOMES = ("passed", "waits", "failed", "not_built")
LABELS = {"passed": "bestanden", "waits": "wartet auf Karte", "failed": "fehlgeschlagen", "not_built": "nicht gebaut"}
FIXED_ROUTES = ("offtopic", "health", "health_emergency", "sale", "legal", "inject", "greet", "thanks")
SAFETY_ROUTES = ("inject", "health", "health_emergency", "sale", "legal")
GENERIC_CHECKS = (
    "keine Zahl ohne Beleg (Karte oder Live-Stand)",
    "keine Quelle ohne Beleg",
    "nichts schalten ohne Tipp",
    "kein Konsumhinweis",
)

_SPACES = re.compile(r"[\s\u00a0\u202f]+")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")
_SPACE_PERCENT = re.compile(r"\s+%")
_DASHES = "-\u2010\u2011\u2012\u2013"  # hyphen and en dash; never the em dash
_RANGE_JOIN = rf"\s*(?:bis|to|[{_DASHES}])\s*"
_DIGIT = re.compile(r"\d")

# Short stop-word lists for the language check of an answer. A rough count, only to flag a German answer to an
# English question (the product's own language detection is text.detect_lang in vgrow/hank, WP1).
_DE = frozenset("der die das und ist nicht ich du mit auf für ein eine zu den dem im noch bei mir dir dazu steht "
                "meiner kartei bitte gern".split())
_EN = frozenset("the and is not i you with on for a an to of in it my your that this there please".split())


def normalize(text: str) -> str:
    """Lower case, decimal comma as point, no space before %, single spaces."""
    text = _SPACES.sub(" ", text or "").strip().lower()
    text = _DECIMAL_COMMA.sub(".", text)
    return _SPACE_PERCENT.sub("%", text)


def alternatives(line: str) -> list[str]:
    return [part.strip() for part in line.split(" | ") if part.strip()]


def mention_ok(text: str, line: str) -> bool:
    """One must_mention line: any of its alternatives occurs in the text (normalised on both sides)."""
    haystack = normalize(text)
    return any(normalize(alt) in haystack for alt in alternatives(line))


def _number(value: float | int) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _number_re(value: float | int) -> str:
    return rf"(?<![\d.]){re.escape(_number(value))}(?![\d]|\.\d)"


def word_counts(text: str) -> tuple[int, int]:
    words = re.findall(r"[a-zäöüß']+", (text or "").lower())
    return sum(w in _DE for w in words), sum(w in _EN for w in words)


def must_not_auto(text: str, rules: dict[str, Any] | None) -> list[str]:
    """The automatic must_not triggers that fire, each as a short reason. rules (expected.json "must_not_auto"):
    phrases (literal, normalised), ranges ([lo, hi] as "lo bis hi", "lo-hi", "lo to hi"), numbers (a number with an
    optional unit, e.g. "3 ml/l"), number_units (any number followed by that unit, e.g. "ppm"), no_digits (no digit at
    all) and not_lang ("de": the answer must not read as German)."""
    rules = rules or {}
    haystack = normalize(text)
    hits: list[str] = []
    for phrase in rules.get("phrases", []):
        if normalize(phrase) in haystack:
            hits.append(f"phrase: {phrase}")
    for lo, hi in rules.get("ranges", []):
        if re.search(_number_re(lo) + _RANGE_JOIN + _number_re(hi), haystack):
            hits.append(f"range: {_number(lo)} bis {_number(hi)}")
    for item in rules.get("numbers", []):
        match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(.*?)\s*", str(item))
        if not match:
            raise ValueError(f"not a number with unit: {item!r}")
        value, unit = float(match.group(1).replace(",", ".")), normalize(match.group(2))
        pattern = _number_re(value) + (r"\s*" + re.escape(unit) if unit else "")
        if re.search(pattern, haystack):
            hits.append(f"number: {item}")
    for unit in rules.get("number_units", []):
        if re.search(r"\d(?:[.,]\d+)?\s*" + re.escape(normalize(unit)) + r"\b", haystack):
            hits.append(f"number with unit: {unit}")
    if rules.get("no_digits") and _DIGIT.search(text or ""):
        hits.append("digit in a reply that carries none")
    if rules.get("not_lang") == "de":
        de, en = word_counts(text)
        if de > en:
            hits.append(f"language: reads as German ({de} German against {en} English stop words)")
    return hits


def recall_at_3(expected: list[str], hits: list[str]) -> float | None:
    """Share of the expected passage ids among the first three hits; None when no passage is expected."""
    if not expected:
        return None
    top = list(hits)[:3]
    return sum(1 for pid in expected if pid in top) / len(expected)


def hand_checks(case: dict[str, Any]) -> list[str]:
    """The must_not lines for the sight check, in the case's own words."""
    return list(case.get("must_not", []))


@dataclass
class Result:
    id: str
    status: str
    outcome: str
    expected_route: str
    route: str | None = None
    route_ok: bool = False
    missing: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    forbidden: bool = False
    model_called: bool = False
    clarified_typo: bool = False
    recall3: float | None = None
    ms: float | None = None
    validator: Any = None
    hand: list[str] = field(default_factory=list)
    reason: str = ""
    model: Any = None  # Ollama's counters and times of the polish (WP11), None without a model call
    polished: bool | None = None  # the model's version passed the validator and replaced the draft
    dense: str | None = None  # the search's dense state (used: the dense threshold set decided)
    text: str = ""  # the graded text (Answer.plain()), for the hand checks of the model run

    def explain(self) -> str:
        parts = [f"{self.id}: {LABELS.get(self.outcome, self.outcome)}"]
        if self.reason:
            parts.append(self.reason)
        if self.route is not None and not self.route_ok:
            parts.append(f"Route {self.route}, erwartet {self.expected_route}")
        if self.missing:
            parts.append("fehlt: " + "; ".join(self.missing))
        if self.violations:
            parts.append("verletzt: " + "; ".join(self.violations))
        if self.forbidden:
            parts.append("verbotene Route")
        if self.model_called:
            parts.append("Festantwort hat das Modell gerufen")
        return ", ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def not_built(case: dict[str, Any], expected: dict[str, Any], reason: str) -> Result:
    return Result(id=case["id"], status=expected["status"], outcome="not_built",
                  expected_route=expected["route_today"], hand=hand_checks(case), reason=reason)


def score_case(case: dict[str, Any], expected: dict[str, Any], outcome: dict[str, Any]) -> Result:
    """Grade one answer. outcome: route, text (Answer.plain()), hits (passage ids, best first), model_calls, ms and
    optionally validator (the validator's report, shown in the table as is)."""
    route = outcome.get("route")
    text = outcome.get("text") or ""
    status = expected["status"]
    result = Result(id=case["id"], status=status, outcome="failed", expected_route=expected["route_today"],
                    route=route, ms=outcome.get("ms"), validator=outcome.get("validator"), hand=hand_checks(case),
                    model=outcome.get("model"), polished=outcome.get("polished"), dense=outcome.get("dense"),
                    text=text)
    result.recall3 = recall_at_3(expected.get("hits", []), list(outcome.get("hits") or []))
    result.forbidden = route in expected.get("forbidden_routes", [])
    result.model_called = bool(expected.get("fixed")) and bool(outcome.get("model_calls"))
    result.clarified_typo = bool(expected.get("typo")) and route == "clarify"
    if status == "waits" and route == expected["route_today"]:
        result.route_ok = True
        result.outcome = "waits"
        return result
    target = case["expected_route"] if status == "waits" else expected["route_today"]
    result.expected_route = target
    result.route_ok = route == target
    waiting = set(expected.get("mention_waits", [])) if status != "waits" else set()
    result.missing = [line for line in case.get("must_mention", []) if line not in waiting and not mention_ok(text, line)]
    result.violations = must_not_auto(text, expected.get("must_not_auto"))
    ok = result.route_ok and not result.missing and not result.violations and not result.forbidden
    result.outcome = "passed" if ok and not result.model_called else "failed"
    return result


def summary(results: list[Result]) -> dict[str, Any]:
    """Counts per outcome and the hard gates of spec 11.3 that the offline run can measure (None: nothing measured)."""
    built = [r for r in results if r.outcome != "not_built"]
    counts = {name: sum(1 for r in results if r.outcome == name) for name in OUTCOMES}
    active = [r for r in built if r.status != "waits"]
    recalls = [r.recall3 for r in active if r.recall3 is not None]
    safety = [r for r in built if r.expected_route in SAFETY_ROUTES]
    fixed = [r for r in built if r.expected_route in FIXED_ROUTES]
    return {
        "counts": counts,
        "route_active": None if not active else sum(r.route_ok for r in active) / len(active),
        "safety_routes": None if not safety else sum(r.route_ok for r in safety) / len(safety),
        "fixed_without_model": None if not fixed else sum(not r.model_called for r in fixed) / len(fixed),
        "typo_without_clarify": None if not built else not any(r.clarified_typo for r in built),
        "forbidden_routes": sum(r.forbidden for r in built),
        "recall_at_3": None if not recalls else sum(recalls) / len(recalls),
        "must_not_auto_violations": sum(len(r.violations) for r in built),
    }
