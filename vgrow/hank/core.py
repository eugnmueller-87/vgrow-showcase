"""Hank put together (spec 1, 4.9, 7, 8, 10.4 WP8; addendum N0): one object the server builds, without the box.

Hank(live=..., client=..., lane=..., demo=...) owns the index, the router, the stores, the ledger and the ticker.
It never gets the box: what it reads of the cockpit comes from `live`, a callable that returns the read-only moment
(wiring.Collector, spec 4.2). Nothing here switches anything. A suggestion card is data the page sends to /api/cmd
itself, with every guard of the cockpit.

An answer (addendum N0):
1. Router, search and draft run in code, at once (hard gate under 1 s). The draft is the answer, in Hank's voice:
   verdicts, card facts with numbered sources, the ledger's message, the card sentence.
2. The model polishes in the background, only in the mode "on", only with a model client, only while the lane is not
   held by the photo reading and the queue has room (one runs, at most two wait, spec 4.9). The page shows the draft
   at once and polls GET /api/hank/job/{id}. A version that passes the validator and keeps every core fact of the
   draft (compose.accept: the direct answer's number, unit and verdict word, the proposal line, the mandatory
   sentences, no new number) replaces the text (draft_only false); any other leaves the draft (draft_only true, the
   reason in turns.jsonl). Fixed answers never get a job and never call the model.
3. When the model is known to be unavailable before it is asked (photo reading, Ollama down or the model missing in
   the cached health, queue full), the draft comes as the final answer with the catalog note (spec 3.6: busy_photo,
   model_down, model_missing, queue_full); a polish that fails on the way ends the same way (slow, model_down,
   model_missing).
4. A new question in the same chat (client_id) that reaches the search cancels that chat's polish first
   (_supersede, WP11 follow-up; router.aroute's before_dense): the earlier answer keeps its draft and the new question
   gets the embedding. "Danke!", a greeting or a gate reply never use the embedding and leave the polish running. The route "today" is answered by
   today.py, from code only, like a fixed answer.
5. The route "fix" ("Kannst du das beheben?", a tap on the traffic-light line or a note's "Beheben" button; owner,
   23.10.2026) is answered by fix.py, from code only: the problems off target with their cards. The page sends the
   problem keys of what was tapped (ask(fix=[...])), so the answer covers exactly those; the cards go into the
   registry and the ledger like the cards of a growing answer. A typed wish with a pronoun ("can u fix it?") is
   narrowed by the router to the chat's last topic (Route.fix_keys, asking.fix_scope).
6. v1.2 (owner, 23.10.2026: "he has to realize a question and either he can help or not"): the routes "explain" (a
   glossary term: its text, then the live reading or "Die Box misst das nicht.") and "reading" (the plant's height
   with the day it was measured, a term the box does not measure) come from asking.py, code only. After every
   answer the chat's topic (metric, device, term, problems) goes into the router's TopicBook for follow-ups.

The ticker (spec 1, 6.2, addendum N4): every 60 s, code only: the automation's own switches into the journal, the
index refreshed when a card file changed, the live passages (addendum N2), the ledger's minute and the traffic light
(nudges.tick), new notes into the chat ring, the Ollama health. It hangs outside the poll chain, and every step
catches its own errors (spec 10.1 rule 7).

The mode (spec 7, addendum N8) lives in STATE_DIR/settings.json under hank_mode, the one exception to "state only in
STATE_DIR/hank/". eval_answer() is the bridge of the eval set (tests/hank_eval/report.py).

Importing this module registers both server catalogs (texts.py, texts_live.py) in i18n (spec 10.3).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from vgrow import config, i18n

from . import asking, compose, context, feedplan, livestate, methods, nudges, ollama, proposals, replies, stores, text
from . import strains as strains_mod
from . import shelves as shelves_mod
from . import texts
from . import jev, texts_live, typo, verdicts, wikicards
from . import fix as fix_mod
from . import humidifier_report, pilot_report, pump_report, thirst_report
from . import vpd as vpd_mod
from . import ledger as ledger_mod
from . import router as router_mod
from . import today as today_mod
from . import validator as validator_mod
from . import wiring
from .kartei_index import Index
from .types import Answer, Hit, Passage, Proposal, Route, SourceRef

texts.register()
texts_live.register()
today_mod.register()
fix_mod.register()
pilot_report.register()
pump_report.register()
thirst_report.register()
asking.register()
feedplan.register()
methods.register()
wikicards.register()
strains_mod.register()
shelves_mod.register()
vpd_mod.register()

_LOGGER = logging.getLogger(__name__)

MODES = ("on", "warnings", "off")  # spec 7
MODE_KEY = "hank_mode"  # spec 7: STATE_DIR/settings.json
TICK_SECONDS = 60.0  # spec 1: the ticker, every 60 s
QUEUE_WAITING = 2  # spec 4.9: one answer runs, at most two wait
JOB_KEEP_SECONDS = 15 * 60  # a finished job stays readable this long (the page polls every 1.5 s; spec is silent)
ANSWERS_KEPT = 200  # answers remembered for the thumbs (feedback.jsonl needs route, question, hits, text)
SAYING_EVERY = 5  # spec 4.5: a card saying at most in every fifth answer, never at red
INBOX_ITEMS = 20  # the newest notes GET /api/hank/state hands out (the page skips the ones it has)
DENSE_RETRY_SECONDS = 10 * 60  # a failed embedding build is tried again at most this often (spec is silent)
JOURNAL_ACTIONS = ("light", "spectrum", "cfan", "dfan", "port", "auto_humidifier", "plant_stage", "plant_apply",
                   "plant_bloom",  # plant_bloom moves the stage and the feeding week (review 2, 23.10.2026)
                   # feeding v2 (23.10.2026): the plant's start dates the plan (a Hank card can store it), the
                   # fertilizer switch decides which card doses
                   "plant_start", "feeding_setup")
YES = frozenset({"yes", "ja", "j", "y", "true", "1", "genau", "stimmt"})
NO = frozenset({"no", "nein", "n", "false", "0"})
NOTE_KEYS = {"busy_photo": "hank.reply.busy_photo", "model_down": "hank.reply.model_down",
             "model_missing": "hank.reply.model_missing", "slow": "hank.reply.slow",
             "queue_full": "hank.reply.queue_full"}
STAGES = ("model", "check", "done")  # the job's typing states after the lookup (addendum N6: search, model, check)


# ---------- the mode (spec 7, addendum N8) ----------

def _settings_path() -> Any:
    return config.STATE_DIR / "settings.json"


def _settings() -> dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_mode() -> str:
    """on, warnings or off; on when nothing (or something unknown) is set."""
    mode = _settings().get(MODE_KEY)
    return mode if mode in MODES else "on"


def write_mode(mode: str) -> str:
    """Set the mode in settings.json in one step, keeping every other key (the page language lives there too)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    data = {**_settings(), MODE_KEY: mode}
    config.atomic_write(_settings_path(), json.dumps(data, indent=2) + "\n")
    return mode


# ---------- JSON shapes of appendix B ----------

def source_dict(ref: SourceRef) -> dict[str, Any]:
    return {"id": ref.id, "title": ref.title, "url": ref.url}


def proposal_dict(card: Proposal) -> dict[str, Any]:
    """A card for the page. Card.reason ("request", "probe_humidity:high") is the ledger's link, not a text for
    people, so it is not handed out (the page would print it under the card)."""
    return {"id": card.id, "kind": card.kind, "body": dict(card.body), "label": card.label,
            "two_taps": card.two_taps, "expires": card.expires}


def answer_dict(answer: Answer | None) -> dict[str, Any] | None:
    """Appendix B, plus "more": {text, sources} or None, the part the page folds away ("Mehr aus der Kartei"); its
    sources are numbered on from the bubble's."""
    if answer is None:
        return None
    more = {"text": answer.more, "sources": [source_dict(s) for s in answer.more_sources]} if answer.more else None
    return {"id": answer.id, "route": answer.route, "lang": answer.lang, "text": answer.text,
            "sources": [source_dict(s) for s in answer.sources],
            "proposals": [proposal_dict(p) for p in answer.proposals], "clarify": answer.clarify,
            "chips": list(answer.chips), "draft_only": answer.draft_only, "note": answer.note,
            "timing": dict(answer.timing), "gap": answer.gap, "more": more, "link": answer.link,
            "plan": answer.plan}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _reason_head(reason: str) -> str:
    """The kind of a route reason without what follows it (a typed word, an example sentence): turns.jsonl keeps no
    question text (spec 8.5)."""
    return (reason or "").split(":", 1)[0]


# ---------- jobs ----------

@dataclass
class Job:
    """The model's polish of one draft (addendum N0). stage: model (waiting for or writing with the model), check
    (validating), done."""

    id: str
    lang: str
    draft: Answer
    ctx: context.Context
    started: float
    found: list[str] = field(default_factory=list)
    stage: str = "model"
    cold: bool | None = None
    done: bool = False
    answer: Answer | None = None
    finished: float | None = None
    model_calls: int = 0
    validation: validator_mod.Validation | None = None
    note: str | None = None
    task: asyncio.Task[None] | None = None
    model_turn: dict[str, Any] | None = None  # Ollama's counters and times of the polish (ChatResult.turn())
    client_id: str | None = None  # the chat it belongs to: a new question there cancels it (_supersede)
    lead: str | None = None  # the draft's first line from code (v1.3), kept in front of the polished text

    def view(self, now: float) -> dict[str, Any]:
        """GET /api/hank/job/{id} (appendix B): {stage, elapsed_s, found, cold, draft, done, answer}."""
        end = self.finished if self.finished is not None else now
        return {"id": self.id, "stage": self.stage, "elapsed_s": round(max(0.0, end - self.started), 1),
                "found": list(self.found), "cold": self.cold, "draft": answer_dict(self.draft), "done": self.done,
                "answer": answer_dict(self.answer) if self.done else None}


@dataclass
class Asked:
    """One question through the pipeline: the route, the response for the page and the job, if one was started."""

    route: Route
    response: dict[str, Any]
    answer: Answer
    job: Job | None = None
    ms: int = 0


# ---------- Hank ----------

class Hank:
    """The assistant. live(lang) -> the collector's mapping (wiring.Collector; None: an empty moment); client: an
    ollama.OllamaClient (None: no model and no embedding); lane: the shared ModelLane (the photo reading holds it too);
    demo: the demo run (no Ollama at all: the demo box is made up, so is its plant). There is no box parameter on
    purpose (spec 0.3 rule 1): Hank reads, the collector is the only part that touches the cockpit's objects."""

    def __init__(self, *, live: Callable[[str], Mapping[str, Any]] | None = None,
                 client: ollama.OllamaClient | None = None, lane: ollama.ModelLane | None = None,
                 demo: bool = False, model: bool = True, index: Index | None = None,
                 thresholds: Mapping[str, Mapping[str, float]] | None = None,
                 clock: Callable[[], float] = time.time, tick_seconds: float = TICK_SECONDS) -> None:
        self.live_source = live
        self.demo = bool(demo)
        self.client = None if self.demo else client
        if lane is None:
            lane = getattr(self.client, "lane", None) or ollama.ModelLane()
        self.lane = lane
        if self.client is not None and getattr(self.client, "lane", None) is None:
            self.client.lane = lane  # a question embedding is skipped while a model job holds the lane (spec 2.3)
        self.use_model = bool(model) and self.client is not None
        self.health = ollama.Health(self.client) if self.client is not None else None
        self.clock = clock
        self.tick_seconds = tick_seconds
        self.stores = stores.Stores(clock)
        self.ledger = ledger_mod.Ledger(self.stores.ledger, clock)
        self.registry = proposals.Registry(clock)
        self.window = validator_mod.DropWindow()
        self.index = index
        self.router: router_mod.Router | None = None
        self._thresholds = thresholds
        self.jobs: dict[str, Job] = {}
        self.nudge: nudges.NudgeState | None = None
        self.task: asyncio.Task[None] | None = None  # the ticker (spec WP9: serve() keeps the handle)
        self._side: set[asyncio.Task[Any]] = set()
        self._answers: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._grow_count = 0
        self._ready: asyncio.Event | None = None
        self._dense_tried: float | None = None
        self._warm = False  # the router's off-topic examples and the shelves have their vectors (router.warm)
        self.errors: dict[str, str] = {}  # the last error per step, for the logs and the tests

    # ----- building -----

    def ensure_index(self) -> Index:
        """The index and the router, built once (the lexical part takes a few hundred milliseconds)."""
        if self.index is None:
            self.index = Index.load(embedder=self.client)
        if self.router is None:
            self.router = router_mod.Router(self.index, thresholds=self._thresholds, clock=self.clock)
        return self.index

    async def _wait_ready(self) -> None:
        if self._ready is not None and not self._ready.is_set():
            await self._ready.wait()
        self.ensure_index()

    async def start(self) -> asyncio.Task[None]:
        """Build the index in a thread, then run the ticker until stop(). Returns the ticker task (hank.task)."""
        self._ready = asyncio.Event()
        self.task = asyncio.create_task(self._run(), name="hank")
        return self.task

    async def stop(self) -> None:
        for job in list(self.jobs.values()):
            if job.task is not None and not job.task.done():
                job.task.cancel()
        tasks = [t for t in [self.task, *self._side, *(j.task for j in self.jobs.values())] if t is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None

    async def _run(self) -> None:
        """Build the index, then tick every minute. Nothing here ends the loop but a cancel (spec 10.1 rule 7; review
        3, L2): a failed build is logged, kept in errors["index"] and tried again a minute later, and a failing gap
        pass is logged like a failing tick."""
        while True:
            try:
                await asyncio.to_thread(self.ensure_index)
                self.errors.pop("index", None)
                break
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 (spec 10.1 rule 7: Hank broken, the cockpit runs on)
                _LOGGER.exception("Hank: the card index could not be built; trying again in a minute")
                self.errors["index"] = repr(err)
            finally:
                if self._ready is not None:
                    self._ready.set()
            await asyncio.sleep(self.tick_seconds)
        try:
            self._twin_gaps()
        except Exception as err:  # noqa: BLE001
            self.errors["twin_gaps"] = repr(err)
            _LOGGER.exception("Hank: the twin gap entries failed; the ticker runs on")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                self.errors["tick"] = repr(err)
                _LOGGER.exception("Hank ticker failed; it runs again in a minute")
            await asyncio.sleep(self.tick_seconds)

    def _spawn(self, coro: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._side.add(task)
        task.add_done_callback(self._side.discard)
        return task

    # ----- the live moment -----

    def _sources(self, lang: str) -> dict[str, Any]:
        """The collector's moment plus Hank's own parts: the active ledger entries and the journal (the newest three
        lines without the automation's, which the automation view already lists, plus the newest accepted light
        card, which the 24 h rule of addendum N5 needs). A caller's own ledger or journal is kept (the eval set)."""
        try:
            got = self.live_source(lang) if self.live_source is not None else wiring.empty(lang)
            live = dict(got) if isinstance(got, Mapping) else wiring.empty(lang)
        except Exception as err:  # noqa: BLE001
            self.errors["live"] = repr(err)
            _LOGGER.warning("Hank: live state unreadable (%r), answering without it", err)
            live = wiring.empty(lang)
        if "ledger" not in live:
            live["ledger"] = stores.safe(lambda: self.ledger.items(active=True), "ledger items") or []
        if "journal" not in live:
            live["journal"] = stores.safe(self._journal_lines, "journal") or []
        return live

    def _journal_lines(self) -> list[dict[str, Any]]:
        journal = self.stores.journal
        recent = journal.recent(livestate.RECENT, exclude=("automation",))
        light = journal.last(proposals.LIGHT_ACTIONS, "hank_accepted")
        if light is not None and light not in recent:
            return [light, *recent]
        return recent

    def _live_passages(self) -> list[Passage]:
        """Addendum N2: one passage per state group, id live/<group>, new every minute."""
        groups = {lang: livestate.collect(self._sources(lang)).groups(lang) for lang in ("de", "en")}
        out = []
        for de, en in zip(groups["de"], groups["en"], strict=True):
            out.append(Passage(id=f"live/{de.key}", card=de.key, kind="live",
                               title={"de": i18n.t("hank.source.live", "de"), "en": i18n.t("hank.source.live", "en")},
                               text={"de": de.text, "en": en.text}, numbers=tuple(dict.fromkeys(de.numbers)),
                               sources=("live",), entities=(), meta={"group": de.key, "marker": de.id}))
        return out

    # ----- asking -----

    async def ask(self, q: Any, *, client_id: str | None = None, lang: str | None = None,
                  model: bool | None = None, fix: list[str] | None = None) -> dict[str, Any]:
        """POST /api/hank/ask: {done: true, answer} for a final answer, {id, done: false, draft} while the model
        polishes the draft (appendix B, addendum N0). fix: the problem keys of the traffic-light line or the note the
        owner tapped (fix.py); they only narrow a fix answer, the question is routed as always."""
        return (await self.ask_full(q, client_id=client_id, lang=lang, model=model, fix=fix)).response

    async def ask_full(self, q: Any, *, client_id: str | None = None, lang: str | None = None,
                       model: bool | None = None, fix: list[str] | None = None) -> Asked:
        t0 = time.perf_counter()
        await self._wait_ready()
        assert self.router is not None
        mode = read_mode()
        hint = lang if lang in ("de", "en") else i18n.current_lang()
        cleaned = router_mod.clean(q)
        qlang = text.detect_lang(cleaned, hint) if cleaned and fix is None else hint
        live = self._sources(qlang)
        if fix is not None:  # a tap on the light or a note: the page's own question, the fix answer of those problems
            route = self.router.tap_fix(q, lang_hint=hint, mode=mode, live=live)  # the safety gates first (tap_fix)
        else:
            route = await self.router.aroute(q, lang_hint=qlang, live=live, mode=mode, client_id=client_id,
                                             before_dense=lambda: self._supersede(client_id),  # only when it searches
                                             skip_dense=lambda: self.lane.busy)
        asked = self._respond(route, cleaned, live, t0, mode=mode, model=model, fix=fix, client_id=client_id)
        if asked.job is not None:
            asked.job.client_id = client_id
        if fix is None and isinstance(cleaned, str) and cleaned:  # Jev beside the router, watch only (jev.py)
            self._spawn(jev.watch(cleaned, lang=qlang, route=route.name), "hank-jev-watch")
        return asked

    async def _supersede(self, client_id: str | None) -> None:
        """A new question in the same chat cancels the polish still running or waiting for that chat (WP11: while a
        polish held the lane for 45 to 83 s, every new question skipped the embedding and went lexical). Cancelling
        the task closes its HTTP request, so Ollama stops writing; the earlier answer keeps its draft (_polish's
        finally), and the lane is free again unless another chat's polish or the photo reading holds it. The daily
        photo reading is not cancelled: while it runs, a question still goes lexical (rare, once a day)."""
        if not client_id:
            return
        running = [j for j in self.jobs.values()
                   if j.client_id == client_id and not j.done and j.task is not None and not j.task.done()]
        for job in running:
            assert job.task is not None
            job.note = "superseded"  # before the cancel, so _polish's turn log carries the reason
            job.task.cancel()
        if running:
            await asyncio.gather(*(j.task for j in running if j.task is not None), return_exceptions=True)
        for job in running:
            self._close_unstarted(job)

    def _close_unstarted(self, job: Job) -> None:
        """A polish task cancelled before its first step never entered _polish, so its finally never ran: the draft
        becomes the answer here, else the job stays open and the page polls it forever."""
        if not job.done:
            job.answer = replace(job.draft, draft_only=True, note=None)
            job.stage, job.done, job.finished = "done", True, self.clock()

    def _clarify_client(self, clarify_id: str | None) -> str | None:
        assert self.router is not None
        for cid, open_question in self.router.book.open_.items():
            if open_question.id == clarify_id:
                return cid
        return None

    async def clarify(self, clarify_id: str | None, answer: Any, *, client_id: str | None = None,
                      lang: str | None = None, model: bool | None = None) -> dict[str, Any]:
        """POST /api/hank/clarify {id, answer}: Ja or Nein on "Meinst du X?" (spec 3.4). The page sends the
        question's id; the client it belongs to is looked up."""
        t0 = time.perf_counter()
        await self._wait_ready()
        assert self.router is not None
        word = str(answer).strip().lower() if not isinstance(answer, bool) else ("yes" if answer else "no")
        if word not in YES | NO:
            raise ValueError("answer must be yes or no")
        cid = client_id or self._clarify_client(clarify_id) or ""
        pending = self.router.book.pending(cid)
        mode = read_mode()
        plang = pending.lang if pending is not None else (lang if lang in ("de", "en") else i18n.current_lang())
        live = self._sources(plang)
        route = self.router.answer_clarify(cid, clarify_id, word in YES, live=live, mode=mode)
        if route.name == "grow":  # a new polish follows: the chat's earlier one gives way (a "Nein" leaves it)
            await self._supersede(cid)
        question = pending.question if pending is not None else ""
        if pending is not None and word in YES:
            question = typo.substitute(pending.question, pending.candidate)
        asked = self._respond(route, question, live, t0, mode=mode, model=model, client_id=cid or None)
        if asked.job is not None:
            asked.job.client_id = cid or None
        return asked.response

    def _drain_spellings(self) -> None:
        """Learned and rejected spellings to aliases-learned.json (an explicit "Ja" only, addendum N7)."""
        assert self.router is not None
        confirmed, rejected = self.router.book.drain()
        aliases = self.stores.aliases
        for corr in confirmed:
            stores.safe(lambda c=corr: aliases.confirm(c.token, c.term, c.entity), "alias confirm")
        for corr in rejected:
            stores.safe(lambda c=corr: aliases.reject(c.token, c.term), "alias reject")

    def _respond(self, route: Route, question: str, live: dict[str, Any], t0: float, *, mode: str,
                 model: bool | None, fix: list[str] | None = None, client_id: str | None = None) -> Asked:
        self._drain_spellings()
        if route.name != "grow":
            problems: list[str] = []
            if route.name == "today":  # what is open, from code only (today.py); no model, no job
                answer = self._today(live, route.lang)
                problems = asking_problems(live, route.lang)
            elif route.name == "fix":  # the problems off target and their cards, from code only (fix.py)
                keys = fix if fix is not None else route.fix_keys
                answer = self._fix(live, route.lang, keys, route=route, mode=mode, problems=problems)
            elif route.name == "autopilot":  # what the autopilot did, from its own log (pilot_report.py), no model
                answer = pilot_report.answer(live, route.lang, answer_id=_new_id("a"))
            elif route.name == "pump_history":
                answer = pump_report.answer(live, route.lang, answer_id=_new_id("a"))
            elif route.name == "thirst":  # the Durst-Index's brief (thirst_report.py), from code, no model
                answer = thirst_report.answer(live, route.lang, answer_id=_new_id("a"), question=question)
            elif route.name == "humidifier_tank":
                answer = humidifier_report.answer(live, route.lang, answer_id=_new_id("a"))
            elif route.name == "strains":  # the strain advice or the lexicon about one strain (strains.py), no model
                answer = strains_mod.answer(route, live, answer_id=_new_id("a"), index=self.index)
            elif route.form in shelves_mod.FORMS:  # 01.11.2026: a shelf card or a "Meinst du ...?" (shelves.py)
                answer = shelves_mod.answer(route, live, answer_id=_new_id("a"), index=self.index)
            elif route.name == "explain" and route.cards:
                # v1.4: a method card (methods.py: the steps, when, autoflower, box fit, the window while it flowers)
                # or "Erzähl mir mehr über <Titel>" about any wiki card Hank reads (wikicards.py)
                answer = wikicards.answer(route, live, answer_id=_new_id("a"), index=self.index)
            elif route.name == "explain" or (route.name == "no_data" and route.term):
                # a glossary term, then its live reading (asking.py); a no_data question about a glossary term gets
                # the term's text with "Eine eigene Karte habe ich dazu nicht" and stays on the gap list (v1.3);
                # v1.4: a technique with a method card gets one line more, that the card has the steps
                answer = methods.with_pointer(asking.explain_answer(route, live, answer_id=_new_id("a")), route)
            elif route.name == "reading":  # the plant's height, a term the box does not measure (asking.py)
                if route.term in feedplan.TERMS:  # feeding v2: the age, the plan, the choice; a claim's card
                    answer = feedplan.reading(route, live, answer_id=_new_id("a"), now=self.clock())
                    self.registry.add(answer.proposals)
                else:
                    answer = asking.reading_answer(route, live, answer_id=_new_id("a"))
            else:
                answer = replies.answer(route, index=self.index, answer_id=_new_id("a"))
            ms = round((time.perf_counter() - t0) * 1000)
            answer.timing = {"ms": ms}
            if answer.gap:
                self._gap_for(route, question)
            self._remember(answer, route, question)
            self._turn(route, answer, ms)
            self._topic(client_id, route, problems)
            return Asked(route, {"done": True, "answer": answer_dict(answer)}, answer, None, ms)
        asked = self._grow(route, question, live, t0, mode=mode, model=model)
        self._topic(client_id, route, [])
        return asked

    def _today(self, live: dict[str, Any], lang: str) -> Answer:
        """The today list (today.py). The question for the real bloom start is asked once per estimated date; after
        that a short reminder stands there while the date is still only estimated (asking.bloom_line)."""
        state = livestate.collect(live)
        estimate = asking.bloom_estimate(state)
        asked = bool(estimate) and bool(stores.safe(lambda: asking.asked_before("bloom_start", estimate or ""),
                                                    "asked"))
        # feeding v2 (spec 4.10): the plant's start, asked once per stored value (unknown, or only approximate)
        start = feedplan.age_asked_key(state)
        age_asked = bool(start) and bool(stores.safe(lambda: asking.asked_before("start_date", start or ""),
                                                     "asked"))
        answer = today_mod.answer(live, lang, cards=self.registry.open(), answer_id=_new_id("a"), bloom_asked=asked,
                                  age_asked=age_asked)
        self.registry.add(answer.proposals)  # the VPD explanation's cards (vpd.py) are new; the open ones stay
        question = asking.bloom_line(state, lang, asked=False)
        if estimate and not asked and question and question in answer.text:
            stores.safe(lambda: asking.mark_asked("bloom_start", estimate), "asked")
        age_question = feedplan.age_line(state, lang, asked=False)
        if start and not age_asked and age_question and age_question in answer.text:
            stores.safe(lambda: asking.mark_asked("start_date", start), "asked")
        return answer

    def _topic(self, client_id: str | None, route: Route, problems: list[str]) -> None:
        """The chat's topic after an answer, for the follow-ups (asking.TopicBook): the term or reading of an explain
        or reading answer, the metric or device a growing answer was about, the problems a fix or today answer
        listed. A greeting, thanks, a gate or no_data leave the topic as it was."""
        if not client_id or self.router is None:
            return
        found = stores.safe(lambda: topic_of(route, problems, index=self.index), "topic")
        if found is not None:
            self.router.topics.remember(client_id, found)

    def _fix(self, live: dict[str, Any], lang: str, keys: list[str] | None, *, route: Route | None = None,
             mode: str = "on", problems: list[str] | None = None) -> Answer:
        """The fix answer (fix.py). Its cards go into the registry (a tap is then "hank_accepted") and the advice into
        the ledger, linked to its card, exactly as for a growing answer (spec 6.2). A card handed out again (a second
        fix ask, or after "Nicht jetzt") takes over its entry (Ledger.relink), so a tap on it moves the entry to
        applied. A bare typed "Was soll ich tun?" with nothing off target gets the today list instead (today.py)."""
        now = self.clock()
        found = fix_mod.compose(live, lang, keys=keys, now=now, about=route.about if route is not None else None)
        if problems is not None:  # the problems this answer is about, for the chat's topic (follow-ups)
            problems += [fix_mod.key_of(v) for v in found.advised]
            problems += [fix_mod.KEY_STAGE] if any(c.kind == "plant_stage" for c in found.cards) else []
        reason = (route.reason or "") if route is not None else ""
        if found.nothing and keys is None and mode == "on" and self.router is not None                 and self.router.gates.fix_bare(reason.partition(":")[2]):
            return today_mod.answer(live, lang, cards=self.registry.open(), answer_id=_new_id("a"))
        answer = fix_mod.answer(found, lang, answer_id=_new_id("a"))
        if found.advised:
            stores.safe(lambda: self.ledger.record(found.advised, live=live, answer_id=answer.id,
                                                   proposals=found.cards), "ledger record")
        if found.cards:
            stores.safe(lambda: self.ledger.relink(found.cards, live, now, advised=found.advised), "ledger relink")
        self.registry.add(found.cards)
        return answer

    def _gap_for(self, route: Route, question: str) -> None:
        reason = (route.reason or "").rsplit(";", 1)[-1]  # a follow-up's reason starts with "followup:<kind>;"
        term = None
        if reason.startswith(("uncovered:", "name:")):
            entity = reason.split(":", 1)[1]
            term = replies._entity_name(self.index, entity, route.lang)
        hits = route.retrieval.hits if route.retrieval is not None else []
        best = hits[0].passage.id if hits else None
        stores.safe(lambda: self.stores.gaps.add_question(question, route.lang, kind="no_card", term=term,
                                                          best=best), "gap entry")

    def _cards(self, route: Route, question: str, live: Mapping[str, Any], verdicts: Any, lang: str) -> list[Proposal]:
        """The suggestion cards of an answer, from code only (spec 5.5): the action wish first, then the checks off
        target that the ledger still offers (a declined one only when it got worse, spec 6.3)."""
        now = self.clock()
        cards: list[Proposal] = []
        slots = route.retrieval.slots if route.retrieval is not None else None
        if route.intent == "action":
            cards += stores.safe(lambda: proposals.from_request(question, slots, live, lang=lang, now=now),
                                 "cards from the request") or []
        checks = stores.safe(lambda: proposals.from_checks(verdicts, live, lang=lang, now=now),
                             "cards from the checks") or []
        checks = stores.safe(lambda: self.ledger.offerable(checks, live), "ledger offerable") or []
        cards += [c for c in checks if all(c.body != have.body for have in cards)]
        # feeding v2 (4.7): a stated age in a mixed question ("Meine Pflanze ist 5 Wochen alt, wie viel Dünger?")
        # gets the same start card as the pure claim
        claimed = stores.safe(lambda: feedplan.cards_for_claim(question, live, lang, now=now), "age claim card") or []
        cards += [c for c in claimed if all(c.body != have.body for have in cards)]
        if slots is not None:  # review 3, L1: never a card against the direction the question asks for
            wanted = stores.safe(lambda: proposals.wishes(slots, live), "card directions") or {}
            if wanted:
                kept = stores.safe(lambda: proposals.against(cards, wanted, live), "card directions")
                cards = cards if kept is None else kept
        return cards

    def _grow(self, route: Route, question: str, live: dict[str, Any], t0: float, *, mode: str,
              model: bool | None) -> Asked:
        assert self.index is not None
        lang = route.lang
        route = self._with_experience(route, question)
        route = stages_now(route, question, live)
        sources = self._passage_sources
        ctx = context.build(route, live, None, lang, question=question, passage_sources=sources)
        cards = self._cards(route, question, live, ctx.verdicts, lang)
        # 23.10.2026: a question about the VPD (or a why question about the climate) while the VPD is above its band
        # opens with why, what runs, what blocks and why the light waits (vpd.py), code text that stays in front of a
        # polished answer too; its cards (two taps) join the answer's before the draft, so the draft's proposal line
        # names them, and its link points to the Autopilot card
        high = None if route.intent == "action" else stores.safe(lambda: vpd_lead(route, live, lang), "vpd lead")
        if high is not None:
            cards += [c for c in high.cards if all(c.body != have.body for have in cards)]
        if cards:
            ctx = context.build(route, live, None, lang, question=question, proposals=cards,
                                passage_sources=sources)
        self._grow_count += 1
        red = self.nudge is not None and self.nudge.level == "red"
        composed = compose.build(ctx, saying=self._grow_count % SAYING_EVERY == 0 and not red)
        body = composed.text.strip() or i18n.t(validator_mod.DONT_KNOW_KEY, lang)
        # v1.3 (review 1): a switching wish opens with "Im Chat schalte ich nichts ohne dich" and its card, a why
        # question about the climate or the automation with what the box is doing or what the automation waits for. Code
        # text: it stays in front of a polished answer too (_polish).
        lead = asking.action_lead(cards, lang) if route.intent == "action" else \
            stores.safe(lambda: asking.why_lead(route, livestate.collect(live), lang), "why lead")
        link = None
        if high is not None:  # the VPD explanation takes the place of the box line
            lead = high.text()
            link = high.link
        if lead:
            body = f"{lead}\n{body}"
        answer_id = _new_id("a")
        answer = Answer(id=answer_id, route="grow", lang=lang, text=body, sources=list(composed.sources),
                        proposals=list(cards), clarify=None, chips=[], draft_only=True, note=None, timing={},
                        gap=False, more=composed.more, more_sources=list(composed.more_sources), lead=lead or None,
                        link=link)
        stores.safe(lambda: self.stores.gaps.add_many(composed.gaps), "gap entries")
        stores.safe(lambda: self.ledger.record(composed.advised, live=live, answer_id=answer_id, proposals=cards),
                    "ledger record")
        if composed.told:
            stores.safe(lambda: self.ledger.mark_told(composed.told, live), "ledger told")
        self.registry.add(cards)
        ms = round((time.perf_counter() - t0) * 1000)
        answer.timing = {"ms": ms, "route_ms": (route.retrieval.ms if route.retrieval else {}).get("route")}
        self._remember(answer, route, question)

        want = self.use_model if model is None else (bool(model) and self.client is not None)
        note = self._no_polish(ctx) if want and mode == "on" else None
        if not want or mode != "on" or note is not None:
            # A full queue only means no polish: the draft is a whole answer, so the page shows its "facts only"
            # line, not "Versuch es gleich noch einmal" above it (screens review, finding 12).
            answer.note = None if note == "queue_full" else self._note_text(note, lang)
            self._turn(route, answer, ms, extra={"note": note, "budget": ctx.budget.tokens, "fits": ctx.fits})
            return Asked(route, {"done": True, "answer": answer_dict(answer)}, answer, None, ms)
        job = Job(id=_new_id("j"), lang=lang, draft=answer, ctx=ctx, started=self.clock(),
                  found=self._found(route, lang), cold=self._cold(), lead=lead or None)
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._polish(job, route, ms), name=f"hank_{job.id}")
        return Asked(route, {"id": job.id, "done": False, "draft": answer_dict(answer), "stage": job.stage},
                     answer, job, ms)

    def _passage_sources(self, passage: Passage, lang: str) -> list[SourceRef]:
        """The index's sources of a passage; its own experience source carries the catalog's title (the index names
        it plainly "own")."""
        assert self.index is not None
        return [SourceRef(ref.id, i18n.t("hank.source.own", lang), None) if ref.id == "own" else ref
                for ref in self.index.sources(passage, lang)]

    def _with_experience(self, route: Route, question: str) -> Route:
        """Own experience (spec 8.4) joins a growing answer as one more hit when it matches the question as well as a
        card would (T_HIT of the route's threshold set). The index never searches it by default (it is kept apart
        from the sourced values); context.py gives it its own sentence with the "Aus deiner eigenen Erfahrung" lead."""
        retrieval = route.retrieval
        if retrieval is None or self.router is None or self.index is None:
            return route
        if not self.index.by_meta(kind="experience"):
            return route
        own = self.index.search(question, lang=route.lang, kinds=["experience"], limit=1)
        t_hit = float(self.router.thresholds(retrieval.dense).get("T_HIT", 1.0))
        if not own.hits or own.confidence < t_hit or any(h.passage.id == own.hits[0].passage.id
                                                         for h in retrieval.hits):
            return route
        # Card scores carry slot boosts, the experience search has none: ranked by its own score it would fall
        # behind the fifth card and never be said. It goes right behind the best card instead (the sort in
        # context.py is stable), and its own confidence stays in the channels.
        best = max((h.score for h in retrieval.hits), default=own.hits[0].score)
        joined = Hit(own.hits[0].passage, best, {**own.hits[0].channels, "own": own.confidence})
        hits = [*retrieval.hits[:1], joined, *retrieval.hits[1:]]
        return replace(route, retrieval=replace(retrieval, hits=hits))

    def _found(self, route: Route, lang: str) -> list[str]:
        """Titles of the cards found, without numbers (spec 9.2), for the typing line."""
        out: list[str] = []
        for hit in (route.retrieval.hits if route.retrieval is not None else [])[:context.MAX_FACTS]:
            title = (hit.passage.title or {}).get(lang) or (hit.passage.title or {}).get("de") or ""
            if title and title not in out:
                out.append(title)
        return out[:3]

    def _cold(self) -> bool | None:
        cached = self.health.cached if self.health is not None else None
        loaded = ((cached or {}).get("chat_model") or {}).get("loaded")
        return None if loaded is None else not loaded

    def _note_text(self, note: str | None, lang: str) -> str | None:
        key = NOTE_KEYS.get(note or "")
        if key is None:
            return None
        return texts.say(key, lang, model=self.client.chat_model if self.client is not None else "")

    def _active_jobs(self) -> int:
        return sum(1 for j in self.jobs.values() if not j.done)

    def _no_polish(self, ctx: context.Context) -> str | None:
        """Why the model is not asked at all, or None (addendum N0, spec 3.6, 4.9)."""
        if self.lane.holder == "photo" or "photo" in self.lane.waiting:
            return "busy_photo"
        cached = self.health.cached if self.health is not None else None
        if cached:
            if cached.get("ollama") in ("no_ollama", "failed", "timeout"):
                return "model_down"
            if (cached.get("chat_model") or {}).get("state") == "missing":
                return "model_missing"
        if self._active_jobs() >= 1 + QUEUE_WAITING:
            return "queue_full"
        if not ctx.fits:  # spec 4.6: the sheet does not fit the budget even trimmed; the catalog has no own note
            return "no_fit"
        return None

    async def _polish(self, job: Job, route: Route, ms: int) -> None:
        """The model formulates (at most four sentences), the validator checks, a passing version replaces the
        draft. Every failure leaves the draft (addendum N0)."""
        assert self.client is not None
        result: ollama.ChatResult | None = None
        note: str | None = None
        try:
            async with self.lane.hold("chat"):
                job.stage = "model"
                result = await self.client.chat(context.prompt(job.ctx))
                job.model_calls += 1
                job.model_turn = result.turn()
            if result.cold is not None:
                job.cold = result.cold
            job.stage = "check"
            validation = validator_mod.validate(result.content, job.ctx, job.lang)
            if job.lead and not validation.used_draft:  # the code's lead stays in front (v1.3)
                validation = replace(validation, text=f"{job.lead}\n{validation.text}")
            if not validation.used_draft and validation.text == job.draft.text:
                # only "more" changed: the bubble keeps the draft, no "überarbeitet" (finding 4)
                validation = replace(validation, used_draft=True, rejected=validator_mod.UNCHANGED,
                                     sources=list(job.draft.sources), more=job.draft.more,
                                     more_sources=list(job.draft.more_sources))
            job.validation = validation
            self.window.add(validation)
            if not validation.used_draft:
                job.answer = replace(job.draft, text=validation.text, sources=list(validation.sources),
                                     more=validation.more, more_sources=list(validation.more_sources),
                                     draft_only=False, note=None,
                                     timing={**job.draft.timing, "model_s": result.wall_s})
            elif validation.rejected:  # it passed sentence by sentence but lost a core fact: the draft stays
                _LOGGER.info("Hank: the polished version was not taken (%s); the draft stays", validation.rejected)
        except ollama.LaneBusy:
            note = "busy_photo"
        except ollama.OllamaError as err:
            note = {"no_model": "model_missing", "timeout": "slow"}.get(err.kind, "model_down")
            _LOGGER.info("Hank: the model did not answer (%s): %s", err.kind, err)
        except asyncio.CancelledError:
            note = "superseded" if job.note == "superseded" else None  # a new question (_supersede) or DELETE
            raise
        except Exception as err:  # noqa: BLE001 (a broken polish never takes the draft away)
            note = "model_down"
            self.errors["polish"] = repr(err)
            _LOGGER.exception("Hank: polishing the draft failed")
        finally:
            if job.answer is None:
                job.answer = replace(job.draft, draft_only=True, note=self._note_text(note, job.lang))
            job.note = note
            job.stage = "done"
            job.done = True
            job.finished = self.clock()
            if job.answer is not None and not job.answer.draft_only:
                self._remember(job.answer, route, job.ctx.question, chat=False)
            extra: dict[str, Any] = {"note": note, "budget": job.ctx.budget.tokens, "fits": job.ctx.fits,
                                     "polished": not job.answer.draft_only, "model_calls": job.model_calls,
                                     "validator": job.validation.report() if job.validation else None,
                                     "drop_flag": self.window.flag()}
            if result is not None:
                extra["model"] = result.turn()
            self._turn(route, job.answer, ms, extra=extra)

    def job(self, job_id: str) -> dict[str, Any] | None:
        self._expire_jobs()
        found = self.jobs.get(job_id)
        return None if found is None else found.view(self.clock())

    async def cancel(self, job_id: str) -> bool:
        """DELETE /api/hank/job/{id}: the draft stays the answer."""
        found = self.jobs.get(job_id)
        if found is None:
            return False
        if found.task is not None and not found.task.done():
            found.task.cancel()
            await asyncio.gather(found.task, return_exceptions=True)
        self._close_unstarted(found)
        return True

    def _expire_jobs(self) -> None:
        now = self.clock()
        for job_id in [k for k, j in self.jobs.items() if j.done and (j.finished or now) + JOB_KEEP_SECONDS < now]:
            del self.jobs[job_id]

    # ----- records -----

    def _remember(self, answer: Answer, route: Route, question: str, *, chat: bool = True) -> None:
        """The answer for a later thumb (in memory, the last ANSWERS_KEPT), and the question and answer for the chat
        ring (spec 8: the last 50 messages). A polished version only replaces the remembered text."""
        hits = [h.passage.id for h in (route.retrieval.hits if route.retrieval is not None else [])[:3]]
        self._answers[answer.id] = {"route": answer.route, "question": question, "hits": hits, "text": answer.text,
                                    "lang": answer.lang}
        self._answers.move_to_end(answer.id)
        while len(self._answers) > ANSWERS_KEPT:
            self._answers.popitem(last=False)
        if not chat:
            return
        chat_ring = self.stores.chat
        stores.safe(lambda: chat_ring.add({"kind": "user", "text": question, "lang": answer.lang}), "chat ring")
        stores.safe(lambda: chat_ring.add({"kind": "answer", "answer_id": answer.id, "route": answer.route,
                                           "text": answer.text, "lang": answer.lang}), "chat ring")

    def _turn(self, route: Route, answer: Answer, ms: int, *, extra: Mapping[str, Any] | None = None) -> None:
        retrieval = route.retrieval
        record = {"answer_id": answer.id, "route": route.name, "reason": _reason_head(route.reason),
                  "intent": route.intent, "lang": answer.lang, "ms": ms,
                  "search_ms": dict(retrieval.ms) if retrieval is not None else None,
                  "dense": retrieval.dense if retrieval is not None else None,
                  "confidence": round(retrieval.confidence, 3) if retrieval is not None else None,
                  "hits": [h.passage.id for h in retrieval.hits[:3]] if retrieval is not None else [],
                  "draft_only": answer.draft_only, "cards": len(answer.proposals), **(extra or {})}
        stores.safe(lambda: self.stores.turns.add(record), "turn log")

    def _twin_gaps(self) -> None:
        """Spec 2.1: one gap entry per passage without an English twin, once (not again on every start)."""
        if read_mode() != "on" or self.index is None:
            return
        have = {(g.get("detail") or {}).get("passage") for g in (stores.safe(self.stores.gaps.all, "gaps") or [])
                if g.get("kind") == "twin_missing"}
        for pid in self.index.twin_missing():
            if pid not in have:
                stores.safe(lambda p=pid: self.stores.gaps.note("twin_missing", passage=p), "twin gap")

    # ----- feedback, cards, notes, mode -----

    def feedback(self, answer_id: str, vote: str) -> dict[str, Any]:
        """POST /api/hank/feedback. A thumb never teaches a spelling (addendum N7) and never enters the eval set by
        itself (spec 8.3)."""
        known = self._answers.get(answer_id) or {}
        self.stores.feedback.add(answer_id, vote, route=known.get("route"), question=known.get("question"),
                                 hits=known.get("hits") or [], answer=known.get("text"), lang=known.get("lang"))
        return {"ok": True}

    def decline(self, card_id: str, lang: str | None = None) -> dict[str, Any]:
        """POST /api/hank/proposal {id, outcome: "declined"}: "Nicht jetzt". The card is gone, the ledger entry is
        declined, the journal says so, and the consequence is told once with its source, then respect (spec 6.3)."""
        lang = lang if lang in ("de", "en") else i18n.current_lang()
        card = self.registry.take(card_id)
        live = self._sources(lang)
        item = self.ledger.declined(card if card is not None else card_id, live=live)
        if card is not None:
            stores.safe(lambda: self.stores.journal.add(card.kind, "hank_declined", body=card.body,
                                                        proposal=card.id), "journal")
        out: dict[str, Any] = {"ok": True}
        if item is not None and item.get("state") == "declined":
            telling = self.ledger.tell(item, live, lang, resolve=context.SourceBook())
            if telling.gap:
                stores.safe(lambda: self.stores.gaps.add(telling.gap or {}), "gap entry")
            out["message"] = {"text": telling.text, "sources": [source_dict(s) for s in telling.sources]}
            stores.safe(lambda: self.stores.chat.add({"kind": "ledger", "text": telling.text, "lang": lang}),
                        "chat ring")
        return out

    def precheck_command(self, body: Mapping[str, Any], lang: str | None = None) -> str | None:
        """Before /api/cmd runs a Hank card (the body carries its id in "proposal"): the reason it may not run now, or
        None. A card is checked when it is made; a tap can come much later, after the slider moved or after another
        card was accepted (review 2, S2: two light cards accepted within seconds, a +50 step from an old card). So
        the same rules run again on the moment of the tap: the card is still handed out and unchanged, the readings
        are fresh, proposals.check_body passes (light at most +25 from the level now, the port lock), and a light
        step up waits 24 h after the last accepted one. A command without a card id (the owner's own slider) is
        never checked here."""
        card_id = body.get("proposal")
        if not isinstance(card_id, str):
            return None
        lang = lang if lang in ("de", "en") else i18n.current_lang()
        card = self.registry.get(card_id)
        if card is None:
            return i18n.t("hank.cardcheck.gone", lang)
        if card.body != {k: v for k, v in body.items() if k != "proposal"}:
            return i18n.t("hank.cardcheck.changed", lang)
        state = livestate.collect(self._sources(lang))
        # a start date and the fertilizer switch rest on no reading
        if state.stale and card.body.get("action") not in ("plant_start", "feeding_setup"):
            return i18n.t("hank.cardcheck.stale", lang)
        try:
            proposals.check_body(card.body, state)
        except proposals.Refused as err:
            return i18n.t("hank.cardcheck.refused", lang, reason=str(err))
        if card.body.get("action") == "light" and int(card.body.get("level") or 0) > (
                proposals.light_level(state) or 0) and proposals.light_wait_until(state) is not None:
            return i18n.t("hank.cardcheck.light_wait", lang)
        return None

    def record_command(self, body: Mapping[str, Any], status: int) -> dict[str, Any] | None:
        """The /api/cmd wrapper's part (spec 5.5, 8): the journal line with its origin. A body with the id of a card
        Hank handed out, unchanged, is "hank_accepted" (and moves its ledger entry to applied when the command went
        through); everything else is "human". Commands that switch nothing are not journaled."""
        action = body.get("action")
        if action not in JOURNAL_ACTIONS:
            return None
        ok = status == 200
        clean = {k: v for k, v in body.items() if k != "proposal"}
        card_id = body.get("proposal") if isinstance(body.get("proposal"), str) else None
        card = self.registry.get(card_id) if card_id else None
        origin = "hank_accepted" if card is not None and card.body == clean else "human"
        if origin == "hank_accepted" and ok:
            self.registry.take(card_id or "")
            stores.safe(lambda: self.ledger.accepted(card_id or ""), "ledger accepted")
        return self.stores.journal.add(str(action), origin, body=clean, proposal=card_id if card else None, ok=ok)

    def ack(self, upto: int) -> dict[str, Any]:
        return {"ok": True, "upto": self.stores.chat.ack(int(upto))}

    async def set_mode(self, mode: str) -> dict[str, Any]:
        """POST /api/hank/mode (spec 7). Leaving "on" unloads the chat model at once (spec 4.7); "off" also hides
        the light."""
        write_mode(mode)
        if mode != "on" and self.client is not None:
            self._spawn(self.client.unload(), "hank_unload")
        if mode == "off":
            self.nudge = None
        else:
            self._spawn(self._tick_quietly(), "hank_tick")
        return {"mode": mode}

    async def _tick_quietly(self) -> None:
        try:
            await self.tick()
        except Exception as err:  # noqa: BLE001
            self.errors["tick"] = repr(err)
            _LOGGER.exception("Hank tick after a mode change failed")

    # ----- the ticker -----

    async def tick(self, now: float | None = None) -> nudges.NudgeState | None:
        """One minute of Hank, code only (spec 1, 6.2, 7; addendum N2, N4)."""
        mode = read_mode()
        self._expire_jobs()
        self.registry.expire()
        if mode == "off":  # spec 7: no light, no notes, no ledger, the index rests
            self.nudge = None
            return None
        await self._wait_ready()  # a tick right after a mode change never builds the index a second time
        index = self.ensure_index()
        stores.safe(self.stores.journal.import_automation, "journal import")
        if stores.safe(index.refresh, "index refresh"):
            self._twin_gaps()
        stores.safe(lambda: index.set_dynamic("live", self._live_passages()), "live passages")
        lang = i18n.server_lang()
        live = self._sources(lang)
        stamp = self.clock() if now is None else now
        state = nudges.tick(live, self.ledger, stamp, mode=mode)
        for message in state.messages:
            stores.safe(lambda m=message: self._note(m), "chat ring")
        if state.proposal is not None:
            self.registry.add([state.proposal])
        self.nudge = state
        if self.health is not None:
            try:
                await self.health.get()
            except Exception as err:  # noqa: BLE001
                self.errors["health"] = repr(err)
        if mode == "on":
            self._maybe_dense(stamp)
        return state

    def _note(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """One note op of nudges.tick into the chat ring: "new" and "raise" (a new, unread note at the bottom; every
        earlier note, read or unread, collapses into it, so the chat holds one note however often the owner read
        them in between), "update" (the live note changes in place; without one it is a new note)."""
        chat = self.stores.chat
        body = {k: v for k, v in message.items() if k != "op"}
        op = message.get("op") or "new"
        live = chat.live_note()
        if op == "update" and live is not None:
            return chat.update(int(live["id"]), body)
        return chat.add(body, collapse=True, replace=int(live["id"]) if live is not None else None)

    def _maybe_dense(self, stamp: float) -> None:
        """The card vectors, then the router's off-topic examples (router.warm). The router is warmed even when the
        card vectors came from the cache at start: until 02.11.2026 that skipped it, and on the Mac the off-topic bank
        never had vectors, so the dense thresholds ran beside a bank that compared letters only, a mix the eval set
        never measured."""
        index = self.index
        if index is None or index.embedder is None:
            return
        cards_done = index.dense_state() == "used"
        if cards_done and (self._warm or self.router is None):
            return
        if self._dense_tried is not None and stamp - self._dense_tried < DENSE_RETRY_SECONDS:
            return
        self._dense_tried = stamp

        async def build() -> None:
            if not cards_done:
                await index.start()
            if self.router is not None:
                self._warm = bool(await self.router.warm())

        self._spawn(build(), "hank_dense")

    # ----- GET /api/hank/state -----

    def _inbox(self, lang: str) -> dict[str, Any]:
        messages = stores.safe(self.stores.chat.messages, "chat ring") or []
        items = []
        for message in [m for m in messages if m.get("kind") == "nudge"][-INBOX_ITEMS:]:
            texts_by_lang = message.get("text") if isinstance(message.get("text"), dict) else {}
            card = self.registry.get(str(message.get("proposal"))) if message.get("proposal") else None
            items.append({"id": message.get("id"), "level": message.get("level"),
                          "text": texts_by_lang.get(lang) or texts_by_lang.get("de") or "",
                          "sources": [], "proposals": [proposal_dict(card)] if card is not None else [],
                          # the problem keys for the note's "Beheben" button (fix.py), its revision (an update in
                          # place draws it again) and the notes it collapsed (the page removes those)
                          "fix": [str(k) for k in message.get("fix") or []], "rev": int(message.get("rev") or 0),
                          "replaces": [int(i) for i in message.get("replaces") or []]})
        return {"unread": stores.safe(self.stores.chat.unread, "chat ring") or 0, "items": items}

    def state(self, lang: str | None = None) -> dict[str, Any]:
        """Appendix B: {mode, light, scene, ollama, chat_model, embed, index, inbox, menu}."""
        lang = lang if lang in ("de", "en") else i18n.current_lang()
        mode = read_mode()
        cached = self.health.cached if self.health is not None else None
        index_state = self.index.state() if self.index is not None else {"passages": None, "dense": "off",
                                                                          "model": None, "pull": None}
        out: dict[str, Any] = {
            "mode": mode, "light": None, "scene": None,
            "ollama": "off" if self.client is None else (cached or {}).get("ollama", "unknown"),
            "chat_model": None if self.client is None else ((cached or {}).get("chat_model")
                                                           or {"name": self.client.chat_model, "state": "unknown",
                                                               "loaded": None, "pull": None}),
            "embed": {"model": index_state.get("model"), "state": index_state.get("dense"),
                      "pull": index_state.get("pull"),
                      # WP11: which threshold set routes. dense while every card vector is there; the last
                      # question's own state shows a per-question fallback (off: a model job held the CPU,
                      # timeout, missing: the embedding model did not answer), then that question went lexical.
                      "search": "dense" if index_state.get("dense") == "used" else "lexical",
                      "last_query": index_state.get("last_query"),
                      "timeouts": index_state.get("timeouts", 0)},
            "index": {"passages": index_state.get("passages"), "dense": index_state.get("dense")},
            "inbox": {"unread": 0, "items": []}, "menu": [],
        }
        if mode == "off":
            return out
        if self.nudge is not None:
            out["light"] = self.nudge.light(lang)
            out["scene"] = self.nudge.scene_dict(lang) if mode == "on" else None
        out["inbox"] = self._inbox(lang)
        return out

    def gaps(self) -> dict[str, Any]:
        return self.stores.gaps.export()


# ---------- the current stage's passages (v1.3) ----------

# Only the watering rows: a target of another stage is often what a question means without naming it ("Bei wie viel
# Grad trockne ich?" is the harvest window's climate, eval grow-17).
STAGE_NOW_PARTS = ("watering",)


VPD_LEAD_METRICS = frozenset({"probe_vpd", "vpd"})  # a question about the VPD itself, of any kind
VPD_WHY_METRICS = frozenset({"probe_humidity", "humidity", "probe_temp", "temp"})  # or a why question about the climate


def vpd_lead(route: Route, live: Mapping[str, Any], lang: str) -> vpd_mod.Found | None:
    """The VPD explanation for a growing answer about the live state (reason "state:metric:<metric>"): any question
    about the VPD, a why question about humidity or temperature; only while the VPD is above its band (vpd.explain)."""
    reason = (route.reason or "").rsplit(";", 1)[-1]
    if not reason.startswith("state:metric:"):
        return None
    metric = reason.split(":", 2)[2]
    if metric not in VPD_LEAD_METRICS and not (route.qtype == "why" and metric in VPD_WHY_METRICS):
        return None
    return vpd_mod.explain(live, lang)


def stages_now(route: Route, question: str, live: Mapping[str, Any]) -> Route:
    """A growing question that names no stage ("Wie gieße ich richtig?") is about today's stage: of the stage card's
    passages of one kind (STAGE_NOW_PARTS) only the current stage's stays, when it was found (review 1, H05 listed
    late flower, seedling and veg, not early flower). A question that names a stage keeps every hit."""
    retrieval = route.retrieval
    if retrieval is None or not retrieval.hits:
        return route
    state = livestate.collect(live)
    current = state.stage_key
    if not current or context.mentions(question, route.lang).stages:
        return route

    def part(hit: Hit) -> tuple[str, str] | None:
        meta = hit.passage.meta or {}
        path, stage = str(meta.get("path") or ""), meta.get("stage")
        if not stage or "." not in path or path.split(".", 1)[1] not in STAGE_NOW_PARTS:
            return None
        return str(stage), hit.passage.card + "#" + path.split(".", 1)[1]

    kinds_now = {p[1] for h in retrieval.hits if (p := part(h)) is not None and p[0] == current}
    kept = [h for h in retrieval.hits if (p := part(h)) is None or p[0] == current or p[1] not in kinds_now]
    if len(kept) == len(retrieval.hits):
        return route
    return replace(route, retrieval=replace(retrieval, hits=kept))


# ---------- the chat's topic (v1.2, asking.py) ----------

def asking_problems(live: Mapping[str, Any], lang: str) -> list[str]:
    """The problem keys off target right now ("probe_humidity:high"), for the topic of a today answer."""
    state = livestate.collect(live)
    if state.stale:
        return []
    return [f"{v.metric}:{v.status}" for v in verdicts.build(state, lang, statuses=verdicts.NEGATIVE).verdicts]


def topic_of(route: Route, problems: list[str], *, index: Any = None) -> asking.Topic | None:
    """What an answer was about (asking.Topic), or None when it leaves the chat's topic as it was."""
    gloss = asking.glossary()
    name = route.name
    reason = (route.reason or "").rsplit(";", 1)[-1]
    if name == "explain" and route.cards and not route.term:
        # v1.4: a method card without a glossary term ("Was ist FIMing?"): its name stays the topic, so "und wie
        # mache ich das?" asks the card's how-to; a crop or process card leaves the topic as it was
        label = methods.topic_label(route)
        return asking.Topic(qtype=route.qtype, name=route.cards[0], label=label) if label else None
    if name == "explain" or (name == "no_data" and route.term):
        term = gloss.get(route.term)
        metric = term.metric if term is not None else None
        return asking.Topic(qtype="explain", term=route.term, metric=metric,
                            label=asking.topic_label(gloss, term=route.term))
    if name == "reading":
        if route.term in ("plant_height", "bloom_day", "height_forecast"):
            return asking.Topic(qtype="value", metric=route.term,
                                label=asking.topic_label(gloss, metric=route.term))
        return asking.Topic(qtype="value", term=route.term, label=asking.topic_label(gloss, term=route.term))
    if name == "no_data" and reason.startswith("uncovered:"):
        # "what is lollipopping" without a glossary term: the name stays the topic, so "how do I do that?" gets
        # "Zu ... habe ich keine Anleitung" instead of the off-topic reply (review 1, MUST 5)
        entity = reason.split(":", 1)[1]
        label = {lang: replies.entity_name(index, entity, lang) for lang in ("de", "en")}
        return asking.Topic(qtype=route.qtype, name=entity, label=label)
    if name == "fix":
        about = route.about or {}
        if about.get("metric") or about.get("term"):
            return asking.Topic(qtype="fix", metric=about.get("metric"), term=about.get("term"),
                                problems=tuple(problems), label=dict(about.get("label") or {}))
        return asking.Topic(qtype="fix", problems=tuple(dict.fromkeys(problems)))
    if name == "today":
        return asking.Topic(qtype="today", problems=tuple(dict.fromkeys(problems)))
    if name == "strains":  # the advice keeps its wishes and its open question for the reply; about a strain: nothing
        kept = strains_mod.topic_of(route)
        if not kept or not kept.get("advice"):
            return None
        return asking.Topic(qtype="strains", label=dict(kept.get("label") or {}), advice=dict(kept["advice"]))
    if name == "grow":
        metric = device = None
        if reason.startswith("state:metric:"):
            metric = reason.split(":", 2)[2]
        elif reason.startswith("state:device:"):
            device = reason.split(":", 2)[2]
        slots = route.retrieval.slots if route.retrieval is not None else None
        if metric is None and device is None and slots is not None:
            metric = (slots.metrics or [None])[0]
            device = None if metric else (slots.devices or [None])[0]
        if metric is None and device is None:
            return None
        return asking.Topic(qtype=route.qtype or "value", metric=metric, device=device,
                            label=asking.topic_label(gloss, metric=metric, fallback={"de": device or "",
                                                                                      "en": device or ""}))
    return None


# ---------- the eval bridge (tests/hank_eval/report.py) ----------

_SHARED: dict[str, Any] = {}


def shared_index() -> Index:
    """One lexical index per process for the eval set and the CLI, rebuilt when a card file changed."""
    index = _SHARED.get("index")
    if index is None:
        index = _SHARED["index"] = Index.load()
    else:
        index.refresh()
    return index


async def _dense_shared(embedder: Any) -> tuple[Index, router_mod.Router]:
    """One dense index and router per embedder for the eval set (WP11: scripts/hank_eval.py --dense): the card
    vectors and the off-topic examples embedded once, so the router decides with the dense threshold set exactly as
    the server does when the embedding model answers."""
    key = ("dense", id(embedder))
    found = _SHARED.get(key)
    if found is None:
        index = Index.load(embedder=embedder)
        await index.build_dense()
        found = (index, router_mod.Router(index))
        await found[1].warm(embedder)
        _SHARED[key] = found
    return found


async def eval_answer(question: str, *, lang: str, live: Mapping[str, Any], model: bool = False,
                      url: str | None = None, embedder: Any = None,
                      before: list[str] | None = None) -> dict[str, Any]:
    """The bridge of the eval set (contract in tests/hank_eval/report.py): one question against a fixture's live
    state. Offline (model False) it is the draft, no model call. With model True the Ollama at url (default
    OLLAMA_URL) polishes, and the answer is the polished version when it passed the validator. embedder: an Embedder
    (spec appendix A) for the dense search; None keeps the lexical index. Hank's own state goes to config.STATE_DIR,
    which the run isolates. before: the questions asked first in the same chat (the strain advice's two questions
    and their replies), answered from code without the model; only the last answer is scored."""
    client = ollama.OllamaClient(url=url) if model else None
    if embedder is not None:
        index, router = await _dense_shared(embedder)
    else:
        index, router = shared_index(), None
    hank = Hank(live=lambda _lang: dict(live), client=client, model=model, index=index)
    if router is not None:
        hank.router = router
    try:
        chat = "eval-chat" if before else None
        for earlier in before or []:
            await hank.ask_full(earlier, lang=lang, model=False, client_id=chat)
        asked = await hank.ask_full(question, lang=lang, model=model, client_id=chat)
        answer = asked.answer
        validation = None
        calls = 0
        turn = None
        if asked.job is not None and asked.job.task is not None:
            await asked.job.task
            answer = asked.job.answer or answer
            validation = asked.job.validation.report() if asked.job.validation else None
            calls = asked.job.model_calls
            turn = asked.job.model_turn
        hits = [h.passage.id for h in asked.route.retrieval.hits] if asked.route.retrieval is not None else []
        dense = asked.route.retrieval.dense if asked.route.retrieval is not None else None
        return {"route": asked.route.name, "text": answer.plain(), "hits": hits, "model_calls": calls,
                "ms": asked.ms, "validator": validation, "model": turn, "polished": not answer.draft_only,
                "note": asked.job.note if asked.job is not None else None, "dense": dense,
                "reason": asked.route.reason}
    finally:
        if client is not None:
            await client.close()


__all__ = ["MODES", "Asked", "Hank", "Job", "answer_dict", "eval_answer", "proposal_dict", "read_mode",
           "shared_index", "source_dict", "write_mode"]
