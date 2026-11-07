"""Screenshots of the Durst-Index card and of Hank's watering answers, ONLY against isolated local demos.

    python scripts/thirst_screens.py <out-dir> [--scenes nopot,pot,waiting,ask,due,wethold,skipped] [--devtools 9372]
                                     [--no-hank]

Each scene gets a demo server of its own (python -m vgrow --demo on a free port of 127.0.0.1 with
VGROW_DEMO_THIRST=<scene>, vgrow/demo.py seed_demo_thirst), with USERPROFILE and LOCALAPPDATA on a new empty temporary
folder that also holds the Edge profile.
Headless Edge draws the card on the Automatik page at phone 390 and desktop 1280 width, in German and English, light and
dark. The scene "skipped" first waits until the schedule's slot has come and the need check skipped it, and adds the
watering card with its history. In the scene "ask" Hank answers the four watering questions. Afterwards the server
stops and the temporary folder is deleted. No live box and no cloud account are used; nothing is written into the
repository, the pictures go to <out-dir>.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from screenshots import EDGE, Cdp, prefs_script  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

SCENES = ("nopot", "pot", "waiting", "ask", "due", "wethold", "skipped")
SIZES = (("phone", 390, 844, True), ("desktop", 1280, 900, False))
HANK_SIZES = (("phone", 390, 1500, True), ("desktop", 1280, 1300, False))  # tall enough for a whole answer
QUESTIONS = {"de": ("Muss ich gießen?", "Wann gieße ich?", "Wie viel gieße ich?", "Der Tank ist voll, soll ich gießen?"),
             "en": ("Do I need to water?", "When should I water?", "How much should I water?",
                    "The tank is full, should I water?")}
SLUGS = ("muss", "wann", "wieviel", "tank")
PAD = 8

RECT_JS = """((sel) => {
  const el = document.querySelector(sel);
  if (!el || el.hidden || !el.getClientRects().length) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height };
})(%s)"""

ASK_JS = """(async (q) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  document.querySelector('.hk-fab').click();
  await wait(500);
  const bots = () => document.querySelectorAll('.hk-log .hk-msg.hk-bot').length;
  const before = bots();
  const field = document.querySelector('#hk-q');
  field.value = q;
  field.form.requestSubmit();
  const until = Date.now() + 10000;
  while (Date.now() < until && (bots() <= before || document.querySelector('.hk-log .hk-typing'))) await wait(200);
  await wait(900);
  return bots() > before;
})(%s)"""


def js(cdp: Cdp, expression: str) -> Any:
    result = cdp.cmd("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
    if "exceptionDetails" in result:
        error = result["exceptionDetails"]
        raise AssertionError(error.get("exception", {}).get("description", error["text"]))
    return result["result"].get("value")


def wait_for(cdp: Cdp, expression: str, seconds: float = 20) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if js(cdp, expression):
            return
        cdp.pump(0.25)
    raise AssertionError(f"timed out: {expression}")


def shot(cdp: Cdp, path: Path, clip: dict[str, float] | None = None, *, beyond: bool = True) -> None:
    """A clip of the page (beyond the viewport), or with beyond=False what the screen shows (Hank's drawer is fixed:
    a capture beyond the viewport would stretch it over the whole page)."""
    params: dict[str, Any] = {"format": "png", "captureBeyondViewport": beyond}
    if clip:
        params["clip"] = {**clip, "scale": 1}
    path.write_bytes(base64.b64decode(cdp.cmd("Page.captureScreenshot", **params)["data"]))
    print("wrote", path.name, clip and (round(clip["width"]), round(clip["height"])))


class Demo:
    """One demo server with its own temporary home on a free local port, stopped and deleted on exit (also when it
    does not come up)."""

    def __init__(self, scene: str) -> None:
        self.scene = scene
        with socket.socket() as probe:  # a port nobody listens on: another tool may hold the default ones
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.home = Path(tempfile.mkdtemp(prefix="vgrow-thirst-"))
        self.proc: subprocess.Popen[bytes] | None = None
        self._log: Any = None

    def __enter__(self) -> Demo:
        env = {**os.environ, "USERPROFILE": str(self.home), "LOCALAPPDATA": str(self.home), "HOME": str(self.home),
               "VGROW_DEMO_THIRST": self.scene, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        for name in ("VGROW_DEMO_WATER", "VGROW_DEMO_CLIMATE"):
            env.pop(name, None)
        try:
            self._log = (self.home / "server.log").open("wb")
            self.proc = subprocess.Popen([sys.executable, "-m", "vgrow", "--demo", "--port", str(self.port), "--host",
                                          "127.0.0.1"], cwd=REPO, env=env, stdout=self._log, stderr=subprocess.STDOUT)
            if self._ready():
                return self
            tail = (self.home / "server.log").read_text(encoding="utf-8", errors="replace")[-3000:]
        except BaseException:
            self.__exit__()
            raise
        self.__exit__()
        raise SystemExit(f"the demo server did not answer ({self.scene}):\n{tail}")

    def _ready(self) -> bool:
        key_file = self.home / "vgrow-control" / "demo" / "access-key"
        end = time.monotonic() + 120
        while time.monotonic() < end and self.proc is not None and self.proc.poll() is None:
            if key_file.exists():
                key = key_file.read_text(encoding="utf-8").strip()
                request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/state",
                                                 headers={"Cookie": f"vgrow_key={key}"})
                try:
                    with urllib.request.urlopen(request, timeout=3) as resp:
                        data = json.loads(resp.read())
                        if resp.status == 200 and data.get("demo") is True and data.get("thirst"):
                            return True
                except (OSError, ValueError):
                    pass
            time.sleep(0.5)
        return False

    @property
    def url(self) -> str:
        key = (self.home / "vgrow-control" / "demo" / "access-key").read_text(encoding="utf-8").strip()
        return f"http://127.0.0.1:{self.port}/?key={key}"

    def __exit__(self, *_: Any) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self._log is not None:
            self._log.close()
            self._log = None
        for _ in range(10):  # Edge's helpers or the server may hold a file a moment longer
            shutil.rmtree(self.home, ignore_errors=True)
            if not self.home.exists():
                break
            time.sleep(1)
        print("removed" if not self.home.exists() else "COULD NOT REMOVE", self.home)


def edge(profile: Path, port: int) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2).read())
            return proc, next(t for t in targets if t["type"] == "page")
        except (OSError, StopIteration):
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("Edge did not start")


def open_page(cdp: Cdp, url: str, lang: str, theme: str, script: list[str]) -> None:
    if script:
        cdp.cmd("Page.removeScriptToEvaluateOnNewDocument", identifier=script.pop())
    script.append(cdp.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script(lang, theme))["identifier"])
    cdp.cmd("Page.navigate", url=url + "#automation")
    wait_for(cdp, "typeof state==='object' && state && !!state.thirst && !!document.querySelector('#thirst .th-body')"
                  " && document.querySelector('#thirst .th-body').children.length > 0")
    js(cdp, "document.fonts.ready.then(() => true)")
    cdp.pump(0.6)


def card(cdp: Cdp, out: Path, name: str, selector: str, width: int) -> None:
    js(cdp, f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block:'start'}})")
    cdp.pump(0.3)
    rect = js(cdp, RECT_JS % json.dumps(selector))
    assert rect, (name, selector)
    assert js(cdp, "document.documentElement.scrollWidth <= innerWidth"), (name, "horizontal scroll")
    x = max(0.0, rect["x"] - PAD)
    shot(cdp, out / f"{name}.png", {"x": x, "y": max(0.0, rect["y"] - PAD), "width": min(width - x, rect["width"] + 2 * PAD),
                                    "height": rect["height"] + 2 * PAD})


def scene_shots(cdp: Cdp, out: Path, demo: Demo, hank: bool, errors: list[str]) -> None:
    script: list[str] = []
    scene = demo.scene
    if scene == "skipped":  # the slot lies about two minutes after the start: wait for the need check's word
        open_page(cdp, demo.url, "de", "light", script)  # the page polls every 5 s by itself
        wait_for(cdp, "!!state.watering && state.watering.events.some((e) => e.kind === 'skipped' && "
                      "e.reason === 'thirst')", seconds=260)
    for lang in ("de", "en"):
        for theme in ("light", "dark"):
            for size, width, height, mobile in SIZES:
                cdp.cmd("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=2,
                        mobile=mobile)
                cdp.cmd("Emulation.setTouchEmulationEnabled", enabled=mobile, maxTouchPoints=5 if mobile else 1)
                open_page(cdp, demo.url, lang, theme, script)
                kind = js(cdp, "state.thirst.decision.kind + '/' + state.thirst.decision.reason")
                card(cdp, out, f"{scene}-{size}-{lang}-{theme}", "#thirst", width)
                if scene == "skipped":
                    js(cdp, "document.querySelector('#watering details.feed-more').open = true")
                    card(cdp, out, f"{scene}-schedule-{size}-{lang}-{theme}", "#watering", width)
                print(scene, size, lang, theme, kind)
    if hank:
        for lang in ("de", "en"):
            for theme in ("light", "dark"):
                for size, width, height, mobile in HANK_SIZES:
                    cdp.cmd("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=2,
                            mobile=mobile)
                    cdp.cmd("Emulation.setTouchEmulationEnabled", enabled=mobile, maxTouchPoints=5 if mobile else 1)
                    for slug, question in zip(SLUGS, QUESTIONS[lang], strict=True):
                        open_page(cdp, demo.url, lang, theme, script)
                        wait_for(cdp, "!!document.querySelector('.hk-fab')")
                        assert js(cdp, ASK_JS % json.dumps(question)), ("no answer", question)
                        shot(cdp, out / f"hank-{slug}-{size}-{lang}-{theme}.png", beyond=False)
    assert not errors, errors


def run(out: Path, scenes: list[str], devtools: int, hank: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for scene in scenes:
        with Demo(scene) as demo:
            errors: list[str] = []
            proc, page = edge(demo.home / "edge", devtools)
            try:
                with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
                    cdp = Cdp(ws)
                    cdp.handlers["Runtime.exceptionThrown"] = lambda e: errors.append(
                        e["exceptionDetails"].get("exception", {}).get("description", e["exceptionDetails"]["text"]))
                    for domain in ("Page", "Runtime"):
                        cdp.cmd(domain + ".enable")
                    assert js(cdp, "true")
                    scene_shots(cdp, out, demo, hank and scene == "ask", errors)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                time.sleep(1.5)  # Edge's helper processes let go of the profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--scenes", default=",".join(SCENES), help="comma-separated scenes of vgrow/demo.py")
    parser.add_argument("--devtools", type=int, default=9372, help="Edge's DevTools port (default 9372)")
    parser.add_argument("--no-hank", action="store_true", help="only the card, no Hank answers")
    args = parser.parse_args()
    scenes = [s.strip() for s in args.scenes.split(",") if s.strip()]
    unknown = [s for s in scenes if s not in SCENES]
    if unknown:
        parser.error(f"unknown scene: {', '.join(unknown)}")
    run(args.out_dir, scenes, args.devtools, not args.no_hank)


if __name__ == "__main__":
    main()
