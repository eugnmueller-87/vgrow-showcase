"""Hank WP8: put together, the HTTP side and the CLI (spec 10.4 WP8, appendix B; addendum N0).

The fake Ollama of the eval set runs as a real HTTP server (aiohttp TestServer), so the client, the lane and the
error classes are the real ones. Live states are the eval fixtures F22 and F22-alt with the day frozen.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hank_eval import fixtures, report
from hank_eval.fake_ollama import FakeOllama
from vgrow import config, i18n
from vgrow.hank import api, core, ollama, stores, texts, wiring
from vgrow.hank import __main__ as cli

REPO = Path(__file__).resolve().parent.parent
HANK = REPO / "vgrow" / "hank"
WP8_FILES = [HANK / name for name in ("core.py", "api.py", "wiring.py", "__main__.py")] + [Path(__file__)]
CASES = {c["id"]: c for c in report.load_cases()}
EXPECTED = report.load_expected()
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
GROW_Q = "Ist die Luftfeuchte gerade okay?"  # hank.chip.1, grow on F22 (humidity 79 %, too high)


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def f22(name: str = "F22") -> Callable[[str], dict[str, Any]]:
    """The collector's moment for a fixture, without its ledger (Hank adds his own)."""

    def live(lang: str) -> dict[str, Any]:
        sources = fixtures.build(name, lang=lang).sources()
        sources.pop("ledger")
        return sources

    return live


def cite_first_fact(body: dict[str, Any]) -> str:
    """A model that cites the sheet's first fact with a marker and the words of its lead, without a number: passes the
    validator and keeps the draft's core facts. A bare "Das steht so in meiner Kartei [F1]." says nothing of what it
    cites and is a filler since the acceptance of 23.10.2026 (the draft stays)."""
    user = next(m["content"] for m in body["messages"] if m["role"] == "user")
    found = re.search(r"^\[(F\d+)\] (.*)$", user, re.M)
    marker = found.group(1) if found else "F1"
    lead = re.split(r"[:(\d.]", found.group(2))[0].strip(" ,;") if found else ""
    if "QUESTION" in user:
        return f"My card file says this about {lead} [{marker}]."
    return f"Meine Kartei sagt dazu: {lead} [{marker}]."


@contextlib.asynccontextmanager
async def fake_server(fake: FakeOllama) -> AsyncIterator[str]:
    server = TestServer(fake.app())
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


@contextlib.asynccontextmanager
async def hank_with(fake: FakeOllama | None, *, live: Callable[[str], dict[str, Any]] | None = None,
                    **options: Any) -> AsyncIterator[core.Hank]:
    if fake is None:
        yield core.Hank(live=live or f22(), index=core.shared_index(), **options)
        return
    async with fake_server(fake) as url:
        client = ollama.OllamaClient(url=url)
        try:
            yield core.Hank(live=live or f22(), client=client, index=core.shared_index(), **options)
        finally:
            await client.close()


@contextlib.asynccontextmanager
async def http(hank: core.Hank) -> AsyncIterator[TestClient]:
    app = web.Application()
    api.register(app, hank)
    async with TestClient(TestServer(app)) as client:
        yield client


async def settle(hank: core.Hank) -> None:
    """Wait for Hank's side tasks (a tick after a mode change, the unload, the embedding build)."""
    while hank._side:
        await asyncio.gather(*list(hank._side), return_exceptions=True)


async def until_done(hank: core.Hank, job_id: str) -> dict[str, Any]:
    job = hank.jobs[job_id]
    assert job.task is not None
    await asyncio.wait_for(job.task, 10)
    found = hank.job(job_id)
    assert found is not None and found["done"]
    return found


def question(case_id: str) -> tuple[str, str]:
    return CASES[case_id]["question"], CASES[case_id]["lang"]


# ---------- every route, end to end ----------

ROUTE_CASES = {"grow": "live-01", "no_data": "nodata-01", "offtopic": "off-05", "health": "health-01",
               "health_emergency": "health-03", "sale": "sale-01", "legal": "legal-01", "inject": "inject-02",
               "greet": "greet-01", "thanks": "thanks-01"}


def test_every_route_end_to_end_with_fake_ollama() -> None:
    fake = FakeOllama(reply=cite_first_fact)

    async def go() -> None:
        async with hank_with(fake) as hank, http(hank) as client:
            for route, case_id in ROUTE_CASES.items():
                q, lang = question(case_id)
                before = len(fake.calls("chat"))
                resp = await client.post("/api/hank/ask", json={"q": q, "client_id": "c1", "lang": lang})
                assert resp.status == 200, route
                data = await resp.json()
                if route != "grow":  # fixed answers: at once, never the model (spec 0.3 rule 4)
                    assert data["done"] is True and data["answer"]["route"] == route, (route, data)
                    assert data["answer"]["lang"] == lang and data["answer"]["text"], route
                    assert len(fake.calls("chat")) == before, route
                    continue
                assert data["done"] is False and data["draft"]["route"] == "grow" and data["draft"]["draft_only"]
                assert "79" in data["draft"]["text"]  # the draft is the answer already (addendum N0)
                final = await until_done(hank, data["id"])
                assert final["answer"]["draft_only"] is False and "Kartei" in final["answer"]["text"]
                assert final["answer"]["id"] == data["draft"]["id"]  # the same bubble, revised
                assert len(fake.calls("chat")) == before + 1
                body = fake.calls("chat")[-1]["body"]
                assert body["think"] is False and body["stream"] is False and "format" not in body
            # the router's other ends: a question back, empty, too long, off
            resp = await client.post("/api/hank/ask", json={"q": "Ist die Fehcte okay?", "client_id": "c2"})
            data = await resp.json()
            assert data["answer"]["route"] == "clarify" and data["answer"]["clarify"]["term"] == "Feuchte"
            for q, route in (("", "empty"), ("x" * 501, "too_long")):
                data = await (await client.post("/api/hank/ask", json={"q": q})).json()
                assert data["done"] and data["answer"]["route"] == route
            core.write_mode("off")
            data = await (await client.post("/api/hank/ask", json={"q": GROW_Q})).json()
            assert data["answer"]["route"] == "off" and data["answer"]["text"] == texts.say("hank.reply.off", "de")
        assert len(fake.calls("chat")) == 1  # one polish in all of it

    asyncio.run(go())
    turns = stores.TurnLog().all()
    assert {t["route"] for t in turns} >= set(ROUTE_CASES) | {"clarify", "empty", "too_long", "off"}
    for turn in turns:  # spec 8.5: no question text in the measurement log
        assert not set(turn) & set(stores.TURN_DROP)
        assert "Fehcte" not in json.dumps(turn) and "Luftfeuchte" not in json.dumps(turn)


def test_eval_bridge_answers_in_the_contract_shape() -> None:
    q, lang = question("live-01")
    live = fixtures.build("F22", lang=lang).sources()
    out = asyncio.run(core.eval_answer(q, lang=lang, live=live))
    # the contract of tests/hank_eval/report.py, plus what WP11 added for the model run and the dense eval
    assert set(out) == {"route", "text", "hits", "model_calls", "ms", "validator", "model", "polished", "note",
                        "dense", "reason"}
    assert out["model"] is None and out["dense"] == "off"
    assert out["route"] == "grow" and out["model_calls"] == 0 and out["validator"] is None
    assert out["hits"] and all("#" in h for h in out["hits"])
    assert "79" in out["text"] and "\nQuellen: " in out["text"]  # Answer.plain(): text plus the source line
    assert out["ms"] < 1000  # addendum N0: router, search and draft under 1 s


def test_eval_bridge_with_the_model_polishes() -> None:
    fake = FakeOllama(reply=cite_first_fact)
    q, lang = question("live-01")
    live = fixtures.build("F22", lang=lang).sources()

    async def go() -> dict[str, Any]:
        async with fake_server(fake) as url:
            return await core.eval_answer(q, lang=lang, live=live, model=True, url=url)

    out = asyncio.run(go())
    assert out["route"] == "grow" and out["model_calls"] == 1
    assert out["validator"]["kept"] == 1 and "Kartei" in out["text"]


# ---------- jobs ----------

def test_job_polling_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOllama(reply=cite_first_fact)
    fake.delay["chat"] = 0.3
    seen: list[str] = []
    real = core.validator_mod.validate

    def watch(model_text: str, ctx: Any, lang: str | None = None) -> Any:
        seen.extend(j.stage for j in hank_ref[0].jobs.values() if not j.done)
        return real(model_text, ctx, lang)

    monkeypatch.setattr(core.validator_mod, "validate", watch)
    hank_ref: list[core.Hank] = []

    async def go() -> None:
        async with hank_with(fake) as hank, http(hank) as client:
            hank_ref.append(hank)
            data = await (await client.post("/api/hank/ask", json={"q": GROW_Q, "client_id": "c1"})).json()
            job_id = data["id"]
            await asyncio.sleep(0.1)
            first = await (await client.get(f"/api/hank/job/{job_id}")).json()
            assert first["done"] is False and first["stage"] == "model" and first["answer"] is None
            assert first["draft"]["text"] == data["draft"]["text"] and first["draft"]["draft_only"]
            assert first["found"] and all(not re.search(r"\d", t) for t in first["found"])
            assert isinstance(first["elapsed_s"], float)
            final = await until_done(hank, job_id)
            again = await (await client.get(f"/api/hank/job/{job_id}")).json()
            assert again == final and again["stage"] == "done" and again["answer"]["draft_only"] is False
            assert (await client.get("/api/hank/job/nope")).status == 404
            # cancelling: the draft stays the answer
            fake.delay["chat"] = 5
            data = await (await client.post("/api/hank/ask", json={"q": GROW_Q})).json()
            await asyncio.sleep(0.05)
            assert (await client.delete(f"/api/hank/job/{data['id']}")).status == 200
            gone = await (await client.get(f"/api/hank/job/{data['id']}")).json()
            assert gone["done"] and gone["answer"]["draft_only"] and gone["answer"]["text"] == data["draft"]["text"]

    asyncio.run(go())
    assert seen == ["check"]  # the validator runs in the stage "check"


def test_queue_bound() -> None:
    """Spec 4.9: one answer runs, at most two wait; a fourth gets the draft at once. The draft is a whole answer, so
    it carries no "try again in a moment" note: the page shows its "facts only" line (screens review, finding 12)."""
    fake = FakeOllama(reply=cite_first_fact)
    fake.delay["chat"] = 5

    async def go() -> None:
        async with hank_with(fake) as hank:
            first = [await hank.ask(GROW_Q) for _ in range(3)]
            assert all(r["done"] is False for r in first)
            await asyncio.sleep(0.1)
            assert len(fake.calls("chat")) == 1 and hank.lane.holder == "chat" and len(hank.lane.waiting) == 2
            fourth = await hank.ask(GROW_Q)
            assert fourth["done"] is True and fourth["answer"]["draft_only"]
            assert fourth["answer"]["note"] is None and fourth["answer"]["text"]
            assert texts.say("hank.reply.queue_full", "de") not in fourth["answer"]["text"]
            for r in first:
                await hank.cancel(r["id"])
            assert not hank.lane.busy and not hank.lane.waiting
            assert len(fake.calls("chat")) == 1

    asyncio.run(go())


def test_draft_when_model_missing_or_down_or_photo_busy_or_slow() -> None:
    def note(key: str, model: str = ollama.DEFAULT_CHAT_MODEL) -> str:
        return texts.say(key, "de", model=model)

    async def polished(hank: core.Hank) -> dict[str, Any]:
        data = await hank.ask(GROW_Q)
        assert data["done"] is False
        final = await until_done(hank, data["id"])
        assert final["answer"]["draft_only"] and final["answer"]["text"] == data["draft"]["text"]
        return final["answer"]

    async def go() -> None:
        # the model is missing on the computer
        missing = FakeOllama(models=(ollama.DEFAULT_EMBED_MODEL,))
        async with hank_with(missing) as hank:
            assert (await polished(hank))["note"] == note("hank.reply.model_missing")
            await hank.health.get()  # once the health knows it, the model is not even asked
            data = await hank.ask(GROW_Q)
            assert data["done"] and data["answer"]["note"] == note("hank.reply.model_missing")
            assert "ollama pull gemma4:12b" in data["answer"]["note"]
        # Ollama does not answer
        down = FakeOllama()
        down.fail["chat"] = "no_ollama"
        async with hank_with(down) as hank:
            assert (await polished(hank))["note"] == note("hank.reply.model_down")
            down.fail["version"] = "no_ollama"
            await hank.health.get(force=True)
            calls = len(down.calls("chat"))
            data = await hank.ask(GROW_Q)
            assert data["done"] and data["answer"]["note"] == note("hank.reply.model_down")
            assert len(down.calls("chat")) == calls
        # the photo reading holds the lane: the chat never waits behind it (spec 4.9)
        photo = FakeOllama(reply=cite_first_fact)
        async with hank_with(photo) as hank:
            async with hank.lane.hold("photo"):
                data = await hank.ask(GROW_Q)
            assert data["done"] and data["answer"]["note"] == note("hank.reply.busy_photo")
            assert data["answer"]["draft_only"] and photo.calls("chat") == []
        # too slow: the chat's time limit ran out
        slow = FakeOllama()
        async with hank_with(slow) as hank:
            async def too_slow(messages: Any, **_: Any) -> Any:
                raise ollama.OllamaError("timeout", "timed out after 240 s")

            hank.client.chat = too_slow  # type: ignore[method-assign, union-attr]
            assert (await polished(hank))["note"] == note("hank.reply.slow")
        # a model answer that fails the validator: the draft stays, without a note
        wrong = FakeOllama(reply="Die Luftfeuchte liegt bei 55 % [F1].")
        async with hank_with(wrong) as hank:
            answer = await polished(hank)
            assert answer["note"] is None
            turn = stores.TurnLog().all()[-1]
            assert turn["validator"]["used_draft"] and turn["polished"] is False

    asyncio.run(go())


def test_photo_reading_waits_behind_a_running_chat() -> None:
    """Spec 4.9: the photo reading (WP9 wraps it in lane.hold("photo")) waits until the chat's model call is over."""
    fake = FakeOllama(reply=cite_first_fact)
    fake.delay["chat"] = 0.3
    seen: list[int] = []

    async def go() -> None:
        async with hank_with(fake) as hank:
            data = await hank.ask(GROW_Q)
            await asyncio.sleep(0.05)
            job = hank.jobs[data["id"]]

            async def photo() -> None:
                async with hank.lane.hold("photo"):
                    seen.append(job.model_calls)

            waiting = asyncio.create_task(photo())
            await asyncio.sleep(0.05)
            assert hank.lane.holder == "chat" and hank.lane.waiting == ("photo",)
            await until_done(hank, data["id"])
            await waiting

    asyncio.run(go())
    assert seen == [1]


# ---------- modes ----------

def test_mode_off_and_warnings() -> None:
    fake = FakeOllama(reply=cite_first_fact)

    async def go() -> None:
        async with hank_with(fake) as hank, http(hank) as client:
            i18n.set_server_lang("de")
            await hank.tick()
            state = await (await client.get("/api/hank/state")).json()
            assert state["mode"] == "on" and state["light"]["level"] == "yellow"  # F22: humidity too high
            assert state["scene"]["id"] == "umbrella" and state["inbox"]["unread"] == 1
            assert set(state) == {"mode", "light", "scene", "ollama", "chat_model", "embed", "index", "inbox", "menu"}
            # nur Warnungen: no chat, the light stays, no scene, the model unloads
            assert await (await client.post("/api/hank/mode", json={"mode": "warnings"})).json() == {
                "mode": "warnings"}
            await settle(hank)
            unload = fake.calls("chat")[-1]["body"]
            assert unload["messages"] == [] and unload["keep_alive"] == 0
            data = await (await client.post("/api/hank/ask", json={"q": GROW_Q})).json()
            assert data["answer"]["text"] == texts.say("hank.reply.warnings_only", "de")
            await hank.tick()
            state = await (await client.get("/api/hank/state")).json()
            assert state["mode"] == "warnings" and state["light"]["level"] == "yellow" and state["scene"] is None
            # aus: no chat, no light, no notes; the switch back stays reachable
            await client.post("/api/hank/mode", json={"mode": "off"})
            assert await hank.tick() is None
            state = await (await client.get("/api/hank/state")).json()
            assert state["mode"] == "off" and state["light"] is None and state["inbox"]["items"] == []
            data = await (await client.post("/api/hank/ask", json={"q": GROW_Q})).json()
            assert data["answer"]["route"] == "off"
            assert (await client.post("/api/hank/mode", json={"mode": "loud"})).status == 400
            await client.post("/api/hank/mode", json={"mode": "on"})
            await settle(hank)
            state = await (await client.get("/api/hank/state")).json()
            assert state["mode"] == "on" and state["light"]["level"] == "yellow"
        assert [c for c in fake.calls("chat") if c["body"]["messages"]] == []  # no question reached the model

    asyncio.run(go())
    raw = (config.STATE_DIR / "settings.json").read_bytes()
    data = json.loads(raw)
    assert data == {"lang": "de", "hank_mode": "on"}  # the page language stays (spec 7, addendum N8)
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw


# ---------- clarify, feedback, gaps, cards ----------

def test_clarify_endpoint() -> None:
    async def go() -> None:
        async with hank_with(None) as hank, http(hank) as client:
            data = await (await client.post("/api/hank/ask", json={"q": "Ist die Fehcte okay?",
                                                                    "client_id": "c1"})).json()
            clarify = data["answer"]["clarify"]
            assert data["answer"]["text"] == "Meinst du Feuchte?" and clarify["token"] == "Fehcte"
            yes = await (await client.post("/api/hank/clarify", json={"id": clarify["id"], "answer": "yes"})).json()
            assert yes["done"] and yes["answer"]["route"] == "grow" and "79" in yes["answer"]["text"]
            learned = json.loads((stores.hank_dir() / "aliases-learned.json").read_text(encoding="utf-8"))
            assert learned["learned"]["fehcte"]["term"] == "Feuchte"  # addendum N7: only an explicit yes
            # the learned spelling answers at once next time, for anyone
            again = await (await client.post("/api/hank/ask", json={"q": "Ist die Fehcte okay?",
                                                                     "client_id": "c9"})).json()
            assert again["answer"]["route"] == "grow"
            # an unknown or expired id asks for the question again; a bad answer is a 400
            late = await (await client.post("/api/hank/clarify", json={"id": "gone", "answer": "yes"})).json()
            assert late["answer"]["text"] == texts.say("hank.reply.clarify_no", "de")
            assert (await client.post("/api/hank/clarify", json={"id": "gone", "answer": "maybe"})).status == 400

    asyncio.run(go())


def test_clarify_no_is_remembered() -> None:
    async def go() -> None:
        async with hank_with(None) as hank, http(hank) as client:
            data = await (await client.post("/api/hank/ask", json={"q": "Ist die Fehcte okay?",
                                                                    "client_id": "c2"})).json()
            clarify = data["answer"]["clarify"]
            no = await (await client.post("/api/hank/clarify", json={"id": clarify["id"], "answer": "nein"})).json()
            assert no["answer"]["route"] == "clarify" and no["answer"]["clarify"] is None
            assert no["answer"]["text"] == texts.say("hank.reply.clarify_no", "de")
            stored = json.loads((stores.hank_dir() / "aliases-learned.json").read_text(encoding="utf-8"))
            assert stored["rejected"] == {"fehcte": ["Feuchte"]} and stored["learned"] == {}
            again = await (await client.post("/api/hank/ask", json={"q": "Ist die Fehcte okay?",
                                                                     "client_id": "c3"})).json()
            assert again["answer"]["route"] != "clarify"  # never proposed again (spec 3.4)
        async with hank_with(None) as later:  # a new run reads the rejection from the file
            data = await later.ask("Ist die Fehcte okay?", client_id="c4")
            assert data["answer"]["route"] != "clarify"

    asyncio.run(go())


def test_feedback_and_gaps_endpoints() -> None:
    async def go() -> None:
        async with hank_with(None) as hank, http(hank) as client:
            q, lang = question("nodata-01")
            data = await (await client.post("/api/hank/ask", json={"q": q, "lang": lang})).json()
            assert data["answer"]["route"] == "no_data" and data["answer"]["gap"]
            gaps = (await (await client.get("/api/hank/gaps")).json())["gaps"]
            assert [g["question"] for g in gaps if g["kind"] == "no_card"] == [q]
            assert gaps[0]["term"] == "Tomate"
            ok = await client.post("/api/hank/feedback", json={"answer_id": data["answer"]["id"], "vote": "down"})
            assert ok.status == 200 and await ok.json() == {"ok": True}
            assert (await client.post("/api/hank/feedback", json={"answer_id": "x", "vote": "meh"})).status == 400
            assert (await client.post("/api/hank/feedback", data="not json")).status == 400
            grow = await (await client.post("/api/hank/ask", json={"q": GROW_Q})).json()
            await client.post("/api/hank/feedback", json={"answer_id": grow["answer"]["id"], "vote": "up"})

    asyncio.run(go())
    lines = stores.FeedbackStore().all()
    assert [(f["vote"], f["route"]) for f in lines] == [("down", "no_data"), ("up", "grow")]
    assert lines[0]["question"] == question("nodata-01")[0] and lines[1]["hits"]
    assert not (stores.hank_dir() / "aliases-learned.json").exists()  # a thumb teaches no spelling (N7)
    assert len(stores.FeedbackStore().to_eval()) == 2


def test_own_experience_has_its_own_sentence() -> None:
    """Spec 8.4: own experience joins an answer in a sentence of its own, never as a card value."""
    stores.ExperienceStore().add("In der frühen Blüte lief die Abluft bei hoher Luftfeuchte auf Stufe 5 gut.",
                                 stage="early_flower", run=1)

    async def go() -> dict[str, Any]:
        hank = core.Hank(live=f22())  # its own index: the experience file is read when the index is built
        return await hank.ask("Wie hoch soll die Abluft bei hoher Luftfeuchte in der frühen Blüte?")

    data = asyncio.run(go())
    text = data["answer"]["text"]
    assert "Aus deiner eigenen Erfahrung (Durchgang 1): In der frühen Blüte lief die Abluft" in text
    assert "Eigene Erfahrung" in [s["title"] for s in data["answer"]["sources"]]


def test_a_kept_lesson_reaches_hank_and_a_deleted_one_is_gone() -> None:
    """The owner's lessons (vgrow/lessons.py) live in this store: Hank names the variety, a delete really removes it."""
    store = stores.ExperienceStore()
    kept = store.add("In der frühen Blüte lief die Abluft bei hoher Luftfeuchte auf Stufe 5 gut.",
                     run="Purple Punch", plant="plant-1", variety="Purple Punch")
    question_ = "Wie hoch soll die Abluft bei hoher Luftfeuchte in der frühen Blüte?"

    async def go() -> str:
        return (await core.Hank(live=f22()).ask(question_))["answer"]["text"]

    assert "Aus deiner eigenen Erfahrung (Durchgang Purple Punch): In der frühen Blüte" in asyncio.run(go())
    assert store.change(kept["id"], "In der frühen Blüte lief die Abluft bei hoher Luftfeuchte auf Stufe 3 gut.")["text"].endswith("gut.")
    # A question asked again right away gets a shorter answer without this line, so each state is asked twice.
    assert any("Luftfeuchte auf Stufe 3 gut" in asyncio.run(go()) for _ in range(2))
    assert store.remove(kept["id"]) is True and store.items() == []
    assert not any("eigenen Erfahrung" in asyncio.run(go()) for _ in range(2))
    assert store.remove(kept["id"]) is False and store.change(kept["id"], "x") is None


def test_declined_card_tells_the_consequence_once_and_journals_it() -> None:
    async def go() -> None:
        async with hank_with(None) as hank, http(hank) as client:
            data = await (await client.post("/api/hank/ask", json={"q": GROW_Q})).json()
            cards = {c["body"]["action"]: c for c in data["answer"]["proposals"]}
            assert cards["dfan"]["body"] == {"action": "dfan", "step": 4} and "reason" not in cards["dfan"]
            assert hank.ledger.items(active=True)[0]["recommendation"]["proposal_id"] == cards["dfan"]["id"]
            out = await (await client.post("/api/hank/proposal", json={"id": cards["dfan"]["id"],
                                                                        "outcome": "declined"})).json()
            text = out["message"]["text"]
            assert "71 %" in text and text.endswith(i18n.t("hank.ledger.respect", "de"))
            assert "S17" in [s["id"] for s in out["message"]["sources"]]
            assert hank.ledger.items()[0]["state"] == "declined"
            again = await (await client.post("/api/hank/ask", json={"q": GROW_Q})).json()
            assert all(c["body"]["action"] != "dfan" for c in again["answer"]["proposals"])  # not offered again
            assert (await client.post("/api/hank/proposal", json={"id": "x", "outcome": "maybe"})).status == 400

    asyncio.run(go())
    (line,) = stores.Journal().all()
    assert (line["origin"], line["action"], line["body"]) == ("hank_declined", "dfan", {"action": "dfan", "step": 4})


def test_journal_wrap_records_origin_and_never_breaks_a_command() -> None:
    seen: list[dict[str, Any]] = []

    async def command(request: web.Request) -> web.Response:
        body = await request.json()
        seen.append(body)
        if body.get("action") == "boom":
            raise web.HTTPBadRequest(text="boom")
        return web.json_response({"ok": True}, status=409 if body.get("refuse") else 200)

    async def go() -> None:
        async with hank_with(None) as hank:
            data = await hank.ask(GROW_Q)
            card = next(c for c in data["answer"]["proposals"] if c["body"]["action"] == "dfan")
            app = web.Application()
            app.router.add_post("/api/cmd", api.journal_wrap(command, hank))
            async with TestClient(TestServer(app)) as client:
                assert (await client.post("/api/cmd", json={**card["body"], "refuse": True})).status == 409
                # a card whose body was changed, or an id Hank never handed out, is held back before the command
                # (core.precheck_command, review 2, S2): 409 with the reason, the cockpit's handler is not called
                changed = await client.post("/api/cmd", json={**card["body"], "proposal": card["id"], "refuse": True})
                assert changed.status == 409 and "verändert" in (await changed.json())["error"]
                assert (await client.post("/api/cmd", json={**card["body"], "proposal": card["id"]})).status == 200
                assert (await client.post("/api/cmd", json={"action": "light", "level": 70})).status == 200
                assert (await client.post("/api/cmd", json={"action": "dfan", "step": 9,
                                                            "proposal": "p-forged"})).status == 409
                assert (await client.post("/api/cmd", json={"action": "refresh"})).status == 200
                assert (await client.post("/api/cmd", json={"action": "boom"})).status == 400
                assert (await client.post("/api/cmd", data="no json")).status == 500
                hank.stores.journal.add = None  # type: ignore[assignment]  # a broken journal: the command still runs
                assert (await client.post("/api/cmd", json={"action": "light", "level": 75})).status == 200
            assert hank.ledger.items()[0]["state"] == "applied"  # the tapped card moved its entry (spec 6.2)

    asyncio.run(go())
    lines = stores.Journal().all()
    assert [(e["origin"], e["action"], e["ok"]) for e in lines] == [
        ("human", "dfan", False), ("hank_accepted", "dfan", True), ("human", "light", True)]
    assert lines[1]["proposal"] and lines[2]["proposal"] is None and "proposal" not in lines[1]["body"]
    assert len(seen) == 6  # every readable command reached the cockpit's handler, except the two held-back cards


# ---------- the ticker ----------

def test_ticker_takes_automation_lines_live_passages_and_notes() -> None:
    async def go() -> None:
        async with hank_with(None) as hank:
            first = await hank.tick()
            assert first is not None and first.level == "yellow"
            again = await hank.tick()
            assert again is not None and again.messages == []  # one note per reason (cooldown)
            found = hank.index.search("Wasser", kinds=["live"])  # addendum N2: live/<group>
            assert found.hits and all(h.passage.id.startswith("live/") for h in found.hits)
            assert hank.index.passage("live/climate") is not None

    asyncio.run(go())
    journal = stores.Journal().all()
    assert {e["action"] for e in journal} == {"automation_on", "paused_unconfirmed_stage"}
    assert {e["origin"] for e in journal} == {"automation"}  # addendum N4
    notes = [m for m in stores.ChatRing().messages() if m.get("kind") == "nudge"]
    assert len(notes) == 1 and set(notes[0]["text"]) == {"de", "en"}


def test_ticker_and_answers_survive_broken_parts() -> None:
    def broken(lang: str) -> dict[str, Any]:
        raise RuntimeError("collector down")

    async def go() -> None:
        async with hank_with(None, live=broken) as hank:
            assert (await hank.tick()) is not None  # no readings at all: yellow, never a crash
            data = await hank.ask(GROW_Q)
            assert data["done"] and data["answer"]["route"] == "grow" and data["answer"]["text"]
            assert "live" in hank.errors

    asyncio.run(go())


def test_start_builds_the_index_and_runs_the_ticker() -> None:
    async def go() -> None:
        hank = core.Hank(live=f22(), tick_seconds=0.05)
        task = await hank.start()
        data = await hank.ask(GROW_Q)  # waits for the index, then answers
        assert data["done"] and data["answer"]["route"] == "grow"
        for _ in range(100):
            if hank.nudge is not None:
                break
            await asyncio.sleep(0.02)
        assert hank.nudge is not None and hank.nudge.level == "yellow"
        await hank.stop()
        assert task.done()

    asyncio.run(go())


# ---------- demo, the box, the network ----------

class ExplodingBox:
    """A box that hands out the F22 snapshot and fails loudly on anything else."""

    demo = False

    def __init__(self, *, snapshot_fails: bool = False) -> None:
        self.touched: list[str] = []
        self.snapshot_fails = snapshot_fails

    def snapshot(self) -> dict[str, Any]:
        if self.snapshot_fails:
            raise RuntimeError("no shadow")
        return fixtures.snapshot()

    def __getattr__(self, name: str) -> Any:
        self.touched.append(name)
        raise AssertionError(f"Hank touched box.{name}")


def _collector(box: Any) -> wiring.Collector:
    from vgrow.alerts import Alerts
    from vgrow.automation import HumidifierAutomation
    from vgrow.plant import PlantLog

    fixtures.build("F22")  # writes the fixture's plant log and automation state
    log = PlantLog()
    return wiring.Collector(box, plant_log=log, automation=HumidifierAutomation(box, None, log), alerts=Alerts(None),
                            clock=lambda: fixtures.F22_NOW)


def test_collector_reads_late_parts_and_leaves_out_broken_ones() -> None:
    from types import SimpleNamespace

    class BrokenCamera:
        def status(self) -> dict[str, Any]:
            raise RuntimeError("camera gone")

    parts: dict[str, Any] = {"camera": None}
    live = _collector(ExplodingBox())
    live._camera = parts  # the server's parts: camera and vision come after box.start()
    live._vision = parts
    first = live("de")
    assert first["camera"] == {"configured": False} and first["vision"] == {"busy": False}
    assert set(first) == set(wiring.PARTS)
    assert first["plant_view"]["stage"]["key"] == "early_flower" and first["feeding_view"]["state"] == "draft"
    assert first["automation_view"]["needs_stage_confirmation"] and first["alerts_view"]
    parts["camera"] = BrokenCamera()
    parts["vision"] = SimpleNamespace(busy=True)
    later = live("en")
    assert later["camera"] == {} and later["vision"] == {"busy": True} and "camera" in live.failures
    assert later["plant_view"]["stage"]["label"] != first["plant_view"]["stage"]["label"]  # views in the language


def test_full_cycle_with_exploding_box_never_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    from vgrow.automation import HumidifierAutomation
    from vgrow.plant import PlantLog

    box = ExplodingBox()
    live = _collector(box)  # the fixture writes its plant log here, before the watch starts
    calls: list[str] = []
    for cls, names in ((HumidifierAutomation, ("set_enabled", "manual_switch", "check")),
                       (PlantLog, ("set_stage", "add_water", "add_height"))):
        for name in names:
            monkeypatch.setattr(cls, name, lambda *a, _n=name, **k: calls.append(_n))

    async def go() -> None:
        async with hank_with(None, live=live) as hank, http(hank) as client:
            await hank.tick()
            for q in (GROW_Q, "Dreh bitte das Licht auf 100 Prozent hoch", "Mach die Abluft höher",
                      "Schalte den Befeuchter aus", "Bestätige die Phase"):
                data = await (await client.post("/api/hank/ask", json={"q": q})).json()
                for card in data["answer"]["proposals"]:
                    assert card["body"]["action"] in ("light", "cfan", "dfan", "port", "plant_stage",
                                                      "plant_apply", "auto_humidifier")
                    await client.post("/api/hank/proposal", json={"id": card["id"], "outcome": "declined"})
            await client.get("/api/hank/state")
            await hank.tick()
        box.snapshot_fails = True
        async with hank_with(None, live=live) as hank:
            assert await hank.tick() is not None
            data = await hank.ask(GROW_Q)
            assert data["done"] and data["answer"]["text"]

    asyncio.run(go())
    assert box.touched == [] and calls == []


def test_demo_mode_runs_without_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    from vgrow.automation import HumidifierAutomation
    from vgrow.demo import DemoBox, setup_demo
    from vgrow.plant import PlantLog

    async def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"network in the demo: {args[1:3]}")

    box = DemoBox()
    log = PlantLog()
    automation = HumidifierAutomation(box, None, log)
    camera, vision = setup_demo(log, automation)
    live = wiring.Collector(box, camera=camera, plant_log=log, automation=automation, vision=vision)
    client = ollama.OllamaClient(url="http://127.0.0.1:9")

    async def go() -> None:
        monkeypatch.setattr(aiohttp.ClientSession, "_request", no_network)
        hank = core.Hank(live=live, client=client, demo=True)
        assert hank.client is None and not hank.use_model
        await hank.tick()
        data = await hank.ask(GROW_Q)
        assert data["done"] and data["answer"]["route"] == "grow" and data["answer"]["draft_only"]
        assert data["answer"]["note"] is None
        state = hank.state("de")
        assert state["ollama"] == "off" and state["light"]["level"] in ("green", "yellow", "red")
        await hank.set_mode("warnings")
        await hank.stop()

    asyncio.run(go())


def test_only_ollama_host_is_contacted(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOllama(reply=cite_first_fact)
    hosts: list[str] = []
    real = aiohttp.ClientSession._request

    async def record(self: aiohttp.ClientSession, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        parts = urlsplit(str(url))
        hosts.append(f"{parts.hostname}:{parts.port}")
        return await real(self, method, url, *args, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "_request", record)

    async def go() -> str:
        async with fake_server(fake) as url:
            client = ollama.OllamaClient(url=url)
            hank = core.Hank(live=f22(), client=client)  # its own index, with the embedder
            try:
                await hank.tick()  # health, then the embedding build in the background
                await settle(hank)
                assert hank.index.dense_state() == "used"
                data = await hank.ask(GROW_Q)
                await until_done(hank, data["id"])
                await hank.ask("Moin Hank")
                await hank.set_mode("off")
                await settle(hank)
            finally:
                await client.close()
            parts = urlsplit(url)
            return f"{parts.hostname}:{parts.port}"

    allowed = asyncio.run(go())
    assert hosts and set(hosts) == {allowed}
    endpoints = {r["endpoint"] for r in fake.requests}
    assert endpoints == {"version", "tags", "ps", "embed", "chat"}


def test_the_router_is_warmed_when_the_card_vectors_came_from_the_cache() -> None:
    """02.11.2026: the card vectors came from the cache at start, _maybe_dense saw "used" and stopped, so on the Mac
    the off-topic bank never had vectors."""

    class CachedIndex:
        embedder = object()
        started = 0

        def dense_state(self) -> str:
            return "used"

        async def start(self) -> str:
            self.started += 1
            return "used"

    class Router:
        def __init__(self, ok: bool) -> None:
            self.ok, self.warmed = ok, 0

        async def warm(self) -> bool:
            self.warmed += 1
            return self.ok

    async def go() -> None:
        hank = core.Hank(index=CachedIndex())  # type: ignore[arg-type]
        hank.router = Router(ok=False)  # type: ignore[assignment]
        hank._maybe_dense(1000.0)
        await settle(hank)
        assert hank.router.warmed == 1 and hank.index.started == 0 and not hank._warm  # type: ignore[union-attr]
        hank._maybe_dense(1000.0 + 60)  # failed: tried again only after the retry time
        await settle(hank)
        assert hank.router.warmed == 1  # type: ignore[union-attr]
        hank.router.ok = True  # type: ignore[union-attr]
        hank._maybe_dense(1000.0 + core.DENSE_RETRY_SECONDS + 1)
        await settle(hank)
        assert hank.router.warmed == 2 and hank._warm  # type: ignore[union-attr]
        hank._maybe_dense(1000.0 + 3 * core.DENSE_RETRY_SECONDS)  # warm: nothing more to do
        await settle(hank)
        assert hank.router.warmed == 2 and hank.index.started == 0  # type: ignore[union-attr]

    asyncio.run(go())


def test_hank_has_no_box_parameter() -> None:
    params = inspect.signature(core.Hank.__init__).parameters
    assert "box" not in params and not any("box" in name for name in params)
    assert set(params) >= {"live", "client", "lane", "demo"}
    source = (HANK / "core.py").read_text(encoding="utf-8")
    assert ".snapshot(" not in source  # the box is read in wiring.py only


def test_no_actuator_names_anywhere_in_hank_package() -> None:
    from vgrow.automation import HumidifierAutomation
    from vgrow.box import Box
    from vgrow.camera import Camera
    from vgrow.demo import DemoBox
    from vgrow.plant import PlantLog

    names = {m for cls in (Box, DemoBox, HumidifierAutomation, PlantLog, Camera) for m in vars(cls)
             if m.startswith("set_")}
    names |= {"_send", "refresh_shadow", "manual_switch", "add_water", "add_height", "log_feeding",
              "start_recording", "stop_recording", "take_photo", "notify_listeners", "set_server_lang", "push"}
    assert {"set_light", "set_port", "set_dfan", "set_enabled", "set_stage"} <= names
    pattern = re.compile(r"[.\"']\s*(" + "|".join(sorted(map(re.escape, names))) + r")\b")
    found = []
    for path in sorted(HANK.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                found.append(f"{path.name}:{number}: {line.strip()}")
    assert found == []


# ---------- the CLI ----------

def test_cli_ask_no_model(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the CLI with --no-model called the network")

    monkeypatch.setattr(aiohttp.ClientSession, "_request", no_network)
    assert cli.main(["ask", "--no-model", "Welche", "Werte", "brauche", "ich", "in", "der", "frühen", "Blüte?"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[grow] ") and "Quellen:" in out
    assert cli.main(["ask", "--no-model", "--lang", "de", "Wie", "viel", "Licht", "braucht", "eine", "Tomate?"]) == 0
    assert capsys.readouterr().out.startswith("[no_data] Dazu steht noch nichts in meiner Kartei.")
    assert cli.main(["gaps", "--top", "5"]) == 0
    assert "Tomate" in capsys.readouterr().out
    assert cli.main(["gaps", "export"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["gaps"] and "words" not in exported["gaps"][0]
    assert cli.main(["experience", "add", "Nach", "dem", "Umtopfen", "zwei", "Tage", "Pause.", "--stage", "veg",
                     "--run", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "own"
    assert cli.main(["feedback-to-eval"]) == 0
    assert "0 candidates" in capsys.readouterr().out
    assert cli.main(["gaps", "resolve"]) == 0
    assert "0 gaps answered" in capsys.readouterr().out
    assert cli.main(["reindex", "--lexical"]) == 0
    assert json.loads(capsys.readouterr().out)["passages"] >= 90
    assert cli.main(["bench", "--runs", "1"]) == 0
    assert "p95" in capsys.readouterr().out
    assert cli.main(["ask", "--no-model", "--demo", "Ist", "die", "Luftfeuchte", "gerade", "okay?"]) == 0
    assert capsys.readouterr().out.startswith("[grow] ")
    assert config.STATE_DIR.name == "demo"  # the demo never writes the real run's state


# ---------- files ----------

def test_wp8_files_have_no_em_dash_no_bom_and_lf() -> None:
    for path in WP8_FILES:
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), path.name
        assert EM_DASH not in data.decode("utf-8"), path.name
        assert b"\r\n" not in data, path.name


def test_api_answers_503_when_hank_breaks() -> None:
    async def go() -> None:
        async with hank_with(None) as hank, http(hank) as client:
            hank.state = None  # type: ignore[assignment, method-assign]
            resp = await client.get("/api/hank/state")
            assert resp.status == 503 and "error" in await resp.json()
            assert (await client.post("/api/hank/ack", json={"upto": "x"})).status == 400
            assert (await client.post("/api/hank/ack", json={"upto": 3})).status == 200

    asyncio.run(go())
