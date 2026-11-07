"""Browser acceptance for the routed cockpit; requires Edge and a local --demo server.

Set VGROW_URL to its authenticated URL (never printed), then run:
  python scripts/cockpit_screens.py _shots/redesign

Exercises every page at 1440, 1920, 768 and 375px, in DE/EN and Light/Dark/Auto.
Also checks Back/Forward, old bookmarks, keyboard/drawer focus, cached/partial/offline
data and command feedback. All command tests are intercepted in the browser. Refuses
to run these checks unless /api/state identifies the server as a demo.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from screenshots import Cdp, EDGE, Shooter, prefs_script
from websockets.sync.client import connect

ROUTES = ("overview", "grow/growth", "grow/feeding", "grow/insights", "grow/journal", "automation",
          "devices/controls", "devices/camera", "analytics", "activity/active", "activity/history",
          "activity/rules", "settings", "wiki", "community")
SIZES = ((1440, 1000), (1920, 1080), (768, 1024), (375, 844))


def js(cdp: Cdp, expression: str) -> object:
    result = cdp.cmd("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
    if "exceptionDetails" in result:
        error = result["exceptionDetails"]
        raise AssertionError(error.get("exception", {}).get("description", error["text"]))
    return result["result"].get("value")


def wait_for(cdp: Cdp, expression: str, seconds: int = 15) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if js(cdp, expression):
            return
        cdp.pump(.1)
    raise AssertionError(f"Timed out: {expression}")


def visit(cdp: Cdp, route: str) -> None:
    js(cdp, f"location.hash = {json.dumps(route)}")
    cdp.pump(.08)


def contrast(a: str, b: str) -> float:
    def luminance(value: str) -> float:
        value = value.strip().removeprefix("#")
        if len(value) == 3:
            value = "".join(v * 2 for v in value)
        rgb = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722), strict=True))
    lo, hi = sorted((luminance(a), luminance(b)))
    return (hi + .05) / (lo + .05)


def run(url: str, out: Path, port: int) -> None:
    if urlsplit(url).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Use a local demo URL for browser acceptance.")
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    posted: list[str] = []
    community_requests: list[str] = []
    captures = 0
    cases = 0
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={port}", f"--user-data-dir={out.resolve() / 'edge'}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(60):
            try:
                targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2).read())
                page = next(t for t in targets if t["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        assert page, "Edge did not start"
        with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = Cdp(ws)
            cdp.handlers["Runtime.exceptionThrown"] = lambda e: errors.append(e["exceptionDetails"].get("exception", {}).get("description", e["exceptionDetails"]["text"]))
            def requested(event: dict) -> None:
                request = event["request"]
                destination = urlsplit(request["url"])
                if request["method"] == "POST":
                    posted.append(destination.path)
                if destination.hostname == "vivosun.com":
                    community_requests.append(request["url"])

            cdp.handlers["Network.requestWillBeSent"] = requested
            for domain in ("Page", "Runtime", "Network"):
                cdp.cmd(domain + ".enable")
            shooter = Shooter(cdp, out, "acceptance")

            def capture(name: str, width: int, height: int) -> None:
                nonlocal captures
                data = cdp.cmd("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                               clip={"x": 0, "y": 0, "width": width, "height": height, "scale": 1})["data"]
                (out / f"{name}.png").write_bytes(base64.b64decode(data))
                captures += 1

            previous_script = None
            for lang in ("de", "en"):
                for theme in ("light", "dark", "auto"):
                    if previous_script:
                        cdp.cmd("Page.removeScriptToEvaluateOnNewDocument", identifier=previous_script)
                    previous_script = cdp.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script(lang, theme))["identifier"]
                    cdp.cmd("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if theme == "auto" else "light"}])
                    cdp.cmd("Page.navigate", url=url)
                    wait_for(cdp, "typeof state === 'object' && state && !!window.Cockpit")
                    assert js(cdp, "state.demo === true"), "Acceptance requires the demo; refusing command checks"
                    wait_for(cdp, "!document.querySelector('[data-wiki-open]').hidden")
                    for width, height in SIZES:
                        shooter.emulate(width, height, mobile=width < 960)
                        for route in ROUTES:
                            visit(cdp, route)
                            if route == "wiki":
                                wait_for(cdp, "document.body.classList.contains('wiki-on') && !!document.querySelector('.wiki-title')")
                            visible = js(cdp, "[...document.querySelectorAll('[data-page]')].filter(n => !n.hidden).map(n => n.dataset.page)")
                            expected = [] if route == "wiki" else [route.split("/")[0]]
                            assert visible == expected, (lang, theme, width, route, visible)
                            overflow = js(cdp, "document.documentElement.scrollWidth > innerWidth")
                            assert not overflow, ("horizontal overflow", lang, theme, width, route)
                            assert js(cdp, "document.querySelectorAll('#cam-img').length === 1")
                            assert js(cdp, "[...document.querySelectorAll('[data-i18n]')].every(n => !n.textContent.startsWith('cockpit.'))")
                            if route == "analytics":
                                wait_for(cdp, "!!document.querySelector('#ch-climate .uplot')")
                                assert js(cdp, "document.querySelector('#ch-climate .uplot').getBoundingClientRect().width > 100")
                                assert js(cdp, "!!document.querySelector('#history-data tbody td') && !document.querySelector('#history-data tbody').textContent.includes('Invalid')")
                            if (width in (1440, 375) and route in ("overview", "automation", "devices/controls", "grow/feeding")) or (lang == "en" and theme == "light" and width == 1440):
                                capture(f"{lang}-{theme}-{width}-{route.replace('/', '-')}", width, height)
                            cases += 1
                    tokens = js(cdp, "Object.fromEntries(['--ink','--muted','--card','--accent-strong','--accent-soft','--warn','--warn-soft','--bad','--bad-soft','--info','--info-soft'].map(k=>[k,css(k)]))")
                    for fg, bg in (("--ink", "--card"), ("--muted", "--card"), ("--accent-strong", "--accent-soft"), ("--warn", "--warn-soft"), ("--bad", "--bad-soft"), ("--info", "--info-soft")):
                        assert contrast(tokens[fg], tokens[bg]) >= 4.5, (lang, theme, fg, bg, contrast(tokens[fg], tokens[bg]))
                    print(f"Passed {lang}/{theme}: {len(ROUTES) * len(SIZES)} route/layout cases")

            # Navigation never writes to the hardware or settings API.
            assert not posted, ("Navigation issued POST requests", posted)

            # Public content stays inside the cockpit; account windows are simulated and never sign in.
            from community_screens import check_flows
            cdp.cmd("Network.setBlockedURLs", urls=["*youtube-nocookie.com/*"])
            community_checks = check_flows(cdp)
            cdp.cmd("Network.setBlockedURLs", urls=[])
            assert not community_requests and not posted, (community_requests, posted)

            for old, target in (("autopilot", "automation"), ("card-light", "devices/controls"), ("pflanze", "grow/growth"), ("kamera", "devices/camera"), ("verlauf", "analytics")):
                visit(cdp, old)
                assert js(cdp, "location.hash") == "#" + target
            for route in ("overview", "devices/controls", "analytics"):
                visit(cdp, route)
            js(cdp, "window.history.back()")
            wait_for(cdp, "location.hash === '#devices/controls' && !document.querySelector('#page-devices').hidden")
            js(cdp, "window.history.forward()")
            wait_for(cdp, "location.hash === '#analytics' && !document.querySelector('#page-analytics').hidden")

            shooter.emulate(375, 844, mobile=True)
            visit(cdp, "overview")
            js(cdp, "document.querySelector('#menu-btn').click()")
            wait_for(cdp, "document.querySelector('#app-sidebar').contains(document.activeElement)")
            assert js(cdp, "document.body.classList.contains('nav-open') && document.querySelector('#app-sidebar').contains(document.activeElement)")
            js(cdp, "render(state)")
            assert js(cdp, "document.body.classList.contains('nav-open')"), "A poll closed the navigation drawer"
            cdp.cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
            assert js(cdp, "!document.body.classList.contains('nav-open') && document.activeElement.id === 'menu-btn'")
            js(cdp, "document.querySelector('#menu-btn').click()")
            wait_for(cdp, "document.querySelector('#app-sidebar').contains(document.activeElement)")
            js(cdp, "document.querySelector('#app-sidebar .brand').focus()")
            cdp.cmd("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9, modifiers=8)
            assert js(cdp, "document.activeElement === document.querySelector('[data-route-link=community]')"), "Drawer focus escaped on Shift+Tab"
            js(cdp, "document.querySelector('#nav-close').click()")

            # Safely intercept commands; test pending/confirmed/timeout/rejection without sending a command to any server.
            visit(cdp, "devices/controls")
            results = js(cdp, """(async () => {
              clearTimeout(pollTimer);
              const saved = structuredClone(state), originalApi = api, sent = [];
              const results = {};
              api = async (path, body) => { if (path === '/api/cmd') { sent.push(body); throw new Error('Acceptance: command rejected'); } return originalApi(path, body); };
              try {
                const actual = document.querySelector('#light-val').textContent;
                const slider = document.querySelector('#light-range');
                slider.value = '85'; slider.dispatchEvent(new Event('input'));
                results.sliderKeepsActual = document.querySelector('#light-val').textContent === actual && sent.length === 0;
                const input = document.querySelector('#light-number'); input.focus(); input.value = '70'; render(state);
                results.pollKeepsInput = document.activeElement === input && input.value === '70';
                document.querySelector('#light-number-send').click();
                await new Promise(r => setTimeout(r, 30));
                results.rejected = sent.length === 1 && sent[0].level === 70 && document.querySelector('#fb-light').classList.contains('bad');
                for (const status of ['pending','ok','timeout','rejected']) {
                  render({...saved, pending:{'light.level':{status}}});
                  results[status] = document.querySelector('#fb-light').textContent === t(PENDING[status][0]) && document.querySelector('#light-val').textContent === actual;
                }
              } finally { api = originalApi; render(saved); clearTimeout(pollTimer); }
              return results;
            })()""")
            assert all(results.values()), results
            assert not posted, ("An intercepted command reached the server", posted)

            states = js(cdp, """(() => {
              const saved = structuredClone(state), result = {};
              try {
                render({...saved, sensors_age:900, shadow_age:2});
                result.independentFreshness = document.querySelector('#tile-temp').classList.contains('stale') && !document.querySelectorAll('#age .chip')[1].classList.contains('warn');
                render({...saved, demo:false, online:false, mqtt:false});
                result.disconnected = document.querySelector('#light-toggle').disabled && !document.querySelector('#offline-banner').hidden;
                render({...saved, plant:null, autopilot:null, camera:null, cameras:[], climate:{}});
                result.partial = !document.querySelector('#grow-empty').hidden && !document.querySelector('#camera-empty').hidden && !document.querySelector('#action-items .all-good');
                render(saved);
                document.dispatchEvent(new CustomEvent('vgrow:offline'));
                result.serverDown = document.querySelector('#action-items').textContent.includes(t('cockpit.serverUnavailable')) && document.querySelectorAll('#age .chip.warn').length === 2;
                render(saved);
                result.recovery = !document.querySelector('#tile-temp').classList.contains('stale');
              } finally { render(saved); clearTimeout(pollTimer); }
              return result;
            })()""")
            assert all(states.values()), states
            visit(cdp, "overview")
            capture("acceptance-final-mobile", 375, 844)
            assert not errors, errors
            report = {"route_layout_cases": cases, "screenshots": captures, "navigation_posts": posted,
                      "javascript_errors": errors, "command_feedback": results, "data_states": states,
                      "community_embedded_requests": community_requests, "community_desk": community_checks,
                      "legacy_links": True, "browser_history": True, "keyboard_drawer": True, "semantic_text_contrast_AA": True}
            (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
    finally:
        proc.terminate()
        proc.wait(timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=9348)
    args = parser.parse_args()
    run(os.environ["VGROW_URL"], args.out, args.port)
