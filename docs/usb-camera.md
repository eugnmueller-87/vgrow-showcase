# USB camera on the Mac server

[Repository](../README.md) · [Setup](getting-started.md) · [Camera gallery](gallery.md#both-cameras-without-an-overlay-over-the-footage)

The Logitech HD Pro Webcam C920 is an additional camera source. The existing GrowCam,
its credentials and its daily photo/analysis source stay as configured. Camera selection
in the viewer does not redirect plant analysis to a desk camera.

GrowCam is the measuring-strip/height reference; C920 is the detail source. Under My Grow / AI Insights,
the user can explicitly select a source for a new descriptive observation, with HD preselected when available.
That separate analysis action captures a fresh frame from the selected camera and records camera identity,
resolution and time. It does not change the recorded plant stage, observed start date, height or controls.
User estimates from the GrowCam ruler retain their approximate marker and provenance in records and exports.

Connect the USB camera to the Mac (a data-capable adapter/hub is supported), then run:

```sh
.venv/bin/python scripts/install_usb_camera.py --device 'HD Pro Webcam C920'
```

The installer creates `STATE_DIR/camera-usb.json` and a local `VGrow Camera.app` helper
from the already installed imageio-ffmpeg executable. The app has its own bundle ID,
camera usage description and camera entitlement. macOS asks the owner to allow camera
access normally; no privacy database is modified or reset. Repeated installation with
the same ffmpeg binary preserves the installed helper. The helper is needed because a
camera process started directly over SSH or by a background service may have no usable
GUI permission prompt. The user must be logged into the Mac's desktop session.

After restarting the server, both cameras appear together under Devices / Camera and
on Overview. GrowCam is on the left and the USB camera on the right; narrow screens
stack the panels. Each camera has independent live/still, fullscreen and recording
controls. The action center follows the camera row. A single-camera installation keeps
one panel. The same players move between pages without duplicating their streams, and
streams stop while the page or browser tab is hidden. Source IDs are `growcam` and `usb`.
The USB panel offers digital zoom from 1× to 4× in half steps. Use +/−, click the zoom
value to reset, and drag the enlarged image to pan (also with touch). Focus the image
for keyboard +/−, 0 and arrow keys. Zoom controls sit beside Start recording below the
image. Fullscreen includes the player and its separate control bar, keeping the footage
clear. Zoom and pan
belong to this viewer, preserve the source feed and full-frame recordings, and do not
send camera/device commands. Enlarging a crop does not add optical detail.
Both viewers fill their frames, including fullscreen, with a centered crop where the
screen and camera proportions differ. Images keep their proportions, with no added
letterboxing. Panning stays bounded to keep the entire viewing frame covered.
Snapshot/live/recordings URLs accept `?camera=usb`; recording commands accept the
`camera` field. Omitting it preserves existing GrowCam routes. Unknown IDs are rejected.

The helper captures video only at 1920 × 1080 (configurable to 1280 × 720 or 640 × 480).
One capture is shared by all viewers and still requests; MJPEG is served at 5 fps.
A local Unix socket with owner-only permissions transports frames. Capturing always
uses the configured device name and `:none` for audio; the built-in camera and microphone
are never fallback devices. No new network port is exposed.

Snapshots require a fresh frame. A disconnected/stalled camera becomes offline, ends
its live streams and retries acquisition. While macOS does not list the configured
camera, the page says exactly that, no helper is started, and the server looks again
every 15 seconds; the camera returns by itself once it is listed. The listing opens no
camera. USB recordings encode shared JPEG frames to
H.264 MP4 without audio and live in `recordings/usb/`, separate from GrowCam files.
Recordings keep the existing two-hour duration and free-disk limits. Closing the service
finishes recording, stops the exact helper executable and removes its socket.

**Hiding a camera (owner, 07.11.2026).** A camera card without a picture offers **Hide**.
The card goes, the other camera takes the row, and that camera's offline alarm stays quiet,
with no "back" push for a camera that is only hidden. A line under the cards names each
hidden camera with **Show**; once a hidden camera delivers pictures again, the line says
so and the button turns green. The camera itself keeps running, so recordings, the pest
watch and the photo check use it whenever it delivers. The setting is `hidden_cameras` in
`settings.json` (`vgrow/camera_prefs.py`), so phone and desktop show the same.
