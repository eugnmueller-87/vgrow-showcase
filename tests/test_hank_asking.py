"""Hank v1.2, the owner's words of 23.10.2026: "Hank is a bit silly, he has to realize a question and either he can
help or not."

Seen live: "what is VPD" got the live VPD reading instead of an explanation, "can u fix it?" had nothing to refer to,
"Fix everything" came back with the injection reply, and the plant's height and bloom start were not where Hank could
see them. Built in vgrow/hank/asking.py (with router.py, core.py, fix.py, today.py), each part tested here:
1. question types (explain, value, how-to, why) decided before composing, and answered as that type;
2. explain from the glossary (kartei/glossary/glossary.json): the term's text first, then the live reading or "Die Box
   misst das nicht.", then "Entwurf"; never the reading alone;
3. can help or not: a typed question without a card says so in one sentence and what Hank can do instead;
4. follow-ups per chat for 15 minutes ("can u fix it?", "what about humidity?", "Und das?", "warum?");
5. the height with the day it was measured, and the today list's asks (height older than two days, bloom start only
   estimated, asked once);
6. every new text in German and English.

The live state is the eval fixture F22 (probe 79 %, VPD without a reading, light 60 % against 100 %, stage and bloom
start only estimated from the camera on 22.10., height 61 cm on 22.10.) with the day frozen.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from hank_eval import fixtures
from vgrow import config, i18n
from vgrow.hank import asking, core, fix, livestate, nudges, today
from vgrow.hank.kartei_index import Index
from vgrow.hank.router import Router
from vgrow.hank.types import ROUTES

REPO = Path(__file__).resolve().parent.parent
GLOSSARY = REPO / "kartei" / "glossary" / "glossary.json"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
T0 = fixtures.F22_NOW.timestamp()


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


@pytest.fixture(scope="module")
def index() -> Index:
    return core.shared_index()


class Clock:
    def __init__(self, start: float = T0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def f22(lang: str = "de") -> dict[str, Any]:
    sources = fixtures.build("F22", lang=lang).sources()
    sources.pop("ledger")
    return sources


def with_height(live: dict[str, Any], day: str, cm: float = 61.0) -> dict[str, Any]:
    out = copy.deepcopy(live)
    out["plant_view"]["height"] = {"cm": cm, "date": day}
    out["plant_view"]["heights"] = [out["plant_view"]["height"]]
    return out


def ask(questions: list[tuple[str, str]], *, live: Any = None, clock: Clock | None = None,
        client_id: str = "chat") -> list[dict[str, Any]]:
    """A chat: the questions one after the other in one client_id, no model; the answers as the page gets them."""
    clock = clock or Clock()

    async def go() -> list[dict[str, Any]]:
        hank = core.Hank(live=live or f22, index=core.shared_index(), clock=clock)
        out = []
        try:
            for question, lang in questions:
                got = await hank.ask_full(question, client_id=client_id, lang=lang, model=False)
                out.append({**core.answer_dict(got.answer), "reason": got.route.reason, "plain": got.answer.plain()})
        finally:
            await hank.stop()
        return out

    return asyncio.run(go())


# ---------- the glossary card ----------

def test_glossary_card_22_sourced_draft_terms_in_both_languages() -> None:
    raw = GLOSSARY.read_text(encoding="utf-8")
    assert not raw.startswith("﻿") and "\r\n" not in raw and EM_DASH not in raw
    data = json.loads(raw)
    # 19 of 23.10.2026 plus lollipopping, defoliation and mainlining (review 1, MUST 6; source G28, read in full), plus
    # the Durst-Index card's Durst-Index and ET (26.10.2026, sources G34 to G37)
    assert data["status"] == "draft" and len(data["terms"]) == 24
    for term in data["terms"]:
        assert term["text"] and term["text_en"] and term["label"] and term["label_en"], term["key"]
        assert set(term["aliases"]) == {"de", "en"} and term["aliases"]["de"] and term["aliases"]["en"], term["key"]
        assert term["sources"] and all(s in data["sources"] for s in term["sources"]), term["key"]
    assert {t["key"]: t["relates_to_metric"] for t in data["terms"]}["vpd"] == "probe_vpd"
    assert {t["key"]: t["relates_to_metric"] for t in data["terms"]}["ppfd"] is None
    by_key = {t["key"]: t for t in data["terms"]}
    assert {"lollipopping", "defoliation", "mainlining"} <= set(by_key)
    for key in ("lollipopping", "defoliation", "mainlining"):
        assert by_key[key]["sources"] == ["G28"] and by_key[key]["kind"] == "process", key
    assert by_key["exhaust-and-circulation"]["relates_to_device"] == ["dfan_step", "cfan_step"]
    assert by_key["light-spectrum"]["relates_to_device"] == ["light.spectrum"]
    assert {k for k, t in by_key.items() if t["measurable"] and not t["relates_to_metric"]} == {"ppfd", "dli", "ec",
                                                                                              "ph"}
    # Hank does not advise topping an autoflower (the glossary's own note, source G28), the text says so (review 2)
    assert "Autoflower-Sorten" in by_key["topping"]["text"] and "autoflowering" in by_key["topping"]["text_en"]
    for term in data["terms"]:  # nothing about consumption or effects, as for the 19
        body = (term["text"] + term["text_en"]).lower()
        assert not any(word in body for word in ("thc", "rausch", "wirkung", "high werden", "effect")), term["key"]


@pytest.mark.parametrize("subject,key", [
    ("VPD", "vpd"), ("vdp", "vpd"), ("Dampfdruckdefizit", "vpd"), ("vapor pressure deficit", "vpd"),
    ("Luftfeuchtigkeit", "relative-humidity"), ("humidity", "relative-humidity"), ("rF", "relative-humidity"),
    ("PPFD", "ppfd"), ("Tageslichtintegral", "dli"), ("ein Kohlefilter", "carbon-filter"),
    ("Kohlefilter in der Box", "carbon-filter"), ("Überwässern", "overwatering"), ("LST", "low-stress-training"),
    ("Trichome", "trichome"), ("Toppen", "topping"), ("Lollipopping", "lollipopping"), ("lolipopping", "lollipopping"),
    ("untere Triebe entfernen", "lollipopping"), ("Entlauben", "defoliation"), ("defoliate", "defoliation"),
    ("Mainlining", "mainlining"), ("Unterschied zwischen Abluft und Umluft", "exhaust-and-circulation"),
])
def test_glossary_finds_terms_by_alias_in_both_languages(subject: str, key: str) -> None:
    term = asking.glossary().find(asking.words(subject))
    assert term is not None and term.key == key, subject


@pytest.mark.parametrize("subject", ["Schwefelmangel", "die beste Luftfeuchte für die Blüte", "der Plan für heute",
                                     "Dampfdruck", "Vollspektrum"])
def test_glossary_finds_nothing_for_other_subjects(subject: str) -> None:
    # "Dampfdruck" and "Vollspektrum" were removed as aliases on purpose (they would lead to a wrong answer)
    assert asking.glossary().find(asking.words(subject)) is None, subject


# ---------- 1. question types ----------

@pytest.mark.parametrize("question,qtype", [
    ("Was ist VPD?", "explain"), ("Was bedeutet PPFD?", "explain"), ("Was heißt LST?", "explain"),
    ("Erklär mir Topping", "explain"), ("Kannst du mir VPD erklären?", "explain"), ("what is VPD", "explain"),
    ("What does VPD mean?", "explain"), ("Explain DLI", "explain"), ("What's a carbon filter?", "explain"),
    ("Wie warm ist es?", "value"), ("Wie feucht ist es in der Box?", "value"), ("Wie hoch ist die Pflanze?", "value"),
    ("How humid is it?", "value"), ("How is the humidity?", "value"), ("Was ist die Luftfeuchte gerade?", "value"),
    ("Wie mache ich LST?", "howto"), ("Wie kann ich die Luftfeuchte senken?", "howto"), ("How do I top a plant?", "howto"),
    ("Warum ist die Feuchte so hoch?", "why"), ("Why is the light at 60%?", "why"),
    ("Was ist los?", None), ("Kannst du das beheben?", None), ("Ich hab Trauermücken in der Erde", None),
    # typed in a hurry (review 1, MUST 4): the owner's first bug with one typo, chat filler at the end
    ("wht is vpd?", "explain"), ("whats ppfd lol", "explain"), ("was isn dli", "explain"), ("was is lolipopping",
                                                                                           "explain"),
    ("Wie senke ich die Luftfeuchtigkeit?", "howto"),
])
def test_question_types(question: str, qtype: str | None) -> None:
    assert asking.classify(question).qtype == qtype, question


def test_new_routes_are_known() -> None:
    assert {"explain", "reading"} <= set(ROUTES)


# ---------- 2. explain from the glossary ----------

def test_what_is_vpd_explains_first_then_the_reading_then_draft() -> None:
    [en] = ask([("what is VPD", "en")])
    assert en["route"] == "explain" and en["reason"] == "explain:vpd"
    lines = en["text"].split("\n")
    assert lines[0].startswith("VPD stands for vapor pressure deficit")  # the term first, never the reading alone
    assert lines[0].endswith("[1] [2] [3].")  # its three sources, numbered
    assert lines[1] == "VPD at the probe: no reading."  # F22 has no VPD reading: said, no number made up
    assert lines[2] == "From the glossary: draft, not yet approved."
    assert [s["id"] for s in en["sources"]] == ["G1", "G2", "G3"] and en["sources"][0]["url"].startswith("https://")
    [de] = ask([("Was ist VPD?", "de")])
    assert de["text"].startswith("VPD steht für Dampfdruckdefizit") and "Entwurf" in de["text"]


def test_a_term_with_a_metric_gives_its_reading_with_the_verdict_after_the_text() -> None:
    [de] = ask([("Was ist die Luftfeuchtigkeit?", "de")])
    lines = de["text"].split("\n")
    assert de["route"] == "explain" and lines[0].startswith("Die relative Luftfeuchte gibt in Prozent an")
    assert lines[1].startswith("Luftfeuchte am Fühler: 79 %, zu hoch (Ziel 45 bis 60 %")


def test_a_term_the_box_does_not_measure_says_so() -> None:
    [de] = ask([("Was ist PPFD?", "de")])
    lines = de["text"].split("\n")
    assert de["route"] == "explain" and lines[0].startswith("PPFD zählt, wie viele Lichtteilchen")
    assert lines[1] == "Die Box misst das nicht." and lines[2] == "Aus dem Glossar: Entwurf, noch nicht freigegeben."
    [en] = ask([("What is PPFD?", "en")])
    assert en["text"].split("\n")[1] == "The box does not measure this."


def test_explain_never_calls_the_model_and_is_final() -> None:
    [de] = ask([("Was ist Hysterese?", "de")])
    assert de["route"] == "explain" and de["draft_only"] is False and de["proposals"] == []


# ---------- 3. can help or not ----------

def test_lollipopping_the_owners_own_example_is_explained() -> None:
    [de] = ask([("Was ist Lollipopping?", "de")])
    lines = de["text"].split("\n")
    assert de["route"] == "explain" and lines[0].startswith("Beim Lollipopping entfernst du im unteren Drittel")
    assert lines[0].endswith("[1].") and [s["id"] for s in de["sources"]] == ["G28"]
    assert lines[1] == "Aus dem Glossar: Entwurf, noch nicht freigegeben."  # no "Die Box misst das nicht."
    [en] = ask([("was is lolipopping", "de")])  # spoken German with a typo: German, not English (review 1, S02)
    assert en["route"] == "explain" and en["lang"] == "de" and en["text"].startswith("Beim Lollipopping")


def test_a_typed_grow_question_without_a_card_is_no_data_not_offtopic() -> None:
    """Review 1, MUST 1: "Was ist Schwefelmangel?" got "Da bin ich der Falsche, ich bin ein einfacher Farmer". Since
    01.11.2026 the sulfur card answers it from the shelves (vgrow/hank/shelves.py); a growing question without any card
    still gets "nothing in my card file" (nodata-04, nodata-05 and the unknown cases of the eval set)."""
    [de] = ask([("Was ist Schwefelmangel?", "de")])
    assert de["route"] == "grow" and "Schwefel" in de["plain"], de["plain"]
    assert ask([("Wie wird das Wetter morgen?", "de")])[0]["route"] == "offtopic"  # off-topic stays off-topic


def test_an_off_topic_example_never_wins_over_a_glossary_term() -> None:
    [de] = ask([("Was ist der Unterschied zwischen Abluft und Umluft?", "de")])  # was: "Java und JavaScript"
    assert de["route"] == "explain" and de["text"].startswith("Die Abluft sorgt für den Luftaustausch")


def test_a_how_to_about_a_glossary_term_gives_its_text_and_says_there_is_no_guide() -> None:
    # v1.4: a technique with a method card gets the card's steps (tests/test_hank_methods.py); a glossary term
    # without one keeps the glossary's text and "no step-by-step guide"
    [en] = ask([("How do I do LST?", "en")])
    assert en["route"] == "explain" and en["reason"] == "method:howto:lst"
    assert "LST (low stress training): tying down, step by step (from the method card):" in en["text"].split("\n")
    [de] = ask([("Wie mache ich einen Steckling?", "de")])
    lines = de["text"].split("\n")
    assert de["route"] == "explain" and de["reason"] == "howto:cutting"
    assert lines[0].startswith("Ein Steckling ist") and lines[1] == "Eine Schritt-für-Schritt-Anleitung habe ich nicht."


def test_a_how_to_without_a_card_offers_the_techniques_not_the_box() -> None:
    [en] = ask([("how do I make my plant a hermie?", "en")])
    assert en["route"] == "no_data"
    assert en["text"] == ("There is nothing in my card file about that yet, I have no guide to hermaphrodite. I can "
                          "explain terms such as LST, Topping or Lollipopping.")


def test_a_no_data_question_about_a_glossary_term_answers_from_the_glossary() -> None:
    """Review 1, MUST 3: "kenne ich nur den Namen" for the Aktivkohlefilter, whose glossary text has the interval."""
    [de] = ask([("Wie oft muss ich den Aktivkohlefilter wechseln?", "de")])
    lines = de["text"].split("\n")
    assert de["route"] == "no_data" and de["gap"] is True  # still on the gap list: no card of its own
    assert lines[0] == "Eine eigene Karte habe ich dazu nicht, im Glossar steht zu Aktivkohlefilter:"
    assert "alle 3 Monate zu wechseln" in lines[1] and "nur den Namen" not in de["text"]
    assert lines[-1] == "Aus dem Glossar: Entwurf, noch nicht freigegeben."
    [dli] = ask([("was isn dli", "de")])
    assert dli["route"] == "explain" and dli["text"].startswith("DLI (Tageslichtintegral) ist")


def test_the_no_data_text_never_calls_a_glossary_term_only_a_name() -> None:
    from vgrow.hank.types import Route

    for name in ("Aktivkohlefilter", "DLI", "LST", "Lollipopping"):
        for qtype in (None, "explain", "why"):
            text = asking.no_data_text(Route("no_data", None, None, None, "info", "de", "x", qtype=qtype), name)
            assert "nur den Namen" not in text, (name, qtype)


def test_an_untyped_no_data_keeps_its_catalog_text() -> None:
    [de] = ask([("Wie viel Licht braucht eine Tomate?", "de")])
    assert de["route"] == "no_data" and de["text"].startswith("Dazu steht noch nichts in meiner Kartei.")


def test_status_questions_get_the_today_list() -> None:
    [de] = ask([("Wie geht es meiner Box?", "de")])
    assert de["route"] == "today" and de["text"].startswith("Luftfeuchte am Fühler: 79 %, zu hoch")
    [en] = ask([("How is my plant doing?", "en")])
    assert en["route"] == "today"
    assert ask([("Wie geht es dir?", "de")])[0]["route"] == "greet"


# ---------- 4. follow-ups ----------

def test_can_u_fix_it_after_what_is_vpd_is_about_vpd() -> None:
    _, fixed = ask([("what is VPD", "en"), ("can u fix it?", "en")])
    lines = fixed["text"].split("\n")
    assert fixed["route"] == "fix"
    assert lines[0] == "Right now the box gives no reading for VPD, so there is nothing I can fix."
    assert lines[1] == "For what else is off target, this helps, most urgent first:"
    assert lines[2].startswith("Humidity at the probe: 79%, too high") and lines[3].startswith("Light: 60%")
    assert lines[-1] == "In the chat I switch nothing without you: each card below waits for your confirmation."


def test_kannst_du_das_beheben_after_a_humidity_question_covers_the_humidity_only() -> None:
    _, fixed = ask([("Ist die Luftfeuchte gerade okay?", "de"), ("Kannst du das beheben?", "de")])
    assert fixed["route"] == "fix"
    assert fixed["text"].split("\n")[1].startswith("Luftfeuchte am Fühler: 79 %")
    assert "Licht: 60 %" not in fixed["text"]  # the topic was the humidity, not every problem
    assert [p["body"]["action"] for p in fixed["proposals"]] == ["dfan", "plant_stage"]


def test_what_about_humidity_after_an_explanation_is_an_explanation() -> None:
    _, about = ask([("what is VPD", "en"), ("what about humidity?", "en")])
    assert about["route"] == "explain" and about["reason"] == "followup:about;explain:relative-humidity"
    assert about["text"].startswith("Relative humidity gives, in percent")


def test_und_die_luftfeuchte_after_a_reading_is_a_reading() -> None:
    _, about = ask([("Wie warm ist es?", "de"), ("Und die Luftfeuchte?", "de")])
    assert about["route"] == "grow" and about["reason"] == "followup:about;state:metric:probe_humidity"
    assert about["text"].startswith("Luftfeuchte am Fühler: 79 %")


def test_pronoun_why_and_meaning_follow_ups() -> None:
    first, again, why, meaning = ask([("Ist die Luftfeuchte gerade okay?", "de"), ("Und das?", "de"),
                                      ("warum?", "de"), ("Was bedeutet das?", "de")])
    assert again["reason"].startswith("followup:again;state:metric:probe_humidity")
    assert why["reason"].startswith("followup:why;state:metric:probe_humidity")
    assert meaning["route"] == "explain" and meaning["reason"] == "followup:explain;explain:relative-humidity"


def test_follow_ups_that_broke_in_review_1() -> None:
    _, again = ask([("Wie hoch ist die Pflanze?", "de"), ("Und das?", "de")])
    assert again["route"] == "reading" and again["reason"] == "followup:again;reading:plant_height"  # was: feeding
    _, _, now = ask([("Was ist Luftfeuchte?", "de"), ("Danke!", "de"), ("und wie ist sie gerade?", "de")])
    assert now["reason"].startswith("followup:again;state:metric:probe_humidity")
    _, high = ask([("what is PPFD?", "en"), ("and how high is it now?", "en")])
    assert high["route"] == "reading" and high["text"].startswith("The box does not measure PPFD.")
    _, how = ask([("what is lollipopping", "en"), ("how do I do that?", "en")])
    # v1.4: the how-to of a technique with a method card is that card's steps
    assert how["reason"] == "followup:howto;method:howto:lollipopping" and "step by step" in how["text"]


def test_a_no_data_name_stays_the_topic_for_a_how_to() -> None:
    _, how = ask([("What is a hermie?", "en"), ("how do I do that?", "en")])
    assert how["route"] == "no_data" and how["reason"].startswith("followup:howto;")
    assert how["text"].startswith("There is nothing in my card file about that yet, I have no guide to ")


def test_a_term_without_a_value_has_no_again_or_why_follow_up() -> None:
    """Review 2: "Was sind Trichome?" then "und das?" got the climate reading, "warum?" ripeness text."""
    _, again, why = ask([("Was sind Trichome?", "de"), ("und das?", "de"), ("warum?", "de")])
    assert not again["reason"].startswith("followup:") and not why["reason"].startswith("followup:")


def test_the_topic_book_prunes_old_chats_and_caps_the_chat_id() -> None:
    clock = Clock()
    book = asking.TopicBook(clock=clock)
    for n in range(50):
        book.remember(f"chat-{n}", asking.Topic(metric="probe_vpd"))
    clock.now += asking.TOPIC_TTL + 1
    book.remember("fresh", asking.Topic(metric="probe_vpd"))
    assert list(book.topics) == ["fresh"]
    book.remember("x" * 500, asking.Topic(metric="probe_vpd"))
    assert max(len(cid) for cid in book.topics) == asking.CLIENT_ID_MAX
    assert book.get("x" * 500) is not None


def test_without_a_topic_nothing_is_written_out() -> None:
    [alone] = ask([("Und das?", "de")])
    assert not alone["reason"].startswith("followup:")
    [fixed] = ask([("can u fix it?", "en")])
    assert fixed["route"] == "fix" and fixed["text"].startswith("What helps, most urgent first:")


def test_the_topic_lasts_15_minutes_and_belongs_to_its_chat() -> None:
    clock = Clock()

    async def go() -> tuple[str, str, str]:
        hank = core.Hank(live=f22, index=core.shared_index(), clock=clock)
        try:
            await hank.ask("what is VPD", client_id="a", lang="en", model=False)
            other = await hank.ask("can u fix it?", client_id="b", lang="en", model=False)
            clock.now += 14 * 60
            fresh = await hank.ask("can u fix it?", client_id="a", lang="en", model=False)
            clock.now += 16 * 60  # past 15 minutes since the fix answer (which kept VPD as the topic)
            late = await hank.ask("can u fix it?", client_id="a", lang="en", model=False)
        finally:
            await hank.stop()
        return other["answer"]["text"], fresh["answer"]["text"], late["answer"]["text"]

    other, fresh, late = asyncio.run(go())
    assert other.startswith("What helps, most urgent first:")  # another chat has no topic
    assert fresh.startswith("Right now the box gives no reading for VPD")
    assert late.startswith("What helps, most urgent first:")  # the topic expired


def test_topic_book_keeps_a_topic_through_thanks_and_greetings() -> None:
    _, thanks, fixed = ask([("what is VPD", "en"), ("Thanks!", "en"), ("can u fix it?", "en")])
    assert thanks["route"] == "thanks" and fixed["text"].startswith("Right now the box gives no reading for VPD")


def test_fix_scope_by_wording() -> None:
    topic = asking.Topic(qtype="explain", metric="probe_vpd", term="vpd", problems=("light_percent:low",),
                         label={"de": "VPD", "en": "VPD"})
    assert asking.fix_scope("can you fix it", topic)[0] == ["probe_vpd:high", "probe_vpd:low"]
    assert asking.fix_scope("can you fix those things", topic)[0] == ["light_percent:low"]
    assert asking.fix_scope("fix everything", topic) == (None, None)
    assert asking.fix_scope("kannst du alles beheben", topic) == (None, None)
    assert asking.fix_scope("can you fix it", None) == (None, None)


# ---------- 5. height and bloom start ----------

def test_height_answer_names_the_day_it_was_measured() -> None:
    [de] = ask([("Wie hoch ist die Pflanze?", "de")])
    assert de["route"] == "reading" and de["text"] == "Die Pflanze ist 61 cm hoch, gemessen am 22.10."
    [en] = ask([("How tall is my plant?", "en")])
    assert en["text"] == "The plant is 61 cm tall, measured on Oct 22."
    [old] = ask([("Wie hoch ist die Pflanze?", "de")], live=lambda lang: with_height(f22(lang), "2026-10-18"))
    assert old["text"] == ("Die Pflanze ist 61 cm hoch, gemessen am 18.10. Das ist 4 Tage her: trag die Höhe von "
                           "heute in der Pflanzenkarte ein, dann sehe ich sofort, wo wir stehen.")


def test_a_term_the_box_does_not_measure_as_a_value_question() -> None:
    [de] = ask([("Wie hoch ist der PPFD gerade?", "de")])
    assert de["route"] == "reading" and de["text"] == "PPFD misst die Box nicht. Was PPFD ist, kann ich dir erklären."
    assert ask([("Wie viel Licht braucht eine Tomate in der VGrow?", "de")])[0]["route"] != "reading"


def test_a_term_of_a_box_device_gives_the_device_state_not_not_measured() -> None:
    """Review 1, MUST 2: "Wie hoch ist die Abluft gerade?" got "misst die Box nicht" (the exhaust is on step 3)."""
    [de] = ask([("Wie hoch ist die Abluft gerade?", "de")])
    assert de["route"] == "reading" and de["text"].startswith("Gerade: Abluft Stufe 3, Umluft kein Wert.")
    [en] = ask([("how is the light spectrum?", "en")])
    assert en["text"] == "The lamp is on the flower spectrum right now. I can explain what light spectrum is."
    [why] = ask([("what is the exhaust fan for?", "en")])
    assert why["text"].split("\n")[1] == "Right now: exhaust fan step 3, circulation fan no value."
    assert "does not measure" not in why["text"]


def test_not_measured_only_for_a_quantity_and_ec_ph_with_the_last_logged_value() -> None:
    """Review 1, SHOULD 2: "Die Box misst das nicht." under topping, LST and trichomes read silly."""
    for question in ("Was ist Topping?", "Was sind Trichome?", "Was ist LST?", "Was ist ein Steckling?"):
        assert "misst" not in ask([(question, "de")])[0]["text"], question
    assert ask([("Was ist DLI?", "de")])[0]["text"].split("\n")[1] == "Die Box misst das nicht."

    def logged(lang: str) -> dict[str, Any]:
        live = f22(lang)
        live["plant_view"]["water"] = [{"time": "2026-10-20T09:00:00", "ph": 6.2, "ec": 1.4},
                                       {"time": "2026-10-22T09:00:00", "ph": None, "ec": 1.6}]
        return live

    [ec] = ask([("Was ist EC?", "de")], live=logged)
    assert ec["text"].split("\n")[1] == "Die Box misst das nicht; zuletzt eingetragen: EC 1,6 mS/cm am 22.10."
    [ph] = ask([("What is pH?", "en")], live=logged)
    assert ph["text"].split("\n")[1] == "The box does not measure this; last logged: pH 6.2 on Oct 20."
    assert ask([("Was ist EC?", "de")])[0]["text"].split("\n")[1] == ("Die Box misst das nicht, und eingetragen hast "
                                                                      "du noch keinen Wert.")


def test_the_day_of_flowering_is_a_reading_with_where_the_date_comes_from() -> None:
    """Review 1, SHOULD 3 (V09 got off-topic) and MUST 8 (never "1 Tag" from the camera's date)."""
    [de] = ask([("Welcher Tag der Blüte ist heute?", "de")])
    assert de["route"] == "reading" and de["reason"] == "reading:bloom_day"
    assert de["text"] == ("Der Blütebeginn ist nur geschätzt: spätestens 22.10., denn das erste ausgewertete "
                          "Kamerabild zeigte schon weiße Härchen. Nach den Tagesfenstern der Phasenkarte (grober "
                          "Rückfall) begann die Blüte eher um den 1.10.; heute wäre Mitte der Blüte (seit 15.10.). "
                          "Trag den Tag mit den ersten weißen Härchen in der Pflanzenkarte unter Blütebeginn ein.")

    def owner(lang: str) -> dict[str, Any]:
        live = f22(lang)
        live["plant_view"]["bloom"] = {"date": "2026-10-01", "by": "owner", "flower_day": 21, "estimated": False,
                                       "latest": False, "suggest": None}
        return live

    [en] = ask([("Which flowering week is it?", "en")], live=owner)
    assert en["text"] == "Flowering for 21 days (flowering week 4), counted from Oct 1, entered by you."
    assert ask([("Wie viel Dünger in Blütewoche 3?", "de")])[0]["route"] != "reading"  # a feeding question


def test_today_asks_for_an_old_height_and_once_for_the_real_bloom_start() -> None:
    def live(lang: str) -> dict[str, Any]:
        return with_height(f22(lang), "2026-10-19")

    first, second = ask([("Was ist heute zu tun?", "de"), ("Was ist heute zu tun?", "de")], live=live)
    lines = first["text"].split("\n")
    assert len(lines) <= today.MAX_LINES
    stage = next(line for line in lines if line.startswith("Phase bestätigen"))
    # an upper bound, never "1 Tag" (owner: "Stage ist bestimmt nicht ein Tag"), with the stage card's rough window
    # feeding v2 (spec 4.10): plant.json's start is only approximate, so its question shares the stage line, last
    assert stage.endswith("Blütebeginn spätestens 22.10.: das erste ausgewertete Kamerabild zeigte schon weiße "
                          "Härchen. Nach den Tagesfenstern der Phasenkarte (grober Rückfall) eher um den 1.10. Wann "
                          "hast du die ersten weißen Härchen gesehen? Trag den Tag in der Pflanzenkarte unter "
                          "Blütebeginn ein. Der Start 10.09.2026 ist nur ungefähr. Weißt du den Tag der Keimung? Dann "
                          "trag ihn in der Pflanzenkarte ein.")
    assert "Höhe heute eintragen: zuletzt 61 cm am 19.10." in lines
    again = next(line for line in second["text"].split("\n") if line.startswith("Phase bestätigen"))
    assert again.endswith("Blütebeginn eintragen: bisher nur spätestens 22.10. bekannt. Der Start 10.09.2026 ist "
                          "nur ungefähr: den echten Tag in der Pflanzenkarte eintragen.")  # asked once, then shorter
    assert json.loads((config.STATE_DIR / "hank" / "asked.json").read_text(encoding="utf-8")) == {
        "bloom_start": "2026-10-22", "start_date": "approx:2026-09-10"}


def test_today_has_no_bloom_question_once_the_owner_gave_the_date() -> None:
    live = f22("de")
    live["plant_view"]["bloom"] = {**live["plant_view"]["bloom"], "by": "owner", "estimated": False,
                                   "date": "2026-10-01"}
    found, _ = today.lines(live, "de")
    assert not any("Härchen" in line or "Blütebeginn" in line for line in found)
    assert not any("Höhe" in line for line in found)  # the height of today is there


def test_the_digest_names_the_bloom_start_as_an_upper_bound_and_the_height() -> None:
    """The model sees "spätestens 22.10." (never one day of flowering) and today's height (owner: "dass die KI
    sofort sieht, wo wir stehen"; both reviews, SHOULD 1 and 4)."""
    live = f22("de")
    stage = next(g for g in livestate.collect(live).groups("de") if g.key == "stage").text
    assert stage.endswith("nur geschätzt, nicht bestätigt. Blütebeginn spätestens 22.10. Höhe 61 cm (22.10.).")
    en = next(g for g in livestate.collect(f22("en")).groups("en") if g.key == "stage").text
    assert en.endswith("Flowering start Oct 22 at the latest. Height 61 cm (Oct 22).")
    live["plant_view"]["bloom"] = {**live["plant_view"]["bloom"], "by": "owner", "date": "2026-10-01"}
    assert "Blütebeginn 1.10., von dir eingetragen." in livestate.collect(live).digest("de")


# ---------- order: climate before light ----------

def test_traffic_light_and_fix_put_the_climate_first_and_the_light_last() -> None:
    found = nudges.assess(f22("de"))
    sentence = found.text["de"]
    assert sentence.index("Luftfeuchte") < sentence.index("Licht")
    assert found.problems == ["probe_humidity:high", "light_percent:low"]
    lines = fix.compose(f22("de"), "de", now=T0).lines
    assert lines[1].startswith("Luftfeuchte") and lines[2].startswith("Licht")


# ---------- 6. texts ----------

def test_catalog_parity_and_registration() -> None:
    assert asking.check() == [] and fix.check() == [] and today.check() == []
    for key in asking.DE:
        assert i18n.t(key, "de") == asking.DE[key] and i18n.t(key, "en") == asking.EN[key]
        assert EM_DASH not in asking.DE[key] + asking.EN[key]


def test_router_routes_typed_questions_without_the_search(index: Index) -> None:
    router = Router(index)
    got = router.route("Was ist VPD?", lang_hint="de", live=f22("de"))
    assert got.name == "explain" and got.retrieval is None and got.term == "vpd"
    got = router.route("Wie hoch ist die Pflanze?", lang_hint="de", live=f22("de"))
    assert got.name == "reading" and got.term == "plant_height"
    # the gates come first: a blocked topic in an explain question is the health reply
    assert router.route("Was ist THC?", lang_hint="de", live=f22("de")).name == "health"


# ---------- v1.3: the rest of review 1 ----------

def test_why_about_the_climate_opens_with_what_the_box_is_doing() -> None:
    [why] = ask([("Warum ist die Luftfeuchte so hoch?", "de")])
    assert why["text"].split("\n")[0] == "In der Box gerade: Abluft Stufe 3, Luftbefeuchter (USB 2) aus."
    [auto] = ask([("Wieso wartet die Automatik?", "de")])
    assert auto["text"].split("\n")[0] == ("Die Befeuchter-Automatik wartet, weil die Phase nur geschätzt ist: sie "
                                           "schaltet erst, wenn du die Phase bestätigst.")


def test_a_switching_wish_opens_with_never_switching_and_its_card() -> None:
    [de] = ask([("Kannst du die Lampe auf 100 % stellen?", "de")])
    assert de["text"].split("\n")[0] == ("Im Chat schalte ich nichts ohne dich: die Karte unten (Licht auf 85 %) "
                                         "wartet auf deinen Tipp.")
    assert "light" in [p["body"]["action"] for p in de["proposals"]]  # the card, not a switch


def test_how_tall_it_will_get_is_the_strain_card_not_todays_height() -> None:
    [de] = ask([("Wie hoch wird meine Pflanze noch?", "de")])
    assert de["route"] == "reading" and de["reason"] == "reading:height_forecast"
    assert de["text"].startswith("Die Pflanze ist 61 cm hoch, gemessen am 22.10. Laut Sortenkarte, Höhe indoor: bis "
                                 "120 cm; Zamnesia 90 cm. VGrow-Wuchshöhe 92 cm.")
    assert "THC" not in de["text"]


def test_order_is_an_everyday_word_not_a_typo_of_odor() -> None:
    [en] = ask([("can you order more nutrients?", "en")])
    assert en["route"] != "clarify"


def test_watering_names_todays_stage_only() -> None:
    [de] = ask([("Wie gieße ich richtig?", "de")])
    assert de["text"].startswith("Frühe Blüte (Stretch), Gießen:")
    assert "Keimling, Gießen" not in de["text"] and "Späte Blüte (Reife), Gießen" not in de["text"]
    [late] = ask([("Wie gieße ich in der späten Blüte?", "de")])  # a named stage keeps its own rows
    assert "Späte Blüte (Reife), Gießen" in late["text"]


def test_only_a_single_growing_term_turns_off_topic_into_no_data() -> None:
    """MUST 1 without new false hits: questions shaped like the acceptance's off-topic ones (acc-16, acc-17, their
    wordings stay unseen here) stay off-topic, and so does a single term with no growing word in it."""
    assert ask([("What's a nice name for a goldfish?", "en")])[0]["route"] == "offtopic"
    assert ask([("How do I get my baby to eat vegetables?", "en")])[0]["route"] == "offtopic"
    assert ask([("Was ist Quantenphysik?", "de")])[0]["route"] == "offtopic"
    [rot] = ask([("Was ist Wurzelfäule?", "de")])  # matched "Was ist die Wurzel aus 144?" before
    assert rot["route"] == "no_data" and rot["text"].startswith("Dazu steht noch nichts in meiner Kartei, zu "
                                                                "Wurzelfäule kenne ich nur den Namen.")
    assert ask([("Was ist die Wurzel aus 144?", "de")])[0]["route"] == "offtopic"
