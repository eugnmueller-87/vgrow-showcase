"""The wiki: every card of kartei/ as a page, read at request time, so a new card folder shows up without code.

GET /api/wiki is the index (categories with their cards), GET /api/wiki/card/{folder}/{key} one card, and
GET /api/wiki/search?q= a small search over labels, aliases and texts, typo tolerant. POST /api/wiki/packages turns
the cannabis package on or off; the setting lives in STATE_DIR/settings.json under "packages" (never in the repo).
GET /api/wiki/fertilizers is the Dünger list (every card of kartei/nutrients/, the fertilizer market of 23.10.2026,
with its filters by type and medium and the overview of the types; fertilizers_view). GET /api/wiki/strains is the
strain lexicon's list (the Sorten area, vgrow/strains.py): every strain as a compact row
with its fit in the box, for the page's search, filters and paging. The lexicon's entries (a collection with "area")
stand in that area, not among the category tiles; its private_only data never goes into a public build
(strains_view(public=True), areas_view(public=True)), and its shop and breeder pages stand as address and date, never
as a link.

A card is a JSON file in a subfolder of kartei/ (folders starting with "_" are Hank's own and left out). A file with a
list of keyed entries under one of COLLECTION_FIELDS (the glossary's "terms") gives one card per entry; the entry
inherits the file's status, package and sources. Unknown folders land in "weitere", unknown kinds are drawn by the
page's generic renderer. Nothing here writes into the repository.

Product rules: cards of the cannabis package (stages, strains, problems, legal, and any card with package
"cannabis") are shown only while the package is on. Inside a mixed card (cloning, drying, germination) the cannabis
parts carry the package too and leave with it: a list item {"package": "cannabis", ...}, a string list marked by a
parallel "<field>_packages" list, and {"if_off": {"cannabis": {...}}} for the texts that read differently without
cannabis (pkg_filter). Consumption and medical claims are removed from prose. The separate factual variety
reference exposes the requested numeric THC median with its source and uncertainty, never an effect claim.
Card texts are data; the page sets them as text.

The files are read again only when one of them changed (size or mtime, a stat walk per request), so a new card still
shows up without a restart, and the search scores fields prepared once per language and package setting.

Pictures: a card's "image" field names a photo in web/img/wiki/ (downloaded once from Wikimedia Commons, with its
author, licence and file page) and/or one of the wiki's own drawings (web/img/wiki/drawings.svg), with the steps a
drawing panel belongs to. image_view() checks it (file name, file on disk, licence, https links) and anything wrong is
left out, so the page falls back to the drawing or a plain tile, never a broken picture. The photo's credit also joins
the card's sources. These licensed images use /static/img/wiki/<file>. The factual variety catalogue also supports
source-hosted reference pictures on an exact host/path allowlist; it does not claim an open reuse licence. A
photo that stands for something else says so ("note", with "compare": true for a comparison picture), and a strain
without a drawing of its own gets the wiki's plant drawing (STRAIN_DRAWING): photos of strains come only from the
owner's camera. The new variety reference keeps its external photographs separately and labels representative images.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import threading
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from aiohttp import web

from . import config
from .i18n import current_lang, localize, t

_LOGGER = logging.getLogger(__name__)

LIBRARY = config.REPO / "kartei"

# Folder -> category id; the page names the category (web/i18n.js wiki.cat.*). Order of the index.
FOLDER_CATEGORY = {
    "crops": "pflanzen",
    "health": "health", "varieties": "varieties",
    "stages": "cannabis", "strains": "cannabis", "problems": "cannabis",
    "processes": "vorgaenge",
    "methods": "methoden",
    "nutrients": "duengen",
    "glossary": "glossar",
    "legal": "recht",
}
CATEGORY_ORDER = ("pflanzen", "cannabis", "health", "vorgaenge", "methoden", "duengen", "glossar", "recht", "weitere")
# The fertilizer market (owner, 23.10.2026): every card of kartei/nutrients/ stands in the area "duenger" (its own
# list with filters, GET /api/wiki/fertilizers), not among the category tiles, as the strain lexicon does.
FERTILIZER_AREA = "duenger"
FERTILIZER_FOLDER = "nutrients"
FERTILIZER_OVERVIEW = "fertilizer_types"  # the kind of the overview card that compares the types
OTHER = "weitere"
# Folder -> kind when a card names none. The card's own "kind" wins.
FOLDER_KIND = {
    "health": "plant_condition", "varieties": "variety",
    "crops": "crop", "processes": "process", "stages": "stage", "strains": "strain", "problems": "problem",
    "methods": "method", "nutrients": "nutrient", "glossary": "term", "legal": "legal",
}
FOLDER_ORDER = ("crops", "stages", "strains", "problems", "processes", "methods", "nutrients", "glossary", "legal")
COLLECTION_FIELDS = ("terms", "cards", "entries", "items", "methods", "problems")
# Groups inside a category, in the order of the page (web/i18n.js wiki.group.*): stages and strains first, then the
# problem cards by their own "group" field. A card without a group, or with an unknown one, stands after them.
GROUP_ORDER = ("stages_strains", "naehrstoffe", "schaedlinge", "krankheiten", "stress")
FOLDER_GROUP = {"stages": "stages_strains", "strains": "stages_strains"}
_GROUP_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

CANNABIS = "cannabis"
CANNABIS_FOLDERS = frozenset({"stages", "strains", "varieties", "problems", "legal"})
KNOWN_PACKAGES = (CANNABIS,)
SETTINGS_KEY = "packages"
DEFAULT_PACKAGES = (CANNABIS,)  # this install: on until the owner switches it off
IF_OFF = "if_off"  # {"if_off": {"cannabis": {"label": "...", "label_en": "..."}}}: the text while the package is off
PKG_SUFFIX = "_packages"  # "warnings_packages": ["cannabis", null, ...], one mark per item of warnings and warnings_en

# Never sent to the page: notes for the research ("source_notes" says so itself), the editors' hints ("_..."), and
# the search names (used for the search, not shown).
HIDDEN_KEYS = frozenset({"source_notes", "aliases", IF_OFF})
# Strain cards: cultivation facts only (owner's rule, "nothing that promotes consumption").
CONSUMPTION_RE = re.compile(
    r"\b(?:THC\w*|CBD\w*|CBN|CBG|Cannabinoid\w*|cannabinoid\w*|Wirkung\w*|Rausch\w*|effects?|potency|Potenz\w*)\b")
# A sentence ends at . ! ? before a capital letter, a quote or a bracket, never after an abbreviation ("aw ca. 0.62",
# "z. B. Basilikum"): cutting there would leave half a sentence on the card (review 23.10.2026, curing).
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"„“(\[])")
_ABBREV_RE = re.compile(r"\b(?:ca|bzw|vgl|ggf|evtl|inkl|max|min|Nr|Abs|Art|etc|approx|vs|[A-Za-z])\.$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
_URL_SPLIT = ": http"
MAX_QUERY = 100
MAX_RESULTS = 20
SNIPPET_WORDS = 14
# Strings under these keys are not text a person reads: ids, units, metrics, links.
_NOT_TEXT = frozenset({"key", "id", "kind", "status", "metric", "unit", "when", "url", "verified", "verified_reason",
                       "spectrum", "sources", "preset_sources", "tags", "topic", "package", "retrieved",
                       "relates_to_metric", "verified_at", "version", "product", "image", IF_OFF})

# ---------- pictures ----------
IMAGE_DIR = config.REPO / "web" / "img" / "wiki"
IMAGE_URL = "/static/img/wiki/"
_IMAGE_FILE_RE = re.compile(r"^[a-z0-9-]+\.(?:webp|jpg)$")
_DRAWING_RE = re.compile(r"^[a-z0-9-]{1,40}$")
# a ported licence keeps its country ("CC BY 3.0 US", the Bugwood pictures): still CC BY, with its own deed
LICENSE_RE = re.compile(r"^(?:CC0 1\.0|Public Domain|CC BY(?:-SA)? \d\.\d(?: [A-Z]{2})?)$")
COMMONS_USER = "https://commons.wikimedia.org/wiki/User:"
STRAIN_DRAWING = "strain"  # drawings.svg "strain-1": a plant in the box, for every strain without its own drawing
BLURB_MAX = 140  # a tile's line and a step's bold lead: one sentence, cut at a word past this


# ---------- text helpers: Hank's folding and typo distance when they load, a plain fallback otherwise ----------

try:  # pure helpers without dependencies (vgrow/hank/text.py, typo.py); a broken Hank never costs the wiki
    from .hank.text import fold as _fold
    from .hank.typo import osa as _osa
except Exception:  # pragma: no cover - only when vgrow/hank cannot be imported
    _LOGGER.warning("Wiki: Hank's text helpers not loadable, plain folding")

    def _fold(s: str) -> str:
        s = s.lower().replace("ß", "ss")
        return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))

    def _osa(a: str, b: str, limit: int = 3) -> int:
        if a == b:
            return 0
        if abs(len(a) - len(b)) > limit:
            return limit + 1
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1] if prev[-1] <= limit else limit + 1

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
STOP = frozenset(_fold(w) for w in (
    "der die das ein eine einen einem einer und oder ist sind was wie wo wann warum wer mit von vom zum zur im in am an "
    "auf fuer für bei mein meine meiner the a an and or is are what how where when why who with of to in on at for my "
    "about über ueber zu es ich du").split())


def fold(s: str) -> str:
    return _fold(str(s))


def words(s: str) -> list[str]:
    return _WORD_RE.findall(fold(s))


def typo_limit(word: str) -> int:
    """How many edits a query word may be away from a card word: none below 4 letters, one up to 6, then two."""
    n = len(word)
    return 0 if n < 4 else 1 if n <= 6 else 2


# ---------- settings: the package switch ----------

def settings_path() -> Path:
    return config.STATE_DIR / "settings.json"


def _settings() -> dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def packages() -> list[str]:
    """The packages that are on: settings.json "packages", or this install's default when the key is missing."""
    value = _settings().get(SETTINGS_KEY)
    if not isinstance(value, list):
        return list(DEFAULT_PACKAGES)
    return [p for p in value if isinstance(p, str) and p in KNOWN_PACKAGES]


def set_package(name: str, on: bool) -> list[str]:
    """Switch one package, keeping every other key of settings.json (the language, Hank's mode), in one step."""
    if name not in KNOWN_PACKAGES:
        raise ValueError(name)
    now = [p for p in packages() if p != name] + ([name] if on else [])
    data = {**_settings(), SETTINGS_KEY: now}
    config.atomic_write(settings_path(), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return now


def package_view(on: Iterable[str]) -> dict[str, bool]:
    active = set(on)
    return {p: p in active for p in KNOWN_PACKAGES}


# ---------- reading kartei/ ----------

def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        _LOGGER.warning("Wiki: %s not readable (%s), skipped", path, err)
        return None


def _status(value: Any) -> str:
    """draft unless a card says otherwise: a card without a status is never shown as checked."""
    return value if isinstance(value, str) and value.strip() else "draft"


def _package(folder: str, card: Mapping[str, Any], inherited: str | None = None) -> str | None:
    own = card.get("package")
    if isinstance(own, str) and own:
        return own
    if inherited:
        return inherited
    return CANNABIS if folder in CANNABIS_FOLDERS else None


def _read_entries(library: Path) -> list[dict[str, Any]]:
    """Every card of every subfolder, unlocalized: folder, key, kind, category, status, package, raw, sources."""
    out: list[dict[str, Any]] = []
    if not library.is_dir():
        return out
    folders = sorted((p for p in library.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))),
                     key=lambda p: (FOLDER_ORDER.index(p.name) if p.name in FOLDER_ORDER else len(FOLDER_ORDER), p.name))
    for folder in folders:
        if not NAME_RE.match(folder.name):
            continue
        seen: set[str] = set()
        for path in sorted(folder.glob("*.json")):
            data = _read(path)
            if not isinstance(data, dict):
                continue
            base = {"folder": folder.name, "category": FOLDER_CATEGORY.get(folder.name, OTHER), "file": path.name}
            field = next((f for f in COLLECTION_FIELDS
                          if isinstance(data.get(f), list) and any(isinstance(x, dict) and x.get("key") for x in data[f])),
                         None)
            if field:
                file_status, file_package = data.get("status"), _package(folder.name, data)
                # the strain lexicon (vgrow/strains.py): its entries stand in an area of their own (the Sorten list),
                # not among the category tiles, and are private (private_only: never in a public build)
                area = data.get("area") if isinstance(data.get("area"), str) and _GROUP_RE.match(data["area"]) else None
                private = data.get("private_only") is True
                for item in data[field]:
                    if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                        continue
                    if isinstance(item.get("same_as"), str):  # the owner's own card stands in for it, never a copy
                        continue
                    key = item["key"]
                    if not NAME_RE.match(key) or key in seen:
                        continue
                    seen.add(key)
                    raw = dict(item)
                    extra: dict[str, Any] = {"area": area, "category": area} if area else {}
                    if private:
                        extra["private"] = True
                    out.append({**base, **extra, "key": key,
                                "kind": str(raw.get("kind") or FOLDER_KIND.get(folder.name, "card")),
                                "status": _status(raw.get("status") or file_status),
                                "package": _package(folder.name, raw, file_package), "raw": raw,
                                "sources": data.get("sources") if isinstance(data.get("sources"), dict) else {},
                                "collection": True})
                continue
            key = str(data.get("key") or path.stem)
            if not NAME_RE.match(key) or key in seen:
                key = path.stem
            if key in seen:
                continue
            seen.add(key)
            if folder.name == FERTILIZER_FOLDER:
                base = {**base, "area": FERTILIZER_AREA, "category": FERTILIZER_AREA}
            out.append({**base, "key": key, "kind": str(data.get("kind") or FOLDER_KIND.get(folder.name, "card")),
                        "status": _status(data.get("status")), "package": _package(folder.name, data), "raw": data,
                        "sources": data.get("sources") if isinstance(data.get("sources"), dict) else {},
                        "collection": False})
    return out


_LOCK = threading.Lock()
_ENTRY_CACHE: dict[str, tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}
# the cards' search fields, the vocabulary of all their words, and _near per query word (typing "Trocknen S" does
# not work out "trocknen" again)
_Prepared = tuple[list[dict[str, Any]], frozenset[str], dict[str, tuple[frozenset[str], frozenset[str]]]]
NEAR_CACHE = 512
_SEARCH_CACHE: dict[tuple[str, str, frozenset[str]], tuple[tuple[Any, ...], _Prepared]] = {}


def _signature(library: Path) -> tuple[Any, ...]:
    """(folder, file, size, mtime) of every card file: a stat walk, cheap; a changed, new or deleted card changes it."""
    out: list[tuple[str, str, int, int]] = []
    try:
        folders = sorted(p for p in library.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
    except OSError:
        return ()
    for folder in folders:
        for path in sorted(folder.glob("*.json")):
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((folder.name, path.name, st.st_size, st.st_mtime_ns))
    return tuple(out)


def _load(library: Path) -> tuple[tuple[Any, ...], list[dict[str, Any]]]:
    """The cards with the signature they were read under; read again only when a file changed. The entries are
    shared between requests: nothing may change them (every view below builds copies)."""
    sig = _signature(library)
    name = str(library)
    with _LOCK:
        hit = _ENTRY_CACHE.get(name)
        if hit is not None and hit[0] == sig:
            return hit
    entries = _read_entries(library)
    with _LOCK:
        _ENTRY_CACHE[name] = (sig, entries)
        for k in [k for k, v in _SEARCH_CACHE.items() if k[0] == name and v[0] != sig]:
            del _SEARCH_CACHE[k]
    return sig, entries


def _entries(library: Path) -> list[dict[str, Any]]:
    return _load(library)[1]


def visible(entry: Mapping[str, Any], on: Iterable[str]) -> bool:
    return entry.get("package") is None or entry.get("package") in set(on)


# ---------- one card for the page ----------

def _strip(value: Any) -> Any:
    """A copy without hidden keys ("_..." editor hints, research notes, search names)."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items()
                if isinstance(k, str) and not k.startswith("_") and k not in HIDDEN_KEYS
                and not (k.endswith("_en") and k[:-3] in HIDDEN_KEYS)}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _off(package: Any, active: set[str]) -> bool:
    return isinstance(package, str) and bool(package) and package not in active


def _overrides(card: Mapping[str, Any], active: set[str]) -> dict[str, Any]:
    """The "if_off" texts of every package that is off (later packages win)."""
    out: dict[str, Any] = {}
    table = card.get(IF_OFF)
    if isinstance(table, Mapping):
        for package, texts in table.items():
            if _off(package, active) and isinstance(texts, Mapping):
                out.update(texts)
    return out


def _pkg_filter(value: Any, on: Iterable[str]) -> tuple[Any, bool]:
    """(a copy without the parts of packages that are off, whether anything left). Dict items of any list that carry
    an inactive "package" go; a string list F loses the items its parallel F_packages marks (in F and F_en alike);
    "if_off" replaces texts. The marks never reach the page: no item keeps "package", no F_packages, no if_off."""
    active = set(on)
    changed = False

    def walk(v: Any, top: bool = False) -> Any:
        nonlocal changed
        if isinstance(v, list):
            kept = [x for x in v if not (isinstance(x, Mapping) and _off(x.get("package"), active))]
            changed = changed or len(kept) != len(v)
            return [walk(x) for x in kept]
        if not isinstance(v, Mapping):
            return v
        out: dict[str, Any] = {}
        for k, x in v.items():
            if not isinstance(k, str) or k == IF_OFF or k.endswith(PKG_SUFFIX) or (k == "package" and not top):
                continue
            base = k[:-3] if k.endswith("_en") else k
            marks = v.get(base + PKG_SUFFIX)
            if isinstance(x, list) and isinstance(marks, list):
                gone = {i for i, p in enumerate(marks) if _off(p, active)}
                if gone:
                    changed = True
                    x = [item for i, item in enumerate(x) if i not in gone]
            out[k] = walk(x)
        over = _overrides(v, active)
        if over:
            changed = True
            out.update({k: walk(x) for k, x in over.items() if isinstance(k, str)})
        return out

    return walk(value, top=True), changed


def pkg_filter(value: Any, on: Iterable[str]) -> Any:
    """A card (or a part of one) as it reads with the packages `on`: see _pkg_filter."""
    return _pkg_filter(value, on)[0]


def _sentences(s: str) -> list[str]:
    out: list[str] = []
    for part in _SENTENCE_RE.split(s):
        if out and _ABBREV_RE.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def _clean_text(s: str) -> str:
    """A text without its sentences about THC, cannabinoids or effects (a text without one stays as it is)."""
    if not CONSUMPTION_RE.search(s):
        return s
    return " ".join(part for part in _sentences(s) if not CONSUMPTION_RE.search(part)).strip()


def no_consumption(card: dict[str, Any], kind: str = "") -> dict[str, Any]:
    """Facts about THC and the like leave every card, and so do such sentences, on every card ("nothing that promotes
    consumption is shown"; review 23.10.2026: curing and drying had them). Source titles are not touched: they are
    cut out of the body and parsed apart. The separate variety reference has a validated numeric metadata block;
    this prose filter continues removing effect/medical claims. `kind` stays for callers."""
    out = dict(card)
    for field in ("facts", "facts_en"):
        facts = out.get(field)
        if isinstance(facts, dict):
            out[field] = {k: v for k, v in facts.items()
                          if not CONSUMPTION_RE.search(str(k)) and not CONSUMPTION_RE.search(str(v))}
    for field in ("fact_labels", "fact_labels_en"):
        labels = out.get(field)
        if isinstance(labels, dict):
            out[field] = {k: v for k, v in labels.items() if not CONSUMPTION_RE.search(f"{k} {v}")}
    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return _clean_text(value)
        if isinstance(value, list):
            # only a sentence cut down to nothing leaves; an empty chart cell (None, "-") keeps its place, else a
            # chart row slides into the wrong weeks (fertilizer market 2, 23.10.2026: Terra Flores stood under T1-5)
            return [x for v in value for x in (walk(v),) if not (isinstance(v, str) and x == "")]
        if isinstance(value, dict):
            return {k: (v if k in _NOT_TEXT else walk(v)) for k, v in value.items()}
        return value

    return {k: (v if k in _NOT_TEXT or k.startswith(("facts", "fact_labels")) else walk(v)) for k, v in out.items()}


# The research notes inside a source title ("abgerufen 2026-10-22", "Ratgeber ohne Datum", "Blog eines
# Samenhändlers (Erfahrungsquelle)") are German; in English they are put in English phrase by phrase. The title of a
# work stays as it was published: a German book or law keeps its German name. Longer phrases come first.
_NAME = r"[A-Z][\w'-]*\.?(?: (?:[A-Z]\.|[A-Z][\w'-]*\.?))*"
_PERSON = r"[A-Z][\w'-]*\.? [A-Z][\w'.-]*(?: [A-Z][\w'.-]*)?"
_SOURCE_EN: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(a), b) for a, b in (
    (r"\babgerufen\b", "retrieved"),
    (r"\bRatgeber ohne Autor und Datum\b", "guide, no author or date"),
    (r"\bohne Autor und Datum\b", "no author or date"),
    (r"\bohne Datum auf der Seite\b", "no date on the page"),
    (r"\bRatgeber ohne Datum\b", "guide, undated"),
    (r"\bohne Datum\b", "undated"),
    (r"\bohne Autor\b", "no author"),
    (r"\bStand laut Seitenmetadaten\b", "as of (page metadata)"),
    (r"\blaut Seite aktualisiert\b", "updated (per the page)"),
    (r"\bRatgeber des Boxherstellers\b", "guide by the box maker"),
    (r"\bRatgeber für den Feldanbau\b", "guide for field growing"),
    (r"\bAnbau-Ratgeber\b", "growing guide"),
    (r"\bRatgeber\b", "guide"),
    (r"\bBlog eines Samenhändlers\b", "blog of a seed shop"),
    (r"\bBlog einer Saatgutfirma\b", "blog of a seed company"),
    (r"\bGrower-Blog\b", "grower blog"),
    (r"\bErfahrungsquelle\b", "practical experience"),
    (r"\bFachpresse ohne Versuchsdaten\b", "trade press without trial data"),
    (r"\bFachpresse\b", "trade press"),
    (r"\bFachartikel\b", "research article"),
    (r"\bHerstellerhandbuch\b", "manufacturer's manual"),
    (r"\bZubehörliste und Spezifikation\b", "accessory list and specification"),
    (r"\bProduktseite der Zamnesia-Eigenmarke\b", "product page of Zamnesia's own brand"),
    (r"\bSamenhändler mit (?:eigener Marke|Eigenmarke)\b", "seed shop with its own brand"),
    (r"\bOnline-Shop für Samen und Zubehör\b", "online shop for seeds and supplies"),
    (r"\bSaatgutzüchter und -händler\b", "seed breeder and seller"),
    (r"\bHändlerangabe\b", "retailer's statement"),
    (r"\bSeitentext nennt (.+?) als Entwickler\b", r"the page text names \1 as the breeder"),
    (r"\bHändlerseite\b", "retailer page"),
    (r"\bInfo-Seite der Universität\b", "university info page"),
    (r"\bvorläufiges Konferenzabstract\b", "preliminary conference abstract"),
    (r"\bKonferenzabstract\b", "conference abstract"),
    (r"\bAbstract-Seite\b", "abstract page"),
    (r"\bAbstract über\b", "abstract via"),
    (r"\bInhalt über (.+?) gelesen\b", r"content read via \1"),
    (r"\bPDF über einen Textkonverter gelesen\b", "PDF read through a text converter"),
    (r"\böffentlich geförderter Projektbericht\b", "publicly funded project report"),
    (r"\bbritischer Gartenbau-Wohltätigkeitsverein und kein Extension-Dienst\b",
     "British horticultural charity, not an extension service"),
    (r"\bnicht von einer Extension geprüft\b", "not reviewed by an extension service"),
    (r"\bnur Beleg für das deutsche Wort\b", "only evidence for the German word"),
    (r"\bTitel aus der URL abgeleitet\b", "title taken from the URL"),
    (r"\bTitel auf der Seite abgeschnitten\b", "title cut off on the page"),
    (r"\bDatumsformat der Seite\b", "the page's date format"),
    (r"\bTag und Monat unklar\b", "day and month unclear"),
    (r"\bob mitlaufendes Datum ist ungeprüft\b", "whether the date updates itself is unchecked"),
    (r"\benglische Fassung von\b", "English version of"),
    (r"\bamtliche Fassung\b", "official version"),
    (r"\bUN-Behörde\b", "UN agency"),
    (r"\bBehörde\b", "public authority"),
    (r"\badaptiert nach\b", "adapted from"),
    (r"\bÜberarbeitung\b", "revision"),
    (r"\bnur Abstract gelesen\b", "abstract only"),
    (r"\bBehördenleitlinie, kein Gesetz\b", "authority guideline, not a law"),
    (r"\bBehördenseite\b", "public authority page"),
    (r"\bVortragsfolien, keine Studie\b", "lecture slides, not a study"),
    (r"\bHersteller, kommerziell\b", "manufacturer, commercial"),
    (r"\bHändler, kommerziell\b", "retailer, commercial"),
    (r"\bHerstellerangabe\b", "manufacturer's statement"),
    (r"\bHerstellerseite\b", "manufacturer's page"),
    (r"\bBeratungsdienst\b", "extension service"),
    (r"\bÜbersichtsarbeit\b", "review article"),
    (r"\bMasterarbeit\b", "master's thesis"),
    (r"\bDoktorarbeit\b", "doctoral thesis"),
    (r"\bAbschlussarbeit\b", "thesis"),
    (r"\bInstitutsbericht\b", "institute report"),
    (r"\bFeldversuch\b", "field trial"),
    (r"\bnicht begutachtet\b", "not peer reviewed"),
    (r"\benglisches Abstract\b", "English abstract"),
    (r"\bkommerzielles Kursmaterial auf\b", "commercial course material on"),
    (r"\bLehrseite\b", "teaching page"),
    (r"\bAnbauseite\b", "growing page"),
    (r"\bSeitendatum\b", "page date"),
    (r"\blaut Seite geändert\b", "changed (per the page)"),
    (r"\berstveröffentlicht\b", "first published"),
    (r"\bangenommen\b", "adopted"),
    (r"\bKapitel\b", "chapter"),
    (r"\bBetreuer\b", "supervisor"),
    (r"\bveröffentlicht\b", "published"),
    (r"\bzuletzt geändert\b", "last changed"),
    (r"\baktualisiert\b", "updated"),
    (r"\büberprüft\b", "reviewed"),
    (r"\bgeprüft\b", "checked"),
    (r"\bStand\b", "as of"),
    (r"\bAbschnitte\b", "sections"),
    (r"\bAbschnitt\b", "section"),
    (r"\bTabelle\b", "table"),
    (r"\bS\. (\d+) und (\d+)\b", r"pp. \1 and \2"),
    (r"\bS\. (\d+)", r"p. \1"),
    (r"\bJanuar (\d{4})", r"January \1"), (r"\bFebruar (\d{4})", r"February \1"), (r"\bMärz (\d{4})", r"March \1"),
    (r"\bMai (\d{4})", r"May \1"), (r"\bJuni (\d{4})", r"June \1"), (r"\bJuli (\d{4})", r"July \1"),
    (r"\bOktober (\d{4})", r"October \1"), (r"\bDezember (\d{4})", r"December \1"),
    (r"\b(\d{1,2})\. (January|February|March|April|May|June|July|August|September|October|November|December)\b",
     r"\1 \2"),
    (r"\bvom (\d{1,2} [A-Z][a-z]+ \d{4})", r"of \1"),
    # "und" only where it joins numbers, sections or people ("Walters und Currey (Iowa State University) 2015",
    # "Andy Wyenandt und Jim Simon"), never inside a German name or title: "Weinbau und Gartenbau (LWG)" and
    # "Freiland und Folientunnel" stay
    (r"(\d) und (?=\d|table|section)", r"\1 and "),
    (r"\b(\w+) und (?=table|section)", r"\1 and "),
    (rf"\b({_NAME}) und ({_NAME})(?= \([^)]*\) \d{{4}}| \d{{4}}| et al\.)", r"\1 and \2"),
    (rf"\b({_PERSON}) und ({_PERSON})(?=,|;|\)|$)", r"\1 and \2"),
    (r"(^|, )und (?=[A-Z])", r"\1and "),
))


def source_title_en(title: str) -> str:
    """A source title with its German research notes put in English (the work's own title stays)."""
    for pattern, repl in _SOURCE_EN:
        title = pattern.sub(repl, title)
    return title


def parse_source(sid: str, value: Any, lang: str) -> dict[str, Any]:
    """Both source forms of the cards: "Title: URL" (split at the last ": http") and {title, title_en, url}. In
    English a title without its own title_en gets its research notes in English (source_title_en)."""
    if isinstance(value, Mapping):
        own_en = lang == "en" and bool(value.get("title_en"))
        title = str((value.get("title_en") if own_en else value.get("title")) or sid)
        url = value.get("url")
        if lang == "en" and not own_en:
            title = source_title_en(title)
        return {"id": sid, "title": title, "url": str(url) if url else None}
    raw = str(value or "")
    cut = raw.rfind(_URL_SPLIT)
    title, url = (raw[:cut].strip(), raw[cut + 2:].strip()) if cut >= 0 else (raw.strip() or sid, None)
    return {"id": sid, "title": source_title_en(title) if lang == "en" else title, "url": url}


def _cited(value: Any, known: set[str]) -> list[str]:
    """Source ids a collection entry cites: its "sources" lists and markers like [G3] or (Q1, Q2) in its texts."""
    found: list[str] = []

    def add(sid: str) -> None:
        if sid in known and sid not in found:
            found.append(sid)

    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                if key.endswith("sources") and isinstance(x, str):
                    add(x)
                else:
                    walk(x, key)
        elif isinstance(v, str):
            for sid in re.findall(r"(?<![\w])([A-Z]{1,2}\d{1,3})(?![\w])", v):
                add(sid)

    walk(value)
    return found


def _sources(entry: Mapping[str, Any], lang: str, raw: Any = None) -> list[dict[str, Any]]:
    """The card's sources; a collection entry, or a card that lost parts to a package switched off (`raw`, the
    filtered card), lists only the ones its remaining texts cite."""
    table = entry.get("sources") or {}
    if raw is not None:
        ids = _cited(raw, set(table))
    else:
        ids = _cited(entry["raw"], set(table)) if entry.get("collection") else list(table)
    out = [parse_source(sid, table[sid], lang) for sid in ids]
    for src in out:  # links only with http(s); anything else stays a title
        if src["url"] and not re.match(r"^https?://", src["url"], re.I):
            src["url"] = None
    if entry.get("folder") == "varieties":
        # a seed version's maker page is a shop page: named with its address and day, never a link (the lexicon's
        # rule below); SeedFinder too, its pages carry shop offers
        for src in out:
            value = table.get(src["id"])
            if re.fullmatch(r"Z\d+", src["id"]) and isinstance(value, Mapping):
                host = re.match(r"^https?://(?:www\.)?([^/:?#]+)", src["url"] or "", re.I)
                src["host"] = host.group(1).lower() if host else None
                src["retrieved"] = value.get("retrieved") if isinstance(value.get("retrieved"), str) else None
                src["url"] = None
    if entry.get("private") and entry.get("folder") != "varieties":
        # the lexicon's sources are shop and breeder pages: named with their address and the day they were read,
        # never as a link (Hank's rule "never a shop link" holds on the page too)
        for src in out:
            value = table.get(src["id"])
            host = re.match(r"^https?://(?:www\.)?([^/:?#]+)", src["url"] or "", re.I)
            if host:
                src["host"] = host.group(1).lower()
            if isinstance(value, Mapping) and isinstance(value.get("retrieved"), str):
                src["retrieved"] = value["retrieved"]
            src["url"] = None
    return out


def label_of(card: Mapping[str, Any], key: str) -> str:
    for field in ("label", "name", "title"):
        value = card.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return key


_LABEL_KEYS = ("label", "label_en", "label_short", "label_short_en", "name", "name_en", "title", "title_en")


def _on(on: Iterable[str] | None) -> list[str]:
    return packages() if on is None else list(on)


def _https(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") and len(value) < 600 else None


def _photo(img: Mapping[str, Any], image_dir: Path) -> dict[str, Any] | None:
    """The photo part of an image field, or None when anything about it is wrong (the drawing or a tile stands in)."""
    name, thumb, credit = img.get("file"), img.get("thumb"), img.get("credit")
    if not (isinstance(name, str) and _IMAGE_FILE_RE.match(name) and isinstance(thumb, str)
            and _IMAGE_FILE_RE.match(thumb) and isinstance(credit, Mapping)):
        return None
    if not (image_dir / name).is_file() or not (image_dir / thumb).is_file():
        return None
    work, author, lic = (str(credit.get(k) or "").strip() for k in ("work", "author", "license"))
    lic_url, page_url = _https(credit.get("license_url")), _https(credit.get("page_url"))
    alt = img.get("alt")
    if not (work and author and LICENSE_RE.match(lic) and lic_url and page_url and isinstance(alt, str) and alt.strip()):
        return None
    w, h = img.get("w"), img.get("h")
    size = {"w": w, "h": h} if isinstance(w, int) and isinstance(h, int) and 0 < w <= 1200 and 0 < h <= 2400 else {}
    note = img.get("note")
    remark = {"note": note.strip(), "compare": img.get("compare") is True} if isinstance(note, str) and note.strip() else {}
    # the uploader's profile page, when the author is only a user name (the fungus gnat card: "User:Ich")
    profile = _https(credit.get("author_url"))
    who = {"author_url": profile} if profile and profile.startswith(COMMONS_USER) else {}
    return {"src": IMAGE_URL + name, "thumb": IMAGE_URL + thumb, **size, "alt": alt.strip(), **remark,
            "credit": {"work": work, "author": author, **who, "license": lic, "license_url": lic_url,
                       "page_url": page_url}}


def _drawing(img: Mapping[str, Any]) -> dict[str, Any] | None:
    did = img.get("drawing")
    if not (isinstance(did, str) and _DRAWING_RE.match(did)):
        return None
    steps = []
    for item in img.get("steps") or []:
        if not isinstance(item, Mapping):
            continue
        step, fig = item.get("step"), item.get("fig")
        if (isinstance(step, int) and not isinstance(step, bool) and step > 0 and isinstance(fig, str)
                and fig.startswith(did + "-") and _DRAWING_RE.match(fig)):
            steps.append({"step": step, "fig": fig})
    return {"id": did, "steps": steps}


def _reference_photo(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """One source-hosted reference photograph on the exact allowlist, as a tile (640 px) and a large (1280 px) size
    for the photo on top and the gallery's zoom (owner, 01.11.2026: "really good, high quality pictures"), or None."""
    url, source = raw.get("reference_url"), raw.get("source_url")
    allowed = (r"https://leafly-public\.imgix\.net/strains/(?:reviews/)?photos/[^?#\s]+\.(?:jpg|jpeg|png)",
               r"https://leafly-cms-production\.imgix\.net/wp-content/uploads/[^?#\s]+\.(?:jpg|jpeg|png)",
               r"https://images\.leafly\.com/flower-images/defaults/[a-z0-9/-]+\.png")
    if not (isinstance(url, str) and any(re.fullmatch(pattern, url) for pattern in allowed)):
        return None
    if not (isinstance(source, str) and re.fullmatch(
            r"https://www\.leafly\.com/(?:strains/[a-z0-9-]+|news/strains-products/top-100-marijuana-strains)", source)):
        return None
    return {"src": url + "?w=640&auto=format&fit=crop", "large": url + "?w=1280&auto=format&fit=max",
            "source": source, "representative": raw.get("representative") is True}


def image_view(raw: Any, lang: str, on: Iterable[str], image_dir: Path | None = None,
               kind: str | None = None) -> dict[str, Any] | None:
    """A card's "image" field as the page draws it: {"photo": {...}, "drawing": {...}} (either may be missing), or
    None. An image of a package that is off leaves with the package (a hemp leaf on a general glossary term). A
    strain (`kind`) without a drawing of its own gets the plant drawing, so no strain is ever a grey tile."""
    if kind == "variety" and isinstance(raw, Mapping):
        # Source-hosted photographs are credited as references, never relabeled as open-license images.
        # Exact hosts and paths only: no arbitrary third-party tracking URL from card data.
        main = _reference_photo(raw)
        if main:
            return {"external": main, "drawing": {"id": STRAIN_DRAWING, "steps": []}}
        return {"drawing": {"id": STRAIN_DRAWING, "steps": []}}
    if kind == "strain":
        view = image_view(raw, lang, on, image_dir) or {}
        return {**view, "drawing": view.get("drawing") or {"id": STRAIN_DRAWING, "steps": []}}
    if not isinstance(raw, Mapping) or _off(raw.get("package"), set(on)):
        return None
    img = localize(dict(raw), lang)
    out: dict[str, Any] = {}
    photo = _photo(img, image_dir or IMAGE_DIR)
    if photo:
        out["photo"] = photo
    drawing = _drawing(img)
    if drawing:
        out["drawing"] = drawing
    return out or None


def _cut(sentence: str) -> str:
    sentence = sentence.strip()
    if len(sentence) <= BLURB_MAX:
        return sentence
    head = sentence[:BLURB_MAX].rsplit(" ", 1)[0].rstrip(" ,;:(")
    return head + " …"


# "tile" is a card's own line for its tile (the problem cards: no summary, only lists), so it wins
_BLURB_FIELDS = ("tile", "summary", "what", "text", "description")
_BLURB_CACHE: dict[tuple[str, str, str, frozenset[str]], tuple[Any, str]] = {}


def blurb(entry: Mapping[str, Any], lang: str, on: Iterable[str]) -> str:
    """The card's first sentence (summary, what, text or description; THC rule and package filter applied), for the
    tile in the list. Kept per card, language and packages while the card's data object stays the same."""
    on = list(on)
    key = (f"{entry['folder']}/{entry['key']}", lang, str(entry.get("file", "")), frozenset(on))
    with _LOCK:
        hit = _BLURB_CACHE.get(key)
    if hit is not None and hit[0] is entry["raw"]:
        return hit[1]
    body = _body(entry["raw"], entry["kind"], lang, on)
    text = next((body[f] for f in _BLURB_FIELDS if isinstance(body.get(f), str) and body[f].strip()), "")
    out = _cut(_sentences(text.strip())[0]) if text else ""
    with _LOCK:
        if len(_BLURB_CACHE) > 4096:
            _BLURB_CACHE.clear()
        _BLURB_CACHE[key] = (entry["raw"], out)
    return out


def _leads(steps: Any) -> list[str] | None:
    """Each step's first sentence when it is short enough to read as a heading, else "" (the page bolds it)."""
    if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
        return None
    out = []
    for s in steps:
        first = _sentences(s.strip())[0] if s.strip() else ""
        out.append(first if first and len(first) <= BLURB_MAX and first != s.strip() else "")
    return out


PREFACE = ("Vorweg:", "First:")  # a step that is a caveat about the steps, not a step of its own (no number)


def _prefaces(steps: Any) -> list[bool] | None:
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        return None
    return [s.lstrip().startswith(PREFACE) for s in steps]


_PLANT_CACHE: dict[str, Any] = {"mtime": None, "key": None}


def current_strain() -> str | None:
    """The strain card of the running grow (plant.json "strain_card"), read again when the file changes."""
    from . import plant  # here, not at the top: the wiki loads without the plant module's chain

    path = plant.run_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    if _PLANT_CACHE["mtime"] != mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        name = data.get("strain_card")
        ref = data.get("strain_ref")  # a plant chosen in the cockpit (vgrow/plant_run.py) points into the lexicon
        if name is None and isinstance(ref, Mapping) and ref.get("source") == "lexicon":
            name = ref.get("key")
        _PLANT_CACHE.update(mtime=mtime, key=name if isinstance(name, str) and NAME_RE.match(name) else None)
    return _PLANT_CACHE["key"]


def _current(entry: Mapping[str, Any], current: str | None) -> bool:
    return bool(current) and entry.get("folder") == "strains" and entry.get("key") == current


def _body(raw: Any, kind: str, lang: str, on: Iterable[str]) -> Any:
    """What the page reads of a card: the parts of switched-off packages gone, hidden keys gone, no THC, one language."""
    body = localize(no_consumption(_strip(pkg_filter(raw, on)), kind), lang)
    body.pop("sources", None)
    return body


def card_view(entry: Mapping[str, Any], lang: str, on: Iterable[str] | None = None, current: str | None = None,
              image_dir: Path | None = None) -> dict[str, Any]:
    """One card for the page, as it reads with the packages `on` (default: the switch in settings.json). `current`
    is the running grow's strain card (current_strain()); the page then shows the owner's own camera photo."""
    on = _on(on)
    filtered, changed = _pkg_filter(entry["raw"], on)
    body = localize(no_consumption(_strip(filtered), entry["kind"]), lang)
    body.pop("sources", None)
    body.pop("image", None)
    image = image_view(entry["raw"].get("image"), lang, on, image_dir, entry["kind"])
    sources = _sources(entry, lang, filtered if changed else None)
    if image and image.get("photo"):
        c = image["photo"]["credit"]
        sources.append({"id": "B1", "kind": "image", "title": c["work"], "author": c["author"], "license": c["license"],
                        "license_url": c["license_url"], "url": c["page_url"]})
    if isinstance(body.get("steps"), list):
        leads, prefaces = _leads(body["steps"]), _prefaces(filtered.get("steps"))
        if leads is not None:
            body["steps_lead"] = leads
        if prefaces is not None and len(prefaces) == len(body["steps"]) and any(prefaces):
            body["steps_preface"] = prefaces
    out = {"folder": entry["folder"], "key": entry["key"], "kind": entry["kind"], "category": entry["category"],
           "status": entry["status"], "package": entry["package"], "label": label_of(body, entry["key"]),
           "card": body, "sources": sources, "image": image, "current": _current(entry, current)}
    if entry["kind"] == "variety":
        box_cm = _box_cm()
        body["reference"] = variety_reference(entry["raw"].get("reference"), box_cm)
        body["reference"]["named_variants"] = named_variants(out["label"], lang)
        body["reference"]["family"] = variety_family(entry)
        body["reference"]["box_cm"] = box_cm
        family = body["reference"]["family"]["parents"]
        if (body["reference"]["origin"] or {}).get("landrace") or any((p.get("origin") or {}).get("landrace")
                                                                     for p in family):
            # what a landrace is, in the page's plain words, cites these two
            table = entry.get("sources") or {}
            sources.extend(parse_source(sid, table[sid], lang) for sid in ("LR1", "LR2") if sid in table)
    if entry["kind"] == "strain":
        body.pop("grow", None)  # the numbers travel as "lexicon", worked out; the card shows its sourced texts
        lex = lexicon_view(entry, lang, on)
        if lex is not None:
            out["lexicon"] = lex
            box = lex.get("box_source")
            if box and all(s["id"] != box["id"] for s in sources):
                sources.append(box)
    return out


def variety_reference(raw: Any, box_cm: int | None = None) -> dict[str, Any]:
    """Encyclopedic identity facts only. Missing percentages and availability stay unknown.

    THC is a source's median reference figure, not a tested value or a promised range.
    These numeric facts follow the owner's request for factual potency metadata; the
    existing removal of consumption/medical claims from prose remains in force.
    `box_cm`, the box's growing height, turns a seed version's centimetres into the height step.
    """
    raw = raw if isinstance(raw, Mapping) else {}
    value = raw.get("thc_percent")
    category = raw.get("classification")
    parents = raw.get("parents")
    def names(field: str) -> list[str]:
        value = raw.get(field)
        return [s[:100] for s in value if isinstance(s, str) and s.strip()][:12] if isinstance(value, list) else []

    return {"classification": category if category in ("indica", "sativa", "hybrid") else "unknown",
            "thc_percent": value if type(value) in (int, float) and 0 < value <= 100 else None,
            "thc_basis": "source_median", "indica_percent": None, "sativa_percent": None,
            "parents": [p[:100] for p in parents if isinstance(p, str) and p.strip()][:6] if isinstance(parents, list) else [],
            "aliases": names("aliases"), "terpenes": names("terpenes"), "descendants": names("descendants"),
            "seed_availability": "unverified", "auto_availability": "unverified", "clone_only": "unverified",
            "retrieved": raw.get("retrieved") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw.get("retrieved", ""))) else None,
            "grow": grow_view(raw.get("grow"), raw.get("seed_facts"), box_cm), "origin": origin_view(raw.get("origin")),
            "seed": seed_view(raw.get("seed_facts")), "popularity": popularity_view(raw.get("popularity"))}


# Owner, 01.11.2026: "some sort of a rating system, something like 1 to 10 ... what people are using to measure those".
# What people use is Leafly's grow information, the same three steps seed banks print: difficulty (easy, moderate,
# difficult), height (short, average, tall), yield (low, medium, high), and the flowering time. Each step is a fixed
# point on 1 to 10 and the flowering time one point per five days, so the score never claims more precision than the
# source has. Nothing is averaged into a total, and a step the source does not give stays without a score.
EASE_SCORE = {"easy": 9, "moderate": 6, "difficult": 3}
COMPACT_SCORE = {"short": 9, "average": 6, "tall": 3}
YIELD_SCORE = {"low": 3, "medium": 6, "high": 9}


def speed_score(days: int) -> int:
    """Seven weeks of flowering or less: 10; a point less per five days more; 1 at the slowest (85 days: 3)."""
    return max(1, min(10, int(10 - (days - 49) / 5 + 0.5)))


def _span(value: Any, low: int, high: int) -> list[int] | None:
    """[from, to] in whole numbers within low..high, or None."""
    if (isinstance(value, list) and len(value) == 2 and all(type(v) is int and low <= v <= high for v in value)
            and value[0] <= value[1]):
        return [value[0], value[1]]
    return None


def seed_view(raw: Any) -> dict[str, Any] | None:
    """A seed version's figures (owner, 01.11.2026: "I'll also need to provide this data ... research the internet"):
    the maker's own product page or SeedFinder's breeder entry, read word for word (scripts/enrich_variety_reference.py).
    It fills only what Leafly leaves open, and every figure keeps its source id."""
    if not isinstance(raw, Mapping) or not re.fullmatch(r"Z\d{1,3}", str(raw.get("source_id") or "")):
        return None
    maker, product = raw.get("maker"), raw.get("product")
    if not (isinstance(maker, str) and maker.strip() and isinstance(product, str) and product.strip()):
        return None
    out = {"source_id": raw["source_id"], "maker": maker.strip()[:60], "product": product.strip()[:80],
           "via": "seedfinder" if raw.get("via") == "seedfinder" else "maker",
           "flowering_days": _span(raw.get("flowering_days"), 30, 140),
           "height_cm": _span(raw.get("height_indoor_cm"), 20, 400),
           "yield_g_m2": _span(raw.get("yield_indoor_g_m2"), 50, 1500),
           "upto": [f for f in ("height", "yield") if f in (raw.get("upto") or [])],
           "height_word": raw.get("height_word") if raw.get("height_word") in COMPACT_SCORE else None,
           "yield_word": raw.get("yield_word") if raw.get("yield_word") in YIELD_SCORE else None,
           "difficulty": raw.get("difficulty") if raw.get("difficulty") in EASE_SCORE else None}
    figures = ("flowering_days", "height_cm", "yield_g_m2", "height_word", "yield_word", "difficulty")
    return out if any(out[f] for f in figures) else None


# Popularity (owner, 01.11.2026: "how popular the breed is"): how many people rated it on Leafly. A point per factor
# of ten in reviews, two and a half to the factor: 100 reviews are a 5, 1000 an 8 (7.5 rounds up), 10 000 a 10.
POPULARITY_LEVELS = ((5000, "top"), (2000, "very"), (500, "known"), (100, "uncommon"), (0, "rare"))


def popularity_view(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not re.fullmatch(r"L\d+", str(raw.get("source_id") or "")):
        return None
    reviews = raw.get("reviews")
    if type(reviews) is not int or reviews < 1:
        return None
    score = max(1, min(10, int(2.5 * math.log10(reviews) + 0.5)))
    level = next(name for floor, name in POPULARITY_LEVELS if reviews >= floor)
    return {"reviews": reviews, "score": score, "level": level, "source_id": raw["source_id"],
            "retrieved": raw.get("retrieved") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw.get("retrieved", ""))) else None}


def _height_step(cm: list[int], box_cm: int | None) -> str:
    """A seed version's indoor height against the box: its upper figure fits the box, fits with training down (up to
    half again the box), or does not (owner, 01.11.2026: "this is a very large breed. This breed is extremely good
    for boxes"). Without a box: up to 100 cm short, up to 150 cm average."""
    box = box_cm or 100
    return "short" if cm[1] <= box else "average" if cm[1] <= box * 1.5 else "tall"


def _yield_step(g_m2: list[int]) -> str:
    """A seed version's indoor yield, its middle figure: under 350 g/m² low, up to 500 medium, above that high."""
    middle = sum(g_m2) / 2
    return "low" if middle < 350 else "medium" if middle <= 500 else "high"


def grow_view(raw: Any, seed: Any = None, box_cm: int | None = None) -> dict[str, Any] | None:
    """The grow facts, their 1-to-10 scores and the plain verdict ("good", "fine", "catch", "later", "partial",
    "partial_fine", "partial_catch"), or None without any grow fact. Leafly's grow information first; a seed version's
    figures (seed_view) fill what Leafly leaves open. "from" names each fact's source. The verdict's reasons are codes;
    the page words them."""
    raw = raw if isinstance(raw, Mapping) else {}
    leafly = raw.get("source_id") if isinstance(raw.get("source_id"), str) and re.fullmatch(r"L\d+", raw["source_id"]) else None
    days = raw.get("flowering_days")
    facts: dict[str, Any] = {"difficulty": raw.get("difficulty") if raw.get("difficulty") in EASE_SCORE else None,
                             "height": raw.get("height") if raw.get("height") in COMPACT_SCORE else None,
                             "yield": raw.get("yield") if raw.get("yield") in YIELD_SCORE else None,
                             "flowering_days": days if type(days) is int and 30 <= days <= 140 else None}
    origin: dict[str, dict[str, Any]] = {f: {"id": leafly, "by": "Leafly"} for f, v in facts.items() if v and leafly}
    facts = {f: (v if f in origin else None) for f, v in facts.items()}
    sv = seed_view(seed)
    if sv:
        by = {"id": sv["source_id"], "by": sv["maker"], "via": sv["via"]}
        if not facts["difficulty"] and sv["difficulty"]:
            facts["difficulty"], origin["difficulty"] = sv["difficulty"], {**by, "word": sv["difficulty"]}
        if not facts["height"] and sv["height_cm"]:
            facts["height"], origin["height"] = _height_step(sv["height_cm"], box_cm), {**by, "cm": sv["height_cm"],
                                                                                       "upto": "height" in sv["upto"]}
        elif not facts["height"] and sv["height_word"]:
            facts["height"], origin["height"] = sv["height_word"], {**by, "word": sv["height_word"]}
        if not facts["yield"] and sv["yield_g_m2"]:
            facts["yield"], origin["yield"] = _yield_step(sv["yield_g_m2"]), {**by, "g_m2": sv["yield_g_m2"],
                                                                               "upto": "yield" in sv["upto"]}
        elif not facts["yield"] and sv["yield_word"]:
            facts["yield"], origin["yield"] = sv["yield_word"], {**by, "word": sv["yield_word"]}
        if not facts["flowering_days"] and sv["flowering_days"]:
            facts["flowering_days"] = int(sum(sv["flowering_days"]) / 2 + 0.5)
            origin["flowering_days"] = {**by, "days": sv["flowering_days"]}
    if not any(facts.values()):
        return None
    scores = {"ease": EASE_SCORE.get(facts["difficulty"]), "compact": COMPACT_SCORE.get(facts["height"]),
              "speed": speed_score(facts["flowering_days"]) if facts["flowering_days"] else None,
              "yield": YIELD_SCORE.get(facts["yield"])}
    pros = [code for code, hit in (("easy", scores["ease"] == 9), ("short", scores["compact"] == 9),
                                   ("fast", (scores["speed"] or 0) >= 8), ("high_yield", scores["yield"] == 9)) if hit]
    cons = [code for code, hit in (("difficult", scores["ease"] == 3), ("tall", scores["compact"] == 3),
                                   ("slow", scores["speed"] is not None and scores["speed"] <= 4),
                                   ("low_yield", scores["yield"] == 3)) if hit]
    if scores["ease"] is None:
        # how hard it is stays open, but a catch or a plus that is known is said, not greyed out
        verdict = "partial_catch" if cons else "partial_fine" if pros else "partial"
    elif scores["ease"] == 3:
        verdict = "later"
    elif cons:
        verdict = "catch"
    else:
        verdict = "good" if scores["ease"] == 9 else "fine"
    ids = list(dict.fromkeys(o["id"] for o in origin.values()))
    return {**facts, "weeks": round(facts["flowering_days"] / 7) if facts["flowering_days"] else None,
            "scores": scores, "verdict": verdict, "pros": pros, "cons": cons, "from": origin, "source_ids": ids,
            "source_id": leafly or (ids[0] if ids else None)}


def origin_view(raw: Any) -> dict[str, Any] | None:
    """Breeder, place, era and landrace, each read word for word in the source (scripts/enrich_variety_reference.py),
    or None."""
    if not isinstance(raw, Mapping):
        return None
    breeder = raw.get("breeder") if isinstance(raw.get("breeder"), str) and raw["breeder"].strip() else None
    place = raw.get("place")
    place = ({"en": place["en"][:120], "de": place["de"][:120]} if isinstance(place, Mapping)
             and all(isinstance(place.get(k), str) and place[k].strip() for k in ("en", "de")) else None)
    era = raw.get("era")
    if isinstance(era, Mapping) and type(era.get("year")) is int:
        era = {"year": era["year"]}
    elif isinstance(era, Mapping) and type(era.get("decade")) is int and era.get("part") in ("early", "mid", "late", None):
        era = {"decade": era["decade"], "part": era.get("part")}
    else:
        era = None
    landrace = raw.get("landrace") is True
    if not (breeder or place or era or landrace):
        return None
    return {"breeder": breeder, "place": place, "era": era, "landrace": landrace}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(name))


def variety_family(entry: Mapping[str, Any], library: Path | None = None) -> dict[str, Any]:
    """The variety's parents and descendants as the reference names them, each with its own encyclopedia page when
    there is one, and for a parent with a page its own parents and origin: the family tree two generations deep
    (owner, 01.11.2026: "where did this breed come from? What did they cross it with? So a little bit the science")."""
    pages: dict[str, Mapping[str, Any]] = {}
    for other in _entries(library or LIBRARY):
        if other["folder"] != "varieties":
            continue
        ref = other["raw"].get("reference") or {}
        for name in [other["raw"].get("label") or other["key"], *(ref.get("aliases") or [])]:
            if isinstance(name, str) and _key(name):
                pages.setdefault(_key(name), other)
    ref = variety_reference(entry["raw"].get("reference"))

    def person(name: str, deep: bool) -> dict[str, Any]:
        other = pages.get(_key(name))
        if other is None or other["key"] == entry["key"]:
            return {"name": name, "key": None}
        out: dict[str, Any] = {"name": name, "key": other["key"]}
        if deep:
            theirs = variety_reference(other["raw"].get("reference"))
            out["origin"] = theirs["origin"]
            out["parents"] = [person(p, False) for p in theirs["parents"]]
        return out

    return {"parents": [person(p, True) for p in ref["parents"]],
            "descendants": [person(d, False) for d in ref["descendants"]]}


def _box_cm() -> int | None:
    """The box's growing height (the lexicon's "box"), for "grows tall: in your tent ..."."""
    from . import strains

    return strains.load(LIBRARY).box_cm


def named_variants(label: str, lang: str) -> list[dict[str, Any]]:
    """Exact normalized names only; these products are not asserted to share an original clone's genetics."""
    from . import strains

    def base(value: str) -> str:
        value = re.sub(r"\b(?:auto|automatic|autoflower|feminized|feminisiert|fem|photo|regular)\b", "", fold(value))
        return re.sub(r"[^a-z0-9]", "", value)

    lex = strains.load(LIBRARY)
    return [{"key": strain.card_key, "label": lex.text(strain.label, lang), "brand": strain.brand,
             "flowering": strain.flowering} for strain in lex.strains if base(lex.text(strain.label, lang)) == base(label)]


def varieties_view(lang: str, on: Iterable[str], library: Path | None = None,
                   public: bool = False) -> dict[str, Any] | None:
    """The reference catalogue, distinct from brand-specific products with a similar name."""
    if public:
        return None
    rows = []
    box_cm = _box_cm()
    for entry in _entries(library or LIBRARY):
        if entry["folder"] == "varieties" and visible(entry, on):
            rows.append({**summary(entry, lang, on), "reference": variety_reference(entry["raw"].get("reference"), box_cm),
                         "selection": "rare" if entry["raw"].get("selection") == "rare" else "leafly100"})
    if not rows:
        return None
    rows.sort(key=lambda row: fold(row["label"]))
    return {"count": len(rows), "varieties": rows,
            "selection_source": "https://www.leafly.com/news/strains-products/top-100-marijuana-strains"}


def lexicon_view(entry: Mapping[str, Any], lang: str, on: Iterable[str]) -> dict[str, Any] | None:
    """The lexicon's block of a strain card (vgrow/strains.py): its fit in the box ("sure", "lst", "tall",
    "unknown"), the box height and its source, the numbers, the brand, whether it is the owner's own card the
    lexicon points to, and the maker's-figure note. None when the lexicon does not know the card (a card of a test's
    own library, or the package off)."""
    from . import strains  # here: strains imports Hank's folding, never the wiki

    if entry.get("folder") != strains.FOLDER:
        return None
    lex = strains.load(LIBRARY)
    if not lex.visible(on):
        return None
    found = lex.by_card(str(entry["folder"]), str(entry["key"]))
    if found is None:
        return None
    box = parse_source(*lex.box_source, lang) if lex.box_source is not None else None
    return {"fit": found.fit(lex.box_cm), "box_cm": lex.box_cm, "box_source": box, "flowering": found.flowering,
            "height": found.height, "bloom": found.bloom, "harvest": found.harvest, "difficulty": found.difficulty,
            "brand": found.brand, "own": found.own, "note": lex.text(lex.note, lang)}


def strains_view(lang: str, on: Iterable[str], library: Path | None = None,
                 public: bool = False) -> dict[str, Any] | None:
    """GET /api/wiki/strains: every strain of the lexicon as a compact row for the Sorten list (search, filters and
    paging run on the page), the box with its source, the brands, the note; None while the cannabis package is off or
    there is no lexicon. public: a public build, which never carries the private lexicon (None then)."""
    from . import strains

    lex = strains.load(library or LIBRARY, public=public)
    if not lex.visible(on):
        return None
    box = parse_source(*lex.box_source, lang) if lex.box_source is not None else None
    rows = strains.rows(lex, lang, current=_current_for(library))
    brands: dict[str, int] = {}
    for row in rows:
        brands[row["brand"]] = brands.get(row["brand"], 0) + 1
    return {"name": lex.text(lex.name, lang), "note": lex.text(lex.note, lang), "status": lex.status,
            "box": {"cm": lex.box_cm, "source": box}, "count": len(rows), "strains": rows,
            "brands": [{"name": b, "count": n} for b, n in sorted(brands.items(), key=lambda x: (-x[1], fold(x[0])))]}


def areas_view(on: Iterable[str], library: Path | None = None, public: bool = False) -> list[dict[str, Any]]:
    """The areas beside the categories: the Sorten list (id, how many strains, how many autoflowers, how many fit the
    box for sure; left out while the package is off, without a lexicon, and in a public build) and the Dünger list
    (id, how many lines, how many with a plan the cockpit computes)."""
    from . import strains

    out: list[dict[str, Any]] = []
    reference = varieties_view("en", on, library, public)
    if reference:
        out.append({"id": "varieties", "count": reference["count"]})
    lex = strains.load(library or LIBRARY, public=public)
    if lex.visible(on):
        out.append({"id": strains.AREA, "count": len(lex.strains),
                    "auto": sum(s.flowering == "auto" for s in lex.strains),
                    "sure": sum(s.fit(lex.box_cm) == "sure" for s in lex.strains)})
    lines = [e for e in _entries(library or LIBRARY) if e.get("area") == FERTILIZER_AREA and visible(e, on)
             and e["kind"] != FERTILIZER_OVERVIEW]
    if lines:
        out.append({"id": FERTILIZER_AREA, "count": len(lines), "plans": sum(_selectable(e) for e in lines)})
    return out


def feeding_for_soil(raw: Mapping[str, Any]) -> bool:
    """False for a line that names its media without soil (feeding.for_soil): the list says "not for soil"."""
    from . import feeding  # here: the wiki loads without the planner's chain

    return feeding.for_soil(dict(raw))


def _selectable(entry: Mapping[str, Any]) -> bool:
    """A line whose chart the cockpit computes (feeding.library_setups: a "selectable" block on a readable page)."""
    from . import feeding  # here: the wiki loads without the planner's chain

    return feeding._selectable(str(entry["key"]), dict(entry["raw"])) is not None


_FERTILIZER_WORDS = frozenset(fold(w) for w in ("Düngen", "Duengen", "Dünger", "Duenger", "Düngemittel",
                                                 "fertilizer", "fertilizers", "fertiliser", "nutrients", "feeding"))
TYPE_KEYS = ("organic_liquid", "mineral_liquid", "multi_part", "powder", "slow_release", "pre_fertilised_soil")
MEDIA = ("soil", "coco", "hydro")


def _active_card() -> str | None:
    """The nutrient card of the fertilizer the owner chose on the page (plant log), or None; never raises."""
    try:
        from . import feeding, plant

        profile = plant.load_profile()
        setup = feeding.setup_of(profile) if profile else None
        if not setup or setup.get("choice_pending"):
            return None
        card = setup.get("card")
        return card if isinstance(card, str) else None
    except Exception:  # noqa: BLE001 (a broken plant log never costs the wiki its list)
        _LOGGER.warning("Wiki: the chosen fertilizer is not readable, list without it")
        return None


def fertilizers_view(lang: str, on: Iterable[str], library: Path | None = None) -> dict[str, Any] | None:
    """GET /api/wiki/fertilizers: every line of kartei/nutrients/ as a compact row for the Dünger list (filters by
    type and medium run on the page), the overview card comparing the types, and the line in use. None without a
    line. A row: key, label, maker, types, media, blurb, status, selectable (the cockpit computes its plan), chart
    (it has a chart at all), soil (False for a line that names its media without soil, the owner's medium:
    the page says "not for soil", fertilizer market 2) and current (the fertilizer chosen on the page)."""
    on = list(on)
    entries = [e for e in _entries(library or LIBRARY) if e.get("area") == FERTILIZER_AREA and visible(e, on)]
    lines = [e for e in entries if e["kind"] != FERTILIZER_OVERVIEW]
    if not lines:
        return None
    active = _active_card() if library is None or Path(library) == LIBRARY else None
    overview = next((e for e in entries if e["kind"] == FERTILIZER_OVERVIEW), None)
    labels: dict[str, str] = {}
    if overview is not None:
        for item in localize(overview["raw"].get("types") or [], lang):
            if isinstance(item, dict) and item.get("key") in TYPE_KEYS and isinstance(item.get("label"), str):
                labels[item["key"]] = item["label"]
    rows = []
    for e in lines:
        raw = e["raw"]
        line = raw.get("line") if isinstance(raw.get("line"), Mapping) else {}
        head = summary(e, lang, on)
        rows.append({"key": e["key"], "label": head["label"],
                     "maker": str(line.get("maker") or raw.get("product") or ""),
                     "types": [k for k in line.get("types") or [] if k in TYPE_KEYS],
                     "media": [m for m in line.get("media") or [] if m in MEDIA],
                     "blurb": head["blurb"], "status": e["status"], "selectable": _selectable(e),
                     "chart": bool(raw.get("pages")), "soil": feeding_for_soil(raw),
                     "current": e["key"] == active})
    rows.sort(key=lambda r: (not r["current"], not r["selectable"], fold(r["label"])))
    return {"count": len(rows), "lines": rows,
            "overview": summary(overview, lang, on) if overview is not None else None,
            "types": [{"key": k, "label": labels.get(k, k), "count": sum(k in r["types"] for r in rows)}
                      for k in TYPE_KEYS if any(k in r["types"] for r in rows)],
            "media": [{"key": m, "count": sum(m in r["media"] for r in rows)} for m in MEDIA
                      if any(m in r["media"] for r in rows)]}


def _labels(raw: Mapping[str, Any], on: Iterable[str]) -> dict[str, Any]:
    labels = {k: v for k, v in raw.items() if k in _LABEL_KEYS}
    labels.update({k: v for k, v in _overrides(raw, set(on)).items() if k in _LABEL_KEYS})
    return labels


def group_of(entry: Mapping[str, Any]) -> str | None:
    """The group a card stands in inside its category: its own "group" (the problem cards), or its folder's
    (FOLDER_GROUP), or None. The page heads each group with its name (web/i18n.js wiki.group.*)."""
    own = entry["raw"].get("group")
    if isinstance(own, str) and _GROUP_RE.match(own):
        return own
    return FOLDER_GROUP.get(entry["folder"])


def group_rank(group: str | None) -> int:
    return GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER)


def summary(entry: Mapping[str, Any], lang: str, on: Iterable[str] | None = None, current: str | None = None,
            image_dir: Path | None = None) -> dict[str, Any]:
    on = _on(on)
    body = localize(_labels(entry["raw"], on), lang)
    # a tile may carry a shorter title ("label_short"): the full one with its caveats stands on the card itself
    short = body.get("label_short")
    label = short.strip() if isinstance(short, str) and short.strip() else label_of(body, entry["key"])
    return {"folder": entry["folder"], "key": entry["key"], "kind": entry["kind"], "label": label,
            "status": entry["status"], "package": entry["package"], "blurb": blurb(entry, lang, on),
            "group": group_of(entry),
            "image": image_view(entry["raw"].get("image"), lang, on, image_dir, entry["kind"]),
            "current": _current(entry, current)}


def _current_for(library: Path | None) -> str | None:
    """The running grow's strain, only for the cockpit's own kartei (a test's library has no grow)."""
    return current_strain() if library is None or Path(library) == LIBRARY else None


def index_view(lang: str, on: Iterable[str], library: Path | None = None) -> dict[str, Any]:
    on = list(on)
    current = _current_for(library)
    groups: dict[str, list[dict[str, Any]]] = {}
    hidden = 0
    for entry in _entries(library or LIBRARY):
        if not visible(entry, on):
            hidden += 1
            continue
        if entry.get("area"):  # the Sorten list has a view of its own (strains_view), not one tile per strain here
            continue
        groups.setdefault(entry["category"], []).append(summary(entry, lang, on, current))
    categories = []
    for cid in CATEGORY_ORDER:
        cards = groups.get(cid)
        if cards:
            # by group first (the cannabis category: stages and strains, then the problems by their "group"), then
            # folder and name, so the page can head every group once
            cards.sort(key=lambda c: (group_rank(c.get("group")),
                                      FOLDER_ORDER.index(c["folder"]) if c["folder"] in FOLDER_ORDER else 99,
                                      fold(c["label"])))
            categories.append({"id": cid, "cards": cards})
    return {"categories": categories, "packages": package_view(on), "hidden": hidden,
            "count": sum(len(c["cards"]) for c in categories),
            "drafts": sum(c["status"] == "draft" for cat in categories for c in cat["cards"]),
            "areas": areas_view(on, library)}


def find(folder: str, key: str, library: Path | None = None) -> dict[str, Any] | None:
    if not NAME_RE.match(folder or "") or not NAME_RE.match(key or ""):
        return None
    return next((e for e in _entries(library or LIBRARY) if e["folder"] == folder and e["key"] == key), None)


# ---------- search ----------

def _texts(value: Any, key: str = "") -> Iterable[str]:
    if key in _NOT_TEXT:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _texts(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v, key)


def _aliases(raw: Mapping[str, Any]) -> list[str]:
    value = raw.get("aliases")
    found: list[str] = []
    if isinstance(value, list):
        found = [str(v) for v in value if isinstance(v, str)]
    elif isinstance(value, dict):
        for v in value.values():
            if isinstance(v, list):
                found += [str(x) for x in v if isinstance(x, str)]
    return found


def query_words(q: str) -> list[str]:
    """The folded words of a query without stop words; a query of stop words only finds nothing (no wild guess)."""
    return list(dict.fromkeys(w for w in words(q) if len(w) >= 2 and w not in STOP))


def _near(word: str, vocab: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """(the words starting with `word`, the words within its typo limit) out of a vocabulary: worked out once per
    query word over every card's words together, so each card then only asks whether it holds one of them."""
    starts = frozenset(w for w in vocab if w.startswith(word)) if len(word) >= 3 else frozenset()
    limit = typo_limit(word)
    if not limit:
        return starts, frozenset()
    return starts, frozenset(w for w in vocab if abs(len(w) - len(word)) <= limit and _osa(word, w, limit) <= limit)


def _match(word: str, field_words: frozenset[str] | set[str], folded: str,
           near: tuple[frozenset[str], frozenset[str]] | None = None) -> tuple[int, bool]:
    """(points 0..4, typo) of one query word against one field: 4 a whole word, 3 a word start, 2 inside a word
    (from 4 letters on: "ice" is no hit in "advice"), 1 within the typo limit. `near` is _near(word, ...) over a
    vocabulary holding the field's words; without it the field's own words are walked."""
    if word in field_words:
        return 4, False
    starts, typos = near if near is not None else _near(word, field_words)
    if not starts.isdisjoint(field_words):
        return 3, False
    if len(word) >= 4 and word in folded:
        return 2, False
    if not typos.isdisjoint(field_words):
        return 1, True
    return 0, False


def _snippet(texts: list[str], word: str) -> str:
    for s in texts:
        parts = s.split()
        for i, part in enumerate(parts):
            if word in fold(part):
                start = max(0, i - SNIPPET_WORDS // 2)
                end = min(len(parts), start + SNIPPET_WORDS)
                out = " ".join(parts[start:end])
                return ("… " if start else "") + out + (" …" if end < len(parts) else "")
    return ""


def _prepare(entry: Mapping[str, Any], lang: str, on: list[str]) -> dict[str, Any]:
    """One card's search fields, built once per language and package setting: label, search names, texts in the
    request language, texts in the other language (an English word on the German page still finds its card)."""
    other_lang = "de" if lang == "en" else "en"
    view = card_view(entry, lang, on)
    other = localize(_labels(entry["raw"], on), other_lang)
    texts = list(_texts(view["card"]))
    other_texts = list(_texts(_body(entry["raw"], entry["kind"], other_lang, on)))
    fields = [(f"{view['label']} {label_of(other, '')}", 10.0), (" ".join(_aliases(entry["raw"])), 7.0),
              (" ".join(texts), 1.5), (" ".join(other_texts), 1.0)]
    return {"hit": {**summary(entry, lang, on), "category": entry["category"]}, "texts": texts,
            "fields": [(frozenset(words(text)), fold(text), weight) for text, weight in fields]}


def _prepared(library: Path, lang: str, on: list[str]) -> _Prepared:
    """The visible cards' search fields and the vocabulary of all their words, per language and package setting."""
    sig, entries = _load(library)
    key = (str(library), lang, frozenset(on))
    with _LOCK:
        hit = _SEARCH_CACHE.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    items = [_prepare(entry, lang, on) for entry in entries if visible(entry, on)]
    vocab = frozenset(w for item in items for fw, _, _ in item["fields"] for w in fw)
    out: _Prepared = (items, vocab, {})
    with _LOCK:
        _SEARCH_CACHE[key] = (sig, out)
    return out


def search(q: str, lang: str, on: Iterable[str], library: Path | None = None) -> dict[str, Any]:
    """Labels count most, then the search names, then the texts. Every query word is scored against each field
    (whole word, word start, inside a word, typo); a card needs at least one word found. When some cards hold every
    query word, only those come back ("Ice Cream" is the strain, not every card with "ice" somewhere); otherwise the
    cards that match more of the query come first."""
    q = str(q or "")[:MAX_QUERY]
    qwords = query_words(q)
    results: list[tuple[Any, ...]] = []
    if not qwords:
        return {"q": q, "results": []}
    query_set = set(qwords)
    items, vocab, known = _prepared(library or LIBRARY, lang, list(on))
    near = {}
    for word in qwords:
        if word not in known:
            if len(known) >= NEAR_CACHE:
                known.clear()
            known[word] = _near(word, vocab)
        near[word] = known[word]
    for item in items:
        score, matched, typo, text_word = 0.0, 0, False, ""
        for word in qwords:
            best, best_typo = 0.0, False
            for i, (fw, folded, weight) in enumerate(item["fields"]):
                points, was_typo = _match(word, fw, folded, near[word])
                if points and points * weight > best:
                    best, best_typo = points * weight, was_typo
                if i == 2 and points >= 2 and not text_word:
                    text_word = word  # the snippet shows where the word stands in the card's own text
            if best:
                matched += 1
                score += best
                typo = typo or best_typo
        if not matched:
            continue
        hit = {**item["hit"], "typo": typo}
        if text_word:
            hit["snippet"] = _snippet(item["texts"], text_word)
        # A card whose name is exactly the query comes first, then the cards of a category the query names ("Düngen",
        # "Duengen", "Methoden"); on equal scores the index order decides, so the plant ("Sweet basil") stands before a
        # method about it ("Pinching herbs (basil)").
        exact = set(words(str(hit.get("label") or ""))) == query_set
        named = bool(set(words(str(hit.get("category") or ""))) & query_set)  # ids fold like words: duengen -> dungen
        if hit.get("category") == FERTILIZER_AREA:  # the area kept the old category's names ("Düngen", "Dünger")
            named = named or bool(query_set & _FERTILIZER_WORDS)
        rank = CATEGORY_ORDER.index(hit["category"]) if hit.get("category") in CATEGORY_ORDER else len(CATEGORY_ORDER)
        results.append((matched, exact, named, score, rank, hit))
    if any(r[0] == len(qwords) for r in results):  # AND first, OR only as the fallback
        results = [r for r in results if r[0] == len(qwords)]
    results.sort(key=lambda r: (-r[0], not r[1], not r[2], -r[3], r[4], fold(r[5]["label"])))
    return {"q": q, "results": [r[5] for r in results[:MAX_RESULTS]]}


# ---------- routes ----------

def register(app: web.Application, library: Path | None = None) -> None:
    """The wiki's six routes (GET /api/wiki/strains: the Sorten list, GET /api/wiki/fertilizers: the Dünger list), behind the cockpit's key cookie like every route. File reads run off the loop, and a
    failure answers an error, never a 500 page."""
    from . import wiki_tools

    async def index(_: web.Request) -> web.Response:
        lang = current_lang()
        try:
            data = await asyncio.to_thread(index_view, lang, packages(), library)
        except Exception:
            _LOGGER.exception("Wiki index failed")
            return web.json_response({"error": t("wiki.unavailable")}, status=503)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def card(request: web.Request) -> web.Response:
        lang = current_lang()
        folder, key = request.match_info["folder"], request.match_info["key"]
        try:
            entry = await asyncio.to_thread(find, folder, key, library)
            if entry is None:
                return web.json_response({"error": t("wiki.not_found")}, status=404)
            on = packages()
            if not visible(entry, on):
                return web.json_response({"error": t("wiki.hidden"), "hidden": True}, status=404)
            data = card_view(entry, lang, on, _current_for(library))
            data = await asyncio.to_thread(wiki_tools.enrich, entry, data, lang, on)
        except Exception:
            _LOGGER.exception("Wiki card failed")
            return web.json_response({"error": t("wiki.unavailable")}, status=503)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    searching = asyncio.Semaphore(1)  # one search at a time in the shared thread pool; a burst of keys queues here

    async def find_cards(request: web.Request) -> web.Response:
        lang = current_lang()
        try:
            async with searching:
                data = await asyncio.to_thread(search, request.query.get("q", ""), lang, packages(), library)
        except Exception:
            _LOGGER.exception("Wiki search failed")
            return web.json_response({"error": t("wiki.unavailable")}, status=503)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def switch(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            name, on = body.get("package"), body.get("on")
            if name not in KNOWN_PACKAGES or not isinstance(on, bool):
                raise ValueError("package/on")
        except (ValueError, AttributeError, TypeError) as err:
            return web.json_response({"error": t("error.bad_request", reason=str(err))}, status=400)
        now = await asyncio.to_thread(set_package, name, on)
        return web.json_response({"packages": package_view(now)})

    async def fertilizer_list(_: web.Request) -> web.Response:
        lang = current_lang()
        try:
            data = await asyncio.to_thread(fertilizers_view, lang, packages(), library)
        except Exception:
            _LOGGER.exception("Wiki fertilizer list failed")
            return web.json_response({"error": t("wiki.unavailable")}, status=503)
        if data is None:
            return web.json_response({"error": t("wiki.not_found")}, status=404)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def strain_list(_: web.Request) -> web.Response:
        lang = current_lang()
        try:
            data = await asyncio.to_thread(strains_view, lang, packages(), library)
        except Exception:
            _LOGGER.exception("Wiki strain list failed")
            return web.json_response({"error": t("wiki.unavailable")}, status=503)
        if data is None:
            return web.json_response({"error": t("wiki.hidden"), "hidden": True}, status=404)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def variety_list(_: web.Request) -> web.Response:
        try:
            data = await asyncio.to_thread(varieties_view, current_lang(), packages(), library)
            if data is not None:
                data = await asyncio.to_thread(wiki_tools.enrich_rows, data, library)
        except Exception:
            _LOGGER.exception("Wiki reference catalogue failed")
            return web.json_response({"error": t("wiki.unavailable")}, status=503)
        if data is None:
            return web.json_response({"error": t("wiki.hidden"), "hidden": True}, status=404)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    app.router.add_get("/api/wiki", index)
    app.router.add_get("/api/wiki/strains", strain_list)
    app.router.add_get("/api/wiki/varieties", variety_list)
    app.router.add_get("/api/wiki/fertilizers", fertilizer_list)
    app.router.add_get("/api/wiki/search", find_cards)
    app.router.add_get("/api/wiki/card/{folder}/{key}", card)
    app.router.add_post("/api/wiki/packages", switch)
    wiki_tools.register(app, library)
