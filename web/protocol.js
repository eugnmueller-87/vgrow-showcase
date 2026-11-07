/* The protocol observes existing controls. This page never sends actuator commands. */
(function () {
  "use strict";
  let report = null, older = [], busy = false, error = false, generation = 0, signature = "", fetched = 0;
  const n = (tag, cls, text) => make(tag, cls, text);
  const statuses = { requested: "protocol.sent", confirmed: "protocol.confirmed", refused: "protocol.refused",
    unconfirmed: "protocol.unconfirmed", saved: "protocol.saved", observed: "protocol.observed", suggested: "protocol.suggested",
    unrecorded: "protocol.unrecorded", superseded: "protocol.superseded", skipped: "protocol.skipped" };
  const responses = { waiting: "protocol.waiting", watching: "protocol.watching", improving: "protocol.improving",
    in_range: "protocol.inRange", worse: "protocol.worse", not_improved: "protocol.notImproved",
    unrecorded: "protocol.responseUnknown", no_issue: "protocol.noLinkedIssue", superseded: "protocol.observationEnded",
    saved: "protocol.savedResult", not_executed: "protocol.notExecuted", overshot: "protocol.overshot" };
  const origins = { automatic: "protocol.originAutomatic", hank: "protocol.originHank", manual: "protocol.originManual",
    schedule: "protocol.originSchedule", safety: "protocol.originSafety", external: "protocol.originExternal", watering: "protocol.originWatering" };
  const metrics = { probe_temp: "protocol.temperature", probe_humidity: "protocol.humidity", probe_vpd: "protocol.vpd",
    light_percent: "light.title", light_schedule: "lightView.title", humidifier_response: "protocol.humidifierResponse", pump_control: "protocol.pumpControl" };
  const events = { ...statuses, ...responses, detected: "protocol.detected", decision: "protocol.decision",
    task: "protocol.taskLinked", imported: "protocol.imported", resolved: "protocol.inRange", ended: "protocol.observationEnded",
    context_changed: "protocol.contextChanged" };
  const isOpen = () => location.hash === "#activity/protocol";
  const linked = () => (window.PlantWorkspace?.assistantAllowed() ?? true) && (!window.PlantWorkspace?.headers()?.["X-Workspace-Box"] || window.PlantWorkspace.headers()["X-Workspace-Box"] === "box-1");
  const time = (at, seconds = false) => typeof at === "number" ? new Date(at * 1000).toLocaleString(LOCALE[LANG], { dateStyle: "medium", timeStyle: seconds ? "medium" : "short", timeZone: "Europe/Berlin" }) : t("check.unknown");
  const value = (v, unit = "") => typeof v === "boolean" ? t(v ? "ctl.on" : "ctl.off") : typeof v === "number" ? `${unit === "kPa" ? v.toLocaleString(LOCALE[LANG], {maximumFractionDigits: 2}) : num(v)}${unit ? " " + unit : ""}` : v === "nw" ? t("fan.wind") : SPECTRUM[v] ? t(SPECTRUM[v]) : t("check.unknown");
  const maintenance = (metric) => ["humidifier_response", "pump_control"].includes(metric);
  const device = (id) => id === "dfan" ? t("fan.dfan") : id === "cfan" ? t("fan.cfan") : id === "light" || id === "spectrum" ? t("light.title") : id === "usb1" ? "USB 1" : id === "usb2" ? "USB 2" : t("protocol.settings");
  const summary = (totals) => t("protocol.summary", totals || { active: 0, working: 0, blocked: 0 });
  function decisionText(d) {
    if (d.code === "limit") return t("protocol.limit", { limit: d.limit, reported: d.reported });
    if (d.code === "already_off") return t("protocol.alreadyOff");
    if (d.code === "pump_setup") return t("protocol.pumpSetup");
    if (d.code === "connection" || d.code === "missing") return t("protocol.stale");
    return (LANG === "de" ? d.text_de : d.text) || d.text || t("protocol.noController");
  }
  function issueCard(issue) {
    const card = n("div", "protocol-issue"); card.id = issue.id;
    card.append(n("h4", "", t(metrics[issue.metric] || "check.unknown") + (maintenance(issue.metric) ? "" : " · " + t(issue.direction === "high" ? "check.high" : "check.low"))));
    const reading = n("div", "protocol-reading");
    if (["humidifier_response", "pump_control"].includes(issue.metric)) reading.append(n("strong", "", t("protocol.checkRequired")));
    else reading.append(n("strong", "", value(issue.current, issue.unit)), n("span", "", t("protocol.target", { min: value(issue.min, issue.unit), max: value(issue.max, issue.unit) })));
    card.append(reading, n("p", "hint", t("protocol.detectedAt", { time: time(issue.at) })));
    for (const d of issue.decisions || []) card.append(n("p", "protocol-decision", device(d.device) + ": " + decisionText(d)));
    const latest = [...(report?.tasks || []), ...older].find((task) => task.issues.includes(issue.id));
    if (latest) {
      const go = n("button", "text-link protocol-task-link", t("protocol.viewTask") + ": " + t(statuses[latest.status] || "protocol.unrecorded"));
      go.type = "button"; go.onclick = () => { $("protocol-filter").value = $("protocol-origin").value = "all"; render(); const target = document.getElementById(latest.id); if (target) { target.scrollIntoView({ block: "center" }); target.classList.add("is-highlighted"); } };
      card.append(go);
    }
    return card;
  }
  function taskCard(task) {
    const card = n("article", "protocol-task"); card.id = task.id;
    const head = n("div", "protocol-task-head"), at = n("time", "", time(task.at)); at.dateTime = new Date(task.at * 1000).toISOString();
    head.append(n("span", "protocol-origin", t(origins[task.origin] || "protocol.originManual")), at);
    const unit = task.device === "light" ? "%" : ["cfan", "dfan"].includes(task.device) ? "/10" : "";
    const heading = ["saved", "skipped"].includes(task.status) ? device(task.device) : device(task.device) + ": " + (task.before != null ? value(task.before, unit) + " → " : "") + value(task.target, unit);
    card.append(head, n("h4", "", heading));
    const stages = n("div", "protocol-stages");
    for (const [label, text, cls] of [["protocol.task", t(task.status === "saved" ? "protocol.saved" : ["suggested", "skipped"].includes(task.status) ? "protocol.notExecuted" : task.status === "observed" ? "protocol.observed" : "protocol.requestRecorded"), ""],
      ["protocol.device", t(statuses[task.status] || "protocol.unrecorded"), task.status === "confirmed" || task.status === "saved" ? "is-ok" : ["refused", "unconfirmed"].includes(task.status) ? "is-attention" : ""],
      ["protocol.result", t(responses[task.response] || "protocol.responseUnknown"), task.response === "in_range" ? "is-ok" : ["worse", "not_improved", "overshot"].includes(task.response) ? "is-attention" : ""]]) {
      const part = n("div", "protocol-stage " + cls); part.append(n("small", "", t(label)), n("b", "", text)); stages.append(part);
    }
    card.append(stages);
    for (const base of task.baseline || []) card.append(n("p", "hint", t("protocol.problem", {
      metric: t(metrics[base.metric] || "check.unknown"), value: ["humidifier_response", "pump_control"].includes(base.metric) ? t("protocol.checkRequired") : value(base.value, base.unit)
    })));
    if (["usb1", "usb2"].includes(task.device)) card.append(n("p", "hint", t("protocol.powerOnly")));
    if (task.historical) card.append(n("p", "hint", t("protocol.historical")));
    for (const o of task.observations || []) {
      if (maintenance(o.metric)) { card.append(n("p", "protocol-observation", t(metrics[o.metric]) + ": " + t(o.current ? "protocol.checkRequired" : "protocol.checkCleared"))); continue; }
      card.append(n("p", "protocol-observation", t("protocol.comparison", {
      metric: t(metrics[o.metric] || "check.unknown"), before: value(o.value, o.unit), after: value(o.current, o.unit),
      delta: (o.delta > 0 ? "+" : "") + value(o.delta, o.unit), time: time(o.at)
      })));
    }
    if (task.historical_baseline) card.append(n("p", "hint", t("protocol.oldBaseline", { metric: t(metrics[task.historical_baseline.metric] || "check.unknown"), value: value(task.historical_baseline.value) })));
    if (task.reason) {
      const reason = typeof task.reason === "object" ? task.reason[LANG] || task.reason.en : task.origin === "safety" ? t("protocol.safetyReason") : task.origin === "automatic" ? "" : task.origin === "watering" ? t("protocol.wateringReason") : task.reason;
      if (reason) card.append(n("p", "", reason));
    }
    const details = n("details"), list = n("ol"); details.append(n("summary", "", t("protocol.timeline")));
    for (const event of task.events || []) { const item = n("li"); item.append(n("time", "", time(event.at, true)), t(events[event.kind] || "protocol.requestRecorded")); list.append(item); }
    details.append(list); card.append(details); return card;
  }
  function render() {
    $("protocol-refresh").disabled = busy || !linked();
    $("protocol-error").textContent = !linked() ? t("protocol.scope") : error || report?.storage_error ? t("protocol.failed") : "";
    if (!report || !linked()) {
      $("protocol-issues").replaceChildren(n("p", "hint", t(!linked() ? "protocol.scope" : error ? "protocol.failed" : "protocol.loading")));
      $("protocol-tasks").replaceChildren(); $("protocol-more").hidden = true;
      $("protocol-summary").textContent = ""; $("protocol-freshness").textContent = ""; $("protocol-since").textContent = ""; return;
    }
    $("protocol-summary").textContent = summary(report.totals);
    $("protocol-freshness").textContent = report.fresh ? t("protocol.sampleAt", { time: time(report.sensor_at) }) : t("protocol.stale");
    $("protocol-since").textContent = t("protocol.since", { time: time(report.started_at) });
    const tasks = [...new Map([...report.tasks, ...older].map((task) => [task.id, task])).values()];
    const filter = $("protocol-filter").value, origin = $("protocol-origin").value;
    const shown = tasks.filter((task) => (origin === "all" || task.origin === origin) && (filter === "all" ||
      filter === "working" && task.monitoring || filter === "attention" && (["refused", "unconfirmed", "skipped"].includes(task.status) || ["worse", "not_improved", "overshot"].includes(task.response)) || filter === "confirmed" && task.status === "confirmed"));
    const sig = JSON.stringify([LANG, report.issues, shown, report.fresh]);
    if (signature !== sig) {
      const open = new Set([...$("protocol-tasks").querySelectorAll(".protocol-task details[open]")].map((d) => d.parentElement.id));
      $("protocol-issues").replaceChildren(...(report.issues.length ? report.issues.map(issueCard) : [n("p", "hint", t(report.fresh ? "protocol.noIssues" : "protocol.stale"))]));
      $("protocol-tasks").replaceChildren(...(shown.length ? shown.map(taskCard) : [n("p", "hint", t("protocol.noTasks"))]));
      for (const id of open) { const d = document.getElementById(id)?.querySelector("details"); if (d) d.open = true; }
      signature = sig;
    }
    $("protocol-more").hidden = !report.next; $("protocol-more").disabled = busy;
  }
  async function refresh(force = false, more = false) {
    if (!isOpen() || !linked() || busy || !force && Date.now() - fetched < 4000) return;
    const gen = generation; busy = true; render();
    try {
      const data = await api("/api/protocol" + (more && report?.next ? "?before=" + encodeURIComponent(report.next) : ""));
      if (gen !== generation) return;
      if (more) { older = [...older, ...data.tasks]; report.next = data.next; }
      else { if (older.length && report) data.next = report.next; report = data; }
      fetched = Date.now(); error = false;
    } catch (_) { if (gen === generation) error = true; }
    finally { busy = false; if (gen === generation) render(); }
  }
  document.addEventListener("vgrow:state", (e) => {
    const s = e.detail.hank_protocol;
    $("protocol-overview").textContent = linked() && s?.updated_at ? s.storage_error ? t("protocol.failed") : summary(s) : "";
    refresh();
  });
  document.addEventListener("vgrow:offline", () => { error = true; render(); });
  document.addEventListener("workspace:context", () => { generation++; report = null; older = []; signature = ""; fetched = 0; render(); refresh(true); });
  window.addEventListener("hashchange", () => { render(); refresh(true); });
  new MutationObserver(() => { signature = ""; render(); refresh(true); }).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  $("protocol-filter").onchange = $("protocol-origin").onchange = render;
  $("protocol-refresh").onclick = () => refresh(true);
  $("protocol-more").onclick = () => refresh(true, true);
  render(); refresh();
})();
