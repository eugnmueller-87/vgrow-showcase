"""Run Hank's eval set and print the table (docs/hank-v1-spec.md 11.1).

    python scripts/hank_eval.py --offline                      no model, runs anywhere
    python scripts/hank_eval.py --model --url http://127.0.0.1:11434   with the model (the Mac)
    python scripts/hank_eval.py --offline --dense --url http://127.0.0.1:11434   offline, dense search (WP11)

Options: --case ID (repeatable) for single cases, --no-save to only print. --dense routes with the embedding model
(the dense threshold set, as the server when the model answers); its vectors come from --url once and are cached in
STATE_DIR/hank/eval/embed-cache-<model>.json (scripts/hank_calibrate.py fills the same cache). --stop-after S ends a
model run after the first answer that took longer than S seconds (default 240, spec 4.7). Without --no-save the run lands in
STATE_DIR/hank/eval/ as <time>.json, latest.json and latest.md, and the comparison with the previous run lists
regressions.

The fixtures (F22, F22-alt) are built in a temporary state folder, never in the real one: the eval writes the plant
log and the automation state of its constructed day, and Hank's own lists (gaps, aliases) during the run.

Exit code: 0 when nothing failed, 1 when a case failed, 3 when the pipeline is not built yet (the table still shows
every case as "nicht gebaut"), 2 for wrong arguments.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for extra in (REPO, REPO / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from hank_eval import fixtures, report  # noqa: E402  (needs the paths above)

from vgrow import config  # noqa: E402


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hank eval set (spec 11.1)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="without a model: route, retrieval, draft")
    mode.add_argument("--model", action="store_true", help="with the model at --url (the Mac)")
    parser.add_argument("--url", default=None, help="Ollama URL for --model, e.g. http://127.0.0.1:11434")
    parser.add_argument("--case", action="append", dest="cases", default=None, help="only this case (repeatable)")
    parser.add_argument("--no-save", action="store_true", help="print only, write nothing to STATE_DIR/hank/eval")
    parser.add_argument("--dense", action="store_true", help="search with the embedding model at --url (or the cache)")
    parser.add_argument("--embed-cache", default=None, help="vector cache for --dense")
    parser.add_argument("--offline-embed", action="store_true", help="--dense from the cache only, never ask Ollama")
    parser.add_argument("--stop-after", type=float, default=240.0, help="model run: stop after an answer slower "
                                                                       "than this many seconds")
    args = parser.parse_args(argv)
    if args.model and not args.url:
        parser.error("--model needs --url")
    if args.cases:
        known = {c["id"] for c in report.load_cases()}
        unknown = [c for c in args.cases if c not in known]
        if unknown:
            parser.error(f"unknown case: {', '.join(unknown)}")
    return args


def _answerer(args: argparse.Namespace, out_dir: Path) -> Any:
    """The bridge, with the dense embedder when --dense, and the stop rule of a model run (--stop-after)."""
    from vgrow.hank import core, ollama

    embedder = None
    if args.dense:
        sys.path.append(str(REPO / "scripts"))
        from hank_calibrate import CachedEmbedder, safe_name

        model = ollama.embed_model_name()
        cache = Path(args.embed_cache) if args.embed_cache else out_dir / f"embed-cache-{safe_name(model)}.json"
        embedder = CachedEmbedder(args.url, model, cache, offline=args.offline_embed)
    state = {"stop": False}

    async def answer(question: str, **kw: Any) -> dict[str, Any]:
        if state["stop"]:
            return {"route": "skipped", "text": "", "hits": [], "model_calls": 0, "ms": None, "validator": None}
        out = await core.eval_answer(question, embedder=embedder, **kw)
        wall = (out.get("model") or {}).get("wall_s")
        if wall is not None and wall > args.stop_after:
            print(f"Abbruch: eine Antwort brauchte {wall} s (über {args.stop_after} s)", flush=True)
            state["stop"] = True
        return out

    return answer


def run(args: argparse.Namespace, out_dir: Path | None = None) -> report.Run:
    """The cases with fresh fixtures, in a temporary state folder with the fixture's day frozen."""
    answerer = _answerer(args, out_dir or config.STATE_DIR / "hank" / "eval")
    saved = (config.STATE_DIR, config.ENV_FILE, config.BRAIN_NTFY_FILE)
    with tempfile.TemporaryDirectory(prefix="hank-eval-") as tmp:
        root = Path(tmp)
        config.ENV_FILE = root / "no.env"  # no credentials, no push target: nothing leaves the machine
        config.BRAIN_NTFY_FILE = root / "no-brain.env"
        counter = iter(range(10**6))

        def build_live(name: str, lang: str, ledger: str | None) -> fixtures.Live:
            config.use_state_dir(root / f"case-{next(counter)}")  # every case starts from an empty state
            return fixtures.build(name, lang=lang, ledger=ledger)

        try:
            with fixtures.frozen_day():
                return report.run_all(build_live, ids=args.cases, answerer=answerer, model=args.model,
                                      url=args.url)
        finally:
            config.use_state_dir(saved[0])
            config.ENV_FILE, config.BRAIN_NTFY_FILE = saved[1], saved[2]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    out_dir = config.STATE_DIR / "hank" / "eval"  # read now: the run below points STATE_DIR elsewhere for a while
    result = run(args, out_dir)
    print(report.overview(result))
    print()
    print(report.table(result))
    if not args.no_save:
        path, found = report.save(result, out_dir)
        print()
        print(f"Gespeichert: {path} (und latest.json, latest.md)")
        for line in found:
            print(f"Rückschritt: {line}")
    counts = result.summary()["counts"]
    if counts["failed"]:
        return 1
    if counts["not_built"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
