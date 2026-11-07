"""Hank v1.4, the owner's words of 23.10.2026: "What we also need to add is how to top the nodes properly, in the wiki
and in Hank's knowledge base. I want him to be able to explain how to top the node properly since this is a very
small space. It's important to have one proper node and we're going basically after the heads, so like Hydra kind of."

Built in vgrow/hank/methods.py and wikicards.py (with kartei_index.py, router.py, core.py), each part tested here:
1. the index reads the ten method cards (label, aliases, what, steps, when, autoflower, box fit, risks, with their
   source ids, status and package), the search and the typo tolerance find them;
2. how-to questions answer from the card: steps first, then when, autoflower, box fit as the card words them, the
   first sourced risk, the sources, the draft note; explain questions keep the glossary text plus one line;
3. the owner's words for more heads route to the mainlining and topping cards, from a question pattern in code;
4. the stage window: before flowering nothing extra, while flowering one plain line, first;
5. the cannabis package switched off: no answer from its cards or glossary terms, said so;
6. "Erzähl mir mehr über <Titel>" for every card of every folder the wiki button is shown for.

The live state is the eval fixture F22 (stage early flower, only estimated from the camera) with the day frozen.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from hank_eval import fixtures
from vgrow import config, wiki
from vgrow.hank import asking, core, kartei_index, methods, wikicards
from vgrow.hank.kartei_index import Index

REPO = Path(__file__).resolve().parent.parent
METHODS = REPO / "kartei" / "methods"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
CARDS = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(METHODS.glob("*.json"))}
CANNABIS = {k for k, c in CARDS.items() if c.get("package") == "cannabis"}


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def f22(lang: str = "de") -> dict[str, Any]:
    sources = fixtures.build("F22", lang=lang).sources()
    sources.pop("ledger")
    return sources


def staged(key: str, set_by: str, label: str) -> Any:
    def live(lang: str = "de") -> dict[str, Any]:
        out = copy.deepcopy(f22(lang))
        out["plant_view"]["stage"] = {**out["plant_view"]["stage"], "key": key, "set_by": set_by, "label": label}
        return out
    return live


def packages(on: list[str]) -> None:
    path = config.STATE_DIR / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"packages": on}), encoding="utf-8")


def ask(questions: list[tuple[str, str]], *, live: Any = None, client_id: str = "chat") -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        hank = core.Hank(live=live or f22, index=core.shared_index())
        out = []
        try:
            for question, lang in questions:
                got = await hank.ask_full(question, client_id=client_id, lang=lang)
                out.append({**core.answer_dict(got.answer), "reason": got.route.reason, "plain": got.answer.plain()})
        finally:
            await hank.stop()
        return out

    return asyncio.run(go())


def lines(answer: dict[str, Any]) -> list[str]:
    return answer["text"].split("\n")


# ---------- 1. the index reads the method cards ----------

def test_the_ten_method_cards_are_there_and_eight_carry_the_cannabis_package() -> None:
    assert set(CARDS) == {"topping", "fimming", "mainlining", "lst", "scrog", "supercropping", "lollipopping",
                          "defoliation", "basil_pinching", "tomato_suckering"}
    assert CANNABIS == set(CARDS) - {"basil_pinching", "tomato_suckering"}
    assert all(c["status"] == "draft" for c in CARDS.values())


def test_index_reads_every_field_of_every_method_card_with_its_sources() -> None:
    index = Index.load()
    assert not [c for c in index.skipped_cards if c.startswith("methods/")]
    for key, card in CARDS.items():
        found = [p for p in index.passages if p.kind == "methods" and p.card == key]
        paths = {p.meta["path"] for p in found}
        assert {"what", "when", "box_fit", "risks"} <= paths, key
        assert {f"steps.{i}" for i in range(len(card["steps"]))} <= paths, key
        if card.get("autoflower"):
            assert "autoflower" in paths, key
        for p in found:
            assert p.status == "draft" and p.meta["package"] == card.get("package") and p.meta["method"] == key
            assert p.title["de"].startswith(card["label"]) and p.title["en"].startswith(card["label_en"])
            assert set(p.sources) <= set(card["sources"]), (key, p.id)
            assert all(sid in p.text["de"] for sid in p.sources), (key, p.id)  # ids its own text cites
            assert not p.twin_missing, p.id
        what = next(p for p in found if p.meta["path"] == "what")
        for alias in card["aliases"]["en"]:
            assert alias in what.title["en"], (key, alias)  # the card's own aliases, nothing more


def test_method_cards_stay_out_of_the_default_search_and_cover_only_their_technique() -> None:
    index = Index.load()
    assert "methods" in kartei_index.NOT_BY_DEFAULT
    climate = index.search("Wie warm soll es in der frühen Blüte sein?")
    assert not [h for h in climate.hits if h.passage.kind == "methods"]
    assert index.covered("topping") and index.covered("lollipopping") and index.covered("fimming")
    assert not index.covered("tomato")  # the suckering card mentions tomatoes; it is no card about them
    assert not index.covered("cuttings")  # "apical cut" is an alias of topping, no cutting


@pytest.mark.parametrize("question,key", [
    ("Topen", "topping"), ("Lolipopping", "lollipopping"), ("Mainlinig", "mainlining"), ("Wie toppe ich?", "topping"),
    ("how do I top my plant", "topping"), ("Manifolding", "mainlining"), ("FIM", "fimming"), ("ScrOG", "scrog"),
    ("Herunterbinden", "lst"), ("Ausgeizen", "tomato_suckering"), ("Entlauben", "defoliation"),
    ("toppen", "topping"), ("Super Cropping", "supercropping"),
])
def test_typo_tolerant_names_find_the_card(question: str, key: str) -> None:
    assert methods.named(question).keys[:1] == [key], question


def test_top_alone_is_no_method_word() -> None:
    for question in ("Do lettuce seeds need light or should I press them on top?", "the top of the plant is pale"):
        assert "topping" not in methods.named(question).keys, question


def test_the_method_search_finds_the_card_by_a_typo() -> None:
    index = Index.load()
    for question, key in (("Lolipopping", "lollipopping"), ("Topen", "topping"), ("Mainlinig", "mainlining")):
        hits = index.search(question, kinds=["methods"]).hits
        assert hits and key in {h.passage.card for h in hits[:3]}, question


def test_a_question_that_names_a_method_and_asks_something_else_is_searched_in_that_card() -> None:
    [de] = ask([("Hilft Topping gegen die Höhe in der Box?", "de")])
    assert de["route"] == "grow" and de["reason"] == "method:topping"
    assert all(s["id"].startswith("M") for s in de["sources"])
    [tomato] = ask([("Wie toppe ich eine Tomate?", "de")])
    assert tomato["route"] == "no_data"  # no card covers the tomato: no answer from the topping card


# ---------- 2. how-to and explain ----------

def test_how_to_top_gives_the_steps_first_then_when_autoflower_box_fit_risk_sources_draft() -> None:
    card = CARDS["topping"]
    [de] = ask([("Wie toppe ich richtig?", "de")], live=staged("veg", "manual", "Wachstum"))
    got = lines(de)
    assert de["route"] == "explain" and de["reason"] == "method:howto:topping" and de["draft_only"] is False
    assert got[0] == "Topping (Triebspitze abschneiden), Schritt für Schritt (aus der Methodenkarte):"
    steps = [f"{n}. {s}" for n, s in enumerate(card["steps"], 1)]
    assert got[1:1 + len(steps)] == steps  # word for word, numbered, first
    rest = got[1 + len(steps):]
    assert rest[0] == f"Zeitpunkt: {card['when']}"
    assert rest[1] == f"Autoflower: {card['autoflower']}"
    assert rest[2] == f"In der VGrow: {card['box_fit']}"
    assert rest[3] == ("Risiko: High-Stress-Training braucht laut Ijoma et al. längere Erholung und kann bei falscher "
                       "Ausführung bleibend schaden (M7, Tabelle 1, gilt für HST insgesamt).")
    assert rest[4] == "Aus der Methodenkarte: Entwurf, noch nicht freigegeben." and len(rest) == 5
    assert "(M18, Erfahrungsquelle)" in de["text"]  # the experience-source marks stay
    ids = [s["id"] for s in de["sources"]]
    assert ids == sorted(ids, key=lambda s: int(s[1:])) and {"M7", "M18", "M20", "M24"} <= set(ids)
    assert {"M2", "M3", "M4", "M5", "M6"} <= set(ids)  # "M1 bis M6" cites the range
    assert set(ids) <= set(card["sources"])


def test_how_to_in_english_and_for_mainlining() -> None:
    [en] = ask([("how do I top my plant", "en")], live=staged("veg", "manual", "Vegetative"))
    assert en["reason"] == "method:howto:topping"
    assert lines(en)[1] == f"1. {CARDS['topping']['steps_en'][0]}"
    assert f"When: {CARDS['topping']['when_en']}" in lines(en)
    [de] = ask([("Wie mache ich Mainlining?", "de")], live=staged("veg", "manual", "Wachstum"))
    assert de["reason"] == "method:howto:mainlining"
    assert lines(de)[1] == f"1. {CARDS['mainlining']['steps'][0]}"


def test_main_risk_skips_an_introduction_without_a_source() -> None:
    lib = methods.library()
    risk = methods.main_risk(lib.get("defoliation"), "de")
    assert risk and not risk.startswith("Die ersten vier Punkte") and re.search(r"\bM\d+\b", risk)
    for key in CARDS:
        assert methods.main_risk(lib.get(key), "de"), key  # every card has a sourced risk sentence


def test_explain_keeps_the_glossary_text_and_adds_one_line_last() -> None:
    [de] = ask([("Was ist Mainlining?", "de")])
    got = lines(de)
    assert de["route"] == "explain" and de["reason"] == "explain:mainlining"
    assert got[0].startswith("Mainlining verbindet sanftes Training")  # the glossary's short text, unchanged
    assert got[-2] == "Aus dem Glossar: Entwurf, noch nicht freigegeben."
    assert got[-1] == ("Die Schritte stehen auf der Methodenkarte Mainlining / Manifolding: frag mich „Wie mache ich "
                       "Mainlining?“.")
    [lolli] = ask([("Was ist Lollipopping?", "de")])
    assert lines(lolli)[1] == "Aus dem Glossar: Entwurf, noch nicht freigegeben."  # v1.2's lines stay where they were
    assert lines(lolli)[-1].startswith("Die Schritte stehen auf der Methodenkarte Lollipopping")
    [vpd] = ask([("Was ist VPD?", "de")])
    assert "Methodenkarte" not in vpd["text"]  # no method card, no line


def test_explain_without_a_glossary_term_answers_from_the_card() -> None:
    [de] = ask([("Was ist FIMing?", "de")])
    got = lines(de)
    assert de["reason"] == "method:explain:fimming"
    assert got[1] == CARDS["fimming"]["what"]
    assert got[2] == "Die Schritte stehen auf der Methodenkarte FIMing (Spitze teilweise abschneiden): frag mich „Wie " \
                     "mache ich FIMing?“."
    _, how = ask([("Was ist FIMing?", "de"), ("und wie mache ich das?", "de")])
    assert how["reason"] == "followup:howto;method:howto:fimming"


def test_can_i_still_top_gives_when_autoflower_and_the_risk() -> None:
    [de] = ask([("Kann ich meine Autoflower noch toppen?", "de")])
    got = lines(de)
    assert de["reason"] == "method:can:topping"
    assert got[0].startswith("Nur geschätzt, nicht bestätigt: deine Pflanze steht in Phase")
    assert got[1] == "Topping (Triebspitze abschneiden), wann es passt (aus der Methodenkarte):"
    assert got[2] == f"Zeitpunkt: {CARDS['topping']['when']}" and got[3] == f"Autoflower: {CARDS['topping']['autoflower']}"
    assert got[4].startswith("Risiko: ") and got[5] == "Aus der Methodenkarte: Entwurf, noch nicht freigegeben."


# ---------- 3. the owner's words: more heads ----------

@pytest.mark.parametrize("question,lang", [
    ("Wie bekomme ich mehr Köpfe?", "de"), ("mehrere Haupttriebe aus einem Knoten, wie geht das?", "de"),
    ("how do I get more heads?", "en"), ("How do I get more colas?", "en"), ("Hydra?", "en"),
    ("how do I top for more heads, like a hydra?", "en"), ("one node many heads", "en"), ("manifold", "en"),
])
def test_more_heads_routes_to_mainlining_then_topping(question: str, lang: str) -> None:
    [got] = ask([(question, lang)], live=staged("veg", "manual", "Wachstum"))
    assert got["reason"] == "method:heads:mainlining+topping", question
    head = "Mainlining / Manifolding, Schritt für Schritt" if lang == "de" else "Mainlining / manifolding, step by step"
    assert lines(got)[0].startswith(head)
    assert got["more"]["text"].startswith("Topping (")  # the single cut, folded away with its own sources
    assert {s["id"] for s in got["more"]["sources"]} <= set(CARDS["topping"]["sources"])
    assert {s["id"] for s in got["sources"]} <= set(CARDS["mainlining"]["sources"])


def test_no_card_got_an_alias_its_sources_do_not_carry() -> None:
    heads_words = {"hydra", "heads", "köpfe", "koepfe", "colas", "haupttriebe", "kopf"}
    for key, card in CARDS.items():
        aliases = {a.lower() for lang in ("de", "en") for a in card["aliases"][lang]}
        assert not aliases & heads_words, key
    assert CARDS["topping"]["aliases"] == {"de": [], "en": ["apical cut", "pinching", "decapitation"]}


# ---------- 4. the stage window ----------

def test_pre_flower_cards_say_so_in_their_when() -> None:
    assert "vor den ersten Härchen" in CARDS["topping"]["when"]
    assert "in der Blüte nie" in CARDS["fimming"]["when"]
    assert CARDS["mainlining"]["when"].startswith("Nur in der Wachstumsphase")
    assert "bevor die Blüte richtig einsetzt" in CARDS["lst"]["when"]
    assert methods.PRE_FLOWER == ("topping", "fimming", "mainlining")
    # LST (review M2): the card says no new ties once flowering runs and the stretch slows, and that M3 does not say
    # whether a first start in flower makes sense; so no line in the early flower (the stretch), one from mid flower
    assert "musst du keine neuen Binder mehr setzen" in CARDS["lst"]["when"]
    assert "Ob ein erster Start erst in der Blüte noch sinnvoll ist, sagt M3 nicht." in CARDS["lst"]["when"]
    assert methods.TIES_DONE == ("mid_flower", "late_flower", "harvest")


def test_while_flowering_the_window_line_comes_once_first_with_how_the_stage_is_known() -> None:
    [estimated] = ask([("Wie toppe ich richtig?", "de")])  # F22: early flower, only estimated
    assert lines(estimated)[0] == ("Nur geschätzt, nicht bestätigt: deine Pflanze steht in Phase Frühe Blüte "
                                   "(Stretch), also in der Blüte. Für Topping ist das Fenster bei ihr vorbei, die "
                                   "Karte gilt für den nächsten Durchgang.")  # how it is known first (review N2)
    assert estimated["text"].count("Fenster bei ihr vorbei") == 1
    [owner] = ask([("Wie toppe ich richtig?", "de")], live=staged("mid_flower", "manual", "Mitte der Blüte"))
    assert lines(owner)[0].startswith("Von dir gesetzt: deine Pflanze steht in Phase Mitte der Blüte, also in der "
                                      "Blüte.")
    [heads] = ask([("how do I top for more heads, like a hydra?", "en")])
    assert lines(heads)[0].endswith("For this plant the window for mainlining and topping has passed, the card is "
                                    "for the next grow.")
    assert heads["text"].count("window for") == 1 and "window" not in heads["more"]["text"]


def test_lst_gets_the_cards_own_sentence_only_after_the_stretch() -> None:
    [early] = ask([("Wie mache ich LST?", "de")])  # F22: early flower (the stretch), estimated
    assert "Fenster" not in early["text"] and "Binder sagt die Karte" not in early["text"]
    assert lines(early)[0].startswith("LST (")
    for key, label in (("mid_flower", "Mitte der Blüte"), ("late_flower", "Späte Blüte"), ("harvest", "Ernte")):
        [got] = ask([("Wie mache ich LST?", "de")], live=staged(key, "manual", label))
        assert lines(got)[0] == (f"Von dir gesetzt: deine Pflanze steht in Phase {label}. Zu neuen LST-Bindern sagt "
                                 "die Karte: Wenn die Blüte läuft und das Streckungswachstum nachlässt, musst du keine "
                                 "neuen Binder mehr setzen."), key
        assert "Fenster" not in got["text"], key  # no "window has passed": the card says no more
    [en] = ask([("how do I do LST?", "en")], live=staged("late_flower", "manual", "Late flower"))
    assert lines(en)[0] == ("Set by you: your plant is at stage Late flower. About new LST ties the card says: Once "
                            "flowering is well underway and stretching slows, you no longer need to add ties.")


@pytest.mark.parametrize("key,set_by,how", [
    ("harvest", "manual", "Von dir gesetzt"), ("late_flower", "days", "Aus den Tagen seit Start, nicht bestätigt"),
    ("early_flower", "estimate", "Nur geschätzt, nicht bestätigt"),
])
def test_every_flowering_stage_and_every_way_it_is_known_gives_the_line(key: str, set_by: str, how: str) -> None:
    [got] = ask([("Wie toppe ich richtig?", "de")], live=staged(key, set_by, "X"))
    assert lines(got)[0].startswith(f"{how}: deine Pflanze steht in Phase X, also in der Blüte."), key
    assert got["text"].count("Fenster bei ihr vorbei") == 1


def test_before_flowering_and_for_other_methods_nothing_extra() -> None:
    for live in (staged("veg", "manual", "Wachstum"), staged("seedling", "estimate", "Keimling")):
        [got] = ask([("Wie toppe ich richtig?", "de")], live=live)
        assert "Fenster" not in got["text"] and lines(got)[0].startswith("Topping (")
    [lolli] = ask([("Wie mache ich Lollipopping?", "de")])  # flowering, but lollipopping is no pre-flower method
    assert "Fenster" not in lolli["text"]


# ---------- 5. the cannabis package ----------

def test_cannabis_package_off_no_answer_from_its_cards_or_terms() -> None:
    packages([])
    for question in ("Wie toppe ich richtig?", "Was ist Mainlining?", "Hilft Topping gegen die Höhe in der Box?",
                     "Was ist Lollipopping?", "Wie bekomme ich mehr Köpfe?", "Was sind Trichome?"):
        [got] = ask([(question, "de")])
        assert got["route"] == "package_off", question
        assert got["text"].startswith("Das Cannabis-Paket ist ausgeschaltet"), question
        assert not got["sources"] and "M18" not in got["text"], question
    [en] = ask([("how do I top my plant", "en")])
    assert en["route"] == "package_off" and en["text"].startswith("The cannabis package is switched off")
    [herbs] = ask([("Wie mache ich Ausgeizen bei Tomaten?", "de")])  # no package: still answered
    assert herbs["reason"] == "method:howto:tomato_suckering"
    [vpd] = ask([("Was ist VPD?", "de")])
    assert vpd["route"] == "explain"
    index = Index.load()
    assert not {h.passage.card for h in index.search("Topping Knoten", kinds=["methods"]).hits} & CANNABIS
    assert not index.covered("topping")
    [hermie] = ask([("how do I make my plant a hermie?", "en")])
    assert "LST" not in hermie["text"] and "Topping" not in hermie["text"]  # no cannabis term offered


def test_cannabis_package_on_again_answers_again() -> None:
    packages([])
    assert ask([("Wie toppe ich richtig?", "de")])[0]["route"] == "package_off"
    packages(["cannabis"])
    assert ask([("Wie toppe ich richtig?", "de")])[0]["reason"] == "method:howto:topping"


# ---------- 6. "Erzähl mir mehr über <Titel>" (the wiki's button) ----------

def _button_questions(on: list[str]) -> list[tuple[dict[str, Any], str, str]]:
    out = []
    for entry in wikicards.entries():
        for lang, form in (("de", "Erzähl mir mehr über {label}"), ("en", "Tell me more about {label}")):
            out.append((entry, lang, form.format(label=wiki.summary(entry, lang, on)["label"])))
    return out


@pytest.mark.parametrize("on", [["cannabis"], []])
def test_every_card_of_every_listed_folder_is_answered(on: list[str]) -> None:
    packages(on)
    asked = _button_questions(on)
    folders = {e["folder"] for e, _, _ in asked}
    assert folders == set(wikicards.EXPLAIN_FOLDERS)
    # a title two shown cards share, word for word ("Lollipopping (untere ...)", "Mainlining (manifolding)" and
    # "Mainlining / manifolding"): either card is right
    sharing: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for entry, lang, question in asked:
        if wiki.visible(entry, on):
            sharing.setdefault((lang, tuple(asking.words(question))), set()).add(f"{entry['folder']}/{entry['key']}")
    answers = ask([(q, lang) for _, lang, q in asked])
    for (entry, lang, question), got in zip(asked, answers, strict=True):
        if wiki.visible(entry, on):
            assert got["route"] == "explain", question
            shown = got["reason"].removeprefix("more:")  # the reason checked for every folder (review S6)
            same = sharing[(lang, tuple(asking.words(question)))]
            assert shown in same and (shown == f"{entry['folder']}/{entry['key']}" or len(same) > 1), question
            assert got["lang"] == lang and got["text"], question
            assert ("Entwurf" if lang == "de" else "draft") in got["text"], question
        else:  # hidden: the switch is said, unless a shown card has the same title ("Entlauben (Defoliation)")
            shown = got["reason"].removeprefix("more:")
            assert got["route"] == "package_off" or (got["route"] == "explain" and shown != f"{entry['folder']}/"
                                                     f"{entry['key']}"), question


def test_the_button_answers_from_the_right_card() -> None:
    [chives] = ask([("Erzähl mir mehr über Schnittlauch", "de")])
    got = lines(chives)
    assert chives["reason"] == "more:crops/chives" and got[0] == "Schnittlauch (aus der Kartei):"
    assert got[1] == "Phasen auf der Karte: Keimung, Wachstum, Ernte und Nachwuchs und Treiberei (ausgegrabene " \
                     "Gartenhorste)."
    assert got[-1] == "Aus der Kartei: Entwurf, noch nicht freigegeben."
    assert {"Q1", "Q2", "Q3", "Q5"} <= {s["id"] for s in chives["sources"]}
    [topping] = ask([("Tell me more about Topping (cutting off the main tip)", "en")])
    assert topping["reason"] == "more:methods/topping" and CARDS["topping"]["what_en"] in topping["text"]
    [term_en] = ask([("Tell me more about Topping", "en")])  # the glossary card's full English title
    assert term_en["reason"] == "more:glossary/topping" and lines(term_en)[-1].startswith("The steps are on the "
                                                                                           "method card Topping")
    [short] = ask([("Erzähl mir mehr über Mainlining", "de")])  # a short title of two cards: the method card first
    assert short["reason"] == "more:methods/mainlining"
    [term] = ask([("Erzähl mir mehr über Topping (Toppen)", "de")])
    assert term["reason"] == "more:glossary/topping" and term["text"].startswith("Beim Toppen schneidest du")
    [cloning] = ask([("Erzähl mir mehr über Klonen: Stecklinge bewurzeln (Cannabis und Kräuter)", "de")])
    assert cloning["reason"] == "more:processes/cloning" and "Schritte auf der Karte: 1. " in cloning["text"]


def test_cloning_with_the_package_off_reads_without_its_cannabis_steps() -> None:
    packages([])
    [herbs] = ask([("Erzähl mir mehr über Klonen: Stecklinge bewurzeln (Kräuter)", "de")])
    assert herbs["route"] == "explain" and "Cannabis" not in herbs["text"]


def test_the_page_lists_the_same_folders_and_decides_per_folder() -> None:
    hank = (REPO / "web" / "hank.js").read_text(encoding="utf-8")
    found = re.search(r"explainFolders: \[([^\]]*)\]", hank)
    assert found and re.findall(r'"([a-z]+)"', found.group(1)) == list(wikicards.EXPLAIN_FOLDERS)
    assert "explainCard: true" in hank
    page = (REPO / "web" / "wiki.js").read_text(encoding="utf-8")
    button = page[page.index("function hankButton"):]
    assert button.index("window.HANK_FEATURES.explainFolders") < button.index("document.querySelector")
    assert "hankButton(d.label, d.folder)" in page
    assert set(wikicards.EXPLAIN_FOLDERS) <= set(wiki.FOLDER_ORDER)


# ---------- the texts ----------

def test_catalogs_are_complete_in_both_languages() -> None:
    assert methods.check() == [] and wikicards.check() == []


def test_new_files_are_utf8_without_bom_lf_and_without_em_dash() -> None:
    for name in ("vgrow/hank/methods.py", "vgrow/hank/wikicards.py", "tests/test_hank_methods.py", "web/hank.js",
                 "web/wiki.js", "vgrow/hank/kartei_index.py", "vgrow/hank/router.py", "vgrow/hank/core.py"):
        raw = (REPO / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw, name
        assert EM_DASH not in raw.decode("utf-8"), name


def test_answers_carry_no_em_dash() -> None:
    for question, lang in (("Wie toppe ich richtig?", "de"), ("how do I top for more heads, like a hydra?", "en"),
                           ("Erzähl mir mehr über Schnittlauch", "de")):
        [got] = ask([(question, lang)])
        assert EM_DASH not in got["plain"], question


def test_glossary_terms_know_their_package() -> None:
    gloss = asking.glossary()
    assert gloss.get("topping").package == "cannabis" and gloss.get("vpd").package is None
    assert methods.method_of_term("low-stress-training").key == "lst"


def test_seed_shops_lose_their_link_in_method_answers() -> None:
    """Decision D3 (chat_policy.json blocked_source_hosts): Zamnesia and Hanfgarten appear as text without a link."""
    [de] = ask([("Wie toppe ich richtig?", "de")])
    by_id = {s["id"]: s for s in de["sources"]}
    assert by_id["M18"]["url"] is None and by_id["M15"]["url"] is None and "Zamnesia" in by_id["M18"]["title"]
    assert by_id["M1"]["url"].startswith("https://pmc.ncbi.nlm.nih.gov/")
    lib = methods.library()
    refs = methods.resolve(None, lib.get("topping").sources, ["M18", "M1"], "de")
    assert refs[0].url is None and refs[1].url


# ---------- review of 23.10.2026: the fixes ----------

@pytest.mark.parametrize("question", [
    "tippen", "tippe", "filmen", "fixen", "topfen", "tapped", "Tipping bucket", "Kann ich meine Pflanze filmen?",
    "Welche Lottozahlen soll ich tippen?", "Soll ich die Kräuter umtopfen?",
])
def test_everyday_words_are_no_method_typos(question: str) -> None:
    """Review M1: the general distance-1 rule took "tippen" for "toppen" and "filmen" for "fimmen"."""
    found = methods.named(question)
    assert found.keys == [] and not found.heads, question


def test_everyday_words_never_get_a_method_answer_or_the_package_line() -> None:
    for on in (["cannabis"], []):
        packages(on)
        for question in ("Kann ich meine Pflanze filmen?", "Welche Lottozahlen soll ich tippen?"):
            [got] = ask([(question, "de")])
            assert "method" not in got["reason"] and got["route"] != "package_off", (on, question)
            assert "Cannabis-Paket" not in got["text"], (on, question)


@pytest.mark.parametrize("question,lang,reason", [
    ("how to top the nodes properly", "en", "method:howto:topping"),
    ("How do I get multiple heads from one node?", "en", "method:heads:mainlining+topping"),
    ("Wie bekomme ich mehrere Köpfe von einem Knoten?", "de", "method:heads:mainlining+topping"),
])
def test_the_owners_own_sentences_get_the_card(question: str, lang: str, reason: str) -> None:
    """Review S4: "nodes", "from" and "von" were missing from the filler words."""
    [got] = ask([(question, lang)], live=staged("veg", "manual", "Wachstum"))
    assert got["reason"] == reason, question


def test_a_heads_question_without_a_method_name_is_searched_in_the_heads_cards() -> None:
    [got] = ask([("Bringen mehr Köpfe mehr Ertrag in der Box?", "de")])
    assert got["route"] == "grow" and got["reason"] == "method:mainlining", got["reason"]
    [tomato] = ask([("How do I get more colas on my tomato?", "en")])
    assert "method" not in tomato["reason"]  # another plant: not the cannabis heads cards


def test_counting_heads_is_no_wish_for_more() -> None:
    for question in ("How many heads of lettuce can I grow?", "Wie viele Köpfe Salat passen in die Box?"):
        assert not methods.named(question).heads, question


def test_what_is_manifolding_keeps_the_glossary_text() -> None:
    """Review N4: an explain question about a glossary term stays an explanation, the heads route aside."""
    [got] = ask([("Was ist Manifolding?", "de")])
    assert got["reason"] == "explain:mainlining" and lines(got)[-1].startswith("Die Schritte stehen auf der")
    assert not methods.named("Ich brauche einen neuen Verteiler").heads  # a power strip


def test_a_glossary_term_without_the_package_is_still_explained_with_the_package_off() -> None:
    """Review S3: defoliation is a glossary term without a package, its method card carries "cannabis"."""
    packages([])
    [got] = ask([("Was ist Entlauben?", "de")])
    assert got["route"] == "explain" and got["reason"] == "explain:defoliation", got["reason"]
    assert "Methodenkarte" not in got["text"]  # the card is switched off: no pointer to it
    [bare] = ask([("Entlauben?", "de")])
    assert bare["route"] == "explain" and "defoliation" in bare["reason"] and "Methodenkarte" not in bare["text"]
    [how] = ask([("Wie entlaube ich richtig?", "de")])
    assert how["route"] == "package_off"  # the steps are the card's, and it is off


def test_a_follow_up_with_the_package_off_stays_off() -> None:
    packages(["cannabis"])
    [_, follow] = ask([("Was ist FIMing?", "de"), ("und wie mache ich das?", "de")])
    assert follow["reason"].endswith("method:howto:fimming")
    packages([])
    first, again = ask([("Was ist VPD?", "de"), ("Und wie toppe ich?", "de")])
    assert first["route"] == "explain" and again["route"] == "package_off"


def test_sources_carry_their_card_id_so_the_marks_in_the_text_resolve() -> None:
    """Review S1: the text cites "(M18, Erfahrungsquelle)", the page numbers by position: the title starts with the
    id, and the plain text does not print it twice."""
    [de] = ask([("Wie toppe ich richtig?", "de")], live=staged("veg", "manual", "Wachstum"))
    assert all(s["title"].startswith(f"{s['id']}: ") for s in de["sources"])
    assert "M18 M18" not in de["plain"] and "M18: " in de["plain"]
    [heads] = ask([("how do I top for more heads, like a hydra?", "en")])
    assert all(s["title"].startswith(f"{s['id']}: ") for s in [*heads["sources"], *heads["more"]["sources"]])
    [chives] = ask([("Erzähl mir mehr über Schnittlauch", "de")])
    assert all(s["title"].startswith(f"{s['id']}: ") for s in chives["sources"])


@pytest.mark.parametrize("question,key", [
    ("Mainlniing", "mainlining"), ("Mainlning", "mainlining"), ("Lolippoping", "lollipopping"),
    ("Defoliaton", "defoliation"),
])
def test_one_typo_in_a_long_method_word_still_finds_the_card(question: str, key: str) -> None:
    """Review M1, the alternative kept: one edit only for words of eight letters or more that are no known word."""
    known = Index.load().vocab.is_known
    assert methods.named(question, known=known).keys == [key], question


def test_the_long_typo_rule_leaves_known_and_short_words_alone() -> None:
    known = Index.load().vocab.is_known
    for question in ("Tipping bucket", "Can I grow lollipops?", "toppings on pizza", "mainland"):
        assert methods.named(question, known=known).keys == [], question


def test_one_typo_in_the_other_words_keeps_the_structured_answer() -> None:
    for question, lang, reason in (("Wie toppe ich ricthig?", "de", "method:howto:topping"),
                                   ("Kann ich meine Autofolwer noch toppen?", "de", "method:can:topping"),
                                   ("How do I get multpile heads from one node?", "en",
                                    "method:heads:mainlining+topping"),
                                   ("Was ist Mainlniing?", "de", "explain:mainlining")):
        [got] = ask([(question, lang)])
        assert got["reason"] == reason, question


def test_the_button_title_may_carry_one_typo() -> None:
    [got] = ask([("Tell me more about Topipng (cutting off the main tip)", "en")])
    assert got["reason"] == "more:methods/topping"
