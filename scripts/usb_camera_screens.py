"""Check simultaneous camera panels against a local demo. All recording commands are intercepted."""
import argparse
import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from cockpit_screens import js, wait_for
from screenshots import Cdp, EDGE, Shooter, prefs_script
from websockets.sync.client import connect


def check_zoom(c, shooter):
    """Only local display controls: no device or recording commands, usable against real feeds too."""
    shooter.emulate(1440, 1000, mobile=False)
    js(c, 'window.zoomURLs=[...cameraViews.values()].map(v=>v.$("cam-img").src)')
    assert js(c, 'cameraViews.get("growcam").$("camera-zoom").hidden && !cameraViews.get("usb").$("camera-zoom").hidden')
    js(c, 'cameraViews.get("usb").$("camera-zoom-in").click();cameraViews.get("usb").$("camera-zoom-in").click()')
    assert js(c, 'cameraViews.get("usb").zoom===2 && cameraViews.get("growcam").zoom===1')
    assert js(c, 'JSON.stringify(zoomURLs)===JSON.stringify([...cameraViews.values()].map(v=>v.$("cam-img").src))')
    js(c, 'cameraViews.get("usb").$("camera-frame").scrollIntoView({behavior:"instant",block:"center"})')
    c.pump(.2)  # Let the compositor update pointer hit testing after scrolling.
    box = js(c, '(()=>{const r=cameraViews.get("usb").$("camera-frame").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/3}})()')
    c.cmd('Input.dispatchMouseEvent', type='mousePressed', button='left', clickCount=1, **box)
    c.cmd('Input.dispatchMouseEvent', type='mouseMoved', buttons=1, x=box['x']+70, y=box['y']+30)
    c.cmd('Input.dispatchMouseEvent', type='mouseReleased', button='left', clickCount=1, x=box['x']+70, y=box['y']+30)
    assert js(c, 'cameraViews.get("usb").pan.x>0 && !cameraViews.get("usb").drag')
    js(c, 'renderCameras(state)')
    assert js(c, 'cameraViews.get("usb").zoom===2 && cameraViews.get("usb").pan.x>0')
    shooter.shot_of('hd-zoom-desktop', '#camera-views', width=1440)
    # Fullscreen includes a separate toolbar, keeping controls below the footage.
    c.cmd('Page.bringToFront')
    c.cmd('Runtime.evaluate', expression='void cameraViews.get("usb").$("camera-fullscreen").onclick()', userGesture=True)
    try:
        wait_for(c, 'document.fullscreenElement===cameraViews.get("usb").$("camera-player")')
    except AssertionError:
        raise AssertionError(js(c, '({fullscreen:document.fullscreenElement?.id,feedback:cameraViews.get("usb").$("fb-cam").textContent,player:cameraViews.get("usb").$("camera-player")?.id})')) from None
    wait_for(c, 'cameraViews.get("usb").$("camera-frame").clientWidth===innerWidth')
    js(c, 'cameraViews.get("usb").$("camera-zoom-in").click()')
    assert js(c, 'cameraViews.get("usb").zoom===2.5 && document.fullscreenElement.contains(cameraViews.get("usb").$("camera-zoom-in"))')
    assert js(c, 'cameraViews.get("usb").$("camera-zoom").getBoundingClientRect().top>=cameraViews.get("usb").$("camera-frame").getBoundingClientRect().bottom')
    assert js(c, '(()=>{const r=cameraViews.get("usb").$("camera-zoom").getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight})()')
    c.pump(.5)
    picture = c.cmd('Page.captureScreenshot', format='png', captureBeyondViewport=False)['data']
    (shooter.out / 'hd-zoom-fullscreen.png').write_bytes(base64.b64decode(picture))
    js(c, 'document.exitFullscreen()')
    wait_for(c, '!document.fullscreenElement')
    js(c, 'cameraViews.get("usb").$("camera-frame").focus()')
    c.cmd('Input.dispatchKeyEvent', type='keyDown', key='0', code='Digit0')
    c.cmd('Input.dispatchKeyEvent', type='keyUp', key='0', code='Digit0')
    assert js(c, 'cameraViews.get("usb").zoom===1 && cameraViews.get("usb").pan.x===0 && cameraViews.get("usb").$("camera-zoom-out").disabled')
    js(c, 'for(let i=0;i<10;i++)cameraViews.get("usb").$("camera-zoom-in").click()')
    assert js(c, 'cameraViews.get("usb").zoom===4 && cameraViews.get("usb").$("camera-zoom-in").disabled')
    js(c, 'cameraViews.get("usb").$("camera-zoom-reset").click();cameraViews.get("usb").$("camera-zoom-in").click()')
    shooter.emulate(375, 844, mobile=True)
    js(c, 'cameraViews.get("usb").$("camera-frame").scrollIntoView({behavior:"instant",block:"center"})')
    c.pump(.2)
    box = js(c, '(()=>{const r=cameraViews.get("usb").$("camera-frame").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/3}})()')
    c.cmd('Input.dispatchTouchEvent', type='touchStart', touchPoints=[{**box, 'id': 1}])
    c.cmd('Input.dispatchTouchEvent', type='touchMove', touchPoints=[{'x': box['x']+45, 'y': box['y']+10, 'id': 1}])
    c.cmd('Input.dispatchTouchEvent', type='touchEnd', touchPoints=[])
    assert js(c, 'cameraViews.get("usb").pan.x>0 && Math.abs(cameraViews.get("usb").pan.x)<=1 && !cameraViews.get("usb").drag')
    assert js(c, 'getComputedStyle(cameraViews.get("usb").$("camera-frame")).touchAction==="none"')
    assert js(c, '[...cameraViews.get("usb").$("camera-zoom").querySelectorAll("button")].every(b=>b.getBoundingClientRect().height>=44)')
    shooter.shot_of('hd-zoom-mobile', '[data-camera="usb"]', width=375)
    js(c, 'cameraViews.get("usb").$("camera-zoom-reset").click()')
    assert js(c, 'getComputedStyle(cameraViews.get("usb").$("camera-frame")).touchAction!=="none"')


def run(url, out):
    assert urlsplit(url).hostname in ('localhost', '127.0.0.1')
    out.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--remote-debugging-port=9394',
                               f'--user-data-dir={out.resolve()/"edge"}', 'about:blank'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors, posts = [], []
    try:
        for _ in range(60):
            try:
                page = next(p for p in json.load(urllib.request.urlopen('http://127.0.0.1:9394/json')) if p['type'] == 'page')
                break
            except (OSError, StopIteration):
                time.sleep(.2)
        with connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            c = Cdp(ws)
            c.handlers['Runtime.exceptionThrown'] = lambda e: errors.append(e['exceptionDetails'].get('exception', {}).get('description', e['exceptionDetails']['text']))
            c.handlers['Network.requestWillBeSent'] = lambda e: posts.append(e['request']['url']) if e['request']['method'] == 'POST' else None
            for domain in ('Page', 'Runtime', 'Network'):
                c.cmd(domain+'.enable')
            c.cmd('Page.addScriptToEvaluateOnNewDocument', source=prefs_script('de', 'light'))
            c.cmd('Page.navigate', url=url+'#overview')
            wait_for(c, 'typeof state!=="undefined" && !!state && state.demo && cameraViews.size===2')
            live = '[...cameraViews.values()].every(v=>v.live && v.$("cam-img").naturalWidth>0 && v.$("camera-loading").hidden)'
            wait_for(c, live)
            assert js(c, '[...cameraViews.values()].every(v=>v.$("cam-img").src.includes("camera="+v.id))')
            check_zoom(c, Shooter(c, out, 'camera-zoom'))
            js(c, 'window.otherURL=cameraViews.get("usb").$("cam-img").src;cameraViews.get("growcam").$("cam-still").click()')
            wait_for(c, 'cameraViews.get("growcam").$("cam-img").src.includes("snapshot.jpg") && cameraViews.get("growcam").$("cam-img").naturalWidth>0')
            assert js(c, 'cameraViews.get("usb").live && cameraViews.get("usb").$("cam-img").src===otherURL')
            js(c, 'cameraViews.get("growcam").$("cam-live").click()')
            wait_for(c, live)
            js(c, '''window.cameraQACommands=[];const qaFetch=fetch;window.fetch=(url,opts)=>{
              if(opts?.method==='POST') {const body=JSON.parse(opts.body);cameraQACommands.push(body);
                if(!['set_lang','record_start','record_stop','record_delete'].includes(body.action)) throw Error('Unexpected command');
                return Promise.resolve(new Response(JSON.stringify({ok:true,file:'2026-10-26_18-00-00.mp4',server_lang:body.lang})));}
              return qaFetch(url,opts);};''')
            for ident in ('growcam', 'usb'):
                ref = f'cameraViews.get({json.dumps(ident)})'
                js(c, ref+'.$("cam-rec").click()')
                wait_for(c, '!'+ref+'.recordingBusy')
                js(c, f'renderCameras({{...state,cameras:state.cameras.map(cam=>cam.id==={json.dumps(ident)}?{{...cam,recording:{{seconds:2}}}}:cam)}});'+ref+'.$("cam-rec").click()')
                wait_for(c, '!'+ref+'.recordingBusy')
            recorded = js(c, 'cameraQACommands.filter(b=>b.action.startsWith("record_"))')
            assert [(b['action'], b['camera']) for b in recorded] == [('record_start', 'growcam'), ('record_stop', 'growcam'), ('record_start', 'usb'), ('record_stop', 'usb')]
            js(c, '''window.fullscreenTargets=[];for(const v of cameraViews.values()) {
              v.$('camera-player').requestFullscreen=async()=>fullscreenTargets.push(v.id);v.$('camera-fullscreen').click();}''')
            assert js(c, 'fullscreenTargets') == ['growcam', 'usb']
            shooter = Shooter(c, out, 'camera-pair')
            cases = 0
            for lang in ('de', 'en'):
                for theme in ('light', 'dark'):
                    js(c, f'setLang({json.dumps(lang)});applyTheme({json.dumps(theme)})')
                    for width, height in ((1440, 1000), (1024, 900), (768, 1024), (375, 844), (320, 740)):
                        shooter.emulate(width, height, mobile=width<960)
                        for route in ('overview', 'devices/camera'):
                            js(c, f'location.hash={json.dumps(route)}')
                            c.pump(.15)
                            wait_for(c, live)
                            assert not js(c, 'document.documentElement.scrollWidth>innerWidth'), (lang, theme, width, route)
                            assert js(c, 'new Set([...document.querySelectorAll("[id]")].map(el=>el.id)).size===document.querySelectorAll("[id]").length')
                            assert js(c, '[...cameraViews.values()].every(v=>v.$("camera-fullscreen").textContent===t("cockpit.fullscreen") && v.$("cam-still").textContent===t("cam.modeStill"))')
                            assert js(c, 'cameraViews.get("usb").$("camera-zoom").getBoundingClientRect().top>=cameraViews.get("usb").$("camera-frame").getBoundingClientRect().bottom')
                            layout = js(c, '''(()=>{const [a,b]=[...document.querySelectorAll('#camera-views > [data-camera]')].map(el=>el.getBoundingClientRect());
                              return {aligned:Math.abs(a.top-b.top)<2,stacked:b.top>=a.bottom,width:a.width,otherWidth:b.width};})()''')
                            assert layout['aligned'] if width>=768 else layout['stacked'], (width, route, layout)
                            assert abs(layout['width']-layout['otherWidth'])<2
                            if route=='overview' and width>=768:
                                assert js(c, 'document.querySelector(".action-center").getBoundingClientRect().top>=document.querySelector("#camera-views").getBoundingClientRect().bottom-2')
                            if width in (1440, 375) and route=='overview':
                                shooter.shot_of(f'{lang}-{theme}-{width}', '#camera-views', width=width)
                            cases += 1
            js(c, 'location.hash="settings"')
            wait_for(c, '[...cameraViews.values()].every(v=>!v.live && !v.$("cam-img").hasAttribute("src"))')
            js(c, 'location.hash="overview"')
            wait_for(c, live)
            js(c, 'window.healthyURL=cameraViews.get("growcam").$("cam-img").src;renderCameras({...state,cameras:state.cameras.map(cam=>cam.id==="usb"?{...cam,reachable:false}:cam)})')
            assert js(c, '!cameraViews.get("usb").live && cameraViews.get("growcam").live && cameraViews.get("growcam").$("cam-img").src===healthyURL')
            js(c, 'renderCameras(state)')
            wait_for(c, live)
            js(c, 'renderCameras({camera:state.camera})')
            assert js(c, 'cameraViews.size===1 && !document.querySelector("#camera-views").classList.contains("multiple")')
            js(c, 'renderCameras({camera:null})')
            assert js(c, 'cameraViews.size===0')
            js(c, 'renderCameras(state)')
            wait_for(c, 'cameraViews.size===2 && '+live)
            assert not errors, errors
            assert not posts, posts
            print(json.dumps({'layouts': cases, 'simultaneous_live': True, 'independent_controls': True,
                              'hidden_stream_cleanup': True, 'offline_isolation': True, 'single_camera': True,
                              'hd_zoom': '1x-4x, mouse/touch pan, reset, keyboard, fullscreen',
                              'console_errors': errors, 'posts': posts}))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    run(os.environ['VGROW_URL'], parser.parse_args().out)
