/* Public content stays in the cockpit. Reddit uses native browser links; other account services use companion windows. */
(() => {
  "use strict";
  const root = document.getElementById("community-hub");
  const TEXT = {
    heading: "community.hub.heading", intro: "community.hub.intro", refresh: "community.hub.refresh",
    refreshing: "community.hub.refreshing", loading: "community.hub.loading", search: "community.hub.search",
    all: "community.hub.all", stories: "community.hub.stories", videos: "community.hub.videos", saved: "community.hub.saved",
    source: "community.hub.source", checked: "community.hub.checked", cached: "community.hub.cached",
    unavailable: "community.hub.unavailable", noMatches: "community.hub.noMatches", savedEmpty: "community.hub.savedEmpty",
    save: "community.hub.save", unsave: "community.hub.unsave", read: "community.hub.read", play: "community.hub.play",
    videoFrom: "community.hub.videoFrom", story: "community.hub.story", video: "community.hub.video",
    tools: "community.hub.tools", toolsNote: "community.hub.toolsNote", window: "community.hub.window",
    focus: "community.hub.focus", signin: "community.hub.signin", blocked: "community.hub.blocked",
    openTab: "community.hub.openTab", windowOpened: "community.hub.windowOpened", close: "community.hub.close",
    discordNote: "community.hub.discordNote", facebookNote: "community.hub.facebookNote", accountNote: "community.hub.accountNote",
    online: "community.hub.online", members: "community.hub.members", publicCounts: "community.hub.publicCounts",
    noCounts: "community.hub.noCounts", youtube: "community.hub.youtube", storage: "community.hub.storage",
    unread: "community.hub.unread", publicFeed: "community.hub.publicFeed", resultCount: "community.hub.resultCount",
    sourceOrder: "community.hub.sourceOrder", demo: "community.hub.demo", playerNote: "community.hub.playerNote",
    posts: "community.hub.posts", post: "community.hub.post", discussion: "community.hub.discussion", more: "community.hub.more",
    redditTab: "community.hub.redditTab", redditHelp: "community.hub.redditHelp", redditHelpText: "community.hub.redditHelpText",
    redditSignin: "community.hub.redditSignin", redditSupport: "community.hub.redditSupport",
    copyLink: "community.hub.copyLink", copied: "community.hub.copied", copyManual: "community.hub.copyManual"
  };
  const tr = (key, values) => t(TEXT[key], values);
  const n = (tag, cls = "", text) => { const element = document.createElement(tag); element.className = cls; if (text !== undefined) element.textContent = text; return element; };
  const services = { vivosun: "VIVOSUN", discord: "Discord", facebook: "Facebook", reddit: "Reddit" };
  const windows = new Map();
  let data = null, busy = false, lastLoad = 0, loadedLang = "", filter = "all", query = "", storageFailed = false, limit = 12;
  let bookmarks = {}, seen = [], ui = {}, preference = "", activeDialog = null, scrollPosition = 0;
  let imageChecks = [], imageObserver = null, pixelFrame = 0, lastDensity = window.devicePixelRatio;
  const checkImages = () => imageChecks.forEach(check => check());
  function watchPixelDensity() {
    pixelFrame = 0;
    if (!visible()) return;
    if (lastDensity !== window.devicePixelRatio) { lastDensity = window.devicePixelRatio; checkImages(); }
    pixelFrame = requestAnimationFrame(watchPixelDensity);
  }
  try {
    const saved = JSON.parse(localStorage.getItem("vgrow.community.desk") || "{}");
    if (["all", "posts", "stories", "videos", "saved"].includes(saved.filter)) filter = saved.filter;
    if (typeof saved.query === "string") query = saved.query.slice(0, 200);
    if (saved.bookmarks && typeof saved.bookmarks === "object" && !Array.isArray(saved.bookmarks)) bookmarks = saved.bookmarks;
    if (Array.isArray(saved.seen)) seen = saved.seen.filter(id => typeof id === "string").slice(-500);
  } catch (_) { /* blocked or old browser storage; the public feed still works */ }
  function url(service, login = false) {
    if (service === "vivosun") return `https://vivosun.com/${LANG === "de" ? "de" : "en"}-DE/${login ? "account/login?redirect=%2Fcommunity" : "community"}`;
    if (service === "discord") return "https://discord.com/invite/DHp7a6jxNk";
    if (service === "reddit") return "https://www.reddit.com/r/VIVOSUN/";
    return "https://www.facebook.com/groups/477264677334391/";
  }
  function validItem(item) {
    if (!item || typeof item.id !== "string" || !["story", "video", "post"].includes(item.kind) || typeof item.title !== "string") return false;
    try {
      const u = new URL(item.url);
      if (u.protocol !== "https:" || u.username || u.password || u.port) return false;
      if (item.kind === "story") return u.hostname === "vivosun.com" && /^\/(?:de|en)-DE\/community(?:\/article\/[^/]+)?$/.test(u.pathname);
      if (item.kind === "post") return u.hostname === "www.reddit.com" && /^\/r\/VIVOSUN\/comments\/[a-z0-9]+(?:\/[^?#]*)?$/i.test(u.pathname);
      return /^[A-Za-z0-9_-]{11}$/.test(item.video) && u.hostname === "www.youtube.com" && u.pathname === "/watch" && u.searchParams.get("v") === item.video;
    } catch (_) { return false; }
  }
  function cover(item, article) {
    const picture = n("div", "comm-picture"); picture.hidden = true;
    try {
      const u = new URL(item.image), hosts = item.kind === "post"
        ? ["preview.redd.it", "i.redd.it", "external-preview.redd.it", "a.thumbs.redditmedia.com", "b.thumbs.redditmedia.com"]
        : ["image.next.vivosun.com"];
      if (u.protocol === "https:" && hosts.includes(u.hostname) && !u.username && !u.password && !u.port) {
        // Decode off-screen first. Never show a low-resolution candidate while checking it.
        const img = n("img"); img.alt = ""; img.decoding = "async"; img.referrerPolicy = "no-referrer";
        let decoded = false;
        const check = () => {
          if (!article.isConnected) return;
          const border = getComputedStyle(article), density = Math.max(1, window.devicePixelRatio || 1);
          const width = article.getBoundingClientRect().width - parseFloat(border.borderLeftWidth) - parseFloat(border.borderRightWidth);
          // object-fit: cover needs enough pixels on BOTH axes after the 16:9 crop.
          const approved = decoded && width > 0 && img.naturalWidth >= Math.max(640, Math.ceil(width * density))
            && img.naturalHeight >= Math.max(360, Math.ceil(width * 9 / 16 * density));
          picture.hidden = !approved;
          article.classList.toggle("comm-card-with-image", approved);
        };
        imageChecks.push(check);
        img.onload = async () => {
          try { await img.decode(); decoded = true; picture.prepend(img); check(); }
          catch (_) { /* An undecodable image stays out of the card. */ }
        };
        img.onerror = () => { decoded = false; picture.hidden = true; article.classList.remove("comm-card-with-image"); };
        img.src = u.href;
      }
    } catch (_) { /* Text-only card when the source has no supported image. */ }
    picture.append(n("span", "comm-kind", tr(item.kind)));
    return picture;
  }
  function persist() {
    try { localStorage.setItem("vgrow.community.desk", JSON.stringify({ filter, query, bookmarks, seen })); }
    catch (_) { storageFailed = true; }
    if (ui.storage) ui.storage.hidden = !storageFailed;
  }
  function button(text, action, cls = "") { const b = n("button", `btn ${cls}`, text); b.type = "button"; b.onclick = action; return b; }
  function external(label, destination) {
    const a = n("a", "text-link", label); a.href = destination; a.target = "_blank"; a.rel = "noopener noreferrer"; a.referrerPolicy = "no-referrer"; return a;
  }
  function redditLink(label, destination, cls, item) {
    // Native links retain browser tab/context-menu behavior. A popup opening successfully says nothing
    // about Reddit accepting the request; the cockpit cannot inspect that cross-origin response.
    const a = external(label, destination); a.className = `btn ${cls}`;
    if (item) {
      a.addEventListener("click", () => remember(item));
      a.addEventListener("auxclick", event => { if (event.button === 1) remember(item); });
    }
    return a;
  }
  function remember(item) { if (!seen.includes(item.id)) { seen.push(item.id); seen = seen.slice(-500); persist(); } ui.feed?.querySelector(`[data-community-item="${CSS.escape(item.id)}"] .comm-unread`)?.remove(); }
  function openWindow(service, destination, item) {
    if (!Object.hasOwn(services, service) || service === "reddit") return;
    let w = windows.get(service), opened = false;
    try {
      if (!w || w.closed) {
        const width = Math.min(1020, Math.max(520, screen.availWidth - 100)), height = Math.max(400, Math.min(900, screen.availHeight - 80));
        w = window.open("", `vgrow_community_${service}`, `popup=yes,width=${width},height=${height},resizable=yes,scrollbars=yes`);
        if (w) {
          try { const referrer = w.document.createElement("meta"); referrer.name = "referrer"; referrer.content = "no-referrer"; w.document.head.append(referrer); w.opener = null; w.location.replace(destination || url(service)); }
          catch (_) { if (destination) w.location.href = destination; } // browser found an existing cross-origin named window
          windows.set(service, w); opened = true;
        }
      } else { if (destination) w.location.href = destination; opened = true; }
      if (opened) w.focus();
    } catch (_) { opened = false; windows.delete(service); }
    ui.notice.replaceChildren(); ui.notice.hidden = false;
    if (opened) {
      if (item) remember(item);
      ui.notice.append(n("span", "", tr("windowOpened", { name: services[service] })));
    } else ui.notice.append(n("span", "", tr("blocked")), external(tr("openTab"), destination || url(service)));
    updateWindowLabels();
  }
  function updateWindowLabels() {
    if (!ui.toolButtons) return;
    for (const [service, b] of ui.toolButtons) {
      if (service === "reddit") { b.textContent = tr("redditTab"); continue; }
      let open = false;
      try { open = !!windows.get(service) && !windows.get(service).closed; } catch (_) { /* browser isolated the window */ }
      b.textContent = tr(open ? "focus" : "window", { name: services[service] });
    }
  }
  function closePlayer() { activeDialog?.close(); }
  function play(item, trigger) {
    if (!validItem(item)) return;
    closePlayer();
    const dialog = n("dialog", "comm-player"), head = n("header", "comm-player-head"), title = n("h2", "", item.title || tr("videoFrom", { name: item.creator }));
    title.id = "community-player-title"; dialog.setAttribute("aria-labelledby", title.id);
    head.append(title, button(tr("close"), () => dialog.close())); dialog.append(head);
    const iframe = n("iframe"); iframe.title = title.textContent; iframe.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    iframe.allowFullscreen = true; iframe.referrerPolicy = "strict-origin-when-cross-origin";
    iframe.src = `https://www.youtube-nocookie.com/embed/${item.video}?autoplay=1&rel=0`;
    dialog.append(iframe, n("p", "hint", tr("playerNote")), external(tr("youtube"), item.url));
    dialog.addEventListener("keydown", event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dialog.close(); } });
    dialog.addEventListener("close", () => { iframe.remove(); dialog.remove(); if (activeDialog === dialog) activeDialog = null; trigger?.focus({ preventScroll: true }); }, { once: true });
    document.body.append(dialog); dialog.showModal(); activeDialog = dialog; remember(item);
  }
  function build() {
    imageObserver?.disconnect(); imageChecks = [];
    closePlayer(); root.replaceChildren(); preference = LANG;
    const heading = n("header", "comm-heading"), words = n("div");
    words.append(n("span", "eyebrow", tr("publicFeed")), n("h2", "", tr("heading")), n("p", "muted", tr("intro")));
    const refresh = button(tr("refresh"), () => load(true), "comm-refresh"); heading.append(words, refresh);
    const layout = n("div", "comm-layout"), content = n("section", "comm-content"), tools = n("aside", "card comm-tools");
    const searchLabel = n("label", "comm-search"), search = n("input"); search.type = "search"; search.name = "community-search"; search.value = query; search.maxLength = 200; search.placeholder = tr("search"); searchLabel.append(n("span", "sr-only", tr("search")), search);
    const tabs = n("nav", "comm-filters"); tabs.setAttribute("aria-label", tr("source"));
    for (const key of ["all", "posts", "stories", "videos", "saved"]) { const b = button(tr(key), () => { filter = key; limit = 12; persist(); drawFeed(); }); b.dataset.communityFilter = key; tabs.append(b); }
    const status = n("p", "comm-status"), count = n("p", "comm-count"), feed = n("div", "comm-feed"); status.setAttribute("role", "status"); count.setAttribute("aria-live", "polite");
    const redditHelp = n("details", "comm-reddit-help"), redditHelpActions = n("div", "comm-help-actions");
    redditHelpActions.append(external(tr("redditSignin"), "https://www.reddit.com/login/"),
      external(tr("redditSupport"), "https://support.reddithelp.com/hc/en-us/requests/new"));
    redditHelp.append(n("summary", "", tr("redditHelp")), n("p", "hint", tr("redditHelpText")), redditHelpActions);
    content.append(searchLabel, tabs, redditHelp, status, count, feed);
    const notice = n("div", "comm-notice"); notice.setAttribute("role", "status"); notice.hidden = true;
    const storage = n("p", "comm-status", tr("storage")); storage.hidden = !storageFailed;
    tools.append(n("span", "eyebrow", tr("tools")), n("p", "muted", tr("toolsNote")));
    const toolButtons = [];
    for (const [service, name] of Object.entries(services)) {
      const block = n("section", `comm-tool comm-tool-${service}`), h = n("h3", "", name);
      const launch = service === "reddit" ? redditLink(tr("redditTab"), url(service), "comm-launch") : button("", () => openWindow(service), "comm-launch"); launch.dataset.communityWindow = service; toolButtons.push([service, launch]); block.append(h);
      if (service === "discord") { const presence = n("div", "comm-presence"); block.append(presence, n("p", "hint", tr("discordNote"))); ui.presence = presence; }
      if (service === "facebook") block.append(n("p", "hint", tr("facebookNote")));
      block.append(launch);
      if (service === "vivosun") block.append(button(tr("signin"), () => openWindow("vivosun", url("vivosun", true)), "comm-signin"));
      tools.append(block);
    }
    tools.append(n("p", "hint", tr("accountNote")));
    const quick = n("nav", "comm-quick-tools"); quick.setAttribute("aria-label", tr("tools"));
    for (const [service, name] of Object.entries(services)) { const b = service === "reddit" ? redditLink(name, url(service), "") : button(name, () => openWindow(service)); b.setAttribute("aria-label", service === "reddit" ? tr("redditTab") : tr("window", { name })); quick.append(b); }
    layout.append(content, tools); root.append(heading, quick, notice, storage, layout);
    ui = { ...ui, refresh, status, count, feed, search, tabs, notice, storage, toolButtons };
    imageObserver = new ResizeObserver(checkImages); imageObserver.observe(feed);
    search.oninput = () => { query = search.value; limit = 12; persist(); drawFeed(); };
    draw();
  }
  function card(item) {
    const article = n("article", `comm-card comm-card-${item.kind}`); article.dataset.communityItem = item.id;
    const picture = cover(item, article);
    const text = n("div", "comm-card-body"), meta = n("div", "comm-card-meta");
    meta.append(n("span", "", item.creator)); if (!seen.includes(item.id)) meta.append(n("small", "comm-unread", tr("unread")));
    const title = n("h3", "", item.title || tr("videoFrom", { name: item.creator }));
    text.append(meta, title);
    if (item.source && item.source !== item.creator) text.append(n("p", "hint comm-card-source", item.source));
    if (item.published && !Number.isNaN(Date.parse(item.published))) { const date = n("time", "hint", new Date(item.published).toLocaleString(LOCALE[LANG], { dateStyle: "medium", timeStyle: "short" })); date.dateTime = item.published; text.append(date); }
    const actions = n("div", "comm-card-actions");
    const read = item.kind === "post" ? redditLink(tr("discussion"), item.url, "comm-read", item)
      : button(tr(item.kind === "video" ? "play" : "read"), () => item.kind === "video" ? play(item, read) : openWindow("vivosun", item.url, item), "comm-read");
    const saved = !!bookmarks[item.id], save = button(tr(saved ? "unsave" : "save"), () => {
      if (bookmarks[item.id]) delete bookmarks[item.id]; else { if (Object.keys(bookmarks).length >= 200) delete bookmarks[Object.keys(bookmarks)[0]]; bookmarks[item.id] = { ...item }; }
      persist(); drawFeed(); ui.feed.querySelector(`[data-community-item="${CSS.escape(item.id)}"] .comm-save`)?.focus({ preventScroll: true });
    }, "comm-save"); save.setAttribute("aria-pressed", String(saved));
    actions.append(read, save);
    if (item.kind === "post") {
      const copyStatus = n("p", "hint comm-copy-status"), field = n("input", "comm-copy-field");
      field.type = "text"; field.readOnly = true; field.value = item.url; field.hidden = true;
      field.setAttribute("aria-label", tr("copyLink")); copyStatus.setAttribute("role", "status");
      field.addEventListener("click", () => field.select());
      const copy = button(tr("copyLink"), async () => {
        try {
          if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
          await navigator.clipboard.writeText(item.url); field.hidden = true; copyStatus.textContent = tr("copied");
        } catch (_) {
          field.hidden = false; copyStatus.textContent = tr("copyManual"); field.focus(); field.select();
        }
      }, "comm-copy");
      actions.append(copy); text.append(actions, field, copyStatus);
    } else text.append(actions);
    article.append(picture, text); return article;
  }
  function drawFeed() {
    if (!ui.feed) return;
    imageChecks = [];
    let items = filter === "saved" ? Object.values(bookmarks).filter(validItem) : (data?.items || []).filter(validItem);
    if (filter === "stories") items = items.filter(item => item.kind === "story");
    if (filter === "posts") items = items.filter(item => item.kind === "post");
    if (filter === "videos") items = items.filter(item => item.kind === "video");
    const term = query.toLocaleLowerCase();
    if (term) items = items.filter(item => `${item.title} ${item.creator} ${item.source}`.toLocaleLowerCase().includes(term));
    for (const b of ui.tabs.children) b.setAttribute("aria-pressed", String(b.dataset.communityFilter === filter));
    ui.feed.replaceChildren();
    ui.count.textContent = data || filter === "saved" ? tr("resultCount", { n: items.length }) : "";
    if (items.length) { items.slice(0, limit).forEach(item => ui.feed.append(card(item))); if (items.length > limit) ui.feed.append(button(tr("more"), () => { limit += 12; drawFeed(); }, "comm-more")); }
    else if (!data && busy && filter !== "saved") { for (let i = 0; i < 3; i++) ui.feed.append(n("div", "comm-skeleton")); }
    else {
      const failed = filter === "posts" ? data?.sources?.reddit?.error : ["stories", "videos"].includes(filter) ? data?.sources?.vivosun?.error : Object.values(data?.sources || {}).some(source => source.error);
      ui.feed.append(n("p", "comm-empty", tr(filter === "saved" && !query ? "savedEmpty" : !data || failed ? "unavailable" : "noMatches")));
    }
    updateWindowLabels();
  }
  function draw() {
    if (!ui.refresh) return;
    ui.refresh.disabled = busy; ui.refresh.textContent = tr(busy ? "refreshing" : "refresh");
    const sources = [data?.sources?.vivosun, data?.sources?.reddit].filter(Boolean), at = Math.max(0, ...sources.map(source => source.at || 0)), stale = sources.some(source => source.stale);
    const when = at ? new Date(at * 1000).toLocaleString(LOCALE[LANG], { dateStyle: "medium", timeStyle: "short" }) : "";
    ui.status.textContent = !data ? tr(busy ? "loading" : "unavailable") : `${data.demo ? tr("demo") + " · " : ""}${stale ? tr("cached") : tr("sourceOrder")}${when ? " · " + tr("checked", { when }) : ""}`;
    ui.status.classList.toggle("is-stale", stale);
    ui.presence.replaceChildren();
    const counts = data?.discord;
    if (counts) {
      const numbers = n("div", "comm-presence-numbers");
      for (const [key, label] of [["online", "online"], ["members", "members"]]) { const cell = n("div"); cell.append(n("strong", "", counts[key] == null ? "?" : new Intl.NumberFormat(LOCALE[LANG]).format(counts[key])), n("small", "", tr(label))); numbers.append(cell); }
      ui.presence.append(numbers, n("p", "hint", tr("publicCounts")));
      if (data.sources?.discord?.stale) ui.presence.append(n("p", "comm-status is-stale", tr("cached")));
    } else ui.presence.append(n("p", "hint", tr("noCounts")));
    drawFeed();
  }
  async function load(force = false) {
    if (busy) return;
    if (!force && loadedLang === LANG && Date.now() - lastLoad < 600000) return;
    busy = true; draw(); const language = LANG;
    try {
      const result = await api(`/api/community${force ? "?refresh=1" : ""}`);
      if (language !== LANG) return;
      data = result; lastLoad = Date.now(); loadedLang = language;
    } catch (_) {
      if (data) data = { ...data, sources: Object.fromEntries(Object.entries(data.sources || {}).map(([key, source]) => [key, { ...source, stale: true, error: "unavailable" }])) };
      lastLoad = 0;
    } finally { busy = false; if (language === LANG) draw(); else if (visible()) load(); }
  }
  function visible() { return location.hash === "#community" && !document.hidden; }
  function route() { document.body.classList.toggle("community-on", location.hash === "#community"); if (!visible()) { closePlayer(); return; } if (!pixelFrame) watchPixelDensity(); if (preference !== LANG) build(); load(); }
  document.addEventListener("visibilitychange", route);
  addEventListener("resize", checkImages);
  addEventListener("scroll", () => { if (visible()) scrollPosition = scrollY; }, { passive: true });
  addEventListener("hashchange", () => { const previous = scrollPosition; route(); if (visible()) requestAnimationFrame(() => scrollTo(0, previous)); });
  addEventListener("storage", event => { if (event.key === "vgrow.community.desk") { try { const saved = JSON.parse(event.newValue || "{}"); if (saved.bookmarks && typeof saved.bookmarks === "object" && !Array.isArray(saved.bookmarks)) bookmarks = saved.bookmarks; seen = Array.isArray(saved.seen) ? saved.seen.slice(-500) : []; drawFeed(); } catch (_) { /* invalid other tab state */ } } });
  new MutationObserver(route).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  setInterval(() => { if (visible()) { load(); updateWindowLabels(); } }, 30000);
  route();
})();
