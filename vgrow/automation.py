"""Humidifier on USB 2, switched by the probe humidity against the current phase's target band.

Owner's decision (24.10.2026): off as soon as the phase maximum is reached, on again below the
effective minimum, so the band between them is the hysteresis. Switching on waits 15 minutes after
the last automatic switch; reaching the target never waits for that timer. Every switch is logged,
a manual switch pauses the automation for an hour, and if the humidity stays
above the band for an hour with the humidifier already off, a push says the exhaust has to help.
Starts disabled; the owner turns it on after naming the humidifier on USB 2 on the page.

Fresh data only: with readings older than 10 minutes (or none) it never switches on. The fail-safe:
if the humidifier runs while the readings are older than 15 minutes, or while the probe has reported
no humidity for 15 minutes, it switches the humidifier off, because off is the safe state. That counts
as done only once the box reports the port off; until then the command goes out again on every poll,
and one push says the box does not confirm. A spell (stale readings or no probe value) lasts until
fresh readings with a probe value come in; in it the fail-safe acts once. It ignores the manual pause
and the 15-minute wait. Switched back on by hand in the spell, the humidifier stays on: a manual
switch in a spell means the owner has taken over until it ends.

Only with a confirmed stage (found risk of 22.10.2026: the band came from an estimated stage, or even from the
days since the start): unless the owner set the stage (set_by "manual"), the automation never switches the
humidifier on. A running humidifier goes off once, through the same confirmed off as the fail-safe, and one push
says the automation waits for the stage. That off is logged as "stage_pause_off" and listed in "recent", so the
page's timeline never shows an older "on" as the newest line while USB 2 is off; a pause that switched nothing is
logged as "paused_unconfirmed_stage" and, like "failsafe_unconfirmed", stays out of "recent" (view() shows the pause
as needs_stage_confirmation). Switched on by hand during the pause, the humidifier stays on. Stale or missing
readings are handled first, as before.

An unreadable plant.json with no readable version known (plant.load_profile keeps the last one that parsed) is its
own pause, "profile": the same once-only confirmed off, a push that names plant.json instead of a missing stage,
its own flags and no switch time, so the fixed file takes over at the next poll.

Added with the autopilot (23.10.2026, critique M1 and M6, two reviews):
- Port guard (vgrow/portguard.py): the rule switches USB 2 ON only while the owner has named the humidifier there on
  the page (ports.json may only veto with another device), and the port assignment is the confirmed one. A change,
  an unnamed port or an unreadable record holds every "on"; the offs (fail-safe, stage and profile pause, humidity
  over the band) still run, because off is the safe state for USB 2 (a port locked always on in ports.json is
  protected by the box's own port lock). A port named as something else (a pump) is never switched at all; the rule
  only ever drives USB 2, never USB 1, where the drip pump hangs since 23.10.2026. Logged once as "paused_port".
- The fail-safe runs whether the rule is on or off: switching the rule (or the autopilot) off must never leave a
  running humidifier unwatched.
- Plausibility stop: after the rule's own "on", the probe must rise 3 points within 20 minutes; if not, off, no more
  "on" until the owner names the device on USB 2 again, and a push asks whether it really is the humidifier.
- Command history (vgrow/budget.py): every switch is recorded; command counts impose no hourly or daily quotas.
- An unconfirmed off (fail-safe or pause) goes out again with a growing wait, 90 s, 3, 6, 12, then every 15 min,
  instead of on every poll. A send refused before it went out (no connection) starts no wait.

The effective floor (23.10.2026, measured 21:20: probe 25.06 °C, 45.5 %, VPD 1.73 kPa, early flower card humidity 45 to
60 %, VPD 1.0 to 1.4 kPa): above about 22 °C the card's two bands contradict each other, 45 % at 25 °C is 1.74 kPa, and
the rule held at 45.5 % while the VPD stood 0.33 over its maximum. With the lights on and a fresh probe temperature the
rule therefore switches on below humidity_floor(): the humidity at which the air VPD (the box's Tetens formula, no leaf
offset, vgrow/demo.py vpd) equals the stage's VPD maximum, never below the card's humidity minimum and never above its
maximum minus FLOOR_HEADROOM, so the band keeps a hysteresis. Off at the card's maximum; in between the state
holds. Lights off or unknown, no probe temperature or no VPD band: the card's minimum as before. Every gate above holds
unchanged; the "on" names its reason ("vpd_high" or "too_dry") with the floor and the temperature it came from.

An owner may lower the off target for the current confirmed stage period. It persists across restarts, expires
when that stage period changes, never raises the card maximum and leaves at least FLOOR_HEADROOM above the minimum.
The VPD floor is capped below this target too; a lower off target must never leave the restart threshold above it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from datetime import datetime
from typing import Any

from . import config
from .budget import BUDGET, BudgetExceeded, CommandBudget
from .humidifier_tank import HumidifierTank
from .i18n import fmt_qty, localize, t
from .plant import PlantLog, current_stage, load_profile, profile_problem
from .portguard import CHOICES, PortGuard

_LOGGER = logging.getLogger(__name__)

PORT = "usb2"
METRIC = "probe_humidity"
VPD_METRIC = "probe_vpd"
FLOOR_HEADROOM = 3.0  # the effective floor stays at least this many points below the card's humidity maximum
MIN_SWITCH_SECONDS = 15 * 60  # restart delay only; reaching the humidity target must switch off on the next poll
MANUAL_PAUSE_SECONDS = 60 * 60
STUCK_HIGH_SECONDS = 60 * 60
STALE_SECONDS = 10 * 60  # older readings never switch the humidifier on
FAILSAFE_SECONDS = 15 * 60  # this old, or this long without a probe value, with the humidifier on: off
CONFIRM_SECONDS = 20  # the box confirms a command within 15 s (box.PENDING_SECONDS) or not at all
RECENT = 5
RETRY_BACKOFF = (90, 180, 360, 720, 900)  # seconds before an unconfirmed off goes out again, then every 15 min
PLAUSIBLE_RISE = 3.0  # points the probe humidity must rise after the rule switched the humidifier on ...
PLAUSIBLE_SECONDS = 20 * 60  # ... within this time (own choice; 22.10.: +12.5 points in 32 minutes)
# What "recent" (the page's list) shows: what happened at the box. "failsafe_unconfirmed" and the pauses that
# switched nothing ("paused_unconfirmed_stage", "paused_unreadable_profile") stay in the file only.
ACTIONS = ("humidifier_on", "humidifier_off", "paused_manual", "automation_on", "automation_off", "failsafe_off",
           "stage_pause_off", "profile_pause_off", "implausible_off")
PAUSE_FLAGS = ("stage_pause_done", "stage_pause_tried", "stage_pause_pushed")
PROFILE_FLAGS = ("profile_pause_done", "profile_pause_tried", "profile_pause_pushed")
# Per pause kind: the reason in last_action and the log, what check() returns, and the log action of its off.
PAUSE_REASON = {"stage": "unconfirmed_stage", "profile": "unreadable_profile"}
PAUSE_RESULT = {"stage": "paused_unconfirmed_stage", "profile": "paused_unreadable_profile"}
PAUSE_OFF = {"stage": "stage_pause_off", "profile": "profile_pause_off"}


def stage_confirmed(stage: dict[str, Any] | None) -> bool:
    """Only a stage the owner set counts; an estimate or the days since the start never set the band."""
    return bool(stage) and (stage or {}).get("set_by") == "manual"


def humidity_band(stage: dict[str, Any] | None) -> tuple[float | None, float | None]:
    for target in (stage or {}).get("targets", []):
        if target.get("metric") == METRIC:
            return target.get("min"), target.get("max")
    return None, None


def vpd_band(stage: dict[str, Any] | None) -> tuple[float | None, float | None]:
    for target in (stage or {}).get("targets", []):
        if target.get("metric") == VPD_METRIC:
            return target.get("min"), target.get("max")
    return None, None


def saturation_kpa(temp: float) -> float:
    """Saturation vapour pressure over water in kPa (Tetens), the formula of the box's own air VPD (vgrow/demo.py)."""
    return 0.6108 * math.exp(17.27 * temp / (temp + 237.3))


def lights_on(snap: dict[str, Any]) -> bool | None:
    """Whether the lamp is on by the reported level (as the autopilot reads it), None without a report."""
    level = (snap.get("light") or {}).get("level")
    if isinstance(level, bool) or not isinstance(level, int | float):
        return None
    return level > 0


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def humidity_floor(stage: dict[str, Any] | None, snap: dict[str, Any], *, off_at: float | None = None) -> dict[str, Any]:
    """The humidity below which the rule switches the humidifier on, computed in this ONE place.

    With the lights on, fresh readings and a probe temperature: the relative humidity at which the air VPD equals the
    stage's VPD maximum, 100 * (1 - VPD_max / es(T)) with es the Tetens saturation pressure, never below the card's
    humidity minimum and never above its maximum minus FLOOR_HEADROOM (the band keeps a hysteresis). Otherwise the
    card's minimum. Returns {"floor", "reason" ("vpd" when the VPD raised it, else "card"), "card_min", "temp" (the
    probe temperature it was computed for, or None), "vpd_max", "clamped" (cut to the maximum minus FLOOR_HEADROOM)}.
    off_at optionally lowers that maximum to the owner's switch-off target; it never raises the card's limit."""
    lo, hi = humidity_band(stage)
    out: dict[str, Any] = {"floor": lo, "reason": "card", "card_min": lo, "temp": None, "vpd_max": None,
                           "clamped": False}
    if not _number(lo) or not _number(hi):
        return out
    if off_at is not None:
        hi = min(hi, off_at)
    _, vmax = vpd_band(stage)
    temp = (snap.get("climate") or {}).get("probe_temp")
    if not _number(vmax) or not _number(temp) or lights_on(snap) is not True or is_stale(snap.get("sensors_age")):
        return out
    needed = 100 * (1 - float(vmax) / saturation_kpa(float(temp)))
    top = float(hi) - FLOOR_HEADROOM
    floor = round(max(float(lo), min(needed, top)), 1)
    out.update(temp=float(temp), vpd_max=float(vmax), clamped=needed > top > float(lo))
    if floor > float(lo):
        out.update(floor=floor, reason="vpd")
    return out


def is_stale(age: Any) -> bool:
    """Too old to act on: no reading at all, or older than STALE_SECONDS."""
    return age is None or age > STALE_SECONDS


def spell(snap: dict[str, Any]) -> str | None:
    """Why the automation cannot act on this poll: "stale" readings, a "missing" probe value, or None."""
    if is_stale(snap.get("sensors_age")):
        return "stale"
    if (snap.get("climate") or {}).get(METRIC) is None:
        return "missing"
    return None


def _entry(raw: Any) -> dict[str, Any] | None:
    """A log line in the page's shape: epoch seconds, action, value or None."""
    if not isinstance(raw, dict) or raw.get("action") not in ACTIONS:
        return None
    when = raw.get("time")
    try:
        epoch = float(when) if isinstance(when, int | float) else datetime.fromisoformat(str(when)).timestamp()
    except ValueError:
        return None
    value = raw.get("value")
    out = {"time": epoch, "action": raw["action"], "value": value if _number(value) else None}
    if raw.get("reason") in ("vpd_high", "too_dry"):  # why the rule switched on, with the floor and its temperature
        out.update(reason=raw["reason"], floor=raw.get("floor") if _number(raw.get("floor")) else None,
                   temp=raw.get("temp") if _number(raw.get("temp")) else None)
    return out


class HumidifierAutomation:
    port = PORT

    def __init__(self, box: Any, alerts: Any, plant_log: PlantLog, guard: PortGuard | None = None,
                 budget: CommandBudget | None = None) -> None:
        self.box = box
        self.alerts = alerts
        self.plant_log = plant_log
        self.guard = guard or PortGuard("box-1")
        self.budget = budget or BUDGET
        self._sent = False  # whether the last _off_confirmed really sent (a refused send starts no retry wait)
        self.state_path = config.STATE_DIR / "automation-state.json"
        self.log_path = config.STATE_DIR / "automation-log.jsonl"
        self.state: dict[str, Any] = self._load()
        self.recent: deque[dict[str, Any]] = self._read_recent()
        self.tank = HumidifierTank()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        # failsafe_done: this spell is settled (the box confirmed off, or the owner took over).
        # failsafe_tried: an off in this spell is not confirmed yet; its push went out.
        # stage_pause_*: the same for a pause without a confirmed stage, plus whether its push went out.
        # profile_pause_*: the same for a plant.json that cannot be read.
        return {"enabled": False, "paused_until": None, "last_switch": None, "high_since": None,
                "pushed": False, "last_action": None, "failsafe_done": False, "failsafe_tried": False,
                "missing_since": None, "retry_at": None, "retries": 0, "port_hold": None, "budget_logged": False,
                "on_probe": None, "implausible": None, "implausible_pending": False,
                **{flag: False for flag in PAUSE_FLAGS + PROFILE_FLAGS}, **data}

    def _save(self) -> None:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def _read_recent(self) -> deque[dict[str, Any]]:
        """The newest entries from the end of the log; the file only ever grows, so read just its tail."""
        out: deque[dict[str, Any]] = deque(maxlen=RECENT)
        try:
            with self.log_path.open("rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 16384))
                lines = f.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                entry = _entry(json.loads(line))
            except ValueError:
                continue  # the first line of the tail may be cut
            if entry:
                out.append(entry)
        return out

    def _log(self, entry: dict[str, Any], at: float | None = None) -> None:
        when = time.time() if at is None else at
        line = {"time": datetime.fromtimestamp(when).isoformat(timespec="seconds"), **entry}
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        parsed = _entry({**line, "time": when})
        if parsed:
            self.recent.append(parsed)

    def blocked(self) -> tuple[str | None, dict[str, Any] | None]:
        """Why the automation may not regulate now, and the current stage: "profile" (plant.json cannot be read and
        no readable version is known), "stage" (no stage the owner set) or None."""
        profile = load_profile()
        if profile is None and profile_problem():
            return "profile", None
        stage = current_stage(profile or {}, self.plant_log)
        return (None if stage_confirmed(stage) else "stage"), stage

    def stage(self) -> dict[str, Any] | None:
        """The current stage with its set_by: manual, estimate or days (plant.current_stage)."""
        return self.blocked()[1]

    def _band(self, stage: dict[str, Any] | None) -> tuple[float | None, float | None]:
        """The card's limits with an optional lower off target for this confirmed stage period only."""
        lo, hi = humidity_band(stage)
        target = self.state.get("humidity_target")
        if isinstance(target, dict) and stage_confirmed(stage):
            off = target.get("off_at")
            if (target.get("stage") == stage.get("key") and target.get("since") == stage.get("since")
                    and _number(off) and math.isfinite(off) and _number(lo) and _number(hi)
                    and lo + FLOOR_HEADROOM <= off <= hi):
                hi = float(off)
        return lo, hi

    def band(self) -> tuple[float | None, float | None]:
        return self._band(self.stage())

    def set_target(self, off_at: Any) -> None:
        """Save an owner target without switching a port or clearing any manual pause. None restores the card."""
        if off_at is None:
            target = None
        else:
            kind, stage = self.blocked()
            if kind is not None:
                raise ValueError(t("auto.target_stage"))
            lo, hi = humidity_band(stage)
            if not _number(lo) or not _number(hi):
                raise ValueError(t("auto.target_stage"))
            if not _number(off_at) or not math.isfinite(off_at) or not lo + FLOOR_HEADROOM <= off_at <= hi:
                raise ValueError(t("auto.target_range", lo=fmt_qty(lo + FLOOR_HEADROOM, "%"), hi=fmt_qty(hi, "%")))
            target = {"stage": stage["key"], "since": stage.get("since"), "off_at": float(off_at)}
        self.state["humidity_target"] = target
        self._log({"action": "humidity_target", "target": target})
        self._save()

    def set_enabled(self, on: bool) -> None:
        # A fresh start either way: no pause, and no fail-safe or pause left over from before.
        self.state.update(enabled=bool(on), paused_until=None, failsafe_done=False, failsafe_tried=False,
                          retry_at=None, retries=0, **{flag: False for flag in PAUSE_FLAGS + PROFILE_FLAGS})
        self._log({"action": "automation_on" if on else "automation_off"})
        self._save()

    def manual_switch(self) -> None:
        """The owner switched the port by hand: step back for an hour instead of fighting them.
        In a spell that also means the owner has taken over: the fail-safe leaves this spell alone.
        In a pause likewise: the pause does not switch off what the owner switched on."""
        self.state["on_probe"] = None  # the owner switched: the plausibility stop watches only the rule's own "on"
        if spell(self.box.snapshot()) is not None:
            # The fail-safe watches with the rule off too: a switch by hand in a spell is the owner taking over.
            self.state.update(failsafe_done=True, failsafe_tried=False)
        if not self.state["enabled"]:
            self._save()
        if self.state["enabled"]:
            self.state["paused_until"] = time.time() + MANUAL_PAUSE_SECONDS
            kind = self.blocked()[0]
            if kind is not None:
                self.state.update({f"{kind}_pause_done": True, f"{kind}_pause_tried": False})
            self._log({"action": "paused_manual"})
            self._save()

    def _track(self, why: str | None, now: float) -> None:
        """Spell bookkeeping, on every poll and also while disabled, so a new spell always starts clean."""
        before = dict(self.state)
        if why == "missing":
            self.state["missing_since"] = self.state.get("missing_since") or now
        else:
            self.state["missing_since"] = None
        if why is None:  # fresh readings with a probe value: the spell is over, the next one may act again
            self.state.update(failsafe_done=False, failsafe_tried=False)
            if not any(self.state.get(f) for f in ("stage_pause_tried", "profile_pause_tried")):
                self.state.update(retry_at=None, retries=0)
        if self.state != before:
            self._save()

    async def check(self, snap: dict[str, Any]) -> str | None:
        """Run after every sensor poll. Returns what it did, for tests and the log.

        Order (review 2, M1; review 1, S1): a port that ports.json or the owner names as something else is never
        touched. Then the fail-safe, whether the rule is on or off: it only ever switches off, and the autopilot's or
        the owner's off must never leave a running humidifier unwatched. Then, with the rule on, everything toward
        off (the stage or profile pause, humidity over the band, the plausibility stop) runs even while the port
        guard holds; only switching on waits for a confirmed assignment the owner named."""
        now = time.time()
        why = spell(snap)
        self._track(why, now)
        port = (snap.get("ports") or {}).get(PORT) or {}
        hold = self.guard.may_switch_humidifier(PORT, snap)
        self.tank.observe(snap, assigned=self.guard.owner_device(PORT) == "humidifier" and hold is None,
                          off_at=self.band()[1], now=now)
        if hold == "not_humidifier":
            return await self._port_hold(hold) if self.state["enabled"] else None
        if why == "stale":
            return await self._failsafe(why, snap.get("sensors_age"), port, now)  # never switch on with old data
        if why == "missing":
            return await self._failsafe(why, now - self.state["missing_since"], port, now)
        if not self.state["enabled"]:
            return None
        if hold is None:
            self._port_hold_end()
        else:
            await self._port_hold(hold)  # logged and pushed once; it holds only an "on" (below)
        kind, stage = self.blocked()
        if kind != "profile":
            self._end_pause("profile")  # plant.json reads again (or never failed)
        if kind is not None:
            return await self._pause(kind, stage, port, now)  # never on; a running humidifier goes off once
        self._end_pause("stage")
        value = snap["climate"][METRIC]
        lo, hi = self._band(stage)
        if lo is None or hi is None or port.get("on") is None:
            return None
        floor = humidity_floor(stage, snap, off_at=hi)
        on_below = floor["floor"]
        self._watch_stuck(value, hi, port.get("on"), now)
        implausible = await self._plausible(value, port, now)
        if implausible is not None:
            return implausible
        if self.state["paused_until"] and now < self.state["paused_until"]:
            return None
        if not port.get("can_write"):
            return None  # auto or plan mode in the app: not ours to switch
        want = False if value >= hi else True if value < on_below else None
        if want is True and port["on"] is False and (hold is not None or self.state.get("implausible")):
            return f"paused_port_{hold}" if hold is not None else "paused_implausible"
        if want is None or want == port["on"]:
            return None
        last_at = self.state["last_switch"]
        if want and last_at and now - last_at < MIN_SWITCH_SECONDS:
            return None  # avoid rapid restarts, but never delay the target's off
        try:  # classify an off as a safety command in the shared history
            await self.budget.send("humidifier", f"port.{PORT}", lambda: self.box.set_port(PORT, want),
                                   safety=not want)
        except BudgetExceeded as err:
            if not self.state.get("budget_logged"):
                self.state["budget_logged"] = True
                self._log({"action": "budget_wait", "value": None, "until": err.until})
                self._save()
            return "budget_wait"
        except Exception as err:  # no live connection, or a refused command: the next poll tries again
            _LOGGER.warning("Humidifier %s not sent: %s", "on" if want else "off", err)
            return None
        action = "humidifier_on" if want else "humidifier_off"
        entry: dict[str, Any] = {"action": action, "value": value, "time": now}
        line: dict[str, Any] = {"action": action, "value": value, "band": [lo, hi]}
        if want:
            # Why: below the card's minimum it is too dry; between it and the floor the VPD is too high.
            reason = "too_dry" if value < lo else "vpd_high"
            entry.update(reason=reason, floor=on_below, temp=floor["temp"])
            line.update(reason=reason, floor=on_below, temp=floor["temp"], vpd=snap["climate"].get(VPD_METRIC),
                        vpd_max=floor["vpd_max"])
        self.state.update(last_switch=now, last_action=entry, budget_logged=False,
                          on_probe={"at": now, "value": value} if want else None)
        self._log(line)
        self._save()
        if want and line["reason"] == "vpd_high":
            _LOGGER.info("Humidifier on at %.1f %%: VPD too high, floor %.1f %% at %.1f °C (band %s-%s)", value,
                         on_below, floor["temp"], lo, hi)
        else:
            _LOGGER.info("Humidifier %s at %.1f %% (band %s-%s)", "on" if want else "off", value, lo, hi)
        return action

    async def _plausible(self, value: float, port: dict[str, Any], now: float) -> str | None:
        """The plausibility stop (review 2, M3): after the rule switched the humidifier on, the probe humidity must
        rise by PLAUSIBLE_RISE points within PLAUSIBLE_SECONDS. If it does not, whatever hangs on USB 2 may not be
        the humidifier (a pump), or it has no water: off (a safety command), the rule stops switching on until the
        owner names the device on USB 2 again, and one push asks. Returns "implausible_off" when it acted."""
        implausible = self.state.get("implausible")
        if implausible:
            named = self.guard.assigned_at(PORT)
            if named is not None and named > implausible:
                self.state["implausible"] = None  # the owner named the device again after the stop
                self._save()
        if self.state.get("implausible_pending"):  # the stop's off was not confirmed: again, with the backoff
            if port.get("on") is not True:
                self.state.update(implausible_pending=False, retry_at=None, retries=0)
                self._save()
            elif port.get("can_write") and self._retry_due(now):
                if await self._off_confirmed("Plausibility stop"):
                    self.state.update(implausible_pending=False, retry_at=None, retries=0)
                else:
                    self._schedule_retry(now)
                self._save()
                return "implausible_off"
        probe = self.state.get("on_probe")
        if not isinstance(probe, dict):
            return None
        if port.get("on") is False:
            self.state["on_probe"] = None
            self._save()
            return None
        if value - probe["value"] >= PLAUSIBLE_RISE:
            self.state["on_probe"] = None
            self._save()
            return None
        if now - probe["at"] < PLAUSIBLE_SECONDS or port.get("on") is not True:
            return None
        self.state.update(on_probe=None, implausible=now)
        sent = await self._off_confirmed("Plausibility stop")
        if not sent:
            self.state["implausible_pending"] = True
            self._schedule_retry(now)
        if sent:
            self.state.update(last_switch=now, last_action={"action": "humidifier_off", "value": value, "time": now,
                                                            "reason": "implausible"})
        self._log({"action": "implausible_off", "value": value, "start": probe["value"], "confirmed": sent})
        self._save()
        _LOGGER.warning("Plausibility stop: probe %.1f %% -> %.1f %% in %d min after humidifier on; off, rule waits",
                        probe["value"], value, (now - probe["at"]) // 60)
        if self.alerts is not None:
            await self.alerts.push(t("auto.implausible_title"),
                                   t("auto.implausible_body", port=PORT.replace("usb", "USB "),
                                     start=fmt_qty(probe["value"], "%", digits=0), now=fmt_qty(value, "%", digits=0),
                                     minutes=int(PLAUSIBLE_SECONDS // 60)), priority=4)
        return "implausible_off"

    async def _failsafe(self, why: str, age: Any, port: dict[str, Any], now: float) -> str | None:
        """The humidifier on in a spell: off, settled only by the box's report. Not confirmed: one push,
        then the command again on every poll (an off for a box that is offline lands once it is back)."""
        if age is not None and age <= FAILSAFE_SECONDS:
            return None
        if self.state.get("failsafe_done"):
            return None
        if port.get("on") is False and self.state.get("failsafe_tried"):
            return await self._settled(why, age, now, late=True)  # the box took the off after all
        if port.get("on") is not True or not port.get("can_write"):
            return None
        if self.state.get("failsafe_tried") and not self._retry_due(now):
            return "failsafe_unconfirmed"  # the off went out and was not confirmed: wait before the next one
        if await self._off_confirmed("Fail-safe"):
            return await self._settled(why, age, now)
        first = not self.state.get("failsafe_tried")
        self.state["failsafe_tried"] = True
        self._schedule_retry(now)
        self._save()
        _LOGGER.warning("Fail-safe: the box does not confirm the humidifier off (%s, %s s)", why, age)
        if first:  # the log file keeps it; "recent" only lists what really happened at the box
            self._log({"action": "failsafe_unconfirmed", "value": None, "reason": why, "age": age})
            await self._push(why, age, "unconfirmed")
        return "failsafe_unconfirmed"

    async def _off_confirmed(self, who: str) -> bool:
        """The confirmed off of the fail-safe and the stage pause: send it, then wait for the box's report. A safety
        command: counted in the budget, never held back by it."""
        self._sent = False
        try:
            await self.budget.send("humidifier", f"port.{PORT}", lambda: self.box.set_port(PORT, False), safety=True)
            self._sent = True
            return await self._reports_off()
        except Exception as err:  # e.g. the cloud is down as well: no live connection to send on
            _LOGGER.warning("%s could not switch the humidifier off: %s", who, err)
            return False

    def _retry_due(self, now: float) -> bool:
        at = self.state.get("retry_at")
        return at is None or now >= at

    def _schedule_retry(self, now: float) -> None:
        """After an off that went out and was not confirmed: the next one after 90 s, 3, 6, 12, then 15 min. A send
        that was refused before it left (no connection) is no attempt and starts no wait."""
        if not self._sent:
            return
        n = int(self.state.get("retries") or 0)
        self.state.update(retries=n + 1, retry_at=now + RETRY_BACKOFF[min(n, len(RETRY_BACKOFF) - 1)])

    async def _port_hold(self, kind: str) -> str:
        """The port guard says no to switching on (vgrow/portguard.py): USB 2 is something else ("not_humidifier":
        nothing at all is switched), the owner has not named the humidifier there ("unassigned"), or the assignment
        changed or is not reported yet. Logged and pushed once per kind."""
        if kind == "unknown":
            return "paused_port_unknown"
        if self.state.get("port_hold") != kind:
            self.state["port_hold"] = kind
            self._log({"action": "paused_port", "value": None, "reason": kind})
            self._save()
            if kind in ("changed", "unassigned") and self.alerts is not None:
                key = "auto.port_body" if kind == "changed" else "auto.port_unassigned_body"
                await self.alerts.push(t("auto.port_title" if kind == "changed" else "auto.port_unassigned_title"),
                                       t(key, port=PORT.replace("usb", "USB ")), priority=4 if kind == "changed" else 3)
        return f"paused_port_{kind}"

    def _port_hold_end(self) -> None:
        if self.state.get("port_hold"):
            self.state["port_hold"] = None
            self._save()

    async def _pause(self, kind: str, stage: dict[str, Any] | None, port: dict[str, Any], now: float) -> str | None:
        """kind "stage" (no confirmed stage) or "profile" (plant.json unreadable): the humidifier is never switched
        on. If it runs, it goes off once (settled only by the box's report, sent again on every poll until then), and
        one push names the cause. Once settled, nothing more happens until the cause is gone."""
        done, tried, pushed = (f"{kind}_pause_{flag}" for flag in ("done", "tried", "pushed"))
        if self.state.get(done):
            return None
        on = port.get("on")
        if on is None:
            return None  # no report from the port yet: decide on the next poll, never on a guess
        if on is False:  # off by hand, in the app, or our earlier off has landed
            was_tried = bool(self.state.get(tried))
            return await self._pause_settled(kind, stage, now, switched=was_tried, late=was_tried)
        if not port.get("can_write"):  # on, but on auto or a plan in the app: not ours to switch
            if not self.state.get(pushed):
                self.state[pushed] = True
                self._save()
                await self._push_pause(kind, stage, "not_switchable")
            return None
        if self.state.get(tried) and not self._retry_due(now):
            return f"{kind}_pause_unconfirmed"  # sent and not confirmed: wait before the next one
        if await self._off_confirmed(f"{kind.capitalize()} pause"):
            return await self._pause_settled(kind, stage, now, switched=True)
        first = not self.state.get(tried)
        self.state[tried] = True
        self._schedule_retry(now)
        self._save()
        _LOGGER.warning("%s pause: the box does not confirm the humidifier off", kind.capitalize())
        if first:  # the log file keeps it; "recent" only lists what really happened at the box
            self._log({"action": "failsafe_unconfirmed", "value": None, "reason": PAUSE_REASON[kind]})
            if not self.state.get(pushed):
                self.state[pushed] = True
                self._save()
                await self._push_pause(kind, stage, "unconfirmed")
        return f"{kind}_pause_unconfirmed"

    async def _pause_settled(self, kind: str, stage: dict[str, Any] | None, now: float, *, switched: bool,
                             late: bool = False) -> str:
        self.state.update({f"{kind}_pause_done": True, f"{kind}_pause_tried": False, "retry_at": None, "retries": 0})
        if switched:  # last_action keeps the page's on/off meaning, the reason says why
            self.state["last_action"] = {"action": "humidifier_off", "value": None, "time": now,
                                         "reason": PAUSE_REASON[kind]}
            if kind == "stage":  # a confirmed stage then waits like after any switch; a fixed plant.json does not
                self.state["last_switch"] = now
        # The off itself goes to "recent" (the page's timeline); a pause that switched nothing stays in the file.
        self._log({"action": PAUSE_OFF[kind] if switched else PAUSE_RESULT[kind], "value": None,
                   "switched_off": switched, "reason": PAUSE_REASON[kind],
                   "stage": (stage or {}).get("key"), "set_by": (stage or {}).get("set_by")})
        pushed = bool(self.state.get(f"{kind}_pause_pushed"))
        self.state[f"{kind}_pause_pushed"] = True
        self._save()
        if kind == "stage":
            _LOGGER.warning("Humidifier automation paused: stage %s not confirmed (%s)%s", (stage or {}).get("key"),
                            (stage or {}).get("set_by"), ", humidifier off, confirmed by the box" if switched else "")
        else:
            _LOGGER.warning("Humidifier automation paused: plant.json unreadable%s",
                            ", humidifier off, confirmed by the box" if switched else "")
        if late:
            await self._push(PAUSE_REASON[kind], None, "late")
        elif not pushed:
            await self._push_pause(kind, stage, "off" if switched else "paused")
        return PAUSE_RESULT[kind]

    def _end_pause(self, kind: str) -> None:
        """The cause is gone (the stage is confirmed, plant.json reads again): that pause is over, and the next time
        it acts once again."""
        flags = PAUSE_FLAGS if kind == "stage" else PROFILE_FLAGS
        if any(self.state.get(flag) for flag in flags):
            self.state.update({flag: False for flag in flags})
            self._save()

    async def _push_pause(self, kind: str, stage: dict[str, Any] | None, outcome: str) -> None:
        if self.alerts is None:
            return
        label = PORT.replace("usb", "USB ")
        if kind == "profile":
            title, cause, closing = (t("auto.profile_title"), t("auto.profile_cause", error=profile_problem() or "?"),
                                     t("auto.profile_paused"))
        else:
            shown = localize(stage) if stage else None  # the server language: nobody is asking
            cause = (t("auto.stage_cause", stage=shown.get("label") or shown["key"]) if shown
                     else t("auto.stage_cause_none"))
            title, closing = t("auto.stage_title"), t("auto.stage_paused")
        middle = ""
        if outcome == "off":
            middle = t("auto.stage_off", port=label)
        elif outcome == "not_switchable":
            middle = t("auto.stage_not_switchable", port=label)
        elif outcome == "unconfirmed":
            title, middle = t("auto.failsafe_title_unconfirmed"), t("auto.failsafe_unconfirmed", port=label)
        body = " ".join(part for part in (cause, middle, closing) if part)
        await self.alerts.push(title, body, priority=4 if outcome == "unconfirmed" else 3)

    async def _reports_off(self) -> bool:
        """True once the box reports the port off; False when it rejects, times out or stays silent."""
        deadline = time.time() + CONFIRM_SECONDS
        while True:
            snap = self.box.snapshot()
            if ((snap.get("ports") or {}).get(PORT) or {}).get("on") is False:
                return True
            status = ((snap.get("pending") or {}).get(f"port.{PORT}") or {}).get("status")
            if status in ("rejected", "timeout") or time.time() >= deadline:
                return False
            await asyncio.sleep(1)

    async def _settled(self, why: str, age: Any, now: float, *, late: bool = False) -> str:
        # last_action keeps the page's on/off meaning; the log and "recent" say it was the fail-safe.
        self.state.update(failsafe_done=True, failsafe_tried=False, last_switch=now, retry_at=None, retries=0,
                          last_action={"action": "humidifier_off", "value": None, "time": now, "reason": "failsafe"})
        self._log({"action": "failsafe_off", "value": None, "reason": why, "age": age})
        self._save()
        _LOGGER.warning("Fail-safe: humidifier off (%s, %s s), confirmed by the box", why, age)
        await self._push(why, age, "late" if late else "off")
        return "failsafe_off"

    async def _push(self, why: str, age: Any, outcome: str) -> None:
        if self.alerts is None:
            return
        label = PORT.replace("usb", "USB ")
        if outcome == "late":
            await self.alerts.push(t("auto.failsafe_title_late"), t("auto.failsafe_late", port=label), priority=3)
            return
        if age is None:
            cause = t("auto.failsafe_never")
        else:
            cause = t("auto.failsafe_probe" if why == "missing" else "auto.failsafe_stale", minutes=int(age // 60))
        if outcome == "off":
            title = t("auto.failsafe_title_probe" if why == "missing" else "auto.failsafe_title")
            body = f"{cause} {t('auto.failsafe_off', port=label)}"
        else:
            title, body = t("auto.failsafe_title_unconfirmed"), f"{cause} {t('auto.failsafe_unconfirmed', port=label)}"
        await self.alerts.push(title, body, priority=4)

    def _watch_stuck(self, value: float, hi: float, is_on: bool, now: float) -> None:
        if value > hi and not is_on:
            self.state["high_since"] = self.state["high_since"] or now
            if now - self.state["high_since"] >= STUCK_HIGH_SECONDS and not self.state["pushed"]:
                self.state["pushed"] = True
                self._save()
                if self.alerts is not None:
                    asyncio.ensure_future(self.alerts.push(
                        t("auto.stuck_title"),
                        t("auto.stuck_body", value=fmt_qty(value, "%", digits=0), limit=fmt_qty(hi, "%", digits=0)),
                        priority=4))
        elif value <= hi:
            if self.state["high_since"] or self.state["pushed"]:
                self.state.update(high_since=None, pushed=False)
                self._save()

    def view(self, snap: dict[str, Any] | None = None) -> dict[str, Any]:
        """The page's view. needs_stage_confirmation: the stage is not set by the owner, so the automation
        does not switch on (true whether or not it is enabled). estimate: the suggested stage for a one-tap
        confirm with the existing plant_stage action, or None when there is no stage at all. profile_problem: why
        plant.json cannot be read, while no readable version is known (then no stage is known either)."""
        snap = self.box.snapshot() if snap is None else snap
        kind, stage = self.blocked()
        lo, hi = self._band(stage)
        floor = humidity_floor(stage, snap, off_at=hi)
        paused = self.state["paused_until"]
        why = spell(snap)
        needs = not stage_confirmed(stage)
        estimate = None
        if needs and stage:
            shown = localize(stage)  # the request's language
            estimate = {"key": stage["key"], "label": shown.get("label") or stage["key"], "set_by": stage.get("set_by")}
        vmin, vmax = vpd_band(stage)
        climate = snap.get("climate") or {}
        vpd_now, hum_now = climate.get(VPD_METRIC), climate.get(METRIC)
        fresh = why is None and _number(vpd_now)
        # What the rule sees now: "too_dry" below the card's minimum, "vpd_high" between it and the effective floor.
        need = None
        if why is None and _number(hum_now) and _number(lo) and _number(floor["floor"]):
            need = "too_dry" if hum_now < lo else "vpd_high" if hum_now < floor["floor"] else None
        return {"port": PORT, "enabled": self.state["enabled"], "band": [lo, hi],
                "card_band": list(humidity_band(stage)),
                # floor: below this the rule switches on now (humidity_floor); reason "vpd" when the VPD raised it
                "floor": floor, "need": need,
                "vpd": {"value": vpd_now if fresh else None, "band": [vmin, vmax],
                        "high": bool(fresh and _number(vmax) and vpd_now > vmax)},
                "paused_until": paused if paused and paused > time.time() else None,
                "last_action": self.state["last_action"],
                "recent": [dict(e) for e in reversed(self.recent)],
                "stale": why == "stale", "missing": why == "missing",
                "needs_stage_confirmation": needs, "estimate": estimate,
                "profile_problem": profile_problem() if kind == "profile" else None,
                "port_hold": self.state.get("port_hold") if self.state["enabled"] else None,
                "port_device": self.guard.owner_device(PORT), "port_choices": list(CHOICES),
                "implausible": bool(self.state.get("implausible")),
                "tank": self.tank.view(implausible=bool(self.state.get("implausible")))}
