"""Hank and the feeding plan v2 (spec 4.7 to 4.10 of the VIVOSUN design, owner 23.10.2026): units, the plant's age,
"wann dünge ich was", the fertilizer choice. Pure code: the texts below are final, no model phrases them.

1. Units. A feeding answer speaks in the ACTIVE card's unit (feeding.view amount_unit and amount_digits): grams with
   half spoons for VIVOSUN Base A/B, ml for BioBizz. Never a hard-coded "ml". amount_items() is the one place that
   writes "Base A 0,75 g (1,5 Löffel)"; context.py and today.py call it.
2. A stated age of the PLANT ("meine Pflanze ist 5 Wochen alt", "she is 5 weeks old", "seit dem 18.09."), never of
   water, seeds or the nutrient solution (age_claim). It is compared with the start that counts (plant.age_view,
   plant.age_check). When it differs, or no start is known, the answer carries ONE two-tap card that stores the start
   under STATE_DIR through /api/cmd plant_start (proposals.py kind plant_start), exactly as the plant card does. A
   plant week ("Pflanzenwoche 6") is a whole week: no card, the answer asks for the day. The age never changes a dose.
3. "Wann dünge ich was?" / "What do I feed when?" (asks_feed_plan): the dated plan from today on for the active card,
   from feeding.view()["timeline"]: this week's amount, the next change with its date and new amount, up to three
   changes after it, the week the table ends. In draft and needs_stage only the dates and the reason, no amount.
4. "Welchen Dünger soll ich nehmen?" (asks_feed_choice): the fertilizers plant.json holds, that only one counts at a
   time and that the owner picks it in the feeding card. Hank recommends none: no card says which one is better.
5. The today list (today.py): the age question once per stored value (asking.asked_before "start_date"), then a short
   reminder; the fertilizer choice while none is made.

Texts: the catalog below, in German and English, registered into i18n like asking.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from vgrow import i18n, plant
from vgrow.i18n import fmt_num, fmt_qty, t

from . import asking, proposals
from .livestate import LiveState, collect, day_text
from .types import Answer, Proposal, Route

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
UNITS = ("ml", "g")  # the amount units feeding.view hands out (feeding.UNITS)
MAX_CHANGES = 4  # the next change plus up to three after it; the rest stands on the page (spec 4.9)
MAX_WEEKS = 52  # the plant card's bound: an age in whole weeks, 0 to 52
MAX_DAYS = plant.MAX_AGE_DAYS
FUTURE_DAYS = 60  # a day and month without a year up to this far ahead is a future start, not last year's

DE: dict[str, str] = {
    # units
    "hank.feed.spoon_1": "1 Löffel",
    "hank.feed.spoon_n": "{n} Löffel",
    "hank.feed.item": "{label} {value}",
    "hank.feed.item_spoons": "{label} {value} ({spoons})",
    "hank.feed.and": "{a} und {b}",
    # the fertilizer choice
    "hank.feed.choice_needed": "Welchen Dünger gibst du: {options}? Es gilt immer nur einer. Wähle ihn in der "
                               "Düngerkarte, bis dahin rechnet das Cockpit keine Mengen.",
    "hank.feed.choice_fact": "Zwei Dünger sind eingetragen, {options}, und es gilt immer nur einer: welchen du gibst, "
                             "wählst du in der Düngerkarte, bis dahin rechnet das Cockpit keine Mengen.",
    "hank.feed.choice_active": "Gerade gilt {active}. Es gilt immer nur ein Dünger; wechseln kannst du in der "
                               "Düngerkarte, mit zwei Tipps.",
    "hank.feed.choice_none": "Hier ist nur ein Dünger eingetragen: {active}.",
    "hank.feed.active_fact": "Gerade gilt {active}, und es gilt immer nur ein Dünger: wechseln kannst du in der "
                             "Düngerkarte, mit zwei Tipps.",
    "hank.feed.choice_why": "Welcher besser ist, sagt keine meiner Karten: das entscheidest du.",
    "hank.feed.choice_more": "Dazu kannst du einen der {n} Dünger aus dem Wiki wählen.",
    # the plan
    "hank.plan.today": "Heute ({date}, {week}{age}): je Liter {items}.",
    "hank.plan.age": ", Pflanze {age} alt",
    "hank.plan.age_approx": ", Pflanze etwa {age} alt",
    "hank.plan.change": "Ab {date} ({week}): {items}.",
    "hank.plan.change_latest": "Spätestens ab {date} ({week}): {items}.",
    "hank.plan.change_item": "{label} {to} statt {from}",
    "hank.plan.change_none": "{label} keine Menge mehr",
    "hank.plan.change_undated": "Die nächste Änderung kommt mit {week}; ein Datum gibt es erst mit dem Blütebeginn.",
    "hank.plan.more": "Weitere Wochen stehen auf der Seite.",
    "hank.plan.label_end": "Ab {date} ({week}) nennt die Tabelle keine Menge mehr: das Cockpit rechnet keine, das "
                           "entscheidest du.",
    "hank.plan.label_end_latest": "Spätestens ab {date} ({week}) nennt die Tabelle keine Menge mehr: das Cockpit "
                                  "rechnet keine, das entscheidest du.",
    "hank.plan.water": "Ab {date} ({week}) nur Wasser.",
    "hank.plan.water_latest": "Spätestens ab {date} ({week}) nur Wasser.",
    "hank.plan.harvest": "Ab {date} ({week}) Ernte, kein Dünger mehr.",
    "hank.plan.harvest_latest": "Spätestens ab {date} ({week}) Ernte, kein Dünger mehr.",
    "hank.plan.factor": "Faktor {factor} ist eingerechnet, nie mehr als die Tabelle.",
    "hank.plan.frequency": "Wie oft du düngst, sagt die Tabelle nicht.",
    "hank.plan.spoons": "Die Löffel sind auf halbe abgerundet; unter einem halben Löffel hilft nur die Waage.",
    "hank.plan.draft": "Die Düngerkarte ({card}) ist noch nicht bestätigt: bis dahin nenne ich keine Mengen, nur "
                       "die Daten. Bestätige sie in der Düngerkarte.",
    "hank.plan.needs_stage": "Die Woche ist nur geschätzt: keine Mengen, bis du die Phase bestätigst. Die Daten "
                             "gelten spätestens.",
    "hank.plan.week_now": "Diese Woche ({span}): {week}.",
    "hank.plan.week_now_latest": "Diese Woche (spätestens {span}): {week}.",
    "hank.plan.week_undated": "Diese Woche: {week}.",
    "hank.plan.span": "{from} bis {to}",
    "hank.plan.next_date": "Die Menge ändert sich ab {date} ({week}).",
    "hank.plan.next_date_latest": "Die Menge ändert sich spätestens ab {date} ({week}).",
    "hank.plan.water_now": "{week}, diese Woche kein Dünger.",
    "hank.plan.harvest_now": "{week}. Kein Dünger mehr.",
    "hank.plan.label_end_now": "Nach Blütewoche {week} nennt die Tabelle keine Menge mehr: das Cockpit rechnet "
                               "keine, das entscheidest du.",
    "hank.plan.no_card": "Einen Düngerplan kann ich gerade nicht zeigen.",
    # the plant's age
    "hank.age.consistent": "Passt: nach dem Start am {stored} ist sie {age} alt (Tag {days}, Pflanzenwoche {life}). "
                           "Nichts zu ändern.",
    "hank.age.conflict": "Das passt nicht zu meinem Datum. Als Start steht der {stored} ({by}); danach ist sie {age} "
                         "alt (Tag {days}). Bei {claim} wäre der Start der {implied}.",
    "hank.age.unknown": "Danke. Einen Starttag kenne ich noch nicht. Bei {claim} wäre es der {implied}.",
    "hank.age.unknown_date": "Danke. Einen Starttag kenne ich noch nicht. Mit dem Start am {implied} ist sie {age} "
                             "alt.",
    "hank.age.conflict_date": "Das passt nicht zu meinem Datum. Als Start steht der {stored} ({by}); danach ist sie "
                              "{age} alt (Tag {days}). Mit deinem Start am {implied} wäre sie {claim_age} alt.",
    "hank.age.doses": "Die Düngermengen ändern sich dadurch nicht: sie hängen an deiner Phase und am Blütebeginn.",
    "hank.age.card": "Übernehmen kannst du ihn mit zwei Tipps auf der Karte.",
    "hank.age.week_conflict": "Du sagst Pflanzenwoche {n}. Nach meinem Start am {stored} ist sie in Pflanzenwoche "
                              "{life}. Welcher Tag war der Start? Trag ihn in der Pflanzenkarte ein.",
    "hank.age.week_consistent": "Passt: nach dem Start am {stored} ist sie in Pflanzenwoche {life}. Nichts zu "
                                "ändern.",
    "hank.age.week_unknown": "Pflanzenwoche {n} ist eine ganze Woche, kein Tag. Welcher Tag war der Start? Trag "
                             "ihn in der Pflanzenkarte ein.",
    "hank.age.future": "Ein Start in der Zukunft geht nicht. Trag den Starttag in der Pflanzenkarte ein.",
    "hank.age.too_old": "Das wäre mehr als {days} Tage her. Trag den Starttag in der Pflanzenkarte ein.",
    "hank.age.too_many_weeks": "Mehr als {weeks} Wochen nimmt die Pflanzenkarte nicht in Wochen an. Trag den "
                               "Starttag in der Pflanzenkarte ein.",
    "hank.age.claim_weeks": "{n} Wochen",
    "hank.age.claim_week": "1 Woche",
    "hank.age.claim_days": "{n} Tagen",
    "hank.age.claim_day": "1 Tag",
    "hank.age.claim_date": "einem Start am {date}",
    "hank.age.approx": "nur ungefähr",
    "hank.age.question": "{line}",
    "hank.age.question_unknown": "Ich kenne das Alter noch nicht. Trag die Wochen oder den Starttag in der "
                                 "Pflanzenkarte ein.",
    "hank.age.mixed_conflict": "Zum Alter: als Start steht der {stored} ({by}), bei {claim} wäre es der {implied}; "
                               "die Düngermengen ändern sich dadurch nicht.",
    "hank.age.mixed_unknown": "Zum Alter: einen Starttag kenne ich noch nicht, bei {claim} wäre es der {implied}; die "
                              "Düngermengen ändern sich dadurch nicht.",
    "hank.age.mixed_consistent": "Zum Alter: das passt zum Start am {stored}.",
    "hank.age.mixed_conflict_date": "Zum Alter: als Start steht der {stored} ({by}), du nennst den {implied}; die "
                                    "Düngermengen ändern sich dadurch nicht.",
    "hank.age.mixed_unknown_date": "Zum Alter: einen Starttag kenne ich noch nicht, du nennst den {implied}; die "
                                   "Düngermengen ändern sich dadurch nicht.",
    "hank.age.mixed_week": "Zum Alter: welcher Tag war der Start? Trag ihn in der Pflanzenkarte ein.",
    "hank.today.age_remind_unknown": "Starttag der Pflanze noch offen: in der Pflanzenkarte eintragen.",
    "hank.today.age_remind_approx": "Der Start {date} ist nur ungefähr: den echten Tag in der Pflanzenkarte "
                                    "eintragen.",
    "hank.today.choice": "Dünger wählen: {options}. Es gilt immer nur einer, bis dahin rechnet das Cockpit keine "
                         "Mengen.",
    "hank.today.next_change": "Nächste Änderung: ab {date} ({week}).",
    "hank.today.next_change_latest": "Nächste Änderung: spätestens ab {date} ({week}).",
    # the fertilizer market (23.10.2026): "ich habe / ich nutze <Dünger>"
    "hank.have.head": "{name} steht im Wiki: {types}, {media}.",
    "hank.have.applied": "So wird er angewendet: {text}",
    "hank.have.active": "{name} ist schon dein Dünger; der Plan unten ist der aus deiner Düngerkarte.",
    "hank.have.switch": "Wechseln kannst du mit zwei Tipps auf der Karte unten. Danach gilt nur noch {name}, und "
                        "keine Menge wird von einem Dünger zum anderen übernommen.",
    "hank.have.plan_ok": "Unten der Plan ab heute: Wochen nach rechts, Produkte nach unten, je Liter mit Faktor "
                         "{factor}, abgerundet.",
    "hank.have.plan_chart": "Unten der Plan ab heute: Wochen nach rechts, Produkte nach unten. Er zeigt, wann "
                            "welches Produkt dran ist, noch keine gerechneten Mengen: {why}",
    "hank.have.why_draft": "die Karte ist noch nicht bestätigt, das machst du nach dem Wechsel in der Düngerkarte.",
    "hank.have.why_stage": "die Woche ist nur geschätzt, bis du die Phase bestätigst.",
    "hank.have.plan_after": "Unten der Plan: diese Woche nennt die Tabelle keinen Dünger mehr ({week}).",
    "hank.have.no_plan": "Einen Plan je Liter zeichne ich dafür nicht: die Karte hat keinen Wochenplan, den das "
                         "Cockpit je Liter rechnen kann. Was der Hersteller vorsieht, steht auf der Karte im Wiki.",
    "hank.have.ambiguous": "Welchen meinst du: {options}? Sag mir den genauen Namen.",
    "hank.have.unknown": "{name} steht noch nicht in meinem Wiki. Wie heißt der Dünger genau, und wer stellt ihn "
                         "her? Schreib mir beides so, wie es auf dem Etikett steht. Bis es eine Karte dafür gibt, "
                         "nenne ich keine Menge.",
    "hank.have.unknown_noname": "Diesen Dünger kenne ich noch nicht aus dem Wiki. Wie heißt er genau, und wer "
                                "stellt ihn her? Schreib mir beides so, wie es auf dem Etikett steht. Bis es eine "
                                "Karte dafür gibt, nenne ich keine Menge.",
    "hank.have.link": "Karte im Wiki öffnen",
    "hank.have.wiki": "Dünger im Wiki",
    "hank.have.media_none": "das Etikett nennt kein Medium",
    "hank.have.media": "für {list}",
    "hank.have.media.soil": "Erde",
    "hank.have.media.coco": "Kokos",
    "hank.have.media.hydro": "Hydro",
    "hank.have.not_soil": "Für Erde ist die Linie nicht gemacht; deshalb biete ich sie im Cockpit nicht an und "
                          "zeichne keinen Plan dafür. Was der Hersteller für {media} vorsieht, steht auf der Karte im "
                          "Wiki.",
    "hank.have.unchecked": "Der Plan auf der Karte ist noch nicht gegen das Original geprüft; bis dahin biete ich ihn "
                           "im Cockpit nicht an.",
    "hank.have.type.organic_liquid": "organisch und flüssig",
    "hank.have.type.mineral_liquid": "mineralisch und flüssig",
    "hank.have.type.multi_part": "mehrteilig",
    "hank.have.type.powder": "Pulver oder Granulat",
    "hank.have.type.slow_release": "ein Langzeitdünger",
    "hank.proposal.feeding_setup": "Dünger wechseln: {label}",
    # the plan graphic
    "hank.plan.g_title": "Plan ab heute: {name}",
    "hank.plan.g_amounts": "Menge je Liter in {unit}, Faktor {factor} eingerechnet, abgerundet.",
    "hank.plan.g_chart": "Wann welches Produkt dran ist, noch ohne Menge: die rechnet das Cockpit erst, wenn Karte "
                         "und Phase bestätigt sind, mit Faktor {factor} und nie mehr als die Tabelle.",
    "hank.plan.g_chart_nofactor": "Wann welches Produkt dran ist, noch ohne Menge: die rechnet das Cockpit erst, "
                                  "wenn Karte und Phase bestätigt sind, nie mehr als die Tabelle.",
    "hank.plan.g_due": "geben",
    "hank.plan.g_where": "Pflanze {age} alt, Phase {stage}.",
    "hank.plan.g_where_stage": "Phase {stage}.",
    "hank.plan.g_age": "Pflanze {age} alt.",
    "hank.plan.g_latest": "Die Daten gelten spätestens: der Blütebeginn ist nur eine Obergrenze.",
    "hank.plan.g_today": "heute",
    "hank.plan.g_bloom": "Blüte {n}",
    "hank.plan.g_from": "ab {date}",
    "hank.plan.g_week": "PW {n}",
    "hank.plan.g_water": "nur Wasser",
    "hank.plan.g_harvest": "Ernte",
    "hank.plan.g_end": "Tabelle endet",
    "hank.plan.g_flush": "spülen",
    "hank.plan.g_flush_with": "Flush laut Tabelle, Grund unklar",
    "hank.plan.g_nothing": "nichts zu geben",
    "hank.plan.g_product": "Produkt",
}

EN: dict[str, str] = {
    "hank.feed.spoon_1": "1 spoon",
    "hank.feed.spoon_n": "{n} spoons",
    "hank.feed.item": "{label} {value}",
    "hank.feed.item_spoons": "{label} {value} ({spoons})",
    "hank.feed.and": "{a} and {b}",
    "hank.feed.choice_needed": "Which fertilizer do you give: {options}? Only one counts at a time. Choose it in the "
                               "feeding card; until then the cockpit computes no amounts.",
    "hank.feed.choice_fact": "Two fertilizers are entered, {options}, and only one counts at a time: you choose the one "
                             "you give in the feeding card; until then the cockpit computes no amounts.",
    "hank.feed.choice_active": "Right now it is {active}. Only one fertilizer counts at a time; you switch in the "
                               "feeding card, with two taps.",
    "hank.feed.choice_none": "Only one fertilizer is entered here: {active}.",
    "hank.feed.active_fact": "Right now it is {active}, and only one fertilizer counts at a time: you switch in the "
                             "feeding card, with two taps.",
    "hank.feed.choice_why": "None of my cards says which one is better: that is your call.",
    "hank.feed.choice_more": "You can also choose one of the {n} fertilizers from the wiki.",
    "hank.plan.today": "Today ({date}, {week}{age}): per litre {items}.",
    "hank.plan.age": ", plant {age} old",
    "hank.plan.age_approx": ", plant about {age} old",
    "hank.plan.change": "From {date} ({week}): {items}.",
    "hank.plan.change_latest": "From {date} at the latest ({week}): {items}.",
    "hank.plan.change_item": "{label} {to} instead of {from}",
    "hank.plan.change_none": "{label} no amount any more",
    "hank.plan.change_undated": "The next change comes with {week}; there is a date only once the flowering start is "
                                "known.",
    "hank.plan.more": "Further weeks are on the page.",
    "hank.plan.label_end": "From {date} ({week}) the chart gives no amount any more: the cockpit computes none, that "
                           "is your call.",
    "hank.plan.label_end_latest": "From {date} at the latest ({week}) the chart gives no amount any more: the cockpit "
                                  "computes none, that is your call.",
    "hank.plan.water": "From {date} ({week}) plain water only.",
    "hank.plan.water_latest": "From {date} at the latest ({week}) plain water only.",
    "hank.plan.harvest": "From {date} ({week}) harvest, no more fertilizer.",
    "hank.plan.harvest_latest": "From {date} at the latest ({week}) harvest, no more fertilizer.",
    "hank.plan.factor": "Factor {factor} is included, never more than the chart.",
    "hank.plan.frequency": "How often to feed, the chart does not say.",
    "hank.plan.spoons": "The spoons are rounded down to halves; below half a spoon only the scale helps.",
    "hank.plan.draft": "The feeding card ({card}) is not confirmed yet: until then I give no amounts, only the dates. "
                       "Confirm it in the feeding card.",
    "hank.plan.needs_stage": "The week is only estimated: no amounts until you confirm the stage. The dates hold at "
                             "the latest.",
    "hank.plan.week_now": "This week ({span}): {week}.",
    "hank.plan.week_now_latest": "This week ({span} at the latest): {week}.",
    "hank.plan.week_undated": "This week: {week}.",
    "hank.plan.span": "{from} to {to}",
    "hank.plan.next_date": "The amount changes from {date} ({week}).",
    "hank.plan.next_date_latest": "The amount changes from {date} at the latest ({week}).",
    "hank.plan.water_now": "{week}, no fertilizer this week.",
    "hank.plan.harvest_now": "{week}. No more fertilizer.",
    "hank.plan.label_end_now": "After flowering week {week} the chart gives no amount any more: the cockpit computes "
                               "none, that is your call.",
    "hank.plan.no_card": "I cannot show a feeding plan right now.",
    "hank.age.consistent": "That fits: from the start on {stored} she is {age} old (day {days}, plant week {life}). "
                           "Nothing to change.",
    "hank.age.conflict": "That does not fit my date. The start on record is {stored} ({by}); from it she is {age} "
                         "old (day {days}). With {claim} the start would be {implied}.",
    "hank.age.unknown": "Thanks. I do not know a start day yet. With {claim} it would be {implied}.",
    "hank.age.unknown_date": "Thanks. I do not know a start day yet. With the start on {implied} she is {age} old.",
    "hank.age.conflict_date": "That does not fit my date. The start on record is {stored} ({by}); from it she is "
                              "{age} old (day {days}). With your start on {implied} she would be {claim_age} old.",
    "hank.age.doses": "The feeding amounts do not change with it: they follow your stage and the flowering start.",
    "hank.age.card": "You can take it over with two taps on the card.",
    "hank.age.week_conflict": "You say plant week {n}. From my start on {stored} she is in plant week {life}. Which "
                              "day was the start? Enter it on the plant card.",
    "hank.age.week_consistent": "That fits: from the start on {stored} she is in plant week {life}. Nothing to "
                                "change.",
    "hank.age.week_unknown": "Plant week {n} is a whole week, not a day. Which day was the start? Enter it on the "
                             "plant card.",
    "hank.age.future": "A start in the future is not possible. Enter the start day on the plant card.",
    "hank.age.too_old": "That would be more than {days} days ago. Enter the start day on the plant card.",
    "hank.age.too_many_weeks": "The plant card takes no more than {weeks} weeks as an age. Enter the start day on "
                               "the plant card.",
    "hank.age.claim_weeks": "{n} weeks",
    "hank.age.claim_week": "1 week",
    "hank.age.claim_days": "{n} days",
    "hank.age.claim_day": "1 day",
    "hank.age.claim_date": "a start on {date}",
    "hank.age.approx": "only approximate",
    "hank.age.question": "{line}",
    "hank.age.question_unknown": "I do not know the age yet. Enter the weeks or the start day on the plant card.",
    "hank.age.mixed_conflict": "On the age: the start on record is {stored} ({by}), with {claim} it would be "
                               "{implied}; the feeding amounts do not change with it.",
    "hank.age.mixed_unknown": "On the age: I do not know a start day yet, with {claim} it would be {implied}; the "
                              "feeding amounts do not change with it.",
    "hank.age.mixed_consistent": "On the age: that fits the start on {stored}.",
    "hank.age.mixed_conflict_date": "On the age: the start on record is {stored} ({by}), you name {implied}; the "
                                    "feeding amounts do not change with it.",
    "hank.age.mixed_unknown_date": "On the age: I do not know a start day yet, you name {implied}; the feeding "
                                   "amounts do not change with it.",
    "hank.age.mixed_week": "On the age: which day was the start? Enter it on the plant card.",
    "hank.today.age_remind_unknown": "The plant's start day is still open: enter it on the plant card.",
    "hank.today.age_remind_approx": "The start {date} is only approximate: enter the real day on the plant card.",
    "hank.today.choice": "Choose a fertilizer: {options}. Only one counts at a time; until then the cockpit computes "
                         "no amounts.",
    "hank.today.next_change": "Next change: from {date} ({week}).",
    "hank.today.next_change_latest": "Next change: from {date} at the latest ({week}).",
    "hank.have.head": "{name} is in the wiki: {types}, {media}.",
    "hank.have.applied": "How it is applied: {text}",
    "hank.have.active": "{name} is already your fertilizer; the plan below is the one from your feeding card.",
    "hank.have.switch": "You switch with two taps on the card below. After that only {name} counts, and no amount "
                        "carries over from one fertilizer to the other.",
    "hank.have.plan_ok": "Below the plan from today: weeks across, products down, per litre with factor {factor}, "
                         "rounded down.",
    "hank.have.plan_chart": "Below the plan from today: weeks across, products down. It shows when each product "
                            "is due, no computed amounts yet: {why}",
    "hank.have.why_draft": "the card is not confirmed yet, you do that in the feeding card after the switch.",
    "hank.have.why_stage": "the week is only estimated until you confirm the stage.",
    "hank.have.plan_after": "Below the plan: this week the chart gives no more fertilizer ({week}).",
    "hank.have.no_plan": "I draw no per-litre plan for it: the card has no weekly plan the cockpit can compute per "
                         "litre. What the maker intends is on the card in the wiki.",
    "hank.have.ambiguous": "Which one do you mean: {options}? Tell me the exact name.",
    "hank.have.unknown": "{name} is not in my wiki yet. What is the fertilizer's exact name, and who makes it? Write "
                         "both the way they are printed on the label. Until there is a card for it, I give no amount.",
    "hank.have.unknown_noname": "I do not know this fertilizer from the wiki yet. What is its exact name, and who "
                                "makes it? Write both the way they are printed on the label. Until there is a card "
                                "for it, I give no amount.",
    "hank.have.link": "Open the card in the wiki",
    "hank.have.wiki": "Fertilizers in the wiki",
    "hank.have.media_none": "the label names no medium",
    "hank.have.media": "for {list}",
    "hank.have.media.soil": "soil",
    "hank.have.media.coco": "coco",
    "hank.have.media.hydro": "hydro",
    "hank.have.not_soil": "The line is not made for soil; so I do not offer it in the cockpit and draw no plan for "
                          "it. What the maker intends for {media} is on the card in the wiki.",
    "hank.have.unchecked": "The plan on the card is not checked against the original yet; until then I do not offer "
                           "it in the cockpit.",
    "hank.have.type.organic_liquid": "organic and liquid",
    "hank.have.type.mineral_liquid": "mineral and liquid",
    "hank.have.type.multi_part": "multi-part",
    "hank.have.type.powder": "powder or granules",
    "hank.have.type.slow_release": "a slow-release fertilizer",
    "hank.proposal.feeding_setup": "Switch fertilizer: {label}",
    "hank.plan.g_title": "Plan from today: {name}",
    "hank.plan.g_amounts": "Amount per litre in {unit}, factor {factor} included, rounded down.",
    "hank.plan.g_chart": "When each product is due, no amount yet: the cockpit computes it once the card and the "
                         "stage are confirmed, with factor {factor} and never above the chart.",
    "hank.plan.g_chart_nofactor": "When each product is due, no amount yet: the cockpit computes it once the card "
                                  "and the stage are confirmed, never above the chart.",
    "hank.plan.g_due": "add",
    "hank.plan.g_where": "Plant {age} old, stage {stage}.",
    "hank.plan.g_where_stage": "Stage {stage}.",
    "hank.plan.g_age": "Plant {age} old.",
    "hank.plan.g_latest": "The dates hold at the latest: the flowering start is only an upper bound.",
    "hank.plan.g_today": "today",
    "hank.plan.g_bloom": "Flower {n}",
    "hank.plan.g_from": "from {date}",
    "hank.plan.g_week": "pw {n}",
    "hank.plan.g_water": "plain water",
    "hank.plan.g_harvest": "harvest",
    "hank.plan.g_end": "chart ends",
    "hank.plan.g_flush": "flush",
    "hank.plan.g_flush_with": "flush per chart, reason unclear",
    "hank.plan.g_nothing": "nothing to add",
    "hank.plan.g_product": "Product",
}


def check() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash, an umlaut
    in English."""
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
        if re.search("[äöüÄÖÜß]", EN[key]):
            problems.append(f"umlaut in EN: {key}")
    return problems


def register() -> None:
    """Check parity and put the texts into the server catalogs (i18n.DE, i18n.EN). Safe to call more than once."""
    problems = check()
    if problems:
        raise ValueError("feedplan: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _lang(lang: str) -> str:
    return lang if lang in ("de", "en") else "de"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _today(state: LiveState) -> date:
    return state.now.date() if isinstance(state.now, datetime) else date.today()


# ---------- 1. units ----------

def unit_of(view: Mapping[str, Any] | None) -> str:
    """The unit of the view's amounts (feeding.view amount_unit); for an old view without it the chart's unit
    ("g/l" gives g), else ml."""
    unit = _dict(view).get("amount_unit")
    if unit in UNITS:
        return unit
    chart = str(_dict(view).get("unit") or "").split("/")[0]
    return chart if chart in UNITS else "ml"


def quantity(value: Any, unit: str, lang: str) -> str:
    """'0,75 g', '1,5 ml': the number as feeding.view rounded it (down), with the unit."""
    return fmt_qty(value, unit, lang)


def spoons_text(n: Any, lang: str) -> str | None:
    """'1,5 Löffel', '1 spoon'; None for no spoons (below half a spoon, or a card without a spoon)."""
    if not _number(n) or n <= 0:
        return None
    if float(n) == 1:
        return t("hank.feed.spoon_1", lang)
    return t("hank.feed.spoon_n", lang, n=fmt_num(n, lang))


def item(label: Any, value: Any, unit: str, lang: str, spoons: Any = None) -> str:
    """'Base A 0,75 g (1,5 Löffel)', 'Bio-Grow 1,5 ml'."""
    shown = spoons_text(spoons, lang)
    if shown:
        return t("hank.feed.item_spoons", lang, label=label, value=quantity(value, unit, lang), spoons=shown)
    return t("hank.feed.item", lang, label=label, value=quantity(value, unit, lang))


def amount_items(rows: Iterable[Mapping[str, Any]], unit: str, lang: str, *, key: str = "per_liter",
                 spoon_key: str = "spoons_per_liter") -> list[str] | None:
    """Each row's amount in the card's unit, with spoons where the view gives them. None when a row has no number:
    never "Base A ? g" (feeding.view always gives one in state ok)."""
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if not _number(row.get(key)):
            return None
        out.append(item(row.get("label") or row.get("component"), row[key], unit, lang, row.get(spoon_key)))
    return out


def joined(parts: list[str], lang: str) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return t("hank.feed.and", lang, a=", ".join(parts[:-1]), b=parts[-1])


# ---------- 2. a stated age ----------

@dataclass(frozen=True)
class AgeClaim:
    """kind "weeks" | "days" | "life_week" | "date"; n the number (0 for a date); day the date (kind date only)."""

    kind: str
    n: int
    day: str | None = None


_NUMBER_WORDS = {
    "null": 0, "ein": 1, "eine": 1, "einer": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwolf": 12, "zero": 0, "one": 1, "a": 1, "an": 1,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}
_NUM = r"(\d{1,3}|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")"
_ORD = r"(\d{1,2})(?:st|nd|rd|th|te|ten)?"
# what the age is about: never water, seeds, the solution or the fertilizer (critique 23.10.2026: "Das Wasser im Tank
# ist 3 Tage alt" must not move the plant's start)
_NOT_PLANT = frozenset({
    "wasser", "giesswasser", "leitungswasser", "tank", "tanks", "kanne", "giesskanne", "losung", "nahrlosung",
    "dungerlosung", "mischung", "samen", "saat", "saatgut", "same", "dunger", "erde", "substrat", "flasche",
    "flaschen", "pulver", "granulat", "foto", "fotos", "bild", "bilder", "kamera", "messung", "messwerte", "box",
    "zelt", "lampe", "steckling", "stecklinge", "klon", "blatt", "blatter", "water", "solution", "mix", "seed",
    "seeds", "fertilizer", "fertiliser", "nutrients", "nutrient", "soil", "bottle", "bottles", "powder", "photo",
    "picture", "camera", "reading", "readings", "tent", "lamp", "clone", "clones", "cutting", "cuttings", "leaf",
    "leaves", "umgetopft", "umtopfen", "repotted", "repot", "topf", "pot",
})
# flowering: "Blütewoche 5", "flowering week 5", "5. Woche der Blüte" are bloom weeks, never the plant's age
_FLOWER = frozenset({"blute", "bluten", "bluhen", "bluht", "blutewoche", "bluhwoche", "blutephase", "flowering",
                     "flower", "flowers", "bloom", "blooming", "flowered", "bluhte", "pistils", "harchen"})
_HARVEST = frozenset({"ernte", "ernten", "harvest", "erntereif"})
_FEED = re.compile(r"\b(dung\w*|nahrstoff\w*|feed\w*|fertili[sz]\w*|nutrients?|wie ?viel\w*|how (much|many)|menge\w*|"
                   r"dosis|dose|ml|gramm|grams?|loffel\w*|spoons?|base [ab])\b")
_CLAIMS = (
    # "6 Wochen und 1 Tag alt", "6 weeks and 1 day old": the days in all (kind weeks_days, n = 7 x weeks + days)
    ("weeks_days", re.compile(rf"\b{_NUM} (?:wochen|woche) (?:und )?{_NUM} (?:tage|tagen|tag) alt\b")),
    ("weeks_days", re.compile(rf"\b{_NUM} weeks? (?:and )?{_NUM} days? old\b")),
    # weeks / days: an age, "N Wochen alt", "N weeks old"
    ("weeks", re.compile(rf"\b{_NUM} (?:wochen|woche) alt\b")),
    ("days", re.compile(rf"\b{_NUM} (?:tage|tagen|tag) alt\b")),
    ("weeks", re.compile(rf"\b{_NUM} weeks? old\b")),
    ("days", re.compile(rf"\b{_NUM} days? old\b")),
    # "vor N Wochen gekeimt", "germinated N weeks ago"
    ("weeks", re.compile(rf"\bvor {_NUM} (?:wochen|woche) (?:gekeimt|gesat|ausgesat|gepflanzt|gestartet|angefangen)")),
    ("days", re.compile(rf"\bvor {_NUM} (?:tagen|tag) (?:gekeimt|gesat|ausgesat|gepflanzt|gestartet|angefangen)")),
    ("weeks", re.compile(rf"\b(?:germinated|sprouted|started|planted) {_NUM} weeks? ago\b")),
    ("days", re.compile(rf"\b(?:germinated|sprouted|started|planted) {_NUM} days? ago\b")),
    # "seit N Wochen am Wachsen"
    ("weeks", re.compile(rf"\bseit {_NUM} (?:wochen|woche) (?:am wachsen|wachst|gekeimt|keimt|lebt)")),
    ("days", re.compile(rf"\bseit {_NUM} (?:tagen|tag) (?:am wachsen|wachst|gekeimt|keimt|lebt)")),
    # the plant's week: "Pflanzenwoche 6", "in der 6. Woche", "6. Lebenswoche", "plant week 6"
    ("life_week", re.compile(rf"\b(?:pflanzenwoche|lebenswoche) {_ORD}\b")),
    ("life_week", re.compile(rf"\b{_ORD} (?:pflanzenwoche|lebenswoche)\b")),
    ("life_week", re.compile(rf"\bin der {_ORD} woche\b")),
    ("life_week", re.compile(rf"\bwoche {_ORD} (?:der|meiner|ihrer) pflanze\b")),
    ("life_week", re.compile(rf"\bplant week {_ORD}\b")),
    ("life_week", re.compile(rf"\bweek {_ORD} of (?:my|the|her|its) (?:plant|life)\b")),
    ("life_week", re.compile(rf"\bin (?:her|its|the) {_ORD} week\b")),
)
_MONTHS = {"januar": 1, "jan": 1, "january": 1, "februar": 2, "feb": 2, "february": 2, "marz": 3, "mar": 3,
           "march": 3, "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "jun": 6, "june": 6, "juli": 7,
           "jul": 7, "july": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
           "okt": 10, "oct": 10, "october": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12, "dec": 12,
           "december": 12}
_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_DATE_LEAD = r"(?:seit dem|seit|gekeimt am|gesat am|gestartet am|keimung am|start am|since the|since|" \
             r"germinated on|sprouted on|started on|germinated|sprouted)"
_DATES = (
    re.compile(rf"\b{_DATE_LEAD} (\d{{1,2}}) (\d{{1,2}})(?: (\d{{4}}))?\b"),  # "seit dem 18.09.", words "18 09"
    re.compile(rf"\b{_DATE_LEAD} (\d{{1,2}}) {_MONTH}(?: (\d{{4}}))?\b"),
    re.compile(rf"\b{_DATE_LEAD} {_MONTH} (\d{{1,2}})(?:st|nd|rd|th)?(?: (\d{{4}}))?\b"),
)
# the words a pure claim may carry beside the claim itself: then code answers, without the search
_CLAIM_WORDS = frozenset({
    "meine", "mein", "die", "unsere", "pflanze", "autoflower", "auto", "ice", "cream", "cake", "sie", "ist", "ja",
    "jetzt", "schon", "genau", "etwa", "ungefahr", "circa", "ca", "knapp", "fast", "uber", "alt", "wochen", "woche",
    "tage", "tagen", "tag", "und", "also", "ok", "okay", "ubrigens", "nur", "zur", "info", "hi", "hallo", "hey",
    "vor", "gekeimt", "gesat", "ausgesat", "gepflanzt", "gestartet", "angefangen", "seit", "dem", "am", "wachsen",
    "wachst", "keimt", "lebt", "in", "der", "pflanzenwoche", "lebenswoche", "meiner", "ihrer", "keimung", "start",
    "my", "plant", "she", "is", "the", "now", "already", "about", "around", "almost", "nearly", "just", "over",
    "old", "weeks", "week", "days", "day", "and", "so", "btw", "germinated", "sprouted", "started", "planted",
    "ago", "since", "on", "of", "her", "its", "life", "it", "s", "shes", "she's", "hank", "st", "nd", "rd", "th",
    "te", "ten", "wir", "sind", "we", "are",
})
_PLANT_AGE_Q = re.compile(r"^(?:und |also |hey |hi |hallo )?(?:wie alt ist (?:meine |die |unsere )?(?:pflanze|sie|"
                          r"autoflower|ice cream cake)(?: (?:jetzt|eigentlich|genau|gerade|schon|denn))*|"
                          r"wie viele (?:wochen|tage) (?:ist|alt ist) (?:meine |die )?(?:pflanze|sie)(?: alt)?|"
                          r"how old is (?:my |the )?(?:plant|she|it|autoflower)(?: (?:now|exactly|actually))*|"
                          r"how many (?:weeks|days) old is (?:my |the )?(?:plant|she|it))$")


def _words(q: str) -> list[str]:
    return asking.words(q)


def _to_int(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    return _NUMBER_WORDS.get(raw)


def _year_for(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        if year is not None:
            return date(year, month, day)
        found = date(today.year, month, day)
    except ValueError:
        return None
    if found > today and (found - today).days <= FUTURE_DAYS:
        return found  # "seit dem 25.10." on 23.10.: a day ahead, refused by the answer (never silently last year)
    if found > today:  # "seit dem 18.09." in January means last year
        try:
            found = date(today.year - 1, month, day)
        except ValueError:
            return None
    return found


# Clauses: the age is judged in the clause that states it ("Meine Pflanze ist 5 Wochen alt, wie viel Dünger?" is a
# claim plus a feeding question; "Die Nährlösung ist 2 Wochen alt" is no claim). A dot ends a clause only after a
# letter and before a space or the end, so "18.09." stays one date and "in der 6. Woche" one clause.
_CLAUSE = re.compile(r"[,;:!?]|(?<!\d)\.(?=\s|$)|\s(?:und|and)\s(?!" + _NUM + r" (?:tag|day))|\s(?:aber|but)\s")
# A clause states the PLANT's age only with the plant as its subject ("meine Pflanze", "sie", "she", "my plant", the
# strain, a germination word) or with nothing else in it than the claim words ("5 Wochen alt", "seit dem 18.09."):
# "Der Tee ist 5 Wochen alt" or "Ich habe seit dem 18.09. nicht gegossen" state nothing about the plant (critique
# 23.10.2026).
_PLANT_WORDS = frozenset({"pflanze", "pflanzchen", "autoflower", "sie", "she", "plant", "cake", "gekeimt", "keimt",
                          "keimung", "gesat", "ausgesat", "germinated", "sprouted", "wachst", "wachsen", "lebt"})
# the plant named by a noun (or her germination), not by a pronoun: "sie" and "she" take the subject before
_NAMED_PLANT = _PLANT_WORDS - {"sie", "she"}


def age_claim(q: str, today: date | None = None) -> AgeClaim | None:
    """A stated age of the plant, or None. Folded words (asking.words), clause by clause. Never in a clause that names
    flowering, the harvest ("5 Wochen bis zur Ernte"), a time ahead ("noch 5 Wochen") or something else than the
    plant (water, seeds, the solution, the fertilizer, the box), and a plant week never in a question with a feeding
    word ("Wie viel Dünger in der 5. Woche?" stays a feeding question)."""
    today = today or date.today()
    all_words = _words(q)
    whole = " ".join(all_words)
    subject = None  # the subject of the clauses so far: "plant", "other" (water, seeds, ...) or None
    for clause in _CLAUSE.split(text_fold(q)):
        found = _words(clause)
        if not found:
            continue
        present = set(found)
        before, subject = subject, _subject(present, subject)
        if present & _FLOWER or present & _HARVEST or present & _NOT_PLANT:
            continue
        joined_ = " ".join(found)
        if re.search(r"\bbase [ab]\b|\bbis\b|\buntil\b|\bnoch\b \d+|\bstill\b|\bleft\b", joined_):
            continue  # "noch 5 Wochen", "until": a time ahead, no age
        if not present & _PLANT_WORDS and _rest(found):
            continue  # another subject ("Der Tee ist 5 Wochen alt") or another verb ("seit dem 18.09. nicht gegossen")
        if not present & _NAMED_PLANT:
            # no noun of the plant in this clause ("3 Tage alt", "sie ist 3 Tage alt"): the clause before names the
            # subject. "Das Wasser im Tank, 3 Tage alt" and "Die Samen, sie sind 5 Wochen alt" are about the water and
            # the seeds (critique 23.10.2026, M2); with no subject before, the question must name nothing else.
            if before == "other":
                continue
            if before is None and not present & _PLANT_WORDS and set(all_words) & _NOT_PLANT:
                continue
        claim = _claim_in(joined_, whole, today)
        if claim is not None:
            return claim
    return None


def _subject(present: set[str], before: str | None) -> str | None:
    """The subject after a clause: the plant when it names her, something else when it names water, seeds or the
    like, else the one before (a pronoun or a bare age keeps it)."""
    if present & _NAMED_PLANT:
        return "plant"
    if present & _NOT_PLANT:
        return "other"
    if before is None and present & _PLANT_WORDS:
        return "plant"
    return before


def text_fold(q: str) -> str:
    from . import text  # folded as asking.words folds it, punctuation kept for the clauses

    return text.fold(str(q or ""))


def _claim_in(joined_: str, whole: str, today: date) -> AgeClaim | None:
    for kind, pattern in _CLAIMS:
        hit = pattern.search(joined_)
        if not hit:
            continue
        n = _to_int(hit.group(1))
        if n is None:
            continue
        if kind == "weeks_days":
            extra = _to_int(hit.group(2))
            if extra is None:
                continue
            return AgeClaim("days", 7 * n + extra)
        if kind == "life_week" and (_FEED.search(whole) or not 1 <= n <= MAX_WEEKS + 1):
            return None
        return AgeClaim(kind, n)
    for pattern in _DATES:
        hit = pattern.search(joined_)
        if not hit:
            continue
        groups = hit.groups()
        if groups[0].isdigit() and groups[1].isdigit():
            day_n, month, year = int(groups[0]), int(groups[1]), groups[2]
        elif groups[0].isdigit():
            day_n, month, year = int(groups[0]), _MONTHS[groups[1]], groups[2]
        else:
            month, day_n, year = _MONTHS[groups[0]], int(groups[1]), groups[2]
        if not 1 <= month <= 12:
            continue
        day = _year_for(day_n, month, int(year) if year else None, today)
        if day is None:
            continue
        return AgeClaim("date", 0, day.isoformat())
    return None


def pure_claim(q: str, claim: AgeClaim | None = None) -> bool:
    """The question is the claim and nothing else ("Meine Pflanze ist 5 Wochen alt."): code answers it whole. A
    mixed one ("..., wie viel Dünger?") goes the normal way and carries the claim's card."""
    claim = claim or age_claim(q)
    if claim is None:
        return False
    return not _rest(_words(q))


def _rest(found: list[str]) -> list[str]:
    """The words beside the claim words, numbers and months."""
    return [w for w in found if w not in _CLAIM_WORDS and _to_int(w) is None and w not in _MONTHS
            and not re.fullmatch(r"\d{1,4}(?:st|nd|rd|th|te|ten)?", w)]


def asks_plant_age(q: str) -> bool:
    """"Wie alt ist meine Pflanze?", "How old is my plant?": a question, no claim."""
    return bool(_PLANT_AGE_Q.match(" ".join(_words(q))))


# ---------- 3. the plan, 4. the choice ----------

_PLAN_RE = re.compile(
    r"\b(?:wann (?:dunge|gebe|gieße|giesse) ich (?:was|welchen dunger|welche menge|wie viel)|was (?:dunge|gebe) ich "
    r"wann|wann (?:kommt|gibt es) (?:welcher|welche) (?:dunger|menge)|dungeplan\w*|dungerplan\w*|dungeschema\w*|"
    r"wie sieht (?:mein|der) (?:dunge|dunger)?plan aus|plan ab heute|wie geht es (?:mit dem dungen|beim dungen) "
    r"weiter|what do i feed when|when do i feed what|when do i give (?:what|which fertili[sz]er)|feeding "
    r"(?:schedule|plan)|nutrient (?:schedule|plan)|my feeding plan|fertili[sz]er (?:schedule|plan))\b")
_NOT_PLAN = re.compile(r"\b(?:nachste\w* mal|next time|wie oft|how often|woche \d|week \d|\d+ woche|blutewoche \d|"
                       r"heute|today|diese woche|this week)\b")
_CHOICE_RE = re.compile(
    r"\b(?:welchen dunger (?:soll|sollte|nehme|nehm|nimm|gebe|geb|brauche|empfiehlst|nutze|benutze)|welcher dunger "
    r"(?:ist besser|soll|passt)|biobizz oder vivosun|vivosun oder biobizz|welchen der (?:beiden|zwei) dunger|"
    r"which fertili[sz]er (?:should|do|to|is better)|biobizz or vivosun|vivosun or biobizz)\b")


def asks_feed_plan(q: str) -> bool:
    """"Wann dünge ich was?", "What do I feed when?", "Düngeplan": the dated plan from today on. Not "Wann dünge ich
    das nächste Mal?" (the card's frequency note answers that) and not a question about one week."""
    joined_ = " ".join(_words(q))
    return bool(_PLAN_RE.search(joined_)) and not _NOT_PLAN.search(joined_)


def asks_feed_choice(q: str) -> bool:
    """"Welchen Dünger soll ich nehmen?", "Which fertilizer should I use?"."""
    return bool(_CHOICE_RE.search(" ".join(_words(q))))


def route_for(q: str, lang: str, *, today: date | None = None) -> Route | None:
    """The reading route of a question this module answers from code alone, or None (asking.typed_route calls it).
    The claim's numbers travel in the reason ("reading:age_claim:weeks=5"): types.Route takes no new field."""
    claim = age_claim(q, today)
    if claim is not None and pure_claim(q, claim):
        value = claim.day if claim.kind == "date" else claim.n
        return Route("reading", None, None, None, "info", lang, f"reading:age_claim:{claim.kind}={value}",
                     qtype="value", term="age_claim")
    if asks_plant_age(q):
        return Route("reading", None, None, None, "info", lang, "reading:plant_age", qtype="value", term="plant_age")
    if asks_feed_plan(q):
        return Route("reading", None, None, None, "info", lang, "reading:feed_plan", qtype="value", term="feed_plan")
    if asks_feed_choice(q):
        return Route("reading", None, None, None, "info", lang, "reading:feed_choice", qtype="value",
                     term="feed_choice")
    have = fertilizer_claim(q)
    if have is not None:
        return Route("reading", None, None, None, "info", lang, f"reading:feed_have:{have.reason()}", qtype="value",
                     term="feed_have")
    return None


TERMS = ("age_claim", "plant_age", "feed_plan", "feed_choice", "feed_have")


def claim_of(route: Route) -> AgeClaim | None:
    """The claim a route's reason carries ("reading:age_claim:weeks=5")."""
    found = re.search(r"age_claim:(weeks|days|life_week|date)=([0-9-]+)", route.reason or "")
    if not found:
        return None
    kind, value = found.groups()
    if kind == "date":
        return AgeClaim("date", 0, value)
    return AgeClaim(kind, int(value))


# ---------- the age answers ----------

def _age(state: LiveState) -> dict[str, Any]:
    return _dict(state.plant_view.get("age"))


def _by(age: Mapping[str, Any], lang: str) -> str:
    source = age.get("from")
    key = "plant.start_by.owner_weeks" if source == "age_weeks" else "plant.start_by.owner_days" \
        if source == "age_days" else f"plant.start_by.{age.get('by')}"
    out = t(key, lang) if age.get("by") in ("owner", "plan") else "?"
    return f"{out}, {t('hank.age.approx', lang)}" if age.get("approx") else out


def _claim_text(claim: AgeClaim, lang: str) -> str:
    if claim.kind == "date":
        return t("hank.age.claim_date", lang, date=plant.date_text(claim.day, lang))
    if claim.kind == "weeks":
        return t("hank.age.claim_week", lang) if claim.n == 1 else t("hank.age.claim_weeks", lang, n=claim.n)
    return t("hank.age.claim_day", lang) if claim.n == 1 else t("hank.age.claim_days", lang, n=claim.n)


def card_body(claim: AgeClaim, today: date) -> dict[str, Any] | None:
    """The plant_start body the claim would store, as /api/cmd takes it from the plant card: an age in weeks or
    days (the start is then known only as roughly as said, plant.age_view approx), or a date."""
    if claim.kind == "weeks" and 0 <= claim.n <= MAX_WEEKS:
        return {"action": "plant_start", "age_weeks": claim.n}
    if claim.kind == "days" and 0 <= claim.n <= MAX_DAYS:
        return {"action": "plant_start", "age_days": claim.n}
    if claim.kind == "date" and claim.day:
        return {"action": "plant_start", "date": claim.day}
    return None


def implied_start(body: Mapping[str, Any], today: date) -> date | None:
    """The day a plant_start body stands for (proposals.start_day): today minus the weeks or days, or its date."""
    return proposals.start_day(body, today)


def card_label(body: Mapping[str, Any], today: date, lang: str) -> str:
    """'Startdatum 18.09.2026 übernehmen (5 Wochen alt)' (proposals.start_label)."""
    return proposals.start_label(body, today, lang)


def claim_answer(claim: AgeClaim, state: LiveState, lang: str) -> tuple[list[str], list[dict[str, Any]]]:
    """The answer's sentences and the card bodies (at most one) for a stated age, against plant.age_view."""
    lang = _lang(lang)
    today = _today(state)
    age = _age(state)
    stored = plant.parse_day(age.get("start"))
    if claim.kind == "life_week":
        check = plant.age_check(stored, "life_week", claim.n, today)
        if stored is None:
            return [t("hank.age.week_unknown", lang, n=claim.n)], []
        info = plant.age_info(stored, today) or {}
        key = "hank.age.week_consistent" if check["consistent"] else "hank.age.week_conflict"
        return [t(key, lang, n=claim.n, stored=plant.date_text(stored, lang), life=info.get("life_week", "?"))], []
    body = card_body(claim, today)
    day = implied_start(body, today) if body else None
    if body is None and claim.kind == "weeks" and 7 * claim.n <= MAX_DAYS:
        return [t("hank.age.too_many_weeks", lang, weeks=MAX_WEEKS)], []
    if body is None or day is None:
        return [t("hank.age.too_old", lang, days=MAX_DAYS)], []
    if day > today:
        return [t("hank.age.future", lang)], []
    if (today - day).days > MAX_DAYS:
        return [t("hank.age.too_old", lang, days=MAX_DAYS)], []
    implied = plant.date_text(day, lang)
    claim_text = _claim_text(claim, lang)
    if stored is None:
        if claim.kind == "date":  # the owner named the day itself: say the age it gives, not the day twice
            first = t("hank.age.unknown_date", lang, implied=implied,
                      age=plant.age_text(plant.age_info(day, today) or {}, lang))
        else:
            first = t("hank.age.unknown", lang, claim=claim_text, implied=implied)
        return [first, t("hank.age.doses", lang), t("hank.age.card", lang)], [body]
    info = plant.age_info(stored, today) or {"days": 0, "weeks": 0, "extra_days": 0, "life_week": 1}
    if claim.kind == "date":
        consistent = day == stored
    else:
        consistent = bool(plant.age_check(stored, claim.kind, claim.n, today)["consistent"])
    if consistent:
        return [t("hank.age.consistent", lang, stored=plant.date_text(stored, lang), age=plant.age_text(info, lang),
                  days=info["days"], life=info["life_week"])], []
    if claim.kind == "date":
        first = t("hank.age.conflict_date", lang, stored=plant.date_text(stored, lang), by=_by(age, lang),
                  age=plant.age_text(info, lang), days=info["days"], implied=implied,
                  claim_age=plant.age_text(plant.age_info(day, today) or {}, lang))
    else:
        first = t("hank.age.conflict", lang, stored=plant.date_text(stored, lang), by=_by(age, lang),
                  age=plant.age_text(info, lang), days=info["days"], claim=claim_text, implied=implied)
    return [first, t("hank.age.doses", lang), t("hank.age.card", lang)], [body]


def age_lines(state: LiveState, lang: str) -> list[str]:
    """"Wie alt ist meine Pflanze?": the age from plant.age_view (its own line, written by plant.py), with the ask
    sentence when the start is unknown or only approximate."""
    age = _age(state)
    if age.get("state") != "known" or not age.get("line"):
        return [t("hank.age.question_unknown", lang)]
    out = [str(age["line"])]
    if age.get("ask") and age.get("ask_text"):
        out.append(str(age["ask_text"]))
    return out


def cards_for(bodies: Iterable[Mapping[str, Any]], live: LiveState | Mapping[str, Any], lang: str, *,
              now: float | None = None) -> list[Proposal]:
    """The cards of the bodies, checked by proposals.make (Refused ones are left out)."""
    out = []
    for body in bodies:
        try:
            out.append(proposals.make(body, live, lang, reason="age_claim", now=now))
        except proposals.Refused:
            continue
    return out


def cards_for_claim(question: str, live: LiveState | Mapping[str, Any], lang: str, *,
                    now: float | None = None) -> list[Proposal]:
    """A mixed question with a stated age ("Meine Pflanze ist 5 Wochen alt, wie viel Dünger?"): the claim's card,
    exactly as the pure claim gets it (core._cards)."""
    state = collect(live)
    claim = age_claim(question, _today(state))
    if claim is None:
        return []
    return cards_for(claim_answer(claim, state, lang)[1], state, lang, now=now)


def claim_fact(question: str, live: LiveState | Mapping[str, Any], lang: str) -> str | None:
    """The sentence a mixed question's claim gets in the answer (context.build: a mandatory computed fact, so the
    model can only phrase it)."""
    state = collect(live)
    today = _today(state)
    claim = age_claim(question, today)
    if claim is None:
        return None
    lang = _lang(lang)
    age = _age(state)
    stored = plant.parse_day(age.get("start"))
    if claim.kind == "life_week":
        if stored is not None and plant.age_check(stored, "life_week", claim.n, today)["consistent"]:
            return t("hank.age.mixed_consistent", lang, stored=plant.date_text(stored, lang))
        return t("hank.age.mixed_week", lang)
    body = card_body(claim, today)
    day = implied_start(body, today) if body else None
    if day is None or day > today or (today - day).days > MAX_DAYS:
        return t("hank.age.mixed_week", lang)
    if stored is None:
        if claim.kind == "date":
            return t("hank.age.mixed_unknown_date", lang, implied=plant.date_text(day, lang))
        return t("hank.age.mixed_unknown", lang, claim=_claim_text(claim, lang), implied=plant.date_text(day, lang))
    consistent = day == stored if claim.kind == "date" else \
        bool(plant.age_check(stored, claim.kind, claim.n, today)["consistent"])
    if consistent:
        return t("hank.age.mixed_consistent", lang, stored=plant.date_text(stored, lang))
    if claim.kind == "date":
        return t("hank.age.mixed_conflict_date", lang, stored=plant.date_text(stored, lang), by=_by(age, lang),
                 implied=plant.date_text(day, lang))
    return t("hank.age.mixed_conflict", lang, stored=plant.date_text(stored, lang), by=_by(age, lang),
             claim=_claim_text(claim, lang), implied=plant.date_text(day, lang))


# ---------- the plan answer ----------

def _feed(state: LiveState) -> dict[str, Any]:
    return _dict(state.feeding_view)


def _house(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    """plant.json's own fertilizers among the switch's options; the wiki's market cards (feeding "library") are
    counted apart, so the sentence still names the two in the house (23.10.2026). All of them when none is marked."""
    options = [o for o in _dict(view.get("choice")).get("options") or [] if isinstance(o, dict)]
    own = [o for o in options if not o.get("library")]
    return own or options


def _library_count(view: Mapping[str, Any]) -> int:
    return sum(1 for o in _dict(view.get("choice")).get("options") or [] if isinstance(o, dict) and o.get("library"))


def _options(view: Mapping[str, Any], lang: str) -> str:
    labels = [str(o.get("label") or o.get("id")) for o in _house(view)]
    if lang == "de":
        return " oder ".join(labels) if len(labels) <= 2 else ", ".join(labels[:-1]) + " oder " + labels[-1]
    return " or ".join(labels) if len(labels) <= 2 else ", ".join(labels[:-1]) + " or " + labels[-1]


def choice_fact(view: Mapping[str, Any], lang: str) -> str:
    """The choice as one sentence, for a growing answer's facts (the bubble keeps a fact's first and last sentence)."""
    labels = [str(o.get("label") or o.get("id")) for o in _house(view)]
    return t("hank.feed.choice_fact", lang, options=joined(labels, lang))


def active_fact(view: Mapping[str, Any], lang: str) -> str:
    """"Gerade gilt BioBizz ...": a question about the other fertilizer gets which one counts, no amount. One
    sentence: it is a growing answer's direct fact, and the bubble keeps a fact's first and last sentence only."""
    choice = _dict(view.get("choice"))
    active = next((o for o in choice.get("options") or [] if isinstance(o, dict) and o.get("active")), None)
    name = (active or {}).get("label") or (active or {}).get("id") or _dict(view.get("card")).get("name") or "?"
    return t("hank.feed.active_fact", lang, active=name)


def choice_needed(view: Mapping[str, Any]) -> bool:
    return view.get("state") == "needs_choice" or bool(_dict(view.get("choice")).get("needed"))


def choice_lines(state: LiveState, lang: str) -> list[str]:
    """"Welchen Dünger soll ich nehmen?": which ones there are, which counts now, that the owner decides."""
    view = _feed(state)
    choice = _dict(view.get("choice"))
    options = [o for o in choice.get("options") or [] if isinstance(o, dict)]
    more = [t("hank.feed.choice_more", lang, n=_library_count(view))] if _library_count(view) else []
    if choice_needed(view):
        return [t("hank.feed.choice_needed", lang, options=_options(view, lang)), *more,
                t("hank.feed.choice_why", lang)]
    active = next((o for o in options if o.get("active")), None)
    if active is None:
        return [str(view.get("problem") or t("hank.plan.no_card", lang))]
    name = str(active.get("label") or active.get("id"))
    if len(options) < 2:
        return [t("hank.feed.choice_none", lang, active=name)]
    return [t("hank.feed.choice_active", lang, active=name), *more, t("hank.feed.choice_why", lang)]


def _span(column: Mapping[str, Any], lang: str) -> str | None:
    if not column.get("from") or not column.get("to"):
        return None
    return t("hank.plan.span", lang, **{"from": day_text(column["from"], lang), "to": day_text(column["to"], lang)})


def _changes(columns: list[dict[str, Any]], now: int, unit: str, lang: str) -> list[tuple[dict[str, Any], str]]:
    """Every later column whose amounts differ from the one before it, with the change in words."""
    out = []
    before = {v["component"]: v for v in columns[now]["values"]}
    for column in columns[now + 1:]:
        values = {v["component"]: v for v in column["values"]}
        parts = []
        for comp, v in values.items():
            old = before.get(comp) or {}
            if old.get("per_liter") == v.get("per_liter"):
                continue
            if v.get("per_liter") is None:
                parts.append(t("hank.plan.change_none", lang, label=v.get("label")))
                continue
            to = item("", v["per_liter"], unit, lang, v.get("spoons")).strip()
            frm = quantity(old.get("per_liter"), unit, lang) if _number(old.get("per_liter")) else "-"
            parts.append(t("hank.plan.change_item", lang, label=v.get("label"), to=to, **{"from": frm}))
        if parts:
            out.append((column, joined(parts, lang)))
        before = values
    return out


def _week_name(n: int, lang: str) -> str:
    """'Blütewoche 9', 'Flowering week 9': written like the chart's column labels (capital first letter)."""
    week = t("hank.forecast.week", lang, n=n)
    return week[:1].upper() + week[1:]


def _tails(view: Mapping[str, Any], timeline: Mapping[str, Any], lang: str) -> list[str]:
    """What comes after the last column, with its date (bloom start + weeks), in the active card's words."""
    start = plant.parse_day(view.get("bloom_start"))
    latest = timeline.get("dates") == "latest"
    out = []
    for entry in view.get("after_bloom") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("bloom_week"), int):
            continue
        if start is None:
            continue
        day = day_text((start + timedelta(days=7 * (entry["bloom_week"] - 1))).isoformat(), lang)
        week = _week_name(entry["bloom_week"], lang)
        if entry.get("key") in ("label_end", "water", "harvest"):
            name = "label_end" if entry["key"] == "label_end" else entry["key"]
            out.append(t(f"hank.plan.{name}_latest" if latest else f"hank.plan.{name}", lang, date=day, week=week))
    return out


def last_chart_week(view: Mapping[str, Any]) -> int | str:
    """The last flowering week the table names: the week before its end (after_bloom label_end, else the timeline's
    tail). Never the current week minus one (critique 23.10.2026, M1: in week 12 that said "after week 11")."""
    tails = ("label_end", "water", "harvest")  # Plagron ends with harvest, BioBizz with water (critique, M2)
    found = [int(e["bloom_week"]) for e in view.get("after_bloom") or []
             if isinstance(e, dict) and e.get("key") in tails and isinstance(e.get("bloom_week"), int)]
    ends = [int(e["bloom_week"]) for e in view.get("after_bloom") or []
            if isinstance(e, dict) and e.get("key") == "label_end" and isinstance(e.get("bloom_week"), int)]
    if ends:
        return min(ends) - 1  # the table's own end first: Hesi names a plain-water week 8, then ends (market 2)
    if found:
        return min(found) - 1
    tail = _dict(_dict(view.get("timeline")).get("tail"))
    if tail.get("key") in tails and isinstance(tail.get("bloom_week"), int):
        return int(tail["bloom_week"]) - 1
    weeks = [c["bloom_week"] for c in view.get("columns") or []
             if isinstance(c, dict) and isinstance(c.get("bloom_week"), int)]
    return max(weeks) if weeks else "?"


def plan_lines(state: LiveState, lang: str) -> list[str]:
    """"Wann dünge ich was?": the dated plan from feeding.view's timeline, in the active card's unit. At most five
    lines (spec 4.9); the age ask last when the start is unknown or rough."""
    lang = _lang(lang)
    view = _feed(state)
    st = view.get("state")
    unit = unit_of(view)
    timeline = _dict(view.get("timeline"))
    week = _dict(view.get("week"))
    out: list[str] = []
    if choice_needed(view):
        return [t("hank.feed.choice_needed", lang, options=_options(view, lang))]
    if st == "no_card" or not view:
        return [str(view.get("problem") or t("hank.plan.no_card", lang))]
    if st == "label_ends":
        return [t("hank.plan.label_end_now", lang, week=last_chart_week(view))]
    if st == "harvest":
        return [t("hank.plan.harvest_now", lang, week=week.get("label") or "?")]
    if st == "water_only" and view.get("not_before"):  # the owner's date for this run, not a week of the chart
        return [str(view.get("warning") or "")]
    if st == "water_only":
        out.append(t("hank.plan.water_now", lang, week=week.get("label") or "?"))
        out += _tails(view, timeline, lang)[1:]
        return out[:5]
    columns = [c for c in timeline.get("columns") or [] if isinstance(c, dict)]
    now = next((i for i, c in enumerate(columns) if c.get("status") == "now"), None)
    latest = timeline.get("dates") == "latest"
    age = _dict(timeline.get("age"))
    if st == "ok" and now is not None:
        items = amount_items(columns[now]["values"], unit, lang, spoon_key="spoons")
        if not items:
            return [t("hank.plan.no_card", lang)]
        key = "hank.plan.age_approx" if _age(state).get("approx") else "hank.plan.age"
        age_text = t(key, lang, age=plant.age_text(age, lang)) if age.get("days") is not None else ""
        out.append(t("hank.plan.today", lang, date=day_text(_today(state).isoformat(), lang),
                     week=columns[now].get("label") or week.get("label") or "?", age=age_text,
                     items=joined(items, lang)))
        changes = _changes(columns, now, unit, lang)
        for column, said in changes[:MAX_CHANGES]:
            if column.get("from"):
                out.append(t("hank.plan.change_latest" if latest else "hank.plan.change", lang,
                             date=day_text(column["from"], lang), week=column.get("label"), items=said))
            else:
                out.append(t("hank.plan.change_undated", lang, week=column.get("label")))
                break
        lines = [out[0], " ".join(out[1:])] if len(out) > 1 else out
        if len(changes) > MAX_CHANGES:
            lines[-1] = f"{lines[-1]} {t('hank.plan.more', lang)}"
        tails = _tails(view, timeline, lang)
        if tails:
            lines.append(" ".join(tails))
        factor = view.get("dose_factor")
        foot = [t("hank.plan.factor", lang, factor=fmt_num(factor, lang))] if _number(factor) else []
        if unit == "g" and view.get("measure"):  # feeding.spoons floors to half a spoon (critique 23.10.2026)
            foot.append(t("hank.plan.spoons", lang))
        foot.append(t("hank.plan.frequency", lang))
        lines.append(" ".join(foot))
        out = lines
    else:
        # draft, needs_stage: only the dates and the reason, never an amount (not even the label's numbers)
        if st == "draft":
            out.append(t("hank.plan.draft", lang, card=_dict(view.get("card")).get("name") or "?"))
        elif st == "needs_stage":
            out.append(t("hank.plan.needs_stage", lang))
        else:  # state ok without a column for today: no reason to name, no plan to show (critique 23.10.2026)
            return [t("hank.plan.no_card", lang)]
        if now is not None:
            span = _span(columns[now], lang)
            label = columns[now].get("label") or week.get("label") or "?"
            if span:
                out.append(t("hank.plan.week_now_latest" if latest else "hank.plan.week_now", lang, week=label,
                             span=span))
            else:
                out.append(t("hank.plan.week_undated", lang, week=label))
        nxt = _dict(timeline.get("next_change"))
        if nxt.get("date") and not nxt.get("tail"):
            out.append(t("hank.plan.next_date_latest" if nxt.get("latest") else "hank.plan.next_date", lang,
                         date=day_text(nxt["date"], lang), week=nxt.get("label") or "?"))
        elif nxt and not nxt.get("date") and not nxt.get("tail"):
            out.append(t("hank.plan.change_undated", lang, week=nxt.get("label") or "?"))
        tails = _tails(view, timeline, lang)
        if tails:
            out.append(" ".join(tails))
    ask = _dict(_age(state).get("ask"))
    if ask and _age(state).get("ask_text"):
        out.append(str(_age(state)["ask_text"]))
    return out[:6]


def reading(route: Route, live: LiveState | Mapping[str, Any], *, answer_id: str,
            now: float | None = None) -> Answer:
    """The Answer of a reading route of this module (asking.reading_answer hands these terms over): final code text,
    the claim's card when there is one. core.Hank registers the card (registry.add)."""
    lang = _lang(route.lang)
    state = collect(live)
    cards: list[Proposal] = []
    if route.term == "age_claim":
        claim = claim_of(route)
        if claim is None:
            lines = [t("hank.age.question_unknown", lang)]
        else:
            lines, bodies = claim_answer(claim, state, lang)
            cards = cards_for(bodies, state, lang, now=now)
            if bodies and not cards:  # the body did not pass its checks: no card, so no sentence about one
                lines = [line for line in lines if line != t("hank.age.card", lang)]
    elif route.term == "plant_age":
        lines = age_lines(state, lang)
    elif route.term == "feed_plan":
        lines = plan_lines(state, lang)
        # the same plan as a graphic under the text (owner, 23.10.2026), drawn from the same timeline
        return Answer(id=answer_id, route="reading", lang=lang, text="\n".join(lines), proposals=cards,
                      plan=plan_graphic(_feed(state), lang))
    elif route.term == "feed_have":
        return have_answer(route, state, lang, answer_id=answer_id, now=now)
    else:
        lines = choice_lines(state, lang)
    joiner = "\n" if route.term == "feed_plan" else " "
    return Answer(id=answer_id, route="reading", lang=lang, text=joiner.join(lines), proposals=cards)


# ---------- the today list ----------

def age_asked_key(state: LiveState) -> str | None:
    """What the age question is about, for asking.asked_before("start_date", ...): the reason and the start it is
    about, so a new start (or a new kind of doubt) asks again; None when nothing is to ask."""
    age = _age(state)
    ask = _dict(age.get("ask"))
    if not ask:
        return None
    return f"{ask.get('reason')}:{age.get('start') or ''}"


def age_line(state: LiveState, lang: str, *, asked: bool) -> str | None:
    """The today list's question for the plant's start, asked once per stored value (core.Hank._today), then a short
    reminder while it is still unknown or rough."""
    age = _age(state)
    ask = _dict(age.get("ask"))
    if not ask:
        return None
    if not asked and age.get("ask_text"):
        return str(age["ask_text"])
    if ask.get("reason") == "approx" and age.get("start"):
        return t("hank.today.age_remind_approx", lang, date=plant.date_text(age["start"], lang))  # as ask_text
    return t("hank.today.age_remind_unknown", lang)


def choice_line(state: LiveState, lang: str) -> str | None:
    """"Dünger wählen: BioBizz oder VIVOSUN" while several fertilizers are entered and none is chosen."""
    view = _feed(state)
    if not choice_needed(view):
        return None
    return t("hank.today.choice", lang, options=_options(view, lang))


def next_change_text(view: Mapping[str, Any], lang: str) -> str | None:
    """'Nächste Änderung: ab 19.11. (Blütewoche 5).' for the today feeding line, when the change has a date."""
    nxt = _dict(_dict(view.get("timeline")).get("next_change"))
    if not nxt.get("date"):
        return None
    week = nxt.get("label") or (_week_name(nxt["bloom_week"], lang) if nxt.get("bloom_week") else "?")
    return t("hank.today.next_change_latest" if nxt.get("latest") else "hank.today.next_change", lang,
             date=day_text(nxt["date"], lang), week=week)


# ---------- 5. "ich habe / ich nutze <Dünger>": the fertilizer market (owner, 23.10.2026) ----------
#
# The owner names the fertilizer he has; Hank names the line from the wiki (kartei/nutrients/: its type, its medium,
# how it is applied), offers the switch as a two-tap card (proposals kind feeding_setup, the page's own switch) and
# draws the plan from the plant's age and the stage the owner set (feeding.preview: the same view and timeline the
# feeding card shows, every rule unchanged). A name the wiki does not know gets no dose: Hank asks for the exact name
# and the maker as printed on the label (the chat takes no photo yet, 23.10.2026). A compound ending in "dünger" /
# "fertilizer" ("Ich nutze Superdünger 3000") names a fertilizer too. Code only, no model.

_HAVE_LEAD = re.compile(
    r"^(?:(?:hi|hallo|hey|also|ok|okay|ubrigens|btw|so|und|and|jetzt|now) )*"
    r"(?:ich (?:habe|hab|hatte|nutze|nutz|benutze|verwende|nehme|nehm|gebe|geb|dunge)"
    r"(?: (?:mit|jetzt|gerade|schon|auch|aktuell|nun|zuhause|zu hause|hier|da))*"
    r"|meine? (?P<own>\w+) (?:ist|sind|heisst)"
    r"|i (?:have|use|feed|am using|got|ve got|m using|now use|switched to)(?: (?:with|now|already|currently))*"
    r"|ive got|im using|my (?P<own_en>\w+) (?:is|are))"
    r"(?: (?P<rest>.+))?$")
_DETERMINERS = frozenset({"den", "die", "das", "einen", "eine", "ein", "der", "diesen", "dieses", "the", "a", "an",
                          "some", "von", "vom", "from", "by", "called", "namens", "marke", "brand", "auch", "jetzt",
                          "now", "mit", "with"})
_FERT_WORDS = frozenset({"dunger", "dungemittel", "nahrstoffe", "nahrstoff", "nahrstofflosung", "fertilizer",
                         "fertiliser", "fertilizers", "fertilisers", "nutrients", "nutrient", "dungerlinie",
                         "duenger", "naehrstoffe", "duengemittel"})
# words that make a sentence with a fertilizer word something else than naming one ("Ich habe zu viel Dünger
# gegeben", "I have no fertilizer"): no claim then, the normal way answers it
_NOT_A_NAME = frozenset({"zu", "viel", "wenig", "mehr", "gegeben", "gedungt", "gegossen", "kein", "keine", "keinen",
                         "nicht", "noch", "nie", "zuviel", "uberdungt", "verbrannt", "frage", "wie", "was", "wann",
                         "welche", "welchen", "warum", "too", "much", "little", "more", "no", "not", "any", "never",
                         "overfed", "burnt", "burned", "how", "what", "when", "which", "why", "question", "gelb",
                         "yellow", "problem", "problems", "probleme"})
_ALIAS_CACHE: dict[str, Any] = {"sig": None, "table": []}


@dataclass(frozen=True)
class FertClaim:
    """kind "card" (one card of the wiki), "cards" (several fit the words: Hank asks which), "unknown" (a name
    the wiki does not know); cards the card ids; name the name as the owner wrote it (unknown only)."""

    kind: str
    cards: tuple[str, ...] = ()
    name: str = ""

    def reason(self) -> str:
        if self.kind == "unknown":
            return f"unknown={self.name}"
        return f"{self.kind}={'+'.join(self.cards)}"


def _alias_table() -> list[tuple[tuple[str, ...], str]]:
    """(folded words of a name, card id) for every card of kartei/nutrients/ with a "line": its aliases in both
    languages, its name, the switch's label without the bracket, and its maker (a maker alone fits all its lines).
    Read again only when a card changed."""
    from vgrow import feeding

    folder = feeding.LIBRARY / "nutrients"
    try:
        paths = sorted(folder.glob("*.json"))
        sig = tuple((x.name, x.stat().st_mtime_ns, x.stat().st_size) for x in paths)
    except OSError:
        return []
    if _ALIAS_CACHE["sig"] == sig:
        return _ALIAS_CACHE["table"]
    table: list[tuple[tuple[str, ...], str]] = []
    for path in paths:
        card = feeding.load_card(path.stem)
        if not isinstance(card, dict) or not isinstance(card.get("line"), dict):
            continue
        names: list[str] = []
        aliases = card.get("aliases")
        if isinstance(aliases, dict):
            for forms in aliases.values():
                names += [f for f in forms if isinstance(f, str)] if isinstance(forms, list) else []
        names += [str(card.get(k)) for k in ("name", "name_en") if card.get(k)]
        sel = card.get("selectable") if isinstance(card.get("selectable"), dict) else {}
        names += [str(sel.get(k)).split(" (")[0] for k in ("label", "label_en") if sel.get(k)]
        maker = str(card["line"].get("maker") or "").split(" (")[0]
        if maker:
            names.append(maker)
        for name in names:
            found = tuple(_words(name))
            if found and (found, path.stem) not in table:
                table.append((found, path.stem))
    _ALIAS_CACHE.update(sig=sig, table=table)
    return table


def _same(word: str, name: str) -> bool:
    """A word of the question is a word of the name: the same, or one typo away in a name word of six letters or
    more ("Plagorn" is Plagron; "Terra" stays exact, "Tera" is not "Terra")."""
    if word == name:
        return True
    from .typo import osa

    return len(name) >= 6 and abs(len(word) - len(name)) <= 1 and osa(word, name, 1) <= 1


def _fert_word(word: str) -> bool:
    return any(_same(word, f) for f in _FERT_WORDS)


_FERT_SUFFIXES = ("dunger", "fertilizer", "fertiliser")  # folded: "Superdünger", "Superduenger", "Superfertilizer"


def _fert_compound(word: str) -> bool:
    """A compound name ending in a fertilizer word ("Superdünger", "Blumendünger", "MegaFertilizer"): it says the
    name is a fertilizer and stays part of the name (fertilizer market 2, 23.10.2026)."""
    return any(word.endswith(f) and len(word) > len(f) for f in _FERT_SUFFIXES) and not _fert_word(word)


def _find(rest: list[str], table: list[tuple[tuple[str, ...], str]]) -> tuple[str, ...]:
    """The cards whose longest name stands in the words, as a contiguous run; several on a tie."""
    best, cards = 0, []
    for name, card in table:
        n = len(name)
        if n < best or n > len(rest):
            continue
        if any(all(_same(w, x) for w, x in zip(rest[i:i + n], name, strict=True))
               for i in range(len(rest) - n + 1)):
            if n > best:
                best, cards = n, []
            if card not in cards:
                cards.append(card)
    return tuple(cards)


def _original_name(q: str, folded: list[str]) -> str:
    """The name in the owner's own spelling: the run of the question's words that folds to `folded`."""
    raw = re.findall(r"[^\W_][\w+&.%-]*", str(q or ""))
    keyed = [(w, (_words(w) or [""])[0]) for w in raw]
    n = len(folded)
    for i in range(len(keyed) - n + 1):
        if [k for _, k in keyed[i:i + n]] == folded:
            return " ".join(w for w, _ in keyed[i:i + n]).strip(" .,;:!?")
    return " ".join(folded)


def fertilizer_claim(q: str) -> FertClaim | None:
    """"Ich habe BioBizz", "Ich nutze Canna Terra", "I use Plagron Terra", "Mein Dünger ist ...": the fertilizer the
    owner names, or None. A name of the wiki's cards (aliases, name, maker) gives the card or, on a tie, the cards to
    ask about; an unknown name counts only beside a fertilizer word ("Ich nutze den Dünger von X") and without words
    that make it a question or a problem ("Ich habe zu viel Dünger gegeben" is no claim)."""
    found = _HAVE_LEAD.match(" ".join(_words(q)))
    if not found or not found.group("rest"):
        return None
    own = found.group("own") or found.group("own_en")
    if own is not None and not (_fert_word(own) or _fert_compound(own)):
        return None  # "Meine Pflanze ist ...", "My plant is ...": no fertilizer named
    rest = found.group("rest").split()
    cards = _find(rest, _alias_table())
    if cards:
        return FertClaim("card" if len(cards) == 1 else "cards", cards)
    # "Mein Dünger ist X": the lead itself says it is a fertilizer; so does a compound ("Ich nutze Superdünger 3000")
    named = any(_fert_word(w) or _fert_compound(w) for w in _words(q))
    if not named or set(rest) & _NOT_A_NAME or len(rest) > 7:
        return None
    name = [w for w in rest if not _fert_word(w) and w not in _DETERMINERS]
    return FertClaim("unknown", (), _original_name(q, name) if name else "")


def claim_from(route: Route) -> FertClaim | None:
    found = re.search(r"feed_have:(card|cards|unknown)=(.*)$", route.reason or "")
    if not found:
        return None
    kind, value = found.groups()
    if kind == "unknown":
        return FertClaim("unknown", (), value)
    return FertClaim(kind, tuple(v for v in value.split("+") if v))


def _first_sentences(text: Any, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", str(text or "").strip())
    return " ".join(p for p in parts[:n] if p)


def _card_name(cid: str, lang: str) -> str:
    from vgrow import feeding

    card = feeding.load_card(cid)
    shown = i18n.localize(card, lang) if card else {}
    return str(shown.get("name") or cid)


def _setup_for(cid: str) -> str | None:
    """The setup id that doses this card (plant.json's own or the library's), or None: not selectable."""
    from vgrow import feeding

    try:
        setups = feeding.setups_of(plant.load_profile())
    except Exception:  # noqa: BLE001 (an unreadable plant.json: no switch, never a crash)
        return None
    return next((sid for sid, own in setups.items() if own.get("card") == cid), None)


def _sources_of(card: Mapping[str, Any], lang: str) -> list[Any]:
    """The card's chart sources (weeks.sources), else its first source, as SourceRefs for the answer's source line."""
    from .types import SourceRef

    table = card.get("sources") if isinstance(card.get("sources"), dict) else {}
    ids = [s for s in ((card.get("weeks") or {}).get("sources") or []) if s in table] or list(table)[:1]
    out = []
    for sid in ids:
        src = i18n.localize(table[sid], lang) if isinstance(table[sid], dict) else {"title": str(table[sid])}
        url = src.get("url") if isinstance(src.get("url"), str) and src["url"].startswith("https://") else None
        out.append(SourceRef(sid, str(src.get("title") or sid), url))
    return out


def have_answer(route: Route, state: LiveState, lang: str, *, answer_id: str, now: float | None = None) -> Answer:
    """The answer to "ich nutze <Dünger>": the line, its type and how it is applied, the switch as a two-tap card
    when it is not in use, and the plan as a graphic. Never an amount the feeding planner did not compute."""
    from vgrow import feeding

    claim = claim_from(route)
    if claim is None or claim.kind == "unknown":
        name = claim.name if claim else ""
        text = t("hank.have.unknown", lang, name=name) if name else t("hank.have.unknown_noname", lang)
        return Answer(id=answer_id, route="reading", lang=lang, text=text, gap=True,
                      link={"href": "#wiki/duenger", "label": t("hank.have.wiki", lang)})
    if claim.kind == "cards":
        names = [_card_name(c, lang) for c in claim.cards]
        word = " oder " if lang == "de" else " or "
        options = names[0] if len(names) == 1 else ", ".join(names[:-1]) + word + names[-1]
        return Answer(id=answer_id, route="reading", lang=lang, text=t("hank.have.ambiguous", lang, options=options),
                      link={"href": "#wiki/duenger", "label": t("hank.have.wiki", lang)})
    cid = claim.cards[0]
    card = feeding.load_card(cid) or {}
    shown = i18n.localize(card, lang)
    line = shown.get("line") if isinstance(shown.get("line"), dict) else {}
    # the switch's own name ("BioBizz", "VIVOSUN Base A + B"), else the card's ("BioTabs Organic System")
    label = str(_dict(shown.get("selectable")).get("label") or "").split(" (")[0].strip()
    name = label or str(shown.get("name") or cid)
    types = [t(f"hank.have.type.{k}", lang) for k in line.get("types") or [] if f"hank.have.type.{k}" in DE]
    media = [t(f"hank.have.media.{m}", lang) for m in line.get("media") or [] if f"hank.have.media.{m}" in DE]
    lines = [t("hank.have.head", lang, name=name, types=joined(types, lang) or "?",
               media=t("hank.have.media", lang, list=joined(media, lang)) if media else t("hank.have.media_none",
                                                                                            lang))]
    how = str(line.get("application_short") or "") or _first_sentences(line.get("application"))
    if how:
        lines.append(t("hank.have.applied", lang, text=how))
    cards: list[Proposal] = []
    plan = None
    sid = _setup_for(cid)
    if sid is None and not feeding.for_soil(card):
        # a coco or hydro line (fertilizer market 2): said why it is not offered, never a plan for the soil
        lines.append(t("hank.have.not_soil", lang, media=joined(media, lang) or "?"))
    elif sid is None and not feeding.plan_checked(card):
        lines.append(t("hank.have.unchecked", lang))
    elif sid is None:
        lines.append(t("hank.have.no_plan", lang))
    else:
        options = [o for o in _dict(_feed(state).get("choice")).get("options") or [] if isinstance(o, dict)]
        active = next((o.get("id") for o in options if o.get("active")), None)
        try:
            view = feeding.preview(sid, lang, today=_today(state))
        except Exception:  # noqa: BLE001 (a broken card or plant log: no plan, the rest of the answer stands)
            view = None
        if active == sid:
            lines.append(t("hank.have.active", lang, name=name))
        else:
            try:
                cards.append(proposals.make({"action": "feeding_setup", "setup": sid}, state, lang,
                                            reason="feed_have", now=now))
                lines.append(t("hank.have.switch", lang, name=name))
            except proposals.Refused:
                pass
        if view:
            plan = plan_graphic(view, lang)
            st = view.get("state")
            if plan is not None and st == "ok":
                lines.append(t("hank.have.plan_ok", lang, factor=fmt_num(view.get("dose_factor"), lang)))
            elif plan is not None and st in ("draft", "needs_stage"):
                why = t("hank.have.why_draft" if st == "draft" else "hank.have.why_stage", lang)
                lines.append(t("hank.have.plan_chart", lang, why=why))
            elif plan is not None and st in ("water_only", "harvest", "label_ends"):
                lines.append(t("hank.have.plan_after", lang, week=_dict(view.get("week")).get("label") or "?"))
    return Answer(id=answer_id, route="reading", lang=lang, text=" ".join(lines), proposals=cards,
                  sources=_sources_of(card, lang), plan=plan,
                  link={"href": f"#wiki/nutrients/{cid}", "label": t("hank.have.link", lang)})


def _cell(value: Any, lang: str, digits: int | None) -> str:
    return fmt_num(value, lang, digits) if _number(value) else ""


def plan_graphic(view: Mapping[str, Any] | None, lang: str) -> dict[str, Any] | None:
    """The feeding plan as a table for the chat (owner, 23.10.2026: "baut dafür auch eine Grafik, wie und wann was
    gemacht werden soll"): from feeding.view's timeline, the same data as the page's plan. Columns from this week on,
    then what follows the chart (plain water, harvest, the chart's end); one row per product the setup holds; each
    cell the amount per litre (state ok, rounded down by the planner) or else only whether the product is due that
    week, never the label's number (draft, needs_stage: the factor is not applied yet, said so in the caption).
    Today, plain-water, flush and empty weeks are marked. None without a timeline (no card, no choice)."""
    view = _dict(view)
    timeline = _dict(view.get("timeline"))
    columns = [c for c in timeline.get("columns") or [] if isinstance(c, dict)]
    if not timeline or view.get("state") in ("no_card", "needs_choice", None):
        return None
    lang = _lang(lang)
    amounts = view.get("state") == "ok"
    unit = unit_of(view)
    digits = view.get("amount_digits") if amounts and isinstance(view.get("amount_digits"), int) else None
    now = next((i for i, c in enumerate(columns) if c.get("status") == "now"), None)
    shown = columns[now:] if now is not None else []
    chart = [r for r in view.get("chart") or [] if isinstance(r, dict)]
    flush = [r for r in chart if "flush" in str(r.get("component") or "")]
    keys = [c.get("key") for c in view.get("columns") or [] if isinstance(c, dict)]
    head: list[dict[str, Any]] = []
    rows: dict[str, dict[str, Any]] = {}
    for column in shown:
        values = [v for v in column.get("values") or [] if isinstance(v, dict)]
        cells = {}
        for v in values:
            rows.setdefault(str(v.get("component")), {"label": str(v.get("label") or v.get("component")),
                                                      "cells": []})
            # draft, needs_stage: whether the product is due this week, never the label's number (critique
            # 23.10.2026, M1: "Terra Vega 5,4" read as ml/l although the factor 0.5 was not applied)
            if amounts:
                text = _cell(v.get("per_liter"), lang, digits)
            else:
                text = t("hank.plan.g_due", lang) if _number(v.get("chart")) else ""
            sub = spoons_text(v.get("spoons"), lang) if amounts and unit == "g" else None
            cells[str(v.get("component"))] = {"text": text, **({"sub": sub} if sub else {})}
        index = keys.index(column.get("key")) if column.get("key") in keys else -1
        kind = "feed"
        flushed = any(0 <= index < len(r.get("values") or []) and _number((r.get("values") or [])[index])
                      for r in flush)
        fed = any(c["text"] for comp, c in cells.items() if "flush" not in comp)
        if flushed and not fed:
            kind = "flush"
        elif not any(c["text"] for c in cells.values()):
            kind = "nothing"
        # flush next to a full base feed (CANNA Terra W7): no "flush" mark over a week that feeds, the chart's
        # own oddity said instead (critique 23.10.2026, S4)
        mark = {"flush": "hank.plan.g_flush", "nothing": "hank.plan.g_nothing"}.get(kind)
        if flushed and fed:
            mark = "hank.plan.g_flush_with"
        label = t("hank.plan.g_bloom", lang, n=column["bloom_week"]) if isinstance(column.get("bloom_week"), int) \
            else str(column.get("label") or column.get("key") or "").split(" (")[0]
        subs = []
        if column.get("from"):
            subs.append(t("hank.plan.g_from", lang, date=day_text(column["from"], lang)))
        if isinstance(column.get("life_week"), int):
            subs.append(t("hank.plan.g_week", lang, n=column["life_week"]))
        head.append({"label": label, "sub": " · ".join(subs), "now": column.get("status") == "now", "kind": kind,
                     "mark": t(mark, lang) if mark else ""})
        for comp, row in rows.items():
            row["cells"].append(cells.get(comp, {"text": ""}))
    start = plant.parse_day(view.get("bloom_start"))
    week = _dict(view.get("week"))
    for entry in view.get("after_bloom") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("bloom_week"), int):
            continue
        key = entry.get("key")
        kind = {"water": "water", "harvest": "harvest", "label_end": "end"}.get(str(key), "end")
        day = (start + timedelta(days=7 * (entry["bloom_week"] - 1))).isoformat() if start else None
        head.append({"label": t("hank.plan.g_bloom", lang, n=entry["bloom_week"]),
                     "sub": t("hank.plan.g_from", lang, date=day_text(day, lang)) if day else "",
                     "now": week.get("key") == key, "kind": kind,
                     "mark": t({"water": "hank.plan.g_water", "harvest": "hank.plan.g_harvest"}.get(kind,
                                                                                                "hank.plan.g_end"),
                               lang)})
        for row in rows.values():
            row["cells"].append({"text": ""})
    if not head:
        return None
    factor = view.get("dose_factor")
    if amounts:
        caption = t("hank.plan.g_amounts", lang, unit=f"{unit}/l", factor=fmt_num(factor, lang))
    elif _number(factor):
        caption = t("hank.plan.g_chart", lang, factor=fmt_num(factor, lang))
    else:
        caption = t("hank.plan.g_chart_nofactor", lang)
    notes = []
    age = _dict(timeline.get("age"))
    stage = _dict(view.get("week_source")).get("stage")
    # the plant's age always when it is known, the stage only when one is set (critique 23.10.2026, S3)
    if age.get("days") is not None and stage:
        notes.append(t("hank.plan.g_where", lang, age=plant.age_text(age, lang), stage=stage))
    elif age.get("days") is not None:
        notes.append(t("hank.plan.g_age", lang, age=plant.age_text(age, lang)))
    elif stage:
        notes.append(t("hank.plan.g_where_stage", lang, stage=stage))
    if timeline.get("dates") == "latest":
        notes.append(t("hank.plan.g_latest", lang))
    return {"title": t("hank.plan.g_title", lang, name=_dict(view.get("card")).get("name") or "?"),
            "caption": caption, "notes": notes, "product": t("hank.plan.g_product", lang),
            "today": t("hank.plan.g_today", lang), "basis": "amount" if amounts else "chart", "unit": unit,
            "head": head, "rows": list(rows.values())}


__all__ = ["DE", "EN", "TERMS", "AgeClaim", "FertClaim", "active_fact", "fertilizer_claim", "have_answer",
           "plan_graphic", "age_asked_key", "age_claim", "age_line", "age_lines",
           "amount_items", "asks_feed_choice", "asks_feed_plan", "asks_plant_age", "card_body", "card_label",
           "cards_for_claim", "check", "choice_line", "choice_lines", "claim_answer", "claim_fact", "claim_of",
           "implied_start", "item", "last_chart_week", "next_change_text", "plan_lines", "pure_claim", "quantity", "reading",
           "register", "route_for", "spoons_text", "unit_of"]
