"""Screenshots of Hank for the layout check before acceptance (spec WP10, memory "seiten-headless-rendern-vor-abnahme").

Usage (PC with Edge): .venv/Scripts/python.exe scripts/hank_screens.py <out-dir> [--lang de|en] [--live] [--port 9334]
                      [--only answer,lamp]

Two sources, both opened in headless Edge over the DevTools protocol (phone 390x844 with touch, desktop 1280x800), in
light and dark:

- the preview (default): scripts/build_preview.py into <out-dir>, answers from scripts/preview_mock.js, which
  window.HANK_PREVIEW pins, so every state can be shown: closed, open and empty, an answer with sources and thumbs,
  a question back, suggestion cards, the typing line, off, and the traffic light green, yellow and red.
- the real API (--live): the demo cockpit (vgrow.demo, made-up box, own state folder in <out-dir>) with the real Hank
  (vgrow/hank/api.register) on 127.0.0.1, and the eval set's fake Ollama on the next port, which answers after
  LIVE_MODEL_SECONDS so the typing line and the revised answer can be seen. The light shows what the demo box gives;
  it cannot be pinned. This is the cockpit's own make_app() plus Hank's routes, the way serve() will put them
  together (WP9); it needs no change in vgrow/server.py and never logs in to VIVOSUN.

A test with jsdom sees no layout: these pictures are looked at by a person.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_preview  # noqa: E402
from screenshots import EDGE, Cdp, prefs_script  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

PHONE = (390, 844)
DESKTOP = (1280, 800)
LIVE_MODEL_SECONDS = 14.0  # the fake model's answer time in --live: past 10 s the typing line counts seconds
QUESTIONS = {
    "de": {"humidity": "Ist die Luftfeuchte gerade okay?", "clarify": "Meine Pomate hat gelbe Blätter",
           "light": "Dreh bitte das Licht auf 100 Prozent hoch", "humidifier": "Warum ist der Befeuchter aus?",
           "offtopic": "Wie wird das Wetter morgen in Berlin?", "typo": "Ist die Fehcte okay?",
           "warm": "Wie warm ist es?", "damp": "Wie feucht ist es?", "lamp": "Wie ist das Licht?",
           "feed": "Wann dünge ich das nächste Mal?", "tomato": "Meine Tomahte hat gelbe Bläter",
           "fix": "Kannst du das beheben?", "explain": "Was ist VPD?", "fixit": "Kannst du das beheben?",
           "height": "Wie hoch ist die Pflanze?", "lolli": "Was ist Lollipopping?",
           "bloomday": "Welcher Tag der Blüte ist heute?", "howto": "Wie mache ich Lollipopping?",
           "exhaust": "Wie hoch ist die Abluft gerade?"},
    "en": {"humidity": "Is the humidity okay right now?", "clarify": "My pomato has yellow leaves",
           "light": "Please turn the light up to 100 percent", "humidifier": "Why is the humidifier off?",
           "offtopic": "What is the weather tomorrow in Berlin?", "typo": "Is the humidty okay?",
           "warm": "How warm is it?", "damp": "How humid is it?", "lamp": "How is the light?",
           "feed": "When do I feed the next time?", "tomato": "My tomahto has yellow leafs",
           "fix": "can you fix those things?", "explain": "what is VPD", "fixit": "can u fix it?",
           "height": "How tall is my plant?", "lolli": "What is lollipopping?",
           "bloomday": "Which flowering day is it today?", "howto": "How do I do LST?",
           "exhaust": "How high is the exhaust fan right now?"},
}

# Resolves once Hank is drawn (button or the switch-on button), the fonts are there and the charts had a moment.
READY_JS = """(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = Date.now() + 15000;
  while (Date.now() < until && !document.querySelector('.hk-fab, .hk-wake')) await wait(200);
  await document.fonts.ready;
  await wait(800);
  return !!document.querySelector('.hk-fab, .hk-wake');
})()"""

OPEN_JS = """(async () => {
  document.querySelector('.hk-fab').click();
  await new Promise((r) => setTimeout(r, 400));
  return true;
})()"""

ASK_JS = """(async (q, waitMs) => {
  const q2 = document.querySelector('#hk-q');
  q2.value = q;
  q2.form.requestSubmit();
  await new Promise((r) => setTimeout(r, waitMs));
  return true;
})(%s, %d)"""

# The first tap on a two-tap card: it then asks for the second one.
ARM_JS = """(() => {
  const cards = [...document.querySelectorAll('.hk-prop')];
  const b = cards.length && cards[cards.length - 1].querySelector('.btn.primary');
  if (b) b.click();
  return !!b;
})()"""

# "Nicht jetzt" on the newest card: the consequence comes back as Hank's own message.
SKIP_JS = """(async () => {
  const cards = [...document.querySelectorAll('.hk-prop')];
  const b = cards.length && cards[cards.length - 1].querySelector('.btn:not(.primary)');
  if (b) b.click();
  await new Promise((r) => setTimeout(r, 900));
  return !!b;
})()"""

# "Mehr aus der Kartei" opened on the newest answer (acceptance 23.10.2026: the bubble stays short, the rest folds).
MORE_JS = """(async () => {
  const all = [...document.querySelectorAll('.hk-more')];
  const d = all[all.length - 1];
  if (d) { d.open = true; d.scrollIntoView({ block: 'start' }); }
  await new Promise((r) => setTimeout(r, 300));
  return !!d;
})()"""

# A long answer opens at its first line; this shows its end (the typing line, the revised mark, the cards).
END_JS = """(() => {
  const log = document.querySelector('.hk-log');
  if (log) log.scrollTop = log.scrollHeight;
  return !!document.querySelector('.hk-typing .hk-secs, .hk-revised');
})()"""

LIGHT_JS = """(() => {
  const el = document.querySelector('.hk-light');
  if (!el || el.hidden) return false;
  el.scrollIntoView({ block: 'center' });
  return true;
})()"""

# A tap on the traffic-light line (owner, 23.10.2026: "I need to be able to click on it. And also to fix the
# problem."): the drawer opens with "Was kann ich dagegen tun?" and Hank's fix answer with its cards.
# v1.2: the plant card's field "Blütebeginn" (its note says "geschätzt" while only the camera's estimate stands)
PLANT_JS = """(async () => {
  const el = document.getElementById('plant-bloom-row');
  if (!el) return false;
  await new Promise((r) => setTimeout(r, 1500));  // the photo strip loads late and pushes the row down
  el.scrollIntoView({ block: 'center' });
  await new Promise((r) => setTimeout(r, 300));
  return true;
})()"""

TAP_JS = """(async () => {
  const b = document.querySelector('.hk-light-btn');
  if (!b || b.closest('.hk-light').hidden) return false;
  b.click();
  await new Promise((r) => setTimeout(r, 1500));
  return true;
})()"""

# The "Beheben" button of the newest note in the open drawer.
NOTE_FIX_JS = """(async () => {
  document.querySelector('.hk-fab').click();
  await new Promise((r) => setTimeout(r, 400));
  const all = [...document.querySelectorAll('.hk-hintmsg .hk-fix')];
  const b = all[all.length - 1];
  if (b) b.click();
  await new Promise((r) => setTimeout(r, 1500));
  return !!b;
})()"""

# The mode switch in the drawer's head: "aus" twice (the first tap shows what it means, acceptance 23.10.2026), then
# the small button to switch on again is all that is left.
OFF_JS = """(async () => {
  document.querySelector('.hk-fab').click();
  await new Promise((r) => setTimeout(r, 300));
  document.querySelector('.hk-seg [data-mode="off"]').click();
  await new Promise((r) => setTimeout(r, 300));
  document.querySelector('.hk-seg [data-mode="off"]').click();
  await new Promise((r) => setTimeout(r, 900));
  return !!document.querySelector('.hk-wake:not([hidden])');
})()"""

# The first tap on "aus": the sentence about what it means, and the request for a second tap.
OFF_ARMED_JS = """(async () => {
  document.querySelector('.hk-fab').click();
  await new Promise((r) => setTimeout(r, 300));
  document.querySelector('.hk-seg [data-mode="off"]').click();
  await new Promise((r) => setTimeout(r, 300));
  return !document.querySelector('#hk-mode-hint').hidden;
})()"""

# "nur Warnungen": the field is closed and the greeting says why.
WARN_JS = """(async () => {
  document.querySelector('.hk-fab').click();
  await new Promise((r) => setTimeout(r, 300));
  document.querySelector('.hk-seg [data-mode="warnings"]').click();
  await new Promise((r) => setTimeout(r, 900));
  return document.querySelector('.hk-q').disabled;
})()"""
BACK_ON_JS = """(async () => {
  const b = document.querySelector('.hk-seg [data-mode="on"]');
  if (b) b.click();
  await new Promise((r) => setTimeout(r, 900));
  return true;
})()"""

# Back on after the "off" picture, so the next page load of the live server has Hank again.
ON_JS = """(async () => {
  const b = document.querySelector('.hk-wake');
  if (b && !b.hidden) b.click();
  await new Promise((r) => setTimeout(r, 900));
  return !!document.querySelector('.hk-fab:not([hidden])');
})()"""

Step = tuple[str, Any]


def _ask(text: str, ms: int) -> Step:
    return ("js", ASK_JS % (json.dumps(text), ms))


def states(lang: str) -> list[tuple[str, dict[str, Any], list[Step]]]:
    """The preview's states: (name, HANK_PREVIEW, steps); a step is ("js", code) or ("wait", seconds)."""
    q = QUESTIONS[lang]
    return [
        ("closed", {"light": "green", "hum": 55}, []),
        ("closed-unread", {"light": "yellow", "hum": 64, "hint": True}, []),
        ("light-green", {"light": "green", "hum": 55}, [("js", LIGHT_JS)]),
        ("light-yellow", {"light": "yellow", "hum": 64}, [("js", LIGHT_JS)]),
        ("light-red", {"light": "red", "hum": 82}, [("js", LIGHT_JS)]),
        ("light-tap", {"light": "yellow", "hum": 64}, [("js", TAP_JS)]),
        ("light-tap-green", {"light": "green", "hum": 55}, [("js", TAP_JS)]),
        ("note-fix", {"light": "yellow", "hum": 64, "hint": True}, [("js", NOTE_FIX_JS)]),
        ("open-empty", {"light": "green", "hum": 55}, [("js", OPEN_JS)]),
        ("open-note", {"light": "yellow", "hum": 64, "hint": True}, [("js", OPEN_JS)]),
        ("answer", {"light": "yellow", "hum": 64}, [("js", OPEN_JS), _ask(q["humidity"], 7000)]),
        ("typing", {"light": "yellow", "hum": 64, "slow": True, "elapsedOffset": 12},
         [("js", OPEN_JS), _ask(q["humidity"], 2500)]),
        ("clarify", {"light": "green", "hum": 55}, [("js", OPEN_JS), _ask(q["clarify"], 900)]),
        ("offtopic", {"light": "green", "hum": 55}, [("js", OPEN_JS), _ask(q["offtopic"], 900)]),
        ("proposal", {"light": "yellow", "hum": 64},
         [("js", OPEN_JS), _ask(q["light"], 900), _ask(q["humidifier"], 900), ("js", ARM_JS), ("wait", 0.3)]),
        ("declined", {"light": "yellow", "hum": 64}, [("js", OPEN_JS), _ask(q["light"], 900), ("js", SKIP_JS)]),
        ("off", {"mode": "off"}, []),
    ]


def live_states(lang: str) -> list[tuple[str, dict[str, Any], list[Step]]]:
    """The real API's states. The server keeps its state between pictures; "off" switches Hank on again after."""
    q = QUESTIONS[lang]
    wait_typing = int((LIVE_MODEL_SECONDS - 2.5) * 1000)  # the typing line with its seconds, before the model is done
    wait_revised = int((LIVE_MODEL_SECONDS + 4) * 1000)
    return [
        ("closed", {}, []),
        ("light", {}, [("js", LIGHT_JS)]),
        ("light-tap", {}, [("js", TAP_JS)]),
        ("fix-typed", {}, [("js", OPEN_JS), _ask(q["fix"], 1500)]),
        ("note-fix", {}, [("js", NOTE_FIX_JS)]),
        ("open-empty", {}, [("js", OPEN_JS)]),
        # A short question: on the demo box most sheets exceed the model budget (addendum N0: 500 tokens), and
        # then the draft is final at once, with no typing line to show (measured 23.10.2026).
        ("typing", {}, [("js", OPEN_JS), _ask(q["warm"], wait_typing), ("js", END_JS)]),
        ("answer", {}, [("js", OPEN_JS), _ask(q["warm"], wait_revised), ("js", END_JS)]),
        ("draft", {}, [("js", OPEN_JS), _ask(q["humidity"], 1500), ("js", END_JS)]),
        ("clarify", {}, [("js", OPEN_JS), _ask(q["typo"], 1200)]),
        ("offtopic", {}, [("js", OPEN_JS), _ask(q["offtopic"], 1200)]),
        ("nodata", {}, [("js", OPEN_JS), _ask(q["clarify"], 1200)]),
        ("proposal", {}, [("js", OPEN_JS), _ask(q["light"], 1500)]),
        ("declined", {}, [("js", OPEN_JS), _ask(q["light"], 1500), ("js", SKIP_JS)]),
        # the five questions of the acceptance of 23.10.2026: the answer first, the rest folded away
        ("damp", {}, [("js", OPEN_JS), _ask(q["damp"], 1500), ("js", END_JS)]),
        ("lamp", {}, [("js", OPEN_JS), _ask(q["lamp"], 1500), ("js", END_JS)]),
        ("damp-more", {}, [("js", OPEN_JS), _ask(q["damp"], 1500), ("js", MORE_JS)]),
        ("feed", {}, [("js", OPEN_JS), _ask(q["feed"], 1500), ("js", END_JS)]),
        ("tomato", {}, [("js", OPEN_JS), _ask(q["tomato"], 1500)]),
        # v1.2 (owner, 23.10.2026): explain first, a follow-up about it, the height with its day, no glossary term
        ("explain", {}, [("js", OPEN_JS), _ask(q["explain"], 1500), ("js", END_JS)]),
        ("explain-fix", {}, [("js", OPEN_JS), _ask(q["explain"], 1500), _ask(q["fixit"], 1500)]),
        ("height", {}, [("js", OPEN_JS), _ask(q["height"], 1500)]),
        ("lolli", {}, [("js", OPEN_JS), _ask(q["lolli"], 1500)]),
        ("plant-bloom", {}, [("js", PLANT_JS)]),
        # v1.3 (reviews of v1.2): the day of flowering, a how-to about a glossary term, a device term
        ("bloomday", {}, [("js", OPEN_JS), _ask(q["bloomday"], 1500)]),
        ("howto", {}, [("js", OPEN_JS), _ask(q["howto"], 1500)]),
        ("exhaust", {}, [("js", OPEN_JS), _ask(q["exhaust"], 1500)]),
        ("off-armed", {}, [("js", OFF_ARMED_JS)]),
        ("warnings", {}, [("js", WARN_JS), ("after", BACK_ON_JS)]),
        ("off", {}, [("js", OFF_JS), ("after", ON_JS)]),
    ]


# ---------- the live server: the demo cockpit with the real Hank ----------

def _cite_first_fact(body: dict[str, Any]) -> str:
    """The fake model cites the sheet's first fact with its marker and the words of its lead, no number: the
    validator lets it through and the draft's core facts stay, so the revised answer can be seen. (A bare "Das steht
    so in meiner Kartei [F1]." is a filler since the acceptance of 23.10.2026: the draft stays.)"""
    user = next((m.get("content") or "" for m in body.get("messages") or [] if m.get("role") == "user"), "")
    found = re.search(r"^\[(F\d+)\] (.*)$", user, re.M)
    marker = found.group(1) if found else "F1"
    lead = re.split(r"[:(\d.]", found.group(2))[0].strip(" ,;") if found else ""
    if "QUESTION" in user:
        return f"My card file says this about {lead} [{marker}]."
    return f"Meine Kartei sagt dazu: {lead} [{marker}]."


class LiveServer:
    """The demo cockpit and Hank in a thread of their own, on 127.0.0.1:port; the fake Ollama on port + 1."""

    def __init__(self, state: Path, port: int) -> None:
        self.state = state
        self.port = port
        self.key = secrets.token_urlsafe(24)
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, name="hank-live", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?key={self.key}"

    def __enter__(self) -> LiveServer:
        self._thread.start()
        if not self.ready.wait(120) or self.error is not None:
            raise SystemExit(f"the live server did not start: {self.error!r}")
        return self

    def __exit__(self, *_: Any) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(30)

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as err:  # noqa: BLE001 (reported to the main thread)
            self.error = err
            self.ready.set()

    async def _serve(self) -> None:
        import aiohttp
        from aiohttp import web

        sys.path.insert(0, str(REPO))
        sys.path.insert(0, str(REPO / "tests"))
        from hank_eval.fake_ollama import FakeOllama

        from vgrow import config, server
        from vgrow.alerts import Alerts
        from vgrow.automation import HumidifierAutomation
        from vgrow.demo import DemoBox, setup_demo
        from vgrow.hank import api, ollama, wiring
        from vgrow.hank.core import Hank
        from vgrow.hank.kartei_index import Index
        from vgrow.plant import PlantLog

        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        config.use_state_dir(self.state)  # never the real run's key, logs, plant log or settings
        # The fake knows the model name this machine's settings resolve to (HANK_MODEL, VISION_MODEL, the default).
        fake = FakeOllama(models=(ollama.model_name(), ollama.embed_model_name()), reply=_cite_first_fact)
        fake.delay["chat"] = LIVE_MODEL_SECONDS
        fake_runner = web.AppRunner(fake.app())
        await fake_runner.setup()
        await web.TCPSite(fake_runner, "127.0.0.1", self.port + 1).start()
        box = DemoBox()
        async with aiohttp.ClientSession() as session:
            alerts = Alerts(session)
            alerts.demo = True
            plant_log = PlantLog()
            automation = HumidifierAutomation(box, alerts, plant_log)
            camera, vision = setup_demo(plant_log, automation)
            parts: dict[str, Any] = {"camera": camera}
            box.listeners.append(server.poll_handler(alerts, automation, parts, plant_log))
            await box.start()
            lane = ollama.ModelLane()
            client = ollama.OllamaClient(session, url=f"http://127.0.0.1:{self.port + 1}", lane=lane)
            collector = wiring.Collector(box, camera=parts, plant_log=plant_log, automation=automation,
                                         alerts=alerts, vision=vision)
            # The index without an embedder: the fake's hash vectors carry no meaning and would only blur the routing.
            hank = Hank(live=collector, client=client, lane=lane, index=Index.load())
            app = server.make_app(box, alerts, self.key, camera, plant_log, vision, automation)
            api.register(app, hank)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", self.port).start()
            await hank.start()
            for _ in range(100):  # the first tick draws the light
                if hank.nudge is not None:
                    break
                await asyncio.sleep(0.1)
            self.ready.set()
            try:
                await self._stop.wait()
            finally:
                await hank.stop()
                await runner.cleanup()
                await box.stop()
                await fake_runner.cleanup()


# ---------- the camera ----------

def _edge(out: Path, port: int) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    # The demo run is started with USERPROFILE pointing at an empty folder, so it finds no push settings; Edge does
    # not start that way. HANK_EDGE_HOME gives Edge (only Edge) the real profile folder back.
    env = dict(os.environ)
    if env.get("HANK_EDGE_HOME"):
        env["USERPROFILE"] = env["HANK_EDGE_HOME"]
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={port}", f"--user-data-dir={out / 'prof-cdp'}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    for _ in range(40):
        try:
            targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())
            return proc, next(t for t in targets if t["type"] == "page")
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("Edge did not start")


def shoot(out: Path, lang: str, port: int, url: str, plan: list[tuple[str, dict[str, Any], list[Step]]],
          prefix: str = "", on_size: Callable[[Cdp], None] | None = None, only: set[str] | None = None) -> None:
    plan = [item for item in plan if not only or item[0] in only]
    proc, page = _edge(out, port)
    try:
        with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = Cdp(ws)
            cdp.cmd("Page.enable")
            cdp.cmd("Runtime.enable")
            for theme in ("light", "dark"):
                for size, (w, h), mobile in (("phone", PHONE, True), ("desktop", DESKTOP, False)):
                    cdp.cmd("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=2, mobile=mobile)
                    cdp.cmd("Emulation.setTouchEmulationEnabled", enabled=mobile, maxTouchPoints=5 if mobile else 1)
                    for name, pin, steps in plan:
                        source = prefs_script(lang, theme) + f"\nwindow.HANK_PREVIEW = {json.dumps(pin)};"
                        added = cdp.cmd("Page.addScriptToEvaluateOnNewDocument", source=source)["identifier"]
                        cdp.cmd("Page.navigate", url=url)
                        cdp.pump(1.0)
                        if not cdp.js(READY_JS, wait=True):
                            print("Hank did not appear:", name, size, theme)
                        after = []
                        for kind, step in steps:
                            if kind == "wait":
                                cdp.pump(step)
                            elif kind == "after":
                                after.append(step)
                            else:
                                cdp.js(step, wait=True)
                        cdp.pump(0.4)
                        data = cdp.cmd("Page.captureScreenshot", format="png")["data"]
                        path = out / f"{prefix}{name}-{size}-{lang}-{theme}.png"
                        path.write_bytes(base64.b64decode(data))
                        print("wrote", path.name)
                        for step in after:
                            cdp.js(step, wait=True)
                        cdp.cmd("Page.removeScriptToEvaluateOnNewDocument", identifier=added)
    finally:
        proc.terminate()


def run(out: Path, lang: str, port: int, only: set[str] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    preview = out / "hank-preview.html"
    build_preview.main(preview)
    # The artifact viewer wraps the preview in a page with a viewport tag; a bare file needs its own, or a phone
    # lays it out 980 px wide.
    viewport = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    preview.write_bytes((viewport + preview.read_text(encoding="utf-8")).encode("utf-8"))
    shoot(out, lang, port, preview.resolve().as_uri(), states(lang), only=only)


def run_live(out: Path, lang: str, port: int, server_port: int, only: set[str] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with LiveServer(out / "live-state", server_port) as live:
        print("live server at", f"http://127.0.0.1:{server_port}/")
        shoot(out, lang, port, live.url, live_states(lang), prefix="live-", only=only)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--lang", choices=("de", "en"), default="de")
    parser.add_argument("--live", action="store_true", help="the demo cockpit with the real Hank instead of the preview")
    parser.add_argument("--port", type=int, default=9334, help="DevTools port (default 9334)")
    parser.add_argument("--server-port", type=int, default=8790,
                        help="--live: the cockpit's port; the fake Ollama takes the next one (default 8790)")
    parser.add_argument("--only", default="", help="comma-separated state names, e.g. answer,lamp,lamp-more")
    args = parser.parse_args()
    only = {name.strip() for name in args.only.split(",") if name.strip()} or None
    if args.live:
        run_live(args.out_dir, args.lang, args.port, args.server_port, only)
    else:
        run(args.out_dir, args.lang, args.port, only)


if __name__ == "__main__":
    main()
