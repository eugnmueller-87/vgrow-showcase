/* Presentation and navigation only. app.js owns measurements, persisted data and actuator commands. */
(function () {
  "use strict";

  const pages = {
    overview: "cockpit.page.overview", grow: "cockpit.page.grow", automation: "cockpit.page.automation",
    devices: "cockpit.page.devices", analytics: "cockpit.page.analytics", water: "waterPage.title", activity: "cockpit.page.activity",
    settings: "cockpit.page.settings", wiki: "wiki.title", community: "community.title"
  };
  const panels = { grow: ["growth", "feeding", "insights", "journal"], devices: ["controls", "light", "camera"], activity: ["active", "protocol", "history", "rules"] };
  const legacy = {
    kamera: "devices/camera", pflanze: "grow/growth", "card-plant": "grow/growth", autopilot: "automation",
    steuerung: "devices/controls", "card-light": "devices/controls", "card-cfan": "devices/controls",
    "card-dfan": "devices/controls", "card-ports": "devices/controls", verlauf: "analytics",
    "card-history": "analytics", alarme: "activity/active", "card-alerts": "activity/rules", feed: "grow/feeding",
    "plant-ai": "grow/insights", "photo-strip": "grow/journal", "plant-start-date": "grow/growth"
  };
  const deviceStatus = {
    ready: "pilot.device.ready", observe: "pilot.device.observe", needs_setup: "pilot.device.needs",
    waiting: "pilot.device.waiting", paused: "pilot.device.paused", pending: "pilot.device.pending",
    manual: "pilot.device.manual", off: "pilot.device.off"
  };
  const modeKeys = { off: "pilot.mode.off", observe: "pilot.mode.observe", auto: "pilot.mode.auto" };
  let current = null;
  let snapshot = null;
  let receivedAt = 0;
  let serverDown = false;
  let drawerOpen = false;
  const signatures = {};
  let tankEditing = false;
  let tankBusy = false;
  let wateringEditing = false;
  let wateringBusy = false;
  const wateringStatuses = {
    starting: "watering.starting", running: "watering.running", stopping: "watering.stopping",
    unmanaged: "watering.unmanaged", blocked: "watering.blocked", scheduled: "watering.scheduled", disabled: "watering.disabled"
  };
  const wateringReasons = {
    setup: "watering.reason.setup", schedule: "watering.reason.schedule", role: "watering.reason.role",
    assignment: "watering.reason.assignment", connection: "watering.reason.connection", supply: "watering.reason.supply",
    port: "watering.reason.port", busy: "watering.reason.busy", storage: "watering.reason.storage", budget: "watering.reason.budget",
    publish: "watering.reason.publish", duration: "watering.reason.duration", manual: "watering.reason.manual",
    disabled: "watering.reason.disabled", restart: "watering.reason.restart", shutdown: "watering.reason.shutdown",
    unconfirmed: "watering.reason.unconfirmed", missed: "watering.reason.missed",
    thirst: "watering.reason.thirst"  // hook: the need check (vgrow/thirst.py) skipped the slot; its words are e.detail
  };
  const wateringSetup = new Set(["setup", "schedule", "role", "assignment", "port"]);  // the Banner's lead: setup needed
  const tankLead = { start_button: ["tank.lead.start", "warn"], no_response: ["tank.lead.check", "bad"],
    low: ["tank.lead.low", "warn"] };
  const wateringEvents = { start: "watering.event.start", stopped: "watering.event.stopped", skipped: "watering.event.skipped", due: "watering.event.due" };

  function node(tag, cls, text) { return make(tag, cls, text); }
  function link(text, href, cls = "text-link") {
    const a = node("a", cls, text); a.href = href; return a;
  }
  function replace(id, signature, build) {
    const sig = JSON.stringify([LANG, signature]);
    if (signatures[id] === sig) return;
    signatures[id] = sig;
    $(id).replaceChildren(...build());
  }

  // Exported for offline route checks. Resolve old links from Hank and saved bookmarks without server rewrites.
  function resolve(hash) {
    let path = (hash || "").replace(/^#\/?/, "");
    path = legacy[path] || path;
    let [page, tab] = path.split("/");
    if (!Object.hasOwn(pages, page)) page = "overview";
    if (panels[page]) tab = panels[page].includes(tab) ? tab : panels[page][0];
    else tab = "";
    return { page, tab, path: page === "wiki" ? path : [page, tab].filter(Boolean).join("/") };
  }

  function closeNav(focus = false) {
    drawerOpen = false;
    document.body.classList.remove("nav-open");
    $("nav-backdrop").hidden = true;
    $("menu-btn").setAttribute("aria-expanded", "false");
    $("app-sidebar").inert = innerWidth < 960;
    if (focus) $("menu-btn").focus();
  }

  function route(focus = false, scroll = true) {
    const next = resolve(location.hash);
    const changed = !current || next.path !== current.path;
    current = next;
    if (next.page !== "wiki" && location.hash !== `#${next.path}`) {
      window.history.replaceState(null, "", `#${next.path}`);
    }
    for (const page of document.querySelectorAll("[data-page]")) page.hidden = page.dataset.page !== next.page;
    for (const [page] of Object.entries(panels)) {
      for (const panel of document.querySelectorAll(`[data-${page}-panel]`)) {
        panel.hidden = panel.getAttribute(`data-${page}-panel`) !== (next.page === page ? next.tab : panels[page][0]);
      }
    }
    for (const a of document.querySelectorAll("[data-route-link], [data-tab]")) {
      const on = a.dataset.routeLink ? a.dataset.routeLink === next.page : a.dataset.tab === next.path;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    // Move the same players together; navigation never duplicates their streams.
    const slot = $(next.page === "devices" && next.tab === "camera" ? "devices-camera-slot" : "overview-camera-slot");
    if ($("camera-views").parentElement !== slot) slot.append($("camera-views"));
    refreshCameraPlayback();
    $("page-title").textContent = t(pages[next.page]);
    document.title = `${t(pages[next.page])} · ${t("meta.title")}`;
    if (changed) closeNav();
    if (next.page === "analytics") requestAnimationFrame(() => { if (history.length) drawHistory(); renderHistoryData(); });
    if (changed && scroll && next.page !== "wiki") window.scrollTo(0, 0);
    if (changed && focus && next.page !== "wiki") $("page-title").focus({ preventScroll: true });
    window.PlantWorkspace?.context();
  }

  function navigate(path) {
    if (location.hash !== `#${path}`) location.hash = path;
    route(false, false);
  }

  function reveal(target) {
    const page = target.closest("[data-page]");
    if (!page) return;
    const panel = target.closest("[data-grow-panel]");
    navigate(panel ? `grow/${panel.dataset.growPanel}` : page.dataset.page);
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
  }

  function triggerText(r) {
    const units = { temp: "°C", probe_temp: "°C", humidity: "%", probe_humidity: "%", water_level: "%" };
    return r.op ? t("alerts.rule", { op: r.op, limit: qty(num(r.limit), units[r.metric] || ""), minutes: amount(r.for_minutes) })
      : t("alerts.ruleNoLimit", { minutes: amount(r.for_minutes) });
  }

  function issues(s) {
    const result = [];
    const tank = s.automation?.tank;
    if (tank?.warning) result.push({ kind: "warn", title: t("tank.title"), text: tankWarning(tank),
      href: "#automation", action: t("cockpit.reviewAutomation") });
    const box = s.autopilot?.boxes?.[0];
    if (serverDown) result.push({ kind: "bad", title: t("cockpit.serverUnavailable"), text: t("cockpit.cachedReadings"), href: "#settings", action: t("cockpit.reviewConnection") });
    else if (!s.demo && (!s.online || !s.mqtt)) result.push({ kind: "bad", title: t("status.offline"), text: offlineText(s), href: "#settings", action: t("cockpit.reviewConnection") });
    const rules = (s.alerts || []).filter((r) => r.active);
    for (const r of rules) result.push({ kind: "bad", title: r.label, text: triggerText(r), href: "#activity/rules", action: t("cockpit.viewRule"), metric: r.metric });
    if (s.sensors_age == null || s.sensors_age > 300) result.push({ kind: "warn", title: t("cockpit.staleReadings"), text: t("tiles.measured", { ago: ago(s.sensors_age) }), href: "#analytics", action: t("cockpit.reviewReadings") });
    if (!s.climate || [["temp", "probe_temp"], ["humidity", "probe_humidity"], ["vpd", "probe_vpd"]].some((keys) => keys.every((key) => s.climate[key] == null))) {
      result.push({ kind: "warn", title: t("cockpit.partialReadings"), text: t("cockpit.partialReadingsWhy"), href: "#analytics", action: t("cockpit.reviewReadings") });
    }
    if (s.pest?.open) result.push({ kind: "bad", title: t("pest.title"), text: s.pest.open.text, href: "#grow/insights", action: t("pest.look") });
    else if (s.pest?.confirmed) result.push({ kind: "warn", title: t("pest.confirmedTitle"), text: s.pest.confirmed.text, href: "#grow/insights", action: t("pest.look") });
    const plan = s.grow_plan;
    const nextStep = plan?.todo?.find((step) => !step.done);  // the plan's next step only: one thing at a time
    if (nextStep) result.push({ kind: "info", title: nextStep.title, text: plan.title, href: "#automation", action: plan.labels["btn.open"] });
    const plant = s.plant;
    if (plant?.configured && (!plant.stage || plant.stage.set_by !== "manual")) result.push({ kind: "info", title: t("cockpit.confirmStage"), text: t("cockpit.confirmStageWhy"), href: "#grow/growth", action: t("cockpit.reviewStage") });
    for (const c of plant?.checks || []) {
      if (!["high", "low"].includes(c.status) || rules.some((r) => r.metric === c.metric)) continue;
      const light = c.metric === "light_percent", device = checkDevice(box, c.metric);
      // On auto the autopilot, the humidifier rule or the light plan does what the card's advice says: the card shows
      // the reading and, under it, what is being done (owner, 07.11.2026: "it's on auto, he should do it himself")
      const reading = c.actual == null ? "" : `${qty(readingText(c.actual, c.unit), c.unit)} · ${rangeText(c)}`;
      const plan = light ? s.grow_plan?.light?.text : null;
      result.push({ kind: "warn", title: c.label || c.metric, text: pilotRuns(s, device) ? reading : c.advice || reading,
        handling: device && ["off", "manual"].includes(device.control_state) ? t("cockpit.manualOwnership")
          : plan ? keepUnits(plan) : device?.status ? keepUnits(device.status) : "", href: "#automation", action: t("cockpit.reviewAutomation") });
    }
    for (const d of box?.devices || []) {
      if (!["needs_setup", "paused"].includes(d.control_state)) continue;
      result.push({ kind: "info", title: d.name, text: keepUnits(d.status), href: "#automation", action: t("cockpit.reviewAutomation") });
    }
    return result;
  }

  function issueCard(issue) {
    const article = node("article", `action-item ${issue.kind}`);
    article.append(node("span", "action-symbol", issue.kind === "info" ? "i" : "!"));
    const body = node("div");
    body.append(node("h4", "", issue.title), node("p", "", issue.text));
    if (issue.handling) body.append(node("p", "", issue.handling));
    body.append(link(issue.action, issue.href));
    article.append(body);
    return article;
  }

  function healthy() {
    const div = node("div", "all-good");
    div.append(node("span", "action-symbol", "✓"), node("h4", "", t("cockpit.onTrack")), node("p", "", t("cockpit.onTrackNote")));
    return div;
  }

  function renderActions(s) {
    const list = issues(s);
    const alarmCount = (s.alerts || []).filter((r) => r.active).length;
    $("notification-count").textContent = amount(alarmCount);
    $("notification-count").hidden = !alarmCount;
    $("notification-count").parentElement.setAttribute("aria-label", t("cockpit.notificationCount", { n: amount(alarmCount) }));
    $("action-count").textContent = list.length ? amount(list.length) : t("check.ok");
    $("action-count").className = `pill sm ${list.length ? "warn" : "ok"}`;
    const urgent = list.find((issue) => issue.kind === "bad");
    $("overview-urgent").hidden = !urgent;
    if (urgent) { $("overview-urgent").textContent = `! ${urgent.title}`; $("overview-urgent").href = urgent.href; }
    replace("action-items", list.slice(0, 3), () => list.length ? list.slice(0, 3).map(issueCard) : [healthy()]);
    const checks = (s.alerts || []).filter((r) => !r.active).length;
    $("healthy-checks").textContent = t("cockpit.healthyChecks", { n: amount(checks) });
    replace("active-alerts", list, () => list.length ? list.map(issueCard) : [healthy()]);
  }

  function physicalValue(d, s) {
    const stale = serverDown || (!s.demo && (!s.online || !s.mqtt)) || s.shadow_age == null || s.shadow_age > 300;
    let value;
    if (d.id === "humidifier" || d.id === "pump") {
      const on = s.ports?.[d.port || "usb2"]?.on;
      if (on == null) return "–";
      value = t(on ? "ctl.on" : "ctl.off");
    } else {
      if (d.value == null) return "–";
      value = d.id === "light" ? qty(num(d.value), "%") : String(d.value);
    }
    return stale ? t("cockpit.lastKnown", { value }) : value;
  }

  function renderAutomation(s) {
    const boxes = s.autopilot?.boxes || [];
    const box = boxes[0];
    $("overview-mode").textContent = box && modeKeys[box.mode] ? t(modeKeys[box.mode]) : t("check.unknown");
    $("overview-mode-note").textContent = box ? t("cockpit.openAutomation") : t("cockpit.automationUnavailable");
    if (box?.mode === "off" && s.automation?.enabled) $("overview-mode-note").textContent = t("cockpit.humidityIndependent");
    const pump = s.grow_plan?.pump;
    replace("automation-summary", [boxes, s.ports, pump, s.shadow_age == null || s.shadow_age > 300, serverDown, s.online, s.mqtt], () => {
      if (!box) return [node("p", "hint", t("cockpit.automationUnavailable"))];
      const grid = node("div", "summary-devices");
      const devices = [...(box.devices || []), ...(box.humidifier ? [{ ...box.humidifier, id: "humidifier", name: t("pilot.device.humidifier") }] : []),
        ...(pump ? [pump] : [])];
      for (const d of devices) {
        const a = link("", "#automation", "summary-device");
        const status = exhaustHolding(d) ? "pilot.device.holding" : deviceStatus[d.control_state] || "check.unknown";
        const stale = serverDown || (!s.demo && (!s.online || !s.mqtt)) || s.shadow_age == null || s.shadow_age > 300;
        a.append(node("b", "", d.name), node("strong", "", physicalValue(d, s)), node("small", stale || ["needs_setup", "paused"].includes(d.control_state) ? "warn" : "", stale ? t("cockpit.cachedState") : d.short || t(status)));
        if (d.status) a.title = keepUnits(d.status);
        grid.append(a);
      }
      return [grid];
    });
    for (const kind of ["light", "cfan", "dfan"]) {
      const d = box?.devices?.find((entry) => entry.id === kind);
      const automatic = box?.mode === "auto" && d?.level === "auto";
      const freshness = serverDown || s.shadow_age == null || s.shadow_age > 300 ? t("cockpit.cachedState") : t("tiles.device", { ago: ago(s.shadow_age) });
      const timer = kind === "light" ? s.light_schedule : null;
      const mode = timer?.settings?.enabled
        ? t(timer.status === "manual" ? "lightTimer.manual" : timer.settings.from_requirements ? "lightAuto.mode" : "lightTimer.title")
        : automatic && timer?.status !== "stopped" ? t("pilot.mode.auto") : t("auto.level.manual");
      $(`device-${kind}-meta`).textContent = `${mode} · ${freshness}`;
    }
    // The value above the control is always the measured state, even while a fan target is being edited.
    for (const kind of ["cfan", "dfan"]) {
      const f = s[kind] || {};
      $(`${kind}-val`).textContent = f.step == null ? "–" : kind === "cfan" && f.natural_wind ? t("fan.windStep", { step: f.step }) : `${f.step}/10`;
    }
    $("light-number").disabled = $("light-range").disabled;
    $("light-number-send").disabled = $("light-range").disabled;
    if (document.activeElement !== $("light-number") && !dragging) $("light-number").value = $("light-range").value;
    const pending = Object.values(s.pending || {}).filter((p) => p.status === "pending").length;
    if (pending) $("overview-mode-note").textContent = t("cockpit.commandsPending", { n: amount(pending) });
  }

  function renderNext(s) {
    const p = s.plant;
    $("overview-plant").textContent = p?.plant?.strain || t("plant.title");
    $("overview-context").textContent = [p?.day != null ? t("statusline.day", { n: p.day }) : "",
      p?.stage ? p.stage.set_by === "manual" ? stageShort(p.stage.label) : t("stage.estimated", { stage: stageShort(p.stage.label) }) : t("stage.none"),
      p?.height ? qty(fmt(p.height.cm, 0), "cm") : ""].filter(Boolean).join(" · ");
    $("top-device").textContent = s.device || "VGrow";
    $("grow-empty").hidden = !!p?.configured;
    $("feeding-empty").hidden = !!p?.feeding;
    $("insights-empty").hidden = !!s.vision || !!s.pest?.open || !!s.pest?.confirmed;
    const feedNext = p?.feeding?.not_before ? p.feeding.warning : $("feed-nextchange").hidden ? "" : $("feed-nextchange").textContent;
    const task = !p?.configured ? [t("cockpit.growEmpty"), "", "#grow/growth", t("cockpit.openGrow")]
      : p.stage?.set_by !== "manual" ? [t("cockpit.confirmStage"), t("cockpit.confirmStageWhy"), "#grow/growth", t("cockpit.reviewStage")]
      : p.feeding && !["ok", "water_only", "harvest", "label_ends"].includes(p.feeding.state)
        ? [t("cockpit.reviewFeeding"), $("feed-problem").textContent || $("feed-meta").textContent, "#grow/feeding", t("cockpit.openFeeding")]
        : p.height_age > 2 || !p.height ? [t("cockpit.measureHeight"), $("plant-height-due").textContent, "#grow/growth", t("cockpit.openGrow")]
          : [t("cockpit.nextFeeding"), feedNext || $("feed-meta").textContent || t("cockpit.noScheduledTask"), "#grow/feeding", t("cockpit.openFeeding")];
    replace("next-task", task, () => [node("p", "next-title", task[0]), node("p", "next-copy", task[1]), link(task[3], task[2])]);
    replace("journal-summary", p?.heights || [], () => {
      const list = node("ol", "timeline");
      for (const h of [...(p?.heights || [])].reverse()) {
        const li = node("li"), time = node("time", "", dateLong(h.date)); time.dateTime = h.date;
        li.append(time, node("span", "", `${h.estimated ? "≈ " : ""}${qty(fmt(h.cm, 0), "cm")}`)); list.append(li);
      }
      if (!list.children.length) list.append(node("li", "empty", t("cockpit.journalEmpty")));
      return [list];
    });
  }

  function renderActivity(s) {
    const boxes = s.autopilot?.boxes || [];
    const recent = s.automation?.recent || (s.automation?.last_action ? [s.automation.last_action] : []);
    replace("activity-history", [boxes.map((b) => [b.id, b.log]), recent], () => {
      const events = [];
      for (const box of boxes) for (const e of box.log || []) events.push({ at: e.at, build: () => apLogItem(box, e) });
      for (const e of recent) events.push({ at: typeof e.time === "number" ? e.time : new Date(e.time).getTime() / 1000, build: () => {
        const li = node("li");
        const d = new Date(typeof e.time === "number" ? e.time * 1000 : e.time);
        const time = node("time", "", Number.isNaN(d.getTime()) ? "–" : when(d));
        if (!Number.isNaN(d.getTime())) time.dateTime = d.toISOString();
        const [plain, withValue] = AUTO_ACTION[e.action] || [];
        const text = (e.action === "humidifier_on" && e.value != null && humOnText(e))
          || (withValue && e.value != null ? t(withValue, { value: qty(fmt(e.value, 1), "%") }) : plain ? t(plain) : e.action);
        li.append(time, node("span", "", text)); return li;
      } });
      const list = node("ol", "timeline ap-log");
      for (const e of events.sort((a, b) => b.at - a.at)) list.append(e.build());
      if (!events.length) list.append(node("li", "empty", t("pilot.stepsEmpty")));
      return [list];
    });
  }

  function renderSettings(s) {
    const rows = [[t("cockpit.deviceIdentity"), s.device || "VGrow"], [t("cockpit.cloudConnection"), serverDown ? t("status.gone") : t(s.online ? "status.online" : "status.offline")],
      [t("cockpit.commandConnection"), serverDown ? t("status.gone") : t(s.mqtt ? "status.online" : "status.offline")],
      [t("tiles.label"), t("tiles.measured", { ago: ago(s.sensors_age) })], [t("cockpit.deviceState"), t("tiles.device", { ago: ago(s.shadow_age) })],
      [t("cockpit.notifications"), t(s.ntfy ? "alerts.ntfyOk" : "alerts.ntfyNo")]];
    replace("settings-integrations", rows, () => rows.map(([label, value]) => {
      const div = node("div"); div.append(node("dt", "", label), node("dd", "", value)); return div;
    }));
  }

  function tankWarning(tank) {
    if (tank.warning === "start_button") return t("tank.startButton");
    return tank.warning === "no_response" ? t("tank.noResponse", { minutes: tank.response_minutes })
      : t("tank.low", { remaining: qty(fmt(tank.remaining_ml_estimate, 0), "ml") });
  }

  function wateringWhen(at) {
    return new Intl.DateTimeFormat(LANG === "de" ? "de-DE" : "en-GB", {
      timeZone: "Europe/Berlin", dateStyle: "medium", timeStyle: "short"
    }).format(new Date(at * 1000));
  }

  function renderWatering(s) {
    const w = s.watering;
    $("watering").hidden = !w;
    if (!w) return;
    $("watering-status").textContent = t(wateringStatuses[w.status] || "watering.blocked");
    $("watering-power").textContent = t(w.reported_on === true ? "watering.powerOn" : w.reported_on === false ? "watering.powerOff" : "watering.powerUnknown");
    renderWateringCountdown();
    $("watering-next").textContent = w.next_at ? t("watering.next", { time: wateringWhen(w.next_at) }) : t("watering.noNext");
    const reason = w.error || w.active?.reason || w.blocked;
    const warning = w.status === "unmanaged" ? t("watering.unmanagedHelp")
      : reason && wateringReasons[reason] ? t(wateringReasons[reason]) : "";
    // Hook: the Durst-Index next to Einmal gießen, with a warning when the pot is not due (Einmal gießen stays the
    // owner's own and is never asked; vgrow/thirst.py builds the sentence). Hook: one Banner for the card (design
    // system), the watering's own condition first and the need check's warning as its second sentence; a need that
    // is fine is a hint under the schedule.
    const need = s.thirst && s.thirst.manual;
    const needWarn = need && need.warn ? need.text : "";
    const [lead, variant] = w.status === "unmanaged" ? ["watering.lead.pump", "bad"]
      : !warning ? ["watering.reason.thirst", "warn"]
      : w.error ? ["watering.lead.paused", "bad"]
      : w.active?.reason ? ["watering.lead.stopping", "warn"]
      : wateringSetup.has(reason) ? ["watering.lead.setup", "warn"] : ["watering.lead.paused", "warn"];
    $("watering-warning").hidden = !warning && !needWarn;
    if (warning || needWarn) dsBanner($("watering-warning"), variant, t(lead), [warning, needWarn].filter(Boolean).join(" "));
    $("watering-need").hidden = !need || !!need.warn;
    if (need && !need.warn) $("watering-need").textContent = need.text;
    $("watering-run").disabled = serverDown || wateringBusy || !!w.blocked || !!w.active;
    $("watering-stop").disabled = serverDown || wateringBusy || (!w.active && w.reported_on === false);
    $("watering-disable").hidden = !w.settings.enabled;
    $("watering-disable").disabled = serverDown || wateringBusy;
    if (!wateringEditing) {
      $("watering-duration").value = w.settings.duration_s ?? "";
      $("watering-times").value = w.settings.times.join(", ");
      $("watering-every").value = w.settings.every_days;
      $("watering-first").value = w.settings.first_date ?? "";
      $("watering-enabled").checked = w.settings.enabled;
      $("watering-power-confirmed").checked = w.settings.power_confirmed;
    }
    replace("watering-events", [w.events, LANG], () => w.events.slice().reverse().map((e) => node("li", "", [
      wateringWhen(e.at), t(wateringEvents[e.kind] || "watering.event.due"),
      e.slot || "", e.reason && wateringReasons[e.reason] ? t(wateringReasons[e.reason]) : "",
      e.detail ? e.detail[LANG] || e.detail.de || "" : ""  // the need check's own words, both languages
    ].filter(Boolean).join(" · "))));
  }

  function renderWateringCountdown() {
    const w = snapshot?.watering;
    if (!w) return;
    const remaining = Math.max(0, Math.ceil((w.remaining_s ?? 0) - (Date.now() - receivedAt) / 1000));
    $("watering-countdown").textContent = serverDown ? t("watering.countdownOffline")
      : w.status === "stopping" ? t("watering.waitingOff")
      : w.remaining_s != null ? t("watering.remaining", { seconds: remaining }) : "";
  }

  async function wateringSend(body) {
    if (wateringBusy) return;
    wateringBusy = true;
    const controls = [...$("watering").querySelectorAll("button, input")];
    for (const el of controls) el.disabled = true;
    setFb("fb-watering", t("fb.sending"));
    try {
      await api("/api/cmd", body);
      wateringEditing = false;
      await poll(true);
      setFb("fb-watering", t("watering.saved"), "ok");
    } catch (err) { setFb("fb-watering", err.message, "bad"); }
    finally {
      wateringBusy = false;
      for (const el of controls) el.disabled = false;
      if (snapshot) renderWatering(snapshot);
    }
  }

  function renderTank(s) {
    const tank = s.automation?.tank;
    $("humidifier-tank").hidden = !tank;
    if (!tank) return;
    $("tank-model").hidden = !tank.model;
    if (tank.model) {
      const m = tank.model;
      $("tank-model-name").textContent = `${m.name} · ${m.model_number} · ${qty(fmt(m.capacity_ml, 0), "ml")}`;
      $("tank-model-specs").textContent = t("tank.modelSpecs", { min: m.output_min_ml_h, max: m.output_max_ml_h,
        continuous: m.continuous_hours, intermittent: m.intermittent_hours });
      $("tank-model-manual").href = m.source;
    }
    // Hook: the warning as the card's Banner, its lead naming the situation (design system)
    const [lead, variant] = tankLead[tank.warning] || tankLead.low;
    $("tank-warning").hidden = !tank.warning;
    if (tank.warning) dsBanner($("tank-warning"), variant, t(lead), tankWarning(tank));
    $("tank-button-note").hidden = !tank.button_start;
    $("tank-mist-confirm").hidden = !tank.button_start;
    $("tank-mist-confirm").disabled = tankBusy || s.automation.port_device !== "humidifier" || s.ports?.usb2?.on !== true;
    $("tank-runtime").textContent = t(tank.refilled_at == null ? "tank.runtimeTotal" : tank.button_start ? "tank.runtimeMist" : "tank.runtimeCycle", {
      hours: fmt((tank.refilled_at == null ? tank.runtime_s : tank.cycle_runtime_s) / 3600, 1)
    });
    $("tank-remaining").textContent = tank.remaining_ml_estimate == null ? t("tank.unknown")
      : t("tank.estimate", { used: qty(fmt(tank.used_ml_estimate, 0), "ml"), remaining: qty(fmt(tank.remaining_ml_estimate, 0), "ml") });
    $("tank-rate").textContent = tank.rate_ml_h == null ? t("tank.rateUnknown")
      : t(tank.rate_source === "refills" ? "tank.rateLearned" : "tank.rateOwner", { rate: qty(fmt(tank.rate_ml_h, 1), "ml/h") });
    $("tank-last-refill").textContent = tank.refilled_at == null ? t("tank.noRefill")
      : t("tank.lastRefill", { time: when(new Date(tank.refilled_at * 1000)), count: tank.refill_count,
        amount: qty(fmt(tank.starting_ml, 0), "ml") });
    if (!tankEditing) {
      $("tank-capacity").value = tank.capacity_ml;
      $("tank-output").value = tank.rate_source === "owner" ? tank.rate_ml_h : "";
      $("tank-has-button").checked = !!tank.button_start;
      $("tank-mist-mode").value = tank.mist_mode ?? "unknown";
    }
    const enabled = s.automation.port_device === "humidifier";
    if (!$("tank-fill-amount").value) $("tank-fill-amount").value = tank.starting_ml ?? tank.capacity_ml;
    $("tank-fill-amount").max = tank.capacity_ml;
    for (const id of ["tank-refill", "tank-empty-refill"]) $(id).disabled = !enabled || tankBusy;
    replace("tank-refills", tank.refills, () => tank.refills.map((event) => node("li", "", t("tank.refillLine", {
      time: when(new Date(event.at * 1000)), kind: t(event.was_empty ? "tank.emptyKind" : "tank.fullKind"),
      amount: qty(fmt(event.amount_ml ?? tank.capacity_ml, 0), "ml")
    }))));
  }

  async function tankSave(body) {
    if (tankBusy) return;
    tankBusy = true;
    const controls = [...$("humidifier-tank").querySelectorAll("button")];
    for (const button of controls) button.disabled = true;
    setFb("fb-tank", t("fb.sending"));
    try {
      await api("/api/cmd", body);
      tankEditing = false;
      await poll(true);
      setFb("fb-tank", t("tank.saved"), "ok");
    } catch (err) { setFb("fb-tank", err.message, "bad"); }
    finally {
      tankBusy = false;
      for (const button of controls) button.disabled = false;
      if (snapshot) renderTank(snapshot);
    }
  }

  function renderFreshness() {
    if (!snapshot) return;
    const elapsed = Math.max(0, (Date.now() - receivedAt) / 1000);
    const sensorAge = snapshot.sensors_age == null ? null : snapshot.sensors_age + elapsed;
    const shadowAge = snapshot.shadow_age == null ? null : snapshot.shadow_age + elapsed;
    $("age").replaceChildren(chip(t("tiles.measured", { ago: ago(sensorAge) }), serverDown || sensorAge == null || sensorAge > 300 ? "warn" : ""),
      chip(t("tiles.device", { ago: ago(shadowAge) }), serverDown || shadowAge == null || shadowAge > 600 ? "warn" : ""));
    for (const metric of ["temp", "hum", "vpd"]) $("tile-" + metric).classList.toggle("stale", serverDown || sensorAge == null || sensorAge > 300);
    const cameras = cameraSources(snapshot);
    const camerasDown = cameras.filter(cam => serverDown || cam.reachable === false).length;
    const cameraOkay = cameras.length > camerasDown;  // at least one delivers
    const hiddenOnes = (snapshot.cameras || []).some(cam => cam.configured && cam.hidden);  // their own line says so
    $("camera-empty").hidden = cameras.length > 0 || hiddenOnes;
    $("devices-camera-empty").hidden = cameras.length > 0 || hiddenOnes;
    $("device-camera-title").textContent = cameras.length > 1 ? t("cockpit.camerasTitle") : cameras[0]?.name || "GrowCam";
    $("device-camera-status").textContent = !cameraOkay ? t("status.offline") : camerasDown ? t("cockpit.camerasPartlyOffline", { down: amount(camerasDown), total: amount(cameras.length) }) : t("status.online");
    $("device-camera-status").className = `pill sm ${cameraOkay && !camerasDown ? "ok" : "warn"}`;
    renderCameraProgress(serverDown);
    window.PlantWorkspace?.context();
  }

  function sync(s) {
    snapshot = s;
    receivedAt = Date.now();
    serverDown = false;
    renderActions(s); renderAutomation(s); renderNext(s); renderActivity(s); renderSettings(s); renderTank(s); renderWatering(s); renderFreshness();
    route();
  }

  function renderHistoryData() {
    if (current?.page !== "analytics") return;
    const selected = history.slice(-120);
    const kind = snapshot?.climate?.water_kind;
    replace("history-data", [hours, selected, kind], () => {
      if (!selected.length) return [node("p", "hint", t("history.empty"))];
      const table = node("table", "history-table");
      table.append(node("caption", "hint", t("cockpit.tableRows", { shown: amount(selected.length), total: amount(history.length) })));
      const head = node("thead"), tr = node("tr");
      const headers = [t("chart.time"), `${t("chart.temp")} · ${t("tiles.inBox")} (°C)`, `${t("chart.hum")} · ${t("tiles.inBox")} (%)`,
        `${t("chart.vpd")} · ${t("tiles.inBox")} (kPa)`, `${t("chart.temp")} · ${t("tiles.atPlant")} (°C)`,
        `${t("chart.hum")} · ${t("tiles.atPlant")} (%)`, `${t("chart.vpd")} · ${t("tiles.atPlant")} (kPa)`, `${t("chart.water")} (%)`, `${t("chart.wtemp")} (°C)`];
      for (const text of headers) { const th = node("th", "", text); th.scope = "col"; tr.append(th); }
      head.append(tr); table.append(head);
      const body = node("tbody");
      for (const row of selected) {
        const line = node("tr");
        const level = kind !== "wet_dry" && kind !== null && row.waterLv != null && row.waterLv >= 0 && row.waterLv <= 100 ? row.waterLv : null;
        const values = [stamp(new Date(row.time * 1000)), fmt(row.inTemp ?? row.bTemp), fmt(row.inHumi ?? row.bHumi), fmt(row.inVpd ?? row.bVpd, 2),
          fmt(row.pTemp), fmt(row.pHumi), fmt(row.pVpd, 2), fmt(level), fmt(row.waterT)];
        for (const value of values) line.append(node("td", "", value));
        body.append(line);
      }
      table.append(body); return [table];
    });
  }

  window.Cockpit = { resolve, navigate, reveal, sync };
  addEventListener("hashchange", () => route(true));
  addEventListener("resize", () => { if (!drawerOpen) $("app-sidebar").inert = innerWidth < 960; });
  document.addEventListener("vgrow:state", (e) => sync(e.detail));
  document.addEventListener("vgrow:history", renderHistoryData);
  document.addEventListener("vgrow:offline", () => {
    serverDown = true;
    if (snapshot) { renderActions(snapshot); renderAutomation(snapshot); renderSettings(snapshot); renderWatering(snapshot); renderFreshness(); }
    else {
      const issue = { kind: "bad", title: t("cockpit.serverUnavailable"), text: t("offline.server"), href: "#settings", action: t("cockpit.reviewConnection") };
      $("action-items").replaceChildren(issueCard(issue));
      $("active-alerts").replaceChildren(issueCard(issue));
      $("overview-context").textContent = t("cockpit.serverUnavailable");
      $("overview-mode").textContent = t("check.unknown");
      $("action-count").textContent = t("check.unknown");
    }
  });
  $("menu-btn").onclick = () => {
    if (drawerOpen) { closeNav(true); return; }
    drawerOpen = true;
    $("app-sidebar").inert = false;
    document.body.classList.add("nav-open");
    $("nav-backdrop").hidden = false;
    $("menu-btn").setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => { if (drawerOpen) $("app-sidebar").querySelector(".side-nav a.active, .side-nav a").focus(); });
  };
  $("nav-close").onclick = () => closeNav(true);
  $("nav-backdrop").onclick = () => closeNav(true);
  $("watering-settings").oninput = () => { wateringEditing = true; };
  $("watering-settings").onsubmit = (e) => {
    e.preventDefault();
    if (!e.target.reportValidity()) return;
    wateringSend({ action: "watering_config", duration_s: $("watering-duration").value === "" ? null : Number($("watering-duration").value),
      times: $("watering-times").value.split(",").map((v) => v.trim()).filter(Boolean),
      every_days: Number($("watering-every").value), first_date: $("watering-first").value || null,
      enabled: $("watering-enabled").checked, power_confirmed: $("watering-power-confirmed").checked });
  };
  $("watering-run").onclick = () => wateringSend({ action: "watering_start" });
  $("watering-stop").onclick = () => wateringSend({ action: "watering_stop" });
  $("watering-disable").onclick = () => wateringSend({ action: "watering_config", ...snapshot.watering.settings, enabled: false });
  setInterval(renderWateringCountdown, 1000);
  $("tank-settings").oninput = () => { tankEditing = true; };
  $("tank-settings").onsubmit = (e) => {
    e.preventDefault();
    if (!e.target.reportValidity()) return;
    tankSave({ action: "humidifier_tank_config", capacity_ml: Number($("tank-capacity").value),
      rate_ml_h: $("tank-output").value === "" ? null : Number($("tank-output").value),
      button_start: $("tank-has-button").checked, mist_mode: $("tank-mist-mode").value });
  };
  function confirmRefill(wasEmpty) {
    if (!$("tank-fill-amount").reportValidity()) return;
    tankSave({ action: "humidifier_refill", was_empty: wasEmpty, amount_ml: Number($("tank-fill-amount").value) });
  }
  $("tank-refill").onclick = () => confirmRefill(false);
  $("tank-empty-refill").onclick = () => confirmRefill(true);
  $("tank-mist-confirm").onclick = () => tankSave({ action: "humidifier_mist_confirm" });
  document.addEventListener("keydown", (e) => {
    if (!drawerOpen) return;
    if (e.key === "Escape") { e.preventDefault(); closeNav(true); }
    if (e.key !== "Tab") return;
    const focusable = [...$("app-sidebar").querySelectorAll("a, button")].filter((n) => !n.hidden && n.getClientRects().length && !n.disabled);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  $("app-sidebar").addEventListener("click", (e) => { if (e.target.closest("a, [data-wiki-open], [data-news-open]")) closeNav(); });
  document.querySelector(".skip-link").onclick = (e) => { e.preventDefault(); $("main-content").focus(); };
  $("light-number-send").onclick = () => {
    const input = $("light-number");
    if (!input.reportValidity() || input.disabled) return;
    $("light-range").value = input.value;
    $("light-range").dispatchEvent(new Event("change"));
  };
  $("light-number").onkeydown = (e) => { if (e.key === "Enter") $("light-number-send").click(); };
  setInterval(renderFreshness, 15000);
  setInterval(() => renderCameraProgress(serverDown), 500);
  route();
  if (state) sync(state);
})();
