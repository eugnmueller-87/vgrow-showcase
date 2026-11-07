"""Feeding v2, part 2 (Hank), spec 4.7 to 4.10 of the VIVOSUN design and the owner's words of 23.10.2026: Hank
speaks in the active fertilizer's unit (grams with spoons for VIVOSUN Base A/B, ml for BioBizz), hears a stated age
of the PLANT and offers its start as a two-tap card, answers "Wann dünge ich was?" from the dated plan, says
"Dünger wählen" while no fertilizer is chosen, and sends child-safety questions about the new bottles through the same
gate as the BioBizz ones (the household has a toddler; the VIVOSUN label says KEEP OUT OF REACH OF CHILDREN).

The live states are the eval fixtures with the day frozen on 22.10.2026: F22 (BioBizz chosen, card a draft, stage and
bloom start only estimated, plant.json's start 10.09.2026 only approximate), F22-vivosun (VIVOSUN chosen and
confirmed, the owner's stage early flower since 22.10.) and F22-nochoice (two fertilizers, none chosen).
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hank_eval import fixtures
from hank_eval.fake_ollama import FakeOllama
from vgrow import config, feeding, plant
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.hank import context, core, feedplan, livestate, ollama, proposals, stores, today, wiring
from vgrow.plant import PlantLog
from vgrow.server import COOKIE, make_app

REPO = Path(__file__).resolve().parent.parent
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
T0 = fixtures.F22_NOW.timestamp()
TODAY = fixtures.F22_DATE


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def live_of(name: str = "F22"):  # type: ignore[no-untyped-def]
    """The fixture's sources as Hank's live callable takes them (one build per language)."""
    built: dict[str, dict[str, Any]] = {}

    def live(lang: str) -> dict[str, Any]:
        if lang not in built:
            sources = fixtures.build(name, lang=lang).sources()
            sources.pop("ledger")
            built[lang] = sources
        return built[lang]

    return live


def ask(questions: list[tuple[str, str]], *, live: Any = None, client: Any = None) -> list[dict[str, Any]]:
    """A chat without the model: each answer as the page gets it, with the route's reason."""

    async def go() -> list[dict[str, Any]]:
        hank = core.Hank(live=live or live_of(), index=core.shared_index(), clock=lambda: T0, client=client)
        out = []
        try:
            for question, lang in questions:
                got = await hank.ask_full(question, client_id="chat", lang=lang, model=False)
                out.append({**core.answer_dict(got.answer), "reason": got.route.reason})
        finally:
            await hank.stop()
        return out

    return asyncio.run(go())


def state(name: str = "F22", lang: str = "de") -> livestate.LiveState:
    return livestate.collect(live_of(name)(lang))


def with_view(name: str, change: dict[str, Any], lang: str = "de") -> livestate.LiveState:
    sources = copy.deepcopy(live_of(name)(lang))
    sources["feeding_view"].update(change)
    return livestate.collect(sources)


# ---------- the catalog ----------

def test_the_catalog_has_both_languages_and_no_em_dash() -> None:
    assert feedplan.check() == []
    raw = (REPO / "vgrow" / "hank" / "feedplan.py").read_text(encoding="utf-8")
    assert EM_DASH not in raw and not raw.startswith("﻿") and "\r\n" not in raw


# ---------- 1. units ----------

@pytest.mark.parametrize("question", ["Wie viel Gramm Base A?", "Wie viele Löffel pro Liter?", "Wie viele Loeffel?",
                                      "how many grams of Base B?", "How many spoons per litre?", "Wie viel g/l?"])
def test_grams_and_spoons_are_amount_questions(question: str) -> None:
    from vgrow.hank import text

    assert context._AMOUNT_RE.search(text.fold(question)), question


def test_a_gram_card_answers_in_grams_with_spoons_and_never_in_ml() -> None:
    live = live_of("F22-vivosun")
    [grams], [spoons], [en] = (ask([(q, lang)], live=live) for q, lang in (
        ("Wie viel Gramm Base A pro Liter?", "de"), ("Wie viele Löffel pro Liter?", "de"),
        ("How many spoons of Base B per litre?", "en")))
    for answer in (grams, spoons):
        assert answer["route"] == "grow"
        assert answer["text"].startswith("Diese Woche (Blütewoche 1) je Liter Wasser: Base A 0,75 g, 1,5 Löffel "
                                         "(Tabelle 1,5 g/l), Base B 0,75 g, 1,5 Löffel (Tabelle 1,5 g/l) [1].")
        assert not re.search(r"\bml\b", answer["text"])
    assert "Lampe" not in spoons["text"]  # "pro" is a stop word: "pro Liter" no longer finds "25 Punkte pro Tippen"
    assert en["text"].startswith("This week (Flowering week 1) per litre of water: Base A 0.75 g, 1.5 spoons (chart "
                                 "1.5 g/l), Base B 0.75 g, 1.5 spoons (chart 1.5 g/l) [1].")


def test_the_today_line_uses_the_active_cards_unit_and_names_the_next_change() -> None:
    lines, _ = today.lines(live_of("F22-vivosun")("de"), "de")
    feed = [line for line in lines if line.startswith("Wenn du heute gießt")]
    assert feed == ["Wenn du heute gießt, je Liter (Blütewoche 1): Base A 0,75 g (1,5 Löffel), Base B 0,75 g (1,5 "
                    "Löffel). Faktor 0,5 ist eingerechnet, nie mehr als die Tabelle. Nächste Änderung: spätestens ab "
                    "19.11. (Blütewoche 5)."]  # the owner's stage day is an upper bound of the real flip
    assert not any(re.search(r"\bml\b", line) for line in lines)
    en, _ = today.lines(live_of("F22-vivosun")("en"), "en")
    assert any(line.startswith("If you water today, per litre (Flowering week 1): Base A 0.75 g (1.5 spoons), Base B "
                               "0.75 g (1.5 spoons).") for line in en)


def test_an_ml_card_keeps_ml() -> None:
    view = {"amount_unit": "ml", "doses": [{"label": "Bio·Grow", "per_liter": 1.5}]}
    assert feedplan.amount_items(view["doses"], feedplan.unit_of(view), "de") == ["Bio·Grow 1,5 ml"]
    assert feedplan.unit_of({}) == "ml"  # an old view without the key: ml, as before
    assert feedplan.amount_items([{"label": "Base A", "per_liter": None}], "g", "de") is None  # never "? g"


def test_hanks_wiring_passes_the_page_units_so_the_part_1_guard_opens() -> None:
    log = PlantLog()
    log.set_feeding_choice("vivosun", feeding.setups_of(plant.load_profile()))
    got = wiring.Collector(DemoBox(), plant_log=log)("de")
    view = got["feeding_view"]
    assert view.get("withheld") is None and view["state"] != "no_card"
    assert view["amount_unit"] == "g" and view["card"]["id"] == "vivosun-base-ab"


def test_hard_coded_ml_is_gone_from_the_feeding_lines() -> None:
    for name in ("context.py", "today.py"):
        source = (REPO / "vgrow" / "hank" / name).read_text(encoding="utf-8")
        assert 'fmt_qty(d.get("per_liter"), "ml"' not in source, name


# ---------- 2. a stated age ----------

@pytest.mark.parametrize(("question", "kind", "n"), [
    ("Meine Pflanze ist 5 Wochen alt", "weeks", 5), ("Sie ist fünf Wochen alt.", "weeks", 5),
    ("She is 5 weeks old", "weeks", 5), ("My plant is five weeks old", "weeks", 5),
    ("vor 5 Wochen gekeimt", "weeks", 5), ("germinated 5 weeks ago", "weeks", 5), ("36 Tage alt", "days", 36),
    ("Sie ist 6 Wochen und 1 Tag alt", "days", 43), ("she is 6 weeks and 1 day old", "days", 43),
    ("in der 6. Woche", "life_week", 6), ("Pflanzenwoche 6", "life_week", 6), ("plant week 6", "life_week", 6),
])
def test_age_claim_parses_german_and_english(question: str, kind: str, n: int) -> None:
    assert feedplan.age_claim(question, TODAY) == feedplan.AgeClaim(kind, n)


@pytest.mark.parametrize("question", ["seit dem 18.09.", "Sie ist seit dem 18.09. am Wachsen",
                                      "Gekeimt am 18.09.2026", "germinated on September 18"])
def test_a_stated_start_day_is_a_claim(question: str) -> None:
    assert feedplan.age_claim(question, TODAY) == feedplan.AgeClaim("date", 0, "2026-09-18")


@pytest.mark.parametrize("question", [
    "Das Wasser im Tank ist 3 Tage alt", "Die Nährlösung ist 2 Wochen alt", "Die Samen sind 5 Wochen alt",
    "my seeds are 5 weeks old", "The water is 3 days old", "Die Mischung ist 3 Tage alt", "Der Tee ist 5 Wochen alt",
    "Ich habe seit dem 18.09. nicht gegossen", "Der Dünger ist seit dem 18.09. offen", "Blütewoche 5",
    "flowering week 5", "5. Woche der Blüte", "5 Wochen bis zur Ernte", "Wie viel Dünger in Woche 5?",
    "Wie viel Dünger in der 5. Woche?", "Wie alt ist meine Pflanze?", "Seit dem 18.09. blüht sie",
    # the age in a clause of its own, the subject in the clause before (critique 23.10.2026, M2)
    "Das Wasser im Tank, 3 Tage alt", "Das Wasser im Tank: 3 Tage alt", "Die Samen, 5 Wochen alt",
    "my seeds, 5 weeks old", "The water: 3 days old", "Die Samen, sie sind 5 Wochen alt",
    "Die Nährlösung, seit dem 18.09. angesetzt", "Die Nährlösung, 2 Wochen alt, ist sie noch gut?",
])
def test_age_claim_negatives(question: str) -> None:
    assert feedplan.age_claim(question, TODAY) is None


def test_routes_for_a_pure_claim_the_age_question_the_plan_and_the_choice() -> None:
    route = feedplan.route_for("Meine Pflanze ist 5 Wochen alt", "de", today=TODAY)
    assert route.name == "reading" and route.reason == "reading:age_claim:weeks=5" and route.term == "age_claim"
    assert feedplan.route_for("Wie alt ist meine Pflanze?", "de").term == "plant_age"
    assert feedplan.route_for("Wann dünge ich was?", "de").term == "feed_plan"
    assert feedplan.route_for("What do I feed when?", "en").term == "feed_plan"
    assert feedplan.route_for("Welchen Dünger soll ich nehmen?", "de").term == "feed_choice"
    for other in ("Wann dünge ich das nächste Mal?", "Wie viel Dünger in Blütewoche 3?",
                  "Meine Pflanze ist 5 Wochen alt, wie viel Dünger?"):
        assert feedplan.route_for(other, "de", today=TODAY) is None, other  # the frequency note, a week, mixed


def test_conflict_answer_and_card() -> None:
    de, en = ask([("Meine Pflanze ist 5 Wochen alt", "de"), ("She is 5 weeks old", "en")])
    assert de["route"] == "reading" and de["reason"] == "reading:age_claim:weeks=5"
    assert de["text"] == ("Das passt nicht zu meinem Datum. Als Start steht der 10.09.2026 (aus plant.json, nur "
                          "ungefähr); danach ist sie 6 Wochen alt (Tag 42). Bei 5 Wochen wäre der Start der "
                          "17.09.2026. Die Düngermengen ändern sich dadurch nicht: sie hängen an deiner Phase und am "
                          "Blütebeginn. Übernehmen kannst du ihn mit zwei Tipps auf der Karte.")
    [card] = de["proposals"]
    assert card["kind"] == "plant_start" and card["two_taps"] is True
    assert card["body"] == {"action": "plant_start", "age_weeks": 5}  # stored as an age: the start stays "etwa"
    assert card["label"] == "Startdatum 17.09.2026 übernehmen (5 Wochen alt)"
    assert en["text"].startswith("That does not fit my date. The start on record is Sep 10, 2026 (from plant.json, "
                                 "only approximate)")
    assert en["proposals"][0]["label"] == "Take over the start date Sep 17, 2026 (5 weeks old)"


def test_a_stated_start_day_gives_a_dated_card() -> None:
    [out] = ask([("seit dem 18.09.", "de")])
    assert "Mit deinem Start am 18.09.2026 wäre sie 4 Wochen und 6 Tage alt." in out["text"]
    assert out["proposals"][0]["body"] == {"action": "plant_start", "date": "2026-09-18"}


def test_consistent_claim_gives_no_card() -> None:
    [out] = ask([("Sie ist 6 Wochen alt", "de")])
    assert out["text"] == ("Passt: nach dem Start am 10.09.2026 ist sie 6 Wochen alt (Tag 42, Pflanzenwoche 7). "
                           "Nichts zu ändern.")
    assert out["proposals"] == []


def test_unknown_start_gives_a_card() -> None:
    base = live_of()

    def unknown(lang: str) -> dict[str, Any]:
        out = copy.deepcopy(base(lang))
        out["plant_view"]["age"] = {"state": "unknown", "start": None, "by": None, "ask": {"reason": "unknown"}}
        return out

    [out] = ask([("Meine Pflanze ist 5 Wochen alt", "de")], live=unknown)
    assert out["text"].startswith("Danke. Einen Starttag kenne ich noch nicht. Bei 5 Wochen wäre es der 17.09.2026.")
    assert [c["body"] for c in out["proposals"]] == [{"action": "plant_start", "age_weeks": 5}]


def test_life_week_conflict_asks_without_a_card() -> None:
    [out] = ask([("Pflanzenwoche 4", "de")])
    assert out["text"] == ("Du sagst Pflanzenwoche 4. Nach meinem Start am 10.09.2026 ist sie in Pflanzenwoche 7. "
                           "Welcher Tag war der Start? Trag ihn in der Pflanzenkarte ein.")
    assert out["proposals"] == []


def test_card_body_checks() -> None:
    live = state()
    ok = [{"action": "plant_start", "age_weeks": 5}, {"action": "plant_start", "age_days": 36},
          {"action": "plant_start", "date": "2026-09-18"}]
    for body in ok:
        assert proposals.check_body(body, live) == body
        assert proposals.two_taps(body) is True
        assert proposals.label(body, live, "de").startswith("Startdatum ")
    bad = [{"action": "plant_start"}, {"action": "plant_start", "on": True},
           {"action": "plant_start", "date": "2026-09-18", "age_weeks": 5},
           {"action": "plant_start", "date": "2026-09-18", "extra": 1}, {"action": "plant_start", "date": "18.09."},
           {"action": "plant_start", "date": "2026-12-01"}, {"action": "plant_start", "date": "2025-01-01"},
           {"action": "plant_start", "age_weeks": 53}, {"action": "plant_start", "age_weeks": True},
           {"action": "plant_start", "age_days": -1}, {"action": "plant_start", "age_days": 401}]
    for body in bad:
        with pytest.raises(proposals.Refused):
            proposals.check_body(body, live)
    # critique 23.10.2026: no plant_start body ever falls through to the humidifier branch
    assert proposals.label({"action": "auto_humidifier", "on": True}, live, "de") == "Befeuchter-Automatik an"


def test_accepting_the_card_stores_the_start_under_state_dir_and_is_journaled_as_hank_accepted() -> None:
    log = PlantLog()
    box = DemoBox()
    plant_json = (REPO / "plant.json").read_bytes()

    async def go() -> None:
        hank = core.Hank(live=wiring.Collector(box, plant_log=log, clock=lambda: fixtures.F22_NOW), demo=True)
        client = TestClient(TestServer(make_app(box, Alerts(), "k", plant_log=log, hank=hank)))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})
        try:
            got = await hank.ask_full("Meine Pflanze ist 5 Wochen alt", client_id="c", lang="de", model=False)
            [card] = got.answer.proposals
            resp = await client.post("/api/cmd", json={**card.body, "proposal": card.id})
            assert resp.status == 200 and await resp.json() == {"ok": True, "date": "2026-09-17"}
            resp = await client.post("/api/cmd", json={"action": "feeding_setup", "setup": "vivosun"})
            assert resp.status == 200
        finally:
            await client.close()
            await hank.stop()

    asyncio.run(go())
    assert log.data["start_date"]["date"] == "2026-09-17" and log.data["start_date"]["from"] == "age_weeks"
    assert (config.STATE_DIR / "plant-log.json").exists()
    assert (REPO / "plant.json").read_bytes() == plant_json  # never the repository file
    lines = stores.Journal().all()
    assert [(e["action"], e["origin"], e["body"]) for e in lines] == [
        ("plant_start", "hank_accepted", {"action": "plant_start", "age_weeks": 5}),
        ("feeding_setup", "human", {"action": "feeding_setup", "setup": "vivosun"})]


def test_plant_start_and_feeding_setup_are_journal_actions() -> None:
    assert {"plant_start", "feeding_setup"} <= set(core.JOURNAL_ACTIONS)
    # the fertilizer market (23.10.2026): Hank offers the switch as a two-tap card; the owner's tap still decides
    assert "plant_start" in proposals.KINDS and "feeding_setup" in proposals.KINDS
    assert proposals.two_taps({"action": "feeding_setup", "setup": "canna-terra"})


def test_the_age_question_reads_from_code_and_asks_while_it_is_rough() -> None:
    de, en = ask([("Wie alt ist meine Pflanze?", "de"), ("How old is my plant?", "en")])
    assert de["route"] == "reading" and de["reason"] == "reading:plant_age"
    assert de["text"] == ("Die Pflanze ist etwa 6 Wochen alt (Pflanzenwoche 7), Start etwa 10.09.2026, aus "
                          "plant.json. Der Start 10.09.2026 ist nur ungefähr. Weißt du den Tag der Keimung? Dann trag "
                          "ihn in der Pflanzenkarte ein.")
    assert en["text"].startswith("The plant is about 6 weeks old (plant week 7)")
    unknown = state()
    unknown.plant_view["age"] = {"state": "unknown"}
    assert feedplan.age_lines(unknown, "de") == ["Ich kenne das Alter noch nicht. Trag die Wochen oder den Starttag "
                                                 "in der Pflanzenkarte ein."]


def test_mixed_question_is_a_grow_route_with_the_card_and_a_mandatory_fact() -> None:
    [out] = ask([("Meine Pflanze ist 5 Wochen alt, wie viel Dünger?", "de")], live=live_of("F22-vivosun"))
    assert out["route"] == "grow"
    assert out["text"].startswith("Diese Woche (Blütewoche 1) je Liter Wasser: Base A 0,75 g, 1,5 Löffel")
    assert ("Zum Alter: als Start steht der 10.09.2026 (aus plant.json, nur ungefähr), bei 5 Wochen wäre es der "
            "17.09.2026; die Düngermengen ändern sich dadurch nicht.") in out["text"]
    assert "Mein Vorschlag: Startdatum 17.09.2026 übernehmen (5 Wochen alt). Du bestätigst ihn mit zwei Tipps." \
        in out["text"]
    assert [c["body"] for c in out["proposals"]] == [{"action": "plant_start", "age_weeks": 5}]


def test_no_model_is_called_for_age_plan_and_choice_routes() -> None:
    fake = FakeOllama(reply="Das steht so in meiner Kartei [F1].")

    async def go() -> None:
        server = TestServer(fake.app())
        await server.start_server()
        client = ollama.OllamaClient(url=str(server.make_url("")).rstrip("/"))
        hank = core.Hank(live=live_of("F22-vivosun"), client=client, index=core.shared_index(), clock=lambda: T0)
        try:
            for q in ("Meine Pflanze ist 5 Wochen alt", "Wie alt ist meine Pflanze?", "Wann dünge ich was?",
                      "Welchen Dünger soll ich nehmen?"):
                out = await hank.ask(q, client_id="c1")
                assert out["done"] is True and out["answer"]["route"] == "reading", q
            assert fake.calls("chat") == []
        finally:
            await hank.stop()
            await client.close()
            await server.close()

    asyncio.run(go())


# ---------- 3. "Wann dünge ich was?" ----------

def test_feed_plan_answers_in_the_active_cards_unit() -> None:
    de, en = ask([("Wann dünge ich was?", "de"), ("What do I feed when?", "en")], live=live_of("F22-vivosun"))
    assert de["reason"] == "reading:feed_plan" and de["proposals"] == []
    assert de["text"].split("\n") == [
        "Heute (22.10., Blütewoche 1, Pflanze etwa 6 Wochen alt): je Liter Base A 0,75 g (1,5 Löffel) und Base B "
        "0,75 g (1,5 Löffel).",  # "etwa": plant.json's start is only approximate (critique 23.10.2026, S2)
        "Spätestens ab 19.11. (Blütewoche 5): Base A 0,5 g (1 Löffel) statt 0,75 g und Base B 0,5 g (1 Löffel) statt "
        "0,75 g.",  # the bloom start is the day the owner set the stage: an upper bound (S1)
        "Spätestens ab 17.12. (Blütewoche 9) nennt die Tabelle keine Menge mehr: das Cockpit rechnet keine, das "
        "entscheidest du.",
        "Faktor 0,5 ist eingerechnet, nie mehr als die Tabelle. Die Löffel sind auf halbe abgerundet; unter einem "
        "halben Löffel hilft nur die Waage. Wie oft du düngst, sagt die Tabelle nicht.",
        "Der Start 10.09.2026 ist nur ungefähr. Weißt du den Tag der Keimung? Dann trag ihn in der Pflanzenkarte ein.",
    ]  # the age ask last, because plant.json's start is only approximate
    assert en["text"].split("\n")[1] == ("From Nov 19 at the latest (Flowering week 5): Base A 0.5 g (1 spoon) "
                                         "instead of 0.75 g and Base B 0.5 g (1 spoon) instead of 0.75 g.")
    assert "plant about 6 weeks old" in en["text"].split("\n")[0]
    assert not re.search(r"\bml\b", de["text"] + en["text"])


def test_feed_plan_in_draft_gives_dates_and_the_reason_but_no_amount() -> None:
    [out] = ask([("Wann dünge ich was?", "de")])  # F22: BioBizz, the card not confirmed, the bloom start estimated
    assert out["text"].split("\n")[:3] == [
        "Die Düngerkarte (BioBizz Düngeschema 2026) ist noch nicht bestätigt: bis dahin nenne ich keine Mengen, nur "
        "die Daten. Bestätige sie in der Düngerkarte.",
        "Diese Woche (spätestens 22.10. bis 28.10.): Blütewoche 1.",
        "Die Menge ändert sich spätestens ab 29.10. (Blütewoche 2).",
    ]
    assert "Spätestens ab 17.12. (Blütewoche 9) nur Wasser." in out["text"]
    assert not re.search(r"\d\s*(ml|g)\b", out["text"])


def test_feed_plan_by_state() -> None:
    viv = state("F22-vivosun")
    view = viv.feeding_view
    estimated = copy.deepcopy(view)
    estimated.update(state="needs_stage", doses=None)
    estimated["timeline"]["dates"] = "latest"
    estimated["timeline"]["next_change"]["latest"] = True
    for column in estimated["timeline"]["columns"]:
        for value in column["values"]:
            value["per_liter"] = value["spoons"] = None
    lines = feedplan.plan_lines(with_view("F22-vivosun", estimated), "de")
    assert lines[:3] == ["Die Woche ist nur geschätzt: keine Mengen, bis du die Phase bestätigst. Die Daten gelten "
                         "spätestens.", "Diese Woche (spätestens 22.10. bis 28.10.): Blütewoche 1.",
                         "Die Menge ändert sich spätestens ab 19.11. (Blütewoche 5)."]
    assert not any(re.search(r"\d\s*g\b|Löffel", line) for line in lines)
    ends = feedplan.plan_lines(with_view("F22-vivosun", {"state": "label_ends", "week": {"key": "label_end",
                                                                                       "bloom_week": 9}}), "de")
    assert ends == ["Nach Blütewoche 8 nennt die Tabelle keine Menge mehr: das Cockpit rechnet keine, das "
                    "entscheidest du."]
    harvest = feedplan.plan_lines(with_view("F22", {"state": "harvest", "week": {"label": "Ab Blütewoche 10: Ernte"}}),
                                  "de")
    assert harvest == ["Ab Blütewoche 10: Ernte. Kein Dünger mehr."]
    water = feedplan.plan_lines(with_view("F22", {"state": "water_only", "week": {"label": "Blütewoche 9: nur "
                                                                                           "Wasser"}}), "de")
    assert water == ["Blütewoche 9: nur Wasser, diese Woche kein Dünger.",
                     "Spätestens ab 24.12. (Blütewoche 10) Ernte, kein Dünger mehr."]  # F22: the estimate, an upper bound
    assert feedplan.plan_lines(state("F22-nochoice"), "de") == [
        "Welchen Dünger gibst du: BioBizz (flüssig, organisch) oder VIVOSUN Base A + B (Granulat)? Es gilt immer nur "
        "einer. Wähle ihn in der Düngerkarte, bis dahin rechnet das Cockpit keine Mengen."]


# ---------- 4. the fertilizer choice ----------

def test_choose_a_fertilizer_once_with_the_reason_and_no_amounts() -> None:
    lines, _ = today.lines(live_of("F22-nochoice")("de"), "de")
    assert [line for line in lines if "Dünger" in line and "wählen" in line] == [
        "Dünger wählen: BioBizz (flüssig, organisch) oder VIVOSUN Base A + B (Granulat). Es gilt immer nur einer, bis "
        "dahin rechnet das Cockpit keine Mengen."]
    assert not any("je Liter" in line or "Düngerkarte bestätigen" in line for line in lines)
    assert len(lines) <= today.MAX_LINES
    [grow, choice] = ask([("Wie viel Dünger gebe ich diese Woche?", "de"), ("Welchen Dünger soll ich nehmen?", "de")],
                         live=live_of("F22-nochoice"))
    assert grow["route"] == "grow" and grow["text"].startswith(
        "Zwei Dünger sind eingetragen, BioBizz (flüssig, organisch) und VIVOSUN Base A + B (Granulat), und es gilt "
        "immer nur einer")
    assert not re.search(r"\d\s*(ml|g)\b", grow["text"])
    assert choice["reason"] == "reading:feed_choice" and choice["text"].endswith(
        "Welcher besser ist, sagt keine meiner Karten: das entscheidest du.")
    [active] = ask([("Welchen Dünger soll ich nehmen?", "de")])
    assert active["text"].startswith("Gerade gilt BioBizz (flüssig, organisch).")


def test_an_empty_tank_and_the_fertilizer_choice_both_stand_on_the_today_list() -> None:
    """Merge of main (water sensor) and feeding v2: the refill line and the choice line, each once, the refill first."""
    from vgrow.hank import texts_live

    texts_live.register()
    today.register()
    for lang, refill in (("de", "Tank auffüllen."), ("en", "Refill the tank.")):
        sources = copy.deepcopy(live_of("F22-nochoice")(lang))
        sources["snapshot"].setdefault("climate", {}).update(
            water_kind="wet_dry", water_state="dry", water_level=None, water_raw=None, water_since=None,
            water_run_s=600)  # ten dry minutes: past the alarm's own debounce
        lines, _ = today.lines(sources, lang)
        choose = feedplan.choice_line(livestate.collect(sources), lang)
        assert choose
        tank = [i for i, line in enumerate(lines) if line.startswith(refill)]
        choice = [i for i, line in enumerate(lines) if line == choose]
        assert len(tank) == 1 and len(choice) == 1 and tank[0] < choice[0], lines
        assert not any("je Liter" in line or "per litre" in line for line in lines)  # still no amounts
        assert len(lines) <= today.MAX_LINES


# ---------- 5. the today list ----------

def test_today_list_asks_for_the_start_once_then_reminds() -> None:
    first, second = ask([("Was ist heute zu tun?", "de"), ("Was ist heute zu tun?", "de")],
                        live=live_of("F22-vivosun"))
    ask_line = "Der Start 10.09.2026 ist nur ungefähr. Weißt du den Tag der Keimung? Dann trag ihn in der Pflanzenkarte ein."
    assert ask_line in first["text"].split("\n")
    assert "Der Start 10.09.2026 ist nur ungefähr: den echten Tag in der Pflanzenkarte eintragen." in \
        second["text"].split("\n")
    assert len(first["text"].split("\n")) <= today.MAX_LINES
    asked = json.loads((config.STATE_DIR / "hank" / "asked.json").read_text(encoding="utf-8"))
    assert asked["start_date"] == "approx:2026-09-10"


# ---------- 6. child safety ----------

@pytest.mark.parametrize(("question", "lang", "route"), [
    ("Ist Base A giftig für mein Kind?", "de", "health"), ("Is Base A toxic for my child?", "en", "health"),
    ("Ist Base A gitig für mein Kind?", "de", "health"),  # one letter missing: still the safety gate (spec 11.3)
    ("Ist das Granulat gefährlich für meinen Hund?", "de", "health"),
    ("Is VIVOSUN safe around my toddler?", "en", "health"),
    ("Mein Kind hat Base A gegessen", "de", "health_emergency"),
    ("Mein Sohn hat Granulat verschluckt", "de", "health_emergency"),
    ("I got Base B in my eye", "en", "health_emergency"),
    ("VIVOSUN Base B in the eye, what now?", "en", "health_emergency"),
])
def test_gates_name_the_vivosun_bottles(question: str, lang: str, route: str) -> None:
    [out] = ask([(question, lang)])
    assert out["route"] == route, (question, out["reason"])
    assert out["proposals"] == []
    assert ("Giftnotruf" if lang == "de" else "poison control") in out["text"]


def test_the_child_safety_reply_quotes_the_label_and_judges_nothing() -> None:
    [de] = ask([("Ist Base A giftig für mein Kind?", "de")])
    assert de["text"].endswith("Auf dem VIVOSUN-Etikett steht dazu: außer Reichweite von Kindern aufbewahren.")
    assert "harmlos" not in de["text"] and "ungiftig" not in de["text"]
    [bio] = ask([("Ist BioBizz giftig für mein Kind?", "de")])
    assert bio["route"] == "health" and "VIVOSUN" not in bio["text"]  # the label sentence only for its bottles


@pytest.mark.parametrize("question", ["Wie viel Base A pro Liter?", "Was ist Base B?",
                                      "Is this kind of fertilizer safe for flowering?"])
def test_feeding_questions_about_the_bottles_stay_feeding_questions(question: str) -> None:
    [out] = ask([(question, "de" if "Liter" in question or "Was" in question else "en")])
    assert out["route"] not in ("health", "health_emergency"), out["reason"]


# ---------- 7. the search ----------

def test_context_drops_passages_of_the_inactive_nutrient_card() -> None:
    [viv] = ask([("Wie viel Dünger gebe ich diese Woche?", "de")], live=live_of("F22-vivosun"))
    assert "BioBizz" not in viv["text"] and "Bio-Grow" not in viv["text"]
    [named] = ask([("Was ist Bio-Bloom?", "de")], live=live_of("F22-vivosun"))
    assert "Bio" in named["text"]  # the question names the other fertilizer: its passages stay


def test_vocab_maps_base_a_and_b_to_feeding() -> None:
    for question in ("Was ist Base B?", "Wie viel Base A?", "VIVOSUN Granulat"):
        assert context.mentions(question, "de").feeding, question
    vocab = json.loads((REPO / "kartei" / "_hank" / "vocab.json").read_text(encoding="utf-8"))
    entries = vocab if isinstance(vocab, list) else vocab.get("entries") or vocab.get("terms") or []
    maps = {e["id"]: e.get("maps_to") for e in entries if isinstance(e, dict)}
    assert maps.get("base_a") == "base_a" and maps.get("base_b") == "base_b"


def test_the_card_is_valid_for_30_minutes_like_every_card() -> None:
    card = proposals.make({"action": "plant_start", "age_weeks": 5}, state(), "de", now=time.time())
    assert card.expires - time.time() <= proposals.VALID_SECONDS + 1 and card.two_taps


# ---------- 8. the preview ----------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_preview_mock_answers_in_the_amount_unit() -> None:
    from test_hank_web import run_mock

    grams = run_mock({"hum": 79, "amount_unit": "g"})["feed"]["answer"]["text"]
    assert "Base A 1,5 g/l" in grams and "Löffel" in grams and not re.search(r"\bml\b", grams)
    assert "ml/l" in run_mock()["feed"]["answer"]["text"]  # BioBizz stays the default, in ml
    mock = (REPO / "scripts" / "preview_mock.js").read_text(encoding="utf-8")
    assert 'amount_unit: "ml"' in mock and EM_DASH not in mock


# ---------- critique 23.10.2026 ----------

@pytest.mark.parametrize(("question", "kind", "n"), [
    ("Meine Pflanze, 5 Wochen alt, braucht sie mehr Licht?", "weeks", 5),  # the plant is the subject before
    ("Meine Pflanze ist 5 Wochen alt, wie viel Dünger?", "weeks", 5),
    ("sie ist 3 Tage alt", "days", 3),
])
def test_a_clause_without_a_subject_takes_the_one_before(question: str, kind: str, n: int) -> None:
    assert feedplan.age_claim(question, TODAY) == feedplan.AgeClaim(kind, n)


def test_a_water_age_in_its_own_clause_gives_no_card_and_no_age_fact() -> None:
    for question in ("Das Wasser im Tank, 3 Tage alt, ist das schlimm?", "my seeds, 5 weeks old, still good?"):
        assert feedplan.cards_for_claim(question, state(), "de") == []
        assert feedplan.claim_fact(question, state(), "de") is None


def test_label_end_names_the_last_chart_week_even_past_it() -> None:
    for current in (9, 12):  # M1: in week 12 the table still ends after week 8
        ends = feedplan.plan_lines(with_view("F22-vivosun", {"state": "label_ends", "week": {
            "key": "label_end", "bloom_week": current}}), "de")
        assert ends == ["Nach Blütewoche 8 nennt die Tabelle keine Menge mehr: das Cockpit rechnet keine, das "
                        "entscheidest du."], current
    assert feedplan.last_chart_week({"after_bloom": [], "timeline": {"tail": {"key": "label_end", "bloom_week": 9}}}) \
        == 8
    assert feedplan.last_chart_week({}) == "?"


def test_a_future_day_is_refused_not_moved_to_last_year() -> None:
    claim = feedplan.age_claim("seit dem 25.10.", TODAY)
    assert claim == feedplan.AgeClaim("date", 0, "2026-10-25")
    lines, bodies = feedplan.claim_answer(claim, state(), "de")
    assert lines == ["Ein Start in der Zukunft geht nicht. Trag den Starttag in der Pflanzenkarte ein."]
    assert bodies == []
    assert feedplan.age_claim("seit dem 30.12.", TODAY) == feedplan.AgeClaim("date", 0, "2025-12-30")  # far ahead


def test_more_than_52_weeks_says_so_instead_of_400_days() -> None:
    lines, bodies = feedplan.claim_answer(feedplan.AgeClaim("weeks", 54), state(), "de")
    assert lines == ["Mehr als 52 Wochen nimmt die Pflanzenkarte nicht in Wochen an. Trag den Starttag in der "
                     "Pflanzenkarte ein."] and bodies == []
    assert feedplan.claim_answer(feedplan.AgeClaim("weeks", 60), state(), "de")[0][0].startswith(
        "Das wäre mehr als 400 Tage her.")


def test_unit_of_falls_back_to_the_chart_unit() -> None:
    assert feedplan.unit_of({"unit": "g/l"}) == "g"
    assert feedplan.unit_of({"unit": "ml/l"}) == "ml"
    assert feedplan.unit_of({"amount_unit": "g", "unit": "ml/l"}) == "g"


@pytest.mark.parametrize(("question", "lang"), [
    ("Kann mein Kind Base A trinken?", "de"), ("Can my toddler eat the granules?", "en"),
    ("Ist das Pulver giftig für mein Kind?", "de"), ("Is the powder safe for my kid?", "en"),
    ("Darf mein Hund das Granulat fressen?", "de"), ("Can my dog lick Base B?", "en"),
])
def test_infinitives_and_the_powder_reach_the_child_gate(question: str, lang: str) -> None:
    [out] = ask([(question, lang)])
    assert out["route"] == "health" and out["reason"].startswith("gate:child_risk:"), (question, out["reason"])
    assert out["proposals"] == []


def test_the_label_sentence_follows_a_vivosun_product_anywhere_in_the_question() -> None:
    [out] = ask([("Ist Dünger giftig für mein Kind, ich meine Base A?", "de")])
    assert out["route"] == "health" and "VIVOSUN-Etikett" in out["text"], out["reason"]


def test_a_question_about_the_other_fertilizer_gets_no_amount_of_the_active_one() -> None:
    [bio] = ask([("Wie viel Base A pro Liter?", "de")])  # F22: BioBizz counts
    assert bio["route"] == "grow"
    assert "Gerade gilt BioBizz" in bio["text"], bio["text"]
    # the active card's amounts never stand under the other card's name; the named card's own label chart may
    assert not re.search(r"\bml\b", bio["text"]) and "Bio-Grow" not in bio["text"]
    [viv] = ask([("Wie viel Bio-Grow gebe ich?", "de")], live=live_of("F22-vivosun"))
    assert "Gerade gilt VIVOSUN" in viv["text"] and "Base A 0,75 g" not in viv["text"], viv["text"]


def test_the_suggestion_chip_names_no_unit() -> None:
    from vgrow.hank import texts

    for lang in ("de", "en"):
        assert not any(re.search(r"\bml\b", chip) for chip in texts.chips(lang))


def test_stage_dates_hold_at_the_latest() -> None:
    line = state("F22-vivosun").feeding_view["timeline"]
    assert line["dates"] == "latest" and line["next_change"]["latest"] is True


def test_the_today_list_names_the_end_of_the_table() -> None:
    sources = copy.deepcopy(live_of("F22-vivosun")("de"))
    sources["feeding_view"].update(state="label_ends", doses=[], week={"key": "label_end", "bloom_week": 10})
    lines, _ = today.lines(sources, "de")
    assert "Nach Blütewoche 8 nennt die Tabelle keine Menge mehr: das Cockpit rechnet keine, das entscheidest du."         in lines
