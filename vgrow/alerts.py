"""Threshold alerts to the phone via ntfy. Fires on the change into a bad state, not every poll.

Rules live in alerts.json. A rule may name a "fallback" metric, read only when its own metric has no
value (the probe at leaf height first, the built-in sensor without it). The fallback never decides about
an alarm, or a hold, that the rule's own metric started: one poll without a probe value must neither
send a false all-clear nor restart the hold time.
A rule with "stage_max_margin" follows the plant stage (the cockpit's own rule, not a sourced number): its
limit is the current stage's maximum for the rule's metric plus the margin. A stage the owner set may move the limit
either way. A stage that is only estimated (or taken from the days since the start) may only tighten it, never
raise it above the rule's fixed limit: the alarm is the safety net while the automation waits for a confirmed
stage, so it does not loosen on the same unconfirmed stage (review of 22.10.2026). Without such a stage the rule's
fixed limit applies. The stage comes in the snapshot under "stage" (server.poll_handler puts it there).
Texts come from the catalog in the server language: this runs after every poll, outside any request.
The rule on "water_state" (tank_empty) reads the type-9 wet/dry sensor (box.water_view): it holds while the sensor
is dry, its for_minutes is the debounce against a short wobble, and stale or offline data decides nothing.
The debounce counts the readings' own time (climate "water_run_s", box.water_run), not the clock: a poll that
fails or brings no new row confirms nothing, and a wet row or a gap in between starts the run again.
The rules on "dark_period" and "short_day" read the measured last 24 hours (server.poll_handler puts light_view.LightDay
in the snapshot as "light_day"): a day with too few measured hours decides nothing. "usb_camera" is the close camera.
A rule's own "remind_hours" replaces the common one, for what the owner can only fix at the box.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import aiohttp

from . import config
from .box import WATER_STALE_SECONDS
from .i18n import current_lang, fmt_num, fmt_qty, fmt_time, localize, t

_LOGGER = logging.getLogger(__name__)

UNITS = {"temp": "°C", "probe_temp": "°C", "humidity": "%", "probe_humidity": "%", "water_level": "%"}


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def stage_limit(rule: dict[str, Any], stage: dict[str, Any] | None) -> dict[str, Any] | None:
    """For a rule with stage_max_margin: the stage's maximum for the rule's metric plus the margin, with the stage
    it came from and who set it. None without a stage that has such a maximum, and None for a stage the owner did not
    set whose limit would lie above the rule's own: then the rule's own limit applies."""
    margin = rule.get("stage_max_margin")
    if not _number(margin) or not stage:
        return None
    for target in stage.get("targets") or []:
        top = target.get("max")
        if target.get("metric") == rule["metric"] and _number(top):
            limit = top + margin
            fixed = rule.get("above")
            if stage.get("set_by") != "manual" and _number(fixed) and limit > fixed:
                return None  # an estimate may tighten the alarm, never loosen it
            return {"key": stage.get("key"), "label": stage.get("label") or stage.get("key"), "max": top,
                    "margin": margin, "limit": limit, "set_by": stage.get("set_by")}
    return None


def effective_rule(rule: dict[str, Any], stage: dict[str, Any] | None) -> dict[str, Any]:
    """The rule as it applies now: with a stage limit, 'above' is that limit and 'stage_limit' says where from."""
    found = stage_limit(rule, stage)
    return rule if found is None else {**rule, "above": found["limit"], "stage_limit": found}


def reading_value(rule: dict[str, Any], snapshot: dict[str, Any]) -> tuple[Any, str | None]:
    """The value a rule looks at and the metric it came from; the fallback only when the metric is empty."""
    climate = snapshot.get("climate") or {}
    value = climate.get(rule["metric"])
    if value is None and rule.get("fallback"):
        fallback = climate.get(rule["fallback"])
        return fallback, (rule["fallback"] if fallback is not None else None)
    return value, (rule["metric"] if value is not None else None)


# ---------- the tank (type-9 wet/dry water sensor, box.water_view) ----------
TANK_METRIC = "water_state"
TANK_STALE_SECONDS = WATER_STALE_SECONDS  # as the offline rule: three missed polls, no ground for an alarm


def tank_breached(rule: dict[str, Any], snapshot: dict[str, Any]) -> bool | None:
    """True while the sensor reports the rule's state ("is", default "dry"), False for the other one, None for an
    unknown value, no type-9 sensor, or stale or offline data: an old "dry" must neither alarm nor clear."""
    age = snapshot.get("sensors_age")
    if not snapshot.get("online") or not _number(age) or age > TANK_STALE_SECONDS:
        return None
    state = (snapshot.get("climate") or {}).get(TANK_METRIC)
    if state not in ("wet", "dry"):
        return None
    return state == rule.get("is", "dry")


def tank_held(rule: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """Whether the readings have said the rule's state for its for_minutes without a break (box.water_run).
    Without that run (an old snapshot, the demo before its first poll) the hold never counts as reached."""
    run = (snapshot.get("climate") or {}).get("water_run_s")
    return _number(run) and run >= rule["for_minutes"] * 60


def tank_confirmed(snapshot: dict[str, Any]) -> bool:
    """The tank alarm's own debounce (alerts.json tank_empty), for Hank's refill line: the readings said dry for
    the rule's minutes. Freshness is the caller's check (Hank has its own). Without the rule, or with a broken
    alerts.json, False: the line then waits for the alarm."""
    try:
        rule = config.alert_rules()["rules"]["tank_empty"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    climate = snapshot.get("climate") or {}
    return climate.get(TANK_METRIC) == rule.get("is", "dry") and tank_held(rule, snapshot)


def tank_reading(snapshot: dict[str, Any], lang: str | None = None) -> str:
    """What the sensor says now: water there, or dry since when (the time of the last change, if known)."""
    climate = snapshot.get("climate") or {}
    if climate.get(TANK_METRIC) != "dry":
        return t("alert.tank_wet", lang)
    since = climate.get("water_since")
    return t("alert.tank_dry_since", lang, time=fmt_time(since, lang)) if _number(since) else t("alert.tank_dry", lang)


# ---------- the measured day (light_view.LightDay) and the close camera ----------
DARK_METRIC = "dark_period"
SHORT_DAY_METRIC = "short_day"
USB_METRIC = "usb_camera"
NO_READING = ("camera", USB_METRIC, "offline", TANK_METRIC, DARK_METRIC, SHORT_DAY_METRIC)  # no value against a limit


def light_day_breached(rule: dict[str, Any], snapshot: dict[str, Any]) -> bool | None:
    """dark_period: under the rule's dark hours in the last 24. short_day: the light ran about the rule's hours and the
    stage's card allows other ones. None with too few measured hours, or for short_day without a card to ask."""
    day = snapshot.get("light_day")
    if not day or day["measured_hours"] < rule["min_measured_hours"]:
        return None
    if rule["metric"] == DARK_METRIC:
        return day["dark_hours"] < rule["dark_hours_below"]
    planned = day.get("hours_planned")
    if not planned:
        return None
    return rule["lit_hours"] not in planned and abs(day["lit_hours"] - rule["lit_hours"]) <= rule["within_hours"]


def breached(rule: dict[str, Any], snapshot: dict[str, Any]) -> bool | None:
    """True/False for a rule, None when the reading is missing (never alert on a guess)."""
    if rule["metric"] == TANK_METRIC:
        return tank_breached(rule, snapshot)
    if rule["metric"] in (DARK_METRIC, SHORT_DAY_METRIC):
        return light_day_breached(rule, snapshot)
    if rule["metric"] == USB_METRIC:
        cam = snapshot.get(USB_METRIC) or {}
        if not cam.get("configured") or cam.get("reachable") is None:
            return None  # no such camera, or not looked yet
        return cam["reachable"] is False
    if rule["metric"] == "camera":
        cam = snapshot.get("camera") or {}
        if cam.get("searching"):
            return True  # in the account, not found on the network (its power supply?): the hold time decides
        if not cam.get("configured") or cam.get("reachable") is None:
            return None  # no camera, or not checked yet: never alert on a guess
        return cam["reachable"] is False
    if rule["metric"] == "offline":
        age = snapshot.get("sensors_age")
        # Stale after three missed polls; the hold time below then applies as for every rule.
        return (not snapshot.get("online")) or age is None or age > 300
    if rule["metric"] == "water_level" and (snapshot.get("climate") or {}).get("water_kind") == "wet_dry":
        # A type-9 sensor reports wet or dry, never a level: the percent rule does not apply to it (tank_empty does).
        # False, not None, so an alarm raised from a dry 0 read as "0 %" before the decoding (23.10.2026) clears.
        return False
    value, _ = reading_value(rule, snapshot)
    if value is None:
        return None
    if "below" in rule and value < rule["below"]:
        return True
    if "at_most" in rule and value <= rule["at_most"]:
        return True
    if "above" in rule and value > rule["above"]:
        return True
    return False


def _limit(rule: dict[str, Any]) -> Any:
    return rule.get("below", rule.get("at_most", rule.get("above")))


def margin_text(margin: Any, unit: str, lang: str | None = None) -> str:
    """The margin with its unit: on a percent reading it is percentage points ('10 Punkte'), never '10 %'."""
    return t("alert.margin_points", lang, n=fmt_num(margin, lang)) if unit == "%" else fmt_qty(margin, unit, lang)


def stage_name(found: dict[str, Any], lang: str | None = None) -> str:
    """The stage a limit follows, marked when the owner has not set it."""
    label = found.get("label") or found.get("key")
    return label if found.get("set_by") == "manual" else t("alert.stage_estimated", lang, stage=label)


class Alerts:
    demo = False  # the demo marks every push title, so a push about made-up values is never taken for real

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self.config = config.alert_rules()
        self._state_path = config.STATE_DIR / "alerts-state.json"
        self.state: dict[str, dict[str, Any]] = self._load()
        self.target = config.ntfy()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    async def check(self, snapshot: dict[str, Any]) -> list[str]:
        """Evaluate all rules; returns the messages it pushed (for tests and the page)."""
        now = time.time()
        lang = current_lang()
        sent: list[str] = []
        stage = localize(snapshot.get("stage"), lang) if snapshot.get("stage") else None
        for name, raw in self.config["rules"].items():
            rule = effective_rule(localize(raw, lang), stage)
            remind = rule.get("remind_hours", self.config.get("remind_hours", 6)) * 3600
            hit = breached(rule, snapshot)
            st = self.state.setdefault(name, {"since": None, "active": False, "last_push": None, "source": None})
            if rule["metric"] in ("camera", USB_METRIC) and (snapshot.get(rule["metric"]) or {}).get("hidden"):
                # the owner put this camera away (camera_prefs): no alarm, and no "back" push for one that is not back
                st.update(since=None, active=False, last_push=None, source=None)
                continue
            if hit is None:
                continue
            used = None if rule["metric"] in NO_READING else reading_value(rule, snapshot)[1]
            if used is not None and used != rule["metric"] and st["since"] and st.get("source") == rule["metric"]:
                continue  # the fallback neither clears nor restarts what the rule's own metric started
            if hit:
                st["since"] = st["since"] or now
                if used is not None and st.get("source") != rule["metric"]:
                    st["source"] = used  # the own metric, once it has spoken, keeps the say
                if rule["metric"] == TANK_METRIC:
                    held = tank_held(rule, snapshot)  # the readings' time, not the poll's
                else:
                    held = now - st["since"] >= rule["for_minutes"] * 60
                due = st["last_push"] is None or now - st["last_push"] >= remind
                if held and (not st["active"] or due):
                    msg = self._message(rule, snapshot, reminder=st["active"], lang=lang, since=st["since"])
                    if await self.push(rule["label"], msg, priority=4):
                        st["last_push"] = now
                        sent.append(msg)
                    st["active"] = True
            else:
                if st["active"]:
                    msg = t("alert.recovered", lang, label=rule["label"], reading=self._reading(rule, snapshot, lang))
                    if await self.push(t("alert.recovered_title", lang), msg, priority=2):
                        sent.append(msg)
                st.update(since=None, active=False, last_push=None, source=None)
        self._save()
        return sent

    @staticmethod
    def _reading(rule: dict[str, Any], snapshot: dict[str, Any], lang: str | None = None,
                 since: float | None = None) -> str:
        """since: when the alarm's state began. It survives a restart, a camera's own offline_seconds does not."""
        metric = rule["metric"]
        if metric in ("camera", USB_METRIC):
            cam = snapshot.get(metric) or {}
            if cam.get("reachable") and not cam.get("searching"):
                return t("alert.camera_back", lang)
            secs = round(max(cam.get("offline_seconds") or 0, time.time() - since if since else 0))
            if metric == "camera":
                return t("alert.camera", lang, minutes=secs // 60)
            reason = cam.get("error") or ""
            return (t("alert.usb_camera_hours", lang, hours=secs // 3600, reason=reason) if secs >= 7200
                    else t("alert.usb_camera", lang, minutes=secs // 60, reason=reason)).strip()
        if metric in (DARK_METRIC, SHORT_DAY_METRIC):
            day = snapshot.get("light_day") or {}
            return t("alert.light_day", lang, lit=fmt_num(day.get("lit_hours"), lang),
                     dark=fmt_num(day.get("dark_hours"), lang))
        if metric == "offline":
            age = snapshot.get("sensors_age")
            return t("alert.no_data", lang) if age is None else t("alert.last_reading", lang, minutes=age // 60)
        if metric == TANK_METRIC:
            return tank_reading(snapshot, lang)
        value, used = reading_value(rule, snapshot)
        text = fmt_qty(value, UNITS.get(used or metric, ""), lang, 0 if used == "water_level" else 1)
        return t("alert.reading_fallback" if used and used != metric else "alert.reading", lang, value=text)

    def _message(self, rule: dict[str, Any], snapshot: dict[str, Any], *, reminder: bool,
                 lang: str | None = None, since: float | None = None) -> str:
        limit = ""
        unit = UNITS.get(rule["metric"], "")
        if rule["metric"] not in NO_READING:
            limit = t("alert.limit", lang, limit=fmt_qty(_limit(rule), unit, lang))
        text = t("alert.message_reminder" if reminder else "alert.message", lang,
                 label=rule["label"], limit=limit, reading=self._reading(rule, snapshot, lang, since))
        if rule["metric"] == TANK_METRIC:
            text += " " + t("alert.tank_why", lang)  # why it matters: the wick plant drinks from this tank
        elif rule["metric"] == USB_METRIC:
            text += " " + t("alert.usb_camera_why", lang)
        elif rule["metric"] == DARK_METRIC:
            text += " " + t("alert.dark_why", lang)
        elif rule["metric"] == SHORT_DAY_METRIC:
            text += " " + t("alert.short_day_why", lang, hours=(snapshot["light_day"].get("hours_planned") or ["?"])[0])
        found = rule.get("stage_limit")
        if found:  # the limit and the stage it follows, so a seedling's 85 % is not read as a typo
            text += " " + t("alert.stage_limit", lang, stage=stage_name(found, lang),
                            max=fmt_qty(found["max"], unit, lang), margin=margin_text(found["margin"], unit, lang),
                            limit=fmt_qty(found["limit"], unit, lang))
        return text

    async def push(self, title: str, message: str, *, priority: int = 3, click: str | None = None) -> bool:
        """click: a page to open when the push is tapped (ntfy's Click header); never carries the access key."""
        if self.target is None or self._session is None:
            _LOGGER.warning("No ntfy target configured; alert not sent: %s", message)
            return False
        server, topic = self.target
        headers = {"Title": t("push.title_demo" if self.demo else "push.title", title=title),
                   "Priority": str(priority), "Tags": "seedling"}
        if click:
            headers["Click"] = click
        try:
            async with self._session.post(
                f"{server}/{topic}",
                data=message.encode("utf-8"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                ok = resp.status < 300
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("ntfy push failed: %s", err)
            return False
        if not ok:
            _LOGGER.warning("ntfy answered %s", resp.status)
        return ok

    def view(self, stage: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """The rules for the page, each with the limit that applies now. stage_limit: where a stage-following
        limit comes from ({"key", "label", "max", "margin", "limit", "set_by"}), None for a fixed one."""
        lang = current_lang()
        shown = localize(stage, lang) if stage else None
        out = []
        for name, raw in self.config["rules"].items():
            rule = effective_rule(localize(raw, lang), shown)
            st = self.state.get(name, {})
            limit = _limit(rule)
            found = rule.get("stage_limit")
            out.append({
                "name": name, "label": rule["label"], "metric": rule["metric"], "fallback": rule.get("fallback"),
                "op": "<" if "below" in rule else "≤" if "at_most" in rule else ">" if "above" in rule else None,
                "limit": limit, "for_minutes": rule["for_minutes"], "active": bool(st.get("active")),
                "stage_limit": {k: found[k] for k in ("key", "label", "max", "margin", "limit", "set_by")}
                if found else None,
            })
        return out
