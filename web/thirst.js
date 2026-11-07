// The Durst-Index card on the Automatik page (#thirst, vgrow/thirst.py), next to the watering schedule: first "Topf
// einrichten" while no pot is set up, then the index bar with its band, the forecast, the plan by stage (like the
// feeding plan), the one-tap check, and the owner's inputs (weighing, watered by hand, refilled, jug runs, the pot).
// Nothing here switches a device: every button is a thirst_* action that only logs, and a scheduled watering slot
// runs only through the need check on the server.
// Loaded after app.js and cockpit.js and uses their globals t(), tn(), LANG, $, poll(), when(), dateShort(), fmt(),
// qty(), setFb(), feedEdge() and window.PlantWorkspace; it never touches render() or state. It draws from the app's
// /api/state answer (the event "vgrow:state", key "thirst"). The card is built once and only its values change on every
// answer, so a number being typed is never wiped. Every text is a key of web/i18n.js, written out in full.
(function () {
  "use strict";

  const ARM_MS = 4000;  // a two-tap button waits this long for the second tap
  const MAX_TI = 150;
  const KIND = {  // decision -> [pill text, pill colour, note colour]
    WAIT: ["thirst.kind.WAIT", "ok", "info"],
    ASK: ["thirst.kind.ASK", "warn", "warn"],
    PROPOSE: ["thirst.kind.PROPOSE", "info", "info"],
    BLOCKED: ["thirst.kind.BLOCKED", "warn", "warn"],
    ALERT: ["thirst.kind.ALERT", "bad", "bad"],
  };
  const CONF = { high: "thirst.conf.high", medium: "thirst.conf.medium", low: "thirst.conf.low" };
  const FINGER = [["dry", "thirst.dry"], ["moist", "thirst.moist"], ["wet", "thirst.wet"]];
  const LIFT = [["light", "thirst.light"], ["medium", "thirst.medium"], ["heavy", "thirst.heavy"]];
  const POT_FLAGS = [["drain", "thirst.potDrain"], ["saucer", "thirst.potSaucer"], ["feet", "thirst.potFeet"],
    ["wick", "thirst.potWick"], ["lifts", "thirst.potLifts"], ["scale", "thirst.potScale"],
    ["dryback", "thirst.potDryback"]];
  const PLAN_ROWS = [["stage", "thirst.planStage"], ["trigger", "thirst.planTrigger"], ["cap", "thirst.planCap"],
    ["use", "thirst.planUse"], ["every", "thirst.planEvery"], ["week", "thirst.planWeek"]];
  const GLOSSARY = [["thirst.glTi", "thirst.glTiText"], ["thirst.glEt", "thirst.glEtText"],
    ["thirst.glVpd", "thirst.glVpdText"], ["thirst.glBand", "thirst.glBandText"],
    ["thirst.glGate", "thirst.glGateText"]];

  let built = null;  // the card's elements, made once
  let open = null;  // the form shown: weigh, manual, refill, flow, pot (one at a time)
  let last = null;  // the last "thirst" answer
  const arm = {};  // what -> until (ms)
  let busy = false;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // A fixed text: drawn now and again by app.js's applyStatic() after a language switch (data-i18n).
  function stat(tag, cls, key) {
    const e = el(tag, cls, t(key));
    e.dataset.i18n = key;
    return e;
  }

  function btn(cls, key, onClick) {
    const b = stat("button", cls, key);
    b.type = "button";
    b.dataset.key = key;
    b.addEventListener("click", onClick);
    return b;
  }

  function num(v, digits) {
    return new Intl.NumberFormat(LANG === "de" ? "de-DE" : "en-US", { maximumFractionDigits: digits ?? 1,
      useGrouping: false }).format(v);
  }

  function litres(v) { return qty(num(v, Math.abs(v) < 1 ? 2 : 1), "l"); }

  function parse(text) {  // 1,5 and 1.5 in both languages; "" is none
    const s = String(text ?? "").trim().replace(",", ".");
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : NaN;
  }

  function armed(what) { return Date.now() < (arm[what] || 0); }

  function armNow(what, button, askKey) {
    arm[what] = Date.now() + ARM_MS;
    button.classList.add("arm");
    if (askKey) button.textContent = t(askKey);
    setTimeout(() => { if (!armed(what)) { button.classList.remove("arm"); button.textContent = t(button.dataset.key); } },
      ARM_MS + 100);
  }

  // POST /api/cmd with the answer kept: a second tap ("ask") comes back as 409 with its sentence.
  async function cmd(body) {
    const res = await fetch("/api/cmd", {
      method: "POST", credentials: "same-origin",
      headers: { "X-Lang": LANG, "Content-Type": "application/json", ...(window.PlantWorkspace?.headers() || {}) },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    return { ok: res.ok, data };
  }

  async function send(body, done) {
    if (busy) return null;
    busy = true;
    setFb("fb-thirst", t("fb.sending"));
    try {
      const { ok, data } = await cmd(body);
      if (ok) {
        setFb("fb-thirst", t("thirst.saved"), "ok");
        if (done) done(data);
        poll(true);
      } else {
        setFb("fb-thirst", data.error || t("thirst.failed"), "bad");
      }
      return { ok, data };
    } catch (err) {
      setFb("fb-thirst", err.message, "bad");
      return null;
    } finally {
      busy = false;
    }
  }

  // ---------- the card, built once ----------

  function field(labelKey, input) {
    const wrap = el("label", "th-field");
    wrap.append(stat("span", "th-flabel", labelKey), input);
    return wrap;
  }

  function textInput(id, mode, placeholderKey) {
    const i = el("input", "th-in");
    i.type = "text";
    i.id = id;
    i.inputMode = mode;
    i.autocomplete = "off";
    i.placeholder = t(placeholderKey);
    i.dataset.i18nAttr = `placeholder:${placeholderKey}`;
    return i;
  }

  function chipToggle(key, on) {
    const b = stat("button", "th-toggle", key);
    b.type = "button";
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.addEventListener("click", () => b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true"));
    return b;
  }

  function pressed(b) { return b.getAttribute("aria-pressed") === "true"; }

  function formWrap(what) {
    const wrap = el("div", "th-form");
    wrap.dataset.what = what;
    return wrap;
  }

  function build() {
    const body = $("thirst-body");
    if (!body) return null;
    const b = {};
    // 1. without a pot: setting it up comes first
    b.setup = el("div", "th-setup");
    b.setup.append(stat("p", "th-setup-h", "thirst.setupTitle"), stat("p", "hint", "thirst.setupHint"));
    // 2. the index and its band
    b.hero = el("div", "th-hero");
    const ti = el("div", "th-ti");
    const val = el("span", "th-val");
    b.tiValue = el("span", "v");
    b.tiUnit = el("span", "u", "%");
    val.append(b.tiValue, b.tiUnit);
    b.tiNote = el("span", "th-note");
    ti.append(stat("span", "k", "thirst.ti"), val, stat("span", "th-help", "thirst.tiHelp"), b.tiNote);
    b.bar = el("div", "th-bar");
    b.bar.setAttribute("role", "img");
    b.band = el("i", "th-band");
    b.fill = el("i", "th-fill");
    b.line = el("i", "th-line");
    b.line.style.left = `${(100 / MAX_TI) * 100}%`;
    b.mark = el("i", "th-mark");
    b.bar.append(b.band, b.fill, b.line, b.mark);
    const scale = el("div", "th-scale");
    const s100 = el("span", "th-s100", "100");
    s100.style.left = `${(100 / MAX_TI) * 100}%`;
    scale.append(el("span", "", "0"), s100, el("span", "", String(MAX_TI)));
    b.barWrap = el("div", "th-barwrap");
    b.barWrap.append(b.bar, scale);
    b.hero.append(ti, b.barWrap);
    b.over = el("p", "th-over");
    b.chips = el("div", "th-chips");
    b.decision = el("div", "note th-decision");
    b.decisionText = el("p", "th-dtext");
    b.why = el("ul", "th-why");
    b.waiting = el("p", "th-wait");
    b.decision.append(b.decisionText, b.why, b.waiting);
    // 3. the forecast
    b.forecast = el("div", "th-forecast");
    b.forecastLines = el("ul", "th-flines");
    b.forecast.append(stat("b", "sub-h", "thirst.forecastTitle"), b.forecastLines);
    b.gate = el("p", "note th-gate");
    // 4. the plan by stage
    b.plan = el("div", "th-plan");
    b.planHead = stat("b", "sub-h", "thirst.planTitle");
    b.planCaption = stat("p", "feed-cap", "thirst.planCaption");
    b.planTable = el("div", "th-plan-table");
    b.basis = el("ul", "th-basis");
    b.legend = el("p", "feed-cap th-legend");
    b.plan.append(b.planHead, b.planCaption, b.planTable, b.legend, b.basis);
    // 5. the one-tap check, and the question after a watering
    b.check = el("div", "th-check");
    b.checkQ = el("p", "th-q");
    b.checkBtns = el("div", "chips");
    b.check.append(b.checkQ, b.checkBtns);
    b.arrival = el("div", "th-arrival");
    b.arrivalQ = el("p", "th-q");
    b.arrivalBtns = el("div", "chips");
    b.arrival.append(b.arrivalQ, b.arrivalBtns);
    // the pump: power, flow, a running USB 1
    b.pump = el("div", "th-pump");
    b.pumpOn = el("div", "banner th-pump-on");  // the card's one Banner, at the top
    b.pumpOn.hidden = true;  // until an answer says the pump has power
    b.pumpFlow = el("p", "hint th-pump-flow");
    b.pumpPower = el("p", "hint th-pump-power");
    b.pump.append(b.pumpFlow, b.pumpPower);
    // 6. the actions and their forms
    b.actions = el("div", "th-actions");
    for (const [what, key] of [["weigh", "thirst.weigh"], ["manual", "thirst.manual"], ["refill", "thirst.refill"],
      ["flow", "thirst.flow"], ["pot", "thirst.pot"]]) {
      const a = btn("btn sm", key, () => { open = open === what ? null : what; update(); });
      a.dataset.what = what;
      b.actions.appendChild(a);
    }
    b.forms = { weigh: weighForm(), manual: manualForm(), refill: refillForm(), flow: flowForm(), pot: potForm() };
    const forms = el("div", "th-forms");
    for (const form of Object.values(b.forms)) forms.appendChild(form.wrap);
    b.eventsHead = stat("b", "sub-h th-events-h", "thirst.events");
    b.events = el("ol", "th-events");
    const gloss = el("details", "disclosure th-gloss");  // a Disclosure: chevron, title, what is inside
    const list = el("dl", "th-gl");
    for (const [term, text] of GLOSSARY) list.append(stat("dt", "", term), stat("dd", "", text));
    const summary = el("summary");
    const chev = dsIcon("chevron");
    chev.setAttribute("class", "chev");
    const label = el("span", "t");
    label.append(stat("b", "", "thirst.howTitle"), stat("span", "", "thirst.howDesc"));
    summary.append(chev, label);
    const glossBody = el("div", "disclosure-body");
    glossBody.append(list);
    gloss.append(summary, glossBody);
    body.append(b.pumpOn, b.setup, b.hero, b.over, b.chips, b.decision, b.forecast, b.gate, b.plan, b.check, b.arrival,
      b.pump, b.actions, forms, b.eventsHead, b.events, gloss);
    return b;
  }

  function weighForm() {
    const wrap = formWrap("weigh");
    const kg = textInput("th-kg", "decimal", "thirst.kgPlaceholder");
    const full = chipToggle("thirst.weighFull", false);
    const save = btn("btn primary", "thirst.weighSave", () => {
      const value = parse(kg.value);
      if (value == null || Number.isNaN(value)) { setFb("fb-thirst", t("thirst.needNumber"), "bad"); return; }
      send({ action: "thirst_weigh", kg: value, full: pressed(full) }, () => { kg.value = ""; open = null; update(); });
    });
    wrap.append(stat("p", "hint", "thirst.weighHint"), field("thirst.kg", kg), full, save);
    return { wrap };
  }

  function manualForm() {
    const wrap = formWrap("manual");
    const amount = textInput("th-litres", "decimal", "thirst.litresPlaceholder");
    const at = el("input", "th-in");
    at.type = "datetime-local";
    at.id = "th-when";
    let large = false;
    const save = btn("btn primary", "thirst.manualSave", async () => {
      const value = parse(amount.value);
      if (value == null || Number.isNaN(value)) { setFb("fb-thirst", t("thirst.needNumber"), "bad"); return; }
      const body = { action: "thirst_manual", litres: value };
      if (at.value) body.at = at.value;
      if (large) body.large = true;
      const out = await send(body, () => { amount.value = ""; at.value = ""; open = null; update(); });
      large = false;
      if (out && !out.ok && out.data && out.data.ask === "large") {
        large = true;
        armNow("manual", save, "thirst.manualSaveArm");
      }
    });
    wrap.append(field("thirst.litres", amount), field("thirst.when", at), stat("p", "hint", "thirst.feedNote"), save);
    return { wrap };
  }

  function refillForm() {
    const wrap = formWrap("refill");
    const amount = textInput("th-refill", "decimal", "thirst.litresPlaceholder");
    const quick = el("div", "chips");
    for (const n of [1, 2, 6]) {
      const q = el("button", "", `${num(n)} l`);
      q.type = "button";
      q.addEventListener("click", () => { amount.value = num(n); });
      quick.appendChild(q);
    }
    const feed = chipToggle("thirst.withFeed", false);
    const save = btn("btn primary", "thirst.refillSave", () => {
      const value = parse(amount.value);
      if (value == null || Number.isNaN(value)) { setFb("fb-thirst", t("thirst.needNumber"), "bad"); return; }
      send({ action: "thirst_refill", litres: value, feed: pressed(feed) }, () => { amount.value = ""; open = null; update(); });
    });
    wrap.append(quick, field("thirst.litres", amount), feed, stat("p", "hint", "thirst.refillHint"), save);
    return { wrap };
  }

  function flowForm() {
    const wrap = formWrap("flow");
    const seconds = textInput("th-flow-s", "numeric", "thirst.flowSecondsPlaceholder");
    const ml = textInput("th-flow-ml", "decimal", "thirst.flowMlPlaceholder");
    let confirmation = null;
    const resetConfirmation = () => {
      confirmation = null;
      delete arm.flow;
      save.textContent = t("thirst.flowSave");
    };
    seconds.addEventListener("input", resetConfirmation);
    ml.addEventListener("input", resetConfirmation);
    const save = btn("btn primary", "thirst.flowSave", async () => {
      const s = parse(seconds.value), m = parse(ml.value);
      if (s == null || m == null || Number.isNaN(s) || Number.isNaN(m)) { setFb("fb-thirst", t("thirst.needNumber"), "bad"); return; }
      const values = JSON.stringify([s, m]);
      const body = { action: "thirst_flow", seconds: s, ml: m };
      if (confirmation === values && armed("flow")) body.confirm = true;
      const out = await send(body, () => { ml.value = ""; open = null; update(); });
      resetConfirmation();
      if (out && !out.ok && out.data?.ask === "flow") {
        confirmation = values;
        armNow("flow", save, "thirst.flowSaveArm");
      }
    });
    const reset = btn("btn sm", "thirst.flowReset", () => {
      if (!armed("flowReset")) { armNow("flowReset", reset, "thirst.flowResetArm"); return; }
      delete arm.flowReset;
      send({ action: "thirst_flow", reset: true }, () => { open = null; update(); });
    });
    wrap.append(stat("p", "hint", "thirst.flowHint"), field("thirst.flowSeconds", seconds), field("thirst.flowMl", ml),
      save, reset);
    return { wrap, seconds };
  }

  function potForm() {
    const wrap = formWrap("pot");
    const volume = textInput("th-vol", "decimal", "thirst.volumePlaceholder");
    const diameter = textInput("th-dia", "decimal", "thirst.cmPlaceholder");
    const height = textInput("th-hgt", "decimal", "thirst.cmPlaceholder");
    const step = textInput("th-step", "decimal", "thirst.stepPlaceholder");
    const flags = {};
    const chips = el("div", "chips th-flags");
    for (const [name, key] of POT_FLAGS) {
      flags[name] = chipToggle(key, false);
      chips.appendChild(flags[name]);
    }
    let confirm = false;
    const save = btn("btn primary", "thirst.potSave", async () => {
      const body = { action: "thirst_pot", volume_l: volume.value, diameter_cm: diameter.value, height_cm: height.value,
        scale_step_g: step.value };
      for (const [name] of POT_FLAGS) body[name] = pressed(flags[name]);
      if (confirm) body.confirm = true;
      const out = await send(body, () => { open = null; update(); });
      confirm = false;
      if (out && !out.ok && out.data && out.data.ask) {
        confirm = true;
        armNow("pot", save, "thirst.potSaveArm");
      }
    });
    wrap.append(stat("p", "hint", "thirst.potHint"), field("thirst.volume", volume),
      field("thirst.diameter", diameter), field("thirst.height", height), chips, field("thirst.step", step), save);
    return { wrap, volume, diameter, height, step, flags };
  }

  // ---------- drawing a state ----------

  function hours(seconds) { return num(Math.max(0, seconds) / 3600, seconds < 36000 ? 1 : 0); }

  function chipEl(text, cls) { return el("span", `chip ${cls || ""}`.trim(), text); }

  function capital(text) { return text ? text.charAt(0).toUpperCase() + text.slice(1) : text; }

  function drawChips(w) {
    const out = [];
    if (w.confidence) out.push(chipEl(t(CONF[w.confidence] || "thirst.conf.low"), w.confidence === "low" ? "warn" : ""));
    if (w.measured) {
      const age = Date.now() / 1000 - w.measured.at;
      out.push(chipEl(t(w.measured.kind === "weigh" ? "thirst.measuredWeigh" : w.measured.kind === "sensor" ? "thirst.measuredSensor" : "thirst.measuredFinger", { h: hours(age) })));
    } else if (w.ti != null) {
      const checked = w.last_check ? w.last_check.at : null;
      const weighed = w.last_weigh != null && (checked == null || w.last_weigh >= checked);
      out.push(chipEl(weighed ? t("thirst.estimatedWeigh", { h: hours(Date.now() / 1000 - w.last_weigh) })
        : checked != null ? t("thirst.estimated", { h: hours(Date.now() / 1000 - checked) })
          : t("thirst.estimatedNever"), "warn"));
    }
    if (w.band != null) out.push(chipEl(t("thirst.band", { band: num(Math.round(w.band), 0) })));
    for (const block of w.blocks || []) out.push(chipEl(capital(block.text), "warn"));
    if (w.stage && w.stage.contested && !(w.blocks || []).some((b) => b.key === "stage")) {
      out.push(chipEl(t("thirst.stageContested"), "warn"));
    }
    if (w.drains_without) out.push(chipEl(tn("thirst.drains", w.drains_without, { n: w.drains_without })));
    built.chips.replaceChildren(...out);
  }

  function drawForecast(w) {
    const f = w.forecast;
    built.forecast.hidden = !f;
    if (!f) return;
    const lines = f.lines || (f.text ? [f.text] : []);
    built.forecastLines.replaceChildren(...lines.map((line) => el("li", "", line)));
  }

  function planCell(week, row) {
    if (row === "stage") {
      const cell = el("span", "", stageShort(week.label));
      cell.appendChild(el("small", "", t(week.stage_estimated ? "thirst.planEstimated" : "thirst.planSet")));
      return cell;
    }
    // Only the stage card's own presets are no estimate here: the trigger rests on the assumed available water, the use
    // on the model's start value (both until the first weighed dry-down).
    if (row === "trigger") return `≈ ${litres(week.d_trig_l)}`;
    if (row === "cap") return week.cap_by === "stage" ? litres(week.cap_l) : `≈ ${litres(week.cap_l)}`;
    if (row === "use") return `≈ ${litres(week.use_l_day)}`;
    if (row === "every") return week.every_days == null ? "–" : `≈ ${tn("thirst.planDays", week.every_days, { n: num(week.every_days) })}`;
    if (row === "week") return `≈ ${litres(week.week_l)}`;
    return "";
  }

  function drawPlan(w) {
    const p = w.plan;
    built.plan.hidden = !p || !(p.weeks || []).length;
    if (built.plan.hidden) return;
    const sig = JSON.stringify([LANG, p.weeks, p.basis]);
    if (built.planTable.dataset.sig === sig) return;
    built.planTable.dataset.sig = sig;
    const table = el("table", "feed-chart th-plan-chart");
    const head = table.createTHead().insertRow();
    head.appendChild(el("th"));
    for (const week of p.weeks) {
      const th = el("th", week.now ? "now" : "", dateShort(week.from));
      th.scope = "col";
      if (week.now) th.appendChild(el("small", "", t("thirst.planNow")));
      head.appendChild(th);
    }
    const tbody = table.createTBody();
    for (const [row, key] of PLAN_ROWS) {
      const tr = tbody.insertRow();
      const th = el("th", "", t(key));
      th.scope = "row";
      tr.appendChild(th);
      for (const week of p.weeks) {
        const td = el("td", week.now ? "now" : "");
        const value = planCell(week, row);
        if (typeof value === "string") td.textContent = value; else td.appendChild(value);
        tr.appendChild(td);
      }
    }
    const wrap = el("div", "feed-scroll");
    wrap.appendChild(table);
    wrap.addEventListener("scroll", () => { if (typeof feedEdge === "function") feedEdge(wrap); }, { passive: true });
    built.planTable.replaceChildren(wrap);
    requestAnimationFrame(() => { if (typeof feedEdge === "function") feedEdge(wrap); });
    built.legend.textContent = t("thirst.planLegend");
    built.basis.replaceChildren(...(p.basis || []).map((b) => {
      const li = el("li", b.measured ? "measured" : "estimated");
      li.append(el("span", "th-mark-s", b.measured ? "✓" : "≈"), el("span", "", b.text));
      li.title = t(b.measured ? "thirst.basisMeasured" : "thirst.basisEstimated");
      return li;
    }));
  }

  function checkButtons(w) {
    const check = (w.decision || {}).check || {};
    const lift = check.how === "lift" || (w.pot && w.pot.lifts && !w.hold);
    const depth = check.depth_cm || w.depth_cm || 5;
    built.checkQ.textContent = lift ? t("thirst.liftQ") : t("thirst.fingerQ", { depth: num(depth) });
    const answers = lift ? LIFT : FINGER;
    const sig = `${LANG}|${lift}|${depth}`;
    if (built.checkBtns.dataset.sig === sig) return;
    built.checkBtns.dataset.sig = sig;
    built.checkBtns.replaceChildren(...answers.map(([answer, key]) => {
      const b = el("button", "", t(key));
      b.type = "button";
      b.addEventListener("click", () => send({ action: "thirst_check", answer, depth_cm: lift ? null : depth }));
      return b;
    }));
  }

  function arrivalButtons(w) {
    const a = w.arrival;
    const sig = a ? `${LANG}|${a.event}|${a.pump}` : "";
    if (built.arrival.dataset.sig === sig) return;
    built.arrival.dataset.sig = sig;
    if (!a) { built.arrivalBtns.replaceChildren(); return; }
    const pairs = a.pump
      ? [["dry", "thirst.arrivedDry"], ["saucer", "thirst.arrivedSaucer"], ["no", "thirst.arrivedNo"]]
      : [[true, "thirst.saucerWater"], [false, "thirst.saucerDry"]];
    built.arrivalQ.textContent = t(a.pump ? "thirst.arrivedQ" : "thirst.saucerQ");
    built.arrivalBtns.replaceChildren(...pairs.map(([value, key]) => {
      const b = el("button", "", t(key));
      b.type = "button";
      b.addEventListener("click", () => send(a.pump ? { action: "thirst_arrived", event: a.event, answer: value }
        : { action: "thirst_saucer", water: value, event: a.event }));
      return b;
    }));
  }

  function drawEvents(w) {
    const items = (w.events || []).map((e) => {
      const li = el("li");
      li.append(el("span", "th-when", when(new Date(e.at * 1000))), el("span", "", e.text));
      return li;
    });
    if (!items.length) items.push(el("li", "hint", t("thirst.eventsNone")));
    built.events.replaceChildren(...items);
  }

  function fillPotForm(w) {
    const f = built.forms.pot;
    if (!w.pot || f.wrap.dataset.filled === `${w.pot.id}|${LANG}`) return;
    f.wrap.dataset.filled = `${w.pot.id}|${LANG}`;  // the stored pot fills the form once, typing is never overwritten
    f.volume.value = w.pot.volume_l != null ? num(w.pot.volume_l) : "";
    f.diameter.value = w.pot.diameter_cm != null ? num(w.pot.diameter_cm) : "";
    f.height.value = w.pot.height_cm != null ? num(w.pot.height_cm) : "";
    f.step.value = w.pot.scale_step_g != null ? num(w.pot.scale_step_g, 0) : "";
    for (const [name] of POT_FLAGS) f.flags[name].setAttribute("aria-pressed", w.pot[name] ? "true" : "false");
  }

  function update() {
    const w = last;
    if (!built || !w) return;
    const hasPot = !!w.pot;
    const kind = (w.decision || {}).kind || "WAIT";
    const [pillKey, pillCls, noteCls] = KIND[kind] || KIND.WAIT;
    const pill = $("thirst-pill");
    pill.textContent = t(hasPot ? pillKey : "thirst.kind.SETUP");
    pill.className = `status ${hasPot ? pillCls : "warn"}`;
    // without a pot: the pot form, open, and nothing that needs the pot
    built.setup.hidden = hasPot;
    for (const part of [built.hero, built.over, built.chips, built.forecast, built.plan, built.check, built.pump,
      built.eventsHead, built.events]) part.hidden = !hasPot;
    const known = w.ti != null;
    built.tiValue.textContent = known ? num(Math.round(w.ti), 0) : t("thirst.tiUnknown");
    built.tiUnit.hidden = !known;
    built.hero.classList.toggle("unknown", !known);
    built.tiNote.textContent = known && w.d_l != null && w.d_trig_l
      ? t("thirst.tiLitres", { missing: litres(w.d_l), trigger: litres(w.d_trig_l) }) : "";
    built.barWrap.hidden = !known;
    const pct = (v) => `${Math.max(0, Math.min(MAX_TI, v)) / MAX_TI * 100}%`;
    if (known) {
      built.fill.style.width = pct(w.ti);
      built.fill.classList.toggle("due", w.ti >= 100);
      built.mark.style.left = pct(w.ti);
      const band = w.band || 0;
      built.band.style.left = pct(w.ti - band);
      built.band.style.width = `calc(${pct(w.ti + band)} - ${pct(w.ti - band)})`;
      built.bar.setAttribute("aria-label", t("thirst.barLabel", { ti: num(Math.round(w.ti), 0), band: num(Math.round(band), 0) }));
    }
    const over = w.overshoot;
    built.over.textContent = !hasPot ? "" : !over ? t("thirst.overshootNone")
      : over.unknown ? t("thirst.overshootUnknown", { date: when(new Date(over.at * 1000)) })
        : t("thirst.overshoot", { ml: qty(num(Math.round(over.ml), 0), "ml"), date: when(new Date(over.at * 1000)) });
    if (hasPot) drawChips(w);
    built.decision.className = `note th-decision ${noteCls}`;
    built.decision.hidden = !hasPot;  // without a pot the setup block above says it already
    built.decisionText.textContent = (w.decision || {}).text || "";
    built.why.replaceChildren(...((w.decision || {}).why || []).map((line) => el("li", "", line)));
    built.waiting.hidden = !(w.waiting_s >= 3600);
    built.waiting.textContent = w.waiting_s >= 3600 ? t("thirst.waiting", { h: hours(w.waiting_s) }) : "";
    if (hasPot) { drawForecast(w); drawPlan(w); }
    built.gate.hidden = !w.gate;
    if (w.gate) {
      built.gate.textContent = w.gate.text;
      built.gate.className = `note th-gate ${w.gate.run ? "info" : "warn"}`;
    }
    if (hasPot) checkButtons(w);
    built.arrival.hidden = !w.arrival;
    arrivalButtons(w);
    const pump = w.pump || {};
    built.pumpOn.hidden = !hasPot || !pump.on_text;
    if (pump.on_text) dsBanner(built.pumpOn, "warn", t("thirst.lead.pump"), pump.on_text);
    built.pumpFlow.textContent = pump.flow_text || "";
    built.pumpPower.hidden = !pump.power_text;
    built.pumpPower.textContent = pump.power_text || "";
    // the actions: without a pot only the pot form, open
    for (const a of built.actions.children) {
      const what = a.dataset.what;
      a.hidden = !hasPot;
      a.classList.toggle("on", open === what);
      a.setAttribute("aria-expanded", open === what ? "true" : "false");
    }
    const formOpen = hasPot ? open : "pot";
    for (const [what, form] of Object.entries(built.forms)) form.wrap.hidden = formOpen !== what;
    const flowSeconds = built.forms.flow.seconds;
    const runTime = typeof state === "object" && state && state.watering ? state.watering.settings.duration_s : null;
    if (!flowSeconds.value && runTime) flowSeconds.value = String(runTime);
    fillPotForm(w);
    if (hasPot) drawEvents(w);
  }

  function take(s) {
    const card = $("thirst");
    if (!card) return;
    const w = s && s.thirst;
    card.hidden = !w;
    if (!w) return;
    if (!built) built = build();
    last = w;
    try { update(); } catch (err) { console.error(err); }
  }

  document.addEventListener("vgrow:state", (e) => take(e.detail));
  if (typeof state === "object" && state) take(state);
})();
