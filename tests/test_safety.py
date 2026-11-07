"""The safety fixes of 22.10.2026, one block each: camera alarm, cloud failure, humidifier fail-safe,
always-on lock in the actuator, unbiased prompt, probe temperature, neutral texts."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import alerts as alerts_mod
from vgrow import automation as am
from vgrow import camera as camera_mod
from vgrow import config, i18n
from vgrow.alerts import Alerts, breached
from vgrow.box import ApiClient, Box, CommandRefused, climate_view, scale_row
from vgrow.camera import Camera
from vgrow.demo import DemoBox
from vgrow.i18n import Text, TextError
from vgrow.plant import PlantLog
from vgrow.server import COOKIE, alert_snapshot, make_app, poll_handler
from vgrow.vivosun.models import DeviceInfo

REPO = Path(__file__).resolve().parent.parent
VGROW = DeviceInfo("dev1", "vivosun-VSCTLY18-acct-dev1", "vivosun/VS_COMMON/VSCTLY18/acct/dev1", "VGrow", True, 1, "controller")
MANUAL = {
    "light": {"mode": 0, "inPlan": 0, "lv": 75, "manu": {"lv": 75, "spec": 50}},
    "usb1": {"mode": 0, "inPlan": 0, "lv": 100, "manu": {"lv": 100}},
    "usb2": {"mode": 0, "inPlan": 0, "lv": 100, "manu": {"lv": 100}},
    "connected": True,
}


class FakeMqtt:
    is_connected = True

    def __init__(self) -> None:
        self.sent: list[dict] = []  # type: ignore[type-arg]

    async def publish_shadow_update(self, payload: str, qos: int = 0) -> None:
        self.sent.append(json.loads(payload))

    async def publish(self, topic: str, payload: bytes, qos: int = 0) -> None: ...


def make_box() -> tuple[Box, FakeMqtt]:
    box = Box("x@example.test", "unused")
    box.device = VGROW
    box._mqtt = FakeMqtt()  # type: ignore[assignment]
    box.reported = json.loads(json.dumps(MANUAL))
    return box, box._mqtt  # type: ignore[return-value]


class RecordingAlerts(Alerts):
    def __init__(self, rules: dict) -> None:  # type: ignore[type-arg]
        self._session = None
        self.config = {"remind_hours": 6, "rules": rules}
        self._state_path = config.STATE_DIR / "alerts-state.json"
        self.state = {}
        self.target = ("https://ntfy.example", "t")
        self.pushed: list[str] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.pushed.append(f"{title}|{message}")
        return True


def online_camera(tmp_path: Path, reachable: bool | None) -> Camera:
    cam = Camera(tmp_path)
    cam.ffmpeg, cam.ip, cam._user, cam._password = "ffmpeg", "10.0.0.9", "u", "p"
    cam.reachable = reachable
    cam.offline_since = time.time() - 900 if reachable is False else None
    return cam


# ---------- a. the camera alarm sees the camera ----------

def test_unreachable_camera_breaches_the_camera_rule(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rule = {"label": "Kamera offline", "metric": "camera", "for_minutes": 0}
    assert "camera" not in DemoBox().snapshot()  # the bug: the plain snapshot has no camera
    assert breached(rule, alert_snapshot(DemoBox(), online_camera(tmp_path, False))) is True
    assert breached(rule, alert_snapshot(DemoBox(), online_camera(tmp_path, True))) is False
    assert breached(rule, alert_snapshot(DemoBox(), None)) is None


def test_poll_handler_reads_the_camera_that_arrives_after_start(tmp_path) -> None:  # type: ignore[no-untyped-def]
    alerts = RecordingAlerts({"camera_offline": {"label": "Kamera offline", "metric": "camera", "for_minutes": 0}})
    parts: dict = {"camera": None}  # type: ignore[type-arg]
    on_poll = poll_handler(alerts, None, parts)
    box = DemoBox()
    asyncio.run(on_poll(box))
    assert alerts.pushed == []  # no camera yet: never alert on a guess
    parts["camera"] = online_camera(tmp_path, False)  # serve() sets it once the camera exists
    asyncio.run(on_poll(box))
    assert len(alerts.pushed) == 1 and alerts.pushed[0].startswith("Kamera offline|Kamera offline. Kein Bild, seit 15 min")


def test_a_failing_alert_does_not_stop_the_automation() -> None:
    calls: list[str] = []

    class Broken(RecordingAlerts):
        async def check(self, snapshot):  # type: ignore[no-untyped-def]
            raise OSError("disk full")

    class Auto:
        async def check(self, snap):  # type: ignore[no-untyped-def]
            calls.append("automation")

    asyncio.run(poll_handler(Broken({}), Auto(), {"camera": None})(DemoBox()))  # type: ignore[arg-type]
    assert calls == ["automation"]


# ---------- b. a failed cloud poll is not silent ----------

class FailingApi:
    async def get_point_rows(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("cloud down")


class Api:
    async def get_point_rows(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [{"time": int(time.time()), "bTemp": 2400, "bHumi": 5000, "bVpd": 120}]


def test_failed_poll_still_reaches_the_listeners_and_the_offline_rule() -> None:
    box, _ = make_box()
    box._api, box._tokens = FailingApi(), object()  # type: ignore[assignment]
    box.sensors, box.sensors_at = {"bTemp": 24.0}, time.time() - 400  # last good reading 400 s ago
    alerts = RecordingAlerts({"offline": {"label": "Box offline", "metric": "offline", "for_minutes": 0}})
    seen: list[dict] = []  # type: ignore[type-arg]

    async def record(b):  # type: ignore[no-untyped-def]
        seen.append(b.snapshot())

    box.listeners += [record, poll_handler(alerts, None, {"camera": None})]
    asyncio.run(box.poll_once())  # used to swallow the error and call nobody
    assert len(seen) == 1 and seen[0]["sensors_age"] >= 400 and seen[0]["error"] == "Messwerte: cloud down"
    assert alerts.pushed == ["Box offline|Box offline. Letzter Messwert vor 6 min."]
    box._api = Api()  # type: ignore[assignment]
    asyncio.run(box.poll_once())
    assert len(seen) == 2 and seen[1]["error"] is None and seen[1]["sensors_age"] <= 1  # the row's own second


def test_demo_box_uses_the_same_listener_method() -> None:
    assert DemoBox.notify_listeners is Box.notify_listeners
    box, calls = DemoBox(), []

    async def listener(b):  # type: ignore[no-untyped-def]
        calls.append(b)

    async def run() -> None:
        box.listeners.append(listener)
        await box.start()  # evaluates once right away, then once per poll
        await box.stop()

    asyncio.run(run())
    assert calls == [box]


# ---------- c. the humidifier needs fresh data, and has a fail-safe ----------

PROFILE = {"plant": {"start_date": "2026-08-31"}, "stages": [
    {"key": "bloom", "label": "Blüte", "from_day": 0, "to_day": 999, "targets": [
        {"metric": "probe_humidity", "label": "Feuchte", "unit": "%", "min": 40, "max": 55}]}]}


class Pushes:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.pushed.append((title, message))
        return True


def automation(monkeypatch, alerts=None):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(am, "load_profile", lambda: PROFILE)
    box = DemoBox()
    log = PlantLog()
    log.set_stage("bloom")  # the owner's stage: without one the automation never switches on (test_stage_rules)
    auto = am.HumidifierAutomation(box, alerts, log)
    # The owner named the humidifier on USB 2 on the page: only then does the rule switch it on (review 2, M3).
    auto.guard.assign("usb2", "humidifier", box.snapshot())
    auto.set_enabled(True)
    return box, auto


def snap(box: DemoBox, rh: float, age: float | None) -> dict:  # type: ignore[type-arg]
    s = box.snapshot()
    s["climate"]["probe_humidity"] = rh
    s["sensors_age"] = age
    return s


def test_stale_readings_never_switch_the_humidifier_on(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto = automation(monkeypatch)
    box.ports["usb2"] = False
    for age in (None, 601, 5000):
        assert asyncio.run(auto.check(snap(box, 30, age))) is None, age  # far below the band, yet no switch
    assert box.ports["usb2"] is False
    assert asyncio.run(auto.check(snap(box, 30, 40))) == "humidifier_on"  # fresh data: as before


def test_failsafe_switches_off_once_and_pushes_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pushes = Pushes()
    box, auto = automation(monkeypatch, pushes)
    box.ports["usb2"] = True
    auto.manual_switch()  # even a manual pause does not hold the fail-safe back
    assert asyncio.run(auto.check(snap(box, 50, 899))) is None and box.ports["usb2"] is True  # stale, not yet 15 min
    assert asyncio.run(auto.check(snap(box, 50, 901))) == "failsafe_off" and box.ports["usb2"] is False
    assert pushes.pushed == [("Luftbefeuchter aus: keine frischen Messwerte",
                              "Seit 15 min kein frischer Messwert. Das Cockpit hat den Luftbefeuchter (USB 2) "
                              "ausgeschaltet, die Box hat es bestätigt. Aus ist der sichere Zustand; die Automatik "
                              "schaltet wieder, sobald frische Werte kommen.")]
    box.ports["usb2"] = True  # switched back on by hand: once means once
    assert asyncio.run(auto.check(snap(box, 50, 3000))) is None and box.ports["usb2"] is True
    assert len(pushes.pushed) == 1
    actions = [json.loads(line)["action"] for line in auto.log_path.read_text(encoding="utf-8").splitlines()]
    assert actions.count("failsafe_off") == 1
    assert auto.view(snap(box, 50, 3000))["last_action"]["action"] == "humidifier_off"
    asyncio.run(auto.check(snap(box, 50, 40)))  # fresh again: the next stale spell may act once more
    assert auto.state["failsafe_done"] is False
    assert asyncio.run(auto.check(snap(box, 50, None))) == "failsafe_off"  # no reading at all counts as stale
    assert "Seit dem Start kein Messwert" in pushes.pushed[-1][1]


def test_failsafe_tries_again_when_the_send_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto = automation(monkeypatch)
    box.ports["usb2"] = True
    real = box.set_port

    async def offline(port: str, on: bool) -> None:
        raise CommandRefused("No live connection to the box right now")

    box.set_port = offline  # type: ignore[method-assign]
    assert asyncio.run(auto.check(snap(box, 50, 1200))) == "failsafe_unconfirmed" and auto.state["failsafe_done"] is False
    box.set_port = real  # type: ignore[method-assign]
    assert asyncio.run(auto.check(snap(box, 50, 1300))) == "failsafe_off" and box.ports["usb2"] is False


def test_view_reports_stale_and_the_newest_entries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto = automation(monkeypatch)
    start = time.time() - 3600
    for i in range(7):
        auto._log({"action": "humidifier_on", "value": 40 + i}, at=start + i)
    auto._log({"action": "something_new"}, at=start + 10)  # unknown actions stay out of the page's list
    view = auto.view(snap(box, 50, 40))
    assert view["stale"] is False and len(view["recent"]) == 5
    assert [e["value"] for e in view["recent"]] == [46, 45, 44, 43, 42]  # newest first
    assert all(set(e) == {"time", "action", "value"} and isinstance(e["time"], float) for e in view["recent"])
    assert auto.view(snap(box, 50, 601))["stale"] is True and auto.view(snap(box, 50, None))["stale"] is True
    again = am.HumidifierAutomation(box, None, PlantLog())  # after a restart: read back from the log file
    recent = again.view(snap(box, 50, 40))["recent"]
    assert [e["value"] for e in recent] == [46, 45, 44, 43, 42] and abs(recent[0]["time"] - (start + 6)) < 1.5
    assert again.view(snap(box, 50, 40))["recent"][-1]["action"] == "humidifier_on"


def test_old_log_lines_with_iso_times_become_epoch_seconds(monkeypatch, isolated_state) -> None:  # type: ignore[no-untyped-def]
    isolated_state.mkdir(parents=True, exist_ok=True)
    (isolated_state / "automation-log.jsonl").write_text(
        '{"time": "2026-10-22T11:45:00", "action": "automation_on"}\n'
        '{"time": "2026-10-22T12:01:13", "action": "humidifier_off", "value": 82.7, "band": [55, 70]}\n'
        'not json\n', encoding="utf-8")
    box, auto = automation(monkeypatch)  # set_enabled adds a third entry
    recent = auto.view(snap(box, 50, 40))["recent"]
    assert [e["action"] for e in recent] == ["automation_on", "humidifier_off", "automation_on"]
    assert recent[1]["value"] == 82.7 and recent[2]["value"] is None
    from datetime import datetime
    assert recent[1]["time"] == datetime(2026, 10, 22, 12, 1, 13).timestamp()


# ---------- d. the always-on lock sits in the actuator ----------

def test_always_on_port_is_refused_by_the_box_itself(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ports = tmp_path / "ports.json"
    ports.write_text(json.dumps({"usb1": {"label": "Kamera", "label_en": "Camera", "always_on": True}}), encoding="utf-8")
    monkeypatch.setattr(config, "PORTS_FILE", ports)
    box, mqtt = make_box()
    with pytest.raises(CommandRefused, match="Kamera bleibt immer an"):
        asyncio.run(box.set_port("usb1", False))
    assert mqtt.sent == []
    asyncio.run(box.set_port("usb1", True))  # on stays allowed
    asyncio.run(box.set_port("usb2", False))  # and other ports are untouched
    assert len(mqtt.sent) == 2
    demo = DemoBox()
    demo.ports["usb1"] = True  # on, as the lock holds it (the demo's own USB 1, the pump, starts off)
    with pytest.raises(CommandRefused, match="Kamera bleibt immer an"):
        asyncio.run(demo.set_port("usb1", False))
    assert demo.ports["usb1"] is True


def test_real_ports_file_names_the_pump_on_usb1_and_locks_no_port() -> None:
    """23.10.2026: the drip pump hangs on USB 1, where the camera drew its power; the camera needs its own power supply
    now. USB 1 still locked always on ran the pump for about half an hour and refused the off (409). The lock itself
    stays and is tested with tests/fixtures/ports-locked.json; the owner's real file names the pump and locks nothing."""
    rules = config.port_rules()
    assert rules is not None
    assert rules["usb1"]["device"] == "pump" and rules["usb1"]["label"] == "Tropfbewässerung (Pumpe)"
    assert rules["usb1"]["label_en"] == "Drip irrigation (pump)"
    assert rules["usb2"]["device"] == "humidifier" and rules["usb2"]["label"] == "Luftbefeuchter"
    assert not any(rule.get("always_on") for rule in rules.values())
    box, mqtt = make_box()
    asyncio.run(box.set_port("usb1", False))  # the pump goes off by hand: the box no longer refuses it
    assert len(mqtt.sent) == 1
    # No rule switches the pump: the humidifier rule drives USB 2 only, the port guard knows USB 1 as the pump (a veto
    # whatever the page says), and the autopilot has no port command at all.
    from vgrow.portguard import PortGuard, configured_device
    assert am.PORT == "usb2" and configured_device("usb1") == "pump"
    snap = {"ports": {"usb1": {"on": False, "accessory": None}, "usb2": {"on": False, "accessory": None}}}
    assert PortGuard().may_switch_humidifier("usb1", snap) == "not_humidifier"
    assert "set_port" not in (REPO / "vgrow" / "autopilot.py").read_text(encoding="utf-8")


# ---------- e. the prompt does not pass an estimate off as the operator's word ----------

def test_prompt_names_only_a_manual_stage_as_set_by_the_operator() -> None:
    from vgrow.vision import context_text
    plant = {"configured": True, "plant": {"strain": "Test Auto"}, "day": 42,
             "stage": {"key": "early_flower", "label": "Frühe Blüte (Stretch)", "set_by": "estimate"}}
    for set_by in ("estimate", "days"):
        plant["stage"]["set_by"] = set_by  # type: ignore[index]
        text = context_text({}, plant, None, "de")
        assert "Geschätzte Phase, vom Betreiber nicht bestätigt: Frühe Blüte (Stretch)." in text
        assert "Vom Betreiber gesetzte" not in text
        assert "Estimated stage, not confirmed by the operator" in context_text({}, plant, None, "en")
    plant["stage"]["set_by"] = "manual"  # type: ignore[index]
    assert "Vom Betreiber gesetzte Phase: Frühe Blüte (Stretch)." in context_text({}, plant, None, "de")
    assert "Stage set by the operator: Frühe Blüte (Stretch)." in context_text({}, plant, None, "en")


def test_previous_issues_only_in_the_same_language() -> None:
    from vgrow.vision import context_text
    old = {"date": "2026-10-21T12:05", "result": {"stage_guess": "veg", "issues": [{"issue": "Gelbe Blätter"}]}}
    assert "Gelbe Blätter" in context_text({}, None, old, "de")  # no lang: written before 22.10.2026, German
    assert "Gelbe Blätter" not in context_text({}, None, old, "en")
    assert "Yellow leaves" in context_text({}, None, {**old, "lang": "en", "result": {
        "stage_guess": "veg", "issues": [{"issue": "Yellow leaves"}]}}, "en")


def test_reading_stores_its_language_and_asks_in_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from vgrow import vision as v
    calls: list = []  # type: ignore[type-arg]
    reading = {"stage_guess": "veg", "confidence": "low", "stage_reason": "x", "visible_signs": [], "issues": [],
               "next_steps": [], "not_assessable": []}

    async def chat(body):  # type: ignore[no-untyped-def]
        calls.append(body)
        return {"model": body["model"], "message": {"content": json.dumps(reading)}}

    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    vis = v.Vision(camera=None)
    vis._chat = chat  # type: ignore[method-assign]
    record = asyncio.run(vis.analyze(photo, {}, None, "en"))
    assert record["lang"] == "en" and calls[0]["messages"][0]["content"].endswith("Answer in English, briefly.")
    assert "Climate now" in calls[0]["messages"][1]["content"]
    assert vis.view()["latest"]["lang"] == "en" and "context" not in vis.view()["latest"]


# ---------- f. temperature alarms read the probe at leaf height ----------

def test_temperature_rules_read_the_probe_and_fall_back_to_the_box() -> None:
    rules = config.alert_rules()["rules"]
    for name in ("temp_low", "temp_high"):
        assert rules[name]["metric"] == "probe_temp" and rules[name]["fallback"] == "temp", name
    low, high = rules["temp_low"], rules["temp_high"]
    assert breached(low, {"climate": {"probe_temp": 16.0, "temp": 12.0}}) is False  # the probe decides
    assert breached(low, {"climate": {"probe_temp": 14.0, "temp": 20.0}}) is True
    assert breached(low, {"climate": {"probe_temp": None, "temp": 12.0}}) is True  # no probe value: the box
    assert breached(high, {"climate": {"probe_temp": 25.0, "temp": 33.0}}) is False
    assert breached(low, {"climate": {}}) is None
    reading = alerts_mod.Alerts._reading
    assert reading(low, {"climate": {"probe_temp": None, "temp": 12.04}}, "de") == (
        "Jetzt 12,0 °C am eingebauten Sensor, der Fühler meldet keinen Wert.")
    assert reading(low, {"climate": {"probe_temp": 14.2, "temp": 20.0}}, "en") == "Now 14.2 °C."


def test_probe_alarm_message_end_to_end() -> None:
    alerts = RecordingAlerts({"temp_low": {**config.alert_rules()["rules"]["temp_low"], "for_minutes": 0}})
    asyncio.run(alerts.check({"climate": {"probe_temp": 13.55, "temp": 19.0}}))
    assert alerts.pushed == ["Zu kalt (Fühler)|Zu kalt (Fühler) (Grenze 15 °C). Jetzt 13,6 °C."]


# ---------- g. neutral, correct texts ----------

def test_content_files_have_no_personal_names_or_vault_references() -> None:
    for path in [REPO / "alerts.json", REPO / "plant.json", REPO / "ports.json", *(REPO / "kartei").rglob("*.json")]:
        text = path.read_text(encoding="utf-8")
        for word in ("Eugen", "Lukas", "MOC", "Gaertnern", "2000451117"):
            assert word not in text, (path.name, word)


def test_camera_power_and_ports_are_named_alike_everywhere() -> None:
    # Since 23.10.2026 the camera needs its own power supply and the pump hangs on USB 1: no text sends the owner to
    # USB 1 for a camera without power any more.
    source = config.alert_rules()["rules"]["camera_offline"]["source"]
    assert "braucht sie ein eigenes USB-Netzteil" in source and "Kamera-Strom an USB 1" not in source
    assert "Luftbefeuchter an USB 2" in source
    for lang in ("de", "en"):
        assert "USB 1" not in i18n.t("alert.camera", lang, minutes=12)
    web_texts = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
    assert "Sie hängt an USB 1" not in web_texts and "plugged into USB 1" not in web_texts
    assert "humidifier\n        on USB 2" in (Camera.watch.__doc__ or "") or "humidifier on USB 2" in " ".join(
        (Camera.watch.__doc__ or "").split())
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    # The detailed German operating notes moved out of the English product overview.
    operations = (REPO / "docs" / "operations.md").read_text(encoding="utf-8")
    assert "docs/operations.md" in readme
    assert "stoppt nach 2 min" not in readme + operations and "höchstens 12 h" in operations
    from vgrow import vision
    assert "163 s and 238 s per reading (gemma3:12b)" in " ".join((vision.__doc__ or "").split())


# ---------- second review of 22.10.2026: the clock, the probe, the confirmation, the edges ----------

def rows_api(rows: list[dict]) -> object:  # type: ignore[type-arg]
    class RowsApi:
        async def get_point_rows(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return rows
    return RowsApi()


def test_sensor_age_is_the_readings_own_not_the_polls() -> None:
    box, _ = make_box()
    box._tokens = object()  # type: ignore[assignment]
    now = int(time.time())
    box._api = rows_api([{"time": now - 840, "bTemp": 2400, "bHumi": 5000, "pHumi": 6000}])  # type: ignore[assignment]
    asyncio.run(box.poll_sensors())
    assert 838 <= box.snapshot()["sensors_age"] <= 842  # the upload stalled 14 min ago; the poll said 0
    # A newer row of sentinels only (sensor and probe gone) is no reading: it must not look fresh.
    box._api = rows_api([{"time": now - 840, "bTemp": 2400, "bHumi": 5000},  # type: ignore[assignment]
                         {"time": now - 60, "bTemp": -6666, "bHumi": -6666, "pTemp": -6666, "pHumi": -6666}])
    asyncio.run(box.poll_sensors())
    assert box.snapshot()["sensors_age"] >= 838
    # Neither does a row without a time, nor one an hour in the future.
    box._api = rows_api([{"bTemp": 2500, "bHumi": 5000}, {"time": now + 3600, "bTemp": 2500}])  # type: ignore[assignment]
    asyncio.run(box.poll_sensors())
    assert box.snapshot()["sensors_age"] >= 838 and box.snapshot()["climate"]["temp"] == 24.0
    box._api = rows_api([{"time": now - 30, "bTemp": 2500, "bHumi": 5100}])  # type: ignore[assignment]
    asyncio.run(box.poll_sensors())
    assert box.snapshot()["sensors_age"] <= 32 and box.snapshot()["climate"]["temp"] == 25.0


def test_a_failing_first_poll_does_not_stop_the_start(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box = Box("x@example.test", "unused")

    async def login() -> None:
        box.device, box._tokens = VGROW, object()  # type: ignore[assignment]

    async def connect() -> None: ...

    async def failing(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("point log down")

    box._login, box._connect = login, connect  # type: ignore[method-assign]
    monkeypatch.setattr(ApiClient, "get_point_rows", failing)
    seen: list[dict] = []  # type: ignore[type-arg]

    async def listener(b):  # type: ignore[no-untyped-def]
        seen.append(b.snapshot())

    box.listeners.append(listener)

    async def run() -> None:
        await box.start()  # raised before, so serve() never reached the web server
        try:
            assert [t.get_name() for t in box._tasks] == ["vgrow_supervise", "vgrow_poll", "vgrow_shadow_refresh"]
        finally:
            await box.stop()

    asyncio.run(run())
    assert len(seen) == 1 and seen[0]["sensors_age"] is None and seen[0]["error"] == "Messwerte: point log down"


def test_the_probe_stays_the_probe_when_the_builtin_sensor_is_missing() -> None:
    view = climate_view(scale_row({"bTemp": -6666, "bHumi": -6666, "bVpd": -6666,
                                   "pTemp": 3150, "pHumi": 8000, "pVpd": 90}))
    assert (view["probe_temp"], view["probe_humidity"]) == (31.5, 80.0)
    assert view["temp"] is None and view["humidity"] is None  # the probe does not pass for the box
    rules = config.alert_rules()["rules"]
    snapshot = {"climate": view}
    assert breached(rules["humidity_high"], snapshot) is True and breached(rules["temp_high"], snapshot) is True
    assert alerts_mod.Alerts._reading(rules["temp_high"], snapshot, "de") == "Jetzt 31,5 °C."
    # And without a probe value the humidity alarm reads the built-in sensor, like the temperature rules.
    assert rules["humidity_high"]["fallback"] == "humidity"
    assert breached(rules["humidity_high"], {"climate": {"probe_humidity": None, "humidity": 75.0}}) is True


def test_no_probe_value_switches_the_humidifier_off_after_15_minutes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pushes = Pushes()
    box, auto = automation(monkeypatch, pushes)
    box.ports["usb2"] = True
    unplugged = snap(box, 50, 40)
    unplugged["climate"]["probe_humidity"] = None  # fresh rows keep coming, with -6666 for the probe
    assert asyncio.run(auto.check(unplugged)) is None and auto.state["missing_since"] is not None
    view = auto.view(unplugged)
    assert view["missing"] is True and view["stale"] is False  # the page no longer says "active"
    auto.state["missing_since"] -= am.FAILSAFE_SECONDS + 1
    assert asyncio.run(auto.check(unplugged)) == "failsafe_off" and box.ports["usb2"] is False
    assert pushes.pushed == [("Luftbefeuchter aus: Fühler meldet keinen Wert",
                              "Der Fühler meldet seit 15 min keine Luftfeuchte. Das Cockpit hat den Luftbefeuchter "
                              "(USB 2) ausgeschaltet, die Box hat es bestätigt. Aus ist der sichere Zustand; die "
                              "Automatik schaltet wieder, sobald frische Werte kommen.")]
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(unplugged)) is None and len(pushes.pushed) == 1  # once per spell
    assert asyncio.run(auto.check(snap(box, 50, 40))) is None  # the probe is back: the spell is over
    assert auto.state["missing_since"] is None and auto.state["failsafe_done"] is False


class SilentBox(DemoBox):
    """Takes every command and never reports it: the box is offline while the cloud is up."""

    sent = 0

    async def set_port(self, port: str, on: bool) -> None:
        self.sent += 1


def test_failsafe_counts_only_an_off_the_box_confirms(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(am, "CONFIRM_SECONDS", 0)
    monkeypatch.setattr(am, "load_profile", lambda: PROFILE)
    clock = [time.time()]
    monkeypatch.setattr(am.time, "time", lambda: clock[0])
    pushes, box = Pushes(), SilentBox()
    auto = am.HumidifierAutomation(box, pushes, PlantLog())
    auto.set_enabled(True)
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50, 1000))) == "failsafe_unconfirmed"
    assert auto.state["failsafe_done"] is False and auto.state["last_action"] is None  # nothing claims "off"
    assert pushes.pushed == [("Luftbefeuchter: Ausschalten nicht bestätigt",
                              "Seit 16 min kein frischer Messwert. Das Ausschalten des Luftbefeuchters (USB 2) ist "
                              "nicht bestätigt: die Box antwortet nicht. Bitte den Befeuchter selbst ausschalten; das "
                              "Cockpit versucht es in wachsenden Abständen erneut (90 s, 3, 6, 12, dann alle 15 min).")]
    clock[0] += 60  # the next poll comes before the first wait (90 s) is over: nothing goes out
    assert asyncio.run(auto.check(snap(box, 50, 1060))) == "failsafe_unconfirmed" and box.sent == 1
    clock[0] += 30
    assert asyncio.run(auto.check(snap(box, 50, 1090))) == "failsafe_unconfirmed" and box.sent == 2  # sent again
    assert auto.state["retry_at"] == clock[0] + 180  # then 3 min, 6, 12, at most 15 (critique M6: a backoff)
    assert len(pushes.pushed) == 1  # and said once
    assert "failsafe_off" not in [e["action"] for e in auto.view(snap(box, 50, 1090))["recent"]]
    box.ports["usb2"] = False  # the box is back and has taken the off
    assert asyncio.run(auto.check(snap(box, 50, 1180))) == "failsafe_off" and auto.state["failsafe_done"] is True
    assert pushes.pushed[-1] == ("Luftbefeuchter jetzt aus", "Die Box meldet den Luftbefeuchter (USB 2) jetzt als aus.")
    assert auto.view(snap(box, 50, 1180))["recent"][0]["action"] == "failsafe_off"


def test_failsafe_on_the_real_box_waits_for_its_report(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(am, "CONFIRM_SECONDS", 0)
    monkeypatch.setattr(am, "load_profile", lambda: PROFILE)
    box, mqtt = make_box()
    auto = am.HumidifierAutomation(box, Pushes(), PlantLog())
    auto.set_enabled(True)
    assert asyncio.run(auto.check(snap(box, 50, 1000))) == "failsafe_unconfirmed"  # type: ignore[arg-type]
    assert mqtt.sent[-1]["state"]["desired"]["usb2"]["manu"]["lv"] == 0  # the off went out, unconfirmed
    box._on_message("$aws/things/x/shadow/update/documents", json.dumps(
        {"current": {"state": {"reported": {"usb2": {"mode": 0, "inPlan": 0, "lv": 0, "manu": {"lv": 0}}}}}}).encode(), 1)
    assert asyncio.run(auto.check(snap(box, 50, 1090))) == "failsafe_off"  # type: ignore[arg-type]


def test_a_manual_switch_in_a_stale_spell_means_the_owner_took_over(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pushes = Pushes()
    box, auto = automation(monkeypatch, pushes)
    box.ports["usb2"] = True  # switched on by hand, readings 1000 s old, the fail-safe has not acted yet
    real = box.snapshot
    box.snapshot = lambda: {**real(), "sensors_age": 1000}  # type: ignore[method-assign]
    auto.manual_switch()
    assert auto.state["failsafe_done"] is True and auto.state["paused_until"]
    assert asyncio.run(auto.check(box.snapshot())) is None and box.ports["usb2"] is True and pushes.pushed == []
    box.snapshot = real  # type: ignore[method-assign]
    asyncio.run(auto.check(snap(box, 50, 40)))
    assert auto.state["failsafe_done"] is False  # fresh readings: the next spell is the automation's again


def test_a_new_spell_starts_clean_even_after_the_automation_was_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto = automation(monkeypatch)
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50, 1000))) == "failsafe_off"
    auto.set_enabled(False)
    assert auto.state["failsafe_done"] is False  # switching starts over
    auto.state["failsafe_done"] = True  # as an older version left it behind
    asyncio.run(auto.check(snap(box, 50, 40)))  # fresh readings while the automation is off
    assert auto.state["failsafe_done"] is False
    auto.set_enabled(True)
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50, 2000))) == "failsafe_off"  # the next outage is covered


def test_a_probe_dropout_neither_clears_nor_restarts_the_probe_alarm(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    alerts = RecordingAlerts({"temp_high": config.alert_rules()["rules"]["temp_high"]})  # 10 min hold
    clock = [1000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: clock[0])
    hot, dropout = {"climate": {"probe_temp": 31.0, "temp": 26.0}}, {"climate": {"probe_temp": None, "temp": 26.0}}
    for minutes, reading in ((0, hot), (6, dropout), (12, hot)):  # the dropout falls into the hold time
        clock[0] = 1000.0 + minutes * 60
        asyncio.run(alerts.check(reading))
    assert alerts.pushed == ["Zu warm (Fühler)|Zu warm (Fühler) (Grenze 30 °C). Jetzt 31,0 °C."]
    clock[0] += 90
    asyncio.run(alerts.check(dropout))
    assert len(alerts.pushed) == 1  # no false all-clear from the built-in sensor
    clock[0] += 90
    asyncio.run(alerts.check({"climate": {"probe_temp": 25.0, "temp": 24.0}}))
    assert alerts.pushed[-1] == "Wieder ok|Zu warm (Fühler): wieder im Rahmen. Jetzt 25,0 °C."
    # Raised by the built-in sensor (no probe at all), cleared by the probe once it is back and fine.
    for _ in range(2):
        clock[0] += 601
        asyncio.run(alerts.check({"climate": {"probe_temp": None, "temp": 31.0}}))
    assert alerts.pushed[-1].endswith("Jetzt 31,0 °C am eingebauten Sensor, der Fühler meldet keinen Wert.")
    clock[0] += 90
    asyncio.run(alerts.check({"climate": {"probe_temp": 24.0, "temp": 31.0}}))
    assert alerts.pushed[-1].startswith("Wieder ok|")


def test_a_camera_in_the_account_but_not_on_the_network_raises_the_alarm(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def nowhere(own_ip: str) -> None:
        return None

    monkeypatch.setattr(camera_mod, "find_camera_ip", nowhere)
    cam = Camera(tmp_path)
    cam.ffmpeg, cam._user, cam._password = "ffmpeg", "u", "p"  # credentials from the account, no GROWCAM_IP
    asyncio.run(cam.rescan())
    status = cam.status()
    assert status["configured"] is False and status["searching"] is True
    rule = config.alert_rules()["rules"]["camera_offline"]
    assert breached(rule, {"camera": status}) is True  # was None forever: no ip, so "not configured"
    cam._search_since -= 900  # type: ignore[operator]
    assert 890 <= cam.status()["offline_seconds"] <= 910
    assert "seit 15 min nicht im Netz" in alerts_mod.Alerts._reading(rule, {"camera": cam.status()}, "de")
    cam.ip = "10.0.0.9"  # found: watch() checks it from now on
    assert cam.status()["searching"] is False and breached(rule, {"camera": cam.status()}) is None
    assert breached(rule, {"camera": Camera(tmp_path).status()}) is None  # no camera in the account: no alarm


def test_an_unreadable_ports_file_fails_closed(tmp_path, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    ports = tmp_path / "ports.json"
    monkeypatch.setattr(config, "PORTS_FILE", ports)
    ports.write_text('{"usb1": {"label": "Kamera", "always_on": true,}}', encoding="utf-8")  # one trailing comma
    box, mqtt = make_box()
    with pytest.raises(CommandRefused, match="nicht lesbar"):
        asyncio.run(box.set_port("usb1", False))
    with pytest.raises(CommandRefused):
        asyncio.run(box.set_port("usb2", False))  # nothing known yet: nothing goes off
    asyncio.run(box.set_port("usb2", True))  # on is always safe
    assert len(mqtt.sent) == 1 and caplog.text.count("ports.json unreadable") == 1  # logged once, not per call

    async def page_state() -> dict:  # type: ignore[type-arg]
        async with TestClient(TestServer(make_app(DemoBox(), RecordingAlerts({}), "k"))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            return await (await client.get("/api/state")).json()

    assert all(p["always_on"] for p in asyncio.run(page_state())["ports"].values())  # the page locks the off too
    ports.write_text(json.dumps({"usb1": {"label": "Kamera", "always_on": True}}), encoding="utf-8")
    assert config.port_rules()["usb1"]["always_on"] is True  # type: ignore[index]
    ports.write_text('{"usb1": {"label": "Kamera", "always_on": false,}}', encoding="utf-8")  # broken again
    assert config.port_rules()["usb1"]["always_on"] is True  # type: ignore[index]  # the last good version
    with pytest.raises(CommandRefused, match="Kamera bleibt immer an"):
        asyncio.run(box.set_port("usb1", False))
    asyncio.run(box.set_port("usb2", False))  # known to be free
    ports.unlink()
    assert config.port_rules() == {}  # no file at all: nothing configured, nothing locked


def test_nested_error_reasons_are_said_in_the_readers_language() -> None:
    from vgrow import vision as v

    class DeadCamera:
        async def photo(self) -> bytes:
            raise TextError("camera.no_answer")

    vis = v.Vision(DeadCamera())
    record = asyncio.run(vis.run({}, None, "de"))  # the daily run of a German server
    assert record["error"] == "Tagesfoto fehlgeschlagen: Kamera antwortet nicht"
    token = i18n.REQUEST_LANG.set("en")  # an English page reads it later
    try:
        assert vis.view()["error"] == "Daily photo failed: The camera does not answer"
    finally:
        i18n.REQUEST_LANG.reset(token)

    class NoBoxApi:
        async def login(self, email: str, password: str) -> object:
            return object()

        async def get_devices(self, tokens: object) -> list:  # type: ignore[type-arg]
            return []

    box, _ = make_box()
    box._api, box._aws = NoBoxApi(), object()  # type: ignore[assignment]
    with pytest.raises(TextError) as raised:
        asyncio.run(box._login())
    box._errors["login"] = raised.value  # what _supervise keeps
    assert box.last_error == "Anmeldung: Keine VGrow (VSCTLY18) in diesem Vivosun-Konto. Gefunden: nichts"
    token = i18n.REQUEST_LANG.set("en")
    try:
        assert box.last_error == "Login: No VGrow (VSCTLY18) in this Vivosun account. Found: nothing"
        assert str(Text("box.none_found")) == "nothing"
    finally:
        i18n.REQUEST_LANG.reset(token)
