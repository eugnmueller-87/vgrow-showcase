"""Hank WP1: types and text basics (fold, tokens, stems, grams, language, token estimate, numbers)."""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path

import pytest

from vgrow.hank import text
from vgrow.hank.types import (
    ROUTES,
    Answer,
    Correction,
    Fact,
    Hit,
    Num,
    Passage,
    Proposal,
    Retrieval,
    Route,
    SourceRef,
    Verdict,
)

REPO = Path(__file__).resolve().parent.parent
WP1_FILES = [REPO / "vgrow" / "hank" / name for name in ("__init__.py", "types.py", "text.py", "typo.py")] + [
    REPO / "tests" / "test_hank_text.py", REPO / "tests" / "test_hank_typo.py"]


# ---------- types ----------

def test_types_import_for_every_later_package() -> None:
    module = importlib.import_module("vgrow.hank.types")
    for name in ("Num", "SourceRef", "Passage", "Hit", "Correction", "Retrieval", "Route", "Fact", "Verdict",
                 "Proposal", "Answer", "Embedder", "Clarify"):
        assert hasattr(module, name), name
    package = importlib.import_module("vgrow.hank")
    source = (REPO / "vgrow" / "hank" / "__init__.py").read_text(encoding="utf-8")
    code = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert package.__doc__ and not code  # docstring only


def test_frozen_types_are_frozen_and_passages_hash_by_id() -> None:
    num = Num(79.0, "%")
    with pytest.raises(dataclasses.FrozenInstanceError):
        num.value = 1.0  # type: ignore[misc]
    passage = Passage("stages/autoflower#notes.2", "stages/autoflower", "stages", {"de": "t"}, {"de": "x"},
                      (Num(12.0),), ("S1",), ("autoflower",))
    assert passage.status == "approved" and passage.meta == {} and not passage.twin_missing
    assert len({passage, passage}) == 1
    fact = Fact("F1", "card", "Ziel 45 bis 60 %", (Num(45.0, "%"), Num(60.0, "%")), (SourceRef("S5", "VIVOSUN", None),))
    verdict = Verdict("probe_humidity", "Luftfeuchte", "high", 79.0, 45.0, 60.0, "%", "all", "early_flower",
                      "estimate", "zu hoch", None, fact)
    assert hash(fact) and hash(verdict)


def test_mutable_types_take_the_appendix_a_fields_in_order() -> None:
    hit = Hit(Passage("a#b", "a", "stages", {}, {}, (), (), ()), 0.5)
    retrieval = Retrieval([hit], 0.5, None)  # type: ignore[arg-type]
    assert retrieval.dense == "off" and retrieval.corrections == [] and retrieval.ms == {}
    route = Route("grow", None, retrieval, None)
    assert route.intent == "info" and route.lang == "de" and route.name in ROUTES
    assert [f.name for f in dataclasses.fields(Correction)] == ["token", "term", "entity", "distance", "mode"]
    assert [f.name for f in dataclasses.fields(Proposal)] == ["id", "kind", "body", "label", "reason", "expires",
                                                               "two_taps"]
    assert [f.name for f in dataclasses.fields(Answer)] == ["id", "route", "lang", "text", "sources", "proposals",
                                                             "clarify", "chips", "draft_only", "note", "timing", "gap",
                                                             "more", "more_sources", "lead", "link", "plan"]  # new fields: at the end


def test_answer_plain_appends_the_source_line() -> None:
    answer = Answer("a-1", "grow", "de", "Ziel 40 bis 50 % [1].",
                    [SourceRef("S5", "VIVOSUN Anleitung", "https://example.org/s5"), SourceRef("N5", "BioBizz", None)])
    assert answer.plain() == ("Ziel 40 bis 50 % [1].\nQuellen: [1] S5 VIVOSUN Anleitung https://example.org/s5; "
                              "[2] N5 BioBizz")
    assert Answer("a-2", "greet", "en", "Hi.").plain() == "Hi."
    assert Answer("a-3", "grow", "en", "x", [SourceRef("S1", "t", None)]).plain().endswith("Sources: [1] S1 t")


# ---------- fold, tokens, stems ----------

def test_fold_umlaut_and_digraph_equivalence() -> None:
    pairs = [("düngen", "duengen"), ("Dünger", "Duenger"), ("Blüte", "Bluete"), ("Trauermücken", "Trauermuecken"),
             ("Größe", "Groesse"), ("Straße", "Strasse"), ("Ähre", "Aehre"), ("Töpfe", "Toepfe"),
             ("Luftfeuchtigkeit", "LUFTFEUCHTIGKEIT"), ("Pflücksalat", "Pfluecksalat"), ("Café", "cafe")]
    for a, b in pairs:
        assert text.fold(a) == text.fold(b), (a, b)
    assert text.fold("düngen") == "dungen" and text.fold("Straße") == "strasse"
    assert text.fold("Feuer") == "feuer" and text.fold("Queen") == "queen"  # ue after a vowel or q stays
    assert text.fold("Bauer") == "bauer"
    assert text.fold("45 % rF, 0,8 kPa") == "45 % rf, 0,8 kpa"  # non-letters untouched


STEM_PAIRS_DE = [
    ("Tomaten", "Tomate"), ("duenge", "düngen"), ("Blüten", "Blüte"), ("Dünger", "düngen"), ("Pflanzen", "Pflanze"),
    ("Blätter", "Blatt"), ("Wurzeln", "Wurzel"), ("Stecklinge", "Steckling"), ("Stecklingen", "Steckling"),
    ("Trauermücken", "Trauermücke"), ("Wochen", "Woche"), ("Tagen", "Tage"), ("Lampen", "Lampe"),
    ("Erbsen", "Erbse"), ("Töpfe", "Topf"), ("Lüfters", "Lüfter"), ("Feuchte", "feucht"), ("Sorten", "Sorte"),
    ("Knospen", "Knospe"), ("Schädlinge", "Schädling"), ("Keimlinge", "Keimling"), ("Blütewochen", "Blütewoche"),
    ("Werte", "Wert"), ("Zelte", "Zelt"), ("Basilikums", "Basilikum"), ("Microgreens", "Microgreen"),
    ("Kräutern", "Kräuter"), ("Pilze", "Pilz"), ("Böden", "Boden"), ("Sonnenblumen", "Sonnenblume"),
]
STEM_PAIRS_EN = [
    ("tomatoes", "tomato"), ("plants", "plant"), ("seedlings", "seedling"), ("flowering", "flower"),
    ("watered", "water"), ("watering", "water"), ("berries", "berry"), ("boxes", "box"),
    ("humidifiers", "humidifier"), ("cuttings", "cutting"), ("dried", "drying"), ("growing", "grows"),
]


def test_stem_table_inflections_40_pairs() -> None:
    assert len(STEM_PAIRS_DE) + len(STEM_PAIRS_EN) >= 40
    for a, b in STEM_PAIRS_DE:
        assert text.tokens(a) == text.tokens(b), (a, b, text.tokens(a), text.tokens(b))
    for a, b in STEM_PAIRS_EN:
        assert text.tokens(a, lang="en") == text.tokens(b, lang="en"), (a, b)
    assert text.tokens("Tomaten") == ["tomat"] and text.tokens("duenge") == ["dung"]
    assert text.stem_word("co2") == "co2" and text.stem_word("vpd") == "vpd"  # digits and short words stay


def test_tokens_hyphen_words_ratios_numbers_and_stop_words() -> None:
    assert text.tokens("Bio-Bloom", stem=False) == ["biobloom", "bio", "bloom"]
    assert text.tokens("Alg-A-Mic", stem=False) == ["algamic", "alg", "mic"]  # "a" is a stop word
    assert text.tokens("auf 12/12 stellen", stem=False) == ["12/12", "12", "12", "stellen"]
    assert text.tokens("0,8 kPa", stem=False) == ["0.8", "kpa"]
    assert text.tokens("Muss ich die Lampe höher stellen?") == ["lamp", "hoh", "stell"]
    assert text.tokens("Is the humidity okay right now?", stem=False) == ["humidity", "okay", "right"]
    assert text.tokens("Ist die Lampe an?", stem=False, stop=False) == ["ist", "die", "lampe", "an"]
    assert "co2" in text.tokens("CO2-Wert", stem=False) and text.tokens("don't", stop=False) == ["dont"]


def test_grams_are_padded_trigrams_without_numbers() -> None:
    assert text.grams("Tomate") == ["#to", "tom", "oma", "mat", "ate", "te#"]
    assert text.grams("rF") == ["#rf", "rf#"]
    assert text.grams("4 ml/l 12/12") == ["#ml", "ml#"]  # "l" is a one-letter word, numbers give no grams
    shared = set(text.grams("Luftfeuchtigkeit")) & set(text.grams("Luftfeuchte"))
    assert {"luf", "uft", "ftf", "tfe", "feu", "euc", "uch", "cht"} <= shared


# ---------- language, token estimate ----------

def test_detect_lang_ties_use_page_lang() -> None:
    assert text.detect_lang("Ist die Luftfeuchte gerade okay?", "en") == "de"
    assert text.detect_lang("Is the humidity okay right now?", "de") == "en"
    assert text.detect_lang("Wieviel ml Biobiz Bio-Bloom soll ich in der 4. Blütewoche geben?", "en") == "de"
    assert text.detect_lang("Which values do I need in early flower?", "de") == "en"
    for tie in ("VPD?", "Bio-Bloom 4 ml", "", "Hank in so"):  # no stop word, or only words of both lists
        assert text.detect_lang(tie, "de") == "de" and text.detect_lang(tie, "en") == "en"
    assert text.detect_lang("VPD?", "fr") == "de"


def test_estimate_tokens_is_chars_over_2_8_rounded_up() -> None:
    assert text.estimate_tokens("") == 0
    assert text.estimate_tokens("abc") == 2
    assert text.estimate_tokens("x" * 28) == 10 and text.estimate_tokens("x" * 29) == 11


# ---------- numbers ----------

def nums(s: str, lang: str = "de") -> list[tuple[float, str]]:
    return [(n.value, n.unit) for n in text.parse_numbers(s, lang)]


def test_parse_numbers_ranges_ratios_decimal_comma() -> None:
    assert nums("45 bis 60 %") == [(45, "%"), (60, "%")]
    assert nums("45-60%") == [(45, "%"), (60, "%")]
    assert nums("45 to 60 %", "en") == [(45, "%"), (60, "%")]
    assert nums("zwischen 45 und 60 %") == [(45, "%"), (60, "%")]
    assert nums("45 und 60 %") == [(45, ""), (60, "%")]  # "und" alone is no range
    assert nums("0,8 kPa") == [(0.8, "kPa")] and nums("0.8 kPa", "en") == [(0.8, "kPa")]
    assert nums("1.400 ml") == [(1400, "ml")] and nums("1.4") == [(1.4, "")] and nums("1,400") == [(1.4, "")]
    assert nums("1,400", "en") == [(1400, "")] and nums("1.4", "en") == [(1.4, "")] and nums("1,5", "en") == [(1.5, "")]
    assert nums("1.000,5 g") == [(1000.5, "g")] and nums("1,000.5 g", "en") == [(1000.5, "g")]
    assert nums("18/6 oder 20/4") == [(18, ""), (6, ""), (20, ""), (4, "")]
    assert text.ratios("18/6 oder 20/4, 1/4 der Menge, 4 ml/l, 10/22/2026") == ["18/6", "20/4", "1/4"]
    assert nums("4 ml/l") == [(4, "ml/l")] and nums("4ml pro Liter") == [(4, "ml/l")]
    assert nums("25°C, 25 °C, 79 %, 79%") == [(25, "°C"), (25, "°C"), (79, "%"), (79, "%")]
    assert nums("79 °C") != nums("79 %")  # same number, different unit: a different claim
    assert nums("1,2 mS/cm und 300 µmol/m²/s, 300 PPFD") == [(1.2, "mS/cm"), (300, "µmol"), (300, "µmol")]
    assert nums("Stand 22.10. und 22.10.2026") == [(22, ""), (10, ""), (22, ""), (10, ""), (2026, "")]
    assert nums("Der pH-Wert 6.2.") == [(6.2, "")]  # one-digit day and month: a decimal, not a date
    assert nums("in der 4. Blütewoche") == [(4, "")] and nums("4. Woche") == [(4, "")]  # ordinal, no unit
    assert nums("Quelle S17, USB 2, CO2, gemma4") == [(2, "")]  # numbers glued to letters are names
    assert nums("sank um 71 %, -2 °C") == [(71, "%"), (2, "°C")]  # signs are ignored
    assert nums("42 Tage, 3 Wochen, 18 h, 90 s, 30 Minuten, 2 Stufen, 25 Punkte") == [
        (42, "d"), (3, "wk"), (18, "h"), (90, "s"), (30, "min"), (2, "step"), (25, "pt")]
    assert nums("5 mal, 3 sind, 10 Lampen") == [(5, ""), (3, ""), (10, "")]  # no unit hidden in a word


def test_parse_numbers_number_words() -> None:
    assert nums("drei Pflanzen, zwei Wochen, zwölf Stunden, zwanzig, hundert") == [
        (3, ""), (2, "wk"), (12, "h"), (20, ""), (100, "")]
    assert nums("eine Woche, einen Tag, eine Pflanze, ein Fehler") == [(1, "wk"), (1, "d")]  # ein only before a unit
    assert nums("halbe Dosis, die doppelte Menge") == [(0.5, ""), (2, "")]
    assert nums("two weeks, one plant, half the dose, an hour, a few days", "en") == [(2, "wk"), (0.5, ""), (1, "h")]
    assert nums("drei Pflanzen", "de") != nums("drei Pflanzen", "en")  # each language reads its own words
    assert text.parse_numbers("drei Pflanzen", "de", words=False) == []
    assert all(isinstance(n, Num) and n.unit in text.UNIT_NAMES | {""} for n in text.parse_numbers(
        "45 bis 60 %, 0,8 kPa, 4 ml/l, 25 °C, 300 W, 30 cm, zwei Tage", "de"))


# ---------- house rules ----------

def test_wp1_files_have_no_em_dash_and_no_bom() -> None:
    for path in WP1_FILES:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r\n" not in raw, path.name
        assert "\u2014" not in raw.decode("utf-8"), path.name
