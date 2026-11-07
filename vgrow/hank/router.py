"""Hank's router (spec 3.1, 3.3, 3.4, 3.5; addendum N2), pure code: which way a question goes.

Steps in the order of spec 3.1, the first that decides wins: clean (empty, too_long), mode (off), an open "Meinst du
X?" (yes or no), the gates (inject, health with the emergency first, sale, legal, greeting and thanks), then search and
decision. One change from the acceptance of 23.10.2026 (review 3, H2 and L5): the emergency check comes right after
cleaning, before the mode and before the injection gate, so "nur Warnungen" or an injection phrase never hides the
emergency reply (a fixed text, no model). The decision follows the table of addendum N2, which replaces the one of spec 3.3:

1. an ambiguous or borderline correction                       -> clarify
2. off-topic similarity >= T_OFF and confidence < T_HIT          -> offtopic
3. the question names a device or a metric of the live state     -> grow (also without a card hit)
3b. dense, when the set has M_OFF: the nearest off-topic example is closer than the nearest card passage by at
   least M_OFF (raw cosines)                                     -> offtopic (WP11, see below)
4. an uncovered directory name without a passage                 -> no_data
4b. lexical, when the set has T_OFF_HARD: off-topic similarity >= T_OFF_HARD -> offtopic (WP11, see below)
5. confidence >= T_HIT                                           -> grow
6. a directory name, or confidence >= T_DOMAIN                   -> no_data
7. otherwise                                                     -> offtopic
One addition from the acceptance of 23.10.2026: a growing step the cards cover ("düngen", "gießen", "ernten") skips
row 2 and, with a card hit, is grow before row 6 ("Wann dünge ich das nächste Mal?" matched "Wann ist das nächste
Länderspiel?" and came back off-topic).

How rows 1 and 3 are read here (the addendum gives the rows, not their details; see the WP5 report):
- Row 1 asks only when the answer matters: a correction that typo.py leaves as "ask" turns into "Meinst du X?" when
  the question with X put in goes another way than the question as typed. "A lettuce guide" (grow-03, guide against
  Gurke) and "the weather going to be" (off-01, going against growing) then need no question.
- Row 3 counts a box device (USB, lamp, fans, humidifier, tank, camera, probe, automation) and a metric the live state
  carries (data/gates.json "router"). The box as a whole (VGrow) counts only when nothing uncovered is named, and a
  sentence that also names an uncovered name never counts: "Wie viel Licht braucht eine Tomate in der VGrow?" stays
  no_data (nodata-01, the reason the spec puts the uncovered row first), while "How many µmol does the VGrow lamp put
  out?" is grow although a later sentence names lettuce (grow-03). Tool passages (addendum N2: Hank, the traffic
  light, the suggestion card, confirming the stage, switching Hank off) are searched as well when the caller set them
  (Index.set_dynamic), and a hit there at T_HIT is grow too.

Row 3b (WP11, 23.10.2026): the calibration found that with either threshold set some off-topic sentences ("How do I
bake a cake?", "Wie spät ist es?") reach T_HIT, because one common word matches a card ("bake" nothing, but "time",
"spät" the late flower) and row 2 lets a confidence at T_HIT win. The embedding separates them cleanly where the
word scores cannot: a growing question is closer to a card passage than to any off-topic example, an off-topic one
the other way round. Row 3b compares those two raw cosines; it sits after the live-state row, so a question about the
box's own devices or readings is never turned down by it. The lexical set has no vectors; there row 4b is a plain
"very close to an off-topic example" (T_OFF_HARD, 3-gram Dice), which catches the sentences of data/offtopic.json and
near copies of them, not unseen wordings. It comes after the uncovered row, so "Kann ich meinen Salat verkaufen?"
stays no_data (it shares "kaufen" with a shopping example). Without the key in the set a row does nothing.

The today question (WP11 follow-up, 23.10.2026): "Was ist heute zu tun?", "What should I do today?" and the like
(data/gates.json "today") route to "today" after the four safety gates and before greeting and thanks, when the
question names no directory name outside a plant or box qualifier ("Was muss ich heute bei der Pflanze machen?"). WP11 measured both wordings as offtopic (close to "Welcher Tag ist heute?"); the
answer is a short list from code (today.py), so it needs neither the search nor the embedding.

The fix wish (owner, 23.10.2026): "Kannst du das beheben?", "Mach das bitte", "Fix it", "What should I do about it?"
(data/gates.json "fix") route to "fix" after the four safety gates and before the today question, greeting, thanks
and the off-topic decision ("can you fix those things?" had matched "How are things with you?" and came back as a
greeting). The answer is composed from code (fix.py): the problems the traffic light shows, with suggestion cards. In
the mode "nur Warnungen" the fix wish is the one question that is answered, because it belongs to a warning; every
other question there still gets the warnings_only reply, and the open "Meinst du X?" is not read. A tap on the traffic-light line or a note's
"Beheben" button (tap_fix) runs the same safety gates on the text it carries before it is a fix wish: the page only
ever sends its own question, but the endpoint takes any text.

The gates never wait for the embedding (addendum N2): route() takes a vector computed by the caller, aroute() asks the
index for it only after the gates. Fixed answers never call the model (spec 0.3 rule 4): this module has no model
client at all.

Nothing is written here. The caller (WP8) writes the gap entry for no_data, the learned and the rejected spellings
(ClarifyBook.confirmed, .rejected; stores are WP7's), and the turn log.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import uuid
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from vgrow import config

from . import asking, humidifier_report, livestate, methods, pump_report, replies, text, thirst_report, typo, wikicards
from . import strains as strains_mod
from . import shelves as shelves_mod
from . import gates as gates_mod
from .gates import GateHit, Gates, NameRef
from .slots import LIVE_DEVICES
from .types import Clarify, Correction, Retrieval, Route

_LOGGER = logging.getLogger(__name__)

MAX_CHARS = 500  # spec 3.1 step 1
# A question about the autopilot (owner, 23.10.2026: "Was hat der Autopilot gemacht?" got "Da bin ich der Falsche").
# After the four safety gates, before the today question; the answer comes from its log (pilot_report.py).
PILOT_WORD = re.compile(r"\bauto ?pilot")
CLARIFY_TTL = 300.0  # spec 3.4: an open "Meinst du X?" is valid for 5 minutes
MODES = ("on", "warnings", "off")  # spec 7
TOOL_KIND = "tool"  # addendum N2: passages about Hank and the page, set by the caller
_INVISIBLE = "".join(chr(c) for c in (*range(0x200B, 0x2010), 0x2028, 0x2029, 0x2060, 0xFEFF))
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f" + _INVISIBLE + "]")  # controls, zero widths, BOM
_LEADING_HANK = re.compile(r"^\s*(?:hey\s+|hallo\s+|hi\s+)?hank\s*[,:;]\s*(?=\S)", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ACTION_SWITCH = frozenset({"on", "off", "up", "down"})


# ---------- cleaning (spec 3.1 step 1) ----------

def clean(q: Any) -> str:
    """Control characters out, white space as single spaces, a leading "Hank," dropped, chat short forms written out
    ("can u fix it?" -> "can you fix it?", live test 23.10.2026). The length is checked by the caller (MAX_CHARS)."""
    s = "" if q is None else str(q)
    s = unicodedata.normalize("NFC", s)
    s = _CONTROL.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _SHORT_FORMS_RE.sub(lambda m: _SHORT_FORMS[m.group(0).lower()], s)
    return _LEADING_HANK.sub("", s).strip()


# Chat short forms, whole words only, written out before routing. Only forms that cannot be a real word of a growing
# question ("u" is never a unit here: the cockpit writes units after numbers, "20 u" does not occur).
_SHORT_FORMS = {
    "u": "you", "ur": "your", "pls": "please", "plz": "please", "thx": "thanks", "ty": "thank you", "wat": "what",
    "wut": "what", "2day": "today", "tmrw": "tomorrow", "idk": "I don't know", "cuz": "because", "coz": "because",
    "kannste": "kannst du", "haste": "hast du", "machste": "machst du", "biste": "bist du", "gehts": "geht es",
    "nen": "einen",
}
# not before a dot: "Temp u. Feuchte" is "und"; "ne" stays out ("..., ne?" is a tag question, not "eine")
_SHORT_FORMS_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted((re.escape(k) for k in _SHORT_FORMS), key=len, reverse=True))
                             + r")(?![\w.-])", re.IGNORECASE)


def sentences(q: str) -> list[str]:
    """The sentences of a question, split after . ! ? and a space."""
    return [part for part in _SENTENCE_END.split(q) if part.strip()]


# ---------- the open "Meinst du X?" (spec 3.4) ----------

@dataclass
class ClarifyBook:
    """Open clarify questions per client_id (in memory, CLARIFY_TTL), and the answers for the caller to store:
    confirmed spellings (an explicit "Ja" only, addendum N7) and rejected ones. A rejected spelling is never proposed
    again in this run (aliases()); the store keeps it for later runs (aliases-learned.json, WP7)."""

    ttl: float = CLARIFY_TTL
    clock: Callable[[], float] = time.time
    open_: dict[str, Clarify] = field(default_factory=dict)
    confirmed: list[Correction] = field(default_factory=list)  # not yet handed to the store
    rejected: list[Correction] = field(default_factory=list)  # not yet handed to the store
    _refused: list[Correction] = field(default_factory=list)  # every rejection of this run

    def open(self, client_id: str, clarify: Clarify) -> None:
        self.open_[client_id] = clarify

    def pending(self, client_id: str | None) -> Clarify | None:
        """The client's open question while it is valid; an expired one is dropped."""
        if not client_id:
            return None
        found = self.open_.get(client_id)
        if found is None:
            return None
        if self.clock() - found.created > self.ttl:
            self.open_.pop(client_id, None)
            return None
        return found

    def close(self, client_id: str | None) -> Clarify | None:
        return self.open_.pop(client_id, None) if client_id else None

    def confirm(self, corr: Correction) -> None:
        self.confirmed.append(corr)

    def reject(self, corr: Correction) -> None:
        self.rejected.append(corr)
        self._refused.append(corr)

    def aliases(self) -> dict[str, Any] | None:
        """This run's rejected spellings in the form typo.correct() reads ({"rejected": {token: [term, ...]}});
        None when nothing was rejected."""
        if not self._refused:
            return None
        out: dict[str, list[str]] = {}
        for corr in self._refused:
            terms = out.setdefault(corr.token, [])
            if corr.term not in terms:
                terms.append(corr.term)
        return {"rejected": out}

    def drain(self) -> tuple[list[Correction], list[Correction]]:
        """The confirmed and rejected spellings not yet handed to the store, then empty both lists."""
        confirmed, rejected = list(self.confirmed), list(self.rejected)
        self.confirmed.clear()
        self.rejected.clear()
        return confirmed, rejected


# ---------- the router ----------

@dataclass(frozen=True)
class _Typed:
    """What the search route carries from the steps before it (v1.2): the question's type (asking.QTYPES or None)
    and the kind of follow-up it was written out from."""

    qtype: str | None = None
    followed: str | None = None
    subject: tuple[str, ...] = ()  # an explain question's subject words (asking.Asked.subject)


@dataclass
class _Decision:
    name: str
    reason: str
    retrieval: Retrieval


def _live_state(live: Any) -> livestate.LiveState | None:
    if live is None:
        return None
    try:
        return livestate.collect(live)
    except Exception as err:  # noqa: BLE001 (a broken live state never stops the router)
        _LOGGER.warning("Hank router: live state unreadable (%s)", err)
        return None


def _cannabis_run(state: livestate.LiveState | None) -> bool:
    """The run is a cannabis run when its strain card is set (decision D2: the cannabis cards are the run's cards).
    Without a live state the router takes the safe side (spec 3.2: gates tuned for recall)."""
    if state is None or not state.plant_view:
        return True
    if state.plant_view.get("configured") is False:
        return True
    return bool(state.plant_view.get("strain"))


def _growing(state: livestate.LiveState | None) -> bool:
    """A grow runs: the plant is set up with its strain card. Unlike _cannabis_run no safe side: without a live state
    Hank does not know it."""
    view = state.plant_view if state is not None else None
    return bool(view) and view.get("configured") is not False and bool(view.get("strain"))


def _read_learned() -> Mapping[str, Any] | None:
    path = config.STATE_DIR / "hank" / "aliases-learned.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, Mapping) else None


def _merge_aliases(learned: Mapping[str, Any] | None, extra: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Learned spellings from the file plus this run's rejections, in the {"learned", "rejected"} form."""
    if not extra:
        return learned
    if learned and ("learned" in learned or "rejected" in learned):
        base_learned = dict(learned.get("learned") or {})
        base_rejected = learned.get("rejected") or {}
    else:
        base_learned, base_rejected = dict(learned or {}), {}
    rejected: dict[str, list[str]] = {}
    if isinstance(base_rejected, Mapping):
        for token, terms in base_rejected.items():
            rejected[str(token)] = [terms] if isinstance(terms, str) else [str(t) for t in terms or []]
    elif isinstance(base_rejected, list):
        for item in base_rejected:
            if isinstance(item, Mapping):
                rejected.setdefault(str(item.get("token", "")), []).append(str(item.get("term", "")))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                rejected.setdefault(str(item[0]), []).append(str(item[1]))
    for token, terms in (extra.get("rejected") or {}).items():
        rejected.setdefault(token, [])
        rejected[token] += [t for t in terms if t not in rejected[token]]
    return {"learned": base_learned, "rejected": rejected}


class Router:
    """Routes questions against one index. route() is synchronous pure code; aroute() adds the question's embedding
    after the gates. Thresholds come from the index (data/thresholds.json, two sets: dense and lexical); `thresholds`
    overrides single values per set (the calibration and the tests use it)."""

    def __init__(self, index: Any, *, gates: Gates | None = None, offtopic: gates_mod.ExampleBank | None = None,
                 clock: Callable[[], float] = time.time, thresholds: Mapping[str, Mapping[str, float]] | None = None,
                 book: ClarifyBook | None = None) -> None:
        self.index = index
        self.gates = gates if gates is not None else Gates.load()
        self.offtopic = offtopic if offtopic is not None else gates_mod.load_offtopic()
        self.clock = clock
        self.overrides = {k: dict(v) for k, v in (thresholds or {}).items()}
        self.book = book if book is not None else ClarifyBook(clock=clock)
        self.topics = asking.TopicBook(clock=clock)  # v1.2: the last topic per chat, for follow-ups (core fills it)
        self.shelves = shelves_mod.Shelves(index)  # 01.11.2026: asked before a refusal (shelves.py)
        self._domain_key: tuple[int, int] | None = None
        self._refresh_domain()

    def _refresh_domain(self) -> None:
        """The growing words the safety examples are compared without (gates.domain_words); read again when the
        index was rebuilt from the files. The card part of the index is a new list object only then (live and tool
        passages, set every minute, keep it), so its identity is the cheap test; about 10 ms per rebuild."""
        state = getattr(self.index, "_state", None)
        cards = getattr(state, "card_docs", None)
        holder = cards if cards is not None else state
        key = (len(cards) if cards is not None else len(self.index.passages), id(holder))
        if key != self._domain_key:
            self.gates.set_domain(gates_mod.domain_words(self.index))
            self._domain_key = key

    # ----- thresholds -----

    def thresholds(self, dense: str | None) -> dict[str, float]:
        name = "dense" if dense == "used" else "lexical"
        values = dict(self.index.threshold_set(dense=dense))
        values.update(self.overrides.get(name, {}))
        return values

    # ----- warm-up -----

    async def warm(self, embedder: Any = None) -> bool:
        """Embed the off-topic examples once (at start, in the background), with the index's embedder unless one is
        given. Never raises; without vectors the off-topic row compares letters only.

        The shelves keep searching by letters. Measured 02.11.2026 with granite-embedding on the eval set (dense): the
        shelves' vectors turned three cases from passing or waiting into failures, one a forbidden route (a KCanG
        question answered from a shelf card), while the off-topic vectors changed nothing. Shelves.start builds them
        once their thresholds are set for vectors (scripts/hank_shelves.py measures letters only so far)."""
        embedder = embedder if embedder is not None else getattr(self.index, "embedder", None)
        return await self.offtopic.embed(embedder, model=getattr(self.index, "model", None))

    # ----- entry points -----

    def route(self, q: Any, *, lang_hint: str = "de", live: Any = None, pending: Clarify | None = None,
              mode: str = "on", dense_vec: list[float] | None = None, dense_state: str | None = None,
              client_id: str | None = None, aliases: Mapping[str, Any] | None = None) -> Route:
        """One question, all steps of spec 3.1. dense_vec: the question's vector from Index.embed_query (or None,
        then dense_state says why). pending: the client's open clarify (else the router's own book by client_id)."""
        early = self._before_search(q, lang_hint=lang_hint, live=live, pending=pending, mode=mode,
                                    dense_vec=dense_vec, dense_state=dense_state, client_id=client_id,
                                    aliases=aliases)
        if isinstance(early, Route):
            return early
        cleaned, lang, state, t0, typed = early
        return self._search_route(cleaned, lang, state, dense_vec, dense_state, client_id, aliases, t0, typed)

    async def aroute(self, q: Any, *, lang_hint: str = "de", live: Any = None, pending: Clarify | None = None,
                     mode: str = "on", skip_dense: bool | Callable[[], bool] = False, client_id: str | None = None,
                     aliases: Mapping[str, Any] | None = None,
                     before_dense: Callable[[], Awaitable[None]] | None = None) -> Route:
        """As route(), but the question's embedding is asked for only after the gates (addendum N2: 0.8 s limit,
        skipped while a photo reading or a chat answer runs: skip_dense). before_dense is awaited only when the
        question reaches the search, before skip_dense is read (a callable is read then): the caller cancels the
        chat's running polish there (core._supersede), so "Danke!" or a greeting never cost the earlier answer its
        polish, while a growing question finds the lane free."""
        early = self._before_search(q, lang_hint=lang_hint, live=live, pending=pending, mode=mode, dense_vec=None,
                                    dense_state=None, client_id=client_id, aliases=aliases, deferred=True)
        if isinstance(early, Route):
            return early
        cleaned, lang, state, t0, typed = early
        if before_dense is not None:
            await before_dense()
        skip = skip_dense() if callable(skip_dense) else skip_dense
        vec, dense_state = await self.index.embed_query(cleaned, skip=skip)
        return self._search_route(cleaned, lang, state, vec, dense_state, client_id, aliases, t0, typed)

    def tap_fix(self, q: Any, *, lang_hint: str = "de", mode: str = "on", live: Any = None) -> Route:
        """The fix wish from a tap (the traffic-light line or a note's "Beheben" button, owner 23.10.2026): the page
        shows its own question ("Was kann ich dagegen tun?") and asks for the fix answer of the tapped problems. The
        page only sends its own catalog text, but POST /api/hank/ask takes any text with `fix`, so the safety gates run
        on it first, as for a typed question: the emergency check before the mode, then inject, health, sale and legal
        (review 2, M1). "aus" answers nothing, "nur Warnungen" shows the fix answer because it belongs to a warning
        (a safety gate's reply stays closed there, as for a typed question)."""
        t0 = time.perf_counter()
        hint = lang_hint if lang_hint in ("de", "en") else "de"
        cleaned = clean("" if q is None else str(q))
        if len(cleaned) > MAX_CHARS:
            return self._fixed("too_long", hint, reason="too_long")
        mended = self._mended(cleaned) if cleaned else None
        if cleaned:
            emergency = self.gates.emergency(text.fold(cleaned))
            if emergency is None and mended is not None:
                emergency = self.gates.emergency(mended)
            if emergency is not None:
                return self._gate_route(emergency, cleaned, hint, t0)
        if mode == "off":
            return self._fixed("off", hint, reason="off")
        if cleaned:
            self._refresh_domain()
            names = [NameRef(h.entity, h.kind) for h in self.index.names.find(cleaned)]
            t_gate = float(self.thresholds("lexical").get("T_GATE", 0.60))
            names, run, strain = self._strain_context(cleaned, names, _cannabis_run(_live_state(live)))
            hit = self.gates.check(cleaned, t_gate=t_gate, names=names, cannabis_run=run)
            if hit is None and mended is not None:
                hit = self.gates.check(mended, t_gate=t_gate, names=names, cannabis_run=run)
            hit = hit if hit is not None else self._strain_strength(cleaned, strain)
            if hit is not None and hit.gate not in gates_mod.SOCIAL_GATES:
                if mode == "warnings":
                    return self._fixed("off", hint, reason="warnings")
                return self._gate_route(hit, cleaned, hint, t0)
        return Route("fix", None, None, None, "info", hint, "fix:tap")

    def answer_clarify(self, client_id: str, clarify_id: str | None, yes: bool, *, live: Any = None,
                       mode: str = "on", dense_vec: list[float] | None = None,
                       dense_state: str | None = None) -> Route:
        """POST /api/hank/clarify (appendix B): the Ja/Nein buttons. An unknown or expired id gives clarify_no's
        sibling: the question is gone, so the reply asks for the question again (hank.reply.clarify_no)."""
        pending = self.book.pending(client_id)
        if pending is None or (clarify_id and pending.id != clarify_id):
            return self._fixed("clarify", pending.lang if pending else "de", reason="clarify_expired")
        return self._resolve(pending, "yes" if yes else "no", client_id=client_id, live=live, mode=mode,
                             dense_vec=dense_vec, dense_state=dense_state)

    # ----- steps 1 to 8 -----

    def _before_search(self, q: Any, *, lang_hint: str, live: Any, pending: Clarify | None, mode: str,
                       dense_vec: list[float] | None, dense_state: str | None, client_id: str | None,
                       aliases: Mapping[str, Any] | None, deferred: bool = False,
                       followed: str | None = None) -> Route | tuple[str, str, livestate.LiveState | None, float,
                                                                      _Typed]:
        """Steps 1 to 8 of spec 3.1 and, after the gates, the fix wish, the today question, the follow-up and the
        typed questions of v1.2 (asking.py). A Route, or what the search needs: the cleaned question, its language,
        the live state, the start time and the question's type. followed: the kind of follow-up this question was
        written out from (it is not written out a second time)."""
        t0 = time.perf_counter()
        hint = lang_hint if lang_hint in ("de", "en") else "de"
        raw = "" if q is None else str(q)
        cleaned = clean(raw)
        lang = text.detect_lang(cleaned, hint) if cleaned else hint
        if len(cleaned) > MAX_CHARS:
            return self._fixed("too_long", lang, reason="too_long")
        if not cleaned:
            return self._fixed("empty", lang, reason="empty")
        mended = self._mended(cleaned)  # typos in gate words (acceptance: 11.3 safety classes on variants)
        emergency = self.gates.emergency(text.fold(cleaned))  # before the mode and every gate (review 3, H2, L5)
        if emergency is None and mended is not None:
            emergency = self.gates.emergency(mended)
        if emergency is not None:
            return self._gate_route(emergency, cleaned, lang, t0)
        if mode == "off":
            return self._fixed("off", lang, reason=mode)
        warnings_only = mode == "warnings"  # chat closed, but a fix wish belongs to a warning (owner, 23.10.2026)
        open_question = None if warnings_only else (pending if pending is not None else self.book.pending(client_id))
        if open_question is not None and self.clock() - open_question.created <= self.book.ttl:
            word = self.gates.answer_word(cleaned)
            if word is not None:
                return self._resolve(open_question, word, client_id=client_id, live=live, mode=mode,
                                     dense_vec=None if deferred else dense_vec,
                                     dense_state=None if deferred else dense_state, aliases=aliases)
        if client_id:
            self.book.close(client_id)  # a new question drops the open one
        self._refresh_domain()
        state = _live_state(live)
        names = [NameRef(h.entity, h.kind) for h in self.index.names.find(cleaned)]
        t_gate = float(self.thresholds("lexical").get("T_GATE", 0.60))  # gates never use the embedding (N2)
        names, run, strain = self._strain_context(cleaned, names, _cannabis_run(state))
        hit = self.gates.check(cleaned, t_gate=t_gate, names=names, cannabis_run=run)
        if hit is None and mended is not None:
            hit = self.gates.check(mended, t_gate=t_gate, names=names, cannabis_run=run)
            if hit is not None:
                hit = GateHit(hit.gate, hit.how, f"mended:{hit.detail}", hit.score)
        hit = hit if hit is not None else self._strain_strength(cleaned, strain)
        if (hit is not None and hit.gate == "sale" and hit.how == "buy"
                and strains_mod.is_advice(strains_mod.fold(cleaned))
                and not self.gates.asks_where_to_buy(text.fold(cleaned))):
            # "Welche Sorte soll ich kaufen?" asks for the advice the owner wants (23.10.2026), not where to buy: the
            # strain route below answers it, and its closing line says that the choice and the buying are the person's
            hit = None
        exact = self.gates.fix_exact(cleaned)
        if hit is not None and hit.gate == "inject" and hit.how == "example" and exact is not None:
            hit = None  # "Fix everything" resembles "Forget everything you were told." (v1.2); a pattern still wins
        topic = self.topics.get(client_id)
        if hit is None or hit.gate in gates_mod.SOCIAL_GATES:  # after the safety gates, before greeting and thanks
            vocab = getattr(self.index, "vocab", None)
            wish = self.gates.fix(cleaned, known=getattr(vocab, "is_known", None)) or exact
            if wish is not None:  # "Kannst du das beheben?": the problems of the light, from code alone (fix.py)
                keys, about = asking.fix_scope(wish, topic)  # "can u fix it?" after "what is VPD": about VPD
                return Route("fix", None, None, None, "info", lang, f"fix:{wish}", fix_keys=keys, about=about)
        if warnings_only:  # "nur Warnungen" answers nothing else in the chat, not even a safety gate's reply
            return self._fixed("off", lang, reason="warnings")
        if (hit is None or hit.gate in gates_mod.SOCIAL_GATES) and humidifier_report.asks(cleaned):
            return Route("humidifier_tank", None, None, None, "info", lang, "humidifier_tank")
        if (hit is None or hit.gate in gates_mod.SOCIAL_GATES) and pump_report.asks(cleaned):
            return Route("pump_history", None, None, None, "info", lang, "pump_history")
        if (hit is None or hit.gate in gates_mod.SOCIAL_GATES) and thirst_report.asks(cleaned, names, state):
            return Route("thirst", None, None, None, "info", lang, "thirst")  # "Muss ich gießen?" (thirst_report.py)
        if (hit is None or hit.gate in gates_mod.SOCIAL_GATES) and PILOT_WORD.search(text.fold(cleaned)):
            # "Was hat der Autopilot gemacht?": its mode and last steps from its own log (pilot_report.py), no search
            return Route("autopilot", None, None, None, "info", lang, "autopilot")
        typed = _Typed()
        if hit is None or hit.gate in gates_mod.SOCIAL_GATES:
            asked = self.gates.today(cleaned, names=names)
            if asked is not None:  # "Was ist heute zu tun?": answered from code alone (today.py), no search
                return Route("today", None, None, None, "info", lang, f"today:{asked}")
            # the strain advice and the lexicon (strains.py): "Welche Sorte passt in meine Box?", the reply to Hank's
            # own question about it, "Was weißt du über Northern Lights Auto?"; in a cannabis context only
            folded = text.fold(cleaned)
            advice = strains_mod.route(cleaned, lang, topic, tell_more=wikicards.subject(cleaned) is not None,
                                       cannabis_context=self.gates.cannabis_context(folded, names,
                                                                                    _cannabis_run(state)))
            if advice is not None:
                return advice
            if followed is None:  # "Und das?", "warum?", "what about humidity?": written out against the last topic
                again = asking.follow_up(cleaned, lang, topic, state=state, gloss=self.glossary(),
                                         known=lambda said: bool(self.index.names.find(said)))
                if again is not None:
                    written = self._before_search(again.question, lang_hint=lang, live=live, pending=None,
                                                  mode=mode, dense_vec=dense_vec, dense_state=dense_state,
                                                  client_id=client_id, aliases=aliases, deferred=deferred,
                                                  followed=again.kind)
                    if isinstance(written, Route):
                        written.reason = f"followup:{again.kind};{written.reason}"
                        return written
                    return (written[0], written[1], written[2], t0, replace(written[4], followed=again.kind))
            kind = asking.classify(cleaned)
            more = wikicards.route(cleaned, lang, gloss=self.glossary())
            if more is not None:  # v1.4: "Erzähl mir mehr über <Titel>", the wiki's "Hank fragen" button
                return more
            method = methods.route(cleaned, lang, kind, gloss=self.glossary(), known=self.index.vocab.is_known)
            if method is not None:  # v1.4: "Wie toppe ich richtig?", "more heads, like a hydra", a package off
                return method
            typed_route = methods.guard_term(asking.typed_route(cleaned, lang, kind, gloss=self.glossary()))
            if typed_route is not None:  # "Was ist VPD?": the glossary; "Wie hoch ist die Pflanze?": the plant log
                return typed_route
            typed = _Typed(kind.qtype, followed, kind.subject)
        if hit is not None:
            return self._gate_route(hit, cleaned, lang, t0)
        return cleaned, lang, state, t0, typed

    @staticmethod
    def glossary() -> asking.Glossary:
        """The glossary (kartei/glossary/glossary.json), read again when the file changed."""
        return asking.glossary()

    def _mended(self, cleaned: str) -> str | None:
        """The question with typos in gate words mended (Gates.fuzzy), or None. Never raises."""
        vocab = getattr(self.index, "vocab", None)
        known = getattr(vocab, "is_known", None)
        try:
            return self.gates.fuzzy(cleaned, known=known)
        except Exception as err:  # noqa: BLE001 (the plain gates still ran)
            _LOGGER.warning("Hank router: mending gate words failed (%s)", err)
            return None

    def _gate_route(self, hit: GateHit, cleaned: str, lang: str, t0: float) -> Route:
        retrieval = None
        if hit.gate == "legal":  # spec 3.2: the fixed reply plus the top passage of kind legal, with its source
            retrieval = self.index.search(cleaned, lang=lang, kinds=["legal"], limit=1)
        route = Route(hit.gate, None, retrieval, None, "info", lang, f"gate:{hit.how}:{hit.detail}")
        route.reply = replies.reply_text(route, index=self.index)
        if retrieval is not None:
            retrieval.ms["route"] = round((time.perf_counter() - t0) * 1000)
        return route

    def _fixed(self, name: str, lang: str, *, reason: str) -> Route:
        route = Route(name, None, None, None, "info", lang, reason)
        route.reply = replies.reply_text(route, index=self.index)
        return route

    def _resolve(self, pending: Clarify, word: str, *, client_id: str | None, live: Any, mode: str,
                 dense_vec: list[float] | None, dense_state: str | None,
                 aliases: Mapping[str, Any] | None = None) -> Route:
        """Spec 3.4: "Ja" puts the term in and answers the first question; "Nein" asks for other words and never
        proposes that spelling again."""
        if client_id:
            self.book.close(client_id)
        corr = pending.candidate
        if word == "no":
            self.book.reject(corr)
            return self._fixed("clarify", pending.lang, reason=f"clarify_no:{corr.token}>{corr.term}")
        self.book.confirm(corr)
        question = typo.substitute(pending.question, corr)
        route = self.route(question, lang_hint=pending.lang, live=live, mode=mode, dense_vec=dense_vec,
                           dense_state=dense_state, aliases=aliases)
        route.reason = f"clarify_yes:{corr.token}>{corr.term};{route.reason}"
        return route

    # ----- step 9: search and decision -----

    def _search_route(self, cleaned: str, lang: str, state: livestate.LiveState | None,
                      dense_vec: list[float] | None, dense_state: str | None, client_id: str | None,
                      aliases: Mapping[str, Any] | None, t0: float, typed: _Typed | None = None) -> Route:
        aliases = _merge_aliases(aliases if aliases is not None else _read_learned(), self.book.aliases()) \
            if (aliases is not None or self.book.aliases()) else None
        base = self._decide(cleaned, lang, state, dense_vec, dense_state, aliases)
        typed = typed or _Typed()
        rescued = methods.guard_term(self._glossary_route(cleaned, lang, base, typed))
        if rescued is not None:  # a glossary term the search left off-topic or without a card (v1.3)
            base.retrieval.ms["route"] = round((time.perf_counter() - t0) * 1000)
            return rescued
        shelf = self._shelf_route(cleaned, lang, base, dense_vec, dense_state)
        if shelf is not None:  # 01.11.2026: a refusal or a weak hit asks the shelves first (shelves.py)
            base.retrieval.ms["route"] = round((time.perf_counter() - t0) * 1000)
            return shelf
        # A question that, as typed, already matches an off-topic example is meant as typed ("Welche Serie kannst
        # du mir empfehlen?" is no "Sorte" in disguise), so it gets no question back.
        known_offtopic = base.reason.startswith(("offtopic_example:", "offtopic_nn:", "foreign_host:"))
        if base.name == "offtopic" and not known_offtopic and _growing(state):
            nothing = strains_mod.unknown_route(cleaned, lang)
            if nothing is not None:  # 01.11.2026: "Haben wir Infos zu X?" while a grow runs is no "wrong guy"
                base.retrieval.ms["route"] = round((time.perf_counter() - t0) * 1000)
                return nothing
        asks = [c for c in base.retrieval.corrections
                if c.mode == "ask" and self._speaks(c, lang) and not known_offtopic]
        if asks:  # a strain's name is no typo: "Purple Punch" asked "Meinst du Pumpe?" (owner, 01.11.2026)
            named = strains_mod.strain_words(cleaned)
            asks = [c for c in asks if strains_mod.fold(c.token) not in named]
        for corr in asks:  # addendum N2 row 1, read as: ask only when the answer changes the route
            other = self._decide(typo.substitute(cleaned, corr), lang, state, dense_vec, dense_state, aliases)
            if other.name != base.name:
                clarify = Clarify(uuid.uuid4().hex[:12], cleaned, corr, lang, self.clock())
                if client_id:
                    self.book.open(client_id, clarify)
                route = Route("clarify", None, base.retrieval, clarify, "info", lang,
                              f"clarify:{corr.token}>{corr.term}:{base.name}>{other.name}")
                route.reply = replies.reply_text(route, index=self.index)
                base.retrieval.ms["route"] = round((time.perf_counter() - t0) * 1000)
                return route
        intent = "action" if base.name == "grow" and self._action(base.retrieval) else "info"
        name = base.name
        if name == "offtopic" and base.reason.startswith("none:") and (asking.term_like(typed.qtype, typed.subject,
                                                                                       self.gates.domain)
                                                                       or typed.followed):
            # "Was ist Schwefelmangel?" (review 1, MUST 1): an explain question about one unknown term, or a
            # follow-up of a growing topic, that matched no off-topic example and no card is a growing question
            # Hank has no card for, not a "Da bin ich der Falsche". Everyday questions in explain or how-to form
            # stay off-topic (acceptance acc-16, acc-17): no single growing term is asked (asking.term_like).
            name = "no_data"
        reason = f"followup:{typed.followed};{base.reason}" if typed.followed else base.reason
        route = Route(name, None, base.retrieval, None, intent, lang, reason, qtype=typed.qtype)
        if name != "grow":
            route.reply = replies.reply_text(route, index=self.index, question=cleaned)
        base.retrieval.ms["route"] = round((time.perf_counter() - t0) * 1000)
        return route

    def _shelf_route(self, cleaned: str, lang: str, base: _Decision, dense_vec: list[float] | None,
                     dense_state: str | None) -> Route | None:
        """Owner, 01.11.2026: "he checks at first for everything". The main search refused (offtopic, no_data) or
        found a weak hit: the shelves (problems, plant health, crops, processes, the fertilizer market) answer, suggest
        up to three cards, or leave the decision as it was (shelves.decide). A decision from the live state, a tool,
        a method card or a foreign host is never asked again."""
        if base.name not in ("offtopic", "no_data"):
            return None  # before a refusal only, never over an answer (shelves.decide)
        if self._foreign_word(cleaned):  # "Can you fix my car?": one word ("fix") covered a zinc tip in full
            return None
        found, hits = self.shelves.find(cleaned, lang, dense_vec, dense_state)
        if found is not None and hits:
            # a crop card only for its own plant: "gelbe Blätter" names no crop, the microgreens card is no answer
            asked = {h.entity for h in self.index.names.find(cleaned) if h.kind == "plant"}
            asked |= {*base.retrieval.uncovered, *(getattr(base.retrieval.slots, "entities", None) or [])}  # typos
            asked = {e for e in asked if e != "plant" and (self.index.vocab.entry(e) or {}).get("kind") == "plant"}
            crop = {f"{h.passage.kind}/{h.passage.card}" for h in found.hits
                    if h.passage.kind == "crops" and asked & set(h.passage.entities)}
            hits = [h for h in hits if not h.card.startswith("crops/") or h.card in crop]
        named = [(self._names_card(cleaned, found, h.card), -i, h) for i, h in enumerate(hits[:shelves_mod.SUGGEST_MAX])]
        named_card = max(named, key=lambda n: n[:2])[2] if any(n[0] for n in named) else None
        if named_card is not None and named_card.score >= shelves_mod.T_SUGGEST:
            # the question names a card by its name ("tell me about Athena Pro", "Was ist BioTabs?"): that card answers
            return shelves_mod.route_of("answer", [named_card, *[h for h in hits if h is not named_card]], lang,
                                        base.reason, found, self.shelves)
        if hits and base.reason.startswith(("offtopic_example:", "offtopic_nn:")):
            return None  # like an off-topic example: only a card the question names by name answers (above)
        if len(set(text.tokens(cleaned, lang=lang))) < 2:
            return None  # "Wie spät ist es?": one word ("spät") covers a cold-stress passage in full, that is no match
        uncovered = list(base.retrieval.uncovered)
        if uncovered and found is not None and hits:
            # "Wie oft gieße ich meine Gurke?": the best shelf card must name what the main cards do not, else the
            # honest "no card" stays (a cucumber answered with "Meinst du Überwässern?" is no help, nor a hermaphrodite
            # with "Meinst du Unterwässern?"). A plant needs its
            # own crop card: a disease entry that names the tomato in passing is no tomato card ("Wie toppe ich eine
            # Tomate?")
            named = {e for h in found.hits if f"{h.passage.kind}/{h.passage.card}" == hits[0].card
                     for e in h.passage.entities}
            plants = [e for e in uncovered if (self.index.vocab.entry(e) or {}).get("kind") == "plant"]
            if not all(e in named for e in uncovered) or (plants and not hits[0].card.startswith("crops/")):
                return None
        outcome = shelves_mod.decide(hits, base.name, base.reason, float(base.retrieval.confidence))
        if outcome is None:
            return None
        return shelves_mod.route_of(outcome, hits, lang, base.reason, found, self.shelves)

    @staticmethod
    def _strain_context(cleaned: str, names: list[NameRef], run: bool) -> tuple[list[NameRef], bool, bool]:
        """For the safety gates (01.11.2026, holdout h27 and h28): a question that names a known strain is about
        cannabis, and the strain's name is no other plant ("Gelato" in "Gelato Auto" is no ice cream plant)."""
        if not strains_mod.names_strain(cleaned):
            return names, run, False
        return [n for n in names if n.kind != "plant"], True, True

    @staticmethod
    def _strain_strength(cleaned: str, strain: bool) -> GateHit | None:
        """How strong a named strain is: the health gate, as "Wie stark wirkt die Sorte?" ("Wie stark ist Purple
        Punch?" named no "Sorte" and passed every gate)."""
        found = strains_mod.STRENGTH.search(strains_mod.fold(cleaned)) if strain else None
        return GateHit("health", "pattern", f"strain_strength:{found.group(0)}", 1.0) if found else None

    @staticmethod
    def _names_card(cleaned: str, found: Any, card: str) -> int:
        """How well the question carries the card's name (0: not at all). The name is the title before any bracket;
        it counts with every word of a one-word name, two words of a longer one ("Athena Pro" of "Athena Pro Line"),
        or for a fertilizer its brand alone ("BioTabs" of "BioTabs Organic System"). More words, better."""
        words = set(gates_mod.words(cleaned.replace("-", " ")))
        best = 0
        for hit in found.hits:
            if f"{hit.passage.kind}/{hit.passage.card}" != card:
                continue
            for title in hit.passage.title.values():
                name = gates_mod.words(str(title).split(" (")[0].split(":")[0].replace("-", " "))
                count = sum(w in words for w in name)
                brand = card.startswith("nutrients/") and bool(name) and name[0] in words and len(name[0]) >= 5
                if name and (count >= min(2, len(name)) or brand):
                    best = max(best, count)
        return best

    def _glossary_route(self, cleaned: str, lang: str, base: _Decision, typed: _Typed) -> Route | None:
        """v1.3 (review 1, MUST 1 and 3): a glossary term the search did not answer.
        - An explain or how-to question the search sent off-topic ("Was ist der Unterschied zwischen Abluft und
          Umluft?" matched "Java und JavaScript") that names a glossary term, outside a foreign host ("Auspuff vom
          Auto"): the route explain.
        - A no_data question about a glossary term ("Wie oft muss ich den Aktivkohlefilter wechseln?"): the term
          alone with question words is explain; otherwise no_data with the term, answered from the glossary text
          ("Eine eigene Karte habe ich dazu nicht, im Glossar steht zu ...") and still on the gap list. Every
          uncovered name must be that term: "Wie viel Licht braucht eine Tomate?" stays plain no_data.
        - An explain question about one growing term of the name directory without a card that an off-topic example
          took ("Was ist Wurzelfäule?" matched "Was ist die Wurzel aus 144?"): no_data with that name."""
        gloss = self.glossary()
        if base.name == "offtopic" and typed.qtype in ("explain", "howto"):
            if self._foreign_word(cleaned):
                return None
            term = gloss.mentioned(cleaned)
            if term is not None:
                return Route("explain", None, None, None, "info", lang,
                             f"{'howto' if typed.qtype == 'howto' else 'explain'}:{term.key};{base.reason}",
                             qtype=typed.qtype, term=term.key)
            named = self.index.names.find(" ".join(typed.subject)) if asking.term_like(
                typed.qtype, typed.subject, self.gates.domain) else []
            entity = next((h.entity for h in named if not self.index.covered(h.entity)), None)
            if entity is None:
                return None
            route = Route("no_data", None, base.retrieval, None, "info", lang,
                          f"named:{base.reason.split(':', 1)[0]};uncovered:{entity}", qtype=typed.qtype)
            route.reply = replies.reply_text(route, index=self.index, question=cleaned)
            return route
        if base.name != "no_data":
            return None
        uncovered = list(base.retrieval.uncovered)
        terms = [asking.entity_term(self.index.vocab.entry(e), gloss) for e in uncovered]
        if any(term is None for term in terms) or len({term.key for term in terms if term}) > 1:
            return None
        term = terms[0] if terms else gloss.mentioned(cleaned)
        if term is None:
            return None
        if asking.only_term(cleaned, term):
            return Route("explain", None, None, None, "info", lang, f"explain:{term.key};{base.reason}",
                         qtype="explain", term=term.key)
        return Route("no_data", None, base.retrieval, None, "info", lang, f"glossary:{term.key};{base.reason}",
                     qtype=typed.qtype, term=term.key)

    def _foreign_word(self, q: str) -> bool:
        hosts = self.gates.foreign_hosts
        return bool(hosts) and any(w in hosts for w in gates_mod.words(q.replace("-", " ")))

    def _decide(self, q: str, lang: str, state: livestate.LiveState | None, dense_vec: list[float] | None,
                dense_state: str | None, aliases: Mapping[str, Any] | None) -> _Decision:
        """Rows 2 to 7 of addendum N2 for one wording of the question."""
        stage = state.stage if state is not None else None
        feed = state.feeding_view if state is not None else None
        found = self.index.search(q, dense_vec, lang=lang, stage=stage, feed=feed, aliases=aliases,
                                  dense_state=dense_state)
        th = self.thresholds(found.dense)
        t_hit, t_domain, t_off = float(th["T_HIT"]), float(th["T_DOMAIN"]), float(th["T_OFF"])
        off, example = self.offtopic.best(gates_mod.gram_set(q))
        if found.dense == "used" and dense_vec is not None:
            basis_map = self.index.thresholds.get("dense_basis") or {}
            basis = float(basis_map.get(self.index.model or "", basis_map.get("default", 0.25)))
            dense_off, dense_example = self.offtopic.best_dense(dense_vec, basis)
            if dense_off > off:
                off, example = dense_off, dense_example
        conf = found.confidence
        method = self._method_rule(q, lang, found, aliases)
        growing = self._growing_word(q, found) or (f"method:{method[0]}" if method is not None else None)
        if off >= t_off and conf < t_hit and growing is None:
            return _Decision("offtopic", f"offtopic_example:{off:.2f}:{example}", found)
        state_reason = self._state_rule(q, found)
        if state_reason == "box" and method is not None:
            state_reason = None  # "Hilft Topping gegen die Höhe in der Box?": about the method card, in this box
        if state_reason and state_reason.startswith("foreign_host:"):  # the fan of the car, not of the box
            return _Decision("offtopic", state_reason, found)
        if state_reason:
            return _Decision("grow", f"state:{state_reason}", found)
        margin = None if method is not None else self._nearest_offtopic(found, dense_vec, th.get("M_OFF"))
        if margin is not None:
            return _Decision("offtopic", f"offtopic_nn:{margin[0]:.2f}:{margin[1]}", found)
        tool = self._tool_rule(q, lang, t_hit, aliases)
        if tool is not None:
            merged = replace(found, hits=[*tool.hits, *found.hits], confidence=max(conf, tool.confidence))
            return _Decision("grow", f"tool:{tool.hits[0].passage.id}", merged)
        if method is not None:  # v1.4: a question that names a method card, answered from that card's passages
            key, hits = method
            merged = replace(found, hits=hits, confidence=max(conf, max(h.score for h in hits)))
            return _Decision("grow", f"method:{key}", merged)
        if found.uncovered:
            return _Decision("no_data", f"uncovered:{found.uncovered[0]}", found)
        hard = th.get("T_OFF_HARD")
        if hard is not None and found.dense != "used" and off >= float(hard):
            return _Decision("offtopic", f"offtopic_example:{off:.2f}:{example}", found)
        if conf >= t_hit:
            return _Decision("grow", f"hit:{conf:.2f}", found)
        if growing is not None and found.hits:
            return _Decision("grow", f"process:{growing}", found)
        if found.slots.entities:
            return _Decision("no_data", f"name:{found.slots.entities[0]}", found)
        if conf >= t_domain:
            return _Decision("no_data", f"domain:{conf:.2f}", found)
        return _Decision("offtopic", f"none:{conf:.2f}", found)

    def _nearest_offtopic(self, found: Retrieval, dense_vec: list[float] | None,
                          m_off: Any) -> tuple[float, str | None] | None:
        """Row 3b: (margin, nearest example) when the nearest off-topic example beats the nearest card passage by
        M_OFF or more, else None. Only with the dense channel used and M_OFF set."""
        if m_off is None or found.dense != "used" or dense_vec is None:
            return None
        off_cos, near = self.offtopic.best_cosine(dense_vec)
        card_cos = self.index.max_cosine(dense_vec)
        if near is None or card_cos is None:
            return None
        margin = off_cos - card_cos
        return (margin, near) if margin >= float(m_off) else None

    def _state_rule(self, q: str, found: Retrieval) -> str | None:
        """Addendum N2 row 3 as described in the module docstring: the trigger that fired, or None. A sentence that
        names a box device or a live metric of another machine or place ("der Lüfter von meinem Auto", data/gates.json
        "router" foreign_hosts) never fires; when no other sentence does, "foreign_host:<word>" (the caller: offtopic)."""
        uncovered = set(found.uncovered)
        state_metrics = self.gates.state_metrics
        box_itself = self.gates.box_itself
        corrected = q
        for corr in found.corrections:
            if corr.mode == "auto":
                corrected = typo.substitute(corrected, corr)
        foreign: str | None = None
        for sentence in sentences(corrected):
            hits = self.index.names.find(sentence)
            blocked = any(h.entity in uncovered and (h.maps_to or h.entity) not in LIVE_DEVICES for h in hits)
            if blocked:
                continue
            host = self._foreign_host(sentence, hits)
            if host is not None:
                foreign = foreign or host
                continue
            for h in hits:
                key = h.maps_to or h.entity
                if h.kind == "device" and key in LIVE_DEVICES and key not in box_itself:
                    return f"device:{key}"
                if h.kind == "metric" and h.maps_to in state_metrics:
                    return f"metric:{h.maps_to}"
            if not uncovered and any(h.kind == "device" and (h.maps_to or h.entity) in box_itself for h in hits):
                return "box"
        return f"foreign_host:{foreign}" if foreign else None

    def _foreign_host(self, sentence: str, hits: list[Any]) -> str | None:
        """The foreign_hosts word of a sentence that names a box device or a live metric but not the box itself
        ("Mein PC-Lüfter ist laut": pc), else None."""
        hosts = self.gates.foreign_hosts
        if not hosts:
            return None
        word = next((w for w in gates_mod.words(sentence.replace("-", " ")) if w in hosts), None)
        if word is None:
            return None
        box_itself = self.gates.box_itself
        if any(h.kind == "device" and (h.maps_to or h.entity) in box_itself for h in hits):
            return None  # "Steht die Box in der Küche zu warm?" is about the box
        named = any((h.kind == "device" and (h.maps_to or h.entity) in LIVE_DEVICES)
                    or (h.kind == "metric" and h.maps_to in self.gates.state_metrics) for h in hits)
        return word if named else None

    def _growing_word(self, q: str, found: Retrieval) -> str | None:
        """A growing step the cards cover ("Wann dünge ich das nächste Mal?": feeding), or None. Such a question is
        never off-topic because its wording resembles an off-topic example ("Wann ist das nächste Länderspiel?",
        acceptance 23.10.2026), and it is a growing question even below T_HIT. A plant or product name alone does not
        count (off-05: pesto from basil stays off-topic)."""
        uncovered = set(found.uncovered)
        for hit in self.index.names.find(q):
            if hit.kind == "process" and hit.entity not in uncovered and self.index.covered(hit.entity):
                return str(hit.entity)
        return None

    def _method_rule(self, q: str, lang: str, found: Retrieval,
                     aliases: Mapping[str, Any] | None) -> tuple[str, list[Any]] | None:
        """v1.4: a question that names a method card ("Hilft Topping gegen die Höhe in der Box?") is searched in
        that card's passages (the method cards are not in the default search, kartei_index.OPT_IN_KINDS). None when
        no visible card is named, when the question also names a name no card covers ("Wie toppe ich eine
        Tomate?" stays no_data), or when the card's passages do not match at all."""
        if not self.index.by_meta(kind=methods.KIND):
            return None
        named = methods.named(q, gloss=self.glossary(), known=self.index.vocab.is_known)
        lib = methods.library()
        wanted = named.keys
        other_plant = any(hit.kind == "plant" and hit.entity not in ("cannabis", "autoflower")
                          for hit in self.index.names.find(q))  # "more colas on my tomato": not these cards
        if not wanted and named.heads and not other_plant:  # "Bringen mehr Köpfe mehr Ertrag?": heads cards (S4)
            wanted = list(methods.HEADS)
        keys = [k for k in wanted if (m := lib.get(k)) is not None and m.visible()]
        if not keys:
            return None
        covers = {e for p in self.index.by_meta(kind=methods.KIND) if p.meta.get("method") in keys
                  for e in p.meta.get("covers") or ()}
        if any(e not in covers for e in found.uncovered):
            return None
        hits = [h for h in self.index.search(q, lang=lang, kinds=[methods.KIND], aliases=aliases, limit=60).hits
                if h.passage.meta.get("method") in keys][:5]
        return (keys[0], hits) if hits else None

    def _tool_rule(self, q: str, lang: str, t_hit: float, aliases: Mapping[str, Any] | None) -> Retrieval | None:
        """Tool passages (addendum N2), when the caller set them: a hit at T_HIT."""
        if not self.index.by_meta(kind=TOOL_KIND):
            return None
        tool = self.index.search(q, lang=lang, kinds=[TOOL_KIND], aliases=aliases, limit=3)
        if tool.hits and tool.confidence >= t_hit:
            return tool
        return None

    def _speaks(self, corr: Correction, lang: str) -> bool:
        """A candidate is asked about only in a form of the question's language: "Meinst du sage?" (the English name
        of Salbei, for "spare" in "Wie spare ich ...") or "Do you mean Gurke?" is no question to ask. A name that is
        the same in both languages (Microgreens, VPD) passes."""
        entry = self.index.vocab.entry(corr.entity) or {}
        own = entry.get(lang) or []
        own = [own] if isinstance(own, str) else own
        term = typo.compact(corr.term)
        return any(isinstance(form, str) and typo.compact(form) == term for form in own)

    @staticmethod
    def _action(found: Retrieval) -> bool:
        """Spec 3.5: a switching verb with a device or metric and a value or direction is an action wish."""
        slots = found.slots
        if not slots.verb or not (slots.box_devices or slots.metrics):
            return False
        return slots.switch in _ACTION_SWITCH or slots.percent is not None or slots.step is not None


# ---------- the module function of appendix A ----------

_ROUTERS: weakref.WeakKeyDictionary[Any, Router] = weakref.WeakKeyDictionary()


def router_for(index: Any) -> Router:
    """One Router per index (gates and examples are read once)."""
    found = _ROUTERS.get(index)
    if found is None:
        found = Router(index)
        _ROUTERS[index] = found
    return found


def route(q: Any, *, lang_hint: str = "de", index: Any, vocab: Any = None, live: Any = None,
          pending: Clarify | None = None, mode: str = "on", dense_vec: list[float] | None = None,
          **options: Any) -> Route:
    """Spec appendix A: router.route(q, *, lang_hint, index, vocab, live, pending, mode, dense_vec) -> Route.
    vocab is accepted for the frozen signature; the index's own name directory (vocab.json plus the port labels) is
    the one used."""
    del vocab
    return router_for(index).route(q, lang_hint=lang_hint, live=live, pending=pending, mode=mode,
                                   dense_vec=dense_vec, **options)

