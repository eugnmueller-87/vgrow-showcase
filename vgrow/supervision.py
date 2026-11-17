"""The supervision ("Aufsicht"): every part of VGrow that can go quiet, as a signal with an explicit state.

Why (15.11.2026): the thirst index sat nine days without a single measurement, the soil sensor had lost its setting,
three waterings by hand never reached it, and nothing said so. Every part had its own way of failing quietly: a log
line, a tile, a step that raised on every poll. Here each part is one signal of SIGNALS, read from what the part
already keeps, and each signal has exactly one rule in alerts.json (metric "supervision"). So the engine that exists
(vgrow/alerts.py) holds it, pushes it, reminds of it on the rule's remind_hours and says when it is back, and Hank shows
a red rule without any code of his own.

States. ok; silent (the part is quiet beyond its limit); unknown (its reader raised: counts as silent, never as fine);
starting (no proof yet in this process, or the grace after a start); waiting (a parent is silent: one cause, one push);
off (switched off on purpose); unconfigured (never installed or not set up); muted (the owner silenced it for a while).
silent and unknown alarm, ok clears, starting and waiting decide nothing, and off, unconfigured and muted end an alarm
quietly (alerts.QUIET).

Two paths, no double push. The poll (server.poll_handler) calls read() after the measured day and before its steps, so
every rule sees the same snapshot as the others; Alerts.check then evaluates every rule but the two of the watchdog.
The watcher task (vgrow_supervision, every WATCH_S) drives only those two, poll_stalled and supervision_failing,
through alerts.check(only=...), because a dead or hung poll cannot report itself, and the poll reports a dead watcher
(supervision_silent): they watch each other. The watcher also writes alive_at every minute, so the next start knows
how long VGrow was away and says it once on the phone.

Evidence. A signal whose limit is over an hour reads what its part persists (the thirst log, calibration.json,
grow-plan.json, the pest checks, news/state.json, collector.json), so a deploy never resets a long clock. Shorter ones
may read memory. A part with no proof at all in this process counts from the start and stays "starting" until its
limit has passed: no proof, no ok. Only the "seen" latches, the mutes, the peaks, the starts (a deploy's own marked)
and alive_at need this module's own file, STATE_DIR/supervision-state.json.

Reads only: nothing here switches a device, and nothing pushes except the "was away" notice, through alerts.push. No
exception text ever leaves the process: ntfy is a third party, and its address holds the topic. The class name stays
in the log and in the JSON the PC reads, and the owner's sentences carry no code name at all (sup.step.*, sup.task.*).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import box as box_mod
from . import config, device_check, i18n
from . import alerts as alerts_mod
from .automation import FAILSAFE_SECONDS
from .fan_stick import REFRESH_S as FAN_REFRESH_S
from .fan_stick import FanStick
from .grow_plan import HEIGHT_EVERY_S, UNREADABLE_PUSH_AFTER_S
from .i18n import UserError, fmt_time, localize, t
from .light_schedule import CONFIRM_SECONDS as LIGHT_CONFIRM_SECONDS
from .light_schedule import boundaries
from .light_view import DAY_REFRESH
from .measurements import COLLECT_SECONDS
from .soil import POLL_S as SOIL_POLL_S
from .soil import STALE_S as SOIL_STALE_S
from .soil import SoilSensor
from .thirst import SENSOR_WATER_LOOK_S
from .watering import CONFIRM_SECONDS as PUMP_CONFIRM_SECONDS

_LOGGER = logging.getLogger(__name__)

STATE_FILE = "supervision-state.json"
DAY_S = 86400.0
WATCH_S = 60.0  # the watcher's tick: the cadence of Hank's ticker (hank/core.py TICK_SECONDS)
# After a start, and again after a gap of the watcher: three missed polls (box.WATER_STALE_SECONDS), because serve()
# starts its parts one after another and their first readings come in within that time.
GRACE_S = float(box_mod.WATER_STALE_SECONDS)
WATCH_GAP_S = 3 * WATCH_S  # three missed ticks of the watcher (the three-missed rule of box.py): a Mac that slept
# A gap since the last sign of life this long is an outage, said once on the phone. A deploy (install_mac.sh: bootout,
# bootstrap, kickstart -k) stays well under it, so it says nothing.
GAP_S = float(box_mod.WATER_STALE_SECONDS)
FAIL_POLLS = 3  # a step that raised three polls in a row: the three-missed rule of box.py (WATER_STALE_SECONDS)
SKIPS = 2  # two failed watering slots in a row: one is a blip (a restart), two a pattern; own choice
# Starts in 24 h that make a restart loop, deploys not counted: a process that dies every 72 minutes reaches it. Own
# choice (review of 15.11.2026): a loop with gaps under GAP_S says nothing else, each start resets the evidence in
# memory, and the PC's daily check lands in a running window.
RESTARTS_24H = 20
# A deploy is no restart loop, yet it looks like one: launchctl bootout ends the process by SIGTERM, which runs no
# finally, and the Mac saw up to 31 pushes on one day (22.10.2026). scripts/install_mac.sh writes this mark just before
# it stops the server; a start within DEPLOY_WINDOW_S after it is the deploy's own. Ten minutes, an own choice: serve()
# logs in to the cloud, connects and polls before it builds the supervision, and a crash that soon after a deploy is
# lost to the count, which a loop does not miss.
DEPLOY_FILE = "deploy-at"
DEPLOY_WINDOW_S = 600.0
LIT_HOURS_MIN = HEIGHT_EVERY_S / 3600  # the lamp lit this long in 24 h before the ruler and the pest watch count
MUTE_MAX_DAYS = 14  # a mute always ends: a forgotten one must not become the next silence like the nine days of 11.2026
MUTE_WHY_MAX = 200  # the reason is kept this long; own choice
PEAK_DAYS = 14  # the highest age per signal and day, two weeks of it: the basis to calibrate every limit (15.11.2026)
NOTICES_MAX = 5  # "was away" notices still to say; own choice
AWAY_PRIORITY = 3  # the notice is news, not an alarm
# The watcher's alerts.check ends after this: under its own minute (WATCH_S), over two ntfy timeouts (10 s each,
# alerts.push) for its two rules.
CHECK_TIMEOUT_S = 45.0
SAVE_TIMEOUT_S = 10.0  # the state file is written off the loop, as the collector's (measurements.Collector._persist)
READ_EVERY_S = 600.0  # news/state.json and collector.json are read this seldom: the news watcher's own cadence
UNNAMED = re.compile(r"Task-\d+")  # a task nobody named (aiohttp's, a thread hop): nobody's part

OK, SILENT, UNKNOWN, STARTING, WAITING, OFF, UNCONFIGURED, MUTED = (
    "ok", "silent", "unknown", "starting", "waiting", "off", "unconfigured", "muted")
STATES = (OK, SILENT, UNKNOWN, STARTING, WAITING, OFF, UNCONFIGURED, MUTED)


# ---------- the catalog (German first, English twin), registered into the server catalogs at import ----------

DE: dict[str, str] = {
    "sup.age.minute": "{n} Minute",
    "sup.age.minutes": "{n} Minuten",
    "sup.age.hour": "{n} Stunde",
    "sup.age.hours": "{n} Stunden",
    "sup.age.day": "{n} Tag",
    "sup.age.days": "{n} Tagen",
    "sup.polls.one": "{n} Abfrage",
    "sup.polls.many": "{n} Abfragen",
    "sup.back": "Geprüft um {time}.",
    "sup.unknown": "Die Aufsicht kann diesen Teil gerade nicht prüfen. Das ist kein Befund über den Teil selbst. Bitte "
                   "VGrow auf dem Mac neu starten; kommt es wieder vor, das Protokoll lesen.",
    "sup.unknown.title": "{label} (nicht prüfbar)",
    "sup.phase.step": " (hängt gerade: {step})",
    "sup.phase.dead": " (die Abfrage ist beendet)",
    "sup.what.read": "Aufsicht",
    "sup.what.unknown": "ein Teil des Servers",
    "sup.what.unnamed": "eine Regel ohne Namen",
    # the poll's steps (server.poll_handler, their __qualname__) and the long-running tasks (HANDLES, REQUIRED) in plain
    # words: the owner reads no code name (review of 15.11.2026)
    "sup.step.Alerts.check": "Alarmprüfung",
    "sup.step.HumidifierAutomation.check": "Befeuchter-Automatik",
    "sup.step.AutopilotHub.check": "Autopilot",
    "sup.step.Thirst.check": "Durst-Index",
    "sup.step.FanStick.check": "Ventilator-Stick",
    "sup.step.GrowPlan.check": "Grow-Plan",
    "sup.task.vgrow_poll": "Abfrage der Box",
    "sup.task.vgrow_supervise": "Verbindung der Box",
    "sup.task.vgrow_shadow_refresh": "Verbindung der Box",
    "sup.task.watering_watchdog": "Pumpen-Wächter",
    "sup.task.light_schedule": "Lichttimer",
    "sup.task.hank": "Hank",
    "sup.task.vgrow_supervision": "Aufsicht",
    "sup.task.soil_sensor": "Bodensensor",
    "sup.task.fan_stick": "Ventilator-Stick",
    "sup.task.grow_plan_height": "Höhenleser",
    "sup.task.measurements_collect": "Messwertspeicher",
    "sup.task.pump_report_history": "Pumpenbericht",
    "sup.task.news_watch": "Nachrichten",
    "sup.task.hank_protocol": "Hanks Protokoll",
    "sup.task.growcam_watch": "GrowCam",
    "sup.task.daily_photo": "Tagesfoto",
    "sup.task.pest_watch": "Schädlingswache",
    "sup.task.usb_camera_capture": "Nahkamera",
    "sup.task.grow_plan": "Grow-Plan",
    "sup.task.news": "Nachrichten",
    "sup.poll_stalled.now": "Die Box wurde seit {age} nicht mehr abgefragt{phase}.",
    "sup.supervision_failing.now": "{what}: bricht seit {polls} hintereinander ab.",
    "sup.supervision_failing.now.rule": "Alarmregel nicht auswertbar: {what}.",
    "sup.supervision_silent.now": "Die Überwachung hat seit {age} nicht nachgesehen.",
    "sup.tasks_dead.now": "Nicht mehr aktiv: {names}.",
    "sup.watering_watchdog.now": "Der Pumpen-Wächter hat seit {age} nicht reagiert.",
    "sup.light_timer.now.unconfirmed": "Der letzte Lichtwechsel wurde von der Box nicht bestätigt.",
    "sup.light_timer.now.beat": "Der Lichttimer hat seit {age} nicht reagiert.",
    "sup.light_timer.now.storage": "Der Lichttimer kann seine Einstellungen nicht sicher lesen oder speichern und "
                                   "schaltet deshalb nicht. Unter Geräte, Lichtplan den Lichttimer prüfen und "
                                   "„Lichttimer speichern“ tippen.",
    "sup.device_link.now.link": "Die Befehlsverbindung zur Box fehlt seit {age}.",
    "sup.device_link.now.shadow": "Die Box meldet Licht, Lüfter und USB-Anschlüsse seit {age} nicht mehr.",
    "sup.probe_silent.now": "Der Fühler auf Blatthöhe liefert seit {age} keinen Wert.",
    "sup.builtin_silent.now": "Aus dem Inneren der Box kommt seit {age} kein Wert.",
    "sup.tank_sensor_silent.now": "Der Wassersensor am Tank meldet seit {age} weder nass noch trocken.",
    "sup.soil_silent.now.gateway": "Das Gateway antwortet seit {age} nicht.",
    "sup.soil_silent.now.sensor": "Das Gateway antwortet, aber der Bodenfühler sendet seit {age} nichts.",
    "sup.thirst_engine.now": "Er ist nicht gestartet oder bricht bei jeder Abfrage ab (seit {age}).",
    "sup.thirst_input.now": "Seit {age} hat der Durst-Index keine Messung bekommen (Fingerprobe, Wägung oder "
                            "Bodensensor).",
    "sup.watering_skips.now": "Die letzten {n} Gießtermine liefen nicht: {reason}",
    "sup.watering_skips.now.storage": "Die Bewässerungsdaten lassen sich nicht sicher lesen oder speichern, deshalb "
                                      "läuft kein Gießtermin. Auf der Seite Automatik den Gießplan prüfen und "
                                      "„Gießplan speichern“ tippen.",
    "sup.watering_skips.thirst": "Der Durst-Index hat den Termin übersprungen.",
    "sup.push_broken.now.none": "Es ist kein Push-Ziel eingerichtet.",
    "sup.push_broken.now.failing": "Seit {age} scheitert jeder Push.",
    "sup.hank_silent.now.beat": "Hank hat seit {age} nicht nachgesehen.",
    "sup.hank_silent.now.missing": "Hank wurde nicht gestartet.",
    "sup.restarts.now": "VGrow startete in den letzten 24 Stunden {n} Mal neu, ohne dass ausgeliefert wurde.",
    "sup.soil_uncalibrated.now": "Seit {age} liefert der Sensor Werte, ist aber nicht auf deine Erde eingestellt.",
    "sup.thirst_sensor_look.now": "Der Durst-Index hat seit {age} nicht nach Wasser im Bodensensor gesucht, obwohl "
                                  "das Gateway antwortet.",
    "sup.ruler_silent.now": "Seit {age} gab es keine brauchbare Höhenmessung aus dem Kamerabild.",
    "sup.fan_stick_silent.now": "Der Stick am Ventilator (E25) meldet seit {age} nichts.",
    "sup.pest_watch_silent.now": "Seit {age} gab es keine gelungene Schädlingsprüfung.",
    "sup.store_silent.now.collect": "Seit {age} kommen keine Messwerte im Speicher an.",
    "sup.store_silent.now.day": "Die Lichtbilanz der letzten 24 Stunden lässt sich seit {age} nicht lesen.",
    "sup.store_silent.now.missing": "Der Messwertspeicher ist nicht gestartet.",
    "sup.news_silent.now": "Seit {age} gab es keinen erfolgreichen Abruf.",
    "sup.ollama_silent.now": "Ollama hat seit {age} nicht geantwortet.",
    "sup.away.title": "VGrow lief nicht",
    "sup.away.body": "VGrow lief von {start} bis {end} nicht. In der Zeit liefen weder Alarme noch Automatik.",
    "sup.away.body_days": "VGrow lief vom {start_day} {start} bis {end_day} {end} nicht. In der Zeit liefen weder "
                          "Alarme noch Automatik.",
    "sup.away.slots": "Geplante Gießzeiten in der Zeit sind entfallen, es wird nicht nachgegossen.",
    "sup.mute.unknown": "Unbekanntes Signal {signal}",
    "sup.mute.days": "Stummschalten für 1 bis {max} Tage",
    "sup.mute.why": "Bitte einen Grund angeben",
    "sup.none": "Keine Aufsicht in diesem Lauf",
}

EN: dict[str, str] = {
    "sup.age.minute": "{n} minute",
    "sup.age.minutes": "{n} minutes",
    "sup.age.hour": "{n} hour",
    "sup.age.hours": "{n} hours",
    "sup.age.day": "{n} day",
    "sup.age.days": "{n} days",
    "sup.polls.one": "{n} poll",
    "sup.polls.many": "{n} polls",
    "sup.back": "Checked at {time}.",
    "sup.unknown": "The supervision cannot check this part right now. This is no finding about the part itself. Please "
                   "restart VGrow on the Mac; if it happens again, read the log.",
    "sup.unknown.title": "{label} (cannot be checked)",
    "sup.phase.step": " (stuck at {step})",
    "sup.phase.dead": " (the polling has ended)",
    "sup.what.read": "Supervision",
    "sup.what.unknown": "a part of the server",
    "sup.what.unnamed": "a rule without a name",
    "sup.step.Alerts.check": "Alarm check",
    "sup.step.HumidifierAutomation.check": "Humidifier automation",
    "sup.step.AutopilotHub.check": "Autopilot",
    "sup.step.Thirst.check": "Thirst index",
    "sup.step.FanStick.check": "Fan stick",
    "sup.step.GrowPlan.check": "Grow plan",
    "sup.task.vgrow_poll": "box polling",
    "sup.task.vgrow_supervise": "box connection",
    "sup.task.vgrow_shadow_refresh": "box connection",
    "sup.task.watering_watchdog": "pump watchdog",
    "sup.task.light_schedule": "light timer",
    "sup.task.hank": "Hank",
    "sup.task.vgrow_supervision": "supervision",
    "sup.task.soil_sensor": "soil sensor",
    "sup.task.fan_stick": "fan stick",
    "sup.task.grow_plan_height": "height reader",
    "sup.task.measurements_collect": "measurement store",
    "sup.task.pump_report_history": "pump report",
    "sup.task.news_watch": "news",
    "sup.task.hank_protocol": "Hank's log",
    "sup.task.growcam_watch": "GrowCam",
    "sup.task.daily_photo": "daily photo",
    "sup.task.pest_watch": "pest watch",
    "sup.task.usb_camera_capture": "close-up camera",
    "sup.task.grow_plan": "grow plan",
    "sup.task.news": "news",
    "sup.poll_stalled.now": "The box has not been polled for {age}{phase}.",
    "sup.supervision_failing.now": "{what}: failed on {polls} in a row.",
    "sup.supervision_failing.now.rule": "Alarm rule cannot be evaluated: {what}.",
    "sup.supervision_silent.now": "The supervision has not checked for {age}.",
    "sup.tasks_dead.now": "No longer active: {names}.",
    "sup.watering_watchdog.now": "The pump watchdog has not responded for {age}.",
    "sup.light_timer.now.unconfirmed": "The box did not confirm the last light switch.",
    "sup.light_timer.now.beat": "The light timer has not responded for {age}.",
    "sup.light_timer.now.storage": "The light timer cannot read or save its settings safely, so it does not switch. "
                                   "Under Devices, Light schedule check the light timer and tap “Save light timer”.",
    "sup.device_link.now.link": "The command link to the box has been down for {age}.",
    "sup.device_link.now.shadow": "For {age} the box has not reported light, fans or USB ports.",
    "sup.probe_silent.now": "The probe at leaf height has sent no value for {age}.",
    "sup.builtin_silent.now": "No value from inside the box for {age}.",
    "sup.tank_sensor_silent.now": "The water sensor at the tank has reported neither wet nor dry for {age}.",
    "sup.soil_silent.now.gateway": "The gateway has not answered for {age}.",
    "sup.soil_silent.now.sensor": "The gateway answers, but the soil probe has sent nothing for {age}.",
    "sup.thirst_engine.now": "It did not start or fails on every poll (for {age}).",
    "sup.thirst_input.now": "For {age} the thirst index has had no measurement (finger test, weighing or soil "
                            "sensor).",
    "sup.watering_skips.now": "The last {n} watering slots did not run: {reason}",
    "sup.watering_skips.now.storage": "The watering data cannot be read or saved safely, so no watering slot runs. On "
                                      "the Automation page check the watering schedule and tap “Save watering "
                                      "schedule”.",
    "sup.watering_skips.thirst": "The thirst index skipped the slot.",
    "sup.push_broken.now.none": "No push address is set up.",
    "sup.push_broken.now.failing": "Every push has failed for {age}.",
    "sup.hank_silent.now.beat": "Hank has not looked for {age}.",
    "sup.hank_silent.now.missing": "Hank was not started.",
    "sup.restarts.now": "VGrow restarted {n} times in the last 24 hours without a deploy.",
    "sup.soil_uncalibrated.now": "For {age} the sensor has delivered values but is not set to your soil.",
    "sup.thirst_sensor_look.now": "For {age} the thirst index has not looked for water in the soil sensor although "
                                  "the gateway answers.",
    "sup.ruler_silent.now": "For {age} there was no usable height reading from the camera picture.",
    "sup.fan_stick_silent.now": "The stick at the fan (E25) has reported nothing for {age}.",
    "sup.pest_watch_silent.now": "For {age} there was no successful pest check.",
    "sup.store_silent.now.collect": "No readings have reached the store for {age}.",
    "sup.store_silent.now.day": "The light balance of the last 24 hours cannot be read for {age}.",
    "sup.store_silent.now.missing": "The measurement store did not start.",
    "sup.news_silent.now": "No successful fetch for {age}.",
    "sup.ollama_silent.now": "Ollama has not answered for {age}.",
    "sup.away.title": "VGrow was not running",
    "sup.away.body": "VGrow did not run from {start} to {end}. Meanwhile neither alerts nor automation ran.",
    "sup.away.body_days": "VGrow did not run from {start_day} {start} to {end_day} {end}. Meanwhile neither alerts "
                          "nor automation ran.",
    "sup.away.slots": "Watering slots in that time were skipped and are not made up.",
    "sup.mute.unknown": "Unknown signal {signal}",
    "sup.mute.days": "Mute for 1 to {max} days",
    "sup.mute.why": "Please give a reason",
    "sup.none": "No supervision in this run",
}

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself


def check_catalog() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash."""
    problems = [f"only in DE: {k}" for k in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {k}" for k in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        if i18n.placeholders(DE[key]) != i18n.placeholders(EN[key]):
            problems.append(f"placeholders differ: {key}")
        if "lang" in i18n.placeholders(DE[key]):
            problems.append(f"placeholder lang: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    """Put the texts into the server catalogs (i18n.DE, i18n.EN). Safe to call more than once."""
    problems = check_catalog()
    if problems:
        raise ValueError("supervision: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


register()


# ---------- a reading and its context ----------

def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and value == value \
        and abs(value) != float("inf") else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Reading:
    """What a reader found: the state, the newest proof that the part works (None: none in this process), and the
    values of its sentence (age in seconds, cause, names)."""
    state: str
    last_ok: float | None = None
    params: dict[str, Any] = field(default_factory=dict)


class Ctx:
    """One evaluation: the Supervisor, the snapshot it reads, the moment, and what was read so far (the parents)."""

    def __init__(self, sup: Supervisor, snap: Mapping[str, Any], now: float) -> None:
        self.sup, self.snap, self.now = sup, snap, now
        self.done: dict[str, tuple[Reading, str]] = {}  # id: (what the reader found, the final state)
        self._memo: dict[str, Any] = {}

    def part(self, name: str) -> Any:
        return self.sup.parts.get(name)

    def has(self, name: str) -> bool:
        """serve() put this part into parts (None there: its start failed)."""
        return name in self.sup.parts

    def since(self, at: float | None) -> float:
        """Seconds since `at`, or since the start without it; a clock set back gives 0, never a silence."""
        return max(0.0, self.now - (self.sup.started if at is None else at))

    def aged(self, sig: str, last_ok: float | None, *, limit: float | None = None, **params: Any) -> Reading:
        """ok while the newest proof is within the limit, silent beyond it. Without proof in this process the age
        counts from the start, and the part is "starting" until the limit has passed: no proof, no ok."""
        limit = BY_ID[sig].limit if limit is None else limit
        age = self.since(last_ok)
        if last_ok is None:
            return Reading(SILENT if age > limit else STARTING, None, {**params, "age": age})
        return Reading(SILENT if age > limit else OK, last_ok, {**params, "age": age})

    def remembered(self, sig: str) -> float | None:
        """The newest proof an earlier evaluation of this process saw (memory: fine for a limit under an hour)."""
        return self.sup.last_ok.get(sig)

    def box_fresh(self) -> bool:
        """Online with readings at most three polls old (alerts.tank_breached): else the offline rule owns the cause."""
        age = _num(self.snap.get("sensors_age"))
        return bool(self.snap.get("online")) and age is not None and age <= box_mod.WATER_STALE_SECONDS

    def reading_at(self) -> float:
        """The newest reading's own time (sensors_age is its age, not the poll's)."""
        return self.now - (_num(self.snap.get("sensors_age")) or 0.0)

    def expected(self, sig: str, present: bool) -> bool:
        """The rule's "expect": always, or seen (also without one): expected once the part reported itself one time.
        The latch lives in supervision-state.json, so a part that later goes quiet stays expected."""
        if present:
            self.sup.latch(sig, self.now)
        return self.sup.expect(sig) == "always" or sig in self.sup.state["seen"]

    def memo(self, key: str, make: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    def soil(self) -> SoilSensor | None:
        """The soil sensor, a real one: the demo's made-up one is no part to watch."""
        found = self.part("soil")
        return found if isinstance(found, SoilSensor) else None

    def soil_view(self) -> dict[str, Any]:
        return self.memo("soil", lambda: dict(self.soil().view()))  # type: ignore[union-attr]

    def thirst_data(self) -> dict[str, Any]:
        return self.memo("thirst", lambda: dict(self.part("thirst").supervision()))

    def profile(self) -> Any:
        from .plant import load_profile  # here: plant.json is read once per evaluation, only by who needs it

        def read() -> Any:
            try:
                return load_profile()
            except Exception:  # an unreadable plant.json is the plant card's to say; here it means no plant
                return None

        return self.memo("profile", read)

    def lit_hours(self) -> float | None:
        """Hours the lamp was lit in the last 24 (the measured day of this poll), None without the day."""
        day = self.snap.get("light_day")
        return _num(day.get("lit_hours")) if isinstance(day, dict) else None


# ---------- the readers, one per signal ----------

def _poll(ctx: Ctx) -> Reading:
    """Box._poll_loop: alive, and a round ended within the limit. Every round ends, a failed one too (box.poll_once
    notifies the listeners), so a cloud outage is no stall."""
    loops = ctx.sup.box_loops()
    if loops is None:
        return Reading(UNCONFIGURED)  # the demo's box has no loops, and no supervision
    task = loops.get("vgrow_poll")
    last = _num(getattr(ctx.sup.box, "polled_at", None))
    if task is None or task.done():
        return Reading(SILENT, last, {"age": ctx.since(last), "phase": "dead"})
    return ctx.aged("poll_stalled", last, phase=ctx.sup.phase if ctx.sup.round_at is not None else None)


def _failing(ctx: Ctx) -> Reading:
    """The swallowed exceptions: a poll step, or read() itself, that raised FAIL_POLLS polls in a row, or a rule of
    alerts.json that raised at its last check (Alerts.broken; one bad entry counts at once)."""
    sup = ctx.sup
    steps = [["read", "", sup.read_fails, sup.read_error]] if sup.read_fails >= FAIL_POLLS else []
    steps += [["step", name, st["n"], st["error"]] for name, st in sorted(sup.steps.items()) if st["n"] >= FAIL_POLLS]
    broken = _dict(getattr(sup.alerts, "broken", None))
    rules = [["rule", name, int(_dict(st).get("n") or 1), _dict(st).get("error")] for name, st in sorted(broken.items())]
    found = steps + rules
    if not found:
        return Reading(OK, ctx.now)
    # a broken rule is named by its own label in the sentence, never its id (the id and the class stay in params)
    labels = {name: {k: v for k, v in sup._rule(name).items() if k in ("label", "label_en")} for name in broken}
    return Reading(SILENT, None, {"cause": "step" if steps else "rule", "what": [[k, n] for k, n, _, _ in found],
                                  "n": max(n for _, _, n, _ in found), "error": found[0][3] or "?",
                                  **({"labels": labels} if rules else {})})


def _watcher(ctx: Ctx) -> Reading:
    """The watcher task's beat (set at the start of every tick). The poll reports it: the watcher cannot report its
    own death."""
    task, beat = ctx.sup.task, ctx.sup.watch_beat
    if task is not None and task.done():
        return Reading(SILENT, beat, {"age": ctx.since(beat)})
    return ctx.aged("supervision_silent", beat)


def _tasks(ctx: Ctx) -> Reading:
    """Every 'while True' task of TASKS that should run: one that ended is dead (a cancelled one was stopped on
    purpose), and a part of REQUIRED whose start failed is listed under its own name. Nothing inside the grace:
    serve() starts the parts one after another."""
    if ctx.now < ctx.sup.grace_until:
        return Reading(STARTING, None, {"names": []})
    states = ctx.sup.task_states()
    dead = [name for name in TASKS if states.get(name) == "dead"]
    dead += [name for name in REQUIRED if ctx.has(name) and ctx.part(name) is None]
    return Reading(SILENT, None, {"names": dead}) if dead else Reading(OK, ctx.now, {"names": []})


def _pump(ctx: Ctx) -> Reading:
    """The watering watchdog, the loop that switches the pump off (watering.py, line 1): its completed tick."""
    ctl = ctx.part("watering")
    if ctl is None:
        return Reading(UNCONFIGURED)
    task, beat = getattr(ctl, "task", None), _num(getattr(ctl, "ticked_at", None))
    if task is not None and task.done():
        return Reading(SILENT, beat, {"age": ctx.since(beat)})
    return ctx.aged("watering_watchdog", beat)


def _light(ctx: Ctx) -> Reading:
    """The light timer's own verdict and its loop: after two unconfirmed attempts it sets failed and says nothing until
    the next edge (light_schedule.tick). The lamp's reported level decides with it (review of 15.11.2026): a lamp that
    stands at the edge's level was switched outside the cockpit (the VIVOSUN app, a late report) and ends the alarm,
    which the timer itself never notices, and after a failed edge the next one waits while the timer tries it, so a
    standing fault gives neither an all-clear nor a new alarm at every edge. A storage fault is silent: the timer then
    switches nothing until it is saved again, disabled or not (light_schedule.blocked). A timer the owner switched off,
    his change until the next edge and the blocked states (connection, mode) are not this signal's business."""
    timer = ctx.part("light_schedule")
    if timer is None:
        return Reading(UNCONFIGURED)
    if getattr(timer, "error", None) == "storage":
        return Reading(SILENT, None, {"cause": "storage"})
    cfg = _dict(getattr(timer, "cfg", None))
    if not cfg.get("enabled"):
        return Reading(OFF)
    task, beat = getattr(timer, "task", None), _num(getattr(timer, "ticked_at", None))
    if task is not None and task.done():
        return Reading(SILENT, beat, {"age": ctx.since(beat), "cause": "beat"})
    found = ctx.aged("light_timer", beat, cause="beat")
    if found.state == SILENT:
        return found
    override = _num(getattr(timer, "override_until", None))
    if override is not None and ctx.now < override:
        return found
    edge = boundaries(cfg, ctx.now)[0]
    stands = _num(_dict(ctx.snap.get("light")).get("level")) == edge["level"]  # an unknown level never stands
    failed = _num(getattr(timer, "failed", None))
    if failed == edge["at"]:
        return found if stands else Reading(SILENT, None, {"cause": "unconfirmed"})
    trying = (_dict(getattr(timer, "pending", None)).get("boundary") == edge["at"]
              or ctx.now - edge["at"] <= 3 * LIGHT_CONFIRM_SECONDS)  # its two attempts, from the edge's first tick
    if failed is not None and not stands and getattr(timer, "completed", None) != edge["at"] and trying:
        return Reading(WAITING)  # an earlier edge failed and this one is not confirmed yet: decides nothing
    return found


def _device_link(ctx: Ctx) -> Reading:
    """The reported shadow (light, fans, USB): its own time while MQTT is up, else the last poll that had both. The
    gates refuse above 300 s already (watering.blocked, light_schedule.blocked)."""
    if not ctx.box_fresh():
        return Reading(WAITING)
    age = _num(ctx.snap.get("shadow_age"))
    if ctx.snap.get("mqtt") and age is not None:
        return ctx.aged("device_link", ctx.now - age, cause="shadow")
    return ctx.aged("device_link", ctx.remembered("device_link"), cause="shadow" if ctx.snap.get("mqtt") else "link")


def _climate(sig: str, keys: tuple[str, str]) -> Callable[[Ctx], Reading]:
    """A sensor of the box (box.climate_view): either of its two values on a poll, while the box is fresh. A dead probe
    falls back to the built-in sensor without a word (alerts.reading_value), and the humidifier's fail-safe acts only
    while USB 2 is on; without the built-in sensor the mist test is off (thirst._sample)."""
    def read(ctx: Ctx) -> Reading:
        if not ctx.box_fresh():
            return Reading(WAITING)
        climate = _dict(ctx.snap.get("climate"))
        present = any(climate.get(key) is not None for key in keys)
        if not ctx.expected(sig, present):
            return Reading(UNCONFIGURED)
        return ctx.aged(sig, ctx.reading_at() if present else ctx.remembered(sig))
    return read


def _tank(ctx: Ctx) -> Reading:
    """The water sensor: a wet/dry row (Box.water_seen_at, the newest row with one) or a level. Stale or missing state
    neither alarms nor clears the tank rule (alerts.tank_breached) and the pump refuses (box.watering_allowed), so
    without this a dead sensor only skips, silently. Expected once its accessory type (9 or 10) was seen."""
    if not ctx.box_fresh():
        return Reading(WAITING)
    kind = box_mod.aux_type(ctx.snap.get("aux_device"))
    if not ctx.expected("tank_sensor_silent", kind in (box_mod.AUX_WET_DRY, box_mod.AUX_LEVEL)):
        return Reading(UNCONFIGURED)
    climate = _dict(ctx.snap.get("climate"))
    seen = [_num(getattr(ctx.sup.box, "water_seen_at", None))]
    if climate.get("water_level") is not None or climate.get("water_state") in ("wet", "dry"):
        seen.append(ctx.reading_at())
    found = max((s for s in seen if s is not None), default=None)
    return ctx.aged("tank_sensor_silent", found if found is not None else ctx.remembered("tank_sensor_silent"))


def _soil(ctx: Ctx) -> Reading:
    """A usable soil percent (SoilSensor.view). The gateway's answer alone is no proof: it can answer without the
    WH51's channel (soil.poll_once sets heard_at either way)."""
    if ctx.soil() is None:
        return Reading(UNCONFIGURED)
    view = ctx.soil_view()
    if view.get("percent") is not None:
        return ctx.aged("soil_silent", ctx.now - (_num(view.get("age")) or 0.0), cause="sensor")
    return ctx.aged("soil_silent", ctx.remembered("soil_silent"), cause="sensor" if view.get("fresh") else "gateway")


def _thirst_engine(ctx: Ctx) -> Reading:
    """Thirst.check past its ledger (checked_at). Without a Durst-Index the gate is closed and every slot skips
    (server.py, thirst.closed_gate)."""
    if not ctx.has("thirst"):
        return Reading(UNCONFIGURED)
    thirst = ctx.part("thirst")
    if thirst is None:
        return Reading(SILENT, None, {"age": ctx.since(None), "cause": "missing"})
    return ctx.aged("thirst_engine", _num(getattr(thirst, "checked_at", None)))


def _thirst_input(ctx: Ctx) -> Reading:
    """The incident's outcome: the newest check or weighing (Thirst.measured_at, floor the pot's set-up), against the
    model's measured_valid_s. Configured only for a pot the plan or the sensor relies on."""
    if ctx.part("thirst") is None:
        return Reading(WAITING if ctx.has("thirst") else UNCONFIGURED)  # thirst_engine owns a failed start
    data = ctx.thirst_data()
    set_at = _num(data.get("pot_set_at"))
    if set_at is None or not (data.get("schedule_on") or ctx.soil() is not None):
        return Reading(UNCONFIGURED)  # no pot, or a pot the owner checks purely by hand
    valid = _num(data.get("valid_s"))
    if _num(data.get("checked_at")) is None or valid is None:
        # no check ran to its end yet (thirst_engine), or the model is unreadable: then Thirst.check raises in its ET
        # sample on every poll (supervision_failing) and the gate skips every slot (watering_skips)
        return Reading(WAITING)
    return ctx.aged("thirst_input", max(set_at, _num(data.get("measured_at")) or 0.0), limit=valid)


def _skips(ctx: Ctx) -> Reading:
    """The last SKIPS scheduled slots all ended in a fault (fault()): a setup, a port, the model, the flow. A slot
    the gate skipped because the pot is not due, or one another rule owns (offline, the tank), breaks the chain."""
    ctl = ctx.part("watering")
    if ctl is None:
        return Reading(UNCONFIGURED)
    if getattr(ctl, "error", None) == "storage":
        # watering.json unreadable (the plan comes up as DEFAULTS, switched off) or a save failed: no slot runs until
        # it is saved again, and as "off" this read like the owner's own choice (review of 15.11.2026)
        return Reading(SILENT, None, {"cause": "storage"})
    if not _dict(getattr(ctl, "cfg", None)).get("enabled"):
        return Reading(OFF)  # a plan the owner switched off is quiet
    # only the slots since it was last switched on (WateringController.enabled_at): watering.json keeps its events while
    # it is off, and two failed slots of last week alarmed at the first poll after the owner fixed the cause
    since = _num(getattr(ctl, "enabled_at", None)) or 0.0
    slots = slot_outcomes(getattr(ctl, "events", None) or [])
    last = [slot for slot in slots if (_num(slot.get("at")) or 0.0) >= since][-SKIPS:]
    if len(last) == SKIPS and all(fault(slot) for slot in last):
        return Reading(SILENT, None, {"n": SKIPS, "reason": last[-1]})
    return Reading(OK, ctx.now)


def _push(ctx: Ctx) -> Reading:
    """ntfy: no target is the failure itself here, since then nothing could speak; else every push failing longer than
    the limit while the attempts still fail (Alerts.push_health). The engine retries an undelivered alarm every poll,
    so a real outage with one waiting keeps failed_at fresh, and queued alarms arrive once ntfy is back. A single failed
    push nobody retries (a one-shot notice, a failed all-clear) proves no outage once failed_at is older than
    alerts.PUSH_RUN_GAP_S, and then nothing waits to be sent: it alarmed 20 minutes later over a working ntfy before
    (review of 15.11.2026)."""
    alerts = ctx.sup.alerts
    if getattr(alerts, "target", None) is None:
        return Reading(SILENT, None, {"cause": "none"})
    health = alerts.push_health() if callable(getattr(alerts, "push_health", None)) else {}
    since, tried = _num(health.get("failed_since")), _num(health.get("failed_at"))
    if since is None or tried is None or ctx.now - tried > alerts_mod.PUSH_RUN_GAP_S:
        return Reading(OK, ctx.now)
    age = ctx.since(since)
    return Reading(SILENT if age > BY_ID["push_broken"].limit else OK, _num(health.get("ok_at")),  # type: ignore[operator]
                   {"age": age, "cause": "failing"})


def _hank(ctx: Ctx) -> Reading:
    """Hank's ticker (ticked_at, also in mode off). He cannot report his own silence."""
    if not ctx.has("hank"):
        return Reading(UNCONFIGURED)
    hank = ctx.part("hank")
    if hank is None:
        return Reading(SILENT, None, {"age": ctx.since(None), "cause": "missing"})
    task, beat = getattr(hank, "task", None), _num(getattr(hank, "ticked_at", None))
    if task is not None and task.done():
        return Reading(SILENT, beat, {"age": ctx.since(beat), "cause": "beat"})
    return ctx.aged("hank_silent", beat, cause="beat")


def _uncalibrated(ctx: Ctx) -> Reading:
    """The incident's first cause: a sensor that delivers values but was never set to the soil (calibrated False), so
    Thirst.sensor_check logs no check. Its own clock, max(the pot's set-up, calibration.json's mtime): a reset rewrites
    the file, and an old state alarms at the first evaluation, not a day after a deploy."""
    sensor = ctx.soil()
    if sensor is None:
        return Reading(UNCONFIGURED)
    if ctx.part("thirst") is None:
        return Reading(WAITING if ctx.has("thirst") else UNCONFIGURED)
    set_at = _num(ctx.thirst_data().get("pot_set_at"))
    if set_at is None:
        return Reading(UNCONFIGURED)
    view = ctx.soil_view()
    if view.get("percent") is None:
        return Reading(WAITING)  # soil_silent owns a sensor without a value
    if view.get("calibrated"):
        return Reading(OK, ctx.now)
    return ctx.aged("soil_uncalibrated", max(set_at, _mtime(sensor.calibration_path()) or 0.0))


def _look(ctx: Ctx) -> Reading:
    """Thirst.sensor_waterings run to its end (look_at): the wire the hand waterings of 07., 12. and 15.11.2026 needed.
    Its early returns (no pot, no soil history, a pump run open) are no look."""
    if ctx.soil() is None:
        return Reading(UNCONFIGURED)
    if ctx.part("thirst") is None:
        return Reading(WAITING if ctx.has("thirst") else UNCONFIGURED)
    data = ctx.thirst_data()
    if _num(data.get("pot_set_at")) is None:
        return Reading(UNCONFIGURED)
    if not ctx.soil_view().get("fresh"):
        return Reading(WAITING)  # behind soil_silent: a gateway that does not answer gives nothing to look at
    return ctx.aged("thirst_sensor_look", _num(data.get("look_at")))


def _ruler(ctx: Ctx) -> Reading:
    """A usable height from the GrowCam's picture (grow-plan.json height.at). height_tick returns silently with the
    lamp off or the camera down, and the lamp distance check then works with the last height."""
    plan = ctx.part("grow_plan")
    if plan is None or not ctx.profile():
        return Reading(UNCONFIGURED)
    cam = _dict(ctx.snap.get("camera"))
    if not cam.get("configured"):
        return Reading(UNCONFIGURED)
    if cam.get("hidden"):
        return Reading(OFF)  # the owner put the GrowCam away (camera_prefs)
    lit = ctx.lit_hours()
    if cam.get("reachable") is not True or cam.get("searching") or lit is None or lit < LIT_HOURS_MIN:
        return Reading(WAITING)  # camera_offline owns a camera that is down; a dark day has nothing to read
    state = _dict(getattr(plan, "state", None))
    if isinstance(state.get("height_problem"), dict):
        return Reading(WAITING)  # the plan's own push after 24 h owns an unreadable strip
    return ctx.aged("ruler_silent", _num(_dict(state.get("height")).get("at")))


def _fan_stick(ctx: Ctx) -> Reading:
    """The E25 stick's shadow report (FanStick.view age), while its day and night rule is on. The loop logs a silent
    stick once, and check() never touches a fan that is not reporting."""
    stick = ctx.part("fan_stick")
    if not isinstance(stick, FanStick):
        return Reading(UNCONFIGURED)
    view = _dict(stick.view())
    if not view.get("found"):
        return Reading(UNCONFIGURED)
    if not view.get("auto"):
        return Reading(OFF)  # the owner's off, here or in the app
    if not ctx.box_fresh():
        # the stick reads its shadow with the box's AWS session: a cloud or internet outage silences both, and the
        # offline rule owns that cause. Not a parent: device_link itself only waits then (review of 15.11.2026)
        return Reading(WAITING)
    age = _num(view.get("age"))
    return ctx.aged("fan_stick_silent", None if age is None else ctx.now - age)


def _pest(ctx: Ctx) -> Reading:
    """The newest pest check with a result (one file per check under pest/checks). A failure is only a record
    without one and a log line."""
    pest = ctx.part("pest")
    if pest is None or not ctx.profile():
        return Reading(UNCONFIGURED)
    lit = ctx.lit_hours()
    if pest.camera() is None or lit is None or lit < LIT_HOURS_MIN:
        return Reading(WAITING)  # the camera rules own a missing camera; a dark day has no check
    done = [c for c in pest.checks() if isinstance(c, dict) and c.get("result")]
    return ctx.aged("pest_watch_silent", _num(done[-1].get("at")) if done else None)


def _store(ctx: Ctx) -> Reading:
    """The point-log collection (Collector.last_ok, else collector.json) and the measured day of this poll: without the
    day the rules dark_period and short_day decide nothing, silently (alerts.light_day_breached)."""
    if not ctx.has("store"):
        return Reading(UNCONFIGURED)
    if not ctx.box_fresh():
        return Reading(WAITING)  # an offline box gives the collector nothing to fetch
    collector = ctx.part("collector")
    if ctx.part("store") is None or collector is None:
        return Reading(SILENT, None, {"age": ctx.since(None), "cause": "missing"})
    last = _num(getattr(collector, "last_ok", None))
    found = ctx.aged("store_silent", last if last is not None else ctx.sup.saved_collect(collector), cause="collect")
    if found.state != SILENT and ctx.part("light_day") is not None:
        day = ctx.aged("store_silent", ctx.sup.day_ok_at, limit=DAY_REFRESH, cause="day")
        if day.state == SILENT:
            return day
    return found


def _news(ctx: Ctx) -> Reading:
    """news/state.json last_success (else its first run), read tolerantly: news.load_state() moves a broken file
    aside, which a reader must never do."""
    if ctx.part("news") is None:
        return Reading(UNCONFIGURED)  # not started yet, or its start failed (tasks_dead names that)
    return ctx.aged("news_silent", ctx.sup.news_success())


def _ollama(ctx: Ctx) -> Reading:
    """Hank's cached health probe ("ollama" ok). Only his tick refreshes it, and in mode off his tick returns first,
    so the cache goes stale on purpose there."""
    health = getattr(ctx.part("hank"), "health", None)
    if health is None:
        return Reading(UNCONFIGURED)
    from .hank.core import read_mode  # here: Hank is loaded once he exists, and a broken Hank costs this signal only

    if read_mode() == "off":
        return Reading(OFF)
    cached = health.cached
    checked = _num(cached.get("checked")) if isinstance(cached, dict) and cached.get("ollama") == "ok" else None
    return ctx.aged("ollama_silent", checked if checked is not None else ctx.remembered("ollama_silent"))


def _restarts(ctx: Ctx) -> Reading:
    """A restart loop (review of 15.11.2026): the starts of 24 h in supervision-state.json, this one counted, without
    the deploys' own (DEPLOY_FILE). Each gap stays under GAP_S, so no "was away" notice comes, each start resets the
    evidence in memory and the grace hides the rest. The count is whole at the start, so no grace (Signal.grace)."""
    n = ctx.sup.starts_24h(ctx.now) - ctx.sup.deploys_24h(ctx.now)
    return Reading(SILENT, None, {"n": n}) if n >= RESTARTS_24H else Reading(OK, ctx.now, {"n": n})


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


# ---------- the watering slots (watering.json events) ----------

# A skip is a fault when nothing but the owner or a repair helps (the codes of WateringController.blocked and of the
# need gate, Thirst._verdict). Benign: missed (the "was away" notice owns it), connection and supply (offline,
# device_link, tank_empty, tank_sensor_silent own them), busy, and every gate answer about the pot itself.
FAULT_REASONS = ("setup", "role", "assignment", "port", "storage", "budget", "publish")
FAULT_GATES = ("model", "no_pot", "unknown", "no_power", "no_flow", "error")


def slot_outcomes(events: list[Any]) -> list[dict[str, Any]]:
    """Each scheduled slot ("due") with how it ended: skipped (its reason, the gate's code, the gate's text) or
    started; a start that ended in "skipped publish" (watering._start) is a publish fault. A slot not ended yet has
    no outcome."""
    slots: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if kind == "due":
            slots.append({"at": event.get("at"), "outcome": None})
        elif slots and kind == "skipped" and slots[-1]["outcome"] in (None, "start"):
            if slots[-1]["outcome"] is None or event.get("reason") == "publish":
                slots[-1].update(outcome="skipped", code=event.get("reason"), gate=event.get("gate"),
                                 detail=event.get("detail") if isinstance(event.get("detail"), dict) else None)
        elif slots and kind == "start" and slots[-1]["outcome"] is None:
            slots[-1]["outcome"] = "start"
    return slots


def fault(slot: Mapping[str, Any]) -> bool:
    if slot.get("outcome") != "skipped":
        return False
    if slot.get("code") == "thirst":
        return slot.get("gate") in FAULT_GATES  # without a gate code (an event before 15.11.2026): benign
    return slot.get("code") in FAULT_REASONS


# ---------- the tasks (asyncio.all_tasks names and the parts' handles) ----------

# Where each long-running task's handle lives: (the key of serve()'s parts, the attribute); "box" for Box.loops(),
# "self" for the supervision's own, None for one found by its name only (make_app's cleanup context keeps no handle).
HANDLES: dict[str, tuple[str | None, str | None]] = {
    "vgrow_poll": ("box", None), "vgrow_supervise": ("box", None), "vgrow_shadow_refresh": ("box", None),
    "watering_watchdog": ("watering", "task"), "light_schedule": ("light_schedule", "task"), "hank": ("hank", "task"),
    "vgrow_supervision": ("self", "task"),
    "soil_sensor": ("soil", "task"), "fan_stick": ("fan_stick", "task"), "grow_plan_height": ("grow_plan", "task"),
    "measurements_collect": ("collector", "task"), "pump_report_history": ("pump_history", "task"),
    "news_watch": ("news", "task"), "hank_protocol": (None, None), "growcam_watch": ("camera", "watch_task"),
    "daily_photo": ("vision", "task"), "pest_watch": ("pest", "task"), "usb_camera_capture": ("usb_camera", "_task"),
}
# The tasks another signal owns, with that signal.
OWNED = {"vgrow_poll": "poll_stalled", "watering_watchdog": "watering_watchdog", "light_schedule": "light_timer",
         "hank": "hank_silent", "vgrow_supervision": "supervision_silent"}
TASKS = tuple(name for name in HANDLES if name not in OWNED)  # tasks_dead watches these
# Tasks that end by design, with why.
TRANSIENT = {"growcam_search": "ends once the camera is found (Camera.keep_searching)",
             "plant_analyze": "one reading, on the owner's tap", "usb_camera_recording": "one recording",
             "demo_poll": "the demo's box, which runs without a supervision"}
# The vendored MQTT client's loops (vgrow/vivosun/mqtt_client.py connect()), one pair per link: the box's and the E25
# stick's. Not ours to name, and watched through their link: a failed loop disconnects, so the box's "mqtt" turns
# False (device_link) and the stick's shadow ages (fan_stick_silent). Without this every real run listed them as
# unwatched, every day (review of 15.11.2026).
EXTERNAL = {"vivosun_mqtt_receive": ("device_link", "fan_stick_silent"),
            "vivosun_mqtt_keepalive": ("device_link", "fan_stick_silent")}
# Hank's side tasks and answer jobs carry made names (hank/core.py _spawn, the polish job, "hank-jev-watch")
DYNAMIC = ("hank_", "hank-")
# Parts serve() starts in every real run whose failed start is only logged: None in parts is that failure.
REQUIRED = ("grow_plan", "news")
# The task each part signal reads through. While tasks_dead names that task, the part's own silence is its doing, and
# the part's rule would send a second push with the wrong step (gateway and battery for a loop a restart brings back).
# One cause, one push; a dead task elsewhere gates nothing here.
TASK_OF = {"soil_silent": "soil_sensor", "fan_stick_silent": "fan_stick", "ruler_silent": "grow_plan_height",
           "store_silent": "measurements_collect", "news_silent": "news_watch", "pest_watch_silent": "pest_watch"}

# Every device of the device test (device_check.DEVICES) and what watches it: a signal or an older rule.
COVERAGE = {"light": ("light_timer", "device_link", "dark_period", "short_day"), "spectrum": ("device_link",),
            "dfan": ("device_link",), "cfan": ("device_link",), "humidifier": ("device_link", "probe_silent"),
            "pump": ("watering_watchdog", "watering_skips"), "tank": ("tank_sensor_silent", "tank_empty"),
            "soil": ("soil_silent", "soil_uncalibrated"), "fan_stick": ("fan_stick_silent",),
            "growcam": ("camera_offline", "ruler_silent"), "usb_camera": ("usb_camera_offline",),
            "push": ("push_broken",)}


# ---------- the table ----------

@dataclass(frozen=True)
class Signal:
    id: str  # also the name of its rule in alerts.json
    group: str
    reader: Callable[[Ctx], Reading]
    limit: float | None  # seconds; None for a count or a verdict without an age
    basis: str
    parents: tuple[str, ...] = ()
    grace: bool = True  # the start grace applies; False for evidence that is whole at the start (the starts file)


# Parents come before their children: read() goes down the table in this order.
SIGNALS: tuple[Signal, ...] = (
    Signal("poll_stalled", "loop", _poll, float(box_mod.WATER_STALE_SECONDS),
           "box.WATER_STALE_SECONDS: three missed polls of POLL_SECONDS 90"),
    Signal("supervision_failing", "loop", _failing, 3.0 * box_mod.POLL_SECONDS,
           "FAIL_POLLS 3 polls of box.POLL_SECONDS 90 in a row; a broken rule at once"),
    Signal("supervision_silent", "loop", _watcher, WATCH_GAP_S, "3 x WATCH_S 60: three missed watcher ticks"),
    Signal("tasks_dead", "loop", _tasks, 0.0, "no cadence: every listed task is a 'while True' loop"),
    Signal("watering_watchdog", "loop", _pump, float(PUMP_CONFIRM_SECONDS),
           "watering.CONFIRM_SECONDS: the time a pump ON must be confirmed in"),
    Signal("light_timer", "loop", _light, float(LIGHT_CONFIRM_SECONDS),
           "light_schedule.CONFIRM_SECONDS for the loop; the failed edge is the timer's own verdict"),
    Signal("hank_silent", "loop", _hank, 300.0, "5 x hank/core.py TICK_SECONDS 60"),
    Signal("restarts", "loop", _restarts, None,
           "RESTARTS_24H 20 starts in 24 h, a deploy's own not counted (DEPLOY_FILE): own choice", grace=False),
    Signal("device_link", "box", _device_link, 2.0 * box_mod.SHADOW_REFRESH_SECONDS,
           "2 x box.SHADOW_REFRESH_SECONDS 240: one late refresh does not flap"),
    Signal("probe_silent", "box", _climate("probe_silent", ("probe_temp", "probe_humidity")), float(FAILSAFE_SECONDS),
           "automation.FAILSAFE_SECONDS: this long without a probe value"),
    Signal("builtin_silent", "box", _climate("builtin_silent", ("temp", "humidity")), float(FAILSAFE_SECONDS),
           "automation.FAILSAFE_SECONDS, the code's own age for a channel without a value"),
    # behind device_link: without the shadow the accessory type (aux35Dev) is unknown, so no row decodes and the latched
    # sensor would count from the start and blame its plug for the link's cause (review of 15.11.2026)
    Signal("tank_sensor_silent", "box", _tank, 3.0 * box_mod.WATER_STALE_SECONDS,
           "3 x box.WATER_STALE_SECONDS: the poll's 900 s window holds no row after it", parents=("device_link",)),
    Signal("soil_silent", "soil", _soil, float(SOIL_STALE_S), "soil.STALE_S: the tile's own 'not current' mark"),
    Signal("soil_uncalibrated", "soil", _uncalibrated, DAY_S,
           "own choice: a day to water through, drain two hours and set the sensor on the Water & soil page"),
    Signal("thirst_engine", "thirst", _thirst_engine, float(box_mod.WATER_STALE_SECONDS),
           "box.WATER_STALE_SECONDS: three missed polls"),
    Signal("thirst_input", "thirst", _thirst_input, DAY_S,
           "kartei/water_model.json measured_valid_s: after it every slot skips as unmeasured",
           parents=("soil_silent", "soil_uncalibrated")),
    Signal("thirst_sensor_look", "thirst", _look, 3.0 * (SOIL_POLL_S + SENSOR_WATER_LOOK_S),
           "3 x (soil.POLL_S 60 + thirst.SENSOR_WATER_LOOK_S 300)", parents=("soil_silent", "thirst_engine")),
    Signal("watering_skips", "thirst", _skips, None, "SKIPS 2 slots in a row: one is a blip, two a pattern"),
    Signal("push_broken", "push", _push, 10.0 * box_mod.POLL_SECONDS,
           "own choice: ten polls of retries (box.POLL_SECONDS 90) with ntfy's 10 s timeout"),
    Signal("ruler_silent", "parts", _ruler, float(UNREADABLE_PUSH_AFTER_S + HEIGHT_EVERY_S),
           "grow_plan.UNREADABLE_PUSH_AFTER_S 24 h + HEIGHT_EVERY_S 3 h"),
    Signal("fan_stick_silent", "parts", _fan_stick, 3.0 * FAN_REFRESH_S, "3 x fan_stick.REFRESH_S 300"),
    Signal("ollama_silent", "parts", _ollama, 1800.0,
           "own choice: Ollama starts after the login, and a chat answer may take 240 s", parents=("hank_silent",)),
    Signal("pest_watch_silent", "parts", _pest, DAY_S,
           "own choice: hourly while lit, worst case a 12 h dark phase + 1 h + the model's 30 min",
           parents=("ollama_silent",)),
    Signal("store_silent", "parts", _store, 3.0 * COLLECT_SECONDS,
           "3 x measurements.COLLECT_SECONDS 900; light_view.DAY_REFRESH 600 for the measured day"),
    Signal("news_silent", "parts", _news, 2 * DAY_S, "2 x news.STALE_AFTER 24 h: a day of failed hourly retries"),
)
BY_ID = {sig.id: sig for sig in SIGNALS}
WATCHDOG_SIGNALS = ("poll_stalled", "supervision_failing")  # the rules with "driver": "watchdog"


# ---------- the sentences ----------

def age_text(seconds: Any, lang: str | None = None) -> str:
    """An age in plain words, rounded down: 1 Minute, 5 Minuten, 1 Stunde, 3 Stunden, 1 Tag, 9 Tagen (never
    '1 Stunden')."""
    s = max(0.0, _num(seconds) or 0.0)
    if s < 3600:
        n, unit = max(1, int(s // 60)), "minute"
    elif s < DAY_S:
        n, unit = int(s // 3600), "hour"
    else:
        n, unit = int(s // DAY_S), "day"
    return t(f"sup.age.{unit}" + ("" if n == 1 else "s"), lang, n=n)


def _skip_reason(reason: Any, lang: str | None) -> str:
    """A failed slot's reason in words: the gate's own sentence, or the controller's reason."""
    slot = _dict(reason)
    if slot.get("code") == "thirst":
        text = str(_dict(slot.get("detail")).get(i18n.normalize(lang) or i18n.current_lang()) or
                   t("sup.watering_skips.thirst", lang))
    else:
        text = t(f"watering.reason.{slot.get('code')}", lang)
    return text if text.endswith((".", "!", "?")) else text + "."


def _named(kind: str, name: Any, lang: str | None) -> str:
    """A poll step (its __qualname__) or a task in plain words (sup.step.*, sup.task.*): the owner reads no code name.
    One without its words is a part of the server, never its raw name (review of 15.11.2026)."""
    key = f"sup.{kind}.{name}"
    return t(key, lang) if key in DE else t("sup.what.unknown", lang)


def _rule_label(name: Any, labels: Mapping[str, Any], lang: str | None) -> str:
    """A broken rule by its own label in this language (alerts.json label, label_en), never its id."""
    label = localize(_dict(labels.get(str(name))), lang).get("label")
    return label.strip() if isinstance(label, str) and label.strip() else t("sup.what.unnamed", lang)


def _listed(names: list[str]) -> str:
    return ", ".join(dict.fromkeys(names))  # in order and each once: two tasks of one part are one part


def _words(sig: str, params: Mapping[str, Any], lang: str | None) -> dict[str, Any]:
    words: dict[str, Any] = {"age": age_text(params.get("age"), lang)}
    if sig == "poll_stalled":
        phase = params.get("phase")
        words["phase"] = (t("sup.phase.dead", lang) if phase == "dead" else
                          t("sup.phase.step", lang, step=_named("step", phase, lang)) if phase else "")
    elif sig == "supervision_failing":
        what = [pair for pair in params.get("what") or [] if isinstance(pair, list) and len(pair) == 2]
        kind = what[0][0] if what else "read"
        if kind == "rule":
            listed = _listed([_rule_label(name, _dict(params.get("labels")), lang) for k, name in what if k == "rule"])
        else:  # a step or read() itself opens the sentence: "ein Teil des Servers" must not start it in lower case
            listed = _listed([_named("step", name, lang) for k, name in what if k == "step"] if kind == "step"
                             else [t("sup.what.read", lang)])
            listed = listed[:1].upper() + listed[1:]
        words["what"] = listed  # the error class stays in params and the log: it tells the owner nothing
        n = int(_num(params.get("n")) or 0)
        words["polls"] = t("sup.polls.one" if n == 1 else "sup.polls.many", lang, n=n)
    elif sig == "tasks_dead":
        words["names"] = _listed([_named("task", name, lang) for name in params.get("names") or []])
    elif sig == "watering_skips" and params.get("cause") != "storage":
        words["n"] = params.get("n") or SKIPS
        words["reason"] = _skip_reason(params.get("reason"), lang)
    elif sig == "restarts":
        words["n"] = int(_num(params.get("n")) or 0)
    return words


def reading(block: Mapping[str, Any], lang: str | None = None) -> str:
    """The sentence of a signal block (Supervisor.read) in this language: what is wrong now, with its age in plain
    words; for ok the time it was checked. Never an exception text, never a path."""
    sig = str(block.get("signal"))
    state = block.get("state")
    params = _dict(block.get("params"))
    if state == OK:
        return t("sup.back", lang, time=fmt_time(_num(block.get("at")) or time.time(), lang))
    if state == UNKNOWN or sig not in BY_ID:
        return t("sup.unknown", lang)  # its class stays in params and the log
    cause = params.get("cause")
    key = f"sup.{sig}.now.{cause}" if cause and f"sup.{sig}.now.{cause}" in DE else f"sup.{sig}.now"
    if key not in DE:
        return t("sup.unknown", lang)
    return t(key, lang, **_words(sig, params, lang))


def message(block: Mapping[str, Any], hint: Any, lang: str | None = None) -> str:
    """The sentence and, for a silent part, the rule's hint: what it costs, and one plain next step. Never for an
    unknown one: what a silent part costs is not verified there, and its own sentence has the step."""
    text = reading(block, lang)
    return f"{text} {hint}" if hint and block.get("state") == SILENT else text


def unchecked_label(label: Any, lang: str | None = None) -> str:
    """A rule's label for the push of a reader that raised (state unknown, alerts.Alerts._unchecked): it names the part
    and says it could not be checked, so no fault is claimed."""
    return t("sup.unknown.title", lang, label=label)


def _plain(value: Any) -> Any:
    """JSON for the page: numbers rounded, nothing but plain values."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, float):
        return round(value, 1)
    return value if value is None or isinstance(value, str | int | bool) else str(value)


def _day_text(epoch: float, lang: str | None) -> str:
    day = datetime.fromtimestamp(epoch)
    return f"{day:%d.%m.}" if (i18n.normalize(lang) or i18n.current_lang()) == "de" else f"{day.month}/{day.day}"


# ---------- the supervisor ----------

class Supervisor:
    """Every signal of SIGNALS with its state. box: the box (polled_at, loops(), water_seen_at); alerts: the cockpit's
    Alerts (its rules say expect and level; push_health, broken, push); parts: serve()'s dict with every other part
    under its name. clock, sleep and tasks (asyncio.all_tasks) are replaceable for tests."""

    def __init__(self, box: Any, alerts: Any, parts: dict[str, Any], *, clock: Callable[[], float] = time.time,
                 state_path: Path | None = None, sleep: Callable[[float], Any] = asyncio.sleep,
                 tasks: Callable[[], Any] | None = None) -> None:
        self.box, self.alerts, self.parts = box, alerts, parts
        self.clock, self.sleep = clock, sleep
        self._state_path = state_path
        self._all_tasks = tasks or asyncio.all_tasks
        self.started = clock()
        self.grace_until = self.started + GRACE_S
        self.last_ok: dict[str, float] = {}  # the newest proof per signal seen in this process
        self.read_fails = 0  # polls in a row whose read() raised (server.poll_handler counts them)
        self.read_error: str | None = None
        self.steps: dict[str, dict[str, Any]] = {}  # poll step: {"n": failures in a row, "error": the class}
        self.phase: str | None = None  # the poll step running now
        self.round_at: float | None = None  # when the running poll round began; None between rounds
        self.day_ok_at: float | None = None  # the last poll whose measured day could be read
        self.watch_beat: float | None = None  # the start of the watcher's last tick
        self.task: asyncio.Task[None] | None = None
        self.last: dict[str, Any] | None = None  # the newest read()
        self.watched: dict[str, Any] | None = None  # the newest watchdog_snapshot()
        self._logged: dict[str, str] = {}  # signal or step: the error class last logged, so a log line comes once
        self._read: dict[str, tuple[float, Any]] = {}  # the files read seldom: name -> (when, value)
        self._save_failed = False
        self.state = self._load()
        self._starting(self.started)

    # ---------- the file ----------

    def path(self) -> Path:
        return self._state_path if self._state_path is not None else config.STATE_DIR / STATE_FILE

    def _load(self) -> dict[str, Any]:
        """supervision-state.json, every field checked: a broken or missing file is an empty one."""
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as err:
            _LOGGER.warning("Supervision state unreadable (%s), starting over", type(err).__name__)
            data = {}
        data = _dict(data)
        peaks = {sig: {day: float(age) for day, age in days.items() if _num(age) is not None}
                 for sig, days in _dict(data.get("peaks")).items() if sig in BY_ID and isinstance(days, dict)}
        gap = _dict(data.get("last_gap"))
        return {
            "alive_at": _num(data.get("alive_at")),
            "starts": [float(s) for s in data.get("starts") or [] if _num(s) is not None]
            if isinstance(data.get("starts"), list) else [],
            "deploys": [float(s) for s in data.get("deploys") or [] if _num(s) is not None]
            if isinstance(data.get("deploys"), list) else [],
            "seen": {sig: float(at) for sig, at in _dict(data.get("seen")).items() if _num(at) is not None},
            "mutes": {sig: dict(m) for sig, m in _dict(data.get("mutes")).items()
                      if sig in BY_ID and isinstance(m, dict) and _num(m.get("until")) is not None},
            "peaks": peaks,
            "notices": [dict(n) for n in data.get("notices") or [] if isinstance(n, dict)
                        and _num(n.get("from")) is not None and _num(n.get("to")) is not None][-NOTICES_MAX:]
            if isinstance(data.get("notices"), list) else [],
            "last_gap": gap if _num(gap.get("from")) is not None and _num(gap.get("to")) is not None else None,
        }

    def _starting(self, now: float) -> None:
        """This start: counted and shown, a deploy's own start marked (DEPLOY_FILE), so only the others make a restart
        loop (RESTARTS_24H); and a gap since the last sign of life over GAP_S is an outage to say."""
        alive = self.state["alive_at"]
        if alive is not None and now - alive > GAP_S:
            self._away(alive, now)
        self.state["starts"] = [s for s in self.state["starts"] if 0 <= now - s < DAY_S] + [now]
        mark = self._deploy_mark()
        deployed = [now] if mark is not None and 0 <= now - mark <= DEPLOY_WINDOW_S else []
        self.state["deploys"] = [s for s in self.state["deploys"] if 0 <= now - s < DAY_S] + deployed

    def _deploy_mark(self) -> float | None:
        """When scripts/install_mac.sh last stopped the server for a deploy (whole seconds), None without a mark. A
        broken mark is none: it is read in the constructor, and the supervision must never fail to start over it."""
        try:
            text = (self.path().parent / DEPLOY_FILE).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):  # ValueError: bytes that are no UTF-8
            return None
        return float(text) if text.isascii() and text.isdigit() else None

    def starts_24h(self, now: float) -> int:
        return len([s for s in self.state["starts"] if 0 <= now - s < DAY_S])

    def deploys_24h(self, now: float) -> int:
        return len([s for s in self.state["deploys"] if 0 <= now - s < DAY_S])

    def _away(self, start: float, end: float) -> None:
        self.state["notices"] = [*self.state["notices"], {"from": start, "to": end}][-NOTICES_MAX:]
        self.state["last_gap"] = {"from": start, "to": end}

    async def save(self) -> None:
        """supervision-state.json in one step (config.atomic_write), off the event loop, at most SAVE_TIMEOUT_S."""
        text = json.dumps(self.state, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            await asyncio.wait_for(asyncio.to_thread(config.atomic_write, self.path(), text), SAVE_TIMEOUT_S)
            self._save_failed = False
        except Exception as err:  # the state is a help, never a reason to stop
            if not self._save_failed:
                _LOGGER.warning("Supervision state not saved (%s); kept in memory", type(err).__name__)
            self._save_failed = True

    # ---------- what the alarms' rules say ----------

    def _rule(self, sig: str) -> dict[str, Any]:
        rules = _dict(_dict(getattr(self.alerts, "config", None)).get("rules"))
        return _dict(rules.get(sig))

    def expect(self, sig: str) -> str:
        return str(self._rule(sig).get("expect") or "seen")

    def latch(self, sig: str, now: float) -> None:
        self.state["seen"].setdefault(sig, now)

    # ---------- the poll's hooks (server.poll_handler); plain assignments, they never raise ----------

    def poll_started(self) -> None:
        self.round_at, self.phase = self.clock(), None

    def step_started(self, name: str) -> None:
        self.phase = name

    def step_finished(self, name: str, error: BaseException | None = None) -> None:
        """A step's failures in a row (they were only logged before 15.11.2026); a step that runs clears its count."""
        self.phase = None
        if error is None:
            self.steps.pop(name, None)
        else:
            self.steps[name] = {"n": int(_dict(self.steps.get(name)).get("n") or 0) + 1,
                                "error": type(error).__name__}

    def poll_finished(self) -> None:
        self.round_at, self.phase = None, None

    def read_failed(self, error: BaseException) -> None:
        self.read_fails += 1
        self.read_error = type(error).__name__

    # ---------- reading ----------

    def read(self, snap: Mapping[str, Any]) -> dict[str, Any]:
        """Every signal against this poll's snapshot: snap["supervision"] = {"at", "signals": {id: block}}. A reader
        that raises gives "unknown" for its own signal and never stops the others."""
        now = self.clock()
        if isinstance(snap.get("light_day"), dict):
            self.day_ok_at = now
        ctx = Ctx(self, snap, now)
        signals = {sig.id: self._evaluate(ctx, sig) for sig in SIGNALS}
        self.read_fails, self.read_error = 0, None
        self.last = {"at": now, "signals": signals}
        return self.last

    def watchdog_snapshot(self) -> dict[str, Any]:
        """The two signals a dead poll cannot report, as a snapshot of their own for alerts.check(only=...): it holds
        nothing else, and the watchdog's scope keeps "offline" from reading it as an empty box."""
        now = self.clock()
        ctx = Ctx(self, {}, now)
        self.watched = {"at": now, "signals": {sig: self._evaluate(ctx, BY_ID[sig]) for sig in WATCHDOG_SIGNALS}}
        return {alerts_mod.SUPERVISION_METRIC: self.watched}

    def _evaluate(self, ctx: Ctx, sig: Signal) -> dict[str, Any]:
        try:
            found = sig.reader(ctx)
        except Exception as err:  # vault rule 1: what cannot be checked counts as silent, never as fine
            found = Reading(UNKNOWN, None, {"error": type(err).__name__})
            if self._logged.get(sig.id) != type(err).__name__:
                self._logged[sig.id] = type(err).__name__
                _LOGGER.exception("Supervision: the reader of %s failed", sig.id)
        else:
            self._logged.pop(sig.id, None)
        state = found.state
        parents = [ctx.done[p] for p in sig.parents if p in ctx.done]
        if state in (SILENT, UNKNOWN, STARTING) and any(r.state in (SILENT, UNKNOWN) and final != MUTED
                                                         for r, final in parents):
            state = WAITING  # one cause, one push: the parent's rule speaks (a parent the owner muted gates nothing)
        dead = ctx.done.get("tasks_dead")
        if (state in (SILENT, UNKNOWN, STARTING) and dead is not None and dead[0].state == SILENT and dead[1] != MUTED
                and TASK_OF.get(sig.id) in (dead[0].params.get("names") or ())):
            state = WAITING  # its task is dead: tasks_dead speaks, with the step that helps (TASK_OF)
        if state in (SILENT, UNKNOWN) and sig.grace and ctx.now < self.grace_until:
            state = STARTING
        if self._muted(sig.id, ctx.now) is not None:
            state = MUTED
        ctx.done[sig.id] = (found, state)
        if found.state == OK and found.last_ok is not None:
            self.last_ok[sig.id] = max(found.last_ok, self.last_ok.get(sig.id, found.last_ok))
        age = _num(found.params.get("age"))
        if age is not None and found.state in (OK, SILENT):
            self._peak(sig.id, age, ctx.now)
        block = {"signal": sig.id, "state": state, "at": ctx.now, "age": None if age is None else round(age),
                 "limit": sig.limit, "cause": found.params.get("cause"), "params": _plain(found.params)}
        if state != found.state:
            block["found"] = found.state
        return block

    def _peak(self, sig: str, age: float, now: float) -> None:
        """The highest age per day, PEAK_DAYS of them: near misses show before a limit is changed."""
        days = self.state["peaks"].setdefault(sig, {})
        day = datetime.fromtimestamp(now).date().isoformat()
        days[day] = max(age, days.get(day, 0.0))
        for old in sorted(days)[:-PEAK_DAYS]:
            del days[old]

    def peak(self, sig: str) -> float | None:
        days = self.state["peaks"].get(sig) or {}
        return round(max(days.values())) if days else None

    # ---------- the tasks ----------

    def box_loops(self) -> dict[str, Any] | None:
        loops = getattr(self.box, "loops", None)
        return dict(loops()) if callable(loops) else None

    def task_states(self) -> dict[str, str]:
        """Every watched task that should run now: "running", or "dead" (it ended, and nobody cancelled it). A part
        that is not there, or a handle that was never started or was stopped on purpose, is not expected."""
        running = {task.get_name() for task in self._all_tasks() if not task.done()}
        loops = self.box_loops()
        out: dict[str, str] = {}
        for name, (key, attr) in HANDLES.items():
            if key is None:
                out[name] = "running" if name in running else "dead"
                continue
            if key == "box":
                if loops is None:
                    continue
                handle = loops.get(name)
            else:
                owner = self if key == "self" else self.parts.get(key)
                if owner is None:
                    continue
                handle = getattr(owner, attr or "", None)
            if handle is None or not callable(getattr(handle, "done", None)):
                continue
            out[name] = "dead" if handle.done() and not handle.cancelled() else "running"
        return out

    def unwatched(self) -> list[str]:
        """Running tasks with a name nobody watches: a new part without a signal is itself a finding."""
        names = {task.get_name() for task in self._all_tasks() if not task.done()}
        return sorted(name for name in names if name not in HANDLES and name not in TRANSIENT and name not in EXTERNAL
                      and not UNNAMED.fullmatch(name) and not name.startswith(DYNAMIC))

    # ---------- the files read seldom ----------

    def _seldom(self, name: str, read: Callable[[], Any]) -> Any:
        now = self.clock()
        found = self._read.get(name)
        if found is None or not 0 <= now - found[0] < READ_EVERY_S:
            found = (now, read())
            self._read[name] = found
        return found[1]

    def news_success(self) -> float | None:
        """news/state.json: the last successful fetch, else the first run; None without either."""
        def read() -> float | None:
            from . import news  # here: a broken news module costs this signal, never the cockpit

            try:
                data = json.loads(news.state_path().read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, ValueError):
                return None  # a broken file stays where it is: news.load_state() moves it aside, never a reader
            data = _dict(data)
            for key in ("last_success", "first_run"):
                try:
                    return datetime.fromisoformat(str(data[key])).timestamp()
                except (KeyError, TypeError, ValueError):
                    continue
            return None

        return self._seldom("news", read)

    def saved_collect(self, collector: Any) -> float | None:
        """collector.json's last good request, while the collector holds none in memory yet (Collector.loop seeds it
        only after its first wait)."""
        return self._seldom("collect", lambda: _num(_dict(collector.load_state()).get("last_ok")))

    # ---------- the mutes ----------

    def _muted(self, sig: str, now: float) -> dict[str, Any] | None:
        found = self.state["mutes"].get(sig)
        if found is not None and float(found["until"]) <= now:
            del self.state["mutes"][sig]  # it ended: the signal is back
            return None
        return found

    def mute(self, signal: Any, days: Any, why: Any) -> dict[str, Any]:
        """The owner silences one signal for days (1 to MUTE_MAX_DAYS) with his reason; it is listed while it lasts
        and ends by itself. UserError for an unknown signal, other days or no reason. save() writes it."""
        if not isinstance(signal, str) or signal not in BY_ID:
            raise UserError(t("sup.mute.unknown", signal=str(signal)))
        if type(days) is not int or not 1 <= days <= MUTE_MAX_DAYS:
            raise UserError(t("sup.mute.days", max=MUTE_MAX_DAYS))
        reason = str(why or "").strip() if isinstance(why, str) else ""
        if not reason:
            raise UserError(t("sup.mute.why"))
        now = self.clock()
        record = {"until": now + days * DAY_S, "why": reason[:MUTE_WHY_MAX], "at": now, "days": days}
        self.state["mutes"][signal] = record
        return {"signal": signal, **record}

    def unmute(self, signal: Any) -> bool:
        """True when a mute ended."""
        if not isinstance(signal, str) or signal not in BY_ID:
            raise UserError(t("sup.mute.unknown", signal=str(signal)))
        return self.state["mutes"].pop(signal, None) is not None

    # ---------- the watcher ----------

    async def tick(self) -> None:
        """One minute of the watcher: its beat (a gap over WATCH_GAP_S starts the grace again, one over GAP_S is an
        outage, said once), the two rules a dead poll cannot report, the notices still to say, alive_at."""
        now = self.clock()
        before, self.watch_beat = self.watch_beat, now
        if before is not None and now - before > WATCH_GAP_S:
            self.grace_until = max(self.grace_until, now + GRACE_S)
            if now - before > GAP_S:
                self._away(before, now)
        try:
            await asyncio.wait_for(self.alerts.check(self.watchdog_snapshot(), only=self.alerts.watchdog_rules()),
                                   CHECK_TIMEOUT_S)
            self._logged.pop("watchdog", None)
        except Exception as err:  # a hung or broken check costs this tick, never the watcher
            if self._logged.get("watchdog") != type(err).__name__:
                self._logged["watchdog"] = type(err).__name__
                _LOGGER.exception("Supervision: the watchdog's alarm check failed")
        await self._notices()
        self.state["alive_at"] = now
        await self.save()

    async def _notices(self) -> None:
        """The "was away" notices, oldest first, until one is not accepted: tried again every tick, so a Mac back from
        sleep without network still says it once the network is back."""
        while self.state["notices"]:
            notice = self.state["notices"][0]
            lang = i18n.server_lang()  # nobody is asking: the notice speaks the server language
            try:
                ok = await self.alerts.push(t("sup.away.title", lang), self.away_text(notice, lang),
                                            priority=AWAY_PRIORITY)
            except Exception:  # a broken push costs this try
                _LOGGER.exception("Supervision: the notice could not be pushed")
                ok = False
            if not ok:
                return
            self.state["notices"].pop(0)

    def away_text(self, notice: Mapping[str, Any], lang: str | None = None) -> str:
        start, end = float(notice["from"]), float(notice["to"])
        same = datetime.fromtimestamp(start).date() == datetime.fromtimestamp(end).date()
        text = t("sup.away.body" if same else "sup.away.body_days", lang, start=fmt_time(start, lang),
                 end=fmt_time(end, lang), start_day=_day_text(start, lang), end_day=_day_text(end, lang))
        ctl = self.parts.get("watering")
        try:  # missed slots are never caught up (watering.py, line 5)
            missed = bool(_dict(ctl.cfg).get("enabled")) and bool(ctl.slots(start, end)) if ctl is not None else False
        except Exception:  # a broken schedule costs the sentence about it
            missed = False
        return f"{text} {t('sup.away.slots', lang)}" if missed else text

    async def watch(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # the watcher never dies of its own body; the poll reports a dead one
                _LOGGER.exception("Supervision tick failed")
            await self.sleep(WATCH_S)

    def start(self) -> asyncio.Task[None]:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.watch(), name="vgrow_supervision")
        return self.task

    async def stop(self) -> None:
        """Ends the watcher and writes alive_at a last time: a deploy's gap then counts from here."""
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.state["alive_at"] = self.clock()
        await self.save()

    # ---------- the page and the PC ----------

    def view(self, snap: Mapping[str, Any] | None = None, lang: str | None = None) -> dict[str, Any]:
        """For GET /api/supervision and /api/state "supervision". poll and watcher (age and limit; the PC's daily check
        reads them), loops (every watched task: running or dead), signals (state, age, limit, the peak of 14 days, the
        sentence while the reader finds the part silent or cannot check it), active (every active rule with since),
        muted (reason and end), unwatched task names, process (uptime, starts and deploys in 24 h, the last gap), push
        (never the topic), device_tests (ages, no limit) and the free disk (information, no alarm). The signals are the
        newest poll's, the watchdog's two the watcher's."""
        lang = i18n.normalize(lang) or i18n.current_lang()
        now = self.clock()
        blocks = dict(_dict((self.last or {}).get("signals")))
        blocks.update(_dict((self.watched or {}).get("signals")))
        signals = {}
        for sig in SIGNALS:
            block = blocks.get(sig.id)
            if block is None:
                continue
            rule = localize(self._rule(sig.id), lang)
            # The sentence follows what the reader found: a rule stays active while the grace after a start or a silent
            # parent turns its signal into starting or waiting, and its card then showed only the hold time
            found = block.get("found", block["state"])
            text = message({**block, "state": found}, rule.get("hint"), lang) if found in (SILENT, UNKNOWN) else None
            signals[sig.id] = {
                "state": block["state"], "age_s": block.get("age"), "limit_s": sig.limit, "peak_age_s": self.peak(sig.id),
                "cause": block.get("cause"), "at": block.get("at"), "group": sig.group, "label": rule.get("label"),
                "level": rule.get("level", "red"), "text": text}
        rules = _dict(_dict(getattr(self.alerts, "config", None)).get("rules"))
        states = _dict(getattr(self.alerts, "state", None))
        active = [{"rule": name, "kind": _dict(raw).get("kind"), "signal": _dict(raw).get("signal"),
                   "level": _dict(raw).get("level", "red"), "label": localize(_dict(raw), lang).get("label"),
                   "since": _dict(states.get(name)).get("since")}
                  for name, raw in rules.items() if _dict(states.get(name)).get("active")]
        muted = [{"signal": sig, "label": localize(self._rule(sig), lang).get("label"), **_plain(m)}
                 for sig, m in sorted(self.state["mutes"].items()) if float(m["until"]) > now]
        polled = _num(getattr(self.box, "polled_at", None))
        loops = self.task_states()
        loops.update({name: "failed" for name in REQUIRED if name in self.parts and self.parts[name] is None})
        health = self.alerts.push_health() if callable(getattr(self.alerts, "push_health", None)) else {}
        return {
            "at": now, "lang": lang,
            "poll": {"at": polled, "age_s": round(now - (polled if polled is not None else self.started)),
                     "limit_s": BY_ID["poll_stalled"].limit, "phase": self.phase if self.round_at is not None else None},
            "watcher": {"at": self.watch_beat, "age_s": None if self.watch_beat is None else round(now - self.watch_beat),
                        "limit_s": BY_ID["supervision_silent"].limit,
                        "running": self.task is not None and not self.task.done()},
            "loops": loops, "signals": signals, "active": active, "muted": muted, "unwatched": self.unwatched(),
            "process": {"started": self.started, "uptime_s": round(now - self.started),
                        "starts_24h": self.starts_24h(now), "deploys_24h": self.deploys_24h(now),
                        "last_gap": self.state["last_gap"], "notices": len(self.state["notices"])},
            "push": {k: health.get(k) for k in ("target", "failed_since", "failed_at", "ok_at")},
            "device_tests": self._device_tests(now), "disk": {"free_bytes": self._free_bytes()},
        }

    @staticmethod
    def _device_tests(now: float) -> dict[str, dict[str, Any]]:
        """When each device was last tested (device_check): shown, never alarmed; the tests have no cadence."""
        out = {}
        for device, row in device_check.load().items():
            row = _dict(row)
            at = max((v for v in (_num(row.get("at")), _num(row.get("owner_at"))) if v is not None), default=None)
            if at is not None:
                out[device] = {"at": at, "age_s": round(max(0.0, now - at)), "effect": row.get("effect")}
        return out

    @staticmethod
    def _free_bytes() -> int | None:
        """Free disk where the state lives: information for a limit to derive after 14 days, no alarm yet."""
        try:
            return shutil.disk_usage(config.STATE_DIR if config.STATE_DIR.exists() else config.STATE_DIR.parent).free
        except OSError:
            return None
