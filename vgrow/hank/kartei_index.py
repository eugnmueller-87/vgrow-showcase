"""The card index and its search (spec 2.1 to 2.7, addendum N2).

Adapters turn every card of kartei/ and the run file plant.json into passages with stable ids `kind/card#path`
(spec 2.1). Three channels score a question against them: BM25 coverage over stemmed words with bilingual name
expansion, 3-gram containment for typos and compound words, and the cosine of an embedding (spec 2.2). A weighted sum
plus slot boosts gives the confidence the router decides on (spec 2.4). Rank fusion (RRF) is computed alongside for
the calibration run only.

Embeddings: vectors come from the embedder (vgrow/hank/ollama.py, the Embedder of spec appendix A) in batches, are
cached in STATE_DIR/hank/embeddings-<model>.json keyed by sha256 of the passage text, and only changed passages are
embedded again (spec 2.7). The question's own vector is asked for separately (embed_query), with the 0.8 s limit of
addendum N2, so the caller can run its safety gates first and never wait on the embedder for them. Missing model,
Ollama down, a running model job or incomplete vectors: the search runs lexically with the second threshold set.

Pure Python, no new dependency (spec 0.3 rule 5). Paths under STATE_DIR are resolved at call time (spec 10.1 rule 3).
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import json
import logging
import math
import re
import time
from array import array
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vgrow import config
from vgrow import feeding as feeding_mod
from vgrow.hank import slots as slots_mod
from vgrow.hank import text, typo
from vgrow.hank.slots import Names, Slots
from vgrow.hank.types import Hit, Passage, Retrieval, SourceRef

_LOGGER = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
THRESHOLDS_FILE = DATA_DIR / "thresholds.json"
POLICY_FILE = DATA_DIR / "chat_policy.json"
LIBRARY = config.REPO / "kartei"
RUN_FILE = config.REPO / "plant.json"

DEFAULT_EMBED_MODEL = "granite-embedding:278m"  # spec 2.3, decision D1
QUERY_TIMEOUT = 0.8  # addendum N2: the question's embedding (the spec said 1.5 s)
DOC_TIMEOUT = 120.0  # one batch of passages; the spec gives no limit for the build, a cold load takes 21 to 44 s
DOC_BATCH = 16
WARM_TIMEOUT = 240.0  # loading a model: the spec gives no own limit, so the chat limit of spec 4.7
K1, B = 1.2, 0.75  # BM25, spec 2.2
DEFAULT_LIMIT = 10

DYNAMIC_KINDS = ("live", "tool")  # addendum N2: refreshed by the caller (live every minute), never from kartei/
OWN_KIND = "experience"  # spec 8.4: own experience, never mixed with the sourced values
# Not searched unless the caller asks for them by kind: own experience is kept apart (spec 8.4), live and tool passages
# are the caller's (addendum N2).
# v1.4: method cards (kartei/methods/) are searched only when asked for by kind (the router's method rule, a question
# that names a method): in the default search their 114 passages took the scope decision from the climate and feeding
# questions (measured on the eval set: 14 cases failed). They cover only their own technique (meta "covers"), not
# every name they mention (the tomato card would otherwise "cover" the light question about a tomato, nodata-01).
OPT_IN_KINDS = ("methods",)
NOT_BY_DEFAULT = frozenset((*DYNAMIC_KINDS, OWN_KIND, *OPT_IN_KINDS))
_CARD_NAME_KINDS = frozenset({"plant", "product"})  # what in a card name counts as the card's own name (spec 2.4)
_SOURCE_ID_RE = re.compile(r"(?<![\w])([A-Z]{1,2}\d{1,3})(?![\w])")
# The shelves (owner, 01.11.2026, vgrow/hank/shelves.py): the folders that never enter the main index and get one of
# their own (Index(shelves=True)) together with the whole fertilizer market. Before, they had no adapter at all: 27
# problem cards, 22 plant-health entries, 4 crops and 4 processes Hank could not find.
SHELF_FOLDERS = ("problems", "health", "crops", "processes")
_SHELF_ID_RE = re.compile(r"(?<![a-z])([A-Z]{1,2}\d{1,3})(?!\d)")  # "P1P5" are two ids
_URL_SPLIT = ": http"
METHOD_FIELDS = ("what", "steps", "when", "autoflower", "box_fit", "risks")  # v1.4: the fields of a method card


def packages_on() -> frozenset[str]:
    """The packages switched on (STATE_DIR/settings.json "packages"), read exactly as the wiki reads them
    (vgrow/wiki.py packages(), its default when the key is missing). Read at call time; on a broken import or file
    the wiki's own default holds (every known package on), so a fault never hides cards."""
    try:
        from vgrow import wiki

        return frozenset(wiki.packages())
    except Exception as err:  # noqa: BLE001 (Hank is a guest: a broken settings read never stops the search)
        _LOGGER.warning("Hank index: the package switch is unreadable (%s), every package counts as on", err)
        return frozenset(("cannabis",))


def passage_visible(passage: Passage, on: Iterable[str]) -> bool:
    """A passage of a card with a package (meta "package") counts only while that package is on."""
    package = passage.meta.get("package") if isinstance(passage.meta, Mapping) else None
    return not package or package in set(on)


# ---------- small helpers ----------

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as err:
        _LOGGER.warning("Hank index: %s not readable (%s), skipped", path, err)
        return None


def slug(s: str) -> str:
    """The id form of a key (spec 2.1): folded, lower case, every run of non-letters as one "_"."""
    return re.sub(r"[^a-z0-9]+", "_", text.fold(str(s))).strip("_")


def fmt(value: float | int | None, lang: str) -> str:
    """A number as a card writes it: no ".0", a decimal comma in German."""
    if value is None:
        return ""
    number = float(value)
    out = str(int(number)) if number.is_integer() else f"{number:g}"
    return out.replace(".", ",") if lang == "de" else out


def range_text(lo: Any, hi: Any, unit: str, lang: str) -> str:
    """ "45 bis 60 %", "höchstens 85 %", "mindestens 12,8 °C", "100 %"; English "to", "at most", "at least"."""
    unit = f" {unit}" if unit else ""
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        if float(lo) == float(hi):
            return f"{fmt(lo, lang)}{unit}"
        joint = " bis " if lang == "de" else " to "
        return f"{fmt(lo, lang)}{joint}{fmt(hi, lang)}{unit}"
    if hi is not None:
        return f"{'höchstens' if lang == 'de' else 'at most'} {fmt(hi, lang)}{unit}"
    return f"{'mindestens' if lang == 'de' else 'at least'} {fmt(lo, lang)}{unit}"


def parse_source(sid: str, value: Any, lang: str = "de") -> SourceRef:
    """Both source forms of the cards (spec 2.1): the text "Title: URL" (split at the last ": http") and the object
    {title, title_en, url, retrieved}. English takes title_en when there is one."""
    if isinstance(value, Mapping):
        title = value.get("title_en") if lang == "en" and value.get("title_en") else value.get("title")
        url = value.get("url")
        return SourceRef(sid, str(title or sid), str(url) if url else None)
    raw = str(value or "")
    cut = raw.rfind(_URL_SPLIT)
    if cut >= 0:
        return SourceRef(sid, raw[:cut].strip(), raw[cut + 2:].strip())
    return SourceRef(sid, raw.strip() or sid, None)


def _unique(items: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _normalize(vec: Iterable[float]) -> list[float]:
    values = [float(v) for v in vec]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm else values


def _pack(vec: list[float]) -> str:
    return base64.b64encode(array("f", vec).tobytes()).decode("ascii")


def _unpack(data: str) -> list[float]:
    values = array("f")
    values.frombytes(base64.b64decode(data))
    return list(values)


# Unit words in a question ("wie viel Grad", "100 Prozent", "6 Liter") are matched as the unit itself, so "Prozent"
# finds "%" the way the cards write it, instead of the one passage that spells the word out. Values are the canonical
# units of text.parse_numbers (text.UNIT_NAMES).
_UNIT_WORDS: dict[str, str] = {text.fold(word): unit for unit, words in {
    "%": "prozent percent", "°C": "grad celsius degrees", "°F": "fahrenheit", "l": "liter litre liters litres litern",
    "ml": "ml milliliter millilitre", "cm": "cm zentimeter centimeter", "mm": "mm", "W": "watt", "kPa": "kpa",
    "g": "gramm gram grams",  # feeding v2: the VIVOSUN card doses in g/l ("Wie viel Gramm Base A?")
    "h": "stunden stunde hours hour", "min": "minuten minute minutes", "d": "tage tagen days",
    "wk": "wochen weeks", "step": "stufe stufen steps", "pt": "punkte punkten points", "ppm": "ppm", "lx": "lux",
}.items() for word in words.split()}


def unit_terms(s: str, lang: str) -> list[str]:
    """The canonical units of the numbers in a text as search terms ("u:%", "u:°C", "u:ml/l")."""
    return [f"u:{num.unit}" for num in text.parse_numbers(s, lang, words=False) if num.unit]


def _chart_covers(passage: Passage, names: Names) -> tuple[str, ...]:
    """What a nutrient card passage covers (covered()): its names, but a technique (a directory "process") only when
    both languages name it. A chart is about feeding; the label's "Seedling/Clone" column or "salts in the top of the
    substrate" make no card about cuttings or topping (the merge of 23.10.2026 found the VIVOSUN card covering both,
    so "Wie mache ich Stecklinge?" would have lost its "Dazu steht noch nichts in meiner Kartei")."""
    kinds = {h.entity: h.kind for lang in ("de", "en") for h in names.find(f"{passage.title[lang]}\n{passage.text[lang]}")}
    in_de = {h.entity for h in names.find(f"{passage.title['de']}\n{passage.text['de']}")}
    in_en = {h.entity for h in names.find(f"{passage.title['en']}\n{passage.text['en']}")}
    return tuple(e for e in passage.entities
                 if kinds.get(e) != "process" or (e in in_de and e in in_en) or e in (passage.meta.get("card_names") or ()))


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _safe_name(model: str) -> str:
    """A model name as a file name part ("granite-embedding:278m" has a colon, which Windows forbids)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or THRESHOLDS_FILE)
    if not isinstance(data, dict):
        raise RuntimeError(f"Hank thresholds missing or broken: {path or THRESHOLDS_FILE}")
    return data


def load_policy(path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or POLICY_FILE)
    return data if isinstance(data, dict) else {}


def embed_model_name(model: str | None = None, embedder: Any = None) -> str:
    """The embedding model: an explicit name, else the embedder's own, else HANK_EMBED_MODEL in ~/.vgrow.env, else
    granite-embedding:278m (spec 2.3). Read at call time."""
    if model:
        return model
    own = getattr(embedder, "embed_model", None)
    if isinstance(own, str) and own:
        return own
    return (config.read_env(config.ENV_FILE).get("HANK_EMBED_MODEL") or "").strip() or DEFAULT_EMBED_MODEL


def _error_kind(err: BaseException) -> str:
    """A question embedding that did not come back, as a dense state: a running model job skips it ("off"), a
    timeout is "timeout", everything else (no Ollama, no model, a failure) is "missing"."""
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if type(err).__name__ == "LaneBusy":
        return "off"
    if getattr(err, "kind", None) == "timeout":
        return "timeout"
    return "missing"


# ---------- the built index ----------

@dataclass
class _Doc:
    passage: Passage
    tf: dict[str, float]
    length: float
    grams: frozenset[str]
    embed_text: str
    sha: str


@dataclass
class _State:
    passages: list[Passage] = field(default_factory=list)
    docs: list[_Doc] = field(default_factory=list)
    by_id: dict[str, int] = field(default_factory=dict)
    postings: dict[str, dict[int, float]] = field(default_factory=dict)
    gram_postings: dict[str, set[int]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)  # "kind/card" -> raw source map
    covered: set[str] = field(default_factory=set)
    covered_pkg: dict[str, set[str]] = field(default_factory=dict)  # entity -> packages of the only cards naming it
    vocab: typo.Vocab | None = None
    names: Names | None = None
    run_page: str | None = None
    fingerprint: dict[str, tuple[int, int]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # cards without an adapter
    card_docs: list[_Doc] = field(default_factory=list)  # docs from files; docs = card_docs + dynamic ones

    def index_docs(self, docs: list[_Doc]) -> None:
        """Fill docs, by_id and the postings from scratch (in memory, a few milliseconds for 100 passages)."""
        self.docs, self.passages, self.by_id = list(docs), [d.passage for d in docs], {}
        self.postings, self.gram_postings = {}, {}
        for number, doc in enumerate(self.docs):
            self.by_id[doc.passage.id] = number
            for term, count in doc.tf.items():
                self.postings.setdefault(term, {})[number] = count
            for gram in doc.grams:
                self.gram_postings.setdefault(gram, set()).add(number)


@dataclass
class _QWord:
    terms: frozenset[str]  # stems of the word itself (German and English rules)
    alts: frozenset[str]  # stems of its synonyms and compound parts (weight expansion_weight)


class Index:
    """The searchable card index. Build it with Index.load(); search() is synchronous and pure code; the embedding
    calls (warm, build_dense, embed_query) are coroutines and never raise."""

    def __init__(self, library: Path, run_file: Path | None, embedder: Any, cache_dir: Path | None, *,
                 model: str | None = None, vocab_file: Path | None = None, policy_file: Path | None = None,
                 thresholds_file: Path | None = None, ports: Mapping[str, Any] | None = None,
                 experience_file: Path | None = None, shelves: bool = False) -> None:
        self.library = Path(library)
        self.shelves = shelves  # the shelves' own index (SHELF_FOLDERS and the fertilizer market), never the main one
        self.fallback: Index | None = None  # the shelves' index: sources of a shelf passage the main index answers with
        self.run_file = Path(run_file) if run_file is not None else None
        self.embedder = embedder
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.model = embed_model_name(model, embedder) if embedder is not None else (model or None)
        self.vocab_file = Path(vocab_file) if vocab_file is not None else self.library / "_hank" / "vocab.json"
        self.policy = load_policy(policy_file)
        self.thresholds = load_thresholds(thresholds_file)
        self._ports = ports
        self._experience_file = Path(experience_file) if experience_file is not None else None
        self._vectors: dict[str, list[float]] = {}  # sha256 of the embed text -> unit vector
        self._dense_error: str | None = None
        self._dense_building = False
        self.dense_timeouts = 0
        self.last_query: dict[str, Any] | None = None  # the dense state of the last question's embedding (WP11)
        self._state = _State()
        self._dynamic: dict[str, list[Passage]] = {}

    # ----- building -----

    @classmethod
    def load(cls, library: Path | str | None = None, run_file: Path | str | None = None, embedder: Any = None,
             cache_dir: Path | str | None = None, **options: Any) -> Index:
        """Build the index from kartei/ and plant.json now (lexical part, a few hundred milliseconds) and read the
        vector cache. The vectors that are missing come with build_dense(), in the background."""
        index = cls(Path(library) if library is not None else LIBRARY,
                    Path(run_file) if run_file is not None else RUN_FILE, embedder,
                    Path(cache_dir) if cache_dir is not None else None, **options)
        index.reload()
        index._read_cache()
        return index

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir if self._cache_dir is not None else config.STATE_DIR / "hank"

    @property
    def experience_file(self) -> Path:
        return self._experience_file if self._experience_file is not None else \
            config.STATE_DIR / "hank" / "experience.jsonl"

    @property
    def cache_file(self) -> Path:
        shelf = "shelves-" if self.shelves else ""  # two indexes never write one cache file
        return self.cache_dir / f"embeddings-{shelf}{_safe_name(self.model or DEFAULT_EMBED_MODEL)}.json"

    def _watched(self) -> list[Path]:
        files = sorted(self.library.rglob("*.json")) if self.library.exists() else []
        extra = [self.run_file, self.experience_file]
        if self._ports is None:
            extra.append(config.PORTS_FILE)
        return files + [p for p in extra if p is not None]

    def _fingerprint(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for path in self._watched():
            try:
                stat = path.stat()
            except OSError:
                continue
            out[str(path)] = (stat.st_size, stat.st_mtime_ns)
        return out

    def stale(self) -> bool:
        """True when a file under kartei/, plant.json, ports.json or the experience file changed size or mtime, or
        appeared or went away (spec 2.7: the ticker checks this, it only stats files)."""
        return self._fingerprint() != self._state.fingerprint

    def refresh(self) -> bool:
        """Rebuild if stale(); True if it did. Vectors of unchanged passages are kept (keyed by their text)."""
        if not self.stale():
            return False
        self.reload()
        return True

    def reload(self) -> None:
        """Build everything again from the files. The new state replaces the old one in one step."""
        fingerprint = self._fingerprint()
        entries = self._vocab_entries()
        names = Names(entries)
        builder = _Builder(self, names)
        builder.build()
        state = _State(sources=builder.sources, names=names, run_page=builder.run_page, fingerprint=fingerprint,
                       skipped=builder.skipped)
        words: list[str] = []
        for passage in builder.passages:
            if passage.kind in OPT_IN_KINDS:  # v1.4: a method card's words never make a typo elsewhere "known"
                continue
            words.extend([passage.title["de"], passage.title["en"], passage.text["de"], passage.text["en"]])
        state.vocab = typo.Vocab(entries, words=words)
        state.card_docs = [self._doc(passage, state.vocab.splits) for passage in builder.passages]
        for doc in state.card_docs:
            if doc.passage.kind in OPT_IN_KINDS:
                entities = tuple(doc.passage.meta.get("covers") or ())
            elif doc.passage.kind == "nutrients":
                entities = _chart_covers(doc.passage, names)
            elif doc.passage.kind not in NOT_BY_DEFAULT:
                entities = doc.passage.entities
            else:
                continue
            package = doc.passage.meta.get("package")
            if package:
                for entity in entities:
                    state.covered_pkg.setdefault(entity, set()).add(str(package))
            else:
                state.covered.update(entities)
        dynamic = [self._doc(p, state.vocab.splits) for kind in DYNAMIC_KINDS for p in self._dynamic.get(kind, [])]
        state.index_docs(state.card_docs + dynamic)
        self._state = state

    def _vocab_entries(self) -> list[dict[str, Any]]:
        data = _read_json(self.vocab_file)
        entries = [e for e in (data or {}).get("entries", []) if isinstance(e, Mapping)] if isinstance(data, dict) \
            else []
        if not entries:
            _LOGGER.warning("Hank index: no name directory at %s, searching without names", self.vocab_file)
        ports = self._ports if self._ports is not None else (config.port_rules() or {})
        for key, rule in ports.items():
            if not isinstance(rule, Mapping) or str(key).startswith("_"):
                continue
            label, label_en = rule.get("label"), rule.get("label_en") or rule.get("label")
            if isinstance(label, str) and label.strip():
                entries.append({"id": str(key), "kind": "device", "de": [label], "en": [str(label_en)],
                                "maps_to": str(key)})
        return [dict(e) for e in entries]

    def _doc(self, passage: Passage, splits: Mapping[str, list[str]]) -> _Doc:
        tf: dict[str, float] = {}

        def add(s: str, lang: str, weight: float) -> None:
            for word in text.tokens(s, stem=False, lang=lang):
                terms = [text.stem_word(word, lang), *(text.stem_word(p, lang) for p in splits.get(word, ()))]
                if word in _UNIT_WORDS:  # "in Prozent" also as the unit, as in the question (see _UNIT_WORDS)
                    terms.append(f"u:{_UNIT_WORDS[word]}")
                for term in terms:
                    tf[term] = tf.get(term, 0.0) + weight
            for term in unit_terms(s, lang):
                tf[term] = tf.get(term, 0.0) + weight

        add(passage.title["de"], "de", 2.0)  # spec 2.2: titles and names count double
        add(passage.text["de"], "de", 1.0)
        if passage.title["en"] != passage.title["de"]:
            add(passage.title["en"], "en", 2.0)
        if passage.text["en"] != passage.text["de"]:
            add(passage.text["en"], "en", 1.0)
        body = " ".join(_unique([passage.title["de"], passage.title["en"], passage.text["de"], passage.text["en"]]))
        embed_text = "\n".join(_unique([passage.title["de"], passage.text["de"], passage.text["en"]]))
        return _Doc(passage, tf, sum(tf.values()), frozenset(text.grams(body)), embed_text, _sha(embed_text))

    # ----- live and tool passages (addendum N2) -----

    def set_dynamic(self, kind: str, passages: Iterable[Passage]) -> None:
        """Replace the passages of a dynamic kind ("live": one per state group, every minute; "tool": how Hank and
        the page work). They are searched only when asked for by kinds=, and never count for covered(). Rebuilds
        the postings in memory; no file is read."""
        if kind not in DYNAMIC_KINDS:
            raise ValueError(f"unknown dynamic kind {kind!r}, known: {', '.join(DYNAMIC_KINDS)}")
        self._dynamic[kind] = [p for p in passages if p.kind == kind]
        state = self._state
        splits = state.vocab.splits if state.vocab else {}
        dynamic = [self._doc(p, splits) for name in DYNAMIC_KINDS for p in self._dynamic.get(name, [])]
        fresh = _State(sources=state.sources, covered=state.covered, covered_pkg=state.covered_pkg,
                       vocab=state.vocab, names=state.names,
                       run_page=state.run_page, fingerprint=state.fingerprint, skipped=state.skipped,
                       card_docs=state.card_docs)
        fresh.index_docs(state.card_docs + dynamic)
        self._state = fresh

    # ----- lookups -----

    @property
    def passages(self) -> list[Passage]:
        return list(self._state.passages)

    @property
    def vocab(self) -> typo.Vocab:
        return self._state.vocab or typo.Vocab()

    @property
    def names(self) -> Names:
        return self._state.names or Names()

    @property
    def skipped_cards(self) -> list[str]:
        """Cards under kartei/ that no adapter reads (their schema has no adapter yet)."""
        return list(self._state.skipped)

    def passage(self, pid: str) -> Passage | None:
        number = self._state.by_id.get(pid)
        return None if number is None else self._state.passages[number]

    def covered(self, entity: str) -> bool:
        """A directory entry is covered when at least one indexed card passage names it (spec 2.5). Own experience,
        live and tool passages do not count, nor does a card of a package that is switched off (v1.4)."""
        if entity in self._state.covered:
            return True
        packages = self._state.covered_pkg.get(entity)
        return bool(packages) and bool(packages & packages_on())

    def by_meta(self, **kw: Any) -> list[Passage]:
        """Passages whose meta (or kind, card, status) equal all given values."""
        out = []
        for passage in self._state.passages:
            ok = True
            for key, value in kw.items():
                have = getattr(passage, key) if key in ("kind", "card", "status", "id") else passage.meta.get(key)
                if have != value:
                    ok = False
                    break
            if ok:
                out.append(passage)
        return out

    def twin_missing(self) -> list[str]:
        """Ids of passages without an English twin (spec 2.1: one gap entry each, written by the caller)."""
        return [p.id for p in self._state.passages if p.twin_missing]

    def sources(self, passage: Passage, lang: str = "de") -> list[SourceRef]:
        """The passage's sources resolved on its own card (S ids collide between cards). Seed-shop hosts lose their
        link (chat_policy.json blocked_source_hosts, decision D3)."""
        raw = self._table(f"{passage.kind}/{passage.card}")
        out = []
        for sid in passage.sources:
            if sid == "own":
                out.append(SourceRef("own", "own", None))
                continue
            if sid not in raw:
                continue
            ref = parse_source(sid, raw[sid], lang)
            if ref.url and self._blocked_host(ref.url):
                ref = SourceRef(ref.id, ref.title, None)
            out.append(ref)
        return out

    def refs(self, table: Mapping[str, Any] | str | None, ids: Iterable[str], lang: str = "de") -> list[SourceRef]:
        """Source ids resolved on one card's own source table (a table, or "kind/card" of an indexed card), in the
        given order, unknown ids left out; seed-shop hosts lose their link as in sources() (decision D3). For the
        code answers of v1.4 (methods.py, wikicards.py), which cite a card's sources without a passage."""
        raw = self._table(table) if isinstance(table, str) else (table or {})
        out = []
        for sid in _unique(ids):
            if sid not in raw:
                continue
            ref = parse_source(sid, raw[sid], lang)
            if ref.url and self._blocked_host(ref.url):
                ref = SourceRef(ref.id, ref.title, None)
            out.append(ref)
        return out

    def _table(self, key: str) -> Mapping[str, Any]:
        """One card's source table ("kind/card"), from the shelves' index when this one does not hold the card."""
        found = self._state.sources.get(key)
        if found is None and self.fallback is not None:
            found = self.fallback._state.sources.get(key)
        return found or {}

    def _blocked_host(self, url: str) -> bool:
        hosts = (self.policy.get("blocked_source_hosts") or {}).get("hosts") or []
        found = re.match(r"^[a-z]+://([^/:?#]+)", url.strip().lower())
        host = found.group(1) if found else ""
        return any(host == h or host.endswith("." + h) for h in hosts)

    # ----- dense state -----

    def dense_state(self) -> str:
        """off (no embedder), used (every card passage has its vector), partial (the build is running or not done),
        missing (the model or Ollama did not answer the build)."""
        if self.embedder is None:
            return "off"
        if all(d.sha in self._vectors for d in self._state.card_docs if d.passage.kind not in NOT_BY_DEFAULT):
            return "used"
        if self._dense_error == "missing" and not self._dense_building:
            return "missing"
        return "partial"

    def state(self) -> dict[str, Any]:
        """For GET /api/hank/state (spec 2.3): passage count, dense state, embedding model and, when the model is
        missing, the command that installs it."""
        dense = self.dense_state()
        model = self.model or DEFAULT_EMBED_MODEL
        return {"passages": sum(1 for p in self._state.passages if p.kind not in DYNAMIC_KINDS), "dense": dense,
                "model": model if self.embedder is not None else None,
                "pull": f"ollama pull {model}" if dense == "missing" else None,
                "timeouts": self.dense_timeouts,
                "last_query": dict(self.last_query) if self.last_query is not None else None}

    # ----- embeddings (coroutines, never raise) -----

    def _read_cache(self) -> None:
        if self.embedder is None:
            return
        data = _read_json(self.cache_file)
        if not isinstance(data, dict) or data.get("model") != self.model:
            return
        for sha, packed in (data.get("vectors") or {}).items():
            try:
                self._vectors[sha] = _unpack(packed)
            except (ValueError, TypeError):
                continue

    def _write_cache(self) -> None:
        keep = {d.sha for d in self._state.card_docs if d.sha in self._vectors}
        payload = {"version": 1, "model": self.model,
                   "vectors": {sha: _pack(self._vectors[sha]) for sha in sorted(keep)}}
        try:
            config.atomic_write(self.cache_file, json.dumps(payload) + "\n")
        except OSError as err:
            _LOGGER.warning("Hank index: vector cache not written (%s)", err)

    async def warm(self, *, timeout: float = WARM_TIMEOUT) -> bool:
        """Load the embedding model once at start and keep it loaded (addendum N2, keep_alive -1). The client's own
        warm_embedder() is used when it has one. Never raises."""
        if self.embedder is None:
            return False
        try:
            own = getattr(self.embedder, "warm_embedder", None)
            if callable(own):
                return bool(await own(timeout=timeout))
            await self._embed(["warm"], kind="query", timeout=timeout)
            return True
        except Exception as err:  # noqa: BLE001 (Hank is a guest: a failed warm-up is only logged)
            _LOGGER.info("Hank index: warming the embedder failed: %s", err)
            return False

    async def _embed(self, texts: list[str], *, kind: str, timeout: float) -> list[list[float]]:
        call = self.embedder.embed(texts, kind=kind, timeout=timeout)
        return await asyncio.wait_for(call, timeout)

    async def build_dense(self, *, batch: int = DOC_BATCH, timeout: float = DOC_TIMEOUT) -> str:
        """Embed every card passage that has no vector yet, in batches, and write the cache. Returns the dense state
        afterwards. Never raises: a failure leaves the index lexical."""
        if self.embedder is None:
            return "off"
        if self._dense_building:
            return "partial"
        self._dense_building = True
        try:
            await self._build_dense(batch, timeout)
        finally:
            self._dense_building = False
        return self.dense_state()

    async def _build_dense(self, batch: int, timeout: float) -> None:
        todo = _unique((d.sha, d.embed_text) for d in self._state.card_docs
                       if d.passage.kind not in NOT_BY_DEFAULT and d.sha not in self._vectors)
        for start in range(0, len(todo), max(1, batch)):
            chunk = todo[start:start + max(1, batch)]
            try:
                vectors = await self._embed([t for _, t in chunk], kind="doc", timeout=timeout)
            except Exception as err:  # noqa: BLE001 (Hank is a guest: the index stays lexical)
                self._dense_error = "missing" if _error_kind(err) == "missing" else "timeout"
                _LOGGER.warning("Hank index: embedding the passages failed (%s): %s", self._dense_error, err)
                return
            if len(vectors) != len(chunk):
                self._dense_error = "missing"
                _LOGGER.warning("Hank index: %d vectors for %d passages", len(vectors), len(chunk))
                return
            for (sha, _), vec in zip(chunk, vectors):
                self._vectors[sha] = _normalize(vec)
        self._dense_error = None
        if todo:
            self._write_cache()

    async def start(self) -> str:
        """At server start, in the background: warm the embedder, then embed what is missing."""
        if self.embedder is None:
            return "off"
        await self.warm()
        return await self.build_dense()

    async def embed_query(self, query: str, *, timeout: float = QUERY_TIMEOUT,
                          skip: bool = False) -> tuple[list[float] | None, str]:
        """The question's vector and the dense state (addendum N2: 0.8 s, called after the pattern gates). skip=True
        while a photo reading or a chat answer runs (spec 2.3: they share the CPU). Never raises."""
        vec, state = await self._embed_query(query, timeout=timeout, skip=skip)
        if self.embedder is not None:
            self.last_query = {"state": state, "at": time.time()}
        return vec, state

    async def _embed_query(self, query: str, *, timeout: float, skip: bool) -> tuple[list[float] | None, str]:
        if self.embedder is None:
            return None, "off"
        ready = self.dense_state()
        if ready != "used":
            return None, ready
        if skip:
            return None, "off"
        try:
            vectors = await self._embed([query], kind="query", timeout=timeout)
        except Exception as err:  # noqa: BLE001
            kind = _error_kind(err)
            if kind == "timeout":
                self.dense_timeouts += 1
            return None, kind
        if not vectors:
            return None, "missing"
        return _normalize(vectors[0]), "used"

    async def asearch(self, query: str, *, timeout: float = QUERY_TIMEOUT, skip: bool = False,
                      **kw: Any) -> Retrieval:
        """embed_query() then search(): the whole search with the dense channel when it answers in time."""
        vec, state = await self.embed_query(query, timeout=timeout, skip=skip)
        return self.search(query, vec, dense_state=state, **kw)

    def max_cosine(self, qvec: list[float] | None, kinds: Iterable[str] | None = None) -> float | None:
        """The highest raw cosine between a question's vector and the card passages (default: every card kind,
        without own experience, live and tool), or None without vectors (the nearest neighbour row, WP11)."""
        if not qvec or self.dense_state() != "used":
            return None
        q = _normalize(qvec)
        top: float | None = None
        for i in self._pool(kinds):
            vec = self._vectors.get(self._state.docs[i].sha)
            if vec and len(vec) == len(q):
                cos = sum(a * b for a, b in zip(q, vec))
                top = cos if top is None or cos > top else top
        return top

    # ----- search -----

    def threshold_set(self, retrieval: Retrieval | None = None, *, dense: str | None = None) -> dict[str, float]:
        """The thresholds that apply: the dense set when the embedding channel was used, else the lexical one."""
        used = (retrieval.dense if retrieval is not None else dense) == "used"
        return dict(self.thresholds["dense" if used else "lexical"])

    def _pool(self, kinds: Iterable[str] | None) -> list[int]:
        """The docs a search looks at: the kinds asked for (default: every card kind, without own experience, live and
        tool), never a card of a package that is switched off (v1.4: the cannabis switch of the wiki)."""
        state = self._state
        on = packages_on() if any(d.passage.meta.get("package") for d in state.docs) else frozenset()
        if kinds is None:
            return [i for i, d in enumerate(state.docs) if d.passage.kind not in NOT_BY_DEFAULT
                    and passage_visible(d.passage, on)]
        wanted = set(kinds)
        return [i for i, d in enumerate(state.docs) if d.passage.kind in wanted and passage_visible(d.passage, on)]

    def _aliases(self, aliases: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        if aliases is not None:
            return aliases
        data = _read_json(config.STATE_DIR / "hank" / "aliases-learned.json")
        return data if isinstance(data, Mapping) else None

    def _query_words(self, corrected: str, hits: list[slots_mod.NameHit], lang: str) -> list[_QWord]:
        vocab = self.vocab
        de = text.tokens(corrected, lang="de")
        en = text.tokens(corrected, lang="en")
        raw = text.tokens(corrected, stem=False)
        synonyms: dict[int, set[str]] = {}
        for hit in hits:  # spec 2.2: a name adds all its synonyms in both languages
            alts: set[str] = set()
            for surface in vocab.names(hit.entity):
                # One-word names only, a hyphen name as its joined form: the parts of "Autoflower-Pflanze" or
                # "relative humidity" ("Pflanze", "relative") are not synonyms of the name.
                if len(surface.split()) > 1:
                    continue
                for rules in ("de", "en"):
                    found = text.tokens(surface, lang=rules)
                    if found:
                        alts.add(found[0])
            for pos in range(hit.start, hit.end):
                synonyms.setdefault(pos, set()).update(alts)
        units = unit_terms(corrected, lang)
        words: list[_QWord] = []
        seen: set[frozenset[str]] = set()
        for pos, (d, e, w) in enumerate(zip(de, en, raw)):
            if w in _UNIT_WORDS:
                units.append(f"u:{_UNIT_WORDS[w]}")  # a unit word: matched as its canonical term below
                continue
            terms = frozenset({d, e})
            if terms in seen:
                continue
            seen.add(terms)
            alts = set(synonyms.get(pos, set()))
            for part in vocab.splits.get(w, ()):  # spec 2.6: compound parts as extra words
                alts.update({text.stem_word(part, "de"), text.stem_word(part, "en")})
            words.append(_QWord(terms, frozenset(alts - terms)))
        for unit in _unique(units):
            words.append(_QWord(frozenset({unit}), frozenset()))
        return words

    def _idf(self, n_docs: int, df: int) -> float:
        return math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)

    def search(self, query: str, dense: list[float] | None = None, *, lang: str | None = None,
               stage: Any = None, feed: Mapping[str, Any] | None = None, aliases: Mapping[str, Any] | None = None,
               kinds: Iterable[str] | None = None, limit: int = DEFAULT_LIMIT, dense_state: str | None = None,
               fusion: str | None = None) -> Retrieval:
        """Search the cards for a question.

        dense: the question's unit vector from embed_query(), or None (then dense_state says why: off, timeout,
        missing; without it the index's own state). lang: the question's language (detected when None). stage: the
        current stage (its key or plant.current_stage()), for the live boost. feed: feeding.view() of the run, or
        {"page", "week"}, for "diese Woche". aliases: learned spellings (default: STATE_DIR/hank/aliases-learned.json).
        kinds: the passage kinds to search (default: all card kinds, without own experience, live and tool).
        fusion: "sum" (default, spec 2.4) or "rrf" (rank fusion, for the calibration's comparison only)."""
        t0 = time.perf_counter()
        state = self._state
        query = (query or "").strip()
        lang = lang if lang in ("de", "en") else text.detect_lang(query, "de")
        if not query or not state.docs:
            return Retrieval([], 0.0, Slots(), dense=dense_state or self.dense_state(), ms={"total": 0})
        match = typo.scan(query, self.vocab, self._aliases(aliases))
        corrected = match.corrected or query
        t_vocab = time.perf_counter()
        name_hits = self.names.find(corrected)
        slot = slots_mod.read(corrected, lang, hits=name_hits)
        t_slots = time.perf_counter()

        pool = self._pool(kinds)
        n_docs = len(pool)
        pool_set = set(pool)
        words = self._query_words(corrected, name_hits, lang)
        exp = float(self.thresholds.get("expansion_weight", 0.7))
        avgdl = (sum(state.docs[i].length for i in pool) / n_docs) if n_docs else 1.0
        bm25_cov = {i: 0.0 for i in pool}
        bm25_raw = {i: 0.0 for i in pool}
        total_idf = 0.0
        for word in words:
            direct: dict[int, float] = {}
            for term in word.terms:
                for i, count in state.postings.get(term, {}).items():
                    if i in pool_set:
                        direct[i] = max(direct.get(i, 0.0), count)
            alt: dict[int, float] = {}
            for term in word.alts:
                for i, count in state.postings.get(term, {}).items():
                    if i in pool_set and i not in direct:
                        alt[i] = max(alt.get(i, 0.0), count)
            idf = self._idf(n_docs, len(direct) or len(alt))
            total_idf += idf
            for found, weight in ((direct, 1.0), (alt, exp)):
                for i, count in found.items():
                    bm25_cov[i] += weight * idf
                    length = state.docs[i].length
                    bm25_raw[i] += weight * idf * count * (K1 + 1) / (count + K1 * (1 - B + B * length / avgdl))
        if total_idf > 0:
            bm25_cov = {i: min(1.0, v / total_idf) for i, v in bm25_cov.items()}

        q_grams = set(text.grams(corrected))
        ngram = {i: 0.0 for i in pool}
        gram_total = 0.0
        for gram in q_grams:
            holders = state.gram_postings.get(gram, set()) & pool_set
            idf = self._idf(n_docs, len(holders))
            gram_total += idf
            for i in holders:
                ngram[i] += idf
        if gram_total > 0:
            ngram = {i: min(1.0, v / gram_total) for i, v in ngram.items()}
        t_lex = time.perf_counter()

        used_dense = dense is not None and self.dense_state() == "used"
        dense_out = "used" if used_dense else (dense_state if dense_state and dense_state != "used"
                                               else self.dense_state())
        if dense_out == "used" and not used_dense:
            dense_out = "off"
        dense_cal = {i: 0.0 for i in pool}
        if used_dense:
            qvec = _normalize(dense or [])
            basis_map = self.thresholds.get("dense_basis") or {}
            basis = float(basis_map.get(self.model or "", basis_map.get("default", 0.25)))
            for i in pool:
                vec = self._vectors.get(state.docs[i].sha)
                if not vec or len(vec) != len(qvec):
                    continue
                cos = sum(a * b for a, b in zip(qvec, vec))
                dense_cal[i] = max(0.0, min(1.0, (cos - basis) / (1 - basis)))
        t_dense = time.perf_counter()

        weights = self.thresholds["weights"]["dense" if used_dense else "lexical"]
        current = stage.get("key") if isinstance(stage, Mapping) else stage
        feed_page, feed_week = _feed_position(feed)
        scored: list[Hit] = []
        for i in pool:
            base = weights["bm25"] * bm25_cov[i] + weights["ngram"] * ngram[i]
            if used_dense:
                base += weights["dense"] * dense_cal[i]
            if base <= 0:
                continue  # boosts lift hits, they never make one
            passage = state.docs[i].passage
            boost, why = self._boost(passage, slot, current, feed_page, feed_week, state.run_page)
            channels = {"bm25": round(bm25_cov[i], 6), "bm25_raw": round(bm25_raw[i], 6),
                        "ngram": round(ngram[i], 6), "dense": round(dense_cal[i], 6), "boost": round(boost, 6)}
            channels.update({f"boost_{k}": v for k, v in why.items()})
            scored.append(Hit(passage, base + boost, channels))

        mode = fusion or str(self.thresholds.get("fusion", "sum"))
        if mode == "rrf":
            self._rrf(scored, used_dense)
            scored.sort(key=lambda h: (-h.channels["rrf"], -h.score, h.passage.id))
        else:
            scored.sort(key=lambda h: (-h.score, -h.channels["bm25_raw"], h.passage.id))
        hits = scored[:max(0, limit)]
        confidence = min(1.0, max((h.score for h in scored), default=0.0))
        named = _unique([*name_hits_entities(name_hits), *match.entities()])
        uncovered = [e for e in named if not self.covered(e)]
        t_end = time.perf_counter()
        ms = {"vocab": round((t_vocab - t0) * 1000), "slots": round((t_slots - t_vocab) * 1000),
              "lexical": round((t_lex - t_slots) * 1000), "dense": round((t_dense - t_lex) * 1000),
              "total": round((t_end - t0) * 1000)}
        return Retrieval(hits, confidence, slot, list(match.corrections), uncovered, dense_out, ms)

    def _boost(self, passage: Passage, slot: Slots, current: str | None, feed_page: str | None,
               feed_week: str | None, run_page: str | None) -> tuple[float, dict[str, float]]:
        """The slot boosts of spec 2.4, each named, for one passage."""
        values = self.thresholds["boosts"]
        why: dict[str, float] = {}
        meta = passage.meta
        own_stage = meta.get("stage")
        # The stage in context: the one the question names, else on a live question the current one, else none.
        context = list(slot.stages) or ([current] if slot.live and current else [])
        if own_stage:
            if slot.stages:
                if own_stage in slot.stages:
                    why["stage_named"] = values["stage_named"]
            elif slot.live and current and own_stage == current:
                why["stage_live"] = values["stage_live"]
        # A metric fits only on a passage of the stage in context (or one without a stage): the humidity target of
        # another stage is not the target the question is about.
        fits_stage = not own_stage or not context or own_stage in context
        metric = meta.get("metric")
        if fits_stage and metric and metric in slot.metrics:
            why["metric"] = values["metric"]
        elif fits_stage and meta.get("metrics") and len(set(slot.metrics) & set(meta["metrics"])) >= 2:
            why["metric"] = values["metric"]
        card_names = meta.get("card_names") or ()
        if card_names and set(card_names) & set(slot.entities):
            why["card_name"] = values["card_name"]
        column = meta.get("column")
        if column:
            week = None
            if slot.week is not None and slot.week_kind in ("bloom", None):
                week = f"bloom_{slot.week}"
            elif slot.this_week and feed_week:  # "diese Woche": the run's current column
                week = feed_week
            if week and column == week:
                why["nutrient_week"] = values["nutrient_week"]
            page = slot.page
            if page is None and (week or slot.components):
                page = feed_page or run_page
            if page and meta.get("page") == page:
                why["nutrient_page"] = values["nutrient_page"]
            if slot.components and set(slot.components) & set(meta.get("components") or ()):
                why["nutrient_component"] = values["nutrient_component"]
        return sum(why.values()), why

    def _rrf(self, hits: list[Hit], used_dense: bool) -> None:
        k = float(self.thresholds.get("rrf_k", 60))
        channels = ["bm25", "ngram", "boost"] + (["dense"] if used_dense else [])
        for hit in hits:
            hit.channels["rrf"] = 0.0
        for name in channels:
            ranked = sorted((h for h in hits if h.channels[name] > 0), key=lambda h: -h.channels[name])
            for rank, hit in enumerate(ranked, 1):
                hit.channels["rrf"] += 1.0 / (k + rank)


def name_hits_entities(hits: Iterable[slots_mod.NameHit]) -> list[str]:
    return _unique(h.entity for h in hits)


def _feed_position(feed: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """(page key, week column key) from feeding.view() or a plain {"page": ..., "week": ...}."""
    if not isinstance(feed, Mapping):
        return None, None
    page, week = feed.get("page"), feed.get("week")
    if isinstance(page, Mapping):
        page = page.get("key")
    if isinstance(week, Mapping):
        week = week.get("key")
    return (str(page) if page else None), (str(week) if week else None)


# ---------- adapters (spec 2.1) ----------

class _Builder:
    """Reads the cards and the run file into passages."""

    def __init__(self, index: Index, names: Names) -> None:
        self.index = index
        self.names = names
        self.policy = index.policy
        self.passages: list[Passage] = []
        self.sources: dict[str, dict[str, Any]] = {}
        self.skipped: list[str] = []
        self.run_page: str | None = None
        self._ids: set[str] = set()
        blocked = (self.policy.get("strain_fields") or {}).get("blocked") or []
        self._blocked_fields = [text.fold(b) for b in blocked]
        self._blocked_tags = {text.fold(t) for t in (self.policy.get("blocked_topics") or {}).get("tags") or []}

    def build(self) -> None:
        library = self.index.library
        folders = sorted(p for p in library.iterdir() if p.is_dir() and not p.name.startswith("_")) \
            if library.exists() else []
        shelves = self.index.shelves
        # the shelves (owner, 01.11.2026: "he checks at first for everything"): the problem, health, crop and process
        # cards and the whole fertilizer market get an index of their own (vgrow/hank/shelves.py), so they never move
        # the ranking of a growing question in this one; the main index keeps the run's own nutrient cards
        run_cards = None if shelves else self._run_cards()
        for folder in folders:
            if shelves and folder.name not in (*SHELF_FOLDERS, "nutrients"):
                continue
            if not shelves and folder.name in SHELF_FOLDERS:
                continue
            for path in sorted(folder.glob("*.json")):
                card = _read_json(path)
                if not isinstance(card, dict):
                    continue
                if folder.name == "nutrients" and run_cards is not None and path.stem not in run_cards:
                    # the fertilizer market (23.10.2026): a card plant.json's setups do not name (Plagron, CANNA,
                    # BioTabs, the overview of the types) is answered by Hank from code (feedplan.py, "ich nutze
                    # ...") and from the wiki ("Erzähl mir mehr"), never searched: eight new charts would move the
                    # ranking of every growing question, as the strain lexicon would
                    continue
                if card.get("area") and isinstance(card.get("entries"), list):
                    # the strain lexicon (vgrow/strains.py): answered by Hank's strain advice from code, never searched
                    # (213 strains would take the scope decision from every growing question)
                    continue
                kind = folder.name
                adapter = {"stages": self._stages, "strains": self._strain, "nutrients": self._nutrients,
                           "methods": self._methods, "problems": self._problem, "health": self._conditions,
                           "crops": self._crop, "processes": self._process}.get(kind)
                before = len(self.passages)
                if adapter is not None:
                    adapter(kind, path.stem, card)
                elif isinstance(card.get("facts"), list):
                    self._generic(kind, path.stem, card)
                if len(self.passages) == before:
                    self.skipped.append(f"{kind}/{path.stem}")
        if self.skipped:
            _LOGGER.info("Hank index: no adapter for %s", ", ".join(self.skipped))
        self._own_names_only()
        if shelves:
            return  # the run file and the experience belong to the main index
        if self.index.run_file is not None:
            run = _read_json(self.index.run_file)
            if isinstance(run, dict):
                self._run(run)
        self._experience()

    def _own_names_only(self) -> None:
        """A card-name entity counts for the card-name boost only if no other card names it ("Ice Cream Cake" is the
        strain card's own; "Autoflower" or "VIVOSUN" appear on several cards and identify none of them)."""
        holders: dict[str, set[str]] = {}
        for passage in self.passages:
            if passage.kind in OPT_IN_KINDS:  # v1.4: a method card naming the strain takes no name from its card
                continue
            for entity in passage.entities:
                holders.setdefault(entity, set()).add(f"{passage.kind}/{passage.card}")
        for number, passage in enumerate(self.passages):
            names = passage.meta.get("card_names")
            if not names:
                continue
            own = tuple(e for e in names if holders.get(e, set()) <= {f"{passage.kind}/{passage.card}"})
            meta = dict(passage.meta)
            if own:
                meta["card_names"] = own
            else:
                meta.pop("card_names", None)
            self.passages[number] = dataclasses.replace(passage, meta=meta)

    # ----- shared -----

    def _run_cards(self) -> set[str] | None:
        """The nutrient cards plant.json's setups name (or its flat "card"), or None without a run file (a test's
        own library: then every card is searched)."""
        if self.index.run_file is None:
            return None
        run = _read_json(self.index.run_file)
        if not isinstance(run, dict):
            return None
        feed = run.get("feeding") if isinstance(run.get("feeding"), dict) else {}
        setups = feed.get("setups") if isinstance(feed.get("setups"), dict) else {"default": feed}
        return {str(own.get("card")) for own in setups.values() if isinstance(own, dict) and own.get("card")}

    def _card_names(self, *labels: Any) -> tuple[str, ...]:
        out: list[str] = []
        for label in labels:
            if isinstance(label, str):
                for hit in self.names.find(label):
                    if hit.kind in _CARD_NAME_KINDS and hit.entity not in out:
                        out.append(hit.entity)
        return tuple(out)

    def _refs(self, card_key: str, *texts: str) -> list[str]:
        known = self.sources.get(card_key, {})
        found: list[str] = []
        for s in texts:
            for sid in _SOURCE_ID_RE.findall(s or ""):
                if sid in known and sid not in found:
                    found.append(sid)
        return found

    def add(self, kind: str, card: str, path: str, title: tuple[str, str | None], body: tuple[str, str | None],
            sources: Iterable[str], *, status: str = "approved", meta: Mapping[str, Any] | None = None,
            card_names: tuple[str, ...] = ()) -> Passage | None:
        de, en = body
        if not de or not str(de).strip():
            return None
        pid = f"{kind}/{card}#{path}"
        if pid in self._ids:
            _LOGGER.warning("Hank index: duplicate passage id %s, the second one is skipped", pid)
            return None
        self._ids.add(pid)
        twin_missing = not (en and str(en).strip())
        text_en = str(en) if not twin_missing else str(de)
        title_de, title_en = title[0], title[1] or title[0]
        numbers = _unique([*text.parse_numbers(str(de), "de"), *text.parse_numbers(text_en, "en")])
        entities = _unique([*self.names.entities(f"{title_de}\n{de}"), *self.names.entities(f"{title_en}\n{text_en}"),
                            *card_names])
        full_meta = {"path": path, **(meta or {})}
        if card_names:
            full_meta["card_names"] = card_names
        passage = Passage(pid, card, kind, {"de": title_de, "en": title_en}, {"de": str(de), "en": text_en},
                          tuple(numbers), tuple(_unique(sources)), tuple(entities), status, full_meta, twin_missing)
        self.passages.append(passage)
        return passage

    @staticmethod
    def _status(card: Mapping[str, Any]) -> str:
        return "draft" if str(card.get("status") or "").lower() == "draft" else "approved"

    # ----- stages/*.json -----

    def _stages(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        key = f"{kind}/{card_id}"
        self.sources[key] = dict(card.get("sources") or {})
        status = self._status(card)
        name = str(card.get("name") or card_id)
        name_en = str(card.get("name_en") or name)
        names = self._card_names(card.get("name"), card.get("name_en"))
        summary = card.get("summary")
        if summary:
            self.add(kind, card_id, "summary", (name, name_en), (summary, card.get("summary_en")),
                     self._refs(key, summary), status=status, card_names=names)
        notes, notes_en = card.get("notes") or [], card.get("notes_en") or []
        for i, note in enumerate(notes):
            if isinstance(note, str):
                twin = notes_en[i] if i < len(notes_en) and isinstance(notes_en[i], str) else None
                self.add(kind, card_id, f"notes.{i}", (name, name_en), (note, twin), self._refs(key, note),
                         status=status, card_names=names)
        for stage in card.get("stages") or []:
            if isinstance(stage, dict) and stage.get("key"):
                self._stage(kind, card_id, key, stage, status, names)

    def _stage(self, kind: str, card_id: str, key: str, stage: dict[str, Any], status: str,
               names: tuple[str, ...]) -> None:
        skey = str(stage["key"])
        label = str(stage.get("label") or skey)
        label_en = str(stage.get("label_en") or label)
        base_meta = {"stage": skey, "stage_label": {"de": label, "en": label_en},
                     "from_day": stage.get("from_day"), "to_day": stage.get("to_day")}
        targets = [t for t in stage.get("targets") or [] if isinstance(t, dict) and t.get("metric")]
        note, note_en = stage.get("preset_note"), stage.get("preset_note_en")
        rows_de = [f"{t.get('label') or t['metric']} {range_text(t.get('min'), t.get('max'), t.get('unit') or '', 'de')}"
                   for t in targets]
        rows_en = [f"{t.get('label_en') or t.get('label') or t['metric']} "
                   f"{range_text(t.get('min'), t.get('max'), t.get('unit') or '', 'en')}" for t in targets]
        if rows_de or note:
            de = f"{label}: " + "; ".join(rows_de) + "." if rows_de else f"{label}."
            en = f"{label_en}: " + "; ".join(rows_en) + "." if rows_en else f"{label_en}."
            if note:
                de += f" {note}"
                en += f" {note_en or note}"
            srcs = _unique([s for t in targets for s in t.get("sources") or []] + self._refs(key, note or ""))
            self.add(kind, card_id, f"{skey}.overview", (label, label_en),
                     (de, en if (note_en or not note) else None), srcs, status=status,
                     meta={**base_meta, "metrics": tuple(_unique(t["metric"] for t in targets))}, card_names=names)
        for target in targets:
            self._target(kind, card_id, key, target, label, label_en, base_meta, status, names)
        if note:
            self.add(kind, card_id, f"{skey}.preset_note", (label, label_en), (note, note_en),
                     _unique([*self._refs(key, note), *(stage.get("preset_sources") or [])]), status=status,
                     meta=base_meta, card_names=names)
        water = stage.get("watering")
        if isinstance(water, dict):
            minutes, flow = water.get("pump_minutes"), water.get("flow_ml")
            start, start_en = water.get("start") or "", water.get("start_en") or ""
            parts_de = [p for p in (f"Pumpe {fmt(minutes, 'de')} Minuten" if minutes is not None else "",
                                    f"{fmt(flow, 'de')} ml" if flow is not None else "", start) if p]
            parts_en = [p for p in (f"pump {fmt(minutes, 'en')} minutes" if minutes is not None else "",
                                    f"{fmt(flow, 'en')} ml" if flow is not None else "", start_en) if p]
            if parts_de:
                self.add(kind, card_id, f"{skey}.watering", (label, label_en),
                         (f"{label}, Gießen: " + ", ".join(parts_de) + ".",
                          f"{label_en}, watering: " + ", ".join(parts_en) + "." if (start_en or not start) else None),
                         water.get("sources") or [], status=status,
                         meta={**base_meta, "pump_minutes": minutes, "flow_ml": flow}, card_names=names)

    def _target(self, kind: str, card_id: str, key: str, target: dict[str, Any], label: str, label_en: str,
                base_meta: dict[str, Any], status: str, names: tuple[str, ...]) -> None:
        metric = str(target["metric"])
        when = str(target.get("when") or "all")
        unit = str(target.get("unit") or "")
        tlabel = str(target.get("label") or metric)
        tlabel_en = str(target.get("label_en") or tlabel)
        lo, hi = target.get("min"), target.get("max")
        advice = [target.get("advice_high"), target.get("advice_low")]
        advice_en = [target.get("advice_high_en"), target.get("advice_low_en")]
        de = f"{label}, {tlabel}: {range_text(lo, hi, unit, 'de')}."
        en = f"{label_en}, {tlabel_en}: {range_text(lo, hi, unit, 'en')}."
        de += "".join(f" {a}" for a in advice if a)
        en_complete = all(bool(b) == bool(a) for a, b in zip(advice, advice_en))
        en += "".join(f" {a}" for a in advice_en if a)
        meta = {**base_meta, "metric": metric, "when": when, "min": lo, "max": hi, "unit": unit, "label": tlabel,
                "label_en": tlabel_en, "verified": target.get("verified"),
                "verified_reason": target.get("verified_reason")}
        path = f"{base_meta['stage']}.{metric}.{when}"
        srcs = _unique([*(target.get("sources") or []), *self._refs(key, *[a for a in advice if a])])
        self.add(kind, card_id, path, (f"{label}, {tlabel}", f"{label_en}, {tlabel_en}"),
                 (de, en if en_complete else None), srcs, status=status, meta=meta, card_names=names)
        for side in ("high", "low"):
            body = target.get(f"consequence_{side}")
            if not body:
                continue
            self.add(kind, card_id, f"{path}.consequence_{side}", (f"{label}, {tlabel}", f"{label_en}, {tlabel_en}"),
                     (body, target.get(f"consequence_{side}_en")), target.get(f"consequence_{side}_sources") or [],
                     status=status,
                     meta={**meta, "status_key": side, "consequence_from": target.get(f"consequence_{side}_from")},
                     card_names=names)

    # ----- strains/*.json -----

    def _blocked_field(self, name: str) -> bool:
        folded = text.fold(name)
        return any(folded == b or folded.startswith(b) for b in self._blocked_fields)

    def _strain(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        key = f"{kind}/{card_id}"
        self.sources[key] = dict(card.get("sources") or {})
        status = self._status(card)
        name = str(card.get("name") or card_id)
        name_en = str(card.get("name_en") or name)
        names = self._card_names(card.get("name"), card.get("name_en"))
        summary = card.get("summary")
        if summary:
            self.add(kind, card_id, "summary", (name, name_en), (summary, card.get("summary_en")),
                     self._refs(key, summary), status=status, card_names=names)
        facts = card.get("facts")
        facts_en = card.get("facts_en") if isinstance(card.get("facts_en"), dict) else {}
        # The keys of facts and facts_en are the German labels (stable ids); fact_labels_en names them in English,
        # so an English answer never reads "Genetik:" (review 3, L3).
        labels_en = card.get("fact_labels_en") if isinstance(card.get("fact_labels_en"), dict) else {}
        if isinstance(facts, dict):  # source_notes are never indexed (spec 2.1)
            for fact, value in facts.items():
                if not isinstance(value, str) or self._blocked_field(fact):
                    continue
                twin = facts_en.get(fact)
                fact_en = labels_en.get(fact) if isinstance(labels_en.get(fact), str) else None
                self.add(kind, card_id, f"facts.{slug(fact)}",
                         (f"{name}: {fact}", f"{name_en}: {fact_en or fact}"),
                         (value, twin if isinstance(twin, str) else None), self._refs(key, value), status=status,
                         meta={"fact": fact, "fact_en": fact_en}, card_names=names)
        elif isinstance(facts, list):
            self._facts(kind, card_id, key, card, status, names)
        warnings, warnings_en = card.get("warnings") or [], card.get("warnings_en") or []
        for i, warning in enumerate(warnings):
            if isinstance(warning, str):
                twin = warnings_en[i] if i < len(warnings_en) and isinstance(warnings_en[i], str) else None
                self.add(kind, card_id, f"warnings.{i}", (name, name_en), (warning, twin), self._refs(key, warning),
                         status=status, card_names=names)

    # ----- nutrients/*.json -----

    def _nutrients(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        key = f"{kind}/{card_id}"
        self.sources[key] = dict(card.get("sources") or {})
        status = self._status(card)
        name = str(card.get("name") or card_id)
        name_en = str(card.get("name_en") or name)
        names = self._card_names(card.get("name"), card.get("name_en"), card.get("product"))
        unit = str(card.get("unit") or "")
        components = card.get("components") or {}
        columns = [c for c in (card.get("weeks") or {}).get("columns") or [] if isinstance(c, dict) and c.get("key")]
        for page_key, page in (card.get("pages") or {}).items():
            if not isinstance(page, dict):
                continue
            plabel = str(page.get("label") or page_key)
            plabel_en = str(page.get("label_en") or plabel)
            rows = page.get("rows") or {}
            for number, column in enumerate(columns):
                clabel = str(column.get("label") or column["key"])
                clabel_en = str(column.get("label_en") or clabel)
                cells: list[tuple[str, str, Any]] = []
                for comp, values in rows.items():
                    if isinstance(values, list) and number < len(values) and values[number] is not None:
                        info = components.get(comp) or {}
                        cells.append((str(info.get("label") or comp), str(info.get("label_en") or info.get("label")
                                                                            or comp), values[number]))
                if not cells:
                    continue
                de = f"{plabel}, {clabel}: " + ", ".join(f"{c} {fmt(v, 'de')} {unit}".rstrip() for c, _, v in cells) + "."
                en = f"{plabel_en}, {clabel_en}: " + ", ".join(f"{c} {fmt(v, 'en')} {unit}".rstrip()
                                                               for _, c, v in cells) + "."
                meta = {"page": page_key, "column": str(column["key"]), "week": column.get("bloom_week"),
                        "components": tuple(comp for comp, values in rows.items() if isinstance(values, list)
                                            and number < len(values) and values[number] is not None),
                        "unit": unit, "values": {comp: values[number] for comp, values in rows.items()
                                                 if isinstance(values, list) and number < len(values)}}
                self.add(kind, card_id, f"{page_key}.{column['key']}",
                         (f"{name}: {plabel}, {clabel}", f"{name_en}: {plabel_en}, {clabel_en}"), (de, en),
                         page.get("sources") or [], status=status, meta=meta, card_names=names)
        for i, note in enumerate(card.get("notes") or []):
            if isinstance(note, dict) and note.get("text"):
                self.add(kind, card_id, f"notes.{i}", (name, name_en), (note["text"], note.get("text_en")),
                         note.get("sources") or self._refs(key, note["text"]), status=status, card_names=names)
        frequency = card.get("frequency")
        if isinstance(frequency, dict) and frequency.get("text"):
            self.add(kind, card_id, "frequency", (name, name_en), (frequency["text"], frequency.get("text_en")),
                     frequency.get("sources") or [], status=status, card_names=names)
        measure = card.get("measure")  # feeding v2: the label's own measure ("1 spoon=0.5g")
        if isinstance(measure, dict) and measure.get("text"):
            self.add(kind, card_id, "measure", (f"{name}: {measure.get('label') or ''}".rstrip(": "),
                                                f"{name_en}: {measure.get('label_en') or measure.get('label') or ''}"
                                                .rstrip(": ")),
                     (measure["text"], measure.get("text_en")), measure.get("sources") or [], status=status,
                     meta={"measure": measure.get("grams")}, card_names=names)
        if card.get("status_note"):
            self.add(kind, card_id, "status", (name, name_en), (card["status_note"], card.get("status_note_en")),
                     self._refs(key, card["status_note"]), status=status, meta={"card_status": card.get("status")},
                     card_names=names)
        if card.get("summary"):
            self.add(kind, card_id, "summary", (name, name_en), (card["summary"], card.get("summary_en")),
                     self._refs(key, card["summary"]), status=status, card_names=names)

    # ----- methods/*.json (v1.4, owner 23.10.2026: "how to top the node properly") -----

    def _methods(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        """A method card: one passage per field (what, every step, when, autoflower, box_fit, risks), each with the
        source ids its own text cites, the card's status and package (meta "package": the search leaves a card of a
        package that is switched off out, Index._pool). The card's aliases go into the title of its "what" passage,
        so the name finds the card in both languages; nothing is added that the card does not carry."""
        key = f"{kind}/{card_id}"
        self.sources[key] = dict(card.get("sources") or {})
        status = self._status(card)
        label = str(card.get("label") or card_id)
        label_en = str(card.get("label_en") or label)
        aliases = card.get("aliases") if isinstance(card.get("aliases"), Mapping) else {}
        alias_de = [str(a) for a in aliases.get("de") or [] if isinstance(a, str) and a.strip()]
        alias_en = [str(a) for a in aliases.get("en") or [] if isinstance(a, str) and a.strip()]
        names = self._card_names(label, label_en, *alias_de, *alias_en)
        # the technique the card is about: the process names of the directory in its short label (without the
        # explanation in brackets: "cutting off the main tip" is no cutting) and its aliases
        short = [str(x).split(" (")[0].split(":")[0] for x in (label, label_en)]
        covers = tuple(_unique(h.entity for form in (*short, *alias_de, *alias_en)
                               for h in self.names.find(form)
                               if h.kind == "process" and h.end - h.start == len(text.tokens(form, lang="de"))))
        package = card.get("package") if isinstance(card.get("package"), str) and card.get("package") else None
        base = {"method": card_id, "package": package, "covers": covers, "label": {"de": label, "en": label_en},
                "aliases": {"de": tuple(alias_de), "en": tuple(alias_en)}}

        def title(field: str) -> tuple[str, str]:
            if field != "what":
                return label, label_en
            return (label + (f" ({', '.join(alias_de)})" if alias_de else ""),
                    label_en + (f" ({', '.join(alias_en)})" if alias_en else ""))

        for field in METHOD_FIELDS:
            if field == "steps":
                steps = card.get("steps") if isinstance(card.get("steps"), list) else []
                steps_en = card.get("steps_en") if isinstance(card.get("steps_en"), list) else []
                for i, step in enumerate(steps):
                    if not isinstance(step, str):
                        continue
                    twin = steps_en[i] if i < len(steps_en) and isinstance(steps_en[i], str) else None
                    self.add(kind, card_id, f"steps.{i}", title(field), (step, twin), self._refs(key, step),
                             status=status, meta={**base, "field": "steps", "step": i}, card_names=names)
                continue
            body = card.get(field)
            if not isinstance(body, str) or not body.strip():
                continue
            twin = card.get(f"{field}_en")
            self.add(kind, card_id, field, title(field), (body, twin if isinstance(twin, str) else None),
                     self._refs(key, body), status=status, meta={**base, "field": field}, card_names=names)

    # ----- the shelves (vgrow/hank/shelves.py, owner 01.11.2026) -----

    def _texts(self, kind: str, card_id: str, key: str, field: str, title: tuple[str, str], values: Any, twins: Any,
               status: str, meta: Mapping[str, Any], names: tuple[str, ...]) -> None:
        """One passage per text of a field (a string or a list of strings, the English twin beside it), each with the
        source ids it cites ("P1P5" too: problem cards write two ids without a space)."""
        values = [values] if isinstance(values, str) else values if isinstance(values, list) else []
        twins = [twins] if isinstance(twins, str) else twins if isinstance(twins, list) else []
        known = self.sources.get(key, {})
        for i, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                continue
            twin = twins[i] if i < len(twins) and isinstance(twins[i], str) else None
            refs = _unique(sid for sid in _SHELF_ID_RE.findall(value) if sid in known)
            self.add(kind, card_id, f"{field}.{i}", title, (value, twin), refs, status=status,
                     meta={**meta, "field": field}, card_names=names)

    def _shelf_card(self, kind: str, card_id: str, card: Mapping[str, Any]) -> tuple[str, str, tuple[str, str], str,
                                                                                    dict[str, Any], tuple[str, ...]]:
        """The shared part of a shelf card: its sources, title (label and aliases, {"de": [...], "en": [...]}),
        status, package and names."""
        key = f"{kind}/{card_id}"
        sources = card.get("sources")
        self.sources[key] = dict(sources) if isinstance(sources, Mapping) else {}
        label = str(card.get("label") or card_id)
        label_en = str(card.get("label_en") or label)
        aliases = card.get("aliases") if isinstance(card.get("aliases"), Mapping) else {}
        alias_de = [str(a) for a in aliases.get("de") or [] if isinstance(a, str) and a.strip()]
        alias_en = [str(a) for a in aliases.get("en") or [] if isinstance(a, str) and a.strip()]
        names = self._card_names(label, label_en, *alias_de, *alias_en)
        title = (label + (f" ({', '.join(alias_de)})" if alias_de else ""),
                 label_en + (f" ({', '.join(alias_en)})" if alias_en else ""))
        package = card.get("package") if isinstance(card.get("package"), str) and card.get("package") else None
        meta = {"package": package, "label": {"de": label, "en": label_en}}
        return key, label, title, self._status(card), meta, names

    def _problem(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        """A problem card (pests, deficiencies, stress): the tile, symptoms, causes, what to check and do, prevention
        and look-alikes, the aliases in the title so a name finds it in both languages."""
        key, _, title, status, meta, names = self._shelf_card(kind, card_id, card)
        for field in ("tile", "symptoms", "causes", "do_now", "prevention", "look_alikes"):
            self._texts(kind, card_id, key, field, title, card.get(field), card.get(f"{field}_en"), status, meta,
                        names)
        checks = [c for c in card.get("check") or [] if isinstance(c, Mapping)]
        self._texts(kind, card_id, key, "check", title, [str(c.get("text") or "") for c in checks],
                    [str(c.get("text_en") or "") for c in checks], status, meta, names)

    def _conditions(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        """The plant-health collection (health/conditions.json): one card per entry, its summary and facts."""
        table = card.get("sources") if isinstance(card.get("sources"), Mapping) else {}
        for entry in card.get("entries") or []:
            if not isinstance(entry, Mapping) or not entry.get("key"):
                continue
            entry_id = str(entry["key"])
            own = {sid: table[sid] for sid in entry.get("sources") or [] if sid in table}
            key, _, title, status, meta, names = self._shelf_card(kind, entry_id, {**entry, "sources": own,
                                                                                   "status": card.get("status")})
            facts = entry.get("facts") if isinstance(entry.get("facts"), Mapping) else {}
            facts_en = entry.get("facts_en") if isinstance(entry.get("facts_en"), Mapping) else {}
            texts = [entry.get("summary"), *facts.values()]
            twins = [entry.get("summary_en"), *(facts_en.get(k) for k in facts)]
            for i, (value, twin) in enumerate(zip(texts, twins)):
                if isinstance(value, str) and value.strip():
                    self.add(kind, entry_id, f"text.{i}", title, (value, twin if isinstance(twin, str) else None),
                             list(own), status=status, meta={**meta, "field": "summary" if i == 0 else "fact"},
                             card_names=names)

    def _crop(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        """A crop card: its notes for the VGrow, each stage's notes and the warnings."""
        key, _, title, status, meta, names = self._shelf_card(kind, card_id, card)
        fit = card.get("fit_for_vgrow") if isinstance(card.get("fit_for_vgrow"), Mapping) else {}
        for note in ("light_note", "heat_note"):
            self._texts(kind, card_id, key, f"fit.{note}", title, fit.get(note), fit.get(f"{note}_en"), status,
                        meta, names)
        for stage in card.get("stages") or []:
            if isinstance(stage, Mapping) and stage.get("key"):
                stitle = (f"{title[0]}: {stage.get('label') or stage['key']}",
                          f"{title[1]}: {stage.get('label_en') or stage.get('label') or stage['key']}")
                self._texts(kind, card_id, key, f"stages.{stage['key']}", stitle, stage.get("notes"),
                            stage.get("notes_en"), status, meta, names)
        self._texts(kind, card_id, key, "warnings", title, card.get("warnings"), card.get("warnings_en"), status,
                    meta, names)

    def _process(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        """A process card (cloning, drying, curing, germination): each step's notes, the notes for the VGrow and the
        warnings."""
        key, _, title, status, meta, names = self._shelf_card(kind, card_id, card)
        for step in card.get("steps") or []:
            if isinstance(step, Mapping) and step.get("key"):
                stitle = (f"{title[0]}: {step.get('label') or step['key']}",
                          f"{title[1]}: {step.get('label_en') or step.get('label') or step['key']}")
                step_meta = {**meta, "package": step.get("package") or meta["package"]}
                self._texts(kind, card_id, key, f"steps.{step['key']}", stitle, step.get("notes"),
                            step.get("notes_en"), status, step_meta, names)
        fit = card.get("fit_for_vgrow") if isinstance(card.get("fit_for_vgrow"), Mapping) else {}
        for note in ("light_note", "heat_note"):
            self._texts(kind, card_id, key, f"fit.{note}", title, fit.get(note), fit.get(f"{note}_en"), status,
                        meta, names)
        packages = [p for p in card.get("warnings_packages") or [] if isinstance(p, str)]
        self._texts(kind, card_id, key, "warnings", title, card.get("warnings"), card.get("warnings_en"), status,
                    {**meta, "package": packages[0] if packages else meta["package"]}, names)

    # ----- the generic card contract (spec 2.8) -----

    def _generic(self, kind: str, card_id: str, card: dict[str, Any]) -> None:
        key = f"{kind}/{card_id}"
        self.sources[key] = dict(card.get("sources") or {})
        names = self._card_names(card.get("name"), card.get("name_en"), *(card.get("aliases") or []))
        self._facts(kind, card_id, key, card, self._status(card), names)

    def _facts(self, kind: str, card_id: str, key: str, card: dict[str, Any], status: str,
               names: tuple[str, ...]) -> None:
        name = str(card.get("name") or card_id)
        name_en = str(card.get("name_en") or name)
        aliases = [a for a in card.get("aliases") or [] if isinstance(a, str)]
        title_de = name + (f" ({', '.join(aliases)})" if aliases else "")
        title_en = name_en + (f" ({', '.join(aliases)})" if aliases else "")
        for fact in card.get("facts") or []:
            if not isinstance(fact, dict) or not fact.get("id") or not fact.get("text"):
                continue
            labels = {text.fold(str(t)) for t in [fact.get("topic"), *(fact.get("tags") or [])] if t}
            if labels & self._blocked_tags:
                continue  # chat_policy.json blocked_topics.tags: never indexed
            value = fact.get("value") if isinstance(fact.get("value"), dict) else {}
            meta = {"topic": fact.get("topic"), "tags": tuple(fact.get("tags") or ()), "min": value.get("min"),
                    "max": value.get("max"), "unit": value.get("unit")}
            self.add(kind, card_id, f"facts.{slug(fact['id'])}", (title_de, title_en),
                     (fact["text"], fact.get("text_en")), fact.get("sources") or self._refs(key, fact["text"]),
                     status=status, meta=meta, card_names=names)

    # ----- plant.json -----

    def _run(self, run: dict[str, Any]) -> None:
        card_id = self.index.run_file.stem if self.index.run_file else "plant"
        kind = "run"
        key = f"{kind}/{card_id}"
        # feeding v2: plant.json's "feeding" holds its setups (feeding.setups_of merges the shared keys into each).
        # The first one gives the run page for the search boost; the page of the card that doses comes from the live
        # feeding view (context._consistent), which knows the owner's choice.
        setups = list(feeding_mod.setups_of(run).values())
        feeding = setups[0] if setups else {}
        self.run_page = str(feeding.get("page")) if feeding.get("page") else None
        nutrients = [f"nutrients/{s.get('card')}" for s in setups if s.get("card")]
        # plant.json names its own sources W1 to W4; the feeding factor cites the nutrient cards' N5, N6 (the same
        # in both cards).
        merged: dict[str, Any] = {}
        for nutrient in reversed(nutrients):
            merged.update(self.sources.get(nutrient, {}))
        self.sources[key] = {**merged, **dict(run.get("sources") or {})}
        title = ("Durchgang", "Run")
        for name, target in (run.get("water_targets") or {}).items():
            if not isinstance(target, dict):
                continue
            label = str(target.get("label") or name)
            label_en = str(target.get("label_en") or label)
            span_de = range_text(target.get("min"), target.get("max"), str(target.get("unit") or ""), "de")
            span_en = range_text(target.get("min"), target.get("max"), str(target.get("unit") or ""), "en")
            note, note_en = target.get("note") or "", target.get("note_en") or ""
            de = " ".join(p for p in (f"{label}: {span_de}." if span_de else f"{label}.", note) if p)
            en = " ".join(p for p in (f"{label_en}: {span_en}." if span_en else f"{label_en}.", note_en) if p)
            # the declared sources plus the ids the note itself cites ("CANNA ... (W2)"; review 3, L6)
            refs = _unique([*(target.get("sources") or []), *self._refs(key, de, en)])
            self.add(kind, card_id, f"water_targets.{name}", (label, label_en),
                     (de, en if (note_en or not note) else None), refs,
                     meta={"metric": f"water_{name}", "min": target.get("min"), "max": target.get("max"),
                           "unit": target.get("unit")})
        medium = run.get("medium")
        if isinstance(medium, dict) and medium.get("name"):
            de = " ".join(p for p in (str(medium["name"]) + ".", medium.get("note") or "") if p)
            en_ok = bool(medium.get("name_en")) and (bool(medium.get("note_en")) or not medium.get("note"))
            en = " ".join(p for p in (str(medium.get("name_en") or "") + ".", medium.get("note_en") or "") if p)
            self.add(kind, card_id, "medium", title, (de, en if en_ok else None), self._refs(key, de),
                     meta={"type": medium.get("type")})
        if feeding.get("dose_factor") is not None and feeding.get("dose_factor_note"):
            self.add(kind, card_id, "feeding.dose_factor", title,
                     (feeding["dose_factor_note"], feeding.get("dose_factor_note_en")),
                     feeding.get("dose_factor_sources") or self._refs(key, feeding["dose_factor_note"]),
                     meta={"dose_factor": feeding.get("dose_factor"), "card": feeding.get("card"),
                           "page": None})

    # ----- STATE_DIR/hank/experience.jsonl (spec 8.4) -----

    def _experience(self) -> None:
        path = self.index.experience_file
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for number, line in enumerate(lines):
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a cut last line (spec 0.3 rule 7)
            if not isinstance(item, dict):
                continue
            body = item.get("text")
            de = body.get("de") if isinstance(body, dict) else body
            en = body.get("en") if isinstance(body, dict) else None
            if not isinstance(de, str) or not de.strip():
                continue
            ident = slug(str(item.get("id") or number))
            self.add(OWN_KIND, "own", f"own.{ident}", ("Eigene Erfahrung", "Own experience"), (de, en), ["own"],
                     status="own", meta={"stage": item.get("stage"), "run": item.get("run"), "t": item.get("t")})
