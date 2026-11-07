"""Screenshots of the live VGrow Cockpit with real phone emulation, via Edge's DevTools protocol.

Usage (PC, SSH tunnel to the server first):
  ssh -f -N -L 18742:127.0.0.1:8742 <mac>
  VGROW_URL="http://localhost:18742/?key=<access-key>" .venv/Scripts/python.exe scripts/screenshots.py <out-dir>
      [--lang de|en] [--theme light|dark|auto] [--width 390] [--port 9333] [--storage KEY=VALUE ...]
Headless Edge cannot render narrower than ~500 px with --window-size, hence device emulation.
The URL (with access key) comes from the VGROW_URL env var and is never printed.
Language and theme go into localStorage before the page loads; every file name carries both. Pictures the
server draws (the demo camera and photos) follow the page language too: their URLs carry ?lang=.
--width sets the phone width (360 for a small phone), --port the DevTools port, so two runs can go side by side.
--storage puts more localStorage entries in place first, e.g. vgrow.feedLiters=1.5 for the can's litres.
"""
from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

EDGE = os.environ.get("EDGE_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
PORT = 9333  # DevTools port; --port overrides it
PHONE = (390, 844)  # --width overrides the width
DESKTOP = (1440, 1000)
PAD = 8

# Resolves when the first state is rendered, the charts are drawn (or 12 s passed), the fonts are
# loaded and every image has either loaded or failed. A live MJPEG image never "completes": capped.
READY_JS = """(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = Date.now() + 12000;
  while (Date.now() < until) {
    if (typeof state === 'object' && state !== null && document.querySelector('#ch-climate .uplot')) break;
    await wait(250);
  }
  await document.fonts.ready;
  for (const i of document.querySelectorAll('img[loading=lazy]')) i.loading = 'eager';  // off-screen strip photos too
  const open = [...document.images].filter((i) => i.getAttribute('src') && !i.complete);
  await Promise.race([
    Promise.all(open.map((i) => new Promise((r) => {
      i.addEventListener('load', r, { once: true });
      i.addEventListener('error', r, { once: true });
    }))),
    wait(6000),
  ]);
  await wait(500);
  return true;
})()"""

RECT_JS = """((sel) => {
  const el = document.querySelector(sel);
  if (!el || el.hidden || !el.getClientRects().length) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height };
})(%s)"""

# Where to cut a desktop shot: at a card edge, so no card is sliced in half. The lowest edge above the
# limit, unless that leaves less than two thirds of a screen; then the first edge below it.
CUT_JS = """((limit) => {
  const edges = [...document.querySelectorAll('main .card, main .tiles, main .statusline')]
    .filter((el) => !el.hidden && el.getClientRects().length)
    .map((el) => el.getBoundingClientRect().bottom + scrollY)
    .sort((a, b) => a - b);
  const above = edges.filter((b) => b <= limit);
  const below = edges.filter((b) => b > limit);
  if (above.length && above[above.length - 1] >= limit * 0.66) return above[above.length - 1];
  return below.length ? Math.min(below[0], limit * 2) : limit;
})(%d)"""


class Cdp:
    """A minimal DevTools client: commands, plus events handed to handlers while it waits."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.responses: dict[int, dict[str, Any]] = {}

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg:
            # A handler may send a nested command (e.g. fulfill an iframe request). Keep any outer reply.
            self.responses[msg["id"]] = msg
            return
        handler = self.handlers.get(msg.get("method", ""))
        if handler is not None:
            handler(msg.get("params", {}))

    def cmd(self, method: str, **params: Any) -> dict[str, Any]:
        i = next(self.ids)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            msg = self.responses.pop(i) if i in self.responses else json.loads(self.ws.recv())
            if msg.get("id") == i:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self._dispatch(msg)

    def pump(self, seconds: float) -> None:
        """Wait while still answering events (an intercepted request would otherwise stall the page)."""
        end = time.time() + seconds
        while (left := end - time.time()) > 0:
            try:
                msg = json.loads(self.ws.recv(timeout=left))
            except TimeoutError:
                break
            self._dispatch(msg)

    def js(self, expr: str, *, wait: bool = False) -> Any:
        res = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=wait)
        return res["result"].get("value")


class Shooter:
    def __init__(self, cdp: Cdp, out: Path, tag: str, phone: tuple[int, int] = PHONE) -> None:
        self.cdp, self.out, self.tag, self.phone = cdp, out, tag, phone

    def emulate(self, width: int, height: int, *, mobile: bool) -> None:
        self.cdp.cmd("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=2, mobile=mobile)
        # Touch emulation makes (hover: none) match, as on a real phone (bigger switches, no hover).
        self.cdp.cmd("Emulation.setTouchEmulationEnabled", enabled=mobile, maxTouchPoints=5 if mobile else 1)

    def load(self, url: str) -> None:
        self.cdp.cmd("Page.navigate", url=url)
        self.cdp.pump(1.5)
        self.cdp.js(READY_JS, wait=True)

    def rect(self, selector: str) -> dict[str, float] | None:
        return self.cdp.js(RECT_JS % json.dumps(selector))

    def shot(self, name: str, clip: dict[str, float] | None = None) -> None:
        params: dict[str, Any] = {"format": "png", "captureBeyondViewport": True}
        if clip:
            params["clip"] = {**clip, "scale": 1}
        data = self.cdp.cmd("Page.captureScreenshot", **params)["data"]
        path = self.out / f"{name}-{self.tag}.png"
        path.write_bytes(base64.b64decode(data))
        print("wrote", path.name, clip and (round(clip["width"]), round(clip["height"])))

    def shot_of(self, name: str, first: str, last: str | None = None, width: int | None = None) -> None:
        """One element, or the span from one element to another, measured right before the shot.
        The width defaults to this run's phone width, read now, not when the function was defined."""
        top = self.rect(first)
        bottom = self.rect(last) if last else top
        if top is None or bottom is None:
            print("skipped", name, "(not shown)")
            return
        y = top["y"] - PAD
        self.shot(name, {"x": 0, "y": y, "width": width or self.phone[0],
                         "height": bottom["y"] + bottom["height"] + PAD - y})

    def full(self, name: str, width: int, *, mobile: bool) -> None:
        # A viewport as tall as the page: fixed bars (the desktop side bar) then span the whole shot.
        self.emulate(width, self.cdp.js("document.documentElement.scrollHeight"), mobile=mobile)
        self.cdp.pump(0.8)
        self.shot(name, {"x": 0, "y": 0, "width": width, "height": self.cdp.js("document.documentElement.scrollHeight")})


def prefs_script(lang: str, theme: str, storage: dict[str, str] | None = None) -> str:
    values = json.dumps({**(storage or {}), "vgrow.lang": lang, "vgrow.theme": theme})
    return f"try {{ for (const [k, v] of Object.entries({values})) localStorage.setItem(k, v); }} catch (e) {{}}"


def run(url: str, out: Path, lang: str, theme: str, setup: Callable[[Cdp], None] | None = None, *,
        width: int = PHONE[0], port: int = PORT, storage: dict[str, str] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    phone = (width, PHONE[1])
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={port}", f"--user-data-dir={out / 'prof-cdp'}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(40):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        if page is None:
            raise SystemExit("Edge did not start")
        with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = Cdp(ws)
            cdp.cmd("Page.enable")
            cdp.cmd("Runtime.enable")
            cdp.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script(lang, theme, storage))
            if setup is not None:
                setup(cdp)
            s = Shooter(cdp, out, f"{lang}-{theme}", phone)
            # ---- phone ----
            s.emulate(*phone, mobile=True)
            s.load(url)
            s.shot("phone-overview", {"x": 0, "y": 0, "width": phone[0], "height": phone[1]})  # the first screen
            s.shot_of("phone-camera", "#card-cam")
            s.shot_of("phone-plant", "#card-plant")
            s.shot_of("phone-feeding", "#feed")
            s.shot_of("phone-autopilot", "#autopilot")
            s.shot_of("phone-controls", "#card-light", "#card-ports")
            s.shot_of("phone-history", "#verlauf", "#card-history")
            s.shot_of("phone-alerts", "#alarme", "#card-alerts")
            s.full("phone-full", phone[0], mobile=True)
            # ---- desktop, loaded fresh so the page draws for a mouse, not for touch ----
            s.emulate(*DESKTOP, mobile=False)
            s.load(url)
            cut = round(cdp.js(CUT_JS % (DESKTOP[1] + 120)) + 24)
            s.emulate(DESKTOP[0], cut, mobile=False)
            cdp.pump(0.8)
            s.shot("desktop-top", {"x": 0, "y": 0, "width": DESKTOP[0], "height": cut})
            s.shot_of("desktop-feeding", "#feed", width=DESKTOP[0])
            s.full("desktop-full", DESKTOP[0], mobile=False)
    finally:
        proc.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--lang", choices=("de", "en"), default="de")
    parser.add_argument("--theme", choices=("light", "dark", "auto"), default="light")
    parser.add_argument("--width", type=int, default=PHONE[0], help="phone width in CSS px (default 390)")
    parser.add_argument("--port", type=int, default=PORT, help="DevTools port (default 9333)")
    parser.add_argument("--storage", action="append", default=[], metavar="KEY=VALUE",
                        help="one more localStorage entry before the page loads (repeatable)")
    args = parser.parse_args()
    storage = dict(item.split("=", 1) for item in args.storage if "=" in item)
    run(os.environ["VGROW_URL"], args.out_dir, args.lang, args.theme, width=args.width, port=args.port,
        storage=storage)


if __name__ == "__main__":
    main()
