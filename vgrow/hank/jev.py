"""Jev beside the router, watch only (owner, 28.10.2026: "quick decisions, Hank handles the output").

Every question Hank routes also goes to TypeSafe's Jev, a hosted decision model (api.typesafe.ai, hosted in the US).
Jev judges four things about the message: plants, the grow box, a part of the app, the rules for growing at home.
This module writes the four probabilities next to the router's route as one line of STATE_DIR/hank/jev-watch.jsonl.
It changes no answer: the call runs as a side task after the router has decided, and a failure is a logged error,
never a "no" (TypeSafe skill: judgments from Jev, decisions in code).

Off without TYPESAFE_API_KEY in ~/.vgrow.env, read per question, so adding the key needs no restart. The wording is
the one measured on 28.10.2026 (branch claude/jev-frontdoor, experiments/jev-frontdoor: 0 of 147 growing questions
refused, 1 of 388 unseen off-topic sentences let through). Change it only with a new measurement on new questions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp

from vgrow import config

_LOGGER = logging.getLogger(__name__)

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
YES, NO = 0.7, 0.3  # the skill's starting thresholds; between them the question is unsettled
BUDGET_S = 0.8  # Hank's time limit for the front-door step (addendum N2): logged in watch mode, not enforced
TIMEOUT_S = 5.0  # watch only: wait past the budget, so the log shows the real time
WHERE = "Typed into the chat of Hank, the helper inside an app that runs a small grow box at home."
JUDGMENTS: dict[str, dict[str, Any]] = {
    "plants": {"type": "noul", "instructions": "Is `message` about plants or growing them?", "criteria": {
        "true": "The message asks about a plant, its seeds or seedlings, soil, watering, feeding, light, pests, "
                "diseases or harvest.",
        "false": "The message is about something else and no plant is involved."}},
    "grow_box": {"type": "noul", "instructions": "Is `message` about the grow box or one of its devices?", "criteria": {
        "true": "The message asks about the box, its lamp, fans, pump, humidifier, sensors, camera or the climate "
                "inside it.",
        "false": "The message does not concern the box or its equipment."}},
    "app_feature": {"type": "noul", "instructions": "Does `message` ask how a part of the app works?", "criteria": {
        "true": "The message asks what a screen, card, button, setting or message of the app means or how to use it.",
        "false": "The message asks something else, for example a personal question to the helper."}},
    "grow_law": {"type": "noul", "instructions": "Does `message` ask about the rules for growing plants at home?",
                 "criteria": {
        "true": "The message asks whether growing at home is allowed, or about the limits and rules for it.",
        "false": "The message asks about something other than the rules for growing at home."}},
}


def watch_file() -> Path:
    return config.STATE_DIR / "hank" / "jev-watch.jsonl"


def api_key() -> str | None:
    """The key from ~/.vgrow.env only: the process environment is not read, so a test run never calls the API."""
    try:
        return config.read_env(config.ENV_FILE).get("TYPESAFE_API_KEY") or None
    except (OSError, UnicodeDecodeError):
        return None


def payload(question: str) -> dict[str, Any]:
    return {"model": MODEL, "state": {"message": question, "where": WHERE}, "questions": JUDGMENTS}


def verdict(p: float) -> str:
    return "yes" if p >= YES else "no" if p < NO else "unsettled"


async def judge(question: str, key: str, *, url: str = URL, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """One request: the four probabilities, the highest one and its verdict, the model, the seconds. Raises on any
    failure; the caller logs it."""
    started = time.perf_counter()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(url, json=payload(question), headers={"Authorization": f"Bearer {key}"}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            body = await resp.json()
    seconds = time.perf_counter() - started
    p = {name: float(body["answers"][name]["noul"]) for name in JUDGMENTS}
    top = max(p.values())
    return {"p": {name: round(value, 4) for name, value in p.items()}, "p_on_topic": round(top, 4),
            "verdict": verdict(top), "model": body.get("model"), "seconds": round(seconds, 3),
            "within_budget": seconds <= BUDGET_S}


async def watch(question: str, *, lang: str, route: str, url: str = URL) -> None:
    """The side task: ask Jev, append one line. Never raises; a failure is a line with "error"."""
    key = api_key()
    if not key or not question:
        return
    row: dict[str, Any] = {"at": round(time.time(), 3), "lang": lang, "question": question, "route": route}
    try:
        row.update(await judge(question, key, url=url))
    except asyncio.CancelledError:
        raise
    except Exception as err:  # noqa: BLE001 - a failed call is a log line, never an answer
        row["error"] = type(err).__name__ + (f": {err}" if str(err) else "")
    try:
        path = watch_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as err:
        _LOGGER.warning("Jev watch: line not written (%s)", err)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The watch week in one view (scripts/jev_watch_report.py): where Jev and the router agree, every question where
    they part, the unsettled ones, the time against the budget. The router "refuses" with the route offtopic only;
    every other route counts as answered, and the lists carry the route, so the owner judges no_data and clarify."""
    judged = [r for r in rows if "verdict" in r]

    def item(r: dict[str, Any]) -> dict[str, Any]:
        return {"question": r["question"], "route": r["route"], "p_on_topic": r["p_on_topic"]}

    refused = [r for r in judged if r["route"] == "offtopic"]
    answered = [r for r in judged if r["route"] != "offtopic"]
    seconds = sorted(r["seconds"] for r in judged)
    return {
        "questions": len(rows), "judged": len(judged), "errors": sum("error" in r for r in rows),
        "agree": sum(r["verdict"] == "no" for r in refused) + sum(r["verdict"] == "yes" for r in answered),
        "jev_would_refuse_what_the_router_answered": [item(r) for r in answered if r["verdict"] == "no"],
        "jev_would_answer_what_the_router_refused": [item(r) for r in refused if r["verdict"] == "yes"],
        "unsettled": [item(r) for r in judged if r["verdict"] == "unsettled"],
        "seconds_median": seconds[len(seconds) // 2] if seconds else None,
        "over_budget": sum(not r.get("within_budget", False) for r in judged),
    }
