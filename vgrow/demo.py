"""Stand-ins for layout work, screenshots and people without a box. Not real readings: the page shows a DEMO banner.

Base values are the documented examples (upstream VIVOSUN-API-SPEC.md: 20.04 C, 55.08 %,
1.05 kPa; issue #43: water 75 %, 22.26 C). The probe values are made up inside the early-flower
targets of kartei/stages/autoflower.json, so the plant card shows real checks. The history is a
made-up curve around them.
Nothing here can pass for the real box: the camera serves a drawing that says "DEMO-Kamera" /
"DEMO camera", the AI assessment names the model "demo" and starts with "Demo-Einschätzung" /
"Demo assessment", there are no recordings, and every push title says DEMO. serve() gives the demo its
own state folder. The box runs the flower spectrum, as the early-flower preset it is measured against says.
"""

from __future__ import annotations

import asyncio
import copy
import math
import os
import time
from datetime import date, datetime, timedelta
from typing import Any
from xml.sax.saxutils import escape

from aiohttp import web

from . import feeding
from .box import CFAN_LEVEL_MAP, DFAN_LEVEL_MAP, POLL_SECONDS, SPECTRUMS, Box, CommandRefused, check_port_lock
from .i18n import current_lang, normalize, t
from .vision import PHOTO_NAME, Vision

DEMO_RUN_SECONDS = 4  # a "reading" takes this long, so the busy state is visible; no model runs
# The demo's water sensor. Unset: the DWC kit of issue #43 (type 10, 75 %). VGROW_DEMO_WATER=wet or dry: the owner's
# wet/dry sensor (type 9, vgrow/box.py water_view), last changed two hours before the demo started, for screenshots.
DEMO_WATER_ENV = "VGROW_DEMO_WATER"
DEMO_WATER_CHANGED_S = 2 * 3600
# VGROW_DEMO_CLIMATE=vpd_high: the climate measured on the owner's box on 23.10.2026 at 21:20, held flat for screenshots
# of the VPD-high case (probe at leaf height 25.06 °C, 45.5 %, VPD 1.73 kPa; the box's own sensor 22.6 °C, 43.9 %,
# 1.53 kPa), with the humidifier on USB 2 off. Unset: the made-up day curve below.
DEMO_CLIMATE_ENV = "VGROW_DEMO_CLIMATE"
VPD_HIGH_CLIMATE = {"temp": 22.6, "humidity": 43.9, "vpd": 1.53, "probe_temp": 25.06, "probe_humidity": 45.5,
                    "probe_vpd": 1.73}


def vpd(temp: float, humidity: float) -> float:
    """Tetens, from the air temperature like the box reports it (no leaf offset)."""
    return 0.6108 * math.exp(17.27 * temp / (temp + 237.3)) * (1 - humidity / 100)


class DemoBox:
    demo = True
    notify_listeners = Box.notify_listeners  # the same listener pattern as the real box

    def __init__(self) -> None:
        self.listeners: list[Any] = []
        self.light = {"level": 75, "mode": 0, "in_plan": False, "spectrum": "bloom"}
        self.cfan_step, self.natural_wind, self.dfan_step = 5, False, 4
        # USB 1 is the drip pump (off: it runs only by hand), USB 2 the humidifier (ports.json). The camera needs its
        # own power supply and is no port.
        self.ports = {"usb1": False, "usb2": True}
        self._task: asyncio.Task[None] | None = None
        wanted = os.environ.get(DEMO_WATER_ENV, "").strip().lower()
        self.water_state = wanted if wanted in ("wet", "dry") else None
        self.water_since = time.time() - DEMO_WATER_CHANGED_S if self.water_state else None
        self.vpd_high = os.environ.get(DEMO_CLIMATE_ENV, "").strip().lower() == "vpd_high"
        if self.vpd_high:
            self.ports["usb2"] = False

    async def start(self) -> None:
        # Like the real box: one evaluation right away, then one per poll, so alerts and automation run.
        await self.notify_listeners()
        self._task = asyncio.create_task(self._poll_loop(), name="demo_poll")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def refresh_shadow(self) -> None: ...

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            await self.notify_listeners()

    def _climate(self, ts: float) -> dict[str, float]:
        if self.vpd_high:
            return {**VPD_HIGH_CLIMATE, "water_level": 75.0, "water_temp": 22.26, "room_temp": 19.85,
                    "room_humidity": 54.49}
        day = math.sin(ts / 86400 * 2 * math.pi)
        probe_temp, probe_hum = 22.2 + 1.2 * day, 56.0 - 3.0 * day
        return {
            "temp": round(20.04 + 2.5 * day, 2), "humidity": round(55.08 - 6 * day, 2),
            "vpd": round(1.05 + 0.2 * day, 2), "probe_temp": round(probe_temp, 2),
            "probe_humidity": round(probe_hum, 2), "probe_vpd": round(vpd(probe_temp, probe_hum), 2),
            "water_level": 75.0, "water_temp": 22.26, "room_temp": 19.85, "room_humidity": 54.49,
        }

    def _water(self, climate: dict[str, Any]) -> dict[str, Any]:
        """The water fields as box.water_view gives them for the demo's sensor type."""
        if self.water_state is None:
            return {**climate, "water_kind": "level", "water_state": None, "water_raw": None, "water_since": None}
        return {**climate, "water_kind": "wet_dry", "water_state": self.water_state, "water_level": None,
                "water_temp": None, "water_raw": None, "water_since": self.water_since,
                "water_run_s": time.time() - self.water_since}  # the readings said it since then (box.water_run)

    def snapshot(self) -> dict[str, Any]:
        return {
            "demo": True, "device": "VGrow Smart Grow Box", "online": True, "mqtt": True,
            "shadow_age": 3, "sensors_age": 40, "climate": self._water(self._climate(time.time())),
            "aux_device": 10 if self.water_state is None else 9,
            "light": {**self.light, "spectrum_writable": True},
            "cfan": {"step": None if self.natural_wind else self.cfan_step, "natural_wind": self.natural_wind, "mode": 0},
            "dfan": {"step": self.dfan_step, "mode": 0},
            "ports": {k: {"on": v, "mode": 0, "in_plan": 0, "accessory": None, "can_write": True} for k, v in self.ports.items()},
            "plan": None, "pending": {}, "error": None,
        }

    async def history(self, hours: int) -> list[dict[str, Any]]:
        end = int(time.time())
        step = max(60, hours * 3600 // 720)
        rows = []
        for ts in range(end - hours * 3600, end, step):
            c = self._climate(ts)
            rows.append({"time": ts, "bTemp": c["temp"], "bHumi": c["humidity"], "bVpd": c["vpd"],
                         "pTemp": c["probe_temp"], "pHumi": c["probe_humidity"], "pVpd": c["probe_vpd"],
                         "waterLv": c["water_level"], "waterT": c["water_temp"]})
        if self.water_state is not None:  # the wet/dry sensor: scaled 200 (raw 20000) wet, 0 dry, no tank temperature
            for r in rows:
                dry = self.water_state == "dry" and self.water_since is not None and r["time"] >= self.water_since
                r.update(waterLv=0.0 if dry else 200.0, waterT=None)
        return rows

    async def set_light(self, level: int) -> None:
        self.light["level"] = 0 if level <= 0 else max(25, min(100, int(level)))

    async def set_spectrum(self, name: str) -> None:
        if name not in SPECTRUMS:
            raise CommandRefused(t("box.unknown_spectrum", name=repr(name)))
        self.light["spectrum"] = name

    async def set_cfan(self, step: int | None, natural_wind: bool = False) -> None:
        self.natural_wind = natural_wind
        if not natural_wind:
            self.cfan_step = max(0, min(len(CFAN_LEVEL_MAP) - 1, int(step or 0)))

    async def set_dfan(self, step: int) -> None:
        self.dfan_step = max(0, min(len(DFAN_LEVEL_MAP) - 1, int(step)))

    async def set_port(self, port: str, on: bool) -> None:
        if port not in self.ports:
            raise CommandRefused(t("box.not_a_port", port=port))
        check_port_lock(port, bool(on))
        self.ports[port] = bool(on)


# ---------- placeholder picture ----------

MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _stamp(when: datetime, lang: str) -> str:
    """As the page writes dates: 22.10.2026 19:39, and in English with the month's name (Oct 22, 2026,
    7:39 PM), because 10/09 reads as two different days on the two sides of the Atlantic."""
    if lang == "de":
        return f"{when:%d.%m.%Y %H:%M}"
    hour = when.hour % 12 or 12
    return f"{MONTHS_EN[when.month - 1]} {when.day}, {when.year}, {hour}:{when:%M} {'PM' if when.hour >= 12 else 'AM'}"


def _leaf(x: float, y: float, angle: float, scale: float, color: str) -> str:
    fingers = []
    for spread in (-52, -26, 0, 26, 52):
        length = 48 * scale * (1 - abs(spread) / 130)
        fingers.append(f'<ellipse cx="0" cy="{-length / 2:.1f}" rx="{6.5 * scale:.1f}" ry="{length / 2:.1f}" '
                       f'transform="rotate({spread})"/>')
    return f'<g transform="translate({x:.0f} {y:.0f}) rotate({angle})" fill="{color}">{"".join(fingers)}</g>'


def placeholder_svg(title: str, caption: str, stamp: str, footer: str) -> bytes:
    """A drawn grow box under a big DEMO title: never a real picture, never mistaken for one."""
    plant = "".join(_leaf(1000, y, side * 58, s, color)
                    for y, s in ((520, 1.15), (468, 1.0), (418, 0.85), (372, 0.7))
                    for side, color in ((-1, "#3f8f55"), (1, "#2f7a45")))
    pistils = "".join(f'<path d="M1000 300 l{dx} {dy}"/>'
                      for dx, dy in ((-14, -8), (14, -8), (-9, -15), (9, -15), (0, -18)))
    corners = "".join(f'<path d="{d}"/>' for d in (
        "M40 90 V40 H90", "M1190 40 H1240 V90", "M40 630 V680 H90", "M1190 680 H1240 V630"))
    font = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<rect width="1280" height="720" fill="#14181f"/>'
        f'<g fill="none" stroke="#5b6678" stroke-width="5" stroke-linecap="round">{corners}</g>'
        '<rect x="820" y="70" width="360" height="590" rx="16" fill="#1b212b" stroke="#3b4656" stroke-width="6"/>'
        '<rect x="850" y="96" width="300" height="16" rx="8" fill="#f3ead8"/>'
        '<path d="M858 112 H1142 L1170 650 H830 Z" fill="#f3ead8" opacity="0.07"/>'
        '<path d="M836 330 H1164" stroke="#56627a" stroke-width="3" stroke-dasharray="10 10"/>'
        '<path d="M1000 552 C1000 470 1003 400 1000 304" stroke="#2f6f40" stroke-width="8" fill="none" '
        'stroke-linecap="round"/>'
        f'{plant}'
        '<circle cx="1000" cy="300" r="14" fill="#7fb069"/>'
        f'<g stroke="#f4f1ea" stroke-width="2.5" stroke-linecap="round">{pistils}</g>'
        '<rect x="928" y="548" width="144" height="16" rx="8" fill="#3b2a1e"/>'
        '<path d="M934 562 H1066 L1052 640 H948 Z" fill="#6b4a32"/>'
        f'<text x="80" y="300" font-family="{font}" font-size="88" font-weight="800" fill="#f5c542">{escape(title)}</text>'
        f'<text x="84" y="360" font-family="{font}" font-size="30" fill="#c9d1dc">{escape(caption)}</text>'
        f'<text x="84" y="408" font-family="{font}" font-size="26" fill="#8a94a6">{escape(stamp)}</text>'
        f'<text x="84" y="640" font-family="{font}" font-size="24" fill="#8a94a6">{escape(footer)}</text>'
        '</svg>'
    )
    return svg.encode("utf-8")


class DemoCamera:
    """Stands in for the GrowCam: a drawn placeholder, live is the same still, and no recordings."""

    demo = True
    content_type = "image/svg+xml"
    configured = True
    reachable = True
    name = "DEMO"
    ip = None
    error = None
    max_record_seconds = 120 * 60

    def picture(self, caption_key: str = "demo.camera_caption", when: datetime | None = None) -> bytes:
        lang = current_lang()  # the page's language: X-Lang, or ?lang= on an image URL
        return placeholder_svg(t("demo.camera_title", lang), t(caption_key, lang),
                               _stamp(when or datetime.now(), lang), t("demo.footer", lang))

    def status(self) -> dict[str, Any]:
        return {"configured": True, "name": self.name, "ip": None, "error": None, "reachable": True,
                "offline_seconds": None, "recording": None, "max_minutes": self.max_record_seconds // 60,
                "demo": True}

    async def snapshot(self) -> bytes:
        return self.picture()

    async def photo(self) -> bytes:
        return self.picture("demo.photo_caption")

    async def live(self, request: web.Request) -> web.StreamResponse:
        return web.Response(body=self.picture(), content_type=self.content_type, headers={"Cache-Control": "no-store"})

    def recordings(self) -> list[dict[str, Any]]:
        return []

    def path_for(self, name: str) -> None:
        return None

    async def start_recording(self) -> str:
        raise ValueError(t("demo.no_recording"))

    async def stop_recording(self) -> str | None:
        raise ValueError(t("demo.no_recording"))

    def delete(self, name: str) -> None:
        raise ValueError(t("demo.no_recording"))

    async def close(self) -> None: ...


# ---------- AI assessment ----------

DEMO_READING: dict[str, dict[str, Any]] = {
    "de": {
        "stage_guess": "early_flower",
        "confidence": "medium",
        "stage_reason": "Demo-Einschätzung: erste weiße Härchen an den Triebspitzen, die Pflanze streckt sich.",
        "visible_signs": ["Weiße Härchen an mehreren Triebspitzen", "Blätter gleichmäßig grün",
                          "Triebe unter dem Netz verteilt"],
        "issues": [{"issue": "Untere Blätter etwas hell", "severity": "watch", "where": "unten links"},
                   {"issue": "Ein Trieb wächst über das Netz", "severity": "info", "where": "Mitte"}],
        "next_steps": ["Licht in Schritten auf die Blühvoreinstellung (100 %) bringen",
                       "Den hohen Trieb unter das Netz binden", "Feuchte am Fühler unter 60 % halten"],
        "not_assessable": ["Trichomfarbe, dafür eine Makroaufnahme machen"],
    },
    "en": {
        "stage_guess": "early_flower",
        "confidence": "medium",
        "stage_reason": "Demo assessment: first white pistils at the shoot tips, the plant is stretching.",
        "visible_signs": ["White pistils at several shoot tips", "Leaves evenly green",
                          "Shoots spread out under the net"],
        "issues": [{"issue": "Lower leaves slightly pale", "severity": "watch", "where": "bottom left"},
                   {"issue": "One shoot grows above the net", "severity": "info", "where": "center"}],
        "next_steps": ["Raise the light in steps to the flower preset (100%)",
                       "Tie the tall shoot under the net", "Keep the humidity at the probe below 60%"],
        "not_assessable": ["Trichome color, take a macro photo for that"],
    },
}


class DemoVision(Vision):
    """A fixed assessment in both languages through the normal vision view; no model, no files."""

    demo = True

    def __init__(self, camera: Any) -> None:
        super().__init__(camera)
        self.model = "demo"
        noon = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0)
        self._made = noon if noon <= datetime.now() else noon - timedelta(days=1)
        self._runs = 0
        self._source: dict[str, Any] | None = None
        self._observation = False

    def latest(self, lang: str | None = None) -> dict[str, Any]:  # type: ignore[override]
        lang = normalize(lang) or current_lang()
        record = {"date": self._made.isoformat(timespec="minutes"), "photo": f"{self._made.date().isoformat()}.jpg",
                "model": "demo", "local": True, "tokens": {"in": None, "out": None}, "seconds": None,
                "lang": lang, "demo": True, "result": copy.deepcopy(DEMO_READING[lang])}
        if self._source:
            record["camera_source"] = dict(self._source)
        if self._observation:
            record["observation_only"] = True
            record["result"]["next_steps"] = []
        return record

    def photo_names(self, limit: int = 21) -> list[str]:  # type: ignore[override]
        return [(date.today() - timedelta(days=d)).isoformat() for d in (8, 6, 4, 2, 0)][-limit:]

    def photo_file(self, name: str) -> tuple[bytes, str] | None:  # type: ignore[override]
        if not PHOTO_NAME.fullmatch(name) or name[:-4] not in self.photo_names() + [self._made.date().isoformat()]:
            return None
        when = datetime.combine(date.fromisoformat(name[:-4]), datetime.min.time()).replace(hour=12, minute=5)
        return self.camera.picture("demo.photo_caption", when), self.camera.content_type

    def photo_taken_today(self) -> bool:
        return True

    def due(self, retry_after: float = 2 * 3600) -> bool:
        return False  # no daily job in the demo

    def manual_runs_today(self) -> int:
        return self._runs

    async def run(self, snap: dict[str, Any], plant: dict[str, Any] | None,
                  lang: str | None = None, *, camera: Any = None, source: dict[str, Any] | None = None,
                  observation_only: bool = False) -> dict[str, Any]:
        lang = normalize(lang) or current_lang()
        if self.busy:
            raise ValueError(t("vision.busy", lang))
        self.busy = True
        self.started = self.last_attempt = time.time()
        try:
            await asyncio.sleep(DEMO_RUN_SECONDS)
            self._runs += 1
            self._made = datetime.now()
            self._source, self._observation = source, observation_only
            return self.latest(lang)
        finally:
            self.busy = False
            self.started = None


# ---------- seeding ----------

# (seconds ago, action, probe humidity): switches just outside the early-flower band 45-60 %, as the automation acts.
DEMO_AUTOMATION = (
    (6 * 3600, "automation_on", None),
    (5 * 3600 + 20 * 60, "humidifier_on", 44.1),
    (4 * 3600, "paused_manual", None),
    (2 * 3600 + 10 * 60, "humidifier_off", 60.8),
    (38 * 60, "humidifier_on", 44.6),
)
DEMO_HEIGHTS = ((15, 29), (8, 37), (2, 43))  # (days ago, cm)
DEMO_WATER = ((6, 19, 10, 6.2, 1.3), (3, 19, 5, 6.4, 1.5), (1, 18, 40, 6.3, 1.6))  # (days ago, h, min, pH, EC)


def setup_demo(plant_log: Any, automation: Any, now: float | None = None) -> tuple[DemoCamera, DemoVision]:
    """Fresh demo content on every start: plant log, an enabled automation with history, camera and AI, and the
    feeding chart as an unconfirmed draft."""
    now = time.time() if now is None else now
    today = date.today()
    plant_log.data = {"stages": [], "heights": [], "water": []}  # no stage set: the estimate from plant.json shows
    for days, cm in DEMO_HEIGHTS:
        plant_log.add_height(cm, (today - timedelta(days=days)).isoformat())
    for days, hour, minute, ph, ec in DEMO_WATER:
        plant_log.add_water(ph, ec, datetime.combine(today - timedelta(days=days), datetime.min.time())
                            .replace(hour=hour, minute=minute))
    automation.log_path.parent.mkdir(parents=True, exist_ok=True)
    automation.log_path.write_text("", encoding="utf-8")
    automation.recent.clear()
    for ago, action, value in DEMO_AUTOMATION:
        automation._log({"action": action, "value": value}, at=now - ago)  # through its own log: "recent" sees it
    ago, action, value = DEMO_AUTOMATION[-1]
    # The demo's stage is the estimate from plant.json, so its first poll pauses the automation and switches the
    # demo humidifier off, as the real one would. Its push counts as sent: a push on every demo start is noise.
    automation.state.update(enabled=True, paused_until=None, last_switch=now - ago, high_since=None, pushed=False,
                            failsafe_done=False, failsafe_tried=False, stage_pause_done=False,
                            stage_pause_tried=False, stage_pause_pushed=True,
                            last_action={"action": action, "value": value, "time": now - ago})
    automation._save()
    # The feeding chart starts as a draft, as on a new run: two taps on "Matches my bottles" confirm the chart, and
    # since the demo's stage is only the estimate, the amounts follow once the stage is confirmed too (two taps on
    # the suggestion); "Fed" then fills the log. An earlier demo run's confirmation and log are gone.
    for path in (feeding.confirmation_path(), feeding.log_path()):
        path.unlink(missing_ok=True)
    camera = DemoCamera()
    return camera, DemoVision(camera)


def seed_demo_autopilot(pilot: Any, now: float | None = None) -> None:
    """A fresh autopilot on every demo start: off, with a made-up morning in its log so every kind of line shows (a
    would-be step from observing, a real step that can still be undone, a manual change, the mist lock, yesterday's
    summary). The demo's exhaust stands at 4, so the real step "3 to 4" is the one the undo button sends back."""
    from .autopilot import q, text, tk

    now = time.time() if now is None else now
    # The port assignment starts unnamed, as on a new run: the humidifier row asks what hangs on USB 2.
    for path in (pilot.log_path, pilot.state_path, pilot.guard.path()):
        path.unlink(missing_ok=True)
    pilot.state = pilot._defaults()
    pilot.state["base"] = 3
    vpd = text("ap.reason.vpd_low", value=q(0.94, "kPa", 2), limit=q(1.0, "kPa", 2))
    hum = text("ap.reason.hum_high", value=q(63.4, "%", 1), limit=q(60, "%", 1))
    yesterday = text("ap.summary", day={"tw": {"de": "Gestern", "en": "Yesterday"}}, steps=q(1), would=q(2),
                     undos=q(0), pauses=q(1), failed=q(0), t=q(96, "%", 0), h=q(84, "%", 0), v=q(71, "%", 0))
    lines = [
        (26 * 3600, {"kind": "summary", "day": "demo", "counts": {}, "text": yesterday}),
        (5 * 3600, {"kind": "mode", "text": text("ap.ev.mode_observe")}),
        (4 * 3600 + 40 * 60, {"kind": "event", "text": text("ap.ev.mist", probe=q(78.2, "%", 1), box=q(49.5, "%", 1))}),
        (4 * 3600, {"kind": "event", "text": text("ap.ev.mist_end")}),
        (3 * 3600, {"kind": "would", "device": "dfan", "from": 3, "to": 4, "text": text(
            "ap.step.would", device=tk("ap.dev.dfan"), old=q(3), new=q(4), reason=vpd)}),
        (2 * 3600, {"kind": "manual", "device": "light", "source": "app", "text": text(
            "ap.ev.manual_app", device=tk("ap.dev.light"), old=q(50, "%"), new=q(75, "%"), until={"at": now - 3600})}),
        (40 * 60, {"kind": "mode", "text": text("ap.ev.mode_auto")}),
        (2 * 60, {"kind": "step", "device": "dfan", "from": 3, "to": 4, "status": "sent", "text": text(
            "ap.step.done", device=tk("ap.dev.dfan"), old=q(3), new=q(4), reason=hum)}),
        (30, {"kind": "mode", "text": text("ap.ev.mode_off")}),
    ]
    for ago, entry in lines:
        line = pilot._log(entry, at=now - ago)
        if entry["kind"] == "step":
            pilot._log({"kind": "outcome", "of": line["id"], "status": "ok"}, at=now - ago + 30)
    pilot.state["last_summary"] = {"day": "demo", "counts": {}, "text": yesterday}
    pilot._save()


# ---------- the Durst-Index card's scenes (vgrow/thirst.py), for screenshots only ----------

# VGROW_DEMO_THIRST=<scene> seeds the demo's own state folder on start: a made-up pot, its log and a constant ET
# history, so each state of the card shows without waiting days. Unset: nothing is seeded, the card asks for the pot.
DEMO_THIRST_ENV = "VGROW_DEMO_THIRST"
THIRST_SCENES = ("nopot", "pot", "waiting", "ask", "due", "wethold", "skipped", "shortened")
# The pot as the owner entered his (27.10.2026): 3 US gallons, 3 x 3.785 l, about 11.4 l, diameter and height not
# measured. Only the scenes use it: a pot nobody entered stays unknown, the cockpit has no default pot. The flags and the
# scale are made up for the scenes.
DEMO_POT = {"volume_l": 11.4, "diameter_cm": None, "height_cm": None, "drain": True, "saucer": True, "feet": False,
            "wick": False, "lifts": False, "scale": True, "scale_step_g": 10.0}
DEMO_FULL_KG = 8.4  # made up: the 11.4 l pot weighed full and drained
DEMO_JUG = ((30, 22.0), (30, 22.5))  # made up: two jug runs of 30 s, within 10 % of each other


async def seed_demo_thirst(thirst: Any, box: Any, watering: Any, guard: Any, scene: str | None = None,
                           now: float | None = None) -> str | None:
    """Seed one scene of the Durst-Index card into the demo's state folder (VGROW_DEMO_THIRST or `scene`):
    nopot, pot (no anchor yet), waiting (anchored, about 45 %, only modelled), ask (about 92 %, modelled), due (a dry
    finger test a few minutes ago at about 95 %, lights on 90 min ago), wethold (2 l by hand onto a pot missing less),
    skipped (weighed at about 62 % an hour ago, the schedule's next slot two minutes ahead: the need check skips it),
    shortened (due, the pump switch confirmed, the flow measured, a long slot two minutes ahead: the need check shortens
    it). The owner's stage is early flower (set two days ago), as on the real box. Returns the scene or None."""
    import json

    from . import config
    from . import thirst as thirst_mod

    scene = scene or os.environ.get(DEMO_THIRST_ENV, "").strip().lower() or None
    if scene not in THIRST_SCENES:
        return None
    now = time.time() if now is None else now
    for path in (thirst.pot_path(), thirst.state_path(), thirst.log_path()):
        path.unlink(missing_ok=True)
    thirst.state = {}
    thirst.series = thirst_mod.Series()
    thirst._read_cache.clear()
    if scene == "nopot":
        thirst.save()
        return scene
    thirst.plant_log.set_stage("early_flower", since=(date.today() - timedelta(days=2)).isoformat())
    model = thirst_mod.load_model()
    snap = box.snapshot()
    level = float((snap.get("light") or {}).get("level") or 75)
    vpd_now = (snap.get("climate") or {}).get("probe_vpd")
    rate = thirst_mod.et_rate_ml_h(model, light=level, vpd=vpd_now, height_cm=thirst.height_cm(),
                                   last_lit=level) / 1000 / 3600
    d_trig = float(model("f_trig")) * float(model("a_w")) * DEMO_POT["volume_l"]

    def ago(ti: float) -> float:
        return now - ti / 100 * d_trig / rate

    pot = thirst_mod.Pot(**DEMO_POT, set_at=now - 8 * 86400, id="pot-demo")
    config.atomic_write(thirst.pot_path(), json.dumps(pot.as_dict(), ensure_ascii=False, indent=2) + "\n")
    thirst.log({"kind": "pot", "pot": pot.id, "litres": pot.volume()}, at=pot.set_at)
    thirst.series = thirst_mod.constant_series(rate, pot.set_at, now)
    thirst.state["inputs"] = {"at": now, "rate_l_s": rate, "light": level, "last_lit": level,
                              **({"vpd": vpd_now, "vpd_clear": vpd_now} if vpd_now is not None else {})}
    if scene in ("waiting", "ask"):
        thirst.log({"kind": "weigh", "kg": DEMO_FULL_KG, "full": True}, at=ago(45 if scene == "waiting" else 92))
    elif scene in ("due", "shortened"):
        thirst.log({"kind": "weigh", "kg": DEMO_FULL_KG, "full": True}, at=ago(95))
        thirst.log({"kind": "check", "answer": "dry", "depth_cm": 5.0}, at=now - 10 * 60)
        thirst.state["lights"] = {"on": True, "edge_at": now - 90 * 60}  # made up: the lamp came on 90 min ago
    elif scene == "wethold":
        thirst.log({"kind": "weigh", "kg": DEMO_FULL_KG, "full": True}, at=ago(55) - 10 * 3600)
        thirst.log({"kind": "water", "litres": 2.0, "origin": "hand"}, at=now - 10 * 3600)
    elif scene == "skipped":
        full = ago(130)
        thirst.log({"kind": "weigh", "kg": DEMO_FULL_KG, "full": True}, at=full)
        thirst.log({"kind": "weigh", "kg": round(DEMO_FULL_KG - 0.61 * d_trig, 2)}, at=now - 3600)
    if scene == "shortened":
        for seconds, ml in DEMO_JUG:
            thirst.log({"kind": "flow", "seconds": seconds, "ml": ml}, at=now - 3 * 3600)
    thirst.save()
    if scene in ("skipped", "shortened"):
        # the pump named on USB 1 and a slot two minutes ahead: the watering controller asks the need check then
        guard.assign("usb1", "pump", box.snapshot())
        slot = datetime.fromtimestamp(now + 150).astimezone(thirst.tz)
        await watering.configure({"duration_s": 3600 if scene == "shortened" else 120, "times": [f"{slot:%H:%M}"],
                                  "every_days": 1, "first_date": slot.date().isoformat(), "power_confirmed": True,
                                  "enabled": True, "timezone": "Europe/Berlin"})
    return scene
