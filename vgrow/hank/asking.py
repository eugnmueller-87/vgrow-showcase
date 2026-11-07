"""What kind of question it is, and whether Hank can help (owner, 23.10.2026: "Hank is a bit silly, he has to realize
a question and either he can help or not").

Seen live that morning: "what is VPD" got the live VPD reading instead of an explanation, and "can u fix it?" after it
had nothing to refer to. Built here, from code alone (no model, nothing switched):

1. Question types. classify() reads the question's words: explain ("Was ist X?", "Was bedeutet X?", "Erklär mir X",
   "What is X?", "What does X mean?", "Explain X"), value ("Wie warm ist es?", "Wie hoch ist die Pflanze?", "How
   humid is it?"), how-to ("Wie mache ich X?", "How do I X?"), why ("Warum ...?", "Why ...?"). The router asks it
   after the safety gates, the fix wish and the today question.
2. The glossary (kartei/glossary/glossary.json, 19 sourced terms, status draft): an explain question whose subject is
   a glossary term (aliases included, both languages) is the route "explain": the term's text first, with its numbered
   sources, then one sentence with the live reading when the term has relates_to_metric, else "Die Box misst das
   nicht.", then the draft note ("Entwurf, noch nicht freigegeben."). Never the reading alone.
3. The route "reading", for a value question the cards cannot answer but code can: the plant's height with the day
   it was measured ("61 cm, gemessen am 22.10.") and, when that is more than two days old, the ask for today's; a
   glossary term the box does not measure ("Wie hoch ist der PPFD?": "PPFD misst die Box nicht.").
4. Can help or not: a typed question that finds no card (route no_data) says so in one sentence and what Hank can do
   instead, by type (no_data_text). Untyped no_data keeps its catalog text.
5. Follow-ups (TopicBook): the last topic of a chat (client_id) for 15 minutes, the metric, device, glossary term or
   problem list of the last answer. "Und das?", "warum?", "what about humidity?", "Was bedeutet das?" are written out
   against it (follow_up) and routed as the full question; "can u fix it?" narrows the fix answer to that topic
   (fix_scope). Without a topic nothing is written out: the question goes its normal way.

Texts: the catalog below, in German and English, registered into i18n like today.py and fix.py.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from vgrow import config, i18n, plant
from vgrow.i18n import fmt_qty, t

from . import text, verdicts
from .kartei_index import packages_on, parse_source
from .livestate import LiveState, collect, day_text, port_name, reading
from .types import Answer, Route, SourceRef

_LOGGER = logging.getLogger(__name__)

GLOSSARY_FILE = config.REPO / "kartei" / "glossary" / "glossary.json"
TOPIC_TTL = 15 * 60.0  # a chat's last topic, for follow-ups (owner, 23.10.2026)
QTYPES = ("explain", "value", "howto", "why")
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
MAX_SUBJECT_WORDS = 6  # an explain subject longer than this is no term ("Was ist der beste Weg, um ...")
CAN_EXPLAIN = ("vpd", "ppfd", "relative-humidity")  # the examples of "Erklären kann ich dir zum Beispiel ..."
CAN_EXPLAIN_HOWTO = ("low-stress-training", "topping", "lollipopping")  # offered when a how-to has no card

DE: dict[str, str] = {
    "hank.explain.no_metric": "Die Box misst das nicht.",
    "hank.explain.no_reading": "Gerade liefert die Box dafür keinen Messwert.",
    "hank.explain.reading": "{label}: {value}.",
    "hank.explain.draft": "Aus dem Glossar: Entwurf, noch nicht freigegeben.",
    "hank.explain.water_last": "Die Box misst das nicht; zuletzt eingetragen: {label} {value} am {date}.",
    "hank.explain.water_none": "Die Box misst das nicht, und eingetragen hast du noch keinen Wert.",
    "hank.explain.fans": "Gerade: Abluft {dfan}, Umluft {cfan}.",
    "hank.explain.spectrum": "Die Lampe steht gerade auf dem Spektrum {name}.",
    "hank.explain.plan_on": "Der Lichtplan der Box ist gerade aktiv; die Stunden stellst du in der Vivosun-App ein.",
    "hank.explain.plan_off": "Der Lichtplan der Box ist gerade nicht aktiv; die Stunden stellst du in der Vivosun-App "
                             "ein.",
    "hank.explain.howto_none": "Eine Schritt-für-Schritt-Anleitung habe ich nicht.",
    "hank.explain.from_glossary": "Eine eigene Karte habe ich dazu nicht, im Glossar steht zu {term}:",
    "hank.reading.height": "Die Pflanze ist {value} hoch, gemessen am {date}.",
    "hank.reading.height_old": "Das ist {days} Tage her: trag die Höhe von heute in der Pflanzenkarte ein, dann sehe "
                               "ich sofort, wo wir stehen.",
    "hank.reading.height_none": "Eine Höhe hast du noch nicht eingetragen. Trag sie in der Pflanzenkarte ein, dann "
                                "sehe ich, wo wir stehen.",
    "hank.reading.unmeasured": "{label} misst die Box nicht.",
    "hank.reading.bloom": "Seit {days} Tagen in der Blüte (Blütewoche {week}), gezählt ab {date}, {by}.",
    "hank.reading.bloom_today": "Seit heute in der Blüte (Blütewoche 1), gezählt ab {date}, {by}.",
    "hank.reading.bloom_latest": "Der Blütebeginn ist nur geschätzt: spätestens {date}, denn das erste ausgewertete "
                                 "Kamerabild zeigte schon weiße Härchen.",
    "hank.reading.bloom_window": "Nach den Tagesfenstern der Phasenkarte (grober Rückfall) begann die Blüte eher um "
                                 "den {date}; heute wäre {stage} (seit {since}).",
    "hank.reading.height_card": "Laut Sortenkarte, Höhe indoor: {fact}.",
    "hank.reading.height_card_none": "Wie hoch sie noch wird, steht in keiner meiner Karten.",
    "hank.reading.bloom_none": "Einen Blütebeginn kenne ich noch nicht.",
    "hank.reading.bloom_enter": "Trag den Tag mit den ersten weißen Härchen in der Pflanzenkarte unter Blütebeginn "
                                "ein.",
    "hank.can.explain_term": "Was {label} ist, kann ich dir erklären.",
    "hank.can.explain_some": "Erklären kann ich dir zum Beispiel {labels}.",
    "hank.can.box": "Ich kann dir sagen, wie es deiner Box gerade geht.",
    "hank.can.problems": "Ich kann dir sagen, was gerade nicht im Ziel ist und was dagegen hilft.",
    "hank.nodata.plain": "Dazu steht noch nichts in meiner Kartei.",
    "hank.nodata.term": "Dazu steht noch nichts in meiner Kartei, zu {term} kenne ich nur den Namen.",
    "hank.nodata.howto": "Dazu steht noch nichts in meiner Kartei, eine Anleitung habe ich nicht.",
    "hank.nodata.howto_term": "Dazu steht noch nichts in meiner Kartei, eine Anleitung zu {term} habe ich nicht.",
    "hank.why.box": "In der Box gerade: {items}.",
    "hank.why.dfan": "Abluft {value}",
    "hank.why.cfan": "Umluft {value}",
    "hank.why.port_on": "{label} ({port}) an",
    "hank.why.port_off": "{label} ({port}) aus",
    "hank.why.auto_stage": "Die Befeuchter-Automatik wartet, weil die Phase nur geschätzt ist: sie schaltet erst, "
                           "wenn du die Phase bestätigst.",
    "hank.action.lead": "Im Chat schalte ich nichts ohne dich: die Karte unten ({label}) wartet auf deinen Tipp.",
    "hank.action.lead_none": "Im Chat schalte ich nichts ohne deinen Tipp, und dafür habe ich keine Karte zum "
                             "Antippen.",
    "hank.today.bloom_ask": "Blütebeginn spätestens {date}: das erste ausgewertete Kamerabild zeigte schon weiße "
                            "Härchen.",
    "hank.today.bloom_window": "Nach den Tagesfenstern der Phasenkarte (grober Rückfall) eher um den {date}.",
    "hank.today.bloom_question": "Wann hast du die ersten weißen Härchen gesehen? Trag den Tag in der Pflanzenkarte "
                                 "unter Blütebeginn ein.",
    "hank.today.bloom_remind": "Blütebeginn eintragen: bisher nur spätestens {date} bekannt.",
    "hank.today.height_old": "Höhe heute eintragen: zuletzt {value} am {date}.",
    "hank.today.height_none": "Höhe heute eintragen: noch keine Messung in der Pflanzenkarte.",
    "hank.digest.bloom": "Blütebeginn {date}, {by}",
    "hank.digest.bloom_latest": "Blütebeginn spätestens {date}",
    "hank.digest.height": "Höhe {value} ({date})",
    "hank.digest.bloom_by.owner": "von dir eingetragen",
    "hank.digest.bloom_by.plan": "aus plant.json",
    "hank.digest.bloom_by.stage": "ab deiner Blütephase",
    "hank.digest.bloom_by.estimate": "nur geschätzt",
}

EN: dict[str, str] = {
    "hank.explain.no_metric": "The box does not measure this.",
    "hank.explain.no_reading": "Right now the box gives no reading for it.",
    "hank.explain.reading": "{label}: {value}.",
    "hank.explain.draft": "From the glossary: draft, not yet approved.",
    "hank.explain.water_last": "The box does not measure this; last logged: {label} {value} on {date}.",
    "hank.explain.water_none": "The box does not measure this, and you have not logged a value yet.",
    "hank.explain.fans": "Right now: exhaust fan {dfan}, circulation fan {cfan}.",
    "hank.explain.spectrum": "The lamp is on the {name} spectrum right now.",
    "hank.explain.plan_on": "The box's light schedule is active right now; you set the hours in the Vivosun app.",
    "hank.explain.plan_off": "The box's light schedule is not active right now; you set the hours in the Vivosun app.",
    "hank.explain.howto_none": "I have no step-by-step guide.",
    "hank.explain.from_glossary": "I have no card of my own on that; the glossary says about {term}:",
    "hank.reading.height": "The plant is {value} tall, measured on {date}.",
    "hank.reading.height_old": "That was {days} days ago: enter today's height on the plant card, then I see at once "
                               "where we stand.",
    "hank.reading.height_none": "You have not entered a height yet. Enter it on the plant card, then I see where we "
                                "stand.",
    "hank.reading.unmeasured": "The box does not measure {label}.",
    "hank.reading.bloom": "Flowering for {days} days (flowering week {week}), counted from {date}, {by}.",
    "hank.reading.bloom_today": "Flowering since today (flowering week 1), counted from {date}, {by}.",
    "hank.reading.bloom_latest": "The flowering start is only estimated: {date} at the latest, because the first "
                                 "analysed camera image already showed white pistils.",
    "hank.reading.bloom_window": "By the stage card's day windows (a rough fallback) flowering began nearer {date}; "
                                 "today would be {stage} (since {since}).",
    "hank.reading.height_card": "Per the strain card, height indoors: {fact}.",
    "hank.reading.height_card_none": "How tall it will get is in none of my cards.",
    "hank.reading.bloom_none": "I do not know a flowering start yet.",
    "hank.reading.bloom_enter": "Enter the day of the first white pistils on the plant card under flowering start.",
    "hank.can.explain_term": "I can explain what {label} is.",
    "hank.can.explain_some": "I can explain terms such as {labels}.",
    "hank.can.box": "I can tell you how your box is doing right now.",
    "hank.can.problems": "I can tell you what is off target right now and what helps.",
    "hank.nodata.plain": "There is nothing in my card file about that yet.",
    "hank.nodata.term": "There is nothing in my card file about that yet, I only know the name {term}.",
    "hank.nodata.howto": "There is nothing in my card file about that yet, I have no guide for it.",
    "hank.nodata.howto_term": "There is nothing in my card file about that yet, I have no guide to {term}.",
    "hank.why.box": "In the box right now: {items}.",
    "hank.why.dfan": "exhaust fan {value}",
    "hank.why.cfan": "circulation fan {value}",
    "hank.why.port_on": "{label} ({port}) on",
    "hank.why.port_off": "{label} ({port}) off",
    "hank.why.auto_stage": "The humidifier automation waits because the stage is only estimated: it switches only "
                           "once you confirm the stage.",
    "hank.action.lead": "In the chat I switch nothing without you: the card below ({label}) waits for your tap.",
    "hank.action.lead_none": "In the chat I switch nothing without your tap, and I have no card to tap for this.",
    "hank.today.bloom_ask": "Flowering start {date} at the latest: the first analysed camera image already showed "
                            "white pistils.",
    "hank.today.bloom_window": "By the stage card's day windows (a rough fallback) nearer {date}.",
    "hank.today.bloom_question": "When did you see the first white pistils? Enter the day on the plant card under "
                                 "flowering start.",
    "hank.today.bloom_remind": "Enter the flowering start: so far only known as {date} at the latest.",
    "hank.today.height_old": "Enter today's height: last {value} on {date}.",
    "hank.today.height_none": "Enter today's height: no measurement on the plant card yet.",
    "hank.digest.bloom": "Flowering start {date}, {by}",
    "hank.digest.bloom_latest": "Flowering start {date} at the latest",
    "hank.digest.height": "Height {value} ({date})",
    "hank.digest.bloom_by.owner": "entered by you",
    "hank.digest.bloom_by.plan": "from plant.json",
    "hank.digest.bloom_by.stage": "from your flowering stage",
    "hank.digest.bloom_by.estimate": "only estimated",
}


def check() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash."""
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        de, en = i18n.placeholders(DE[key]), i18n.placeholders(EN[key])
        if de != en:
            problems.append(f"placeholders differ: {key} {sorted(de)} {sorted(en)}")
        if "lang" in de | en:
            problems.append(f"placeholder lang: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    """Check parity and put the texts into the server catalogs (i18n.DE, i18n.EN). Safe to call more than once."""
    problems = check()
    if problems:
        raise ValueError("asking: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _lang(lang: str) -> str:
    return lang if lang in ("de", "en") else "de"


def _dot(sentence: str) -> str:
    """One full stop where a German short date ends the sentence ("gemessen am 22.10." plus ".")."""
    return re.sub(r"(?<=\d)\.\.(?=\s|$)", ".", sentence)


# ---------- words ----------

_APOSTROPHES = re.compile("['’‘`´]")
_WORD = re.compile(r"\w+")


def words(q: str) -> list[str]:
    """The question's words, folded (text.fold), apostrophes dropped ("What's" is "whats"), "Hank" left out."""
    folded = _APOSTROPHES.sub("", text.fold(str(q or "")))
    return [w for w in _WORD.findall(folded) if w != "hank"]


_LEAD = frozenset({"und", "also", "ok", "okay", "na", "so", "hey", "hi", "hallo", "moin", "sag", "mal", "jo", "and",
                   "well", "so", "hello", "yo", "alright", "right", "hm", "hmm", "achso", "ah", "oh"})


def _without_lead(found: list[str]) -> list[str]:
    out = list(found)
    while out and out[0] in _LEAD:
        out = out[1:]
    return out


# ---------- question types ----------

_ARTICLES = frozenset({"ein", "eine", "einen", "einem", "einer", "der", "die", "das", "den", "dem", "des", "a", "an",
                       "the", "so", "son", "sone", "dieses", "diese", "dieser"})
_TRAIL = frozenset({"eigentlich", "genau", "denn", "nochmal", "bitte", "uberhaupt", "please", "exactly", "again",
                    "actually", "so", "mal", "eigentl", "ist", "sind", "bedeutet", "heisst", "is", "are", "means",
                    "mean", "for", "fur", "gut", "good", "used",
                    # chat filler at the end ("whats ppfd lol", review 1, S05)
                    "lol", "haha", "hahaha", "pls", "plz", "xd", "lmao", "bro", "digga", "alter", "eigentlich"})
_TIME = frozenset({"gerade", "aktuell", "jetzt", "momentan", "heute", "now", "currently", "today", "grad"})
# the words an explain subject may carry beside the term: where it is, not what about it
_QUALIFIERS = frozenset({"in", "im", "der", "die", "das", "dem", "den", "box", "vgrow", "growbox", "zelt", "meiner",
                         "meinem", "meine", "mein", "deiner", "bei", "mir", "an", "auf", "the", "my", "of", "a", "at",
                         "on", "for", "fur", "your", "deine", "ein", "eine", "einen",
                         # "der Unterschied zwischen Abluft und Umluft" is still about that term (review 1, E10)
                         "unterschied", "zwischen", "difference", "between", "und", "and", "vs", "versus"})
# the words a value question about a term may carry beside it ("Wie hoch ist der PPFD gerade?")
_VALUE_WORDS = frozenset({"wie", "hoch", "viel", "ist", "sind", "gerade", "aktuell", "jetzt", "momentan", "der",
                          "die", "das", "den", "dem", "mein", "meine", "meiner", "in", "im", "box", "vgrow", "bei",
                          "mir", "wert", "welchen", "welcher", "was", "how", "high", "much", "is", "the", "my",
                          "current", "currently", "now", "right", "what", "whats", "value", "level", "in", "of",
                          "box", "at", "grad", "es", "hat", "has", "steht", "stehen", "stufe", "step", "lauft",
                          "auf", "on", "welche", "welches", "eingestellt", "set", "running", "it", "eigentlich",
                          "stand", "von", "state"})

_EXPLAIN = [re.compile(p) for p in (
    r"^was (?:ist|sind|war|waren)(?: (?:eigentlich|genau|denn|nochmal|noch mal|uberhaupt))* (?P<s>.+)$",
    r"^was (?:bedeutet|bedeuten|heisst|heissen|meint|meinst du mit) (?P<s>.+)$",
    r"^was (?:versteht|meint) man (?:unter|mit) (?P<s>.+)$",
    r"^(?:erklar|erklare|erklarst|erklaer|erklaere) (?:du )?(?:mir )?(?:mal )?(?:bitte )?(?:kurz )?(?:was )?"
    r"(?P<s>.+)$",
    r"^(?:kannst|konntest) du (?:mir )?(?:mal )?(?:bitte )?(?:kurz )?(?:erklaren |erklaeren )?(?:was )?(?P<s>.+?)"
    r"(?: erklaren| erklaeren)?$",
    r"^wofur (?:steht|stehen|ist|sind|braucht man|brauche ich) (?P<s>.+)$",
    r"^(?:what is|what are|whats|what was|what were)(?: (?:actually|exactly|again))* (?P<s>.+)$",
    r"^what (?:dos|do) (?P<s>.+?) (?:mean|stand for)$",
    r"^what do you mean by (?P<s>.+)$",
    r"^what is meant by (?P<s>.+)$",
    r"^(?:explain|define|describe) (?:to me |me )?(?:what )?(?P<s>.+)$",
    r"^(?:can|could) you (?:please )?(?:explain|define|describe) (?:to me |me )?(?:what )?(?P<s>.+)$",
    r"^(?:meaning of|definition of) (?P<s>.+)$",
    # typed in a hurry (review 1, S07: "wht is vpd?" got the live reading, the owner's first bug with one typo)
    r"^(?:wht|wat|whta|waht|wass|was|what|wot) (?:is|isn|ist|iz|s|isses) (?P<s>.+)$",
)]
# "Was ist los?", "Was ist mit X?", "What is wrong with ...": no term is asked about
_NOT_EXPLAIN = re.compile(r"^(?:los|da los|denn los|mit|passiert|hier los|wrong|up|going on|happening|the matter|"
                          r"the problem|the issue|heute|today|der plan|the plan)\b")
_EXPLAIN_ONLY_VERB = re.compile(r"^(?:kannst|konntest) du ")  # needs "erklaren" in the question to be explain

_VALUE = [re.compile(p) for p in (
    r"^wie (?:warm|kalt|heiss|feucht|trocken|nass|hoch|gross|viel|hell|dunkel|laut|stark|alt|lang|weit|voll|leer)"
    r"(?: \w+)? (?:ist|sind|wird|war|steht|stehen|liegt|liegen|hat)\b",
    r"^wie (?:ist|sind|steht|stehen) (?:die|der|das|es|meine|mein|deine)\b",
    r"^wie ?viele?\b",
    r"^welchen? (?:wert|stand)\b",
    r"^how (?:warm|cold|hot|humid|dry|wet|high|tall|big|much|bright|loud|old|long|full|far) (?:is|are|was|does)\b",
    r"^how (?:is|are) (?:the|my|it|your)\b",
    r"^how many\b",
    r"^(?:whats|what is) the (?:current|actual|present)\b",
)]
_HOWTO = [re.compile(p) for p in (
    r"^wie (?:mache|mach|macht|kann|soll|muss|sollte|konnte|bekomme|kriege|lerne|fange|starte|baue|setze|stelle|"
    r"richte|benutze|nutze|verwende|gehe|mache ich|schneide|binde|dunge|giesse|ernte|trockne|lagere|senke|erhohe|"
    r"reduziere|verringere|verbessere|vermeide|verhindere|schaffe|bringe|halte) (?:ich|man|du)\b",
    r"^wie (?:geht|funktioniert|klappt)\b",
    r"\banleitung\b",
    r"^how (?:do|can|should|would|could|shall) (?:i|you|we|one)\b",
    r"^how to\b",
    r"^how (?:dos|does) (?:one|it work)\b",
    r"\b(?:guide|tutorial|instructions) (?:to|for|on)\b",
)]
_WHY = re.compile(r"(?:^| )(?:warum|wieso|weshalb|weswegen|why|how come)(?: |$)")
_HEIGHT_WORDS = frozenset({"hohe", "pflanzenhohe", "wuchshohe", "height"})
_HEIGHT_ADJ = frozenset({"hoch", "gross", "tall", "high", "big"})
_PLANT_WORDS = frozenset({"pflanze", "plant", "autoflower", "auto", "pflanzen", "plants"})
_HEIGHT_FUTURE = frozenset({"wird", "werden", "will", "grow", "grows", "wachst", "wachsen", "noch", "get", "gets",
                            "endhohe", "final", "maximal", "maximum", "max"})


@dataclass(frozen=True)
class Asked:
    """The type of a question (None: none of QTYPES) and, for explain, the words of what is asked about."""

    qtype: str | None
    subject: tuple[str, ...] = ()


def _trim(subject: list[str]) -> list[str]:
    out = list(subject)
    while out and out[0] in _ARTICLES:
        out = out[1:]
    while out and out[-1] in _TRAIL:
        out = out[:-1]
    return out


def classify(q: str) -> Asked:
    """The question's type, from its words alone (no search). value comes first ("Was ist die Luftfeuchte gerade?"
    asks for the reading), then explain, how-to and why."""
    found = _without_lead(words(q))
    joined = " ".join(found)
    if not found:
        return Asked(None)
    if any(p.search(joined) for p in _VALUE):
        return Asked("value")
    timed = bool(_TIME & set(found))
    for pattern in _EXPLAIN:
        m = pattern.match(joined)
        if m is None:
            continue
        if _EXPLAIN_ONLY_VERB.match(joined) and "erklaren" not in found and "erklaeren" not in found:
            continue  # "Kannst du das beheben?" is no explain question
        subject = m.group("s").strip()
        if _NOT_EXPLAIN.match(subject):
            break
        if timed and pattern.pattern.startswith(("^was (?:ist", "^(?:what is", "^(?:wht")):
            return Asked("value")  # "Was ist die Luftfeuchte gerade?"
        return Asked("explain", tuple(_trim(subject.split())))
    if any(p.search(joined) for p in _HOWTO):
        return Asked("howto")
    if _WHY.search(joined):
        return Asked("why")
    return Asked(None)


def asks_height(q: str) -> bool:
    """A value question about the plant's height ("Wie hoch ist die Pflanze?", "How tall is my plant?", "Höhe?").
    A question about how tall it will get ("Wie hoch wird meine Pflanze noch?", review 1, N14) is no reading: the
    search finds the strain card for it."""
    found = set(words(q))
    if found & _HEIGHT_FUTURE:
        return False
    if found & _HEIGHT_WORDS:
        return True
    return bool(found & _HEIGHT_ADJ) and bool(found & _PLANT_WORDS) and not found & _CLIMATE_WORDS


def asks_height_forecast(q: str) -> bool:
    """"Wie hoch wird meine Pflanze noch?", "How tall will my plant get?" (review 1, N14): today's height and what
    the strain card says about the height indoors, from code."""
    found = set(words(q))
    return bool(found & _HEIGHT_FUTURE) and bool(found & (_HEIGHT_ADJ | _HEIGHT_WORDS)) \
        and bool(found & _PLANT_WORDS) and not found & _CLIMATE_WORDS and len(found) <= 9


_CLIMATE_WORDS = frozenset({"luftfeuchte", "luftfeuchtigkeit", "feuchte", "feuchtigkeit", "temperatur", "vpd",
                            "licht", "humidity", "temperature", "light", "wasser", "water", "wasserstand", "lampe",
                            "lamp", "abluft", "umluft", "exhaust"})


# ---------- the glossary ----------

@dataclass(frozen=True)
class Term:
    key: str
    label: Mapping[str, str]
    text: Mapping[str, str]
    sources: tuple[str, ...]
    metric: str | None
    aliases: tuple[tuple[str, ...], ...]
    # v1.3 (review 1, 23.10.2026): a box device whose state Hank names instead of "not measured" (dfan_step,
    # cfan_step, light.spectrum, light.plan); a quantity (only then "Die Box misst das nicht."); "process" for a
    # hands-on technique (topping, LST, lollipopping), offered when a how-to has no card.
    devices: tuple[str, ...] = ()
    measurable: bool = False
    kind: str | None = None
    package: str | None = None  # v1.4: "cannabis" for a term of the cannabis package (the wiki's switch)

    def short(self, lang: str) -> str:
        """The label without its explanation in brackets: "VPD" of "VPD (Dampfdruckdefizit)"."""
        return (self.label.get(lang) or self.label.get("de") or self.key).split(" (")[0].strip()

    def inline(self, lang: str) -> str:
        """The short label inside an English sentence: "light spectrum", but "PPFD" and "VPD" stay as they are."""
        word = self.short(lang)
        return word if lang != "en" or word[:2].isupper() else word[:1].lower() + word[1:]


@dataclass
class Glossary:
    terms: list[Term] = field(default_factory=list)
    sources: dict[str, Any] = field(default_factory=dict)
    status: str = "draft"
    by_alias: dict[tuple[str, ...], str] = field(default_factory=dict)

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Glossary:
        out = cls(sources=dict(data.get("sources") or {}), status=str(data.get("status") or "draft"))
        for raw in data.get("terms") or []:
            if not isinstance(raw, Mapping) or not raw.get("key") or not raw.get("text"):
                continue
            label = {"de": str(raw.get("label") or raw["key"]), "en": str(raw.get("label_en") or raw.get("label")
                                                                         or raw["key"])}
            body = {"de": str(raw["text"]), "en": str(raw.get("text_en") or raw["text"])}
            aliases = raw.get("aliases") if isinstance(raw.get("aliases"), Mapping) else {}
            forms = [*(aliases.get("de") or []), *(aliases.get("en") or []), label["de"].split(" (")[0],
                     label["en"].split(" (")[0], label["de"], label["en"]]
            keys = []
            for form in forms:
                key = tuple(words(str(form)))
                if key and key not in keys:
                    keys.append(key)
            devices = raw.get("relates_to_device")
            devices = [devices] if isinstance(devices, str) else devices if isinstance(devices, list) else []
            term = Term(key=str(raw["key"]), label=label, text=body,
                        sources=tuple(str(s) for s in raw.get("sources") or []),
                        metric=str(raw["relates_to_metric"]) if raw.get("relates_to_metric") else None,
                        aliases=tuple(keys), devices=tuple(str(d) for d in devices if d),
                        measurable=raw.get("measurable") is True,
                        kind=str(raw["kind"]) if raw.get("kind") else None,
                        package=str(raw["package"]) if isinstance(raw.get("package"), str) and raw.get("package")
                        else None)
            out.terms.append(term)
            for key in keys:
                out.by_alias.setdefault(key, term.key)
        return out

    def get(self, key: str | None) -> Term | None:
        return next((term for term in self.terms if term.key == key), None) if key else None

    def by_metric(self, metric: str | None) -> Term | None:
        return next((term for term in self.terms if metric and term.metric == metric), None)

    def find(self, subject: Iterable[str]) -> Term | None:
        """The term a subject names: the whole subject is an alias, or (up to MAX_SUBJECT_WORDS words) the longest
        alias inside it as whole words ("Kohlefilter in der Box")."""
        found = _trim(list(subject))
        if not found:
            return None
        key = self.by_alias.get(tuple(found))
        if key is not None:
            return self.get(key)
        if len(found) > MAX_SUBJECT_WORDS:
            return None
        best: tuple[int, int, str] | None = None
        for alias, term_key in self.by_alias.items():
            n = len(alias)
            for i in range(len(found) - n + 1):
                if tuple(found[i:i + n]) != alias:
                    continue
                rest = set(found[:i] + found[i + n:])
                if rest <= _QUALIFIERS:  # "Kohlefilter in der Box", never "beste Luftfeuchte für die Blüte"
                    score = (n, len("".join(alias)))
                    if best is None or score > best[:2]:
                        best = (*score, term_key)
        return self.get(best[2]) if best else None

    def mentioned(self, q: str, *, short: bool = False) -> Term | None:
        """A term named anywhere in a question, longest alias first (for the "kann ich dir erklären" offer). short:
        also two- and three-letter aliases ("LST", "EC"), for a question that is about the term alone."""
        found = words(q)
        best: tuple[int, int, str] | None = None
        for alias, term_key in self.by_alias.items():
            n = len(alias)
            if not short and len(alias) == 1 and len(alias[0]) < 4:
                continue  # "ec", "ph", "rh" in passing are no reason to offer an explanation
            if any(tuple(found[i:i + n]) == alias for i in range(len(found) - n + 1)):
                score = (n, len("".join(alias)))
                if best is None or score > best[:2]:
                    best = (*score, term_key)
        return self.get(best[2]) if best else None

    def refs(self, term: Term, lang: str) -> list[SourceRef]:
        return [parse_source(sid, self.sources[sid], lang) for sid in term.sources if sid in self.sources]


_GLOSSARY: dict[str, Any] = {}


def glossary(path: Path | None = None) -> Glossary:
    """The glossary, read again when the file changed (mtime); an empty one when it is missing or unreadable."""
    path = path or GLOSSARY_FILE
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return Glossary()
    cached = _GLOSSARY.get(str(path))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        _LOGGER.warning("Hank glossary %s unreadable (%s)", path, err)
        return Glossary()
    found = Glossary.from_data(data if isinstance(data, Mapping) else {})
    _GLOSSARY[str(path)] = (stamp, found)
    return found


# ---------- topics and follow-ups ----------

# How a follow-up names a metric again in a written-out question (the router's state rule then finds it).
METRIC_WORDS = {
    "probe_humidity": ("Luftfeuchte", "humidity"), "humidity": ("Luftfeuchte", "humidity"),
    "probe_temp": ("Temperatur", "temperature"), "temp": ("Temperatur", "temperature"),
    "probe_vpd": ("VPD", "VPD"), "light_percent": ("Licht", "light"), "water_level": ("Wasserstand", "water level"),
    "cfan_step": ("Umluft", "circulation fan"), "dfan_step": ("Abluft", "exhaust fan"),
    "plant_height": ("Höhe der Pflanze", "plant height"), "bloom_day": ("Blütetag", "flowering day"),
    "height_forecast": ("Endhöhe der Pflanze", "final plant height"),
}


@dataclass
class Topic:
    """What the last answer of a chat was about: its question type, a metric, a device, a glossary term, the problems
    it listed ("probe_humidity:high", "stage"), an uncovered directory name a no_data answer named (name: "Zu
    Lollipopping habe ich keine Anleitung" after "how do I do that?"), the label to name it by, and when it was
    said."""

    qtype: str | None = None
    metric: str | None = None
    device: str | None = None
    term: str | None = None
    problems: tuple[str, ...] = ()
    label: Mapping[str, str] = field(default_factory=dict)
    created: float = 0.0
    name: str | None = None
    # the strain advice (strains.py, 23.10.2026): the wishes so far and the question Hank asked, so the reply
    # ("Autoflower", "Erster Grow") and a second advice question in the same chat find them
    advice: Mapping[str, Any] | None = None

    def empty(self) -> bool:
        return not (self.metric or self.device or self.term or self.problems or self.name or self.advice)


CLIENT_ID_MAX = 64  # a chat id longer than this is cut (review 2: no length limit, every id kept forever)


@dataclass
class TopicBook:
    ttl: float = TOPIC_TTL
    clock: Callable[[], float] = time.time
    topics: dict[str, Topic] = field(default_factory=dict)

    def get(self, client_id: str | None) -> Topic | None:
        if not client_id:
            return None
        client_id = client_id[:CLIENT_ID_MAX]
        found = self.topics.get(client_id)
        if found is None:
            return None
        if self.clock() - found.created > self.ttl:
            self.topics.pop(client_id, None)
            return None
        return found

    def remember(self, client_id: str | None, topic: Topic | None) -> None:
        """A new topic replaces the chat's last one; an empty one ("Danke!", a greeting) leaves it. Topics of other
        chats past the ttl are dropped here (review 2: after 1000 expired chats 999 entries were still held)."""
        if not client_id or topic is None or topic.empty():
            return
        now = self.clock()
        for old in [cid for cid, kept in self.topics.items() if now - kept.created > self.ttl]:
            self.topics.pop(old, None)
        topic.created = now
        self.topics[client_id[:CLIENT_ID_MAX]] = topic


_PRONOUN = r"(?:das|dies|es|sie|er|ihn|that|this|it|they|she|he)"
_FOLLOW_AGAIN = re.compile(
    r"^(?:und |and )?(?:das|dies|es|sie|er|ihn|that|this|it|they|she|he|damit|dazu|davon)$|"
    r"^(?:what|how) about (?:it|that|this|them)$|^was ist damit$|^und was ist damit$|"
    # "und wie ist sie gerade?" after "Was ist Luftfeuchte?" and "Danke!" (review 1, U08); "and how high is it now?"
    r"^(?:und |and )?(?:wie (?:ist|steht|hoch ist|viel ist|sieht) " + _PRONOUN + r"(?: aus)?|"
    r"how (?:is|high is|much is|tall is) (?:it|that|this|she|he)|what is it|whats it)"
    r"(?: (?:gerade|jetzt|heute|aktuell|now|right now|today|currently))?$")
_FOLLOW_WHY = re.compile(r"^(?:und |and |but |aber )?(?:warum|wieso|weshalb|why|how come)(?: (?:das|denn|nicht|so|"
                         r"is that|not|then|eigentlich))*$")
_FOLLOW_EXPLAIN = re.compile(r"^(?:was (?:bedeutet|heisst|ist|meint) (?:das|dies|es)(?: (?:eigentlich|genau|denn))*|"
                             r"what (?:dos|does) (?:that|this|it) mean|what is (?:that|this|it)|whats (?:that|this|it)|"
                             r"(?:erklar|erklare) (?:mir )?(?:das|es)(?: mal| bitte)*|explain (?:that|it|this))$")
# "how do I do that?" after "what is lollipopping" (review 1, U07)
_FOLLOW_HOWTO = re.compile(r"^(?:und |and )?(?:wie (?:mache|macht|mach|geht|funktioniert|klappt) (?:ich |man |du )?"
                           r"(?:das|es|dies)(?: (?:richtig|genau|am besten|denn))?|"
                           r"how (?:do|can|should|would) (?:i|you|we|one) (?:do )?(?:that|this|it)"
                           r"(?: (?:properly|right|exactly|best))?|how does (?:that|this|it) work|"
                           r"how to do (?:that|this|it))$")
_FOLLOW_ABOUT = [re.compile(p) for p in (
    r"^(?:und|and) (?:was ist )?(?:mit )?(?:der |dem |den |die |das |the |my |meiner |meinem )?(?P<s>.+)$",
    r"^(?:what|how) about (?:the |my )?(?P<s>.+)$",
    r"^was ist mit (?:der |dem |den |die |das |meiner |meinem )?(?P<s>.+)$",
)]


@dataclass(frozen=True)
class FollowUp:
    kind: str  # again, why, explain, howto, about
    question: str  # the written-out question, routed instead of the typed one


def _metric_word(metric: str | None, lang: str) -> str | None:
    pair = METRIC_WORDS.get(metric or "")
    return None if pair is None else pair[0 if lang == "de" else 1]


def _label(topic: Topic, lang: str, gloss: Glossary) -> str | None:
    term = gloss.get(topic.term) or gloss.by_metric(topic.metric)
    if term is not None and not topic.metric:
        return term.short(lang)
    return _metric_word(topic.metric, lang) or (term.short(lang) if term else None) or topic.label.get(lang)


def _status(state: LiveState | None, metric: str | None) -> str | None:
    if state is None or not metric:
        return None
    return next((str(r.get("status")) for r in state.checks if r.get("metric") == metric), None)


def _again(topic: Topic, label: str | None, lang: str, gloss: Glossary) -> FollowUp | None:
    """"Und das?" / "and how high is it now?": the topic's current value, written out so the router finds it. A term
    the box can say nothing about now (trichomes, topping) and an uncovered name have no value: no follow-up
    (review 2: "Was sind Trichome?" then "und das?" got the climate reading)."""
    de = lang == "de"
    if topic.metric == "plant_height":
        return FollowUp("again", "Wie hoch ist die Pflanze?" if de else "How tall is my plant?")
    if topic.metric == "height_forecast":
        return FollowUp("again", "Wie hoch wird die Pflanze noch?" if de else "How tall will my plant get?")
    if topic.metric == "bloom_day":
        return FollowUp("again", "Welcher Blütetag ist heute?" if de else "Which flowering day is it today?")
    if (topic.metric or topic.device) and label:
        return FollowUp("again", f"Wie ist {label} gerade?" if de else f"How is the {label} right now?")
    term = gloss.get(topic.term)
    if term is not None and (term.devices or term.measurable):
        word = term.inline(lang)
        return FollowUp("again", f"Wie ist der Stand von {word} gerade?" if de
                        else f"What is the state of the {word} right now?")
    return None


def follow_up(q: str, lang: str, topic: Topic | None, *, state: LiveState | None = None,
              gloss: Glossary | None = None, known: Callable[[str], bool] | None = None) -> FollowUp | None:
    """The question written out against the chat's last topic, or None (no topic, or not a follow-up).
    "Und das?" asks for the topic's reading, "Warum?" why it is off, "Was bedeutet das?" for the term, "Wie mache ich
    das?" for a how-to of the term or name, "Und die Luftfeuchte?" / "What about humidity?" asks the last question's
    type about a new subject (after an explanation an explanation, otherwise its reading). known(subject) says a
    subject is a directory name or a glossary term: an "und ..." that names neither is no follow-up ("und dann?")."""
    if topic is None:
        return None
    lang = _lang(lang)
    gloss = gloss or glossary()
    found = words(q)
    while found and found[0] in _LEAD - {"und", "and"}:
        found = found[1:]
    joined = " ".join(found)
    if not joined or len(found) > 7:
        return None
    label = _label(topic, lang, gloss)
    term = gloss.get(topic.term) or gloss.by_metric(topic.metric)
    named = term.short(lang) if term is not None else (topic.label.get(lang) if topic.name else None)
    if _FOLLOW_EXPLAIN.match(joined):
        if not named:
            return None
        return FollowUp("explain", f"Was ist {named}?" if lang == "de" else f"What is {named}?")
    if _FOLLOW_HOWTO.match(joined):
        if not named or topic.metric or topic.device:
            return None  # "wie mache ich das?" after a reading is a fix wish or a new question, not a how-to
        return FollowUp("howto", f"Wie mache ich {named}?" if lang == "de" else f"How do I do {named}?")
    if _FOLLOW_WHY.match(joined):
        if not label or not (topic.metric or topic.device):
            return None  # no reading to be off: "warum?" after a term or a name goes its own way (review 2)
        status = _status(state, topic.metric)
        if status in ("high", "low"):
            how = {"high": ("zu hoch", "too high"), "low": ("zu niedrig", "too low")}[status][0 if lang == "de" else 1]
            return FollowUp("why", f"Warum ist {label} {how}?" if lang == "de" else f"Why is the {label} {how}?")
        return FollowUp("why", f"Warum ist {label} so?" if lang == "de" else f"Why is the {label} like that?")
    if _FOLLOW_AGAIN.match(joined):
        return _again(topic, label, lang, gloss)
    for pattern in _FOLLOW_ABOUT:
        m = pattern.match(joined)
        if m is None:
            continue
        subject = _trim(m.group("s").split())
        if not subject or len(subject) > 3:
            return None
        said = " ".join(subject)
        named_now = gloss.find(subject) is not None or (known is not None and known(said))
        if not named_now:
            return None
        if topic.qtype == "explain":
            return FollowUp("about", f"Was ist {said}?" if lang == "de" else f"What is {said}?")
        return FollowUp("about", f"Wie ist {said} gerade?" if lang == "de" else f"How is the {said} right now?")
    return None


_SINGULAR = frozenset({"it", "this", "that", "das", "es", "dies", "dagegen", "da", "dem", "den", "die"})
_PLURAL = frozenset({"them", "these", "those", "things", "issus", "problems", "probleme", "sachen", "dinge", "diese"})
_ALL = frozenset({"everything", "alles", "all"})


def fix_scope(wish: str, topic: Topic | None) -> tuple[list[str] | None, dict[str, Any] | None]:
    """The problem keys a typed fix wish refers to, and what it is about: "can you fix it?" after "what is VPD" is
    about VPD (keys probe_vpd:high and probe_vpd:low), "can you fix those things?" about the problems the last answer
    listed. A wish for everything ("Fix everything", "Kannst du alles beheben?"), a bare one ("Was soll ich tun?") or
    one without a topic covers every problem (None)."""
    said = set(str(wish or "").split())
    if topic is None or said & _ALL:
        return None, None
    if said & _PLURAL and topic.problems:
        return list(topic.problems), None
    if said & (_SINGULAR | _PLURAL):
        if topic.metric:
            return [f"{topic.metric}:high", f"{topic.metric}:low"], {"metric": topic.metric, "term": topic.term,
                                                                      "label": dict(topic.label)}
        if topic.term:
            return [], {"metric": None, "term": topic.term, "label": dict(topic.label)}
        if topic.problems:
            return list(topic.problems), None
    return None, None


def topic_label(gloss: Glossary, *, metric: str | None = None, term: str | None = None,
                fallback: Mapping[str, str] | None = None) -> dict[str, str]:
    found = gloss.get(term) or gloss.by_metric(metric)
    out = {}
    for lang in ("de", "en"):
        word = (found.short(lang) if found is not None and not metric else None) or _metric_word(metric, lang) \
            or (found.short(lang) if found else None) or (fallback or {}).get(lang)
        if word:
            out[lang] = word
    return out


# ---------- routing the typed questions ----------

_STATUS = re.compile(r"^(?:wie (?:geht es|gehts|geht s|steht es um|lauft es (?:mit|in|bei)|sieht es (?:mit|in|bei) "
                     r"(?:aus )?)(?: (?:mit|in|bei|um))? (?:meiner|meinen|meine|der|die|dem|den|deiner) "
                     r"(?:box|growbox|vgrow|pflanze|pflanzen|grow)(?: aus| so| gerade| heute)*"
                     r"|how (?:is|are|s) (?:my|the) (?:box|grow box|growbox|plant|plants|grow)(?: doing| going)?"
                     r"(?: today| right now| now)?"
                     r"|hows (?:my|the) (?:box|plant|grow)(?: doing| going)?(?: today| now)?)$")


def asks_status(q: str) -> bool:
    """"Wie geht es meiner Box?", "How is my plant doing?": the today list is that answer (what is open)."""
    return bool(_STATUS.match(" ".join(_without_lead(words(q)))))


# the words a how-to about a term may carry beside it ("How do I do LST?", "Wie mache ich Lollipopping richtig?")
_HOWTO_WORDS = frozenset({"wie", "mache", "mach", "macht", "ich", "man", "du", "das", "richtig", "am", "besten", "genau",
                          "how", "do", "i", "you", "to", "does", "one", "it", "properly", "correctly", "best", "the",
                          "a", "an", "my", "der", "die", "den", "dem", "meiner", "meine", "mein", "pflanze", "plant",
                          "bei", "mit", "with", "on", "kann", "can", "should", "soll", "sollte", "geht", "funktioniert",
                          "work", "works", "eine", "einen", "ein", "an", "anleitung", "guide", "for", "fur", "zu",
                          "zum", "beim", "lerne", "learn", "start", "starte", "anfangen", "fange", "please", "bitte",
                          "mal", "eigentlich", "denn", "we", "wir", "autoflower", "auto", "meinem"})
# "Welcher Tag der Blüte ist heute?", "Seit wann blüht sie?", "Which flowering week is it?" (review 1, V09)
_BLOOM_WORDS = frozenset({"blute", "bluhen", "bluht", "flowering", "bloom", "blooming", "flower", "flowered",
                          "blutetag", "blutewoche", "bluhtag", "bluhwoche", "blutephase"})
_BLOOM_WHEN = frozenset({"tag", "tage", "tagen", "woche", "wochen", "day", "days", "week", "weeks", "wann", "lange",
                         "long", "since", "blutetag", "blutewoche", "bluhtag", "bluhwoche"})
_BLOOM_DAY_WORDS = _BLOOM_WORDS | _BLOOM_WHEN | frozenset({
    "welcher", "welche", "welches", "wie", "viel", "viele", "seit", "der", "die", "das", "im", "in", "ist", "sind",
    "heute", "gerade", "jetzt", "meine", "meiner", "pflanze", "wir", "sie", "es", "schon", "steht", "stehen", "uns",
    "befinden", "aktuell", "what", "which", "of", "is", "it", "are", "we", "the", "my", "plant", "how", "many", "when",
    "today", "now", "has", "been", "she", "at", "on", "current", "currently", "hat", "haben", "ihre", "her", "its",
    "denn", "eigentlich", "nummer", "number", "zahlt", "count", "gezahlt", "counted", "started", "begonnen", "an",
    "angefangen", "begann", "start", "beginn"})


def asks_bloom_day(q: str) -> bool:
    """A question for the day or week of flowering, made only of such words (so "Wie viel Dünger in Blütewoche 3?"
    stays a feeding question)."""
    found = words(q)
    if not found or not set(found) <= _BLOOM_DAY_WORDS:
        return False
    return bool(set(found) & _BLOOM_WORDS) and bool(set(found) & _BLOOM_WHEN)


def typed_route(q: str, lang: str, asked: Asked, *, gloss: Glossary | None = None) -> Route | None:
    """The route of a typed question that code answers without the search: explain (a glossary term; a how-to about
    one gets its text and "no step-by-step guide"), reading (the plant's height, the day of flowering, a glossary term
    the box shows as a device state or does not measure) or today for a status question ("Wie geht es meiner Box?",
    what hank.can.box promises). None: the search decides."""
    gloss = gloss or glossary()
    if asks_status(q):
        return Route("today", None, None, None, "info", lang, "today:status", qtype="value")
    if asks_bloom_day(q):
        return Route("reading", None, None, None, "info", lang, "reading:bloom_day", qtype="value", term="bloom_day")
    from . import feedplan  # feeding v2: a stated age, the plant's age, the dated plan, the fertilizer choice

    planned = feedplan.route_for(q, lang)
    if planned is not None:
        return planned
    if asks_height_forecast(q):
        return Route("reading", None, None, None, "info", lang, "reading:height_forecast", qtype="value",
                     term="height_forecast")
    if asked.qtype == "explain":
        term = gloss.find(asked.subject)
        if term is not None:
            return Route("explain", None, None, None, "info", lang, f"explain:{term.key}", qtype="explain",
                         term=term.key)
        return None
    if asked.qtype == "howto":
        term = gloss.mentioned(q, short=True)
        if term is not None and _only_term(q, term, _HOWTO_WORDS):
            return Route("explain", None, None, None, "info", lang, f"howto:{term.key}", qtype="howto", term=term.key)
        return None
    if asked.qtype == "value":
        if asks_height(q):
            return Route("reading", None, None, None, "info", lang, "reading:plant_height", qtype="value",
                         term="plant_height")
        term = gloss.mentioned(q, short=True)
        if term is not None and term.metric is None and (term.devices or term.measurable) \
                and _only_term(q, term, _VALUE_WORDS):
            return Route("reading", None, None, None, "info", lang, f"reading:term:{term.key}", qtype="value",
                         term=term.key)
    return None


def _only_term(q: str, term: Term, allowed: frozenset[str] = _VALUE_WORDS) -> bool:
    """The question is about the term and nothing else: "Wie hoch ist der PPFD gerade?", not "Wie viel Licht braucht
    eine Tomate?"; allowed: the words it may carry beside the term."""
    found = words(q)
    for alias in sorted(term.aliases, key=len, reverse=True):
        n = len(alias)
        for i in range(len(found) - n + 1):
            if tuple(found[i:i + n]) == alias:
                return set(found[:i] + found[i + n:]) <= allowed
    return False


def term_like(qtype: str | None, subject: Iterable[str], domain: Iterable[str] = ()) -> bool:
    """An explain question about one growing term ("Was ist Schwefelmangel?"): one or two words, none an everyday
    word (text.COMMON) or shorter than four letters, and one of them holds a growing word of the cards (domain, five
    letters or more: "schwefel"). "What's a nice name for a goldfish?" and "Was ist Quantenphysik?" are none."""
    found = [w for w in subject if w not in _ARTICLES]
    if qtype != "explain" or not 1 <= len(found) <= 2:
        return False
    if not all(len(w) >= 4 and w not in text.COMMON and not w.isdigit() for w in found):
        return False
    grow = [d for d in domain if len(d) >= 5]
    return any(d in w for w in found for d in grow)


TYPED_GROW = ("explain", "howto", "why")  # question types that are growing questions even without a card


def entity_term(entry: Mapping[str, Any] | None, gloss: Glossary) -> Term | None:
    """The glossary term a name of the directory (kartei/_hank/vocab.json) stands for: carbon_filter is the term
    carbon-filter, by any of its forms in either language. None when no form is a glossary term."""
    if not isinstance(entry, Mapping):
        return None
    for lang in ("de", "en"):
        forms = entry.get(lang) or []
        for form in [forms] if isinstance(forms, str) else forms:
            term = gloss.find(words(str(form)))
            if term is not None:
                return term
    return None


def only_term(q: str, term: Term) -> bool:
    """The question names the term with nothing but question words around it ("Was isn DLI?", "Aktivkohlefilter?"):
    it is an explain question, whatever its form (review 1, MUST 3)."""
    return _only_term(q, term, _VALUE_WORDS | _HOWTO_WORDS | _TRAIL | _ARTICLES | frozenset(
        {"was", "ist", "isn", "is", "wht", "what", "whats", "erklar", "erklare", "explain", "bedeutet", "heisst",
         "means", "mean", "sind", "are"}))


# ---------- answers ----------

def _with_marks(body: str, count: int) -> str:
    """Source marks before the end of the text: "... wächst langsamer [1] [2]."."""
    body = body.strip()
    if not count:
        return body
    end = body[-1] if body and body[-1] in ".!?" else "."
    marks = " ".join(f"[{n}]" for n in range(1, count + 1))
    return f"{body.rstrip('.!?').rstrip()} {marks}{end}"


def _fan(state: LiveState, key: str, lang: str) -> str:
    step = (state.snapshot.get(key) or {}).get("step") if isinstance(state.snapshot.get(key), dict) else None
    if step is None or isinstance(step, bool):
        return t("hank.digest.none", lang)
    return t("hank.digest.fan_step", lang, step=step)


def device_sentence(term: Term, state: LiveState, lang: str) -> str | None:
    """The state of the box device a term belongs to ("Gerade: Abluft Stufe 3, Umluft kein Wert."), instead of
    "Die Box misst das nicht." (review 1: "Wie hoch ist die Abluft gerade?" got "misst die Box nicht")."""
    light = state.snapshot.get("light") if isinstance(state.snapshot.get("light"), dict) else {}
    out: list[str] = []
    if {"dfan_step", "cfan_step"} & set(term.devices):
        out.append(t("hank.explain.fans", lang, dfan=_fan(state, "dfan", lang), cfan=_fan(state, "cfan", lang)))
    if "light.spectrum" in term.devices:
        spectrum = light.get("spectrum")
        if spectrum in ("seedling", "veg", "bloom"):
            out.append(t("hank.explain.spectrum", lang, name=t(f"hank.digest.spectrum.{spectrum}", lang)))
        else:
            out.append(t("hank.explain.no_reading", lang))
    if "light.plan" in term.devices:
        plan = light.get("in_plan")
        out.append(t("hank.explain.plan_on" if plan else "hank.explain.plan_off", lang) if isinstance(plan, bool)
                   else t("hank.explain.no_reading", lang))
    return " ".join(out) or None


_WATER = {"ec": ("EC", "mS/cm"), "ph": ("pH", None)}


def water_sentence(term: Term, state: LiveState, lang: str) -> str | None:
    """EC and pH: the box does not measure them, so the last value the owner logged on the plant card, with its day
    ("Die Box misst das nicht; zuletzt eingetragen: EC 1,6 mS/cm am 22.10.")."""
    if term.key not in _WATER:
        return None
    label, unit = _WATER[term.key]
    rows = state.plant_view.get("water") if isinstance(state.plant_view.get("water"), list) else []
    for row in reversed(rows):
        value = row.get(term.key) if isinstance(row, dict) else None
        if value is None or isinstance(value, bool):
            continue
        shown = fmt_qty(value, unit, lang) if unit else i18n.fmt_num(value, lang)
        return _dot(t("hank.explain.water_last", lang, label=label, value=shown,
                      date=day_text(str(row.get("time") or "")[:10], lang)))
    return t("hank.explain.water_none", lang)


def live_sentence(term: Term, state: LiveState, lang: str) -> str | None:
    """The one sentence after the explanation: the live reading of the term's metric with its verdict, the state of
    its device, the owner's last logged value (EC, pH), or for any other quantity that the box does not measure it.
    None for a term that is no quantity (topping, trichomes): "Die Box misst das nicht." under those read silly
    (review 1)."""
    if term.metric is None:
        if term.devices:
            return device_sentence(term, state, lang)
        if term.measurable:
            return water_sentence(term, state, lang) or t("hank.explain.no_metric", lang)
        return None
    found = verdicts.build(state, lang, metrics=[term.metric])
    if found.stale:
        return found.stale_sentence or t("hank.explain.no_reading", lang)
    if found.verdicts:
        return verdicts.once_estimate([found.verdicts[0].sentence], lang)[0]
    value = state.climate(term.metric)
    if value is None:
        return t("hank.explain.no_reading", lang)
    unit = {"probe_vpd": "kPa", "vpd": "kPa"}.get(term.metric, "%" if "humidity" in term.metric else "°C")
    return t("hank.explain.reading", lang, label=verdicts.label(term.metric, term.short(lang), lang),
             value=reading(value, term.metric, unit, lang))


def explain_answer(route: Route, live: LiveState | Mapping[str, Any], *, answer_id: str,
                   gloss: Glossary | None = None) -> Answer:
    """"Was ist X?": the term's text first with its sources, then the live sentence (if the term has one), then the
    draft note. A how-to about a term (route.qtype "howto") gets the text and "Eine Schritt-für-Schritt-Anleitung
    habe ich nicht." instead of the live sentence. A no_data question that names a term (route no_data with a term,
    "Wie oft muss ich den Aktivkohlefilter wechseln?") opens with "Eine eigene Karte habe ich dazu nicht, im Glossar
    steht zu X:" and stays no_data (on the gap list)."""
    gloss = gloss or glossary()
    lang = _lang(route.lang)
    term = gloss.get(route.term)
    if term is None:  # the glossary changed between routing and answering
        return Answer(id=answer_id, route="no_data", lang=lang, text=no_data_text(route, None), gap=True)
    refs = gloss.refs(term, lang)
    lines = []
    nodata = route.name == "no_data"
    if nodata:
        lines.append(t("hank.explain.from_glossary", lang, term=term.short(lang)))
    lines.append(_with_marks(term.text.get(lang) or term.text["de"], len(refs)))
    if route.qtype == "howto":
        lines.append(t("hank.explain.howto_none", lang))
    else:
        live_line = live_sentence(term, collect(live), lang)
        if live_line:
            lines.append(live_line)
    if gloss.status != "approved":
        lines.append(t("hank.explain.draft", lang))
    return Answer(id=answer_id, route="no_data" if nodata else "explain", lang=lang, text="\n".join(lines),
                  sources=refs, gap=nodata)


def height_lines(state: LiveState, lang: str) -> list[str]:
    height = state.plant_view.get("height") if isinstance(state.plant_view.get("height"), dict) else None
    if not height or height.get("cm") is None:
        return [t("hank.reading.height_none", lang)]
    lines = [_dot(t("hank.reading.height", lang, value=fmt_qty(height["cm"], "cm", lang),
                    date=day_text(height.get("date"), lang)))]
    age = height_age(state)
    if age is not None and age > plant.HEIGHT_DUE_DAYS:
        lines.append(t("hank.reading.height_old", lang, days=age))
    return lines


def height_age(state: LiveState) -> int | None:
    """Days since the newest height, counted on the moment's own day (the fixtures freeze it)."""
    height = state.plant_view.get("height") if isinstance(state.plant_view.get("height"), dict) else None
    day = plant.parse_day((height or {}).get("date"))
    if day is None:
        return None
    today = state.now.date() if isinstance(state.now, datetime) else date.today()
    return (today - day).days


_SOURCE_MARK = re.compile(r"\s*\((?:[SGNW]\d+)(?:\s*,\s*[SGNW]\d+)*\)")
HEIGHT_FACT = "Höhe indoor"  # the strain card's fact key (kartei/strains/, the key is the same in both languages)


def height_card_line(state: LiveState, lang: str) -> str:
    """What the strain card says about the height indoors, word for word without its source marks, and the card's
    warning when the plant can outgrow the box. Never the card's other facts."""
    strain = state.plant_view.get("strain") if isinstance(state.plant_view.get("strain"), dict) else {}
    facts = strain.get("facts") if isinstance(strain.get("facts"), dict) else {}
    fact = facts.get(HEIGHT_FACT)
    if not isinstance(fact, str) or not fact.strip():
        return t("hank.reading.height_card_none", lang)
    out = _dot(t("hank.reading.height_card", lang, fact=_SOURCE_MARK.sub("", fact).strip().rstrip(".")))
    warnings = [w for w in strain.get("warnings") or [] if isinstance(w, str) and w.strip()]
    return f"{out} {warnings[0].strip()}" if warnings else out


def _bloom(state: LiveState) -> dict[str, Any]:
    bloom = state.plant_view.get("bloom")
    return bloom if isinstance(bloom, dict) else {}


def bloom_lines(state: LiveState, lang: str) -> list[str]:
    """"Welcher Tag der Blüte ist heute?": days and week since the bloom start with where the date comes from; while
    only the camera's estimate stands behind it, that it is an upper bound ("spätestens 22.10."), the stage card's
    rough window and the ask for the real day (owner, 23.10.2026: "Stage ist bestimmt nicht ein Tag")."""
    bloom = _bloom(state)
    day, by = bloom.get("date"), bloom.get("by")
    suggest = bloom.get("suggest") if isinstance(bloom.get("suggest"), dict) else None
    window = None
    if suggest and suggest.get("date"):
        window = _dot(t("hank.reading.bloom_window", lang, date=day_text(suggest["date"], lang),
                        stage=suggest.get("stage") or "?", since=day_text(suggest.get("stage_since"), lang)))
    if not day:
        return [line for line in (t("hank.reading.bloom_none", lang), window, t("hank.reading.bloom_enter", lang))
                if line]
    if bloom.get("latest") or bloom.get("estimated"):
        return [line for line in (t("hank.reading.bloom_latest", lang, date=day_text(day, lang)), window,
                                  t("hank.reading.bloom_enter", lang)) if line]
    days = bloom.get("flower_day")
    where = t(f"hank.digest.bloom_by.{by}", lang) if by in ("owner", "plan", "stage", "estimate") else str(by or "?")
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        return [t("hank.reading.bloom_none", lang), t("hank.reading.bloom_enter", lang)]
    if days == 0:
        return [t("hank.reading.bloom_today", lang, date=day_text(day, lang), by=where)]
    return [t("hank.reading.bloom", lang, days=days, week=days // 7 + 1, date=day_text(day, lang), by=where)]


def reading_answer(route: Route, live: LiveState | Mapping[str, Any], *, answer_id: str,
                   gloss: Glossary | None = None) -> Answer:
    gloss = gloss or glossary()
    lang = _lang(route.lang)
    state = collect(live)
    if route.term == "plant_height":
        lines = height_lines(state, lang)
    elif route.term == "bloom_day":
        lines = bloom_lines(state, lang)
    elif route.term == "height_forecast":
        lines = [*height_lines(state, lang), height_card_line(state, lang)]
    elif route.term in ("age_claim", "plant_age", "feed_plan", "feed_choice"):
        from . import feedplan  # feeding v2: code text and the claim's card (core.Hank registers the card)

        return feedplan.reading(route, state, answer_id=answer_id)
    else:
        term = gloss.get(route.term)
        label = term.inline(lang) if term else str(route.term)
        said = live_sentence(term, state, lang) if term is not None else None
        if said is None or said == t("hank.explain.no_metric", lang):
            said = t("hank.reading.unmeasured", lang, label=label)
        lines = [said, t("hank.can.explain_term", lang, label=label)]
    return Answer(id=answer_id, route="reading", lang=lang, text=" ".join(lines))


def _joined(labels: list[str], lang: str) -> str:
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + (" oder " if lang == "de" else " or ") + labels[-1]


def _can_explain(gloss: Glossary, lang: str, keys: Iterable[str] = CAN_EXPLAIN) -> str:
    on = packages_on()  # v1.4: never offer a term of a package that is switched off
    labels = [term.short(lang) for key in keys
              if (term := gloss.get(key)) is not None and (not term.package or term.package in on)]
    if not labels:
        return t("hank.can.box", lang)
    return t("hank.can.explain_some", lang, labels=_joined(labels, lang))


def no_data_text(route: Route, name: str | None, *, question: str = "", gloss: Glossary | None = None) -> str:
    """No card for a typed question: one plain sentence and what Hank can do instead (owner, 23.10.2026: "either he
    can help or not"). name: the uncovered directory name the router found, if any. A glossary term is never called
    "nur den Namen" (review 1, MUST 3): Hank can explain it, so that is the offer. A how-to without a card offers the
    techniques the glossary explains, not the box status (review 1, H01)."""
    gloss = gloss or glossary()
    lang = _lang(route.lang)
    qtype = route.qtype
    term = gloss.find(words(name)) if name else None  # "Wie mache ich LST?": LST is a glossary term
    term = term or (gloss.mentioned(question) if question else None)
    if qtype == "howto":
        first = t("hank.nodata.howto_term", lang, term=name) if name else t("hank.nodata.howto", lang)
        then = t("hank.can.explain_term", lang, label=term.short(lang)) if term else \
            _can_explain(gloss, lang, CAN_EXPLAIN_HOWTO)
        return f"{first} {then}"
    first = t("hank.nodata.term", lang, term=name) if name and term is None else t("hank.nodata.plain", lang)
    if qtype == "explain":
        then = t("hank.can.explain_term", lang, label=term.short(lang)) if term else _can_explain(gloss, lang)
    elif qtype == "why":
        then = t("hank.can.problems", lang)
    elif term is not None:
        then = t("hank.can.explain_term", lang, label=term.short(lang))
    else:
        then = t("hank.can.box", lang)
    return f"{first} {then}"


# ---------- leads of a growing answer (review 1, SHOULD 6 and 9) ----------

_CLIMATE = frozenset({"probe_humidity", "humidity", "probe_temp", "temp", "probe_vpd", "vpd"})
_SWITCH_KINDS = ("light", "spectrum", "dfan", "cfan", "port", "auto_humidifier")


def why_lead(route: Route, state: LiveState, lang: str) -> str | None:
    """The first line of a why answer about the climate or the automation: what the box is doing right now (exhaust
    step, circulation step, the humidifier's port), or why the humidifier automation waits (the stage is only
    estimated). Facts of the live state only; the card's advice follows in the answer."""
    if route.qtype != "why":
        return None
    reason = (route.reason or "").rsplit(";", 1)[-1]
    auto = state.automation_view
    # "Wieso ist der Befeuchter aus?" (device humidifier) already opens with the automation's reason (compose)
    if reason == "state:device:automation" and auto.get("enabled") and auto.get("needs_stage_confirmation"):
        return t("hank.why.auto_stage", lang)
    if not reason.startswith("state:metric:") or reason.split(":", 2)[2] not in _CLIMATE:
        return None
    items = []
    for fan, key in (("dfan", "hank.why.dfan"), ("cfan", "hank.why.cfan")):
        step = (state.snapshot.get(fan) or {}).get("step") if isinstance(state.snapshot.get(fan), dict) else None
        if isinstance(step, int) and not isinstance(step, bool):
            items.append(t(key, lang, value=t("hank.digest.fan_step", lang, step=step)))
    port = str(auto.get("port") or "")
    ports = state.snapshot.get("ports") if isinstance(state.snapshot.get("ports"), dict) else {}
    on = (ports.get(port) or {}).get("on") if port and isinstance(ports.get(port), dict) else None
    if isinstance(on, bool):
        items.append(t("hank.why.port_on" if on else "hank.why.port_off", lang, label=state.port_label(port, lang),
                       port=port_name(port)))
    if not items:
        return None
    text = ", ".join(items)
    return t("hank.why.box", lang, items=text[:1].upper() + text[1:] if lang == "en" else text)


def action_lead(cards: Iterable[Any], lang: str) -> str:
    """The first line of an answer to a switching wish ("Kannst du die Lampe auf 100 % stellen?"): in the chat
    nothing switches without the owner; the card below does, after the owner's tap (review 1, N04). What runs under
    "Hank regelt" is the autopilot by its fixed rules, never the language model."""
    cards = list(cards)
    card = next((c for c in cards if getattr(c, "kind", None) in _SWITCH_KINDS), cards[0] if cards else None)
    if card is None or not getattr(card, "label", None):
        return t("hank.action.lead_none", lang)
    return t("hank.action.lead", lang, label=card.label)


# ---------- today: the bloom start and the height ----------

ASKED_FILE = "asked.json"  # under STATE_DIR/hank: what the today list has asked once


def _asked_path() -> Path:
    return config.STATE_DIR / "hank" / ASKED_FILE


def asked_before(what: str, value: str) -> bool:
    try:
        data = json.loads(_asked_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get(what) == value


def mark_asked(what: str, value: str) -> None:
    try:
        data = json.loads(_asked_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    data[what] = value
    config.atomic_write(_asked_path(), json.dumps(data, indent=2) + "\n")


def bloom_estimate(state: LiveState) -> str | None:
    """The bloom start's date while only the camera's estimate stands behind it (plant.bloom_view), else None."""
    bloom = state.plant_view.get("bloom") if isinstance(state.plant_view.get("bloom"), dict) else {}
    return str(bloom["date"]) if bloom.get("estimated") and bloom.get("date") else None


def bloom_line(state: LiveState, lang: str, *, asked: bool) -> str | None:
    """The today list's question for the real bloom start, asked once per estimated date; after that a short
    reminder while the date is still only estimated. The estimate is an upper bound ("spätestens 22.10.": the first
    analysed image already showed white pistils), with the stage card's rough window when it gives an earlier day."""
    day = bloom_estimate(state)
    if day is None:
        return None
    if asked:
        return _dot(t("hank.today.bloom_remind", lang, date=day_text(day, lang)))
    parts = [t("hank.today.bloom_ask", lang, date=day_text(day, lang))]
    suggest = _bloom(state).get("suggest")
    if isinstance(suggest, dict) and suggest.get("date"):
        parts.append(_dot(t("hank.today.bloom_window", lang, date=day_text(suggest["date"], lang))))
    parts.append(t("hank.today.bloom_question", lang))
    return " ".join(parts)


def height_due(state: LiveState, lang: str) -> str | None:
    """"Höhe heute eintragen" when the newest height is older than two days (or there is none)."""
    if not state.plant_view.get("configured", True) or not state.plant_view:
        return None
    height = state.plant_view.get("height") if isinstance(state.plant_view.get("height"), dict) else None
    if not height or height.get("cm") is None:
        return t("hank.today.height_none", lang)
    age = height_age(state)
    if age is None or age <= plant.HEIGHT_DUE_DAYS:
        return None
    return _dot(t("hank.today.height_old", lang, value=fmt_qty(height["cm"], "cm", lang),
                  date=day_text(height.get("date"), lang)))


__all__ = ["DE", "EN", "QTYPES", "Asked", "FollowUp", "Glossary", "Term", "Topic", "TopicBook", "action_lead",
           "asks_bloom_day", "asks_height", "bloom_line", "bloom_lines", "check", "classify", "explain_answer",
           "fix_scope", "follow_up", "glossary", "height_due", "live_sentence", "no_data_text", "only_term",
           "reading_answer", "register", "typed_route", "why_lead", "words"]
