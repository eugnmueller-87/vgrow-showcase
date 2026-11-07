"""Hank WP9: built into the server as a guest (spec 10.4 WP9, 0.3 rule 2, 4.9, 7, 9.1; addendum N10).

The cockpit must run exactly as before with Hank, without Hank, and with a Hank that breaks: the same /api/state,
the same /api/cmd answers, the same poll chain. Hank only adds his own routes and a journal line after a command.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, i18n, server, vision
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation
from vgrow.demo import DemoBox, setup_demo
from vgrow.hank import api as hank_api
from vgrow.hank import core, ollama, stores, wiring
from vgrow.hank.types import Proposal
from vgrow.plant import PlantLog
from vgrow.server import COOKIE, make_app, poll_handler

REPO = Path(__file__).resolve().parent.parent
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
WP9_FILES = [REPO / "vgrow" / name for name in ("server.py", "vision.py", "i18n.py")] + [
    REPO / "tests" / "test_vgrow.py", REPO / "README.md", Path(__file__)]


async def client_for(app: Any) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: "k"})
    return client


def demo_hank(box: Any, **parts: Any) -> core.Hank:
    """What build_hank() makes for the demo: no Ollama, the read-only collector over the cockpit's objects."""
    return core.Hank(live=wiring.Collector(box, **parts), demo=True)


def light_card(card_id: str = "p-test", level: int = 50) -> Proposal:
    return Proposal(id=card_id, kind="light", body={"action": "light", "level": level}, label="Licht 50 %",
                    reason="request", expires=time.time() + 1800, two_taps=False)


def key_tree(value: Any) -> Any:
    """The shape of a JSON value: dict keys all the way down, lists by their first element."""
    if isinstance(value, dict):
        return {k: key_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [key_tree(value[0])] if value else []
    return type(value).__name__ if value is not None else None


# ---------- the page files ----------

def test_page_files_include_hank(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for name in ("hank.js", "hank-i18n.js", "hank.css"):
        assert name in server.PAGE_FILES
    assert server.PAGE_FILES[:4] == ("index.html", "app.js", "style.css", "i18n.js")  # the old ones stay first
    web = tmp_path / "web"
    web.mkdir()
    for number, name in enumerate(server.PAGE_FILES):
        (web / name).write_text("x", encoding="utf-8")
        os.utime(web / name, (1_000_000 + number, 1_000_000 + number))
    monkeypatch.setattr(server, "WEB", web)
    os.utime(web / "hank.css", (2_000_000, 2_000_000))  # a change only in Hank's style sheet
    assert server.web_version() == "2000000"  # reloads open pages too
    (web / "hank.js").unlink()  # a missing file never breaks the version
    assert server.web_version() == "2000000"


# ---------- signatures and the poll chain ----------

def test_make_app_signature_is_backward_compatible() -> None:
    params = list(inspect.signature(make_app).parameters.values())
    assert [p.name for p in params] == ["box", "alerts", "key", "camera", "plant_log", "vision", "automation",
                                        "hank", "measurements", "autopilot", "pump_history", "watering", "extra_cameras", "light_schedule",
                                        "thirst", "pest", "soil", "fan_stick", "grow_plan"]  # the Durst-Index (26.10.2026), the pest watch (31.10.2026), the soil sensor and the E6, the grow plan's stick (06.11.2026)
    assert params[-1].default is None  # spec 10.1 rule 5: new parameters go last, with a default

    async def go() -> None:
        box, alerts, log = DemoBox(), Alerts(), PlantLog()
        automation = HumidifierAutomation(box, alerts, log)
        camera, vis = setup_demo(log, automation)
        # positional, as about twenty test sites call it: no Hank, no Hank routes
        client = await client_for(make_app(box, alerts, "k", camera, log, vis, automation))
        try:
            assert (await client.get("/api/state")).status == 200
            assert (await client.get("/api/hank/state")).status == 404
        finally:
            await client.close()

    asyncio.run(go())


def test_poll_handler_steps_unchanged() -> None:
    assert list(inspect.signature(poll_handler).parameters) == ["alerts", "automation", "parts", "plant_log"]
    assert "hank" not in inspect.getsource(poll_handler).lower()  # spec 0.3 rule 2: nothing of Hank hangs there
    calls: list[str] = []

    class Step:
        def __init__(self, name: str) -> None:
            self.name = name

        async def check(self, snap: dict[str, Any]) -> None:
            calls.append(self.name)

    asyncio.run(poll_handler(Step("alerts"), Step("automation"), {"camera": None})(DemoBox()))  # type: ignore[arg-type]
    assert calls == ["alerts", "automation"]


# ---------- the /api/cmd wrapper ----------

def test_cmd_wrapper_records_origin_human_and_hank() -> None:
    async def go() -> None:
        box = DemoBox()
        hank = demo_hank(box)
        hank.registry.add([light_card()])
        client = await client_for(make_app(box, Alerts(), "k", hank=hank))
        try:
            resp = await client.post("/api/cmd", json={"action": "light", "level": 50, "proposal": "p-test"})
            assert resp.status == 200 and await resp.json() == {"ok": True}
            assert box.light["level"] == 50  # the command ran as the cockpit's own, the extra key ignored
            assert hank.registry.get("p-test") is None  # a tapped card is used up
            resp = await client.post("/api/cmd", json={"action": "dfan", "step": 3})
            assert resp.status == 200 and box.dfan_step == 3
            # a card id Hank no longer holds (expired, or a restart) is checked again and held back: the card's
            # rules were checked on another moment (core.precheck_command, review 2, S2); the slider still runs
            gone = await client.post("/api/cmd", json={"action": "light", "level": 75, "proposal": "p-gone"})
            assert gone.status == 409 and box.light["level"] == 50
            # commands that switch nothing are not journaled
            await client.post("/api/cmd", json={"action": "refresh"})
        finally:
            await client.close()
        lines = stores.Journal().all()
        assert [(e["action"], e["origin"], e["proposal"], e["ok"]) for e in lines] == [
            ("light", "hank_accepted", "p-test", True), ("dfan", "human", None, True)]
        assert all("proposal" not in (e["body"] or {}) for e in lines)  # the body is the command's own
        assert (config.STATE_DIR / "hank" / "actions.jsonl").exists()

    asyncio.run(go())


def test_cmd_wrapper_never_breaks_a_command(locked_ports) -> None:  # type: ignore[no-untyped-def]
    # a locked port from tests/fixtures/ports-locked.json: the real ports.json locks none since 23.10.2026
    bodies: list[Any] = [
        {"action": "light", "level": 40},  # goes through
        {"action": "port", "port": "usb1", "on": False},  # refused: the locked USB 1 never switches off (409)
        {"action": "light"},  # a bad request (400)
        {"action": "nonsense"},  # unknown (400)
    ]

    async def answers(hank: Any) -> list[tuple[int, Any]]:
        box = DemoBox()
        box.ports["usb1"] = True  # on, as the lock holds it (the demo's pump on USB 1 starts off)
        client = await client_for(make_app(box, Alerts(), "k", hank=hank))
        out: list[tuple[int, Any]] = []
        try:
            for body in bodies:
                resp = await client.post("/api/cmd", json=body)
                out.append((resp.status, await resp.json()))
            resp = await client.post("/api/cmd", data="no json", headers={"Content-Type": "application/json"})
            out.append((resp.status, None))
        finally:
            await client.close()
        assert box.light["level"] == 40 and box.ports["usb1"] is True
        return out

    class Exploding:
        """A Hank whose journal breaks on every line."""

        def record_command(self, body: Any, status: int) -> None:
            raise RuntimeError("journal broken")

    without = asyncio.run(answers(None))
    assert [status for status, _ in without] == [200, 409, 400, 400, 500]
    assert asyncio.run(answers(demo_hank(DemoBox()))) == without
    assert asyncio.run(answers(Exploding())) == without
    assert asyncio.run(answers(object())) == without  # not even a Hank: the wrapper still never breaks a command


def test_make_app_without_hank_routes_when_register_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def broken(app: Any, hank: Any) -> None:
        raise RuntimeError("register broken")

    monkeypatch.setattr(hank_api, "register", broken)

    async def go() -> None:
        box = DemoBox()
        hank = demo_hank(box)
        client = await client_for(make_app(box, Alerts(), "k", hank=hank))
        try:
            assert (await client.get("/api/state")).status == 200
            assert (await client.get("/api/hank/state")).status == 404
            resp = await client.post("/api/cmd", json={"action": "light", "level": 50})
            assert resp.status == 200 and box.light["level"] == 50
        finally:
            await client.close()
        assert stores.Journal().all() == []  # the unwrapped command: no journal line

    asyncio.run(go())


# ---------- the photo reading in the lane ----------

def test_vision_lane_wraps_chat_and_default_keep_alive_is_zero(tmp_path) -> None:  # type: ignore[no-untyped-def]
    photo = tmp_path / "2026-10-22.jpg"
    photo.write_bytes(b"\xff\xd8 fake jpeg")
    vis = vision.Vision(camera=None)
    assert vis.lane is None and vis.keep_alive_hint is None
    sent: list[dict[str, Any]] = []
    holders: list[str | None] = []

    async def chat(body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        holders.append(vis.lane.holder if vis.lane is not None else None)
        await asyncio.sleep(0)
        return {"model": body["model"], "message": {"role": "assistant", "content": "{}"}}

    vis._chat = chat  # type: ignore[method-assign]
    asyncio.run(vis.analyze(photo, {}, None))  # no lane: as before
    assert holders == [None] and sent[-1]["keep_alive"] == 0

    lane = ollama.ModelLane()
    vis.lane = lane
    asyncio.run(vis.analyze(photo, {}, None))
    assert holders[-1] == "photo" and lane.holder is None  # held around the model call, released after

    async def behind_a_chat() -> None:
        async with lane.hold("chat"):
            reading = asyncio.create_task(vis.analyze(photo, {}, None))
            await asyncio.sleep(0.05)
            assert not reading.done() and lane.waiting == ("photo",) and len(sent) == 2
        await reading
        assert len(sent) == 3 and holders[-1] == "photo"

    asyncio.run(behind_a_chat())
    assert all(body["keep_alive"] == 0 for body in sent)

    vis.keep_alive_hint = lambda: "10m"  # Hank in use: keep the model warm
    asyncio.run(vis.analyze(photo, {}, None))
    assert sent[-1]["keep_alive"] == "10m"
    for bad in (lambda: 1 / 0, lambda: None, lambda: -1, lambda: True, lambda: "forever"):
        vis.keep_alive_hint = bad
        asyncio.run(vis.analyze(photo, {}, None))
        assert sent[-1]["keep_alive"] == 0
    assert vision.Vision(camera=None).keep_alive() == 0  # a new reader starts at 0 again


def test_vision_and_hank_share_one_model_name(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert vision.MODEL == ollama.DEFAULT_CHAT_MODEL == "gemma4:12b"
    assert "163 s and 238 s per reading (gemma3:12b)" in " ".join((vision.__doc__ or "").split())  # pinned
    env = tmp_path / "vgrow.env"
    monkeypatch.setattr(config, "ENV_FILE", env)
    for text, model in [("", "gemma4:12b"), ("VISION_MODEL=gemma3:12b\n", "gemma3:12b"),
                        ("VISION_MODEL=gemma3:12b\nHANK_MODEL=small:4b\n", "small:4b")]:
        env.write_text(text, encoding="utf-8")
        assert vision.Vision(camera=None).model == ollama.model_name() == model
    env.write_text("OLLAMA_URL=http://127.0.0.1:9999/\n", encoding="utf-8")
    assert vision.Vision(camera=None).url == ollama.ollama_url() == "http://127.0.0.1:9999"
    env.unlink()
    assert vision.Vision(camera=None).url == vision.OLLAMA_URL


# ---------- settings.json ----------

def test_settings_write_is_atomic_and_keeps_other_keys(isolated_state: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    path = isolated_state / "settings.json"
    isolated_state.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lang": "de", "hank_mode": "warnings", "other": 1}), encoding="utf-8")
    writes: list[Path] = []
    real = config.atomic_write

    def spy(target: Path, text: str) -> None:
        writes.append(target)
        real(target, text)

    monkeypatch.setattr(config, "atomic_write", spy)
    assert i18n.set_server_lang("en") == "en"
    assert writes == [path]
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n")
    assert json.loads(raw) == {"lang": "en", "hank_mode": "warnings", "other": 1}
    assert core.read_mode() == "warnings"  # the language switch never resets Hank's mode
    assert core.write_mode("off") == "off" and i18n.server_lang() == "en"  # nor the other way round

    def crash(src: Any, dst: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(config.os, "replace", crash)
    with pytest.raises(OSError):
        i18n.set_server_lang("de")
    assert json.loads(path.read_text(encoding="utf-8")) == {"lang": "en", "hank_mode": "off", "other": 1}
    assert sorted(p.name for p in isolated_state.iterdir()) == ["settings.json"]  # no half file, no temp file left


# ---------- serve() ----------

@contextlib.asynccontextmanager
async def serving(monkeypatch: pytest.MonkeyPatch, box: Any) -> AsyncIterator[tuple[aiohttp.ClientSession, str]]:
    """serve(DemoBox) on a free port; yields a session with the key cookie and the base URL."""
    socks: list[Any] = []
    real_bind = server.bind_socket

    def bind(host: str, port: int) -> Any:
        socks.append(real_bind(host, port))
        return socks[-1]

    monkeypatch.setattr(server, "bind_socket", bind)
    task = asyncio.create_task(server.serve(box, port=0, host="127.0.0.1"))
    try:
        for _ in range(200):
            if socks or task.done():
                break
            await asyncio.sleep(0.02)
        assert socks and not task.done(), task.exception() if task.done() else None
        base = f"http://127.0.0.1:{socks[0].getsockname()[1]}"
        key = config.access_key_path().read_text(encoding="utf-8")
        async with aiohttp.ClientSession(cookies={COOKIE: key}) as session:
            for _ in range(200):
                assert not task.done(), task.exception() if task.done() else None
                with contextlib.suppress(aiohttp.ClientError):
                    async with session.get(f"{base}/api/state") as resp:
                        if resp.status == 200:
                            break
                await asyncio.sleep(0.05)
            yield session, base
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_serve_wires_hank_as_a_guest(isolated_state, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    built: list[Any] = []
    real_build = server.build_hank

    def build(*args: Any, **kwargs: Any) -> Any:
        built.append(real_build(*args, **kwargs))
        built.append(kwargs)
        return built[0]

    monkeypatch.setattr(server, "build_hank", build)
    box = DemoBox()

    async def go() -> None:
        async with serving(monkeypatch, box) as (session, base):
            hank, kwargs = built
            assert isinstance(hank, core.Hank) and hank.demo and hank.client is None  # the demo runs without Ollama
            assert hank.task is not None and not hank.task.done()  # the ticker runs, with its handle
            assert kwargs["vision"].lane is hank.lane  # the photo reading shares the lane
            assert len(box.listeners) == 1  # only the cockpit's poll handler
            async with session.get(f"{base}/api/hank/state") as resp:
                assert resp.status == 200 and (await resp.json())["mode"] == "on"
            async with session.post(f"{base}/api/cmd", json={"action": "dfan", "step": 2}) as resp:
                assert resp.status == 200
            # the ticker may already have taken the demo automation's own lines in (addendum N4): those aside
            assert [(e["action"], e["origin"]) for e in stores.Journal().all()
                    if e["origin"] != "automation"] == [("dfan", "human")]
        assert hank.task is None  # stopped with the server

    asyncio.run(go())


@pytest.mark.parametrize("where", ["construction", "start", "import"])
def test_serve_survives_hank_construction_error(isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
                                                where: str) -> None:
    if where == "construction":
        def broken(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Hank broken")

        monkeypatch.setattr(core, "Hank", broken)
    elif where == "start":
        async def no_start(self: Any) -> Any:
            raise RuntimeError("ticker broken")

        monkeypatch.setattr(core.Hank, "start", no_start)
    else:
        monkeypatch.setattr(wiring, "Collector", None)  # calling it fails inside build_hank, like a broken import
    box = DemoBox()

    async def go() -> None:
        async with serving(monkeypatch, box) as (session, base):
            async with session.get(f"{base}/api/state") as resp:
                state = await resp.json()
                assert resp.status == 200 and state["demo"] is True and "hank" not in state
            async with session.post(f"{base}/api/cmd", json={"action": "light", "level": 50}) as resp:
                assert resp.status == 200 and box.light["level"] == 50
            async with session.get(f"{base}/api/hank/state") as resp:
                # without Hank there are no Hank routes; with a Hank whose ticker failed, the routes still answer
                assert resp.status == (200 if where == "start" else 404)
            assert len(box.listeners) == 1

    asyncio.run(go())


def test_demo_state_unchanged_by_hank() -> None:
    async def state_of(with_hank: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        box, alerts, log = DemoBox(), Alerts(), PlantLog()
        alerts.demo = True
        automation = HumidifierAutomation(box, alerts, log)
        camera, vis = setup_demo(log, automation)
        hank = None
        if with_hank:
            hank = demo_hank(box, camera=camera, plant_log=log, automation=automation, alerts=alerts, vision=vis)
            hank.ensure_index()
            await hank.tick()  # a full minute of Hank before the page asks
        before = {"light": dict(box.light), "ports": dict(box.ports), "cfan": box.cfan_step, "dfan": box.dfan_step}
        client = await client_for(make_app(box, alerts, "k", camera, log, vis, automation, hank))
        try:
            state = await (await client.get("/api/state")).json()
            if hank is not None:
                assert (await client.get("/api/hank/state")).status == 200
                await hank.tick()
        finally:
            await client.close()
        after = {"light": dict(box.light), "ports": dict(box.ports), "cfan": box.cfan_step, "dfan": box.dfan_step}
        assert after == before  # Hank switched nothing
        return state, after

    plain, plain_box = asyncio.run(state_of(False))
    guest, guest_box = asyncio.run(state_of(True))
    assert "hank" not in guest and key_tree(guest) == key_tree(plain)  # spec 9.1: no key hank in /api/state
    assert plain_box == guest_box
    # what comes from the box and the camera is the same value for value (climate and plant follow the clock)
    for part in ("ports", "light", "cfan", "dfan", "camera", "online", "error"):
        assert json.dumps(guest[part], sort_keys=True) == json.dumps(plain[part], sort_keys=True), part


# ---------- texts ----------

def test_wp9_files_have_no_em_dash_and_no_bom() -> None:
    for path in WP9_FILES:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path.name
        assert EM_DASH not in raw.decode("utf-8"), path.name
