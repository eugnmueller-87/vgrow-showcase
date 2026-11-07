"""The wiki: GET /api/wiki, /api/wiki/card/{folder}/{key}, /api/wiki/search and POST /api/wiki/packages, read from
kartei/ at request time; the cannabis package switch; the page's entry under "News"; and wiki.js drawing card texts as
text only. No network anywhere; settings.json lives in the test's own state folder (tests/conftest.py)."""

from __future__ import annotations

import asyncio
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
from vgrow import config, i18n, server, wiki
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
NODE = shutil.which("node")


def wiki_js() -> str:
    return (WEB / "wiki.js").read_text(encoding="utf-8")


async def call(app: Any, method: str, path: str, *, cookie: bool = True, lang: str = "de",
               body: Any = None) -> tuple[int, Any]:
    async with TestClient(TestServer(app)) as client:
        if cookie:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
        resp = await client.request(method, path, headers={"X-Lang": lang}, json=body)
        try:
            data = await resp.json()
        except Exception:
            data = {"text": await resp.text()}
        return resp.status, data


def cockpit() -> Any:
    return make_app(DemoBox(), Alerts(), "k")


def get(path: str, **kw: Any) -> tuple[int, Any]:
    return asyncio.run(call(cockpit(), "GET", path, **kw))


def library_app(library: Path) -> Any:
    """The wiki's routes alone, on a library of the test's own (with the language middleware the cockpit has)."""
    @aio_web.middleware
    async def language(request: aio_web.Request, handler: Any) -> Any:
        token = i18n.REQUEST_LANG.set(i18n.normalize(request.headers.get("X-Lang")))
        try:
            return await handler(request)
        finally:
            i18n.REQUEST_LANG.reset(token)

    app = aio_web.Application(middlewares=[language])
    wiki.register(app, library)
    return app


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "kartei"
    write(lib / "crops" / "mint.json", {"key": "mint", "kind": "crop", "status": "draft", "label": "Minze",
                                        "label_en": "Mint", "warnings": ["<b>heiß</b> <img src=x onerror=alert(1)>"],
                                        "sources": {"Q1": "Uni, Minze: https://example.org/mint"}})
    write(lib / "crops" / "hemp-crop.json", {"key": "hemp-crop", "kind": "crop", "package": "cannabis",
                                             "label": "Hanf als Kultur", "label_en": "Hemp as a crop"})
    write(lib / "methods" / "lst.json", {"key": "lst", "kind": "method", "label": "Niedriges Biegen",
                                         "label_en": "Low stress training", "what": "Triebe sanft biegen.",
                                         "what_en": "Bend shoots gently.", "aliases": ["LST", "Biegen"]})
    write(lib / "pests" / "aphids.json", {"label": "Blattläuse", "label_en": "Aphids", "summary": "Kleine Sauger."})
    write(lib / "legal" / "rule.json", {"kind": "legal", "name": "Regel", "name_en": "Rule", "facts": []})
    write(lib / "glossary" / "glossary.json", {
        "name": "Glossar", "status": "draft",
        "terms": [{"key": "vpd", "label": "VPD (Dampfdruckdefizit)", "label_en": "VPD (vapor pressure deficit)",
                   "text": "Wie viel Feuchte der Luft fehlt [G1].", "text_en": "How much moisture the air lacks [G1].",
                   "sources": ["G1"], "relates_to_metric": "probe_vpd", "aliases": {"de": ["Dampfdruck"], "en": []}},
                  {"key": "ec", "label": "EC", "text": "Leitwert.", "sources": ["G2"]}],
        "sources": {"G1": "WSL, Was ist VPD?: https://example.org/vpd", "G2": "Uni, EC: https://example.org/ec",
                    "G3": "nie zitiert: https://example.org/none"}})
    write(lib / "_hank" / "vocab.json", {"entries": []})
    (lib / "crops" / "broken.json").write_text("{not json", encoding="utf-8")
    return lib


# ---------- the index ----------

def test_routes_sit_behind_the_key_cookie() -> None:
    for path in ("/api/wiki", "/api/wiki/card/crops/basil", "/api/wiki/search?q=basil"):
        status, _ = get(path, cookie=False)
        assert status == 403, path


def test_index_lists_every_folder_of_kartei_in_the_fixed_category_order() -> None:
    status, de = get("/api/wiki", lang="de")
    assert status == 200
    ids = [c["id"] for c in de["categories"]]
    assert ids == [c for c in wiki.CATEGORY_ORDER if c in ids]
    assert ids[:2] == ["pflanzen", "cannabis"] and "vorgaenge" in ids and ids[-1] == "recht"
    # the feeding cards stand in the Dünger area since 23.10.2026 (the fertilizer market), not among the tiles
    assert "duengen" not in ids and [a["id"] for a in de["areas"]] == ["varieties", "sorten", "duenger"]
    crops = {c["key"]: c for c in de["categories"][0]["cards"]}
    assert crops["basil"]["label"].startswith("Basilikum") and crops["basil"]["kind"] == "crop"
    assert crops["basil"]["status"] == "draft" and set(crops["basil"]) == {"folder", "key", "kind", "label", "status",
                                                                            "package", "blurb", "group", "image", "current"}
    cannabis = {c["folder"] for c in de["categories"][1]["cards"]}
    assert cannabis == {"stages", "strains", "problems"}
    _, en = get("/api/wiki", lang="en")
    crops_en = {c["key"]: c for c in en["categories"][0]["cards"]}
    assert crops_en["basil"]["label"].startswith("Sweet basil")
    assert de["packages"] == {"cannabis": True} and de["hidden"] == 0
    assert de["count"] == sum(len(c["cards"]) for c in de["categories"])


def test_new_folders_appear_without_code_and_unknown_ones_go_to_more(library: Path) -> None:
    status, data = asyncio.run(call(library_app(library), "GET", "/api/wiki", cookie=False))
    assert status == 200
    ids = [c["id"] for c in data["categories"]]
    assert ids == ["pflanzen", "methoden", "glossar", "recht", "weitere"]
    cats = {c["id"]: c["cards"] for c in data["categories"]}
    assert [c["key"] for c in cats["weitere"]] == ["aphids"] and cats["weitere"][0]["kind"] == "card"
    assert cats["methoden"][0]["kind"] == "method"
    # a glossary file with keyed terms gives one card per term; the broken file and Hank's folder are skipped
    assert [c["key"] for c in cats["glossar"]] == ["ec", "vpd"] and cats["glossar"][0]["kind"] == "term"
    assert {c["key"] for c in cats["pflanzen"]} == {"mint", "hemp-crop"}
    # a card without a status is never shown as checked
    assert cats["weitere"][0]["status"] == "draft" and cats["recht"][0]["package"] == "cannabis"


# ---------- one card ----------

def test_card_answers_one_card_with_its_sources_in_the_request_language() -> None:
    status, de = get("/api/wiki/card/crops/basil", lang="de")
    assert status == 200
    assert de["kind"] == "crop" and de["category"] == "pflanzen" and de["status"] == "draft"
    assert de["label"].startswith("Basilikum") and de["card"]["stages"] and "sources" not in de["card"]
    assert de["sources"][0]["id"] == "Q1" and de["sources"][0]["url"].startswith("https://")
    assert all(not k.endswith("_en") for k in de["card"])
    _, en = get("/api/wiki/card/crops/basil", lang="en")
    assert en["label"].startswith("Sweet basil") and en["card"]["warnings"] != de["card"]["warnings"]


def test_unknown_or_odd_names_are_a_plain_404() -> None:
    for path in ("/api/wiki/card/crops/nope", "/api/wiki/card/nope/basil", "/api/wiki/card/_hank/vocab",
                 "/api/wiki/card/crops/..%2Fplant", "/api/wiki/card/..%2F..%2Fvgrow/config"):
        status, body = get(path)
        assert status == 404, path
        assert body.get("error") == i18n.t("wiki.not_found", lang="de") or "text" in body


def test_collection_entries_carry_only_the_sources_they_cite(library: Path) -> None:
    status, card = asyncio.run(call(library_app(library), "GET", "/api/wiki/card/glossary/vpd", lang="en"))
    assert status == 200
    assert card["kind"] == "term" and card["label"] == "VPD (vapor pressure deficit)"
    assert card["card"]["text"] == "How much moisture the air lacks [G1]." and card["card"]["relates_to_metric"] == "probe_vpd"
    assert [s["id"] for s in card["sources"]] == ["G1"] and card["status"] == "draft"
    assert "aliases" not in card["card"]  # search names are for the search, not for the page


def test_strain_cards_keep_cultivation_facts_and_never_thc() -> None:
    for lang in ("de", "en"):
        status, card = get("/api/wiki/card/strains/ice-cream-cake-auto", lang=lang)
        assert status == 200 and card["kind"] == "strain"
        assert card["card"]["facts"], "the growing facts stay"
        text = json.dumps(card["card"], ensure_ascii=False)
        assert not re.search(r"THC|CBD|Cannabinoid|cannabinoid|Wirkung|effect", text), text
        assert "source_notes" not in card["card"]  # notes for the research, the card itself says so
    _, hits = get("/api/wiki/search?q=THC")
    assert "ice-cream-cake-auto" not in [h["key"] for h in hits["results"]]


def test_card_texts_with_markup_come_back_as_plain_strings(library: Path) -> None:
    """The server never renders markup: a warning with tags is the same string in the JSON (the page sets it as text)."""
    _, card = asyncio.run(call(library_app(library), "GET", "/api/wiki/card/crops/mint"))
    assert card["card"]["warnings"] == ["<b>heiß</b> <img src=x onerror=alert(1)>"]
    assert card["sources"] == [{"id": "Q1", "title": "Uni, Minze", "url": "https://example.org/mint"}]


def test_sources_without_an_http_link_keep_no_link() -> None:
    assert wiki.parse_source("Q1", "Titel ohne Link", "de") == {"id": "Q1", "title": "Titel ohne Link", "url": None}
    entry = {"raw": {}, "collection": False, "sources": {"Q1": {"title": "x", "url": "javascript:alert(1)"}}}
    assert wiki._sources(entry, "de")[0]["url"] is None


# ---------- the package switch ----------

def test_cannabis_package_is_on_by_default_and_the_switch_hides_its_cards() -> None:
    app = cockpit()

    async def run() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})

            async def j(method: str, path: str, body: Any = None) -> tuple[int, Any]:
                resp = await client.request(method, path, json=body, headers={"X-Lang": "de"})
                return resp.status, await resp.json()

            out["before"] = await j("GET", "/api/wiki")
            out["off"] = await j("POST", "/api/wiki/packages", {"package": "cannabis", "on": False})
            out["after"] = await j("GET", "/api/wiki")
            out["card"] = await j("GET", "/api/wiki/card/stages/autoflower")
            out["legal"] = await j("GET", "/api/wiki/card/legal/kcang-grundregeln")
            out["crop"] = await j("GET", "/api/wiki/card/crops/basil")
            out["search"] = await j("GET", "/api/wiki/search?q=Autoflower")
            out["bad"] = await j("POST", "/api/wiki/packages", {"package": "tomatoes", "on": True})
            out["on"] = await j("POST", "/api/wiki/packages", {"package": "cannabis", "on": True})
            out["again"] = await j("GET", "/api/wiki/card/stages/autoflower")
        return out

    i18n.set_server_lang("en")  # settings.json already holds a key: the switch keeps it
    out = asyncio.run(run())
    before = [c["id"] for c in out["before"][1]["categories"]]
    after = [c["id"] for c in out["after"][1]["categories"]]
    assert "cannabis" in before and "recht" in before
    assert out["off"] == (200, {"packages": {"cannabis": False}})
    assert "cannabis" not in after and "recht" not in after and "pflanzen" in after
    # stages, strains, legal, curing (a cannabis card in the mixed folder processes/) and the cannabis methods
    hidden = sum(1 for e in wiki._read_entries(wiki.LIBRARY) if e["package"] == wiki.CANNABIS)
    assert hidden >= 4
    assert out["after"][1]["hidden"] == hidden and out["after"][1]["packages"] == {"cannabis": False}
    assert out["card"][0] == 404 and out["card"][1]["hidden"] is True
    assert out["legal"][0] == 404 and out["crop"][0] == 200
    assert all(h["folder"] not in wiki.CANNABIS_FOLDERS for h in out["search"][1]["results"])
    assert out["bad"][0] == 400
    assert out["on"] == (200, {"packages": {"cannabis": True}}) and out["again"][0] == 200
    settings = json.loads((config.STATE_DIR / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"lang": "en", "packages": ["cannabis"]}


def test_a_card_marked_cannabis_is_hidden_wherever_it_lives(library: Path) -> None:
    on = wiki.index_view("de", ["cannabis"], library)
    off = wiki.index_view("de", [], library)
    keys = lambda v: {c["key"] for cat in v["categories"] for c in cat["cards"]}  # noqa: E731
    assert "hemp-crop" in keys(on) and "hemp-crop" not in keys(off) and "rule" not in keys(off)
    assert off["hidden"] == 2 and "mint" in keys(off)


def test_switch_off_leaves_no_cannabis_in_mixed_cards_and_the_search() -> None:
    """Review 23.10.2026, MUST 1: curing stayed listed and "THC", "Cannabinoid" or "KCanG" still found process cards."""
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": []}))
    status, body = get("/api/wiki/card/processes/curing")
    assert status == 404 and body["hidden"] is True
    _, index = get("/api/wiki")
    assert "curing" not in {c["key"] for cat in index["categories"] for c in cat["cards"]}
    for lang in ("de", "en"):
        for q in ("THC", "Cannabinoid", "KCanG", "Gelato", "Indica", "Autoflower"):
            _, hits = get(f"/api/wiki/search?q={q}", lang=lang)
            assert hits["results"] == [], (lang, q, [h["key"] for h in hits["results"]])
        _, cloning = get("/api/wiki/card/processes/cloning", lang=lang)
        text = json.dumps({"label": cloning["label"], "card": cloning["card"]}, ensure_ascii=False)
        assert not re.search(r"cannabis|KCanG|THC|Cannabinoid", text, re.I), text[:300]
        # the law's sources leave with the warnings that cited them; study titles naming Cannabis sativa may stay
        assert not any("KCanG" in s["title"] for s in cloning["sources"])
        assert [s["key"] for s in cloning["card"]["steps"]] == ["herbs_cutting", "herbs_temperature",
                                                                 "herbs_humidity_light", "herbs_duration_check"]
        _, drying = get("/api/wiki/card/processes/drying", lang=lang)
        assert not any(s["key"].startswith("flowers_") for s in drying["card"]["steps"])
        assert len(drying["card"]["warnings"]) == 3 and "KCanG" not in json.dumps(drying, ensure_ascii=False)
        _, germ = get("/api/wiki/card/processes/germination", lang=lang)
        assert "cannabis_germination" not in [s["key"] for s in germ["card"]["steps"]]
        assert not re.search(r"cannabis|KCanG", json.dumps(germ["card"], ensure_ascii=False), re.I)
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": ["cannabis"]}))
    _, cloning = get("/api/wiki/card/processes/cloning")
    assert len(cloning["card"]["steps"]) == 14 and "Cannabis" in cloning["label"]
    assert len(cloning["card"]["warnings"]) == 7 and any(s["title"].startswith("Konsumcannabisgesetz")
                                                         for s in cloning["sources"])


def test_pkg_filter_drops_marked_items_and_reads_if_off(library: Path) -> None:
    write(library / "processes" / "mixed.json", {
        "key": "mixed", "kind": "process", "label": "Gemischt (Cannabis und Kräuter)", "label_en": "Mixed",
        "if_off": {"cannabis": {"label": "Gemischt (Kräuter)", "label_en": "Mixed (herbs)"}},
        "steps": [{"key": "a", "package": "cannabis", "label": "Cannabis-Schritt", "notes": "Nur Cannabis [Q1]."},
                  {"key": "b", "label": "Kräuter", "notes": "Kräuter schneiden [Q2].",
                   "if_off": {"cannabis": {"notes": "Nur Kräuter [Q2]."}}}],
        "warnings": ["Cannabis-Warnung (Q1).", "Kräuterwarnung (Q2)."],
        "warnings_en": ["Cannabis warning (Q1).", "Herb warning (Q2)."],
        "warnings_packages": ["cannabis", None],
        "sources": {"Q1": "Recht: https://example.org/q1", "Q2": "Uni: https://example.org/q2"}})
    entry = wiki.find("processes", "mixed", library)
    assert entry is not None
    off_de, off_en = wiki.card_view(entry, "de", []), wiki.card_view(entry, "en", [])
    assert off_de["label"] == "Gemischt (Kräuter)" and off_en["label"] == "Mixed (herbs)"
    assert [s["key"] for s in off_de["card"]["steps"]] == ["b"]
    assert off_de["card"]["steps"][0]["notes"] == "Nur Kräuter [Q2]."
    assert off_de["card"]["warnings"] == ["Kräuterwarnung (Q2)."] and off_en["card"]["warnings"] == ["Herb warning (Q2)."]
    assert [s["id"] for s in off_de["sources"]] == ["Q2"]  # a source only the hidden parts cite goes with them
    on = wiki.card_view(entry, "de", ["cannabis"])
    assert on["label"].startswith("Gemischt (Cannabis") and [s["key"] for s in on["card"]["steps"]] == ["a", "b"]
    assert len(on["card"]["warnings"]) == 2 and [s["id"] for s in on["sources"]] == ["Q1", "Q2"]
    for view in (off_de, off_en, on):  # the marks never reach the page
        text = json.dumps(view["card"])
        assert '"package"' not in text and "warnings_packages" not in text and "if_off" not in text
    summaries = {c["key"]: c["label"] for cat in wiki.index_view("de", [], library)["categories"] for c in cat["cards"]}
    assert summaries["mixed"] == "Gemischt (Kräuter)"
    assert "mixed" not in [h["key"] for h in wiki.search("Cannabis", "de", [], library)["results"]]
    assert "mixed" in [h["key"] for h in wiki.search("Cannabis", "de", ["cannabis"], library)["results"]]


def test_no_card_shows_thc_or_cannabinoid_sentences_in_either_language() -> None:
    """SHOULD 4: the sentence filter runs on every card, not only on strains; source titles stay as they are."""
    for lang in ("de", "en"):
        for entry in wiki._entries(wiki.LIBRARY):
            view = wiki.card_view(entry, lang, ["cannabis"])
            text = json.dumps(view["card"], ensure_ascii=False)
            assert not re.search(r"THC|CBD|Cannabinoid|cannabinoid", text), (lang, entry["key"])
    curing = wiki.card_view(wiki.find("processes", "curing"), "de", ["cannabis"])
    assert "Gläser dunkel halten" in curing["card"]["fit_for_vgrow"]["light_note"]  # the rest of the note stays
    assert any("Cannabinoid" in s["title"] for s in curing["sources"])
    # a sentence is cut as a whole, never after "ca." or "z. B." (the first render left "0.62 to 0.7) (Q10)." behind)
    for lang in ("de", "en"):
        notes = wiki.card_view(wiki.find("processes", "curing"), lang, ["cannabis"])["card"]["steps"][0]["notes"]
        assert notes.endswith(("offen.", "open.")) and "(Q10)" not in notes, notes
    assert wiki._clean_text("Kräuter, z. B. Basilikum. Bei THC-reichem Hanf (aw ca. 0.6) gilt das (Q1). Ende.") == \
        "Kräuter, z. B. Basilikum. Ende."


VISIBLE_TWINS = ("label", "name", "title", "summary", "light_note", "heat_note", "notes", "warnings", "text")


def test_every_visible_card_text_has_an_english_twin() -> None:
    """SHOULD 5: the English page showed German fit notes and a German stage title."""
    missing = []

    def walk(value: Any, where: str, strain: bool) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                texts = isinstance(v, str) or (isinstance(v, list) and v and all(isinstance(x, str) for x in v))
                if k in VISIBLE_TWINS and texts and v and f"{k}_en" not in value and not (strain and k == "name"):
                    missing.append(f"{where}.{k}")
                walk(v, f"{where}.{k}", strain)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{where}[{i}]", strain)

    for path in sorted((ROOT / "kartei").glob("*/*.json")):
        if path.parent.name.startswith("_"):
            continue
        walk(json.loads(path.read_text(encoding="utf-8")), f"{path.parent.name}/{path.name}",
             path.parent.name == "strains")  # a strain's name is a proper name, the same in both languages
    assert missing == []
    stage = wiki.card_view(wiki.find("stages", "autoflower"), "en", ["cannabis"])
    assert stage["label"] == "Autoflower in the VIVOSUN VGrow"


def test_package_marks_line_up_with_their_lists() -> None:
    for path in sorted((ROOT / "kartei").glob("*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, marks in data.items() if isinstance(data, dict) else ():
            if key.endswith(wiki.PKG_SUFFIX):
                base = key[: -len(wiki.PKG_SUFFIX)]
                assert len(marks) == len(data[base]) == len(data.get(f"{base}_en", data[base])), (path.name, key)
                assert set(marks) <= {None, *wiki.KNOWN_PACKAGES}, (path.name, key)


def test_cards_are_read_again_only_when_a_file_changes(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = []
    real = wiki._read_entries
    monkeypatch.setattr(wiki, "_read_entries", lambda lib: reads.append(lib) or real(lib))
    wiki.search("Minze", "de", [], library)
    wiki.search("Minze", "de", [], library)
    wiki.index_view("de", [], library)
    assert len(reads) == 1  # unchanged files: one read for three requests
    write(library / "crops" / "mint.json", {"key": "mint", "kind": "crop", "label": "Pfefferminze lang",
                                            "label_en": "Mint"})
    assert wiki.search("Pfefferminze", "de", [], library)["results"][0]["key"] == "mint"
    write(library / "crops" / "sage.json", {"key": "sage", "kind": "crop", "label": "Salbei"})
    assert "sage" in {c["key"] for cat in wiki.index_view("de", [], library)["categories"] for c in cat["cards"]}
    (library / "crops" / "sage.json").unlink()
    assert wiki.search("Salbei", "de", [], library)["results"] == []
    assert len(reads) == 4


def test_packages_read_from_settings_and_ignore_unknown_values() -> None:
    assert wiki.packages() == ["cannabis"]  # no settings.json: this install's default
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": ["cannabis", "x", 3]}))
    assert wiki.packages() == ["cannabis"]
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": []}))
    assert wiki.packages() == []


# ---------- search ----------

@pytest.mark.parametrize(("q", "lang", "first"), [
    ("Basilkum", "de", "basil"),  # one letter missing
    ("Schnitlauch", "de", "chives"),  # one letter missing
    ("Klonnen", "de", "cloning"),  # a doubled letter in a label word
    ("basil", "en", "basil"),
    ("Duengen", "de", "duengerarten"),  # umlaut written out; the area's overview of the types comes first
    ("Stecklinge", "de", "cloning"),
])
def test_search_finds_labels_with_typos_and_folded_umlauts(q: str, lang: str, first: str) -> None:
    status, data = get(f"/api/wiki/search?q={q}", lang=lang)
    assert status == 200
    assert data["results"] and data["results"][0]["key"] == first, (q, [h["key"] for h in data["results"]])


def test_search_marks_typos_and_shows_where_a_text_word_stands() -> None:
    _, typo = get("/api/wiki/search?q=Basilkum")
    assert typo["results"][0]["typo"] is True and typo["results"][0]["category"] == "pflanzen"
    _, text = get("/api/wiki/search?q=Mehltau")
    hit = next(h for h in text["results"] if h["key"] == "basil")
    assert "Mehltau" in hit["snippet"] and hit["typo"] is False


def test_search_reads_aliases_and_stays_empty_for_nothing(library: Path) -> None:
    assert wiki.search("Dampfdruck", "de", [], library)["results"][0]["key"] == "vpd"
    assert wiki.search("LST", "de", [], library)["results"][0]["key"] == "lst"
    assert wiki.search("", "de", [], library)["results"] == []
    assert wiki.search("was ist", "de", [], library)["results"] == []  # only stop words: no wild guess
    assert wiki.search("x" * 500, "de", [], library)["q"] == "x" * wiki.MAX_QUERY


def test_search_wants_every_word_first_and_short_words_match_no_inside_a_word() -> None:
    """NICE 6: "Ice Cream" also found chives, cloning, germination and drying ("ice" in "advice", "price")."""
    for lang in ("de", "en"):
        _, data = get("/api/wiki/search?q=Ice%20Cream", lang=lang)
        keys = [h["key"] for h in data["results"]]
        # the strain first; method cards that name the plant in the box may follow, never a card with "ice" in "advice"
        assert keys[0] == "ice-cream-cake-auto", lang
        assert not {"chives", "cloning", "germination", "drying"} & set(keys), (lang, keys)
    assert wiki._match("ice", frozenset({"advice"}), "advice") == (0, False)
    assert wiki._match("mehl", frozenset({"echter", "mehltau"}), "echter mehltau")[0] == 3
    assert wiki._match("ehlt", frozenset({"mehltau"}), "mehltau")[0] == 2
    # no card holds both words: the cards with one of them still come back (OR as the fallback)
    _, either = get("/api/wiki/search?q=Basilikum%20Xqzvw")
    assert either["results"] and either["results"][0]["key"] == "basil"


# ---------- the page ----------

def test_one_responsive_resource_entry_under_news_hidden_until_the_server_answers() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    line = '<button type="button" class="wiki-open" data-wiki-open hidden data-i18n="wiki.title"></button>'
    news = '<button type="button" class="news-open" data-news-open hidden data-i18n="news.title"></button>'
    assert html.count(line) == 1
    for start in ('<div class="side-resources"',):
        block = html[html.index(start):]
        lines = [x.strip() for x in block.splitlines()]
        assert lines[lines.index(news) + 1] == line, start
    wiki_lines = [x.strip() for x in html.splitlines() if "wiki" in x.lower()]
    assert wiki_lines == ['<link rel="stylesheet" href="/static/wiki.css">', line,
                          '<script src="/static/wiki.js"></script>', '<script src="/static/wiki-tools.js"></script>']
    order = web.page().order
    assert order.index("/static/app.js") < order.index("/static/wiki.js")
    assert "wiki.js" in server.PAGE_FILES and "wiki.css" in server.PAGE_FILES
    side = html[html.index('<nav class="side-nav"'):html.index("</nav>", html.index('<nav class="side-nav"'))]
    assert side.count("<a href") == 8  # task pages; one resources entry works on desktop and in the mobile drawer


def test_wiki_js_builds_text_only_and_opens_links_safely() -> None:
    src = wiki_js()
    code_only = "\n".join(x for x in src.splitlines() if not x.lstrip().startswith("//"))
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                   "createContextualFragment", "localStorage", "importNode", "adoptNode", "cloneNode"):
        assert banned not in code_only, banned
    # One parser, for the wiki's own drawings file only, as SVG; nothing of it is adopted: rebuild() copies element by
    # element through a whitelist of tags and attributes (no script, no link, no style, no on... handler).
    assert code_only.count("new DOMParser()") == 1
    assert 'new DOMParser().parseFromString(text, "image/svg+xml")' in code_only
    load = code_only[code_only.index("function loadArt"):code_only.index("function rebuild")]
    assert "fetch(`/static/img/wiki/drawings.svg" in load and "new DOMParser()" in load
    tags = re.search(r"const SVG_TAGS = new Set\(\[([^\]]+)\]\)", code_only).group(1)
    attrs = re.search(r"const SVG_ATTRS = new Set\(\[([^\]]+)\]\)", code_only).group(1)
    for unsafe in ('"script"', '"a"', '"use"', '"image"', '"foreignObject"', '"style"', '"href"', '"on'):
        assert unsafe not in tags and unsafe not in attrs, unsafe
    assert 'a.rel = "noopener noreferrer"' in src and 'a.target = "_blank"' in src
    assert "/^https?:\\/\\//i.test(url)" in src
    assert "\"/api/wiki/packages\"" in src  # the switch lives in the view, never in the sidebar
    # "Hank fragen" only once Hank can explain a card (review 23.10.2026, MUST 2: before v1.2 he answers he is the
    # wrong one), and clearing the field drops an answer still on its way (NICE 9)
    hank = src[src.index("function hankButton"):]
    assert hank.index("window.HANK_FEATURES && window.HANK_FEATURES.explainCard") < hank.index("document.querySelector")
    assert "if (!q) { W.seq++; W.results = null; return; }" in src


def test_wiki_texts_exist_in_both_languages_and_files_are_clean() -> None:
    data = web.dictionary()
    wiki_keys = sorted(k for k in data["de"] if k.startswith("wiki."))
    assert wiki_keys and wiki_keys == sorted(k for k in data["en"] if k.startswith("wiki."))
    for key in ("wiki.title", "wiki.package", "wiki.draft", "wiki.hankBtn", "wiki.hankAsk", "wiki.back"):
        assert key in wiki_keys
    assert data["de"]["wiki.package"] == "Cannabis-Wissen anzeigen" and data["de"]["wiki.draft"] == "Entwurf"
    assert data["de"]["wiki.hankAsk"] == "Erzähl mir mehr über {label}"
    for name in ("web/wiki.js", "web/wiki.css", "vgrow/wiki.py", "tests/test_wiki.py", "web/i18n.js", "web/index.html"):
        raw = (ROOT / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), name
        assert b"\r\n" not in raw, name
        assert EM_DASH not in raw.decode("utf-8"), name
    for lang in ("de", "en"):
        for key in ("wiki.unavailable", "wiki.not_found", "wiki.hidden"):
            assert i18n.t(key, lang=lang) != key


def test_nothing_on_the_page_promotes_consumption() -> None:
    data = web.dictionary()
    for lang in ("de", "en"):
        for key, text in data[lang].items():
            if key.startswith("wiki."):
                assert not re.search(r"CBD|Wirkung|effect|high\b|Rausch", text), (lang, key)
                if "THC" in text:
                    assert key in {"wiki.lex.varietyIntro", "wiki.lex.thc", "wiki.lex.thcShort", "wiki.lex.thcNote", "wiki.lex.thcLegend",
                                   "wiki.tools.thc_percent", "wiki.tools.invalid_value"}


# ---------- wiki.js in node, with a small stand-in for the DOM ----------

DOM = r"""
const made = [];
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.childNodes = []; this.attrs = {}; this.dataset = {};
    this.className = ""; this.hidden = false; this.style = {}; this.own = ""; this.listeners = {}; made.push(this); }
  get classList() { const self = this; const list = () => self.className.split(/\s+/).filter(Boolean);
    return { add(c) { if (!list().includes(c)) self.className = [...list(), c].join(" "); },
      remove(c) { self.className = list().filter((x) => x !== c).join(" "); },
      toggle(c, on) { (on ?? !list().includes(c)) ? this.add(c) : this.remove(c); },
      contains(c) { return list().includes(c); } }; }
  set textContent(v) { this.childNodes = []; this.own = String(v); }
  get textContent() { return this.own + this.childNodes.map((c) => typeof c === "string" ? c : c.textContent).join(""); }
  append(...xs) { for (const x of xs) if (x != null) { if (typeof x !== "string") x.parentNode = this; this.childNodes.push(x); } }
  appendChild(x) { this.append(x); return x; }
  prepend(...xs) { this.childNodes.unshift(...xs); }
  replaceChildren(...xs) { this.childNodes = []; this.own = ""; this.append(...xs); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  focus() {} setSelectionRange() {}
  get isConnected() { return true; }
  createTHead() { const n = new El("thead"); this.append(n); return n; }
  createTBody() { const n = new El("tbody"); this.append(n); return n; }
  insertRow() { const n = new El("tr"); this.append(n); return n; }
  all() { return this.childNodes.filter((c) => typeof c !== "string").flatMap((c) => [c, ...c.all()]); }
  matches(sel) { if (sel.startsWith(".")) return this.classList.contains(sel.slice(1));
    if (sel.startsWith("#")) return this.id === sel.slice(1); if (sel.startsWith("[")) return false;
    return this.tagName === sel.toUpperCase(); }
  querySelector(sel) { return this.all().find((n) => n.matches(sel)) || null; }
  querySelectorAll(sel) { return this.all().filter((n) => n.matches(sel)); }
}
const main = new El("main");
const body = new El("body"); body.append(main);
const listeners = {};
const window = { I18N: { de: {}, en: {} }, addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); }, scrollTo() {} };
const document = { body, documentElement: new El("html"), activeElement: null, hidden: false, title: "",
  createElement: (t) => new El(t), createElementNS: (_, t) => new El(t),
  querySelector: (s) => s === "main" ? main : body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s),
  getElementById: (id) => body.all().find((n) => n.id === id) || null,
  addEventListener() {}, removeEventListener() {} };
const location = { hash: "", pathname: "/", search: "" };
const history = { pushState() { location.hash = ""; }, back() {} };
class MutationObserver { observe() {} }
let LANG = "de";
const LOCALE = { de: "de-DE", en: "en-US" };
let state = { climate: { probe_temp: 25.7, probe_humidity: 61, probe_vpd: 1.53 } };
function t(key, params) { return params ? `${key}${JSON.stringify(params)}` : key; }
function tn(key, n) { return `${key}:${n}`; }
const CARDS = %s;
async function api(path) {
  if (path === "/api/wiki") return { categories: [], packages: { cannabis: true }, hidden: 0, count: 0 };
  const m = path.match(/^\/api\/wiki\/card\/([^/]+)\/([^/]+)$/);
  if (m) { const c = CARDS[`${decodeURIComponent(m[1])}/${decodeURIComponent(m[2])}`]; if (c) return c; }
  throw new Error("404 " + path);
}
"""

RUN = r"""
const tick = () => new Promise((r) => setTimeout(r, 20));
(async () => {
  await tick();
  const out = {};
  for (const id of Object.keys(CARDS)) {
    location.hash = `#wiki/${id}`;
    for (const f of listeners.hashchange || []) await f();
    await tick();
    const view = main.childNodes[0];
    const nodes = view.all();
    out[id] = { text: view.textContent, tags: [...new Set(nodes.map((n) => n.tagName))],
      links: nodes.filter((n) => n.tagName === "A").map((n) => ({ href: n.href, target: n.target || "", rel: n.rel || "" })) };
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run_page(cards: dict[str, Any]) -> tuple[dict[str, Any], str]:
    script = DOM % json.dumps(cards, ensure_ascii=False) + wiki_js() + RUN
    run = subprocess.run([NODE or "node", "-"], input=script, capture_output=True, text=True, encoding="utf-8",
                         timeout=60)  # on stdin: a command line this long is too long for Windows
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout), run.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("failed", [False, True])
def test_back_to_index_discards_a_late_article_response_and_cockpit_uses_browser_history(failed: bool) -> None:
    """A quick Back while an article loads must keep the index, including when the abandoned request fails."""
    run_js = r"""
const originalApi = api;
let finish;
let browserPushes = 0;
window.history = { pushState() { browserPushes++; location.hash = ""; }, back() {} };
api = (path) => path === "/api/wiki/card/crops/delayed"
  ? new Promise((resolve, reject) => { finish = %s ? reject : resolve; }) : originalApi(path);
(async () => {
  await new Promise(r => setTimeout(r, 20));
  location.hash = "#wiki/crops/delayed";
  const pending = listeners.hashchange[0]();
  location.hash = "#wiki";
  await listeners.hashchange[0]();
  const view = document.getElementById("wiki-view");
  const before = !!view.querySelector(".wiki-q");
  finish(%s ? new Error("abandoned") : { label: "Delayed article", kind: "crop", card: { summary: "late" } });
  await pending;
  const after = !!view.querySelector(".wiki-q");
  view.querySelector(".wiki-back").listeners.click[0]();
  console.log(JSON.stringify({before, after, browserPushes, hidden: view.hidden}));
})().catch(e => { console.error(e); process.exit(1); });
""" % (str(failed).lower(), str(failed).lower())
    script = DOM % "{}" + wiki_js() + run_js
    result = subprocess.run([NODE or "node", "-"], input=script, capture_output=True, text=True, encoding="utf-8",
                            timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"before": True, "after": True, "browserPushes": 1, "hidden": True}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_card_text_with_markup_stays_text_and_only_http_links_open_in_a_new_tab() -> None:
    evil = "<img src=x onerror=alert(1)>"
    card = {"folder": "crops", "key": "x", "kind": "crop", "category": "pflanzen", "status": "draft", "package": None,
            "label": "<b>Fett</b>",
            "card": {"summary": evil, "warnings": ["<script>alert(2)</script>"],
                     "stages": [{"label": "<i>Keimung</i>", "days_from": 0, "days_to": 7,
                                 "targets": [{"metric": "probe_temp", "min": 20, "max": 25, "unit": "°C"}]}]},
            "sources": [{"id": "Q1", "title": "<u>t</u>", "url": "javascript:alert(3)"},
                        {"id": "Q2", "title": "ok", "url": "https://example.org/q2"}]}
    out, err = run_page({"crops/x": card})
    page = out["crops/x"]
    for raw in (evil, "<b>Fett</b>", "<script>alert(2)</script>", "<i>Keimung</i>", "<u>t</u>"):
        assert raw in page["text"], raw
    assert not {"IMG", "SCRIPT", "I", "U"} & set(page["tags"]), page["tags"]  # <b> is the page's own (step titles)
    assert [a for a in page["links"] if not a["href"].startswith("#wiki")] == [{"href": "https://example.org/q2", "target": "_blank", "rel": "noopener noreferrer"}]
    assert "wiki.draft" in page["text"] and "wiki.sources" in page["text"]  # the badge and the sources are there
    assert not err


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_real_card_renders_with_its_own_renderer() -> None:
    """All cards of kartei/ in both languages: no renderer falls back, every card shows its label, badge and sources."""
    for lang in ("de", "en"):
        cards = {}
        for cat in wiki.index_view(lang, ["cannabis"])["categories"]:
            for c in cat["cards"]:
                cards[f"{c['folder']}/{c['key']}"] = wiki.card_view(wiki.find(c["folder"], c["key"]), lang)
        for row in [*wiki.fertilizers_view(lang, ["cannabis"])["lines"], {"key": "duengerarten"}]:  # the Dünger area
            cards[f"nutrients/{row['key']}"] = wiki.card_view(wiki.find("nutrients", row["key"]), lang)
        out, err = run_page(cards)
        assert not err, err  # "wiki: renderer failed" would be on stderr
        for cid, page in out.items():
            assert cards[cid]["label"] in page["text"], cid
            assert ("wiki.draft" in page["text"]) == (cards[cid]["status"] == "draft"), cid
            assert all(link["href"].startswith("https://") and link["rel"] == "noopener noreferrer"
                       for link in page["links"] if link["href"].startswith("http")), cid
        stage = out["stages/autoflower"]
        assert "TABLE" in stage["tags"] and "wiki.sec.stageTable" in stage["text"]
        assert "wiki.sec.chart" in out["nutrients/biobizz-2026"]["text"]
        assert "wiki.sec.boosters" in out["nutrients/biobizz-2026"]["text"]
        assert "wiki.sec.lineBy" in out["nutrients/plagron-terra"]["text"] and "wiki.fert.printed" in             out["nutrients/plagron-terra"]["text"]
        assert "wiki.sec.fplan" in out["nutrients/biotabs"]["text"] and "wiki.sec.chart" not in             out["nutrients/biotabs"]["text"]  # tablets and single feeds: no per-litre chart
        assert "wiki.sec.market" in out["nutrients/duengerarten"]["text"] and "wiki.fert.mistakes" in             out["nutrients/duengerarten"]["text"]
        assert "THC" not in out["strains/ice-cream-cake-auto"]["text"]


# ---------- pictures: photos from Wikimedia Commons, the wiki's own drawings, the owner's camera ----------

IMG = WEB / "img" / "wiki"
DRAWINGS = IMG / "drawings.svg"


def credits() -> dict[str, Any]:
    return json.loads((IMG / "credits.json").read_text(encoding="utf-8"))


def raw_images() -> dict[str, dict[str, Any]]:
    """Every card's own "image" field, as written in kartei/ (folder/key -> field)."""
    return {f"{e['folder']}/{e['key']}": e["raw"]["image"] for e in wiki._entries(wiki.LIBRARY)
            if isinstance(e["raw"].get("image"), dict)}


def panel_ids() -> set[str]:
    return set(re.findall(r'<g id="([a-z0-9-]+)"', DRAWINGS.read_text(encoding="utf-8")))


def webp_size(path: Path) -> tuple[int, int]:
    """Width and height from a WebP header (lossy VP8 or extended VP8X), without Pillow."""
    data = path.read_bytes()[:40]
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", path.name
    if data[12:16] == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    assert data[12:16] == b"VP8 ", path.name
    return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF


def test_every_card_has_a_picture_and_every_photo_its_credit() -> None:
    images = raw_images()
    assert len(images) >= 40  # every crop, process, method and the strain, and nearly every glossary term
    photos = 0
    for cid, raw in images.items():
        view = wiki.image_view(raw, "de", ["cannabis"], kind="variety" if cid.startswith("varieties/") else None)
        assert view, cid  # a field that does not validate would silently fall back: that is a bug in the data
        if "file" in raw:
            photos += 1
            photo = view["photo"]
            assert photo["src"] == "/static/img/wiki/" + raw["file"] and photo["thumb"].startswith("/static/img/wiki/")
            c = photo["credit"]
            assert wiki.LICENSE_RE.match(c["license"]) and c["author"] and c["work"].startswith("File:"), cid
            assert c["license_url"].startswith("https://") and c["page_url"].startswith(
                "https://commons.wikimedia.org/wiki/File:"), cid
        if "drawing" in raw:
            assert view["drawing"]["id"] == raw["drawing"], cid
    assert photos >= 18
    # the credit also stands in the card's sources, with a link to the file page
    basil = wiki.card_view(wiki.find("crops", "basil"), "en", ["cannabis"])
    assert basil["sources"][-1] == {"id": "B1", "kind": "image", "title": "File:Lush basil (Ocimum basilicum).jpg",
                                    "author": "Surplusvalue", "license": "CC BY-SA 3.0",
                                    "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
                                    "url": "https://commons.wikimedia.org/wiki/File:Lush_basil_(Ocimum_basilicum).jpg"}
    assert "image" not in basil["card"]  # the picture travels beside the text, never inside it


def test_credits_file_lists_every_photo_once_and_matches_the_cards() -> None:
    doc = credits()
    entries = {e["file"]: e for e in doc["images"]}
    on_disk = {p.name for p in IMG.iterdir() if p.suffix in (".webp", ".jpg") and not p.stem.endswith("-t")}
    assert set(entries) == on_disk
    used: dict[str, list[str]] = {}
    for cid, raw in raw_images().items():
        if "file" not in raw:
            continue
        used.setdefault(raw["file"], []).append(cid)
        e = entries[raw["file"]]
        who = {"author_url": e["author_url"]} if "author_url" in e else {}  # a user name links to the profile
        assert raw["thumb"] == e["thumb"] and raw["credit"] == {
            "work": e["work"], "author": e["author"], **who, "license": e["license"], "license_url": e["license_url"],
            "page_url": e["file_page"]}, cid
    for name, e in entries.items():
        assert sorted(e["cards"]) == sorted(used.get(name, [])), name
        assert (IMG / e["thumb"]).is_file() and wiki.LICENSE_RE.match(e["license"]), name
        assert e["file_page"].startswith("https://commons.wikimedia.org/wiki/File:"), name
    assert doc["drawings"]["file"] == "drawings.svg" and DRAWINGS.is_file()


def test_pictures_stay_inside_the_size_budget() -> None:
    files = [p for p in IMG.iterdir() if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    assert total < 8 * 1024 * 1024, total
    for p in files:
        if p.suffix in (".webp", ".jpg"):
            assert p.suffix == ".webp" and p.stat().st_size <= 256_000, (p.name, p.stat().st_size)
            w, h = webp_size(p)
            assert w <= 1200 and h <= 1200, (p.name, w, h)
            if p.stem.endswith("-t"):
                assert (w, h) == (480, 360), p.name
    for raw in raw_images().values():
        if "file" in raw:
            assert (raw["w"], raw["h"]) == webp_size(IMG / raw["file"]), raw["file"]


def test_local_photos_stay_local_and_reference_photos_use_the_checked_source() -> None:
    for lang in ("de", "en"):
        for entry in wiki._entries(wiki.LIBRARY):
            view = wiki.card_view(entry, lang, ["cannabis"])
            photo = (view["image"] or {}).get("photo")
            if photo:
                assert re.fullmatch(r"/static/img/wiki/[a-z0-9-]+\.webp", photo["src"]), entry["key"]
                assert re.fullmatch(r"/static/img/wiki/[a-z0-9-]+-t\.webp", photo["thumb"]), entry["key"]
    src = wiki_js()
    # every src the wiki sets: a checked /static/img/wiki/ path, or the owner's photo from the cockpit's own route
    sets = re.findall(r"\.src = ([^;]+);", src)
    assert sets and set(sets) <= {"src", "W.own.src", "own.src", "photo.src"}, sets
    assert "const OWN_SRC = /^\\/static\\/img\\/wiki\\/[a-z0-9-]+\\.(?:webp|jpg)$/;" in src
    assert "if (typeof src !== \"string\" || !OWN_SRC.test(src)) return null;" in src
    assert "const path = `/photos/${last}.jpg`;" in src
    assert "http" not in DRAWINGS.read_text(encoding="utf-8").replace('xmlns="http://www.w3.org/2000/svg"', "")


def test_a_wrong_image_field_falls_back_and_never_reaches_the_page(tmp_path: Path) -> None:
    (tmp_path / "a.webp").write_bytes(b"x")
    (tmp_path / "a-t.webp").write_bytes(b"x")
    good = {"file": "a.webp", "thumb": "a-t.webp", "alt": "Blatt", "alt_en": "Leaf",
            "credit": {"work": "File:A.jpg", "author": "Someone", "license": "CC BY-SA 4.0",
                       "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                       "page_url": "https://commons.wikimedia.org/wiki/File:A.jpg"}}
    assert wiki.image_view(good, "en", [], tmp_path)["photo"]["alt"] == "Leaf"
    bad = [{**good, "file": "https://example.org/a.webp"}, {**good, "file": "../a.webp"}, {**good, "file": "b.webp"},
           {**good, "credit": {**good["credit"], "license": "All rights reserved"}},
           {**good, "credit": {**good["credit"], "page_url": "http://commons.wikimedia.org/x"}},
           {**good, "credit": {**good["credit"], "author": ""}}, {**good, "alt": ""}]
    for raw in bad:
        assert wiki.image_view(raw, "de", [], tmp_path) is None, raw
    # a drawing stays when the photo is wrong; a broken drawing id or step is dropped
    mixed = wiki.image_view({**bad[0], "drawing": "tom", "steps": [{"step": 2, "fig": "tom-2"},
                                                                   {"step": 0, "fig": "tom-1"},
                                                                   {"step": 3, "fig": "x-1"}]}, "de", [], tmp_path)
    assert mixed == {"drawing": {"id": "tom", "steps": [{"step": 2, "fig": "tom-2"}]}}
    assert wiki.image_view({"drawing": "<script>"}, "de", []) is None
    # an image of a package that is off leaves with it (the hemp leaf on "nutrient burn")
    burn = wiki.find("glossary", "nutrient-burn")
    assert wiki.card_view(burn, "de", [])["image"] is None
    assert wiki.card_view(burn, "de", ["cannabis"])["image"]["photo"]
    assert not any(s.get("kind") == "image" for s in wiki.card_view(burn, "de", [])["sources"])


def test_drawings_exist_for_every_step_and_name_their_panels_in_both_languages() -> None:
    ids = panel_ids()
    data = web.dictionary()
    src = wiki_js()
    alt = dict(re.findall(r'"([a-z0-9-]+)": "(wiki\.art\.[a-z0-9_]+)"', src[src.index("const ART_ALT"):]))
    drawn = {i for i in ids if not i.startswith("glyph-")}
    assert set(alt) == drawn  # every panel has its alt text, and no alt text names a missing panel
    for key in alt.values():
        for lang in ("de", "en"):
            assert data[lang][key].strip() and EM_DASH not in data[lang][key], (lang, key)
        assert data["de"][key] != data["en"][key], key
    for kind in ("crop", "process", "method", "term", "stage", "strain", "nutrient", "legal", "problem", "card"):
        assert f"glyph-{kind}-1" in ids, kind
    for cid, raw in raw_images().items():
        if "drawing" not in raw:
            continue
        assert f"{raw['drawing']}-1" in ids, cid
        steps = wiki.find(*cid.split("/"))["raw"].get("steps") or []
        for item in raw.get("steps", []):
            assert item["fig"] in ids and 1 <= item["step"] <= len(steps), (cid, item)
            assert not steps[item["step"] - 1].startswith("Vorweg:"), (cid, item)  # a caveat is no step to draw
    svg = DRAWINGS.read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+\s*=", svg)  # no event handler
    for unsafe in ("<script", "href", "<image", "<foreignObject", "javascript:"):
        assert unsafe not in svg, unsafe
    raw_bytes = DRAWINGS.read_bytes()
    assert not raw_bytes.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw_bytes and EM_DASH not in svg


def test_every_photo_has_its_alt_text_in_both_languages() -> None:
    for cid, raw in raw_images().items():
        if "file" not in raw:
            continue
        de, en = raw.get("alt", ""), raw.get("alt_en", "")
        assert de.strip() and en.strip() and de != en, cid
        assert EM_DASH not in de + en, cid
        assert not re.search(r"THC|CBD|Rausch|joint|smok|rauch", de + en, re.I), cid  # plants and tools only
        assert wiki.card_view(wiki.find(*cid.split("/")), "en", ["cannabis"])["image"]["photo"]["alt"] == en


def test_tiles_carry_a_first_sentence_and_the_running_strain_is_marked(library: Path) -> None:
    index = wiki.index_view("de", ["cannabis"])
    cards = {f"{c['folder']}/{c['key']}": c for cat in index["categories"] for c in cat["cards"]}
    assert index["count"] == len(cards)
    assert index["drafts"] == sum(c["status"] == "draft" for c in cards.values())
    assert cards["methods/topping"]["blurb"] == "Die Spitze des Haupttriebs wird abgeschnitten."
    assert all(len(c["blurb"]) <= wiki.BLURB_MAX + 2 for c in cards.values())
    assert not any(re.search(r"THC|Cannabinoid", c["blurb"]) for c in cards.values())
    assert cards["strains/ice-cream-cake-auto"]["current"] is True  # plant.json "strain_card"
    assert sum(c["current"] for c in cards.values()) == 1
    assert wiki.card_view(wiki.find("strains", "ice-cream-cake-auto"), "de", ["cannabis"],
                          wiki.current_strain())["current"] is True
    own = wiki.index_view("de", ["cannabis"], library)  # a library of a test has no running grow
    assert not any(c["current"] for cat in own["categories"] for c in cat["cards"])
    _, data = get("/api/wiki/card/strains/ice-cream-cake-auto")
    assert data["current"] is True and data["image"] == {"drawing": {"id": "strain-auto", "steps": []}}


def test_steps_get_a_short_bold_lead_and_caveats_lose_their_number() -> None:
    for lang in ("de", "en"):
        topping = wiki.card_view(wiki.find("methods", "topping"), lang, ["cannabis"])["card"]
        assert len(topping["steps_lead"]) == len(topping["steps"])
        for lead, text in zip(topping["steps_lead"], topping["steps"]):
            assert lead == "" or (text.startswith(lead) and len(lead) <= wiki.BLURB_MAX and lead != text)
        assert "steps_preface" not in topping
        defol = wiki.card_view(wiki.find("methods", "defoliation"), lang, ["cannabis"])["card"]
        assert defol["steps_preface"][0] is True and not any(defol["steps_preface"][1:])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_method_card_numbers_its_steps_and_a_photo_card_credits_its_photo() -> None:
    cards = {cid: wiki.card_view(wiki.find(*cid.split("/")), "de", ["cannabis"])
             for cid in ("methods/defoliation", "crops/basil")}
    out, err = run_page(cards)
    assert not err, err
    defol = out["methods/defoliation"]
    assert "Vorweg:" in defol["text"] and "Schere vor dem Schnitt reinigen." in defol["text"]
    basil = out["crops/basil"]
    assert "IMG" in basil["tags"] and "FIGCAPTION" in basil["tags"]
    assert "wiki.credit.photo" in basil["text"] and "Surplusvalue" in basil["text"]
    hrefs = [link["href"] for link in basil["links"]]
    assert "https://creativecommons.org/licenses/by-sa/3.0/" in hrefs
    assert "https://commons.wikimedia.org/wiki/File:Lush_basil_(Ocimum_basilicum).jpg" in hrefs


def test_a_strain_without_a_picture_of_its_own_gets_the_plant_drawing(library: Path) -> None:
    """Photos of strains come only from the owner's camera (never a seed bank or a shop): every other strain card
    shows the wiki's own plant drawing instead of the grey tile, in the list and on the card."""
    assert wiki.image_view(None, "de", ["cannabis"], kind="strain") == {"drawing": {"id": "strain", "steps": []}}
    assert f"{wiki.STRAIN_DRAWING}-1" in panel_ids()
    write(library / "strains" / "new-one.json", {"key": "new-one", "kind": "strain", "label": "Neue Sorte",
                                                  "label_en": "New strain", "facts": {"type": "photoperiod"}})
    entry = wiki.find("strains", "new-one", library)
    assert wiki.card_view(entry, "de", ["cannabis"])["image"] == {"drawing": {"id": "strain", "steps": []}}
    tiles = {c["key"]: c for cat in wiki.index_view("en", ["cannabis"], library)["categories"] for c in cat["cards"]}
    assert tiles["new-one"]["image"] == {"drawing": {"id": "strain", "steps": []}}
    # a strain with its own drawing keeps it
    ice = wiki.card_view(wiki.find("strains", "ice-cream-cake-auto"), "de", ["cannabis"])
    assert ice["image"]["drawing"]["id"] == "strain-auto"
    assert not wiki.image_view(None, "de", ["cannabis"], kind="crop")


def test_a_photo_that_stands_for_something_else_says_so() -> None:
    for lang in ("de", "en"):
        dli = wiki.card_view(wiki.find("glossary", "dli"), lang, ["cannabis"])["image"]["photo"]
        assert dli["compare"] is True and "DLI" in dli["note"]
        ec = wiki.card_view(wiki.find("glossary", "ec"), lang, ["cannabis"])["image"]["photo"]
        assert ec["compare"] is False and ec["note"].strip()
        basil = wiki.card_view(wiki.find("crops", "basil"), lang, ["cannabis"])["image"]["photo"]
        assert "note" not in basil and "compare" not in basil
    assert "rain gauge" in wiki.card_view(wiki.find("glossary", "dli"), "en", ["cannabis"])["image"]["photo"]["note"]
    for cid in ("glossary/dli", "glossary/overwatering", "glossary/ec", "glossary/ph"):
        raw = raw_images()[cid]
        assert raw["note"].strip() and raw["note_en"].strip() and raw["note"] != raw["note_en"], cid
        assert EM_DASH not in raw["note"] + raw["note_en"], cid


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_comparison_line_stands_above_the_credit() -> None:
    cards = {cid: wiki.card_view(wiki.find(*cid.split("/")), "de", ["cannabis"]) for cid in ("glossary/dli", "glossary/ph")}
    out, err = run_page(cards)
    assert not err, err
    assert "wiki.credit.compare" in out["glossary/dli"]["text"] and "Regenmesser" in out["glossary/dli"]["text"]
    assert "wiki.credit.note" in out["glossary/ph"]["text"] and "wiki.credit.compare" not in out["glossary/ph"]["text"]


def test_english_source_titles_carry_their_research_notes_in_english() -> None:
    en = wiki.source_title_en
    assert en("Uni, Basil, Ratgeber ohne Autor und Datum, abgerufen 2026-10-22") == (
        "Uni, Basil, guide, no author or date, retrieved 2026-10-22")
    assert en("Zamnesia, Blog eines Samenhändlers (Erfahrungsquelle)") == "Zamnesia, blog of a seed shop (practical experience)"
    assert en("Walters und Currey (Iowa State University) 2015, Air Temperature") == (
        "Walters and Currey (Iowa State University) 2015, Air Temperature")
    assert en("Penn State Extension, Growing Microgreens (Elsa Sánchez und Robert Berghage, updated 2025)") == (
        "Penn State Extension, Growing Microgreens (Elsa Sánchez and Robert Berghage, updated 2025)")
    assert en("Webinar vom 8. Februar 2023, S. 3 und 4") == "Webinar of 8 February 2023, pp. 3 and 4"
    # the title of a German work stays as it was published
    for title in ("Bayerische Landesanstalt für Weinbau und Gartenbau (LWG)", "Freiland und Folientunnel",
                  "Deutscher Wetterdienst (DWD), Wetter- und Klimalexikon, Relative Feuchte"):
        assert en(title) == title
    assert wiki.parse_source("Q1", "Uni, abgerufen 2026-10-22: https://example.org/x", "de")["title"] == (
        "Uni, abgerufen 2026-10-22")
    assert wiki.parse_source("Q1", "Uni, abgerufen 2026-10-22: https://example.org/x", "en") == {
        "id": "Q1", "title": "Uni, retrieved 2026-10-22", "url": "https://example.org/x"}
    assert wiki.parse_source("Q2", {"title": "Titel, abgerufen 2026", "title_en": "Own title"}, "en")["title"] == "Own title"
    notes = re.compile(r"\b(?:abgerufen|Erfahrungsquelle|Ratgeber|Fachartikel|ohne Datum|veröffentlicht|aktualisiert)\b")
    for entry in wiki._entries(wiki.LIBRARY):
        for src in wiki.card_view(entry, "en", ["cannabis"])["sources"]:
            assert not notes.search(src["title"]), (entry["key"], src["title"])


def test_list_pictures_are_decorative_and_the_card_keeps_the_alt_text() -> None:
    src = wiki_js()
    body = src[src.index("function media(card)"):src.index("function dayText(")]
    assert 'img.alt = "";' in body and "svgPanel(fig, \"\")" in body and "artAlt" not in body
    assert "img.alt = textOf(photo.alt);" in src  # photoImg: the card's own picture carries its alt text


def test_the_your_plant_card_and_narrow_phones_keep_their_size() -> None:
    css = (WEB / "wiki.css").read_text(encoding="utf-8")
    assert ".wiki-feature .wiki-media { aspect-ratio: auto; height: 300px; }" in css
    assert "@media (max-width: 359px) { .wiki-tiles > li.wide { grid-column: auto; } }" in css
