"""Hank WP3: the cards Hank reads (stage card, strain card, legal card), the name directory and the chat policy."""

from __future__ import annotations

import json
import re
from pathlib import Path

from vgrow.i18n import localize
from vgrow.plant import evaluate

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "kartei" / "_hank" / "vocab.json"
POLICY = REPO / "vgrow" / "hank" / "data" / "chat_policy.json"
KINDS = {"plant", "product", "device", "metric", "pest", "disease", "process", "stage", "term"}
MANDATORY = ["Tomate", "Chili", "Paprika", "Basilikum", "Schnittlauch", "Pflücksalat", "Microgreens", "Erbse", "Radieschen",
             "Sonnenblume", "Trauermücke", "Thrips", "Mehltau", "Umfallkrankheit", "Stecklinge", "Keimen", "Trocknen", "Curing",
             "Umtopfen", "CO2", "Stickstoff", "BioBizz", "Bio-Bloom", "Bio-Grow", "Top-Max", "Alg-A-Mic", "Light-Mix", "All-Mix",
             "Autoflower", "Ice Cream Cake", "VGrow", "GrowCam", "VPD", "Abluft", "Umluft", "Befeuchter", "Lampe", "Netz",
             "Trichome", "Lupe"]
EM_DASH = chr(0x2014)  # written this way so this file holds no em dash itself
PERSONAL = ("Eugen", "Lukas", "MOC", "Gaertnern", "2000451117")  # as tests/test_safety.py checks for kartei/**


def load(path: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(path.read_text(encoding="utf-8"))


def fold(s: str) -> str:
    """Spec 2.2: lower case, ß to ss, umlauts and ae/oe/ue to a/o/u (not after a vowel or q)."""
    s = s.lower().replace("ß", "ss").replace("ä", "a").replace("ö", "o").replace("ü", "u")
    return re.sub(r"(?<![aeiouq])([aou])e", r"\1", s)


def stage_card() -> dict:  # type: ignore[type-arg]
    return load(REPO / "kartei" / "stages" / "autoflower.json")


def stage(key: str, lang: str = "de") -> dict:  # type: ignore[type-arg]
    return localize(next(s for s in stage_card()["stages"] if s["key"] == key), lang)


def numbers(text: str) -> list[float]:
    return sorted(float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text))


# ---------- name directory ----------

def test_vocab_schema_and_mandatory_terms() -> None:
    vocab = load(VOCAB)
    assert vocab["version"] == 1 and len(vocab["entries"]) >= 250
    ids: set[str] = set()
    owner: dict[str, str] = {}
    card = stage_card()
    stage_keys = {s["key"] for s in card["stages"]}
    metrics = {t["metric"] for s in card["stages"] for t in s.get("targets", [])}
    cards = [load(path) for path in sorted((REPO / "kartei" / "nutrients").glob("*.json"))]  # feeding v2: both cards
    code_keys = {
        "stage": stage_keys,
        "product": {key for c in cards for key in [*c.get("components", {}), *c.get("pages", {})]},
        "device": {"light", "cfan", "dfan"},
        "metric": metrics | {"humidity", "temp", "vpd", "room_temp", "room_humidity", "water_level", "spectrum",
                             "cfan_step", "dfan_step", "sensors_age", "ph", "ec"},
    }
    for entry in vocab["entries"]:
        assert set(entry) <= {"id", "kind", "de", "en", "maps_to", "split"}, entry
        assert re.fullmatch(r"[a-z0-9_]+", entry["id"]) and entry["id"] not in ids, entry["id"]
        ids.add(entry["id"])
        assert entry["kind"] in KINDS, entry["id"]
        for lang in ("de", "en"):
            assert entry[lang] and all(isinstance(f, str) and f.strip() == f and f for f in entry[lang]), (entry["id"], lang)
        forms = {fold(f) for f in entry["de"] + entry["en"]}
        for form in forms:  # one name, one entry: otherwise the typo match could not decide
            assert owner.setdefault(form, entry["id"]) == entry["id"], (form, entry["id"], owner[form])
        if "maps_to" in entry:
            assert entry["maps_to"] in code_keys[entry["kind"]], entry["id"]
        for whole, parts in entry.get("split", {}).items():
            assert whole in forms and whole == fold(whole), (entry["id"], whole)
            assert all(isinstance(p, str) and p == fold(p) and p in whole for p in parts), (entry["id"], whole)
    de_forms = {f for e in vocab["entries"] for f in e["de"]}
    assert [m for m in MANDATORY if m not in de_forms] == []
    humidity = next(e for e in vocab["entries"] if e["id"] == "humidity")
    assert humidity["maps_to"] == "probe_humidity" and {"Luftfeuchte", "Luftfeuchtigkeit"} <= set(humidity["de"])
    assert {"humidity", "RH"} <= set(humidity["en"])


def test_vocab_has_no_en_suffix_keys_names_or_em_dash() -> None:
    raw = VOCAB.read_bytes()
    text = raw.decode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf") and EM_DASH not in text
    for word in PERSONAL:
        assert word not in text, word

    def keys(obj: object) -> list[str]:
        if isinstance(obj, dict):
            return [*obj, *(k for v in obj.values() for k in keys(v))]
        if isinstance(obj, list):
            return [k for v in obj for k in keys(v)]
        return []

    assert not [k for k in keys(load(VOCAB)) if k.endswith("_en")]
    # names only: no links, no source ids, no sentences
    assert "http" not in text and not re.search(r"\b[SWNLQ]\d+\b", text)
    for entry in load(VOCAB)["entries"]:
        for form in entry["de"] + entry["en"]:
            assert len(form.split()) <= 4 and not form.endswith("."), form


# ---------- stage card: consequence fields, S17, review verdicts, English twins ----------

def test_evaluate_row_carries_consequence_and_sources() -> None:
    snap = {"climate": {"probe_humidity": 79.0}, "light": {"level": 60}}
    row = next(r for r in evaluate(stage("early_flower"), snap) if r["metric"] == "probe_humidity")
    assert row["status"] == "high" and row["consequence_sources"] == ["S17"]
    assert "71 %" in row["consequence"] and "78 bis 98 %" in row["consequence"] and "dauerhaft" in row["consequence"]
    assert "70" not in row["advice"] and row["sources"] == ["S5"]  # the range keeps its own source
    assert (row["verified"], row["verified_reason"]) == ("partial", "range")
    en = next(r for r in evaluate(stage("early_flower", "en"), snap) if r["metric"] == "probe_humidity")
    assert "71%" in en["consequence"] and en["consequence_sources"] == ["S17"]
    # 65 % is too humid for early flower, but the trial ran at 78 to 98 %: the card does not carry the consequence there
    mild = next(r for r in evaluate(stage("early_flower"), {"climate": {"probe_humidity": 65.0}}) if r["metric"] == "probe_humidity")
    assert mild["status"] == "high" and mild["consequence"] is None and mild["consequence_sources"] == []
    mid = next(r for r in evaluate(stage("mid_flower"), {"climate": {"probe_humidity": 65.0}}) if r["metric"] == "probe_humidity")
    assert mid["consequence"] == "Dichte Blüten und stehende Luft begünstigen Schimmel." and mid["consequence_sources"] == ["S33"]
    late = next(r for r in evaluate(stage("late_flower"), {"climate": {"probe_humidity": 65.0}}) if r["metric"] == "probe_humidity")
    assert late["status"] == "high" and late["consequence"] is None  # no source of its own in the card: nothing added
    ok = next(r for r in evaluate(stage("early_flower"), {"climate": {"probe_humidity": 50.0}}) if r["metric"] == "probe_humidity")
    assert ok["status"] == "ok" and ok["consequence"] is None and ok["consequence_sources"] == []
    light = next(r for r in evaluate(stage("early_flower"), snap) if r["metric"] == "light_percent")
    assert light["status"] == "low" and light["consequence"] is None and light["verified"] == "supported"


def test_stage_card_s17_numbers_match_in_both_languages() -> None:
    target = next(t for t in next(s for s in stage_card()["stages"] if s["key"] == "early_flower")["targets"]
                  if t["metric"] == "probe_humidity")
    de, en = target["consequence_high"], target["consequence_high_en"]
    assert numbers(de) == numbers(en) == [71.0, 78.0, 98.0]
    assert de.count("%") == en.count("%") == 2
    assert target["consequence_high_sources"] == ["S17"] and target["consequence_high_from"] == 78
    assert "Cherry Berry" in de and "Cherry Berry" in en and "keine Autoflower" in de and "not an autoflower" in en
    assert "dauerhaft" in de and "durchgehend" in de
    # the rounded, context-free sentence is gone from the page text in both languages
    for s in stage_card()["stages"]:
        for t in s.get("targets", []):
            for key in ("advice_high", "advice_low", "advice_high_en", "advice_low_en"):
                assert "S17" not in t.get(key, "") and "rund 70" not in t.get(key, "") and "about 70" not in t.get(key, "")


def test_every_target_carries_a_review_verdict() -> None:
    for s in stage_card()["stages"]:
        for t in s.get("targets", []):
            assert t["verified"] in ("supported", "partial"), (s["key"], t["metric"])
            assert (t["verified"] == "partial") == ("verified_reason" in t), (s["key"], t["metric"])
            assert t.get("verified_reason", "range") in ("range", "derived", "context"), (s["key"], t["metric"])
            if t["metric"] == "light_percent":
                assert t["verified"] == "supported"  # the box preset itself, per the manual


def test_stage_card_summary_and_notes_have_english_twins() -> None:
    card = stage_card()
    assert len(card["notes_en"]) == len(card["notes"]) and card["summary_en"]
    assert "publishes no PPFD" in card["notes_en"][3]  # grow-03 reads English source text now


# ---------- strain card ----------

def test_strain_facts_have_english_twins_without_thc() -> None:
    card = load(REPO / "kartei" / "strains" / "ice-cream-cake-auto.json")
    assert set(card["facts_en"]) == set(card["facts"]) - {"THC"}  # same keys, they are the passage ids
    assert not any("THC" in v or "thc" in v.lower() for v in card["facts_en"].values())
    assert card["summary_en"].startswith("Autoflower, 75% indica")
    for key, text in card["facts_en"].items():
        assert numbers(text) == numbers(card["facts"][key]), key


# ---------- legal card (C1) ----------

def test_legal_card_holds_the_two_checked_basic_rules() -> None:
    card = load(REPO / "kartei" / "legal" / "kcang-grundregeln.json")
    assert (card["id"], card["kind"], card["status"]) == ("kcang-grundregeln", "legal", "draft")  # a human releases it
    facts = {f["id"]: f for f in card["facts"]}
    assert set(facts) == {"plant_limit", "child_protection"}
    limit, child = facts["plant_limit"], facts["child_protection"]
    assert "drei Cannabispflanzen gleichzeitig" in limit["text"] and "18" in limit["text"] and "§ 9 Abs. 1 KCanG" in limit["text"]
    assert limit["value"]["max"] == 3 and limit["sources"] == ["L1"]
    assert "Kindern" in child["text"] and "§ 10 KCanG" in child["text"] and child["sources"] == ["L2", "L3"]
    for fact in card["facts"]:
        assert numbers(fact["text"]) == numbers(fact["text_en"]), fact["id"]
        assert set(fact["sources"]) <= set(card["sources"]), fact["id"]
    for source in card["sources"].values():
        assert source["url"].startswith("https://www.gesetze-im-internet.de/kcang/") and source["retrieved"] == "2026-10-22"
    text = json.dumps(card, ensure_ascii=False)
    # no binding word on cuttings and the plant limit: the statute has no counting rule for them (addendum N3)
    assert "Steckling" not in text and "cutting" not in text.lower()
    assert '"' not in "".join(f["text_en"] for f in card["facts"])  # own words, no quotes


def _texts_en(value: object) -> list[str]:
    if isinstance(value, dict):
        return [v for k, v in value.items() if k.endswith("_en") and isinstance(v, str)] + [
            t for v in value.values() for t in _texts_en(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts_en(v)]
    return []


def test_no_quotation_mark_in_any_card_text_en() -> None:
    """Addendum N3 and critique c6: the manufacturer sentences in their own words, no verbatim quote in a card's
    English text (the BioBizz card still had three; acceptance 23.10.2026, review 3, M2)."""
    quotes = ('"', chr(0x201C), chr(0x201D), chr(0x201E))
    for path in sorted((REPO / "kartei").rglob("*.json")):
        if path.parent.name == "_hank":
            continue
        found = [t for t in _texts_en(load(path)) if any(q in t for q in quotes)]
        assert not found, (path.name, found)


# ---------- chat policy ----------

def test_chat_policy_shape_and_patterns() -> None:
    policy = load(POLICY)
    assert policy["version"] == 1 and EM_DASH not in POLICY.read_text(encoding="utf-8")
    assert {"thc", "cbd", "wirkung", "geschmack", "aroma", "effekt", "terpen"} <= set(policy["strain_fields"]["blocked"])
    for word in ("THC", "Terpene", "Geschmack", "Wirkung", "Rausch"):  # N5
        assert word in policy["blocked_topics"]["terms"]["de"], word
    never = {lang: [re.compile(p, re.IGNORECASE) for p in policy["never_say"][lang]] for lang in ("de", "en")}

    def hit(lang: str, sentence: str) -> bool:
        return any(p.search(sentence) for p in never[lang])

    for sentence in ("Du kannst die Ernte rauchen.", "Die Wirkung setzt nach einer Stunde ein.", "Es schmeckt nach Beeren.",
                     "Verkauf an Freunde ist einfach.", "Du darfst etwas weitergeben.", "Für 8 Euro das Gramm geht das.",
                     "Zum Konsum reicht ein Gramm.", "Der Geschmack wird süßer."):
        assert hit("de", sentence), sentence
    for sentence in ("You can smoke it after curing.", "Vape it at low heat.", "You will get high quickly.",
                     "The flavor gets sweeter.", "You could sell some to a friend.", "Give a bit away to your neighbor.",
                     "The effects of the buds last hours.", "Terpenes shape the taste."):
        assert hit("en", sentence), sentence
    # never_say must not drop what Hank says from the active cards
    card = stage_card()
    de_texts = [card["summary"], *card["notes"]]
    en_texts = [card["summary_en"], *card["notes_en"]]
    for s in card["stages"]:
        de_texts += [s.get("preset_note", "")] + [t.get(k, "") for t in s.get("targets", []) for k in
                                                   ("advice_high", "advice_low", "consequence_high", "consequence_low")]
        en_texts += [s.get("preset_note_en", "")] + [t.get(k, "") for t in s.get("targets", []) for k in
                                                      ("advice_high_en", "advice_low_en", "consequence_high_en", "consequence_low_en")]
    legal = load(REPO / "kartei" / "legal" / "kcang-grundregeln.json")
    de_texts += [legal["summary"], *(f["text"] for f in legal["facts"])]
    en_texts += [legal["summary_en"], *(f["text_en"] for f in legal["facts"])]
    de_texts += ["Die Lampe verbraucht 100 W.", "Das Konsumcannabisgesetz regelt den Eigenanbau.", "Du brauchst das jetzt."]
    en_texts += ["The lamp's power consumption is 100 W.", "Humidity too high: exhaust fan up.", "Dealing with mold early helps."]
    assert [t for t in de_texts if t and hit("de", t)] == []
    assert [t for t in en_texts if t and hit("en", t)] == []
    # the seed shops the strain card cites are the blocked hosts, and their names have replacements
    strain = load(REPO / "kartei" / "strains" / "ice-cream-cake-auto.json")
    hosts = {re.sub(r"^www\.", "", re.search(r"https?://([^/]+)", s)[1]) for s in strain["sources"].values()
             if "vivosun" not in s}
    assert hosts <= set(policy["blocked_source_hosts"]["hosts"])
    # acceptance 23.10.2026 (review 3, M1): the retailers the cards mark as such, and 420seedz as a precaution
    assert {"420seedz.de", "hydrobuilder.com", "trimleaf.com", "johnnyseeds.com", "amazon.de"} <= set(
        policy["blocked_source_hosts"]["hosts"])
    for name, words in policy["vendor_names"]["names"].items():
        assert set(words) == {"de", "en"} and all(words.values()), name
