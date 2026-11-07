"""Browser acceptance for the wiki directory, topic pages and factual strain profiles.

VGROW_URL must point to an isolated local demo. Never submits settings or actuator commands.
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


def run(url: str, out: Path, port: int) -> None:
    assert urlsplit(url).hostname in ('127.0.0.1', 'localhost', '::1'), 'Use an isolated local demo'
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', f'--remote-debugging-port={port}',
                             f'--user-data-dir={out.resolve() / "edge"}', 'about:blank'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors, posts = [], []
    cases = 0
    try:
        for _ in range(50):
            try:
                page = next(t for t in json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/json').read()) if t['type'] == 'page')
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        with connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            c = Cdp(ws)
            c.handlers['Runtime.exceptionThrown'] = lambda e: errors.append(e['exceptionDetails'].get('exception', {}).get('description', e['exceptionDetails']['text']))
            c.handlers['Network.requestWillBeSent'] = lambda e: posts.append(urlsplit(e['request']['url']).path) if e['request']['method'] == 'POST' else None
            for domain in ('Page', 'Runtime', 'Network'):
                c.cmd(domain + '.enable')
            shooter = Shooter(c, out, 'lexicon')
            pref = None
            for lang in ('de', 'en'):
                for theme in ('light', 'dark'):
                    if pref:
                        c.cmd('Page.removeScriptToEvaluateOnNewDocument', identifier=pref)
                    pref = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script(lang, theme))['identifier']
                    c.cmd('Page.navigate', url=url + '#wiki')
                    wait_for(c, "typeof state === 'object' && state && !!document.querySelector('.wiki-directory')")
                    assert js(c, 'state.demo')
                    for width, height in ((1920, 1080), (1280, 900), (768, 1024), (375, 844), (320, 700)):
                        shooter.emulate(width, height, mobile=width < 960)
                        for route in ('wiki', 'wiki/topic/nutrient-issues', 'wiki/topic/pests', 'wiki/topic/diseases',
                                      'wiki/topic/stress', 'wiki/varieties', 'wiki/varieties/blue-dream', 'wiki/health/oedema'):
                            visit(c, route)
                            selector = '.wiki-directory' if route == 'wiki' else '.wiki-profile-grid' if route == 'wiki/varieties' else '.wiki-topic-results' if '/topic/' in route else '.wiki-sec'
                            wait_for(c, '!!document.querySelector(' + json.dumps(selector) + ')')
                            assert js(c, 'document.documentElement.scrollWidth <= innerWidth'), (lang, theme, width, route)
                            assert js(c, "!document.querySelector('.wiki-error')"), (lang, theme, route)
                            if route == 'wiki':
                                assert js(c, "document.querySelectorAll('.wiki-topic[data-topic=nutrient-issues]').length === 1 && !document.querySelector('.wiki-index-category, .wiki-catbox, .wiki-tiles')")
                                assert js(c, "document.querySelector('.wiki-topic[data-topic=varieties] .wiki-topic-count').textContent === '100'")
                            if width in (1280, 375) and route in ('wiki', 'wiki/varieties', 'wiki/varieties/blue-dream', 'wiki/topic/diseases'):
                                shooter.shot(f'{lang}-{theme}-{width}-{route.replace("/", "-")}')
                            cases += 1
            visit(c, 'wiki/varieties')
            wait_for(c, "document.querySelectorAll('.wiki-profile-grid > li').length === 24")
            js(c, "document.querySelector('.wiki-more-btn').click()")
            assert js(c, "document.querySelectorAll('.wiki-profile-grid > li').length === 48")
            js(c, "document.querySelector('.wiki-profile-filter[data-type=indica]').click()")
            assert js(c, "document.querySelector('.wiki-profile-filter[data-type=indica]').getAttribute('aria-pressed') === 'true'")
            js(c, "(() => {document.querySelector('.wiki-profile-filter[data-type=\"\"]').click(); const q=document.querySelector('.wiki-q'); q.value='Blue Dream'; q.dispatchEvent(new Event('input'));})()")
            assert js(c, "document.querySelectorAll('.wiki-profile-grid > li').length === 1")
            js(c, "document.querySelector('.wiki-profile-grid a').click()")
            wait_for(c, "location.hash === '#wiki/varieties/blue-dream' && !!document.querySelector('.wiki-profile-facts')")
            assert js(c, "document.querySelector('.wiki-profile-facts').textContent.includes('21%') && !!document.querySelector('.wiki-credit a[href^=\"https://www.leafly.com/\"]')")
            image_ok = js(c, "!!document.querySelector('.wiki-reference-photo img') && document.querySelector('.wiki-reference-photo img').complete && document.querySelector('.wiki-reference-photo img').naturalWidth>0")
            if not image_ok:
                wait_for(c, "!!document.querySelector('.wiki-reference-photo img') && document.querySelector('.wiki-reference-photo img').naturalWidth>0", seconds=20)
            js(c, "document.querySelector('.wiki-reference-photo img').dispatchEvent(new Event('error'))")
            assert js(c, "document.querySelector('.wiki-reference-photo').textContent.includes(t('wiki.lex.photoMissing'))")
            js(c, 'window.history.back()')
            wait_for(c, "location.hash === '#wiki/varieties' && document.querySelectorAll('.wiki-profile-grid > li').length === 1")
            visit(c, 'wiki/varieties/crunch-berries')
            wait_for(c, "!!document.querySelector('.wiki-reference-photo')")
            assert js(c, "document.querySelector('.wiki-reference-photo').textContent.includes(t('wiki.lex.photoRepresentative'))")
            visit(c, 'wiki/topic/diseases')
            js(c, "(() => {const q=document.querySelector('.wiki-q');q.value='mildew';q.dispatchEvent(new Event('input'));})()")
            assert js(c, "document.querySelectorAll('.wiki-topic-articles > li').length === 2")
            js(c, "document.querySelector('.wiki-topic-articles a').click()")
            wait_for(c, "location.hash.startsWith('#wiki/health/') && !!document.querySelector('.wiki-sec')")
            js(c, 'window.history.back()')
            wait_for(c, "location.hash === '#wiki/topic/diseases' && document.querySelectorAll('.wiki-topic-articles > li').length === 2")
            visit(c, 'wiki')
            js(c, "(() => {const q=document.querySelector('.wiki-q');q.value='Blue Dream';q.dispatchEvent(new Event('input'));})()")
            wait_for(c, "!!document.querySelector('.wiki-results a[href=\"#wiki/varieties/blue-dream\"]')")
            assert not errors and set(posts) <= {'/api/wiki/tools/state'}, (errors, posts)
            report = {'layout_cases': cases, 'catalogue_count': 100, 'paging_filter_search_history': True,
                      'real_reference_image_loaded': True, 'image_failure_fallback': True,
                      'representative_photo_label': True, 'global_search': True, 'errors': errors, 'posts': posts}
            (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--port', type=int, default=9383)
    args = parser.parse_args()
    run(os.environ['VGROW_URL'], args.out, args.port)
