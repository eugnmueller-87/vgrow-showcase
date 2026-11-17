// What Hank does in his garden (web/garden.js): decided only from the app's /api/state answer and GET /api/hank/state,
// never from a timer or a guess. No drawing and no DOM here, so tests/test_garden.py can run every case in node.
//
// window.GardenLogic.decide(state, hank) -> {job, mood, say, params, doing, urgent, soft}
//   job     what he goes and does (web/garden.js has a script per job); "chill" means nothing is left to do
//   urgent  he goes at once. After a few minutes he takes breaks too (web/garden.js), and the bubble keeps saying what is wrong
//   soft    the climate is off: he goes when it has stayed off a while, and takes breaks, because the fans and the humidifier do the real work
//   say     the text key of his bubble, params its values, doing the key of the line under his picture
// window.GardenLogic.notices(state) -> active alarms that are no job: a camera, a rule on the light. They go on his notice board.
// window.GardenLogic.chillPool(state, lightsOn) -> the free-time activities that fit: beer and cigarette are for the owner's own
// box only, the demo stays without them.
// window.GardenLogic.buddy(state, now, lastStateAt, offline) -> what his helper robot watches, {status, items}: see buddy() below.
(function () {
  "use strict";

  const LOST_AFTER_S = 900;  // the automation's own fail-safe age (vgrow/hank/nudges.py OFFLINE_SECONDS)
  const LINK_GRACE_MS = 10 * 1000;  // one failed poll can be a restart for a new version; app.js polls every 5 s
  const LINK_STALE_MS = 30 * 1000;  // six polls without a state: a request that hangs never says "vgrow:offline"

  function num(value) { return typeof value === "number" && isFinite(value) ? value : null; }

  function status(s, metric) {
    const rows = (s.plant && s.plant.checks) || [];
    const row = rows.find((r) => r.metric === metric);
    return row ? row.status : null;
  }

  // The probe hangs at the plant and is what the targets mean; the box sensor only stands in without a probe.
  function climate(s) {
    const c = s.climate || {};
    const pick = (probe, box) => (num(c[probe]) !== null ? num(c[probe]) : num(c[box]));
    return { temp: pick("probe_temp", "temp"), hum: pick("probe_humidity", "humidity"), vpd: pick("probe_vpd", "vpd") };
  }

  function pumpOn(s) {
    const port = (s.thirst && s.thirst.pump && s.thirst.pump.port) || "usb1";
    const row = s.ports && s.ports[port];
    return Boolean((row && row.on === true) || (s.watering && s.watering.active));
  }

  function tankDry(s) {
    const c = s.climate || {};
    if (c.water_state === "dry") return true;
    return (s.alerts || []).some((a) => a.active && (a.name === "water_low" || a.name === "tank_empty"));
  }

  function lightsOn(s) { return num(s.light && s.light.level) !== null && s.light.level > 0; }

  function humidifierOn(s) {
    const port = (s.automation && s.automation.port) || "usb2";
    const row = s.ports && s.ports[port];
    return Boolean(row && row.on === true);
  }

  // The autopilot regulates this device itself, as vgrow/hank/pilot_report.py regulated() says it: Automatik, the device on
  // "auto", no VIVOSUN plan or automation on it, not paused by a hand. Then the air is its job, not the owner's.
  function regulated(s, id) {
    const box = s.autopilot && s.autopilot.boxes && s.autopilot.boxes[0];
    if (!box || box.mode !== "auto" || box.locked_out || box.forced_off) return null;
    const device = (box.devices || []).find((d) => d && d.id === id);
    return device && device.level === "auto" && !device.app_mode && !device.paused_until ? device : null;
  }

  function soilText(s) {
    const soil = s.soil || {};
    if (!soil.fresh) return null;
    return num(soil.relative) !== null ? soil.relative : num(soil.percent);
  }

  function heightCm(s) {
    const box = s.autopilot && s.autopilot.boxes && s.autopilot.boxes[0];
    const value = box ? num(box.height_cm) : null;
    return value !== null ? value : num(s.plant && s.plant.height && s.plant.height.cm);
  }

  function openTodos(s) {
    const rows = (s.grow_plan && s.grow_plan.todo) || [];
    return rows.filter((row) => !row.done);
  }

  function job(name, mood, say, doing, extra) {
    return { job: name, mood, say, doing, params: {}, urgent: false, soft: false, ...extra };
  }

  // The watering decision of the Durst-Index (vgrow/thirst.py trigger): WAIT is nothing to do, the rest is his.
  function thirstJob(s) {
    const decision = s.thirst && s.thirst.decision;
    const soil = s.soil || {};
    const params = { soil: soilText(s) };
    const flow = s.thirst && s.thirst.pump && s.thirst.pump.flow;
    const noPump = Boolean(flow && flow.calibrated === false);
    const thirsty = (extra) => job("thirsty", "worry", noPump ? "garden.say.thirstyNoPump" : "garden.say.thirsty", "garden.do.thirsty",
      { urgent: true, params, ...extra });
    if (decision) {
      if (decision.kind === "PROPOSE") {
        const litres = num(decision.dose && decision.dose.litres);
        return litres === null ? thirsty()
          : job("thirsty", "worry", "garden.say.dose", "garden.do.thirsty", { urgent: true, params: { ...params, litres } });
      }
      if (decision.kind === "BLOCKED" && decision.reason === "blocked") return thirsty();
      if (decision.kind === "ALERT" && decision.reason === "wet_72h") return job("soggy", "worry", "garden.say.soggy", "garden.do.soggy", { urgent: true });
      if (decision.kind === "ALERT") return thirsty();
      if (decision.kind === "ASK" && decision.check && decision.check.how === "finger") {
        const depth = num(decision.check.depth_cm);
        return job("poke", "think", depth === null ? "garden.say.check" : "garden.say.checkDepth", "garden.do.poke", { soft: true, params: { depth } });
      }
    }
    return soil.fresh && soil.state === "low" ? thirsty() : null;
  }

  // Only an alarm about the plant's air is a job. Seen 06.11.2026: "close camera offline" kept him at the board for days,
  // waiting for an adapter in the post. Whatever else is active goes on the board (notices) and he lives on.
  const AIR_ALARMS = ["temp_high", "temp_low", "humidity_high"];

  function alarmJob(s) {
    const active = (s.alerts || []).filter((a) => a.active);
    const c = climate(s);
    const named = (name) => active.some((a) => a.name === name);
    if (named("temp_high")) return job("hot", "worry", "garden.say.hot", "garden.do.hot", { urgent: true, params: { temp: c.temp } });
    if (named("temp_low")) return job("cold", "worry", "garden.say.cold", "garden.do.cold", { urgent: true, params: { temp: c.temp } });
    if (named("humidity_high")) return job("humid", "worry", "garden.say.humid", "garden.do.humid", { urgent: true, params: { hum: c.hum } });
    return null;
  }

  function noteAlarm(name) {
    return !AIR_ALARMS.includes(name) && name !== "water_low" && name !== "tank_empty";
  }

  function notices(s) {
    return (s.alerts || []).filter((a) => a.active && noteAlarm(a.name)).map((a) => String(a.label || a.name));
  }

  // Off target but no alarm. The light is left out on purpose: the plan holds it below the card's value while it is held back.
  function climateJob(s) {
    const c = climate(s);
    const temp = status(s, "probe_temp");
    const hum = status(s, "probe_humidity");
    const vpd = lightsOn(s) ? status(s, "probe_vpd") : null;  // like the autopilot: the VPD only counts with the lights on
    if (temp === "high") return job("hot", "worry", "garden.say.hot", "garden.do.hot", { soft: true, params: { temp: c.temp } });
    if (temp === "low") return job("cold", "worry", "garden.say.cold", "garden.do.cold", { soft: true, params: { temp: c.temp } });
    if (hum === "low" || vpd === "high") {
      return job("dry", "think", vpd === "high" ? "garden.say.dryVpd" : "garden.say.dryHum", "garden.do.dry", { soft: true, params: { vpd: c.vpd, hum: c.hum } });
    }
    if (hum === "high" || vpd === "low") return job("humid", "think", "garden.say.humid", "garden.do.humid", { soft: true, params: { hum: c.hum } });
    return null;
  }

  function decide(s, hank) {
    const age = num(s.sensors_age);
    if (!s.demo && (s.online === false || s.mqtt === false || s.error || (age !== null && age > LOST_AFTER_S))) {
      return job("lost", "worry", "garden.say.lost", "garden.do.lost", { urgent: true });
    }
    const pump = pumpOn(s);
    if (tankDry(s)) return job("tank", "alert", pump ? "garden.say.dryRun" : "garden.say.tank", "garden.do.tank", { urgent: true });
    if (pump) return job("water", "ok", soilText(s) === null ? "garden.say.pump" : "garden.say.pumpSoil", "garden.do.water", { urgent: true, params: { soil: soilText(s) } });
    const alarm = alarmJob(s);
    if (alarm) return alarm;
    const thirst = thirstJob(s);
    if (thirst) return thirst;
    const tank = s.automation && s.automation.tank;
    if (tank && tank.warning) return job("mist", "think", "garden.say.mistTank", "garden.do.mist", { urgent: true });
    const air = climateJob(s);
    if (air) return air;
    return job("chill", "chill", null, null, { lights: lightsOn(s), hankLevel: hank && hank.light ? hank.light.level : null });
  }

  // Everything Hank's helper robot (web/garden.js Robot) reports, read in this one place:
  // - every active row of the server's alert list, the supervision rules for silent signals included (kind "supervision",
  //   level "red" or "yellow"; a row without a level counts as red). The label comes localized from the server.
  // - the page losing the server: app.js fires "vgrow:offline" when a poll fails (offline), and a state that stops coming
  //   is the same news. now and lastStateAt are milliseconds; without them the age does not count.
  // -> {status, items}: status "alert" with at least one item, else "night" while the lamp reports 0, else "calm".
  //    An item is {id, text, level, key}: key is the text key of his line, text fills its {what}. A missing field is calm.
  //    level is carried, not yet used: every active row is a problem to him, yellow as much as red (owner, 15.11.2026: "any
  //    error whatsoever"). Whether a yellow row, a notice in the cockpit, should be quieter for him is the owner's call.
  function buddy(s, now, at, offline) {
    const items = [], ids = new Set();
    const add = (item) => { if (!ids.has(item.id)) { ids.add(item.id); items.push(item); } };
    const word = (value) => (typeof value === "string" && value.trim() ? value.trim() : null);
    const age = num(now) !== null && num(at) !== null ? now - at : null;
    if ((offline === true && (age === null || age >= LINK_GRACE_MS)) || (age !== null && age >= LINK_STALE_MS)) {
      add({ id: "link", text: null, level: "red", key: "garden.robot.link" });
    }
    const rows = s && Array.isArray(s.alerts) ? s.alerts : [];
    for (const row of rows) {
      if (!row || typeof row !== "object" || !row.active) continue;
      const text = word(row.label);
      add({ id: `alert:${word(row.name) || word(row.signal) || text || "?"}`, text, level: row.level === "yellow" ? "yellow" : "red",
        key: text ? "garden.robot.tell" : "garden.robot.tellPlain" });
    }
    if (items.length) return { status: "alert", items };
    const level = num(s && s.light && s.light.level);
    return { status: level !== null && level <= 0 ? "night" : "calm", items };
  }

  function chillPool(s, lights) {
    const own = !s.demo;
    if (!lights) return own ? ["nap", "nap", "game", "beer"] : ["nap", "nap", "game", "juice"];
    return own ? ["beer", "game", "cig", "inspect", "juice"] : ["juice", "game", "inspect"];
  }

  window.GardenLogic = { decide, chillPool, climate, status, pumpOn, tankDry, lightsOn, humidifierOn, soilText, heightCm, openTodos, notices, noteAlarm, regulated, buddy };
})();
