"""Method cards in Hank's answers (v1.4; owner, 23.10.2026: "I want him to be able to explain how to top the node
properly since this is also a very small space. It's important to have one proper node and we're going basically
after the heads. So like Hydra kind of.").

The ten cards of kartei/methods/ (topping, fimming, mainlining, lst, scrog, supercropping, lollipopping,
defoliation, basil_pinching, tomato_suckering) answer from code, word for word as the cards put them:

1. Which card a question names: the card's own aliases and label (both languages), the forms of the glossary term of
   the same technique (GLOSSARY_LINK: the glossary's own aliases, such as "toppen", "lolipopping", "mainlinig"), a
   few verb forms of the question itself (QUESTION_FORMS: "Wie toppe ich", "how do I top my plant"), and the
   typos: short ones as fixed forms ("Topen" in QUESTION_FORMS, "Lolipopping" and "Mainlinig" in the glossary's
   aliases), long ones by one edit (_typo: word and form eight letters or more, the first letter right, and the word
   no known word). The rule for five letters took everyday words for method names ("tippen" for "toppen", "filmen"
   for "fimmen", "Tipping" for "Topping", review M1). The other words of a question may carry one typo too
   ("ricthig", "Autofolwer", _is_filler) once a method is named. The forms are question patterns in code: no card
   gets an alias its sources do not carry.
2. The owner's own words: more heads or main shoots ("mehr Köpfe", "mehrere Haupttriebe", "more colas", "Hydra",
   "one node many heads", HEADS_PATTERNS) lead to the mainlining card first and the topping card after it.
3. The question's form (form_of): a how-to ("Wie toppe ich richtig?") gets the numbered steps first, then when, the
   autoflower line and the box fit as the card words them (its source marks kept, "Erfahrungsquelle" included),
   the first risk sentence that cites a source, the sources and the draft note; "Kann ich noch toppen?" gets when,
   autoflower and the risk; "Was ist FIMing?" (no glossary term) the card's "what" and where the steps are. An
   explain question about a glossary term keeps the glossary's text (asking.py) and gets one line more: the method
   card has the steps (pointer_line). A question that names a method and asks something else ("Hilft Topping gegen
   die Höhe?") is searched in the method cards (router, the method rule).
4. The stage window: topping, FIM and mainlining belong before flowering (PRE_FLOWER; each card's "when" says so).
   While the plant flowers (the stage the owner set, or the estimate, said as an estimate first), the answer says
   once, first, that for this plant the window has passed and the card is for the next grow. Before flowering
   nothing is added. The LST card says less: no new ties once flowering runs and the stretch slows, and that M3 does
   not say whether a first start in flower makes sense. So LST gets its line only from mid flower on (TIES_DONE),
   and the line quotes the card's own sentence (review M2; the task's "new LST ties before flowering" is wider than
   the card).
5. The cannabis package: a card or glossary term of a package that is switched off (STATE_DIR/settings.json, read as
   the wiki reads it) is not answered from; Hank says the package is switched off (route package_off).

Texts: the catalog below, in German and English, registered into i18n like asking.py.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vgrow import config, i18n, plant
from vgrow.i18n import t

from . import asking, typo
from .kartei_index import packages_on
from .livestate import LiveState, collect
from .types import Answer, Route, SourceRef

_LOGGER = logging.getLogger(__name__)

METHODS_DIR = config.REPO / "kartei" / "methods"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
KIND = "methods"
# The cards whose "when" puts them before flowering: topping ("vor den ersten Härchen", M20), fimming ("in der Blüte
# nie", M6, M8), mainlining ("Nur in der Wachstumsphase", M1). The test test_pre_flower_cards_say_so_in_their_when
# checks the cards still say it.
PRE_FLOWER = ("topping", "fimming", "mainlining")
# LST: "Wenn die Blüte läuft und das Streckungswachstum nachlässt, musst du keine neuen Binder mehr setzen" (M3). The
# early flower is the stretch itself, so the line comes only from these stages on, and it quotes that sentence.
TIES_DONE = ("mid_flower", "late_flower", "harvest")
_TIES_SENTENCE = {"de": re.compile(r"\bkeine neuen Binder\b"), "en": re.compile(r"\bno longer need to add ties\b")}
# A glossary term and the method card of the same technique (the glossary's keys, kartei/glossary/glossary.json).
GLOSSARY_LINK = {"topping": "topping", "low-stress-training": "lst", "lollipopping": "lollipopping",
                 "defoliation": "defoliation", "mainlining": "mainlining"}
HEADS = ("mainlining", "topping")  # more heads: the manifold first, then the single cut it is built from
# Verb forms a question uses for a technique ("Wie toppe ich?"): question patterns, never aliases of a card.
QUESTION_FORMS: dict[str, tuple[str, ...]] = {
    "topping": ("toppe", "toppst", "toppt", "toppen", "getoppt", "getopt", "topp", "topped", "topping",
                "topen", "toppn", "topings"),  # the last three: typos as fixed forms (review M1)
    "fimming": ("fime", "fimst", "fimt", "fimmen", "gefimt", "fimmed", "fiming", "fimen"),
    "mainlining": ("mainline", "mainlinen", "gemainlint", "mainlined", "mainlinning", "mainlinig"),
    "defoliation": ("entlaube", "entlaubst", "entlaubt", "entlauben", "defoliate", "defoliating"),
    "lollipopping": ("lollipoppe", "lollipoppen", "gelollipoppt", "lollipop", "lolipoping", "lolipopping"),
    "supercropping": ("supercroppe", "supercroppen", "supercrop", "supercropped"),
    "scrog": ("scroggen", "scrogge", "scrogging"),
}
# "top" alone is a method word only next to a verb's company ("how do I top my plant", "can I still top"), never in
# "on top" or "top of the plant".
_TOP_BEFORE = frozenset({"i", "you", "we", "to", "can", "should", "could", "would", "and", "or", "not", "still",
                         "already", "ich", "du", "man"})
_TOP_AFTER = frozenset({"my", "the", "it", "her", "him", "this", "a", "an", "them", "for", "autoflower", "autoflowers",
                        "auto", "autos", "plant", "plants", "now", "again", "properly", "correctly", "right", "once",
                        "twice", "at", "above", "meine", "meinen", "die", "sie"})
# "how many heads" and "wie viele Köpfe" count, they do not ask for more (the lookbehinds); "Verteiler" is an
# everyday word (a power strip) and names no method here (review N4).
HEADS_PATTERNS = [re.compile(p) for p in (
    r"(?<!\bhow )(?<!\bwie )\b(?:mehr|mehrere|viele|several|more|multiple|many|lots of|extra|zusatzliche)(?: \w+)? "
    r"(?:kopfe|kopfen|kopf|haupttriebe|haupttrieben|haupttrieb|hauptriebe|colas|cola|heads|head|tops|main stems|"
    r"main shoots|main colas|main branches|blutenstande)\b",
    r"\bhydra\b",
    r"\b(?:one|a single|ein|einem|einen) (?:node|knoten)\b.*\b(?:heads|kopfe|colas|tops|haupttriebe)\b",
    r"\b(?:manifold|manifolding)\b",
)]
_HEADS_WORDS = frozenset({"mehr", "mehrere", "viele", "several", "more", "multiple", "many", "lots", "extra",
                          "zusatzliche", "kopfe", "kopf", "haupttriebe", "haupttrieb", "hauptriebe", "colas", "cola",
                          "heads", "head", "tops", "main", "stems", "shoots", "branches", "blutenstande", "hydra",
                          "node", "knoten", "one", "single", "ein", "einem", "einen", "manifold", "manifolding",
                          "verteiler", "like", "wie", "so", "eine", "art", "kind", "of", "sort", "auf", "aus"})
_FILLER = (asking._HOWTO_WORDS | asking._TRAIL | asking._ARTICLES | _HEADS_WORDS | frozenset({
    "kann", "darf", "soll", "sollte", "sollen", "muss", "konnte", "can", "could", "should", "may", "must", "noch",
    "still", "jetzt", "now", "schon", "already", "wann", "when", "zu", "spat", "late", "too", "ok", "okay", "safe",
    "sicher", "ist", "is", "es", "it", "meine", "meiner", "mein", "meinen", "my", "autoflower", "autoflowers", "auto",
    "autos", "pflanze", "plant", "pflanzen", "plants", "wie", "how", "was", "what", "whats", "heisst", "bedeutet",
    "erklar", "erklare", "explain", "mean", "means", "und", "and", "oder", "or", "get", "bekomme", "kriege", "make",
    "mache", "do", "does", "i", "ich", "man", "you", "we", "wir", "richtig", "properly", "correctly", "right",
    "best", "am", "besten", "fur", "for", "um", "damit", "dann", "then", "wieder", "again", "eigentlich", "genau",
    "macht", "machen", "geht", "funktioniert", "work", "works", "sie", "her", "ihr", "die", "der", "das", "dem",
    "den", "a", "an", "the", "this", "that", "diese", "dieser", "durch", "by", "with", "mit", "im", "in", "my",
    "erst", "first", "it", "lassen", "let", "ein", "einmal", "once", "try", "versuchen", "probieren", "anfangen",
    "beginnen", "start", "fange", "starte", "sinn", "sense", "worth", "lohnt", "sich", "es", "noch", "box", "vgrow",
    "growbox", "zelt", "tent", "kleinen", "small", "space", "platz",
    # the owner's own sentences ("how to top the nodes properly", "multiple heads from one node", "mehrere Köpfe von
    # einem Knoten", review S4)
    "nodes", "from", "von", "per", "out", "off", "on", "at", "into", "tip", "tips", "stem", "spitze", "triebspitze",
    "trieb"}))
_HOWTO_ANYWHERE = re.compile(r"\b(?:wie (?:geht|mache|macht|mach|funktioniert|klappt)|how (?:do|does|to|can|should)"
                             r"(?: (?:i|you|we|one|it|that|this))?)\b")
_CAN_WORDS = frozenset({"kann", "darf", "soll", "sollte", "muss", "konnte", "can", "could", "should", "may", "must",
                        "wann", "when", "spat", "late", "lohnt", "worth"})
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"„“(\[])")
_ABBREV = re.compile(r"\b(?:ca|bzw|vgl|ggf|evtl|inkl|max|min|Nr|Abs|Art|etc|approx|vs|al|z|B|[A-Za-z])\.$")
_SOURCE_ID = re.compile(r"(?<![\w])([A-Z]{1,2}\d{1,3})(?![\w])")

DE: dict[str, str] = {
    "hank.method.howto_head": "{label}, Schritt für Schritt (aus der Methodenkarte):",
    "hank.method.more_head": "{label} (aus der Methodenkarte):",
    "hank.method.can_head": "{label}, wann es passt (aus der Methodenkarte):",
    "hank.method.when": "Zeitpunkt: {text}",
    "hank.method.autoflower": "Autoflower: {text}",
    "hank.method.box_fit": "In der VGrow: {text}",
    "hank.method.risk": "Risiko: {text}",
    "hank.method.draft": "Aus der Methodenkarte: Entwurf, noch nicht freigegeben.",
    "hank.method.window": "{how}: deine Pflanze steht in Phase {stage}, also in der Blüte. Für {what} ist das "
                          "Fenster bei ihr vorbei, die Karte gilt für den nächsten Durchgang.",
    "hank.method.window_ties": "{how}: deine Pflanze steht in Phase {stage}. Zu neuen LST-Bindern sagt die Karte: "
                               "{quote}",
    "hank.method.window.topping": "Topping",
    "hank.method.window.fimming": "FIMing",
    "hank.method.window.mainlining": "Mainlining",
    "hank.method.window.heads": "Mainlining und Topping",
    "hank.method.pointer": "Die Schritte stehen auf der Methodenkarte {label}: frag mich „{ask}“.",
    "hank.method.ask": "Wie mache ich {short}?",
    "hank.method.heads_more": "Den einzelnen Schnitt, mit dem das anfängt, zeigt die Karte {label} unten unter "
                              "„{more}“.",
    "hank.method.more": "Mehr aus der Kartei",
    "hank.package.off": "Das Cannabis-Paket ist ausgeschaltet: aus seinen Karten antworte ich gerade nicht, auch "
                        "nicht zu {label}. Einschalten kannst du es im Wiki unter „Cannabis-Wissen anzeigen“.",
    "hank.package.off_plain": "Das Cannabis-Paket ist ausgeschaltet: aus seinen Karten antworte ich gerade nicht. "
                              "Einschalten kannst du es im Wiki unter „Cannabis-Wissen anzeigen“.",
}

EN: dict[str, str] = {
    "hank.method.howto_head": "{label}, step by step (from the method card):",
    "hank.method.more_head": "{label} (from the method card):",
    "hank.method.can_head": "{label}, when it fits (from the method card):",
    "hank.method.when": "When: {text}",
    "hank.method.autoflower": "Autoflower: {text}",
    "hank.method.box_fit": "In the VGrow: {text}",
    "hank.method.risk": "Risk: {text}",
    "hank.method.draft": "From the method card: draft, not yet approved.",
    "hank.method.window": "{how}: your plant is at stage {stage}, so it is flowering. For this plant the window "
                          "for {what} has passed, the card is for the next grow.",
    "hank.method.window_ties": "{how}: your plant is at stage {stage}. About new LST ties the card says: {quote}",
    "hank.method.window.topping": "topping",
    "hank.method.window.fimming": "FIMing",
    "hank.method.window.mainlining": "mainlining",
    "hank.method.window.heads": "mainlining and topping",
    "hank.method.pointer": "The steps are on the method card {label}: ask me \"{ask}\".",
    "hank.method.ask": "How do I do {short}?",
    "hank.method.heads_more": "The single cut it starts with is on the card {label}, below under \"{more}\".",
    "hank.method.more": "More from the card file",
    "hank.package.off": "The cannabis package is switched off: I do not answer from its cards right now, {label} "
                        "included. You can switch it on in the wiki under \"Show cannabis knowledge\".",
    "hank.package.off_plain": "The cannabis package is switched off: I do not answer from its cards right now. You can "
                              "switch it on in the wiki under \"Show cannabis knowledge\".",
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
    problems = check()
    if problems:
        raise ValueError("methods: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _lang(lang: str | None) -> str:
    return lang if lang in ("de", "en") else "de"


# ---------- the cards ----------

@dataclass(frozen=True)
class Method:
    key: str
    label: Mapping[str, str]
    status: str
    package: str | None
    texts: Mapping[str, Mapping[str, str]]  # field -> {"de": ..., "en": ...} (what, when, autoflower, box_fit, risks)
    steps: Mapping[str, tuple[str, ...]]
    sources: Mapping[str, Any]
    forms: tuple[tuple[str, ...], ...]  # folded word sequences that name the card (its own aliases and labels)

    def short(self, lang: str) -> str:
        """The label's name without its explanation: "Topping" of "Topping (Triebspitze abschneiden)", "LST" of
        "LST (Low Stress Training): Herunterbinden", "Mainlining" of "Mainlining / Manifolding"."""
        label = self.label.get(lang) or self.label["de"]
        return re.split(r" \(|: | / ", label)[0].strip()

    def text(self, name: str, lang: str) -> str:
        found = self.texts.get(name) or {}
        return (found.get(lang) or found.get("de") or "").strip()

    def step_list(self, lang: str) -> tuple[str, ...]:
        return self.steps.get(lang) or self.steps.get("de") or ()

    def visible(self, on: Iterable[str] | None = None) -> bool:
        return not self.package or self.package in set(packages_on() if on is None else on)

    @property
    def draft(self) -> bool:
        return self.status != "approved"


def _short_forms(label: str) -> list[str]:
    return [label, re.split(r" \(|: ", label)[0], *re.split(r" \(|: ", label)[0].split(" / ")]


def _method(path: Path, data: Mapping[str, Any]) -> Method | None:
    key = str(data.get("key") or path.stem)
    if not data.get("steps") and not data.get("what"):
        return None
    label = {"de": str(data.get("label") or key), "en": str(data.get("label_en") or data.get("label") or key)}
    texts = {}
    for name in ("what", "when", "autoflower", "box_fit", "risks"):
        de, en = data.get(name), data.get(f"{name}_en")
        if isinstance(de, str) and de.strip():
            texts[name] = {"de": de, "en": en if isinstance(en, str) and en.strip() else de}
    steps_de = tuple(s for s in data.get("steps") or [] if isinstance(s, str) and s.strip())
    steps_en = tuple(s for s in data.get("steps_en") or [] if isinstance(s, str) and s.strip())
    aliases = data.get("aliases") if isinstance(data.get("aliases"), Mapping) else {}
    forms: list[tuple[str, ...]] = []
    for surface in [*_short_forms(label["de"]), *_short_forms(label["en"]), *(aliases.get("de") or []),
                    *(aliases.get("en") or [])]:
        found = tuple(asking.words(str(surface)))
        if found and found not in forms:
            forms.append(found)
    package = data.get("package") if isinstance(data.get("package"), str) and data.get("package") else None
    status = "draft" if str(data.get("status") or "draft").lower() == "draft" else str(data.get("status"))
    return Method(key, label, status, package, texts, {"de": steps_de, "en": steps_en or steps_de},
                  dict(data.get("sources") or {}), tuple(forms))


@dataclass
class Library:
    methods: dict[str, Method] = field(default_factory=dict)

    def get(self, key: str | None) -> Method | None:
        return self.methods.get(key or "")


_CACHE: dict[str, tuple[tuple[Any, ...], Library]] = {}


def library(folder: Path | None = None) -> Library:
    """The method cards, read again when a file changed (size or mtime); an empty library without the folder."""
    folder = folder or METHODS_DIR
    try:
        files = sorted(folder.glob("*.json"))
        sig = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files)
    except OSError:
        return Library()
    hit = _CACHE.get(str(folder))
    if hit is not None and hit[0] == sig:
        return hit[1]
    out = Library()
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            _LOGGER.warning("Hank methods: %s unreadable (%s), skipped", path, err)
            continue
        if isinstance(data, Mapping):
            found = _method(path, data)
            if found is not None:
                out.methods[found.key] = found
    _CACHE[str(folder)] = (sig, out)
    return out


def method_of_term(term_key: str | None, lib: Library | None = None) -> Method | None:
    """The method card of a glossary term (GLOSSARY_LINK), or None."""
    key = GLOSSARY_LINK.get(term_key or "")
    return (lib or library()).get(key) if key else None


def term_package(term: asking.Term | None) -> str | None:
    return getattr(term, "package", None) if term is not None else None


def term_visible(term: asking.Term | None, on: Iterable[str] | None = None) -> bool:
    package = term_package(term)
    return not package or package in set(packages_on() if on is None else on)


# ---------- which card a question names ----------

@dataclass
class Named:
    keys: list[str] = field(default_factory=list)  # the methods named, in text order
    used: set[int] = field(default_factory=set)  # word positions that named them
    heads: bool = False  # the owner's "more heads" wording (HEADS_PATTERNS)
    ambiguous: bool = False  # one word that names two cards ("pinching": topping and basil_pinching)

    @property
    def one(self) -> str | None:
        return self.keys[0] if len(self.keys) == 1 and not self.ambiguous else None


def _forms_of(method: Method, gloss: asking.Glossary) -> list[tuple[str, ...]]:
    forms = list(method.forms)
    for term_key, key in GLOSSARY_LINK.items():
        term = gloss.get(term_key) if key == method.key else None
        if term is not None:
            forms += [a for a in term.aliases if a not in forms]
    forms += [(w,) for w in QUESTION_FORMS.get(method.key, ()) if (w,) not in forms]
    return forms


_BASIC: list[typo.Vocab] = []


def basic_known(word: str) -> bool:
    """A stop word or a common word (typo.Vocab without a directory): the fallback without the index's vocabulary."""
    if not _BASIC:
        _BASIC.append(typo.Vocab())
    return _BASIC[0].is_known(word)


Known = Callable[[str], bool]


def _typo(word: str, form: str, known: Known) -> bool:
    """One typo in a long word: word and form eight letters or more, the first letter right, the word itself no known
    word ("Mainlniing" is "mainlining", "Lolippoping" is "lollipopping"). Shorter typos are fixed forms (review M1:
    at five letters "tippen" was "toppen", at seven "tipping" is "topping")."""
    return (len(word) >= 8 and len(form) >= 8 and word[0] == form[0] and typo.osa(word, form, 1) == 1
            and not known(word))


_FILLER_LONG = tuple(sorted(w for w in _FILLER if len(w) >= 6))


def _is_filler(word: str, known: Known) -> bool:
    """A filler word, or one typo in a filler word of six letters or more that is no known word itself ("ricthig",
    "Autofolwer", "multpile"). Only asked once a method or more heads is named, so it never names a card."""
    if word in _FILLER:
        return True
    return len(word) >= 6 and not known(word) and any(
        f[0] == word[0] and abs(len(f) - len(word)) <= 1 and typo.osa(word, f, 1) == 1 for f in _FILLER_LONG)


_HEADS_LONG = tuple(sorted(w for w in _HEADS_WORDS if len(w) >= 6))


def _mended(word: str, known: Known) -> str:
    """A heads word with one typo, mended for HEADS_PATTERNS ("multpile" is "multiple"), six letters or more and no
    known word itself; any other word as it is."""
    if word in _HEADS_WORDS or len(word) < 6 or known(word):
        return word
    return next((h for h in _HEADS_LONG if h[0] == word[0] and typo.osa(word, h, 1) == 1), word)


def named(q: str, *, lib: Library | None = None, gloss: asking.Glossary | None = None,
          known: Known | None = None) -> Named:
    """The method cards a question names, and whether it asks for more heads. known: the index's vocabulary
    (router), else stop words and common words."""
    known = known or basic_known
    lib = lib or library()
    gloss = gloss or asking.glossary()
    found = asking.words(q)
    joined = " ".join(_mended(w, known) for w in found)  # "multpile heads" is "multiple heads"
    out = Named(heads=any(p.search(joined) for p in HEADS_PATTERNS))
    spans: list[tuple[int, int, int, str]] = []  # (start, end, quality, key); quality 2 exact, 1 one typo
    for method in lib.methods.values():
        for form in _forms_of(method, gloss):
            n = len(form)
            for i in range(len(found) - n + 1):
                if tuple(found[i:i + n]) == form:
                    spans.append((i, i + n, 2, method.key))
                elif n == 1 and _typo(found[i], form[0], known):
                    spans.append((i, i + 1, 1, method.key))
    for i, word in enumerate(found):  # "how do I top my plant", "can I still top"
        if word in ("top", "tops") and (i > 0 and found[i - 1] in _TOP_BEFORE
                                        or i + 1 < len(found) and found[i + 1] in _TOP_AFTER) \
                and not (i > 0 and found[i - 1] in ("on", "the", "of")):
            spans.append((i, i + 1, 2, "topping"))
    spans.sort(key=lambda s: (-(s[1] - s[0]), -s[2], s[0]))
    taken: dict[int, tuple[int, int]] = {}  # position -> (length, quality) of the span that took it
    for start, end, quality, key in spans:
        positions = range(start, end)
        if any(p in taken for p in positions):
            first = taken.get(start)
            if first == (end - start, quality) and key not in out.keys and all(p in out.used for p in positions):
                out.ambiguous = True  # the same word, as good a match, for a second card
                out.keys.append(key)
            continue
        for p in positions:
            taken[p] = (end - start, quality)
        out.used.update(positions)
        if key not in out.keys:
            out.keys.append(key)
    return out


def _rest(q: str, used: set[int]) -> list[str]:
    return [w for i, w in enumerate(asking.words(q)) if i not in used]


def form_of(q: str, asked: asking.Asked, found: Named, known: Known | None = None) -> str | None:
    """The form of a question about one method (or more heads): howto, can, explain, heads; None when it asks
    something else as well (then the search answers, from the method cards: the router's method rule)."""
    words = asking.words(q)
    rest = _rest(q, found.used)
    if any(not _is_filler(w, known or basic_known) for w in rest):
        return None
    if not found.keys and not found.heads:
        return None
    lead = asking._without_lead(words)
    howto = asked.qtype == "howto" or (len(lead) > 1 and lead[0] == "wie" and words.index(lead[1]) in found.used) \
        or bool(_HOWTO_ANYWHERE.search(" ".join(words)))  # "Topen, wie geht das richtig?"
    explain_one = asked.qtype == "explain" and found.one is not None  # "Was ist Manifolding?": the glossary (N4)
    if found.heads and not explain_one and (howto or not found.keys or set(found.keys) <= set(HEADS)):
        return "heads" if (howto or not set(rest) & _CAN_WORDS) else "can"
    if found.one is None:
        return None
    if asked.qtype == "explain":
        return "explain"
    if howto:
        return "howto"
    if set(rest) & _CAN_WORDS:
        return "can"
    return "explain"


# ---------- routing ----------

def route(q: str, lang: str, asked: asking.Asked, *, gloss: asking.Glossary | None = None,
          lib: Library | None = None, known: Known | None = None) -> Route | None:
    """The route of a question about a method card, from code (no search), or None. An explain question about a
    glossary term is left to asking.typed_route (the glossary's text, with pointer_line added by core)."""
    gloss = gloss or asking.glossary()
    lib = lib or library()
    found = named(q, lib=lib, gloss=gloss, known=known)
    if not found.keys and not found.heads:
        return None
    form = form_of(q, asked, found, known)
    keys = list(HEADS) if form == "heads" else ([found.one] if found.one else list(found.keys))
    methods = [m for m in (lib.get(k) for k in keys) if m is not None]
    if not methods:
        return None
    if form is None:
        hidden = [m for m in methods if not m.visible()]
        if hidden and len(hidden) == len(methods) and not found.ambiguous:  # "Hilft Topping gegen ...?" while off
            return package_off_route(lang, label=hidden[0].short(lang), reason=f"package_off:methods/{hidden[0].key}")
        return None  # the search answers it (router: the method rule)
    linked = _linked(methods[0].key, gloss) if form == "explain" and len(methods) == 1 else None
    glossary_shown = linked is not None and term_visible(linked)  # "Was ist Entlauben?" with the package off (S3)
    if not glossary_shown and not all(m.visible() for m in methods):
        hidden = next(m for m in methods if not m.visible())
        return package_off_route(lang, label=hidden.short(lang), reason=f"package_off:methods/{hidden.key}")
    method = methods[0]
    if form == "explain":
        term = next((gloss.get(t) for t, k in GLOSSARY_LINK.items() if k == method.key and gloss.get(t)), None)
        if term is not None and asked.qtype == "explain":
            if gloss.find(asked.subject) is not None:
                return None  # the glossary explains it; core adds the pointer to the steps
            # "Was ist Mainlniing?": the card was named by a typo the glossary does not know; the same route
            return Route("explain", None, None, None, "info", lang, f"explain:{term.key}", qtype="explain",
                         term=term.key)
        if term is not None:  # "Topping?": the glossary term alone
            return Route("explain", None, None, None, "info", lang, f"explain:{term.key};method:{method.key}",
                         qtype="explain", term=term.key)
    cards = [f"{KIND}/{m.key}" for m in methods]
    return Route("explain", None, None, None, "info", lang, f"method:{form}:{'+'.join(m.key for m in methods)}",
                 qtype="howto" if form in ("howto", "heads") else "explain", term=_linked_term(method.key, gloss),
                 cards=cards, form=form)


def _linked(key: str, gloss: asking.Glossary) -> asking.Term | None:
    """The glossary term of a method card, or None."""
    found = _linked_term(key, gloss)
    return gloss.get(found) if found else None


def _linked_term(key: str, gloss: asking.Glossary) -> str | None:
    return next((t for t, k in GLOSSARY_LINK.items() if k == key and gloss.get(t) is not None), None)


def package_off_route(lang: str, *, label: str | None, reason: str) -> Route:
    route_ = Route("package_off", None, None, None, "info", lang, reason)
    route_.reply = package_off_text(lang, label)
    return route_


def package_off_text(lang: str, label: str | None) -> str:
    lang = _lang(lang)
    return t("hank.package.off", lang, label=label) if label else t("hank.package.off_plain", lang)


def guard_term(found: Route | None, gloss: asking.Glossary | None = None) -> Route | None:
    """A typed route about a glossary term of a package that is switched off becomes package_off (v1.4)."""
    if found is None or not found.term:
        return found
    gloss = gloss or asking.glossary()
    term = gloss.get(found.term)
    if term is None or term_visible(term):
        return found
    return package_off_route(found.lang, label=term.short(found.lang), reason=f"package_off:glossary/{term.key}")


# ---------- answers ----------

def sentences(s: str) -> list[str]:
    out: list[str] = []
    for part in _SENTENCE.split(s.strip()):
        if out and _ABBREV.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return [p for p in out if p.strip()]


def main_risk(method: Method, lang: str) -> str | None:
    """The first sentence of the card's risks that cites a source (an introduction such as "Die ersten vier Punkte
    sind Erfahrungsquellen." cites none and is skipped)."""
    for sentence in sentences(method.text("risks", lang)):
        if _SOURCE_ID.search(sentence) and any(sid in method.sources for sid in _SOURCE_ID.findall(sentence)):
            return sentence.strip()
    return None


def flowering(state: LiveState) -> dict[str, Any] | None:
    """The stage while the plant flowers (the owner's stage or the estimate, as the rest of Hank reads it), else
    None."""
    stage = state.stage
    if stage and stage.get("key") in plant.FLOWERING_STAGES:
        return stage
    return None


def _how(stage: Mapping[str, Any], lang: str) -> str:
    """How the stage is known, said first and with a capital ("Nur geschätzt, nicht bestätigt", review N2)."""
    set_by = stage.get("set_by")
    how = t(f"hank.digest.set_by.{set_by}", lang) if set_by in ("manual", "estimate", "days") else str(set_by or "?")
    return how[:1].upper() + how[1:]


def window_line(keys: Iterable[str], state: LiveState, lang: str, lib: Library | None = None) -> str | None:
    """Once, first: the plant flowers, so the window of a pre-flowering method has passed for it. LST from mid
    flower on: the card's own sentence about new ties (TIES_DONE)."""
    keys = list(keys)
    stage = flowering(state)
    if stage is None:
        return None
    label = stage.get("label") or stage.get("key")
    pre = [k for k in keys if k in PRE_FLOWER]
    if pre:
        what = t("hank.method.window.heads", lang) if set(pre) == set(HEADS) else t(f"hank.method.window.{pre[0]}",
                                                                                    lang)
        return t("hank.method.window", lang, stage=label, how=_how(stage, lang), what=what)
    lst = (lib or library()).get("lst") if "lst" in keys else None
    if lst is None or stage.get("key") not in TIES_DONE:
        return None
    quote = next((s for s in sentences(lst.text("when", lang)) if _TIES_SENTENCE[lang].search(s)), None)
    return t("hank.method.window_ties", lang, stage=label, how=_how(stage, lang), quote=quote) if quote else None


def _block(method: Method, form: str, lang: str) -> list[str]:
    """The card's lines for one form, word for word as the card puts them."""
    label = method.label.get(lang) or method.label["de"]
    lines: list[str] = []
    if form in ("howto", "heads", "more"):
        lines.append(t("hank.method.more_head" if form == "more" else "hank.method.howto_head", lang, label=label))
        if form == "more" and method.text("what", lang):
            lines.append(method.text("what", lang))
        lines += [f"{n}. {step.strip()}" for n, step in enumerate(method.step_list(lang), 1)]
    elif form == "can":
        lines.append(t("hank.method.can_head", lang, label=label))
    elif form == "explain":
        lines.append(t("hank.method.more_head", lang, label=label))
        lines.append(method.text("what", lang))
        lines.append(pointer(method, lang))
        return [line for line in lines if line]
    for name, key in (("when", "hank.method.when"), ("autoflower", "hank.method.autoflower"),
                      ("box_fit", "hank.method.box_fit")):
        if name == "box_fit" and form == "can":
            continue
        body = method.text(name, lang)
        if body:
            lines.append(t(key, lang, text=body))
    risk = main_risk(method, lang)
    if risk:
        lines.append(t("hank.method.risk", lang, text=risk))
    return lines


def _natural(sid: str) -> tuple[str, int]:
    m = re.match(r"([A-Z]+)(\d+)$", sid)
    return (m.group(1), int(m.group(2))) if m else (sid, 0)


_RANGE = re.compile(r"(?<![\w])([A-Z]{1,2})(\d{1,3}) (?:bis|to|-|–) (?:([A-Z]{1,2}))?(\d{1,3})(?![\w])")


def cited(lines: Iterable[str], table: Mapping[str, Any]) -> list[str]:
    """The source ids the lines cite that the card's table holds, M1 before M2 before M10; a range ("M1 bis M6",
    "M1 to M6") cites every id in it."""
    ids: set[str] = set()
    for line in lines:
        ids.update(sid for sid in _SOURCE_ID.findall(line) if sid in table)
        for prefix, lo, other, hi in _RANGE.findall(line):
            if (other and other != prefix) or int(hi) - int(lo) > 50:
                continue
            ids.update(f"{prefix}{n}" for n in range(int(lo), int(hi) + 1) if f"{prefix}{n}" in table)
    return sorted(ids, key=_natural)


def resolve(index: Any, table: Mapping[str, Any], ids: list[str], lang: str) -> list[SourceRef]:
    """A card's sources by id, through the index (seed-shop hosts lose their link, decision D3); without an index the
    chat policy's hosts are applied here, so a code answer never links a seed shop."""
    if index is not None and hasattr(index, "refs"):
        return [_with_id(ref) for ref in index.refs(dict(table), ids, lang)]
    from .kartei_index import load_policy, parse_source

    hosts = (load_policy().get("blocked_source_hosts") or {}).get("hosts") or []
    out = []
    for sid in ids:
        ref = parse_source(sid, table[sid], lang)
        host = (re.match(r"^[a-z]+://([^/:?#]+)", (ref.url or "").lower()) or [None, ""])[1]
        if ref.url and any(host == h or host.endswith("." + h) for h in hosts):
            ref = SourceRef(ref.id, ref.title, None)
        out.append(_with_id(ref))
    return out


def _with_id(ref: SourceRef) -> SourceRef:
    """The card's own id in front of the title ("M18: ..."): the text cites "(M18, Erfahrungsquelle)" and the page
    numbers its sources by position, so the id is how a reader finds the source a sentence names (review S1)."""
    if not ref.id or ref.title.startswith(f"{ref.id}: "):
        return ref
    return SourceRef(ref.id, f"{ref.id}: {ref.title}" if ref.title else ref.id, ref.url)


def _refs(index: Any, method: Method, ids: list[str], lang: str) -> list[SourceRef]:
    return resolve(index, method.sources, ids, lang)


def pointer(method: Method, lang: str) -> str:
    return t("hank.method.pointer", lang, label=method.label.get(lang) or method.label["de"],
             ask=t("hank.method.ask", lang, short=method.short(lang)))


def answer(route_: Route, live: LiveState | Mapping[str, Any], *, answer_id: str, index: Any = None,
           lib: Library | None = None) -> Answer:
    """The answer of a method route (route.cards, route.form), from the cards alone."""
    lib = lib or library()
    lang = _lang(route_.lang)
    form = route_.form or "howto"
    keys = [c.split("/", 1)[1] for c in route_.cards or [] if c.startswith(f"{KIND}/")]
    methods = [m for m in (lib.get(k) for k in keys) if m is not None]
    if not methods:  # the card went away between routing and answering
        return Answer(id=answer_id, route="no_data", lang=lang, text=t("hank.nodata.plain", lang), gap=True)
    if not all(m.visible() for m in methods):
        hidden = next(m for m in methods if not m.visible())
        return Answer(id=answer_id, route="package_off", lang=lang, text=package_off_text(lang, hidden.short(lang)))
    state = collect(live)
    first, *others = methods
    lines: list[str] = []
    window = window_line([m.key for m in methods] if form == "heads" else [first.key], state, lang, lib) \
        if form in ("howto", "heads", "can", "more") else None
    if window:
        lines.append(window)
    lines += _block(first, "howto" if form == "heads" else form, lang)
    if form == "heads" and others:
        second = others[0]
        lines.append(t("hank.method.heads_more", lang, label=second.label.get(lang) or second.label["de"],
                       more=t("hank.method.more", lang)))
    if any(m.draft for m in methods):
        lines.append(t("hank.method.draft", lang))
    sources = _refs(index, first, cited(lines, first.sources), lang)
    more, more_sources = "", []
    if form == "heads" and others:  # the topping card, folded away with its own sources ("Mehr aus der Kartei")
        second = others[0]
        block = _block(second, "howto", lang)
        if second.draft:
            block.append(t("hank.method.draft", lang))
        more ="\n".join(block)
        more_sources = _refs(index, second, cited(block, second.sources), lang)
    return Answer(id=answer_id, route="explain", lang=lang, text="\n".join(lines), sources=sources, more=more,
                  more_sources=more_sources)


def with_pointer(found: Answer, route_: Route, lib: Library | None = None) -> Answer:
    """An explanation from the glossary about a technique that has a method card: one line more, last, that the
    card has the steps (owner's rule: "Explain questions keep the glossary's short text")."""
    method = method_of_term(route_.term, lib)
    if method is None or not method.visible() or route_.qtype not in (None, "explain"):
        return found
    if found.route not in ("explain", "no_data"):
        return found
    found.text = f"{found.text}\n{pointer(method, _lang(found.lang))}"
    return found


def topic_label(route_: Route, lib: Library | None = None) -> dict[str, str] | None:
    """The method a method answer was about, by its short name, for follow-ups ("und wie mache ich das?")."""
    keys = [c.split("/", 1)[1] for c in route_.cards or [] if c.startswith(f"{KIND}/")]
    method = (lib or library()).get(keys[0]) if keys else None
    return {lang: method.short(lang) for lang in ("de", "en")} if method is not None else None


__all__ = ["DE", "EN", "GLOSSARY_LINK", "HEADS", "PRE_FLOWER", "QUESTION_FORMS", "Library", "Method", "Named",
           "answer", "check", "cited", "flowering", "form_of", "guard_term", "library", "main_risk", "method_of_term",
           "named", "package_off_route", "package_off_text", "pointer", "register", "route", "sentences",
           "topic_label", "window_line", "with_pointer"]
