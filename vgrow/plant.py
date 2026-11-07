"""One plant per run: profile (facts and per-stage targets from plant.json) and its log.

plant.json (in the repo) holds what was researched: strain, start, stages with target ranges,
each range tied to a source. The log (in the state dir) holds what happened: stage changes the owner
made, measured heights. The page compares live readings against the current stage's targets and
turns every deviation into one concrete next step. Nothing is applied to the box without a tap.
Displayed texts carry English twins (label, label_en); view() hands out one language.

The log outlives a run (a new plant gets a new plant.json, the log in the state folder stays): a stage the owner
set before the run's start_date belongs to an earlier plant and confirms nothing for this one (owner_stages).
A plant.json that stops parsing (one trailing comma is enough) gives the last version that did, as ports.json does;
profile_problem() says why, so the page and the automation can name the real cause.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config
from .i18n import UserError, current_lang, localize, t

_LOGGER = logging.getLogger(__name__)

PLANT_FILE = config.REPO / "plant.json"
RUN_FILE = "plant-run.json"  # under STATE_DIR: the plant the owner chose in the cockpit (vgrow/plant_run.py)
PHOTOPERIOD_VEG_SOURCE = "Grow Weed Easy, Lichtpläne: https://www.growweedeasy.com/light-schedules"


def run_path() -> Path:
    """The running grow's file: the owner's chosen plant, else the repository's plant.json. Read at call time: choosing a
    plant takes effect without a restart, and the demo and the tests move STATE_DIR."""
    chosen = config.STATE_DIR / RUN_FILE
    return chosen if chosen.exists() else PLANT_FILE


LIBRARY = config.REPO / "kartei"  # the card index: stages/, strains/, nutrients/ (one JSON per card)

_runs_good: dict[str, dict[str, Any]] = {}  # per plant.json path: the last content that parsed
_run_problems: dict[str, str] = {}  # per path: why it does not parse right now; absent while it does


def parse_number(value: Any) -> float | None:
    """A form value as a number: empty means none, a decimal comma is fine."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        raise UserError(t("plant.not_a_number", value=value)) from None


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_run(path: Path) -> dict[str, Any] | None:
    """plant.json itself. Missing: None (no run). Unreadable: the last version that parsed (None if there was none),
    logged once per problem, and profile_problem() names it until the file parses again."""
    where = str(path)
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(run, dict):
            raise ValueError("the top level is not an object")
    except FileNotFoundError:
        _run_problems.pop(where, None)
        return None
    except (OSError, ValueError) as err:
        problem = f"{type(err).__name__}: {err}"
        good = _runs_good.get(where)
        if _run_problems.get(where) != problem:
            _LOGGER.error("%s unreadable (%s); %s", path.name, problem,
                          "using the last readable version" if good is not None else "no readable version known")
        _run_problems[where] = problem
        return copy.deepcopy(good) if good is not None else None
    _run_problems.pop(where, None)
    _runs_good[where] = copy.deepcopy(run)
    return run


def profile_problem(path: Path | None = None) -> str | None:
    """Why plant.json does not parse right now (then the profile is its last readable version, or None); None while
    it parses or is simply missing."""
    return _run_problems.get(str(path or run_path()))


def load_profile(path: Path | None = None, library: Path = LIBRARY) -> dict[str, Any] | None:
    """The current run (run_path) plus the cards it names.

    plant.json: {"plant": {...}, "stages_card": "autoflower", "strain_card": "...", "nutrient_card": "..."}
    A run may also carry its own "stages" list, which then wins over the template. A run the owner chose in the cockpit
    (vgrow/plant_run.py) has no strain card of its own: its strain comes from the lexicon or the encyclopedia.
    """
    run = _read_run(path or run_path())
    if run is None:
        return None
    profile = dict(run)
    sources = dict(run.get("sources", {}))
    for kind, folder in (("stages_card", "stages"), ("strain_card", "strains"), ("nutrient_card", "nutrients")):
        name = run.get(kind)
        card = _read(library / folder / f"{name}.json") if name else None
        if card is None:
            continue
        sources.update(card.get("sources", {}))
        if kind == "stages_card" and "stages" not in run:
            profile["stages"] = card.get("stages", [])
        if kind == "stages_card" and "light_schedule" not in run and "light_schedule" in card:
            profile["light_schedule"] = copy.deepcopy(card["light_schedule"])
        # source_notes are research notes about the sources, for whoever edits the card, not for the page.
        if kind == "strain_card":
            profile["strain"] = {k: v for k, v in card.items() if k not in ("sources", "source_notes")}
        if kind == "nutrient_card":
            profile["nutrient"] = {k: v for k, v in card.items() if k not in ("sources", "source_notes")}
    profile["sources"] = sources
    if "record" in (run.get("plant") or {}):  # a chosen plant (vgrow/plant_run.py)
        from . import plant_run  # here: plant_run imports this module

        profile["strain"] = plant_run.strain_card(run, library if library != LIBRARY else None)
    if run.get("stages_variant") == "photoperiod":
        # It flowers when the lamp goes to 12/12 (the box's flower preset, S1 on the stage card), so that is its plan in
        # flower; before that 18/6 keeps it growing (L1). The stage card's own 18/6 is written for autoflowers to harvest.
        profile["sources"] = {**sources, "L1": PHOTOPERIOD_VEG_SOURCE}
        profile["stages"] = [{**s, "light_schedule": {"hours_on": 12, "sources": ["S1"]}
                              if s.get("key") in FLOWERING_STAGES else {"hours_on": 18, "alternative_hours_on": [20],
                                                                        "sources": ["L1"]}}  # L1: 18 to 24 hours in veg
                             for s in profile.get("stages", [])]
    return profile


def library_index(library: Path = LIBRARY) -> dict[str, list[dict[str, Any]]]:
    """Short view of every card, for the Kartei section of the page."""
    out: dict[str, list[dict[str, Any]]] = {}
    for folder in ("strains", "nutrients", "stages"):
        cards = []
        for path in sorted((library / folder).glob("*.json")) if (library / folder).exists() else []:
            card = _read(path) or {}
            if isinstance(card.get("entries"), list) or card.get("private_only") is True:
                continue  # a collection (the strain lexicon) is no card of its own, and private data stays out
            cards.append({"key": path.stem, "name": card.get("name", path.stem), "summary": card.get("summary"),
                          "facts": card.get("facts", {}), "sources": len(card.get("sources", {}))})
        out[folder] = cards
    return out


PRE_FLOWER_STAGES = ("seedling", "veg")
FLOWERING_STAGES = ("early_flower", "mid_flower", "late_flower", "harvest")


def _one_per_day(heights: list[Any]) -> list[dict[str, Any]]:
    """One height per day, the last one entered for that day, oldest day first (the log of 22.10.2026 held three
    identical 61 cm entries for one day). Entries without a readable date keep their place at the start."""
    by_day: dict[str, dict[str, Any]] = {}
    undated: list[dict[str, Any]] = []
    for entry in heights:
        if not isinstance(entry, dict):
            continue
        day = entry.get("date")
        if parse_day(day) is None:
            undated.append(entry)
        else:
            by_day.pop(str(day), None)  # the later entry of a day replaces the earlier one
            by_day[str(day)] = entry
    return [*undated, *(by_day[d] for d in sorted(by_day))]


def height_metadata(estimated: bool = False, camera: str | None = None, method: str | None = None) -> dict[str, Any]:
    """A ruler reading is an estimate with a named reference camera: the owner's (user_ruler_estimate) or the code's
    reading of the strip (camera_strip, vgrow/ruler.py). Never a measurement with a tape."""
    if (type(estimated) is not bool or camera not in (None, "growcam") or camera and not estimated
            or method not in (None, "user_ruler_estimate", "camera_strip") or method and not camera):
        raise ValueError("height source: a GrowCam ruler reading must be marked as estimated")
    result: dict[str, Any] = {"estimated": True} if estimated else {}
    if camera:
        result.update(camera="growcam", method=method or "user_ruler_estimate")
    return result


class PlantLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.STATE_DIR / "plant-log.json"
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("stages", [])  # [{"key": ..., "since": "YYYY-MM-DD"}]
        data.setdefault("heights", [])  # [{"cm": ..., "date": "YYYY-MM-DD"}]
        data.setdefault("water", [])  # [{"time": "...", "ph": ..., "ec": ...}]
        # The first white pistils, as the owner entered the date on the page: {"date": "YYYY-MM-DD", "set_at": "..."}.
        # It counts before the camera's estimate and before plant.json's bloom_start (bloom_start()).
        data.setdefault("bloom_start", None)
        # Which fertilizer setup of plant.json the owner gives (feeding.setups): {"setup": id, "set_at": "..."}.
        # Only one at a time; the page's switch (two taps) sets it, never plant.json (feeding.choice_of).
        data.setdefault("feeding_choice", None)
        # The plant's start as the owner entered it (germination): {"date": "YYYY-MM-DD", "set_at": "...", "from":
        # "date" | "age_weeks" | "age_days", "run": plant.json's start_date when it was set}. It counts before
        # plant.json's start_date (effective_start), only for the run it was entered in.
        data.setdefault("start_date", None)
        # The first day with fertilizer for this run, "YYYY-MM-DD" (owner, 06.11.2026: "I would start with the fertilizer in
        # 2 weeks"). Plain water until then (feeding.evaluate). None: the chart counts from the start.
        data.setdefault("feed_from", None)
        data["heights"] = _one_per_day(data["heights"] if isinstance(data["heights"], list) else [])
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def set_stage(self, key: str, since: str | None = None) -> None:
        self.data["stages"].append({"key": key, "since": since or date.today().isoformat()})
        self._save()

    def start_new_run(self, archive: Path) -> None:
        """A new plant takes over the cockpit (vgrow/plant_run.py): this plant's log is copied to `archive`, and its
        stages, heights, readings and dates no longer count. Which fertilizer the owner gives stays."""
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.data.update(stages=[], heights=[], water=[], bloom_start=None, start_date=None, feed_from=None)
        self._save()

    def add_water(self, ph: float | None, ec: float | None, at: datetime | None = None) -> None:
        if ph is None and ec is None:
            raise UserError(t("plant.ph_or_ec"))
        if ph is not None and not 0 < ph < 14:
            raise UserError(t("plant.ph_range"))
        if ec is not None and not 0 <= ec < 10:
            raise UserError(t("plant.ec_range"))
        self.data["water"].append({"time": (at or datetime.now()).isoformat(timespec="minutes"),
                                   "ph": None if ph is None else round(ph, 2),
                                   "ec": None if ec is None else round(ec, 2)})
        self._save()

    def add_height(self, cm: float | None, on: str | None = None, *, estimated: bool = False,
                   camera: str | None = None, method: str | None = None) -> None:
        """One height per day: a second entry on the same day replaces the first (the owner corrects a typo or
        measures again), it never adds a second line."""
        if cm is None or not 1 <= cm <= 300:
            raise UserError(t("plant.height_range"))
        metadata = height_metadata(estimated, camera, method)
        self.data["heights"] = _one_per_day([*self.data["heights"],
            {"cm": round(cm, 1), "date": on or date.today().isoformat(), **metadata}])
        self._save()

    def set_bloom_start(self, value: Any, *, start: date | None = None, today: date | None = None,
                        at: datetime | None = None) -> str:
        """The day the owner saw the first white pistils (the page's field "Blütebeginn", two taps). A YYYY-MM-DD
        date, not in the future and not before the run's start_date (start). A new date replaces the old one."""
        day = parse_day(value)
        if day is None:
            raise UserError(t("plant.bloom_bad_date", value=value))
        if day > (today or date.today()):
            raise UserError(t("plant.bloom_future"))
        if start is not None and day < start:
            raise UserError(t("plant.bloom_before_start", date=date_text(start, current_lang())))
        self.data["bloom_start"] = {"date": day.isoformat(),
                                    "set_at": (at or datetime.now()).isoformat(timespec="seconds")}
        self._save()
        return day.isoformat()

    def set_feed_from(self, value: Any) -> str | None:
        """The owner's first day with fertilizer, or None for no such day."""
        day = None if value in (None, "") else parse_day(value)
        if value not in (None, "") and day is None:
            raise UserError(t("plant.bloom_bad_date", value=value))
        self.data["feed_from"] = day.isoformat() if day else None
        self._save()
        return self.data["feed_from"]

    def set_feeding_choice(self, setup_id: Any, known: Any, at: datetime | None = None) -> str:
        """The fertilizer the owner gives from now on (the page's switch, two taps): one of plant.json's
        feeding.setups. Only one counts at a time; amounts are never carried from one to the other."""
        ids = [str(k) for k in known]
        if not isinstance(setup_id, str) or setup_id not in ids:
            raise UserError(t("feeding.unknown_setup", setup=setup_id, known=", ".join(ids)))
        self.data["feeding_choice"] = {"setup": setup_id,
                                       "set_at": (at or datetime.now()).isoformat(timespec="seconds")}
        self._save()
        return setup_id

    def set_start_date(self, value: Any = None, *, age_weeks: Any = None, age_days: Any = None,
                       today: date | None = None, at: datetime | None = None, run_start: date | None = None,
                       current: date | None = None) -> str:
        """The plant's start (germination) as the owner gives it on the plant card, two taps: exactly one of a
        YYYY-MM-DD date, an age in whole weeks (0 to 52) or in days (0 to MAX_AGE_DAYS). Never in the future, never
        more than MAX_AGE_DAYS ago. run_start: plant.json's start_date (kept with the entry: a new run does not
        inherit it). current: the start that counts now (effective_start). The new start may not move across a day
        that holds a stage or a bloom start the owner entered: moving it later would silently drop those entries
        (owner_stages and owner_bloom_start ignore anything before the start), moving it earlier could revive entries
        of an earlier plant. Returns the stored date. The dose never depends on it (feeding: stage and bloom start)."""
        today = today or date.today()
        given = [name for name, v in (("date", value), ("age_weeks", age_weeks), ("age_days", age_days))
                 if v not in (None, "")]
        if len(given) != 1:
            raise UserError(t("plant.start_one_of"))
        source = given[0]
        if source == "date":
            day = parse_day(value)
            if day is None:
                raise UserError(t("plant.start_bad_date", value=value))
        else:
            limit = 52 if source == "age_weeks" else MAX_AGE_DAYS
            raw = str(age_weeks if source == "age_weeks" else age_days).strip()
            if isinstance(age_weeks if source == "age_weeks" else age_days, bool) or not raw.isdigit():
                raise UserError(t("plant.not_a_number", value=raw))
            n = int(raw)
            if not 0 <= n <= limit:
                raise UserError(t("plant.start_weeks_range" if source == "age_weeks" else "plant.start_days_range",
                                  max=limit))
            day = today - timedelta(days=7 * n if source == "age_weeks" else n)
        if day > today:
            raise UserError(t("plant.start_future"))
        if (today - day).days > MAX_AGE_DAYS:
            raise UserError(t("plant.start_too_old", days=MAX_AGE_DAYS))
        crossed = self._crossed_entry(current, day)
        if crossed is not None:
            raise UserError(t("plant.start_crosses_entry", date=date_text(crossed, current_lang()),
                              start=date_text(day, current_lang())))
        self.data["start_date"] = {"date": day.isoformat(),
                                   "set_at": (at or datetime.now()).isoformat(timespec="seconds"),
                                   "from": source, "run": run_start.isoformat() if run_start else None}
        self._save()
        return day.isoformat()

    def _crossed_entry(self, current: date | None, new: date) -> date | None:
        """The first owner entry (stage or bloom start) on a day between the current start and the new one: the days
        that would switch between "this plant" and "an earlier plant". Without a current start every entry counts
        now, so any entry before the new start would be dropped."""
        days = [parse_day(e.get("since")) for e in self.data.get("stages", []) if isinstance(e, dict)]
        bloom = self.data.get("bloom_start")
        days.append(parse_day(bloom.get("date")) if isinstance(bloom, dict) else None)
        lo, hi = (date.min, new) if current is None else (min(current, new), max(current, new))
        inside = sorted(d for d in days if d is not None and lo <= d < hi)
        return inside[0] if inside else None


def parse_day(value: Any) -> date | None:
    """A YYYY-MM-DD date, or None for anything else."""
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def local_today() -> date:
    """plant's calendar day (tests pin it with plant.date); the server's age preview uses the same day."""
    return date.today()


MAX_AGE_DAYS = 400  # the cockpit's own bound for a start (one plant, one run), not a sourced number
START_TOLERANCE_DAYS = 3  # the cockpit's own tolerance: "5 Wochen alt" may be up to 3 days short of 35 days


def effective_start(profile: dict[str, Any] | None, log: PlantLog | None) -> tuple[date | None, str | None]:
    """The plant's start and who gave it: "owner" (the plant card, plant log) before "plan" (plant.json's
    start_date). An owner entry made for another run (its "run" is not this plant.json's start_date) counts as none,
    since the plant log outlives a run."""
    plant = (profile or {}).get("plant") or {}
    if plant.get("start_by") in ("owner", "plan"):  # already resolved by with_owner_start
        return parse_day(plant.get("start_date")), plant["start_by"]
    plan = parse_day(plant.get("start_date"))
    entry = log.data.get("start_date") if log is not None else None
    if isinstance(entry, dict):
        own = parse_day(entry.get("date"))
        run = entry.get("run")
        if own is not None and (run is None or plan is None or run == plan.isoformat()):
            return own, "owner"
    return (plan, "plan") if plan is not None else (None, None)


def with_owner_start(profile: dict[str, Any], log: PlantLog | None) -> dict[str, Any]:
    """The profile with the start that counts (effective_start) in plant.start_date and its origin in
    plant.start_by, so every reader of profile["plant"]["start_date"] (plant_day, owner_stages, owner_bloom_start,
    bloom_suggestion, bloom_view) sees the owner's date. A shallow copy; wrapping twice changes nothing."""
    if not isinstance(profile, dict) or (profile.get("plant") or {}).get("start_by"):
        return profile
    day, by = effective_start(profile, log)
    plant = dict(profile.get("plant") or {})
    plant["start_date"] = day.isoformat() if day else plant.get("start_date")
    plant["start_by"] = by
    entry = log.data.get("start_date") if log is not None else None
    if by == "owner" and isinstance(entry, dict):
        plant["start_from"] = entry.get("from")
    return {**profile, "plant": plant}


def age_info(start: date | None, today: date) -> dict[str, int] | None:
    """{days, weeks, extra_days, life_week}: day 0 is the start day (as plant_day); "5 Wochen alt" is 35 to 41 days;
    life_week = days // 7 + 1, so a plant of 5 weeks is in plant week 6. None without a start or before it."""
    if start is None or today < start:
        return None
    days = (today - start).days
    return {"days": days, "weeks": days // 7, "extra_days": days % 7, "life_week": days // 7 + 1}


def age_check(stored: date | None, kind: str, n: int, today: date) -> dict[str, Any]:
    """A stated age against the stored start. kind "weeks": consistent while the stored age is 7n - 3 to 7n + 6 days
    (START_TOLERANCE_DAYS short, or up to six days into the next week), implied start today - 7n. kind "days":
    within one day, implied today - n. kind "life_week" (plant week n): 7(n - 1) to 7n - 1 days; no implied start
    (a whole week), so the caller asks for the day. consistent is None without a stored start."""
    stored_days = (today - stored).days if stored is not None else None
    implied: date | None
    if kind == "weeks":
        implied = today - timedelta(days=7 * n)
        fits = None if stored_days is None else 7 * n - START_TOLERANCE_DAYS <= stored_days <= 7 * n + 6
    elif kind == "days":
        implied = today - timedelta(days=n)
        fits = None if stored_days is None else abs(stored_days - n) <= 1
    elif kind == "life_week":
        implied = None
        fits = None if stored_days is None else 7 * (n - 1) <= stored_days <= 7 * n - 1
    else:
        raise ValueError(kind)
    return {"kind": kind, "n": n, "stored_start": stored.isoformat() if stored else None, "stored_days": stored_days,
            "implied_start": implied.isoformat() if implied else None, "consistent": fits,
            "diff_days": None if implied is None or stored is None else (implied - stored).days}


def age_text(info: dict[str, int], lang: str) -> str:
    """"5 Wochen und 1 Tag" / "5 weeks and 1 day", "3 Tage", "5 Wochen"."""
    weeks, days = info["weeks"], info["extra_days"]
    w = t("plant.age_w1", lang) if weeks == 1 else t("plant.age_wn", lang, n=weeks)
    d = t("plant.age_d1", lang) if days == 1 else t("plant.age_dn", lang, n=days)
    if not weeks:
        return d
    return w if not days else t("plant.age_and", lang, weeks=w, days=d)


def age_view(profile: dict[str, Any] | None, log: PlantLog | None, lang: str,
             today: date | None = None) -> dict[str, Any]:
    """The plant's age for the page, the feeding plan and (part 2) Hank: {start, by, from, approx, days, weeks,
    extra_days, life_week, state, ask, min, max, line, ask_text}. state "unknown" without a start, else "known".
    approx: the start is only roughly known (plant.json says start_approx and the owner has not set it, or the owner
    gave it as weeks). ask: None, {"reason": "unknown"} or {"reason": "approx"} (plant.json's rough start only; a
    start the owner gave is never asked again). min/max bound the date field. line and ask_text are code texts in
    `lang`, dates written as the page writes them. The age never changes a dose."""
    today = today or date.today()
    start, by = effective_start(profile, log)
    plant = (profile or {}).get("plant") or {}
    source = None
    if by == "owner":
        entry = log.data.get("start_date") if log is not None else None
        source = entry.get("from") if isinstance(entry, dict) else plant.get("start_from")
    approx = start is not None and ((by == "plan" and bool(plant.get("start_approx"))) or source == "age_weeks")
    info = age_info(start, today)
    ask = None
    if info is None:
        ask = {"reason": "unknown"}
    elif by == "plan" and approx:
        ask = {"reason": "approx"}
    out: dict[str, Any] = {"start": start.isoformat() if start else None, "by": by, "from": source, "approx": approx,
                           "state": "known" if info else "unknown", "ask": ask,
                           "min": (today - timedelta(days=MAX_AGE_DAYS)).isoformat(), "max": today.isoformat(),
                           **(info or {"days": None, "weeks": None, "extra_days": None, "life_week": None})}
    if info is None:
        out["line"] = t("plant.age_unknown", lang)
    else:
        who = "plant.start_by.owner_weeks" if source == "age_weeks" else \
            "plant.start_by.owner_days" if source == "age_days" else f"plant.start_by.{by}"
        # A rough start gives whole weeks only: "etwa 6 Wochen und 1 Tag" would claim a precision it has not (N4).
        shown_age = age_text({**info, "extra_days": 0}, lang) if approx and info["weeks"] else age_text(info, lang)
        out["line"] = t("plant.age_line_approx" if approx else "plant.age_line", lang, age=shown_age,
                        days=info["days"], life=info["life_week"], date=date_text(start, lang), by=t(who, lang))
    out["ask_text"] = None if ask is None else t(f"plant.age_ask_{ask['reason']}", lang,
                                                 date=date_text(start, lang) if start else "")
    return out


def owner_stages(profile: dict[str, Any], log: PlantLog) -> list[dict[str, Any]]:
    """The stages the owner set for this run, oldest first. The log outlives a run: an entry from before the run's
    start_date (or without a readable date, once the run has one) belongs to an earlier plant and is left out."""
    profile = with_owner_start(profile, log)
    start = parse_day((profile.get("plant") or {}).get("start_date"))
    out = []
    for entry in log.data.get("stages", []):
        if not isinstance(entry, dict):
            continue
        if start is not None:
            since = parse_day(entry.get("since"))
            if since is None or since < start:
                continue
        out.append(entry)
    return out


def owner_bloom_start(profile: dict[str, Any], log: PlantLog) -> date | None:
    """The bloom start the owner entered on the page (PlantLog.set_bloom_start), or None. Like a stage, a date from
    before this run's start_date belongs to an earlier plant and is left out."""
    profile = with_owner_start(profile, log)
    entry = log.data.get("bloom_start")
    day = parse_day(entry.get("date")) if isinstance(entry, dict) else None
    start = parse_day((profile.get("plant") or {}).get("start_date"))
    if day is None or start is not None and day < start:
        return None
    return day


def flowering_since(owned: Any) -> date | None:
    """The first day of the owner's current flowering spell: the earliest date among the flowering stages at the end
    of the log (a stage before flowering in between starts a new spell). None without one."""
    since = None
    for entry in reversed(list(owned)):
        if not isinstance(entry, dict) or entry.get("key") not in FLOWERING_STAGES:
            break
        day = parse_day(entry.get("since"))
        if day is None:
            break
        since = day if since is None else min(since, day)
    return since


def bloom_start(profile: dict[str, Any], log: PlantLog, stage: dict[str, Any] | None = None) -> tuple[date | None,
                                                                                                   str | None]:
    """The date flowering counts from and where it comes from, in the order the feeding planner uses
    (feeding.week_source): "owner" (the page's field), "plan" (bloom_start in plant.json), "stage" (the day the owner
    set the first flowering stage of this spell), "estimate" (bloom_start_estimate, the camera). A stage the owner
    set before flowering has none."""
    manual = stage if stage and stage.get("set_by") == "manual" else None
    if manual and manual.get("key") in PRE_FLOWER_STAGES:
        return None, None
    owner = owner_bloom_start(profile, log)
    if owner is not None:
        return owner, "owner"
    setup = profile.get("feeding") if isinstance(profile.get("feeding"), dict) else {}
    plan = parse_day(setup.get("bloom_start"))
    if plan is not None:
        return plan, "plan"
    if manual and manual.get("key") in FLOWERING_STAGES:
        since = flowering_since(owner_stages(profile, log))
        if since is not None:
            return since, "stage"
    estimate = parse_day(setup.get("bloom_start_estimate"))
    if estimate is not None:
        return estimate, "estimate"
    return None, None


def _stage_from_bloom(stages: list[dict[str, Any]], start: date, today: date) -> tuple[dict[str, Any], date] | None:
    """The flowering stage the days since the owner's bloom start fall into, and the day it began. The card's day
    windows count from the plant's start (they are a fallback, card note 4); here they count from the first flowering
    stage's from_day on, so 18 days of flowering on the windows 21 to 35 and 35 to 49 is the second stage.
    The harvest window is never picked from a date alone (review 2, 23.10.2026): ripeness is read from the trichomes
    (glossary G24 to G26), not counted in days, and a slip in the date picker (September for October) must not turn
    the stage into harvest. Past the last flowering window the stage stays late flower."""
    flowering = sorted((s for s in stages if s.get("key") in FLOWERING_STAGES and s.get("key") != "harvest"
                        and isinstance(s.get("from_day"), int)), key=lambda s: s["from_day"])
    if not flowering or today < start:
        return None
    offset = flowering[0]["from_day"]
    at = offset + (today - start).days
    found = next((s for s in flowering if s["from_day"] <= at < s.get("to_day", 10**6)), flowering[-1])
    return found, start + timedelta(days=found["from_day"] - offset)


def _with_bloom(stage: dict[str, Any], profile: dict[str, Any], log: PlantLog, today: date) -> dict[str, Any]:
    """The stage with its bloom start: bloom_start (YYYY-MM-DD or None), bloom_by (owner, plan, stage, estimate or
    None) and flower_day (days since the bloom start, None before it or without one)."""
    day, by = bloom_start(profile, log, stage)
    flower_day = (today - day).days if day is not None and day <= today else None
    entered = owner_bloom_start(profile, log)  # also under a stage before flowering, for the feeding warning
    return {**stage, "bloom_start": day.isoformat() if day else None, "bloom_by": by, "flower_day": flower_day,
            "owner_bloom_start": entered.isoformat() if entered else None}


def current_stage(profile: dict[str, Any], log: PlantLog, today: date | None = None) -> dict[str, Any] | None:
    """The owner's last recorded stage of this run wins; otherwise the estimate, then the stage whose day window
    contains today. A bloom start the owner entered on the page (owner_bloom_start) calibrates a stage the owner has
    not set: the flowering stage its days fall into, since the day that stage began (owner, 23.10.2026: "Stage ist
    bestimmt nicht ein Tag"; the camera put the start on the day the photo reading began). It stays an estimate
    (set_by "estimate", estimate_from "bloom"): only the owner's own stage lets the automation switch."""
    profile = with_owner_start(profile, log)
    stages = profile.get("stages", [])
    if not stages:
        return None
    today = today or date.today()
    by_key = {s["key"]: s for s in stages}
    owned = owner_stages(profile, log)
    if owned:
        last = owned[-1]
        if last.get("key") in by_key:
            return _with_bloom({**by_key[last["key"]], "since": last.get("since"), "set_by": "manual"}, profile, log,
                               today)
    owner = owner_bloom_start(profile, log)
    derived = _stage_from_bloom(stages, owner, today) if owner is not None else None
    if derived is not None:
        found, since = derived
        return _with_bloom({**found, "since": since.isoformat(), "set_by": "estimate", "estimate_from": "bloom",
                            "estimate_note": None}, profile, log, today)
    estimate = profile.get("stage_estimate") or {}
    if estimate.get("key") in by_key:
        return _with_bloom({**by_key[estimate["key"]], "since": estimate.get("since"), "set_by": "estimate",
                            "estimate_note": estimate.get("note")}, profile, log, today)
    day = plant_day(profile, today)
    if day is None:
        return None
    if profile.get("stages_variant") == "photoperiod":  # it flowers by the lamp, never by its age
        stages = [s for s in stages if s.get("key") not in FLOWERING_STAGES] or stages
    for stage in stages:
        if stage.get("from_day", 0) <= day < stage.get("to_day", 10**6):
            return _with_bloom({**stage, "set_by": "days"}, profile, log, today)
    return _with_bloom({**stages[-1], "set_by": "days"}, profile, log, today)


def plant_day(profile: dict[str, Any], today: date | None = None) -> int | None:
    start = (profile.get("plant") or {}).get("start_date")
    if not start:
        return None
    return ((today or date.today()) - datetime.strptime(start, "%Y-%m-%d").date()).days


def _actual(metric: str, snap: dict[str, Any]) -> float | None:
    climate = snap.get("climate") or {}
    if metric in climate:
        return climate[metric]
    if metric == "light_percent":
        return (snap.get("light") or {}).get("level")
    if metric == "cfan_step":
        return (snap.get("cfan") or {}).get("step")
    if metric == "dfan_step":
        return (snap.get("dfan") or {}).get("step")
    return None


def lights_on(snap: dict[str, Any]) -> bool | None:
    level = (snap.get("light") or {}).get("level")
    return None if level is None else level > 0


def _consequence(target: dict[str, Any], status: str, value: float | None) -> tuple[str | None, list[str]]:
    """The card's consequence of staying low or high, with its own sources (they can differ from the range's).
    consequence_<status>_from limits it to where the source carries it: high from that value up, low from it down."""
    if status not in ("low", "high") or value is None:
        return None, []
    text = target.get(f"consequence_{status}")
    start = target.get(f"consequence_{status}_from")
    if not text or start is not None and (value < start if status == "high" else value > start):
        return None, []
    return text, list(target.get(f"consequence_{status}_sources", []))


def evaluate(stage: dict[str, Any] | None, snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Each target becomes one row: actual, range, ok/low/high, and the next step if it is off.
    A target without min and max is "none": a value with nothing to hold it against is not ok.
    Extra keys for Hank, which the page ignores: consequence and consequence_sources (only while the value is off and
    inside the card's consequence range), verified and verified_reason (the card's review verdict for the range)."""
    if not stage:
        return []
    on = lights_on(snap)
    rows = []
    for target in stage.get("targets", []):
        when = target.get("when")  # "day" (lights on), "night" (lights off) or None (always)
        if when == "day" and on is False or when == "night" and on is True:
            continue
        value = _actual(target["metric"], snap)
        lo, hi = target.get("min"), target.get("max")
        if value is None:
            status = "unknown"
        elif lo is None and hi is None:
            status = "none"
        elif lo is not None and value < lo:
            status = "low"
        elif hi is not None and value > hi:
            status = "high"
        else:
            status = "ok"
        consequence, consequence_sources = _consequence(target, status, value)
        rows.append({
            "metric": target["metric"], "label": target.get("label", target["metric"]),
            "unit": target.get("unit", ""), "actual": value, "min": lo, "max": hi, "status": status,
            "advice": target.get(f"advice_{status}") if status in ("low", "high") else None,
            "sources": target.get("sources", []),
            "consequence": consequence, "consequence_sources": consequence_sources,
            "verified": target.get("verified"), "verified_reason": target.get("verified_reason"),
        })
    return rows


MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
HEIGHT_DUE_DAYS = 2  # Hank asks for today's height when the newest one is older than this (owner, 23.10.2026)


def date_text(value: Any, lang: str) -> str:
    """As the page writes a date: 22.10.2026 in German, Oct 22, 2026 in English; the input as it is when no date."""
    day = value if isinstance(value, date) else parse_day(value)
    if day is None:
        return str(value)
    return f"{day:%d.%m.%Y}" if lang == "de" else f"{MONTHS_EN[day.month - 1]} {day.day}, {day.year}"


def bloom_suggestion(profile: dict[str, Any], today: date | None = None) -> dict[str, Any] | None:
    """A rough bloom start from the stage card's day windows (card note: only a fallback), for the plant card's
    one-tap suggestion while the date is open or only the camera's estimate (owner, 23.10.2026: "Stage ist bestimmt
    nicht ein Tag"). {date: the day the first flowering window begins, stage and stage_key: the window today falls
    into, stage_since: the day that window began}. None without a start date, before the first flowering window, or
    without windows."""
    today = today or date.today()
    start = parse_day((profile.get("plant") or {}).get("start_date"))
    stages = [s for s in profile.get("stages", []) if isinstance(s, dict) and isinstance(s.get("from_day"), int)]
    flowering = sorted((s for s in stages if s.get("key") in FLOWERING_STAGES and s.get("key") != "harvest"),
                       key=lambda s: s["from_day"])
    if start is None or not flowering:
        return None
    day = (today - start).days
    if day < flowering[0]["from_day"]:
        return None
    now = next((s for s in stages if s["from_day"] <= day < s.get("to_day", 10**6)), stages[-1])
    return {"date": (start + timedelta(days=flowering[0]["from_day"])).isoformat(),
            "stage": now.get("label") or now["key"], "stage_key": now["key"],
            "stage_since": (start + timedelta(days=now["from_day"])).isoformat()}


def bloom_view(profile: dict[str, Any], log: PlantLog, stage: dict[str, Any] | None,
               lang: str) -> dict[str, Any]:
    """The plant card's field: {date, by, flower_day, estimated, latest, suggest, min, max, note}. estimated is True
    while only the camera's estimate stands behind the date, so the card and Hank say "geschätzt" and Hank asks for
    the real date. latest: the date is an upper bound, not a start (the first analysed image already showed white
    pistils, so flowering began on that day or before; the card says "spätestens", never "seit 1 Tag").
    suggest: bloom_suggestion while the date is open or estimated and the windows give an earlier day. min and max
    bound the date input (the run's start, today)."""
    profile = with_owner_start(profile, log)
    day, by = bloom_start(profile, log, stage)
    today = date.today()
    start = parse_day((profile.get("plant") or {}).get("start_date"))
    suggest = bloom_suggestion(profile, today) if by in (None, "estimate") else None
    if suggest is not None and day is not None and suggest["date"] >= day.isoformat():
        suggest = None  # the windows say nothing earlier than the camera
    return {"date": day.isoformat() if day else None, "by": by,
            "flower_day": (today - day).days if day is not None and day <= today else None,
            "estimated": by == "estimate", "latest": by == "estimate", "suggest": suggest,
            "min": start.isoformat() if start else None, "max": today.isoformat(),
            "note": t(f"plant.bloom_by.{by}", lang) if by else None}


def height_age(heights: list[dict[str, Any]], today: date | None = None) -> int | None:
    """Days since the newest height, None without one."""
    day = parse_day((heights[-1] if heights else {}).get("date"))
    return None if day is None else ((today or date.today()) - day).days


def view(profile: dict[str, Any] | None, log: PlantLog, snap: dict[str, Any],
         lang: str | None = None) -> dict[str, Any]:
    lang = lang or current_lang()
    problem = profile_problem()  # plant.json does not parse: the profile is its last readable version
    if profile is None:
        return {"configured": False, "problem": problem}
    profile = with_owner_start(localize(profile, lang), log)  # one language, no *_en keys; the owner's start
    stage = current_stage(profile, log)
    if stage and stage.get("estimate_from") == "bloom" and stage.get("bloom_start"):
        stage["estimate_note"] = t("plant.stage_from_bloom", lang, date=date_text(stage["bloom_start"], lang),
                                   stage=stage.get("label") or stage["key"])
    heights = log.data["heights"]
    return {
        "configured": True,
        "problem": problem,
        "plant": profile.get("plant", {}),
        "strain": profile.get("strain"),
        "nutrient": profile.get("nutrient"),
        "day": plant_day(profile),
        "stage": {k: stage.get(k) for k in ("key", "label", "since", "set_by", "estimate_note", "estimate_from",
                                             "preset", "preset_note", "watering", "bloom_start", "bloom_by",
                                             "flower_day")} if stage else None,
        # The field "Blütebeginn" of the plant card: the date flowering counts from and where it comes from (owner:
        # entered on the page; plan: plant.json; stage: the owner's first flowering stage; estimate: the camera).
        "bloom": bloom_view(profile, log, stage, lang),
        # The plant's age from the start that counts (owner before plant.json): the page's age line and start field,
        # the feeding timeline (feeding.view age=...) and, in part 2, Hank's age questions all read this one dict.
        "age": age_view(profile, log, lang),
        "notes": profile.get("notes", []),
        "stages": [{"key": s["key"], "label": s.get("label", s["key"])} for s in profile.get("stages", [])],
        "checks": evaluate(stage, snap),
        "height": heights[-1] if heights else None,
        "height_age": height_age(heights),  # days since the newest height (Hank asks for today's after two)
        "medium": profile.get("medium"),
        "water_targets": profile.get("water_targets"),
        "water": log.data["water"][-12:],
        "heights": heights[-30:],
        "sources": profile.get("sources", {}),
        "checked_at": time.time(),
    }
