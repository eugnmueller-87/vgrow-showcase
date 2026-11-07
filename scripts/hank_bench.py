"""Measure Hank's model costs on the Mac (docs/hank-v1-spec.md 2.3, 4.8, 10.4 WP11, 12.1; addendum N0).

    python scripts/hank_bench.py --url http://127.0.0.1:11434 [--ssh user@host] [--out bench.json]

What it measures, one request at a time (never two model requests at once):
1. Ollama version and the models present.
2. The embedding model (HANK_EMBED_MODEL, else granite-embedding:278m): cold (unloaded first, --cold-runs times)
   and warm per question (every question of the eval set, one request each), then the card passages as the server
   embeds them (batches of 16) and as one batch.
3. The chat model (HANK_MODEL, else VISION_MODEL, else gemma4:12b): load time (unloaded first), then answers with the
   real Hank sheet (the exact messages the server would send for an eval case, addendum N0: at most 500 input tokens,
   num_predict 100, think false, num_thread 3): cold, warm with the same sheet, warm with another sheet.
4. RAM: `ollama ps` and `vm_stat` over ssh (--ssh), after each model is loaded.

Times: "wall" is measured here, on the calling machine, and includes the network path (through an SSH tunnel also the
LAN round trip); "server" is Ollama's own total_duration, load_duration, prompt_eval_duration and eval_duration. With
--ssh the server log of the cockpit (~/.vgrow-control/server.log) is checked before every model step; new ERROR lines
or a traceback stop the bench at once. The access key is never read, and log lines are never printed.

At the end both models are unloaded again (keep_alive 0) unless --keep-loaded, so the Mac is left as it was found
(with nothing of Hank loaded).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for extra in (REPO, REPO / "tests"):  # tests before scripts: tests/hank_eval is a package, scripts/hank_eval.py not
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
if str(REPO / "scripts") not in sys.path:
    sys.path.append(str(REPO / "scripts"))

import aiohttp  # noqa: E402
from hank_calibrate import isolated  # noqa: E402
from hank_eval import fixtures, report  # noqa: E402

from vgrow import config  # noqa: E402
from vgrow.hank import ollama, text  # noqa: E402
from vgrow.hank.core import Hank, shared_index  # noqa: E402
from vgrow.hank.kartei_index import DOC_BATCH, NOT_BY_DEFAULT  # noqa: E402

SERVER_LOG = "~/.vgrow-control/server.log"
CHAT_CASES = ("live-01", "grow-06")  # a live question (verdicts, state) and a card question (a nutrient row)


class Stop(Exception):
    """The Mac's server log showed an error: model work stops at once."""


# ---------- ssh ----------

class Mac:
    def __init__(self, host: str | None) -> None:
        self.host = host
        self.log_lines: int | None = None

    def run(self, command: str, timeout: float = 30.0) -> str | None:
        if not self.host:
            return None
        done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host, command],
                              capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return done.stdout if done.returncode == 0 else None

    def mark_log(self) -> None:
        out = self.run(f"wc -l < {SERVER_LOG}")
        self.log_lines = int(out.strip()) if out and out.strip().isdigit() else None

    def check_log(self) -> list[str]:
        """Kinds of new problem lines in the server log since mark_log (never the lines themselves)."""
        if not self.host or self.log_lines is None:
            return []
        out = self.run(f"tail -n +{self.log_lines + 1} {SERVER_LOG} | grep -E -c ' ERROR |Traceback' || true")
        count = int(out.strip()) if out and out.strip().isdigit() else 0
        return [f"{count} new ERROR/Traceback lines in the server log"] if count else []

    def guard(self) -> None:
        problems = self.check_log()
        if problems:
            raise Stop("; ".join(problems))

    def ollama_ps(self) -> str | None:
        return self.run("/usr/local/bin/ollama ps")

    def stop_model(self, model: str) -> bool:
        return self.run(f"/usr/local/bin/ollama stop {model}") is not None

    def vm_stat(self) -> dict[str, Any] | None:
        out = self.run("vm_stat; sysctl -n hw.memsize")
        if not out:
            return None
        lines = out.strip().splitlines()
        page = 4096
        if "page size of" in lines[0]:
            page = int(lines[0].split("page size of")[1].split()[0])
        pages: dict[str, int] = {}
        for line in lines[1:]:
            if ":" in line:
                name, _, value = line.partition(":")
                value = value.strip().rstrip(".")
                if value.isdigit():
                    pages[name.strip()] = int(value)
        total = int(lines[-1]) if lines[-1].strip().isdigit() else None

        def gb(*names: str) -> float:
            return round(sum(pages.get(n, 0) for n in names) * page / 1e9, 2)

        return {"total_gb": round(total / 1e9, 2) if total else None, "free_gb": gb("Pages free"),
                "active_gb": gb("Pages active"), "inactive_gb": gb("Pages inactive"),
                "wired_gb": gb("Pages wired down"), "compressed_gb": gb("Pages occupied by compressor"),
                "speculative_gb": gb("Pages speculative"), "swapouts": pages.get("Swapouts")}


# ---------- helpers ----------

def _s(ns: Any) -> float | None:
    return None if not isinstance(ns, int | float) else round(ns / 1e9, 3)


def stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {"n": len(values), "min": round(ordered[0], 3), "median": round(statistics.median(ordered), 3),
            "p95": round(p95, 3), "max": round(ordered[-1], 3)}


async def embed_raw(client: ollama.OllamaClient, model: str, texts: list[str], timeout: float,
                    keep_alive: Any = ollama.EMBED_KEEP_ALIVE) -> dict[str, Any]:
    body = {"model": model, "input": texts, "truncate": True, "keep_alive": keep_alive}
    t0 = time.monotonic()
    data = await client._request("POST", "/api/embed", body=body, model=model,
                                 timeout=aiohttp.ClientTimeout(total=timeout, connect=ollama.CHAT_CONNECT_S))
    wall = time.monotonic() - t0
    return {"wall_s": round(wall, 3), "server_s": _s(data.get("total_duration")), "load_s": _s(data.get("load_duration")),
            "prompt_eval_count": data.get("prompt_eval_count"), "n": len(texts)}


async def unload(client: ollama.OllamaClient, mac: Mac, model: str, *, embed: bool) -> None:
    """Unload a model: ollama stop over ssh when possible, else keep_alive 0 through the API."""
    if mac.stop_model(model):
        await asyncio.sleep(2)
        return
    if embed:
        await embed_raw(client, model, ["x"], ollama.WARM_TIMEOUT_S, keep_alive=0)
    else:
        await client.unload(model)
    await asyncio.sleep(2)


class _Recorder:
    """A stand-in model client: records the messages Hank would send and fails, so the draft path ends."""

    def __init__(self, model: str) -> None:
        self.chat_model = model
        self.embed_model = ollama.embed_model_name()
        self.lane = None
        self.messages: list[dict[str, Any]] | None = None

    async def chat(self, messages: list[dict[str, Any]], **_: Any) -> Any:
        self.messages = messages
        raise ollama.OllamaError("failed", "recorded only")

    async def close(self) -> None:
        return None


async def hank_messages(case_id: str, model: str) -> list[dict[str, Any]]:
    """The exact chat messages Hank's server would send for an eval case (built from code, no model)."""
    case = next(c for c in report.load_cases() if c["id"] == case_id)
    exp = report.load_expected()[case_id]
    with isolated("hank-bench-"):
        live = fixtures.build(exp["fixture"], lang=case["lang"], ledger=exp.get("ledger")).sources()
        recorder = _Recorder(model)
        hank = Hank(live=lambda _lang: dict(live), client=recorder, model=True, index=shared_index())  # type: ignore[arg-type]
        asked = await hank.ask_full(case["question"], lang=case["lang"], model=True)
        if asked.job is not None and asked.job.task is not None:
            await asked.job.task
    if recorder.messages is None:
        raise RuntimeError(f"{case_id} did not reach the model (route {asked.route.name})")
    return recorder.messages


def chat_row(result: ollama.ChatResult, label: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    est = sum(text.estimate_tokens(m["content"]) for m in messages)
    gen = result.eval_count / result.eval_s if result.eval_count and result.eval_s else None
    read = result.prompt_eval_count / result.prompt_eval_s if result.prompt_eval_count and result.prompt_eval_s else None
    return {"label": label, "wall_s": result.wall_s, "server_total_s": result.total_s, "load_s": result.load_s,
            "prompt_eval_count": result.prompt_eval_count, "prompt_eval_s": result.prompt_eval_s,
            "eval_count": result.eval_count, "eval_s": result.eval_s,
            "read_tok_s": round(read, 2) if read else None, "write_tok_s": round(gen, 2) if gen else None,
            "estimated_input_tokens": est, "done_reason": result.done_reason, "think_leak": result.think_leak,
            "answer_chars": len(result.content)}


# ---------- the bench ----------

async def bench(args: argparse.Namespace) -> dict[str, Any]:
    mac = Mac(args.ssh)
    url = args.url.rstrip("/")
    out: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "url": url, "via": args.via,
                           "note": "wall_s is measured on the calling machine and includes the network path; "
                                   "server_s and the *_s fields of Ollama are measured on the Mac"}
    embed_model = args.embed_model or ollama.embed_model_name()
    chat_model = args.chat_model or ollama.model_name()
    async with aiohttp.ClientSession() as session:
        client = ollama.OllamaClient(session, url)
        out["version"] = (await client.version()).get("version")
        out["models_present"] = sorted(m.get("name") for m in (await client.tags()).get("models", []))
        mac.mark_log()
        out["ram_before"] = {"ollama_ps": mac.ollama_ps(), "vm_stat": mac.vm_stat()}

        # ----- embedding -----
        questions = [c["question"] for c in report.load_cases()]
        out["embed"] = {"model": embed_model}
        cold = []
        for _ in range(args.cold_runs):
            mac.guard()
            await unload(client, mac, embed_model, embed=True)
            cold.append(await embed_raw(client, embed_model, [questions[0]], ollama.WARM_TIMEOUT_S))
        out["embed"]["cold"] = cold
        warm = []
        for q in questions:
            mac.guard()
            warm.append(await embed_raw(client, embed_model, [q], 30.0))
        out["embed"]["warm_per_question"] = {"wall": stats([w["wall_s"] for w in warm]),
                                             "server": stats([w["server_s"] for w in warm if w["server_s"]]),
                                             "over_0_8_s_wall": sum(1 for w in warm if w["wall_s"] > 0.8),
                                             "over_0_8_s_server": sum(1 for w in warm
                                                                      if (w["server_s"] or 0) > 0.8)}
        index = shared_index()
        passages = [d.embed_text for d in index._state.card_docs if d.passage.kind not in NOT_BY_DEFAULT]
        batches = []
        t0 = time.monotonic()
        for start in range(0, len(passages), DOC_BATCH):
            mac.guard()
            batches.append(await embed_raw(client, embed_model, passages[start:start + DOC_BATCH], 600.0))
        out["embed"]["passages"] = {"count": len(passages), "batch": DOC_BATCH,
                                    "batches_wall_s": round(time.monotonic() - t0, 2),
                                    "batches_server_s": round(sum(b["server_s"] or 0 for b in batches), 2),
                                    "tokens": sum(b["prompt_eval_count"] or 0 for b in batches)}
        mac.guard()
        one = await embed_raw(client, embed_model, passages, 600.0)
        out["embed"]["passages"]["one_batch"] = one
        out["ram_embed_loaded"] = {"ollama_ps": mac.ollama_ps(), "vm_stat": mac.vm_stat()}

        # ----- chat -----
        if not args.skip_chat:
            sheets = {cid: await hank_messages(cid, chat_model) for cid in CHAT_CASES}
            out["chat"] = {"model": chat_model, "options": dict(ollama.CHAT_OPTIONS), "think": False,
                           "runs": []}
            mac.guard()
            await unload(client, mac, chat_model, embed=False)
            first, second = CHAT_CASES
            plan = [(f"cold, {first}", first), (f"warm, same sheet {first}", first),
                    (f"warm, other sheet {second}", second)]
            for label, cid in plan:
                mac.guard()
                result = await client.chat(sheets[cid], model=chat_model)
                row = chat_row(result, label, sheets[cid])
                out["chat"]["runs"].append(row)
                print(f"chat {label}: wall {row['wall_s']} s, load {row['load_s']} s, prompt "
                      f"{row['prompt_eval_count']} tok in {row['prompt_eval_s']} s, gen {row['eval_count']} tok in "
                      f"{row['eval_s']} s", flush=True)
                if (result.wall_s or 0) > ollama.CHAT_TOTAL_S:
                    break
            out["ram_both_loaded"] = {"ollama_ps": mac.ollama_ps(), "vm_stat": mac.vm_stat()}
        out["log_problems"] = mac.check_log()
        if not args.keep_loaded:
            await unload(client, mac, embed_model, embed=True)
            if not args.skip_chat:
                await unload(client, mac, chat_model, embed=False)
            out["ram_after"] = {"ollama_ps": mac.ollama_ps()}
    return out


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hank bench on the Mac (spec 10.4 WP11)")
    parser.add_argument("--url", default=None, help="Ollama URL (default OLLAMA_URL, else http://127.0.0.1:11434)")
    parser.add_argument("--ssh", default=None, help="user@host of the Mac, for ollama ps, vm_stat, the log check "
                                                    "and ollama stop")
    parser.add_argument("--via", default="", help="free text for the report, e.g. 'SSH tunnel from the PC'")
    parser.add_argument("--embed-model", default=None)
    parser.add_argument("--chat-model", default=None)
    parser.add_argument("--cold-runs", type=int, default=3)
    parser.add_argument("--skip-chat", action="store_true")
    parser.add_argument("--keep-loaded", action="store_true")
    parser.add_argument("--out", default=None, help="JSON file (default STATE_DIR/hank/eval/bench-<time>.json)")
    args = parser.parse_args(argv)
    args.url = args.url or ollama.ollama_url()
    return args


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    out_file = Path(args.out) if args.out else config.STATE_DIR / "hank" / "eval" / \
        f"bench-{time.strftime('%Y-%m-%dT%H-%M-%S')}.json"
    try:
        result = asyncio.run(bench(args))
        code = 0
    except Stop as err:
        result = {"stopped": str(err)}
        code = 4
    config.atomic_write(out_file, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"saved: {out_file}")
    return code


if __name__ == "__main__":
    sys.exit(main())
