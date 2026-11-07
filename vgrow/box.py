"""One VGrow box: login, live shadow over MQTT, climate from the point log, guarded commands.

The protocol work lives in ``vgrow.vivosun`` (vendored, see UPSTREAM.md). This module only
wires it together for a single device and keeps the state the web page reads.

Logging in (compliance finding of 22.10.2026: on a lasting failure the old loop logged in about 90 times an
hour, each time with fresh identifiers, which VIVOSUN could read as abusive traffic):
- Network and server failures back off: 10 s, 20 s, 40 s ... at most 5 min, each wait up to 20 % shorter at random.
- A rejected login (wrong password, an account without a VGrow) stops the fast loop: a clear error on the page,
  the next try after 30 min at the earliest.
- Never more than 6 logins in any rolling hour. The attempts are kept in STATE_DIR, so restarts, a crash loop
  under launchd and a parallel --check count too.
- One spClientId, spSessionId and MQTT client id for the server, reused for every login and reconnect and kept in
  STATE_DIR/login-state.json, so a restart (or a crash loop) carries the same ones; the 30-minute pause after a
  rejection is kept there as well. --check uses its own, never stored (remember=False): it runs next to the server,
  and two connections with one MQTT client id would push each other off.
- A failed first login no longer stops the server; the supervisor retries by the same rules.

The MQTT link (review of 22.10.2026): a link that the cloud accepts and drops again within STABLE_SECONDS counts as a
failure (backoff, a reason on the page, a fresh login through the hourly gate), not as a reconnect every 10 s; the
backoff resets only once a link has stayed up that long. Never more than CONNECT_MAX_PER_HOUR connections in a rolling
hour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from . import config
from .i18n import Text, TextError, TimeOfDay, localize, reason, t
from .vivosun import shadow as sh
from .vivosun.api import VivosunApiClient
from .vivosun.aws_auth import AwsAuthClient, AwsCredentials
from .vivosun.const import (
    API_LOGIN_PATH,
    API_POINT_LOG_PATH,
    CFAN_LEVEL_MAP,
    CFAN_NATURAL_WIND_VALUE,
    DFAN_LEVEL_MAP,
    SENSOR_UNAVAILABLE_SENTINEL,
    TOPIC_SHADOW_GET,
    TOPIC_SHADOW_UPDATE_REJECTED,
)
from .vivosun.exceptions import VivosunAuthError
from .vivosun.models import AuthTokens, AwsIdentity, DeviceInfo, client_model_token
from .vivosun.mqtt_client import MQTTClient, MQTTError
from .vivosun.ports import PORTS_BY_MODEL, build_manual_port_payload, reported_port

_LOGGER = logging.getLogger(__name__)

VGROW_MODEL = "VSCTLY18"
POLL_SECONDS = 90  # same cadence as the upstream integration
# Read device status even when no actuator settings change. Under the 300 s the watering and the alerts accept: at 600 s the pump
# refused to start for five minutes out of ten, and a scheduled watering slot would have been lost the same way (06.11.2026).
SHADOW_REFRESH_SECONDS = 4 * 60
SHADOW_REFRESH_TIMEOUT_SECONDS = 15
PENDING_SECONDS = 15  # upstream: unconfirmed commands are dropped after 15 s
SUPERVISE_SECONDS = 10  # while healthy, the connection is looked at every 10 s
BACKOFF_FIRST_SECONDS = 10
BACKOFF_MAX_SECONDS = 5 * 60
BACKOFF_JITTER = 0.2  # each wait is up to 20 % shorter at random, never longer than its step
AUTH_RETRY_SECONDS = 30 * 60
LOGIN_MAX_PER_HOUR = 6
LOGIN_WINDOW_SECONDS = 3600
LOGIN_FILE = "login-attempts.json"
LOGIN_STATE_FILE = "login-state.json"  # the server's identifiers and the pause after a rejection, across restarts
STABLE_SECONDS = 60  # a link that drops sooner counts as a failure; the backoff resets only after this long up
CONNECT_MAX_PER_HOUR = 12  # MQTT connections in any rolling hour: the cockpit's own rule, not a sourced number
# App presets, from upstream select.py SPECTRUM_PRESETS (not observed on this box yet).
SPECTRUMS = {"seedling": 20, "veg": 50, "bloom": 100}

# Point-log keys and their divisor. Temperatures, humidity, VPD and the DWC water
# readings arrive as hundredths (upstream docs/VIVOSUN-API-SPEC.md, issue #43).
_SCALED = {
    "inTemp": 100, "inHumi": 100, "inVpd": 100,
    "outTemp": 100, "outHumi": 100, "outVpd": 100,
    "bTemp": 100, "bHumi": 100, "bVpd": 100,
    "pTemp": 100, "pHumi": 100, "pVpd": 100,
    "waterLv": 100, "waterT": 100, "coreTemp": 100,
}
# A row counts as a reading only with at least one of these; a row of -6666 sentinels is no reading.
_CLIMATE = ("inTemp", "inHumi", "bTemp", "bHumi", "pTemp", "pHumi")


def row_time(row: dict[str, Any], now: float) -> float | None:
    """A row's own timestamp in epoch seconds, if plausible (upstream vseed.parse_point_log: 0 < t <= now + 60)."""
    try:
        stamp = float(row.get("time"))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return stamp if math.isfinite(stamp) and 0 < stamp <= now + 60 else None


def newest_reading(rows: list[dict[str, Any]], now: float) -> tuple[float, dict[str, float | int | None]] | None:
    """The newest row with a plausible time and a climate value, scaled, with its time; None if there is none."""
    best: tuple[float, dict[str, float | int | None]] | None = None
    for row in rows:
        stamp = row_time(row, now)
        if stamp is None or (best is not None and stamp <= best[0]):
            continue
        scaled = scale_row(row)
        if any(scaled.get(key) is not None for key in _CLIMATE):
            best = (stamp, scaled)
    return best


def scale_row(row: dict[str, Any]) -> dict[str, float | int | None]:
    """Turn one raw point-log row into display units; the -6666 sentinel becomes None."""
    out: dict[str, float | int | None] = {"time": row.get("time")}
    for key, divisor in _SCALED.items():
        raw = row.get(key)
        if type(raw) is not int or raw == SENSOR_UNAVAILABLE_SENTINEL:
            out[key] = None
        else:
            out[key] = round(raw / divisor, 2)
    rssi = row.get("rssi")
    out["rssi"] = rssi if type(rssi) is int else None
    water = row.get("waterLv")
    out["waterLv_raw"] = water if type(water) is int else None  # kept for values that are not a level
    return out


def climate_view(row: dict[str, Any], aux: Any = None) -> dict[str, Any]:
    """Box climate plus the probe, without deciding which one hangs at the plant.

    Main sensor: in* (GrowHub controllers), else b* (built-in, e.g. VGrow). The probe is always p*:
    with the built-in sensor missing, its value must not pass for the box's, nor leave probe_* empty.
    """
    def val(key: str) -> float | None:
        value = row.get(key)
        return None if value is None else float(value)

    groups = [(val(f"{p}Temp"), val(f"{p}Humi"), val(f"{p}Vpd")) for p in ("in", "b", "p")]
    main = next((g for g in groups[:2] if g[0] is not None or g[1] is not None), (None, None, None))
    probe = groups[2]
    return {
        "temp": main[0], "humidity": main[1], "vpd": main[2],
        "probe_temp": probe[0], "probe_humidity": probe[1], "probe_vpd": probe[2],
        **water_view(row, aux),  # water_kind, water_state, water_level, water_raw, water_temp: by accessory type
        "room_temp": val("outTemp"),
        "room_humidity": val("outHumi"),
    }


# ---------- the water sensor, decoded by its accessory type (shadow aux35Dev, snapshot "aux_device") ----------
# Type 9 (the owner's VGrow, the sensor without a pump): waterLv is a two-state wet/dry signal, NOT a level.
# 20000 = water at the sensor, 0 = dry. This was MEASURED on 23.10.2026 with the owner's test, it is not documented
# by VIVOSUN: waterLv was 20000 every minute from 22.10. 10:19 to 23.10. 16:04, 0 from 16:05 to 16:08 while the
# owner held the sensor out of the water, and 20000 again from 16:09. waterT is null for this type.
# Type 10 (the DWC kit): waterLv in hundredths of a percent (7500 = 75 %, 10000 = 100 %, 0 % possible) and waterT
# in hundredths of a degree Celsius, as owners report in lientry/homeassistant-vivosun-growhub issue #43.
# Any other type, or no type reported: only the raw value is kept, never a guess.
AUX_WET_DRY = 9
AUX_LEVEL = 10
WATER_WET_RAW, WATER_DRY_RAW = 20000, 0
WATER_STATE_FILE = "water-state.json"  # the type-9 state and the time it last changed, across restarts
# Three missed polls: a reading this old is no ground for an alarm or a pump run (alerts.TANK_STALE_SECONDS, the
# pump hook). A gap this long between two wet/dry rows also breaks a run of readings (Box.track_water).
WATER_STALE_SECONDS = 300


def aux_type(aux: Any) -> int | None:
    """The accessory type as an int (9, 10), or None for anything else."""
    if type(aux) is int:
        return aux
    text = aux.strip() if isinstance(aux, str) else ""
    return int(text) if text.isascii() and text.isdigit() else None  # "²" is a digit to isdigit, not to int


def _water_raw(row: Mapping[str, Any]) -> int | None:
    """waterLv as the cloud sent it: waterLv_raw of a scaled row, else waterLv of a raw point-log row."""
    raw = row.get("waterLv_raw", row.get("waterLv"))
    return raw if type(raw) is int and raw != SENSOR_UNAVAILABLE_SENTINEL else None


def water_state_of(aux: Any, raw: Any) -> str | None:
    """For a type-9 sensor: "wet" (20000), "dry" (0) or "unknown" (any other value). None for another type or
    without a value."""
    if aux_type(aux) != AUX_WET_DRY or type(raw) is not int or raw == SENSOR_UNAVAILABLE_SENTINEL:
        return None
    return "wet" if raw == WATER_WET_RAW else "dry" if raw == WATER_DRY_RAW else "unknown"


def water_view(row: Mapping[str, Any], aux: Any) -> dict[str, Any]:
    """The water fields of climate_view. water_kind: "wet_dry" (type 9), "level" (type 10) or None (unknown type).
    water_raw: the raw value whenever it is not decoded (unknown type, or a value the type does not explain)."""
    raw = _water_raw(row)
    kind = aux_type(aux)
    out: dict[str, Any] = {"water_kind": None, "water_state": None, "water_level": None, "water_raw": None,
                           "water_temp": None}
    if kind == AUX_WET_DRY:
        state = water_state_of(kind, raw)
        out.update(water_kind="wet_dry", water_state=state, water_raw=raw if state == "unknown" else None)
    elif kind == AUX_LEVEL:
        level = None if raw is None or not 0 <= raw <= 10000 else round(raw / 100, 2)
        temp = row.get("waterT")  # already scaled in a scaled row (scale_row), raw hundredths otherwise
        if "waterLv_raw" not in row and type(temp) is int:
            temp = None if temp == SENSOR_UNAVAILABLE_SENTINEL else round(temp / 100, 2)
        out.update(water_kind="level", water_level=level, water_raw=raw if level is None else None,
                   water_temp=None if temp is None else float(temp))
    else:
        out["water_raw"] = raw
    return out


def _water_seen(rows: list[dict[str, Any]], aux: Any, now: float) -> list[tuple[float, str]]:
    """(row time, "wet" or "dry") of every row with a type-9 state, oldest first."""
    return sorted((stamp, state) for row in rows
                  if (stamp := row_time(row, now)) is not None
                  and (state := water_state_of(aux, _water_raw(row))) in ("wet", "dry"))


def water_run(seen: list[tuple[float, str]]) -> tuple[float, bool] | None:
    """The trailing run of the newest state in `seen` (oldest first) without a gap over WATER_STALE_SECONDS: the
    time of its first row, and whether it reaches back to the window's earliest row (then it may go on from the
    previous poll). None without rows. This, not the clock, is what the tank alarm debounces on."""
    if not seen:
        return None
    state, start = seen[-1][1], seen[-1][0]
    for stamp, other in reversed(seen[:-1]):
        if other != state or start - stamp > WATER_STALE_SECONDS:
            return start, False
        start = stamp
    return start, True


def water_change(rows: list[dict[str, Any]], aux: Any, now: float) -> tuple[str | None, float | None, bool]:
    """From one poll's rows (oldest first or not): the type-9 state of the newest row with one, the time its
    trailing run of that state began, and whether another state comes before that run in the rows (then that time
    is the change itself; without, it is only the earliest row of the window). (None, None, False) without a state."""
    seen = _water_seen(rows, aux, now)
    if not seen:
        return None, None, False
    state, start = seen[-1][1], seen[-1][0]
    for stamp, other in reversed(seen):
        if other != state:
            return state, start, True
        start = stamp
    return state, start, False


def watering_allowed(snapshot: Mapping[str, Any]) -> bool:
    """May a timed pump cycle run from this tank? Only with fresh readings and
    water at the sensor (type 9 "wet") or a level above 0 % (type 10). Dry, unknown, no sensor, stale or offline
    means no: a pump that runs dry is damaged, and the wick plant then gets nothing either."""
    if not snapshot.get("online"):
        return False
    age = snapshot.get("sensors_age")
    if not isinstance(age, int | float) or isinstance(age, bool) or not math.isfinite(age) or not 0 <= age <= WATER_STALE_SECONDS:
        return False
    climate = snapshot.get("climate") or {}
    if climate.get("water_kind") == "wet_dry":
        return climate.get("water_state") == "wet"
    level = climate.get("water_level")
    if climate.get("water_kind") == "level" and isinstance(level, int | float) and not isinstance(level, bool):
        return math.isfinite(level) and level > 0
    return False


def fan_step(shadow_level: int | None, level_map: tuple[int, ...]) -> int | None:
    """Map a raw shadow fan level back to the app's 0..10 step."""
    if shadow_level is None:
        return None
    if shadow_level in level_map:
        return level_map.index(shadow_level)
    # Not an exact table value: take the closest step rather than inventing one.
    return min(range(len(level_map)), key=lambda i: abs(level_map[i] - shadow_level))


def deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value


class CommandRefused(Exception):
    """A command the box would not accept in its current mode, refused before sending."""


def pump_off_allowed(port: str, box_id: str = "box-1") -> bool:
    """With ports.json unreadable and no good version in memory: may an OFF still go to `port`? Only for a pump,
    named in the file's raw text (config.pump_ports_raw) or by the owner on the page (the port guard's record)."""
    if port in config.pump_ports_raw():
        return True
    from .portguard import PortGuard  # here: portguard imports only config, and box stays importable on its own

    return PortGuard(box_id).owner_device(port) == "pump"


def check_port_lock(port: str, on: bool, *, box_id: str = "box-1") -> None:
    """ports.json always_on, enforced in the actuator: no page, preset or automation switches it off.
    Fails closed: with ports.json unreadable and no good version in memory, nothing is switched off, except a pump:
    its OFF is never refused (a pump held on waters without end, 23.10.2026)."""
    rules = config.port_rules()
    if rules is None:
        if not on and not pump_off_allowed(port, box_id):
            raise CommandRefused(t("port.rules_unreadable"))
        return
    rule = rules.get(port) or {}
    if rule.get("always_on") and not on and not pump_off_allowed(port, box_id):
        raise CommandRefused(t("port.always_on", port=localize(rule).get("label") or port))


@dataclass
class Pending:
    field: str
    target: Any
    token: str
    deadline: float
    status: str = "pending"  # pending | ok | rejected | timeout
    settled_at: float | None = None


# ---------- login rules ----------

class LoginRejected(TextError):
    """VIVOSUN refused the credentials, or the account holds no VGrow: retried at most every 30 minutes."""


class LinkDropped(TextError):
    """The cloud accepted the MQTT link and closed it again within STABLE_SECONDS: a failure, not a reconnect."""


def dump_text(value: Any) -> Any:
    """A catalog text (or a plain value) as JSON, so a reason read back after a restart is still said in the
    reader's language."""
    if isinstance(value, TextError):
        value = value.message
    if isinstance(value, Text):
        return {"text": value.key, "params": {k: dump_text(v) for k, v in value.params.items()}}
    if isinstance(value, TimeOfDay):
        return {"time": value.epoch}
    return value if value is None or isinstance(value, str | int | float | bool) else str(value)


def load_text(data: Any) -> Any:
    """The reverse of dump_text."""
    if isinstance(data, dict) and isinstance(data.get("text"), str):
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        return Text(data["text"], **{str(k): load_text(v) for k, v in params.items()})
    if isinstance(data, dict) and isinstance(data.get("time"), int | float) and not isinstance(data["time"], bool):
        return TimeOfDay(float(data["time"]))
    return data


def failure_kind(err: BaseException) -> str:
    """'auth' for a rejected login or an account without a VGrow; 'network' for everything else (no network,
    a server error, an expired AWS token, the unexpected): those back off and try again."""
    return "auth" if isinstance(err, LoginRejected) else "network"


def backoff_seconds(failures: int, rng: random.Random | None = None) -> float:
    """The wait after the n-th network or server failure in a row: 10 s, 20 s, 40 s ... at most 5 min,
    each up to 20 % shorter at random (never longer than its step)."""
    step = min(BACKOFF_MAX_SECONDS, BACKOFF_FIRST_SECONDS * 2 ** min(max(failures, 1) - 1, 16))
    return step * (1 - BACKOFF_JITTER * (rng or random).random())


def _bare(text: Any) -> Any:
    """A plain error text without its closing full stop, so 'reason. Next try at ...' reads as one sentence."""
    return text.rstrip(" .") or text if isinstance(text, str) else text


class LoginGate:
    """No more than LOGIN_MAX_PER_HOUR logins in any rolling hour.

    The attempts live in STATE_DIR/login-attempts.json and the file is read again before every decision, so
    a restart, a crash loop under launchd and a parallel --check all count. Should the file be unwritable,
    this process still counts its own attempts in memory.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._memory: list[float] = []

    @staticmethod
    def path() -> Path:
        return config.STATE_DIR / LOGIN_FILE

    def recent(self, now: float | None = None) -> list[float]:
        """Attempts less than an hour away from now, oldest first. Either way: a clock set back by a few minutes
        must not hand out fresh attempts."""
        now = self._clock() if now is None else now
        try:
            raw = json.loads(self.path().read_text(encoding="utf-8")).get("attempts", [])
        except (OSError, ValueError, AttributeError):
            raw = []
        stamps = {float(s) for s in raw if isinstance(s, int | float) and not isinstance(s, bool)}
        return sorted(s for s in stamps | set(self._memory) if abs(now - s) < LOGIN_WINDOW_SECONDS)

    def next_allowed(self, now: float | None = None) -> float:
        """Now, if a login may go out; otherwise the moment the oldest counted attempt leaves the hour."""
        now = self._clock() if now is None else now
        recent = self.recent(now)
        if len(recent) < LOGIN_MAX_PER_HOUR:
            return now
        return recent[-LOGIN_MAX_PER_HOUR] + LOGIN_WINDOW_SECONDS

    def record(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        self._memory = [*(m for m in self._memory if abs(now - m) < LOGIN_WINDOW_SECONDS), now]
        attempts = sorted({*self.recent(now), now})
        try:
            config.atomic_write(self.path(), json.dumps({"attempts": attempts}) + "\n")
        except OSError as err:
            _LOGGER.warning("Login attempts not saved (%s); this process counts them in memory", err)


class ApiClient(VivosunApiClient):
    """Upstream client plus the full point-log rows (upstream returns only the newest), and one pair of login
    identifiers for all of its logins.

    Upstream's login() mints a fresh spClientId and spSessionId on every call. This client swaps in its own
    pair at the request, so every retry carries the same identifiers, while upstream's payload and token parsing
    stay exactly as they are (vgrow/vivosun/ is not edited). Only fields upstream sends are replaced.
    """

    def __init__(self, session: aiohttp.ClientSession, *, sp_client_id: str | None = None,
                 sp_session_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(session, **kwargs)
        self.login_ids = {"spClientId": sp_client_id or str(uuid.uuid4()),
                          "spSessionId": sp_session_id or str(uuid.uuid4())}

    async def _request_json(self, method: str, path: str, *, headers: Mapping[str, str] | None = None,
                            json_body: Mapping[str, object] | None = None) -> Mapping[str, object]:
        if path == API_LOGIN_PATH and json_body is not None:
            json_body = {**json_body, **{k: v for k, v in self.login_ids.items() if k in json_body}}
        return await super()._request_json(method, path, headers=headers, json_body=json_body)

    async def get_point_rows(
        self, tokens: AuthTokens, device: DeviceInfo, *, start_time: int, end_time: int
    ) -> list[dict[str, Any]]:
        payload = {
            "sceneId": device.scene_id,
            "deviceId": device.device_id,
            "startTime": start_time,
            "endTime": end_time,
            "reportType": 0,
            "orderBy": "asc",
            "timeLevel": "ONE_MINUTE",
        }
        data = await self._request_json(
            "POST", API_POINT_LOG_PATH, headers=self._auth_headers(tokens), json_body=payload
        )
        rows = data.get("iotDataLogList") if isinstance(data, dict) else None
        return [row for row in rows or [] if isinstance(row, dict)]


class Box:
    def __init__(self, email: str, password: str, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, rng: random.Random | None = None,
                 remember: bool = True, box_id: str = "box-1") -> None:
        self.box_id = box_id
        self._email = email
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._api: ApiClient | None = None
        self._aws: AwsAuthClient | None = None
        self._tokens: AuthTokens | None = None
        self._identity: AwsIdentity | None = None
        self._creds: AwsCredentials | None = None
        self._mqtt: MQTTClient | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.device: DeviceInfo | None = None
        self.camera_device: DeviceInfo | None = None
        self.other_devices: list[str] = []
        self.reported: dict[str, Any] = {}
        self.desired: dict[str, Any] = {}
        self.sensors: dict[str, float | int | None] = {}
        self.sensors_at: float | None = None
        # Every point-log row of the last poll's 15-minute window ([] after a failed poll): the measurement store
        # keeps them all (vgrow/measurements.py), the cockpit itself uses only the newest.
        self.last_rows: list[dict[str, Any]] = []
        self.shadow_at: float | None = None
        self.pending: dict[str, Pending] = {}
        # login | connection | sensors -> reason, shown on the page; a TextError stays one, so it is
        # translated for whoever reads it, not frozen in the language of the moment it happened.
        self._errors: dict[str, Any] = {}
        self.listeners: list[Any] = []  # called after every sensor poll, a failed one too (alerts, automation)
        self.port_listeners: list[Any] = []  # read-only observers of reported USB fragments, never control polls
        # Login rules (module docstring); clock, sleep and rng are replaceable for tests.
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._gate = LoginGate(clock)
        self._needs_login = True
        self._failures = 0  # network or server failures in a row, for the backoff
        self._retry_at = 0.0  # no attempt before this: a backoff, a rejected login or the hourly cap
        self._last_reason: Any = None  # the last failure, named again while the hourly cap holds
        self._connected_at: float | None = None  # when the current MQTT link came up; None once it is booked
        self._connects: list[float] = []  # MQTT connection attempts in the last hour
        # One set of identifiers, reused for every login and reconnect; the server's survive restarts.
        self._remember = remember
        saved = self._read_login_state() if remember else {}
        self._sp_client_id = saved.get("spClientId") or str(uuid.uuid4())
        self._sp_session_id = saved.get("spSessionId") or str(uuid.uuid4())
        self._mqtt_client_id = saved.get("mqttClientId") or f"vgrow-control-{uuid.uuid4().hex[:12]}"
        self._login_state = {**saved, "spClientId": self._sp_client_id, "spSessionId": self._sp_session_id,
                             "mqttClientId": self._mqtt_client_id}
        if remember:
            self._restore_rejection(saved)
            if saved != self._login_state:
                self._write_login_state()
        # The type-9 water sensor: its state and when it last changed (water_change), kept across restarts.
        self.water_state: str | None = None
        self.water_since: float | None = None
        # The run of readings in that state (water_run): its first row and the newest row with a wet/dry value.
        # Not stored: after a restart the run starts again from the first window, which the 15 minutes cover.
        self.water_run_from: float | None = None
        self.water_seen_at: float | None = None
        if remember:
            self._read_water_state()

    # ---------- what survives a restart ----------

    @staticmethod
    def login_state_path() -> Path:
        return config.STATE_DIR / LOGIN_STATE_FILE

    @classmethod
    def _read_login_state(cls) -> dict[str, Any]:
        """The stored identifiers (only well-formed ones) and the pause after a rejection, if any."""
        try:
            data = json.loads(cls.login_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, Any] = {}
        for name in ("spClientId", "spSessionId"):
            try:
                out[name] = str(uuid.UUID(str(data.get(name))))
            except ValueError:
                pass  # missing or not a uuid: a fresh one
        mqtt = data.get("mqttClientId")
        if isinstance(mqtt, str) and mqtt.startswith("vgrow-control-") and len(mqtt) <= 64 and mqtt.isascii():
            out["mqttClientId"] = mqtt
        until = data.get("auth_retry_at")
        if isinstance(until, int | float) and not isinstance(until, bool) and math.isfinite(until):
            out["auth_retry_at"] = float(until)
            out["auth_reason"] = data.get("auth_reason")
        return out

    # ---------- the water sensor's last change (type 9) ----------

    def _read_water_state(self) -> None:
        try:
            data = json.loads((config.STATE_DIR / WATER_STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        since = data.get("since") if isinstance(data, dict) else None
        if data.get("state") in ("wet", "dry") and isinstance(since, int | float) and not isinstance(since, bool):
            self.water_state, self.water_since = data["state"], float(since)

    def track_water(self, rows: list[dict[str, Any]], now: float | None = None) -> None:
        """Keep the time of the last wet/dry change. A change seen inside the rows gives its exact minute; a state
        that differs from the one held without a visible change (a gap, a restart) starts at the window's earliest
        row; a first state without a visible change has no known start."""
        now = time.time() if now is None else now
        aux = self.reported.get("aux35Dev")
        state, start, changed = water_change(rows, aux, now)
        if state is None:
            return
        self._track_water_run(_water_seen(rows, aux, now), state)
        if state == self.water_state:
            if changed and start is not None and (self.water_since is None or start > self.water_since):
                self._set_water(state, start)  # dry, wet, dry between two polls: the last change is the later one
            return
        since = start if changed or self.water_state is not None else None
        self._set_water(state, since)

    def _track_water_run(self, seen: list[tuple[float, str]], state: str) -> None:
        """The run of readings in `state` for the alarm's debounce: it goes on from the previous poll only when this
        window shows no other state and no gap before it and its rows join the newest row seen before."""
        found = water_run(seen)
        if found is None:
            return
        start, reaches_back = found
        newest = seen[-1][0]
        if (reaches_back and state == self.water_state and self.water_run_from is not None
                and self.water_seen_at is not None and start - self.water_seen_at <= WATER_STALE_SECONDS):
            start, newest = min(start, self.water_run_from), max(newest, self.water_seen_at)
        self.water_run_from, self.water_seen_at = start, newest

    def _set_water(self, state: str, since: float | None) -> None:
        self.water_state, self.water_since = state, since
        if self._remember and since is not None:
            try:
                config.atomic_write(config.STATE_DIR / WATER_STATE_FILE,
                                    json.dumps({"state": state, "since": since}) + "\n")
            except OSError as err:
                _LOGGER.warning("Water state not saved (%s); this process keeps it in memory", err)

    def climate_now(self) -> dict[str, Any]:
        """climate_view of the newest reading, decoded by the accessory type, with water_since for a type-9 state."""
        if not self.sensors:
            return {}
        climate = climate_view(self.sensors, self.reported.get("aux35Dev"))
        held = climate.get("water_state") in ("wet", "dry") and climate["water_state"] == self.water_state
        climate["water_since"] = self.water_since if held else None
        # How long the readings themselves have said this state without a break (water_run), in seconds: the
        # tank alarm and Hank's refill line debounce on it, so polls that bring no new row confirm nothing.
        run = (self.water_seen_at - self.water_run_from
               if held and self.water_run_from is not None and self.water_seen_at is not None else None)
        climate["water_run_s"] = None if run is None else max(0.0, run)
        return climate

    def _write_login_state(self) -> None:
        if not self._remember:
            return
        try:
            config.atomic_write(self.login_state_path(), json.dumps(self._login_state, indent=2) + "\n")
        except OSError as err:
            _LOGGER.warning("Login state not saved (%s); this process keeps it in memory", err)

    def _restore_rejection(self, saved: dict[str, Any]) -> None:
        """A rejection before the restart still holds its 30 minutes, with its reason on the page. A stored time
        further out than that (a clock set back) is cut to it."""
        until = saved.get("auth_retry_at")
        now = self._clock()
        if until is None or until <= now:
            return
        self._retry_at = min(until, now + AUTH_RETRY_SECONDS)
        cause = load_text(saved.get("auth_reason"))
        if not (isinstance(cause, Text) or (isinstance(cause, str) and cause)):  # missing or not readable
            cause = Text("box.login_rejected_before")
        self._last_reason = _bare(cause)
        self._errors["login"] = TextError("box.retry_slow", reason=cause, at=TimeOfDay(self._retry_at),
                                          minutes=AUTH_RETRY_SECONDS // 60)

    # ---------- lifecycle ----------

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._api = ApiClient(self._session, sp_client_id=self._sp_client_id, sp_session_id=self._sp_session_id)
        self._aws = AwsAuthClient(self._session)
        # The first login and connect follow the same rules as every retry, and a failure no longer stops the
        # server: launchd would restart it every 30 s, and each start logged in again.
        wait = await self._supervise_once()
        # poll_once, not poll_sensors: a failing point log at boot must not stop the server before it serves.
        # The listeners then see no reading, so the offline rule starts its hold.
        await self.poll_once()
        self._tasks = [
            asyncio.create_task(self._supervise(wait), name="vgrow_supervise"),
            asyncio.create_task(self._poll_loop(), name="vgrow_poll"),
            asyncio.create_task(self._shadow_loop(), name="vgrow_shadow_refresh"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._mqtt is not None:
            await self._mqtt.disconnect()
        if self._session is not None:
            await self._session.close()

    async def _login(self) -> None:
        """One login: tokens, the VGrow among the devices, the AWS identity and credentials. Only a refusal of
        the credentials themselves or an account without a VGrow is LoginRejected; a failure in a later step
        (devices, identity, Cognito) is a network or server failure, the credentials were accepted."""
        assert self._api is not None and self._aws is not None
        try:
            self._tokens = await self._api.login(self._email, self._password)
        except VivosunAuthError as err:
            raise LoginRejected("box.login_rejected", reason=_bare(str(err) or type(err).__name__)) from err
        devices = await self._api.get_devices(self._tokens)
        boxes = [d for d in devices if client_model_token(d.client_id) == VGROW_MODEL]
        if not boxes:
            names = ", ".join(f"{d.name} ({client_model_token(d.client_id)})" for d in devices)
            raise LoginRejected("box.no_vgrow", model=VGROW_MODEL, found=names or Text("box.none_found"))
        self.device = boxes[0]
        self.camera_device = next((d for d in devices if d.device_type == "camera"), None)
        self.other_devices = [d.name for d in devices if d.device_id != self.device.device_id]
        self._identity = await self._api.get_aws_identity(self._tokens)
        self._creds = await self._aws.get_credentials_for_identity(self._identity)
        _LOGGER.info("Found %s", self.device.name)

    async def _connect(self) -> None:
        assert self._aws and self._identity and self._creds and self.device
        if self._mqtt is not None:
            await self._mqtt.disconnect()
        url = self._aws.sigv4_sign_mqtt_url(
            endpoint=self._identity.aws_host, region=self._identity.aws_region, credentials=self._creds
        )
        client = MQTTClient(
            websocket_url=url,
            thing=self.device.client_id,
            topic_prefix=self.device.topic_prefix,
            client_id=self._mqtt_client_id,  # the same on every reconnect of this process
            label="vgrow",
        )
        await client.connect()
        client.add_message_callback(self._on_message)
        try:
            await client.subscribe([(TOPIC_SHADOW_UPDATE_REJECTED.format(thing=self.device.client_id), 1)])
        except (MQTTError, TimeoutError):
            _LOGGER.warning("Rejection topic unavailable; unconfirmed commands still time out")
        self._mqtt = client
        await self.refresh_shadow()

    async def refresh_shadow(self) -> None:
        if self._mqtt is not None and self._mqtt.is_connected and self.device is not None:
            await self._mqtt.publish(TOPIC_SHADOW_GET.format(thing=self.device.client_id), b"{}")

    async def _shadow_loop(self) -> None:
        """Read-only refresh, independent of sensor polls and the connection/login supervisor.

        Connecting already requests the initial shadow. Only a reported response in _on_message updates its age;
        a request, failure or disconnected period must never make cached status appear fresh.
        """
        while True:
            await self._sleep(SHADOW_REFRESH_SECONDS)
            try:
                await asyncio.wait_for(self.refresh_shadow(), timeout=SHADOW_REFRESH_TIMEOUT_SECONDS)
            except Exception as err:
                _LOGGER.warning("Periodic device status refresh failed: %s", reason(err))

    async def _supervise(self, wait: float = SUPERVISE_SECONDS) -> None:
        """Keep the box connected; each look says how long to wait before the next one."""
        while True:
            await self._sleep(wait)
            try:
                wait = await self._supervise_once()
            except Exception:  # never let the supervisor die; logins stay behind the gate either way
                _LOGGER.exception("Supervisor step failed")
                wait = SUPERVISE_SECONDS

    async def _supervise_once(self) -> float:
        """One look: log in if needed (through the hourly gate), renew the AWS credentials before they expire
        (~1 h), reconnect on drops. Returns the seconds until the next look."""
        now = self._clock()
        # No legitimate wait is longer than an hour (the cap); a clock set back must not stretch one beyond that.
        self._retry_at = min(self._retry_at, now + LOGIN_WINDOW_SECONDS)
        if now < self._retry_at:
            return self._retry_at - now
        step = "login"
        try:
            if self._needs_login:
                allowed = self._gate.next_allowed(now)
                if allowed > now:
                    return self._hold_for_cap(allowed, now)
                self._gate.record(now)
                await self._login()
                self._needs_login = False
                self._errors.pop("login", None)
                self._forget_rejection()
            step = "connection"
            assert self._aws is not None
            if self._creds is not None and self._aws.credentials_need_refresh(self._creds):
                _LOGGER.info("Renewing AWS credentials")
                self._creds = await self._aws.get_credentials_for_identity(self._identity)  # type: ignore[arg-type]
                self._connects.append(now)  # counted, never held back: old credentials would only expire
                await self._connect()
                self._connected_at = self._clock()
            elif self._mqtt is None or not self._mqtt.is_connected:
                up_since = self._connected_at
                if self._mqtt is not None and up_since is not None and now - up_since < STABLE_SECONDS:
                    self._connected_at = None  # booked once: the next try connects anew
                    raise LinkDropped("box.link_dropped")
                allowed = self._connect_allowed(now)
                if allowed > now:
                    return self._hold_connects(allowed, now)
                _LOGGER.info("Connecting to the box over MQTT")
                self._connects.append(now)
                await self._connect()
                self._connected_at = self._clock()
        except Exception as err:  # keep the loop alive, show the reason on the page
            return self._failed(step, err)
        self._retry_at = 0.0
        self._errors.pop("login", None)
        if self._connected_at is not None and now - self._connected_at >= STABLE_SECONDS:
            # Only a link that held counts as recovered: one the cloud drops again at once keeps the backoff going.
            self._failures, self._last_reason = 0, None
            self._errors.pop("connection", None)
        return SUPERVISE_SECONDS

    def _connect_allowed(self, now: float) -> float:
        """Now, if a connection may go out; otherwise the moment the oldest of the last CONNECT_MAX_PER_HOUR
        leaves the hour."""
        self._connects = [c for c in self._connects if abs(now - c) < LOGIN_WINDOW_SECONDS]
        if len(self._connects) < CONNECT_MAX_PER_HOUR:
            return now
        return self._connects[-CONNECT_MAX_PER_HOUR] + LOGIN_WINDOW_SECONDS

    def _hold_connects(self, allowed: float, now: float) -> float:
        """The hourly cap on connections is reached: none before `allowed`, and the page says so."""
        self._retry_at = allowed
        self._errors["connection"] = TextError("box.connect_capped", count=CONNECT_MAX_PER_HOUR, at=TimeOfDay(allowed))
        _LOGGER.warning("%d MQTT connections in the last hour: the next one waits %.0f s", CONNECT_MAX_PER_HOUR,
                        allowed - now)
        return allowed - now

    def _forget_rejection(self) -> None:
        """A login went through: no pause from an earlier rejection is kept for the next start."""
        if self._login_state.get("auth_retry_at") is not None:
            self._login_state.update(auth_retry_at=None, auth_reason=None)
            self._write_login_state()

    def _failed(self, step: str, err: Exception) -> float:
        """Book a failed login or connection; returns the wait before the next try. The next try logs in again
        first, as the old loop did after a failed reconnect, but only as often as the rules allow."""
        now = self._clock()
        self._needs_login = True
        cause = reason(err)
        self._last_reason = _bare(cause)
        if failure_kind(err) == "auth":
            self._retry_at = now + AUTH_RETRY_SECONDS
            self._errors["login"] = TextError("box.retry_slow", reason=cause, at=TimeOfDay(self._retry_at),
                                              minutes=AUTH_RETRY_SECONDS // 60)
            self._login_state.update(auth_retry_at=self._retry_at, auth_reason=dump_text(cause))
            self._write_login_state()  # a restart keeps the 30 minutes
            _LOGGER.warning("Login rejected, next try in %d min: %s", AUTH_RETRY_SECONDS // 60, err)
        else:
            self._failures += 1
            self._retry_at = now + backoff_seconds(self._failures, self._rng)
            self._errors[step] = TextError("box.retry_at", reason=self._last_reason, at=TimeOfDay(self._retry_at))
            _LOGGER.warning("%s failed (%d in a row), next try in %.0f s: %s", step.capitalize(), self._failures,
                            self._retry_at - now, err)
        return self._retry_at - now

    def _hold_for_cap(self, allowed: float, now: float) -> float:
        """The hourly cap is reached: no login before `allowed`, and the page says so."""
        self._retry_at = allowed
        at = TimeOfDay(allowed)
        self._errors["login"] = (
            TextError("box.login_capped_after", reason=self._last_reason, count=LOGIN_MAX_PER_HOUR, at=at)
            if self._last_reason is not None else TextError("box.login_capped", count=LOGIN_MAX_PER_HOUR, at=at))
        _LOGGER.warning("%d logins in the last hour: the next one waits %.0f s", LOGIN_MAX_PER_HOUR, allowed - now)
        return allowed - now

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            await self.poll_once()

    async def poll_once(self) -> None:
        """One poll. A failed one still reaches the listeners: the offline rule must see the data go stale."""
        try:
            await self.poll_sensors()
        except Exception as err:
            self._errors["sensors"] = reason(err)
            _LOGGER.warning("Sensor poll failed: %s", err)
            await self.notify_listeners()

    # ---------- reading ----------

    def _logged_in(self) -> None:
        if not (self._api and self._tokens and self.device):
            raise TextError("box.not_logged_in")  # a sentence for the page, not an AssertionError

    async def poll_sensors(self) -> None:
        self.last_rows = []
        self._logged_in()
        assert self._api and self._tokens and self.device
        end = int(time.time())
        rows = await self._api.get_point_rows(self._tokens, self.device, start_time=end - 900, end_time=end)
        self.last_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        self.track_water(self.last_rows)
        # The age is the reading's own: a stalled upload keeps its last row in the 15-minute window, and
        # timing that by the poll would make every staleness guard 15 minutes late.
        newest = newest_reading(rows, time.time())
        if newest is not None and (self.sensors_at is None or newest[0] >= self.sensors_at):
            self.sensors_at, self.sensors = newest
        self._errors.pop("sensors", None)
        await self.notify_listeners()

    async def notify_listeners(self) -> None:
        """The one place listeners are called; a failing listener never stops the others."""
        for listener in list(self.listeners):
            try:
                await listener(self)
            except Exception:
                _LOGGER.exception("Listener failed")

    @property
    def last_error(self) -> str | None:
        """The most serious open problem, in the language of whoever asks."""
        for kind in ("login", "connection", "sensors"):
            if kind in self._errors:
                return t(f"box.error.{kind}", reason=self._errors[kind])
        return None

    async def point_rows(self, seconds: int) -> list[dict[str, Any]]:
        """The raw point-log rows of the last `seconds` (at most 168 h), over the logged-in session. Never logs in:
        without a login it raises box.not_logged_in, and the supervisor's rules decide when the next login goes out.
        The charts (history) and the measurement store (vgrow/measurements.py) both read through here."""
        self._logged_in()
        assert self._api and self._tokens and self.device
        seconds = max(60, min(int(seconds), 168 * 3600))
        end = int(time.time())
        return await self._api.get_point_rows(self._tokens, self.device, start_time=end - seconds, end_time=end)

    async def history(self, hours: int) -> list[dict[str, float | int | None]]:
        hours = max(1, min(hours, 168))
        rows = await self.point_rows(hours * 3600)
        scaled = [scale_row(row) for row in rows]
        step = max(1, len(scaled) // 720)  # a phone chart needs no more than ~720 points
        return scaled[::step]

    def _on_message(self, topic: str, payload: bytes, qos: int) -> None:
        try:
            document = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(document, dict):
            return
        if topic.endswith("/update/rejected"):
            token = document.get("clientToken")
            for pending in self.pending.values():
                if pending.token == token and pending.status == "pending":
                    pending.status, pending.settled_at = "rejected", time.time()
            return
        reported = sh.extract_shadow_reported(document)
        if reported:
            deep_merge(self.reported, reported)
            self.shadow_at = time.time()
        state = document.get("state")
        if isinstance(state, dict) and isinstance(state.get("desired"), dict):
            deep_merge(self.desired, state["desired"])
        self._settle()

        if reported:
            ports = {name: reported_port(self.reported.get(name)).is_on for name in reported
                     if re.fullmatch(r"usb[1-9][0-9]*", name)
                     and isinstance(reported[name], dict) and "lv" in reported[name]}
            if ports:
                for listener in list(self.port_listeners):
                    try:
                        listener(self, ports, self.shadow_at)
                    except Exception:
                        _LOGGER.exception("USB report observer failed")

    # ---------- view ----------

    def parsed(self) -> dict[str, Any]:
        return dict(sh.parse_reported_fragment(self.reported)) if self.reported else {}

    def _current(self, field: str) -> Any:
        p = self.parsed()
        if field == "light.level":
            return p.get("light", {}).get("level")
        if field == "light.spec":
            return p.get("light", {}).get("spectrum")
        if field == "cFan.level":
            return "nw" if self._natural_wind() else p.get("cFan", {}).get("level")
        if field == "dFan.level":
            return p.get("dFan", {}).get("level")
        if field.startswith("port."):
            return reported_port(self.reported.get(field[5:])).is_on
        return None

    def _natural_wind(self) -> bool:
        """E42A: raw level 200. VGrow: its own nw flag (seen live: nw 1, lv 185, manu.lv 85)."""
        cfan = self.reported.get("cFan")
        if not isinstance(cfan, dict):
            return False
        level = cfan.get("lv")
        return cfan.get("nw") == 1 or (type(level) is int and level > 100)

    def _settle(self) -> None:
        now = time.time()
        for field, pending in list(self.pending.items()):
            if pending.status != "pending":
                if pending.settled_at is not None and now - pending.settled_at > 20:
                    del self.pending[field]  # the page has had time to show the outcome
                continue
            current = self._current(pending.field)
            # VGrow in natural wind keeps its nw flag and only stores the new manual level (seen 22.10.2026):
            # that is the box taking the command, so it counts as confirmed.
            if pending.field == "cFan.level" and current == "nw":
                current = self.parsed().get("cFan", {}).get("manual_level")
            if current == pending.target:
                pending.status, pending.settled_at = "ok", now
            elif now > pending.deadline:
                pending.status, pending.settled_at = "timeout", now

    def snapshot(self) -> dict[str, Any]:
        self._settle()
        p = self.parsed()
        light, cfan, dfan = p.get("light", {}), p.get("cFan", {}), p.get("dFan", {})
        model = client_model_token(self.device.client_id) if self.device else ""
        ports = {}
        for name in PORTS_BY_MODEL.get(model, ()):
            report = reported_port(self.reported.get(name))
            ports[name] = {
                "on": report.is_on, "mode": report.mode, "in_plan": report.in_plan,
                "accessory": report.accessory_code, "can_write": report.can_write,
            }
        cfan_level = cfan.get("level")
        natural_wind = self._natural_wind()
        spectrum = light.get("spectrum")
        return {
            "device": self.device.name if self.device else None,
            "online": bool(p.get("connection", {}).get("connected", self.device.online if self.device else False)),
            "mqtt": bool(self._mqtt and self._mqtt.is_connected),
            "shadow_age": None if self.shadow_at is None else round(time.time() - self.shadow_at),
            "sensors_age": None if self.sensors_at is None else max(0, round(time.time() - self.sensors_at)),
            "climate": self.climate_now(),
            "aux_device": self.reported.get("aux35Dev"),
            "light": {
                "level": light.get("level"),
                "mode": light.get("mode"),
                "in_plan": bool(light.get("in_plan")),
                # Whether the box reported the plan flag at all: the autopilot never writes a light whose plan is
                # unknown (a write sets mode 0 and ends a running plan).
                "in_plan_known": isinstance(light.get("in_plan"), bool),
                "spectrum": next((k for k, v in SPECTRUMS.items() if v == spectrum), spectrum),
                "spectrum_writable": light.get("mode") == 0 and light.get("in_plan") is False,
            },
            "cfan": {
                # In natural wind the stored manual level is the meaningful step.
                "step": fan_step(cfan.get("manual_level") if natural_wind else cfan_level, CFAN_LEVEL_MAP),
                "natural_wind": natural_wind,
                # The VGrow keeps natural wind in a separate flag; its write command is not observed yet.
                "nw_writable": model != VGROW_MODEL,
                "mode": cfan.get("mode"),
            },
            "dfan": {"step": fan_step(dfan.get("level"), DFAN_LEVEL_MAP), "mode": dfan.get("mode")},
            "ports": ports,
            "plan": p.get("plan", {}).get("active_stage"),
            "pending": {k: {"status": v.status, "target": v.target} for k, v in self.pending.items()},
            "error": self.last_error,
            "server_time": datetime.now(tz=UTC).isoformat(),
        }

    # ---------- commands ----------

    async def _send(self, field: str, payload: dict[str, Any], target: Any) -> None:
        if self._mqtt is None or not self._mqtt.is_connected:
            raise CommandRefused(t("box.no_connection"))
        token = uuid.uuid4().hex[:16]
        await self._mqtt.publish_shadow_update(json.dumps({**payload, "clientToken": token}), qos=1)
        self.pending[field] = Pending(field, target, token, time.time() + PENDING_SECONDS)

    async def set_light(self, level: int) -> None:
        level = sh.clamp_light_level(int(level))
        await self._send("light.level", sh.build_light_level_payload(level), level)

    async def set_spectrum(self, name: str) -> None:
        if name not in SPECTRUMS:
            raise CommandRefused(t("box.unknown_spectrum", name=repr(name)))
        light = self.parsed().get("light", {})
        # Upstream rule: spectrum writes only with a genuine manual, no-plan report.
        if light.get("mode") != 0 or light.get("in_plan") is not False:
            raise CommandRefused(t("box.spectrum_manual_only"))
        await self._send("light.spec", sh.build_light_spectrum_payload(SPECTRUMS[name]), SPECTRUMS[name])

    async def set_cfan(self, step: int | None, natural_wind: bool = False) -> None:
        model = client_model_token(self.device.client_id) if self.device else ""
        if natural_wind and model == VGROW_MODEL:
            raise CommandRefused(t("box.natural_wind_app"))
        if natural_wind:
            await self._send("cFan.level", sh.build_cfan_level_payload(CFAN_NATURAL_WIND_VALUE), "nw")
            return
        step = max(0, min(10, int(step or 0)))
        level = CFAN_LEVEL_MAP[step]
        await self._send("cFan.level", sh.build_cfan_level_payload(level), level)

    async def set_dfan(self, step: int) -> None:
        step = max(0, min(10, int(step)))
        level = DFAN_LEVEL_MAP[step]
        await self._send("dFan.level", sh.build_dfan_level_payload(level), level)

    async def set_port(self, port: str, on: bool) -> None:
        if self.device is None:  # not logged in yet: say that, not "not a port of this box"
            raise CommandRefused(t("box.no_connection"))
        model = client_model_token(self.device.client_id)
        if port not in PORTS_BY_MODEL.get(model, ()):
            raise CommandRefused(t("box.not_a_port", port=port))
        check_port_lock(port, bool(on), box_id=self.box_id)
        report = reported_port(self.reported.get(port))
        if not report.can_write:
            raise CommandRefused(t("box.port_not_manual", port=port))
        await self._send(f"port.{port}", build_manual_port_payload(port, bool(on)), bool(on))
