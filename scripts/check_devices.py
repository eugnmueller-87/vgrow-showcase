"""One functional test of the box's devices through the cockpit's own command path. Stdlib only, on the Mac:
  .venv/bin/python scripts/check_devices.py --owner-asked --out /tmp/vgrow-test [--only light,cfan,dfan,humidifier]

Each step sends the owner's command, waits until the box itself reports the new value, and for the exhaust and the
humidifier also records what the climate sensors did. Every device goes back to its value from the start, also after an
error or a kill. A command pauses that device in the autopilot for an hour, the same as a tap on the page. The results go
to the device check of the water dashboard (vgrow/device_check.py).

The lamp goes dark for seconds and the exhaust stops for minutes, so it runs only when the owner asked for it. Look at the
camera picture first: on 06.11.2026 the records said the box was empty while a young plant stood in it. Above TEMP_MAX or
HUM_MAX it stops and restores everything. The pump is left out on purpose: USB power can mean water, so that test needs
the owner at the box with the hose in a measuring cup.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import pathlib
import signal
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8742"
CONFIRM_S = 45
SAMPLE_S = 30
EXHAUST_HOLD_S = 360
MIST_ON_S = 480
MIST_AFTER_S = 240
TEMP_MAX, HUM_MAX = 30.0, 70.0


class Cockpit:
    def __init__(self) -> None:
        key = (pathlib.Path.home() / ".vgrow-control" / "access-key").read_text(encoding="utf-8").strip()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.opener.open(f"{BASE}/?key={key}", timeout=15).read()

    def get(self, path: str) -> Any:
        return json.load(self.opener.open(BASE + path, timeout=20))

    def state(self) -> dict[str, Any]:
        return self.get("/api/state")

    def command(self, **body: Any) -> tuple[int, Any]:
        request = urllib.request.Request(BASE + "/api/cmd", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", "replace")[:300]

    def picture(self, path: pathlib.Path) -> None:
        try:
            with self.opener.open(f"{BASE}/camera/snapshot.jpg?camera=growcam", timeout=40) as response:
                path.write_bytes(response.read())
            say("picture", name=path.name, bytes=path.stat().st_size)
        except OSError as err:
            say("picture", name=path.name, error=type(err).__name__)


def say(kind: str, **fields: Any) -> None:
    print(json.dumps({"at": time.strftime("%H:%M:%S"), "kind": kind, **fields}, ensure_ascii=False), flush=True)


def reported(state: dict[str, Any], what: str) -> Any:
    if what.startswith("usb"):
        return (state.get("ports") or {}).get(what, {}).get("on")
    if what == "light":
        return (state.get("light") or {}).get("level")
    if what == "spectrum":
        return (state.get("light") or {}).get("spectrum")
    return (state.get(what) or {}).get("step")  # cfan, dfan


def switch(cockpit: Cockpit, what: str, target: Any, results: dict[str, dict[str, Any]] | None = None) -> bool:
    """One command, then the box's own report. True when the box reports the target in time."""
    ok = _switch(cockpit, what, target)
    if results is not None:
        device = "humidifier" if what.startswith("usb") else what
        row = results.setdefault(device, {"device": device, "command": True})
        row["command"] = row["command"] and ok[0]
        if ok[1] is not None:
            row["seconds"] = max(row.get("seconds", 0), ok[1])
    return ok[0]


def _switch(cockpit: Cockpit, what: str, target: Any) -> tuple[bool, float | None]:
    body: dict[str, Any]
    if what.startswith("usb"):
        body = {"action": "port", "port": what, "on": target}
    elif what == "light":
        body = {"action": "light", "level": target}
    elif what == "spectrum":
        body = {"action": "spectrum", "name": target}
    else:
        body = {"action": what, "step": target}
    started = time.monotonic()
    status, answer = cockpit.command(**body)
    if status != 200:
        say("refused", device=what, target=target, status=status, answer=answer)
        return False, None
    while time.monotonic() - started < CONFIRM_S:
        if reported(cockpit.state(), what) == target:
            seconds = round(time.monotonic() - started, 1)
            say("confirmed", device=what, target=target, seconds=seconds)
            return True, seconds
        time.sleep(2)
    say("no_confirmation", device=what, target=target, reported=reported(cockpit.state(), what))
    return False, None


def climate(cockpit: Cockpit) -> dict[str, Any]:
    state = cockpit.state()
    c = state.get("climate") or {}
    return {"temp": c.get("temp"), "hum": c.get("humidity"), "probe_temp": c.get("probe_temp"),
            "probe_hum": c.get("probe_humidity"), "sensors_age": state.get("sensors_age")}


def watch(cockpit: Cockpit, label: str, seconds: float) -> dict[str, Any]:
    """The climate readings while a device holds a value, the last ones returned. The box delivers new readings about
    every 90 s. Past the limits the test stops; the caller's finally restores everything."""
    end = time.monotonic() + seconds
    last = None
    while True:
        now = climate(cockpit)
        values = {k: v for k, v in now.items() if k != "sensors_age"}
        if values != last:
            say("climate", label=label, **now)
            last = values
        temps = [v for v in (now["temp"], now["probe_temp"]) if v is not None]
        hums = [v for v in (now["hum"], now["probe_hum"]) if v is not None]
        if (temps and max(temps) >= TEMP_MAX) or (hums and max(hums) >= HUM_MAX):
            say("limit", **now)
            raise SystemExit(4)
        left = end - time.monotonic()
        if left <= 0:
            return now
        time.sleep(min(SAMPLE_S, left))


def test_light(cockpit: Cockpit, out: pathlib.Path, results: dict[str, dict[str, Any]]) -> None:
    cockpit.picture(out / "light-start.jpg")
    for name, what, target in (("light-100", "light", 100), ("spectrum-veg", "spectrum", "veg"),
                               ("spectrum-seedling", "spectrum", "seedling"), ("spectrum-bloom", "spectrum", "bloom"),
                               ("light-0", "light", 0)):
        if switch(cockpit, what, target, results):
            time.sleep(8)  # the camera's exposure settles
            cockpit.picture(out / f"{name}.jpg")


def test_cfan(cockpit: Cockpit, results: dict[str, dict[str, Any]]) -> None:
    for step in (5, 10, 0):
        if switch(cockpit, "cfan", step, results):
            time.sleep(15)


def test_dfan(cockpit: Cockpit, results: dict[str, dict[str, Any]]) -> None:
    watch(cockpit, "before", 100)
    if switch(cockpit, "dfan", 10, results):
        watch(cockpit, "exhaust 10", EXHAUST_HOLD_S)
    if switch(cockpit, "dfan", 0, results):
        watch(cockpit, "exhaust 0", EXHAUST_HOLD_S)


def test_humidifier(cockpit: Cockpit, port: str, results: dict[str, dict[str, Any]]) -> None:
    before = watch(cockpit, "before", 100)
    if switch(cockpit, port, True, results):
        after = watch(cockpit, "humidifier on", MIST_ON_S)
        if before["probe_hum"] is not None and after["probe_hum"] is not None:
            rise = after["probe_hum"] - before["probe_hum"]
            results["humidifier"].update(before=before["probe_hum"], after=after["probe_hum"], unit="%", effect=rise >= 2)
    if switch(cockpit, port, False, results):
        watch(cockpit, "humidifier off", MIST_AFTER_S)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-asked", action="store_true", help="the owner asked for this test and has seen the box")
    parser.add_argument("--out", default="/tmp/vgrow-test")
    parser.add_argument("--only", default="light,cfan,dfan,humidifier")
    args = parser.parse_args()
    if not args.owner_asked:
        say("refused", why="the lamp and the fans switch: run it only with --owner-asked, after a look at the picture")
        return 2
    wanted = args.only.split(",")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cockpit = Cockpit()
    results: dict[str, dict[str, Any]] = {}

    state = cockpit.state()
    if state.get("online") is not True or state.get("mqtt") is not True or state.get("pending"):
        say("refused", why="box offline or a command still pending", pending=state.get("pending"))
        return 2
    if (state.get("light_schedule") or {}).get("settings", {}).get("enabled"):
        say("refused", why="the light schedule is on: level 0 would stop it")
        return 2
    humidifier = next((name for name, port in (state.get("ports") or {}).items()
                       if "humid" in str(port.get("label")).lower()), None)
    start = {what: reported(state, what) for what in ("light", "spectrum", "cfan", "dfan")}
    if humidifier:
        start[humidifier] = reported(state, humidifier)
    say("start", values=start, climate=climate(cockpit), soil=state.get("soil"))

    def stop(_signum: int, _frame: Any) -> None:
        raise SystemExit(3)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        if "light" in wanted:
            test_light(cockpit, out, results)
            switch(cockpit, "light", start["light"])
        if "cfan" in wanted:
            test_cfan(cockpit, results)
            switch(cockpit, "cfan", start["cfan"])
        if "dfan" in wanted:
            test_dfan(cockpit, results)
            switch(cockpit, "dfan", start["dfan"])
        if "humidifier" in wanted and humidifier:
            test_humidifier(cockpit, humidifier, results)
    finally:
        now = cockpit.state()
        for what, value in start.items():
            if reported(now, what) != value:
                switch(cockpit, what, value)
        now = cockpit.state()
        say("end", values={what: reported(now, what) for what in start}, climate=climate(cockpit), soil=now.get("soil"))
        for row in results.values():
            status, answer = cockpit.command(action="device_check_record", **row)
            if status != 200:
                say("not_recorded", device=row["device"], status=status, answer=answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
