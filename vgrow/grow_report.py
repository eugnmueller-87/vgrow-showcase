"""Grow report for one plant's record: what went well, what did not, what to watch next time (Tracker M5d; the owner,
23.10.2026: a report per plant, "aus Messwerten und deinen Notizen", with lessons for the next grow).

Code judges, nothing here asks a model or switches a device. Per stage the plant went through (the owner's stages
in the plant log, inside the record's time in the box): the share of measured minutes inside the stage card's band
for each climate target at the probe (temperature with the light on and off, humidity, VPD). From WELL percent on
the target went well, under POOR percent it did not, and "next time" carries the card's own advice for the side it
missed on (advice_high or advice_low; the cards cite their sources). WELL and POOR are the cockpit's own thresholds,
no source names them, and the page says so. A target with fewer than MIN_ROWS measured minutes gets no verdict.
Pests: the watch's confirmed findings in that time, with the card the owner identified and that card's first
prevention line. The owner's own words (the summary written at the end, the journal) stand beside the numbers.
Use (Tracker M6b): water and feed as logged, a pump run without a measured flow stays a run with its minutes and never
becomes litres; power is an estimate, marked as one, for the lamp alone (no meter exists).

GET /api/grow-report/{plant}: the report in the page's language. The record's time in the box comes from the
workspace (record_places); a plant without one has no measured part.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from aiohttp import web

from . import feeding, pest_watch, wiki, workspace
from .box import climate_view
from .i18n import current_lang, fmt_num, fmt_qty, localize, normalize, t
from .light_view import READING_HOLD as LIGHT_HOLD
from .light_view import lamp_hours
from .plant import date_text

WELL = 80.0  # percent of measured minutes inside the band: went well (own threshold, no source)
POOR = 50.0  # under this: did not go well (own threshold, no source)
MIN_ROWS = 360  # some six hours of measured minutes before a target gets a verdict
LAMP_WATTS = 100  # the VGrow's LED as VIVOSUN rates it (manual, kartei/crops: dimmable 25 to 100 %); assumed linear
CLIMATE = ("probe_temp", "probe_humidity", "probe_vpd")
NAME_RE = re.compile(r"[\w-]{1,80}", re.ASCII)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def stage_spans(stages: Iterable[Mapping[str, Any]], start: float, end: float) -> list[tuple[str, float, float]]:
    """The owner's stages as (key, from, to) in seconds, cut to [start, end). A stage counts from local midnight of
    its "since" day until the next stage's; the last until end. Stages entered later for the same day replace the
    earlier ones of that day (the owner corrected a tap)."""
    by_day: dict[date, str] = {}
    for entry in stages:
        day = _day(entry.get("since"))
        if day is not None and entry.get("key"):
            by_day[day] = str(entry["key"])
    days = sorted(by_day)
    out = []
    for i, day in enumerate(days):
        begin = datetime.combine(day, datetime.min.time()).timestamp()
        finish = datetime.combine(days[i + 1], datetime.min.time()).timestamp() if i + 1 < len(days) else end
        a, b = max(begin, start), min(finish, end)
        if b > a:
            out.append((by_day[day], a, b))
    return out


def measure(rows: list[Mapping[str, Any]], target: Mapping[str, Any]) -> dict[str, Any]:
    """One target over the rows: measured minutes, and the percent inside the band, below and above it. "when": day
    counts rows with the light on, night with the light off; a row whose light is not known within LIGHT_HOLD counts
    for neither."""
    low, high, when = target.get("min"), target.get("max"), target.get("when")
    light, light_at = None, None
    inside = below = above = 0
    for row in rows:
        at = float(row.get("time") or 0)
        if row.get("light") is not None:
            light, light_at = row["light"], at
        known = light_at is not None and at - light_at <= LIGHT_HOLD
        if when in ("day", "night"):
            if not known or (when == "day") != ((light or 0) > 0):
                continue
        value = climate_view(dict(row)).get(target["metric"])
        if value is None:
            continue
        if low is not None and value < low:
            below += 1
        elif high is not None and value > high:
            above += 1
        else:
            inside += 1
    n = inside + below + above
    pct = (lambda k: round(100 * k / n, 1)) if n else (lambda k: None)
    return {"rows": n, "inside": pct(inside), "below": pct(below), "above": pct(above)}


def verdict(m: Mapping[str, Any]) -> str | None:
    """well, poor, mixed, or None with too few measured minutes."""
    if m["rows"] < MIN_ROWS or m["inside"] is None:
        return None
    return "well" if m["inside"] >= WELL else "poor" if m["inside"] < POOR else "mixed"


def climate(store: Any, profile: Mapping[str, Any] | None, stages: list[Mapping[str, Any]],
            spans: list[tuple[float, float]], lang: str) -> list[dict[str, Any]]:
    """Per stage and climate target: the measurement and its verdict, in the stage card's own words."""
    cards = {s.get("key"): localize(s, lang) for s in (profile or {}).get("stages") or [] if isinstance(s, dict)}
    out = []
    for since, until in spans:
        for key, a, b in stage_spans(stages, since, until):
            card = cards.get(key)
            if card is None:
                continue
            rows = store.rows(a, b)
            for target in card.get("targets") or []:
                if target.get("metric") not in CLIMATE:
                    continue
                m = measure(rows, target)
                out.append({"stage": key, "stage_label": card.get("label") or key, "metric": target["metric"],
                            "label": target.get("label") or target["metric"], "when": target.get("when"),
                            "min": target.get("min"), "max": target.get("max"), "unit": target.get("unit", ""),
                            "from": a, "to": b, **m, "verdict": verdict(m),
                            "advice": (target.get("advice_high") if (m["above"] or 0) >= (m["below"] or 0)
                                       else target.get("advice_low")),
                            "sources": list(target.get("sources") or [])})
    return out


def pests(checks: list[Mapping[str, Any]], start: float, end: float, lang: str,
          on: Iterable[str] | None = None) -> dict[str, Any]:
    """The watch in that time: checks, findings, what the owner confirmed and identified, and for an identified card
    its first prevention line."""
    inside = [c for c in checks if c.get("result") and start <= float(c.get("at") or 0) < end]
    confirmed = [c for c in inside if (c.get("finding") or {}).get("status") == "confirmed"]
    found = []
    for check in confirmed:
        ref = (check.get("finding") or {}).get("identified")
        entry = None
        if ref and ref != pest_watch.OTHER and "/" in ref:
            folder, key = ref.split("/", 1)
            entry = wiki.find(folder, key)
            if entry is not None and not wiki.visible(entry, wiki.packages() if on is None else on):
                entry = None
        card = wiki.card_view(entry, lang, on) if entry is not None else None
        prevention = [p for p in ((card or {}).get("card") or {}).get("prevention") or [] if isinstance(p, str)]
        found.append({"at": check.get("at"), "date": check.get("date"), "card": ref if card else None,
                      "label": pest_watch.short_label(card["label"]) if card else None,
                      "signs": pest_watch.describe(check.get("flags") or [], lang),
                      "prevention": prevention[0] if prevention else None})
    return {"checks": len(inside), "flagged": sum(1 for c in inside if c.get("finding")), "confirmed": found}


def use(store: Any, thirst: Any, spans: list[tuple[float, float]], lang: str) -> dict[str, Any]:
    """What the plant used in its time in the box: water, feed and the lamp's power. Water counts the thirst log's
    litres and the feeding log's; a run the log holds without litres is counted as a run (no measured flow yet)."""
    litres, runs, minutes = 0.0, 0, 0.0
    for entry in thirst.entries() if thirst is not None else []:
        if entry.get("kind") != "water" or not any(a <= float(entry["at"]) < b for a, b in spans):
            continue
        if isinstance(entry.get("litres"), int | float):
            litres += entry["litres"]
        else:
            runs += 1
            minutes += float(entry.get("minutes") or 0)
    feed: dict[tuple[str, str], dict[str, Any]] = {}
    for a, b in spans:
        given = feeding.use_between(a, b, lang)
        litres += given["liters"]
        for item in given["items"]:
            row = feed.setdefault((item["label"], item["unit"]), {**item, "total": 0.0})
            row["total"] = round(row["total"] + item["total"], 2)
    hours = sum(lamp_hours(store.rows(a, b), a, b) for a, b in spans) if store is not None else 0.0
    return {"water": {"litres": round(litres, 2), "unmeasured_runs": runs, "unmeasured_minutes": round(minutes)},
            "feed": list(feed.values()),
            "power": {"kwh": round(hours * LAMP_WATTS / 1000, 1), "watts": LAMP_WATTS, "estimated": True} if hours else None}


def use_lines(used: Mapping[str, Any], lang: str) -> list[str]:
    water, power = used["water"], used["power"]
    out = [t("report.use_water", lang, litres=fmt_num(water["litres"], lang))] if water["litres"] else []
    if water["unmeasured_runs"]:
        out.append(t("report.use_runs", lang, runs=water["unmeasured_runs"], minutes=water["unmeasured_minutes"]))
    out += [t("report.use_feed", lang, label=i["label"], amount=fmt_qty(i["total"], i["unit"], lang)) for i in used["feed"]]
    if power:
        out.append(t("report.use_power", lang, kwh=fmt_num(power["kwh"], lang), watts=power["watts"]))
    return out


def lines(report: Mapping[str, Any], lang: str) -> dict[str, list[str]]:
    """The lists the page shows, as sentences with the numbers."""
    well, poor, nxt = [], [], []
    for row in report["climate"]:  # the card's label says day or night itself ("Temperatur bei Licht")
        what = {"label": row["label"], "stage": row["stage_label"], "inside": _num(row["inside"]), "rows": row["rows"]}
        if row["verdict"] == "well":
            well.append(t("report.well", lang, **what))
        elif row["verdict"] == "poor":
            side = "above" if (row["above"] or 0) >= (row["below"] or 0) else "below"
            poor.append(t(f"report.poor_{side}", lang, **what, off=_num(row[side])))
            if row["advice"] and row["advice"] not in nxt:
                nxt.append(row["advice"])
    for found in report["pests"]["confirmed"]:
        poor.append(t("report.pest", lang, date=date_text(str(found.get("date") or "")[:10], lang),
                      what=found["label"] or found["signs"]))
        if found["prevention"] and found["prevention"] not in nxt:
            nxt.append(found["prevention"])
    return {"well": well, "poor": poor, "next": nxt, "use": use_lines(report["use"], lang)}


def _num(value: Any) -> str:
    return "" if value is None else f"{value:.0f}"


def build(plant: str, *, store: Any, log: Any, profile: Mapping[str, Any] | None, pest: Any = None, thirst: Any = None,
          lang: str | None = None, now: float | None = None) -> dict[str, Any]:
    """The report for one plant's record (workspace id). Raises KeyError for an unknown plant."""
    lang = normalize(lang) or current_lang()
    now = time.time() if now is None else now
    with workspace.database() as db:
        try:
            data = workspace.record_data(db, plant)
        except workspace.Invalid as err:  # workspace_missing
            raise KeyError(plant) from err
    record = data["record"]
    spans = [(float(p["since"]), float(p["until"]) if p.get("until") is not None else now)
             for p in data["box_intervals"] if p.get("since") is not None]
    stages = list((log.data if log is not None else {}).get("stages") or [])
    measured = climate(store, profile, stages, spans, lang) if store is not None and spans else []
    start = min((a for a, _ in spans), default=now)
    end = max((b for _, b in spans), default=now)
    checks = pest.checks() if pest is not None else []
    report = {"plant": {"id": plant, "name": record.get("name"), "variety": record.get("variety"),
                        "started": record.get("started"), "ended": record.get("ended"),
                        "harvested": record.get("harvested")},
              "span": {"from": start if spans else None, "to": end if spans else None},
              "climate": measured, "pests": pests(checks, start, end, lang) if spans else
              {"checks": 0, "flagged": 0, "confirmed": []},
              "notes": {"summary": record.get("summary") or "",
                        "journal": [{"day": j.get("day"), "text": j.get("text")} for j in record.get("journal") or []]},
              "use": use(store, thirst, spans, lang),
              "thresholds": {"well": WELL, "poor": POOR, "min_rows": MIN_ROWS}}
    report["lines"] = lines(report, lang)
    return report


def register(app: web.Application, *, measurements: Any, plant_log: Any, profile: Any, pest: Any = None,
             thirst: Any = None) -> None:
    async def read(request: web.Request) -> web.Response:
        ident = request.match_info["plant"]
        if not NAME_RE.fullmatch(ident):
            return web.json_response({"error": "report_not_found"}, status=404)
        try:
            out = await asyncio.to_thread(build, ident, store=measurements, log=plant_log, profile=profile(),
                                          pest=pest, thirst=thirst, lang=current_lang())
        except KeyError:
            return web.json_response({"error": "report_not_found"}, status=404)
        except (OSError, sqlite3.Error):
            return web.json_response({"error": "report_storage"}, status=503)
        return web.json_response(out, headers={"Cache-Control": "no-store"})

    app.router.add_get("/api/grow-report/{plant}", read)
