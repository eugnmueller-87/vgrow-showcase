"""Hank's garden, the idle screen (web/garden-logic.js, garden-art.js, garden.js): what he does comes from the box.

- decide() runs in node on small states: every trigger the owner named, and the order between them.
- The whole scene runs in node on a canvas that draws nothing: through every job for simulated minutes, so a job that leads
  nowhere, a loop that hangs or a pose that throws shows here. Whether it looks right is judged on rendered pictures.
- Beer and cigarette are for the owner's own box, the demo never shows them. This public version has no joint (owner, 07.11.2026).
- The garden only looks: it reads two answers and sends no command, also when the owner plays with it.
- It stays until its button or Esc closes it (owner, 06.11.2026: "if I move the mouse, it disappears"), and nothing keeps Hank stuck on one spot.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import test_web_i18n as web

WEB = Path(__file__).resolve().parent.parent / "web"
NODE = shutil.which("node")
FILES = ("garden-logic.js", "garden-art.js", "garden.js")
needs_node = pytest.mark.skipif(not NODE, reason="node is not installed")

STUBS = r"""
let seed = 20261106;
Math.random = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };  // the same day every run
const noop = () => {};
const ctx = new Proxy({}, { get(target, name) {
  if (name === "getImageData") return (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) });
  if (name in target) return target[name];
  return noop;
}, set(target, name, value) { target[name] = value; return true; } });
const created = [];
const fakeCanvas = () => {
  const el = { width: 300, height: 150, style: {}, on: {}, attrs: {}, getContext: () => ctx, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(name, fn) { this.on[name] = fn; }, append: noop, replaceChildren: noop,
    getBoundingClientRect() { return this.className === "garden-bar" ? { left: 400, top: 6, bottom: 46, width: 394, height: 40 } : { left: 0, top: 0, bottom: 600, width: 800, height: 600 }; }, focus: noop, blur: noop };
  created.push(el);
  return el;
};
const docOn = {};
const fire = (name, event) => { for (const fn of docOn[name] || []) fn({ preventDefault: noop, stopPropagation: noop, ...event }); };
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
global.window = { addEventListener: noop, matchMedia: () => ({ matches: false }), innerWidth: 800, innerHeight: 600, devicePixelRatio: 1 };
global.document = { createElement: fakeCanvas, addEventListener: (name, fn) => { (docOn[name] = docOn[name] || []).push(fn); }, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], body: { append: noop }, hidden: false, activeElement: null };
global.setInterval = () => 0;
global.requestAnimationFrame = () => 1;
global.cancelAnimationFrame = noop;
global.t = (key, params) => key + (params ? JSON.stringify(params) : "");
global.fmt = (value) => String(value);
global.when = () => "12:00";
global.api = async () => ({});
"""


def run(body: str) -> Any:
    files = ",".join(json.dumps(str(WEB / name)) for name in FILES)
    script = f"{STUBS}\nfor (const f of [{files}]) require(f);\nconst G = window.GardenLogic, H = window.HankGarden;\n{body}\n"
    out = subprocess.run([NODE or "node", "-"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


GOOD: dict[str, Any] = {
    "online": True, "mqtt": True, "sensors_age": 60, "error": None, "light": {"level": 60}, "cfan": {"step": 5}, "dfan": {"step": 5},
    "climate": {"probe_temp": 24.0, "probe_humidity": 60.0, "probe_vpd": 1.1, "water_state": "wet"},
    "ports": {"usb1": {"on": False}, "usb2": {"on": True}},
    "thirst": {"decision": {"kind": "WAIT", "reason": "not_due"}, "pump": {"port": "usb1", "flow": {"calibrated": True}}},
    "watering": {"active": None}, "automation": {"port": "usb2", "tank": {"warning": None}},
    "soil": {"fresh": True, "percent": 31.0, "relative": None, "state": None}, "alerts": [],
    "plant": {"checks": [{"metric": "probe_temp", "status": "ok"}, {"metric": "probe_humidity", "status": "ok"}, {"metric": "probe_vpd", "status": "ok"},
                         {"metric": "light_percent", "status": "low"}], "height": {"cm": 15}},
    "grow_plan": {"todo": [], "stage": {"key": "veg"}},
    "autopilot": {"boxes": [{"plant": "Purple Punch", "height_cm": 15.1}]},
}


def state(**changes: Any) -> dict[str, Any]:
    s = json.loads(json.dumps(GOOD))
    for path, value in changes.items():
        cur = s
        parts = path.split("__")
        for part in parts[:-1]:
            cur = cur[part]
        cur[parts[-1]] = value
    return s


def decide(s: dict[str, Any], hank: Any = None) -> dict[str, Any]:
    return run(f"console.log(JSON.stringify(G.decide({json.dumps(s)}, {json.dumps(hank)})));")


def checks(**status: str) -> list[dict[str, str]]:
    base = {"probe_temp": "ok", "probe_humidity": "ok", "probe_vpd": "ok", "light_percent": "ok", **status}
    return [{"metric": k, "status": v} for k, v in base.items()]


@needs_node
def test_nothing_to_do_means_free_time() -> None:
    found = decide(state())
    assert found["job"] == "chill" and not found["urgent"]


@needs_node
def test_a_light_held_below_the_card_is_not_a_job() -> None:
    assert decide(state(plant__checks=checks(light_percent="low")))["job"] == "chill"


@needs_node
def test_an_empty_tank_makes_him_say_the_pump_cannot_pump() -> None:
    found = decide(state(climate__water_state="dry"))
    assert found["job"] == "tank" and found["say"] == "garden.say.tank" and found["urgent"]


@needs_node
def test_the_pump_makes_him_water() -> None:
    found = decide(state(ports__usb1__on=True))
    assert found["job"] == "water" and found["say"] == "garden.say.pumpSoil" and found["params"]["soil"] == 31
    assert decide(state(ports__usb1__on=True, soil__fresh=False))["say"] == "garden.say.pump"


@needs_node
def test_a_pump_on_a_dry_tank_is_the_tank_first() -> None:
    found = decide(state(ports__usb1__on=True, climate__water_state="dry"))
    assert found["job"] == "tank" and found["say"] == "garden.say.dryRun"


@needs_node
def test_the_pump_port_comes_from_the_server() -> None:
    s = state(ports__usb2__on=True)
    s["thirst"]["pump"]["port"] = "usb2"
    assert decide(s)["job"] == "water"


@needs_node
def test_a_box_that_does_not_answer() -> None:
    assert decide(state(online=False))["job"] == "lost"
    assert decide(state(sensors_age=901))["job"] == "lost"
    assert decide(state(sensors_age=900))["job"] == "chill"
    assert decide(state(online=False, demo=True))["job"] == "chill"


@needs_node
@pytest.mark.parametrize(("decision", "job", "say"), [
    ({"kind": "PROPOSE", "reason": "hand", "dose": {"litres": 0.7}}, "thirsty", "garden.say.dose"),
    ({"kind": "ALERT", "reason": "ti_high"}, "thirsty", "garden.say.thirsty"),
    ({"kind": "BLOCKED", "reason": "blocked"}, "thirsty", "garden.say.thirsty"),
    ({"kind": "ALERT", "reason": "wet_72h"}, "soggy", "garden.say.soggy"),
    ({"kind": "ASK", "reason": "check", "check": {"how": "finger", "depth_cm": 5}}, "poke", "garden.say.checkDepth"),
    ({"kind": "WAIT", "reason": "hold"}, "chill", None),
])
def test_the_watering_decision(decision: dict[str, Any], job: str, say: str | None) -> None:
    found = decide(state(thirst__decision=decision))
    assert found["job"] == job and found["say"] == say


@needs_node
def test_thirsty_with_an_unmeasured_pump_says_why_it_cannot_pump() -> None:
    found = decide(state(thirst__decision={"kind": "ALERT", "reason": "ti_high"}, thirst__pump__flow__calibrated=False))
    assert found["job"] == "thirsty" and found["say"] == "garden.say.thirstyNoPump"


@needs_node
def test_a_calibrated_soil_reading_below_target_is_thirst() -> None:
    assert decide(state(soil__state="low"))["job"] == "thirsty"
    assert decide(state(soil__state="low", soil__fresh=False))["job"] == "chill"


@needs_node
@pytest.mark.parametrize(("status", "job"), [
    ({"probe_temp": "high"}, "hot"), ({"probe_temp": "low"}, "cold"), ({"probe_vpd": "high"}, "dry"), ({"probe_humidity": "low"}, "dry"),
    ({"probe_humidity": "high"}, "humid"), ({"probe_vpd": "low"}, "humid"),
])
def test_the_climate_off_target_is_soft_work(status: dict[str, str], job: str) -> None:
    found = decide(state(plant__checks=checks(**status)))
    assert found["job"] == job and found["soft"] and not found["urgent"]


@needs_node
def test_the_vpd_in_the_dark_is_no_job() -> None:
    """Seen 06.11.2026: 30 minutes after lights off the probe read VPD 0.81 and falling, with temperature and humidity on target."""
    for status in ("low", "high"):
        assert decide(state(light__level=0, plant__checks=checks(probe_vpd=status)))["job"] == "chill"
    assert decide(state(light__level=0, plant__checks=checks(probe_humidity="high")))["job"] == "humid"


@needs_node
def test_an_alarm_about_the_air_is_urgent() -> None:
    hot = decide(state(alerts=[{"name": "temp_high", "active": True}]))
    assert hot["job"] == "hot" and hot["urgent"]
    assert decide(state(alerts=[{"name": "temp_high", "active": False}]))["job"] == "chill"
    assert decide(state(alerts=[{"name": "tank_empty", "active": True}]))["job"] == "tank"


@needs_node
@pytest.mark.parametrize("name", ["usb_camera_offline", "camera_offline", "dark_period", "short_day", "something_new"])
def test_an_alarm_nobody_can_act_on_now_is_a_note_on_the_board_not_a_job(name: str) -> None:
    """Seen 06.11.2026: "close camera offline" kept him at the board, and the adapter is in the post."""
    s = state(alerts=[{"name": name, "label": "Label", "active": True}])
    assert decide(s)["job"] == "chill"
    assert run(f"console.log(JSON.stringify(G.notices({json.dumps(s)})));") == ["Label"]


@needs_node
def test_the_tank_and_the_air_are_not_notes() -> None:
    s = state(alerts=[{"name": n, "active": True} for n in ("temp_high", "temp_low", "humidity_high", "water_low", "tank_empty")])
    assert run(f"console.log(JSON.stringify(G.notices({json.dumps(s)})));") == []


@needs_node
def test_the_humidifier_running_out() -> None:
    assert decide(state(automation__tank__warning="low"))["job"] == "mist"


@needs_node
def test_free_time_fits_the_box_and_the_lamp() -> None:
    pools = run("""
      console.log(JSON.stringify({
        day: G.chillPool({}, true), night: G.chillPool({}, false), demoDay: G.chillPool({ demo: true }, true), demoNight: G.chillPool({ demo: true }, false),
      }));""")
    assert {"beer", "cig", "game", "juice"} <= set(pools["day"]) and "joint" not in pools["day"]
    assert {"nap", "game"} <= set(pools["night"]) and "joint" not in pools["night"] and "nap" not in pools["day"]
    for pool in (pools["demoDay"], pools["demoNight"]):
        assert not {"beer", "joint", "cig"} & set(pool)


SCENE = """
function play(s, hank, seconds, watch) {
  const scene = new H.Scene(fakeCanvas(), {});
  scene.resize(384, 216);
  scene.feed(s, hank);
  const seen = { poses: new Set(), props: new Set(), jobs: new Set(), said: new Set(), spots: [] };
  for (let i = 0; i < seconds * 15; i++) {
    scene.step(1 / 15);
    if (i % 3 === 0) scene.draw();
    seen.poses.add(scene.a.pose);
    if (scene.a.prop) seen.props.add(scene.a.prop);
    if (scene.saying) seen.said.add(scene.saying.text);
    if (scene.doing) seen.jobs.add(scene.doing);
    if (watch) watch(scene);
  }
  return { poses: [...seen.poses], props: [...seen.props], doing: [...seen.jobs], said: [...seen.said], gx: scene.a.gx, gy: scene.a.gy };
}
"""


def scene(s: dict[str, Any], seconds: int, hank: Any = None) -> dict[str, Any]:
    return run(f"{SCENE}\nconsole.log(JSON.stringify(play({json.dumps(s)}, {json.dumps(hank)}, {seconds})));")


@needs_node
def test_the_pump_sends_him_to_the_barrel_and_then_pours_on_the_plants() -> None:
    seen = scene(state(ports__usb1__on=True), 40)
    assert {"fill", "pour", "walk"} <= set(seen["poses"]) and "can" in seen["props"]
    assert seen["doing"] == ["garden.do.water"]
    assert any("garden.say.pumpSoil" in text for text in seen["said"])


@needs_node
def test_an_empty_tank_keeps_him_at_the_barrel_and_the_bubble_stays() -> None:
    result = run(f"""{SCENE}
      let always = true, ticks = 0;
      const out = play({json.dumps(state(climate__water_state="dry"))}, null, 60, (scene) => {{ ticks++; if (ticks > 15 && !scene.saying) always = false; }});
      out.always = always;
      console.log(JSON.stringify(out));""")
    assert result["always"] and {"lookin", "scratch"} <= set(result["poses"])


@needs_node
def test_free_time_runs_for_minutes_and_uses_the_fridge_the_chair_and_the_pc() -> None:
    seen = scene(state(), 1800)
    assert {"reach", "sip", "hold", "type"} <= set(seen["poses"])
    assert "beer" in seen["props"] and "joint" not in seen["props"]
    assert "garden.do.game" in seen["doing"]


@needs_node
def test_the_demo_never_smokes_or_drinks_beer() -> None:
    seen = scene(state(demo=True), 900)
    assert not {"beer", "joint", "cig"} & set(seen["props"])
    assert not {"garden.do.beer", "garden.do.joint", "garden.do.cig"} & set(seen["doing"])
    assert "juice" in seen["props"]


@needs_node
def test_lights_out_is_night_and_he_can_nap() -> None:
    result = run(f"""{SCENE}
      let darkest = 1;
      const out = play({json.dumps(state(light__level=0))}, null, 900, (scene) => {{ darkest = Math.min(darkest, scene.d); }});
      out.darkest = darkest;
      console.log(JSON.stringify(out));""")
    assert result["darkest"] == 0 and "nap" in result["poses"]


@needs_node
@pytest.mark.parametrize("change", [
    {"plant__checks": checks(probe_temp="high")}, {"plant__checks": checks(probe_temp="low")}, {"plant__checks": checks(probe_vpd="high")},
    {"plant__checks": checks(probe_humidity="high")}, {"thirst__decision": {"kind": "ALERT", "reason": "ti_high"}},
    {"thirst__decision": {"kind": "ALERT", "reason": "wet_72h"}}, {"thirst__decision": {"kind": "ASK", "reason": "check", "check": {"how": "finger", "depth_cm": 5}}},
    {"online": False}, {"alerts": [{"name": "camera_offline", "label": "Camera", "active": True}]}, {"automation__tank__warning": "low"},
    {"grow_plan__todo": [{"id": "probe", "title": "Hang the probe", "done": False}]},
])
def test_every_job_keeps_going_without_an_error(change: dict[str, Any]) -> None:
    seen = scene(state(**change), 300)
    assert seen["doing"], seen


@needs_node
def test_a_soft_job_takes_breaks_and_never_says_all_is_on_target_meanwhile() -> None:
    seen = scene(state(plant__checks=checks(probe_vpd="high")), 900)
    assert "spray" in seen["poses"] and "garden.do.dry" in seen["doing"] and len(seen["doing"]) > 1
    assert not any(key in text for text in seen["said"] for key in ("garden.say.beer", "garden.say.juice", "garden.say.joint"))
    assert any("garden.say.break" in text for text in seen["said"])


@needs_node
def test_a_todo_shows_up_between_the_free_time() -> None:
    seen = scene(state(grow_plan__todo=[{"id": "probe", "title": "Hang the probe", "done": False}]), 600)
    assert any("garden.say.todo" in text and "Hang the probe" in text for text in seen["said"]) and "point" in seen["poses"]


@needs_node
def test_a_value_on_the_edge_does_not_make_him_run_back_and_forth() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      const good = {json.dumps(state())}, off = {json.dumps(state(plant__checks=checks(probe_vpd="high")))};
      scene.feed(good, null);
      let switched = 0, job = scene.decision.job;
      for (let i = 0; i < 300; i++) {{
        scene.feed(i % 2 ? good : off);
        for (let k = 0; k < 75; k++) scene.step(1 / 15);
        if (scene.decision.job !== job) {{ switched++; job = scene.decision.job; }}
      }}
      console.log(JSON.stringify({{ switched }}));""")
    assert result["switched"] == 0


@needs_node
def test_pump_on_interrupts_at_once_and_pump_off_ends_it() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state())}, null);
      for (let i = 0; i < 150; i++) scene.step(1 / 15);
      scene.feed({json.dumps(state(ports__usb1__on=True))});
      const during = scene.decision.job;
      for (let i = 0; i < 150; i++) scene.step(1 / 15);
      scene.feed({json.dumps(state())});
      console.log(JSON.stringify({{ during, after: scene.decision.job }}));""")
    assert result == {"during": "water", "after": "chill"}


@needs_node
def test_a_camera_alarm_leaves_him_his_free_time_and_he_mentions_it_at_the_board() -> None:
    seen = scene(state(alerts=[{"name": "usb_camera_offline", "label": "Close camera offline", "active": True}]), 900)
    assert "garden.do.todo" in seen["doing"] and len(seen["doing"]) >= 3, seen["doing"]  # the board once in a while, and his day around it
    assert any("garden.say.notice" in text and "Close camera offline" in text for text in seen["said"])


@needs_node
def test_a_camera_alarm_does_not_fill_the_line_under_his_job() -> None:
    advice = "VPD too high: the air is too dry or too warm."
    rows = checks(probe_vpd="high")
    rows[2]["advice"] = advice
    dry = state(plant__checks=rows, alerts=[{"name": "usb_camera_offline", "label": "Close-up camera offline", "active": True}])
    light = lambda text, *fix: {"light": {"level": "red", "text": text, "fix": list(fix)}}
    cases = {"camera": light("Act now: Close-up camera offline.", "alert:usb_camera_offline"),
             "both": light("Act now: Close-up camera offline, Too hot.", "alert:temp_high", "alert:usb_camera_offline", "probe_temp:high"),
             "air": light("Act now: Too hot.", "alert:temp_high", "probe_temp:high"),
             "check": light("VPD is off.", "probe_vpd:high")}
    result = run(f"""{SCENE}
      const out = {{}}, cases = {json.dumps(cases)};
      for (const name of Object.keys(cases)) {{
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed({json.dumps(dry)}, cases[name]);
        out[name] = [scene.decision.job, scene.detail()];
      }}
      console.log(JSON.stringify(out));""")
    assert result == {"camera": ["dry", advice], "both": ["dry", advice], "air": ["dry", "Act now: Too hot."], "check": ["dry", "VPD is off."]}


def piloted(mode: str = "auto", log: list[dict[str, Any]] | None = None, **fan: Any) -> dict[str, Any]:
    device = {"id": "dfan", "name": "Exhaust fan", "level": "auto", "value": 8, "app_mode": None, "paused_until": None,
              "status": WATCH, **fan}
    return {"boxes": [{"plant": "Purple Punch", "height_cm": 15.1, "mode": mode, "devices": [device], "log": log or []}]}


WATCH = "watching: the temperature is 28.1 °C, above 28.0 °C. One step up once it stays above 28.5 °C for 10 min"


@needs_node
def test_on_auto_the_line_under_his_job_says_what_the_autopilot_does_never_the_cards_order() -> None:
    """Owner, 07.11.2026, on "Too warm: turn the exhaust fan up." under "Fanning himself" while the exhaust ran on
    Automatik: "it's on auto, he should do everything himself, not telling me to do it"."""
    order = "Too warm: turn the exhaust fan up."
    rows = checks(probe_temp="high")
    rows[0]["advice"] = order
    step = "Autopilot: Exhaust fan 8 to 9, because the probe temperature is 28.6 °C, above 28.0 °C."
    stepped = lambda ago: piloted(log=[{"kind": "event", "at": "AGO", "ago": 60, "text": "mist"},
                                       {"kind": "step", "device": "dfan", "ago": ago, "text": step}])
    cases = {"auto": piloted(), "stepped": stepped(600), "old step": stepped(3600), "observe": piloted("observe"),
             "manual": piloted(level="manual"), "paused": piloted(paused_until=1_794_040_000), "app": piloted(app_mode="auto")}
    camera = {"light": {"level": "red", "text": "Act now: Close-up camera offline.", "fix": ["alert:usb_camera_offline"]}}
    result = run(f"""{SCENE}
      const out = {{}}, cases = {json.dumps(cases)}, base = {json.dumps(state(plant__checks=rows))};
      for (const name of Object.keys(cases)) {{
        for (const e of cases[name].boxes[0].log) e.at = Date.now() / 1000 - e.ago;
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed({{ ...base, autopilot: cases[name] }}, {json.dumps(camera)});
        out[name] = [scene.decision.job, scene.detail()];
      }}
      console.log(JSON.stringify(out));""")
    line = 'garden.auto.device{"device":"Exhaust fan","status":"' + WATCH + '"}'
    assert result == {"auto": ["hot", line], "stepped": ["hot", step], "old step": ["hot", line], "observe": ["hot", order],
                      "manual": ["hot", order], "paused": ["hot", order], "app": ["hot", order]}


@needs_node
def test_a_long_line_under_his_job_is_never_cut_the_third_row_takes_the_hints_place() -> None:
    """The autopilot's status runs to three rows below Full HD, and "holding: VPD ... above its band, but less exhaust
    only once ..." at every width (07.11.2026). The wide window has two rows and the hint: a third row takes the hint's
    place, so the sentence is never cut off. Short lines keep the hint."""
    long = ("Exhaust fan: holding: VPD 1.28 kPa above its band, but less exhaust only once the probe shows at most "
            "27.0 °C (otherwise it gets too warm).")
    out = run(f"""{SCENE}
      const out = {{}}, art = window.GardenArt;
      for (const [name, text, w, h] of [["long", {json.dumps(long)}, 384, 216], ["narrow", {json.dumps(long)}, 340, 216],
                                        ["short", "Too warm.", 384, 216], ["phone", {json.dumps(long)}, 292, 380],
                                        ["phone short", "Too warm.", 292, 380]]) {{
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(w, h);
        scene.feed({json.dumps(state())}, null);
        scene.detail = () => text;
        const drawn = [], real = art.text;
        art.text = (c, s, x, y, col) => {{ drawn.push(s); return real(c, s, x, y, col); }};
        scene.drawDialog(ctx);
        art.text = real;
        const box = scene.dialog;
        out[name] = {{ drawn, rows: art.wrap(text, box.w - 56).length, room: box.h > 54 ? 3 : 2, end: text.split(" ").pop() }};
      }}
      console.log(JSON.stringify(out));""")
    assert out["long"]["rows"] == 3 and out["narrow"]["rows"] == 3  # Full HD and below: three rows, the window has two
    for name, case in out.items():
        assert any(s.endswith(case["end"]) for s in case["drawn"]), (name, case)  # the end of the sentence is drawn
        assert ("garden.back" in case["drawn"]) is (case["rows"] <= case["room"]), (name, case)


@needs_node
def test_on_auto_against_dry_air_the_running_humidifier_comes_first() -> None:
    rows = checks(probe_vpd="high")
    rows[2]["advice"] = "VPD too high: the air is too dry or too warm."
    mist = "holding: mist at the probe, humidity and VPD do not count, only the temperature"
    found = run(f"""{SCENE}
      const out = {{}};
      for (const on of [true, false]) {{
        const s = {json.dumps(state(plant__checks=rows, autopilot=piloted(status=mist)))};
        s.ports.usb2.on = on;
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed(s, null);
        out[on ? "on" : "off"] = [scene.decision.job, scene.detail()];
      }}
      console.log(JSON.stringify(out));""")
    fan = 'garden.auto.device{"device":"Exhaust fan","status":"' + mist + '"}'
    assert found == {"on": ["dry", "garden.auto.humidifier " + fan], "off": ["dry", fan]}


@needs_node
def test_an_alarm_that_stays_gets_breaks_and_his_bubble_keeps_saying_it() -> None:
    seen = scene(state(alerts=[{"name": "temp_high", "active": True}]), 1200)
    assert "garden.do.hot" in seen["doing"] and len(seen["doing"]) > 1
    assert any("garden.say.hot" in text for text in seen["said"])
    assert not any("garden.say.beer" in text or "garden.say.joint" in text for text in seen["said"])  # never "all on target" meanwhile


@needs_node
def test_as_long_as_the_pump_runs_he_waters() -> None:
    assert scene(state(ports__usb1__on=True), 1200)["doing"] == ["garden.do.water"]


# ---------- the owner plays ----------

PLAY = SCENE + """
const A = window.GardenArt;
function fresh(s) { const scene = new H.Scene(fakeCanvas(), {}); scene.resize(384, 216); scene.feed(s, null); scene.a.gx = 1; scene.a.gy = 8; return scene; }  // away from everything: his own box counts first
function spot(scene, gx, gy) { return [scene.ox + A.X(gx, gy), scene.oy + A.Y(gx, gy)]; }
function middle(scene, row) { const b = scene.boxOf(row); return [scene.ox + b[0] + b[2] / 2, scene.oy + b[1] + b[3] / 2]; }
function play2(scene, seconds, seen) {
  for (let i = 0; i < seconds * 15; i++) {
    scene.step(1 / 15);
    if (i % 5 === 0) scene.draw();
    if (seen) { seen.poses.add(scene.a.pose); if (scene.a.prop) seen.props.add(scene.a.prop); if (scene.saying) seen.said.add(scene.saying.text); }
  }
}
const log = () => ({ poses: new Set(), props: new Set(), said: new Set() });
const plain = (seen) => ({ poses: [...seen.poses], props: [...seen.props], said: [...seen.said] });
"""


def play(body: str, s: dict[str, Any] | None = None) -> Any:
    return run(f"{PLAY}\nconst S = {json.dumps(s or state())};\n{body}")


@needs_node
def test_a_click_on_the_grass_walks_him_there_and_after_a_while_he_goes_back_to_his_day() -> None:
    result = play("""
      const scene = fresh(S);
      play2(scene, 5);
      const hit = scene.click(...spot(scene, 7, 6.5));
      const told = scene.doing;
      play2(scene, 22);
      const there = [scene.a.gx, scene.a.gy], still = scene.order !== null;
      play2(scene, 60);
      console.log(JSON.stringify({ hit, told, there, still, after: scene.order }));""")
    assert result["hit"] == "ground" and result["told"] == "garden.do.walk" and result["still"]
    assert result["there"] == pytest.approx([7, 6.5], abs=0.05)
    assert result["after"] is None


@needs_node
def test_every_thing_can_be_clicked_and_sends_him_somewhere() -> None:
    result = play("""
      const out = {};
      for (const row of H.THINGS) {
        const scene = fresh(S);
        const hit = scene.click(...middle(scene, row));
        const seen = log();
        play2(scene, 45, seen);
        out[row.name] = { hit, said: plain(seen).said };
      }
      console.log(JSON.stringify(out));""")
    assert set(result) == {"hank", "board", "table", "chair", "lantern", "thermo", "barrel", "pc", "fridge", "plants", "fountain", "windmill", "tree"}
    for name, row in result.items():
        assert row["hit"] == name, (name, row)
        assert row["said"], name


@needs_node
def test_what_he_says_about_a_thing_is_the_real_value() -> None:
    s = state(light_schedule={"settings": {"on_time": "20:00", "off_time": "16:00"}}, dfan__step=7, cfan__step=3)
    result = play("""
      const said = (name) => { const scene = fresh(S); const row = H.THINGS.find((r) => r.name === name); scene.click(...middle(scene, row)); const seen = log(); play2(scene, 30, seen); return plain(seen).said.join("|"); };
      console.log(JSON.stringify({ thermo: said("thermo"), mill: said("windmill"), lamp: said("lantern"), plants: said("plants") }));""", s)
    assert "24" in result["thermo"] and "60" in result["thermo"] and "1.1" in result["thermo"]
    assert '"dfan":"7"' in result["mill"] and '"cfan":"3"' in result["mill"]
    assert '"level":"60"' in result["lamp"] and '"off":"16:00"' in result["lamp"]
    assert "Purple Punch" in result["plants"] and "15.1" in result["plants"] and '"soil":"31"' in result["plants"]


@needs_node
def test_the_sun_tells_the_lamp() -> None:
    result = play("""
      const scene = fresh(S);
      scene.step(1 / 15); scene.draw();
      const hit = scene.click(scene.sunAt[0], scene.sunAt[1]);
      console.log(JSON.stringify({ hit, key: scene.talkKey }));""")
    assert result == {"hit": "sun", "key": "garden.act.lampPlain"}


@needs_node
def test_the_fridge_gives_a_beer_on_his_own_box_and_never_in_the_demo() -> None:
    body = """
      const seen = log();
      for (const name of ["fridge", "fridge", "chair", "table", "pc"]) {
        const scene = fresh(S);
        scene.fridgeNext = seen.props.size ? 1 : 0;
        scene.click(...middle(scene, H.THINGS.find((r) => r.name === name)));
        play2(scene, 50, seen);
      }
      console.log(JSON.stringify(plain(seen)));"""
    own = play(body)
    assert {"beer", "cig"} <= set(own["props"]) and "joint" not in own["props"]
    demo = play(body, state(demo=True))
    assert not {"beer", "joint", "cig"} & set(demo["props"]) and "juice" in demo["props"]


@needs_node
def test_a_click_on_his_windows_does_nothing_and_the_sky_neither() -> None:
    result = play("""
      const scene = fresh(S);
      const before = scene.order;
      const hits = [scene.click(scene.stats.x + 4, scene.stats.y + 4), scene.click(scene.dialog.x + 30, scene.dialog.y + 10), scene.click(10, 150)];
      console.log(JSON.stringify({ hits, same: scene.order === before }));""")
    assert result == {"hits": [None, None, None], "same": True}


@needs_node
def test_a_pump_that_starts_takes_him_away_from_what_he_was_told() -> None:
    result = play(f"""
      const scene = fresh(S);
      scene.click(...spot(scene, 7, 6.5));
      play2(scene, 2);
      scene.feed({json.dumps(state(ports__usb1__on=True))});
      console.log(JSON.stringify({{ job: scene.decision.job, order: scene.order, doing: scene.doing }}));""")
    assert result == {"job": "water", "order": None, "doing": "garden.do.water"}


@needs_node
def test_what_he_was_told_runs_on_when_the_pump_stops_or_the_climate_drifts() -> None:
    result = play(f"""
      const wet = {json.dumps(state(ports__usb1__on=True))}, off = {json.dumps(state(plant__checks=checks(probe_vpd="high")))};
      const scene = fresh(wet);
      scene.click(...spot(scene, 7, 6.5));
      scene.feed(S);
      const afterPump = scene.order !== null;
      for (let i = 0; i < 40; i++) {{ scene.feed(off); scene.step(0.1); scene.pending && (scene.pending.at -= 5); }}
      console.log(JSON.stringify({{ afterPump, stillTold: scene.order !== null, job: scene.decision.job }}));""")
    assert result == {"afterPump": True, "stillTold": True, "job": "chill"}


@needs_node
def test_an_alarm_can_be_walked_away_from_and_he_comes_back_to_it() -> None:
    result = play(f"""
      const scene = fresh({json.dumps(state(climate__water_state="dry"))});
      play2(scene, 2);
      const said = scene.saying && scene.saying.text;
      scene.click(...spot(scene, 7, 6.5));
      scene.feed({json.dumps(state(climate__water_state="dry"))});
      const quiet = !scene.saying;
      play2(scene, 60);
      console.log(JSON.stringify({{ said, quiet, order: scene.order, back: scene.saying && scene.saying.text, job: scene.decision.job }}));""")
    assert "garden.say.tank" in result["said"] and result["quiet"] and result["order"] is None
    assert result["job"] == "tank" and "garden.say.tank" in result["back"]


@needs_node
def test_the_windmill_turns_slowly_and_still_shows_the_exhaust_step() -> None:
    result = play(f"""
      const turn = (s) => {{ const scene = fresh(s); const start = scene.rotor; for (let i = 0; i < 150; i++) scene.step(1 / 15); return (scene.rotor - start) / 10; }};
      console.log(JSON.stringify({{ high: turn({json.dumps(state(dfan__step=10))}), low: turn({json.dumps(state(dfan__step=2))}) }}));""")
    assert result["high"] < 1.0, result  # under one radian a second: a full turn takes more than six seconds
    assert result["high"] > result["low"] * 2


# ---------- it stays ----------

STICKY = """
const S = %s;
fire("vgrow:state", { detail: S });
"""


def sticky(body: str, s: dict[str, Any] | None = None) -> Any:
    return run(STICKY % json.dumps(s or state()) + body)


@needs_node
def test_neither_the_mouse_nor_a_click_nor_a_key_closes_it_only_esc_and_its_button() -> None:
    result = sticky("""
      H.open();
      const canvas = created.find((el) => el.on.pointerleave);
      const buttons = created.filter((el) => el.type === "button");
      const open0 = H.isOpen();
      for (let i = 0; i < 20; i++) fire("pointermove", { pointerType: "mouse", clientX: i * 50, clientY: i * 30 });
      const afterMove = H.isOpen();
      canvas.on.pointermove({ pointerType: "mouse", clientX: 400, clientY: 300 });
      canvas.on.click({ clientX: 400, clientY: 300 });
      const afterClick = H.isOpen();
      fire("keydown", { key: "a" });
      fire("keydown", { key: " " });
      const afterKey = H.isOpen();
      fire("pointerdown", {}); fire("wheel", {}); fire("scroll", {});
      const afterWheel = H.isOpen();
      fire("keydown", { key: "Escape" });
      const afterEsc = H.isOpen();
      H.open();
      const again = H.isOpen();
      buttons[1].on.click({});
      console.log(JSON.stringify({ open0, afterMove, afterClick, afterKey, afterWheel, afterEsc, again, afterButton: H.isOpen(), count: buttons.length }));""")
    assert result == {"open0": True, "afterMove": True, "afterClick": True, "afterKey": True, "afterWheel": True,
                      "afterEsc": False, "again": True, "afterButton": False, "count": 2}


@needs_node
def test_the_page_below_gets_no_key_while_it_is_open() -> None:
    result = sticky("""
      let stopped = 0;
      fire("keydown", { key: "a", stopPropagation: () => { stopped++; } });
      const closed = stopped;
      H.open();
      fire("keydown", { key: "a", stopPropagation: () => { stopped++; } });
      console.log(JSON.stringify({ closed, open: stopped }));""")
    assert result == {"closed": 0, "open": 1}


@needs_node
def test_the_pin_opens_the_page_in_the_garden() -> None:
    result = run("""
      store["vgrow.garden.pin"] = "1";
      const before = H.isOpen();
      fire("vgrow:state", { detail: %s });
      const after = H.isOpen();
      fire("keydown", { key: "Escape" });
      fire("vgrow:state", { detail: %s });
      console.log(JSON.stringify({ before, after, closed: H.isOpen(), stays: store["vgrow.garden.pin"] }));""" % (json.dumps(state()), json.dumps(state())))
    assert result == {"before": False, "after": True, "closed": False, "stays": "1"}


@needs_node
def test_the_pin_button_switches_and_says_so() -> None:
    result = sticky("""
      H.open();
      const pin = created.filter((el) => el.type === "button")[0];
      const first = pin.attrs["aria-pressed"];
      pin.on.click({});
      const second = [pin.attrs["aria-pressed"], store["vgrow.garden.pin"]];
      pin.on.click({});
      console.log(JSON.stringify({ first, second, third: [pin.attrs["aria-pressed"], store["vgrow.garden.pin"] || null], open: H.isOpen() }));""")
    assert result == {"first": "false", "second": ["true", "1"], "third": ["false", None], "open": True}


# ---------- nothing under the two buttons: they lie on top of the canvas ----------

BUBBLES = """
const A = window.GardenArt;
const WORDS = ["VPD 1.23. The air is too dry.", "Thermometer: 24.6 degrees and 58 percent. VPD 1.23. The air is too dry, so he sprays now.",
               "Three long lines of what he says about the plants, about the soil and about the tank, because the owner clicked on all of it and wants to read it."];
function placed(W, Ht, bar, head, words) {
  const scene = new H.Scene(fakeCanvas(), {});
  scene.resize(W, Ht);
  scene.bar = bar;
  scene.fx = { head };
  scene.say(words, 10);
  return { scene, box: scene.drawBubble(ctx) };
}
"""
BIG_SCREEN = "427, 240, { left: 313, bottom: 11 }"  # 2560 x 1440, the owner's picture of 07.11.2026


@needs_node
def test_his_bubble_is_never_under_the_buttons_nor_across_the_status_window() -> None:
    result = run(BUBBLES + """
      const SCREENS = [[427, 240, 313, 11], [384, 216, 302, 14], [340, 216, 237, 14], [456, 217, 357, 14], [412, 223, 311, 12], [293, 633, 157, 26], [292, 380, 156, 35], [400, 430, 250, 22]];
      const bad = [];
      let seen = 0;
      for (const [W, Ht, left, bottom] of SCREENS) for (const words of WORDS) {
        const { scene } = placed(W, Ht, { left, bottom }, [0, 0], words);
        for (let x = -150; x <= 150; x += 5) for (let y = -45; y <= 170; y += 3) {
          scene.fx = { head: [x, y] };
          const b = scene.drawBubble(ctx);
          seen++;
          const overBar = b.x + b.w > left && b.y < bottom;
          const overStats = !scene.tall && b.x < scene.stats.x + scene.stats.w && b.y < scene.stats.y + scene.stats.h;
          if (overBar || overStats || b.x < 0 || b.y < 0 || b.x + b.w > W) bad.push({ W, Ht, x, y, b });
        }
      }
      console.log(JSON.stringify({ seen, bad: bad.slice(0, 5) }));""")
    assert result["seen"] > 10000 and result["bad"] == []


@needs_node
def test_the_bubble_from_the_owners_picture_moves_beside_the_buttons_and_still_points_at_him() -> None:
    result = run(BUBBLES + f"""
      const {{ scene, box }} = placed({BIG_SCREEN}, [45, -3], WORDS[0]);
      console.log(JSON.stringify({{ box, tipX: scene.ox + 45, tipY: scene.oy - 3 - 4 }}));""")
    box = result["box"]
    assert box["x"] + box["w"] <= 313
    assert box["x"] + 4 <= result["tipX"] <= box["x"] + box["w"] - 6
    assert box["y"] + box["h"] + 5 == result["tipY"]  # above his head, not across it


@needs_node
def test_away_from_the_buttons_the_bubble_stays_centred_over_his_head() -> None:
    result = run(BUBBLES + f"""
      const {{ scene, box }} = placed({BIG_SCREEN}, [45, 60], WORDS[0]);
      console.log(JSON.stringify({{ box, tipX: scene.ox + 45 }}));""")
    assert abs(result["box"]["x"] + result["box"]["w"] / 2 - result["tipX"]) <= 1


@needs_node
def test_at_the_raised_bed_two_lines_fit_above_his_head() -> None:
    # the spot of the jobs "too dry" and "too damp", grid (3.55, 1.08): his hat starts 3 pixels above the island's origin
    result = run(BUBBLES + """
      const out = [];
      for (const [W, Ht] of [[384, 216], [427, 240], [480, 235]]) {
        const { scene, box } = placed(W, Ht, { left: W - 100, bottom: 16 }, [40, -3], WORDS[1].slice(0, 58));
        out.push({ lines: scene.saying.lines.length, tail: box.y + box.h + 5, hat: scene.oy - 3 });
      }
      console.log(JSON.stringify(out));""")
    for row in result:
        assert row["lines"] == 2 and row["tail"] <= row["hat"], row


@needs_node
def test_his_bobbing_head_does_not_make_the_bubble_jump() -> None:
    result = run(BUBBLES + f"""
      const {{ scene }} = placed({BIG_SCREEN}, [45, 0], WORDS[0]);
      const edge = 39 - scene.oy;  // with his head here, the bubble runs just along the bottom of the buttons
      const at = (y) => {{ scene.fx = {{ head: [45, y] }}; return scene.drawBubble(ctx).x; }};
      const bobbing = [-1, 0, 1, 0, -1, 0, 1, 0].map((d) => at(edge + d));
      console.log(JSON.stringify({{ bobbing, away: at(edge + 12) }}));""")
    assert len(set(result["bobbing"])) == 1
    assert result["away"] > result["bobbing"][0]


@needs_node
def test_the_name_under_the_mouse_is_not_under_the_buttons_either() -> None:
    result = run(BUBBLES + f"""
      const {{ scene }} = placed({BIG_SCREEN}, [0, 0], WORDS[0]);
      const boxes = [], real = A.windowBox;
      A.windowBox = (c, x, y, w, h) => {{ boxes.push({{ x, y, w, h }}); return real(c, x, y, w, h); }};
      scene.hover = {{ label: "garden.thing.lamp", x: 401, y: 20 }};
      scene.drawHover(ctx);
      console.log(JSON.stringify(boxes));""")
    assert len(result) == 1 and result[0]["y"] >= 11


@needs_node
def test_the_garden_measures_its_two_buttons_with_their_outline() -> None:
    result = sticky("""
      let scene = null;
      const resize = H.Scene.prototype.resize;
      H.Scene.prototype.resize = function (w, h) { scene = this; return resize.call(this, w, h); };
      H.open();
      console.log(JSON.stringify({ W: scene.W, H: scene.H, bar: scene.bar }));""")
    assert result == {"W": 400, "H": 300, "bar": {"left": 199, "bottom": 24}}  # 800 x 600 CSS pixels at two per scene pixel


# ---------- the files ----------

def test_the_top_bar_has_the_button_on_every_page() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    top = html[html.index('<header class="app-topbar">'):html.index("</header>")]
    assert 'data-garden-open' in top and 'data-i18n="garden.open"' in top


def test_the_garden_runs_slower_than_real_time() -> None:
    pace = re.search(r"const PACE = ([\d.]+);", (WEB / "garden.js").read_text(encoding="utf-8"))
    assert pace and 0.4 <= float(pace.group(1)) <= 0.8


def test_the_garden_waits_a_minute() -> None:
    assert "const IDLE_MS = 60 * 1000;" in (WEB / "garden.js").read_text(encoding="utf-8")


def test_the_garden_only_looks() -> None:
    for name in FILES:
        src = (WEB / name).read_text(encoding="utf-8")
        assert "/api/cmd" not in src and "fetch(" not in src and "XMLHttpRequest" not in src, name
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//"))
        assert not re.search(r"\bapi\((?!\"/api/hank/state\"\))", code), name


def test_the_page_loads_the_files_in_order_after_the_app() -> None:
    order = web.page().order
    positions = [order.index(f"/static/{name}") for name in FILES]
    assert positions == sorted(positions) and order.index("/static/app.js") < positions[0]


def test_every_garden_file_reloads_open_pages() -> None:
    from vgrow import server

    assert set(FILES) | {"garden.css"} <= set(server.PAGE_FILES)


def test_the_file_rules() -> None:
    for name in (*FILES, "garden.css"):
        raw = (WEB / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), name
        text = raw.decode("utf-8")
        assert web.EM_DASH not in text, name
    for name in FILES:
        src = (WEB / name).read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert banned not in src, (name, banned)
    # the art file holds no language: its only words are pixel rows and colours
    art = (WEB / "garden-art.js").read_text(encoding="utf-8")
    assert not re.search(r"\bt\(\"", art)


def test_the_look_is_our_own_no_borrowed_names() -> None:
    """The owner asked for a retro feel; the world, the man and the font are drawn here, and no other game's name sits in the code."""
    for name in (*FILES, "garden.css"):
        text = (WEB / name).read_text(encoding="utf-8").lower()
        assert "ragnarok" not in text and "poring" not in text, name
