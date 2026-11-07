"""Exercise the browser's real command watcher with device reports and controlled deadlines."""
import shutil
import subprocess
from pathlib import Path

import pytest

from vgrow.hank import humidifier_report

WEB = Path(__file__).resolve().parents[1] / "web"


@pytest.mark.skipif(not shutil.which("node"), reason="Node not installed")
def test_card_distinguishes_acceptance_reported_confirmation_and_failure():
    program = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  const commandWatches = new Map();');
const end = source.indexOf('  function expired(p)', start);
assert(start > 0 && end > start);
let now = 1000, nextTimer = 1;
const timers = new Map();
const context = vm.createContext({
  H: {app: null}, Date: {now: () => now},
  say: (fb, key) => {fb.dataset.hk = key;fb.textContent = key;return fb;},
  setTimeout: (fn) => {const id=nextTimer++;timers.set(id, fn);return id;},
  clearTimeout: (id) => timers.delete(id),
  api: () => {throw Error('Watching a report must never send a command');}
});
vm.runInContext(source.slice(start,end), context);
const feedback = () => ({isConnected:true,dataset:{},className:'',textContent:''});
const snap = (fields={}) => ({online:true,mqtt:true,shadow_age:1,pending:{},...fields});
const begin = (body) => {const fb=feedback();context.watchCommand(body,fb);return fb;};
const settle = (state) => context.settleCommands(state,true);
const expire = () => {now+=21000;for(const fn of [...timers.values()])fn();};
const body = {action:'dfan',step:9};
let fb=begin(body);
assert.equal(fb.dataset.hk,'hank.proposal.sent');
settle(snap({dfan:{step:9},pending:{'dFan.level':{status:'pending',target:90}}}));
assert.equal(fb.dataset.hk,'hank.proposal.sent','HTTP success and pending state are not a confirmation');
settle(snap({dfan:{step:8},pending:{'dFan.level':{status:'ok',target:90}}}));
assert.equal(fb.dataset.hk,'hank.proposal.sent','a success for another target must not confirm this one');
settle(snap({dfan:{step:9},pending:{'dFan.level':{status:'ok',target:90}}}));
assert.equal(fb.dataset.hk,'hank.proposal.confirmed');
assert.equal(timers.size,0);
for(const [changes, key] of [
  [{pending:{'dFan.level':{status:'rejected'}}},'hank.proposal.rejected'],
  [{pending:{'dFan.level':{status:'timeout'}}},'hank.proposal.unconfirmed']]) {
  fb=begin(body);settle(snap({dfan:{step:8},...changes}));assert.equal(fb.dataset.hk,key);
}
for(const changes of [{online:false},{mqtt:false},{shadow_age:301},{shadow_age:null}]) {
  fb=begin(body);context.H.app=snap({dfan:{step:9},...changes});settle(context.H.app);
  assert.equal(fb.dataset.hk,'hank.proposal.sent');expire();
  assert.equal(fb.dataset.hk,'hank.proposal.unconfirmed','stale/offline reports never confirm');
}
fb=begin(body);context.H.app=snap({dfan:{step:9}});expire();
assert.equal(fb.dataset.hk,'hank.proposal.unconfirmed','a pre-POST snapshot cannot confirm at timeout');
for(const [command, report] of [
  [{action:'light',level:0},{light:{level:0}}],
  [{action:'cfan',step:7},{cfan:{step:7}}],
  [{action:'cfan',natural_wind:true},{cfan:{natural_wind:true}}],
  [{action:'port',port:'usb2',on:false},{ports:{usb2:{on:false}}}]
]) {
  fb=begin(command);settle(snap(report));assert.equal(fb.dataset.hk,'hank.proposal.confirmed');
}
fb=begin(body);fb.isConnected=false;settle(snap());assert.equal(timers.size,0);
fb=begin({action:'plant_stage',key:'early_flower'});assert.equal(fb.dataset.hk,'hank.proposal.saved');
fb=begin({action:'plant_apply'});assert.equal(fb.dataset.hk,'hank.proposal.accepted');
assert.equal(timers.size,0);
'''
    result = subprocess.run(["node", "-e", program, str(WEB / "hank.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_old_humidifier_fault_never_claims_the_device_is_still_powered_on():
    auto = {"enabled": True, "implausible": True, "tank": {"warning": "no_response", "response_minutes": 20}}
    de = humidifier_report.warning(auto, "de")
    en = humidifier_report.warning(auto, "en")
    assert "letzten Wirkungsprüfung" in de and "last response check" in en
    assert "eingeschaltet gemeldet" not in de and "reported powered on" not in en
    assert "20" in de and "20" in en
