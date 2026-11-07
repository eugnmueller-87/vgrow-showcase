"""Full demo mode: every card filled without hardware, and nothing in it can pass for the real box."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from datetime import date

from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, feeding
from vgrow import demo as demo_mod
from vgrow.alerts import Alerts, breached
from vgrow.automation import HumidifierAutomation
from vgrow.demo import DemoBox, DemoCamera, setup_demo
from vgrow.plant import PlantLog, load_profile
from vgrow.server import COOKIE, alert_snapshot, make_app, serve


def demo_parts():  # type: ignore[no-untyped-def]
    """What serve() wires for --demo, without the endless serving."""
    box, alerts, log = DemoBox(), Alerts(), PlantLog()
    alerts.demo = True
    automation = HumidifierAutomation(box, alerts, log)
    camera, vision = setup_demo(log, automation)
    return box, make_app(box, alerts, "k", camera, log, vision, automation), vision


async def client_for(app):  # type: ignore[no-untyped-def]
    client = TestClient(TestServer(app))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: "k"})
    return client


def test_demo_state_fills_every_card() -> None:
    async def run() -> None:
        _, app, _ = demo_parts()
        client = await client_for(app)
        try:
            de = await (await client.get("/api/state")).json()
            en = await (await client.get("/api/state", headers={"X-Lang": "en"})).json()
        finally:
            await client.close()
        assert de["demo"] is True
        assert de["camera"] == {"configured": True, "name": "DEMO", "ip": None, "error": None, "reachable": True,
                                "offline_seconds": None, "recording": None, "max_minutes": 120, "demo": True,
                                "hidden": False}
        c = de["climate"]
        assert all(c[k] is not None for k in ("temp", "humidity", "vpd", "probe_temp", "probe_humidity", "probe_vpd",
                                              "water_level", "water_temp"))
        # USB 1 is the drip pump since 23.10.2026 (ports.json): off in the demo, not locked; the camera needs its own power
        assert de["ports"]["usb1"] == {**de["ports"]["usb1"], "on": False, "label": "Tropfbewässerung (Pumpe)",
                                     "always_on": False}
        assert en["ports"]["usb1"]["label"] == "Drip irrigation (pump)"
        auto = de["automation"]
        assert auto["enabled"] is True and auto["stale"] is False and auto["band"] == [45, 60]
        assert [e["action"] for e in auto["recent"]] == [
            "humidifier_on", "humidifier_off", "paused_manual", "humidifier_on", "automation_on"]
        assert [e["value"] for e in auto["recent"]] == [44.6, 60.8, None, 44.1, None]
        assert auto["recent"][0]["time"] > auto["recent"][1]["time"] > time.time() - 7 * 3600
        plant = de["plant"]
        assert plant["configured"] and plant["height"]["cm"] == 43 and len(plant["heights"]) == 3
        assert [w["ph"] for w in plant["water"]] == [6.2, 6.4, 6.3] and plant["stage"]["set_by"] == "estimate"
        status = {r["metric"] if r["metric"] != "probe_temp" else r["label"]: r["status"] for r in plant["checks"]}
        assert status["probe_humidity"] == status["probe_vpd"] == status["Temperatur bei Licht"] == "ok"
        assert status["light_percent"] == "low"  # 75 % against the flower preset: one item under "to do"
        for lang, state, prefix in (("de", de, "Demo-Einschätzung"), ("en", en, "Demo assessment")):
            vision = state["vision"]
            latest = vision["latest"]
            assert vision["demo"] is True and vision["model"] == "demo" and vision["busy"] is False
            assert latest["model"] == "demo" and latest["demo"] is True and latest["lang"] == lang
            result = latest["result"]
            assert result["stage_reason"].startswith(prefix)
            assert all(result[k] for k in ("stage_guess", "confidence", "stage_reason", "visible_signs", "issues",
                                           "next_steps", "not_assessable"))
            assert "context" not in latest and latest["photo"].endswith(".jpg")
        assert en["automation"]["recent"] == de["automation"]["recent"]
        assert not any(a["active"] for a in de["alerts"])

    asyncio.run(run())


def test_demo_starts_the_feeding_chart_as_a_draft_every_time() -> None:
    """Like a new run: no amounts until the chart and the stage are confirmed on the page, whatever an earlier demo
    run did."""
    setup = feeding.setups_of(load_profile())["biobizz"]  # an earlier demo run confirmed the chart and fed
    feeding.confirm(setup, setup["card"], feeding.fingerprint(setup, feeding.load_card(setup["card"])))
    feeding.log_path().write_text('{"time": "2026-10-22T09:00:00", "liters": 1.5, "items": []}\n', encoding="utf-8")

    async def run() -> None:
        _, app, _ = demo_parts()
        client = await client_for(app)
        try:
            fed = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            # Two fertilizers in plant.json and a fresh plant log: first the choice, nothing computed until then.
            assert (fed["state"], fed["doses"], fed["choice"]["needed"]) == ("needs_choice", None, True)
            assert [o["id"] for o in fed["choice"]["options"]][:2] == ["biobizz", "vivosun"]  # then the library
            ok = await client.post("/api/cmd", json={"action": "feeding_setup", "setup": "biobizz"})
            assert ok.status == 200
            fed = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            assert (fed["state"], fed["confirmed"], fed["doses"], fed["last_fed"]) == ("draft", False, None, None)
            assert fed["chart"] and fed["sources"]["N1"]["url"].startswith("https://")  # the chart and its source show
            ok = await client.post("/api/cmd", json={"action": "feeding_confirm", "card": fed["card"]["id"],
                                                     "fingerprint": fed["fingerprint"]})
            assert ok.status == 200  # two taps on the page later: the chart is confirmed, the week not yet
            fed = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            assert (fed["confirmed"], fed["state"], fed["doses"]) == (True, "needs_stage", None)
            stage = (await (await client.get("/api/state")).json())["plant"]["stage"]
            assert (await client.post("/api/cmd", json={"action": "plant_stage", "key": stage["key"]})).status == 200
            fed = (await (await client.get("/api/state")).json())["plant"]["feeding"]
            assert fed["state"] == "ok" and fed["week"]["key"] == "bloom_1" and fed["doses"]  # now the amounts
        finally:
            await client.close()

    asyncio.run(run())


def test_demo_camera_is_a_labelled_placeholder_and_never_records() -> None:
    async def run() -> None:
        _, app, _ = demo_parts()
        client = await client_for(app)
        try:
            still = await client.get("/camera/snapshot.jpg")
            assert still.status == 200 and still.content_type == "image/svg+xml"
            body = await still.text()
            assert "DEMO-Kamera" in body and "Platzhalter, kein echtes Kamerabild" in body and "inoffiziell" in body
            assert "camera" not in body  # no English left in the German picture
            en = await (await client.get("/camera/snapshot.jpg", headers={"X-Lang": "en"})).text()
            assert "DEMO camera" in en and "Placeholder, not a real camera image" in en and "unofficial" in en
            live = await client.get("/camera/live.mjpg")
            assert live.status == 200 and live.content_type == "image/svg+xml" and "DEMO-Kamera" in await live.text()
            # The browser loads pictures without X-Lang: the page puts its language into the URL instead.
            live_en = await (await client.get("/camera/live.mjpg?t=1&lang=en")).text()
            assert "DEMO camera" in live_en and "Placeholder" in live_en
            assert re.search(r"[A-Z][a-z]{2} \d{1,2}, 20\d\d", live_en)  # month-name date in English, no 10/22/2026
            assert await (await client.get("/api/recordings")).json() == {"recordings": []}
            for action in ("record_start", "record_stop"):
                resp = await client.post("/api/cmd", json={"action": action})
                assert resp.status == 409
                assert (await resp.json())["error"] == "Die Demo nimmt nichts auf: es gibt keine echte Kamera"
            gone = await client.post("/api/cmd", json={"action": "record_delete", "name": "2026-10-22_09-34-05.mp4"},
                                     headers={"X-Lang": "en"})
            assert gone.status == 409 and (await gone.json())["error"].startswith("The demo records nothing")
            assert (await client.get("/recordings/2026-10-22_09-34-05.mp4")).status == 404
        finally:
            await client.close()

    asyncio.run(run())


def test_demo_photos_are_placeholders_too() -> None:
    async def run() -> None:
        _, app, vision = demo_parts()
        client = await client_for(app)
        try:
            names = (await (await client.get("/api/photos")).json())["photos"]
            assert len(names) == 5 and names[-1] == date.today().isoformat() and names == sorted(names)
            photo = await client.get(f"/photos/{names[0]}.jpg")
            assert photo.status == 200 and photo.content_type == "image/svg+xml"
            assert "DEMO-Kamera" in await photo.text() and "Demo-Tagesfoto" in await photo.text()
            english = await (await client.get(f"/photos/{names[0]}.jpg?lang=en")).text()
            assert "DEMO camera" in english and "Demo daily photo" in english
            latest = vision.latest()
            assert (await client.get(f"/photos/{latest['photo']}")).status == 200
            assert (await client.get("/photos/2020-01-01.jpg")).status == 404
            assert (await client.get("/photos/..%2Fkey.jpg")).status == 404
        finally:
            await client.close()

    asyncio.run(run())


def test_demo_assessment_runs_without_a_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(demo_mod, "DEMO_RUN_SECONDS", 0)

    async def run() -> None:
        _, app, vision = demo_parts()
        before = vision.latest()["date"]
        client = await client_for(app)
        try:
            resp = await client.post("/api/cmd", json={"action": "plant_analyze"}, headers={"X-Lang": "en"})
            assert await resp.json() == {"ok": True, "started": True, "model": "demo"}
            record = await vision.job
            assert record["lang"] == "en" and record["result"]["stage_reason"].startswith("Demo assessment")
            state = await (await client.get("/api/state", headers={"X-Lang": "en"})).json()
        finally:
            await client.close()
        assert vision.manual_runs_today() == 1 and state["vision"]["latest"]["date"] >= before
        assert state["vision"]["latest"]["date"][:10] == date.today().isoformat()

    asyncio.run(run())


def test_demo_values_never_raise_an_alarm() -> None:
    """The demo runs the real alert check: its made-up values must stay inside every rule all day."""
    box, camera = DemoBox(), DemoCamera()
    rules = config.alert_rules()["rules"]
    start = time.time()
    for hour in range(0, 24 * 4):
        monkey_time = start + hour * 900
        snap = alert_snapshot(box, camera)
        snap["climate"] = box._climate(monkey_time)
        for name, rule in rules.items():
            assert breached(rule, snap) in (False, None), (name, hour, snap["climate"])


def test_demo_pushes_say_demo() -> None:
    class Resp:
        status = 200

        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
            return False

    class Session:
        def __init__(self) -> None:
            self.titles: list[str] = []

        def post(self, url, data, headers, timeout):  # type: ignore[no-untyped-def]
            self.titles.append(headers["Title"])
            return Resp()

    session = Session()
    alerts = Alerts(session)  # type: ignore[arg-type]
    alerts.target = ("https://ntfy.example", "t")
    alerts.demo = True
    assert asyncio.run(alerts.push("Zu warm", "x")) is True
    alerts.demo = False
    asyncio.run(alerts.push("Zu warm", "x"))
    assert session.titles == ["VGrow DEMO: Zu warm", "VGrow: Zu warm"]


def test_serve_gives_the_demo_its_own_state_folder(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        task = asyncio.create_task(serve(DemoBox(), port=0, host="127.0.0.1"))
        demo_dir = isolated_state / "demo"
        for _ in range(100):
            if (demo_dir / "access-key").exists() or task.done():
                break
            await asyncio.sleep(0.05)
        try:
            assert not task.done(), task.exception() if task.done() else None
            assert config.STATE_DIR == demo_dir
            heights = json.loads((demo_dir / "plant-log.json").read_text(encoding="utf-8"))["heights"]
            assert [h["cm"] for h in heights] == [29, 37, 43]
            lines = (demo_dir / "automation-log.jsonl").read_text(encoding="utf-8").splitlines()
            # Five seeded lines, then the first poll: the demo's stage is only the estimate, so the automation
            # pauses and switches the demo humidifier off once (without a push: the demo seeds that as sent). That
            # off is the newest line of the page's timeline, not the seeded "on at 44.6 %" before it. Before it the
            # port guard notes once that nobody has named the device on USB 2 yet (it holds only an "on").
            assert len(lines) == 7 and json.loads(lines[-1])["action"] == "stage_pause_off"
            assert json.loads(lines[-2]) | {"time": None} == {"time": None, "action": "paused_port", "value": None,
                                                              "reason": "unassigned"}
            assert json.loads((demo_dir / "automation-state.json").read_text(encoding="utf-8"))["enabled"] is True
            assert (demo_dir / "alerts-state.json").exists()  # the first demo poll ran the real alert check
            assert not (isolated_state / "plant-log.json").exists() and not (isolated_state / "access-key").exists()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())
