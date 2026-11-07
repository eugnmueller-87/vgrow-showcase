"""Hank's gates before the search (spec 3.1 steps 3 to 8, 3.2; addendum N2, N5), pure code.

In the order of spec 3.1: injection, health with the emergency first, sale, legal interpretation, greeting and thanks;
with one change from the acceptance of 23.10.2026 (review 3, H2): the emergency check runs before every other gate,
also before the injection gate ("Ignorier die Tippfehler, mein Kind hat Dünger getrunken!" gets the emergency reply).
An emergency reply is a fixed text: it never calls the model, so moving it first costs nothing.
A gate fires when one of its patterns matches the folded question (text.fold) or when the 3-gram similarity to one of
its example sentences reaches T_GATE. Patterns and examples live in vgrow/hank/data/gates.json, the off-topic examples
in data/offtopic.json.

Two passes: the patterns of the four safety gates first, in the order of spec 3.1, then their examples in the same
order, then greeting and thanks. A pattern is the stronger evidence, an example the fallback for typos and rewording.
Safety examples are compared without the growing words (the words of the cards and of the name directory, set by the
router through set_domain()): "When can I harvest?" must not look like "Can I sell some of my harvest?".

The gates never wait for the embedding (addendum N2): they run first and only on letters. The example banks can carry
vectors as well (ExampleBank.embed), which the router uses for the off-topic similarity after the gates.

Topics that chat_policy.json blocks (THC, terpenes, taste, effect, intoxication: addendum N5) fire the health gate
with how="blocked_topic"; the caller writes no gap entry for them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import text, typo

_LOGGER = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
GATES_FILE = DATA_DIR / "gates.json"
OFFTOPIC_FILE = DATA_DIR / "offtopic.json"
POLICY_FILE = DATA_DIR / "chat_policy.json"

SAFETY_GATES = ("inject", "health", "sale", "legal")  # health includes health_emergency (spec 3.1 step 5)
SOCIAL_GATES = ("greet", "thanks")
# Name kinds that make a short text more than a greeting (spec 3.2: "kein Messgrößen-, Geräte- oder Pflanzenname").
_NAME_KINDS = frozenset({"metric", "device", "plant"})
EMBED_TIMEOUT = 120.0  # embedding the example sentences once at start; the spec gives no own limit


# ---------- small helpers ----------

def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise RuntimeError(f"Hank gates: {path} missing or broken ({err})") from err
    if not isinstance(data, dict):
        raise RuntimeError(f"Hank gates: {path} is not an object")
    return data


def _compile(patterns: Iterable[str] | None) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in patterns or [] if isinstance(p, str) and p]


def _first(patterns: Sequence[re.Pattern[str]], folded: str) -> str | None:
    for pattern in patterns:
        found = pattern.search(folded)
        if found:
            return found.group(0)
    return None


_LITERAL = re.compile(r"[a-z]{5,}")
_TOKEN = re.compile(r"[^\W\d_]{5,}")


def literals(patterns: Iterable[str]) -> frozenset[str]:
    """The plain words of regular expressions (five letters or more): r"\\w*gekaut\\b" gives "gekaut"."""
    out: set[str] = set()
    for pattern in patterns:
        out.update(_LITERAL.findall(re.sub(r"\\[a-zA-Z]", " ", pattern)))
    return frozenset(out)


MEND_MIN = 6  # gate words and question words this long or longer are mended ("fixed" is no typo of "fined")


def _bigrams(word: str) -> frozenset[str]:
    return frozenset(word[i:i + 2] for i in range(len(word) - 1))


def _near_substring(token: str, word: str) -> tuple[int, int] | None:
    """Where `token` holds `word` with at most one typo (OSA distance 1), as (start, end) in token, or None."""
    n = len(word)
    for size in (n, n - 1, n + 1):
        if size < 4 or size > len(token):
            continue
        for start in range(len(token) - size + 1):
            if typo.osa(token[start:start + size], word, limit=1) <= 1:
                return start, start + size
    return None


def gram_set(s: str) -> frozenset[str]:
    """The 3-grams of a text's words without stop words (text.grams)."""
    return frozenset(text.grams(s))


def content_words(s: str) -> tuple[str, ...]:
    """The folded words of a text without stop words, numbers and one-letter words (text.tokens, unstemmed)."""
    return tuple(w for w in text.tokens(s, stem=False) if w and not w[0].isdigit())


def stripped_grams(words_: Iterable[str], domain: frozenset[str]) -> frozenset[str]:
    """The 3-grams of the words that are not growing words: what a sentence says beyond the cards."""
    kept = [w for w in words_ if w not in domain]
    return frozenset(text.grams(" ".join(kept))) if kept else frozenset()


def dice(a: frozenset[str], b: frozenset[str]) -> float:
    """Dice coefficient of two gram sets, 0 to 1 (symmetric, so a short example does not count as found in a long
    question just because its few grams occur there)."""
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


_WORD = re.compile(r"[^\W_]+(?:['" + chr(0x2019) + r"][^\W_]+)?")  # an apostrophe joins


def words(q: str) -> list[str]:
    """The words of a question without "Hank" and without punctuation (spec 3.2, greeting rule)."""
    return [w for w in _WORD.findall(text.fold(q)) if w != "hank"]


def _normalize(vec: Iterable[float]) -> list[float]:
    values = [float(v) for v in vec]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm else values


# ---------- example banks ----------

@dataclass
class Example:
    text: str
    lang: str
    grams: frozenset[str]  # all words but stop words
    words: tuple[str, ...] = ()
    content: frozenset[str] = frozenset()  # without the growing words (set_domain)


@dataclass
class ExampleBank:
    """Example sentences of one class, compared by 3-gram Dice and, once embed() ran, by the embedding cosine."""

    name: str
    examples: list[Example] = field(default_factory=list)
    vectors: list[list[float]] | None = None
    model: str | None = None

    @classmethod
    def of(cls, name: str, raw: Mapping[str, Any] | None) -> ExampleBank:
        bank = cls(name)
        for lang, sentences in (raw or {}).items():
            for sentence in sentences or []:
                if isinstance(sentence, str) and sentence.strip():
                    grams = gram_set(sentence)
                    bank.examples.append(Example(sentence, str(lang), grams, content_words(sentence), grams))
        return bank

    def __len__(self) -> int:
        return len(self.examples)

    def set_domain(self, domain: frozenset[str]) -> None:
        for example in self.examples:
            example.content = stripped_grams(example.words, domain)

    def best(self, q_grams: frozenset[str]) -> tuple[float, str | None]:
        """The highest Dice to any example and that example."""
        top, which = 0.0, None
        for example in self.examples:
            value = dice(q_grams, example.grams)
            if value > top:
                top, which = value, example.text
        return top, which

    def best_both(self, q_grams: frozenset[str], q_content: frozenset[str]) -> tuple[float, str | None]:
        """The highest similarity that holds both with and without the growing words (the smaller of the two Dice
        values per example). With the growing words alone a question about the harvest looks like a sale example;
        without them alone a filter swap ("tauschen") does."""
        top, which = 0.0, None
        for example in self.examples:
            value = min(dice(q_grams, example.grams), dice(q_content, example.content))
            if value > top:
                top, which = value, example.text
        return top, which

    def best_dense(self, qvec: Sequence[float] | None, basis: float) -> tuple[float, str | None]:
        """The highest calibrated cosine (as the dense channel, spec 2.2), or 0 without vectors."""
        if not qvec or not self.vectors or basis >= 1:
            return 0.0, None
        q = _normalize(qvec)
        top, which = 0.0, None
        for example, vec in zip(self.examples, self.vectors):
            if len(vec) != len(q):
                continue
            cos = sum(a * b for a, b in zip(q, vec))
            value = max(0.0, min(1.0, (cos - basis) / (1 - basis)))
            if value > top:
                top, which = value, example.text
        return top, which

    def best_cosine(self, qvec: Sequence[float] | None) -> tuple[float, str | None]:
        """The highest raw cosine to any example and that example, or (0, None) without vectors (the nearest
        neighbour row of the router, WP11)."""
        if not qvec or not self.vectors:
            return 0.0, None
        q = _normalize(qvec)
        top, which = -1.0, None
        for example, vec in zip(self.examples, self.vectors):
            if len(vec) != len(q):
                continue
            cos = sum(a * b for a, b in zip(q, vec))
            if cos > top:
                top, which = cos, example.text
        return (top, which) if which is not None else (0.0, None)

    async def embed(self, embedder: Any, *, model: str | None = None, timeout: float = EMBED_TIMEOUT) -> bool:
        """Embed every example once (at start, in the background). Never raises: without vectors the bank compares
        letters only."""
        if embedder is None or not self.examples:
            return False
        try:
            call = embedder.embed([e.text for e in self.examples], kind="query", timeout=timeout)
            vectors = await asyncio.wait_for(call, timeout)
        except Exception as err:  # noqa: BLE001 (Hank is a guest: the bank stays lexical)
            _LOGGER.info("Hank gates: embedding the %s examples failed: %s", self.name, err)
            return False
        if not isinstance(vectors, list) or len(vectors) != len(self.examples):
            _LOGGER.info("Hank gates: %s vectors do not fit the %d examples", self.name, len(self.examples))
            return False
        self.vectors = [_normalize(v) for v in vectors]
        self.model = model
        return True


# ---------- the gates ----------

@dataclass(frozen=True)
class GateHit:
    """A gate that fired. gate: inject, health_emergency, health, sale, legal, greet, thanks. how: pattern, example,
    blocked_topic. detail: the matched text or the example sentence. score: 1 for a pattern, else the similarity."""

    gate: str
    how: str
    detail: str
    score: float = 1.0


@dataclass(frozen=True)
class NameRef:
    """A directory name found in the question, as the gates need it (entity id and kind)."""

    entity: str
    kind: str


class Gates:
    """The gates of spec 3.2 from gates.json, with the blocked topics of chat_policy.json."""

    def __init__(self, data: Mapping[str, Any], policy: Mapping[str, Any] | None = None) -> None:
        self.data = data
        self.max_words = int(data.get("max_words", 5))
        self.inject_patterns = _compile((data.get("inject") or {}).get("patterns"))
        emergency = data.get("health_emergency") or {}
        self.who = _compile(emergency.get("who"))
        self.ingest = _compile(emergency.get("ingest"))
        self.adult_who = _compile(emergency.get("adult_who"))
        self.adult_ingest = _compile(emergency.get("adult_ingest"))
        self.substance = _compile(emergency.get("substance"))
        self.exposure = _compile(emergency.get("exposure"))
        self.chemical = _compile(emergency.get("chemical"))
        self.risk = _compile(emergency.get("risk"))  # feeding v2: "Ist Base A giftig für mein Kind?"
        self.risk_words = tuple(text.fold(w) for w in emergency.get("risk_words") or ())  # and "gitig" (one typo)
        self.label_products = _compile(emergency.get("label_products"))  # the label says: out of reach of children
        health = data.get("health") or {}
        self.consumption = _compile(health.get("consumption"))
        self.medical = _compile(health.get("medical"))
        self.self_words = _compile(health.get("self"))
        self.dose_words = _compile(health.get("dose_words"))
        self.use_verbs = _compile(health.get("use_verbs"))
        self.cannabis_objects = _compile(health.get("cannabis_objects"))
        self.fertilizer_words = _compile(health.get("fertilizer_words"))
        self.amount = _compile(health.get("amount"))
        self.products = _compile(health.get("products"))
        self.make_verbs = _compile(health.get("make_verbs"))
        self.strain_words = _compile(health.get("strain_words"))
        sale = data.get("sale") or {}
        self.sale_strong = _compile(sale.get("strong"))
        self.sale_weak = _compile(sale.get("weak"))
        self.sale_objects = _compile(sale.get("objects"))
        self.buy = _compile(sale.get("buy"))  # buying seeds or a strain (23.10.2026): the person's own choice
        self.buy_objects = _compile(sale.get("buy_objects"))
        self.buy_not = _compile(sale.get("buy_not"))
        self.buy_past = _compile(sale.get("buy_past"))  # "bestellte Samen, wie keimen sie?" is no buy (review)
        self.buy_grow = _compile(sale.get("buy_grow"))
        self.buy_where = _compile(sale.get("buy_where"))  # where, a shop, a price: the buy reply beats the advice
        self.legal_patterns = _compile((data.get("legal") or {}).get("patterns"))
        self.greet_patterns = _compile((data.get("greet") or {}).get("patterns"))
        self.thanks_patterns = _compile((data.get("thanks") or {}).get("patterns"))
        today = data.get("today") or {}
        self.today_patterns = _compile(today.get("patterns"))
        self.today_max_words = int(today.get("max_words", 9))
        self.today_lead = frozenset(text.fold(str(w)) for w in today.get("lead") or [])
        self.today_qualifiers = [re.compile(rf"(?:^| ){p}(?= |$)") for p in today.get("qualifiers") or []
                                 if isinstance(p, str) and p]
        self.today_qualifier_names = frozenset(str(n) for n in today.get("qualifier_names") or [])
        fix = data.get("fix") or {}
        self.fix_patterns = _compile(fix.get("patterns"))
        self.fix_max_words = int(fix.get("max_words", 10))
        self.fix_fillers = frozenset(text.fold(str(w)) for w in fix.get("fillers") or [])
        self.fix_lead = frozenset(text.fold(str(w)) for w in fix.get("lead") or [])
        self.fix_bare_patterns = _compile(fix.get("bare"))
        # v1.2: whole-question fix wishes that the injection gate's example similarity must not take ("Fix
        # everything" resembles "Forget everything you were told."), in the words the fix patterns read
        self.fix_exact_forms = frozenset(" ".join(words(str(f))) for f in fix.get("exact") or [] if str(f).strip())
        # the words of the fix patterns (five letters or more), for one typo per word ("behben", "thigns", "shold")
        self.fix_vocab = frozenset(w for p in fix.get("patterns") or [] if isinstance(p, str)
                                   for w in re.findall(r"[a-z]{5,}", re.sub(r"\\[a-zA-Z]", " ", p)))
        cannabis = data.get("cannabis") or {}
        self.cannabis_entities = frozenset(str(e) for e in cannabis.get("entities") or [])
        self.cannabis_terms = _compile(cannabis.get("terms"))
        clarify = data.get("clarify") or {}
        self.yes_words = frozenset(" ".join(words(w)) for w in clarify.get("yes") or [])
        self.no_words = frozenset(" ".join(words(w)) for w in clarify.get("no") or [])
        router = data.get("router") or {}
        self.state_metrics = frozenset(str(m) for m in router.get("state_metrics") or [])
        self.box_itself = frozenset(str(m) for m in router.get("box_itself") or [])
        self.foreign_hosts = frozenset(text.fold(str(w)) for w in router.get("foreign_hosts") or [])
        self.banks = {name: ExampleBank.of(name, (data.get(name) or {}).get("examples"))
                      for name in (*SAFETY_GATES, *SOCIAL_GATES)}
        raw: list[str] = []
        for section in ("inject", "health_emergency", "health", "sale", "legal", "greet", "thanks", "cannabis"):
            for value in (data.get(section) or {}).values():
                if isinstance(value, list):
                    raw.extend(v for v in value if isinstance(v, str))
        self.lexicon = sorted((w for w in literals(raw) if len(w) >= MEND_MIN), key=len, reverse=True)
        self._lexicon_grams = [(w, _bigrams(w)) for w in self.lexicon]
        self.domain: frozenset[str] = frozenset()
        terms = ((policy or {}).get("blocked_topics") or {}).get("terms") or {}
        flat = sorted({text.fold(str(t)) for values in terms.values() for t in values or [] if str(t).strip()},
                      key=len, reverse=True)
        self.blocked_topics = re.compile(r"(?<![^\W_])(?:" + "|".join(re.escape(t) for t in flat) + r")(?![^\W_])") \
            if flat else None

    @classmethod
    def load(cls, path: Path | None = None, policy_path: Path | None = None) -> Gates:
        policy_file = policy_path or POLICY_FILE
        try:
            policy = json.loads(policy_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            _LOGGER.warning("Hank gates: %s not readable (%s), no blocked topics", policy_file, err)
            policy = {}
        return cls(_read_json(path or GATES_FILE), policy if isinstance(policy, dict) else {})

    def fuzzy(self, q: str, known: Any = None) -> str | None:
        """The folded question with typos in gate words mended ("rumgkeaut" to "rumgekaut", "Rückenshcmerzen" to
        "ruckenschmerzen", "harevst" to "harvest"), or None when nothing changed. Only words that are not known
        (`known(word)`, the name directory with the card words and everyday words) are touched, and only to a word
        of the gate patterns at distance 1 (acceptance 23.10.2026: the 11.3 gate "safety classes on the variants,
        100 %"; a typo inside a trigger word slipped past every pattern)."""
        folded = text.fold(q)
        changed = False
        out: list[str] = []
        last = 0
        for match in _TOKEN.finditer(folded):
            token = match.group(0)
            if len(token) < MEND_MIN or known is not None and known(token):
                continue
            grams = _bigrams(token)
            for word, word_grams in self._lexicon_grams:
                if len(word) > len(token) + 1 or word in token:
                    continue
                if len(word_grams & grams) < len(word_grams) - 3:  # one typo breaks at most three bigrams
                    continue
                where = _near_substring(token, word)
                if where is not None:
                    out.append(folded[last:match.start()])
                    out.append(token[:where[0]] + word + token[where[1]:])
                    last = match.end()
                    changed = True
                    break
        if not changed:
            return None
        out.append(folded[last:])
        return "".join(out)

    def set_domain(self, domain: Iterable[str]) -> None:
        """The growing words (folded words of the cards and of the name directory). Safety examples are compared
        without them from now on."""
        self.domain = frozenset(domain)
        for name in SAFETY_GATES:
            self.banks[name].set_domain(self.domain)

    # ----- the order of spec 3.1 -----

    def check(self, q: str, *, t_gate: float, names: Sequence[NameRef] = (),
              cannabis_run: bool = True) -> GateHit | None:
        """The first gate that fires (spec 3.1 steps 4 to 8), or None. names: the directory names in the question;
        cannabis_run: the run is a cannabis run (decides the sale gate's context)."""
        folded = text.fold(q)
        hit = self.emergency(folded)  # first of all (review 3, H2)
        if hit is not None:
            return hit
        for step in (self.inject, self.health, self.sale, self.legal):
            hit = step(folded, names=names, cannabis_run=cannabis_run)
            if hit is not None:
                return hit
        hit = self.examples(q, folded, t_gate=t_gate, names=names, cannabis_run=cannabis_run)
        if hit is not None:
            return hit
        return self.social(q, t_gate=t_gate, names=names)

    def examples(self, q: str, folded: str, *, t_gate: float, names: Sequence[NameRef] = (),
                 cannabis_run: bool = True) -> GateHit | None:
        """The second pass: the safety examples, similar both with and without the growing words (best_both)."""
        q_grams = gram_set(q)
        content = stripped_grams(content_words(q), self.domain) if self.domain else q_grams
        for gate in SAFETY_GATES:
            if gate == "sale" and not self.cannabis_context(folded, names, cannabis_run):
                continue
            score, example = self.banks[gate].best_both(q_grams, content)
            if example is not None and score >= t_gate:
                return GateHit(gate, "example", example, round(score, 4))
        return None

    def inject(self, folded: str, **_: Any) -> GateHit | None:
        found = _first(self.inject_patterns, folded)
        return GateHit("inject", "pattern", found) if found else None

    def emergency(self, folded: str) -> GateHit | None:
        """A child or an animal that took something in (spec 3.2, addendum N5); an adult who swallowed, drank or ate
        something from the box; a chemical in the eyes or on the skin (acceptance 23.10.2026, review 3, H1)."""
        who, ingest = _first(self.who, folded), _first(self.ingest, folded)
        if who and ingest:
            return GateHit("health_emergency", "pattern", f"{who} + {ingest}")
        adult, took = _first(self.adult_who, folded), _first(self.adult_ingest, folded)
        if adult and took:
            stuff = _first(self.substance, folded)
            if stuff:
                return GateHit("health_emergency", "pattern", f"{adult} + {took} + {stuff}")
        exposed = _first(self.exposure, folded)
        if exposed:
            stuff = _first(self.chemical, folded)
            if stuff:
                return GateHit("health_emergency", "pattern", f"{exposed} + {stuff}")
        # Feeding v2 (23.10.2026, a toddler in the household): a child or an animal, a risk word and a growing
        # substance ("Ist Base A giftig für mein Kind?", "Is BioBizz safe for my dog?") is no emergency yet, but it is
        # no growing question either: a fixed safety reply (replies.py, hank.reply.health_child), never a card.
        risk = (_first(self.risk, folded) or self._near_risk(folded)) \
            if who and not (who == "kind" and " kind of " in f" {folded} ") else None
        if risk:
            # a product whose label says KEEP OUT OF REACH OF CHILDREN first, wherever it stands, so the reply
            # carries that sentence ("Dünger probiert ... Base A", critique 23.10.2026)
            stuff = _first(self.label_products, folded) or _first(self.substance, folded)                 or _first(self.chemical, folded)
            if stuff:
                return GateHit("health", "child_risk", f"{who} + {risk} + {stuff}")
        return None

    def _near_risk(self, folded: str) -> str | None:
        """A risk word one edit away ("gitig", "toxc"): words of five letters or more only, so no short word turns
        into one (typo.osa, the same distance the typo fixer uses)."""
        for word in re.findall(r"[a-z]{5,}", folded):
            for risk in self.risk_words:
                if abs(len(word) - len(risk)) <= 1 and typo.osa(word, risk, 2) <= 1:
                    return risk
        return None

    def cannabis_context(self, folded: str, names: Sequence[NameRef], cannabis_run: bool) -> bool:
        """Spec 3.2: the question names cannabis, or it names no other plant and the run is a cannabis run."""
        if _first(self.cannabis_terms, folded):
            return True
        plants = [n.entity for n in names if n.kind == "plant"]
        if any(p in self.cannabis_entities for p in plants):
            return True
        if plants:
            return False
        return cannabis_run

    def health(self, folded: str, *, names: Sequence[NameRef] = (), cannabis_run: bool = True,
               **_: Any) -> GateHit | None:
        hit = self.emergency(folded)
        if hit:
            return hit
        if self.blocked_topics is not None:
            found = self.blocked_topics.search(folded)
            if found:
                return GateHit("health", "blocked_topic", found.group(0))
        # A device consumes water too. Exclude only this grammatical device question;
        # any remaining ingestion/medical words still go through all the usual gates.
        consumption_text = re.sub(r"\bhow much water doe?s (?:the |my |a |this )?humidifier consume\b",
                                  "humidifier water usage", folded)
        found = _first(self.consumption, consumption_text)
        if found:
            return GateHit("health", "pattern", found)
        medical = _first(self.medical, folded)
        if medical and (_first(self.self_words, folded) or _first(self.cannabis_objects, folded)
                        or _first(self.strain_words, folded)):
            return GateHit("health", "pattern", medical)
        dose = _first(self.dose_words, folded)
        if dose and _first(self.cannabis_objects, folded) and not _first(self.fertilizer_words, folded):
            return GateHit("health", "pattern", dose)
        use = _first(self.use_verbs, folded)
        if use and _first(self.cannabis_objects, folded) and self.cannabis_context(folded, names, cannabis_run):
            return GateHit("health", "pattern", use)
        amount = _first(self.amount, folded)  # "Wie viel soll ich von der Ernte nehmen?" (review 3, M3)
        if (amount and _first(self.cannabis_objects, folded) and not _first(self.fertilizer_words, folded)
                and self.cannabis_context(folded, names, cannabis_run)):
            return GateHit("health", "pattern", amount)
        product = _first(self.products, folded)  # "Wie mache ich aus der Ernte Öl?"
        if (product and _first(self.make_verbs, folded) and _first(self.cannabis_objects, folded)
                and self.cannabis_context(folded, names, cannabis_run)):
            return GateHit("health", "pattern", product)
        return None

    def sale(self, folded: str, *, names: Sequence[NameRef] = (), cannabis_run: bool = True,
             **_: Any) -> GateHit | None:
        if not self.cannabis_context(folded, names, cannabis_run):
            return None
        found = _first(self.sale_strong, folded)
        if found:
            return GateHit("sale", "pattern", found)
        weak = _first(self.sale_weak, folded)
        if weak and _first(self.sale_objects, folded):
            return GateHit("sale", "pattern", weak)
        # buying (owner, 23.10.2026: which strain and how the person buys it is the person's own choice): the reply
        # says so, without a shop, a price or a link; a legal question about it goes to the legal gate
        buying = _first(self.buy, folded)
        if buying and _first(self.buy_grow, folded):  # a past buy next to a growing question asks how to grow
            probe = folded
            for past in self.buy_past:
                probe = past.sub(" ", probe)
            buying = _first(self.buy, probe)
        if buying and _first(self.buy_objects, folded) and not _first(self.buy_not, folded) \
                and self.legal(folded) is None:
            return GateHit("sale", "buy", buying)
        return None

    def asks_where_to_buy(self, folded: str) -> bool:
        """The question asks where, for a shop or for a price ("Wo kann ich die Sorte kaufen?"): the buy reply then,
        never the strain advice (review 23.10.2026, SHOULD 2)."""
        return _first(self.buy_where, folded) is not None

    def legal(self, folded: str, **_: Any) -> GateHit | None:
        found = _first(self.legal_patterns, folded)
        return GateHit("legal", "pattern", found) if found else None

    def social(self, q: str, *, t_gate: float, names: Sequence[NameRef] = ()) -> GateHit | None:
        """Greeting or thanks (spec 3.2): short, no metric, device or plant name, a pattern or an example."""
        plain = words(q)
        if len(plain) > self.max_words or any(n.kind in _NAME_KINDS for n in names):
            return None
        if not plain:  # only "Hank" (and punctuation) is a greeting; nothing at all is the router's "empty"
            return GateHit("greet", "pattern", "hank") if "hank" in text.fold(q) else None
        joined = " ".join(plain)
        found = _first(self.thanks_patterns, joined)
        if found:
            return GateHit("thanks", "pattern", found)
        found = _first(self.greet_patterns, joined)
        if found:
            return GateHit("greet", "pattern", found)
        q_grams = gram_set(q)
        best: GateHit | None = None
        for name in SOCIAL_GATES:
            score, example = self.banks[name].best(q_grams)
            if example is not None and score >= t_gate and (best is None or score > best.score):
                best = GateHit(name, "example", example, round(score, 4))
        return best

    def today(self, q: str, *, names: Sequence[NameRef] = ()) -> str | None:
        """The today question ("Was ist heute zu tun?", "What should I do today?", data/gates.json "today"): the
        matched words, or None. Never with a directory name in the question (that one is a growing question) and never
        above max_words words. Leading greetings and fillers ("Moin", "also") are dropped, then a pattern must hold
        the whole rest ("Mein Handy ist langsam, was kann ich tun?" is none). A qualifier for the plant or the box
        ("bei der Pflanze", "beim Grow", "in der Box") is dropped first; the names it carries (qualifier_names) are
        then allowed. The router asks it after the safety gates and before greeting and thanks."""
        if not self.today_patterns:
            return None
        joined = " ".join(words(q))
        stripped = joined
        for pattern in self.today_qualifiers:
            stripped = pattern.sub(" ", stripped)
        plain = stripped.split()
        if names and (stripped == joined or any(n.entity not in self.today_qualifier_names for n in names)):
            return None  # a directory name outside a qualifier: a growing question
        if not plain or len(plain) > self.today_max_words:
            return None
        while plain and plain[0] in self.today_lead:
            plain = plain[1:]
        return _first(self.today_patterns, " ".join(plain)) if plain else None

    def fix(self, q: str, known: Any = None) -> str | None:
        """The fix wish ("Kannst du das beheben?", "Fix it", "What should I do about it?", data/gates.json "fix"): the
        matched words, or None. Filler words ("bitte", "please") are dropped anywhere, leading greetings and fillers
        ("Moin", "ok") at the start, then a pattern must hold the whole rest, so only a question that is nothing but
        the wish counts ("Wie fixiere ich den Haupttrieb?" and "What should I do about the humidity?" are growing
        questions). The router asks it after the safety gates and before the today question, greeting and thanks."""
        if not self.fix_patterns:
            return None
        plain = [w for w in words(q) if w not in self.fix_fillers]
        while plain and plain[0] in self.fix_lead:
            plain = plain[1:]
        if not plain or len(plain) > self.fix_max_words:
            return None
        found = _first(self.fix_patterns, " ".join(plain))
        if found is not None:
            return found
        # one typo per word, the owner types on a phone ("Kannst du das behben?", "can you fix those thigns?"): a word
        # of five letters or more that is no known word (`known`, the name directory with the card and everyday
        # words) and no word of the patterns is mended to a pattern word at distance 1; the pattern must still hold
        # the whole question, so a growing question never becomes the fix wish this way
        mended = [self._fix_word(w, known) for w in plain]
        return _first(self.fix_patterns, " ".join(mended)) if mended != plain else None

    def fix_exact(self, q: str) -> str | None:
        """One of the exact fix forms ("Fix everything", "Can you fix everything?", "Kannst du alles beheben?"), as the
        whole question after the fillers and the leading words, or None. The router lets such a question pass an
        injection hit by example similarity only; an injection pattern still wins (data/gates.json fix "exact")."""
        if not self.fix_exact_forms:
            return None
        plain = [w for w in words(q) if w not in self.fix_fillers]
        while plain and plain[0] in self.fix_lead:
            plain = plain[1:]
        joined = " ".join(plain)
        return joined if joined in self.fix_exact_forms else None

    def _fix_word(self, word: str, known: Any) -> str:
        if len(word) < 5 or word in self.fix_vocab or known is not None and known(word):
            return word
        near = [v for v in self.fix_vocab if abs(len(v) - len(word)) <= 1 and typo.osa(word, v, limit=1) <= 1]
        return near[0] if len(near) == 1 else word

    def fix_bare(self, wish: str) -> bool:
        """True for a fix wish that names no problem at all ("Was soll ich tun?", "What now?", data/gates.json "fix"
        "bare"): with nothing off target it asks what is to do, so core answers it with the today list. `wish` is the
        matched text gates.fix returned (the route's reason after "fix:")."""
        return bool(wish) and _first(self.fix_bare_patterns, wish) is not None

    # ----- the open "Meinst du X?" (spec 3.4) -----

    def answer_word(self, q: str) -> str | None:
        """'yes' or 'no' when the whole question is one of the clarify words, else None."""
        joined = " ".join(words(q))
        if joined in self.yes_words:
            return "yes"
        if joined in self.no_words:
            return "no"
        return None


def load_offtopic(path: Path | None = None) -> ExampleBank:
    """The off-topic examples (addendum N2: at least 60 sentences in German and English)."""
    data = _read_json(path or OFFTOPIC_FILE)
    return ExampleBank.of("offtopic", data.get("examples"))


def domain_words(index: Any) -> frozenset[str]:
    """The growing words of an index: the folded words of its card passages (titles and texts, both languages)
    and of its name directory. Live, tool and own-experience passages do not count."""
    out: set[str] = set()
    for passage in getattr(index, "passages", []) or []:
        # v1.4: nor do the method cards, which are searched only when a question names one (kartei_index.OPT_IN_KINDS):
        # their long texts ("everything", "instructions") took words the injection examples are compared by
        if passage.kind in ("live", "tool", "experience", "methods"):
            continue
        for value in (*passage.title.values(), *passage.text.values()):
            out.update(content_words(str(value)))
    vocab = getattr(index, "vocab", None)
    for entry in (getattr(vocab, "entries", None) or {}).values():
        for lang in ("de", "en"):
            names = entry.get(lang) or []
            for name in [names] if isinstance(names, str) else names:
                if isinstance(name, str):
                    out.update(content_words(name))
    return frozenset(out)
