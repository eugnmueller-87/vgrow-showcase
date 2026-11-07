// The news list: one "News" entry at the end of the section navigation opens a short list of headlines and links.
// Loaded after app.js and uses its globals t(), api(), LANG, LOCALE, dateLong() and when(); it never touches render().
// A guest like Hank: until /api/news answers once, the entry stays hidden and the cockpit runs on without it.
// Every text is a key of web/i18n.js (news.*), written out in full here, so tests/test_web_i18n.py can check each one.
// No markup from data: titles, names and source lines go in through textContent, links only with http: or https:.
// No images, no teasers: the cockpit shows the headline, the source and a link to the original, nothing else.
(function () {
  "use strict";

  if (!window.I18N || typeof api !== "function") return;

  // publisher_kind -> label. An unknown kind gets no label rather than a wrong one.
  const KIND = {
    official: "news.kind.official",
    court: "news.kind.court",
    press: "news.kind.press",
    advocacy: "news.kind.advocacy",
    research: "news.kind.research",
    horticulture: "news.kind.horticulture",
  };
  const N = { built: false, dialog: null, body: null, data: null, error: "" };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function safeUrl(url) {
    return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
  }

  function day(iso) { return typeof iso === "string" && iso ? dateLong(iso.slice(0, 10)) : ""; }

  function closeIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M6 6l12 12M18 6L6 18");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    svg.append(path);
    return svg;
  }

  // ---------- the dialog ----------

  function build() {
    const dialog = el("dialog", "news-dialog");
    dialog.id = "news-dialog";
    dialog.setAttribute("aria-labelledby", "news-title");
    const head = el("div", "news-head");
    const title = el("h2", "news-title");
    title.id = "news-title";
    title.tabIndex = -1;
    const shut = el("button", "news-close");
    shut.type = "button";
    shut.append(closeIcon());
    shut.addEventListener("click", () => dialog.close());
    head.append(title, shut);
    const body = el("div", "news-body");
    dialog.append(head, body);
    // A tap on the backdrop closes it, as Escape does (the dialog itself fills its box, so only the backdrop is it).
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
    dialog.addEventListener("close", () => {
      for (const b of document.querySelectorAll("[data-news-open]")) b.setAttribute("aria-expanded", "false");
    });
    document.body.append(dialog);
    Object.assign(N, { built: true, dialog, body, title, shut });
    labels();
  }

  function labels() {
    N.title.textContent = t("news.title");
    N.shut.setAttribute("aria-label", t("news.close"));
  }

  function itemRow(item) {
    const li = el("li", "news-item");
    const meta = el("div", "news-meta");
    const kind = KIND[item.publisher_kind];
    if (kind) meta.append(el("span", "pill sm", t(kind)));
    meta.append(el("span", "news-src", String(item.source_name || "")));
    const date = item.dated === "feed" ? day(item.published) : t("news.seen", { date: day(item.first_seen) });
    if (date) meta.append(el("span", "news-date", date));
    li.append(meta, el("p", "news-headline", String(item.title || "")));
    const url = safeUrl(item.link);
    if (url) {
      const a = el("a", "news-link", t("news.original"));
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.title = t("news.newTab");
      li.append(a);
    }
    // the source line only when it says more than the name above it
    const attr = item.attribution ? String(item.attribution) : "";
    if (attr && attr !== String(item.source_name || "")) li.append(el("p", "news-attr", t("news.source", { text: attr })));
    return li;
  }

  function names(list) { return list.map((s) => String(s.name || s.id || "")).filter(Boolean).join(", "); }

  function render() {
    if (!N.built) return;
    const box = N.body;
    box.replaceChildren(el("p", "news-explain", t("news.explain")));
    if (N.error) {
      box.append(el("p", "news-error", N.error));
      return;
    }
    const d = N.data;
    if (!d) {
      box.append(el("p", "news-status", t("news.loading")));
      return;
    }
    const items = Array.isArray(d.items) ? d.items : [];
    const sources = Array.isArray(d.sources) ? d.sources : [];
    if (items.length) {
      const list = el("ol", "news-list");
      list.append(...items.map(itemRow));
      box.append(list);
    } else {
      box.append(el("p", "news-empty", d.quiet_since ? t("news.empty", { date: day(d.quiet_since) }) : t("news.emptyNever")));
    }
    const foot = el("div", "news-foot");
    if (d.last_fetch) {
      const reached = sources.filter((s) => s.ok).length;
      foot.append(el("p", "news-status", t("news.stand", { time: when(new Date(d.last_fetch)), ok: reached, total: sources.length })));
      const missing = names(sources.filter((s) => !s.ok));
      if (missing) foot.append(el("p", "news-status", t("news.missing", { names: missing })));
    }
    const gaps = names(sources.filter((s) => s.gap));
    if (gaps) foot.append(el("p", "news-status news-gap", t("news.gap", { names: gaps })));
    if (foot.childNodes.length) box.append(foot);
  }

  async function load() {
    try {
      const d = await api("/api/news");
      if (!d || typeof d !== "object" || !Array.isArray(d.items)) throw new Error(t("news.error"));
      N.data = d;
      N.error = "";
      return true;
    } catch (err) {
      N.error = (err && err.message) || t("news.error");
      return false;
    } finally {
      render();
    }
  }

  async function open(trigger) {
    if (!N.built) build();
    N.error = "";
    render();
    if (!N.dialog.open) N.dialog.showModal();
    trigger.setAttribute("aria-expanded", "true");
    N.title.focus({ preventScroll: true });
    await load();
  }

  function entries() { return document.querySelectorAll("[data-news-open]"); }

  // The page language changed: the entry's text follows through data-i18n, the open list is drawn again, and fetched
  // again while open, since the server answers headlines and source names in the request language.
  new MutationObserver(() => {
    if (!N.built) return;
    labels();
    render();
    if (N.dialog && N.dialog.open) load();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });

  // Shown only once the server answered: a server without the route, or a broken one, leaves the page as it was.
  // A first call that fails (a server restart) is tried again whenever the tab comes back, until one answer arrives.
  let shown = false;
  let asking = false;
  async function start() {
    if (shown || asking) return;
    asking = true;
    try {
      const d = await api("/api/news");
      if (!d || typeof d !== "object" || !Array.isArray(d.items)) return;
      N.data = d;
    } catch (_) {
      return;
    } finally {
      asking = false;
    }
    shown = true;
    document.removeEventListener("visibilitychange", retry);
    for (const b of entries()) {
      b.setAttribute("aria-haspopup", "dialog");
      b.setAttribute("aria-controls", "news-dialog");
      b.setAttribute("aria-expanded", "false");
      b.addEventListener("click", () => open(b));
      b.hidden = false;
    }
  }
  function retry() { if (!document.hidden) start(); }
  document.addEventListener("visibilitychange", retry);
  start();
})();
