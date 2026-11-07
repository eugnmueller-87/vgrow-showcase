"""Hank's strain advice (owner, 23.10.2026: "Ich möchte, dass man Hank fragen kann, welche Sorte sich zum Beispiel für
den Anbau am besten eignet, dass er die Person optimal beraten kann. Welche Sorte das dann ist und wie die Person die
kauft, ist der Person selbst überlassen.").

Code decides, the model is never asked: the answer is a code answer like the method cards' (no polish, no job).

1. Which questions (route): an advice question ("Welche Sorte passt in meine Box?", "Welche Autoflower für
   Anfänger?", "Ich will eine kurze Blüte", "what strain fits my grow box?"), always in a cannabis context (the
   question names cannabis, or no other plant and the run is a cannabis run, as the sale gate reads it); a question
   about one strain of the lexicon ("Was weißt du über Northern Lights Auto?", "Erzähl mir mehr über ...", "Passt
   Zkittlez in meine Box?"); and the short reply to Hank's own question while one is open in the chat.
2. At most two short questions, one at a time, only when they matter: autoflower or photoperiod while the question
   says neither; then the first grow or experience while neither that nor a time wish is known (the reply may give
   the longest time to harvest instead). The answers are kept for the chat in the router's TopicBook (Topic.advice),
   so a second advice question in the same chat is answered at once, and a short reply after the advice ("und
   photoperiodisch?") asks again with that one wish changed.
3. The ranking (rank): strains that are too tall never come; those that fit for sure before those that fit with
   training (LST), those with an unclear height only when fewer than three others are left. Then the wishes: the type
   filters, a beginner's wish puts "einfach laut Hersteller" first, a short time or a longest time puts the shortest
   time first, one basis per ranking: "kurze Blüte" the flowering time, "schnelle Ernte" and a longest time seed to
   harvest (a strain without that figure only when no strain has it, then by its flowering time alone). Then difficulty, then how complete the maker's figures are, then the
   name. One suggestion per name.
4. The answer: three suggestions, each with its indoor height, its time (seed to harvest, else flowering), its
   difficulty and why it fits, with the lexicon's source ids; the box height with its source (S1 of the stage card);
   "Herstellerangabe, ungeprüft"; that the choice and the buying are the person's; where the full list is (the wiki's
   Sorten area). Shop names in running text follow decision D3 (claims.vendors), a source is never a link to a shop
   (chat_policy.json blocked_source_hosts), and nothing about THC, effects or taste is ever said: a question about
   them meets the health gate first (chat_policy.json blocked_topics), a buying question the sale gate's buy reply.
5. The cannabis package switched off in the wiki: the lexicon is off for Hank too (route package_off).

Texts: the catalog below, in German and English, registered into i18n like methods.py.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from vgrow import i18n
from vgrow import strains as lexicon_mod
from vgrow.i18n import t

from . import claims, methods, text, typo
from .kartei_index import packages_on
from .types import Answer, Route

_LOGGER = logging.getLogger(__name__)

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
ROUTE = "strains"
LEVELS = ("beginner", "easy", "experienced", "any")
PICKS = 3
MAX_QUESTIONS = 2
ABOUT_MAX = 5  # a name several strains share: this many lines, the rest is in the wiki
WIKI_LINK = "#wiki/sorten"

DE: dict[str, str] = {
    "hank.strains.ask.flowering": "Gern, ich suche dir drei Sorten aus dem Sortenlexikon heraus. Eine kurze Frage "
                                  "vorher: Soll es eine Autoflower sein oder eine photoperiodische Sorte?",
    "hank.strains.ask.level": "Noch eine Frage: Ist das dein erster Grow, oder hast du schon Erfahrung? Du kannst mir "
                              "auch sagen, wie viele Wochen es von Samen bis Ernte höchstens dauern darf.",
    "hank.strains.chip.auto": "Autoflower",
    "hank.strains.chip.photo": "Photoperiodisch",
    "hank.strains.chip.any": "Egal",
    "hank.strains.chip.first": "Erster Grow",
    "hank.strains.chip.experienced": "Ich habe Erfahrung",
    "hank.strains.chip.weeks": "Höchstens 12 Wochen",
    "hank.strains.head.1": "Ein Vorschlag aus dem Sortenlexikon für deine VGrow ({cm} cm Wuchshöhe für eine "
                           "Pflanze, {id}), {wish}:",
    "hank.strains.head.2": "Zwei Vorschläge aus dem Sortenlexikon für deine VGrow ({cm} cm Wuchshöhe für eine "
                           "Pflanze, {id}), {wish}:",
    "hank.strains.head.3": "Drei Vorschläge aus dem Sortenlexikon für deine VGrow ({cm} cm Wuchshöhe für eine "
                           "Pflanze, {id}), {wish}:",
    "hank.strains.none": "Im Sortenlexikon finde ich keine Sorte, die in deine VGrow passt und zu {wish} passt. Lockere "
                         "eine Vorgabe, oder sieh dir im Wiki unter Sorten die ganze Liste an.",
    "hank.strains.wish.none": "ohne weitere Vorgabe",
    "hank.strains.wish.auto": "Autoflower",
    "hank.strains.wish.photo": "photoperiodisch",
    "hank.strains.wish.beginner": "für den ersten Grow",
    "hank.strains.wish.easy": "möglichst einfach",
    "hank.strains.wish.experienced": "mit Erfahrung",
    "hank.strains.wish.short": "möglichst kurze Blüte",
    "hank.strains.wish.short_harvest": "möglichst kurz von Samen bis Ernte",
    "hank.strains.wish.max": "höchstens {weeks} Wochen von Samen bis Ernte",
    "hank.strains.wish.brand": "von {brand}",
    "hank.strains.pick": "{n}. {label} ({brand}): Höhe innen {height}; {time}; Schwierigkeit {difficulty}. Passt, "
                         "weil {why} ({ids}).",
    "hank.strains.time.harvest": "Samen bis Ernte {text}",
    "hank.strains.time.bloom": "Blüte {text}",
    "hank.strains.time.none": "keine Zeitangabe",
    "hank.strains.height.none": "keine Angabe",
    "hank.strains.difficulty.easy": "einfach",
    "hank.strains.difficulty.medium": "mittel",
    "hank.strains.difficulty.hard": "schwer",
    "hank.strains.difficulty.none": "keine Angabe",
    "hank.strains.why.sure": "sie sicher in die Box passt",
    "hank.strains.why.lst": "das untere Ende ({lo} cm) mit Training (LST) unter die {cm} cm passt",
    "hank.strains.why.unknown": "der Hersteller nur „bis {hi} cm“ angibt, die Höhe ist also offen",
    "hank.strains.why.unknown_none": "der Hersteller keine Höhe angibt, die Höhe ist also offen",
    "hank.strains.why.auto": "sie eine Autoflower ist",
    "hank.strains.why.photo": "sie photoperiodisch ist",
    "hank.strains.why.easy": "der Hersteller sie einfach nennt",
    "hank.strains.why.short": "{time} zu den kürzesten passenden Zeiten gehört",
    "hank.strains.why.max": "{time} in deine {weeks} Wochen passt",
    "hank.strains.why.bloom_only": "die Blüte allein ({time}) in deine {weeks} Wochen passt, Samen bis Ernte nennt der "
                                   "Hersteller nicht",
    "hank.strains.why.brand": "sie von {brand} ist",
    "hank.strains.why.own": "es die Sorte deiner Pflanze ist",
    "hank.strains.and": "und",
    "hank.strains.fewer": "Mehr passende Sorten finde ich im Lexikon nicht.",
    "hank.strains.lst": "Mit Training heißt: die Sorte kann über die Box hinauswachsen, Herunterbinden (LST) hält sie "
                        "flach. Die Schritte stehen auf der Methodenkarte LST.",
    "hank.strains.note": "Alle Zahlen: Herstellerangabe, ungeprüft.",
    "hank.strains.choice": "Welche Sorte du nimmst und wie du sie kaufst, entscheidest du selbst.",
    "hank.strains.more": "Alle {n} Sorten mit Filtern stehen im Wiki unter Sorten.",
    "hank.strains.link": "Sortenlexikon öffnen",
    "hank.strains.draft": "Aus dem Sortenlexikon: Entwurf, noch nicht freigegeben.",
    "hank.strains.about_head": "{label} ({brand}), aus dem Sortenlexikon:",
    "hank.strains.about_own": "{label}, die Sorte deiner Pflanze, aus ihrer Karte:",
    "hank.strains.about_many": "Im Sortenlexikon stehen {n} Sorten mit dem Namen {name}:",
    "hank.strains.about_not_auto": "Eine {asked} steht nicht im Sortenlexikon. Unter dem Namen {name} stehen dort nur "
                                   "photoperiodische Sorten:",
    "hank.strains.about_not_photo": "Eine photoperiodische {name} steht nicht im Sortenlexikon, nur diese "
                                    "Autoflower:",
    "hank.strains.about_line": "{n}. {label} ({brand}), {type}: {time}, Höhe innen {height}, in der VGrow {fit} "
                               "({ids}).",
    "hank.strains.about_rest": "Dazu {n} weitere im Wiki unter Sorten.",
    "hank.strains.about_fact": "{label}: {text}",
    "hank.strains.variety_head": "{label}, aus der Sortenenzyklopädie im Wiki:",
    "hank.strains.variety_class": "Einordnung: {value} ({ids})",
    "hank.strains.variety_parents": "Eltern: {value} ({ids})",
    "hank.strains.variety_other": "Eine andere Quelle nennt andere Eltern: {value} ({ids})",
    "hank.strains.variety_children": "Daraus gezüchtet: {value} ({ids})",
    "hank.strains.variety_nogrow": "Wuchshöhe und Blütezeit stehen dort nicht, und im Sortenlexikon der Züchter steht "
                                   "{label} nicht. Ob sie in deine VGrow passt, kann ich deshalb nicht sagen.",
    "hank.strains.variety_nocm": "Zentimeter nennt die Quelle nicht, und im Sortenlexikon der Züchter steht {label} "
                                 "nicht. Ob sie genau in deine VGrow passt, kann ich deshalb nicht sagen.",
    "hank.strains.variety_grow": "Anbau laut Quelle: {facts} ({ids}).",
    "hank.strains.grow.difficulty": "Schwierigkeit {value}",
    "hank.strains.grow.height": "Höhe {value}",
    "hank.strains.grow.yield": "Ertrag {value}",
    "hank.strains.grow.days": "Blüte rund {weeks} Wochen",
    "hank.strains.grow.height_cm": "Höhe innen {lo} bis {hi} cm",
    "hank.strains.grow.yield_g": "Ertrag innen {lo} bis {hi} g/m²",
    "hank.strains.grow.days_span": "Blüte {lo} bis {hi} Tage",
    "hank.strains.grow.days_one": "Blüte {n} Tage",
    "hank.strains.grow.height_cm_one": "Höhe innen {n} cm",
    "hank.strains.grow.height_cm_upto": "Höhe innen bis {hi} cm",
    "hank.strains.grow.yield_g_one": "Ertrag innen {n} g/m²",
    "hank.strains.grow.yield_g_upto": "Ertrag innen bis {hi} g/m²",
    "hank.strains.variety_box": "Deine VGrow hat {box} cm Wuchshöhe. Angaben der Hersteller sind ungeprüft.",
    "hank.strains.level.easy": "einfach",
    "hank.strains.level.moderate": "mittel",
    "hank.strains.level.difficult": "schwierig",
    "hank.strains.level.short": "klein",
    "hank.strains.level.average": "mittel",
    "hank.strains.level.tall": "hoch",
    "hank.strains.level.low": "wenig",
    "hank.strains.level.medium": "mittel",
    "hank.strains.level.high": "viel",
    "hank.strains.variety_verdict": "Im Steckbrief im Wiki heißt das: {verdict}.",
    "hank.strains.verdict.good": "gute Wahl für die nächste Runde",
    "hank.strains.verdict.fine": "machbar, mit etwas Aufmerksamkeit",
    "hank.strains.verdict.catch": "machbar, aber mit Haken",
    "hank.strains.verdict.later": "eher was für später",
    "hank.strains.verdict.partial": "nur zum Teil bekannt",
    "hank.strains.verdict.partial_fine": "sieht machbar aus, wie schwer sie ist, ist offen",
    "hank.strains.verdict.partial_catch": "hat einen Haken, wie schwer sie ist, ist offen",
    "hank.strains.variety_also": "In der Sortenenzyklopädie: {value}, Eltern {parents} ({ids}).",
    "hank.strains.variety_also_split": "In der Sortenenzyklopädie: {value} ({ids_c}), eine andere Quelle sagt "
                                       "{other} ({ids_o}); Eltern {parents} ({ids_p}).",
    "hank.strains.variety_other_class": "Eine andere Quelle ordnet sie anders ein: {value} ({ids})",
    "hank.strains.variety_link": "Eintrag im Wiki öffnen",
    "hank.strains.unknown": "Zu {name} finde ich nichts, weder im Sortenlexikon ({n} Sorten der Züchter) noch in der "
                            "Sortenenzyklopädie ({m} Sorten). Ich erfinde dazu nichts.",
    "hank.strains.unknown_any": "Zu {name} finde ich nichts: nicht im Sortenlexikon ({n} Sorten der Züchter), nicht in "
                                "der Sortenenzyklopädie ({m} Sorten) und nicht in meiner Kartei. Ich erfinde dazu "
                                "nichts.",
    "hank.strains.type.auto": "Autoflower",
    "hank.strains.type.photo": "photoperiodisch",
    "hank.strains.type.none": "Typ ohne Angabe",
    "hank.strains.geno.indica": "{p} % Indica",
    "hank.strains.geno.sativa": "{p} % Sativa",
    "hank.strains.geno.even": "je {p} % Indica und Sativa",
    "hank.strains.fit.sure": "In der VGrow: passt sicher, Höhe innen {h}, die Box hat {cm} cm Wuchshöhe für eine "
                             "Pflanze ({id}).",
    "hank.strains.fit.lst": "In der VGrow: nur mit Training (LST), Höhe innen {h}: das untere Ende passt unter die "
                            "{cm} cm ({id}), das obere nicht.",
    "hank.strains.fit.unknown": "In der VGrow: Höhe unklar, der Hersteller nennt innen nur „{h}“, mehr als die {cm} cm "
                                "der Box ({id}).",
    "hank.strains.fit.unknown_none": "In der VGrow: Höhe unklar, der Hersteller nennt keine Höhe innen; die Box hat "
                                     "{cm} cm ({id}).",
    "hank.strains.fit.tall": "In der VGrow: zu hoch, Höhe innen {h}, schon das untere Ende liegt über den {cm} cm "
                             "({id}).",
    "hank.strains.fitword.sure": "passt sicher",
    "hank.strains.fitword.lst": "nur mit Training",
    "hank.strains.fitword.unknown": "Höhe unklar",
    "hank.strains.fitword.tall": "zu hoch",
    "hank.strains.house_brand": "Marke eines Samenhändlers",
    "hank.strains.box": "Die VGrow hat {cm} cm Wuchshöhe für eine Pflanze ({id}).",
}

EN: dict[str, str] = {
    "hank.strains.ask.flowering": "Sure, I will pick three strains from the strain lexicon for you. One short question "
                                  "first: should it be an autoflower or a photoperiod strain?",
    "hank.strains.ask.level": "One more question: is this your first grow, or do you have experience? You can also "
                              "tell me how many weeks from seed to harvest it may take at most.",
    "hank.strains.chip.auto": "Autoflower",
    "hank.strains.chip.photo": "Photoperiod",
    "hank.strains.chip.any": "Doesn't matter",
    "hank.strains.chip.first": "First grow",
    "hank.strains.chip.experienced": "I have experience",
    "hank.strains.chip.weeks": "12 weeks at most",
    "hank.strains.head.1": "One suggestion from the strain lexicon for your VGrow ({cm} cm growing height for one "
                           "plant, {id}), {wish}:",
    "hank.strains.head.2": "Two suggestions from the strain lexicon for your VGrow ({cm} cm growing height for one "
                           "plant, {id}), {wish}:",
    "hank.strains.head.3": "Three suggestions from the strain lexicon for your VGrow ({cm} cm growing height for one "
                           "plant, {id}), {wish}:",
    "hank.strains.none": "In the strain lexicon I find no strain that fits your VGrow and {wish}. Loosen one wish, or "
                         "look at the whole list in the wiki under Strains.",
    "hank.strains.wish.none": "without any other wish",
    "hank.strains.wish.auto": "autoflower",
    "hank.strains.wish.photo": "photoperiod",
    "hank.strains.wish.beginner": "for a first grow",
    "hank.strains.wish.easy": "as easy as possible",
    "hank.strains.wish.experienced": "with experience",
    "hank.strains.wish.short": "flowering as short as possible",
    "hank.strains.wish.short_harvest": "as short as possible from seed to harvest",
    "hank.strains.wish.max": "at most {weeks} weeks from seed to harvest",
    "hank.strains.wish.brand": "from {brand}",
    "hank.strains.pick": "{n}. {label} ({brand}): indoor height {height}; {time}; difficulty {difficulty}. It fits "
                         "because {why} ({ids}).",
    "hank.strains.time.harvest": "seed to harvest {text}",
    "hank.strains.time.bloom": "flowering {text}",
    "hank.strains.time.none": "no time given",
    "hank.strains.height.none": "not given",
    "hank.strains.difficulty.easy": "easy",
    "hank.strains.difficulty.medium": "medium",
    "hank.strains.difficulty.hard": "hard",
    "hank.strains.difficulty.none": "not given",
    "hank.strains.why.sure": "it fits the box for sure",
    "hank.strains.why.lst": "with training (LST) the lower end ({lo} cm) fits under the {cm} cm",
    "hank.strains.why.unknown": "the maker only gives \"up to {hi} cm\", so the height is open",
    "hank.strains.why.unknown_none": "the maker gives no height, so the height is open",
    "hank.strains.why.auto": "it is an autoflower",
    "hank.strains.why.photo": "it is a photoperiod strain",
    "hank.strains.why.easy": "the maker calls it easy",
    "hank.strains.why.short": "{time} is among the shortest times that fit",
    "hank.strains.why.max": "{time} fits into your {weeks} weeks",
    "hank.strains.why.bloom_only": "the flowering alone ({time}) fits into your {weeks} weeks, the maker gives no seed "
                                   "to harvest time",
    "hank.strains.why.brand": "it is from {brand}",
    "hank.strains.why.own": "it is your plant's strain",
    "hank.strains.and": "and",
    "hank.strains.fewer": "I find no more strains in the lexicon that fit.",
    "hank.strains.lst": "With training means: the strain can outgrow the box, tying it down (LST) keeps it low. The "
                        "steps are on the method card LST.",
    "hank.strains.note": "All figures: maker's figure, unverified.",
    "hank.strains.choice": "Which strain you take and how you buy it is your decision.",
    "hank.strains.more": "All {n} strains with filters are in the wiki under Strains.",
    "hank.strains.link": "Open the strain lexicon",
    "hank.strains.draft": "From the strain lexicon: draft, not yet approved.",
    "hank.strains.about_head": "{label} ({brand}), from the strain lexicon:",
    "hank.strains.about_own": "{label}, your plant's strain, from its card:",
    "hank.strains.about_many": "The strain lexicon has {n} strains named {name}:",
    "hank.strains.about_not_auto": "There is no {asked} in the strain lexicon. Under the name {name} it has only "
                                   "photoperiod strains:",
    "hank.strains.about_not_photo": "There is no photoperiod {name} in the strain lexicon, only these autoflowers:",
    "hank.strains.about_line": "{n}. {label} ({brand}), {type}: {time}, indoor height {height}, in the VGrow {fit} "
                               "({ids}).",
    "hank.strains.about_rest": "{n} more in the wiki under Strains.",
    "hank.strains.about_fact": "{label}: {text}",
    "hank.strains.variety_head": "{label}, from the strain encyclopedia in the wiki:",
    "hank.strains.variety_class": "Classification: {value} ({ids})",
    "hank.strains.variety_parents": "Parents: {value} ({ids})",
    "hank.strains.variety_other": "Another source names other parents: {value} ({ids})",
    "hank.strains.variety_children": "Bred from it: {value} ({ids})",
    "hank.strains.variety_nogrow": "Height and flowering time are not given there, and the breeders' strain lexicon "
                                   "has no {label}. So I cannot say whether it fits your VGrow.",
    "hank.strains.variety_nocm": "The source gives no centimetres, and the breeders' strain lexicon has no {label}. "
                                 "So I cannot say exactly whether it fits your VGrow.",
    "hank.strains.variety_grow": "Growing, per the source: {facts} ({ids}).",
    "hank.strains.grow.difficulty": "difficulty {value}",
    "hank.strains.grow.height": "height {value}",
    "hank.strains.grow.yield": "yield {value}",
    "hank.strains.grow.days": "flowering about {weeks} weeks",
    "hank.strains.grow.height_cm": "indoor height {lo} to {hi} cm",
    "hank.strains.grow.yield_g": "indoor yield {lo} to {hi} g/m²",
    "hank.strains.grow.days_span": "flowering {lo} to {hi} days",
    "hank.strains.grow.days_one": "flowering {n} days",
    "hank.strains.grow.height_cm_one": "indoor height {n} cm",
    "hank.strains.grow.height_cm_upto": "indoor height up to {hi} cm",
    "hank.strains.grow.yield_g_one": "indoor yield {n} g/m²",
    "hank.strains.grow.yield_g_upto": "indoor yield up to {hi} g/m²",
    "hank.strains.variety_box": "Your VGrow has {box} cm of growing height. Makers' figures are unverified.",
    "hank.strains.level.easy": "easy",
    "hank.strains.level.moderate": "moderate",
    "hank.strains.level.difficult": "difficult",
    "hank.strains.level.short": "short",
    "hank.strains.level.average": "average",
    "hank.strains.level.tall": "tall",
    "hank.strains.level.low": "small",
    "hank.strains.level.medium": "medium",
    "hank.strains.level.high": "large",
    "hank.strains.variety_verdict": "In the wiki fact sheet that reads: {verdict}.",
    "hank.strains.verdict.good": "a good pick for your next grow",
    "hank.strains.verdict.fine": "doable with a little care",
    "hank.strains.verdict.catch": "doable, with a catch",
    "hank.strains.verdict.later": "better saved for later",
    "hank.strains.verdict.partial": "only partly known",
    "hank.strains.verdict.partial_fine": "looks doable, how hard it is stays open",
    "hank.strains.verdict.partial_catch": "has a catch, how hard it is stays open",
    "hank.strains.variety_also": "In the strain encyclopedia: {value}, parents {parents} ({ids}).",
    "hank.strains.variety_also_split": "In the strain encyclopedia: {value} ({ids_c}), another source says {other} "
                                       "({ids_o}); parents {parents} ({ids_p}).",
    "hank.strains.variety_other_class": "Another source classifies it differently: {value} ({ids})",
    "hank.strains.variety_link": "Open the wiki entry",
    "hank.strains.unknown": "I find nothing on {name}, neither in the strain lexicon ({n} strains from breeders) nor "
                            "in the strain encyclopedia ({m} strains). I will not make anything up about it.",
    "hank.strains.unknown_any": "I find nothing on {name}: not in the strain lexicon ({n} strains from breeders), not "
                                "in the strain encyclopedia ({m} strains) and not in my card file. I will not make "
                                "anything up about it.",
    "hank.strains.type.auto": "autoflower",
    "hank.strains.type.photo": "photoperiod",
    "hank.strains.type.none": "type not given",
    "hank.strains.geno.indica": "{p}% indica",
    "hank.strains.geno.sativa": "{p}% sativa",
    "hank.strains.geno.even": "{p}% indica, {p}% sativa",
    "hank.strains.fit.sure": "In the VGrow: fits for sure, indoor height {h}, the box has {cm} cm growing height for "
                             "one plant ({id}).",
    "hank.strains.fit.lst": "In the VGrow: only with training (LST), indoor height {h}: the lower end fits under the "
                            "{cm} cm ({id}), the upper end does not.",
    "hank.strains.fit.unknown": "In the VGrow: height unclear, the maker gives indoors only \"{h}\", more than the "
                                "box's {cm} cm ({id}).",
    "hank.strains.fit.unknown_none": "In the VGrow: height unclear, the maker gives no indoor height; the box has "
                                     "{cm} cm ({id}).",
    "hank.strains.fit.tall": "In the VGrow: too tall, indoor height {h}, even the lower end is above the {cm} cm "
                             "({id}).",
    "hank.strains.fitword.sure": "fits for sure",
    "hank.strains.fitword.lst": "only with training",
    "hank.strains.fitword.unknown": "height unclear",
    "hank.strains.fitword.tall": "too tall",
    "hank.strains.house_brand": "a seed shop's brand",
    "hank.strains.box": "The VGrow has {cm} cm growing height for one plant ({id}).",
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
        raise ValueError("strains: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _lang(lang: str | None) -> str:
    return lang if lang in ("de", "en") else "de"


# ---------- reading the question ----------

def _re(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts))


# "Auto" alone is a car as often as an autoflower ("Welches Auto soll ich mir kaufen?"): only "welche Auto" below
_NOUN = r"(?:sorte|sorten|strain|strains|autoflower|autoflowers|samen|seeds?|genetik\w*|photoperiod\w*)"
ADVICE = [re.compile(p) for p in (
    # "Welche Sorte passt in meine Box?", "Welche Autoflower ist am einfachsten?", "what strain fits my grow box?"
    rf"\b(?:welche[rsnm]?|was fur (?:eine?n?|ne)|which|what)(?: \w+){{0,2}} {_NOUN}\b(?![-\w])",
    r"\bwelche autos?\b(?![-\w])",
    # "Kannst du mir eine Sorte empfehlen?", "recommend a strain"
    r"\b(?:sorte|sorten|strain|strains)\b.{0,40}\b(?:empfehl\w*|empfiehl\w*|vorschlag\w*|vorschlagen|tipps?|recommend\w*|"
    r"suggest\w*|passt|passen|fits?|eignet|geeignet\w*|suitable)\b",
    r"\b(?:empfiehl\w*|empfehl\w*|recommend\w*|suggest\w*|vorschlag\w*|geeignete\w*|passende\w*|suitable)\b.{0,30}"
    r"\b(?:sorte|sorten|strain|strains)\b",
    # "die beste Autoflower", "the easiest strain", "die kleinste Sorte" (the adjective next to the noun)
    rf"\b(?:beste\w*|best|einfachste\w*|easiest|schnellste\w*|fastest|quickest|kleinste\w*|smallest|"
    rf"kompakteste\w*)(?: \w+)? {_NOUN}\b(?![-\w])",
    # "Ich will eine kurze Blüte", "I want a short flowering"
    r"\b(?:ich (?:will|mochte|hatte gern|suche|brauche)|i (?:want|would like|need|am looking for|m looking for)|"
    r"lieber)\b.{0,30}\b(?:kurz\w*|schnell\w*|short\w*|quick\w*|fast\w*)\b.{0,25}\b(?:blute\w*|flowering|bloom\w*|"
    r"ernte\w*|harvest\w*|grow|durchgang|zyklus|cycle)\b",
    # "Sorte für Anfänger", "strain for a small box"
    r"\b(?:sorte|sorten|strain|strains|autoflower\w*)\b.{0,20}\b(?:fur|for)\b.{0,20}\b(?:anfanger\w*|einsteiger\w*|"
    r"beginner\w*|newbies?|first|ersten?|box|vgrow|growbox|zelt|tent)\b",
)]
# a question about the running plant or about a growing value is no advice question ("Welche Sorte ist meine
# Pflanze?", "Welche Autoflower braucht wie viel Licht?")
NOT_ADVICE = re.compile(
    r"\b(?:meine|meiner|my|unsere|our) (?:pflanze|plant|sorte|strain)\b|\b(?:dung\w*|fertiliz\w*|nahrstoff\w*|"
    r"nutrient\w*|licht\w*|light\w*|lampe|lamp|wasser\w*|water\w*|giess\w*|ph|ec|temperatur\w*|temperature\w*|"
    r"feucht\w*|humid\w*|vpd|phase\w*|stage|schimmel\w*|mold|mould|trockn\w*|drying|curing|keim\w*|germinat\w*)\b")
ABOUT = [re.compile(p) for p in (
    r"^(?:und )?(?:was weisst du|was weiss du|was weist du|weisst du was) (?:uber|von|zu)(?: der sorte| die sorte)? "
    r"(?P<s>.+)$",
    r"^(?:erzahl|erzahle) (?:mir )?(?:mal )?(?:bitte )?(?:mehr |etwas |was |alles )?(?:uber|von|zu) (?P<s>.+)$",
    r"^(?:kennst du|infos? (?:zu|uber)|was ist mit)(?: der sorte| die sorte)? (?P<s>.+)$",
    r"^(?:what do you know about|tell me (?:more |something |a bit )?about|do you know|info (?:on|about)|what about)"
    r"(?: the strain)? (?P<s>.+)$",
    r"^(?:passt|wird) (?:die sorte )?(?P<s>.+?) (?:in (?:meine|die|deine|eine) (?:box|vgrow|growbox)|zu hoch|"
    r"zu gross)$",
    r"^(?:does|dos|will|would|can) (?:the strain )?(?P<s>.+?) (?:fit|grow) (?:in|into) (?:my|the|a) (?:box|vgrow|grow ?box)$",
    r"^(?:wie (?:hoch|gross|lange) (?:wird|blut|bluht|braucht)) (?:die sorte )?(?P<s>.+?)(?: indoor| innen)?$",
    r"^(?:how (?:tall|big|long) (?:does|dos|will)) (?:the strain )?(?P<s>.+?) (?:get|grow|flower|take)(?: indoors)?$",
    # "do we have information about purple punch?" (owner, 01.11.2026: Hank turned it away), "Haben wir Infos zu ...?"
    r"^(?:do|did|have) (?:we|you)(?: have| got| know)?(?: any| some)?(?: info| infos| information| details| data)?"
    r" (?:about|on|for|regarding)(?: the strain| the variety| the breed)? (?P<s>.+)$",
    r"^(?:haben|hast|habt|gibt) (?:wir|du|ihr|es)(?: irgendwelche| schon)?(?: infos?| informationen| details| daten|"
    r" was| etwas)? (?:uber|zu|zur|zum|von)(?: der sorte| die sorte| sorte)? (?P<s>.+)$",
)]
_TRAIL = frozenset({"bitte", "please", "mal", "denn", "eigentlich", "so", "genau", "lol", "pls"})
_ARTICLES = frozenset({"die", "der", "das", "den", "dem", "eine", "einer", "ein", "the", "a", "an"})
AUTO_WORDS = frozenset({"auto", "autos", "automatic", "automatics", "automatik", "autoflower", "autoflowers",
                        "autoflowering"})
# A strain named anywhere in a question about it (owner, 01.11.2026: "do we have information about purple punch?" got
# "Wrong guy for that" although the lexicon had it): every other word must ask for information, so "Wie oft gieße ich
# meine Purple Punch?" keeps its way. A one-word name ("Gelato", "Critical", "Grapefruit") counts only alone or beside
# one of the INFO words.
INFO_WORDS = frozenset({
    "info", "infos", "information", "informationen", "details", "daten", "data", "facts", "fakten", "wiki", "lexikon",
    "lexicon", "encyclopedia", "enzyklopadie", "sorte", "strain", "variety", "breed", "about", "uber", "know", "kennst",
    "tell", "erzahl", "erzahle"})
ASKING_WORDS = INFO_WORDS | _TRAIL | _ARTICLES | frozenset({
    "do", "does", "dos", "did", "we", "you", "i", "me", "us", "have", "has", "got", "any", "anything", "some", "something",
    "everything", "all", "on", "of", "for", "regarding", "re", "show", "give", "find", "look", "up", "search", "more",
    "bit", "little", "what", "whats", "is", "it", "its", "this", "that", "there", "in", "can", "could", "would", "want",
    "to", "learn", "read", "saved", "stored", "hey", "hi", "hello", "hank", "like", "cannabis",
    "haben", "hast", "hat", "habt", "wir", "du", "ihr", "ich", "mir", "uns", "zu", "zur", "zum", "von", "des", "einen",
    "was", "weisst", "weiss", "wissen", "kenne", "erzahlen", "sag", "sage", "zeig", "zeige", "such", "suche", "finde",
    "mehr", "etwas", "irgendwas", "alles", "gibt", "es", "sorten", "im", "in", "ist", "will", "mochte", "gern", "gerne",
    "gespeichert", "dazu", "kannst", "kann", "uberhaupt", "hallo"}) | frozenset({
    # what a strain's entry answers ("is Purple Punch good for beginners?", "Wie lange blüht Purple Punch?"): its type,
    # genetics, time, height, yield, difficulty and fit in the box. Never "my"/"meine": that is the owner's plant.
    "how", "tall", "big", "high", "height", "long", "flower", "flowers", "flowering", "bloom", "take", "takes",
    "harvest", "yield", "yields", "much", "easy", "hard", "difficult", "difficulty", "good", "beginner", "beginners",
    "newbie", "first", "indoors", "indoor", "outdoor", "outdoors", "fit", "fits", "parents", "genetics", "cross",
    "related", "type", "wie", "hoch", "gross", "lange", "lang", "bluht", "blute", "blutezeit", "ernte", "ertrag",
    "einfach", "schwer", "schwierig", "gut", "anfanger", "einsteiger", "innen", "aussen", "drinnen", "eltern",
    "genetik", "kreuzung", "verwandt", "typ", "dauert"}) | frozenset({
    # its type: "Ist Purple Punch Indica?", "Is Purple Punch indica or sativa?" (owner, 01.11.2026: "Meinst du Pumpe?")
    "indica", "sativa", "hybrid", "hybride", "dominant", "dominiert", "lastig", "indicalastig", "sativalastig",
    "indicadominant", "sativadominant", "oder", "or", "photo", "photoperiod", "photoperiodic", "photoperiodisch",
    "fotoperiodisch", "feminized", "feminisiert", "feminisierte", "regular"})
STRAIN_WORD = re.compile(r"\b(?:sorte|sorten|strain|strains|breed|variety|varieties|genetik|genetics|cannabis)\b")
# How strong a named strain is (holdout h27, 01.11.2026: "Wie stark ist Purple Punch?" passed every gate): the health
# gate's question, never the lexicon's. "high" is left out: "how high does it grow" asks for the height.
STRENGTH = re.compile(r"\b(?:stark|starke[rsnm]?|starkste\w*|strong|stronger|strongest|potent\w*|potenz|wirkung|wirkt|"
                      r"wirken|effect|effects|thc|cbd|ballert|knallt|turnt)\b")
# A drug, a cannabinoid or a health word is no unknown strain (review 23.10.2026: "Was weißt du über LSD?"): the
# honest no names the strain lexicon, so these keep the refusal. The words of tests/test_strain_lexicon.py NAME_WORDS.
NOT_A_STRAIN = re.compile(r"\b(?:thc|cbd|cbg|cannabinoid\w*|medical|medizin\w*|lsd\w*|ayahuasca|crack|kokain\w*|"
                          r"cocain\w*|heroin|opium|mdma|ecstasy|ketamin\w*|meth|dmt|psilo\w*|shrooms?|mescal\w*|"
                          r"peyote|drog\w*|drugs?)\b")
MAX_NAME_WORDS = 6

AUTO = re.compile(r"\b(?:auto|autos|autoflower|autoflowers|autoflowering|automatic|automatics|automatik|"
                  r"selbstbluhend\w*)\b(?![-\w])")
PHOTO = re.compile(r"\b(?:photo|photos|photoperiod\w*|foto|fotoperiod\w*|lichtgesteuert\w*|nicht (?:als |eine )?"
                   r"auto\w*|keine auto\w*|not an? auto\w*|no auto\w*)\b")
ANY = re.compile(r"\b(?:egal|beides|both|any|either|doesn ?t matter|does not matter|no preference|keine praferenz|"
                 r"weiss nicht|weis nicht|keine ahnung|don ?t know|not sure|unsicher|ist mir gleich|gleich)\b")
BEGINNER = re.compile(r"\b(?:anfanger\w*|einsteiger\w*|neuling\w*|beginner\w*|newbie\w*|novice\w*|erst\w*|first|"
                      r"noch nie|never|(?:keine|kaum|wenig|no|little|not much) (?:erfahrung|experience))\b")
EASY = re.compile(r"\b(?:einfach\w*|leicht|leichte\w*|easy|easiest|simple|simplest|unkompliziert\w*|"
                  r"pflegeleicht\w*|robust\w*)\b")
EXPERIENCED = re.compile(r"\b(?:erfahren\w*|erfahrung\w*|fortgeschritten\w*|profi\w*|experienc\w*|advanced|expert\w*|"
                         r"schon (?:oft|mehrmals|mehrere|ein paar|einige|viele)|not my first|nicht (?:mein|der|das) "
                         r"erst\w*)\b")
SHORT = re.compile(r"\b(?:kurz\w*|schnell\w*|zugig\w*|short\w*|quick\w*|fast|faster|fastest|rasch\w*)\b")
# a short wish about the whole run ("schnelle Ernte", "a quick harvest") compares seed to harvest; every other short
# wish ("kurze Blüte") the flowering time alone (review 23.10.2026, SHOULD 5); not "grow": "kurze Blüte für den ersten
# Grow" means the flowering
SHORT_HARVEST = re.compile(r"\b(?:ernte\w*|harvest\w*|samen bis|seed to|insgesamt|gesamt\w*|total|durchgang\w*|"
                           r"zyklus|cycle)\b")
# while Hank's question is open, a short question of its own is no reply to it ("Ist es zu kalt für Autoflower?")
ASIDE = re.compile(r"\b(?:wie|warum|wieso|weshalb|wann|wo|woran|ist es|ist das|how|why|when|where|is it|is this|too|"
                   r"zu (?:kalt|warm|heiss|nass|feucht|trocken|hell|dunkel|hoch|gross|klein|viel|wenig))\b")
WEEKS = re.compile(r"\b(\d{1,3})\s*(?:wochen|woche|wo|weeks?|wks?)\b")
DAYS = re.compile(r"\b(\d{1,3})\s*(?:tage|tagen|tag|days?)\b")
MONTHS = re.compile(r"\b(\d{1,2})\s*(?:monate|monaten|monat|months?)\b")
WORD_NUMBERS = {"zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
                "zehn": 10, "elf": 11, "zwolf": 12, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


# the words the advice is recognised by: one typo in a word of five letters or more, first letter right, is mended
# ("what strian fits", "Ich will eine kurze Blueete"; spec 11.3, the variants of the eval cases)
MEND_WORDS = ("strain", "strains", "sorte", "sorten", "autoflower", "autoflowers", "blute", "bluten", "blutezeit",
              "flowering", "anfanger", "beginner", "einfach", "einfachste", "einfachsten", "easiest", "photoperiod",
              "photoperiodisch", "photoperiodische", "empfehlen", "recommend", "schnell", "schnellste")


def _mend(word: str) -> str:
    if len(word) < 5 or word in MEND_WORDS:
        return word
    for target in MEND_WORDS:
        if word[0] == target[0] and abs(len(word) - len(target)) <= 1 and typo.osa(word, target, 1) <= 1:
            return target
    return word


def fold(s: str) -> str:
    """The question folded (text.fold), punctuation as spaces, and one typo in the advice's own words mended."""
    plain = re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", text.fold(s or ""))).replace("'", " ").strip()
    return " ".join(_mend(w) for w in plain.split(" ")) if plain else plain


def as_typed(q: str, words: list[str]) -> str:
    """Folded words of the question as the owner typed them: "Blue Zushi", not the folded "Blu Zushi"."""
    raw = re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", q or "")).replace("'", " ").split()
    folded = [fold(w) for w in raw]
    for i in range(len(raw) - len(words) + 1):
        if folded[i:i + len(words)] == words:
            return " ".join(raw[i:i + len(words)])
    return " ".join(words).title()


def _numbers(folded: str) -> str:
    return re.sub(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", lambda m: str(WORD_NUMBERS[m.group(1)]), folded)


@dataclass
class Wishes:
    flowering: str | None = None  # auto, photoperiod, any
    level: str | None = None  # beginner (a first grow), easy (as easy as possible), experienced, any
    short: bool = False
    short_on: str | None = None  # what a short wish compares: bloom (the flowering time) or harvest (seed to harvest)
    max_days: int | None = None
    brand: str | None = None
    asked: list[str] = field(default_factory=list)  # the questions Hank asked in this chat
    ask: str | None = None  # the question open now: flowering or level

    def as_dict(self) -> dict[str, Any]:
        return {"flowering": self.flowering, "level": self.level, "short": self.short, "short_on": self.short_on,
                "max_days": self.max_days,
                "brand": self.brand, "asked": list(self.asked), "ask": self.ask}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Wishes:
        data = data or {}
        days = data.get("max_days")
        return cls(flowering=data.get("flowering") if data.get("flowering") in ("auto", "photoperiod", "any") else None,
                   level=data.get("level") if data.get("level") in LEVELS else None,
                   short=data.get("short") is True,
                   short_on=data.get("short_on") if data.get("short_on") in ("bloom", "harvest") else None,
                   max_days=int(days) if isinstance(days, int) else None,
                   brand=str(data["brand"]) if isinstance(data.get("brand"), str) else None,
                   asked=[a for a in data.get("asked") or [] if a in ("flowering", "level")],
                   ask=data.get("ask") if data.get("ask") in ("flowering", "level") else None)


def _max_days(folded: str) -> int | None:
    s = _numbers(folded)
    for pattern, k in ((WEEKS, 7), (DAYS, 1), (MONTHS, 30)):
        m = pattern.search(s)
        if m:
            days = int(m.group(1)) * k
            if 21 <= days <= 400:
                return days
    return None


def _brand(folded: str, lex: lexicon_mod.Lexicon) -> str | None:
    for brand in sorted({s.brand for s in lex.strains if s.brand}, key=len, reverse=True):
        tokens = [w for w in lexicon_mod.words(brand) if w != "s"]
        if not tokens:
            continue
        joined = r"\W?s?\W?".join(re.escape(w) for w in tokens)
        if re.search(rf"\b{joined}\b", folded) or (len(tokens[0]) >= 6 and re.search(rf"\b{re.escape(tokens[0])}s?\b",
                                                                                      folded)):
            return brand
    return None


def wishes_of(q: str, lex: lexicon_mod.Lexicon, *, answering: str | None = None) -> tuple[Wishes, bool]:
    """(the wishes a text states, whether it stated any). answering: the question Hank asked, so "egal" or a bare
    "erster" answers that one."""
    folded = fold(q)
    w = Wishes()
    if PHOTO.search(folded):
        w.flowering = "photoperiod"
    elif AUTO.search(folded):
        w.flowering = "auto"
    if BEGINNER.search(folded):  # first, so "keine Erfahrung" is no experience
        w.level = "beginner"
    elif EXPERIENCED.search(folded):
        w.level = "experienced"
    elif EASY.search(folded):
        w.level = "easy"
    w.short = bool(SHORT.search(folded))
    if w.short:
        w.short_on = "harvest" if SHORT_HARVEST.search(folded) else "bloom"
    w.max_days = _max_days(folded)
    w.brand = _brand(folded, lex)
    if ANY.search(folded) and answering == "flowering" and w.flowering is None:
        w.flowering = "any"
    if ANY.search(folded) and answering == "level" and w.level is None and w.max_days is None:
        w.level = "any"
    said = bool(w.flowering or w.level or w.short or w.max_days or w.brand)
    return w, said


def merged(old: Wishes, new: Wishes) -> Wishes:
    """The chat's wishes with the new text's put over them (a stated wish replaces the old one)."""
    return Wishes(flowering=new.flowering or old.flowering, level=new.level or old.level,
                  short=new.short or old.short, short_on=new.short_on if new.short else old.short_on, max_days=new.max_days or old.max_days, brand=new.brand or old.brand,
                  asked=list(old.asked), ask=None)


def next_question(w: Wishes) -> str | None:
    """The question that still matters, or None: the type first, then the experience (unless a time is wished);
    never more than two, never one twice."""
    if len(w.asked) >= MAX_QUESTIONS:
        return None
    if w.flowering is None and "flowering" not in w.asked:
        return "flowering"
    if w.level is None and not w.short and w.max_days is None and "level" not in w.asked:
        return "level"
    return None


_REPLY_FILLER = frozenset("""
und and lieber oder or eher rather dann then bitte please doch mal nur only eine einen ein a an the what about how
welche which sorte sorten strain strains ist is mit with fur for grow mein meine my ich i habe have hab schon already
hochstens max maximal at most bis up to unter under within in zum mit dem der die das ja yes ok okay gerne gern lieber
mal bitte danke thanks wochen weeks tage days monate months
""".split())


def wish_only(folded: str) -> bool:
    """A reply made of wishes and filler words only ("und photoperiodisch?", "lieber für Anfänger", "höchstens 10
    Wochen"), nothing else."""
    rest = _numbers(folded)
    for pattern in (PHOTO, AUTO, BEGINNER, EXPERIENCED, EASY, SHORT, ANY, WEEKS, DAYS, MONTHS):
        rest = pattern.sub(" ", rest)
    return all(w in _REPLY_FILLER or w.isdigit() for w in rest.split())


def is_advice(folded: str) -> bool:
    return any(p.search(folded) for p in ADVICE) and not NOT_ADVICE.search(folded)


def _subject(folded: str) -> list[str] | None:
    for pattern in ABOUT:
        m = pattern.match(folded)
        if m is None:
            continue
        found = m.group("s").split()
        while found and found[-1] in _TRAIL:
            found = found[:-1]
        while found and found[0] in _ARTICLES:
            found = found[1:]
        return found or None
    return None


def _base(tokens: Iterable[str]) -> tuple[tuple[str, ...], bool]:
    """A name's words without its autoflower word, and whether it had one ("northern lights auto" -> ("northern",
    "lights"), True; "orion f1 automatic" -> ("orion", "f1"), True)."""
    out = [w for w in tokens if w not in AUTO_WORDS]
    return tuple(out), len(out) != len(list(tokens))


def _same(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Equal names, one typo allowed in one word of five letters or more ("Nothern Lights")."""
    if a == b:
        return True
    if len(a) != len(b) or not a:
        return False
    differ = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    return len(differ) == 1 and len(differ[0][0]) >= 5 and typo.osa(*differ[0], 1) <= 1


def _unique(found: Iterable[lexicon_mod.Strain]) -> tuple[lexicon_mod.Strain, ...]:
    return tuple({s.key: s for s in found}.values())


@dataclass(frozen=True)
class Named:
    strains: tuple[lexicon_mod.Strain, ...]
    asked: str  # the name as asked, for the answer ("Northern Lights Auto")
    name: str  # the name the matches share ("Northern Lights")
    auto: bool | None  # the question named an autoflower (True), or nothing about the type (None)
    exact: bool


def named(subject: list[str], lex: lexicon_mod.Lexicon) -> Named | None:
    """The strains a subject names: the exact name first, then every strain of that name without its autoflower word
    (the answer then says which types the lexicon has)."""
    tokens = tuple(w for w in " ".join(subject).replace("-", " ").split() if w)
    if not tokens:
        return None
    base, auto = _base(tokens)
    exact, by_base = [], []
    for strain in lex.strains:
        for name in strain.names:
            own = tuple(name.replace("-", " ").split())
            own_base, _ = _base(own)
            if _same(own, tokens) or (auto and own_base == base and _same(own_base, base) and strain.flowering == "auto"):
                exact.append(strain)
                break
            if base and _same(own_base, base):
                by_base.append(strain)
                break
    if exact:
        name = lex.text(exact[0].label, "de")
        # a name asked without a type also lists its autoflowers ("Purple Punch": the Auto is the one nearer the box)
        more = [s for s in by_base if s.flowering == "auto"] if not auto else []
        return Named(_unique(exact + more), name, name, True if auto else None, True)
    if by_base:
        name = re.sub(r"\s+(?:Auto(?:flower(?:ing)?)?|Automatic)$", "", lex.text(by_base[0].label, "de"))
        return Named(_unique(by_base), f"{name} Auto" if auto else name, name, True if auto else None,
                     False)
    return None


# ---------- the strain encyclopedia (kartei/varieties/popular.json, the wiki's reference varieties) ----------
# Read here, not through wiki.py: Hank tells its classification, parents and descendants with their source ids, never
# its THC figures or terpenes (the health gate's line, as for the lexicon).

ENCYCLOPEDIA = lexicon_mod.LIBRARY / "varieties" / "popular.json"
SELECTION_ID = "L0"  # the editorial list itself: a second voice beside each variety's own page
_ENC_CACHE: dict[str, tuple[int, Encyclopedia]] = {}


@dataclass(frozen=True)
class Variety:
    key: str
    label: str
    names: tuple[tuple[str, ...], ...]  # the label and the aliases, folded into words
    reference: Mapping[str, Any]
    sources: Mapping[str, Any]


@dataclass(frozen=True)
class Encyclopedia:
    varieties: tuple[Variety, ...] = ()
    package: str | None = None

    def visible(self, on: Iterable[str]) -> bool:
        return bool(self.varieties) and (self.package is None or self.package in set(on))

    def get(self, key: Any) -> Variety | None:
        return next((v for v in self.varieties if v.key == key), None)


def encyclopedia(path: Path | None = None) -> Encyclopedia:
    """The encyclopedia, read again when the file changed; empty when it is missing or broken (never raises)."""
    path = path or ENCYCLOPEDIA
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return Encyclopedia()
    cached = _ENC_CACHE.get(str(path))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        _LOGGER.warning("strain encyclopedia unreadable: %s", err)
        return Encyclopedia()
    table = data.get("sources") if isinstance(data.get("sources"), Mapping) else {}
    out = []
    for entry in data.get("entries") or []:
        ref = entry.get("reference") if isinstance(entry.get("reference"), Mapping) else {}
        label = str(entry.get("label_en") or entry.get("label") or "")
        if not entry.get("key") or not label:
            continue
        spelled = [label, str(entry.get("label") or ""), *(str(a) for a in ref.get("aliases") or [])]
        names = tuple(dict.fromkeys(tuple(fold(n).replace("-", " ").split()) for n in spelled if fold(n).strip()))
        out.append(Variety(str(entry["key"]), label, names, ref,
                           {sid: table[sid] for sid in entry.get("sources") or [] if sid in table}))
    found = Encyclopedia(tuple(out), data.get("package"))
    _ENC_CACHE[str(path)] = (stamp, found)
    return found


def variety_named(subject: Iterable[str], enc: Encyclopedia) -> Variety | None:
    """The variety a subject names (its label or an alias, without an autoflower word, one typo allowed)."""
    base, _ = _base(tuple(w for w in " ".join(subject).replace("-", " ").split() if w))
    if not base:
        return None
    for variety in enc.varieties:
        if any(_same(_base(name)[0], base) for name in variety.names):
            return variety
    return None


def names_strain(q: str, lex: lexicon_mod.Lexicon | None = None, enc: Encyclopedia | None = None) -> bool:
    """The question names a strain of the lexicon or the encyclopedia by a name of two words or more ("Purple Punch",
    "Gelato Auto"), whatever else it asks: for the safety gates, a known strain is cannabis (holdout h28, 01.11.2026:
    "where can I order Gelato Auto seeds?" took Gelato for another plant and passed the sale gate). A one-word name
    ("Gelato", "Critical") is an everyday word too often to count here."""
    return bool(strain_words(q, lex, enc))


def strain_words(q: str, lex: lexicon_mod.Lexicon | None = None, enc: Encyclopedia | None = None) -> frozenset[str]:
    """The folded words of the first strain name of two words or more the question names, or none: names_strain's
    test, and no typo question about them ("Purple" is no "Pumpe" in "Ist Purple Punch Indica?", 01.11.2026)."""
    lex = lex if lex is not None else lexicon_mod.load()
    enc = enc if enc is not None else encyclopedia()
    words = fold(q).replace("-", " ").split()
    for n in range(min(MAX_NAME_WORDS, len(words)), 1, -1):
        for i in range(len(words) - n + 1):
            window = words[i:i + n]
            if window[0] in AUTO_WORDS or window[0] in ASKING_WORDS or window[-1] in ASKING_WORDS - AUTO_WORDS:
                continue
            if named(window, lex) is not None or variety_named(window, enc) is not None:
                return frozenset(window)
    return frozenset()


def mentioned(folded: str, lex: lexicon_mod.Lexicon, enc: Encyclopedia) -> Named | Variety | None:
    """The strain a question names anywhere when all its other words ask for information (ASKING_WORDS): the longest
    name first, the lexicon before the encyclopedia."""
    words = folded.replace("-", " ").split()
    for n in range(min(MAX_NAME_WORDS, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            window, rest = words[i:i + n], words[:i] + words[i + n:]
            if any(w not in ASKING_WORDS for w in rest):
                continue
            if len(_base(window)[0]) < 2 and rest and not any(w in INFO_WORDS for w in rest):
                continue
            found = named(window, lex) or variety_named(window, enc)
            if found is not None:
                return found
    return None


# ---------- the route ----------

def _visible(lex: lexicon_mod.Lexicon) -> bool:
    return lex.visible(packages_on())


def _about_route(found: Named, lang: str, lex: lexicon_mod.Lexicon) -> Route:
    if not _visible(lex):
        return methods.package_off_route(lang, label=found.name, reason="package_off:strains")
    about = {"keys": [s.key for s in found.strains], "asked": found.asked, "name": found.name,
             "auto": found.auto, "exact": found.exact}
    return Route(ROUTE, None, None, None, "info", lang, f"strains:about:{found.strains[0].key}", about=about,
                 form="about")


def _variety_route(variety: Variety, lang: str, enc: Encyclopedia) -> Route:
    if not enc.visible(packages_on()):
        return methods.package_off_route(lang, label=variety.label, reason="package_off:strains")
    return Route(ROUTE, None, None, None, "info", lang, f"strains:variety:{variety.key}",
                 about={"variety": variety.key}, form="variety")


def route(q: str, lang: str, topic: Any = None, *, cannabis_context: bool = True,
          lex: lexicon_mod.Lexicon | None = None, tell_more: bool = False) -> Route | None:
    """The route of a strain question, or None for any other question. topic: the chat's last topic
    (asking.Topic; its advice holds the wishes and the open question). tell_more: the question is the wiki's
    "Erzähl mir mehr über <Titel>" (wikicards.subject): about the owner's own card wikicards answers."""
    lex = lex if lex is not None else lexicon_mod.load()
    if not lex.strains:
        return None
    lang = _lang(lang)
    folded = fold(q)
    kept = getattr(topic, "advice", None) if topic is not None else None
    state = Wishes.from_dict(kept) if isinstance(kept, Mapping) else None
    # the reply to Hank's own question (up to six words), or after the advice a reply of wishes alone ("und
    # photoperiodisch?"): "Ist es zu kalt für Autoflower?" names a type too, and is no reply
    if state is not None and len(folded.split()) <= 6 and not is_advice(folded) and _subject(folded) is None:
        new, said = wishes_of(q, lex, answering=state.ask)
        if said and (wish_only(folded) or (state.ask is not None and not ASIDE.search(folded)
                                            and not NOT_ADVICE.search(folded))):
            if not _visible(lex):
                return methods.package_off_route(lang, label=None, reason="package_off:strains")
            return _advice_route(merged(state, new), lang, reason="strains:reply")
    subject = _subject(folded)
    enc = encyclopedia()
    if subject is not None:
        found = named(subject, lex)
        if found is not None:
            if tell_more and any(s.own for s in found.strains):
                return None  # the owner's card: wikicards answers "Erzähl mir mehr über" from the card itself
            return _about_route(found, lang, lex)
        variety = variety_named(subject, enc)
        if variety is not None:
            return _variety_route(variety, lang, enc)
    if not is_advice(folded):
        hit = mentioned(folded, lex, enc)
        if isinstance(hit, Named):
            if tell_more and any(s.own for s in hit.strains):
                return None
            return _about_route(hit, lang, lex)
        if isinstance(hit, Variety):
            return _variety_route(hit, lang, enc)
        if (subject is not None and not tell_more
                and any(STRAIN_WORD.fullmatch(w) for w in folded.split() if w not in subject)
                and not all(w in ASKING_WORDS or STRAIN_WORD.fullmatch(w) for w in subject)):
            # "Was weißt du über die Sorte X?" with an X neither list knows: say so instead of "wrong guy". The strain
            # word stands before the name: "Erzähl mir mehr über Klonen (Cannabis und Kräuter)" is a wiki card's title
            if not _visible(lex):
                return methods.package_off_route(lang, label=None, reason="package_off:strains")
            return Route(ROUTE, None, None, None, "info", lang, "strains:unknown",
                         about={"asked": as_typed(q, subject)}, form="unknown")
    if not cannabis_context or not is_advice(folded):
        return None
    if not _visible(lex):
        return methods.package_off_route(lang, label=None, reason="package_off:strains")
    new, _ = wishes_of(q, lex)
    return _advice_route(merged(state, new) if state is not None else new, lang, reason="strains:advice")


def unknown_route(q: str, lang: str, lex: lexicon_mod.Lexicon | None = None) -> Route | None:
    """The honest no to an info question about a name nothing knows ("do we have information about Moonfrost
    Delight?", "Haben wir Infos zu Blue Zushi?"), or None for any other question. The router asks it only while a grow
    runs and after the search and the shelves found nothing (owner, 01.11.2026: "hank has to know whether we are
    currently growing or not"; both questions got "Da bin ich der Falsche, ich bin ein einfacher Farmer")."""
    subject = _subject(fold(q))
    if subject is None or all(w in ASKING_WORDS or STRAIN_WORD.fullmatch(w) for w in subject):
        return None
    if NOT_A_STRAIN.search(" ".join(subject)):
        return None
    lex = lex if lex is not None else lexicon_mod.load()
    if not lex.strains or not _visible(lex):
        return None
    return Route(ROUTE, None, None, None, "info", _lang(lang), "strains:unknown_any",
                 about={"asked": as_typed(q, subject), "any": True}, form="unknown")


def _advice_route(w: Wishes, lang: str, *, reason: str) -> Route:
    ask = next_question(w)
    if ask is not None:
        w = replace(w, asked=[*w.asked, ask], ask=ask)
        return Route(ROUTE, None, None, None, "info", lang, f"{reason};ask:{ask}", about=w.as_dict(), form="ask")
    return Route(ROUTE, None, None, None, "info", lang, reason, about=w.as_dict(), form="advice")


# ---------- the ranking ----------

def rank(lex: lexicon_mod.Lexicon, w: Wishes, picks: int = PICKS) -> tuple[list[lexicon_mod.Strain], dict[str, Any]]:
    """The suggestions in order, and what the answer says about how they were found ({"bloom_only": True} when a
    longest time could only be held against the flowering time)."""
    box = lex.box_cm
    pool = [s for s in lex.strains if s.fit(box) != "tall"]
    if w.flowering in ("auto", "photoperiod"):
        pool = [s for s in pool if s.flowering == w.flowering]
    if w.brand:
        pool = [s for s in pool if s.brand == w.brand]
    info: dict[str, Any] = {}
    if w.max_days:
        by_harvest = [s for s in pool if s.harvest is not None and (s.harvest[1] or s.harvest[0] or 0) <= w.max_days]
        if by_harvest:
            pool = by_harvest
        else:  # only strains whose maker gives no seed to harvest time: a given one that is too long rules it out
            pool = [s for s in pool if s.harvest is None and s.bloom_days is not None
                    and (s.bloom_days[1] or s.bloom_days[0] or 0) <= w.max_days]
            info["bloom_only"] = True
    timed = w.short or bool(w.max_days)
    if w.max_days:
        basis = "bloom" if info.get("bloom_only") else "harvest"
    else:
        basis = w.short_on or "bloom"
    if timed:
        info["basis"] = basis

    def key(s: lexicon_mod.Strain) -> tuple[Any, ...]:
        easy = 0 if s.difficulty == "easy" else 1
        time = s.time_days(basis) if timed else None
        wish: list[Any] = []
        if w.level in ("beginner", "easy"):
            wish.append(easy)
        if timed:
            wish.append(time if time is not None else 10 ** 4)
        complete = -sum(x is not None for x in (s.height, s.bloom or s.harvest, s.difficulty))
        return (lexicon_mod.FITS.index(s.fit(box)), *wish, easy, complete, lexicon_mod.fold(s.label["de"]),
                lexicon_mod.fold(s.brand))

    ordered = sorted(pool, key=key)
    firm = [s for s in ordered if s.fit(box) in ("sure", "lst")]
    unclear = [s for s in ordered if s.fit(box) == "unknown"]
    out: list[lexicon_mod.Strain] = []
    seen: set[str] = set()
    for s in firm + unclear:
        name = lexicon_mod.fold(s.label["de"])
        if name in seen:
            continue
        seen.add(name)
        out.append(s)
        if len(out) >= picks:
            break
    return out, info


# ---------- the answer ----------

def _brand_text(brand: str, lang: str) -> str:
    names = (claims.policy().get("vendor_names") or {}).get("names") or {}
    if any(lexicon_mod.fold(n) == lexicon_mod.fold(brand) for n in names):
        return t("hank.strains.house_brand", lang)
    return brand


def _height(s: lexicon_mod.Strain, lang: str) -> str:
    return lexicon_mod.span_text(s.height, "cm", lang) or t("hank.strains.height.none", lang)


def _time(s: lexicon_mod.Strain, lang: str) -> str:
    if s.harvest is not None:
        return t("hank.strains.time.harvest", lang, text=lexicon_mod.span_text(s.harvest, "days", lang))
    if s.bloom is not None:
        return t("hank.strains.time.bloom", lang, text=lexicon_mod.span_text(s.bloom, s.bloom[2], lang))
    return t("hank.strains.time.none", lang)


def _time_on(s: lexicon_mod.Strain, basis: str | None, lang: str) -> str:
    """The time a ranking compared: the flowering time (bloom) or seed to harvest (harvest)."""
    if basis == "bloom" and s.bloom is not None:
        return t("hank.strains.time.bloom", lang, text=lexicon_mod.span_text(s.bloom, s.bloom[2], lang))
    if basis == "harvest" and s.harvest is not None:
        return t("hank.strains.time.harvest", lang, text=lexicon_mod.span_text(s.harvest, "days", lang))
    return _time(s, lang)


def _ids(s: lexicon_mod.Strain) -> list[str]:
    """The source ids behind the numbers a line names: the height, the time, the difficulty (else the type)."""
    facts = s.facts.get("de") or {}
    wanted = ("h_in", "seed_harvest" if s.harvest is not None else "bloom", "difficulty", "type", "form")
    ids: list[str] = []
    for k in wanted:
        ids += [sid for sid in lexicon_mod.source_ids(facts.get(k, "")) if sid not in ids]
    if not ids:  # the owner's card: its facts are keyed by their German labels
        for value in facts.values():
            ids += [sid for sid in lexicon_mod.source_ids(value) if sid in s.sources and sid not in ids]
    return ids


def _joined(parts: list[str], lang: str) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + f" {t('hank.strains.and', lang)} " + parts[-1]


def _why(s: lexicon_mod.Strain, w: Wishes, box: int | None, lang: str, info: Mapping[str, Any]) -> str:
    fit = s.fit(box)
    lo, hi = s.height or (None, None)
    reasons = []
    if fit == "sure":
        reasons.append(t("hank.strains.why.sure", lang))
    elif fit == "lst":
        reasons.append(t("hank.strains.why.lst", lang, lo=lo, cm=box))
    elif s.height is not None:
        reasons.append(t("hank.strains.why.unknown", lang, hi=hi))
    else:
        reasons.append(t("hank.strains.why.unknown_none", lang))
    if w.flowering == "auto":
        reasons.append(t("hank.strains.why.auto", lang))
    elif w.flowering == "photoperiod":
        reasons.append(t("hank.strains.why.photo", lang))
    if w.level in ("beginner", "easy") and s.difficulty == "easy":
        reasons.append(t("hank.strains.why.easy", lang))
    if w.max_days:
        weeks = round(w.max_days / 7)
        if info.get("bloom_only"):
            reasons.append(t("hank.strains.why.bloom_only", lang, time=lexicon_mod.span_text(s.bloom, s.bloom[2], lang)
                             if s.bloom else "", weeks=weeks))
        else:
            reasons.append(t("hank.strains.why.max", lang, time=_time(s, lang), weeks=weeks))
    elif w.short and s.time_days(info.get("basis")) is not None:
        reasons.append(t("hank.strains.why.short", lang, time=_time_on(s, info.get("basis"), lang)))
    if w.brand:
        reasons.append(t("hank.strains.why.brand", lang, brand=_brand_text(w.brand, lang)))
    if s.own:
        reasons.append(t("hank.strains.why.own", lang))
    return _joined(reasons, lang)


def wish_text(w: Wishes, lang: str) -> str:
    parts = []
    if w.flowering == "auto":
        parts.append(t("hank.strains.wish.auto", lang))
    elif w.flowering == "photoperiod":
        parts.append(t("hank.strains.wish.photo", lang))
    if w.level == "beginner":
        parts.append(t("hank.strains.wish.beginner", lang))
    elif w.level == "easy":
        parts.append(t("hank.strains.wish.easy", lang))
    elif w.level == "experienced":
        parts.append(t("hank.strains.wish.experienced", lang))
    if w.max_days:
        parts.append(t("hank.strains.wish.max", lang, weeks=round(w.max_days / 7)))
    elif w.short:
        parts.append(t("hank.strains.wish.short_harvest" if w.short_on == "harvest" else "hank.strains.wish.short", lang))
    if w.brand:
        parts.append(t("hank.strains.wish.brand", lang, brand=_brand_text(w.brand, lang)))
    return ", ".join(parts) if parts else t("hank.strains.wish.none", lang)


def _box_id(lex: lexicon_mod.Lexicon) -> str:
    return lex.box_source[0] if lex.box_source is not None else ""


def _table(lex: lexicon_mod.Lexicon, strains: Iterable[lexicon_mod.Strain]) -> dict[str, Any]:
    """The sources an answer may cite: each strain's own, and the box's."""
    table: dict[str, Any] = {}
    for s in strains:
        table.update(s.sources)
    if lex.box_source is not None:
        table.setdefault(*lex.box_source)
    return table


def _closing(lex: lexicon_mod.Lexicon, lang: str, *, choice: bool = True) -> list[str]:
    lines = [t("hank.strains.note", lang)]
    if choice:
        lines.append(t("hank.strains.choice", lang))
    lines.append(t("hank.strains.more", lang, n=len(lex.strains)))
    if lex.status != "approved":
        lines.append(t("hank.strains.draft", lang))
    return lines


def _answer(answer_id: str, lang: str, lines: list[str], lex: lexicon_mod.Lexicon,
            strains: Iterable[lexicon_mod.Strain], index: Any, *, chips: list[str] | None = None,
            link: bool | dict[str, str] = True, extra: Mapping[str, Any] | None = None) -> Answer:
    lines = [claims.vendors(line, lang) for line in lines if line]
    table = {**_table(lex, strains), **(extra or {})}
    ids = methods.cited(lines, table)
    # the encyclopedia's ids carry the source's own number ("L261615"), longer than the card ids cited() reads
    ids += [sid for sid in extra or {} if sid not in ids
            and any(re.search(rf"(?<![\w]){re.escape(sid)}(?![\w])", line) for line in lines)]
    sources = methods.resolve(index, table, ids, lang)
    if link is True:
        link = {"href": WIKI_LINK, "label": t("hank.strains.link", lang)}
    return Answer(id=answer_id, route=ROUTE, lang=lang, text="\n".join(lines), sources=sources,
                  chips=list(chips or []), link=link or None)


def ask_answer(w: Wishes, lang: str, *, answer_id: str) -> Answer:
    if w.ask == "flowering":
        chips = [t("hank.strains.chip.auto", lang), t("hank.strains.chip.photo", lang), t("hank.strains.chip.any", lang)]
        return Answer(id=answer_id, route=ROUTE, lang=lang, text=t("hank.strains.ask.flowering", lang), chips=chips)
    chips = [t("hank.strains.chip.first", lang), t("hank.strains.chip.experienced", lang),
             t("hank.strains.chip.weeks", lang)]
    return Answer(id=answer_id, route=ROUTE, lang=lang, text=t("hank.strains.ask.level", lang), chips=chips)


def advice_answer(w: Wishes, lang: str, *, answer_id: str, lex: lexicon_mod.Lexicon, index: Any = None) -> Answer:
    picks, info = rank(lex, w)
    box = lex.box_cm
    wish = wish_text(w, lang)
    if not picks:
        lines = [t("hank.strains.none", lang, wish=wish), *_closing(lex, lang)]
        return _answer(answer_id, lang, lines, lex, [], index)
    lines = [t(f"hank.strains.head.{len(picks)}", lang, cm=box, id=_box_id(lex), wish=wish)]
    for n, s in enumerate(picks, 1):
        diff = t(f"hank.strains.difficulty.{s.difficulty}", lang) if s.difficulty else \
            t("hank.strains.difficulty.none", lang)
        lines.append(t("hank.strains.pick", lang, n=n, label=lex.text(s.label, lang), brand=_brand_text(s.brand, lang),
                       height=_height(s, lang), time=_time(s, lang), difficulty=diff,
                       why=_why(s, w, box, lang, info), ids=", ".join(_ids(s))))
    if len(picks) < PICKS:
        lines.append(t("hank.strains.fewer", lang))
    if any(s.fit(box) == "lst" for s in picks):
        lines.append(t("hank.strains.lst", lang))
    lines += _closing(lex, lang)
    return _answer(answer_id, lang, lines, lex, picks, index)


def _type(s: lexicon_mod.Strain, lang: str) -> str:
    key = {"auto": "auto", "photoperiod": "photo"}.get(s.flowering or "", "none")
    return t(f"hank.strains.type.{key}", lang)


def _geno(s: lexicon_mod.Strain, lang: str) -> str | None:
    """The larger share the breeder gives ("90 % Indica"), or None without one."""
    indica, sativa = (s.genotype or {}).get("indica"), (s.genotype or {}).get("sativa")
    if indica is None and sativa is None:
        return None
    if indica is not None and sativa is not None and indica == sativa:
        return t("hank.strains.geno.even", lang, p=indica)
    if sativa is None or (indica is not None and indica > sativa):
        return t("hank.strains.geno.indica", lang, p=indica)
    return t("hank.strains.geno.sativa", lang, p=sativa)


def _kind(s: lexicon_mod.Strain, lang: str) -> str:
    """The type and the share: "photoperiodisch, 90 % Indica" (owner, 01.11.2026: the list of three Purple Punch named
    no share, and the encyclopedia's "Hybrid" stood alone against the breeders' 90 and 80 % Indica)."""
    geno = _geno(s, lang)
    return f"{_type(s, lang)}, {geno}" if geno else _type(s, lang)


def _fit_line(s: lexicon_mod.Strain, lex: lexicon_mod.Lexicon, lang: str) -> str:
    fit = s.fit(lex.box_cm)
    if fit == "unknown" and s.height is None:
        return t("hank.strains.fit.unknown_none", lang, cm=lex.box_cm, id=_box_id(lex))
    return t(f"hank.strains.fit.{fit}", lang, h=_height(s, lang), cm=lex.box_cm, id=_box_id(lex))


def about_answer(about: Mapping[str, Any], lang: str, *, answer_id: str, lex: lexicon_mod.Lexicon,
                 index: Any = None) -> Answer:
    found = [s for s in (lex.get(k) for k in about.get("keys") or []) if s is not None]
    if not found:  # the lexicon changed between routing and answering
        return Answer(id=answer_id, route="no_data", lang=lang, text=t("hank.nodata.plain", lang), gap=True)
    if len(found) == 1 and about.get("exact"):
        s = found[0]
        head = t("hank.strains.about_own", lang, label=lex.text(s.label, lang)) if s.own else \
            t("hank.strains.about_head", lang, label=lex.text(s.label, lang), brand=_brand_text(s.brand, lang))
        lines = [head]
        lines += [t("hank.strains.about_fact", lang, label=label, text=value)
                  for _, label, value in lexicon_mod.ordered_facts(s, lang)]
        lines.append(_fit_line(s, lex, lang))
        extra = None if s.own else _add_also(lines, lex.text(s.label, "de"), lang)
        lines += _closing(lex, lang, choice=False)
        return _answer(answer_id, lang, lines, lex, [s], index, extra=extra)
    autos = [s for s in found if s.flowering == "auto"]
    name, asked = str(about.get("name") or ""), str(about.get("asked") or "")
    if about.get("auto") and not autos:
        lines = [t("hank.strains.about_not_auto", lang, asked=asked, name=name)]
    else:
        lines = [t("hank.strains.about_many", lang, n=len(found), name=name)]
    shown = found[:ABOUT_MAX]
    for n, s in enumerate(shown, 1):
        lines.append(t("hank.strains.about_line", lang, n=n, label=lex.text(s.label, lang),
                       brand=_brand_text(s.brand, lang), type=_kind(s, lang), time=_time(s, lang),
                       height=_height(s, lang), fit=t(f"hank.strains.fitword.{s.fit(lex.box_cm)}", lang),
                       ids=", ".join(_ids(s))))
    if len(found) > len(shown):
        lines.append(t("hank.strains.about_rest", lang, n=len(found) - len(shown)))
    if lex.box_source is not None and lex.box_cm:
        lines.append(t("hank.strains.box", lang, cm=lex.box_cm, id=_box_id(lex)))
    extra = _add_also(lines, name, lang)
    lines += _closing(lex, lang, choice=False)
    return _answer(answer_id, lang, lines, lex, shown, index, extra=extra)


def _add_also(lines: list[str], name: str, lang: str) -> Mapping[str, Any] | None:
    """The encyclopedia's line about the same name under a lexicon answer; its sources, or None. Never under an
    autoflower: "Purple Punch Auto" is another cross than the Purple Punch the encyclopedia describes."""
    words = fold(name).split()
    if not words or any(w in AUTO_WORDS for w in words):
        return None
    variety = variety_named(words, encyclopedia())
    also = _also_line(variety, lang) if variety is not None else None
    if not also:
        return None
    lines.append(also)
    return variety.sources


def _same_value(a: Any, b: Any) -> bool:
    """Equal values, spelling aside ("Dosidos" and "Do-Si-Dos" are one parent)."""
    def plain(x: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", fold(str(x)))
    if isinstance(a, list) and isinstance(b, list):
        return sorted(plain(x) for x in a) == sorted(plain(x) for x in b)
    return plain(a) == plain(b)


def _evidence(ref: Mapping[str, Any], name: str) -> tuple[list[str], list[tuple[str, Any]]]:
    """The source ids that give a field the variety's value (its own page before the editorial list), and the
    other values other sources give."""
    value = ref.get(name)
    items = [e for e in (ref.get("evidence") or {}).get(name) or [] if isinstance(e, Mapping) and e.get("source_id")]
    agree = [str(e["source_id"]) for e in items if _same_value(e.get("value"), value)]
    own = [sid for sid in agree if sid != SELECTION_ID]
    other = [(str(e["source_id"]), e.get("value")) for e in items if not _same_value(e.get("value"), value)]
    return own or agree, other


def _class_text(value: Any) -> str:
    return str(value or "").strip().capitalize()


def _also_line(variety: Variety, lang: str) -> str | None:
    """One line of the encyclopedia under a lexicon answer: classification and parents, when both are cited."""
    ref = variety.reference
    ids_c, other_c = _evidence(ref, "classification")
    ids_p, _ = _evidence(ref, "parents")
    if not ref.get("classification") or not ref.get("parents") or not ids_c or not ids_p:
        return None
    parents = " x ".join(str(p) for p in ref["parents"])
    other = [(sid, v) for sid, v in other_c if isinstance(v, str) and v.strip()]
    if other:  # Purple Punch: Leafly says hybrid, the selection list indica; both stand, neither is dropped
        return t("hank.strains.variety_also_split", lang, value=_class_text(ref["classification"]),
                 ids_c=", ".join(ids_c), other=" / ".join(dict.fromkeys(_class_text(v) for _, v in other)),
                 ids_o=", ".join(dict.fromkeys(sid for sid, _ in other)), parents=parents, ids_p=", ".join(ids_p))
    return t("hank.strains.variety_also", lang, value=_class_text(ref["classification"]),
             parents=parents, ids=", ".join(dict.fromkeys(ids_c + ids_p)))


GROW_LEVEL = {"easy": "hank.strains.level.easy", "moderate": "hank.strains.level.moderate",
              "difficult": "hank.strains.level.difficult", "short": "hank.strains.level.short",
              "average": "hank.strains.level.average", "tall": "hank.strains.level.tall",
              "low": "hank.strains.level.low", "medium": "hank.strains.level.medium", "high": "hank.strains.level.high"}
GROW_FACT = {"difficulty": "hank.strains.grow.difficulty", "height": "hank.strains.grow.height",
             "yield": "hank.strains.grow.yield"}
VERDICT = {"good": "hank.strains.verdict.good", "fine": "hank.strains.verdict.fine",
           "catch": "hank.strains.verdict.catch", "later": "hank.strains.verdict.later",
           "partial": "hank.strains.verdict.partial", "partial_fine": "hank.strains.verdict.partial_fine",
           "partial_catch": "hank.strains.verdict.partial_catch"}


def _grow_lines(ref: Mapping[str, Any], lang: str) -> list[str]:
    """The source's grow facts and the wiki fact sheet's verdict (wiki.grow_view: the same codes the page words), or
    nothing without them (owner, 01.11.2026: "whether this is something for them to go for next time or not")."""
    from .. import wiki

    grow = wiki.grow_view(ref.get("grow"), ref.get("seed_facts"), wiki._box_cm())
    if not grow or not grow["source_ids"]:
        return []
    came = grow["from"]

    def span(o: Mapping[str, Any], figures: list[int], keys: tuple[str, str, str]) -> str:
        """"up to 900", "63", "60 to 75": keys for the range, one figure, up to."""
        lo, hi = figures
        if o.get("upto"):
            return t(keys[2], lang, hi=hi)
        return t(keys[1], lang, n=hi) if lo == hi else t(keys[0], lang, lo=lo, hi=hi)

    facts = []
    for f in ("difficulty", "height", "yield"):
        if not grow[f]:
            continue
        o = came.get(f, {})
        if "cm" in o:  # a maker's centimetres say more than the step (the box is 92 cm)
            facts.append(span(o, o["cm"], ("hank.strains.grow.height_cm", "hank.strains.grow.height_cm_one",
                                           "hank.strains.grow.height_cm_upto")))
        elif "g_m2" in o:
            facts.append(span(o, o["g_m2"], ("hank.strains.grow.yield_g", "hank.strains.grow.yield_g_one",
                                             "hank.strains.grow.yield_g_upto")))
        else:
            facts.append(t(GROW_FACT[f], lang, value=t(GROW_LEVEL[grow[f]], lang)))
    if "days" in came.get("flowering_days", {}):
        days = came["flowering_days"]["days"]
        facts.append(span({}, days, ("hank.strains.grow.days_span", "hank.strains.grow.days_one",
                                     "hank.strains.grow.days_one")))
    elif grow["weeks"]:
        facts.append(t("hank.strains.grow.days", lang, weeks=grow["weeks"]))
    return [t("hank.strains.variety_grow", lang, facts=", ".join(facts), ids=", ".join(grow["source_ids"])),
            t("hank.strains.variety_verdict", lang, verdict=t(VERDICT[grow["verdict"]], lang))]


def variety_answer(about: Mapping[str, Any], lang: str, *, answer_id: str, lex: lexicon_mod.Lexicon,
                   index: Any = None) -> Answer:
    """What the encyclopedia says about a variety the lexicon does not have: classification, parents, what was bred
    from it, the source's grow facts and the fact sheet's verdict, each with its source ids; that its height is not
    given in centimetres, or not at all; never THC or terpenes."""
    variety = encyclopedia().get(about.get("variety"))
    if variety is None:  # the encyclopedia changed between routing and answering
        return Answer(id=answer_id, route="no_data", lang=lang, text=t("hank.nodata.plain", lang), gap=True)
    ref = variety.reference
    lines = [t("hank.strains.variety_head", lang, label=variety.label)]
    ids, other = _evidence(ref, "classification")
    if ref.get("classification") and ids:
        lines.append(t("hank.strains.variety_class", lang, value=_class_text(ref["classification"]),
                       ids=", ".join(ids)))
        for sid, value in other:
            if isinstance(value, str) and value.strip():
                lines.append(t("hank.strains.variety_other_class", lang, value=_class_text(value), ids=sid))
    ids, other = _evidence(ref, "parents")
    if ref.get("parents") and ids:
        lines.append(t("hank.strains.variety_parents", lang, value=" x ".join(str(p) for p in ref["parents"]),
                       ids=", ".join(ids)))
        for sid, value in other:
            if isinstance(value, list) and value:
                lines.append(t("hank.strains.variety_other", lang, value=" x ".join(str(p) for p in value), ids=sid))
    ids, _ = _evidence(ref, "descendants")
    if ref.get("descendants") and ids:
        lines.append(t("hank.strains.variety_children", lang, value=", ".join(str(d) for d in ref["descendants"]),
                       ids=", ".join(ids)))
    grown = _grow_lines(ref, lang)
    lines.extend(grown)
    from .. import wiki

    grow = wiki.grow_view(ref.get("grow"), ref.get("seed_facts"), wiki._box_cm())
    box = wiki._box_cm()
    if grow and "cm" in grow["from"].get("height", {}) and box:
        lines.append(t("hank.strains.variety_box", lang, box=box))
    else:  # Leafly gives a height step, never centimetres: whether it fits the box stays open
        lines.append(t("hank.strains.variety_nocm" if grown else "hank.strains.variety_nogrow", lang,
                       label=variety.label))
    link = {"href": f"#wiki/varieties/{variety.key}", "label": t("hank.strains.variety_link", lang)}
    return _answer(answer_id, lang, lines, lex, [], index, link=link, extra=variety.sources)


def answer(route_: Route, live: Any = None, *, answer_id: str, index: Any = None,
           lex: lexicon_mod.Lexicon | None = None) -> Answer:
    """The answer of a strains route: Hank's question, the advice, what the lexicon or the encyclopedia says about a
    strain, or that neither has it."""
    lex = lex if lex is not None else lexicon_mod.load()
    lang = _lang(route_.lang)
    if not lex.visible(packages_on()):
        return Answer(id=answer_id, route="package_off", lang=lang, text=methods.package_off_text(lang, None))
    if route_.form == "about":
        return about_answer(route_.about or {}, lang, answer_id=answer_id, lex=lex, index=index)
    if route_.form == "variety":
        return variety_answer(route_.about or {}, lang, answer_id=answer_id, lex=lex, index=index)
    if route_.form == "unknown":
        key = "hank.strains.unknown_any" if (route_.about or {}).get("any") else "hank.strains.unknown"
        text_ = t(key, lang, name=str((route_.about or {}).get("asked") or ""),
                  n=len(lex.strains), m=len(encyclopedia().varieties))
        return Answer(id=answer_id, route=ROUTE, lang=lang, text=text_, gap=True,
                      link={"href": WIKI_LINK, "label": t("hank.strains.link", lang)})
    w = Wishes.from_dict(route_.about)
    if route_.form == "ask":
        return ask_answer(w, lang, answer_id=answer_id)
    return advice_answer(w, lang, answer_id=answer_id, lex=lex, index=index)


def topic_of(route_: Route, lex: lexicon_mod.Lexicon | None = None) -> dict[str, Any] | None:
    """What the chat keeps of a strains answer (asking.Topic fields): the wishes and the open question, or the strain
    an about answer named."""
    if route_.name != ROUTE:
        return None
    if route_.form == "about":
        about = route_.about or {}
        lex = lex if lex is not None else lexicon_mod.load()
        first = lex.get((about.get("keys") or [None])[0])
        label = {lang: lex.text(first.label, lang) for lang in ("de", "en")} if first is not None else {}
        return {"name": f"strains/{first.key}" if first is not None else None, "label": label, "advice": None}
    if route_.form in ("variety", "unknown"):
        return {"name": None, "label": {}, "advice": None}
    return {"name": None, "label": {"de": "Sorte", "en": "strain"}, "advice": dict(route_.about or {})}


__all__ = ["ABOUT", "ADVICE", "DE", "EN", "ROUTE", "STRENGTH", "Wishes", "about_answer", "advice_answer", "answer",
           "as_typed", "check", "encyclopedia", "is_advice", "mentioned", "named", "names_strain", "next_question",
           "rank", "register", "route", "strain_words", "topic_of", "unknown_route", "variety_answer", "variety_named",
           "wish_text", "wishes_of"]
