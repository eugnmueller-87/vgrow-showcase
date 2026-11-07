"""Slots of a question (spec 3.3 step 1) and the inflection-aware name matcher they rest on (spec 2.5, 2.6).

Slots are the facts the code reads straight out of a question, before any search: the stage ("späte Blüte"), the
metrics ("Luftfeuchte"), the devices ("Abluft"), a flowering week ("4. Blütewoche", "week 4"), the nutrient page
("Light-Mix") and components ("Bio-Bloom"), litres, a percent value, a fan step, a switch wish (an/aus, hoch/runter)
and the "now" markers ("diese Woche", "jetzt", "gerade"). The search uses them for its boosts (spec 2.4), the router
for the action intent (spec 3.5).

Names: the directory (kartei/_hank/vocab.json) is matched word by word over stemmed tokens, so inflected forms find
their entry ("in der späten Blüte" finds "späte Blüte", "Blüten" finds "Blüte") and a hyphen name matches written
joined, apart or with the hyphen ("Light-Mix", "Light Mix", "Lightmix"). This matcher never corrects typos; that is
typo.scan(), which runs before it on the raw question.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from vgrow.hank import text

MAX_NAME_TOKENS = 6  # longest name in tokens the matcher tries

# Devices that belong to the box (spec 4.2 groups light, fans, USB, camera, automation). maps_to values of
# kartei/_hank/vocab.json, entry ids of the same file, and the USB port keys of ports.json.
LIVE_DEVICES = frozenset({
    "dfan", "cfan", "light", "exhaust_fan", "circulation_fan", "humidifier", "lamp", "growcam", "probe", "water_tank",
    "usb_port", "automation", "vgrow", "pump", "usb1", "usb2",
})

# Adjectives that name a metric ("Wie warm ist es?" asks for the temperature, "Wie feucht ist es?" for the
# humidity), as stemmed tokens. They do so only when the question names no other object: "Ist die Erde zu feucht?",
# "Wie heiß wird die Lampe?", "Can I water with cold water?" ask about the soil, the lamp, the water (finding 6).
ADJECTIVES = frozenset({"warm", "kalt", "heiss", "kuhl", "warmth", "hot", "cold", "cool", "feucht", "humid"})
OTHER_OBJECT_RE = re.compile(r"\b(?:erde\w*|boden\w*|wasser\w*|lampe\w*|topf\w*|topfe\w*|substrat\w*|"
                             r"soil\w*|water\w*|lamps?|pots?|ground)\b")


def other_object(q: str) -> bool:
    """The question names a thing that is not the air of the box (soil, water, lamp, pot)."""
    return OTHER_OBJECT_RE.search(text.fold(q)) is not None


def adjective_only(q: str, hits: Iterable["NameHit"]) -> set[str]:
    """Entities of metric hits that came from an adjective alone in a question about another object: they name no
    metric there (finding 6)."""
    hits = list(hits)
    if not other_object(q):
        return set()
    streams = (text.tokens(q, lang="de"), text.tokens(q, lang="en"))
    by_entity: dict[str, list[NameHit]] = {}
    for hit in hits:
        if hit.kind == "metric":
            by_entity.setdefault(hit.entity, []).append(hit)
    out: set[str] = set()
    for entity, found in by_entity.items():
        if all(h.end - h.start == 1 and any(h.start < len(s) and s[h.start] in ADJECTIVES for s in streams)
               for h in found):
            out.add(entity)
    return out


# Nutrient pages and components as the nutrient card names them (kartei/nutrients/*.json); vocab maps_to points here.
PAGES = frozenset({"light_mix", "all_mix", "label"})  # feeding v2: the VIVOSUN card has one page, the label
COMPONENTS = frozenset({"bio_grow", "bio_bloom", "top_max", "alg_a_mic", "base_a", "base_b"})

# Switching verbs of spec 3.5 (dreh, stell, mach, schalt, set, turn, switch), matched on folded words by prefix.
_VERB_RE = re.compile(r"(?<![^\W\d_])(?:dreh|stell|mach|schalt|set|turn|switch)\w*", re.IGNORECASE)
_SWITCH_WORDS = {
    "an": "on", "ein": "on", "on": "on", "einschalten": "on", "anschalten": "on", "anmachen": "on",
    "aus": "off", "off": "off", "ausschalten": "off", "abschalten": "off", "ausmachen": "off",
    "hoch": "up", "hoher": "up", "rauf": "up", "up": "up", "higher": "up", "increase": "up",
    "runter": "down", "herunter": "down", "niedriger": "down", "down": "down", "lower": "down", "decrease": "down",
}
# "Now" markers (spec 3.3: "diese Woche/jetzt/gerade"), on folded text.
_THIS_WEEK_RE = re.compile(r"\b(?:diese[rnm]?\s+woche|this\s+week)\b")
_NOW_RE = re.compile(r"\b(?:jetzt|gerade|aktuell|momentan|derzeit|zurzeit|heute|now|currently|today|"
                     r"at\s+the\s+moment|right\s+now)\b")

_ORDINALS = {
    "erst": 1, "zweit": 2, "dritt": 3, "viert": 4, "funft": 5, "sechst": 6, "siebt": 7, "acht": 8, "neunt": 9,
    "zehnt": 10, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10,
}
_ORD = "|".join(sorted(_ORDINALS, key=len, reverse=True))
_FLOWER_WEEK = r"(?:blute|bluh)(?:te)?woche|(?:flowering|bloom|flower)\s+week"
# A flowering week, in folded text. Group "n" is a number, group "o" an ordinal word.
_WEEK_PATTERNS = (
    (re.compile(rf"\b(?P<n>\d{{1,2}})\s*(?:\.|st|nd|rd|th)?\s*(?:{_FLOWER_WEEK})"), "bloom"),
    (re.compile(rf"\b(?P<o>{_ORD})\w*\s+(?:{_FLOWER_WEEK})"), "bloom"),
    (re.compile(rf"(?:{_FLOWER_WEEK})\s*(?:nr\.?\s*|no\.?\s*)?(?P<n>\d{{1,2}})\b"), "bloom"),
    (re.compile(r"\bweek\s*(?P<n>\d{1,2})\s+of\s+(?:flowering|bloom)"), "bloom"),
    (re.compile(r"\b(?P<n>\d{1,2})\s*(?:\.|st|nd|rd|th)?\s*(?:woche|week)\b"), None),
    (re.compile(rf"\b(?P<o>{_ORD})\w*\s+(?:woche|week)\b"), None),
    (re.compile(r"\b(?:woche|week)\s*(?P<n>\d{1,2})\b"), None),
)
_FLOWER_WORD_RE = re.compile(r"(?<![^\W\d_])(?:blute|bluh|bloom|flower)")
_STEP_BEFORE_RE = re.compile(r"\b(?:stufe|step|level)\s*(\d{1,2})\b")  # "Stufe 4"; "4 Stufen" comes from parse_numbers


# ---------- the name matcher ----------

@dataclass(frozen=True)
class Name:
    """One directory entry as the matcher keeps it."""

    entity: str
    kind: str
    maps_to: str | None


@dataclass(frozen=True)
class NameHit:
    """A directory name found in a text: the entry, and the token span (start, end) in the stemmed token list."""

    entity: str
    kind: str
    maps_to: str | None
    start: int
    end: int


def _atoms(name: str) -> list[str]:
    return [a for a in re.split(r"[\s\-]+", name.strip()) if a]


class Names:
    """Directory names matched over stemmed tokens (German and English stems both), longest name first.

    entries: dicts in the vocab.json form {"id", "kind", "de": [...], "en": [...], "maps_to"}."""

    def __init__(self, entries: Iterable[Mapping[str, Any]] = ()) -> None:
        self._keys: dict[tuple[str, ...], list[Name]] = {}
        self.entries: dict[str, Name] = {}
        self.longest = 1
        for entry in entries:
            self.add(entry)

    def add(self, entry: Mapping[str, Any]) -> None:
        entity = str(entry.get("id") or "").strip()
        if not entity:
            return
        maps_to = entry.get("maps_to")
        name = Name(entity, str(entry.get("kind") or ""), str(maps_to) if isinstance(maps_to, str) else None)
        self.entries.setdefault(entity, name)
        for lang in ("de", "en"):
            surfaces = entry.get(lang) or []
            for surface in [surfaces] if isinstance(surfaces, str) else surfaces:
                if isinstance(surface, str) and surface.strip():
                    self._add_surface(surface, name)

    def _add_surface(self, surface: str, name: Name) -> None:
        atoms = _atoms(surface)
        variants = [" ".join(atoms)]  # as written, a hyphen read as a space
        if len(atoms) > 1:
            variants.append("".join(atoms))  # written joined: "Lightmix"
        for variant in variants:
            for lang in ("de", "en"):
                key = tuple(text.tokens(variant, lang=lang))
                if not key or len(key) > MAX_NAME_TOKENS:
                    continue
                bucket = self._keys.setdefault(key, [])
                if all(n.entity != name.entity for n in bucket):
                    bucket.append(name)
                self.longest = max(self.longest, len(key))

    def find(self, s: str) -> list[NameHit]:
        """All names in a text, longest first and without overlap, in text order."""
        streams = [text.tokens(s, lang="de"), text.tokens(s, lang="en")]
        size = len(streams[0])
        hits: list[NameHit] = []
        i = 0
        while i < size:
            found = None
            for n in range(min(self.longest, size - i), 0, -1):
                for stream in streams:
                    names = self._keys.get(tuple(stream[i:i + n]))
                    if names:
                        found = (n, names)
                        break
                if found:
                    break
            if found:
                n, names = found
                hits.extend(NameHit(nm.entity, nm.kind, nm.maps_to, i, i + n) for nm in names)
                i += n
            else:
                i += 1
        return hits

    def entities(self, s: str) -> list[str]:
        """Entry ids named in a text, first occurrence order, no duplicates."""
        out: list[str] = []
        for hit in self.find(s):
            if hit.entity not in out:
                out.append(hit.entity)
        return out


# ---------- slots ----------

@dataclass
class Slots:
    """What the code reads out of a question (spec 3.3 step 1). Keys are those of the cards and of the box."""

    stage: str | None = None  # stage key named (late_flower); the first if several are named
    stages: list[str] = field(default_factory=list)  # every stage key named
    flowering: bool = False  # "Blüte" named without a specific stage
    metrics: list[str] = field(default_factory=list)  # metric keys (maps_to), e.g. probe_humidity
    devices: list[str] = field(default_factory=list)  # device keys (maps_to, else the entry id), e.g. dfan
    week: int | None = None  # a week number named ("4. Blütewoche", "week 4")
    week_kind: str | None = None  # "bloom" for a flowering week; None for a plain "Woche 4"
    page: str | None = None  # nutrient page named (light_mix, all_mix)
    components: list[str] = field(default_factory=list)  # nutrient components named (bio_bloom, ...)
    liters: float | None = None
    percent: float | None = None
    step: float | None = None
    switch: str | None = None  # on | off | up | down
    verb: bool = False  # a switching verb (dreh, stell, mach, schalt, set, turn, switch)
    now: bool = False  # "jetzt", "gerade", "now", ... or "diese Woche"
    this_week: bool = False  # "diese Woche", "this week"
    entities: list[str] = field(default_factory=list)  # every directory entry named, first occurrence order

    @property
    def live(self) -> bool:
        """A question about the box as it is now (spec 3.3 step 3): a now marker ("diese Woche", "jetzt", "gerade"),
        or a box device or a metric together with a switching verb or an on/off word ("Dreh das Licht hoch", an
        action wish of spec 3.5; "Wieso ist der Befeuchter aus?", a question about its state). Naming a device alone
        ("die VGrow-Lampe") is not enough: that is a question about the device. Known limit: the German preposition
        "an" reads as "on" ("die Feuchte an den Blüten")."""
        return self.now or (bool(self.box_devices or self.metrics) and (self.verb or self.switch in ("on", "off")))

    @property
    def box_devices(self) -> list[str]:
        """The named devices that are part of the box (LIVE_DEVICES), for the router's live-state rule."""
        return [d for d in self.devices if d in LIVE_DEVICES]

    @property
    def nutrient(self) -> bool:
        """The question names a nutrient page, a component or a week."""
        return bool(self.page or self.components or self.week is not None)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__} | {"live": self.live,
                                                                             "nutrient": self.nutrient}


def _week(folded: str) -> tuple[int | None, str | None]:
    for pattern, kind in _WEEK_PATTERNS:
        match = pattern.search(folded)
        if not match:
            continue
        groups = match.groupdict()
        number = int(groups["n"]) if groups.get("n") else _ORDINALS.get(groups.get("o") or "")
        if number is None or not 0 < number <= 20:
            continue
        if kind is None and _FLOWER_WORD_RE.search(folded):
            kind = "bloom"
        return number, kind
    return None, None


def _switch(folded: str) -> str | None:
    for word in re.findall(r"[^\W\d_]+", folded):
        if word in _SWITCH_WORDS:
            return _SWITCH_WORDS[word]
    return None


def read(q: str, lang: str = "de", *, names: Names | None = None,
         hits: Iterable[NameHit] | None = None) -> Slots:
    """The slots of a (typo-corrected) question. names: the directory matcher; hits: its result for q, if the caller
    already has it."""
    found = list(hits) if hits is not None else (names.find(q) if names is not None else [])
    adjectives = adjective_only(q, found)
    found = [hit for hit in found if hit.entity not in adjectives]
    slots = Slots()
    for hit in found:
        if hit.entity not in slots.entities:
            slots.entities.append(hit.entity)
        key = hit.maps_to
        if hit.kind == "stage":
            if key:
                if key not in slots.stages:
                    slots.stages.append(key)
            elif hit.entity == "flowering":
                slots.flowering = True
        elif hit.kind == "metric" and key:
            if key not in slots.metrics:
                slots.metrics.append(key)
        elif hit.kind == "device":
            device = key or hit.entity
            if device not in slots.devices:
                slots.devices.append(device)
        elif hit.kind == "product" and key:
            if key in PAGES and slots.page is None:
                slots.page = key
            elif key in COMPONENTS and key not in slots.components:
                slots.components.append(key)
    slots.stage = slots.stages[0] if slots.stages else None
    folded = text.fold(q)
    slots.week, slots.week_kind = _week(folded)
    for num in text.parse_numbers(q, lang):
        if num.unit == "l" and slots.liters is None:
            slots.liters = num.value
        elif num.unit == "%" and slots.percent is None:
            slots.percent = num.value
        elif num.unit == "step" and slots.step is None:
            slots.step = num.value
    if slots.step is None:
        before = _STEP_BEFORE_RE.search(folded)
        if before:
            slots.step = float(before.group(1))
    slots.verb = bool(_VERB_RE.search(folded))
    slots.switch = _switch(folded)
    slots.this_week = bool(_THIS_WEEK_RE.search(folded))
    slots.now = slots.this_week or bool(_NOW_RE.search(folded))
    return slots
