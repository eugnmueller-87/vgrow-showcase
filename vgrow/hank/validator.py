"""The validator: code that checks the model's sentences and strikes what has no backing (spec 5, addendum N1).

validate(text, ctx, lang) -> Validation. Per sentence, in this order:
1. Switch claims ("ich habe ausgeschaltet", "is now at") are replaced by hank.reply.no_self, once (reason claim).
   The flat old rule ("Ich schalte nichts selbst", "I never switch anything myself", also "Ich schalte nichts ohne
   dich" without the chat) drops the sentence in every answer shape (reason flat_rule): under "Hank regelt" the
   autopilot switches under Hank's name, so only the chat-scoped rule is true ("Im Chat schalte ich nichts ohne
   dich"). Only a generic object counts ("nichts", "anything"), a concrete sentence about one device stays. It takes
   no filler and does not use up the claim's one filler.
2. Forbidden content from chat_policy.json never_say and model_never_say drops the sentence (reason compliance,
   logged): links, effects, medicine, law, passing on, the automation off, the full chart dose (review 3, H4).
3. Markers [F3], [F1, F2], [F1][F2], [S4]: unknown ones are removed; a sentence without a valid marker drops
   (no_marker), except a filler that is word for word one of hank.filler.* (addendum N1).
4. Numbers: every number of the sentence must stand in a fact or state group whose marker is in the same sentence
   (addendum N1), with its unit when it has one ("79 °C" fails against "79 %": reason unit, else number). Ranges count
   both ends, ratios like 18/6 count as a token and as numbers, number words count everywhere (zwei to zwölf,
   zwanzig, hundert, halb, doppelt; "ein/eine" only before a unit). Numbers from the question are not allowed as
   such (spec 5.3); only the slots (week, litres, step, and the percent of an action wish) are.
5. Verdict words: "im Ziel", "passt", "okay" next to a metric the code judged off target, or "zu hoch" next to one
   it judged on target, drop the sentence (reason verdict). With stale readings any verdict word next to a metric
   drops it (live-05: no climate verdict from old readings).
6. Language: a model text in the other language falls back to the draft as a whole (reason lang).
7. Mandatory facts (Context.mandatory: the dose factor, the request to confirm a draft card, the law) are said by
   the code word for word after the model's sentences; a model sentence that cites nothing but them drops (reason
   mandatory), and a kept sentence that cites a draft card or a partly backed value brings that note along
   (review 3, H3).
The kept sentences get numbered references instead of markers; verdict sentences go in front, the ledger message and
the proposal sentence after (compose.assemble). Nothing left: the draft. The draft empty too: hank.reply.dont_know.

Then the version as a whole (addendum N0: "besteht sie nicht, bleibt der Entwurf"; acceptance 23.10.2026, "Wie warm
ist es?" came back as "Das steht so in meiner Kartei [1]" marked "überarbeitet", the temperature gone):
8. Filler: no kept model sentence says anything of what it cites (no number, fewer than two shared words beyond
   the stage label, the question and the draft's bubble): the draft (filler).
9. Core facts (compose.accept): the draft's direct answer (number with unit, verdict word), its proposal line and its
   mandatory sentences must still be there, the answer must not be shorter than the direct answer and must bring no
   number the draft does not have. Otherwise the draft, with the reason in Validation.rejected (reason core). The
   draft's answer sentence and its verdict's advice stay in the code's words in front of the model's sentences
   (compose.Composed.keep).
10. Unchanged: a bubble equal to the draft's keeps the draft (rejected "unchanged"), so "überarbeitet" never shows
   on a text that did not change.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import Any

from vgrow.i18n import t

from . import claims, compose, text
from . import texts as reply_texts
from .context import Context, marked, vocab
from .texts_live import FILLER_KEYS
from .types import Num, SourceRef

REASONS = ("number", "unit", "no_marker", "verdict", "claim", "flat_rule", "compliance", "lang", "empty", "mandatory",
           "filler", "core")
# Number words the parser does not read as numbers: compounds and fractions (review 3, H4: "vierhundert Gramm",
# "um ein Drittel"). A sentence with one of them must find the same word in a fact it cites.
_NUMBER_STEMS = re.compile(r"(?<![^\W_])(?:\w*(?:hundert|tausend|hundred|thousand)\w*|"
                           r"(?:drei|vier|funf|sech|sieb|acht|neun)zehn|(?:sech|sieb|acht|neun)zig|"
                           r"(?:ein|zwei|drei|vier|funf|sechs|sieben|acht|neun)und\w*zig|"
                           r"(?:thir|four|fif|six|seven|eigh|nine)teen|(?:six|seven|eigh|nine)ty|"
                           r"drittel\w*|viertel\w*|funftel\w*|sechstel\w*|achtel\w*|zehntel\w*|halfte\w*|"
                           r"thirds?|quarters?|fifths?|eighths?|tenths?|dozens?|dutzend\w*)(?![^\W_])")
DONT_KNOW_KEY = "hank.reply.dont_know"
FILLER_SHARED = 2  # words a model sentence without a number must share with what it cites, beyond the free ones
UNCHANGED = "unchanged"  # Validation.rejected when the polished bubble equals the draft's
DROP_LIMIT = 0.30  # spec 5.9: more than 30 % of model sentences dropped over 20 answers flags the system text
DROP_WINDOW = 20

_MARKER = re.compile(r"\[\s*([FS]\d{1,3}(?:\s*,\s*[FS]?\d{1,3})*)\s*\]")
_MARKER_LOOSE = re.compile(r"\[[^\]]{0,24}\]")
_SPACES = re.compile(r"\s+")

_POSITIVE = {
    "de": ("im ziel", "passt", "okay", "ok", "in ordnung", "gut", "optimal", "ideal", "perfekt", "im rahmen",
           "im grunen bereich", "im soll", "kein problem", "unproblematisch", "unbedenklich", "alles gut",
           "nicht schlimm", "passend", "richtig so"),
    "en": ("on target", "fine", "within range", "in range", "okay", "ok", "good", "optimal", "ideal", "perfect",
           "all right", "alright", "no problem", "not a problem", "nothing to worry", "on track", "acceptable",
           "no issue", "not an issue"),
}
_NEGATIVE = {
    "de": ("zu hoch", "zu niedrig", "zu feucht", "zu trocken", "zu warm", "zu kalt", "zu kuhl", "zu hell",
           "zu dunkel", "zu viel", "zu wenig", "uber dem ziel", "unter dem ziel", "ausserhalb"),
    "en": ("too high", "too low", "too humid", "too dry", "too warm", "too hot", "too cold", "too cool", "too bright",
           "too dark", "too much", "too little", "above target", "below target", "out of range", "outside"),
}

# Sentence ends that are not ends (spec 5.1).
_ABBREV = frozenset("""z. b. z.b. ca. bzw. d. h. d.h. u. a. u.a. o. usw. etc. evtl. ggf. inkl. nr. vgl. bspw. sog. abs.
e.g. i.e. approx. vs. dr. st. min. max. std.""".split())
_ORDINAL_LEAD = frozenset("der die das den dem des am im zum zur ab bis vom in seit ihrer seiner deiner meiner".split())
_DATE = re.compile(r"^\(?\d{1,2}\.\d{1,2}\.$")
_ORDINAL = re.compile(r"^\(?\d{1,3}\.$")

# The flat old rule (until "Hank regelt", 23.10.2026): Hank, "ich"/"I" or the old hint's "er"/"he", never switches
# anything, without a scope. Matched on folded text. Only the chat or the autopilot is a scope: a sentence that names
# one of them is the new rule or tells the two apart, and stays. "ohne dich", "until you tap" are no scope: under
# "Hank regelt" the autopilot switches without a tap, so "Ich schalte nichts ohne dich" is the flat rule in disguise.
# The negation must be generic (nichts, nie, keine Geraete, anything, the devices): "Ich kann die Abluft nicht
# ausschalten" or "I can't switch the exhaust off, the port is locked" are concrete and true, and stay.
_FLAT_SCOPE = re.compile(r"\b(?:im\s+chat|in\s+(?:the\s+|this\s+)?chat|autopilot\w*|automatik\w*|automation\w*|"
                         r"hank\s+regelt(?!\s+(?:nichts|nie|niemals|kein))|"
                         r"hank\s+controls(?!\s+(?:nothing|no\b)))\b")
_FLAT_VERB_DE = r"(?:schalte|schaltet|schalt|regle|regele|regelt|steuere|steure|steuert)"
_FLAT_FILL_DE = (r"(?:\s+(?:selbst|selber|aber|hier|ja|doch|auch|eigentlich|ubrigens|grundsatzlich|ansonsten|"
                 r"personlich))*")
# right after the verb: "nichts", "nie", "niemals", "keine Geraete", "nicht selbst"; no bare "nicht"
_FLAT_OBJ_DE = r"(?:nichts|nie|niemals|keine?[nmrs]?\s+gerat\w*|nicht\s+(?:selbst|selber))"
# after "kann/darf/werde": the generic part before the infinitive
_FLAT_MODAL_DE = (r"(?:nichts|(?:nie|niemals|nicht)\s+(?:etwas|irgendetwas|selbst|selber)|keine?[nmrs]?\s+gerat\w*|"
                  r"nie\s+irgendwas)")
_FLAT_NEG_EN = (r"(?:never|don['’]?t|do\s+not|won['’]?t|will\s+not|cannot|can['’]?t|can\s+not|doesn['’]?t|"
                r"does\s+not)")
_FLAT_VERB_EN = r"(?:switch|switches|turn|turns|toggle|toggles|control|controls|regulate|regulates|operate|operates)"
_FLAT_FILL_EN = r"(?:\s+(?:myself|himself|personally|actually|really|ever|directly|just))*"
# the object: generic ("anything", "the devices") or none at all ("I never switch myself.")
_FLAT_OBJ_EN = (r"(?:\s+(?:on|off))?(?:\s+(?:anything|any\s+devices?|any\s+of\s+(?:the\s+)?devices|the\s+devices|"
                r"devices|things|a\s+thing)\b|" + _FLAT_FILL_EN + r"\s*(?:[.!?,;:\[]|$))")
_FLAT_RULE = [
    # "Ich schalte nichts selbst", "Ich selbst schalte nie etwas", "Hank schaltet nichts", "Ich regle nichts"
    re.compile(rf"\b(?:ich|hank){_FLAT_FILL_DE}\s+{_FLAT_VERB_DE}{_FLAT_FILL_DE}\s+{_FLAT_OBJ_DE}\b"),
    # "Selbst schalte ich nichts", "Nie schalte ich"
    re.compile(rf"\b(?:nichts|nie|niemals|selbst|selber)\s+{_FLAT_VERB_DE}\s+ich\b"),
    # "Ich kann nichts selbst schalten", "Hank darf nie etwas einschalten"
    re.compile(rf"\b(?:ich|hank)\s+(?:kann|darf|werde|wird|will)\b(?:(?!\bdu\b)[^.!?,]){{0,30}}?\b{_FLAT_MODAL_DE}\b"
               rf"(?:(?!\bdu\b)[^.!?,]){{0,30}}?\b(?:\w*schalten|regeln|steuern)\b"),
    # the old hint: "Er schaltet nichts selbst"; only with "nichts"/"nie etwas", so "Er schaltet nie ab" (a device)
    # stays
    re.compile(rf"\ber{_FLAT_FILL_DE}\s+schaltet{_FLAT_FILL_DE}\s+(?:nichts|(?:nie|niemals)\s+etwas)\b"),
    # "I never switch anything myself", "I don't switch anything", "Hank never switches anything himself"
    re.compile(rf"\b(?:i|hank){_FLAT_FILL_EN}\s+{_FLAT_NEG_EN}{_FLAT_FILL_EN}\s+{_FLAT_VERB_EN}{_FLAT_FILL_EN}"
               rf"{_FLAT_OBJ_EN}"),
    # the old hint: "He never switches anything himself"; only with "anything", so "he" for a device stays
    re.compile(rf"\bhe{_FLAT_FILL_EN}\s+{_FLAT_NEG_EN}{_FLAT_FILL_EN}\s+switch(?:es)?{_FLAT_FILL_EN}\s+anything\b"),
    # "I switch nothing myself", "Hank switches nothing", "He switches nothing"
    re.compile(rf"\b(?:i|hank|he)\s+{_FLAT_VERB_EN}\s+nothing\b"),
]


def flat_rule(sentence: str) -> bool:
    """True when the sentence states flatly, in German or English, that Hank never switches anything (the old rule
    "Ich schalte nichts selbst"). Both languages are checked, because a model can slip into the other one. A switch
    claim is checked before this and keeps its own behaviour."""
    folded = text.fold(sentence)
    if _FLAT_SCOPE.search(folded):
        return False
    return any(p.search(folded) for p in _FLAT_RULE)


@dataclass
class Dropped:
    sentence: str
    reason: str
    detail: str = ""


@dataclass
class Validation:
    sentences: list[str]  # the final answer's sentences (with numbered references)
    dropped: list[Dropped]
    replaced: bool  # a switch claim was replaced by hank.reply.no_self
    sources: list[SourceRef]
    text: str = ""
    used_draft: bool = False
    model_sentences: int = 0
    kept: int = 0
    composed: compose.Composed | None = None
    more: str = ""  # the folded part ("Mehr aus der Kartei")
    more_sources: list[SourceRef] | None = None
    rejected: str | None = None  # why a version that passed sentence by sentence still did not replace the draft

    @property
    def drop_rate(self) -> float:
        """Sentences dropped one by one (spec 5.9); a whole version kept back (filler, core) is no sentence drop."""
        return 0.0 if not self.model_sentences else len([d for d in self.dropped if d.reason not in (
            "claim", "filler", "core")]) / self.model_sentences

    def report(self) -> dict[str, Any]:
        """For turns.jsonl and the eval table: counts, reasons, no sentence text of the question."""
        reasons: dict[str, int] = {}
        for d in self.dropped:
            reasons[d.reason] = reasons.get(d.reason, 0) + 1
        return {"model_sentences": self.model_sentences, "kept": self.kept, "used_draft": self.used_draft,
                "replaced": self.replaced, "dropped": reasons, "drop_rate": round(self.drop_rate, 3),
                "rejected": self.rejected}


class DropWindow:
    """The sliding drop rate over the last answers (spec 5.9): flag() is True above 30 % over 20 answers."""

    def __init__(self, size: int = DROP_WINDOW, limit: float = DROP_LIMIT) -> None:
        self.size, self.limit = size, limit
        self._items: deque[tuple[int, int]] = deque(maxlen=size)

    def add(self, validation: Validation) -> None:
        if validation.model_sentences:
            dropped = len([d for d in validation.dropped if d.reason not in ("claim", "filler", "core")])
            self._items.append((dropped, validation.model_sentences))

    def rate(self) -> float:
        total = sum(n for _, n in self._items)
        return 0.0 if not total else sum(d for d, _ in self._items) / total

    def flag(self) -> bool:
        return len(self._items) >= self.size and self.rate() > self.limit


# ---------- sentences ----------

def split_sentences(value: str) -> list[str]:
    """Sentences of a text. A dot after an abbreviation (z. B., ca., bzw.), inside a number (1.4), after a date (22.10.)
    or after a German ordinal ("der 4. Blütewoche") is no end, nor is one followed by a lower-case word. A marker
    right after an end ("... hoch. [F2]") belongs to the sentence before."""
    value = _SPACES.sub(" ", value or "").strip()
    out: list[str] = []
    start = 0
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch in ".!?" and (i + 1 == n or value[i + 1] == " "):
            j = i + 1
            while j < n and value[j] == " ":
                j += 1
            marker = _MARKER_LOOSE.match(value, j)
            if marker:  # "... hoch. [F2]" : the marker belongs here; a dot after it too
                j = marker.end()
                while j < n and value[j] in ".!? ":
                    j += 1
                out.append(value[start:j].strip())
                start = i = j
                continue
            if ch == "." and not _is_end(value, start, i, j):
                i += 1
                continue
            out.append(value[start:i + 1].strip())
            start = i = j
            continue
        i += 1
    rest = value[start:].strip()
    if rest:
        out.append(rest)
    return [s for s in out if s]


def _is_end(value: str, start: int, dot: int, nxt: int) -> bool:
    before = value[start:dot + 1].split(" ")
    token = before[-1].lower() if before else ""
    if token in _ABBREV:
        return False
    if _DATE.match(token):
        return False
    if _ORDINAL.match(token) and len(before) >= 2 and before[-2].lower().strip("(") in _ORDINAL_LEAD:
        return False
    if nxt < len(value) and value[nxt].islower():
        return False
    return True


# ---------- checks ----------

def _phrase_in(folded: str, phrase: str) -> bool:
    return re.search(rf"(?<![^\W_]){re.escape(phrase)}(?![^\W_])", folded) is not None


def _metric_forms() -> dict[str, list[tuple[str, ...]]]:
    """Folded search tokens of every name of a metric, by the metric key (vocab maps_to)."""
    out: dict[str, list[tuple[str, ...]]] = {}
    for entry, forms in vocab():
        if entry.get("kind") == "metric" and entry.get("maps_to"):
            out.setdefault(str(entry["maps_to"]), []).extend(form for _, form in forms)
    return out


def _names_metric(sentence: str, metric: str, label: str, forms: dict[str, list[tuple[str, ...]]]) -> bool:
    toks = {"de": text.tokens(sentence, lang="de"), "en": text.tokens(sentence, lang="en")}
    candidates = list(forms.get(metric, []))
    for lang in ("de", "en"):
        candidates.append(tuple(text.tokens(label, lang=lang)))
    for form in candidates:
        if not form:
            continue
        for seq in toks.values():
            if any(tuple(seq[i:i + len(form)]) == form for i in range(len(seq) - len(form) + 1)):
                return True
    return False


def _verdict_conflict(sentence: str, ctx: Context, lang: str) -> str | None:
    folded = text.fold(sentence)
    pos = [w for code in (lang, "en" if lang == "de" else "de") for w in _POSITIVE[code] if _phrase_in(folded, text.fold(w))]
    neg = [w for code in (lang, "en" if lang == "de" else "de") for w in _NEGATIVE[code] if _phrase_in(folded, text.fold(w))]
    if not pos and not neg:
        return None
    forms = _metric_forms()
    if ctx.verdicts.stale:
        for metric in forms:
            if _names_metric(sentence, metric, "", forms):
                return f"stale: {(pos + neg)[0]}"
        return None
    for v in ctx.verdicts.verdicts:
        if not _names_metric(sentence, v.metric, v.label, forms):
            continue
        if v.status in ("low", "high") and pos:
            return f"{v.metric} is {v.status}: {pos[0]}"
        if v.status == "ok" and neg:
            return f"{v.metric} is ok: {neg[0]}"
    return None


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def _number_problem(sentence: str, allowed: list[Num], ratio_texts: list[str], lang: str) -> tuple[str, str] | None:
    for num in text.parse_numbers(sentence, lang):
        values = [a for a in allowed if _same(a.value, num.value)]
        if not values:
            return "number", f"{num.value:g}{num.unit}"
        if num.unit and not any(a.unit == num.unit for a in values):
            return "unit", f"{num.value:g} {num.unit}"
    allowed_ratios = {r for value in ratio_texts for r in text.ratios(value)}
    for ratio in text.ratios(sentence):
        if ratio not in allowed_ratios:
            return "number", ratio
    return None


def _number_word_problem(sentence: str, fact_texts: list[str]) -> str | None:
    """A number word the parser does not count (vierhundert, ein Drittel) that no cited fact has as well."""
    folded = text.fold(sentence)
    facts = " ".join(text.fold(value) for value in fact_texts)
    for match in _NUMBER_STEMS.finditer(folded):
        word = match.group(0)
        if text.is_number_word(word):
            continue  # the parser reads it (addendum N1), the number check above covers it
        if not re.search(rf"(?<![^\W_]){re.escape(word)}(?![^\W_])", facts):
            return word
    return None


def _fillers(lang: str) -> set[str]:
    return {_SPACES.sub(" ", t(key, lang)).strip() for key in FILLER_KEYS}


def _no_self(lang: str) -> str:
    return t(claims.NO_SELF_KEY, lang)


# ---------- the validator ----------

def validate(model_text: str, ctx: Context, lang: str | None = None) -> Validation:
    lang = lang or ctx.lang
    raw = (model_text or "").strip()
    draft = compose.build(ctx)
    if not raw:
        return _fallback(ctx, draft, [Dropped("", "empty")], 0, False)
    bare = _MARKER_LOOSE.sub(" ", raw)
    if text.detect_lang(bare, default=lang) != lang:
        return _fallback(ctx, draft, [Dropped(raw, "lang", text.detect_lang(bare, default=lang))],
                         len(split_sentences(raw)), False)
    sentences = split_sentences(raw)
    dropped: list[Dropped] = []
    kept: list[compose.Piece] = []
    replaced = False
    fillers = _fillers(lang)
    cited: list[str] = []
    grounded = 0
    known = _known_words(ctx, draft, lang)
    for sentence in sentences:
        if claims.switch_claim(sentence, lang):
            dropped.append(Dropped(sentence, "claim"))
            if not replaced:
                replaced = True
                kept.append(compose.Piece(_no_self(lang), (), "filler"))
            continue
        if flat_rule(sentence):  # the old flat rule: wrong under "Hank regelt", in a mixed answer too
            dropped.append(Dropped(sentence, "flat_rule"))
            continue
        pattern = claims.forbidden(sentence, lang) or claims.model_forbidden(sentence, lang)
        if pattern:
            dropped.append(Dropped(sentence, "compliance", pattern))
            continue
        markers: list[str] = []
        for match in _MARKER.finditer(sentence):
            for item in match.group(1).split(","):
                item = item.strip()
                if item and item[0].isdigit():
                    item = (markers[-1][0] if markers else "F") + item
                markers.append(item)
        body = _MARKER_LOOSE.sub("", sentence)
        body = _SPACES.sub(" ", re.sub(r"\s+([.,;:!?])", r"\1", body)).strip()
        valid = [mk for mk in dict.fromkeys(markers) if ctx.known(mk)]
        if valid and all(mk in ctx.mandatory for mk in valid):
            dropped.append(Dropped(sentence, "mandatory", ",".join(valid)))  # the code says it word for word
            continue
        if not valid:
            if body in fillers:
                kept.append(compose.Piece(body, (), "filler"))
            else:
                dropped.append(Dropped(sentence, "no_marker"))
            continue
        allowed: list[Num] = list(ctx.slot_numbers)
        ratio_texts: list[str] = []
        refs: list[SourceRef] = []
        for mk in valid:
            found = marked(ctx, mk)
            if found is None:
                continue
            fact_text, numbers, sources = found
            allowed.extend(numbers)
            ratio_texts.append(fact_text)
            refs.extend(sources)
        problem = _number_problem(body, allowed, ratio_texts, lang)
        if problem:
            dropped.append(Dropped(sentence, problem[0], problem[1]))
            continue
        word = _number_word_problem(body, ratio_texts)
        if word:
            dropped.append(Dropped(sentence, "number", word))
            continue
        conflict = _verdict_conflict(body, ctx, lang)
        if conflict:
            dropped.append(Dropped(sentence, "verdict", conflict))
            continue
        kept.append(compose.Piece(body, tuple(dict.fromkeys(refs)), "model"))
        cited.extend(mk for mk in valid if mk.startswith("F"))
        own_rule = (reply_texts.EN if lang == "en" else reply_texts.DE)[claims.NO_SELF_KEY]  # registered or not
        if _says_something(body, [*ratio_texts, *ctx.verdicts.sentences(lang), own_rule], lang, known):
            grounded += 1
    model_kept = [p for p in kept if p.kind == "model"]
    if not model_kept:
        result = _fallback(ctx, draft, dropped, len(sentences), replaced)
        return result
    if not grounded:  # "Das steht so in meiner Kartei [F1]": a marker, nothing of what it cites
        return _fallback(ctx, draft, [*dropped, Dropped(raw, "filler")], len(sentences), replaced,
                         rejected="filler: the model's sentences say nothing of what they cite")
    kept.extend(_code_after_model(ctx, cited, lang))
    composed = compose.assemble(ctx, kept, draft=False, more=draft.middle, keep=draft.keep)
    reason = compose.accept(draft.core, draft, composed, lang, shown=compose.sheet_numbers(ctx))
    if reason:  # a core fact lost, or a number the draft does not have: the draft stays (addendum N0)
        return _fallback(ctx, draft, [*dropped, Dropped("", "core", reason)], len(sentences), replaced,
                         rejected=reason)
    if composed.text == draft.text:  # only "more" changed: no "überarbeitet" on a bubble that did not (finding 4)
        return _fallback(ctx, draft, dropped, len(sentences), replaced, rejected=UNCHANGED)
    return Validation(sentences=composed.sentences, dropped=dropped, replaced=replaced, sources=composed.sources,
                      text=composed.text, used_draft=False, model_sentences=len(sentences), kept=len(model_kept),
                      composed=composed, more=composed.more, more_sources=list(composed.more_sources))


def _known_words(ctx: Context, draft: compose.Composed, lang: str) -> set[str]:
    """Words a model sentence gets for free: the stage label (it is in every fact), the question and the draft's
    bubble. Sharing only these with a fact says nothing new ("Meine Kartei sagt dazu: Frühe Blüte [F1].")."""
    label = (ctx.live.stage or {}).get("label") if ctx.live is not None else None
    labels = list(label.values()) if isinstance(label, dict) else [label]
    values = [str(v) for v in labels if v] + [ctx.question or "", draft.text or ""]
    return {tok for value in values for tok in text.tokens(value, lang=lang)}


def _says_something(body: str, cited: list[str], lang: str, known: set[str] | frozenset[str] = frozenset()) -> bool:
    """A model sentence with content: a number (checked above against what it cites) or at least FILLER_SHARED words
    it shares with the facts or state groups it cites, the code's verdicts or Hank's own rule (in the chat nothing
    without you), not counting the words it gets for free (known: the stage label, the question, the draft's bubble). A marker
    after an empty phrase ("Das steht so in meiner Kartei [F1].", "Meine Kartei sagt dazu: Frühe Blüte [F1].") is a
    filler (finding 3)."""
    if text.parse_numbers(body, lang):
        return True
    words = set(text.tokens(body, lang=lang)) - set(known)
    return any(len(words & set(text.tokens(value, lang=lang))) >= FILLER_SHARED for value in cited)


def _code_after_model(ctx: Context, cited: list[str], lang: str) -> list[compose.Piece]:
    """What the code says after the model's sentences (review 3, H3): every mandatory fact word for word, and the
    draft and partial notes of the facts the model cited, once each, unless a mandatory fact already says them."""
    out: list[compose.Piece] = []
    said: list[str] = []
    for fid in ctx.mandatory:
        fact = ctx.facts.get(fid)
        if fact is not None and fact.text.strip():
            out.append(compose.Piece(fact.text, fact.sources, "fact", "mandatory"))
            said.append(fact.text)
    notes = [t(key, lang) for key in ("hank.draft.card", "hank.draft.note", "hank.verdict.partial")]
    for fid in dict.fromkeys(cited):
        fact = ctx.facts.get(fid)
        if fact is None or fid in ctx.mandatory:
            continue
        for note in notes:
            if note and note in fact.text and not any(note in s for s in said):
                out.append(compose.Piece(note, (), "note"))
                said.append(note)
    return out


def _fallback(ctx: Context, draft: compose.Composed, dropped: list[Dropped], count: int,
              replaced: bool, *, rejected: str | None = None) -> Validation:
    """Nothing usable from the model: the draft; the draft empty too: "Weiß ich nicht"."""
    if draft.text.strip():
        return Validation(sentences=draft.sentences, dropped=dropped, replaced=replaced, sources=draft.sources,
                          text=draft.text, used_draft=True, model_sentences=count, kept=0, composed=draft,
                          more=draft.more, more_sources=list(draft.more_sources), rejected=rejected)
    dont_know = t(DONT_KNOW_KEY, ctx.lang)
    return Validation(sentences=[dont_know], dropped=dropped, replaced=replaced, sources=[], text=dont_know,
                      used_draft=True, model_sentences=count, kept=0, composed=draft, rejected=rejected)
