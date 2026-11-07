"""Running the eval set and reporting it (docs/hank-v1-spec.md 11.1): load, answer, grade, table, files, regressions.

The answer comes through one bridge into the pipeline, BRIDGE below. Until that function exists the run reports
every case as "not built" and still prints the table, so the set is usable before Hank is.

Bridge contract (for WP8, owner of vgrow/hank/core.py; requested in the WP0 report, not decided here):
    eval_answer(question: str, *, lang: str, live: dict, model: bool = False, url: str | None = None) -> dict
        live:  fixtures.Live.sources(): snapshot, camera, plant_view, automation_view, alerts_view, feeding_view,
               vision, ports, ledger, now (the objects wiring.py reads, spec 4.2)
        model: False offline (the draft, no model call); True with an Ollama at url (the Mac run)
        before: (only when the case has "before") the questions asked first in the same chat
        returns {"route": Route.name, "text": Answer.plain(), "hits": [passage ids, best first, from Retrieval],
                 "model_calls": number of /api/chat calls for this answer, "ms": server time in milliseconds,
                 "validator": the validator's report or None}
    It may be a coroutine function. Hank's own state (gaps, aliases) goes to config.STATE_DIR, which the run isolates.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import fixtures
from .score import GENERIC_CHECKS, LABELS, Result, not_built, score_case, summary

HERE = Path(__file__).resolve().parent
CASES_FILE = HERE / "cases.json"
EXPECTED_FILE = HERE / "expected.json"
BRIDGE = ("vgrow.hank.core", "eval_answer")

Answerer = Callable[..., Any]


class NotBuilt(Exception):
    """The pipeline (or the bridge into it) does not exist yet."""


def load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


def load_expected() -> dict[str, dict[str, Any]]:
    return json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))["cases"]


def bridge() -> Answerer:
    """The pipeline's eval entry, or NotBuilt with the reason."""
    module_name, attr = BRIDGE
    try:
        module = importlib.import_module(module_name)
    except ImportError as err:
        raise NotBuilt(f"{module_name} fehlt (WP8): {err}") from err
    found = getattr(module, attr, None)
    if not callable(found):
        raise NotBuilt(f"{module_name}.{attr} fehlt (Vertrag in tests/hank_eval/report.py)")
    return found


def answer(answerer: Answerer, case: dict[str, Any], live: fixtures.Live, *, model: bool = False,
           url: str | None = None) -> dict[str, Any]:
    # a chat: the questions asked first, for an answerer that takes them (a stand-in of a test may not)
    params = inspect.signature(answerer).parameters
    takes = "before" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    extra = {"before": list(case["before"])} if case.get("before") and takes else {}
    out = answerer(case["question"], lang=case["lang"], live=live.sources(), model=model, url=url, **extra)
    if inspect.isawaitable(out):
        out = asyncio.run(out)  # type: ignore[arg-type]
    if not isinstance(out, dict) or "route" not in out:
        raise TypeError(f"the bridge returned {type(out).__name__} without a route for {case['id']}")
    return out


def run_one(case: dict[str, Any], expected: dict[str, Any], live: fixtures.Live | None, *,
            answerer: Answerer | None = None, model: bool = False, url: str | None = None) -> Result:
    """One case. live: the built fixture of expected["fixture"]. Raises NotBuilt while the pipeline is missing."""
    if live is None:
        raise ValueError(f"{case['id']} needs the fixture {expected.get('fixture')}")
    found = answerer if answerer is not None else bridge()
    return score_case(case, expected, answer(found, case, live, model=model, url=url))


@dataclass
class Run:
    started: str
    mode: str
    results: list[Result] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return summary(self.results)

    def as_dict(self) -> dict[str, Any]:
        return {"started": self.started, "mode": self.mode, "summary": self.summary(),
                "results": [r.as_dict() for r in self.results]}


def run_all(build_live: Callable[[str, str, str | None], fixtures.Live], *, ids: list[str] | None = None,
            answerer: Answerer | None = None, model: bool = False, url: str | None = None) -> Run:
    """Every case (or those in ids). build_live(fixture, lang, ledger) gives a fresh Live per case; the caller owns
    the state folder and the frozen day."""
    run = Run(started=datetime.now().isoformat(timespec="seconds"), mode="model" if model else "offline")
    expected = load_expected()
    try:
        found = answerer if answerer is not None else bridge()
        missing = None
    except NotBuilt as err:
        found, missing = None, str(err)
    for case in load_cases():
        if ids and case["id"] not in ids:
            continue
        exp = expected[case["id"]]
        if found is None:
            run.results.append(not_built(case, exp, missing or ""))
            continue
        live = build_live(exp["fixture"], case["lang"], exp.get("ledger"))
        run.results.append(score_case(case, exp, answer(found, case, live, model=model, url=url)))
    return run


# ---------- output ----------

def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value).replace("|", "/").replace("\n", " ")


def table(run: Run) -> str:
    """The table of spec 11.1: case, route, time, result, validator report, number of hand checks."""
    head = ("Fall", "Status", "Route erwartet", "Route", "Zeit ms", "Modell s", "Ergebnis", "recall@3", "Prüfer",
            "Fehlt", "Handprüfung")
    rows = [head]
    for r in run.results:
        model_s = (r.model or {}).get("wall_s") if isinstance(r.model, dict) else None
        rows.append((r.id, r.status, r.expected_route, _cell(r.route), _cell(r.ms), _cell(model_s),
                     LABELS.get(r.outcome, r.outcome), _cell(r.recall3), _cell(r.validator),
                     _cell("; ".join(r.missing + r.violations) or None), str(len(r.hand))))
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def overview(run: Run) -> str:
    s = run.summary()
    counts = ", ".join(f"{LABELS[k]} {v}" for k, v in s["counts"].items())
    gates = [f"{name}: {_cell(s[name])}" for name in ("route_active", "safety_routes", "fixed_without_model",
                                                      "typo_without_clarify", "forbidden_routes", "recall_at_3",
                                                      "must_not_auto_violations")]
    reasons = sorted({r.reason for r in run.results if r.outcome == "not_built" and r.reason})
    out = [f"Prüfsatz {run.mode}, {run.started}: {len(run.results)} Fälle. {counts}.", "Tore: " + ", ".join(gates)]
    if reasons:
        out.append("Nicht gebaut: " + " / ".join(reasons))
    return "\n".join(out)


def markdown(run: Run) -> str:
    """latest.md: overview, table, then the hand checks per case (spec 11.2: prose must_not lines by eye)."""
    parts = ["# Hank-Prüfsatz", "", overview(run).replace("\n", "\n\n"), "", "```", table(run), "```", "",
             "## Handprüfung", "", "Für jede Antwort: " + "; ".join(GENERIC_CHECKS) + ".", ""]
    for r in run.results:
        if r.hand:
            parts.append(f"### {r.id}")
            parts.extend(f"- [ ] {line}" for line in r.hand)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def regressions(previous: dict[str, Any] | None, run: Run) -> list[str]:
    """Cases that passed in the previous run and do not pass now (spec 11.1: the comparison shows regressions)."""
    if not previous:
        return []
    before = {r["id"]: r["outcome"] for r in previous.get("results", [])}
    return [f"{r.id}: vorher {LABELS.get(before[r.id], before[r.id])}, jetzt {LABELS.get(r.outcome, r.outcome)}"
            for r in run.results if before.get(r.id) == "passed" and r.outcome not in ("passed", "not_built")]


def save(run: Run, out_dir: Path) -> tuple[Path, list[str]]:
    """<date>.json per run, latest.json and latest.md (spec 11.1). Returns the run file and the regressions."""
    from vgrow import config

    previous = None
    latest = out_dir / "latest.json"
    try:
        previous = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    found = regressions(previous, run)
    data = json.dumps(run.as_dict(), indent=2, ensure_ascii=False) + "\n"
    stamp = run.started.replace(":", "-")
    path = out_dir / f"{stamp}.json"
    config.atomic_write(path, data)
    config.atomic_write(latest, data)
    text = markdown(run)
    if found:
        text += "\n## Rückschritte gegen den Vorlauf\n\n" + "\n".join(f"- {line}" for line in found) + "\n"
    config.atomic_write(out_dir / "latest.md", text)
    return path, found
