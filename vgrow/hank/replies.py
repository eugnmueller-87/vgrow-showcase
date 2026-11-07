"""Hank's fixed answers (spec 3.6, 3.2, 3.4; addendum N3, N5), pure code: the catalog text of a route, and the
Answer for every route but grow.

Fixed answers never call the model and never take the model lane (spec 0.3 rule 4). Texts come from texts.py (WP5's
catalog). Additions by route:
- offtopic: the example questions follow in the text ("Frag mich zum Beispiel: ...") and as chips; greet and empty
  carry the chips only (addendum N6: chips under the off-topic, greeting and empty answers).
- legal: the fixed reply plus the top passage of kind legal with its numbered source (spec 3.2), and the draft note
  of addendum N3 while the legal card is a draft.
- no_data: hank.reply.no_data_term when the router found an uncovered name, with that name in the answer language;
  Answer.gap is True (the caller writes the gap entry, spec 8.1). A blocked topic is a health route: no gap (N5).
  A typed question (Route.qtype, v1.2: explain, how-to, why) says it in one sentence and what Hank can do
  instead (asking.no_data_text).
- clarify: "Meinst du X?" with Answer.clarify {id, term, token} for the Ja/Nein buttons; without an open question
  (a "Nein", an expired id) hank.reply.clarify_no.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from . import texts, texts_live
from .types import Answer, Route, SourceRef

CHIP_ROUTES = frozenset({"offtopic", "greet", "empty"})  # addendum N6
_DRAFT_KEY = "hank.draft.card"  # WP6's catalog: "Entwurf, noch nicht freigegeben."
# v1.2: the question types whose no_data answer offers what Hank can do instead. A value question ("Wie viel Licht
# braucht eine Tomate?") keeps the catalog text: "how is your box" is no help for an unknown plant.
TYPED_NO_DATA = frozenset({"explain", "howto", "why"})


def _lang(route: Route) -> str:
    return route.lang if route.lang in ("de", "en") else "de"


def _entity_name(index: Any, entity: str, lang: str) -> str:
    """A directory entry's first name in the language (German if it has none in English), else its id."""
    vocab = getattr(index, "vocab", None)
    entry = vocab.entry(entity) if vocab is not None and hasattr(vocab, "entry") else None
    if entry:
        for key in (lang, "de", "en"):
            names = entry.get(key) or []
            names = [names] if isinstance(names, str) else names
            for name in names:
                if isinstance(name, str) and name.strip():
                    return name.strip()
    return entity


entity_name = _entity_name  # for core.topic_of (v1.3)


def _uncovered(route: Route) -> str | None:
    reason = (route.reason or "").rsplit(";", 1)[-1]  # a follow-up's reason starts with "followup:<kind>;"
    if reason.startswith("uncovered:"):
        return reason.split(":", 1)[1] or None
    return None


def _legal_passage(route: Route) -> Any:
    retrieval = route.retrieval
    if retrieval is None:
        return None
    for hit in retrieval.hits:
        if hit.passage.kind == "legal" and hit.score > 0:
            return hit.passage
    return None


def _legal_sources(route: Route, index: Any) -> list[SourceRef]:
    passage = _legal_passage(route)
    if passage is None:
        return []
    lang = _lang(route)
    if index is not None and hasattr(index, "sources"):
        return list(index.sources(passage, lang))
    return [SourceRef(sid, sid, None) for sid in passage.sources]


def _draft_note(lang: str) -> str:
    catalog = texts_live.EN if lang == "en" else texts_live.DE
    return catalog.get(_DRAFT_KEY) or texts_live.DE.get(_DRAFT_KEY, "")


def reply_text(route: Route, *, index: Any = None, question: str = "") -> str | None:
    """The catalog text of a fixed route in the route's language, or None for grow (the draft and the model answer
    that one, WP6 and WP8). question: the cleaned question, for the typed no_data answer's offer (v1.2)."""
    lang = _lang(route)
    name = route.name
    if name == "grow":
        return None
    if name == "off":
        return texts.say("hank.reply.warnings_only" if route.reason == "warnings" else "hank.reply.off", lang)
    if name == "too_long":
        return texts.say("hank.reply.too_long", lang, max=500)
    if name == "clarify":
        if route.clarify is None:
            return texts.say("hank.reply.clarify_no", lang)
        return texts.say("hank.reply.clarify", lang, term=route.clarify.candidate.term)
    if name == "no_data":
        entity = _uncovered(route)
        if route.qtype in TYPED_NO_DATA:
            from . import asking  # asking imports the index and the verdicts; replies stays light for the router

            return asking.no_data_text(route, _entity_name(index, entity, lang) if entity else None,
                                       question=question)
        if entity:
            return texts.say("hank.reply.no_data_term", lang, term=_entity_name(index, entity, lang))
        return texts.say("hank.reply.no_data", lang)
    if name == "offtopic":
        return " ".join([texts.say("hank.reply.offtopic", lang), *texts.chips(lang)])
    if name == "package_off":  # v1.4: the card's package is switched off in the wiki (methods.py)
        from . import methods

        return methods.package_off_text(lang, None)
    if name == "legal":
        out = texts.say("hank.reply.legal", lang)
        passage = _legal_passage(route)
        if passage is not None:
            body = (passage.text.get(lang) or passage.text.get("de") or "").strip()
            if body:
                if passage.sources:  # the marker before the sentence's end: "... KCanG) [1]." (finding 15)
                    end = body[-1] if body[-1] in ".!?" else "."
                    body = f"{body.rstrip('.!?').rstrip()} [1]{end}"
                out += f" {texts.say('hank.reply.legal_basics', lang)} {body}"
                if passage.status == "draft":
                    out += f" {_draft_note(lang)}"
        return out
    if name == "health" and (route.reason or "").startswith("gate:child_risk:"):
        # feeding v2: a child or an animal and a growing substance with a risk word; the VIVOSUN label's own
        # sentence when the question names those bottles (card V1: "KEEP OUT OF REACH OF CHILDREN")
        out = texts.say("hank.reply.health_child", lang)
        if re.search(r"vivosun|base ?-?[ab]\b|granul", route.reason or ""):
            out += " " + texts.say("hank.reply.health_child_label", lang)
        return out
    if name == "sale" and (route.reason or "").startswith("gate:buy:"):
        # buying seeds or a strain (23.10.2026): the person's own choice, no shop, price or link
        return texts.say("hank.reply.buy", lang)
    key = texts.FIXED_REPLY_KEYS.get(name)
    if key is None:
        raise ValueError(f"no fixed reply for route {name!r}")
    return texts.say(key, lang)


def answer(route: Route, *, index: Any = None, answer_id: str | None = None) -> Answer:
    """The Answer of a fixed route (every route but grow). Never calls the model."""
    if route.name == "grow":
        raise ValueError("grow is answered by the draft and the model, not from the catalog")
    lang = _lang(route)
    body = route.reply if route.reply is not None else reply_text(route, index=index)
    clarify = None
    if route.name == "clarify" and route.clarify is not None:
        c = route.clarify
        clarify = {"id": c.id, "term": c.candidate.term, "token": c.candidate.token}
    sources = _legal_sources(route, index) if route.name == "legal" else []
    return Answer(id=answer_id or uuid.uuid4().hex[:12], route=route.name, lang=lang, text=body or "",
                  sources=sources, proposals=[], clarify=clarify,
                  chips=texts.chips(lang) if route.name in CHIP_ROUTES else [], draft_only=False, note=None,
                  timing={}, gap=route.name == "no_data")
