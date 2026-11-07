"""Offline tests: no Vivosun login, no network. Run: python -m pytest"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import alerts as alerts_mod
from vgrow.box import Box, CommandRefused, climate_view, fan_step, scale_row
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app
from vgrow.vivosun.const import CFAN_LEVEL_MAP, DFAN_LEVEL_MAP
from vgrow.vivosun.models import DeviceInfo

VGROW = DeviceInfo("dev1", "vivosun-VSCTLY18-acct-dev1", "vivosun/VS_COMMON/VSCTLY18/acct/dev1", "VGrow", True, 1, "controller")


class FakeMqtt:
    is_connected = True

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def publish_shadow_update(self, payload: str, qos: int = 0) -> None:
        self.sent.append(json.loads(payload))

    async def publish(self, topic: str, payload: bytes, qos: int = 0) -> None: ...


def make_box(reported: dict) -> tuple[Box, FakeMqtt]:
    box = Box("x@example.test", "unused")
    box.device = VGROW
    box._mqtt = FakeMqtt()  # type: ignore[assignment]
    box.reported = reported
    return box, box._mqtt  # type: ignore[return-value]


MANUAL = {
    "light": {"mode": 0, "inPlan": 0, "lv": 75, "manu": {"lv": 75, "spec": 50}},
    "cFan": {"mode": 0, "lv": 70, "manu": {"lv": 70}},
    "dFan": {"mode": 0, "lv": 50, "manu": {"lv": 50}},
    "usb1": {"mode": 0, "inPlan": 0, "lv": 0, "manu": {"lv": 0}},
    "usb2": {"mode": 1, "inPlan": 0, "lv": 100, "manu": {"lv": 100}},
    "connected": True,
}


def test_scale_row_divides_and_drops_sentinel() -> None:
    row = scale_row({"time": 1, "inTemp": 2004, "inHumi": 5508, "inVpd": 105, "waterLv": 7500,
                     "waterT": 2226, "outTemp": -6666, "rssi": -35})
    assert row["inTemp"] == 20.04 and row["inHumi"] == 55.08 and row["inVpd"] == 1.05
    assert row["waterLv"] == 75.0 and row["waterT"] == 22.26
    assert row["outTemp"] is None and row["rssi"] == -35


def test_climate_view_falls_back_to_builtin_sensor() -> None:
    view = climate_view(scale_row({"bTemp": 2100, "bHumi": 6000, "bVpd": 99}))
    assert view["temp"] == 21.0 and view["humidity"] == 60.0 and view["vpd"] == 0.99
    assert view["water_level"] is None


def test_fan_step_maps_both_tables() -> None:
    assert fan_step(70, CFAN_LEVEL_MAP) == 5
    assert fan_step(50, DFAN_LEVEL_MAP) == 4
    assert fan_step(0, DFAN_LEVEL_MAP) == 0
    assert fan_step(None, DFAN_LEVEL_MAP) is None


def test_snapshot_reads_vgrow_shadow() -> None:
    box, _ = make_box(json.loads(json.dumps(MANUAL)))
    snap = box.snapshot()
    assert snap["light"]["level"] == 75 and snap["light"]["spectrum"] == "veg"
    assert snap["light"]["spectrum_writable"] is True
    assert snap["cfan"]["step"] == 5 and snap["dfan"]["step"] == 4
    assert snap["ports"]["usb1"] == {"on": False, "mode": 0, "in_plan": 0, "accessory": None, "can_write": True}
    assert snap["ports"]["usb2"]["can_write"] is False  # auto mode: read only


def test_commands_build_upstream_payloads_with_token() -> None:
    box, mqtt = make_box(json.loads(json.dumps(MANUAL)))

    async def run() -> None:
        await box.set_light(10)  # below the device minimum: clamps to 25
        await box.set_dfan(6)
        await box.set_port("usb1", True)
        await box.set_spectrum("bloom")

    asyncio.run(run())
    bodies = [{k: v for k, v in m.items() if k != "clientToken"} for m in mqtt.sent]
    assert all(isinstance(m["clientToken"], str) for m in mqtt.sent)
    assert bodies == [
        {"state": {"desired": {"light": {"mode": 0, "manu": {"lv": 25}}}}},
        {"state": {"desired": {"dFan": {"mode": 0, "manu": {"lv": DFAN_LEVEL_MAP[6]}}}}},
        {"state": {"desired": {"usb1": {"manu": {"lv": 100}}}}},
        {"state": {"desired": {"light": {"manu": {"spec": 100}}}}},
    ]


def test_guards_refuse_before_sending() -> None:
    planned = json.loads(json.dumps(MANUAL))
    planned["light"]["inPlan"] = 1
    box, mqtt = make_box(planned)
    with pytest.raises(CommandRefused):
        asyncio.run(box.set_spectrum("veg"))
    with pytest.raises(CommandRefused):
        asyncio.run(box.set_port("usb2", False))  # usb2 is on auto
    with pytest.raises(CommandRefused):
        asyncio.run(box.set_port("plug1", True))  # A22 port, not on a VGrow
    assert mqtt.sent == []


def test_pending_settles_on_report_and_on_rejection() -> None:
    box, mqtt = make_box(json.loads(json.dumps(MANUAL)))
    asyncio.run(box.set_dfan(10))
    assert box.snapshot()["pending"]["dFan.level"]["status"] == "pending"
    # A desired-only acknowledgement is not a device report and must not settle anything.
    box._on_message("$aws/things/x/shadow/update/accepted",
                    json.dumps({"state": {"desired": {"dFan": {"manu": {"lv": 100}}}}}).encode(), 1)
    assert box.snapshot()["pending"]["dFan.level"]["status"] == "pending"
    box._on_message("$aws/things/x/shadow/update/documents",
                    json.dumps({"current": {"state": {"reported": {"dFan": {"lv": 100, "manu": {"lv": 100}}}}}}).encode(), 1)
    assert box.snapshot()["pending"]["dFan.level"]["status"] == "ok"

    asyncio.run(box.set_light(50))
    token = mqtt.sent[-1]["clientToken"]
    box._on_message("$aws/things/x/shadow/update/rejected", json.dumps({"clientToken": token}).encode(), 1)
    assert box.snapshot()["pending"]["light.level"]["status"] == "rejected"


def test_live_vgrow_row_two_sensors_and_unexplained_water() -> None:
    # Raw values read from a real VGrow Y18 on 22.10.2026 (sensor on aux35Dev 9, no pump yet).
    view = climate_view(scale_row({"bTemp": 2568, "bHumi": 4638, "bVpd": 176, "pTemp": 2230,
                                   "pHumi": 6841, "pVpd": 85, "waterLv": 20000, "coreTemp": 4870}))
    assert (view["temp"], view["humidity"], view["vpd"]) == (25.68, 46.38, 1.76)
    assert (view["probe_temp"], view["probe_humidity"], view["probe_vpd"]) == (22.3, 68.41, 0.85)
    assert view["water_level"] is None and view["water_raw"] == 20000


def test_vgrow_natural_wind_is_read_but_not_written() -> None:
    live = json.loads(json.dumps(MANUAL))
    live["cFan"] = {"mode": 0, "inPlan": 0, "lv": 185, "nw": 1, "osc": 0, "manu": {"lv": 85}}
    box, mqtt = make_box(live)
    cfan = box.snapshot()["cfan"]
    assert cfan == {"step": 8, "natural_wind": True, "nw_writable": False, "mode": 0}
    with pytest.raises(CommandRefused):
        asyncio.run(box.set_cfan(None, natural_wind=True))
    assert mqtt.sent == []


def test_pending_times_out() -> None:
    box, _ = make_box(json.loads(json.dumps(MANUAL)))
    asyncio.run(box.set_cfan(9))
    box.pending["cFan.level"].deadline = time.time() - 1
    assert box.snapshot()["pending"]["cFan.level"]["status"] == "timeout"


# ---------- alerts ----------

class RecordingAlerts(alerts_mod.Alerts):
    def __init__(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        self._session = None
        self.config = {"remind_hours": 6, "rules": {
            "temp_high": {"label": "Zu warm", "metric": "temp", "above": 30, "for_minutes": 10},
            "offline": {"label": "Box offline", "metric": "offline", "for_minutes": 10},
        }}
        self._state_path = tmp_path / "state.json"
        self.state = {}
        self.target = ("https://ntfy.example", "t")
        self.pushed: list[str] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.pushed.append(f"{title}|{message}")
        return True


def test_alert_fires_once_after_hold_then_recovers(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(alerts_mod.config, "STATE_DIR", tmp_path)
    a = RecordingAlerts(tmp_path)
    hot = {"online": True, "sensors_age": 30, "climate": {"temp": 31.5}}
    clock = [1000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: clock[0])
    asyncio.run(a.check(hot))
    assert a.pushed == []  # not held for 10 minutes yet
    clock[0] += 601
    asyncio.run(a.check(hot))
    clock[0] += 90
    asyncio.run(a.check(hot))
    assert len(a.pushed) == 1 and "31,5" in a.pushed[0]  # German push: decimal comma
    asyncio.run(a.check({"online": True, "sensors_age": 30, "climate": {"temp": 25.0}}))
    assert len(a.pushed) == 2 and a.pushed[1].startswith("Wieder ok")


def test_missing_reading_never_alerts(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(alerts_mod.config, "STATE_DIR", tmp_path)
    a = RecordingAlerts(tmp_path)
    assert alerts_mod.breached(a.config["rules"]["temp_high"], {"climate": {"temp": None}}) is None


# ---------- server ----------

def test_server_requires_key_and_runs_commands(tmp_path) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        a = RecordingAlerts(tmp_path)
        app = make_app(DemoBox(), a, "secret-key")
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/state")).status == 403
            assert (await client.get("/?key=wrong", allow_redirects=False)).status == 403
            resp = await client.get("/?key=secret-key", allow_redirects=False)
            assert resp.status == 302 and COOKIE in resp.cookies
            client.session.cookie_jar.update_cookies({COOKIE: "secret-key"})
            state = await (await client.get("/api/state")).json()
            assert state["demo"] is True and state["alerts"][0]["label"] == "Zu warm"
            ok = await client.post("/api/cmd", json={"action": "dfan", "step": 7})
            assert ok.status == 200
            assert (await (await client.get("/api/state")).json())["dfan"]["step"] == 7
            bad = await client.post("/api/cmd", json={"action": "port", "port": "plug1", "on": True})
            assert bad.status == 409
            assert (await client.get("/manifest.webmanifest")).status == 200  # public for home-screen install

    asyncio.run(run())


# ---------- camera ----------

from vgrow.camera import Camera, live_args, record_args, stream_url  # noqa: E402


def test_stream_url_quotes_credentials_and_picks_stream() -> None:
    sub = stream_url("192.168.1.50", "us er", "p&ss/w", sub=True)
    assert sub == ("rtsp://192.168.1.50:554/user=us%20er&password=p%26ss%2Fw&channel=1&stream=1.sdp?real_stream")
    assert "stream=0.sdp" in stream_url("10.0.0.2", "a", "b", sub=False)


def test_recording_and_live_never_carry_audio(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rec = record_args("ffmpeg", "rtsp://x", tmp_path / "a.mp4", 7200)
    assert "-an" in rec and rec[rec.index("-c:v") + 1] == "copy" and rec[rec.index("-t") + 1] == "7200"
    assert "-an" in live_args("ffmpeg", "rtsp://x")


def test_recordings_list_and_safe_paths(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cam = Camera(tmp_path)
    (tmp_path / "2026-10-22_09-34-05.mp4").write_bytes(b"x" * 2048)
    (tmp_path / "notes.txt").write_text("not a recording")
    assert [r["name"] for r in cam.recordings()] == ["2026-10-22_09-34-05.mp4"]
    assert cam.path_for("../../.vgrow.env") is None and cam.path_for("notes.txt") is None
    cam.delete("2026-10-22_09-34-05.mp4")
    assert cam.recordings() == []
    with pytest.raises(ValueError):
        cam.delete("2026-10-22_09-34-05.mp4")


def test_camera_routes_404_without_camera(tmp_path) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        app = make_app(DemoBox(), RecordingAlerts(tmp_path), "k")
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            assert (await client.get("/camera/snapshot.jpg")).status == 404
            assert (await client.get("/recordings/2026-10-22_09-34-05.mp4")).status == 404
            assert (await (await client.get("/api/state")).json())["camera"] == {"configured": False}
            assert (await client.post("/api/cmd", json={"action": "record_start"})).status == 404

    asyncio.run(run())


def test_camera_offline_alert_only_on_a_real_check() -> None:
    rule = {"label": "Kamera offline", "metric": "camera", "for_minutes": 10}
    assert alerts_mod.breached(rule, {"camera": {"configured": True, "reachable": False}}) is True
    assert alerts_mod.breached(rule, {"camera": {"configured": True, "reachable": True}}) is False
    assert alerts_mod.breached(rule, {"camera": {"configured": True, "reachable": None}}) is None
    assert alerts_mod.breached(rule, {"camera": {"configured": False}}) is None


def test_camera_status_reports_offline_time(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cam = Camera(tmp_path)
    cam.reachable, cam.offline_since = False, time.time() - 125
    status = cam.status()
    assert status["reachable"] is False and 120 <= status["offline_seconds"] <= 130


def test_locked_port_cannot_be_switched_off(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from vgrow import config as cfg
    ports = tmp_path / "ports.json"
    ports.write_text(json.dumps({"usb1": {"label": "Kamera", "always_on": True}}), encoding="utf-8")
    monkeypatch.setattr(cfg, "PORTS_FILE", ports)

    async def run() -> None:
        box = DemoBox()
        app = make_app(box, RecordingAlerts(tmp_path), "k")
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            state = await (await client.get("/api/state")).json()
            assert state["ports"]["usb1"]["label"] == "Kamera" and state["ports"]["usb1"]["always_on"] is True
            assert state["ports"]["usb2"]["always_on"] is False
            refused = await client.post("/api/cmd", json={"action": "port", "port": "usb1", "on": False})
            assert refused.status == 409
            assert (await client.post("/api/cmd", json={"action": "port", "port": "usb1", "on": True})).status == 200

    asyncio.run(run())


# ---------- plant profile ----------

from datetime import date as _date  # noqa: E402

from vgrow.plant import PlantLog, current_stage, evaluate  # noqa: E402

PROFILE = {
    "plant": {"strain": "Test Auto", "start_date": "2026-08-31"},
    "stages": [
        {"key": "veg", "label": "Wachstum", "from_day": 0, "to_day": 30, "targets": []},
        {"key": "bloom", "label": "Blüte", "from_day": 30, "to_day": 70, "targets": [
            {"metric": "probe_humidity", "label": "Feuchte", "unit": "%", "min": 40, "max": 55,
             "advice_high": "Abluft hoch"},
            {"metric": "probe_temp", "label": "Temp Tag", "unit": "°C", "min": 20, "max": 26, "when": "day"},
            {"metric": "probe_temp", "label": "Temp Nacht", "unit": "°C", "min": 18, "max": 22, "when": "night"},
        ]},
    ],
}


def test_stage_by_days_then_manual_override(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log = PlantLog(tmp_path / "log.json")
    assert current_stage(PROFILE, log, _date(2026, 9, 10))["key"] == "veg"
    assert current_stage(PROFILE, log, _date(2026, 10, 22))["key"] == "bloom"
    log.set_stage("veg", "2026-10-20")
    stage = current_stage(PROFILE, log, _date(2026, 10, 22))
    assert stage["key"] == "veg" and stage["set_by"] == "manual"
    assert PlantLog(tmp_path / "log.json").data["stages"][-1]["key"] == "veg"  # persisted


def test_evaluate_ranges_day_night_and_advice() -> None:
    bloom = PROFILE["stages"][1]
    snap = {"climate": {"probe_humidity": 82.0, "probe_temp": 22.1}, "light": {"level": 25}}
    rows = {r["label"]: r for r in evaluate(bloom, snap)}
    assert rows["Feuchte"]["status"] == "high" and rows["Feuchte"]["advice"] == "Abluft hoch"
    assert rows["Temp Tag"]["status"] == "ok" and "Temp Nacht" not in rows  # lights on: night row hidden
    night = {r["label"] for r in evaluate(bloom, {"climate": {"probe_temp": 17.0}, "light": {"level": 0}})}
    assert night == {"Feuchte", "Temp Nacht"}


def test_a_target_without_limits_is_none_not_ok() -> None:
    stage = {"targets": [{"metric": "water_level", "label": "Wasser", "unit": "%", "min": None, "max": None,
                          "advice_low": "x", "advice_high": "y"}]}
    row = evaluate(stage, {"climate": {"water_level": 75.0}})[0]
    assert row["status"] == "none" and row["advice"] is None  # a value with nothing to hold it against
    assert evaluate(stage, {"climate": {"water_level": None}})[0]["status"] == "unknown"


def test_height_validation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log = PlantLog(tmp_path / "log.json")
    log.add_height(58)
    with pytest.raises(ValueError):
        log.add_height(0)
    assert log.data["heights"][-1]["cm"] == 58


def test_run_loads_its_cards_and_library_lists_them(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from vgrow.plant import library_index, load_profile
    lib = tmp_path / "kartei"
    (lib / "stages").mkdir(parents=True)
    (lib / "strains").mkdir()
    (lib / "stages" / "autoflower.json").write_text(json.dumps(
        {"name": "Autoflower", "stages": PROFILE["stages"], "sources": {"s1": "https://example.org/a"}}), encoding="utf-8")
    (lib / "strains" / "test-auto.json").write_text(json.dumps(
        {"name": "Test Auto", "facts": {"cycle_weeks": "8-10"}, "sources": {"s2": "https://example.org/b"}}), encoding="utf-8")
    run = tmp_path / "plant.json"
    run.write_text(json.dumps({"plant": {"start_date": "2026-08-31"}, "stages_card": "autoflower",
                               "strain_card": "test-auto"}), encoding="utf-8")
    profile = load_profile(run, lib)
    assert [s["key"] for s in profile["stages"]] == ["veg", "bloom"]
    assert profile["strain"]["facts"]["cycle_weeks"] == "8-10" and set(profile["sources"]) == {"s1", "s2"}
    index = library_index(lib)
    assert [c["name"] for c in index["strains"]] == ["Test Auto"] and index["nutrients"] == []


# ---------- vision (local model faked, no Ollama needed) ----------


def _fake_ollama(vis, content: str, calls: list) -> None:  # type: ignore[no-untyped-def, type-arg]
    async def chat(body):  # type: ignore[no-untyped-def]
        calls.append(body)
        return {"model": body["model"], "message": {"role": "assistant", "content": content},
                "prompt_eval_count": 900, "eval_count": 300}

    vis._chat = chat


def test_vision_reads_photo_locally_at_no_cost(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from vgrow import vision as v
    monkeypatch.setattr(v, "ANALYSES", tmp_path / "analysis")
    calls: list = []  # type: ignore[type-arg]
    reading = {"stage_guess": "early_flower", "confidence": "medium", "stage_reason": "weiße Härchen",
               "visible_signs": ["Härchen an den Spitzen"], "issues": [], "next_steps": ["Abluft hoch"],
               "not_assessable": ["Trichomfarbe"]}
    photo = tmp_path / "2026-10-22.jpg"
    photo.write_bytes(b"\xff\xd8 fake jpeg")
    vis = v.Vision(camera=None)
    _fake_ollama(vis, json.dumps(reading), calls)
    record = asyncio.run(vis.analyze(photo, {"climate": {"probe_humidity": 82}}, None))
    assert record["result"]["stage_guess"] == "early_flower"
    assert record["local"] is True and "usd" not in record
    sent = calls[0]
    assert sent["model"] == vis.model and sent["keep_alive"] == 0 and sent["stream"] is False
    assert sent["think"] is False  # reasoning first made a photo take minutes on the Mac's CPU
    assert sent["format"]["properties"]["stage_guess"]["enum"][-1] == "unclear"
    assert sent["messages"][1]["images"] and sent["messages"][0]["role"] == "system"
    assert v.Vision.latest()["result"]["next_steps"] == ["Abluft hoch"]


def test_vision_unreadable_answer_is_recorded_not_crashed(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from vgrow import vision as v
    monkeypatch.setattr(v, "ANALYSES", tmp_path / "analysis")
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    for content in ("kein json", json.dumps({"stage_guess": "bloom"})):
        vis = v.Vision(camera=None)
        _fake_ollama(vis, content, [])
        record = asyncio.run(vis.analyze(photo, {}, None))
        assert record["result"] is None and "keine lesbare" in record["error"]


def test_model_errors_become_instructions() -> None:
    from vgrow.vision import explain
    assert "Ollama" in explain(Exception("Cannot connect to host 127.0.0.1:11434"))
    # The default model is the one Hank uses too (spec 0.2 point 2, WP9).
    assert "ollama pull gemma4:12b" in explain(Exception('model "gemma4:12b" not found, try pulling it first'))
    assert "Minuten" in explain(TimeoutError())


def test_real_profile_loads_with_estimate_and_sources() -> None:
    from vgrow.plant import PlantLog, current_stage, load_profile
    profile = load_profile()
    assert profile["strain"]["name"] == "Ice Cream Cake Automatic"
    keys = [s["key"] for s in profile["stages"]]
    assert keys == ["seedling", "veg", "early_flower", "mid_flower", "late_flower", "harvest"]
    for stage in profile["stages"]:
        for target in stage.get("targets", []):
            assert target["sources"] and all(s in profile["sources"] for s in target["sources"]), target
    import tempfile, pathlib
    log = PlantLog(pathlib.Path(tempfile.mkdtemp()) / "log.json")
    stage = current_stage(profile, log)
    assert stage["key"] == "early_flower" and stage["set_by"] == "estimate"


def test_apply_raises_light_at_most_25_points(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from vgrow import plant as plant_mod
    run = tmp_path / "plant.json"
    run.write_text(json.dumps({"plant": {"start_date": "2026-08-31"}, "stages": [
        {"key": "bloom", "label": "Blüte", "from_day": 0, "to_day": 999,
         "preset": {"light_percent": 100, "spectrum": "bloom", "cfan_step": 10}}]}), encoding="utf-8")
    monkeypatch.setattr(plant_mod, "PLANT_FILE", run)

    async def run_it() -> None:
        box = DemoBox()
        box.light["level"] = 25
        app = make_app(box, RecordingAlerts(tmp_path), "k", plant_log=plant_mod.PlantLog(tmp_path / "log.json"))
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            res = await (await client.post("/api/cmd", json={"action": "plant_apply"})).json()
            assert box.light["level"] == 50 and "Ziel 100" in res["applied"][0]
            assert box.light["spectrum"] == "bloom" and box.cfan_step == 10

    asyncio.run(run_it())


# ---------- humidifier automation ----------

def _auto(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from vgrow import automation as am
    from vgrow import config as cfg
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    monkeypatch.setattr(am, "load_profile", lambda: PROFILE)
    log = PlantLog(tmp_path / "log.json")
    log.set_stage("bloom")
    box = DemoBox()
    auto = am.HumidifierAutomation(box, None, log)
    auto.state_path, auto.log_path = tmp_path / "state.json", tmp_path / "auto.jsonl"
    # The owner named the humidifier on USB 2 on the page: only then does the rule switch it on (review 2, M3).
    auto.guard.assign("usb2", "humidifier", box.snapshot())
    return am, box, auto


def _snap(box, rh):  # type: ignore[no-untyped-def]
    s = box.snapshot()
    s["climate"]["probe_humidity"] = rh
    return s


def test_humidifier_off_above_band_on_below_with_wait(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    am, box, auto = _auto(tmp_path, monkeypatch)
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(_snap(box, 82))) is None  # disabled by default
    auto.set_enabled(True)
    assert asyncio.run(auto.check(_snap(box, 82))) == "humidifier_off" and box.ports["usb2"] is False
    assert asyncio.run(auto.check(_snap(box, 35))) is None  # 15-minute rest before restarting
    auto.state["last_switch"] -= am.MIN_SWITCH_SECONDS + 1
    assert asyncio.run(auto.check(_snap(box, 50))) is None  # inside the band (40-55): leave it
    assert asyncio.run(auto.check(_snap(box, 35))) == "humidifier_on" and box.ports["usb2"] is True
    assert len((tmp_path / "auto.jsonl").read_text(encoding="utf-8").splitlines()) == 3  # on + off + enable


def test_manual_switch_pauses_automation(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _, box, auto = _auto(tmp_path, monkeypatch)
    auto.set_enabled(True)
    box.ports["usb2"] = True
    auto.manual_switch()
    assert asyncio.run(auto.check(_snap(box, 90))) is None and box.ports["usb2"] is True
    assert auto.view()["paused_until"] is not None


def test_cfan_level_confirmed_while_natural_wind_stays_on() -> None:
    live = json.loads(json.dumps(MANUAL))
    live["cFan"] = {"mode": 0, "lv": 185, "nw": 1, "manu": {"lv": 85}}
    box, _ = make_box(live)
    asyncio.run(box.set_cfan(10))
    box._on_message("$aws/things/x/shadow/update/documents", json.dumps(
        {"current": {"state": {"reported": {"cFan": {"lv": 200, "nw": 1, "manu": {"lv": 100}}}}}}).encode(), 1)
    assert box.snapshot()["pending"]["cFan.level"]["status"] == "ok"


def test_water_log_validates_and_keeps_readings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log = PlantLog(tmp_path / "log.json")
    log.add_water(6.3, 1.4)
    log.add_water(None, 1.8)
    with pytest.raises(ValueError):
        log.add_water(None, None)
    with pytest.raises(ValueError):
        log.add_water(15, None)
    readings = PlantLog(tmp_path / "log.json").data["water"]
    assert [(w["ph"], w["ec"]) for w in readings] == [(6.3, 1.4), (None, 1.8)]


def test_real_profile_has_soil_ph_target_with_source() -> None:
    from vgrow.plant import load_profile
    profile = load_profile()
    ph = profile["water_targets"]["ph"]
    assert (ph["min"], ph["max"]) == (6.0, 6.5) and all(s in profile["sources"] for s in ph["sources"])
    assert profile["medium"]["type"] == "soil"
