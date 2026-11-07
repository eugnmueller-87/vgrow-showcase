"""Shared data types of Hank (spec appendix A). Frozen after WP1: later packages add fields only at the end
and only with a default, never rename or reorder.

Plain data, no logic beyond Answer.plain(). Every other Hank module imports from here, so this file imports
nothing from the package except under TYPE_CHECKING.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # defined by WP4 (vgrow/hank/slots.py); only a name here, never imported at run time
    from vgrow.hank.slots import Slots

# The fixed vocabularies used in the fields below, so tests and later packages can check against one list.
ROUTES = ("grow", "no_data", "offtopic", "health", "health_emergency", "sale", "legal", "inject", "greet",
          "thanks", "clarify", "off", "empty", "too_long", "today",  # today: WP11 follow-up, vgrow/hank/today.py
          "fix",  # fix: the owner's "can you fix those things?" (23.10.2026), vgrow/hank/fix.py
          "autopilot",  # autopilot: "Was hat der Autopilot gemacht?" (23.10.2026), vgrow/hank/pilot_report.py
          "pump_history",  # actual USB reports, vgrow/hank/pump_report.py
          "humidifier_tank",  # maintenance estimates; never a measured level
          "explain", "reading",  # v1.2 (23.10.2026), vgrow/hank/asking.py: a glossary term; a value only code has
          "package_off",  # v1.4 (23.10.2026), vgrow/hank/methods.py: the card's package is switched off in the wiki
          "strains",  # 23.10.2026, vgrow/hank/strains.py: the strain advice and the lexicon, from code
          "thirst")  # 26.10.2026, vgrow/hank/thirst_report.py: "Muss ich gießen?" from the Durst-Index's brief
QTYPES = ("explain", "value", "howto", "why")  # v1.2: the question types of vgrow/hank/asking.py
DENSE_STATES = ("used", "off", "timeout", "missing", "partial")
PASSAGE_STATUSES = ("approved", "draft", "own")
CORRECTION_MODES = ("auto", "ask")

_SOURCES_LABEL = {"de": "Quellen", "en": "Sources"}


@dataclass(frozen=True)
class Num:
    """A number pulled out of a text. `unit` is one of the canonical units of text.UNIT_NAMES, or ""."""

    value: float
    unit: str = ""


@dataclass(frozen=True)
class SourceRef:
    """A source resolved per card (S ids collide between cards). `url` is None for text-only sources."""

    id: str
    title: str
    url: str | None


@dataclass(frozen=True)
class Passage:
    """One searchable piece of a card, id scheme `kind/card#path` (spec 2.1)."""

    id: str
    card: str
    kind: str
    title: dict[str, str]
    text: dict[str, str]
    numbers: tuple[Num, ...]
    sources: tuple[str, ...]
    entities: tuple[str, ...]
    status: str = "approved"  # approved | draft | own
    # metric, stage, status_key, min, max, unit, when, page, week, component
    meta: Mapping[str, Any] = field(default_factory=dict)
    twin_missing: bool = False

    def __hash__(self) -> int:  # title, text and meta are dicts: hash by identity of the passage instead
        return hash((self.id, self.card))


@dataclass
class Hit:
    passage: Passage
    score: float
    channels: dict[str, float] = field(default_factory=dict)


@dataclass
class Correction:
    """A spelling fix for one question word. `distance` is the plain OSA distance of the folded forms (the
    first-letter rule of spec 2.5 is already in `mode`). Alias hits carry distance 0."""

    token: str
    term: str
    entity: str
    distance: int
    mode: str  # auto | ask


@dataclass
class Retrieval:
    hits: list[Hit]
    confidence: float
    slots: Slots
    corrections: list[Correction] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    dense: str = "off"  # used | off | timeout | missing | partial
    ms: dict[str, int] = field(default_factory=dict)


@dataclass
class Clarify:
    """An open "Meinst du X?" question (spec 3.4): kept per client_id, valid for 5 minutes from `created`."""

    id: str
    question: str
    candidate: Correction
    lang: str = "de"
    created: float = 0.0


@dataclass
class Route:
    name: str  # one of ROUTES
    reply: str | None = None
    retrieval: Retrieval | None = None
    clarify: Clarify | None = None
    intent: str = "info"
    lang: str = "de"
    reason: str = ""
    # v1.2 (vgrow/hank/asking.py): the question's type (QTYPES or None), the glossary term or reading it is about, and
    # for a typed fix wish the problem keys it narrows to (None: every problem) and what it is about
    qtype: str | None = None
    term: str | None = None
    fix_keys: list[str] | None = None
    about: dict[str, Any] | None = None
    # v1.4 (vgrow/hank/methods.py, wikicards.py): the wiki cards ("methods/topping", "crops/chives") a code answer
    # comes from, and its form (howto, can, explain, heads, more)
    cards: list[str] | None = None
    form: str | None = None


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    text: str
    numbers: tuple[Num, ...]
    sources: tuple[SourceRef, ...]
    status: str = "approved"
    passage: str | None = None


@dataclass(frozen=True)
class Verdict:
    metric: str
    label: str
    status: str
    actual: float | None
    lo: float | None
    hi: float | None
    unit: str
    when: str | None
    stage_key: str | None
    stage_set_by: str | None
    sentence: str
    advice: str | None
    consequence: Fact | None


@dataclass
class Proposal:
    id: str
    kind: str
    body: dict[str, Any]
    label: str
    reason: str
    expires: float
    two_taps: bool


@dataclass
class Answer:
    id: str
    route: str
    lang: str
    text: str
    sources: list[SourceRef] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    clarify: dict[str, Any] | None = None
    chips: list[str] = field(default_factory=list)
    draft_only: bool = False
    note: str | None = None
    timing: dict[str, Any] = field(default_factory=dict)
    gap: bool = False
    # What did not go into the bubble (acceptance 23.10.2026, "nicht ueberladen"): the page folds it away as "Mehr aus
    # der Kartei". Its sources are numbered on from the bubble's.
    more: str = ""
    more_sources: list[SourceRef] = field(default_factory=list)
    # v1.3: the first line of a growing answer that code puts in front of the bubble ("Im Chat schalte ich nichts ohne
    # dich: ...", what the box is doing for a why question); it is part of text, kept here so the shape checks can tell
    # it from the composed bubble.
    lead: str | None = None
    # A link to a part of the page under the bubble (the route "autopilot" and the fix answer: "#autopilot"), as
    # {"href", "label"}; None for every other answer.
    link: dict[str, str] | None = None
    # The feeding plan as a graphic under the bubble (owner, 23.10.2026: "ich nutze <Dünger>" and "Wann dünge ich
    # was?"): weeks across, products down, the amount per litre or the chart value in each cell, today marked. Built
    # in code from feeding.view's timeline (feedplan.plan_graphic); None for every other answer.
    plan: dict[str, Any] | None = None

    def plain(self) -> str:
        """The answer as one flowing text plus a source line, the form the eval run scores (spec 5.8): the bubble,
        then "more", then every source in the order of its number."""
        body = f"{self.text} {self.more}".strip() if self.more else self.text
        every = [*self.sources, *self.more_sources]
        if not every:
            return body
        parts = []
        for number, source in enumerate(every, 1):
            shown_id = None if (source.title or "").startswith(f"{source.id}: ") else source.id  # "M18: ..." (v1.4)
            part = " ".join(bit for bit in (f"[{number}]", shown_id, source.title) if bit)
            if source.url:
                part += f" {source.url}"
            parts.append(part)
        label = _SOURCES_LABEL.get(self.lang, _SOURCES_LABEL["en"])
        return f"{body}\n{label}: " + "; ".join(parts)


class Embedder(Protocol):
    async def embed(self, texts: list[str], *, kind: str, timeout: float) -> list[list[float]]: ...
