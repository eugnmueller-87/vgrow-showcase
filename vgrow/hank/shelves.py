"""Hank's shelves (owner, 01.11.2026: "move everything he's collecting into a RAG ... and the rule that he checks at
first for everything"; "if he doesn't understand the question, he's not answering that. It cannot be happening. He
needs to suggest whether we mean a certain topic or if he doesn't know").

What it is: a second card index (kartei_index.Index with shelves=True) over the folders the main index never held:
the problem cards (pests, deficiencies, stress), the plant-health entries, the crops, the processes and the whole
fertilizer market. Same search as the main index (BM25, 3-grams, the embedding on the Mac), its own vector cache. Kept
apart so its 1,281 passages never move the ranking of a climate or feeding question (the method cards' lesson, v1.4:
mixed into the main search they broke 14 eval cases).

When it is asked (router._search_route, after the main decision and the glossary rescue): only when the main search
refused (offtopic, no_data), never over an answer ("Wie gieße ich richtig?" is today's stage card even as a weak hit,
not the overwatering card). Then three outcomes, never a silent refusal:
- answer: the best card clearly fits (T_ANSWER and a lead of GAP): the route grow with that card's best passages, so
  the answer is drafted and polished like any card answer, with the card's own numbers and source ids;
- suggest: a card is near but not sure (T_SUGGEST, or a tie): "Meinst du eins davon?" with up to three cards as
  buttons, and the question goes on the gap list;
- nothing near: the main decision stands (off-topic or "no card"), as before. A question that names a thing no card
  covers ("Wie oft gieße ich meine Gurke?") keeps its honest "no card" (router._shelf_route): a cucumber answered with
  "Meinst du Überwässern?" would be the silly answer the owner does not want either.
A question an off-topic example matched as typed ("Wie mach ich ein Pesto aus Basilikum?") answers only with a card
it names by name. The safety gates run long before.

Calibrated on tests/hank_eval/shelves_dev.json (01.11.2026, lexical and granite-embedding alike): the off-topic
questions reached at most 0.38 on the shelves, the right cards 0.40 to 1.0.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from vgrow import i18n
from vgrow.i18n import t

from .kartei_index import SHELF_FOLDERS, Index, packages_on
from .types import Answer, Hit, Retrieval, Route

_LOGGER = logging.getLogger(__name__)

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
FORMS = ("shelf", "shelf_suggest")
T_ANSWER = 0.55  # the best card answers alone only from here, and only when it leads by GAP
GAP = 0.08  # "Die unteren Blätter werden gelb": nitrogen, magnesium and sulfur all at 1.0 is a question back
T_SUGGEST = 0.40  # the off-topic questions of the dev set reached 0.38 at most
T_OVER_OFF = 0.60
SUGGEST_MAX = 3
REFRESH_SECONDS = 60.0

DE: dict[str, str] = {
    "hank.shelf.suggest": "Da bin ich mir nicht sicher. Meinst du {items}? Tipp auf eins, dann erzähle ich dir, was "
                          "auf der Karte steht.",
    "hank.shelf.or": "oder",
    "hank.shelf.chip": "Erzähl mir mehr über {label}",
}
EN: dict[str, str] = {
    "hank.shelf.suggest": "I am not sure about that. Do you mean {items}? Tap one and I will tell you what its card "
                          "says.",
    "hank.shelf.or": "or",
    "hank.shelf.chip": "Tell me more about {label}",
}


def check() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, an em dash."""
    out = [f"missing in EN: {k}" for k in DE if k not in EN] + [f"missing in DE: {k}" for k in EN if k not in DE]
    for key in DE.keys() & EN.keys():
        if set(re.findall(r"{(\w+)}", DE[key])) != set(re.findall(r"{(\w+)}", EN[key])):
            out.append(f"placeholders differ: {key}")
    out += [f"em dash: {k}" for k, v in {**DE, **EN}.items() if EM_DASH in v]
    return out


def register() -> None:
    problems = check()
    if problems:
        raise ValueError("shelves: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _lang(lang: str | None) -> str:
    return lang if lang in ("de", "en") else "de"


# ---------- the shelves' index ----------

@dataclass(frozen=True)
class CardHit:
    card: str  # "problems/aphid"
    score: float


_SHARED: dict[tuple[str, int, str | None], tuple[Index, float]] = {}  # one shelves' index per library and embedder


class Shelves:
    """The shelves' own index beside the main one: same library, embedder, model and cache folder, built on first use
    (shared by every router of the process: building it takes a second), read again at most once a minute when a card
    file changed. The main index resolves the sources of shelf passages through it (Index.fallback)."""

    def __init__(self, main: Any) -> None:
        self.main = main

    @property
    def index(self) -> Index | None:
        library = getattr(self.main, "library", None)
        if library is None:
            return None
        embedder = getattr(self.main, "embedder", None)
        key = (str(library), id(embedder), getattr(self.main, "model", None))
        found = _SHARED.get(key)
        if found is None:
            try:
                index = Index.load(library=library, embedder=embedder, cache_dir=getattr(self.main, "_cache_dir", None),
                                   model=getattr(self.main, "model", None), shelves=True)
            except Exception:  # noqa: BLE001 (the shelves are an extra: without them Hank answers as before)
                _LOGGER.exception("Hank shelves: the index could not be built")
                return None
            found = _SHARED[key] = (index, time.monotonic())
        elif time.monotonic() - found[1] > REFRESH_SECONDS:
            _SHARED[key] = (found[0], time.monotonic())
            try:
                found[0].refresh()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Hank shelves: refresh failed, the old index stays")
        if hasattr(self.main, "fallback"):
            self.main.fallback = found[0]
        return found[0]

    async def start(self) -> None:
        """The shelves' vectors. Not started for now: Router.warm leaves them out until the shelves' thresholds are set
        for vectors (measured 02.11.2026, see there)."""
        index = self.index
        if index is not None and index.embedder is not None:
            await index.start()

    def find(self, q: str, lang: str, dense_vec: list[float] | None = None,
             dense_state: str | None = None) -> tuple[Retrieval | None, list[CardHit]]:
        """The search and its cards (each once, at its best passage's score, best first). Never raises."""
        index = self.index
        if index is None:
            return None, []
        try:
            found = index.search(q, dense_vec, lang=lang, dense_state=dense_state)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Hank shelves: search failed")
            return None, []
        out: dict[str, float] = {}
        for hit in found.hits:
            card = f"{hit.passage.kind}/{hit.passage.card}"
            out[card] = max(out.get(card, 0.0), float(hit.score))
        return found, [CardHit(c, s) for c, s in sorted(out.items(), key=lambda kv: -kv[1])]

    def search(self, q: str, lang: str, dense_vec: list[float] | None = None,
               dense_state: str | None = None) -> list[CardHit]:
        return self.find(q, lang, dense_vec, dense_state)[1]

    def covers(self, entity: str) -> bool:
        """A name of the directory some shelf card names ("basil": the basil card), as Index.covered."""
        index = self.index
        return bool(index is not None and index.covered(entity))


def decide(hits: list[CardHit], base_name: str, base_reason: str, base_conf: float) -> str | None:
    """"answer" (one card clearly fits), "suggest" (a question back with the nearest cards) or None (the main
    decision stands). A wrong card told with confidence is worse than a question back, so a tie never answers."""
    if not hits:
        return None
    top = hits[0].score
    clear = top - (hits[1].score if len(hits) > 1 else 0.0) >= GAP
    if base_name not in ("offtopic", "no_data"):
        # the shelves come before a refusal, never over an answer: "Wie gieße ich richtig?" is today's stage card
        # (a weak hit, 0.41, and right), not the overwatering card (1.0)
        return None
    if base_reason.startswith(("offtopic_example:", "offtopic_nn:", "foreign_host:")):
        if top < T_OVER_OFF:
            return None
        return "answer" if clear else "suggest"
    if top >= T_ANSWER and clear:
        return "answer"
    if top >= T_SUGGEST:
        return "suggest"
    return None


LEAD_FIELDS = ("tile", "symptoms", "do_now")  # a problem card always says what it is, what you see, what to do
_NOTE = ("Hinweis", "Note", "Aus der Praxis", "From practice")  # a remark about the sources, a tip: never the lead


def _plain(passage: Any) -> bool:
    """A plain statement of the card, not a remark about its sources ("Hinweis: Alle Wochenangaben stammen aus einem
    Hydrokultur-Versuch") nor a practice tip, which the nitrogen card lists first under its symptoms."""
    return not any(str(passage.text.get(lang) or "").startswith(_NOTE) for lang in ("de", "en"))
NEAR = 0.10  # a question back names only cards this close to the best one ("Microgreens" is no spider mite)


def _focused(retrieval: Retrieval, card: str, shelves: Shelves | None) -> list[Hit]:
    """The passages an answer stands on. A problem or health card: the passage the question matched best, then its
    short description, its first symptom and its first step (LEAD_FIELDS), so "Fehlt ihr Stickstoff?" is answered with
    what nitrogen deficiency looks like and what to do, not with a side note the words happened to match. Any other
    card: its passages the question matched, best first."""
    own = [h for h in retrieval.hits if f"{h.passage.kind}/{h.passage.card}" == card]
    folder, _, key = card.partition("/")
    index = shelves.index if shelves is not None else None
    if folder not in ("problems", "health") or index is None or not own:
        return own
    picked = []
    if folder == "health":
        picked += index.by_meta(kind=folder, card=key)[:2]  # its summary and features
    for field_ in LEAD_FIELDS:
        first = next((p for p in index.by_meta(kind=folder, card=key, field=field_) if _plain(p)), None)
        if first is not None and first not in picked:
            picked.append(first)
    best = next((h.passage for h in own if _plain(h.passage)), None)
    if best is not None and best not in picked:
        picked.append(best)  # then what the question matched best
    top = own[0].score
    return [Hit(p, round(top - i * 0.01, 4), {}) for i, p in enumerate(picked)]


def route_of(outcome: str, hits: list[CardHit], lang: str, reason: str, retrieval: Retrieval | None,
             shelves: Shelves | None = None) -> Route:
    """An answer: the route grow with the card's passages (the normal draft and polish, the card's numbers and source
    ids, as any card answer); a fertilizer card is told by wikicards (route explain), its weekly charts are no answer
    to "what is it". A question back: the route explain with the cards close to the best one, as buttons."""
    tag = f"shelf:{outcome}:{hits[0].card}:{hits[0].score:.2f};{reason}"
    if outcome == "answer" and retrieval is not None:
        card = hits[0].card
        if card.startswith("nutrients/"):
            return Route("explain", None, None, None, "info", lang, tag, cards=[card], form="more")
        focused = replace(retrieval, hits=_focused(retrieval, card, shelves), confidence=hits[0].score, uncovered=[])
        return Route("grow", None, focused, None, "info", lang, tag, cards=[card], form="shelf")
    near = [h for h in hits[:SUGGEST_MAX] if h.score >= hits[0].score - NEAR]
    cards = [h.card for h in near]
    about = {"cards": cards, "scores": [round(h.score, 3) for h in near]}
    return Route("explain", None, retrieval, None, "info", lang, tag, cards=cards, form="shelf_suggest", about=about)


# ---------- the cards ----------

def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def card_of(card: str, library: Path) -> dict[str, Any] | None:
    """The card a shelf id names: a card file ("problems/aphid") or an entry of a collection ("health/<key>")."""
    folder, _, key = card.partition("/")
    if folder not in (*SHELF_FOLDERS, "nutrients") or not key:
        return None
    data = _read(library / folder / f"{key}.json")
    if isinstance(data, dict):
        return data
    for path in sorted((library / folder).glob("*.json")):
        coll = _read(path)
        if isinstance(coll, dict) and isinstance(coll.get("entries"), list):
            for entry in coll["entries"]:
                if isinstance(entry, dict) and entry.get("key") == key:
                    table = coll.get("sources") if isinstance(coll.get("sources"), Mapping) else {}
                    return {**entry, "status": coll.get("status"),
                            "sources": {sid: table[sid] for sid in entry.get("sources") or [] if sid in table}}
    return None


def _label(card: Mapping[str, Any], lang: str, fallback: str) -> str:
    """The card's short name: its label before an explanation in brackets ("Weiße Fliege", not the species list)."""
    full = str((card.get("label_en") if lang == "en" else None) or card.get("label") or fallback)
    return full.split(" (")[0].strip() or full


def _visible(card: Mapping[str, Any]) -> bool:
    package = card.get("package")
    return not isinstance(package, str) or not package or package in packages_on()


def suggest_answer(route_: Route, *, answer_id: str, library: Path) -> Answer:
    """"Meinst du Trauermücken, Wurzelblattläuse oder Weiße Fliege?" with a button per card ("Erzähl mir mehr über
    <Titel>"); the question goes on the gap list."""
    lang = _lang(route_.lang)
    labels = []
    for card_id in route_.cards or []:
        card = card_of(card_id, library)
        if card is not None and _visible(card):
            labels.append(_label(card, lang, card_id.split("/")[-1]))
    labels = list(dict.fromkeys(labels))
    if not labels:
        return Answer(id=answer_id, route="no_data", lang=lang, text=t("hank.nodata.plain", lang), gap=True)
    items = labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} {t('hank.shelf.or', lang)} {labels[-1]}"
    chips = [t("hank.shelf.chip", lang, label=label) for label in labels]
    return Answer(id=answer_id, route="explain", lang=lang, text=t("hank.shelf.suggest", lang, items=items),
                  chips=chips, gap=True)


def answer(route_: Route, live: Any, *, answer_id: str, index: Any = None) -> Answer:
    """The answer of a shelf question back (form shelf_suggest). A clear card is the route grow, answered as any
    card answer (core._grow)."""
    library = Path(getattr(index, "library", None) or Path(__file__).resolve().parents[2] / "kartei")
    return suggest_answer(route_, answer_id=answer_id, library=library)


__all__ = ["DE", "EN", "FORMS", "CardHit", "Shelves", "answer", "card_of", "check", "decide", "register", "route_of",
           "suggest_answer"]
