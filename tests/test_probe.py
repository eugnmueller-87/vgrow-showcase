"""scripts/supervision_probe.py, the Mac side of the PC's daily check (Aufsicht, 15.11.2026), on real sockets.

The key cases go through the real access guard of vgrow/server.py (make_app with the demo box); small aiohttp apps play
a server that sleeps, answers no JSON, has no supervisor or redirects, and a closed port plays a dead service. The key
must never show: not on stdout or stderr, not in the JSON, not in any argv the launchctl and pmset runner gets.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import make_app

REPO = Path(__file__).resolve().parent.parent
KEY = "probe-test-key-0123456789abcdef"  # 31 characters, over config.ACCESS_KEY_MIN
LAUNCHCTL = """{
\t"StandardOutPath" = "/Users/owner/.vgrow-control/server.log";
\t"Label" = "de.vgrow.control";
\t"OnDemand" = false;
\t"LastExitStatus" = 0;
\t"PID" = 123;
\t"Program" = "/Users/owner/vgrow-control/.venv/bin/python";
};
"""
PMSET = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=4653155)\t100%; charged; 0:00 remaining present: true\n"


def load_probe() -> Any:
    spec = importlib.util.spec_from_file_location("supervision_probe", REPO / "scripts" / "supervision_probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe_mod = load_probe()


def key_file(tmp_path: Path, key: str = KEY) -> Path:
    path = tmp_path / "access-key"
    path.write_text(f"{key}\n", encoding="utf-8")
    return path


def against(app: web.Application, call: Callable[[str], Any]) -> Any:
    """The blocking probe in a thread while the server answers on a real port of this machine."""
    async def go() -> Any:
        async with TestServer(app, host="127.0.0.1") as server:
            return await asyncio.to_thread(call, f"http://127.0.0.1:{server.port}")
    return asyncio.run(go())


def real(key: str = KEY) -> web.Application:
    return make_app(DemoBox(), Alerts(), key)


def small(handler: Callable[[web.Request], Any], path: str = "/api/supervision") -> web.Application:
    app = web.Application()
    app.router.add_get(path, handler)
    return app


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Runner:
    """The launchctl and pmset calls, canned: records every argv it gets."""

    def __init__(self, launchctl: tuple[int, str] | None = (0, LAUNCHCTL),
                 pmset: tuple[int, str] | None = (0, PMSET)) -> None:
        self.argv: list[list[str]] = []
        self.answers = {"launchctl": launchctl, "pmset": pmset}

    def __call__(self, argv: Any, timeout: float) -> tuple[int, str] | None:
        assert timeout == probe_mod.RUN_TIMEOUT_S
        self.argv.append(list(argv))
        return self.answers[argv[0]]


def test_ok_through_the_real_guard_and_wrong_key_is_forbidden(tmp_path: Path) -> None:
    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        async with TestServer(real(), host="127.0.0.1") as server:
            base = f"http://127.0.0.1:{server.port}"
            # /api/state: a route that exists in every version, behind the same guard as /api/supervision
            good = await asyncio.to_thread(probe_mod.probe, base, key_file(tmp_path), 10.0, None, path="/api/state")
            wrong = await asyncio.to_thread(probe_mod.probe, base, key_file(tmp_path / "x", "w" * 24), 10.0, None)
            return good, wrong

    (tmp_path / "x").mkdir()
    good, wrong = asyncio.run(run())
    assert good["ok"] is True and good["error"] is None and good["status"] == 200 and good["reachable"] is True
    assert isinstance(good["supervision"], dict) and "online" in good["supervision"]
    assert wrong == {**wrong, "ok": False, "error": "forbidden", "status": 403, "reachable": True,
                     "supervision": None}


def test_the_supervision_body_comes_back_whole(tmp_path: Path) -> None:
    view = {"poll": {"age_s": 41}, "watcher": {"age_s": 12}, "active": [], "muted": [], "unwatched": []}
    seen: dict[str, Any] = {}

    async def answer(request: web.Request) -> web.Response:
        seen.update(cookie=request.cookies.get("vgrow_key"), lang=request.headers.get("X-Lang"))
        return web.json_response(view)

    got = against(small(answer), lambda base: probe_mod.probe(base, key_file(tmp_path), 10.0, None, lang="en"))
    assert got["ok"] is True and got["supervision"] == view and got["status"] == 200
    assert seen == {"cookie": KEY, "lang": "en"}  # the cookie, never ?key= (that only redirects, server.guard)


def test_a_closed_port_is_refused_and_a_sleeping_server_times_out(tmp_path: Path) -> None:
    port = closed_port()
    refused = probe_mod.probe(f"http://127.0.0.1:{port}", key_file(tmp_path), 10.0, None)
    assert refused == {**refused, "ok": False, "error": "refused", "reachable": False, "status": None}

    async def asleep(_: web.Request) -> web.Response:
        await asyncio.sleep(2)  # past the probe's 0.5 s, short enough for the server to close soon after
        return web.json_response({})

    slow = against(small(asleep), lambda base: probe_mod.probe(base, key_file(tmp_path), 0.5, None))
    assert slow == {**slow, "ok": False, "error": "timeout", "reachable": False, "status": None}


def test_no_key_asks_nothing(tmp_path: Path) -> None:
    calls: list[str] = []

    async def answer(_: web.Request) -> web.Response:
        calls.append("asked")
        return web.json_response({})

    missing = against(small(answer), lambda base: probe_mod.probe(base, tmp_path / "none", 10.0, None))
    empty = tmp_path / "empty"
    empty.write_text("  \n", encoding="utf-8")
    blank = against(small(answer), lambda base: probe_mod.probe(base, empty, 10.0, None))
    for got in (missing, blank):
        assert got == {**got, "ok": False, "error": "no_key", "reachable": None, "status": None}
    assert calls == []


def test_no_json_no_supervisor_and_another_status(tmp_path: Path) -> None:
    async def text(_: web.Request) -> web.Response:
        return web.Response(text="<html>nicht JSON</html>", content_type="text/html")

    async def array(_: web.Request) -> web.Response:
        return web.json_response([1, 2])

    async def missing(_: web.Request) -> web.Response:
        return web.json_response({"error": "no supervisor"}, status=503)

    async def broken(_: web.Request) -> web.Response:
        return web.Response(status=500, text="x")

    def asked(handler: Callable[[web.Request], Any]) -> dict[str, Any]:
        return against(small(handler), lambda base: probe_mod.probe(base, key_file(tmp_path), 10.0, None))

    for handler, error, status in ((text, "bad_json", 200), (array, "bad_json", 200), (missing, "unavailable", 503),
                                   (broken, "http", 500)):
        got = asked(handler)
        assert got == {**got, "ok": False, "error": error, "status": status, "reachable": True,
                       "supervision": None}, handler.__name__


def test_a_redirect_is_an_answer_and_the_cookie_does_not_travel(tmp_path: Path) -> None:
    caught: list[Any] = []

    async def thief(request: web.Request) -> web.Response:
        caught.append(request.cookies.get("vgrow_key"))
        return web.json_response({})

    async def run() -> dict[str, Any]:
        async with TestServer(small(thief, "/steal"), host="127.0.0.1") as other:
            async def away(_: web.Request) -> web.Response:
                raise web.HTTPFound(f"http://127.0.0.1:{other.port}/steal")

            async with TestServer(small(away), host="127.0.0.1") as server:
                return await asyncio.to_thread(probe_mod.probe, f"http://127.0.0.1:{server.port}",
                                               key_file(tmp_path), 10.0, None)

    got = asyncio.run(run())
    assert got == {**got, "ok": False, "error": "http", "status": 302, "reachable": True}
    assert caught == []


def test_only_a_loopback_address_gets_the_key(tmp_path: Path) -> None:
    for base in ("http://192.168.1.20:8742", "http://example.invalid:8742", "nonsense"):  # a LAN host, a name, junk
        got = probe_mod.probe(base, key_file(tmp_path), 10.0, None)
        assert got == {**got, "ok": False, "error": "not_local", "reachable": None, "status": None}, base


def test_launchd_and_power_are_read_and_a_parse_failure_only_blanks_them(tmp_path: Path) -> None:
    port = closed_port()
    runner = Runner()
    got = probe_mod.probe(f"http://127.0.0.1:{port}", key_file(tmp_path), 10.0, runner)
    assert got["launchd"] == {"loaded": True, "pid": 123, "last_exit": 0}
    assert got["power"] == {"source": "AC Power", "percent": 100, "line": "Now drawing from 'AC Power'"}
    assert runner.argv == [["launchctl", "list", "de.vgrow.control"], ["pmset", "-g", "batt"]]
    stopped = probe_mod.probe(f"http://127.0.0.1:{port}", key_file(tmp_path), 10.0,
                              Runner(launchctl=(0, '{\n\t"LastExitStatus" = 256;\n};\n'), pmset=(0, "odd output\n")))
    assert stopped["launchd"] == {"loaded": True, "pid": None, "last_exit": 256}  # loaded, not running
    assert stopped["power"] == {"source": None, "percent": None, "line": "odd output"}
    gone = probe_mod.probe(f"http://127.0.0.1:{port}", key_file(tmp_path), 10.0,
                           Runner(launchctl=(113, ""), pmset=None))
    assert gone["launchd"] == {"loaded": False, "pid": None, "last_exit": None} and gone["power"] is None
    assert probe_mod.probe(f"http://127.0.0.1:{port}", key_file(tmp_path), 10.0, None)["launchd"] is None
    assert probe_mod.run_command(["no-such-command-for-the-probe"], 5.0) is None  # not a Mac: blank, no crash


def test_the_key_is_in_no_output_and_no_argv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = key_file(tmp_path)
    leaky = {"poll": {"age_s": 30}, "oops": f"key={KEY}"}  # a server bug that put the key into its body

    async def answer(_: web.Request) -> web.Response:
        return web.json_response(leaky)

    runners: list[Runner] = []

    def main(base: str, *extra: str) -> int:
        runners.append(Runner())
        return probe_mod.main(["--key-file", str(path), "--base", base, "--timeout", "10", *extra], run=runners[-1])

    codes = against(small(answer), lambda base: [main(base), main(base, "--lang", "en")])
    codes.append(main(f"http://127.0.0.1:{closed_port()}"))
    assert codes == [0, 0, 0]  # every outcome exits 0: the PC decides
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 3 and all(line.isascii() for line in lines)  # one line each, ASCII through any ssh
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["ok"] is True and parsed[0]["supervision"]["oops"] == "key=<redacted>"
    assert parsed[2]["error"] == "refused" and parsed[2]["launchd"]["pid"] == 123
    assert KEY not in out and KEY not in err
    assert all(KEY not in " ".join(argv) for r in runners for argv in r.argv)
    assert all(argv[0] in ("launchctl", "pmset") for r in runners for argv in r.argv)
