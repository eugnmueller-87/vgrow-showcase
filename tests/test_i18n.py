"""German and English: catalog parity, content twins, localize(), number format, X-Lang and set_lang."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, i18n
from vgrow import server as server_mod
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation
from vgrow.demo import DEMO_READING, DemoBox
from vgrow.i18n import fmt_num, fmt_qty, localize, t
from vgrow.plant import PlantLog
from vgrow.server import COOKIE, make_app, page_html
from vgrow.vision import SYSTEM

REPO = Path(__file__).resolve().parent.parent
CONTENT = [REPO / "alerts.json", REPO / "plant.json", REPO / "ports.json",
           *sorted((REPO / "kartei").rglob("*.json"))]
UNITS = ("°C", "%", "kPa", "mS/cm")


def load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((REPO / name).read_text(encoding="utf-8"))


# ---------- catalog ----------

def test_catalogs_have_the_same_keys_and_placeholders() -> None:
    assert set(i18n.DE) == set(i18n.EN)
    for key in i18n.DE:
        assert i18n.placeholders(i18n.DE[key]) == i18n.placeholders(i18n.EN[key]), key
        assert "lang" not in i18n.placeholders(i18n.DE[key]), key  # t(key, lang) would swallow it


def test_console_texts_stay_ascii_and_nothing_has_an_em_dash() -> None:
    for catalog in (i18n.DE, i18n.EN):
        for key, text in catalog.items():
            assert "\u2014" not in text, key
            if key.startswith("console."):
                assert text.isascii(), key
    # vgrow/** recursively (vgrow/hank/ too); vgrow/vivosun/ is upstream code copied unchanged (UPSTREAM.md).
    vendored = REPO / "vgrow" / "vivosun"
    ours = [p for p in sorted((REPO / "vgrow").rglob("*")) if p.suffix in (".py", ".json") and vendored not in p.parents]
    for path in [*CONTENT, *ours]:
        assert "\u2014" not in path.read_text(encoding="utf-8"), path.name
    assert "\u2014" not in json.dumps(DEMO_READING, ensure_ascii=False) + SYSTEM["de"] + SYSTEM["en"]


def test_system_prompts_ask_for_their_language() -> None:
    assert SYSTEM["de"].endswith("Antworte auf Deutsch, knapp.")
    assert SYSTEM["en"].endswith("Answer in English, briefly.")


def test_t_uses_the_request_language_else_the_server_language() -> None:
    assert t("push.plant") == "Pflanze"
    token = i18n.REQUEST_LANG.set("en")
    try:
        assert t("push.plant") == "Plant" and t("push.plant", "de") == "Pflanze"
        assert t("vision.limit", count=5) == "Already 5 assessments today"
    finally:
        i18n.REQUEST_LANG.reset(token)
    assert t("no.such.key") == "no.such.key"  # never crash on a missing text


def test_server_language_settings_then_env_then_german(isolated_state) -> None:  # type: ignore[no-untyped-def]
    assert i18n.server_lang() == "de"
    config.ENV_FILE.write_text("VGROW_LANG=en\n", encoding="utf-8")
    assert i18n.server_lang() == "en"
    isolated_state.mkdir(parents=True, exist_ok=True)
    (isolated_state / "settings.json").write_text(json.dumps({"lang": "de", "other": 1}), encoding="utf-8")
    assert i18n.server_lang() == "de"  # settings.json wins over ~/.vgrow.env
    assert i18n.set_server_lang("en") == "en"
    raw = (isolated_state / "settings.json").read_bytes()
    assert b"\r\n" not in raw and json.loads(raw) == {"lang": "en", "other": 1}  # other keys kept, LF
    with pytest.raises(ValueError):
        i18n.set_server_lang("fr")


def test_numbers_use_the_language_separator_and_keep_metric_units() -> None:
    assert fmt_num(31.5, "de", 1) == "31,5" and fmt_num(31.5, "en", 1) == "31.5"
    assert fmt_num(30, "de") == "30" and fmt_num(6.25, "de") == "6,25" and fmt_num(6.0, "en") == "6"
    assert fmt_num(None, "de") == "?"
    assert fmt_qty(79.44, "%", "de", 1) == "79,4 %" and fmt_qty(79.44, "%", "en", 1) == "79.4%"
    assert fmt_qty(22, "°C", "en", 1) == "22.0 °C" and fmt_qty(1.4, "mS/cm", "de") == "1,4 mS/cm"


# ---------- localize ----------

def test_localize_takes_twins_in_english_and_drops_them_always() -> None:
    data = {"label": "Zu warm", "label_en": "Too warm", "min": 20, "sources": ["S5"],
            "targets": [{"advice_low": "Abluft runter", "advice_low_en": "Exhaust fan down"}],
            "warnings": ["a", "b"], "warnings_en": ["A", "B"], "note": "nur deutsch", "start_en": None}
    before = json.dumps(data, sort_keys=True)
    en, de = localize(data, "en"), localize(data, "de")
    assert en == {"label": "Too warm", "min": 20, "sources": ["S5"], "targets": [{"advice_low": "Exhaust fan down"}],
                  "warnings": ["A", "B"], "note": "nur deutsch"}
    assert de == {"label": "Zu warm", "min": 20, "sources": ["S5"], "targets": [{"advice_low": "Abluft runter"}],
                  "warnings": ["a", "b"], "note": "nur deutsch"}
    assert json.dumps(data, sort_keys=True) == before  # a copy, the loaded profile stays intact
    assert list(en) == ["label", "min", "sources", "targets", "warnings", "note"]  # keys keep their place


# ---------- content twins ----------

def _displayed_pairs() -> list[tuple[str, object, object]]:
    """Every displayed German field with its twin, as the contract lists them (missing twin: None)."""
    pairs: list[tuple[str, object, object]] = []

    def pair(where: str, obj: dict, key: str) -> None:  # type: ignore[type-arg]
        if key in obj:
            pairs.append((f"{where}.{key}", obj[key], obj.get(f"{key}_en")))

    stages_card = load("kartei/stages/autoflower.json")
    for key in ("summary", "notes"):  # read by Hank
        pair("stages", stages_card, key)
    for stage in stages_card["stages"]:
        where = f"stages/{stage['key']}"
        pair(where, stage, "label")
        pair(where, stage, "preset_note")
        if "watering" in stage:
            pair(f"{where}.watering", stage["watering"], "start")
        for i, target in enumerate(stage.get("targets", [])):
            for key in ("label", "advice_high", "advice_low", "consequence_high", "consequence_low"):
                pair(f"{where}.targets[{i}]", target, key)
    for path in sorted((REPO / "kartei" / "strains").glob("*.json")):
        strain = json.loads(path.read_text(encoding="utf-8"))
        for key in ("warnings", "summary", "facts"):
            pair(f"strains/{path.stem}", strain, key)
    for path in sorted((REPO / "kartei" / "legal").glob("*.json")):  # card contract 2.8 of the Hank spec
        card, where = json.loads(path.read_text(encoding="utf-8")), f"legal/{path.stem}"
        for key in ("name", "summary"):
            pair(where, card, key)
        for fact in card["facts"]:
            pair(f"{where}.facts.{fact['id']}", fact, "text")
        for name, source in card["sources"].items():
            pair(f"{where}.sources.{name}", source, "title")
    for path in sorted((REPO / "kartei" / "nutrients").glob("*.json")):
        card, where = json.loads(path.read_text(encoding="utf-8")), f"nutrients/{path.stem}"
        for key in ("name", "summary", "status_note"):
            pair(where, card, key)
        _market_pairs(pair, where, card)  # the fertilizer market (23.10.2026): line, choice, types, market
        if "weeks" not in card:  # a card without a readable chart (BioTabs, CANNA COCO, the overview)
            for i, item in enumerate(card.get("notes", [])):
                pair(f"{where}.notes[{i}]", item, "text")
            for name, source in card.get("sources", {}).items():
                pair(f"{where}.sources.{name}", source, "title")
            continue
        weeks = card["weeks"]
        tails = [weeks[k] for k in ("water", "harvest", "end") if k in weeks]  # VIVOSUN: the table's end, no water
        for i, item in enumerate([*weeks["columns"], *tails]):
            pair(f"{where}.weeks[{i}]", item, "label")
        for name in ("measure", "parts"):  # the label's spoon and its "equal amounts" (VIVOSUN)
            if name in card:
                pair(f"{where}.{name}", card[name], "text")
        if "measure" in card:
            pair(f"{where}.measure", card["measure"], "label")
        for name, item in [*card["components"].items(), *card["pages"].items()]:
            pair(f"{where}.{name}", item, "label")
        for i, item in enumerate([card["frequency"], *card["notes"]]):
            pair(f"{where}.notes[{i}]", item, "text")
        for name, source in card["sources"].items():
            pair(f"{where}.sources.{name}", source, "title")
    plant = load("plant.json")
    pair("plant.stage_estimate", plant["stage_estimate"], "note")
    pair("plant.medium", plant["medium"], "name")
    for name, target in plant["water_targets"].items():
        pair(f"plant.water_targets.{name}", target, "label")
        pair(f"plant.water_targets.{name}", target, "note")
    for key in ("dose_factor_note", "bloom_start_note"):
        pair("plant.feeding", plant["feeding"], key)
    for name, own in plant["feeding"]["setups"].items():  # two fertilizers since 23.10.2026
        pair(f"plant.feeding.setups.{name}", own, "label")
        pair(f"plant.feeding.setups.{name}", own, "page_note")
    for name, rule in load("alerts.json")["rules"].items():
        pair(f"alerts.{name}", rule, "label")
    for name, port in load("ports.json").items():
        if isinstance(port, dict):
            pair(f"ports.{name}", port, "label")
    return pairs


def _market_pairs(pair, where: str, card: dict) -> None:  # type: ignore[no-untyped-def,type-arg]
    """The displayed texts of the fertilizer market blocks: the line (where it is used, how it is applied, each
    product's role, the cautions, a plan in words), the switch's label and page note, the overview's types and its
    market facts."""
    line = card.get("line") or {}
    for key in ("media_text", "application", "application_short"):
        pair(f"{where}.line", line, key)
    for i, product in enumerate(line.get("products", [])):
        pair(f"{where}.line.products[{i}]", product, "role")
    for i, item in enumerate(line.get("cautions", [])):
        pair(f"{where}.line.cautions[{i}]", item, "text")
    for i, item in enumerate(line.get("plan", [])):
        pair(f"{where}.line.plan[{i}]", item, "when")
        pair(f"{where}.line.plan[{i}]", item, "text")
    if "selectable" in card:
        pair(f"{where}.selectable", card["selectable"], "label")
        pair(f"{where}.selectable", card["selectable"], "page_note")
    if "boosters" in card:
        pair(f"{where}.boosters", card["boosters"], "text")
    for i, kind in enumerate(card.get("types", [])):
        for key in ("label", "suits", "applied"):
            pair(f"{where}.types[{i}]", kind, key)
        for j, item in enumerate(kind.get("mistakes", [])):
            pair(f"{where}.types[{i}].mistakes[{j}]", item, "text")
    for i, item in enumerate(card.get("market", [])):
        pair(f"{where}.market[{i}]", item, "text")


def _all_twins() -> list[tuple[str, object, object]]:
    """Every *_en key anywhere in the content files, with its base value."""
    found: list[tuple[str, object, object]] = []

    def walk(obj: object, where: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.endswith("_en"):
                    found.append((f"{where}.{k}", obj.get(k[:-3]), v))
                walk(v, f"{where}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{where}[{i}]")

    for path in CONTENT:
        walk(json.loads(path.read_text(encoding="utf-8")), path.name)
    return found


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _numbers(text: str) -> list[float]:
    """All numbers, dates written as y-m-d first (22.10.2026, 10/22/2026 and Oct 22, 2026 are the same day)."""
    text = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", lambda m: f"{m[3]}-{int(m[2])}-{int(m[1])}", text)
    text = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", lambda m: f"{m[3]}-{int(m[1])}-{int(m[2])}", text)
    text = re.sub(r"\b(" + "|".join(MONTHS) + r") (\d{1,2}), (\d{4})\b",
                  lambda m: f"{m[3]}-{MONTHS.index(m[1]) + 1}-{int(m[2])}", text)
    return sorted(float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text))


def _texts(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _blocked_fact_key(key: str) -> bool:
    """A strain fact the chat never shows (vgrow/hank/data/chat_policy.json, decision D3) needs no English twin."""
    policy = json.loads((REPO / "vgrow" / "hank" / "data" / "chat_policy.json").read_text(encoding="utf-8"))
    return key.lower().startswith(tuple(policy["strain_fields"]["blocked"]))


def _twin_texts(where: str, base: object, twin: object) -> tuple[list[str], list[str]]:
    """Base and twin as parallel text lists. Dicts (strain facts) pair key by key: the twin uses the same keys (they are
    ids), and only a blocked key may be left out of it."""
    if isinstance(base, dict) and isinstance(twin, dict):
        assert set(twin) <= set(base), where
        left_out = [k for k in base if k not in twin]
        assert all(_blocked_fact_key(k) for k in left_out), (where, left_out)
        return [str(base[k]) for k in twin], [str(twin[k]) for k in twin]
    return _texts(base), _texts(twin)


def test_every_displayed_german_field_has_an_english_twin() -> None:
    pairs = _displayed_pairs()
    assert len(pairs) > 100  # 6 stages with their targets, plus plant, alerts, ports, strain
    missing = [where for where, _, twin in pairs if not twin]
    assert missing == []
    for where, base, twin in pairs:
        assert type(base) is type(twin), where
        if isinstance(base, list):
            assert len(base) == len(twin), where  # type: ignore[arg-type]


def test_twins_keep_numbers_units_and_source_ids() -> None:
    twins = _all_twins()
    assert twins
    for where, base, twin in twins:
        assert base is not None, f"{where}: twin without a German base"
        for b, e in zip(*_twin_texts(where, base, twin), strict=True):
            assert _numbers(b) == _numbers(e), (where, b, e)
            ids = r"\b[SWN]\d+\b"  # source ids: S stage card, W plant.json, N nutrient card
            assert sorted(re.findall(ids, b)) == sorted(re.findall(ids, e)), (where, b, e)
            for unit in UNITS:
                assert b.count(unit) == e.count(unit), (where, unit, b, e)
            assert "\u2014" not in e


def test_stage_labels_keep_the_cut_convention() -> None:
    labels = {s["key"]: s["label_en"] for s in load("kartei/stages/autoflower.json")["stages"]}
    assert labels == {"seedling": "Seedling", "veg": "Vegetative", "early_flower": "Early flower (stretch)",
                      "mid_flower": "Mid flower", "late_flower": "Late flower (ripening)", "harvest": "Harvest window"}
    for stage in load("kartei/stages/autoflower.json")["stages"]:
        assert (" (" in stage["label"]) == (" (" in stage["label_en"]), stage["key"]


# ---------- server ----------

def _client(app):  # type: ignore[no-untyped-def]
    client = TestClient(TestServer(app))
    return client


def _no_twins_left(obj: object) -> bool:
    if isinstance(obj, dict):
        return not any(isinstance(k, str) and k.endswith("_en") for k in obj) and all(map(_no_twins_left, obj.values()))
    if isinstance(obj, list):
        return all(map(_no_twins_left, obj))
    return True


def test_x_lang_header_picks_the_language_of_each_request() -> None:
    async def run() -> None:
        box, alerts, log = DemoBox(), Alerts(), PlantLog()
        app = make_app(box, alerts, "k", plant_log=log, automation=HumidifierAutomation(box, alerts, log))
        async with _client(app) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            en = await (await client.get("/api/state", headers={"X-Lang": "en"})).json()
            de = await (await client.get("/api/state")).json()
            odd = await (await client.get("/api/state", headers={"X-Lang": "fr"})).json()
        assert (en["lang"], en["server_lang"]) == ("en", "de")
        assert (de["lang"], de["server_lang"]) == ("de", "de") and odd["lang"] == "de"
        assert [a["label"] for a in en["alerts"]][:3] == ["Too cold (probe)", "Too warm (probe)", "Too humid (probe)"]
        assert [a["label"] for a in de["alerts"]][:3] == ["Zu kalt (Fühler)", "Zu warm (Fühler)", "Zu feucht (Fühler)"]
        # the owner's real ports.json: the drip pump on USB 1 since 23.10.2026
        assert en["ports"]["usb1"]["label"] == "Drip irrigation (pump)"
        assert de["ports"]["usb1"]["label"] == "Tropfbewässerung (Pumpe)"
        assert en["plant"]["stage"]["label"] == "Early flower (stretch)"
        assert de["plant"]["stage"]["label"] == "Frühe Blüte (Stretch)"
        assert en["plant"]["stage"]["estimate_note"].startswith("Estimated from the camera image")
        assert en["plant"]["stage"]["watering"]["start"] == "1 hour after lights on"
        assert en["plant"]["strain"]["warnings"][0].startswith("Can outgrow the box")
        assert "source_notes" not in en["plant"]["strain"]  # research notes stay on the server
        assert not any("Zamnesia" in w for w in en["plant"]["strain"]["warnings"] + de["plant"]["strain"]["warnings"])
        assert en["plant"]["water_targets"]["ph"]["label"] == "pH of the nutrient solution"
        # The product name stays as sold; only the part in brackets is translated.
        assert en["plant"]["medium"]["name"] == "420Flow Grow Erde Lightmix (soil with perlite)"
        assert "Cannabis grows" not in de["plant"]["water_targets"]["ph"]["note"]  # no English quote in German
        light = next(c for c in en["plant"]["checks"] if c["metric"] == "light_percent")
        assert light["label"] == "Light" and light["advice"].startswith("Light below the VGrow flower preset (100%)")
        assert _no_twins_left(en) and _no_twins_left(de)
        assert en["automation"]["recent"] == [] and en["automation"]["stale"] is False
        assert en["automation"]["estimate"]["label"] == "Early flower (stretch)"
        assert de["automation"]["estimate"]["label"] == "Frühe Blüte (Stretch)"
        assert en["plant"]["feeding"]["card"]["name"] == "BioBizz nutrient schedule 2026"
        assert de["plant"]["feeding"]["frequency_note"].startswith("Die Tabelle sagt nicht")

    asyncio.run(run())


def test_errors_come_in_the_request_language(locked_ports) -> None:  # type: ignore[no-untyped-def]
    # the lock's refusal from a ports file that locks USB 1 (tests/fixtures/ports-locked.json); the real one locks none
    async def run() -> None:
        app = make_app(DemoBox(), Alerts(), "k")
        async with _client(app) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            en = {"X-Lang": "en"}

            async def err(body: dict, headers: dict | None = None) -> tuple[int, str]:  # type: ignore[type-arg]
                resp = await client.post("/api/cmd", json=body, headers=headers or {})
                return resp.status, (await resp.json())["error"]

            assert await err({"action": "nope"}, en) == (400, "Unknown action 'nope'")
            assert await err({"action": "nope"}) == (400, "Unbekannte Aktion 'nope'")
            assert await err({"action": "port", "port": "usb1", "on": False}, en) == (
                409, "Camera always stays on (ports.json)")
            assert await err({"action": "port", "port": "usb1", "on": False}) == (409, "Kamera bleibt immer an (ports.json)")
            assert await err({"action": "plant_height", "cm": 0}, en) == (400, "Height in cm between 1 and 300")
            assert await err({"action": "plant_water", "ph": "abc"}, en) == (400, "Not a number: abc")
            assert await err({"action": "plant_water"}) == (400, "pH oder EC eintragen")
            assert await err({"action": "light"}, en) == (400, "Bad request: 'level'")
            assert (await err({"action": "spectrum", "name": "x"}, en))[1] == "Unknown spectrum 'x'"
            ok = await client.post("/api/cmd", json={"action": "plant_water", "ph": "6,3"})
            assert ok.status == 200  # a decimal comma is fine
            client.session.cookie_jar.clear()
            forbidden = await client.get("/api/state", headers=en)
            assert forbidden.status == 403 and await forbidden.text() == "Open the link with ?key=... once on this device."
            wrong = await client.get("/?key=nope", allow_redirects=False)
            assert wrong.status == 403 and await wrong.text() == "Falscher Schlüssel"

    asyncio.run(run())


def test_set_lang_persists_the_server_language(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        app = make_app(DemoBox(), Alerts(), "k")
        async with _client(app) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            resp = await client.post("/api/cmd", json={"action": "set_lang", "lang": "en"}, headers={"X-Lang": "en"})
            assert resp.status == 200 and await resp.json() == {"ok": True, "server_lang": "en"}
            state = await (await client.get("/api/state")).json()  # no header: the server language now
            assert (state["lang"], state["server_lang"]) == ("en", "en") and state["alerts"][0]["label"] == "Too cold (probe)"
            bad = await client.post("/api/cmd", json={"action": "set_lang", "lang": "fr"})
            assert bad.status == 400 and "fr" in (await bad.json())["error"]
            page = await (await client.get("/")).text()
            assert 'data-default-lang="en"' in page
        assert json.loads((isolated_state / "settings.json").read_text(encoding="utf-8")) == {"lang": "en"}
        assert i18n.server_lang() == "en"

    asyncio.run(run())


def test_page_html_sets_language_and_versions_every_static_file() -> None:
    html = ('<!doctype html>\n<html class="x" lang="de">\n<head><link rel="stylesheet" href="/static/style.css">'
            '<link rel="stylesheet" href="/vendor/uPlot.min.css"><script src="/static/i18n.js"></script>'
            '<script src="/static/app.js"></script></head></html>')
    out = page_html(html, "123", "en", "de")
    assert '<html lang="en" data-default-lang="de" class="x">' in out
    assert out.count("?v=123") == 3 and 'src="/static/i18n.js?v=123"' in out
    assert 'href="/vendor/uPlot.min.css"' in out  # vendored files are pinned, no version
    assert page_html(out, "124", "de", "de").count("?v=") == 3  # never versioned twice


def test_index_and_web_version_cover_i18n_js(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    web = tmp_path / "web"
    (web / "vendor").mkdir(parents=True)
    (web / "index.html").write_text('<html lang="de"><script src="/static/i18n.js"></script>'
                                    '<script src="/static/app.js"></script></html>', encoding="utf-8")
    (web / "app.js").write_text("", encoding="utf-8")
    (web / "style.css").write_text("", encoding="utf-8")
    now = int(time.time())
    for name in ("index.html", "app.js", "style.css"):
        os.utime(web / name, (now - 100, now - 100))
    monkeypatch.setattr(server_mod, "WEB", web)
    assert server_mod.web_version() == str(now - 100)  # i18n.js missing: tolerated
    (web / "i18n.js").write_text("", encoding="utf-8")
    os.utime(web / "i18n.js", (now, now))
    assert server_mod.web_version() == str(now)  # and counted once it exists

    async def run() -> None:
        app = make_app(DemoBox(), Alerts(), "k")
        async with _client(app) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            page = await (await client.get("/")).text()
            assert '<html lang="de" data-default-lang="de">' in page
            assert f'/static/i18n.js?v={now}"' in page and f'/static/app.js?v={now}"' in page

    asyncio.run(run())


def test_manifest_and_browser_loads_follow_the_page_language() -> None:
    async def run() -> None:
        app = make_app(DemoBox(), Alerts(), "k")
        async with _client(app) as client:  # no cookie: the home-screen install fetches it without one
            de = await (await client.get("/manifest.webmanifest")).json(content_type=None)
            en = await (await client.get("/manifest.webmanifest?lang=en")).json(content_type=None)
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            by_query = await (await client.get("/api/state?lang=en")).json()
            header_wins = await (await client.get("/api/state?lang=en", headers={"X-Lang": "de"})).json()
        assert de["name"] == de["short_name"] == "VGrow Cockpit (inoffiziell)" and de["lang"] == "de"
        assert de["description"].startswith("Inoffizielles Cockpit")
        assert en["name"] == en["short_name"] == "VGrow Cockpit (unofficial)" and en["lang"] == "en"
        assert en["start_url"] == "/" and en["icons"]  # the fixed parts come from the file
        assert by_query["lang"] == "en" and header_wins["lang"] == "de"

    asyncio.run(run())


def test_real_index_page_gets_the_language_attributes() -> None:
    async def run() -> None:
        app = make_app(DemoBox(), Alerts(), "k")
        async with _client(app) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            page = await (await client.get("/")).text()
        assert re.search(r'<html lang="de" data-default-lang="de"', page)
        assert not re.search(r'(?:src|href)="/static/[^"?]+"', page)  # every /static/ file carries ?v=

    asyncio.run(run())
