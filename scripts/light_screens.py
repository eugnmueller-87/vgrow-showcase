"""Read-only light-page acceptance in an isolated local demo. All fixture values are intercepted in the browser."""
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
    if urlsplit(url).hostname not in ('127.0.0.1', 'localhost'):
        raise SystemExit('Use an isolated local demo.')
    out.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--hide-scrollbars',
                                f'--remote-debugging-port={port}', f'--user-data-dir={out.resolve() / "edge"}', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json')) if t['type'] == 'page')
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        with connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            c = Cdp(ws)
            errors, posts = [], []
            c.handlers['Runtime.exceptionThrown'] = lambda e: errors.append(e['exceptionDetails']['text'])
            c.handlers['Network.requestWillBeSent'] = lambda e: posts.append(urlsplit(e['request']['url']).path) if e['request']['method'] == 'POST' else None
            for domain in ('Page', 'Runtime', 'Network'):
                c.cmd(domain + '.enable')
            preference = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script('en', 'light'))['identifier']
            c.cmd('Page.navigate', url=url + '#overview')
            wait_for(c, '!!document.body && document.body.classList.contains("workspace-ready") && !!state')
            assert js(c, 'state.demo === true')
            assert js(c, '!document.querySelector("#tile-automation") && !!document.querySelector("#tile-light")')
            js(c, 'document.querySelector("#tile-light").click()')
            wait_for(c, 'location.hash==="#devices/light" && !document.querySelector("[data-devices-panel=light]").hidden')
            wait_for(c, 'document.querySelector("#light-view-history").textContent.includes("This demo")')
            assert js(c, 'document.querySelectorAll("#light-view-times dd").length') == 4
            assert js(c, '[...document.querySelectorAll("#light-view-times dd")].every(n=>n.textContent==="Not reported")')
            js(c, 'document.querySelector(".light-schedule-card a").click()')
            wait_for(c, 'location.hash==="#devices/controls" && !!document.querySelector("#card-light").getBoundingClientRect().width')
            visit(c, 'devices/light')
            # Supply known light reports without creating a timer, controller command or fake persisted history.
            js(c, '''window.lightRealFetch=window.fetch; window.lightFail=false; window.lightEnd=Date.now()/1000;
              window.fetch=(url,opts)=>url==='/api/light' ? Promise.resolve(new Response(JSON.stringify(lightFail?{error:'unavailable'}:{
                demo:true,store:true,history:{start:lightEnd-86400,end:lightEnd,readings:3,coverage_percent:50,segments:[
                  {start:lightEnd-86400,end:lightEnd-64800,level:100},{start:lightEnd-64800,end:lightEnd-43200,level:0},{start:lightEnd-43200,end:lightEnd,level:null}]}
              }),{status:lightFail?503:200,headers:{'Content-Type':'application/json'}})) : lightRealFetch(url,opts);
              document.querySelector('#light-view-refresh').click();''')
            wait_for(c, 'document.querySelectorAll(".light-history-segment").length===3')
            assert js(c, '!!document.querySelector(".is-off") && !!document.querySelector(".is-unknown")')
            js(c, 'window.lightFixture=state;render({...lightFixture,light:{...lightFixture.light,in_plan:true,in_plan_known:true}})')
            assert 'plan is active' in js(c, 'document.querySelector("#light-view-explanation").textContent')
            assert js(c, '[...document.querySelectorAll("#light-view-times dd")].every(n=>n.textContent==="Not reported")')
            js(c, 'render({...lightFixture,light:{level:100,in_plan:false,in_plan_known:false}})')
            assert 'unknown' in js(c, 'document.querySelector("#light-view-explanation").textContent')
            js(c, 'render({...lightFixture,shadow_age:901})')
            assert 'Last known' in js(c, 'document.querySelector("#light-view-freshness").textContent')
            js(c, 'document.dispatchEvent(new Event("vgrow:offline"))')
            assert 'Last known' in js(c, 'document.querySelector("#overview-light-value").textContent')
            js(c, 'render(lightFixture);lightFail=true;document.querySelector("#light-view-refresh").click()')
            wait_for(c, 'document.querySelector("#light-view-error").textContent.includes("previously")')
            assert js(c, 'document.querySelectorAll(".light-history-segment").length') == 3
            js(c, 'lightFail=false;document.querySelector("#light-view-refresh").click()')
            wait_for(c, '!document.querySelector("#light-view-error").textContent')
            print('Passed light flow: overview link, plan/manual/unknown status, no invented times, gaps, stale/offline and cached history')
            shooter = Shooter(c, out, 'light')
            cases = 0
            for lang in ('en', 'de'):
                for theme in ('light', 'dark'):
                    js(c, f'LANG={json.dumps(lang)};applyStatic();document.documentElement.setAttribute("data-theme",{json.dumps(theme)});render(lightFixture)')
                    for width, height in ((1440, 1000), (768, 1024), (375, 844), (320, 740)):
                        shooter.emulate(width, height, mobile=width < 960)
                        for route in ('overview', 'devices/light', 'automation'):
                            visit(c, route)
                            assert not js(c, 'document.documentElement.scrollWidth>innerWidth'), (lang, theme, width, route)
                            cases += 1
                        visit(c, 'devices/light')
                        if width in (1440, 375):
                            shooter.shot(f'{lang}-{theme}-{width}')
            c.cmd('Page.removeScriptToEvaluateOnNewDocument', identifier=preference)
            c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script('en', 'light'))
            c.cmd('Page.navigate', url=url + '#devices/light')
            wait_for(c, 'typeof state!=="undefined" && !!state && !!document.querySelector("#light-view-history") && document.querySelector("#light-view-history").textContent.includes("This demo")')
            assert js(c, 'location.hash') == '#devices/light'
            assert not posts, posts
            assert not errors, errors
            print(json.dumps({'layout_cases': cases, 'console_errors': len(errors), 'post_requests': len(posts), 'direct_link': 'passed'}))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--port', type=int, default=9398)
    args = parser.parse_args()
    run(os.environ['VGROW_URL'], args.out, args.port)
