"""The access key: never empty or short, written atomically, and kept out of every log."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import stat
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app, serve, startup_banner

REPO = Path(__file__).resolve().parent.parent


async def raw_requests(app: web.Application, payloads: list[bytes]) -> list[bytes]:
    """Bytes on a real socket, as curl or a broken client sends them (the test client would clean them up first).
    Returns each response's status line."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    lines = []
    try:
        for data in payloads:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(data)
            await writer.drain()
            lines.append((await asyncio.wait_for(reader.read(300), 5)).split(b"\r\n")[0])
            writer.close()
    finally:
        await runner.cleanup()
    return lines


def test_a_missing_empty_or_short_key_file_gets_a_new_key(isolated_state) -> None:  # type: ignore[no-untyped-def]
    path = config.access_key_path()
    first = config.access_key()  # no file yet
    assert len(first) >= config.ACCESS_KEY_MIN and path.read_text(encoding="utf-8") == first
    for content in ("", "  \n", "short-key-15chr"):
        path.write_text(content, encoding="utf-8")
        key = config.access_key()
        assert len(key) >= config.ACCESS_KEY_MIN and key != content.strip(), repr(content)
        assert path.read_text(encoding="utf-8") == key
    assert config.access_key() == key  # a key that is long enough stays: the phone's link keeps working
    path.write_text(f"  {key}\n", encoding="utf-8")
    assert config.access_key() == key  # surrounding whitespace is not part of it
    assert not [p.name for p in path.parent.iterdir() if p.name.startswith(".access-key.")]  # no temp file left
    if os.name == "posix":
        path.unlink()
        config.access_key()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600  # owner-only


def test_after_an_empty_key_file_the_guard_still_wants_the_key(isolated_state) -> None:  # type: ignore[no-untyped-def]
    path = config.access_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")  # the bug: an empty key matched an empty or missing cookie
    key = config.access_key()

    async def run() -> None:
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), key))) as client:
            assert (await client.get("/api/state")).status == 403  # no cookie
            client.session.cookie_jar.update_cookies({COOKIE: ""})
            assert (await client.get("/api/state")).status == 403  # an empty cookie
            assert (await client.get("/?key=", allow_redirects=False)).status == 403
            assert (await client.get("/?key=%C3%A4", allow_redirects=False)).status == 403  # not a 500
            ok = await client.get(f"/?key={key}", allow_redirects=False)
            assert ok.status == 302 and COOKIE in ok.cookies
            client.session.cookie_jar.update_cookies({COOKIE: key})
            assert (await client.get("/api/state")).status == 200
        # Even a server that somehow got an empty key lets nobody in.
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), ""))) as client:
            assert (await client.get("/api/state")).status == 403
            assert (await client.get("/?key=", allow_redirects=False)).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: ""})
            assert (await client.get("/api/state")).status == 403

    asyncio.run(run())


def test_a_link_from_another_page_gets_in(isolated_state) -> None:  # type: ignore[no-untyped-def]
    """Owner, 07.11.2026, on the key page his Leitstand opened: "all these applications have to work". The cookie is
    Lax, so a link from another page brings it along. A Strict cookie from before stays home on such a link: the key
    page reloads itself once, the reload is the cockpit's own and brings it, and the page renews it as Lax."""
    key = config.access_key()
    page_accept = {"Accept": "text/html,application/xhtml+xml"}

    async def run() -> None:
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), key))) as client:
            ok = await client.get(f"/?key={key}", allow_redirects=False)
            assert "SameSite=Lax" in ok.headers["Set-Cookie"] and "HttpOnly" in ok.headers["Set-Cookie"]
            client.session.cookie_jar.clear()
            page = await client.get("/", headers=page_accept)
            assert page.status == 403 and page.content_type == "text/html"
            assert '<meta http-equiv="refresh" content="0;url=/?again=1">' in await page.text()
            again = await client.get("/?again=1", headers=page_accept)
            assert again.status == 403 and again.content_type == "text/plain"  # the second miss: the text, never a loop
            data = await client.get("/api/state", headers={"Accept": "application/json"})
            assert data.status == 403 and data.content_type == "text/plain"  # a fetch gets no page
            client.session.cookie_jar.update_cookies({COOKIE: key})
            back = await client.get("/?again=1", headers=page_accept, allow_redirects=False)
            assert back.status == 302 and back.headers["Location"] == "/"  # in, and the address loses its marker
            home = await client.get("/", headers=page_accept)
            assert home.status == 200 and "SameSite=Lax" in home.headers.get("Set-Cookie", "")
            state = await client.get("/api/state")
            assert state.status == 200 and "Set-Cookie" not in state.headers  # only the page renews it

    asyncio.run(run())


def test_the_banner_shows_the_key_only_in_a_terminal(isolated_state) -> None:  # type: ignore[no-untyped-def]
    key = config.access_key()
    hidden = startup_banner(key, 8742, lan="10.0.0.5", ntfy="an", camera="keine im Konto", tty=False)
    assert key not in hidden and config.redact_key(hidden) == hidden  # nothing in it to black out
    assert "http://10.0.0.5:8742/" in hidden and str(config.access_key_path()) in hidden
    assert hidden.isascii()
    terminal = startup_banner(key, 8742, lan="10.0.0.5", ntfy="an", camera="keine im Konto", tty=True)
    assert f"http://10.0.0.5:8742/?key={key}" in terminal and f"http://localhost:8742/?key={key}" in terminal


def test_serve_prints_no_key_when_stdout_is_not_a_terminal(isolated_state, capsys) -> None:  # type: ignore[no-untyped-def]
    async def run() -> str:
        task = asyncio.create_task(serve(DemoBox(), port=0, host="127.0.0.1"))
        out = ""
        try:
            for _ in range(200):
                out += capsys.readouterr().out
                if "VGrow Cockpit" in out or task.done():
                    break
                await asyncio.sleep(0.05)
            assert not task.done(), task.exception() if task.done() else None
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return out

    out = asyncio.run(run())  # pytest captures stdout: not a terminal, like launchd's server.log
    key = (isolated_state / "demo" / "access-key").read_text(encoding="utf-8")
    assert "VGrow Cockpit" in out and key not in out and "access-key" in out


def test_log_lines_never_carry_the_key() -> None:
    record = logging.LogRecord("aiohttp.access", logging.INFO, __file__, 1, '%s "GET /?key=%s HTTP/1.1" 302',
                               ("10.0.0.7", "AbC_123-xyz"), None)
    assert config.RedactKey().filter(record) is True  # it rewrites, it never drops a line
    assert record.getMessage() == '10.0.0.7 "GET /?key=<redacted> HTTP/1.1" 302'
    assert config.redact_key("key=<redacted>") == "key=<redacted>"  # twice is the same as once
    assert config.redact_key("http://x/?key=&lang=en") == "http://x/?key=&lang=en"  # an empty value stays empty
    untouched = logging.LogRecord("vgrow", logging.INFO, __file__, 1, "Found %s", ("VGrow",), None)
    assert config.RedactKey().filter(untouched) and untouched.args == ("VGrow",)


def test_a_malformed_request_line_never_writes_the_key_into_the_log() -> None:
    """The review's probe: aiohttp's "Error handling request" quotes the raw request line in its traceback, which the
    filter never saw. Logging is set up as __main__ does it, on a real socket."""
    key = "AbC_123-xyz-0123456789ab"
    out = io.StringIO()
    handler = config.protect_handler(logging.StreamHandler(out))
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        lines = asyncio.run(raw_requests(make_app(DemoBox(), Alerts(), key), [
            f"GET /?key={key} HTTP/9.x\r\nHost: x\r\n\r\n".encode(),
            f"GET /?key={key} x HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
            f"GET /?key={key}&lang=".encode() + "ä".encode() + b" HTTP/1.1\r\nHost: x\r\n\r\n",  # a typed umlaut
            f"GET /?key={key} HTTP/1.1\r\nHost: x\r\n\r\n".encode()]))
    finally:
        root.removeHandler(handler)
        root.setLevel(level)
    text = out.getvalue()
    assert [line.split(b" ")[1] for line in lines] == [b"400", b"400", b"400", b"302"]
    assert text.count("Error handling request") == 3 and "Traceback" in text  # the tracebacks are there
    assert key not in text and "key=<redacted>" in text  # and cleaned, the access line included


def test_a_cookie_with_raw_bytes_is_a_403_not_a_500(caplog) -> None:  # type: ignore[no-untyped-def]
    lines = asyncio.run(raw_requests(make_app(DemoBox(), Alerts(), "k" * 24), [
        b"GET /api/state HTTP/1.1\r\nHost: x\r\nCookie: vgrow_key=\xff\xfe\r\n\r\n",
        b"GET /api/state HTTP/1.1\r\nHost: x\r\nCookie: vgrow_key=\xed\xa0\x80\r\n\r\n"]))
    assert lines == [b"HTTP/1.1 403 Forbidden"] * 2
    assert "UnicodeEncodeError" not in caplog.text and "Error handling request" not in caplog.text


def test_install_script_blacks_out_old_keys_while_the_server_is_stopped() -> None:
    script = (REPO / "scripts" / "install_mac.sh").read_text(encoding="utf-8")
    step = 'sed -E "s/key=[^[:space:]&\\"\'<>]+/key=<redacted>/g" "$LOG" > "$LOG.redacting"'
    assert step in script and 'if [ -f "$LOG" ]; then' in script  # nothing to do without a log
    assert script.index("launchctl bootout") < script.index(step) < script.index("launchctl bootstrap")
    assert "\r" not in script
