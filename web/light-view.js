/* Opening this view reads only. Automatic applies the displayed requirements after an explicit tap. */
(function () {
  "use strict";
  let snapshot = null, report = null, fetched = 0, busy = false, generation = 0, offline = false;
  let editing = false, saving = false, automaticSaving = false, automaticSuccess = false;
  const timerStates = { disabled: "lightTimer.disabled", scheduled: "lightTimer.scheduled", manual: "lightTimer.manual",
    connection: "lightTimer.connection", mode: "lightTimer.mode", storage: "lightTimer.storage", unconfirmed: "lightTimer.unconfirmed",
    clock: "lightTimer.clock", pending: "lightTimer.pending", requirements: "lightAuto.requirements", stopped: "lightTimer.stopped" };
  const timerMessage = (timer) => t(timerStates[timer.status] || "check.unknown");
  const n = (tag, cls, text) => make(tag, cls, text);
  const presetTexts = { "18-6": "lightTimer.preset18_6", "20-4": "lightTimer.preset20_4", "12-12": "lightTimer.preset12_12" };
  const presetName = (preset) => preset.key.replace("-", "/");
  const minutesOf = (text) => /^\d\d:\d\d$/.test(text || "") ? Number(text.slice(0, 2)) * 60 + Number(text.slice(3)) : null;
  const clock = (minutes) => `${String(Math.floor(minutes / 60) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  function formHours() {
    const on = minutesOf($("light-timer-on").value), off = minutesOf($("light-timer-off").value);
    return on == null || off == null || on === off ? null : (off - on + 1440) % 1440 / 60;
  }
  /* Built once and then only marked, so a poll never takes the focus away from a button. */
  function renderPresets(timer) {
    const root = $("light-timer-presets"), presets = timer?.presets || [], hours = formHours();
    if (root.childElementCount !== presets.length) {
      root.replaceChildren();
      for (const preset of presets) {
        const button = n("button", preset.autoflower ? "toggle" : "toggle is-photo", presetName(preset));
        button.type = "button"; button.onclick = () => applyPreset(preset); root.append(button);
      }
    }
    presets.forEach((preset, i) => root.children[i].setAttribute("aria-pressed", String(preset.hours === hours)));
    const match = presets.find((preset) => preset.hours === hours), note = $("light-timer-preset-note");
    note.replaceChildren();
    if (!match) return;
    note.append(t(presetTexts[match.key] || "check.unknown"), " " + t("lightTimer.source") + " ");
    match.sources.forEach((source, i) => {
      const link = n("a", "", source.name); link.href = source.url; link.target = "_blank"; link.rel = "noopener";
      note.append(...(i ? [", ", link] : [link]));
    });
  }
  function applyPreset(preset) {
    const on = minutesOf($("light-timer-on").value) ?? 360, off = on + preset.hours * 60;  // 06:00 only fills an empty form
    $("light-timer-on").value = clock(on); $("light-timer-off").value = clock(off);
    editing = true; renderPresets(snapshot?.light_schedule);
    setFb("fb-light-timer", t("lightTimer.presetFilled", { name: presetName(preset), on: clock(on), off: clock(off) }));
  }
  const known = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 100;
  const scope = () => window.PlantWorkspace?.headers()?.["X-Workspace-Box"] || "";
  const connectedScope = () => !scope() || scope() === "box-1";
  const open = () => location.hash === "#devices/light" && connectedScope();
  function flag(light) {
    return light?.in_plan_known === false || typeof light?.in_plan !== "boolean" ? "unknown" : light.in_plan ? "active" : "inactive";
  }
  function timeText(at) {
    return new Date(at * 1000).toLocaleString(LOCALE[LANG], { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Berlin" });
  }
  function output(level, stale = false) {
    const value = known(level) ? `${num(level)}%` : t("check.unknown");
    return stale && known(level) ? t("cockpit.lastKnown", { value }) : value;
  }
  function renderAutomatic(timer) {
    const automatic = timer?.automatic, cfg = timer?.settings;
    const linked = connectedScope() && (window.PlantWorkspace?.assistantAllowed() ?? true);
    const plan = linked ? automatic?.plan : null;
    const button = $("light-automatic"), active = !!cfg?.enabled && !!cfg.from_requirements && timer.status === "scheduled";
    if (automaticSuccess && timer?.status === "manual" && !automaticSaving) {
      automaticSuccess = false; setFb("fb-light-automatic", "");
    }
    if (automaticSuccess) setFb("fb-light-automatic", t("lightAuto.saved"), "ok");
    button.disabled = automaticSaving || saving || offline || !linked || !automatic?.available;
    button.setAttribute("aria-pressed", String(active));
    button.setAttribute("aria-busy", String(automaticSaving));
    button.textContent = t(automaticSaving ? "fb.sending" : "lightAuto.button");
    $("light-automatic-plan").textContent = plan ? t("lightAuto.plan", {
      stage: (LANG === "en" ? plan.stage.label_en : plan.stage.label) || plan.stage.label || plan.stage.key,
      hours: plan.hours_on, dark: 24 - plan.hours_on, on: plan.settings.on_time, off: plan.settings.off_time,
      level: plan.settings.level, zone: plan.settings.timezone
    }) : t("lightAuto.requirements");
    const reason = !linked ? t("lightAuto.scope") : offline ? t("offline.server")
      : automatic?.reason ? t(timerStates[automatic.reason] || "lightAuto.requirements") : "";
    $("light-automatic-note").textContent = reason || [timer?.status === "stopped" ? t("lightTimer.stopped") : "",
      active ? t("lightAuto.active") : t("lightAuto.apply"), plan?.default_start ? t("lightAuto.defaultStart") : ""
    ].filter(Boolean).join(" ");
    const sources = $("light-automatic-sources"); sources.replaceChildren();
    if (plan?.sources?.length) {
      sources.append(t("lightTimer.source") + " ");
      plan.sources.forEach((source, i) => {
        const link = n("a", "", source.id); link.title = source.text;
        if (source.url && source.url.startsWith("https://")) { link.href = source.url; link.target = "_blank"; link.rel = "noopener"; }
        sources.append(...(i ? [", ", link] : [link]));
      });
    }
  }
  function renderStatus() {
    const s = snapshot || {}, light = s.light || {}, state = flag(light), timer = s.light_schedule, cfg = timer?.settings;
    renderAutomatic(timer);
    const stale = offline || !s.online || !s.mqtt || s.shadow_age == null || s.shadow_age > 600;
    $("overview-light-value").textContent = output(light.level, stale);
    $("overview-light-note").textContent = t(state === "inactive" ? "lightView.tileInactive" : "lightView.open");
    if (cfg?.enabled) $("overview-light-note").textContent = t("lightTimer.daily", { on: cfg.on_time, off: cfg.off_time });
    const metrics = $("light-view-metrics"); metrics.replaceChildren();
    const control = cfg?.enabled ? t("lightTimer.title") : state === "active" ? t("lightView.plan") : light.mode === 0 && state === "inactive" ? t("lightView.manual") : t("check.unknown");
    for (const [label, value] of [[t("lightView.output"), output(light.level, stale)], [t("lightView.control"), control]]) {
      const metric = n("div", "light-view-metric"); metric.append(n("span", "", label), n("strong", "", value)); metrics.append(metric);
    }
    $("light-view-explanation").textContent = t(state === "active" ? "lightView.active" : state === "inactive" ? "lightView.inactive" : "lightView.unknown");
    if (cfg?.enabled || timer?.status === "storage") $("light-view-explanation").textContent = timerMessage(timer);
    const times = $("light-view-times"); times.replaceChildren();
    const durationMinutes = timer?.hours_on == null ? null : Math.round(timer.hours_on * 60);
    const values = [cfg?.on_time, cfg?.off_time, durationMinutes == null ? null : t("lightTimer.hours", { hours: amount(Math.floor(durationMinutes / 60)), minutes: amount(durationMinutes % 60) }), timer?.next_switch ? timeText(timer.next_switch.at) : null];
    for (const [index, label] of [t("lightView.onTime"), t("lightView.offTime"), t("lightView.hours"), t("lightView.next")].entries()) { const pair = n("div"); pair.append(n("dt", "", label), n("dd", "", values[index] ?? t("lightView.notReported"))); times.append(pair); }
    $("light-view-freshness").textContent = offline ? t("offline.server") : s.shadow_age == null ? t("check.unknown") : t(stale ? "lightView.oldStatus" : "lightView.freshness", { ago: ago(s.shadow_age) });
    $("light-view-explanation").classList.toggle("is-stale", stale);
    $("light-timer-settings").hidden = !timer;
    $("light-timer-summary").hidden = !timer;
    if (timer) {
      $("light-timer-summary-status").textContent = timerMessage(timer);
      $("light-timer-summary-times").textContent = cfg.on_time && cfg.off_time ? t("lightTimer.daily", { on: cfg.on_time, off: cfg.off_time }) + (timer.next_switch ? " · " + t("lightTimer.next", { time: timeText(timer.next_switch.at) }) : "") : t("lightTimer.setup");
      $("light-timer-disable").hidden = !cfg.enabled;
      if (!editing && !saving) {
        $("light-timer-on").value = cfg.on_time || ""; $("light-timer-off").value = cfg.off_time || "";
        $("light-timer-level").value = cfg.level ?? ""; $("light-timer-enabled").checked = cfg.enabled;
      }
      renderPresets(timer);
      for (const el of $("light-timer-settings").querySelectorAll("button,input")) el.disabled = saving || automaticSaving || offline || !connectedScope();
    }
  }
  function renderHistory() {
    const root = $("light-view-history"); root.replaceChildren();
    if (!report) { root.append(n("p", "hint", t("lightView.loading"))); return; }
    const h = report.history;
    if (!h || !h.readings) { root.append(n("p", "hint", t(report.demo ? "lightView.demoHistory" : "lightView.noHistory"))); return; }
    const chart = n("div", "light-history-bar"); chart.setAttribute("role", "img"); chart.setAttribute("aria-label", t("lightView.chartLabel", { coverage: num(h.coverage_percent) }));
    for (const row of h.segments) {
      const type = row.level === null ? "unknown" : row.level === 0 ? "off" : "on";
      const part = n("span", `light-history-segment is-${type}`); part.style.flexBasis = `${(row.end - row.start) / (h.end - h.start) * 100}%`;
      part.title = `${timeText(row.start)} – ${timeText(row.end)} · ${row.level === null ? t("lightView.noReport") : output(row.level)}`;
      if (type === "on") part.style.opacity = String(.35 + row.level / 100 * .65);
      chart.append(part);
    }
    const axis = n("div", "light-history-axis"); axis.append(n("span", "", timeText(h.start)), n("span", "", timeText(h.end)));
    const legend = n("div", "light-history-legend");
    for (const [type, label] of [["on", t("lightView.lit")], ["off", t("lightView.dark")], ["unknown", t("lightView.noReport")]]) { const item = n("span"); item.append(n("i", `is-${type}`), label); legend.append(item); }
    const day = report.balance;
    if (day && day.measured_hours) root.append(n("p", "", t("lightView.balance", { lit: num(day.lit_hours), dark: num(day.dark_hours), measured: num(day.measured_hours) })));
    root.append(chart, axis, legend, n("p", "hint", t("lightView.coverage", { n: amount(h.readings), percent: num(h.coverage_percent) })));
    const details = n("details"), summary = n("summary", "", t("lightView.reportList")), list = n("ol", "light-report-list");
    for (const row of h.segments.filter((r) => r.level !== null).slice(-100).reverse()) list.append(n("li", "", `${timeText(row.start)} – ${timeText(row.end)} · ${output(row.level)}`));
    details.append(summary, list); root.append(details);
  }
  async function refresh(force = false) {
    if (!open() || busy || !force && Date.now() - fetched < 60000) return;
    const current = generation, box = scope(); busy = true; $("light-view-refresh").disabled = true;
    try {
      const data = await api("/api/light");
      if (current !== generation || box !== scope()) return;
      report = data; fetched = Date.now(); $("light-view-error").textContent = ""; renderHistory();
    } catch (_) { if (current === generation) $("light-view-error").textContent = t(report ? "lightView.cachedHistory" : "lightView.failed"); }
    finally { busy = false; $("light-view-refresh").disabled = false; if (current !== generation) refresh(); }
  }
  document.addEventListener("vgrow:state", (event) => { snapshot = event.detail; offline = false; renderStatus(); refresh(); });
  document.addEventListener("vgrow:offline", () => { offline = true; renderStatus(); });
  document.addEventListener("workspace:context", () => { generation++; report = null; fetched = 0; renderStatus(); renderHistory(); refresh(); });
  window.addEventListener("hashchange", () => { renderStatus(); renderHistory(); refresh(); });
  new MutationObserver(() => { renderStatus(); renderHistory(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  $("light-view-refresh").onclick = () => refresh(true);
  async function saveTimer(cfg) {
    if (saving || automaticSaving || !connectedScope()) return;
    saving = true; renderStatus(); setFb("fb-light-timer", t("fb.sending"));
    try {
      await api("/api/cmd", { action: "light_schedule_config", ...cfg });
      editing = false;
      await poll();
      setFb("fb-light-timer", t("lightTimer.saved"), "ok");
    } catch (err) { setFb("fb-light-timer", err.message, "bad"); }
    finally { saving = false; renderStatus(); }
  }
  $("light-timer-settings").oninput = () => { editing = true; renderPresets(snapshot?.light_schedule); };
  $("light-timer-settings").onsubmit = (event) => {
    event.preventDefault();
    saveTimer({ enabled: $("light-timer-enabled").checked, on_time: $("light-timer-on").value || null,
      off_time: $("light-timer-off").value || null, level: Number($("light-timer-level").value), timezone: "Europe/Berlin" });
  };
  $("light-timer-disable").onclick = () => saveTimer({ ...snapshot.light_schedule.settings, enabled: false });
  $("light-automatic").onclick = async () => {
    const plan = snapshot?.light_schedule?.automatic?.plan;
    if ($("light-automatic").disabled || !plan) return;
    automaticSaving = true; automaticSuccess = false; renderStatus(); setFb("fb-light-automatic", t("fb.sending"));
    try {
      await api("/api/cmd", { action: "light_schedule_auto", fingerprint: plan.fingerprint });
      editing = false; await poll();
      automaticSuccess = true; setFb("fb-light-automatic", t("lightAuto.saved"), "ok");
      setFb("fb-light", "");
    } catch (err) {
      setFb("fb-light-automatic", err.message, "bad");
      await poll();
    } finally { automaticSaving = false; renderStatus(); }
  };
  renderStatus(); renderHistory(); refresh();
})();
