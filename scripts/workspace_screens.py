"""Exercise box/plant records in an isolated local demo with Edge. Never operates hardware.

Set VGROW_URL to an authenticated localhost demo URL, then run:
  python scripts/workspace_screens.py _shots/workspace-browser
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
    if urlsplit(url).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise SystemExit('Use an isolated local demo URL.')
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--hide-scrollbars',
                             f'--remote-debugging-port={port}', f'--user-data-dir={out.resolve() / "edge"}',
                             'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors, posts = [], []
    try:
        page = None
        for _ in range(60):
            try:
                targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2))
                page = next(t for t in targets if t['type'] == 'page')
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
            preference = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script('en', 'light'))['identifier']
            c.cmd('Page.navigate', url=url + '#grow/growth')
            wait_for(c, '!!document.body && document.body.classList.contains("workspace-ready") && typeof state === "object" && !!state')
            assert js(c, 'state.demo === true'), 'Refusing mutations outside demo'
            shooter = Shooter(c, out, 'workspace')
            shooter.emulate(1440, 1000, mobile=False)

            def read():
                return js(c, 'api("/api/workspace")')

            def click(selector):
                assert js(c, f'!!document.querySelector({json.dumps(selector)})'), selector
                js(c, f'document.querySelector({json.dumps(selector)}).click()')
                c.pump(.08)

            def by_text(selector, text):
                expression = f'[...document.querySelectorAll({json.dumps(selector)})].find(n=>n.textContent.trim()==={json.dumps(text)})'
                assert js(c, f'!!({expression})'), (selector, text)
                js(c, f'({expression}).click()')
                c.pump(.08)

            def fill(values, scope='dialog[open]'):
                js(c, '(() => { const scope=document.querySelector(' + json.dumps(scope) + '); const values=' + json.dumps(values) + '; for(const [key,value] of Object.entries(values)){ const input=scope.querySelector(`[name="${key}"]`); if(!input) throw Error(key); input.value=value; input.dispatchEvent(new Event("input",{bubbles:true})); input.dispatchEvent(new Event("change",{bubbles:true})); } })()')

            def save():
                click('dialog[open] button[type=submit]')
                wait_for(c, '!document.querySelector("dialog[open]")')

            def select_box(ident):
                click(f'#workspace-switcher [data-box="{ident}"]')
                wait_for(c, f'PlantWorkspace.headers()["X-Workspace-Box"]==={json.dumps(ident)}')

            def select_plant(ident):
                click(f'#workspace-grow [data-plant="{ident}"]')
                wait_for(c, f'PlantWorkspace.headers()["X-Workspace-Plant"]==={json.dumps(ident)}')

            def reload():
                loader = c.cmd('Page.getFrameTree')['frameTree']['frame']['loaderId']
                c.cmd('Page.reload')
                deadline = time.monotonic() + 15
                while c.cmd('Page.getFrameTree')['frameTree']['frame']['loaderId'] == loader:
                    assert time.monotonic() < deadline, 'Page did not reload'
                    c.pump(.1)
                wait_for(c, '!!document.body && document.body.classList.contains("workspace-ready") && typeof state === "object" && !!state')

            click('.ws-start')
            assert js(c, 'document.querySelectorAll("dialog[open] .ws-guide-steps li").length') == 4
            by_text('dialog[open] button', 'Close')
            visit(c, 'overview')
            click('.ws-start')
            by_text('dialog[open] button', 'Add plant')
            wait_for(c, '!!document.querySelector("dialog[open] select[name=origin]")')
            assert js(c, 'location.hash') == '#grow/growth'
            by_text('dialog[open] button', 'Close')

            # A previous failed run may leave QA records, always confined to this demo.
            for box in read()['boxes']:
                if box['name'].startswith('QA_') and not box['archived']:
                    js(c, f'(async()=>{{const d=await api("/api/workspace"); await api("/api/workspace",{{action:"box_archive",id:{json.dumps(box["id"])},archived:true,revision:d.revision}});}})()')
            js(c, 'PlantWorkspace.refresh()')
            select_box('box-1')
            visit(c, 'grow/growth')
            click('.ws-add-box')
            fill({'name': 'QA_Cabinet', 'kind': 'Cabinet', 'capacity': 6, 'notes': 'Shared enclosure, separate records.'})
            save()
            cabinet = next(b['id'] for b in read()['boxes'] if b['name'] == 'QA_Cabinet' and not b['archived'])
            assert js(c, 'document.body.classList.contains("ws-unbound")')
            assert js(c, 'document.querySelector("#side-status").textContent===document.querySelector("#status-text").textContent && document.querySelector("#side-foot").textContent==="QA_Cabinet"')
            click('.ws-add-plant')
            fill({'name': 'QA_Mint', 'variety': 'Mint', 'stageLabel': 'Observed stage A', 'started': '2026-10-01', 'count': 6})
            save()
            records = [p for p in read()['plants'] if p['box'] == cabinet and not p['archived']]
            assert len(records) == 6 and len({p['id'] for p in records}) == 6
            assert [p['name'] for p in records] == [f'QA_Mint {i}' for i in range(1, 7)]
            plant, second = records[0]['id'], records[1]['id']
            select_plant(plant)
            by_text('#workspace-grow button', 'Edit plant record')
            fill({'name': 'QA_Basil', 'variety': 'Basil', 'stageLabel': 'Observed stage B', 'phase_since': '2026-10-19'})
            save()
            records = read()['plants']
            assert next(p for p in records if p['id'] == plant)['phase'] == 'Observed stage B'
            assert next(p for p in records if p['id'] == plant)['phase_age_days'] == 7
            assert next(p for p in records if p['id'] == plant)['phase_recorded'] == '2026-10-26'
            assert next(p for p in records if p['id'] == second)['phase'] == 'Observed stage A'
            fill({'day': '2026-10-26', 'cm': '24.5'}, '#workspace-grow .ws-inline-form')
            click('#workspace-grow .ws-inline-form button[type=submit]')
            wait_for(c, '!!document.querySelector(".ws-table tbody") && document.querySelector(".ws-table tbody").textContent.includes("24.5")')
            visit(c, 'grow/journal')
            fill({'day': '2026-10-26', 'text': 'Independent observation for this record.'}, '#workspace-grow .ws-form')
            click('#workspace-grow .ws-form button[type=submit]')
            wait_for(c, 'document.querySelector(".ws-journal").textContent.includes("Independent observation")')
            select_plant(second)
            assert not js(c, '!!document.querySelector(".ws-journal li")')
            visit(c, 'grow/growth')
            assert not js(c, '!!document.querySelector(".ws-table tbody")')
            select_plant(plant)
            by_text('#workspace-grow button', 'Edit plant record')
            fill({'box': 'box-1'})
            save()
            assert js(c, 'PlantWorkspace.headers()["X-Workspace-Box"]==="box-1"')
            assert js(c, 'document.querySelector(".ws-table tbody").textContent.includes("24.5")')
            assert js(c, '!PlantWorkspace.assistantAllowed()')
            visit(c, 'grow/insights')
            assert js(c, '!document.body.classList.contains("ws-legacy-view") && !document.querySelector("#card-plant").getBoundingClientRect().width')
            visit(c, 'grow/growth')
            by_text('#workspace-grow button', 'Edit plant record')
            fill({'box': cabinet})
            save()
            by_text('#workspace-grow button', 'Edit plant record')
            by_text('dialog[open] button', 'Archive plant')
            wait_for(c, '!document.querySelector("dialog[open]")')
            assert next(p for p in read()['plants'] if p['id'] == plant)['archived']
            click('.ws-archive')
            js(c, '[...document.querySelectorAll(".ws-archive-row")].find(n=>n.textContent.includes("QA_Basil")).querySelectorAll("button")[1].click()')
            wait_for(c, '!document.querySelector(".ws-archive-list").textContent.includes("QA_Basil")')
            by_text('dialog[open] button', 'Close')
            select_plant(plant)
            assert next(p for p in read()['plants'] if p['id'] == plant)['journal'][0]['text'] == 'Independent observation for this record.'
            shooter.shot('six-plants-desktop')
            reload()
            assert js(c, 'PlantWorkspace.headers()["X-Workspace-Plant"]') == plant
            # A failed save retains the user's draft and creates no phantom record.
            js(c, 'window.realFetch=window.fetch; window.fetch=(url,opts)=>url==="/api/workspace"&&opts?.method==="POST"?Promise.resolve(new Response(JSON.stringify({error:"workspace_storage"}),{status:503,headers:{"Content-Type":"application/json"}})):window.realFetch(url,opts)')
            click('.ws-add-plant')
            fill({'name': 'QA_Unsaved'})
            click('dialog[open] button[type=submit]')
            wait_for(c, '!!document.querySelector("dialog[open] .ws-error")')
            assert js(c, 'document.querySelector("dialog input[name=name]").value') == 'QA_Unsaved'
            assert not any(p['name'] == 'QA_Unsaved' for p in read()['plants'])
            js(c, 'window.fetch=window.realFetch')
            c.cmd('Input.dispatchKeyEvent', type='keyDown', key='Escape', code='Escape', windowsVirtualKeyCode=27)
            c.cmd('Input.dispatchKeyEvent', type='keyUp', key='Escape', code='Escape', windowsVirtualKeyCode=27)
            wait_for(c, '!document.querySelector("dialog[open]")')
            assert js(c, 'document.activeElement.classList.contains("ws-add-plant")')
            # A second client editing the database cannot silently overwrite this draft.
            by_text('#workspace-grow button', 'Edit plant record')
            fill({'notes': 'Draft retained after conflict.'})
            js(c, f'(async()=>{{const d=await api("/api/workspace"); const b=d.boxes.find(b=>b.id==={json.dumps(cabinet)}); await api("/api/workspace",{{...b,action:"box_update",revision:d.revision,notes:"Other client"}});}})()')
            click('dialog[open] button[type=submit]')
            wait_for(c, '!!document.querySelector("dialog[open] .ws-error")')
            assert js(c, 'document.querySelector("dialog textarea[name=notes]").value') == 'Draft retained after conflict.'
            save()
            assert next(p for p in read()['plants'] if p['id'] == plant)['notes'] == 'Draft retained after conflict.'
            assert next(p for p in read()['plants'] if p['id'] == plant)['phase_since'] == '2026-10-19'
            print('Passed record flows: batch, independent stages, measurement, journal, move, archive/restore, reload, failure, conflict, Escape/focus')

            # Device-dependent pages must not show the connected controller under a new box.
            for route in ('overview', 'automation', 'devices/controls', 'analytics', 'activity/history'):
                visit(c, route)
                assert js(c, '!!document.querySelector("[data-page]:not([hidden]).ws-blocked-view .ws-context-empty")')
                assert js(c, '[...document.querySelector("[data-page]:not([hidden])").children].every(n=>n.classList.contains("ws-context-empty") || !n.getBoundingClientRect().width)')
            select_box('box-1')
            visit(c, 'devices/controls')
            assert js(c, '!document.body.classList.contains("ws-unbound") && !document.querySelector("#page-devices").classList.contains("ws-blocked-view")')
            assert js(c, 'document.querySelector("#status-text").textContent') != 'No controller connected'
            assert js(c, 'document.querySelector("#side-status").textContent===document.querySelector("#status-text").textContent')
            select_plant('existing-plant')
            assert js(c, 'PlantWorkspace.assistantAllowed()')
            visit(c, 'grow/growth')
            assert js(c, '!document.querySelector("#card-plant").getBoundingClientRect().width')
            print('Passed box context: unconnected page gates, connected status restore, original profile isolation')

            # A dated legacy entry must not be presented as the actual observed duration.
            visit(c, 'grow/insights')
            js(c, 'window.dateTestSnapshot=state')
            for language, heading, age, height_source in (('en', 'Stage entry', '2 days ago', 'manually'),
                                                          ('de', 'Phaseneintrag', 'Vor 2 Tagen', 'manuell')):
                js(c, f'LANG={json.dumps(language)};applyStatic();render({{...dateTestSnapshot,plant:{{...dateTestSnapshot.plant,stage:{{...dateTestSnapshot.plant.stage,set_by:"manual",since:"2026-10-24"}}}}}})')
                assert js(c, 'document.querySelector("#plant-record-kind").textContent') == heading
                assert js(c, 'document.querySelector("#plant-since").textContent') == age
                assert height_source in js(c, 'document.querySelector("#plant-h-date").textContent')
            js(c, 'render(dateTestSnapshot)')
            visit(c, 'grow/growth')
            print('Passed date semantics: entry date stays distinct from observed duration; height provenance is explicit')

            # Simulate tomorrow in a tab left open; the server response is supplied by this demo-only fixture.
            js(c, 'LANG="en";applyStatic();PlantWorkspace.refresh()')
            select_box(cabinet)
            select_plant(plant)
            js(c, '''window.realObservationDate=Date; window.realObservationApi=api; window.observationDayReads=0;
                api=async (...args)=>{const result=await realObservationApi(...args); if(args[0]==='/api/workspace' && !args[1]){
                  observationDayReads++; for(const p of result.plants){if(p.age_days!==null)p.age_days++;if(p.phase_age_days!==null)p.phase_age_days++;}
                } return result;};
                window.Date=class extends realObservationDate { constructor(...args){super(...(args.length?args:[realObservationDate.now()+86400000]));} };
                document.dispatchEvent(new CustomEvent('vgrow:state',{detail:state}));''')
            wait_for(c, 'document.querySelector("#workspace-grow .ws-metrics").textContent.includes("Days: 8")')
            assert js(c, 'observationDayReads') == 1
            js(c, 'document.dispatchEvent(new CustomEvent("vgrow:state",{detail:state}))')
            assert js(c, 'observationDayReads') == 1
            js(c, 'window.Date=realObservationDate;api=realObservationApi;PlantWorkspace.refresh()')
            assert js(c, 'document.querySelector("#workspace-grow .ws-metrics").textContent.includes("Days: 7")')
            print('Passed calendar rollover: open tab refreshes age once on the next day')

            cases = 0
            for lang in ('en', 'de'):
                for theme in ('light', 'dark'):
                    c.cmd('Page.removeScriptToEvaluateOnNewDocument', identifier=preference)
                    preference = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script(lang, theme))['identifier']
                    reload()
                    for native in (False, True):
                        select_box('box-1' if native else cabinet)
                        for width, height in ((1440, 1000), (768, 1024), (375, 844), (320, 740)):
                            shooter.emulate(width, height, mobile=width < 960)
                            for route in ('grow/growth', 'grow/journal', 'overview', 'devices/controls', 'analytics', 'automation'):
                                visit(c, route)
                                assert not js(c, 'document.documentElement.scrollWidth > innerWidth'), (lang, theme, native, width, route, 'overflow')
                                assert js(c, '[...document.querySelectorAll(".ws-field")].every(n=>!!n.control || n.tagName!=="LABEL" || !!n.querySelector("input,select,textarea"))')
                                if not native and route == 'grow/growth' and width in (1440, 375):
                                    shooter.shot(f'{lang}-{theme}-{width}-six-plants')
                                cases += 1
                            visit(c, 'grow/growth')
                            click('.ws-add-plant')
                            assert not js(c, 'document.querySelector("dialog").scrollWidth>document.querySelector("dialog").clientWidth'), (lang, theme, width, 'dialog overflow')
                            if not native and width == 375:
                                shooter.shot(f'{lang}-{theme}-mobile-dialog')
                            js(c, 'document.querySelector("dialog[open]").close()')
                    print(f'Passed layouts {lang}/{theme}')

            # Finish the imported record in this isolated demo, preserve it and start an independent successor.
            js(c, 'LANG="en";applyStatic();PlantWorkspace.refresh()')
            select_box('box-1')
            select_plant('existing-plant')
            visit(c, 'grow/growth')
            original_name = next(p['name'] for p in read()['plants'] if p['id'] == 'existing-plant')
            click('.ws-finish')
            fill({'ended': '2026-10-26', 'summary': 'Stored original observations.'})
            js(c, 'document.querySelector("dialog input[name=confirmed]").checked=true')
            click('dialog[open] button[type=submit]')
            wait_for(c, '!!document.querySelector(".ws-archive-list")')
            finished = next(p for p in read()['plants'] if p['id'] == 'existing-plant')
            assert finished['ended'] == '2026-10-26' and not finished['linked_profile']
            assert not js(c, 'PlantWorkspace.assistantAllowed()')
            js(c, f'[...document.querySelectorAll(".ws-archive-row")].find(n=>n.textContent.includes({json.dumps(original_name)})).querySelector("button").click()')
            wait_for(c, '!!document.querySelector("dialog[open] a[download]")')
            saved_record = js(c, 'api("/api/workspace/records/existing-plant")')
            assert saved_record['record']['name'] == original_name
            assert 'Stored original observations.' in js(c, 'document.querySelector("dialog[open]").textContent')
            by_text('dialog[open] button', 'Close')
            click('.ws-add-plant')
            fill({'name': 'QA_NextRecord', 'origin': 'existing', 'started': '2026-10-26'})
            save()
            replacement = next(p for p in read()['plants'] if p['name'] == 'QA_NextRecord')
            assert replacement['id'] != 'existing-plant' and replacement['origin'] == 'existing'
            assert replacement['heights'] == [] and replacement['journal'] == []
            assert js(c, 'api("/api/workspace/records/existing-plant")') == saved_record
            assert 'QA_NextRecord' in js(c, 'document.querySelector("#workspace-grow").textContent')
            assert not js(c, 'PlantWorkspace.assistantAllowed()')
            for lang in ('en', 'de'):
                js(c, f'LANG={json.dumps(lang)};applyStatic();PlantWorkspace.refresh()')
                for width in (1440, 768, 375, 320):
                    shooter.emulate(width, 1000 if width > 700 else 844, mobile=width < 960)
                    click('.ws-start')
                    assert not js(c, 'document.querySelector("dialog").scrollWidth>document.querySelector("dialog").clientWidth')
                    js(c, 'document.querySelector("dialog").scrollTop=document.querySelector("dialog").scrollHeight')
                    assert js(c, '(()=>{const d=document.querySelector("dialog").getBoundingClientRect(),b=document.querySelector("dialog .ws-dialog-head button").getBoundingClientRect();return b.top>=d.top && b.bottom<=d.bottom})()')
                    js(c, 'document.querySelector("dialog").scrollTop=0')
                    shooter.shot(f'{lang}-{width}-start-guide')
                    js(c, 'document.querySelector("dialog[open]").close()')
                    click('.ws-archive')
                    js(c, f'[...document.querySelectorAll(".ws-archive-row")].find(n=>n.textContent.includes({json.dumps(original_name)})).querySelector("button").click()')
                    wait_for(c, '!!document.querySelector("dialog[open] a[download]")')
                    assert not js(c, 'document.querySelector("dialog").scrollWidth>document.querySelector("dialog").clientWidth')
                    shooter.shot(f'{lang}-{width}-completed-history')
                    js(c, 'document.querySelector("dialog[open]").close()')
            print('Passed lifecycle: visible guide, origins, completed imported record, retained history, independent successor and model isolation')
            assert set(posts) <= {'/api/workspace'}, ('Unexpected POST', posts)
            assert not errors, errors
            print(json.dumps({'layout_cases': cases, 'console_errors': len(errors), 'post_endpoints': sorted(set(posts))}))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--port', type=int, default=9391)
    args = parser.parse_args()
    run(os.environ['VGROW_URL'], args.out, args.port)
