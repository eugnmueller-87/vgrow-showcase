"""A height estimate keeps the ruler-camera provenance and does not imply an automatic measurement."""
import asyncio
from datetime import date

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import server, workspace as ws
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox, DemoCamera
from vgrow.plant import PlantLog


def test_legacy_height_preserves_estimate_source_and_exact_replacement_clears_it():
    log = PlantLog()
    log.add_height(61, "2026-10-23")
    log.add_height(62, "2026-10-26", estimated=True, camera="growcam")
    assert log.data["heights"][-1] == {"cm": 62, "date": "2026-10-26", "estimated": True,
                                       "camera": "growcam", "method": "user_ruler_estimate"}
    assert PlantLog().data["heights"] == log.data["heights"]
    log.add_height(63, "2026-10-26")
    assert log.data["heights"] == [{"cm": 61, "date": "2026-10-23"}, {"cm": 63, "date": "2026-10-26"}]


@pytest.mark.parametrize("values", [{"estimated": "yes"}, {"camera": "usb", "estimated": True},
                                   {"camera": "growcam", "estimated": False}])
def test_bad_height_provenance_does_not_modify_the_log(values):
    log = PlantLog()
    log.add_height(61)
    before = log.path.read_bytes()
    with pytest.raises(ValueError):
        log.add_height(62, **values)
    assert log.path.read_bytes() == before


def test_workspace_import_record_history_and_replacement_preserve_the_estimate(monkeypatch):
    monkeypatch.setattr(ws, "today", lambda: date(2026, 10, 26))
    log = PlantLog()
    log.add_height(62, "2026-10-26", estimated=True, camera="growcam")
    profile = {"plant": {"strain": "Basil", "start_date": "2026-09-01"}, "stages": []}
    ws.initialize("Box", profile, log)
    expected = {"day": "2026-10-26", "cm": 62, "estimated": True, "camera": "growcam", "method": "user_ruler_estimate"}
    assert ws.view()["plants"][0]["heights"] == [expected]
    assert ws.record(ws.NATIVE_PLANT)["record"]["heights"] == [expected]
    ws.mutate({"revision": ws.view()["revision"], "action": "height", "plant": ws.NATIVE_PLANT,
               "day": "2026-10-26", "cm": 62.5, "estimated": True, "camera": "growcam"})
    assert ws.view()["plants"][0]["heights"][0]["estimated"] is True
    assert ws.record(ws.NATIVE_PLANT)["record"]["heights"][0]["method"] == "user_ruler_estimate"
    ws.mutate({"revision": ws.view()["revision"], "action": "height", "plant": ws.NATIVE_PLANT,
               "day": "2026-10-26", "cm": 63})
    assert ws.view()["plants"][0]["heights"] == [{"day": "2026-10-26", "cm": 63}]
    assert log.data["heights"][-1]["cm"] == 62  # separate records never silently alter the controller profile


def test_height_reference_is_growcam_and_cannot_silently_fall_back_to_hd(monkeypatch):
    box, log = DemoBox(), PlantLog()
    async def forbidden(*_):
        raise AssertionError("Height records cannot issue actuator commands")
    box.set_light = box.set_port = forbidden
    monkeypatch.setattr(server, "load_profile", lambda: None)
    async def go():
        for cam, expected in ((DemoCamera(), "growcam"), (None, None)):
            async with TestClient(TestServer(server.make_app(box, Alerts(), "k", cam, plant_log=log,
                                           extra_cameras={"usb": DemoCamera()}))) as client:
                client.session.cookie_jar.update_cookies({server.COOKIE: "k"})
                state = await (await client.get("/api/state")).json()
                assert state["camera_roles"] == {"height": expected, "detail": "usb"}
                assert next(c for c in state["cameras"] if c["id"] == "usb")["role"] == "detail"
                response = await client.post("/api/cmd", json={"action": "plant_height", "cm": 62,
                    "estimated": True, "camera": "growcam"})
                assert response.status == 200 and log.data["heights"][-1]["estimated"] is True
                response = await client.post("/api/cmd", json={"action": "plant_height", "cm": 62,
                    "estimated": True, "camera": "usb"})
                assert response.status == 400
    asyncio.run(go())
