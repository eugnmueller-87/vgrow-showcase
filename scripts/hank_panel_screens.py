"""Check Hank's close button, docking, scrolling and keyboard dismissal in a real browser.

Point VGROW_URL at an isolated local --demo server, then:
  python scripts/hank_panel_screens.py _shots/hank-panel

Uses real pointer clicks (a programmatic .click() would miss an obscured close button).
Never submits chat messages, changes Hank's mode or sends actuator commands.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from cockpit_screens import js, visit, wait_for
from screenshots import Cdp, EDGE, Shooter, prefs_script
from websockets.sync.client import connect

SIZES = ((1920, 1080), (1440, 900), (1280, 800), (1024, 768), (768, 1024), (375, 844), (375, 560), (844, 390), (375, 320))


def click(cdp: Cdp, selector: str) -> None:
    point = js(cdp, """((selector) => {
      const el = document.querySelector(selector), r = el.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      return {x,y,visible:r.width > 0 && r.height > 0 && y >= 0 && y < innerHeight && el.contains(document.elementFromPoint(x,y))};
    })(%s)""" % json.dumps(selector))
    assert point["visible"], ("Control is hidden or covered", selector, point)
    for kind in ("mousePressed", "mouseReleased"):
        cdp.cmd("Input.dispatchMouseEvent", type=kind, x=point["x"], y=point["y"], button="left", clickCount=1)
    cdp.pump(.04)


def run(url: str, out: Path, port: int) -> None:
    assert urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1"), "Use a local demo for regression checks"
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    commands: list[str] = []
    cases = 0
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={port}", f"--user-data-dir={out.resolve() / 'edge'}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(50):
            try:
                page = next(t for t in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2).read()) if t["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        assert page, "Edge did not start"
        with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            c = Cdp(ws)
            c.handlers["Runtime.exceptionThrown"] = lambda e: errors.append(e["exceptionDetails"].get("exception", {}).get("description", e["exceptionDetails"]["text"]))
            c.handlers["Network.requestWillBeSent"] = lambda e: commands.append("/api/cmd") if urlsplit(e["request"]["url"]).path == "/api/cmd" and e["request"]["method"] == "POST" else None
            for domain in ("Page", "Runtime", "Network"):
                c.cmd(domain + ".enable")
            shooter = Shooter(c, out, "panel")
            pref_id = None
            for lang in ("de", "en"):
                for theme in ("light", "dark"):
                    if pref_id:
                        c.cmd("Page.removeScriptToEvaluateOnNewDocument", identifier=pref_id)
                    pref_id = c.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script(lang, theme))["identifier"]
                    c.cmd("Page.navigate", url=url)
                    wait_for(c, "typeof state === 'object' && state && !!document.querySelector('.hk-fab')")
                    assert js(c, "state.demo"), "Regression checks require a demo"
                    for width, height in SIZES:
                        shooter.emulate(width, height, mobile=width < 960)
                        visit(c, "overview")
                        click(c, ".hk-fab")
                        assert js(c, "document.body.classList.contains('hank-open') && !document.querySelector('.hk-drawer').hidden")
                        # A long conversation and long controller explanation must leave the close button and input visible.
                        js(c, """(() => {
                          const log = document.querySelector('.hk-log');
                          for (let i=0;i<30;i++) { const p=document.createElement('p'); p.className='acceptance-message'; p.textContent=t('hank.empty'); log.append(p); }
                          const pilot = document.querySelector('.hk-pilot'); pilot.hidden=false;
                          const p=document.createElement('p'); p.className='acceptance-message'; p.textContent=Array(60).fill(t('hank.empty')).join(' '); pilot.append(p);
                          log.scrollTop=log.scrollHeight;
                          document.querySelector('.hk-top').scrollTop=10000;
                          window.scrollTo(0, document.documentElement.scrollHeight);
                        })()""")
                        c.pump(.08)
                        assert not js(c, "document.documentElement.scrollWidth > innerWidth"), (lang, theme, width, height)
                        assert js(c, "document.querySelector('.hk-close').getBoundingClientRect().top >= 0")
                        assert js(c, "document.querySelector('.hk-foot').getBoundingClientRect().bottom <= innerHeight + 1"), ("Input outside viewport", width, height)
                        assert js(c, "document.querySelector('.hk-log').clientHeight >= 60"), ("No chat scrolling area", width, height)
                        click(c, ".hk-close")
                        assert js(c, "document.querySelector('.hk-drawer').hidden && !document.body.classList.contains('hank-open') && document.activeElement === document.querySelector('.hk-fab')")
                        click(c, ".hk-fab")
                        # Escape works with focus in the chat, and on the dashboard when the panel is docked.
                        if width >= 1280:
                            js(c, "document.querySelector('#page-title').focus()")
                        c.cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
                        assert js(c, "document.querySelector('.hk-drawer').hidden")
                        click(c, ".hk-fab")
                        if width < 1280:
                            assert js(c, "document.querySelector('.hk-drawer').getAttribute('aria-modal') === 'true'")
                            js(c, "document.querySelector('.hk-close').focus()")
                            c.cmd("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9, modifiers=8)
                            assert js(c, "document.querySelector('.hk-drawer').contains(document.activeElement) && document.activeElement !== document.querySelector('.hk-close')")
                            c.cmd("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
                            assert js(c, "document.activeElement === document.querySelector('.hk-close')")
                            panel = js(c, "({top:document.querySelector('.hk-drawer').getBoundingClientRect().top,left:document.querySelector('.hk-drawer').getBoundingClientRect().left})")
                            if panel["top"] > 5 or panel["left"] > 5:
                                x, y = 5, 5
                                assert js(c, "document.elementFromPoint(5,5).classList.contains('hk-backdrop')")
                                for kind in ("mousePressed", "mouseReleased"):
                                    c.cmd("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)
                                assert js(c, "document.querySelector('.hk-drawer').hidden"), "Outside tap did not dismiss"
                                click(c, ".hk-fab")
                        for route in ("overview", "devices/controls", "analytics"):
                            visit(c, route)
                            c.pump(.22)
                            assert not js(c, "document.documentElement.scrollWidth > innerWidth"), ("Open panel overflow", route, width, height)
                            if width >= 1280:
                                bounds = js(c, "({main:document.querySelector('main').getBoundingClientRect().right,bar:document.querySelector('.app-topbar').getBoundingClientRect().right,panel:document.querySelector('.hk-drawer').getBoundingClientRect().left})")
                                assert bounds["main"] <= bounds["panel"] + 1 and bounds["bar"] <= bounds["panel"] + 1, ("Chat covers dashboard tools", route, width, bounds)
                                assert js(c, "document.querySelector('.hk-drawer').getAttribute('aria-modal') === 'false'")
                                if route == "analytics":
                                    assert js(c, "!document.querySelector('#ch-climate .uplot') || document.querySelector('#ch-climate .uplot').getBoundingClientRect().right <= document.querySelector('.hk-drawer').getBoundingClientRect().left + 1"), "Chart did not resize"
                            cases += 1
                        if lang == "en" and theme == "light" and width in (1440, 375, 844):
                            shooter.shot(f"open-{width}x{height}", {"x":0,"y":0,"width":width,"height":height})
                        click(c, ".hk-close")
                        js(c, "document.querySelectorAll('.acceptance-message').forEach(n => n.remove())")
                    print(f"Passed {lang}/{theme}: close, Escape, focus, short screens and docking")
            assert not errors, errors
            assert not commands, commands
            report = {"page_layout_cases":cases,"viewport_sizes":SIZES,"languages":["de","en"],"themes":["light","dark"],
                      "real_pointer_close":True,"escape":True,"focus_return_and_containment":True,
                      "outside_tap":True,"docked_tools_accessible":True,"javascript_errors":errors,"hardware_commands":commands}
            (out / "report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
            print(json.dumps(report,indent=2))
    finally:
        proc.terminate()
        proc.wait(timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=9351)
    args = parser.parse_args()
    run(os.environ["VGROW_URL"], args.out, args.port)
