"""Account-wide command history, by origin, without hourly or daily command quotas.

The old budget interface and JSON filename remain compatible with existing callers and stored history.
Counts are diagnostic only. Legacy held_until values and unreadable history never block a command.
Device ownership, timers, confirmations, retries and safety shutdowns belong to their controllers.
More than SAFETY_ALARM_PER_HOUR safety commands still identifies a fault in the autopilot; an off is never blocked.
The last 24 hours are retained across restarts. Only one server may control an account (README, "Autopilot").
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import config

_LOGGER = logging.getLogger(__name__)

SAFETY_ALARM_PER_HOUR = 20
HOUR, DAY = 3600, 86400
FILE = "command-budget.json"
AUTOMATIC = ("autopilot", "humidifier", "watering", "light_schedule")
ACTUATORS = ("set_light", "set_spectrum", "set_cfan", "set_dfan", "set_port")


class BudgetExceeded(Exception):
    """Compatibility exception for an adapter that explicitly defers a command until `until`.

    CommandBudget itself imposes no quotas and never raises this exception.
    """

    def __init__(self, scope: str, until: float) -> None:
        super().__init__(f"command budget reached ({scope}), free again at {until:.0f}")
        self.scope, self.until = scope, until


class CommandBudget:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._memory: list[dict[str, Any]] = []  # used only when the file cannot be written

    @staticmethod
    def path() -> Path:
        return config.STATE_DIR / FILE  # read at call time: the demo and the tests move STATE_DIR

    def _raw_data(self) -> tuple[dict[str, Any], bool]:
        """The stored record and whether it could be read (a missing file is readable: nothing sent yet)."""
        try:
            text = self.path().read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, True
        except OSError:
            return {}, False
        try:
            data = json.loads(text)
        except ValueError:
            return {}, False
        if not isinstance(data, dict) or not isinstance(data.get("commands", []), list):
            return {}, False
        return data, True

    def _raw(self) -> tuple[list[Any], bool]:
        data, readable = self._raw_data()
        return list(data.get("commands", [])), readable

    def readable(self) -> bool:
        return self._raw_data()[1]

    def entries(self, now: float | None = None) -> list[dict[str, Any]]:
        """The last 24 hours, including rows up to an hour ahead after a backward clock adjustment."""
        now = self._clock() if now is None else now
        raw, _ = self._raw()
        rows = [r for r in raw if isinstance(r, dict) and isinstance(r.get("at"), int | float)]
        rows += [r for r in self._memory if r not in rows]
        return sorted((r for r in rows if -HOUR <= now - r["at"] < DAY), key=lambda r: r["at"])

    def counts(self, now: float | None = None) -> dict[str, int]:
        now = self._clock() if now is None else now
        rows = self.entries(now)
        regular = [r for r in rows if r.get("origin") in AUTOMATIC and not r.get("safety")]
        safety = [r for r in rows if r.get("origin") in AUTOMATIC and r.get("safety")]
        return {"auto_hour": sum(1 for r in regular if now - r["at"] < HOUR), "auto_day": len(regular),
                "safety_hour": sum(1 for r in safety if now - r["at"] < HOUR),
                "manual_hour": sum(1 for r in rows if r.get("origin") not in AUTOMATIC and now - r["at"] < HOUR),
                "all_day": len(rows)}

    def free_at(self, now: float | None = None) -> float | None:
        """No command-count quota can delay a send."""
        return None

    def check(self, origin: str, *, safety: bool = False, now: float | None = None) -> None:
        """Retained caller interface; command history does not gate sends."""
        return None

    def record(self, origin: str, field: str, *, safety: bool = False, box: str = "box-1",
               now: float | None = None) -> None:
        now = self._clock() if now is None else now
        row = {"at": now, "origin": origin, "field": field, "safety": bool(safety), "box": box}
        rows = [*self.entries(now), row]
        record: dict[str, Any] = {"commands": rows}
        try:
            config.atomic_write(self.path(), json.dumps(record) + "\n")
            self._memory = []
        except OSError as err:
            _LOGGER.warning("Command history not saved (%s); this process counts in memory", err)
            self._memory.append(row)

    async def send(self, origin: str, field: str, call: Callable[[], Awaitable[Any]], *, safety: bool = False,
                   box: str = "box-1") -> Any:
        """Send and record. A command refused before it went out (CommandRefused) is not counted; any other
        failure is, because it may have reached the cloud."""
        from .box import CommandRefused  # here: box imports nothing of this module, and this keeps it that way

        self.check(origin, safety=safety)
        try:
            result = await call()
        except CommandRefused:
            raise
        except Exception:
            self.record(origin, field, safety=safety, box=box)
            raise
        self.record(origin, field, safety=safety, box=box)
        return result

    def view(self) -> dict[str, Any]:
        counts = self.counts()
        return {**counts, "per_hour": None, "per_day": None, "free_at": None,
                "limited": False, "recording_error": not self.readable()}


BUDGET = CommandBudget()


class Counted:
    """The box as the page's commands see it: every actuator call is counted as "manual" (never blocked)."""

    def __init__(self, box: Any, budget: CommandBudget | None = None, origin: str = "manual") -> None:
        self._box, self._budget, self._origin = box, budget or BUDGET, origin

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._box, name)
        if name not in ACTUATORS:
            return attr

        async def counted(*args: Any, **kwargs: Any) -> Any:
            return await self._budget.send(self._origin, name, lambda: attr(*args, **kwargs))

        return counted
