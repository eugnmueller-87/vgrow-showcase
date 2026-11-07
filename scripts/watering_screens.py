"""Browser acceptance of watering settings and a two-second cycle, ONLY against an isolated local demo.

VGROW_URL=<local-demo-with-key> python scripts/watering_screens.py _shots/watering-ui
No live box or cloud account is used. Additional DE/EN light/dark desktop/mobile states are read-only fixtures.
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
from hank_panel_screens import click
from screenshots import Cdp, EDGE, Shooter, prefs_script
from websockets.sync.client import connect


def run(url: str, out: Path, port: int) -> None:
    assert urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1"), "Use an isolated local demo"
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={port}",
                             f"--user-data-dir={out.resolve() / 'edge'}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(60):
            try:
                page = next(p for p in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read()) if p["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        assert page
        with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            c = Cdp(ws)
            c.handlers["Runtime.exceptionThrown"] = lambda e: errors.append(e["exceptionDetails"].get("exception", {}).get("description", e["exceptionDetails"]["text"]))
            for domain in ("Page", "Runtime", "Network"):
                c.cmd(domain + ".enable")
            s = Shooter(c, out, "watering")
            s.emulate(1440, 1000, mobile=False)
            script = c.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script("en", "light"))["identifier"]
            c.cmd("Page.navigate", url=url)
            wait_for(c, "typeof state==='object' && state && window.Cockpit && state.watering")
            assert js(c, "state.demo === true"), "Refusing actuator checks outside a demo"
            assert js(c, "!state.watering.settings.enabled && !state.watering.active && !state.ports.usb1.on")
            js(c, "api('/api/cmd',{action:'port_assign',box:'box-1',port:'usb1',device:'pump'})")
            visit(c, "automation")
            js(c, """(() => {
              const values={'watering-duration':'2','watering-times':'06:30, 18:30','watering-every':'2','watering-first':'2026-10-25'};
              for(const [id,value] of Object.entries(values)){
                $(id).value=value;$(id).dispatchEvent(new Event('input',{bubbles:true}));
              }
              $('watering-power-confirmed').checked=true;
              document.querySelector('#watering-settings button').scrollIntoView({behavior:'instant',block:'center'});
            })()""")
            click(c, "#watering-settings button")
            wait_for(c, "state.watering.settings.duration_s===2 && !document.querySelector('#watering-settings button').disabled")
            assert js(c, "!state.watering.settings.enabled && !state.watering.active && !state.ports.usb1.on")
            js(c, "$('watering-duration').value='42';$('watering-duration').dispatchEvent(new Event('input',{bubbles:true}));poll(true)")
            assert js(c, "$('watering-duration').value==='42'"), "Polling overwrote an unsaved field"
            js(c, "$('watering-duration').value='2';document.querySelector('#watering-settings button').scrollIntoView({behavior:'instant',block:'center'})")
            click(c, "#watering-settings button")
            wait_for(c, "!document.querySelector('#watering-settings button').disabled")
            js(c, "$('watering-run').scrollIntoView({behavior:'instant',block:'center'})")
            click(c, "#watering-run")
            wait_for(c, "state.watering.active && state.ports.usb1.on")
            assert js(c, "state.watering.remaining_s<=2 && $('watering-run').disabled")
            js(c, "new Promise(resolve=>setTimeout(resolve,2600)).then(()=>poll(true))")
            wait_for(c, "!state.watering.active && state.ports.usb1.on===false")
            assert js(c, "state.watering.events.some(e=>e.kind==='stopped' && e.reason==='duration')")
            js(c, "clearTimeout(pollTimer);window.wateringFixture=structuredClone(state)")
            cases = 0
            for lang in ("de", "en"):
                c.cmd("Page.removeScriptToEvaluateOnNewDocument", identifier=script)
                script = c.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script(lang, "light"))["identifier"]
                c.cmd("Page.navigate", url=url)
                wait_for(c, "typeof state==='object' && state && state.watering && window.Cockpit")
                js(c, "clearTimeout(pollTimer);window.wateringFixture=structuredClone(state)")
                for theme in ("light", "dark"):
                    js(c, f"applyTheme('{theme}',true)")
                    for width, height in ((1440, 1000), (375, 844)):
                        s.emulate(width, height, mobile=width < 960)
                        visit(c, "automation")
                        for status in ("scheduled", "running", "stopping", "unmanaged", "blocked"):
                            js(c, """((status)=>{
                              const s=structuredClone(wateringFixture),w=s.watering;
                              w.status=status;w.blocked=status==='blocked'?'supply':null;
                              w.settings.enabled=status==='scheduled';w.next_at=Date.now()/1000+3600;
                              w.reported_on=['running','stopping','unmanaged'].includes(status);
                              w.active=['running','stopping'].includes(status)?{phase:status,reason:status==='stopping'?'duration':null}:null;
                              w.remaining_s=status==='running'?30:null;
                              render(s);clearTimeout(pollTimer);
                            })(%s)""" % json.dumps(status))
                            assert js(c, "document.documentElement.scrollWidth<=innerWidth"), (lang, theme, width, status)
                            assert js(c, "$('watering-status').textContent && !$('watering').hidden")
                            if status == "unmanaged":
                                assert js(c, "!$('watering-warning').hidden && !$('watering-stop').disabled")
                            if status == "stopping":
                                assert js(c, "$('watering-countdown').textContent===t('watering.waitingOff')")
                            cases += 1
                        rect = s.rect("#watering")
                        s.shot(f"{lang}-{theme}-{width}", {"x": rect["x"], "y": rect["y"], "width": rect["width"], "height": rect["height"]})
            assert not errors, errors
            print(json.dumps({"fixture_cases": cases, "demo_cycle_seconds": 2, "browser_errors": errors}))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=9366)
    args = parser.parse_args()
    run(os.environ["VGROW_URL"], args.out, args.port)
