// The wiki: one "Wiki" entry under "News" in the section navigation opens a view of every card of kartei/.
// Loaded after app.js and uses its globals t(), tn(), api(), LANG, LOCALE and state; it never touches render().
// A guest like the news: until /api/wiki answers once, the entry stays hidden and the cockpit runs on without it.
// The view replaces the cockpit inside <main> while the address ends in #wiki (#wiki/folder/key for one card,
// #wiki/sorten for the strain lexicon, #wiki/duenger for the fertilizer list), so the browser's back button, a bookmark and a shared link all work; any other
// address shows the cockpit again.
// Every text is a key of web/i18n.js (wiki.*), written out in full here, so tests/test_web_i18n.py can check each one.
// No markup from data: card texts go in through textContent, links only with http: or https:, in a new tab.
(function () {
  "use strict";

  if (!window.I18N || typeof api !== "function") return;

  const CAT = {
    health: "wiki.lex.botanicalReference", varieties: "wiki.lex.varietyTitle",
    pflanzen: "wiki.cat.pflanzen", cannabis: "wiki.cat.cannabis", vorgaenge: "wiki.cat.vorgaenge",
    methoden: "wiki.cat.methoden", duengen: "wiki.cat.duengen", glossar: "wiki.cat.glossar", recht: "wiki.cat.recht",
    weitere: "wiki.cat.weitere", sorten: "wiki.cat.sorten", duenger: "wiki.cat.duenger",
  };
  // The fertilizer list (vgrow/wiki.py fertilizers_view, GET /api/wiki/fertilizers): the types and media its rows
  // carry, and the filters (owner, 23.10.2026: "welche Dünger es gibt, was gut ist, wie was angewendet wird").
  const FTYPE = {
    organic_liquid: "wiki.ftype.organic_liquid", mineral_liquid: "wiki.ftype.mineral_liquid",
    multi_part: "wiki.ftype.multi_part", powder: "wiki.ftype.powder", slow_release: "wiki.ftype.slow_release",
    pre_fertilised_soil: "wiki.ftype.pre_fertilised_soil",
  };
  const FMEDIA = { soil: "wiki.fmedia.soil", coco: "wiki.fmedia.coco", hydro: "wiki.fmedia.hydro" };
  const FF_EMPTY = { type: "", medium: "" };
  // The strain lexicon (vgrow/strains.py, GET /api/wiki/strains): its four box groups in the server's order, the
  // filters, and how many rows one page shows before "Mehr laden".
  const FIT = { sure: "wiki.strainFit.sure", lst: "wiki.strainFit.lst", unknown: "wiki.strainFit.unknown",
    tall: "wiki.strainFit.tall" };
  const FIT_TEXT = { sure: "wiki.strainFit.sure.text", lst: "wiki.strainFit.lst.text",
    unknown: "wiki.strainFit.unknown.text", tall: "wiki.strainFit.tall.text" };
  const FIT_PILL = { sure: "ok", lst: "info", unknown: "", tall: "warn" };
  const FLOWERING = { auto: "wiki.strains.auto", photoperiod: "wiki.strains.photo" };
  const STRAIN_PAGE = 24;
  const BLOOM_WEEKS = [8, 9, 10];
  const HARVEST_DAYS = [70, 80, 90];
  const SF_EMPTY = { q: "", fit: "", type: "", bloom: 0, harvest: 0, diff: "", brand: "" };
  const KIND = {
    variety: "wiki.lex.varietyTitle", plant_condition: "wiki.lex.botanicalReference",
    crop: "wiki.kind.crop", process: "wiki.kind.process", method: "wiki.kind.method", problem: "wiki.kind.problem",
    term: "wiki.kind.term", stage: "wiki.kind.stage", strain: "wiki.kind.strain", nutrient: "wiki.kind.nutrient",
    legal: "wiki.kind.legal", card: "wiki.kind.card", fertilizer_types: "wiki.kind.fertilizer_types",
  };
  // Groups inside a category, in the order the server sorts them (vgrow/wiki.py GROUP_ORDER).
  const GROUP = {
    stages_strains: "wiki.group.stages_strains", naehrstoffe: "wiki.group.naehrstoffe",
    schaedlinge: "wiki.group.schaedlinge", krankheiten: "wiki.group.krankheiten", stress: "wiki.group.stress",
  };
  const GROUP_HINT = {
    naehrstoffe: "wiki.group.naehrstoffe.hint", schaedlinge: "wiki.group.schaedlinge.hint",
    krankheiten: "wiki.group.krankheiten.hint", stress: "wiki.group.stress.hint",
  };
  const METRIC = {
    probe_temp: "wiki.metric.probe_temp", probe_humidity: "wiki.metric.probe_humidity",
    probe_vpd: "wiki.metric.probe_vpd", photoperiod_h: "wiki.metric.photoperiod_h", water_temp: "wiki.metric.water_temp",
  };
  const WHEN = { day: "wiki.when.day", night: "wiki.when.night" };
  const UNIT = { days: "wiki.unit.days", hours: "wiki.unit.hours", weeks: "wiki.unit.weeks" };
  const SPECTRUM_KEY = { seedling: "spectrum.seedling", veg: "spectrum.veg", bloom: "spectrum.bloom" };
  // What the box measures, as [decimals, unit]: the live value next to a check or a glossary term.
  const LIVE = {
    probe_temp: [1, "°C"], probe_humidity: [0, "%"], probe_vpd: [2, "kPa"], water_temp: [1, "°C"],
    temp: [1, "°C"], humidity: [0, "%"], vpd: [2, "kPa"],
  };
  const PACKAGE = "cannabis";
  const W = { built: false, view: null, index: null, card: null, query: "", results: null, error: "", timer: 0,
    seq: 0, from: "", own: undefined, strains: null, sf: { ...SF_EMPTY }, shown: STRAIN_PAGE,
    fert: null, ff: { ...FF_EMPTY }, indexOpen: new Set(), topicQueries: {}, topic: "",
    varieties: null, vq: "", vtype: "", vpick: "", vshown: 24 };
  // Categories drawn as picture tiles; the others (glossary, feeding, law) as compact rows with a small picture.
  const TILE_CATS = new Set(["pflanzen", "cannabis", "vorgaenge", "methoden"]);

  // ---------- pictures: photos from web/img/wiki/, the wiki's own drawings, the owner's camera ----------

  // Every panel of web/img/wiki/drawings.svg with its alt text, written out so tests/test_web_i18n.py sees each key.
  const ART_ALT = {
    "topping-1": "wiki.art.topping_1", "topping-2": "wiki.art.topping_2", "topping-3": "wiki.art.topping_3",
    "fim-1": "wiki.art.fim_1", "fim-2": "wiki.art.fim_2", "fim-3": "wiki.art.fim_3",
    "main-1": "wiki.art.main_1", "main-2": "wiki.art.main_2", "main-3": "wiki.art.main_3",
    "lst-1": "wiki.art.lst_1", "lst-2": "wiki.art.lst_2", "lst-3": "wiki.art.lst_3",
    "scrog-1": "wiki.art.scrog_1", "scrog-2": "wiki.art.scrog_2", "scrog-3": "wiki.art.scrog_3",
    "lolli-1": "wiki.art.lolli_1", "lolli-2": "wiki.art.lolli_2", "lolli-3": "wiki.art.lolli_3",
    "defol-1": "wiki.art.defol_1", "defol-2": "wiki.art.defol_2", "defol-3": "wiki.art.defol_3",
    "sc-1": "wiki.art.sc_1", "sc-2": "wiki.art.sc_2", "sc-3": "wiki.art.sc_3",
    "bp-1": "wiki.art.bp_1", "bp-2": "wiki.art.bp_2", "bp-3": "wiki.art.bp_3",
    "tom-1": "wiki.art.tom_1", "tom-2": "wiki.art.tom_2", "tom-3": "wiki.art.tom_3",
    "strain-auto-1": "wiki.art.strain_auto_1", "strain-1": "wiki.art.strain_1",
    "stretch-1": "wiki.art.stretch_1", "stretch-2": "wiki.art.stretch_2", "stretch-3": "wiki.art.stretch_3",
    "spectrum-1": "wiki.art.spectrum_1", "photoperiod-1": "wiki.art.photoperiod_1",
    "hysteresis-1": "wiki.art.hysteresis_1",
    // the problem cards without a fitting free photo: one panel each
    "aphid-1": "wiki.art.aphid_1", "russet-mite-1": "wiki.art.russet_mite_1",
    "calcium-deficiency-1": "wiki.art.calcium_deficiency_1", "sulfur-deficiency-1": "wiki.art.sulfur_deficiency_1",
    "light-burn-1": "wiki.art.light_burn_1", "wind-burn-1": "wiki.art.wind_burn_1",
    "low-humidity-high-vpd-1": "wiki.art.low_humidity_high_vpd_1", "cold-stress-1": "wiki.art.cold_stress_1",
    "heat-stress-1": "wiki.art.heat_stress_1",
  };
  const PHASE = ["wiki.art.before", "wiki.art.action", "wiki.art.after"];  // three panels: before, the move, after
  const SVG_NS = "http://www.w3.org/2000/svg";
  // The drawings file is parsed once and copied element by element through these lists: no script, no link, no
  // foreign markup can come along, and the copies take the page's colour tokens (web/wiki.css .wa-*).
  const SVG_TAGS = new Set(["g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text"]);
  const SVG_ATTRS = new Set(["d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height",
    "points", "transform", "class", "text-anchor"]);
  const OWN_SRC = /^\/static\/img\/wiki\/[a-z0-9-]+\.(?:webp|jpg)$/;
  const VERSION = (() => {
    const src = document.currentScript && document.currentScript.src;
    const m = typeof src === "string" ? src.match(/[?&]v=([\w.-]+)/) : null;
    return m ? m[1] : "";
  })();
  const ART = { doc: null, failed: false, loading: null };

  function loadArt() {
    if (ART.doc || ART.failed) return Promise.resolve(ART.doc);
    if (typeof fetch !== "function" || typeof DOMParser !== "function") { ART.failed = true; return Promise.resolve(null); }
    if (!ART.loading) {
      ART.loading = fetch(`/static/img/wiki/drawings.svg${VERSION ? `?v=${VERSION}` : ""}`, { credentials: "same-origin" })
        .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
        .then((text) => {
          const doc = new DOMParser().parseFromString(text, "image/svg+xml");
          if (!doc || doc.getElementsByTagName("parsererror").length) throw new Error("svg");
          ART.doc = doc;
          return doc;
        })
        .catch(() => { ART.failed = true; return null; });
    }
    return ART.loading;
  }

  function rebuild(node) {
    const tag = node && node.localName;
    if (!SVG_TAGS.has(tag)) return null;
    const out = document.createElementNS(SVG_NS, tag);
    for (const a of Array.from(node.attributes || [])) if (SVG_ATTRS.has(a.name)) out.setAttribute(a.name, a.value);
    if (tag === "text") out.textContent = String(node.textContent || "").slice(0, 3);
    else for (const child of Array.from(node.children || [])) { const c = rebuild(child); if (c) out.append(c); }
    return out;
  }

  function hasPanel(fig) { return !!(ART.doc && ART.doc.getElementById(fig)); }

  function panels(id) {
    const out = [];
    for (let i = 1; i <= 6 && hasPanel(`${id}-${i}`); i++) out.push(`${id}-${i}`);
    return out;
  }

  function artAlt(figs) {
    const parts = figs.map((f) => (ART_ALT[f] ? t(ART_ALT[f]) : "")).filter(Boolean);
    if (!parts.length) return "";
    return parts.length === 1 ? parts[0] : t("wiki.art.set", { n: parts.length, parts: parts.join("; ") });
  }

  // One panel (or a glyph) as an inline <svg> on the 160 x 200 canvas; null while the drawings are not there.
  function svgPanel(fig, alt) {
    if (!hasPanel(fig)) return null;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 160 200");
    svg.setAttribute("class", "wiki-svg");
    if (alt) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", alt); } else svg.setAttribute("aria-hidden", "true");
    const g = rebuild(ART.doc.getElementById(fig));
    if (g) svg.append(g);
    return svg;
  }

  // A box that fills itself once the drawings have loaded; `make` returns the node, or null for the fallback.
  function artBox(cls, make, fallback) {
    const box = el("div", cls);
    const fill = () => {
      const node = make();
      box.replaceChildren(...(node ? [node] : fallback ? [fallback()] : []));
      box.classList.toggle("wiki-art-none", !node);
    };
    if (ART.doc || ART.failed) fill();
    else loadArt().then(fill);
    return box;
  }

  // The neutral tile: the kind's glyph from the drawings, or its name when even the drawings are missing.
  function glyph(kind) {
    const k = KIND[kind] ? kind : "card";
    return artBox("wiki-glyph", () => svgPanel(`glyph-${k}-1`, ""), () => el("span", "wiki-glyph-t", t(KIND[k])));
  }

  function photoImg(photo, thumb) {
    const src = photo && (thumb ? photo.thumb : photo.src);
    if (typeof src !== "string" || !OWN_SRC.test(src)) return null;
    const img = el("img", "wiki-img");
    img.alt = textOf(photo.alt);
    img.loading = "lazy";
    img.decoding = "async";
    if (!thumb && photo.w && photo.h) { img.width = photo.w; img.height = photo.h; }
    img.src = src;
    return img;
  }

  // A picture that cannot load gives way to the drawing or the tile, never a broken image.
  function guard(img, fallback) {
    img.addEventListener("error", () => { const f = fallback(); if (f && img.parentNode) img.replaceWith(f); }, { once: true });
    return img;
  }

  // The tile's picture: the owner's photo on his running strain, a photo thumbnail, a drawing panel, or the glyph.
  // Decorative in a list (empty alt, no label): the card's title stands right beside it, a screen reader reads it
  // once; the full alt text is on the card itself.
  function media(card) {
    const image = card.image && typeof card.image === "object" ? card.image : {};
    const drawn = () => {
      const d = image.drawing;
      if (!d || typeof d.id !== "string") return glyph(card.kind);
      return artBox("wiki-art", () => {
        const figs = panels(d.id);
        const fig = figs.length >= 3 ? figs[1] : figs[0];
        return fig ? svgPanel(fig, "") : null;
      }, () => glyph(card.kind));
    };
    if (card.current && W.own) {
      const img = el("img", "wiki-img");
      img.alt = "";
      img.loading = "lazy";
      img.src = W.own.src;
      return guard(img, drawn);
    }
    if (image.external) return guard(referenceImg(image.external, card.label || "", true), drawn);
    const img = image.photo ? photoImg(image.photo, true) : null;
    if (img) img.alt = "";
    return img ? guard(img, drawn) : drawn();
  }

  function dayText(iso) {
    const d = new Date(`${iso}T12:00:00`);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString(LOCALE[LANG], { day: "numeric", month: "long", year: "numeric" });
  }

  // The owner's newest daily camera photo (same origin, the cockpit's own route), asked once per opening.
  async function loadOwn() {
    if (W.own !== undefined) return W.own;
    W.own = null;
    try {
      const d = await api("/api/photos");
      const list = d && Array.isArray(d.photos) ? d.photos.filter((x) => /^\d{4}-\d{2}-\d{2}$/.test(x)) : [];
      const last = list[list.length - 1];
      if (last) {
        const path = `/photos/${last}.jpg`;
        W.own = { date: last, src: typeof langUrl === "function" ? langUrl(path) : path };
      }
    } catch (_) { W.own = null; }
    return W.own;
  }

  function link(text, url, cls) {
    const safe = safeUrl(url);
    if (!safe) return el("span", "", text);
    const a = el("a", cls || "wiki-credit-a", text);
    a.href = safe;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = t("wiki.newTab");
    return a;
  }

  // "Foto: Josef Schlaghecken · CC BY-SA 4.0 · Wikimedia Commons, verkleinert", licence and file page as links.
  // A photo that stands for something else (a rain gauge for the DLI, a chickpea's roots for overwatering) says so in
  // a line of its own above the credit: "Vergleichsbild: ...", or "Zum Bild: ..." for a plain remark.
  function credit(photo) {
    const c = photo && photo.credit;
    if (!c) return null;
    const cap = el("figcaption", "wiki-credit");
    const note = textOf(photo.note);
    if (note.trim()) {
      cap.append(el("span", "wiki-credit-note", photo.compare ? t("wiki.credit.compare", { note })
        : t("wiki.credit.note", { note })));
    }
    // an author who is only a Commons user name links to the profile page ("User:Ich")
    const by = el("span", "", c.author_url ? `${t("wiki.credit.photo", { author: "" }).trimEnd()} `
      : t("wiki.credit.photo", { author: textOf(c.author) }));
    if (c.author_url) by.append(link(textOf(c.author), c.author_url));
    cap.append(by, " · ",
      link(textOf(c.license), c.license_url), " · ", link(t("wiki.credit.commons"), c.page_url),
      el("span", "", `, ${t("wiki.credit.resized")}`));
    return cap;
  }

  function drawingCredit() { return el("figcaption", "wiki-credit", t("wiki.credit.drawing")); }

  // The drawing's panels side by side, each with its caption: "Schritt 4" when a step names it, else before/after.
  function panelRow(drawing) {
    const fig = el("figure", "wiki-panels");
    const row = artBox("wiki-panel-row", () => {
      const figs = panels(drawing.id);
      if (!figs.length) return null;
      const wrap = el("div", "wiki-panel-grid");
      wrap.dataset.n = String(figs.length);
      const steps = Array.isArray(drawing.steps) ? drawing.steps : [];
      figs.forEach((f, i) => {
        const cell = el("div", "wiki-panel");
        const svg = svgPanel(f, artAlt([f]));
        if (svg) cell.append(svg);
        const at = steps.find((s) => s && s.fig === f);
        const cap = at ? t("wiki.art.step", { n: at.step }) : figs.length === 3 ? t(PHASE[i]) : "";
        if (cap) cell.append(el("span", "wiki-panel-cap", cap));
        wrap.append(cell);
      });
      return wrap;
    }, null);
    fig.append(row, drawingCredit());
    return fig;
  }

  // The card's picture above the text: the owner's photo, the photo with its credit, or the drawing.
  function hero(d) {
    const image = d.image && typeof d.image === "object" ? d.image : {};
    const box = el("div", "wiki-hero");
    const drawing = image.drawing && typeof image.drawing.id === "string" ? image.drawing : null;
    const fromDrawing = () => (drawing ? panelRow(drawing) : null);
    if (image.external) {
      const fig = el("figure", "wiki-photo wiki-reference-photo");
      const cap = el("figcaption", "wiki-credit");
      const a = el("a", "wiki-src-link", t(image.external.representative ? "wiki.lex.photoRepresentative" : "wiki.lex.photoSource"));
      a.href = image.external.source; a.target = "_blank"; a.rel = "noopener noreferrer"; a.referrerPolicy = "no-referrer";
      cap.append(a, el("span", "", ` · ${t("wiki.lex.photoRights")}`));
      fig.append(guard(referenceImg(image.external, d.label, false), () => el("p", "wiki-muted", t("wiki.lex.photoMissing"))), cap);
      box.append(fig);
      return box;
    }
    if (image.photo) {
      const img = photoImg(image.photo, false);
      if (img) {
        const fig = el("figure", "wiki-photo");
        if (image.photo.w && image.photo.h && image.photo.h > image.photo.w) fig.classList.add("tall");
        fig.append(guard(img, () => { fig.classList.add("wiki-art-none"); return el("span", "", ""); }), credit(image.photo));
        box.append(fig);
        return box;
      }
    }
    if (d.current) {
      const fig = el("figure", "wiki-photo wiki-own");
      const slot = el("div", "wiki-own-slot");
      const shown = fromDrawing();
      slot.append(shown || glyph(d.kind));
      fig.append(slot);
      const cap = el("figcaption", "wiki-credit", t("wiki.credit.drawing"));
      fig.append(cap);
      box.append(fig);
      loadOwn().then((own) => {
        if (!own) { cap.textContent = t("wiki.ownNone"); return; }
        const img = el("img", "wiki-img");
        img.alt = t("wiki.ownPhotoAlt", { date: dayText(own.date) });
        img.src = own.src;
        slot.replaceChildren(guard(img, () => fromDrawing() || glyph(d.kind)));
        fig.classList.add("has-own");
        cap.textContent = t("wiki.ownPhoto", { date: dayText(own.date) });
      });
      return box;
    }
    const drawn = fromDrawing();
    if (drawn) { box.append(drawn); return box; }
    return null;
  }

  // ---------- small helpers ----------

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function safeUrl(url) {
    return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
  }

  function textOf(x) {
    if (x == null) return "";
    if (typeof x === "string") return x;
    if (typeof x === "number" || typeof x === "boolean") return String(x);
    if (typeof x === "object") return textOf(x.text ?? x.label ?? x.name ?? x.title ?? "");
    return "";
  }

  function list(items, ordered) {
    const arr = (Array.isArray(items) ? items : items ? [items] : []).map(textOf).filter((s) => s.trim());
    if (!arr.length) return null;
    const box = el(ordered ? "ol" : "ul", "wiki-list");
    for (const s of arr) box.append(el("li", "", s));
    return box;
  }

  function num(v) {
    return new Intl.NumberFormat(LOCALE[LANG], { maximumFractionDigits: 2, useGrouping: false }).format(v);
  }

  function unitText(unit) {
    if (!unit || typeof unit !== "string") return "";
    return UNIT[unit] ? t(UNIT[unit]) : unit;
  }

  function withUnit(text, unit) {
    const u = unitText(unit);
    return u ? `${text} ${u}` : text;
  }

  // "20 bis 25 °C", "höchstens 85 %", "mindestens 12,8 °C", "74 °F"; a word like "offen" in the data reads "offen".
  function rangeText(lo, hi, unit) {
    const isNum = (v) => typeof v === "number" && Number.isFinite(v);
    if (!isNum(lo) && !isNum(hi)) return (lo != null || hi != null) ? t("wiki.open") : "";
    let text;
    if (isNum(lo) && isNum(hi)) text = lo === hi ? num(lo) : t("wiki.range", { min: num(lo), max: num(hi) });
    else if (isNum(hi)) text = t("wiki.atMost", { v: num(hi) });
    else text = t("wiki.atLeast", { v: num(lo) });
    return withUnit(text, unit);
  }

  function metricName(target) {
    const own = textOf(target && target.label);
    if (own) return own;
    const m = target && target.metric;
    const name = METRIC[m] ? t(METRIC[m]) : String(m || "");
    const when = target && WHEN[target.when] ? t(WHEN[target.when]) : "";
    return when ? `${name}, ${when}` : name;
  }

  function reading(metric) {
    const s = typeof state === "object" && state ? state : null;
    if (!s || !metric) return null;
    const c = s.climate || {};
    const v = metric in c ? c[metric] : s[metric];
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  }

  function liveText(metric) {
    if (!LIVE[metric]) return "";
    const v = reading(metric);
    if (v == null) return t("wiki.live", { value: t("wiki.liveNone") });
    const [digits, unit] = LIVE[metric];
    const value = new Intl.NumberFormat(LOCALE[LANG], { minimumFractionDigits: digits, maximumFractionDigits: digits,
      useGrouping: false }).format(v);
    return t("wiki.live", { value: `${value} ${unit}` });
  }

  function section(titleKey, ...content) {
    const parts = content.flat().filter(Boolean);
    if (!parts.length) return null;
    const box = el("section", "wiki-sec");
    box.append(el("h3", "wiki-h", t(titleKey)), ...parts);
    return box;
  }

  function para(text, cls) {
    const s = textOf(text);
    return s.trim() ? el("p", cls || "wiki-p", s) : null;
  }

  function cardHref(folder, key) {
    return `#wiki/${encodeURIComponent(folder)}/${encodeURIComponent(key)}`;
  }

  // A table that scrolls on its own on a narrow screen, never the page.
  function table(head, rows, cls) {
    const tbl = el("table", `wiki-table${cls ? ` ${cls}` : ""}`);
    const hr = tbl.createTHead().insertRow();
    head.forEach((h, i) => {
      const th = el("th", "", h);
      th.scope = "col";
      if (i) th.className = "c";
      hr.append(th);
    });
    const body = tbl.createTBody();
    for (const row of rows) {
      const tr = body.insertRow();
      row.forEach((cell, i) => {
        const node = el(i ? "td" : "th", i ? "c" : "", cell);
        if (!i) node.scope = "row";
        tr.append(node);
      });
    }
    const wrap = el("div", "wiki-scroll");
    wrap.append(tbl);
    return wrap;
  }

  function targetsTable(targets) {
    const rows = (Array.isArray(targets) ? targets : []).filter((x) => x && typeof x === "object")
      .map((x) => [metricName(x), rangeText(x.min, x.max, x.unit) || t("wiki.open")]);
    return rows.length ? table(["", t("wiki.col.value")], rows, "wiki-targets") : null;
  }

  // ---------- renderers by kind; each returns the nodes between the head and the sources ----------

  function fitBlock(fit) {
    if (!fit || typeof fit !== "object") return null;
    const dl = el("dl", "wiki-dl");
    const add = (labelKey, value) => {
      const s = textOf(value);
      if (!s.trim()) return;
      dl.append(el("dt", "", t(labelKey)), el("dd", "", s));
    };
    if ("height_ok" in fit) {
      const h = fit.height_ok === true ? t("wiki.fit.heightOk") : fit.height_ok === false ? t("wiki.fit.heightNo")
        : t("wiki.fit.heightOpen");
      add("wiki.fit.height", h);
    }
    add("wiki.fit.light", fit.light_note);
    add("wiki.fit.heat", fit.heat_note);
    return dl.childNodes.length ? section("wiki.sec.fit", dl) : null;
  }

  function daysText(from, to) {
    if (typeof from !== "number") return "";
    if (typeof to !== "number" || to >= 365) return t("wiki.daysFrom", { from: num(from) });
    return t("wiki.days", { from: num(from), to: num(to) });
  }

  function cropOrProcess(card) {
    const out = [fitBlock(card.fit_for_vgrow)];
    const stages = Array.isArray(card.stages) ? card.stages : [];
    if (stages.length) {
      const box = el("ol", "wiki-steps");
      for (const st of stages) {
        if (!st || typeof st !== "object") continue;
        const li = el("li", "wiki-step");
        const head = el("div", "wiki-step-h");
        head.append(el("b", "", textOf(st.label) || String(st.key || "")));
        const days = daysText(st.days_from ?? st.from_day, st.days_to ?? st.to_day);
        if (days) head.append(el("span", "wiki-muted", days));
        li.append(head);
        const tbl = targetsTable(st.targets);
        if (tbl) li.append(tbl);
        const notes = para(st.notes, "wiki-p wiki-small");
        if (notes) li.append(notes);
        box.append(li);
      }
      out.push(section("wiki.sec.stages", box));
    }
    const steps = Array.isArray(card.steps) ? card.steps : [];
    if (steps.length) {
      const box = el("ol", "wiki-steps");
      for (const st of steps) {
        if (typeof st === "string") { box.append(el("li", "wiki-step", st)); continue; }
        if (!st || typeof st !== "object") continue;
        const li = el("li", "wiki-step");
        li.append(el("div", "wiki-step-h", textOf(st.label) || String(st.key || "")));
        const tbl = targetsTable(st.conditions || st.targets);
        if (tbl) li.append(tbl);
        const d = st.duration;
        if (d && typeof d === "object") {
          const text = rangeText(d.min, d.max, d.unit);
          if (text) li.append(el("p", "wiki-p wiki-small", t("wiki.duration", { text })));
        }
        const notes = para(st.notes || st.text, "wiki-p wiki-small");
        if (notes) li.append(notes);
        box.append(li);
      }
      out.push(section("wiki.sec.steps", box));
    }
    return out;
  }

  // Numbered step cards: the first sentence in bold when it is short, the drawing panel a step names beside it, and a
  // caveat about the steps ("Vorweg: ...") as a note without a number.
  function methodSteps(card) {
    const steps = (Array.isArray(card.steps) ? card.steps : []).map(textOf);
    if (!steps.some((s) => s.trim())) return null;
    const leads = Array.isArray(card.steps_lead) ? card.steps_lead : [];
    const pre = Array.isArray(card.steps_preface) ? card.steps_preface : [];
    const figs = {};
    const d = W.card && W.card.image && W.card.image.drawing;
    for (const s of d && Array.isArray(d.steps) ? d.steps : []) if (s && typeof s.fig === "string") figs[s.step] = s.fig;
    const out = [];
    const ol = el("ol", "wiki-numsteps");
    let n = 0;
    steps.forEach((text, i) => {
      if (!text.trim()) return;
      const body = el("div", "wiki-ns-body");
      const lead = typeof leads[i] === "string" ? leads[i] : "";
      if (lead && text.startsWith(lead)) {
        body.append(el("b", "wiki-ns-lead", lead));
        const rest = text.slice(lead.length).trim();
        if (rest) body.append(el("p", "wiki-p wiki-small", rest));
      } else body.append(el("p", "wiki-p", text));
      if (pre[i] === true) { const note = el("div", "wiki-preface"); note.append(body); out.push(note); return; }
      n += 1;
      const li = el("li", "wiki-ns");
      li.append(el("span", "wiki-n", String(n)), body);
      const fig = figs[i + 1];
      if (fig) li.append(artBox("wiki-ns-art", () => svgPanel(fig, artAlt([fig])), null));
      ol.append(li);
    });
    out.push(ol);
    return out;
  }

  function method(card) {
    // A string, or {ok|suitable|fits: true/false, text}: "ja: ..." / "nein: ...".
    const yesNo = (v) => {
      if (v && typeof v === "object") {
        const flag = v.ok ?? v.suitable ?? v.fits;
        const word = flag === true ? t("wiki.yes") : flag === false ? t("wiki.no") : "";
        const text = textOf(v);
        return word && text ? `${word}: ${text}` : word || text || null;
      }
      return textOf(v) || null;
    };
    return [
      section("wiki.sec.what", para(card.what)),
      section("wiki.sec.steps", methodSteps(card)),
      section("wiki.sec.when", para(card.when_text ?? card.when)),
      section("wiki.sec.autoflower", para(yesNo(card.autoflower))),
      section("wiki.sec.fit", para(yesNo(card.box_fit))),
      section("wiki.sec.risks", list(card.risks)),
    ];
  }

  function checkList(items) {
    const arr = Array.isArray(items) ? items : items ? [items] : [];
    if (!arr.length) return null;
    const box = el("ul", "wiki-list wiki-checks");
    for (const item of arr) {
      const text = textOf(item);
      if (!text.trim()) continue;
      const li = el("li", "", text);
      const metric = item && typeof item === "object" ? item.metric : null;
      const live = liveText(metric);
      if (live) li.append(el("span", "pill sm info wiki-live", live));
      if (item && typeof item === "object" && (item.min != null || item.max != null)) {
        li.append(el("span", "wiki-muted", ` ${rangeText(item.min, item.max, item.unit)}`));
      }
      box.append(li);
    }
    return box.childNodes.length ? box : null;
  }

  function lookAlikes(items) {
    const arr = Array.isArray(items) ? items : [];
    if (!arr.length) return null;
    const box = el("ul", "wiki-list");
    for (const item of arr) {
      const text = textOf(item);
      if (!text.trim()) continue;
      const li = el("li");
      const folder = item && typeof item === "object" ? item.folder : null;
      const key = item && typeof item === "object" ? item.key : null;
      if (typeof folder === "string" && typeof key === "string") {
        const a = el("a", "", text);
        a.href = cardHref(folder, key);
        li.append(a);
      } else {
        li.textContent = text;
      }
      box.append(li);
    }
    return box;
  }

  function problem(card) {
    return [
      section("wiki.sec.symptoms", list(card.symptoms)),
      section("wiki.sec.causes", list(card.causes)),
      section("wiki.sec.check", checkList(card.check ?? card.checks)),
      section("wiki.sec.doNow", list(card.do_now, true)),
      section("wiki.sec.prevention", list(card.prevention)),
      section("wiki.sec.lookAlikes", lookAlikes(card.look_alikes)),
    ];
  }

  // The text comes first (summary or text, drawn above), then the live value when the box measures the term.
  function term(card) {
    const live = card.relates_to_metric ? liveText(card.relates_to_metric) : "";
    if (!live) return [];
    const p = el("p", "wiki-p");
    p.append(el("span", "pill sm info wiki-live", live));
    return [p];
  }

  function stageCard(card) {
    const stages = (Array.isArray(card.stages) ? card.stages : []).filter((s) => s && typeof s === "object");
    if (!stages.length) return [];
    // One column per target (metric and time of day), in the order the first stage names them.
    const cols = [];
    for (const st of stages) {
      for (const x of Array.isArray(st.targets) ? st.targets : []) {
        if (!x || typeof x !== "object") continue;
        const id = `${x.metric}|${x.when || ""}`;
        if (!cols.some((c) => c.id === id)) cols.push({ id, label: metricName(x), unit: x.unit });
      }
    }
    const head = [t("wiki.col.stage"), t("wiki.col.days"), ...cols.map((c) => c.unit ? `${c.label} (${unitText(c.unit)})` : c.label)];
    const rows = stages.map((st) => {
      const cells = [textOf(st.label) || String(st.key || ""), daysText(st.from_day ?? st.days_from, st.to_day ?? st.days_to)];
      for (const c of cols) {
        const x = (st.targets || []).find((y) => y && `${y.metric}|${y.when || ""}` === c.id);
        cells.push(x ? rangeText(x.min, x.max, "") || t("wiki.open") : "–");
      }
      return cells;
    });
    const out = [section("wiki.sec.stageTable", table(head, rows, "wiki-stages"))];
    const presets = el("div", "wiki-presets");
    for (const st of stages) {
      const lines = [];
      const p = st.preset;
      if (p && typeof p === "object") {
        lines.push(t("wiki.stage.preset", { light: p.light_percent ?? "–",
          spectrum: SPECTRUM_KEY[p.spectrum] ? t(SPECTRUM_KEY[p.spectrum]) : String(p.spectrum ?? "–"), fan: p.cfan_step ?? "–" }));
      }
      const w = st.watering;
      if (w && typeof w === "object") {
        lines.push(t("wiki.stage.watering", { minutes: w.pump_minutes ?? "–", ml: w.flow_ml ?? "–", start: textOf(w.start) }));
      }
      if (textOf(st.preset_note)) lines.push(textOf(st.preset_note));
      if (!lines.length) continue;
      const block = el("div", "wiki-preset");
      block.append(el("b", "", textOf(st.label) || String(st.key || "")));
      for (const line of lines) block.append(el("p", "wiki-p wiki-small", line));
      presets.append(block);
    }
    if (presets.childNodes.length) {
      const det = el("details", "wiki-more");
      det.append(el("summary", "", t("wiki.sec.presets")), presets);
      out.push(det);
    }
    return out;
  }

  // "(B48)" or "(S37); Zamnesia 90 cm (S40)": a value whose text ends in its source id(s) gets them as a line below
  const TRAIL_IDS = /\s*\(((?:[A-Z]{1,2}\d{1,3}(?:, )?)+)\)\s*$/;

  function strain(card) {
    const facts = card.facts && typeof card.facts === "object" && !Array.isArray(card.facts) ? card.facts : {};
    const labels = card.fact_labels && typeof card.fact_labels === "object" ? card.fact_labels : {};
    const lex = W.card && W.card.lexicon && typeof W.card.lexicon === "object" ? W.card.lexicon : null;
    const note = lex ? textOf(lex.note) : "";
    // cockpit tiles: the label small and grey, the value large; in the lexicon the source id and "Herstellerangabe,
    // ungeprüft" stand as a small line under the value, otherwise the marker stays with the value
    const grid = el("div", "wiki-facts");
    for (const [k, v] of Object.entries(facts)) {
      const text = textOf(v);
      if (!text.trim() || (lex && k === "brand")) continue;
      const tile = el("div", "wiki-fact");
      const m = lex ? text.match(TRAIL_IDS) : null;
      tile.append(el("span", "wiki-fact-k", textOf(labels[k]) || k), el("span", "wiki-fact-v", m ? text.slice(0, m.index) : text));
      if (m) {
        const src = t("wiki.strains.source", { id: m[1] });
        tile.append(el("span", "wiki-fact-src", note ? `${src} · ${note}` : src));
      }
      grid.append(tile);
    }
    return [lex ? boxBlock(lex) : null, grid.childNodes.length ? section("wiki.sec.facts", grid) : null];
  }

  // "In der VGrow": the strain's group against the box height, in words, with the box's own source.
  function boxBlock(lex) {
    const fit = FIT[lex.fit] ? lex.fit : "unknown";
    const head = el("div", "wiki-boxfit");
    const pill = el("span", `pill sm ${FIT_PILL[fit]} wiki-fitpill`.trim(), t(FIT[fit]));
    if (lex.flowering && FLOWERING[lex.flowering]) head.append(pill, el("span", "pill sm", t(FLOWERING[lex.flowering])));
    else head.append(pill);
    if (lex.brand) head.append(el("span", "wiki-muted", lex.brand));
    const h = Array.isArray(lex.height) ? lex.height : null;
    const cm = typeof lex.box_cm === "number" ? num(lex.box_cm) : "–";
    const text = !h ? t("wiki.strainFit.none", { cm }) : t(FIT_TEXT[fit], { h: rangeText(h[0], h[1], "cm"), cm });
    const src = lex.box_source && lex.box_source.id ? t("wiki.strains.source", { id: lex.box_source.id }) : "";
    const p = el("p", "wiki-p", text);
    const out = [head, p];
    if (src) out.push(el("p", "wiki-p wiki-small wiki-muted", `${t("wiki.strains.boxHeight", { cm })} ${src}`));
    const box = section("wiki.sec.box", ...out);
    if (box) box.classList.add(`wiki-fit-${fit}`);
    return box;
  }

  function nutrient(card) {
    const out = [];
    const weeks = card.weeks && typeof card.weeks === "object" ? card.weeks : {};
    const cols = Array.isArray(weeks.columns) ? weeks.columns : [];
    const comps = card.components && typeof card.components === "object" ? card.components : {};
    const pages = card.pages && typeof card.pages === "object" ? card.pages : {};
    const unit = textOf(card.unit);
    for (const page of Object.values(pages)) {
      if (!page || typeof page !== "object" || !page.rows) continue;
      const head = [t("wiki.col.product"), ...cols.map((c) => textOf(c.label) || String(c.key || ""))];
      const rows = Object.entries(page.rows).map(([key, values]) => {
        const comp = comps[key] || {};
        const name = textOf(comp.label) || key;
        const label = comp.optional ? t("wiki.optional", { name }) : name;
        return [label, ...cols.map((_, i) => {
          const v = Array.isArray(values) ? values[i] : null;
          return typeof v === "number" ? num(v) : "–";
        })];
      });
      const box = el("div", "wiki-chart");
      box.append(el("h4", "wiki-h4", textOf(page.label)), table(head, rows, "wiki-feed"));
      out.push(box);
    }
    const after = [weeks.water, weeks.harvest, weeks.end].map(textOf).filter(Boolean);
    const sec = section("wiki.sec.chart", out, list(after));
    // the unit belongs in the heading: "Düngeschema in ml/l"
    if (sec) {
      sec.querySelector(".wiki-h").textContent = t("wiki.sec.chart", { unit });
      if (textOf(card.printed_unit)) sec.append(el("p", "wiki-p wiki-small wiki-muted", t("wiki.fert.printed", { unit: textOf(card.printed_unit) })));
    }
    const line = card.line && typeof card.line === "object" ? card.line : null;
    return [line ? lineBlock(line) : null, sec, boosterBlock(card.boosters, cols, pages),
      line ? section("wiki.sec.fplan", srcList(line.plan, true, (x) => `${textOf(x.when)}: ${textOf(x.text)}`)) : null,
      section("wiki.sec.frequency", para(card.frequency)),
      line ? section("wiki.sec.cautions", srcList(line.cautions)) : null];
  }

  // A list of {text, sources} items: the text, its source ids small behind it (the ids stand under "Quellen").
  function srcList(items, ordered, textFn) {
    const arr = (Array.isArray(items) ? items : []).filter((x) => x && typeof x === "object");
    if (!arr.length) return null;
    const box = el(ordered ? "ol" : "ul", "wiki-list");
    for (const x of arr) {
      const text = textFn ? textFn(x) : textOf(x);
      if (!text.trim()) continue;
      const li = el("li", "", text);
      const ids = (Array.isArray(x.sources) ? x.sources : []).filter((s) => typeof s === "string");
      if (ids.length) li.append(el("span", "wiki-muted wiki-sids", ` (${ids.join(", ")})`));
      box.append(li);
    }
    return box.childNodes.length ? box : null;
  }

  // The line of a fertilizer card: its type and medium as pills, the products with their roles, how it is applied.
  function lineBlock(line) {
    const out = [];
    const pills = el("div", "wiki-badges wiki-fpills");
    for (const k of Array.isArray(line.types) ? line.types : []) if (FTYPE[k]) pills.append(el("span", "pill sm info", t(FTYPE[k])));
    for (const m of Array.isArray(line.media) ? line.media : []) if (FMEDIA[m]) pills.append(el("span", "pill sm", t(FMEDIA[m])));
    if (pills.childNodes.length) out.push(pills);
    const media = para(line.media_text, "wiki-p");
    if (media) out.push(media);
    const products = srcList(line.products, false, (x) => {
      const name = textOf(x.product);
      const npk = textOf(x.npk) ? ` (NPK ${textOf(x.npk)})` : "";
      return `${name}${npk}: ${textOf(x.role)}`;
    });
    if (products) out.push(el("h4", "wiki-h4", t("wiki.fert.products")), products);
    const how = para(line.application, "wiki-p");
    if (how) {
      out.push(el("h4", "wiki-h4", t("wiki.fert.applied")), how);
      const ids = (Array.isArray(line.application_sources) ? line.application_sources : []).filter((s) => typeof s === "string");
      if (ids.length) how.append(el("span", "wiki-muted wiki-sids", ` (${ids.join(", ")})`));
    }
    const sec = section("wiki.sec.line", out);
    if (sec && textOf(line.maker)) sec.querySelector(".wiki-h").textContent = t("wiki.sec.lineBy", { maker: textOf(line.maker) });
    return sec;
  }

  // The chart's other rows (BioBizz boosters): for reading only, one table per page, each row in its own unit.
  function boosterBlock(b, cols, cardPages) {
    if (!b || typeof b !== "object" || !Array.isArray(b.rows) || !b.rows.length) return null;
    const pages = [...new Set(b.rows.map((r) => r && r.page).filter(Boolean))];
    const head = [t("wiki.col.product"), ...cols.map((c) => textOf(c.label) || String(c.key || ""))];
    const out = [para(b.text, "wiki-p wiki-small wiki-muted")];
    for (const page of pages) {
      const rows = b.rows.filter((r) => r && r.page === page).map((r) => [`${textOf(r.product)} (${textOf(r.unit)})`,
        ...cols.map((_, i) => { const v = Array.isArray(r.values) ? r.values[i] : null; return typeof v === "number" ? num(v) : "–"; })]);
      const box = el("div", "wiki-chart");
      box.append(el("h4", "wiki-h4", textOf((cardPages || {})[page] && cardPages[page].label) || page), table(head, rows, "wiki-feed"));
      out.push(box);
    }
    return section("wiki.sec.boosters", out);
  }

  // The overview of the fertilizer types: per type what it suits, how it is applied, the usual mistakes, the lines
  // of the wiki that are of this type; then the market facts, each with its source ids.
  function fertilizerTypes(card) {
    const out = [];
    for (const ty of Array.isArray(card.types) ? card.types : []) {
      if (!ty || typeof ty !== "object") continue;
      const box = el("section", "wiki-sec wiki-ftype");
      box.append(el("h3", "wiki-h", textOf(ty.label)));
      const ids = (Array.isArray(ty.sources) ? ty.sources : []).join(", ");
      for (const [key, field] of [["wiki.fert.suits", "suits"], ["wiki.fert.applied", "applied"]]) {
        const p = para(ty[field], "wiki-p");
        if (!p) continue;
        box.append(el("h4", "wiki-h4", t(key)), p);
      }
      const mistakes = srcList(ty.mistakes);
      if (mistakes) box.append(el("h4", "wiki-h4", t("wiki.fert.mistakes")), mistakes);
      const ex = (Array.isArray(ty.examples) ? ty.examples : []).filter((k) => typeof k === "string");
      if (ex.length) {
        const p = el("p", "wiki-p wiki-small");
        p.append(el("span", "wiki-muted", `${t("wiki.fert.examples")} `));
        ex.forEach((k, i) => {
          const a = el("a", "", (W.fert && (W.fert.lines || []).find((r) => r.key === k) || {}).label || k);
          a.href = cardHref("nutrients", k);
          if (i) p.append(", ");
          p.append(a);
        });
        box.append(p);
      }
      if (ids) box.append(el("p", "wiki-p wiki-small wiki-muted", t("wiki.fert.sources", { ids })));
      out.push(box);
    }
    out.push(section("wiki.sec.market", srcList(card.market)));
    return out;
  }

  function legal(card) {
    return [section("wiki.sec.rules", list(card.facts))];
  }

  function generic(card) {
    const out = [];
    const facts = card.facts;
    if (Array.isArray(facts)) out.push(section("wiki.sec.details", list(facts)));
    else if (facts && typeof facts === "object") {
      const dl = el("dl", "wiki-dl");
      for (const [k, v] of Object.entries(facts)) if (textOf(v)) dl.append(el("dt", "", card.fact_labels?.[k] || k), el("dd", "", textOf(v)));
      if (dl.childNodes.length) out.push(section("wiki.sec.details", dl));
    }
    return out;
  }

  const RENDER = { variety: varietyProfile, plant_condition: generic, crop: cropOrProcess, process: cropOrProcess, method, problem, term, stage: stageCard, strain,
    nutrient, legal, fertilizer_types: fertilizerTypes };

  // ---------- the view ----------

  function closeIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M15 5l-7 7 7 7");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
    return svg;
  }

  function build() {
    const view = el("section", "wiki-view");
    view.id = "wiki-view";
    view.hidden = true;
    view.setAttribute("aria-labelledby", "wiki-title");
    const main = document.querySelector("main");
    if (!main) return false;
    main.prepend(view);
    Object.assign(W, { built: true, view });
    return true;
  }

  function headBar(backKey, onBack) {
    const bar = el("div", "wiki-bar");
    const back = el("button", "wiki-back");
    back.type = "button";
    back.append(closeIcon(), el("span", "", t(backKey)));
    back.addEventListener("click", onBack);
    bar.append(back);
    return bar;
  }

  function toCockpit() {
    window.history.pushState("", document.title, location.pathname + location.search);
    route();
    window.scrollTo(0, 0);
  }

  function toList() {
    if (W.from === "list" || W.from === "sorten" || W.from === "duenger" || W.from === "topic" || W.from === "varieties") window.history.back();
    else if (W.card && W.card.folder === "varieties") location.hash = "#wiki/varieties";
    else if (W.card && W.card.category === "sorten") location.hash = "#wiki/sorten";
    else if (W.card && W.card.category === "duenger") location.hash = "#wiki/duenger";
    else location.hash = "#wiki";
  }

  function packageSwitch(index) {
    const row = el("div", "wiki-pkg");
    const label = el("label", "wiki-switch");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !!(index.packages && index.packages[PACKAGE]);
    box.addEventListener("change", async () => {
      box.disabled = true;
      try {
        await api("/api/wiki/packages", { package: PACKAGE, on: box.checked });
        W.results = null;
        W.varieties = null;
        W.strains = null;
        await loadIndex();
        if (W.query) await runSearch(W.query);
      } catch (err) {
        W.error = (err && err.message) || t("wiki.error");
      } finally {
        box.disabled = false;
        renderList();
      }
    });
    label.append(box, el("span", "", t("wiki.package")));
    row.append(label);
    const hint = index.hidden ? tn("wiki.hiddenCards", index.hidden) : t("wiki.packageHint");
    row.append(el("small", "wiki-muted", hint));
    return row;
  }

  // A card as a picture tile (big categories) or a compact row with a small picture (glossary, feeding, law).
  // No kind or draft pill on every row any more: the category heading says the kind, one line says "all drafts".
  function cardLink(card, extra, row) {
    const a = el("a", row ? "wiki-row" : "wiki-tile");
    a.href = cardHref(card.folder, card.key);
    a.dataset.from = "list";
    if (card.current) a.classList.add("is-current");
    const pic = el("span", "wiki-media");
    pic.append(media(card));
    const text = el("span", "wiki-tile-text");
    text.append(el("span", "wiki-link-t", card.label));
    if (card.blurb) text.append(el("span", "wiki-blurb", card.blurb));
    const meta = el("span", "wiki-link-m");
    if (card.current) meta.append(el("span", "pill sm ok", t("wiki.current")));
    if (card.status === "draft" && !(W.index && W.index.drafts && W.index.drafts === W.index.count)) {
      meta.append(el("span", "wiki-draft", t("wiki.draft")));
    }
    if (extra) meta.append(...extra);
    if (meta.childNodes.length) text.append(meta);
    a.append(pic, text);
    return a;
  }

  function renderResults(box) {
    const r = W.results;
    if (!r) { box.append(el("p", "wiki-muted", t("wiki.loading"))); return; }
    const items = Array.isArray(r.results) ? r.results : [];
    if (!items.length) { box.append(el("p", "wiki-empty", t("wiki.empty", { q: W.query }))); return; }
    box.append(el("p", "wiki-muted", tn("wiki.results", items.length)));
    const ul = el("ul", "wiki-rows wiki-results");
    for (const hit of items) {
      const extra = [];
      if (CAT[hit.category]) extra.push(el("span", "wiki-cat", t(CAT[hit.category])));
      if (hit.typo) extra.push(el("span", "wiki-typo", t("wiki.typo")));
      const li = el("li");
      const a = cardLink({ ...hit, blurb: "" }, extra, true);
      if (hit.snippet) a.querySelector(".wiki-tile-text").append(el("span", "wiki-snip", hit.snippet));
      li.append(a);
      ul.append(li);
    }
    box.append(ul);
  }

  function renderList() {
    if (!W.built) return;
    const view = W.view;
    const keepFocus = document.activeElement && document.activeElement.classList.contains("wiki-q");
    const caret = keepFocus ? document.activeElement.selectionStart : null;
    view.replaceChildren();
    const top = el("div", "wiki-top");
    const title = el("h2", "wiki-title", t("wiki.title"));
    title.id = "wiki-title";
    title.tabIndex = -1;
    const shut = el("button", "wiki-back");
    shut.type = "button";
    shut.append(closeIcon(), el("span", "", t("wiki.close")));
    shut.addEventListener("click", toCockpit);
    top.append(shut, title);
    view.append(top, el("p", "wiki-explain", t("wiki.explain")));

    const form = el("form", "wiki-search");
    form.setAttribute("role", "search");
    const q = el("input", "wiki-q");
    q.type = "search";
    q.value = W.query;
    q.placeholder = t("wiki.search");
    q.setAttribute("aria-label", t("wiki.searchLabel"));
    q.autocomplete = "off";
    q.setAttribute("enterkeyhint", "search");
    q.addEventListener("input", () => {
      W.query = q.value;
      clearTimeout(W.timer);
      W.timer = setTimeout(() => runSearch(W.query).then(renderBody), 220);
    });
    form.addEventListener("submit", (e) => { e.preventDefault(); clearTimeout(W.timer); runSearch(q.value).then(renderBody); });
    form.append(q);
    view.append(form);
    const body = el("div", "wiki-body");
    view.append(body);
    W.body = body;
    renderBody();
    if (keepFocus) { q.focus({ preventScroll: true }); if (caret != null) q.setSelectionRange(caret, caret); }
  }

  // A group's heading inside a category's list: its name, how many cards, and a short line what it holds. A card
  // without a known group gets the plain "more" heading, never a raw id.
  function groupHead(group, n) {
    const li = el("li", "wiki-group");
    const h = el("h4", "wiki-group-h");
    h.append(el("span", "wiki-group-name", t(GROUP[group] || "wiki.group.more")), el("small", "", tn("wiki.cards", n)));
    li.append(h);
    const hint = GROUP_HINT[group];
    if (hint) li.append(el("p", "wiki-group-hint", t(hint)));
    return li;
  }

  function jumpTo(target) {
    if (!target) return;
    const heading = target.querySelector("h3") || target;
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
    const reduced = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ behavior: reduced ? "instant" : "smooth", block: "start" });
  }

  function backToIndex() {
    const button = el("button", "wiki-index-back", t("wiki.backIndex"));
    button.type = "button";
    button.addEventListener("click", () => jumpTo(document.getElementById("wiki-index")));
    return button;
  }

  // A visible directory: category names disclose article links, and the two large catalogues open directly.
  // Keep disclosure state when photos, language or browser Back redraw the list.
  function topicIndex(cats, areas) {
    const entries = [...cats];
    for (const area of areas) {
      const after = area.id === "sorten" ? "cannabis" : (cats.some((c) => c.id === "methoden") ? "methoden" : cats.at(-1)?.id);
      const at = entries.findIndex((c) => c.id === after);
      entries.splice(at < 0 ? entries.length : at + 1, 0, area);
    }
    if (!entries.length) return null;
    const nav = el("nav", "wiki-index");
    nav.id = "wiki-index";
    nav.setAttribute("aria-labelledby", "wiki-index-title");
    const title = el("h3", "wiki-index-title", t("wiki.index"));
    title.id = "wiki-index-title";
    nav.append(title, el("p", "wiki-muted", t("wiki.indexHint")));
    const grid = el("div", "wiki-index-grid");
    for (const entry of entries) {
      const name = CAT[entry.id] ? t(CAT[entry.id]) : String(entry.id);
      if (!Array.isArray(entry.cards)) {
        const a = el("a", "wiki-index-area");
        a.href = `#wiki/${entry.id}`;
        a.dataset.from = "list";
        a.append(el("span", "", name), el("small", "", num(entry.count)));
        grid.append(a);
        continue;
      }
      const detail = el("details", "wiki-index-category");
      detail.dataset.cat = entry.id;
      detail.open = W.indexOpen.has(entry.id);
      detail.addEventListener("toggle", () => {
        if (detail.isConnected) detail.open ? W.indexOpen.add(entry.id) : W.indexOpen.delete(entry.id);
      });
      const summary = el("summary");
      summary.append(el("span", "", name), el("small", "", num(entry.cards.length)));
      detail.append(summary);
      const content = el("div", "wiki-index-articles");
      const browse = el("button", "wiki-index-browse", t("wiki.browseCategory"));
      browse.type = "button";
      browse.addEventListener("click", () => jumpTo(document.getElementById(`wiki-cat-${entry.id}`)));
      content.append(browse);
      const grouped = entry.cards.some((c) => GROUP[c.group]);
      const groups = grouped ? [...new Set(entry.cards.map((c) => c.group))] : [null];
      for (const group of groups) {
        if (grouped) content.append(el("h4", "wiki-index-group", t(GROUP[group] || "wiki.group.more")));
        const list = el("ul", "wiki-index-links");
        const cards = entry.cards.filter((c) => !grouped || c.group === group)
          .sort((a, b) => a.label.localeCompare(b.label, LOCALE[LANG]));
        for (const card of cards) {
          const li = el("li");
          const a = el("a", "", card.label);
          a.href = cardHref(card.folder, card.key);
          a.dataset.from = "list";
          li.append(a);
          list.append(li);
        }
        content.append(list);
      }
      detail.append(content);
      grid.append(detail);
    }
    nav.append(grid);
    return nav;
  }

  function pageContents(nav, sections) {
    if (sections.length < 2) return;
    nav.hidden = false;
    nav.setAttribute("aria-labelledby", "wiki-contents-title");
    const title = el("h3", "wiki-index-title", t("wiki.contents"));
    title.id = "wiki-contents-title";
    const list = el("ol", "wiki-contents-list");
    nav.append(title, list);
    sections.forEach((section, i) => {
      const heading = section.querySelector(".wiki-h");
      if (!heading) return;
      section.id = `wiki-section-${i + 1}`;
      const li = el("li");
      const button = el("button", "", heading.textContent);
      button.type = "button";
      button.addEventListener("click", () => jumpTo(section));
      li.append(button);
      list.append(li);
    });
  }

  // "Deine Pflanze": the running strain on top, with the owner's newest camera photo once it is there.
  function featured(card) {
    const a = el("a", "wiki-feature");
    a.href = cardHref(card.folder, card.key);
    a.dataset.from = "list";
    const pic = el("span", "wiki-media");
    pic.append(media(card));
    const text = el("span", "wiki-tile-text");
    const k = el("span", "wiki-feature-k");
    k.append(el("span", "", t("wiki.yourPlant")), el("span", "pill sm ok", t("wiki.current")));
    text.append(k, el("span", "wiki-feature-t", card.label));
    if (card.blurb) text.append(el("span", "wiki-blurb", card.blurb));
    const when = el("span", "wiki-muted wiki-feature-when", W.own ? t("wiki.ownPhoto", { date: dayText(W.own.date) })
      : t("wiki.credit.drawing"));
    text.append(when);
    a.append(pic, text);
    if (W.own === undefined) {
      loadOwn().then((own) => { if (own && a.isConnected && !W.query.trim()) renderBody(); });
    }
    return a;
  }

  // One directory, one destination for every article. Health groups are not repeated under Cannabis.
  function libraryTopics(index) {
    const cards = (index.categories || []).flatMap((category) => category.cards || []);
    const claimed = new Set();
    const out = [];
    const add = (id, label, description, matches) => {
      const rows = cards.filter((row) => !claimed.has(`${row.folder}/${row.key}`) && matches(row));
      rows.forEach((row) => claimed.add(`${row.folder}/${row.key}`));
      if (rows.length) out.push({ id, label, description, cards: rows, count: rows.length, href: `#wiki/topic/${id}` });
    };
    add("nutrient-issues", "wiki.group.naehrstoffe", "wiki.lex.nutrientIntro", (c) => c.group === "naehrstoffe");
    add("pests", "wiki.group.schaedlinge", "wiki.lex.pestIntro", (c) => c.group === "schaedlinge");
    add("diseases", "wiki.group.krankheiten", "wiki.lex.diseaseIntro", (c) => c.group === "krankheiten");
    add("stress", "wiki.group.stress", "wiki.lex.stressIntro", (c) => c.group === "stress");
    add("plants", "wiki.cat.pflanzen", "wiki.lex.plantIntro", (c) => c.folder === "crops");
    add("background", "wiki.lex.background", "wiki.lex.backgroundIntro", (c) => c.folder === "stages" || c.folder === "strains");
    add("processes", "wiki.cat.vorgaenge", "wiki.lex.processIntro", (c) => c.folder === "processes");
    add("methods", "wiki.cat.methoden", "wiki.lex.methodIntro", (c) => c.folder === "methods");
    add("glossary", "wiki.cat.glossar", "wiki.lex.glossaryIntro", (c) => c.folder === "glossary");
    add("law", "wiki.cat.recht", "wiki.lex.lawIntro", (c) => c.folder === "legal");
    add("other", "wiki.cat.weitere", "wiki.lex.otherIntro", (c) => !claimed.has(`${c.folder}/${c.key}`));
    const areas = index.areas || [];
    const strains = areas.find((a) => a.id === "varieties") || areas.find((a) => a.id === "sorten");
    if (strains) out.unshift({ id: "varieties", label: "wiki.lex.varietyTitle", description: "wiki.lex.varietyIntro",
      count: strains.count, href: `#wiki/${strains.id}`, featured: true });
    const nutrients = areas.find((a) => a.id === "duenger");
    if (nutrients) out.push({ id: "fertilizers", label: "wiki.cat.duenger", description: "wiki.lex.fertilizerIntro",
      count: nutrients.count, href: "#wiki/duenger" });
    return out;
  }

  function breadcrumb(label, href) {
    const nav = el("nav", "wiki-breadcrumb");
    nav.setAttribute("aria-label", t("wiki.lex.breadcrumb"));
    const home = el("a", "", t("wiki.title")); home.href = "#wiki";
    nav.append(home);
    if (label) {
      nav.append(el("span", "", "/"));
      if (href) { const a = el("a", "", label); a.href = href; nav.append(a); }
      else nav.append(el("span", "", label));
    }
    return nav;
  }

  function articleParent(d) {
    if (d.folder === "varieties") return { label: "wiki.lex.varietyTitle", href: "#wiki/varieties" };
    if (d.folder === "nutrients") return { label: "wiki.cat.duenger", href: "#wiki/duenger" };
    if (d.category === "sorten") return { label: "wiki.cat.sorten", href: "#wiki/sorten" };
    const groups = { naehrstoffe: "nutrient-issues", schaedlinge: "pests", krankheiten: "diseases", stress: "stress" };
    const group = d.card?.group;
    if (groups[group]) return { label: GROUP[group], href: `#wiki/topic/${groups[group]}` };
    const folders = { crops: ["wiki.cat.pflanzen", "plants"], methods: ["wiki.cat.methoden", "methods"],
      processes: ["wiki.cat.vorgaenge", "processes"], glossary: ["wiki.cat.glossar", "glossary"],
      legal: ["wiki.cat.recht", "law"], stages: ["wiki.lex.background", "background"] };
    const found = folders[d.folder];
    return found ? { label: found[0], href: `#wiki/topic/${found[1]}` } : null;
  }

  function renderBody() {
    const body = W.body;
    if (!body || !body.isConnected) return;
    body.replaceChildren();
    if (W.error) body.append(el("p", "wiki-error", W.error));
    if (W.query.trim()) { renderResults(body); return; }
    if (!W.index) { body.append(el("p", "wiki-muted", t("wiki.loading"))); return; }
    const topics = libraryTopics(W.index);
    const nav = el("nav", "wiki-directory"); nav.id = "wiki-index";
    nav.setAttribute("aria-label", t("wiki.index"));
    const grid = el("div", "wiki-directory-grid");
    for (const topic of topics) {
      const a = el("a", `wiki-topic${topic.featured ? " wiki-topic-featured" : ""}`);
      a.href = topic.href; a.dataset.topic = topic.id;
      const line = el("div", "wiki-topic-heading");
      line.append(el("h3", "", t(topic.label)), el("span", "wiki-topic-count", num(topic.count)));
      a.append(line, el("p", "", t(topic.description)), el("span", "wiki-topic-open", t("wiki.lex.browse")));
      grid.append(a);
    }
    nav.append(grid); body.append(nav, packageSwitch(W.index));
    window.WikiTools?.home(body, W.index);
  }

  function renderTopic() {
    const view = W.view;
    const topic = W.index && libraryTopics(W.index).find((row) => row.id === W.topic);
    view.replaceChildren(headBar("wiki.back", () => { location.hash = "#wiki"; }));
    if (!topic) { view.append(el("p", "wiki-empty", W.error || t("wiki.lex.topicMissing"))); return; }
    view.append(breadcrumb(t(topic.label)));
    const title = el("h2", "wiki-title", t(topic.label)); title.id = "wiki-title"; title.tabIndex = -1;
    view.append(title, el("p", "wiki-explain", t(topic.description)));
    const q = el("input", "wiki-q"); q.type = "search"; q.value = W.topicQueries[topic.id] || "";
    q.placeholder = t("wiki.lex.topicSearch"); q.setAttribute("aria-label", t("wiki.lex.topicSearch"));
    const results = el("div", "wiki-topic-results");
    const draw = () => {
      const query = foldText(q.value).trim(); W.topicQueries[topic.id] = q.value;
      const rows = topic.cards.filter((c) => foldText(`${c.label} ${c.blurb || ""}`).includes(query))
        .sort((a, b) => a.label.localeCompare(b.label, LOCALE[LANG]));
      results.replaceChildren(el("p", "wiki-muted", t("wiki.lex.shown", { shown: rows.length, total: topic.count })));
      const grid = el("ul", "wiki-tiles wiki-topic-articles");
      for (const row of rows) { const li = el("li"); const a = cardLink(row); a.dataset.from = "topic"; li.append(a); grid.append(li); }
      results.append(grid);
      window.WikiTools?.catalogue(grid, rows);
      if (!rows.length) results.append(el("p", "wiki-empty", t("wiki.lex.noMatches")));
    };
    q.addEventListener("input", draw); view.append(q, results); draw();
  }

  function referenceImg(photo, name, decorative) {
    const img = el("img", "wiki-img");
    img.alt = decorative ? "" : t(photo.representative ? "wiki.lex.representativeAlt" : "wiki.lex.photoAlt", { name });
    img.loading = "lazy"; img.decoding = "async"; img.referrerPolicy = "no-referrer"; img.src = photo.src;
    if (photo.large) { img.srcset = `${photo.src} 640w, ${photo.large} 1280w`; img.sizes = "(min-width: 760px) 50vw, 100vw"; }
    return img;
  }

  const CLASSIFICATION = { indica: "wiki.lex.indica", sativa: "wiki.lex.sativa", hybrid: "wiki.lex.hybrid", unknown: "wiki.lex.unknown" };

  function profileFacts(card) {
    const r = card.reference || {};
    const dl = el("dl", "wiki-dl wiki-profile-facts");
    for (const [field, label, value] of [["classification", "wiki.lex.classification", t(CLASSIFICATION[r.classification] || "wiki.lex.unknown")],
      ["thc_percent", "wiki.lex.thc", typeof r.thc_percent === "number" ? `${num(r.thc_percent)}%` : t("wiki.lex.unknown")],
      ["ratio", "wiki.lex.ratio", t("wiki.lex.ratioUnknown")],
      ["parents", "wiki.lex.parents", (r.parents || []).join(" × ") || t("wiki.lex.unknown")],
      ["aliases", "wiki.lex.aliases", (r.aliases || []).join(", ") || t("wiki.lex.unknown")],
      ["terpenes", "wiki.lex.terpenes", (r.terpenes || []).join(", ") || t("wiki.lex.unknown")],
      ["descendants", "wiki.lex.descendants", (r.descendants || []).join(", ") || t("wiki.lex.unknown")]]) {
      const cell = el("dd", "", value); cell.dataset.fact = field;
      dl.append(el("dt", "", t(label)), cell);
    }
    return dl;
  }

  // Owner, 01.11.2026: "it's extremely important for the people to understand whether this is something for them to go
  // for next time or not". The verdict and its reasons are the server's codes (wiki.grow_view); the page words them.
  const VERDICT = {
    good: { title: "wiki.lex.verdict.good", why: "wiki.lex.verdictWhy.good", short: "wiki.lex.verdictShort.good" },
    fine: { title: "wiki.lex.verdict.fine", why: "wiki.lex.verdictWhy.fine", short: "wiki.lex.verdictShort.fine" },
    catch: { title: "wiki.lex.verdict.catch", why: "wiki.lex.verdictWhy.catch", short: "wiki.lex.verdictShort.catch" },
    later: { title: "wiki.lex.verdict.later", why: "wiki.lex.verdictWhy.later", short: "wiki.lex.verdictShort.later" },
    partial: { title: "wiki.lex.verdict.partial", why: "wiki.lex.verdictWhy.partial", short: "wiki.lex.verdictShort.partial" },
    partial_fine: { title: "wiki.lex.verdict.partial_fine", why: "wiki.lex.verdictWhy.partial_fine", short: "wiki.lex.verdictShort.partial_fine" },
    partial_catch: { title: "wiki.lex.verdict.partial_catch", why: "wiki.lex.verdictWhy.partial_catch", short: "wiki.lex.verdictShort.partial_catch" },
    none: { title: "wiki.lex.verdict.none", why: "wiki.lex.verdictWhy.none", short: "wiki.lex.verdictShort.none" } };
  const VERDICTS = ["good", "fine", "catch", "later", "partial", "partial_fine", "partial_catch"];
  const REASON = { pro: { easy: "wiki.lex.pro.easy", short: "wiki.lex.pro.short", fast: "wiki.lex.pro.fast", high_yield: "wiki.lex.pro.high_yield" },
    con: { difficult: "wiki.lex.con.difficult", slow: "wiki.lex.con.slow", low_yield: "wiki.lex.con.low_yield" } };
  const ERA = { early: "wiki.lex.era.early", mid: "wiki.lex.era.mid", late: "wiki.lex.era.late", whole: "wiki.lex.era.whole" };
  const LEVEL = { easy: "wiki.lex.level.easy", moderate: "wiki.lex.level.moderate", difficult: "wiki.lex.level.difficult",
    short: "wiki.lex.level.short", average: "wiki.lex.level.average", tall: "wiki.lex.level.tall",
    low: "wiki.lex.level.low", medium: "wiki.lex.level.medium", high: "wiki.lex.level.high" };
  // Owner, 01.11.2026: "we need to say, hey, this is a very large breed. This breed is extremely good for boxes".
  const GLANCE = { short: "wiki.lex.glance.short", average: "wiki.lex.glance.average", tall: "wiki.lex.glance.tall",
    easy: "wiki.lex.glance.easy", moderate: "wiki.lex.glance.moderate", difficult: "wiki.lex.glance.difficult" };
  const POPULAR = { top: "wiki.lex.pop.top", very: "wiki.lex.pop.very", known: "wiki.lex.pop.known",
    uncommon: "wiki.lex.pop.uncommon", rare: "wiki.lex.pop.rare" };

  // Who says it: "Leafly", the maker, or the maker by way of SeedFinder.
  function sourceName(o) {
    return o.via === "seedfinder" ? t("wiki.lex.viaSeedfinder", { maker: o.by }) : o.by;
  }

  // A score's figure as its source gives it: centimetres, grams, days, or a step in words.
  function factText(g, field) {
    const o = g.from?.[field];
    if (!o) return null;
    const who = sourceName(o);
    // "up to 900", "63 days", "60 to 75 days"
    const span = ([lo, hi], one, upto, range) => o.upto ? t(upto, { who, hi }) : lo === hi ? t(one, { who, n: hi }) : t(range, { who, lo, hi });
    if (o.cm) return span(o.cm, "wiki.lex.factCmOne", "wiki.lex.factCmUpto", "wiki.lex.factCm");
    if (o.g_m2) return span(o.g_m2, "wiki.lex.factGramsOne", "wiki.lex.factGramsUpto", "wiki.lex.factGrams");
    if (o.days) return span(o.days, "wiki.lex.factDaysOne", "wiki.lex.factDaysOne", "wiki.lex.factDays");
    if (field === "flowering_days") return t("wiki.lex.scoreDays", { who, days: g.flowering_days, weeks: g.weeks });
    return t("wiki.lex.scoreFact", { who, value: t(LEVEL[g[field]]) });
  }

  // The plain statements on top: size against the box, how hard, how long, how well known, landrace.
  function glance(r) {
    const g = r.grow || {};
    const out = [];
    if (g.height) {
      const h = g.from?.height;
      const cm = !h?.cm ? "" : h.upto ? t("wiki.lex.glanceCmUpto", { hi: h.cm[1] })
        : h.cm[0] === h.cm[1] ? t("wiki.lex.glanceCmOne", { n: h.cm[1] }) : t("wiki.lex.glanceCm", { lo: h.cm[0], hi: h.cm[1] });
      out.push(cm ? `${t(GLANCE[g.height])} (${cm})` : t(GLANCE[g.height]));
    }
    if (g.difficulty) out.push(t(GLANCE[g.difficulty]));
    if (g.weeks) out.push(t((g.scores?.speed || 0) >= 8 ? "wiki.lex.glance.fast" : (g.scores?.speed || 10) <= 4
      ? "wiki.lex.glance.slow" : "wiki.lex.glance.weeks", { weeks: g.weeks }));
    if (r.popularity) out.push(t(POPULAR[r.popularity.level]));
    if (r.origin?.landrace) {
      const where = r.origin.place ? (LANG === "de" ? r.origin.place.de : r.origin.place.en) : "";
      out.push(where ? t("wiki.lex.familyLandraceFrom", { place: where }) : t("wiki.lex.landrace"));
    }
    if (!out.length) return null;
    const ul = el("ul", "wiki-glance"); ul.setAttribute("aria-label", t("wiki.lex.glanceTitle"));
    for (const text of out) ul.append(el("li", "", text));
    return ul;
  }

  function varietyFit(card) {
    const r = card.reference || {};
    const g = r.grow;
    const verdict = g && VERDICTS.includes(g.verdict) ? g.verdict : "none";
    const box = el("div", `wiki-fit wiki-fit-${verdict}`);
    box.append(el("p", "wiki-fit-q", t("wiki.lex.fitTitle")), el("p", "wiki-fit-verdict", t(VERDICT[verdict].title)));
    const sum = glance(r);
    if (sum) box.append(sum);
    box.append(el("p", "wiki-fit-why", t(VERDICT[verdict].why)));
    const reason = (kind, code) => {
      const li = el("li", `wiki-fit-${kind}`);
      if (code === "tall") {
        li.append(r.box_cm ? t("wiki.lex.con.tall", { cm: r.box_cm }) : t("wiki.lex.con.tallNoBox"), " ");
        for (const [key, label] of [["lst", "LST"], ["topping", "Topping"]]) {
          const a = el("a", "wiki-src-link", label); a.href = cardHref("methods", key); li.append(a, key === "lst" ? " · " : "");
        }
      } else if (REASON[kind][code]) li.append(t(REASON[kind][code], { weeks: g.weeks }));
      return li;
    };
    for (const [kind, codes, title] of [["pro", g?.pros || [], "wiki.lex.pros"], ["con", g?.cons || [], "wiki.lex.cons"]]) {
      if (!codes.length) continue;
      const ul = el("ul", "wiki-fit-list"); ul.setAttribute("aria-label", t(title));
      for (const code of codes) ul.append(reason(kind, code));
      box.append(el("p", "wiki-fit-h", t(title)), ul);
    }
    const variants = r.named_variants || [];
    if (variants.length) {
      const p = el("p", "wiki-fit-seeds", `${t("wiki.lex.fitSeeds")} `);
      variants.forEach((variant, i) => {
        const a = el("a", "wiki-src-link", `${variant.label} · ${variant.brand}`);
        a.href = cardHref("strains", variant.key); a.dataset.from = "varieties";
        p.append(i ? ", " : "", a, ` (${t(FLOWERING[variant.flowering] || "wiki.lex.unknown")})`);
      });
      box.append(p);
    }
    return box;
  }

  // score names: "wiki.lex.score.ease", "wiki.lex.score.compact", "wiki.lex.score.speed", "wiki.lex.score.yield",
  // "wiki.lex.score.popularity"
  const SCORE_NAMES = ["wiki.lex.score.ease", "wiki.lex.score.compact", "wiki.lex.score.speed", "wiki.lex.score.yield",
    "wiki.lex.score.popularity"];

  function scoreRow(name, value, fact) {
    const li = el("li", "wiki-score"); li.dataset.score = name;
    const head = el("div", "wiki-score-head");
    head.append(el("span", "wiki-score-name", t(`wiki.lex.score.${name}`)),
      el("span", "wiki-score-num", typeof value === "number" ? t("wiki.lex.scoreOf", { value }) : t("wiki.lex.scoreNone")));
    const bar = el("div", "wiki-score-bar"); bar.setAttribute("aria-hidden", "true");
    const fill = el("span", ""); fill.style.width = `${typeof value === "number" ? value * 10 : 0}%`; bar.append(fill);
    li.append(head, bar, el("span", "wiki-score-fact", fact || t("wiki.lex.scoreNotGiven")));
    return li;
  }

  function scoreRows(card) {
    const r = card.reference || {};
    const g = r.grow;
    const out = [];
    if (g) {
      const list = el("ul", "wiki-scores");
      for (const [name, field] of [["ease", "difficulty"], ["compact", "height"], ["speed", "flowering_days"], ["yield", "yield"]]) {
        list.append(scoreRow(name, g.scores?.[name], factText(g, field)));
      }
      out.push(el("p", "wiki-muted", t("wiki.lex.scoreLead")), list);
    } else out.push(el("p", "wiki-muted", t("wiki.lex.verdict.none")));  // the reason stands in the box above
    if (r.seed) {
      out.push(el("p", "wiki-muted", t(r.seed.via === "seedfinder" ? "wiki.lex.seedVia" : "wiki.lex.seedFrom",
        { product: r.seed.product, maker: r.seed.maker })));
    }
    if (r.popularity) {
      const pop = el("ul", "wiki-scores wiki-scores-pop");
      pop.append(scoreRow("popularity", r.popularity.score, t("wiki.lex.popFact", { reviews: r.popularity.reviews })));
      out.push(pop, el("p", "wiki-muted", t("wiki.lex.popNote")));
    }
    const how = el("details", "wiki-score-how");
    how.append(el("summary", "", t("wiki.lex.scoreHowTitle")), el("p", "wiki-p", t("wiki.lex.scoreHow")),
      el("p", "wiki-p", t("wiki.lex.scoreHowSeed")), el("p", "wiki-p", t("wiki.lex.popHow")));
    out.push(how);
    return out;
  }

  function eraText(era) {
    if (!era) return "";
    if (typeof era.year === "number") return String(era.year);
    return t(ERA[era.part] || ERA.whole, { decade: era.decade });
  }

  function varietyLink(person) {
    if (!person.key) return document.createTextNode(person.name);
    const a = el("a", "wiki-src-link", person.name); a.href = cardHref("varieties", person.key); a.dataset.from = "varieties";
    return a;
  }

  function joinLinks(node, people, sep) {
    people.forEach((p, i) => node.append(i ? sep : "", varietyLink(p)));
    return node;
  }

  function originRows(card) {
    const r = card.reference || {};
    const o = r.origin || {};
    const fam = r.family || { parents: [], descendants: [] };
    const out = [];
    const dl = el("dl", "wiki-dl wiki-origin");
    const row = (label, value) => { const dd = el("dd", ""); dd.append(value); dl.append(el("dt", "", t(label)), dd); };
    if (o.landrace) row("wiki.lex.originKind", t("wiki.lex.landrace"));
    if (o.place) row("wiki.lex.originPlace", LANG === "de" ? o.place.de : o.place.en);
    if (o.breeder) row("wiki.lex.originBreeder", o.breeder);
    if (o.era) row("wiki.lex.originEra", eraText(o.era));
    row("wiki.lex.parents", fam.parents.length ? joinLinks(el("span", ""), fam.parents, " × ") : t("wiki.lex.parentsNone"));
    out.push(dl);
    // one step further back: each parent with a page of its own says where it comes from
    const back = el("ul", "wiki-list wiki-family");
    for (const p of fam.parents) {
      if (!p.key) continue;
      const li = el("li", "");
      li.append(varietyLink(p), ": ");
      const po = p.origin || {};
      const where = po.place ? (LANG === "de" ? po.place.de : po.place.en) : "";
      if (po.landrace) li.append(where ? t("wiki.lex.familyLandraceFrom", { place: where }) : t("wiki.lex.landrace"));
      else if (p.parents?.length) { li.append(`${t("wiki.lex.familyCross")} `); joinLinks(li, p.parents, " × "); }
      else if (where) li.append(t("wiki.lex.familyFrom", { place: where }));
      else li.append(t("wiki.lex.familyUnknown"));
      back.append(li);
    }
    if (back.childNodes.length) out.push(el("p", "wiki-fit-h", t("wiki.lex.familyBack")), back);
    if (fam.descendants.length) out.push(joinLinks(el("p", "wiki-p", `${t("wiki.lex.descendantsPlain")} `), fam.descendants, ", "));
    if (o.landrace || fam.parents.some((p) => p.origin?.landrace)) out.push(el("p", "wiki-muted", t("wiki.lex.landraceWhat")));
    return out;
  }

  function varietyProfile(card) {
    const identity = section("wiki.lex.identity", profileFacts(card));
    identity.append(el("p", "wiki-muted", t("wiki.lex.thcNote")), el("p", "wiki-muted", t("wiki.lex.classificationNote")));
    const availability = el("div");
    availability.append(el("p", "wiki-p", t("wiki.lex.availabilityUnknown")));
    const variants = card.reference?.named_variants || [];
    if (variants.length) {
      availability.append(el("p", "wiki-p", t("wiki.lex.namesakeNote")));
      const ul = el("ul", "wiki-list");
      for (const variant of variants) {
        const li = el("li"); const a = el("a", "wiki-src-link", `${variant.label} · ${variant.brand}`);
        a.href = cardHref("strains", variant.key); a.dataset.from = "varieties";
        li.append(a, ` · ${t(FLOWERING[variant.flowering] || "wiki.lex.unknown")}`); ul.append(li);
      }
      availability.append(ul);
    }
    const provenance = el("div");
    provenance.append(el("p", "wiki-p", t("wiki.lex.selectionNote")), el("p", "wiki-muted", t("wiki.lex.checked", { date: card.reference?.retrieved || t("wiki.lex.unknown") })));
    // the reference sheet stays, one tap away: the plain answer comes first (owner, 01.11.2026: "very technical,
    // very Wikipedia-like")
    const sheet = el("details", "wiki-sheet");
    sheet.append(el("summary", "", t("wiki.lex.sheetOpen")), identity, section("wiki.lex.availability", availability),
      section("wiki.lex.provenance", provenance));
    return [section("wiki.lex.scoresTitle", scoreRows(card)), section("wiki.lex.originTitle", originRows(card)),
      section("wiki.lex.sheetTitle", sheet)];
  }

  function renderVarieties() {
    const view = W.view;
    view.replaceChildren(headBar("wiki.back", () => { location.hash = "#wiki"; }), breadcrumb(t("wiki.lex.varietyTitle")));
    const title = el("h2", "wiki-title", t("wiki.lex.varietyTitle")); title.id = "wiki-title"; title.tabIndex = -1;
    view.append(title, el("p", "wiki-explain", t("wiki.lex.selectionNote")));
    view.append(el("p", "wiki-muted", t("wiki.lex.thcLegend")));
    const catalogue = el("a", "wiki-catalogue-link", t("wiki.lex.breederCatalogue")); catalogue.href = "#wiki/sorten";
    view.append(catalogue);
    const data = W.varieties;
    if (!data) { view.append(el("p", W.error ? "wiki-error" : "wiki-muted", W.error || t("wiki.loading"))); return; }
    const form = el("form", "wiki-search"); form.setAttribute("role", "search");
    const q = el("input", "wiki-q"); q.type = "search"; q.value = W.vq;
    q.placeholder = t("wiki.lex.varietySearch"); q.setAttribute("aria-label", t("wiki.lex.varietySearch"));
    const filters = el("div", "wiki-profile-filters"); filters.setAttribute("role", "group"); filters.setAttribute("aria-label", t("wiki.lex.classification"));
    const body = el("div", "wiki-profile-results");
    const draw = () => {
      W.vq = q.value;
      const query = foldText(W.vq).trim();
      const rows = data.varieties.filter((row) => (!W.vtype || row.reference.classification === W.vtype)
        && (!W.vpick || (W.vpick === "rare" ? row.selection === "rare" : row.reference.grow?.verdict === "good"))
        && foldText(`${row.label} ${(row.reference.aliases || []).join(" ")} ${(row.reference.parents || []).join(" ")}`).includes(query));
      body.replaceChildren(el("p", "wiki-muted", t("wiki.lex.shown", { shown: rows.length, total: data.count })));
      const ul = el("ul", "wiki-profile-grid");
      for (const row of rows.slice(0, W.vshown)) {
        const li = el("li"); const a = cardLink(row); a.dataset.from = "varieties";
        const text = a.querySelector(".wiki-tile-text");
        const thc = typeof row.reference.thc_percent === "number" ? t("wiki.lex.thcShort", { value: num(row.reference.thc_percent) }) : t("wiki.lex.unknown");
        text.append(el("span", "wiki-profile-meta", `${t(CLASSIFICATION[row.reference.classification])} · ${thc}`));
        const verdict = VERDICTS.includes(row.reference.grow?.verdict) ? row.reference.grow.verdict : "none";
        const tags = el("span", "wiki-profile-tags");
        tags.append(el("span", `wiki-fit-pill wiki-fit-${verdict}`, t(VERDICT[verdict].short)));
        if (row.reference.origin?.landrace) tags.append(el("span", "wiki-fit-pill wiki-rare-pill", t("wiki.lex.landrace")));
        if (row.reference.popularity?.level === "rare") tags.append(el("span", "wiki-fit-pill wiki-rare-pill", t("wiki.lex.pop.rare")));
        text.append(tags);
        if (row.image?.external?.representative) text.append(el("small", "wiki-muted", t("wiki.lex.photoRepresentative")));
        else text.append(el("small", "wiki-muted", t("wiki.lex.photoSource")));
        li.append(a); ul.append(li);
      }
      body.append(ul);
      window.WikiTools?.catalogue(ul, rows.slice(0, W.vshown));
      if (!rows.length) body.append(el("p", "wiki-empty", t("wiki.lex.noMatches")));
      if (rows.length > W.vshown) {
        const more = el("button", "btn wiki-more-btn", t("wiki.lex.more", { shown: W.vshown, total: rows.length }));
        more.type = "button"; more.addEventListener("click", () => { W.vshown += 24; draw(); }); body.append(more);
      }
      for (const button of filters.children) button.setAttribute("aria-pressed", String(button.dataset.type === W.vtype));
      for (const button of picks.children) button.setAttribute("aria-pressed", String(button.dataset.pick === W.vpick));
    };
    for (const value of ["", "indica", "sativa", "hybrid"]) {
      const button = el("button", "wiki-profile-filter", value ? t(CLASSIFICATION[value]) : t("wiki.strains.all"));
      button.type = "button"; button.dataset.type = value;
      button.addEventListener("click", () => { W.vtype = value; W.vshown = 24; draw(); }); filters.append(button);
    }
    // discover (owner, 01.11.2026): what suits a first grow, and the rare ones
    const picks = el("div", "wiki-profile-filters"); picks.setAttribute("role", "group"); picks.setAttribute("aria-label", t("wiki.lex.pickLabel"));
    for (const [value, label] of [["", "wiki.lex.pickAll"], ["beginner", "wiki.lex.pickBeginner"], ["rare", "wiki.lex.pickRare"]]) {
      const button = el("button", "wiki-profile-filter", t(label));
      button.type = "button"; button.dataset.pick = value;
      button.addEventListener("click", () => { W.vpick = value; W.vshown = 24; draw(); }); picks.append(button);
    }
    q.addEventListener("input", () => { W.vshown = 24; draw(); });
    form.addEventListener("submit", (e) => { e.preventDefault(); W.vshown = 24; draw(); });
    form.append(q); view.append(form, filters, picks, body); draw();
  }

  async function loadVarieties() {
    try {
      const data = await api("/api/wiki/varieties");
      if (!data || !Array.isArray(data.varieties)) throw new Error(t("wiki.error"));
      W.varieties = data; W.error = "";
    } catch (err) { W.varieties = null; W.error = (err && err.message) || t("wiki.error"); }
  }

  // An area in the index (the Sorten list, the Dünger list): a numbered section like a category, one wide card that
  // opens the list.
  function areaBox(area, number) {
    if (area.id === "duenger") return fertAreaBox(area, number);
    const box = el("section", "wiki-catbox");
    box.id = "wiki-cat-sorten";
    const h = el("h3", "wiki-cat-h");
    h.append(el("span", "wiki-cat-n", String(number).padStart(2, "0")), el("span", "wiki-cat-name", t(CAT.sorten)),
      el("small", "", tn("wiki.strains.count", area.count)));
    h.append(backToIndex());
    const a = el("a", "wiki-area");
    a.href = "#wiki/sorten";
    a.dataset.from = "list";
    const pic = el("span", "wiki-media");
    pic.append(media({ kind: "strain", image: { drawing: { id: "strain-auto" } } }));
    const text = el("span", "wiki-tile-text");
    text.append(el("span", "wiki-feature-t", t("wiki.area.sorten.title")),
      el("span", "wiki-blurb", t("wiki.area.sorten.text", { n: area.count })));
    const pills = el("span", "wiki-link-m");
    pills.append(el("span", "pill sm ok", t("wiki.area.sorten.sure", { n: area.sure })),
      el("span", "pill sm", t("wiki.area.sorten.auto", { n: area.auto })));
    text.append(pills, el("span", "btn sm primary wiki-area-go", t("wiki.area.sorten.open")));
    a.append(pic, text);
    box.append(h, a);
    return box;
  }

  function fertAreaBox(area, number) {
    const box = el("section", "wiki-catbox");
    box.id = "wiki-cat-duenger";
    const h = el("h3", "wiki-cat-h");
    h.append(el("span", "wiki-cat-n", String(number).padStart(2, "0")), el("span", "wiki-cat-name", t(CAT.duenger)),
      el("small", "", tn("wiki.fert.count", area.count)));
    h.append(backToIndex());
    const a = el("a", "wiki-area");
    a.href = "#wiki/duenger";
    a.dataset.from = "list";
    const pic = el("span", "wiki-media");
    pic.append(glyph("nutrient"));
    const text = el("span", "wiki-tile-text");
    text.append(el("span", "wiki-feature-t", t("wiki.area.duenger.title")),
      el("span", "wiki-blurb", t("wiki.area.duenger.text", { n: area.count })));
    const pills = el("span", "wiki-link-m");
    pills.append(el("span", "pill sm ok", t("wiki.area.duenger.plans", { n: area.plans || 0 })));
    text.append(pills, el("span", "btn sm primary wiki-area-go", t("wiki.area.duenger.open")));
    a.append(pic, text);
    box.append(h, a);
    return box;
  }

  // ---------- the fertilizer list: filters by type and medium, one row per line, the overview on top ----------

  function fertPasses(row, ff, skip) {
    if (skip !== "type" && ff.type && !(row.types || []).includes(ff.type)) return false;
    if (skip !== "medium" && ff.medium && !(row.media || []).includes(ff.medium)) return false;
    return true;
  }

  function fertChips(labelKey, name, options, rows) {
    const box = el("div", "wiki-fgroup");
    const label = el("span", "wiki-flabel", t(labelKey));
    label.id = `wiki-ff-${name}`;
    const row = el("div", "wiki-fchips");
    row.setAttribute("role", "group");
    row.setAttribute("aria-labelledby", label.id);
    const others = rows.filter((r) => fertPasses(r, W.ff, name));
    for (const [value, text] of options) {
      const b = el("button", "wiki-fchip");
      b.type = "button";
      const on = W.ff[name] === value;
      b.setAttribute("aria-pressed", on ? "true" : "false");
      if (on) b.classList.add("on");
      const count = value ? others.filter((r) => fertPasses(r, { ...FF_EMPTY, [name]: value })).length : others.length;
      b.append(el("span", "", text), el("b", "", String(count)));
      if (value && !count && !on) b.disabled = true;
      b.addEventListener("click", () => { W.ff[name] = on ? FF_EMPTY[name] : value; renderFertBody(); });
      row.append(b);
    }
    box.append(label, row);
    return box;
  }

  function fertRow(row) {
    const a = el("a", "wiki-row wiki-fert");
    a.href = cardHref("nutrients", row.key);
    a.dataset.from = "duenger";
    if (row.current) a.classList.add("is-current");
    const pic = el("span", "wiki-media");
    pic.append(glyph("nutrient"));
    const text = el("span", "wiki-tile-text");
    text.append(el("span", "wiki-link-t", row.label));
    if (row.maker) text.append(el("span", "wiki-strain-brand", row.maker));
    if (row.blurb) text.append(el("span", "wiki-blurb", row.blurb));
    const meta = el("span", "wiki-link-m");
    if (row.current) meta.append(el("span", "pill sm ok", t("wiki.fert.current")));
    meta.append(el("span", `pill sm ${row.selectable ? "info" : ""}`.trim(), t(row.selectable ? "wiki.fert.selectable" : "wiki.fert.readOnly")));
    // a coco or hydro line says it is not for soil, the owner's medium (fertilizer market 2, 23.10.2026)
    if (row.soil === false) meta.append(el("span", "pill sm warn", t("wiki.fert.notSoil")));
    for (const k of row.types || []) if (FTYPE[k]) meta.append(el("span", "pill sm", t(FTYPE[k])));
    for (const m of row.media || []) if (FMEDIA[m]) meta.append(el("span", "pill sm", t(FMEDIA[m])));
    text.append(meta);
    a.append(pic, text);
    return a;
  }

  function renderFertilizers() {
    if (!W.built) return;
    const view = W.view;
    view.replaceChildren(headBar("wiki.back", () => { if (W.from === "list") history.back(); else location.hash = "#wiki"; }));
    const head = el("header", "wiki-head");
    const title = el("h2", "wiki-title", t("wiki.area.duenger.title"));
    title.id = "wiki-title";
    title.tabIndex = -1;
    const badges = el("div", "wiki-badges");
    badges.append(el("span", "wiki-cat", t("wiki.cat.duenger")), el("span", "wiki-draft", t("wiki.draft")));
    head.append(title, badges);
    view.append(head);
    const d = W.fert;
    if (W.error || !d) {
      view.append(el("p", W.error ? "wiki-error" : "wiki-muted", W.error || t("wiki.loading")));
      return;
    }
    view.append(el("p", "wiki-explain", t("wiki.fert.explain")));
    if (d.overview) {
      // the overview of the types first, as one row with a "read first" mark (no big picture: a glyph says nothing)
      const a = el("a", "wiki-row wiki-fert-overview");
      a.href = cardHref(d.overview.folder || "nutrients", d.overview.key);
      a.dataset.from = "duenger";
      const pic = el("span", "wiki-media");
      pic.append(glyph("nutrient"));
      const text = el("span", "wiki-tile-text");
      const meta = el("span", "wiki-link-m");
      meta.append(el("span", "pill sm info", t("wiki.fert.overview")));
      text.append(el("span", "wiki-link-t", d.overview.label));
      if (d.overview.blurb) text.append(el("span", "wiki-blurb", d.overview.blurb));
      text.append(meta);
      a.append(pic, text);
      view.append(a);
    }
    const body = el("div", "wiki-body");
    view.append(body);
    W.body = body;
    renderFertBody();
  }

  function renderFertBody() {
    const body = W.body;
    const d = W.fert;
    if (!body || !body.isConnected || !d) return;
    body.replaceChildren();
    const rows = Array.isArray(d.lines) ? d.lines : [];
    const types = (Array.isArray(d.types) ? d.types : []).filter((x) => FTYPE[x.key]);
    const media = (Array.isArray(d.media) ? d.media : []).filter((x) => FMEDIA[x.key]);
    const filters = el("div", "wiki-filters");
    filters.append(
      fertChips("wiki.fert.f.type", "type", [["", t("wiki.strains.all")], ...types.map((x) => [x.key, t(FTYPE[x.key])])], rows),
      fertChips("wiki.fert.f.medium", "medium", [["", t("wiki.strains.all")], ...media.map((x) => [x.key, t(FMEDIA[x.key])])], rows));
    body.append(filters);
    const found = rows.filter((r) => fertPasses(r, W.ff));
    const status = el("div", "wiki-fstatus");
    const count = el("p", "wiki-muted", t("wiki.fert.shown", { shown: found.length, total: rows.length }));
    count.setAttribute("role", "status");
    status.append(count);
    if (Object.keys(FF_EMPTY).some((k) => W.ff[k] !== FF_EMPTY[k])) {
      const reset = el("button", "btn sm wiki-freset", t("wiki.strains.reset"));
      reset.type = "button";
      reset.addEventListener("click", () => { W.ff = { ...FF_EMPTY }; renderFertBody(); });
      status.append(reset);
    }
    body.append(status);
    if (!found.length) { body.append(el("p", "wiki-empty", t("wiki.fert.none"))); return; }
    const ul = el("ul", "wiki-rows wiki-ferts");
    for (const row of found) {
      const li = el("li");
      li.append(fertRow(row));
      ul.append(li);
    }
    body.append(ul);
    body.append(el("p", "wiki-p wiki-small wiki-muted", t("wiki.fert.choose")));
  }

  async function loadFertilizers() {
    try {
      const d = await api("/api/wiki/fertilizers");
      if (!d || typeof d !== "object" || !Array.isArray(d.lines)) throw new Error(t("wiki.error"));
      W.fert = d;
      W.error = "";
    } catch (err) {
      W.fert = null;
      W.error = (err && err.message) || t("wiki.error");
    }
  }

  // ---------- the strain lexicon: search, filters, a compact list in pages ----------

  function foldText(s) {
    return String(s || "").toLowerCase().replace(/ß/g, "ss").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function bloomDays(row) {
    const b = Array.isArray(row.bloom) ? row.bloom : null;
    if (!b) return null;
    const k = b[2] === "weeks" ? 7 : 1;
    const hi = typeof b[1] === "number" ? b[1] : b[0];
    return typeof hi === "number" ? hi * k : null;
  }

  function harvestDays(row) {
    const h = Array.isArray(row.harvest) ? row.harvest : null;
    if (!h) return null;
    return typeof h[1] === "number" ? h[1] : typeof h[0] === "number" ? h[0] : null;
  }

  // Does a row pass every filter, `skip` left out (the counts on a group's chips ignore that group's own choice)?
  function passes(row, sf, skip) {
    if (skip !== "q" && sf.q.trim()) {
      const words = foldText(sf.q).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
      const hay = typeof row.find === "string" ? row.find : foldText(`${row.label} ${row.brand}`);
      if (!words.every((w) => hay.includes(w))) return false;
    }
    if (skip !== "fit" && sf.fit && row.fit !== sf.fit) return false;
    if (skip !== "type" && sf.type && row.flowering !== sf.type) return false;
    if (skip !== "bloom" && sf.bloom) { const d = bloomDays(row); if (d == null || d > sf.bloom * 7) return false; }
    if (skip !== "harvest" && sf.harvest) { const d = harvestDays(row); if (d == null || d > sf.harvest) return false; }
    if (skip !== "diff" && sf.diff && row.difficulty !== sf.diff) return false;
    if (skip !== "brand" && sf.brand && row.brand !== sf.brand) return false;
    return true;
  }

  function strainLine(row) {
    const parts = [];
    const b = Array.isArray(row.bloom) ? row.bloom : null;
    const h = Array.isArray(row.harvest) ? row.harvest : null;
    if (h) parts.push(t("wiki.strains.harvest", { text: rangeText(h[0], h[1], "days") }));
    else if (b) parts.push(t("wiki.strains.bloom", { text: rangeText(b[0], b[1], b[2] === "weeks" ? "weeks" : "days") }));
    const ht = Array.isArray(row.height) ? row.height : null;
    if (ht) parts.push(t("wiki.strains.height", { text: rangeText(ht[0], ht[1], "cm") }));
    return parts.join(" · ");
  }

  function strainRow(row) {
    const a = el("a", "wiki-row wiki-strain");
    a.href = cardHref("strains", row.card);
    a.dataset.from = "sorten";
    if (row.current) a.classList.add("is-current");
    const pic = el("span", "wiki-media");
    pic.append(media({ kind: "strain", image: { drawing: { id: row.drawing || "strain" } } }));
    const text = el("span", "wiki-tile-text");
    const title = el("span", "wiki-link-t", row.label);
    text.append(title, el("span", "wiki-strain-brand", row.brand));
    const line = strainLine(row);
    if (line) text.append(el("span", "wiki-strain-line", line));
    const meta = el("span", "wiki-link-m");
    const fit = FIT[row.fit] ? row.fit : "unknown";
    meta.append(el("span", `pill sm ${FIT_PILL[fit]}`.trim(), t(FIT[fit])));
    if (FLOWERING[row.flowering]) meta.append(el("span", "pill sm", t(FLOWERING[row.flowering])));
    if (row.difficulty === "easy") meta.append(el("span", "pill sm", t("wiki.strains.easy")));
    if (row.current) meta.append(el("span", "pill sm ok", t("wiki.yourPlant")));
    text.append(meta);
    a.append(pic, text);
    return a;
  }

  // One group of filter chips: "Alle" first, then each value with how many strains it leaves.
  function chipGroup(labelKey, name, options, rows) {
    const box = el("div", "wiki-fgroup");
    const label = el("span", "wiki-flabel", t(labelKey));
    label.id = `wiki-f-${name}`;
    const row = el("div", "wiki-fchips");
    row.setAttribute("role", "group");
    row.setAttribute("aria-labelledby", label.id);
    const others = rows.filter((r) => passes(r, W.sf, name));
    for (const [value, text] of options) {
      const b = el("button", "wiki-fchip");
      b.type = "button";
      const on = W.sf[name] === value;
      b.setAttribute("aria-pressed", on ? "true" : "false");
      if (on) b.classList.add("on");
      const count = value ? others.filter((r) => passes(r, { ...SF_EMPTY, [name]: value }, "q")).length : others.length;
      b.append(el("span", "", text), el("b", "", String(count)));
      if (value && !count && !on) b.disabled = true;
      b.addEventListener("click", () => {
        W.sf[name] = on ? SF_EMPTY[name] : value;
        W.shown = STRAIN_PAGE;
        renderStrainBody();
      });
      row.append(b);
    }
    box.append(label, row);
    return box;
  }

  function renderStrains() {
    if (!W.built) return;
    const view = W.view;
    const keepFocus = document.activeElement && document.activeElement.classList.contains("wiki-q");
    const caret = keepFocus ? document.activeElement.selectionStart : null;
    view.replaceChildren(headBar("wiki.back", () => { if (W.from === "list") history.back(); else location.hash = "#wiki"; }));
    const d = W.strains;
    const head = el("header", "wiki-head");
    const title = el("h2", "wiki-title", t("wiki.area.sorten.title"));
    title.id = "wiki-title";
    title.tabIndex = -1;
    const badges = el("div", "wiki-badges");
    badges.append(el("span", "wiki-cat", t("wiki.cat.cannabis")));
    if (d && d.status === "draft") {
      const b = el("span", "wiki-draft", t("wiki.draft"));
      b.title = t("wiki.draftHint");
      badges.append(b);
    }
    head.append(title, badges);
    view.append(head);
    if (W.error || !d) {
      view.append(el("p", W.error ? "wiki-error" : "wiki-muted", W.error || t("wiki.loading")));
      return;
    }
    const cm = d.box && typeof d.box.cm === "number" ? num(d.box.cm) : "–";
    const sid = d.box && d.box.source && d.box.source.id ? d.box.source.id : "";
    view.append(el("p", "wiki-explain", t("wiki.strains.explain", { note: textOf(d.note), cm, id: sid })));
    const form = el("form", "wiki-search");
    form.setAttribute("role", "search");
    const q = el("input", "wiki-q");
    q.type = "search";
    q.value = W.sf.q;
    q.placeholder = t("wiki.strains.search");
    q.setAttribute("aria-label", t("wiki.strains.searchLabel"));
    q.autocomplete = "off";
    q.setAttribute("enterkeyhint", "search");
    q.addEventListener("input", () => { W.sf.q = q.value; W.shown = STRAIN_PAGE; renderStrainBody(); });
    form.addEventListener("submit", (e) => { e.preventDefault(); q.blur(); });
    form.append(q);
    view.append(form);
    const body = el("div", "wiki-body");
    view.append(body);
    W.body = body;
    renderStrainBody();
    if (keepFocus) { q.focus({ preventScroll: true }); if (caret != null) q.setSelectionRange(caret, caret); }
  }

  function renderStrainBody() {
    const body = W.body;
    const d = W.strains;
    if (!body || !body.isConnected || !d) return;
    body.replaceChildren();
    const rows = Array.isArray(d.strains) ? d.strains : [];
    // the box filter always in view; the others folded on a phone (open on a wide screen), so the list starts on
    // the first screen; the summary counts the filters that are set
    const filters = el("div", "wiki-filters");
    filters.append(
      chipGroup("wiki.strains.f.fit", "fit", [["", t("wiki.strains.all")], ...Object.keys(FIT).map((k) => [k, t(FIT[k])])], rows));
    const more = el("details", "wiki-morefilters");
    if (W.moreOpen === undefined) W.moreOpen = typeof matchMedia === "function" && matchMedia("(min-width: 700px)").matches;
    more.open = !!W.moreOpen;
    more.addEventListener("toggle", () => { W.moreOpen = more.open; });
    const active = ["type", "bloom", "harvest", "diff", "brand"].filter((k) => W.sf[k] !== SF_EMPTY[k]).length;
    const sum = el("summary", "", t("wiki.strains.moreFilters"));
    if (active) sum.append(el("span", "pill sm info", tn("wiki.strains.active", active)));
    const inner = el("div", "wiki-filters-inner");
    more.append(sum, inner);
    inner.append(
      chipGroup("wiki.strains.f.type", "type", [["", t("wiki.strains.all")], ["auto", t("wiki.strains.auto")],
        ["photoperiod", t("wiki.strains.photo")]], rows),
      chipGroup("wiki.strains.f.bloom", "bloom", [[0, t("wiki.strains.all")],
        ...BLOOM_WEEKS.map((w) => [w, t("wiki.strains.bloomMax", { n: w })])], rows),
      chipGroup("wiki.strains.f.harvest", "harvest", [[0, t("wiki.strains.all")],
        ...HARVEST_DAYS.map((n) => [n, t("wiki.strains.harvestMax", { n })])], rows),
      chipGroup("wiki.strains.f.diff", "diff", [["", t("wiki.strains.all")], ["easy", t("wiki.strains.easy")]], rows));
    filters.append(more);
    // the brand: a plain select, eight brands do not fit a chip row on a phone
    const brandBox = el("div", "wiki-fgroup");
    const brandLabel = el("label", "wiki-flabel", t("wiki.strains.f.brand"));
    brandLabel.htmlFor = "wiki-f-brand";
    const sel = el("select", "wiki-fselect");
    sel.id = "wiki-f-brand";
    const others = rows.filter((r) => passes(r, W.sf, "brand"));
    const all = el("option", "", `${t("wiki.strains.allBrands")} (${others.length})`);
    all.value = "";
    sel.append(all);
    for (const b of Array.isArray(d.brands) ? d.brands : []) {
      const n = others.filter((r) => r.brand === b.name).length;
      const o = el("option", "", `${b.name} (${n})`);
      o.value = b.name;
      if (!n && W.sf.brand !== b.name) o.disabled = true;
      sel.append(o);
    }
    sel.value = W.sf.brand;
    sel.addEventListener("change", () => { W.sf.brand = sel.value; W.shown = STRAIN_PAGE; renderStrainBody(); });
    brandBox.append(brandLabel, sel);
    inner.append(brandBox);
    body.append(filters);
    const found = rows.filter((r) => passes(r, W.sf));
    const status = el("div", "wiki-fstatus");
    const count = el("p", "wiki-muted", t("wiki.strains.shown", { shown: found.length, total: rows.length }));
    count.setAttribute("role", "status");
    status.append(count);
    const set = Object.keys(SF_EMPTY).some((k) => W.sf[k] !== SF_EMPTY[k]);
    if (set) {
      const reset = el("button", "btn sm wiki-freset", t("wiki.strains.reset"));
      reset.type = "button";
      reset.addEventListener("click", () => { W.sf = { ...SF_EMPTY }; W.shown = STRAIN_PAGE; renderStrains(); });
      status.append(reset);
    }
    body.append(status);
    if (!found.length) { body.append(el("p", "wiki-empty", t("wiki.strains.none"))); return; }
    const ul = el("ul", "wiki-rows wiki-strains");
    for (const row of found.slice(0, W.shown)) {
      const li = el("li");
      li.append(strainRow(row));
      ul.append(li);
    }
    body.append(ul);
    if (found.length > W.shown) {
      const more = el("button", "btn sm wiki-more-btn", t("wiki.strains.more", { shown: Math.min(W.shown, found.length),
        total: found.length }));
      more.type = "button";
      more.addEventListener("click", () => {
        const at = window.scrollY;
        W.shown += STRAIN_PAGE;
        renderStrainBody();
        window.scrollTo(0, at);
      });
      body.append(more);
    }
  }

  async function loadStrains() {
    try {
      const d = await api("/api/wiki/strains");
      if (!d || typeof d !== "object" || !Array.isArray(d.strains)) throw new Error(t("wiki.error"));
      W.strains = d;
      W.error = "";
    } catch (err) {
      W.strains = null;
      W.error = (err && err.message) || t("wiki.error");
    }
  }

  function hankButton(label, folder) {
    // Only when Hank says he can explain a card (Hank v1.2 sets the flag with its router intent): before that he
    // answers that he is the wrong one to ask, and a button that visibly fails is worse than none (review 23.10.).
    if (!(window.HANK_FEATURES && window.HANK_FEATURES.explainCard)) return null;
    // Decided per folder, not per card (v1.4): web/hank.js lists the folders whose every card Hank answers
    // (vgrow/hank/wikicards.py EXPLAIN_FOLDERS); a folder he has no composer for keeps the button hidden.
    const folders = window.HANK_FEATURES.explainFolders;
    if (!Array.isArray(folders) || folders.indexOf(folder) < 0) return null;
    const fab = document.querySelector(".hk-fab");
    const q = document.getElementById("hk-q");
    if (!fab || fab.hidden || !q) return null;
    const b = el("button", "btn sm wiki-hank", t("wiki.hankBtn"));
    b.type = "button";
    b.addEventListener("click", () => {
      const drawer = document.getElementById("hk-drawer");
      if (drawer && drawer.hidden) fab.click();
      if (q.disabled || !q.form) return;  // "nur Warnungen": the drawer opens, the field stays closed
      q.value = t("wiki.hankAsk", { label });
      if (typeof q.form.requestSubmit === "function") q.form.requestSubmit();
    });
    return b;
  }

  function sourcesBlock(sources) {
    const arr = Array.isArray(sources) ? sources : [];
    if (!arr.length) return null;
    const ol = el("ol", "wiki-sources");
    for (const s of arr) {
      const li = el("li");
      const title = s.kind === "image"
        ? t("wiki.src.image", { title: textOf(s.title), author: textOf(s.author), license: textOf(s.license) })
        : textOf(s.title);
      li.append(el("b", "wiki-sid", String(s.id || "")), el("span", "", ` ${title}`));
      if (typeof s.host === "string" && s.host) {
        const when = typeof s.retrieved === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s.retrieved) ? dayText(s.retrieved) : "";
        li.append(el("span", "wiki-muted", ` · ${when ? t("wiki.src.read", { host: s.host, date: when }) : s.host}`));
      }
      if (s.kind === "image" && safeUrl(s.license_url)) li.append(" ", link(t("wiki.src.license"), s.license_url, "wiki-src-link"));
      const url = safeUrl(s.url);
      if (url) {
        const a = el("a", "wiki-src-link", t("wiki.sourceOpen"));
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.title = t("wiki.newTab");
        li.append(" ", a);
      }
      ol.append(li);
    }
    return section("wiki.sources", ol);
  }

  function renderCard() {
    if (!W.built) return;
    const view = W.view;
    view.replaceChildren(headBar("wiki.back", toList));
    const d = W.card;
    if (W.error || !d) {
      view.append(el("p", W.error ? "wiki-error" : "wiki-muted", W.error || t("wiki.loading")));
      return;
    }
    const card = d.card && typeof d.card === "object" ? d.card : {};
    const head = el("header", "wiki-head");
    const title = el("h2", "wiki-title", d.label);
    title.id = "wiki-title";
    title.tabIndex = -1;
    const badges = el("div", "wiki-badges");
    if (CAT[d.category]) badges.append(el("span", "wiki-cat", t(CAT[d.category])));
    if (KIND[d.kind]) badges.append(el("span", "wiki-kind", t(KIND[d.kind])));
    if (d.status === "draft") {
      const b = el("span", "wiki-draft", t("wiki.draft"));
      b.title = t("wiki.draftHint");
      badges.append(b);
    }
    const hank = hankButton(d.label, d.folder);
    if (hank) badges.append(hank);
    head.append(title, badges);
    const parent = articleParent(d);
    view.append(breadcrumb(parent ? t(parent.label) : "", parent?.href), head);
    if (d.status === "draft") view.append(el("p", "wiki-drafthint", t("wiki.draftHint")));
    const contents = el("nav", "wiki-contents");
    contents.hidden = true;
    view.append(contents);
    // picture and lead side by side on a wide screen, the picture first on a phone
    view.classList.toggle("wiki-lexcard", !!d.lexicon);
    const intro = el("div", "wiki-intro");
    const pic = hero(d);
    if (pic) intro.append(pic);
    const leadBox = el("div", "wiki-intro-text");
    for (const field of ["summary", "text", "description"]) {
      const p = para(card[field], "wiki-lead");
      if (p) leadBox.append(p);
    }
    const sub = para(card.status_note, "wiki-p wiki-small wiki-muted");
    if (sub) leadBox.append(sub);
    if (d.kind === "variety") leadBox.append(varietyFit(card));
    if (leadBox.childNodes.length) intro.append(leadBox);
    if (pic && leadBox.childNodes.length) intro.classList.add("two");
    if (intro.childNodes.length) view.append(intro);
    const render = RENDER[d.kind] || generic;
    let parts = [];
    // A card in a shape its renderer does not know still shows: the generic one takes over, and the console says why.
    try { parts = render(card) || []; } catch (err) { console.error("wiki: renderer failed", d.kind, err); parts = generic(card); }
    for (const p of parts.flat()) if (p) view.append(p);
    const notes = card.notes;
    if (Array.isArray(notes) ? notes.length : typeof notes === "string" && notes.trim()) {
      const sec = section("wiki.sec.notes", list(notes));
      if (sec) view.append(sec);
    }
    const warn = section("wiki.sec.warnings", list(card.warnings));
    if (warn) { warn.classList.add("wiki-warn"); view.append(warn); }
    const src = sourcesBlock(d.sources);
    if (src) view.append(src);
    window.WikiTools?.article(view, d);
    pageContents(contents, [...view.childNodes].filter((node) => node.classList && node.classList.contains("wiki-sec")));
  }

  // ---------- loading ----------

  async function loadIndex() {
    try {
      const d = await api("/api/wiki");
      if (!d || typeof d !== "object" || !Array.isArray(d.categories)) throw new Error(t("wiki.error"));
      W.index = d;
      W.error = "";
      return true;
    } catch (err) {
      W.error = (err && err.message) || t("wiki.error");
      return false;
    }
  }

  async function runSearch(text) {
    const q = String(text || "").trim();
    W.query = String(text || "");
    if (!q) { W.seq++; W.results = null; return; }  // an answer still on its way for the old text is dropped
    const seq = ++W.seq;
    try {
      const d = await api(`/api/wiki/search?q=${encodeURIComponent(q)}`);
      if (seq !== W.seq) return;
      W.results = d && Array.isArray(d.results) ? d : { results: [] };
      W.error = "";
    } catch (err) {
      if (seq !== W.seq) return;
      W.results = { results: [] };
      W.error = (err && err.message) || t("wiki.error");
    }
  }

  async function loadCard(folder, key, seq) {
    W.card = null;
    W.error = "";
    renderCard();
    try {
      const d = await api(`/api/wiki/card/${encodeURIComponent(folder)}/${encodeURIComponent(key)}`);
      if (seq !== routeSeq) return;  // Back or another article won while this request was in flight
      if (!d || typeof d !== "object" || !d.card) throw new Error(t("wiki.error"));
      W.card = d;
    } catch (err) {
      if (seq !== routeSeq) return;
      W.error = (err && err.message) || t("wiki.error");
    }
    renderCard();
  }

  // ---------- routing: #wiki is the list, #wiki/folder/key one card, anything else the cockpit ----------

  function parse() {
    const h = location.hash || "";
    if (h !== "#wiki" && !h.startsWith("#wiki/")) return null;
    const parts = h.slice(1).split("/").slice(1).map((p) => { try { return decodeURIComponent(p); } catch (_) { return ""; } });
    if (parts[0] === "tools" && parts[1]) return { tool: parts[1], argument: parts.slice(2).join("/") };
    if (parts[0] === "topic" && parts.length === 2) return { topic: parts[1] };
    if (parts.length === 1 && (parts[0] === "sorten" || parts[0] === "duenger" || parts[0] === "varieties")) return { area: parts[0] };
    return parts.length >= 2 && parts[0] && parts[1] ? { folder: parts[0], key: parts[1] } : {};
  }

  function setEntries(on) {
    for (const b of document.querySelectorAll("[data-wiki-open]")) {
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    }
  }

  let lastWasCard = false;
  let routeSeq = 0;
  async function route() {
    const seq = ++routeSeq;
    const where = parse();
    if (!where) {
      if (W.built && !W.view.hidden) {
        W.view.hidden = true;
        document.body.classList.remove("wiki-on");
        setEntries(false);
        // a section link while the wiki was open: the section was hidden when the browser looked for it
        const id = (location.hash || "").slice(1);
        const target = id && document.getElementById(id);
        if (target) target.scrollIntoView();
      }
      return;
    }
    if (!W.built && !build()) return;
    const opening = W.view.hidden;
    if (opening) W.own = undefined;  // a new opening asks for the newest camera photo again
    W.view.hidden = false;
    document.body.classList.add("wiki-on");
    setEntries(true);
    if (where.tool) {
      lastWasCard = false;
      if (window.WikiTools) await window.WikiTools.open(W.view, where.tool, where.argument || "");
      else W.view.replaceChildren(el("p", "wiki-error", t("wiki.error")));
      if (seq !== routeSeq) return;
      window.scrollTo(0, 0);
    } else if (where.topic) {
      lastWasCard = false;
      if (!W.index || opening) await loadIndex();
      if (seq !== routeSeq) return;
      W.topic = where.topic;
      renderTopic();
      window.scrollTo(0, 0);
    } else if (where.area === "varieties") {
      lastWasCard = false;
      renderVarieties();
      if (!W.varieties || opening) await loadVarieties();
      if (seq !== routeSeq) return;
      renderVarieties();
      window.scrollTo(0, 0);
    } else if (where.folder) {
      lastWasCard = true;
      await loadCard(where.folder, where.key, seq);
      if (seq !== routeSeq) return;
      window.scrollTo(0, 0);
    } else if (where.area === "duenger") {
      const fromCard = lastWasCard;
      lastWasCard = false;
      const from = W.from;
      if (!W.fert || opening || !fromCard) { W.fert = null; renderFertilizers(); await loadFertilizers(); }
      if (seq !== routeSeq) return;
      W.from = from;
      renderFertilizers();
      if (!fromCard) window.scrollTo(0, 0);
    } else if (where.area) {
      const fromCard = lastWasCard;
      lastWasCard = false;
      const from = W.from;
      if (!W.strains || opening || !fromCard) { W.strains = null; renderStrains(); await loadStrains(); }
      if (seq !== routeSeq) return;
      W.from = from;
      renderStrains();
      if (!fromCard) window.scrollTo(0, 0);
    } else {
      const fromCard = lastWasCard;
      lastWasCard = false;
      W.from = "";
      if (!W.index || opening) await loadIndex();
      if (seq !== routeSeq) return;
      renderList();
      if (!fromCard) window.scrollTo(0, 0);
    }
    const title = document.getElementById("wiki-title");
    if (title && opening) title.focus({ preventScroll: true });
  }

  window.addEventListener("hashchange", route);
  window.WikiRefresh = route;
  window.WikiInvalidate = () => { W.varieties = null; };
  // A card opened from the list: "Zur Übersicht" goes back in the history, so the list keeps its scroll position.
  document.addEventListener("click", (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[href^="#wiki/"]') : null;
    if (a) W.from = a.dataset.from || "";
  });

  // The page language changed: the server answers labels and texts in the request language, so fetch again.
  new MutationObserver(() => {
    if (!W.built || W.view.hidden) return;
    W.index = null;
    W.varieties = null;
    if (W.query.trim()) runSearch(W.query).then(() => route());
    else route();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });

  // Shown only once the server answered: a server without the route, or a broken one, leaves the page as it was.
  let shown = false;
  let asking = false;
  async function start() {
    if (shown || asking) return;
    asking = true;
    try {
      if (!(await loadIndex())) return;
    } finally {
      asking = false;
    }
    shown = true;
    W.error = "";
    document.removeEventListener("visibilitychange", retry);
    for (const b of document.querySelectorAll("[data-wiki-open]")) {
      b.setAttribute("aria-controls", "wiki-view");
      b.addEventListener("click", () => {
        if (location.hash === "#wiki") route(); else location.hash = "#wiki";
      });
      b.hidden = false;
    }
    if (parse()) route();  // a deep link: #wiki/crops/basil opens the card right away
  }
  function retry() { if (!document.hidden) start(); }
  document.addEventListener("visibilitychange", retry);
  start();
})();
