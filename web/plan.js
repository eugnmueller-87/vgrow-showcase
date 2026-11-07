// The grow plan's card on the automation page (vgrow/grow_plan.py): one plant, what it needs now, who does it, and the few
// steps only the owner's hands can do. Every text comes from the server in the page's language; this file only draws it.
// Loaded after app.js and uses its globals $, make(), api(), poll(), when(), setFb(). It draws from the app's /api/state
// answer (the event "vgrow:state"). The buttons are the owner's taps: the pump only runs on the jug test and on one litre.
(function () {
  "use strict";

  const ARM_MS = 4000;  // a two-tap button waits this long for the second tap
  let lastSig = null;
  let flowAsked = null;  // the entry the server asked to confirm (an unusual flow)
  const opened = new Set();  // the folds the owner opened: the card is drawn anew often, a fold must not close under a finger

  function fb(text, cls) { setFb("gp-fb", text, cls || ""); }

  async function send(body, done) {
    try {
      await api("/api/cmd", body);
      fb("", "");
      if (done) done();
      poll(true);
    } catch (err) {
      fb(err.message, "bad");
    }
  }

  function button(label, onTap, cls) {
    const b = make("button", cls || "btn sm", label);
    b.type = "button";
    b.addEventListener("click", onTap);
    return b;
  }

  // Two taps: the first one arms and says what the second does.
  function armed(label, again, onTap, cls) {
    let until = 0;
    const b = button(label, () => {
      if (Date.now() >= until) {
        until = Date.now() + ARM_MS;
        b.textContent = again;
        setTimeout(() => { b.textContent = label; }, ARM_MS + 100);
        return;
      }
      until = 0;
      b.textContent = label;
      onTap();
    }, cls);
    return b;
  }

  function fold(name, title) {
    const box = make("details", "plan-log");
    box.open = opened.has(name);
    box.addEventListener("toggle", () => { if (box.open) opened.add(name); else opened.delete(name); });
    box.append(make("summary", "", title));
    return box;
  }

  function tile(data) {
    const box = make("div", "tile");
    const k = make("span", "k");
    k.append(make("span", "", data.label));
    const val = make("span", "val");
    val.append(make("span", "v", data.value), make("span", "u", data.unit || ""));
    const meta = make("span", "meta");
    meta.append(make("span", `sub${data.state ? ` plan-tile-${data.state}` : ""}`, data.sub || ""));
    box.append(k, val, meta);
    return box;
  }

  function pumpControls(item) {
    const row = make("div", "plan-controls");
    const run = button(item.run_label, () => send({ action: "grow_plan_pump_test" }), "btn");
    run.disabled = item.running;
    const ml = make("input", "plan-ml");
    ml.type = "number";
    ml.min = "5";
    ml.max = "5000";
    ml.inputMode = "numeric";
    ml.placeholder = item.ml_label;
    ml.setAttribute("aria-label", item.ml_label);
    const save = button(item.save_label, async () => {
      const value = Number(ml.value);
      if (!value) return;
      const body = { action: "thirst_flow", seconds: item.seconds, ml: value };
      if (flowAsked === value) body.confirm = true;
      try {
        await api("/api/cmd", body);
        flowAsked = null;
        ml.value = "";
        fb("", "");
        poll(true);
      } catch (err) {
        flowAsked = value;  // an unusual flow: the same number once more saves it
        fb(err.message, "bad");
      }
    });
    row.append(run, make("span", "plan-runs", item.runs), ml, save);
    return row;
  }

  function soakControls(item, labels) {
    const row = make("div", "plan-controls");
    const water = button(item.water_label, () => send({ action: "grow_plan_water_now" }), "btn");
    water.disabled = !item.can_water;
    const set = armed(item.set_label, labels["todo.soak_again"], () => send({ action: "soil_calibrate", point: "wet" }));
    set.disabled = !item.can_set;
    row.append(water, set);
    return row;
  }

  function todoItem(item, index, isNext, labels) {
    const li = make("li", `plan-step${item.done ? " done" : ""}${isNext ? " next" : ""}`);
    li.append(make("span", "plan-step-n", item.done ? "✓" : String(index + 1)));
    const body = make("div", "plan-step-body");
    const title = make("b", "", item.title);
    body.append(title);
    if (item.done && item.done_text) body.append(make("span", "plan-step-done", ` ${item.done_text}`));
    if (isNext) {
      body.append(make("p", "plan-step-text", item.text));
      if (item.kind === "pump") body.append(pumpControls(item));
      else if (item.kind === "soak") body.append(soakControls(item, labels));
      else {
        const row = make("div", "plan-controls");
        for (const b of item.buttons || []) {
          const tap = button(b.label, () => send({ action: b.action }), "btn");
          tap.disabled = item.enabled === false;
          row.append(tap);
        }
        body.append(row);
      }
    }
    li.append(body);
    return li;
  }

  function mark(item, labels) {
    const li = make("li", `plan-mark ${item.state}`);
    const head = make("div", "plan-mark-head");
    const pillKind = { done: "ok", now: "warn", next: "info" }[item.state];
    head.append(make("b", "", item.title), make("span", "plan-mark-span", item.span),
      make("span", `pill sm${pillKind ? ` ${pillKind}` : ""}`, item.state_text));
    li.append(head);
    if (item.state === "now" || item.state === "next") li.append(make("p", "plan-step-text", item.text));
    if (item.buttons && item.buttons.length) {
      const row = make("div", "plan-controls");
      for (const b of item.buttons) {
        row.append(b.armed
          ? armed(b.label, labels["mark.flowering_again"], () => send({ action: b.action, key: b.key }), "btn")
          : button(b.label, () => send({ action: b.action, id: b.id }), "btn sm"));
      }
      li.append(row);
    }
    return li;
  }

  function photo(p, labels) {
    const wrap = make("figure", "plan-photo");
    const frame = make("div", "plan-photo-frame");
    const img = make("img");
    img.src = p.url;
    img.alt = p.alt;
    img.loading = "lazy";
    const line = make("div", "plan-photo-line");
    line.style.top = `${p.top_y * 100}%`;
    const tag = make("span", "plan-photo-tag", p.label);
    line.append(tag);
    frame.append(img, line);
    wrap.append(frame, make("figcaption", "hint", labels.photo_note), heightFix(labels));
    return wrap;
  }

  // Seldom: the camera reads the height by itself. Only when the line is off does the owner's number count.
  function heightFix(labels) {
    const box = fold("height", labels["height.fix"]);
    const row = make("div", "plan-controls");
    const cm = make("input", "plan-ml");
    cm.type = "number";
    cm.min = "1";
    cm.max = "300";
    cm.step = "0.5";
    cm.inputMode = "decimal";
    cm.placeholder = labels["height.fix_cm"];
    cm.setAttribute("aria-label", labels["height.fix_cm"]);
    const save = button(labels["height.fix_save"], () => {
      if (Number(cm.value)) send({ action: "grow_plan_height", cm: Number(cm.value) }, () => { cm.value = ""; opened.delete("height"); });
    });
    row.append(cm, save);
    box.append(row);
    return box;
  }

  function table(view) {
    const wrap = make("div", "plan-table-wrap");
    const tbl = make("table", "plan-table");
    const head = make("tr");
    for (const h of view.row_heads) head.append(make("th", "", h));
    const thead = make("thead");
    thead.append(head);
    const body = make("tbody");
    for (const r of view.rows) {
      const row = make("tr");
      const what = make("td", "", r.what);
      const who = make("td", "", r.who);
      const now = make("td", r.state ? `plan-now ${r.state}` : "plan-now", r.now);
      const target = make("td", r.target === "–" ? "plan-none" : "", r.target);  // nothing to say: no line on a phone
      for (const [cell, name] of [[who, view.row_heads[1]], [now, view.row_heads[2]], [target, view.row_heads[3]]]) cell.dataset.label = name;
      if (r.hint) who.append(make("small", "plan-hint", r.hint));
      row.append(what, who, now, target);
      body.append(row);
    }
    tbl.append(thead, body);
    wrap.append(tbl);
    return wrap;
  }

  function settings(view, labels) {
    const box = fold("settings", labels["settings.title"]);
    const row = make("div", "plan-controls");
    const goal = make("input", "plan-ml");
    goal.type = "number";
    goal.min = "25";
    goal.max = "100";
    goal.step = "5";
    goal.value = view.settings.light_goal ?? "";
    goal.setAttribute("aria-label", labels["settings.goal"]);
    goal.placeholder = labels["settings.goal"];
    const feed = make("input", "plan-ml");
    feed.type = "date";
    feed.value = view.settings.feed_from || "";
    feed.setAttribute("aria-label", labels["settings.feed"]);
    const save = button(labels["settings.save"], () => send({ action: "grow_plan_settings",
      light_goal: goal.value === "" ? null : Number(goal.value), feed_from: feed.value || null }));
    const goalField = make("label", "plan-field", labels["settings.goal"]);
    goalField.append(goal);
    const feedField = make("label", "plan-field", labels["settings.feed"]);
    feedField.append(feed);
    row.append(goalField, feedField, save);
    box.append(row);
    return box;
  }

  function draw(view) {
    const card = $("grow-plan");
    const labels = view.labels;
    const head = make("div", "card-h plan-head");
    const names = make("div");
    names.append(make("span", "ap-kicker", view.heading), make("h3", "hero", view.title), make("p", "plan-sub", view.sub));
    head.append(names);
    const nodes = [head];
    if (view.warning) nodes.push(make("div", "banner warn", view.warning));
    const tiles = make("section", "tiles plan-tiles");
    tiles.append(...view.tiles.map(tile));
    nodes.push(tiles);

    if (view.light && view.light.text) {
      const line = make("div", "plan-light");
      line.append(make("p", "plan-light-text", view.light.text));
      if (view.light.start) line.append(button(labels["btn.start_light"], () => send({ action: "light_schedule_auto", fingerprint: view.light.start }), "btn"));
      if (view.light.resume) line.append(button(labels["btn.resume"], () => send({ action: "grow_plan_light_resume" })));
      nodes.push(line);
    }

    const open = view.todo.filter((i) => !i.done);
    const steps = make("section", "plan-section");
    steps.append(make("h4", "plan-h", view.todo_title));
    if (!open.length) steps.append(make("p", "hint", view.todo_none));
    const list = make("ol", "plan-steps");
    view.todo.forEach((item, i) => list.append(todoItem(item, i, open[0] === item, labels)));
    steps.append(list);
    nodes.push(steps);

    const grid = make("div", "plan-grid");
    const marks = make("section", "plan-section");
    marks.append(make("h4", "plan-h", view.marks_title));
    const markList = make("ul", "plan-marks");
    markList.append(...view.marks.map((m) => mark(m, labels)));
    marks.append(markList, button(labels["btn.measure"], () => send({ action: "grow_plan_measure" })));
    grid.append(marks);
    if (view.photo) grid.append(photo(view.photo, labels));
    nodes.push(grid, table(view));

    nodes.push(settings(view, labels));

    if (view.log.length) {
      const log = fold("log", view.log_title);
      const lines = make("ul", "water-events");
      for (const e of view.log) {
        const li = make("li");
        li.append(make("span", "water-event-at", when(new Date(e.at * 1000))), make("span", "", e.text));
        lines.append(li);
      }
      log.append(lines);
      nodes.push(log);
    }
    const message = make("p", "fb");
    message.id = "gp-fb";
    message.setAttribute("role", "status");
    nodes.push(message);
    card.replaceChildren(...nodes);
  }

  function render(s) {
    const card = $("grow-plan");
    const view = s.grow_plan;
    card.hidden = !view || !view.configured;
    if (card.hidden) return;
    // The card holds a number field and two-tap buttons: drawn anew only when something changed, never under a finger.
    const sig = JSON.stringify(view);
    if (sig === lastSig || card.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
    lastSig = sig;
    const message = $("gp-fb");
    const said = message ? [message.textContent, message.className] : null;
    draw(view);
    if (said && said[0]) { const again = $("gp-fb"); again.textContent = said[0]; again.className = said[1]; }
  }

  document.addEventListener("vgrow:state", (e) => render(e.detail));
})();
