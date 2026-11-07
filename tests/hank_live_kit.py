"""Test kit for Hank WP6 (context, verdicts, draft, validator): passages, routes and live states without WP4 and WP5.

passage() builds a Passage straight from the cards in kartei/, with the ids of spec 2.1 and the meta keys of appendix
A, in the shape WP4's index is specified to give (text in both languages, numbers parsed, sources as ids). It stands
in for kartei_index only in these tests; the wording of its texts is this kit's, not WP4's. route() wraps passages in
a Route with a Retrieval, as the router hands it on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hank_eval import fixtures
from vgrow.hank import text
from vgrow.hank.types import Hit, Passage, Retrieval, Route

REPO = Path(__file__).resolve().parent.parent
KARTEI = REPO / "kartei"
_IDS = re.compile(r"\b([A-Z]{1,3}\d{1,3})\b")


def _card(kind: str, name: str) -> dict[str, Any]:
    return json.loads((KARTEI / kind / f"{name}.json").read_text(encoding="utf-8"))


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.fold(value)).strip("_")


def _num(value: Any, lang: str) -> str:
    number = float(value)
    out = str(int(number)) if number.is_integer() else str(number)
    return out.replace(".", ",") if lang == "de" else out


def _target_text(target: dict[str, Any], lang: str) -> str:
    label = target.get("label_en" if lang == "en" else "label") or target["metric"]
    join = " bis " if lang == "de" else " to "
    unit = target.get("unit", "")
    return f"{label}: {_num(target['min'], lang)}{join}{_num(target['max'], lang)} {unit} ({', '.join(target['sources'])})"


def _passage(pid: str, kind: str, card: str, title: tuple[str, str], de: str, en: str | None, sources: list[str],
             meta: dict[str, Any] | None = None, status: str = "approved") -> Passage:
    texts = {"de": de} if en is None else {"de": de, "en": en}
    numbers = tuple(dict.fromkeys([*text.parse_numbers(de, "de"), *(text.parse_numbers(en, "en") if en else [])]))
    return Passage(id=pid, card=f"{kind}/{card}", kind=kind, title={"de": title[0], "en": title[1]}, text=texts,
                   numbers=numbers, sources=tuple(dict.fromkeys(sources)), entities=(), status=status,
                   meta=meta or {}, twin_missing=en is None)


def passage(pid: str) -> Passage:
    """One passage by its spec 2.1 id, e.g. "stages/autoflower#early_flower.probe_humidity.all"."""
    head, path = pid.split("#", 1)
    kind, name = head.split("/", 1)
    card = _card(kind, name)
    parts = path.split(".")
    if kind == "stages":
        if parts[0] == "notes":
            i = int(parts[1])
            de, en = card["notes"][i], (card.get("notes_en") or [None] * 99)[i]
            return _passage(pid, kind, name, ("Hinweis", "Note"), de, en, _IDS.findall(de))
        stage = next(s for s in card["stages"] if s["key"] == parts[0])
        meta = {"stage": stage["key"]}
        if parts[1] == "preset_note":
            de, en = stage["preset_note"], stage.get("preset_note_en")
            return _passage(pid, kind, name, (stage["label"], stage["label_en"]), de, en, _IDS.findall(de), meta)
        if parts[1] == "overview":
            de = stage["label"] + ": " + "; ".join(_target_text(t, "de") for t in stage["targets"])
            en = stage["label_en"] + ": " + "; ".join(_target_text(t, "en") for t in stage["targets"])
            if stage.get("preset_note"):
                de += ". " + stage["preset_note"]
                en += ". " + stage["preset_note_en"]
            ids = [s for t in stage["targets"] for s in t["sources"]] + _IDS.findall(de)
            verified = "partial" if any(t.get("verified") == "partial" for t in stage["targets"]) else "supported"
            return _passage(pid, kind, name, (stage["label"], stage["label_en"]), de, en, ids,
                            {**meta, "verified": verified})
        metric, when = parts[1], parts[2]
        target = next(t for t in stage["targets"] if t["metric"] == metric and (t.get("when") or "all") == when)
        de = f"{stage['label']}, {_target_text(target, 'de')}. {target.get('advice_high', '')} {target.get('advice_low', '')}"
        en = (f"{stage['label_en']}, {_target_text(target, 'en')}. {target.get('advice_high_en', '')} "
              f"{target.get('advice_low_en', '')}")
        meta.update(metric=metric, when=when, min=target["min"], max=target["max"], unit=target.get("unit", ""),
                    verified=target.get("verified"))
        return _passage(pid, kind, name, (stage["label"], stage["label_en"]), de.strip(), en.strip(),
                        target["sources"] + _IDS.findall(de), meta)
    if kind == "nutrients":
        status = "draft" if card.get("status") == "draft" else "approved"
        if parts[0] == "notes":
            note = card["notes"][int(parts[1])]
            return _passage(pid, kind, name, ("Hinweis", "Note"), note["text"], note.get("text_en"), note["sources"],
                            status=status)
        page = card["pages"][parts[0]]
        columns = card["weeks"]["columns"]
        index = next(i for i, c in enumerate(columns) if c["key"] == parts[1])
        col = columns[index]
        rows = []
        rows_en = []
        for comp, values in page["rows"].items():
            value = values[index]
            if value is None:
                continue
            rows.append(f"{card['components'][comp]['label']} {_num(value, 'de')} ml/l")
            rows_en.append(f"{card['components'][comp]['label_en']} {_num(value, 'en')} ml/l")
        de = f"{page['label']}, {col['label']}: {', '.join(rows)} ({', '.join(page['sources'])})"
        en = f"{page['label_en']}, {col['label_en']}: {', '.join(rows_en)} ({', '.join(page['sources'])})"
        return _passage(pid, kind, name, (page["label"], page["label_en"]), de, en, page["sources"],
                        {"page": parts[0], "column": parts[1], "week": col.get("bloom_week")}, status)
    if kind == "strains":
        if parts[0] == "facts":
            key = next(k for k in card["facts"] if slug(k) == parts[1])
            de = card["facts"][key]
            en = (card.get("facts_en") or {}).get(key)
            return _passage(pid, kind, name, (f"{card['name']}: {key}", f"{card['name']}: {key}"), de, en,
                            _IDS.findall(de), {"fact": key})
        if parts[0] == "warnings":
            i = int(parts[1])
            de, en = card["warnings"][i], (card.get("warnings_en") or [None] * 99)[i]
            return _passage(pid, kind, name, ("Warnung", "Warning"), de, en, _IDS.findall(de))
    raise KeyError(pid)


def route(question: str, lang: str, hit_ids: list[str] = (), *, intent: str = "info", slots: Any = None,
          corrections: list[Any] = (), name: str = "grow") -> Route:
    hits = [Hit(passage(pid), round(0.9 - 0.1 * i, 2), {}) for i, pid in enumerate(hit_ids)]
    retrieval = Retrieval(hits=hits, confidence=hits[0].score if hits else 0.0,
                          slots=slots if slots is not None else SimpleNamespace(), corrections=list(corrections))
    return Route(name=name, retrieval=retrieval, intent=intent, lang=lang)


def live(name: str = "F22", lang: str = "de", ledger: str | None = None) -> dict[str, Any]:
    """The fixture's sources (call with the day frozen and an isolated state folder)."""
    return fixtures.build(name, lang=lang, ledger=ledger).sources()
