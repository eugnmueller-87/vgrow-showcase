"""Install the local macOS camera helper and explicitly select a USB video device.

The app bundle gives macOS a stable identity and camera usage description for its
normal consent dialog. It does not alter or reset the system's privacy settings.
"""
import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vgrow import config
from vgrow.camera import ffmpeg_exe


def install(device: str, size: str = '1920x1080') -> Path:
    if sys.platform != 'darwin':
        raise SystemExit('USB camera helper is for macOS.')
    if (not device or len(device) > 160 or ':' in device or any(ord(c) < 32 for c in device)
            or device.casefold() in ('default', 'none') or device.isdigit()):
        raise SystemExit('Select an explicit camera name, not an index or default device.')
    binary = ffmpeg_exe()
    if not binary:
        raise SystemExit('Install imageio-ffmpeg first.')
    root = config.STATE_DIR / 'camera-helper'
    app = root / 'VGrow Camera.app'
    contents = app / 'Contents'
    executable = contents / 'MacOS' / 'VGrowCamera'
    executable.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    marker = root / 'source-sha256'
    unchanged = executable.exists() and marker.exists() and marker.read_text().strip() == digest
    if not unchanged:
        shutil.copy2(binary, executable)
        executable.chmod(0o755)
    info = {'CFBundleExecutable': 'VGrowCamera', 'CFBundleIdentifier': 'de.vgrow.camera',
            'CFBundleName': 'VGrow Camera', 'CFBundleDisplayName': 'VGrow Camera',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'LSUIElement': True,
            'NSCameraUsageDescription': 'VGrow Cockpit zeigt das Bild deiner ausgewählten USB-Kamera an.'}
    if not unchanged:
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        with tempfile.NamedTemporaryFile(suffix='.plist') as entitlements:
            entitlements.write(plistlib.dumps({'com.apple.security.device.camera': True}))
            entitlements.flush()
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--entitlements', entitlements.name,
                            str(app)], check=True, capture_output=True)
        config.atomic_write(marker, digest + '\n')
    config.atomic_write(config.STATE_DIR / 'camera-usb.json', json.dumps(
        {'device': device, 'size': size, 'bridge_app': str(app)}, indent=2) + '\n')
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--size', choices=['1920x1080', '1280x720', '640x480'], default='1920x1080')
    args = parser.parse_args()
    print(install(args.device, args.size))
