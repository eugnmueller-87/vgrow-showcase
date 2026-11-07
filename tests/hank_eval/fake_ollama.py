"""A fake Ollama for the offline eval and the tests (spec 10.4 WP0): chat, embed, tags, ps and version.

Two ways to use it. In process: await fake.chat(body), fake.embed(texts, kind=..., timeout=...) (the Embedder shape of
spec Anhang A), fake.tags(), fake.ps(), fake.version(). Over HTTP: fake.app() is an aiohttp application with the
Ollama paths (/api/chat, /api/embed, /api/tags, /api/ps, /api/version), for TestServer or a real port.

Every call is recorded in fake.requests (endpoint, body, time), so a test can assert what was sent and that a fixed
reply never reached the model. Delays (fake.delay[endpoint] = seconds) and failures (fake.fail[endpoint] = kind) are
per endpoint. Failure kinds follow the four classes of spec 10.4 WP2: "no_ollama" (connection refused), "no_model"
(model not found, the text vision._explain recognises), "timeout" and "failed" (HTTP 500). A model that is not in
fake.models fails as no_model on its own.

Embeddings are deterministic hash vectors: the same model and text always give the same unit vector, different texts
give unrelated ones. They carry no meaning; they only make the dense channel's plumbing testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import struct
import time
from collections.abc import Callable
from typing import Any

from aiohttp import web

CHAT_MODEL = "gemma4:12b"  # spec 4.7 default
EMBED_MODEL = "granite-embedding:278m"  # spec 2.3 default
READ_TOKENS_PER_S = 12.6  # spec 0.2 item 6, the slower of the two measurements; used only for fake durations
WRITE_TOKENS_PER_S = 3.6
FAILURES = ("no_ollama", "no_model", "timeout", "failed")


class FakeOllamaError(Exception):
    """A failure the fake raises in process. status is the HTTP status the app answers with (None: no answer)."""

    def __init__(self, kind: str, message: str, status: int | None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


def hash_vector(text: str, *, model: str = EMBED_MODEL, dim: int = 768) -> list[float]:
    """A deterministic unit vector for (model, text)."""
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(f"{model}\0{counter}\0{text}".encode()).digest()
        for (number,) in struct.iter_unpack(">I", digest):
            values.append(number / 2**31 - 1.0)
        counter += 1
    values = values[:dim]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _tokens(text: str) -> int:
    return max(1, len(text) // 3)  # spec 0.4: a token is about 3 characters


class FakeOllama:
    def __init__(self, *, models: tuple[str, ...] = (CHAT_MODEL, EMBED_MODEL),
                 reply: str | Callable[[dict[str, Any]], str] = "Weiß ich nicht.",
                 thinking: str | None = None, dim: int = 768, version: str = "0.0.0-fake",
                 clock: Callable[[], float] = time.time) -> None:
        self.models = list(models)
        self.reply = reply
        self.thinking = thinking  # set to simulate a think leak (spec 4.7: filled despite think false)
        self.dim = dim
        self.version_text = version
        self.clock = clock
        self.requests: list[dict[str, Any]] = []
        self.delay: dict[str, float] = {}
        self.fail: dict[str, str] = {}
        self.loaded: dict[str, Any] = {}  # model -> keep_alive of its last use

    # ---------- bookkeeping ----------

    def calls(self, endpoint: str | None = None) -> list[dict[str, Any]]:
        """The recorded requests, optionally only those of one endpoint ("chat", "embed", "tags", "ps", "version")."""
        return [r for r in self.requests if endpoint is None or r["endpoint"] == endpoint]

    def reset(self) -> None:
        self.requests.clear()

    async def _enter(self, endpoint: str, body: dict[str, Any] | None) -> None:
        self.requests.append({"endpoint": endpoint, "body": body, "t": self.clock()})
        wait = self.delay.get(endpoint)
        if wait:
            await asyncio.sleep(wait)
        kind = self.fail.get(endpoint)
        if kind:
            raise self._error(kind, (body or {}).get("model") or "")

    @staticmethod
    def _error(kind: str, model: str) -> FakeOllamaError:
        if kind == "no_ollama":
            return FakeOllamaError(kind, "Cannot connect to host 127.0.0.1:11434 ssl:default [Connect call failed]",
                                   None)
        if kind == "no_model":
            return FakeOllamaError(kind, f'model "{model}" not found, try pulling it first', 404)
        if kind == "timeout":
            return FakeOllamaError(kind, "timeout", None)
        if kind == "failed":
            return FakeOllamaError(kind, "fake failure", 500)
        raise ValueError(f"unknown failure kind {kind!r}, known: {', '.join(FAILURES)}")

    def _need_model(self, model: str) -> None:
        if model not in self.models:
            raise self._error("no_model", model)

    def _keep(self, model: str, keep_alive: Any) -> None:
        if keep_alive in (0, "0", "0s", "0m"):
            self.loaded.pop(model, None)
        else:
            self.loaded[model] = keep_alive

    # ---------- the API, in process ----------

    async def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/chat with stream false: the whole answer at once, with Ollama's counters."""
        await self._enter("chat", body)
        model = str(body.get("model") or "")
        self._need_model(model)
        messages = body.get("messages") or []
        cold = model not in self.loaded
        self._keep(model, body.get("keep_alive"))
        if not messages:  # an empty chat only loads or unloads the model
            return {"model": model, "message": {"role": "assistant", "content": ""}, "done": True,
                    "done_reason": "unload" if model not in self.loaded else "load"}
        content = self.reply(body) if callable(self.reply) else self.reply
        prompt = "".join(str(m.get("content") or "") for m in messages)
        read, written = _tokens(prompt), _tokens(content)
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if self.thinking is not None:
            message["thinking"] = self.thinking
        load_ns = 21_000_000_000 if cold else 0  # spec 4.8: loading takes 21 to 44 s
        read_ns = int(read / READ_TOKENS_PER_S * 1e9)
        write_ns = int(written / WRITE_TOKENS_PER_S * 1e9)
        return {"model": model, "message": message, "done": True, "done_reason": "stop",
                "total_duration": load_ns + read_ns + write_ns, "load_duration": load_ns,
                "prompt_eval_count": read, "prompt_eval_duration": read_ns,
                "eval_count": written, "eval_duration": write_ns}

    async def embed_raw(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/embed: {"model", "input": text or list, "truncate", "keep_alive"} -> {"model", "embeddings"}."""
        await self._enter("embed", body)
        model = str(body.get("model") or "")
        self._need_model(model)
        texts = body.get("input")
        texts = [texts] if isinstance(texts, str) else list(texts or [])
        self._keep(model, body.get("keep_alive"))
        vectors = [hash_vector(str(t), model=model, dim=self.dim) for t in texts]
        return {"model": model, "embeddings": vectors, "prompt_eval_count": sum(_tokens(str(t)) for t in texts)}

    async def embed(self, texts: list[str], *, kind: str = "query", timeout: float | None = None,
                    model: str = EMBED_MODEL) -> list[list[float]]:
        """The Embedder of spec Anhang A. kind ("query" or "doc") is recorded, timeout is honoured."""
        body = {"model": model, "input": list(texts), "truncate": True, "keep_alive": "30m", "kind": kind}
        call = self.embed_raw(body)
        data = await (asyncio.wait_for(call, timeout) if timeout is not None else call)
        return data["embeddings"]

    async def tags(self) -> dict[str, Any]:
        await self._enter("tags", None)
        return {"models": [{"name": m, "model": m, "size": 0, "details": {}} for m in self.models]}

    async def ps(self) -> dict[str, Any]:
        await self._enter("ps", None)
        return {"models": [{"name": m, "model": m, "size": 0, "size_vram": 0, "keep_alive": k}
                           for m, k in self.loaded.items()]}

    async def version(self) -> dict[str, Any]:
        await self._enter("version", None)
        return {"version": self.version_text}

    # ---------- the API, over HTTP ----------

    def app(self) -> web.Application:
        """An aiohttp application with Ollama's paths. A failure answers with its status and {"error": text}; the
        kinds without an answer (no_ollama, timeout) close the connection without a response."""

        def handler(call: Callable[..., Any], with_body: bool) -> Callable[[web.Request], Any]:
            async def handle(request: web.Request) -> web.StreamResponse:
                body = await request.json() if with_body else None
                try:
                    data = await (call(body) if with_body else call())
                except FakeOllamaError as err:
                    if err.status is None:
                        if request.transport is not None:
                            request.transport.close()
                        raise web.HTTPServiceUnavailable(text=str(err)) from err
                    return web.json_response({"error": str(err)}, status=err.status)
                return web.json_response(data)

            return handle

        app = web.Application()
        app.router.add_post("/api/chat", handler(self.chat, True))
        app.router.add_post("/api/embed", handler(self.embed_raw, True))
        app.router.add_get("/api/tags", handler(self.tags, False))
        app.router.add_get("/api/ps", handler(self.ps, False))
        app.router.add_get("/api/version", handler(self.version, False))
        return app
