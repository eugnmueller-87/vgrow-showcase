/* Encyclopedia tools. Local reading state is shared by this cockpit; no device commands. */
(() => {
  "use strict";
  const TEXT = {
  "tools": "wiki.tools.tools",
  "browse": "wiki.tools.browse",
  "compare": "wiki.tools.compare",
  "atlas": "wiki.tools.atlas",
  "library": "wiki.tools.library",
  "editorial": "wiki.tools.editorial",
  "history": "wiki.tools.history",
  "compareIntro": "wiki.tools.compareIntro",
  "atlasIntro": "wiki.tools.atlasIntro",
  "libraryIntro": "wiki.tools.libraryIntro",
  "editorialIntro": "wiki.tools.editorialIntro",
  "historyIntro": "wiki.tools.historyIntro",
  "save": "wiki.tools.save",
  "saved": "wiki.tools.saved",
  "remove": "wiki.tools.remove",
  "field": "wiki.tools.field",
  "classification": "wiki.tools.classification",
  "thc_percent": "wiki.tools.thc_percent",
  "parents": "wiki.tools.parents",
  "aliases": "wiki.tools.aliases",
  "terpenes": "wiki.tools.terpenes",
  "descendants": "wiki.tools.descendants",
  "retrieved": "wiki.tools.retrieved",
  "availability": "wiki.tools.availability",
  "indica": "wiki.tools.indica",
  "sativa": "wiki.tools.sativa",
  "hybrid": "wiki.tools.hybrid",
  "unknown": "wiki.tools.unknown",
  "unverified": "wiki.tools.unverified",
  "sourcesDiffer": "wiki.tools.sourcesDiffer",
  "factSource": "wiki.tools.factSource",
  "reported": "wiki.tools.reported",
  "median": "wiki.tools.median",
  "reviewed": "wiki.tools.reviewed",
  "photo": "wiki.tools.photo",
  "imageMissing": "wiki.tools.imageMissing",
  "representative": "wiki.tools.representative",
  "sourcePhoto": "wiki.tools.sourcePhoto",
  "imageSource": "wiki.tools.imageSource",
  "gallery": "wiki.tools.gallery",
  "openPhoto": "wiki.tools.openPhoto",
  "close": "wiki.tools.close",
  "previous": "wiki.tools.previous",
  "next": "wiki.tools.next",
  "zoom": "wiki.tools.zoom",
  "family": "wiki.tools.family",
  "familyNote": "wiki.tools.familyNote",
  "profile": "wiki.tools.profile",
  "outsideCatalogue": "wiki.tools.outsideCatalogue",
  "ambiguousName": "wiki.tools.ambiguousName",
  "addCollection": "wiki.tools.addCollection",
  "manageCollections": "wiki.tools.manageCollections",
  "collectionName": "wiki.tools.collectionName",
  "createCollection": "wiki.tools.createCollection",
  "savedArticles": "wiki.tools.savedArticles",
  "recent": "wiki.tools.recent",
  "empty": "wiki.tools.empty",
  "editCollection": "wiki.tools.editCollection",
  "rename": "wiki.tools.rename",
  "deleteCollection": "wiki.tools.deleteCollection",
  "findProfile": "wiki.tools.findProfile",
  "chooseTwo": "wiki.tools.chooseTwo",
  "comparison": "wiki.tools.comparison",
  "browseProfiles": "wiki.tools.browseProfiles",
  "propose": "wiki.tools.propose",
  "note": "wiki.tools.note",
  "proposedValue": "wiki.tools.proposedValue",
  "sourceUrl": "wiki.tools.sourceUrl",
  "reason": "wiki.tools.reason",
  "submit": "wiki.tools.submit",
  "listHint": "wiki.tools.listHint",
  "classificationHint": "wiki.tools.classificationHint",
  "proposalNote": "wiki.tools.proposalNote",
  "submitted": "wiki.tools.submitted",
  "reviewedNotes": "wiki.tools.reviewedNotes",
  "referenceScope": "wiki.tools.referenceScope",
  "referenceScopeNote": "wiki.tools.referenceScopeNote",
  "searchAtlas": "wiki.tools.searchAtlas",
  "atlasHint": "wiki.tools.atlasHint",
  "atlasCompare": "wiki.tools.atlasCompare",
  "comparePhotos": "wiki.tools.comparePhotos",
  "showLookalike": "wiki.tools.showLookalike",
  "resetComparison": "wiki.tools.resetComparison",
  "all": "wiki.tools.all",
  "leaf": "wiki.tools.leaf",
  "roots": "wiki.tools.roots",
  "flower": "wiki.tools.flower",
  "insect": "wiki.tools.insect",
  "reviewQueue": "wiki.tools.reviewQueue",
  "noPending": "wiki.tools.noPending",
  "reviewNote": "wiki.tools.reviewNote",
  "accept": "wiki.tools.accept",
  "reject": "wiki.tools.reject",
  "accepted": "wiki.tools.accepted",
  "rejected": "wiki.tools.rejected",
  "reviewedCorrections": "wiki.tools.reviewedCorrections",
  "quality": "wiki.tools.quality",
  "filterIssues": "wiki.tools.filterIssues",
  "findArticle": "wiki.tools.findArticle",
  "more": "wiki.tools.more",
  "missing_image": "wiki.tools.missing_image",
  "representative_image": "wiki.tools.representative_image",
  "missing_source": "wiki.tools.missing_source",
  "stale_source": "wiki.tools.stale_source",
  "source_disagreement": "wiki.tools.source_disagreement",
  "missing_ancestry": "wiki.tools.missing_ancestry",
  "unknown_ratios": "wiki.tools.unknown_ratios",
  "unverified_availability": "wiki.tools.unverified_availability",
  "broken_link": "wiki.tools.broken_link",
  "checkSources": "wiki.tools.checkSources",
  "ok": "wiki.tools.ok",
  "broken": "wiki.tools.broken",
  "redirect": "wiki.tools.redirect",
  "unconfirmed": "wiki.tools.unconfirmed",
  "backToArticle": "wiki.tools.backToArticle",
  "historyCoverage": "wiki.tools.historyCoverage",
  "first_observed": "wiki.tools.first_observed",
  "observed": "wiki.tools.observed",
  "reviewed_correction": "wiki.tools.reviewed_correction",
  "before": "wiki.tools.before",
  "after": "wiki.tools.after",
  "noChanges": "wiki.tools.noChanges",
  "absent": "wiki.tools.absent",
  "notFound": "wiki.tools.notFound",
  "loading": "wiki.tools.loading",
  "compare_limit": "wiki.tools.compare_limit",
  "duplicate_collection": "wiki.tools.duplicate_collection",
  "invalid_source": "wiki.tools.invalid_source",
  "invalid_value": "wiki.tools.invalid_value",
  "stale_correction": "wiki.tools.stale_correction",
  "check_in_progress": "wiki.tools.check_in_progress",
  "limit_reached": "wiki.tools.limit_reached",
  "error": "wiki.tools.error"
};
  const tr = (key, values) => t(TEXT[key], values);
  const el = (tag, cls = "", text) => {
    const node = document.createElement(tag); node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const cid = (d) => `${d.folder}/${d.key}`;
  const href = (id) => `#wiki/${id.split("/").map(encodeURIComponent).join("/")}`;
  const fields = ["classification", "thc_percent", "parents", "aliases", "terpenes", "descendants"];
  const flags = ["missing_image", "representative_image", "missing_source", "stale_source", "source_disagreement",
    "missing_ancestry", "unknown_ratios", "unverified_availability", "broken_link"];
  let reading = null, readingPromise = null, readingLang = "", sequence = 0, queue = Promise.resolve();
  const date = (value) => value ? new Date(typeof value === "number" ? value * 1000 : `${value}T12:00:00Z`)
    .toLocaleString(LOCALE[LANG], { dateStyle: "medium", ...(typeof value === "number" ? { timeStyle: "short" } : {}) }) : tr("unknown");
  function a(text, target, external = false) {
    const node = el("a", "wt-link", text); node.href = target;
    if (external) { node.target = "_blank"; node.rel = "noopener noreferrer"; }
    return node;
  }
  function button(text, fn, cls = "") {
    const node = el("button", `wt-button ${cls}`, text); node.type = "button";
    node.addEventListener("click", async () => {
      node.disabled = true;
      try { await fn(node); } catch (err) { failure(node.closest(".wt-panel, .wt-actions, .wt-dialog, .wiki-view") || node.parentElement, err); }
      finally { node.disabled = false; }
    });
    return node;
  }
  function failure(parent, err) {
    const known = ["compare_limit", "duplicate_collection", "invalid_source", "invalid_value", "stale_correction", "check_in_progress", "limit_reached"];
    let box = parent.querySelector(":scope > .wt-error");
    if (!box) { box = el("p", "wt-error"); box.setAttribute("role", "alert"); parent.append(box); }
    box.textContent = known.includes(err?.message) ? tr(err.message) : tr("error");
  }
  function status(parent, key) {
    const box = el("p", "wiki-muted", tr(key)); box.setAttribute("role", "status"); parent.append(box);
    return box;
  }
  function toolbar(parent, current = "") {
    const nav = el("nav", "wt-toolbar"); nav.setAttribute("aria-label", tr("tools"));
    for (const name of ["browse", "compare", "atlas", "library", "editorial"]) {
      const link = a(tr(name), name === "browse" ? "#wiki" : `#wiki/tools/${name}`);
      if (name === current) link.setAttribute("aria-current", "page");
      if (name === "compare") link.dataset.compareCount = "";
      nav.append(link);
    }
    parent.append(nav); sync(); return nav;
  }
  async function loadReading(force = false) {
    if (readingLang !== LANG) { reading = null; readingPromise = null; readingLang = LANG; }
    await queue.catch(() => {});
    if (force) { ++sequence; reading = null; readingPromise = null; }
    if (reading) return reading;
    if (!readingPromise) {
      const order = sequence, language = LANG;
      readingPromise = api("/api/wiki/tools/state").then((data) => {
        if (sequence === order && language === LANG) { reading = data; sync(); }
        return reading || data;
      }).finally(() => { readingPromise = null; });
    }
    return readingPromise;
  }
  function mutate(body) {
    queue = queue.catch(() => {}).then(async () => {
      const order = ++sequence;
      const data = await api("/api/wiki/tools/state", body);
      if (order === sequence) { reading = data; readingLang = LANG; sync(); }
      return data;
    });
    return queue;
  }
  function sync() {
    if (!reading) return;
    document.querySelectorAll("[data-bookmark]").forEach((node) => {
      const on = reading.bookmarks.includes(node.dataset.bookmark);
      node.textContent = tr(on ? "saved" : "save"); node.setAttribute("aria-pressed", String(on));
    });
    document.querySelectorAll("[data-compare-article]").forEach((node) => {
      node.checked = reading.compare.includes(node.dataset.compareArticle);
    });
    document.querySelectorAll("[data-compare-count]").forEach((node) => {
      node.textContent = `${tr("compare")}${reading.compare.length ? ` (${reading.compare.length}/4)` : ""}`;
    });
  }
  function actions(id, variety) {
    const row = el("div", "wt-actions");
    const save = button(tr("save"), async () => { await loadReading(); await mutate({ action: "bookmark", article: id, selected: !reading.bookmarks.includes(id) }); });
    save.dataset.bookmark = id; save.setAttribute("aria-pressed", "false"); row.append(save);
    if (variety) {
      const label = el("label", "wt-check"); const input = el("input"); input.type = "checkbox"; input.dataset.compareArticle = id;
      input.addEventListener("change", async () => {
        input.disabled = true;
        try { await mutate({ action: "compare", article: id, selected: input.checked }); }
        catch (err) { sync(); failure(row, err); } finally { input.disabled = false; }
      });
      label.append(input, tr("compare")); row.append(label);
    }
    loadReading().then(sync).catch((err) => failure(row, err));
    return row;
  }
  function catalogue(ul, rows) {
    [...ul.children].forEach((li, i) => {
      const row = rows[i]; if (row) li.append(actions(cid(row), row.kind === "variety"));
    });
  }
  function value(field, v) {
    if (Array.isArray(v)) return v.join(field === "parents" ? " × " : ", ") || tr("unknown");
    if (v === null || v === undefined || v === "") return tr("unknown");
    if (field === "classification") return tr(String(v));
    if (field === "thc_percent") return `${new Intl.NumberFormat(LOCALE[LANG]).format(v)}%`;
    return String(v);
  }
  function evidenceBox(field, evidence) {
    const details = el("details", `wt-evidence${evidence.disputed ? " wt-disputed" : ""}`);
    details.append(el("summary", "", tr(evidence.disputed ? "sourcesDiffer" : "factSource")));
    const ul = el("ul");
    for (const claim of evidence.claims || []) {
      const li = el("li"); li.append(el("strong", "", value(field, claim.value)), " · ",
        a(claim.basis === "reviewed" ? tr("reviewed") : claim.source.title || tr("factSource"), claim.source.url, true),
        el("small", "wiki-muted", ` · ${tr(claim.basis === "median" ? "median" : claim.basis === "reviewed" ? "reviewed" : "reported")} · ${date(claim.retrieved)}`));
      if (claim.review_note) li.append(el("p", "", claim.review_note));
      ul.append(li);
    }
    details.append(ul); return details;
  }
  function image(photo) {
    const img = el("img", "wt-photo"); img.src = photo.src; img.alt = photo.alt || tr("photo");
    if (photo.large) { img.srcset = `${photo.src} 640w, ${photo.large} 1280w`; img.sizes = "(min-width: 760px) 60vw, 100vw"; }
    img.loading = "lazy"; img.decoding = "async"; img.referrerPolicy = "no-referrer";
    if (photo.w && photo.h) { img.width = photo.w; img.height = photo.h; }
    img.addEventListener("error", () => img.replaceWith(el("p", "wiki-empty", tr("imageMissing"))), { once: true });
    return img;
  }
  function credit(photo) {
    const cap = el("p", "wt-credit");
    if (photo.kind === "external") {
      cap.append(tr(photo.representative ? "representative" : "sourcePhoto"), " · ", a(tr("imageSource"), photo.source, true));
    } else if (photo.credit) {
      const c = photo.credit; cap.append(`${c.author} · `, a(c.license, c.license_url, true), " · ", a(c.work, c.page_url, true));
    }
    return cap;
  }
  function modal(title, trigger) {
    const dialog = el("dialog", "wt-dialog");
    const heading = el("h2", "", title); heading.id = `wt-dialog-${Date.now()}`; dialog.setAttribute("aria-labelledby", heading.id);
    const close = button(tr("close"), () => dialog.close(), "wt-close");
    dialog.append(close, heading); document.body.append(dialog);
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dialog.close(); }
    });
    dialog.addEventListener("close", () => { dialog.remove(); if (trigger?.isConnected) trigger.focus({ preventScroll: true }); }, { once: true });
    dialog.addEventListener("click", (event) => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
    return dialog;
  }
  function gallery(parent, photos) {
    if (!photos?.length) return;
    const box = el("section", "wt-panel wt-gallery wiki-sec"); box.append(el("h3", "wiki-h", tr("gallery")));
    const strip = el("div", "wt-thumbnails");
    photos.forEach((photo, i) => {
      const thumb = button(tr("openPhoto", { n: i + 1 }), (trigger) => {
        const dialog = modal(tr("gallery"), trigger); const stage = el("div", "wt-photo-stage"), caption = el("div"), nav = el("div", "wt-actions");
        let at = i; const count = el("span"); count.setAttribute("aria-live", "polite");
        const draw = () => { stage.replaceChildren(image(photos[at])); caption.replaceChildren(credit(photos[at])); count.textContent = `${at + 1} / ${photos.length}`; };
        const move = (step) => { at = (at + step + photos.length) % photos.length; draw(); };
        const zoom = button(tr("zoom"), () => { const on = stage.classList.toggle("is-zoomed"); zoom.setAttribute("aria-pressed", String(on)); }); zoom.setAttribute("aria-pressed", "false");
        nav.append(button(tr("previous"), () => move(-1)), count, button(tr("next"), () => move(1)), zoom);
        dialog.append(stage, nav, caption);
        dialog.addEventListener("keydown", (event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1); } });
        draw(); dialog.showModal();
      }, "wt-thumb");
      thumb.replaceChildren(image(photo), el("span", "", `${i + 1}`)); thumb.setAttribute("aria-label", tr("openPhoto", { n: i + 1 })); strip.append(thumb);
    });
    box.append(strip, credit(photos[0])); parent.append(box);
  }
  async function family(parent, d) {
    if (d.kind !== "variety") return;
    const box = el("section", "wt-panel wt-family wiki-sec"); box.append(el("h3", "wiki-h", tr("family")), el("p", "wiki-muted", tr("familyNote"))); parent.append(box);
    try {
      const data = await api("/api/wiki/varieties"); if (!box.isConnected) return;
      const fold = (name) => name.normalize("NFKD").replace(/\p{Diacritic}/gu, "").toLowerCase().replace(/[^a-z0-9]/g, "");
      const known = new Map(); for (const row of data.varieties) for (const name of [row.label, ...(row.reference.aliases || [])]) { const k = fold(name); const matches = known.get(k) || []; if (!matches.includes(cid(row))) matches.push(cid(row)); known.set(k, matches); }
      const tree = el("div", "wt-tree"); const ref = d.card.reference;
      for (const [key, names] of [["parents", ref.parents || []], ["profile", [d.label]], ["descendants", ref.descendants || []]]) {
        const col = el("div"); col.append(el("h4", "", tr(key)));
        if (!names.length) col.append(el("p", "wiki-muted", tr("unknown")));
        for (const name of names) {
          const matches = known.get(fold(name)) || []; const node = el("p", "wt-tree-node");
          if (key === "profile") node.append(el("strong", "", name));
          else if (matches.length === 1) node.append(a(name, href(matches[0])));
          else node.append(name, el("small", "wiki-muted", ` · ${tr(matches.length > 1 ? "ambiguousName" : "outsideCatalogue")}`));
          col.append(node);
        }
        tree.append(col);
      }
      box.append(tree);
      if (d.evidence?.parents) box.append(evidenceBox("parents", d.evidence.parents));
    } catch (err) { failure(box, err); }
  }
  async function collections(parent, id) {
    const wrap = el("details", "wt-collections-menu"); wrap.append(el("summary", "", tr("addCollection"))); parent.append(wrap);
    try {
      const data = await loadReading(); if (!wrap.isConnected) return;
      for (const collection of data.collections) {
        const label = el("label", "wt-check"), input = el("input"); input.type = "checkbox"; input.checked = collection.articles.includes(id);
        input.addEventListener("change", async () => { input.disabled = true; try { await mutate({ action: "member", article: id, collection: collection.id, selected: input.checked }); } catch (err) { input.checked = !input.checked; failure(wrap, err); } finally { input.disabled = false; } });
        label.append(input, collection.name); wrap.append(label);
      }
      wrap.append(a(tr("manageCollections"), "#wiki/tools/library"));
    } catch (err) { failure(wrap, err); }
  }
  function input(label, type = "text") {
    const wrapper = el("label", "wt-field"); const node = el(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") node.type = type;
    wrapper.append(el("span", "", label), node); return { wrapper, node };
  }
  function correction(d, trigger) {
    const dialog = modal(tr("propose"), trigger), form = el("form", "wt-form"), selectLabel = el("label", "wt-field", tr("field")), field = el("select");
    for (const name of [...(d.kind === "variety" ? fields : []), "note"]) { const option = el("option", "", tr(name)); option.value = name; field.append(option); }
    selectLabel.append(field);
    const content = input(tr("proposedValue"), "textarea"), source = input(tr("sourceUrl"), "url"), comment = input(tr("reason"), "textarea");
    content.node.required = true; content.node.maxLength = 2000; source.node.maxLength = 600; comment.node.required = true; comment.node.maxLength = 2000;
    const hint = el("p", "wiki-muted");
    const change = () => { const v = d.card.reference?.[field.value]; content.node.value = v === undefined ? "" : Array.isArray(v) ? v.join(", ") : String(v); source.node.required = field.value !== "note"; hint.textContent = tr(fields.includes(field.value) && field.value !== "classification" && field.value !== "thc_percent" ? "listHint" : field.value === "classification" ? "classificationHint" : "proposalNote"); };
    field.addEventListener("change", change); change();
    const send = el("button", "wt-button", tr("submit")); send.type = "submit";
    form.append(selectLabel, content.wrapper, hint, source.wrapper, comment.wrapper, send);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); send.disabled = true;
      try {
        let proposed = content.node.value.trim();
        if (field.value === "thc_percent") proposed = Number(proposed.replace(",", "."));
        else if (["parents", "aliases", "terpenes", "descendants"].includes(field.value)) proposed = proposed.split(",").map((s) => s.trim()).filter(Boolean);
        await api("/api/wiki/tools/corrections/propose", { article: cid(d), field: field.value, value: proposed, source: source.node.value, comment: comment.node.value, base: d.revision.digest });
        form.replaceChildren(el("p", "", tr("submitted")), a(tr("editorial"), "#wiki/tools/editorial"));
      } catch (err) { failure(form, err); } finally { send.disabled = false; }
    });
    dialog.append(el("p", "wiki-muted", tr("proposalNote")), form); dialog.showModal();
  }
  function article(view, d) {
    const nav = toolbar(view); view.insertBefore(nav, view.querySelector(".wiki-head"));
    const row = actions(cid(d), d.kind === "variety");
    row.append(a(tr("history"), `#wiki/tools/history/${cid(d)}`), button(tr("propose"), (trigger) => correction(d, trigger)));
    view.insertBefore(row, view.querySelector(".wiki-intro") || view.lastElementChild);
    collections(row, cid(d)); gallery(view, d.gallery); family(view, d);
    for (const cell of view.querySelectorAll(".wiki-profile-facts dd[data-fact]")) {
      const ev = d.evidence?.[cell.dataset.fact]; if (ev?.claims?.length) cell.append(evidenceBox(cell.dataset.fact, ev));
    }
    if (d.fact_sources?.length) {
      view.querySelectorAll(".wiki-dl dd").forEach((cell) => {
        const details = el("details", "wt-evidence"); details.append(el("summary", "", tr("referenceScope")), el("p", "wiki-muted", tr("referenceScopeNote")));
        d.fact_sources.forEach((s) => details.append(a(s.title, s.url, true), " ")); cell.append(details);
      });
    }
    if (d.reviewed_notes?.length) {
      const notes = el("section", "wt-panel wiki-sec"); notes.append(el("h3", "wiki-h", tr("reviewedNotes")));
      for (const note of d.reviewed_notes) { const p = el("p", "", note.text); if (note.source) p.append(" · ", a(tr("factSource"), note.source, true)); notes.append(p, el("small", "wiki-muted", `${date(note.reviewed)} · ${note.review_note}`)); }
      view.append(notes);
    }
    mutate({ action: "visit", article: cid(d) }).catch((err) => failure(row, err));
  }
  function shell(view, tool) {
    view.replaceChildren(); toolbar(view, tool);
    const section = el("section", `wt-panel wt-${tool}`); section.append(el("h2", "wiki-title", tr(tool)), el("p", "wiki-muted", tr(`${tool}Intro`))); view.append(section);
    return section;
  }
  async function compare(box) {
    const [data, state] = await Promise.all([api("/api/wiki/varieties"), loadReading(true)]); if (!box.isConnected) return;
    const search = input(tr("findProfile"), "search"), choices = el("div", "wt-choice-list"), tableBox = el("div", "wt-compare-table");
    const draw = () => {
      const chosen = data.varieties.filter((row) => reading.compare.includes(cid(row)));
      choices.replaceChildren();
      const q = search.node.value.toLowerCase().trim();
      for (const row of data.varieties.filter((r) => `${r.label} ${(r.reference.aliases || []).join(" ")}`.toLowerCase().includes(q)).slice(0, 12)) {
        const label = el("label", "wt-check"), check = el("input"); check.type = "checkbox"; check.checked = reading.compare.includes(cid(row));
        check.dataset.compareArticle = cid(row);
        check.addEventListener("change", async () => { check.disabled = true; try { await mutate({ action: "compare", article: cid(row), selected: check.checked }); draw(); } catch (err) { sync(); failure(box, err); } finally { check.disabled = false; } });
        label.append(check, row.label); choices.append(label);
      }
      tableBox.replaceChildren();
      if (chosen.length < 2) tableBox.append(el("p", "wiki-empty", tr("chooseTwo")));
      if (!chosen.length) return;
      const table = el("table"); table.append(el("caption", "", tr("comparison"))); const head = el("tr"); head.append(el("th", "", tr("field")));
      for (const row of chosen) { const th = el("th"); th.scope = "col"; th.append(a(row.label, href(cid(row))), button(tr("remove"), async () => { await mutate({ action: "compare", article: cid(row), selected: false }); draw(); })); head.append(th); }
      const thead = el("thead"); thead.append(head); table.append(thead); const body = el("tbody");
      for (const field of [...fields, "retrieved", "availability"]) {
        const line = el("tr"), th = el("th", "", tr(field)); th.scope = "row"; line.append(th);
        for (const row of chosen) {
          const td = el("td", "", field === "availability" ? tr("unverified") : field === "retrieved" ? date(row.reference.retrieved) : value(field, row.reference[field]));
          if (row.evidence?.[field]?.claims?.length) td.append(evidenceBox(field, row.evidence[field])); line.append(td);
        }
        body.append(line);
      }
      table.append(body); tableBox.append(table);
    };
    if (!reading) reading = state; search.node.addEventListener("input", draw); box.append(search.wrapper, choices, a(tr("browseProfiles"), "#wiki/varieties"), tableBox); draw();
  }
  function articleList(parent, ids, state, remove) {
    if (!ids.length) { parent.append(el("p", "wiki-muted", tr("empty"))); return; }
    const list = el("ul", "wt-saved-list");
    for (const id of ids) {
      const row = state.articles[id]; if (!row) continue; const li = el("li"); li.append(a(row.label, href(id)));
      if (remove) li.append(button(tr("remove"), async () => { await remove(id); li.remove(); })); list.append(li);
    }
    parent.append(list);
  }
  async function library(box) {
    const data = await loadReading(true); if (!box.isConnected) return;
    const form = el("form", "wt-actions"), name = input(tr("collectionName")); name.node.required = true; name.node.maxLength = 80;
    const create = el("button", "wt-button", tr("createCollection")); create.type = "submit"; form.append(name.wrapper, create);
    form.addEventListener("submit", async (event) => { event.preventDefault(); create.disabled = true; try { await mutate({ action: "collection_create", name: name.node.value }); await window.WikiRefresh(); } catch (err) { failure(box, err); } finally { create.disabled = false; } });
    box.append(form);
    const saved = el("section"); saved.append(el("h3", "", tr("savedArticles"))); articleList(saved, data.bookmarks, data, (id) => mutate({ action: "bookmark", article: id, selected: false })); box.append(saved);
    for (const collection of data.collections) {
      const part = el("section", "wt-collection"); const title = el("h3", "", collection.name); part.append(title);
      const edit = input(tr("collectionName")); edit.node.value = collection.name; edit.node.maxLength = 80;
      const controls = el("details"); controls.append(el("summary", "", tr("editCollection")), edit.wrapper,
        button(tr("rename"), async () => { await mutate({ action: "collection_rename", collection: collection.id, name: edit.node.value }); title.textContent = edit.node.value.trim(); controls.open = false; }),
        button(tr("deleteCollection"), async () => { await mutate({ action: "collection_delete", collection: collection.id }); part.remove(); }));
      part.append(controls); articleList(part, collection.articles, data, (id) => mutate({ action: "member", article: id, collection: collection.id, selected: false })); box.append(part);
    }
    const recent = el("section"); recent.append(el("h3", "", tr("recent"))); articleList(recent, data.recent, data); box.append(recent);
  }
  function atlasCard(item) {
    const panel = el("article", "wt-atlas-card"); panel.append(el("h3", "", item.label), el("p", "wiki-muted", item.description));
    const picture = el("div", "wt-annotated"); const photo = image(item.photo); picture.append(photo);
    const observations = el("ol", "wt-observations");
    item.markers.forEach((marker, index) => {
      const observation = el("li"); observation.append(el("strong", "", `${marker.label}: `), marker.text); observations.append(observation);
      const point = button(String(index + 1), () => { observations.querySelectorAll("li").forEach((li) => li.classList.remove("is-selected")); observation.classList.add("is-selected"); observation.scrollIntoView({ block: "nearest", behavior: "instant" }); observation.tabIndex = -1; observation.focus({ preventScroll: true }); }, "wt-marker");
      point.style.left = `${marker.x}%`; point.style.top = `${marker.y}%`; point.setAttribute("aria-label", `${index + 1}. ${marker.label}`); picture.append(point);
    });
    photo.addEventListener("error", () => { picture.querySelectorAll(".wt-marker").forEach((marker) => marker.remove()); }, { once: true });
    panel.append(picture, observations, credit(item.photo)); return panel;
  }
  async function atlas(box) {
    const data = await api("/api/wiki/tools/atlas"); if (!box.isConnected) return;
    const q = input(tr("searchAtlas"), "search"), filters = el("div", "wt-actions"), results = el("div", "wt-atlas-grid"), selected = new Set(); let part = "all";
    const note = status(box, "atlasHint");
    const draw = () => {
      results.replaceChildren(); note.textContent = selected.size ? tr("atlasCompare", { n: selected.size }) : tr("atlasHint");
      const rows = selected.size === 2 ? data.items.filter((item) => selected.has(item.id)) : data.items.filter((item) => (part === "all" || item.part === part) && `${item.label} ${item.description}`.toLowerCase().includes(q.node.value.toLowerCase()));
      for (const item of rows) {
        const panel = atlasCard(item); const label = el("label", "wt-check"), check = el("input"); check.type = "checkbox"; check.checked = selected.has(item.id); check.disabled = selected.size === 2 && !check.checked;
        check.addEventListener("change", () => { check.checked ? selected.add(item.id) : selected.delete(item.id); draw(); }); label.append(check, tr("comparePhotos")); panel.prepend(label);
        if (item.lookalikes?.length) panel.append(button(tr("showLookalike"), () => { selected.clear(); selected.add(item.id); selected.add(item.lookalikes[0]); draw(); }));
        results.append(panel);
      }
      if (!rows.length) results.append(el("p", "wiki-empty", tr("empty")));
      filters.querySelectorAll("button[data-part]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.part === part)));
    };
    for (const key of ["all", "leaf", "roots", "flower", "insect"]) { const b = button(tr(key), () => { part = key; selected.clear(); draw(); }); b.dataset.part = key; filters.append(b); }
    filters.append(button(tr("resetComparison"), () => { selected.clear(); draw(); })); q.node.addEventListener("input", () => { selected.clear(); draw(); }); box.append(q.wrapper, filters, results); draw();
  }
  async function editorial(box) {
    const data = await api("/api/wiki/tools/editorial"); if (!box.isConnected) return;
    const queueBox = el("section", "wt-review-queue"); queueBox.append(el("h3", "", tr("reviewQueue")));
    const pending = data.corrections.filter((c) => c.status === "pending");
    if (!pending.length) queueBox.append(el("p", "wiki-muted", tr("noPending")));
    for (const edit of pending) {
      const item = el("article", "wt-review-item"); item.append(a(edit.label, href(edit.article)), el("h4", "", tr(edit.field)), el("p", "", value(edit.field, edit.value)), el("p", "wiki-muted", edit.comment));
      if (edit.source) item.append(a(tr("factSource"), edit.source, true));
      const note = input(tr("reviewNote"), "textarea"); note.node.maxLength = 2000; note.node.required = true;
      const form = el("form", "wt-form"); form.append(note.wrapper);
      for (const decision of ["accepted", "rejected"]) { const b = el("button", "wt-button", tr(decision === "accepted" ? "accept" : "reject")); b.type = "submit"; b.value = decision; form.append(b); }
      form.addEventListener("submit", async (event) => {
        event.preventDefault(); const buttons = [...form.querySelectorAll("button")]; buttons.forEach((b) => { b.disabled = true; });
        try { await api("/api/wiki/tools/corrections/review", { id: edit.id, decision: event.submitter.value, note: note.node.value }); window.WikiInvalidate(); await window.WikiRefresh(); }
        catch (err) { failure(item, err); } finally { buttons.forEach((b) => { b.disabled = false; }); }
      });
      item.append(form); queueBox.append(item);
    }
    box.append(queueBox);
    const archive = el("details", "wt-review-archive"); archive.append(el("summary", "", tr("reviewedCorrections")));
    for (const edit of data.corrections.filter((c) => c.status !== "pending").slice(0, 50)) { const p = el("p"); p.append(a(edit.label, href(edit.article)), ` · ${tr(edit.status)} · ${tr(edit.field)}: ${value(edit.field, edit.value)} · ${edit.review_note} · ${date(edit.reviewed)}`); archive.append(p); }
    box.append(archive, el("h3", "", tr("quality")));
    const filter = el("select"), filterLabel = el("label", "wt-field", tr("filterIssues"));
    for (const key of ["all", ...flags]) { const option = el("option", "", `${tr(key)}${data.counts[key] !== undefined ? ` (${data.counts[key]})` : ""}`); option.value = key; filter.append(option); }
    filterLabel.append(filter); const q = input(tr("findArticle"), "search"), list = el("div", "wt-quality-list"), more = button(tr("more"), () => { shown += 30; draw(); }); let shown = 30;
    const draw = () => {
      list.replaceChildren(); const rows = data.articles.filter((row) => (filter.value === "all" || row.flags.includes(filter.value)) && row.label.toLowerCase().includes(q.node.value.toLowerCase()));
      for (const row of rows.slice(0, shown)) {
        const item = el("article", "wt-quality-item"); item.append(a(row.label, href(row.article)));
        const labels = el("div", "wt-tags"); row.flags.forEach((flag) => labels.append(el("span", "wt-tag", tr(flag)))); item.append(labels);
        const output = el("div", "wt-link-results");
        const showChecks = (checks) => { output.replaceChildren(); for (const check of checks) { const p = el("p"); p.append(a(new URL(check.url).hostname, check.url, true), ` · ${tr(check.status)}${check.http_status ? ` (${check.http_status})` : ""} · ${date(check.checked)}`); output.append(p); } };
        showChecks(row.checks); item.append(button(tr("checkSources"), async () => { const result = await api("/api/wiki/tools/check-links", { article: row.article }); row.checks = result.checks; showChecks(row.checks); }), output, a(tr("history"), `#wiki/tools/history/${row.article}`)); list.append(item);
      }
      more.hidden = rows.length <= shown;
    };
    filter.addEventListener("change", () => { shown = 30; draw(); }); q.node.addEventListener("input", () => { shown = 30; draw(); }); box.append(filterLabel, q.wrapper, list, more); draw();
  }
  function flatten(value, prefix = "", result = {}) {
    if (value && typeof value === "object" && !Array.isArray(value)) { for (const [key, v] of Object.entries(value)) flatten(v, prefix ? `${prefix}.${key}` : key, result); }
    else result[prefix] = typeof value === "string" ? value : JSON.stringify(value);
    return result;
  }
  async function history(box, id) {
    const data = await api(`/api/wiki/tools/history?article=${encodeURIComponent(id)}`); if (!box.isConnected) return;
    box.append(a(tr("backToArticle"), href(id)), el("p", "wiki-muted", tr("historyCoverage")));
    const first = el("select"), last = el("select"), controls = el("div", "wt-history-selectors"), diff = el("div", "wt-diff");
    for (const select of [first, last]) {
      data.revisions.forEach((r, index) => { const option = el("option", "", `#${r.id} · ${date(r.observed)} · ${tr(r.reason)}`); option.value = String(index); select.append(option); });
      const label = el("label", "wt-field", tr(select === first ? "before" : "after")); label.append(select); controls.append(label);
    }
    first.value = String(Math.min(1, data.revisions.length - 1)); last.value = "0";
    const draw = () => {
      diff.replaceChildren(); const before = flatten(data.revisions[Number(first.value)].snapshot), after = flatten(data.revisions[Number(last.value)].snapshot);
      const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((key) => before[key] !== after[key]);
      if (!changed.length) { diff.append(el("p", "wiki-empty", tr("noChanges"))); return; }
      for (const key of changed) { const row = el("section", "wt-change"); row.append(el("h4", "", key), el("p", "wt-before", before[key] ?? tr("absent")), el("p", "wt-after", after[key] ?? tr("absent"))); diff.append(row); }
    };
    first.addEventListener("change", draw); last.addEventListener("change", draw); box.append(controls, diff); draw();
  }
  async function open(view, tool, argument) {
    if (!["compare", "atlas", "library", "editorial", "history"].includes(tool)) { view.replaceChildren(el("p", "wiki-empty", tr("notFound"))); return; }
    const box = shell(view, tool); const loading = status(box, "loading");
    try { await ({ compare, atlas, library, editorial, history })[tool](box, argument); }
    catch (err) { if (box.isConnected) failure(box, err); } finally { loading.remove(); }
  }
  window.WikiTools = { home: (parent) => toolbar(parent), catalogue, article, open };
  window.addEventListener("hashchange", () => { document.querySelectorAll(".wt-dialog").forEach((dialog) => dialog.close()); });
})();
