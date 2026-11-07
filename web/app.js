"use strict";

const $ = (id) => document.getElementById(id);
// Pending command fields ("light.level", "cFan.level", "port.usb2") by their first part: where the outcome shows.
const FB = { light: "fb-light", cFan: "fb-cfan", dFan: "fb-dfan", port: "fb-port" };
const PENDING = { pending: ["fb.sending", ""], ok: ["pending.ok", "ok"], timeout: ["pending.timeout", "bad"], rejected: ["pending.rejected", "bad"] };
const ALARM_TILE = { temp: "t-temp", humidity: "t-hum", water_level: "t-water", water_state: "t-water", probe_temp: "t-temp", probe_humidity: "t-hum" };

// ---------- language, media, strings ----------

// Every text is in i18n.js: window.I18N = {"de": {key: text}, "en": {...}}, with {name} for a value.
// Keys are always written out in full here, never assembled, so tests/test_web_i18n.py can check each one.
const DICT = window.I18N || { de: {}, en: {} };
const LANG_KEY = "vgrow.lang";
const LOCALE = { de: "de-DE", en: "en-US" };
const HOUR = { de: "2-digit", en: "numeric" };  // 09:05 in German, 9:05 AM in US English
const LANG_NAME = { de: "lang.name.de", en: "lang.name.en" };

// Contract: localStorage "vgrow.lang", else <html data-default-lang> from the server, else the browser.
function uiLang() {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === "de" || saved === "en") return saved;
  } catch (_) { /* storage blocked */ }
  const dflt = document.documentElement.dataset.defaultLang;
  if (dflt === "de" || dflt === "en") return dflt;
  return (navigator.language || "").toLowerCase().startsWith("de") ? "de" : "en";
}
let LANG = uiLang();
document.documentElement.lang = LANG;

// A text in the page language. A key missing there falls back to German, then to the key itself.
function t(key, params) {
  const text = (DICT[LANG] || {})[key] ?? DICT.de[key] ?? key;
  return params ? text.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k] ?? "") : m)) : text;
}

const PLURALS = {};
function tn(key, n, params) {  // key.one or key.other, by the language's plural rule
  const rule = PLURALS[LANG] || (PLURALS[LANG] = new Intl.PluralRules(LOCALE[LANG]));
  return t(`${key}.${rule.select(n) === "one" ? "one" : "other"}`, { n: amount(n), ...params });
}

// A text with elements in it: a {name} without a value takes slots[name], e.g. a link or a <code>.
function fill(el, key, params, slots = {}) {
  const parts = t(key, params).split(/\{(\w+)\}/);
  el.replaceChildren(...parts.map((p, i) => (i % 2 ? slots[p] || `{${p}}` : p)).filter((p) => p !== ""));
}

// Pictures, files and the manifest are loaded by the browser itself, without the X-Lang header: their URLs
// carry the page language instead, so the server draws them in it (the demo camera, the demo photos).
function langUrl(path) { return `${path}${path.includes("?") ? "&" : "?"}lang=${LANG}`; }

const SLOTS = new WeakMap();
function applyStatic(root = document) {  // data-i18n="key" sets an element's text, data-i18n-attr="aria-label:key;title:key" its attributes
  for (const el of root.querySelectorAll("[data-i18n]")) {
    if (!SLOTS.has(el)) SLOTS.set(el, Object.fromEntries([...el.querySelectorAll("[data-slot]")].map((s) => [s.dataset.slot, s])));
    fill(el, el.dataset.i18n, null, SLOTS.get(el));
  }
  for (const el of root.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of el.dataset.i18nAttr.split(";")) {
      const at = pair.indexOf(":");
      if (at > 0) el.setAttribute(pair.slice(0, at).trim(), t(pair.slice(at + 1).trim()));
    }
  }
  if (root !== document) return;
  document.title = t("meta.title");  // the tab and the home-screen name carry the unofficial marker too
  const manifest = document.querySelector('link[rel="manifest"]');
  if (manifest) manifest.setAttribute("href", langUrl("/manifest.webmanifest"));
}

// Statuses as [text key, pill class]. "none": a value without a target to hold it against.
const TILE_STATUS = { ok: ["tiles.onTarget", "ok"], high: ["check.high", "bad"], low: ["check.low", "warn"] };
const CHECK_STATUS = { ok: ["check.ok", "ok"], low: ["check.low", "warn"], high: ["check.high", "bad"], unknown: ["check.unknown", ""],
  none: ["check.none", ""] };
const CONFIDENCE = { low: ["ai.confLow", "warn"], medium: ["ai.confMedium", ""], high: ["ai.confHigh", "ok"] };
const SEVERITY = { act: ["ai.sevAct", "bad"], watch: ["ai.sevWatch", "warn"], info: ["ai.sevInfo", "info"] };
const SPECTRUM = { seedling: "spectrum.seedling", veg: "spectrum.veg", bloom: "spectrum.bloom" };
const LEVELS = ["auto.level.manual", "auto.level.suggest", "auto.level.auto"];
const AUTO_ACTION = {  // [plain, with the probe humidity]
  humidifier_on: ["auto.action.on", "auto.action.onAt"],
  humidifier_off: ["auto.action.off", "auto.action.offAt"],
  paused_manual: ["auto.action.pausedManual"],
  automation_on: ["auto.action.automationOn"],
  automation_off: ["auto.action.automationOff"],
  failsafe_off: ["auto.action.failsafeOff"],
  stage_pause_off: ["auto.action.stageOff"],  // the pause for an unconfirmed stage switched it off
  profile_pause_off: ["auto.action.profileOff"],  // the same for an unreadable plant.json
  implausible_off: ["auto.action.implausibleOff"],  // the plausibility stop: the humidity did not rise after "on"
};

const DARK = matchMedia("(prefers-color-scheme: dark)");
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");
const FINE = matchMedia("(hover: hover) and (pointer: fine)");  // a mouse: "click again", on a phone "tap again"

// The second tap's prompt fragment, by the pointer: on a desktop "Tap again" would be the wrong word.
function again() { return t(FINE.matches ? "arm.again.mouse" : "arm.again.touch"); }

let state = null;
let hours = 24;
let history = [];
const charts = {};
const draft = { cfan: null, dfan: null };
const timers = {};
let lightUnlocked = false;
let dragging = false;

async function api(path, body) {
  const headers = { "X-Lang": LANG, ...(window.PlantWorkspace?.headers() || {}) };
  const opts = body === undefined ? { headers } : {
    method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) throw new Error(window.PlantWorkspace?.error(data.error) || data.error || `HTTP ${res.status}`);
  return data;
}

// ---------- numbers, dates, units: by the page language ----------

const NUMBER = {};
function numberFormat(digits, grouping) {
  const id = [LANG, digits, grouping].join();
  return NUMBER[id] || (NUMBER[id] = new Intl.NumberFormat(LOCALE[LANG],
    { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: grouping }));
}

function fmt(v, digits = 1) {  // a reading: fixed decimals, no thousands separator
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "–";
  return numberFormat(digits, false).format(Number(v));
}

function amount(v) { return numberFormat(0, true).format(v); }  // a count or a volume: 1.400 or 1,400

function num(v) {  // whole limits stay whole; pH 6.5 must not round to 7
  return fmt(v, Number.isInteger(v) ? 0 : 1);
}

function euros(v) {
  return new Intl.NumberFormat(LOCALE[LANG], { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(v);
}

// "79 %" in German, "79%" in US English (as the server writes them); every other unit keeps its space.
// The space does not break: a line never ends between a number and its unit.
function unitSuffix(unit) { return unit ? `${unit === "%" && LANG === "en" ? "" : " "}${unit}` : ""; }
function qty(text, unit) { return `${text}${unitSuffix(unit)}`; }

// The same for texts that come written from the server or the content files ("1.400 ml", "29 °C", "35 min").
// Longer units first, so "g/l" and "mg/l" keep their space as a whole (N2: g, g/l, µS/cm, mg/l, °dH were missing).
const UNIT_GAP = /(\d) (?=(?:%|°C|°dH|cm|ml\/l|ml|mg\/l|g\/l|g|min|kPa|mS\/cm|µS\/cm|W|h|s)(?![\p{L}\d]))/gu;
function keepUnits(text) { return String(text ?? "").replace(UNIT_GAP, "$1 "); }

// German writes dates as 22.10.2026; English names the month (Oct 22, 2026): 09/10 reads as two different days
// on the two sides of the Atlantic.
const MONTH = { de: "2-digit", en: "short" };
const DAY = { de: "2-digit", en: "numeric" };

function clock(d) { return d.toLocaleTimeString(LOCALE[LANG], { hour: HOUR[LANG], minute: "2-digit" }); }
function stamp(d) { return d.toLocaleString(LOCALE[LANG], { day: DAY[LANG], month: MONTH[LANG], hour: HOUR[LANG], minute: "2-digit" }); }
function when(d) { return d.toDateString() === new Date().toDateString() ? clock(d) : stamp(d); }
function hourOfDay(h) { return new Date(2000, 0, 1, h).toLocaleTimeString(LOCALE[LANG], { hour: "numeric" }); }  // 12 Uhr, 12 PM

function localDay(iso) {  // "2026-09-10" at local midnight; new Date(iso) would read it as UTC
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
function daysSince(iso) {  // whole calendar days, so a stage set today is "today", not "1 day"
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.max(0, Math.round((today - localDay(iso)) / 86400000));
}
function dateLong(iso) { return iso ? localDay(iso).toLocaleDateString(LOCALE[LANG], { day: DAY[LANG], month: MONTH[LANG], year: "numeric" }) : ""; }
function dateShort(iso) { return iso ? localDay(iso).toLocaleDateString(LOCALE[LANG], { day: DAY[LANG], month: MONTH[LANG] }) : ""; }

function ago(s) {
  if (s === null || s === undefined) return t("ago.never");
  if (s < 90) return t("ago.s", { n: amount(s) });
  if (s < 5400) return t("ago.min", { n: amount(Math.round(s / 60)) });
  return t("ago.h", { n: amount(Math.round(s / 3600)) });
}

// Feedback lines inside blocks that are drawn anew on every state (the plant card's hints) keep their text here,
// so a poll never wipes the answer to a tap.
const FB_KEEP = {};
const FB_REBUILT = new Set(["fb-plant-stage", "fb-plant-camera"]);

function setFb(id, text, cls = "") {
  if (FB_REBUILT.has(id)) FB_KEEP[id] = [text, cls];
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.className = "fb " + cls;
  delete el.dataset.src;
}

function keptFb(id) {  // a feedback line for a block that is being drawn anew, with the text it had
  const el = document.createElement("p");
  el.id = id;
  const [text, cls] = FB_KEEP[id] || ["", ""];
  el.className = `fb ${cls}`.trim();
  el.textContent = text;
  return el;
}

async function send(body, fbId) {
  setFb(fbId, t("fb.sending"));
  try {
    await api("/api/cmd", body);
    poll(true);
  } catch (err) {
    setFb(fbId, err.message, "bad");
  }
}

function chip(text, cls = "") {
  const el = document.createElement("span");
  el.className = `chip ${cls}`.trim();
  el.textContent = text;
  return el;
}

function stageShort(label) {  // "Early flower (stretch)" -> "Early flower"; labels in both languages keep the " ("
  return String(label || "").split(" (")[0];
}

function onMedia(mq, fn) {
  if (mq.addEventListener) mq.addEventListener("change", fn); else mq.addListener(fn);
}

// ---------- rendering ----------

let loadedVersion = null;

function setStatus(cls, text) {
  for (const [pill, label] of [["status-pill", "status-text"], ["side-pill", "side-status"]]) {
    $(pill).className = `pill ${cls}`;
    $(label).textContent = text;
  }
  const nav = $("chip-nav");
  nav.classList.remove("ok", "bad", "demo");
  nav.classList.add(cls);
  $("nav-dot").title = text;
}

function render(s) {
  // A newer page version on the server: reload once instead of running stale code.
  if (s.web_version) {
    if (loadedVersion === null) loadedVersion = s.web_version;
    else if (s.web_version !== loadedVersion) { location.reload(); return; }
  }
  const kindBefore = state && state.climate ? state.climate.water_kind : undefined;
  state = s;
  // The water chart draws a level only for a level sensor: redraw it once the sensor's kind is known or changes.
  if (history.length && (s.climate ? s.climate.water_kind : undefined) !== kindBefore) drawHistory();
  const live = s.online && s.mqtt;
  setStatus(s.demo ? "demo" : live ? "ok" : "bad", t(s.demo ? "status.demo" : live ? "status.online" : "status.offline"));
  $("demo-banner").hidden = !s.demo;
  $("error-banner").hidden = !s.error;
  $("error-banner").textContent = s.error ? t("banner.connection", { error: s.error }) : "";
  $("side-foot").textContent = s.device || "VGrow";

  renderTiles(s);
  const paused = !s.demo && !live;  // commands need the box's live connection
  if (!paused) pauseControls(false);
  renderLight(s.light || {});
  renderFan("cfan", s.cfan || {});
  renderFan("dfan", s.dfan || {});
  $("dfan-auto").hidden = !(s.dfan && s.dfan.mode !== 0 && s.dfan.mode != null);
  renderPorts(s.ports || {}, s.automation);
  if (paused) pauseControls(true, offlineText(s));
  renderPending(s.pending || {});
  renderAlerts(s);
  renderCameras(s);
  renderPlant(s.plant);
  renderVision(s.vision, s.plant);
  renderPest(s.pest);
  renderAutopilot(s);
  bandsChanged(s);
  // Hank's panel (web/hank.js, "Hank regelt") draws from this very answer: one state for the card and for Hank.
  try { document.dispatchEvent(new CustomEvent("vgrow:state", { detail: s })); } catch (err) { console.error(err); }
}

// [tile, built-in sensor metric, probe metric, decimals, unit]
const TILES = [["temp", "temp", "probe_temp", 1, "°C"], ["hum", "humidity", "probe_humidity", 0, "%"], ["vpd", "vpd", "probe_vpd", 2, "kPa"]];
const tileAtPlant = {};  // which sensor each tile shows; its sparkline follows

// ---------- the water tile, by the sensor's type (vgrow/box.py water_view) ----------
// Type 9 sends only wet or dry (measured 23.10.2026 with the owner's test, not documented by VIVOSUN): the tile says
// whether there is water in the tank and since when, never a percent. Type 10 (DWC kit) is a level in %. Only an
// unknown type or a value the type does not explain stays "not decoded yet", with the raw value in the tooltip.
function waterTile(c) {
  const tank = c.water_temp == null ? "" : t("tiles.waterTemp", { value: qty(fmt(c.water_temp), "°C") });
  const none = { show: false, empty: false, state: false, dry: false, value: "", unit: "", src: "", sub: "", pill: "", title: "" };
  if (c.water_state === "wet" || c.water_state === "dry") {
    const dry = c.water_state === "dry";
    const time = c.water_since != null ? when(new Date(c.water_since * 1000)) : "";
    const title = dry ? (time ? t("tiles.tankDrySince", { time }) : t("tiles.tankDry"))
      : (time ? t("tiles.tankWetSince", { time }) : t("tiles.tankWetLong"));
    return { ...none, show: true, state: true, dry, value: t(dry ? "tiles.tankDry" : "tiles.tankWet"), src: t("tiles.tankSensor"),
      sub: time ? t("tiles.tankSince", { time }) : "", pill: dry ? t("tiles.tankRefill") : "", title };
  }
  if (c.water_level != null) return { ...none, show: true, value: fmt(c.water_level, 0), unit: "%", sub: tank };
  if (c.water_raw != null) {
    return { ...none, show: true, empty: true, value: t("tiles.waterNa"),
      sub: [t("tiles.waterUndecoded"), tank].filter(Boolean).join("\n"), title: t("tiles.waterRaw", { raw: c.water_raw }) };
  }
  return none;
}
// ---------- end of the water tile ----------

function renderTiles(s) {
  const c = s.climate || {};
  const checks = (s.plant && s.plant.checks) || [];
  let moved = false;
  for (const [key, boxKey, probeKey, d, unit] of TILES) {
    const probe = c[probeKey], box = c[boxKey];
    const atPlant = probe != null;  // the probe hangs at the plant: its air is what the targets mean
    if (tileAtPlant[key] !== atPlant) { tileAtPlant[key] = atPlant; moved = true; }
    $(`t-${key}`).textContent = fmt(atPlant ? probe : box, d);
    $(`src-${key}`).textContent = atPlant ? t("tiles.atPlant") : box != null ? t("tiles.inBox") : "";
    $(`p-${key}`).textContent = atPlant && box != null ? t("tiles.box", { value: qty(fmt(box, d), unit) }) : "";
    const row = atPlant ? checks.find((r) => r.metric === probeKey && TILE_STATUS[r.status]) : null;
    const pill = $(`st-${key}`);
    pill.hidden = !row;
    if (row) {
      const [text, cls] = TILE_STATUS[row.status];
      pill.textContent = t(text);
      pill.className = `pill sm ${cls}`;
    }
  }
  if (moved && history.length) drawSparks();

  const w = waterTile(c);
  $("water-tile").hidden = !w.show;
  $("water-tile").classList.toggle("empty", w.empty);
  $("water-tile").classList.toggle("state", w.state);
  $("water-tile").classList.toggle("dry", w.dry);
  $("water-tile").title = w.title;
  $("water-tile").querySelector(".src").textContent = w.src;
  document.querySelector(".tiles").classList.toggle("three", !w.show);
  $("t-water").textContent = w.value;
  $("t-water-u").textContent = w.unit;
  $("t-wtemp").textContent = w.sub;
  $("st-water").hidden = !w.pill;
  $("st-water").textContent = w.pill;

  // the Ecowitt sensor (vgrow/soil.py): the sensor's own percent, a raw value until it is set to the owner's soil
  const soil = s.soil;
  $("soil-tile").hidden = !soil;
  if (soil) {
    $("t-soil").textContent = soil.percent == null ? "–" : fmt(soil.percent, 0);
    $("st-soil").hidden = soil.fresh;
    $("p-soil").textContent = soil.voltage == null ? "" : t("tiles.soilBattery", { value: qty(fmt(soil.voltage), "V") });
  }

  const stale =!s.demo && (s.sensors_age == null || s.sensors_age > 300);
  $("age").replaceChildren(chip(t("tiles.measured", { ago: ago(s.sensors_age) }), stale ? "warn" : ""),
    chip(t("tiles.device", { ago: ago(s.shadow_age) })));

  for (const tile of document.querySelectorAll(".tile")) tile.classList.remove("alarm");
  for (const rule of s.alerts || []) {
    if (rule.active && ALARM_TILE[rule.metric]) $(ALARM_TILE[rule.metric]).closest(".tile").classList.add("alarm");
  }
}

function fillList(id, items, cls) {
  $(id).replaceChildren(...(items || []).map((item) => {
    const li = document.createElement("li");
    if (typeof item === "string") li.textContent = keepUnits(item);
    else { li.textContent = `${item.issue} (${item.where})`; li.className = item.severity; }
    if (cls) li.classList.add(cls);
    return li;
  }));
}

function fillIssues(items) {
  const rank = { act: 0, watch: 1, info: 2 };
  const sorted = (items || []).slice().sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3));
  $("ai-issues").replaceChildren(...sorted.map((item) => {
    const li = document.createElement("li");
    const [label, cls] = SEVERITY[item.severity] || SEVERITY.info;
    const pill = document.createElement("span");
    pill.className = `pill sm ${cls}`;
    pill.textContent = t(label);
    const text = document.createElement("span");
    text.textContent = item.where ? `${item.issue} (${item.where})` : item.issue;
    li.append(pill, text);
    return li;
  }));
}

function renderVision(v, p) {
  const show = !!(v && p && p.configured);
  $("plant-ai").hidden = !show;
  if (!show) return;
  const last = v.latest;
  const r = last && last.result;
  const run = v.running_s;
  $("ai-run").disabled = !!v.busy;
  const sources = (state?.cameras || []).filter((camera) => camera.configured && !camera.hidden);
  const picker = $("ai-camera");
  const previousCamera = picker.value;
  const usable = sources.filter((camera) => camera.reachable !== false);
  const cameraLabel = (camera) => usable.includes(camera) ? camera.name || camera.id : t("ai.cameraOffline", { name: camera.name || camera.id });
  const pickerKey = JSON.stringify(sources.map((camera) => [camera.id, cameraLabel(camera)]));
  if (pickerKey !== picker.dataset.sources) {
    picker.replaceChildren(...sources.map((camera) => {
      const option = document.createElement("option"); option.value = camera.id; option.textContent = cameraLabel(camera); option.disabled = !usable.includes(camera); return option;
    }));
    picker.dataset.sources = pickerKey;
    picker.value = usable.some((camera) => camera.id === "usb") ? "usb" : usable.some((camera) => camera.id === previousCamera) ? previousCamera : usable[0]?.id || "";  // HD whenever it delivers
  }
  picker.disabled = !!v.busy;
  $("ai-run").textContent = !v.busy ? t("ai.run") : run == null ? t("ai.busy")
    : t("ai.busyFor", { time: run < 60 ? `${run} s` : `${Math.floor(run / 60)} min ${String(run % 60).padStart(2, "0")} s` });
  for (const [id, items] of [["ai-signs", r && r.visible_signs], ["ai-issues", r && r.issues], ["ai-steps", r && r.next_steps]]) {
    $(`${id}-h`).hidden = !(items && items.length);
  }
  if (!r) {
    $("ai-stage").textContent = v.error ? t("ai.lastTry", { error: v.error }) : t("ai.none", { time: hourOfDay(v.photo_hour ?? 12) });
    $("ai-conf").textContent = ""; $("ai-conf").hidden = true;
    $("ai-when").replaceChildren();
    $("ai-lang").hidden = true;
    $("ai-photo-link").hidden = true;
    fillList("ai-signs", []); fillIssues([]); fillList("ai-steps", []);
    $("ai-na").textContent = "";
    $("ai-take").hidden = true;
    return;
  }
  const [ckey, ccls] = CONFIDENCE[r.confidence] || ["", ""];
  $("ai-conf").textContent = ckey ? t(ckey) : ""; $("ai-conf").className = `pill sm ${ccls}`; $("ai-conf").hidden = !ckey;
  const meta = [stamp(new Date(last.photo_taken_at || last.date))];
  if (last.local !== false) meta.push(t("ai.local"), euros(0));
  if (last.seconds != null) meta.push(t("dur.min", { n: amount(Math.max(1, Math.round(last.seconds / 60))) }));
  if (last.model) meta.push(last.model);
  if (last.camera_source) meta.push(last.camera_source.name, last.camera_source.resolution || "");
  else meta.push(t("ai.sourceUnknown"));
  $("ai-when").replaceChildren(...meta.map((text) => chip(text)));
  // A reading keeps the language it was made in: say so instead of mixing languages silently.
  const other = last.lang && last.lang !== LANG ? LANG_NAME[last.lang] : null;
  $("ai-lang").hidden = !other;
  $("ai-lang").textContent = other ? t("ai.langNote", { language: t(other) }) : "";
  // Stage names come localized from the server; only the model's "unclear" has no stage of its own.
  const named = (p.stages || []).find((s) => s.key === r.stage_guess);
  const guess = named ? named.label : r.stage_guess === "unclear" ? t("stage.unclear") : r.stage_guess;
  $("ai-stage").textContent = keepUnits(t("ai.stage", { stage: guess, reason: r.stage_reason || "" }).trim());
  fillList("ai-signs", r.visible_signs);
  fillIssues(r.issues);
  fillList("ai-steps", r.next_steps);
  $("ai-na").textContent = r.not_assessable && r.not_assessable.length ? t("ai.na", { list: r.not_assessable.join("; ") }) : "";
  // The language is part of the URL, so a switch loads the photo again (the demo draws its caption).
  const photo = last.photo ? langUrl(`/photos/${last.photo}`) : "";
  if ($("ai-photo").getAttribute("src") !== photo) $("ai-photo").src = photo;
  $("ai-photo-link").href = photo; $("ai-photo-link").hidden = !photo;
  // Only the camera's run of daily photos is offered, never one reading (vision.stage_suggestion), and with two taps
  // like every stage confirmation: it lets the automation switch again. "Ausführen" stays the card's one primary.
  const sug = v.stage_suggest;
  const take = $("ai-take");
  take.hidden = !(sug && sug.key === r.stage_guess);
  if (!take.hidden) {
    stageButton(take, "camera", sug, "fb-ai-take");
    take.classList.remove("primary");
  }
}

// The pest watch (vgrow/pest_watch.py): an open finding waits for the owner's look; a confirmed one stays for two days.
const PEST_SIGNS = {
  white_powder: "pest.sign.white_powder", speckles: "pest.sign.speckles", webbing: "pest.sign.webbing",
  insects: "pest.sign.insects", residue: "pest.sign.residue", curled: "pest.sign.curled", bud_mold: "pest.sign.bud_mold",
  chewed: "pest.sign.chewed", patches: "pest.sign.patches", unusual: "pest.sign.unusual",
};
let pestDrawn = "";

function renderPest(w) {
  const shown = w && (w.open || w.confirmed);
  $("pest-card").hidden = !shown;
  if (!shown) return;
  const open = !!w.open;
  $("pest-state").textContent = t(open ? "pest.stateOpen" : "pest.stateConfirmed");
  $("pest-state").className = `pill sm ${open ? "bad" : "warn"}`;
  $("pest-when").textContent = shown.date ? when(new Date(shown.date)) : "";
  $("pest-signs").replaceChildren(...(shown.flags || []).map((f) => {
    const li = document.createElement("li");
    li.textContent = `${PEST_SIGNS[f.key] ? t(PEST_SIGNS[f.key]) : f.key}${f.seen === "yes" ? "" : t("pest.maybe")}${f.where ? `: ${f.where}` : ""}`;
    return li;
  }));
  $("pest-hint").textContent = t(open ? "pest.lookHint" : "pest.confirmedHint");
  $("pest-buttons").hidden = !open;
  renderPestWhat(open ? null : shown);
  // "wird gesendet" goes once the answer changed the card; an error from a refused send stays
  const drawn = `${shown.id}|${(shown.finding || {}).status}|${(shown.finding || {}).identified || ""}`;
  if (drawn !== pestDrawn) { pestDrawn = drawn; setFb("fb-pest", ""); }
  $("pest-card").dataset.check = shown.id;
  const photo = shown.photo ? langUrl(`/photos/${shown.photo}`) : "";
  if ($("pest-photo").getAttribute("src") !== photo) $("pest-photo").src = photo;
  $("pest-photo-link").href = photo; $("pest-photo-link").hidden = !photo;
  const s = w.stats || {};
  $("pest-stats").textContent = s.flagged ? t("pest.stats", { flagged: s.flagged, confirmed: s.confirmed || 0, dismissed: s.dismissed || 0 }) : "";
}

// After the owner's "Da ist was": what was it? The cards the confirmed signs point to (pest_watch.SIGN_CARDS, code,
// never the model) and "something else"; the pick shows that card's own "do now" steps. Tapping the pick again
// chooses anew.
const PEST_OTHER = "other";
function renderPestWhat(c) {
  $("pest-what").hidden = !c;
  if (!c) return;
  const cands = c.candidates || [];
  const picked = (c.finding && c.finding.identified) || "";
  $("pest-cands").replaceChildren(...[...cands.map((x) => [x.card, x.label]), [PEST_OTHER, t("pest.somethingElse")]]
    .map(([card, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "toggle";
      b.textContent = label;
      b.setAttribute("aria-pressed", String(card === picked));
      b.onclick = () => send({ action: "pest_identify", id: c.id, card: card === picked ? "" : card }, "fb-pest");
      return b;
    }));
  const chosen = cands.find((x) => x.card === picked);
  $("pest-steps").hidden = !picked;
  if (!picked) return;
  const steps = (chosen && chosen.do_now) || [];
  $("pest-steps-h").textContent = !chosen ? t("pest.otherHint")
    : steps.length ? t("pest.stepsTitle", { label: chosen.label }) : t("pest.noSteps", { label: chosen.label });
  $("pest-steps-list").replaceChildren(...steps.map((text) => {
    const li = document.createElement("li");
    li.textContent = text;
    return li;
  }));
  const [folder, key] = chosen ? chosen.card.split("/") : [];
  $("pest-card-link").href = chosen ? `#wiki/${encodeURIComponent(folder)}/${encodeURIComponent(key)}` : "#wiki/topic/pests";
  $("pest-card-link").textContent = t(chosen ? "pest.fullCard" : "pest.allPests");
}

// ---------- plant ----------

// Stylised plant drawings, one per stage (viewBox 100 x 120, pot at the bottom). Own vector art.
function leafSvg(x, y, rot, s, n, color) {
  const spread = n <= 3 ? 50 : n === 5 ? 72 : 88;
  let out = `<g transform="translate(${x} ${y}) rotate(${rot}) scale(${s})">`;
  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0 : (i / (n - 1)) * 2 - 1;
    const len = 18 * (1 - 0.45 * Math.abs(t)), w = len * 0.2;
    out += `<path transform="rotate(${(t * spread).toFixed(1)})" d="M0 0Q${w.toFixed(1)} ${(-len * 0.5).toFixed(1)} 0 ${(-len).toFixed(1)}Q${(-w).toFixed(1)} ${(-len * 0.5).toFixed(1)} 0 0Z" fill="${color}"/>`;
  }
  return out + "</g>";
}
function budSvg(x, y, r, bud, pistil) {
  let out = `<g><circle cx="${x}" cy="${y}" r="${r}" fill="${bud}"/><circle cx="${x - r * 0.55}" cy="${y + r * 0.45}" r="${r * 0.7}" fill="${bud}"/>` +
    `<circle cx="${x + r * 0.55}" cy="${y + r * 0.45}" r="${r * 0.7}" fill="${bud}"/><circle cx="${x}" cy="${y - r * 0.7}" r="${r * 0.6}" fill="${bud}"/></g>`;
  out += `<g class="pistil" stroke="${pistil}" stroke-width="0.9" stroke-linecap="round" fill="none">`;
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + 0.4, x1 = x + Math.cos(a) * r * 0.6, y1 = y + Math.sin(a) * r * 0.6;
    out += `<path d="M${x1.toFixed(1)} ${y1.toFixed(1)}l${(Math.cos(a) * 2.6).toFixed(1)} ${(Math.sin(a) * 2.6 - 1).toFixed(1)}"/>`;
  }
  return out + "</g>";
}
const PLANT_ART = {};
function plantSvg(key) {
  if (PLANT_ART[key]) return PLANT_ART[key];
  const L = "var(--leaf)", D = "var(--leaf-dark)", G = "var(--leaf-light)";
  const pot = `<path d="M33 104h34l-4 14H37z" fill="var(--pot)"/><rect x="32" y="101" width="36" height="5" rx="2.5" fill="var(--soil)"/>`;
  const stem = (top) => `<path d="M50 102C50 ${(102 + top) / 2} 50.6 ${top + 8} 50 ${top}" stroke="${D}" stroke-width="2" fill="none" stroke-linecap="round"/>`;
  const nodes = (ys, s0, n) => ys.map((y, i) => {
    const s = s0 * (1 - i * 0.13);
    return leafSvg(50, y, -62, s, n, i % 2 ? L : D) + leafSvg(50, y, 62, s, n, i % 2 ? D : L);
  }).join("");
  let body = "";
  if (key === "seedling") {
    body = stem(84) + `<ellipse cx="43" cy="84" rx="7" ry="3.6" transform="rotate(-18 43 84)" fill="${G}"/>` +
      `<ellipse cx="57" cy="84" rx="7" ry="3.6" transform="rotate(18 57 84)" fill="${G}"/>` + leafSvg(50, 82, 0, 0.45, 3, L);
  } else if (key === "veg") {
    body = stem(52) + nodes([90, 77, 64], 1.0, 5) + leafSvg(50, 54, 0, 0.75, 5, G);
  } else if (key === "early_flower") {
    body = stem(38) + nodes([92, 80, 68, 56], 1.05, 7) + leafSvg(50, 44, 0, 0.6, 5, G) +
      budSvg(50, 39, 3.2, "var(--bud)", "var(--pistil)") + budSvg(37, 60, 2, "var(--bud)", "var(--pistil)") + budSvg(63, 60, 2, "var(--bud)", "var(--pistil)");
  } else if (key === "mid_flower") {
    body = stem(34) + nodes([92, 80, 68, 56], 1.05, 7) +
      budSvg(50, 34, 6, "var(--bud)", "var(--pistil)") + budSvg(34, 58, 3.6, "var(--bud)", "var(--pistil)") +
      budSvg(66, 58, 3.6, "var(--bud)", "var(--pistil)") + budSvg(36, 72, 2.8, "var(--bud)", "var(--pistil)") + budSvg(64, 72, 2.8, "var(--bud)", "var(--pistil)");
  } else if (key === "late_flower") {
    body = stem(32) + nodes([92, 80, 68, 56], 1.0, 7) +
      budSvg(50, 32, 8, "var(--bud)", "var(--pistil-ripe)") + budSvg(33, 57, 5, "var(--bud)", "var(--pistil-ripe)") +
      budSvg(67, 57, 5, "var(--bud)", "var(--pistil-ripe)") + budSvg(35, 72, 3.8, "var(--bud)", "var(--pistil-ripe)") + budSvg(65, 72, 3.8, "var(--bud)", "var(--pistil-ripe)");
  } else {
    body = stem(32) + leafSvg(50, 92, -62, 1.0, 7, "var(--amber)") + leafSvg(50, 92, 62, 1.0, 7, "var(--amber)") + nodes([80, 68, 56], 0.92, 7) +
      budSvg(50, 32, 8.5, "var(--amber)", "var(--pistil-ripe)") + budSvg(33, 57, 5.4, "var(--amber)", "var(--pistil-ripe)") +
      budSvg(67, 57, 5.4, "var(--amber)", "var(--pistil-ripe)") + budSvg(35, 72, 4, "var(--amber)", "var(--pistil-ripe)") + budSvg(65, 72, 4, "var(--amber)", "var(--pistil-ripe)");
  }
  // Cropped to the plant (x 24..76, y 18..120) so the drawing fills its column.
  PLANT_ART[key] = `<svg viewBox="24 18 52 102" role="img" aria-hidden="true"><g class="sway">${body}</g>${pot}</svg>`;
  return PLANT_ART[key];
}

const ICON = {
  warn: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 1.5 15 14H1z" opacity=".25"/><path d="M8 5v4.5M8 11.6v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  info: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="7" opacity=".22"/><path d="M8 7v4.5M8 4.6v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  ok: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="7" opacity=".22"/><path d="M4.9 8.3 7 10.4l4.1-4.6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};
const GAUGE_SCALE = { water_ph: [4, 9], water_ec: [0, 4], probe_temp: [10, 35], temp: [10, 35], probe_humidity: [20, 100], humidity: [20, 100],
  probe_vpd: [0, 2.4], vpd: [0, 2.4], light_percent: [0, 100] };
let photosLoaded = 0;

// Decimals of a reading by its unit, the same on tiles, gauges, chart chips and logs: percent whole,
// kPa with two, EC with up to two (1,6 or 1,45), everything else with one.
function readingText(v, unit) {
  if (unit === "mS/cm") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "–";
    return new Intl.NumberFormat(LOCALE[LANG], { minimumFractionDigits: 1, maximumFractionDigits: 2, useGrouping: false }).format(v);
  }
  return fmt(v, unit === "%" ? 0 : unit === "kPa" ? 2 : 1);
}

// A place on the bar as a CSS left, kept inside the track so nothing pokes out past either end.
function barLeft(pct, halfWidthPx) { return `clamp(${halfWidthPx}px, ${pct}%, calc(100% - ${halfWidthPx}px))`; }

function gaugeEl(r) {
  const [a, b] = GAUGE_SCALE[r.metric] || [0, 100];
  const pct = (v) => Math.max(0, Math.min(100, ((v - a) / (b - a)) * 100));
  const el = document.createElement("div");
  el.className = "gauge";
  const [key, cls] = CHECK_STATUS[r.status] || ["", ""];
  const head = document.createElement("div");
  head.className = "g-head";
  const lbl = document.createElement("span"); lbl.className = "lbl"; lbl.textContent = r.label;
  const val = document.createElement("span"); val.className = "val";
  val.textContent = r.actual == null ? "–" : readingText(r.actual, r.unit);
  const unit = document.createElement("small"); unit.textContent = r.unit || ""; val.appendChild(unit);
  const pill = document.createElement("span"); pill.className = `pill sm ${cls}`; pill.textContent = key ? t(key) : "";
  pill.hidden = !key;
  head.append(lbl, val, pill);
  const bar = document.createElement("div"); bar.className = "g-bar";
  if (r.min != null && r.min === r.max) {  // one value, not a range (the light preset): a tick, not a sliver
    const tick = document.createElement("span"); tick.className = "g-tick"; tick.style.left = barLeft(pct(r.min), 1.5);
    bar.appendChild(tick);
  } else if (r.min != null || r.max != null) {
    const band = document.createElement("span"); band.className = "g-band";
    const lo = pct(r.min ?? a), w = Math.max(pct(r.max ?? b) - lo, 1.2);
    band.style.left = `${Math.min(lo, 100 - w)}%`; band.style.width = `${w}%`;
    bar.appendChild(band);
  }
  if (r.actual != null) {
    const mark = document.createElement("span"); mark.className = `g-mark ${r.status}`; mark.style.left = barLeft(pct(r.actual), 2);
    bar.appendChild(mark);
  }
  const foot = document.createElement("div"); foot.className = "g-foot";
  const f1 = document.createElement("span"); f1.textContent = num(a);  // scale ends: 4 and 9, not 4,0 and 9,0
  const f2 = document.createElement("span"); f2.textContent = rangeText(r);
  const f3 = document.createElement("span"); f3.textContent = num(b);
  foot.append(f1, f2, f3);
  el.append(head, bar, foot);
  return el;
}

function rangeText(r) {
  const unit = unitSuffix(r.unit);
  const d = (v) => fmt(v, r.unit === "kPa" ? 1 : Number.isInteger(v) ? 0 : 1);  // pH 6.5 must not round to 7
  if (r.min != null && r.max != null) {
    return r.min === r.max ? t("check.target", { min: d(r.min), unit }) : t("check.targetRange", { min: d(r.min), max: d(r.max), unit });
  }
  if (r.min != null) return t("check.targetMin", { min: d(r.min), unit });
  if (r.max != null) return t("check.targetMax", { max: d(r.max), unit });
  return "";
}

let stagePick = null;  // a stage tapped on the track, waiting for the set button

function renderStageTrack(p) {
  const track = $("stage-track");
  const keys = (p.stages || []).map((s) => s.key);
  const cur = p.stage ? keys.indexOf(p.stage.key) : -1;
  const sig = `${keys.join()}|${cur}|${(p.stages || []).map((s) => s.label).join()}`;
  if (track.dataset.sig !== sig) {
    track.dataset.sig = sig;
    track.replaceChildren();
    const fill = document.createElement("span"); fill.className = "fill"; track.appendChild(fill);
    p.stages.forEach((s, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "st-step " + (i < cur ? "done" : i === cur ? "current" : "next");
      b.dataset.key = s.key;
      b.title = i === cur ? t("stage.pickNow", { stage: s.label }) : t("stage.pick", { stage: s.label });
      b.innerHTML = plantSvg(s.key);
      const dot = document.createElement("span"); dot.className = "dot";
      const lbl = document.createElement("span"); lbl.className = "lbl"; lbl.textContent = stageShort(s.label);
      b.append(dot, lbl);
      b.onclick = () => askStage(s.key);
      track.appendChild(b);
    });
    requestAnimationFrame(() => { fill.style.width = cur > 0 ? `${(cur / (keys.length - 1)) * 84}%` : "0"; });
    if (stagePick && !keys.includes(stagePick)) stagePick = null;
  }
  for (const b of track.querySelectorAll(".st-step")) b.classList.toggle("pick", b.dataset.key === stagePick);
  $("stage-confirm").hidden = !stagePick;
}

// A tap on the track asks inline first; the stage changes only after the set button.
function askStage(key) {
  const p = state && state.plant;
  const st = p && (p.stages || []).find((s) => s.key === key);
  if (!st) return;
  const isCur = !!(p.stage && p.stage.key === key);
  const settled = isCur && p.stage.set_by === "manual";
  stagePick = key;
  const stage = st.label;
  $("stage-confirm-t").textContent = settled ? t("stage.current", { stage })
    : isCur ? t("stage.confirmEstimate", { stage }) : t("stage.confirm", { stage });
  $("stage-confirm-yes").hidden = settled;
  $("stage-confirm-yes").textContent = isCur ? t("stage.confirmBtn") : t("stage.set");
  $("stage-confirm-no").textContent = settled ? t("stage.close") : t("stage.cancel");
  renderStageTrack(p);
  (settled ? $("stage-confirm-no") : $("stage-confirm-yes")).focus({ preventScroll: true });
}

function closeStageConfirm() {
  stagePick = null;
  $("stage-confirm").hidden = true;
  for (const b of $("stage-track").querySelectorAll(".st-step")) b.classList.remove("pick");
}

const stageArm = { auto: 0, plant: 0, feed: 0, pilot: 0, camera: 0 };  // per button: until then a tap confirms, before that it only asks
let stageBusy = false;  // a confirm is on its way: every stage button waits for its answer, polls or not

// The suggested stage (an estimate, or the days since the start), confirmed with the existing plant_stage action.
// Two taps, as for switching the automation on: a confirmed stage lets the automation switch the humidifier again,
// and the feeding planner compute amounts. onOk takes the success text where the button's own line would vanish.
function stageButton(btn, where, stage, fbId, onOk) {
  const armed = !stageBusy && Date.now() < stageArm[where];
  btn.type = "button";
  btn.dataset.stageConfirm = where;
  btn.className = `btn primary sm stage-btn${armed ? " arm" : ""}`;
  btn.disabled = stageBusy;  // every render runs this: while the request runs it stays off
  btn.textContent = armed ? t("stage.confirmArm", { stage: stage.label, again: again() })
    : t("stage.confirmSuggested", { stage: stage.label });
  btn.onclick = () => {
    if (stageBusy) return;
    if (Date.now() >= stageArm[where]) {
      stageArm[where] = Date.now() + 4000;
      const had = document.activeElement === btn;
      render(state);
      // The plant card draws its hints anew: focus stays on the button for the second tap.
      const next = had && document.querySelector(`[data-stage-confirm="${where}"]`);
      if (next && next !== document.activeElement) next.focus({ preventScroll: true });
      setTimeout(() => { if (state) render(state); }, 4100);
      return;
    }
    stageArm[where] = 0;
    stageBusy = true;
    btn.disabled = true;  // until the answer, whatever renders in between
    // Not send(): no box confirms a stage, so its "sending" would stand forever. The server's answer is the outcome.
    setFb(fbId, t("fb.sending"));
    api("/api/cmd", { action: "plant_stage", key: stage.key }).then(() => {
      stageBusy = false;
      const text = t("stage.confirmedOk", { stage: stage.label });
      if (onOk) { setFb(fbId, ""); onOk(text); } else setFb(fbId, text, "ok");
      poll(true);
    }, (err) => {
      stageBusy = false;
      setFb(fbId, err.message, "bad");
      if (state) render(state);
    });
  };
  return btn;
}

let stageDone = null;  // the plant card's answer to a confirm: its hint is gone with the estimate, the answer stays

function stageConfirmedHint(text) {
  stageDone = { text, until: Date.now() + 6000 };
  setTimeout(() => { if (state) render(state); }, 6100);
}

// The first screen's line: day, stage (marked while only estimated) and the open tasks, the stage to confirm included.
function renderStatusLine(p, open) {
  const el = $("plant-status");
  el.hidden = !p;
  if (!p) return;
  const stage = !p.stage ? t("stage.none") : p.stage.set_by === "manual" ? stageShort(p.stage.label)
    : t("stage.estimated", { stage: stageShort(p.stage.label) });
  const parts = [p.day != null ? t("statusline.day", { n: p.day }) : "", stage].filter(Boolean);
  const todo = open ? tn("statusline.todo", open) : t("statusline.none");
  $("sl-text").textContent = parts.join(" · ");
  $("sl-todo").textContent = todo;
  $("sl-todo").className = `pill sm ${open ? "warn" : "ok"}`;
  el.setAttribute("aria-label", [...parts, todo].join(" · "));
  const key = p.stage ? p.stage.key : "";
  const art = $("sl-art");
  if (art.dataset.key !== key) { art.dataset.key = key; art.innerHTML = key ? plantSvg(key) : ""; }
}

function plantTodos(p, s) {
  const checks = p.checks || [];
  const box = s?.autopilot?.boxes?.[0];
  const fan = box?.devices?.find((d) => d.id === "dfan");
  const handled = (r) => pilotRuns(s, checkDevice(box, r.metric));
  const hot = checks.some((r) => r.metric === "probe_temp" && r.status === "high");
  const reductionHeld = fan && ["ap.hold.vpd_heat", "ap.hold.vpd_no_room", "ap.hold.vpd_humid"].includes(fan.reason_key);
  const dry = (r) => r.metric === "probe_humidity" && r.status === "low" || r.metric === "probe_vpd" && r.status === "high";
  const atLimit = box?.mode === "auto" && fan?.level === "auto" &&
    ["ready", "waiting", "pending"].includes(fan.control_state) && Number.isFinite(fan.value) &&
    Number.isFinite(box.limits?.dfan?.[1]) && fan.value >= box.limits.dfan[1];
  const out = [];
  for (const r of checks) {
    if (!r.advice) continue;
    if ((hot || reductionHeld) && dry(r)) continue;
    if (hot && atLimit && r.metric === "probe_temp") out.push(t("plant.priority.exhaustLimit", { step: fan.value }));
    else if (!handled(r)) out.push(r.advice);
  }
  if (checks.some(dry) && (hot || reductionHeld)) {
    out.push(hot ? t("plant.priority.coolingFirst") : t("plant.priority.reductionHeld", { reason: fan.status }));
  }
  return [...new Set(out)];
}

// The device that works on a check row: the exhaust for the temperature, the humidifier for humidity and VPD (the
// autopilot's exhaust holds for those say so on the exhaust's own card), the light for the light. Anything else: none.
function checkDevice(box, metric) {
  if (metric === "light_percent") return box?.devices?.find((d) => d.id === "light") || null;
  if (/humidity|vpd/.test(metric)) return box?.humidifier || null;
  return /temp/.test(metric) ? box?.devices?.find((d) => d.id === "dfan") || null : null;
}

// Whether the device does the card's advice by itself: the autopilot's own, as vgrow/hank/pilot_report.py regulated() says
// it (Automatik, the device on "auto", no VIVOSUN plan or automation, not paused by a hand), or the humidifier rule while
// it is on and set up. Then the advice is no task for the owner (07.11.2026: "it's on auto, he should do it himself").
function pilotRuns(s, device) {
  const box = s?.autopilot?.boxes?.[0];
  if (!box || !device) return false;
  if (device === box.humidifier) return Boolean(device.enabled) && !device.paused_until && device.control_state !== "needs_setup";
  return box.mode === "auto" && !box.locked_out && !box.forced_off && device.level === "auto" && !device.app_mode && !device.paused_until;
}

function renderPlant(p) {
  const show = !!(p && p.configured);
  $("card-plant").hidden = !show;
  if (!show) { renderStatusLine(null, 0); return; }
  $("plant-name").textContent = p.plant.strain || t("plant.title");
  $("plant-stage").textContent = p.stage ? p.stage.label : t("stage.none");
  $("plant-day").textContent = p.day ?? "–";
  $("plant-start").textContent = p.plant.start_date ? t("plant.since", { date: dateLong(p.plant.start_date) }) : "";
  $("plant-h").textContent = p.height ? `${p.height.estimated ? "≈ " : ""}${fmt(p.height.cm, 0)}` : "–";
  const hAge = p.height && p.height.date ? daysSince(p.height.date) : null;
  $("plant-h-date").textContent = !p.height ? t("plant.heightNone")
    : t("plant.heightMeasured", { date: dateShort(p.height.date) }) + (hAge ? " · " + tn("plant.heightAgo", hAge) : "");
  if (p.height?.estimated) $("plant-h-date").textContent = t(p.height.method === "camera_strip" ? "plant.heightStripDate" : p.height.camera === "growcam" ? "plant.heightGrowCamDate" : "plant.heightEstimateDate", { date: dateShort(p.height.date) });
  // The height belongs to the day (owner, 23.10.2026): older than two days, the card asks for today's, as Hank does.
  $("plant-height-due").textContent = !p.height ? t("plant.heightDueNone")
    : hAge != null && hAge > HEIGHT_DUE_DAYS ? tn("plant.heightDue", hAge) : "";
  const since = p.stage && p.stage.since ? daysSince(p.stage.since) : null;
  const manualEntry = p.stage?.set_by === "manual";
  $("plant-record-kind").textContent = t(manualEntry ? "plant.stageEntry" : "plant.inStage");
  // A stage estimated on the camera's first image day is "spätestens seit", not "seit 1 Tag" (owner: "Stage ist
  // bestimmt nicht ein Tag").
  const atLatest = p.stage && p.stage.set_by !== "manual" && p.bloom && (p.bloom.latest || p.bloom.estimated)
    && p.stage.since && p.stage.since === p.bloom.date;
  $("plant-since").textContent = since == null ? "–" : manualEntry ? since === 0 ? t("plant.today") : tn("plant.entryAgo", since) : atLatest ? t("plant.atLatest", { date: dateShort(p.stage.since) })
    : since === 0 ? t("plant.today") : tn("plant.days", since);
  $("plant-since-by").textContent = !p.stage ? "" : t(p.stage.set_by === "manual" ? "plant.entryDateNote"
    : p.stage.set_by === "estimate" ? "plant.setByEstimate" : "plant.setByDays");
  renderStageTrack(p);

  const hints = [];
  if (p.problem) hints.push(["warn", t("plant.unreadable", { error: p.problem })]);  // the last readable plant.json
  if (stageDone && Date.now() < stageDone.until) hints.push(["ok", stageDone.text]);  // the answer to a confirm
  // A stage the owner has not set (estimate or day count): its note, or a plain sentence, with the confirm button
  // and its own feedback line right under it.
  if (p.stage && p.stage.set_by !== "manual") {
    hints.push(["info", p.stage.estimate_note || t("plant.stageUnconfirmed", { stage: p.stage.label }), p.stage]);
  }
  if (p.plant.confirm) hints.push(["info", p.plant.confirm]);
  // The camera's stage after three daily photos in a row, never backwards (owner, 01.11.2026): its own button
  const sug = state && state.vision && state.vision.stage_suggest;
  if (sug) hints.push(["info", t("plant.cameraStage", { stage: sug.label, days: sug.days }), sug, "camera"]);
  for (const w of (p.strain && p.strain.warnings) || []) hints.push(["warn", w]);
  const active = document.activeElement;
  const refocusOn = active && active.dataset ? active.dataset.stageConfirm : null;
  $("plant-hints").replaceChildren(...hints.map(([kind, text, stage, where = "plant"]) => {
    const li = document.createElement("li"); li.className = kind; li.innerHTML = ICON[kind];
    const body = document.createElement("div"); body.className = "hint-body";
    const span = document.createElement("span"); span.textContent = keepUnits(text); body.appendChild(span);
    if (stage) {
      const fbId = where === "camera" ? "fb-plant-camera" : "fb-plant-stage";
      body.appendChild(stageButton(document.createElement("button"), where, stage, fbId, stageConfirmedHint));
      body.appendChild(keptFb(fbId));
    }
    li.appendChild(body); return li;
  }));
  const focusBack = (refocusOn === "plant" || refocusOn === "camera")
    && document.querySelector(`[data-stage-confirm="${refocusOn}"]`);
  if (focusBack) focusBack.focus({ preventScroll: true });

  const gauges = $("plant-checks");
  gauges.replaceChildren(...(p.checks || []).map(gaugeEl));
  const open = plantTodos(p, state);
  $("plant-todo").hidden = !open.length;
  $("plant-todo-list").replaceChildren(...open.map((text) => { const li = document.createElement("li"); li.textContent = keepUnits(text); return li; }));
  // A stage that is not confirmed is a task too: the automation and the feeding amounts wait for it.
  renderStatusLine(p, open.length + (!p.stage || p.stage.set_by !== "manual" ? 1 : 0));

  const sel = $("plant-stage-select");
  const labels = (p.stages || []).map((s) => s.label).join();
  if (sel.dataset.labels !== labels) {
    sel.dataset.labels = labels;
    sel.replaceChildren(...p.stages.map((s) => new Option(s.label, s.key)));
  }
  if (p.stage && document.activeElement !== sel) sel.value = p.stage.key;
  const st = p.stage || {};
  $("plant-apply").hidden = !st.preset;
  const pre = st.preset || {};
  const parts = [];
  if (pre.light_percent != null) parts.push(t("plant.presetLight", { value: qty(amount(pre.light_percent), "%") }));
  if (pre.spectrum) parts.push(t("plant.presetSpectrum", { name: SPECTRUM[pre.spectrum] ? t(SPECTRUM[pre.spectrum]) : pre.spectrum }));
  if (pre.cfan_step != null) parts.push(t("plant.presetCfan", { step: pre.cfan_step }));
  $("plant-preset").textContent = keepUnits([parts.length ? t("plant.preset", { list: parts.join(", ") }) : "", st.preset_note || ""]
    .filter(Boolean).join(" "));
  const w = st.watering;
  $("plant-water").textContent = w ? keepUnits(t("plant.watering", { minutes: amount(w.pump_minutes), ml: amount(w.flow_ml), start: w.start })) : "";
  const n = Object.keys(p.sources || {}).length;
  $("plant-sources").textContent = n ? tn("plant.sources", n) : "";
  if (Date.now() - photosLoaded > 10 * 60 * 1000) { photosLoaded = Date.now(); loadPhotos(); }
  renderWater(p);
  renderBloom(p);
  renderStart(p);
  renderFeeding(p.feeding);
}

// The flowering start (first white pistils): the owner's date, or the camera's estimate marked "geschätzt" with the
// question for the real date. Two taps save it, as for confirming a stage: it moves the stage, the feeding week and
// what Hank says. The field keeps what the owner typed until the save went through.
let bloomArm = 0;  // until then the second tap saves
let bloomBusy = false;
const HEIGHT_DUE_DAYS = 2;  // as plant.HEIGHT_DUE_DAYS: older than this, the card asks for today's height

function renderBloom(p) {
  const b = (p && p.bloom) || {};
  const input = $("plant-bloom");
  if (b.min) input.min = b.min;
  if (b.max) input.max = b.max;
  // Never pre-fill the camera's date: it is an upper bound, and two taps would save it as the owner's own (review 1).
  if (!input.dataset.dirty && document.activeElement !== input) input.value = b.estimated ? "" : (b.date || "");
  let note;
  if (!b.date) note = t("plant.bloomNone");
  else if (b.latest || b.estimated) note = t("plant.bloomLatest", { date: dateLong(b.date) });
  else note = t("plant.bloomNote", { date: dateLong(b.date), by: b.note || "" });
  // "Seit 1 Tag in der Blüte" only from a real start, never from the camera's "spätestens" (owner, 23.10.2026)
  if (b.date && b.flower_day != null && !(b.latest || b.estimated)) {
    note += " " + (b.flower_day === 0 ? t("plant.flowerToday") : tn("plant.flowerDays", b.flower_day));
  }
  const s = b.suggest;
  if (s && s.date) note += " " + t("plant.bloomSuggestNote", { date: dateShort(s.date), stage: s.stage || "", since: dateShort(s.stage_since) });
  $("plant-bloom-note").textContent = note.replace(/(\d)\.\.(\s|$)/g, "$1.$2");  // "seit 15.10." plus the full stop
  // One tap fills the field with the stage card's rough date; saving it still takes the two taps below.
  $("plant-bloom-suggest-row").hidden = !(s && s.date);
  if (s && s.date) {
    const take = $("plant-bloom-suggest");
    take.dataset.date = s.date;
    take.textContent = t("plant.bloomSuggestTake", { date: dateLong(s.date) });
  }
  const btn = $("plant-bloom-save");
  const armed = !bloomBusy && Date.now() < bloomArm;
  btn.classList.toggle("arm", armed);
  btn.disabled = bloomBusy;
  btn.textContent = armed ? t("plant.bloomArm", { date: dateLong(input.value), again: again() }) : t("plant.bloomSave");
}

// The plant's start (germination): a date, or the age in whole weeks; two taps save it, into the plant log. The server
// writes the age line and, while the owner types, what the value would mean (GET /api/plant/age): no date arithmetic
// here. It dates the feeding plan and the plant week; it never changes a dose.
let startArm = 0;
let startBusy = false;
let startPreview = { sig: "", text: "", bad: false };

function startValue() {  // {date} or {age_weeks}: typing in one field clears the other
  const day = $("plant-start-date").value;
  const weeks = $("plant-age-weeks").value.trim();
  if (day) return { date: day };
  if (weeks !== "") return { age_weeks: weeks };
  return null;
}

function startValueText(v) { return v.date ? dateLong(v.date) : tn("plant.startWeeks", Number(v.age_weeks)); }

function renderStart(p) {
  const a = (p && p.age) || {};
  const input = $("plant-start-date");
  if (a.min) input.min = a.min;
  if (a.max) input.max = a.max;
  // A start the owner gave as a date stands in the field; plant.json's rough one does not (two taps would make it the
  // owner's own, as with the camera's bloom date).
  if (!input.dataset.dirty && document.activeElement !== input && !$("plant-age-weeks").value) {
    input.value = a.by === "owner" && a.from === "date" ? (a.start || "") : "";
  }
  $("plant-start-note").textContent = keepUnits([a.line, a.ask_text].filter(Boolean).join(" "));
  const value = startValue();
  const preview = $("plant-start-preview");
  preview.textContent = value ? startPreview.text : "";
  preview.className = startPreview.bad && value ? "fb bad" : "hint";
  const btn = $("plant-start-save");
  const armed = !startBusy && value && Date.now() < startArm;
  btn.classList.toggle("arm", !!armed);
  btn.disabled = startBusy;
  btn.textContent = armed ? t("plant.startArm", { value: startValueText(value), again: again() }) : t("plant.startSave");
}

function previewStart() {
  const v = startValue();
  const query = !v ? "" : v.date ? `date=${encodeURIComponent(v.date)}` : `weeks=${encodeURIComponent(v.age_weeks)}`;
  if (query === startPreview.sig) return;
  startPreview = { sig: query, text: "", bad: false };
  const redraw = () => { if (state && state.plant) renderStart(state.plant); };
  if (!query) { redraw(); return; }
  api(`/api/plant/age?${query}`).then((res) => {
    if (startPreview.sig !== query) return;
    startPreview = { sig: query, text: res.text || "", bad: false };
    redraw();
  }, (err) => {
    if (startPreview.sig !== query) return;
    startPreview = { sig: query, text: err.message, bad: true };
    redraw();
  });
}

function renderWater(p) {
  const tg = p.water_targets;
  $("water-box").hidden = !tg || !(p.water || []).length;  // no pH/EC meter in use: only show once readings exist
  if ($("water-box").hidden) return;
  $("water-medium").textContent = p.medium ? t("water.medium", { name: p.medium.name }) : "";
  const last = (p.water || []).slice(-1)[0];
  // As evaluate() on the server: no value is "unknown", a value without min and max is "none", never "ok".
  const status = (v, tt) => v == null ? "unknown" : tt.min == null && tt.max == null ? "none"
    : tt.min != null && v < tt.min ? "low" : tt.max != null && v > tt.max ? "high" : "ok";
  const rows = [];
  if (tg.ph) rows.push({ metric: "water_ph", label: tg.ph.label, unit: "", min: tg.ph.min, max: tg.ph.max, actual: last ? last.ph : null, status: status(last && last.ph, tg.ph) });
  if (tg.ec) rows.push({ metric: "water_ec", label: tg.ec.label, unit: "mS/cm", min: tg.ec.min, max: tg.ec.max, actual: last ? last.ec : null,
    status: status(last && last.ec, tg.ec) });
  $("water-gauges").replaceChildren(...rows.map(gaugeEl));
  const notes = [tg.ph && tg.ph.note, tg.ec && tg.ec.note].filter(Boolean);
  $("water-note").textContent = keepUnits([last ? t("water.last", { time: stamp(new Date(last.time)) }) : t("water.none"), ...notes].join(" "));
  $("water-log").replaceChildren(...(p.water || []).slice().reverse().slice(0, 8).map((w) => {
    const li = document.createElement("li");
    const values = [w.ph != null ? `pH ${fmt(w.ph, 1)}` : "", w.ec != null ? `EC ${readingText(w.ec, "mS/cm")}` : ""].filter(Boolean);
    li.textContent = `${stamp(new Date(w.time))}: ${values.join(", ")}`;
    return li;
  }));
}

async function loadPhotos() {
  let data;
  try { data = await api("/api/photos"); } catch (_) { return; }
  const list = (data.photos || []).slice().reverse();
  $("photo-strip-wrap").hidden = !list.length;
  $("photo-strip").replaceChildren(...list.map((d) => {
    const a = document.createElement("a"); a.href = langUrl(`/photos/${d}.jpg`); a.target = "_blank"; a.rel = "noopener";
    const img = document.createElement("img"); img.loading = "lazy"; img.alt = t("photos.alt", { date: dateLong(d) }); img.src = langUrl(`/photos/${d}.jpg`);
    const cap = document.createElement("span"); cap.textContent = dateShort(d);
    a.append(img, cap); return a;
  }));
}

// ---------- feeding ----------

// plant.feeding from the server: the chart as a draft until the owner confirms it against the bottles, then this
// week's doses once the week is confirmed too (the owner's stage, or a bloom_start the owner wrote into plant.json).
// Nothing is computed here: the dose per litre comes with the state, the amounts for the can from
// /api/feeding?liters= (the server rounds both down: 0.1 ml, 0.01 g, half spoons), and "fed" sends only the litres
// plus the card and the fingerprint this page shows (the server refuses a stale tab).
// Two fertilizers (23.10.2026): one doses at a time; the switch takes two taps, the server keeps the choice.
// The dated plan (columns with dates and plant weeks, the next change) comes as f.timeline; the age line as f.age.
const FEED_STATE = { draft: ["feed.stateDraft", "warn"], needs_stage: ["feed.stateNeedsStage", "warn"],
  ok: ["feed.stateOk", "ok"], water_only: ["feed.stateWater", "info"], harvest: ["feed.stateHarvest", ""],
  label_ends: ["feed.stateLabelEnds", "info"], needs_choice: ["feed.stateNeedsChoice", "warn"],
  no_card: ["feed.stateNone", ""] };
// Where the week comes from, said under "this week" (the owner's stage has its own two lines in feedFrom).
const FEED_FROM = { owner: "feed.fromOwner", plan: "feed.fromPlan", estimate: "feed.fromEstimate", none: "feed.fromNone" };
const FEED_LITERS = "vgrow.feedLiters";  // the can's litres, remembered on this device as a plain number (1.5)
let feedShowAll = false;  // optional products the owner does not have: hidden until asked for
let feedPlan = { sig: "", plan: null, failed: false, at: 0 };
let feedBusy = false;
let feedArmTimer = null;
let feedLarge = null;  // litres above the second-tap limit that the owner has confirmed: amounts for exactly these
let feedSwitchArm = { id: "", until: 0 };  // the fertilizer switch: until then a second tap on this one switches
let feedSwitchBusy = false;

function make(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

function currentFeeding() { return (state && state.plant && state.plant.feeding) || null; }

// Litres, chart values and the dose factor as they are, never rounded for show: num() would turn a factor of
// 0.25 into 0.3. Up to three decimals, without trailing zeros.
function plain(v) { return new Intl.NumberFormat(LOCALE[LANG], { maximumFractionDigits: 3, useGrouping: false }).format(v); }

// An amount as the server rounded it: ml with one decimal, g with two (0.75 g must never read "0,8 g", more than
// computed). Every server value is a whole multiple of 0.1 ml or 0.01 g, so the fixed decimals are exact.
function amtText(v, unit, digits) { return v == null ? "–" : qty(fmt(v, digits ?? (unit === "g" ? 2 : 1)), unit); }

// Half spoons as the server counted them from the exact grams (rounded down); null: under half a spoon, the scale.
function spoonsText(n) { return n == null ? t("feed.scale") : tn("feed.spoons", n, { n: plain(n) }); }

function litersText(v) { return qty(plain(v), "l"); }

// The litres field is a text field: a number field shows the browser's separator, not the page's, and rejects "1,5"
// in an English browser. Both separators count. null for an empty field, NaN for anything that is not a number.
function parseLiters(text) {
  const s = String(text ?? "").trim();
  if (!s) return null;
  return /^(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(s) ? Number(s.replace(",", ".")) : NaN;
}

function showLiters(value) {  // the remembered litres, written the page language's way (1,5 or 1.5)
  if (Number.isFinite(value)) $("feed-liters").value = plain(value);
}

// A source as a link (web addresses only), its title and the day it was read as the tooltip.
function sourceLink(src, text, cls = "") {
  const a = make("a", cls, text);
  const url = String((src && src.url) || "");
  if (/^https?:\/\//i.test(url)) { a.href = url; a.target = "_blank"; a.rel = "noopener"; }
  a.title = [src && src.title, src && src.retrieved ? t("feed.retrieved", { date: dateLong(src.retrieved) }) : ""]
    .filter(Boolean).join(", ");
  return a;
}

function sourceChips(ids, sources) {
  return (ids || []).filter((id) => sources && sources[id]).map((id) => sourceLink(sources[id], id, "src-chip"));
}

// A chart wider than the phone fades out on the right while more weeks wait there.
function feedEdge(wrap) { wrap.classList.toggle("more", wrap.scrollLeft + wrap.clientWidth < wrap.scrollWidth - 2); }

// The chart snaps to whole weeks beside the sticky product names (scroll-padding as wide as the names), the far
// end included: the spacer after the last week takes the part of a column that would otherwise peek out from under
// the names (a stray "6" of "Week 6").
function feedSnap(wrap) {
  const table = wrap.querySelector("table");
  const row = table && table.tHead && table.tHead.rows[0];
  if (!row || row.cells.length < 3 || !wrap.clientWidth) return;
  const cells = [...row.cells].filter((c) => !c.classList.contains("pad"));
  const names = cells[0].getBoundingClientRect().width;
  wrap.style.scrollPaddingLeft = `${names}px`;
  const widths = cells.slice(1).map((c) => c.getBoundingClientRect().width);
  const room = wrap.clientWidth - names;
  let tail = 0, k = widths.length;
  while (k > 0 && tail + widths[k - 1] <= room + 0.5) tail += widths[--k];
  const total = widths.reduce((a, b) => a + b, 0);
  const space = total > room + 0.5 && k > 0 ? Math.max(0, room - tail) : 0;  // exact: columns are not whole pixels
  // On the block inside the spacer: a table wider than its box squeezes an empty cell's own width to nothing.
  for (const block of table.querySelectorAll(".pad-in")) block.style.width = `${space.toFixed(2)}px`;
}

function padCell(tag) {  // the spacer cell at the end of a chart row
  const cell = make(tag, "pad");
  cell.appendChild(make("i", "pad-in"));
  return cell;
}

function feedEdges() {
  for (const wrap of document.querySelectorAll(".feed-scroll")) { feedSnap(wrap); feedEdge(wrap); }
}

// One chart table: the columns given (from the server's columns and timeline), products as rows. The current week is
// marked "jetzt", every flowering week carries its date (the server's timeline), the label's own wording sits in the
// header's tooltip. In state ok the owned products show the server's amount per litre, otherwise the chart value.
function feedTable(f, indexes, nowIndex) {
  const cols = f.columns || [];
  const line = f.timeline || {};
  const byKey = Object.fromEntries((line.columns || []).map((c) => [c.key, c]));
  const amounts = f.state === "ok";
  const table = make("table", "feed-chart");
  const headRow = table.createTHead().insertRow();
  headRow.appendChild(make("th"));
  for (const i of indexes) {
    const c = cols[i];
    const tc = byKey[c.key] || {};
    const isNow = i === nowIndex;
    // A long column label ("Anzucht (Woche 1 bis 2)") keeps its short head; the whole label is the tooltip.
    const head = c.bloom_week ? t("feed.week", { n: c.bloom_week }) : String(c.label || "").replace(/\s*\(.*\)$/, "");
    const th = make("th", isNow ? "now" : "", head);
    th.scope = "col";
    th.title = [c.label, c.printed ? t("feed.printed", { text: c.printed }) : ""].filter(Boolean).join(" · ");
    const small = isNow ? t("feed.now") : tc.from ? dateShort(tc.from) : "";
    if (small) th.appendChild(make("small", "", small));
    headRow.appendChild(th);
  }
  headRow.appendChild(padCell("th"));  // the spacer feedSnap sizes
  const body = table.createTBody();
  for (const r of (f.chart || []).filter((row) => feedShowAll || !row.hidden_by_default)) {
    const tr = body.insertRow();
    if (!r.owned) { tr.className = "not-owned"; tr.title = t("feed.notOwned", { list: r.label }); }
    const th = make("th", "", r.optional ? t("feed.optional", { name: r.label }) : r.label);
    th.scope = "row";
    tr.appendChild(th);
    for (const i of indexes) {
      let text;
      const own = amounts && r.owned ? ((byKey[cols[i].key] || {}).values || []).find((v) => v.component === r.component) : null;
      if (own) text = own.per_liter == null ? "–" : fmt(own.per_liter, f.amount_digits ?? 1);
      else { const v = (r.values || [])[i]; text = v == null ? "–" : plain(v); }
      tr.appendChild(make("td", i === nowIndex ? "now" : "", text));
    }
    tr.appendChild(padCell("td"));
  }
  const wrap = make("div", "feed-scroll");
  wrap.appendChild(table);
  wrap.addEventListener("scroll", () => feedEdge(wrap), { passive: true });
  return wrap;
}

// The plan from this week's column on, the past columns folded below it (the whole label table stays one tap away
// for comparing with the bottle). Nothing current after the last column (water, harvest, the table's end): only the
// folded past.
function feedChart(f) {
  const cols = f.columns || [];
  const now = cols.findIndex((c) => f.week && c.key === f.week.key);
  const all = cols.map((_, i) => i);
  const later = now < 0 ? [] : all.slice(now);
  const past = now < 0 ? all : all.slice(0, now);
  const out = [];
  const amounts = f.state === "ok";
  if (later.length) {
    // amount_unit may be missing (the preview mock, an older server): ml, as before (review S3).
    const caption = amounts ? t("feed.planCaption", { unit: `${f.amount_unit || "ml"}/l` }) : t("feed.chartCaption", { unit: f.unit || "" });
    out.push(make("p", "feed-cap", caption));
    if (!amounts && f.state !== "draft") out.push(make("p", "feed-cap", t("feed.captionLabel")));
    out.push(feedTable(f, later, now));
    if (f.timeline && f.timeline.dates === "latest") out.push(make("p", "feed-cap", t("feed.datesLatest")));
  }
  if (past.length) {
    const details = make("details", "feed-past");
    details.appendChild(make("summary", "", t("feed.past", { n: past.length })));
    details.appendChild(feedTable(f, past, -1));
    details.addEventListener("toggle", feedEdges);
    out.push(details);
  }
  const after = (f.after_bloom || []).map((a) => a.label).filter(Boolean);
  if (after.length) out.push(make("p", "feed-cap", t("feed.after", { list: after.join(" · ") })));
  const hidden = (f.chart || []).filter((r) => r.hidden_by_default);
  if (hidden.length) {
    const p = make("p", "feed-cap");
    const btn = make("button", "linkish", t(feedShowAll ? "feed.hide" : "feed.show"));
    btn.type = "button";
    btn.dataset.feedToggle = "1";
    btn.onclick = () => {
      feedShowAll = !feedShowAll;
      renderFeeding(currentFeeding());
      const next = [...document.querySelectorAll("[data-feed-toggle]")].find((b) => b.offsetParent);
      if (next) next.focus({ preventScroll: true });
    };
    p.append(t("feed.notOwned", { list: hidden.map((r) => t("feed.optional", { name: r.label })).join(", ") }), " ", btn);
    out.push(p);
  }
  if (f.page && f.page.note) out.push(make("p", "feed-cap", keepUnits(f.page.note)));
  return out;
}

// An amount cell: the figure in bold, the spoons small under it on a g card with a spoon (tooltip: how it rounds).
function amountCell(cls, value, unit, digits, spoons, withSpoons) {
  const td = make("td", cls);
  td.appendChild(make("b", "", amtText(value, unit, digits)));
  if (withSpoons && value != null) {
    const small = make("small", "feed-spoons", spoonsText(spoons));
    small.title = t("feed.spoonNote");
    td.appendChild(small);
  }
  return td;
}

// This week's doses as the server computed them, and the can's amounts once the server has answered for the litres
// shown in the header. Grams with two decimals, spoons small under them; ml as before.
function feedDoses(f, plan) {
  const unit = f.unit || "";
  const amount = f.amount_unit || "ml";
  const spoons = !!f.measure;
  const table = make("table", "feed-doses");
  const head = table.createTHead().insertRow();
  const can = plan ? t("feed.colCan", { liters: litersText(plan.liters) }) : t("feed.colCanEmpty");
  // "amt" and "ref", not "num" and "chart": .num is every card's numbered badge, .chart a history chart.
  for (const [text, cls] of [[t("feed.colProduct"), ""], [t("feed.colChart"), "amt"], [t("feed.colDose"), "amt"], [can, "amt"]]) {
    const th = make("th", cls, text);
    th.scope = "col";
    head.appendChild(th);
  }
  const body = table.createTBody();
  for (const d of f.doses || []) {
    const tr = body.insertRow();
    const th = make("th", "", d.label);
    th.scope = "row";
    const item = plan && (plan.items || []).find((i) => i.component === d.component);
    const dose = amountCell("amt", d.per_liter, unit, f.amount_digits, d.spoons_per_liter, spoons);
    const total = item ? amountCell("amt total", item.total, plan.unit || amount, f.amount_digits, item.spoons_total, spoons)
      : make("td", "amt total", "–");
    tr.append(th, make("td", "amt ref", qty(plain(d.chart), unit)), dose, total);
  }
  return table;
}

// The line under "this week": where the chart week comes from, and the current week's dates.
function feedFrom(f) {
  const src = f.week_source;
  const now = ((f.timeline && f.timeline.columns) || []).find((c) => c.status === "now");
  const dates = now && now.from ? t("feed.weekDates", { from: dateShort(now.from), to: dateShort(now.to) }) : "";
  if (!src) return dates;
  const date = src.bloom_start ? dateLong(src.bloom_start) : "";
  let from;
  if (src.kind === "stage") {
    from = src.bloom_start ? t("feed.fromStage", { date }) : t("feed.fromStageVeg", { stage: src.stage || "" });
  } else {
    from = FEED_FROM[src.kind] ? t(FEED_FROM[src.kind], { date }) : "";
  }
  return [dates, from].filter(Boolean).join(" · ");
}

// One change of the next dose: "Base A 0,5 g/l statt 0,75 g/l"; a product that starts or stops says so.
function changeText(f, c, basis) {
  const unit = `${f.amount_unit || "ml"}/l`;  // never "undefined/l" (preview mock, review S3)
  const val = (v) => basis === "amount" ? amtText(v, unit, f.amount_digits ?? 1) : qty(plain(v), unit);
  if (c.to == null) return t("feed.changeOff", { name: c.label });
  if (c.from == null) return t("feed.changeOn", { name: c.label, to: val(c.to) });
  return t("feed.changeTo", { name: c.label, to: val(c.to), from: val(c.from) });
}

// The one line about what comes next: the next change of dose with its date ("spätestens" while the bloom start is
// only the camera's estimate), or what follows the last column (plain water, harvest, the table's end).
function feedNextChange(f) {
  const nc = f.timeline && f.timeline.next_change;
  if (!nc) return "";
  if (nc.tail) {
    if (!nc.date) return t("feed.tailNoDate", { label: nc.label || "" });
    return t(nc.latest ? "feed.tailLatest" : "feed.tail", { date: dateShort(nc.date), label: nc.label || "" });
  }
  let list = (nc.changes || []).map((c) => changeText(f, c, nc.basis)).join(", ");
  if (nc.basis !== "amount" && list) list += ` ${t("feed.labelValues")}`;
  if (!nc.date) return t(nc.when === "stage" ? "feed.nextChangeStage" : "feed.nextChangeBloom", { week: nc.label || "", list });
  return t(nc.latest ? "feed.nextChangeLatest" : "feed.nextChange", { date: dateShort(nc.date), week: nc.label || "", list });
}

// The fertilizer switch: one button per setup, the active one pressed. A tap on another one asks, the second tap
// switches (the server stores it; the page drops amounts it had for the other card). Redrawn on every render, since
// its second tap and busy state change without a new feeding view.
function feedSwitch(f) {
  const opts = (f.choice && f.choice.options) || [];
  const box = $("feed-switch");
  box.hidden = opts.length < 2;
  $("feed-choice-note").hidden = !(f.state === "needs_choice");
  $("feed-choice-note").textContent = f.state === "needs_choice"
    ? [t("feed.needsChoice"), (f.choice && f.choice.why) || ""].filter(Boolean).join(" ") : "";
  if (opts.length < 2) { box.replaceChildren(); return; }
  box.title = (f.choice && f.choice.why) || "";
  const had = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.setup : null;
  box.replaceChildren(...opts.map((o) => {
    const armed = !feedSwitchBusy && feedSwitchArm.id === o.id && Date.now() < feedSwitchArm.until;
    const b = make("button", `btn sm${o.active ? " on" : ""}${armed ? " arm" : ""}`,
      armed ? t("feed.switchArm", { name: o.label, again: again() }) : o.label);
    b.type = "button";
    b.dataset.setup = o.id;
    b.setAttribute("aria-pressed", o.active ? "true" : "false");
    b.disabled = feedSwitchBusy || o.active;
    b.onclick = () => feedSwitchTap(o);
    return b;
  }));
  if (had) {
    const back = box.querySelector(`[data-setup="${had}"]`);
    if (back && !back.disabled) back.focus({ preventScroll: true });
  }
}

async function feedSwitchTap(o) {
  if (feedSwitchBusy || o.active) return;
  if (!(feedSwitchArm.id === o.id && Date.now() < feedSwitchArm.until)) {
    feedSwitchArm = { id: o.id, until: Date.now() + 4000 };
    renderFeeding(currentFeeding());
    setTimeout(() => renderFeeding(currentFeeding()), 4100);
    return;
  }
  feedSwitchArm = { id: "", until: 0 };
  feedSwitchBusy = true;
  renderFeeding(currentFeeding());
  setFb("fb-feed-switch", t("fb.sending"));
  try {
    await api("/api/cmd", { action: "feeding_setup", setup: o.id });
    feedPlan = { sig: "", plan: null, failed: false, at: 0 };  // amounts of the other card never carry over
    feedLarge = null;
    setFb("fb-feed-switch", t("feed.switchDone", { name: o.label }), "ok");
    poll(true);
  } catch (err) {
    setFb("fb-feed-switch", err.message, "bad");
  } finally {
    feedSwitchBusy = false;
    renderFeeding(currentFeeding());
  }
}

function feedFacts(f) {
  const dl = $("feed-facts");
  dl.replaceChildren();
  const row = (label, value, chips, notes) => {
    const dd = make("dd");
    dd.append(value, ...chips);
    for (const note of [notes].flat()) {
      if (!note) continue;
      const small = make("small");
      if (typeof note === "string") small.textContent = keepUnits(note); else small.append(...note);
      dd.appendChild(small);
    }
    dl.append(make("dt", "", label), dd);
  };
  const last = f.last_fed;
  if (last) {
    // The line's OWN unit and decimals, never the active card's: an old BioBizz line stays ml while VIVOSUN is
    // active, a g line keeps its two decimals under BioBizz (critique 23.10.2026, must 2). One unbreakable piece per
    // product and amount: a narrow phone never splits "Top-Max" from its "0,7 ml".
    const items = [];
    (last.items || []).forEach((i, n) => {
      if (n) items.push(" · ");
      items.push(make("span", "nowrap", `${i.label} ${amtText(i.total, last.unit || "ml", last.digits)}`));
    });
    const card = last.other_card && last.card_name ? t("feed.otherCard", { name: last.card_name }) : "";
    row(t("feed.lastFed"), [stamp(new Date(last.time)), litersText(last.liters), last.week && last.week.label, card]
      .filter(Boolean).join(" · "), [], items.length ? [items] : last.state === "water_only" ? t("feed.stateWater") : "");
  } else {
    row(t("feed.lastFed"), t("ago.never"), [], "");
  }
  if (f.dose_factor != null) {
    row(t("feed.factor"), t("feed.factorValue", { factor: plain(f.dose_factor) }), sourceChips(f.dose_factor_sources, f.sources),
      f.dose_factor_note);
  }
  if (f.frequency_note) row(t("feed.frequency"), keepUnits(f.frequency_note), sourceChips(f.frequency_sources, f.sources), "");
  // The date is the value; where it comes from stands under "this week" already. A stage before flowering has none.
  row(t("feed.bloomStart"), f.bloom_start ? dateLong(f.bloom_start) : t("feed.bloomNone"),
    sourceChips(f.bloom_start_sources, f.sources), [f.bloom_start ? "" : feedFrom(f), f.bloom_estimate_note, f.bloom_start_note]);
}

// Litres above the second-tap limit: the question right under the field, until the owner answers it for exactly
// these litres. Until then no amounts: 15 typed for 1.5 never turns into 22.5 ml of Bio-Grow unnoticed.
function feedAsk(f, liters) {
  $("feed-large").hidden = liters == null;
  if (liters == null) return;
  $("feed-large-t").textContent = t("feed.largeAsk", { liters: litersText(liters), limit: litersText(f.liters_confirm_above) });
  $("feed-large-yes").textContent = t("feed.largeYes", { liters: litersText(liters) });
  $("feed-large-yes").dataset.liters = String(liters);
}

// Above the second-tap limit, but within what any can holds: beyond that the server's range message says it all.
function bigLiters(f, liters) {
  const [, max] = f.liters_range || [];
  return Number.isFinite(liters) && f.liters_confirm_above != null && liters > f.liters_confirm_above
    && (max == null || liters <= max);
}

// Amounts for the can: asked from the server once per litres, card, week and doses, never computed here. Its answer
// (an error included) shows right under the litres field.
function wantPlan(f, raw) {
  const done = f.state === "ok" || f.state === "water_only";
  const liters = done ? parseLiters(raw) : null;
  const big = bigLiters(f, liters);
  feedAsk(f, big && feedLarge !== liters ? liters : null);
  if (!done || !Number.isFinite(liters) || (big && feedLarge !== liters)) {
    feedPlan = { sig: "", plan: null, failed: false, at: 0 };
    if ($("fb-feed").dataset.src === "plan") setFb("fb-feed", "");
    if (Number.isNaN(liters)) {  // letters in the field: say so where the amounts would be
      setFb("fb-feed", t("feed.badLiters"), "bad");
      $("fb-feed").dataset.src = "plan";
    }
    return;
  }
  const query = `liters=${encodeURIComponent(String(liters))}${big ? "&large=1" : ""}`;
  // The card is part of it: after a switch, amounts of the other card are never shown (critique 23.10.2026).
  const sig = [LANG, query, f.card && f.card.id, f.state, f.week && f.week.key, JSON.stringify(f.doses)].join("|");
  if (sig === feedPlan.sig && !(feedPlan.failed && Date.now() - feedPlan.at > 15000)) return;
  const keep = feedPlan.sig.split("|")[2] === String(f.card && f.card.id) ? feedPlan.plan : null;
  feedPlan = { sig, plan: keep, failed: false, at: Date.now() };  // the last amounts stay until the new ones
  api(`/api/feeding?${query}`).then((data) => {
    if (feedPlan.sig !== sig) return;
    feedPlan.plan = data.plan || null;
    if ($("fb-feed").dataset.src === "plan") setFb("fb-feed", "");
    renderFeeding(currentFeeding());
  }, (err) => {
    if (feedPlan.sig !== sig) return;
    feedPlan = { sig, plan: null, failed: true, at: Date.now() };
    setFb("fb-feed", err.message, "bad");  // e.g. litres out of range, in the server's words, under the field
    $("fb-feed").dataset.src = "plan";
    renderFeeding(currentFeeding());
  });
}

// The last flowering week the card's table names (VIVOSUN 8, CANNA 9): the week before its first tail, else the
// last flowering column. Never a fixed 8 (critique 23.10.2026, M2).
function lastChartWeek(f) {
  // the table's own end first: Hesi names a plain-water week 8 and ends after it (fertilizer market 2)
  const ends = (f.after_bloom || []).filter((a) => a && a.key === "label_end" && Number.isInteger(a.bloom_week));
  if (ends.length) return Math.min(...ends.map((a) => a.bloom_week)) - 1;
  const tails = (f.after_bloom || []).map((a) => a && a.bloom_week).filter((n) => Number.isInteger(n));
  if (tails.length) return Math.min(...tails) - 1;
  const cols = (f.columns || []).map((c) => c && c.bloom_week).filter((n) => Number.isInteger(n));
  return cols.length ? Math.max(...cols) : "?";
}

function renderFeedDone(f) {
  const doses = f.doses || [];
  // Plain water by the owner's date: the warning line says it with the date, not "this week" of the chart.
  const say = f.not_before ? "" : f.state === "water_only" ? "feed.waterWeek" : f.state === "harvest" ? "feed.harvestNote"
    : f.state === "label_ends" ? "feed.labelEndsNote" : doses.length ? "" : "feed.noDose";
  $("feed-say").hidden = !say;
  $("feed-say").textContent = say ? t(say, { week: lastChartWeek(f) }) : "";
  $("feed-doses").replaceChildren(...(f.state === "ok" && doses.length ? [feedDoses(f, feedPlan.plan)] : []));
  // The server refuses a feeding from harvest on and after the table's end.
  $("feed-log-row").hidden = f.state === "harvest" || f.state === "label_ends";
  $("feed-fed").textContent = t(f.state === "water_only" ? "feed.watered" : "feed.fed");
  $("feed-fed").disabled = feedBusy;
  feedFacts(f);
  const next = f.state === "harvest" ? [] : feedChart(f);
  $("feed-next").hidden = !next.length;
  $("feed-chart-next").replaceChildren(...next);
}

let feedSig = "";

function renderFeeding(f) {
  $("feed").hidden = !f;
  if (!f) { feedSig = ""; return; }  // no feeding view (the server could not build it): the rest of the card stays
  const choosing = f.state === "needs_choice";  // two fertilizers, none chosen: only the switch and one sentence
  const draft = f.state === "draft";
  const waiting = f.state === "needs_stage";  // the chart is confirmed, the week is only an estimate
  const done = ["ok", "water_only", "harvest", "label_ends"].includes(f.state);
  feedSwitch(f);
  wantPlan(f, $("feed-liters").value);
  // The stage button follows every render (its second tap, its busy state), not only a changed feeding view.
  const stage = state && state.plant && state.plant.stage;
  const suggest = waiting && stage && stage.set_by !== "manual" ? stage : null;
  $("feed-stage-row").hidden = !suggest;
  if (suggest) stageButton($("feed-stage-btn"), "feed", suggest, "fb-feed-stage", (text) => setFb("fb-feed", text, "ok"));
  // Drawn anew only when something in it changed, not on every poll: a chart scrolled to week 6 stays there. The
  // card id is part of f: a switch redraws everything.
  const sig = JSON.stringify([LANG, f, feedShowAll, feedPlan.plan, !!suggest]);
  if (sig === feedSig) return;
  feedSig = sig;
  const [pill, cls] = FEED_STATE[f.state] || FEED_STATE.no_card;
  $("feed-pill").textContent = t(pill);
  $("feed-pill").className = `pill sm ${cls}`;
  $("feed-meta").textContent = choosing ? "" : [f.card && f.card.name, f.page && f.page.label].filter(Boolean).join(" · ");
  $("feed-problem").hidden = !f.problem;
  $("feed-problem").textContent = f.problem || "";
  const age = (f.timeline && f.timeline.age) || null;
  $("feed-now").hidden = choosing || !(f.week && f.week.label);
  $("feed-week").textContent = (f.week && f.week.label) || "";
  // The week's dates and today's plant week under it, then where the week comes from (the confirm step shows it).
  const from = feedFrom(f);
  const plantWeek = age ? t("feed.plantWeek", { n: age.life_week }) : "";
  // week.note: the label counts this column's own weeks ("Woche 1 bis 4") and the plant is older (review S8).
  $("feed-week-from").textContent = keepUnits([from.split(" · ")[0], plantWeek, ...from.split(" · ").slice(1),
    f.week && f.week.note].filter(Boolean).join(" · "));
  // One age line from the server; while the start is unknown or only rough it asks, with a jump to the field.
  const a = f.age;
  $("feed-age").hidden = choosing || !a;
  $("feed-age-t").textContent = a ? keepUnits([a.ask ? a.ask_text : a.line].filter(Boolean).join(" ")) : "";
  $("feed-age-enter").hidden = !(a && a.ask);
  const change = choosing || f.not_before ? "" : feedNextChange(f);  // before the owner's date the chart's next step is not next
  $("feed-nextchange").hidden = !change;
  $("feed-nextchange").textContent = keepUnits(change);
  $("feed-warning").hidden = choosing || !f.warning;  // the owner's stage and a bloom_start in plant.json do not fit
  $("feed-warning").textContent = keepUnits(f.warning || "");
  $("feed-draft").hidden = !draft;
  $("feed-stage").hidden = !waiting;
  $("feed-done").hidden = !done;
  if (draft) {
    $("feed-draft-note").textContent = t(f.reconfirm ? "feed.reconfirmNote" : "feed.draftNote");
    // The chart's own numbers, and right under them what the factor makes of them once confirmed: a grower who
    // mixes straight from the draft must not give the full chart dose the run does not intend.
    const parts = feedChart(f);
    if (parts.length && f.dose_factor != null) {
      parts.splice(2, 0, make("p", "feed-cap feed-factor", t("feed.draftFactor", { factor: plain(f.dose_factor) })));
    }
    $("feed-chart").replaceChildren(...parts);
  }
  if (waiting) {
    const none = f.week_source && f.week_source.kind === "none";
    $("feed-stage-note").textContent = keepUnits([t(none ? "feed.needsStageNone" : "feed.needsStageNote"),
      f.bloom_estimate_note || ""].filter(Boolean).join(" "));
    $("feed-chart-stage").replaceChildren(...feedChart(f));
  }
  if (done) renderFeedDone(f);
  const notes = choosing ? [] : f.notes || [];
  $("feed-notes").hidden = !notes.length;
  $("feed-notes-t").textContent = tn("feed.notes", notes.length);
  $("feed-notes-list").replaceChildren(...notes.map((n) => {
    const li = make("li", "", keepUnits(n.text));
    li.append(...sourceChips(n.sources, f.sources));
    return li;
  }));
  // The official chart sits above the confirm button in a draft (that is what to compare with), below otherwise.
  const ids = choosing ? [] : ((f.page && f.page.sources) || []).filter((id) => f.sources && f.sources[id]);
  const official = draft ? $("feed-official-draft") : $("feed-official");
  (draft ? $("feed-official") : $("feed-official-draft")).replaceChildren();
  if (ids.length) {
    const links = make("span");
    ids.forEach((id, i) => { if (i) links.append(" · "); links.appendChild(sourceLink(f.sources[id], f.sources[id].title || id)); });
    fill(official, "feed.official", null, { links });
  } else {
    official.replaceChildren();
  }
  // A draft says where its numbers come from; a confirmed chart says since when it counts.
  $("feed-status").textContent = choosing ? "" : draft ? keepUnits((f.card && f.card.status_note) || "")
    : f.confirmed_at ? t("feed.confirmedAt", { time: stamp(new Date(f.confirmed_at)) }) : "";
  requestAnimationFrame(feedEdges);
}

// ---------- camera ----------

const CAMERA_LIVE_DEFAULT = true;
const cameraViews = new Map();
const cameraTemplate = $("card-cam").cloneNode(true);
let cameraViewNumber = 0;

function cameraSources(s) {  // a camera the owner hid (vgrow/camera_prefs.py) gets no card; the others take its room
  return (s?.cameras || (s?.camera ? [{ id: "growcam", ...s.camera }] : []))
    .filter(cam => cam.configured && !cam.hidden).sort((a, b) => (b.id === "growcam") - (a.id === "growcam"));
}

function renderHiddenCameras(s) {  // one line under the cards: each hidden camera with its "Show", and the news when it delivers again
  const line = $("camera-hidden");
  const away = (s?.cameras || []).filter(cam => cam.configured && cam.hidden);
  const key = JSON.stringify(away.map(cam => [cam.id, cam.name, cam.reachable === true, LANG]));
  line.hidden = !away.length;
  if (key === line.dataset.key) return;  // rebuilt only on a change, so a poll never swaps a button under a click
  line.dataset.key = key;
  line.replaceChildren(...away.flatMap(cam => {
    const back = cam.reachable === true, text = document.createElement("span"), show = document.createElement("button");
    text.textContent = t(back ? "cam.hiddenBack" : "cam.hiddenOne", { name: cam.name || cam.id }) + " ";
    show.type = "button";
    show.className = back ? "btn sm primary" : "btn sm";
    show.textContent = t("cam.show");
    show.onclick = () => setCameraHidden(cam.id, false);
    return [text, show, document.createTextNode(" ")];
  }));
}

async function setCameraHidden(id, hidden, view) {
  try {
    await api("/api/cmd", { action: "camera_hide", camera: id, hidden });
    await poll(true);
  } catch (err) {
    if (view) setFb(view.$("fb-cam").id, err.message, "bad");
  }
}

function cameraUrl(path, view) {
  return langUrl(`${path}${path.includes('?') ? '&' : '?'}camera=${encodeURIComponent(view.id)}`);
}

function cameraVisible(view) {
  return !document.hidden && !view.root.hidden && !!view.root.getClientRects().length;
}

function cameraStill(view) {
  if (!view.live && cameraVisible(view)) view.$("cam-img").src = cameraUrl(`/camera/snapshot.jpg?t=${Date.now()}`, view);
}

function camStill() {
  for (const view of cameraViews.values()) cameraStill(view);
}

function camBlank(key, view) {
  view.blankKey = key;
  view.$("cam-ph").hidden = !key;
  if (key) view.$("cam-ph-t").textContent = t(key);
  view.$("cam-img").classList.toggle("broken", !!key);
}

function camSeg(view) {
  for (const [id, on] of [["cam-live", view.live], ["cam-still", !view.live]]) {
    view.$(id).classList.toggle("on", on);
    view.$(id).setAttribute("aria-pressed", String(on));
  }
}

function setLive(on, view) {
  view.live = on;
  clearTimeout(view.timer);
  camSeg(view);
  if (on) {
    camBlank("", view);
    view.$("cam-img").src = cameraUrl(`/camera/live.mjpg?t=${Date.now()}`, view);
    view.timer = setTimeout(() => {
      // MJPEG can have frames while img.complete stays false for the open stream.
      if (view.live && !view.$("cam-img").naturalWidth && cameraVisible(view)) setLive(true, view);
    }, 12000);
  } else if (cameraVisible(view)) {
    cameraStill(view);
  } else {
    view.$("cam-img").removeAttribute("src");
  }
  renderCameraProgress();
}

function refreshCameraPlayback() {
  for (const view of cameraViews.values()) {
    if (!cameraVisible(view)) {
      if (view.live || view.$("cam-img").hasAttribute("src")) setLive(false, view);
    } else if (view.wanted && !view.live && view.state?.reachable !== false) {
      setLive(true, view);
    } else if (!view.$("cam-img").hasAttribute("src")) {
      cameraStill(view);
    }
  }
}

function renderCameraProgress(serverDown = false) {
  for (const view of cameraViews.values()) {
    const waiting = view.live && !view.blankKey && !view.$("cam-img").naturalWidth;
    const offline = serverDown || view.state?.reachable === false;
    view.$("camera-loading").hidden = !waiting;
    view.$("camera-frame").setAttribute("aria-busy", String(waiting));
    view.$("cam-pill").textContent = t(offline ? "status.offline" : waiting ? "cockpit.cameraLoading" : view.live ? "cam.live" : "cam.still");
    view.$("cam-pill").className = "pill sm" + (offline ? " warn" : waiting ? "" : view.live ? " live" : "");
    view.$("camera-freshness").textContent = t(serverDown ? "cockpit.cachedState" : offline ? "status.offline" : waiting ? "cockpit.cameraLoading" : view.live ? "cockpit.liveCamera" : "cam.still");
  }
}

function cameraZoomBounds(view) {
  const frame = view.$("camera-frame");
  // Cover crops inside the image box first. Keep that scaled box over the entire
  // frame when panning, including fullscreen with a different aspect ratio.
  return { x: frame.clientWidth * (view.zoom - 1) / 2,
    y: frame.clientHeight * (view.zoom - 1) / 2 };
}

function applyCameraZoom(view, zoom = view.zoom) {
  if (view.id !== "usb") return;
  view.zoom = Math.min(4, Math.max(1, Number(zoom) || 1));
  if (view.zoom === 1) view.pan = { x: 0, y: 0 };
  const bounds = cameraZoomBounds(view);
  view.$("cam-img").style.transform = `translate(${view.pan.x * bounds.x}px, ${view.pan.y * bounds.y}px) scale(${view.zoom})`;
  view.$("camera-frame").classList.toggle("is-zoomed", view.zoom > 1);
  view.$("camera-zoom-reset").textContent = t("cam.zoomValue", { n: numberFormat(1).format(view.zoom) });
  view.$("camera-zoom-out").disabled = view.zoom === 1;
  view.$("camera-zoom-in").disabled = view.zoom === 4;
  view.$("camera-frame").setAttribute("aria-label", `${view.state?.name || view.id}. ${t("cam.zoomHint")}`);
  view.$("camera-frame").title = t("cam.zoomHint");
}

function bindCameraZoom(view) {
  view.$("camera-zoom").hidden = view.id !== "usb";
  if (view.id !== "usb") return;
  const frame = view.$("camera-frame");
  frame.tabIndex = 0;
  view.$("cam-img").draggable = false;
  view.$("camera-zoom-in").onclick = () => applyCameraZoom(view, view.zoom + .5);
  view.$("camera-zoom-out").onclick = () => applyCameraZoom(view, view.zoom - .5);
  view.$("camera-zoom-reset").onclick = () => applyCameraZoom(view, 1);
  frame.onpointerdown = event => {
    if (view.zoom === 1 || event.button !== 0 || event.target.closest("button")) return;
    event.preventDefault();
    view.drag = { id: event.pointerId, x: event.clientX, y: event.clientY, pan: { ...view.pan } };
    frame.setPointerCapture(event.pointerId);
    frame.classList.add("is-dragging");
  };
  frame.onpointermove = event => {
    if (!view.drag || view.drag.id !== event.pointerId) return;
    const bounds = cameraZoomBounds(view);
    view.pan.x = bounds.x ? Math.max(-1, Math.min(1, view.drag.pan.x + (event.clientX - view.drag.x) / bounds.x)) : 0;
    view.pan.y = bounds.y ? Math.max(-1, Math.min(1, view.drag.pan.y + (event.clientY - view.drag.y) / bounds.y)) : 0;
    applyCameraZoom(view);
  };
  const endDrag = () => { view.drag = null; frame.classList.remove("is-dragging"); };
  frame.onpointerup = frame.onpointercancel = frame.onlostpointercapture = endDrag;
  frame.onkeydown = event => {
    if (event.target !== frame) return;
    if (event.key === "+" || event.key === "=") applyCameraZoom(view, view.zoom + .5);
    else if (event.key === "-" || event.key === "_") applyCameraZoom(view, view.zoom - .5);
    else if (event.key === "0") applyCameraZoom(view, 1);
    else if (view.zoom > 1 && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      const axis = event.key === "ArrowLeft" || event.key === "ArrowRight" ? "x" : "y";
      view.pan[axis] = Math.max(-1, Math.min(1, view.pan[axis] + (["ArrowLeft", "ArrowUp"].includes(event.key) ? .15 : -.15)));
      applyCameraZoom(view);
    } else return;
    event.preventDefault();
  };
  view.zoomObserver = new ResizeObserver(() => applyCameraZoom(view));
  view.zoomObserver.observe(frame);
  applyCameraZoom(view);
}

function createCameraView(id) {
  const root = cameraViews.size === 0 && $("card-cam")?.hidden ? $("card-cam") : cameraTemplate.cloneNode(true);
  const elements = {};
  const suffix = root.id === "card-cam" && root.isConnected ? "" : `-${++cameraViewNumber}`;
  for (const el of [root, ...root.querySelectorAll("[id]")]) {
    elements[el.id] = el;
    el.id += suffix;
  }
  root.dataset.camera = id;
  applyStatic(root);
  // The legacy section anchor belongs to the first card only.
  if (suffix) root.removeAttribute("data-sec");
  const view = { id, root, $: key => elements[key], live: false, wanted: CAMERA_LIVE_DEFAULT,
    timer: null, blankKey: "", wasRecording: null, state: null, recordingBusy: false,
    zoom: 1, pan: { x: 0, y: 0 }, drag: null };
  root.setAttribute("aria-labelledby", view.$("cam-name").id);
  view.$("camera-freshness").classList.add("camera-freshness");
  view.$("cam-img").onload = () => { camBlank("", view); applyCameraZoom(view); renderCameraProgress(); };
  view.$("cam-img").onerror = () => {
    if (view.live) {
      setLive(false, view);
      setFb(view.$("fb-cam").id, t("cam.liveLost"), "bad");
    } else camBlank("cam.noImage", view);
    renderCameraProgress();
  };
  for (const button of view.$("cam-mode").children) {
    button.onclick = () => {
      view.wanted = button.dataset.mode === "live";
      if (view.wanted !== view.live) setLive(view.wanted, view);
    };
  }
  view.$("cam-rec").onclick = () => recordCamera(view);
  view.$("cam-hide").onclick = () => setCameraHidden(id, true, view);
  view.$("camera-fullscreen").onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (view.$("camera-player").requestFullscreen) await view.$("camera-player").requestFullscreen();
      else if (view.$("cam-img").webkitEnterFullscreen) view.$("cam-img").webkitEnterFullscreen();
      else setFb(view.$("fb-cam").id, t("cockpit.fullscreenUnavailable"), "bad");
    } catch (_) { setFb(view.$("fb-cam").id, t("cockpit.fullscreenUnavailable"), "bad"); }
  };
  bindCameraZoom(view);
  camSeg(view);
  cameraViews.set(id, view);
  $("camera-views").append(root);
  return view;
}

function dur(sec) {
  const s = Math.max(0, sec || 0), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(r).padStart(2, "0")}`;
}

function renderCameras(s) {
  const sources = cameraSources(s);
  for (const [id, view] of cameraViews) {
    if (sources.some(cam => cam.id === id)) continue;
    view.root.hidden = true;
    setLive(false, view);
    view.zoomObserver?.disconnect();
    if (view.root.id !== "card-cam") view.root.remove();
    cameraViews.delete(id);
  }
  for (const cam of sources) renderCamera(cam, cameraViews.get(cam.id) || createCameraView(cam.id));
  const host = $("camera-views");
  sources.forEach((cam, index) => {
    const root = cameraViews.get(cam.id).root;
    if (host.children[index] !== root) host.insertBefore(root, host.children[index] || null);
  });
  $("camera-views").classList.toggle("multiple", sources.length > 1);
  renderHiddenCameras(s);
  refreshCameraPlayback();
  renderCameraProgress();
}

function renderCamera(cam, view) {
  view.state = cam;
  view.root.hidden = false;
  view.$("cam-name").textContent = cam.name || cam.id;
  view.$("cam-role").textContent = cam.id === "growcam" ? t("cam.roleHeight") : t("cam.roleDetail");
  view.$("cam-img").alt = `${cam.name || cam.id}: ${t("cam.alt")}`;
  applyCameraZoom(view);
  const off = cam.reachable === false;
  view.$("cam-offline").hidden = !off;
  view.$("cam-hide").hidden = !off;  // only a camera without pictures offers to go away
  if (off) {
    const min = Math.round((cam.offline_seconds || 0) / 60);
    // USB: no power supply to ask about, and the clock restarts with the server, so the server's own reason instead
    view.$("cam-offline").textContent = cam.source === "usb" ? cam.error || t("cam.noImage") : min ? t("cam.offlineFor", { n: amount(min) }) : t("cam.offline");
    if (view.live) setLive(false, view);
    camBlank("cam.noImage", view);
  }
  const rec = cam.recording && !cam.recording.ended ? cam.recording : null;
  view.$("cam-rec").classList.toggle("on", !!rec);
  view.$("cam-rec-t").textContent = rec ? t("cam.stop", { time: dur(rec.seconds) }) : t("cam.start");
  view.$("cam-badge").hidden = !rec;
  view.$("cam-badge-t").textContent = rec ? dur(rec.seconds) : "";
  if (view.wasRecording !== !!rec) { view.wasRecording = !!rec; loadCameraRecordings(view); }
}

async function recordCamera(view) {
  if (view.recordingBusy) return;
  view.recordingBusy = true;
  const recording = view.$("cam-rec").classList.contains("on");
  view.$("cam-rec").disabled = true;
  setFb(view.$("fb-cam").id, t(recording ? "cam.stopping" : "cam.starting"));
  try {
    const res = await api("/api/cmd", { action: recording ? "record_stop" : "record_start", camera: view.id });
    setFb(view.$("fb-cam").id, recording ? t("cam.saved", { name: recLabel(res.file || "") }) : t("cam.recording"), "ok");
    await poll(true);
    loadCameraRecordings(view);
  } catch (err) {
    setFb(view.$("fb-cam").id, err.message, "bad");
  } finally {
    view.recordingBusy = false;
    view.$("cam-rec").disabled = false;
  }
}

function recLabel(name) {  // "2026-10-22_14-05-09.mp4": the recording's start, written the page's way
  const m = name.match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})/);
  if (!m) return name;
  return new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5])
    .toLocaleString(LOCALE[LANG], { day: DAY[LANG], month: MONTH[LANG], year: "numeric", hour: HOUR[LANG], minute: "2-digit" });
}

async function loadRecordings() {
  await Promise.all([...cameraViews.values()].map(loadCameraRecordings));
}

async function loadCameraRecordings(camera) {
  let data;
  try { data = await api(cameraUrl("/api/recordings", camera)); } catch (_) { return; }
  if (cameraViews.get(camera.id) !== camera) return;
  const list = camera.$("recs");
  list.replaceChildren();
  for (const r of data.recordings || []) {
    const li = document.createElement("li");
    const when = document.createElement("div");
    when.className = "when";
    when.textContent = recLabel(r.name);
    const small = document.createElement("small");
    small.textContent = r.active ? t("rec.active") : `${numberFormat(1, true).format(r.mb)} MB`;
    when.appendChild(small);
    li.appendChild(when);
    if (!r.active) {
      const view = document.createElement("a");
      view.href = cameraUrl(`/recordings/${encodeURIComponent(r.name)}`, camera);
      view.target = "_blank";
      view.rel = "noopener";
      view.textContent = t("rec.view");
      const del = document.createElement("button");
      del.textContent = t("rec.delete");
      del.onclick = () => {
        if (!del.classList.contains("arm")) {  // first tap arms, second tap deletes
          del.classList.add("arm");
          del.textContent = t("rec.confirmDelete");
          setTimeout(() => { del.classList.remove("arm"); del.textContent = t("rec.delete"); }, 4000);
          return;
        }
        api("/api/cmd", { action: "record_delete", name: r.name, camera: camera.id })
          .then(() => loadCameraRecordings(camera))
          .catch((err) => setFb(camera.$("fb-cam").id, err.message, "bad"));
      };
      li.append(view, del);
    }
    list.appendChild(li);
  }
}

// ---------- controls ----------

function renderLight(l) {
  const on = (l.level || 0) > 0;
  $("light-val").textContent = l.level == null ? "–" : on ? qty(amount(l.level), "%") : t("ctl.off");
  const locked = !!l.in_plan && !lightUnlocked;
  $("light-plan").hidden = !l.in_plan;
  const tog = $("light-toggle");
  tog.setAttribute("aria-pressed", String(on));
  tog.textContent = t(on ? "ctl.on" : "ctl.off");
  tog.disabled = locked || l.level == null;
  tog.classList.toggle("is-locked", locked);
  const r = $("light-range");
  r.disabled = locked || l.level == null;
  r.classList.toggle("is-locked", locked);
  if (!dragging && on) r.value = l.level;
  const specLocked = !l.spectrum_writable;
  $("spec-seg").classList.toggle("is-locked", specLocked);
  for (const b of $("spec-seg").children) {
    b.classList.toggle("on", b.dataset.spec === l.spectrum);
    b.disabled = specLocked;
  }
  $("spec-hint").hidden = !specLocked;
  if (l.in_plan && !lightUnlocked && !$("unlock-light")) {
    const btn = document.createElement("button");
    btn.className = "btn sm"; btn.id = "unlock-light"; btn.textContent = t("light.unlock");
    btn.onclick = () => { lightUnlocked = true; btn.remove(); render(state); };
    $("light-plan").appendChild(btn);
  }
}

function renderFan(kind, f) {
  const step = draft[kind] ?? f.step;
  const nw = kind === "cfan" && f.natural_wind && draft[kind] === null;
  // The heading reports physical state; the bars may preview a requested speed until acknowledgement.
  const actual = f.step;
  $(`${kind}-val`).textContent = kind === "cfan" && f.natural_wind
    ? (actual == null ? t("fan.wind") : t("fan.windStep", { step: actual }))
    : actual == null ? "–" : actual === 0 ? t("ctl.off") : `${actual}/10`;
  const bars = $(`${kind}-bars`);
  if (!bars.children.length) {
    for (let i = 1; i <= 10; i++) {
      const b = document.createElement("i");
      b.style.height = `${30 + i * 7}%`;
      bars.appendChild(b);
    }
  }
  [...bars.children].forEach((b, i) => b.classList.toggle("on", step != null && i < step));
  document.querySelectorAll(`.chips button[data-fan=${kind}]`).forEach((b) => {
    const isNw = b.dataset.nw === "1";
    b.classList.toggle("on", isNw ? nw : !nw && Number(b.dataset.step) === step);
    if (isNw) {
      const locked = f.nw_writable === false;  // VGrow: shown, switched in the VIVOSUN app for now
      b.disabled = locked;
      b.classList.toggle("is-locked", locked);
      b.title = locked ? t("fan.nwTitle") : "";
      $("nw-hint").hidden = !locked;
    }
  });
}

const portArm = {};  // port -> time until the "really off?" button waits for the second tap

function portName(name) { return String(name).replace("usb", "USB "); }

function renderPorts(ports, auto) {
  const box = $("ports");
  box.replaceChildren();
  const names = Object.keys(ports);
  if (!names.length) box.textContent = t("ports.none");
  for (const name of names) {
    const p = ports[name];
    const row = document.createElement("div");
    row.className = "port";
    const label = document.createElement("div");
    label.className = "name";
    label.textContent = portName(name) + (p.label ? ` · ${p.label}` : "");
    const sub = document.createElement("small");
    const byAuto = !!(auto && auto.enabled && auto.port === name);  // "manual" would contradict the Autopilot card
    sub.textContent = t(p.always_on ? "ports.alwaysOn" : p.on == null ? "ports.unknown" : !p.can_write ? "ports.locked"
      : byAuto ? "ports.byAutopilot" : "ports.manual");
    label.appendChild(sub);
    if (name === "usb1" && state?.watering) {
      const wateringLink = document.createElement("a");
      wateringLink.href = "#automation";
      wateringLink.className = "text-link";
      wateringLink.textContent = t("watering.title");
      label.appendChild(wateringLink);
    }
    const btn = document.createElement("button");
    btn.className = "toggle";
    const locked = p.on != null && (!p.can_write || (p.always_on && p.on));
    const armed = !locked && (portArm[name] || 0) > Date.now();
    btn.setAttribute("aria-pressed", String(!!p.on));
    btn.classList.toggle("is-locked", locked);
    btn.classList.toggle("arm", armed);
    btn.textContent = armed ? t("ports.confirmOff") : t(p.on ? "ctl.on" : "ctl.off");
    btn.disabled = locked || p.on == null;
    btn.onclick = () => {
      // Switching off needs a second tap: on 22.10.2026 USB 1 off also cut the camera's power, and whatever hangs on
      // a port now needs an explicit off. USB1 starts go through the server's bounded watering controller.
      if (p.on && !((portArm[name] || 0) > Date.now())) {
        portArm[name] = Date.now() + 4000;
        render(state);
        setTimeout(() => { if (state) render(state); }, 4100);
        return;
      }
      delete portArm[name];
      send({ action: "port", port: name, on: !p.on }, "fb-port");
    };
    row.append(label, btn);
    box.appendChild(row);
  }
  // The humidifier automation lives in the Autopilot card; this card only points there.
  const ptr = $("ports-auto");
  ptr.hidden = !(auto && ports[auto.port]);
  if (!ptr.hidden) {
    const link = document.createElement("a");
    link.href = "#autopilot";
    link.textContent = t("nav.autopilot");
    fill(ptr, "ports.pointer", { state: autoState(auto)[0] }, { link });
  }
}

// ---------- autopilot ----------

let autoArmUntil = 0;  // switching the automation on waits for a second tap until then

// The order the server checks in: stale or missing readings first, then the unconfirmed stage, then a manual pause.
function autoState(a) {
  if (!a.enabled) return [t("auto.off"), ""];
  if (a.stale) return [t("auto.stale"), "bad"];
  if (a.missing) return [t("auto.noProbe"), "bad"];  // fresh data, but no probe humidity to act on
  if (a.needs_stage_confirmation) return [t("auto.waitStage"), "warn"];  // never switches on until the owner confirms
  if (a.paused_until) return [t("auto.pausedUntil", { time: clock(new Date(a.paused_until * 1000)) }), "warn"];
  return [t("auto.active"), "ok"];
}

function ruleRow(id) {
  let li = $(id);
  if (li) return li;
  li = document.createElement("li");
  li.className = "auto-rule";
  li.id = id;
  const lvl = document.createElement("span");
  lvl.className = "lvl";
  lvl.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  const main = document.createElement("div");
  const head = document.createElement("div");
  head.className = "ar-head";
  const title = document.createElement("b");
  const pill = document.createElement("span");
  pill.className = "pill sm";
  head.append(title, pill);
  const sub = document.createElement("p");
  sub.className = "ar-sub";
  main.append(head, sub);
  li.append(lvl, main);
  $("auto-rules").appendChild(li);
  return li;
}

function setRule(li, title, level, [pill, cls], sub) {
  li.dataset.lvl = String(level);
  li.querySelector(".lvl").title = t(LEVELS[level]);
  li.querySelector(".ar-head b").textContent = title;
  const p = li.querySelector(".ar-head .pill");
  p.textContent = pill;
  p.className = `pill sm ${cls}`;
  li.querySelector(".ar-sub").textContent = sub;
}

function autoRow(a) {
  const li = ruleRow("ar-hum");
  let tog = $("auto-hum");
  if (!tog) {
    tog = document.createElement("button");
    tog.type = "button";
    tog.className = "toggle";
    tog.id = "auto-hum";
    tog.onclick = () => {
      const cur = state && state.automation;
      if (!cur) return;
      if (!cur.enabled && Date.now() > autoArmUntil) {  // switching on: confirm that USB 2 is the humidifier
        autoArmUntil = Date.now() + 5000;
        autoRow(cur);
        setTimeout(() => { if (state) autoRow(state.automation); }, 5100);
        return;
      }
      autoArmUntil = 0;
      send({ action: "auto_humidifier", on: !cur.enabled }, "fb-auto");
    };
    li.appendChild(tog);
  }
  // Without a confirmed stage the automation never switches the humidifier on: why, and the suggestion to confirm.
  let wait = $("ar-stage");
  if (!wait) {
    wait = document.createElement("div");
    wait.className = "ar-stage";
    wait.id = "ar-stage";
    const why = document.createElement("p");
    why.className = "ar-why";
    const btn = document.createElement("button");
    btn.id = "auto-stage";
    wait.append(why, btn);
    li.appendChild(wait);
  }
  tog.hidden = !a;
  wait.hidden = !(a && a.needs_stage_confirmation);
  if (!a) {
    setRule(li, t("auto.humTitle"), 0, [t("auto.missing"), ""], "");
    return li;
  }
  const band = [humBand(a), humNeed(a)].filter(Boolean).join(" · ");
  setRule(li, t("auto.humTitle"), a.enabled ? 2 : 0, autoState(a), band);
  const armed = !a.enabled && Date.now() < autoArmUntil;
  tog.classList.toggle("arm", armed);
  tog.setAttribute("aria-pressed", String(!!a.enabled));
  tog.textContent = armed ? t("auto.armOn", { port: portName(a.port || "usb2") }) : t(a.enabled ? "ctl.on" : "ctl.off");
  if (a.needs_stage_confirmation) {
    const why = wait.querySelector(".ar-why");
    if (a.profile_problem) {  // plant.json cannot be read: that, not a missing stage, is why it waits
      why.textContent = t("auto.profileWhy", { error: a.profile_problem });
    } else if (!a.estimate) {  // no stage at all: where to set one
      const link = document.createElement("a");
      link.href = "#card-plant";
      link.textContent = t("nav.plantCard");
      fill(why, "auto.stageWhyNone", null, { link });
    } else {
      // The newest line of the timeline is the pause's own off: say that it switched off, not only "does not switch on".
      const newest = Array.isArray(a.recent) && a.recent.length ? a.recent[0] : null;
      why.textContent = t(newest && newest.action === "stage_pause_off" ? "auto.stageWhyOff" : "auto.stageWhy");
    }
    const btn = $("auto-stage");
    btn.hidden = !a.estimate;
    if (a.estimate) stageButton(btn, "auto", a.estimate, "fb-auto");
  }
  return li;
}

function aiRow(s) {
  const li = ruleRow("ar-ai");
  const v = s.vision;
  if (!v) {
    setRule(li, t("auto.aiTitle"), 1, [t("auto.off"), ""], t("auto.aiNoCam"));
    return;
  }
  const last = v.latest;
  const bits = [t("auto.aiSchedule", { time: hourOfDay(v.photo_hour ?? 12) }),
    last && last.date ? t("auto.aiLast", { time: stamp(new Date(last.date)) }) : t("auto.aiNever")];
  if (v.model) bits.push(v.model);
  bits.push(t("auto.aiOnlySuggest"));
  const pill = v.busy ? [t("auto.aiRunning"), "info"] : v.error ? [t("auto.aiError"), "warn"] : [t("auto.active"), "ok"];
  setRule(li, t("auto.aiTitle"), 1, pill, bits.join(" · "));
}

function alarmRow(s) {
  const li = ruleRow("ar-alarm");
  const rules = s.alerts || [];
  const fired = rules.filter((r) => r.active).length;
  const push = t(s.ntfy === null ? "alerts.ntfyPreview" : s.ntfy ? "alerts.ntfyOk" : "alerts.ntfyNo");
  const noPush = !fired && s.ntfy === false;  // then the pill says it already: not twice in one row
  const pill = fired ? [tn("auto.fired", fired), "bad"] : noPush ? [push, "warn"] : [t("auto.active"), "ok"];
  setRule(li, t("auto.alertsTitle"), 1, pill, [tn("auto.rules", rules.length), noPush ? "" : push].filter(Boolean).join(" · "));
}

// The band line of the humidifier rule. With the VPD raising the floor (vgrow/automation.py humidity_floor: lights on,
// a fresh probe temperature, the humidity at which the air VPD equals the stage's maximum) "on below" names that floor,
// the VPD maximum and the probe temperature it was computed for, next to the card's own minimum.
function humBand(a) {
  const [lo, hi] = a.band || [];
  if (lo == null || hi == null) return t("auto.noBand");
  const f = a.floor || {};
  if (f.reason === "vpd" && f.floor != null && f.temp != null && f.vpd_max != null) {
    return t(f.clamped ? "auto.bandVpdClamped" : "auto.bandVpd", { max: qty(num(hi), "%"), floor: qty(fmt(f.floor, 1), "%"), min: qty(num(lo), "%"),
      vpd: qty(fmt(f.vpd_max, 1), "kPa"), temp: qty(fmt(f.temp, 1), "°C") });
  }
  return t("auto.band", { min: qty(num(lo), "%"), max: qty(num(hi), "%") });
}

// Why the rule wants the humidifier now: "VPD zu hoch" (between the card's minimum and the effective floor) or
// "zu trocken" (below the card's minimum), or nothing.
function humNeed(a) {
  const v = a.vpd || {};
  if (a.need === "vpd_high" && v.value != null && v.band && v.band[1] != null) {
    return t("auto.needVpd", { value: qty(fmt(v.value, 2), "kPa"), max: qty(fmt(v.band[1], 1), "kPa") });
  }
  if (a.need === "too_dry") return t("auto.needDry");
  return "";
}

// A log line of the rule's "on" with its reason: the floor and the temperature it came from (VPD too high), or too dry.
function humOnText(e) {
  const value = qty(fmt(e.value, 1), "%");
  if (e.reason === "vpd_high" && e.floor != null && e.temp != null) {
    return t("auto.action.onVpd", { value, floor: qty(fmt(e.floor, 1), "%"), temp: qty(fmt(e.temp, 1), "°C") });
  }
  if (e.reason === "too_dry") return t("auto.action.onDry", { value });
  return null;
}

function renderAutoLog(a) {
  const recent = a && Array.isArray(a.recent) ? a.recent : a && a.last_action ? [a.last_action] : [];
  const list = $("auto-log");
  if (!recent.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = t(a ? "auto.logEmpty" : "auto.missing");
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(...recent.slice(0, 5).map((e) => {
    const li = document.createElement("li");
    li.className = `ev-${e.action}`;
    const d = typeof e.time === "number" ? new Date(e.time * 1000) : new Date(e.time);
    const time = document.createElement("time");
    if (!Number.isNaN(d.getTime())) { time.dateTime = d.toISOString(); time.textContent = when(d); }
    const text = document.createElement("span");
    const [plain, withValue] = AUTO_ACTION[e.action] || [];
    // One decimal: 44.6 rounded to 45 would read "on at 45 %" under the rule "on below 45 %".
    text.textContent = (e.action === "humidifier_on" && e.value != null && humOnText(e))
      || (withValue && e.value != null ? t(withValue, { value: qty(fmt(e.value, 1), "%") }) : plain ? t(plain) : e.action);
    li.append(time, text);
    return li;
  }));
}

function renderAutopilot(s) {
  renderPilot(s);
  // With the autopilot card the humidifier is its fourth device row and its steps are in the one log there: the old
  // row and the old "Letzte Aktionen" list would show the same switch twice (review 2, S4).
  const pilotShown = !!(s.autopilot && (s.autopilot.boxes || []).length);
  autoRow(s.automation);
  const hum = $("ar-hum");
  if (hum) hum.hidden = pilotShown;
  const oldLog = document.querySelector("#autopilot .auto-log");
  if (oldLog) oldLog.hidden = pilotShown;
  aiRow(s);
  alarmRow(s);
  renderAutoLog(s.automation);
}

// ---------- the autopilot per box (vgrow/autopilot.py): three states, exceptions folded away, the step log ----------

// The server computes and words everything (status, holds, every log line, in the request language); the page only
// lays it out and sends the owner's taps. Off is one tap, Observe one (it switches nothing), Automatic two (the second
// within 5 s). Allowing a device to run against a VIVOSUN plan or automation, naming what hangs on a port and
// switching the humidifier rule on take two taps as well.
const AP_MODES = ["off", "observe", "auto"];
const AP_MODE = { off: "pilot.mode.off", observe: "pilot.mode.observe", auto: "pilot.mode.auto" };
const AP_MODE_DESC = { off: "pilot.modeDesc.off", observe: "pilot.modeDesc.observe", auto: "pilot.modeDesc.auto" };
const AP_MODE_CLS = { off: "", observe: "info", auto: "ok" };
const AP_LEVELS = ["auto", "suggest", "manual"];
const AP_LEVEL = { auto: "auto.level.auto", suggest: "auto.level.suggest", manual: "auto.level.manual" };
const AP_LEVEL_NUM = { auto: 2, suggest: 1, manual: 0 };
const AP_STATUS = { ok: ["pilot.st.ok", "ok"], sent: ["pilot.st.sent", ""], retry: ["pilot.st.retry", "warn"],
  failed: ["pilot.st.failed", "bad"], cancelled: ["pilot.st.cancelled", ""] };
const AP_KIND = { would: "pilot.kind.would", suggest: "pilot.kind.suggest", step: "pilot.kind.step" };
// What the short log lists: what was switched or would have been, the owner's own changes, failures and holds. The
// mode changes and events (mist, pause over) are behind "alle Einträge" (review 2, S4).
const AP_LOG_KINDS = new Set(["step", "would", "suggest", "manual", "undo", "failed", "hold"]);
// A device's state in the design system's five (DeviceCard): [chip colour, place]. Needs you first, then stopped,
// paused, monitoring, off; the word stays the app's own (AP_DEVICE_TEXT).
const AP_CHIP = { needs_setup: ["warn", 0], paused: ["info", 2], pending: ["info", 2], waiting: ["info", 2],
  observe: ["info", 3], ready: ["ok", 3], manual: ["", 4], off: ["", 4] };
const AP_DEVICE_TEXT = { ready: "pilot.device.ready", observe: "pilot.device.observe", needs_setup: "pilot.device.needs",
  waiting: "pilot.device.waiting", paused: "pilot.device.paused", pending: "pilot.device.pending",
  manual: "pilot.device.manual", off: "pilot.device.off" };
// a fail-safe stopped it: "stopped", red
const AP_STOPPED = new Set(["ap.health.hum_implausible"]);
const AP_COUNT_CHIP = { ready: "ok", needs_setup: "warn", stopped: "bad", waiting: "info", paused: "info", pending: "info",
  observe: "info", holding: "info" };
const AP_MODE_NOTE = { off: "pilot.modeNote.off", observe: "pilot.modeNote.observe", auto: "pilot.modeNote.auto" };
// The log's kinds as the Timeline's dots; a change made in the VIVOSUN app is a hold (the autopilot holds back).
const AP_DOT = { step: "step", would: "suggest", suggest: "suggest", undo: "manual", failed: "failed", hold: "hold" };
const AP_PORT_DEVICES = ["humidifier", "pump", "other"];
const AP_PORT_DEV = { humidifier: "pilot.portHumidifier", pump: "pilot.portPump", other: "pilot.portOther" };
const apArm = {};  // "<box>|<what>" -> until (ms): waiting for the second tap
const apOpen = {};  // "<box>|<details>" -> unfolded (kept across renders)
let apDrawn = "";  // what the card was last drawn from: an unchanged answer is not drawn again (review 2, N2)
let apPortrait = null;

function apArmed(key) { return Date.now() < (apArm[key] || 0); }

// The first tap arms for 5 s and redraws; the second runs fn.
function apTwoTap(key, fn) {
  if (!apArmed(key)) { apArmNow(key); return; }
  apArm[key] = 0;
  fn();
}

function apArmNow(key) {
  apArm[key] = Date.now() + 5000;
  if (state) renderPilot(state, true);
  setTimeout(() => { if (state) renderPilot(state, true); }, 5100);
}

// A step from Hank's panel (web/hank.js): what keeps Hank from regulating, opened where it already lives. The tap in
// Hank's panel is the first of the step's two taps: the step is unfolded, armed and in view, and the owner's second
// tap there confirms it, exactly as if he had tapped it there twice. Nothing is sent from here. Returns whether the
// step was found on the page.
const AP_STEP_ARM = { usb2: "port|humidifier", hum: "hum", lamp: "lamp", ports: "ports" };
function pilotStep(boxId, step) {
  const s = state;
  const box = s && s.autopilot && (s.autopilot.boxes || []).find((b) => b.id === boxId);
  if (!box) return false;
  let target = null;
  if (["ports", "stage", "stage_none"].includes(step)) apOpen[`${box.id}|setup`] = true;  // the lamp: its card
  if (step === "stage" && box.needs && box.needs.stage) {
    if (!stageBusy) stageArm.pilot = Date.now() + 4000;
    render(s);
    setTimeout(() => { if (state) render(state); }, 4100);
    target = document.querySelector('#ap-boxes [data-stage-confirm="pilot"]');
  } else if (step === "stage" || step === "stage_none") {
    target = $("card-plant");  // no estimate: the stage is set on the plant card's track
  } else if (AP_STEP_ARM[step]) {
    if (step === "usb2" || step === "hum") apOpen[`${box.id}|exc`] = true;  // both live in the folded exceptions
    const armable = !(step === "hum" && s.automation && s.automation.enabled) &&
      !(step === "usb2" && s.automation && s.automation.port_device === "humidifier" && !s.automation.implausible);
    if (armable) apArmNow(`${box.id}|${AP_STEP_ARM[step]}`); else renderPilot(s, true);
    target = document.querySelector(`#ap-boxes [data-box="${CSS.escape(box.id)}"] [data-ap-step="${step}"]`);
  }
  if (!target) target = $("autopilot");
  if (!target) return false;
  if (globalThis.Cockpit) globalThis.Cockpit.reveal(target);
  target.scrollIntoView({ block: "center", behavior: REDUCED.matches ? "auto" : "smooth" });
  if (target.focus) target.focus({ preventScroll: true });
  target.classList.add("ap-flash");
  setTimeout(() => target.classList.remove("ap-flash"), 2400);
  return true;
}

// ---------- the design system's components (design-system/components) ----------
// The markup design-system/components/bundle.js builds, built here: the page runs its own script and keeps bundle.js
// in the mirror. Styled by web/ds-components.css; cockpit.js and thirst.js use these too.

const DS_SVG = "http://www.w3.org/2000/svg";
const DS_ICON = {
  info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "M12 11v5M12 8h.01"],
  warn: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "M12 8v5M12 16h.01"],
  bad: ["M12 3 2 20h20L12 3Z", "M12 10v4M12 17h.01"],
  ok: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "m8 12 3 3 5-6"],
  chevron: ["m9 6 6 6-6 6"],
};

function dsIcon(name) {
  const svg = document.createElementNS(DS_SVG, "svg");
  for (const [key, value] of [["viewBox", "0 0 24 24"], ["fill", "none"], ["stroke", "currentColor"],
    ["stroke-width", "1.8"], ["stroke-linecap", "round"], ["stroke-linejoin", "round"], ["aria-hidden", "true"]]) {
    svg.setAttribute(key, value);
  }
  for (const d of DS_ICON[name] || []) {
    const path = document.createElementNS(DS_SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

// A StatusChip: the word is the status, the colour repeats it (ok, warn, info, bad, or "" for neutral).
function dsChip(state, text) {
  return apEl("span", state ? `status ${state}` : "status", text);
}

// The Banner in `host`: an icon, a bold lead that names the situation, the reason (warn, bad, info, ok). One per card:
// a second condition is a second sentence of the same banner. The host keeps its other classes.
function dsBanner(host, variant, lead, text) {
  host.classList.remove("bad", "info", "ok");
  host.classList.add("banner");
  if (variant !== "warn") host.classList.add(variant);
  host.setAttribute("role", variant === "bad" ? "alert" : "status");
  const line = apEl("p");
  line.append(apEl("b", "", `${lead}:`), ` ${text}`);
  const body = apEl("div", "banner-body");
  body.append(line);
  host.replaceChildren(dsIcon(variant), body);
}

// A Disclosure in place of a bare <details>: chevron, title, one line naming what is inside, an optional chip.
function dsDisclosure(title, desc, chip) {
  const det = apEl("details", "disclosure");
  const summary = apEl("summary");
  const chev = dsIcon("chevron");
  chev.setAttribute("class", "chev");
  const label = apEl("span", "t");
  label.append(apEl("b", "", title));
  if (desc) label.append(apEl("span", "", desc));
  summary.append(chev, label);
  if (chip) summary.append(dsChip(chip.state, chip.text));
  const body = apEl("div", "disclosure-body");
  det.append(summary, body);
  return { det, body };
}

function apEl(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function apSend(box, body) { send({ ...body, box: box.id }, "fb-auto"); }

function apHead(box) {
  const head = apEl("div", "ap-head");
  head.append(apEl("b", "", box.name || box.id));
  const bits = [];
  if (box.plant) bits.push(box.plant);
  if (box.stage) {
    bits.push(box.stage.set_by === "manual" ? stageShort(box.stage.label)
      : t("pilot.stageEstimate", { stage: stageShort(box.stage.label) }));
  } else bits.push(t("pilot.noStage"));
  if (box.height_cm != null) bits.push(qty(num(box.height_cm), "cm"));
  head.append(apEl("small", "", bits.join(" · ")));
  return head;
}

function apModeSwitch(box) {
  const seg = apEl("div", "seg block ap-mode");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", t("pilot.modes"));
  for (const mode of AP_MODES) {
    const key = `${box.id}|mode|${mode}`;
    const armed = mode === "auto" && mode !== box.mode && apArmed(key);
    // Off while the autopilot runs stops it: that segment reads in bad, like the armed second tap (SegmentedControl)
    const stops = mode === "off" && box.mode === "auto";
    const btn = apEl("button", `${mode === box.mode ? "on" : ""}${armed || stops ? " arm" : ""}`.trim(),
      armed ? t("pilot.armMode", { mode: t(AP_MODE[mode]), again: again() }) : t(AP_MODE[mode]));
    btn.type = "button";
    btn.dataset.mode = mode;
    btn.setAttribute("aria-pressed", String(mode === box.mode));
    btn.disabled = mode !== "off" && box.forced_off;
    btn.onclick = () => {
      if (mode === box.mode) return;
      // Off and Observe switch nothing at the box: one tap. Automatic switches: two (review 2, N3).
      if (mode !== "auto") { apSend(box, { action: "autopilot_mode", mode }); return; }
      apTwoTap(key, () => apSend(box, { action: "autopilot_mode", mode }));
    };
    seg.append(btn);
  }
  return seg;
}

// One thing the autopilot waits for, with the one button that settles it (two taps).
function apNeed(box, what, why, label, armLabel, body, extra) {
  const wrap = apEl("div", "ap-need");
  wrap.append(apEl("p", "ar-why", why));
  for (const node of extra || []) wrap.append(node);
  const key = `${box.id}|${what}`;
  const armed = apArmed(key);
  const btn = apEl("button", `btn sm${armed ? " arm" : " primary"}`, armed ? armLabel : label);
  btn.type = "button";
  btn.dataset.apStep = what;  // Hank's panel opens this step here (pilotStep)
  btn.onclick = () => apTwoTap(key, () => apSend(box, body));
  wrap.append(btn);
  return wrap;
}

function apPortText(entry) {
  if (!entry) return t("pilot.portNone");
  const bits = [entry.label || t("pilot.portUnlabelled")];
  if (entry.accessory != null) bits.push(t("pilot.portCode", { code: entry.accessory }));
  return bits.join(", ");
}

// A changed port assignment: before and after, port by port, and what confirming means for USB 2 (review 1, S8).
function apPortsChange(box, n) {
  const out = [];
  if (n.unreadable) {
    out.push(apEl("p", "ar-sub", t("pilot.portsUnreadable")));
  } else {
    const before = n.confirmed || {};
    const now = n.now || {};
    const list = apEl("ul", "ap-ports");
    for (const port of [...new Set([...Object.keys(before), ...Object.keys(now)])].sort()) {
      if (JSON.stringify(before[port] || null) === JSON.stringify(now[port] || null)) continue;
      list.append(apEl("li", "", t("pilot.portChange", { port: portName(port), before: apPortText(before[port]),
        now: apPortText(now[port]) })));
    }
    if (list.childElementCount) out.push(list);
  }
  out.push(apEl("p", "ar-sub", t("pilot.portsHum", { port: portName((box.humidifier && box.humidifier.port) || "usb2") })));
  return out;
}

function apNeeds(box) {
  const out = [];
  const n = box.needs || {};
  if (n.ports) {
    out.push(apNeed(box, "ports", t("pilot.portsWhy"), t("pilot.portsBtn"),
      t("pilot.portsArm", { again: again() }), { action: "ports_confirm" }, apPortsChange(box, n.ports)));
  }
  if (n.stage) {
    const wrap = apEl("div", "ap-need");
    wrap.append(apEl("p", "ar-why", t("pilot.stageWhy")));
    wrap.append(stageButton(apEl("button"), "pilot", n.stage, "fb-auto"));
    out.push(wrap);
  }
  // The lamp distance is no setup step: the Light card carries it as its button (apLampButton), where the autopilot
  // waits for it (design-system/patterns.md: the confirmation lives in the device's own card).
  return out;
}

// The autopilot asks for the lamp distance once it runs; Hank's panel lists it already while it is off, so the
// card shows the same step then too (one state in both places).
function apLampNeeded(box) {
  return !!((box.needs && box.needs.lamp) || (box.hank && (box.hank.blockers || []).some((x) => x.id === "lamp")));
}

// The Light card's primary button: the distance is measured (two taps; Hank's panel can arm it).
function apLampButton(box) {
  const cm = (box.lamp && box.lamp.min_cm) || 35;
  const key = `${box.id}|lamp`;
  const armed = apArmed(key);
  const btn = apEl("button", `btn ${armed ? "arm" : "primary"}`,
    armed ? t("pilot.lampArm", { cm, again: again() }) : t("pilot.lampBtn", { cm }));
  btn.type = "button";
  btn.dataset.apStep = "lamp";  // Hank's panel opens this step here (pilotStep)
  btn.onclick = () => apTwoTap(key, () => apSend(box, { action: "autopilot_lamp" }));
  return btn;
}

function apPaused(d) {
  return d.paused_until ? clock(new Date(d.paused_until * 1000)) : null;
}

// Allowing the autopilot to write a device that a VIVOSUN plan or automation runs ends that plan or automation (a
// write sets mode 0). For the light that means: no plan any more, the lamp then burns day and night until the plan is
// set again in the app (commit 834f9c5: "Lampe lief 24 h ohne Plan"). So two taps, with that said (review 1, S9).
function apAppToggle(box, d) {
  const key = `${box.id}|app|${d.id}`;
  const armed = !d.app && apArmed(key);
  const wrap = apEl("div", "ap-app");
  wrap.append(apEl("p", "ar-sub", t(d.id === "light" ? "pilot.appLight" : "pilot.appFan")));
  const btn = apEl("button", `btn sm${armed ? " arm" : ""}`,
    d.app ? t("pilot.appOn") : armed ? t("pilot.appArm", { again: again() }) : t("pilot.appOff"));
  btn.type = "button";
  btn.setAttribute("aria-pressed", String(!!d.app));
  btn.onclick = () => {
    if (d.app) { apSend(box, { action: "autopilot_device", device: d.id, app: false }); return; }  // back: one tap
    apTwoTap(key, () => apSend(box, { action: "autopilot_device", device: d.id, app: true }));
  };
  wrap.append(btn);
  return wrap;
}

function apDeviceRow(box, d) {
  const li = apEl("li", "auto-rule ap-dev");
  li.dataset.lvl = String(AP_LEVEL_NUM[d.level] ?? 0);
  const lvl = apEl("span", "lvl");
  lvl.append(apEl("i"), apEl("i"), apEl("i"));
  lvl.title = t(AP_LEVEL[d.level] || "auto.level.manual");
  const main = apEl("div");
  const head = apEl("div", "ar-head");
  head.append(apEl("b", "", d.name));
  if (d.value != null) head.append(apEl("span", "reading", d.id === "light" ? qty(num(d.value), "%") : String(d.value)));
  const paused = apPaused(d);
  if (paused) head.append(dsChip("info", t("pilot.pausedUntil", { time: paused })));  // review 2, S2
  main.append(head);
  if (d.status) main.append(apEl("p", "ar-sub", keepUnits(d.status)));
  const seg = apEl("div", "seg small ap-level");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", t("pilot.levelFor", { device: d.name }));
  for (const level of AP_LEVELS) {
    const btn = apEl("button", level === d.level ? "on" : "", t(AP_LEVEL[level]));
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(level === d.level));
    btn.onclick = () => { if (level !== d.level) apSend(box, { action: "autopilot_device", device: d.id, level }); };
    seg.append(btn);
  }
  main.append(seg);
  if (d.id !== "cfan" || d.app) main.append(apAppToggle(box, d));  // the circulation fan's natural wind is never ended
  li.append(lvl, main);
  return li;
}

// The humidifier as the fourth device: its own rule (the autopilot's switch does not touch it), what hangs on its
// port (the owner names it, review 2, M3), its band and its last switch.
function apHumRow(box, a) {
  const li = apEl("li", "auto-rule ap-dev ap-hum");
  li.dataset.lvl = a.enabled ? "2" : "0";
  const lvl = apEl("span", "lvl");
  lvl.append(apEl("i"), apEl("i"), apEl("i"));
  const main = apEl("div");
  const head = apEl("div", "ar-head");
  const port = a.port || "usb2";
  head.append(apEl("b", "", t("pilot.humTitle", { port: portName(port) })));
  const [pill, cls] = humState(a);
  head.append(dsChip(cls, pill));
  main.append(head);
  main.append(apEl("p", "ar-sub", humBand(a)));
  const need = humNeed(a);
  if (need) main.append(apEl("p", "ar-sub ap-hum-need", need));
  const last = Array.isArray(a.recent) && a.recent.length ? a.recent[0] : null;
  if (last) main.append(apEl("p", "ar-sub", t("pilot.humLast", { what: humAction(last) })));
  // what hangs on the port: two taps for another choice
  const choose = apEl("div", "ap-port");
  choose.append(apEl("p", "ar-sub", t("pilot.portIs", { port: portName(port) })));
  const seg = apEl("div", "seg block small");  // full width: the device names must not break mid-word
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", t("pilot.portIs", { port: portName(port) }));
  for (const dev of AP_PORT_DEVICES) {
    const key = `${box.id}|port|${dev}`;
    // After the plausibility stop the humidifier must be named again (automation.py _plausible): its button is a
    // live choice then, not the pressed one.
    const on = a.port_device === dev && !(dev === "humidifier" && a.implausible);
    const armed = !on && apArmed(key);
    const btn = apEl("button", `${on ? "on" : ""}${armed ? " arm" : ""}`.trim(),
      armed ? t("pilot.portArm", { device: t(AP_PORT_DEV[dev]), again: again() }) : t(AP_PORT_DEV[dev]));
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(on));
    if (dev === "humidifier") btn.dataset.apStep = "usb2";
    btn.onclick = () => {
      if (on) return;
      apTwoTap(key, () => apSend(box, { action: "port_assign", port, device: dev }));
    };
    seg.append(btn);
  }
  choose.append(seg);
  if (!a.port_device) choose.append(apEl("p", "ar-why", t("pilot.portWhy", { port: portName(port) })));
  else if (a.port_device === "humidifier") choose.append(apEl("p", "hint", t("pilot.portHum", { port: portName(port) })));
  main.append(choose);
  // the rule's own switch: on with two taps, off with one
  const key = `${box.id}|hum`;
  const armed = !a.enabled && apArmed(key);
  const tog = apEl("button", `btn sm${armed ? " arm" : ""}`,
    armed ? t("pilot.humArm", { again: again() }) : t(a.enabled ? "pilot.humSwitchOff" : "pilot.humSwitchOn"));
  tog.type = "button";
  tog.setAttribute("aria-pressed", String(!!a.enabled));
  tog.dataset.apStep = "hum";
  tog.onclick = () => {
    if (a.enabled) { send({ action: "auto_humidifier", on: false }, "fb-auto"); return; }
    apTwoTap(key, () => send({ action: "auto_humidifier", on: true }, "fb-auto"));
  };
  main.append(tog);
  main.append(apEl("p", "hint", t("pilot.humNote")));
  li.append(lvl, main);
  return li;
}

function humState(a) {
  if (a.enabled && a.implausible) return [t("pilot.humImplausible"), "bad"];
  const other = ["pump", "other"].includes(a.port_device) || (a.port_hold === "not_humidifier" && !a.port_device);
  if (a.enabled && other) return [t("pilot.humNotHum"), "warn"];
  // the owner's choice counts at once; the rule's own hold note follows at its next poll
  if (a.enabled && a.port_device !== "humidifier") return [t("pilot.humUnassigned"), "warn"];
  if (a.enabled && a.port_hold === "changed") return [t("pilot.humPorts"), "warn"];
  return autoState(a);
}

function humAction(e) {
  const d = typeof e.time === "number" ? new Date(e.time * 1000) : new Date(e.time);
  const [plain, withValue] = AUTO_ACTION[e.action] || [];
  const what = (e.action === "humidifier_on" && e.value != null && humOnText(e))
    || (withValue && e.value != null ? t(withValue, { value: qty(fmt(e.value, 1), "%") }) : plain ? t(plain) : e.action);
  return Number.isNaN(d.getTime()) ? what : `${what} (${when(d)})`;
}

function apSummaryList(box, hum) {
  const parts = (box.devices || []).map((d) => {
    const paused = apPaused(d);
    return paused ? t("pilot.devPaused", { device: d.name, time: paused })
      : `${d.name} ${t(AP_LEVEL[d.level] || "auto.level.manual")}`;
  });
  if (hum) parts.push(t(hum.enabled ? "pilot.humOn" : "pilot.humOff"));
  return parts.join(" · ");
}

// A Disclosure that stays as the owner left it across redraws; one that holds a warn or bad item opens by itself.
function apFold(box, what, title, desc, chip) {
  const fold = dsDisclosure(title, desc, chip);
  const key = `${box.id}|${what}`;
  fold.det.open = apOpen[key] ?? !!(chip && ["warn", "bad"].includes(chip.state));
  fold.det.addEventListener("toggle", () => { apOpen[key] = fold.det.open; });
  return fold;
}

function apExceptions(box, s) {
  const hum = box.humidifier ? s.automation : null;
  const humCls = hum ? humState(hum)[1] : "";
  // a count, as the Disclosure shows it; the long state word stays in the humidifier row below
  const chip = ["warn", "bad"].includes(humCls) ? { state: humCls, text: tn("pilot.count.needs", 1) } : null;
  // the line under the title names what is inside: each device's level and the humidifier rule
  const { det, body } = apFold(box, "exc", t("pilot.exceptions"), apSummaryList(box, hum), chip);
  const list = apEl("ul", "auto-rules ap-devs");
  for (const d of box.devices || []) list.append(apDeviceRow(box, d));
  if (hum) list.append(apHumRow(box, hum));
  body.append(list);
  const lim = box.limits || {};
  const bud = box.budget || {};
  if (lim.dfan) {
    body.append(apEl("p", "hint", t("pilot.limits", { lo: lim.dfan[0], hi: lim.dfan[1],
      light: lim.light_step, pause: lim.pause_min, usedHour: bud.auto_hour ?? 0, usedDay: bud.auto_day ?? 0 })));
  }
  return det;
}

function apDot(e) {
  return e.kind === "manual" ? (e.source === "app" ? "hold" : "manual") : AP_DOT[e.kind] || "";
}

// Repeats of one event within ten minutes are one entry with a count that opens to its members (Timeline): the light
// changed four times in the VIVOSUN app reads as one line. The entries come newest first.
function apGroup(entries) {
  const groups = [];
  for (const e of entries) {
    const last = groups[groups.length - 1];
    const lead = last && last.members[0];
    if (lead && e.kind === "manual" && lead.kind === "manual" && e.device === lead.device &&
        (e.source || "") === (lead.source || "") && lead.at - e.at <= 600) last.members.push(e);
    else groups.push({ members: [e] });
  }
  return groups;
}

// One log entry. `members` are a grouped repeat's entries, newest first (apGroup); cockpit.js's activity list calls it
// with a single entry.
function apLogItem(box, e, members = [e]) {
  const li = apEl("li", `ap-${e.kind} ${apDot(e)}`.trim());
  const d = new Date(e.at * 1000);
  const time = apEl("time", "", when(d));
  time.dateTime = d.toISOString();
  const body = apEl("div", "ap-line");
  if (members.length > 1) {
    const first = new Date(members[members.length - 1].at * 1000);
    time.textContent = `${clock(first)}–${clock(d)}`;
    const fold = apEl("details", "ap-grp");
    const head = apEl("summary", "grp");
    head.append(keepUnits(e.text), " ", dsChip("", `${members.length}×`));
    const list = apEl("div", "ap-grp-members");
    for (const m of members) list.append(apEl("p", "", `${clock(new Date(m.at * 1000))} ${keepUnits(m.text)}`));
    fold.append(head, list);
    body.append(fold);
    li.append(time, body);
    return li;
  }
  if (AP_KIND[e.kind]) body.append(dsChip(e.kind === "step" ? "ok" : "info", t(AP_KIND[e.kind])), " ");
  body.append(apEl("span", "", keepUnits(e.text)));
  if (e.kind === "step" && AP_STATUS[e.status]) {
    const [key, cls] = AP_STATUS[e.status];
    body.append(" ", dsChip(cls, t(key)));
  }
  if (e.undo) {
    const btn = apEl("button", "btn sm ap-undo-btn", t("pilot.undo"));
    btn.type = "button";
    btn.onclick = () => apSend(box, { action: "autopilot_undo", id: e.id });
    body.append(btn);
  }
  li.append(time, body);
  return li;
}

function apLog(box) {
  const wrap = apEl("div", "ap-logwrap");
  wrap.append(apEl("p", "eyebrow", t("pilot.steps")));
  const list = apEl("ol", "timeline ap-log");
  const all = box.log || [];
  const short = apGroup(all.filter((e) => AP_LOG_KINDS.has(e.kind))).slice(0, 6);
  if (!short.length) list.append(apEl("li", "empty", t("pilot.stepsEmpty")));
  for (const group of short) list.append(apLogItem(box, group.members[0], group.members));
  wrap.append(list);
  if (all.length > short.reduce((n, group) => n + group.members.length, 0)) {
    const { det, body } = apFold(box, "log", t("pilot.allEntries", { n: all.length }), t("pilot.allEntriesDesc"));
    const every = apEl("ol", "timeline ap-log");
    for (const group of apGroup(all)) every.append(apLogItem(box, group.members[0], group.members));
    body.append(every);
    wrap.append(det);
  }
  if (box.summary) wrap.append(apEl("p", "hint ap-summary", t("pilot.summary", { text: keepUnits(box.summary.text) })));
  return wrap;
}

function exhaustHolding(d) {
  return d.id === "dfan" && d.control_state === "waiting" && Number.isFinite(d.value) && d.value > 0 &&
    ["ap.hold.vpd_heat", "ap.hold.vpd_no_room", "ap.hold.vpd_humid", "ap.hold.gap", "ap.hold.at_bound",
      "ap.hold.rest", "ap.hold.budget", "ap.hold.watch_high_up", "ap.hold.watch_low_down", "ap.hold.watch_low_up",
      "ap.hold.watch_high_down", "ap.hold.mist", "ap.hold.undone"].includes(d.reason_key);
}

// The health line's chips as [colour, key, count]. A device a fail-safe stopped counts as "stopped", red, as its card
// says (apDeviceState); the server counts it under needs_setup, and the line said "1 braucht dich" beside "Gestoppt".
function apCountChips(box, holding, pump) {
  const health = box.readiness || {};
  const rows = [...(box.devices || []), ...(box.humidifier ? [box.humidifier] : [])];
  const stopped = rows.filter((d) => d.control_state === "needs_setup" && AP_STOPPED.has(d.reason_key)).length;
  const count = { ...health, needs_setup: (health.needs_setup || 0) - stopped, stopped,
    waiting: (health.waiting || 0) - holding, holding };
  if (pump) count[pump.control_state] = (count[pump.control_state] || 0) + 1;  // not the autopilot's, but on its list
  return [["ready", "pilot.count.ready"], ["needs_setup", "pilot.count.needs"], ["stopped", "pilot.count.stopped"],
    ["waiting", "pilot.count.waiting"], ["paused", "pilot.count.paused"], ["pending", "pilot.count.pending"],
    ["observe", "pilot.count.observe"], ["holding", "pilot.count.holding"]]
    .filter(([kind]) => count[kind] > 0).map(([kind, key]) => [AP_COUNT_CHIP[kind], key, count[kind]]);
}

function apBox(box, s) {
  const sec = apEl("section", "ap-box");
  sec.dataset.box = box.id;
  sec.dataset.mode = box.mode;
  const pump = s.grow_plan && s.grow_plan.pump;
  const pumpNeeds = pump && pump.control_state === "needs_setup";
  const state = pumpNeeds && ["ready", "waiting", "observe"].includes((box.readiness || {}).state) ? "attention" : (box.readiness || {}).state;
  sec.dataset.health = state || "waiting";
  sec.append(apHead(box), apModeSwitch(box), apEl("p", "seg-note", t(AP_MODE_NOTE[box.mode] || "pilot.modeNote.off")));
  // the health line: body-strong, with the counts as chips beside it (Card)
  const line = apEl("div", "counts ap-state");
  const health = box.readiness || {};
  const holding = (box.devices || []).filter(exhaustHolding).length;
  const title = { attention: "pilot.health.attention", waiting: "pilot.health.waiting", ready: "pilot.health.ready",
    observe: "pilot.health.observe", off: "pilot.health.off", manual: "pilot.health.manual" };
  line.append(apEl("h4", "ap-health-title", state === "waiting" && holding > 0 && holding === health.waiting
    ? t("pilot.health.holding") : title[state] ? t(title[state]) : keepUnits(box.status)));
  for (const [chip, key, n] of apCountChips(box, holding, pump)) line.append(dsChip(chip, tn(key, n)));
  sec.append(line, apOverview(box, s));
  const needs = apNeeds(box);
  if (needs.length) {
    const { det, body } = apFold(box, "setup", t("pilot.setupChecks"), t("pilot.setupChecksDesc"),
      { state: "warn", text: tn("pilot.count.needs", needs.length) });
    body.append(...needs);
    sec.append(det);
  }
  sec.append(apExceptions(box, s), apLog(box));
  return sec;
}

// A device's state as the design system words it: the chip, its colour and the card's place in the list.
function apDeviceState(d) {
  const kind = d.control_state || "waiting";
  if (kind === "needs_setup" && AP_STOPPED.has(d.reason_key)) {
    return { kind, chip: "bad", rank: 1, text: t("pilot.device.stopped") };
  }
  const [chip, rank] = AP_CHIP[kind] || AP_CHIP.waiting;
  const paused = kind === "paused" ? apPaused(d) : null;
  const text = exhaustHolding(d) ? t("pilot.device.holding")
    : paused ? t("pilot.device.pausedUntil", { time: paused }) : d.short || t(AP_DEVICE_TEXT[kind] || "pilot.device.waiting");
  return { kind, chip, rank, text };
}

// One device as a DeviceCard: name, chip and reading in one row; under it why and what comes next, never the state
// again; the one button the device waits for. A device with nothing to say is one compact row.
function apDeviceCard(box, s, d) {
  const state = apDeviceState(d);
  const switched = d.id === "humidifier" || d.id === "pump";
  const reading = switched ? ((s.ports || {})[d.port || "usb2"] || {}).on : d.value;
  const shown = reading == null ? "\u2013" : d.id === "light" ? qty(num(reading), "%")
    : switched ? t(reading ? "ctl.on" : "ctl.off") : d.app_mode === "wind" ? t("fan.nw") : String(reading);
  const lines = [];
  let action = null;
  if (d.id === "light" && apLampNeeded(box)) {
    lines.push(t("pilot.lampWhy", { cm: (box.lamp && box.lamp.min_cm) || 35 }));
    action = apLampButton(box);
  } else {
    if (exhaustHolding(d)) lines.push(t("pilot.exhaustHolding", { step: d.value }));
    // the server's reason says why it waits or needs you; on target, off or paused it would repeat the chip
    if (d.status && (d.id === "pump" || !["ready", "off", "manual", "paused"].includes(state.kind))) lines.push(keepUnits(d.status));
    if (d.app_mode === "wind") lines.push(t("pilot.naturalWindNext"));
    const blocks = (box.hank && box.hank.blockers) || [];
    const blocker = blocks.find((b) => (b.devices || []).includes(d.id) && b.step && b.label);
    let step = null, label = null;
    if (blocker && state.kind === "needs_setup") { step = blocker.step; label = blocker.label; }
    else if (d.id === "humidifier" && s.automation && s.automation.port_device !== "humidifier") {
      step = "usb2"; label = t("pilot.identifyPort", { port: portName(d.port || "usb2") });
    }
    if (d.id === "pump" && state.kind === "needs_setup") {  // its steps live on the plan's card, right above
      action = apEl("button", "btn soft", s.grow_plan.labels["btn.open"]);
      action.type = "button";
      action.onclick = () => $("grow-plan").scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (step && label) {
      action = apEl("button", "btn soft", label);  // a step Hank asks for, where it lives (pilotStep)
      action.type = "button";
      action.onclick = () => pilotStep(box.id, step);
    }
  }
  const head = [apEl("b", "", d.name), dsChip(state.chip, state.text), apEl("span", "reading", shown)];
  const compact = !lines.length && !action;
  const card = apEl("article", compact ? "device compact"
    : `device${state.chip === "warn" ? " attention" : ""}${state.chip === "bad" ? " stopped" : ""}`);
  card.dataset.controlState = state.kind;
  card.dataset.device = d.id;
  if (compact) {
    card.append(...head);
    return { rank: state.rank, card };
  }
  const row = apEl("div", "device-h");
  row.append(...head);
  card.append(row, ...lines.map((text) => apEl("p", "", text)));
  if (d.id === "humidifier") {
    const a = s.automation;
    const [lo, hi] = a.band || [];
    const floor = a.floor && a.floor.floor != null ? a.floor.floor : lo;
    if (floor != null && hi != null) card.append(apEl("p", "rule", t("auto.cycle", {
      min: qty(fmt(floor, 1), "%"), max: qty(fmt(hi, 1), "%")
    })));
  }
  if (action) card.append(action);
  return { rank: state.rank, card };
}

function apOverview(box, s) {
  const list = apEl("div", "ap-overview");
  const rows = [...(box.devices || [])];
  if (box.humidifier && s.automation) rows.push({ ...box.humidifier, id: "humidifier", name: t("pilot.device.humidifier") });
  if (s.grow_plan && s.grow_plan.pump) rows.push(s.grow_plan.pump);
  // needs you, stopped, paused, monitoring, off; within one state the box's own order stays
  const cards = rows.map((d) => apDeviceCard(box, s, d)).sort((a, b) => a.rank - b.rank);
  list.append(...cards.map((c) => c.card));
  return list;
}

// Every box with its plant and stage in view (one today); a box that is gone from the answer leaves the page. The
// card is drawn again only when the answer or an armed button changed: a redraw every 5 s took the keyboard and
// screen-reader focus away (review 2, N2). force: an armed button changed.
function renderPilot(s, force) {
  const host = $("ap-boxes");
  if (!host) return;
  const boxes = (s.autopilot && s.autopilot.boxes) || [];
  const now = Date.now();
  const armed = Object.keys(apArm).filter((k) => apArm[k] > now);
  const drawn = JSON.stringify([boxes, s.automation || null, s.grow_plan && s.grow_plan.pump, s.lang || "", document.documentElement.lang, armed,
    stageArm.pilot > now, stageBusy]);
  if (!force && drawn === apDrawn) return;
  apDrawn = drawn;
  host.hidden = !boxes.length;
  if (!apPortrait && window.HankScenes && $("ap-hank-art")) {
    try {
      apPortrait = window.HankScenes.mount($("ap-hank-art"), { size: "head", loops: 1 });
      apPortrait.allowChores(false);
      apPortrait.setActive(false);
    } catch (_) { apPortrait = null; }
  }
  // The old stage line of the humidifier row would repeat the autopilot's own "confirm the stage" (review 2, S4).
  const stageWait = $("ar-stage");
  if (stageWait && boxes.some((b) => b.needs && b.needs.stage)) stageWait.hidden = true;
  host.replaceChildren(...boxes.map((b) => apBox(b, s)));
}

function renderPending(pending) {
  const shown = new Set();
  for (const [field, p] of Object.entries(pending)) {
    const id = FB[field.split(".")[0]];
    if (!id) continue;
    const [key, cls] = PENDING[p.status] || ["", ""];
    setFb(id, key ? t(key) : "", cls);
    $(id).dataset.src = "pending";
    shown.add(id);
  }
  // Outcome texts disappear once the server drops them; error texts from a refused send stay.
  for (const el of document.querySelectorAll(".fb[data-src=pending]")) {
    if (!shown.has(el.id)) { setFb(el.id, ""); delete el.dataset.src; }
  }
}

function renderAlerts(s) {
  const pill = $("ntfy-pill");
  if (s.ntfy === null) {  // preview artifact: it cannot reach ntfy, so it does not pretend to
    pill.className = "pill sm";
    pill.textContent = t("alerts.onlyReal");
  } else {
    pill.className = "pill sm " + (s.ntfy ? "ok" : "warn");  // the same colour as in the Autopilot card
    pill.textContent = t(s.ntfy ? "alerts.ntfyOk" : "alerts.ntfyNo");
  }
  $("test-push").disabled = !s.ntfy;  // without a push target the test can only fail
  const list = $("rules");
  list.replaceChildren();
  const unit = { temp: "°C", humidity: "%", water_level: "%", probe_temp: "°C", probe_humidity: "%" };
  for (const r of s.alerts || []) {
    const li = document.createElement("li");
    const what = document.createElement("div");
    what.className = "what";
    what.textContent = r.label;
    const sub = document.createElement("small");
    const minutes = amount(r.for_minutes);
    // limit is the threshold that applies now; a stage-following one also says which stage it comes from.
    sub.textContent = r.op ? t("alerts.rule", { op: r.op, limit: qty(num(r.limit), unit[r.metric] || ""), minutes })
      : t("alerts.ruleNoLimit", { minutes });
    what.appendChild(sub);
    const from = r.stage_limit;
    if (from && r.op) {
      // "target at most 60% plus 10 points = 70%": the margin with its unit, and the stage marked while estimated.
      const u = unit[r.metric] || "";
      const why = document.createElement("small");
      why.textContent = t("alerts.stageLimit", {
        stage: from.set_by === "manual" ? from.label : t("stage.estimated", { stage: from.label }),
        max: qty(num(from.max), u),
        margin: u === "%" ? t("alerts.marginPoints", { n: num(from.margin) }) : qty(num(from.margin), u),
        limit: qty(num(from.limit ?? r.limit), u) });
      what.appendChild(why);
    }
    const st = document.createElement("span");
    st.className = "pill sm " + (r.active ? "bad" : "ok");
    st.textContent = t(r.active ? "alerts.active" : "check.ok");
    li.append(what, st);
    list.appendChild(li);
  }
}

// ---------- offline: control cards pause instead of pretending ----------

const CONTROL_CARDS = ["card-light", "card-cfan", "card-dfan", "card-ports"];

function pauseControls(on, text = "") {
  $("offline-banner").hidden = !on;
  $("offline-banner").textContent = on ? text : "";
  for (const id of CONTROL_CARDS) {
    const card = $(id);
    card.classList.toggle("paused", on);
    let pill = card.querySelector(".paused-pill");
    if (on) {
      if (!pill) {
        pill = document.createElement("span");
        pill.className = "pill sm paused-pill";
        card.querySelector(".card-h h3").after(pill);
      }
      pill.textContent = t("ctl.paused");
    } else if (pill) {
      pill.remove();
    }
    for (const el of card.querySelectorAll("button, input, select")) {
      if (on && !el.disabled) { el.disabled = true; el.dataset.paused = "1"; }
      else if (!on && el.dataset.paused) { el.disabled = false; delete el.dataset.paused; }
    }
  }
}

function offlineText(s) {
  const sec = s.sensors_age ?? s.shadow_age;
  if (sec == null) return t("offline.bannerNoTime");
  const min = Math.max(1, Math.round(sec / 60));
  return t("offline.banner", { time: min < 90 ? t("dur.min", { n: amount(min) }) : t("dur.h", { n: amount(Math.round(min / 60)) }) });
}

// Page routing lives in cockpit.js; data rendering never switches pages.

// ---------- history ----------

function series(key) {
  return history.map((r) => (r[key] == null ? null : r[key]));
}

function pick(...keys) {
  for (const k of keys) if (history.some((r) => r[k] != null)) return series(k);
  return history.map(() => null);
}

function spark(id, data, color) {
  const svg = $(id);
  const vals = data.filter((v) => v != null);
  if (vals.length < 2) { svg.replaceChildren(); return; }
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const w = 100, h = 26;
  const pts = data.map((v, i) => v == null ? null : `${(i / (data.length - 1)) * w},${h - 3 - ((v - lo) / span) * (h - 6)}`)
    .filter(Boolean).join(" ");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.innerHTML = `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
}

function drawSparks() {  // each sparkline shows the same sensor as its big number
  const src = (key, probe, ...box) => (tileAtPlant[key] && history.some((r) => r[probe] != null) ? series(probe) : pick(...box));
  spark("s-temp", src("temp", "pTemp", "inTemp", "bTemp", "pTemp"), css("--temp"));
  spark("s-hum", src("hum", "pHumi", "inHumi", "bHumi", "pHumi"), css("--hum"));
  spark("s-vpd", src("vpd", "pVpd", "inVpd", "bVpd", "pVpd"), css("--vpd"));
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function timeLabel(ts, withDay) {
  const d = new Date(ts * 1000);
  return withDay ? `${d.toLocaleDateString(LOCALE[LANG], { weekday: "short" })} ${clock(d)}` : clock(d);
}

function tickLabel(ts, withDay) {  // a midnight tick on the 7-day axis needs only its weekday, not "12:00 AM"
  const d = new Date(ts * 1000);
  return withDay && !d.getHours() && !d.getMinutes() ? d.toLocaleDateString(LOCALE[LANG], { weekday: "short" }) : timeLabel(ts, withDay);
}

let measure = null;
function textWidth(text, font) {
  measure = measure || document.createElement("canvas").getContext("2d");
  measure.font = font;
  return measure.measureText(text).width;
}

const AXIS_W = 42;    // every y axis, and the empty side of a one-axis chart: the plot areas line up
const UNIT_PAD = 24;  // room above the plot for the unit labels, clear of a tick label at the very top
const legendDefs = {};

function fontFamily() {
  return getComputedStyle(document.body).fontFamily;
}

// Every target band of a chart sits at the same height on its own scale, so one strip is the target for each line.
function alignBands(bands, defs, data) {
  if (!bands.length) return {};
  let below = 0.6, above = 0.6;  // free space under and over the band, in band widths
  for (const b of bands) {
    const w = b.max - b.min;
    const vals = [];
    defs.forEach((d, i) => { if (d.scale === b.scale) for (const v of data[i + 1]) if (v != null) vals.push(v); });
    if (!vals.length) continue;
    below = Math.max(below, (b.min - Math.min(...vals)) / w + 0.15);
    above = Math.max(above, (Math.max(...vals) - b.max) / w + 0.15);
  }
  return Object.fromEntries(bands.map((b) => {
    const w = b.max - b.min;
    return [b.scale, { range: [b.min - below * w, b.max + above * w] }];
  }));
}

function drawBands(u, bands) {
  if (!bands.length) return;
  const b = bands[0];  // aligned: all bands of the chart cover the same pixels
  const top = u.valToPos(b.max, b.scale, true), bottom = u.valToPos(b.min, b.scale, true);
  const { left, width } = u.bbox;
  const px = Math.max(1, Math.round(u.pxRatio || devicePixelRatio || 1));
  const ctx = u.ctx;
  ctx.save();
  ctx.fillStyle = css("--accent-soft");
  ctx.fillRect(left, top, width, bottom - top);
  ctx.globalAlpha = 0.55;
  ctx.fillStyle = css("--accent");
  ctx.fillRect(left, top, width, px);
  ctx.fillRect(left, bottom - px, width, px);
  ctx.restore();
}

function drawUnits(u, axes) {
  const ctx = u.ctx, pr = u.pxRatio || devicePixelRatio || 1;
  ctx.save();
  ctx.fillStyle = css("--muted");
  ctx.font = `600 ${Math.round(11 * pr)}px ${fontFamily()}`;
  ctx.textBaseline = "top";
  for (const a of axes) {
    const right = a.side === 1;
    ctx.textAlign = right ? "left" : "right";
    ctx.fillText(a.unit, right ? u.bbox.left + u.bbox.width + 6 * pr : u.bbox.left - 6 * pr, 1 * pr);
  }
  ctx.restore();
}

// Tick labels carry the decimals of the tick step, the same on every tick: 1,00 / 1,25 / 1,50, not 1 / 1,25 / 1,5.
function stepDecimals(splits) {
  const step = splits.length > 1 ? Math.abs(splits[1] - splits[0]) : 1;
  const text = String(+step.toFixed(6));
  const dot = text.indexOf(".");
  return dot < 0 ? 0 : text.length - dot - 1;
}

const chartRange = {};  // each chart's own hours: the water dashboard's range is not the analytics page's

function drawChart(id, seriesDefs, data, axes, scales = {}, bands = [], range = hours) {
  const el = $(id);
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
  el.replaceChildren();
  const empty = !data[0].length || data.slice(1).every((s) => s.every((v) => v == null));
  el.hidden = empty;
  if (empty) { $(`lg-${id}`).replaceChildren(); return; }
  const muted = css("--muted");
  const grid = { stroke: css("--line"), width: 1 };
  const font = `12px ${fontFamily()}`;
  const withDay = range > 24;
  chartRange[id] = range;
  const hasRight = axes.some((a) => a.side === 1);
  // Time labels differ in length by language ("12:00", "12:00 PM"): ticks stay at least one label apart.
  const xSpace = Math.max(50, Math.ceil(textWidth(timeLabel(new Date(2026, 0, 7, 12).getTime() / 1000, withDay), font)) + 18);
  legendDefs[id] = seriesDefs;
  charts[id] = new uPlot({
    width: el.clientWidth || el.parentElement.clientWidth - 28,
    height: 170,
    padding: [UNIT_PAD, hasRight ? 0 : AXIS_W, 0, 0],
    series: [{ label: t("chart.time"), value: (u, v) => (v == null ? "–" : timeLabel(v, withDay)) }, ...seriesDefs],
    axes: [
      // The time labels sit a little lower, clear of the lowest y label in the corner ("0" next to "8:00 PM"),
      // and a label that would hang past either end of the plot is left out (its gridline stays).
      { font, stroke: muted, grid, ticks: { show: false }, size: 32, gap: 10, space: xSpace,
        values: (u, splits) => {
          const width = u.bbox.width / (u.pxRatio || devicePixelRatio || 1);
          return splits.map((v) => {
            const label = tickLabel(v, withDay), half = textWidth(label, font) / 2, x = u.valToPos(v, "x");
            return x < half || x > width - half ? null : label;
          });
        } },
      ...axes.map((a) => ({
        scale: a.scale, side: a.side ?? 3, size: AXIS_W, gap: 6, font, stroke: muted,
        grid: { ...grid, show: a.side !== 1 }, ticks: { show: false },
        ...(a.splits ? { splits: () => a.splits } : {}),
        values: (u, splits) => { const d = stepDecimals(splits); return splits.map((v) => fmt(v, d)); },
      })),
    ],
    scales: { x: { time: true }, ...scales, ...alignBands(bands, seriesDefs, data) },
    // Own chips below on every device: latest values without hover, dashed probe samples, the band in one row.
    legend: { show: false },
    cursor: { drag: { x: false, y: false } },
    hooks: {
      drawClear: [(u) => drawBands(u, bands)],
      draw: [(u) => drawUnits(u, axes)],
      setCursor: [(u) => legendValues(id, u)],
    },
  }, data, el);
  buildLegend(id, seriesDefs, bands);
}

function buildLegend(id, defs, bands) {
  const at = document.createElement("span");  // the time under the cursor, shown while hovering or tapping
  at.className = "lg at";
  at.hidden = true;
  const items = [at];
  defs.forEach((d, i) => {
    const el = document.createElement("span");
    el.className = "lg";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 18 6");
    svg.setAttribute("aria-hidden", "true");
    const ln = document.createElementNS("http://www.w3.org/2000/svg", "line");
    const attrs = { x1: 1, y1: 3, x2: 17, y2: 3, stroke: d.stroke, "stroke-width": 2, "stroke-linecap": "round" };
    for (const [k, v] of Object.entries(attrs)) ln.setAttribute(k, v);
    if (d.dash) ln.setAttribute("stroke-dasharray", "4 3");
    svg.appendChild(ln);
    const name = document.createElement("span");
    name.textContent = d.label;
    const val = document.createElement("b");
    val.dataset.i = String(i + 1);
    el.append(svg, name, val);
    items.push(el);
  });
  if (bands.length) {
    const el = document.createElement("span");
    el.className = "lg";
    const swatch = document.createElement("i");
    swatch.className = "band";
    el.append(swatch, bands[0].label || t("chart.target"));
    items.push(el);
  }
  $(`lg-${id}`).replaceChildren(...items);
  legendValues(id, charts[id]);
}

// Chips show the latest value, or the value under a tapped point together with its time.
function legendValues(id, u) {
  const box = $(`lg-${id}`);
  const defs = legendDefs[id];
  if (!u || !box || !defs) return;
  const idx = u.cursor.idx;
  for (const b of box.querySelectorAll("b[data-i]")) {
    const i = Number(b.dataset.i);
    const col = u.data[i] || [];
    let v = idx != null ? col[idx] : null;
    if (idx == null) for (let k = col.length - 1; k >= 0; k--) if (col[k] != null) { v = col[k]; break; }
    if (defs[i - 1]) b.textContent = defs[i - 1].value(u, v);
  }
  const at = box.querySelector(".at");
  if (at) {
    at.hidden = idx == null;
    if (idx != null) at.textContent = t("chart.at", { time: timeLabel(u.data[0][idx], chartRange[id] > 24) });
  }
}

function drawHistory() {
  const xs = history.map((r) => r.time);
  const has = (k) => history.some((r) => r[k] != null);
  const mainIsProbe = !has("inTemp") && !has("bTemp");
  const main = t(mainIsProbe ? "chart.probe" : "chart.box");
  const probe = t("chart.probe");
  const temp = pick("inTemp", "bTemp", "pTemp");
  const hum = pick("inHumi", "bHumi", "pHumi");
  const vpd = pick("inVpd", "bVpd", "pVpd");
  const ptemp = mainIsProbe ? null : series("pTemp");
  const phum = mainIsProbe ? null : series("pHumi");
  const pvpd = mainIsProbe ? null : series("pVpd");
  // Only 0..100 is a level; the placeholder 200 (raw 20000) must not draw an empty-looking chart.
  // A wet/dry sensor (type 9) or a type not decoded yet has no level at all: its dry 0 is not "0 % water".
  const waterKind = state && state.climate ? state.climate.water_kind : undefined;
  const noLevel = waterKind === "wet_dry" || waterKind === null;
  const water = series("waterLv").map((v) => (!noLevel && v != null && v >= 0 && v <= 100 ? v : null));
  const wtemp = series("waterT");
  const line = (name, src, color, scale, unit) => ({
    label: src ? `${name} · ${src}` : name, stroke: color, width: 2, scale, spanGaps: false,
    value: (u, v) => (v == null ? "–" : qty(readingText(v, unit), unit)),  // 61 %, as on the tiles
  });
  const dashed = (s) => ({ ...s, dash: [5, 4], width: 1.5 });
  const climSeries = [line(t("chart.temp"), main, css("--temp"), "c", "°C"), line(t("chart.hum"), main, css("--hum"), "%", "%")];
  const climData = [xs, temp, hum];
  const vpdSeries = [line(t("chart.vpd"), main, css("--vpd"), "k", "kPa")];
  const vpdData = [xs, vpd];
  const probeLines = !!(ptemp && ptemp.some((v) => v != null));
  if (probeLines) {
    climSeries.push(dashed(line(t("chart.temp"), probe, css("--temp"), "c", "°C")), dashed(line(t("chart.hum"), probe, css("--hum"), "%", "%")));
    climData.push(ptemp, phum);
    vpdSeries.push(dashed(line(t("chart.vpd"), probe, css("--vpd"), "k", "kPa")));
    vpdData.push(pvpd);
  }
  // The phase targets hold at the plant, so a band is only drawn against a probe line.
  const band = (metric, scale) => {
    if (!probeLines && !mainIsProbe) return [];
    const r = ((state && state.plant && state.plant.checks) || [])
      .find((c) => c.metric === metric && c.min != null && c.max != null && c.max > c.min);
    return r ? [{ scale, min: r.min, max: r.max }] : [];
  };
  drawChart("ch-climate", climSeries, climData, [{ scale: "c", unit: "°C" }, { scale: "%", unit: "%", side: 1 }], {},
    [...band("probe_temp", "c"), ...band("probe_humidity", "%")]);
  drawChart("ch-vpd", vpdSeries, vpdData, [{ scale: "k", unit: "kPa" }], {}, band("probe_vpd", "k"));
  // Fixed scales with matching steps: the °C ticks 0/25/50 sit on the gridlines of 0/50/100 %.
  drawChart("ch-water", [line(t("chart.water"), "", css("--water"), "%", "%"), line(t("chart.wtemp"), "", css("--temp"), "c", "°C")],
    [xs, water, wtemp], [{ scale: "%", unit: "%", splits: [0, 50, 100] }, { scale: "c", unit: "°C", side: 1, splits: [0, 25, 50] }],
    { "%": { range: [0, 100] }, c: { range: [0, 50] } });
  drawSparks();
}

let bandSig = null;

function bandsChanged(s) {  // new targets (phase, day/night) redraw the bands
  const sig = ((s.plant && s.plant.checks) || []).filter((r) => String(r.metric).startsWith("probe_"))
    .map((r) => `${r.metric}:${r.min}:${r.max}`).join("|");
  if (sig === bandSig) return;
  bandSig = sig;
  if (history.length) drawHistory();
}

let histMsg = ["", null];  // kept as key and values, so a language switch can say it again

function showHistMsg(key, params) {
  histMsg = [key, params];
  $("hist-msg").textContent = key ? t(key, params) : "";
}

async function loadHistory() {
  showHistMsg("history.loading");
  try {
    const data = await api(`/api/history?hours=${hours}`);
    history = data.rows || [];
    showHistMsg(history.length ? "" : "history.empty");
    drawHistory();
    document.dispatchEvent(new CustomEvent("vgrow:history"));
  } catch (err) {
    showHistMsg("history.error", { error: err.message });
  }
}

// ---------- theme ----------

const THEME_KEY = "vgrow.theme";
const THEMES = ["auto", "light", "dark", "safe"];  // safe: the colour-blind-safe light theme (tokens.css)
const THEME_ICON = {
  auto: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 3a7 7 0 0 0 0 14z" fill="currentColor"/></svg>',
  light: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.6" fill="currentColor"/><path d="M10 1.8v2.2M10 16v2.2M1.8 10h2.2M16 10h2.2M4.2 4.2l1.6 1.6M14.2 14.2l1.6 1.6M4.2 15.8l1.6-1.6M14.2 5.8l1.6-1.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  dark: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16.5 12.6A7 7 0 0 1 7.4 3.5a7 7 0 1 0 9.1 9.1z" fill="currentColor"/></svg>',
  safe: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M1.8 10S4.8 4.6 10 4.6 18.2 10 18.2 10 15.2 15.4 10 15.4 1.8 10 1.8 10z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="10" cy="10" r="2.6" fill="currentColor"/></svg>',
};
const THEME_TEXT = { auto: "theme.auto", light: "theme.light", dark: "theme.dark", safe: "theme.safe" };
let theme = "auto";

function storedTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return THEMES.includes(saved) ? saved : "auto";
  } catch (_) {
    return "auto";
  }
}

function themeLabel() {
  const btn = $("theme-btn");
  btn.title = t("theme.label", { mode: t(THEME_TEXT[theme]) });
  btn.setAttribute("aria-label", btn.title);
}

function applyTheme(mode, save = false) {
  theme = THEMES.includes(mode) ? mode : "auto";
  if (theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  if (save) {
    try { localStorage.setItem(THEME_KEY, theme); } catch (_) { /* blocked storage: this page load only */ }
  }
  for (const b of document.querySelectorAll("[data-theme-set]")) {
    const on = b.dataset.themeSet === theme;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  }
  $("theme-btn").innerHTML = THEME_ICON[theme];
  themeLabel();
  themeChanged();
}

function themeChanged() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", css("--side") || "#14181f");
  if (history.length) drawHistory();  // canvas colours are read once per draw
}

// ---------- language switch ----------

function langButtons() {
  for (const b of document.querySelectorAll("[data-lang-set]")) {
    const on = b.dataset.langSet === LANG;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  }
}

// Everything that holds text is drawn again: static texts, caches keyed by text, charts, lists, open questions.
function setLang(lang) {
  if (!DICT[lang] || lang === LANG) return;
  LANG = lang;
  try { localStorage.setItem(LANG_KEY, lang); } catch (_) { /* blocked storage: this page load only */ }
  document.documentElement.lang = lang;
  applyStatic();
  langButtons();
  themeLabel();
  delete $("stage-track").dataset.sig;
  delete $("plant-stage-select").dataset.labels;
  const unlock = $("unlock-light");
  if (unlock) unlock.remove();
  closeStageConfirm();
  for (const k of Object.keys(portArm)) delete portArm[k];  // two-tap confirmations start over
  autoArmUntil = 0;
  for (const k of Object.keys(stageArm)) stageArm[k] = 0;
  $("plant-apply").classList.remove("arm");
  clearTimeout(feedArmTimer);
  $("feed-confirm").classList.remove("arm");  // its text came back with the static texts above
  for (const el of document.querySelectorAll(".fb")) setFb(el.id, "");  // old feedback is in the old language
  for (const k of Object.keys(FB_KEEP)) delete FB_KEEP[k];
  stageDone = null;
  showLiters(parseLiters($("feed-liters").value));  // 1,5 in German, 1.5 in English
  for (const view of cameraViews.values()) camBlank(view.blankKey, view);
  showHistMsg(...histMsg);
  if (history.length) drawHistory();
  photosLoaded = 0;  // the photo strip loads again, its URLs now with the new language
  if (state) render(state);  // the AI photo's URL carries the language, so this loads it again
  // Pictures the server draws (the demo camera) follow ?lang= at once; live is the default, so restart it.
  for (const view of cameraViews.values()) {
    if (view.live) setLive(true, view); else cameraStill(view);
  }
  // The server follows (pushes, the daily reading), then fresh state in the new language.
  api("/api/cmd", { action: "set_lang", lang }).catch(() => {}).finally(() => {
    poll();
    loadRecordings();
  });
}

// ---------- controls ----------

function bindControls() {
  $("light-toggle").onclick = () => {
    const on = (state?.light?.level || 0) > 0;
    send({ action: "light", level: on ? 0 : Number($("light-range").value) }, "fb-light");
  };
  const r = $("light-range");
  r.addEventListener("pointerdown", () => { dragging = true; });
  r.addEventListener("input", () => {
    // A slider target is not a physical confirmation from the box.
    const target = $("light-target-value");
    if (target) target.textContent = t("cockpit.requestedValue", { value: qty(amount(Number(r.value)), "%") });
    if ($("light-number")) $("light-number").value = r.value;
  });
  r.addEventListener("change", () => {
    dragging = false;
    send({ action: "light", level: Number(r.value) }, "fb-light");
  });
  for (const b of $("spec-seg").children) {
    b.onclick = () => send({ action: "spectrum", name: b.dataset.spec }, "fb-light");
  }
  document.querySelectorAll(".stepper").forEach((st) => {
    const kind = st.dataset.fan;
    st.querySelectorAll(".st").forEach((btn) => {
      btn.onclick = () => {
        const cur = draft[kind] ?? state?.[kind]?.step ?? 0;
        draft[kind] = Math.max(0, Math.min(10, cur + Number(btn.dataset.d)));
        renderFan(kind, state[kind] || {});
        clearTimeout(timers[kind]);
        timers[kind] = setTimeout(() => commitFan(kind), 700);  // several taps, one command
      };
    });
  });
  document.querySelectorAll(".chips button[data-fan]").forEach((b) => {
    b.onclick = () => {
      const kind = b.dataset.fan;
      clearTimeout(timers[kind]);
      if (b.dataset.nw === "1") {
        draft[kind] = null;
        send({ action: "cfan", natural_wind: true }, "fb-cfan");
        return;
      }
      draft[kind] = Number(b.dataset.step);
      renderFan(kind, state[kind] || {});
      commitFan(kind);
    };
  });
  for (const b of $("range-seg").children) {
    b.onclick = () => {
      hours = Number(b.dataset.h);
      for (const x of $("range-seg").children) x.classList.toggle("on", x === b);
      loadHistory();
    };
  }
  $("ai-run").onclick = async () => {
    setFb("fb-plant", t("ai.starting"));
    $("ai-run").disabled = true;
    try {
      const res = await api("/api/cmd", { action: "plant_observe", camera: $("ai-camera").value || undefined });
      setFb("fb-plant", res.error ? res.error : t("ai.started"), res.error ? "bad" : "ok");
      poll(true);
    } catch (err) {
      setFb("fb-plant", err.message, "bad");
    }
  };
  for (const button of document.querySelectorAll("[data-pest-answer]")) {
    button.onclick = () => send({ action: "pest_answer", id: $("pest-card").dataset.check, answer: button.dataset.pestAnswer }, "fb-pest");
  }
  $("stage-confirm-yes").onclick = () => {
    const key = stagePick;
    closeStageConfirm();
    if (key) send({ action: "plant_stage", key }, "fb-plant");
  };
  $("stage-confirm-no").onclick = closeStageConfirm;
  $("water-save").onclick = () => {
    const ph = $("water-ph").value, ec = $("water-ec").value;
    if (!ph && !ec) { setFb("fb-plant", t("water.needValue"), "bad"); return; }
    send({ action: "plant_water", ph: ph || null, ec: ec || null }, "fb-plant");
    $("water-ph").value = ""; $("water-ec").value = "";
  };
  $("plant-stage-save").onclick = () => send({ action: "plant_stage", key: $("plant-stage-select").value }, "fb-plant");
  $("plant-height-save").onclick = () => {
    const cm = Number($("plant-height").value);
    if (!cm) { setFb("fb-plant", t("plant.needHeight"), "bad"); return; }
    const estimated = $("plant-height-estimated").checked;
    send({ action: "plant_height", cm, estimated, ...(estimated ? { camera: "growcam" } : {}) }, "fb-plant");
    $("plant-height").value = "";
  };
  $("plant-bloom-suggest").onclick = () => {  // one tap fills the field; the save button still wants two taps
    const day = $("plant-bloom-suggest").dataset.date;
    if (!day) return;
    $("plant-bloom").value = day;
    $("plant-bloom").dataset.dirty = "1";
    bloomArm = 0;
    if (state && state.plant) renderBloom(state.plant);
    $("plant-bloom-save").focus({ preventScroll: true });
  };
  $("plant-bloom").oninput = () => {
    $("plant-bloom").dataset.dirty = "1";
    bloomArm = 0;  // a changed date needs its own two taps
    if (state && state.plant) renderBloom(state.plant);
  };
  $("plant-bloom-save").onclick = async () => {
    const value = $("plant-bloom").value;
    if (bloomBusy) return;
    if (!value) { setFb("fb-plant-bloom", t("plant.bloomNeed"), "bad"); return; }
    if (Date.now() >= bloomArm) {
      bloomArm = Date.now() + 4000;
      if (state && state.plant) renderBloom(state.plant);
      setTimeout(() => { if (state && state.plant) renderBloom(state.plant); }, 4100);
      return;
    }
    bloomArm = 0;
    bloomBusy = true;
    if (state && state.plant) renderBloom(state.plant);
    setFb("fb-plant-bloom", t("fb.sending"));
    try {
      const res = await api("/api/cmd", { action: "plant_bloom", date: value });
      delete $("plant-bloom").dataset.dirty;
      setFb("fb-plant-bloom", t("plant.bloomSaved", { date: dateLong(res.date || value) }), "ok");
      poll(true);
    } catch (err) {
      setFb("fb-plant-bloom", err.message, "bad");
    } finally {
      bloomBusy = false;
      if (state && state.plant) renderBloom(state.plant);
    }
  };
  let startTimer;
  const startTyped = (other) => () => {
    $(other).value = "";  // exactly one of date and weeks
    $("plant-start-date").dataset.dirty = "1";
    startArm = 0;  // a changed value needs its own two taps
    clearTimeout(startTimer);
    startTimer = setTimeout(previewStart, 300);
    if (state && state.plant) renderStart(state.plant);
  };
  $("plant-start-date").oninput = startTyped("plant-age-weeks");
  $("plant-age-weeks").oninput = startTyped("plant-start-date");
  $("plant-start-save").onclick = async () => {
    const value = startValue();
    if (startBusy) return;
    if (!value) { setFb("fb-plant-start", t("plant.startNeed"), "bad"); return; }
    if (Date.now() >= startArm) {
      startArm = Date.now() + 4000;
      if (state && state.plant) renderStart(state.plant);
      setTimeout(() => { if (state && state.plant) renderStart(state.plant); }, 4100);
      return;
    }
    startArm = 0;
    startBusy = true;
    if (state && state.plant) renderStart(state.plant);
    setFb("fb-plant-start", t("fb.sending"));
    try {
      const res = await api("/api/cmd", { action: "plant_start", ...value });
      delete $("plant-start-date").dataset.dirty;
      $("plant-age-weeks").value = "";
      startPreview = { sig: "", text: "", bad: false };
      setFb("fb-plant-start", t("plant.startSaved", { date: dateLong(res.date) }), "ok");
      poll(true);
    } catch (err) {
      setFb("fb-plant-start", err.message, "bad");
    } finally {
      startBusy = false;
      if (state && state.plant) renderStart(state.plant);
    }
  };
  $("feed-age-enter").onclick = () => {  // the age question jumps to the start field on the plant card
    const field = $("plant-start-date");
    if (globalThis.Cockpit) globalThis.Cockpit.reveal(field);
    field.scrollIntoView({ behavior: "smooth", block: "center" });
    field.focus({ preventScroll: true });
  };
  $("plant-apply").onclick = async () => {
    const b = $("plant-apply");
    if (!b.classList.contains("arm")) {  // second tap applies: this changes light and fans
      b.classList.add("arm");
      b.textContent = t("plant.applyConfirm", { again: again() });
      setTimeout(() => { b.classList.remove("arm"); b.textContent = t("plant.apply"); }, 4000);
      return;
    }
    b.classList.remove("arm");
    b.textContent = t("plant.apply");
    setFb("fb-plant", t("plant.applying"));
    try {
      const res = await api("/api/cmd", { action: "plant_apply" });
      setFb("fb-plant", t("plant.applied", { list: res.applied.join(", ") }), "ok");
      poll(true);
    } catch (err) {
      setFb("fb-plant", err.message, "bad");
    }
  };
  $("feed-confirm").onclick = async () => {
    const b = $("feed-confirm");
    const f = currentFeeding();
    if (!f || !f.card) return;
    if (!b.classList.contains("arm")) {  // second tap confirms: from then on the server computes amounts
      b.classList.add("arm");
      b.textContent = t("feed.confirmArm", { again: again() });
      clearTimeout(feedArmTimer);
      feedArmTimer = setTimeout(() => { b.classList.remove("arm"); b.textContent = t("feed.confirmBtn"); }, 4000);
      return;
    }
    clearTimeout(feedArmTimer);
    b.classList.remove("arm");
    b.textContent = t("feed.confirmBtn");
    b.disabled = true;
    setFb("fb-feed-confirm", t("fb.sending"));
    try {
      // The card and the fingerprint of exactly the numbers this page shows: changed numbers are not confirmed.
      await api("/api/cmd", { action: "feeding_confirm", card: f.card.id, fingerprint: f.fingerprint });
      setFb("fb-feed-confirm", t("feed.confirmed"), "ok");
      poll(true);
    } catch (err) {
      setFb("fb-feed-confirm", err.message, "bad");
    } finally {
      b.disabled = false;
    }
  };
  try {  // the can's litres from the last visit on this device, written the page language's way
    showLiters(parseLiters(localStorage.getItem(FEED_LITERS)));
  } catch (_) { /* storage blocked */ }
  for (const d of document.querySelectorAll(".feed-more")) d.addEventListener("toggle", feedEdges);  // width only once open
  window.addEventListener("resize", feedEdges);
  let feedTimer;
  $("feed-liters").addEventListener("input", () => {
    const v = parseLiters($("feed-liters").value);
    try {  // the plain number (1.5), so another language shows it its own way
      if (v === null) localStorage.removeItem(FEED_LITERS); else if (Number.isFinite(v)) localStorage.setItem(FEED_LITERS, String(v));
    } catch (_) { /* this page load only */ }
    clearTimeout(feedTimer);
    feedTimer = setTimeout(() => renderFeeding(currentFeeding()), 350);  // one request once the typing stops
  });
  $("feed-large-yes").onclick = () => {  // the second tap for litres above the limit: amounts for exactly these
    feedLarge = Number($("feed-large-yes").dataset.liters);
    renderFeeding(currentFeeding());
    $("feed-fed").focus({ preventScroll: true });
  };
  $("feed-fed").onclick = async () => {
    const input = $("feed-liters");
    const liters = parseLiters(input.value);
    const f = currentFeeding();
    if (!Number.isFinite(liters)) {
      setFb("fb-feed", t(liters === null ? "feed.needLiters" : "feed.badLiters"), "bad");
      input.focus();
      return;
    }
    feedBusy = true;
    $("feed-fed").disabled = true;  // one tap, one entry
    setFb("fb-feed", t("fb.sending"));
    try {
      // Only the litres (and the second tap for a big can): the server computes every amount itself. The card and
      // the fingerprint this page shows go along: after a switch elsewhere the server refuses, never logs the other.
      const large = !!f && bigLiters(f, liters) && feedLarge === liters;
      const res = await api("/api/cmd", { action: "feeding_log", liters: String(liters), large,
        card: f && f.card ? f.card.id : null, fingerprint: f ? f.fingerprint : null });
      const entry = res.entry || {};
      setFb("fb-feed", t("feed.logged", { liters: litersText(entry.liters), week: (entry.week && entry.week.label) || "" }), "ok");
      poll(true);
    } catch (err) {
      setFb("fb-feed", err.message, "bad");
    } finally {
      feedBusy = false;
      $("feed-fed").disabled = false;
    }
  };
  $("test-push").onclick = async () => {
    setFb("fb-push", t("alerts.sending"));
    try {
      await api("/api/cmd", { action: "test_push" });
      setFb("fb-push", t("alerts.sent"), "ok");
    } catch (err) {
      setFb("fb-push", t("alerts.failed", { error: err.message }), "bad");
    }
  };
  for (const b of document.querySelectorAll("[data-theme-set]")) b.onclick = () => applyTheme(b.dataset.themeSet, true);
  $("theme-btn").onclick = () => applyTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length], true);
  for (const b of document.querySelectorAll("[data-lang-set]")) b.onclick = () => setLang(b.dataset.langSet);
  for (const a of document.querySelectorAll("a[data-nav]")) a.addEventListener("click", () => setActiveNav(a.dataset.nav));
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawHistory, 200); });
}

async function commitFan(kind) {
  const step = draft[kind];
  const fb = kind === "cfan" ? "fb-cfan" : "fb-dfan";
  await send({ action: kind, step }, fb);
  setTimeout(() => { if (draft[kind] === step) { draft[kind] = null; if (state) renderFan(kind, state[kind] || {}); } }, 3000);
}

// ---------- loop ----------

let pollTimer;
async function poll(soon = false) {
  clearTimeout(pollTimer);
  if (soon) { pollTimer = setTimeout(poll, 800); return; }
  try {
    const s = await api("/api/state");
    try { render(s); } catch (err) { console.error(err); }  // a drawing bug must not stop the loop
  } catch (err) {
    setStatus("bad", t("status.gone"));
    $("error-banner").hidden = false;
    $("error-banner").textContent = t("banner.serverDown", { error: err.message });
    pauseControls(true, t("offline.server"));
    document.dispatchEvent(new CustomEvent("vgrow:offline"));
  }
  const busy = state && Object.values(state.pending || {}).some((p) => p.status === "pending");
  if (!document.hidden) pollTimer = setTimeout(poll, busy ? 1500 : 5000);
}

document.addEventListener("visibilitychange", () => {
  refreshCameraPlayback();
  if (document.hidden) return;
  poll(); loadHistory(); camStill();
});
setInterval(camStill, 10000);
applyStatic();
langButtons();
applyTheme(storedTheme());
onMedia(DARK, () => { if (theme === "auto") themeChanged(); });
bindControls();
pauseControls(true, t("status.connecting"));
poll();
loadHistory();
setInterval(() => { if (!document.hidden) loadHistory(); }, 5 * 60 * 1000);
