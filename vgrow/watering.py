"""Bounded USB1 watering cycles. A one-second watchdog owns the stop, independently of sensor polling/Hank.

Settings start disabled and contain no guessed dose. Persist an intent before publishing ON; an interrupted
intent is stopped on restart, never resumed. Only a new USB report confirms OFF (USB power is not water flow).
Local-time slots are attempted once, without catching up missed watering after downtime or a clock change.
The computer and cloud connection must remain available: this is not a timer inside the pump.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import config, i18n
from .box import CommandRefused, check_port_lock, watering_allowed
from .budget import BUDGET, BudgetExceeded, CommandBudget
from .portguard import PortGuard, configured_device

LOG = logging.getLogger(__name__)
MAX_SECONDS = 3600  # technical ceiling, not a watering recommendation
GRACE_SECONDS = 60
CONFIRM_SECONDS = 30
PUBLISH_SECONDS = 5
RETRY_SECONDS = 30
DEFAULTS: dict[str, Any] = {"enabled": False, "duration_s": None, "times": [], "every_days": 1,
                            "first_date": None, "timezone": "Europe/Berlin", "power_confirmed": False}
TEXTS = {
    "setup": ("Zuerst Laufzeit und Pumpenschalter bestätigen.", "Set the run time and confirm the pump power switch first."),
    "schedule": ("Gültige Uhrzeiten und ein Startdatum fehlen.", "Valid watering times and a first date are required."),
    "role": ("USB 1 muss als Pumpe bestätigt sein.", "USB 1 must be confirmed as the pump."),
    "assignment": ("Die USB-Zuordnung ist unbestätigt oder verändert.", "The USB assignment is unconfirmed or has changed."),
    "connection": ("Die Box ist nicht frisch verbunden.", "The box does not have a fresh connection."),
    "supply": ("Frische Messwerte mit Wasser am Sensor fehlen.", "Fresh readings showing water at the sensor are required."),
    "port": ("USB 1 muss im manuellen Modus ohne Geräteplan sein.", "USB 1 must be in manual mode without a device plan."),
    "busy": ("Die Pumpe ist bereits an oder ein Stopp ist noch unbestätigt.", "The pump is already on or its stop is still unconfirmed."),
    "storage": ("Bewässerungsdaten konnten nicht sicher gespeichert werden.", "Watering data could not be saved safely."),
    "budget": ("Das gemeinsame Befehlslimit ist erreicht; dieser Termin entfällt.", "The shared command limit is reached; this slot is skipped."),
    "publish": ("Pumpenstart unbestätigt; der Stopp wird gesendet.", "Pump start unconfirmed; sending a stop."),
    "duration": ("Die eingestellte Laufzeit ist abgelaufen.", "The configured run time has elapsed."),
    "manual": ("Stopp von dir angefordert.", "Stop requested by you."),
    "disabled": ("Zeitplan ausgeschaltet.", "Schedule disabled."),
    "restart": ("Unterbrochener Lauf nach Neustart wird gestoppt.", "Stopping an interrupted run after restart."),
    "shutdown": ("Lauf beim Beenden des Controllers stoppen.", "Stopping the cycle as the controller shuts down."),
    "unconfirmed": ("Keine rechtzeitige Einschaltbestätigung; Stopp wird gesendet.", "No timely power-on confirmation; sending a stop."),
    "missed": ("Verpasster Termin entfällt; es wird nicht nachgegossen.", "Missed slot skipped; no catch-up watering."),
}
for _code, (_de, _en) in TEXTS.items():
    i18n.DE["watering.reason." + _code] = _de
    i18n.EN["watering.reason." + _code] = _en


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def settings(body: dict[str, Any]) -> dict[str, Any]:
    """Strict JSON validation; no truthy strings, zero durations, duplicate times or silent coercion."""
    if not isinstance(body, dict):
        raise ValueError("settings: object")
    out = {key: body.get(key, value) for key, value in DEFAULTS.items()}
    if type(out["enabled"]) is not bool or type(out["power_confirmed"]) is not bool:
        raise ValueError("enabled / power_confirmed")
    duration = out["duration_s"]
    if duration is not None and (type(duration) is not int or not 1 <= duration <= MAX_SECONDS):
        raise ValueError("duration_s: 1..3600")
    if type(out["every_days"]) is not int or not 1 <= out["every_days"] <= 30:
        raise ValueError("every_days: 1..30")
    times = out["times"]
    if (not isinstance(times, list) or len(times) > 12
            or any(not isinstance(t, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in times)
            or len(set(times)) != len(times)):
        raise ValueError("times: HH:MM")
    out["times"] = sorted(times)
    if out["timezone"] != "Europe/Berlin":
        raise ValueError("timezone: Europe/Berlin")
    if out["first_date"] is not None:
        first = date.fromisoformat(str(out["first_date"]))
        if first.isoformat() != out["first_date"] or not 1970 <= first.year <= 2100:
            raise ValueError("first_date: YYYY-MM-DD")
    if out["enabled"] and (not duration or not out["power_confirmed"] or not times or not out["first_date"]):
        raise ValueError(i18n.t("watering.reason.schedule"))
    return out


class WateringController:
    port = "usb1"

    def __init__(self, box: Any, guard: PortGuard | None = None, budget: CommandBudget | None = None,
                 alerts: Any = None, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.box, self.guard, self.budget, self.alerts = box, guard or PortGuard(), budget or BUDGET, alerts
        self.clock, self.monotonic = clock, monotonic
        self.zone = ZoneInfo("Europe/Berlin")
        self.path = config.STATE_DIR / "boxes" / "box-1" / "watering.json"
        self.cfg = dict(DEFAULTS)
        self.active: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.error: str | None = None
        self.cursor = clock()
        self._lock = asyncio.Lock()
        self.task: asyncio.Task[Any] | None = None
        self._deadline = 0.0
        self._next_off = 0.0
        self._off_attempts = 0
        self._seq = 0
        self._report: bool | None = None
        self._barrier = 0
        self._on_seen = False
        self._off_sent = False
        self._notified = False
        self._stop_since: float | None = None
        # Hook: the need check (vgrow/thirst.py) asked before every automatic slot, need_gate(slot_at, duration_s) ->
        # {"run": bool, "max_s": int | None, "text": {"de", "en"}}. It can only skip a slot or shorten its run time;
        # an error or an unusable answer skips the slot. None (the default) keeps the schedule as it is.
        self.need_gate: Callable[[float, int], dict[str, Any]] | None = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.cfg = settings(data["settings"])
            if not finite(data["cursor"]) or not isinstance(data["events"], list):
                raise ValueError("record")
            if any(not isinstance(e, dict) or not finite(e.get("at")) or not 0 <= e["at"] <= 4_133_980_800
                   or e.get("kind") not in ("start", "stopped", "skipped", "due") for e in data["events"]):
                raise ValueError("events")
            self.cursor = max(self.cursor, data["cursor"])
            self.events = [e for e in data["events"] if isinstance(e, dict)][-100:]
            active = data.get("active")
            if active is not None:
                if not isinstance(active, dict) or not finite(active.get("started_at")):
                    raise ValueError("active")
                self.active = {**active, "phase": "stopping", "reason": "restart"}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            self.cfg = dict(DEFAULTS)
            self.error = "storage"
            LOG.error("Unreadable watering record; automatic starts disabled")

    def _save(self) -> None:
        try:
            config.atomic_write(self.path, json.dumps({"settings": self.cfg, "active": self.active,
                                                       "events": self.events[-100:], "cursor": self.cursor}) + "\n")
        except OSError as err:
            self.error = "storage"
            raise CommandRefused(i18n.t("watering.reason.storage")) from err

    def _event(self, kind: str, reason: str | None = None) -> None:
        self.events = [*self.events[-99:], {"at": self.clock(), "kind": kind, "reason": reason}]

    def _identity(self, snap: dict[str, Any]) -> str | None:
        if configured_device(self.port) != "pump" or self.guard.owner_device(self.port) != "pump":
            return "role"
        if self.guard.status(snap) != "ok":
            return "assignment"
        return None

    def blocked(self, snap: dict[str, Any]) -> str | None:
        if self.error == "storage":
            return "storage"
        if not self.cfg["duration_s"] or not self.cfg["power_confirmed"]:
            return "setup"
        identity = self._identity(snap)
        if identity:
            return identity
        if (snap.get("online") is not True or snap.get("mqtt") is not True
                or not finite(snap.get("shadow_age")) or not 0 <= snap["shadow_age"] <= 300):
            return "connection"
        if not finite(snap.get("sensors_age")) or not 0 <= snap["sensors_age"] <= 300 or not watering_allowed(snap):
            return "supply"
        port = (snap.get("ports") or {}).get(self.port) or {}
        if port.get("can_write") is not True:
            return "port"
        try:
            check_port_lock(self.port, False)  # never start a cycle that cannot be switched off
        except CommandRefused:
            return "port"
        if self.active or port.get("on") is not False or (snap.get("pending") or {}).get("port.usb1", {}).get("status") == "pending":
            return "busy"
        return None

    async def configure(self, body: dict[str, Any]) -> None:
        candidate = settings(body)
        async with self._lock:
            previous, self.cfg = self.cfg, candidate
            # Enable only on a usable, idle pump. Disabling always remains possible, even when disconnected.
            reason = self.blocked(self.box.snapshot()) if candidate["enabled"] and not previous["enabled"] else None
            if reason:
                self.cfg = previous
                raise CommandRefused(i18n.t("watering.reason." + reason))
            self.cursor = max(self.cursor, self.clock())
            try:
                self._save()
            except CommandRefused:
                self.cfg = previous
                raise
            self.error = None
            if not candidate["enabled"] and self.active:
                await self._stop("disabled")

    def observe(self, box: Any, ports: dict[str, Any], at: Any, source: str = "shadow") -> None:
        if self.port in ports:
            self._seq += 1
            self._report = ports[self.port] if type(ports[self.port]) is bool else None

    def launch(self) -> None:
        if self.task is not None:
            return
        if hasattr(self.box, "port_listeners") and self.observe not in self.box.port_listeners:
            self.box.port_listeners.append(self.observe)
        self.task = asyncio.create_task(self._watch(), name="watering_watchdog")

    async def _watch(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                LOG.exception("Watering watchdog failed; retaining the stop intent")
            await asyncio.sleep(1)

    async def start(self, *, automatic: bool = False) -> None:
        async with self._lock:
            await self._start(automatic)

    def _need(self, slot_at: float) -> tuple[str | None, int | None, dict[str, str] | None]:
        """The need gate's word on an automatic slot: (skip reason or None, a shorter run time or None, its text)."""
        if self.need_gate is None:
            return None, None, None
        try:
            verdict = self.need_gate(slot_at, self.cfg["duration_s"])
            text = verdict.get("text")
            detail = {k: str(v) for k, v in text.items() if k in ("de", "en")} if isinstance(text, dict) else None
            limit = verdict.get("max_s")
            if verdict.get("run") is not True or (limit is not None and (type(limit) is not int or limit < 1)):
                return "thirst", None, detail
        except Exception:
            LOG.exception("Need check failed; the slot is skipped")
            return "thirst", None, None
        return None, limit if limit is not None and limit < self.cfg["duration_s"] else None, detail

    async def _start(self, automatic: bool, limit_s: int | None = None, detail: dict[str, str] | None = None) -> None:
        reason = self.blocked(self.box.snapshot())
        if reason:
            raise CommandRefused(i18n.t("watering.reason." + reason))
        origin = "watering" if automatic else "manual"
        self.budget.check(origin)
        self._barrier, self._on_seen, self._off_sent, self._notified = self._seq, False, False, False
        self._stop_since = None
        self._next_off = 0
        self._off_attempts = 0
        now = self.clock()
        # the need gate may shorten an automatic run, never lengthen it
        duration = self.cfg["duration_s"] if limit_s is None else max(1, min(self.cfg["duration_s"], limit_s))
        self._deadline = self.monotonic() + duration
        self.active = {"started_at": now, "ends_at": now + duration, "duration_s": duration,
                       "phase": "starting", "reason": None, "automatic": automatic}
        self._event("start")
        if detail:
            self.events[-1]["detail"] = detail
        try:
            self._save()  # intent durable BEFORE ON; a failed save must never energize a pump
        except CommandRefused:
            self.active = None
            raise
        try:
            await self.budget.send(origin, "port.usb1", lambda: asyncio.wait_for(
                self.box.set_port(self.port, True), min(PUBLISH_SECONDS, duration)))
        except (CommandRefused, BudgetExceeded):
            self.active = None  # definitely refused before publishing
            self._event("skipped", "publish")
            self._save()
            raise
        except Exception as err:
            await self._stop("publish")  # possibly published: never retry ON
            raise CommandRefused(i18n.t("watering.reason.publish")) from err

    async def stop(self) -> None:
        async with self._lock:
            if not self.active:  # explicit owner stop may also stop a pre-existing manual ON
                self.active = {"started_at": self.clock(), "phase": "stopping", "reason": "manual", "manual_off": True}
            await self._stop("manual", force=True)

    async def _stop(self, reason: str, *, force: bool = False) -> None:
        if not self.active:
            return
        if self._stop_since is None:
            self._stop_since = self.monotonic()
        self.active.update(phase="stopping", reason=reason)
        try:
            self._save()
        except CommandRefused:
            pass  # storage failure must never prevent OFF
        if not force and self.monotonic() < self._next_off:
            return
        retry = min(300, RETRY_SECONDS * 2 ** min(self._off_attempts, 4))
        self._next_off = self.monotonic() + retry
        self._off_attempts += 1
        snap = self.box.snapshot()
        try:
            if not self.active.get("manual_off") and self._identity(snap):
                raise CommandRefused(i18n.t("watering.reason.assignment"))
            self._barrier = self._seq
            await self.budget.send("watering", "port.usb1", lambda: asyncio.wait_for(
                self.box.set_port(self.port, False), PUBLISH_SECONDS), safety=True)
            self._off_sent = True
        except Exception:
            LOG.warning("Watering stop not sent or unconfirmed; retrying in %s seconds", retry)
        if self._confirmed(False):
            self._finish()
        elif not self._notified and self.alerts is not None and self.monotonic() - self._stop_since >= 15:
            self._notified = True
            # Do not let notification network time delay the pump watchdog.
            asyncio.create_task(self.alerts.push(i18n.t("watering.push.title"), i18n.t("watering.push.stop"), priority=4))

    def _confirmed(self, on: bool) -> bool:
        snap = self.box.snapshot()
        if snap.get("online") is not True or snap.get("mqtt") is not True:
            return False
        if hasattr(self.box, "port_listeners"):
            return self._seq > self._barrier and self._report is on
        return bool(getattr(self.box, "demo", False)) and (snap.get("ports", {}).get(self.port) or {}).get("on") is on

    def _finish(self) -> None:
        self._event("stopped", self.active.get("reason") if self.active else None)
        self.active = None
        self._next_off = 0
        try:
            self._save()
        except CommandRefused:
            pass  # the durable unfinished intent will request OFF again after restart

    def slots(self, after: float, until: float) -> list[tuple[float, str]]:
        if not self.cfg["times"] or not self.cfg["first_date"]:
            return []
        first = date.fromisoformat(self.cfg["first_date"])
        day = max(first, datetime.fromtimestamp(after, self.zone).date())
        end = datetime.fromtimestamp(until, self.zone).date()
        out = []
        while day <= end:
            if (day - first).days % self.cfg["every_days"] == 0:
                for hhmm in self.cfg["times"]:
                    local = datetime.fromisoformat(f"{day.isoformat()}T{hhmm}").replace(tzinfo=self.zone, fold=0)
                    stamp = local.timestamp()
                    # Nonexistent spring-forward time is skipped. Fall-back uses only its first occurrence.
                    if datetime.fromtimestamp(stamp, self.zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                        continue
                    if after < stamp <= until:
                        out.append((stamp, f"{day.isoformat()} {hhmm}"))
            day += timedelta(days=1)
        return sorted(out)

    async def tick(self) -> None:
        async with self._lock:
            if self.active:
                phase = self.active["phase"]
                if phase == "stopping":
                    if self._off_sent and self._confirmed(False):
                        self._finish()
                    else:
                        await self._stop(self.active["reason"])
                else:
                    if not self._on_seen and self._confirmed(True):
                        self._on_seen = True
                        self.active["phase"] = "running"
                    snap = self.box.snapshot()
                    reason = self._identity(snap)
                    if not reason and (not snap.get("online") or not snap.get("mqtt")):
                        reason = "connection"
                    if not reason and (snap.get("ports", {}).get(self.port) or {}).get("can_write") is not True:
                        reason = "port"
                    if not reason and (not watering_allowed(snap) or not finite(snap.get("sensors_age"))
                                       or not 0 <= snap["sensors_age"] <= 300):
                        reason = "supply"
                    if not reason and self.monotonic() >= self._deadline:
                        reason = "duration"
                    if not reason and self._on_seen and self._confirmed(False):
                        reason = "manual"
                    if not reason and not self._on_seen and self.monotonic() >= self._deadline - self.active["duration_s"] + CONFIRM_SECONDS:
                        reason = "unconfirmed"
                    if reason:
                        await self._stop(reason)
            now = self.clock()
            if now <= self.cursor:
                return  # wall clock rollback cannot restart a slot or lengthen a running cycle
            due = self.slots(max(self.cursor, now - 8 * 86400), now) if self.cfg["enabled"] else []
            self.cursor = now
            if not due:
                return
            for stamp, slot in due:
                # Advance/persist BEFORE attempting, including blocked/missed slots. No later catch-up.
                self._event("due")
                self.events[-1]["slot"] = slot
                self._save()
                reason = "missed" if now - stamp > GRACE_SECONDS else self.blocked(self.box.snapshot())
                limit, detail = None, None
                if not reason:
                    reason, limit, detail = self._need(stamp)
                if reason:
                    self._event("skipped", reason)
                    if detail:
                        self.events[-1]["detail"] = detail
                else:
                    try:
                        await self._start(True, limit, detail)
                    except BudgetExceeded:
                        self._event("skipped", "budget")
                    except CommandRefused:
                        self._event("skipped", "publish")
                self._save()

    def view(self, snap: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        linked = snap.get("mqtt") and snap.get("online")
        reported = (snap.get("ports", {}).get(self.port) or {}).get("on") if linked else None
        reason = self.blocked(snap)
        start = max(now, self.cursor)
        first = datetime.fromisoformat(self.cfg["first_date"]).replace(tzinfo=self.zone).timestamp() if self.cfg["first_date"] else start
        future = self.slots(start, max(start, first) + 32 * 86400) if self.cfg["enabled"] else []
        status = (self.active["phase"] if self.active else "unmanaged" if reported is True
                  else "blocked" if reason else "scheduled" if self.cfg["enabled"] else "disabled")
        return {"settings": self.cfg, "status": status, "blocked": reason, "reported_on": reported,
                "active": self.active, "remaining_s": max(0, math.ceil(self._deadline - self.monotonic()))
                if self.active and self.active["phase"] != "stopping" else None,
                "next_at": future[0][0] if future else None, "events": self.events[-12:], "error": self.error}

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        async with self._lock:
            if self.active:
                await self._stop("shutdown", force=True)
        if hasattr(self.box, "port_listeners") and self.observe in self.box.port_listeners:
            self.box.port_listeners.remove(self.observe)


i18n.DE.update({"watering.push.title": "Bewässerung: Pumpenstopp prüfen",
                "watering.push.stop": "Der Controller stoppt die Pumpe. Aus ist noch nicht bestätigt; bitte prüfen, falls weiter Wasser läuft."})
i18n.EN.update({"watering.push.title": "Watering: check pump stop",
                "watering.push.stop": "The controller is stopping the pump. Off is not confirmed yet; please check if water keeps flowing."})
