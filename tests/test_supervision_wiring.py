"""The supervision in the server (vgrow/server.py): the poll hands its states to the alarms before the steps and tells
it which step runs and which failed; a stuck measured day is cut and never read twice at once; the watchdog task
reports a dead or hung poll and the poll reports a dead watchdog; start_supervision never takes the server down; GET
/api/supervision and the mute commands.

Why (15.11.2026): nothing watched the poll loop, the poll steps only logged their failures, and the thirst index sat nine
days without a measurement that nothing reported.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import alerts as alerts_mod
from vgrow import config, server, supervision
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app, poll_handler, start_supervision, start_supervisor, stop_supervisor
from vgrow.supervision import Supervisor

HOUR = 3600.0
KEY = "a-long-access-key-for-this-test-0123456789"
TOPIC = "secret-topic-of-this-test"


class Pushes(Alerts):
    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[tuple[str, str, int]] = []

    async def push(self, title: str, message: str, *, priority: int = 3, click: str | None = None) -> bool:
        self.pushed.append((title, message, priority))
        return True

    def titles(self) -> list[str]:
        return [p[0] for p in self.pushed]


class Task:
    def __init__(self, name: str, *, done: bool = False) -> None:
        self.name, self._done = name, done

    def get_name(self) -> str:
        return self.name

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return False


class LoopBox(DemoBox):
    """The demo's box with the real box's supervision hooks: polled_at and loops()."""

    def __init__(self) -> None:
        super().__init__()
        self.polled_at: float | None = None
        self.tasks = {name: Task(name) for name in ("vgrow_supervise", "vgrow_poll", "vgrow_shadow_refresh")}

    def loops(self) -> dict[str, Task]:
        return dict(self.tasks)


class RealBox(LoopBox):
    """Not the demo: a real run answers 503 without a supervision."""

    demo = False

    def snapshot(self) -> dict[str, Any]:
        snap = super().snapshot()
        snap.pop("demo", None)
        return snap


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: now[0])
    return now


def supervisor(box: Any, alerts: Any, clock: list[float], parts: dict[str, Any] | None = None) -> Supervisor:
    sup = Supervisor(box, alerts, parts if parts is not None else {}, clock=lambda: clock[0],
                     tasks=lambda: [Task("hank_protocol")])
    sup.grace_until = 0.0
    return sup


class Recorder:
    def __init__(self) -> None:
        self.snaps: list[dict[str, Any]] = []

    async def check(self, snapshot: dict[str, Any]) -> list[str]:
        self.snaps.append(snapshot)
        return []


class Step:
    """A poll step that notes the step the supervision names while it runs, and fails `fail` times."""

    def __init__(self, sup: Supervisor, calls: list[str], fail: int = 0) -> None:
        self.sup, self.calls, self.fail = sup, calls, fail

    async def check(self, snap: dict[str, Any]) -> None:
        self.calls.append(self.sup.phase or "")
        if self.fail:
            self.fail -= 1
            raise KeyError("secret text")


class PollBroken(Pushes):
    """Its check raises on the poll's path only: the watchdog's narrow call still works."""

    async def check(self, snapshot: dict[str, Any], *, only: Any = None) -> list[str]:
        if only is None:
            raise RuntimeError("the poll's own check, with a secret text")
        return await super().check(snapshot, only=only)


class Automation:
    async def check(self, snap: dict[str, Any]) -> None:
        raise ValueError("x")


# ---------- the poll ----------

def test_the_poll_hands_the_supervision_to_the_alarms_before_the_steps(clock: list[float]) -> None:
    seen = Recorder()
    box = LoopBox()
    sup = supervisor(box, Pushes(), clock)
    calls: list[str] = []
    automation = Step(sup, calls, fail=2)
    handler = poll_handler(seen, automation, {"camera": None, "supervision": sup})  # type: ignore[arg-type]
    asyncio.run(handler(box))
    assert "supervision" in seen.snaps[0] and set(seen.snaps[0]["supervision"]["signals"]) == set(supervision.BY_ID)
    assert calls == ["Step.check"] and sup.steps == {"Step.check": {"n": 1, "error": "KeyError"}}
    assert sup.round_at is None and sup.phase is None  # the round ended
    asyncio.run(handler(box))
    assert sup.steps["Step.check"]["n"] == 2
    asyncio.run(handler(box))
    assert sup.steps == {}  # a step that runs again clears its count

    def broken(snap: Any) -> Any:
        raise RuntimeError("reader")

    sup.read = broken  # type: ignore[method-assign]
    asyncio.run(handler(box))
    assert sup.read_fails == 1 and sup.read_error == "RuntimeError" and len(seen.snaps) == 4  # the steps ran on
    assert "supervision" not in seen.snaps[-1]


def test_poll_steps_unchanged_without_a_supervision() -> None:
    seen = Recorder()
    asyncio.run(poll_handler(seen, None, {"camera": None})(DemoBox()))  # type: ignore[arg-type]
    assert len(seen.snaps) == 1 and "supervision" not in seen.snaps[0]


def test_a_hanging_light_day_is_cut_and_never_read_twice(monkeypatch: pytest.MonkeyPatch,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(server, "LIGHT_DAY_TIMEOUT_S", 0.05)
    release = asyncio.Event()
    reads: list[int] = []

    class Hanging:
        async def read(self) -> dict[str, Any]:
            reads.append(1)
            await release.wait()  # a stuck thread read
            return {"lit_hours": 24, "dark_hours": 0, "measured_hours": 24}

    seen = Recorder()
    handler = poll_handler(seen, None, {"camera": None, "light_day": Hanging()})  # type: ignore[arg-type]

    async def go() -> None:
        await handler(DemoBox())
        await handler(DemoBox())  # the next poll waits for the same read again, it never starts a second one
        assert reads == [1]
        release.set()
        await handler(DemoBox())  # the stuck read ends while this poll waits for it: its day reaches the alarms

    asyncio.run(go())
    assert len(seen.snaps) == 3 and "light_day" not in seen.snaps[0] and "light_day" not in seen.snaps[1]
    assert seen.snaps[2]["light_day"]["dark_hours"] == 0 and reads == [1]
    assert "Reading the measured light day failed" in caplog.text


# ---------- the watchdog ----------

def test_the_watchdog_reports_a_dead_or_hung_poll(clock: list[float]) -> None:
    alerts = Pushes()
    box = LoopBox()
    box.polled_at = clock[0]
    sup = supervisor(box, alerts, clock)
    box.tasks["vgrow_poll"] = Task("vgrow_poll", done=True)  # the loop died
    asyncio.run(sup.tick())
    clock[0] += 61
    asyncio.run(sup.tick())
    assert alerts.titles() == ["Abfrage der Box steht"]  # by the watchdog alone, after its hold
    assert "die Abfrage ist beendet" in alerts.pushed[0][1]
    # a resumed poll: the "back" push
    box.tasks["vgrow_poll"] = Task("vgrow_poll")
    box.polled_at = clock[0]
    clock[0] += 60
    asyncio.run(sup.tick())
    assert alerts.titles()[-1] == "Wieder ok" and "Abfrage der Box steht" in alerts.pushed[-1][1]
    # a live task whose round hangs in a step; the watcher ticks every minute meanwhile
    sup.poll_started()
    sup.step_started("Thirst.check")
    for _ in range(6):
        clock[0] += 60
        asyncio.run(sup.tick())
    assert alerts.titles()[-1] == "Abfrage der Box steht" and "(hängt gerade: Durst-Index)" in alerts.pushed[-1][1]
    assert "VGrow lief nicht" not in alerts.titles()  # a hung poll is no outage of the process


def test_the_watcher_survives_its_own_body_and_a_hung_check(clock: list[float],
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    class Hung(Pushes):
        async def check(self, snapshot: dict[str, Any], *, only: Any = None) -> list[str]:
            await asyncio.sleep(3600)
            return []

    monkeypatch.setattr(supervision, "CHECK_TIMEOUT_S", 0.05)
    sup = supervisor(LoopBox(), Hung(), clock)
    asyncio.run(sup.tick())  # cut by wait_for: the tick ends, alive_at is written
    assert sup.state["alive_at"] == clock[0] and (config.STATE_DIR / supervision.STATE_FILE).exists()
    ticks: list[int] = []

    async def flaky() -> None:
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("the watcher's own body")

    async def go() -> None:
        sup.tick = flaky  # type: ignore[method-assign]
        sup.sleep = lambda _s: asyncio.sleep(0)  # type: ignore[assignment]
        task = sup.start()
        assert task.get_name() == "vgrow_supervision"
        for _ in range(50):
            if len(ticks) >= 3:
                break
            await asyncio.sleep(0)
        await sup.stop()
        assert task.done()

    asyncio.run(go())
    assert len(ticks) >= 3  # it went on after the exception


def test_a_stale_watcher_makes_the_poll_report_it(clock: list[float]) -> None:
    alerts = Pushes()
    box = LoopBox()
    sup = supervisor(box, alerts, clock)
    sup.watch_beat = clock[0]
    parts = {"camera": None, "supervision": sup}
    handler = poll_handler(alerts, None, parts)
    clock[0] += 181
    box.polled_at = clock[0]
    asyncio.run(handler(box))
    clock[0] += 61
    asyncio.run(handler(box))
    assert "Aufsicht steht" in alerts.titles()


def test_supervision_failing_is_reported_by_the_watchdog_not_the_poll(clock: list[float]) -> None:
    alerts = PollBroken()
    box = LoopBox()
    sup = supervisor(box, alerts, clock)
    handler = poll_handler(alerts, None, {"camera": None, "supervision": sup})  # type: ignore[arg-type]
    for _ in range(3):
        box.polled_at = clock[0]
        asyncio.run(handler(box))
        clock[0] += 90
    assert sup.steps == {"PollBroken.check": {"n": 3, "error": "RuntimeError"}}
    asyncio.run(sup.tick())
    clock[0] += 61
    asyncio.run(sup.tick())
    title, body, _ = alerts.pushed[-1]
    assert title == "Ein Prüfschritt bricht ab"
    # a step without words of its own (this test's double) is a part of the server, never its code name or class
    assert "Ein Teil des Servers: bricht seit 3 Abfragen hintereinander ab." in body
    assert "secret" not in body and "PollBroken" not in body and "RuntimeError" not in body
    # a broken rule counts at once (a fresh alarm state: the last part's episode is persisted in alerts-state.json)
    alerts = Pushes()
    alerts.state = {}
    alerts.config = copy.deepcopy(alerts.config)
    alerts.config["rules"]["broken_rule"] = {"metric": "probe_temp", "above": 30}  # no label, no for_minutes
    sup = supervisor(box, alerts, clock)
    box.polled_at = clock[0]
    asyncio.run(alerts.check({"climate": {"probe_temp": 99.0}}))
    assert set(alerts.broken) == {"broken_rule"}
    asyncio.run(sup.tick())
    clock[0] += 61
    asyncio.run(sup.tick())
    body = alerts.pushed[-1][1]
    assert "Alarmregel nicht auswertbar: eine Regel ohne Namen." in body  # a broken entry without its own label
    assert "broken_rule" not in body and "alerts.json" not in body and "KeyError" not in body
    # a step of the poll raising three times
    alerts = Pushes()
    alerts.state = {}
    sup = supervisor(box, alerts, clock)
    handler = poll_handler(alerts, Automation(), {"camera": None, "supervision": sup})  # type: ignore[arg-type]
    for _ in range(3):
        box.polled_at = clock[0]
        asyncio.run(handler(box))
        clock[0] += 30
    asyncio.run(sup.tick())
    clock[0] += 61
    asyncio.run(sup.tick())
    assert sup.steps == {"Automation.check": {"n": 3, "error": "ValueError"}}  # the id stays for the log and the PC
    assert alerts.pushed[-1][0] == "Ein Prüfschritt bricht ab" and "Automation.check" not in alerts.pushed[-1][1]


class Pilot:
    """One box's autopilot as the hub runs it: its box id, its check, and a box of its own for any box but the first."""

    def __init__(self, box_id: str, *, fail: bool) -> None:
        self.box_id, self.fail, self.calls = box_id, fail, 0
        self.box = DemoBox()

    async def check(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls += 1
        if self.fail:
            raise KeyError("a plant.json shape, with a secret text")
        return []


def test_a_failing_autopilot_or_thirst_sample_reaches_supervision_failing(clock: list[float],
                                                                        caplog: pytest.LogCaptureFixture) -> None:
    """Review of 15.11.2026: AutopilotHub.check and Thirst.check caught their own failures, so their poll step never
    raised and supervision_failing never saw an autopilot, or an ET sample, failing on every poll. Both still finish
    their other boxes and steps, then raise the first failure once."""
    from vgrow import thirst
    from vgrow.autopilot import AutopilotHub

    first, second = Pilot("box-1", fail=True), Pilot("box-2", fail=False)
    hub = AutopilotHub([first, second])  # type: ignore[list-item]
    with pytest.raises(KeyError):
        asyncio.run(hub.check({}))
    assert first.calls == second.calls == 1  # the other box ran although the first failed
    alerts = Pushes()
    alerts.state = {}
    box = LoopBox()
    sup = supervisor(box, alerts, clock)
    handler = poll_handler(alerts, None, {"camera": None, "supervision": sup, "autopilot": hub})
    for _ in range(3):
        box.polled_at = clock[0]
        asyncio.run(handler(box))
        clock[0] += 90
    assert sup.steps == {"AutopilotHub.check": {"n": 3, "error": "KeyError"}}
    asyncio.run(sup.tick())
    clock[0] += 61
    asyncio.run(sup.tick())
    title, body, _ = alerts.pushed[-1]
    assert title == "Ein Prüfschritt bricht ab" and body.startswith(title + ". Autopilot: bricht seit 3 Abfragen")
    assert "secret" not in body and "KeyError" not in body
    assert caplog.text.count("Traceback") == 3  # one per poll, from the poll; the hub logs a line, not a second one
    # the Durst-Index: an ET sample that raises on every poll, while its ledger runs on and keeps thirst_engine green
    index = thirst.Thirst(None, demo=True, clock=lambda: clock[0])
    calls: list[str] = []
    lights = index._lights

    def sample(snap: Any, now: float) -> None:
        raise ValueError("the model, with a secret text")

    def lit(snap: Any, now: float) -> None:
        calls.append("lights")
        lights(snap, now)

    index._sample, index._lights = sample, lit  # type: ignore[method-assign]
    with pytest.raises(ValueError):
        asyncio.run(index.check(box.snapshot()))
    assert calls == ["lights"] and index.checked_at == clock[0]  # the other steps and the ledger ran, then it raised
    assert index.state_path().exists()  # and the state was saved before it
    alerts = Pushes()
    alerts.state = {}
    sup = supervisor(box, alerts, clock, {"thirst": index})
    handler = poll_handler(alerts, None, {"camera": None, "supervision": sup, "thirst": index})
    for _ in range(3):
        box.polled_at = clock[0]
        asyncio.run(handler(box))
        clock[0] += 90
    assert sup.steps == {"Thirst.check": {"n": 3, "error": "ValueError"}}
    assert sup.read({})["signals"]["thirst_engine"]["state"] == "ok"  # it cannot see this: checked_at is fresh
    asyncio.run(sup.tick())
    clock[0] += 61
    asyncio.run(sup.tick())
    assert alerts.pushed[-1][1].startswith("Ein Prüfschritt bricht ab. Durst-Index: bricht seit 3 Abfragen")


# ---------- start and stop ----------

def test_start_supervision_swallows_its_own_failure(monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    parts: dict[str, Any] = {"camera": None}
    built = start_supervision(LoopBox(), Pushes(), parts, thirst=None, hank=None, watering="w")
    assert isinstance(built, Supervisor) and parts["supervision"] is built
    assert parts["thirst"] is None and parts["hank"] is None and parts["watering"] == "w"  # None: a failed start

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("a bug in the supervision")

    monkeypatch.setattr(server, "Supervisor", boom)
    parts = {"camera": None}
    assert start_supervision(LoopBox(), Pushes(), parts) is None and "supervision" not in parts
    assert "The supervision could not be built" in caplog.text

    async def go() -> None:
        await start_supervisor(None)
        await stop_supervisor(None)
        sup = built
        await start_supervisor(sup)
        assert sup.task is not None and sup.task.get_name() == "vgrow_supervision"
        await stop_supervisor(sup)
        assert sup.task is None

    asyncio.run(go())
    saved = json.loads((config.STATE_DIR / supervision.STATE_FILE).read_text(encoding="utf-8"))
    assert saved["alive_at"] is not None  # the last sign of life at the stop: a deploy's gap counts from there


# ---------- the routes and the commands ----------

async def client_for(app: Any, *, cookie: bool = True) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    if cookie:
        client.session.cookie_jar.update_cookies({COOKIE: KEY})
    return client


def test_api_and_commands(clock: list[float]) -> None:
    alerts = Pushes()
    alerts.target = ("https://ntfy.example", TOPIC)
    box = RealBox()
    box.polled_at = clock[0]
    parts: dict[str, Any] = {"camera": None}
    sup = Supervisor(box, alerts, parts, clock=lambda: clock[0])

    async def go() -> None:
        outside = await client_for(make_app(box, alerts, KEY, supervisor=sup), cookie=False)
        try:
            assert (await outside.get("/api/supervision")).status == 403
        finally:
            await outside.close()
        client = await client_for(make_app(box, alerts, KEY, supervisor=sup))
        try:
            sup.read(box.snapshot())
            resp = await client.get("/api/supervision")
            body = await resp.text()
            data = json.loads(body)
            assert resp.status == 200
            assert {"poll", "watcher", "signals", "active", "muted", "unwatched", "process", "push"} <= set(data)
            assert KEY not in body and TOPIC not in body and data["push"]["target"] is True
            state = await (await client.get("/api/state")).json()
            assert "supervision" in state and state["supervision"]["poll"]["limit_s"] == 300
            for wrong in ({"signal": "soil_silent", "days": 3}, {"signal": "soil_silent", "days": 3, "why": " "},
                          {"signal": "soil_silent", "days": 15, "why": "weg"},
                          {"signal": "nope", "days": 3, "why": "weg"}):
                assert (await client.post("/api/cmd", json={"action": "supervision_mute", **wrong})).status == 400
            ok = await client.post("/api/cmd", json={"action": "supervision_mute", "signal": "soil_silent", "days": 3,
                                                     "why": "Gateway abgebaut"})
            assert ok.status == 200 and (await ok.json())["muted"]["why"] == "Gateway abgebaut"
            saved = json.loads((config.STATE_DIR / supervision.STATE_FILE).read_text(encoding="utf-8"))
            assert saved["mutes"]["soil_silent"]["why"] == "Gateway abgebaut"  # written at once, not at the next tick
            view = await (await client.get("/api/supervision", headers={"X-Lang": "en"})).json()
            assert [m["signal"] for m in view["muted"]] == ["soil_silent"] and view["lang"] == "en"
            back = await client.post("/api/cmd", json={"action": "supervision_unmute", "signal": "soil_silent"})
            assert back.status == 200 and (await back.json())["unmuted"] is True
        finally:
            await client.close()
        without = await client_for(make_app(box, alerts, KEY))
        try:
            assert (await without.get("/api/supervision")).status == 503  # a real run without one: the PC sees it
            refused = await without.post("/api/cmd", json={"action": "supervision_mute", "signal": "soil_silent",
                                                           "days": 3, "why": "x"})
            assert refused.status == 409
            assert "supervision" not in await (await without.get("/api/state")).json()
        finally:
            await without.close()
        demo = await client_for(make_app(DemoBox(), alerts, KEY))
        try:
            assert await (await demo.get("/api/supervision")).json() == {"demo": True}
        finally:
            await demo.close()

    asyncio.run(go())
