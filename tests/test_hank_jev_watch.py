"""Jev beside the router, watch only (vgrow/hank/jev.py).

What must hold: without TYPESAFE_API_KEY in ~/.vgrow.env nothing is sent; one question gives one line with the four
judgments and the router's route, and the key never reaches the log; a failed call is a logged error, never a "no";
Hank's answer is the same with and without the watch and never waits for it. A fake TypeSafe server (aiohttp
TestServer) stands in for api.typesafe.ai.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hank_eval import fixtures
from vgrow import config
from vgrow.hank import core, jev

KEY = "ts-test-key-0001"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
OFFTOPIC_Q = "Wie wird das Wetter morgen?"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fixtures.freeze(monkeypatch)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".vgrow.env")
    saved = config.STATE_DIR
    config.use_state_dir(tmp_path / "state")
    yield tmp_path
    config.use_state_dir(saved)


def with_key(tmp_path: Path) -> None:
    (tmp_path / ".vgrow.env").write_text(f"VGROW_NTFY_TOPIC=x\nTYPESAFE_API_KEY={KEY}\n", encoding="utf-8")


def f22(lang: str) -> dict[str, Any]:
    sources = fixtures.build("F22", lang=lang).sources()
    sources.pop("ledger")
    return sources


class FakeJev:
    def __init__(self, *, status: int = 200, p: dict[str, float] | None = None) -> None:
        self.status = status
        self.p = p or {"plants": 0.97, "grow_box": 0.12, "app_feature": 0.03, "grow_law": 0.02}
        self.calls: list[dict[str, Any]] = []

    def app(self) -> web.Application:
        async def handle(request: web.Request) -> web.Response:
            self.calls.append({"auth": request.headers.get("Authorization"), "body": await request.json()})
            if self.status != 200:
                return web.json_response({"error": "boom"}, status=self.status)
            return web.json_response({"model": "jev-1.14.0", "usage": {"input_tokens": 600},
                                      "answers": {k: {"type": "noul", "noul": v} for k, v in self.p.items()}})

        app = web.Application()
        app.router.add_post("/v1/systemone", handle)
        return app


def watch_with(fake: FakeJev, question: str, route: str) -> None:
    async def go() -> None:
        server = TestServer(fake.app())
        await server.start_server()
        try:
            await jev.watch(question, lang="de", route=route, url=str(server.make_url("/v1/systemone")))
        finally:
            await server.close()

    asyncio.run(go())


def lines() -> list[dict[str, Any]]:
    path = jev.watch_file()
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_without_a_key_nothing_is_sent_and_nothing_written() -> None:
    fake = FakeJev()
    watch_with(fake, "Wie oft gieße ich Basilikum?", "grow")
    assert fake.calls == [] and lines() == []


def test_one_line_with_the_four_judgments_and_the_route_and_never_the_key(isolated: Path) -> None:
    with_key(isolated)
    fake = FakeJev()
    watch_with(fake, "Wie oft gieße ich Basilikum?", "grow")
    (call,) = fake.calls
    assert call["auth"] == f"Bearer {KEY}"
    assert call["body"]["state"] == {"message": "Wie oft gieße ich Basilikum?", "where": jev.WHERE}
    assert call["body"]["questions"] == jev.JUDGMENTS and call["body"]["model"] == jev.MODEL
    (row,) = lines()
    assert row["route"] == "grow" and row["lang"] == "de" and row["question"] == "Wie oft gieße ich Basilikum?"
    assert row["verdict"] == "yes" and row["p_on_topic"] == 0.97 and set(row["p"]) == set(jev.JUDGMENTS)
    assert row["model"] == "jev-1.14.0" and row["within_budget"] is True and "error" not in row
    assert KEY not in jev.watch_file().read_text(encoding="utf-8")


def test_a_failed_call_is_a_logged_error_never_a_no(isolated: Path) -> None:
    with_key(isolated)
    watch_with(FakeJev(status=500), OFFTOPIC_Q, "offtopic")
    (row,) = lines()
    assert row["error"] == "RuntimeError: HTTP 500"
    assert "verdict" not in row and "p" not in row and "p_on_topic" not in row


def test_unreachable_jev_is_a_logged_error(isolated: Path) -> None:
    with_key(isolated)
    asyncio.run(jev.watch(OFFTOPIC_Q, lang="de", route="offtopic", url="http://127.0.0.1:9/v1/systemone"))
    (row,) = lines()
    assert row["error"] and "verdict" not in row


def test_thresholds_leave_a_band_between_yes_and_no() -> None:
    assert [jev.verdict(p) for p in (0.7, 0.69, 0.3, 0.29)] == ["yes", "unsettled", "unsettled", "no"]


def test_the_answer_is_the_same_and_never_waits_for_jev(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str]] = []

    async def slow_watch(question: str, *, lang: str, route: str, url: str = jev.URL) -> None:
        seen.append((question, lang, route))
        await asyncio.sleep(5)

    async def go() -> tuple[dict[str, Any], dict[str, Any], float]:
        hank = core.Hank(live=f22, index=core.shared_index())
        try:
            plain = await hank.ask(OFFTOPIC_Q, client_id="c1", lang="de")
            monkeypatch.setattr(jev, "watch", slow_watch)
            started = time.perf_counter()
            watched = await hank.ask(OFFTOPIC_Q, client_id="c2", lang="de")
            seconds = time.perf_counter() - started
            await asyncio.sleep(0.05)  # the answer is out; the side task gets its first turn and is still running
            return plain, watched, seconds
        finally:
            await hank.stop()  # cancels the slow side task: nothing is left running

    plain, watched, seconds = asyncio.run(go())
    assert seconds < 2  # the 5 s watch never holds the answer
    assert plain["done"] is True and watched["done"] is True
    assert (plain["answer"]["route"], plain["answer"]["text"]) == (watched["answer"]["route"], watched["answer"]["text"])
    assert seen == [(OFFTOPIC_Q, "de", watched["answer"]["route"])]


def test_summary_lists_every_disagreement_with_its_route() -> None:
    rows = [
        {"question": "a", "route": "grow", "verdict": "yes", "p_on_topic": 0.9, "seconds": 0.2, "within_budget": True},
        {"question": "b", "route": "offtopic", "verdict": "no", "p_on_topic": 0.1, "seconds": 0.3, "within_budget": True},
        {"question": "c", "route": "grow", "verdict": "no", "p_on_topic": 0.1, "seconds": 0.9, "within_budget": False},
        {"question": "d", "route": "offtopic", "verdict": "yes", "p_on_topic": 0.8, "seconds": 0.2, "within_budget": True},
        {"question": "e", "route": "no_data", "verdict": "unsettled", "p_on_topic": 0.5, "seconds": 0.2,
         "within_budget": True},
        {"question": "f", "route": "grow", "error": "TimeoutError"},
    ]
    s = jev.summarize(rows)
    assert (s["questions"], s["judged"], s["errors"], s["agree"], s["over_budget"]) == (6, 5, 1, 2, 1)
    assert s["jev_would_refuse_what_the_router_answered"] == [{"question": "c", "route": "grow", "p_on_topic": 0.1}]
    assert s["jev_would_answer_what_the_router_refused"] == [{"question": "d", "route": "offtopic", "p_on_topic": 0.8}]
    assert [u["question"] for u in s["unsettled"]] == ["e"]


def test_no_em_dash_in_the_watch_files() -> None:
    repo = Path(__file__).resolve().parent.parent
    for path in (repo / "vgrow" / "hank" / "jev.py", Path(__file__), repo / "scripts" / "jev_watch_report.py"):
        assert EM_DASH not in path.read_text(encoding="utf-8"), path
