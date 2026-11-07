"""Protocol browser acceptance against an isolated local demo with /test/reading sensor fixtures.

The test changes the DEMO exhaust once and injects DEMO readings. It refuses remote or non-demo targets.
VGROW_URL holds the authenticated demo URL and is never printed.
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
        raise SystemExit("Use an isolated local demo with the protocol sensor fixture.")
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
            errors, commands = [], []
            c.handlers["Runtime.exceptionThrown"] = lambda e: errors.append(e["exceptionDetails"]["text"])
            c.handlers["Network.requestWillBeSent"] = lambda e: commands.append(e["request"].get("postData")) if e["request"]["method"] == "POST" and urlsplit(e["request"]["url"]).path == "/api/cmd" else None
            for domain in ("Page", "Runtime", "Network"):
                c.cmd(domain + ".enable")
            c.cmd("Page.addScriptToEvaluateOnNewDocument", source=prefs_script("en", "light"))
            c.cmd("Page.navigate", url=url + "#activity/protocol")
            wait_for(c, '!!document.body && document.body.classList.contains("workspace-ready") && !!state')
            assert js(c, "state.demo === true")
            wait_for(c, 'document.querySelectorAll(".protocol-issue").length>=2 && document.querySelectorAll(".protocol-task").length>=2')
            assert "8/10" in js(c, 'document.querySelector("#protocol-issues").textContent')
            assert "confirmation not recorded" in js(c, 'document.querySelector("#protocol-tasks").textContent')
            assert not commands, "Reading the protocol must never issue commands"
            # The normal actuator endpoint, explicitly called by this acceptance test, changes only the demo box.
            js(c, '(async()=>{await api("/api/cmd",{action:"dfan",step:9});await poll();document.querySelector("#protocol-refresh").click()})()')
            wait_for(c, 'document.querySelector("#protocol-tasks").textContent.includes("Device confirmed")', seconds=20)
            assert "Waiting for new readings" in js(c, 'document.querySelector("#protocol-tasks").textContent')
            assert js(c, 'document.querySelectorAll(".protocol-issue").length') >= 2
            js(c, '(async()=>{await api("/test/reading",{rh:65,vpd:1.05});})()')
            c.pump(3.2)
            js(c, 'document.querySelector("#protocol-refresh").click()')
            wait_for(c, 'document.querySelector("#protocol-tasks").textContent.includes("Moving toward target")')
            assert "67" in js(c, 'document.querySelector("#protocol-tasks").textContent')
            js(c, 'document.querySelector("#protocol-origin").value="hank";document.querySelector("#protocol-origin").dispatchEvent(new Event("change"))')
            assert js(c, 'document.querySelectorAll(".protocol-task").length') == 1
            assert "confirmation not recorded" in js(c, 'document.querySelector("#protocol-tasks").textContent')
            js(c, 'document.querySelector("#protocol-origin").value="all";document.querySelector("#protocol-origin").dispatchEvent(new Event("change"));document.querySelector("#protocol-more").click()')
            wait_for(c, 'document.querySelectorAll(".protocol-task").length>50')
            count = js(c, 'document.querySelectorAll(".protocol-task").length')
            js(c, 'document.querySelector("#protocol-refresh").click()')
            c.pump(.5)
            assert js(c, 'document.querySelectorAll(".protocol-task").length') == count
            assert js(c, 'document.querySelector("#protocol-more").hidden'), "refresh must not reset the older-page cursor"
            shooter, cases = Shooter(c, out, "protocol"), 0
            for lang in ("en", "de"):
                for theme in ("light", "dark"):
                    js(c, f'LANG={json.dumps(lang)};applyStatic();document.documentElement.lang=LANG;document.documentElement.dataset.theme={json.dumps(theme)}')
                    for width, height in ((1440, 1000), (768, 1024), (375, 844), (320, 740)):
                        shooter.emulate(width, height, mobile=width < 960)
                        visit(c, "activity/protocol")
                        assert not js(c, "document.documentElement.scrollWidth>innerWidth"), (lang, theme, width)
                        assert not js(c, '[...document.querySelectorAll(".protocol-task,.protocol-issue")].some(n=>n.scrollWidth>n.clientWidth+2)'), (lang, theme, width)
                        if width in (1440, 375):
                            top = shooter.rect("[data-activity-panel=protocol]")
                            first = shooter.rect(".protocol-task")
                            shooter.shot(f"{lang}-{theme}-{width}", {"x": top["x"], "y": top["y"], "width": top["width"], "height": first["y"] + first["height"] - top["y"] + 10})
                        cases += 1
            js(c, 'LANG="en";document.documentElement.lang="en";applyStatic();(async()=>{await api("/test/reading",{rh:55,vpd:1.2})})()')
            c.pump(3.2)
            js(c, 'document.querySelector("#protocol-refresh").click()')
            wait_for(c, 'document.querySelector("#protocol-summary").textContent.startsWith("0 open") && document.querySelector("#protocol-tasks").textContent.includes("Readings back in target")')
            # A failed fetch keeps the loaded history and a visible warning.
            js(c, 'window.protocolRealApi=api;api=(p,b)=>p.startsWith("/api/protocol")?Promise.reject(Error("fixture offline")):protocolRealApi(p,b);document.querySelector("#protocol-refresh").click()')
            wait_for(c, '!!document.querySelector("#protocol-error").textContent')
            assert js(c, 'document.querySelectorAll(".protocol-task").length') == count
            assert len(commands) == 1, commands
            assert not errors, errors
            print(json.dumps({"layouts":cases,"demo_commands":len(commands),"console_errors":0,
                "confirmation_vs_effect":"passed","filters_pagination_cached_error":"passed","history_rows":count}))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=9406)
    args = parser.parse_args()
    run(os.environ["VGROW_URL"], args.out, args.port)
