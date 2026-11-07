/* General inventory and context selection. Device commands remain owned by their connected box. */
(() => {
  "use strict";
  const TEXT = {
  "boxes": "workspace.boxes",
  "box": "workspace.box",
  "addBox": "workspace.addBox",
  "editBox": "workspace.editBox",
  "saveBox": "workspace.saveBox",
  "boxName": "workspace.boxName",
  "boxType": "workspace.boxType",
  "capacity": "workspace.capacity",
  "notes": "workspace.notes",
  "boxConnectedNote": "workspace.boxConnectedNote",
  "boxUnboundNote": "workspace.boxUnboundNote",
  "archiveBox": "workspace.archiveBox",
  "addPlant": "workspace.addPlant",
  "editPlant": "workspace.editPlant",
  "plantName": "workspace.plantName",
  "variety": "workspace.variety",
  "varietyHint": "workspace.varietyHint",
  "varietyCatalogue": "workspace.varietyCatalogue",
  "flowering": "workspace.flowering",
  "floweringUnknown": "workspace.floweringUnknown",
  "floweringAuto": "workspace.floweringAuto",
  "floweringPhoto": "workspace.floweringPhoto",
  "factAuto": "workspace.factAuto",
  "factPhoto": "workspace.factPhoto",
  "factBloom": "workspace.factBloom",
  "factHeight": "workspace.factHeight",
  "factHarvest": "workspace.factHarvest",
  "factBy": "workspace.factBy",
  "spanUpTo": "workspace.spanUpTo",
  "spanFrom": "workspace.spanFrom",
  "fitSure": "workspace.fitSure",
  "fitLst": "workspace.fitLst",
  "fitTall": "workspace.fitTall",
  "heightShort": "workspace.heightShort",
  "heightAverage": "workspace.heightAverage",
  "heightTall": "workspace.heightTall",
  "difficultyEasy": "workspace.difficultyEasy",
  "difficultyMedium": "workspace.difficultyMedium",
  "difficultyHard": "workspace.difficultyHard",
  "harvestWindow": "workspace.harvestWindow",
  "controlPlant": "workspace.controlPlant",
  "controlHint": "workspace.controlHint",
  "controlStage": "workspace.controlStage",
  "controlStagePick": "workspace.controlStagePick",
  "recordedStage": "workspace.recordedStage",
  "startDate": "workspace.startDate",
  "approximate": "workspace.approximate",
  "approximateShort": "workspace.approximateShort",
  "quantity": "workspace.quantity",
  "quantityHint": "workspace.quantityHint",
  "recordOnly": "workspace.recordOnly",
  "savePlant": "workspace.savePlant",
  "createPlants": "workspace.createPlants",
  "archivePlant": "workspace.archivePlant",
  "archive": "workspace.archive",
  "archiveHint": "workspace.archiveHint",
  "archiveEmpty": "workspace.archiveEmpty",
  "restoreBoxFirst": "workspace.restoreBoxFirst",
  "restore": "workspace.restore",
  "close": "workspace.close",
  "retry": "workspace.retry",
  "busy": "workspace.busy",
  "plantCount": "workspace.plantCount",
  "plantCountOne": "workspace.plantCountOne",
  "observedSince": "workspace.observedSince",
  "observedHint": "workspace.observedHint",
  "observedNeedsStage": "workspace.observedNeedsStage",
  "observedAge": "workspace.observedAge",
  "observedUnknown": "workspace.observedUnknown",
  "entryDate": "workspace.entryDate",
  "manualHeight": "workspace.manualHeight",
  "cameraHeightEstimate": "workspace.cameraHeightEstimate",
  "cameraHeightRead": "workspace.cameraHeightRead",
  "heightByCamera": "workspace.heightByCamera",
  "heightCorrect": "workspace.heightCorrect",
  "approximateHeight": "workspace.approximateHeight",
  "heightSource": "workspace.heightSource",
  "startHere": "workspace.startHere",
  "guideIntro": "workspace.guideIntro",
  "guideDevices": "workspace.guideDevices",
  "guideDevicesText": "workspace.guideDevicesText",
  "guideRecord": "workspace.guideRecord",
  "guideRecordText": "workspace.guideRecordText",
  "guideObserve": "workspace.guideObserve",
  "guideObserveText": "workspace.guideObserveText",
  "guideFinish": "workspace.guideFinish",
  "guideFinishText": "workspace.guideFinishText",
  "guideModel": "workspace.guideModel",
  "openDevices": "workspace.openDevices",
  "origin": "workspace.origin",
  "originUnknown": "workspace.originUnknown",
  "originSeed": "workspace.originSeed",
  "originCutting": "workspace.originCutting",
  "originExisting": "workspace.originExisting",
  "originOther": "workspace.originOther",
  "finishRecord": "workspace.finishRecord",
  "finishHint": "workspace.finishHint",
  "finishDate": "workspace.finishDate",
  "finishSummary": "workspace.finishSummary",
  "finishConfirm": "workspace.finishConfirm",
  "finishSave": "workspace.finishSave",
  "completed": "workspace.completed",
  "viewHistory": "workspace.viewHistory",
  "downloadRecord": "workspace.downloadRecord",
  "historyHint": "workspace.historyHint",
  "coverageHint": "workspace.coverageHint",
  "recordEvents": "workspace.recordEvents",
  "historyLimit": "workspace.historyLimit",
  "eventCreated": "workspace.eventCreated",
  "eventImported": "workspace.eventImported",
  "eventUpdated": "workspace.eventUpdated",
  "eventHeight": "workspace.eventHeight",
  "eventNote": "workspace.eventNote",
  "eventArchived": "workspace.eventArchived",
  "eventRestored": "workspace.eventRestored",
  "eventCompleted": "workspace.eventCompleted",
  "workspace_completed": "workspace.workspace_completed",
  "capacityCount": "workspace.capacityCount",
  "notRecorded": "workspace.notRecorded",
  "plants": "workspace.plants",
  "plantRecords": "workspace.plantRecords",
  "plantRecord": "workspace.plantRecord",
  "noPhase": "workspace.noPhase",
  "connectedBox": "workspace.connectedBox",
  "notConnected": "workspace.notConnected",
  "emptyBox": "workspace.emptyBox",
  "emptyBoxHint": "workspace.emptyBoxHint",
  "record": "workspace.record",
  "journal": "workspace.journal",
  "existingProfile": "workspace.existingProfile",
  "existingProfileNote": "workspace.existingProfileNote",
  "profileUnavailable": "workspace.profileUnavailable",
  "recordSections": "workspace.recordSections",
  "age": "workspace.age",
  "days": "workspace.days",
  "recordedPhase": "workspace.recordedPhase",
  "height": "workspace.height",
  "importedNote": "workspace.importedNote",
  "cameraStage": "workspace.cameraStage",
  "cameraStageLink": "workspace.cameraStageLink",
  "reportTitle": "workspace.reportTitle",
  "reportWell": "workspace.reportWell",
  "reportPoor": "workspace.reportPoor",
  "reportNext": "workspace.reportNext",
  "reportUse": "workspace.reportUse",
  "reportLessons": "workspace.reportLessons",
  "lessonKeep": "workspace.lessonKeep",
  "lessonKept": "workspace.lessonKept",
  "lessonNew": "workspace.lessonNew",
  "lessonEdit": "workspace.lessonEdit",
  "lessonSave": "workspace.lessonSave",
  "lessonDelete": "workspace.lessonDelete",
  "lessonDeleteAgain": "workspace.lessonDeleteAgain",
  "lessonHow": "workspace.lessonHow",
  "lesson_text": "workspace.lesson_text",
  "lesson_plant": "workspace.lesson_plant",
  "lesson_missing": "workspace.lesson_missing",
  "reportEmpty": "workspace.reportEmpty",
  "reportHow": "workspace.reportHow",
  "sharedBoxNote": "workspace.sharedBoxNote",
  "measurements": "workspace.measurements",
  "date": "workspace.date",
  "saveMeasurement": "workspace.saveMeasurement",
  "heightHint": "workspace.heightHint",
  "recentMeasurements": "workspace.recentMeasurements",
  "noMeasurements": "workspace.noMeasurements",
  "journalText": "workspace.journalText",
  "addNote": "workspace.addNote",
  "journalEmpty": "workspace.journalEmpty",
  "boxPlants": "workspace.boxPlants",
  "managePlants": "workspace.managePlants",
  "noSharedData": "workspace.noSharedData",
  "inventoryOnly": "workspace.inventoryOnly",
  "boxScope": "workspace.boxScope",
  "workspace_invalid": "workspace.workspace_invalid",
  "workspace_date": "workspace.workspace_date",
  "workspace_capacity": "workspace.workspace_capacity",
  "workspace_missing": "workspace.workspace_missing",
  "workspace_stale": "workspace.workspace_stale",
  "workspace_limit": "workspace.workspace_limit",
  "workspace_linked": "workspace.workspace_linked",
  "workspace_before_start": "workspace.workspace_before_start",
  "workspace_date_conflict": "workspace.workspace_date_conflict",
  "workspace_height": "workspace.workspace_height",
  "workspace_unbound": "workspace.workspace_unbound",
  "workspace_context": "workspace.workspace_context",
  "workspace_profile": "workspace.workspace_profile",
  "workspace_storage": "workspace.workspace_storage",
  "statusGrowing": "workspace.statusGrowing",
  "stageDrying": "workspace.stageDrying",
  "stageCuring": "workspace.stageCuring",
  "stageReady": "workspace.stageReady",
  "stepHarvest": "workspace.stepHarvest",
  "stepArchive": "workspace.stepArchive",
  "stageDay": "workspace.stageDay",
  "harvest": "workspace.harvest",
  "harvestHint": "workspace.harvestHint",
  "harvestDate": "workspace.harvestDate",
  "harvestedOn": "workspace.harvestedOn",
  "afterHarvest": "workspace.afterHarvest",
  "stageSince": "workspace.stageSince",
  "moveTo": "workspace.moveTo",
  "stageDate": "workspace.stageDate",
  "toArchive": "workspace.toArchive",
  "momentHarvest": "workspace.momentHarvest",
  "momentDrying": "workspace.momentDrying",
  "momentCuring": "workspace.momentCuring",
  "momentReady": "workspace.momentReady",
  "momentArchive": "workspace.momentArchive",
  "backToSteps": "workspace.backToSteps",
  "eventHarvested": "workspace.eventHarvested",
  "eventStage": "workspace.eventStage",
  "workspace_harvested": "workspace.workspace_harvested",
  "workspace_not_harvested": "workspace.workspace_not_harvested",
  "prepTitle": "workspace.prepTitle",
  "prepReady": "workspace.prepReady",
  "prepHint": "workspace.prepHint",
  "prepSource": "workspace.prepSource",
  "prepDebris": "workspace.prepDebris",
  "prepClean": "workspace.prepClean",
  "prepWater": "workspace.prepWater",
  "prepCameras": "workspace.prepCameras",
  "prepInspect": "workspace.prepInspect"
};
  const tr = (key, values) => t(TEXT[key], values);
  // After the harvest (vgrow/harvest.py): each step's name, and Hank's moment for it (web/hank-scenes.js) with its sentence.
  const STAGE = { drying: "stageDrying", curing: "stageCuring", ready: "stageReady" };
  const MOMENT = { harvest: ["harvest", "momentHarvest"], drying: ["dry", "momentDrying"], curing: ["cure", "momentCuring"],
    ready: ["ready", "momentReady"], archive: ["archive", "momentArchive"] };
  const n = (tag, cls = "", text) => { const node = document.createElement(tag); node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  const byId = (id) => document.getElementById(id);
  let data = null, selected = { box: "box-1", plants: {} }, signature = "", busy = false, loading = false, snapshot = null, contextKey = "", loadedDay = "";
  try { const saved = JSON.parse(localStorage.getItem("vgrow.workspace") || "null"); if (saved?.box && saved.plants && typeof saved.plants === "object") selected = saved; } catch (_) { /* first visit or blocked storage */ }
  const top = n("section", "ws-switcher"), grow = n("section", "ws-grow"), summary = n("section", "card ws-overview");
  top.id = "workspace-switcher"; grow.id = "workspace-grow";
  byId("main-content").insertBefore(top, byId("page-overview")); byId("page-grow").prepend(grow); byId("page-overview").prepend(summary);
  for (const id of ["page-automation", "page-devices", "page-analytics", "page-activity"]) { const label = n("p", "ws-box-scope"); label.dataset.workspaceScope = ""; byId(id).prepend(label); }
  document.body.classList.add("workspace-loading");
  function currentBox() { return data?.boxes.find((b) => b.id === selected.box && !b.archived); }
  function plants(box = selected.box) { return data?.plants.filter((p) => p.box === box && !p.archived && !p.ended && !p.harvested) || []; }
  function afterHarvest() { return data?.plants.filter((p) => p.harvested && !p.archived && !p.ended) || []; }
  function currentPlant() { return plants().find((p) => p.id === selected.plants[selected.box]) || null; }
  function nativeBox() { return currentBox()?.controller === data?.native_box; }
  // An empty box (all harvested) is the controller's own context, as on the server (validate_context): Hank stays.
  function linkedPlant() { return nativeBox() && (!currentPlant() || currentPlant().id === data?.linked_plant); }
  function saveSelection() { try { localStorage.setItem("vgrow.workspace", JSON.stringify(selected)); } catch (_) { /* navigation still works */ } }
  function normalize() {
    if (!currentBox()) selected.box = data.boxes.find((b) => !b.archived)?.id;
    if (!currentPlant()) selected.plants[selected.box] = plants()[0]?.id || "";
    saveSelection();
  }
  function error(code) { return TEXT[code] ? tr(code) : ""; }
  function alert(parent, err) {
    let box = parent.querySelector(":scope > .ws-error"); if (!box) { box = n("p", "ws-error"); box.setAttribute("role", "alert"); parent.append(box); }
    box.textContent = err?.message || tr("workspace_storage");
  }
  function button(label, fn, cls = "") {
    const b = n("button", `btn ${cls}`, label); b.type = "button";
    b.onclick = async () => { b.disabled = true; try { await fn(b); } catch (err) { alert(b.closest(".ws-dialog, .ws-panel, .ws-grow, .ws-switcher") || b.parentElement, err); } finally { b.disabled = false; } };
    return b;
  }
  function link(label, href) { const a = n("a", "text-link", label); a.href = href; return a; }
  function dateText(value) { return value ? new Date(`${value}T12:00:00Z`).toLocaleDateString(LOCALE[LANG], { dateStyle: "medium", timeZone: "Europe/Berlin" }) : tr("notRecorded"); }
  function plantCount(value) { return tr(value === 1 ? "plantCountOne" : "plantCount", { n: value }); }
  function today() { return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
  function metric(label, value, note = "") { const node = n("div", "ws-metric"); node.append(n("span", "", label), n("strong", "", value), n("small", "", note)); return node; }
  async function refresh() {
    if (loading) return; loading = true;
    try { data = await api("/api/workspace"); loadedDay = today(); normalize(); signature = ""; render(); }
    catch (err) { if (!data) top.replaceChildren(n("p", "", tr("workspace_storage")), button(tr("retry"), refresh)); else alert(top, err); }
    finally { loading = false; }
  }
  async function write(body) {
    if (busy || !data) throw new Error(tr("busy")); busy = true;
    try {
      const result = await api("/api/workspace", { ...body, revision: data.revision }); data = result; loadedDay = today(); normalize(); signature = ""; render(); return result;
    } catch (err) { if (err.message === tr("workspace_stale")) await refresh(); throw err; }
    finally { busy = false; }
  }
  function selectBox(id) { selected.box = id; normalize(); signature = ""; render(); top.querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true }); }
  function selectPlant(id) { selected.plants[selected.box] = id; saveSelection(); signature = ""; render(); grow.querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true }); }
  function field(label, type = "text", value = "") {
    const wrap = n("label", "ws-field"); const input = n(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") input.type = type;
    input.value = value ?? ""; wrap.append(n("span", "", label), input); return { wrap, input };
  }
  function modal(title, trigger) {
    const dialog = n("dialog", "ws-dialog"); const head = n("div", "ws-dialog-head"), heading = n("h2", "", title);
    heading.id = "workspace-dialog-title"; dialog.setAttribute("aria-labelledby", heading.id);
    head.append(heading, button(tr("close"), () => dialog.close())); dialog.append(head); document.body.append(dialog);
    dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dialog.close(); } });
    dialog.addEventListener("close", () => { dialog.remove(); if (trigger?.isConnected) trigger.focus({ preventScroll: true }); else byId("workspace-switcher").querySelector("button")?.focus({ preventScroll: true }); }, { once: true });
    return dialog;
  }
  function startGuide(trigger) {
    const dialog = modal(tr("startHere"), trigger), body = n("div", "ws-guide");
    body.append(n("p", "ws-guide-intro", tr("guideIntro")));
    const steps = n("ol", "ws-guide-steps");
    for (const [title, text] of [["guideDevices", "guideDevicesText"], ["guideRecord", "guideRecordText"], ["guideObserve", "guideObserveText"], ["guideFinish", "guideFinishText"]]) {
      const step = n("li"); step.append(n("h3", "", tr(title)), n("p", "ws-muted", tr(text))); steps.append(step);
    }
    const actions = n("div", "ws-actions");
    actions.append(button(tr("addPlant"), () => { dialog.close(); if (location.hash === "#grow/growth") plantForm(null, trigger); else { window.addEventListener("hashchange", () => plantForm(null, trigger), { once: true }); location.hash = "#grow/growth"; } }, "primary"),
      button(tr("openDevices"), () => { dialog.close(); location.hash = "#devices/controls"; }));
    body.append(steps, n("p", "ws-profile-note", tr("guideModel")), actions); dialog.append(body); dialog.showModal();
  }
  function finishForm(plant, trigger) {
    const dialog = modal(tr("finishRecord"), trigger), form = n("form", "ws-form"), ended = field(tr("finishDate"), "date", today()), summary = field(tr("finishSummary"), "textarea");
    ended.input.name = "ended"; ended.input.required = true; ended.input.min = plant.started || "1900-01-01"; ended.input.max = today(); summary.input.name = "summary"; summary.input.maxLength = 4000;
    const confirm = n("label", "ws-check"), check = n("input"); check.type = "checkbox"; check.name = "confirmed"; check.required = true; confirm.append(check, tr("finishConfirm"));
    form.append(n("p", "ws-record-notes", plant.name), n("p", "ws-muted", tr("finishHint")), ended.wrap, summary.wrap, confirm);
    submit(form, tr("finishSave"), async () => { await write({ action: "plant_finish", id: plant.id, ended: ended.input.value, summary: summary.input.value, confirmed: check.checked }); moment(form, "archive", { label: tr("archive"), fn: () => archived(trigger) }); });
    dialog.append(form); dialog.showModal(); ended.input.focus();
  }
  // Hank's picture: a plain stage, or the still frame of a step, or the step played once (play). Without the scenes a sprout.
  function hankStage(cls, scene, play) {
    const host = n("span", `hk-scope ws-hank ${cls}`); host.setAttribute("aria-hidden", "true");
    try { const stage = window.HankScenes?.mount(host, { size: "mini" }); if (!stage) host.textContent = "🌱"; else if (scene) stage.moment(scene, !play); }
    catch (_) { host.textContent = "🌱"; /* the card works without its picture */ }
    return host;
  }
  // The step happened: Hank plays it once in the open dialog, in place of the form that did it.
  function moment(node, step, after) {
    const [scene, caption] = MOMENT[step], dialog = node.closest("dialog"), view = n("div", "ws-moment"), text = n("p", "ws-moment-caption", tr(caption));
    text.setAttribute("role", "status");
    const actions = n("div", "ws-actions"), done = button(tr("close"), () => dialog.close(), "primary"); actions.append(done);
    if (after) actions.append(button(after.label, () => { dialog.close(); after.fn(); }));
    view.append(hankStage("ws-moment-stage", scene, true), text, actions); node.replaceWith(view); done.focus({ preventScroll: true });
  }
  function harvestForm(plant, trigger) {
    const dialog = modal(tr("harvest"), trigger), form = n("form", "ws-form"), when = field(tr("harvestDate"), "date", today());
    when.input.name = "day"; when.input.required = true; when.input.min = plant.started || "1900-01-01"; when.input.max = today();
    form.append(n("p", "ws-record-notes", plant.name), n("p", "ws-muted", tr("harvestHint")), when.wrap);
    submit(form, tr("harvest"), async () => { await write({ action: "plant_harvest", id: plant.id, day: when.input.value }); moment(form, "harvest", { label: tr("backToSteps"), fn: () => stagesDialog(plant, trigger) }); });
    dialog.append(form); dialog.showModal(); when.input.focus();
  }
  // A harvested plant: where it stands, the next step (the date is the day the step began), notes, and the way to the archive.
  function stagesDialog(plant, trigger) {
    const dialog = modal(plant.name, trigger), p = data.plants.find((x) => x.id === plant.id) || plant, body = n("div", "ws-stages");
    const order = ["harvest", "drying", "curing", "ready", "archive"], at = order.indexOf(p.post_stage), steps = n("ol", "ws-steps");
    order.forEach((key, i) => { const li = n("li", i < at ? "is-done" : i === at ? "is-now" : "", tr(STAGE[key] || (key === "harvest" ? "stepHarvest" : "stepArchive"))); if (i === at) li.setAttribute("aria-current", "step"); steps.append(li); });
    body.append(hankStage("ws-moment-stage", MOMENT[p.post_stage][0], false), steps,
      n("p", "ws-muted", `${tr("harvestedOn", { date: dateText(p.harvested) })} · ${tr("stageSince", { date: dateText(p.post_since), n: p.post_days + 1 })}`));
    const when = field(tr("stageDate"), "date", today()), moves = n("div", "ws-actions"), next = { drying: "curing", curing: "ready", ready: "archive" }[p.post_stage];
    when.input.name = "day"; when.input.min = p.post_since; when.input.max = today();
    for (const stage of Object.keys(STAGE).filter((s) => s !== p.post_stage)) {
      moves.append(button(tr(STAGE[stage]), async () => { await write({ action: "plant_stage", id: p.id, stage, day: when.input.value }); moment(body, stage, { label: tr("backToSteps"), fn: () => stagesDialog(plant, trigger) }); }, stage === next ? "primary" : ""));
    }
    moves.append(button(tr("toArchive"), () => { dialog.close(); finishForm(p, trigger); }, next === "archive" ? "primary" : ""));
    body.append(n("h3", "", tr("moveTo")), when.wrap, moves);
    const note = n("form", "ws-form"), entry = field(tr("journalText"), "textarea"); entry.input.name = "text"; entry.input.required = true; entry.input.maxLength = 4000;
    note.append(entry.wrap); submit(note, tr("addNote"), async () => { await write({ action: "note", plant: p.id, day: today(), text: entry.input.value }); dialog.close(); stagesDialog(plant, trigger); });
    const notes = n("ol", "ws-journal");
    for (const item of p.journal.slice(0, 3)) { const li = n("li"); li.append(n("time", "ws-muted", dateText(item.day)), n("p", "", item.text)); notes.append(li); }
    body.append(n("h3", "", tr("journal")), note, notes, button(tr("viewHistory"), () => { dialog.close(); return recordHistory(p, trigger); }));
    dialog.append(body); dialog.showModal();
  }
  async function recordHistory(plant, trigger) {
    const dialog = modal(plant.name, trigger), body = n("div"); dialog.append(body); dialog.showModal();
    try {
      const report = await api(`/api/workspace/records/${encodeURIComponent(plant.id)}`), record = report.record;
      if (!dialog.isConnected) return;
      const download = link(tr("downloadRecord"), `/api/workspace/records/${encodeURIComponent(plant.id)}/export`); download.className = "btn primary"; download.setAttribute("download", "");
      body.append(n("p", "ws-profile-note", record.ended ? `${tr("completed")} · ${dateText(record.ended)}` : tr("historyHint")), download,
        n("p", "ws-muted", tr("coverageHint")));
      if (record.summary) body.append(n("p", "ws-record-notes", record.summary));
      if (record.notes) body.append(n("p", "ws-record-notes", record.notes));
      const dates = n("dl", "ws-history-facts");
      const after = record.harvested ? [[tr("harvestDate"), dateText(record.harvested)], [tr("afterHarvest"), `${tr(STAGE[record.post_stage])} · ${dateText(record.post_since)}`]] : [];
      for (const [label, value] of [[tr("startDate"), dateText(record.started)], [tr("recordedPhase"), record.phase[LANG] || record.phase.en || tr("noPhase")], [tr("observedSince"), dateText(record.phase_since)], ...after]) dates.append(n("dt", "", label), n("dd", "", value));
      body.append(dates);
      growReport(body, plant);
      const heights = n("ol", "ws-journal");
      for (const row of record.heights.slice(-100).reverse()) heights.append(n("li", "ws-archive-row", `${dateText(row.day)} · ${heightValue(row)} · ${heightNote(row)}`));
      body.append(n("h3", "", tr("measurements")), heights);
      const journalList = n("ol", "ws-journal");
      for (const row of record.journal.slice(-100).reverse()) { const li = n("li"); li.append(n("time", "ws-muted", dateText(row.day)), n("p", "", row.text)); journalList.append(li); }
      body.append(n("h3", "", tr("journal")), journalList);
      const labels = { created: "eventCreated", imported: "eventImported", updated: "eventUpdated", height: "eventHeight", note: "eventNote", archived: "eventArchived", restored: "eventRestored", completed: "eventCompleted", harvested: "eventHarvested", stage: "eventStage" }, events = n("ol", "ws-journal");
      for (const row of report.events.slice(-100).reverse()) {
        const li = n("li", "ws-archive-row"), at = new Date(row.at * 1000).toLocaleString(LOCALE[LANG], { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Berlin" });
        const phase = row.data.after?.phase;
        const step = STAGE[row.data.to] ? ` · ${tr(STAGE[row.data.to])}` : "";
        li.append(n("span", "", `${at} · ${tr(labels[row.kind] || "eventUpdated")}${phase ? ` · ${phase[LANG] || phase.en || ""}` : ""}${step}`)); events.append(li);
      }
      body.append(n("h3", "", tr("recordEvents")), events, n("p", "ws-muted", tr("historyLimit")));
    } catch (err) { if (dialog.isConnected) alert(body, err); }
  }
  // The record's report (vgrow/grow_report.py): what went well, what did not, what to watch next time, from the box's
  // measurements in the record's time and the stage card's bands and advice. The owner's notes stand above it.
  async function growReport(body, plant) {
    const section = n("section", "ws-report"); body.append(section);
    try {
      const report = await api(`/api/grow-report/${encodeURIComponent(plant.id)}`), l = report.lines || {}, lessons = lessonBlock(plant);
      section.append(n("h3", "", tr("reportTitle")));
      for (const [key, items] of [["reportWell", l.well], ["reportPoor", l.poor], ["reportNext", l.next], ["reportUse", l.use]]) {
        if (!items || !items.length) continue;
        const list = n("ul", "ws-journal");
        items.forEach((text) => { const li = n("li", "", text); if (key === "reportNext") li.append(" ", lessons.keep(text)); list.append(li); });
        section.append(n("h4", "", tr(key)), list);
      }
      if (!["well", "poor", "next"].some((k) => (l[k] || []).length)) section.append(n("p", "ws-muted", tr("reportEmpty")));
      section.append(n("p", "ws-muted", tr("reportHow", { well: report.thresholds.well, poor: report.thresholds.poor })), lessons.node);
      await lessons.load();
    } catch (_) { section.remove(); }  // without a report the record reads as before
  }
  // The owner's lessons (vgrow/lessons.py): a "next time" line or his own words, kept only on his tap. Hank gives them
  // as his own experience, apart from the sourced values.
  function lessonBlock(plant) {
    const node = n("div", "ws-lessons"), keepers = [], words = (text) => text.replace(/\s+/g, " ").trim();
    let rows = [];
    const send = async (body) => { rows = (await api("/api/lessons", body)).lessons; draw(); };
    function draw() {
      const list = n("ol", "ws-journal");
      for (const row of rows) {
        const li = n("li"), actions = n("div", "ws-actions");
        let armed = false;
        actions.append(button(tr("lessonEdit"), () => {
          const box = field(tr("lessonNew"), "textarea", row.text); box.input.maxLength = 1000;
          li.replaceChildren(box.wrap, button(tr("lessonSave"), () => send({ action: "update", id: row.id, text: box.input.value })));
        }), button(tr("lessonDelete"), (b) => {
          if (armed) return send({ action: "delete", id: row.id });
          armed = true; b.textContent = tr("lessonDeleteAgain");
        }));
        li.append(n("p", "", row.text), n("small", "ws-muted", row.run || ""), actions);
        list.append(li);
      }
      const own = field(tr("lessonNew"), "textarea"); own.input.maxLength = 1000;
      node.replaceChildren(n("h4", "", tr("reportLessons")), ...(rows.length ? [list] : []), own.wrap,
        button(tr("lessonKeep"), () => send({ action: "add", plant: plant.id, text: own.input.value })), n("p", "ws-muted", tr("lessonHow")));
      for (const { b, text } of keepers) if (b.isConnected && rows.some((r) => r.plant === plant.id && r.text === words(text))) b.replaceWith(n("small", "ws-muted", tr("lessonKept")));
    }
    return {
      node,
      keep(text) { const b = button(tr("lessonKeep"), () => send({ action: "add", plant: plant.id, text })); keepers.push({ b, text }); return b; },
      async load() { try { rows = (await api("/api/lessons")).lessons; } catch (_) { node.remove(); return; } draw(); },
    };
  }
  function submit(form, label, send) {
    const b = n("button", "btn primary", label); b.type = "submit"; form.append(b);
    form.addEventListener("submit", async (event) => { event.preventDefault(); if (b.disabled) return; b.disabled = true; try { await send(); } catch (err) { alert(form, err); } finally { b.disabled = false; } });
  }
  function boxForm(box, trigger) {
    const dialog = modal(tr(box ? "editBox" : "addBox"), trigger), form = n("form", "ws-form");
    const name = field(tr("boxName"), "text", box?.name), kind = field(tr("boxType"), "text", box?.kind), size = field(tr("capacity"), "number", box?.capacity), notes = field(tr("notes"), "textarea", box?.notes);
    for (const [key, f] of [["name", name], ["kind", kind], ["capacity", size], ["notes", notes]]) f.input.name = key;
    name.input.required = true; name.input.maxLength = 120; kind.input.maxLength = 120; size.input.min = "1"; size.input.max = "1000"; size.input.step = "1"; notes.input.maxLength = 4000;
    form.append(name.wrap, kind.wrap, size.wrap, notes.wrap, n("p", "ws-muted", tr(box?.controller ? "boxConnectedNote" : "boxUnboundNote")));
    submit(form, tr("saveBox"), async () => {
      const result = await write({ action: box ? "box_update" : "box_create", id: box?.id, name: name.input.value, kind: kind.input.value, capacity: size.input.value === "" ? null : Number(size.input.value), notes: notes.input.value });
      if (!box) selectBox(result.created); dialog.close();
    });
    if (box && !box.controller) form.append(button(tr("archiveBox"), async () => { await write({ action: "box_archive", id: box.id, archived: true }); dialog.close(); }));
    dialog.append(form); dialog.showModal(); name.input.focus();
  }
  // The wiki's varieties for the form, loaded once (vgrow/plant_run.py). Free text stays possible: the list only suggests.
  let varietyRows = null;
  async function loadVarieties() {
    if (varietyRows) return varietyRows;
    try { varietyRows = (await api("/api/workspace/varieties")).rows || []; } catch (_) { return []; }
    return varietyRows;
  }
  function varietyText(row) { return `${row.label} · ${row.brand || tr("varietyCatalogue")}`; }
  function spanText(span) {
    const [lo, hi] = span;
    if (lo !== null && hi !== null) return lo === hi ? String(lo) : `${lo}–${hi}`;
    return hi !== null ? tr("spanUpTo", { n: hi }) : tr("spanFrom", { n: lo });
  }
  const FIT = { sure: "fitSure", lst: "fitLst", tall: "fitTall" };
  const HEIGHT_WORD = { short: "heightShort", average: "heightAverage", tall: "heightTall" };
  const DIFFICULTY = { easy: "difficultyEasy", medium: "difficultyMedium", moderate: "difficultyMedium", hard: "difficultyHard", difficult: "difficultyHard" };
  function factsText(row, flowering) {
    const type = flowering || row.flowering, parts = [];
    if (type) parts.push(tr(type === "auto" ? "factAuto" : "factPhoto"));
    if (row.bloom) parts.push(tr("factBloom", { span: spanText(row.bloom) }));
    if (row.harvest) parts.push(tr("factHarvest", { span: spanText(row.harvest) }));
    if (row.height) parts.push(tr("factHeight", { span: spanText(row.height) }));
    if (FIT[row.fit] && row.box_cm) parts.push(tr(FIT[row.fit], { box: row.box_cm }));
    if (HEIGHT_WORD[row.height_word]) parts.push(tr(HEIGHT_WORD[row.height_word]));
    if (DIFFICULTY[row.difficulty]) parts.push(tr(DIFFICULTY[row.difficulty]));
    if (parts.length && row.by) parts.push(tr("factBy", { by: row.by }));
    return parts.join(" · ");
  }
  function addDays(day, count) { const at = new Date(`${day}T12:00:00Z`); at.setUTCDate(at.getUTCDate() + count); return at.toISOString().slice(0, 10); }
  // A maker's figure, counted from the seed or from the first flowers: an orientation. Ripeness is read from the trichomes.
  function harvestWindow(plant) {
    const facts = plant.variety_facts, bloom = plant.linked_profile ? snapshot?.plant?.bloom?.date : null;
    const from = facts?.harvest && plant.started ? [plant.started, facts.harvest] : facts?.bloom && bloom ? [bloom, facts.bloom] : null;
    if (!from || from[1][0] === null || from[1][1] === null) return "";
    return tr("harvestWindow", { from: dateText(addDays(from[0], from[1][0])), to: dateText(addDays(from[0], from[1][1])) });
  }
  function plantForm(plant, trigger) {
    const dialog = modal(tr(plant ? "editPlant" : "addPlant"), trigger), form = n("form", "ws-form");
    const name = field(tr("plantName"), "text", plant?.name), variety = field(tr("variety"), "text", plant?.variety), phase = field(tr("recordedStage"), "text", plant?.phase), observed = field(tr("observedSince"), "date", plant?.phase_since), started = field(tr("startDate"), "date", plant?.started), notes = field(tr("notes"), "textarea", plant?.notes), count = field(tr("quantity"), "number", 1);
    for (const [key, f] of [["name", name], ["variety", variety], ["stageLabel", phase], ["phase_since", observed], ["started", started], ["notes", notes], ["count", count]]) f.input.name = key;
    name.input.required = true; [name, variety, phase].forEach((f) => { f.input.maxLength = 120; }); notes.input.maxLength = 4000; started.input.max = today(); started.input.min = "1900-01-01"; count.input.min = "1"; count.input.max = "50"; count.input.step = "1"; count.input.required = true;
    observed.input.min = plant?.started || "1900-01-01"; observed.input.max = today();
    started.input.onchange = () => { observed.input.min = started.input.value || "1900-01-01"; };
    const stageHint = n("p", "ws-muted");
    phase.input.oninput = () => { const none = !phase.input.value.trim(); observed.input.disabled = none; if (none) observed.input.value = ""; stageHint.textContent = tr(none ? "observedNeedsStage" : "observedHint"); };  // the server refuses a stage date without a stage
    phase.input.oninput();
    const approximate = n("label", "ws-check"), check = n("input"); check.type = "checkbox"; check.checked = !!plant?.approximate; approximate.append(check, tr("approximate"));
    const target = n("label", "ws-field", tr("box")), select = n("select"); select.name = "box";
    const origin = n("label", "ws-field", tr("origin")), source = n("select"); source.name = "origin";
    for (const [value, key] of [["", "originUnknown"], ["seed", "originSeed"], ["cutting", "originCutting"], ["existing", "originExisting"], ["other", "originOther"]]) { const option = n("option", "", tr(key)); option.value = value; source.append(option); }
    source.value = plant?.origin || ""; origin.append(source);
    for (const b of data.boxes.filter((b) => !b.archived)) { const option = n("option", "", b.name); option.value = b.id; select.append(option); }
    select.value = plant?.box || selected.box; select.disabled = !!plant?.linked_profile; target.append(select);
    // The pick from the wiki: it fills the flowering type and shows what it knows; the owner's choice of type always wins.
    let ref = plant?.variety_ref || null;
    const list = n("datalist"), facts = n("p", "ws-muted ws-variety-facts"), flowering = n("label", "ws-field", tr("flowering")), floweringSelect = n("select");
    list.id = "ws-variety-list"; variety.input.setAttribute("list", list.id); floweringSelect.name = "flowering";
    for (const [value, key] of [["", "floweringUnknown"], ["auto", "floweringAuto"], ["photoperiod", "floweringPhoto"]]) { const option = n("option", "", tr(key)); option.value = value; floweringSelect.append(option); }
    floweringSelect.value = plant?.flowering || ""; flowering.append(floweringSelect);
    const shown = () => { const row = (varietyRows || []).find((r) => r.source === ref?.source && r.key === ref?.key); facts.textContent = row ? factsText(row, floweringSelect.value) : ""; facts.hidden = !row; };
    variety.input.oninput = () => {
      const typed = variety.input.value.trim(), row = (varietyRows || []).find((r) => varietyText(r) === typed);
      if (row) { ref = { source: row.source, key: row.key }; if (row.flowering) floweringSelect.value = row.flowering; if (!name.input.value.trim()) name.input.value = row.label; }
      else if (typed !== (plant?.variety || "")) ref = null;
      shown();
    };
    floweringSelect.onchange = shown;
    loadVarieties().then((rows) => { list.replaceChildren(...rows.map((r) => { const option = n("option"); option.value = varietyText(r); return option; })); shown(); });
    // The owner's own choice that the cockpit runs on this plant (vgrow/plant_run.py); a plant it already runs on stays.
    const runs = !!plant?.linked_profile, stages = snapshot?.plant?.stages || [];
    const control = n("label", "ws-check"), controlBox = n("input"), controlHint = n("p", "ws-muted", tr("controlHint")), stagePick = n("label", "ws-field", tr("controlStage")), stageSelect = n("select"), recordNote = n("p", "ws-muted", tr("recordOnly"));
    controlBox.type = "checkbox"; control.append(controlBox, tr("controlPlant")); stageSelect.name = "controlStage";
    for (const [value, label] of [["", tr("controlStagePick")], ...stages.map((s) => [s.key, s.label])]) { const option = n("option", "", label); option.value = value; stageSelect.append(option); }
    stagePick.append(stageSelect);
    controlBox.checked = !plant && select.value === data.native_box && !plants(data.native_box).some((p) => p.linked_profile);
    const syncControl = () => {
      const possible = !runs && select.value === data.native_box && (plant || Number(count.input.value) === 1);
      if (!possible) controlBox.checked = false;
      const on = controlBox.checked; control.hidden = !possible; controlHint.hidden = !on; stagePick.hidden = !on; stageSelect.required = on;
      for (const node of [phase.wrap, observed.wrap, stageHint, recordNote]) node.hidden = on;
    };
    controlBox.onchange = syncControl; count.input.addEventListener("input", syncControl); select.addEventListener("change", syncControl);
    form.append(name.wrap, variety.wrap, list, n("p", "ws-muted", tr("varietyHint")), facts, flowering, target, origin, control, controlHint, stagePick, phase.wrap, observed.wrap, stageHint, recordNote, started.wrap, approximate);
    if (!plant) form.append(count.wrap, n("p", "ws-muted", tr("quantityHint")));
    form.append(notes.wrap);
    syncControl();
    submit(form, tr(plant ? "savePlant" : "createPlants"), async () => {
      const choose = controlBox.checked, stageKey = choose ? stageSelect.value : "";
      const stageLabel = stages.find((s) => s.key === stageKey)?.label || "";
      const result = await write({ action: plant ? "plant_update" : "plant_create", id: plant?.id, box: select.value, name: name.input.value, variety: variety.input.value, variety_ref: ref, flowering: floweringSelect.value || null, origin: source.value, phase: choose ? stageLabel : phase.input.value, phase_since: choose ? null : observed.input.value || null, started: started.input.value || null, approximate: check.checked, notes: notes.input.value, count: Number(count.input.value) });
      const id = plant?.id || result.created;
      selected.box = select.value; selectPlant(id);
      if (choose || runs) { await api("/api/cmd", { action: "run_activate", plant: id, stage: stageKey || null }); await refresh(); poll(true); }
      dialog.close();
    });
    if (plant && !plant.linked_profile) form.append(button(tr("archivePlant"), async () => { await write({ action: "plant_archive", id: plant.id, archived: true }); dialog.close(); }));
    dialog.append(form); dialog.showModal(); name.input.focus();
  }
  function archived(trigger) {
    const dialog = modal(tr("archive"), trigger), list = n("div", "ws-archive-list");
    const draw = () => {
      list.replaceChildren();
      for (const b of data.boxes.filter((b) => b.archived)) { const row = n("div", "ws-archive-row"); row.append(n("span", "", `${b.name} · ${plantCount(b.plant_count)}`), button(tr("restore"), async () => { await write({ action: "box_archive", id: b.id, archived: false }); draw(); })); list.append(row); }
      for (const p of data.plants.filter((p) => p.archived || p.ended)) {
        const row = n("div", "ws-archive-row"), box = data.boxes.find((b) => b.id === p.box); row.append(n("span", "", `${p.name} · ${box?.name || ""}`));
        row.append(button(tr("viewHistory"), () => { dialog.close(); return recordHistory(p, trigger); }));
        if (p.ended) row.append(n("small", "ws-muted", `${tr("completed")} · ${dateText(p.ended)}`));
        else if (box?.archived) row.append(n("small", "ws-muted", tr("restoreBoxFirst")));
        else row.append(button(tr("restore"), async () => { await write({ action: "plant_archive", id: p.id, archived: false }); draw(); })); list.append(row);
      }
      if (!list.children.length) list.append(n("p", "ws-muted", tr("archiveEmpty")));
    };
    dialog.append(n("p", "ws-muted", tr("archiveHint")), list); draw(); dialog.showModal();
  }
  function plantTile(p) {
    const b = button("", () => selectPlant(p.id), "ws-plant-tab"); b.dataset.plant = p.id; b.setAttribute("aria-pressed", String(currentPlant()?.id === p.id));
    const icon = n("span", "ws-plant-icon", "🌱"); icon.setAttribute("aria-hidden", "true");
    const words = n("span", "ws-plant-label"); words.append(n("strong", "", p.name), n("small", "", p.phase || tr("noPhase"))); b.replaceChildren(icon, words); return b;
  }
  function renderTop() {
    top.replaceChildren(); const row = n("div", "ws-box-row"), tabs = n("nav", "ws-box-tabs"); tabs.setAttribute("aria-label", tr("boxes"));
    for (const box of data.boxes.filter((b) => !b.archived)) { const b = button("", () => selectBox(box.id), "ws-box-tab"); b.dataset.box = box.id; b.setAttribute("aria-pressed", String(selected.box === box.id)); b.append(n("span", "", box.name), n("small", "", box.plant_count)); tabs.append(b); }
    const actions = n("div", "ws-actions"); actions.append(button(tr("startHere"), startGuide, "primary ws-start"), button(tr("addBox"), (b) => boxForm(null, b), "ws-add-box"), button(tr("archive"), archived, "ws-archive")); row.append(tabs, actions); top.append(row);
  }
  function heightValue(row) { return `${row.estimated ? "≈ " : ""}${new Intl.NumberFormat(LOCALE[LANG]).format(row.cm)} cm`; }
  function heightNote(row) { return tr(row.method === "camera_strip" ? "cameraHeightRead" : row.camera === "growcam" ? "cameraHeightEstimate" : row.estimated ? "approximateHeight" : "manualHeight"); }
  // The camera reads this plant off the strip (vgrow/ruler.py), so no form stands in the way (owner, 06.11.2026: "just clatter").
  // It comes back when the camera has stopped reading for more than two days, the point Hank asks for a height, and stays for
  // every plant the camera does not see. A wrong reading is corrected under its picture (web/plan.js).
  function cameraMeasures(plant) { return linkedPlant() && plant.heights.some((row) => row.method === "camera_strip" && daysSince(row.day) <= HEIGHT_DUE_DAYS); }
  function measurements(parent, plant) {
    const section = n("section", "ws-panel"); section.append(n("h3", "", tr("measurements")));
    if (cameraMeasures(plant)) {
      const note = n("p", "ws-muted", `${tr("heightByCamera")} `); note.append(link(tr("heightCorrect"), "#automation")); section.append(note);
    } else {
      const form = n("form", "ws-inline-form"), stamp = field(tr("date"), "date", today()), cm = field(tr("height"), "number");
      stamp.input.name = "day"; cm.input.name = "cm";
      stamp.input.required = true; stamp.input.min = plant.started || "1900-01-01"; stamp.input.max = today(); cm.input.required = true; cm.input.min = "0.01"; cm.input.max = "10000"; cm.input.step = "0.01";
      const estimate = n("label", "tank-button-option"), estimated = n("input"); estimated.type = "checkbox";
      estimate.append(estimated, t("plant.heightGrowCamOption")); estimate.hidden = !nativeBox();
      form.append(stamp.wrap, cm.wrap, estimate); submit(form, tr("saveMeasurement"), async () => { await write({ action: "height", plant: plant.id, day: stamp.input.value, cm: Number(cm.input.value), estimated: estimated.checked, ...(estimated.checked ? { camera: "growcam" } : {}) }); }); section.append(form, n("p", "ws-muted", tr("heightHint")));
      if (nativeBox()) section.append(n("p", "ws-muted", t("plant.heightCameraHint")));
    }
    if (plant.heights.length) {
      const table = n("table", "ws-table"), caption = n("caption", "ws-muted", tr("recentMeasurements")), head = n("tr"); head.append(n("th", "", tr("date")), n("th", "", tr("height"))); const thead = n("thead"); thead.append(head); const body = n("tbody");
      const sourceLabel = tr("heightSource");
      for (const row of plant.heights.slice(0, 12)) { const tr = n("tr"), value = n("td", "ws-height-cell", heightValue(row)); value.append(n("small", "", `${sourceLabel}: ${heightNote(row)}`)); tr.append(n("td", "", dateText(row.day)), value); body.append(tr); }
      table.append(caption, thead, body); section.append(table);
    } else section.append(n("p", "ws-muted", tr("noMeasurements")));
    parent.append(section);
  }
  function journal(parent, plant) {
    const section = n("section", "ws-panel"); section.append(n("h3", "", tr("journal")));
    const form = n("form", "ws-form"), stamp = field(tr("date"), "date", today()), entry = field(tr("journalText"), "textarea");
    stamp.input.name = "day"; entry.input.name = "text";
    stamp.input.required = true; stamp.input.max = today(); stamp.input.min = plant.started || "1900-01-01"; entry.input.required = true; entry.input.maxLength = 4000;
    form.append(stamp.wrap, entry.wrap); submit(form, tr("addNote"), async () => { await write({ action: "note", plant: plant.id, day: stamp.input.value, text: entry.input.value }); }); section.append(form);
    const entries = n("ol", "ws-journal");
    for (const item of plant.journal.slice(0, 30)) { const li = n("li"); li.append(n("time", "ws-muted", dateText(item.day)), n("p", "", item.text)); entries.append(li); }
    if (!entries.children.length) section.append(n("p", "ws-muted", tr("journalEmpty"))); else section.append(entries);
    parent.append(section);
  }
  function renderGrow() {
    grow.replaceChildren(); const box = currentBox(); if (!box) return;
    const boxHead = n("header", "ws-box-head"), title = n("div"); title.append(n("span", "eyebrow", tr("box")), n("h2", "", box.name), n("p", "ws-muted", [box.kind, plantCount(box.plant_count), box.capacity ? tr("capacityCount", { n: box.capacity }) : ""].filter(Boolean).join(" · ")));
    boxHead.append(title, button(tr("editBox"), (b) => boxForm(box, b))); grow.append(boxHead);
    const state = n("p", `ws-connection ${nativeBox() ? "is-connected" : ""}`, tr(nativeBox() ? "connectedBox" : "notConnected")); grow.append(state);
    const plantsHead = n("div", "ws-section-head"); plantsHead.append(n("h3", "", tr("plants")), button(tr("addPlant"), (b) => plantForm(null, b), "primary ws-add-plant")); grow.append(plantsHead);
    const tabs = n("nav", "ws-plant-tabs"); tabs.setAttribute("aria-label", tr("plantRecords")); plants().forEach((p) => tabs.append(plantTile(p))); grow.append(tabs);
    if (!plants().length) { const empty = n("div", "ws-empty"); empty.append(n("h3", "", tr("emptyBox")), n("p", "ws-muted", tr("emptyBoxHint")), button(tr("addPlant"), (b) => plantForm(null, b), "primary")); grow.append(empty); return; }
    const plant = currentPlant(), head = n("div", "ws-section-head"), words = n("div"); words.append(n("span", "eyebrow", tr("plantRecord")), n("h2", "", plant.name), n("p", "ws-muted", plant.variety));
    const actions = n("div", "ws-actions"); actions.append(button(tr("editPlant"), (b) => plantForm(plant, b)), button(tr("viewHistory"), (b) => recordHistory(plant, b)), button(tr("harvest"), (b) => harvestForm(plant, b), "primary ws-harvest"), button(tr("finishRecord"), (b) => finishForm(plant, b), "ws-finish")); head.append(words, actions); grow.append(head);
    const nav = n("nav", "ws-record-nav"); nav.setAttribute("aria-label", tr("recordSections"));
    for (const [key, path] of [["record", "growth"], ["journal", "journal"], ...(linkedPlant() ? [["existingProfile", "insights"]] : [])]) { const a = link(tr(key), `#grow/${path}`); if (location.hash.endsWith(`/${path}`)) a.setAttribute("aria-current", "page"); nav.append(a); } grow.append(nav);
    const legacy = linkedPlant() && /#grow\/(feeding|insights)/.test(location.hash);
    if (legacy) { grow.append(n("p", "ws-profile-note", tr("existingProfileNote", { name: snapshot?.plant?.plant?.strain || plant.variety }))); return; }
    if (/#grow\/(feeding|insights)/.test(location.hash)) { grow.append(n("p", "ws-profile-note", tr("profileUnavailable"))); return; }
    if (location.hash === "#grow/journal") { journal(grow, plant); return; }
    const metrics = n("div", "ws-metrics"), height = plant.heights[0];
    const stageAge = plant.phase_since ? tr("observedAge", { date: dateText(plant.phase_since), days: plant.phase_age_days }) : tr("observedUnknown");
    const recordDate = plant.phase_recorded ? tr("entryDate", { date: dateText(plant.phase_recorded) }) : "";
    metrics.append(metric(tr("age"), plant.age_days === null ? tr("notRecorded") : tr("days", { n: plant.age_days }), `${dateText(plant.started)}${plant.approximate ? ` · ${tr("approximateShort")}` : ""}`), metric(tr("recordedPhase"), plant.phase || tr("noPhase"), [stageAge, recordDate].filter(Boolean).join(" · ")), metric(tr("height"), height ? heightValue(height) : tr("notRecorded"), height ? `${dateText(height.day)} · ${heightNote(height)}` : "")); grow.append(metrics);
    cameraStage(grow);
    if (plant.notes) grow.append(n("p", "ws-record-notes", plant.notes));
    if (plant.imported) grow.append(n("p", "ws-profile-note", tr("importedNote")));
    if (plant.variety_facts) { const line = [factsText(plant.variety_facts, plant.flowering), harvestWindow(plant)].filter(Boolean).join(" · "); if (line) grow.append(n("p", "ws-muted ws-variety-facts", line)); }
    grow.append(n("p", "ws-muted", tr("sharedBoxNote")));
    measurements(grow, plant);
  }
  // The camera's stage once three daily photos in a row show it, never backwards (vgrow/vision.py stage_suggestion).
  // It belongs to the box's controller profile, so only beside the linked plant; the confirm button sits under Einblicke.
  function cameraStage(grow) {
    const sug = linkedPlant() && snapshot?.vision?.stage_suggest;
    if (!sug) return;
    const note = n("p", "ws-profile-note", `${tr("cameraStage", { stage: sug.label, days: sug.days })} `);
    note.append(link(tr("cameraStageLink"), "#grow/insights"));
    grow.append(note);
  }
  // A plant on the overview: Hank's picture of where it stands, its name, a status pill and one line more.
  function lifeTile(p) {
    const b = button("", () => { if (p.post_stage) return stagesDialog(p, b); selectPlant(p.id); location.hash = "#grow/growth"; }, "ws-plant-tab ws-life-tile"); b.dataset.plant = p.id;
    const words = n("span", "ws-plant-label"), pill = n("span", "ws-pill", p.post_stage ? `${tr(STAGE[p.post_stage])} · ${tr("stageDay", { n: p.post_days + 1 })}` : [tr("statusGrowing"), p.phase].filter(Boolean).join(" · "));
    pill.dataset.stage = p.post_stage || "growing";
    const box = data.boxes.find((x) => x.id === p.box), more = p.post_stage ? [tr("harvestedOn", { date: dateText(p.harvested) }), box?.name] : [p.age_days === null ? "" : tr("days", { n: p.age_days })];
    words.append(n("strong", "", p.name), pill, n("small", "", more.filter(Boolean).join(" · ")));
    b.append(hankStage("ws-life-stage", p.post_stage ? MOMENT[p.post_stage][0] : null, false), words); return b;
  }
  // Between two grows (vgrow/harvest.py): the empty box gets a short list until the next plant moves in. Each step that is
  // a claim names its source; Hank cleans once per page load, then the card keeps his still frame.
  const PREP = [["debris", "prepDebris", "https://ipm-cahnr.media.uconn.edu/wp-content/uploads/sites/3216/2024/05/greenhousesanitation.pdf"],
    ["clean", "prepClean", "https://vegcropshotline.org/article/cleaning-and-disinfesting-your-greenhouse-before-planting/"],
    ["water", "prepWater", ""], ["cameras", "prepCameras", ""],
    ["inspect", "prepInspect", "https://ipm.ucanr.edu/home-and-landscape/aphids/"]];
  const prepPlayed = new Set();
  function prepCard(box) {
    const card = n("div", "ws-prep"), done = new Set(box.prep || []), ready = PREP.every(([item]) => done.has(item)), words = n("div", "ws-prep-words");
    words.append(n("h4", "", tr(ready ? "prepReady" : "prepTitle")), n("p", "ws-muted", tr("prepHint")));
    const list = n("ul", "ws-prep-list");
    for (const [item, key, source] of PREP) {
      const li = n("li"), label = n("label", "ws-check"), check = n("input"); check.type = "checkbox"; check.checked = done.has(item);
      check.onchange = async () => { check.disabled = true; try { await write({ action: "box_prep", box: box.id, item, done: check.checked }); } catch (err) { check.checked = !check.checked; alert(card, err); } finally { check.disabled = false; } };
      label.append(check, n("span", "", tr(key))); li.append(label);
      if (source) { const a = link(tr("prepSource"), source); a.target = "_blank"; a.rel = "noopener"; li.append(a); }
      list.append(li);
    }
    const actions = n("div", "ws-actions"); actions.append(button(tr("addPlant"), (b) => plantForm(null, b), ready ? "primary" : ""));
    words.append(list, actions);
    card.append(hankStage("ws-prep-stage", "clean", !prepPlayed.has(box.id)), words); prepPlayed.add(box.id);
    return card;
  }
  function renderSummary() {
    summary.replaceChildren(); const box = currentBox(); if (!box) return;
    const head = n("div", "ws-section-head"); head.append(n("h3", "", tr("boxPlants")), link(tr("managePlants"), "#grow/growth")); summary.append(head);
    const list = n("div", "ws-summary-plants"); plants().forEach((p) => list.append(lifeTile(p))); summary.append(list);
    if (!plants().length) summary.append(prepCard(box));
    const later = afterHarvest();  // out of every box, so they show whichever box is selected
    if (later.length) { const rest = n("div", "ws-summary-plants"); later.forEach((p) => rest.append(lifeTile(p))); summary.append(n("h4", "ws-life-head", tr("afterHarvest")), rest); }
  }
  function context() {
    if (!data) return;
    const box = currentBox(), plant = currentPlant(); if (!box) return;
    const connected = nativeBox(), primary = linkedPlant(), legacy = primary && /#grow\/(feeding|insights)/.test(location.hash);
    const wasUnbound = document.body.classList.contains("ws-unbound");
    document.body.classList.toggle("ws-unbound", !connected); document.body.classList.toggle("ws-other-plant", !primary); document.body.classList.toggle("ws-legacy-view", legacy);
    if (wasUnbound && connected && snapshot) window.render(snapshot);
    for (const page of document.querySelectorAll("[data-page]")) {
      const gated = !connected && ["overview", "automation", "devices", "analytics", "activity"].includes(page.dataset.page);
      page.classList.toggle("ws-blocked-view", gated);
      let empty = page.querySelector(":scope > .ws-context-empty");
      if (gated && !empty) { empty = n("section", "card ws-context-empty"); page.append(empty); }
      if (empty) {
        empty.hidden = !gated;
        if (gated && empty.dataset.box !== box.id) { empty.dataset.box = box.id; empty.replaceChildren(n("span", "eyebrow", box.name), n("h2", "", tr("notConnected")), n("p", "ws-muted", tr("noSharedData")), link(tr("managePlants"), "#grow/growth")); }
      }
    }
    byId("top-device").textContent = box.name; byId("side-foot").textContent = box.name;
    if (!connected) {
      for (const [pill, label] of [["status-pill", "status-text"], ["side-pill", "side-status"]]) { byId(label).textContent = tr("notConnected"); byId(pill).className = "pill warn"; }
      byId("age").textContent = tr("inventoryOnly"); byId("notification-count").hidden = true;
    }
    if (plant) { byId("overview-plant").textContent = plant.name; byId("overview-context").textContent = [box.name, plant.phase, plant.age_days === null ? "" : tr("days", { n: plant.age_days })].filter(Boolean).join(" · "); }
    else { byId("overview-plant").textContent = box.name; byId("overview-context").textContent = tr("emptyBoxHint"); }
    document.querySelectorAll("[data-workspace-scope]").forEach((node) => { node.textContent = tr("boxScope", { name: box.name }); });
    const key = `${box.id}/${plant?.id || ""}`;
    if (contextKey !== key) { contextKey = key; document.dispatchEvent(new Event("workspace:context")); }
  }
  function render() {
    if (!data) return;
    document.body.classList.remove("workspace-loading"); document.body.classList.add("workspace-ready");
    const sig = JSON.stringify([data.revision, selected, LANG, location.hash]);
    if (sig !== signature) { signature = sig; renderTop(); renderGrow(); renderSummary(); }
    context();
  }
  window.PlantWorkspace = { headers: () => data ? { "X-Workspace-Box": selected.box, ...(currentPlant() ? { "X-Workspace-Plant": currentPlant().id } : {}) } : {},
    error, context, assistantAllowed: () => !data || linkedPlant(), refresh };
  document.addEventListener("vgrow:state", (event) => { snapshot = event.detail; if (data && loadedDay !== today() && !busy) refresh(); else render(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && data && loadedDay !== today() && !busy) refresh(); });
  document.addEventListener("vgrow:offline", context);
  window.addEventListener("hashchange", () => { document.querySelectorAll(".ws-dialog").forEach((dialog) => dialog.close()); render(); });
  new MutationObserver(() => { signature = ""; refresh(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  window.addEventListener("storage", (event) => { if (event.key === "vgrow.workspace") { try { const s = JSON.parse(event.newValue); if (s?.box && s.plants) { selected = s; if (data) { normalize(); signature = ""; render(); } } } catch (_) { /* invalid storage */ } } });
  refresh();
})();
