"""The owner's 55% off target: persistent, stage-bound, with a restart floor below it and the same guards."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_vpd_rule import EARLY, PROFILE, rule, snap
from vgrow import automation as am
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation, humidity_floor
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app


def test_55_target_stops_at_equality_and_restarts_below_52_after_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    box, hum, clock = rule(monkeypatch)
    original = copy.deepcopy(PROFILE)
    hum.set_target(55)
    assert hum.view(box.snapshot())["band"] == [45, 55]
    assert hum.view(box.snapshot())["card_band"] == [45, 60]
    assert hum.view(box.snapshot())["floor"]["floor"] == 52
    assert box.sent == []  # saving the setting does not send a hardware command
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_on"
    clock[0] += 90
    box.climate["probe_humidity"] = 54.99
    assert asyncio.run(hum.check(box.snapshot())) is None
    clock[0] += 90
    box.climate["probe_humidity"] = 55
    assert asyncio.run(hum.check(box.snapshot())) == "humidifier_off"
    assert box.ports["usb2"]["on"] is False
    off_at = clock[0]
    # A later process uses the persisted target and cooldown.
    again = HumidifierAutomation(box, None, hum.plant_log, budget=hum.budget)
    assert again.band() == (45, 55)
    assert again.view()["floor"]["floor"] == 52
    box.climate["probe_humidity"] = 51.9
    clock[0] = off_at + am.MIN_SWITCH_SECONDS - 1
    assert asyncio.run(again.check(box.snapshot())) is None
    clock[0] += 1
    box.climate["probe_humidity"] = 52
    assert asyncio.run(again.check(box.snapshot())) is None
    box.climate["probe_humidity"] = 51.9
    assert asyncio.run(again.check(box.snapshot())) == "humidifier_on"
    assert box.sent == [("port.usb2", True), ("port.usb2", False), ("port.usb2", True)]
    assert PROFILE == original  # the researched stage card remains intact


@pytest.mark.parametrize("bad", [True, False, "55", [], {}, float("nan"), float("inf"), 47.9, 60.1])
def test_invalid_target_is_rejected_without_changing_state(monkeypatch: pytest.MonkeyPatch, bad: object) -> None:
    box, hum, _ = rule(monkeypatch)
    hum.set_target(55)
    before = copy.deepcopy(hum.state)
    with pytest.raises(ValueError):
        hum.set_target(bad)
    assert hum.state == before and box.sent == []


def test_target_preserves_manual_pause_and_enable_state(monkeypatch: pytest.MonkeyPatch) -> None:
    box, hum, _ = rule(monkeypatch)
    hum.manual_switch()
    paused = hum.state["paused_until"]
    hum.set_target(55)
    assert hum.state["paused_until"] == paused
    assert hum.state["enabled"] is True
    box.ports["usb2"]["on"] = True
    box.climate["probe_humidity"] = 55
    assert asyncio.run(hum.check(box.snapshot())) is None
    hum.set_enabled(False)
    hum.set_target(54)
    assert hum.state["enabled"] is False and box.sent == []


@pytest.mark.parametrize("change", ["stage", "since", "confirmation"])
def test_another_stage_period_does_not_reuse_the_target(monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    _, hum, _ = rule(monkeypatch)
    hum.set_target(55)
    current = copy.deepcopy(hum.stage())
    current[{"stage": "key", "since": "since", "confirmation": "set_by"}[change]] = {
        "stage": "late_flower", "since": "2026-12-01", "confirmation": "estimate",
    }[change]
    monkeypatch.setattr(hum, "blocked", lambda: (None if change != "confirmation" else "stage", current))
    assert hum.band() == (45, 60)


def test_no_new_target_without_a_confirmed_stage_but_reset_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _, hum, _ = rule(monkeypatch, owner_stage=False)
    with pytest.raises(ValueError):
        hum.set_target(55)
    hum.set_target(None)
    assert hum.state["humidity_target"] is None


def test_reset_restores_the_card_and_its_vpd_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    box, hum, _ = rule(monkeypatch)
    hum.set_target(55)
    hum.set_target(None)
    assert hum.band() == (45, 60)
    assert hum.view()["floor"] == humidity_floor(EARLY, box.snapshot())
    lines = [json.loads(line) for line in hum.log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[-1] == {"time": lines[-1]["time"], "action": "humidity_target", "target": None}


@pytest.mark.parametrize("temp,light", [(27, 75), (25.3, 75), (21, 75), (25.3, 0)])
def test_lower_target_keeps_the_floor_below_it_at_different_temperatures_and_at_night(temp: float, light: int) -> None:
    floor = humidity_floor(EARLY, snap(temp, light=light), off_at=55)
    assert 45 <= floor["floor"] <= 52


def test_target_api_is_authenticated_validated_and_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    box, hum, _ = rule(monkeypatch)

    async def run() -> None:
        app = make_app(DemoBox(), Alerts(), "k", automation=hum, plant_log=hum.plant_log)
        async with TestClient(TestServer(app)) as client:
            assert (await client.post("/api/cmd", json={"action": "humidifier_target", "off_at": 55})).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            assert (await client.post("/api/cmd", json={"action": "humidifier_target", "off_at": 55})).status == 200
            data = await (await client.get("/api/state")).json()
            assert data["automation"]["band"] == [45, 55]
            assert data["automation"]["floor"]["floor"] <= 52
            for body in ({"action": "humidifier_target", "off_at": True}, {"action": "humidifier_target"}):
                assert (await client.post("/api/cmd", json=body)).status == 400
                assert hum.band() == (45, 55)
            assert (await client.post("/api/cmd", json={"action": "humidifier_target", "off_at": None})).status == 200
            assert hum.band() == (45, 60) and box.sent == []

    asyncio.run(run())
