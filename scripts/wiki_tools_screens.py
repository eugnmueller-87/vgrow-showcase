"""Browser acceptance of wiki reading/editorial tools. Writes only to a local demo.

VGROW_URL must be a local authenticated demo URL (never printed). The acceptance
creates a favorite, collection and reviewed reference correction in that demo.
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

from websockets.sync.client import connect

from cockpit_screens import js, wait_for
from screenshots import Cdp, EDGE, Shooter, prefs_script


def run(url: str, out: Path, port: int) -> None:
    if urlsplit(url).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise SystemExit('Use a local demo only.')
    out.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--hide-scrollbars',
                                f'--remote-debugging-port={port}', f'--user-data-dir={out.resolve() / "edge"}', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors, posts = [], []
    cases = 0
    try:
        page = None
        for _ in range(50):
            try:
                page = next(p for p in json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2).read()) if p['type'] == 'page')
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        assert page, 'Edge did not start'
        with connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            c = Cdp(ws)
            c.handlers['Runtime.exceptionThrown'] = lambda e: errors.append(e['exceptionDetails'].get('exception', {}).get('description', e['exceptionDetails']['text']))
            c.handlers['Network.requestWillBeSent'] = lambda e: posts.append(urlsplit(e['request']['url']).path) if e['request']['method'] == 'POST' else None
            for domain in ('Page', 'Runtime', 'Network'):
                c.cmd(domain + '.enable')
            shooter = Shooter(c, out, 'wiki-tools')
            c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script('en', 'light'))
            c.cmd('Page.navigate', url=url + '#wiki')
            wait_for(c, 'typeof state === "object" && state && !!document.querySelector(".wiki-directory")')
            assert js(c, 'state.demo'), 'Mutations require a demo server'
            js(c, '''(async () => {
              const s=await api('/api/wiki/tools/state');
              for(const id of s.bookmarks) await api('/api/wiki/tools/state',{action:'bookmark',article:id,selected:false});
              for(const id of s.compare) await api('/api/wiki/tools/state',{action:'compare',article:id,selected:false});
              for(const col of s.collections) if(col.name==='Browser reference collection') await api('/api/wiki/tools/state',{action:'collection_delete',collection:col.id});
            })()''')

            def visit(route: str, selector: str) -> None:
                js(c, 'location.hash = ' + json.dumps('#wiki/' + route))
                c.pump(.1)
                wait_for(c, '!!document.querySelector(' + json.dumps(selector) + ')')
                assert js(c, '!document.querySelector(".wt-error")'), js(c, '[...document.querySelectorAll(".wt-error")].map(n=>n.textContent)')

            visit('varieties/blue-dream', '.wt-gallery')
            wait_for(c, '!!document.querySelector(".wt-tree")')
            assert js(c, 'document.querySelectorAll(".wiki-contents-list button").length >= 6')
            js(c, 'document.querySelector("[data-bookmark]").click()')
            wait_for(c, 'document.querySelector("[data-bookmark]").getAttribute("aria-pressed") === "true"')
            js(c, 'document.querySelector("[data-compare-article]").click()')
            wait_for(c, 'document.querySelector("[data-compare-count]").textContent.includes("1/4")')
            # A native modal must close with Escape and restore keyboard focus.
            js(c, 'document.querySelector(".wt-thumb").focus();document.querySelector(".wt-thumb").click()')
            wait_for(c, '!!document.querySelector("dialog[open]")')
            c.cmd('Input.dispatchKeyEvent', type='keyDown', key='ArrowRight', code='ArrowRight')
            assert js(c, 'document.querySelector("dialog [aria-live]").textContent.startsWith("2 /")')
            c.cmd('Input.dispatchKeyEvent', type='keyDown', key='Escape', code='Escape')
            c.cmd('Input.dispatchKeyEvent', type='keyUp', key='Escape', code='Escape')
            wait_for(c, '!document.querySelector(".wt-dialog")')
            assert js(c, 'document.activeElement.classList.contains("wt-thumb")')

            visit('tools/library', '.wt-library .wt-saved-list')
            assert js(c, '''!!document.querySelector('.wt-library a[href="#wiki/varieties/blue-dream"]')''')
            js(c, '(() => { const f=document.querySelector(".wt-library > form");f.querySelector("input").value="Browser reference collection";f.requestSubmit(); })()')
            wait_for(c, '!!document.querySelector(".wt-collection")')
            visit('varieties/blue-dream', '.wt-collections-menu .wt-check')
            js(c, 'document.querySelector(".wt-collections-menu").open=true;document.querySelector(".wt-collections-menu input").click()')
            wait_for(c, 'document.querySelector(".wt-collections-menu input").checked && !document.querySelector(".wt-collections-menu input").disabled')
            visit('varieties/sour-diesel', '.wt-gallery')
            assert js(c, 'document.querySelector("[data-fact=classification] .wt-disputed") !== null')
            js(c, 'document.querySelector("[data-compare-article]").click()')
            wait_for(c, 'document.querySelector("[data-compare-count]").textContent.includes("2/4")')
            visit('tools/compare', '.wt-compare-table table')
            assert js(c, 'document.querySelectorAll(".wt-compare-table thead th").length === 3')

            # Queue a correction through the form and accept it through the review UI.
            visit('varieties/blue-dream', '.wt-gallery')
            js(c, '''document.querySelector('a[href^="#wiki/tools/history/"]').nextElementSibling.click()''')
            wait_for(c, '!!document.querySelector(".wt-dialog form")')
            js(c, '(() => {const f=document.querySelector(".wt-dialog form");const s=f.querySelector("select");s.value="thc_percent";s.dispatchEvent(new Event("change"));f.querySelectorAll("textarea")[0].value="22";f.querySelector("input[type=url]").value="https://example.org/browser-reference";f.querySelectorAll("textarea")[1].value="Browser acceptance reference correction";f.requestSubmit();})()')
            wait_for(c, '''!!document.querySelector('.wt-dialog a[href="#wiki/tools/editorial"]')''')
            js(c, 'document.querySelector("dialog").close()')
            visit('tools/editorial', '.wt-review-item')
            js(c, '(() => {const f=document.querySelector(".wt-review-item form");f.querySelector("textarea").value="Checked in isolated demo";f.requestSubmit(f.querySelector("button[value=accepted]"));})()')
            wait_for(c, '!document.querySelector(".wt-review-item") && !!document.querySelector(".wt-quality-item")')
            visit('varieties/blue-dream', '.wt-gallery')
            assert js(c, 'document.querySelector("[data-fact=thc_percent]").textContent.startsWith("22%")')
            visit('tools/history/varieties/blue-dream', '.wt-history-selectors')
            assert js(c, 'document.querySelectorAll(".wt-history select:first-of-type option").length >= 2 && !!document.querySelector(".wt-change")')
            visit('tools/atlas', '.wt-atlas-card')
            assert js(c, 'document.querySelectorAll(".wt-atlas-card").length === 6')
            js(c, 'document.querySelector(".wt-marker").click()')
            assert js(c, 'document.querySelector(".wt-observations .is-selected") === document.activeElement')
            js(c, 'document.querySelectorAll(".wt-atlas-card .wt-check input")[0].click();document.querySelectorAll(".wt-atlas-card .wt-check input")[1].click()')
            assert js(c, 'document.querySelectorAll(".wt-atlas-card").length === 2')

            # Reload from the server: favorites, membership, comparison and correction must persist.
            loader = c.cmd('Page.getFrameTree')['frameTree']['frame']['loaderId']
            c.cmd('Page.reload')
            end = time.monotonic() + 15
            while c.cmd('Page.getFrameTree')['frameTree']['frame']['loaderId'] == loader and time.monotonic() < end:
                c.pump(.1)
            wait_for(c, '!!document.querySelector(".wt-atlas-card")')
            visit('tools/library', '.wt-collection .wt-saved-list')
            assert js(c, '''document.querySelectorAll('.wt-library a[href="#wiki/varieties/blue-dream"]').length >= 3''')

            pref = None
            for lang in ('de', 'en'):
                for theme in ('light', 'dark'):
                    if pref:
                        c.cmd('Page.removeScriptToEvaluateOnNewDocument', identifier=pref)
                    pref = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script(lang, theme))['identifier']
                    c.cmd('Page.navigate', url=url + '#wiki/tools/compare')
                    wait_for(c, f'document.documentElement.lang === "{lang}" && !!document.querySelector(".wt-compare-table table")')
                    for width, height in ((1440, 1000), (768, 1024), (375, 844), (320, 700)):
                        shooter.emulate(width, height, mobile=width < 960)
                        for route, selector in (('tools/compare', '.wt-compare-table table'), ('tools/atlas', '.wt-atlas-card'),
                                                ('tools/library', '.wt-collection'), ('tools/editorial', '.wt-quality-item'),
                                                ('tools/history/varieties/blue-dream', '.wt-history-selectors'),
                                                ('varieties/blue-dream', '.wt-tree'), ('varieties', '.wiki-profile-grid')):
                            visit(route, selector)
                            assert js(c, 'document.documentElement.scrollWidth <= innerWidth'), (lang, theme, width, route, js(c, 'document.documentElement.scrollWidth'))
                            assert js(c, '!document.querySelector(".wiki-view").textContent.includes("undefined")'), (lang, route)
                            if width in (1440, 375) and route in ('tools/compare', 'tools/atlas', 'tools/library'):
                                if route == 'tools/atlas':
                                    js(c, 'document.querySelectorAll(".wt-atlas img").forEach(i => i.loading="eager")')
                                    wait_for(c, '[...document.querySelectorAll(".wt-atlas img")].every(i=>i.complete && i.naturalWidth>0)')
                                shooter.shot(f'{lang}-{theme}-{width}-{route.replace("/", "-")}')
                            cases += 1
            assert not errors, errors
            assert set(posts) <= {'/api/wiki/tools/state', '/api/wiki/tools/corrections/propose', '/api/wiki/tools/corrections/review'}, posts
            report = {'layout_cases': cases, 'persistence_after_reload': True, 'review_updates_article': True,
                      'photo_dialog_keyboard_focus': True, 'atlas_comparison': True, 'no_device_writes': True, 'errors': errors}
            (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--port', type=int, default=9387)
    args = parser.parse_args()
    run(os.environ['VGROW_URL'], args.out, args.port)
