"""Hank's shelves measured: does each question land on the right card or route? (owner, 01.11.2026: "move everything
he's collecting into a RAG ... and the rule that he checks at first for everything").

Without the model (the route and the search only), on the eval fixture F22 with the day frozen, like the strain tests.
A case passes when the route is one of its `routes` (or, without `routes`, none of the set's `never` routes) and its
`card` (or one of its `cards`) is among the route's cards or the top three hits.

    python scripts/hank_shelves.py tests/hank_eval/shelves_dev.json
    python scripts/hank_shelves.py tests/hank_eval/shelves_holdout.json   # once, at the end
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]  # tests/hank_eval (the package), not scripts/hank_eval.py

TOP = 3


def found_cards(route: Any) -> list[str]:
    """The cards an answer stands on: a shelf answer's first card only (it is told), a shelf question's buttons, else
    the route's cards and the top three hits."""
    if route.form == "shelf":
        return list(route.cards or [])[:1]
    if route.form == "shelf_suggest":
        return list(route.cards or [])
    cards = list(route.cards or [])
    hits = getattr(route.retrieval, "hits", None) or []
    for hit in hits[:TOP]:
        passage = hit.passage
        cards.append(f"{passage.kind}/{passage.card}")
    return list(dict.fromkeys(cards))


def judge(case: dict[str, Any], route: Any, never: list[str]) -> tuple[bool, str]:
    routes = case.get("routes")
    if routes and route.name not in routes:
        return False, f"route {route.name}, wanted {'/'.join(routes)}"
    if not routes and route.name in never:
        return False, f"route {route.name}"
    wanted = case.get("cards") or ([case["card"]] if case.get("card") else [])
    if wanted:
        cards = found_cards(route)
        if not any(w in cards for w in wanted):
            return False, f"cards {cards[:4]}, wanted {'/'.join(wanted)}"
    return True, ""


def run(path: Path) -> int:
    import pytest
    from hank_eval import fixtures
    from vgrow.hank import core

    data = json.loads(path.read_text(encoding="utf-8"))
    never = list(data.get("never") or [])
    patch = pytest.MonkeyPatch()
    fixtures.freeze(patch)

    def live(lang: str) -> dict[str, Any]:
        sources = fixtures.build("F22", lang=lang).sources()
        sources.pop("ledger")
        return sources

    async def go() -> list[tuple[dict[str, Any], Any]]:
        out = []
        for case in data["cases"]:
            hank = core.Hank(live=lambda lang=None, _l=case["lang"]: live(_l), index=core.shared_index())
            try:
                got = await hank.ask_full(case["q"], client_id=case["id"], lang=case["lang"], model=False)
                out.append((case, got.route))
            finally:
                await hank.stop()
        return out

    passed = 0
    for case, route in asyncio.run(go()):
        ok, why = judge(case, route, never)
        passed += ok
        print(f"{'ok  ' if ok else 'FAIL'} {case['id']}  {route.name:12} {case['q'][:58]:58} {why}")
    print(f"\n{passed} of {len(data['cases'])} passed ({path.name})")
    patch.undo()
    return 0 if passed == len(data["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1])))
