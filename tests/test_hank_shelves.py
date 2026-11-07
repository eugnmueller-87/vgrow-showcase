"""Hank's shelves (vgrow/hank/shelves.py; owner, 01.11.2026: "move everything he's collecting into a RAG ... and the
rule that he checks at first for everything"; "if he doesn't understand the question ... He needs to suggest whether we
mean a certain topic or if he doesn't know").

The problem, plant-health, crop and process cards and the fertilizer market sit in an index of their own; a refusal
or a weak hit asks it first: a clear card answers, a near one becomes a question back with buttons, a thing no card
names keeps the honest "no card", an off-topic question stays off-topic. The live state is the eval fixture F22 with
the day frozen, as in the strain tests.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from hank_eval import fixtures
from vgrow.hank import core, shelves
from vgrow.hank.kartei_index import SHELF_FOLDERS, Index
from vgrow.hank.router import Router

EM_DASH = chr(0x2014)


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def f22(lang: str = "de") -> dict[str, Any]:
    sources = fixtures.build("F22", lang=lang).sources()
    sources.pop("ledger")
    return sources


def route(q: str, lang: str = "de"):
    return Router(core.shared_index()).route(q, lang_hint=lang, live=f22(lang))


def ask(q: str, lang: str = "de") -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        hank = core.Hank(live=f22, index=core.shared_index())
        try:
            got = await hank.ask_full(q, client_id="shelves", lang=lang, model=False)
            return {**core.answer_dict(got.answer), "reason": got.route.reason, "name": got.route.name,
                    "form": got.route.form, "cards": got.route.cards}
        finally:
            await hank.stop()

    return asyncio.run(go())


def test_the_shelf_folders_never_enter_the_main_index_and_fill_their_own() -> None:
    main = core.shared_index()
    assert not [p for p in main._state.passages if p.kind in SHELF_FOLDERS]
    shelf = Index.load(shelves=True)
    kinds = {p.kind for p in shelf._state.passages}
    assert set(SHELF_FOLDERS) <= kinds and "nutrients" in kinds
    assert not [p for p in shelf._state.passages if p.kind in ("stages", "methods", "run", "strains")]
    aphid = [p for p in shelf._state.passages if p.card == "aphid"]
    assert aphid and all(p.meta.get("package") == "cannabis" for p in aphid)
    assert "Blattlaus" in aphid[0].title["de"]  # the aliases are in the title, so the name finds it


@pytest.mark.parametrize(("q", "lang", "card"), [
    ("Wie gieße ich Basilikum?", "de", "crops/basil"),
    ("Ich hab Trauermücken in der Erde, was kann ich dagegen machen?", "de", "problems/fungus_gnat"),
    ("Silbrige Flecken auf den Blättern mit schwarzen Pünktchen", "de", "problems/thrips"),
    ("can I grow lettuce in the box?", "en", "crops/lettuce-leaf"),
])
def test_a_clear_card_answers_with_its_own_passages_and_sources(q: str, lang: str, card: str) -> None:
    got = route(q, lang)
    assert got.name == "grow" and got.form == "shelf" and got.cards == [card], (q, got.reason)
    assert {f"{h.passage.kind}/{h.passage.card}" for h in got.retrieval.hits} == {card}
    answer = ask(q, lang)
    assert answer["sources"] or answer["more"]["sources"], answer


def test_a_named_fertilizer_card_is_told_by_its_card() -> None:
    for q, lang, card in (("Was ist BioTabs?", "de", "nutrients/biotabs"),
                          ("tell me about Athena Pro", "en", "nutrients/athena-pro")):
        a = ask(q, lang)
        assert a["name"] == "explain" and a["cards"] == [card], (q, a["reason"])
        assert "don't know" not in a["text"] and a["sources"], a["text"]


def test_a_tie_is_a_question_back_with_buttons_never_a_guess() -> None:
    a = ask("there are tiny webs between the leaves and little dots", "en")
    assert a["form"] == "shelf_suggest" and a["gap"], a["reason"]
    assert a["text"].startswith("I am not sure about that. Do you mean Spider mites")
    assert a["chips"][0] == "Tell me more about Spider mites"
    assert "Microgreens" not in a["text"]  # only the cards close to the best one
    b = ask("What should I do for the plant with yellow leaves?", "en")  # nitrogen, sulfur, magnesium all fit
    assert b["form"] == "shelf_suggest" and "Nitrogen deficiency" in b["text"] and " or " in b["text"], b["text"]
    assert all(c.startswith("Tell me more about ") for c in b["chips"])
    c = ask("Was soll ich bei der Pflanze gegen gelbe Blätter machen?")  # names no crop: no crop card
    assert c["form"] == "shelf_suggest" and not [x for x in c["cards"] if x.startswith("crops/")], c["cards"]


@pytest.mark.parametrize(("q", "lang"), [
    ("Wie oft muss ich meine Gurke gießen?", "de"), ("Welche Erde braucht eine Orchidee?", "de"),
    ("how do I make my plant a hermie?", "en"),
    ("How much fertilizer does a cucumber plant need per week?", "en"),
    ("When should I repot my monstera and into what size pot?", "en"),
])
def test_a_thing_no_card_names_keeps_the_honest_no(q: str, lang: str) -> None:
    got = route(q, lang)
    assert got.name == "no_data" and not (got.form or "").startswith("shelf"), (q, got.reason)


@pytest.mark.parametrize(("q", "lang"), [
    ("Can you fix my car?", "en"), ("who is the president of France?", "en"),
    ("Schreib mir ein Gedicht über den Herbst", "de"), ("Wie mach ich ein gutes Pesto aus Basilikum?", "de"),
])
def test_off_topic_stays_off_topic(q: str, lang: str) -> None:
    got = route(q, lang)
    assert got.name == "offtopic", (q, got.reason)


def test_a_climate_answer_of_the_main_cards_keeps_its_way() -> None:
    for q in ("Wie viel Feuchte brauche ich in der späten Blüte?", "Wie gieße ich richtig?",
              "Bei wie viel Grad und wie viel Prozent trockne ich die Blüten?"):
        got = route(q)
        assert got.name == "grow" and not (got.form or "").startswith("shelf"), (q, got.reason)


def test_the_safety_gates_still_come_first() -> None:
    assert route("how much THC will my Purple Punch have?", "en").name == "health"
    assert route("Wo kaufe ich Samen von Purple Punch?").name == "sale"


def test_the_curing_card_knows_the_owners_word() -> None:
    hits = shelves.Shelves(core.shared_index()).search("Wie lange muss ich fermentieren?", "de")
    assert hits and hits[0].card == "processes/curing", hits[:3]


def test_decide_answers_only_a_clear_card() -> None:
    hit = shelves.CardHit
    assert shelves.decide([hit("a/x", 0.8), hit("a/y", 0.5)], "no_data", "domain:0.3", 0.3) == "answer"
    assert shelves.decide([hit("a/x", 1.0), hit("a/y", 1.0)], "no_data", "domain:0.3", 0.3) == "suggest"
    assert shelves.decide([hit("a/x", 0.5), hit("a/y", 0.3)], "no_data", "domain:0.3", 0.3) == "suggest"
    assert shelves.decide([hit("a/x", 0.35)], "offtopic", "none:0.2", 0.2) is None
    assert shelves.decide([hit("a/x", 0.7)], "grow", "hit:0.6", 0.6) is None  # a main answer stays
    assert shelves.decide([hit("a/x", 1.0), hit("a/y", 0.5)], "grow", "hit:0.41", 0.41) is None  # even a weak one


def test_the_catalog_has_both_languages_and_no_em_dash() -> None:
    assert shelves.check() == []
    assert not [k for k, v in {**shelves.DE, **shelves.EN}.items() if EM_DASH in v]
    assert re.search(r"{items}", shelves.DE["hank.shelf.suggest"]) and re.search(r"{items}", shelves.EN["hank.shelf.suggest"])
