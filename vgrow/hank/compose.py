"""The draft: Hank's answer from code alone (spec 0.4, 4.3; addendum N0: the draft is the answer).

An answer has three parts, always in this order (spec 4.3):
1. head, from code: the stale sentence instead of verdicts, or the verdict sentences, plus the "general guide value"
   note once when a shown range is only partly backed (addendum N3);
2. middle: in the draft the card's advice for each verdict off target, the facts the search found, the computed facts
   and the forecast; in a model answer the validated model sentences take this place;
3. tail, from code: the ledger's consequence message (once, with its source, then respect, spec 6.3) and the
   proposal sentence.

Card source ids in a text ("(S5)") become numbered references ("[1]"), numbered in order of first use over the whole
answer; a fact whose ids are not in its text gets its references at its end. Seed shop names are replaced, a draft
sentence that hits never_say is dropped (it never should: the cards are vetted), and the answer lists the sources
actually used. Nothing here calls the model.

Answer shape (owner rule "nicht ueberladen", acceptance 23.10.2026): the bubble opens with the one sentence that
answers the question (Context.direct: the live reading with the code's verdict, the asked target, the week's chart
row, the automation's reason), then at most three short sentences ranked for this question (the verdict's advice and
the code's mandatory sentences first, then one related target or warning), then the proposal line. Everything else
goes into "more" (the page shows it folded as "Mehr aus der Kartei"). Source markers stay on the sentences that need
them; the cockpit's own rules and the live readings carry none, and the bubble takes no further sentence once it shows
three numbered sources. The draft's core facts (Composed.core) are what a polished version must keep (accept()).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from vgrow.i18n import t

from . import claims, text
from .context import Context, fit, overlap
from .types import Num, SourceRef, Verdict

_ID = r"[A-Z]{1,3}\d{1,3}"
_PURE = re.compile(rf"\s*\(\s*({_ID}(?:\s*,\s*{_ID})*)\s*\)")
_BARE = re.compile(rf"(?<![\w\[])({_ID})(?![\w\]])")
_END = re.compile(r"([.!?:]*)\s*$")
_REFS = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")
_SPACE_PUNCT = re.compile(r"\s+([.,;:!?)])")
_EMPTY_PAREN = re.compile(r"\s*\((?:\s*(?:gesucht in|searched in|siehe|see|vgl\.)?[\s,;]*)\)")

BUBBLE_SENTENCES = 4  # the answer sentence plus at most three more; the proposal line comes on top
BUBBLE_SOURCES = 3  # numbered sources the bubble shows at most; the rest are cited inside "more"
BUBBLE_RANK = 4  # a sentence ranked worse than this goes into "more" even when the bubble has room
_LANG_WORDS = {"de": ("deutsch", "german"), "en": ("englisch", "english")}
_PAREN_END = re.compile(r"\s*\(([^()]*)\)\s*$")
UNNUMBERED = ("cockpit", "live")  # the cockpit's own rules and the live readings: no source marker needed
# The verdict word of each status, as the catalog templates hank.verdict.<status> write it (a test pins them).
VERDICT_WORDS = {"de": {"ok": "im Ziel", "high": "zu hoch", "low": "zu niedrig"},
                 "en": {"ok": "on target", "high": "too high", "low": "too low"}}


@dataclass
class Piece:
    """One sentence (or a short run of sentences) of an answer with the sources it rests on."""

    text: str
    sources: tuple[SourceRef, ...] = ()
    kind: str = "fact"  # stale, verdict, note, advice, fact, forecast, ledger, proposal, saying, model, filler
    # How the answer shape ranks it: direct (answers the question), advice_direct (the advice of that verdict),
    # related (a computed fact made for this question), mandatory (said word for word in every answer), the ledger's
    # ledger_advice, ledger_state, ledger_consequence, ledger_respect, ledger_none; "" for the rest.
    role: str = ""
    # A whole card section (a stage overview, a card summary): when it answers, only its answering sentence goes
    # into the bubble, the rest into "more" (finding 2: the light answer dumped the whole stage card).
    whole: bool = False


@dataclass(frozen=True)
class Core:
    """A core fact of the draft that a polished version must keep (acceptance rule, addendum N0): the direct answer
    (its numbers with unit and, for a verdict, the verdict word; word for word when it has no number), the first
    sentence of the verdict's advice, the proposal line and every mandatory sentence (word for word). exact: the text
    itself must be there."""

    kind: str  # direct, advice, proposal, mandatory
    text: str
    numbers: tuple[Num, ...] = ()
    words: tuple[str, ...] = ()
    exact: bool = False


@dataclass
class Composed:
    text: str
    sentences: list[str]
    sources: list[SourceRef]
    head: list[Piece] = field(default_factory=list)
    middle: list[Piece] = field(default_factory=list)
    tail: list[Piece] = field(default_factory=list)
    advised: list[Verdict] = field(default_factory=list)  # verdicts whose advice the draft carries (ledger, 6.2)
    told: list[tuple[Any, Any, Any]] = field(default_factory=list)  # (stage, metric, status): consequence told
    dropped: list[str] = field(default_factory=list)  # draft sentences dropped by never_say
    gaps: list[dict[str, Any]] = field(default_factory=list)
    more: str = ""  # what did not go into the bubble ("Mehr aus der Kartei"), numbered on from the bubble
    more_sentences: list[str] = field(default_factory=list)
    more_sources: list[SourceRef] = field(default_factory=list)
    core: list[Core] = field(default_factory=list)
    direct: str = ""  # the bubble's first sentence, the answer to the question
    # The draft's own sentences a polished version keeps in the code's words (validator): the card or computed
    # sentence that answers the question and the first sentence of its verdict's advice, when the bubble shows them.
    keep: list[Piece] = field(default_factory=list)


def one_per_document(refs: Iterable[SourceRef], lang: str | None) -> tuple[SourceRef, ...]:
    """A document cited in two language versions ("Düngeschema 2026 (PDF, deutsch)" and "(PDF, englisch)") keeps the
    version in the answer language, so one source is not counted twice in the bubble."""
    refs = tuple(dict.fromkeys(refs))
    if not lang:
        return refs
    groups: dict[str, list[SourceRef]] = {}
    for ref in refs:
        found = _PAREN_END.search(ref.title or "")
        words = text.fold(found.group(1)) if found else ""
        if found and any(w in words for pair in _LANG_WORDS.values() for w in pair):
            groups.setdefault(text.fold(ref.title[: found.start()]), []).append(ref)
    drop: set[SourceRef] = set()
    for twins in groups.values():
        if len(twins) < 2:
            continue
        own = [r for r in twins if any(w in text.fold(r.title) for w in _LANG_WORDS.get(lang, ()))]
        keep = own[0] if own else twins[0]
        drop.update(r for r in twins if r is not keep)
    return tuple(r for r in refs if r not in drop)


class Numbering:
    """Numbers sources in order of first use; the same source keeps its number across the answer."""

    def __init__(self, lang: str | None = None) -> None:
        self.order: list[SourceRef] = []
        self._index: dict[tuple[str, str, str | None], int] = {}
        self.lang = lang  # set: a document in two language versions is cited once (one_per_document)

    def number(self, ref: SourceRef) -> int:
        key = (ref.id, ref.title, ref.url)
        if key not in self._index:
            self.order.append(ref)
            self._index[key] = len(self.order)
        return self._index[key]

    def render(self, piece: Piece) -> str:
        return _closed(self._render(piece))

    def _render(self, piece: Piece) -> str:
        body = piece.text.strip()
        every = numbered(piece.sources)
        sources = one_per_document(every, self.lang)
        if not sources:
            return body
        twins = {ref.id for ref in every} - {ref.id for ref in sources}  # the other language version of a document
        by_id: dict[str, SourceRef] = {}
        for ref in sources:
            by_id.setdefault(ref.id, ref)
        used: set[str] = set()

        def pure(match: re.Match[str]) -> str:
            ids = [i.strip() for i in match.group(1).split(",") if i.strip() not in twins]
            if not ids or not all(i in by_id for i in ids):
                return match.group(0)
            used.update(ids)
            return " [" + _joined(self.number(by_id[i]) for i in ids) + "]"

        def bare(match: re.Match[str]) -> str:
            sid = match.group(1)
            if sid in twins:
                return ""
            if sid not in by_id:
                return sid
            used.add(sid)
            return f"[{self.number(by_id[sid])}]"

        body = _PURE.sub(pure, body)
        body = _BARE.sub(bare, body)
        rest = [ref for ref in dict.fromkeys(sources) if ref.id not in used]
        if rest:
            refs = " [" + _joined(self.number(ref) for ref in rest) + "]"
            end = _END.search(body)
            punct = end.group(1) if end else ""
            core = body[: end.start()] if end else body
            body = f"{core}{refs}{punct or '.'}"
        return body


def _joined(numbers: Iterable[int]) -> str:
    """Reference numbers in ascending order, each once ("[1, 2, 3]", never "[2, 1, 3]")."""
    return ", ".join(str(n) for n in sorted(set(numbers)))


def numbered(refs: Iterable[SourceRef]) -> tuple[SourceRef, ...]:
    """The sources that get a numbered marker: not the cockpit's own rules, not the live readings (they need none,
    the sentence names the cockpit or the reading itself)."""
    return tuple(ref for ref in refs if ref.id not in UNNUMBERED)


def _closed(body: str) -> str:
    """A sentence ends with its punctuation, also when a card text ended with a source id."""
    body = body.strip()
    return body if not body or body.endswith((".", "!", "?", ":")) else body + "."


def _resolve_inline(ctx: Context, value: str, card: str | None) -> tuple[SourceRef, ...]:
    if not ctx.resolve or not card:
        return ()
    ids = list(dict.fromkeys(_BARE.findall(value)))
    return ctx.resolve(card, ids, ctx.lang) if ids else ()


def head(ctx: Context) -> list[Piece]:
    lang = ctx.lang
    vs = ctx.verdicts
    if vs.stale:
        out = [Piece(vs.stale_sentence or "", (), "stale", "direct")]
        if ctx.stale_cause:
            out.append(Piece(t("hank.verdict.stale_cause", lang), (), "note", "stale_cause"))
        return out
    shown = vs.sentences(lang)  # the estimate note once (verdicts.once_estimate)
    out = []
    for i, verdict in enumerate(vs.verdicts):
        if ctx.direct == ("verdict", i):
            role = "direct"
        elif verdict.metric in ctx.focus:
            role = "related"  # a metric of the device the question names (live-04: the humidifier, the humidity)
        else:
            role = "warning" if verdict.status in ("low", "high") else ""
        out.append(Piece(shown[i], vs.sources.get(i, ()), "verdict", role))
    if vs.partial:
        out.append(Piece(t("hank.verdict.partial", lang), (), "note", "partial"))
    return out


def middle(ctx: Context, *, saying: bool = False) -> list[Piece]:
    """The draft's body: advice, facts, forecast; saying=True appends one card saying (spec 4.5: the caller allows
    it at most every fifth answer, never at red)."""
    out = [Piece(advice, _resolve_inline(ctx, advice, ctx.stage_card), "advice",
                 "advice_direct" if ctx.direct == ("verdict", index) else "advice")
           for index, advice in ctx.advice]
    for fid in ctx.draft_facts:
        fact = ctx.facts[fid]
        path = (fact.passage or "").partition("#")[2]
        if ctx.direct == ("fact", fid):
            role = "direct"
        elif fid in ctx.mandatory:
            role = "mandatory"
        elif fid in ctx.related:
            role = "related"
        elif path == "summary":  # what a card is about: never the bubble unless it is the answer
            role = "summary"
        else:  # a whole stage card only in "more" (the light answer dumped the stage card); its notes may be said
            role = "overview" if path.endswith(".overview") else ""
        whole = path == "summary" or path.endswith(".overview")
        out.append(Piece(fact.text, fact.sources, "fact", role, whole))
    if ctx.forecast:
        fact = ctx.facts[ctx.forecast]
        out.append(Piece(fact.text, fact.sources, "forecast", "related"))  # only made when the question asks for it
    negative = any(v.status in ("low", "high") for v in ctx.verdicts.verdicts) or ctx.verdicts.stale
    if saying and ctx.sayings and not negative:
        out.append(Piece(ctx.sayings[0], (), "saying"))
    return out


def tail(ctx: Context) -> tuple[list[Piece], list[tuple[Any, Any, Any]]]:
    lang = ctx.lang
    out: list[Piece] = []
    told: list[tuple[Any, Any, Any]] = []
    for note in ctx.ledger_notes:
        if not note.tell:
            continue
        item = note.item
        if note.advice:
            out.append(Piece(t("hank.ledger.advice", lang, advice=note.advice),
                             _resolve_inline(ctx, note.advice, ctx.stage_card), "ledger", "ledger_advice"))
        if item.get("state") == "declined":
            out.append(Piece(t("hank.ledger.declined", lang), (), "ledger", "ledger_state"))
        elif note.unchanged:
            out.append(Piece(t("hank.ledger.not_applied", lang), (), "ledger", "ledger_state"))
        verdict = ctx.verdicts.verdicts[note.verdict_index] if note.verdict_index is not None else None
        if verdict is not None and verdict.consequence is not None:
            out.append(Piece(t("hank.ledger.consequence", lang, text=verdict.consequence.text),
                             verdict.consequence.sources, "ledger", "ledger_consequence"))
        else:
            out.append(Piece(t("hank.ledger.none", lang), (), "ledger", "ledger_none"))
        out.append(Piece(t("hank.ledger.respect", lang), (), "ledger", "ledger_respect"))
        told.append((item.get("stage_key"), item.get("metric"), item.get("status")))
    labels = [p.label for p in ctx.proposals if p.label]
    if len(labels) == 1:
        # a card that takes two taps on the page says so (feeding v2: the plant's start; 23.10.2026: the stage card of
        # the VPD explanation, whose lead says "zwei Tipps", closed with "mit einem Tipp")
        card = next(p for p in ctx.proposals if p.label)
        two = card.two_taps
        out.append(Piece(t("hank.proposal.sentence_two" if two else "hank.proposal.sentence", lang, label=labels[0]),
                         (), "proposal", "proposal"))
    elif labels:
        out.append(Piece(t("hank.proposal.sentence_many", lang, labels="; ".join(labels)), (), "proposal",
                         "proposal"))
    port = ctx.automation_port
    if any(p.kind == "port" and str((p.body or {}).get("port")) == port for p in ctx.proposals):
        from vgrow.automation import MANUAL_PAUSE_SECONDS  # the code's own number, not a copy

        out.append(Piece(t("hank.proposal.hint_automation", lang, minutes=MANUAL_PAUSE_SECONDS // 60), (),
                         "proposal", "proposal"))
    return out, told


# ---------- the answer shape: the bubble and "more" ----------

def _split(value: str) -> list[str]:
    from .validator import split_sentences  # the validator's own splitter (the validator imports this module)

    return split_sentences(value)


def _rank(piece: Piece, i: int, n: int, *, lead: int = 0, shared: int = 0) -> int:
    """How much sentence i (of n) of a piece matters for this question: 0 answers it, 1 must be said (mandatory, the
    ledger's message), 2 the advice and the facts made for this question, 3 one related warning or note, 4 a card
    sentence that shares words with the question, then the rest of the cards. The bubble takes the best four, up to
    rank BUBBLE_RANK; "more" the rest. lead: the sentence of the direct piece that answers; shared: how many question
    words the sentence has."""
    role, first = piece.role, i == 0
    if role == "direct":
        return 0 if i == lead else (5 if piece.whole else 2)
    if role == "mandatory":  # the rule and its limit: the first and the last sentence ("Nie mehr als die Tabelle")
        return 1 if first or i == n - 1 else 5
    if role in ("stale_cause", "ledger_advice", "ledger_none"):
        return 1  # the ledger's message is told once (spec 6.3): advice, consequence, respect stay together
    if role == "ledger_consequence":
        return 1 if first else 5
    if role == "advice_direct":
        return 2 if first else 4
    if role == "related":
        return 2 if first else 3
    if role == "ledger_respect":  # "Das ist deine Entscheidung." closes the message (spec 6.3: then respect)
        return 1 if first else 6
    if piece.kind == "model":
        return 2
    if piece.kind == "filler":  # "In the chat I switch nothing without you" replaces a switch claim: in sight; a
        # catalog filler ("Gern.") only when there is room, and never in "more"
        return 2 if piece.text.strip() in (t(claims.NO_SELF_KEY, "de"), t(claims.NO_SELF_KEY, "en")) else 4
    if role in ("warning", "partial", "ledger_state") or piece.kind == "note":
        return 3 if first else 6
    if role == "overview" and first or role == "summary":
        return 6  # the whole stage card in one sentence, a card's summary: never the bubble unless it is the answer
    if piece.kind in ("advice", "fact", "forecast"):
        return 4 if shared else (5 if first else 6)
    return 7  # sayings, fillers


@dataclass
class _Unit:
    rank: int
    pos: int
    piece: int
    index: int
    text: str
    refs: frozenset[tuple[str, str, str | None]]


def _norm(value: str) -> str:
    """A text without numbered references and card source ids, in single spaces: for comparing sentences. A bracket
    that held only source ids ("(searched in S1, S2)") goes as a whole."""
    value = _REFS.sub("", value)
    value = _PURE.sub("", value)
    value = _BARE.sub("", value)
    value = _EMPTY_PAREN.sub("", value)
    value = _SPACE_PUNCT.sub(r"\1", re.sub(r"\s+", " ", value))
    return value.strip()


def _ids_in(value: str) -> set[str]:
    out: set[str] = set()
    for match in _PURE.finditer(value):
        out.update(i.strip() for i in match.group(1).split(","))
    out.update(_BARE.findall(value))
    return out


def _key(ref: SourceRef) -> tuple[str, str, str | None]:
    return (ref.id, ref.title, ref.url)


@dataclass
class Shape:
    bubble: list[Piece]  # in order: the direct answer first
    proposal: list[Piece]
    more: list[Piece]
    direct: str
    keep: list[Piece] = field(default_factory=list)  # see Composed.keep


def shape(pieces: list[Piece], lang: str | None = None, question: str = "") -> Shape:
    """Split an answer's pieces into the bubble (the direct answer first, at most BUBBLE_SENTENCES sentences and
    BUBBLE_SOURCES numbered sources), the proposal line and "more". The direct answer and the mandatory sentences
    always go into the bubble. A sentence said twice (a card note and the code's own copy of it) is said once. A part
    of a piece cites the sources it names inline plus the piece's trailing ones."""
    proposal = [p for p in pieces if p.role == "proposal" and p.text.strip()]
    units: list[_Unit] = []
    trailing: dict[int, tuple[SourceRef, ...]] = {}
    code = lang or "de"
    split = {pi: _split(p.text) or [p.text.strip()] for pi, p in enumerate(pieces)
             if p.role != "proposal" and p.text.strip()}
    leads: dict[int, int] = {}
    for pi, sentences in split.items():
        piece = pieces[pi]
        if piece.role == "direct" and piece.kind in ("fact", "model") and question and len(sentences) > 1:
            fits = [fit(question, _norm(s), code) for s in sentences]
            leads[pi] = fits.index(max(fits))  # the card sentence that answers; ties: the first one
    # A further sentence counts as on topic only with question words the answer does not already carry ("How is the
    # light?": the answer says "Light: 75 %", a "Light-Mix" chart row adds nothing).
    answered = {tok[:4] for pi, sentences in split.items() if pieces[pi].role == "direct"
                for tok in text.tokens(_norm(sentences[leads.get(pi, 0)]), lang=code)}
    words = [tok for tok in text.tokens(question, lang=code) if tok[:4] not in answered] if question else []
    for pi, sentences in split.items():
        piece = pieces[pi]
        refs = one_per_document(numbered(piece.sources), lang)
        inline = _ids_in(piece.text)
        trailing[pi] = tuple(r for r in refs if r.id not in inline)
        shared = [overlap(words, _norm(s), code) if words else 0 for s in sentences]
        lead = leads.get(pi, 0)
        for si, sentence in enumerate(sentences):
            own = {_key(r) for r in _cites(refs, sentence, piece.text, trailing[pi])}
            rank = 9 if piece.role == "more_only" else _rank(piece, si, len(sentences), lead=lead,
                                                               shared=shared[si])  # 9: never the bubble
            units.append(_Unit(rank, len(units), pi, si, sentence, frozenset(own)))
    # A mandatory sentence that restates the answer sentence says it already (grow-07: the card's "1/2 bis 1/4"
    # and the code's dose factor sentence with the same claim): the mandatory sentence answers, the card's goes.
    said_by: set[int] = set()
    answer_unit = next((u for u in units if u.rank == 0 and pieces[u.piece].role == "direct"), None)
    if answer_unit is not None:
        twin = next((u for u in units if pieces[u.piece].role == "mandatory" and u.rank == 1
                     and _restates(answer_unit.text, u.text, code)), None)
        if twin is not None:
            said_by.add(answer_unit.pos)
            twin.rank = 0
    seen: set[str] = set()
    chosen: list[_Unit] = []
    rest: list[_Unit] = []
    shown: set[tuple[str, str, str | None]] = set()
    taken: set[tuple[int, int]] = set()
    warned = False
    for unit in sorted(units, key=lambda u: (u.rank, u.pos)):
        key = text.fold(_norm(unit.text))
        if key in seen or unit.pos in said_by:
            continue  # said already (the dose factor as a card note and as the code's own sentence)
        seen.add(key)
        must = unit.rank <= 1
        role = pieces[unit.piece].role
        if not must and role not in _OWN_SAY and any(_restates(unit.text, other.text, code) for other in chosen):
            if pieces[unit.piece].kind != "filler":  # the card's "höchstens 25 Punkte pro Tippen" after the code's
                rest.append(unit)  # own sentence with that rule: in "more", not lost
            continue
        fits = (unit.rank <= BUBBLE_RANK and len(chosen) < BUBBLE_SENTENCES
                and (unit.refs <= shown or len(shown | unit.refs) <= BUBBLE_SOURCES)  # no new source past three
                and (unit.index == 0 or (unit.piece, unit.index - 1) in taken  # never a stray tail ("Set it in
                     or pieces[unit.piece].role in ("direct", "overview"))  # the app."); a card's notes stand alone
                and not (warned and pieces[unit.piece].role == "warning"))  # one warning, not every check off target
        if must or fits:
            chosen.append(unit)
            shown |= unit.refs
            taken.add((unit.piece, unit.index))
            warned = warned or pieces[unit.piece].role == "warning"
        elif pieces[unit.piece].kind != "filler":
            rest.append(unit)
    answer = next((u for u in chosen if u.rank == 0), None)
    direct_piece = answer.piece if answer is not None else None
    lead = ([answer] if answer is not None else []) + sorted(
        (u for u in chosen if u.piece == direct_piece and u is not answer), key=lambda u: u.pos)
    others = sorted((u for u in chosen if u.piece != direct_piece), key=lambda u: u.pos)
    rest.sort(key=lambda u: u.pos)
    first = lead[0].text if lead else (others[0].text if others else "")

    def single(unit: _Unit, role: str) -> Piece:
        piece = pieces[unit.piece]
        refs = _cites(piece.sources, unit.text, piece.text, trailing.get(unit.piece, ()))
        return Piece(unit.text, tuple(dict.fromkeys(refs)), piece.kind, role)

    keep: list[Piece] = []
    if answer is not None and pieces[answer.piece].kind not in ("verdict", "stale", "model", "filler"):
        keep.append(single(answer, "direct"))  # a card or computed sentence answered: a polished version keeps it
    advice = next((u for u in chosen if pieces[u.piece].role == "advice_direct" and u.index == 0), None)
    if advice is not None:
        keep.append(single(advice, "advice_direct"))
    return Shape(bubble=_regroup(lead + others, pieces, trailing), proposal=proposal,
                 more=_regroup(rest, pieces, trailing), direct=first, keep=keep)


# Roles whose sentences are said even when a chosen sentence shares their numbers: the verdict's advice ("Light
# below the VGrow flower preset (100%): ..." shares "light 100 flower" with the verdict, finding 2), the code's
# mandatory sentences and the facts made for this question.
_OWN_SAY = ("advice_direct", "mandatory", "related")
RESTATE_SHARE = 0.7  # share of a sentence's words (numbers aside) the other must have for "says nothing new"


def _restates(sentence: str, said: str, lang: str) -> bool:
    """A sentence that says nothing the other did not: every number of it (with unit) is in the other, and at least
    70 % of its other words are too."""
    nums = text.parse_numbers(_norm(sentence), lang)
    if not nums:
        return False
    have = text.parse_numbers(_norm(said), lang)
    if not all(_has(have, num) for num in nums):
        return False
    mine = {tok for tok in text.tokens(_norm(sentence), lang=lang) if not tok[0].isdigit()}
    if not mine:
        return True
    other = set(text.tokens(_norm(said), lang=lang))
    return len(mine & other) >= RESTATE_SHARE * len(mine)


def _cites(sources: Iterable[SourceRef], body: str, whole: str, trailing: Iterable[SourceRef]) -> list[SourceRef]:
    """The sources a part of a piece cites: the ids it names inline plus the piece's trailing ones. A part with a
    number that cites nothing (the strain card's "Blüte 8 bis 9 Wochen, indoor bis 120 cm laut einem Samenhändler",
    its source named only in a later sentence, finding 9) cites what the piece names, up to two documents."""
    sources = tuple(sources)
    inline = _ids_in(body)
    refs = [r for r in sources if r.id in inline] + list(trailing)
    if not numbered(refs) and text.parse_numbers(_norm(body)):
        named = [r for r in sources if r.id in _ids_in(whole)]
        if 0 < len(numbered(named)) <= 2:
            refs += named
    return refs


def _regroup(units: list[_Unit], pieces: list[Piece], trailing: dict[int, tuple[SourceRef, ...]]) -> list[Piece]:
    """Consecutive sentences of the same piece become one piece again, with the sources it names inline plus the
    piece's trailing sources. A piece split into several runs (its answering sentence moved to the front) carries its
    trailing sources on the runs with a number and on its last run, not on every run."""
    runs: list[list[_Unit]] = []
    for unit in units:
        if runs and unit.piece == runs[-1][-1].piece and unit.index == runs[-1][-1].index + 1:
            runs[-1].append(unit)
        else:
            runs.append([unit])
    last = {run[0].piece: i for i, run in enumerate(runs)}
    out: list[Piece] = []
    for i, run in enumerate(runs):
        piece = pieces[run[0].piece]
        body = " ".join(u.text for u in run)
        tail = trailing.get(run[0].piece, ())
        if last[run[0].piece] != i and not text.parse_numbers(_norm(body)):
            tail = ()
        refs = _cites(piece.sources, body, piece.text, tail)
        out.append(Piece(body, tuple(dict.fromkeys(refs)), piece.kind, piece.role))
    return out


# ---------- core facts and the acceptance of a polished version (addendum N0) ----------

def _reading(sentence: str, lang: str) -> tuple[Num, ...]:
    """The reading of a verdict sentence "{label}: {value}, ...": the first number after the label."""
    _, _, rest = sentence.partition(": ")
    found = text.parse_numbers(_norm(rest), lang)
    return (found[0],) if found else ()


def core_facts(ctx: Context, shaped: Shape, lang: str) -> list[Core]:
    """The draft's core facts: the direct answer (number with unit, the verdict word), the proposal line, every
    mandatory sentence. A polished version that loses one of them never replaces the draft (accept)."""
    out: list[Core] = []
    direct = ctx.direct
    if direct is not None and direct[0] == "verdict" and direct[1] < len(ctx.verdicts.verdicts):
        verdict = ctx.verdicts.verdicts[direct[1]]
        sentence = ctx.verdicts.sentences(lang)[direct[1]]
        word = VERDICT_WORDS.get(lang, {}).get(verdict.status)
        if word and verdict.actual is not None:
            out.append(Core("direct", _norm(sentence), _reading(sentence, lang), (word,)))
        else:  # no reading or no target: the code's own sentence
            out.append(Core("direct", _norm(sentence), (), (), exact=True))
    elif shaped.direct:
        # A direct answer without a number ("Solange die Phase nicht von dir bestätigt ist, schaltet die Automatik
        # ihn nie ein.") is kept word for word, else accept() would check nothing (finding 1).
        sentence = _norm(shaped.direct)
        nums = tuple(text.parse_numbers(sentence, lang))
        out.append(Core("direct", sentence, nums, exact=not nums))
    advice = next((p for p in shaped.keep if p.role == "advice_direct"), None)
    if advice is not None:  # the verdict's advice ("schrittweise hoch, vorher Abstand zur Lampe prüfen", finding 5)
        out.append(Core("advice", _norm(advice.text), exact=True))
    for piece in shaped.proposal:
        out.append(Core("proposal", _norm(piece.text), exact=True))
    for fid in ctx.mandatory:
        fact = ctx.facts.get(fid)
        if fact is not None and fact.text.strip():
            out.append(Core("mandatory", _norm(fact.text), exact=True))
    return out


def _has(hay: list[Num], num: Num) -> bool:
    return any(abs(h.value - num.value) < 1e-6 and (not num.unit or h.unit == num.unit) for h in hay)


def sheet_numbers(ctx: Context) -> list[Num]:
    """Every number the model was shown: the facts and ledger lines it may cite, the state groups, the verdict
    sentences, the question's slots. A number outside these and the draft is new."""
    out: list[Num] = list(ctx.slot_numbers)
    for fid in ctx.shown_facts():
        fact = ctx.facts.get(fid)
        if fact is not None:
            out.extend(fact.numbers)
    for group in ctx.groups:
        if group.key in ctx.keep_groups:
            out.extend(group.numbers)
    for sentence in ctx.verdicts.sentences(ctx.lang):
        out.extend(text.parse_numbers(sentence, ctx.lang))
    return out


def accept(core: list[Core], draft: Composed, polished: Composed, lang: str, *,
           shown: Iterable[Num] = ()) -> str | None:
    """None when the polished version may replace the draft, else why not (addendum N0: a version that does not pass
    leaves the draft): a core fact lost (its number with unit, the verdict word, the proposal line, a mandatory
    sentence), the bubble shorter than the direct answer, or a new number: one neither the draft nor the sheet the
    model was shown has (shown: sheet_numbers). The validator already ties every model number to the fact its
    sentence cites; this is the net under the whole text, the code's own parts included."""
    bubble = _norm(polished.text)
    full = _norm(f"{polished.text} {polished.more}")
    nums = {"bubble": text.parse_numbers(bubble, lang), "full": text.parse_numbers(full, lang)}
    folded = {"bubble": text.fold(bubble), "full": text.fold(full)}
    for item in core:
        where = "bubble" if item.kind in ("direct", "advice", "proposal") else "full"
        if item.exact and any(text.fold(_norm(part)) not in folded[where] for part in _split(item.text) or [item.text]):
            return f"{item.kind} lost: {item.text[:60]}"  # sentence by sentence: the bubble and "more" may share one
        for num in item.numbers:
            if not _has(nums[where], num):
                return f"{item.kind} lost the number {num.value:g} {num.unit}".rstrip()
        if item.words and not any(text.fold(word) in folded[where] for word in item.words):
            return f"{item.kind} lost the verdict word {item.words[0]!r}"
    direct = next((c for c in core if c.kind == "direct"), None)
    if direct is not None and len(bubble) < len(direct.text):
        return "shorter than the direct answer"
    have = [*text.parse_numbers(_norm(f"{draft.text} {draft.more}"), lang), *shown]
    for num in nums["full"]:
        if not _has(have, num):  # value and unit: "79 °C" is new although "79 %" is not
            return f"new number {num.value:g} {num.unit}".rstrip()
    return None


def assemble(ctx: Context, body: Iterable[Piece], *, draft: bool = True, more: Iterable[Piece] = (),
             keep: Iterable[Piece] = ()) -> Composed:
    """head + body + tail, numbered and shaped. body is the draft's middle or the validated model sentences; more:
    pieces that may only go into "more" (a polished version keeps the draft's card facts there); keep: the draft's
    own sentences a polished version says in the code's words before the model's (Composed.keep: the answer
    sentence and the verdict's advice), so the answer never gets lost in polishing (finding 1)."""
    lang = ctx.lang
    numbering = Numbering(lang)
    head_pieces = head(ctx)
    tail_pieces, told = tail(ctx)
    kept_body: list[Piece] = []
    dropped: list[str] = []
    for piece in [*keep, *body]:
        if piece.kind in ("advice", "fact", "forecast", "saying") and claims.forbidden(piece.text, lang):
            dropped.append(piece.text)
            continue
        kept_body.append(Piece(claims.vendors(piece.text, lang), piece.sources, piece.kind, piece.role))
    if not draft and not any(p.role == "direct" for p in [*head_pieces, *kept_body]):
        first = next((i for i, p in enumerate(kept_body) if p.kind == "model"), None)
        if first is not None:  # a card fact answered the question in the draft: the model's first sentence does now
            kept_body[first] = replace(kept_body[first], role="direct")
    extra = [Piece(claims.vendors(p.text, lang), p.sources, p.kind, "more_only") for p in more
             if p.text.strip() and not claims.forbidden(p.text, lang)]
    shaped = shape([*head_pieces, *kept_body, *tail_pieces, *extra], lang, ctx.question)
    sentences = [numbering.render(p) for p in [*shaped.bubble, *shaped.proposal] if p.text.strip()]
    shown = len(numbering.order)
    more_sentences = [numbering.render(p) for p in shaped.more if p.text.strip()]
    advised = [ctx.verdicts.verdicts[i] for i, _ in ctx.advice] if draft else []
    composed = Composed(text=" ".join(sentences), sentences=sentences, sources=list(numbering.order[:shown]),
                        head=head_pieces, middle=kept_body, tail=tail_pieces, advised=advised, told=told,
                        dropped=dropped, gaps=list(ctx.gaps), more=" ".join(more_sentences),
                        more_sentences=more_sentences, more_sources=list(numbering.order[shown:]),
                        direct=_norm(shaped.direct))
    if draft:
        composed.core = core_facts(ctx, shaped, lang)
        composed.keep = list(shaped.keep)
    return composed


def build(ctx: Context, *, saying: bool = False) -> Composed:
    """The whole draft answer."""
    return assemble(ctx, middle(ctx, saying=saying), draft=True)


def draft(ctx: Context, lang: str | None = None) -> str:
    """The draft as one text (spec appendix A). lang must be the context's language when given."""
    if lang is not None and lang != ctx.lang:
        raise ValueError(f"the context is in {ctx.lang}, not {lang}")
    return build(ctx).text
