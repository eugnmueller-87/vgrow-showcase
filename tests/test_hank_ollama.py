"""Hank's Ollama access and model lane (docs/hank-v1-spec.md 4.7, 4.9, 10.4 WP2; addendum N0, N2).

The fake Ollama is an aiohttp test server (tests/hank_eval/fake_ollama.py, FakeOllama.app()), so every request goes
over real HTTP and the bodies, limits and error paths are the ones Ollama would see.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from hank_eval.fake_ollama import CHAT_MODEL, EMBED_MODEL, FakeOllama
from vgrow import config
from vgrow.hank import ollama
from vgrow.hank.ollama import (Health, LaneBusy, ModelLane, OllamaClient, OllamaError, classify_error, explain,
                               model_name)

EM_DASH = chr(0x2014)
MESSAGES = [{"role": "system", "content": "Du bist Hank."}, {"role": "user", "content": "FRAGE: \"Feuchte?\""}]


async def _serve(fake: FakeOllama) -> TestServer:
    server = TestServer(fake.app())
    await server.start_server()
    return server


def _run(fake: FakeOllama, body, *, lane: ModelLane | None = None):  # type: ignore[no-untyped-def]
    """Run body(client) against the fake over HTTP and return its result."""

    async def go():  # type: ignore[no-untyped-def]
        server = await _serve(fake)
        try:
            async with aiohttp.ClientSession() as session:
                client = OllamaClient(session, str(server.make_url("")).rstrip("/"), lane=lane, env={})
                return await body(client)
        finally:
            await server.close()

    return asyncio.run(go())


# ---------- the chat body ----------

def test_chat_body_has_think_false_keep_alive_and_options() -> None:
    fake = FakeOllama(reply="Die Feuchte ist hoch [S1].")

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        return await client.chat(MESSAGES)

    result = _run(fake, body)
    sent = fake.calls("chat")[0]["body"]
    assert sent["model"] == CHAT_MODEL
    assert sent["think"] is False and sent["stream"] is False
    assert sent["keep_alive"] == "10m"
    assert sent["options"] == {"temperature": 0.2, "num_ctx": 4096, "num_predict": 100, "num_thread": 3}
    assert sent["messages"] == MESSAGES
    assert result.content == "Die Feuchte ist hoch [S1]."
    assert result.prompt_eval_count and result.eval_count
    assert result.load_s is not None and result.load_s > 0 and result.cold is True  # first call loads the model
    assert result.prompt_eval_s is not None and result.eval_s is not None and result.wall_s >= 0
    turn = result.turn()
    assert "Feuchte" not in str(turn)  # spec 8.5: turns.jsonl never holds question or answer text
    assert set(turn) >= {"prompt_eval_count", "eval_count", "load_s", "prompt_eval_s", "eval_s", "think_leak"}


def test_chat_has_no_format_and_no_tools() -> None:
    fake = FakeOllama()

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        return await client.chat(MESSAGES)

    _run(fake, body)
    sent = fake.calls("chat")[0]["body"]
    assert "format" not in sent and "tools" not in sent
    assert set(sent) == {"model", "messages", "stream", "think", "keep_alive", "options"}


def test_chat_drops_leaked_thinking(caplog) -> None:  # type: ignore[no-untyped-def]
    fake = FakeOllama(reply="Weiß ich nicht.", thinking="Ich denke lange nach.")

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        return await client.chat(MESSAGES)

    with caplog.at_level("WARNING", logger="vgrow.hank.ollama"):
        result = _run(fake, body)
    assert result.content == "Weiß ich nicht." and result.think_leak is True
    assert "think_leak" in caplog.text and "Ich denke" not in caplog.text and "Ich denke" not in str(result.turn())


def test_unload_sends_empty_chat_with_keep_alive_zero() -> None:
    fake = FakeOllama()

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        await client.chat(MESSAGES)
        assert CHAT_MODEL in fake.loaded
        ok = await client.unload()
        fake.fail["chat"] = "failed"
        failed = await client.unload()  # never raises: switching Hank off must not fail on Ollama
        return ok, failed

    assert _run(fake, body) == (True, False)
    assert fake.calls("chat")[1]["body"] == {"model": CHAT_MODEL, "messages": [], "keep_alive": 0}
    assert CHAT_MODEL not in fake.loaded


# ---------- embeddings ----------

def test_embed_uses_truncate_and_keep_alive() -> None:
    fake = FakeOllama()

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        warm = await client.warm_embedder()
        query = await client.embed(["Luftfeuchte"], kind="query")
        docs = await client.embed(["Luftfeuchte", "Licht"], kind="doc", timeout=5.0)
        empty = await client.embed([], kind="doc", timeout=5.0)
        return warm, query, docs, empty

    warm, query, docs, empty = _run(fake, body)
    assert warm is True and empty == []
    assert len(query) == 1 and len(docs) == 2 and len(docs[0]) == 768
    assert query[0] == docs[0]  # granite has no prefixes, so query and doc of one text are the same vector
    calls = [c["body"] for c in fake.calls("embed")]
    assert len(calls) == 3  # the empty batch never reached Ollama
    for sent in calls:
        assert sent["model"] == EMBED_MODEL and sent["truncate"] is True
        assert sent["keep_alive"] == -1  # addendum N2: warmed with -1 and never let go cold by a later call
    assert calls[0]["input"] == ["warm"]
    assert calls[2]["input"] == ["Luftfeuchte", "Licht"]
    assert fake.loaded[EMBED_MODEL] == -1


def test_embed_prefixes_follow_the_model_table() -> None:
    nomic = "nomic-embed-text-v2-moe"
    fake = FakeOllama(models=(CHAT_MODEL, nomic))

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        await client.embed(["Licht"], kind="query", model=nomic)
        await client.embed(["Licht"], kind="doc", timeout=5.0, model=nomic)

    _run(fake, body)
    inputs = [c["body"]["input"] for c in fake.calls("embed")]
    assert inputs == [["search_query: Licht"], ["search_document: Licht"]]


def test_embed_rejects_unknown_kind_and_doc_without_limit() -> None:
    client = OllamaClient(None, "http://127.0.0.1:1", env={})

    async def go() -> None:
        with pytest.raises(ValueError):
            await client.embed(["x"], kind="passage")
        with pytest.raises(ValueError):
            await client.embed(["x"], kind="doc")
        await client.close()

    asyncio.run(go())


def test_question_embedding_is_skipped_while_the_lane_is_held() -> None:
    fake = FakeOllama()
    lane = ModelLane()

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        async with lane.hold("photo"):
            with pytest.raises(LaneBusy) as busy:
                await client.embed(["Feuchte"], kind="query")
            assert busy.value.holder == "photo"
            docs = await client.embed(["Feuchte"], kind="doc", timeout=5.0)  # the index build is not a question
        async with lane.hold("chat"):
            with pytest.raises(LaneBusy):
                await client.embed(["Feuchte"], kind="query")
        return docs, await client.embed(["Feuchte"], kind="query")

    docs, query = _run(fake, body, lane=lane)
    assert docs == query and len(fake.calls("embed")) == 2


# ---------- limits and errors ----------

def test_timeouts_are_short_for_embed_and_long_for_chat() -> None:
    assert ollama.EMBED_QUERY_TIMEOUT_S == 0.8  # addendum N2
    assert ollama.CHAT_CONNECT_S == 3.0 and ollama.CHAT_TOTAL_S == 240.0  # spec 4.7
    fake = FakeOllama(reply="Weiß ich nicht.")
    fake.delay["embed"] = 1.0
    fake.delay["chat"] = 1.0

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(OllamaError) as slow:
            await client.embed(["Feuchte"], kind="query")
        embed_s = loop.time() - started
        answer = await client.chat(MESSAGES)  # a second is nothing for a chat answer
        return slow.value.kind, embed_s, answer.content

    kind, embed_s, content = _run(fake, body)
    assert kind == "timeout" and embed_s < 1.0
    assert content == "Weiß ich nicht."


def test_error_classification_four_kinds() -> None:
    fake = FakeOllama(models=(EMBED_MODEL,))  # the chat model is not pulled

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        kinds = []
        for setup in ({}, {"chat": "failed"}, {"chat": "no_ollama"}):
            fake.fail.clear()
            fake.fail.update(setup)
            try:
                await client.chat(MESSAGES)
            except OllamaError as err:
                kinds.append((err.kind, err.status))
        fake.fail.clear()
        fake.delay["chat"] = 0.5
        try:
            await client.chat(MESSAGES, timeout=0.1)
        except OllamaError as err:
            kinds.append((err.kind, err.status))
        return kinds

    kinds = _run(fake, body)
    assert kinds == [("no_model", 404), ("failed", 500), ("no_ollama", None), ("timeout", None)]

    assert classify_error(ConnectionRefusedError("[Errno 61] Connect call failed ('127.0.0.1', 11434)")) == "no_ollama"
    assert classify_error(asyncio.TimeoutError()) == "timeout"
    assert classify_error(RuntimeError('model "gemma4:12b" not found, try pulling it first')) == "no_model"
    assert classify_error(RuntimeError("HTTP 500")) == "failed"
    key, params = explain(OllamaError("no_model", "model not found", model="gemma4:12b"))
    assert key == "hank.error.no_model" and params["model"] == "gemma4:12b"
    key, params = explain(RuntimeError("HTTP 500"), "gemma4:12b")
    assert key == "hank.error.failed" and params["reason"] == "HTTP 500"
    assert {explain(OllamaError(k, k))[0] for k in ollama.ERROR_KINDS} == {
        "hank.error.no_ollama", "hank.error.no_model", "hank.error.timeout", "hank.error.failed"}


def test_unreachable_ollama_is_no_ollama() -> None:
    client = OllamaClient(None, "http://127.0.0.1:9", env={})

    async def go() -> str:
        try:
            await client.version()
        except OllamaError as err:
            return err.kind
        finally:
            await client.close()
        return "answered"

    assert asyncio.run(go()) in ("no_ollama", "timeout")  # refused at once on macOS; Windows retries for about 2 s


# ---------- the lane ----------

def test_lane_chat_does_not_wait_behind_photo() -> None:
    async def go() -> None:
        lane = ModelLane()
        async with lane.hold("photo"):
            with pytest.raises(LaneBusy) as busy:
                await asyncio.wait_for(lane.acquire("chat"), 0.5)  # raises at once, never hits the 0.5 s limit
            assert busy.value.holder == "photo"
        # A photo already queued behind a running chat blocks a new chat too: it would otherwise wait behind it.
        async with lane.hold("chat"):
            photo = asyncio.ensure_future(lane.acquire("photo"))
            await asyncio.sleep(0)
            assert lane.waiting == ("photo",)
            with pytest.raises(LaneBusy) as queued:
                await lane.acquire("chat")
            assert queued.value.holder == "photo"
        await photo
        assert lane.holder == "photo"
        lane.release()
        assert lane.holder is None

    asyncio.run(go())


def test_lane_photo_waits_behind_chat() -> None:
    order: list[str] = []

    async def go() -> None:
        lane = ModelLane()
        chat_in = asyncio.Event()
        chat_go = asyncio.Event()

        async def chat() -> None:
            async with lane.hold("chat"):
                order.append("chat start")
                chat_in.set()
                await chat_go.wait()
                order.append("chat end")

        async def photo() -> None:
            await chat_in.wait()
            async with lane.hold("photo"):
                order.append("photo")

        tasks = [asyncio.ensure_future(chat()), asyncio.ensure_future(photo())]
        await chat_in.wait()
        await asyncio.sleep(0.01)
        assert lane.holder == "chat" and lane.waiting == ("photo",)
        chat_go.set()
        await asyncio.gather(*tasks)
        assert lane.holder is None and lane.waiting == ()

    asyncio.run(go())
    assert order == ["chat start", "chat end", "photo"]


def test_lane_reports_holder() -> None:
    now = [1000.0]

    async def go() -> None:
        lane = ModelLane(clock=lambda: now[0])
        assert lane.state() == {"holder": None, "since": None, "waiting": []} and not lane.busy
        async with lane.hold("chat"):
            assert lane.holder == "chat" and lane.since == 1000.0 and lane.busy
            now[0] = 1005.0
            waiter = asyncio.ensure_future(lane.acquire("photo"))
            await asyncio.sleep(0)
            assert lane.state() == {"holder": "chat", "since": 1000.0, "waiting": ["photo"]}
            with pytest.raises(LaneBusy) as low:  # the eval run has the lowest priority and never waits
                await lane.acquire("eval", wait=False)
            assert low.value.holder == "chat"
        await waiter
        assert lane.holder == "photo" and lane.since == 1005.0
        lane.release()
        assert lane.try_acquire("eval") and lane.holder == "eval"
        assert not lane.try_acquire("chat")
        lane.release()
        with pytest.raises(RuntimeError):
            lane.release()

    asyncio.run(go())


def test_lane_cancelled_waiter_leaves_the_queue() -> None:
    async def go() -> None:
        lane = ModelLane()
        await lane.acquire("chat")
        photo = asyncio.ensure_future(lane.acquire("photo"))
        bench = asyncio.ensure_future(lane.acquire("bench"))
        await asyncio.sleep(0)
        photo.cancel()
        with pytest.raises(asyncio.CancelledError):
            await photo
        assert lane.waiting == ("bench",)
        lane.release()
        await bench
        assert lane.holder == "bench"
        lane.release()

    asyncio.run(go())


def test_lane_works_across_event_loops() -> None:
    lane = ModelLane()

    async def once() -> str | None:
        async with lane.hold("chat"):
            return lane.holder

    assert asyncio.run(once()) == "chat"
    assert asyncio.run(once()) == "chat" and lane.holder is None


# ---------- health ----------

def test_health_cache_30s() -> None:
    fake = FakeOllama(models=(CHAT_MODEL,), version="0.12.6")  # the embedder is not pulled
    now = [0.0]

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        health = Health(client, clock=lambda: now[0], wall=lambda: 1_761_000_000.0)
        first = await health.get()
        now[0] = 29.0
        again = await health.get()
        calls_after_cache = len(fake.requests)
        now[0] = 31.0
        fake.fail["version"] = "no_ollama"
        later = await health.get()
        fake.fail.clear()
        before = len(fake.calls("version"))
        both = await asyncio.gather(health.get(force=True), health.get(force=True))
        return first, again, calls_after_cache, later, (both, len(fake.calls("version")) - before)

    first, again, calls_after_cache, later, both = _run(fake, body)
    assert first is again and calls_after_cache == 3  # version, tags, ps once; the second get came from the cache
    assert first["ollama"] == "ok" and first["version"] == "0.12.6" and first["checked"] == 1_761_000_000.0
    assert first["chat_model"] == {"name": CHAT_MODEL, "state": "ok", "loaded": False, "pull": None}
    assert first["embed"] == {"model": EMBED_MODEL, "state": "missing", "loaded": False,
                              "pull": "ollama pull granite-embedding:278m"}
    assert later["ollama"] == "no_ollama" and later["embed"]["state"] == "unknown"
    both, probes = both
    assert both[0] is both[1] and probes == 1  # two callers at once share one probe
    assert both[0]["ollama"] == "ok"


def test_health_sees_loaded_models_and_untagged_names() -> None:
    fake = FakeOllama(models=("gemma4", EMBED_MODEL))  # listed without a tag means :latest

    async def body(client: OllamaClient):  # type: ignore[no-untyped-def]
        await client.warm_embedder()
        return await Health(client).get()

    fake_env = {"HANK_MODEL": "gemma4:latest"}

    async def go():  # type: ignore[no-untyped-def]
        server = await _serve(fake)
        try:
            async with aiohttp.ClientSession() as session:
                client = OllamaClient(session, str(server.make_url("")).rstrip("/"), env=fake_env)
                return await body(client)
        finally:
            await server.close()

    state = asyncio.run(go())
    assert state["chat_model"]["state"] == "ok" and state["chat_model"]["loaded"] is False
    assert state["embed"]["state"] == "ok" and state["embed"]["loaded"] is True


# ---------- names ----------

def test_model_name_resolution_order(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert model_name({}) == "gemma4:12b"
    assert model_name({"VISION_MODEL": "gemma3:12b"}) == "gemma3:12b"
    assert model_name({"VISION_MODEL": "gemma3:12b", "HANK_MODEL": "qwen3:4b"}) == "qwen3:4b"
    assert model_name({"HANK_MODEL": "  ", "VISION_MODEL": "gemma3:12b"}) == "gemma3:12b"
    assert ollama.embed_model_name({}) == "granite-embedding:278m"
    assert ollama.embed_model_name({"HANK_EMBED_MODEL": "bge-m3"}) == "bge-m3"
    assert ollama.ollama_url({}) == "http://127.0.0.1:11434"
    assert ollama.ollama_url({"OLLAMA_URL": "http://10.0.0.5:11434/"}) == "http://10.0.0.5:11434"

    env_file = tmp_path / "vgrow.env"
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    assert model_name() == "gemma4:12b"
    env_file.write_text("VISION_MODEL=gemma3:12b\n", encoding="utf-8")
    assert model_name() == "gemma3:12b"  # read at call time, not at import time
    env_file.write_text("VISION_MODEL=gemma3:12b\nHANK_MODEL=\"qwen3:4b\"\nHANK_EMBED_MODEL=bge-m3\n"
                        "OLLAMA_URL=http://127.0.0.1:11500\n", encoding="utf-8")
    client = OllamaClient(None)
    assert (model_name(), client.chat_model, client.embed_model, client.url) == (
        "qwen3:4b", "qwen3:4b", "bge-m3", "http://127.0.0.1:11500")


def test_ollama_module_has_no_em_dash_and_no_actuator_calls() -> None:
    source = (Path(__file__).resolve().parent.parent / "vgrow" / "hank" / "ollama.py").read_text(encoding="utf-8")
    assert EM_DASH not in source
    assert not source.startswith("\ufeff")
    for name in ("_send(", "refresh_shadow", "/api/cmd", "plant_apply", "plant_stage"):
        assert name not in source
    assert not re.search(r"\.set_(?!result\b)\w+\(", source)  # no set_light, set_port and the like
