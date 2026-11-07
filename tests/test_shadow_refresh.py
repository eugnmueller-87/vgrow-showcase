"""Device status must be requested every ten minutes without actuating hardware or inventing freshness."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from vgrow import box as box_mod
from vgrow.box import POLL_SECONDS, SHADOW_REFRESH_SECONDS, Box
from vgrow.vivosun.const import TOPIC_SHADOW_GET
from vgrow.vivosun.models import DeviceInfo

DEVICE = DeviceInfo("dev1", "vivosun-VSCTLY18-acct-dev1", "vivosun/VS_COMMON/VSCTLY18/acct/dev1",
                    "VGrow", True, 1, "controller")


class StopLoop(Exception):
    pass


class Transport:
    is_connected = True

    def __init__(self):
        self.requests = []
        self.fail = None

    async def publish(self, topic, payload, qos=0):
        self.requests.append((topic, payload, qos))
        if self.fail:
            raise self.fail

    async def publish_shadow_update(self, *args, **kwargs):
        raise AssertionError("Refreshing status must never publish a control command")


def make():
    box = Box("x@example.test", "unused", remember=False)
    box.device = DEVICE
    transport = Transport()
    box._mqtt = transport
    return box, transport


def test_refresh_requests_every_ten_minutes_without_sensor_polls_or_writes():
    box, transport = make()
    waits, request_counts = [], []

    async def sleep(seconds):
        waits.append(seconds)
        request_counts.append(len(transport.requests))
        if len(waits) == 4:
            raise StopLoop

    box._sleep = sleep
    with pytest.raises(StopLoop):
        asyncio.run(box._shadow_loop())
    assert POLL_SECONDS == 90
    assert SHADOW_REFRESH_SECONDS == 240  # below the 300 s the pump accepts, or it refuses half the time
    assert waits == [240, 240, 240, 240]
    assert request_counts == [0, 1, 2, 3]  # wait first; the connection already made the initial request
    assert transport.requests == [(TOPIC_SHADOW_GET.format(thing=DEVICE.client_id), b"{}", 0)] * 3
    assert box.pending == {} and box.shadow_at is None


def test_only_an_actual_report_updates_the_status_age():
    box, transport = make()
    old = time.time() - 1800
    box.shadow_at = old
    box.reported = {"usb1": {"lv": 0, "mode": 0, "inPlan": 0}}
    asyncio.run(box.refresh_shadow())
    assert len(transport.requests) == 1
    assert box.shadow_at == old and box.snapshot()["shadow_age"] >= 1800
    box._on_message("shadow/get/accepted", json.dumps({"state": {"desired": {"usb1": {"lv": 100}}}}).encode(), 0)
    assert box.shadow_at == old
    before = time.time()
    box._on_message("shadow/get/accepted", json.dumps({"state": {"reported": box.reported}}).encode(), 0)
    assert box.shadow_at >= before
    assert box.snapshot()["shadow_age"] <= 1
    assert box.snapshot()["ports"]["usb1"]["on"] is False


def test_disconnect_skips_refresh_without_resetting_age_and_recovers_next_time():
    box, transport = make()
    box.shadow_at = time.time() - 1200
    old = box.shadow_at
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        transport.is_connected = len(waits) == 2
        if len(waits) == 3:
            raise StopLoop

    box._sleep = sleep
    with pytest.raises(StopLoop):
        asyncio.run(box._shadow_loop())
    assert len(transport.requests) == 1 and waits == [240, 240, 240]
    assert box.shadow_at == old


def test_failed_refresh_does_not_stop_future_refreshes_or_trigger_login():
    box, transport = make()
    transport.fail = OSError("temporary network failure")
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            transport.fail = None
        if len(waits) == 3:
            raise StopLoop

    box._sleep = sleep
    with pytest.raises(StopLoop):
        asyncio.run(box._shadow_loop())
    assert len(transport.requests) == 2
    assert box.shadow_at is None and box._tasks == []
    assert not box._gate.path().exists()


def test_hanging_publish_times_out_and_the_next_scheduled_attempt_runs(monkeypatch):
    box, transport = make()
    monkeypatch.setattr(box_mod, "SHADOW_REFRESH_TIMEOUT_SECONDS", .01)
    attempts, cancellations, waits = [], [], []

    async def publish(topic, payload):
        attempts.append(topic)
        if len(attempts) == 1:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations.append(True)
                raise

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            raise StopLoop

    transport.publish = publish
    box._sleep = sleep
    with pytest.raises(StopLoop):
        asyncio.run(box._shadow_loop())
    assert len(attempts) == 2 and cancellations == [True]
    assert box.shadow_at is None


def test_refresh_without_a_known_device_or_transport_does_nothing():
    box, transport = make()
    box.device = None
    asyncio.run(box.refresh_shadow())
    box.device = DEVICE
    box._mqtt = None
    asyncio.run(box.refresh_shadow())
    assert not transport.requests and box.shadow_at is None


def test_shutdown_cancels_the_refresh_wait_without_a_last_request():
    async def run():
        box, transport = make()
        waiting = asyncio.Event()

        async def sleep(seconds):
            assert seconds == 240
            waiting.set()
            await asyncio.Event().wait()

        box._sleep = sleep
        task = asyncio.create_task(box._shadow_loop())
        await waiting.wait()
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not transport.requests

    asyncio.run(run())
