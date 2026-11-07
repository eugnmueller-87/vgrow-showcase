"""The sheet Hank answers from (spec 4.1 to 4.6, addendum N0, N1, N3, N4, N5): state, verdicts, facts, ledger.

build() turns a routed question plus the live state into a Context: the verdicts the code sets (only for the
current stage and the metrics the question is about), the facts (card passages from the search, then computed
facts: feeding, automation, cockpit rules, forecast), the ledger notes and the proposals. The same Context feeds the
draft (compose.py, the answer under addendum N0) and the model prompt (prompt()), so draft and model say the same.

The prompt has a fixed start: the system text is byte-identical in every call of a language, the state block comes
first in the user message and changes only with the readings (spec 4.1). The whole input stays within LIMIT_TOKENS
(addendum N0: 500); trimming drops, in this order, the weakest facts (down to two), the ledger, the forecast, then
state groups the question does not touch (spec 4.6). The system text and the verdicts are never cut. When it still
does not fit, Context.fits is False and the draft is the answer.

Facts carry markers F1, F2, ...; the state groups S1 to S9 count like facts (addendum N1). Source ids in card texts
("(S5)") stay in Fact.text; the prompt strips them (they would clash with the [S1] markers), the draft turns them
into numbered references.

Not decided here, and kept as the caller's: what the ledger does with the notes (WP7), which proposals exist (WP7
builds them, they come in as `proposals`), the route (WP5) and the passages (WP4).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from vgrow import automation, plant
from vgrow.i18n import fmt_qty, fmt_time, localize, t

from . import claims, feedplan, pump_report, slots as slots_mod, text, verdicts
from .livestate import GROUPS, MARKERS, Group, LiveState, collect, day_text, minutes
from .livestate import reading as livestate_reading
from .types import Fact, Hit, Num, Passage, Proposal, Route, SourceRef

_LOGGER = logging.getLogger(__name__)

LIMIT_TOKENS = 500  # addendum N0: system about 150, state about 150, facts about 150, question about 50
MAX_FACTS = 5  # spec 4.4: up to five passages
MIN_FACTS = 2  # spec 4.6: trimming keeps at least two facts
DRAFT_PASSAGES = 3  # passages the draft states (start value; grow-04 and grow-07 expect three)
LIGHT_STEP = 25  # server.py plant_apply: at most +25 points per tap (own safety rule, no source)
LIGHT_FIRST = 50  # server.py plant_apply: from 0 the first tap goes to 50
# Trim order of the state groups (spec 4.6 "einzeilige Gruppen im Stand"); age and stage always stay.
GROUP_TRIM = ("camera", "actions", "fans", "ports", "light", "automation", "climate")
VOCAB_FILE = plant.LIBRARY / "_hank" / "vocab.json"

# Which metrics a device stands for (verdicts shown for it: only the ones off target).
DEVICE_METRICS: dict[str, tuple[str, ...]] = {
    "humidifier": ("probe_humidity",), "automation": ("probe_humidity",), "dehumidifier": ("probe_humidity",),
    "exhaust_fan": ("probe_temp", "probe_humidity", "probe_vpd"), "probe": ("probe_temp", "probe_humidity", "probe_vpd"),
    "lamp": ("light_percent",),
}
# Questions for an amount ("wie viel", ml, Menge): only these get the dose sentences (finding 10: "Wann dünge ich
# das nächste Mal?" got the dose factor, "Nie mehr als die Tabelle" and the card-draft notice). Feeding v2: grams and
# spoons are amounts too ("Wie viel Gramm Base A?", "Wie viele Löffel?", "how many spoons").
_AMOUNT_RE = re.compile(r"\b(wie ?viel\w*|menge\w*|ml|milliliter\w*|dosis|dosier\w*|how (much|many)|amount\w*|"
                        r"doses?|dosage|gramm|grams?|g|g/l|messloffel\w*|loffel\w*|spoons?|scoops?)\b")
# The question asks whether the readings or the box are stuck ("Die Werte bewegen sich nicht mehr. Ist die Box
# hinüber?"): with fresh readings the answer is that they come in.
_STUCK_RE = re.compile(r"\b(beweg\w*|aktualisier\w*|hangt|hangen|kaputt\w*|hinuber|eingefroren|tot|stuck|frozen|"
                       r"broken|updating|dead)\b")
# A question about a device's state ("Läuft der Lüfter?", "Ist die Lampe an?", "Is the camera on?") is answered by
# that device's line of the live state, not by a card sentence that happens to share a word (live test 23.10.2026:
# "Läuft der Lüfter?" opened with the strain card's "Braucht gute Lüftung gegen Schimmel"). Only status wordings:
# "Wie hoch soll die Lampe hängen?" stays with the card. The humidifier keeps the automation's reason.
_DEVICE_GROUPS = {"circulation_fan": "fans", "exhaust_fan": "fans", "lamp": "light", "growcam": "camera",
                  "pump": "ports"}
_STATUS_RE = re.compile(r"\b(lauft|laufen|eingeschaltet|ausgeschaltet|angeschaltet|abgeschaltet|funktioniert|arbeitet|"
                        r"running|working|switched on|turned on|status|zustand)\b"
                        r"|\b(ist|sind|is|are)\b[^?]*\b(an|aus|on|off)\W*$")
# Which state groups a metric or device needs in the prompt.
METRIC_GROUPS = {"probe_humidity": "climate", "probe_temp": "climate", "probe_vpd": "climate", "humidity": "climate",
                 "temp": "climate", "vpd": "climate", "water_level": "climate", "room_temp": "climate",
                 "room_humidity": "climate", "light_percent": "light", "spectrum": "light", "cfan_step": "fans",
                 "dfan_step": "fans", "sensors_age": "age"}
DEVICE_GROUPS = {"humidifier": ("automation", "ports", "climate"), "automation": ("automation", "climate"),
                 "exhaust_fan": ("fans", "climate"), "circulation_fan": ("fans",), "lamp": ("light",),
                 "growcam": ("camera",), "usb_port": ("ports",), "probe": ("climate",),
                 "water_tank": ("climate",), "pump": ("actions", "ports")}
# The device a ledger recommendation is about, by its proposal action.
ACTION_DEVICES = {"dfan": "exhaust_fan", "cfan": "circulation_fan", "light": "lamp", "auto_humidifier": "automation"}

_NOW_RE = re.compile(r"\b(jetzt|gerade|aktuell|momentan|derzeit|im moment|zur zeit|heute|diese woche|now|right now|"
                     r"currently|at the moment|today|this week|nachste(s)? mal|next time)\b")
_TIMING_RE = re.compile(r"\b(wann|wie lange|wie viele wochen|wie viele tage|how long|when|how many weeks|"
                        r"how many days)\b")
_HARVEST_RE = re.compile(r"ernte|harvest|blu|bloom|flower|reif|ripe")
# "Wann dünge ich das nächste Mal?" asks for the rhythm too (acceptance 23.10.2026): the card's frequency note.
_FREQ_RE = re.compile(r"\b(wie oft|how often|takt|rhythmus|intervall|interval|frequency|nachste(s)? mal|next time|"
                      r"wann wieder|when again)\b")
_FEED_RE = re.compile(r"dung|nahrstoff|nutrient|fertili|feed|dosis|dose|\bml\b|\bgramm\b|\bgrams?\b|loffel|"
                      r"\bspoons?\b|\bbase [ab]\b|vivosun")
_ID_RE = r"[A-Z]{1,3}\d{1,3}"
_PURE_IDS = re.compile(rf"\s*\(\s*({_ID_RE}(?:\s*,\s*{_ID_RE})*)\s*\)")
_TRAILING_ID = re.compile(rf",\s*({_ID_RE})\s*\)")


# ---------- names in the question ----------

@dataclass
class Mentions:
    """What the question names, from the name register (kartei/_hank/vocab.json) and a few fixed words."""

    ids: set[str] = field(default_factory=set)  # vocab ids
    metrics: set[str] = field(default_factory=set)  # check metrics (maps_to of metric entries)
    devices: set[str] = field(default_factory=set)  # vocab ids of devices, plus port keys (usb2)
    stages: set[str] = field(default_factory=set)  # stage keys (maps_to of stage entries)
    pages: set[str] = field(default_factory=set)  # nutrient pages (light_mix, all_mix)
    products: set[str] = field(default_factory=set)
    plants: set[str] = field(default_factory=set)  # vocab ids of plants ("basil"): a question about another crop
    now: bool = False
    timing: bool = False
    frequency: bool = False
    feeding: bool = False
    harvest: bool = False


@lru_cache(maxsize=2)
def _vocab_cached(path: str, mtime: float) -> tuple[tuple[dict[str, Any], tuple[tuple[str, tuple[str, ...]], ...]], ...]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        _LOGGER.error("vocab unreadable (%s): no names matched", err)
        return ()
    out = []
    for entry in data.get("entries", []):
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        forms = []
        for lang in ("de", "en"):
            for form in entry.get(lang) or []:
                toks = tuple(text.tokens(str(form), lang=lang))
                if toks:
                    forms.append((lang, toks))
        out.append((entry, tuple(forms)))
    return tuple(out)


def vocab(path: Path = VOCAB_FILE) -> tuple[tuple[dict[str, Any], tuple[tuple[str, tuple[str, ...]], ...]], ...]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    return _vocab_cached(str(path), mtime)


def _contains(seq: list[str], part: tuple[str, ...]) -> bool:
    n = len(part)
    return any(tuple(seq[i:i + n]) == part for i in range(len(seq) - n + 1))


def _take(seq: list[str], part: tuple[str, ...], used: set[int]) -> bool:
    """Match part in seq on positions no longer name took before ("Light-Mix" is a product, its "light" is no
    metric); mark the positions as used."""
    n = len(part)
    for i in range(len(seq) - n + 1):
        if tuple(seq[i:i + n]) == part and not used & set(range(i, i + n)):
            used.update(range(i, i + n))
            return True
    return False


# The products whose name makes a question a feeding question: both nutrient cards (feeding v2: VIVOSUN Base A/B).
FEED_PRODUCTS = frozenset({"biobizz", "bio_bloom", "bio_grow", "top_max", "alg_a_mic", "fertilizer",
                           "organic_fertilizer", "vivosun", "vivosun_base", "base_a", "base_b"})


def mentions(question: str, lang: str, *, ports: Mapping[str, Any] | None = None,
             corrections: Iterable[Any] = (), entities: Iterable[str] = (), vocab_path: Path = VOCAB_FILE) -> Mentions:
    """The names a question mentions. Each form is matched as a sequence of folded, stemmed search tokens, in its own
    language, longest names first; corrections from the typo matcher (Correction.entity) and the slots' entities
    (Slots.entities, WP4) count as mentioned."""
    out = Mentions()
    toks = {"de": text.tokens(question, lang="de"), "en": text.tokens(question, lang="en")}
    found: set[str] = {str(c.entity) for c in corrections if getattr(c, "entity", None)}
    found.update(str(e) for e in entities if e)
    entries = vocab(vocab_path)
    by_id = {entry["id"]: entry for entry, _ in entries}
    used: dict[str, set[int]] = {"de": set(), "en": set()}
    ranked = sorted(((len(form), lang, form, entry["id"]) for entry, forms in entries for lang, form in forms),
                    key=lambda item: -item[0])  # longest names first
    matched: dict[str, list[tuple[str, ...]]] = {}
    for _, lang, form, vid in ranked:
        if _take(toks[lang], form, used[lang]):
            found.add(vid)
            matched.setdefault(vid, []).append(form)
    if slots_mod.other_object(question):  # "Ist die Erde zu feucht?": "feucht" names no metric here (finding 6)
        for vid, forms in matched.items():
            entry = by_id.get(vid) or {}
            if entry.get("kind") == "metric" and all(len(f) == 1 and f[0] in slots_mod.ADJECTIVES for f in forms):
                found.discard(vid)
    for vid in found:
        entry = by_id.get(vid)
        if not entry:
            continue
        kind, target = entry.get("kind"), entry.get("maps_to")
        out.ids.add(vid)
        if kind == "metric" and target:
            out.metrics.add(str(target))
        elif kind == "device":
            out.devices.add(vid)
        elif kind == "stage" and target:
            out.stages.add(str(target))
        elif kind == "plant":
            out.plants.add(vid)
        elif kind == "product":
            out.products.add(vid)
            if target in ("light_mix", "all_mix"):
                out.pages.add(str(target))
        elif kind == "process" and vid in ("feeding", "flushing"):
            out.feeding = True
    for port, rule in (ports or {}).items():
        names = [str(v) for k, v in (rule or {}).items() if k in ("label", "label_en") and v]
        for name in names:
            for lang in ("de", "en"):
                form = tuple(text.tokens(name, lang=lang))
                if form and _contains(toks[lang], form):
                    out.devices.add(str(port))
    folded = text.fold(question)
    out.now = bool(_NOW_RE.search(folded))
    out.timing = bool(_TIMING_RE.search(folded))
    out.frequency = bool(_FREQ_RE.search(folded))
    out.harvest = bool(_HARVEST_RE.search(folded)) or bool(out.ids & {"harvest", "harvest_window", "trichome"})
    out.feeding = out.feeding or bool(out.products & FEED_PRODUCTS) or bool(_FEED_RE.search(folded))
    return out


# ---------- sources ----------

def _split_source(raw: Any, lang: str) -> tuple[str, str | None]:
    """A card source as (title, url): "Title: https://..." (split at the last ": http") or {title, title_en, url}."""
    if isinstance(raw, dict):
        shown = localize(raw, lang)
        return str(shown.get("title") or ""), (str(shown["url"]) if shown.get("url") else None)
    value = str(raw)
    cut = value.rfind(": http")
    if cut < 0:
        return value, None
    return value[:cut].strip(), value[cut + 2:].strip()


class SourceBook:
    """Resolves source ids per card (S ids collide between cards, spec 2.1) to SourceRef. A link to a blocked host
    (seed shops, decision D3) is dropped, the source stays as text. Unknown ids are left out and logged."""

    def __init__(self, library: Path = plant.LIBRARY, run_file: Path = plant.PLANT_FILE,
                 extra: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.library, self.run_file = library, run_file
        self.extra = {k: dict(v) for k, v in (extra or {}).items()}
        self._cards: dict[str, dict[str, Any]] = {}

    def _sources(self, card: str) -> dict[str, Any]:
        if card in self.extra:
            return self.extra[card]
        if card not in self._cards:
            kind, _, name = card.partition("/")
            path = self.run_file if kind == "run" else self.library / kind / f"{name}.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                found = data.get("sources") if isinstance(data, dict) else None
            except (OSError, ValueError):
                found = None
            self._cards[card] = found if isinstance(found, dict) else {}
        return self._cards[card]

    def resolve(self, card: str | None, ids: Iterable[str], lang: str) -> tuple[SourceRef, ...]:
        out: list[SourceRef] = []
        ids = [str(i) for i in ids]
        if card and card.split("/", 1)[0] == "experience":
            return (SourceRef("own", t("hank.source.own", lang), None),)
        table = self._sources(card) if card else {}
        for sid in ids:
            if sid in ("cockpit", "live", "own"):
                out.append(SourceRef(sid, t(f"hank.source.{sid}", lang), None))
                continue
            if sid not in table:
                _LOGGER.info("source %s not on card %s: left out", sid, card)
                continue
            title, url = _split_source(table[sid], lang)
            if claims.blocked_url(url):
                url = None
            out.append(SourceRef(sid, title, url))
        return tuple(dict.fromkeys(out))

    __call__ = resolve


def card_of(passage: Passage) -> str:
    """The card a passage comes from, as kind/name ("stages/autoflower"), from its id `kind/card#path`."""
    if "#" in passage.id:
        return passage.id.split("#", 1)[0]
    return passage.card if "/" in passage.card else f"{passage.kind}/{passage.card}"


def _named_sources(card: str, body: str, lang: str) -> tuple[SourceRef, ...]:
    """The card's sources whose publisher the text names ("laut Hanfgarten": the Hanfgarten source), for a strain
    summary that cites no id: a number from a seed shop never stands without its marker (finding 9)."""
    book = SourceBook()
    folded = text.fold(body)
    ids = []
    for sid, raw in book._sources(card).items():
        title, _ = _split_source(raw, lang)
        publisher = text.fold(title.split(",", 1)[0]).strip()
        if len(publisher) >= 4 and re.search(rf"(?<![^\W_]){re.escape(publisher)}(?![^\W_])", folded):
            ids.append(sid)
    return book.resolve(card, ids, lang) if ids else ()


def path_of(passage: Passage) -> str:
    return passage.id.split("#", 1)[1] if "#" in passage.id else ""


def strip_ids(value: str) -> str:
    """Source ids out of a card text, for the prompt: "(S5)" goes, "(VGrow-Voreinstellung, S1)" keeps its words."""
    value = _PURE_IDS.sub("", value)
    return _TRAILING_ID.sub(")", value)


# ---------- the context ----------

@dataclass
class LedgerNote:
    """A ledger entry this answer is about (spec 6.2, 6.3, addendum N4). tell: name the consequence now (once per
    stage, metric and direction); unchanged: the readings show the advice was not followed."""

    item: dict[str, Any]
    verdict_index: int | None
    tell: bool
    unchanged: bool
    advice: str


@dataclass
class Budget:
    tokens: int = 0
    limit: int = LIMIT_TOKENS
    fits: bool = True
    trimmed: list[str] = field(default_factory=list)


@dataclass
class Context:
    lang: str
    question: str
    route: Route | None
    live: LiveState
    intent: str = "info"
    mentions: Mentions = field(default_factory=Mentions)
    verdicts: verdicts.VerdictSet = field(default_factory=verdicts.VerdictSet)
    facts: dict[str, Fact] = field(default_factory=dict)  # every fact by id, F1 ...
    prompt_facts: list[str] = field(default_factory=list)  # ids in the prompt, best first (after trimming)
    draft_facts: list[str] = field(default_factory=list)  # ids the draft states, in order
    book: list[str] = field(default_factory=list)  # ledger fact ids (BUCH)
    forecast: str | None = None  # the forecast's fact id
    covered: set[str] = field(default_factory=set)  # passage facts a verdict or computed fact already says
    advice: list[tuple[int, str]] = field(default_factory=list)  # (verdict index, advice) for the draft
    ledger_notes: list[LedgerNote] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    keep_groups: set[str] = field(default_factory=lambda: set(GROUPS))
    stale_cause: bool = False
    slot_numbers: tuple[Num, ...] = ()
    stage_card: str | None = None
    automation_port: str | None = None
    gaps: list[dict[str, Any]] = field(default_factory=list)  # gap entries for the store (WP7/WP8 write them)
    budget: Budget = field(default_factory=Budget)
    book_kept: bool = True
    forecast_kept: bool = True
    resolve: Callable[..., tuple[SourceRef, ...]] | None = None  # (card, ids, lang) -> refs
    sayings: list[str] = field(default_factory=list)  # card field "saying" of the hits (spec 4.5), maybe empty
    # Facts the code states word for word in every answer, the model's too (acceptance 23.10.2026, review 3, H3): the
    # dose factor note of a dose answer (addendum N5), the request to confirm a draft feeding card, and the legal
    # basics. Trimming never drops them, and a model sentence never replaces them.
    mandatory: list[str] = field(default_factory=list)
    # The answer shape (compose.shape, acceptance 23.10.2026): what answers the question, ("verdict", index),
    # ("fact", fid) or ("stale", None); the metrics the question asks about or names through a device; the computed
    # facts made for this question (the automation's state for "warum ist der Befeuchter aus", the cockpit's light
    # step for a light wish, the probe against the box sensor, the feeding plan).
    direct: tuple[str, Any] | None = None
    focus: set[str] = field(default_factory=set)
    related: set[str] = field(default_factory=set)
    # A passage fact whose text another passage of the search, with the same score, merely repeats in part (the
    # overview holds its preset note, grow-05): that shorter passage as a fact with the same id. The last trimming step swaps it in, when
    # nothing else is left to cut (feeding v2, grow-17: the two harvest passages tie once the VIVOSUN card is in the
    # index, and the overview took the prompt to 518 of 500 tokens).
    shorter: dict[str, Fact] = field(default_factory=dict)

    @property
    def fits(self) -> bool:
        return self.budget.fits

    def group(self, marker: str) -> Group | None:
        return next((g for g in self.groups if g.id == marker), None)

    def known(self, marker: str) -> bool:
        """A marker the model was shown: a kept fact, a ledger fact or forecast if kept, or a kept state group."""
        if marker.startswith("S"):
            g = self.group(marker)
            return g is not None and g.key in self.keep_groups
        return marker in self.shown_facts()

    def shown_facts(self) -> list[str]:
        out = list(self.prompt_facts)
        if self.forecast and self.forecast_kept:
            out.append(self.forecast)
        if self.book_kept:
            out.extend(self.book)
        return out


def _slots(route: Route | None) -> Any:
    return getattr(getattr(route, "retrieval", None), "slots", None)


def _slot_numbers(route: Route | None, intent: str) -> tuple[Num, ...]:
    """Numbers the validator allows from the question's slots (spec 5.3): week, litres, step, and the percent level
    only for an action wish. The slot names are WP4's; read defensively."""
    slots = _slots(route)
    out: list[Num] = []
    for name, unit in (("week", "wk"), ("liters", "l"), ("step", "step")):
        value = getattr(slots, name, None)
        if isinstance(value, int | float) and not isinstance(value, bool):
            out += [Num(float(value), unit), Num(float(value), "")]
    percent = getattr(slots, "percent", None)
    if intent == "action" and isinstance(percent, int | float) and not isinstance(percent, bool):
        out.append(Num(float(percent), "%"))
    return tuple(out)


def _numbers(value: str, lang: str, extra: Iterable[Num] = ()) -> tuple[Num, ...]:
    """Numbers of a fact text, plus ratio values below 1 ("1/2" also allows "halb", 0.5)."""
    nums = list(text.parse_numbers(value, lang))
    for ratio in text.ratios(value):
        a, b = (float(x) for x in ratio.split("/"))
        if b and a < b:
            nums.append(Num(a / b, ""))
    return tuple(dict.fromkeys([*nums, *extra]))


def _fact_lang(passage: Passage, lang: str) -> str:
    """The language a passage's text is really in: German when the English twin is missing (the index then puts
    the German text under "en" as well, spec 2.1)."""
    if passage.twin_missing or not passage.text.get(lang):
        return "de"
    return lang


def _passage_text(passage: Passage, lang: str) -> str:
    body = passage.text.get(lang) or passage.text.get("de") or next(iter(passage.text.values()), "")
    if path_of(passage).startswith("facts."):  # a key and value fact: say which key ("Höhe indoor: bis 120 cm")
        label = (passage.meta.get("fact_en") if lang == "en" else None) or passage.meta.get("fact") or (
            passage.title or {}).get(lang) or (passage.title or {}).get("de") or ""
        if label and not text.fold(body).startswith(text.fold(str(label))):
            body = f"{label}: {body}"
    return body.strip()


def _column(meta: Mapping[str, Any]) -> Any:
    """A chart cell's column (bloom_4, veg); the index keeps it in "column", the week number in "week"."""
    return meta.get("column") if meta.get("column") is not None else meta.get("week")


def _ordered_hits(route: Route | None) -> list[Hit]:
    hits = list(getattr(getattr(route, "retrieval", None), "hits", None) or [])
    return sorted(hits, key=lambda h: -float(h.score))


# Which nutrient card a product name points to (vocab ids), so a question that names the other fertilizer keeps its
# passages ("Was ist Base B?" while BioBizz doses).
_CARD_PRODUCTS = {"vivosun-base-ab": frozenset({"vivosun", "vivosun_base", "base_a", "base_b"}),
                  "biobizz-2026": frozenset({"biobizz", "bio_bloom", "bio_grow", "top_max", "alg_a_mic"})}


def _active_card(live: LiveState) -> str | None:
    """The nutrient card that doses now (feeding.view card id), or None (no choice, no card, nothing withheld)."""
    view = live.feeding_view if isinstance(live.feeding_view, dict) else {}
    if view.get("state") in (None, "needs_choice") or view.get("withheld"):
        return None
    card = view.get("card") if isinstance(view.get("card"), dict) else {}
    return str(card["id"]) if card.get("id") else None


def _named_cards(m: Mentions) -> set[str]:
    return {card for card, names in _CARD_PRODUCTS.items() if m.products & names}


def _consistent(hits: list[Hit], m: Mentions, live: LiveState, slots: Any, *, live_question: bool) -> list[Hit]:
    """One stage and one nutrient page and week per answer (grow-01, grow-06): passages of another stage, page or
    week than the leading one drop out, so the draft never sets two bands or two chart values side by side. A live
    question without a named stage keeps the current stage's bands, and "this week" keeps this week's chart column
    (live-01, live-02), whatever the search put first."""
    stage = next(iter(m.stages)) if len(m.stages) == 1 else None
    if stage is None and live_question and live.stage_key:
        stage = live.stage_key
    if stage is None:
        stage = next((h.passage.meta.get("stage") for h in hits if _bands(h.passage)), None)
    page = getattr(slots, "page", None) or (next(iter(m.pages)) if len(m.pages) == 1 else None)
    if page is None:
        cells = [h.passage.meta.get("page") for h in hits if h.passage.meta.get("page")]
        run_page = (live.feeding_view.get("page") or {}).get("key") if isinstance(live.feeding_view.get("page"),
                                                                                    dict) else None
        page = run_page if run_page in cells else (cells[0] if cells else None)
    week = getattr(slots, "week", None)
    column = None
    feed_week = live.feeding_view.get("week") if isinstance(live.feeding_view.get("week"), dict) else {}
    if week is None and m.now and feed_week.get("key"):
        column = feed_week["key"]
    elif week is None:
        column = next((_column(h.passage.meta) for h in hits
                       if h.passage.meta.get("page") == page and _column(h.passage.meta) is not None), None)
    active = _active_card(live)
    named = _named_cards(m)
    choosing = feedplan.choice_needed(live.feeding_view if isinstance(live.feeding_view, dict) else {})
    out = []
    for hit in hits:
        meta = hit.passage.meta
        card = str(hit.passage.card).removeprefix("nutrients/")  # the index says "biobizz-2026", the kit the path
        if active and hit.passage.kind == "nutrients" and card != active and card not in named:
            continue  # feeding v2: only the fertilizer that doses, unless the question names the other one
        if choosing and hit.passage.kind == "nutrients" and meta.get("page") and _column(meta) is not None:
            continue  # no fertilizer chosen: no week of either table stands for "this week" (feeding v2)
        if stage and _bands(hit.passage) and meta.get("stage") != stage:
            continue  # a band of another stage (preset notes of a neighbouring stage may stay, grow-05)
        if page and meta.get("page") and meta.get("page") != page:
            continue
        if meta.get("page") and _column(meta) is not None:
            if week is not None and not _week_is(_column(meta), week):
                continue
            if week is None and column is not None and _column(meta) != column:
                continue
        out.append(hit)
    return out


def _bands(passage: Passage) -> bool:
    """A passage that states target bands of a stage: one target, or the stage overview."""
    return bool(passage.meta.get("stage")) and (bool(passage.meta.get("metric")) or path_of(passage).endswith(
        ".overview"))


def _week_is(meta_week: Any, week: Any) -> bool:
    """A cell's week (bloom_4, 4) against a slot week (4)."""
    raw = str(meta_week)
    digits = re.findall(r"\d+", raw)
    return bool(digits) and int(digits[-1]) == int(week)


def _relevant_groups(m: Mentions) -> set[str]:
    out = {"age", "stage"}
    for metric in m.metrics:
        if metric in METRIC_GROUPS:
            out.add(METRIC_GROUPS[metric])
    for device in m.devices:
        out.update(DEVICE_GROUPS.get(device, ("ports",) if device.startswith("usb") else ()))
    return out


def _unchanged(baseline: Mapping[str, Any], snap: Mapping[str, Any]) -> bool:
    """True when every device value the ledger kept at the entry is still the same (spec 6.2: no change seen)."""
    seen = False
    for key, value in baseline.items():
        if key == "value":
            continue
        if key in ("dfan", "cfan"):
            now = (snap.get(key) or {}).get("step")
        elif key == "light":
            now = (snap.get("light") or {}).get("level")
        elif key.endswith("_on") and key.startswith("usb"):
            now = ((snap.get("ports") or {}).get(key[:-3]) or {}).get("on")
        else:
            continue
        seen = True
        if now != value:
            return False
    return seen


def _proposal_label(proposal: Mapping[str, Any], lang: str) -> str | None:
    action = proposal.get("action")
    if action in ("dfan", "cfan") and proposal.get("step") is not None:
        return t(f"hank.proposal.{action}", lang, step=proposal["step"])
    if action == "light" and proposal.get("level") is not None:
        return t("hank.proposal.light", lang, value=fmt_qty(proposal["level"], "%", lang))
    return None


def _cockpit(lang: str) -> tuple[SourceRef, ...]:
    return (SourceRef("cockpit", t("hank.source.cockpit", lang), None),)


class _Builder:
    def __init__(self, ctx: Context, resolve: Callable[..., tuple[SourceRef, ...]]) -> None:
        self.ctx, self.resolve = ctx, resolve
        self.n = 0

    def add(self, kind: str, body: str, refs: tuple[SourceRef, ...], *, lang: str | None = None,
            status: str = "approved", passage: str | None = None, extra: Iterable[Num] = ()) -> str:
        self.n += 1
        fid = f"F{self.n}"
        self.ctx.facts[fid] = Fact(id=fid, kind=kind, text=body, numbers=_numbers(body, lang or self.ctx.lang, extra),
                                   sources=refs, status=status, passage=passage)
        return fid


def build(route: Route | None, live: LiveState | Mapping[str, Any], ledger_items: Iterable[Mapping[str, Any]] | None,
          lang: str, *, question: str = "", proposals: Iterable[Proposal] = (),
          resolve: Callable[..., tuple[SourceRef, ...]] | None = None, vocab_path: Path = VOCAB_FILE,
          passage_sources: Callable[[Passage, str], Iterable[SourceRef]] | None = None,
          limit: int = LIMIT_TOKENS) -> Context:
    """The sheet for one answer. live: a LiveState or the collector's mapping; ledger_items: the ledger's items
    (spec 6.1 shape; None takes live.ledger); proposals: WP7's cards for this answer; resolve(card, ids, lang):
    a source resolver for cards (default: a SourceBook over kartei/ and plant.json); passage_sources(passage, lang):
    the index's own resolver for a passage (kartei_index.Index.sources), used for passages when given."""
    state = collect(live)
    items = [dict(i) for i in (state.ledger if ledger_items is None else ledger_items) if isinstance(i, Mapping)]
    resolve = resolve or SourceBook()
    intent = getattr(route, "intent", "info") or "info"
    retrieval = getattr(route, "retrieval", None)
    slots = _slots(route)
    m = mentions(question, lang, ports=state.ports, corrections=getattr(retrieval, "corrections", None) or (),
                 entities=_list(getattr(slots, "entities", None)),
                 vocab_path=vocab_path)
    ctx = Context(lang=lang, question=question, route=route, live=state, intent=intent, mentions=m,
                  proposals=list(proposals), slot_numbers=_slot_numbers(route, intent),
                  automation_port=str(state.automation_view.get("port") or automation.PORT), resolve=resolve)
    m.now = m.now or bool(getattr(slots, "now", False) or getattr(slots, "this_week", False))
    b = _Builder(ctx, resolve)
    live_question = not m.stages and bool(m.metrics or m.devices or m.now or intent == "action")
    hits = _consistent(_ordered_hits(route), m, state, slots, live_question=live_question)
    current = state.stage_key
    stages_card = next((card_of(h.passage) for h in hits if h.passage.kind == "stages"), None)
    ctx.stage_card = stages_card or _stage_card_of_run()

    # ---- verdicts: current stage only, metrics the question is about (spec 4.3) ----
    asked = set(_list(getattr(slots, "stages", None))) | m.stages
    if getattr(slots, "stage", None):
        asked.add(str(slots.stage))
    other_stage = bool(asked) and current not in asked
    explicit = set(m.metrics) | set(_list(getattr(slots, "metrics", None)))
    feeding_only = m.feeding and not (explicit or m.devices)  # "When do I feed the next time?": no climate verdict
    for hit in hits:  # because a climate passage came up in the search (finding 7)
        meta = hit.passage.meta
        if hit.passage.kind == "stages" and meta.get("metric") and meta.get("stage") in (None, current) \
                and not feeding_only:
            explicit.add(str(meta["metric"]))
    device_metrics = {mm for d in m.devices for mm in DEVICE_METRICS.get(d, ())}
    if ctx.automation_port in m.devices:
        device_metrics.add(automation.METRIC)
    for note_item in items:  # an open ledger entry about a named device brings its metric along (live-06)
        action = ((note_item.get("recommendation") or {}).get("proposal") or {}).get("action")
        if ACTION_DEVICES.get(str(action)) in m.devices and note_item.get("metric"):
            device_metrics.add(str(note_item["metric"]))
    touches_live = bool(explicit or device_metrics or m.now or intent == "action" or m.devices
                        or any(h.passage.kind == "live" for h in hits))
    kinds = {h.passage.kind for h in hits[:MAX_FACTS]}
    # "Wie warm keimt Basilikum?" asks about basil, not about the box: no live verdict for another crop, unless the
    # question says "jetzt" (the box's run is cannabis; the crop cards have no live checks).
    other_crop = bool(m.plants - {"cannabis"}) and not m.now
    if other_stage or not touches_live or other_crop:
        vs = verdicts.VerdictSet()
    elif state.stale:
        vs = verdicts.build(state, lang)
    else:
        wanted = explicit | device_metrics
        if not wanted and m.now and not m.feeding and not kinds & {"nutrients", "strains"}:
            vs = verdicts.build(state, lang, statuses=("low", "high"), card=ctx.stage_card, resolve=resolve)
        else:
            rows = verdicts.build(state, lang, metrics=explicit, card=ctx.stage_card, resolve=resolve)
            extra = verdicts.build(state, lang, metrics=device_metrics - explicit, statuses=("low", "high"),
                                   card=ctx.stage_card, resolve=resolve)
            vs = _merge(rows, extra)
    asked_metrics = set(m.metrics) | set(_list(getattr(slots, "metrics", None)))
    if vs.verdicts and not vs.stale:  # "VPD am Fühler: kein Messwert" only when the question names it (finding 10)
        keep = [i for i, v in enumerate(vs.verdicts)
                if v.metric in asked_metrics or v.status in verdicts.NEGATIVE
                or v.status == "ok" and not asked_metrics]  # a named metric: only its verdict, plus the off ones
        if len(keep) != len(vs.verdicts):
            vs = verdicts.VerdictSet(verdicts=[vs.verdicts[i] for i in keep],
                                     sources={n: vs.sources[i] for n, i in enumerate(keep) if i in vs.sources},
                                     partial={n for n, i in enumerate(keep) if i in vs.partial})
    ctx.verdicts = vs
    shown_metrics = {v.metric for v in vs.verdicts}
    ctx.stale_cause = vs.stale and bool(m.ids & {"vgrow", "readings", "vivosun_app", "cockpit"} or not m.devices)

    # ---- ledger notes (spec 6.2, 6.3, addendum N4) ----
    told_keys = {(i.get("stage_key"), i.get("metric"), i.get("status")) for i in items if i.get("consequence_told")}
    for item in items:
        if item.get("state") not in ("open", "not_applied", "declined") or item.get("stage_key") not in (None, current):
            continue
        proposal = (item.get("recommendation") or {}).get("proposal") or {}
        device = ACTION_DEVICES.get(str(proposal.get("action")))
        named = device in m.devices if device else False
        if item.get("metric") not in shown_metrics and not named:
            continue
        index = next((i for i, v in enumerate(vs.verdicts) if v.metric == item.get("metric")), None)
        verdict = vs.verdicts[index] if index is not None else None
        if verdict is not None and verdict.status not in verdicts.NEGATIVE:
            continue  # back on target: the ledger resolves it, nothing to tell
        key = (item.get("stage_key"), item.get("metric"), item.get("status"))
        tell = (item.get("state") in ("not_applied", "declined") or named) and key not in told_keys
        unchanged = item.get("state") == "not_applied" or (item.get("state") == "open"
                                                            and _unchanged(item.get("baseline") or {}, state.snapshot))
        advice = _proposal_label(proposal, lang) or ((verdict.advice or "") if verdict else "")
        ctx.ledger_notes.append(LedgerNote(dict(item), index, tell, unchanged, advice.rstrip(". ")))
        if tell:
            told_keys.add(key)
            if verdict is None or verdict.consequence is None:
                ctx.gaps.append({"kind": "consequence_missing", "stage": key[0], "metric": key[1], "status": key[2]})
    noted = {n.verdict_index for n in ctx.ledger_notes if n.verdict_index is not None}
    seen_advice: set[str] = set()
    for index, verdict in enumerate(vs.verdicts):
        if verdict.advice and index not in noted and verdict.advice not in seen_advice:
            seen_advice.add(verdict.advice)
            ctx.advice.append((index, verdict.advice))

    # ---- passage facts ----
    # A feeding answer when the question is about feeding, or a nutrient passage is among the two best hits and the
    # question names no reading or device ("Ist das Licht okay?" got the dose block from a BioBizz note;
    # screens review, finding 10).
    feeding_topic = m.feeding or (not (m.metrics or m.devices)
                                  and any(h.passage.kind == "nutrients" for h in hits[:2]))
    feed_draft = feeding_topic and state.feeding_view.get("state") == "draft"
    draft_note_done = feed_draft  # the computed feeding fact says it, with the request to confirm
    # feeding v2: the card that doses and that the owner confirmed (feeding.json, per card) is no draft for him, even
    # though its file says status draft: its passages get no "Entwurf" note
    confirmed_card = _active_card(state) if state.feeding_view.get("confirmed") else None
    passage_ids: list[tuple[str, Hit]] = []
    bodies: list[str] = []
    for hit in hits:
        if len(passage_ids) >= MAX_FACTS:
            break
        p = hit.passage
        if p.kind == "legal" and hit is not hits[0] and "kcang" not in m.ids:
            continue  # the law is said word for word when it answers, not because a feeding question brushed it
        body = _passage_text(p, lang)
        if not body:
            continue
        folded = text.fold(body)
        holder = next((i for i, seen in enumerate(bodies) if folded in seen or seen in folded), None)
        if holder is not None:  # the overview repeats its preset note (grow-05): say it once
            held = passage_ids[holder][0] if holder < len(passage_ids) else None
            if (held and folded in bodies[holder] and len(folded) < len(bodies[holder]) and held not in ctx.shorter
                    and p.status == passage_ids[holder][1].passage.status
                    and float(hit.score) >= float(passage_ids[holder][1].score)):  # a tie: the search could not
                # tell them apart, so the shorter one is as good an answer (a weaker one never replaces the overview)
                refs = tuple(passage_sources(p, lang)) if passage_sources else resolve(card_of(p), p.sources, lang)
                ctx.shorter[held] = Fact(held, p.kind, body, tuple(p.numbers), refs, p.status, p.id)
            continue
        bodies.append(folded)
        flang = _fact_lang(p, lang)
        if p.kind == "experience" or p.status == "own":
            run = p.meta.get("run", "?")
            body = f"{t('hank.experience.prefix', lang, n=run)} {body}"
        notes = []
        if p.status == "draft":
            if p.kind == "nutrients" and str(p.card).removeprefix("nutrients/") == confirmed_card:
                pass  # confirmed by the owner (see confirmed_card above)
            elif p.kind == "nutrients":
                if not draft_note_done:
                    notes.append(t("hank.draft.note", lang))
                    draft_note_done = True
            else:
                notes.append(t("hank.draft.card", lang))
        if p.meta.get("verified") == "partial" and not vs.partial:  # the head says it once (screens review, 10)
            notes.append(t("hank.verdict.partial", lang))
        refs = tuple(passage_sources(p, lang)) if passage_sources else resolve(card_of(p), p.sources, lang)
        if not refs and p.kind == "strains":
            refs = _named_sources(card_of(p), body, lang)  # "laut Hanfgarten" names its source (finding 9)
        fid = b.add(p.kind, body, refs, lang=flang, status=p.status, passage=p.id, extra=p.numbers)
        if p.kind == "legal":
            ctx.mandatory.append(fid)  # the law in the code's own words, never the model's (review 3, H3)
        if p.kind == "experience" or p.status == "own":
            ctx.related.add(fid)  # own experience matched the question as well as a card (spec 8.4): in sight
        if notes:  # the notes are in the answer language, the body maybe not (twin missing)
            fact = ctx.facts[fid]
            joined = body + " " + " ".join(notes)
            ctx.facts[fid] = Fact(fact.id, fact.kind, joined, fact.numbers, fact.sources, fact.status, fact.passage)
        passage_ids.append((fid, hit))
        meta = p.meta
        if p.kind == "stages" and meta.get("metric") in shown_metrics and meta.get("stage") in (None, current) \
                and not meta.get("status_key") \
                and not vs.stale:
            ctx.covered.add(fid)  # the verdict and its advice already say it
        if meta.get("status_key") and any(v.metric == meta.get("metric") and v.status == meta.get("status_key")
                                          for v in vs.verdicts):
            ctx.related.add(fid)  # what follows from the reading as it is now: said in the bubble

    # ---- computed facts (spec 4.4) ----
    computed: list[str] = []
    history = state.snapshot.get("pump_history")
    if (isinstance(history, Mapping) and not m.plants
            and ("watering" in m.ids or "pump" in m.devices
                 or re.search(r"\b(giess\w*|water\w*|pumpe\w*|pump)\b", text.fold(question)))):
        summary = pump_report.answer(state, lang, answer_id="pump-context").text
        fid = b.add("computed", t("hank.pump.context", lang, date=history.get("date") or "?", summary=summary),
                    _cockpit(lang))
        computed.append(fid)
        ctx.mandatory.append(fid)
        ctx.related.add(fid)
    probe = next((v for v in vs.verdicts if v.metric == "probe_humidity"), None)
    box_humidity = state.climate("humidity")
    if probe is not None and probe.actual is not None and box_humidity is not None:
        computed.append(b.add("computed", t("hank.fact.probe_vs_box", lang,
                                            value=livestate_reading(box_humidity, "humidity", "%", lang)),
                              _cockpit(lang)))
        if "probe_humidity" in asked_metrics | device_metrics:  # said for a humidity question, else only in "more"
            ctx.related.add(computed[-1])
    automation_facts: dict[str, str] = {}
    if m.devices & {"humidifier", "automation"} or ctx.automation_port in m.devices:
        automation_facts = _automation_facts(b, state, lang)
        computed += list(automation_facts.values())
        ctx.related.update(automation_facts.values())
    light_wish = intent == "action" and ("lamp" in m.devices or "light_percent" in m.metrics)
    said = any(str(LIGHT_STEP) in ctx.facts[fid].text and re.search(r"tipp|tap", text.fold(ctx.facts[fid].text))
               for fid, _ in passage_ids)  # the card already states the cockpit's step rule (live-03)
    if light_wish or any(p.kind == "light" for p in ctx.proposals) and not said:
        # A light wish always gets the cockpit's step rule in the code's words: it says why the card is not the asked
        # level (live-03); the card's own copy of the rule stays in "more".
        computed.append(b.add("computed", t("hank.fact.light_step", lang, step=LIGHT_STEP, first=LIGHT_FIRST),
                              _cockpit(lang)))
        if light_wish or "light_percent" in asked_metrics | device_metrics:
            ctx.related.add(computed[-1])
    reading_facts: list[str] = []
    if not vs.stale and ("water_tank" in m.devices and not m.feeding or "water_level" in asked_metrics):
        # (not "I fill a 6 liter tank" in a feeding question, live-02)
        level = state.climate("water_level")  # the tank's reading answers "Wie viel Wasser ist im Tank?"
        body = (t("hank.fact.water_level", lang, value=livestate_reading(level, "water_level", "%", lang))
                if level is not None else t("hank.fact.water_level_none", lang))
        if state.tank_state is not None:  # a type-9 sensor knows only wet or dry, never a level
            since = state.tank_since
            body = (t("hank.fact.tank_wet", lang) if state.tank_state == "wet"
                    else t("hank.fact.tank_dry_since", lang, time=fmt_time(since, lang)) if since is not None
                    else t("hank.fact.tank_dry", lang))
        reading_facts.append(b.add("computed", body, _cockpit(lang)))
    age = minutes(state.sensors_age)
    stuck = _STUCK_RE.search(text.fold(question)) is not None
    if not vs.stale and age is not None and stuck and not asked_metrics - {"sensors_age"}:
        reading_facts.append(b.add("computed", t("hank.fact.readings_fresh", lang, minutes=max(1, age)),
                                   _cockpit(lang)))  # the readings come in: the box is not stuck
    if not vs.stale and _STATUS_RE.search(text.fold(question)):
        wanted = {group for device, group in _DEVICE_GROUPS.items() if device in m.devices}
        for group in state.groups(lang):
            if group.key in wanted:  # the device's own state line answers first
                reading_facts.append(b.add("computed", group.text, _cockpit(lang)))
    computed += reading_facts
    ctx.related.update(reading_facts)
    claim = feedplan.claim_fact(question, state, lang) if question else None
    if claim:  # "Meine Pflanze ist 5 Wochen alt, wie viel Dünger?": the age check in code words (feeding v2, 4.7)
        computed.append(b.add("computed", claim, _cockpit(lang)))
        ctx.mandatory.append(computed[-1])
        ctx.related.add(computed[-1])
    feed_facts: list[str] = []
    if feeding_topic and state.feeding_view:
        week_named = getattr(slots, "week", None) is not None or bool(re.search(r"\d+\.\s*bl|week\s*\d|woche\s*\d",
                                                                                text.fold(question)))
        timing_only = (m.timing or m.frequency) and not _AMOUNT_RE.search(text.fold(question))
        # an amount question that names no week asks for this week's amount (feeding v2: "Wie viel Gramm Base B pro
        # Liter?", "Wie viele Löffel?" once the card and the stage are confirmed)
        amount_now = not week_named and bool(_AMOUNT_RE.search(text.fold(question))) \
            and state.feeding_view.get("state") == "ok"
        active, named = _active_card(state), _named_cards(m)
        facts, row_key = _feeding_facts(b, state, lang, resolve,
                                        now=m.now or amount_now or not week_named and not hits,
                                        frequency=m.frequency, doses=not timing_only,
                                        other_card=bool(active and named and active not in named))
        computed += facts
        feed_facts = [fid for fid in facts if fid not in ctx.mandatory]
        rhythm = str(state.feeding_view.get("frequency_note") or "")
        if m.frequency and rhythm:  # "wie oft", "das nächste Mal": the rhythm answers first, the week's row after
            feed_facts.sort(key=lambda fid: ctx.facts[fid].text != rhythm)
        ctx.related.update(feed_facts)
        for fid, hit in passage_ids:
            meta = hit.passage.meta
            if row_key and meta.get("page") == row_key[0] and str(_column(meta)) == str(row_key[1]):
                ctx.covered.add(fid)

    # ---- order: best open passage, computed facts, the other open passages, then the passages a verdict already
    # says (trimming cuts from the end) ----
    # What follows from a reading that is not the reading now ("Bleibt es dauerhaft zu feucht, drohen
    # Ertragsverluste" at 53 %, on target) is never said: not in the draft, not on the sheet.
    judged = {(v.metric, v.status) for v in vs.verdicts}
    off_status = {fid for fid, hit in passage_ids if hit.passage.meta.get("status_key")
                  and any(v.metric == hit.passage.meta.get("metric") for v in vs.verdicts)
                  and (hit.passage.meta.get("metric"), hit.passage.meta.get("status_key")) not in judged}
    passage_ids = [(fid, hit) for fid, hit in passage_ids if fid not in off_status]
    open_ids = [fid for fid, _ in passage_ids if fid not in ctx.covered]
    ctx.prompt_facts = open_ids[:1] + computed + open_ids[1:] + [fid for fid, _ in passage_ids if fid in ctx.covered]
    draft_passages = [fid for fid, _ in passage_ids if fid not in ctx.covered][:DRAFT_PASSAGES]
    named = set(m.metrics) | set(_list(getattr(slots, "metrics", None)))
    yes_no = re.match(r"\W*(ist|sind|passt|stimmt|geht|is|are|does|do)\b", text.fold(question)) is not None
    if live_question and yes_no and intent == "info" and not m.feeding and any(
            v.metric in named and v.status in ("ok", "low", "high") for v in vs.verdicts):
        # A yes/no about a reading: the verdict first, then its advice and what the code computed, no card dump
        # (screens review, finding 10: "Ist die Luftfeuchte gerade okay?" ran to 25 lines on the phone).
        draft_passages = []
    elif intent == "action":  # a switching wish: the card, its rule and the one best passage (screens review, 10)
        draft_passages = draft_passages[:1]
    if vs.stale:  # old readings: the stale sentence answers, no card text around it (live-05, finding 11)
        draft_passages = []
    ctx.draft_facts = draft_passages + computed
    ctx.draft_facts += [fid for fid in ctx.mandatory if fid not in ctx.draft_facts]

    ctx.focus = asked_metrics | device_metrics
    ctx.direct = _direct(ctx, asked_metrics, device_metrics, automation_facts, feed_facts, passage_ids, question,
                         reading_facts)

    # ---- forecast (spec 4.5, addendum N3) ----
    if m.timing and m.harvest:
        ctx.forecast = _forecast(b, state, lang, resolve)

    # ---- ledger facts for the prompt (BUCH) ----
    for note in ctx.ledger_notes:
        item = note.item
        when = item.get("created")
        label = vs.verdicts[note.verdict_index].label if note.verdict_index is not None else str(item.get("metric"))
        state_name = str(item.get("state"))
        line = t("hank.ledger.line", lang, time=_clock(when, lang), advice=note.advice or "?", label=label,
                 state=t(f"hank.ledger.state.{state_name}", lang))
        ctx.book.append(b.add("ledger", line, _cockpit(lang)))

    for hit in hits:
        saying = hit.passage.meta.get("saying")
        if isinstance(saying, Mapping):
            saying = saying.get(lang) or saying.get("de")
        if isinstance(saying, str) and saying.strip():
            ctx.sayings.append(saying.strip())

    if "watering" in m.ids and not any(h.passage.meta.get("beginner_mistake") for h in hits):
        ctx.gaps.append({"kind": "beginner_mistake_missing", "topic": "watering"})

    ctx.groups = state.groups(lang)
    _trim(ctx, limit)
    return ctx


# The automation fact that says best why the humidifier is off or on, most specific first.
AUTOMATION_REASONS = ("auto_tank", "auto_stage", "auto_paused", "auto_off", "auto_stale", "auto_missing", "auto_band")


# Question words that point at no topic ("Dreh das Licht hoch" is about the light, not about "höchstens").
# "laut" is "loud" in a question and "according to" in a card ("laut einem Samenhändler"); "Werte" is in every
# reading question and in "PPFD-Werte"; comparatives say which way, not what about (finding 9: "Muss die Abluft
# wirklich höher? Die find ich jetzt schon zu laut." opened with the seed shop's plant height).
_VAGUE = frozenset("hoch hoh hohe hoher hoeh viel mehr wenig gut lang kurz noch schon wirklich eigentlich bitte "
                   "genau etwa laut lauter wert werte niedrig niedriger tiefer weiter much more less good long short "
                   "really still higher lower loud value values".split())


def overlap(question: list[str], value: str, lang: str) -> int:
    """Question words a text shares, by their first four letters (the stemmer keeps "Trocknen" and "trockne" apart
    only in the ending); short and vague question words do not count."""
    have = {tok[:4] for tok in text.tokens(value, lang=lang) if len(tok) >= 4 and not tok[0].isdigit()}
    return len({tok[:4] for tok in question if len(tok) >= 4 and not tok[0].isdigit() and tok not in _VAGUE} & have)


# Words of a question that ask for a quantity in a unit ("bei wie viel Grad": °C), on the folded question.
_ASKED_UNITS = (
    (re.compile(r"\b(grad|warm|kalt|heiss|kuhl|temperatur\w*|temp|degrees?|hot|cold|cool)\b"), {"°C"}),
    (re.compile(r"\b(prozent|percent|feucht\w*|luftfeucht\w*|humid\w*)\b"), {"%"}),  # "at 100%" is a value
    (re.compile(r"\b(ml|milliliter|dung\w*|dosis|dose|nutrients?)\b"), {"ml", "ml/l"}),
    (re.compile(r"\b(gramm|grams?|g|loffel\w*|messloffel\w*|spoons?|base [ab]|vivosun)\b"), {"g", "g/l"}),
    (re.compile(r"\b(hoch|gross|tall|cm)\b"), {"cm"}),
    (re.compile(r"\b(lange?|long|wochen?|weeks?|tage?|days?)\b"), {"wk", "d"}),
)
_ADJECTIVE_RE = re.compile(r"\b(warm\w*|kalt\w*|heiss\w*|kuhl\w*|hot|cold|cool|feucht\w*|humid\w*)\b")
_QUANTITY = re.compile(r"\b(wie ?viel\w*|wie (hoch|lange?|warm|feucht|oft)|welche werte|wann|how (much|many|high|"
                       r"long|warm|humid|often)|what values|when)\b")


def fit(question: str, value: str, lang: str) -> int:
    """How well a text answers a question, for choosing the answer sentence: shared words, twice for every unit the
    question asks for that the text gives a number in ("bei wie viel Grad und wie viel Prozent": "16 bis 20 °C und
    55 bis 60 %"), one more for any number when the question asks for a quantity."""
    folded = text.fold(question)
    if slots_mod.other_object(question):  # "Wie heiß wird die Lampe?" asks for no air temperature (finding 6)
        folded = _ADJECTIVE_RE.sub(" ", folded)
    nums = text.parse_numbers(value, lang)
    units = {n.unit for n in nums}
    score = overlap(text.tokens(question, lang=lang), value, lang)
    for pattern, wanted in _ASKED_UNITS:
        if pattern.search(folded) and units & wanted:
            score += 2
    if nums and _QUANTITY.search(folded):
        score += 1
    return score


def _direct(ctx: Context, asked: set[str], device_metrics: set[str], automation_facts: Mapping[str, str],
            feed_facts: list[str], passage_ids: list[tuple[str, Hit]], question: str,
            reading_facts: Iterable[str] = ()) -> tuple[str, Any] | None:
    """What answers the question, so the bubble can open with it (acceptance 23.10.2026, "Wie warm ist es?"): stale
    readings; the verdict of the asked metric (the live reading with the code's verdict); the verdict a ledger message
    of this answer is about (live-06: "Muss die Abluft wirklich höher?"); for the humidifier the automation's reason;
    for a switching wish the device's verdict; for feeding the week's row or doses; else the card passage that fits
    the question best (the asked metric, the stage overview for several asked metrics, the most shared words), else
    the first verdict."""
    vs = ctx.verdicts
    if vs.stale:
        return ("stale", None)
    for i, verdict in enumerate(vs.verdicts):
        if verdict.metric in asked:
            return ("verdict", i)
    for note in ctx.ledger_notes:
        if note.tell and note.verdict_index is not None:
            return ("verdict", note.verdict_index)
    for fid in reading_facts:  # the tank's level, the readings coming in
        return ("fact", fid)
    for key in AUTOMATION_REASONS:
        if key in automation_facts:
            return ("fact", automation_facts[key])
    if ctx.intent == "action":
        for i, verdict in enumerate(vs.verdicts):
            if verdict.metric in device_metrics:
                return ("verdict", i)
    for fid in feed_facts:
        if fid in ctx.draft_facts:
            return ("fact", fid)
    drafted = [fid for fid in ctx.draft_facts if fid not in ctx.mandatory]
    by_fid = dict(passage_ids)

    def score(fid: str) -> int:
        hit = by_fid.get(fid)
        meta = hit.passage.meta if hit is not None else {}
        bonus = 0
        if meta.get("metric") in asked:
            bonus = 2
        elif hit is not None and path_of(hit.passage).endswith(".overview") and len(asked) > 1:
            bonus = 2 * len(asked)  # "Temp, Feuchte und VPD": the overview answers all of them at once
        return bonus + fit(question, strip_ids(ctx.facts[fid].text), ctx.lang)

    if drafted:
        best = max(drafted, key=score)  # max keeps the first of equal scores: the search's order
        if score(best) <= 0 and vs.verdicts:  # no card sentence fits ("Muss die Abluft wirklich höher?"): the
            off = next((i for i, v in enumerate(vs.verdicts) if v.status in verdicts.NEGATIVE), 0)
            return ("verdict", off)  # readings of the named device answer, the one off target first
        return ("fact", best)
    if ctx.draft_facts:
        return ("fact", ctx.draft_facts[0])
    return ("verdict", 0) if vs.verdicts else None


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value] if isinstance(value, list | tuple | set) else []


def _stage_card_of_run() -> str | None:
    try:
        run = json.loads(plant.PLANT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = run.get("stages_card") if isinstance(run, dict) else None
    return f"stages/{name}" if name else None


def _merge(a: verdicts.VerdictSet, b: verdicts.VerdictSet) -> verdicts.VerdictSet:
    out = verdicts.VerdictSet(verdicts=list(a.verdicts), sources=dict(a.sources), partial=set(a.partial))
    for i, v in enumerate(b.verdicts):
        index = len(out.verdicts)
        out.verdicts.append(v)
        if i in b.sources:
            out.sources[index] = b.sources[i]
        if i in b.partial:
            out.partial.add(index)
    return out


def _clock(value: Any, lang: str) -> str:
    try:
        return fmt_time(datetime.fromisoformat(str(value)).timestamp(), lang)
    except (TypeError, ValueError):
        return "?"


def _automation_facts(b: _Builder, state: LiveState, lang: str) -> dict[str, str]:
    """The automation's state as facts, by their catalog key (auto_band, auto_stage, ...), in the order said."""
    auto = state.automation_view
    if not auto:
        return {}
    refs = _cockpit(lang)
    from . import humidifier_report
    maintenance = humidifier_report.warning(auto, lang)
    if maintenance:
        return {"auto_tank": b.add("computed", maintenance, refs)}
    if not auto.get("enabled"):
        return {"auto_off": b.add("computed", t("hank.fact.auto_off", lang), refs)}
    out: dict[str, str] = {}
    band = auto.get("band") or [None, None]
    if isinstance(band, list | tuple) and len(band) == 2 and None not in band:
        floor = (auto.get("floor") or {}).get("floor")
        on_below = floor if isinstance(floor, int | float) and not isinstance(floor, bool) else band[0]
        out["auto_band"] = b.add("computed", t("hank.fact.auto_band", lang, lo=fmt_qty(on_below, "%", lang),
                                               hi=fmt_qty(band[1], "%", lang)), refs)
    if auto.get("needs_stage_confirmation"):
        out["auto_stage"] = b.add("computed", t("hank.fact.auto_stage", lang), refs)
    paused = auto.get("paused_until")
    if isinstance(paused, int | float) and not isinstance(paused, bool):
        out["auto_paused"] = b.add("computed", t("hank.fact.auto_paused", lang, time=fmt_time(float(paused), lang)),
                                   refs)
    if auto.get("stale"):
        out["auto_stale"] = b.add("computed", t("hank.fact.auto_stale", lang,
                                                minutes=minutes(automation.STALE_SECONDS)), refs)
    if auto.get("missing"):
        out["auto_missing"] = b.add("computed", t("hank.fact.auto_missing", lang), refs)
    return out


def _qty_items(rows: Iterable[Mapping[str, Any]], key: str, unit: str, lang: str) -> list[str]:
    out = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out.append(t("hank.fact.feed_item", lang, label=row.get("label") or row.get("component"),
                     value=fmt_qty(value, unit, lang)))
    return out


def _dose_value(dose: Mapping[str, Any], unit: str, lang: str) -> str:
    """'0,75 g, 1,5 Löffel' or '1,5 ml': the amount per litre in the active card's unit, the spoons where the view
    gives them (feeding v2: never "ml" after a gram amount)."""
    value = fmt_qty(dose.get("per_liter"), unit, lang)
    spoons = feedplan.spoons_text(dose.get("spoons_per_liter"), lang)
    return f"{value}, {spoons}" if spoons else value


def _feeding_facts(b: _Builder, state: LiveState, lang: str, resolve: Callable[..., tuple[SourceRef, ...]], *,
                   now: bool, frequency: bool, doses: bool = True,
                   other_card: bool = False) -> tuple[list[str], tuple[str, str] | None]:
    """Facts from feeding.view (spec 4.4, addendum N5): amounts only in state ok and only as the view computed them;
    in draft only this week's chart row and the request to confirm; the dose factor note in every dose answer.
    doses=False (a question about when or how often, not how much, finding 10): no amounts, so neither the request
    to confirm the chart nor the dose factor; the rhythm, "only water" and "harvest" stay. other_card (the question
    names only the fertilizer that does not dose now, "Wie viel Base A?" while BioBizz counts): which one counts and
    where to switch, never the active card's amounts under the other card's name (critique 23.10.2026, S4)."""
    view = state.feeding_view
    card_id = ((view.get("card") or {}).get("id")) if isinstance(view.get("card"), dict) else None
    card = f"nutrients/{card_id}" if card_id else None
    extra = {card: view.get("sources") or {}} if card else {}
    own = SourceBook(extra=extra) if extra else None

    def refs(ids: Iterable[str]) -> tuple[SourceRef, ...]:
        ids = list(ids)
        if own is not None:
            found = own.resolve(card, ids, lang)
            if found:
                return found
        return resolve(card, ids, lang) if card else ()

    out: list[str] = []
    st = view.get("state")
    unit = view.get("unit") or "ml/l"  # the chart's unit ("ml/l", "g/l")
    amount_unit = feedplan.unit_of(view)  # the amounts' unit (feeding v2: "g" for VIVOSUN, "ml" for BioBizz)
    page = view.get("page") if isinstance(view.get("page"), dict) else {}
    week = view.get("week") if isinstance(view.get("week"), dict) else {}
    row_key = None
    page_refs = refs(page.get("sources") or [])
    if st == "no_card":
        if view.get("problem"):
            out.append(b.add("computed", str(view["problem"]), _cockpit(lang)))
        return out, None
    if feedplan.choice_needed(view):  # two fertilizers, none chosen: no amounts, ask which one (feeding v2); the
        # direct answer of a feeding question (context._direct takes the first feeding fact)
        out.append(b.add("computed", feedplan.choice_fact(view, lang), _cockpit(lang)))
        return out, None
    if other_card:  # the direct answer of the question, as the choice fact is (context._direct)
        out.append(b.add("computed", feedplan.active_fact(view, lang), _cockpit(lang)))
        return out, None
    if st == "label_ends":  # the table stops after flowering week 8: no amount, the owner decides (feeding v2)
        out.append(b.add("computed", feedplan.plan_lines(state, lang)[0], page_refs or _cockpit(lang)))
        return out, None
    if st == "draft" and doses:
        out.append(b.add("computed", t("hank.fact.feed_draft", lang), _cockpit(lang)))  # one sentence (the bubble)
        b.ctx.mandatory.append(out[-1])
        if now and week.get("label") and page:
            chart = [row for row in view.get("chart") or [] if isinstance(row, dict) and row.get("owned")]
            items = _qty_items(chart, "now", unit, lang)
            if items:
                out.append(b.add("computed", t("hank.fact.feed_week", lang, week=week["label"], page=page.get("label"),
                                               items=", ".join(items)), page_refs))
                row_key = (str(page.get("key")), str(week.get("key")))
    elif st == "needs_stage":
        out.append(b.add("computed", t("hank.fact.feed_needs_stage", lang), _cockpit(lang)))
    elif st == "draft":
        pass  # no amounts asked: no chart row, no request to confirm
    elif st in ("ok",) and now and view.get("doses") and doses:
        items = [t("hank.fact.feed_dose_item", lang, label=d.get("label"), value=_dose_value(d, amount_unit, lang),
                   chart=fmt_qty(d.get("chart"), unit, lang)) for d in view["doses"] if isinstance(d, dict)]
        out.append(b.add("computed", t("hank.fact.feed_doses", lang, week=week.get("label") or "?",
                                       items=", ".join(items)), page_refs))
        plan = view.get("plan") if isinstance(view.get("plan"), dict) else None
        if plan and plan.get("items"):
            items = feedplan.amount_items(plan["items"], plan.get("unit") or amount_unit, lang, key="total",
                                          spoon_key="spoons_total") or []
            out.append(b.add("computed", t("hank.fact.feed_plan", lang, liters=fmt_qty(plan.get("liters"), "l", lang),
                                           items=", ".join(items)), page_refs))
        row_key = (str(page.get("key")), str(week.get("key")))
    elif st == "water_only" and view.get("not_before"):  # the owner's date for this run, not a week of the chart
        out.append(b.add("computed", str(view.get("warning") or ""), page_refs))
    elif st == "water_only" and week.get("label"):
        out.append(b.add("computed", t("hank.fact.feed_water_only", lang, week=week["label"]), page_refs))
    elif st == "harvest" and week.get("label"):
        out.append(b.add("computed", t("hank.fact.feed_harvest", lang, week=week["label"]), page_refs))
    if view.get("dose_factor_note") and doses:
        out.append(b.add("computed", str(view["dose_factor_note"]), refs(view.get("dose_factor_sources") or [])))
        b.ctx.mandatory.append(out[-1])  # addendum N5: every dose answer, the model's too (review 3, H3)
    if frequency and view.get("frequency_note"):
        out.append(b.add("computed", str(view["frequency_note"]), refs(view.get("frequency_sources") or [])))
    return out, row_key


def _forecast(b: _Builder, state: LiveState, lang: str, resolve: Callable[..., tuple[SourceRef, ...]]) -> str | None:
    """Harvest from bloom_start plus the BioBizz weeks (addendum N3), "frühestens" when the start is only estimated.
    Without a bloom_start there is no forecast: the stage day windows are only a fallback (card notes.4)."""
    view = state.feeding_view
    start = plant.parse_day(view.get("bloom_start"))
    harvest = next((row for row in view.get("after_bloom") or [] if isinstance(row, dict) and row.get("key") ==
                    "harvest"), None)
    if start is None or not harvest or not isinstance(harvest.get("bloom_week"), int):
        return None
    week_no = int(harvest["bloom_week"])
    day = start + timedelta(days=(week_no - 1) * 7)
    today = state.now.date() if isinstance(state.now, datetime) else date.today()
    days = (day - today).days
    week = t("hank.forecast.week", lang, n=week_no)
    when = day_text(start.isoformat(), lang)
    source = view.get("week_source") if isinstance(view.get("week_source"), dict) else {}
    if days <= 0:
        body = t("hank.forecast.reached", lang, week=week, start=when)
    else:
        key = "hank.forecast.harvest" if source.get("confirmed") else "hank.forecast.harvest_estimate"
        body = t(key, lang, week=week, start=when, days=days)
    card_id = ((view.get("card") or {}).get("id")) if isinstance(view.get("card"), dict) else None
    card = f"nutrients/{card_id}" if card_id else None
    own = SourceBook(extra={card: view.get("sources") or {}}) if card else None
    refs = own.resolve(card, view.get("bloom_start_sources") or [], lang) if own else ()
    return b.add("forecast", body, refs or _cockpit(lang))


# ---------- prompt and budget ----------

def _user(ctx: Context) -> str:
    lang = ctx.lang
    lines = [t("hank.prompt.head.state", lang), ctx.live.digest(lang, ctx.keep_groups)]
    sentences = ctx.verdicts.sentences(lang)
    if sentences:
        lines.append(t("hank.prompt.head.verdicts", lang))
        lines.extend(f"- {s}" for s in sentences)
    facts = list(ctx.prompt_facts) + ([ctx.forecast] if ctx.forecast and ctx.forecast_kept else [])
    if facts:
        lines.append(t("hank.prompt.head.facts", lang))
        lines.extend(f"[{fid}] {strip_ids(ctx.facts[fid].text)}" for fid in facts)
    if ctx.book and ctx.book_kept:
        lines.append(t("hank.prompt.head.ledger", lang))
        lines.extend(f"[{fid}] {strip_ids(ctx.facts[fid].text)}" for fid in ctx.book)
    question = ctx.question.replace('"', "'")
    lines.append(f'{t("hank.prompt.head.question", lang)}: "{question}"')
    return "\n".join(lines)


def prompt(ctx: Context) -> list[dict[str, str]]:
    """The two chat messages. The system message is the same bytes in every call of a language (spec 4.1)."""
    return [{"role": "system", "content": t("hank.prompt.system", ctx.lang)}, {"role": "user", "content": _user(ctx)}]


def tokens(ctx: Context) -> int:
    return sum(text.estimate_tokens(msg["content"]) for msg in prompt(ctx))


def _trim(ctx: Context, limit: int) -> None:
    budget = Budget(limit=limit)
    keep = _relevant_groups(ctx.mentions) | {METRIC_GROUPS.get(v.metric, "climate") for v in ctx.verdicts.verdicts}
    droppable = [g for g in GROUP_TRIM if g not in keep]
    while tokens(ctx) > limit:
        cuttable = [fid for fid in ctx.prompt_facts if fid not in ctx.mandatory]
        swap = next((fid for fid in ctx.prompt_facts if fid in ctx.shorter), None)
        if len(ctx.prompt_facts) > MIN_FACTS and cuttable:
            fid = cuttable[-1]  # the weakest fact that is not mandatory (review 3, H3)
            ctx.prompt_facts.remove(fid)
            budget.trimmed.append(f"fact:{fid}")
        elif ctx.book and ctx.book_kept:
            ctx.book_kept = False
            budget.trimmed.append("ledger")
        elif ctx.forecast and ctx.forecast_kept:
            ctx.forecast_kept = False
            budget.trimmed.append("forecast")
        elif droppable:
            group = droppable.pop(0)
            ctx.keep_groups.discard(group)
            budget.trimmed.append(f"group:{group}")
        elif ctx.live.stage_extras:  # v1.3: the bloom start and the height in the stage group, the last to go
            ctx.live.stage_extras = False
            ctx.groups = ctx.live.groups(ctx.lang)
            budget.trimmed.append("group:stage_extras")
        elif swap is not None:  # last: the shorter passage that says the same part (see Context.shorter)
            ctx.facts[swap] = ctx.shorter.pop(swap)
            budget.trimmed.append(f"shorter:{swap}")
        else:
            budget.fits = False
            break
    budget.tokens = tokens(ctx)
    ctx.budget = budget


def marked(ctx: Context, marker: str) -> tuple[str, tuple[Num, ...], tuple[SourceRef, ...]] | None:
    """Text, numbers and sources of a marker the model may cite: a fact F#, or a state group S# (live source)."""
    if marker.startswith("S"):
        g = ctx.group(marker)
        if g is None:
            return None
        return g.text, g.numbers, (SourceRef("live", t("hank.source.live", ctx.lang), None),)
    fact = ctx.facts.get(marker)
    if fact is None:
        return None
    return fact.text, fact.numbers, fact.sources


__all__ = ["Budget", "Context", "LedgerNote", "Mentions", "SourceBook", "build", "card_of", "marked", "mentions",
           "prompt", "strip_ids", "tokens", "MARKERS"]
