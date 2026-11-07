"""GrowCam C4: still pictures, a live MJPEG view and recordings, all over the LAN via ffmpeg.

Seen on the author's camera (22.10.2026): main stream HEVC 2560x1440, sub stream HEVC 800x448,
both 15 fps, audio G.711. Credentials come from the Vivosun account (setting.jf devUser/devPass,
upstream api.py), never from a file. Recordings are video only: the camera has a microphone, and
recording other people's private speech is a criminal offence (Section 201 StGB) wherever the file lives.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import web

from .i18n import TextError, t

_LOGGER = logging.getLogger(__name__)

DVRIP_PORT = 34567
RTSP_PORT = 554
REC_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.mp4$")
LIVE_MAX_SECONDS = 12 * 3600  # live stays on while the page is open (the camera has to stay on the entire time)
MAX_LIVE_VIEWERS = 3
MIN_FREE_BYTES = 5 * 1024**3


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def stream_url(ip: str, user: str, password: str, *, sub: bool) -> str:
    """XMEye-style RTSP path; both streams verified on the camera."""
    return (
        f"rtsp://{ip}:{RTSP_PORT}/user={quote(user, safe='')}&password={quote(password, safe='')}"
        f"&channel=1&stream={1 if sub else 0}.sdp?real_stream"
    )


def record_args(ffmpeg: str, url: str, out: Path, max_seconds: int) -> list[str]:
    # -an: no audio, see module docstring. -c:v copy: no re-encoding. hvc1: Safari plays HEVC in MP4.
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
            "-an", "-c:v", "copy", "-tag:v", "hvc1", "-t", str(max_seconds), "-movflags", "+faststart", str(out)]


def live_args(ffmpeg: str, url: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
            "-an", "-r", "5", "-q:v", "6", "-f", "mpjpeg", "pipe:1"]


def snapshot_args(ffmpeg: str, url: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
            "-an", "-frames:v", "1", "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"]


def port_open(ip: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


async def find_camera_ip(own_ip: str) -> str | None:
    """Scan the local /24 for a host that answers on both camera ports."""
    prefix = own_ip.rsplit(".", 1)[0]

    async def probe(host: str) -> str | None:
        ok = await asyncio.to_thread(port_open, host, DVRIP_PORT)
        return host if ok and await asyncio.to_thread(port_open, host, RTSP_PORT) else None

    hits = await asyncio.gather(*(probe(f"{prefix}.{i}") for i in range(1, 255)))
    return next((h for h in hits if h), None)


class Camera:
    content_type = "image/jpeg"  # what snapshot() returns

    def __init__(self, rec_dir: Path, max_record_minutes: int = 120) -> None:
        self.rec_dir = rec_dir
        self.max_record_seconds = max_record_minutes * 60
        self.ffmpeg = ffmpeg_exe()
        self.ip: str | None = None
        self.name: str | None = None
        self._user: str | None = None
        self._password: str | None = None
        self._snap: tuple[float, bytes] | None = None
        self._snap_lock = asyncio.Lock()
        self._live = 0
        self._rec: asyncio.subprocess.Process | None = None
        self._rec_file: Path | None = None
        self._rec_started: float | None = None
        self._error: tuple[str, dict[str, Any]] | None = None  # catalog key and values, read in any language
        self._own_ip = "127.0.0.1"
        self.search_task: asyncio.Task[None] | None = None
        self.watch_task: asyncio.Task[None] | None = None
        self.reachable: bool | None = None  # None until the first check
        self.last_seen: float | None = None
        self.offline_since: float | None = None
        self._search_since: float | None = None  # in the account, but not found on the network since then

    @property
    def error(self) -> str | None:
        return None if self._error is None else t(self._error[0], **self._error[1])

    @property
    def configured(self) -> bool:
        return bool(self.ffmpeg and self.ip and self._user and self._password)

    @property
    def searching(self) -> bool:
        """In the account with credentials, but no address yet: keep_searching() goes on scanning.
        The camera alarm counts this as offline (a camera without power looks exactly like this at boot)."""
        return bool(self.ffmpeg and self._user and self._password and not self.ip)

    async def configure(self, device: Any, ip_hint: str | None, own_ip: str) -> None:
        if device is None or not device.camera_username or not device.camera_password:
            self._error = ("camera.no_account", {})
            return
        if not self.ffmpeg:
            self._error = ("camera.no_ffmpeg", {})
            return
        self.name, self._user, self._password = device.name, device.camera_username, device.camera_password
        self._own_ip = own_ip
        if ip_hint:
            # A fixed address is trusted even if the camera is briefly off the 2.4 GHz WiFi (seen on
            # 22.10.2026: "No route to host" for a minute, then back); stills just fail until it returns.
            self.ip, self._error = ip_hint, None
        else:
            await self.rescan()
        _LOGGER.info("GrowCam %s at %s", self.name, self.ip)

    async def rescan(self) -> None:
        self.ip = await find_camera_ip(self._own_ip)
        if self.ip:
            self._error, self._search_since = None, None
        else:
            self._error = ("camera.not_found", {})
            self._search_since = self._search_since or time.time()

    async def watch(self) -> None:
        """Every 30 s: is the camera on the network? Seen 22.10.2026: with its power on USB 1, switching USB 1
        off took the camera offline within seconds. Since 23.10.2026 it needs its own USB power supply (its address
        GROWCAM_IP), the drip pump hangs on USB 1 and the humidifier on USB 2 (ports.json)."""
        while True:
            if self.ip:
                ok = await asyncio.to_thread(port_open, self.ip, DVRIP_PORT, 2.0)
                now = time.time()
                if ok:
                    self.reachable, self.last_seen, self.offline_since = True, now, None
                else:
                    if self.reachable is not False:
                        self.offline_since = now
                    self.reachable = False
            await asyncio.sleep(30)

    async def keep_searching(self) -> None:
        """Without a fixed address, retry the network scan every minute until the camera shows up."""
        while self.ip is None and self._user:
            await asyncio.sleep(60)
            await self.rescan()

    def _url(self, *, sub: bool) -> str:
        assert self.ip and self._user and self._password
        return stream_url(self.ip, self._user, self._password, sub=sub)

    # ---------- pictures ----------

    async def snapshot(self) -> bytes:
        async with self._snap_lock:  # several open pages share one ffmpeg run
            if self._snap and time.time() - self._snap[0] < 5:
                return self._snap[1]
            proc = await asyncio.create_subprocess_exec(
                *snapshot_args(self.ffmpeg, self._url(sub=True)),  # type: ignore[arg-type]
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
            except TimeoutError:
                proc.kill()
                raise TextError("camera.no_answer") from None
            if proc.returncode != 0 or not out:
                raise RuntimeError(err.decode(errors="replace").replace(self._password or "", "***")[-200:])
            self._snap = (time.time(), out)
            return out

    async def photo(self) -> bytes:
        """One full-resolution frame (2560x1440) from the main stream, for the daily plant photo."""
        proc = await asyncio.create_subprocess_exec(
            *snapshot_args(self.ffmpeg, self._url(sub=False)),  # type: ignore[arg-type]
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            raise TextError("camera.no_answer") from None  # stored by the daily photo, said in the reader's language
        if proc.returncode != 0 or not out:
            raise RuntimeError(err.decode(errors="replace").replace(self._password or "", "***")[-200:])
        return out

    async def live(self, request: web.Request) -> web.StreamResponse:
        if self._live >= MAX_LIVE_VIEWERS:
            raise web.HTTPTooManyRequests(text=t("camera.too_many_viewers"))
        proc = await asyncio.create_subprocess_exec(
            *live_args(self.ffmpeg, self._url(sub=True)),  # type: ignore[arg-type]
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        resp = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace;boundary=ffmpeg", "Cache-Control": "no-store",
        })
        await resp.prepare(request)
        self._live += 1
        started = time.time()
        try:
            assert proc.stdout is not None
            while time.time() - started < LIVE_MAX_SECONDS:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # the page switched back to stills or closed
        finally:
            self._live -= 1
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        return resp

    # ---------- recordings ----------

    async def start_recording(self) -> str:
        if self._rec is not None and self._rec.returncode is None:
            raise ValueError(t("camera.recording_running"))
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.rec_dir).free < MIN_FREE_BYTES:
            raise ValueError(t("camera.low_disk", gb=MIN_FREE_BYTES // 1024**3))
        out = self.rec_dir / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.mp4"
        self._rec = await asyncio.create_subprocess_exec(
            *record_args(self.ffmpeg, self._url(sub=False), out, self.max_record_seconds),  # type: ignore[arg-type]
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        self._rec_file, self._rec_started = out, time.time()
        _LOGGER.info("Recording to %s", out.name)
        return out.name

    async def stop_recording(self) -> str | None:
        proc, name = self._rec, self._rec_file.name if self._rec_file else None
        if proc is None:
            return None
        if proc.returncode is None and proc.stdin is not None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.write(b"q")  # ffmpeg finishes the MP4 cleanly on "q"
                await proc.stdin.drain()
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except TimeoutError:
                proc.terminate()
                await proc.wait()
        self._rec, self._rec_file, self._rec_started = None, None, None
        return name

    def recording(self) -> dict[str, Any] | None:
        if self._rec is None:
            return None
        if self._rec.returncode is not None:  # hit the time limit or the camera dropped: the file stays
            _LOGGER.info("Recording %s ended on its own (rc %s)", self._rec_file, self._rec.returncode)
            self._rec, self._rec_file, self._rec_started = None, None, None
            return None
        return {"file": self._rec_file.name if self._rec_file else None,
                "seconds": round(time.time() - (self._rec_started or time.time())), "ended": False}

    def recordings(self) -> list[dict[str, Any]]:
        if not self.rec_dir.exists():
            return []
        active = self._rec_file.name if self._rec_file and self._rec and self._rec.returncode is None else None
        out = []
        for path in sorted(self.rec_dir.glob("*.mp4"), reverse=True):
            if not REC_NAME.match(path.name):
                continue
            out.append({"name": path.name, "mb": round(path.stat().st_size / 1024**2, 1), "active": path.name == active})
        return out

    def path_for(self, name: str) -> Path | None:
        if not REC_NAME.match(name):
            return None
        path = self.rec_dir / name
        return path if path.exists() else None

    def delete(self, name: str) -> None:
        path = self.path_for(name)
        if path is None:
            raise ValueError(t("camera.recording_missing"))
        if self._rec_file is not None and path.name == self._rec_file.name and self._rec and self._rec.returncode is None:
            raise ValueError(t("camera.stop_first"))
        os.remove(path)

    def status(self) -> dict[str, Any]:
        searching = self.searching
        since = self._search_since if searching else self.offline_since
        offline_for = None if since is None else round(time.time() - since)
        return {"configured": self.configured, "searching": searching, "name": self.name, "ip": self.ip,
                "error": self.error, "reachable": self.reachable, "offline_seconds": offline_for,
                "recording": self.recording(), "max_minutes": self.max_record_seconds // 60}

    async def close(self) -> None:
        await self.stop_recording()
