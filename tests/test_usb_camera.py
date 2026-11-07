"""USB camera isolation, shared acquisition, stale-frame protection and real MP4 encoding."""
import asyncio
import json
import subprocess
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import usb_camera as usb
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox, DemoCamera
from vgrow.i18n import TextError, t
from vgrow.server import COOKIE, make_app

SEEN_BY_MACOS = usb.UsbCamera._seen_by_macos  # the real one: every other test has macOS list the camera
LISTING = ('[AVFoundation indev @ 0x7f] AVFoundation video devices:\n'
           '[AVFoundation indev @ 0x7f] [0] FaceTime HD Camera (Built-in)\n'
           '[AVFoundation indev @ 0x7f] [1] HD Pro Webcam C920\n'
           '[AVFoundation indev @ 0x7f] AVFoundation audio devices:\n'
           '[AVFoundation indev @ 0x7f] [0] C920 Microphone\n'
           ': Input/output error\n')


@pytest.fixture(autouse=True)
def macos_lists_the_camera(monkeypatch):
    async def listed(self):
        return True
    monkeypatch.setattr(usb.UsbCamera, '_seen_by_macos', listed)


def test_listing_reads_the_video_section_only():
    assert usb.listed_video_devices(LISTING) == ['FaceTime HD Camera (Built-in)', 'HD Pro Webcam C920']
    assert usb.listed_video_devices('[x] AVFoundation video devices:\n[x] AVFoundation audio devices:\n') == []
    assert usb.listed_video_devices('dyld: Library not loaded') is None


@pytest.mark.parametrize('device,output,expected', [('HD Pro Webcam C920', LISTING, True), ('Another Camera', LISTING, False),
                                                    ('C920 Microphone', LISTING, False), ('HD Pro Webcam C920', 'dyld: Library not loaded', None)])
def test_macos_answers_yes_no_or_unclear(tmp_path, monkeypatch, device, output, expected):
    async def run():
        class Process:
            async def communicate(self):
                return None, output.encode()
        async def create(*args, **kwargs):
            assert '-list_devices' in args and not any(':none' in arg for arg in args), 'The listing opens no camera'
            return Process()
        monkeypatch.setattr(usb.asyncio, 'create_subprocess_exec', create)
        camera = usb.UsbCamera(tmp_path, device)
        camera.ffmpeg = 'ffmpeg'
        assert await SEEN_BY_MACOS(camera) is expected
    asyncio.run(run())


def test_a_listing_that_cannot_run_is_unclear_and_blocks_nothing(tmp_path, monkeypatch):
    async def run():
        async def create(*args, **kwargs):
            raise OSError('no ffmpeg')
        monkeypatch.setattr(usb.asyncio, 'create_subprocess_exec', create)
        camera = usb.UsbCamera(tmp_path, 'C920')
        camera.ffmpeg = 'ffmpeg'
        assert await SEEN_BY_MACOS(camera) is None
    asyncio.run(run())


def test_a_camera_macos_does_not_list_names_the_reason_and_returns_by_itself(tmp_path, monkeypatch):
    async def run():
        calls, listed, reader = [], [False], asyncio.StreamReader()
        class Process:
            stdout = reader
            returncode = None
            def kill(self):
                self.returncode = -9
            async def wait(self):
                return self.returncode
        async def create(*args, **kwargs):
            calls.append(args)
            return Process()
        async def seen(self):
            return listed[0]
        monkeypatch.setattr(usb.asyncio, 'create_subprocess_exec', create)
        monkeypatch.setattr(usb.UsbCamera, '_seen_by_macos', seen)
        monkeypatch.setattr(usb.sys, 'platform', 'darwin')
        monkeypatch.setattr(usb, 'SEEN_RETRY', .01)
        camera = usb.UsbCamera(tmp_path, 'C920')
        camera.ffmpeg = 'ffmpeg'
        camera.start()
        await asyncio.sleep(.05)
        status = camera.status()
        assert status['reachable'] is False and status['error'] == t('camera.usb_not_seen')
        assert calls == [], 'Nothing to open: no capture and no helper start'
        with pytest.raises(TextError) as refused:
            await asyncio.wait_for(camera.snapshot(), timeout=1)  # at once, not after the 15 s frame wait
        assert refused.value.text() == t('camera.usb_not_seen')
        listed[0] = True
        await asyncio.sleep(.05)
        assert len(calls) == 1 and camera.status()['error'] is None
        reader.feed_data(b'\xff\xd8back\xff\xd9')
        assert await camera.snapshot() == b'\xff\xd8back\xff\xd9'
        assert camera.status()['reachable'] is True
        await camera.close()
    asyncio.run(run())


def test_explicit_device_and_video_only_capture_and_encoding(tmp_path):
    args = usb.capture_args('ffmpeg', 'HD Pro Webcam C920', '1920x1080')
    assert args[args.index('-i') + 1] == 'HD Pro Webcam C920:none'
    assert args[args.index('-video_size') + 1] == '1920x1080'
    assert '-an' in args and 'default:none' not in args and '0:none' not in args
    args = usb.encoding_args('ffmpeg', tmp_path / 'video.mp4', 7200)
    assert args[args.index('-i') + 1] == 'pipe:0' and '-an' in args
    assert args[args.index('-c:v', args.index('-i')) + 1] == 'libx264'
    assert args[args.index('-t') + 1] == '7200'


def test_camera_is_opt_in_and_recordings_have_their_own_directory(tmp_path, monkeypatch):
    assert usb.configured_camera(tmp_path, tmp_path / 'recordings') is None
    path = tmp_path / 'camera-usb.json'
    for data in ({}, {'device': 'default:none'}, {'device': 'default'}, {'device': '0'},
                 {'device': 'x\nprivate'}, {'device': 'C920', 'size': '99999x99999'}, []):
        path.write_text(json.dumps(data))
        assert usb.configured_camera(tmp_path, tmp_path / 'recordings') is None
    path.write_text(json.dumps({'device': 'HD Pro Webcam C920', 'size': '1920x1080'}))
    camera = usb.configured_camera(tmp_path, tmp_path / 'recordings')
    assert camera.device == 'HD Pro Webcam C920'
    assert camera.rec_dir == tmp_path / 'recordings/usb'
    monkeypatch.setattr(usb.sys, 'platform', 'darwin')
    camera.ffmpeg = 'ffmpeg'
    assert camera.configured and not camera.searching


def test_shared_capture_extracts_fragmented_jpegs_and_stops_the_device(tmp_path, monkeypatch):
    async def run():
        calls = []
        reader = asyncio.StreamReader()
        class Process:
            stdout = reader
            returncode = None
            def kill(self):
                self.returncode = -9
            async def wait(self):
                return self.returncode
        process = Process()
        async def create(*args, **kwargs):
            calls.append(args)
            return process
        monkeypatch.setattr(usb.asyncio, 'create_subprocess_exec', create)
        monkeypatch.setattr(usb.sys, 'platform', 'darwin')
        camera = usb.UsbCamera(tmp_path, 'C920')
        camera.ffmpeg = 'ffmpeg'
        camera.start()
        await asyncio.sleep(0)
        reader.feed_data(b'\xff\xd8first')
        await asyncio.sleep(.01)
        reader.feed_data(b'frame\xff\xd9')
        frames = await asyncio.gather(camera.snapshot(), camera.snapshot(), camera.photo())
        assert frames == [b'\xff\xd8firstframe\xff\xd9'] * 3
        assert len(calls) == 1 and camera.status()['reachable'] is True
        camera._frame_at = time.monotonic() - 6
        assert camera.status()['reachable'] is False
        pending = asyncio.create_task(camera.snapshot())
        await asyncio.sleep(.01)
        assert not pending.done(), 'A stale frame must never satisfy a new snapshot'
        reader.feed_data(b'\xff\xd8second\xff\xd9')
        assert await pending == b'\xff\xd8second\xff\xd9'
        await camera.close()
        assert process.returncode == -9 and camera._task is None
    asyncio.run(run())


def test_camera_routes_select_source_without_changing_primary_or_crossing_recordings(tmp_path):
    class Fake(DemoCamera):
        def __init__(self, name, frame):
            super().__init__()
            self.name, self.frame, self.calls = name, frame, []
        def status(self):
            return {**super().status(), 'name': self.name}
        async def snapshot(self):
            return self.frame
        async def start_recording(self):
            self.calls.append('start')
            return 'test.mp4'
        def path_for(self, name):
            return None
    async def run():
        primary, extra = Fake('GrowCam', b'growcam'), Fake('C920', b'usb')
        app = make_app(DemoBox(), Alerts(), 'test-key', primary, extra_cameras={'usb': extra})
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/camera/snapshot.jpg?camera=usb')).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: 'test-key'})
            assert await (await client.get('/camera/snapshot.jpg')).read() == b'growcam'
            assert await (await client.get('/camera/snapshot.jpg?camera=usb')).read() == b'usb'
            assert (await client.get('/camera/snapshot.jpg?camera=unknown')).status == 404
            assert (await client.get('/recordings/test.mp4?camera=unknown')).status == 404
            assert (await client.get('/api/recordings?camera=unknown')).status == 404
            state = await (await client.get('/api/state')).json()
            assert state['camera']['name'] == 'GrowCam'
            assert [item['id'] for item in state['cameras']] == ['growcam', 'usb']
            assert state['camera_default'] == 'usb'
            result = await client.post('/api/cmd', json={'action': 'record_start', 'camera': 'usb'})
            assert result.status == 200 and extra.calls == ['start'] and primary.calls == []
            assert (await client.post('/api/cmd', json={'action': 'record_start', 'camera': {}})).status == 400
    asyncio.run(run())


def test_usb_records_shared_frames_as_playable_h264_without_audio(tmp_path, monkeypatch):
    async def run():
        camera = usb.UsbCamera(tmp_path, 'C920')
        if not camera.ffmpeg:
            pytest.skip('ffmpeg unavailable')
        jpeg = subprocess.run([camera.ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                               '-i', 'color=c=green:s=320x240:r=5', '-frames:v', '1',
                               '-f', 'image2pipe', '-c:v', 'mjpeg', 'pipe:1'], capture_output=True, check=True).stdout
        monkeypatch.setattr(camera, 'start', lambda: None)
        await camera._publish(jpeg)
        name = await camera.start_recording()
        with pytest.raises(ValueError):
            await camera.start_recording()
        await asyncio.sleep(1.1)
        assert await camera.stop_recording() == name
        decoded = subprocess.run([camera.ffmpeg, '-hide_banner', '-i', str(tmp_path / name), '-f', 'null', '-'],
                                 capture_output=True, text=True)
        assert decoded.returncode == 0, decoded.stderr
        assert 'Video: h264' in decoded.stderr and 'Audio:' not in decoded.stderr
        assert camera.recording() is None
        assert camera.recordings()[0]['name'] == name
        await camera.close()
    asyncio.run(run())


def test_desktop_helper_uses_a_private_socket_and_cleans_up_its_exact_process(tmp_path, monkeypatch):
    async def run():
        calls = []
        class Process:
            returncode = None
            def kill(self):
                self.returncode = -9
            async def wait(self):
                return self.returncode
        class Writer:
            closed = False
            def close(self):
                self.closed = True
            async def wait_closed(self):
                pass
        class Listener:
            closed = False
            def close(self):
                self.closed = True
            async def wait_closed(self):
                pass
        reader, writer, listener = asyncio.StreamReader(), Writer(), Listener()
        async def listen(callback, path):
            path.touch()
            asyncio.create_task(callback(reader, writer))
            return listener
        async def create(*args, **kwargs):
            calls.append(args)
            return Process()
        permissions = []
        chmod = usb.os.chmod
        def checked_chmod(path, mode):
            permissions.append((path.name, mode))
            chmod(path, mode)
        monkeypatch.setattr(usb.asyncio, 'start_unix_server', listen, raising=False)
        monkeypatch.setattr(usb.asyncio, 'create_subprocess_exec', create)
        monkeypatch.setattr(usb.os, 'chmod', checked_chmod)
        monkeypatch.setattr(usb.sys, 'platform', 'darwin')
        camera = usb.UsbCamera(tmp_path / 'recordings/usb', 'C920', bridge_app=tmp_path / 'VGrow Camera.app')
        camera.ffmpeg = 'ffmpeg'
        camera.start()
        reader.feed_data(b'\xff\xd8desktop-frame\xff\xd9')
        assert await camera.snapshot() == b'\xff\xd8desktop-frame\xff\xd9'
        assert permissions == [('usb-camera.sock', 0o600)]
        await camera.close()
        assert writer.closed and listener.closed
        assert not (tmp_path / 'recordings/usb-camera.sock').exists()
        assert calls[0][0] == '/usr/bin/open' and '-a' in calls[0]
        assert calls[-1][:2] == ('/usr/bin/pkill', '-f')
        assert calls[-1][2].startswith('^') and 'VGrowCamera' in calls[-1][2]
    asyncio.run(run())
