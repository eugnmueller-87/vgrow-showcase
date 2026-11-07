"""Hiding a camera (owner, 07.11.2026): its card goes, its offline alarm stays quiet, every other setting stays."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import alerts as alerts_mod
from vgrow import camera_prefs, i18n
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app

GONE = {"configured": True, "reachable": False, "offline_seconds": 60, "error": "macOS sieht die Kamera nicht."}
HOUR = 3600


class Cam:
    """Only what the state and /api/cmd ask of a camera."""
    configured = True

    def __init__(self, name: str, reachable: bool) -> None:
        self.name, self.reachable = name, reachable

    def status(self) -> dict[str, Any]:
        return {"configured": True, "reachable": self.reachable, "name": self.name}


class Pushes(Alerts):
    def __init__(self, rule: str) -> None:
        super().__init__()
        self.config = copy.deepcopy(self.config)
        self.config["rules"] = {rule: self.config["rules"][rule]}
        self.pushed: list[str] = []

    async def push(self, title: str, message: str, *, priority: int = 3, click: str | None = None) -> bool:
        self.pushed.append(message)
        return True


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: now[0])
    return now


def test_hiding_keeps_every_other_setting(isolated_state: Path) -> None:
    i18n.settings_path().parent.mkdir(parents=True)
    i18n.settings_path().write_text(json.dumps({"lang": "en", "packages": ["cannabis"], "hank_mode": "on"}),
                                    encoding="utf-8")
    assert camera_prefs.set_hidden("usb", True) == ["usb"]
    assert json.loads(i18n.settings_path().read_text(encoding="utf-8")) == {
        "lang": "en", "packages": ["cannabis"], "hank_mode": "on", "hidden_cameras": ["usb"]}
    assert camera_prefs.set_hidden("growcam", True) == ["growcam", "usb"]
    assert camera_prefs.set_hidden("usb", False) == ["growcam"] and camera_prefs.hidden() == {"growcam"}
    assert i18n.server_lang() == "en"


def test_a_missing_or_broken_file_hides_nothing(isolated_state: Path) -> None:
    assert camera_prefs.hidden() == set()
    i18n.settings_path().parent.mkdir(parents=True)
    i18n.settings_path().write_text("{broken", encoding="utf-8")
    assert camera_prefs.hidden() == set()
    i18n.settings_path().write_text(json.dumps({"hidden_cameras": "usb"}), encoding="utf-8")
    assert camera_prefs.hidden() == set()


@pytest.mark.parametrize(("rule", "key"), [("usb_camera_offline", "usb_camera"), ("camera_offline", "camera")])
def test_a_hidden_camera_ends_its_alarm_quietly_and_raises_none(isolated_state: Path, clock: list[float],
                                                                rule: str, key: str) -> None:
    alerts = Pushes(rule)
    asyncio.run(alerts.check({key: GONE}))
    clock[0] += 2 * HOUR
    asyncio.run(alerts.check({key: GONE}))
    assert len(alerts.pushed) == 1 and alerts.state[rule]["active"]
    asyncio.run(alerts.check({key: {**GONE, "hidden": True}}))
    assert len(alerts.pushed) == 1, "no 'it answers again' for a camera that is only hidden"
    assert alerts.state[rule] == {"since": None, "active": False, "last_push": None, "source": None}
    clock[0] += 48 * HOUR
    asyncio.run(alerts.check({key: {**GONE, "hidden": True}}))
    assert len(alerts.pushed) == 1
    asyncio.run(alerts.check({key: GONE}))  # shown again and still gone: the hold time starts over
    assert len(alerts.pushed) == 1 and alerts.state[rule]["since"] == clock[0]


def test_the_page_hides_a_camera_and_brings_it_back(isolated_state: Path) -> None:
    async def go() -> list[Any]:
        app = make_app(DemoBox(), Alerts(), "k", Cam("GrowCam", True), extra_cameras={"usb": Cam("C920", False)})
        client = TestClient(TestServer(app))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})
        try:
            seen = []
            for body in ({"action": "camera_hide", "camera": "usb"},
                         {"action": "camera_hide", "camera": "nope"},
                         {"action": "camera_hide", "camera": ["usb"]}):
                r = await client.post("/api/cmd", json=body)
                seen.append((r.status, await r.json()))
            seen.append(await (await client.get("/api/state")).json())
            r = await client.post("/api/cmd", json={"action": "camera_hide", "camera": "usb", "hidden": False})
            seen.append((r.status, await r.json()))
            seen.append(await (await client.get("/api/state")).json())
            return seen
        finally:
            await client.close()

    hide, unknown, wrong, state, show, again = asyncio.run(go())
    assert hide == (200, {"ok": True, "hidden": ["usb"]})
    assert unknown[0] == 404 and wrong[0] == 404
    assert {c["id"]: c["hidden"] for c in state["cameras"]} == {"growcam": False, "usb": True}
    assert show == (200, {"ok": True, "hidden": []})
    assert {c["id"]: c["hidden"] for c in again["cameras"]} == {"growcam": False, "usb": False}
