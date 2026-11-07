"""The soil sensor (vgrow/soil.py): the gateway's answer, a silent gateway, the log, setting it to the soil, the target,
the Durst-Index hook, /api/state. The device check (vgrow/device_check.py) at the end."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, device_check
from vgrow.alerts import Alerts
from vgrow.box import CommandRefused
from vgrow.demo import DemoBox
from vgrow.server import make_app
from vgrow.soil import STALE_S, DemoSoil, Reading, SoilSensor, parse_livedata, parse_room, start_soil, targets

# The owner's gateway on 06.11.2026, word for word apart from the whitespace
OWNER_ANSWER = {
    "common_list": [],
    "wh25": [{"intemp": "22.1", "unit": "C", "inhumi": "49%", "abs": "1021.0 hPa", "rel": "1021.0 hPa"}],
    "ch_soil": [{"channel": "1", "name": "", "battery": "5", "voltage": "1.50", "humidity": "13%"}],
    "debug": [{"heap": "104088", "runtime": "440", "usr_interval": "--"}],
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def run_with_gateway(handler: Any, check: Any) -> None:
    async def main() -> None:
        app = web.Application()
        app.router.add_get("/get_livedata_info", handler)
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession() as session:
                await check(session, f"{server.host}:{server.port}")
        finally:
            await server.close()
    asyncio.run(main())


def sensor_at(percent: float, clock: Clock | None = None) -> SoilSensor:
    """A sensor with a fresh reading and no session: for everything after the gateway's answer."""
    sensor = SoilSensor(None, "127.0.0.1:9", clock=clock or Clock())  # type: ignore[arg-type]
    sensor.readings = [Reading(1, percent, 1.5)]
    sensor.heard_at = sensor.clock()
    return sensor


def test_the_owners_answer() -> None:
    assert parse_livedata(OWNER_ANSWER) == [Reading(1, 13.0, 1.5, "")]
    assert parse_room(OWNER_ANSWER) == (22.1, 49.0)


def test_channels_sorted_and_unusable_rows_left_out() -> None:
    rows = [{"channel": "3", "humidity": "40%"}, {"channel": "2", "humidity": "--"}, {"channel": "x", "humidity": "5%"},
            {"channel": "4", "humidity": "140%"}, "junk", {"channel": "1", "humidity": "0%", "voltage": "--"}]
    assert parse_livedata({"ch_soil": rows}) == [Reading(1, 0.0, None, ""), Reading(3, 40.0, None, "")]


def test_answers_without_soil_channels() -> None:
    for data in ({}, {"ch_soil": None}, {"ch_soil": "x"}, [], None, "x"):
        assert parse_livedata(data) == []
        assert parse_room(data) == (None, None)


def test_poll_reads_the_gateway_even_with_a_wrong_content_type() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text=json.dumps(OWNER_ANSWER), content_type="text/html")

    async def check(session: aiohttp.ClientSession, ip: str) -> None:
        sensor = SoilSensor(session, ip, clock=Clock())
        heard = []
        sensor.on_reading = heard.append
        await sensor.poll_once()
        view = sensor.view()
        assert (view["percent"], view["channel"], view["voltage"], view["fresh"]) == (13.0, 1, 1.5, True)
        assert (view["room_temp"], view["room_humidity"]) == (22.1, 49.0)
        assert view["calibrated"] is False and view["state"] is None and view["relative"] is None
        assert heard == [view]

    run_with_gateway(handler, check)


def test_a_silent_gateway_stops_showing_the_number() -> None:
    answers = [web.Response(text=json.dumps(OWNER_ANSWER)), web.Response(status=500), web.Response(text="not json")]

    async def handler(_: web.Request) -> web.Response:
        return answers.pop(0)

    async def check(session: aiohttp.ClientSession, ip: str) -> None:
        clock = Clock()
        sensor = SoilSensor(session, ip, clock=clock)
        await sensor.poll_once()
        clock.now += 60
        await sensor.poll_once()  # 500
        await sensor.poll_once()  # not json
        assert sensor.view()["percent"] == 13.0 and sensor.view()["age"] == 60  # a minute late is still the reading
        clock.now += STALE_S
        assert sensor.view()["percent"] is None and sensor.view()["fresh"] is False

    run_with_gateway(handler, check)


def test_an_unreachable_gateway_never_raises() -> None:
    async def main() -> None:
        async with aiohttp.ClientSession() as session:
            sensor = SoilSensor(session, "127.0.0.1:9")
            await sensor.poll_once()
            assert sensor.view()["percent"] is None and sensor.view()["age"] is None

    asyncio.run(main())


def test_a_broken_listener_costs_nothing_but_itself() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text=json.dumps(OWNER_ANSWER))

    async def check(session: aiohttp.ClientSession, ip: str) -> None:
        sensor = SoilSensor(session, ip)

        def boom(_view: dict[str, Any]) -> None:
            raise RuntimeError("boom")

        sensor.on_reading = boom
        await sensor.poll_once()
        assert sensor.view()["percent"] == 13.0

    run_with_gateway(handler, check)


def test_the_log_keeps_real_changes_and_a_line_every_quarter_hour() -> None:
    sensor = sensor_at(13.0)
    for at, percent in ((0, 13.0), (60, 14.0), (120, 13.0), (180, 16.0), (180 + 899, 16.0), (180 + 900, 16.0)):
        sensor.readings = [Reading(1, percent, 1.5)]
        sensor._log(1000.0 + at)
    lines = [json.loads(line) for line in sensor.log_path().read_text(encoding="utf-8").splitlines()]
    # the wobble of one point (14, 13) waits; three points at once, and the quarter hour, are written
    assert [(line["at"] - 1000, line["percent"]) for line in lines] == [(0, 13.0), (180, 16.0), (1080, 16.0)]


def test_history_filters_by_time_and_channel_and_skips_broken_lines() -> None:
    clock = Clock()
    clock.now = 10_000.0
    sensor = sensor_at(20.0, clock)
    sensor.folder().mkdir(parents=True)
    rows = [{"at": 1000.0, "channel": 1, "percent": 10.0}, {"at": 9000.0, "channel": 1, "percent": 20.0},
            {"at": 9100.0, "channel": 2, "percent": 90.0}]
    sensor.log_path().write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    assert sensor.history(1) == [{"at": 9000.0, "percent": 20.0}]
    assert SoilSensor(None, "x", clock=clock).history(1) != []  # type: ignore[arg-type]


def test_no_log_is_an_empty_history() -> None:
    assert sensor_at(10.0).history(24) == []


def test_the_target_follows_the_watering_model() -> None:
    assert targets("veg") == (73, 105)  # half of 30 points below 55: 40 of 55
    assert targets("seedling") == (78, 105)  # the seedling's 0.4: 43 of 55
    assert targets(None) == (73, 105)


def test_setting_it_to_the_soil_gives_the_scale_and_the_state() -> None:
    clock = Clock()
    sensor = sensor_at(50.0, clock)
    assert sensor.calibrate({"point": "wet"})["ok"] is True
    view = sensor.view()
    assert (view["calibrated"], view["wet_percent"], view["relative"], view["trigger"], view["too_wet"], view["state"]) \
        == (True, 50.0, 100.0, 73, 105, "ok")
    for percent, relative, state in ((36.0, 72.0, "low"), (37.0, 74.0, "ok"), (53.0, 106.0, "high")):
        sensor.readings = [Reading(1, percent)]
        assert (sensor.view()["relative"], sensor.view()["state"]) == (relative, state)
    assert sensor.calibrate({"reset": True})["soil"]["calibrated"] is False


def test_the_optional_dry_point_moves_the_zero() -> None:
    sensor = sensor_at(10.0)
    sensor.calibrate({"point": "dry"})
    sensor.readings = [Reading(1, 50.0)]
    sensor.calibrate({"point": "wet"})
    sensor.readings = [Reading(1, 30.0)]
    assert sensor.view()["relative"] == 50.0
    sensor.readings = [Reading(1, 5.0)]
    assert sensor.view()["relative"] == 0.0  # never below the dry point


def test_setting_refuses_what_cannot_be_a_scale() -> None:
    sensor = sensor_at(8.0)
    with pytest.raises(CommandRefused):  # dry air is 0: a pot at 8 cannot be the top of a scale
        sensor.calibrate({"point": "wet"})
    with pytest.raises(ValueError):
        sensor.calibrate({"point": "soggy"})
    silent = SoilSensor(None, "x")  # type: ignore[arg-type]
    with pytest.raises(CommandRefused):
        silent.calibrate({"point": "wet"})


def test_a_broken_calibration_file_counts_as_not_set() -> None:
    sensor = sensor_at(40.0)
    sensor.folder().mkdir(parents=True)
    sensor.calibration_path().write_text("not json", encoding="utf-8")
    assert sensor.view()["calibrated"] is False


def test_no_address_no_sensor_and_the_demo_has_its_own() -> None:
    async def main() -> None:
        async with aiohttp.ClientSession() as session:
            assert start_soil(session, demo=False) is None
            assert isinstance(start_soil(session, demo=True), DemoSoil)

    asyncio.run(main())


def test_the_address_comes_from_the_env_file() -> None:
    config.ENV_FILE.write_text("ECOWITT_IP=192.168.1.91\n", encoding="utf-8")
    assert config.ecowitt_ip() == "192.168.1.91"


def state_of(soil: Any = None, **kwargs: Any) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        app = make_app(DemoBox(), Alerts(), "k" * 32, soil=soil, **kwargs)
        async with TestClient(TestServer(app)) as client:
            await client.get("/?key=" + "k" * 32, allow_redirects=False)
            response = await client.get("/api/state")
            assert response.status == 200
            return await response.json()

    return asyncio.run(main())


def test_api_state_carries_the_reading_only_with_a_sensor() -> None:
    assert "soil" not in state_of(None)
    assert state_of(DemoSoil())["soil"]["percent"] == 42.0


def test_a_broken_sensor_costs_its_tile_never_the_page() -> None:
    class Broken:
        def view(self) -> dict[str, Any]:
            raise RuntimeError("boom")

    assert "soil" not in state_of(Broken())


def test_history_route_and_calibrate_command() -> None:
    async def main() -> None:
        app = make_app(DemoBox(), Alerts(), "k" * 32, soil=DemoSoil())
        async with TestClient(TestServer(app)) as client:
            await client.get("/?key=" + "k" * 32, allow_redirects=False)
            rows = await (await client.get("/api/soil/history?hours=24")).json()
            assert len(rows["rows"]) == 96
            assert (await client.get("/api/soil/history?hours=x")).status == 400
            answer = await client.post("/api/cmd", json={"action": "soil_calibrate", "point": "wet"})
            assert answer.status == 200 and (await answer.json())["soil"]["calibrated"] is True

    asyncio.run(main())


def test_calibrate_without_a_sensor_says_so() -> None:
    async def main() -> None:
        app = make_app(DemoBox(), Alerts(), "k" * 32)
        async with TestClient(TestServer(app)) as client:
            await client.get("/?key=" + "k" * 32, allow_redirects=False)
            assert (await client.post("/api/cmd", json={"action": "soil_calibrate", "point": "wet"})).status == 409
            assert (await (await client.get("/api/soil/history")).json()) == {"rows": []}

    asyncio.run(main())


# ---------- the device check ----------

def test_device_check_records_and_the_owner_decides_the_effect() -> None:
    device_check.record({"device": "dfan", "command": True, "seconds": 2.1}, now=5.0)
    data = device_check.load()
    assert data["dfan"]["command"] is True and data["dfan"]["effect"] is None and data["dfan"]["at"] == 5.0
    device_check.confirm({"device": "dfan", "ok": False}, now=6.0)
    assert device_check.load()["dfan"] == {**data["dfan"], "effect": False, "owner": True, "owner_at": 6.0}
    device_check.record({"device": "humidifier", "command": True, "effect": True, "before": 43.8, "after": 56.5, "unit": "%"})
    row = device_check.load()["humidifier"]
    assert (row["before"], row["after"], row["unit"]) == (43.8, 56.5, "%")


def test_device_check_refuses_what_it_does_not_know() -> None:
    for body in ({"device": "toaster"}, {"device": "light", "command": "yes"}, {"device": "light", "before": "x"}):
        with pytest.raises(ValueError):
            device_check.record(body)
    with pytest.raises(ValueError):
        device_check.confirm({"device": "light", "ok": "yes"})
    assert device_check.load() == {}


def test_device_check_reaches_the_page() -> None:
    device_check.record({"device": "light", "command": True})
    assert state_of(None)["device_check"]["light"]["command"] is True
