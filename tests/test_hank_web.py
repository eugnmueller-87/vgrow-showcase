"""Hank's page part (WP10): web/hank.js, hank-i18n.js, hank.css, the three lines in index.html, the preview.

Same rules as the cockpit's own texts (tests/test_web_i18n.py, whose readers are reused here): every text is a key,
written out in full in hank.js, in German and English, English without German words, no em dash anywhere.
On top: no innerHTML, hank.js loads after app.js, the preview carries Hank and its mock answers his paths.
Part b: the page against the real API. A real Hank (fixture F22, a fake Ollama over HTTP) answers every path the
page uses; the fields hank.js reads must be there, and the preview's mock must send the same shapes and the server's
own fixed texts. The mock checks run in node when node is installed, and are skipped otherwise.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import TestClient, TestServer

import test_web_i18n as web
from hank_eval import fixtures
from hank_eval.fake_ollama import FakeOllama
from vgrow.hank import api, core, ollama, texts

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
SCRIPTS = REPO / "scripts"
HANK_FILES = ("hank.js", "hank-i18n.js", "hank.css")
HANK_PATH = re.compile(r"/api/hank/[a-z]+/?")


def hank_js() -> str:
    return (WEB / "hank.js").read_text(encoding="utf-8")


def hank_texts() -> dict[str, dict[str, str]]:
    return web._texts_file("hank-i18n.js", web.HANK_PREFIX)


def hank_strings() -> list[tuple[str, int]]:
    return web.js_strings(hank_js())


def test_hank_keys_exist_and_are_used() -> None:
    texts = hank_texts()
    assert set(texts) == {"de", "en"}
    de, en = texts["de"], texts["en"]
    assert set(de) == set(en), set(de) ^ set(en)
    for key, text in de.items():
        assert key.startswith("hank."), key
        assert web.KEY_LIKE.fullmatch(key), key
        assert text.strip() and en[key].strip(), key
        assert set(web.PLACEHOLDER.findall(text)) == set(web.PLACEHOLDER.findall(en[key])), key
    keys = set(de)
    src = hank_js()
    literals = {text for text, _ in hank_strings()}
    # every key is written out in full in hank.js (a plural by its base, as tn() takes it)
    unused = sorted(k for k in keys if k not in literals and k.rpartition(".")[0] not in literals)
    assert not unused, f"hank texts nobody shows: {unused}"
    # every key hank.js names exists, as a t() call, a tn() call or an entry of a lookup table
    for key in re.findall(r"\bt\(\s*\"([^\"]+)\"", src):
        assert key in keys, f't("{key}") has no text'
    for key in re.findall(r"\btn\(\s*\"([^\"]+)\"", src):
        assert {f"{key}.one", f"{key}.other"} <= keys, f'tn("{key}") needs .one and .other'
    for text, line in hank_strings():
        if text.startswith("hank.") and web.KEY_LIKE.fullmatch(text):
            assert web.known(text, keys), f"hank.js line {line}: {text!r} is not in hank-i18n.js"


def test_hank_en_has_no_german_words() -> None:
    german = {key: text for key, text in hank_texts()["en"].items() if web.GERMAN.search(text)}
    assert not german, german


def test_hank_js_has_no_german_or_em_dash() -> None:
    found = [(line, text) for text, line in hank_strings() if web.GERMAN.search(text) or web.EM_DASH in text]
    assert not found, found
    for name in HANK_FILES:
        assert web.EM_DASH not in (WEB / name).read_text(encoding="utf-8"), name
    assert web.EM_DASH not in (SCRIPTS / "preview_mock.js").read_text(encoding="utf-8")


def test_hank_js_never_uses_innerhtml() -> None:
    src = hank_js()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in src, banned
    # The only markup parsed is the fixed drawing and the icons of this file, in exactly one place.
    assert src.count("parseFromString(") == 1
    assert re.findall(r"svgFrom\(([^)]*)\)", src) and all(
        arg.startswith(("ICON", "mascot(", "markup")) for arg in re.findall(r"svgFrom\(([^)]*)\)", src))
    # Links only with http: or https:, set in one place and only from the checked value; the one other link is a
    # same-page anchor (the Autopilot card), checked against a pattern that allows nothing but "#name".
    assert src.count(".href =") == 3 and "a.href = url;" in src and "go.href = link.href;" in src
    assert 'protocolLink.href = "#activity/protocol";' in src  # fixed, same-app activity route
    # "#autopilot", "#wiki/sorten", and since 23.10.2026 a wiki card ("#wiki/nutrients/canna-terra"): two segments
    assert r"/^#[a-z][a-z0-9-]*(?:\/[a-z0-9-]+){0,2}$/.test(link.href)" in src
    assert "/^https?:\\/\\//i.test(url)" in src


def test_hank_js_is_wrapped_so_its_names_never_clash_with_app_js() -> None:
    # Classic scripts share one global scope: a top-level const in hank.js named like one of app.js would throw.
    code = "\n".join(line for line in hank_js().splitlines() if not line.startswith("//")).strip()
    assert code.startswith("(function () {") and code.endswith("})();")


def test_hank_scripts_load_after_app() -> None:
    order = web.page().order
    assert order.index("/static/app.js") < order.index("/static/hank-i18n.js") < order.index("/static/hank.js")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    head = html[:html.index("</head>")]
    assert head.index('href="/static/style.css"') < head.index('<link rel="stylesheet" href="/static/hank.css">')
    # Hank's scripts and styles stay in order; the cockpit also hosts his decorative portrait.
    assert [line for line in html.splitlines() if "hank" in line.lower() and line.startswith(("<link", "<script"))] == [
        '<link rel="stylesheet" href="/static/hank.css">',
        '<link rel="stylesheet" href="/static/hank-scenes.css">',
        '<script src="/static/hank-i18n.js"></script>',
        '<script src="/static/hank-scenes.js"></script>',
        '<script src="/static/hank.js"></script>',
    ]


def _build_preview(out: Path) -> str:
    spec = importlib.util.spec_from_file_location("build_preview", SCRIPTS / "build_preview.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.main(out)
    return out.read_text(encoding="utf-8")


def test_preview_build_contains_hank(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = _build_preview(tmp_path / "preview.html")
    capsys.readouterr()
    assert "window.HANK_I18N = " in page
    assert hank_js().strip() in page
    assert (WEB / "hank.css").read_text(encoding="utf-8").strip() in page
    assert "function mockHank(" in page
    # in the order index.html loads them: the app (with the mock in place of api()), then Hank's texts, then Hank
    assert page.index("function renderPlant(") < page.index("window.HANK_I18N = ") < page.index("function mascot(")
    assert "/static/hank" not in page  # everything inlined, nothing left to fetch
    assert (WEB / "light-view.js").read_text(encoding="utf-8").strip() in page
    assert 'path === "/api/light"' in page


def test_mock_answers_hank_paths() -> None:
    used = set(HANK_PATH.findall(hank_js()))
    assert {"/api/hank/state", "/api/hank/ask", "/api/hank/job/"} <= used, used
    mock = (SCRIPTS / "preview_mock.js").read_text(encoding="utf-8")
    assert 'path.startsWith("/api/hank/")' in mock
    missing = sorted(p for p in used if f'"{p}"' not in mock)
    assert not missing, f"preview_mock.js does not answer {missing}"


NODE = shutil.which("node")
NODE_HARNESS = """
const window = { HANK_PREVIEW: %s };
let LANG = "de";
function t(key) { return key; }
%s
(async () => {
  const out = {};
  out.state = await api("/api/hank/state");
  for (const [name, q] of [["greet", "Hallo Hank"], ["off", "Wie wird das Wetter morgen?"], ["humid", "Ist die Luftfeuchte okay?"],
                           ["feed", "Wie viel ml Dünger diese Woche?"], ["clarify", "Meine Pomate"], ["health", "Kann ich das rauchen?"]]) {
    out[name] = await api("/api/hank/ask", { q, client_id: "c-test", lang: "de" });
  }
  out.job = await api(`/api/hank/job/${out.humid.id}`);
  HANK_MOCK.jobs[out.humid.id].start -= 60000;  // the polished version, without waiting for it
  out.job_done = await api(`/api/hank/job/${out.humid.id}`);
  out.yes = await api("/api/hank/clarify", { id: out.clarify.answer.clarify.id, answer: "yes", client_id: "c-test", lang: "de" });
  out.vote = await api("/api/hank/feedback", { answer_id: out.feed.answer.id, vote: "up" });
  out.skip = await api("/api/hank/proposal", { id: out.humid.draft.proposals[0].id, outcome: "declined" });
  out.ack = await api("/api/hank/ack", { upto: 1 });
  out.mode = await api("/api/hank/mode", { mode: "warnings" });
  out.warned = await api("/api/hank/ask", { q: "Ist die Luftfeuchte okay?", client_id: "c-test", lang: "de" });
  out.fixed = HANK_FIXED;
  out.chips = HANK_CHIPS;
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run_mock(preview: dict[str, Any] | None = None) -> dict[str, Any]:
    """The preview's mock in node, with window.HANK_PREVIEW pinned (default: humid at 79 %, one unread note)."""
    pin = preview if preview is not None else {"hum": 79, "hint": True}
    mock = (SCRIPTS / "preview_mock.js").read_text(encoding="utf-8")
    run = subprocess.run([NODE or "node", "-e", NODE_HARNESS % (json.dumps(pin), mock)], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mock_answers_follow_the_api_contract_in_node() -> None:
    out = run_mock()
    state = out["state"]
    assert {"mode", "light", "scene", "inbox", "menu"} <= set(state) and state["mode"] == "on"
    assert state["light"]["level"] in ("green", "yellow", "red")
    assert state["inbox"]["unread"] == 1 and state["inbox"]["items"][0]["id"] == 1
    assert out["greet"]["done"] and out["greet"]["answer"]["route"] == "greet" and out["greet"]["answer"]["chips"]
    assert out["off"]["answer"]["route"] == "offtopic" and len(out["off"]["answer"]["chips"]) == 4
    humid = out["humid"]
    assert set(humid) == {"id", "done", "draft", "stage"} and humid["done"] is False and humid["stage"] == "model"
    assert humid["draft"]["route"] == "grow" and humid["draft"]["draft_only"] and humid["draft"]["proposals"]
    assert out["job"]["stage"] in ("model", "check") and out["job"]["done"] is False and out["job"]["answer"] is None
    feed = _answer(out["feed"])
    assert feed["route"] == "grow" and "0,5" in feed["text"]
    assert all(s["url"] is None or s["url"].startswith("https://") for s in feed["sources"])
    assert out["clarify"]["answer"]["clarify"]["term"] == "Tomate"
    assert out["yes"]["answer"]["text"].startswith("Dazu steht noch nichts in meiner Kartei.")
    assert out["health"]["answer"]["route"] == "health" and not out["health"]["answer"]["chips"]
    assert out["vote"] == {"ok": True} and out["skip"]["ok"] and out["skip"]["message"]["text"]
    assert out["ack"] == {"ok": True, "upto": 1}
    assert out["mode"] == {"mode": "warnings"} and out["warned"]["answer"]["route"] == "off"
    assert out["warned"]["answer"]["text"] == texts.say("hank.reply.warnings_only", "de")


# ---------- part b: the page against the real API (vgrow/hank/api.py, core.py) ----------

GROW_Q = "Ist die Luftfeuchte gerade okay?"  # hank.chip.1, grow on F22 (probe humidity 79 %, too high)
CLARIFY_Q = "Ist die Fehcte okay?"  # a borderline typo: "Meinst du Feuchte?" (tests/test_hank_router.py)


def _f22(lang: str) -> dict[str, Any]:
    sources = fixtures.build("F22", lang=lang).sources()
    sources.pop("ledger")  # Hank keeps his own
    return sources


def _cite_first_fact(body: dict[str, Any]) -> str:
    """A model that cites the sheet's first fact with a marker and the words of its lead, no number: its version
    passes the validator and keeps the draft's core facts (a bare "Das steht so in meiner Kartei [F1]." is a filler
    since the acceptance of 23.10.2026)."""
    user = next(m["content"] for m in body["messages"] if m["role"] == "user")
    found = re.search(r"^\[(F\d+)\] (.*)$", user, re.M)
    lead = re.split(r"[:(\d.]", found.group(2))[0].strip(" ,;") if found else ""
    return f"Meine Kartei sagt dazu: {lead} [{found.group(1) if found else 'F1'}]."


async def _real_calls() -> dict[str, Any]:
    """Every path the page uses, once, against a real Hank on F22 with a fake Ollama that answers in 0.4 s."""
    fake = FakeOllama(reply=_cite_first_fact)
    fake.delay["chat"] = 0.4
    out: dict[str, Any] = {}
    async with TestClient(TestServer(fake.app())) as ollama_side:
        client = ollama.OllamaClient(url=str(ollama_side.make_url("")).rstrip("/"))
        hank = core.Hank(live=_f22, client=client, index=core.shared_index())
        try:
            await hank.tick()  # the traffic light and its first note
            app = aio_web.Application()
            api.register(app, hank)
            async with TestClient(TestServer(app)) as page:
                async def get(path: str) -> Any:
                    res = await page.get(path)
                    assert res.status == 200, (path, await res.text())
                    return await res.json()

                async def post(path: str, body: dict[str, Any]) -> Any:
                    res = await page.post(path, json=body)
                    assert res.status == 200, (path, body, await res.text())
                    return await res.json()

                out["state"] = await get("/api/hank/state")
                for name, q in (("greet", "Hallo Hank"), ("off", "Wie wird das Wetter morgen?"),
                                ("feed", "Wie viel ml Dünger gebe ich diese Woche?"), ("clarify", CLARIFY_Q),
                                ("health", "Kann ich das rauchen?"), ("humid", GROW_Q)):
                    out[name] = await post("/api/hank/ask", {"q": q, "client_id": "c-test", "lang": "de"})
                out["job"] = await get(f"/api/hank/job/{out['humid']['id']}")
                job = hank.jobs[out["humid"]["id"]]
                assert job.task is not None
                await asyncio.wait_for(job.task, 10)
                out["job_done"] = await get(f"/api/hank/job/{out['humid']['id']}")
                out["yes"] = await post("/api/hank/clarify", {"id": out["clarify"]["answer"]["clarify"]["id"],
                                                              "answer": "yes", "client_id": "c-test", "lang": "de"})
                out["vote"] = await post("/api/hank/feedback", {"answer_id": out["greet"]["answer"]["id"],
                                                                "vote": "up"})
                card = out["humid"]["draft"]["proposals"][0]
                out["skip"] = await post("/api/hank/proposal", {"id": card["id"], "outcome": "declined"})
                out["ack"] = await post("/api/hank/ack", {"upto": out["state"]["inbox"]["items"][-1]["id"]})
                out["mode"] = await post("/api/hank/mode", {"mode": "warnings"})
                out["warned"] = await post("/api/hank/ask", {"q": GROW_Q, "client_id": "c-test", "lang": "de"})
        finally:
            await hank.stop()
            await client.close()
    return out


def _answer(response: dict[str, Any]) -> dict[str, Any]:
    """The answer of POST /api/hank/ask: the final one, or the draft while the model polishes it."""
    return response.get("answer") or response["draft"]


def _shapes(out: dict[str, Any]) -> dict[str, set[str]]:
    """The key sets of every object the page reads, by name."""
    state, humid = out["state"], out["humid"]
    return {
        "state": set(state), "light": set(state["light"]), "inbox": set(state["inbox"]),
        "inbox_item": set(state["inbox"]["items"][0]), "scene": set(state["scene"]),
        "fixed_answer": set(out["greet"]["answer"]), "grow_answer": set(_answer(out["feed"])),
        "source": set(_answer(out["feed"])["sources"][0]), "proposal": set(humid["draft"]["proposals"][0]),
        "clarify": set(out["clarify"]["answer"]["clarify"]), "ask_job": set(humid), "job": set(out["job"]),
        "job_done": set(out["job_done"]), "polished_answer": set(out["job_done"]["answer"]),
        "decline": set(out["skip"]), "decline_message": set(out["skip"]["message"]), "mode": set(out["mode"]),
        "ack": set(out["ack"]), "feedback": set(out["vote"]),
    }


# What hank.js reads of each object (the names it uses after a dot). Anything else the server sends is ignored.
PAGE_READS = {
    "state": {"mode", "light", "inbox"},
    "light": {"level", "text", "proposal", "fix"},
    "inbox": {"unread", "items"},
    "inbox_item": {"id", "level", "text", "sources", "proposals", "fix", "rev", "replaces"},
    "fixed_answer": {"id", "route", "text", "sources", "proposals", "clarify", "chips", "draft_only", "note", "link",
                     "plan"},
    "grow_answer": {"id", "route", "text", "sources", "proposals", "draft_only", "note", "more"},
    "source": {"title", "url"},
    "proposal": {"id", "body", "label", "two_taps", "expires"},
    "clarify": {"id"},
    "ask_job": {"id", "done", "draft", "stage"},
    "job": {"done", "draft", "stage", "elapsed_s"},
    "job_done": {"done", "answer"},
    "polished_answer": {"text", "sources", "draft_only", "more"},
    "decline_message": {"text", "sources"},
    "mode": {"mode"},
}


@pytest.fixture
def real_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The real API's answers on the F22 day, in this test's own state folder (conftest.isolated_state)."""
    fixtures.freeze(monkeypatch)
    return asyncio.run(_real_calls())


def test_real_api_answers_every_path_the_page_uses(real_api: dict[str, Any]) -> None:
    out = real_api
    assert out["state"]["mode"] == "on" and out["state"]["light"]["level"] in ("green", "yellow", "red")
    assert out["state"]["inbox"]["unread"] >= 1
    assert out["greet"]["done"] and out["greet"]["answer"]["chips"]
    off = out["off"]["answer"]
    assert off["route"] == "offtopic" and off["text"].endswith(" ".join(off["chips"]))  # see withoutChips in hank.js
    assert out["humid"]["done"] is False and out["humid"]["draft"]["route"] == "grow"
    assert out["job"]["stage"] == "model" and out["job"]["done"] is False and out["job"]["answer"] is None
    assert out["job_done"]["done"] and out["job_done"]["answer"]["draft_only"] is False
    assert out["clarify"]["answer"]["route"] == "clarify" and out["clarify"]["answer"]["clarify"]["term"] == "Feuchte"
    assert out["yes"].get("done") is not None
    assert out["skip"]["ok"] and out["skip"]["message"]["text"]
    assert out["ack"]["ok"] and out["mode"] == {"mode": "warnings"}
    assert out["warned"]["answer"]["route"] == "off"
    # the light's sentence may open with the level's own words, which the page leaves out once (withoutLead)
    light = out["state"]["light"]
    lead = hank_texts()["de"][f"hank.light.{light['level']}"]
    assert light["text"].startswith(lead) or light["level"] != "yellow"


def test_page_reads_only_fields_the_real_api_sends(real_api: dict[str, Any]) -> None:
    shapes = _shapes(real_api)
    src = hank_js()
    for name, fields in PAGE_READS.items():
        missing = fields - shapes[name]
        assert not missing, f"the real API sends no {sorted(missing)} in {name}"
        for field in fields:
            assert re.search(rf"\.{field}\b", src), f"hank.js never reads {name}.{field}: drop it from PAGE_READS"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mock_shapes_match_the_real_api(real_api: dict[str, Any]) -> None:
    real, mock = _shapes(real_api), _shapes(run_mock())
    differ = {name: {"real only": sorted(real[name] - mock[name]), "mock only": sorted(mock[name] - real[name])}
              for name in real if real[name] != mock[name]}
    assert not differ, differ


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mock_fixed_replies_are_the_server_catalog() -> None:
    out = run_mock()
    term = {"de": "Tomate", "en": "tomato"}
    for key, by_lang in out["fixed"].items():
        catalog = "hank.reply.no_data_term" if key == "no_data" else f"hank.reply.{key}"
        for lang, text in by_lang.items():
            assert text == texts.say(catalog, lang, term=term[lang]), (key, lang)
    assert out["chips"] == {lang: texts.chips(lang) for lang in ("de", "en")}
