// The water dashboard (#water, vgrow/soil.py): soil moisture with its target, the tank, what was given, the thirst
// index, and the record of the device test (vgrow/device_check.py). Below it the E6's card on the Geräte page
// (vgrow/fan_stick.py).
// Loaded after app.js and cockpit.js and uses their globals t(), $, make(), api(), poll(), when(), ago(), fmt(), qty(),
// setFb(), css() and drawChart(); it never touches render(). It draws from the app's /api/state answer (the event
// "vgrow:state"). Only the E6's buttons switch a device, the owner's own taps; everything else here reads or logs.
(function () {
  "use strict";

  const ARM_MS = 4000;  // a two-tap button waits this long for the second tap
  const HISTORY_EVERY_MS = 5 * 60 * 1000;
  const EVENTS_SHOWN = 5;
  const SOIL_STATE = { low: ["water.stateLow", "warn"], ok: ["water.stateOk", "ok"], high: ["water.stateHigh", "bad"] };
  // Everything a person can only judge at the box, whatever the box reports
  const OWNER_CONFIRMS = new Set(["spectrum", "cfan", "dfan", "pump", "fan_stick", "soil", "push"]);
  const NO_COMMAND = new Set(["tank", "soil", "growcam", "usb_camera"]);  // these only report, nothing is sent to them

  let range = 24;
  let rows = [];
  let fetchedAt = 0;
  let loading = false;
  let snapshot = null;
  let armedUntil = 0;

  function visible() { return !$("page-water").hidden; }

  function onDate(at) { return when(new Date(at * 1000)); }

  // ---------- the chart ----------

  async function loadHistory() {
    if (loading) return;
    loading = true;
    try {
      rows = (await api(`/api/soil/history?hours=${range}`)).rows || [];
      fetchedAt = Date.now();
    } catch (err) {
      console.error(err);
    } finally {
      loading = false;
    }
    drawSoil();
  }

  function relativeOf(percent, soil) {
    const zero = soil.dry_percent || 0;
    return Math.max(0, 100 * (percent - zero) / (soil.wet_percent - zero));
  }

  function drawSoil() {
    if (!visible() || !snapshot || !snapshot.soil) return;
    const soil = snapshot.soil;
    const scaled = soil.calibrated;
    const xs = rows.map((r) => r.at);
    const ys = rows.map((r) => (scaled ? relativeOf(r.percent, soil) : r.percent));
    $("wd-chart-title").textContent = t(scaled ? "water.chartRelative" : "water.chartRaw");
    $("wd-chart-hint").textContent = t(scaled ? "water.chartHintRelative" : "water.chartHintRaw");
    $("wd-chart-empty").hidden = xs.length > 0;
    const series = [{ label: t(scaled ? "water.seriesRelative" : "water.seriesRaw"), stroke: css("--water"), width: 2,
      scale: "%", spanGaps: true, value: (u, v) => (v == null ? "–" : qty(fmt(v, 0), "%")) }];
    const top = Math.max(scaled ? 120 : 100, ...ys.map((v) => v + 5));
    const bands = scaled && soil.trigger != null ? [{ scale: "%", min: soil.trigger, max: 100, label: t("water.band") }] : [];
    drawChart("wd-chart", series, [xs, ys], [{ scale: "%", unit: "%" }], { "%": { range: [0, top] } }, bands, range);
    const table = make("table", "history-table");
    const head = make("tr");
    head.append(make("th", "", t("chart.time")), make("th", "", t("water.seriesRaw")));
    if (scaled) head.append(make("th", "", t("water.seriesRelative")));
    table.append(head);
    for (const r of rows.slice(-48).reverse()) {
      const row = make("tr");
      row.append(make("td", "", onDate(r.at)), make("td", "", qty(fmt(r.percent, 0), "%")));
      if (scaled) row.append(make("td", "", qty(fmt(relativeOf(r.percent, soil), 0), "%")));
      table.append(row);
    }
    $("wd-chart-data").replaceChildren(table);
  }

  // ---------- the tiles ----------

  function renderSoilTile(soil) {
    const scaled = soil && soil.calibrated && soil.relative != null;
    $("wd-soil").textContent = !soil || soil.percent == null ? "–" : fmt(scaled ? soil.relative : soil.percent, 0);
    $("wd-soil-src").textContent = t(scaled ? "water.ofDrained" : "tiles.soilRaw");
    const state = soil && SOIL_STATE[soil.state];
    const pill = $("wd-soil-pill");
    pill.hidden = !state;
    if (state) { pill.textContent = t(state[0]); pill.className = `pill sm ${state[1]}`; }
    const parts = [];
    if (scaled) parts.push(t("water.rawNow", { value: qty(fmt(soil.percent, 0), "%") }));
    if (soil && soil.age != null) parts.push(soil.fresh ? ago(soil.age) : t("tiles.soilSilent"));
    $("wd-soil-sub").textContent = parts.join(" · ");
  }

  function renderTank(c) {
    const wet = c.water_state === "wet", dry = c.water_state === "dry";
    $("wd-tank").textContent = wet ? t("tiles.tankWet") : dry ? t("tiles.tankDry") : "–";
    $("wd-tank-pill").hidden = !dry;
    $("wd-tank-pill").textContent = dry ? t("tiles.tankRefill") : "";
    $("wd-tank-sub").textContent = c.water_since != null ? t("tiles.tankSince", { time: onDate(c.water_since) }) : "";
  }

  // What was given in the last seven days, from the Durst-Index's own ledger: nothing is counted that it does not count.
  function renderGiven(th) {
    const flow = th && th.pump && th.pump.flow;
    if (th && th.given_week_n) {
      $("wd-given").textContent = fmt(th.given_week_l, 2);
      $("wd-given-u").textContent = "l";
      $("wd-given-sub").textContent = t("water.givenCount", { n: th.given_week_n });
    } else {
      $("wd-given").textContent = "–";
      $("wd-given-u").textContent = "";
      $("wd-given-sub").textContent = t(flow && flow.calibrated ? "water.givenNone" : "water.givenNoFlow");
    }
  }

  function renderThirst(th) {
    const known = th && th.ti != null;
    $("wd-thirst").textContent = known ? fmt(th.ti, 0) : "–";
    $("wd-thirst-u").textContent = known ? "%" : "";
    $("wd-thirst-sub").textContent = (th && th.decision && th.decision.text) || "";
  }

  // ---------- the target and setting the sensor to the soil ----------

  function renderTarget(soil) {
    const lead = $("wd-target-lead"), pill = $("wd-target-pill"), status = $("wd-set-status");
    const scaled = soil && soil.calibrated;
    pill.hidden = !soil;
    pill.textContent = t(scaled ? "water.setDone" : "water.setOpen");
    pill.className = `pill sm ${scaled ? "ok" : "warn"}`;
    lead.textContent = scaled && soil.trigger != null
      ? t("water.targetSet", { low: soil.trigger, high: soil.too_wet })
      : t("water.targetUnset");
    status.textContent = scaled
      ? t("water.setStatus", { value: qty(fmt(soil.wet_percent, 0), "%"), time: onDate(soil.wet_at) })
      : t("water.setHow");
    $("wd-set-reset").hidden = !scaled;
    $("wd-set-wet").disabled = !soil || soil.percent == null;
  }

  async function setSoil(body, fb) {
    try {
      await api("/api/cmd", { action: "soil_calibrate", ...body });
      setFb(fb, t("water.setSaved"), "ok");
      fetchedAt = 0;
      poll(true);
    } catch (err) {
      setFb(fb, err.message, "bad");
    }
  }

  // ---------- supply: what the page can say about water at all ----------

  function fact(list, name, value) {
    list.append(make("dt", "", name), make("dd", "", value));
  }

  function renderSupply(s) {
    const list = make("div", "water-facts");
    const th = s.thirst || {};
    const pump = th.pump || {};
    const flow = pump.flow || {};
    const usb = (s.ports || {})[pump.port || "usb1"] || {};
    fact(list, t("water.pumpNow"), t(usb.on ? "ctl.on" : "ctl.off"));
    fact(list, t("water.flow"), flow.calibrated && flow.ml_s != null
      ? qty(fmt(flow.ml_s * 60, 0), "ml/min") : t("water.flowOpen"));
    fact(list, t("water.pumpSwitch"), t(pump.power_confirmed ? "water.pumpConfirmed" : "water.pumpOpen"));
    fact(list, t("water.pot"), th.pot_volume_l != null ? qty(fmt(th.pot_volume_l, 1), "l") : t("water.potOpen"));
    const w = s.watering || {};
    const next = w.next_at != null ? onDate(w.next_at) : null;
    fact(list, t("water.plan"), w.settings && w.settings.enabled
      ? (next ? t("water.planNext", { time: next }) : t("water.planOn")) : t("water.planOff"));
    const mist = (s.automation || {}).tank || {};
    fact(list, t("water.mistTank"), mist.refilled_at != null
      ? t("water.mistRefilled", { time: onDate(mist.refilled_at) }) : "–");
    const soil = s.soil || {};
    if (soil.room_temp != null) {
      fact(list, t("water.room"), `${qty(fmt(soil.room_temp), "°C")} · ${qty(fmt(soil.room_humidity, 0), "%")}`);
    }
    $("wd-supply").replaceChildren(...list.childNodes);
    const events = ((th.events) || []).filter((e) => e.text).slice(0, EVENTS_SHOWN);
    $("wd-events").replaceChildren(...(events.length ? events.map((e) => {
      const li = make("li");
      li.append(make("span", "water-event-at", onDate(e.at)), make("span", "", e.text));
      return li;
    }) : [make("li", "hint", t("water.eventsNone"))]));
  }

  // ---------- the device check ----------

  function apDevice(s, id) {
    const box = ((s.autopilot || {}).boxes || [])[0] || {};
    return (box.devices || []).find((d) => d.id === id) || {};
  }

  function autoText(s, id) {
    const hum = s.automation || {};
    const w = s.watering || {};
    const fan = s.fan_stick || {};
    const soil = s.soil || {};
    switch (id) {
      case "light": return t(((s.light_schedule || {}).settings || {}).enabled ? "devcheck.autoLightOn" : "devcheck.autoLightOff");
      case "dfan": case "cfan": return apDevice(s, id).status || t("devcheck.autoNone");
      case "humidifier": return t(hum.implausible ? "devcheck.autoHumLocked" : hum.enabled ? "devcheck.autoHumOn" : "devcheck.autoHumOff");
      case "pump": return t(w.settings && w.settings.enabled ? "devcheck.autoPumpOn" : "devcheck.autoPumpOff");
      case "tank": return t("devcheck.autoTank");
      case "soil": return t(soil.calibrated ? "devcheck.autoSoilOn" : "devcheck.autoSoilOff");
      case "fan_stick": return t(fan.auto ? "devcheck.autoFanOn" : "devcheck.autoFanOff");
      default: return "–";
    }
  }

  function liveText(s, id) {
    const c = s.climate || {};
    const camera = (ident) => (s.cameras || []).find((x) => x.id === ident) || {};
    switch (id) {
      case "light": return s.light ? qty(fmt(s.light.level, 0), "%") : "–";
      case "spectrum": {
        const names = { seedling: "spectrum.seedling", veg: "spectrum.veg", bloom: "spectrum.bloom" };
        return s.light && names[s.light.spectrum] ? t(names[s.light.spectrum]) : "–";
      }
      case "dfan": return s.dfan ? t("devcheck.step", { n: s.dfan.step }) : "–";
      case "cfan": return s.cfan ? t("devcheck.step", { n: s.cfan.step }) : "–";
      case "humidifier": return t((s.ports || {}).usb2?.on ? "ctl.on" : "ctl.off");
      case "pump": return t((s.ports || {}).usb1?.on ? "ctl.on" : "ctl.off");
      case "tank": return c.water_state === "wet" ? t("tiles.tankWet") : c.water_state === "dry" ? t("tiles.tankDry") : "–";
      case "soil": return s.soil && s.soil.percent != null ? qty(fmt(s.soil.percent, 0), "%") : "–";
      case "fan_stick": return s.fan_stick && s.fan_stick.fan_seen ? t("devcheck.step", { n: s.fan_stick.step }) : "–";
      case "growcam": return t(camera("growcam").reachable ? "devcheck.seen" : "devcheck.notSeen");
      case "usb_camera": return t(camera("usb").reachable ? "devcheck.seen" : "devcheck.notSeen");
      default: return "–";
    }
  }

  const CHECK_ROWS = [["light", "devcheck.light"], ["spectrum", "devcheck.spectrum"], ["dfan", "devcheck.dfan"],
    ["cfan", "devcheck.cfan"], ["humidifier", "devcheck.humidifier"], ["pump", "devcheck.pump"], ["tank", "devcheck.tank"],
    ["soil", "devcheck.soil"], ["fan_stick", "devcheck.fanStick"], ["growcam", "devcheck.growcam"],
    ["usb_camera", "devcheck.usbCamera"], ["push", "devcheck.push"]];

  function verdict(value) {
    const on = value === true, off = value === false;
    const pill = make("span", `pill sm ${on ? "ok" : off ? "bad" : "warn"}`,
      t(on ? "devcheck.yes" : off ? "devcheck.no" : "devcheck.open"));
    return pill;
  }

  async function confirm(device, ok) {
    try {
      await api("/api/cmd", { action: "device_check_confirm", device, ok });
      poll(true);
    } catch (err) {
      setFb("wd-check-fb", err.message, "bad");
    }
  }

  function renderCheck(s) {
    const data = s.device_check || {};
    const rowsHtml = CHECK_ROWS.map(([id, name]) => {
      const r = data[id] || {};
      const row = make("tr");
      const label = make("td", "", t(name));
      if (r.at != null) label.append(make("small", "water-check-at", onDate(r.at)));
      const command = make("td");
      if (NO_COMMAND.has(id)) command.textContent = "–";
      else command.append(verdict(r.command));
      if (r.seconds != null) command.append(make("small", "water-check-at", qty(fmt(r.seconds), "s")));
      const effect = make("td");
      effect.append(verdict(r.effect));
      if (r.before != null && r.after != null) {
        effect.append(make("small", "water-check-at", t("devcheck.measured", { before: qty(fmt(r.before), r.unit || ""), after: qty(fmt(r.after), r.unit || "") })));
      } else if (r.owner) {
        effect.append(make("small", "water-check-at", t("devcheck.byOwner")));
      }
      if (OWNER_CONFIRMS.has(id) && !r.owner) {
        const buttons = make("span", "water-check-btns");
        const yes = make("button", "btn sm", t("devcheck.works"));
        const no = make("button", "btn sm", t("devcheck.fails"));
        yes.type = no.type = "button";
        yes.addEventListener("click", () => confirm(id, true));
        no.addEventListener("click", () => confirm(id, false));
        buttons.append(yes, no);
        effect.append(buttons);
      }
      const live = make("td", "", liveText(s, id));
      const auto = make("td", "", autoText(s, id));
      // On a phone the table stacks and its head is hidden: each cell then names its column (water.css)
      live.dataset.label = t("devcheck.now");
      command.dataset.label = t("devcheck.command");
      effect.dataset.label = t("devcheck.effect");
      auto.dataset.label = t("devcheck.auto");
      row.append(label, live, command, effect, auto);
      return row;
    });
    $("wd-check").replaceChildren(...rowsHtml);
  }

  let checkSig = null;

  function render(s) {
    snapshot = s;
    if (!visible()) return;
    renderSoilTile(s.soil);
    renderTank(s.climate || {});
    renderGiven(s.thirst);
    renderThirst(s.thirst);
    renderTarget(s.soil);
    renderSupply(s);
    // The table holds buttons: drawn anew only when something in it changed, so a tap is never lost mid-press.
    const sig = JSON.stringify([s.device_check, CHECK_ROWS.map(([id]) => [liveText(s, id), autoText(s, id)])]);
    if (sig !== checkSig) { checkSig = sig; renderCheck(s); }
    if (Date.now() - fetchedAt > HISTORY_EVERY_MS) loadHistory();
  }

  // ---------- the E6 on the E25 stick, a card on the Geräte page ----------

  function renderFan(s) {
    const fan = s.fan_stick;
    const card = $("card-e6");
    card.hidden = !fan || !fan.found;
    if (card.hidden) return;
    $("e6-val").textContent = fan.fan_seen ? (fan.step === 0 ? t("ctl.off") : fmt(fan.step, 0)) : "–";
    $("e6-meta").textContent = t(fan.online ? (fan.fan_seen ? "fanStick.online" : "fanStick.noFan") : "fanStick.offline");
    for (const b of document.querySelectorAll("#e6-steps [data-e6-step]")) {
      b.classList.toggle("on", fan.fan_seen && Number(b.dataset.e6Step) === fan.step);
      b.disabled = !fan.online;
    }
    const osc = $("e6-osc");
    osc.classList.toggle("on", fan.osc);
    osc.setAttribute("aria-pressed", String(fan.osc));
    osc.disabled = !fan.online;
    const auto = $("e6-auto");
    auto.setAttribute("aria-pressed", String(fan.auto));
    auto.classList.toggle("on", fan.auto);
    fillSteps($("e6-day"), fan.day_step);
    fillSteps($("e6-night"), fan.night_step);
    $("e6-hint").textContent = t(fan.paused_until ? "fanStick.paused" : fan.auto ? "fanStick.autoOn" : "fanStick.autoOff",
      fan.paused_until ? { time: onDate(fan.paused_until) } : undefined);
  }

  function fillSteps(select, value) {
    if (document.activeElement === select) return;
    if (!select.options.length) {
      for (let step = 0; step <= 10; step++) {
        const option = make("option", "", step === 0 ? t("ctl.off") : String(step));
        option.value = String(step);
        select.append(option);
      }
    }
    if (value != null) select.value = String(value);
  }

  async function fanCommand(body) {
    try {
      await api("/api/cmd", body);
      setFb("fb-e6", "", "");
      poll(true);
    } catch (err) {
      setFb("fb-e6", err.message, "bad");
    }
  }

  function wire() {
    $("wd-range").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-h]");
      if (!b) return;
      range = Number(b.dataset.h);
      for (const other of $("wd-range").querySelectorAll("button")) other.classList.toggle("on", other === b);
      loadHistory();
    });
    $("wd-set-wet").addEventListener("click", () => {
      const button = $("wd-set-wet");
      if (Date.now() >= armedUntil) {  // two taps: this reading becomes the top of the scale
        armedUntil = Date.now() + ARM_MS;
        button.textContent = t("water.setWetAgain");
        setTimeout(() => { button.textContent = t("water.setWet"); }, ARM_MS + 100);
        return;
      }
      armedUntil = 0;
      button.textContent = t("water.setWet");
      setSoil({ point: "wet" }, "wd-set-fb");
    });
    $("wd-set-reset").addEventListener("click", () => setSoil({ reset: true }, "wd-set-fb"));
    $("e6-steps").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-e6-step]");
      if (b) fanCommand({ action: "fan_stick_step", step: Number(b.dataset.e6Step) });
    });
    $("e6-osc").addEventListener("click", () => fanCommand({ action: "fan_stick_osc", on: $("e6-osc").getAttribute("aria-pressed") !== "true" }));
    $("e6-auto").addEventListener("click", () => fanCommand({ action: "fan_stick_config", auto: $("e6-auto").getAttribute("aria-pressed") !== "true" }));
    for (const [id, key] of [["e6-day", "day_step"], ["e6-night", "night_step"]]) {
      $(id).addEventListener("change", (e) => fanCommand({ action: "fan_stick_config", [key]: Number(e.target.value) }));
    }
    addEventListener("hashchange", () => {
      if (!snapshot) return;
      requestAnimationFrame(() => { render(snapshot); if (visible()) drawSoil(); });
    });
    addEventListener("resize", () => { if (visible()) drawSoil(); });
  }

  wire();
  document.addEventListener("vgrow:state", (e) => { render(e.detail); renderFan(e.detail); });
})();
