"""The wiki's "Hank fragen" button (v1.4): "Erzähl mir mehr über <Titel>" / "Tell me more about <title>".

web/wiki.js sends the card's title as the page shows it (the language and the package switch of the moment). Hank
finds that card among the wiki's own cards (vgrow/wiki.py reads kartei/ for both) and answers from it, in code:

- methods: the method card in full (methods.answer, form "more": what, the numbered steps, when, autoflower, box
  fit, the first sourced risk, the stage window while the plant flowers);
- glossary: the glossary's explanation (asking.explain_answer), plus where the steps are when a method card exists;
- crops, processes, stages, strains, nutrients, legal: a short card summary from the card as the wiki shows it
  (wiki.card_view: the parts of a package that is switched off gone, nothing about consumption): its summary, its
  stages or steps by name, the light and heat notes for the VGrow, the first warning, each with its own source marks,
  then that the rest is on the card, then the draft note.

The button is decided per folder, not per card (EXPLAIN_FOLDERS, mirrored in web/hank.js HANK_FEATURES): a folder is
listed only when Hank has a composer for its cards, and tests/test_hank_methods.py asks every card of every listed
folder in both languages, with the cannabis package on and off. A folder that is not listed ("problems", or one
added later) keeps the button hidden until it gets a composer here.

A card of a package that is switched off is never answered from (route package_off), as in methods.py.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from vgrow import i18n
from vgrow.i18n import t

from . import asking, methods, typo
from .kartei_index import packages_on
from .livestate import LiveState
from .types import Answer, Route

_LOGGER = logging.getLogger(__name__)

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
# The folders whose cards Hank answers, in the order a title shared by two cards is resolved ("Lollipopping (untere
# Triebe entfernen)" is the method card's title and the glossary term's: the method card has the steps).
EXPLAIN_FOLDERS = ("methods", "crops", "processes", "glossary", "stages", "strains", "nutrients", "legal")

_MORE = [re.compile(p) for p in (
    r"^(?:erzahl|erzahle|erzahlst|erzahlen) (?:du )?(?:mir )?(?:mal )?(?:bitte )?(?:noch )?(?:mehr|etwas|was|"
    r"alles) (?:uber|von|zu) (?P<s>.+)$",
    r"^(?:kannst|konntest) du mir (?:mal )?(?:bitte )?(?:mehr|etwas|was) (?:uber|von|zu) (?P<s>.+?)(?: erzahlen)?$",
    r"^(?:mehr|etwas mehr) (?:uber|zu) (?P<s>.+)$",
    r"^(?:tell|can you tell|could you tell) me (?:a bit |a little |some )?(?:more )?(?:about|on) (?P<s>.+)$",
    r"^(?:more about|more on) (?P<s>.+)$",
)]
# a title the words of the question do not cover any more after these (the page sends the title alone)
_TRAIL = frozenset({"bitte", "please", "mal", "noch", "erzahlen", "lol", "pls", "danke", "thanks"})

DE: dict[str, str] = {
    "hank.card.head": "{label} (aus der Kartei):",
    "hank.card.phases": "Phasen auf der Karte: {items}.",
    "hank.card.steps": "Schritte auf der Karte: {items}.",
    "hank.card.light": "Licht in der VGrow: {text}",
    "hank.card.heat": "Wärme in der VGrow: {text}",
    "hank.card.warning": "Achtung: {text}",
    "hank.card.rest": "Alles Weitere steht auf der Karte im Wiki.",
    "hank.card.draft": "Aus der Kartei: Entwurf, noch nicht freigegeben.",
}

EN: dict[str, str] = {
    "hank.card.head": "{label} (from the card file):",
    "hank.card.phases": "Stages on the card: {items}.",
    "hank.card.steps": "Steps on the card: {items}.",
    "hank.card.light": "Light in the VGrow: {text}",
    "hank.card.heat": "Heat in the VGrow: {text}",
    "hank.card.warning": "Watch out: {text}",
    "hank.card.rest": "The rest is on the card in the wiki.",
    "hank.card.draft": "From the card file: draft, not yet approved.",
}


def check() -> list[str]:
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        if i18n.placeholders(DE[key]) != i18n.placeholders(EN[key]):
            problems.append(f"placeholders differ: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    problems = check()
    if problems:
        raise ValueError("wikicards: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _lang(lang: str | None) -> str:
    return lang if lang in ("de", "en") else "de"


def _wiki() -> Any:
    from vgrow import wiki  # at call time: vgrow/wiki.py imports Hank's text helpers itself

    return wiki


# ---------- the question ----------

def subject(q: str) -> list[str] | None:
    """The words after "Erzähl mir mehr über" / "Tell me more about", or None for any other question."""
    joined = " ".join(asking._without_lead(asking.words(q)))
    for pattern in _MORE:
        found = pattern.match(joined)
        if found is not None:
            words = found.group("s").split()
            while words and words[-1] in _TRAIL:
                words = words[:-1]
            while words and words[0] in asking._ARTICLES:
                words = words[1:]
            return words or None
    return None


def entries() -> list[dict[str, Any]]:
    """The wiki's cards (vgrow/wiki.py, read again when a file changed) of the folders Hank answers. The strain
    lexicon's entries (the Sorten area) are not among them: Hank answers those from the lexicon (strains.py). The
    Dünger area's cards are (23.10.2026): they are the nutrient cards, only listed apart."""
    wiki = _wiki()
    return [e for e in wiki._entries(wiki.LIBRARY) if e.get("folder") in EXPLAIN_FOLDERS
            and (not e.get("area") or e.get("area") == getattr(wiki, "FERTILIZER_AREA", None))]


def _titles(entry: Mapping[str, Any]) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    """Every title the page can show for a card (both languages, with the package on and off: the if_off titles),
    as folded words: (the full titles, the titles without their explanation in brackets)."""
    wiki = _wiki()
    raw = entry.get("raw") if isinstance(entry.get("raw"), Mapping) else {}
    out: list[str] = [str(raw.get(k)) for k in ("label", "label_en", "name", "name_en", "title", "title_en")
                      if isinstance(raw.get(k), str)]
    for lang in ("de", "en"):
        for on in ([], list(wiki.KNOWN_PACKAGES)):
            try:
                out.append(str(wiki.summary(entry, lang, on).get("label") or ""))
            except Exception as err:  # noqa: BLE001 (a broken card never stops the others)
                _LOGGER.warning("Hank wikicards: no title for %s (%s)", entry.get("key"), err)
    full: list[tuple[str, ...]] = []
    short: list[tuple[str, ...]] = []
    for title in out:
        for form, bucket in ((title, full), (re.split(r" \(|: | / ", title)[0], short)):
            found = tuple(asking.words(form))
            if found and found not in bucket:
                bucket.append(found)
    return full, short


def _typo(word: str, title: str) -> bool:
    """One typo in a one-word title of five letters or more, the first letter right. Only here, after "Erzähl mir
    mehr über" has said a card is meant: as a general rule it took "tippen" for "toppen" (review M1)."""
    return len(word) >= 5 and len(title) >= 5 and word[0] == title[0] and typo.osa(word, title, 1) == 1


def _one_typo(wanted: tuple[str, ...], title: tuple[str, ...]) -> bool:
    """The title with one typo in one of its words, the others exact ("Topipng (cutting off the main tip)")."""
    if len(wanted) != len(title):
        return False
    differ = [(w, t_) for w, t_ in zip(wanted, title, strict=True) if w != t_]
    return len(differ) == 1 and _typo(*differ[0])


def find(words: list[str], on: Iterable[str] | None = None) -> dict[str, Any] | None:
    """The card a title names: a full title first, then a title without its explanation ("Topping"), then one typo
    in one word of a title. A title two cards share goes to the one the package switch shows ("Entlauben
    (Defoliation)" is the glossary term's title and the method card's: with the cannabis package off only the term
    is shown), then to the folder first in EXPLAIN_FOLDERS."""
    wanted = tuple(words)
    active = set(packages_on() if on is None else on)
    wiki = _wiki()
    best: tuple[tuple[int, int, int], dict[str, Any]] | None = None
    for entry in entries():
        full, short = _titles(entry)
        level = 0
        if wanted in full:
            level = 3
        elif wanted in short:
            level = 2
        elif any(_one_typo(wanted, t_) for t_ in full + short):
            level = 1
        if not level:
            continue
        rank = (level, int(wiki.visible(entry, active)), -EXPLAIN_FOLDERS.index(entry["folder"]))
        if best is None or rank > best[0]:
            best = (rank, entry)
    return best[1] if best else None


def route(q: str, lang: str, *, gloss: asking.Glossary | None = None) -> Route | None:
    """The route of "Erzähl mir mehr über <Titel>", or None (no such question, or no card by that title)."""
    words = subject(q)
    if not words:
        return None
    entry = find(words)
    if entry is None:
        return None
    wiki = _wiki()
    folder, key = str(entry["folder"]), str(entry["key"])
    label = str(wiki.summary(entry, lang, list(packages_on())).get("label") or key)
    if not wiki.visible(entry, packages_on()):
        return methods.package_off_route(lang, label=re.split(r" \(|: ", label)[0],
                                         reason=f"package_off:{folder}/{key}")
    reason = f"more:{folder}/{key}"
    if folder == "glossary":
        gloss = gloss or asking.glossary()
        term = gloss.get(key)
        if term is None:
            return None
        return Route("explain", None, None, None, "info", lang, reason, qtype="explain", term=term.key)
    if folder == "methods":
        return Route("explain", None, None, None, "info", lang, reason, qtype="explain",
                     term=methods._linked_term(key, gloss or asking.glossary()), cards=[f"{folder}/{key}"],
                     form="more")
    return Route("explain", None, None, None, "info", lang, reason, qtype="explain", cards=[f"{folder}/{key}"],
                 form="more")


# ---------- the answer ----------

def _entry(card: str) -> dict[str, Any] | None:
    folder, _, key = card.partition("/")
    return next((e for e in entries() if e["folder"] == folder and e["key"] == key), None)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _labels(items: Iterable[Any]) -> list[str]:
    return [_text(i.get("label")) for i in items if isinstance(i, Mapping) and _text(i.get("label"))]


def _joined(labels: list[str], lang: str) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + (" und " if lang == "de" else " and ") + labels[-1]


def lines_of(entry: Mapping[str, Any], lang: str, on: Iterable[str]) -> tuple[str, list[str]]:
    """(the card's title, the lines Hank says about it), from the card as the wiki shows it."""
    wiki = _wiki()
    view = wiki.card_view(entry, lang, list(on))
    card = view.get("card") if isinstance(view.get("card"), Mapping) else {}
    label = str(view.get("label") or entry.get("key"))
    lines = [t("hank.card.head", lang, label=label)]
    for name in ("summary", "text", "description"):
        if _text(card.get(name)):
            lines.append(_text(card[name]))
            break
    stages = _labels(card.get("stages") or [])
    if stages:
        lines.append(t("hank.card.phases", lang, items=_joined(stages, lang)))
    steps = _labels(card.get("steps") or [])
    if steps:
        lines.append(t("hank.card.steps", lang, items="; ".join(f"{n}. {s}" for n, s in enumerate(steps, 1))))
    for fact in card.get("facts") if isinstance(card.get("facts"), list) else []:
        if isinstance(fact, Mapping) and _text(fact.get("text")):
            lines.append(_text(fact["text"]))
    if _text(card.get("status_note")):
        lines.append(_text(card["status_note"]))
    fit = card.get("fit_for_vgrow") if isinstance(card.get("fit_for_vgrow"), Mapping) else {}
    if _text(fit.get("light_note")):
        lines.append(t("hank.card.light", lang, text=_text(fit["light_note"])))
    if _text(fit.get("heat_note")):
        lines.append(t("hank.card.heat", lang, text=_text(fit["heat_note"])))
    warnings = [w for w in card.get("warnings") or [] if _text(w)]
    if warnings:
        lines.append(t("hank.card.warning", lang, text=_text(warnings[0])))
    lines.append(t("hank.card.rest", lang))
    return label, lines


def _sources_cited(entry: Mapping[str, Any], lines: list[str], explicit: Iterable[str] = ()) -> list[str]:
    table = entry.get("sources") if isinstance(entry.get("sources"), Mapping) else {}
    ids = set(methods.cited(lines, table)) | {s for s in explicit if s in table}
    return sorted(ids, key=methods._natural)


def answer(route_: Route, live: LiveState | Mapping[str, Any], *, answer_id: str, index: Any = None) -> Answer:
    """The answer of a "tell me more" route about a crop, process, stage, strain, nutrient or legal card (method
    cards go to methods.answer, glossary terms to asking.explain_answer)."""
    lang = _lang(route_.lang)
    card = (route_.cards or [""])[0]
    if card.startswith(f"{methods.KIND}/"):
        return methods.answer(route_, live, answer_id=answer_id, index=index)
    entry = _entry(card)
    if entry is None:
        return Answer(id=answer_id, route="no_data", lang=lang, text=t("hank.nodata.plain", lang), gap=True)
    on = packages_on()
    if not _wiki().visible(entry, on):
        return Answer(id=answer_id, route="package_off", lang=lang, text=methods.package_off_text(lang, None))
    _, lines = lines_of(entry, lang, on)
    explicit = [s for f in (_wiki().card_view(entry, lang, list(on)).get("card") or {}).get("facts") or []
                if isinstance(f, Mapping) for s in f.get("sources") or []]
    if str(entry.get("status") or "draft") == "draft":
        lines.append(t("hank.card.draft", lang))
    ids = _sources_cited(entry, lines, explicit)
    table = dict(entry.get("sources") or {})
    sources = methods.resolve(index, table, ids, lang)
    return Answer(id=answer_id, route="explain", lang=lang, text="\n".join(lines), sources=sources)


__all__ = ["DE", "EN", "EXPLAIN_FOLDERS", "answer", "check", "entries", "find", "lines_of", "register", "route",
           "subject"]
