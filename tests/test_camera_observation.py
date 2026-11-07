"""Fresh, source-labelled photo observations must never modify a plant record or an actuator."""
import asyncio
import base64
import json
from datetime import date

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, vision
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox, DemoCamera
from vgrow.server import COOKIE, make_app


class Camera(DemoCamera):
    def __init__(self, name, picture):
        self.name, self.picture_bytes, self.calls = name, picture, 0

    async def photo(self):
        self.calls += 1
        return self.picture_bytes

    def status(self):
        return {**super().status(), "resolution": "1920x1080"}


def result():
    return {"stage_guess": "mid_flower", "confidence": "medium", "stage_reason": "Visible flowers",
            "visible_signs": ["Developed flowers"], "issues": [], "next_steps": [],
            "not_assessable": ["Exact stage start date", "Whole plant outside the crop"]}


def test_hd_source_and_bytes_are_preserved_without_replacing_the_daily_photo():
    cam = Camera("HD", b"fresh-hd-jpeg")
    other = Camera("GrowCam", b"older-jpeg")
    vis = vision.Vision(other)
    vision.PHOTOS.mkdir(parents=True)
    original = vision.PHOTOS / f"{date.today().isoformat()}.jpg"
    original.write_bytes(b"daily-original")
    calls = []
    async def chat(body):
        calls.append(body)
        return {"message": {"content": json.dumps(result())}}
    vis._chat = chat
    before = list(config.STATE_DIR.glob("plant*"))
    record = asyncio.run(vis.run({}, None, "en", camera=cam,
        source={"id": "usb", "name": "HD", "resolution": "1920x1080"}, observation_only=True))
    assert cam.calls == 1 and other.calls == 0
    assert original.read_bytes() == b"daily-original"
    assert record["photo"] != original.name and vision.PHOTO_NAME.fullmatch(record["photo"])
    assert vision.Vision.photo_file(record["photo"]).read_bytes() == b"fresh-hd-jpeg"
    assert record["camera_source"] == {"id": "usb", "name": "HD", "resolution": "1920x1080"}
    assert record["observation_only"] is True and record["result"]["next_steps"] == []
    assert base64.b64decode(calls[0]["messages"][1]["images"][0]) == b"fresh-hd-jpeg"
    assert calls[0]["messages"][0]["content"] == vision.OBSERVATION_SYSTEM["en"]
    assert "harvest" not in calls[0]["format"]["properties"]["stage_guess"]["enum"]
    assert list(config.STATE_DIR.glob("plant*")) == before
    assert vis.photo_names() == [date.today().isoformat()]


def test_observation_does_not_use_old_stage_or_climate_to_bias_the_image():
    vis = vision.Vision(Camera("HD", b"hd"))
    sent = []
    async def chat(body):
        sent.append(body)
        bad = {**result(), "next_steps": ["unrequested management advice"]}
        return {"message": {"content": json.dumps(bad)}}
    vis._chat = chat
    record = asyncio.run(vis.run({"climate": {"probe_temp": 99}}, {"stage": "old-stage"}, "en",
        source={"id": "usb", "name": "HD"}, observation_only=True))
    assert "old-stage" not in record["context"] and "99" not in record["context"]
    assert record["result"]["next_steps"] == []


def test_an_unavailable_selected_camera_never_silently_uses_another_camera():
    class Dead(Camera):
        async def photo(self):
            raise OSError("unavailable")
    original = Camera("GrowCam", b"growcam")
    vis = vision.Vision(original)
    record = asyncio.run(vis.run({}, None, "en", camera=Dead("HD", b""), source={"id": "usb"}, observation_only=True))
    assert record["result"] is None and record["photo"] is None and original.calls == 0


def test_observation_route_uses_the_selected_camera_and_sends_no_device_commands():
    box, growcam, hd = DemoBox(), Camera("GrowCam", b"growcam"), Camera("HD", b"hd")
    vis = vision.Vision(growcam)
    calls = []
    async def run(*args, **kwargs):
        calls.append(kwargs)
        return {"result": result()}
    vis.run = run
    async def forbidden(*_):
        raise AssertionError("Observation must not control hardware")
    box.set_light = box.set_port = forbidden
    async def go():
        async with TestClient(TestServer(make_app(box, Alerts(), "k", growcam, vision=vis, extra_cameras={"usb": hd}))) as client:
            assert (await client.post("/api/cmd", json={"action": "plant_observe", "camera": "usb"})).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            response = await client.post("/api/cmd", json={"action": "plant_observe", "camera": "usb"})
            assert response.status == 200
            await vis.job
            assert calls[0]["camera"] is hd and calls[0]["source"]["id"] == "usb"
            assert calls[0]["observation_only"] is True
            response = await client.post("/api/cmd", json={"action": "plant_observe", "camera": "missing"})
            assert response.status == 404 and len(calls) == 1
    asyncio.run(go())


def test_photo_source_ids_cannot_create_arbitrary_paths():
    vis = vision.Vision(Camera("HD", b"hd"))
    with pytest.raises(ValueError):
        asyncio.run(vis.take_photo(source={"id": "../escape"}))
