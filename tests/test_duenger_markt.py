"""The fertilizer market (owner, 23.10.2026): every researched line as a card in kartei/nutrients/, the Dünger area of
the wiki with its filters, every card with a readable chart selectable as the fertilizer (the same switch, the same
rules), and Hank: "ich nutze <Dünger>" names the line, offers the switch as a two-tap card and draws the plan."""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import feeding, wiki
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.hank import feedplan, livestate, proposals
from vgrow.hank.types import Route
from vgrow.plant import PlantLog, load_profile
from vgrow.server import COOKIE, make_app

REPO = Path(__file__).resolve().parent.parent
NUTRIENTS = REPO / "kartei" / "nutrients"
BLOOM_START = date(2026, 10, 22)
LIBRARY_SETUPS = ["biocanna-indoor", "canna-terra", "hesi-soil", "plagron-natural", "plagron-terra"]
# no chart at all: a plan in words, label rates only, or no plan (fertilizer market 2, 23.10.2026)
NO_CHART = ["athena-blended", "athena-pro", "biotabs", "green-house-feeding-bio", "vorgeduengte-erde"]
# a chart, but never selectable: not for soil (coco, hydro), or stages instead of weeks
CHART_ONLY = ["advanced-nutrients-ph-perfect", "atami-bcuzz-soil", "canna-aqua", "canna-coco",
              "green-house-feeding-mineral", "terra-aquatica-tripart"]
NOT_FOR_SOIL = ["advanced-nutrients-ph-perfect", "athena-blended", "athena-pro", "canna-aqua", "canna-coco"]
READ_ONLY = sorted(NO_CHART + CHART_ONLY)
LINES = sorted(["biobizz-2026", "biocanna-indoor", "canna-terra", "hesi-soil", "plagron-natural", "plagron-terra",
                "vivosun-base-ab", *READ_ONLY])
NEW_LINES = ["advanced-nutrients-ph-perfect", "atami-bcuzz-soil", "athena-blended", "athena-pro", "canna-aqua",
             "canna-coco", "green-house-feeding-bio", "green-house-feeding-mineral", "hesi-soil",
             "terra-aquatica-tripart", "vorgeduengte-erde"]
EM_DASH = chr(0x2014)
feedplan.register()  # the catalog, as core.py registers it when Hank loads

# The research of 23.10.2026, as the makers print it (ml per 10 litres), column by column: propagation, veg, flowering
# weeks 1.. (None is "-"). The cards hold the same numbers divided by 10.
PRINTED = {
    ("plagron-terra", "lightmix", "terra_grow"): [25, 50, None, None, None, None, None, None, None, None],
    ("plagron-terra", "lightmix", "terra_bloom"): [None, None, 50, 50, 50, 40, 45, 50, 50, 50],
    ("plagron-terra", "growmix", "terra_bloom"): [None, None, None, 50, 50, 40, 45, 50, 50, 50],
    ("plagron-natural", "bio_lightmix", "alga_grow"): [20, 40, None, None, None, None, None, None, None, None],
    ("plagron-natural", "bio_lightmix", "alga_bloom"): [None, None, 40, 40, 40, 30, 35, 40, 40, 40],
    ("plagron-natural", "allmix", "alga_bloom"): [None, None, None, None, 40, 30, 35, 40, 40, 40],
    ("canna-terra", "low", "terra_vega"): [32, 45, 54, 54, None, None, None, None, None, None, None],
    ("canna-terra", "high", "terra_vega"): [49, 62, 71, 71, None, None, None, None, None, None, None],
    ("canna-terra", "low", "terra_flores"): [None, None, None, None, 57, 57, 57, 57, 57, 34, None],
    ("canna-terra", "high", "terra_flores"): [None, None, None, None, 76, 76, 76, 76, 76, 53, None],
    ("canna-terra", "low", "pk_13_14"): [None, None, None, None, None, None, None, 15, None, None, None],
    ("biocanna-indoor", "low", "bio_vega"): [15, 20, 25, 25, None, None, None, None, None, None, None],
    ("biocanna-indoor", "high", "bio_flores"): [None, None, None, None, 40, 40, 40, 40, 30, None, None],
    # fertilizer market 2 (23.10.2026), each read again from the rendered original
    ("biocanna-indoor", "low", "bio_rhizotonic"): [40, 20, 5, 5, 5, 5, 5, 5, 5, None, None],
    ("biocanna-indoor", "high", "bio_boost"): [None, None, None, 40, 40, 40, 40, 40, 40, 40, 40],
    ("canna-terra", "low", "rhizotonic"): [40, 20, 20, 20, 5, 5, 5, 5, 5, None, None],
    ("canna-terra", "high", "cannazym"): [None, 25, 25, 25, 25, 25, 25, 25, 25, 50, 50],
    ("canna-terra", "low", "canna_flush"): [None, None, None, None, None, None, None, None, 40, None, 40],
    ("canna-coco", "low", "coco_a"): [26, 26, 29, 29, 29, 29, 29, 29, 26, 26, None],
    ("canna-coco", "high", "coco_b"): [33, 33, 37, 37, 37, 37, 37, 37, 33, 33, None],
    ("canna-coco", "low", "pk_13_14"): [None, None, None, None, None, None, None, 15, None, None, None],
    ("canna-aqua", "low", "aqua_vega_a"): [21, 25, 28, 28, None, None, None, None, None, None, None],
    ("canna-aqua", "high", "aqua_flores_b"): [None, None, None, None, 38, 38, 38, 38, 34, 34, None],
    ("hesi-soil", "soil", "tnt_complex"): [25, 50, None, None, None, None, None, None, None],
    ("hesi-soil", "soil", "bloom_complex"): [None, None, 50, 50, 50, 50, 50, 50, 50],
    ("hesi-soil", "soil", "phosphorus_plus"): [None, None, None, None, None, 25, 25, 25, 25],
    # the review of 24.10.2026: a second run read these from the freshly downloaded originals, not from the cards
    ("hesi-soil", "soil", "root_complex"): [50, 50, 50, None, None, None, None, None, None],
    ("hesi-soil", "soil", "powerzyme"): [20, 20, 20, 20, 20, 20, 20, 20, 20],
    ("hesi-soil", "soil", "boost"): [None, None, 20, None, None, 20, 20, 20, 20],
    ("canna-coco", "high", "cannaboost"): [None, None, 20, 20, 40, 40, 40, 40, 40, 40, 40],
    ("canna-aqua", "low", "aqua_flores_a"): [None, None, None, None, 30, 30, 30, 30, 26, 26, None],
    ("biocanna-indoor", "high", "bio_vega"): [20, 25, 30, 30, None, None, None, None, None, None, None],
}
# The same second read for the charts printed per litre (ml/l as printed; a range's low end on "low", its high end
# on "high"): B'cuzz Soil, pH Perfect and TriPart (24.10.2026, from the freshly downloaded originals).
SECOND_READ = {
    ("atami-bcuzz-soil", "low", "soil_a"): [2, 2.8, 3.5, 4, 4, 3.8, 3.8, 3.5, 3.5, None],
    ("atami-bcuzz-soil", "high", "soil_b"): [3, 3.8, 4.5, 5, 5, 4.8, 4.8, 4.5, 4.5, None],
    ("atami-bcuzz-soil", "low", "ata_calmag"): [0.2, 0.2, 0.2, 0.2, 0.1, 0.1, 0.1, 0.1, 0.1, None],
    ("atami-bcuzz-soil", "high", "root_stimulator"): [1, 1, None, None, None, None, None, None, None, None],
    ("advanced-nutrients-ph-perfect", "base", "ph_perfect_grow"): [1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 4],
    ("advanced-nutrients-ph-perfect", "base", "ph_perfect_bloom"): [1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 4],
    ("terra-aquatica-tripart", "soil", "tripart_grow"): [0.2, 0.6, 1.5, 1.5, 0.7, None],
    ("terra-aquatica-tripart", "soil", "tripart_bloom"): [0.2, 0.6, 0.5, 1, 2.1, None],
    ("terra-aquatica-tripart", "hydro_coco", "tripart_micro"): [0.5, 1, 1.2, 2, 1.6, None],
    ("terra-aquatica-tripart", "hydro_coco", "final_part"): [None, None, None, None, None, 5],
}


def card(name: str) -> dict[str, Any]:
    return json.loads((NUTRIENTS / f"{name}.json").read_text(encoding="utf-8"))


def setups() -> dict[str, dict[str, Any]]:
    return feeding.setups_of(load_profile())


def manual(key: str, since: date = BLOOM_START) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stage = next(s for s in load_profile()["stages"] if s["key"] == key)  # type: ignore[index]
    entry = {"key": key, "since": since.isoformat()}
    return {**stage, "since": entry["since"], "set_by": "manual"}, [entry]


@pytest.fixture
def on_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feeding, "local_today", lambda: BLOOM_START)


# ---------- the cards ----------

def test_every_researched_line_has_a_card_with_its_type_and_application() -> None:
    names = sorted(p.stem for p in NUTRIENTS.glob("*.json") if p.stem != "duengerarten")
    assert names == LINES
    for name in LINES:
        data = card(name)
        line = data["line"]
        assert line["types"] and set(line["types"]) <= set(wiki.TYPE_KEYS), name
        assert set(line["media"]) <= set(wiki.MEDIA), name
        assert line["application"] and line["application_en"] and line["application_short_en"], name
        assert line["products"] and all(p["role"] and p["role_en"] and p["sources"] for p in line["products"]), name
        cited = {s for p in line["products"] for s in p["sources"]} | set(line.get("application_sources", []))
        assert cited <= set(data["sources"]), name  # every cited source is on the card
        assert all(src.get("retrieved") for src in data["sources"].values()), name
        text = json.dumps(data, ensure_ascii=False)
        assert EM_DASH not in text and "vivosun.com" not in json.dumps(line), name  # never a vivosun.com dose
        assert "amazon." not in text, name


def test_the_printed_per_10_litre_numbers_are_the_card_numbers_divided_by_ten() -> None:
    for (name, page, product), printed in PRINTED.items():
        data = card(name)
        assert data["unit"] == "ml/l" and data["printed_unit"] == "ml/10 l"
        rows = data["pages"][page]["rows"][product]
        assert len(rows) == len(data["weeks"]["columns"]) == len(printed), (name, page, product)
        for value, raw in zip(rows, printed, strict=True):
            assert (value is None) == (raw is None), (name, page, product)
            if raw is not None:
                assert Fraction(str(value)) == Fraction(raw, 10), (name, page, product, value, raw)


def test_the_per_litre_charts_match_a_second_read_of_the_original() -> None:
    for (name, page, product), printed in SECOND_READ.items():
        data = card(name)
        assert data["unit"] == "ml/l" and "printed_unit" not in data, name
        rows = data["pages"][page]["rows"][product]
        assert len(rows) == len(data["weeks"]["columns"]) == len(printed), (name, page, product)
        for value, raw in zip(rows, printed, strict=True):
            assert (value is None) == (raw is None), (name, page, product)
            if raw is not None:
                assert Fraction(str(value)) == Fraction(str(raw)), (name, page, product, value, raw)


def test_ranges_keep_low_and_high_apart_and_the_low_end_doses() -> None:
    for name in ("canna-terra", "biocanna-indoor", "canna-coco", "canna-aqua", "atami-bcuzz-soil"):
        data = card(name)
        assert set(data["pages"]) == {"low", "high"}
        assert data.get("selectable", {"page": "low"})["page"] == "low"
        low, high = data["pages"]["low"]["rows"], data["pages"]["high"]["rows"]
        for comp, values in low.items():
            for lo, hi in zip(values, high[comp], strict=True):
                assert (lo is None) == (hi is None) and (lo is None or lo <= hi), (name, comp)


def test_lines_without_a_readable_chart_are_cards_but_never_selectable() -> None:
    for name in NO_CHART:
        data = card(name)
        assert "pages" not in data and "selectable" not in data and "weeks" not in data, name
        assert feeding._selectable(name, data) is None
    for name in CHART_ONLY:
        data = card(name)
        assert data["pages"] and data["weeks"]["columns"] and "selectable" not in data, name
        assert feeding._selectable(name, data) is None
    assert "plan" in card("biotabs")["line"]  # the maker's sequence in words, no per-litre chart
    assert "plan" in card("green-house-feeding-bio")["line"] and "plan" in card("green-house-feeding-mineral")["line"]


def test_the_biobizz_card_keeps_its_chart_and_gains_the_line_and_the_boosters() -> None:
    data = card("biobizz-2026")
    assert set(data["components"]) == {"bio_grow", "bio_bloom", "top_max", "alg_a_mic"}  # the chart rows unchanged
    boosters = {(r["page"], r["product"]): r for r in data["boosters"]["rows"]}
    assert boosters[("all_mix", "Root-Juice")]["values"] == [4, 4, 2, 2, None, None, None, None, None, None]
    assert boosters[("light_mix", "Bio-Heaven")]["values"] == [2, 2, 2, 3, 4, 5, 5, 3, 3, 2]
    assert boosters[("all_mix", "Microbes")]["unit"] == "g/l"  # g per litre: never a row of the ml chart
    assert data["frequency"]["text"].startswith("Die Tabelle sagt nicht, wie oft gedüngt wird")
    assert "Microbes" in data["frequency"]["text"]  # the chart does give one for Microbes (research, point 3)


# ---------- every card with a chart is selectable, with every rule unchanged ----------

def test_library_cards_join_the_switch_after_plant_jsons_own() -> None:
    found = setups()
    assert list(found) == ["biobizz", "vivosun", *LIBRARY_SETUPS]
    for sid in LIBRARY_SETUPS:
        own = found[sid]
        assert own["card"] == sid and own["library_card"] is True and own["dose_factor"] == 0.5
        assert own["components"] == card(sid)["selectable"]["components"]
    assert {own["card"] for own in found.values()}.isdisjoint(READ_ONLY)
    profile = load_profile()
    without = {**profile, "feeding": {k: v for k, v in profile["feeding"].items() if k != "from_library"}}
    assert list(feeding.setups_of(without)) == ["biobizz", "vivosun"]  # opt-in: plant.json's from_library
    flat = {**profile, "feeding": found["biobizz"]}
    assert list(feeding.setups_of(flat)) == ["default"]  # the flat layout stays one setup


@pytest.mark.parametrize("sid", LIBRARY_SETUPS)
def test_a_library_card_is_a_draft_until_confirmed_then_rounds_down_never_above(on_day: None, sid: str) -> None:
    own = {**setups()[sid], "bloom_start": BLOOM_START.isoformat()}
    shown = feeding.view(own, "de", amount_units=feeding.PAGE_UNITS)
    assert (shown["state"], shown["doses"], shown["plan"]) == ("draft", None, None)
    with pytest.raises(feeding.FeedingRefused):
        feeding.log_feeding(own, 1)
    with pytest.raises(feeding.FeedingRefused):
        feeding.confirm(own, sid, "0" * 64)  # a fingerprint the page never showed
    feeding.confirm(own, sid, shown["fingerprint"])
    data = card(sid)
    factor = Fraction(1, 2)
    checked = 0
    for day in range(0, 7 * 12, 3):
        today = date.fromordinal(BLOOM_START.toordinal() + day)
        got = feeding.view(own, "de", today=today, amount_units=feeding.PAGE_UNITS, liters="1,5")
        if got["state"] != "ok":
            assert got["doses"] in (None, [])
            continue
        column = [c["key"] for c in data["weeks"]["columns"]].index(got["week"]["key"])
        rows = data["pages"][data["selectable"]["page"]]["rows"]
        for dose in got["doses"]:
            chart = Fraction(str(rows[dose["component"]][column]))
            assert Fraction(str(dose["per_liter"])) <= chart * factor
            assert Fraction(str(dose["per_liter"])) == math.floor(chart * factor * 10) / Fraction(10)
            checked += 1
        for item in got["plan"]["items"]:
            chart = Fraction(str(rows[item["component"]][column]))
            assert Fraction(str(item["total"])) <= chart * factor * Fraction(3, 2)
    assert checked > 0
    with pytest.raises(Exception, match="Liter|liter"):
        feeding.view(own, "de", amount_units=feeding.PAGE_UNITS, liters="15")  # confirm_above_liters asks twice


def test_confirmations_and_amounts_never_carry_over_between_cards(on_day: None) -> None:
    terra = {**setups()["canna-terra"], "bloom_start": BLOOM_START.isoformat()}
    plagron = {**setups()["plagron-terra"], "bloom_start": BLOOM_START.isoformat()}
    feeding.confirm(terra, "canna-terra", feeding.fingerprint(terra, feeding.load_card("canna-terra")))
    assert feeding.view(terra, "de", amount_units=feeding.PAGE_UNITS)["state"] == "ok"
    assert feeding.view(plagron, "de", amount_units=feeding.PAGE_UNITS)["state"] == "draft"
    with pytest.raises(feeding.FeedingRefused):
        feeding.confirm(plagron, "canna-terra", feeding.fingerprint(terra, feeding.load_card("canna-terra")))


def test_the_page_switch_takes_a_library_card_with_the_same_action(on_day: None) -> None:
    async def run() -> None:
        log = PlantLog()
        log.set_stage("early_flower", BLOOM_START.isoformat())
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), "k", plant_log=log))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            state = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            assert state["state"] == "needs_choice" and len(state["choice"]["options"]) == 7
            assert [o["library"] for o in state["choice"]["options"]] == [False, False, True, True, True, True, True]
            resp = await client.post("/api/cmd", json={"action": "feeding_setup", "setup": "canna-terra"})
            assert resp.status == 200
            fed = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            assert (fed["state"], fed["card"]["id"], fed["doses"]) == ("draft", "canna-terra", None)
            resp = await client.post("/api/cmd", json={"action": "feeding_setup", "setup": "biotabs"})
            assert resp.status == 400  # a card without a chart is no setup
    asyncio.run(run())


def test_the_choice_lives_only_in_the_state_folder() -> None:
    before = (REPO / "plant.json").read_bytes(), {p.name: p.read_bytes() for p in NUTRIENTS.glob("*.json")}
    PlantLog().set_feeding_choice("plagron-natural", setups())
    feeding.preview("canna-terra", "de")
    after = (REPO / "plant.json").read_bytes(), {p.name: p.read_bytes() for p in NUTRIENTS.glob("*.json")}
    assert before == after


# ---------- the wiki ----------

def test_the_duenger_area_lists_every_line_with_filters_and_the_overview() -> None:
    data = wiki.fertilizers_view("de", ["cannabis"])
    assert data is not None and data["count"] == len(LINES)
    rows = {r["key"]: r for r in data["lines"]}
    assert set(rows) == set(LINES)
    assert {k for k, r in rows.items() if r["selectable"]} == set(LINES) - set(READ_ONLY)
    assert rows["biotabs"]["types"] == ["slow_release"] and rows["canna-coco"]["media"] == ["coco"]
    assert {k for k, r in rows.items() if not r["soil"]} == set(NOT_FOR_SOIL)  # the page says "not for soil"
    assert rows["canna-aqua"]["media"] == ["hydro"] and rows["vivosun-base-ab"]["soil"]  # no medium named: not shut out
    assert data["overview"]["key"] == "duengerarten"
    assert {t["key"] for t in data["types"]} >= {"organic_liquid", "mineral_liquid", "multi_part", "powder",
                                                 "slow_release"}
    assert {m["key"] for m in data["media"]} == {"soil", "coco", "hydro"}
    en = wiki.fertilizers_view("en", [])
    assert en is not None and en["count"] == len(LINES)  # no cannabis package needed
    overview = wiki.card_view(wiki.find("nutrients", "duengerarten"), "en", [])
    assert [t["key"] for t in overview["card"]["types"]] == list(wiki.TYPE_KEYS)
    assert all(t["mistakes"] for t in overview["card"]["types"]) and overview["card"]["market"]


def test_cannabis_wording_leaves_with_the_package() -> None:
    off = wiki.card_view(wiki.find("nutrients", "canna-terra"), "de", [])
    on = wiki.card_view(wiki.find("nutrients", "canna-terra"), "de", ["cannabis"])
    assert len(off["card"]["notes"]) == len(on["card"]["notes"]) - 1
    assert "Autoflower" not in json.dumps(off["card"]["notes"], ensure_ascii=False)
    assert "N5" not in {s["id"] for s in off["sources"]}


# ---------- Hank: "ich nutze <Dünger>" ----------

@pytest.mark.parametrize(("question", "kind", "cards"), [
    ("Ich nutze Canna Terra", "card", ("canna-terra",)),
    ("ich habe BioBizz", "card", ("biobizz-2026",)),
    ("I'm using Plagron Terra", "card", ("plagron-terra",)),
    ("I use Alga Bloom", "card", ("plagron-natural",)),
    ("Ich hab Vivosun", "card", ("vivosun-base-ab",)),
    ("Ich nutze Plagron", "cards", ("plagron-natural", "plagron-terra")),
    ("Ich verwende BioTabs", "card", ("biotabs",)),
    ("Ich nutze den Dünger von Hesi", "card", ("hesi-soil",)),
    ("I use CANNA COCO", "card", ("canna-coco",)),
    ("Ich nutze Athena", "cards", ("athena-blended", "athena-pro")),
    ("Ich habe B'cuzz Soil", "card", ("atami-bcuzz-soil",)),
    ("Ich nutze den Dünger von Mills", "unknown", ()),
    ("My fertilizer is Floraflex", "unknown", ()),
    ("Ich nutze Superdünger 3000", "unknown", ()),
    ("Ich nutze Superduenger 3000", "unknown", ()),
    ("I use MegaFertilizer", "unknown", ()),
])
def test_a_named_fertilizer_is_heard(question: str, kind: str, cards: tuple[str, ...]) -> None:
    claim = feedplan.fertilizer_claim(question)
    assert claim is not None and (claim.kind, claim.cards) == (kind, cards), (question, claim)


@pytest.mark.parametrize("question", ["Ich habe zu viel Dünger gegeben", "Ich habe gelbe Blätter",
                                      "Ich habe zu viel Superdünger gegeben", "Ich nutze die Lampe",
                                      "Ich nutze die Lampe auf 60 %", "I have no fertilizer",
                                      "Ich habe eine Frage zum Dünger", "Wie viel Dünger gebe ich diese Woche?"])
def test_other_sentences_are_no_fertilizer_claim(question: str) -> None:
    assert feedplan.fertilizer_claim(question) is None


def _live(choice: str | None = "biobizz") -> livestate.LiveState:
    log = PlantLog()
    log.set_stage("early_flower", BLOOM_START.isoformat())
    if choice:
        log.set_feeding_choice(choice, setups())
    profile = load_profile()
    view = feeding.view(feeding.setup_of(profile, log), "de", amount_units=feeding.PAGE_UNITS,
                        **feeding.context(profile, log, "de"))
    from datetime import datetime
    return livestate.LiveState(feeding_view=view, now=datetime(2026, 10, 23, 12, 0))


def _ask(question: str, state: livestate.LiveState, lang: str = "de") -> Any:
    route = feedplan.route_for(question, lang)
    assert route is not None and route.term == "feed_have", question
    return feedplan.reading(route, state, answer_id="a-1", now=0.0)


def test_canna_terra_gets_the_line_the_switch_card_and_the_plan(on_day: None) -> None:
    answer = _ask("Ich nutze Canna Terra", _live())
    assert answer.text.startswith("CANNA Terra steht im Wiki: mineralisch und flüssig, für Erde.")
    assert "zwei Tipps" in answer.text and "keine gerechneten Mengen" in answer.text
    [card_] = answer.proposals
    assert (card_.kind, card_.body, card_.two_taps) == ("feeding_setup", {"action": "feeding_setup",
                                                                          "setup": "canna-terra"}, True)
    plan = answer.plan
    assert plan["basis"] == "chart" and plan["title"] == "Plan ab heute: CANNA Terra"
    assert [r["label"] for r in plan["rows"]] == ["Terra Vega", "Terra Flores"]
    assert plan["head"][0]["now"] and plan["head"][0]["label"] == "Blüte 1"
    assert plan["head"][-1]["kind"] == "end"  # the chart's end after W9
    # draft: only whether a product is due, never the label's number (critique 23.10.2026, M1: "5,4" read as ml/l
    # although the factor 0.5 was not applied)
    assert plan["rows"][0]["cells"][0]["text"] == "geben" and plan["rows"][1]["cells"][0]["text"] == ""
    assert answer.link == {"href": "#wiki/nutrients/canna-terra", "label": "Karte im Wiki öffnen"}
    assert answer.sources and answer.sources[0].id == "CT1"


def test_the_plan_shows_amounts_once_the_card_and_the_stage_are_confirmed(on_day: None) -> None:
    own = setups()["plagron-terra"]
    feeding.confirm(own, "plagron-terra", feeding.fingerprint(own, feeding.load_card("plagron-terra")))
    answer = _ask("I use Plagron Terra", _live(), "en")
    plan = answer.plan
    assert plan["basis"] == "amount" and "factor 0.5" in plan["caption"]
    bloom = next(r for r in plan["rows"] if r["label"] == "Terra Bloom")
    assert bloom["cells"][0]["text"] == "2.5"  # 5 ml/l x 0.5, rounded down to 0.1
    assert plan["head"][-1]["kind"] == "harvest"  # week 11: harvest with water only


def test_the_fertilizer_in_use_gets_no_switch_card(on_day: None) -> None:
    answer = _ask("Ich habe BioBizz", _live("biobizz"))
    assert answer.proposals == [] and "ist schon dein Dünger" in answer.text and answer.plan
    marks = [h["kind"] for h in answer.plan["head"]]
    assert marks[-2:] == ["water", "harvest"]  # flowering week 9 plain water, week 10 harvest


def test_an_unknown_fertilizer_gets_no_dose_and_lands_on_the_gap_list() -> None:
    answer = _ask("Ich nutze den Dünger von Mills", _live())
    assert answer.text.startswith("Mills steht noch nicht in meinem Wiki.")
    # the chat takes no photo yet (23.10.2026): the name and the maker as printed on the label
    assert "wer stellt ihn her" in answer.text and "wie es auf dem Etikett steht" in answer.text
    assert "Foto" not in answer.text and "nenne ich keine Menge" in answer.text
    assert answer.gap and answer.plan is None and answer.proposals == []
    assert not any(ch.isdigit() for ch in answer.text)
    en = _ask("My fertilizer is Floraflex", _live(), "en")
    assert en.text.startswith("Floraflex is not in my wiki yet.") and "who makes it" in en.text
    assert "printed on the label" in en.text and "photo" not in en.text and "I give no amount" in en.text


@pytest.mark.parametrize(("question", "lang", "said"), [
    ("Ich nutze Superdünger 3000", "de", "Superdünger 3000 steht noch nicht in meinem Wiki."),
    ("Ich habe Blumendünger", "de", "Blumendünger steht noch nicht in meinem Wiki."),
    ("I use MegaFertilizer", "en", "MegaFertilizer is not in my wiki yet."),
])
def test_a_compound_fertilizer_name_gets_the_unknown_answer_never_no_data(question: str, lang: str, said: str) -> None:
    answer = _ask(question, _live(), lang)  # _ask: the route is feed_have, not the generic no-data one
    assert answer.text.startswith(said) and answer.gap and answer.plan is None and answer.proposals == []
    assert "ml" not in answer.text and "g/l" not in answer.text


def test_a_maker_with_several_lines_asks_which_and_a_read_only_line_draws_nothing(on_day: None) -> None:
    which = _ask("Ich nutze Plagron", _live())
    assert which.text == "Welchen meinst du: Plagron 100% NATURAL oder Plagron 100% TERRA? Sag mir den genauen Namen."
    assert which.plan is None and which.proposals == []
    tabs = _ask("Ich verwende BioTabs", _live())
    assert "Einen Plan je Liter zeichne ich dafür nicht" in tabs.text and tabs.plan is None and not tabs.proposals


def test_the_switch_card_is_checked_against_the_live_options() -> None:
    state = _live("biobizz")
    with pytest.raises(proposals.Refused):
        proposals.check_body({"action": "feeding_setup", "setup": "biobizz"}, state)  # already in use
    with pytest.raises(proposals.Refused):
        proposals.check_body({"action": "feeding_setup", "setup": "biotabs"}, state)  # no setup
    with pytest.raises(proposals.Refused):
        proposals.check_body({"action": "feeding_setup", "setup": "canna-terra", "liters": 2}, state)
    assert proposals.check_body({"action": "feeding_setup", "setup": "vivosun"}, state)["setup"] == "vivosun"


def test_the_feed_plan_question_carries_the_same_graphic(on_day: None) -> None:
    route = Route("reading", None, None, None, "info", "de", "reading:feed_plan", qtype="value", term="feed_plan")
    answer = feedplan.reading(route, _live("biobizz"), answer_id="a-2")
    assert answer.plan is not None and answer.plan["rows"][0]["label"] == "Bio-Grow"
    assert answer.plan["head"][0]["now"]


# ---------- after the review of 23.10.2026 ----------

@pytest.mark.parametrize("lang", ["de", "en"])
def test_an_unconfirmed_plan_shows_no_number_and_names_the_factor(on_day: None, lang: str) -> None:
    for name in ("canna-terra", "plagron-terra", "biocanna-indoor", "plagron-natural"):
        view = feeding.preview(name, lang, today=BLOOM_START)
        assert view["state"] in ("draft", "needs_stage"), name
        plan = feedplan.plan_graphic(view, lang)
        assert plan is not None and plan["basis"] == "chart", name
        due = feedplan.t("hank.plan.g_due", lang)
        cells = {c["text"] for r in plan["rows"] for c in r["cells"]}
        assert cells <= {due, ""} and due in cells, (name, cells)  # never "5,4" before the factor is applied
        assert not any(ch.isdigit() for ch in "".join(cells)), name
        assert ("Faktor 0,5" if lang == "de" else "factor 0.5") in plan["caption"], (name, plan["caption"])


def test_plagron_in_flowering_week_9_is_harvest_not_the_end_of_the_table() -> None:
    own = {**setups()["plagron-terra"], "bloom_start": BLOOM_START.isoformat()}
    feeding.confirm(own, "plagron-terra", feeding.fingerprint(own, feeding.load_card("plagron-terra")))
    week9 = date.fromordinal(BLOOM_START.toordinal() + 7 * 8)
    for name in ("plagron-terra", "plagron-natural"):
        assert feeding.has_tail(card(name)), name
        shown = feeding.view({**setups()[name], "bloom_start": BLOOM_START.isoformat()}, "de", today=week9,
                             amount_units=feeding.PAGE_UNITS)
        assert shown["week"]["key"] == "harvest" and shown["week"]["bloom_week"] == 9, name
        assert feedplan.last_chart_week(shown) == 8, name
    confirmed = feeding.view(own, "de", today=week9, amount_units=feeding.PAGE_UNITS)
    assert (confirmed["state"], confirmed["doses"]) == ("harvest", [])
    for name in ("vivosun-base-ab", "canna-terra", "biocanna-indoor"):
        assert not feeding.has_tail(card(name)), name  # their tables end: label_ends stays
    assert feeding.has_tail(card("biobizz-2026"))


def test_canna_in_flowering_week_10_says_the_table_ends_after_week_9() -> None:
    own = {**setups()["canna-terra"], "bloom_start": BLOOM_START.isoformat()}
    feeding.confirm(own, "canna-terra", feeding.fingerprint(own, feeding.load_card("canna-terra")))
    week10 = date.fromordinal(BLOOM_START.toordinal() + 7 * 9)
    shown = feeding.view(own, "de", today=week10, amount_units=feeding.PAGE_UNITS)
    assert (shown["state"], shown["week"]["bloom_week"]) == ("label_ends", 10)
    assert feedplan.last_chart_week(shown) == 9
    state = livestate.LiveState(feeding_view=shown)
    for lang, said in (("de", "Nach Blütewoche 9 "), ("en", "After flowering week 9 ")):
        line = feedplan.plan_lines(state, lang)[0]
        assert line.startswith(said) and "?" not in line, line
    with pytest.raises(feeding.FeedingRefused, match="Ab Blütewoche 10 ") as refused:
        feeding.log_feeding(own, 1, today=week10, lang="de")
    assert "Etikett" not in str(refused.value)  # the label sentence is VIVOSUN's, never CANNA's
    vivosun = feeding.load_card("vivosun-base-ab") or {}
    assert feedplan.last_chart_week({"after_bloom": [{"key": "label_end", "bloom_week": 9}]}) == 8
    assert any("Ende der Blütephase" in n["text"] for n in vivosun["notes"])  # VIVOSUN keeps its label sentence


def test_the_page_note_names_the_cards_own_last_week() -> None:
    js = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
    app = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    notes = [line.split(":", 1)[1] for line in js.splitlines() if '"feed.labelEndsNote"' in line]
    assert len(notes) == 2 and all("{week}" in n and "Woche 8" not in n and "week 8" not in n for n in notes)
    assert all("Etikett" not in n and "label" not in n for n in notes)  # the label sentence is VIVOSUN's card note
    assert 't(say, { week: lastChartWeek(f) })' in app


def test_the_plan_graphic_shows_the_age_without_a_stage_and_no_flush_over_a_feeding_week(on_day: None) -> None:
    view = feeding.preview("canna-terra", "de", today=BLOOM_START)
    timeline = {**view["timeline"], "age": {"days": 49, "weeks": 7, "extra_days": 0}}
    plan = feedplan.plan_graphic({**view, "timeline": timeline, "week_source": {}}, "de")
    assert plan is not None and "Pflanze 7 Wochen alt." in plan["notes"]
    w7 = next(h for h in plan["head"] if h["label"] == "Blüte 7")
    assert (w7["kind"], w7["mark"]) == ("feed", "Flush laut Tabelle, Grund unklar")  # Terra Flores is fed in W7
    assert all(h["mark"] != "spülen" for h in plan["head"] if h["label"] != "Blüte 9")


def test_the_review_fixes_on_the_cards() -> None:
    terra = card("canna-terra")
    assert any("5 ml/l" in c["text"] and "7,6 ml/l" in c["text"] for c in terra["line"]["cautions"])
    plagron = card("plagron-terra")
    keep = next(c for c in plagron["line"]["cautions"] if "mindestens eine Woche" in c["text"])
    assert "Terra Grow vier Tage" in keep["text"] and keep["sources"] == ["PT2", "PT3"]
    coco = card("canna-coco")
    assert any("australische Schema V25.01" in c["text"] for c in coco["line"]["cautions"]) and "CC2" in coco["sources"]
    assert coco["version"] == "DE V26.02" and coco["weeks"]["sources"] == ["CC3"]
    types = json.dumps(card("duengerarten"), ensure_ascii=False)
    for gone in ("Bodenleben", "sofort verfügbar", "Calcium getrennt", "Quelle des Herstellers"):
        assert gone not in types, gone


# ---------- fertilizer market 2 (23.10.2026): the missing lines, checked against the original ----------

def _missing_twins(node: Any, path: str = "") -> list[str]:
    """Every shown text with its English twin: a German text field without its _en field."""
    out: list[str] = []
    if isinstance(node, dict):
        for key in ("name", "summary", "status_note", "text", "label", "role", "application", "application_short",
                    "media_text", "when", "title", "page_note"):
            if isinstance(node.get(key), str) and node[key] and f"{key}_en" not in node:
                out.append(f"{path}.{key}")
        for key, value in node.items():
            out += _missing_twins(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out += _missing_twins(value, f"{path}[{i}]")
    return out


@pytest.mark.parametrize("name", NEW_LINES)
def test_every_new_card_is_checked_sourced_and_bilingual(name: str) -> None:
    data = card(name)
    assert data["check"]["state"] == "checked", name
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["check"]["on"]), name  # a later re-check keeps the test green
    date.fromisoformat(data["check"]["on"])
    assert data["check"]["text"] and data["check"]["text_en"], name
    assert not _missing_twins(data), _missing_twins(data)
    for page in (data.get("pages") or {}).values():
        assert page["sources"] and set(page["sources"]) <= set(data["sources"]), name  # every dose row has its source
        assert all(len(row) == len(data["weeks"]["columns"]) for row in page["rows"].values()), name
    for entry in data["line"].get("plan") or []:
        assert entry["sources"] and set(entry["sources"]) <= set(data["sources"]), name
    raw = json.dumps(data, ensure_ascii=False)
    assert "vivosun.com" not in json.dumps(data["line"]) and "amazon." not in raw and EM_DASH not in raw, name
    text = (NUTRIENTS / f"{name}.json").read_bytes()
    assert not text.startswith(b"\xef\xbb\xbf") and b"\r" not in text, name  # UTF-8 without BOM, LF


def test_the_existing_canna_tables_were_checked_against_the_original() -> None:
    for name in ("canna-terra", "biocanna-indoor"):
        data = card(name)
        assert data["check"]["state"] == "checked", name
        assert feeding._selectable(name, data) is not None, name
        for key in ("text", "text_en"):  # what was checked, not the story of the review
            assert "selben Tag" not in data["check"][key] and "same day" not in data["check"][key], name


def test_only_soil_cards_with_a_checked_plan_are_selectable() -> None:
    terra = card("canna-terra")
    assert feeding._selectable("canna-terra", terra) is not None
    coco_media = {**terra, "line": {**terra["line"], "media": ["coco"]}}
    assert feeding._selectable("canna-terra", coco_media) is None  # a chart for coco never doses on soil
    unchecked = {**terra, "check": {"state": "unchecked", "text": "nicht gegen das Original geprüft",
                                    "text_en": "not checked against the original"}}
    assert feeding._selectable("canna-terra", unchecked) is None
    no_medium = {**terra, "line": {**terra["line"], "media": []}}
    assert feeding._selectable("canna-terra", no_medium) is not None  # a label naming no medium is not shut out
    as_text = {**terra, "line": {**terra["line"], "media": "coco"}}
    assert feeding._selectable("canna-terra", as_text) is None  # a medium written as text is still that medium
    assert feeding.for_soil({"line": {"media": "soil"}}) and feeding.for_soil({"line": {"media": ""}})
    for name in NOT_FOR_SOIL:
        assert not feeding.for_soil(card(name)), name


def test_hesi_soil_doses_half_its_chart_once_confirmed_then_water_then_the_end() -> None:
    own = {**setups()["hesi-soil"], "bloom_start": BLOOM_START.isoformat()}
    assert own["components"] == ["tnt_complex", "bloom_complex"] and own["dose_factor"] == 0.5
    feeding.confirm(own, "hesi-soil", feeding.fingerprint(own, feeding.load_card("hesi-soil")))
    week1 = feeding.view(own, "de", today=BLOOM_START, amount_units=feeding.PAGE_UNITS)
    assert week1["state"] == "ok" and [(d["component"], d["per_liter"]) for d in week1["doses"]] == [
        ("bloom_complex", 2.5)]  # 50 ml per 10 l = 5 ml/l, x 0.5
    week8 = date.fromordinal(BLOOM_START.toordinal() + 7 * 7)
    flush = feeding.view(own, "de", today=week8, amount_units=feeding.PAGE_UNITS)
    assert (flush["state"], flush["doses"], flush["week"]["bloom_week"]) == ("water_only", [], 8)
    week9 = date.fromordinal(BLOOM_START.toordinal() + 7 * 8)
    after = feeding.view(own, "de", today=week9, amount_units=feeding.PAGE_UNITS)
    assert (after["state"], after["week"]["bloom_week"]) == ("label_ends", 9)  # never a harvest Hesi does not print
    assert feedplan.last_chart_week(after) == 8
    with pytest.raises(feeding.FeedingRefused, match="Ab Blütewoche 9 "):
        feeding.log_feeding(own, 1, today=week9, lang="de")


def test_hank_names_a_coco_line_as_not_for_soil_and_offers_hesi(on_day: None) -> None:
    coco = _ask("Ich nutze CANNA COCO", _live())
    assert coco.text.startswith("CANNA COCO A und B steht im Wiki")
    assert "Für Erde ist die Linie nicht gemacht" in coco.text
    assert coco.plan is None and coco.proposals == [] and coco.link["href"] == "#wiki/nutrients/canna-coco"
    aqua = _ask("I use Canna Aqua", _live(), "en")
    assert "The line is not made for soil" in aqua.text and "hydro" in aqua.text and aqua.plan is None
    hesi = _ask("Ich nutze Hesi", _live())
    [switch] = hesi.proposals
    assert switch.body == {"action": "feeding_setup", "setup": "hesi-soil"} and hesi.plan["basis"] == "chart"
    assert hesi.plan["head"][-2]["kind"] == "water" and hesi.plan["head"][-1]["kind"] == "end"
    tri = _ask("Ich nutze TriPart", _live())
    assert "Einen Plan je Liter zeichne ich dafür nicht" in tri.text and tri.plan is None  # soil, but stages


@pytest.mark.parametrize("name", ["canna-terra", "biocanna-indoor", "hesi-soil", "green-house-feeding-mineral",
                                  "atami-bcuzz-soil", "biobizz-2026"])
def test_the_wiki_keeps_every_empty_chart_cell_in_its_column(name: str) -> None:
    """Fertilizer market 2: the wiki dropped None from chart rows, so Terra Flores stood under days 1 to 5."""
    raw = card(name)
    for lang in ("de", "en"):
        shown = wiki.card_view(wiki.find("nutrients", name), lang, ["cannabis"])["card"]
        for key, page in raw["pages"].items():
            assert shown["pages"][key]["rows"] == page["rows"], (name, lang, key)
