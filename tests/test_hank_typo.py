"""Hank WP1: OSA distance, name directory, typo decision table (spec 2.5, addendum N2)."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest

from vgrow.hank import typo
from vgrow.hank.types import Correction
from vgrow.hank.typo import Vocab, correct, decide, osa, scan, substitute

REPO = Path(__file__).resolve().parent.parent

# The mandatory names of spec 2.5 as a test directory. The real kartei/_hank/vocab.json belongs to WP3.
ENTRIES = [
    {"id": "tomato", "kind": "plant", "de": ["Tomate", "Tomaten", "Zwergtomate"], "en": ["tomato", "tomatoes"]},
    {"id": "chili", "kind": "plant", "de": ["Chili", "Chilis"], "en": ["chili", "chilies"]},
    {"id": "paprika", "kind": "plant", "de": ["Paprika"], "en": ["bell pepper"]},
    {"id": "basil", "kind": "plant", "de": ["Basilikum"], "en": ["basil"]},
    {"id": "chives", "kind": "plant", "de": ["Schnittlauch"], "en": ["chives"]},
    {"id": "lettuce", "kind": "plant", "de": ["Pflücksalat", "Schnittsalat"], "en": ["leaf lettuce"]},
    {"id": "microgreens", "kind": "plant", "de": ["Microgreens"], "en": ["microgreens"]},
    {"id": "pea", "kind": "plant", "de": ["Erbse", "Erbsen"], "en": ["pea", "peas"]},
    {"id": "radish", "kind": "plant", "de": ["Radieschen"], "en": ["radish", "radishes"]},
    {"id": "sunflower", "kind": "plant", "de": ["Sonnenblume", "Sonnenblumen"], "en": ["sunflower"]},
    {"id": "fungus_gnat", "kind": "pest", "de": ["Trauermücke", "Trauermücken"], "en": ["fungus gnat", "fungus gnats"]},
    {"id": "thrips", "kind": "pest", "de": ["Thrips"], "en": ["thrips"]},
    {"id": "powdery_mildew", "kind": "disease", "de": ["Mehltau", "Echter Mehltau"], "en": ["powdery mildew"]},
    {"id": "damping_off", "kind": "disease", "de": ["Umfallkrankheit"], "en": ["damping off"]},
    {"id": "cuttings", "kind": "process", "de": ["Stecklinge", "Steckling"], "en": ["cuttings", "clones"]},
    {"id": "germination", "kind": "process", "de": ["Keimen", "Keimung"], "en": ["germination"]},
    {"id": "drying", "kind": "process", "de": ["Trocknen"], "en": ["drying"]},
    {"id": "curing", "kind": "process", "de": ["Curing"], "en": ["curing"]},
    {"id": "repotting", "kind": "process", "de": ["Umtopfen"], "en": ["repotting"]},
    {"id": "co2", "kind": "term", "de": ["CO2"], "en": ["CO2"]},
    {"id": "nitrogen", "kind": "term", "de": ["Stickstoff"], "en": ["nitrogen"]},
    {"id": "fertilizer", "kind": "term", "de": ["Dünger"], "en": ["fertilizer"]},
    {"id": "biobizz", "kind": "product", "de": ["BioBizz"], "en": ["BioBizz"]},
    {"id": "bio_bloom", "kind": "product", "de": ["Bio-Bloom"], "en": ["Bio-Bloom"]},
    {"id": "bio_grow", "kind": "product", "de": ["Bio-Grow"], "en": ["Bio-Grow"]},
    {"id": "top_max", "kind": "product", "de": ["Top-Max"], "en": ["Top-Max"]},
    {"id": "alg_a_mic", "kind": "product", "de": ["Alg-A-Mic"], "en": ["Alg-A-Mic"]},
    {"id": "light_mix", "kind": "product", "de": ["Light-Mix"], "en": ["Light-Mix"]},
    {"id": "all_mix", "kind": "product", "de": ["All-Mix"], "en": ["All-Mix"]},
    {"id": "autoflower", "kind": "plant", "de": ["Autoflower"], "en": ["autoflower"]},
    {"id": "ice_cream_cake", "kind": "plant", "de": ["Ice Cream Cake"], "en": ["Ice Cream Cake"]},
    {"id": "vgrow", "kind": "device", "de": ["VGrow"], "en": ["VGrow"]},
    {"id": "growcam", "kind": "device", "de": ["GrowCam"], "en": ["GrowCam"]},
    {"id": "vpd", "kind": "metric", "de": ["VPD"], "en": ["VPD"]},
    {"id": "exhaust", "kind": "device", "de": ["Abluft"], "en": ["exhaust fan"]},
    {"id": "circulation", "kind": "device", "de": ["Umluft"], "en": ["circulation fan"]},
    {"id": "humidifier", "kind": "device", "de": ["Befeuchter", "Luftbefeuchter"], "en": ["humidifier"]},
    {"id": "lamp", "kind": "device", "de": ["Lampe"], "en": ["lamp"]},
    {"id": "net", "kind": "device", "de": ["Netz"], "en": ["trellis net"]},
    {"id": "trichomes", "kind": "term", "de": ["Trichome"], "en": ["trichomes"]},
    {"id": "loupe", "kind": "device", "de": ["Lupe"], "en": ["loupe"]},
    {"id": "humidity", "kind": "metric", "de": ["Luftfeuchte", "Luftfeuchtigkeit", "Feuchte", "rF"],
     "en": ["humidity", "relative humidity", "RH"], "maps_to": "probe_humidity",
     "split": {"luftfeuchtigkeit": ["luft", "feuchtigkeit"]}},
]

# The eight typos of the eval set (spec 2.5): token, entity, term. All distance 1, all silent.
TYPOS = [
    ("Tomahte", "tomato", "Tomate"),
    ("Trauermueken", "fungus_gnat", "Trauermücken"),
    ("Luftfeuchtikeit", "humidity", "Luftfeuchtigkeit"),
    ("Basilicum", "basil", "Basilikum"),
    ("Biobiz", "biobizz", "BioBizz"),
    ("Schnitlauch", "chives", "Schnittlauch"),
    ("Stekclinge", "cuttings", "Stecklinge"),
    ("Microgrens", "microgreens", "Microgreens"),
]
TYPO_QUESTIONS = {
    "Tomahte": "Wie viel Licht braucht eine Tomahte in der VGrow?",
    "Trauermueken": "Ich hab Trauermueken in der Erde, was kann ich dagegen machen?",
    "Luftfeuchtikeit": "Wie hoch darf die Luftfeuchtikeit eigentlich noch in der späten Blüte sein?",
    "Basilicum": "Wie feucht darf es für mein Basilicum maximal werden? Hab Angst vor Mehltau.",
    "Biobiz": "Wieviel ml Biobiz Bio-Bloom soll ich in der 4. Blütewoche geben? Hab die Light-Mix Erde.",
    "Schnitlauch": "Mein Schnitlauch zieht plötzlich ein, liegt das am Licht?",
    "Stekclinge": "Wie lange brauchen Basilikum Stekclinge bis die Wurzeln haben und woran seh ich das?",
    "Microgrens": "Wie lang brauchen Erbsen Microgrens bis ich ernten kann?",
}

# 100 real words that must never be corrected (spec 2.5). Some sit within distance 2 of a name on purpose
# (Torte/Tomate, Trips/Thrips, keinen/Keimen, trockenen/Trocknen, Rampe/Lampe).
NOT_TYPOS = [
    "Wasser", "Lampe", "Pause", "Salz", "Kohl", "Erde", "Licht", "Blatt", "Topf", "Boden", "Wurzel", "Knospe",
    "Garten", "Fenster", "Zimmer", "Küche", "Tisch", "Woche", "Monat", "Stunde", "Minute", "Abend",
    "Morgen", "Nacht", "Sonne", "Regen", "Wetter", "Wärme", "Kälte", "Hitze", "Strom", "Kabel",
    "Stecker", "Schalter", "Knopf", "Handy", "Kamera", "Foto", "Menge", "Dosis", "Flasche", "Kanne", "Eimer",
    "Schere", "Messer", "Zange", "Schlauch", "Torte", "Kuchen", "Brot", "Milch", "Zucker",
    "Mehl", "Kinder", "Hund", "Katze", "Mutter", "Freund", "Nachbar", "Arbeit", "Urlaub", "Reise",
    "Rampe", "keinen", "keinem", "wieder", "wirklich", "trocken", "trockenen", "feucht", "gelb", "Farbe", "Zelt",
    "Netz", "Tomaten",
    "water", "lamp", "spoon", "salt", "during", "chips", "trips", "light", "leaf", "leaves", "soil", "week",
    "hours", "minutes", "tomorrow", "today", "garden", "window", "kitchen", "sugar", "bread", "money", "phone",
    "table", "tree",
]


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _strings(value)]
    if isinstance(node, list):
        return [s for value in node for s in _strings(value)]
    return []


def kartei_words() -> list[str]:
    # the glossary is left out like the name directory: its aliases hold misspellings on purpose ("Luftfeuchtikeit"),
    # and the index never reads it into the card words (kartei_index builds its words from passages only)
    files = [REPO / "plant.json", *sorted((REPO / "kartei").rglob("*.json"))]
    return [s for path in files if path.parent.name not in ("_hank", "glossary")
            for s in _strings(json.loads(path.read_text(encoding="utf-8")))]


@pytest.fixture(scope="module")
def vocab() -> Vocab:
    return Vocab(ENTRIES)


@pytest.fixture(scope="module")
def vocab_with_kartei() -> Vocab:
    return Vocab(ENTRIES, words=kartei_words())


# ---------- distance and table ----------

def test_osa_transposition_is_one() -> None:
    assert osa("stekclinge", "stecklinge") == 1  # adjacent swap
    assert osa("ab", "ba") == 1 and osa("abc", "ca") == 3  # OSA, not full Damerau (which gives 2)
    assert osa("tomahte", "tomate") == 1 and osa("biobiz", "biobizz") == 1 and osa("basilicum", "basilikum") == 1
    assert osa("", "abc") == 3 and osa("same", "same") == 0
    assert osa("kitten", "sitting", limit=2) == 3  # beyond the limit: limit + 1
    assert osa("abcdef", "a", limit=3) == 4


def test_decision_table() -> None:
    assert decide(3, 1, False) is None and decide(4, 1, False) is None  # short words: never fuzzy
    assert decide(5, 1, False) == "auto" and decide(8, 1, False) == "auto" and decide(12, 1, False) == "auto"
    assert decide(6, 1, True) == "ask" and decide(10, 1, True) == "ask"  # several candidates
    assert decide(6, 2, False) == "ask" and decide(10, 2, False) == "ask"  # borderline (addendum N2)
    assert decide(6, 3, False) is None and decide(12, 3, False) is None


# ---------- corrections ----------

def test_typo_cases_auto_correct(vocab: Vocab) -> None:
    for token, entity, term in TYPOS:
        fix = correct(token, vocab)
        assert fix == Correction(token, term, entity, 1, "auto"), (token, fix)
        match = scan(TYPO_QUESTIONS[token], vocab)
        assert entity in match.entities() and not match.asks(), (token, match)
        assert term in match.corrected and token not in match.corrected


def test_typo_cases_with_the_card_file_words(vocab_with_kartei: Vocab) -> None:
    """With every card-file word known, "Basilicum" is no typo: the basil card itself writes "Ocimum
    basilicum". Pinned here and reported as an open question (WP3: add Basilicum as a basil name)."""
    for token, entity, term in TYPOS:
        fix = correct(token, vocab_with_kartei)
        if token == "Basilicum":
            assert fix is None and vocab_with_kartei.is_known(token)
        else:
            assert fix is not None and (fix.entity, fix.term, fix.mode) == (entity, term, "auto"), token


def test_not_typos_100_words_untouched(vocab_with_kartei: Vocab) -> None:
    assert len(NOT_TYPOS) == 100 and len({w.lower() for w in NOT_TYPOS}) == 100
    for word in NOT_TYPOS:
        assert correct(word, vocab_with_kartei) is None, word
        assert not scan(f"Ich frage nach {word}", vocab_with_kartei).corrections, word


def test_common_words_are_a_real_guard(vocab: Vocab) -> None:
    near = {word: [f.surface for _, _, f in vocab.candidates(word)] for word in ("Torte", "Rampe", "keinen")}
    assert "Tomate" in near["Torte"] and "Lampe" in near["Rampe"] and "Keimen" in near["keinen"]
    for word in near:
        assert correct(word, vocab) is None, word


def test_not_typos_also_hold_with_the_real_directory() -> None:
    if not typo.VOCAB_FILE.exists():
        pytest.skip("kartei/_hank/vocab.json comes with WP3")
    real = Vocab.load(words=kartei_words())
    assert len(real) > 0
    for word in NOT_TYPOS:
        assert correct(word, real) is None, word


def test_inflected_forms_are_not_typos(vocab_with_kartei: Vocab) -> None:
    for word in ("Tomaten", "Tomate", "duenge", "düngen", "Blüten", "Stecklingen", "Trauermücken", "Trauermuecken",
                 "Basilikums", "Erbsen", "Sonnenblumen", "Befeuchters", "tomatoes", "Luftfeuchte"):
        assert correct(word, vocab_with_kartei) is None, word
        assert not scan(f"Was ist mit {word}?", vocab_with_kartei).asks(), word
    assert [m.entity for m in scan("Meine Tomaten und Erbsen", vocab_with_kartei).mentions] == ["tomato", "pea"]


def test_ambiguous_asks(vocab: Vocab) -> None:
    two = Vocab([*ENTRIES, {"id": "mint", "kind": "plant", "de": ["Minze"], "en": ["mint"]},
                 {"id": "chickweed", "kind": "plant", "de": ["Miere"], "en": ["chickweed"]}])
    fix = two.candidates("Minre")
    assert {f.entity for eff, _, f in fix if eff == 1} == {"mint", "chickweed"}
    assert correct("Minre", two).mode == "ask"  # type: ignore[union-attr]
    assert correct("Tomahtee", vocab) == Correction("Tomahtee", "Tomate", "tomato", 2, "ask")  # distance 2, 8 letters
    assert correct("Domate", vocab).mode == "ask"  # type: ignore[union-attr]  # first letter counts double
    assert correct("Basilicun", vocab).mode == "ask"  # type: ignore[union-attr]  # 9 letters, distance 2
    assert correct("Wasilikum", vocab).mode == "ask"  # type: ignore[union-attr]  # 9 letters, first letter
    assert correct("Tomxyz", vocab) is None  # distance 3
    assert correct("Lupf", vocab) is None and correct("VDP", vocab) is None  # 4 letters, under 4
    match = scan("Wie viel Licht braucht eine Domate?", vocab)
    assert match.asks() and match.asks()[0].term == "Tomate" and "Tomate" not in match.corrected
    assert "tomato" not in match.entities()  # an open question is no mention yet


def test_aliases_and_rejections(vocab: Vocab) -> None:
    assert correct("Lupf", vocab, {"lupf": "Lupe"}) == Correction("Lupf", "Lupe", "loupe", 0, "auto")
    learned = {"learned": {"Domate": {"term": "Tomate", "count": 2}}, "rejected": {}}
    assert correct("Domate", vocab, learned) == Correction("Domate", "Tomate", "tomato", 0, "auto")
    assert scan("Eine Domate", vocab, learned).mentions[0].how == "alias"
    assert correct("Domate", vocab, {"rejected": {"domate": ["Tomate"]}}) is None  # never proposed again
    assert correct("Domate", vocab, {"rejected": [["Domate", "Tomate"]]}) is None
    assert correct("Lupf", vocab, {"lupf": "Gibtsnicht"}) is None  # an alias to an unknown name does nothing
    assert correct("Tomate", vocab, {"tomate": "Lupe"}) is None  # an exact name is never replaced


def test_correct_speed_under_30ms(vocab: Vocab) -> None:
    rng = random.Random(7)
    letters = "abcdefghiklmnoprstuwz"
    extra = [{"id": f"x{i}", "kind": "term", "de": ["".join(rng.choice(letters) for _ in range(rng.randint(5, 12)))],
              "en": []} for i in range(1950)]
    big = Vocab([*ENTRIES, *extra], words=kartei_words())
    assert len(big) >= 2000
    probes = [token for token, _, _ in TYPOS] + ["Wasserkocher", "Ventilator", "Hydrokultur", "Gewaechshaus"]
    worst = 0.0
    for token in probes:
        best = min(_timed(correct, token, big) for _ in range(3))
        worst = max(worst, best)
    assert worst < 0.030, f"slowest correction took {worst * 1000:.1f} ms"


def _timed(fn, *args) -> float:  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


# ---------- directory scan ----------

def test_scan_finds_phrases_hyphen_words_and_compounds(vocab: Vocab) -> None:
    match = scan("Wieviel ml Biobiz Bio-Bloom soll ich in der 4. Blütewoche geben? Hab die Light-Mix Erde.", vocab)
    assert match.entities() == ["biobizz", "bio_bloom", "light_mix"]
    assert [m.how for m in match.mentions] == ["auto", "exact", "exact"]
    assert match.corrected.startswith("Wieviel ml BioBizz Bio-Bloom")
    for q in ("Is the relative humidity okay?", "Wie ist die Luftfeuchte?", "rF zu hoch", "RH too high"):
        assert scan(q, vocab).entities() == ["humidity"], q
    for q in ("Light Mix", "Lightmix", "light-mix", "Light-Mix-Erde"):
        assert scan(q, vocab).entities() == ["light_mix"], q
    assert scan("Ice Cream Cake Automatic", vocab).entities() == ["ice_cream_cake"]
    blom = scan("Wie viel Bio-Blom?", vocab)
    assert blom.entities() == ["bio_bloom"] and blom.corrected == "Wie viel Bio-Bloom?"
    assert scan("Wie hoch ist der CO2-Wert?", vocab).entities() == ["co2"]
    hit = scan("Ich habe Trauermuecken", vocab).mentions[0]  # the folded digraph is an exact name, not a typo
    assert (hit.entity, hit.how, hit.surface) == ("fungus_gnat", "exact", "Trauermuecken")
    assert vocab.entry("humidity")["maps_to"] == "probe_humidity"  # type: ignore[index]
    assert vocab.splits["luftfeuchtigkeit"] == ["luft", "feuchtigkeit"]
    assert vocab.names("humidity")[:2] == ["Luftfeuchte", "Luftfeuchtigkeit"] and "RH" in vocab.names("humidity")


def test_scan_leaves_plain_questions_alone(vocab_with_kartei: Vocab) -> None:
    for q in ("Hi Hank, wie warm ist es?", "Danke dir!", "Wie wird das Wetter morgen?", "Was kostet ein Auto?"):
        match = scan(q, vocab_with_kartei)
        assert not match.mentions and not match.corrections and match.corrected == q, q


def test_substitute_puts_the_confirmed_term_in() -> None:
    fix = Correction("Tomahte", "Tomate", "tomato", 1, "ask")
    assert substitute("Wie viel Licht braucht eine Tomahte? tomahte!", fix) == (
        "Wie viel Licht braucht eine Tomate? Tomate!")
    assert substitute("Tomahtenfeld", fix) == "Tomahtenfeld"  # whole words only


def test_vocab_load_missing_or_broken_file_is_empty(tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
    assert len(Vocab.load(tmp_path / "missing.json")) == 0
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert len(Vocab.load(broken)) == 0
    assert "not readable" in caplog.text
    good = tmp_path / "vocab.json"
    good.write_text(json.dumps({"version": 1, "entries": ENTRIES[:2]}), encoding="utf-8")
    assert Vocab.load(good).lookup("Tomaten")[0].entity == "tomato"
    assert correct("Tomahte", {"entries": ENTRIES[:1]}).entity == "tomato"  # type: ignore[union-attr]
