"""The autopilot: a rule set in code that regulates light, exhaust fan and circulation fan of one box.

Owner's wish (23.10.2026): "Hank auf Autopilot setzen, und er reguliert alles. Oder ich reguliere manuell." Design:
docs/autopilot-entwurf.md, critique: docs/autopilot-kritik.md (conditional go, observation mode first).

Who switches: this module, never the language model. Hank (vgrow/hank/) only reads brief(), which the server puts into
the snapshot his collector reads (server.HankBox); nothing of vgrow/hank/ imports this module, and this module
imports nothing of Hank (tests/test_autopilot.py checks both).

Three modes, default "off":
- off: computes nothing, sends nothing.
- observe ("Beobachten"): computes every rule and logs what it would do ("Autopilot würde: Abluft 5 auf 6, weil ...")
  against a virtual device state, as if the step had been sent, so the log reads like a day of "auto". Sends nothing.
- auto ("Automatik"): sends, for every device whose exception is "auto". A device on "suggest" is logged like in
  observe, a device on "manual" is not touched.
Automatic takes two taps on the page, off and observe one. VGROW_AUTOPILOT=0 in ~/.vgrow.env (or the environment)
forces off at start and refuses switching on. Three failed commands in a row switch it off by itself. Off (and
VGROW_AUTOPILOT=0) touch the autopilot only: the humidifier rule keeps its own switch, and its fail-safe watches a
running humidifier whether that rule is on or not (review 1, M4; review 2, M1).

A command goes out once and is retried once, a moment later, only while everything still holds: Automatik, the
device on "auto", no pause, no plan or VIVOSUN automation. A mode or level change, a restart or a retry older than
RETRY_MAX_AGE never sends it again (review 1, M1 and M2); it is only settled, "ok" or "cancelled".

Manual always wins: a command from the cockpit (the page, an accepted Hank card, "Rückgängig") or a change the box
reports that the autopilot did not send (the VIVOSUN app, the buttons on the box, a change while the server was down)
pauses that device for MANUAL_PAUSE seconds (the server pauses it BEFORE it sends the owner's command, so a poll in
between holds). After the pause it regulates from the value then reported; for the exhaust that value becomes the
new base step, also for a change made while the autopilot was off. It never restores a stored value. A manual light
change ends the light ramp until the owner confirms the lamp distance again; a manual circulation step stays until
the next stage change (the preset applies at a stage change only, never at switching on).

Safety rules (each one a test in tests/test_autopilot.py):
- Readings older than STALE_SECONDS, a gap longer than GAP_RESET_SECONDS (median and hold timers start over), no MQTT
  link, the box offline, or no reported device value: no change. A device whose mode (or, for the light, whether a
  plan runs) the box does not report is never touched, whatever the exceptions say.
- Only with a stage the owner set (set_by "manual"), never an estimate. Harvest: nothing.
- Light: at most +LIGHT_STEP per day (24 h after the last light change, by anyone), only with the lights on, never from
  0, never above the stage preset, held while the probe temperature is less than 1 °C below the day maximum or the VPD
  is above its band, a one-time owner confirmation of the lamp distance (again after a manual light change or 10 cm of
  growth). No lowering to the preset; only its own last increase goes back when it gets too warm (a safety step).
- Mist lock: probe humidity more than MIST_GAP points above the box sensor, or no box sensor value at all: humidity
  and VPD do not drive the fans. Mist minutes add up over 24 hours; one push at MIST_PUSH_AFTER.
- "The exhaust does not work here" (two steps, no effect) is for humidity and VPD only, and a resting rule never
  holds back an urgent (rank 1) temperature vote (review 2, M2).
- VPD too high (23.10.2026, measured: probe 25.06 °C, 45.5 %, 1.73 kPa against 1.0 to 1.4): one step down on the
  exhaust keeps the moisture in. The lowest rank, like "VPD too low"; only with the lights on and no mist, only while
  the probe temperature stays at least VPD_HIGH_HEADROOM below the day maximum and the humidity is not above its band,
  so it never fights "too warm" or "too humid", and not within COLD_HUMID_WINDOW after a "too warm" vote, so the two
  do not flip the exhaust at the day maximum; never below the flower minimum (_bounds).
- Account-wide command history (vgrow/budget.py), without fixed hourly or daily command-count quotas.
- Port guard (vgrow/portguard.py): a changed port assignment pauses all automation until the owner confirms.
- Writing a fan or light level sets the box mode to manual (mode 0) and can end the owner's light plan or VIVOSUN's
  own exhaust automation: a device in plan or auto mode is not touched unless the owner allowed it in the exceptions.

State: STATE_DIR/boxes/<box id>/autopilot-state.json (atomic), the step log next to it (autopilot-log.jsonl, epoch
seconds). Nothing is written into the repository. The box id is in every path, log line and command from the start.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import statistics
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from . import config
from .box import CommandRefused
from .budget import BUDGET, SAFETY_ALARM_PER_HOUR, BudgetExceeded, CommandBudget
from .i18n import fmt_qty, fmt_time, localize, t
from .plant import PlantLog, current_stage, load_profile, profile_problem
from .portguard import PortGuard

_LOGGER = logging.getLogger(__name__)

MODES = ("off", "observe", "auto")
LEVELS = ("auto", "suggest", "manual")
DEVICES = ("light", "dfan", "cfan")
DEFAULT_BOX = "box-1"
FLOWER = ("early_flower", "mid_flower", "late_flower")

# ---- data ----
STALE_SECONDS = 600  # older readings: no change for any device
GAP_RESET_SECONDS = 300  # a gap between two readings longer than this: median and hold timers start over
NEW_READING_SECONDS = 20  # a reading at most this much newer than the last one is the same reading again
SMOOTH_N = 5  # median of the last five readings
MIN_READINGS = 3
PERSIST_SECONDS = 600  # a condition must hold 10 minutes before it acts
WARMUP_CHECKS = 2  # the first two polls after a start send nothing (the shadow may not be there yet)

# ---- margins (design 2.1, critique M4: inner margin three times the outer one until the effect is measured) ----
OUT_MARGIN = {"probe_temp": 0.5, "probe_humidity": 2.0, "probe_vpd": 0.05}
IN_MARGIN = {"probe_temp": 1.5, "probe_humidity": 6.0, "probe_vpd": 0.15}
DEADBAND = {"probe_temp": 1.0, "probe_humidity": 4.0, "probe_vpd": 0.10}  # "did it move" after two steps
HARD_TEMP = 2.0  # degrees outside the band: temperature outranks everything (own choice, no source)
VPD_HIGH_HEADROOM = 1.0  # "VPD too high" votes only this far below the day maximum (as the light ramp, own choice)
MIST_GAP = 20.0  # probe humidity this far above the box sensor: mist from the humidifier (one recorded day)
MIST_PUSH_AFTER = 2 * 3600  # mist minutes in the last 24 h, added up over every episode (review 2, S3) ...
MIST_LOG_EVERY = 6 * 3600  # ... and a mist start or end is logged at most once per 6 hours

# ---- exhaust fan ----
DFAN_SPAN = 3  # base step plus or minus three ...
DFAN_MIN, DFAN_MAX = 2, 8  # ... never outside 2..8, never 0
DFAN_FLOWER_MIN = 4  # in flower never below 4 (50 %, const.py DFAN_LEVEL_MAP; critique S6)
DFAN_SAME_GAP = 30 * 60
DFAN_REVERSE_GAP = 90 * 60
RETURN_AFTER = 90 * 60  # all readings settled this long: one step back toward the base step, once per light period
COLD_HUMID_WINDOW = 3 * 3600  # "too humid" and "too cold" within 3 h: hold instead of flipping at the 2 degree line
WINDUP_REST = 6 * 3600  # the exhaust did not move a reading after two steps: that reading rests 6 h ...
WINDUP_MEMORY = 48 * 3600
WINDUP_REST_AGAIN = 24 * 3600  # ... and 24 h when it happens again within 48 h (the closed-loop toy box: a weak
# exhaust against lasting humidity would otherwise retry the same three steps every 6 to 8 hours)
AT_BOUND_REPORT = 30 * 60

# ---- light ----
LIGHT_STEP = 25
LIGHT_WAIT = 24 * 3600
LIGHT_WINDOW = (3600, 6 * 3600)  # 1 to 6 hours after lights on, when the lights-on moment is known
LIGHT_HEADROOM = 1.0  # probe temperature at least this far below the day maximum ...
LIGHT_HEADROOM_HOLD = 30 * 60  # ... for 30 minutes
LIGHT_QUIET = 3600  # no other autopilot step in the last hour, so every effect belongs to one step
LIGHT_HEAT_MARGIN, LIGHT_HEAT_HOLD = 1.5, 20 * 60  # after its own increase: back if this much too warm for 20 min
LAMP_MIN_CM = 35  # general span for 100 to 300 W LEDs (autoflower.json, early_flower preset_note), not VGrow's own
LAMP_REGROW_CM = 10

# ---- commands ----
MANUAL_PAUSE = 60 * 60
CONFIRM_WAIT = 15  # box.PENDING_SECONDS: the box confirms within 15 s or not at all
RETRY_MAX_AGE = 3 * CONFIRM_WAIT + 2 * 90  # a retry only ever repeats a decision this young (two polls and a margin)
FAIL_PAUSE = 60 * 60
FAILS_PER_DAY = 3  # failures of one device in a day: that device goes to "manual"
FAILS_IN_ROW = 3  # failures in a row over all devices: the autopilot switches itself off
UNDO_WINDOW = 30 * 60
UNDO_BLOCK = 12 * 3600  # an undone rule and direction wait for the next lights on, at most this long
LOCK_STALE = 5 * 60  # another server's heartbeat older than this: its lock is gone
RECENT = 20
VOLATILE = ("stats", "cold_humid")  # saved with the 10-minute cadence only
# The step bookkeeping exists twice: once for the real devices, once ("v_" + key) for what observe and suggest only
# computed. A would-be step never throttles, ramps or streaks a real device, and switching the mode wipes only the
# virtual half (review 1, S5; review 2, S5).
SPLIT = ("history", "last_step", "streak", "rest_until", "windups", "returns")
LOG_KINDS = ("step", "would", "suggest", "manual", "undo", "failed", "hold")  # what the card's short log lists


# ---------- texts ----------

def q(value: Any, unit: str = "", digits: int | None = None) -> dict[str, Any]:
    """A number for a text, formatted in the reader's language when the text is shown."""
    return {"q": [value, unit, digits]}


def tk(key: str) -> dict[str, Any]:
    return {"t": key}


def text(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def _param(value: Any, lang: str | None) -> Any:
    if isinstance(value, dict):
        if "q" in value:
            number, unit, digits = value["q"]
            return fmt_qty(number, unit, lang, digits=digits)
        if "t" in value:
            return t(value["t"], lang)
        if "at" in value:
            return fmt_time(value["at"], lang)
        if "tw" in value:
            return (value["tw"] or {}).get(lang or "de") or (value["tw"] or {}).get("de") or ""
        if "key" in value:
            return render(value, lang)
    return value


def render(entry_text: dict[str, Any] | None, lang: str | None = None) -> str:
    if not isinstance(entry_text, dict) or "key" not in entry_text:
        return ""
    return t(entry_text["key"], lang, **{k: _param(v, lang) for k, v in (entry_text.get("params") or {}).items()})


DEVICE_TEXT = {"light": "ap.dev.light", "dfan": "ap.dev.dfan", "cfan": "ap.dev.cfan"}
METRIC_TEXT = {"probe_temp": "ap.metric.probe_temp", "probe_humidity": "ap.metric.probe_humidity",
               "probe_vpd": "ap.metric.probe_vpd"}
UNIT = {"probe_temp": ("°C", 1), "probe_humidity": ("%", 1), "probe_vpd": ("kPa", 2)}
# What is left once the exhaust is at its limit, by the rule that wanted the next step. Never the card's "exhaust fan
# up": the autopilot already did that (owner, 07.11.2026: "it's on auto, he should do it himself, not tell me").
BOUND_TEXT = {"temp_high": "ap.bound.temp_high", "temp_low": "ap.bound.temp_low", "hum_high": "ap.bound.humid",
              "vpd_low": "ap.bound.humid", "vpd_high": "ap.bound.dry"}


def value_param(device: str, value: Any) -> dict[str, Any]:
    return q(value, "%") if device == "light" else q(value)


def reading(metric: str, value: float) -> dict[str, Any]:
    unit, digits = UNIT[metric]
    return q(value, unit, digits)


# ---------- the stage card ----------

def stage_confirmed(stage: dict[str, Any] | None) -> bool:
    return bool(stage) and (stage or {}).get("set_by") == "manual"


def bands(stage: dict[str, Any] | None) -> dict[str, Any]:
    """{metric or metric_when: target} from the stage card."""
    out: dict[str, Any] = {}
    for target in (stage or {}).get("targets", []):
        if isinstance(target, dict) and isinstance(target.get("min"), int | float) and isinstance(
                target.get("max"), int | float):
            out[target["metric"] + ("_" + target["when"] if target.get("when") else "")] = target
    return out


def temp_band(tg: dict[str, Any], lights: bool | None) -> tuple[float, float] | None:
    """Day or night band by the lights; unknown lights: the outer band over both (rank 1 only, critique M2)."""
    day, night = tg.get("probe_temp_day") or tg.get("probe_temp"), tg.get("probe_temp_night")
    if lights is True and day:
        return day["min"], day["max"]
    if lights is False:
        chosen = night or day
        return (chosen["min"], chosen["max"]) if chosen else None
    both = [b for b in (day, night) if b]
    return (min(b["min"] for b in both), max(b["max"] for b in both)) if both else None


def env_forced_off() -> bool:
    """VGROW_AUTOPILOT=0 in ~/.vgrow.env or the environment: the autopilot stays off (the SSH-only way out)."""
    try:
        value = os.environ.get("VGROW_AUTOPILOT") or config.read_env(config.ENV_FILE).get("VGROW_AUTOPILOT", "")
    except (OSError, UnicodeDecodeError):
        return False
    return str(value).strip() == "0"


def _alive(owner: str) -> bool:
    """Whether the server that holds the lock still runs. Only answerable on POSIX for this host (signal 0 tests a
    pid without touching it); elsewhere, and for another host, the heartbeat's age decides alone. Never os.kill on
    Windows: there it would end the process."""
    host, _, rest = owner.partition(":")
    pid = rest.partition(":")[0]
    if os.name != "posix" or host != socket.gethostname() or not pid.isdigit():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


# ---------- one box ----------

class Autopilot:
    def __init__(self, box: Any, *, box_id: str = DEFAULT_BOX, name: str | None = None, alerts: Any = None,
                 plant_log: PlantLog | None = None, humidifier: Any = None, budget: CommandBudget | None = None,
                 guard: PortGuard | None = None, clock: Callable[[], float] = time.time,
                 stage_source: Callable[[], tuple[str | None, dict[str, Any] | None]] | None = None) -> None:
        self.box = box
        self.box_id = box_id
        self.name = name
        self.alerts = alerts
        self.plant_log = plant_log or PlantLog()
        self.humidifier = humidifier
        self.budget = budget or BUDGET
        self.light_schedule: Any = None  # daily timing has its own switch and owns brightness while enabled
        self.guard = guard or PortGuard(box_id)
        self._clock = clock
        self._stage_source = stage_source
        self.dir = config.STATE_DIR / "boxes" / box_id
        self.state_path = self.dir / "autopilot-state.json"
        self.log_path = self.dir / "autopilot-log.jsonl"
        self.lock_path = self.dir / "autopilot.lock"
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._seq = 0
        self._saved_key = ""
        self._saved_at = 0.0
        self._beat_at = 0.0
        self.state = self._load()
        # A command still unconfirmed when the server stopped is never sent again: after a restart it may be hours
        # old (review 1, M2). It is only settled: confirmed if the box reports its target, else cancelled.
        self.state["in_flight"] = {d: f for d, f in self.state["in_flight"].items() if isinstance(f, dict)}
        for flight in self.state["in_flight"].values():
            flight.update(cancelled="restart", failed=False)
        self.forced_off = env_forced_off()
        if self.forced_off and self.state["mode"] != "off":
            self.state.update(mode="off", mode_since=self._clock())
            self._log({"kind": "event", "text": text("ap.ev.forced_off")})
            self._save()
        # transient, rebuilt after a restart: the median window and the hold timers
        self.window: list[tuple[float, dict[str, float | None]]] = []
        self.since: dict[str, float | None] = {}
        self.checks = 0
        self.holds: dict[str, dict[str, Any]] = {}
        self.status: dict[str, Any] = text("ap.hold.off")
        self.locked_out = False
        self._seq = 0
        self._pushes: list[tuple[Any, ...]] = []  # (title, body, priority[, params]): sent at the end of a poll
        self._stage_now: tuple[str | None, dict[str, Any] | None] | None = None
        self._virtual = False  # whether the device being decided runs virtually (observe, suggest)
        self._acted = False  # one ordinary step per poll and box, so every effect belongs to one step

    # ---------- state ----------

    def _defaults(self) -> dict[str, Any]:
        return {
            "box": self.box_id, "mode": "off", "mode_since": None,
            "devices": {d: {"level": "auto", "app": False} for d in DEVICES},
            "paused": {}, "pause_why": {}, "base": None, "virtual": {}, "seen": {}, "expect": {}, "in_flight": {},
            "history": {d: [] for d in DEVICES}, "last_step": {}, "streak": None, "rest_until": {},
            "returns": {"period": 0, "count": 0}, "settled_since": None, "windups": {},
            **self._virtual_books(),
            "lights": {"on": None, "edge_at": None, "first_on_at": None, "period": 0},
            "light": {"last_change_at": None, "series_stopped": False, "own_up": None, "auto_up_at": None},
            "lamp": None, "cfan_stage": None, "cfan_stage_virtual": None, "fails": {}, "fails_in_row": 0,
            "undo_block": {}, "undone": [],
            "said": {}, "cold_humid": {"hum_at": None, "cold_at": None, "temp_high_at": None}, "at_bound_since": None,
            "mist_since": None, "mist_spans": [], "mist_logged_at": None, "mist_logged": False,
            "stats": None, "last_summary": None,
        }

    @staticmethod
    def _virtual_books() -> dict[str, Any]:
        return {"v_history": {d: [] for d in DEVICES}, "v_last_step": {}, "v_streak": None, "v_rest_until": {},
                "v_windups": {}, "v_returns": {"period": 0, "count": 0}}

    def _k(self, key: str, virtual: bool | None = None) -> str:
        """The state key of a piece of step bookkeeping: the real one, or its virtual twin for observe and suggest."""
        virtual = self._virtual if virtual is None else virtual
        return f"v_{key}" if virtual and key in SPLIT else key

    def _load(self) -> dict[str, Any]:
        base = self._defaults()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            return base
        except (OSError, ValueError) as err:  # unreadable: off, as the humidifier rule does
            _LOGGER.error("Autopilot state %s unreadable (%s): off", self.state_path, err)
            return base
        merged = {**base, **data}
        for key in ("devices",):
            merged[key] = {d: {**base[key][d], **((data.get(key) or {}).get(d) or {})} for d in DEVICES}
        for key in ("history", "v_history"):
            merged[key] = {d: list((data.get(key) or {}).get(d) or []) for d in DEVICES}
        merged["light"] = {**base["light"], **(data.get("light") or {})}
        if not isinstance(merged.get("in_flight"), dict):
            merged["in_flight"] = {}
        if merged.get("mode") not in MODES:
            merged["mode"] = "off"
        return merged

    def _save(self) -> None:
        try:
            config.atomic_write(self.state_path, json.dumps(self.state, indent=1, ensure_ascii=False) + "\n")
        except OSError as err:
            _LOGGER.warning("Autopilot state not saved: %s", err)
        self._saved_key = self._state_key()

    def _state_key(self) -> str:
        """What must reach the disk at once; the day's counters and the vote clocks ride along every 10 minutes."""
        return json.dumps({k: v for k, v in self.state.items() if k not in VOLATILE}, sort_keys=True, default=str)

    def _save_poll(self, now: float) -> None:
        """The save at the end of a poll: at once when anything but the day's counters changed, else every 10 min."""
        if self._state_key() == self._saved_key and now - self._saved_at < 600:
            return
        self._save()
        self._saved_at = now

    def _log(self, entry: dict[str, Any], at: float | None = None) -> dict[str, Any]:
        now = self._clock() if at is None else at
        self._seq += 1
        line = {"id": f"{self.box_id}-{int(now * 1000)}-{self._seq}", "at": now, "box": self.box_id,
                "mode": self.state.get("mode"), **entry}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as err:
            _LOGGER.warning("Autopilot log not written: %s", err)
        return line

    def entries(self, limit: int = 400) -> list[dict[str, Any]]:
        """The newest log lines, oldest first (the file only grows, so only its tail is read)."""
        try:
            with self.log_path.open("rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 262144))
                lines = f.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # the first line of the tail may be cut
            if isinstance(entry, dict) and isinstance(entry.get("at"), int | float):
                out.append(entry)
        return out[-limit:]

    def _update_entry(self, entry_id: str, **fields: Any) -> None:
        """A later outcome of a step (confirmed, failed, undone) as its own log line that names the step."""
        self._log({"kind": "outcome", "of": entry_id, **fields})

    # ---------- the lock: two servers never regulate one box ----------

    def _heartbeat(self, now: float) -> bool:
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        holder, beat = data.get("owner"), data.get("at")
        if (holder and holder != self.owner and isinstance(beat, int | float) and 0 <= now - beat < LOCK_STALE
                and _alive(str(holder))):
            self.locked_out = True
            return False
        self.locked_out = False
        if holder == self.owner and 0 <= now - self._beat_at < LOCK_STALE / 2:
            return True  # the heartbeat is written every 2.5 minutes, read on every poll
        try:
            config.atomic_write(self.lock_path, json.dumps({"owner": self.owner, "at": now}) + "\n")
            self._beat_at = now
        except OSError as err:
            _LOGGER.warning("Autopilot lock not written: %s", err)
        return True

    def release(self) -> None:
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if data.get("owner") == self.owner:
                self.lock_path.unlink()
        except (OSError, ValueError):
            pass

    # ---------- the owner's controls ----------

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError(mode)
        if mode != "off" and self.forced_off:
            raise CommandRefused(t("ap.refused.forced_off"))
        now = self._clock()
        before = self.state["mode"]
        if mode == before:
            return self.state
        snap = self.box.snapshot()
        off_since = self.state.get("mode_since") if before == "off" else None
        # A command still on its way belongs to the mode that sent it: never retried in another one (review 1, M1).
        for device in list(self.state["in_flight"]):
            self._cancel_flight(device, "mode")
        self.state.update(mode=mode, mode_since=now, fails_in_row=0)
        # Only the virtual half is wiped: what observe and suggest computed. The real history, the rhythm and the
        # safety step that takes back the autopilot's own light increase stay (review 1, S5; review 2, S5).
        self.state.update(virtual={}, cfan_stage_virtual=None, **self._virtual_books())
        self.state["light"].pop("virtual_at", None)
        self.state["light"].pop("v_own_up", None)
        if before == "observe" or mode == "observe":
            self.state["settled_since"] = None
        if before == "off":
            # The exhaust's base step stays, unless there is none yet or the autopilot was off for more than a day
            # (a change the owner made while it was off already became the base, see _detect_external).
            if self.state.get("base") is None or off_since is None or now - off_since > 86400:
                self.state["base"] = (snap.get("dfan") or {}).get("step")
                self.state["streak"] = None
            # The circulation step the owner runs now is his choice: the preset applies at the next stage change
            # only, never at switching on (review 2, S6).
            _, stage = self.stage()
            self.state["cfan_stage"] = self.state["cfan_stage_virtual"] = (stage or {}).get("key")
            self.state["settled_since"] = None
            self.since.clear()
        self._log({"kind": "mode", "text": text(f"ap.ev.mode_{mode}"), "from_mode": before, "to_mode": mode})
        self.status = text("ap.hold.off" if mode == "off" else "ap.hold.next_poll")
        # Off touches the autopilot only. The humidifier rule has its own switch (its row in the exceptions), and its
        # fail-safe watches a running humidifier whether that rule is on or off (review 1, M4; review 2, M1).
        self._save()
        return self.state

    def _cancel_flight(self, device: str, why: str) -> None:
        """A command still waiting for the box's confirmation is never sent again. It stays only to be settled: "ok"
        if the box reports its target within RETRY_MAX_AGE, else "cancelled" (_settle_in_flight)."""
        flight = self.state["in_flight"].get(device)
        if isinstance(flight, dict):
            flight["cancelled"] = why
            flight["failed"] = False

    def set_device(self, device: str, level: str | None = None, app: bool | None = None) -> None:
        if device not in DEVICES or (level is not None and level not in LEVELS):
            raise ValueError(device)
        cfg = self.state["devices"][device]
        if level is not None:
            cfg["level"] = level
        if app is not None:
            cfg["app"] = bool(app)
        if cfg["level"] != "auto":
            self._cancel_flight(device, "level")  # suggest or manual: nothing of it is sent again (review 1, M1)
        self.state["virtual"].pop(device, None)
        self._log({"kind": "event", "device": device,
                   "text": text("ap.ev.level", device=tk(DEVICE_TEXT[device]), level=tk(f"ap.level.{cfg['level']}"))})
        self._save()

    def confirm_lamp(self) -> None:
        """The owner measured the lamp distance (at least 35 cm): the ramp may raise the light, from now on."""
        heights = self.plant_log.data.get("heights") or []
        cm = heights[-1].get("cm") if heights and isinstance(heights[-1], dict) else None
        self.state["lamp"] = {"at": self._clock(), "height_cm": cm}
        self.state["light"]["series_stopped"] = False
        self._log({"kind": "event", "device": "light", "text": text("ap.ev.lamp", cm=q(LAMP_MIN_CM, "cm"))})
        self._save()

    def manual(self, device: str, target: Any = None, source: str = "cockpit") -> None:
        """A command of the owner (the page, a Hank card, the undo button): this device pauses MANUAL_PAUSE seconds."""
        if device not in DEVICES:
            return
        now = self._clock()
        until = now + MANUAL_PAUSE
        self.state["paused"][device] = until
        self.state["pause_why"][device] = source
        flight = self.state["in_flight"].pop(device, None)
        if isinstance(flight, dict) and flight.get("entry"):
            self._update_entry(flight["entry"], status="cancelled", why="manual")
        self.state["virtual"].pop(device, None)
        if target is not None:
            self.state["expect"][device] = {"target": target, "until": now + 600}
        self._owner_took(device)
        if self.state["mode"] != "off" and source != "undo":
            self._log({"kind": "manual", "device": device, "to": target, "source": source,
                       "text": text("ap.ev.manual_cockpit", device=tk(DEVICE_TEXT[device]), until={"at": until})})
        self._save()

    def _owner_took(self, device: str) -> None:
        """What a change by the owner (cockpit, app, buttons) means beyond the pause: the light ramp ends, the exhaust
        streak starts over, and the circulation step the owner set is the stage's step from now on, so the preset never
        sets it back after the pause (review 1, S4)."""
        if device == "light":
            light = self.state["light"]
            light.update(series_stopped=True, own_up=None, last_change_at=self._clock())
            light.pop("v_own_up", None)
        elif device == "dfan":
            self.state["streak"] = self.state["v_streak"] = None
        elif device == "cfan":
            _, stage = self.stage()
            self.state["cfan_stage"] = self.state["cfan_stage_virtual"] = (stage or {}).get("key")

    async def undo(self, entry_id: str) -> dict[str, Any]:
        """Send the old value of a step as a manual command (it pauses the device), valid UNDO_WINDOW seconds."""
        entry = self._undoable().get(entry_id)
        if entry is None:
            raise CommandRefused(t("ap.refused.undo_gone"))
        device, old = entry["device"], entry["from"]
        call = {"light": lambda: self.box.set_light(int(old)), "dfan": lambda: self.box.set_dfan(int(old)),
                "cfan": lambda: self.box.set_cfan(int(old))}[device]
        await self.budget.send("manual", f"undo.{device}", call, box=self.box_id)
        now = self._clock()
        self.manual(device, old, source="undo")
        direction = 1 if (entry["to"] - entry["from"]) > 0 else -1
        self.state["undo_block"][device] = {"dir": direction, "until": now + UNDO_BLOCK}
        self.state["undone"] = [*self.state.get("undone", [])[-50:], entry_id]
        self._log({"kind": "undo", "device": device, "of": entry_id, "from": entry["to"], "to": old,
                   "text": text("ap.ev.undo", device=tk(DEVICE_TEXT[device]), value=value_param(device, old),
                                until={"at": self.state["paused"][device]})})
        self._save()
        return {"device": device, "to": old}

    def _undoable(self) -> dict[str, dict[str, Any]]:
        now = self._clock()
        snap = self.box.snapshot()
        outcomes = {e.get("of"): e for e in self.entries() if e.get("kind") == "outcome"}
        out = {}
        for e in self.entries():
            if e.get("kind") != "step" or now - e["at"] > UNDO_WINDOW or e["id"] in self.state.get("undone", []):
                continue
            if (outcomes.get(e["id"]) or {}).get("status") == "failed":
                continue
            if self._real(snap, e["device"]) != e["to"]:
                continue  # the situation has moved on: the old value is no longer the undo
            out[e["id"]] = e
        return out

    # ---------- reading the box ----------

    @staticmethod
    def _real(snap: dict[str, Any], device: str) -> Any:
        if device == "light":
            return (snap.get("light") or {}).get("level")
        if device == "dfan":
            return (snap.get("dfan") or {}).get("step")
        return (snap.get("cfan") or {}).get("step")

    def _current(self, snap: dict[str, Any], device: str, virtual: bool) -> Any:
        real = self._real(snap, device)
        if virtual:
            return self.state["virtual"].get(device, real)
        return real

    @staticmethod
    def _app_mode(snap: dict[str, Any], device: str) -> str | None:
        """Why the box runs this device on its own: "plan" (light plan), "auto" (VIVOSUN automation), "wind", or
        "unknown" when the box does not report the mode (or, for the light, whether a plan runs). Unknown is never
        taken as manual: a write sets mode 0 and could end a plan or VIVOSUN's own automation (review 1, M3)."""
        if device == "light":
            light = snap.get("light") or {}
            if light.get("in_plan"):
                return "plan"
            if light.get("mode") is None or light.get("in_plan") is None or light.get("in_plan_known") is False:
                return "unknown"
            return None if light.get("mode") == 0 else "auto"
        if device == "dfan":
            mode = (snap.get("dfan") or {}).get("mode")
            return "unknown" if mode is None else None if mode == 0 else "auto"
        cfan = snap.get("cfan") or {}
        if cfan.get("natural_wind"):
            return "wind"
        mode = cfan.get("mode")
        return "unknown" if mode is None else None if mode == 0 else "auto"

    def stage(self) -> tuple[str | None, dict[str, Any] | None]:
        if self._stage_now is not None:
            return self._stage_now  # read once per poll
        if self._stage_source is not None:
            return self._stage_source()
        profile = load_profile()
        if profile is None and profile_problem():
            return "profile", None
        stage = current_stage(profile or {}, self.plant_log)
        return (None if stage_confirmed(stage) else "stage"), stage

    def _paused(self, device: str, now: float) -> float | None:
        until = self.state["paused"].get(device)
        return until if until and now < until else None

    # ---------- the poll ----------

    async def check(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        """Run after every sensor poll. Returns the log entries it wrote for steps (would-be and real)."""
        now = self._clock()
        if not self._heartbeat(now):
            self.status = text("ap.hold.locked")
            return []
        self._stage_now = None
        self._stage_now = self.stage()
        self._acted = False
        try:
            return await self._check(snap, now)
        finally:
            self._stage_now = None
            pushes, self._pushes = self._pushes, []
            for title, body, priority, *params in pushes:
                await self._push(title, body, priority, **(params[0] if params else {}))

    async def _check(self, snap: dict[str, Any], now: float) -> list[dict[str, Any]]:
        self.checks += 1
        self._observe_lights(snap, now)
        self._end_pauses(snap, now)
        self._settle_in_flight(snap, now)
        self._detect_external(snap, now)
        values = self._readings(snap, now)
        self._stats(values, snap, now)
        self._summary(now)
        out: list[dict[str, Any]] = []
        mode = self.state["mode"]
        self.holds = {}
        if mode == "off":
            self.status = text("ap.hold.forced_off" if self.forced_off else "ap.hold.off")
            self._save_poll(now)
            return out
        blocked = self._global_block(snap, values, now)
        if blocked is not None:
            self.status = blocked
            self._save_poll(now)
            return out
        kind, stage = self.stage()
        tg = bands(stage)
        for device, flight in list(self.state["in_flight"].items()):
            if flight.get("failed"):
                await self._retry(device, flight, now)
        intents = []
        for device in DEVICES:
            cfg = self.state["devices"][device]
            if cfg["level"] == "manual":
                self.holds[device] = text("ap.hold.manual_level")
                continue
            virtual = mode == "observe" or cfg["level"] == "suggest"
            hold = self._device_block(snap, device, virtual, now)
            if hold is not None:
                self.holds[device] = hold
                if device == "cfan" and hold["key"] == "ap.hold.natural_wind":
                    self.state["cfan_stage"] = (stage or {}).get("key")  # once per stage: natural wind wins it
                continue
            cur = self._current(snap, device, virtual)
            decide = {"light": self._decide_light, "dfan": self._decide_dfan, "cfan": self._decide_cfan}[device]
            self._virtual = virtual
            intent = decide(cur, values, tg, stage or {}, now)
            if isinstance(intent, dict) and "to" in intent:
                intent.update(device=device, virtual=virtual, frm=cur)
                intents.append(intent)
            elif intent is not None:
                self.holds[device] = intent
            else:
                self.holds[device] = text("ap.hold.on_target")
        for intent in sorted(intents, key=lambda i: (not i.get("safety"), DEVICES.index(i["device"]))):
            entry = await self._act(intent, snap, now)
            if entry is not None:
                out.append(entry)
        regulating = sum(1 for d in DEVICES if self.state["devices"][d]["level"] == "auto"
                         or (mode == "observe" and self.state["devices"][d]["level"] == "suggest"))
        hum_on = self.humidifier is not None and bool(self.humidifier.state.get("enabled"))
        key = "ap.status.observe" if mode == "observe" else "ap.status.auto"
        # In observe the humidifier rule may still switch for real: the status line says so (review 1, N4).
        self.status = text(key + ("_hum" if hum_on and mode == "observe" else ""), n=regulating, total=len(DEVICES))
        self._save_poll(now)
        return out

    def _global_block(self, snap: dict[str, Any], values: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
        """Why no device may change on this poll, or None."""
        if self.checks <= WARMUP_CHECKS:
            return text("ap.hold.warmup")
        port = self.guard.status(snap)
        if port == "changed":
            self._say_once("ports", text("ap.ev.ports_changed"), push=("ap.push.ports_title", "ap.push.ports_body", 4))
            return text("ap.hold.ports")
        self.state["said"].pop("ports", None)
        kind, stage = self.stage()
        if kind == "profile":
            return text("ap.hold.profile", error=profile_problem() or "?")
        if kind == "stage":
            return text("ap.hold.stage")
        if (stage or {}).get("key") == "harvest" or not (stage or {}).get("targets"):
            return text("ap.hold.harvest")
        if not snap.get("mqtt", True) or snap.get("online") is False:
            return text("ap.hold.no_link")
        age = snap.get("sensors_age")
        if age is None or age > STALE_SECONDS:
            self._say_once("stale", text("ap.ev.stale"))
            return text("ap.hold.stale")
        self.state["said"].pop("stale", None)
        if values is None:
            return text("ap.hold.gathering")
        counts = self.budget.counts(now)
        if counts["safety_hour"] > SAFETY_ALARM_PER_HOUR and self.state["mode"] == "auto":
            self._shutdown(now, "ap.ev.safety_flood")
            self._pushes.append(("ap.push.off_title", "ap.push.flood_body", 4))  # review 2, N1
            return text("ap.hold.off")
        return None

    def _device_block(self, snap: dict[str, Any], device: str, virtual: bool, now: float) -> dict[str, Any] | None:
        if device == "light" and self.light_schedule is not None and self.light_schedule.cfg["enabled"]:
            return text("ap.hold.light_schedule")
        until = self._paused(device, now)
        if until is not None:
            why = self.state["pause_why"].get(device) or "manual"
            return text("ap.hold.paused_failed" if why == "failed" else "ap.hold.paused", until={"at": until})
        if self._real(snap, device) is None:
            return text("ap.hold.no_value")
        app = self._app_mode(snap, device)
        if app == "wind":
            return text("ap.hold.natural_wind")
        if app == "unknown":
            return text("ap.hold.mode_unknown")  # the exception in the card never allows this one
        if app is not None and not self.state["devices"][device]["app"]:
            return text("ap.hold.app_plan" if app == "plan" else "ap.hold.app_auto")
        if not virtual and device in self.state["in_flight"]:
            return text("ap.hold.in_flight")
        return None

    # ---------- what happened since the last poll ----------

    def _observe_lights(self, snap: dict[str, Any], now: float) -> None:
        level = (snap.get("light") or {}).get("level")
        on = None if level is None else level > 0
        lights = self.state["lights"]
        if on is None:
            return
        if lights.get("on") is not None and on != lights["on"]:
            lights["period"] = int(lights.get("period") or 0) + 1
            if on:
                lights["edge_at"] = now
                self.state["undo_block"] = {}  # an undone rule may act again from the next lights on
        if on and not lights.get("first_on_at"):
            lights["first_on_at"] = now
        if not on:
            lights["first_on_at"] = None
        lights["on"] = on

    def _end_pauses(self, snap: dict[str, Any], now: float) -> None:
        """A pause that ran out: regulate from the value reported now; the exhaust takes it as its new base."""
        for device, until in list(self.state["paused"].items()):
            if until and now >= until:
                del self.state["paused"][device]
                self.state["pause_why"].pop(device, None)
                self.state["virtual"].pop(device, None)
                if device == "dfan":
                    self.state["base"] = self._real(snap, "dfan")
                    self.state["streak"] = None
                self.since.clear()  # what held before the pause does not count after it
                self._log({"kind": "event", "device": device,
                           "text": text("ap.ev.pause_end", device=tk(DEVICE_TEXT[device]))})
        for device, block in list(self.state["undo_block"].items()):
            if now >= (block or {}).get("until", 0):
                del self.state["undo_block"][device]

    def _settle_in_flight(self, snap: dict[str, Any], now: float) -> None:
        """A real command: confirmed once the box reports the target; else one retry, then a failure."""
        for device, flight in list(self.state["in_flight"].items()):
            value = self._real(snap, device)
            if value == flight["target"]:
                del self.state["in_flight"][device]
                self.state["fails_in_row"] = 0
                self.state["seen"][device] = value
                self._update_entry(flight["entry"], status="ok")
                continue
            if flight.get("cancelled"):  # a mode or level change, a restart: settled only, never sent again
                if (value is not None and value != flight["from"]) or now - flight["at"] > RETRY_MAX_AGE:
                    del self.state["in_flight"][device]
                    self._update_entry(flight["entry"], status="cancelled", why=flight["cancelled"])
                continue
            if value is not None and value != flight["from"]:
                continue  # someone else changed it meanwhile: _detect_external sees that
            if now - flight["at"] < CONFIRM_WAIT:
                continue
            if self.state["mode"] != "auto" or self.state["devices"][device]["level"] != "auto":
                self._cancel_flight(device, "mode")  # no retry outside Automatik, never later on an old decision
                continue
            flight["failed"] = True  # _act sends it once more, or books the failure

    def _detect_external(self, snap: dict[str, Any], now: float) -> None:
        """A change the box reports that the autopilot did not send: the owner (app, buttons, or while the server
        was down). That device pauses MANUAL_PAUSE seconds, a manual light change ends the ramp."""
        for device in DEVICES:
            value = self._real(snap, device)
            if value is None:
                continue
            if device == "light" and value == 0:
                continue  # lights off is the light cycle (or the owner's off), never a level to regulate from
            seen = self.state["seen"].get(device)
            self.state["seen"][device] = value  # for the light: the last level while on, across the dark phase
            if seen is None or seen == value:
                continue
            flight = self.state["in_flight"].get(device)
            if flight and value == flight["target"]:
                continue
            expect = self.state["expect"].get(device)
            if expect and value == expect.get("target") and now <= expect.get("until", 0):
                self.state["expect"].pop(device, None)
                continue
            # Off: nothing to pause, but the change is the owner's all the same. The light remembers it for the 24 h
            # rule, the owner's exhaust step is the base when the autopilot comes back, his circulation step stays.
            self._owner_took(device)
            if self.state["mode"] == "off":
                if device == "dfan":
                    self.state["base"] = value
                continue
            flight = self.state["in_flight"].pop(device, None)
            if isinstance(flight, dict) and flight.get("entry"):
                self._update_entry(flight["entry"], status="cancelled", why="manual")
            until = now + MANUAL_PAUSE
            self.state["paused"][device] = until
            self.state["pause_why"][device] = "app"
            self.state["virtual"].pop(device, None)
            self._log({"kind": "manual", "device": device, "from": seen, "to": value, "source": "app",
                       "text": text("ap.ev.manual_app", device=tk(DEVICE_TEXT[device]),
                                    old=value_param(device, seen), new=value_param(device, value),
                                    until={"at": until})})

    def _readings(self, snap: dict[str, Any], now: float) -> dict[str, Any] | None:
        """The median of the last readings, or None while there are too few. A gap or an old reading starts over."""
        age = snap.get("sensors_age")
        climate = snap.get("climate") or {}
        if age is None or age > GAP_RESET_SECONDS:  # an old reading: start over once fresh ones come (critique S1)
            self.window.clear()
            self.since.clear()
            return None
        at = now - age
        last = self.window[-1][0] if self.window else None
        if last is not None and at - last > GAP_RESET_SECONDS:
            self.window.clear()
            self.since.clear()  # a hold timer must not span a gap: fresh data, old counters (critique S1)
            last = None
        if last is None or at - last > NEW_READING_SECONDS:
            keys = ("probe_temp", "probe_humidity", "probe_vpd", "humidity")
            self.window.append((at, {k: climate.get(k) for k in keys}))
            self.window = self.window[-SMOOTH_N:]
        if len(self.window) < MIN_READINGS:
            return None

        def med(key: str) -> float | None:
            vals = [r[key] for _, r in self.window if isinstance(r.get(key), int | float)]
            return statistics.median(vals) if len(vals) >= MIN_READINGS else None

        return {k: med(k) for k in ("probe_temp", "probe_humidity", "probe_vpd", "humidity")}

    def _held(self, key: str, cond: bool, now: float, hold: float = PERSIST_SECONDS) -> bool:
        if not cond:
            self.since[key] = None
            return False
        if self.since.get(key) is None:
            self.since[key] = now
        return now - (self.since[key] or now) >= hold

    def _mist(self, values: dict[str, Any], now: float) -> str | None:
        """"mist" (probe more than MIST_GAP above the box sensor), "unknown" (a probe value but none from the box
        sensor: the lock cannot be checked, so it holds, review 1, S6) or None. Mist that comes and goes is added up
        over the last 24 hours: at MIST_PUSH_AFTER in total one push, and a start or end is logged at most once per
        MIST_LOG_EVERY (review 2, S3: twelve short episodes a day used to give 24 log lines and never a push)."""
        probe, box = values.get("probe_humidity"), values.get("humidity")
        if probe is None:
            return None
        if box is None:
            return "unknown"
        mist = probe - box > MIST_GAP
        spans = [list(s) for s in self.state.get("mist_spans") or []
                 if isinstance(s, list | tuple) and len(s) == 2 and (s[1] is None or now - s[1] < 86400)]
        open_now = bool(spans) and spans[-1][1] is None
        if mist and not open_now:
            spans.append([now, None])
            logged = now - (self.state.get("mist_logged_at") or 0) >= MIST_LOG_EVERY
            self.state["mist_logged"] = logged
            if logged:
                self.state["mist_logged_at"] = now
                self._log({"kind": "event", "text": text("ap.ev.mist", probe=reading("probe_humidity", probe),
                                                          box=reading("probe_humidity", box))})
        elif not mist and open_now:
            spans[-1][1] = now
            if self.state.get("mist_logged"):
                self.state["mist_logged"] = False
                self._log({"kind": "event", "text": text("ap.ev.mist_end")})
        self.state["mist_spans"] = spans
        self.state["mist_since"] = spans[-1][0] if spans and spans[-1][1] is None else None
        total = sum(min(now, e if e is not None else now) - max(s, now - 86400) for s, e in spans
                    if (e if e is not None else now) > now - 86400)
        if total >= MIST_PUSH_AFTER:
            self._say_once("mist_long", text("ap.ev.mist_long", minutes=q(round(total / 60), "min", 0)),
                           push=("ap.push.mist_title", "ap.push.mist_body", 3))
        elif total < MIST_PUSH_AFTER / 2:
            self.state["said"].pop("mist_long", None)  # receded: the next long spell is said again
        return "mist" if mist else None

    # ---------- the rules ----------

    def _bounds(self, stage: dict[str, Any]) -> tuple[int, int]:
        base = self.state.get("base")
        if base is None:
            return DFAN_MIN, DFAN_MAX
        lo = max(DFAN_MIN, base - DFAN_SPAN, DFAN_FLOWER_MIN if stage.get("key") in FLOWER else 0)
        hi = min(DFAN_MAX, base + DFAN_SPAN)
        return min(lo, base), max(hi, base)

    def _decide_dfan(self, cur: int, values: dict[str, Any], tg: dict[str, Any], stage: dict[str, Any],
                     now: float) -> dict[str, Any] | None:
        lights = self.state["lights"].get("on")
        base = self.state.get("base")
        if base is None:
            self.state["base"] = base = cur
        mist = self._mist(values, now)
        votes: list[tuple[int, int, str, str, float, float]] = []  # rank, direction, reason, metric, value, limit
        settled = True
        band_t = temp_band(tg, lights)
        x = values.get("probe_temp")
        if band_t and x is not None:
            lo, hi = band_t
            if self._held("t+", x > hi + OUT_MARGIN["probe_temp"], now):
                votes.append((1 if x > hi + HARD_TEMP else 3, +1, "temp_high", "probe_temp", x, hi))
                self.state["cold_humid"]["temp_high_at"] = now  # "VPD too high" waits after this (see below)
            if self._held("t-", x < lo - OUT_MARGIN["probe_temp"], now):
                votes.append((1 if x < lo - HARD_TEMP else 3, -1, "temp_low", "probe_temp", x, lo))
            settled &= lo + IN_MARGIN["probe_temp"] <= x <= hi - IN_MARGIN["probe_temp"]
            if lights is None:
                votes = [v for v in votes if v[0] == 1]  # lights unknown: only the outer band, only rank 1 (M2)
        else:
            settled = False
        h = values.get("probe_humidity")
        h_band = tg.get("probe_humidity")
        humid_now = False
        if mist == "unknown" and h_band and h is not None:
            humid_now = h > h_band["max"]  # no box sensor: no humidity vote, but never a step down into humid air
        if h_band and h is not None and not mist and lights is not None:
            humid_now = h > h_band["max"]
            if self._held("h+", h > h_band["max"] + OUT_MARGIN["probe_humidity"], now):
                votes.append((2, +1, "hum_high", "probe_humidity", h, h_band["max"]))
            settled &= h <= h_band["max"] - IN_MARGIN["probe_humidity"]
        else:
            self.since["h+"] = None  # a reading from before the mist does not count after it
            settled &= not mist
        p = values.get("probe_vpd")
        p_band = tg.get("probe_vpd")
        no_room = None  # the VPD is too high, but the vote waits: the limit it waits for, "humid", "heat" or "unknown"
        if p_band and p is not None and not mist and lights is True:  # VPD only with the lights on (critique M3)
            if self._held("p-", p < p_band["min"] - OUT_MARGIN["probe_vpd"], now):
                votes.append((4, +1, "vpd_low", "probe_vpd", p, p_band["min"]))
            # Too dry for this temperature: one step down keeps the moisture in. Only with room to the day maximum and
            # the humidity not above its band, so it never fights "too warm" or "too humid" (23.10.2026).
            # It also waits COLD_HUMID_WINDOW after a "too warm" vote, so the two do not flip the exhaust back and
            # forth at the day maximum (review 23.10.2026, S2).
            day = tg.get("probe_temp_day") or tg.get("probe_temp")
            heat_at = self.state["cold_humid"].get("temp_high_at")
            heat = bool(heat_at) and now - heat_at <= COLD_HUMID_WINDOW
            room = (bool(day) and x is not None and x <= day["max"] - VPD_HIGH_HEADROOM and not humid_now
                    and not heat)
            if self._held("p+", room and p > p_band["max"] + OUT_MARGIN["probe_vpd"], now):
                votes.append((4, -1, "vpd_high", "probe_vpd", p, p_band["max"]))
            if not room and p > p_band["max"]:  # inside the margin too, never "on target" over the band (07.11.2026)
                if humid_now:
                    no_room = "humid"
                elif not day or x is None:
                    no_room = "unknown"
                elif heat:
                    no_room = "heat"
                else:
                    no_room = day["max"] - VPD_HIGH_HEADROOM
            settled &= p_band["min"] + IN_MARGIN["probe_vpd"] <= p <= p_band["max"] - IN_MARGIN["probe_vpd"]
        else:
            self.since["p-"] = None
            self.since["p+"] = None
        rest = self.state[self._k("rest_until")]
        # A resting rule does not vote, but an urgent (rank 1) temperature always does (review 2, M2).
        resting = [v for v in votes if v[0] != 1 and now < rest.get(v[3], 0)]
        votes = [v for v in votes if v[0] == 1 or now >= rest.get(v[3], 0)]
        block = self.state["undo_block"].get("dfan")
        if block:
            votes = [v for v in votes if v[1] != block["dir"]]
        ch = self.state["cold_humid"]
        if any(v[2] == "hum_high" for v in votes):
            ch["hum_at"] = now
        if any(v[2] == "temp_low" for v in votes):
            ch["cold_at"] = now
        cold_humid = bool(ch.get("hum_at") and ch.get("cold_at") and abs(ch["hum_at"] - ch["cold_at"]) <= COLD_HUMID_WINDOW
                          and now - max(ch["hum_at"], ch["cold_at"]) <= COLD_HUMID_WINDOW)
        direction, win = 0, None
        if votes:
            ups = [v for v in votes if v[1] > 0]
            downs = [v for v in votes if v[1] < 0]
            if cold_humid:
                # Too cold and too humid: the exhaust can do only one. Hold and say so; only a rank-1 cold may bring a
                # raised exhaust back to its base step, never below (critique M4, S6).
                rank1 = [v for v in downs if v[0] == 1]
                if rank1 and cur > base:
                    direction, win = -1, rank1[0]
                else:  # not the card's "exhaust fan down": that is the step the autopilot holds back
                    self._say_once("cold_humid", text("ap.ev.cold_humid", advice=tk("ap.cold_humid.fix")))
                    return text("ap.hold.cold_humid")
            elif ups and downs:
                best_up, best_dn = min(v[0] for v in ups), min(v[0] for v in downs)
                if best_up == best_dn:
                    self._say_once("conflict", text("ap.ev.conflict"))
                    return text("ap.hold.conflict")
                direction = +1 if best_up < best_dn else -1
            else:
                direction = +1 if ups else -1
            if win is None:
                win = min((v for v in votes if v[1] == direction), key=lambda v: v[0])
            if direction < 0 and humid_now and cur <= base:
                return text("ap.hold.cold_humid")  # never a step down into humid air below the base (S6)
        else:
            self.state["said"].pop("cold_humid", None)
            self.state["said"].pop("conflict", None)
        if direction:
            self.state["settled_since"] = None
            reason_key, metric, val, limit = win[2], win[3], win[4], win[5]
            last = self.state[self._k("last_step")].get("dfan")
            if last:
                gap = DFAN_REVERSE_GAP if direction != last.get("dir") else DFAN_SAME_GAP
                if now - last["at"] < gap:
                    return text("ap.hold.gap", at={"at": last["at"] + gap})  # the last step gets time to act
            streak = self.state.get(self._k("streak"))
            # "The exhaust does not work here" is about humidity and VPD only. More exhaust never makes heat worse:
            # against a rising day heat two steps can look like no effect, and going back to the base then made it
            # hotter (review 2, M2, the hot-day toy run: 7 back to 5 at 27.3 °C).
            if (streak and streak["dir"] == direction and streak["metric"] == metric and streak["count"] >= 2
                    and metric in ("probe_humidity", "probe_vpd")):
                # how far the reading moved the way the step wanted: humidity and a too high VPD should fall
                falls = metric == "probe_humidity" or direction < 0
                moved = (streak["first"] - val) if falls else (val - streak["first"])
                if moved < DEADBAND[metric]:  # two steps and the reading has not moved: the exhaust does not work here
                    if cur != base:
                        return {"to": base, "dir": 1 if base > cur else -1, "metric": metric, "kind": "windup",
                                "reason": text("ap.reason.windup", metric=tk(METRIC_TEXT[metric]), base=q(base))}
                    windups = self.state[self._k("windups")]
                    before = [s for s in (windups.get(metric) or []) if now - s < WINDUP_MEMORY]
                    windups[metric] = [*before, now]
                    rest[metric] = now + (WINDUP_REST_AGAIN if before else WINDUP_REST)
                    self.state[self._k("streak")] = None
                    return text("ap.hold.windup")
            lo, hi = self._bounds(stage)
            target = cur + direction
            if not lo <= target <= hi:
                self.state["at_bound_since"] = self.state.get("at_bound_since") or now
                if now - self.state["at_bound_since"] >= AT_BOUND_REPORT:
                    self._say_once("at_bound", text("ap.ev.at_bound", device=tk("ap.dev.dfan"), value=q(cur),
                                                    advice=tk(BOUND_TEXT[reason_key])))
                return text("ap.hold.at_bound", value=q(cur))
            self.state["at_bound_since"] = None
            self.state["said"].pop("at_bound", None)
            return {"to": target, "dir": direction, "metric": metric, "metric_value": val, "kind": "rule",
                    "reason": text(f"ap.reason.{reason_key}", value=reading(metric, val), limit=reading(metric, limit))}
        self.state["at_bound_since"] = None
        if settled and lights is not None:
            self.state["settled_since"] = self.state.get("settled_since") or now
            returns = self.state[self._k("returns")]
            period = self.state["lights"].get("period", 0)
            if returns.get("period") != period:
                self.state[self._k("returns")] = returns = {"period": period, "count": 0}
            last = self.state[self._k("last_step")].get("dfan") or {}
            if (cur != base and now - self.state["settled_since"] >= RETURN_AFTER and returns["count"] < 1
                    and now - last.get("at", 0) >= RETURN_AFTER):
                d = -1 if cur > base else +1
                return {"to": cur + d, "dir": d, "metric": None, "kind": "return",
                        "reason": text("ap.reason.return_base", base=q(base))}
        else:
            self.state["settled_since"] = None
        # Never "on target" while a reading is past its band: inside the margin, or while its ten minutes run, the status
        # says what the rule waits for (07.11.2026: Hank said "too warm" at 28.1 °C, the exhaust "on target", and the
        # page told the owner to turn it up). The temperature first, as in the votes.
        if band_t and x is not None and not band_t[0] <= x <= band_t[1]:
            margin = HARD_TEMP if lights is None else OUT_MARGIN["probe_temp"]  # lights unknown: rank 1 only
            if x > band_t[1]:
                return self._waits("probe_temp", x, band_t[1], band_t[1] + margin, +1)
            return self._waits("probe_temp", x, band_t[0], band_t[0] - margin, -1)
        if resting:  # the reading is still off, its rule rests: say so instead of "on target" (review 2, M2)
            metric = resting[0][3]
            return text("ap.hold.rest", metric=tk(METRIC_TEXT[metric]), at={"at": rest[metric]})
        if isinstance(no_room, int | float):  # never "on target" while the VPD is over its band (23.10.2026)
            return text("ap.hold.vpd_no_room", value=reading("probe_vpd", p), limit=reading("probe_temp", no_room))
        if no_room == "humid":
            return text("ap.hold.vpd_humid", value=reading("probe_vpd", p))
        if no_room == "heat":
            return text("ap.hold.vpd_heat", value=reading("probe_vpd", p),
                        at={"at": self.state["cold_humid"]["temp_high_at"] + COLD_HUMID_WINDOW})
        if no_room == "unknown":
            return text("ap.hold.vpd_no_temp", value=reading("probe_vpd", p))
        if mist == "unknown":
            return text("ap.hold.box_humidity_missing")
        if mist:
            return text("ap.hold.mist")
        if h_band and h is not None and lights is not None and h > h_band["max"]:
            top = h_band["max"]
            return self._waits("probe_humidity", h, top, top + OUT_MARGIN["probe_humidity"], +1)
        if p_band and p is not None and lights is True and not p_band["min"] <= p <= p_band["max"]:
            if p < p_band["min"]:
                return self._waits("probe_vpd", p, p_band["min"], p_band["min"] - OUT_MARGIN["probe_vpd"], +1)
            # too dry with room to the day maximum: without room a vpd_* hold above has said why already
            return self._waits("probe_vpd", p, p_band["max"], p_band["max"] + OUT_MARGIN["probe_vpd"], -1)
        return None

    def _waits(self, metric: str, value: float, limit: float, trigger: float, step: int) -> dict[str, Any]:
        """The exhaust's status while a reading is past its band and no rule moves it yet: inside the margin, or past
        it for less than PERSIST_SECONDS. A step that way the owner undid waits for the next lights on and says so."""
        block = self.state["undo_block"].get("dfan") or {}
        if block.get("dir") == step:
            return text("ap.hold.undone", at={"at": block.get("until")})
        side = "high" if value > limit else "low"
        return text(f"ap.hold.watch_{side}_{'up' if step > 0 else 'down'}", metric=tk(METRIC_TEXT[metric]),
                    value=reading(metric, value), limit=reading(metric, limit), trigger=reading(metric, trigger),
                    minutes=q(PERSIST_SECONDS // 60, "min", 0))

    def _decide_cfan(self, cur: int, values: dict[str, Any], tg: dict[str, Any], stage: dict[str, Any],
                     now: float) -> dict[str, Any] | None:
        """The stage preset's circulation step, once per stage. Never regulated by the climate (no source says what
        a circulation step does at the probe), never set back after a manual change."""
        key = stage.get("key")
        step = (stage.get("preset") or {}).get("cfan_step")
        done = "cfan_stage_virtual" if self._virtual else "cfan_stage"  # observing never uses up the real one
        if self.state.get(done) == key:
            return None
        if step is None or cur == step:
            self.state[done] = key
            return None
        label = localize(stage, "de").get("label") or key
        label_en = localize(stage, "en").get("label") or key
        return {"to": int(step), "dir": 1 if step > cur else -1, "metric": None, "kind": "preset",
                "reason": text("ap.reason.cfan_preset", stage={"tw": {"de": label, "en": label_en}}, step=q(step))}

    def _decide_light(self, cur: int, values: dict[str, Any], tg: dict[str, Any], stage: dict[str, Any],
                      now: float) -> dict[str, Any] | None:
        preset = (stage.get("preset") or {}).get("light_percent")
        lights = self.state["lights"].get("on")
        day = tg.get("probe_temp_day") or tg.get("probe_temp")
        x, p = values.get("probe_temp"), values.get("probe_vpd")
        light = self.state["light"]
        # Safety first: its own last increase goes back when it gets too warm for 20 minutes (critique S6).
        own = light.get("v_own_up" if self._virtual else "own_up")
        if own and now - own["at"] < LIGHT_WAIT and day and x is not None and cur == own["to"]:
            if self._held("lh", x > day["max"] + LIGHT_HEAT_MARGIN, now, LIGHT_HEAT_HOLD):
                return {"to": own["from"], "dir": -1, "metric": "probe_temp", "kind": "heat", "safety": True,
                        "reason": text("ap.reason.light_heat", value=reading("probe_temp", x),
                                       limit=reading("probe_temp", day["max"] + LIGHT_HEAT_MARGIN))}
        else:
            self.since["lh"] = None
        if not preset:
            return text("ap.hold.no_preset")
        if lights is not True or cur <= 0:
            self.since["lt"] = None
            return text("ap.hold.light_off" if lights is False or cur == 0 else "ap.hold.lights_unknown")
        # The temperature hold runs from lights on, so it is already measured when the window opens.
        ok = bool(day) and x is not None and x <= day["max"] - LIGHT_HEADROOM and not (
            tg.get("probe_vpd") and p is not None and p > tg["probe_vpd"]["max"])
        warm_enough_held = self._held("lt", ok, now, LIGHT_HEADROOM_HOLD)
        if cur >= preset:
            return text("ap.hold.at_preset", value=q(preset, "%"))
        if light.get("series_stopped"):
            return text("ap.hold.series_stopped")
        lamp = self.state.get("lamp")
        heights = self.plant_log.data.get("heights") or []
        cm = heights[-1].get("cm") if heights and isinstance(heights[-1], dict) else None
        if not lamp:
            return text("ap.hold.lamp")
        if cm is not None and lamp.get("height_cm") is not None and cm - lamp["height_cm"] > LAMP_REGROW_CM:
            return text("ap.hold.lamp_grown", cm=q(cm - lamp["height_cm"], "cm", 0))
        last = max(light.get("last_change_at") or 0, (light.get("virtual_at") or 0) if self._virtual else 0) or None
        if last and now - last < LIGHT_WAIT:
            return text("ap.hold.wait_24h", at={"at": last + LIGHT_WAIT})
        edge = self.state["lights"].get("edge_at")
        if edge and now - edge < 24 * 3600:
            since_on = now - edge
            if not LIGHT_WINDOW[0] <= since_on <= LIGHT_WINDOW[1]:
                return text("ap.hold.window")
        elif now - (self.state["lights"].get("first_on_at") or now) < LIGHT_WINDOW[0]:
            return text("ap.hold.window")
        if not day or x is None:
            return text("ap.hold.no_value")
        if not warm_enough_held:
            return text("ap.hold.too_warm", limit=reading("probe_temp", day["max"] - LIGHT_HEADROOM))
        recent = [s for s in self._all_steps() if now - s < LIGHT_QUIET]
        if recent:
            return text("ap.hold.quiet")
        new = min(int(preset), int(cur) + LIGHT_STEP)
        return {"to": new, "dir": 1, "metric": None, "kind": "ramp",
                "reason": text("ap.reason.light_ramp", preset=q(preset, "%"), value=reading("probe_temp", x),
                               limit=reading("probe_temp", day["max"]))}

    # ---------- acting ----------

    def _limits_ok(self, device: str, direction: int, now: float, safety: bool) -> dict[str, Any] | None:
        """Device timing for real and virtual steps alike; history length never imposes a command quota."""
        key = self._k("history")
        hist = [s for s in self.state[key][device] if now - s < 86400]
        self.state[key][device] = hist
        if safety:
            return None
        if device == "dfan":
            last = self.state[self._k("last_step")].get("dfan")
            if last:
                gap = DFAN_REVERSE_GAP if direction != last.get("dir") else DFAN_SAME_GAP
                if now - last["at"] < gap:
                    return text("ap.hold.gap", at={"at": last["at"] + gap})
        return None

    def _all_steps(self) -> list[float]:
        """Every step of every device: the real ones, and while deciding virtually the would-be ones as well."""
        out = [s for d in DEVICES for s in self.state["history"][d]]
        if self._virtual:
            out += [s for d in DEVICES for s in self.state["v_history"][d]]
        return out

    async def _act(self, intent: dict[str, Any], snap: dict[str, Any], now: float) -> dict[str, Any] | None:
        device, frm, to = intent["device"], intent["frm"], intent["to"]
        self._virtual = bool(intent["virtual"])  # the bookkeeping of this step: real or virtual
        flight = self.state["in_flight"].get(device)
        if flight and flight.get("failed"):
            return await self._retry(device, flight, now)
        why = self._limits_ok(device, intent["dir"], now, bool(intent.get("safety")))
        if why is None and self._acted and not intent.get("safety"):
            why = text("ap.hold.quiet")
        if why is not None:
            self.holds[device] = why
            return None
        if intent["virtual"]:
            kind = "would" if self.state["mode"] == "observe" else "suggest"
            self._acted = self._acted or not intent.get("safety")
            entry = self._log({"kind": kind, "device": device, "from": frm, "to": to, "rule": intent.get("kind"),
                               "safety": bool(intent.get("safety")),
                               "text": text(f"ap.step.{kind}", device=tk(DEVICE_TEXT[device]),
                                            old=value_param(device, frm), new=value_param(device, to),
                                            reason=intent["reason"])})
            self.state["virtual"][device] = to
            self._booked(intent, now)
            return entry
        # Real: read the box and the owner's switches again right before sending. A manual change, a pause, the mode
        # switched off or the device set to suggest or manual while an earlier command of this poll was awaited: all
        # of them win (critique S2; review 1, S2 and S3).
        fresh = self.box.snapshot()
        if self._real(fresh, device) != frm or not self._may_send(device, fresh):
            return None
        try:
            await self._send(device, to, bool(intent.get("safety")))
        except BudgetExceeded as err:
            self._say_once("budget", text("ap.ev.budget", at={"at": err.until}),
                           push=("ap.push.budget_title", "ap.push.budget_body", 3))
            self.holds[device] = text("ap.hold.budget")
            return None
        except CommandRefused as err:  # no live connection: no attempt, the next poll tries again
            self.holds[device] = text("ap.hold.refused", error=str(err))
            return None
        self.state["said"].pop("budget", None)
        self._acted = self._acted or not intent.get("safety")
        entry = self._log({"kind": "step", "device": device, "from": frm, "to": to, "rule": intent.get("kind"),
                           "safety": bool(intent.get("safety")), "status": "sent",
                           "text": text("ap.step.done", device=tk(DEVICE_TEXT[device]), old=value_param(device, frm),
                                        new=value_param(device, to), reason=intent["reason"])})
        self.state["in_flight"][device] = {"target": to, "from": frm, "at": now, "attempt": 1, "entry": entry["id"],
                                           "safety": bool(intent.get("safety"))}
        self.state["expect"][device] = {"target": to, "until": now + 600}  # its own change is never the owner's
        self._booked(intent, now)
        return entry

    def _may_send(self, device: str, snap: dict[str, Any]) -> bool:
        """The last check before a real command (a first try or a retry): Automatik, the device on "auto", no pause,
        not run by a plan or VIVOSUN's automation (unless allowed), mode known. Read at the moment of sending."""
        now = self._clock()
        if self.state["mode"] != "auto" or self.state["devices"][device]["level"] != "auto":
            return False
        if self._paused(device, now):
            return False
        hold = self._device_block(snap, device, False, now)
        return hold is None or hold.get("key") == "ap.hold.in_flight"

    async def _send(self, device: str, value: int, safety: bool) -> None:
        """Send through the account-wide command history."""
        call = {"light": lambda: self.box.set_light(int(value)), "dfan": lambda: self.box.set_dfan(int(value)),
                "cfan": lambda: self.box.set_cfan(int(value))}[device]
        await self.budget.send("autopilot", device, call, safety=safety, box=self.box_id)

    def _booked(self, intent: dict[str, Any], now: float) -> None:
        """The bookkeeping after a step (real or virtual): rhythm, streak, light series, cfan stage."""
        device = intent["device"]
        virtual = bool(intent["virtual"])
        k = lambda key: self._k(key, virtual)  # noqa: E731
        self.state[k("history")][device].append(now)
        self.state[k("last_step")][device] = {"at": now, "dir": intent["dir"]}
        self.since = {key: None for key in self.since}  # give the step time to act before the next one
        if device == "dfan":
            self.state["settled_since"] = None
            if intent.get("kind") == "windup":
                metric = intent["metric"]
                windups = self.state[k("windups")]
                before = [s for s in (windups.get(metric) or []) if now - s < WINDUP_MEMORY]
                windups[metric] = [*before, now]
                self.state[k("rest_until")][metric] = now + (WINDUP_REST_AGAIN if before else WINDUP_REST)
                self.state[k("streak")] = None
            elif intent.get("kind") == "return":
                returns = self.state[k("returns")]
                returns["count"] = returns.get("count", 0) + 1
                self.state[k("streak")] = None
            elif intent.get("kind") == "rule":
                streak = self.state.get(k("streak"))
                if streak and streak["dir"] == intent["dir"] and streak["metric"] == intent["metric"]:
                    streak["count"] += 1
                else:
                    self.state[k("streak")] = {"dir": intent["dir"], "metric": intent["metric"], "count": 1,
                                               "first": intent.get("metric_value")}
            else:
                self.state[k("streak")] = None
        if device == "light":
            light = self.state["light"]
            light["virtual_at" if virtual else "last_change_at"] = now
            light["v_own_up" if virtual else "own_up"] = ({"at": now, "from": intent["frm"], "to": intent["to"]}
                                                          if intent["to"] > intent["frm"] else None)
            if not virtual and intent["to"] > intent["frm"]:
                light["auto_up_at"] = now  # Hank's 24 h rule sees it (review 1, S10)
        if device == "cfan":
            _, stage = self.stage()
            self.state["cfan_stage_virtual" if intent["virtual"] else "cfan_stage"] = (stage or {}).get("key")

    async def _retry(self, device: str, flight: dict[str, Any], now: float) -> dict[str, Any] | None:
        """One more try for an unconfirmed command; a second miss is a failure. The retry repeats a decision of a
        moment ago only: never outside Automatik, never for a device set to suggest or manual, paused or now run by
        a plan or VIVOSUN's automation, never one older than RETRY_MAX_AGE (review 1, M1 and M2)."""
        if flight.get("cancelled"):
            return None  # settled in _settle_in_flight, never sent again
        if now - flight.get("at", 0) > RETRY_MAX_AGE:
            # The box never confirmed it, and the moment for a second try has passed (an adapter deferred it, or the server
            # was busy): no late repeat of an old decision, but it counts as a failure (review 1, M2).
            return self._book_failure(device, flight, now)
        if not self._may_send(device, self.box.snapshot()):
            self._cancel_flight(device, "blocked")
            return None
        if flight.get("attempt", 1) < 2:
            try:
                await self._send(device, flight["target"], bool(flight.get("safety")))
            except (BudgetExceeded, CommandRefused):
                return None
            flight.update(attempt=2, at=now, failed=False)
            self._update_entry(flight["entry"], status="retry")
            return None
        return self._book_failure(device, flight, now)

    def _book_failure(self, device: str, flight: dict[str, Any], now: float) -> None:
        """An unconfirmed command: the device pauses, three in a day put it on manual, three in a row switch the
        autopilot off. The pushes go out at the end of the poll, never between two commands of one poll."""
        self.state["in_flight"].pop(device, None)
        self._update_entry(flight["entry"], status="failed")
        until = now + FAIL_PAUSE
        self.state["paused"][device] = until
        self.state["pause_why"][device] = "failed"
        fails = [s for s in self.state["fails"].get(device, []) if now - s < 86400] + [now]
        self.state["fails"][device] = fails
        self.state["fails_in_row"] = int(self.state.get("fails_in_row") or 0) + 1
        self._log({"kind": "failed", "device": device, "to": flight["target"],
                   "text": text("ap.ev.failed", device=tk(DEVICE_TEXT[device]),
                                value=value_param(device, flight["target"]), until={"at": until})})
        self._pushes.append(("ap.push.failed_title", "ap.push.failed_body", 3, {"device": tk(DEVICE_TEXT[device])}))
        if len(fails) >= FAILS_PER_DAY:
            self.state["devices"][device]["level"] = "manual"
            self._log({"kind": "event", "device": device, "text": text("ap.ev.to_manual", device=tk(DEVICE_TEXT[device]))})
            self._pushes.append(("ap.push.manual_title", "ap.push.manual_body", 4, {"device": tk(DEVICE_TEXT[device])}))
        if self.state["fails_in_row"] >= FAILS_IN_ROW:
            self._shutdown(now, "ap.ev.self_off")
            self._pushes.append(("ap.push.off_title", "ap.push.off_body", 4, {}))
        return None

    def _shutdown(self, now: float, key: str) -> None:
        self.state.update(mode="off", mode_since=now)
        self._log({"kind": "event", "text": text(key)})
        self._save()

    def _say_once(self, key: str, message: dict[str, Any], push: tuple[str, str, int] | None = None) -> None:
        if self.state["said"].get(key):
            return
        self.state["said"][key] = self._clock()
        self._log({"kind": "hold", "text": message})
        if push is not None:
            self._pushes.append(push)  # sent at the end of the poll, never as a loose task

    async def _push(self, title: str, body: str, priority: int, **params: Any) -> None:
        if self.alerts is None:
            return
        try:
            await self.alerts.push(t(title), t(body, **{k: _param(v, None) for k, v in params.items()}),
                                   priority=priority)
        except Exception:
            _LOGGER.exception("Autopilot push failed")

    # ---------- the day ----------

    def _stats(self, values: dict[str, Any] | None, snap: dict[str, Any], now: float) -> None:
        """Time in target, per day: the start of the hit rate (design 3, Tageszusammenfassung)."""
        day = datetime.fromtimestamp(now).date().isoformat()
        stats = self.state.get("stats")
        if not isinstance(stats, dict) or stats.get("day") != day:
            if isinstance(stats, dict) and stats.get("day"):
                self.state["pending_summary"] = stats
            stats = {"day": day, "n": {}, "in": {}}
            self.state["stats"] = stats
        if values is None:
            return
        _, stage = self.stage()
        tg = bands(stage)
        lights = self.state["lights"].get("on")
        checks = {"probe_temp": temp_band(tg, lights) if lights is not None else None,
                  "probe_humidity": (tg["probe_humidity"]["min"], tg["probe_humidity"]["max"])
                  if tg.get("probe_humidity") else None,
                  "probe_vpd": (tg["probe_vpd"]["min"], tg["probe_vpd"]["max"]) if tg.get("probe_vpd") else None}
        for metric, band in checks.items():
            x = values.get(metric)
            if band is None or x is None:
                continue
            stats["n"][metric] = stats["n"].get(metric, 0) + 1
            stats["in"][metric] = stats["in"].get(metric, 0) + (1 if band[0] <= x <= band[1] else 0)

    def _summary(self, now: float) -> None:
        stats = self.state.pop("pending_summary", None)
        if not stats:
            return
        day = stats["day"]
        rows = [e for e in self.entries(2000) if datetime.fromtimestamp(e["at"]).date().isoformat() == day]
        counts = {k: sum(1 for e in rows if e.get("kind") == k) for k in ("step", "would", "suggest", "undo", "manual",
                                                                            "failed")}

        def pct(metric: str) -> Any:
            n = stats["n"].get(metric, 0)
            return q(round(100 * stats["in"].get(metric, 0) / n), "%", 0) if n else tk("ap.na")

        summary = text("ap.summary", day={"tw": {"de": datetime.fromisoformat(day).strftime("%d.%m."),
                                                  "en": datetime.fromisoformat(day).strftime("%b %d")}},
                       steps=q(counts["step"]), would=q(counts["would"] + counts["suggest"]), undos=q(counts["undo"]),
                       pauses=q(counts["manual"]), failed=q(counts["failed"]), t=pct("probe_temp"),
                       h=pct("probe_humidity"), v=pct("probe_vpd"))
        if self.state["mode"] == "off" and not any(counts.values()):
            return  # a day switched off has nothing to report
        self.state["last_summary"] = {"day": day, "counts": counts, "text": summary}
        self._log({"kind": "summary", "day": day, "counts": counts, "text": summary})

    # ---------- the page ----------

    def control_info(self, snap: dict[str, Any], device: str, now: float) -> tuple[str, dict[str, Any]]:
        """Read-only readiness for the page and Hank. A selected mode never proves a device can act.

        Recheck current pauses and reported modes without running any decision, timer, guard write or command.
        Decision holds come from the most recent poll; an in-band device is monitored even when it sends nothing.
        """
        mode = self.state["mode"]
        if device == "light" and self.light_schedule is not None and self.light_schedule.cfg["enabled"]:
            schedule = self.light_schedule.view(snap)
            state = {"scheduled": "ready", "manual": "paused", "pending": "pending"}.get(schedule["status"], "waiting")
            return state, text("ap.hold.light_schedule")
        if self.forced_off or self.locked_out:
            return "needs_setup", text("ap.hold.forced_off" if self.forced_off else "ap.hold.locked")
        if mode == "off":
            return "off", text("ap.hold.off")
        if self.state["devices"][device]["level"] == "manual":
            return "manual", text("ap.hold.manual_level")
        if not snap.get("mqtt", True) or snap.get("online") is False:
            return "waiting", text("ap.hold.no_link")
        if snap.get("sensors_age") is None or snap["sensors_age"] > STALE_SECONDS:
            return "waiting", text("ap.hold.stale")
        global_key = (self.status or {}).get("key", "")
        if global_key.startswith("ap.hold."):
            if global_key in ("ap.hold.stage", "ap.hold.ports", "ap.hold.profile"):
                return "needs_setup", self.status
            return "waiting", text("ap.hold.warmup") if global_key == "ap.hold.off" else self.status
        hold = self._device_block(snap, device, mode == "observe" or self.state["devices"][device]["level"] == "suggest", now)
        if hold is None and (self.holds.get(device) or {}).get("key") in (
                "ap.hold.natural_wind", "ap.hold.mode_unknown", "ap.hold.app_plan", "ap.hold.app_auto",
                "ap.hold.paused", "ap.hold.paused_failed", "ap.hold.no_value", "ap.hold.in_flight"):
            # A fresh device report can clear these before the next sensor poll. Do not resurrect its old warning
            # or claim regulation before the controller has evaluated the newly available device.
            hold = text("ap.hold.next_poll")
        if hold is None and device == "light" and self._needs_lamp(snap, self.stage()[1]):
            hold = self.holds.get(device) or text("ap.hold.lamp")
        hold = hold or self.holds.get(device) or text("ap.hold.on_target")
        key = hold.get("key")
        if key in ("ap.hold.lamp", "ap.hold.lamp_grown", "ap.hold.series_stopped", "ap.hold.natural_wind",
                   "ap.hold.mode_unknown", "ap.hold.app_plan", "ap.hold.app_auto"):
            return "needs_setup", hold
        if key in ("ap.hold.paused", "ap.hold.paused_failed"):
            return "paused", hold
        if key == "ap.hold.in_flight":
            return "pending", hold
        if key not in ("ap.hold.on_target", "ap.hold.at_preset", "ap.hold.light_off"):
            return "waiting", hold
        if mode == "observe" or self.state["devices"][device]["level"] == "suggest":
            return "observe", hold
        return "ready", hold

    def _humidifier_control(self, snap: dict[str, Any], hum: dict[str, Any]) -> None:
        view = self.humidifier.view(snap) if callable(getattr(self.humidifier, "view", None)) else None
        if view is None:
            return  # compatibility with an adapter that only exposes the rule's switch
        if not view.get("enabled"):
            state, reason = "off", text("ap.health.hum_off")
        elif view.get("profile_problem"):
            state, reason = "needs_setup", text("ap.hold.profile", error=view["profile_problem"])
        elif view.get("needs_stage_confirmation"):
            state, reason = "needs_setup", text("ap.hold.stage")
        elif view.get("implausible"):
            state, reason = "needs_setup", text("ap.health.hum_implausible")
        elif view.get("port_device") != "humidifier":
            state, reason = "needs_setup", text("ap.health.hum_unassigned" if not view.get("port_device") else "ap.health.hum_other")
        elif view.get("port_hold"):
            state, reason = "needs_setup", text("ap.hold.ports")
        elif not snap.get("mqtt", True) or snap.get("online") is False:
            state, reason = "waiting", text("ap.hold.no_link")
        elif view.get("stale") or view.get("missing"):
            state, reason = "waiting", text("ap.hold.stale" if view.get("stale") else "ap.hold.no_value")
        elif view.get("paused_until"):
            state, reason = "paused", text("ap.hold.paused", until={"at": view["paused_until"]})
        else:
            state, reason = "ready", text("ap.health.hum_ready")
        hum.update(control_state=state, reason_key=reason["key"], status=reason,
                   port_device=view.get("port_device"), implausible=bool(view.get("implausible")))

    def view(self, snap: dict[str, Any] | None = None, lang: str | None = None) -> dict[str, Any]:
        snap = self.box.snapshot() if snap is None else snap
        now = self._clock()
        kind, stage = self.stage()
        undoable = self._undoable()
        outcomes: dict[str, str] = {}
        log = []
        for e in self.entries():
            if e.get("kind") == "outcome":
                outcomes[e.get("of")] = e.get("status")
        for e in reversed(self.entries()):
            if e.get("kind") in ("outcome", "summary"):
                continue  # the day's report has its own line under the log
            item = {"id": e["id"], "at": e["at"], "kind": e.get("kind"), "device": e.get("device"),
                    "source": e.get("source"),  # "app": changed in the VIVOSUN app (the page groups repeats)
                    "from": e.get("from"), "to": e.get("to"), "text": render(e.get("text"), lang),
                    "status": outcomes.get(e["id"], e.get("status")), "undo": e["id"] in undoable}
            log.append(item)
            if len(log) >= RECENT:
                break
        devices = []
        for d in DEVICES:
            cfg = self.state["devices"][d]
            until = self._paused(d, now)
            control, reason = self.control_info(snap, d, now)
            devices.append({"id": d, "name": t(DEVICE_TEXT[d], lang), "level": cfg["level"], "app": cfg["app"],
                            "value": self._real(snap, d), "virtual": self.state["virtual"].get(d),
                            "paused_until": until, "app_mode": self._app_mode(snap, d),
                            "status": render(reason, lang), "control_state": control, "reason_key": reason["key"]})
        shown = localize(stage, lang) if stage else None
        heights = self.plant_log.data.get("heights") or []
        profile = load_profile() if self._stage_source is None else None
        lamp = self.state.get("lamp")
        needs = {
            "stage": ({"key": stage["key"], "label": shown.get("label") or stage["key"], "set_by": stage.get("set_by")}
                      if kind == "stage" and stage else None),
            "lamp": self._needs_lamp(snap, stage),
            "ports": self.guard.changed(),
        }
        hum = None
        if self.humidifier is not None:
            hstate = self.humidifier.state
            hum = {"enabled": bool(hstate.get("enabled")), "port": getattr(self.humidifier, "port", "usb2"),
                   "paused_until": hstate.get("paused_until") if (hstate.get("paused_until") or 0) > now else None}
            self._humidifier_control(snap, hum)
            if isinstance(hum.get("status"), dict):
                hum["status"] = render(hum["status"], lang)
        controls = devices + ([hum] if hum and hum.get("control_state") else [])
        readiness = {key: sum(item.get("control_state") == key for item in controls)
                     for key in ("ready", "observe", "waiting", "needs_setup", "paused", "pending", "manual", "off")}
        readiness["total"] = len(controls)
        readiness["state"] = ("off" if self.state["mode"] == "off" else "attention" if readiness["needs_setup"]
                              else "waiting" if readiness["waiting"] or readiness["paused"] or readiness["pending"]
                              else "observe" if self.state["mode"] == "observe" else "ready" if readiness["ready"] else "manual")
        summary = self.state.get("last_summary")
        return {
            "id": self.box_id, "name": self.name or snap.get("device") or self.box_id,
            "plant": ((profile or {}).get("plant") or {}).get("strain"),
            "height_cm": heights[-1].get("cm") if heights and isinstance(heights[-1], dict) else None,
            "stage": {"key": stage["key"], "label": shown.get("label") or stage["key"],
                      "set_by": stage.get("set_by")} if stage else None,
            "mode": self.state["mode"], "mode_since": self.state.get("mode_since"), "forced_off": self.forced_off,
            "locked_out": self.locked_out, "status": render(self.status, lang),
            "devices": devices, "humidifier": hum, "needs": needs, "readiness": readiness,
            "lamp": {"min_cm": LAMP_MIN_CM, "confirmed_at": (lamp or {}).get("at"),
                     "height_cm": (lamp or {}).get("height_cm")},
            "base": self.state.get("base"), "log": log,
            "summary": ({"day": summary["day"], "text": render(summary["text"], lang)} if summary else None),
            "budget": self.budget.view(),
            "limits": {"dfan": list(self._bounds(stage or {})), "dfan_per_hour": None,
                       "dfan_per_day": None, "light_step": LIGHT_STEP, "pause_min": MANUAL_PAUSE // 60,
                       "undo_min": UNDO_WINDOW // 60, "mist_gap": MIST_GAP},
        }

    def _needs_lamp(self, snap: dict[str, Any], stage: dict[str, Any] | None) -> bool:
        """Whether the light ramp waits for the owner's lamp distance (the page's "Lampenabstand bestätigen"). Not while
        the light timer owns the brightness: this ramp does not run then (vgrow/grow_plan.py watches the distance)."""
        if self.light_schedule is not None and self.light_schedule.cfg["enabled"]:
            return False
        lamp = self.state.get("lamp")
        heights = self.plant_log.data.get("heights") or []
        cm = heights[-1].get("cm") if heights and isinstance(heights[-1], dict) else None
        grown = bool(lamp and cm is not None and lamp.get("height_cm") is not None
                     and cm - lamp["height_cm"] > LAMP_REGROW_CM)
        return bool(self.state["mode"] != "off" and self.state["devices"]["light"]["level"] != "manual"
                    and (not lamp or grown or bool(self.state["light"].get("series_stopped")))
                    and (self._real(snap, "light") or 0) < ((stage or {}).get("preset") or {}).get("light_percent", 0))

    def brief(self, snap: dict[str, Any] | None = None) -> dict[str, Any]:
        """What Hank may read (through the snapshot his collector gets, never by importing this module): the mode,
        each device's level and status, the last three steps, and when the autopilot itself last raised the light.
        Every text in both languages; plain data only."""
        snap = self.box.snapshot() if snap is None else snap
        now = self._clock()

        def both(entry_text: dict[str, Any] | None) -> dict[str, str]:
            return {lang: render(entry_text, lang) for lang in ("de", "en")} if entry_text else {"de": "", "en": ""}

        devices = {}
        for d in DEVICES:
            cfg = self.state["devices"][d]
            devices[d] = {"level": cfg["level"], "app": cfg["app"], "app_mode": self._app_mode(snap, d),
                          "paused_until": self._paused(d, now), "status": both(self.holds.get(d)),
                          "name": {lang: t(DEVICE_TEXT[d], lang) for lang in ("de", "en")}}
        recent = [{"at": e["at"], "kind": e.get("kind"), "device": e.get("device"), "text": both(e.get("text"))}
                  for e in self.entries() if e.get("kind") in ("step", "would", "suggest", "undo", "failed", "manual")]
        kind, stage = self.stage()
        # needs: what the autopilot waits for from the owner (Hank names it, with the place on the page)
        return {"box": self.box_id, "mode": self.state["mode"], "forced_off": self.forced_off,
                "locked_out": self.locked_out, "status": both(self.status), "devices": devices,
                "recent": recent[-3:][::-1], "light_step_at": self.state["light"].get("auto_up_at"),
                "needs": {"stage": kind == "stage", "lamp": self._needs_lamp(snap, stage)}}


class AutopilotHub:
    """All boxes. One today (box-1); each has its own state folder, log and rules."""

    def __init__(self, pilots: list[Autopilot]) -> None:
        self.pilots = {p.box_id: p for p in pilots}

    def get(self, box_id: str | None) -> Autopilot:
        pilot = self.pilots.get(box_id or DEFAULT_BOX)
        if pilot is None:
            raise KeyError(box_id)
        return pilot

    async def check(self, snap: dict[str, Any]) -> None:
        for box_id, pilot in self.pilots.items():
            try:
                await pilot.check(snap if box_id == DEFAULT_BOX else pilot.box.snapshot())
            except Exception:  # one box failing never stops the others
                _LOGGER.exception("Autopilot %s failed", box_id)

    def view(self, snap: dict[str, Any] | None = None, lang: str | None = None) -> dict[str, Any]:
        boxes = []
        for box_id, pilot in self.pilots.items():
            try:
                boxes.append(pilot.view(snap if box_id == DEFAULT_BOX else None, lang))
            except Exception:
                _LOGGER.exception("Autopilot view %s failed", box_id)
        return {"boxes": boxes}

    def brief(self, snap: dict[str, Any] | None = None) -> dict[str, Any]:
        boxes = []
        for box_id, pilot in self.pilots.items():
            try:
                boxes.append(pilot.brief(snap if box_id == DEFAULT_BOX else None))
            except Exception:
                _LOGGER.exception("Autopilot brief %s failed", box_id)
        return {"boxes": boxes}

    def release(self) -> None:
        for pilot in self.pilots.values():
            pilot.release()


def device_of(action: str) -> str | None:
    """Which autopilot device a cockpit command touches."""
    return {"light": "light", "dfan": "dfan", "cfan": "cfan"}.get(action)


__all__ = ["Autopilot", "AutopilotHub", "MODES", "LEVELS", "DEVICES", "render", "device_of"]
