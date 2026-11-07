"""python -m vgrow.hank: Hank from the command line (spec 8.1, 8.3, 8.4, 10.4 WP8).

  ask "Frage" [--no-model] [--lang de] [--demo]   one question; --no-model: the draft only, no Ollama call
  gaps [--top 20]                                 the open gaps, asked most first
  gaps export                                     the open gaps as JSON for the research
  gaps resolve                                    route every open question gap again; hits become "answered"
  feedback-to-eval                                eval candidates from the thumbs (never into the eval set itself)
  experience add "Text" [--stage KEY] [--run N] [--en TEXT]   an own note, source "own"
  reindex [--lexical]                             rebuild the index; without --lexical embed what is missing
  bench [--runs 3]                                router, search and draft times without the model (p50, p95)

The CLI never logs in to VIVOSUN and never switches anything. ask reads no live box: without --demo the live state is
empty (only the cards answer), with --demo it is the demo box's made-up moment. State goes to STATE_DIR/hank/ like the
server's, so `gaps` shows what the server collected. The CLI is an operator's tool: its own lines are English, like
python -m vgrow; Hank's answers come in the question's language from the catalogs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from vgrow import config

from . import ollama, stores, wiring


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _demo_live() -> Any:
    from vgrow.automation import HumidifierAutomation
    from vgrow.demo import DemoBox
    from vgrow.plant import PlantLog

    box = DemoBox()
    log = PlantLog()
    return wiring.Collector(box, plant_log=log, automation=HumidifierAutomation(box, None, log))


async def _ask(args: argparse.Namespace) -> int:
    from .core import Hank

    if args.demo:
        config.use_state_dir(config.STATE_DIR / "demo")  # like the server's demo: never the real run's state
    client = None if args.no_model else ollama.OllamaClient()
    hank = Hank(live=_demo_live() if args.demo else None, client=client, model=not args.no_model)
    try:
        asked = await hank.ask_full(" ".join(args.question), lang=args.lang)
        answer = asked.answer
        if asked.job is not None and asked.job.task is not None:
            await asked.job.task
            answer = asked.job.answer or answer
        print(f"[{asked.route.name}] {answer.plain()}")
        if answer.note:
            print(answer.note)
        for card in answer.proposals:
            print(f"card: {card.label} {json.dumps(card.body, ensure_ascii=False)}")
        return 0
    finally:
        if client is not None:
            await client.close()


def _gaps(args: argparse.Namespace) -> int:
    gaps = stores.GapStore()
    if args.action == "export":
        _print_json(gaps.export())
        return 0
    if args.action == "resolve":
        from .core import shared_index
        from .router import Router

        router = Router(shared_index())
        done = gaps.resolve(lambda gap: router.route(gap["question"], lang_hint=gap.get("lang") or "de").name ==
                            "grow")
        print(f"{len(done)} gaps answered.")
        for gap in done:
            print(f"- {gap.get('question')}")
        return 0
    for gap in gaps.top(args.top):
        label = gap.get("question") or json.dumps(gap.get("detail"), ensure_ascii=False)
        print(f"{gap.get('count', 1):>4}  {gap.get('kind'):<26} {label}")
    return 0


def _feedback_to_eval(_: argparse.Namespace) -> int:
    found = stores.FeedbackStore()
    written = found.to_eval()
    print(f"{len(written)} candidates written to {found.candidates_path}")
    return 0


def _experience(args: argparse.Namespace) -> int:
    entry = stores.ExperienceStore().add(" ".join(args.text), stage=args.stage, run=args.run, text_en=args.en)
    _print_json(entry)
    return 0


async def _reindex(args: argparse.Namespace) -> int:
    from .kartei_index import Index

    client = None if args.lexical else ollama.OllamaClient()
    try:
        index = Index.load(embedder=client)
        if client is not None:
            await index.start()
        _print_json(index.state())
        return 0
    finally:
        if client is not None:
            await client.close()


def _questions() -> list[tuple[str, str]]:
    cases = config.REPO / "tests" / "hank_eval" / "cases.json"
    try:
        data = json.loads(Path(cases).read_text(encoding="utf-8"))
        found = [(c["question"], c["lang"]) for c in data["cases"]]
    except (OSError, ValueError, KeyError):
        found = []
    if found:
        return found
    from . import texts

    return [(q, lang) for lang in ("de", "en") for q in texts.chips(lang)]


async def _bench(args: argparse.Namespace) -> int:
    from .core import Hank, shared_index

    hank = Hank(index=shared_index(), model=False)
    times: list[float] = []
    questions = _questions()
    for _ in range(max(1, args.runs)):
        for question, lang in questions:
            started = time.perf_counter()
            await hank.ask_full(question, lang=lang, model=False)
            times.append((time.perf_counter() - started) * 1000)
    times.sort()
    p95 = times[min(len(times) - 1, round(0.95 * (len(times) - 1)))]
    print(f"{len(times)} answers without the model: p50 {statistics.median(times):.0f} ms, p95 {p95:.0f} ms, "
          f"max {times[-1]:.0f} ms")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="python -m vgrow.hank", description="Hank, the cockpit's read-only assistant")
    sub = top.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="one question")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--no-model", action="store_true", help="the draft only, no Ollama call")
    ask.add_argument("--lang", choices=("de", "en"), default=None)
    ask.add_argument("--demo", action="store_true", help="the demo box's made-up live state")
    gaps = sub.add_parser("gaps", help="the gap list")
    gaps.add_argument("action", nargs="?", choices=("export", "resolve"))
    gaps.add_argument("--top", type=int, default=20)
    sub.add_parser("feedback-to-eval", help="eval candidates from the thumbs")
    exp = sub.add_parser("experience", help="own experience")
    exp_sub = exp.add_subparsers(dest="experience_command", required=True)
    add = exp_sub.add_parser("add", help="add an own note")
    add.add_argument("text", nargs="+")
    add.add_argument("--stage", default=None)
    add.add_argument("--run", default=None)
    add.add_argument("--en", default=None, help="the English text of the note")
    reindex = sub.add_parser("reindex", help="rebuild the index")
    reindex.add_argument("--lexical", action="store_true", help="no embeddings")
    bench = sub.add_parser("bench", help="router, search and draft times without the model")
    bench.add_argument("--runs", type=int, default=3)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "ask":
        return asyncio.run(_ask(args))
    if args.command == "gaps":
        return _gaps(args)
    if args.command == "feedback-to-eval":
        return _feedback_to_eval(args)
    if args.command == "experience":
        return _experience(args)
    if args.command == "reindex":
        return asyncio.run(_reindex(args))
    return asyncio.run(_bench(args))


if __name__ == "__main__":
    sys.exit(main())
