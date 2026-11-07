"""Hank's watering questions from the Durst-Index (vgrow/thirst.py): "Muss ich gießen?", "Wann gieße ich?", "Wie viel
gieße ich?", "Der Tank ist voll, soll ich gießen?" and their English twins, answered from code with the index, the
forecast, why, and the one-tap check on the card. The today list and the traffic light name the watering when
something is to do.

Hank only reads. The server hands him the Durst-Index's brief: server.HankBox puts it into the snapshot his collector
reads, under "thirst" ({configured, ti, kind, reason, text, why, check, forecast, planned_l, cap_l, pump_amount, ...},
every text in both languages). Nothing here imports vgrow/thirst.py, and this module switches nothing: the check is a
tap on the card, watering is the owner's, a scheduled slot runs only through the need check.

Where it hangs in (one line each, so the other modules stay as they are):
- router.py: a watering question about this plant (asks()) is the route "thirst", after the pump history question
  ("Wurde heute schon gegossen?" keeps its own route) and the safety gates; core answers it here (answer()).
- today.py: today_lines(), the watering line of the today list, only when something is to do.
- nudges.py: light(), yellow while the watering asks, is due, is blocked or alerts; else nothing.
A full tank is never a reason to water: a question about the full tank gets exactly that sentence first.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from vgrow import i18n
from vgrow.i18n import fmt_qty, t

from .livestate import LiveState, collect
from .text import fold
from .types import Answer

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
ROUTE = "thirst"
TODO_KINDS = ("ASK", "PROPOSE", "BLOCKED", "ALERT")  # what the today list and the light name; WAIT is no to-do

DE: dict[str, str] = {
    "hank.thirst.none": "Der Durst-Index meldet sich gerade nicht. Auf der Karte Durst-Index unter Automatik steht, "
                        "was der Topf braucht.",
    "hank.thirst.tank_full": "Ein voller Tank ist für mich nie ein Grund zu gießen: ob gegossen wird, sagt der Topf.",
    "hank.thirst.term": "Durst-Index",
    "hank.thirst.gloss": "(wie viel Wasser dem Topf fehlt; 100 heißt fällig)",
    "hank.thirst.lead.WAIT": "Nein, noch nicht.",
    "hank.thirst.lead.WAIT_round": "Ja, zur nächsten Runde.",
    "hank.thirst.lead.ASK": "Erst prüfen.",
    "hank.thirst.lead.PROPOSE": "Ja.",
    "hank.thirst.lead.BLOCKED": "Nein, gerade gesperrt.",
    "hank.thirst.lead.ALERT": "Bitte selbst nachsehen.",
    "hank.thirst.lead.SETUP": "Das kann ich noch nicht sagen.",
    "hank.thirst.when": "Wann: {forecast}",
    "hank.thirst.amount": "Wie viel: das sagt erst die Prüfung. Voraussichtlich etwa {litres}, höchstens {cap} je "
                          "Gießen.",
    "hank.thirst.amount_cap": "Wie viel: das sagt erst die Prüfung, höchstens {cap} je Gießen.",
    "hank.thirst.amount_at_cap": "Wie viel: das sagt erst die Prüfung. Voraussichtlich etwa {litres}, so viel "
                                 "erlaubt die Phase je Gießen höchstens.",
    "hank.thirst.pump_unknown": "Mit der Pumpe ist die Menge unbekannt, bis der Durchfluss gemessen ist.",
    "hank.thirst.pump_nothing": "Pumpenläufe zählen erst, wenn der Pumpenschalter in der Gießplan-Einstellung bestätigt "
                                "ist.",
    "hank.thirst.why": "Warum: {why}",
    "hank.thirst.check": "Selbst prüfen, ein Tipp genügt. {check}",
    "hank.thirst.tap": "Die Antwort ist ein Tipp auf der Karte Durst-Index unter Automatik.",
    "hank.thirst.next": "Nächster Schritt: {check}",
    "hank.thirst.today": "Gießen: {text}",
    "hank.thirst.light": "Gießen: {text}",
    "hank.thirst.schedule": "Bedarfsprüfung: {text}",
}

EN: dict[str, str] = {
    "hank.thirst.none": "The thirst index does not report right now. The thirst index card under Automation shows what "
                        "the pot needs.",
    "hank.thirst.tank_full": "A full tank is never a reason for me to water: whether to water is the pot's call.",
    "hank.thirst.term": "Thirst index",
    "hank.thirst.gloss": "(how much water the pot is missing; 100 means due)",
    "hank.thirst.lead.WAIT": "No, not yet.",
    "hank.thirst.lead.WAIT_round": "Yes, at the next round.",
    "hank.thirst.lead.ASK": "Check first.",
    "hank.thirst.lead.PROPOSE": "Yes.",
    "hank.thirst.lead.BLOCKED": "No, blocked right now.",
    "hank.thirst.lead.ALERT": "Please look yourself.",
    "hank.thirst.lead.SETUP": "I cannot say that yet.",
    "hank.thirst.when": "When: {forecast}",
    "hank.thirst.amount": "How much: only the check says. Probably about {litres}, at most {cap} per watering.",
    "hank.thirst.amount_cap": "How much: only the check says, at most {cap} per watering.",
    "hank.thirst.amount_at_cap": "How much: only the check says. Probably about {litres}, the most the stage "
                                 "allows per watering.",
    "hank.thirst.pump_unknown": "With the pump the amount is unknown until the flow is measured.",
    "hank.thirst.pump_nothing": "Pump runs count only once the pump switch is confirmed in the watering schedule "
                                "settings.",
    "hank.thirst.why": "Why: {why}",
    "hank.thirst.check": "To check yourself, one tap is enough. {check}",
    "hank.thirst.tap": "The answer is one tap on the thirst index card under Automation.",
    "hank.thirst.next": "Next step: {check}",
    "hank.thirst.today": "Watering: {text}",
    "hank.thirst.light": "Watering: {text}",
    "hank.thirst.schedule": "Need check: {text}",
}


def check() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash."""
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        de, en = i18n.placeholders(DE[key]), i18n.placeholders(EN[key])
        if de != en:
            problems.append(f"placeholders differ: {key}")
        if "lang" in de | en:
            problems.append(f"placeholder lang: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    """Check parity and put the texts into the server catalogs. Safe to call more than once."""
    problems = check()
    if problems:
        raise ValueError("thirst_report: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


register()


# ---------- the question ----------

OWN = "ownplant"  # this run's own plant named by its strain ("die Ice Cream Cake"), see asks()
_PERSON = r"(?:die pflanze|meine pflanze|ownplant|sie|den topf|meinen topf|das|es)"
_SOON = r"(?:heute|heut|jetzt|gerade|schon|wieder|noch|mal|denn|eigentlich|bald|morgen|das nachste mal|als nachstes)"
_DE_VERB = r"(?:giessen|wassern|nachgiessen|nachwassern)"
_QUESTIONS = [re.compile(p) for p in (
    # German, folded (gießen is giessen, wässern wassern, fällig fallig)
    rf"\b(?:muss|soll|sollte|darf|kann|musste) ich(?: (?:{_PERSON}|{_SOON}))* {_DE_VERB}\b",
    rf"\bwann (?:muss|soll|sollte|darf|kann) ich(?: (?:{_PERSON}|{_SOON}))* {_DE_VERB}\b",
    r"\bwann (?:giesse|giess|wassere) ich\b",
    rf"\bwann(?: (?:{_SOON}))* {_DE_VERB}\b",
    r"\bwie viel(?: wasser)? (?:giesse|giess|gebe) ich\b",
    r"\bwie viel(?: wasser)?(?: (?:muss|soll|sollte) ich)?(?: (?:sie|die pflanze|den topf|heute|jetzt))* "
    r"(?:giessen|geben|nachgiessen)\b",
    r"\b(?:braucht|hat) (?:die pflanze|meine pflanze|ownplant|sie|der topf|mein topf) (?:jetzt |schon |wieder |noch )?"
    r"(?:wasser|durst)\b",
    r"\b(?:ist|wann ist) (?:es|der topf)(?: (?:schon|jetzt|wieder))? (?:zeit zum giessen|fallig|dran)\b",
    r"\bgiessen fallig\b",
    r"^(?:und )?(?:giessen|wassern)$",
    r"\b(?:wie hoch|wie steht|wo steht) (?:ist |steht )?der durst ?index\b",
    r"\bdurst ?index (?:jetzt|heute)\b",
    # English
    r"\b(?:do|should|shall|must|can) i(?: (?:need to|have to))?(?: (?:now|today|already|again|still|yet))? water\b",
    r"\bdo i (?:need|have) to water\b",
    r"\bwhen (?:do|should|shall|must|can) i(?: (?:next|again))? water\b",
    r"\bwhen to water\b",
    r"\b(?:does|do) (?:the plant|she|it|the pot|my plant|ownplant) need (?:water|watering)\b",
    r"\bis (?:she|the plant|it|my plant|the pot|ownplant) (?:thirsty|dry)\b",
    r"\bhow much (?:water )?(?:should|do|shall|must) i (?:water|give)\b",
    r"\bhow much should i water\b",
    r"\b(?:time to water|watering due|due for water(?:ing)?)\b",
    r"\bwhat is the thirst index (?:now|today)\b",
    r"\bhow (?:high|much) is the thirst index\b",
)]
_NOT = [re.compile(p) for p in (
    # a how-to stays a growing question (the stage card's watering rows, test_watering_names_todays_stage_only)
    r"\bwie (?:giesse|giess|wassere|soll ich|muss ich|sollte ich) (?:ich )?(?:richtig|am besten|giessen|wassern)?\b",
    r"\bwie giesst man\b",
    r"\bhow (?:do|should|can) i water\b",
    r"\bhow to water\b",
    # how often is the stage card's and the frequency's question, not the pot's of today
    r"\bwie oft\b",
    r"\bhow often\b",
    # what the word means: the glossary answers it; "Erzähl mir mehr über ..." is the wiki's button (wikicards.py)
    r"^(?:was ist|was bedeutet|was heisst|erklar|what is|what does|what s|explain)\b(?!.*\b(?:jetzt|heute|now|today)\b)",
    r"\b(?:erzahl|erzahle|erklar|erklare)(?: mir)?(?: mehr)? (?:uber|zu|zum|zur)\b",
    r"\b(?:tell me|more) (?:more )?about\b",
    # how the index is computed is the glossary's and the card's explanation (review N7)
    r"\b(?:wie wird|wie rechnet|berechnet|berechnen|how is|how does|calculated|computed)\b",
    # the tank's own size is a question about the tank (review N7)
    r"\b(?:fasst|fassen|halt der tank|hold|holds|capacity|kapazitat)\b",
    # watering too much is the overwatering card's question
    r"\b(?:zu viel|zu oft|too much|too often)\b",
)]
_TANK = re.compile(r"\b(?:tank|wassertank|reservoir)\b.*\b(?:voll|aufgefullt|full|refilled)\b|"
                   r"\b(?:voller|vollem|full) (?:tank|wassertank|reservoir)\b")
_AMOUNT = re.compile(r"\b(?:wie viel|how much)\b")
_WHEN = re.compile(r"\b(?:wann|when)\b")


def _own_plant(name: Any, state: LiveState | None) -> bool:
    """Whether a plant named in the question is this run's own plant (its strain on the plant card)."""
    if state is None:
        return False
    strain = (state.plant_view.get("plant") or {}).get("strain") if isinstance(state.plant_view, Mapping) else None
    entity = str(getattr(name, "entity", name) or "").replace("_", " ")
    return bool(strain) and bool(entity) and entity in fold(str(strain))


# The question's key words with one typo (a letter swapped, left out, doubled or replaced, "giesen" for "gießen"),
# compared before folding, so ß stays one letter: the variants of "Muss ich gießen?" stay this question.
_TYPO_WORDS = ("gieße", "gießen", "wässern", "should")


def _typo_distance(a: str, b: str) -> int:
    """Edits from a to b (insert, delete, replace, swap two neighbours), the optimal string alignment distance."""
    rows = [list(range(len(b) + 1))]
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            cost = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cost = min(cost, rows[-2][j - 2] + 1)
            row.append(cost)
        rows.append(row)
    return rows[-1][-1]


def _mend(question: str) -> str:
    """The question with a key word's single typo mended: "Muss ich gieen?" asks "Muss ich gießen?"."""
    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        low = word.lower()
        if len(low) < 5 or low in _TYPO_WORDS:
            return word
        for key in _TYPO_WORDS:
            if abs(len(key) - len(low)) <= 1 and _typo_distance(low, key) == 1:
                return key
        return word
    return re.sub(r"\w+", fix, question or "")


def _folded(question: str) -> str:
    folded = re.sub(r"[^\w ]+", " ", fold(_mend(question)))
    return re.sub(r"\s+", " ", folded).strip()


def asks(question: str, names: Iterable[Any] = (), state: LiveState | None = None) -> bool:
    """A watering question about this plant: "Muss ich gießen?", "Wann gieße ich?", "Wie viel gieße ich?", "Do I need
    to water?". Not a how-to ("Wie gieße ich richtig?"), not how often, not what a word means or how it is computed,
    not the tank's size, not about watering too much, and never about another plant ("Wie oft muss ich meine Gurke
    gießen?" stays the gap it is). One typo in a key word is mended."""
    folded = _folded(question)
    if not folded or any(p.search(folded) for p in _NOT):
        return False
    for name in names:
        if getattr(name, "kind", None) != "plant":
            continue
        if not _own_plant(name, state):
            return False
        # this run's own plant by its strain ("Muss ich die Ice Cream Cake gießen?") is the plant of the question
        words = str(getattr(name, "entity", name) or "").replace("_", " ").strip()
        if words:
            folded = re.sub(rf"\b(?:(?:die|meine|the|my) )?{re.escape(words)}(?: auto(?:matic)?)?\b", OWN, folded)
    return any(p.search(folded) for p in _QUESTIONS)


def tank_question(question: str) -> bool:
    """The question names a full (or just refilled) tank."""
    return bool(_TANK.search(_folded(question)))


# ---------- the brief ----------

def brief(live: LiveState | Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The Durst-Index's brief from the snapshot (server.HankBox), or None when the server put none there."""
    state = collect(live)
    found = state.snapshot.get("thirst") if isinstance(state.snapshot, Mapping) else None
    return found if isinstance(found, Mapping) else None


def _lang(lang: str) -> str:
    return lang if lang in ("de", "en") else "de"


def _said(value: Any, lang: str) -> str:
    if isinstance(value, Mapping):
        return str(value.get(lang) or value.get("de") or "")
    return str(value or "")


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text.endswith((".", "!", "?", ":")) else text + "."


def _glossed(text: str, lang: str) -> str:
    """The first "Durst-Index" of an answer with its plain meaning right after it, behind its value when one follows
    ("Durst-Index 92 % (wie viel Wasser dem Topf fehlt; 100 heißt fällig)"): the word is explained where it first
    appears (review S5)."""
    term = re.escape(t("hank.thirst.term", lang))
    found = re.search(rf"{term}(?: [\d.,]+ ?%)?", text, flags=re.IGNORECASE)
    if found is None:
        return text
    return f"{text[:found.end()]} {t('hank.thirst.gloss', lang)}{text[found.end():]}"


def _litres(value: Any, lang: str) -> str:
    digits = 2 if abs(float(value)) < 1 else 1
    return fmt_qty(round(float(value), digits), "l", lang)


# ---------- the answer of the route "thirst" ----------

def answer_text(live: LiveState | Mapping[str, Any], lang: str, question: str = "") -> str:
    """The plain answer: (the full-tank sentence), yes or no with the decision, the forecast (first for "wann"), the
    amount (for "wie viel"), why, and the one-tap check."""
    lang = _lang(lang)
    found = brief(live)
    folded = _folded(question)
    lines: list[str] = []
    if tank_question(question):
        lines.append(t("hank.thirst.tank_full", lang))
    if found is None:
        lines.append(t("hank.thirst.none", lang))
        return "\n".join(lines)
    kind = str(found.get("kind") or "WAIT")
    lead = "SETUP" if not found.get("configured") or found.get("ti") is None else kind
    if lead == "WAIT" and found.get("reason") == "round":  # measured due, every block clear: at the round
        lead = "WAIT_round"
    text = _said(found.get("text"), lang)
    lines.append(f"{t(f'hank.thirst.lead.{lead}', lang)} {text}".strip())
    forecast = _said(found.get("forecast"), lang)
    amount: list[str] = []
    if _AMOUNT.search(folded) and kind != "PROPOSE" and found.get("configured"):
        cap = found.get("cap_l")
        if found.get("planned_l") and cap and float(found["planned_l"]) >= float(cap) - 0.0005:
            amount.append(t("hank.thirst.amount_at_cap", lang, litres=_litres(found["planned_l"], lang)))
        elif found.get("planned_l") and cap:
            amount.append(t("hank.thirst.amount", lang, litres=_litres(found["planned_l"], lang),
                            cap=_litres(cap, lang)))
        elif cap:
            amount.append(t("hank.thirst.amount_cap", lang, cap=_litres(cap, lang)))
    if _AMOUNT.search(folded) and found.get("pump_amount") in ("unknown", "nothing"):
        amount.append(t(f"hank.thirst.pump_{found['pump_amount']}", lang))
    when = [t("hank.thirst.when", lang, forecast=forecast)] if forecast else []
    lines += [*when, *amount] if _WHEN.search(folded) else [*amount, *when]
    schedule = _said(found.get("schedule_note"), lang)
    if schedule:
        lines.append(t("hank.thirst.schedule", lang, text=schedule))
    why = found.get("why")
    reasons = [str(w) for w in (why.get(lang) or why.get("de") or [])] if isinstance(why, Mapping) else []
    if reasons:
        lines.append(t("hank.thirst.why", lang, why=" ".join(_sentence(r) for r in reasons)))
    said = _said(found.get("check"), lang)
    if found.get("reason") == "no_pot" and said:
        lines.append(t("hank.thirst.next", lang, check=said))
    elif kind == "ASK" and found.get("configured"):  # the decision already asks the check: only where to tap
        lines.append(t("hank.thirst.tap", lang))
    elif said:
        lines.append(t("hank.thirst.check", lang, check=said))
    text_out = "\n".join(lines)
    return _glossed(text_out, lang)


def answer(live: LiveState | Mapping[str, Any], lang: str, *, answer_id: str, question: str = "") -> Answer:
    """The route "thirst" answered from code (core._respond), no model, no card: the check is a tap on the page."""
    return Answer(id=answer_id, route=ROUTE, lang=_lang(lang), text=answer_text(live, lang, question))


# ---------- the today list and the traffic light ----------

def _todo(found: Mapping[str, Any] | None) -> bool:
    if found is None:
        return False
    return found.get("kind") in TODO_KINDS or not found.get("configured")


def today_lines(live: LiveState | Mapping[str, Any], lang: str) -> list[str]:
    """The watering line of the today list (today.py), only when something is to do: set up the pot, check, water, a
    block while it is due, an alert."""
    lang = _lang(lang)
    found = brief(live)
    if not _todo(found):
        return []
    assert found is not None
    return [t("hank.thirst.today", lang, text=_said(found.get("text"), lang))]


def light(live: LiveState | Mapping[str, Any], assessment: Any) -> Any:
    """For nudges.assess: yellow while the watering asks, is due, is blocked or alerts, as an Assessment of nudges
    (handed in, so this module imports nothing of it); None otherwise, also for a pot nobody set up yet (the today
    list says that). The problem keys stay empty: the fix answer has no watering card."""
    found = brief(live)
    if found is None or not found.get("configured") or found.get("kind") not in TODO_KINDS:
        return None
    texts = {lang: t("hank.thirst.light", lang, text=_said(found.get("text"), lang)) for lang in ("de", "en")}
    return assessment("yellow", f"thirst:{str(found.get('kind')).lower()}:{found.get('reason')}", texts)


def schedule_lines(live: LiveState | Mapping[str, Any], lang: str) -> list[str]:
    """For the pump history's next-slot answer (pump_report.py): what the need check says about the next slot."""
    found = brief(live)
    said = _said((found or {}).get("schedule_note"), _lang(lang)) if found else ""
    return [t("hank.thirst.schedule", _lang(lang), text=said)] if said else []


__all__ = ["DE", "EN", "ROUTE", "answer", "answer_text", "asks", "brief", "check", "light", "register",
           "schedule_lines", "tank_question", "today_lines"]
