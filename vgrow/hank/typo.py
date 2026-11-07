"""Names and typos (spec 2.5, addendum N2): OSA distance, the name directory, and the decision table.

The name directory (kartei/_hank/vocab.json, WP3) lists plant, product, device, metric, pest, disease, process,
stage and term names in German and English. It is not a word list for "is this about growing": it serves typos,
names, and "do I know the name but have no card?".

Decision table (spec 2.5 with the addendum N2 definition of a borderline case), on the folded word:

| length | auto (silent fix)                    | ask ("Meinst du X?")                                   |
|--------|--------------------------------------|--------------------------------------------------------|
| < 4    | never                                | never                                                  |
| 4      | only a learned alias                 | never                                                  |
| 5 to 8 | distance 1, one candidate            | distance 1 with several candidates, distance 2         |
| >= 9   | distance 1, one candidate            | distance 1 with several candidates, distance 2         |

An error at the first letter counts double, so distance 1 there is a borderline case (ask) and distance 2 there
is out of reach. A word that, folded and stemmed, already is a word of the directory, of the card file (passed in
as `words`), a stop word or a common everyday word is never a typo. Learned aliases are read before the typo
match; a rejected pair is never proposed again.

Pure Python: candidates come from a delete-neighbourhood index (every form with up to two letters removed), so a
lookup checks a handful of forms instead of all of them.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vgrow.hank import text
from vgrow.hank.types import Correction

_LOGGER = logging.getLogger(__name__)

VOCAB_FILE = Path(__file__).resolve().parent.parent.parent / "kartei" / "_hank" / "vocab.json"
MIN_LEN = 4  # shorter words are never corrected
ALIAS_ONLY_LEN = 4  # words of this length: learned alias only
MAX_DISTANCE = 2  # the largest effective distance that is still asked about
MIN_FORM_LEN = 4  # directory forms shorter than this (VPD, rF, CO2) match only exactly
MAX_PHRASE = 6  # longest name in words that the scanner tries

_GROUP_RE = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*(?:-[^\W_]+(?:['\u2019][^\W_]+)*)*")


def osa(a: str, b: str, limit: int = 3) -> int:
    """Optimal string alignment distance (Damerau with adjacent transpositions, each substring edited once).
    Returns the distance if it is at most `limit`, else limit + 1."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > limit:
        return limit + 1
    if not la or not lb:
        return max(la, lb) if max(la, lb) <= limit else limit + 1
    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        ai = a[i - 1]
        cur = [i] + [0] * lb
        row_min = i
        for j in range(1, lb + 1):
            bj = b[j - 1]
            value = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ai != bj))
            if i > 1 and j > 1 and ai == b[j - 2] and a[i - 2] == bj and prev2[j - 2] + 1 < value:
                value = prev2[j - 2] + 1
            cur[j] = value
            if value < row_min:
                row_min = value
        if row_min > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[lb] if prev[lb] <= limit else limit + 1


def decide(length: int, effective: int, ambiguous: bool) -> str | None:
    """The decision table of spec 2.5 for one fuzzy candidate: "auto", "ask" or None (no correction).
    `effective` is the OSA distance plus one when the first letter differs. Learned aliases do not come here."""
    if length <= ALIAS_ONLY_LEN or effective < 1 or effective > MAX_DISTANCE:
        return None
    if effective == 1 and not ambiguous:
        return "auto"
    return "ask"


def compact(word: str) -> str:
    """The folded form used for matching: fold() without hyphens, apostrophes and spaces."""
    return re.sub(r"[\s\-'\u2019]+", "", text.fold(word))


def _deletes(word: str, depth: int) -> set[str]:
    out = {word}
    frontier = {word}
    for _ in range(depth):
        frontier = {w[:i] + w[i + 1:] for w in frontier for i in range(len(w))}
        out |= frontier
    return out


@dataclass(frozen=True)
class Form:
    """One spelling of a directory entry: the surface as written, the entry id and kind, its order."""

    surface: str
    entity: str
    kind: str
    order: int


class Vocab:
    """The name directory plus the words that are known not to be typos.

    entries: dicts in the vocab.json form {"id", "kind", "de": [...], "en": [...], optional "maps_to", "split"}.
    words: extra known words, usually every word of the card file (WP4 passes them; then "Tomaten" or "duenge"
    are never typos). Stop words and text.COMMON are always known."""

    def __init__(self, entries: Iterable[Mapping[str, Any]] = (), *, words: Iterable[str] = ()) -> None:
        self.entries: dict[str, Mapping[str, Any]] = {}
        self.splits: dict[str, list[str]] = {}
        self._exact: dict[str, list[Form]] = {}  # compact form -> forms
        self._stems: dict[str, dict[str, list[Form]]] = {"de": {}, "en": {}}  # stem of compact form -> forms
        self._index: dict[str, dict[str, set[str]]] = {"raw": {}, "stem": {}}  # delete key -> compact keys
        self._stem_keys: dict[str, list[str]] = {}  # stem key -> compact forms it came from
        self._known: set[str] = set()
        self._known_stems: set[str] = set()
        self._order = 0
        self.max_phrase = 1
        self.add_words(text.STOP)
        self.add_words(text.COMMON)
        for entry in entries:
            self.add(entry)
        self.add_words(words)

    # ---------- building ----------

    @classmethod
    def load(cls, path: Path | str | None = None, *, words: Iterable[str] = ()) -> Vocab:
        """Read vocab.json (default kartei/_hank/vocab.json). A missing or broken file gives an empty directory
        and a log line: Hank then has no names, the cockpit is unaffected."""
        target = Path(path) if path is not None else VOCAB_FILE
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            entries = data.get("entries", []) if isinstance(data, dict) else []
        except (OSError, ValueError) as err:
            _LOGGER.warning("Hank name directory %s not readable (%s), continuing without names", target, err)
            entries = []
        return cls([e for e in entries if isinstance(e, Mapping)], words=words)

    def add(self, entry: Mapping[str, Any]) -> None:
        """Add one entry, also at run time (device labels from ports.json). An entry without id is skipped."""
        entity = str(entry.get("id") or "").strip()
        if not entity:
            return
        if entity not in self.entries:
            self.entries[entity] = entry
        kind = str(entry.get("kind") or "")
        for lang in ("de", "en"):
            names = entry.get(lang) or []
            for surface in [names] if isinstance(names, str) else names:
                if isinstance(surface, str) and surface.strip():
                    self._add_form(surface.strip(), entity, kind)
        split = entry.get("split")
        if isinstance(split, Mapping):
            for word, parts in split.items():
                if isinstance(parts, list):
                    self.splits[compact(str(word))] = [compact(str(p)) for p in parts if str(p).strip()]

    def _add_form(self, surface: str, entity: str, kind: str) -> None:
        key = compact(surface)
        if not key:
            return
        bucket = self._exact.setdefault(key, [])
        if any(form.entity == entity for form in bucket):
            return
        form = Form(surface, entity, kind, self._order)
        self._order += 1
        bucket.append(form)
        self.max_phrase = min(MAX_PHRASE, max(self.max_phrase, len(_GROUP_RE.findall(surface.replace("-", " ")))))
        for word in text.tokens(surface, stem=False, stop=False):
            self._learn_word(word)
        if len(key) >= MIN_FORM_LEN and key.isalpha():
            for lang in ("de", "en"):
                self._stems[lang].setdefault(text.stem_word(key, lang), []).append(form)
            self._index_key("raw", key)
            stem_key = text.stem_word(key, "de")
            self._stem_keys.setdefault(stem_key, [])
            if key not in self._stem_keys[stem_key]:
                self._stem_keys[stem_key].append(key)
            self._index_key("stem", stem_key)

    def _index_key(self, table: str, key: str) -> None:
        index = self._index[table]
        for deleted in _deletes(key, MAX_DISTANCE):
            index.setdefault(deleted, set()).add(key)

    def _learn_word(self, word: str) -> None:
        self._known.add(word)
        for lang in ("de", "en"):
            self._known_stems.add(text.stem_word(word, lang))

    def add_words(self, words: Iterable[str]) -> None:
        """Mark words as known (never typos). Words may be raw text; they are folded and split here."""
        for item in words:
            for word in text.tokens(str(item), stem=False, stop=False):
                if not word[0].isdigit():
                    self._learn_word(word)

    # ---------- lookups ----------

    def __len__(self) -> int:
        return sum(len(forms) for forms in self._exact.values())

    def entry(self, entity: str) -> Mapping[str, Any] | None:
        return self.entries.get(entity)

    def names(self, entity: str) -> list[str]:
        """All spellings of an entry in both languages, German first (for query expansion, spec 2.2)."""
        entry = self.entries.get(entity) or {}
        out: list[str] = []
        for lang in ("de", "en"):
            names = entry.get(lang) or []
            for name in [names] if isinstance(names, str) else names:
                if isinstance(name, str) and name.strip() and name.strip() not in out:
                    out.append(name.strip())
        return out

    def lookup(self, phrase: str) -> list[Form]:
        """Exact match of a word or phrase, folded, inflections allowed through the stemmer
        ("Tomaten" finds Tomate). Short forms (under four letters) must match exactly."""
        key = compact(phrase)
        if not key:
            return []
        if key in self._exact:
            return list(self._exact[key])
        if len(key) < MIN_LEN or not key.isalpha():
            return []
        for lang in ("de", "en"):
            forms = self._stems[lang].get(text.stem_word(key, lang))
            if forms:
                return list(forms)
        return []

    def is_known(self, word: str) -> bool:
        """True if the word, folded and stemmed, is a directory word, a card-file word, a stop word or common."""
        key = compact(word)
        if not key:
            return True
        if key in self._known or key in self._exact:
            return True
        return any(text.stem_word(key, lang) in self._known_stems for lang in ("de", "en"))

    def candidates(self, word: str) -> list[tuple[int, int, Form]]:
        """Fuzzy candidates as (effective distance, OSA distance, form), best first, effective distance at most
        MAX_DISTANCE. Effective distance adds one when the first letter differs. The folded word is compared with
        the forms, and its stem (if it has five letters or more) with the stems of the forms, there only at
        effective distance 1 ("Tomahten" finds Tomate); the smaller distance counts, and on a tie a match of the
        whole word ranks before a match of the stem."""
        key = compact(word)
        best: dict[str, tuple[int, int, int]] = {}  # compact form -> (effective, raw, 0 = word, 1 = stem)
        for path, (table, probe) in enumerate((("raw", key), ("stem", text.stem_word(key, "de")))):
            if table == "stem" and (len(probe) <= ALIAS_ONLY_LEN or probe == key):
                continue  # a stem of four letters or fewer is as short as a word that is never fuzzy-matched
            index = self._index[table]
            seen: set[str] = set()
            for deleted in _deletes(probe, MAX_DISTANCE):
                seen |= index.get(deleted, set())
            for cand in seen:
                raw = osa(probe, cand, MAX_DISTANCE)
                if raw > MAX_DISTANCE:
                    continue
                effective = raw + (probe[:1] != cand[:1])
                if effective > (1 if table == "stem" else MAX_DISTANCE):
                    continue  # stems are short: only a plain one-letter slip counts there
                forms_of = [cand] if table == "raw" else self._stem_keys.get(cand, [])
                for form_key in forms_of:
                    if form_key not in best or (effective, raw, path) < best[form_key]:
                        best[form_key] = (effective, raw, path)
        ranked = sorted(((eff, raw, path, form) for form_key, (eff, raw, path) in best.items()
                         for form in self._exact.get(form_key, [])),
                        key=lambda item: (item[0], item[1], item[2], item[3].order))
        return [(eff, raw, form) for eff, raw, _, form in ranked]


# ---------- aliases ----------

def _alias_tables(aliases: Mapping[str, Any] | None) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Learned and rejected spellings, from either a plain {wrong: term} map or
    {"learned": {wrong: term | {"term": term, ...}}, "rejected": {wrong: [term, ...]} | [[wrong, term], ...]}."""
    learned: dict[str, str] = {}
    rejected: dict[str, set[str]] = {}
    if not aliases:
        return learned, rejected
    if "learned" in aliases or "rejected" in aliases:
        raw_learned = aliases.get("learned") or {}
        raw_rejected = aliases.get("rejected") or {}
    else:
        raw_learned, raw_rejected = aliases, {}
    if isinstance(raw_learned, Mapping):
        for wrong, value in raw_learned.items():
            term = value.get("term") if isinstance(value, Mapping) else value
            if isinstance(term, str) and term.strip():
                learned[compact(str(wrong))] = term.strip()
    pairs: list[tuple[str, str]] = []
    if isinstance(raw_rejected, Mapping):
        for wrong, terms in raw_rejected.items():
            for term in [terms] if isinstance(terms, str) else terms or []:
                pairs.append((str(wrong), str(term)))
    elif isinstance(raw_rejected, list):
        for item in raw_rejected:
            if isinstance(item, Mapping):
                pairs.append((str(item.get("token", "")), str(item.get("term", ""))))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((str(item[0]), str(item[1])))
    for wrong, term in pairs:
        if wrong and term:
            rejected.setdefault(compact(wrong), set()).add(compact(term))
    return learned, rejected


def _as_vocab(vocab: Vocab | Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> Vocab:
    if isinstance(vocab, Vocab):
        return vocab
    if isinstance(vocab, Mapping):
        return Vocab(vocab.get("entries", []))
    return Vocab(vocab)


# ---------- correction ----------

def correct(token: str, vocab: Vocab | Mapping[str, Any] | Iterable[Mapping[str, Any]],
            aliases: Mapping[str, Any] | None = None) -> Correction | None:
    """Correct one question word against the name directory, or None if it needs no correction.

    Order: too short -> None; an exact directory form (also inflected) -> None; a learned alias -> auto;
    a known word -> None; four letters -> None; else the fuzzy decision table (see decide())."""
    voc = _as_vocab(vocab)
    key = compact(token)
    if len(key) < MIN_LEN or not key.isalpha():
        return None
    if voc.lookup(key):
        return None
    learned, rejected = _alias_tables(aliases)
    if key in learned:
        forms = voc.lookup(learned[key])
        if forms:
            return Correction(token, forms[0].surface, forms[0].entity, 0, "auto")
    if voc.is_known(key) or len(key) <= ALIAS_ONLY_LEN:
        return None
    refused = rejected.get(key, set())
    cands = [c for c in voc.candidates(key) if compact(c[2].surface) not in refused
             and c[2].entity not in {f.entity for r in refused for f in voc.lookup(r)}]
    if not cands:
        return None
    best_eff = cands[0][0]
    top = [c for c in cands if c[0] == best_eff]
    entities = {c[2].entity for c in top}
    mode = decide(len(key), best_eff, len(entities) > 1)
    if mode is None:
        return None
    eff, raw, form = top[0]
    return Correction(token, form.surface, form.entity, raw, mode)


# ---------- directory match over a whole question ----------

@dataclass(frozen=True)
class Mention:
    """A directory name found in a question. `how`: exact, alias or auto (a silent fix). start/end are character
    offsets in the original question."""

    entity: str
    kind: str
    term: str
    surface: str
    start: int
    end: int
    how: str


@dataclass
class VocabMatch:
    mentions: list[Mention] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)  # auto and ask, in question order
    corrected: str = ""  # the question with auto fixes and aliases put in

    def entities(self) -> list[str]:
        """Entry ids of all mentions (exact, alias, auto), first occurrence order, no duplicates."""
        out: list[str] = []
        for mention in self.mentions:
            if mention.entity not in out:
                out.append(mention.entity)
        return out

    def asks(self) -> list[Correction]:
        return [c for c in self.corrections if c.mode == "ask"]


@dataclass
class _Atom:
    word: str  # compact folded
    start: int
    end: int
    group: int
    joinable: bool  # only whitespace or a hyphen between this atom and the next


def _atoms(q: str) -> list[_Atom]:
    atoms: list[_Atom] = []
    for number, match in enumerate(_GROUP_RE.finditer(q)):
        pos = match.start()
        for part in match.group(0).split("-"):
            if part:
                atoms.append(_Atom(compact(part), pos, pos + len(part), number, True))
            pos += len(part) + 1
    for i, atom in enumerate(atoms[:-1]):
        gap = q[atom.end:atoms[i + 1].start]
        atom.joinable = gap.strip() in ("", "-")
    if atoms:
        atoms[-1].joinable = False
    return atoms


def scan(q: str, vocab: Vocab | Mapping[str, Any] | Iterable[Mapping[str, Any]],
         aliases: Mapping[str, Any] | None = None) -> VocabMatch:
    """Find directory names in a question (the "Verzeichnis-Abgleich" of spec 3.3 step 2).

    Phrases are matched longest first over words joined across spaces and hyphens ("Light Mix", "Light-Mix" and
    "Lightmix" all find Light-Mix; "Light-Mix-Erde" finds it too). Words left over are typo-checked one by one,
    a hyphen word also as a whole ("Bio-Blom")."""
    voc = _as_vocab(vocab)
    atoms = _atoms(q)
    learned, _ = _alias_tables(aliases)
    found: list[Mention] = []
    used = [False] * len(atoms)
    i = 0
    while i < len(atoms):
        hit = None
        for n in range(min(voc.max_phrase, len(atoms) - i), 0, -1):
            if n > 1 and not all(atoms[k].joinable for k in range(i, i + n - 1)):
                continue
            forms = voc.lookup("".join(a.word for a in atoms[i:i + n]))
            if forms:
                hit = (n, forms)
                break
        if hit:
            n, forms = hit
            start, end = atoms[i].start, atoms[i + n - 1].end
            for form in forms:
                found.append(Mention(form.entity, form.kind, form.surface, q[start:end], start, end, "exact"))
            for k in range(i, i + n):
                used[k] = True
            i += n
        else:
            i += 1
    corrections: list[tuple[int, int, Correction]] = []
    groups: dict[int, list[int]] = {}
    for k, atom in enumerate(atoms):
        groups.setdefault(atom.group, []).append(k)
    for members in groups.values():
        free = [k for k in members if not used[k]]
        if not free:
            continue
        if len(members) > 1 and len(free) == len(members):
            start, end = atoms[members[0]].start, atoms[members[-1]].end
            whole = correct(q[start:end], voc, aliases)
            if whole:
                corrections.append((start, end, whole))
                continue
        for k in free:
            atom = atoms[k]
            fix = correct(q[atom.start:atom.end], voc, aliases)
            if fix:
                corrections.append((atom.start, atom.end, fix))
    corrections.sort(key=lambda item: item[0])
    for start, end, fix in corrections:
        if fix.mode == "auto":
            how = "alias" if compact(fix.token) in learned else "auto"
            kind = str((voc.entry(fix.entity) or {}).get("kind") or "")
            found.append(Mention(fix.entity, kind, fix.term, q[start:end], start, end, how))
    found.sort(key=lambda m: m.start)
    corrected = q
    for start, end, fix in reversed(corrections):
        if fix.mode == "auto":
            corrected = corrected[:start] + fix.term + corrected[end:]
    return VocabMatch(found, [fix for _, _, fix in corrections], corrected)


def substitute(q: str, correction: Correction) -> str:
    """Put the term of a confirmed correction into the question (the "Ja" path of spec 3.4): every occurrence
    of the corrected word as a whole word, case-insensitive."""
    pattern = re.compile(rf"(?<![^\W_]){re.escape(correction.token)}(?![^\W_])", re.IGNORECASE)
    return pattern.sub(correction.term, q)
