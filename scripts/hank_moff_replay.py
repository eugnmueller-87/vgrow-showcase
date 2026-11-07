"""Replay row 3b (M_OFF) of the dense set on every calibration item, from the vector cache only (no Mac, no Ollama).

    python scripts/hank_moff_replay.py                    M_OFF 0.11 against 0.09 (the change of 23.10.2026)
    python scripts/hank_moff_replay.py --old 0.11 --new 0.10
    python scripts/hank_moff_replay.py --cache <file>     another vector cache

What it does (docs/hank-v1-messung.md, M_OFF 0.09): the 711 items of scripts/hank_calibrate.py (the 64 eval cases,
their typo variants, the safety gate rewordings and the sentences of offtopic.json) go through the router once with
their cached vectors, then the decision table of addendum N2 is replayed with both M_OFF values, for the router's
view (the sentence in its own bank) and the honest one (left out). It prints, as JSON: the items without a vector,
every route that changes between the two values, the items whose margin lies between them, the margin range per
group and hank_calibrate.verify's mismatches of the real router at the new value. Exit code 0 when nothing changed
and nothing mismatched, 1 otherwise, 2 when vectors are missing (run hank_calibrate.py against the Mac once).

The vectors come from the cache hank_calibrate.py fills (default STATE_DIR/hank/eval/embed-cache-<model>.json).
Like the calibration, everything runs in an isolated state folder; nothing is written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for extra in (REPO, REPO / "tests", REPO / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import hank_calibrate as hc  # noqa: E402  (needs the paths above)

from vgrow import config  # noqa: E402
from vgrow.hank import ollama  # noqa: E402
from vgrow.hank.kartei_index import Index  # noqa: E402
from vgrow.hank.router import Router, clean  # noqa: E402


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay M_OFF (row 3b) on the calibration items, cache only")
    parser.add_argument("--old", type=float, default=0.11, help="the M_OFF before (default 0.11)")
    parser.add_argument("--new", type=float, default=0.09, help="the M_OFF after (default 0.09)")
    parser.add_argument("--embed-model", default=None, help="embedding model (default: HANK_EMBED_MODEL, else "
                                                            "granite-embedding:278m)")
    parser.add_argument("--cache", default=None, help="vector cache file (default STATE_DIR/hank/eval/"
                                                      "embed-cache-<model>.json)")
    return parser.parse_args(argv)


async def replay(args: argparse.Namespace) -> dict[str, Any]:
    model = args.embed_model or ollama.embed_model_name()
    cache = Path(args.cache) if args.cache else hc.default_out_dir() / f"embed-cache-{hc.safe_name(model)}.json"
    hc.TYPO_CASES = frozenset(k for k, v in hc.report.load_expected().items() if v.get("typo"))
    items = hc.build_items()
    emb = hc.CachedEmbedder(None, model, cache, offline=True)
    with hc.isolated() as root:
        lives = hc.LiveBook(root)
        index = Index.load(embedder=emb, cache_dir=config.STATE_DIR / "hank-index")
        router = Router(index)
        passages = [d.embed_text for d in index._state.card_docs if d.passage.kind not in hc.NOT_BY_DEFAULT]
        await emb.prefetch(passages, "doc")
        if await index.build_dense(timeout=60) != "used":
            raise ollama.OllamaError("no_ollama", "the card vectors are not all in the cache")
        await emb.prefetch([e.text for e in router.offtopic.examples], "query")
        await router.warm(emb)
        missing = [i.id for i in items if emb.cached(clean(i.question)) is None]

        def vec_of(q: str) -> list[float] | None:
            found = emb.cached(q)
            return hc._normalize(found) if found else None

        records = hc.build_records(router, items, lives, vec_of)
        base = dict(index.thresholds["dense"])
        old, new = {**base, "M_OFF": args.old}, {**base, "M_OFF": args.new}
        lo, hi = sorted((args.old, args.new))
        changed, band = [], []
        groups: dict[str, list[float]] = {}
        for rec in records:
            for in_bank in (False, True):
                a, b = hc.route_of(rec, old, in_bank), hc.route_of(rec, new, in_bank)
                if a != b:
                    changed.append({"id": rec.item.id, "in_bank": in_bank, "old": a, "new": b,
                                    "expected": rec.item.expected})
            if rec.base is None or rec.base.nn is None:
                continue
            for margin in (rec.base.nn, rec.base.nn_inbank):
                if margin is not None and lo <= margin < hi:
                    band.append({"id": rec.item.id, "margin": round(margin, 3)})
            key = rec.item.case_kind if rec.item.source != "offtopic_file" else "offtopic.json"
            groups.setdefault(key, []).append(rec.base.nn)
        mismatches = hc.verify(router, records, new, "dense", lives)
    return {"cache": str(cache), "items": len(items), "no_vector": missing, "old": args.old, "new": args.new,
            "route_changes": changed, "margin_between": band,
            "margin_range": {k: [round(min(v), 3), round(max(v), 3), len(v)] for k, v in sorted(groups.items())},
            "verify_mismatches_at_new": mismatches[:20], "n_mismatch": len(mismatches)}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    try:
        result = asyncio.run(replay(args))
    except ollama.OllamaError as err:
        print(f"vectors missing in the cache: {err}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    if result["no_vector"]:
        return 2
    return 1 if result["route_changes"] or result["n_mismatch"] else 0


if __name__ == "__main__":
    sys.exit(main())
