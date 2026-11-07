"""Hank's only network connection: the local Ollama at OLLAMA_URL (spec 0.3.5, 4.7, 4.9; addendum N0, N2).

Four parts:
- OllamaClient(session, url): chat, embed, tags, ps, version, plus warm_embedder and unload. Every request carries its
  own short limits, so a hanging Ollama never holds up the cockpit.
- classify_error / explain: one of four kinds (no_ollama, no_model, timeout, failed), like vision._explain, but with
  Hank's own catalog keys hank.error.<kind> (the texts live in texts.py, WP5).
- ModelLane: a lock with an owner name, so only one model job runs at a time. A chat answer never waits behind the
  photo reading; the photo reading waits behind a running chat answer. It is handed in through constructors, never a
  module global (asyncio objects belong to one event loop, and the tests start many).
- Health: what Ollama has (version, models present, models loaded), cached for 30 s.

Nothing here switches anything on the box, and nothing here reads or writes state files. The model names and the URL
are read from ~/.vgrow.env at call time (config.ENV_FILE), never at import time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from vgrow import config

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_CHAT_MODEL = "gemma4:12b"  # spec 4.7, roadmap decision
DEFAULT_EMBED_MODEL = "granite-embedding:278m"  # spec 2.3, decision D1

# Chat request (spec 4.7 with addendum N0.3: num_predict 100, num_thread 3, think false).
CHAT_KEEP_ALIVE = "10m"
CHAT_OPTIONS: Mapping[str, Any] = {
    "temperature": 0.2,
    "num_ctx": 4096,  # equal to the photo reading, so switching between them does not force a reload (spec 4.7)
    "num_predict": 100,  # addendum N0.3 (the spec said 140)
    "num_thread": 3,  # addendum N0.3: one core stays free for the control server
}
CHAT_CONNECT_S = 3.0  # spec 4.7: connection 3 s
CHAT_TOTAL_S = 240.0  # spec 4.7: total 240 s, then the draft with the text "slow"

# Embedding request (spec 2.7: batch, truncate true; addendum N2: question embedding 0.8 s, warm with keep_alive -1).
# keep_alive is -1 on every embed call, not only on the warm-up: Ollama takes keep_alive from each request, so a later
# call with "30m" (the spec 2.7 value) would undo the warm-up and let the embedder go cold again, which is the dense
# flicker the addendum fixes (critique e3).
EMBED_KEEP_ALIVE = -1
EMBED_QUERY_TIMEOUT_S = 0.8  # addendum N2 (the spec said 1.5 s)
WARM_TIMEOUT_S = CHAT_TOTAL_S  # loading or unloading a model; the spec names no own limit, so the chat limit
PROBE_TIMEOUT_S = CHAT_CONNECT_S  # version, tags, ps: no own limit in the spec, so the connection limit
HEALTH_TTL_S = 30.0  # spec 10.4 WP2

# Prefixes per embedding model (spec 2.3): {model: (query_prefix, doc_prefix)}. Unknown models get none.
EMBED_PREFIXES: Mapping[str, tuple[str, str]] = {
    "granite-embedding:278m": ("", ""),
    "bge-m3": ("", ""),
    "nomic-embed-text-v2-moe": ("search_query: ", "search_document: "),
}
EMBED_KINDS = ("query", "doc")

ERROR_KINDS = ("no_ollama", "no_model", "timeout", "failed")

# Owners of the lane, and who gives way to whom: a chat never waits behind the photo (spec 4.9).
OWNERS = ("chat", "photo", "eval", "bench")
GIVES_WAY_TO: Mapping[str, tuple[str, ...]] = {"chat": ("photo",)}


# ---------- names and settings, read at call time ----------

def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return config.read_env(config.ENV_FILE) if env is None else env


def model_name(env: Mapping[str, str] | None = None) -> str:
    """The one model name for chat and photo (spec 4.7, 4.9): HANK_MODEL, else VISION_MODEL, else gemma4:12b."""
    values = _env(env)
    return (values.get("HANK_MODEL") or "").strip() or (values.get("VISION_MODEL") or "").strip() or DEFAULT_CHAT_MODEL


def embed_model_name(env: Mapping[str, str] | None = None) -> str:
    """The embedding model (spec 2.3): HANK_EMBED_MODEL, else granite-embedding:278m."""
    return (_env(env).get("HANK_EMBED_MODEL") or "").strip() or DEFAULT_EMBED_MODEL


def ollama_url(env: Mapping[str, str] | None = None) -> str:
    """OLLAMA_URL, else http://127.0.0.1:11434, without a trailing slash."""
    return ((_env(env).get("OLLAMA_URL") or "").strip() or DEFAULT_URL).rstrip("/")


def embed_prefix(model: str, kind: str) -> str:
    query, doc = EMBED_PREFIXES.get(model, ("", ""))
    return query if kind == "query" else doc


def pull_command(model: str) -> str:
    return f"ollama pull {model}"


def _full_name(name: str) -> str:
    """Ollama lists a model without a tag as name:latest."""
    return name if ":" in name else f"{name}:latest"


# ---------- errors ----------

class OllamaError(Exception):
    """A failed Ollama call, already classified. kind is one of ERROR_KINDS; status is the HTTP status or None."""

    def __init__(self, kind: str, message: str, *, status: int | None = None, model: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.model = model


class LaneBusy(Exception):
    """The lane is taken and the caller must not wait (a chat behind the photo, a call with wait=False, or a question
    embedding while a model job runs). holder is the owner that has the lane, or the one queued that blocks it."""

    def __init__(self, holder: str | None) -> None:
        super().__init__(f"model lane busy: {holder}")
        self.holder = holder


def _kind_from_text(text: str, status: int | None = None) -> str:
    low = text.lower()
    if "cannot connect" in low or "connection refused" in low or "connect call failed" in low:
        return "no_ollama"
    if "not found" in low and "model" in low:
        return "no_model"
    if status == 404 and "not found" in low:
        return "no_model"
    if "timeout" in low or "timed out" in low:
        return "timeout"
    return "failed"


def classify_error(err: BaseException) -> str:
    """One of no_ollama, no_model, timeout, failed (the classes of vision._explain)."""
    if isinstance(err, OllamaError):
        return err.kind
    if isinstance(err, TimeoutError):  # asyncio.TimeoutError and aiohttp's timeout errors are TimeoutError
        return "timeout"
    if isinstance(err, aiohttp.ClientConnectionError | ConnectionError):
        return "no_ollama"
    return _kind_from_text(str(err))


def explain(err: BaseException, model: str | None = None) -> tuple[str, dict[str, Any]]:
    """Catalog key and values for an Ollama error: hank.error.<kind>. The texts are WP5's (texts.py); the values here
    are `model` always and `reason` for failed, so a text may use either and extra values do no harm (i18n.t)."""
    kind = classify_error(err)
    name = model or (err.model if isinstance(err, OllamaError) else None) or model_name()
    params: dict[str, Any] = {"model": name}
    if kind == "failed":
        params["reason"] = str(err)[:160]
    return f"hank.error.{kind}", params


# ---------- the lane ----------

@dataclass
class _Waiter:
    owner: str
    future: asyncio.Future[None]


class ModelLane:
    """One model job at a time, with the owner's name.

    hold(owner) waits in arrival order. Two exceptions, both raising LaneBusy at once instead of waiting:
    - an owner that gives way (GIVES_WAY_TO: chat gives way to photo) when the lane is held by, or already promised
      to, an owner it gives way to. So a chat never waits behind the photo, and a photo queued behind a running chat
      waits for it;
    - wait=False when the lane is taken or someone is queued (the eval run, spec 11.1: lowest priority, gives up).

    Plain Python state plus futures made on the running loop, so one lane object works in any event loop.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._holder: str | None = None
        self._since: float | None = None
        self._waiters: deque[_Waiter] = deque()

    @property
    def holder(self) -> str | None:
        return self._holder

    @property
    def since(self) -> float | None:
        return self._since

    @property
    def busy(self) -> bool:
        return self._holder is not None

    @property
    def waiting(self) -> tuple[str, ...]:
        return tuple(w.owner for w in self._waiters if not w.future.done())

    def state(self) -> dict[str, Any]:
        return {"holder": self._holder, "since": self._since, "waiting": list(self.waiting)}

    def _blocker(self, owner: str) -> str | None:
        give_way = GIVES_WAY_TO.get(owner, ())
        if self._holder in give_way:
            return self._holder
        for waiter in self.waiting:
            if waiter in give_way:
                return waiter
        return None

    def _take(self, owner: str) -> None:
        self._holder = owner
        self._since = self._clock()

    def try_acquire(self, owner: str) -> bool:
        """Take the lane only if it is free and nobody is queued. Pair with release()."""
        if self._holder is not None or self.waiting:
            return False
        self._take(owner)
        return True

    async def acquire(self, owner: str, *, wait: bool = True) -> None:
        if not owner:
            raise ValueError("the lane needs an owner name")
        if self._holder is None and not self.waiting:
            self._take(owner)
            return
        blocker = self._blocker(owner)
        if blocker is not None:
            raise LaneBusy(blocker)
        if not wait:
            raise LaneBusy(self._holder or (self.waiting[0] if self.waiting else None))
        waiter = _Waiter(owner, asyncio.get_running_loop().create_future())
        self._waiters.append(waiter)
        try:
            await waiter.future
        except asyncio.CancelledError:
            if waiter.future.done() and not waiter.future.cancelled() and self._holder == owner:
                self.release()  # handed over in the same moment as the cancel: pass it on
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
            raise

    def release(self) -> None:
        if self._holder is None:
            raise RuntimeError("release of a free model lane")
        self._holder = None
        self._since = None
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.future.done():  # cancelled while waiting
                continue
            self._take(waiter.owner)
            waiter.future.set_result(None)
            return

    @asynccontextmanager
    async def hold(self, owner: str, *, wait: bool = True) -> AsyncIterator[ModelLane]:
        await self.acquire(owner, wait=wait)
        try:
            yield self
        finally:
            self.release()


# ---------- results ----------

def _seconds(ns: Any) -> float | None:
    """Ollama's nanoseconds as seconds; None when the field is missing or not a number."""
    if isinstance(ns, bool) or not isinstance(ns, int | float):
        return None
    return round(ns / 1e9, 3)


@dataclass(frozen=True)
class ChatResult:
    """One chat answer with Ollama's counters (spec 4.7). Durations in seconds, None when Ollama did not say."""

    content: str
    model: str
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    load_s: float | None = None
    prompt_eval_s: float | None = None
    eval_s: float | None = None
    total_s: float | None = None
    wall_s: float = 0.0
    done_reason: str | None = None
    think_leak: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def cold(self) -> bool | None:
        """True when the model had to be loaded first (load_duration over one second). None when unknown."""
        return None if self.load_s is None else self.load_s >= 1.0

    def turn(self) -> dict[str, Any]:
        """The fields for turns.jsonl (spec 4.7, 8.5): counters and times, never the question or the answer text."""
        return {"model": self.model, "prompt_eval_count": self.prompt_eval_count, "eval_count": self.eval_count,
                "load_s": self.load_s, "prompt_eval_s": self.prompt_eval_s, "eval_s": self.eval_s,
                "total_s": self.total_s, "wall_s": self.wall_s, "done_reason": self.done_reason,
                "think_leak": self.think_leak}


# ---------- the client ----------

class OllamaClient:
    """Talks to OLLAMA_URL and nothing else.

    session: an aiohttp.ClientSession the caller owns (serve() makes one). None makes the client open its own on first
    use; close() then closes it. lane: optional ModelLane; when given, a question embedding is skipped (LaneBusy)
    while any model job holds the lane (spec 2.3, 4.9). The chat does not take the lane itself: the caller holds it
    around chat() with hold("chat"), so it can answer with the draft at once when the photo has it.
    """

    def __init__(self, session: aiohttp.ClientSession | None = None, url: str | None = None, *,
                 lane: ModelLane | None = None, env: Mapping[str, str] | None = None) -> None:
        self._session = session
        self._own_session = session is None
        self._url = url.rstrip("/") if url else None
        self._env = env
        self.lane = lane

    @property
    def url(self) -> str:
        return self._url or ollama_url(self._env)

    @property
    def chat_model(self) -> str:
        return model_name(self._env)

    @property
    def embed_model(self) -> str:
        return embed_model_name(self._env)

    async def close(self) -> None:
        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()
        if self._own_session:
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or (self._own_session and self._session.closed):
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def _request(self, method: str, path: str, *, body: dict[str, Any] | None, timeout: aiohttp.ClientTimeout,
                       model: str | None = None) -> dict[str, Any]:
        """One call, every failure turned into an OllamaError with its kind."""
        session = self._get_session()
        try:
            async with session.request(method, f"{self.url}{path}", json=body, timeout=timeout) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = None
                if resp.status != 200:
                    text = (data.get("error") if isinstance(data, dict) else None) or f"HTTP {resp.status}"
                    raise OllamaError(_kind_from_text(str(text), resp.status), str(text), status=resp.status,
                                      model=model)
                if not isinstance(data, dict):
                    raise OllamaError("failed", f"unreadable answer from {path}", status=resp.status, model=model)
                return data
        except OllamaError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:  # connection refused, dropped, timed out: one of the four kinds
            raise OllamaError(classify_error(err), str(err) or type(err).__name__, model=model) from err

    # ----- chat -----

    def chat_body(self, messages: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
        """The request body of spec 4.7 with addendum N0: no format, no tools, think false, stream false."""
        return {
            "model": model or self.chat_model,
            "messages": list(messages),
            "stream": False,
            "think": False,  # mandatory: without it a photo took 462 instead of 182 s (roadmap); default varies [R18]
            "keep_alive": CHAT_KEEP_ALIVE,
            "options": dict(CHAT_OPTIONS),
        }

    async def chat(self, messages: list[dict[str, Any]], *, model: str | None = None,
                   timeout: float = CHAT_TOTAL_S) -> ChatResult:
        name = model or self.chat_model
        body = self.chat_body(messages, name)
        started = time.monotonic()
        data = await self._request("POST", "/api/chat", body=body, model=name,
                                   timeout=aiohttp.ClientTimeout(total=timeout, connect=min(CHAT_CONNECT_S, timeout)))
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        thinking = message.get("thinking")
        leak = bool(thinking)
        if leak:  # spec 4.7: filled despite think false, so dropped and logged
            _LOGGER.warning("think_leak: model %s returned %d characters of thinking despite think false; dropped",
                            name, len(str(thinking)))
        content = message.get("content")
        return ChatResult(
            content=content if isinstance(content, str) else "",
            model=str(data.get("model") or name),
            prompt_eval_count=data.get("prompt_eval_count"),
            eval_count=data.get("eval_count"),
            load_s=_seconds(data.get("load_duration")),
            prompt_eval_s=_seconds(data.get("prompt_eval_duration")),
            eval_s=_seconds(data.get("eval_duration")),
            total_s=_seconds(data.get("total_duration")),
            wall_s=round(time.monotonic() - started, 3),
            done_reason=data.get("done_reason"),
            think_leak=leak,
            raw={k: v for k, v in data.items() if k != "message"},
        )

    async def unload(self, model: str | None = None) -> bool:
        """Unload the chat model at once (mode "off", spec 4.7): an empty chat with keep_alive 0 [R21]. Never raises."""
        name = model or self.chat_model
        try:
            await self._request("POST", "/api/chat", body={"model": name, "messages": [], "keep_alive": 0},
                                model=name, timeout=aiohttp.ClientTimeout(total=WARM_TIMEOUT_S, connect=CHAT_CONNECT_S))
        except OllamaError as err:
            _LOGGER.info("Unloading %s failed (%s): %s", name, err.kind, err)
            return False
        return True

    # ----- embeddings -----

    async def embed(self, texts: list[str], *, kind: str = "query", timeout: float | None = None,
                    model: str | None = None) -> list[list[float]]:
        """The Embedder of spec appendix A. kind "query" (a question) or "doc" (passages for the index).

        A question embedding defaults to the 0.8 s limit of addendum N2 and is skipped with LaneBusy while a model job
        holds the lane. A doc batch needs an explicit timeout: the spec gives none for the index build.
        """
        if kind not in EMBED_KINDS:
            raise ValueError(f"unknown embedding kind {kind!r}, known: {', '.join(EMBED_KINDS)}")
        if timeout is None:
            if kind != "query":
                raise ValueError("a doc embedding needs an explicit timeout")
            timeout = EMBED_QUERY_TIMEOUT_S
        if kind == "query" and self.lane is not None and self.lane.busy:
            raise LaneBusy(self.lane.holder)
        if not texts:
            return []
        name = model or self.embed_model
        prefix = embed_prefix(name, kind)
        body = {"model": name, "input": [prefix + str(t) for t in texts], "truncate": True,
                "keep_alive": EMBED_KEEP_ALIVE}
        data = await self._request("POST", "/api/embed", body=body, model=name,
                                   timeout=aiohttp.ClientTimeout(total=timeout, connect=min(CHAT_CONNECT_S, timeout)))
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts) or not all(
                isinstance(v, list) and v for v in vectors):
            raise OllamaError("failed", f"embed answered {len(vectors) if isinstance(vectors, list) else 'no'} "
                                        f"vectors for {len(texts)} texts", model=name)
        return [[float(x) for x in v] for v in vectors]

    async def warm_embedder(self, model: str | None = None, *, timeout: float = WARM_TIMEOUT_S) -> bool:
        """Load the embedding model at start and keep it loaded (addendum N2: keep_alive -1). Never raises."""
        name = model or self.embed_model
        body = {"model": name, "input": ["warm"], "truncate": True, "keep_alive": EMBED_KEEP_ALIVE}
        try:
            await self._request("POST", "/api/embed", body=body, model=name,
                                timeout=aiohttp.ClientTimeout(total=timeout, connect=CHAT_CONNECT_S))
        except OllamaError as err:
            _LOGGER.info("Warming the embedder %s failed (%s): %s", name, err.kind, err)
            return False
        return True

    # ----- probes -----

    def _probe_timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S, connect=CHAT_CONNECT_S)

    async def tags(self) -> dict[str, Any]:
        return await self._request("GET", "/api/tags", body=None, timeout=self._probe_timeout())

    async def ps(self) -> dict[str, Any]:
        return await self._request("GET", "/api/ps", body=None, timeout=self._probe_timeout())

    async def version(self) -> dict[str, Any]:
        return await self._request("GET", "/api/version", body=None, timeout=self._probe_timeout())


# ---------- health ----------

def _names(listing: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in listing.get("models") or []:
        if isinstance(item, dict):
            for key in ("name", "model"):
                if item.get(key):
                    names.add(_full_name(str(item[key])))
    return names


class Health:
    """What Ollama has, cached for 30 s (spec 10.4 WP2), for GET /api/hank/state and the choice draft or model.

    get() answers
    {"ollama": "ok" | no_ollama | timeout | failed, "version": str | None, "checked": epoch seconds,
     "chat_model": {"name", "state": "ok" | "missing" | "unknown", "loaded": bool | None, "pull": command | None},
     "embed": {"model", "state": "ok" | "missing" | "unknown", "loaded": bool | None, "pull": command | None}}.
    "unknown" means Ollama did not answer, so nothing can be said about the model. Concurrent callers share one probe.
    """

    def __init__(self, client: OllamaClient, *, ttl: float = HEALTH_TTL_S,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.client = client
        self.ttl = ttl
        self._clock = clock
        self._wall = wall
        self._value: dict[str, Any] | None = None
        self._at: float | None = None
        self._running: asyncio.Task[dict[str, Any]] | None = None

    @property
    def cached(self) -> dict[str, Any] | None:
        """The last result without probing (None before the first probe)."""
        return self._value

    def invalidate(self) -> None:
        self._at = None

    async def get(self, *, force: bool = False) -> dict[str, Any]:
        if not force and self._value is not None and self._at is not None and self._clock() - self._at < self.ttl:
            return self._value
        running = self._running
        if running is not None and not running.done() and running.get_loop() is asyncio.get_running_loop():
            return await asyncio.shield(running)
        task = asyncio.ensure_future(self._probe())
        self._running = task
        try:
            value = await asyncio.shield(task)
        finally:
            if self._running is task and task.done():
                self._running = None
        self._value, self._at = value, self._clock()
        return value

    async def _probe(self) -> dict[str, Any]:
        chat, embed = self.client.chat_model, self.client.embed_model
        result: dict[str, Any] = {"ollama": "ok", "version": None, "checked": self._wall()}
        try:
            result["version"] = (await self.client.version()).get("version")
            present = _names(await self.client.tags())
        except OllamaError as err:
            result["ollama"] = err.kind
            for key, name in (("chat_model", chat), ("embed", embed)):
                result[key] = {"name" if key == "chat_model" else "model": name, "state": "unknown",
                               "loaded": None, "pull": None}
            return result
        try:
            loaded: set[str] | None = _names(await self.client.ps())
        except OllamaError as err:
            _LOGGER.info("Ollama ps failed (%s): %s", err.kind, err)
            loaded = None
        for key, name in (("chat_model", chat), ("embed", embed)):
            ok = _full_name(name) in present
            result[key] = {"name" if key == "chat_model" else "model": name, "state": "ok" if ok else "missing",
                           "loaded": None if loaded is None else _full_name(name) in loaded,
                           "pull": None if ok else pull_command(name)}
        return result
