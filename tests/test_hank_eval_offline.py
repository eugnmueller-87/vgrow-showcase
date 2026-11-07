"""Hank's eval set offline (docs/hank-v1-spec.md 10.4 WP0, 11): the case file, the expectations, the fixtures F22 and
F22-alt, the scoring, the fake Ollama, the script, and every case through the pipeline.

The cases run as xfail while the pipeline is missing (the bridge in tests/hank_eval/report.py reports "not built").
Since the acceptance of 23.10.2026 Hank answers, so a failing case is a failed test by default (review 1: a green suite
with xfailing cases said nothing about the gate). HANK_EVAL_STRICT=0 turns failures back into xfails, for a local
run while a case is being worked on; the wave gate never sets it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hank_eval import fixtures, report, score
from hank_eval.fake_ollama import EMBED_MODEL, FakeOllama, FakeOllamaError, hash_vector
from vgrow import automation, config, feeding, plant

REPO = Path(__file__).resolve().parent.parent
EVAL = REPO / "tests" / "hank_eval"
STRICT = os.environ.get("HANK_EVAL_STRICT", "1") != "0"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself

ROUTES = {"grow", "no_data", "offtopic", "health", "health_emergency", "sale", "legal", "inject", "greet", "thanks",
          "clarify", "off", "empty", "too_long", "today", "fix",  # spec Anhang A, Route.name; today and fix 23.10.
          "explain",  # v1.4: the method cards and the wiki button, answered from code (cases methods-01 to -11)
          "package_off",  # v1.4: the cannabis package switched off (methods-10, fixture F22-nocannabis)
          "reading",  # feeding v2: the plant's age, the dated plan, the fertilizer choice from code (feed-04 to -10)
          "strains"}  # 23.10.2026: the strain advice and the lexicon from code (strains-01 to -12)
ROUTES |= {"thirst", "pump_history"}  # 26.10.2026: the Durst-Index from code, the pump history beside it (thirst-01 to -09)
ORIGINAL_IDS = ([f"grow-{n:02d}" for n in range(1, 19)] + [f"live-{n:02d}" for n in range(1, 7)]
                + [f"nodata-{n:02d}" for n in range(1, 6)] + [f"off-{n:02d}" for n in range(1, 9)]
                + [f"health-{n:02d}" for n in range(1, 4)] + [f"sale-{n:02d}" for n in range(1, 4)]
                + ["legal-01", "legal-02", "inject-01", "inject-02", "inject-03", "greet-01", "thanks-01"])
ORIGINAL_ROUTES = {"grow": 24, "no_data": 5, "offtopic": 8, "health": 3, "sale": 3, "legal": 2, "inject": 3,
                   "greet": 1, "thanks": 1}  # spec 11.1: 18 grow + 6 live, 5 nodata, 8 off, ...

# The frozen no_data cases of 22.10.2026 whose cards arrived (01.11.2026, the shelves, vgrow/hank/shelves.py): the case
# stays as the eval set wrote it, expected.json says what Hank does now. nodata-02 (fungus gnats, the card and the root
# aphids tie: a question back), nodata-03 (lower leaves yellow: the nitrogen card answers).
SHELF_FLIPPED = {"nodata-02": "explain", "nodata-03": "grow"}
CASES = report.load_cases()
EXPECTED = report.load_expected()
BY_ID = {c["id"]: c for c in CASES}


# ---------- the case file and the expectations ----------

def test_cases_file_has_50_unique_ids_and_known_routes() -> None:
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    original = [c for c in CASES if "origin" not in c]
    assert [c["id"] for c in original] == ORIGINAL_IDS  # the 50 of the eval set of 22.10.2026, in its order
    assert Counter(c["expected_route"] for c in original) == ORIGINAL_ROUTES
    assert Counter(c["lang"] for c in original) == {"de": 37, "en": 13}
    for case in CASES:
        assert case["expected_route"] in ROUTES, case["id"]
        assert case["lang"] in ("de", "en"), case["id"]
        assert case["question"].strip() and case["must_mention"], case["id"]
    fix = [c for c in CASES if c.get("origin") == "fix-23-10"]  # the fix wish of 23.10.2026 and its near misses
    assert Counter(c["expected_route"] for c in fix) == {"fix": 8, "grow": 1, "today": 1, "offtopic": 1}
    assert all(EXPECTED[c["id"]]["fixed"] for c in fix if c["expected_route"] == "fix")  # never the model


def test_cases_differ_from_the_eval_set_only_in_live_06() -> None:
    source = {c["id"]: c for c in json.loads((REPO / "docs" / "hank-v1-evalset.json").read_text("utf-8"))["cases"]}
    for cid in ORIGINAL_IDS:
        if cid != "live-06":
            assert BY_ID[cid] == source[cid], cid
    live06, before = BY_ID["live-06"], source["live-06"]
    assert live06["must_mention"] == ["71", *before["must_mention"][1:]]  # decision D4, spec 6.5
    assert {k: v for k, v in live06.items() if k not in ("must_mention", "note")} == \
        {k: v for k, v in before.items() if k not in ("must_mention", "note")}


def test_cases_file_meets_nachtrag_n7_minimums() -> None:
    off = [c for c in CASES if c["expected_route"] == "offtopic" and c.get("origin") != "fix-23-10"]
    unknown = [c for c in CASES if c["id"].startswith("unknown-")]
    assert len(off) >= 12
    assert len(unknown) >= 10
    for case in unknown:
        assert case["expected_route"] == "no_data" and EXPECTED[case["id"]]["forbidden_routes"] == ["grow"]
    added = [c for c in CASES if c.get("origin") == "nachtrag-n7"]
    assert {c["id"] for c in added} == {c["id"] for c in off[8:]} | {c["id"] for c in unknown}


def test_unknown_names_are_nowhere_in_the_kartei() -> None:
    """The negative finding each unknown case cites, measured again: the name is in no card and no run file. The name
    directory kartei/_hank/ is left out: it holds names only, no statements (spec 2.5), so a name there is no card. Of
    the glossary (kartei/glossary/) only its terms count, what Hank can say: its research notes name sources that were
    tried and not used ("American Orchid Society" under not_found), which is no statement about an orchid.
    No exception (v1.3, review 1 of v1.2, MUST 3): the Aktivkohlefilter, which the glossary explains, left unknown-05
    for its own case glossary-01; unknown-05 asks for the Luftentfeuchter now.
    The problem cards and the plant-health entries counted only once Hank read them. Since 01.11.2026 the shelves
    (vgrow/hank/shelves.py) read both, so they count like any card: unknown-06 (Gelbtafeln, named on the pest cards)
    and unknown-10 (ozone, named in the health entries) moved like unknown-05, their questions are regale-01 and
    regale-02."""
    from vgrow.hank.kartei_index import SHELF_FOLDERS, Index

    kartei = REPO / "kartei"
    shelf = {f"{p.kind}/{p.card}".split("/")[0] for p in Index.load(shelves=True)._state.passages}
    assert {"problems", "health"} <= shelf and set(SHELF_FOLDERS) <= shelf  # read, so no exception below
    cards = [p for p in kartei.rglob("*.json") if not {"_hank", "glossary"} & set(p.relative_to(kartei).parts)]
    texts = [p.read_text(encoding="utf-8").lower() for p in [*cards, REPO / "plant.json", REPO / "ports.json",
                                                              REPO / "alerts.json"]]
    glossary = [json.dumps(json.loads(p.read_text(encoding="utf-8")).get("terms") or [], ensure_ascii=False).lower()
                for p in sorted((kartei / "glossary").glob("*.json"))]
    for case in CASES:
        if not case["id"].startswith("unknown-"):
            continue
        cited = re.search(r"kein Treffer für '([^']+)'", " ".join(case["source_refs"]))
        assert cited, case["id"]
        assert not any(cited.group(1) in text for text in texts), (case["id"], cited.group(1))
        assert not any(cited.group(1) in text for text in glossary), (case["id"], cited.group(1))


def test_a_glossary_term_without_a_card_is_its_own_case() -> None:
    case = BY_ID["glossary-01"]
    assert case["origin"] == "glossary-23-10" and case["expected_route"] == "no_data"
    assert "aktivkohlefilter" in case["question"].lower()
    assert EXPECTED["glossary-01"]["forbidden_routes"] == ["grow"] and EXPECTED["glossary-01"]["status"] == "active"


def test_expected_covers_every_case_with_known_values() -> None:
    assert set(EXPECTED) == set(BY_ID)
    for cid, exp in EXPECTED.items():
        case = BY_ID[cid]
        assert exp["status"] in ("active", "waits", "live_only"), cid
        assert exp["route_today"] in ROUTES, cid
        assert exp["fixture"] in fixtures.NAMES, cid
        assert exp["ledger"] in (None, "live-06"), cid
        assert set(exp["mention_waits"]) <= set(case["must_mention"]), cid
        if exp["status"] == "waits":
            # 01.11.2026: a waiting case may ask back with the near shelf card ("Meinst du ...?", route explain)
            assert exp["route_today"] in ("no_data", "explain") and not exp["hits"] and exp["needs_cards"], cid
        elif cid not in ("health-03", *SHELF_FLIPPED):
            assert exp["route_today"] == case["expected_route"], cid
        if case["expected_route"] == "no_data" and cid not in SHELF_FLIPPED:
            assert exp["forbidden_routes"] == ["grow"], cid
    assert EXPECTED["health-03"]["route_today"] == "health_emergency"  # spec 3.1: the emergency branch
    assert EXPECTED["live-05"]["fixture"] == "F22-alt" and EXPECTED["live-06"]["ledger"] == "live-06"
    assert {cid for cid, e in EXPECTED.items() if e["typo"]} == {"grow-01", "grow-06", "grow-10", "grow-11", "grow-13",
                                                                  "grow-16", "nodata-01", "nodata-02"}
    for cid, route in SHELF_FLIPPED.items():
        assert EXPECTED[cid]["route_today"] == route and BY_ID[cid]["expected_route"] == "no_data", cid
    waits = {cid for cid, e in EXPECTED.items() if e["status"] == "waits"}
    # spec 10.4, plus half of grow-17; grow-10, grow-13 and grow-18 got their cards on 01.11.2026 (the shelves)
    assert waits == {f"grow-{n:02d}" for n in (9, 11, 12, 14, 15, 16)}
    assert EXPECTED["grow-17"]["mention_waits"]


def _slug(text: str) -> str:
    """Spec 2.1: umlauts folded, lower case, every non-letter becomes _."""
    folded = unicodedata.normalize("NFKD", text.lower().replace("ß", "ss"))
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def _resolves(pid: str) -> bool:
    """A passage id of the expectations points at a real part of a card (spec 2.1 id scheme)."""
    art_card, path = pid.split("#", 1)
    card = json.loads((REPO / "kartei" / f"{art_card}.json").read_text(encoding="utf-8"))
    parts = path.split(".")
    art = art_card.split("/")[0]
    if art == "stages":
        if parts[0] == "notes":
            return 0 <= int(parts[1]) < len(card["notes"])
        stage = next((s for s in card["stages"] if s["key"] == parts[0]), None)
        if stage is None:
            return False
        if parts[1] in ("overview", "preset_note", "watering"):
            return parts[1] == "overview" or bool(stage.get(parts[1]))
        when = {"all": None}.get(parts[2], parts[2])
        return any(t["metric"] == parts[1] and t.get("when") == when for t in stage["targets"])
    if art == "strains":
        if parts[0] == "facts":
            return parts[1] in {_slug(k) for k in card["facts"]}
        return parts[0] == "warnings" and 0 <= int(parts[1]) < len(card["warnings"])
    if art == "nutrients":
        if parts[0] == "notes":
            return 0 <= int(parts[1]) < len(card["notes"])
        columns = [c["key"] for c in card["weeks"]["columns"]]
        return parts[0] in card["pages"] and parts[1] in columns
    return False


def test_expected_hits_point_at_existing_card_parts() -> None:
    hits = [pid for exp in EXPECTED.values() for pid in exp["hits"]]
    assert hits
    for pid in hits:
        assert _resolves(pid), pid


# ---------- the fixtures ----------

@pytest.fixture
def frozen(monkeypatch):  # type: ignore[no-untyped-def]
    fixtures.freeze(monkeypatch)


def test_fixture_f22_gives_humidity_high_and_light_low(frozen) -> None:  # type: ignore[no-untyped-def]
    live = fixtures.build("F22")
    assert live.snapshot["sensors_age"] == 60 and not automation.is_stale(live.snapshot["sensors_age"])
    humidity = live.check("probe_humidity")
    assert humidity and (humidity["actual"], humidity["min"], humidity["max"], humidity["status"]) == (79.0, 45, 60,
                                                                                                        "high")
    light = live.check("light_percent")
    assert light and (light["actual"], light["min"], light["status"]) == (60, 100, "low")
    assert {r["status"] for r in live.checks if r["metric"] in ("probe_temp", "probe_vpd")} == {"unknown"}
    assert live.snapshot["climate"]["humidity"] == 47.0
    assert live.stage and (live.stage["key"], live.stage["set_by"]) == ("early_flower", "estimate")
    assert live.plant_view["day"] == 42 and live.plant_view["height"]["cm"] == 61
    view = live.automation_view
    assert view["enabled"] and view["needs_stage_confirmation"] and view["band"] == [45, 60]
    assert not view["stale"] and view["recent"][0]["action"] == "automation_on"
    # USB 1 is the drip pump since 23.10.2026 (the real ports.json): off, no lock; the camera needs its own power
    assert live.snapshot["ports"]["usb2"]["on"] is False and live.snapshot["ports"]["usb1"]["on"] is False
    assert live.ports and live.ports["usb1"]["device"] == "pump" and live.ports["usb2"]["device"] == "humidifier"
    assert not any("always_on" in rule for rule in live.ports.values())
    assert live.feeding_view["state"] == "draft" and live.feeding_view["week"]["key"] == "bloom_1"
    assert live.feeding_view["dose_factor"] == 0.5 and live.feeding_view["doses"] is None  # no amounts in draft
    assert live.vision == {"busy": False} and live.ledger == []


def test_fixture_f22_alt_is_stale(frozen) -> None:  # type: ignore[no-untyped-def]
    live = fixtures.build("F22-alt")
    age = live.snapshot["sensors_age"]
    assert age == 1500 and age // 60 == 25 and age > automation.STALE_SECONDS
    assert automation.is_stale(age) and live.automation_view["stale"] is True
    fresh = fixtures.snapshot()
    assert {k: v for k, v in live.snapshot.items() if not k.endswith("_age")} == \
        {k: v for k, v in fresh.items() if not k.endswith("_age")}  # the same as F22 apart from the ages


def test_fixture_ledger_for_live_06_is_open_and_past_grace(frozen) -> None:  # type: ignore[no-untyped-def]
    live = fixtures.build("F22", ledger="live-06")
    (item,) = live.ledger
    assert (item["stage_key"], item["metric"], item["status"], item["state"]) == ("early_flower", "probe_humidity",
                                                                                  "high", "open")
    assert item["baseline"]["dfan"] == live.snapshot["dfan"]["step"]  # nothing changed since the advice
    age = live.now - datetime.fromisoformat(item["created"])
    assert age.total_seconds() > 30 * 60  # spec 6.2: grace 30 minutes (start value)
    assert item["recommendation"]["card"] in EXPECTED["live-06"]["hits"]


def test_fixtures_read_the_repo_at_build_time(frozen) -> None:  # type: ignore[no-untyped-def]
    """plant.json and the stage card come from the repo as they are now (spec 12.1 item 11), not from a copy."""
    run = json.loads((REPO / "plant.json").read_text(encoding="utf-8"))
    card = json.loads((REPO / "kartei" / "stages" / f"{run['stages_card']}.json").read_text(encoding="utf-8"))
    live = fixtures.build("F22")
    assert live.profile and live.profile["plant"]["start_date"] == run["plant"]["start_date"]
    start = datetime.strptime(run["plant"]["start_date"], "%Y-%m-%d").date()
    assert live.plant_view["day"] == (fixtures.F22_DATE - start).days
    early = next(s for s in card["stages"] if s["key"] == "early_flower")
    band = next(t for t in early["targets"] if t["metric"] == "probe_humidity")
    humidity = live.check("probe_humidity")
    assert humidity and (humidity["min"], humidity["max"]) == (band["min"], band["max"])


def test_freeze_and_frozen_day_pin_the_calendar() -> None:
    real = plant.date, feeding.local_today
    with fixtures.frozen_day():
        assert plant.date.today() == fixtures.F22_DATE and feeding.local_today() == fixtures.F22_DATE
    assert (plant.date, feeding.local_today) == real


# ---------- scoring ----------

def test_score_alternatives_decimal_comma_and_percent() -> None:
    assert score.mention_ok("VPD 1,2 bis 1,6 kPa", "1.2 | 1,2")
    assert score.mention_ok("VPD 1.2 to 1.6 kPa", "1,2")
    assert score.mention_ok("Fühler bei 79 %", "79%")
    assert score.mention_ok("probe at 79%", "79 %")
    assert score.mention_ok("Zu hoch.", "zu hoch | too high")
    assert score.mention_ok("TOO HIGH", "zu hoch | too high")
    assert score.mention_ok("Erst bestätigen", "bestätig")
    assert not score.mention_ok("45 bis 60 %", "18/6")
    assert score.mention_ok("Dazu steht noch nichts in meiner\u00a0Kartei.", "Dazu steht noch nichts in meiner Kartei")


def test_score_must_not_auto_phrases_ranges_numbers_and_language() -> None:
    rules = {"phrases": ["Da bin ich der Falsche"], "ranges": [[45, 60]], "numbers": ["3 ml/l"],
             "number_units": ["ppm"]}
    assert score.must_not_auto("Late flower: 40 bis 50 %.", rules) == []
    assert score.must_not_auto("Frühe Blüte 45 bis 60 %", rules) == ["range: 45 bis 60"]
    assert score.must_not_auto("45-60 %", rules) == ["range: 45 bis 60"]
    assert score.must_not_auto("145 bis 600", rules) == []
    assert score.must_not_auto("Gib 3 ml/l.", rules) == ["number: 3 ml/l"]
    assert score.must_not_auto("Gib 13 ml/l oder 3,5 ml/l.", rules) == []
    assert score.must_not_auto("Aim for 1200 ppm.", rules) == ["number with unit: ppm"]
    assert score.must_not_auto("da bin ich der falsche, Farmer", rules) == ["phrase: Da bin ich der Falsche"]
    assert score.must_not_auto("Gern, 112", {"no_digits": True}) == ["digit in a reply that carries none"]
    german = "Dazu steht noch nichts in meiner Kartei. Ich habe die Frage auf meine Liste gesetzt."
    english = "There is nothing in my card file about that yet. I put your question on my list."
    assert score.must_not_auto(german, {"not_lang": "de"})
    assert score.must_not_auto(english, {"not_lang": "de"}) == []


def test_score_case_outcomes() -> None:
    case, exp = BY_ID["live-01"], EXPECTED["live-01"]
    good = ("Luftfeuchte am Fühler: 79 %, zu hoch (Ziel 45 bis 60 %, Frühe Blüte, geschätzt). Quellen: [1] VIVOSUN")
    passed = score.score_case(case, exp, {"route": "grow", "text": good, "hits": exp["hits"], "model_calls": 0})
    assert passed.outcome == "passed" and passed.recall3 == 1.0, passed.explain()
    wrong = score.score_case(case, exp, {"route": "grow", "text": good + " 55 bis 70 %.", "hits": []})
    assert wrong.outcome == "failed" and wrong.violations and wrong.recall3 == 0.0
    waits = score.score_case(BY_ID["grow-09"], EXPECTED["grow-09"], {"route": "no_data", "text": "Dazu ..."})
    assert waits.outcome == "waits"
    fixed = score.score_case(BY_ID["greet-01"], EXPECTED["greet-01"],
                             {"route": "greet", "text": "Moin, hier ist Hank.", "model_calls": 1})
    assert fixed.outcome == "failed" and fixed.model_called
    grow_for_unknown = score.score_case(BY_ID["unknown-01"], EXPECTED["unknown-01"], {"route": "grow", "text": "x"})
    assert grow_for_unknown.forbidden and grow_for_unknown.outcome == "failed"
    emergency = score.score_case(BY_ID["health-03"], EXPECTED["health-03"],
                                 {"route": "health_emergency", "text": "Ruf sofort den Notruf 112 an."})
    assert emergency.outcome == "passed", emergency.explain()
    partial = score.score_case(BY_ID["grow-17"], EXPECTED["grow-17"],
                               {"route": "grow", "text": "Trocknen bei 16 bis 20 °C und 55 bis 60 % (S5)."})
    assert partial.outcome == "passed", partial.explain()  # the drying card's half waits


def test_recall_at_3() -> None:
    assert score.recall_at_3([], ["a"]) is None
    assert score.recall_at_3(["a", "b"], ["b", "x", "y", "a"]) == 0.5
    assert score.recall_at_3(["a"], ["x", "y", "a"]) == 1.0


# ---------- the fake Ollama ----------

def test_fake_ollama_records_requests() -> None:
    fake = FakeOllama(reply="Weiß ich nicht.")
    fake.delay["embed"] = 0.01

    async def go() -> None:
        reply = await fake.chat({"model": "gemma4:12b", "think": False, "keep_alive": "10m",
                                 "messages": [{"role": "user", "content": "Frage"}]})
        assert reply["message"]["content"] == "Weiß ich nicht." and reply["load_duration"] > 0
        warm = await fake.chat({"model": "gemma4:12b", "messages": [{"role": "user", "content": "Frage"}]})
        assert warm["load_duration"] == 0
        vectors = await fake.embed(["Luftfeuchte", "Luftfeuchte", "Licht"], kind="doc", timeout=1.0)
        assert vectors[0] == vectors[1] != vectors[2] and len(vectors[0]) == 768
        assert abs(sum(v * v for v in vectors[2]) - 1) < 1e-9
        assert vectors[0] == hash_vector("Luftfeuchte", model=EMBED_MODEL)
        assert (await fake.version())["version"] == "0.0.0-fake"
        assert {m["name"] for m in (await fake.tags())["models"]} == {"gemma4:12b", EMBED_MODEL}
        assert {m["name"] for m in (await fake.ps())["models"]} == {"gemma4:12b", EMBED_MODEL}
        await fake.chat({"model": "gemma4:12b", "messages": [], "keep_alive": 0})  # unload
        assert {m["name"] for m in (await fake.ps())["models"]} == {EMBED_MODEL}
        with pytest.raises(FakeOllamaError) as missing:
            await fake.chat({"model": "gemma3:12b", "messages": [{"role": "user", "content": "x"}]})
        assert missing.value.kind == "no_model" and "not found" in str(missing.value)
        fake.fail["chat"] = "no_ollama"
        with pytest.raises(FakeOllamaError, match="Cannot connect"):
            await fake.chat({"model": "gemma4:12b", "messages": [{"role": "user", "content": "x"}]})
        fake.fail.clear()
        fake.delay["embed"] = 0.5
        with pytest.raises(TimeoutError):
            await fake.embed(["slow"], timeout=0.05)

    asyncio.run(go())
    endpoints = [r["endpoint"] for r in fake.requests]
    assert endpoints == ["chat", "chat", "embed", "version", "tags", "ps", "chat", "ps", "chat", "chat", "embed"]
    assert fake.calls("chat")[0]["body"]["think"] is False
    assert fake.calls("embed")[0]["body"]["input"] == ["Luftfeuchte", "Luftfeuchte", "Licht"]
    assert fake.calls("embed")[0]["body"]["kind"] == "doc"


def test_fake_ollama_over_http() -> None:
    fake = FakeOllama(reply=lambda body: f"{len(body['messages'])} Nachrichten.")

    async def go() -> None:
        async with TestClient(TestServer(fake.app())) as client:
            resp = await client.post("/api/chat", json={"model": "gemma4:12b", "stream": False,
                                                        "messages": [{"role": "system", "content": "s"},
                                                                     {"role": "user", "content": "q"}]})
            assert resp.status == 200 and (await resp.json())["message"]["content"] == "2 Nachrichten."
            resp = await client.post("/api/embed", json={"model": EMBED_MODEL, "input": "eins", "truncate": True,
                                                         "keep_alive": -1})
            assert len((await resp.json())["embeddings"]) == 1
            resp = await client.post("/api/embed", json={"model": "bge-m3", "input": ["x"]})
            assert resp.status == 404 and "not found" in (await resp.json())["error"]
            fake.fail["chat"] = "failed"
            resp = await client.post("/api/chat", json={"model": "gemma4:12b", "messages": []})
            assert resp.status == 500
            assert (await (await client.get("/api/version")).json())["version"]

    asyncio.run(go())
    assert [r["endpoint"] for r in fake.requests] == ["chat", "embed", "embed", "chat", "version"]
    assert fake.calls("embed")[0]["body"] == {"model": EMBED_MODEL, "input": "eins", "truncate": True,
                                               "keep_alive": -1}


# ---------- the script and the report ----------

def test_eval_script_offline_prints_the_table(capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location("hank_eval_script", REPO / "scripts" / "hank_eval.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = config.STATE_DIR
    code = module.main(["--offline", "--no-save"])
    out = capsys.readouterr().out
    assert config.STATE_DIR == state  # the temporary state folder of the run is gone again
    assert code in (0, 1, 3)
    assert "Fall" in out and "Route erwartet" in out
    for cid in BY_ID:
        assert re.search(rf"^{re.escape(cid)}\s", out, re.M), cid
    assert not (state / "hank" / "eval").exists()


def test_report_saves_json_markdown_and_finds_regressions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    case, exp = BY_ID["greet-01"], EXPECTED["greet-01"]
    good = report.Run(started="2026-10-22T20:00:00", mode="offline",
                      results=[score.score_case(case, exp, {"route": "greet", "text": "Moin, hier ist Hank."})])
    report.save(good, tmp_path)
    bad = report.Run(started="2026-10-22T21:00:00", mode="offline",
                     results=[score.score_case(case, exp, {"route": "grow", "text": "Moin"})])
    _, found = report.save(bad, tmp_path)
    assert found == ["greet-01: vorher bestanden, jetzt fehlgeschlagen"]
    assert (tmp_path / "2026-10-22T21-00-00.json").exists()
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["results"][0]["outcome"] == "failed"
    md = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "Rückschritte" in md and "- [ ] " in md


def test_run_all_with_a_stand_in_answerer(frozen) -> None:  # type: ignore[no-untyped-def]
    """The path a built pipeline takes: a fixture per case, the bridge's arguments, grading, the gates."""
    seen: list[tuple[str, str, dict]] = []  # type: ignore[type-arg]

    def stand_in(question: str, *, lang: str, live: dict, model: bool, url: str | None) -> dict:  # type: ignore[type-arg]
        seen.append((question, lang, live))
        case = next(c for c in CASES if c["question"] == question)
        return {"route": EXPECTED[case["id"]]["route_today"], "text": "", "hits": [], "model_calls": 0, "ms": 1}

    built = report.run_all(lambda name, lang, ledger: fixtures.build(name, lang=lang, ledger=ledger),
                           answerer=stand_in)
    assert len(built.results) == len(CASES) and len(seen) == len(CASES)
    assert {"snapshot", "plant_view", "automation_view", "alerts_view", "feeding_view", "camera", "vision", "ports",
            "ledger", "now"} <= set(seen[0][2])
    stale = next(live for q, _, live in seen if q == BY_ID["live-05"]["question"])
    assert stale["snapshot"]["sensors_age"] == 1500
    ledger = next(live for q, _, live in seen if q == BY_ID["live-06"]["question"])
    assert ledger["ledger"] and ledger["ledger"][0]["metric"] == "probe_humidity"
    gates = built.summary()
    assert gates["route_active"] == 1.0 and gates["safety_routes"] == 1.0 and gates["forbidden_routes"] == 0
    assert gates["counts"]["waits"] == 6 and gates["counts"]["not_built"] == 0  # 3 got their cards on 01.11.2026
    assert "Fall" in report.table(built) and "Handprüfung" in report.markdown(built)


def test_eval_files_have_no_em_dash_and_no_bom() -> None:
    files = [*sorted(EVAL.iterdir()), REPO / "scripts" / "hank_eval.py", Path(__file__)]
    for path in files:
        if path.is_file():
            data = path.read_bytes()
            assert not data.startswith(b"\xef\xbb\xbf"), path.name
            assert EM_DASH not in data.decode("utf-8"), path.name
            assert b"\r\n" not in data, path.name


# ---------- the cases through the pipeline ----------

@pytest.mark.parametrize("case_id", list(BY_ID))
def test_eval_case_offline(case_id, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    case, exp = BY_ID[case_id], EXPECTED[case_id]
    try:
        answerer = report.bridge()
    except report.NotBuilt as err:
        pytest.xfail(str(err))
    fixtures.freeze(monkeypatch)
    live = fixtures.build(exp["fixture"], lang=case["lang"], ledger=exp["ledger"])
    result = report.run_one(case, exp, live, answerer=answerer)
    if result.outcome in ("passed", "waits"):
        return
    if not STRICT:
        pytest.xfail(result.explain())
    pytest.fail(result.explain())
