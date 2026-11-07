"""The soil moisture sensor: an Ecowitt WH51 heard by an Ecowitt GW1200 gateway on the home network.

Read only. Every minute the server asks the gateway GET /get_livedata_info: no login, no cloud, and nothing has to be
opened on this server. Seen on the owner's gateway on 06.11.2026 (firmware GW1200A_V1.4.7); Ecowitt's manual does not
document this page, so a gateway that stops answering it only costs the tile.

The sensor's percent runs from dry air (0) to a glass of water (90 or more, WH51 manual) and says nothing about the
owner's soil until it is set to it (the manual: "the sensor need to be calibrated to suit your soil type"). One tap
does that: the reading with the pot watered and drained is 100 on the relative scale. From there the target comes from
the watering model: water when half of the available water is used (U1), in a substrate that holds about 55 % water by
volume when drained, 30 % of it available (B05), so at 73 % of the drained reading. That the sensor is linear in between
is an assumption until a first dry-down has been compared with a finger check.

Through on_reading the Durst-Index (vgrow/thirst.py) gets every calibrated reading. Nothing here switches a device.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from . import config, i18n
from .box import CommandRefused
from .i18n import t

_LOGGER = logging.getLogger(__name__)

POLL_S = 60.0  # the sensor reports every 70 seconds (WH51 manual)
STALE_S = 600.0  # ten minutes without an answer: the tile stops showing the number as current
LOG_EVERY_S = 900.0  # a line in the log at least this often
LOG_STEP = 2.0  # and at once on a change this big: the sensor wobbles by one point (13, 14, 13)
TIMEOUT_S = 8.0
MIN_SPAN = 10.0  # the drained reading must stand this far above the dry point, or the scale means nothing
HISTORY_TAIL_BYTES = 4 * 1024 * 1024

DE = {
    "soil.no_reading": "Der Bodensensor liefert gerade keinen Wert. Einstellen geht erst mit einem frischen Wert.",
    "soil.span": "Der Wert liegt zu nah am Trockenwert ({dry} %). Erst gießen und abtropfen lassen, dann einstellen.",
    "soil.not_found": "Kein Bodensensor eingerichtet.",
}
EN = {
    "soil.no_reading": "The soil sensor has no current reading. Setting it needs a fresh reading.",
    "soil.span": "The reading is too close to the dry value ({dry} %). Water and let it drain first, then set it.",
    "soil.not_found": "No soil sensor is set up.",
}
i18n.DE.update(DE)
i18n.EN.update(EN)


@dataclass(frozen=True, slots=True)
class Reading:
    channel: int
    percent: float
    voltage: float | None = None
    name: str = ""


def _number(text: Any) -> float | None:
    """'13%', '1.50' or 13 as a number; the gateway's '--' and anything else as None."""
    try:
        number = float(str(text).strip().rstrip("%"))
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def parse_livedata(data: Any) -> list[Reading]:
    """The soil channels of a /get_livedata_info answer, lowest first. A channel without a percent is left out."""
    rows = data.get("ch_soil") if isinstance(data, Mapping) else None
    readings = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        channel, percent = _number(row.get("channel")), _number(row.get("humidity"))
        if channel is None or percent is None or not 0 <= percent <= 100:
            continue
        readings.append(Reading(int(channel), percent, _number(row.get("voltage")), str(row.get("name") or "")))
    return sorted(readings, key=lambda reading: reading.channel)


def parse_room(data: Any) -> tuple[float | None, float | None]:
    """The gateway's own air sensor: where it hangs, not in the box."""
    rows = data.get("wh25") if isinstance(data, Mapping) else None
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else {}
    return _number(row.get("intemp")), _number(row.get("inhumi"))


def targets(stage_key: str | None) -> tuple[int, int] | None:
    """(due below, too wet above) on the relative scale, from kartei/water_model.json; None when it cannot be read."""
    from .thirst import ModelError, load_model

    try:
        model = load_model()
        shares = model("stage_f_trig")
        share = float(shares.get(stage_key) or model("f_trig"))
        capacity, available = float(model("soil_container_capacity")), float(model("a_w"))
        return round(100 * (capacity - share * available) / capacity), int(model("soil_too_wet_percent"))
    except (ModelError, TypeError, ValueError, AttributeError) as err:
        _LOGGER.warning("Soil targets not available: %s", err)
        return None


class SoilSensor:
    """One gateway. The tile shows its lowest channel, the log keeps every channel."""

    def __init__(self, session: aiohttp.ClientSession, ip: str, clock: Callable[[], float] = time.time) -> None:
        self.session = session
        self.url = f"http://{ip}/get_livedata_info"
        self.clock = clock
        self.readings: list[Reading] = []
        self.room: tuple[float | None, float | None] = (None, None)
        self.heard_at: float | None = None  # the gateway's last usable answer
        self.task: asyncio.Task[None] | None = None
        self.on_reading: Callable[[dict[str, Any]], None] | None = None
        self.stage_of: Callable[[], str | None] | None = None
        self._logged: dict[int, tuple[float, float]] = {}
        self._failing = False

    def folder(self) -> Path:
        return config.STATE_DIR / "soil"

    def log_path(self) -> Path:
        return self.folder() / "readings.jsonl"

    def calibration_path(self) -> Path:
        return self.folder() / "calibration.json"

    def calibration(self) -> dict[str, Any]:
        try:
            data = json.loads(self.calibration_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as err:
            _LOGGER.warning("Soil calibration unreadable: %s", err)
            return {}
        return data if isinstance(data, dict) else {}

    def calibrate(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """soil_calibrate: {"point": "wet"} takes the current reading as the watered and drained pot, {"point": "dry"}
        as bone-dry soil (optional, without it the scale starts at the sensor's zero); {"reset": true} forgets both."""
        data = self.calibration()
        if body.get("reset") is True:
            data = {}
        else:
            point = body.get("point")
            if point not in ("wet", "dry"):
                raise ValueError("point: wet or dry")
            reading = self.view()
            percent = reading["percent"]
            if percent is None:
                raise CommandRefused(t("soil.no_reading"))
            dry = percent if point == "dry" else float((data.get("dry") or {}).get("percent") or 0)
            wet = percent if point == "wet" else (data.get("wet") or {}).get("percent")
            if wet is not None and float(wet) - dry < MIN_SPAN:
                raise CommandRefused(t("soil.span", dry=i18n.fmt_num(dry)))
            data[point] = {"percent": percent, "at": self.clock()}
        self.folder().mkdir(parents=True, exist_ok=True)
        config.atomic_write(self.calibration_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return {"ok": True, "soil": self.view()}

    async def poll_once(self) -> None:
        try:
            async with self.session.get(self.url, timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as response:
                response.raise_for_status()
                # the gateway does not always label its JSON, so the text is parsed here
                data = json.loads(await response.text())
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            if not self._failing:
                _LOGGER.warning("Soil gateway not answering: %s", type(err).__name__)
            self._failing = True
            return
        self._failing = False
        self.readings = parse_livedata(data)
        self.room = parse_room(data)
        self.heard_at = self.clock()
        self._log(self.heard_at)
        if self.on_reading is not None:
            try:
                self.on_reading(self.view())
            except Exception:  # a broken listener costs its own step, never the reading
                _LOGGER.exception("Soil listener failed")

    def _log(self, at: float) -> None:
        lines = []
        for reading in self.readings:
            last = self._logged.get(reading.channel)
            if last is None or abs(last[1] - reading.percent) >= LOG_STEP or at - last[0] >= LOG_EVERY_S:
                lines.append({"at": at, "channel": reading.channel, "percent": reading.percent,
                              "voltage": reading.voltage})
                self._logged[reading.channel] = (at, reading.percent)
        if not lines:
            return
        try:
            self.folder().mkdir(parents=True, exist_ok=True)
            with self.log_path().open("a", encoding="utf-8", newline="\n") as f:
                f.writelines(json.dumps(line) + "\n" for line in lines)
        except OSError as err:
            _LOGGER.warning("Soil log not written: %s", err)

    def history(self, hours: float) -> list[dict[str, Any]]:
        """The lowest channel's logged readings of the last hours, oldest first."""
        since, rows = self.clock() - hours * 3600, []
        channel = self.readings[0].channel if self.readings else None
        try:
            with self.log_path().open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - HISTORY_TAIL_BYTES))
                lines = f.read().split(b"\n")[0 if size <= HISTORY_TAIL_BYTES else 1:]
        except OSError:
            return []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            at, percent = row.get("at"), row.get("percent") if isinstance(row, dict) else None
            if isinstance(at, int | float) and isinstance(percent, int | float) and at >= since \
                    and (channel is None or row.get("channel") == channel):
                rows.append({"at": at, "percent": percent})
        return rows

    def view(self) -> dict[str, Any]:
        """For /api/state as "soil". No percent when the gateway answered without a soil channel or went silent."""
        age = None if self.heard_at is None else max(0.0, self.clock() - self.heard_at)
        reading = self.readings[0] if self.readings else None
        fresh = age is not None and age <= STALE_S
        percent = reading.percent if reading and fresh else None
        data = self.calibration()
        wet, dry = data.get("wet") or {}, data.get("dry") or {}
        out = {"percent": percent, "channel": reading.channel if reading else None,
               "voltage": reading.voltage if reading else None, "age": age, "fresh": fresh,
               "calibrated": wet.get("percent") is not None, "wet_percent": wet.get("percent"), "wet_at": wet.get("at"),
               "dry_percent": dry.get("percent"), "relative": None, "trigger": None, "too_wet": None, "state": None,
               "room_temp": self.room[0] if fresh else None, "room_humidity": self.room[1] if fresh else None}
        if out["calibrated"]:
            band = targets(self.stage_of() if self.stage_of is not None else None)
            if band is not None:
                out["trigger"], out["too_wet"] = band
                if percent is not None:
                    zero = float(dry.get("percent") or 0)
                    relative = max(0.0, 100 * (percent - zero) / (float(wet["percent"]) - zero))
                    out["relative"] = round(relative, 1)
                    out["state"] = "low" if relative < band[0] else "high" if relative > band[1] else "ok"
        return out

    async def loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:  # a broken answer costs one reading, never the loop
                _LOGGER.exception("Soil reading failed")
            await asyncio.sleep(POLL_S)

    def start(self) -> None:
        self.task = asyncio.create_task(self.loop(), name="soil_sensor")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


class DemoSoil:
    """A made-up reading for the demo's tile and dashboard: no gateway, no log."""

    on_reading = None
    stage_of = None

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock

    def calibrate(self, _body: Mapping[str, Any]) -> dict[str, Any]:
        return {"ok": True, "soil": self.view()}

    def history(self, hours: float) -> list[dict[str, Any]]:
        now, rows = self.clock(), []
        steps = int(hours * 4)
        for i in range(steps):
            days_ago = (steps - i) / 96
            rows.append({"at": now - days_ago * 86400, "percent": round(34 + 18 * ((1 - days_ago / 3) % 1), 1)})
        return rows

    def view(self) -> dict[str, Any]:
        band = targets(None) or (73, 105)
        return {"percent": 42.0, "channel": 1, "voltage": 1.5, "age": 30.0, "fresh": True, "calibrated": True,
                "wet_percent": 52.0, "wet_at": self.clock() - 3 * 86400, "dry_percent": None, "relative": 80.8,
                "trigger": band[0], "too_wet": band[1], "state": "ok", "room_temp": 22.0, "room_humidity": 50.0}

    async def stop(self) -> None:
        return None


def start_soil(session: aiohttp.ClientSession, demo: bool) -> SoilSensor | DemoSoil | None:
    """The sensor when ECOWITT_IP is set, the made-up one in the demo, otherwise None and no tile."""
    if demo:
        return DemoSoil()
    ip = config.ecowitt_ip()
    if not ip:
        return None
    sensor = SoilSensor(session, ip)
    sensor.start()
    return sensor
