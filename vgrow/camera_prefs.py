"""Cameras the owner put away: settings.json "hidden_cameras" (owner, 07.11.2026: the window of the camera that does
not work should simply go, and come back once a camera is there again).

The page leaves a hidden camera's card out and lets the other one take the row; its offline alarm stays quiet
(alerts.Alerts.check), without a "back" push for a camera that is not back. Nothing else changes: the camera keeps
running, so recordings, the pest watch and the photo check use it whenever it delivers, and the page then says so
next to its "Show" button. Hiding and showing are one tap each (/api/cmd camera_hide).
"""

from __future__ import annotations

import json
from typing import Any

from . import config, i18n

KEY = "hidden_cameras"


def _settings() -> dict[str, Any]:
    try:
        data = json.loads(i18n.settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def hidden() -> set[str]:
    """The ids of the hidden cameras ("growcam", "usb"); an unreadable file hides nothing."""
    value = _settings().get(KEY)
    return {ident for ident in value if isinstance(ident, str)} if isinstance(value, list) else set()


def set_hidden(ident: str, hide: bool) -> list[str]:
    """Hide or show one camera, keeping every other key of settings.json (the language, Hank's mode, the wiki
    packages), in one step."""
    now = sorted((hidden() - {ident}) | ({ident} if hide else set()))
    data = {**_settings(), KEY: now}
    config.atomic_write(i18n.settings_path(), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return now
