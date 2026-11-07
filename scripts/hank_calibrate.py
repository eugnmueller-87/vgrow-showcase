"""Calibrate Hank's route thresholds (docs/hank-v1-spec.md 2.4, 10.4 WP11, 11.3; addendum N2, N7).

    python scripts/hank_calibrate.py                                       lexical set only, nothing written
    python scripts/hank_calibrate.py --url http://127.0.0.1:11434          both sets (dense with the embedding model)
    python scripts/hank_calibrate.py --url http://127.0.0.1:11434 --write  and write vgrow/hank/data/thresholds.json

Input: the eval set (tests/hank_eval/cases.json, expected.json) plus generated variants (spec 2.4): per case the two
longest content words with a swapped, a dropped and a doubled letter, ae/oe/ue for an umlaut and a missing umlaut;
rewordings from the gate examples (data/gates.json, the four safety classes); and the off-topic sentences of
data/offtopic.json (addendum N2), each scored with itself left out of the off-topic bank ("leave one out"), because
with itself in the bank its similarity is 1 by construction.

Per item the router runs once up to the decision (gates, search, off-topic similarity, the live-state row, the
uncovered names, the clarify candidates). The decision table of addendum N2 is then replayed in plain Python for every
point of a grid over T_HIT, T_OFF and T_DOMAIN; the chosen point is checked against the real Router afterwards (every
item, the routes must agree). T_GATE is not calibrated: the gates run before the embedding (addendum N2) and read the
lexical set's T_GATE only, so the dense set's T_GATE has no effect.

Objective, lexicographic (spec 2.4): 1. no misrouted safety item (inject, health, sale, legal), 2. never grow for an
item whose expected route is no_data (an uncovered name), 3. largest route accuracy, 4. largest recall@3, 5. smallest
clarify rate. Ties (the spec names no rule): the optimal value nearest to the file's start value, T_HIT first, then
T_OFF, then T_DOMAIN; where the data does not decide, nothing moves. The report gives each value's plateau (the range
that keeps the best score with the others fixed), which is the margin the eval set leaves. All variants of a case
sit in the same fold of the 5-fold cross-validation.

Router rows 3b and 4b (added by WP11, 23.10.2026): with the thresholds alone no grid point kept every growing case
grow and every off-topic sentence off grow, because one common card word lifts some everyday questions to T_HIT. The
dense set therefore also calibrates M_OFF (row 3b: the nearest off-topic example beats the nearest card passage by
that much, raw cosines), the lexical set T_OFF_HARD (row 4b: 3-gram similarity to an off-topic example, from 0.50
up). Both are searched after T_*, then T_* once more with the chosen value.

Dense set: the embedding model's vectors come from --url (OLLAMA_URL by default, read from ~/.vgrow.env) once and are
cached in STATE_DIR/hank/eval/embed-cache-<model>.json, so a second run does not ask the Mac again. dense_basis (spec
2.2: the median cosine of unrelated pairs) is measured as the median cosine between the off-topic sentences and the
card passages. The dense fusion weights are compared over a few variants; the start weights stay unless a variant wins
the objective.

Report: printed, and as calibration-<time>.json and calibration-latest.md in STATE_DIR/hank/eval/. The fixtures run in
a temporary state folder with the fixture's day frozen, like scripts/hank_eval.py.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import copy
import hashlib
import json
import math
import re
import statistics
import sys
import tempfile
import time
from array import array
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for extra in (REPO, REPO / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import aiohttp  # noqa: E402

from hank_eval import fixtures, report  # noqa: E402
from hank_eval.score import recall_at_3  # noqa: E402

from vgrow import config  # noqa: E402
from vgrow.hank import gates as gates_mod  # noqa: E402
from vgrow.hank import ollama, text, typo  # noqa: E402
from vgrow.hank.kartei_index import NOT_BY_DEFAULT, THRESHOLDS_FILE, Index  # noqa: E402
from vgrow.hank.router import Router, clean  # noqa: E402
from vgrow.hank.types import Route  # noqa: E402

SETS = ("lexical", "dense")
SAFETY = frozenset({"inject", "health", "health_emergency", "sale", "legal"})
SAFETY_EXAMPLE_GATES = ("inject", "health", "sale", "legal")
CALIBRATED = ("T_HIT", "T_OFF", "T_DOMAIN")  # plus M_OFF for the dense set (row 3b of the router)
FOLDS = 5
EMBED_BATCH = 16  # the server's batch size for the passages (kartei_index.DOC_BATCH)
EMBED_TIMEOUT_S = 300.0  # one batch through the tunnel, with a possible cold load of the model

GRID = {
    "T_HIT": [round(0.20 + 0.01 * i, 2) for i in range(61)],  # 0.20 to 0.80
    "T_OFF": [round(0.20 + 0.025 * i, 3) for i in range(33)],  # 0.20 to 1.00
    "T_DOMAIN": [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
}
# Dense fusion weight variants (spec 2.4 start value first): bm25, ngram, dense.
DENSE_WEIGHTS = [
    {"bm25": 0.40, "ngram": 0.30, "dense": 0.30},
    {"bm25": 0.35, "ngram": 0.25, "dense": 0.40},
    {"bm25": 0.30, "ngram": 0.20, "dense": 0.50},
    {"bm25": 0.45, "ngram": 0.35, "dense": 0.20},
]

_UMLAUT_DIGRAPH = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
_UMLAUT_PLAIN = str.maketrans("äöüÄÖÜ", "aouAOU")
_WORD = re.compile(r"[^\W\d_]{5,}")


# ---------- shared helpers (scripts/hank_bench.py and scripts/hank_eval.py use them too) ----------

@contextlib.contextmanager
def isolated(prefix: str = "hank-wp11-") -> Iterator[Path]:
    """A temporary state folder, no credentials and no push target, the fixture's day frozen (as hank_eval.run)."""
    saved = (config.STATE_DIR, config.ENV_FILE, config.BRAIN_NTFY_FILE)
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        root = Path(tmp)
        config.ENV_FILE = root / "no.env"
        config.BRAIN_NTFY_FILE = root / "no-brain.env"
        config.use_state_dir(root / "state")
        try:
            with fixtures.frozen_day():
                yield root
        finally:
            config.use_state_dir(saved[0])
            config.ENV_FILE, config.BRAIN_NTFY_FILE = saved[1], saved[2]


class LiveBook:
    """One built fixture per (name, lang, ledger), each in its own subfolder of the isolated state folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._built: dict[tuple[str, str, str | None], dict[str, Any]] = {}

    def get(self, name: str, lang: str, ledger: str | None = None) -> dict[str, Any]:
        key = (name, lang, ledger)
        if key not in self._built:
            config.use_state_dir(self.root / f"live-{len(self._built)}")
            self._built[key] = fixtures.build(name, lang=lang, ledger=ledger).sources()
            config.use_state_dir(self.root / "state")
        return self._built[key]


def _pack(vec: list[float]) -> str:
    return base64.b64encode(array("f", vec).tobytes()).decode("ascii")


def _unpack(data: str) -> list[float]:
    values = array("f")
    values.frombytes(base64.b64decode(data))
    return list(values)


def default_out_dir() -> Path:
    return config.STATE_DIR / "hank" / "eval"


def safe_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


class CachedEmbedder:
    """The Embedder of spec appendix A in front of one Ollama URL, with a vector cache on disk.

    Every miss opens its own aiohttp session in the running event loop (the eval runs each case in a new loop), asks
    Ollama in batches of EMBED_BATCH, one request at a time, and saves the cache. offline=True never asks: a miss
    raises OllamaError("no_ollama"), so the index falls back to lexical exactly as the server would."""

    def __init__(self, url: str | None, model: str, cache_file: Path, *, offline: bool = False) -> None:
        self.url = (url or ollama.ollama_url()).rstrip("/")
        self.embed_model = model
        self.cache_file = cache_file
        self.offline = offline
        self.requests = 0
        self.texts_sent = 0
        self.seconds = 0.0
        self._cache: dict[str, list[float]] = {}
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("model") == model:
            for key, packed in (data.get("vectors") or {}).items():
                with contextlib.suppress(ValueError, TypeError):
                    self._cache[key] = _unpack(packed)

    def _key(self, kind: str, value: str) -> str:
        return hashlib.sha256(f"{self.embed_model}\0{kind}\0{value}".encode()).hexdigest()

    def cached(self, value: str, kind: str = "query") -> list[float] | None:
        return self._cache.get(self._key(kind, value))

    def save(self) -> None:
        payload = {"version": 1, "model": self.embed_model,
                   "vectors": {k: _pack(v) for k, v in sorted(self._cache.items())}}
        config.atomic_write(self.cache_file, json.dumps(payload) + "\n")

    async def prefetch(self, texts: Iterable[str], kind: str = "query") -> int:
        """Ask Ollama for every text not in the cache. Returns how many were asked for."""
        todo = list(dict.fromkeys(t for t in texts if self._key(kind, t) not in self._cache))
        if not todo:
            return 0
        if self.offline:
            raise ollama.OllamaError("no_ollama", f"{len(todo)} vectors missing and --offline-embed set")
        async with aiohttp.ClientSession() as session:
            client = ollama.OllamaClient(session, self.url)
            for start in range(0, len(todo), EMBED_BATCH):
                chunk = todo[start:start + EMBED_BATCH]
                t0 = time.monotonic()
                vectors = await client.embed(chunk, kind=kind, timeout=EMBED_TIMEOUT_S, model=self.embed_model)
                self.seconds += time.monotonic() - t0
                self.requests += 1
                self.texts_sent += len(chunk)
                for value, vec in zip(chunk, vectors):
                    self._cache[self._key(kind, value)] = vec
        self.save()
        return len(todo)

    async def embed(self, texts: list[str], *, kind: str = "query", timeout: float | None = None) -> list[list[float]]:
        await self.prefetch(texts, kind)
        return [list(self._cache[self._key(kind, t)]) for t in texts]


def _normalize(vec: Iterable[float]) -> list[float]:
    values = [float(v) for v in vec]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm else values


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------- items ----------

@dataclass
class Item:
    id: str
    group: str  # the fold group: all variants of one case share it
    question: str
    lang: str
    expected: str
    accept: frozenset[str]
    source: str  # case, variant, gate_example, offtopic_file
    fixture: str = "F22"
    ledger: str | None = None
    hits: tuple[str, ...] = ()
    status: str = "active"
    exclude: str | None = None  # the off-topic example left out of the bank (leave one out)
    case_kind: str = ""  # grow, live, nodata, off, ... (the case id's prefix)


def _content_words(question: str) -> list[str]:
    found = []
    for word in _WORD.findall(question):
        if text.fold(word) in text.STOP:
            continue
        if word not in found:
            found.append(word)
    return sorted(found, key=lambda w: (-len(w), found.index(w)))[:2]


def word_variants(word: str) -> list[tuple[str, str]]:
    """Typo variants of spec 2.4, never at the first letter."""
    n = len(word)
    m = max(2, n // 2)
    out = [("swap", word[:m - 1] + word[m] + word[m - 1] + word[m + 1:]) if m < n else ("swap", word),
           ("drop", word[:m] + word[m + 1:]),
           ("double", word[:m] + word[m] + word[m:])]
    if any(c in _UMLAUT_DIGRAPH for c in word):
        out.append(("digraph", "".join(_UMLAUT_DIGRAPH.get(c, c) for c in word)))
        out.append(("no_umlaut", word.translate(_UMLAUT_PLAIN)))
    seen: set[str] = set()
    kept = []
    for kind, value in out:
        if value != word and value not in seen:
            seen.add(value)
            kept.append((kind, value))
    return kept


def build_items() -> list[Item]:
    cases = report.load_cases()
    expected = report.load_expected()
    items: list[Item] = []
    for case in cases:
        exp = expected[case["id"]]
        route = exp["route_today"]
        accept = frozenset({route})
        common = {"lang": case["lang"], "expected": route, "accept": accept, "fixture": exp["fixture"],
                  "ledger": exp.get("ledger"), "hits": tuple(exp.get("hits") or ()), "status": exp["status"],
                  "case_kind": case["id"].split("-")[0]}
        items.append(Item(case["id"], case["id"], case["question"], source="case", **common))
        number = 0
        for word in _content_words(case["question"]):
            for kind, value in word_variants(word):
                number += 1
                question = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", value, case["question"], count=1)
                items.append(Item(f"{case['id']}~{number}:{kind}", case["id"], question, source="variant",
                                  **common))
    gate_data = json.loads((REPO / "vgrow" / "hank" / "data" / "gates.json").read_text(encoding="utf-8"))
    for gate in SAFETY_EXAMPLE_GATES:
        accept = frozenset({gate, "health_emergency"}) if gate == "health" else frozenset({gate})
        for lang, sentences in (gate_data[gate].get("examples") or {}).items():
            for number, sentence in enumerate(sentences, 1):
                items.append(Item(f"gate:{gate}:{lang}:{number}", f"gate:{gate}:{lang}:{number}", sentence, lang,
                                  gate, accept, "gate_example", case_kind=gate))
    off_data = json.loads((REPO / "vgrow" / "hank" / "data" / "offtopic.json").read_text(encoding="utf-8"))
    for lang, sentences in off_data["examples"].items():
        for number, sentence in enumerate(sentences, 1):
            items.append(Item(f"offtopic.json:{lang}:{number}", f"offtopic.json:{lang}:{number}", sentence, lang,
                              "offtopic", frozenset({"offtopic"}), "offtopic_file", exclude=sentence,
                              case_kind="off"))
    return items


# ---------- features: the router once, up to the decision ----------

@dataclass
class Feat:
    conf: float
    off: float  # with the excluded example left out
    off_inbank: float  # with every example in the bank (what the real router sees)
    state: bool
    tool: float | None
    uncovered: bool
    entities: bool
    dense: str
    hits: list[str]
    reason: str = ""
    nn: float | None = None  # row 3b: nearest off-topic cosine minus nearest card cosine (left out)
    nn_inbank: float | None = None  # the same with every example in the bank


@dataclass
class Rec:
    item: Item
    fixed: str | None
    base: Feat | None = None
    alts: list[Feat] = field(default_factory=list)
    vec: list[float] | None = None


def _offtopic(bank: gates_mod.ExampleBank, q: str, vec: list[float] | None, basis: float, used: bool,
              exclude: str | None) -> tuple[float, float]:
    """(left out, in bank): the router's off-topic similarity (3-gram Dice, and the calibrated cosine when the dense
    channel was used; the larger counts), once without the excluded example and once with every example."""
    grams = gates_mod.gram_set(q)
    loo = inbank = 0.0
    qn = _normalize(vec) if (used and vec) else None
    vectors = bank.vectors if qn is not None and bank.vectors else None
    for pos, example in enumerate(bank.examples):
        value = gates_mod.dice(grams, example.grams)
        if vectors is not None:
            vec_e = vectors[pos]
            if len(vec_e) == len(qn):
                cos = _dot(qn, vec_e)
                value = max(value, max(0.0, min(1.0, (cos - basis) / (1 - basis))))
        inbank = max(inbank, value)
        if example.text != exclude:
            loo = max(loo, value)
    return loo, inbank


def _basis(index: Index) -> float:
    basis_map = index.thresholds.get("dense_basis") or {}
    return float(basis_map.get(index.model or "", basis_map.get("default", 0.25)))


def feature(r: Router, q: str, lang: str, state: Any, vec: list[float] | None, exclude: str | None) -> Feat:
    stage = state.stage if state is not None else None
    feed = state.feeding_view if state is not None else None
    found = r.index.search(q, vec, lang=lang, stage=stage, feed=feed, aliases=None,
                           dense_state="used" if vec is not None else None)
    used = found.dense == "used" and vec is not None
    loo, inbank = _offtopic(r.offtopic, q, vec, _basis(r.index), used, exclude)
    tool = None
    if r.index.by_meta(kind="tool"):
        t = r.index.search(q, lang=lang, kinds=["tool"], aliases=None, limit=3)
        tool = t.confidence if t.hits else None
    feat = Feat(found.confidence, loo, inbank, bool(r._state_rule(q, found)), tool, bool(found.uncovered),
                bool(found.slots.entities), found.dense, [h.passage.id for h in found.hits])
    if used and r.offtopic.vectors:
        qn = _normalize(vec or [])
        card = r.index.max_cosine(qn)
        cos = [(_dot(qn, v), e.text) for e, v in zip(r.offtopic.examples, r.offtopic.vectors)]
        if card is not None and cos:
            feat.nn_inbank = max(c for c, _ in cos) - card
            feat.nn = max((c for c, t in cos if t != exclude), default=-1.0) - card
    feat.corrections = found.corrections  # type: ignore[attr-defined]
    return feat


def build_records(r: Router, items: list[Item], lives: LiveBook, vec_of: Any) -> list[Rec]:
    from vgrow.hank import livestate  # noqa: F401  (the router's own import, kept for clarity)

    records = []
    for item in items:
        live = lives.get(item.fixture, item.lang, item.ledger)
        early = r._before_search(item.question, lang_hint=item.lang, live=live, pending=None, mode="on",
                                 dense_vec=None, dense_state=None, client_id=None, aliases=None, deferred=True)
        if isinstance(early, Route):
            records.append(Rec(item, early.name))
            continue
        cleaned, lang, state = early[:3]
        vec = vec_of(cleaned)
        base = feature(r, cleaned, lang, state, vec, item.exclude)
        asks = [c for c in base.corrections if c.mode == "ask" and r._speaks(c, lang)]  # type: ignore[attr-defined]
        alts = [feature(r, typo.substitute(cleaned, c), lang, state, vec, item.exclude) for c in asks]
        records.append(Rec(item, None, base, alts, vec))
    return records


# ---------- the decision table of addendum N2, replayed ----------

def decide(f: Feat, th: Mapping[str, float], inbank: bool) -> tuple[str, bool]:
    off = f.off_inbank if inbank else f.off
    if off >= th["T_OFF"] and f.conf < th["T_HIT"]:
        return "offtopic", True
    if f.state:
        return "grow", False
    nn = f.nn_inbank if inbank else f.nn
    if th.get("M_OFF") is not None and nn is not None and nn >= th["M_OFF"]:
        return "offtopic", True
    if f.tool is not None and f.tool >= th["T_HIT"]:
        return "grow", False
    if f.uncovered:
        return "no_data", False
    if th.get("T_OFF_HARD") is not None and f.dense != "used" and off >= th["T_OFF_HARD"]:
        return "offtopic", True
    if f.conf >= th["T_HIT"]:
        return "grow", False
    if f.entities:
        return "no_data", False
    if f.conf >= th["T_DOMAIN"]:
        return "no_data", False
    return "offtopic", False


def route_of(rec: Rec, th: Mapping[str, float], inbank: bool = False) -> str:
    if rec.fixed is not None:
        return rec.fixed
    assert rec.base is not None
    name, known_off = decide(rec.base, th, inbank)
    if not known_off:
        for alt in rec.alts:
            if decide(alt, th, inbank)[0] != name:
                return "clarify"
    return name


@dataclass
class Score:
    safety_bad: int
    uncovered_grow: int
    accuracy: float
    recall: float
    clarify_rate: float
    detail: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[float, ...]:
        return (-self.safety_bad, -self.uncovered_grow, round(self.accuracy, 9), round(self.recall, 9),
                -round(self.clarify_rate, 9))


def recall_of(records: list[Rec], sources: tuple[str, ...] = ("case",)) -> float | None:
    values = []
    for rec in records:
        item = rec.item
        if item.source in sources and item.hits and item.status != "waits":
            hits = rec.base.hits if rec.base is not None else []
            value = recall_at_3(list(item.hits), hits)
            if value is not None:
                values.append(value)
    return sum(values) / len(values) if values else None


def score(records: list[Rec], th: Mapping[str, float], recall: float) -> Score:
    safety_bad = uncovered = correct = clar = 0
    for rec in records:
        got = route_of(rec, th)
        exp = rec.item
        if exp.expected in SAFETY and got not in exp.accept:
            safety_bad += 1
        if exp.expected == "no_data" and got == "grow":
            uncovered += 1
        if got in exp.accept:
            correct += 1
        if got == "clarify":
            clar += 1
    n = max(1, len(records))
    return Score(safety_bad, uncovered, correct / n, recall, clar / n)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def search_grid(records: list[Rec], recall: float, base: Mapping[str, float]) -> tuple[dict[str, float], Score, int]:
    """The best grid point by the lexicographic objective; ties by medians. Returns (thresholds, score, ties)."""
    best_key: tuple[float, ...] | None = None
    best: list[dict[str, float]] = []
    for t_hit in GRID["T_HIT"]:
        for t_off in GRID["T_OFF"]:
            for t_dom in GRID["T_DOMAIN"]:
                if t_dom > t_hit:
                    continue
                th = {**base, "T_HIT": t_hit, "T_OFF": t_off, "T_DOMAIN": t_dom}
                key = score(records, th, recall).key()
                if best_key is None or key > best_key:
                    best_key, best = key, [th]
                elif key == best_key:
                    best.append(th)
    chosen = best
    for name in CALIBRATED:
        values = sorted({t[name] for t in chosen})
        mid = _median(values)
        start = base.get(name)
        pick = mid if start is None else min(values, key=lambda v: (abs(v - start), abs(v - mid)))
        chosen = [t for t in chosen if t[name] == pick]
    th = chosen[0]
    return th, score(records, th, recall), len(best)


def plateau(records: list[Rec], recall: float, th: Mapping[str, float]) -> dict[str, list[float]]:
    """Per calibrated value, the range around the chosen point (others fixed) that keeps the best score."""
    best = score(records, th, recall).key()
    out = {}
    for name in CALIBRATED:
        ok = [v for v in GRID[name] if score(records, {**th, name: v}, recall).key() == best]
        out[name] = [min(ok), max(ok)] if ok else []
    return out


M_OFF_GRID = [None, *[round(0.01 * i, 2) for i in range(0, 31)]]
# Floor 0.50: the highest 3-gram similarity of a growing eval case to an off-topic example is 0.32 (23.10.2026).
T_OFF_HARD_GRID = [None, *[round(0.50 + 0.05 * i, 2) for i in range(11)]]


def search_with_margin(records: list[Rec], recall: float, base: Mapping[str, float],
                       dense: bool) -> tuple[dict[str, float], Score, int]:
    """search_grid plus row 3b of the router (M_OFF for the dense set, T_OFF_HARD for the lexical one): T_* first
    without the row, then the row's value over its grid (None: the row off) with those T_*, then T_* again."""
    name = "M_OFF" if dense else "T_OFF_HARD"
    plain = {k: v for k, v in base.items() if k not in ("M_OFF", "T_OFF_HARD")}
    th, s, ties = search_grid(records, recall, plain)
    best_key, best = None, []
    for m in (M_OFF_GRID if dense else T_OFF_HARD_GRID):
        cand = {**th, **({} if m is None else {name: m})}
        key = score(records, cand, recall).key()
        if best_key is None or key > best_key:
            best_key, best = key, [m]
        elif key == best_key:
            best.append(m)
    values = [m for m in best if m is not None]
    if not values:
        return th, s, ties
    start = base.get(name)
    m_off = _median(values) if start is None else min(values, key=lambda v: abs(v - start))
    return search_grid(records, recall, {**plain, name: m_off})


def fold_of(group: str) -> int:
    return int(hashlib.sha256(group.encode()).hexdigest(), 16) % FOLDS


def cross_validate(records: list[Rec], recall: float, base: Mapping[str, float],
                   dense: bool = False) -> list[dict[str, Any]]:
    out = []
    for k in range(FOLDS):
        train = [r for r in records if fold_of(r.item.group) != k]
        test = [r for r in records if fold_of(r.item.group) == k]
        th, _, _ = search_with_margin(train, recall, base, dense)
        s = score(test, th, recall)
        test_ids = {r.item.id for r in test}
        c = checks(test, th)
        out.append({"fold": k, "train": len(train), "test": len(test),
                    "thresholds": {n: th[n] for n in (*CALIBRATED, "M_OFF", "T_OFF_HARD") if n in th},
                    "test_offtopic_file_to_grow_left_out": len(c["offtopic_file_left_out_to_grow"]),
                    "test_grow_live_not_grow": len(c["grow_live_cases_not_grow"]) + len(
                        c["grow_live_variants_not_grow"]),
                    "test_ids": len(test_ids),
                    "test_accuracy": round(s.accuracy, 4), "test_safety_bad": s.safety_bad,
                    "test_uncovered_grow": s.uncovered_grow, "test_clarify_rate": round(s.clarify_rate, 4)})
    return out


# ---------- the checks of spec 11.3 and the owner's off-topic requirement ----------

def checks(records: list[Rec], th: Mapping[str, float]) -> dict[str, Any]:
    def routes(pred: Any, inbank: bool = False) -> list[tuple[str, str]]:
        return [(r.item.id, route_of(r, th, inbank)) for r in records if pred(r.item)]

    def wrong(rows: list[tuple[str, str]], accept: Mapping[str, frozenset[str]]) -> list[str]:
        return [f"{i}={g}" for i, g in rows if g not in accept[i]]

    accept = {r.item.id: r.item.accept for r in records}
    cases = [r for r in records if r.item.source == "case"]
    active_cases = routes(lambda i: i.source == "case" and i.status != "waits")
    variants = routes(lambda i: i.source == "variant")
    safety = routes(lambda i: i.expected in SAFETY)
    grow_live = routes(lambda i: i.source == "case" and i.case_kind in ("grow", "live") and i.status != "waits")
    grow_live_var = routes(lambda i: i.source == "variant" and i.case_kind in ("grow", "live") and i.status != "waits")
    off_cases = routes(lambda i: i.source == "case" and i.expected == "offtopic")
    off_file_in = routes(lambda i: i.source == "offtopic_file", inbank=True)
    off_file_loo = routes(lambda i: i.source == "offtopic_file")
    no_data = routes(lambda i: i.expected == "no_data")
    typo_cases = [r for r in cases if r.item.id in TYPO_CASES]

    def share(rows: list[tuple[str, str]]) -> float | None:
        return None if not rows else round(sum(g in accept[i] for i, g in rows) / len(rows), 4)

    def bad(rows: list[tuple[str, str]], route: str) -> list[str]:
        return [i for i, g in rows if g == route]

    return {
        "route_active_cases": share(active_cases), "route_active_wrong": wrong(active_cases, accept),
        "route_variants": share(variants), "route_variants_wrong": wrong(variants, accept),
        "safety": share(safety), "safety_wrong": wrong(safety, accept),
        "typo_cases_without_clarify": all(route_of(r, th) == r.item.expected for r in typo_cases),
        "typo_cases_wrong": [f"{r.item.id}={route_of(r, th)}" for r in typo_cases
                             if route_of(r, th) != r.item.expected],
        "no_data_routed_grow": bad(no_data, "grow"),
        "grow_live_cases_not_grow": [f"{i}={g}" for i, g in grow_live if g != "grow"],
        "grow_live_variants_not_grow": [f"{i}={g}" for i, g in grow_live_var if g != "grow"],
        "offtopic_cases_to_grow": bad(off_cases, "grow"),
        "offtopic_cases_not_offtopic": [f"{i}={g}" for i, g in off_cases if g != "offtopic"],
        "offtopic_file_in_bank_to_grow": [i for i, g in off_file_in if g == "grow"],
        "offtopic_file_in_bank_not_offtopic": [f"{i}={g}" for i, g in off_file_in if g not in ("offtopic",)],
        "offtopic_file_left_out_to_grow": [i for i, g in off_file_loo if g == "grow"],
        "offtopic_file_left_out_not_offtopic": [f"{i}={g}" for i, g in off_file_loo if g != "offtopic"],
        "counts": {"items": len(records), "cases": len(cases), "variants": len(variants),
                   "safety_items": len(safety), "offtopic_file": len(off_file_in)},
    }


TYPO_CASES = frozenset()  # filled from expected.json (typo: true) in main


def owner_feasible(records: list[Rec], recall: float, base: Mapping[str, float]) -> dict[str, Any]:
    """Is there any grid point where every active grow and live case is grow, no off-topic case and no sentence of
    offtopic.json (in the bank, as the router runs) is grow, no safety item is missed and no no_data case is grow?"""
    found = []
    for t_hit in GRID["T_HIT"]:
        for t_off in GRID["T_OFF"]:
            th = {**base, "T_HIT": t_hit, "T_OFF": t_off, "T_DOMAIN": min(base["T_DOMAIN"], t_hit)}
            ok = True
            for rec in records:
                i = rec.item
                got = route_of(rec, th, inbank=i.source == "offtopic_file")
                if i.source == "case" and i.case_kind in ("grow", "live") and i.status != "waits" and got != "grow":
                    ok = False
                elif i.expected == "offtopic" and i.source in ("case", "offtopic_file") and got == "grow":
                    ok = False
                elif i.expected == "no_data" and i.source == "case" and got == "grow":
                    ok = False
                if not ok:
                    break
            if ok:
                found.append((t_hit, t_off))
    return {"feasible": bool(found), "points": len(found),
            "T_HIT_range": [min(p[0] for p in found), max(p[0] for p in found)] if found else None,
            "T_OFF_range": [min(p[1] for p in found), max(p[1] for p in found)] if found else None}


def verify(r: Router, records: list[Rec], th: Mapping[str, float], set_name: str, lives: LiveBook) -> list[str]:
    """The chosen point against the real Router (bank with every example, so offtopic.json items in-bank)."""
    real = Router(r.index, gates=r.gates, offtopic=r.offtopic, thresholds={set_name: dict(th)})
    real._domain_key = r._domain_key
    # the replay tunes the main decision; the shelves (vgrow/hank/shelves.py) come after it, on a refusal only
    real._shelf_route = lambda *_a, **_k: None  # type: ignore[method-assign]
    mismatches = []
    for rec in records:
        i = rec.item
        live = lives.get(i.fixture, i.lang, i.ledger)
        got = real.route(i.question, lang_hint=i.lang, live=live, dense_vec=rec.vec,
                         dense_state="used" if rec.vec is not None else None).name
        want = route_of(rec, th, inbank=True)
        if got != want:
            mismatches.append(f"{i.id}: replay {want}, router {got}")
    return mismatches


def conf_table(records: list[Rec]) -> dict[str, Any]:
    """Confidence and off-topic similarity per group, for the report (where the classes sit)."""
    groups: dict[str, list[tuple[float, float]]] = {}
    for rec in records:
        if rec.base is None or rec.item.source not in ("case", "offtopic_file"):
            continue
        key = rec.item.source if rec.item.source == "offtopic_file" else rec.item.case_kind
        groups.setdefault(key, []).append((rec.base.conf, rec.base.off))
    out = {}
    for key, rows in sorted(groups.items()):
        confs = [c for c, _ in rows]
        offs = [o for _, o in rows]
        out[key] = {"n": len(rows), "conf_min": round(min(confs), 3), "conf_max": round(max(confs), 3),
                    "off_min": round(min(offs), 3), "off_max": round(max(offs), 3)}
    return out


# ---------- one threshold set ----------

async def calibrate_set(set_name: str, items: list[Item], lives: LiveBook, embedder: CachedEmbedder | None,
                        log: Any) -> dict[str, Any]:
    started = time.monotonic()
    index = Index.load(embedder=embedder, cache_dir=config.STATE_DIR / "hank-index")
    file_th = copy.deepcopy(index.thresholds)
    out: dict[str, Any] = {"set": set_name, "before": {n: file_th[set_name][n] for n in (*CALIBRATED, "T_GATE")}}
    vec_of: Any = lambda _q: None  # noqa: E731
    r = Router(index)
    if set_name == "dense":
        assert embedder is not None
        passages = [d.embed_text for d in index._state.card_docs if d.passage.kind not in NOT_BY_DEFAULT]
        log(f"dense: {len(passages)} passages, {len(r.offtopic.examples)} off-topic examples, {len(items)} items")
        await embedder.prefetch(passages, "doc")
        state = await index.build_dense(timeout=EMBED_TIMEOUT_S)
        if state != "used":
            raise RuntimeError(f"dense index not complete: {state}")
        await embedder.prefetch([e.text for e in r.offtopic.examples], "query")
        if not await r.warm(embedder):
            raise RuntimeError("off-topic examples could not be embedded")
        cleaned = [clean(i.question) for i in items]
        await embedder.prefetch(cleaned, "query")
        out["embedding"] = {"model": embedder.embed_model, "requests": embedder.requests,
                            "texts_sent": embedder.texts_sent, "seconds": round(embedder.seconds, 1)}
        # dense_basis (spec 2.2): median cosine of unrelated pairs = off-topic sentences against card passages
        pvecs = [_normalize(index._vectors[d.sha]) for d in index._state.card_docs
                 if d.passage.kind not in NOT_BY_DEFAULT and d.sha in index._vectors]
        cosines = [_dot(o, p) for o in (r.offtopic.vectors or []) for p in pvecs]
        measured = round(statistics.median(cosines), 3)
        q_vecs = {c: _normalize(embedder.cached(c) or []) for c in cleaned}
        out["dense_basis"] = {"start": _basis(index), "measured": measured, "pairs": len(cosines),
                              "cos_p10": round(sorted(cosines)[len(cosines) // 10], 3),
                              "cos_p90": round(sorted(cosines)[len(cosines) * 9 // 10], 3)}
        vec_of = lambda q: q_vecs.get(q) or (_normalize(embedder.cached(q)) if embedder.cached(q) else None)  # noqa: E731
        configs = [(b, w) for b in (out["dense_basis"]["start"], measured) for w in DENSE_WEIGHTS]
    else:
        configs = [(None, file_th["weights"]["lexical"])]

    base_th = dict(file_th[set_name])
    results = []
    best: dict[str, Any] | None = None
    for basis, weights in configs:
        index.thresholds["weights"][set_name] = dict(weights)
        if basis is not None:
            index.thresholds.setdefault("dense_basis", {})[index.model] = basis
        records = build_records(r, items, lives, vec_of)
        recall = recall_of(records) or 0.0
        before = score(records, base_th, recall)
        th, s, ties = search_with_margin(records, recall, base_th, set_name == "dense")
        row = {"basis": basis, "weights": dict(weights), "recall_at_3": round(recall, 4),
               "recall_at_3_variants": round(recall_of(records, ("variant",)) or 0.0, 4),
               "before": {"thresholds": {n: base_th[n] for n in CALIBRATED}, "safety_bad": before.safety_bad,
                          "uncovered_grow": before.uncovered_grow, "accuracy": round(before.accuracy, 4),
                          "clarify_rate": round(before.clarify_rate, 4)},
               "after": {"thresholds": {n: th[n] for n in (*CALIBRATED, "M_OFF", "T_OFF_HARD") if n in th},
                         "safety_bad": s.safety_bad,
                         "uncovered_grow": s.uncovered_grow, "accuracy": round(s.accuracy, 4),
                         "clarify_rate": round(s.clarify_rate, 4), "optimal_points": ties},
               "_key": s.key(), "_records": records, "_th": th}
        log(f"{set_name} basis={basis} weights={weights}: recall@3 {recall:.3f}, accuracy before "
            f"{before.accuracy:.3f} after {s.accuracy:.3f} at {row['after']['thresholds']}")
        results.append(row)
        if best is None or row["_key"] > best["_key"]:
            best = row
    assert best is not None
    records, th = best["_records"], best["_th"]
    index.thresholds["weights"][set_name] = dict(best["weights"])
    if best["basis"] is not None:
        index.thresholds["dense_basis"][index.model] = best["basis"]
    # RRF comparison (spec 2.4): only replaces the sum if recall@3 improves by 3 points or more
    rrf_hits = []
    for rec in records:
        i = rec.item
        if i.source == "case" and i.hits and i.status != "waits" and rec.base is not None:
            live = lives.get(i.fixture, i.lang, i.ledger)
            early = r._before_search(i.question, lang_hint=i.lang, live=live, pending=None, mode="on",
                                     dense_vec=None, dense_state=None, client_id=None, aliases=None, deferred=True)
            if isinstance(early, Route):
                continue
            cleaned, lang, state = early[:3]
            found = index.search(cleaned, rec.vec, lang=lang, stage=state.stage if state else None,
                                 feed=state.feeding_view if state else None, aliases=None,
                                 dense_state="used" if rec.vec is not None else None, fusion="rrf")
            rrf_hits.append(recall_at_3(list(i.hits), [h.passage.id for h in found.hits]) or 0.0)
    rrf = sum(rrf_hits) / len(rrf_hits) if rrf_hits else None
    out.update({
        "variants": [{k: v for k, v in row.items() if not k.startswith("_")} for row in results],
        "chosen": {"basis": best["basis"], "weights": best["weights"],
                   "thresholds": {n: th[n] for n in (*CALIBRATED, "M_OFF", "T_OFF_HARD") if n in th},
                   "T_GATE": base_th["T_GATE"]},
        "recall_at_3": best["recall_at_3"], "recall_at_3_rrf": None if rrf is None else round(rrf, 4),
        "fusion": "rrf" if rrf is not None and rrf >= best["recall_at_3"] + 0.03 else "sum",
        "checks_before": checks(records, base_th), "checks_after": checks(records, th),
        "cv": cross_validate(records, best["recall_at_3"], base_th, set_name == "dense"),
        "owner_requirement": owner_feasible(records, best["recall_at_3"], th),
        "plateau": plateau(records, best["recall_at_3"], th),
        "groups": conf_table(records),
        "verify_mismatches": verify(r, records, th, set_name, lives),
        "seconds": round(time.monotonic() - started, 1),
    })
    return out


# ---------- writing ----------

def write_thresholds(results: Mapping[str, Mapping[str, Any]], path: Path, stamp: str, model: str | None) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for set_name, res in results.items():
        chosen = res["chosen"]
        data[set_name].pop("M_OFF", None)
        data[set_name].pop("T_OFF_HARD", None)
        data[set_name].update(chosen["thresholds"])
        data["weights"][set_name] = chosen["weights"]
        if set_name == "dense" and chosen["basis"] is not None and model:
            data.setdefault("dense_basis", {})[model] = chosen["basis"]
    data["about"] = ("Search fusion and route thresholds (spec 2.2 to 2.4, addendum N2). Calibrated by "
                     "scripts/hank_calibrate.py (WP11); the block calibration says when, with which model and on "
                     "what. Two threshold sets: dense (embedding channel used) and lexical (BM25 and 3-grams only). "
                     "konf = sum of weight times channel value, plus the boosts; the confidence of a search is its "
                     "best konf, at most 1. T_GATE is not calibrated: the gates read the lexical set's T_GATE only "
                     "(addendum N2: they never wait for the embedding), so the dense T_GATE has no effect.")
    data["calibration"] = {
        "date": stamp, "embed_model": model,
        "objective": "spec 2.4: safety misroutes, no_data routed grow, route accuracy, recall@3, clarify rate",
        "sets": {name: {"items": res["checks_after"]["counts"]["items"], "recall_at_3": res["recall_at_3"],
                        "accuracy": next(v["after"]["accuracy"] for v in res["variants"]
                                         if v["weights"] == res["chosen"]["weights"]
                                         and v["basis"] == res["chosen"]["basis"]),
                        "before": res["before"]}
                 for name, res in results.items()},
    }
    config.atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def markdown(result: Mapping[str, Any]) -> str:
    lines = [f"# Hank-Kalibrierung {result['started']}", "", f"Modell (Embedding): {result.get('embed_model')}", ""]
    for name, res in result["sets"].items():
        lines += [f"## Satz {name}", "",
                  f"- vorher: {res['before']}", f"- gewählt: {res['chosen']}",
                  f"- recall@3: {res['recall_at_3']} (RRF {res['recall_at_3_rrf']}), Verschmelzung {res['fusion']}",
                  f"- Abgleich mit dem echten Router: {len(res['verify_mismatches'])} Abweichungen",
                  f"- Bedingung des Owners erreichbar: {res['owner_requirement']}", ""]
        for when in ("checks_before", "checks_after"):
            c = res[when]
            lines.append(f"### {when}")
            lines.append("")
            for key, value in c.items():
                lines.append(f"- {key}: {value}")
            lines.append("")
        lines.append("### Kreuzprüfung (5 Teile)")
        lines.append("")
        for fold in res["cv"]:
            lines.append(f"- {fold}")
        lines.append("")
        lines.append("### Varianten")
        lines.append("")
        for v in res["variants"]:
            lines.append(f"- {v}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hank threshold calibration (spec 2.4, WP11)")
    parser.add_argument("--url", default=None, help="Ollama URL for the dense set (default: OLLAMA_URL, else "
                                                    "http://127.0.0.1:11434); the dense set needs it")
    parser.add_argument("--embed-model", default=None, help="embedding model (default: HANK_EMBED_MODEL, else "
                                                            "granite-embedding:278m)")
    parser.add_argument("--sets", default="lexical,dense", help="comma list of lexical, dense")
    parser.add_argument("--cache", default=None, help="vector cache file (default STATE_DIR/hank/eval/"
                                                      "embed-cache-<model>.json, also with --out: the cache is "
                                                      "no report)")
    parser.add_argument("--offline-embed", action="store_true", help="never ask Ollama, use the cache only")
    parser.add_argument("--out", default=None, help="report folder (default STATE_DIR/hank/eval)")
    parser.add_argument("--write", action="store_true", help="write vgrow/hank/data/thresholds.json")
    args = parser.parse_args(argv)
    args.sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    if any(s not in SETS for s in args.sets):
        parser.error(f"unknown set in --sets, known: {', '.join(SETS)}")
    return args


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    global TYPO_CASES
    out_dir = Path(args.out) if args.out else default_out_dir()
    model = args.embed_model or ollama.embed_model_name()
    # the cache stays in the default folder when --out points elsewhere (review of 23.10.2026: "--offline-embed
    # --out <folder>" looked for the vectors in that folder and stopped with "vectors missing")
    cache = Path(args.cache) if args.cache else default_out_dir() / f"embed-cache-{safe_name(model)}.json"
    url = args.url or ollama.ollama_url()
    TYPO_CASES = frozenset(k for k, v in report.load_expected().items() if v.get("typo"))

    def log(line: str) -> None:
        print(line, flush=True)

    result: dict[str, Any] = {"started": datetime.now().isoformat(timespec="seconds"), "embed_model": model,
                              "url": url if "dense" in args.sets else None, "sets": {}}
    items = build_items()
    log(f"{len(items)} items: " + ", ".join(f"{s} {sum(1 for i in items if i.source == s)}"
                                           for s in ("case", "variant", "gate_example", "offtopic_file")))
    embedder = CachedEmbedder(url, model, cache, offline=args.offline_embed) if "dense" in args.sets else None
    with isolated() as root:
        lives = LiveBook(root)
        for set_name in args.sets:
            result["sets"][set_name] = await calibrate_set(set_name, items, lives,
                                                           embedder if set_name == "dense" else None, log)
    stamp = result["started"].replace(":", "-")
    config.atomic_write(out_dir / f"calibration-{stamp}.json",
                        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n")
    config.atomic_write(out_dir / "calibration-latest.md", markdown(result))
    log(f"report: {out_dir / f'calibration-{stamp}.json'}")
    if args.write:
        write_thresholds(result["sets"], THRESHOLDS_FILE, result["started"][:10], model)
        log(f"written: {THRESHOLDS_FILE}")
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    result = asyncio.run(main_async(args))
    for name, res in result["sets"].items():
        print(f"\n== {name}: before {res['before']} -> chosen {res['chosen']}")
        print(f"recall@3 {res['recall_at_3']} (rrf {res['recall_at_3_rrf']}), verify mismatches "
              f"{len(res['verify_mismatches'])}")
        for when in ("checks_before", "checks_after"):
            print(f"-- {when}")
            for key, value in res[when].items():
                print(f"   {key}: {value}")
        print(f"-- owner requirement: {res['owner_requirement']}")
        print(f"-- plateau: {res['plateau']}")
        print(f"-- groups: {res['groups']}")
        for fold in res["cv"]:
            print(f"-- cv {fold}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
