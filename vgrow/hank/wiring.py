"""The live collector (spec 4.2): what Hank reads of the running cockpit, read-only.

Collector(box, ...) holds the cockpit's objects and hands out, per call, the mapping livestate.collect() reads:
snapshot (box.snapshot()), camera (camera.status()), plant_view (plant.view), automation_view
(HumidifierAutomation.view), alerts_view (Alerts.view), feeding_view (feeding.view), vision (only busy), ports
(config.port_rules()) and now. The names are those of tests/hank_eval/fixtures.Live.sources(). The ledger and Hank's
journal are Hank's own; core.py adds them.

Nothing here switches, writes or sends. The only methods called on the cockpit's objects are the read-only views
listed above; the box in particular is only ever asked for snapshot(). Every part is read on its own: a part that
fails is left out (and logged), so a broken camera or an unreadable plant.json never stops Hank, and Hank never stops
them. The camera only exists after box.start(), so it may be given as a callable that returns the current one (the
server keeps it in its `parts` dict).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from vgrow import config, feeding, plant

_LOGGER = logging.getLogger(__name__)

PARTS = ("snapshot", "camera", "plant_view", "automation_view", "alerts_view", "feeding_view", "vision", "ports",
         "now")


def _current(value: Any, key: str) -> Any:
    """The object itself, the one a callable returns now, or the one under `key` in a mapping (the server's parts)."""
    if isinstance(value, Mapping):
        return value.get(key)
    return value() if callable(value) else value


class Collector:
    """Reads the cockpit for Hank. box: anything with snapshot(); camera and vision: the object, a callable returning
    the current one, or a mapping holding it under "camera" or "vision" (both exist only after box.start(), the server
    keeps them in its `parts`), or None; plant_log, automation, alerts: the server's objects or None. clock: the time
    of the collected moment (default datetime.now)."""

    def __init__(self, box: Any, *, camera: Any = None, plant_log: plant.PlantLog | None = None,
                 automation: Any = None, alerts: Any = None, vision: Any = None,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self._box = box
        self._camera = camera
        self._plant_log = plant_log
        self._automation = automation
        self._alerts = alerts
        self._vision = vision
        self._clock = clock
        self.failures: dict[str, str] = {}  # part -> last error, for GET /api/hank/state and the logs

    def camera(self) -> Any:
        return _current(self._camera, "camera")

    def vision(self) -> Any:
        return _current(self._vision, "vision")

    def _part(self, name: str, read: Callable[[], Any]) -> Any:
        try:
            value = read()
        except Exception as err:  # noqa: BLE001 (spec 10.1 rule 7: a broken part never stops Hank or the cockpit)
            if self.failures.get(name) != repr(err):
                _LOGGER.warning("Hank live state: %s unreadable (%r), left out", name, err)
            self.failures[name] = repr(err)
            return None
        self.failures.pop(name, None)
        return value

    def __call__(self, lang: str = "de") -> dict[str, Any]:
        """One moment of the cockpit, views in `lang` (the answer's language: plant.view and feeding.view localize
        their texts)."""
        out: dict[str, Any] = {}
        snap = self._part("snapshot", lambda: dict(self._box.snapshot()) if self._box is not None else None)
        snap = snap if isinstance(snap, dict) else {}
        out["snapshot"] = snap
        cam = self.camera()
        out["camera"] = self._part("camera", lambda: cam.status() if cam is not None else {"configured": False}) \
            or {}
        profile = self._part("profile", plant.load_profile)
        log = self._plant_log
        stage = None
        if profile and log is not None:
            stage = self._part("stage", lambda: plant.current_stage(profile, log))
        if log is not None:
            out["plant_view"] = self._part("plant_view", lambda: plant.view(profile, log, snap, lang)) or {}
            if profile:
                # feeding v2: the same keywords as the page (feeding.context: stage, owner stages, age, setups) and
                # every unit the page shows: Hank writes each amount in the active card's unit (feedplan.py), so the
                # part-1 guard (LEGACY_UNITS, view "withheld") opens for him too.
                out["feeding_view"] = self._part("feeding_view", lambda: feeding.view(
                    feeding.setup_of(profile, log), lang, amount_units=feeding.PAGE_UNITS,
                    **feeding.context(profile, log, lang))) or {}
        if self._automation is not None:
            out["automation_view"] = self._part("automation_view", lambda: self._automation.view(snap)) or {}
        if self._alerts is not None:
            out["alerts_view"] = self._part("alerts_view", lambda: self._alerts.view(stage)) or []
        vision = self.vision()
        out["vision"] = {"busy": bool(getattr(vision, "busy", False))} if vision is not None else {"busy": False}
        out["ports"] = self._part("ports", config.port_rules) or {}
        out["now"] = self._clock()
        return out


def empty(lang: str = "de") -> dict[str, Any]:
    """No cockpit at all (the CLI without --demo, a Hank built without a collector): an empty moment."""
    del lang
    return {"snapshot": {}, "camera": {}, "plant_view": {}, "automation_view": {}, "alerts_view": [],
            "feeding_view": {}, "vision": {"busy": False}, "ports": config.port_rules() or {}, "now": datetime.now()}
