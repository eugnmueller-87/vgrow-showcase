"""A daily, user-configured light timer, independent of sensor polls and the brightness autopilot.

Disabled until configured. Reconcile the current period after reconnect/restart; never replay old switches.
Only manual/no-plan hardware is controlled. A manual change yields until the next scheduled boundary.
The owner's Off in the cockpit stops the timer instead (owner, 01.11.2026): no scheduled ON brings the light back
until Automatic or the saved form switches it on again, and that switch-on applies the current period at once.
The host and cloud connection must stay available; this does not install a timer inside the light.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import config
from .box import CommandRefused
from .budget import BUDGET, CommandBudget
from .vivosun.const import LIGHT_MIN_BRIGHTNESS

LOG = logging.getLogger(__name__)
ZONE = ZoneInfo("Europe/Berlin")
DEFAULTS = {"enabled": False, "on_time": None, "off_time": None, "level": None, "timezone": "Europe/Berlin",
            "from_requirements": False}
CONFIRM_SECONDS = 30
# Ready-made daily cycles. A preset only fills the form (the off time from the on time); saving stays the owner's tap.
# 24/0 has no switch to time, so it is not a preset: timer off, light on by hand. GWE fetched 27.10.2026,
# S6 is the autoflower card's source (kartei/stages/autoflower.json, 22.10.2026).
GWE = {"name": "growweedeasy.com", "url": "https://www.growweedeasy.com/light-schedules"}
S6 = {"name": "VIVOSUN", "url": "https://vivosun.com/growing_guide/photoperiod-vs-autoflower-grow-lights-key-differences/"}
PRESETS = (
    {"key": "18-6", "hours": 18, "autoflower": True, "sources": [GWE, S6]},  # veg (GWE); autos to harvest (S6)
    {"key": "20-4", "hours": 20, "autoflower": True, "sources": [S6]},  # autos to harvest (S6, kartei/stages/autoflower)
    {"key": "12-12", "hours": 12, "autoflower": False, "sources": [GWE, S6]},  # flowering for photoperiods; autos: S6
)
TEXTS = {
    "disabled": ("Lichttimer aus", "Light timer disabled"),
    "scheduled": ("Lichttimer aktiv", "Light timer active"),
    "manual": ("Deine Änderung gilt bis zum nächsten Schalttermin.", "Your manual change holds until the next scheduled switch."),
    "connection": ("Wartet auf eine frische Geräteverbindung.", "Waiting for a fresh device connection."),
    "mode": ("Licht muss im manuellen Modus ohne VIVOSUN-Plan sein.", "The light must be in manual mode without a VIVOSUN plan."),
    "storage": ("Lichttimer konnte nicht sicher gespeichert werden.", "The light timer could not be saved safely."),
    "unconfirmed": ("Lichtwechsel nicht bestätigt. Bitte Gerät prüfen.", "Light switch not confirmed. Check the device."),
    "clock": ("Wartet nach einer zurückgestellten Uhr auf den nächsten Schalttermin.", "Waiting for the next switch after the clock moved backward."),
    "pending": ("Wartet auf Bestätigung des Lichtwechsels.", "Waiting for confirmation of the light switch."),
    "requirements": ("Wartet auf bestätigte Pflanzenphase und belegte Lichtanforderungen.",
                     "Waiting for a confirmed plant stage and sourced light requirements."),
    "changed": ("Der vorgeschlagene Lichtplan hat sich geändert. Bitte neu laden und prüfen.",
                "The proposed light plan changed. Refresh and review it before applying."),
    "stopped": ("Aus, weil du das Licht ausgeschaltet hast. Automatisch startet den Plan wieder.",
                "Off because you switched the light off. Automatic starts the plan again."),
}


def settings(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("settings: object")
    out = {k: body.get(k, v) for k, v in DEFAULTS.items()}
    if (type(out["enabled"]) is not bool or type(out["from_requirements"]) is not bool
            or out["timezone"] != "Europe/Berlin"):
        raise ValueError("enabled / timezone")
    for key in ("on_time", "off_time"):
        if out[key] is not None and (not isinstance(out[key], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", out[key])):
            raise ValueError(key + ": HH:MM")
    if out["level"] is not None and (type(out["level"]) is not int or not LIGHT_MIN_BRIGHTNESS <= out["level"] <= 100):
        raise ValueError(f"level: {LIGHT_MIN_BRIGHTNESS}..100")
    if out["enabled"] and (not out["on_time"] or not out["off_time"] or not out["level"] or out["on_time"] == out["off_time"]):
        raise ValueError("Distinct on/off times and an on brightness are required")
    return out


def boundaries(cfg: dict[str, Any], now: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """Actual local-time edges: first occurrence at fall-back, nonexistent spring-forward edges skipped."""
    today = datetime.fromtimestamp(now, ZONE).date()
    edges = []
    for offset in range(-2, 3):
        day = today + timedelta(days=offset)
        for key, on in (("on_time", True), ("off_time", False)):
            local = datetime.fromisoformat(f"{day.isoformat()}T{cfg[key]}").replace(tzinfo=ZONE, fold=0)
            at = local.timestamp()
            if datetime.fromtimestamp(at, ZONE).replace(tzinfo=None) == local.replace(tzinfo=None):
                edges.append({"at": at, "on": on, "level": cfg["level"] if on else 0})
    edges.sort(key=lambda e: e["at"])
    return max((e for e in edges if e["at"] <= now), key=lambda e: e["at"]), next(e for e in edges if e["at"] > now)


def stage_hours(profile: dict[str, Any] | None, stage: dict[str, Any] | None) -> list[int] | None:
    """The hours of light this stage's card allows: its cycle and the allowed alternatives, read as automatic_plan
    reads them. None without a stage or a cycle: then nobody can say what the day should have been."""
    if not profile or not stage:
        return None
    cycle = stage.get("light_schedule", profile.get("light_schedule"))
    if not isinstance(cycle, dict) or type(cycle.get("hours_on")) is not int:
        return None
    others = cycle.get("alternative_hours_on")
    return [cycle["hours_on"], *(h for h in (others if isinstance(others, list) else []) if type(h) is int)]


def automatic_plan(profile: dict[str, Any] | None, stage: dict[str, Any] | None,
                   cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Use explicit, sourced card requirements. Never infer a plant type from its name or an AI guess.

    A stage can override the profile's cycle (e.g. a photoperiod flower stage). Preserve the owner's start time
    and an allowed alternative cycle; 06:00 is a visible form default only when no start time exists.
    """
    if not profile or not stage or stage.get("set_by") != "manual":
        return None
    cycle = stage.get("light_schedule", profile.get("light_schedule"))
    level = (stage.get("preset") or {}).get("light_percent")
    if not isinstance(cycle, dict) or type(level) is not int or not LIGHT_MIN_BRIGHTNESS <= level <= 100:
        return None
    hours = cycle.get("hours_on")
    alternatives = cycle.get("alternative_hours_on", [])
    if (type(hours) is not int or not 0 < hours < 24 or not isinstance(alternatives, list)
            or any(type(h) is not int or not 0 < h < 24 for h in alternatives)):
        return None
    cycle_sources, preset_sources = cycle.get("sources"), stage.get("preset_sources")
    sources = profile.get("sources") or {}
    if (not isinstance(cycle_sources, list) or not cycle_sources or not isinstance(preset_sources, list)
            or not preset_sources or not isinstance(sources, dict)):
        return None
    ids = [*cycle_sources, *preset_sources]
    if any(not isinstance(i, str) or not isinstance(sources.get(i), str) for i in ids):
        return None
    on = cfg.get("on_time") or "06:00"
    start = int(on[:2]) * 60 + int(on[3:])
    if cfg.get("on_time") and cfg.get("off_time"):
        end = int(cfg["off_time"][:2]) * 60 + int(cfg["off_time"][3:])
        saved_hours = ((end - start) % 1440) / 60
        if saved_hours in [hours, *alternatives]:
            hours = int(saved_hours)
    end = (start + hours * 60) % 1440
    candidate = settings({"enabled": True, "on_time": on, "off_time": f"{end // 60:02d}:{end % 60:02d}",
                          "level": level, "from_requirements": True})
    refs = []
    for ident in dict.fromkeys(ids):
        match = re.search(r"https://[^\s)]+", sources[ident])
        refs.append({"id": ident, "text": sources[ident], "url": match[0] if match else None})
    plan = {"settings": candidate, "stage": {k: stage.get(k) for k in ("key", "label", "label_en", "since")},
            "plant": (profile.get("plant") or {}).get("strain"), "hours_on": hours, "sources": refs,
            "default_start": not bool(cfg.get("on_time"))}
    plan["fingerprint"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan


class LightSchedule:
    def __init__(self, box: Any, budget: CommandBudget | None = None, clock: Callable[[], float] = time.time) -> None:
        self.box, self.budget, self.clock = box, budget or BUDGET, clock
        self.path = config.STATE_DIR / "boxes" / "box-1" / "light-schedule.json"
        self.cfg = dict(DEFAULTS)
        self.completed: float | None = None
        self.expected: int | None = None
        self.override_until: float | None = None
        self.pending: dict[str, Any] | None = None
        self.failed: float | None = None
        self.error: str | None = None
        self.retry_at = 0.0
        self.reconcile = True
        self.events: list[dict[str, Any]] = []
        self.task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()
        self.requirements_source: Callable[[], tuple[dict[str, Any] | None, dict[str, Any] | None]] | None = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.cfg = settings(data["settings"])
            for key in ("completed", "override_until", "failed"):
                value = data.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                    raise ValueError(key)
                setattr(self, key, value)
            self.expected = data.get("expected")
            if self.expected is not None and (type(self.expected) is not int or not 0 <= self.expected <= 100):
                raise ValueError("expected")
            rows = data.get("events", [])
            if not isinstance(rows, list) or any(not isinstance(e, dict) for e in rows):
                raise ValueError("events")
            self.events = rows[-100:]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            self.cfg, self.error = dict(DEFAULTS), "storage"
            LOG.error("Unreadable light schedule; automatic light switches disabled")

    def _save(self) -> None:
        try:
            config.atomic_write(self.path, json.dumps({"settings": self.cfg, "completed": self.completed,
                "expected": self.expected, "override_until": self.override_until, "failed": self.failed,
                "events": self.events[-100:]}) + "\n")
        except OSError as err:
            self.error = "storage"
            raise CommandRefused(TEXTS["storage"][1]) from err

    def _event(self, kind: str, **values: Any) -> None:
        self.events = [*self.events[-99:], {"at": self.clock(), "kind": kind, **values}]

    def blocked(self, snap: dict[str, Any]) -> str | None:
        if self.error == "storage":
            return "storage"
        age = snap.get("shadow_age")
        if snap.get("online") is not True or snap.get("mqtt") is not True or type(age) not in (int, float) or not 0 <= age <= 300:
            return "connection"
        light = snap.get("light") or {}
        if (light.get("mode") != 0 or light.get("in_plan") is not False or light.get("in_plan_known") is False
                or type(light.get("level")) is not int or not 0 <= light["level"] <= 100):
            return "mode"
        return None

    async def configure(self, body: dict[str, Any]) -> None:
        candidate = settings(body)
        if candidate["from_requirements"] and candidate["enabled"]:
            raise ValueError("Use the Automatic action to load plant requirements")
        candidate["from_requirements"] = False
        async with self._lock:
            reason = self.blocked(self.box.snapshot())
            if candidate["enabled"] and reason not in (None, "storage"):
                raise CommandRefused(TEXTS[reason][1])
            previous = (self.cfg, self.completed, self.expected, self.override_until, self.pending, self.failed, self.events)
            self.cfg = candidate
            self.completed = self.expected = self.override_until = self.pending = self.failed = None
            self._event("configured", enabled=candidate["enabled"])
            try:
                self._save()  # durable settings before the watcher can send any command
            except CommandRefused:
                (self.cfg, self.completed, self.expected, self.override_until, self.pending, self.failed, self.events) = previous
                raise
            self.retry_at, self.error = 0, None

    def automatic(self, snap: dict[str, Any]) -> dict[str, Any]:
        plan = None
        if self.requirements_source is not None:
            try:
                profile, stage = self.requirements_source()
                plan = automatic_plan(profile, stage, self.cfg)
            except (OSError, ValueError, KeyError, TypeError):
                LOG.exception("Light requirements unavailable")
        reason = "requirements" if plan is None else self.blocked(snap)
        return {"available": reason is None, "reason": reason, "plan": plan}

    def _apply_automatic(self, plan: dict[str, Any], kind: str) -> None:
        previous = (self.cfg, self.completed, self.expected, self.override_until, self.pending, self.failed, self.events)
        self.cfg = dict(plan["settings"])
        self.completed = self.expected = self.override_until = self.pending = self.failed = None
        self._event(kind, stage=plan["stage"]["key"], hours_on=plan["hours_on"], level=self.cfg["level"])
        try:
            self._save()  # persist ownership before any hardware write
        except CommandRefused:
            (self.cfg, self.completed, self.expected, self.override_until, self.pending, self.failed, self.events) = previous
            raise
        self.reconcile, self.retry_at, self.error = True, 0, None

    async def resume_automatic(self, fingerprint: str | None) -> None:
        from .i18n import current_lang
        language = 0 if current_lang() == "de" else 1
        async with self._lock:
            automatic = self.automatic(self.box.snapshot())
            if not automatic["available"]:
                raise CommandRefused(TEXTS[automatic["reason"]][language])
            plan = automatic["plan"]
            if not fingerprint or fingerprint != plan["fingerprint"]:
                raise CommandRefused(TEXTS["changed"][language])
            self._apply_automatic(plan, "automatic")
        await self.tick()  # reconcile now, including OFF during the dark period

    def manual(self) -> None:
        if not self.cfg["enabled"]:
            return
        _, upcoming = boundaries(self.cfg, self.clock())
        self.override_until, self.pending = upcoming["at"], None
        self._event("manual", until=self.override_until)
        self._save()

    def stop(self) -> None:
        """The owner switched the light off: the timer stays off, its times kept for switching it on again.

        An off is never refused: if the file cannot be written, this run stays stopped and the page says "storage".
        """
        if not self.cfg["enabled"]:
            return
        self.cfg = {**self.cfg, "enabled": False}
        self.completed = self.expected = self.override_until = self.pending = self.failed = None
        self._event("stopped")
        try:
            self._save()
        except CommandRefused:
            LOG.error("Light timer stop not saved; a restart would resume the timer")

    async def tick(self) -> None:
        async with self._lock:
            if not self.cfg["enabled"]:
                return
            now = self.clock()
            if self.override_until and now < self.override_until:
                return
            if self.cfg["from_requirements"]:
                automatic = self.automatic(self.box.snapshot())
                if not automatic["available"]:
                    self.reconcile = True
                    # Missing requirements never invent a new ON. The saved dark period still switches OFF.
                    if automatic["reason"] != "requirements" or boundaries(self.cfg, now)[0]["on"]:
                        return
                elif automatic["plan"]["settings"] != self.cfg:
                    self._apply_automatic(automatic["plan"], "requirements_updated")
            edge, _ = boundaries(self.cfg, now)
            if self.completed is not None and edge["at"] < self.completed:
                return
            if self.blocked(self.box.snapshot()):
                self.reconcile = True
                return
            if self.reconcile:
                self.completed, self.pending, self.reconcile = None, None, False
            level = self.box.snapshot()["light"]["level"]
            if self.failed == edge["at"]:
                return
            attempts = 0
            if self.pending and self.pending["boundary"] != edge["at"]:
                self.pending = None  # a stale ON is never retried in the OFF period
            if self.pending:
                if level == self.pending["target"]:
                    self.pending = None
                elif now - self.pending["at"] < CONFIRM_SECONDS:
                    return
                elif self.pending["attempts"] >= 2:
                    self.failed, self.pending, self.error = edge["at"], None, "unconfirmed"
                    self._event("unconfirmed", level=edge["level"])
                    self._save()
                    return
                else:
                    attempts = self.pending["attempts"]
            if self.completed == edge["at"]:
                if level != self.expected:
                    self.manual()
                return
            if level == edge["level"]:
                self.completed, self.expected, self.pending, self.error = edge["at"], level, None, None
                self.override_until = None
                self._event("confirmed", level=level)
                self._save()
                return
            if now < self.retry_at:
                return
            self.pending = {"boundary": edge["at"], "target": edge["level"], "at": now, "attempts": attempts + 1}
            self._event("requested", level=edge["level"])
            self._save()
            try:
                await self.budget.send("light_schedule", "light.level", lambda: asyncio.wait_for(
                    self.box.set_light(edge["level"]), 5), safety=edge["level"] == 0)
            except CommandRefused:
                self.pending = None
                self.retry_at = now + CONFIRM_SECONDS
            except Exception:
                LOG.exception("Light switch not confirmed; waiting before checking/retrying")

    def view(self, snap: dict[str, Any], lang: str | None = None) -> dict[str, Any]:
        from .i18n import current_lang
        now = self.clock()
        automatic = self.automatic(snap)
        edge = upcoming = None
        status = "disabled"
        if self.cfg["enabled"]:
            edge, upcoming = boundaries(self.cfg, now)
            status = ("manual" if self.override_until and now < self.override_until else self.blocked(snap)
                      or ("requirements" if self.cfg["from_requirements"] and not automatic["available"] else None)
                      or ("unconfirmed" if self.failed == edge["at"] else "clock" if self.completed and edge["at"] < self.completed
                          else "pending" if self.pending else "scheduled"))
        elif self.error:
            status = self.error
        elif self.events and self.events[-1].get("kind") == "stopped":
            status = "stopped"
        hours = None
        if self.cfg["on_time"] and self.cfg["off_time"] and self.cfg["on_time"] != self.cfg["off_time"]:
            minutes = lambda text: int(text[:2]) * 60 + int(text[3:])  # noqa: E731
            hours = round((minutes(self.cfg["off_time"]) - minutes(self.cfg["on_time"])) % 1440 / 60, 2)
        return {"settings": dict(self.cfg), "status": status, "message": TEXTS[status][0 if (lang or current_lang()) == "de" else 1],
                "next_switch": upcoming, "expected_level": edge["level"] if edge else None,
                "hours_on": hours, "override_until": self.override_until if status == "manual" else None,
                "reported_level": (snap.get("light") or {}).get("level"), "events": self.events[-20:],
                "presets": [dict(p, sources=[dict(s) for s in p["sources"]]) for p in PRESETS],
                "automatic": automatic}

    def launch(self) -> None:
        async def watch() -> None:
            while True:
                try:
                    await self.tick()
                except Exception:
                    LOG.exception("Light timer check failed")
                await asyncio.sleep(1)
        if self.task is None:
            self.task = asyncio.create_task(watch(), name="light_schedule")

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
