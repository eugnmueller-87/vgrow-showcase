"""Browser acceptance for the community desk. Uses a local demo only; never sends messages or device commands."""
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

from cockpit_screens import js, visit, wait_for
from screenshots import Cdp, EDGE, Shooter, prefs_script
from websockets.sync.client import connect


def check_flows(c: Cdp):
    """Shared with the cockpit acceptance script. All account windows are simulated here."""
    assert js(c, 'state.demo===true'), 'Community checks require a local demo'
    visit(c, 'community')
    wait_for(c, 'document.querySelectorAll(".comm-card").length===6')
    js(c, '''window.nativeCommunityFetch=window.fetch; window.nativeCommunityOpen=window.open;
      window.communityPopups=[];
      window.open=(url,name,features)=>{
        const popup={closed:false,opener:window,focuses:0,name,features,
          document:{createElement:document.createElement.bind(document),head:{append(){}}},
          location:{url:null,navigations:[],replace(url){this.url=url;this.navigations.push(url)}},
          focus(){this.focuses++}};
        Object.defineProperty(popup.location,'href',{set(url){this.url=url;this.navigations.push(url)}});
        window.communityPopups.push(popup);return popup;
      };''')

    def click(selector):
        js(c, f'document.querySelector({json.dumps(selector)}).click()')
        c.pump(.08)

    def search(value):
        js(c, f'(()=>{{const input=document.querySelector(".comm-search input");input.value={json.dumps(value)};input.dispatchEvent(new Event("input",{{bubbles:true}}));}})()')

    click('[data-community-filter=stories]')
    assert js(c, 'document.querySelectorAll(".comm-card").length===3')
    search('a community project')
    assert js(c, 'document.querySelectorAll(".comm-card").length===1')
    click('.comm-save')
    click('[data-community-filter=saved]')
    assert js(c, 'document.querySelectorAll(".comm-card").length===1 && document.querySelector(".comm-save").getAttribute("aria-pressed")==="true"')
    search('')
    click('[data-community-filter=posts]')
    assert js(c, 'document.querySelectorAll(".comm-card-post").length===2')
    # Reddit uses native links instead of the named popup path. Inspect/prevent the test navigation
    # only after the production handler has run; no external account page is opened by acceptance.
    assert js(c, '''[...document.querySelectorAll('.comm-card-post .comm-read,.comm-tool-reddit .comm-launch,.comm-quick-tools a')].every(a=>
      a.tagName==='A' && a.target==='_blank' && a.rel==='noopener noreferrer' && new URL(a.href).hostname==='www.reddit.com')''')
    js(c, '''window.redditNavigation=null;const a=document.querySelector('.comm-card-post .comm-read');
      a.addEventListener('click',e=>{window.redditNavigation={url:a.href,alreadyPrevented:e.defaultPrevented};e.preventDefault()},{once:true});a.click();''')
    assert js(c, 'communityPopups.length===0 && redditNavigation.alreadyPrevented===false && !document.querySelector(".comm-card-post").querySelector(".comm-unread") && document.querySelector(".comm-notice").hidden')
    assert js(c, '''[...document.querySelectorAll('.comm-reddit-help a')].map(a=>a.href).join('|')===
      'https://www.reddit.com/login/|https://support.reddithelp.com/hc/en-us/requests/new' ''')
    # Clipboard writes can be unavailable on the installation's HTTP LAN address or denied by the browser.
    js(c, '''Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async url=>{window.redditCopied=url}}})''')
    click('.comm-card-post .comm-copy')
    assert js(c, 'redditCopied===document.querySelector(".comm-card-post .comm-read").href && document.querySelector(".comm-copy-field").hidden && !!document.querySelector(".comm-copy-status").textContent')
    js(c, '''Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('denied')}}})''')
    click('.comm-card-post .comm-copy')
    assert js(c, '''(()=>{const field=document.querySelector('.comm-copy-field');return !field.hidden && field.readOnly && field.value===redditNavigation.url && document.activeElement===field && field.selectionEnd===field.value.length})()''')
    js(c, "Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})")
    click('.comm-card-post .comm-copy')
    assert js(c, '!document.querySelector(".comm-copy-field").hidden')
    js(c, "delete navigator.clipboard")
    click('[data-community-filter=all]')
    js(c, 'window.communityCardBefore=document.querySelector(".comm-card");scrollTo(0,500)')
    c.pump(.2)
    position = js(c, 'scrollY')
    visit(c, 'devices/controls')
    visit(c, 'community')
    c.pump(.2)
    assert js(c, 'window.communityCardBefore===document.querySelector(".comm-card")')
    assert abs(js(c, 'scrollY') - position) < 16, ('Community scroll lost', position, js(c, 'scrollY'))
    click('[data-community-window=discord]')
    click('[data-community-window=discord]')
    assert js(c, 'communityPopups.length===1 && communityPopups[0].focuses===2 && communityPopups[0].opener===null && communityPopups[0].location.url==="https://discord.com/invite/DHp7a6jxNk"')
    click('[data-community-window=vivosun]')
    click('.comm-signin')
    click('[data-community-window=vivosun]')
    assert js(c, 'communityPopups.length===2 && communityPopups[1].location.url.includes("/account/login") && communityPopups[1].location.navigations.length===2')
    visit(c, 'overview')
    visit(c, 'community')
    click('[data-community-window=discord]')
    assert js(c, 'communityPopups.length===2 && communityPopups[0].focuses===3')
    # Denied popups get a visible, ordinary fallback link. No automatic retry loop.
    js(c, 'communityPopups.forEach(p=>p.closed=true);window.open=()=>null')
    click('[data-community-window=facebook]')
    assert js(c, '!document.querySelector(".comm-notice").hidden && document.querySelector(".comm-notice a").href==="https://www.facebook.com/groups/477264677334391/" && document.querySelector(".comm-notice a").rel==="noopener noreferrer"')
    assert js(c, 'location.hash==="#community"')
    click('[data-community-filter=videos]')
    assert not js(c, '!!document.querySelector(".comm-player iframe")')
    click('.comm-card .comm-read')
    assert js(c, '!!document.querySelector(".comm-player[open]") && document.querySelector(".comm-player iframe").src.startsWith("https://www.youtube-nocookie.com/embed/HZOcNR8eczc?") && document.querySelector(".comm-player iframe").referrerPolicy==="strict-origin-when-cross-origin"')
    c.cmd('Input.dispatchKeyEvent', type='keyDown', key='Escape', code='Escape', windowsVirtualKeyCode=27)
    c.cmd('Input.dispatchKeyEvent', type='keyUp', key='Escape', code='Escape', windowsVirtualKeyCode=27)
    wait_for(c, '!document.querySelector(".comm-player")')
    assert js(c, 'document.activeElement.classList.contains("comm-read")')
    # A network outage retains the existing feed and marks all displayed source snapshots as cached.
    js(c, 'window.fetch=(url,opts)=>String(url).startsWith("/api/community")?Promise.reject(Error("Offline acceptance")):nativeCommunityFetch(url,opts)')
    click('.comm-refresh')
    wait_for(c, '!document.querySelector(".comm-refresh").disabled')
    assert js(c, 'document.querySelectorAll(".comm-card").length===1 && document.querySelector(".comm-content .comm-status").classList.contains("is-stale")')
    click('[data-community-filter=saved]')
    assert js(c, 'document.querySelectorAll(".comm-card").length===1')
    js(c, 'window.fetch=nativeCommunityFetch;window.open=nativeCommunityOpen')
    click('.comm-refresh')
    wait_for(c, '!document.querySelector(".comm-refresh").disabled')
    search('')
    click('[data-community-filter=all]')
    # Long feeds paginate, unsafe links are discarded, and provider text cannot execute as markup.
    js(c, '''(async()=>{
      const fixture=await api('/api/community');const story=fixture.items.find(item=>item.kind==='story');
      const items=Array.from({length:20},(_,n)=>({...story,id:'qa-post-'+n,title:'Public item '+n}));
      items.unshift({...story,id:'qa-html',title:'<img src=x onerror=window.communityXSS=true>',image:'javascript:alert(1)'});
      items.push({...story,id:'qa-bad-url',url:'javascript:alert(1)'});
      window.fetch=(url,opts)=>String(url).startsWith('/api/community')?Promise.resolve(new Response(JSON.stringify({...fixture,items}),{headers:{'Content-Type':'application/json'}})):nativeCommunityFetch(url,opts);
    })()''')
    click('.comm-refresh')
    wait_for(c, '!document.querySelector(".comm-refresh").disabled')
    assert js(c, 'document.querySelectorAll(".comm-card").length===12 && !!document.querySelector(".comm-more") && !window.communityXSS && !document.querySelector("[data-community-item=qa-html] h3 img") && !document.querySelector("[data-community-item=qa-bad-url]")')
    click('.comm-more')
    assert js(c, 'document.querySelectorAll(".comm-card").length===21 && !document.querySelector(".comm-more")')
    js(c, 'window.fetch=nativeCommunityFetch')
    click('.comm-refresh')
    wait_for(c, 'document.querySelectorAll(".comm-card").length===6')
    return {'feed': True, 'search': True, 'saved': True, 'source_filters': True, 'scroll_restored': True,
            'window_reuse': True, 'login_not_reset': True, 'popup_fallback': True, 'player_escape_focus': True, 'offline_snapshot': True,
            'pagination': True, 'safe_metadata': True, 'reddit_native_links': True, 'reddit_read_marker': True,
            'reddit_no_false_success': True, 'copy_success_denied_unavailable': True, 'reddit_recovery_links': True}


def check_image_cards(c: Cdp):
    """Reject small/short/broken images before display; verify resizing and retina pixel coverage."""
    requests = []

    def image(event):
        url = event['request']['url']
        requests.append(url)
        width, height = (140, 105) if 'tiny' in url else (1600, 120) if 'short' in url else (640, 360) if 'border' in url else (2000, 1125)
        preview = base64.b64encode(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="{width}" height="{height}" fill="#25714b"/></svg>'.encode()).decode()
        c.cmd('Fetch.fulfillRequest', requestId=event['requestId'], responseCode=404 if 'missing' in url else 200,
              responseHeaders=[{'name': 'Content-Type', 'value': 'image/svg+xml'}], body='' if 'missing' in url else preview)

    def viewport(width, density=2):
        c.cmd('Emulation.setDeviceMetricsOverride', width=width, height=1000, deviceScaleFactor=density, mobile=width<960)
        c.pump(.2)

    def valid_visible_images():
        return js(c, """[...document.querySelectorAll('.comm-picture:not([hidden])')].every(p=>{
          const i=p.querySelector('img'),r=p.getBoundingClientRect(),d=devicePixelRatio;
          return i.complete && i.naturalWidth>=640 && i.naturalHeight>=360 && i.naturalWidth>=Math.ceil(r.width*d) && i.naturalHeight>=Math.ceil(r.height*d);
        })""")

    c.handlers['Fetch.requestPaused'] = image
    c.cmd('Fetch.enable', patterns=[{'urlPattern': 'https://preview.redd.it/*'}, {'urlPattern': 'https://image.next.vivosun.com/*'}])
    viewport(1920)
    try:
        js(c, '''(async()=>{
          const fixture=await api('/api/community');
          fixture.items[0].image='https://preview.redd.it/qa-community-high.svg';
          fixture.items[1].image='https://preview.redd.it/qa-community-tiny.svg';
          fixture.items[2].image='https://image.next.vivosun.com.evil.example/track.jpg';
          fixture.items[3].image='https://image.next.vivosun.com/qa-community-border.svg';
          fixture.items[4].image='https://image.next.vivosun.com/qa-community-short.svg';
          fixture.items[5].image='https://image.next.vivosun.com/qa-community-missing.svg';
          window.fetch=(url,opts)=>String(url).startsWith('/api/community')?Promise.resolve(new Response(JSON.stringify(fixture),{headers:{'Content-Type':'application/json'}})):nativeCommunityFetch(url,opts);
          document.querySelector('[data-community-filter=all]').click();
          document.querySelector('.comm-refresh').click();
        })()''')
        wait_for(c, '!document.querySelector(".comm-refresh").disabled && document.querySelectorAll(".comm-picture img").length===4')
        wait_for(c, 'document.querySelectorAll(".comm-picture:not([hidden])").length===1')
        assert valid_visible_images()
        assert js(c, '''document.querySelector('[data-community-item=demo-post-0] img').referrerPolicy==='no-referrer' &&
          !document.querySelector('[data-community-item=demo-story-0] img') &&
          document.querySelector('[data-community-item=demo-post-1] .comm-picture').getBoundingClientRect().height===0 &&
          !document.querySelector('.comm-cover-art,.comm-cover-note')''')
        # A source may fit a narrow display but fail after resizing or moving to a denser screen.
        viewport(320)
        wait_for(c, 'document.querySelectorAll(".comm-picture:not([hidden])").length===2')
        assert valid_visible_images()
        viewport(1920, 1)
        wait_for(c, '!document.querySelector("[data-community-item=demo-story-1] .comm-picture").hidden')
        assert valid_visible_images()
        viewport(1920, 2)
        wait_for(c, 'document.querySelector("[data-community-item=demo-story-1] .comm-picture").hidden')
        assert valid_visible_images()
        js(c, 'document.querySelector("[data-community-item=demo-post-0] .comm-save").click();document.querySelector("[data-community-filter=saved]").click()')
        wait_for(c, '!!document.querySelector("[data-community-item=demo-post-0].comm-card-with-image")')
        js(c, 'document.querySelector("[data-community-item=demo-post-0] .comm-save").click()')
        assert js(c, 'document.querySelectorAll(".comm-card").length===1')
        assert not any('evil.example' in url for url in requests)
    finally:
        js(c, 'window.fetch=nativeCommunityFetch;document.querySelector("[data-community-filter=all]").click();document.querySelector(".comm-refresh").click()')
        wait_for(c, '!document.querySelector(".comm-refresh").disabled')
        c.cmd('Fetch.disable')
        del c.handlers['Fetch.requestPaused']
        viewport(1440)
    return {'source_photos': True, 'low_resolution_rejected': True, 'short_crop_rejected': True,
            'broken_image_omitted': True, 'no_placeholder_panels': True, 'resize_and_retina_checked': True,
            'unsafe_image_blocked': True, 'saved_photo': True}


def run(url: str, out: Path, port: int):
    if urlsplit(url).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise SystemExit('Use a local demo URL.')
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--hide-scrollbars', f'--remote-debugging-port={port}',
                             f'--user-data-dir={out.resolve() / "edge"}', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors, posts = [], []
    try:
        page = None
        for _ in range(60):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2)) if t['type'] == 'page')
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
            # Test player lifecycle without loading third-party scripts or playing any media.
            c.cmd('Network.setBlockedURLs', urls=['*youtube-nocookie.com/*'])
            preference = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script('en', 'light') + "localStorage.removeItem('vgrow.community.desk');")['identifier']
            c.cmd('Page.navigate', url=url + '#overview')
            wait_for(c, '!!document.body && document.body.classList.contains("workspace-ready") && typeof state === "object" && !!state')
            assert js(c, 'state.demo===true')
            c.cmd('Page.removeScriptToEvaluateOnNewDocument', identifier=preference)
            preference = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script('en', 'light'))['identifier']
            js(c, 'document.fonts.ready.then(()=>true)')
            shooter = Shooter(c, out, 'community')
            shooter.emulate(1440, 1000, mobile=False)
            flows = check_flows(c)
            flows.update(check_image_cards(c))
            print('Passed community flows:', json.dumps(flows))

            def reload():
                loader = c.cmd('Page.getFrameTree')['frameTree']['frame']['loaderId']
                c.cmd('Page.reload')
                deadline = time.monotonic() + 15
                while c.cmd('Page.getFrameTree')['frameTree']['frame']['loaderId'] == loader:
                    assert time.monotonic() < deadline
                    c.pump(.1)
                wait_for(c, '!!document.body && document.body.classList.contains("workspace-ready") && typeof state === "object" && !!state')
                wait_for(c, '!!document.querySelector(".comm-card")')

            # Saved cards and the selected view survive a reload.
            js(c, 'document.querySelector("[data-community-filter=saved]").click()')
            reload()
            assert js(c, 'document.querySelectorAll(".comm-card").length===1 && document.querySelector("[data-community-filter=saved]").getAttribute("aria-pressed")==="true"')
            js(c, 'document.querySelector("[data-community-filter=all]").click()')

            cases = 0
            for lang in ('de', 'en'):
                for theme in ('light', 'dark', 'auto'):
                    c.cmd('Page.removeScriptToEvaluateOnNewDocument', identifier=preference)
                    preference = c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script(lang, theme))['identifier']
                    c.cmd('Emulation.setEmulatedMedia', features=[{'name': 'prefers-color-scheme', 'value': 'dark' if theme == 'auto' else 'light'}])
                    reload()
                    for width, height in ((1920, 1080), (1440, 1000), (768, 1024), (375, 844), (320, 740)):
                        shooter.emulate(width, height, mobile=width < 960)
                        for name in ('all', 'posts', 'stories', 'videos', 'saved'):
                            js(c, f'document.querySelector("[data-community-filter={name}]").click()')
                            assert not js(c, 'document.documentElement.scrollWidth>innerWidth'), (lang, theme, width, name, 'overflow')
                            small = js(c, '[...document.querySelectorAll(".comm-filters .btn,.comm-card-actions .btn,.comm-launch")].filter(b=>b.getBoundingClientRect().height<43.9).map(b=>({text:b.textContent,height:b.getBoundingClientRect().height}))')
                            assert not small, (lang, theme, width, name, 'small target', small)
                            assert js(c, '!document.querySelector("#page-community iframe,#page-community form")')
                            if name == 'all' and width in (1440, 375):
                                js(c, 'scrollTo(0,0);document.activeElement?.blur()')
                                c.pump(.1)
                                shooter.shot(f'{lang}-{theme}-{width}')
                            cases += 1
                    print(f'Passed layouts {lang}/{theme}')
            assert not posts, ('Unexpected POST', posts)
            assert not errors, errors
            report = {'layout_cases': cases, 'flows': flows, 'console_errors': errors, 'posts': posts}
            (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--port', type=int, default=9394)
    args = parser.parse_args()
    run(os.environ['VGROW_URL'], args.out, args.port)
