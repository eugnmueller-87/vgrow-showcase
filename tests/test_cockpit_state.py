"""Presentation must retain last reported actuator values while clearly marking their age.

Exercise the real, pure formatter in Node. No browser, server or hardware commands.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("lang", ["de", "en"])
def test_old_device_state_keeps_reported_values_and_never_invents_an_unknown_value(lang: str) -> None:
    source = (WEB / "cockpit.js").read_text(encoding="utf-8")
    formatter = source[source.index("  function physicalValue("):source.index("  function renderAutomation(")]
    dictionary = (WEB / "i18n.js").read_text(encoding="utf-8")
    texts = json.loads(dictionary.removeprefix("window.I18N = ").strip().removesuffix(";"))[lang]
    harness = r"""
const texts = %s;
const t = (key, params = {}) => texts[key].replace(/\{(\w+)\}/g, (_, k) => params[k] ?? '');
const num = String, qty = (value, unit) => value + unit;
let serverDown = false;
%s
const fresh = {online:true, mqtt:true, shadow_age:2, ports:{usb2:{on:false}}};
const stale = {...fresh, shadow_age:901};
const device = {id:'light', value:100};
const value = physicalValue(device, fresh);
const output = {
 fresh:value,
 stale:physicalValue(device, stale),
 staleOff:physicalValue({id:'humidifier'}, stale),
 zero:physicalValue({id:'cfan',value:0}, stale),
 missing:physicalValue({id:'dfan'}, stale),
 disconnected:physicalValue(device, {...fresh, mqtt:false}),
};
serverDown = true;
output.serverDown = physicalValue(device, fresh);
process.stdout.write(JSON.stringify(output));
""" % (json.dumps(texts), formatter)
    result = subprocess.run([NODE or "node", "-"], input=harness, text=True, encoding="utf-8",
                            capture_output=True, check=True, timeout=15)
    out = json.loads(result.stdout)
    known = texts["cockpit.lastKnown"].replace("{value}", out["fresh"])
    assert out["fresh"] == "100%"
    assert out["stale"] == out["disconnected"] == out["serverDown"] == known
    assert out["staleOff"] == texts["cockpit.lastKnown"].replace("{value}", texts["ctl.off"])
    assert out["zero"] == texts["cockpit.lastKnown"].replace("{value}", "0")
    assert out["missing"] == "–"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("lang", ["de", "en"])
def test_hot_dry_advice_uses_cooling_priority_and_respects_reported_limits(lang: str) -> None:
    source = (WEB / "app.js").read_text(encoding="utf-8")
    todos = source[source.index("function plantTodos("):source.index("function renderPlant(")]
    holding = source[source.index("function exhaustHolding("):source.index("function apBox(")]
    texts = json.loads((WEB / "i18n.js").read_text(encoding="utf-8").removeprefix("window.I18N = ").strip().removesuffix(";"))[lang]
    harness = r"""
const texts = %s;
const t = (key, params = {}) => texts[key].replace(/\{(\w+)\}/g, (_, k) => params[k] ?? '');
%s
%s
const p = {checks:[
 {metric:'probe_temp',status:'high',advice:'INCREASE'},
 {metric:'probe_humidity',status:'low',advice:'DECREASE'},
 {metric:'probe_vpd',status:'high',advice:'VPD'},
 {metric:'water_level',status:'low',advice:'REFILL'}
]};
const fan={id:'dfan',level:'auto',value:8,control_state:'waiting',reason_key:'ap.hold.vpd_heat',status:'RECENT_HEAT'};
const s={autopilot:{boxes:[{mode:'auto',devices:[fan],limits:{dfan:[4,8]}}]}};
const bounded=plantTodos(p,s);
fan.value=7;
const belowLimit=plantTodos(p,s);
fan.level='manual';
const byHand=plantTodos(p,s);
fan.level='auto';
p.checks[0]={metric:'probe_temp',status:'ok',advice:null};
const recentlyHot=plantTodos(p,s);
fan.reason_key='ap.hold.on_target';
const cool=plantTodos(p,s);
const labels={holding:exhaustHolding({...fan,reason_key:'ap.hold.gap'}),
 warmup:exhaustHolding({...fan,reason_key:'ap.hold.warmup'}),
 offline:exhaustHolding({...fan,reason_key:'ap.hold.offline'}),
 off:exhaustHolding({...fan,value:0,reason_key:'ap.hold.gap'}),
 pending:exhaustHolding({...fan,control_state:'pending',reason_key:'ap.hold.gap'})};
process.stdout.write(JSON.stringify({bounded,belowLimit,byHand,recentlyHot,cool,labels}));
""" % (json.dumps(texts), todos, holding)
    result = subprocess.run([NODE or "node", "-"], input=harness, text=True, encoding="utf-8",
                            capture_output=True, check=True, timeout=15)
    out = json.loads(result.stdout)
    assert "INCREASE" not in out["bounded"] and "DECREASE" not in out["bounded"]
    assert any("8/10" in line for line in out["bounded"]) and "REFILL" in out["bounded"]
    # on auto the autopilot turns the exhaust up itself: no task for the owner (07.11.2026); by hand it is his
    assert "INCREASE" not in out["belowLimit"] and "DECREASE" not in out["belowLimit"] and "REFILL" in out["belowLimit"]
    assert "INCREASE" in out["byHand"] and "DECREASE" not in out["byHand"]
    assert "DECREASE" not in out["recentlyHot"] and any("RECENT_HEAT" in line for line in out["recentlyHot"])
    assert "DECREASE" in out["cool"]
    assert out["labels"] == {"holding": True, "warmup": False, "offline": False, "off": False, "pending": False}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("lang", ["de", "en"])
def test_on_auto_a_check_shows_the_reading_and_what_is_done_never_the_cards_order(lang: str) -> None:
    """Owner, 07.11.2026, on "Too warm: turn the exhaust fan up." while all three devices ran on Automatik: "it's on
    auto, he should do everything himself, not telling me to do it". The warning card keeps the reading and the line of
    what the autopilot, the humidifier rule or the light plan does; the plant card lists no task for it. By hand the
    card's advice is the owner's again."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    cockpit = (WEB / "cockpit.js").read_text(encoding="utf-8")
    parts = [app[app.index("function rangeText("):app.index("let stagePick")],
             app[app.index("function plantTodos("):app.index("function renderPlant(")],
             app[app.index("function exhaustHolding("):app.index("function apBox(")],
             cockpit[cockpit.index("  function issues("):cockpit.index("  function issueCard(")]]
    texts = json.loads((WEB / "i18n.js").read_text(encoding="utf-8").removeprefix("window.I18N = ").strip().removesuffix(";"))[lang]
    harness = r"""
const texts = %s;
const t = (key, params = {}) => texts[key].replace(/\{(\w+)\}/g, (_, k) => params[k] ?? '');
const fmt = (v, d) => Number(v).toFixed(d), unitSuffix = (u) => (u ? ' ' + u : ''), qty = (text, unit) => text + unitSuffix(unit);
const readingText = (v, unit) => fmt(v, unit === '%%' ? 0 : unit === 'kPa' ? 2 : 1), keepUnits = String;
const serverDown = false, ago = String, offlineText = String, triggerText = String, tankWarning = String;
%s
const fan = {id:'dfan', name:'FAN', level:'auto', value:8, control_state:'waiting', reason_key:'ap.hold.watch_high_up', status:'WATCHING'};
const lamp = {id:'light', name:'LIGHT', level:'auto', value:40, control_state:'waiting', reason_key:'ap.hold.light_schedule', status:'TIMER'};
const hum = {enabled:true, control_state:'ready', status:'HUMIDIFYING'};
const checks = [
 {metric:'probe_temp', label:'TEMP', status:'high', actual:28.1, unit:'°C', min:22, max:28, advice:'TURN_UP'},
 {metric:'probe_vpd', label:'VPD', status:'high', actual:1.28, unit:'kPa', min:0.8, max:1.2, advice:'TOO_DRY'},
 {metric:'light_percent', label:'LIGHT', status:'low', actual:40, unit:'%%', min:80, max:80, advice:'RAISE'},
 {metric:'water_level', label:'TANK', status:'low', actual:10, unit:'%%', min:20, max:null, advice:'REFILL'}];
const s = {online:true, mqtt:true, sensors_age:60, alerts:[], climate:{probe_temp:28.1, probe_humidity:66, probe_vpd:1.28},
  plant:{configured:false, checks}, grow_plan:{todo:[], light:{text:'PLAN_HOLDS_40'}},
  autopilot:{boxes:[{mode:'auto', devices:[fan, lamp], humidifier:hum, limits:{dfan:[7, 10]}}]}};
const cards = (list) => Object.fromEntries(list.filter((i) => i.kind === 'warn').map((i) => [i.title, [i.text, i.handling]]));
const auto = cards(issues(s)), autoTodos = plantTodos(s.plant, s);
for (const d of [fan, lamp]) { d.level = 'manual'; d.control_state = 'manual'; }
hum.enabled = false; hum.control_state = 'off';
const hand = cards(issues(s)), handTodos = plantTodos(s.plant, s);
const holding = ['ap.hold.watch_high_up', 'ap.hold.watch_low_down', 'ap.hold.watch_low_up', 'ap.hold.watch_high_down',
  'ap.hold.mist', 'ap.hold.undone'].map((key) => exhaustHolding({id:'dfan', control_state:'waiting', value:8, reason_key:key}));
process.stdout.write(JSON.stringify({auto, autoTodos, hand, handTodos, holding}));
""" % (json.dumps(texts), "\n".join(parts))
    result = subprocess.run([NODE or "node", "-"], input=harness, text=True, encoding="utf-8",
                            capture_output=True, check=True, timeout=15)
    out = json.loads(result.stdout)
    target = texts["check.targetRange"].replace("{min}", "22").replace("{max}", "28").replace("{unit}", " °C")
    assert out["auto"]["TEMP"] == [f"28.1 °C · {target}", "WATCHING"]
    assert out["auto"]["VPD"][1] == "HUMIDIFYING" and out["auto"]["VPD"][0].startswith("1.28 kPa · ")
    assert out["auto"]["LIGHT"][1] == "PLAN_HOLDS_40" and out["auto"]["LIGHT"][0].startswith("40 % · ")
    assert out["auto"]["TANK"] == ["REFILL", ""]  # no device works on it: the advice stays the owner's
    assert not {"TURN_UP", "TOO_DRY", "RAISE"} & set(out["autoTodos"]) and "REFILL" in out["autoTodos"]
    manual = texts["cockpit.manualOwnership"]
    assert out["hand"] == {"TEMP": ["TURN_UP", manual], "VPD": ["TOO_DRY", manual], "LIGHT": ["RAISE", manual], "TANK": ["REFILL", ""]}
    assert {"TURN_UP", "RAISE", "REFILL"} <= set(out["handTodos"])
    assert out["holding"] == [True] * 6  # the automation card: "Holding speed" and the reason under it
