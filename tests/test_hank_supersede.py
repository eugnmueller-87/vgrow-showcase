"""Hank, WP11 follow-up of 23.10.2026: a new question in the same chat cancels the polish still running for it.

WP11 measured 45 to 83 s per polish on the Mac; while one held the model lane, every new question skipped the
embedding (spec 2.3) and went lexical, where unseen everyday questions get through. Now the new question cancels its
chat's polish first: the HTTP request to Ollama is closed (so Ollama stops writing), the earlier answer keeps its
draft, and the new question is routed with the embedding. Another chat's polish and the photo reading are left alone.
Only a question that reaches the search cancels (router.aroute's before_dense): "Danke!" or a greeting never use the
embedding, so they leave the polish running. A polish cancelled before its first step still ends as done (review of
23.10.2026: it used to stay open, and the page polled it forever).

The model is a real HTTP server (aiohttp TestServer) whose /api/chat takes long and notes when the client goes away,
so the test sees the connection being closed, not only the task being cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hank_eval import fixtures
from vgrow.hank import core, ollama

GROW_Q = "Ist die Luftfeuchte gerade okay?"  # grow on F22, gets a polish job
NEXT_Q = "Welche Werte brauche ich in der frühen Blüte?"


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def f22(lang: str) -> dict[str, Any]:
    sources = fixtures.build("F22", lang=lang).sources()
    sources.pop("ledger")
    return sources


class SlowChat:
    """/api/chat that writes for `seconds` unless the client hangs up first (then `gone` is set)."""

    def __init__(self, seconds: float = 30.0) -> None:
        self.seconds = seconds
        self.started = asyncio.Event()
        self.gone = asyncio.Event()
        self.calls = 0

    async def chat(self, request: web.Request) -> web.Response:
        self.calls += 1
        await request.json()
        self.started.set()
        loop = asyncio.get_running_loop()
        end = loop.time() + self.seconds
        try:
            while loop.time() < end:
                transport = request.transport
                if transport is None or transport.is_closing():
                    self.gone.set()
                    break
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:  # aiohttp cancels the handler when the client closed the connection
            self.gone.set()
            raise
        return web.json_response({"model": "gemma4:12b", "message": {"role": "assistant", "content": "x"},
                                  "done": True})

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/chat", self.chat)
        return app


@contextlib.asynccontextmanager
async def hank_on(slow: SlowChat) -> AsyncIterator[core.Hank]:
    server = TestServer(slow.app())
    await server.start_server()
    client = ollama.OllamaClient(url=str(server.make_url("")).rstrip("/"))
    try:
        hank = core.Hank(live=f22, client=client, index=core.shared_index())
        seen: list[bool] = []
        hank.ensure_index()
        assert hank.router is not None
        aroute = hank.router.aroute

        async def watch(*args: Any, **kw: Any) -> Any:
            skip = kw.get("skip_dense")
            if not callable(skip):
                seen.append(bool(skip))
                return await aroute(*args, **kw)

            def recorded() -> bool:  # the lane's state when the embedding is asked for (after before_dense)
                value = bool(skip())
                seen.append(value)
                return value

            return await aroute(*args, **{**kw, "skip_dense": recorded})

        hank.router.aroute = watch  # type: ignore[method-assign]
        hank.skips = seen  # type: ignore[attr-defined]
        yield hank
    finally:
        await hank.stop()
        await client.close()
        await server.close()


def test_new_question_in_the_same_chat_cancels_the_polish() -> None:
    slow = SlowChat()

    async def go() -> None:
        async with hank_on(slow) as hank:
            first = await hank.ask(GROW_Q, client_id="chat-a")
            assert first["done"] is False
            await asyncio.wait_for(slow.started.wait(), 5)
            assert hank.lane.holder == "chat"
            second = await hank.ask(NEXT_Q, client_id="chat-a")
            # the request was closed, so Ollama stops writing
            await asyncio.wait_for(slow.gone.wait(), 5)
            job = hank.job(first["id"])
            assert job is not None and job["done"]
            assert job["answer"]["draft_only"] is True and job["answer"]["text"] == first["draft"]["text"]
            assert job["answer"]["note"] is None and hank.jobs[first["id"]].note == "superseded"
            # the second question was routed with the lane free: the embedding was asked for, not skipped
            assert hank.skips == [False, False]  # type: ignore[attr-defined]
            assert second["done"] is False and hank.jobs[second["id"]].client_id == "chat-a"
            await hank.cancel(second["id"])
            assert not hank.lane.busy

    asyncio.run(go())


def test_another_chat_and_the_photo_reading_are_left_alone() -> None:
    slow = SlowChat()

    async def go() -> None:
        async with hank_on(slow) as hank:
            first = await hank.ask(GROW_Q, client_id="chat-a")
            await asyncio.wait_for(slow.started.wait(), 5)
            other = await hank.ask(NEXT_Q, client_id="chat-b")  # another chat: the first polish runs on
            assert not hank.jobs[first["id"]].done and not slow.gone.is_set()
            assert hank.skips == [False, True]  # type: ignore[attr-defined]
            await hank.cancel(first["id"])
            await hank.cancel(other["id"])
            assert not hank.lane.busy
            # the daily photo reading holds the lane: never cancelled, the question goes lexical (documented)
            async with hank.lane.hold("photo"):
                data = await hank.ask(GROW_Q, client_id="chat-a")
                assert hank.lane.holder == "photo"
            assert data["done"] is True and data["answer"]["note"] is not None  # busy_photo: the draft at once
            assert hank.skips[-1] is True  # type: ignore[attr-defined]

    asyncio.run(go())


def test_a_waiting_polish_is_cancelled_too_and_no_client_id_cancels_nothing() -> None:
    slow = SlowChat()

    async def go() -> None:
        async with hank_on(slow) as hank:
            running = await hank.ask(GROW_Q, client_id="chat-b")
            await asyncio.wait_for(slow.started.wait(), 5)
            waiting = await hank.ask(GROW_Q, client_id="chat-a")  # queued behind chat-b's polish
            await asyncio.sleep(0.05)
            assert hank.lane.waiting == ("chat",)
            await hank.ask(NEXT_Q, client_id="chat-a")  # cancels chat-a's waiting polish, chat-b runs on
            assert hank.jobs[waiting["id"]].done and hank.jobs[waiting["id"]].answer.draft_only  # type: ignore[union-attr]
            assert not hank.jobs[running["id"]].done
            assert slow.calls == 1  # the waiting polish never reached the model
            before = {k for k, j in hank.jobs.items() if not j.done}
            await hank.ask(NEXT_Q)  # no client id (the CLI, the eval bridge): nothing is cancelled
            assert before <= {k for k, j in hank.jobs.items() if not j.done}

    asyncio.run(go())


def test_a_polish_cancelled_before_its_first_step_still_ends() -> None:
    """Two questions in a row with no yield in between (a double tap on send): the first polish task never ran."""
    slow = SlowChat()

    async def go() -> None:
        async with hank_on(slow) as hank:
            first = await hank.ask(GROW_Q, client_id="chat-a")
            second = await hank.ask(GROW_Q, client_id="chat-a")  # no await between: the first task never started
            job = hank.job(first["id"])
            assert job is not None and job["done"] is True
            assert job["answer"]["draft_only"] is True and job["answer"]["text"] == first["draft"]["text"]
            assert hank.jobs[first["id"]].note == "superseded"
            assert slow.calls <= 1  # the first polish never reached the model
            await hank.cancel(second["id"])
            assert hank.job(second["id"])["done"] is True  # type: ignore[index]
            # DELETE on a job whose task never started ends it the same way
            third = await hank.ask(NEXT_Q, client_id="chat-b")
            assert await hank.cancel(third["id"]) is True
            assert hank.job(third["id"])["done"] is True  # type: ignore[index]
            assert not hank.lane.busy

    asyncio.run(go())


def test_thanks_or_a_greeting_leave_the_polish_running_and_the_turn_log_names_superseded() -> None:
    slow = SlowChat()

    async def go() -> None:
        async with hank_on(slow) as hank:
            first = await hank.ask(GROW_Q, client_id="chat-a")
            await asyncio.wait_for(slow.started.wait(), 5)
            for social in ("Danke!", "Moin Hank"):
                data = await hank.ask(social, client_id="chat-a")
                assert data["done"] is True
            assert not hank.jobs[first["id"]].done and not slow.gone.is_set()
            await hank.ask(NEXT_Q, client_id="chat-a")  # a growing question: now the polish gives way
            await asyncio.wait_for(slow.gone.wait(), 5)
            assert hank.jobs[first["id"]].done
            turns = [t for t in hank.stores.turns.all() if t.get("answer_id") == hank.jobs[first["id"]].draft.id]
            assert turns and turns[-1]["note"] == "superseded"
            for job in list(hank.jobs.values()):
                await hank.cancel(job.id)

    asyncio.run(go())
