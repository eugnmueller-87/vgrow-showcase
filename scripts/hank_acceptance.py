"""Run the 20 acceptance cases of addendum N7 (tests/hank_eval/acceptance.json) and print them case by case.

    python scripts/hank_acceptance.py            lexical set and the draft, fixture F22, no model
    python scripts/hank_acceptance.py --json     the same as JSON

The cases were written after the WP11 calibration and are for the acceptance only: nothing is tuned on them. A case
is right when its route is one of the accepted routes and, for a live case with "opening" (review of 23.10.2026), when
the answer's first sentence names the device or reading asked about: the route alone said 17 of 20 while "Läuft der
Lüfter?" opened with the humidity verdict. The holdout list (today wordings written before the today patterns were
widened) is reported on its own, and so is the fix regression list (the fix wish of 23.10.2026: eight wishes that
must route fix, four near misses that must not; written with the patterns in view, a regression gate, not an unseen
measure). The dense set needs the embedding model on the Mac; until it ran there, every case
is reported as "dense pending".

Like scripts/hank_eval.py the fixture is built in a temporary state folder with the fixture's day frozen, one fresh
folder per case, so nothing reaches the real state. Exit code: 0 always (a report, not a gate), 2 for a broken file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for extra in (REPO, REPO / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from hank_eval import fixtures  # noqa: E402  (needs the paths above)

from vgrow import config  # noqa: E402
from vgrow.hank import text  # noqa: E402

ACCEPTANCE_FILE = REPO / "tests" / "hank_eval" / "acceptance.json"
CATEGORIES = {"grow": 6, "live": 4, "today": 2, "offtopic": 5, "safety": 3}  # addendum N7, as the owner asked
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n")


def first_sentence(answer: str) -> str:
    """The answer's first sentence (split after . ! ? and a space, or at a line break)."""
    return next((part for part in _SENTENCE_END.split(answer.strip()) if part.strip()), "")


def opening_ok(case: dict[str, Any], answer: str) -> bool | None:
    """None without "opening"; else whether the first sentence holds one of its words (folded)."""
    words = case.get("opening")
    if not words:
        return None
    head = text.fold(first_sentence(answer))
    return any(text.fold(w) in head for w in words)


def load() -> dict[str, Any]:
    return json.loads(ACCEPTANCE_FILE.read_text(encoding="utf-8"))


def run(cases: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every case through the router and the draft (core.eval_answer, lexical, no model). One row per case. ok: the
    route is accepted and, where the case has "opening", the first sentence names what was asked about."""
    from vgrow.hank import core

    data = load()
    cases = data["cases"] if cases is None else cases
    rows: list[dict[str, Any]] = []
    saved = (config.STATE_DIR, config.ENV_FILE, config.BRAIN_NTFY_FILE)
    with tempfile.TemporaryDirectory(prefix="hank-acceptance-") as tmp:
        root = Path(tmp)
        config.ENV_FILE = root / "no.env"
        config.BRAIN_NTFY_FILE = root / "no-brain.env"
        try:
            with fixtures.frozen_day():
                for number, case in enumerate(cases):
                    config.use_state_dir(root / f"case-{number}")
                    live = fixtures.build(data.get("fixture", "F22"), lang=case["lang"]).sources()
                    out = asyncio.run(core.eval_answer(case["question"], lang=case["lang"], live=live))
                    route_ok = out["route"] in case["accept"]
                    opening = opening_ok(case, out["text"])
                    rows.append({"id": case["id"], "category": case["category"], "lang": case["lang"],
                                 "question": case["question"], "expected": case["expected_route"],
                                 "route": out["route"], "reason": out.get("reason"), "route_ok": route_ok,
                                 "opening": opening, "ok": route_ok and opening is not False, "dense": "pending",
                                 "ms": out.get("ms"), "text": out["text"]})
        finally:
            config.use_state_dir(saved[0])
            config.ENV_FILE, config.BRAIN_NTFY_FILE = saved[1], saved[2]
    return rows


def table(rows: list[dict[str, Any]], title: str = "Abnahme N7") -> str:
    routed = sum(r.get("route_ok", r["ok"]) for r in rows)
    lines = [f"{title}, lexikalisch, Entwurf: {sum(r['ok'] for r in rows)} von {len(rows)} richtig "
             f"({routed} auf einer angenommenen Route, Live-Fälle zusätzlich mit dem ersten Satz); "
             f"dicht: wartet auf den Mac", ""]
    for r in rows:
        mark = "ok" if r["ok"] else ("ANFANG" if r.get("route_ok") else "FALSCH")
        first = r["text"].replace("\n", " / ")
        lines.append(f"{r['id']}  {r['category']:<8} {mark:<6} erwartet {r['expected']:<8} bekam {r['route']:<8} "
                     f"({r['reason']})  {r['question']}")
        lines.append(f"        {first[:220]}{'...' if len(first) > 220 else ''}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Hank acceptance cases of addendum N7")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    args = parser.parse_args(argv)
    try:
        rows = run()
        holdout = run(load().get("holdout") or [])
        regression = run(load().get("fix_regression") or [])
    except (OSError, ValueError, KeyError) as err:
        print(f"acceptance.json unreadable: {err}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"cases": rows, "holdout": holdout, "fix_regression": regression}, ensure_ascii=False,
                         indent=1))
    else:
        print(table(rows))
        if holdout:
            print()
            print(table(holdout, "Holdout Heute (nie darauf abgestimmt)"))
        if regression:
            print()
            print(table(regression, "Rückfall Fix-Wunsch 23.10.2026 (mit den Mustern im Blick geschrieben)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
