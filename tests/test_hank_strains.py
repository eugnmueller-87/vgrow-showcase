"""Hank's strain advice (vgrow/hank/strains.py; owner, 23.10.2026: "Ich möchte, dass man Hank fragen kann, welche
Sorte sich zum Beispiel für den Anbau am besten eignet, dass er die Person optimal beraten kann. Welche Sorte das dann
ist und wie die Person die kauft, ist der Person selbst überlassen.").

Code decides and words the answer, the model is never asked: which questions route to it, at most two short questions
one at a time and kept for the chat, the ranking (fits the box first, then the wishes, then difficulty), three
suggestions with height, time, difficulty, why, source ids and "Herstellerangabe, ungeprüft", never THC or a shop
link, buying answered by the sale gate, the lexicon about one strain, the cannabis package switched off. The live
state is the eval fixture F22 with the day frozen; every chat has its own client id.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from hank_eval import fixtures
from vgrow import config, strains as lexicon, wiki
from vgrow.hank import core, strains
from vgrow.hank.router import Router

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
FORBIDDEN = re.compile(r"\bTHC\b|\bCBD\b|cannabinoid|Wirkung|\beffects?\b|Geschmack|\btaste\b|Rausch|\bhigh\b", re.I)
SHOP_LINK = re.compile(r"https?://(?:www\.)?(?:hanfgarten|barneysfarm|hanfgartenshop|zamnesia)\.")


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def f22(lang: str = "de") -> dict[str, Any]:
    sources = fixtures.build("F22", lang=lang).sources()
    sources.pop("ledger")
    return sources


def ask(questions: list[str], lang: str = "de", client_id: str = "chat") -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        hank = core.Hank(live=f22, index=core.shared_index())
        out = []
        try:
            for question in questions:
                got = await hank.ask_full(question, client_id=client_id, lang=lang, model=False)
                out.append({**core.answer_dict(got.answer), "reason": got.route.reason, "plain": got.answer.plain(),
                            "job": got.job})
        finally:
            await hank.stop()
        return out

    return asyncio.run(go())


def packages(on: list[str]) -> None:
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": on}))


def picks(text: str) -> list[str]:
    return re.findall(r"^\d\. (.+?) \(", text, re.M)


# ---------- which questions ----------

@pytest.mark.parametrize(("q", "lang"), [
    ("Welche Sorte passt in meine Box?", "de"), ("Welche Autoflower für Anfänger?", "de"),
    ("Welche Autoflower ist am einfachsten?", "de"), ("Ich will eine kurze Bluete", "de"),
    ("Kannst du mir eine Sorte empfehlen?", "de"), ("Welche Sorten passen in die VGrow?", "de"),
    ("what strain fits my grow box?", "en"), ("Which autoflower is easiest for a beginner?", "en"),
    ("Can you recommend a strain?", "en"), ("Was ist die beste Autoflower für die Box?", "de"),
])
def test_advice_questions_route_to_the_strain_advice(q: str, lang: str) -> None:
    route = Router(core.shared_index()).route(q, lang_hint=lang, live=f22(lang))
    assert route.name == "strains" and route.form in ("ask", "advice"), (q, route.name, route.reason)


@pytest.mark.parametrize(("q", "lang"), [
    ("Welches Auto soll ich mir kaufen?", "de"), ("Welche Autoflower braucht wie viel Licht?", "de"),
    ("Welche Sorte ist meine Pflanze?", "de"), ("Welche Basilikum-Sorte passt in die Box?", "de"),
    ("Welche Werte brauche ich bei Autoflower?", "de"), ("Wie warm ist es?", "de"),
    ("Which car should I buy?", "en"), ("What is the best temperature for autos?", "en"),
])
def test_other_questions_keep_their_way(q: str, lang: str) -> None:
    route = Router(core.shared_index()).route(q, lang_hint=lang, live=f22(lang))
    assert route.name != "strains", (q, route.reason)


# ---------- two questions at most, one at a time, kept for the chat ----------

def test_the_box_question_asks_the_type_then_the_experience_then_answers() -> None:
    a, b, c = ask(["Welche Sorte passt in meine Box?", "Autoflower", "Erster Grow"])
    assert a["route"] == b["route"] == c["route"] == "strains"
    assert a["text"].endswith("Soll es eine Autoflower sein oder eine photoperiodische Sorte?")
    assert a["chips"] == ["Autoflower", "Photoperiodisch", "Egal"] and a["sources"] == []
    assert b["text"].startswith("Noch eine Frage: Ist das dein erster Grow") and "Erster Grow" in b["chips"]
    assert c["text"].startswith("Drei Vorschläge aus dem Sortenlexikon für deine VGrow (92 cm Wuchshöhe für eine "
                                "Pflanze, S1), Autoflower, für den ersten Grow:")
    assert len(picks(c["text"])) == 3 and all(x["job"] is None for x in (a, b, c))  # code only, no model


def test_a_question_that_says_it_all_is_answered_at_once() -> None:
    (a,) = ask(["Welche Autoflower ist am einfachsten?"])
    assert a["reason"] == "strains:advice" and len(picks(a["text"])) == 3
    assert "Autoflower, möglichst einfach:" in a["text"].splitlines()[0]


def test_never_more_than_two_questions_and_never_one_twice() -> None:
    a, b, c = ask(["Welche Sorte passt in meine Box?", "egal", "weiss nicht"])
    assert a["reason"].endswith("ask:flowering") and b["reason"].endswith("ask:level")
    assert c["reason"] == "strains:reply" and len(picks(c["text"])) == 3
    w = strains.Wishes(asked=["flowering", "level"])
    assert strains.next_question(w) is None
    assert strains.next_question(strains.Wishes(flowering="auto", short=True)) is None  # a time wish: no level question


def test_a_time_wish_skips_the_experience_question() -> None:
    a, b = ask(["Ich will eine kurze Bluete", "Autoflower"])
    assert a["reason"].endswith("ask:flowering") and b["reason"] == "strains:reply"
    assert "Autoflower, möglichst kurze Blüte:" in b["text"].splitlines()[0]
    lex = lexicon.load()
    times = [lex_strain.time_days("bloom") for lex_strain in (next(s for s in lex.strains if lexicon.fold(s.label["de"]) ==
                                                          lexicon.fold(name)) for name in picks(b["text"]))]
    assert times == sorted(times)


def test_the_answers_stay_for_the_chat_and_a_short_reply_changes_one_wish() -> None:
    out = ask(["Welche Sorte passt in meine Box?", "Autoflower", "Erster Grow", "und photoperiodisch?",
               "Welche Sorte passt in meine Box?"])
    assert "photoperiodisch, für den ersten Grow:" in out[3]["text"].splitlines()[0]
    assert out[4]["reason"] == "strains:advice" and "photoperiodisch, für den ersten Grow:" in out[4]["text"]
    # another chat starts from nothing
    (fresh,) = ask(["Welche Sorte passt in meine Box?"], client_id="other")
    assert fresh["reason"].endswith("ask:flowering")


def test_a_question_that_names_a_type_is_no_reply_after_the_advice() -> None:
    out = ask(["Welche Autoflower für Anfänger?", "Ist es zu kalt für Autoflower?"])
    assert out[1]["route"] != "strains"


def test_english_runs_the_same_way() -> None:
    a, b, c = ask(["what strain fits my grow box?", "autoflower", "I have experience"], lang="en")
    assert a["text"].endswith("should it be an autoflower or a photoperiod strain?")
    assert b["text"].startswith("One more question: is this your first grow")
    assert c["text"].startswith("Three suggestions from the strain lexicon for your VGrow (92 cm growing height for "
                                "one plant, S1), autoflower, with experience:")
    assert "All figures: maker's figure, unverified." in c["text"] and "is your decision" in c["text"]


# ---------- the ranking and the answer ----------

def test_suggestions_fit_the_box_first_never_too_tall_and_three_names() -> None:
    lex = lexicon.load()
    for w in (strains.Wishes(), strains.Wishes(flowering="auto"), strains.Wishes(flowering="photoperiod"),
              strains.Wishes(flowering="auto", level="beginner"), strains.Wishes(short=True),
              strains.Wishes(flowering="photoperiod", level="experienced")):
        got, _ = strains.rank(lex, w)
        assert len(got) == 3 and len({lexicon.fold(s.label["de"]) for s in got}) == 3, w
        fits = [lexicon.FITS.index(s.fit(lex.box_cm)) for s in got]
        assert fits == sorted(fits) and "tall" not in {s.fit(lex.box_cm) for s in got}, w
        if w.flowering in ("auto", "photoperiod"):
            assert {s.flowering for s in got} == {w.flowering}, w
    autos, _ = strains.rank(lex, strains.Wishes(flowering="auto"))
    assert {s.fit(lex.box_cm) for s in autos} == {"sure"}  # eight autoflowers fit for sure: those first
    beginners, _ = strains.rank(lex, strains.Wishes(flowering="photoperiod", level="beginner"))
    assert all(s.difficulty == "easy" for s in beginners)


def test_a_longest_time_holds_seed_to_harvest_and_says_so_when_only_flowering_is_known() -> None:
    lex = lexicon.load()
    got, info = strains.rank(lex, strains.Wishes(flowering="auto", max_days=77))
    assert got and all(s.harvest is not None and s.harvest[1] <= 77 for s in got) and info == {"basis": "harvest"}
    got, info = strains.rank(lex, strains.Wishes(flowering="photoperiod", max_days=63))
    assert info == {"bloom_only": True, "basis": "bloom"} and all(s.bloom_days[1] <= 63 for s in got)
    assert all(s.harvest is None for s in got)  # a given seed to harvest time that is too long rules a strain out
    (a,) = ask(["Welche photoperiodische Sorte schafft es in 9 Wochen?"])
    assert "Samen bis Ernte nennt der Hersteller nicht" in a["text"]


def test_each_suggestion_names_height_time_difficulty_why_and_its_source() -> None:
    (a,) = ask(["Welche Autoflower für Anfänger?"])
    lines = [line for line in a["text"].splitlines() if re.match(r"^\d\. ", line)]
    assert len(lines) == 3
    for line in lines:
        assert "Höhe innen" in line and ("Blüte" in line or "Samen bis Ernte" in line) and "Schwierigkeit" in line
        assert "Passt, weil" in line and re.search(r"\((?:[HB]\d{1,3}(?:, )?)+\)\.$", line), line
    assert "Alle Zahlen: Herstellerangabe, ungeprüft." in a["text"]
    assert "Welche Sorte du nimmst und wie du sie kaufst, entscheidest du selbst." in a["text"]
    assert a["link"] == {"href": "#wiki/sorten", "label": "Sortenlexikon öffnen"}
    ids = [s["id"] for s in a["sources"]]
    assert ids[-1] == "S1" and all(re.match(r"^[HB]\d+$", i) for i in ids[:-1])
    assert all(s["url"] is None for s in a["sources"][:-1])  # shop and breeder pages: never a link
    assert not FORBIDDEN.search(a["plain"]) and not SHOP_LINK.search(a["plain"]) and EM_DASH not in a["plain"]


def test_a_seed_shop_name_in_running_text_follows_decision_d3() -> None:
    (a,) = ask(["Welche Autoflower für Anfänger?"])
    assert "Marke eines Samenhändlers" in a["text"] and "(Hanfgarten)" not in a["text"]


# ---------- buying, THC, one strain, the package switch ----------

@pytest.mark.parametrize(("q", "lang"), [
    ("Wo kann ich Samen kaufen?", "de"), ("Wo bekomme ich die Sorte her?", "de"),
    ("Where can I buy Northern Lights seeds?", "en"), ("Kann ich Autoflower-Samen online bestellen?", "de"),
])
def test_buying_is_the_persons_own_choice(q: str, lang: str) -> None:
    (a,) = ask([q], lang=lang)
    assert a["route"] == "sale" and a["reason"].startswith("gate:buy:"), a["reason"]
    assert ("entscheidest du selbst" in a["text"]) if lang == "de" else ("is your decision" in a["text"])
    assert not SHOP_LINK.search(a["text"]) and "KCanG" not in a["text"]


def test_selling_still_meets_the_sale_gate() -> None:
    (a,) = ask(["Kann ich Samen kaufen und dann verkaufen?"])
    assert a["route"] == "sale" and a["reason"].startswith("gate:pattern:")


def test_a_thc_question_about_a_strain_never_gets_a_number() -> None:
    for q, lang in (("Wie viel THC hat Northern Lights?", "de"), ("How much THC does Tangerine Dream Auto have?", "en"),
                    ("Welche Sorte hat am meisten THC?", "de")):
        (a,) = ask([q], lang=lang)
        assert a["route"] == "health" and "%" not in a["text"], (q, a["reason"])


def test_one_strain_is_answered_from_its_entry() -> None:
    (a,) = ask(["Was weisst du ueber Tangerine Dream Auto?"])
    assert a["route"] == "strains" and a["text"].startswith("Tangerine Dream Auto (Barney's Farm), aus dem Sortenlexikon:")
    assert "Samen bis Ernte: 70 bis 75 Tage (B97)" in a["text"] and "In der VGrow: passt sicher" in a["text"]
    assert [s["id"] for s in a["sources"]] == ["B97", "S1"]


def test_northern_lights_auto_is_not_in_the_lexicon_and_hank_says_what_is() -> None:
    (a,) = ask(["Was weisst du ueber Northern Lights Auto?"])
    lines = a["text"].splitlines()
    assert lines[0] == ("Eine Northern Lights Auto steht nicht im Sortenlexikon. Unter dem Namen Northern Lights "
                        "stehen dort nur photoperiodische Sorten:")
    assert len([x for x in lines if re.match(r"^\d\. Northern Lights ", x)]) == 3
    assert "Die VGrow hat 92 cm Wuchshöhe für eine Pflanze (S1)." in lines


def test_the_owners_card_is_answered_from_it_and_the_wiki_button_keeps_its_way() -> None:
    (a,) = ask(["Was weisst du über Ice Cream Cake Automatic?"])
    assert a["route"] == "strains" and a["text"].startswith("Ice Cream Cake Automatic, die Sorte deiner Pflanze")
    assert "THC" not in a["plain"]
    (b,) = ask(["Erzähl mir mehr über Ice Cream Cake Automatic"])
    assert b["route"] == "explain" and b["reason"] == "more:strains/ice-cream-cake-auto"
    (c,) = ask(["Erzähl mir mehr über Tangerine Dream Auto"])
    assert c["route"] == "strains"


def test_the_package_switched_off_switches_the_lexicon_off_for_hank() -> None:
    packages([])
    for q in ("Welche Sorte passt in meine Box?", "Was weisst du ueber Tangerine Dream Auto?"):
        (a,) = ask([q])
        assert a["route"] == "package_off" and "Cannabis-Paket ist ausgeschaltet" in a["text"], q
    packages(["cannabis"])
    (a,) = ask(["Welche Sorte passt in meine Box?"])
    assert a["route"] == "strains"


def test_the_catalog_has_both_languages_and_no_em_dash() -> None:
    assert strains.check() == []
    assert set(strains.DE) == set(strains.EN)


# ---------- review 23.10.2026: buying, strength, the time basis, the open question, LSD ----------

@pytest.mark.parametrize(("q", "lang"), [
    ("Wo finde ich die Photoperiode?", "de"), ("Where do I find the photoperiod setting?", "en"),
    ("Ich habe bestellte Samen, wie keimen sie am besten?", "de"), ("I bought seeds, how do I germinate them?", "en"),
    ("Wo finde ich, ob meine Samen feminisiert sind?", "de"),
])
def test_a_growing_question_with_a_buy_word_is_no_buy(q: str, lang: str) -> None:
    route = Router(core.shared_index()).route(q, lang_hint=lang, live=f22(lang))
    assert not (route.name == "sale" and route.reason.startswith("gate:buy:")), (q, route.reason)


@pytest.mark.parametrize(("q", "lang"), [
    ("Welche Sorte soll ich kaufen?", "de"), ("Which strain should I buy?", "en"),
    ("Welche Autoflower soll ich für meine Box bestellen?", "de"),
])
def test_an_advice_question_with_a_buy_word_gets_the_advice(q: str, lang: str) -> None:
    (a,) = ask([q], lang=lang)
    assert a["route"] == "strains", (q, a["reason"])
    (b,) = ask([q, "Autoflower", "Erster Grow" if lang == "de" else "First grow"], lang=lang, client_id="c2")[-1:]
    assert ("entscheidest du selbst" in b["text"]) if lang == "de" else ("is your decision" in b["text"])


@pytest.mark.parametrize(("q", "lang"), [
    ("Wo kann ich eine Sorte kaufen, die in meine Box passt?", "de"), ("Welche Sorte kann ich online bestellen?", "de"),
    ("Which strain can I buy cheapest?", "en"),
])
def test_where_a_shop_or_a_price_keeps_the_buy_reply(q: str, lang: str) -> None:
    (a,) = ask([q], lang=lang)
    assert a["route"] == "sale" and a["reason"].startswith("gate:buy:"), (q, a["reason"])


@pytest.mark.parametrize(("q", "lang"), [
    ("Welche Autoflower ist die stärkste?", "de"), ("Was ist die stärkste Autoflower?", "de"),
    ("Welche Samen sind am stärksten?", "de"), ("What is the strongest autoflower?", "en"),
    ("Which autoflower is the strongest?", "en"), ("most potent seeds for my box", "en"),
])
def test_a_strength_question_meets_the_health_gate_whatever_the_noun(q: str, lang: str) -> None:
    (a,) = ask([q], lang=lang)
    assert a["route"] == "health" and not picks(a["text"]), (q, a["reason"])


def test_growth_vigour_is_no_strength_question() -> None:
    route = Router(core.shared_index()).route("Welche Autoflower wächst am stärksten?", lang_hint="de", live=f22())
    assert route.name != "health", route.reason


def test_a_short_flowering_compares_flowering_times_only() -> None:
    lex = lexicon.load()
    got, info = strains.rank(lex, strains.Wishes(short=True, short_on="bloom"))
    assert info == {"basis": "bloom"} and len(got) == 3
    times = [s.time_days("bloom") for s in got]
    assert None not in times and times == sorted(times)
    w, _ = strains.wishes_of("Ich will eine kurze Blüte", lex)
    assert (w.short, w.short_on) == (True, "bloom")
    w, _ = strains.wishes_of("Ich will eine schnelle Ernte", lex)
    assert (w.short, w.short_on) == (True, "harvest")
    got, info = strains.rank(lex, w)
    keys = [(lexicon.FITS.index(s.fit(lex.box_cm)), s.time_days("harvest") or 10 ** 4) for s in got]
    assert info == {"basis": "harvest"} and keys == sorted(keys)  # the fit first, then seed to harvest
    assert "möglichst kurz von Samen bis Ernte" in strains.wish_text(w, "de")
    assert "as short as possible from seed to harvest" in strains.wish_text(w, "en")


def test_a_question_of_its_own_while_hanks_question_is_open_is_no_reply() -> None:
    a, b = ask(["Welche Sorte passt in meine Box?", "Ist es zu kalt für Autoflower?"])
    assert a["reason"].endswith("ask:flowering") and b["reason"] != "strains:reply" and not b["reason"].startswith(
        "strains:reply"), b["reason"]
    a, b = ask(["Welche Sorte passt in meine Box?", "Lieber eine Autoflower"], client_id="c3")
    assert b["reason"].startswith("strains:reply")


def test_a_drug_name_is_no_strain() -> None:
    (a,) = ask(["Was weißt du über LSD?"])
    assert a["route"] != "strains", a["reason"]
    for q in ("Welche Sorte passt in meine Box?", "Welche Autoflower für Anfänger?", "Ich will eine kurze Blüte"):
        for out in ask([q, "Egal", "Egal"], client_id=q):
            assert not re.search(r"\bLSD|Ayahuasca|Euforia|Crack|Laughing|Cough", out["text"]), out["text"]


# ---------- a strain named anywhere, and the encyclopedia (owner, 01.11.2026) ----------
# "do we have information about purple punch?" got "Wrong guy for that" although the lexicon had three Purple Punch
# entries and the wiki's encyclopedia one more: Hank looks the name up in both before anything else answers.

@pytest.mark.parametrize(("q", "lang"), [
    ("do we have information about purple punch?", "en"), ("Purple Punch?", "en"),
    ("Haben wir Infos zu Purple Punch?", "de"), ("hey hank, anything on purple punch?", "en"),
    ("Gibt es im Wiki was zu Purple Punch?", "de"), ("what is purple punch", "en"),
])
def test_a_strain_named_in_any_wording_is_looked_up(q: str, lang: str) -> None:
    (a,) = ask([q], lang=lang)
    assert a["route"] == "strains" and a["reason"].startswith("strains:about:"), (q, a["reason"])
    assert "Purple Punch" in a["text"] and not FORBIDDEN.search(a["plain"]), a["text"]


def test_the_lexicon_answer_names_the_encyclopedia_too() -> None:
    (a,) = ask(["do we have information about purple punch?"], lang="en")
    line = next(x for x in a["text"].splitlines() if x.startswith("In the strain encyclopedia:"))
    assert "Hybrid" in line and "Granddaddy Purple x Larry OG" in line, line
    cited = {s["id"] for s in a["sources"]}
    assert any(sid.startswith("L") for sid in cited), cited
    # the editorial list (L0) is cited only where it disagrees with the variety's own page (01.11.2026: Leafly's page
    # says hybrid, its list indica), never beside a value the page itself gives
    assert line == ("In the strain encyclopedia: Hybrid (L261615), another source says Indica (L0); parents "
                    "Granddaddy Purple x Larry OG (L261615)."), line


@pytest.mark.parametrize(("q", "lang"), [
    ("How often should I water my Purple Punch?", "en"), ("Wie oft gieße ich meine Purple Punch?", "de"),
    ("Is it critical that the humidity is high?", "en"), ("Ist Grapefruit gut für die Haut?", "de"),
    ("do we have information about VPD?", "en"),
])
def test_a_name_inside_another_question_keeps_its_way(q: str, lang: str) -> None:
    route = Router(core.shared_index()).route(q, lang_hint=lang, live=f22(lang))
    assert not (route.name == "strains" and route.form in ("about", "variety", "unknown")), (q, route.reason)


def test_a_variety_only_in_the_encyclopedia_is_answered_from_it() -> None:
    (a,) = ask(["do we have information about Papaya Punch?"], lang="en")
    assert a["route"] == "strains" and a["reason"] == "strains:variety:papaya-punch", a["reason"]
    text_ = a["text"]
    assert text_.startswith("Papaya Punch, from the strain encyclopedia in the wiki:")
    assert "Classification: Hybrid" in text_ and "Parents: Purple Punch x Papaya" in text_, text_
    assert "cannot say whether it fits your VGrow" in text_
    assert not FORBIDDEN.search(a["plain"]) and "%" not in text_ and "Limonene" not in text_, text_
    assert a["link"]["href"] == "#wiki/varieties/papaya-punch"
    assert all(s["id"].startswith("L") for s in a["sources"]) and a["sources"], a["sources"]
    (b,) = ask(["Was weisst du ueber Papaya Punch?"])
    assert b["reason"] == "strains:variety:papaya-punch" and b["text"].startswith("Papaya Punch, aus der Sortenenzyklop")


def test_an_unknown_strain_gets_an_honest_no_not_wrong_guy() -> None:
    (a,) = ask(["Was weisst du ueber die Sorte Moonfrost Delight?"])
    assert a["reason"] == "strains:unknown", a["reason"]
    assert a["text"].startswith("Zu Moonfrost Delight finde ich nichts") and "erfinde dazu nichts" in a["text"]
    (b,) = ask(["do we have information about the strain Moonfrost Delight?"], lang="en")
    assert b["reason"] == "strains:unknown" and "I will not make anything up" in b["text"], b["reason"]


def test_a_thc_question_about_an_encyclopedia_variety_meets_the_health_gate() -> None:
    (a,) = ask(["How much THC does Papaya Punch have?"], lang="en")
    assert a["route"] == "health" and "23" not in a["text"], a["reason"]


def test_a_name_without_a_type_lists_its_autoflower_and_spellings_are_one_parent() -> None:
    (a,) = ask(["Purple Punch?"], lang="en")
    assert a["text"].startswith("The strain lexicon has 3 strains named Purple Punch:"), a["text"]
    assert "Purple Punch Auto (Barney's Farm), autoflower" in a["text"]
    (b,) = ask(["do we have information about Slurricane?"], lang="en")
    assert "Parents: Dosidos x Purple Punch" in b["text"] and "Another source" not in b["text"], b["text"]


# ---------- the safety gates know the strain names (holdout h27, h28, 01.11.2026) ----------

def test_a_named_strain_is_cannabis_for_the_gates() -> None:
    (a,) = ask(["Wie stark ist Purple Punch?"])
    assert a["route"] == "health", a["reason"]  # passed every gate before: no "Sorte" in the question
    (b,) = ask(["where can I order Gelato Auto seeds?"], lang="en")
    assert b["route"] == "sale", b["reason"]  # "Gelato" was taken for another plant
    (c,) = ask(["How potent is Papaya Punch?"], lang="en")
    assert c["route"] == "health", c["reason"]


def test_a_named_strain_keeps_its_growing_questions() -> None:
    for q, lang, route in (("How tall does Purple Punch get?", "en", "strains"), ("Wie hoch wird Gelato Auto?", "de", "strains"),
                           ("Is Purple Punch good for beginners?", "en", "strains"),
                           ("Wie oft gieße ich meine Purple Punch?", "de", "grow")):
        (a,) = ask([q], lang=lang)
        assert a["route"] == route, (q, a["reason"])
    assert not strains.names_strain("Gelato ist mein Lieblingseis")  # one word is no strain for the gates


# ---------- indica or sativa (owner, 01.11.2026: "Ist Purple Punch Indica?" got "Meinst du Pumpe?") ----------

def test_the_type_question_names_each_breeders_share_and_both_classifications() -> None:
    (a,) = ask(["Ist Purple Punch Indica?"])
    assert a["route"] == "strains" and a["reason"].startswith("strains:about:"), a["reason"]
    assert "photoperiodisch, 90 % Indica: " in a["text"] and "photoperiodisch, 80 % Indica: " in a["text"], a["text"]
    # Leafly says hybrid, the selection list indica: both stand, with their ids
    assert "Hybrid (L261615), eine andere Quelle sagt Indica (L0)" in a["text"], a["text"]
    (b,) = ask(["Is Purple Punch indica or sativa?"], lang="en")
    assert b["route"] == "strains" and "photoperiod, 90% indica: " in b["text"], b["reason"]
    assert not FORBIDDEN.search(b["plain"]), b["plain"]


def test_a_strains_name_is_never_asked_as_a_typo() -> None:
    assert strains.strain_words("Ist Purple Punch Indica?") == frozenset({"purple", "punch"})
    assert strains.strain_words("Gelato ist mein Lieblingseis") == frozenset()
    for q in ("Ist Purple Punch Indica?", "Wie viel Licht braucht Purple Punch?", "Purple Punch Indica oder Sativa?"):
        (a,) = ask([q])
        assert a["route"] != "clarify" and "Pumpe" not in a["text"], (q, a["reason"])


# ---------- while a grow runs, an unknown name gets the honest no (owner, 01.11.2026) ----------

def test_an_unknown_name_while_growing_gets_an_honest_no_not_wrong_guy() -> None:
    # "hank has to know whether we are currently growing or not": both got "Da bin ich der Falsche"
    (a,) = ask(["do we have information about Moonfrost Delight?"], lang="en")
    assert a["route"] == "strains" and a["reason"] == "strains:unknown_any", a["reason"]
    assert a["text"].startswith("I find nothing on Moonfrost Delight: not in the strain lexicon") and \
        "not in my card file" in a["text"], a["text"]
    (b,) = ask(["Haben wir Infos zu Blue Zushi?"])
    assert b["text"].startswith("Zu Blue Zushi finde ich nichts"), b["text"]  # as typed, not the folded "Blu"
    assert b["link"]["href"] == strains.WIKI_LINK


def test_a_known_off_topic_question_stays_wrong_guy_while_growing() -> None:
    for q, lang in (("Was weisst du ueber Fussball?", "de"), ("tell me about the weather", "en")):
        (a,) = ask([q], lang=lang)
        assert a["route"] == "offtopic", (q, a["reason"])


def test_without_a_grow_an_unknown_name_stays_off_topic() -> None:
    live = f22("en")
    live["plant_view"] = {**live["plant_view"], "strain": None}
    route = Router(core.shared_index()).route("do we have information about Moonfrost Delight?", lang_hint="en",
                                              live=live)
    assert route.name == "offtopic", route.reason


def test_a_rare_strain_gets_its_grow_facts_and_the_fact_sheets_verdict() -> None:
    # owner, 01.11.2026: Laughing Buddha and other rare strains in the encyclopedia, with "is it for me next time"
    (de,) = ask(["Ist Laughing Buddha eine Sativa?"], lang="de")
    assert de["route"] == "strains" and de["reason"] == "strains:variety:laughing-buddha"
    assert "Eltern: Thai x Jamaican (L2773)" in de["text"]
    assert ("Anbau laut Quelle: Schwierigkeit schwierig, Höhe hoch, Ertrag mittel, Blüte rund 12 Wochen (L2773)."
            in de["text"])
    assert "Im Steckbrief im Wiki heißt das: eher was für später." in de["text"]
    assert "Wuchshöhe und Blütezeit stehen dort nicht" not in de["text"] and "THC" not in de["text"]
    (en,) = ask(["do we have information about Kali Mist?"], lang="en")
    # Leafly first, the yield from Serious Seeds' own page (01.11.2026: "I'll also need to provide this data")
    data = json.loads((wiki.LIBRARY / "varieties/popular.json").read_text(encoding="utf-8"))
    zid = next(e["reference"]["seed_facts"]["source_id"] for e in data["entries"] if e["key"] == "kali-mist")
    assert (f"Growing, per the source: height average, indoor yield 300 to 500 g/m², flowering about 11 weeks "
            f"(L42, {zid}).") in en["text"]
    assert "In the wiki fact sheet that reads: has a catch, how hard it is stays open." in en["text"]
    assert "The source gives no centimetres" in en["text"]
    (gh,) = ask(["Was weißt du über Neville's Haze?"], lang="de")
    assert "Schwierigkeit schwierig, Höhe hoch, Ertrag innen bis 700 g/m², Blüte 84 bis 98 Tage" in gh["text"]
    assert "Im Steckbrief im Wiki heißt das: eher was für später." in gh["text"]
