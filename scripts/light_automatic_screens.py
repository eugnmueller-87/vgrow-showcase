"""Automatic-light browser acceptance. Use only an isolated local demo with a confirmed early-flower stage.

This sends real API commands to the DEMO box. It refuses remote URLs and non-demo state.
VGROW_URL supplies the authenticated URL; it is never printed.
"""
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


def run(url, out, port):
    if urlsplit(url).hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("Use an isolated local demo.")
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={port}", f"--user-data-dir={out.resolve() / 'edge'}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                page = next(p for p in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")) if p["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            c = Cdp(ws)
            errors, posts = [], []
            c.handlers["Runtime.exceptionThrown"] = lambda e: errors.append(e["exceptionDetails"]["text"])
            c.handlers["Network.requestWillBeSent"] = lambda e: posts.append(e["request"]["postData"]) if e["request"]["method"] == "POST" and "postData" in e["request"] else None
            for domain in ("Page", "Runtime", "Network"):
                c.cmd(domain + ".enable")
            c.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script("en", "light"))
            c.cmd("Page.navigate", url=url + "#devices/controls")
            wait_for(c, '!!document.body && document.body.classList.contains("workspace-ready") && !!state')
            assert js(c, "state.demo === true"), "No actuator checks outside the demo"
            wait_for(c, '!document.querySelector("#light-automatic").disabled')
            assert "18/6" in js(c, 'document.querySelector("#light-automatic-plan").textContent')
            assert not posts, "Opening Controls sends no command"
            js(c, 'document.querySelector("#light-automatic").click()')
            wait_for(c, 'state.light_schedule.settings.from_requirements && state.light.level===100 && document.querySelector("#light-automatic").getAttribute("aria-pressed")==="true"')
            # Existing manual brightness remains usable. Automatic resumes without waiting for an edge.
            js(c, 'document.querySelector("#light-number").value=40;document.querySelector("#light-number-send").click()')
            wait_for(c, 'state.light.level===40 && state.light_schedule.status==="manual"')
            assert "Manual" in js(c, 'document.querySelector("#device-light-meta").textContent')
            js(c, 'document.querySelector("#light-automatic").click()')
            wait_for(c, 'state.light.level===100 && state.light_schedule.status==="scheduled"')
            assert [json.loads(p)["action"] for p in posts] == ["light_schedule_auto", "light", "light_schedule_auto"]
            # Freeze polling for presentation-only fixtures; none of these cases sends a command.
            js(c, '''window.lightAutoFixture=state;window.autoRealFetch=fetch;
              window.fetch=(url,opts)=>url==='/api/state' ? Promise.resolve(new Response(JSON.stringify(lightAutoFixture),{headers:{'Content-Type':'application/json'}})) : autoRealFetch(url,opts);''')
            for reason in ("requirements", "connection", "mode"):
                js(c, 'window.reasonFixture=structuredClone(lightAutoFixture);reasonFixture.light_schedule.automatic.available=false;reasonFixture.light_schedule.automatic.reason=' + json.dumps(reason) + ';render(reasonFixture)')
                assert js(c, 'document.querySelector("#light-automatic").disabled')
                assert js(c, '!!document.querySelector("#light-automatic-note").textContent')
            js(c, 'render(lightAutoFixture);document.dispatchEvent(new Event("vgrow:offline"))')
            assert js(c, 'document.querySelector("#light-automatic").disabled')
            js(c, 'render(lightAutoFixture)')
            js(c, 'window.originalAllowed=PlantWorkspace.assistantAllowed;PlantWorkspace.assistantAllowed=()=>false;document.dispatchEvent(new Event("workspace:context"))')
            assert js(c, 'document.querySelector("#light-automatic").disabled && !document.querySelector("#light-automatic-sources").textContent')
            js(c, 'PlantWorkspace.assistantAllowed=originalAllowed;document.dispatchEvent(new Event("workspace:context"))')
            shooter, cases = Shooter(c, out, "automatic"), 0
            for lang in ("en", "de"):
                for theme in ("light", "dark"):
                    js(c, f'LANG={json.dumps(lang)};applyStatic();document.documentElement.setAttribute("data-theme",{json.dumps(theme)});render(lightAutoFixture)')
                    for width, height in ((1440, 1000), (768, 1024), (375, 844), (320, 740)):
                        shooter.emulate(width, height, mobile=width < 960)
                        visit(c, "devices/controls")
                        assert not js(c, "document.documentElement.scrollWidth>innerWidth"), (lang, theme, width)
                        assert not js(c, 'document.querySelector("#light-automatic-plan").scrollWidth>document.querySelector("#light-automatic-plan").clientWidth'), (lang, theme, width)
                        if width in (1440, 375):
                            shooter.shot(f"{lang}-{theme}-{width}", shooter.rect("#card-light"))
                        cases += 1
            assert not errors, errors
            assert len(posts) == 3, posts
            print(json.dumps({"layouts": cases, "console_errors": len(errors), "demo_commands": len(posts),
                              "automatic_manual_resume": "passed", "unavailable_offline": "passed"}))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=9399)
    args = parser.parse_args()
    run(os.environ["VGROW_URL"], args.out, args.port)
