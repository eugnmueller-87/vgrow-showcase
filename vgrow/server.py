"""Small web server for the phone: the page, three JSON endpoints, and the access key."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import socket
import sys
import time
from datetime import datetime, timedelta
from html import escape as html_escape
from pathlib import Path
from typing import Any

from aiohttp import web

from . import camera_prefs, config, feeding, hankpilot, i18n
from . import measurements as measurements_mod
from .alerts import Alerts
from .automation import HumidifierAutomation
from .autopilot import Autopilot, AutopilotHub
from .box import CommandRefused, check_port_lock
from .budget import BUDGET, Counted
from .camera import Camera
from .demo import seed_demo_autopilot, seed_demo_thirst, setup_demo
from . import portguard
from .portguard import PortGuard
from .pump_history import PumpHistory
from .watering import WateringController
from .light_schedule import LightSchedule, stage_hours
from .hank_protocol import ACTIONS as PROTOCOL_ACTIONS, Protocol
from . import device_check, lessons, light_view, plant_run
from .fan_stick import start_fan_stick
from .grow_plan import start_grow_plan
from .soil import start_soil
from . import thirst as thirst_mod
from .thirst import Thirst
from .i18n import UserError, localize, t
from .plant import MAX_AGE_DAYS, PRE_FLOWER_STAGES, PlantLog, age_check, age_info, age_text, current_stage, date_text
from .plant import effective_start, library_index, load_profile, parse_day, parse_number
from .plant import local_today as plant_today
from .plant import view as plant_view
from .vision import MAX_MANUAL_PER_DAY, PHOTO_HOUR, Vision, stage_suggestion
from . import pest_watch as pest_watch_mod
from .pest_watch import PestWatch

_LOGGER = logging.getLogger(__name__)
WEB = config.REPO / "web"
# A change in any of them reloads open pages; Hank's three files count too (spec 9.1), his two scene files, and the
# news list's two. tests/test_hank_scenes.py checks that every /static/ file of index.html is listed here.
PAGE_FILES = ("index.html", "app.js", "style.css", "i18n.js", "hank.js", "hank-i18n.js", "hank.css", "hank-scenes.js",
              "hank-scenes.css", "news.js", "news.css")
PAGE_FILES += ("wiki.js", "wiki-tools.js", "wiki.css")  # the wiki view (vgrow/wiki.py)
PAGE_FILES += ("cockpit.js", "cockpit.css", "workspace.js", "workspace.css")  # task routes and box/plant records
PAGE_FILES += ("community.js", "community.css")
PAGE_FILES += ("light-view.js",)
PAGE_FILES += ("thirst.js", "thirst.css")  # the Durst-Index card next to the watering schedule (vgrow/thirst.py)
PAGE_FILES += ("plan.js", "plan.css")  # the grow plan's card on the automation page (vgrow/grow_plan.py)
PAGE_FILES += ("water.js", "water.css")  # the water dashboard (#water), the device check and the E6's card
PAGE_FILES += ("protocol.js", "protocol.css")
PAGE_FILES += ("garden-logic.js", "garden-art.js", "garden.js", "garden.css")  # Hank's garden, the idle screen
PAGE_FILES += ("ds-components.css",)  # the design system's adopted components (scripts/build-tokens.py)
# The design system's compiled tokens (scripts/build-tokens.py), served under /design-system/ and counted like the page
# files. Only these: the rest of design-system/ mirrors the Design System artifact and is never served.
DESIGN_FILES = ("tokens.css",)
PUBLIC = {"/manifest.webmanifest", "/icon.svg"}  # home-screen install fetches these without the cookie
COOKIE = "vgrow_key"
# Lax: the cookie comes along when another page opens the cockpit, the owner's Leitstand (07.11.2026: "all these
# applications have to work"). Every GET route only reads, and a POST from another site still goes without it.
COOKIE_SAMESITE = "Lax"
COOKIE_DAYS = 400
# The key page reloads itself once: a cookie set before 07.11.2026 is Strict and stays home on a link from another
# page, the reload comes from the cockpit's own page and brings it along. A second miss shows the text, never a loop.
AGAIN = "again"
AGAIN_PAGE = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
              '<meta http-equiv="refresh" content="0;url={url}"><title>VGrow</title><p>{text}</p>')


def lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def design_dir() -> Path:
    return WEB.parent / "design-system"  # next to web/, so a test that moves WEB moves this too


def web_version() -> str:
    """Changes whenever a page file changes; deploys update web/ without a server restart."""
    files = [WEB / name for name in PAGE_FILES] + [design_dir() / name for name in DESIGN_FILES]
    times = [path.stat().st_mtime for path in files if path.exists()]
    return str(int(max(times))) if times else "0"


def page_html(html: str, version: str, lang: str, default_lang: str) -> str:
    """index.html as served: language attributes on <html>, every /static/ and /design-system/ file versioned."""
    # Versioned asset URLs: a browser never keeps running yesterday's app.js (seen 22.10.2026:
    # an open tab ran the pre-camera script for an hour and showed no camera).
    html = re.sub(r'((?:src|href)="/(?:static|design-system)/[^"?]+)"', rf'\1?v={version}"', html)
    match = re.search(r"<html\b([^>]*)>", html, flags=re.IGNORECASE)
    if match:
        others = re.sub(r'\s+(?:lang|data-default-lang)\s*=\s*"[^"]*"', "", match.group(1))
        html = (f'{html[:match.start()]}<html lang="{lang}" data-default-lang="{default_lang}"{others}>'
                f"{html[match.end():]}")
    return html


def interactive() -> bool:
    """stdout is a terminal someone looks at. Under launchd or pythonw it is server.log."""
    try:
        return bool(sys.stdout is not None and sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):  # no isatty, or a closed stream
        return False


def startup_banner(key: str, port: int, *, lan: str, ntfy: str, camera: str, tty: bool) -> str:
    """The lines printed at start. The link with the key only goes to an interactive terminal; anywhere else
    stdout ends up in a log file, so the banner names the key file instead."""
    if tty:
        return t("console.running", phone=f"http://{lan}:{port}/?key={key}",
                 local=f"http://localhost:{port}/?key={key}", ntfy=ntfy, camera=camera)
    return t("console.running_hidden", phone=f"http://{lan}:{port}/", local=f"http://localhost:{port}/",
             key_file=str(config.access_key_path()), ntfy=ntfy, camera=camera)


def alert_snapshot(box: Any, camera: Any | None) -> dict[str, Any]:
    """The box snapshot plus the camera status: what alerts, automation and the page read. "hidden": the owner put the
    GrowCam away (camera_prefs), its alarm then stays quiet."""
    snap = box.snapshot()
    snap["camera"] = ({**camera.status(), "hidden": "growcam" in camera_prefs.hidden()} if camera is not None
                      else {"configured": False})
    return snap


def stage_now(plant_log: PlantLog | None) -> dict[str, Any] | None:
    """The current plant stage, any source (owner, estimate, days); None without a profile or a log."""
    if plant_log is None:
        return None
    profile = load_profile()
    return current_stage(profile, plant_log) if profile else None


def feeding_week(profile: dict[str, Any] | None, plant_log: PlantLog) -> dict[str, Any]:
    """What the feeding planner needs besides the setup (feeding.context): the current stage (in the request's
    language, for its warnings), the stages the owner set for this run (where the flowering weeks count from), the
    plant's age (dates the plan, never a dose) and every setup (the fertilizer switch). amount_units: the page shows
    every unit (ml, g and spoons); feeding.view withholds a g card from a caller that does not say so (Hank's part-1
    code, review M1). log_feeding takes the same keywords and ignores the ones a log line does not need."""
    return {**feeding.context(profile, plant_log, i18n.current_lang()), "amount_units": feeding.PAGE_UNITS}


def age_preview(profile: dict[str, Any] | None, plant_log: PlantLog, query: Any, today: Any = None) -> dict[str, Any]:
    """GET /api/plant/age: what a stated age or start would mean, computed here so the page does no date arithmetic.
    Exactly one of weeks, days, life_week or date. The plant.age_check result, would_move_days (the implied start
    against the one that counts now, + later) and text, the sentence the page shows while the owner types. Writes
    nothing. ValueError (400) for anything else."""
    today = today or plant_today()
    given = [k for k in ("weeks", "days", "life_week", "date") if query.get(k) not in (None, "")]
    if len(given) != 1:
        raise UserError(t("plant.start_one_of"))
    kind = given[0]
    raw = str(query.get(kind)).strip()
    if kind == "date":
        day = parse_day(raw)
        if day is None:
            raise UserError(t("plant.start_bad_date", value=raw))
        kind, n = "days", (today - day).days
    else:
        if not raw.isdigit():
            raise UserError(t("plant.not_a_number", value=raw))
        n = int(raw)
    if kind == "weeks" and n > 52:
        raise UserError(t("plant.start_weeks_range", max=52))
    if n < 0:
        raise UserError(t("plant.start_future"))
    if n * (7 if kind == "weeks" else 1) > MAX_AGE_DAYS:
        raise UserError(t("plant.start_too_old", days=MAX_AGE_DAYS))
    stored, _by = effective_start(profile, plant_log)
    check = age_check(stored, kind, n, today)
    implied = parse_day(check["implied_start"]) if check["implied_start"] else None
    move = (implied - stored).days if implied is not None and stored is not None else None
    lang = i18n.current_lang()
    text = None
    if implied is not None:
        info = age_info(implied, today) or {"weeks": 0, "extra_days": 0, "life_week": 1, "days": 0}
        params = {"date": date_text(implied, lang), "age": age_text(info, lang), "life": info["life_week"],
                  "old": date_text(stored, lang) if stored else "", "n": abs(move or 0)}
        key = "plant.start_preview" if move is None else "plant.start_preview_same" if move == 0 else \
            "plant.start_preview_later" if move > 0 else "plant.start_preview_earlier"
        text = t(key, lang, **params)
    return {**check, "would_move_days": move, "text": text}


def wants_large(value: Any) -> bool:
    """The page's second tap for litres above confirm_above_liters: true, 1 or "1" ("true" in a query)."""
    return value is True or str(value).strip().lower() in ("1", "true")


def poll_handler(alerts: Alerts, automation: HumidifierAutomation | None, parts: dict[str, Any],
                 plant_log: PlantLog | None = None) -> Any:
    """The listener for every poll. The camera only exists after box.start(), so it is read from parts.
    The alerts get the plant stage in the snapshot: the humidity limit follows it (alerts.stage_limit).
    The autopilot (vgrow/autopilot.py), when serve() put one into parts, is the third step, after the alarms and the
    humidifier rule; a failure of one step never stops the others. The Durst-Index (vgrow/thirst.py), when serve() put
    one into parts, comes last: it only samples and reads, it switches nothing."""
    async def on_poll(b: Any) -> None:
        snap = alert_snapshot(b, parts.get("camera"))
        try:
            snap["stage"] = stage_now(plant_log)
        except Exception:  # an unreadable plant.json must not stop the alarms: they fall back to fixed limits
            _LOGGER.exception("Reading the plant stage failed")
        if parts.get("usb_camera") is not None:  # the close camera's own alarm: the rule usb_camera_offline
            snap["usb_camera"] = {**parts["usb_camera"].status(), "hidden": "usb" in camera_prefs.hidden()}
        if parts.get("light_day") is not None:  # the measured day against the stage's card: dark_period, short_day
            try:
                day = await parts["light_day"].read()
                snap["light_day"] = {**day, "hours_planned": stage_hours(load_profile(), snap.get("stage"))}
            except Exception:  # without the day those two rules say nothing; the other alarms run on
                _LOGGER.exception("Reading the measured light day failed")
        steps = [alerts.check] + ([automation.check] if automation is not None else [])
        if parts.get("autopilot") is not None:
            steps.append(parts["autopilot"].check)
        if parts.get("thirst") is not None:  # hook: the Durst-Index's ET sample, its pump port and its pushes
            steps.append(parts["thirst"].check)
        if parts.get("fan_stick") is not None:  # hook: the E6 on the E25 stick holds its step for lamp on or off
            steps.append(parts["fan_stick"].check)
        if parts.get("grow_plan") is not None:  # hook: the light of the day, the spectrum of the stage, the water watch
            steps.append(parts["grow_plan"].check)
        for step in steps:
            try:
                await step(snap)
            except Exception:  # a failing alert must not keep the automation from running, nor the other way round
                _LOGGER.exception("Poll step %s failed", step.__qualname__)
    return on_poll


# ---------- news (vgrow/news.py): a guest like Hank, nothing of the cockpit waits for it ----------

NEWS_DAYS, NEWS_LIMIT = 7, 10  # the page's list: the last 7 days, at most 10 items (docs/news-quellen.md, 5)
NEWS_CHECK_S = 600.0  # the watcher asks news.due() this often; due() keeps the fetch itself to once a day
NEWS_FIRST_CHECK_S = 60.0  # the first look a minute after the start: a day missed while the server was off comes soon
DEMO_NEWS_LINK = "https://example.org/"  # reserved for examples (RFC 2606): a demo item never points at a real source


def demo_news(now: datetime | None = None) -> dict[str, Any]:
    """The demo's fixed list, in the shape of news.view(): made-up sources in the request language, links to
    example.org, one source not reached and one with a possible gap, so every line of the page shows. Nothing is
    fetched or stored."""
    now = (now or datetime.now()).astimezone()

    def iso(dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    run = now.replace(hour=6, minute=4, second=0, microsecond=0)
    run = run if run <= now else run - timedelta(days=1)
    court_day = (run - timedelta(days=2)).replace(hour=0, minute=0)
    rows = [  # source id, kind, name key, title key, published, dated, first seen
        ("demo-official", "official", "news.demo.official", "news.demo.official_title", run - timedelta(days=1),
         "feed", run - timedelta(days=1)),
        ("demo-court", "court", "news.demo.court", "news.demo.court_title", court_day, "feed", run - timedelta(days=2)),
        ("demo-advocacy", "advocacy", "news.demo.advocacy", "news.demo.advocacy_title", run - timedelta(days=3),
         "feed", run - timedelta(days=3)),
        ("demo-research", "research", "news.demo.research", "news.demo.research_title", run - timedelta(days=5),
         "first_seen", run - timedelta(days=5)),
    ]
    items = []
    for number, (sid, kind, name_key, title_key, published, dated, seen) in enumerate(rows, start=1):
        attribution = (t("news.demo.court_attribution", date=f"{published:%d.%m.%Y}") if kind == "court"
                       else t(name_key))
        items.append({"id": f"demo{number}", "source_id": sid, "source_name": t(name_key), "publisher_kind": kind,
                      "rights": "free", "lang": i18n.current_lang(), "title": t(title_key), "title_cut": False,
                      "link": f"{DEMO_NEWS_LINK}#news-{number}", "published": iso(published), "dated": dated,
                      "first_seen": iso(seen), "matched": ["cannabis"], "attribution": attribution,
                      "link_target": "_blank"})
    sources = []
    for sid, kind, name_key in [*((r[0], r[1], r[2]) for r in rows),
                                ("demo-horticulture", "horticulture", "news.demo.horticulture")]:
        reached = sid != "demo-horticulture"
        sources.append({"id": sid, "name": t(name_key), "kind": kind, "rights": "free", "ok": reached,
                        "last_ok": iso(run) if reached else iso(run - timedelta(days=1)),
                        "last_error": None if reached else "HTTP 503", "backoff_until": None,
                        "gap": sid == "demo-research", "kept": 1 if reached else 0, "dropped": 0,
                        "recheck_due": False})
    return {"items": items, "last_fetch": iso(run), "sources": sources, "quiet_since": items[0]["first_seen"]}


def news_answer(demo: bool) -> dict[str, Any]:
    """What GET /api/news answers: news.view(days=7, limit=10), or the demo's fixed list, plus the install's privacy
    switch (config.NEWS_PRIVATE through news.is_private) and whether this is the demo."""
    from . import news  # imported here: a broken news module costs the list, never the cockpit

    data = demo_news() if demo else news.view(days=NEWS_DAYS, limit=NEWS_LIMIT)
    return {**data, "private": news.is_private(), "demo": demo}


class NewsWatcher:
    """The news task next to the server: every NEWS_CHECK_S it asks news.due() and runs news.refresh() when the
    day's fetch is due. Never two fetches at once (this flag, and news.fetch_all's own), every error caught and
    logged: a dead network or a broken feed never reaches the cockpit. Not started in the demo, which never fetches."""

    def __init__(self, *, interval: float = NEWS_CHECK_S, first: float = NEWS_FIRST_CHECK_S,
                 sleep: Any = asyncio.sleep) -> None:
        self.interval, self.first, self.sleep = interval, first, sleep
        self.running = False
        self.task: asyncio.Task[None] | None = None

    async def tick(self) -> bool:
        """One look: True when a fetch ran. A look while a fetch is running does nothing."""
        if self.running:
            return False
        self.running = True
        try:
            from . import news

            if not news.due(datetime.now().astimezone(), news.load_state()):
                return False
            new = await news.refresh()
            _LOGGER.info("News: %d new", len(new))
            return True
        except Exception:  # the watcher is a guest: log it and look again at the next interval
            _LOGGER.exception("News check failed")
            return False
        finally:
            self.running = False

    async def loop(self) -> None:
        await self.sleep(self.first)
        while True:
            await self.tick()
            await self.sleep(self.interval)

    def start(self) -> asyncio.Task[None]:
        self.task = asyncio.create_task(self.loop(), name="news_watch")
        self.task.add_done_callback(_forget_failure)
        return self.task

    async def stop(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.task = None


def start_news(demo: bool) -> NewsWatcher | None:
    """Read the privacy switch and start the watcher; None in the demo or when the start fails (logged)."""
    if demo:
        return None
    try:
        config.NEWS_PRIVATE = config.news_private()
        watcher = NewsWatcher()
        watcher.start()
        return watcher
    except Exception:
        _LOGGER.exception("The news watcher could not be started; the cockpit runs on")
        return None


async def stop_news(watcher: NewsWatcher | None) -> None:
    if watcher is None:
        return
    try:
        await watcher.stop()
    except Exception:
        _LOGGER.exception("Stopping the news watcher failed")


def make_app(box: Any, alerts: Alerts, key: str, camera: Any | None = None,
             plant_log: PlantLog | None = None, vision: Vision | None = None,
             automation: HumidifierAutomation | None = None, hank: Any = None,
             measurements: measurements_mod.MeasurementStore | None = None,
             autopilot: AutopilotHub | None = None, pump_history: PumpHistory | None = None,
             watering: WateringController | None = None,
             extra_cameras: dict[str, Any] | None = None, light_schedule: LightSchedule | None = None,
             thirst: Thirst | None = None, pest: PestWatch | None = None, soil: Any = None,
             fan_stick: Any = None, grow_plan: Any = None) -> web.Application:
    """The cockpit's app. measurements: the local store (vgrow/measurements.py) or None (the demo).
    soil: the soil sensor (vgrow/soil.py) or None: its view goes into /api/state as "soil", it switches nothing.
    fan_stick: the E6 on the E25 stick (vgrow/fan_stick.py) or None: its view goes into /api/state as "fan_stick", the
    owner's commands are the /api/cmd actions fan_stick_*.
    grow_plan: the plan of the run (vgrow/grow_plan.py) or None: its view goes into /api/state as "grow_plan", the owner's
    commands are the /api/cmd actions grow_plan_*. It tells the light timer the level of the day.
    pest: the pest watch (vgrow/pest_watch.py) or None: its view goes into /api/state as "pest", the owner answers a
    finding at POST /api/pest/answer; neither switches a device.
    thirst: the Durst-Index (vgrow/thirst.py) or None: its view goes into /api/state as "thirst" and to GET /api/thirst,
    the owner's inputs are the /api/cmd actions thirst_* (thirst.ACTIONS); none of them switches a device.
    watering: the independent pump timer; starts are bounded, its watchdog is managed by serve().
    pump_history: the read-only USB report history, also served behind the key at GET /api/pump-history.
    autopilot: the regulator of every box (vgrow/autopilot.py) or None; its view goes into /api/state as "autopilot"
    and to GET /api/autopilot (for Hank to read later), its controls are /api/cmd actions.
    hank: the assistant (vgrow.hank.core.Hank) or None. With Hank, his routes
    (/api/hank/*) are added and /api/cmd gets the journal wrapper; without him, or when adding them fails, the app is
    exactly the cockpit (spec 0.3 rule 2: Hank is a guest)."""
    plant_log = plant_log or PlantLog()
    if light_schedule is not None:
        def light_requirements():
            profile = load_profile()
            from .plant import profile_problem
            if profile_problem():
                return None, None
            stage = current_stage(profile or {}, plant_log)
            return profile, grow_plan.light_stage(profile, stage) if grow_plan is not None else stage
        light_schedule.requirements_source = light_requirements
    photos_from: Any = vision if vision is not None else Vision  # the demo serves its own placeholders
    cameras = {'growcam': camera} if camera is not None else {}
    cameras.update(extra_cameras or {})
    protocol = Protocol()

    def protocol_input():
        sampled_at = time.time()
        snap = box.snapshot()
        profile = load_profile()
        plant = plant_view(profile, plant_log, snap, "en")
        pilot_en = autopilot.view(snap, "en") if autopilot is not None else {}
        pilot_de = autopilot.view(snap, "de") if autopilot is not None else {}
        native = next((b for b in pilot_en.get("boxes", []) if b["id"] == "box-1"), {})
        german = next((b for b in pilot_de.get("boxes", []) if b["id"] == "box-1"), {})
        for device in native.get("devices", []):
            device["status_de"] = next((d.get("status") for d in german.get("devices", []) if d["id"] == device["id"]), None)
        if native.get("humidifier"):
            native["humidifier"]["status_de"] = (german.get("humidifier") or {}).get("status")
        for entry in native.get("log", []):
            entry["text_de"] = next((e.get("text") for e in german.get("log", []) if e["id"] == entry["id"]), None)
        run = (profile or {}).get("plant") or {}
        stage = current_stage(profile or {}, plant_log) or {}
        return {"snapshot": snap, "plant": plant, "pilot": native, "captured_at": sampled_at,
                "context": json.dumps([run.get("strain"), run.get("start_date"), stage.get("key"), stage.get("since")]),
                "automation": automation.view(snap) if automation is not None else {},
                "watering": watering.view(snap) if watering is not None else {},
                "light_schedule": light_schedule.view(snap, "en") if light_schedule is not None else {}}

    async def protocol_lifecycle(_app):
        async def watch():
            while True:
                try:
                    data = protocol_input()
                    worker = asyncio.create_task(asyncio.to_thread(protocol.observe, data))
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        await worker  # finish the transaction before teardown or a different test's state directory
                        raise
                except Exception:
                    protocol.error = True
                    _LOGGER.exception("Hank protocol observation failed; device control is unchanged")
                await asyncio.sleep(3)
        task = asyncio.create_task(watch(), name="hank_protocol")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def protocol_view(request):
        from . import workspace
        try:
            selected = request.headers.get("X-Workspace-Box", "box-1")
            if selected != "box-1" or request.headers.get("X-Workspace-Box") and not await asyncio.to_thread(workspace.validate_context, selected, request.headers.get("X-Workspace-Plant")):
                raise workspace.Invalid("workspace_profile")
            result = await asyncio.to_thread(protocol.view, int(request.query.get("limit", "50")), request.query.get("before"))
            result["storage_error"] = protocol.error
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except workspace.Invalid as err:
            return web.json_response({"error": str(err)}, status=409)
        except (ValueError, TypeError):
            return web.json_response({"error": "protocol_query"}, status=400)
        except (OSError, workspace.sqlite3.Error):
            return web.json_response({"error": "protocol_storage"}, status=503)

    @web.middleware
    async def language(request: web.Request, handler: Any) -> web.StreamResponse:
        # The page sends X-Lang on every API call. Pictures and the manifest are loaded by the browser,
        # which sends no header: their URLs carry ?lang= instead. Without either, the server language.
        lang = i18n.normalize(request.headers.get("X-Lang")) or i18n.normalize(request.query.get("lang"))
        token = i18n.REQUEST_LANG.set(lang)
        try:
            return await handler(request)
        finally:
            i18n.REQUEST_LANG.reset(token)

    def matches(given: str | None) -> bool:
        """An empty or missing value never matches, whatever the key; bytes, so any character is safe to compare.
        aiohttp decodes raw header bytes with surrogateescape (a cookie of \\xff\\xfe arrives as lone surrogates):
        surrogatepass turns any such text into bytes instead of raising, so it is a plain mismatch, never a 500."""
        if not given or not key:
            return False
        try:
            return secrets.compare_digest(str(given).encode("utf-8", "surrogatepass"), key.encode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            return False

    def keep_key(resp: web.StreamResponse) -> None:
        resp.set_cookie(COOKIE, key, max_age=COOKIE_DAYS * 86400, httponly=True, samesite=COOKIE_SAMESITE)

    @web.middleware
    async def guard(request: web.Request, handler: Any) -> web.StreamResponse:
        if request.path in PUBLIC or request.path.startswith("/vendor/"):
            return await handler(request)
        given = request.query.get("key")
        if given is not None:
            if not matches(given):
                raise web.HTTPForbidden(text=t("guard.wrong_key"))
            resp = web.HTTPFound("/")
            keep_key(resp)
            raise resp
        if not matches(request.cookies.get(COOKIE)):
            if request.method == "GET" and AGAIN not in request.query and "text/html" in request.headers.get("Accept", ""):
                url = html_escape(str(request.rel_url.update_query({AGAIN: "1"})), quote=True)
                raise web.HTTPForbidden(text=AGAIN_PAGE.format(url=url, text=html_escape(t("guard.need_key"))),
                                        content_type="text/html")
            raise web.HTTPForbidden(text=t("guard.need_key"))
        if AGAIN in request.query and request.method == "GET":  # in with the reload: the address without its marker
            raise web.HTTPFound(request.rel_url.with_query([(k, v) for k, v in request.query.items() if k != AGAIN]))
        if request.method == 'POST' and request.path.startswith('/api/hank/') and request.headers.get('X-Workspace-Box'):
            from . import workspace
            try:
                linked = await asyncio.to_thread(workspace.validate_context, request.headers['X-Workspace-Box'],
                                                 request.headers.get('X-Workspace-Plant'))
                if not linked:
                    raise workspace.Invalid('workspace_profile')
            except workspace.Invalid as err:
                return web.json_response({'error': str(err)}, status=409)
            except (OSError, workspace.sqlite3.Error):
                return web.json_response({'error': 'workspace_storage'}, status=503)
        response = await handler(request)
        if request.path == "/" and isinstance(response, web.Response):
            keep_key(response)  # renews it as Lax: a cookie set before 07.11.2026 is Strict
        return response

    async def index(_: web.Request) -> web.Response:
        html = page_html((WEB / "index.html").read_text(encoding="utf-8"), web_version(),
                         i18n.current_lang(), i18n.server_lang())
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def state(_: web.Request) -> web.Response:
        lang = i18n.current_lang()
        snap = alert_snapshot(box, camera)
        profile = load_profile()
        snap["alerts"] = alerts.view(current_stage(profile, plant_log) if profile else None)
        snap["ntfy"] = alerts.target is not None
        rules = config.port_rules()  # None: unreadable, and the box refuses every port-off until it is fixed
        for name, port in snap.get("ports", {}).items():
            rule = localize((rules or {}).get(name, {}), lang)
            port["label"] = rule.get("label")
            port["always_on"] = bool(rule.get("always_on")) or rules is None
        snap["web_version"] = web_version()  # the page reloads itself when this changes
        snap["hank_protocol"] = {**protocol.latest, "storage_error": protocol.error}
        plant = plant_view(profile, plant_log, snap, lang)
        if plant.get("configured"):
            try:
                plant["feeding"] = feeding.view(feeding.setup_of(profile, plant_log), lang,
                                                **feeding_week(profile, plant_log))
            except Exception:  # a broken feeding setup must not take the whole page down
                _LOGGER.exception("Feeding view failed")
                plant["feeding"] = None
        snap["plant"] = plant
        if automation is not None:
            snap["automation"] = automation.view(snap)
        if watering is not None:
            snap["watering"] = watering.view(snap)
        if light_schedule is not None:
            snap["light_schedule"] = light_schedule.view(snap, lang)
        if thirst is not None:
            try:
                snap["thirst"] = thirst.view(snap, lang)
            except Exception:  # a broken Durst-Index costs its card, never the page
                _LOGGER.exception("Thirst view failed")
        if pest is not None:
            try:
                snap["pest"] = pest.view(lang)
            except Exception:  # a broken pest watch costs its card, never the page
                _LOGGER.exception("Pest view failed")
        if soil is not None:
            try:
                snap["soil"] = soil.view()
            except Exception:  # a broken soil sensor costs its tile, never the page
                _LOGGER.exception("Soil view failed")
        if fan_stick is not None:
            try:
                snap["fan_stick"] = fan_stick.view()
            except Exception:  # a broken stick costs its card, never the page
                _LOGGER.exception("Fan stick view failed")
        snap["device_check"] = device_check.load()
        if autopilot is not None:
            try:
                snap["autopilot"] = autopilot.view(snap, lang)
            except Exception:  # a broken autopilot view must not take the page down
                _LOGGER.exception("Autopilot view failed")
            for box_view in (snap.get("autopilot") or {}).get("boxes") or []:
                # Hank's panel ("Hank regelt", vgrow/hankpilot.py): the same mode, worded for Hank, plus what blocks
                # him and what he does about each warning. Read-only; a failure costs the panel, never the page.
                try:
                    hum = snap.get("automation") if box_view.get("humidifier") is not None else None
                    box_view["hank"] = hankpilot.panel(autopilot.get(box_view["id"]), box_view, hum, lang)
                except Exception:
                    _LOGGER.exception("Hank's autopilot panel failed")
        if vision is not None:
            snap["vision"] = vision.view()
            # the camera's stage after STAGE_RUN_DAYS daily photos in a row, never backwards: the plant card offers it
            snap["vision"]["stage_suggest"] = stage_suggestion(snap["vision"].get("stage_run"), snap.get("plant"))
        if grow_plan is not None:  # after everything it reads
            try:
                snap["grow_plan"] = grow_plan.view(snap, lang)
            except Exception:  # a broken plan costs its card, never the page
                _LOGGER.exception("Grow plan view failed")
        snap["lang"] = lang
        snap["server_lang"] = i18n.server_lang()
        hidden_cameras = camera_prefs.hidden()
        snap['cameras'] = [{'id': ident, 'role': 'height_reference' if ident == 'growcam' else 'detail',
                           **cam.status(), 'hidden': ident in hidden_cameras} for ident, cam in cameras.items()]
        snap['camera_roles'] = {'height': 'growcam' if 'growcam' in cameras else None,
                                'detail': 'usb' if 'usb' in cameras else None}
        snap['camera_default'] = 'usb' if 'usb' in cameras else 'growcam'
        return web.json_response(snap)

    def need_camera(ident: str | None = None) -> Any:
        if ident is not None and not isinstance(ident, str):
            raise web.HTTPBadRequest()
        cam = cameras.get(ident or ('growcam' if camera is not None else 'usb'))
        if cam is None or not cam.configured:
            raise web.HTTPNotFound(text=json.dumps({"error": t("camera.none")}), content_type="application/json")
        return cam

    async def snapshot(request: web.Request) -> web.Response:
        cam = need_camera(request.query.get('camera'))
        try:
            picture = await cam.snapshot()
        except RuntimeError as err:
            return web.Response(status=502, text=str(err))
        return web.Response(body=picture, content_type=cam.content_type, headers={"Cache-Control": "no-store"})

    async def live(request: web.Request) -> web.StreamResponse:
        return await need_camera(request.query.get('camera')).live(request)

    async def photo_file(request: web.Request) -> web.StreamResponse:
        found = photos_from.photo_file(request.match_info["name"])
        if found is None:
            raise web.HTTPNotFound()
        if isinstance(found, Path):
            return web.FileResponse(found)
        body, content_type = found
        return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "no-store"})

    async def photos(_: web.Request) -> web.Response:
        return web.json_response({"photos": photos_from.photo_names()})

    async def library(_: web.Request) -> web.Response:
        return web.json_response(library_index())

    async def feeding_plan(request: web.Request) -> web.Response:
        """The planner for one watering: plant.feeding plus "plan" for ?liters= (read-only, nothing is logged).
        &large=1 is the page's second tap for litres above confirm_above_liters."""
        profile = load_profile()
        try:
            data = feeding.view(feeding.setup_of(profile, plant_log), i18n.current_lang(),
                                liters=request.query.get("liters"),
                                large=wants_large(request.query.get("large")), **feeding_week(profile, plant_log))
        except UserError as err:
            return web.json_response({"error": str(err)}, status=400)
        return web.json_response(data)

    async def plant_age(request: web.Request) -> web.Response:
        """What a stated age or start would mean (?weeks= | days= | life_week= | date=), read-only."""
        try:
            data = age_preview(load_profile(), plant_log, request.query)
        except UserError as err:
            return web.json_response({"error": str(err)}, status=400)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def news_list(_: web.Request) -> web.Response:
        """The news list behind the key cookie, like every route. A failure answers an error, never a 500 page."""
        try:
            data = await asyncio.to_thread(news_answer, bool(getattr(box, "demo", False)))  # file reads off the loop
        except Exception:
            _LOGGER.exception("News view failed")
            return web.json_response({"error": t("news.unavailable")}, status=503)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def measurements_summary(request: web.Request) -> web.Response:
        """The local store's days for the report (vgrow/measurements.py): ?days=N (1 to 400, default 7) and
        optionally ?band=probe_vpd:0.8:1.2, repeatable. Without a store (the demo, or a store that failed to start)
        an empty list that says so. Behind the key cookie like every route; no page uses it yet."""
        try:
            days = int(request.query.get("days", "7"))
            bands = measurements_mod.parse_bands(request.query.getall("band", []))
        except ValueError:
            return web.json_response({"error": t("measurements.bad_request")}, status=400)
        if measurements is None:
            return web.json_response({"store": False, "demo": bool(getattr(box, "demo", False)), "days": []})
        try:
            aux = (getattr(box, "reported", None) or {}).get("aux35Dev")  # the water sensor's type decodes waterLv
            data = await asyncio.to_thread(measurements.summary, days, bands, aux=aux)
        except Exception:
            _LOGGER.exception("Measurement summary failed")
            return web.json_response({"error": t("measurements.unavailable")}, status=503)
        return web.json_response({"store": True, "demo": False, **data}, headers={"Cache-Control": "no-store"})

    async def recordings(request: web.Request) -> web.Response:
        ident = request.query.get('camera') or ('growcam' if camera is not None else 'usb')
        cam = cameras.get(ident)
        if request.query.get('camera') and cam is None:
            raise web.HTTPNotFound()
        return web.json_response({"recordings": cam.recordings() if cam else []})

    async def recording_file(request: web.Request) -> web.StreamResponse:
        path = need_camera(request.query.get('camera')).path_for(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path)  # supports Range requests, which Safari needs for video

    async def autopilot_view(_: web.Request) -> web.Response:
        """Every box's autopilot: mode, devices, the step log with its texts in the request language. Read-only."""
        if autopilot is None:
            return web.json_response({"boxes": []})
        return web.json_response(autopilot.view(alert_snapshot(box, camera), i18n.current_lang()),
                                 headers={"Cache-Control": "no-store"})

    async def pump_history_view(_: web.Request) -> web.Response:
        reader = pump_history or PumpHistory(measurements)
        data = await asyncio.to_thread(reader.brief, box.snapshot())
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def soil_history(request: web.Request) -> web.Response:
        """The water dashboard's chart: the soil sensor's logged readings, ?hours=N (1 to 720, default 24)."""
        try:
            hours = max(1, min(720, int(request.query.get("hours", "24"))))
        except ValueError:
            return web.json_response({"error": t("measurements.bad_request")}, status=400)
        if soil is None:
            return web.json_response({"rows": []})
        rows = await asyncio.to_thread(soil.history, hours)
        return web.json_response({"rows": rows}, headers={"Cache-Control": "no-store"})

    async def thirst_view(_: web.Request) -> web.Response:
        """The Durst-Index card's data in the request language, read only (the same as /api/state "thirst")."""
        if thirst is None:
            return web.json_response({"error": t("thirst.none")}, status=404)
        try:
            data = thirst.view(box.snapshot(), i18n.current_lang())
        except Exception:
            _LOGGER.exception("Thirst view failed")
            return web.json_response({"error": t("thirst.none")}, status=503)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    def pilot(body: dict[str, Any]) -> Autopilot | None:
        if autopilot is None:
            return None
        try:
            return autopilot.get(body.get("box"))
        except KeyError:
            raise UserError(t("ap.refused.unknown_box", box=str(body.get("box")))) from None

    def manual(body: dict[str, Any], device: str, target: Any) -> None:
        """A command of the owner pauses that device in the autopilot (manual always wins)."""
        found = pilot(body)
        if found is not None:
            found.manual(device, target)

    async def history(request: web.Request) -> web.Response:
        try:
            hours = int(request.query.get("hours", "24"))
        except ValueError:
            hours = 24
        try:
            rows = await box.history(hours)
        except Exception as err:
            return web.json_response({"error": t("error.cloud", reason=str(err) or type(err).__name__)}, status=502)
        return web.json_response({"rows": rows})

    async def command(request: web.Request) -> web.Response:
        body = await request.json()
        action = body.get("action")
        if request.headers.get("X-Workspace-Box") and action not in ("set_lang", "test_push"):
            from . import workspace
            try:
                linked = await asyncio.to_thread(workspace.validate_context, request.headers['X-Workspace-Box'],
                                                 request.headers.get('X-Workspace-Plant'))
                if not linked and (str(action).startswith(('plant_', 'feeding_', 'thirst_', 'grow_plan_')) or action == "light_schedule_auto"):
                    raise workspace.Invalid('workspace_profile')
                if body.get('box') and body['box'] != request.headers['X-Workspace-Box']:
                    raise workspace.Invalid('workspace_context')
            except workspace.Invalid as err:
                return web.json_response({'error': str(err)}, status=409)
            except (OSError, ValueError, workspace.sqlite3.Error):
                return web.json_response({'error': 'workspace_storage'}, status=503)
        cbox: Any = Counted(box, BUDGET)  # every command of the page counts in the account-wide budget, never held
        try:
            # The owner's command pauses the device in the autopilot BEFORE it is sent: a poll running meanwhile then
            # holds instead of sending its own value after the owner's (review 1, S3). Manual always wins.
            if action == "light":
                if light_schedule is not None:
                    # Off stops the timer until it is switched on again; any other level holds it until the next
                    # scheduled switch (owner, 01.11.2026: off must not come back on with the next scheduled ON).
                    if int(body["level"]) == 0:
                        light_schedule.stop()
                    else:
                        light_schedule.manual()
                manual(body, "light", int(body["level"]))
                await cbox.set_light(int(body["level"]))
            elif action == "light_schedule_config":
                if light_schedule is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                await light_schedule.configure(body)
            elif action == "light_schedule_auto":
                if light_schedule is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                await light_schedule.resume_automatic(body.get("fingerprint"))
            elif action == "spectrum":
                await cbox.set_spectrum(str(body["name"]))
            elif action == "cfan":
                manual(body, "cfan", None if body.get("natural_wind") else body.get("step"))
                await cbox.set_cfan(body.get("step"), bool(body.get("natural_wind")))
            elif action == "dfan":
                manual(body, "dfan", int(body["step"]))
                await cbox.set_dfan(int(body["step"]))
            elif action == "port":
                check_port_lock(str(body["port"]), bool(body["on"]),
                                box_id=getattr(cbox, "box_id", "box-1"))  # the box checks it again itself
                if watering is not None and str(body["port"]) == watering.port:
                    if type(body["on"]) is not bool:
                        raise ValueError("on: boolean")
                    if body["on"]:
                        await watering.start()
                    else:
                        await watering.stop()
                else:
                    await cbox.set_port(str(body["port"]), bool(body["on"]))
                if automation is not None and str(body["port"]) == automation.port:
                    automation.manual_switch()
            elif action in ("watering_config", "watering_start", "watering_stop"):
                if watering is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                if action == "watering_config":
                    await watering.configure(body)
                elif action == "watering_start":
                    await watering.start()
                else:
                    await watering.stop()
            elif action in thirst_mod.ACTIONS:
                # The owner's pot, checks, weighings, hand waterings, refills, saucer and arrival answers and jug runs
                # (vgrow/thirst.py): logged under STATE_DIR, no device is switched. A second tap comes back as 409
                # with "ask" and its sentence.
                if thirst is None:
                    return web.json_response({"error": t("thirst.none")}, status=409)
                try:
                    result = getattr(thirst, thirst_mod.ACTIONS[action])(body)
                except thirst_mod.ModelError:
                    return web.json_response({"error": t("thirst.decision.model")}, status=409)
                if result.get("ok") is False and result.get("ask"):
                    return web.json_response({"error": result.get("text"), "ask": result["ask"]}, status=409)
                return web.json_response(result)
            elif action == "pest_answer":
                # The owner's look at a pest finding: yes, no or unsure (vgrow/pest_watch.py). Logged, no device moves.
                if pest is None:
                    return web.json_response({"error": t("pest.no_camera")}, status=409)
                try:
                    record = pest.answer(str(body.get("id") or ""), str(body.get("answer") or ""),
                                         str(body.get("note") or ""))
                except KeyError:
                    return web.json_response({"error": t("pest.unknown_check")}, status=404)
                except ValueError as err:
                    return web.json_response({"error": str(err)}, status=400)
                return web.json_response({"ok": True, "finding": record["finding"]})
            elif action == "pest_identify":
                # What the owner found a confirmed finding to be: one of its cards, "other", or "" to choose again.
                if pest is None:
                    return web.json_response({"error": t("pest.no_camera")}, status=409)
                try:
                    record = pest.identify(str(body.get("id") or ""), str(body.get("card") or ""))
                except KeyError:
                    return web.json_response({"error": t("pest.unknown_check")}, status=404)
                except ValueError as err:
                    return web.json_response({"error": str(err)}, status=400)
                return web.json_response({"ok": True, "finding": record["finding"]})
            elif action == "refresh":
                await box.refresh_shadow()
            elif action in ("record_start", "record_stop", "record_delete"):
                cam = need_camera(body.get('camera'))
                try:
                    if action == "record_start":
                        return web.json_response({"ok": True, "file": await cam.start_recording()})
                    if action == "record_stop":
                        return web.json_response({"ok": True, "file": await cam.stop_recording()})
                    cam.delete(str(body["name"]))
                except ValueError as err:
                    return web.json_response({"error": str(err)}, status=409)
            elif action == "auto_humidifier":
                if automation is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                automation.set_enabled(bool(body["on"]))
            elif action == "humidifier_target":
                if automation is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                automation.set_target(body["off_at"])
            elif action in ("humidifier_tank_config", "humidifier_refill", "humidifier_mist_confirm"):
                if automation is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                if action == "humidifier_tank_config":
                    automation.tank.configure(body.get("capacity_ml"), body.get("rate_ml_h"),
                                              button_start=body.get("button_start"), model=body.get("model"),
                                              mist_mode=body.get("mist_mode"))
                elif action == "humidifier_refill":
                    automation.tank.refill(body.get("was_empty"), amount_ml=body.get("amount_ml"))
                else:
                    if (automation.guard.owner_device("usb2") != "humidifier"
                            or (box.snapshot().get("ports", {}).get("usb2") or {}).get("on") is not True):
                        raise ValueError(t("tank.mist_power"))
                    automation.tank.confirm_mist()
            elif action in ("autopilot_mode", "autopilot_device", "autopilot_undo", "autopilot_lamp", "ports_confirm",
                            "port_assign"):
                found = pilot(body)
                if found is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                if action == "autopilot_mode":
                    found.set_mode(str(body["mode"]))
                elif action == "autopilot_device":
                    level = body.get("level")
                    found.set_device(str(body["device"]), None if level is None else str(level),
                                     None if body.get("app") is None else bool(body.get("app")))
                elif action == "autopilot_undo":
                    return web.json_response({"ok": True, "undone": await found.undo(str(body["id"]))})
                elif action == "autopilot_lamp":
                    found.confirm_lamp()
                elif action == "port_assign":  # "An USB 2 hängt: Befeuchter / Pumpe / anderes" (two taps)
                    port = str(body["port"])
                    if bool(((config.port_rules() or {}).get(port) or {}).get("always_on")):
                        return web.json_response({"error": t("ap.refused.always_on", port=port)}, status=409)
                    listed = portguard.configured_device(port)
                    if str(body["device"]) == portguard.HUMIDIFIER and listed not in (None, portguard.HUMIDIFIER):
                        # ports.json names another device there and vetoes the humidifier rule anyway: saying
                        # "humidifier" would be stored and change nothing. Naming a pump or "other" stays allowed.
                        rule = localize((config.port_rules() or {}).get(port) or {}, i18n.current_lang())
                        return web.json_response({"error": t("ap.refused.not_humidifier", port=port,
                                                             device=rule.get("label") or listed)}, status=409)
                    if not found.guard.assign(port, str(body["device"]), box.snapshot()):
                        return web.json_response({"error": t("ap.refused.no_ports")}, status=409)
                    return web.json_response({"ok": True, "port": port, "device": str(body["device"])})
                else:
                    # Confirming a changed assignment forgets the owner's word for every port whose entry changed:
                    # whatever hangs on USB 2 now is switched by humidity only once he names it again (review 1, S8).
                    moved = found.guard.confirm(box.snapshot())
                    if moved is None:
                        return web.json_response({"error": t("ap.refused.no_ports")}, status=409)
                    return web.json_response({"ok": True, "unnamed": moved})
            elif action == "plant_stage":
                profile = load_profile() or {}
                if str(body["key"]) not in {s["key"] for s in profile.get("stages", [])}:
                    return web.json_response({"error": t("plant.unknown_stage")}, status=400)
                plant_log.set_stage(str(body["key"]))
            elif action == "plant_water":
                plant_log.add_water(parse_number(body.get("ph")), parse_number(body.get("ec")))
            elif action == "plant_height":
                plant_log.add_height(parse_number(body.get("cm")), estimated=body.get("estimated", False),
                                     camera=body.get("camera"))  # same day replaces, never a second line
            elif action == "plant_bloom":
                # The owner's first white pistils (the plant card's field, two taps): into the plant log under
                # STATE_DIR, never into plant.json. It counts before the camera's estimate for the stage, the stage
                # day count, the feeding week and Hank.
                profile = load_profile() or {}
                stage = current_stage(localize(profile, i18n.current_lang()), plant_log) if profile else None
                if stage and stage.get("set_by") == "manual" and stage.get("key") in PRE_FLOWER_STAGES:
                    # A stage before flowering that the owner set wins over any bloom start (plant.bloom_start):
                    # saving the date would say "saved" and change nothing (review 2, 23.10.2026).
                    return web.json_response({"error": t("plant.bloom_stage_before",
                                                         stage=stage.get("label") or stage["key"])}, status=409)
                start = effective_start(profile, plant_log)[0]  # the owner's start before plant.json's
                return web.json_response({"ok": True, "date": plant_log.set_bloom_start(body.get("date"),
                                                                                        start=start)})
            elif action == "feeding_confirm":
                # The card and the fingerprint the page showed: a chart that changed since is not confirmed.
                record = feeding.confirm(feeding.setup_of(load_profile(), plant_log), body.get("card"),
                                         body.get("fingerprint"))
                return web.json_response({"ok": True, "confirmed_at": record["confirmed_at"]})
            elif action == "feeding_log":
                # Only the litres come from the page; every amount is computed here from the chart. The card and the
                # fingerprint the page showed must still be what the server uses: a stale tab (BioBizz in ml) never
                # logs after a switch to VIVOSUN in g (critique 23.10.2026, must 3).
                profile = load_profile()
                entry = feeding.log_feeding(feeding.setup_of(profile, plant_log), body.get("liters"),
                                            large=wants_large(body.get("large")), lang=i18n.current_lang(),
                                            page_card=body.get("card"), page_fingerprint=body.get("fingerprint"),
                                            **feeding_week(profile, plant_log))
                return web.json_response({"ok": True, "entry": entry})
            elif action == "feeding_setup":
                # The fertilizer the owner gives from now on (two taps on the page): into the plant log, never into
                # plant.json. Only one at a time; nothing is carried over (confirmations are per card).
                setups = feeding.setups_of(load_profile())
                chosen = plant_log.set_feeding_choice(body.get("setup"), setups.keys())
                return web.json_response({"ok": True, "setup": chosen})
            elif action == "plant_start":
                # The plant's start (germination), two taps on the plant card: a date, or an age in weeks or days.
                # Into the plant log, never into plant.json. It dates the feeding plan; it never changes a dose.
                profile = load_profile() or {}
                run_start = parse_day((profile.get("plant") or {}).get("start_date"))
                stored = plant_log.set_start_date(body.get("date"), age_weeks=body.get("age_weeks"),
                                                  age_days=body.get("age_days"), run_start=run_start,
                                                  current=effective_start(profile, plant_log)[0])
                return web.json_response({"ok": True, "date": stored})
            elif action == "plant_apply":
                stage = current_stage(load_profile() or {}, plant_log)
                preset = (stage or {}).get("preset") or {}
                if not preset:
                    return web.json_response({"error": t("plant.no_preset")}, status=409)
                done = []
                if "light_percent" in preset:
                    # Safety rule (mine, not from a source): at most +25 points per tap, so a jump from
                    # 25 % to 100 % cannot burn the tips. Tap again after a day or two.
                    target = int(preset["light_percent"])
                    now_level = (box.snapshot().get("light") or {}).get("level") or 0
                    step_to = min(target, now_level + 25) if now_level > 0 else min(target, 50)
                    manual(body, "light", step_to)
                    await cbox.set_light(step_to)
                    level, goal = i18n.fmt_qty(step_to, "%"), i18n.fmt_qty(target, "%")
                    done.append(t("plant.applied_light", level=level) if step_to >= target
                                else t("plant.applied_light_partial", level=level, target=goal))
                if "spectrum" in preset:
                    await cbox.set_spectrum(str(preset["spectrum"])); done.append(t("plant.applied_spectrum"))
                if "cfan_step" in preset:
                    manual(body, "cfan", int(preset["cfan_step"]))
                    await cbox.set_cfan(int(preset["cfan_step"])); done.append(t("plant.applied_cfan"))
                if "dfan_step" in preset:
                    manual(body, "dfan", int(preset["dfan_step"]))
                    await cbox.set_dfan(int(preset["dfan_step"])); done.append(t("plant.applied_dfan"))
                return web.json_response({"ok": True, "applied": done})
            elif action in ("plant_analyze", "plant_observe"):
                selected = camera
                camera_id = "growcam"
                if action == "plant_observe":
                    camera_id = body.get("camera") or ("usb" if "usb" in cameras else "growcam")
                    selected = need_camera(camera_id)
                if vision is None or selected is None or not selected.configured:
                    return web.json_response({"error": t("camera.none")}, status=409)
                if vision.manual_runs_today() >= MAX_MANUAL_PER_DAY:
                    return web.json_response({"error": t("vision.limit", count=MAX_MANUAL_PER_DAY)}, status=409)
                if vision.busy or vision.job is not None and not vision.job.done():
                    return web.json_response({"error": t("vision.busy")}, status=409)
                lang = i18n.current_lang()  # a manual reading speaks the page's language
                snap_now = box.snapshot()
                # The local model needs minutes on the Mac's CPU: start it and let the page poll /api/state.
                if action == "plant_observe":
                    source = {"id": camera_id, "name": selected.name, "resolution": selected.status().get("resolution")}
                    task = vision.run(snap_now, None, lang, camera=selected, source=source, observation_only=True)
                else:
                    task = vision.run(snap_now, plant_view(load_profile(), plant_log, snap_now, lang), lang)
                vision.job = asyncio.create_task(task, name="plant_analyze")
                return web.json_response({"ok": True, "started": True, "model": vision.model})
            elif action == "set_lang":
                lang = i18n.normalize(body.get("lang"))
                if lang is None:
                    return web.json_response({"error": t("error.unknown_lang", code=repr(body.get("lang")))},
                                             status=400)
                return web.json_response({"ok": True, "server_lang": i18n.set_server_lang(lang)})
            elif action == "camera_hide":  # the owner puts a camera's card away, or brings it back (camera_prefs)
                ident = body.get("camera")
                if not isinstance(ident, str) or ident not in cameras:
                    return web.json_response({"error": t("camera.none")}, status=404)
                return web.json_response({"ok": True,
                                          "hidden": camera_prefs.set_hidden(ident, body.get("hidden", True) is not False)})
            elif action == "test_push":
                ok = await alerts.push(t("push.test_title"), t("push.test_body"), priority=3)
                return web.json_response({"ok": ok}, status=200 if ok else 502)
            elif action == "soil_calibrate":
                if soil is None:
                    return web.json_response({"error": t("soil.not_found")}, status=409)
                return web.json_response(soil.calibrate(body))
            elif str(action).startswith("grow_plan_"):
                if grow_plan is None:
                    return web.json_response({"error": t("gp.err.no_plan")}, status=409)
                if action == "grow_plan_measure":
                    return web.json_response({"ok": True, "reading": await grow_plan.measure()})
                elif action == "grow_plan_height":
                    await grow_plan.correct_height(parse_number(body.get("cm")))
                elif action == "grow_plan_settings":
                    grow_plan.settings(body, box.snapshot())
                elif action == "grow_plan_mark":
                    grow_plan.mark(body.get("id"), body.get("done", True) is True)
                elif action == "grow_plan_probe":
                    grow_plan.probe_hung()
                elif action == "grow_plan_light_resume":
                    grow_plan.resume_light()
                elif action == "grow_plan_pump_test":
                    await grow_plan.pump_test()
                elif action == "grow_plan_water_now":
                    await grow_plan.water_now()
                elif action == "grow_plan_watch_on":
                    await grow_plan.watch_on(current_stage(load_profile() or {}, plant_log))
                elif action == "grow_plan_watch_off":
                    await grow_plan.watch_off()
                else:
                    return web.json_response({"error": t("gp.err.no_plan")}, status=400)
            elif action in ("fan_stick_step", "fan_stick_osc", "fan_stick_config"):
                if fan_stick is None:
                    return web.json_response({"error": t("box.no_connection")}, status=409)
                if action == "fan_stick_step":
                    await fan_stick.set_step(body.get("step"))
                elif action == "fan_stick_osc":
                    await fan_stick.set_osc(body.get("on"))
                else:
                    return web.json_response(fan_stick.configure(body))
            elif action == "run_activate":
                # The owner's choice: the cockpit runs on this plant from now on (vgrow/plant_run.py). No device moves
                # here; the automation reads the new plant's stage at its next poll.
                from . import workspace

                if plant_log is None:
                    return web.json_response({"error": t("auto.none")}, status=409)
                record = await asyncio.to_thread(workspace.plant_record, body.get("plant"))
                if record["box"] != workspace.NATIVE_BOX:
                    return web.json_response({"error": t("run.not_this_box")}, status=409)
                return web.json_response(plant_run.activate(record, plant_log, stage=body.get("stage") or None))
            elif action == "device_check_record":
                return web.json_response(device_check.record(body))
            elif action == "device_check_confirm":
                return web.json_response(device_check.confirm(body))
            else:
                return web.json_response({"error": t("error.unknown_action", action=repr(action))}, status=400)
        except (CommandRefused, feeding.FeedingRefused) as err:
            return web.json_response({"error": str(err)}, status=409)
        except UserError as err:
            return web.json_response({"error": str(err)}, status=400)
        except (KeyError, ValueError) as err:
            return web.json_response({"error": t("error.bad_request", reason=str(err))}, status=400)
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[language, guard])
    app.cleanup_ctx.append(protocol_lifecycle)
    cmd_handler: Any = command
    if hank is not None:
        try:
            from .hank import api as hank_api

            # The command runs unchanged with every guard; the wrapper only adds the journal line afterwards
            # (spec 5.5, 8). Hank's routes sit behind the same key cookie as everything else.
            wrapped = hank_api.journal_wrap(command, hank)
            hank_api.register(app, hank)
            cmd_handler = wrapped
        except Exception:  # spec 10.1 rule 7: a broken Hank never keeps the cockpit from serving
            _LOGGER.exception("Hank's routes could not be added; the cockpit runs without Hank")
    ordinary_command = cmd_handler

    async def recorded_command(request):
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return await ordinary_command(request)
        before, origin = None, "manual"
        if isinstance(body, dict) and body.get("action") in PROTOCOL_ACTIONS and request.headers.get("X-Workspace-Box", "box-1") == "box-1":
            try:
                before = protocol_input()
                card = hank.registry.get(body.get("proposal")) if hank is not None and isinstance(body.get("proposal"), str) else None
                if card is not None and card.body == {k: v for k, v in body.items() if k != "proposal"}:
                    origin = "hank"
            except Exception:
                _LOGGER.exception("Hank protocol could not capture the command baseline")
        response = await ordinary_command(request)
        if before is not None:
            try:
                error = json.loads(response.body or b"{}").get("error") if response.status >= 400 else None
                await asyncio.to_thread(protocol.capture, body, response.status, before, protocol_input(), origin, error)
            except Exception:
                protocol.error = True
                _LOGGER.exception("Hank protocol receipt failed; the command response is unchanged")
        return response

    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/autopilot", autopilot_view)
    app.router.add_get("/api/pump-history", pump_history_view)
    app.router.add_get("/api/thirst", thirst_view)

    async def grow_plan_photo(_: web.Request) -> web.StreamResponse:
        """The picture of the last height reading: the page draws the line it read on top."""
        if grow_plan is None or not grow_plan.photo_path().exists():
            return web.Response(status=404)
        return web.FileResponse(grow_plan.photo_path(), headers={"Cache-Control": "no-store", "Content-Type": "image/jpeg"})

    app.router.add_get("/api/grow_plan/photo", grow_plan_photo)
    app.router.add_get("/api/soil/history", soil_history)
    app.router.add_get("/api/history", history)
    app.router.add_post("/api/cmd", recorded_command)
    app.router.add_get("/api/protocol", protocol_view)
    app.router.add_get("/api/recordings", recordings)
    app.router.add_get("/api/library", library)
    from . import workspace
    workspace.register(app, name=lambda: box.snapshot().get('device'), profile=load_profile, log=plant_log, measurements=measurements)
    from . import grow_report  # a record's report: lief gut, lief nicht gut, nächstes Mal (read-only)
    grow_report.register(app, measurements=measurements, plant_log=plant_log, profile=load_profile, pest=pest, thirst=thirst)
    lessons.register(app)
    light_view.register(app, box=box, measurements=measurements, controller=light_schedule)
    app.router.add_get("/api/feeding", feeding_plan)
    app.router.add_get("/api/plant/age", plant_age)
    app.router.add_get("/api/news", news_list)
    from . import community
    community.register(app, demo=bool(box.snapshot().get('demo')))
    app.router.add_get("/api/measurements/summary", measurements_summary)
    # ---------- wiki (vgrow/wiki.py): the cards of kartei/ as pages; a broken wiki costs the wiki, not the cockpit
    try:
        from . import wiki

        wiki.register(app)
    except Exception:
        _LOGGER.exception("The wiki's routes could not be added; the cockpit runs without the wiki")
    app.router.add_get("/photos/{name}", photo_file)
    app.router.add_get("/api/photos", photos)
    app.router.add_get("/camera/snapshot.jpg", snapshot)
    app.router.add_get("/camera/live.mjpg", live)
    app.router.add_get("/recordings/{name}", recording_file)
    async def manifest(_: web.Request) -> web.Response:
        # The file holds the fixed parts; name and description follow ?lang= (the page sets it), else the
        # server language. The name always carries the unofficial marker.
        data = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
        data.update(name=t("meta.title"), short_name=t("meta.title"), description=t("meta.description"),
                    lang=i18n.current_lang())
        return web.json_response(data, content_type="application/manifest+json",
                                 dumps=lambda obj: json.dumps(obj, ensure_ascii=False))

    async def icon(_: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB / "icon.svg")

    app.router.add_get("/manifest.webmanifest", manifest)
    app.router.add_get("/icon.svg", icon)
    async def design_file(request: web.Request) -> web.FileResponse:
        return web.FileResponse(design_dir() / request.path.removeprefix("/design-system/"))

    app.router.add_static("/static/", WEB, show_index=False)
    app.router.add_static("/vendor/", WEB / "vendor", show_index=False)
    for name in DESIGN_FILES:
        app.router.add_get(f"/design-system/{name}", design_file)
    return app


async def daily_photo(box: Any, camera: Camera, vision: Vision, plant_log: PlantLog, alerts: Alerts) -> None:
    """From noon on, once a day while the light is on: photo, the local model's reading, push if it says 'act'."""
    while True:
        await asyncio.sleep(600)
        try:
            snap = box.snapshot()
            light_on = ((snap.get("light") or {}).get("level") or 0) > 0
            due = datetime.now().hour >= PHOTO_HOUR and vision.due()
            if not (due and light_on and camera.configured and camera.reachable):
                continue
            lang = i18n.server_lang()  # nobody is asking: the daily reading speaks the server language
            record = await vision.run(snap, plant_view(load_profile(), plant_log, snap, lang), lang)
            urgent = [i for i in (record.get("result") or {}).get("issues", []) if i.get("severity") == "act"]
            if urgent:
                await alerts.push(t("push.plant", lang), "; ".join(f"{i['issue']} ({i['where']})" for i in urgent),
                                  priority=4)
        except Exception:
            _LOGGER.exception("Daily plant photo failed")


def page_link(fragment: str) -> str | None:
    """VGROW_PAGE_URL from ~/.vgrow.env (the cockpit as the phone reaches it, without the key) plus a page fragment."""
    base = (config.read_env(config.ENV_FILE).get("VGROW_PAGE_URL") or "").strip().rstrip("/")
    return f"{base}/#{fragment}" if base.startswith(("http://", "https://")) and "key=" not in base else None


async def pest_loop(box: Any, pest: PestWatch, alerts: Alerts) -> None:
    """While the light is on and a plant is set up: the pest checklist every hour, a push for each new sign.

    The push names the signs and opens the AI insights panel; the owner answers there (yes, no, unsure)."""
    while True:
        await asyncio.sleep(600)
        try:
            snap = box.snapshot()
            light_on = ((snap.get("light") or {}).get("level") or 0) > 0
            if not (light_on and pest.due() and load_profile() and pest.camera() is not None):
                continue
            lang = i18n.server_lang()
            record = await pest.run(lang)
            fresh = pest.to_push(record)
            if fresh and await alerts.push(t("pest.title", lang), t("pest.push_body", lang,
                                           signs=pest_watch_mod.describe(fresh, lang)),
                                           priority=4, click=page_link("grow/insights")):
                pest.mark_pushed(record)
        except Exception:
            _LOGGER.exception("Pest check failed")


def bind_socket(host: str, port: int) -> socket.socket:
    """The listening socket, bound and listening before anything logs in. A taken port (a second server, the demo on
    the same port) fails here, so a start that cannot serve never reaches VIVOSUN. As asyncio's create_server does:
    SO_REUSEADDR on POSIX, so a restart does not wait out TIME_WAIT; never on Windows, where it would let a second
    server bind the same port."""
    family, kind, proto, _, address = socket.getaddrinfo(host or None, port, type=socket.SOCK_STREAM,
                                                         flags=socket.AI_PASSIVE)[0]
    sock = socket.socket(family, kind, proto)
    try:
        if os.name == "posix":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)
        sock.listen(128)
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


class HankBox:
    """The box as Hank's collector sees it: snapshot() only, with the autopilot's brief added under "autopilot" (mode,
    each device's level and status, the last steps, when it last raised the light; texts in both languages). Hank
    reads it as plain data and imports nothing of the autopilot; he can still switch nothing (review 2, M4).
    The optional pump history adds today's reported runs as plain data under pump_history, without commands.
    The optional Durst-Index adds its brief under "thirst" (vgrow/thirst.py, every text in both languages)."""

    def __init__(self, box: Any, hub: AutopilotHub | None, pump_history: PumpHistory | None = None,
                 watering: WateringController | None = None, thirst: Thirst | None = None) -> None:
        self._box, self._hub = box, hub
        self._pump_history = pump_history
        self._watering = watering
        self._thirst = thirst

    def snapshot(self) -> dict[str, Any]:
        snap = dict(self._box.snapshot())
        if self._watering is not None:
            snap["watering"] = self._watering.view(snap)
        if self._thirst is not None:
            try:
                snap["thirst"] = self._thirst.brief(snap)
            except Exception:  # a broken brief never stops Hank
                _LOGGER.exception("Thirst brief for Hank failed")
        if self._pump_history is not None:
            try:
                snap["pump_history"] = self._pump_history.brief(snap)
            except Exception:
                _LOGGER.exception("Pump history for Hank failed")
        if self._hub is not None:
            try:
                snap["autopilot"] = self._hub.brief(snap)
            except Exception:  # a broken brief never stops Hank
                _LOGGER.exception("Autopilot brief for Hank failed")
        return snap


def build_hank(session: Any, *, box: Any, parts: dict[str, Any], plant_log: PlantLog,
               automation: HumidifierAutomation | None, alerts: Alerts, vision: Vision | None,
               demo: bool) -> Any:
    """Hank for serve() (spec 10.4 WP9), or None when anything about him fails: the error is logged and the cockpit
    runs without him (spec 0.3 rule 2, 10.1 rule 7). One ModelLane for the chat and the photo reading, the
    OllamaClient on serve()'s session (none in the demo: the demo runs without Ollama), and the read-only collector
    over the cockpit's objects (the camera through `parts`, where serve() keeps the current one). Hank gets no box."""
    try:
        from .hank import ollama as hank_ollama
        from .hank.core import Hank
        from .hank.wiring import Collector

        lane = hank_ollama.ModelLane()
        client = None if demo else hank_ollama.OllamaClient(session, lane=lane)
        live = Collector(box, camera=parts, plant_log=plant_log, automation=automation, alerts=alerts,
                         vision=vision)
        return Hank(live=live, client=client, lane=lane, demo=demo)
    except Exception:
        _LOGGER.exception("Hank could not be built; the cockpit runs without Hank")
        return None


def start_thirst(box: Any, **kwargs: Any) -> Thirst | None:
    """The Durst-Index listening to the box's USB reports (vgrow/thirst.py; its poll step runs in poll_handler through
    parts["thirst"]), or None when it cannot be built: the error is logged, and serve() then gives the watering
    controller the closed gate, so every scheduled slot is skipped (fail closed)."""
    try:
        thirst = Thirst(box, **kwargs)
        thirst.start(box)
        return thirst
    except Exception:
        _LOGGER.exception("The thirst index could not be started; every scheduled watering slot is skipped")
        return None


def _forget_failure(task: asyncio.Task[Any]) -> None:
    """Hank's ticker logs its own failures; reading the exception here keeps asyncio from logging it a second
    time as "never retrieved"."""
    if not task.cancelled():
        task.exception()


async def start_hank(hank: Any) -> None:
    """Start Hank's ticker as a task with a handle (hank.task). A failure is logged, the cockpit runs on."""
    if hank is None:
        return
    try:
        task = await hank.start()
        if isinstance(task, asyncio.Task):
            task.add_done_callback(_forget_failure)
    except Exception:
        _LOGGER.exception("Hank's ticker could not be started; the cockpit runs on")


async def stop_hank(hank: Any) -> None:
    if hank is None:
        return
    try:
        await hank.stop()
    except Exception:
        _LOGGER.exception("Stopping Hank failed")


async def serve(box: Any, *, port: int, host: str = "0.0.0.0") -> None:
    import aiohttp

    demo = bool(getattr(box, "demo", False))
    if demo:  # the demo never reads or writes the real run's key, logs, plant log or settings
        config.use_state_dir(config.STATE_DIR / "demo")
    # The key and the port first, then the login (review of 22.10.2026): under launchd a start that is going to fail
    # restarts every 30 s, and each of those starts used to log in to VIVOSUN before it found the port taken.
    key = config.access_key()
    sock = bind_socket(host, port)
    try:
        async with aiohttp.ClientSession() as session:
            alerts = Alerts(session)
            alerts.demo = demo

            plant_log = PlantLog()
            guard = PortGuard("box-1")  # one box today; every state path already carries its id
            automation = HumidifierAutomation(box, alerts, plant_log, guard=guard, budget=BUDGET)
            pilot = Autopilot(box, box_id="box-1", alerts=alerts, plant_log=plant_log, humidifier=automation,
                              budget=BUDGET, guard=guard)
            # VGROW_AUTOPILOT=0 locks the autopilot only. The humidifier rule keeps its own switch, and its fail-safe
            # watches a running humidifier whether that rule is on or off (review 1, M4; review 2, M1).
            hub = AutopilotHub([pilot])
            parts: dict[str, Any] = {"camera": None, "autopilot": hub}
            camera: Any = None
            extra_cameras: dict[str, Any] = {}
            vision: Vision | None = None
            pest: PestWatch | None = None
            if demo:
                camera, vision = setup_demo(plant_log, automation)
                seed_demo_autopilot(pilot)
                parts["camera"] = camera

            box.listeners.append(poll_handler(alerts, automation, parts, plant_log))
            # The measurement store's listener after the alarms: the boot poll is kept too. None in the demo.
            store, collector = measurements_mod.setup(box, demo)
            if store is not None:
                parts["light_day"] = light_view.LightDay(store)
            pump_history = PumpHistory(store, guard)
            pump_history.start(box)
            try:
                await box.start()
            except BaseException:
                await pump_history.stop()
                raise
            watering = WateringController(box, guard, BUDGET, alerts)
            light_schedule = LightSchedule(box, BUDGET)
            thirst = start_thirst(box, alerts=alerts, plant_log=plant_log, guard=guard, demo=demo, watering=watering,
                                  light=light_schedule)
            if thirst is not None:
                parts["thirst"] = thirst  # sampled on every poll by poll_handler: no listener of its own
            if demo and thirst is not None:
                await seed_demo_thirst(thirst, box, watering, guard)
            # The need check before every automatic slot, set before the watchdog runs: without a Durst-Index every
            # scheduled slot is skipped. Einmal gießen stays the owner's own and is never asked.
            watering.need_gate = thirst.gate if thirst is not None else thirst_mod.closed_gate
            watering.launch()
            pilot.light_schedule = light_schedule
            light_schedule.launch()
            if collector is not None:
                try:
                    collector.start()  # the backfill first, then one point-log request every 15 minutes
                except Exception:
                    _LOGGER.exception("The measurement collector could not be started; the cockpit runs on")
                    collector = None
            if not demo and getattr(box, "camera_device", None) is not None:
                camera = Camera(config.RECORDINGS_DIR)
                await camera.configure(box.camera_device, config.growcam_ip(), lan_ip())
                camera.search_task = asyncio.create_task(camera.keep_searching(), name="growcam_search")
                camera.watch_task = asyncio.create_task(camera.watch(), name="growcam_watch")
                parts["camera"] = camera  # from now on every poll checks the camera too
                vision = Vision(camera)
                vision.task = asyncio.create_task(  # type: ignore[attr-defined]
                    daily_photo(box, camera, vision, plant_log, alerts), name="daily_photo")
            if not demo:
                from .usb_camera import configured_camera
                usb_camera = configured_camera(config.STATE_DIR, config.RECORDINGS_DIR)
                if usb_camera is not None:
                    usb_camera.start()
                    extra_cameras['usb'] = usb_camera
                    parts["usb_camera"] = usb_camera  # from now on every poll checks the close camera too
                    if vision is None:
                        vision = Vision(usb_camera)
            if vision is not None and not demo:
                # The close camera first: aphids and mildew need the detail; the GrowCam when there is none.
                pest = PestWatch(vision, detail=extra_cameras.get("usb"), overview=camera)
                pest.task = asyncio.create_task(pest_loop(box, pest, alerts), name="pest_watch")  # type: ignore[attr-defined]
            # Hank last, as a guest: nothing above depends on him, and poll_handler never sees him.
            hank = build_hank(session, box=HankBox(box, hub, pump_history, watering, thirst), parts=parts,
                              plant_log=plant_log, automation=automation, alerts=alerts, vision=vision, demo=demo)
            if hank is not None and vision is not None:
                vision.lane = hank.lane  # the photo reading waits behind a running chat answer (spec 4.9)
            soil = start_soil(session, demo)  # a guest too: read only, nothing above depends on it
            if soil is not None:
                def stage_key() -> str | None:
                    return (stage_now(plant_log) or {}).get("key")

                soil.stage_of = stage_key
                if thirst is not None:
                    soil.on_reading = thirst.sensor_check  # a calibrated reading is a check of the Durst-Index
            fan_stick = start_fan_stick(box, demo)
            parts["fan_stick"] = fan_stick
            # The plan last: it reads all of the above and only the owner's taps and the light of the day go through it.
            grow_plan = start_grow_plan(box, plant_log, demo, alerts=alerts, guard=guard, timer=light_schedule, watering=watering,
                                        thirst=thirst, soil=soil, measurements=store, camera_of=lambda: parts.get("camera"),
                                        link=page_link, tank=automation.tank)
            parts["grow_plan"] = grow_plan
            runner = web.AppRunner(make_app(box, alerts, key, camera, plant_log, vision, automation, hank,
                                            measurements=store, autopilot=hub, pump_history=pump_history, watering=watering,
                                            extra_cameras=extra_cameras, light_schedule=light_schedule, thirst=thirst,
                                            pest=pest, soil=soil, fan_stick=fan_stick, grow_plan=grow_plan))
            await runner.setup()
            await web.SockSite(runner, sock).start()
            await start_hank(hank)
            news_watcher = start_news(demo)  # a guest too, and never in the demo
            if demo:
                cam_line = t("console.demo_camera")
            elif camera is None:
                cam_line = t("console.no_camera")
            else:
                cam_line = (t("console.camera_at", name=camera.name, ip=camera.ip) if camera.configured
                            else t("console.camera_off", error=camera.error))
            print(startup_banner(key, sock.getsockname()[1], lan=lan_ip(), camera=cam_line, tty=interactive(),
                                 ntfy=t("console.ntfy_on") if alerts.target else t("console.ntfy_off")), flush=True)
            try:
                await asyncio.Event().wait()
            finally:
                await light_schedule.close()
                await watering.close()
                if thirst is not None:
                    thirst.stop()
                await stop_news(news_watcher)
                if soil is not None:
                    await soil.stop()
                await fan_stick.stop()
                if grow_plan is not None:
                    await grow_plan.stop()
                if collector is not None:
                    await collector.stop()
                await stop_hank(hank)
                if camera:
                    await camera.close()  # finishes a running recording cleanly
                for extra_camera in extra_cameras.values():
                    await extra_camera.close()
                await runner.cleanup()
                await box.stop()
                await pump_history.stop()
                hub.release()  # a clean stop frees the box for the next server at once
    finally:
        sock.close()  # already closed with the site; never left open when the start fails before it
