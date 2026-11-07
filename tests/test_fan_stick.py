"""The E6 on the E25 stick (vgrow/fan_stick.py): what the stick reported on 06.11.2026, commands, the rule, off is off."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow.alerts import Alerts
from vgrow.box import CommandRefused
from vgrow.budget import CommandBudget
from vgrow.demo import DemoBox
from vgrow.fan_stick import APP_CHANGE_AFTER_S, PAUSE_S, STICK_MODEL, DemoFanStick, FanStick, step_of, start_fan_stick
from vgrow.server import make_app


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeMqtt:
    is_connected = True

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.published: list[str] = []

    async def publish_shadow_update(self, body: str, qos: int = 0) -> None:
        self.updates.append(json.loads(body))

    async def publish(self, topic: str, _payload: bytes) -> None:
        self.published.append(topic)

    async def disconnect(self) -> None:
        self.is_connected = False


# The stick's report on 06.11.2026 after the owner set the fan up in the app and chose level 60 (shortened to its keys)
REPORT = {"state": {"reported": {"connected": True, "ctrlTyp": 11, "netVer": "1.4.9",
                                 "cFan": {"lv": 60, "manu": {"lv": 60}, "mode": 0, "nw": 0, "osc": 0, "inPlan": 0}}}}


def stick(*, report: bool = True) -> tuple[FanStick, FakeMqtt, Clock]:
    clock = Clock()
    fan = FanStick(SimpleNamespace(), budget=CommandBudget(clock=clock), clock=clock)
    fan.device = SimpleNamespace(name="GrowHub E25 stick", client_id=f"{STICK_MODEL}-x", topic_prefix="p")
    mqtt = FakeMqtt()
    fan._mqtt = mqtt
    if report:
        fan.on_message("t", json.dumps(REPORT).encode(), 1)
    return fan, mqtt, clock


def report_level(fan: FanStick, level: int) -> None:
    fan.on_message("t", json.dumps({"state": {"reported": {"cFan": {"lv": level, "manu": {"lv": level}}}}}).encode(), 1)


def test_the_levels_the_app_sends_are_the_box_steps() -> None:
    assert [step_of(v) for v in (0, 44, 60, 100)] == [0, 1, 3, 10]
    assert step_of(62) == 3 and step_of(None) is None and step_of(True) is None


def test_view_reads_what_the_stick_reported() -> None:
    fan, _mqtt, _clock = stick()
    view = fan.view()
    assert view["online"] is True and view["fan_seen"] is True and view["step"] == 3 and view["osc"] is False
    assert view["manual"] is True and view["auto"] is False


def test_a_stick_without_a_fan_says_so() -> None:
    fan, _mqtt, _clock = stick(report=False)
    fan.on_message("t", json.dumps({"state": {"reported": {"connected": True, "ctrlTyp": 0}}}).encode(), 1)
    assert fan.view()["fan_seen"] is False and fan.view()["online"] is True


def test_broken_messages_change_nothing() -> None:
    fan, _mqtt, _clock = stick()
    for payload in (b"not json", b"[]", b"{}"):
        fan.on_message("t", payload, 1)
    assert fan.view()["step"] == 3


def test_a_step_sends_the_same_command_as_the_app() -> None:
    fan, mqtt, _clock = stick()
    asyncio.run(fan.set_step(6))
    desired = mqtt.updates[-1]["state"]["desired"]["cFan"]
    assert desired["manu"]["lv"] == 75 and desired["mode"] == 0
    assert fan.view()["pending"] is True
    report_level(fan, 75)
    assert fan.view()["pending"] is False and fan.view()["step"] == 6


def test_oscillation_and_bad_values() -> None:
    fan, mqtt, _clock = stick()
    asyncio.run(fan.set_osc(True))
    assert mqtt.updates[-1]["state"]["desired"]["cFan"]["osc"] in (1, True)
    for bad in ("on", None):
        with pytest.raises(ValueError):
            asyncio.run(fan.set_osc(bad))
    for bad in (11, -1, "3", None, 2.5):
        with pytest.raises(ValueError):
            asyncio.run(fan.set_step(bad))


def test_no_connection_no_command() -> None:
    fan, mqtt, _clock = stick()
    mqtt.is_connected = False
    with pytest.raises(CommandRefused):
        asyncio.run(fan.set_step(3))


def test_the_rule_is_off_until_the_owner_switches_it_on() -> None:
    fan, mqtt, _clock = stick()
    asyncio.run(fan.check({"light": {"level": 0}}))
    assert mqtt.updates == []


def test_the_rule_follows_the_lamp() -> None:
    fan, mqtt, _clock = stick()
    fan.configure({"auto": True, "day_step": 3, "night_step": 1})
    asyncio.run(fan.check({"light": {"level": 40}}))
    assert mqtt.updates == []  # already at the day step
    asyncio.run(fan.check({"light": {"level": 0}}))
    assert mqtt.updates[-1]["state"]["desired"]["cFan"]["manu"]["lv"] == 44
    report_level(fan, 44)
    asyncio.run(fan.check({"light": {"level": 100}}))
    assert mqtt.updates[-1]["state"]["desired"]["cFan"]["manu"]["lv"] == 60


def test_configure_takes_the_fans_own_step_without_numbers() -> None:
    fan, _mqtt, _clock = stick()
    assert fan.configure({"auto": True})["fan_stick"]["day_step"] == 3
    assert fan.view()["night_step"] == 3
    for body in ({"auto": "yes"}, {"day_step": 11}):
        with pytest.raises(ValueError):
            fan.configure(body)


def test_the_rule_survives_a_restart() -> None:
    fan, _mqtt, clock = stick()
    fan.configure({"auto": True, "day_step": 3, "night_step": 1})
    again = FanStick(SimpleNamespace(), clock=clock)
    assert again.cfg == {"auto": True, "day_step": 3, "night_step": 1}


def test_a_change_in_the_app_pauses_the_rule_and_off_ends_it() -> None:
    fan, mqtt, clock = stick()
    fan.configure({"auto": True, "day_step": 3, "night_step": 3})
    clock.now += APP_CHANGE_AFTER_S + 1
    report_level(fan, 80)  # the owner in the app
    asyncio.run(fan.check({"light": {"level": 40}}))
    assert mqtt.updates == [] and fan.view()["paused_until"] == clock.now + PAUSE_S
    clock.now += PAUSE_S + 1
    asyncio.run(fan.check({"light": {"level": 40}}))
    assert mqtt.updates[-1]["state"]["desired"]["cFan"]["manu"]["lv"] == 60  # the pause is over
    report_level(fan, 60)
    clock.now += APP_CHANGE_AFTER_S + 1
    report_level(fan, 0)  # off in the app
    asyncio.run(fan.check({"light": {"level": 40}}))
    assert fan.view()["auto"] is False
    count = len(mqtt.updates)
    clock.now += PAUSE_S + 1
    asyncio.run(fan.check({"light": {"level": 40}}))
    assert len(mqtt.updates) == count  # off stays off


def test_off_on_the_page_ends_the_rule_and_another_step_pauses_it() -> None:
    fan, _mqtt, clock = stick()
    fan.configure({"auto": True})
    asyncio.run(fan.set_step(6))
    assert fan.view()["auto"] is True and fan.view()["paused_until"] == clock.now + PAUSE_S
    asyncio.run(fan.set_step(0))
    assert fan.view()["auto"] is False


def test_the_rule_leaves_the_apps_own_automation_alone() -> None:
    fan, mqtt, _clock = stick()
    fan.configure({"auto": True, "day_step": 6, "night_step": 6})
    fan.on_message("t", json.dumps({"state": {"reported": {"cFan": {"mode": 2}}}}).encode(), 1)
    asyncio.run(fan.check({"light": {"level": 40}}))
    assert mqtt.updates == []


def test_look_finds_the_stick_only_when_the_box_has_logged_in() -> None:
    clock = Clock()
    box = SimpleNamespace(_api=None, _tokens=None, _aws=None, _identity=None, _creds=None)
    fan = FanStick(box, clock=clock)
    asyncio.run(fan._look())
    assert fan.device is None

    calls = []

    async def get_devices(_tokens: Any) -> list[Any]:
        calls.append(1)
        return [SimpleNamespace(client_id="VSCTLY18-x", name="VGrow")]

    box._api, box._tokens, box._aws, box._identity, box._creds = SimpleNamespace(get_devices=get_devices), 1, 1, 1, 1
    asyncio.run(fan._look())
    asyncio.run(fan._look())
    assert fan.device is None and len(calls) == 1  # no stick in the account: asked again only after a while


def test_the_demo_fan_and_the_page() -> None:
    assert isinstance(start_fan_stick(DemoBox(), demo=True), DemoFanStick)

    async def main() -> None:
        app = make_app(DemoBox(), Alerts(), "k" * 32, fan_stick=DemoFanStick())
        async with TestClient(TestServer(app)) as client:
            await client.get("/?key=" + "k" * 32, allow_redirects=False)
            assert (await (await client.get("/api/state")).json())["fan_stick"]["step"] == 3
            assert (await client.post("/api/cmd", json={"action": "fan_stick_step", "step": 6})).status == 200
            assert (await client.post("/api/cmd", json={"action": "fan_stick_osc", "on": True})).status == 200
            answer = await client.post("/api/cmd", json={"action": "fan_stick_config", "auto": True})
            assert (await answer.json())["fan_stick"]["auto"] is True
            state = await (await client.get("/api/state")).json()
            assert state["fan_stick"]["step"] == 6 and state["fan_stick"]["osc"] is True
            assert (await client.post("/api/cmd", json={"action": "fan_stick_step", "step": 99})).status == 400

    asyncio.run(main())


def test_without_a_stick_the_page_says_no_connection() -> None:
    async def main() -> None:
        app = make_app(DemoBox(), Alerts(), "k" * 32)
        async with TestClient(TestServer(app)) as client:
            await client.get("/?key=" + "k" * 32, allow_redirects=False)
            assert "fan_stick" not in await (await client.get("/api/state")).json()
            assert (await client.post("/api/cmd", json={"action": "fan_stick_step", "step": 3})).status == 409

    asyncio.run(main())
