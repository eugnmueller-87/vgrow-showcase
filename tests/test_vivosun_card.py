"""The VIVOSUN Base A/B card: the label of the owner's two bottles number for number (six photos of 23.10.2026,
hobby vault Gaertnern/raw/etiketten/2026-10-23-vivosun-base-a-b), nothing invented, only the sources that were found
and read, every displayed text with an English twin, and the gaps stated."""

from __future__ import annotations

import json
import re
from pathlib import Path

from vgrow.i18n import localize

REPO = Path(__file__).resolve().parent.parent
CARD_FILE = REPO / "kartei" / "nutrients" / "vivosun-base-ab.json"
BIOBIZZ_FILE = REPO / "kartei" / "nutrients" / "biobizz-2026.json"
# The usage table of both bottles, g per litre: Seedling/Clone Week 1-2 | Vegetative Week 1-4 | Flowering Week 1-4
# (four columns) | Flowering Week 5-8 (four columns). Base A and Base B print the same numbers.
ROW = [0.5, 1, 1.5, 1.5, 1.5, 1.5, 1, 1, 1, 1]
PRINTED = ["Seedling/Clone Week 1-2", "Vegetative Week 1-4", *["Flowering Week 1-4"] * 4, *["Flowering Week 5-8"] * 4]
ANALYSIS = {
    "base_a": [("N", 15), ("K2O", 1.0), ("Ca", 17), ("Fe", 0.09)],
    "base_b": [("N", 4), ("P2O5", 8), ("K2O", 12), ("Mg", 8), ("Cu", 0.003), ("Mn", 0.036), ("Zn", 0.022), ("B", 0.03)],
}


def card() -> dict:  # type: ignore[type-arg]
    return json.loads(CARD_FILE.read_text(encoding="utf-8"))


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


def test_the_card_is_the_label_number_for_number() -> None:
    data = card()
    assert (data["product"], data["unit"], data["status"]) == ("VIVOSUN", "g/l", "draft")
    assert data["measure"]["grams"] == 0.5 and data["measure"]["printed"] == "1 spoon=0.5g"
    assert data["parts"]["mode"] == "equal" and data["parts"]["printed"] == "Use equal amounts of Base A and Base B."
    assert list(data["pages"]) == ["label"]
    weeks = data["weeks"]
    assert [c["key"] for c in weeks["columns"]] == ["propagation", "veg", *(f"bloom_{n}" for n in range(1, 9))]
    assert [c.get("bloom_week") for c in weeks["columns"]] == [None, None, *range(1, 9)]
    assert [c["printed"] for c in weeks["columns"]] == PRINTED
    rows = data["pages"]["label"]["rows"]
    assert rows == {"base_a": ROW, "base_b": ROW}  # equal amounts: both rows hold the same number in every column
    assert "water" not in weeks and "harvest" not in weeks and weeks["end"]["bloom_week"] == 9
    assert {k: c["npk"] for k, c in data["components"].items()} == {"base_a": "15-0-1", "base_b": "4-8-12"}
    for component, expected in ANALYSIS.items():
        got = data["components"][component]["analysis"]
        assert [(re.search(r"\((\w+)\)", a["printed"]).group(1), a["percent"]) for a in got] == expected  # type: ignore[union-attr]
        for a in got:  # the printed line carries the same number as the field
            assert float(re.search(r"([\d.]+)%$", a["printed"]).group(1)) == a["percent"]  # type: ignore[union-attr]


def test_no_medium_is_invented() -> None:
    data = card()
    page = data["pages"]["label"]
    assert page["label"] == "Etikett-Tabelle (nennt kein Medium)" and page["label_en"] == "Label table (names no medium)"
    for text in (page["label"], page["label_en"], data["summary"], data["summary_en"]):
        assert not re.search(r"\b(?:soil|coco|hydro|Erde|Kokos)\b", text.replace("nennt kein Medium", "")
                             .replace("names no medium", "")), text
    medium = next(n for n in data["notes"] if "kein Medium" in n["text"])
    assert "no medium" in medium["text_en"] and medium["sources"] == ["V1"]


def test_sources_are_only_the_ones_found() -> None:
    data = card()
    known = set(data["sources"])
    assert known == {"V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10", "V12", "N5", "N6"}
    cited = [data["measure"], data["parts"], data["weeks"], data["frequency"], *data["pages"].values(), *data["notes"]]
    for item in cited:
        assert item["sources"] and set(item["sources"]) <= known, item
    v1 = data["sources"]["V1"]
    assert "url" not in v1 and "6 Fotos, die du am 23.10.2026 gemacht hast" in v1["title"]
    # no local path and no vault reference on the page (tests/test_safety.py); the photos lie in the owner's notes
    assert not any(w in v1["title"] + v1["title_en"] for w in (":\\", "OneDrive", "Vault", "vault", "Gaertnern"))
    for key, source in data["sources"].items():
        if key == "V1":
            continue
        assert source["url"].startswith("https://"), key
        assert source["retrieved"] == ("2026-10-22" if key in ("N5", "N6") else "2026-10-23"), key


def test_every_displayed_text_has_an_english_twin() -> None:
    data = card()
    pairs = _pairs({k: v for k, v in data.items() if k != "source_notes"})
    assert len(pairs) >= 30
    assert [where for where, _, twin in pairs if not twin] == []
    shown = localize(data, "en")
    assert not re.search(r"[äöüß]", json.dumps({k: v for k, v in shown.items() if k not in ("sources", "source_notes")},
                                                 ensure_ascii=False))


def test_no_em_or_en_dash_anywhere() -> None:
    text = CARD_FILE.read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2013" not in text
    assert not text.startswith("\ufeff") and "\r\n" not in text


def test_shared_dose_factor_sources_are_identical_in_both_cards() -> None:
    biobizz = json.loads(BIOBIZZ_FILE.read_text(encoding="utf-8"))
    for key in ("N5", "N6"):
        assert card()["sources"][key] == biobizz["sources"][key], key


def test_the_gaps_are_stated() -> None:
    data = card()
    notes = " ".join(n["text"] for n in data["notes"])
    assert "Herstelldatum, eine Chargennummer und die Füllmenge sind auf den Fotos nicht zu lesen" in notes
    assert "Das Etikett sagt nicht, wie oft gedüngt wird" in data["frequency"]["text"]
    # the label says nothing about autoflowers: every autoflower sentence is marked as such and cites no V1
    for note in data["notes"]:
        if "Autoflower" in note["text"] and "kein Hinweis auf Autoflower" not in note["text"]:
            assert note.get("package") == "cannabis" and "V1" not in note["sources"], note["text"][:60]
    # the label's own sentence about the end, and that the table stops after flowering week 8 (critique 23.10.2026)
    end = next(n for n in data["notes"] if "Ende der Blütephase" in n["text"])
    assert "endet aber nach Blütewoche 8" in end["text"] and "until the end of the flowering phase" in end["text_en"]
    assert data["weeks"]["end"]["label"] == "Ab Blütewoche 9: die Tabelle endet, keine Menge"
    # no sentence the sources do not carry: the Cronk "restart at 1" claim is gone (critique 23.10.2026, must 1)
    assert "neu bei 1" not in notes and "restarts" not in " ".join(n["text_en"] for n in data["notes"])


def test_berlin_water_note_uses_the_published_numbers() -> None:
    data = card()
    water = next(n for n in data["notes"] if "µS/cm (Tiefwerder)" in n["text"])
    assert "650 µS/cm (Spandau, Stolpe)" in water["text"] and "1040 µS/cm (Tiefwerder)" in water["text"]
    assert "0,3 mS/cm" in water["text"] and "Alle neun liegen darüber" in water["text"] and "BWB" in water["text"]
    hard = next(n for n in data["notes"] if "°dH" in n["text"])
    assert "15,1 bis 23,3 °dH" in hard["text"] and "93 bis 143 mg/l" in hard["text"]
    assert "nicht ermittelt" in hard["text"] and "V3" in hard["sources"]
    # the nine rows as read from the BWB PDF stand in source_notes
    rows = next(s for s in data["source_notes"] if "analysewerte-wasserwerke.pdf" in s)
    for name in ("Beelitzhof", "Friedrichshagen", "Kaulsdorf", "Kladow", "Spandau", "Stolpe", "Tegel", "Tiefwerder",
                 "Wuhlheide"):
        assert name in rows


def test_the_liquid_kit_warning_is_present() -> None:
    liquid = next(n for n in card()["notes"] if "Flüssigkeit" in n["text"])
    assert "V10" in liquid["sources"] and "Milliliter je Gallone" in liquid["text"] and "Gramm je Liter" in liquid["text"]
    assert "milliliters per gallon" in liquid["text_en"] and "grams per liter" in liquid["text_en"]
