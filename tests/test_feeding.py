"""Feeding card and planner: the BioBizz chart number for number, never above it, the week from the owner's stage
(or a bloom_start the owner wrote), no amounts before the owner confirms the card and the week, every amount
computed on the server, both languages."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import feeding, i18n
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.feeding import FeedingRefused, current_week, doses, evaluate, load_card, tenth_down, totals, view
from vgrow.i18n import UserError, localize
from vgrow.plant import PlantLog, load_profile
from vgrow.server import COOKIE, make_app

REPO = Path(__file__).resolve().parent.parent
CARD_FILE = REPO / "kartei" / "nutrients" / "biobizz-2026.json"
BLOOM_START = date(2026, 10, 22)

# BioBizz nutrient schedule 2026, ml per litre of water, as verified against the German and the English PDF
# (retrieved 2026-10-22): propagation | vegetative | flowering weeks 1..8. None is "-" in the chart.
CHART = {
    "all_mix": {
        "bio_grow": (2, 2, (3, 2, 2, 2, 2, 2, 2, 1)),
        "bio_bloom": (None, None, (1, 2, 3, 3, 3, 4, 4, 4)),
        "top_max": (None, None, (1, 2, 2, 3, 3, 4, 4, 4)),
    },
    "light_mix": {
        "bio_grow": (2, 2, (3, 4, 4, 4, 2, 2, 2, 1)),
        "bio_bloom": (None, None, (2, 3, 3, 4, 4, 4, 3, 3)),
        "top_max": (None, None, (1, 2, 2, 3, 3, 4, 4, 1)),
        "alg_a_mic": (None, 2, (2, 2, 3, 3, 4, 4, 3, 2)),
    },
}
SOURCES = {
    "N1": "https://biobizz.com/downloads/biobizz-nutrient-schedule-2026-de.pdf",
    "N2": "https://biobizz.com/downloads/biobizz-nutrient-schedule-2026.pdf",
    "N3": "https://biobizz.com/knowledge-base/nutrient-schedule",
    "N4": "https://biobizz.com/cultivation-tips/",
    "N5": "https://vivosun.com/en-CA/growing_guide/what-are-autoflowering-seeds/",
    "N6": "https://420seedz.de/guide/cannabis-anbauen-anfaenger",
    # the fertilizer market (23.10.2026): the line block cites the All-Mix page and the 2026 data sheets
    "N7": "https://biobizz.com/allmix/",
    "N8": "https://biobizz.com/downloads/bio-grow-data-sheet-2026-en-gb-988ef409.pdf",
    "N9": "https://biobizz.com/downloads/bio-bloom-data-sheet-2026-en-gb-9ad87a34.pdf",
    "N10": "https://biobizz.com/downloads/top-max-data-sheet-2026-en-gb-93e37af0.pdf",
    "N11": "https://biobizz.com/downloads/all-mix-data-sheet-2026-en-gb-762f8623.pdf",
}


# Every fertilizer the switch offers (23.10.2026): plant.json's setups, then the selectable cards in file order.
ALL_SETUPS = ["biobizz", "vivosun", "biocanna-indoor", "canna-terra", "hesi-soil", "plagron-natural", "plagron-terra"]


def card() -> dict:  # type: ignore[type-arg]
    return json.loads(CARD_FILE.read_text(encoding="utf-8"))


def setup() -> dict:  # type: ignore[type-arg]
    """The BioBizz setup merged with the shared feeding keys (plant.json holds two setups since 23.10.2026)."""
    return feeding.setups_of(load_profile())["biobizz"]


def planned(**extra: object) -> dict:  # type: ignore[type-arg]
    """The run's setup with a bloom_start the owner wrote in (22.10.2026): the week no longer waits for a stage."""
    return {**setup(), "bloom_start": BLOOM_START.isoformat(), **extra}


def confirm(s: dict) -> dict:  # type: ignore[type-arg]
    """What the page's two taps do: the card and the fingerprint it showed."""
    return feeding.confirm(s, s["card"], feeding.fingerprint(s, load_card(s["card"])))


def manual(key: str, since: date = BLOOM_START) -> tuple[dict, list]:  # type: ignore[type-arg]
    """A stage the owner set, as plant.current_stage and plant.owner_stages hand it over."""
    stage = next(s for s in load_profile()["stages"] if s["key"] == key)  # type: ignore[index]
    entry = {"key": key, "since": since.isoformat()}
    return {**stage, "since": entry["since"], "set_by": "manual"}, [entry]


@pytest.fixture
def on_day(monkeypatch):  # type: ignore[no-untyped-def]
    """Pin the server's calendar: the week must not depend on the day the tests run."""
    def pin(day: date) -> None:
        monkeypatch.setattr(feeding, "local_today", lambda: day)
    pin(BLOOM_START)
    return pin


# ---------- the card ----------

def test_the_chart_is_the_biobizz_2026_chart_number_for_number() -> None:
    data = card()
    assert (data["product"], data["version"], data["status"], data["unit"]) == ("BioBizz", "2026", "draft", "ml/l")
    assert set(data["pages"]) == set(CHART)
    for page, rows in CHART.items():
        assert set(data["pages"][page]["rows"]) == set(rows), page
        for component, (propagation, veg, bloom) in rows.items():
            assert data["pages"][page]["rows"][component] == [propagation, veg, *bloom], (page, component)
    weeks = data["weeks"]
    assert [c["key"] for c in weeks["columns"]] == ["propagation", "veg", *(f"bloom_{n}" for n in range(1, 9))]
    assert [c.get("bloom_week") for c in weeks["columns"][2:]] == list(range(1, 9))
    assert (weeks["water"]["bloom_week"], weeks["harvest"]["bloom_week"]) == (9, 10)
    assert {k: c["label"] for k, c in data["components"].items()} == {
        "bio_grow": "Bio-Grow", "bio_bloom": "Bio-Bloom", "top_max": "Top-Max", "alg_a_mic": "Alg-A-Mic"}
    assert [k for k, c in data["components"].items() if c.get("optional")] == ["alg_a_mic"]
    assert {k: s["url"] for k, s in data["sources"].items()} == SOURCES
    assert {s["retrieved"] for k, s in data["sources"].items() if k in ("N1", "N2", "N3", "N4", "N5", "N6")}         == {"2026-10-22"}  # the chart as checked on 22.10.; the line block's sources were read on 23.10.2026
    assert {s["retrieved"] for k, s in data["sources"].items() if k in ("N7", "N8", "N9", "N10", "N11")}         == {"2026-10-23"}
    for item in [*data["notes"], data["frequency"], weeks, *data["pages"].values()]:
        assert item["sources"] and set(item["sources"]) <= set(SOURCES), item
    notes = " ".join(n["text_en"] for n in data["notes"])
    # Hank v1 addendum N3 (binding): the manufacturer sentences in own words, no verbatim quote in text_en
    for words in ("from the day of the switch to flowering", "no own feeding schedule for autoflowers",
                  "1/2 to 1/4 of the nutrient amount"):
        assert words in notes
    assert '"' not in notes
    assert "6.2-6.3" in notes and "2-3 weeks" in notes and "half the dose" in notes
    assert data["frequency"]["text_en"].startswith("The chart does not say how often to feed")


def test_the_run_uses_the_light_mix_page_half_dose_and_the_camera_estimate() -> None:
    feed = setup()
    assert (feed["card"], feed["page"], feed["dose_factor"]) == ("biobizz-2026", "light_mix", 0.5)
    vivo = feeding.setups_of(load_profile())["vivosun"]  # the second fertilizer shares factor and dates
    assert (vivo["card"], vivo["page"], vivo["components"]) == ("vivosun-base-ab", "label", ["base_a", "base_b"])
    assert vivo["dose_factor"] == 0.5 and set(vivo["dose_factor_sources"]) == {"N5", "N6"}
    assert vivo["bloom_start_estimate"] == feed["bloom_start_estimate"] and vivo["setup"] == "vivosun"
    assert "setups" not in feed and "card" not in load_profile()["feeding"]  # type: ignore[index]
    # The camera's date is an estimate, kept apart from a bloom_start the owner would write: it never doses.
    assert feed["bloom_start"] is None and feed["bloom_start_estimate"] == "2026-10-22"
    assert feed["components"] == ["bio_grow", "bio_bloom", "top_max"]  # no Alg-A-Mic
    assert set(feed["dose_factor_sources"]) == {"N5", "N6"} and feed["bloom_start_sources"] == ["N3"]
    assert "Kamerabild" in feed["bloom_start_estimate_note"] and "camera image" in feed["bloom_start_estimate_note_en"]
    for key in ("page_note", "bloom_start_note", "bloom_start_estimate_note"):  # the grower is "du", not "Betreiber"
        assert "Betreiber" not in feed[key] and "operator" not in feed[f"{key}_en"], key
    assert "Schätzung" not in feed["bloom_start_note"] and "estimate" not in feed["bloom_start_note_en"]
    profile = load_profile()  # everything that was there before stays
    assert profile["stage_estimate"]["key"] == "early_flower" and profile["medium"]["type"] == "soil"  # type: ignore[index]
    assert set(profile["sources"]) >= {"W1", "W2", "W3", "W4"}  # type: ignore[index]


# ---------- never above the chart ----------

@pytest.mark.parametrize("factor", ["1/2", "1/4", "1/3", "7/10", "19/20", "1"])
def test_doses_and_totals_never_exceed_the_chart(factor: str) -> None:
    f = Fraction(factor)
    litres = [Fraction(1, 10), Fraction(3, 10), Fraction(1), Fraction(3, 2), Fraction(27, 10), Fraction(20)]
    checked = 0
    for page in card()["pages"].values():
        for column in range(10):
            for d in doses(page, list(page["rows"]), column, f):
                chart = Fraction(d["chart"])
                per_liter = tenth_down(d["exact"])
                assert per_liter <= chart * f <= chart and chart * f - per_liter < Fraction(1, 10)
                assert (per_liter * 10).denominator == 1  # whole tenths of a millilitre
                for liters in litres:
                    total = tenth_down(d["exact"] * liters)
                    assert total <= chart * f * liters <= chart * liters
                    assert chart * f * liters - total < Fraction(1, 10)
                checked += 1
    assert checked > 50
    capped = doses(card()["pages"]["light_mix"], ["bio_grow"], 2, Fraction(3, 2))  # a factor above 1 adds nothing
    assert capped[0]["exact"] == 3


def test_amounts_are_exact_fractions_rounded_down() -> None:
    page = card()["pages"]["light_mix"]
    # 3 ml/l x 0.7 is 2.1 exactly; in floating point it is 2.0999999999999996 and would round down to 2.0.
    assert totals(doses(page, ["bio_grow"], 2, Fraction("0.7")), Fraction(1))[0]["per_liter"] == 2.1
    plan = totals(doses(page, ["bio_grow", "bio_bloom", "top_max"], 2, Fraction(1, 2)), Fraction(3, 2))
    assert [(p["component"], p["per_liter"], p["total"]) for p in plan] == [
        ("bio_grow", 1.5, 2.2), ("bio_bloom", 1.0, 1.5), ("top_max", 0.5, 0.7)]  # 2.25 and 0.75 round down


# ---------- the week ----------

def test_the_week_counts_from_bloom_start_then_water_then_harvest() -> None:
    keys = [f"bloom_{n}" for n in range(1, 9)]
    cases = [(-30, "veg", None), (-1, "veg", None), (0, "bloom_1", 1), (6, "bloom_1", 1), (7, "bloom_2", 2),
             (49, "bloom_8", 8), (55, "bloom_8", 8), (56, "water", 9), (62, "water", 9), (63, "harvest", 10),
             (200, "harvest", 29)]
    for offset, key, week in cases:
        got = current_week(BLOOM_START, BLOOM_START + timedelta(days=offset), keys, 10)
        assert (got["key"], got["bloom_week"]) == (key, week), offset
    assert current_week(None, BLOOM_START, keys, 10)["key"] == "veg"  # no bloom_start: still vegetative


def test_each_phase_gives_the_right_column_or_nothing() -> None:
    feed, chart = planned(), card()

    def plan(offset: int) -> dict:  # type: ignore[type-arg]
        return evaluate(feed, chart, True, BLOOM_START + timedelta(days=offset))

    before = plan(-1)  # vegetative column: only Bio-Grow, 2 ml/l at half dose
    assert before["state"] == "ok" and [(d["component"], d["exact"]) for d in before["doses"]] == [("bio_grow", 1)]
    week8 = plan(55)  # light mix, flowering week 8: 1, 3, 1 ml/l
    assert [(d["component"], d["chart"]) for d in week8["doses"]] == [("bio_grow", 1), ("bio_bloom", 3), ("top_max", 1)]
    assert plan(56)["state"] == "water_only" and plan(56)["doses"] == []
    assert plan(63)["state"] == "harvest" and plan(63)["doses"] == []
    assert evaluate({**feed, "bloom_start": "22.10.2026"}, chart, True, BLOOM_START)["problem"] == (
        "feeding.bad_date", {"field": "bloom_start"})
    assert evaluate({**feed, "bloom_start_estimate": "x"}, chart, True, BLOOM_START)["problem"] == (
        "feeding.bad_date", {"field": "bloom_start_estimate"})
    for factor in (0, -0.5, 1.5, "0.5", None, True):
        assert evaluate({**feed, "dose_factor": factor}, chart, True, BLOOM_START)["state"] == "no_card", factor
    assert evaluate({**feed, "page": "hydro"}, chart, True, BLOOM_START)["problem"][0] == "feeding.no_page"
    assert evaluate(feed, None, True, BLOOM_START)["problem"] == ("feeding.no_card", {"card": "biobizz-2026"})
    assert evaluate(None, chart, True, BLOOM_START)["problem"] == ("feeding.none", {})
    assert load_card("../plant") is None and load_card("no-such-card") is None


# ---------- draft, confirm, log ----------

def test_a_draft_shows_the_chart_and_its_sources_but_no_amounts(on_day) -> None:  # type: ignore[no-untyped-def]
    shown = view(setup(), "de", liters="1,5")
    assert (shown["state"], shown["confirmed"], shown["doses"], shown["plan"]) == ("draft", False, None, None)
    assert [r["component"] for r in shown["chart"]] == ["bio_grow", "bio_bloom", "top_max", "alg_a_mic"]
    assert shown["chart"][0]["values"] == [2, 2, 3, 4, 4, 4, 2, 2, 2, 1] and shown["chart"][0]["now"] == 3
    alg = shown["chart"][-1]
    assert (alg["optional"], alg["owned"], alg["hidden_by_default"]) == (True, False, True)
    assert shown["week"] == {"key": "bloom_1", "phase": "bloom", "bloom_week": 1, "day": 0, "label": "Blütewoche 1",
                             "note": None}
    assert set(shown["sources"]) == set(SOURCES) and shown["sources"]["N1"]["url"] == SOURCES["N1"]
    assert shown["frequency_note"].startswith("Die Tabelle sagt nicht, wie oft gedüngt wird")
    assert shown["card"]["status"] == "draft" and shown["dose_factor"] == 0.5 and shown["last_fed"] is None
    # The confirm step shows where the week comes from: here the camera's estimate, with its own words.
    assert shown["week_source"] == {"kind": "estimate", "confirmed": False, "stage": None, "bloom_start": "2026-10-22"}
    assert shown["bloom_estimate_note"].startswith("Obergrenze, kein Beginn: das erste ausgewertete Kamerabild (22.10.2026")
    assert len(shown["fingerprint"]) == 64 and shown["reconfirm"] is False
    with pytest.raises(FeedingRefused, match="bestätigen"):
        feeding.log_feeding(setup(), 1.5)


def test_confirm_then_log_computes_every_amount_on_the_server(on_day) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        log = PlantLog()
        log.set_stage("early_flower", BLOOM_START.isoformat())  # the owner confirmed the stage on 22.10.2026
        app = make_app(DemoBox(), Alerts(), "k", plant_log=log)
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})

            async def cmd(body: dict, headers: dict | None = None) -> tuple[int, dict]:  # type: ignore[type-arg]
                resp = await client.post("/api/cmd", json=body, headers=headers or {})
                return resp.status, await resp.json()

            shown = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            assert shown["state"] == "needs_choice" and shown["doses"] is None  # two fertilizers, none chosen
            status, res = await cmd({"action": "feeding_setup", "setup": "biobizz"})
            assert status == 200 and res == {"ok": True, "setup": "biobizz"}
            shown = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            seen = shown["fingerprint"]  # what the page displayed and sends back
            page = {"card": "biobizz-2026", "fingerprint": seen}
            status, res = await cmd({"action": "feeding_log", "liters": 1.5, **page})
            assert status == 409 and res["error"].startswith("Erst die Düngertabelle prüfen und bestätigen")
            status, res = await cmd({"action": "feeding_confirm", "card": "biobizz-2025", "fingerprint": seen})
            assert status == 409 and "BioBizz Düngeschema 2026" in res["error"]  # an old page confirms nothing; named
            for wrong in ({"card": "biobizz-2026"}, {"card": "biobizz-2026", "fingerprint": "0" * 64},
                          {"fingerprint": seen}):
                status, res = await cmd({"action": "feeding_confirm", **wrong})
                assert status == 409, wrong  # the card and exactly the numbers the page showed, or nothing
            assert not feeding.confirmation_path().exists()
            status, res = await cmd({"action": "feeding_confirm", "card": "biobizz-2026", "fingerprint": seen})
            assert status == 200 and res["ok"] is True
            saved = json.loads(feeding.confirmation_path().read_text(encoding="utf-8"))
            assert saved == {"cards": {"biobizz-2026": {"card": "biobizz-2026", "version": "2026", "page": "light_mix",
                                                        "fingerprint": seen, "confirmed_at": res["confirmed_at"]}}}
            fed = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            assert (fed["state"], fed["confirmed"], fed["confirmed_at"]) == ("ok", True, res["confirmed_at"])
            assert fed["week_source"] == {"kind": "stage", "confirmed": True, "stage": "Frühe Blüte (Stretch)",
                                          "bloom_start": "2026-10-22"}
            assert fed["warning"] is None and fed["bloom_estimate_note"] is None
            assert fed["doses"] == [
                {"component": "bio_grow", "label": "Bio-Grow", "chart": 3, "per_liter": 1.5},
                {"component": "bio_bloom", "label": "Bio-Bloom", "chart": 2, "per_liter": 1.0},
                {"component": "top_max", "label": "Top-Max", "chart": 1, "per_liter": 0.5}]
            planned = await (await client.get("/api/feeding?liters=1,5")).json()
            assert [(i["component"], i["total"]) for i in planned["plan"]["items"]] == [
                ("bio_grow", 2.2), ("bio_bloom", 1.5), ("top_max", 0.7)]
            assert planned["plan"]["liters"] == 1.5 and not feeding.log_path().exists()  # planning logs nothing
            assert (await client.get("/api/feeding?liters=150")).status == 400
            # The page sends litres. Amounts it sends along are ignored: the server computes its own.
            status, res = await cmd({"action": "feeding_log", "liters": "1,5", "doses": {"bio_grow": 99},
                                     "items": [{"component": "bio_grow", "total": 99}], **page})
            assert status == 200
            assert [(i["component"], i["total"]) for i in res["entry"]["items"]] == [
                ("bio_grow", 2.2), ("bio_bloom", 1.5), ("top_max", 0.7)]
            line = json.loads(feeding.log_path().read_text(encoding="utf-8").splitlines()[-1])
            assert (line["liters"], line["dose_factor"], line["week"], line["state"]) == (1.5, 0.5, "bloom_1", "ok")
            assert 99 not in [i["total"] for i in line["items"]]
            last = (await (await client.get("/api/state", headers={"X-Lang": "en"})).json())["plant"]["feeding"]
            assert last["last_fed"]["liters"] == 1.5 and last["last_fed"]["week"]["label"] == "Flowering week 1"
            assert last["last_fed"]["items"][0] == {"component": "bio_grow", "label": "Bio-Grow", "per_liter": 1.5,
                                                    "total": 2.2}
            assert (last["last_fed"]["unit"], last["last_fed"]["digits"], last["last_fed"]["other_card"]) == (
                "ml", 1, False)
            assert line["unit"] == "ml" and line["setup"] == "biobizz" and line["card"] == "biobizz-2026"
            for liters in (0, -1, 25, "abc", None, True):
                status, res = await cmd({"action": "feeding_log", "liters": liters, **page}, {"X-Lang": "en"})
                assert status == 400, liters
            assert res["error"] in ("Liters between 0.1 and 20", "Not a number: True")
            assert len(feeding.log_path().read_text(encoding="utf-8").splitlines()) == 1

    asyncio.run(run())


def test_week_9_logs_plain_water_and_week_10_refuses(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(planned())
    on_day(BLOOM_START + timedelta(days=56))
    water = view(planned(), "de", liters=2)
    assert water["state"] == "water_only" and water["doses"] == [] and water["plan"]["items"] == []
    entry = feeding.log_feeding(planned(), 2)
    assert entry["state"] == "water_only" and entry["items"] == [] and entry["week"]["label"] == "Blütewoche 9: nur Wasser"
    line = json.loads(feeding.log_path().read_text(encoding="utf-8").splitlines()[-1])
    assert (line["week_source"], line["bloom_start"]) == ("plan", "2026-10-22")  # the log says where the week came from
    on_day(BLOOM_START + timedelta(days=63))
    assert view(planned(), "de")["state"] == "harvest"
    with pytest.raises(FeedingRefused, match="Ab Blütewoche 10"):
        feeding.log_feeding(planned(), 2)
    with pytest.raises(UserError):
        view(planned(), "de", liters="zwei")


# ---------- both languages ----------

def _pairs(obj: object, where: str = "") -> list[tuple[str, object, object]]:
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and not k.endswith("_en") and f"{k}_en" in obj:
                found.append((f"{where}.{k}", v, obj[f"{k}_en"]))
            found += _pairs(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _pairs(v, f"{where}[{i}]")
    return found


def test_every_displayed_feeding_text_has_an_english_twin(on_day) -> None:  # type: ignore[no-untyped-def]
    data = card()
    displayed = [data, *data["weeks"]["columns"], data["weeks"]["water"], data["weeks"]["harvest"],
                 *data["components"].values(), *data["pages"].values(), data["frequency"], *data["notes"],
                 *data["sources"].values()]
    for item in displayed:
        for key in ("name", "summary", "status_note", "label", "text", "title"):
            if key in item:
                assert item.get(f"{key}_en"), (key, item[key])
    feed = setup()
    for key in ("page_note", "dose_factor_note", "bloom_start_note", "bloom_start_estimate_note"):
        assert feed[f"{key}_en"], key
    assert len(_pairs(data)) >= 30
    en, de = view(setup(), "en"), view(setup(), "de")
    text = json.dumps(en, ensure_ascii=False)
    assert "_en" not in text.replace("en-CA", "")  # no twin left; the VIVOSUN URL has en-CA in it
    assert en["week"]["label"] == "Flowering week 1" and de["week"]["label"] == "Blütewoche 1"
    # Pre-fertilized, as the German "leicht vorgedüngt": "lightly fertilized" would read like an instruction.
    assert en["page"]["label"] == "Light-Mix or Coco-Mix (lightly pre-fertilized)"
    assert "(lightly pre-fertilized)" in card()["summary_en"] and "lightly fertilized" not in card()["summary_en"]
    assert en["card"]["name"] == "BioBizz nutrient schedule 2026" and en["frequency_note"].startswith("The chart")
    assert not re.search(r"[äöüß]", json.dumps({k: en[k] for k in en if k != "sources"}, ensure_ascii=False))
    for lang in ("de", "en"):
        token = i18n.REQUEST_LANG.set(lang)
        try:
            with pytest.raises(UserError) as raised:
                feeding.parse_liters("25")
        finally:
            i18n.REQUEST_LANG.reset(token)
        assert str(raised.value) == {"de": "Liter zwischen 0,1 und 20", "en": "Liters between 0.1 and 20"}[lang]


# ---------- the week follows the owner's stage (review of 22.10.2026) ----------

def test_a_stage_before_flowering_forces_the_vegetative_column(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(planned())
    stage, owned = manual("veg")
    # The review's probe: card confirmed, the owner sets veg, yet the block said "Flowering week 1" with Top-Max.
    shown = view(planned(), "de", stage=stage, owner_stages=owned)  # plant.json says flowering, the owner says veg
    assert (shown["state"], shown["week"]["key"], shown["week_source"]["kind"]) == ("ok", "veg", "stage")
    assert [(d["component"], d["per_liter"]) for d in shown["doses"]] == [("bio_grow", 1.0)]  # no bloom products
    assert shown["warning"] == ("Deine Phase ist Wachstum, plant.json setzt den Blütebeginn aber auf 22.10.2026. Das "
                                "Cockpit nimmt die Spalte Wachstum; stimmt der Blütebeginn, die Phase ändern.")
    seedling, owned = manual("seedling")
    plain = view(setup(), "de", stage=seedling, owner_stages=owned)  # the camera's estimate says flowering: ignored
    # A seedling takes the propagation column (23.10.2026); for BioBizz it holds the same Bio-Grow 2 ml/l.
    assert (plain["state"], plain["week"]["key"], plain["warning"], plain["bloom_estimate_note"]) == (
        "ok", "propagation", None, None)
    assert [(d["component"], d["per_liter"]) for d in plain["doses"]] == [("bio_grow", 1.0)]


def test_a_flowering_stage_counts_from_the_day_the_owner_set_the_first_one(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(setup())
    first = BLOOM_START + timedelta(days=10)
    log = [{"key": "veg", "since": "2026-10-01"}, {"key": "early_flower", "since": first.isoformat()},
           {"key": "mid_flower", "since": (first + timedelta(days=7)).isoformat()}]
    mid = manual("mid_flower", first + timedelta(days=7))[0]
    on_day(first + timedelta(days=15))  # day 15 of flowering: week 3
    shown = view(setup(), "de", stage=mid, owner_stages=log)
    assert (shown["state"], shown["week"]["key"], shown["bloom_start"]) == ("ok", "bloom_3", first.isoformat())
    assert shown["week_source"] == {"kind": "stage", "confirmed": True, "stage": "Mitte der Blüte",
                                    "bloom_start": first.isoformat()}
    restart = first + timedelta(days=13)  # back to veg in between: a new flowering spell starts its own weeks
    again = [*log, {"key": "veg", "since": restart.isoformat()}, {"key": "early_flower", "since": restart.isoformat()}]
    assert view(setup(), "de", stage=manual("early_flower", restart)[0], owner_stages=again)["week"]["key"] == "bloom_1"
    written = view(planned(), "de", stage=mid, owner_stages=log)  # a bloom_start the owner wrote in plant.json wins
    assert (written["week_source"]["kind"], written["week"]["key"], written["warning"]) == ("plan", "bloom_4", None)


def test_an_estimated_week_never_doses(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(setup())
    shown = view(setup(), "de", liters="1,5")  # the card is confirmed, the week is only the camera's estimate
    assert (shown["state"], shown["doses"], shown["plan"]) == ("needs_stage", None, None)
    assert shown["week"]["key"] == "bloom_1"
    assert shown["week_source"]["kind"] == "estimate" and shown["bloom_estimate_note"].startswith("Obergrenze")
    with pytest.raises(FeedingRefused, match="Erst die Phase bestätigen"):
        feeding.log_feeding(setup(), 1.5)
    assert not feeding.log_path().exists()
    estimated = {**manual("early_flower")[0], "set_by": "estimate"}  # an estimated stage confirms nothing either
    assert view(setup(), "de", stage=estimated, owner_stages=[])["state"] == "needs_stage"
    unknown = view({k: v for k, v in setup().items() if k != "bloom_start_estimate"}, "de")
    assert (unknown["state"], unknown["week_source"]["kind"], unknown["week"]["key"]) == ("needs_stage", "none", "veg")
    stage, owned = manual("early_flower")  # the owner confirms the stage: from then on amounts
    assert view(setup(), "de", stage=stage, owner_stages=owned)["state"] == "ok"


def test_a_stage_that_does_not_fit_the_chart_week_is_flagged(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(setup())
    stage, owned = manual("early_flower")
    later = planned(bloom_start=(BLOOM_START + timedelta(days=10)).isoformat())  # plant.json: flowering from Nov 1
    shown = view(later, "en", stage=localize(stage, "en"), owner_stages=owned)
    assert shown["week"]["key"] == "veg" and shown["warning"] == (
        "Your stage is Early flower (stretch), but the chart is at Vegetative (flowering start Nov 1, 2026). Please "
        "check the stage, or enter the right flowering start as bloom_start in plant.json.")
    harvest, owned = manual("harvest")  # a harvest window while the chart still feeds
    flagged = view(setup(), "de", stage=harvest, owner_stages=owned)
    assert flagged["state"] == "ok" and flagged["warning"].startswith(
        f"Deine Phase ist {harvest['label']}, die Tabelle steht aber bei Blütewoche 1 (Blütebeginn 22.10.2026).")
    fits = manual("early_flower")
    assert view(setup(), "de", stage=fits[0], owner_stages=fits[1])["warning"] is None


# ---------- the confirmation covers what was compared ----------

def test_the_confirmation_covers_exactly_the_numbers_the_owner_compared(on_day, tmp_path) -> None:  # type: ignore[no-untyped-def]
    library = tmp_path / "kartei"
    (library / "nutrients").mkdir(parents=True)
    path = library / "nutrients" / "biobizz-2026.json"
    path.write_text(CARD_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    s = planned()
    feeding.confirm(s, s["card"], view(s, "de", library=library)["fingerprint"], library=library)
    assert view(s, "de", library=library)["state"] == "ok"

    def edited(change) -> dict:  # type: ignore[no-untyped-def, type-arg]
        data = card()
        change(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        return view(s, "de", library=library, amount_units=feeding.PAGE_UNITS)  # as the page asks

    # The review's probe: a corrected number (week 1 Bio-Grow 3 -> 8) and a new version stayed "confirmed".
    for change in (lambda d: d["pages"]["light_mix"]["rows"]["bio_grow"].__setitem__(2, 8),
                   lambda d: d.__setitem__("version", "2027"), lambda d: d.__setitem__("unit", "g/l"),
                   lambda d: d["weeks"]["water"].__setitem__("bloom_week", 10)):
        again = edited(change)
        assert (again["state"], again["confirmed"], again["doses"], again["reconfirm"]) == ("draft", False, None, True)
    odd = edited(lambda d: d.__setitem__("unit", "ml/10l"))  # a unit the cockpit does not compute: nothing at all
    assert (odd["state"], odd["doses"]) == ("no_card", None) and "ml/10l" in odd["problem"]
    assert edited(lambda d: None)["state"] == "ok"  # back to what was compared: confirmed again
    same = edited(lambda d: d["pages"]["light_mix"]["rows"]["bio_grow"].__setitem__(2, 3.0))  # 3.0 is the same 3
    assert same["state"] == "ok"
    assert edited(lambda d: d["components"]["bio_grow"].__setitem__("label", "Bio Grow"))["state"] == "ok"  # a label
    for other in ({"page": "all_mix"}, {"components": ["bio_grow"]}, {"dose_factor": 0.25}):
        assert view({**s, **other}, "de", library=library)["state"] == "draft", other
    with pytest.raises(FeedingRefused):
        feeding.confirm(s, s["card"], None, library=library)  # no fingerprint from the page: nothing confirmed
    old = {"card": "biobizz-2026", "confirmed_at": "2026-10-22T10:00:00"}  # a confirmation from before fingerprints
    feeding.confirmation_path().write_text(json.dumps(old), encoding="utf-8")
    assert view(s, "de", library=library)["state"] == "draft"


# ---------- litres: the typo guard catches 15 for 1.5 ----------

def test_more_than_5_litres_needs_the_second_tap(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(planned())
    with pytest.raises(UserError, match="15 l ist mehr als 5 l"):
        view(planned(), "de", liters="15")
    big = view(planned(), "de", liters="15", large=True)  # the second tap: then the amounts for 15 l
    assert [(i["component"], i["total"]) for i in big["plan"]["items"]] == [
        ("bio_grow", 22.5), ("bio_bloom", 15.0), ("top_max", 7.5)]
    with pytest.raises(UserError):
        feeding.log_feeding(planned(), "15")
    assert not feeding.log_path().exists()
    assert feeding.log_feeding(planned(), "15", large=True)["liters"] == 15.0
    assert view(planned(), "de", liters="5")["plan"]["liters"] == 5.0  # up to 5 l without asking
    with pytest.raises(UserError, match="zwischen 0,1 und 20"):
        view(planned(), "de", liters="25", large=True)  # the hard limit holds with the second tap too
    own = planned(confirm_above_liters=2)  # a small pot: plant.json asks earlier
    with pytest.raises(UserError, match="2,5 l ist mehr als 2 l"):
        view(own, "de", liters="2,5")
    assert view(own, "de")["liters_confirm_above"] == 2.0 and view(planned(), "de")["liters_confirm_above"] == 5.0
    assert view(planned(confirm_above_liters=50), "de")["liters_confirm_above"] == 5.0  # out of range: the default


def test_the_server_asks_for_the_second_tap_too(on_day) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        log = PlantLog()
        log.set_stage("early_flower", BLOOM_START.isoformat())
        log.set_feeding_choice("biobizz", ["biobizz", "vivosun"])
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), "k", plant_log=log))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            seen = (await (await client.get("/api/state")).json())["plant"]["feeding"]["fingerprint"]
            body = {"action": "feeding_confirm", "card": "biobizz-2026", "fingerprint": seen}
            page = {"card": "biobizz-2026", "fingerprint": seen}
            assert (await client.post("/api/cmd", json=body)).status == 200
            refused = await client.get("/api/feeding?liters=15", headers={"X-Lang": "en"})
            assert refused.status == 400
            assert (await refused.json())["error"] == "15 l is more than 5 l: please check the liters and confirm"
            assert (await (await client.get("/api/feeding?liters=15&large=1")).json())["plan"]["liters"] == 15.0
            assert (await client.post("/api/cmd", json={"action": "feeding_log", "liters": "15", **page})).status == 400
            assert not feeding.log_path().exists()
            logged = await client.post("/api/cmd", json={"action": "feeding_log", "liters": "15", "large": True,
                                                         **page})
            assert logged.status == 200 and (await logged.json())["entry"]["liters"] == 15.0

    asyncio.run(run())


# ---------- VIVOSUN Base A/B, units, two setups, the dated plan (owner, 23.10.2026) ----------

VIVO_CARD = REPO / "kartei" / "nutrients" / "vivosun-base-ab.json"
BIOBIZZ_FINGERPRINT = "998ca5082913fd49ec2b3fdc90dd9f23c970a4ef7cf67a0b827e766733d4dd8c"  # before the units build
HALF_SPOON, CENT = Fraction(1, 2), Fraction(1, 100)


def pview(*args: object, **kwargs: object) -> dict:  # type: ignore[type-arg]
    """view() as the page calls it: every amount unit (ml, g and spoons), server.feeding_week."""
    return view(*args, amount_units=feeding.PAGE_UNITS, **kwargs)  # type: ignore[arg-type]


def vivo(**extra: object) -> dict:  # type: ignore[type-arg]
    return {**feeding.setups_of(load_profile())["vivosun"], **extra}


def vplanned(**extra: object) -> dict:  # type: ignore[type-arg]
    return vivo(bloom_start=BLOOM_START.isoformat(), **extra)


def choose(setup_id: str) -> PlantLog:
    log = PlantLog()
    log.set_feeding_choice(setup_id, feeding.setups_of(load_profile()))
    return log


def vivo_page() -> dict:  # type: ignore[type-arg]
    return json.loads(VIVO_CARD.read_text(encoding="utf-8"))["pages"]["label"]


@pytest.mark.parametrize("factor", ["1/2", "1/4", "1/3", "7/10", "19/20", "1"])
def test_g_amounts_never_exceed_label_times_factor(factor: str) -> None:
    f = Fraction(factor)
    checked = 0
    for column in range(10):
        for liters in (Fraction(1, 10), Fraction(3, 10), Fraction(1), Fraction(3, 2), Fraction(27, 10), Fraction(20)):
            for row in totals(doses(vivo_page(), ["base_a", "base_b"], column, f), liters, "g", HALF_SPOON):
                chart = Fraction(str(row["chart"]))
                per_liter, total = Fraction(str(row["per_liter"])), Fraction(str(row["total"]))
                assert per_liter <= chart * f and chart * f - per_liter < CENT
                assert total <= chart * f * liters and chart * f * liters - total < CENT
                assert (per_liter / CENT).denominator == 1 and (total / CENT).denominator == 1
                for key, grams in (("spoons_per_liter", per_liter), ("spoons_total", total)):
                    n = row[key]
                    if n is not None:
                        assert (Fraction(str(n)) / HALF_SPOON).denominator == 1  # whole half spoons
                        assert Fraction(str(n)) * HALF_SPOON <= grams  # never more than the gram figure shown
                checked += 1
    assert checked == 10 * 6 * 2


def test_spoon_rounding_examples() -> None:
    can = totals(doses(vivo_page(), ["base_a"], 2, HALF_SPOON), Fraction(3, 2), "g", HALF_SPOON)[0]
    assert (can["per_liter"], can["total"], can["spoons_per_liter"], can["spoons_total"]) == (0.75, 1.12, 1.5, 2.0)
    tiny = totals(doses(vivo_page(), ["base_a"], 0, HALF_SPOON), Fraction(1, 5), "g", HALF_SPOON)[0]
    assert (tiny["total"], tiny["spoons_total"]) == (0.05, None)  # 0.25 g/l x 0.2 l: under half a spoon, the scale
    trap = totals(doses(vivo_page(), ["base_a"], 2, Fraction("0.7")), Fraction(1), "g", HALF_SPOON)[0]
    assert trap["per_liter"] == 1.05  # 1.5 x 0.7 is 1.05 exactly, never 1.04 from floating point
    assert feeding.spoons(Fraction(3, 4), None) is None
    assert feeding.floor_to(Fraction(7, 4), HALF_SPOON) == Fraction(3, 2)
    assert feeding.amount_unit({"unit": "g/l"}) == "g" and feeding.amount_unit({"unit": "ml/10l"}) is None
    # the amounts of the report: flowering week 2, factor 0.5, 1 l and 2.5 l
    week2 = doses(vivo_page(), ["base_a", "base_b"], 3, HALF_SPOON)
    one, two_half = totals(week2, Fraction(1), "g", HALF_SPOON), totals(week2, Fraction(5, 2), "g", HALF_SPOON)
    assert [(r["total"], r["spoons_total"]) for r in one] == [(0.75, 1.5), (0.75, 1.5)]
    assert [(r["total"], r["spoons_total"]) for r in two_half] == [(1.87, 3.5), (1.87, 3.5)]


def test_ml_lines_and_the_biobizz_card_are_unchanged(on_day) -> None:  # type: ignore[no-untyped-def]
    assert feeding.fingerprint(setup(), card()) == BIOBIZZ_FINGERPRINT  # no silent re-confirmation after the build
    confirm(planned())
    shown = pview(planned(), "de", liters="1,5")
    assert [(d["component"], d["per_liter"]) for d in shown["doses"]] == [
        ("bio_grow", 1.5), ("bio_bloom", 1.0), ("top_max", 0.5)]
    assert "spoons_per_liter" not in shown["doses"][0]
    assert (shown["amount_unit"], shown["amount_digits"], shown["plan"]["unit"], shown["measure"]) == ("ml", 1, "ml",
                                                                                                        None)
    old = {"time": "2026-10-21T09:00:00", "card": "biobizz-2026", "page": "light_mix", "week": "bloom_1",
           "bloom_week": 1, "liters": 1.5, "state": "ok",
           "items": [{"component": "bio_grow", "chart": 3, "per_liter": 1.5, "total": 2.2}]}  # a line from before
    feeding.config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    feeding.log_path().write_text(json.dumps(old) + "\n", encoding="utf-8")
    last = pview(planned(), "de")["last_fed"]
    assert (last["unit"], last["digits"], last["other_card"]) == ("ml", 1, False)
    assert last["items"] == [{"component": "bio_grow", "label": "Bio-Grow", "per_liter": 1.5, "total": 2.2}]
    seen_from_vivo = pview(vplanned(), "de")["last_fed"]  # VIVOSUN active: the BioBizz line stays ml, its own labels
    assert (seen_from_vivo["unit"], seen_from_vivo["digits"], seen_from_vivo["other_card"]) == ("ml", 1, True)
    assert seen_from_vivo["card_name"].startswith("BioBizz") and seen_from_vivo["items"][0]["label"] == "Bio-Grow"


def test_a_g_line_stays_g_with_two_decimals_while_biobizz_is_active(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(vplanned())
    entry = feeding.log_feeding(vplanned(), "1,5")
    assert entry["unit"] == "g" and entry["digits"] == 2
    assert [(i["component"], i["per_liter"], i["total"], i["spoons_total"]) for i in entry["items"]] == [
        ("base_a", 0.75, 1.12, 2.0), ("base_b", 0.75, 1.12, 2.0)]
    line = json.loads(feeding.log_path().read_text(encoding="utf-8").splitlines()[-1])
    assert (line["unit"], line["setup"], line["card"]) == ("g", "vivosun", "vivosun-base-ab")
    last = pview(planned(), "de")["last_fed"]
    assert (last["unit"], last["digits"], last["other_card"], last["items"][0]["total"]) == ("g", 2, True, 1.12)


def test_the_label_ends_after_week_8(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(vplanned())
    on_day(BLOOM_START + timedelta(days=55))  # flowering week 8: 1 g/l x 0.5
    assert [d["per_liter"] for d in pview(vplanned(), "de")["doses"]] == [0.5, 0.5]
    for offset in (56, 63, 120):
        on_day(BLOOM_START + timedelta(days=offset))
        shown = pview(vplanned(), "de", liters=1)
        assert (shown["state"], shown["doses"], shown["plan"]) == ("label_ends", [], None), offset
        assert shown["week"]["key"] == "label_end" and shown["week"]["label"].startswith("Ab Blütewoche 9")
    assert [a["key"] for a in shown["after_bloom"]] == ["label_end"]
    assert all(c["status"] == "past" for c in shown["timeline"]["columns"]) and shown["timeline"]["next_change"] is None
    with pytest.raises(FeedingRefused, match="Ab Blütewoche 9 nennt die Tabelle keine Menge mehr"):
        feeding.log_feeding(vplanned(), 1)
    assert not feeding.log_path().exists()
    assert pview(vplanned(), "en")["week"]["label"] == "From flowering week 9: the table ends, no amount"


def test_seedling_uses_the_propagation_column_when_the_card_has_one(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(vplanned())
    seedling, owned = manual("seedling")
    shown = pview(vplanned(), "de", stage=seedling, owner_stages=owned)
    assert shown["week"]["key"] == "propagation"
    assert [(d["component"], d["per_liter"], d["spoons_per_liter"]) for d in shown["doses"]] == [
        ("base_a", 0.25, 0.5), ("base_b", 0.25, 0.5)]  # 0.5 x 0.5 = half a spoon, never the vegetative 1 g/l
    veg, owned_veg = manual("veg")
    assert [d["per_liter"] for d in pview(vplanned(), "de", stage=veg, owner_stages=owned_veg)["doses"]] == [0.5, 0.5]
    confirm(planned())
    bio = pview(planned(), "de", stage=seedling, owner_stages=owned)  # BioBizz: propagation Bio-Grow 2 = vegetative 2
    assert [(d["component"], d["per_liter"]) for d in bio["doses"]] == [("bio_grow", 1.0)]


def test_two_setups_need_a_choice(on_day) -> None:  # type: ignore[no-untyped-def]
    profile = load_profile()
    pending = feeding.setup_of(profile, None)
    assert pending["choice_pending"] is True and pending["setup"] == "biobizz"
    shown = pview(pending, "de", setups=feeding.setups_of(profile))
    assert (shown["state"], shown["doses"], shown["timeline"], shown["setup"]) == ("needs_choice", None, None, None)
    # plant.json's two first, then every selectable card of kartei/nutrients/ (from_library, 23.10.2026)
    assert shown["choice"]["needed"] is True and [o["id"] for o in shown["choice"]["options"]] == ALL_SETUPS
    assert [o["unit"] for o in shown["choice"]["options"]] == ["ml", "g", "ml", "ml", "ml", "ml", "ml"]
    with pytest.raises(FeedingRefused, match="Erst den Dünger wählen"):
        feeding.log_feeding(pending, 1)
    with pytest.raises(FeedingRefused, match="Erst den Dünger wählen"):
        feeding.confirm(pending, "biobizz-2026", feeding.fingerprint(pending, card()))
    log = choose("vivosun")
    chosen = feeding.setup_of(profile, log)
    assert chosen["card"] == "vivosun-base-ab" and "choice_pending" not in chosen
    assert pview(chosen, "de")["state"] == "draft"
    log.data["feeding_choice"] = {"setup": "no-longer-there", "set_at": "x"}  # a stale id: no choice, no crash
    assert feeding.setup_of(profile, log)["choice_pending"] is True
    flat = {**profile, "feeding": setup()}  # the flat layout: one setup, as before, no choice needed
    assert feeding.setup_of(flat, None) is flat["feeding"] and list(feeding.setups_of(flat)) == ["default"]
    assert pview(feeding.setup_of(flat, None), "de")["choice"]["needed"] is False


def test_the_choice_lives_in_the_plant_log_not_in_the_repo() -> None:
    before = (REPO / "plant.json").read_bytes()
    log = choose("vivosun")
    stored = json.loads((feeding.config.STATE_DIR / "plant-log.json").read_text(encoding="utf-8"))
    assert stored["feeding_choice"]["setup"] == "vivosun" and stored["feeding_choice"]["set_at"]
    assert (REPO / "plant.json").read_bytes() == before
    with pytest.raises(UserError, match="Diesen Dünger gibt es in plant.json nicht"):
        log.set_feeding_choice("hydro", ["biobizz", "vivosun"])
    assert feeding.setup_of(load_profile())["card"] == "vivosun-base-ab"  # without a log: the state folder's


def test_confirmations_are_kept_per_card(on_day, tmp_path) -> None:  # type: ignore[no-untyped-def]
    confirm(planned())
    confirm(vplanned())
    assert pview(planned(), "de")["state"] == "ok" and pview(vplanned(), "de")["state"] == "ok"  # both stay
    saved = json.loads(feeding.confirmation_path().read_text(encoding="utf-8"))
    assert set(saved["cards"]) == {"biobizz-2026", "vivosun-base-ab"}
    legacy = saved["cards"]["biobizz-2026"]  # a flat file from before setups still confirms its card
    feeding.confirmation_path().write_text(json.dumps(legacy), encoding="utf-8")
    assert pview(planned(), "de")["state"] == "ok" and pview(vplanned(), "de")["state"] == "draft"
    confirm(vplanned())  # merging keeps the legacy card
    assert pview(planned(), "de")["state"] == "ok" and pview(vplanned(), "de")["state"] == "ok"
    library = tmp_path / "kartei"
    (library / "nutrients").mkdir(parents=True)
    for path in (CARD_FILE, VIVO_CARD):
        (library / "nutrients" / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads(VIVO_CARD.read_text(encoding="utf-8"))
    data["measure"]["grams"] = 1  # another spoon size: only this card is a draft again
    (library / "nutrients" / VIVO_CARD.name).write_text(json.dumps(data), encoding="utf-8")
    again = pview(vplanned(), "de", library=library)
    assert (again["state"], again["reconfirm"]) == ("draft", True)
    assert pview(planned(), "de", library=library)["state"] == "ok"


def test_a_switch_never_carries_amounts_across(on_day) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        log = PlantLog()
        log.set_stage("early_flower", BLOOM_START.isoformat())
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), "k", plant_log=log))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})

            async def cmd(body: dict) -> tuple[int, dict]:  # type: ignore[type-arg]
                resp = await client.post("/api/cmd", json=body)
                return resp.status, await resp.json()

            async def fed() -> dict:  # type: ignore[type-arg]
                return (await (await client.get("/api/state")).json())["plant"]["feeding"]

            assert (await cmd({"action": "feeding_setup", "setup": "biobizz"}))[0] == 200
            bio = await fed()
            assert (await cmd({"action": "feeding_confirm", "card": bio["card"]["id"],
                               "fingerprint": bio["fingerprint"]}))[0] == 200
            bio = await fed()  # the tab that still shows BioBizz in ml
            assert bio["state"] == "ok" and bio["amount_unit"] == "ml"
            assert (await cmd({"action": "feeding_setup", "setup": "hydro"}))[0] == 400
            assert (await cmd({"action": "feeding_setup", "setup": "vivosun"}))[0] == 200
            stale = {"action": "feeding_log", "liters": "1,5", "card": bio["card"]["id"],
                     "fingerprint": bio["fingerprint"]}
            status, res = await cmd(stale)  # a stale tab never logs with the other card (critique, must 3)
            assert status == 409 and "VIVOSUN Nutrients" in res["error"] and not feeding.log_path().exists()
            vivo_view = await fed()
            assert (vivo_view["state"], vivo_view["card"]["id"], vivo_view["doses"]) == ("draft", "vivosun-base-ab",
                                                                                         None)
            status, _ = await cmd({"action": "feeding_confirm", "card": "vivosun-base-ab",
                                   "fingerprint": vivo_view["fingerprint"]})
            assert status == 200
            vivo_view = await fed()
            can = await (await client.get("/api/feeding?liters=1,5")).json()
            assert [(i["component"], i["total"], i["spoons_total"]) for i in can["plan"]["items"]] == [
                ("base_a", 1.12, 2.0), ("base_b", 1.12, 2.0)]
            assert can["plan"]["unit"] == "g" and can["amount_digits"] == 2
            wrong = {"action": "feeding_log", "liters": "1,5", "card": "vivosun-base-ab", "fingerprint": "0" * 64}
            assert (await cmd(wrong))[0] == 409  # numbers that changed since the page showed them
            status, _ = await cmd({"action": "feeding_log", "liters": "1,5", "card": "vivosun-base-ab",
                                   "fingerprint": vivo_view["fingerprint"]})
            assert status == 200
            line = json.loads(feeding.log_path().read_text(encoding="utf-8").splitlines()[-1])
            assert line["unit"] == "g" and {i["component"] for i in line["items"]} == {"base_a", "base_b"}
            assert (await cmd({"action": "feeding_setup", "setup": "biobizz"}))[0] == 200  # back: still confirmed
            back = await fed()
            assert back["state"] == "ok" and back["last_fed"]["unit"] == "g"  # the g line keeps its unit

    asyncio.run(run())


def test_only_one_card_doses(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(vplanned())
    shown = pview(vplanned(), "de", liters=2)
    assert {d["component"] for d in shown["doses"]} == {"base_a", "base_b"}
    assert {i["component"] for i in shown["plan"]["items"]} == {"base_a", "base_b"}
    assert {c["component"] for c in shown["chart"]} == {"base_a", "base_b"}
    assert shown["choice"]["why"].startswith("Es gilt immer nur ein Dünger")
    assert pview(vplanned(), "en")["choice"]["why"].startswith("Only one fertilizer applies at a time")


def test_timeline_dates_and_plant_weeks(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(vplanned())
    on_day(date(2026, 10, 23))
    line = pview(vplanned(), "de", age={"start": "2026-09-18"})["timeline"]
    assert line["dates"] == "exact" and line["age"] == {"days": 35, "weeks": 5, "extra_days": 0, "life_week": 6}
    now = next(c for c in line["columns"] if c["status"] == "now")
    assert (now["key"], now["from"], now["to"]) == ("bloom_1", "2026-10-22", "2026-10-28")
    assert [(v["component"], v["per_liter"], v["spoons"]) for v in now["values"]] == [
        ("base_a", 0.75, 1.5), ("base_b", 0.75, 1.5)]
    later = [c["from"] for c in line["columns"] if c["status"] == "later"]
    assert later == ["2026-10-29", "2026-11-05", "2026-11-12", "2026-11-19", "2026-11-26", "2026-12-03",
                     "2026-12-10"]
    past = [c for c in line["columns"] if c["status"] == "past"]
    assert [c["key"] for c in past] == ["propagation", "veg"] and all(c["from"] is None for c in past)
    change = line["next_change"]
    assert (change["date"], change["key"], change["life_week"], change["latest"]) == ("2026-11-19", "bloom_5", 9,
                                                                                     False)
    assert [(c["component"], c["from"], c["to"], c["spoons_to"]) for c in change["changes"]] == [
        ("base_a", 0.75, 0.5, 1.0), ("base_b", 0.75, 0.5, 1.0)]
    assert line["tail"] == {"key": "label_end", "label": "Ab Blütewoche 9: die Tabelle endet, keine Menge",
                            "bloom_week": 9, "from": "2026-12-17", "life_week": 13}
    on_day(date(2026, 11, 20))  # in weeks 5 to 8 the next change is the table's end
    tail = pview(vplanned(), "de")["timeline"]["next_change"]
    assert (tail["date"], tail["tail"], tail["changes"]) == ("2026-12-17", True, [])


def test_timeline_dates_are_latest_when_only_estimated(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(vivo())
    shown = pview(vivo(), "de", age={"start": "2026-09-10"})  # the camera's estimate: an upper bound
    line = shown["timeline"]
    assert shown["state"] == "needs_stage" and line["dates"] == "latest"
    assert line["next_change"]["latest"] is True and line["next_change"]["basis"] == "chart"
    assert [(c["from"], c["to"]) for c in line["next_change"]["changes"]] == [(1.5, 1), (1.5, 1)]  # label values
    assert all(v["per_liter"] is None and v["spoons"] is None for c in line["columns"] for v in c["values"])


def test_timeline_without_a_bloom_start_or_start_date(on_day) -> None:  # type: ignore[no-untyped-def]
    veg, owned = manual("veg")
    confirm(vivo())
    line = pview(vivo(), "de", stage=veg, owner_stages=owned)["timeline"]
    assert line["dates"] == "none" and line["age"] is None
    assert all(c["from"] is None and c["life_week"] is None for c in line["columns"])
    change = line["next_change"]
    assert (change["date"], change["key"], change["when"]) == (None, "bloom_1", "bloom_start")
    seedling, owned = manual("seedling")
    first = pview(vivo(), "de", stage=seedling, owner_stages=owned)["timeline"]["next_change"]
    assert (first["key"], first["when"]) == ("veg", "stage")


def test_age_never_changes_a_dose(on_day) -> None:  # type: ignore[no-untyped-def]
    confirm(vplanned())
    early = pview(vplanned(), "de", age={"start": "2026-09-10"})
    late = pview(vplanned(), "de", age={"start": "2026-09-18"})
    for key in ("state", "week", "doses"):
        assert early[key] == late[key], key
    assert [c["from"] for c in early["timeline"]["columns"]] == [c["from"] for c in late["timeline"]["columns"]]
    assert early["timeline"]["age"]["life_week"] == 7 and late["timeline"]["age"]["life_week"] == 5


def test_fingerprint_covers_measure_unit_and_end() -> None:
    data = json.loads(VIVO_CARD.read_text(encoding="utf-8"))
    base = feeding.fingerprint(vivo(), data)
    for change in (lambda d: d["measure"].__setitem__("grams", 1), lambda d: d.__setitem__("unit", "ml/l"),
                   lambda d: d["weeks"]["end"].__setitem__("bloom_week", 10),
                   lambda d: d["pages"]["label"]["rows"]["base_b"].__setitem__(2, 2)):
        other = json.loads(json.dumps(data))
        change(other)
        assert feeding.fingerprint(vivo(), other) != base
    label = json.loads(json.dumps(data))
    label["measure"]["text"] = "anders"  # a wording is not a number: the confirmation stays
    assert feeding.fingerprint(vivo(), label) == base


# ---------- review of part 1 (23.10.2026): what Hank's part-1 code must not see, the week note, a broken file ----------

def test_a_caller_that_knows_only_ml_gets_no_vivosun_number(on_day) -> None:  # type: ignore[no-untyped-def]
    """Review M1: Hank's part-1 code writes "ml" after every dose. Called as its wiring calls view() (no
    amount_units), a g card is withheld: no chart value, dose, plan or timeline, only where the amounts are."""
    from vgrow.hank import today as hank_today
    from vgrow.hank.livestate import LiveState
    confirm(vplanned())
    page = pview(vplanned(), "de", liters=2)
    assert page["state"] == "ok" and page["withheld"] is None and page["doses"]
    legacy = view(vplanned(), "de", liters=2)
    assert (legacy["state"], legacy["withheld"], legacy["doses"], legacy["plan"]) == ("no_card", "g", None, None)
    assert legacy["chart"] == [] and legacy["timeline"] is None and legacy["notes"] == [] and legacy["sources"] == {}
    assert legacy["week"] is None and legacy["fingerprint"] is None and legacy["measure"] is None
    assert "Cockpit-Seite" in legacy["problem"] and "VIVOSUN" in legacy["problem"]
    en = view(vplanned(), "en")
    assert "cockpit page" in en["problem"] and "grams and spoons" in en["problem"]
    assert hank_today._feeding(LiveState(feeding_view=legacy), "de") is None  # never "Base A 0,75 ml"
    confirm(planned())
    bio = view(planned(), "de", liters=2)  # an ml card reaches every caller as before
    assert bio["state"] == "ok" and bio["withheld"] is None and bio["doses"]


def test_the_vegetative_column_says_when_the_plant_is_older_than_its_label_weeks(on_day) -> None:  # type: ignore[no-untyped-def]
    """Review S8: "Wachstum (Woche 1 bis 4)" next to plant week 7 needs the sentence that the label counts only
    those weeks; the column and the dose stay."""
    veg, owned = manual("veg")
    confirm(vivo())
    old = pview(vivo(), "de", stage=veg, owner_stages=owned, age={"start": "2026-09-01", "life_week": 7})
    young = pview(vivo(), "de", stage=veg, owner_stages=owned, age={"start": "2026-10-01", "life_week": 3})
    assert old["week"]["key"] == young["week"]["key"] == "veg" and old["doses"] == young["doses"]
    assert old["week"]["note"] == ("Das Etikett nennt für diese Phase nur Woche 1 bis 4, die Pflanze ist älter. Das "
                                   "Cockpit bleibt bei dieser Spalte, bis du eine andere Phase setzt")  # the plant
    # week itself stands next to it on the page already
    assert young["week"]["note"] is None and pview(vivo(), "de", stage=veg, owner_stages=owned)["week"]["note"] is None
    en = pview(vivo(), "en", stage=veg, owner_stages=owned, age={"life_week": 7})
    assert en["week"]["note"].startswith("The label names only weeks 1 to 4 for this stage")
    seedling, owned = manual("seedling")
    sprout = pview(vivo(), "de", stage=seedling, owner_stages=owned, age={"life_week": 3})
    assert sprout["week"]["key"] == "propagation" and "Woche 1 bis 2" in sprout["week"]["note"]
    flowering = pview(vplanned(), "de", age={"life_week": 12})
    assert flowering["week"]["phase"] == "bloom" and flowering["week"]["note"] is None
    veg, owned = manual("veg")
    bio = view(planned(), "de", stage=veg, owner_stages=owned, age={"life_week": 12})  # no label_weeks: no note
    assert bio["week"]["note"] is None


def test_a_broken_confirmation_file_is_never_overwritten(on_day) -> None:  # type: ignore[no-untyped-def]
    """Review N1: confirming one card over an unreadable feeding.json would drop the other card's record."""
    feeding.confirmation_path().parent.mkdir(parents=True, exist_ok=True)
    feeding.confirmation_path().write_text('{"cards": {"biobizz-2026": ', encoding="utf-8")
    with pytest.raises(FeedingRefused, match="nicht lesbar"):
        confirm(vplanned())
    assert feeding.confirmation_path().read_text(encoding="utf-8") == '{"cards": {"biobizz-2026": '
    assert pview(vplanned(), "de")["state"] == "draft"  # reading it still counts as no confirmation, no crash


def test_hanks_index_finds_the_run_page_in_the_setups_layout() -> None:
    """Review S2: Hank's index reads the run through feeding.setups_of (part 2 of feeding v2)."""
    from vgrow.hank.kartei_index import Index
    pages = {s.get("page") for s in feeding.setups_of(load_profile()).values()}
    assert Index.load()._state.run_page in pages
