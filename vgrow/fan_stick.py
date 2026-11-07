"""The AeroWave E6 clip fan on a GrowHub E25 stick: a second VIVOSUN device in the owner's account, next to the box.

The stick has its own shadow, so it gets its own MQTT connection and client id (shared ids would make AWS IoT drop one
of the two connections). The login stays the box's: this class reads its tokens and AWS credentials and connects again
when the box renewed them. Seen on the owner's stick on 06.11.2026 (scripts/capture_accessory.py): once the fan is set
up in the app the shadow reports cFan with lv, manu.lv, mode and osc, and the app's own command was desired.cFan.manu.lv,
the same as the box's circulation fan, so the payloads of vivosun/shadow.py apply. Every command counts in the account's
command history (budget.py).

The rule, off until the owner switches it on: the fan holds the owner's day step while the lamp is on and the night step
while it is off. A change in the app pauses the rule for an hour. The owner's off, here or in the app, switches the rule
off until the owner switches it on again (owner, 01.11.2026: off must not come back by itself).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import config
from .box import CommandRefused, deep_merge
from .budget import BUDGET, CommandBudget
from .i18n import t
from .vivosun import shadow as sh
from .vivosun.const import CFAN_LEVEL_MAP, TOPIC_SHADOW_GET
from .vivosun.models import client_model_token
from .vivosun.mqtt_client import MQTTClient

_LOGGER = logging.getLogger(__name__)

STICK_MODEL = "VSCTL002"
LOOK_S = 30.0  # how often the connection is checked
DEVICE_LOOK_S = 600.0  # how often the account is asked again while no stick is there
REFRESH_S = 300.0  # how often the shadow is read again
PAUSE_S = 3600.0  # a change in the app pauses the rule this long, the same as the autopilot's manual pause
APP_CHANGE_AFTER_S = 45.0  # a report differing this long after our command is the owner's change, not a slow report
PENDING_S = 30.0


def step_of(level: Any) -> int | None:
    """The cockpit's step 0..10 for a reported level: the map's closest step (the app sends the same levels)."""
    if not isinstance(level, int | float) or isinstance(level, bool):
        return None
    return min(range(len(CFAN_LEVEL_MAP)), key=lambda step: abs(CFAN_LEVEL_MAP[step] - level))


def _step(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 10:
        raise ValueError("step: 0..10")
    return value


class FanStick:
    def __init__(self, box: Any, budget: CommandBudget = BUDGET, clock: Callable[[], float] = time.time) -> None:
        self.box = box
        self.budget = budget
        self.clock = clock
        self.device: Any = None
        self.reported: dict[str, Any] = {}
        self.shadow_at: float | None = None
        self.pending: dict[str, Any] | None = None
        self.paused_until: float | None = None
        self.error: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.cfg = self._load()
        self._mqtt: Any = None
        self._creds: Any = None
        self._client_id = f"vgrow-e25-{uuid.uuid4().hex[:12]}"
        self._expected: float | None = None
        self._commanded_at = 0.0
        self._device_look = 0.0
        self._refresh = 0.0

    # ---------- settings ----------

    @staticmethod
    def path() -> Path:
        return config.STATE_DIR / "fan-stick.json"

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
            return {"auto": data.get("auto") is True, "day_step": _step(data.get("day_step")),
                    "night_step": _step(data.get("night_step"))}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as err:
            _LOGGER.warning("Fan stick settings unreadable, the rule stays off: %s", err)
        return {"auto": False, "day_step": None, "night_step": None}

    def _save(self) -> None:
        config.atomic_write(self.path(), json.dumps(self.cfg) + "\n")

    def configure(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """fan_stick_config: the rule's day and night step and its switch. Without steps it takes the fan's own step."""
        auto = body.get("auto", self.cfg["auto"])
        if type(auto) is not bool:
            raise ValueError("auto: boolean")
        current = step_of(self.fan().get("lv"))
        day = _step(body.get("day_step", self.cfg["day_step"]))
        day = day if day is not None else current
        night = _step(body.get("night_step", self.cfg["night_step"]))
        night = night if night is not None else day
        if auto and day is None:
            raise CommandRefused(t("box.no_connection"))
        self.cfg = {"auto": auto, "day_step": day, "night_step": night}
        self.paused_until = None
        self._save()
        return {"ok": True, "fan_stick": self.view()}

    # ---------- the connection ----------

    def connected(self) -> bool:
        return self._mqtt is not None and bool(self._mqtt.is_connected)

    async def _look(self) -> None:
        box = self.box
        if not all(getattr(box, name, None) is not None for name in ("_api", "_tokens", "_aws", "_identity", "_creds")):
            return
        now = self.clock()
        if self.device is None:
            if now < self._device_look:
                return
            self._device_look = now + DEVICE_LOOK_S
            devices = await box._api.get_devices(box._tokens)
            self.device = next((d for d in devices if client_model_token(d.client_id) == STICK_MODEL), None)
            if self.device is None:
                return
        if not self.connected() or self._creds is not box._creds:
            await self._connect()
        elif now >= self._refresh:
            await self._read_shadow()

    async def _connect(self) -> None:
        box = self.box
        if self._mqtt is not None:
            await self._mqtt.disconnect()
            self._mqtt = None
        url = box._aws.sigv4_sign_mqtt_url(endpoint=box._identity.aws_host, region=box._identity.aws_region,
                                           credentials=box._creds)
        client = MQTTClient(websocket_url=url, thing=self.device.client_id, topic_prefix=self.device.topic_prefix,
                            client_id=self._client_id, label="e25")
        await client.connect()
        client.add_message_callback(self.on_message)
        self._mqtt, self._creds = client, box._creds
        await self._read_shadow()

    async def _read_shadow(self) -> None:
        self._refresh = self.clock() + REFRESH_S
        await self._mqtt.publish(TOPIC_SHADOW_GET.format(thing=self.device.client_id), b"{}")

    def on_message(self, _topic: str, payload: bytes, _qos: int) -> None:
        try:
            document = json.loads(payload)
        except ValueError:
            return
        reported = sh.extract_shadow_reported(document) if isinstance(document, dict) else None
        if not reported:
            return
        deep_merge(self.reported, reported)
        self.shadow_at = self.clock()
        if self._expected is None:
            self._expected = self.fan().get("lv")
        if self.pending is not None and self.pending["read"]() == self.pending["target"]:
            self.pending = None

    async def loop(self) -> None:
        while True:
            try:
                await self._look()
                self.error = None
            except Exception as err:  # a broken stick costs its card, never the box
                if self.error is None:
                    _LOGGER.warning("Fan stick not reachable: %s", type(err).__name__)
                self.error = type(err).__name__
            await asyncio.sleep(LOOK_S)

    def start(self) -> None:
        self.task = asyncio.create_task(self.loop(), name="fan_stick")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self._mqtt is not None:
            await self._mqtt.disconnect()
            self._mqtt = None

    # ---------- what it reports ----------

    def fan(self) -> dict[str, Any]:
        fan = self.reported.get("cFan")
        return fan if isinstance(fan, dict) else {}

    def view(self) -> dict[str, Any]:
        fan = self.fan()
        pending = self.pending
        if pending is not None and self.clock() > pending["until"]:
            pending = self.pending = None
        return {"found": self.device is not None, "name": getattr(self.device, "name", None),
                "online": self.connected() and self.reported.get("connected") is True,
                "fan_seen": "lv" in fan, "level": fan.get("lv"), "step": step_of(fan.get("lv")),
                "osc": bool(fan.get("osc")), "manual": fan.get("mode") in (0, None),
                "age": None if self.shadow_at is None else max(0.0, self.clock() - self.shadow_at),
                "pending": pending is not None, "auto": self.cfg["auto"], "day_step": self.cfg["day_step"],
                "night_step": self.cfg["night_step"], "paused_until": self.paused_until, "error": self.error}

    # ---------- commands ----------

    async def _publish(self, origin: str, field: str, payload: dict[str, Any]) -> None:
        if not self.connected():
            raise CommandRefused(t("box.no_connection"))
        body = json.dumps({**payload, "clientToken": uuid.uuid4().hex[:16]})
        await self.budget.send(origin, f"e25.{field}", lambda: self._mqtt.publish_shadow_update(body, qos=1))

    async def set_step(self, step: Any, origin: str = "manual") -> None:
        step = _step(step)
        if step is None:
            raise ValueError("step: 0..10")
        level = CFAN_LEVEL_MAP[step]
        await self._publish(origin, "level", sh.build_cfan_level_payload(level))
        self._expected, self._commanded_at = level, self.clock()
        self.pending = {"read": lambda: self.fan().get("lv"), "target": level, "until": self.clock() + PENDING_S}
        if origin == "manual" and self.cfg["auto"]:
            if step == 0:
                self.cfg = {**self.cfg, "auto": False}  # off is off, until the owner switches the rule on again
                self._save()
            else:
                self.paused_until = self.clock() + PAUSE_S

    async def set_osc(self, on: Any) -> None:
        if type(on) is not bool:
            raise ValueError("on: boolean")
        await self._publish("manual", "osc", sh.build_cfan_oscillate_payload(on))
        self.pending = {"read": lambda: bool(self.fan().get("osc")), "target": on, "until": self.clock() + PENDING_S}

    async def check(self, snap: Mapping[str, Any]) -> None:
        """A step of every poll: holds the owner's step for lamp on or off. Never touches a fan that is not reporting."""
        if not self.cfg["auto"] or not self.connected() or "lv" not in self.fan():
            return
        now, level = self.clock(), self.fan()["lv"]
        if self._expected is not None and level != self._expected and now - self._commanded_at > APP_CHANGE_AFTER_S:
            self._expected = level  # changed in the app: the owner's word
            if level == 0:
                self.cfg = {**self.cfg, "auto": False}
                self._save()
            else:
                self.paused_until = now + PAUSE_S
            return
        if self.paused_until is not None:
            if now < self.paused_until:
                return
            self.paused_until = None
        lamp = (snap.get("light") or {}).get("level")
        if not isinstance(lamp, int | float) or self.fan().get("mode") not in (0, None) or self.pending is not None:
            return
        target = self.cfg["day_step"] if lamp > 0 else self.cfg["night_step"]
        if target is not None and step_of(level) != target:
            await self.set_step(target, origin="fan_rule")


class DemoFanStick:
    """The demo's fan: answers at once, no cloud."""

    def __init__(self) -> None:
        self.state = {"found": True, "name": "GrowHub E25", "online": True, "fan_seen": True, "level": CFAN_LEVEL_MAP[3],
                      "step": 3, "osc": False, "manual": True, "age": 5.0, "pending": False, "auto": False,
                      "day_step": 3, "night_step": 3, "paused_until": None, "error": None}

    def view(self) -> dict[str, Any]:
        return dict(self.state)

    def configure(self, body: Mapping[str, Any]) -> dict[str, Any]:
        for key in ("auto", "day_step", "night_step"):
            if key in body:
                self.state[key] = body[key]
        return {"ok": True, "fan_stick": self.view()}

    async def set_step(self, step: Any, origin: str = "manual") -> None:
        step = _step(step)
        if step is None:
            raise ValueError("step: 0..10")
        self.state.update(step=step, level=CFAN_LEVEL_MAP[step])

    async def set_osc(self, on: Any) -> None:
        self.state["osc"] = bool(on)

    async def check(self, _snap: Mapping[str, Any]) -> None:
        return None

    async def stop(self) -> None:
        return None


def start_fan_stick(box: Any, demo: bool) -> FanStick | DemoFanStick:
    if demo:
        return DemoFanStick()
    stick = FanStick(box)
    stick.start()
    return stick
