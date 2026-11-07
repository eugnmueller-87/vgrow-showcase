"""Read-only pump history from actual USB reports, including short runs made in the VIVOSUN app.

Minute rows remain usable as sampled history. They never prove that no short run happened between polls.
Neither a command nor a wet tank proves a pump run or an amount of water. Identity is captured when observed;
legacy rows before the owner's port confirmation remain unidentified USB observations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from .measurements import MeasurementStore, device_of
from .portguard import PortGuard, configured_device

_LOGGER = logging.getLogger(__name__)
MAX_GAP = 180


class PumpHistory:
    def __init__(self, store: MeasurementStore | None, guard: PortGuard | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.guard = guard or PortGuard()
        self.clock = clock
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=2048)
        self.task: asyncio.Task[Any] | None = None
        self.box: Any = None
        self.failed = False

    def identities(self) -> dict[str, str]:
        return {port: device for port, device in self.guard.devices().items()
                if configured_device(port) in (None, device)}

    def start(self, box: Any) -> None:
        if self.store is None or not hasattr(box, "port_listeners") or self.task is not None:
            return
        self.box = box
        box.port_listeners.append(self.observe)
        box.listeners.append(self.poll)
        self.task = asyncio.create_task(self._writer(), name="pump_report_history")

    def observe(self, box: Any, ports: Mapping[str, Any], at: float | None, source: str = "shadow") -> None:
        if self.store is None or at is None:
            return
        if source == "shadow":
            snap = box.snapshot()
            if not snap.get("mqtt") or snap.get("online") is False:
                ports = {p: None for p in ports}  # a retained shadow of an offline box is not a new pump run
        devices = self.identities()
        records = [{"at": at, "port": p, "on": value if type(value) is bool else None,
                    "device": devices.get(p), "source": source} for p, value in ports.items()]
        try:
            self.queue.put_nowait((records, device_of(box)))
        except asyncio.QueueFull:
            self.failed = True
            _LOGGER.warning("Pump history queue is full; some USB observations could not be saved")

    async def poll(self, box: Any) -> None:
        snap = box.snapshot()
        linked = snap.get("mqtt") and snap.get("online")
        ports = {p: r.get("on") if linked else None for p, r in (snap.get("ports") or {}).items()}
        self.observe(box, ports, self.clock(), "poll")

    async def _writer(self) -> None:
        while True:
            records, device = await self.queue.get()
            try:
                await asyncio.to_thread(self.store.add_port_observations, records, device)
            except Exception:
                self.failed = True
                _LOGGER.exception("USB observations could not be saved; pump history is incomplete")
            finally:
                self.queue.task_done()

    async def stop(self) -> None:
        if self.box is not None:
            self.box.port_listeners.remove(self.observe)
            self.box.listeners.remove(self.poll)
            self.box = None
        if self.task is not None:
            try:
                await asyncio.wait_for(self.queue.join(), timeout=5)
            except TimeoutError:
                _LOGGER.warning("Pump history did not finish writing before shutdown")
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    def brief(self, snap: Mapping[str, Any]) -> dict[str, Any]:
        now = self.clock()
        devices = self.identities()
        pump_ports = [p for p, device in devices.items() if device == "pump"]
        states = [((snap.get("ports") or {}).get(p) or {}).get("on") for p in pump_ports]
        linked = snap.get("mqtt") and snap.get("online")
        current = (any(states) if states and all(type(s) is bool for s in states) else None) if linked else None
        zone = self.store.tz if self.store is not None else None
        moment = datetime.fromtimestamp(now, zone).astimezone() if zone is None else datetime.fromtimestamp(now, zone)
        out: dict[str, Any] = {"date": moment.date().isoformat(), "timezone": str(moment.tzinfo),
                               "available": False, "complete": False, "current_on": current,
                               "pump_ports": pump_ports, "runs": [], "unidentified_on": 0,
                               "observations": 0, "first_observation": None, "last_observation": None,
                               "recording_error": self.failed}
        try:
            if self.store is not None:
                start, end = self.store.day_bounds(moment.date())
                end = min(end, now + 0.000001)
                samples = self.store.port_observations(start, end)
                first = {}
                for row in samples:
                    first.setdefault(row["port"], row["at"])
                legacy = [r for r in self.store.legacy_port_observations(start, end)
                          if r["at"] < first.get(r["port"], float("inf"))]
                for row in legacy:
                    port = row["port"]
                    confirmed = self.guard.assigned_at(port)
                    if devices.get(port) == "pump" and confirmed is not None and row["at"] >= confirmed:
                        row["device"] = "pump"
                    elif row["state"] == 1 and devices.get(port) == "pump":
                        out["unidentified_on"] += 1
                samples = sorted(legacy + samples, key=lambda r: r["at"])
                out["available"] = self.store.file_for().exists()
            else:
                samples = []
            confirmed = [r for r in samples if r.get("device") == "pump"]
            out["observations"] = len(confirmed)
            if confirmed:
                out.update(first_observation=confirmed[0]["at"], last_observation=confirmed[-1]["at"])
            relevant = list(samples)  # an unknown or different accessory breaks a pump's observed span
            # A current genuine on report is useful even before the background writer has caught up.
            if current:
                relevant += [{"at": now, "port": p, "state": 1, "device": "pump", "source": "current"}
                             for p in pump_ports if ((snap.get("ports") or {}).get(p) or {}).get("on") is True]
            opened: dict[str, dict[str, Any]] = {}
            for row in relevant:
                port, at = row["port"], row["at"]
                if row.get("device") != "pump":
                    opened.pop(port, None)
                    continue
                run = opened.get(port)
                if run is not None and at - run["last_on"] > MAX_GAP:
                    opened.pop(port)
                    run = None
                if row["state"] == 1:
                    if run is None:
                        run = {"port": port, "first_on": at, "last_on": at, "off_seen": None,
                               "approximate": True}
                        out["runs"].append(run)
                        opened[port] = run
                    run["last_on"] = at
                elif run is not None:
                    if row["state"] == 0:
                        run["off_seen"] = at
                    opened.pop(port)
            out["observed_on"] = bool(out["runs"])
            for run in out["runs"]:
                for key in ("first_on", "last_on", "off_seen"):
                    value = run[key]
                    local = (datetime.fromtimestamp(value, zone) if zone else datetime.fromtimestamp(value).astimezone()
                             ) if value is not None else None
                    run[key + "_local"] = local.isoformat() if local is not None else None
        except Exception:
            _LOGGER.exception("Pump history could not be read")
            out.update(available=False, observed_on=bool(current), recording_error=True)
        return out
