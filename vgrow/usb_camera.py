"""An explicitly selected macOS USB camera, shared by viewers, stills and recordings.

Never opens a microphone or falls back to the built-in camera. One capture process owns
the USB device; recording consumes its JPEG frames instead of opening the camera again.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from .camera import Camera, LIVE_MAX_SECONDS, MAX_LIVE_VIEWERS, MIN_FREE_BYTES
from .i18n import TextError, t

LOG = logging.getLogger(__name__)
MAX_FRAME = 8 * 1024**2
FRAME_FPS = 5
SEEN_RETRY = 15  # seconds between looks while macOS does not list the camera
NOT_SEEN = ('camera.usb_not_seen', {})


def capture_args(ffmpeg: str, device: str, size: str) -> list[str]:
    return [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'avfoundation',
            '-pixel_format', 'uyvy422', '-framerate', '30', '-video_size', size,
            '-i', f'{device}:none', '-an', '-r', str(FRAME_FPS), '-q:v', '3',
            '-f', 'image2pipe', '-c:v', 'mjpeg', 'pipe:1']


def listing_args(ffmpeg: str) -> list[str]:
    return [ffmpeg, '-hide_banner', '-f', 'avfoundation', '-list_devices', 'true', '-i', '']


def listed_video_devices(output: str) -> list[str] | None:
    """Video device names in ffmpeg's listing; None when it printed no listing at all."""
    names, in_video, answered = [], False, False
    for line in output.splitlines():
        if 'AVFoundation video devices:' in line:
            in_video = answered = True
        elif 'AVFoundation audio devices:' in line:
            in_video = False
        elif in_video and (match := re.search(r'\] \[\d+\] (.+)$', line)):
            names.append(match.group(1).strip())
    return names if answered else None


def encoding_args(ffmpeg: str, out: Path, seconds: int) -> list[str]:
    return [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'image2pipe',
            '-framerate', str(FRAME_FPS), '-c:v', 'mjpeg', '-i', 'pipe:0', '-an',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20', '-pix_fmt', 'yuv420p',
            '-t', str(seconds), '-movflags', '+faststart', str(out)]


def configured_camera(root: Path, rec_dir: Path) -> UsbCamera | None:
    """Opt-in owner configuration, separate from provider credentials and the GrowCam."""
    path = root / 'camera-usb.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        device, size = data['device'], data.get('size', '1920x1080')
        if (not isinstance(device, str) or not device or len(device) > 160
                or any(ord(c) < 32 for c in device) or ':' in device
                or device.casefold() in ('default', 'none') or device.isdigit()
                or size not in ('1920x1080', '1280x720', '640x480')):
            raise ValueError('Invalid USB camera configuration')
        return UsbCamera(rec_dir / 'usb', device, size, bridge_app=Path(data['bridge_app']) if data.get('bridge_app') else None)
    except (OSError, ValueError, KeyError, TypeError):
        LOG.warning('USB camera configuration is invalid; provider camera remains available')
        return None


class UsbCamera(Camera):
    def __init__(self, rec_dir: Path, device: str, size: str = '1920x1080', *, bridge_app: Path | None = None) -> None:
        super().__init__(rec_dir)
        self.name = self.device = device
        self.size = size
        self.bridge_app = bridge_app
        self._frame: bytes | None = None
        self._frame_at = 0.0
        self._sequence = 0
        self._condition = asyncio.Condition()
        self._task: asyncio.Task | None = None
        self._writer: asyncio.Task | None = None
        self._capture: asyncio.subprocess.Process | None = None
        self._record_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.ffmpeg and self.device and sys.platform == 'darwin')

    @property
    def searching(self) -> bool:
        return False

    def start(self) -> None:
        if self.configured and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run(), name='usb_camera_capture')

    async def _publish(self, frame: bytes) -> None:
        async with self._condition:
            self._frame, self._frame_at = frame, time.monotonic()
            self._sequence += 1
            self.reachable, self.last_seen, self.offline_since, self._error = True, time.time(), None, None
            self._condition.notify_all()

    def _offline(self, key: str) -> None:
        if self.reachable is not False:
            self.offline_since = time.time()
        self.reachable, self._error = False, (key, {})

    async def _seen_by_macos(self) -> bool | None:
        """False only when macOS answers and the name is missing. Opens no camera; anything unclear is None."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(*listing_args(self.ffmpeg), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, output = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (OSError, asyncio.TimeoutError):
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            return None
        names = listed_video_devices(output.decode('utf-8', 'replace'))
        return None if names is None else self.device in names

    async def _run(self) -> None:
        while True:
            if await self._seen_by_macos() is False:  # nothing to open: say so, no helper start, look again soon
                self._offline(NOT_SEEN[0])
                await asyncio.sleep(SEEN_RETRY)
                continue
            if self._error == NOT_SEEN:
                self._error = None
            proc = None
            listener, writer = None, None
            try:
                if self.bridge_app:
                    self.rec_dir.parent.mkdir(parents=True, exist_ok=True)
                    socket_path = self.rec_dir.parent / 'usb-camera.sock'
                    with contextlib.suppress(FileNotFoundError):
                        socket_path.unlink()
                    ready = asyncio.get_running_loop().create_future()
                    async def connected(reader, client):
                        if ready.done():
                            client.close()
                            return
                        ready.set_result((reader, client))
                    listener = await asyncio.start_unix_server(connected, path=socket_path)
                    os.chmod(socket_path, 0o600)
                    args = capture_args(str(self.bridge_app / 'Contents/MacOS/VGrowCamera'), self.device, self.size)[1:]
                    args[-1] = f'unix://{socket_path}'
                    proc = await asyncio.create_subprocess_exec('/usr/bin/open', '-W', '-g', '-n',
                        '-a', str(self.bridge_app), '--args', *args,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    reader, writer = await asyncio.wait_for(ready, timeout=120)
                else:
                    proc = await asyncio.create_subprocess_exec(*capture_args(self.ffmpeg, self.device, self.size),
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    reader = proc.stdout
                self._capture = proc
                buffer = bytearray()
                while True:
                    chunk = await asyncio.wait_for(reader.read(65536), timeout=15)
                    if not chunk:
                        raise OSError('USB camera stream ended')
                    buffer.extend(chunk)
                    while True:
                        start = buffer.find(b'\xff\xd8')
                        end = buffer.find(b'\xff\xd9', max(start, 0) + 2)
                        if start < 0 or end < 0:
                            break
                        frame = bytes(buffer[start:end + 2])
                        del buffer[:end + 2]
                        if len(frame) <= MAX_FRAME:
                            await self._publish(frame)
                    if len(buffer) > MAX_FRAME:
                        raise OSError('USB camera frame exceeds limit')
            except (OSError, asyncio.TimeoutError):
                self._offline('camera.usb_unavailable')
            finally:
                if writer:
                    writer.close()
                    with contextlib.suppress(ConnectionResetError, BrokenPipeError):
                        await writer.wait_closed()
                if listener:
                    listener.close()
                    await listener.wait_closed()
                    with contextlib.suppress(FileNotFoundError):
                        socket_path.unlink()
                if self.bridge_app:
                    # Only our exact installed executable; never terminate arbitrary camera apps.
                    pattern = '^' + re.escape(str(self.bridge_app / 'Contents/MacOS/VGrowCamera')) + '( |$)'
                    stop = await asyncio.create_subprocess_exec('/usr/bin/pkill', '-f', pattern,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await stop.wait()
                if proc is not None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                self._capture = None
            await asyncio.sleep(5)

    async def snapshot(self) -> bytes:
        self.start()
        if self._error == NOT_SEEN:
            raise TextError(NOT_SEEN[0])
        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait_for(
                    lambda: self._frame is not None and time.monotonic() - self._frame_at < 5), timeout=15)
                return self._frame
        except asyncio.TimeoutError:
            key, values = self._error or ('camera.usb_unavailable', {})
            raise TextError(key, **values) from None

    async def photo(self) -> bytes:
        return await self.snapshot()

    async def live(self, request: web.Request) -> web.StreamResponse:
        if self._live >= MAX_LIVE_VIEWERS:
            raise web.HTTPTooManyRequests(text=t('camera.too_many_viewers'))
        try:
            await self.snapshot()
        except TextError as err:
            raise web.HTTPBadGateway(text=err.text()) from None
        response = web.StreamResponse(headers={'Content-Type': 'multipart/x-mixed-replace;boundary=vgrow-usb',
                                               'Cache-Control': 'no-store'})
        self._live += 1
        try:
            await response.prepare(request)
            sequence, started = -1, time.monotonic()
            while time.monotonic() - started < LIVE_MAX_SECONDS:
                async with self._condition:
                    await asyncio.wait_for(self._condition.wait_for(lambda: self._sequence > sequence), timeout=10)
                    sequence, frame = self._sequence, self._frame
                await response.write(b'--vgrow-usb\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                     + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
        except (ConnectionResetError, asyncio.CancelledError, asyncio.TimeoutError):
            pass
        finally:
            self._live -= 1
        return response

    async def start_recording(self) -> str:
        async with self._record_lock:
            return await self._start_recording()

    async def _start_recording(self) -> str:
        if self._rec is not None and self._rec.returncode is None:
            raise ValueError(t('camera.recording_running'))
        try:
            await self.snapshot()
        except TextError as err:
            raise ValueError(err.text()) from None
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.rec_dir).free < MIN_FREE_BYTES:
            raise ValueError(t('camera.low_disk', gb=MIN_FREE_BYTES // 1024**3))
        if self._writer:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
        out = self.rec_dir / f'{datetime.now():%Y-%m-%d_%H-%M-%S}.mp4'
        self._rec = await asyncio.create_subprocess_exec(*encoding_args(self.ffmpeg, out, self.max_record_seconds),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        self._rec_file, self._rec_started = out, time.time()
        self._writer = asyncio.create_task(self._write_recording(self._rec), name='usb_camera_recording')
        return out.name

    async def _write_recording(self, proc: asyncio.subprocess.Process) -> None:
        try:
            while proc.returncode is None:
                if self._frame is None or time.monotonic() - self._frame_at > 5:
                    break
                proc.stdin.write(self._frame)
                await proc.stdin.drain()
                await asyncio.sleep(1 / FRAME_FPS)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if proc.stdin:
                proc.stdin.close()

    async def stop_recording(self) -> str | None:
        proc, name = self._rec, self._rec_file.name if self._rec_file else None
        if self._writer:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
            self._writer = None
        if proc is not None:
            if proc.stdin:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        self._rec, self._rec_file, self._rec_started = None, None, None
        return name

    def status(self) -> dict[str, Any]:
        view = super().status()
        view.update(source='usb', resolution=self.size, fps=FRAME_FPS)
        if self._frame_at and time.monotonic() - self._frame_at >= 5:
            view['reachable'] = False
        return view

    async def close(self) -> None:
        await self.stop_recording()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
