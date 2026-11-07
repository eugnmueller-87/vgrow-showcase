"""The port guard: which device hangs on which USB port, and a stop for all automation when that changes.

Why (critique of the autopilot draft, 23.10.2026, M1): the humidifier rule was bound to "usb2" alone. The owner plans
a pump on a USB port; had it landed on USB 2, the humidity band would have switched the pump (a flood). So:
- The owner says on the page what hangs on a port ("An USB 2 hängt: Befeuchter / Pumpe / anderes", two taps). The
  humidifier rule switches a port on only if the OWNER named the humidifier there (review 2, M3). ports.json is a
  repository file the page cannot change; it can only veto: a port whose "device" there names something else (a pump)
  is never switched, whatever the page says. A port without a "device" in ports.json is no humidifier by its label
  any more ("Pumpe Befeuchter-Tank" would have counted, review 1, N5).
- The assignment of every port (ports.json device and label, plus the accessory code the box reports as "dev") is
  remembered as confirmed. The very first one is taken as seen ("first_seen"): it lets the autopilot notice a later
  change, but it never allows the humidifier rule to switch anything on; only the owner's choice does. Any later
  change pauses ALL automation (the humidifier rule's "on" and the autopilot) until the owner confirms the new
  assignment on the page (two taps). Confirming a change forgets the owner's choice for every port whose entry
  changed, so whatever now hangs on USB 2 is switched by humidity only after the owner names it again (review 1, S8).
  Whether "dev" tells a pump from a humidifier is not known (a guess, critique M1); a changed ports.json is caught
  either way.
- An unreadable record never confirms what is plugged in now: it counts as a change (review 1, S7). Only a record
  that does not exist yet starts from "first_seen".
- A port without a report yet is "unknown": nothing is switched on on it, and it is no change.
The record lives in STATE_DIR/boxes/<box>/port-assignment.json, never in the repository.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from . import config

_LOGGER = logging.getLogger(__name__)

HUMIDIFIER = "humidifier"
CHOICES = ("humidifier", "pump", "other")  # what the owner can say hangs on a switchable port


def configured_device(port: str) -> str | None:
    """What ports.json says hangs on `port` (its "device"); None when it names nothing or cannot be read."""
    rules = config.port_rules()
    rule = (rules or {}).get(port) or {}
    device = rule.get("device")
    return device if isinstance(device, str) and device else None


def assignment(snap: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """Every port's device, label and reported accessory code; None while a port has no report yet."""
    ports = snap.get("ports") or {}
    if not ports:
        return None
    rules = config.port_rules()
    out = {}
    for name, report in sorted(ports.items()):
        if not isinstance(report, dict) or report.get("on") is None:
            return None
        rule = (rules or {}).get(name) or {}
        out[name] = {"device": configured_device(name) if rules is not None else None,
                     "label": rule.get("label") if rules is not None else None,
                     "accessory": report.get("accessory")}
    return out


class PortGuard:
    def __init__(self, box_id: str = "box-1") -> None:
        self.box_id = box_id

    def path(self) -> Path:
        return config.STATE_DIR / "boxes" / self.box_id / "port-assignment.json"

    def _read(self) -> tuple[dict[str, Any], str | None]:
        """The record and what is wrong with it: None, "absent" (never written) or "unreadable"."""
        try:
            raw = self.path().read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, "absent"
        except OSError:
            return {}, "unreadable"
        try:
            data = json.loads(raw)
        except ValueError:
            return {}, "unreadable"
        if not isinstance(data, dict) or not isinstance(data.get("confirmed"), dict):
            return {}, "unreadable"
        return data, None

    def _write(self, data: dict[str, Any]) -> None:
        try:
            config.atomic_write(self.path(), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        except OSError as err:
            _LOGGER.warning("Port assignment not saved: %s", err)

    def status(self, snap: dict[str, Any]) -> str:
        """"ok", "unknown" (a port without a report) or "changed" (pause everything until confirmed; also an
        unreadable record)."""
        now = assignment(snap)
        data, problem = self._read()
        if problem == "unreadable":
            return "changed"
        if now is None:
            return "changed" if data.get("changed") else "unknown"
        if problem == "absent":
            self._write({"confirmed": now, "confirmed_at": time.time(), "by": "first_seen", "devices": {}})
            return "ok"
        confirmed = data["confirmed"]
        if now == confirmed:
            if data.get("changed"):  # back as it was (a flicker, or the owner undid it): nothing to confirm
                self._write({**data, "changed": None})
            return "ok"
        if data.get("changed") != now:
            _LOGGER.warning("Port assignment changed: %s -> %s; all automation pauses until confirmed", confirmed, now)
            self._write({**data, "changed": now, "changed_at": time.time()})
        return "changed"

    def changed(self) -> dict[str, Any] | None:
        """The confirmed and the new assignment while a change waits for the owner, else None. An unreadable record
        is a change with nothing confirmed."""
        data, problem = self._read()
        if problem == "unreadable":
            return {"confirmed": None, "now": None, "since": None, "unreadable": True}
        if not data.get("changed"):
            return None
        return {"confirmed": data.get("confirmed"), "now": data.get("changed"), "since": data.get("changed_at")}

    def devices(self) -> dict[str, str]:
        """What the owner said hangs on each port (only ports he named)."""
        data, _ = self._read()
        found = data.get("devices") if isinstance(data.get("devices"), dict) else {}
        return {k: v for k, v in found.items() if v in CHOICES}

    def owner_device(self, port: str) -> str | None:
        return self.devices().get(port)

    def assigned_at(self, port: str) -> float | None:
        data, _ = self._read()
        at = (data.get("devices_at") or {}).get(port) if isinstance(data.get("devices_at"), dict) else None
        return at if isinstance(at, int | float) else None

    def confirm(self, snap: dict[str, Any]) -> list[str] | None:
        """The owner's two taps on a changed assignment: what the box reports now is the confirmed one. Returns the
        ports whose entry changed (the owner's choice for them is forgotten), or None when a port has no report yet."""
        now = assignment(snap)
        if now is None:
            return None
        data, _ = self._read()
        before = data.get("confirmed") if isinstance(data.get("confirmed"), dict) else {}
        devices = dict(self.devices())
        moved = sorted(p for p in set(before) | set(now) if before.get(p) != now.get(p))
        if not before:
            moved = sorted(now)  # nothing readable was confirmed: every port is named again
        for port in moved:
            devices.pop(port, None)
        self._write({"confirmed": now, "confirmed_at": time.time(), "by": "owner", "changed": None,
                     "devices": devices, "devices_at": {p: a for p, a in (data.get("devices_at") or {}).items()
                                                        if p in devices}})
        return moved

    def assign(self, port: str, device: str, snap: dict[str, Any]) -> bool:
        """The owner's two taps "An USB 2 hängt: ...": his word for this port, and the assignment the box reports
        now is confirmed with it. False when a port has no report yet."""
        if device not in CHOICES:
            raise ValueError(device)
        now = assignment(snap)
        if now is None or port not in now:
            return False
        data, _ = self._read()
        devices = {**self.devices(), port: device}
        devices_at = {**(data.get("devices_at") if isinstance(data.get("devices_at"), dict) else {}),
                      port: time.time()}
        self._write({"confirmed": now, "confirmed_at": time.time(), "by": "owner", "changed": None,
                     "devices": devices, "devices_at": devices_at})
        return True

    def may_switch_humidifier(self, port: str, snap: dict[str, Any]) -> str | None:
        """None when the humidifier rule may switch `port` on; otherwise why not: "not_humidifier" (ports.json or the
        owner names something else there: nothing is switched at all), "unassigned" (the owner has not named the
        humidifier there yet), "changed" or "unknown" (the assignment)."""
        listed = configured_device(port)
        if listed is not None and listed != HUMIDIFIER:
            return "not_humidifier"
        owner = self.owner_device(port)
        if owner is not None and owner != HUMIDIFIER:
            return "not_humidifier"
        status = self.status(snap)
        if status != "ok":
            return status
        data, _ = self._read()
        if owner != HUMIDIFIER or data.get("by") != "owner":
            return "unassigned"
        return None
