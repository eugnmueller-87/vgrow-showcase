"""Feeding planner: a nutrient chart from kartei/nutrients/, the run's setup from plant.json ("feeding"), the
owner's stage from the plant log, and the owner's confirmation and feeding log in the state folder.

The card holds the manufacturer's chart number for number, in the card's own unit per litre of water ("ml/l" for
BioBizz, "g/l" for the VIVOSUN label): pages (soil types), rows (products), columns (propagation, vegetative,
flowering weeks 1 to 8), then what the card says after the last flowering column: plain water and harvest
(BioBizz), or the end of the label's table (VIVOSUN, weeks.end). The setup names the card, the page, the products
the owner has and a dose factor; it may hold a bloom_start the owner wrote in on purpose, and a bloom_start_estimate
(from a camera picture or the AI reading) that never doses.

Never above the chart: the dose per litre is chart value x dose factor (the factor is at most 1), computed exactly
with fractions and rounded DOWN, to 0.1 ml for ml cards and to 0.01 g for g cards; the total for a watering is that
exact dose x litres, rounded down the same way. A g card with a measuring spoon (card "measure": {"grams": 0.5},
the label's "1 spoon=0.5g") also gets spoons, computed from the exact grams and rounded down to half a spoon, so
spoons x grams per spoon is never above the gram figure shown. Amounts only ever come from here: feeding_log takes
litres from the page, never amounts.

Units (owner, 23.10.2026): ml stays ml; an old log line without "unit" is ml and stays readable. Each log line names
its own card and unit, and the last feeding is shown in that line's unit, never in the active card's.

Setups (owner, 23.10.2026: BioBizz and VIVOSUN Base A+B in the house): plant.json's "feeding" may hold several
"setups" (card, page, page note, products); the shared keys around them (dose factor, bloom start, estimate,
litres limit) apply to every setup. Only ONE setup doses at a time: the owner picks it on the page (two taps), it is
stored in the plant log under STATE_DIR (PlantLog.set_feeding_choice), never in plant.json. Until a choice is made
the state is needs_choice and nothing is computed. The flat layout (no "setups") is one setup, as before. The
owner's confirmation is kept per card (feeding.json {"cards": {card id: record}}), so confirming one card never
confirms the other, and switching back to a confirmed card needs no new confirmation while its numbers stay the
same.

The week (review of 22.10.2026: the chart week must follow the stage the owner set, never an estimate):
- A stage the owner set before flowering: seedling takes the propagation column when the card has one (the VIVOSUN
  seedling dose is half the vegetative one), veg the vegetative column, whatever else says flowering.
- A flowering stage the owner set (early, mid or late flower, harvest window): flowering weeks count from bloom_start
  in plant.json if the owner put one there, else from the day the owner set the first stage of this flowering spell.
- No stage from the owner: a bloom_start in plant.json counts (the owner wrote it). bloom_start_estimate and nothing
  at all do not: the state is needs_stage, the chart shows from the estimated week on, and amounts wait until the
  owner confirms the stage, as the humidifier automation does.
- The owner's own bloom start, entered on the plant card (plant log, plant.owner_bloom_start; owner 23.10.2026:
  the camera put it on the day the photo reading began, the autoflower had flowered longer): it counts before
  plant.json's bloom_start and before the day a flowering stage was set, never against a stage before flowering.
  It reaches the planner in the stage (plant.current_stage: bloom_by "owner", bloom_start), kind "owner".
- A bloom_start in plant.json that does not fit the owner's stage gives a warning on the page.
Flowering weeks: week = days // 7 + 1 from bloom_start. Before bloom_start (or without one) the vegetative column;
weeks 1 to 8 their column; then BioBizz: week 9 plain water, from week 10 nothing; a card without "water"
(VIVOSUN): from week 9 the state label_ends, no amount (the label's table stops after flowering week 8, its text
names use until the end of flowering without an amount); a card whose harvest follows its last column (Plagron:
week 9 harvest, plain water only) is harvest from there (has_tail).

The plant's age (plant.age_view) never changes a dose. It only makes the schedule readable: timeline() gives every
column its dates (from the bloom start), the plant week on its first day, the current column, folded past columns,
the next change of dose with its date, and the tail after the last column.

The confirmation covers what the owner compared with the bottles: card, version, unit, page, the page's rows for the
products the owner has, the columns, the products and the dose factor (plus the spoon size and the table's end on a
card that has them), as one fingerprint. Any change there (a corrected number, another page, a new version) is a new
draft until the owner confirms again. A feeding_log from the page carries the card and the fingerprint it showed and
is refused when they are no longer what the server uses (a stale tab never logs with another card).

Litres: 0.1 to 20 per watering. Above confirm_above_liters (plant.json "feeding", default 5) the page asks a second
time and sends large, so a typo like 15 instead of 1.5 never turns into amounts unnoticed.

States: no_card (nothing usable configured; "problem" says why), needs_choice (several setups, none chosen: no
amounts), draft (the owner has not confirmed the card: the chart and its sources, but no amounts), needs_stage (card
confirmed, the week is not: no amounts), ok (amounts), water_only (BioBizz week 9), harvest (BioBizz from week 10),
label_ends (a card whose table ends: no amount).
The chart says nothing about how often to feed, and neither does the planner.
Displayed texts carry English twins (_en); view() hands out one language.

Interfaces for Hank (part 2 wired them: vgrow/hank/feedplan.py, context.py, today.py, wiring.py):
- view()["timeline"]: the dated plan (see timeline()); view()["age"]: plant.age_view as handed in;
- view()["amount_unit"] ("ml" or "g") and view()["amount_digits"]; doses carry spoons_per_liter on g cards;
- view()["card"]["id"] and view()["setup"]: the active card and setup; view()["choice"]: the options and why only one;
- view()["last_fed"]["unit"] / ["digits"] / ["other_card"]: the last line in its own unit;
- context(profile, log, lang): the keyword arguments view() needs (stage, owner_stages, age, setups);
- setup_of(profile) without a log reads the state folder's plant log, so a caller without one sees the choice;
- view(amount_units=...): the amount units the CALLER can show. The default is LEGACY_UNITS ("ml",), what Hank's
  part-1 code understands (it writes "ml" after every dose, vgrow/hank/context.py and today.py). For a card in
  another unit (VIVOSUN: g and spoons) such a caller gets state no_card, problem feeding.unit_withheld and
  "withheld": the unit, and no chart, doses, plan, timeline or notes, so Hank never says "0,75 ml" for grams and
  shows no VIVOSUN number before its own gates exist. The page passes PAGE_UNITS (server.feeding_week), and so
  does Hank's wiring since part 2 (he reads amount_unit, amount_digits and spoons_per_liter; the gates name VIVOSUN);
- view()["week"]["note"]: on a column whose label counts its own weeks (card "label_weeks": [1, 4]) while the plant
  is older than that, the sentence that the label names only those weeks and the cockpit stays in this column.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import config
from . import plant as plant_mod
from .i18n import UserError, current_lang, fmt_num, localize, normalize, t
from .plant import FLOWERING_STAGES, LIBRARY, PRE_FLOWER_STAGES, PlantLog, age_info, parse_number
from .plant import flowering_since as _flowering_since

_LOGGER = logging.getLogger(__name__)

STATES = ("no_card", "needs_choice", "draft", "needs_stage", "ok", "water_only", "harvest", "label_ends")
MIN_LITERS, MAX_LITERS = Fraction(1, 10), Fraction(20)  # any watering can; outside this the litres are refused
# The cockpit's own typo guard (15 instead of 1.5), not a sourced number: more than this needs a second tap.
CONFIRM_ABOVE_LITERS = Fraction(5)
CARD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # a file name in kartei/nutrients/, nothing that walks paths
ONE = Fraction(1)
# The rounding step per amount unit, always DOWN: 0.1 ml as before; 0.01 g, because 0.75 g shown as "0,8 g" would be
# more than computed. The cockpit's own display steps, not from a label.
UNITS = {"ml": Fraction(1, 10), "g": Fraction(1, 100)}
# The amount units a caller of view() can show. LEGACY_UNITS: a caller written for ml only (Hank's part-1 code);
# PAGE_UNITS: the cockpit page, which shows each amount in its own unit and in spoons.
LEGACY_UNITS = ("ml",)
PAGE_UNITS = tuple(UNITS)
DIGITS = {"ml": 1, "g": 2}  # decimals the page shows per unit; every value is a whole multiple of the step
# Spoons are shown in half spoons, rounded DOWN from the exact grams: the cockpit's own convention, not from a label
# (the label only says "1 spoon=0.5g").
SPOON_STEP = Fraction(1, 2)
END_KEY = "label_end"  # the week key after the last column of a card without plain water (weeks.end)
# Stage keys of kartei/stages/: before flowering the vegetative column; from early flower on the flowering weeks.
PRE_FLOWER = PRE_FLOWER_STAGES  # ("seedling", "veg"), kept in plant.py
FLOWERING = FLOWERING_STAGES  # ("early_flower", "mid_flower", "late_flower", "harvest")
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_FROM_STATE = object()  # setup_of(profile) without a log: read the state folder's plant log
_UNCHECKED = object()  # log_feeding without the page's card and fingerprint (a direct call, not the page)


class FeedingRefused(Exception):
    """A feeding action that does not fit the current state (no card, not confirmed, harvest): HTTP 409."""


def local_today() -> date:
    """The server's calendar day; tests replace it, so a week does not depend on when they run."""
    return date.today()


# ---------- pure computation ----------

def floor_to(value: Fraction, step: Fraction) -> Fraction:
    """value rounded DOWN to a whole multiple of step: never more than computed."""
    return Fraction(math.floor(value / step)) * step


def tenth_down(value: Fraction) -> Fraction:
    """Rounded down to 0.1: never more than computed."""
    return floor_to(value, Fraction(1, 10))


def exact(value: Any) -> Fraction | None:
    """A JSON number as an exact fraction (0.1 stays 1/10); None for null, booleans, text, NaN and infinity."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return Fraction(str(value))
    except (ValueError, OverflowError):
        return None


def ml(value: Fraction) -> float:
    """An amount for JSON, rounded down to 0.1 ml."""
    return float(tenth_down(value))


def amount(value: Fraction, unit: str = "ml") -> float:
    """An amount for JSON in the card's unit, rounded down to that unit's step (0.1 ml, 0.01 g)."""
    return float(floor_to(value, UNITS[unit]))


def amount_unit(card: dict[str, Any] | None) -> str | None:
    """The unit of an amount: the head of the card's "<u>/l" when the cockpit computes it ("ml", "g"), else None."""
    unit = (card or {}).get("unit")
    if not isinstance(unit, str) or not unit.endswith("/l"):
        return None
    head = unit[:-2]
    return head if head in UNITS else None


def measure_grams(card: dict[str, Any] | None) -> Fraction | None:
    """Grams per measuring spoon (card "measure": {"grams": 0.5}), exact; only on a g card, else None."""
    if amount_unit(card) != "g":
        return None
    grams = exact(((card or {}).get("measure") or {}).get("grams"))
    return grams if grams is not None and grams > 0 else None


def spoons(grams: Fraction, per_spoon: Fraction | None) -> float | None:
    """Spoons for an exact gram amount, rounded DOWN to half a spoon; None without a spoon size or below half a
    spoon (then only the scale helps: the page says so)."""
    if per_spoon is None:
        return None
    count = floor_to(grams / per_spoon, SPOON_STEP)
    return float(count) if count > 0 else None


def bloom_keys(card: dict[str, Any]) -> list[str]:
    """The flowering columns, week 1 first."""
    columns = [c for c in (card.get("weeks") or {}).get("columns", []) if isinstance(c.get("bloom_week"), int)]
    return [c["key"] for c in sorted(columns, key=lambda c: c["bloom_week"])]


def harvest_week(card: dict[str, Any]) -> int:
    """The first flowering week with nothing at all: the card's harvest week, else the end of its table."""
    weeks = card.get("weeks") or {}
    found = (weeks.get("harvest") or {}).get("bloom_week") or (weeks.get("end") or {}).get("bloom_week")
    return int(found or len(bloom_keys(card)) + 2)


def has_tail(card: dict[str, Any]) -> bool:
    """Whether the card itself says what follows its last flowering column: a plain-water week (BioBizz), or a
    harvest right after it (Plagron: "harvest, plain water only" in week 9). Only then are the weeks after the
    table plain water and harvest; otherwise they are the end of the table (VIVOSUN, CANNA: label_ends). Critique
    23.10.2026, M2: Plagron in week 9 read "the table ends" although its card says harvest."""
    weeks = card.get("weeks") or {}
    if isinstance(weeks.get("water"), dict):
        return True
    harvest = weeks.get("harvest") if isinstance(weeks.get("harvest"), dict) else None
    return harvest is not None and "end" not in weeks and harvest.get("bloom_week") == len(bloom_keys(card)) + 1


def ends_after_water(card: dict[str, Any]) -> bool:
    """A plain-water week and then the end of the table, no harvest named (Hesi SOIL: "flush with pure water" in
    week 8, then nothing): the weeks after the water week are label_ends, never a harvest the plan does not print."""
    weeks = card.get("weeks") or {}
    return isinstance(weeks.get("water"), dict) and isinstance(weeks.get("end"), dict) \
        and not isinstance(weeks.get("harvest"), dict)


def current_week(bloom_start: date | None, today: date, keys: list[str], harvest: int, *, water: bool = True,
                 pre_key: str = "veg", end_after_water: bool = False) -> dict[str, Any]:
    """Where the plant stands in the chart today. keys: the flowering columns in order; harvest: the first week
    with nothing at all. The weeks between the last flowering column and harvest are plain water. water=False (the
    card has no plain-water week): every week after the last flowering column is the end of the table (phase "end").
    end_after_water: after the water weeks the table ends (phase "end") instead of a harvest.
    pre_key: the column before flowering ("veg", or "propagation" for a seedling)."""
    if bloom_start is None or today < bloom_start:
        return {"key": pre_key, "phase": "veg", "bloom_week": None, "day": None}
    day = (today - bloom_start).days
    week = day // 7 + 1
    if week <= len(keys):
        return {"key": keys[week - 1], "phase": "bloom", "bloom_week": week, "day": day}
    if not water:
        return {"key": END_KEY, "phase": "end", "bloom_week": week, "day": day}
    if week < harvest:
        return {"key": "water", "phase": "water", "bloom_week": week, "day": day}
    if end_after_water:
        return {"key": END_KEY, "phase": "end", "bloom_week": week, "day": day}
    return {"key": "harvest", "phase": "harvest", "bloom_week": week, "day": day}


def doses(page: dict[str, Any], owned: list[str], column: int, factor: Fraction) -> list[dict[str, Any]]:
    """The owner's products with a chart value in this column: the chart value and the exact amount per litre,
    chart x factor with the factor capped at 1, so never above the chart."""
    rows = page.get("rows") or {}
    out = []
    for component in owned:
        values = rows.get(component)
        if not isinstance(values, list) or not 0 <= column < len(values):
            continue
        chart = exact(values[column])
        if chart is None or chart <= 0:
            continue  # "-" in the chart: not used this week
        out.append({"component": component, "chart": values[column], "exact": chart * min(factor, ONE)})
    return out


def totals(items: list[dict[str, Any]], liters: Fraction, unit: str = "ml",
           per_spoon: Fraction | None = None) -> list[dict[str, Any]]:
    """Per litre and for the whole watering, both rounded down from the exact dose (0.1 ml, 0.01 g). With a spoon
    size (g cards) each row also carries spoons_per_liter and spoons_total, from the EXACT grams, rounded down to half
    a spoon: half spoons are whole multiples of 0.01 g, so spoons x grams per spoon never exceeds the gram figure."""
    rows = []
    for d in items:
        row = {"component": d["component"], "chart": d["chart"], "per_liter": amount(d["exact"], unit),
               "total": amount(d["exact"] * liters, unit)}
        if per_spoon is not None:
            row.update(spoons_per_liter=spoons(d["exact"], per_spoon),
                       spoons_total=spoons(d["exact"] * liters, per_spoon))
        rows.append(row)
    return rows


def parse_bloom_start(value: Any) -> date | None:
    """None when there is none; ValueError when it is not a YYYY-MM-DD date."""
    if value in (None, ""):
        return None
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _day(value: Any) -> date | None:
    try:
        return parse_bloom_start(value)
    except ValueError:
        return None


def flowering_since(owner_stages: Iterable[dict[str, Any]]) -> date | None:
    """The first day of the owner's current flowering spell (plant.flowering_since)."""
    return _flowering_since(owner_stages)


def owner_date(stage: dict[str, Any] | None) -> date | None:
    """The bloom start the owner entered on the plant card, as plant.current_stage hands it on in the stage."""
    if not stage or stage.get("bloom_by") != "owner":
        return None
    return _day(stage.get("bloom_start"))


def week_source(plan: date | None, estimate: date | None, stage: dict[str, Any] | None,
                owner_stages: Iterable[dict[str, Any]], today: date, owner: date | None = None) -> dict[str, Any]:
    """Where the chart week comes from. kind: "owner" (the bloom start the owner entered on the plant card), "stage"
    (the owner's stage), "plan" (bloom_start in plant.json), "estimate" (bloom_start_estimate) or "none" (nothing
    known: the vegetative column, assumed). confirmed: the owner stands behind it, so amounts may follow. bloom_start:
    the date flowering weeks count from (None: before flowering). pre: the column before flowering ("propagation" for
    a seedling the owner set, else "veg"). warning: catalog key and values when a bloom_start in plant.json does not
    fit the owner's stage."""
    manual = stage if stage and stage.get("set_by") == "manual" else None
    key = (manual or {}).get("key")
    label = (manual or {}).get("label") or key
    if key in PRE_FLOWER:
        # The owner's own date counts before plant.json's; it reaches here in the stage (owner_bloom_start), since a
        # stage before flowering hands on no bloom start (review 2, 23.10.2026: the date was ignored without a word).
        entered = _day((stage or {}).get("owner_bloom_start"))
        if entered is not None and entered <= today:
            warning = ("feeding.stage_before_owner_bloom", {"stage": label, "date": entered})
        elif plan is not None and plan <= today:
            warning = ("feeding.stage_before_bloom", {"stage": label, "date": plan})
        else:
            warning = None
        # A seedling takes the propagation column (VIVOSUN: 0.5 g/l, half the vegetative 1 g/l); evaluate falls back
        # to the vegetative column on a card without one.
        return {"kind": "stage", "confirmed": True, "bloom_start": None, "stage": label, "warning": warning,
                "pre": "propagation" if key == "seedling" else "veg"}
    if owner is not None:
        return {"kind": "owner", "confirmed": True, "bloom_start": owner, "stage": label, "warning": None, "pre": "veg"}
    if key in FLOWERING:
        if plan is not None:
            return {"kind": "plan", "confirmed": True, "bloom_start": plan, "stage": label, "warning": None,
                    "pre": "veg"}
        since = flowering_since(owner_stages)
        if since is not None:
            return {"kind": "stage", "confirmed": True, "bloom_start": since, "stage": label, "warning": None,
                    "pre": "veg"}
    if plan is not None:
        return {"kind": "plan", "confirmed": True, "bloom_start": plan, "stage": None, "warning": None, "pre": "veg"}
    if estimate is not None:
        return {"kind": "estimate", "confirmed": False, "bloom_start": estimate, "stage": None, "warning": None,
                "pre": "veg"}
    return {"kind": "none", "confirmed": False, "bloom_start": None, "stage": None, "warning": None, "pre": "veg"}


def stage_misfit(stage: dict[str, Any] | None, week: dict[str, Any],
                 bloom_start: date | None) -> tuple[str, dict[str, Any]] | None:
    """The owner's flowering stage against a chart week that does not fit it: still vegetative, or a harvest window
    while the chart still feeds. A stage before flowering always gets the vegetative column (week_source)."""
    if not stage or stage.get("set_by") != "manual" or bloom_start is None:
        return None
    key = stage.get("key")
    if (key in FLOWERING and week["phase"] == "veg") or (key == "harvest" and week["phase"] == "bloom"):
        return "feeding.stage_misfit", {"stage": stage.get("label") or key, "week": week, "date": bloom_start}
    return None


def _column_keys(card: dict[str, Any]) -> list[Any]:
    return [c.get("key") for c in (card.get("weeks") or {}).get("columns", []) if isinstance(c, dict)]


def evaluate(setup: dict[str, Any] | None, card: dict[str, Any] | None, confirmed: bool, today: date,
             stage: dict[str, Any] | None = None, owner_stages: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Today's plan in the card's own terms, without texts: state, problem (catalog key and values), the week, its
    source, a warning, the page key, the factor and the doses (exact fractions; None in needs_choice, draft,
    needs_stage and no_card, [] when nothing to add)."""
    out: dict[str, Any] = {"state": "no_card", "problem": None, "week": None, "page": None, "factor": None,
                           "doses": None, "source": None, "warning": None, "not_before": None}
    if not setup or not isinstance(setup, dict):
        out["problem"] = ("feeding.none", {})
        return out
    card_id = setup.get("card")
    if card is None:
        out["problem"] = ("feeding.no_card", {"card": card_id})
        return out
    page_key = setup.get("page")
    page = (card.get("pages") or {}).get(page_key)
    if not isinstance(page, dict):
        out["problem"] = ("feeding.no_page", {"card": card_id, "page": page_key})
        return out
    if amount_unit(card) is None:
        out["problem"] = ("feeding.bad_unit", {"card": card_id, "unit": card.get("unit")})
        return out
    factor = exact(setup.get("dose_factor"))
    if factor is None or not 0 < factor <= 1:
        out["problem"] = ("feeding.bad_factor", {})
        return out
    dates = {}
    for field in ("bloom_start", "bloom_start_estimate"):
        try:
            dates[field] = parse_bloom_start(setup.get(field))
        except ValueError:
            out["problem"] = ("feeding.bad_date", {"field": field})
            return out
    if setup.get("choice_pending"):
        out.update(state="needs_choice", page=page_key, factor=factor)  # several setups, none chosen: no amounts
        return out
    source = week_source(dates["bloom_start"], dates["bloom_start_estimate"], stage, owner_stages, today,
                         owner_date(stage))
    columns = _column_keys(card)
    pre_key = source["pre"] if source["pre"] in columns else "veg"
    week = current_week(source["bloom_start"], today, bloom_keys(card), harvest_week(card),
                        water=has_tail(card), pre_key=pre_key, end_after_water=ends_after_water(card))
    out.update(page=page_key, factor=factor, week=week, source=source,
               warning=source["warning"] or stage_misfit(stage, week, source["bloom_start"]))
    if not confirmed:
        out["state"] = "draft"  # the chart and its sources, no amounts
        return out
    if not source["confirmed"]:
        out["state"] = "needs_stage"  # the card is confirmed, the week is only an estimate: still no amounts
        return out
    # The owner's first day with fertilizer for this run (plant log feed_from, through setup_of; owner, 06.11.2026: "I would
    # start with the fertilizer in 2 weeks"): plain water until then, whatever the chart says for this week.
    not_before = _day(setup.get("not_before"))
    if not_before is not None and today < not_before:
        out.update(state="water_only", doses=[], not_before=not_before, warning=("feeding.not_before", {"date": not_before}))
        return out
    if week["phase"] in ("water", "harvest", "end"):
        out.update(state={"water": "water_only", "harvest": "harvest", "end": "label_ends"}[week["phase"]], doses=[])
        return out
    column = columns.index(week["key"]) if week["key"] in columns else -1  # no such column: nothing to add
    owned = [c for c in setup.get("components") or [] if isinstance(c, str)]
    out.update(state="ok", doses=doses(page, owned, column, factor))
    return out


def confirm_above(setup: dict[str, Any] | None) -> Fraction:
    """Litres above which a watering needs the second tap: plant.json's confirm_above_liters, else the default."""
    value = exact((setup or {}).get("confirm_above_liters"))
    return value if value is not None and MIN_LITERS <= value <= MAX_LITERS else CONFIRM_ABOVE_LITERS


def parse_liters(value: Any, *, large: bool = False, above: Fraction | None = None) -> Fraction:
    """Litres from the page ("1,5" is fine), as an exact fraction within MIN_LITERS..MAX_LITERS. More than `above`
    (default CONFIRM_ABOVE_LITERS) only with large: the page's second tap."""
    number = parse_number(value)
    if number is None or not math.isfinite(number) or not MIN_LITERS <= Fraction(str(number)) <= MAX_LITERS:
        raise UserError(t("feeding.liters_range", min=fmt_num(float(MIN_LITERS)), max=fmt_num(float(MAX_LITERS))))
    amount_ = Fraction(str(number))
    limit = CONFIRM_ABOVE_LITERS if above is None else above
    if amount_ > limit and not large:
        raise UserError(t("feeding.liters_large", liters=fmt_num(float(amount_)), limit=fmt_num(float(limit))))
    return amount_


# ---------- the card, the setups, the confirmation, the log ----------

def load_card(card_id: Any, library: Path = LIBRARY) -> dict[str, Any] | None:
    if not isinstance(card_id, str) or not CARD_ID.fullmatch(card_id):
        return None
    try:
        data = json.loads((library / "nutrients" / f"{card_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number_key(value: Any) -> str | None:
    """A chart value as written exactly (3 and 3.0 are the same number), None for "-"."""
    found = exact(value)
    return None if found is None else str(found)


def fingerprint(setup: dict[str, Any] | None, card: dict[str, Any] | None) -> str | None:
    """What the owner compares with the bottles, as one hash: card, version, unit, page, the page's rows for the
    products the owner has, the columns, the products and the dose factor; on a card that has them also the spoon size
    (measure.grams) and the week the table ends (weeks.end), left out otherwise so a card without them keeps the hash
    its confirmation was made with. None without a usable card and page."""
    if not isinstance(setup, dict) or not isinstance(card, dict):
        return None
    page_key = setup.get("page")
    page = (card.get("pages") or {}).get(page_key)
    if not isinstance(page, dict):
        return None
    owned = [c for c in setup.get("components") or [] if isinstance(c, str)]
    rows = page.get("rows") or {}
    weeks = card.get("weeks") or {}
    factor = exact(setup.get("dose_factor"))
    data = {
        "card": setup.get("card"), "version": card.get("version"), "unit": card.get("unit"), "page": page_key,
        "components": owned,
        "rows": {c: [_number_key(v) for v in rows[c]] if isinstance(rows.get(c), list) else None for c in owned},
        "columns": [[c.get("key"), c.get("bloom_week")] for c in weeks.get("columns", []) if isinstance(c, dict)],
        "water": (weeks.get("water") or {}).get("bloom_week"),
        "harvest": (weeks.get("harvest") or {}).get("bloom_week"),
        "dose_factor": None if factor is None else str(factor),
    }
    if "measure" in card:
        data["measure"] = _number_key((card.get("measure") or {}).get("grams"))
    if "end" in weeks:
        data["end"] = (weeks.get("end") or {}).get("bloom_week")
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def confirmation_path() -> Path:
    return config.STATE_DIR / "feeding.json"


def log_path() -> Path:
    return config.STATE_DIR / "feeding-log.jsonl"


def _confirmations(strict: bool = False) -> dict[str, dict[str, Any]]:
    """Every card's confirmation: {"cards": {card id: record}}. A flat file from before setups ({"card": ...,
    "fingerprint": ...}) counts as that one card's record. A missing file is no confirmation. An unreadable one reads
    as none too, except with strict (confirm, which writes the file back): then FeedingRefused, because writing one
    card's record over a broken file would silently drop the other card's confirmation (review N1)."""
    path = confirmation_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        if strict:
            raise FeedingRefused(t("feeding.confirmations_broken", file=path.name)) from err
        return {}
    if not isinstance(data, dict):
        if strict:
            raise FeedingRefused(t("feeding.confirmations_broken", file=path.name))
        return {}
    cards = data.get("cards")
    out = {k: v for k, v in cards.items() if isinstance(k, str) and isinstance(v, dict)} \
        if isinstance(cards, dict) else {}
    if isinstance(data.get("card"), str) and data["card"] not in out:
        out[data["card"]] = {k: v for k, v in data.items() if k != "cards"}
    return out


def read_confirmation(card_id: Any = None) -> dict[str, Any] | None:
    """With a card id: {"card", "version", "page", "fingerprint", "confirmed_at"} once the owner has confirmed that
    card, else None. Without: the whole map {card id: record}, None when empty."""
    cards = _confirmations()
    if card_id is None:
        return cards or None
    found = cards.get(str(card_id))
    return found if isinstance(found, dict) and found.get("card", card_id) == card_id else None


def is_confirmed(setup: dict[str, Any] | None, card: dict[str, Any] | None) -> bool:
    """The owner confirmed exactly what the setup and the card say now: the same card and the same fingerprint.
    A confirmation from before fingerprints (card id only) counts as none. Per card: BioBizz never confirms VIVOSUN."""
    current = fingerprint(setup, card)
    if current is None:
        return False
    record = read_confirmation((setup or {}).get("card")) or {}
    return record.get("fingerprint") == current


_SELECT_KEYS = ("label", "label_en", "page", "page_note", "page_note_en")
_LIBRARY_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, dict[str, Any]]]] = {}


def for_soil(card: dict[str, Any] | None) -> bool:
    """Whether the card's line may be used on soil, the owner's medium: False only when the line names its media
    and soil is not among them (a coco or hydro line stands in the wiki with its medium, never in the cockpit's
    switch; fertilizer market 2, 23.10.2026). A label that names no medium at all (VIVOSUN: "das Etikett nennt kein
    Medium") is not called a soil line, and not shut out either. A medium written as text instead of a list
    ("coco") is read as that one medium, never as "no medium named"."""
    line = (card or {}).get("line")
    media = line.get("media") if isinstance(line, dict) else None
    if isinstance(media, str):
        media = [media] if media.strip() else []
    return not (isinstance(media, list) and media and "soil" not in media)


def plan_checked(card: dict[str, Any] | None) -> bool:
    """Whether the card's plan may dose: False while the card says its rows were not compared with the maker's
    original ("check": {"state": "unchecked"}, "nicht gegen das Original geprüft"). A card without "check" keeps
    what it was before the field existed."""
    check = (card or {}).get("check")
    return not (isinstance(check, dict) and check.get("state") == "unchecked")


def _selectable(card_id: str, card: dict[str, Any] | None) -> dict[str, Any] | None:
    """The setup a card offers ("selectable": its page, the products, the switch's label), or None: a card without a
    readable chart (no "selectable", a page it does not have, a product its page has no row for, a unit the planner
    does not compute) is never selectable, and neither is a line not made for soil or a plan not checked against
    the maker's original (owner, 23.10.2026)."""
    if not isinstance(card, dict):
        return None
    if not for_soil(card) or not plan_checked(card):
        return None
    sel = card.get("selectable")
    if not isinstance(sel, dict) or amount_unit(card) is None:
        return None
    page = (card.get("pages") or {}).get(sel.get("page"))
    owned = sel.get("components")
    if not isinstance(page, dict) or not isinstance(owned, list) or not owned:
        return None
    rows = page.get("rows") or {}
    if not all(isinstance(c, str) and isinstance(rows.get(c), list) for c in owned):
        return None
    out = {k: sel[k] for k in _SELECT_KEYS if isinstance(sel.get(k), str)}
    # "library_card", not "from_library": that one is plant.json's shared switch and reaches every setup
    return {**out, "card": card_id, "components": list(owned), "library_card": True}


def library_setups(library: Path = LIBRARY) -> dict[str, dict[str, Any]]:
    """Every nutrient card of kartei/nutrients/ that has a readable chart ("selectable"), as a setup by its card id
    (owner, 23.10.2026: "jede Karte mit Plan wählbar"), in file order. Read again only when a file changed; the
    caller gets copies. Nothing is written anywhere."""
    folder = library / "nutrients"
    try:
        paths = sorted(folder.glob("*.json"))
        sig = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in paths)
    except OSError:
        return {}
    hit = _LIBRARY_CACHE.get(str(folder))
    if hit is None or hit[0] != sig:
        found: dict[str, dict[str, Any]] = {}
        for path in paths:
            if not CARD_ID.fullmatch(path.stem):
                continue
            own = _selectable(path.stem, load_card(path.stem, library))
            if own is not None:
                found[path.stem] = own
        hit = (sig, found)
        _LIBRARY_CACHE[str(folder)] = hit
    return json.loads(json.dumps(hit[1]))


def setups_of(profile: dict[str, Any] | None, library: Path = LIBRARY) -> dict[str, dict[str, Any]]:
    """plant.json's setups by id, each merged with the shared "feeding" keys (dose factor, bloom start, estimate,
    litres limit) and carrying its id as "setup". The flat layout (no "setups") is one setup, "default", unchanged.
    {} without a "feeding" object. With "from_library": true beside the setups, every selectable card of
    kartei/nutrients/ joins after them under its card id (library_setups), unless a setup of plant.json already names
    that card; the shared keys hold for them too, so the factor and every rule of the planner are the same."""
    feed = (profile or {}).get("feeding")
    if not isinstance(feed, dict):
        return {}
    setups = feed.get("setups")
    if not isinstance(setups, dict) or not setups:
        return {"default": feed}
    shared = {k: v for k, v in feed.items() if k != "setups"}
    out = {str(sid): {**shared, **own, "setup": str(sid)} for sid, own in setups.items() if isinstance(own, dict)}
    if feed.get("from_library") is True:
        named = {own.get("card") for own in out.values()}
        for cid, own in library_setups(library).items():
            if cid not in out and cid not in named:
                out[cid] = {**shared, **own, "setup": cid}
    return out


def choice_of(log: PlantLog | None, profile: dict[str, Any] | None = None) -> str | None:
    """The setup the owner chose (plant log), or None. An id plant.json no longer has counts as no choice, never a
    crash."""
    entry = log.data.get("feeding_choice") if log is not None else None
    chosen = entry.get("setup") if isinstance(entry, dict) else None
    if not isinstance(chosen, str):
        return None
    return chosen if profile is None or chosen in setups_of(profile) else None


def setup_of(profile: dict[str, Any] | None, log: Any = _FROM_STATE) -> dict[str, Any] | None:
    """The ONE setup the planner doses, or None without a "feeding" object. One setup (or the flat layout): that one.
    Several: the one the owner chose; none chosen (or a stale id): the first one with "choice_pending": True, which
    evaluate turns into needs_choice, no amounts. Without a log argument the state folder's plant log is read, so a
    caller that has none (Hank's wiring in part 1) still sees the owner's choice; log=None means no choice."""
    setups = setups_of(profile)
    if not setups:
        return None
    if log is _FROM_STATE:
        log = PlantLog()
    wait = log.data.get("feed_from") if log is not None else None  # the owner's first day with fertilizer
    if len(setups) == 1:
        found = next(iter(setups.values()))
        return {**found, "not_before": wait} if wait else found
    chosen = choice_of(log, profile)
    if chosen is not None:
        return {**setups[chosen], "not_before": wait} if wait else setups[chosen]
    pending = {**next(iter(setups.values())), "choice_pending": True}
    return {**pending, "not_before": wait} if wait else pending


def _card_name(card: dict[str, Any] | None, card_id: Any) -> str:
    """The card's name in the request's language for a message to the owner; the file id only without a card."""
    return str(localize(card, current_lang()).get("name") or card_id) if card else str(card_id)


def _state_for(setup: dict[str, Any] | None, library: Path, today: date | None, stage: dict[str, Any] | None = None,
               owner_stages: Iterable[dict[str, Any]] = ()) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    setup = setup if isinstance(setup, dict) else None
    card_id = (setup or {}).get("card")
    card = load_card(card_id, library) if card_id else None
    return card, evaluate(setup, card, is_confirmed(setup, card), today or local_today(), stage, owner_stages)


def confirm(setup: dict[str, Any] | None, card_from_page: Any, fingerprint_from_page: Any, *,
            library: Path = LIBRARY, now: datetime | None = None) -> dict[str, Any]:
    """The owner has checked the card the page showed: from now on the planner computes amounts for it. The page sends
    the card id and the fingerprint it displayed; both must be what the server uses now, so a tap on an old page, or
    on numbers that changed since, never confirms a chart the owner did not see. Kept per card: the other card's
    confirmation stays as it is."""
    card, plan = _state_for(setup, library, now.date() if now else None)
    if plan["state"] == "no_card":
        key, params = plan["problem"]
        raise FeedingRefused(t(key, **params))
    if plan["state"] == "needs_choice":
        raise FeedingRefused(t("feeding.needs_choice"))
    card_id = str((setup or {})["card"])
    if card_from_page is None or str(card_from_page) != card_id:
        raise FeedingRefused(t("feeding.wrong_card", card=_card_name(card, card_id)))
    current = fingerprint(setup, card)
    if not fingerprint_from_page or str(fingerprint_from_page) != current:
        raise FeedingRefused(t("feeding.changed"))
    record = {"card": card_id, "version": (card or {}).get("version"), "page": (setup or {}).get("page"),
              "fingerprint": current, "confirmed_at": (now or datetime.now()).isoformat(timespec="seconds")}
    cards = _confirmations(strict=True)
    cards[card_id] = record  # add or replace only this card's record
    config.atomic_write(confirmation_path(), json.dumps({"cards": cards}, indent=2) + "\n")
    _LOGGER.info("Feeding card %s confirmed (page %s)", card_id, record["page"])
    return record


def log_feeding(setup: dict[str, Any] | None, liters: Any, *, large: bool = False, stage: dict[str, Any] | None = None,
                owner_stages: Iterable[dict[str, Any]] = (), library: Path = LIBRARY, today: date | None = None,
                now: datetime | None = None, lang: str | None = None, page_card: Any = _UNCHECKED,
                page_fingerprint: Any = _UNCHECKED, **_context: Any) -> dict[str, Any]:
    """One watering with feed: the amounts are computed here from the chart, never taken from the page. page_card and
    page_fingerprint: what the page showed (the server always passes them); a card or numbers that are no longer what
    the server uses are refused, so a stale tab never logs with another card. **_context takes the rest of
    context() (age, setups), which a log line does not need."""
    amount_ = parse_liters(liters, large=large, above=confirm_above(setup))
    card, plan = _state_for(setup, library, today, stage, owner_stages)
    if plan["state"] == "no_card":
        key, params = plan["problem"]
        raise FeedingRefused(t(key, **params))
    if plan["state"] == "needs_choice":
        raise FeedingRefused(t("feeding.needs_choice"))
    card_id = (setup or {}).get("card")
    if page_card is not _UNCHECKED:
        if page_card is None or str(page_card) != str(card_id):
            raise FeedingRefused(t("feeding.wrong_card", card=_card_name(card, card_id)))
        if not page_fingerprint or str(page_fingerprint) != fingerprint(setup, card):
            raise FeedingRefused(t("feeding.changed"))
    if plan["state"] == "draft":
        raise FeedingRefused(t("feeding.not_confirmed"))
    if plan["state"] == "needs_stage":
        raise FeedingRefused(t("feeding.needs_stage"))
    if plan["state"] == "harvest":
        raise FeedingRefused(t("feeding.harvest", week=harvest_week(card or {})))
    if plan["state"] == "label_ends":
        raise FeedingRefused(t("feeding.label_ends", week=harvest_week(card or {})))
    unit = amount_unit(card) or "ml"
    source = plan["source"]
    entry = {"time": (now or datetime.now()).isoformat(timespec="seconds"), "card": card_id,
             "setup": (setup or {}).get("setup"), "unit": unit,
             "page": plan["page"], "week": plan["week"]["key"], "bloom_week": plan["week"]["bloom_week"],
             "week_source": source["kind"],
             "bloom_start": source["bloom_start"].isoformat() if source["bloom_start"] else None,
             "dose_factor": float(plan["factor"]), "liters": float(amount_), "state": plan["state"],
             "items": totals(plan["doses"], amount_, unit, measure_grams(card))}
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with log_path().open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return _entry_view(entry, card_id, lang, library)


def last_entry() -> dict[str, Any] | None:
    """The newest line of the feeding log; the file only grows, so only its tail is read."""
    try:
        with log_path().open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # the first line of the tail may be cut
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            return entry
    return None


def use_between(start: float, end: float, lang: str | None = None) -> dict[str, Any]:
    """What the feeding log says was given in [start, end), for the grow report: the litres of water with feed, and per
    product its total in the line's own unit. A line that does not read is left out."""
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    liters, items = 0.0, {}
    for line in lines:
        try:
            entry = json.loads(line)
            at = datetime.fromisoformat(str(entry.get("time"))).timestamp()
            given = float(entry.get("liters") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
        if not start <= at < end:
            continue
        liters += given
        view = _entry_view(entry, entry.get("card"), lang)
        for item in view["items"]:
            if isinstance(item.get("total"), int | float):
                row = items.setdefault((entry.get("card"), item["component"], view["unit"]),
                                       {"label": item["label"], "total": 0.0, "unit": view["unit"]})
                row["total"] = round(row["total"] + item["total"], 2)
    return {"liters": round(liters, 2), "items": list(items.values())}


# ---------- the page's view ----------

def _labels(card: dict[str, Any] | None) -> dict[str, str]:
    return {k: v.get("label", k) for k, v in ((card or {}).get("components") or {}).items() if isinstance(v, dict)}


def _tails(weeks: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """What a card says after its last flowering column, in order: plain water, harvest, the end of the table."""
    found = [(k, weeks[k]) for k in ("water", "harvest", "end") if isinstance(weeks.get(k), dict)]
    return sorted(found, key=lambda kv: kv[1].get("bloom_week") or 0)


def _week_label(card: dict[str, Any] | None, key: str | None) -> str | None:
    weeks = (card or {}).get("weeks") or {}
    for column in weeks.get("columns", []):
        if column.get("key") == key:
            return column.get("label")
    for name, entry in _tails(weeks):
        if key in (name, entry.get("key")):
            return entry.get("label")
    return None


def _entry_view(entry: dict[str, Any], active_card: Any, lang: str | None = None,
                library: Path = LIBRARY) -> dict[str, Any]:
    """A log line for the page, in the line's OWN card and unit: an old line without "unit" is ml (critique 23.10.2026:
    a BioBizz line must never read as grams while VIVOSUN is active, nor a g line be rounded to 0.1). other_card: the
    line belongs to another card than the active one; card_name names it."""
    card_id = entry.get("card")
    own = load_card(card_id, library) if card_id else None
    shown = localize(own, lang) if own else None
    unit = entry.get("unit") if entry.get("unit") in UNITS else "ml"
    labels = _labels(shown)
    items = []
    for i in entry.get("items", []):
        if not isinstance(i, dict):
            continue
        item = {"component": i.get("component"), "label": labels.get(i.get("component"), i.get("component")),
                "per_liter": i.get("per_liter"), "total": i.get("total")}
        for key in ("spoons_per_liter", "spoons_total"):
            if key in i:
                item[key] = i[key]
        items.append(item)
    return {"time": entry.get("time"), "liters": entry.get("liters"), "state": entry.get("state"),
            "card": card_id, "card_name": (shown or {}).get("name"), "unit": unit, "digits": DIGITS[unit],
            "other_card": active_card is not None and card_id is not None and card_id != active_card,
            "week": {"key": entry.get("week"), "bloom_week": entry.get("bloom_week"),
                     "label": _week_label(shown, entry.get("week"))},
            "items": items}


def _week_note(card: dict[str, Any] | None, week: dict[str, Any] | None, age: dict[str, Any] | None,
               lang: str) -> str | None:
    """A column before flowering whose label counts its own weeks (card "label_weeks": [1, 4], "Vegetative Week 1-4")
    while the plant's week (plant.age_view life_week) is past the last of them: the label names only those weeks,
    and the cockpit stays in this column until the owner sets another stage (review S8). Never moves a column or a
    dose; None otherwise."""
    if not week or week.get("phase") != "veg":
        return None
    column = next((c for c in ((card or {}).get("weeks") or {}).get("columns", [])
                   if isinstance(c, dict) and c.get("key") == week.get("key")), None)
    span = (column or {}).get("label_weeks")
    life = (age or {}).get("life_week")
    if not (isinstance(span, list) and len(span) == 2 and all(isinstance(n, int) for n in span)):
        return None
    if not isinstance(life, int) or isinstance(life, bool) or life <= span[1]:
        return None
    return t("feeding.week_span", lang, first=span[0], last=span[1])  # the plant week stands beside it


def _date_text(day: date, lang: str) -> str:
    """As the page writes a date: 22.10.2026 in German, Oct 22, 2026 in English."""
    return f"{day:%d.%m.%Y}" if lang == "de" else f"{MONTHS_EN[day.month - 1]} {day.day}, {day.year}"


def _warning_text(warning: tuple[str, dict[str, Any]] | None, card: dict[str, Any] | None, lang: str) -> str | None:
    """A warning in the page's language: the date as the page writes it, the week by its chart label."""
    if not warning:
        return None
    key, params = warning
    values = dict(params)
    if isinstance(values.get("date"), date):
        values["date"] = _date_text(values["date"], lang)
    if isinstance(values.get("week"), dict):
        values["week"] = _week_label(card, values["week"].get("key")) or values["week"].get("key")
    return t(key, lang, **values)


def _life_week(start: date | None, day: date | None) -> int | None:
    info = age_info(start, day) if start is not None and day is not None else None
    return info["life_week"] if info else None


def timeline(card: dict[str, Any] | None, setup: dict[str, Any] | None, plan: dict[str, Any], today: date,
             start: date | None = None) -> dict[str, Any] | None:
    """The plant's own dated plan from the shown (localized) card and the evaluate() result. The age never moves a
    column: columns follow the stage and the bloom start; the start only adds plant weeks.

    {"dates": "exact" | "latest" | "none",   latest: the bloom start is only an upper bound (the camera's estimate, or
                                             the day the owner set the first flowering stage)
     "age": plant.age_info today | None,
     "columns": [{key, label, printed, bloom_week, from, to, life_week, status: "past" | "now" | "later",
                  values: [{component, label, chart, per_liter, spoons}]}],
     "next_change": {date, key, label, bloom_week, when, changes: [{component, label, from, to}], basis, latest,
                     tail} | None,
     "tail": {key, label, bloom_week, from} | None}
    Flowering columns run from bloom_start + 7 (w - 1) days for 7 days; propagation and veg have no dates (their
    length is open). per_liter and spoons (chart x factor, floored) only in state ok; otherwise the label's chart
    values only, and next_change.basis says "chart". next_change: the first later column whose doses differ from the
    current one (when: "bloom_start" or "stage" if that column has no date yet), else the tail. None in no_card and
    needs_choice."""
    if card is None or plan.get("week") is None or plan["state"] in ("no_card", "needs_choice"):
        return None
    weeks = card.get("weeks") or {}
    columns = [c for c in weeks.get("columns", []) if isinstance(c, dict)]
    rows = (((card.get("pages") or {}).get(plan["page"]) or {}).get("rows")) or {}
    owned = [c for c in (setup or {}).get("components") or [] if isinstance(c, str)]
    labels = _labels(card)
    unit = amount_unit(card) or "ml"
    per_spoon = measure_grams(card)
    source = plan["source"] or {}
    bloom = source.get("bloom_start")
    # latest: the camera's estimate, and the day the owner set the first flowering stage (plant.bloom_start kind
    # "stage"): both are upper bounds of the real flip, so a column's date holds at the latest (critique 23.10.2026)
    dates = "none" if bloom is None else "latest" if source.get("kind") in ("estimate", "stage") else "exact"
    amounts = plan["state"] == "ok"
    factor = min(plan["factor"], ONE)
    week = plan["week"]
    keys = [c.get("key") for c in columns]
    now = keys.index(week["key"]) if week["key"] in keys else None
    after = week["phase"] in ("water", "harvest", "end")

    def cell(component: str, i: int) -> dict[str, Any]:
        values = rows.get(component)
        raw = values[i] if isinstance(values, list) and i < len(values) else None
        chart = exact(raw)
        used = chart is not None and chart > 0
        dose = chart * factor if used else None  # type: ignore[operator]
        return {"component": component, "label": labels.get(component, component), "chart": raw if used else None,
                "per_liter": amount(dose, unit) if amounts and dose is not None else None,
                "spoons": spoons(dose, per_spoon) if amounts and dose is not None else None, "_chart": chart if used
                else None}

    out_columns = []
    for i, column in enumerate(columns):
        bloom_week = column.get("bloom_week")
        first = bloom + timedelta(days=7 * (bloom_week - 1)) if bloom is not None and isinstance(bloom_week, int) \
            else None
        status = "past" if after or (now is not None and i < now) else "now" if i == now else "later"
        out_columns.append({"key": column.get("key"), "label": column.get("label"), "printed": column.get("printed"),
                            "bloom_week": bloom_week, "from": first.isoformat() if first else None,
                            "to": (first + timedelta(days=6)).isoformat() if first else None,
                            "life_week": _life_week(start, first), "status": status,
                            "values": [cell(c, i) for c in owned]})
    tails = _tails(weeks)
    tail = None
    if tails:
        name, entry = tails[0]
        bloom_week = entry.get("bloom_week")
        first = bloom + timedelta(days=7 * (bloom_week - 1)) if bloom is not None and isinstance(bloom_week, int) \
            else None
        tail = {"key": entry.get("key", name), "label": entry.get("label"), "bloom_week": bloom_week,
                "from": first.isoformat() if first else None, "life_week": _life_week(start, first)}
    next_change = None
    if now is not None and not after:
        current = {v["component"]: v["_chart"] for v in out_columns[now]["values"]}
        for column in out_columns[now + 1:]:
            if {v["component"]: v["_chart"] for v in column["values"]} == current:
                continue
            was = {v["component"]: v for v in out_columns[now]["values"]}
            changes = []
            for v in column["values"]:
                old = was[v["component"]]
                if old["_chart"] == v["_chart"]:
                    continue
                pick = "per_liter" if amounts else "chart"
                changes.append({"component": v["component"], "label": v["label"], "from": old[pick], "to": v[pick],
                                "spoons_from": old["spoons"], "spoons_to": v["spoons"]})
            when = None if column["from"] else ("bloom_start" if column["bloom_week"] else "stage")
            next_change = {"date": column["from"], "key": column["key"], "label": column["label"],
                           "bloom_week": column["bloom_week"], "life_week": column["life_week"], "when": when,
                           "changes": changes, "basis": "amount" if amounts else "chart",
                           "latest": dates == "latest" and column["from"] is not None, "tail": False}
            break
        if next_change is None and tail is not None:
            next_change = {"date": tail["from"], "key": tail["key"], "label": tail["label"],
                           "bloom_week": tail["bloom_week"], "life_week": tail["life_week"],
                           "when": None if tail["from"] else "bloom_start", "changes": [],
                           "basis": "amount" if amounts else "chart",
                           "latest": dates == "latest" and tail["from"] is not None, "tail": True}
    for column in out_columns:
        for v in column["values"]:
            del v["_chart"]
    return {"dates": dates, "age": age_info(start, today) if start else None, "columns": out_columns,
            "next_change": next_change, "tail": tail}


def _choice(setups: dict[str, dict[str, Any]], active: str | None, lang: str, library: Path) -> dict[str, Any]:
    """The fertilizer switch: every setup with its card, unit and whether its card is confirmed; why only one."""
    options = []
    for sid, own in setups.items():
        card = load_card(own.get("card"), library)
        shown = localize(own, lang)
        options.append({"id": sid, "label": shown.get("label") or (localize(card, lang) if card else {}).get("name")
                        or sid, "card": own.get("card"),
                        "card_name": (localize(card, lang) if card else {}).get("name"),
                        "unit": amount_unit(card), "confirmed": is_confirmed(own, card), "active": sid == active,
                        # a card of the wiki's fertilizer market (from_library), not one of plant.json's own setups
                        "library": own.get("library_card") is True})
    return {"needed": len(options) > 1 and active is None, "active": active, "why": t("feeding.one_card_only", lang),
            "options": options}


def context(profile: dict[str, Any] | None, log: PlantLog, lang: str | None = None) -> dict[str, Any]:
    """The keyword arguments view() needs besides the setup: the current stage (localized, for its warnings), the
    stages the owner set for this run (where flowering weeks count from), the plant's age (plant.age_view) and every
    setup (for the switch). The server and, in part 2, Hank's wiring call this instead of collecting them twice."""
    if not profile:
        return {"stage": None, "owner_stages": [], "age": None, "setups": {}}
    lang = normalize(lang) or current_lang()
    stage = plant_mod.current_stage(profile, log)
    return {"stage": localize(stage, lang) if stage else None, "owner_stages": plant_mod.owner_stages(profile, log),
            "age": plant_mod.age_view(profile, log, lang), "setups": setups_of(profile)}


def view(setup: dict[str, Any] | None, lang: str | None = None, *, today: date | None = None, liters: Any = None,
         large: bool = False, stage: dict[str, Any] | None = None, owner_stages: Iterable[dict[str, Any]] = (),
         library: Path = LIBRARY, age: dict[str, Any] | None = None,
         setups: dict[str, dict[str, Any]] | None = None,
         amount_units: Iterable[str] = LEGACY_UNITS) -> dict[str, Any]:
    """plant.feeding for the page, in one language. stage and owner_stages (plant.current_stage, plant.owner_stages)
    say where the week comes from. With liters (GET /api/feeding?liters=), "plan" holds the amounts for that
    watering; UserError for litres out of range, or above confirm_above_liters without large. age: plant.age_view
    (its start dates the timeline's plant weeks; it never changes a dose). setups: setups_of(profile), for the
    switch between fertilizers. amount_units: the units the caller can show (LEGACY_UNITS by default, the page passes
    PAGE_UNITS); a card in another unit gives the caller no number at all (see _withheld)."""
    lang = normalize(lang) or current_lang()
    setup = setup if isinstance(setup, dict) else None
    amount_ = parse_liters(liters, large=large, above=confirm_above(setup)) if liters not in (None, "") else None
    owner_stages = list(owner_stages)
    today = today or local_today()
    card, plan = _state_for(setup, library, today, stage, owner_stages)
    state = plan["state"]
    shown = localize(card, lang) if card else None
    shown_setup = localize(setup or {}, lang)
    card_id = (setup or {}).get("card")
    confirmation = read_confirmation(card_id) if card_id else None
    confirmed = is_confirmed(setup, card)
    labels = _labels(shown)
    unit = amount_unit(card) or "ml"
    per_spoon = measure_grams(card)
    page = ((shown or {}).get("pages") or {}).get(plan["page"]) if plan["page"] else None
    owned = [c for c in (setup or {}).get("components") or [] if isinstance(c, str)]
    weeks = (shown or {}).get("weeks") or {}
    columns = weeks.get("columns", [])
    week = plan["week"]
    now_index = next((i for i, c in enumerate(columns) if week and c.get("key") == week["key"]), None)
    chart = []
    for component, values in ((page or {}).get("rows") or {}).items():
        if not isinstance(values, list):
            continue
        optional = bool(((shown or {}).get("components") or {}).get(component, {}).get("optional"))
        chart.append({"component": component, "label": labels.get(component, component), "optional": optional,
                      "owned": component in owned, "hidden_by_default": optional and component not in owned,
                      "values": values,
                      "now": values[now_index] if now_index is not None and now_index < len(values) else None})
    items = plan["doses"]
    dose_list = None if items is None else [
        {"component": d["component"], "label": labels.get(d["component"], d["component"]), "chart": d["chart"],
         "per_liter": amount(d["exact"], unit),
         **({"spoons_per_liter": spoons(d["exact"], per_spoon)} if per_spoon is not None else {})} for d in items]
    watering = None
    if amount_ is not None and state in ("ok", "water_only"):
        watering = {"liters": float(amount_), "unit": unit,
                    "items": [{**row, "label": labels.get(row["component"], row["component"])}
                              for row in totals(items or [], amount_, unit, per_spoon)]}
    last = last_entry()
    key, params = plan["problem"] or (None, {})
    source = plan["source"]
    bloom_start = source["bloom_start"].isoformat() if source and source["bloom_start"] else None
    active = None if (setup or {}).get("choice_pending") else (setup or {}).get("setup")
    all_setups = setups if setups else ({str(active or "default"): setup} if setup else {})
    measure = (shown or {}).get("measure") if per_spoon is not None else None
    start = _day((age or {}).get("start"))
    out = {
        "state": state,
        "problem": t(key, lang, **params) if key else None,
        "setup": active,
        # The switch between fertilizers (owner, 23.10.2026): one doses at a time, the owner picks it with two taps.
        "choice": _choice(all_setups, active if len(all_setups) > 1 else next(iter(all_setups), None), lang, library),
        "card": None if shown is None else {
            "id": card_id, "name": shown.get("name"), "product": shown.get("product"),
            "version": shown.get("version"), "status": shown.get("status"), "status_note": shown.get("status_note")},
        "fingerprint": fingerprint(setup, card),  # what feeding_confirm and feeding_log send back
        # A confirmation of this card that no longer fits: the numbers changed since the owner compared them.
        "reconfirm": bool(not confirmed and confirmation),
        "page": None if page is None else {"key": plan["page"], "label": page.get("label"),
                                           "note": shown_setup.get("page_note"), "sources": page.get("sources", [])},
        "confirmed": confirmed,
        "confirmed_at": (confirmation or {}).get("confirmed_at") if confirmed else None,
        "dose_factor": None if plan["factor"] is None else float(plan["factor"]),
        "dose_factor_note": shown_setup.get("dose_factor_note"),
        "dose_factor_sources": shown_setup.get("dose_factor_sources", []),
        # Where this week comes from: kind stage, plan, estimate or none; stage is the owner's stage behind it.
        "week_source": None if source is None else {"kind": source["kind"], "confirmed": source["confirmed"],
                                                    "stage": source["stage"], "bloom_start": bloom_start},
        "bloom_start": bloom_start,
        "bloom_start_note": shown_setup.get("bloom_start_note"),
        "bloom_start_sources": shown_setup.get("bloom_start_sources", []),
        # The estimate's own words only while the estimate is what the week counts from.
        "bloom_estimate_note": shown_setup.get("bloom_start_estimate_note")
        if source and source["kind"] == "estimate" else None,
        "warning": _warning_text(plan["warning"], shown, lang),
        # note: the label counts this column's own weeks and the plant is older (review S8).
        "week": None if week is None else {**week, "label": _week_label(shown, week["key"]),
                                           "note": _week_note(shown, week, age, lang)},
        "unit": (shown or {}).get("unit"),
        # The amounts' unit and decimals: ml with 1, g with 2 (0.75 g must never show as "0,8 g").
        "amount_unit": unit,
        "amount_digits": DIGITS[unit],
        # The label's own measure (g cards with a spoon): amounts also in half spoons, rounded down.
        "measure": None if measure is None else {"grams": float(per_spoon), "spoon_step": float(SPOON_STEP),
                                                 "label": measure.get("label"), "text": measure.get("text"),
                                                 "printed": measure.get("printed"),
                                                 "sources": measure.get("sources", [])},
        "columns": [{"key": c.get("key"), "label": c.get("label"), "bloom_week": c.get("bloom_week"),
                     "printed": c.get("printed")} for c in columns],
        "after_bloom": [{"key": entry.get("key", k), "label": entry.get("label"), "bloom_week": entry.get("bloom_week")}
                        for k, entry in _tails(weeks)],
        "chart": chart,
        "doses": dose_list,
        "plan": watering,
        # The dated plan (columns with dates and plant weeks, the next change, the tail): see timeline().
        "timeline": timeline(shown, setup, plan, today, start),
        "age": age,
        "frequency_note": ((shown or {}).get("frequency") or {}).get("text"),
        "frequency_sources": ((shown or {}).get("frequency") or {}).get("sources", []),
        "notes": [{"text": n.get("text"), "sources": n.get("sources", [])} for n in (shown or {}).get("notes", [])],
        "sources": (shown or {}).get("sources", {}),
        "last_fed": _entry_view(last, card_id, lang, library) if last else None,
        "liters_range": [float(MIN_LITERS), float(MAX_LITERS)],
        "liters_confirm_above": float(confirm_above(setup)),
        "withheld": None,  # the unit a caller could not show (see _withheld); None when nothing is withheld
        "not_before": plan["not_before"].isoformat() if plan.get("not_before") else None,  # plain water until this day
    }
    computed = amount_unit(card)
    if computed is not None and computed not in set(amount_units):
        return _withheld(out, computed, lang)
    return out


def _withheld(out: dict[str, Any], unit: str, lang: str) -> dict[str, Any]:
    """The view for a caller that cannot show this card's unit (Hank's part-1 code knows only ml, review M1): state
    no_card with a problem that says where the amounts are, the card's name, the switch and the last feeding's time
    and litres; no chart value, dose, plan, timeline, note or source of that card."""
    card = out["card"] or {}
    empty: dict[str, Any] = {
        "state": "no_card", "withheld": unit,
        "problem": t("feeding.unit_withheld", lang, card=card.get("name") or card.get("id") or "?"),
        "fingerprint": None, "reconfirm": False, "page": None, "confirmed": False, "confirmed_at": None,
        "dose_factor": None, "week_source": None, "bloom_start": None, "bloom_estimate_note": None, "warning": None,
        "week": None, "measure": None, "columns": [], "after_bloom": [], "chart": [], "doses": None, "plan": None,
        "timeline": None, "frequency_note": None, "frequency_sources": [], "notes": [], "sources": {},
    }
    last = out.get("last_fed")
    if isinstance(last, dict):
        empty["last_fed"] = {"time": last.get("time"), "liters": last.get("liters"), "card": last.get("card"),
                             "unit": last.get("unit"), "items": []}
    return {**out, **empty}


def preview(setup_id: str, lang: str | None = None, *, profile: dict[str, Any] | None = None, log: Any = _FROM_STATE,
            today: date | None = None, library: Path = LIBRARY) -> dict[str, Any] | None:
    """The page's view of ONE setup as if it were the one in use, for Hank's answer to "ich nutze <Dünger>" (owner,
    23.10.2026: Hank draws the plan of the fertilizer named). Nothing is chosen or stored: the owner's choice stays
    as it is, and "choice" says which one is active. Every rule of view() holds unchanged: a card the owner has not
    confirmed shows its chart values and no amount, needs_stage no amount, amounts rounded down and never above the
    chart times the factor. None when the setup is unknown or there is no "feeding"."""
    profile = plant_mod.load_profile() if profile is None else profile
    if log is _FROM_STATE:
        log = PlantLog()
    setups = setups_of(profile, library)
    own = setups.get(str(setup_id))
    if own is None or log is None:
        return None
    lang = normalize(lang) or current_lang()
    ctx = context(profile, log, lang)
    ctx["setups"] = setups
    out = view(own, lang, today=today, library=library, amount_units=PAGE_UNITS, **ctx)
    active = choice_of(log, profile) if len(setups) > 1 else next(iter(setups), None)
    out["choice"] = _choice(setups, active, lang, library)
    out["preview"] = True
    out["setup"] = str(setup_id)
    return out

