"""The strain lexicon (owner, 23.10.2026: "Sorten dürfen als Lexikon dokumentiert werden", "alle Sorten sollte jeder
sehen"), read once for the wiki's Sorten area and Hank's strain advice (vgrow/hank/strains.py).

The data: kartei/strains/lexikon.json, a wiki collection ("entries") with "area": "sorten", "private_only": true,
package cannabis, status draft. Every entry carries its facts as text with the source id in brackets (DE and EN) and a
"grow" block with the numbers the filters and the advice compare. An entry with "same_as" ("strains/ice-cream-cake-
auto") is the owner's own hand-made card: the lexicon points to it and never holds a second copy; its numbers come from
that card's "grow" block and its sources from that card's table.

The box: "box" names the growing height (92 cm for one plant) and where it is written (the stage card's S1, the VIVOSUN
manual). fit() sorts a strain into four groups the owner named:
- sure: the indoor height reaches at most the box height ("bis 60 cm", "70 bis 90 cm");
- lst: the lower end fits, the upper end does not ("80 bis 120 cm"): only with low-stress training;
- tall: even the lower end is above the box;
- unknown: no indoor height, or only "bis X" above the box (the lower end is not given, so it is not known to fit).

Code decides: the numbers here are the makers' figures as collected ("Herstellerangabe, ungeprüft"); nothing is
estimated. A public build (public=True) gets an empty lexicon: private_only data never leaves the private install.
Honest about that guard: no running code path passes public=True today (the server is the private install). What
keeps the lexicon out of a public page is scripts/build_preview.py: its check_public refuses private_only data, and
the preview carries no wiki at all. public=True is the switch for a public build that does not exist yet.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

_LOGGER = logging.getLogger(__name__)

LIBRARY = config.REPO / "kartei"
FOLDER = "strains"
AREA = "sorten"
FITS = ("sure", "lst", "unknown", "tall")  # the order of the list and of the advice: sure first, too tall last
FLOWERING = ("auto", "photoperiod")
DIFFICULTIES = ("easy", "medium", "hard")
DRAWINGS = {"auto": "strain-auto"}  # the wiki's drawings (web/img/wiki/drawings.svg); every other strain: "strain"
PLAIN_DRAWING = "strain"
_SID = re.compile(r"(?<![\w])([A-Z]{1,2}\d{1,3})(?![\w])")
_TRAIL_IDS = re.compile(r"\s*\((?:[A-Z]{1,2}\d{1,3}(?:, )?)+\)\s*$")  # "(H2)" or "(B4, H9)" at the end
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
# the order the facts of an entry are told in (wiki tiles and Hank's "Was weißt du über ...?"); "brand" heads them
FACT_ORDER = ("type", "genetics", "bloom", "seed_harvest", "h_in", "h_out", "yield_in", "difficulty", "form")

try:  # Hank's folding (umlauts and ß like the wiki's search); a plain fallback when vgrow/hank cannot be imported
    from .hank.text import fold as _fold
except Exception:  # pragma: no cover - only when vgrow/hank cannot be imported

    def _fold(s: str) -> str:
        s = s.lower().replace("ß", "ss")
        return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def fold(s: Any) -> str:
    return _fold(str(s or ""))


def words(s: Any) -> list[str]:
    return re.findall(r"[^\W_]+", fold(s))


Span = tuple[int | None, int | None]


def _span(value: Any) -> Span | None:
    """[lo, hi] of the data ([null, 120] is "bis 120"), or None when nothing usable is there."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    lo, hi = (v if isinstance(v, (int, float)) and not isinstance(v, bool) else None for v in value)
    if lo is None and hi is None:
        return None
    return (int(lo) if lo is not None else None, int(hi) if hi is not None else None)


def fit_of(height: Span | None, box: int | None) -> str:
    """sure, lst, tall or unknown (module docstring)."""
    if box is None or height is None:
        return "unknown"
    lo, hi = height
    if hi is not None and hi <= box:
        return "sure"
    if lo is not None and lo <= box:
        return "lst"
    if lo is not None and lo > box:
        return "tall"
    return "unknown"


@dataclass(frozen=True)
class Strain:
    key: str  # the lexicon's key (a pointer keeps its own key)
    card: str  # the wiki card the page opens: "strains/<key>" (the owner's card for a pointer)
    label: Mapping[str, str]
    names: tuple[str, ...]  # every name it is known by, folded (the pointer's label and the card's name)
    brand: str
    flowering: str | None
    sex: str | None
    height: Span | None
    height_out: Span | None
    bloom: tuple[int | None, int | None, str] | None  # as the maker gives it: (lo, hi, "weeks" or "days")
    harvest: Span | None  # seed to harvest in days, only where the maker gives it
    yield_in: Span | None
    difficulty: str | None
    genotype: Mapping[str, int] | None
    facts: Mapping[str, Mapping[str, str]]  # lang -> fact key -> text with its source id
    labels: Mapping[str, Mapping[str, str]]  # lang -> fact key -> label
    sources: Mapping[str, Any]  # its own sources only (the ids its texts and grow block cite)
    own: bool  # the owner's hand-made card (same_as)
    drawing: str
    status: str
    package: str | None
    private: bool

    @property
    def folder(self) -> str:
        return self.card.split("/", 1)[0]

    @property
    def card_key(self) -> str:
        return self.card.split("/", 1)[1]

    @property
    def bloom_days(self) -> Span | None:
        if self.bloom is None:
            return None
        lo, hi, unit = self.bloom
        k = 7 if unit == "weeks" else 1
        return (lo * k if lo is not None else None, hi * k if hi is not None else None)

    def time_days(self, basis: str | None = None) -> int | None:
        """The time the maker gives, in days (its upper end; the lower end when only that is there). basis "bloom":
        the flowering time only ("kurze Blüte"); "harvest": seed to harvest only ("höchstens 12 Wochen von Samen bis
        Ernte"); None: seed to harvest where given, else the flowering time. A ranking passes one basis, so a flowering
        time never stands next to a time from seed to harvest (review 23.10.2026, SHOULD 5)."""
        spans = {"bloom": (self.bloom_days,), "harvest": (self.harvest,)}.get(basis or "",
                                                                             (self.harvest, self.bloom_days))
        for span in spans:
            if span is not None:
                return span[1] if span[1] is not None else span[0]
        return None

    def fit(self, box: int | None) -> str:
        return fit_of(self.height, box)

    def visible(self, on: Iterable[str]) -> bool:
        return not self.package or self.package in set(on)

    def search_text(self) -> str:
        """What the Sorten search looks in: the names, the brand and the genetics (no source ids), folded, short."""
        facts = self.facts.get("de") or {}
        genetics = next((v for k, v in facts.items() if k in ("genetics", "Genetik")), "")
        parts = dict.fromkeys([*self.label.values(), self.brand, _TRAIL_IDS.sub("", genetics)])
        return " ".join(fold(p) for p in parts if p)


@dataclass(frozen=True)
class Lexicon:
    strains: tuple[Strain, ...] = ()
    box_cm: int | None = None
    box_source: tuple[str, Any] | None = None  # (id, the stage card's raw source value)
    name: Mapping[str, str] = field(default_factory=dict)
    note: Mapping[str, str] = field(default_factory=dict)
    status: str = "draft"
    package: str | None = None
    private: bool = False

    def get(self, key: str | None) -> Strain | None:
        return next((s for s in self.strains if s.key == key), None)

    def by_card(self, folder: str, key: str) -> Strain | None:
        return next((s for s in self.strains if s.card == f"{folder}/{key}"), None)

    def visible(self, on: Iterable[str]) -> bool:
        return bool(self.strains) and (not self.package or self.package in set(on))

    def text(self, field_: Mapping[str, str], lang: str) -> str:
        return str(field_.get(lang) or field_.get("de") or "")


# ---------- reading ----------

def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        _LOGGER.warning("Strain lexicon: %s not readable (%s), skipped", path, err)
        return None


def _bloom(grow: Mapping[str, Any]) -> tuple[int | None, int | None, str] | None:
    for name, unit in (("flowering_weeks", "weeks"), ("flowering_days", "days")):
        span = _span(grow.get(name))
        if span is not None:
            return (*span, unit)
    return None


def _cited(texts: Iterable[str], table: Mapping[str, Any], extra: Iterable[str] = ()) -> dict[str, Any]:
    ids: list[str] = []
    for s in texts:
        ids += [sid for sid in _SID.findall(str(s)) if sid in table and sid not in ids]
    ids += [sid for sid in extra if sid in table and sid not in ids]
    return {sid: table[sid] for sid in ids}


def _strain(item: Mapping[str, Any], grow: Mapping[str, Any], table: Mapping[str, Any], *, key: str, card: str,
            label: Mapping[str, str], brand: str, own: bool, status: str, package: str | None, private: bool,
            names: Iterable[str], facts: Mapping[str, Any], facts_en: Mapping[str, Any], labels: Mapping[str, Any],
            labels_en: Mapping[str, Any], extra_sources: Iterable[str]) -> Strain:
    flowering = grow.get("flowering") if grow.get("flowering") in FLOWERING else None
    difficulty = grow.get("difficulty") if grow.get("difficulty") in DIFFICULTIES else None
    geno = grow.get("genotype") if isinstance(grow.get("genotype"), Mapping) else None
    f_de = {str(k): str(v) for k, v in facts.items() if isinstance(v, str)}
    f_en = {str(k): str(v) for k, v in facts_en.items() if isinstance(v, str)}
    drawing = item.get("image", {}).get("drawing") if isinstance(item.get("image"), Mapping) else None
    return Strain(
        key=key, card=card, label=label,
        names=tuple(dict.fromkeys(fold(n) for n in names if n)), brand=brand, flowering=flowering,
        sex=str(grow.get("sex")) if grow.get("sex") in ("feminized", "regular") else None,
        height=_span(grow.get("height_indoor_cm")), height_out=_span(grow.get("height_outdoor_cm")),
        bloom=_bloom(grow), harvest=_span(grow.get("seed_to_harvest_days")),
        yield_in=_span(grow.get("yield_indoor_g_m2")), difficulty=difficulty,
        genotype={k: int(v) for k, v in geno.items() if isinstance(v, (int, float))} if geno else None,
        facts={"de": f_de, "en": f_en or f_de},
        labels={"de": {str(k): str(v) for k, v in labels.items()}, "en": {str(k): str(v) for k, v in labels_en.items()}},
        sources=_cited([*f_de.values(), *f_en.values()], table, extra_sources), own=own,
        drawing=str(drawing) if isinstance(drawing, str) else DRAWINGS.get(flowering or "", PLAIN_DRAWING),
        status=status, package=package, private=private)


def _from_file(path: Path, data: Mapping[str, Any], library: Path) -> Lexicon:
    table = data.get("sources") if isinstance(data.get("sources"), Mapping) else {}
    status = str(data.get("status") or "draft")
    package = data.get("package") if isinstance(data.get("package"), str) and data.get("package") else None
    private = data.get("private_only") is True
    out: list[Strain] = []
    seen: set[str] = set()
    for item in data.get("entries") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("key"), str) or not _KEY.match(item["key"]):
            continue
        key = item["key"]
        if key in seen:
            continue
        seen.add(key)
        label = {"de": str(item.get("label") or key), "en": str(item.get("label_en") or item.get("label") or key)}
        brand = str(item.get("brand") or "")
        same = item.get("same_as")
        if isinstance(same, str):  # the owner's own card: its numbers, facts and sources, never a copy here
            folder, _, card_key = same.partition("/")
            card = _read(library / folder / f"{card_key}.json") if folder == FOLDER and _KEY.match(card_key) else None
            if not isinstance(card, Mapping) or not isinstance(card.get("grow"), Mapping):
                _LOGGER.warning("Strain lexicon: %s points to %s, which has no grow block; skipped", key, same)
                continue
            grow = card["grow"]
            own_table = card.get("sources") if isinstance(card.get("sources"), Mapping) else {}
            name = str(card.get("name") or label["de"])
            out.append(_strain(card, grow, own_table, key=key, card=same, label={"de": name, "en": str(
                card.get("name_en") or name)}, brand=brand, own=True, status=str(card.get("status") or status),
                package=package, private=private, names=(name, label["de"], label["en"]),
                facts=card.get("facts") or {}, facts_en=card.get("facts_en") or {},
                labels=card.get("fact_labels") or {}, labels_en=card.get("fact_labels_en") or {},
                extra_sources=[s for s in grow.get("sources") or [] if isinstance(s, str)]))
            continue
        grow = item.get("grow") if isinstance(item.get("grow"), Mapping) else {}
        out.append(_strain(item, grow, table, key=key, card=f"{FOLDER}/{key}", label=label, brand=brand, own=False,
                           status=str(item.get("status") or status), package=package, private=private,
                           names=(label["de"], label["en"]), facts=item.get("facts") or {},
                           facts_en=item.get("facts_en") or {}, labels=item.get("fact_labels") or {},
                           labels_en=item.get("fact_labels_en") or {},
                           extra_sources=[s for s in item.get("also_sources") or [] if isinstance(s, str)]))
    box = data.get("box") if isinstance(data.get("box"), Mapping) else {}
    height = box.get("growing_height_cm")
    box_cm = int(height) if isinstance(height, (int, float)) and not isinstance(height, bool) and height > 0 else None
    box_source = None
    where, sid = box.get("card"), box.get("source")
    if isinstance(where, str) and isinstance(sid, str):
        folder, _, card_key = where.partition("/")
        card = _read(library / folder / f"{card_key}.json") if _KEY.match(folder) and _KEY.match(card_key) else None
        sources = card.get("sources") if isinstance(card, Mapping) and isinstance(card.get("sources"), Mapping) else {}
        if sid in sources:
            box_source = (sid, sources[sid])
    return Lexicon(tuple(out), box_cm, box_source,
                   {"de": str(data.get("name") or "Sortenlexikon"), "en": str(data.get("name_en") or "Strain lexicon")},
                   {"de": str(data.get("note") or ""), "en": str(data.get("note_en") or data.get("note") or "")},
                   status, package, private)


def is_lexicon(data: Any) -> bool:
    """A kartei file that is a strain lexicon: a collection of entries in the Sorten area."""
    return isinstance(data, Mapping) and data.get("area") == AREA and isinstance(data.get("entries"), list)


_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[Any, ...], Lexicon]] = {}


def _signature(library: Path) -> tuple[Any, ...]:
    out = []
    for folder in (FOLDER, "stages"):
        try:
            paths = sorted((library / folder).glob("*.json"))
        except OSError:
            continue
        for path in paths:
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((folder, path.name, st.st_size, st.st_mtime_ns))
    return tuple(out)


def load(library: Path | None = None, *, public: bool = False) -> Lexicon:
    """The lexicon of kartei/strains/ (the first lexicon file; there is one), read again when a file changed. public:
    a public or preview build, which never carries private_only data (an empty lexicon then)."""
    library = Path(library or LIBRARY)
    sig = _signature(library)
    name = str(library)
    with _LOCK:
        hit = _CACHE.get(name)
    if hit is None or hit[0] != sig:
        found = Lexicon()
        for path in sorted((library / FOLDER).glob("*.json")) if (library / FOLDER).is_dir() else []:
            data = _read(path)
            if is_lexicon(data):
                found = _from_file(path, data, library)
                break
        with _LOCK:
            _CACHE[name] = (sig, found)
        hit = (sig, found)
    lex = hit[1]
    return Lexicon() if public and lex.private else lex


# ---------- texts both the wiki and Hank use ----------

_UNITS = {"weeks": ("Wochen", "weeks"), "days": ("Tage", "days"), "cm": ("cm", "cm"), "g_m2": ("g/m²", "g/m²")}


def span_text(span: Span | tuple[int | None, int | None, str] | None, unit: str, lang: str) -> str:
    """ "8 bis 9 Wochen", "bis 120 cm", "60 cm"; English "8 to 9 weeks", "up to 120 cm"."""
    if span is None:
        return ""
    lo, hi = span[0], span[1]
    word = _UNITS.get(unit, (unit, unit))[0 if lang == "de" else 1]
    if lo is None and hi is None:
        return ""
    if lo is None:
        return f"bis {hi} {word}" if lang == "de" else f"up to {hi} {word}"
    if hi is None or lo == hi:
        return f"{lo} {word}"
    return f"{lo} bis {hi} {word}" if lang == "de" else f"{lo} to {hi} {word}"


def ordered_facts(strain: Strain, lang: str) -> list[tuple[str, str, str]]:
    """(key, label, text) of a strain's facts in FACT_ORDER, the brand left out (it heads the entry)."""
    facts = strain.facts.get(lang) or strain.facts.get("de") or {}
    labels = strain.labels.get(lang) or strain.labels.get("de") or {}
    keys = [k for k in FACT_ORDER if k in facts] + [k for k in facts if k not in FACT_ORDER and k != "brand"]
    return [(k, str(labels.get(k) or k), facts[k]) for k in keys]


def source_ids(text: str) -> list[str]:
    return list(dict.fromkeys(_SID.findall(text or "")))


def rows(lex: Lexicon, lang: str, *, current: str | None = None) -> list[dict[str, Any]]:
    """The compact rows of the wiki's Sorten list: the label in the request language, the numbers as numbers (the
    page writes the words), "card" the wiki card the row opens (folder strains). Sorted as the page shows them
    first: sure, with training, unknown, too tall, then by name and brand."""
    out = []
    for s in lex.strains:
        row: dict[str, Any] = {"key": s.key, "card": s.card_key, "label": lex.text(s.label, lang), "brand": s.brand,
                               "flowering": s.flowering, "fit": s.fit(lex.box_cm), "height": s.height,
                               "bloom": s.bloom, "harvest": s.harvest, "difficulty": s.difficulty, "find": s.search_text()}
        if s.drawing != PLAIN_DRAWING:
            row["drawing"] = s.drawing
        if s.own:
            row["own"] = True
        if current and s.card == f"{FOLDER}/{current}":
            row["current"] = True
        out.append(row)
    out.sort(key=lambda r: (FITS.index(r["fit"]), fold(r["label"]), fold(r["brand"])))
    return out


__all__ = ["AREA", "FITS", "FOLDER", "Lexicon", "Strain", "fit_of", "fold", "is_lexicon", "load", "ordered_facts",
           "rows", "source_ids", "span_text", "words"]
