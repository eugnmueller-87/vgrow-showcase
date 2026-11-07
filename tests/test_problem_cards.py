"""The problem cards of kartei/problems/ (cannabis package, wave 1, draft, 23.10.2026): the wiki's problem format,
German and English twins with the same numbers, units and source ids, sources that exist and are cited, the VIVOSUN
rule (no page first fetched automatically on 23.10.2026 stands behind a visible statement; a page that was already in
the kartei on 22.10.2026 counts as a source, per the order of 23.10.2026), check items on metrics the cockpit knows, and
the wiki showing them under cannabis and hiding them with the cannabis switch off."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import test_wiki as tw
from test_i18n import UNITS, _numbers
from vgrow import config, wiki

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "kartei" / "problems"
CARDS = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(FOLDER.glob("*.json"))}
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
LISTS = ("symptoms", "causes", "do_now", "prevention", "look_alikes")
GROUPS = {"naehrstoffe", "schaedlinge", "krankheiten", "stress"}
ID_RE = re.compile(r"(?<![\w])(P\d{1,3})(?![\w])")
# The two VIVOSUN pages first fetched (automatically) on 23.10.2026: nothing visible may rest on them.
HELD_URLS = {
    "https://vivosun.com/growing_guide/how-to-fix-cannabis-heat-stress",
    "https://vivosun.com/growing_guide/light-burn-and-light-stress",
}
HELD_REASON = "VIVOSUN-Seite automatisch abgerufen, erst nach Lesen von Hand verwenden"
# The lamp distance guide: in the kartei as S7 since 22.10.2026 (commit 406af3f), so a source per the order of
# 23.10.2026; held until that order.
DISTANCE_URL = "https://vivosun.com/growing_guide/how-far-away-should-lights-be.md"
# The four that were in the kartei on 22.10.2026, and where.
KEPT = {
    "https://vivosun.com/support/guide/vgrow-smart-grow-box.md": ("kartei/stages/autoflower.json", "S1"),
    "https://vivosun.com/growing_guide/temperature-and-humidity.md": ("kartei/stages/autoflower.json", "S5"),
    DISTANCE_URL: ("kartei/stages/autoflower.json", "S7"),
    "https://vivosun.com/en-CA/growing_guide/what-are-autoflowering-seeds/": ("kartei/nutrients/biobizz-2026.json",
                                                                              "N5"),
}


def url_of(source: Any) -> str:
    """Both source forms: "Title: URL" and {title, title_en, url, ...} (the practice source of 23.10.2026)."""
    if isinstance(source, dict):
        return str(source.get("url") or "")
    return source[source.rfind(": http") + 2:].strip()


def visible(card: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in card.items() if not k.startswith("_") and k != "sources"}


def pairs(card: dict[str, Any]) -> list[tuple[str, str, str]]:
    out = [("label", card["label"], card["label_en"])]
    for field in LISTS:
        assert len(card[field]) == len(card[f"{field}_en"]), field
        out += [(f"{field}[{i}]", de, en) for i, (de, en) in enumerate(zip(card[field], card[f"{field}_en"]))]
    out += [(f"check[{i}]", c["text"], c["text_en"]) for i, c in enumerate(card["check"])]
    return out


def known_metrics() -> set[str]:
    """What the cockpit knows: the targets of the stage card (the climate at the probe, light_percent from the light
    level), and "watering", the watering plan the stages of that card carry (all but the harvest window)."""
    stages = json.loads((ROOT / "kartei" / "stages" / "autoflower.json").read_text(encoding="utf-8"))["stages"]
    metrics = {t["metric"] for st in stages for t in st.get("targets", [])}
    if any("watering" in st for st in stages):
        metrics.add("watering")
    return metrics


# ---------- the files ----------

def test_every_problem_card_loads_in_the_wiki_format() -> None:
    assert len(CARDS) == 27
    for stem, card in CARDS.items():
        assert card["key"] == stem and card["kind"] == "problem", stem
        assert card["package"] == "cannabis" and card["status"] == "draft" and card["group"] in GROUPS, stem
        assert card["label"].strip() and card["label_en"].strip(), stem
        assert set(card["aliases"]) == {"de", "en"} and all(card["aliases"][lang] for lang in ("de", "en")), stem
        for field in LISTS:
            assert card[field] and all(isinstance(s, str) and s.strip() for s in card[field]), (stem, field)
        assert card["check"] and all(set(c) == {"metric", "text", "text_en"} for c in card["check"]), stem
        assert isinstance(card["_left_out"], list), stem
        for old in ("check_box", "prevent", "lookalikes", "left_out", "aliases_en", "category"):
            assert old not in card, (stem, old)
        entry = wiki.find("problems", stem)
        assert entry is not None and entry["category"] == "cannabis" and entry["package"] == "cannabis", stem
    for path in FOLDER.glob("*.json"):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw, path.name
        assert EM_DASH not in raw.decode("utf-8"), path.name


def test_twins_keep_numbers_units_and_source_ids() -> None:
    for stem, card in CARDS.items():
        for where, de, en in pairs(card):
            assert de.strip() and en.strip(), (stem, where)
            assert _numbers(de) == _numbers(en), (stem, where, de, en)
            assert sorted(ID_RE.findall(de)) == sorted(ID_RE.findall(en)), (stem, where, de, en)
            for unit in UNITS:
                assert de.count(unit) == en.count(unit), (stem, where, unit)


def test_cited_sources_exist_and_every_source_is_cited() -> None:
    for stem, card in CARDS.items():
        cited = set(ID_RE.findall(json.dumps(visible(card), ensure_ascii=False)))
        assert cited == set(card["sources"]), (stem, cited ^ set(card["sources"]))
        assert all(url_of(s).startswith("https://") for s in card["sources"].values()), stem


# ---------- VIVOSUN: no automatic fetch behind a visible statement ----------

def test_no_vivosun_source_comes_from_the_automatic_fetch_of_23_10() -> None:
    kept = 0
    for stem, card in CARDS.items():
        for sid, source in card["sources"].items():
            if "vivosun.com" not in source:
                continue
            url = url_of(source)
            assert url not in HELD_URLS, (stem, sid)
            assert "2026-10-23" not in source, (stem, sid)
            file, kartei_id = KEPT[url]
            assert f"aus der Kartei übernommen ({file}, {kartei_id}), abgerufen 2026-10-22" in source, (stem, sid)
            other = json.loads((ROOT / file).read_text(encoding="utf-8"))["sources"][kartei_id]
            assert url in json.dumps(other), (stem, sid)  # the kartei card really holds this page
            kept += 1
        text = json.dumps(visible(card), ensure_ascii=False)
        assert not any(url in text for url in HELD_URLS), stem
    assert kept == 22  # 10 manual, 8 climate guide, 2 autoflower page, 2 lamp distance guide


def test_statements_on_the_held_pages_are_listed_for_reading_by_hand() -> None:
    held = {stem: [e for e in card["_left_out"] if isinstance(e, dict) and e.get("url") in HELD_URLS]
            for stem, card in CARDS.items()}
    assert {stem for stem, items in held.items() if items} == {"heat_stress", "light_burn", "wind_burn"}
    assert {stem: len(items) for stem, items in held.items() if items} == {"heat_stress": 7, "light_burn": 7,
                                                                            "wind_burn": 1}
    for stem, items in held.items():
        shown = json.dumps(visible(CARDS[stem]), ensure_ascii=False)
        for item in items:
            assert item["reason"] == HELD_REASON and item["text"].strip() and item["text_en"].strip(), stem
            assert _numbers(item["text"]) == _numbers(item["text_en"]), (stem, item["text"])
            if item["action"] == "entfernt":
                assert item["text"] not in shown, (stem, item["text"])
            else:
                assert item["now"] in shown and item["text"] not in shown, (stem, item["text"])


# The statements held back only because of the lamp distance guide, back since the order of 23.10.2026 counts kartei
# S7 as a source: (card, section, position it had before the hold, start of the German text). Each is back at that
# position and cites that page, taken over from kartei S7 with the date 2026-10-22. Not among them: the heat stress
# cause "Lampe zu stark oder zu nah", which never rested on S7 (see test_the_lamp_cause_of_heat_stress_stays_held).
RESTORED = [
    ("light_burn", "symptoms", 0, "Früh: gebleichte oder weiße Blattspitzen an den Blättern, die der Lampe am "
                                  "nächsten sind; gelbe oder braune Flecken; Blätter rollen nach oben (P1, "
                                  "Erfahrungsquelle)."),
    ("light_burn", "causes", 0, "Lampe zu stark oder zu nah. VIVOSUN nennt Richtwerte (P1, Erfahrungsquelle): nach "
                                "Leistung 12 bis 24 Zoll für LEDs mit 100 bis 300 W"),
    ("light_burn", "prevention", 0, "Beim Einstellen oben anfangen und nach unten arbeiten: VIVOSUN rät zum größten "
                                    "empfohlenen Abstand; von zu wenig Licht erholt sich eine Pflanze leichter als von "
                                    "Lichtbrand (P1, Erfahrungsquelle)."),
    ("light_burn", "check", 0, "Gegen Voreinstellung (Keimling 60, Wachstum 80, Blüte 100 % P4). Der VIVOSUN-"
                               "Abstandsratgeber nennt für Keimlinge 25 bis 50 % zum Start (P1, Erfahrungsquelle)"),
    ("heat_stress", "prevention", 1, "Warme Zuluft vermeiden, Licht nur schrittweise erhöhen und vorher den Abstand "
                                     "prüfen (siehe Karte Lichtbrand; P1, P11, Erfahrungsquellen)."),
    ("heat_stress", "look_alikes", 0, "Lichtbrand: Schaden an den Blättern nahe der Lampe (siehe Karte Lichtbrand, "
                                      "P11, Erfahrungsquelle)."),
]


def test_statements_of_the_lamp_distance_guide_are_back_and_cite_kartei_s7() -> None:
    for stem, section, position, text in RESTORED:
        card = CARDS[stem]
        lines = [c["text"] for c in card["check"]] if section == "check" else card[section]
        twins = [c["text_en"] for c in card["check"]] if section == "check" else card[f"{section}_en"]
        hit = [i for i, s in enumerate(lines) if s.startswith(text)]
        assert hit == [position], (stem, section, hit, text)  # back once, where it stood before the hold
        de, en = lines[position], twins[position]
        ids = set(ID_RE.findall(de))
        assert ids == set(ID_RE.findall(en)), (stem, de, en)
        distance = [i for i in ids if url_of(card["sources"][i]) == DISTANCE_URL]
        assert len(distance) == 1, (stem, de)
        source = card["sources"][distance[0]]
        assert "aus der Kartei übernommen (kartei/stages/autoflower.json, S7), abgerufen 2026-10-22" in source, stem
        assert "2026-10-23" not in source, stem
    s7 = json.loads((ROOT / "kartei" / "stages" / "autoflower.json").read_text(encoding="utf-8"))["sources"]["S7"]
    assert url_of(s7) == DISTANCE_URL
    for stem, card in CARDS.items():  # nothing stays held for the distance guide alone
        assert not [e for e in card["_left_out"] if isinstance(e, dict) and e.get("url") == DISTANCE_URL], stem
    assert CARDS["light_burn"]["status_note"].startswith("Unvollständig: Zwei Herstellerseiten")
    assert CARDS["light_burn"]["status_note_en"].startswith("Incomplete: two maker pages")


def test_nothing_from_the_two_held_pages_is_visible() -> None:
    for stem, card in CARDS.items():
        assert not [s for s in card["sources"].values() if url_of(s) in HELD_URLS], stem
        shown = json.dumps(visible(card), ensure_ascii=False)
        assert "how-to-fix-cannabis-heat-stress" not in shown and "light-burn-and-light-stress" not in shown, stem
        for item in card["_left_out"]:
            if isinstance(item, dict) and item.get("url") in HELD_URLS:
                assert item["text"] not in shown and item["text_en"] not in shown, (stem, item["text"])
    # the ids the two held pages had stay out of the cards they would belong to
    assert "P2" not in CARDS["heat_stress"]["sources"] and "P2" not in CARDS["light_burn"]["sources"]


def test_the_lamp_cause_of_heat_stress_stays_held() -> None:
    """Review 2 of 23.10.2026, F1: 'Lampe zu stark oder zu nah' as a cause of heat rested on the climate guide (P1) and
    the held heat stress page (P2), never on the lamp distance guide, and nothing read shows that S7 ties a close lamp
    to heat (the check notes of 23.10. found 'Too Much Light' on P2, and on S7 for LEDs 'LEDs don't throw heat like HPS
    or CMH bulbs'). So it stays held until a hand read; S7 (P11) stands only behind the two lines it carries."""
    heat = CARDS["heat_stress"]
    shown = json.dumps(visible(heat), ensure_ascii=False)
    assert "zu stark oder zu nah" not in shown and "too strong or too close" not in shown
    held = [e for e in heat["_left_out"] if isinstance(e, dict) and "Lampe zu stark oder zu nah" in e["text"]]
    assert len(held) == 1 and held[0]["section"] == "causes" and held[0]["url"] in HELD_URLS, held
    assert "Hitze-Bezug in S7 nicht nachgelesen" in held[0]["held"] and held[0]["now"] in heat["causes"]
    assert [i for i, s in heat["sources"].items() if url_of(s) == DISTANCE_URL] == ["P11"]
    lines = [(f, i) for f in LISTS for i, s in enumerate(heat[f]) if "P11" in ID_RE.findall(s)]
    assert lines == [("prevention", 1), ("look_alikes", 0)], lines
    assert not [c for c in heat["check"] if "P11" in ID_RE.findall(c["text"])]


def test_cards_that_hold_statements_name_the_gap_on_the_page() -> None:
    """Review 2 of 23.10.2026, F7: the page never shows _left_out, so every card that holds statements for one of the
    two pages says so in a status note, in both languages, and the wiki shows it as written. Before, only light burn
    did; heat stress held seven statements and wind burn one look-alike without a word on the page."""
    holding = {stem for stem, card in CARDS.items()
               if [e for e in card["_left_out"] if isinstance(e, dict) and e.get("url") in HELD_URLS]}
    assert holding == {"heat_stress", "light_burn", "wind_burn"}
    for stem in sorted(holding):
        card = CARDS[stem]
        assert card["status_note"].startswith("Unvollständig: Zwei Herstellerseiten"), stem
        assert card["status_note_en"].startswith("Incomplete: two maker pages"), stem
        assert _numbers(card["status_note"]) == _numbers(card["status_note_en"]), stem
        for lang in ("de", "en"):
            shown = wiki.card_view(wiki.find("problems", stem), lang, ["cannabis"])["card"]
            assert shown["status_note"] == card["status_note_en" if lang == "en" else "status_note"], (stem, lang)


# ---------- the check list ----------

def test_check_items_carry_a_known_metric() -> None:
    known = known_metrics()
    assert {"probe_temp", "probe_humidity", "probe_vpd", "light_percent", "watering"} <= known
    for stem, card in CARDS.items():
        for item in card["check"]:
            assert item["metric"] in known, (stem, item["metric"])


# ---------- the wiki ----------

def keys_in(view: dict[str, Any], category: str) -> set[str]:
    return {c["key"] for cat in view["categories"] if cat["id"] == category for c in cat["cards"]
            if c["folder"] == "problems"}


def test_the_wiki_lists_them_under_cannabis_and_the_switch_hides_them() -> None:
    assert keys_in(wiki.index_view("de", ["cannabis"]), "cannabis") == set(CARDS)
    off = wiki.index_view("de", [])
    assert not any(c["folder"] == "problems" for cat in off["categories"] for c in cat["cards"])
    assert off["hidden"] >= len(CARDS)
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": []}))
    status, body = tw.get("/api/wiki/card/problems/spider_mite")
    assert status == 404 and body["hidden"] is True
    for q, lang in (("Spinnmilbe", "de"), ("spider mite", "en"), ("Hitzestress", "de")):
        _, hits = tw.get(f"/api/wiki/search?q={q}", lang=lang)
        assert not [h for h in hits["results"] if h["folder"] == "problems"], q
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": ["cannabis"]}))
    status, card = tw.get("/api/wiki/card/problems/spider_mite")
    assert status == 200 and card["category"] == "cannabis" and card["kind"] == "problem"


@pytest.mark.parametrize("lang", ["de", "en"])
def test_a_card_reads_whole_in_one_language_without_hidden_parts(lang: str) -> None:
    for stem, raw in CARDS.items():
        view = wiki.card_view(wiki.find("problems", stem), lang, ["cannabis"])
        card = view["card"]
        for field in LISTS:  # the page's consumption filter drops no sentence of a problem card
            assert card[field] == raw[f"{field}_en" if lang == "en" else field], (stem, field)
        assert [c["metric"] for c in card["check"]] == [c["metric"] for c in raw["check"]], stem
        assert all(c["text"] == r["text_en" if lang == "en" else "text"] for c, r in zip(card["check"], raw["check"]))
        assert "_left_out" not in card and "aliases" not in card and "sources" not in card, stem
        assert not [k for k in card if k.endswith("_en") or k.startswith("_")], stem
        # the card's own sources, then the photo's credit (B1) when the card shows a photo
        photo = ["B1"] if "file" in raw["image"] else []
        assert [s["id"] for s in view["sources"]] == list(raw["sources"]) + photo, stem
        assert view["label"] == raw["label_en" if lang == "en" else "label"], stem


@pytest.mark.parametrize(("q", "lang", "first"), [
    ("Spinnmilbe", "de", "spider_mite"), ("Spinnmilben", "de", "spider_mite"), ("spider mite", "en", "spider_mite"),
    ("Hitzestress", "de", "heat_stress"), ("heat stress", "en", "heat_stress"), ("Trauermücken", "de", "fungus_gnat"),
])
def test_search_finds_problem_cards_by_name(q: str, lang: str, first: str) -> None:
    hits = wiki.search(q, lang, ["cannabis"])["results"]
    assert hits and hits[0]["key"] == first, (q, [h["key"] for h in hits])


@pytest.mark.parametrize(("q", "lang"), [("gelbe Blätter", "de"), ("Vergilbung", "de"), ("yellow leaves", "en"),
                                         ("yellowing", "en")])
def test_search_for_yellow_leaves_puts_the_deficiencies_first(q: str, lang: str) -> None:
    """Review 23.10.2026, SHOULD 5: the three yellowing deficiencies carry the search names, so they take ranks 1 to 3
    (before only "somewhere in the hits", with a snippet from a look-alike line)."""
    keys = [h["key"] for h in wiki.search(q, lang, ["cannabis"])["results"]]
    assert set(keys[:3]) == {"nitrogen_deficiency", "magnesium_deficiency", "sulfur_deficiency"}, (q, keys)


def test_review_fixes_of_23_10_stay_out_of_the_visible_text() -> None:
    """Review 23.10.2026: MUST 2 (no 'Lampe zu stark oder zu nah' on P1 alone; review 2 of 23.10.2026, F1: not on the
    lamp distance guide either, it stays held, see test_the_lamp_cause_of_heat_stress_stays_held), MUST 3 (no CSU page
    that could not be read), SHOULD 4 (light burn named its gap; S7 closes it, the manual sentence stays), SHOULD 6 (no
    'Spinosad wirkt meist besser')."""
    heat = json.dumps(visible(CARDS["heat_stress"]), ensure_ascii=False)
    assert "zu stark oder zu nah" not in heat and "too strong or too close" not in heat
    mite = CARDS["spider_mite"]
    assert "P6" not in mite["sources"] and "CSU" not in json.dumps(visible(mite), ensure_ascii=False)
    assert not any("colostate" in s for s in mite["sources"].values())
    thrips = json.dumps(visible(CARDS["thrips"]), ensure_ascii=False)
    assert "meist besser" not in thrips and "more effective" not in thrips
    assert "nicht an blühenden Pflanzen" in thrips and "not be applied to plants that are flowering" in thrips
    burn = CARDS["light_burn"]
    assert burn["causes"][0].startswith("Lampe zu stark oder zu nah.") and burn["causes_en"][0].startswith("Lamp too")
    assert "weder eine PPFD" in burn["causes"][0] and "neither a PPFD" in burn["causes_en"][0]
    assert "Lücke" not in json.dumps(visible(burn), ensure_ascii=False)
    assert burn["status_note"].strip() and burn["status_note_en"].strip()
    for lang in ("de", "en"):
        card = wiki.card_view(wiki.find("problems", "light_burn"), lang, ["cannabis"])["card"]
        assert card["status_note"] == burn["status_note_en" if lang == "en" else "status_note"], lang


@pytest.mark.skipif(tw.NODE is None, reason="node is not installed")
def test_problem_cards_render_every_section_with_the_live_value() -> None:
    for lang in ("de", "en"):
        cards = {f"problems/{stem}": wiki.card_view(wiki.find("problems", stem), lang, ["cannabis"])
                 for stem in ("spider_mite", "heat_stress", "light_burn")}
        out, err = tw.run_page(cards)
        assert not err, err
        for cid, page in out.items():
            for sec in ("symptoms", "causes", "check", "doNow", "prevention", "lookAlikes"):
                assert f"wiki.sec.{sec}" in page["text"], (cid, sec)
            assert "wiki.live" in page["text"] and "25,7 °C" in page["text"].replace("25.7", "25,7"), cid
            assert "wiki.sources" in page["text"] and "OL" in page["tags"], cid


# ---------- the practice source: Pott Planter (23.10.2026) ----------

VIDEO_URL = "https://www.youtube.com/watch?v=G2g8oC86m44"
VIDEO_SOURCE = {
    "title": "Pott Planter, YouTube: Schluss mit mickrigen Pflanzen, 5 Fehler beim Eigenanbau",
    "title_en": "Pott Planter, YouTube: 5 beginner mistakes in home growing",
    "url": VIDEO_URL,
    "retrieved": "2026-10-23",
    "note": "Erfahrungsquelle; Transkript von Hand kopiert",
    "note_en": "experience source; transcript copied by hand",
}
PRACTICE_DE = "Aus der Praxis (Pott Planter): "
PRACTICE_EN = "From practice (Pott Planter): "
WITH_VIDEO = {"aphid", "overwatering", "underwatering", "nitrogen_deficiency", "nutrient_burn", "high_humidity_flower",
              "botrytis_bud_rot", "cold_stress"}
# A video time mark like 1:24 or 12:06 (the owner, 23.10.2026: "Wir brauchen die Minutenangaben nicht ... Hank wird
# nur durch die Minuten verwirrt"). Not a ratio inside a longer run of numbers, not a clock time with seconds.
MINUTE_RE = re.compile(r"(?<![\d.:,/])\d{1,2}:\d{2}(?![\d:])")


def video_id(card: dict[str, Any]) -> str | None:
    ids = [sid for sid, s in card["sources"].items() if url_of(s) == VIDEO_URL]
    assert len(ids) <= 1, ids
    return ids[0] if ids else None


def lines_of(card: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every visible list line of a card with its twin: (where, de, en)."""
    out = []
    for field in LISTS:
        out += [(f"{field}[{i}]", de, en) for i, (de, en) in enumerate(zip(card[field], card[f"{field}_en"]))]
    out += [(f"check[{i}]", c["text"], c["text_en"]) for i, c in enumerate(card["check"])]
    return out


def practice_items() -> list[tuple[str, str, str, str]]:
    return [(stem, where, de, en) for stem, card in CARDS.items() for where, de, en in lines_of(card)
            if de.startswith(PRACTICE_DE)]


def test_the_video_is_one_new_source_per_card_with_the_next_free_number() -> None:
    assert {stem for stem, card in CARDS.items() if video_id(card)} == WITH_VIDEO
    for stem in WITH_VIDEO:
        card = CARDS[stem]
        sid = video_id(card)
        assert card["sources"][sid] == VIDEO_SOURCE, stem
        numbers = sorted(int(k[1:]) for k in card["sources"])
        assert sid == f"P{numbers[-1]}" and numbers == list(range(1, numbers[-1] + 1)), (stem, sid)
        assert list(card["sources"])[-1] == sid, stem


def test_no_minute_marks_in_any_kartei_file_that_cites_the_video() -> None:
    citing = [p for p in (ROOT / "kartei").rglob("*.json") if VIDEO_URL in p.read_text(encoding="utf-8")]
    assert {p.stem for p in citing if p.parent == FOLDER} == WITH_VIDEO
    for path in citing:
        text = path.read_text(encoding="utf-8")  # the whole file, _left_out included
        assert not MINUTE_RE.findall(text), (path.name, MINUTE_RE.findall(text))
        assert '"time"' not in text, path.name
    assert MINUTE_RE.search("Pott Planter 1:57") and MINUTE_RE.search("bei 12:06")
    assert not MINUTE_RE.search("NPK 3:1:2") and not MINUTE_RE.search("um 12:06:30")


def test_every_practice_item_cites_the_video_and_only_practice_items_do() -> None:
    items = practice_items()
    assert len(items) == 19
    for stem, where, de, en in items:
        sid = video_id(CARDS[stem])
        assert en.startswith(PRACTICE_EN), (stem, where, en)
        assert sid in ID_RE.findall(de) and sid in ID_RE.findall(en), (stem, where)
    for stem in WITH_VIDEO:
        card, sid = CARDS[stem], video_id(CARDS[stem])
        for where, de, en in lines_of(card):
            if sid in ID_RE.findall(de) or sid in ID_RE.findall(en):
                assert de.startswith(PRACTICE_DE) and en.startswith(PRACTICE_EN), (stem, where, de)
        assert "Pott Planter" not in json.dumps(visible(card), ensure_ascii=False).replace(
            PRACTICE_DE.strip(), "").replace(PRACTICE_EN.strip(), ""), stem  # named only in the item opener
        assert [e for e in card["_left_out"] if isinstance(e, str) and "Zeitmarken" in e], stem


# The tips the check found contradicted, and the outdoor ones: nowhere on a card, or not in a practice item.
NOWHERE = ("Neem", "neem", "Schutzschild", "shield", "fast ausgeschlossen", "almost impossible")
NOT_IN_PRACTICE = re.compile(r"weniger Stickstoff|less nitrogen|feuchte[nr]? Luft|humid air|Luftfeuchte|humidity|"
                             r"\bSonne\w*|\bsun\w*|Hauswand|\bwall\b|BioBizz|Bio-Grow|Hanferde|Perlit|perlite|"
                             r"Kokosfaser|coco fiber", re.I)


def test_contradicted_and_outdoor_tips_of_the_video_are_not_visible() -> None:
    for stem, card in CARDS.items():
        shown = json.dumps(visible(card), ensure_ascii=False)
        assert not [w for w in NOWHERE if w in shown], (stem, [w for w in NOWHERE if w in shown])
    for stem, where, de, en in practice_items():
        assert not NOT_IN_PRACTICE.search(de) and not NOT_IN_PRACTICE.search(en), (stem, where, de)
        assert not wiki.CONSUMPTION_RE.search(de) and not wiki.CONSUMPTION_RE.search(en), (stem, where)


def test_practice_items_say_no_more_than_the_video() -> None:
    """Review of the practice items, 23.10.2026: F1 lightly pre-fertilized soil 'passt' for germination and young plants
    (the video: 'perfekt geeignet'), never 'reicht', since how long it feeds a young plant no source settles (nutrient
    burn card); F3 'je nach Topfgröße' as he says it, without a direction; F2 aphids 'entfernen', not 'einzelne Läuse
    abstreifen'; F4 next to 'ins Gießwasser' the open question of fertilizer in the box's tank; F5 the conclusion from
    an unchecked claim marked as an inference; F6 his example plant stood outdoors."""
    items = {(stem, where): (de, en) for stem, where, de, en in practice_items()}

    def item(stem: str, word: str) -> tuple[str, str]:
        hit = [pair for (s, _), pair in items.items() if s == stem and word in pair[0]]
        assert len(hit) == 1, (stem, word, hit)
        return hit[0]

    de, en = item("nitrogen_deficiency", "vorgedüngt")
    assert "passt für Keimung und Jungpflanze" in de and "suits germination and young plants" in en
    assert "je nach Topfgröße" in de and "depending on pot size" in en
    for word in ("reicht", "suffices", "kleinen Topf", "small pot", "früher", "sooner"):
        assert word not in de and word not in en, word
    de, en = item("nitrogen_deficiency", "Gießwasser")
    assert de.endswith("Ob Dünger in den Tank der Box darf, sagt keine gelesene Quelle.")
    assert en.endswith("Whether fertilizer may go into the box's tank, no source read says.")
    de, en = item("aphid", "Fingern")
    assert "Läuse mit den Fingern entfernen oder wegpusten P10" in de
    assert "remove aphids with your fingers or blow them off P10" in en
    assert not [w for w in ("einzelne", "abstreifen", "single", "wipe") if w in de or w in en]
    de, en = item("underwatering", "Mikroorganismen")
    assert "Folgerung, nicht aus einer Quelle: die Erde nicht ganz austrocknen lassen" in de
    assert "Inference, not from a source: do not let the soil dry out completely" in en
    de, en = item("cold_stress", "Beispielpflanze")
    assert "Beispielpflanze draußen" in de and "example plant outdoors" in en


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_wiki_shows_the_practice_items_and_the_video_title(lang: str) -> None:
    for stem in sorted(WITH_VIDEO):
        view = wiki.card_view(wiki.find("problems", stem), lang, ["cannabis"])
        prefix = PRACTICE_EN if lang == "en" else PRACTICE_DE
        shown = [s for f in LISTS for s in view["card"][f] if s.startswith(prefix)]
        assert shown, stem
        src = [s for s in view["sources"] if s["url"] == VIDEO_URL]
        assert len(src) == 1 and src[0]["title"] == VIDEO_SOURCE["title_en" if lang == "en" else "title"], stem
