"""The record of the device test (scripts/check_devices.py and the owner's own look), for the water dashboard.

A box's report proves a command arrived, not that a fan turns or that water flows. So each device keeps two answers: did
the command arrive, and did the device do it, measured by a sensor or confirmed by the owner. Nothing here switches
anything. One file under STATE_DIR, written only by the server.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import config

_LOGGER = logging.getLogger(__name__)

DEVICES = ("light", "spectrum", "dfan", "cfan", "humidifier", "pump", "tank", "soil", "fan_stick", "growcam", "usb_camera",
           "push")
NUMBERS = ("seconds", "before", "after")


def path() -> Path:
    return config.STATE_DIR / "device-check.json"


def load() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        _LOGGER.warning("Device check unreadable: %s", err)
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: Mapping[str, Any]) -> None:
    config.atomic_write(path(), json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def record(body: Mapping[str, Any], now: float | None = None) -> dict[str, Any]:
    """device_check_record: one device's result. command and effect are true, false or left out (not tested); before,
    after and unit say what a sensor measured. A result of a test replaces the last one of that device."""
    device = body.get("device")
    if device not in DEVICES:
        raise ValueError(f"device: {device!r}")
    row: dict[str, Any] = {"at": time.time() if now is None else now}
    for key in ("command", "effect"):
        value = body.get(key)
        if value is not None and type(value) is not bool:
            raise ValueError(f"{key}: true or false")
        row[key] = value
    for key in NUMBERS:
        value = body.get(key)
        if value is not None and (not isinstance(value, int | float) or isinstance(value, bool)):
            raise ValueError(f"{key}: a number")
        row[key] = value
    row["unit"] = str(body["unit"])[:8] if body.get("unit") else None
    data = load()
    data[device] = row
    _save(data)
    return {"ok": True, "device_check": data}


def confirm(body: Mapping[str, Any], now: float | None = None) -> dict[str, Any]:
    """device_check_confirm: the owner looked or listened; the effect is what they say, the command stays as tested."""
    device, ok = body.get("device"), body.get("ok")
    if device not in DEVICES:
        raise ValueError(f"device: {device!r}")
    if type(ok) is not bool:
        raise ValueError("ok: true or false")
    data = load()
    row = dict(data.get(device) or {})
    row.update(effect=ok, owner=True, owner_at=time.time() if now is None else now)
    data[device] = row
    _save(data)
    return {"ok": True, "device_check": data}
