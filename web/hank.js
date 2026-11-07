// Hank, the grow assistant: a chat drawer, one traffic-light line above the plant status, suggestion cards.
// Loaded after app.js and uses its globals t(), tn(), api(), LANG, $, poll() and pilotStep(); it never touches render()
// or state. "Hank regelt" (owner, 23.10.2026: "I cannot set Hank to auto") is the autopilot's own mode: it reads the
// app's /api/state answer (the event "vgrow:state", autopilot.boxes[].hank, vgrow/hankpilot.py) and sends the card's
// own action (autopilot_mode); there is no second mode and no second store.
// Hank is a guest: until /api/hank/state answers, nothing of him is shown, and the cockpit runs on without him.
// Every text is a key of web/hank-i18n.js, written out in full here, so tests/test_hank_web.py can check each one.
// No markup from data: texts go in through textContent, links only with http: or https:.
(function () {
  "use strict";

  if (!window.HANK_I18N || !window.I18N) return;
  Object.assign(window.I18N.de, window.HANK_I18N.de);
  Object.assign(window.I18N.en, window.HANK_I18N.en);

  // The wiki's "Hank fragen" button (web/wiki.js hankButton): Hank answers "Erzähl mir mehr über <Titel>" for every
  // card of these folders (v1.4, vgrow/hank/wikicards.py EXPLAIN_FOLDERS, the same list in the same order; a test
  // compares them). The button stays hidden on a card of any other folder.
  window.HANK_FEATURES = Object.assign(window.HANK_FEATURES || {}, {
    explainCard: true,
    explainFolders: ["methods", "crops", "processes", "glossary", "stages", "strains", "nutrients", "legal"],
  });

  const POLL_IDLE = 15000;   // GET /api/hank/state while nothing runs
  const POLL_BUSY = 1500;    // while a question is being answered
  const SECONDS_FROM = 10;   // the typing line shows its seconds from here on
  const ARM_MS = 4000;       // a two-tap button waits this long for the second tap
  const MAX_CHARS = 500;
  const CLIENT_KEY = "vgrow.hank.client";
  const MODES = ["on", "warnings", "off"];
  const MODE_TEXT = { on: "hank.mode.on", warnings: "hank.mode.warnings", off: "hank.mode.off" };
  // "Hank regelt": the autopilot's three modes (vgrow/autopilot.py MODES), the same words as its card.
  const PILOT_MODES = ["off", "observe", "auto"];
  const PILOT_TEXT = { off: "hank.ctl.off", observe: "hank.ctl.observe", auto: "hank.ctl.auto" };
  const STAGE_TEXT = { search: "hank.typing.search", model: "hank.typing.model", check: "hank.typing.check" };
  // [label, face]: the colour is never the only signal, the line always says its level in words.
  const LIGHT = { green: ["hank.light.green", "joy"], yellow: ["hank.light.yellow", "think"], red: ["hank.light.red", "alert"] };
  const ROUTE_FACE = { greet: "joy", thanks: "joy", health_emergency: "alert" };
  // example questions only under these answers; under the strain advice the replies to Hank's own question
  // ("Autoflower", "Erster Grow"), which the server sends as its chips
  const CHIP_ROUTES = new Set(["offtopic", "greet", "empty", "strains"]);
  const VOTE_ROUTES = new Set(["grow", "explain", "strains"]);  // thumbs on growing answers, explanations, advice
  const FINE_POINTER = matchMedia("(hover: hover) and (pointer: fine)");
  // The example questions under the greeting of an empty chat (spec 9.2), the server's hank.chip.* in the same words.
  const CHIP_KEYS = ["hank.chip.1", "hank.chip.2", "hank.chip.3", "hank.chip.4"];
  // A number and its unit stay on one line ("100 %" never breaks before the "%"; screens review, finding 6).
  const NUM_UNIT = /(\d)[ \t]+(?=(?:%|\u00b0C|kPa|ml\/l|ml|mS\/cm|cm|W|\u00b5mol|l|h|min|s)(?![\w\/]))/g;
  const NBSP = "\u00a0";
  // What Hank does in a chore of web/hank-scenes.js, for the label of the drawer's avatar.
  const CHORE_TEXT = { water: "hank.chore.water", prune: "hank.chore.prune", feed: "hank.chore.feed",
    lens: "hank.chore.lens", coffee: "hank.chore.coffee", gnat: "hank.chore.gnat" };

  // ---------- the drawing ----------

  // One drawing, four expressions: the man from his garden (the portrait in web/garden-art.js), in lines instead of pixels.
  // viewBox 0..120; the hat brim gives the silhouette at small sizes. Its colours are tokens in hank.css.
  function mascot(expr, opts) {
    const o = opts || {};
    const eyes = {
      hello: '<path d="M37 66 L53 64.5 V77 H40.5 Z" fill="var(--eye-white)"/><path d="M43 64.9 L52.2 64.6 V77 H43 Z" fill="var(--iris)"/><path d="M45.3 64.8 L51.4 64.6 V74.5 H45.3 Z" fill="var(--eye)"/><rect x="46" y="66" width="2.8" height="2.8" fill="#fff"/><rect x="49.4" y="70.4" width="1.6" height="1.6" fill="#fff"/><path d="M35 67.5 L37.5 64.5 L54 62.8" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 64.5 L83 66 L79.5 77 H67 Z" fill="var(--eye-white)"/><path d="M68.8 64.6 L78 64.9 V77 H68.8 Z" fill="var(--iris)"/><path d="M69.6 64.6 L75.7 64.8 V74.5 H69.6 Z" fill="var(--eye)"/><rect x="70.3" y="66" width="2.8" height="2.8" fill="#fff"/><rect x="73.9" y="70.4" width="1.6" height="1.6" fill="#fff"/><path d="M66 62.8 L82.5 64.5 L85 67.5" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      think: '<path d="M37 66 L53 64.5 V77 H40.5 Z" fill="var(--eye-white)"/><path d="M44.4 64.30000000000001 L53.6 63.99999999999999 V77 H44.4 Z" fill="var(--iris)"/><path d="M46.699999999999996 64.2 L52.8 63.99999999999999 V73.9 H46.699999999999996 Z" fill="var(--eye)"/><rect x="47.4" y="65.4" width="2.8" height="2.8" fill="#fff"/><rect x="50.8" y="69.80000000000001" width="1.6" height="1.6" fill="#fff"/><path d="M35 67.5 L37.5 64.5 L54 62.8" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 64.5 L83 66 L79.5 77 H67 Z" fill="var(--eye-white)"/><path d="M70.2 63.99999999999999 L79.4 64.30000000000001 V77 H70.2 Z" fill="var(--iris)"/><path d="M71.0 63.99999999999999 L77.10000000000001 64.2 V73.9 H71.0 Z" fill="var(--eye)"/><rect x="71.7" y="65.4" width="2.8" height="2.8" fill="#fff"/><rect x="75.30000000000001" y="69.80000000000001" width="1.6" height="1.6" fill="#fff"/><path d="M66 62.8 L82.5 64.5 L85 67.5" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      alert: '<path d="M37 64 L53 62.5 V78 H40 Z" fill="var(--eye-white)"/><path d="M45.5 67 H50.5 V74 H45.5 Z" fill="var(--eye)"/><rect x="46.2" y="67.8" width="1.8" height="1.8" fill="#fff"/><path d="M35.5 65.5 L37.5 62.5 L54 61" stroke="var(--eye)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 62.5 L83 64 L80 78 H67 Z" fill="var(--eye-white)"/><path d="M69.5 67 H74.5 V74 H69.5 Z" fill="var(--eye)"/><rect x="70.2" y="67.8" width="1.8" height="1.8" fill="#fff"/><path d="M66 61 L82.5 62.5 L84.5 65.5" stroke="var(--eye)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      joy: '<path d="M38 72.5 L45.5 65.5 L53 72.5" stroke="var(--eye)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 72.5 L74.5 65.5 L82 72.5" stroke="var(--eye)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    }[expr];
    const brows = {
      hello: '<path d="M37.5 61 L53 60" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 60 L82.5 61" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      think: '<path d="M38 61 L53 60" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 57.5 L82 56.8" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      alert: '<path d="M37 62 L53 57.8" stroke="var(--beard)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 57.8 L83 62" stroke="var(--beard)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      joy: '<path d="M38 59 L53 58" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 58 L82 59" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    }[expr];
    const mouth = {
      hello: '<path d="M52 91.5 L60 94.2 L68 91.5" stroke="var(--mouth)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      think: '<ellipse cx="62" cy="93.2" rx="2.4" ry="2.6" fill="var(--mouth)"/>',
      alert: '<path d="M53 93.2 H67" stroke="var(--mouth)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
      joy: '<path d="M50 90 H70 L65 97.5 H55 Z" fill="var(--mouth)"/><path d="M51.5 90.4 H68.5 L67.4 92.2 H52.6 Z" fill="#fff"/><path d="M55.5 97 L60 94.4 L64.5 97 Z" fill="#e8826b"/>',
    }[expr];
    const extra = {
      hello: '',
      think: '<circle cx="99" cy="31" r="2.6" fill="var(--muted)"/><circle cx="105" cy="24" r="3.3" fill="var(--muted)"/><circle cx="111" cy="15" r="4.2" fill="var(--muted)"/>',
      alert: '<circle cx="104" cy="20" r="11" fill="var(--warn)"/><path d="M104 13.5 V21.5" stroke="#fff" stroke-width="3.2" stroke-linecap="round"/><circle cx="104" cy="26.5" r="1.9" fill="#fff"/>',
      joy: '<path d="M14 26 l2.4 5.6 5.6 2.4 -5.6 2.4 -2.4 5.6 -2.4 -5.6 -5.6 -2.4 5.6 -2.4z" fill="var(--straw)"/><path d="M106 22 l1.8 4.2 4.2 1.8 -4.2 1.8 -1.8 4.2 -1.8 -4.2 -4.2 -1.8 4.2 -1.8z" fill="var(--accent)"/>',
    }[expr];
    const label = o.label || "Hank";
    return `<svg viewBox="0 0 120 120" role="img" aria-label="${label}"><g class="${o.bob ? "bob" : ""}">` +
      // shirt and neckerchief
      '<path d="M27 120 C29 103 43 97 60 97 C77 97 91 103 93 120 Z" fill="var(--shirt)"/>' +
      '<path d="M36 106 H84 M33 113 H87 M47 99 V120 M73 99 V120" stroke="var(--shirt-line)" stroke-width="1.6" opacity=".7"/>' +
      '<path d="M51 86 H69 V100 L60 104 L51 100 Z" fill="var(--skin-shade)"/>' +
      '<path d="M44 97 L60 103 L76 97 L80 108 L60 119 L40 108 Z" fill="var(--band)" stroke="var(--hk-line)" stroke-width="1.4" stroke-linejoin="round"/>' +
      '<path d="M60 103 L76 97 L80 108 L60 119 Z" fill="var(--band-dark)"/>' +
      // hair behind, ears, face, stubble on the jaw
      '<path d="M25.5 57.7 L20.5 80.6 L28 75.5 L28 90.8 L38.3 85.7 L38.3 52.6 Z" fill="var(--hair-dark)"/><path d="M94.5 57.7 L99.5 78 L92 75.5 L92 85.7 L81.7 83.2 L81.7 52.6 Z" fill="var(--hair-dark)"/>' +
      '<ellipse cx="29.5" cy="71" rx="4.6" ry="7" fill="var(--skin-shade)"/><ellipse cx="90.5" cy="71" rx="4.6" ry="7" fill="var(--skin-shade)"/>' +
      '<path d="M31 50 H89 L89.5 70 L84 86 L71.5 98.5 H48.5 L36 86 L30.5 70 Z" fill="var(--skin)" stroke="var(--hk-line)" stroke-width="2" stroke-linejoin="round"/>' +
      '<path d="M88.5 51 L88.6 70 L83.2 85.5 L71 97.5 H64 L77 81 L80 51 Z" fill="var(--skin-shade)"/>' +
      '<path d="M36.8 85.8 L48.9 97.5 H71.1 L83.3 85.8 L77 83 L70 91.5 H50 L43 83 Z" fill="var(--stubble)"/>' +
      '<path d="M31 56 H89 L89.3 63.5 H30.7 Z" fill="var(--skin-shade)" opacity=".55"/>' +
      // the fringe, and the spikes the hat hides
      '<path d="M29 57 L32 44 L39 47 L44 35 L52 43 L60 32 L67 43 L76 35 L81 47 L88 44 L91 57 Z" fill="var(--hair)"/>' +
      '<path d="M28 50 H90 V60.2 L79.1 55.1 L74 62.8 L66.4 52.6 L58.7 62.8 L51.1 52.6 L43.4 62.8 L38.3 55.1 L30.7 65.3 Z" fill="var(--hair)"/>' +
      '<path d="M28 50 H46 L38.3 55.1 L30.7 65.3 Z" fill="var(--hair-dark)"/>' +
      '<path d="M58.7 52.6 V62.8 M74 53 V62.8" stroke="var(--hair-dark)" stroke-width="1.4"/>' +
      '<path d="M60.5 70 L62 80 L58.5 80.8" stroke="var(--skin-shade)" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' +
      eyes + brows + mouth +
      '<path d="M39.6 88.3 L49.8 80.6 L60 83.2 L70.2 80.6 L80.4 88.3 L70.2 88.3 L60 85.7 L49.8 88.3 Z" fill="var(--beard)" stroke="var(--hk-line)" stroke-width="1" stroke-linejoin="round"/>' +
      // straw hat: silhouette, light, shade, band, leaf
      '<path d="M7.7 52.6 L33.2 44.9 L38.3 24.5 L58.7 19.4 L81.7 24.5 L86.8 44.9 L112.3 52.6 L89.3 57.7 H28.1 Z" fill="var(--straw)" stroke="var(--hk-line)" stroke-width="2.4" stroke-linejoin="round"/>' +
      '<path d="M38.8 25 L58.7 20.4 L63.8 20.4 L53.6 47.5 H33.8 Z" fill="var(--straw-hi)"/><path d="M8.8 52.4 L33.5 45.4 H58.7 L48.5 52.4 Z" fill="var(--straw-hi)"/>' +
      '<path d="M74 23 L81 25 L85.8 47.5 H76.6 Z" fill="var(--straw-mid)"/><path d="M28.3 56.9 H89.2 L102 54.6 H18.5 Z" fill="var(--straw-dark)"/>' +
      '<path d="M33.6 39.8 H86 L87 47.5 H33 Z" fill="var(--band)"/><path d="M71.5 39.8 H86 L87 47.5 H71.5 Z" fill="var(--band-dark)"/>' +
      '<path d="M68.9 37.3 L81.7 27 L86.8 34.7 L76.6 39.8 Z" fill="var(--hat-leaf)"/><path d="M70 37 L83 31" stroke="var(--leaf-dark)" stroke-width="1.2"/>' +
      extra + "</g></svg>";
  }

  // Small line icons, drawn here (not part of the mascot).
  const ICON = {
    close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>',
    send: '<svg viewBox="0 0 24 24"><path d="M4 12l16-8-6 16-2.5-6.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" fill="none"/></svg>',
    up: '<svg viewBox="0 0 24 24"><path d="M7 10.5V20H4v-9.5zM9.5 20h7.6a2 2 0 0 0 2-1.6l1.2-6a2 2 0 0 0-2-2.4H14l.7-3.6A1.8 1.8 0 0 0 11.4 5L9.5 10.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" fill="none"/></svg>',
    go: '<svg viewBox="0 0 24 24"><path d="M9 5.5l6.5 6.5L9 18.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>',
    down: '<svg viewBox="0 0 24 24"><g transform="rotate(180 12 12.5)"><path d="M7 10.5V20H4v-9.5zM9.5 20h7.6a2 2 0 0 0 2-1.6l1.2-6a2 2 0 0 0-2-2.4H14l.7-3.6A1.8 1.8 0 0 0 11.4 5L9.5 10.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" fill="none"/></g></svg>',
  };

  // The drawing and the icons are fixed markup in this file, never data: parsed once, as the HTML parser would.
  const PARSER = new DOMParser();
  function svgFrom(markup) {
    const svg = document.importNode(PARSER.parseFromString(markup, "text/html").body.firstElementChild, true);
    svg.removeAttribute("role");
    svg.removeAttribute("aria-label");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    return svg;
  }
  function face(expr) { return svgFrom(mascot(expr)); }

  // The scenes (web/hank-scenes.js) are a guest of Hank: without them, or if one fails, the still faces stay.
  // loops: how often a state scene repeats before it holds still (spec 9.2: scenes run "selten und kurz").
  function mountScene(host, size, onScene, loops) {
    if (!window.HankScenes) return null;
    try { return window.HankScenes.mount(host, { size, onScene, loops }); } catch (_) { return null; }
  }
  const LOOPS_LIGHT = 1;  // the light line is always on screen: one round, then the still frame
  const LOOPS_HEAD = 3;   // the open drawer: a little longer, then still as well

  // ---------- small helpers ----------

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // A text from a key, kept on the element so a language switch can write it again.
  function say(node, key) {
    node.dataset.hk = key;
    node.textContent = t(key);
    return node;
  }
  function sayAttr(node, attr, key) {
    const kept = (node.dataset.hkAttr || "").split(";").filter((pair) => pair && !pair.startsWith(`${attr}:`));
    node.dataset.hkAttr = [...kept, `${attr}:${key}`].join(";");
    node.setAttribute(attr, t(key));
    return node;
  }
  function retext(root) {
    for (const node of root.querySelectorAll("[data-hk]")) node.textContent = t(node.dataset.hk);
    for (const node of root.querySelectorAll("[data-hk-attr]")) {
      for (const pair of node.dataset.hkAttr.split(";")) {
        const at = pair.indexOf(":");
        if (at > 0) node.setAttribute(pair.slice(0, at), t(pair.slice(at + 1)));
      }
    }
  }

  function button(cls, key) {
    const b = el("button", cls);
    b.type = "button";
    if (key) say(b, key);
    return b;
  }

  function safeUrl(url) {
    return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
  }

  function clientId() {
    try {
      let id = localStorage.getItem(CLIENT_KEY);
      if (!id) {
        id = `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
        localStorage.setItem(CLIENT_KEY, id);
      }
      return id;
    } catch (_) {  // blocked storage: one id for this page load
      return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    }
  }

  function message(err) { return (err && err.message) || String(err); }

  // ---------- state ----------

  const H = {
    built: false, mode: null, open: false, failed: false,
    client: clientId(),
    jobs: new Map(),       // job id -> { row, bubble, draft }
    lastHint: null,        // the newest note id in the chat (POST /api/hank/ack takes it)
    unread: 0,
    scene: { id: null, text: "", level: "" },  // the server's scene (GET /api/hank/state) and the light's level
    stateTimer: null, jobTimer: null, lightSig: "",
    light: null,           // the last light from GET /api/hank/state: its level and problem keys for a tap
    lightRaw: null,        // that light as it came, to draw it again when the autopilot's answer changes
    app: null,             // the app's last /api/state answer (event "vgrow:state")
    pilot: null,           // its first box with Hank's panel: { box, mode, blockers, regulates, last, about, ... }
    pilotSig: "", pilotArm: 0, pilotTimer: null,
  };
  const ui = {};

  // ---------- building ----------

  function build() {
    const root = el("div", "hk-scope hk-root");
    root.id = "hank";

    const fab = button("hk-fab");
    fab.setAttribute("aria-haspopup", "dialog");
    fab.setAttribute("aria-expanded", "false");
    fab.setAttribute("aria-controls", "hk-drawer");
    sayAttr(fab, "aria-label", "hank.fab");
    fab.append(face("hello"), el("span", "hk-badge"));
    fab.lastChild.hidden = true;
    fab.onclick = () => (H.open ? close() : open());

    const wake = button("hk-wake", "hank.mode.turnOn");
    wake.hidden = true;
    wake.onclick = () => setMode("on");

    const drawer = el("section", "hk-drawer");
    drawer.id = "hk-drawer";
    drawer.hidden = true;
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "false");
    drawer.setAttribute("aria-labelledby", "hk-title");
    const backdrop = el("div", "hk-backdrop");
    backdrop.hidden = true;
    backdrop.setAttribute("aria-hidden", "true");
    backdrop.onclick = close;

    const head = el("div", "hk-head");
    const headFace = el("span", "hk-avatar");
    headFace.append(face("hello"));
    const headScene = mountScene(headFace, "head", sceneLabel, LOOPS_HEAD);
    const title = say(el("h2", "hk-title"), "hank.drawer.title");
    title.id = "hk-title";
    title.tabIndex = -1;
    const shut = button("hk-close");
    sayAttr(shut, "aria-label", "hank.drawer.close");
    shut.append(svgFrom(ICON.close));
    shut.onclick = close;

    // Mode controls sit below the permanent title/close row, with their explanation right below.
    const seg = el("div", "seg small hk-seg");
    seg.setAttribute("role", "group");
    sayAttr(seg, "aria-label", "hank.mode.label");
    seg.setAttribute("aria-describedby", "hk-mode-hint");
    for (const m of MODES) {
      const b = button("", MODE_TEXT[m]);
      b.dataset.mode = m;
      b.onclick = () => (m === "off" ? offTapped(b) : setMode(m));
      seg.append(b);
    }
    head.append(headFace, title, shut);
    // What "off" means shows when "aus" is tapped, and a second tap switches off (screens review, finding 11: the
    // sentence took about 100 px of the phone sheet in every mode).
    const modeHint = el("p", "hk-mode-hint");
    modeHint.id = "hk-mode-hint";
    modeHint.hidden = true;
    modeHint.setAttribute("aria-live", "polite");
    modeHint.append(say(el("span"), "hank.mode.hintOff"), document.createTextNode(" "),
      say(el("b"), "hank.proposal.confirm"));
    // "Hank regelt" right under the chat switch: the autopilot's mode, what holds Hank back, what he regulates now.
    const pilot = el("div", "hk-pilot");
    pilot.hidden = true;
    const pilotRow = el("div", "hk-pilot-row");
    const pilotLabel = say(el("span", "hk-pilot-l"), "hank.ctl.label");
    pilotLabel.id = "hk-pilot-l";
    const pilotSeg = el("div", "seg small hk-seg hk-pilot-seg");
    pilotSeg.setAttribute("role", "group");
    pilotSeg.setAttribute("aria-labelledby", "hk-pilot-l");
    pilotSeg.setAttribute("aria-describedby", "hk-pilot-need");
    for (const m of PILOT_MODES) {
      const b = button("", PILOT_TEXT[m]);
      b.dataset.pilot = m;
      b.onclick = () => pilotTapped(m);
      pilotSeg.append(b);
    }
    pilotRow.append(pilotLabel, pilotSeg);
    const pilotNeed = say(el("p", "hk-pilot-need"), "hank.ctl.need");
    pilotNeed.id = "hk-pilot-need";
    pilotNeed.setAttribute("aria-live", "polite");
    const pilotBody = el("div", "hk-pilot-body");
    const pilotFb = el("p", "hk-pilot-fb");
    pilotFb.hidden = true;
    const protocolLink = say(el("a", "text-link"), "hank.ctl.protocol");
    protocolLink.href = "#activity/protocol"; protocolLink.onclick = () => close();
    pilot.append(pilotRow, pilotNeed, pilotBody, pilotFb, protocolLink);
    const top = el("div", "hk-top");
    top.append(seg, modeHint, pilot);

    const err = say(el("p", "hk-err"), "hank.error.state");
    err.hidden = true;

    const log = el("div", "hk-log");
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");

    const form = el("form", "hk-foot");
    const q = el("textarea", "hk-q");
    q.id = "hk-q";
    q.rows = 1;
    q.maxLength = MAX_CHARS;
    q.setAttribute("enterkeyhint", "send");
    sayAttr(q, "placeholder", "hank.input.placeholder");
    sayAttr(q, "aria-label", "hank.input.label");
    const send = el("button", "btn primary hk-send");
    send.type = "submit";
    sayAttr(send, "aria-label", "hank.send");
    send.append(svgFrom(ICON.send));
    form.append(q, send);
    const hint = say(el("p", "hk-hint"), "hank.hint");

    drawer.append(head, top, err, log, form, hint);
    root.append(fab, wake, backdrop, drawer);
    document.body.append(root);
    document.body.classList.add("has-hank");  // room at the bottom of the page for the button

    form.onsubmit = (e) => { e.preventDefault(); submit(); };
    q.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    });
    q.addEventListener("input", grow);
    document.addEventListener("keydown", (e) => {
      if (!H.open || e.defaultPrevented || document.querySelector("dialog[open]")) return;
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key !== "Tab" || ui.drawer.getAttribute("aria-modal") !== "true") return;
      const items = [...ui.drawer.querySelectorAll("button, a[href], input, textarea, select, [tabindex='0']")]
        .filter((n) => !n.disabled && n.getClientRects().length);
      const first = items[0], last = items[items.length - 1];
      if (!first) return;
      if (!ui.drawer.contains(document.activeElement) || (e.shiftKey && document.activeElement === first)) {
        e.preventDefault(); (e.shiftKey ? last : first).focus();
      } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    window.addEventListener("resize", syncDrawerLayout);

    // The traffic light: one line above the plant status, styled like it, never a numbered card. The line itself is
    // a button (owner, 23.10.2026: "I need to be able to click on it. And also to fix the problem."): a tap opens the
    // drawer with "Was kann ich dagegen tun?" and Hank's fix answer for exactly the problems of the line.
    const light = el("div", "statusline hk-scope hk-light");
    light.hidden = true;
    light.setAttribute("role", "status");
    sayAttr(light, "aria-label", "hank.light.label");
    const lightBtn = button("hk-light-btn");
    sayAttr(lightBtn, "title", "hank.light.open");
    lightBtn.onclick = lightTapped;
    light.append(lightBtn);
    const age = typeof $ === "function" ? ($("hank-tip-slot") || $("age")) : null;
    if (age) age.after(light);
    // The light's small stage is made once and kept, so a new sentence never restarts the scene.
    const lightArt = el("span", "hk-light-art");
    const lightScene = mountScene(lightArt, "mini", null, LOOPS_LIGHT);
    if (lightScene) light.classList.add("hk-has-sc");

    Object.assign(ui, { root, fab, wake, backdrop, drawer, title, seg, err, log, q, send, light, lightBtn, modeHint,
      headFace, headScene, lightArt, lightScene, pilot, pilotSeg, pilotNeed, pilotBody, pilotFb });
    H.built = true;
  }

  function grow() {
    const q = ui.q;
    q.style.height = "auto";
    q.style.height = `${Math.min(q.scrollHeight, 132)}px`;
  }

  // ---------- drawer ----------

  function syncDrawerLayout() {
    if (!ui.drawer) return;
    document.body.classList.toggle("hank-open", H.open);
    ui.backdrop.hidden = !H.open;
    ui.drawer.setAttribute("aria-modal", String(H.open && !matchMedia("(min-width: 1280px)").matches));
  }

  function open() {
    if (window.PlantWorkspace?.assistantAllowed() === false) return;
    if (H.mode === "off") return;
    H.open = true;
    ui.drawer.hidden = false;
    ui.fab.setAttribute("aria-expanded", "true");
    ui.root.classList.add("is-open");
    syncDrawerLayout();
    window.dispatchEvent(new Event("resize"));  // charts and feeding tables follow the available width
    if (!ui.log.querySelector(".hk-hello")) {  // the greeting comes first, above any note that arrived before
      const { row, bubble } = botRow("hello", "hk-hello");
      bubble.append(say(el("p", "hk-text"), H.mode === "warnings" ? "hank.mode.warningsChat" : "hank.empty"));
      if (!ui.log.querySelector(".hk-me")) {  // spec 9.2: example questions while the chat is empty
        const box = keyChips(CHIP_KEYS);
        box.classList.add("hk-hello-chips");
        box.hidden = H.mode === "warnings";
        bubble.append(box);
      }
      ui.log.prepend(row);
    }
    ack();
    badge();
    syncScenes();
    scrollDown(true);
    // With a mouse the field takes the focus; on a phone the keyboard would cover the answers, so the title does.
    (FINE_POINTER.matches ? ui.q : ui.title).focus({ preventScroll: true });
  }

  function close() {
    if (!H.open) return;
    H.open = false;
    ui.drawer.hidden = true;
    ui.fab.setAttribute("aria-expanded", "false");
    ui.root.classList.remove("is-open");
    syncDrawerLayout();
    window.dispatchEvent(new Event("resize"));
    syncScenes();
    if (!ui.fab.hidden) ui.fab.focus({ preventScroll: true });
  }

  function scrollDown(force) {
    const log = ui.log;
    if (force || log.scrollHeight - log.scrollTop - log.clientHeight < 120) log.scrollTop = log.scrollHeight;
  }

  // A new answer: a short one shows whole at the bottom, a long one from its first line, with the question it
  // answers just above it when that fits (a tap on the light shows "Was kann ich dagegen tun?" there).
  function showRow(row) {
    const log = ui.log;
    if (row.offsetHeight > log.clientHeight - 16) {
      const asked = row.previousElementSibling;
      const lead = asked && asked.classList.contains("hk-me") && row.offsetTop - asked.offsetTop < log.clientHeight / 3
        ? asked : row;
      log.scrollTop = lead.offsetTop - 8;
    } else log.scrollTop = log.scrollHeight;
  }

  function userRow(text) {
    const row = el("div", "hk-msg hk-me");
    row.append(el("div", "hk-bubble", text));
    ui.log.append(row);
    scrollDown(true);
    return row;
  }

  function botRow(expr, cls) {
    const row = el("div", `hk-msg hk-bot${cls ? ` ${cls}` : ""}`);
    const av = el("span", "hk-avatar");
    av.append(face(expr || "hello"));
    const bubble = el("div", "hk-bubble");
    row.append(av, bubble);
    ui.log.append(row);
    scrollDown(false);
    return { row, bubble };
  }

  function setFace(row, expr) {
    const av = row.querySelector(".hk-avatar");
    if (av) av.replaceChildren(face(expr));
  }

  // ---------- typing line: looking up, writing, checking; seconds only from 10 s on ----------

  function typingLine(stage, seconds) {
    const line = el("p", "hk-typing");
    const dots = el("span", "hk-dots");
    dots.setAttribute("aria-hidden", "true");
    dots.append(el("i"), el("i"), el("i"));
    line.append(dots, say(el("span", "hk-typing-t"), STAGE_TEXT[stage] || "hank.typing.search"));
    if (seconds >= SECONDS_FROM) line.append(el("span", "hk-secs", t("hank.typing.seconds", { s: Math.round(seconds) })));
    return line;
  }

  function showTyping(bubble, stage, seconds) {
    const old = bubble.querySelector(".hk-typing");
    const line = typingLine(stage, seconds);
    const body = bubble.querySelector(".hk-body");
    if (old) old.replaceWith(line);
    else if (body) body.after(line);  // under a draft: right below its text, above cards and thumbs
    else bubble.append(line);
  }

  // ---------- answers ----------

  // Source numbers as the server writes them: [1], and several in one bracket, [2, 3].
  const REFS = /\[(\d+(?:,\s*\d+)*)\]/;
  function textWithRefs(text) {
    const box = el("div", "hk-text");
    for (const para of String(text || "").split(/\n+/)) {
      if (!para.trim()) continue;
      const p = el("p");
      const parts = para.split(REFS);
      parts.forEach((part, i) => {
        if (i % 2) p.append(el("sup", "hk-ref", `[${part}]`));
        else if (part) p.append(document.createTextNode(part.replace(NUM_UNIT, `$1${NBSP}`)));
      });
      box.append(p);
    }
    return box;
  }

  function shownSources(sources) { return (sources || []).filter((s) => s && s.title); }

  // offset: the folded part numbers its sources on from the bubble's ([4] after [1] to [3])
  function sourceList(sources, offset) {
    const list = shownSources(sources);
    if (!list.length) return null;
    const box = el("div", "hk-src");
    box.append(say(el("b", "hk-src-h"), "hank.sources"));
    const ol = el("ol");
    if (offset) ol.start = offset + 1;
    // numbered by position; a code answer from a card (v1.4: method cards, "Tell me more") cites "(M18, ...)" in its
    // text, so its titles start with that id ("M18: ...") and a reader finds the source a sentence names
    for (const s of list) {
      const li = el("li");
      const url = safeUrl(s.url);
      if (url) {
        const a = el("a", "", s.title);
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        li.append(a);
      } else {
        li.textContent = s.title;
      }
      ol.append(li);
    }
    box.append(ol);
    return box;
  }

  // The off-topic reply carries its example questions in the text too (for the plain text of the eval run);
  // under the bubble they are buttons, so the text ends before them and nothing shows twice.
  function withoutChips(text, list) {
    const s = String(text || "");
    const tail = (list || []).join(" ");
    return tail && s.endsWith(` ${tail}`) ? s.slice(0, -tail.length - 1) : s;
  }

  // The part of a bubble that a revised version replaces: the note, the text, the sources.
  // The server's notes ("The model is not answering right now. Here are the facts from my card file:") lead in.
  function answerBody(a, opts) {
    const o = opts || {};
    const body = el("div", "hk-body");
    if (a.note && !o.noNote) body.append(el("p", "hk-note", a.note));
    const shown = CHIP_ROUTES.has(a.route) ? withoutChips(a.text, a.chips) : a.text;
    body.append(textWithRefs(shown));
    const src = sourceList(a.sources);
    if (src) body.append(src);
    const more = moreBlock(a.more, (a.sources || []).length);  // compose numbers every source of the bubble
    if (more) body.append(more);
    return body;
  }

  // The rest of the card file, folded away (owner rule "nicht ueberladen"): the bubble keeps the answer, a native
  // details element keeps the rest one tap away, with its own numbered sources.
  function moreBlock(more, offset) {
    if (!more || !more.text) return null;
    const box = el("details", "hk-more");
    box.append(say(el("summary", "hk-more-h"), "hank.more"));
    box.append(textWithRefs(more.text));
    const src = sourceList(more.sources, offset);
    if (src) box.append(src);
    return box;
  }

  function renderAnswer(row, bubble, a, opts) {
    const o = opts || {};
    setFace(row, ROUTE_FACE[a.route] || "hello");
    bubble.replaceChildren(answerBody(a));
    // the "facts only" line belongs to growing answers (a fixed reply never involves the model anyway), and a note
    // of the server already says why there is no model
    if (a.draft_only && !o.pending && !a.note && VOTE_ROUTES.has(a.route)) {
      bubble.append(say(el("p", "hk-meta"), "hank.draft.note"));
    }
    if (a.plan) { const grid = planGrid(a.plan); if (grid) bubble.append(grid); }
    for (const p of a.proposals || []) bubble.append(proposalCard(p));
    if (a.link) { const go = pageLink(a.link); if (go) bubble.append(go); }
    if (a.clarify) bubble.append(clarifyButtons(a));
    if (CHIP_ROUTES.has(a.route) && (a.chips || []).length) bubble.append(chips(a.chips));
    if (VOTE_ROUTES.has(a.route) && a.id) bubble.append(thumbs(a.id));
    bubble.dataset.answer = a.id || "";
    if (!o.quiet) showRow(row);
  }

  // The feeding plan as a table under the bubble (owner, 23.10.2026: "ich nutze <Dünger>", "Wann dünge ich was?"):
  // weeks across, products down, every text written by the server (feedplan.plan_graphic: the amounts as the planner
  // rounded them, or before that only which product is due, said in the caption). Today's column, plain water, flush and empty
  // weeks are marked. The table scrolls inside the bubble, never the page; no markup from data.
  function planGrid(plan) {
    if (!plan || !Array.isArray(plan.head) || !plan.head.length || !Array.isArray(plan.rows)) return null;
    const box = el("figure", "hk-plan");
    const cap = el("figcaption", "hk-plan-cap");
    cap.append(el("b", "", String(plan.title || "")), el("span", "", String(plan.caption || "")));
    for (const note of Array.isArray(plan.notes) ? plan.notes : []) cap.append(el("span", "hk-plan-note", String(note)));
    const wrap = el("div", "hk-plan-scroll");
    wrap.tabIndex = 0;
    const table = el("table", "hk-plan-t");
    const hr = table.createTHead().insertRow();
    const corner = el("th", "hk-plan-p", String(plan.product || ""));
    corner.scope = "col";
    hr.append(corner);
    let nowCell = null;
    for (const h of plan.head) {
      const th = el("th", `hk-plan-h k-${String(h.kind || "feed").replace(/[^a-z]/g, "")}${h.now ? " now" : ""}`);
      th.scope = "col";
      th.append(el("span", "hk-plan-w", String(h.label || "")));
      if (h.now) th.append(el("span", "hk-plan-today", String(plan.today || "")));
      if (h.sub) th.append(el("small", "", String(h.sub)));
      if (h.mark) th.append(el("span", "hk-plan-mark", String(h.mark)));
      hr.append(th);
      if (h.now && !nowCell) nowCell = th;
    }
    const body = table.createTBody();
    for (const r of plan.rows) {
      const tr = body.insertRow();
      const th = el("th", "hk-plan-p", String(r.label || ""));
      th.scope = "row";
      tr.append(th);
      (Array.isArray(r.cells) ? r.cells : []).forEach((c, i) => {
        const h = plan.head[i] || {};
        const td = el("td", `k-${String(h.kind || "feed").replace(/[^a-z]/g, "")}${h.now ? " now" : ""}`);
        const text = c && c.text ? String(c.text) : "";
        td.append(el("span", "", text || "–"));
        if (c && c.sub) td.append(el("small", "", String(c.sub)));
        tr.append(td);
      });
    }
    wrap.append(table);
    box.append(cap, wrap);
    return box;
  }

  // A link to a part of the page (the Autopilot card): only a same-page anchor, never a URL. The drawer closes so
  // the card is in view; Hank switches nothing, the owner does it there.
  function pageLink(link) {
    // a section of the cockpit (#autopilot) or a page of the wiki (#wiki/sorten, the strain lexicon)
    // and since 23.10.2026 a card of the wiki (#wiki/nutrients/canna-terra, the fertilizer Hank names)
    if (!link || typeof link.href !== "string" || !/^#[a-z][a-z0-9-]*(?:\/[a-z0-9-]+){0,2}$/.test(link.href)) return null;
    const go = el("a", "btn sm hk-link", String(link.label || link.href));
    go.href = link.href;
    go.addEventListener("click", () => close());
    return go;
  }

  // A revised version from the model replaces only the text, with a small mark; cards and thumbs stay.
  function reviseAnswer(row, bubble, a) {
    const body = bubble.querySelector(".hk-body");
    if (!body) { renderAnswer(row, bubble, a); return; }
    body.replaceWith(answerBody(a));
    for (const node of bubble.querySelectorAll(".hk-meta, .hk-typing")) node.remove();
    const mark = say(el("p", "hk-meta hk-revised"), "hank.revised");
    bubble.querySelector(".hk-body").after(mark);
  }

  function finish(job, a) {
    if (!a || typeof a !== "object") { failed(job, new Error("no answer")); return; }
    if (!job.draft) { renderAnswer(job.row, job.bubble, a); return; }
    if (!a.draft_only) { reviseAnswer(job.row, job.bubble, a); return; }
    keepDraft(job);  // the model's version did not pass: the answer from the code stays as it is
  }

  function keepDraft(job) {
    const line = job.bubble.querySelector(".hk-typing");
    if (line) line.remove();
    if (!job.bubble.querySelector(".hk-meta")) {
      job.bubble.querySelector(".hk-body").after(say(el("p", "hk-meta"), "hank.draft.note"));
    }
  }

  function failed(job, err) {
    setFace(job.row, "alert");
    job.bubble.replaceChildren(el("p", "hk-text hk-bad", t("hank.error.ask", { error: message(err) })));
  }

  function chips(list) {
    const box = el("div", "hk-chips");
    for (const text of list) {
      const c = el("button", "hk-chip", text);
      c.type = "button";
      c.onclick = () => ask(text);
      box.append(c);
    }
    return box;
  }

  // The greeting's example questions: keys, so a language switch writes them again; asked in the current words.
  function keyChips(keys) {
    const box = el("div", "hk-chips");
    for (const key of keys) {
      const c = say(el("button", "hk-chip"), key);
      c.type = "button";
      c.onclick = () => ask(t(key));
      box.append(c);
    }
    return box;
  }

  function clarifyButtons(a) {
    const box = el("div", "hk-actions");
    const id = (a.clarify && a.clarify.id) || a.id;
    for (const [answer, key] of [["yes", "hank.clarify.yes"], ["no", "hank.clarify.no"]]) {
      const b = button(answer === "yes" ? "btn primary sm" : "btn sm", key);
      b.onclick = () => {
        for (const x of box.querySelectorAll("button")) x.disabled = true;
        userRow(t(key));
        run("/api/hank/clarify", { id, answer, client_id: H.client, lang: LANG });
      };
      box.append(b);
    }
    return box;
  }

  function thumbs(answerId) {
    const box = el("div", "hk-thumbs");
    for (const [vote, key] of [["up", "hank.thumb.up"], ["down", "hank.thumb.down"]]) {
      const b = button("hk-thumb");
      sayAttr(b, "aria-label", key);
      sayAttr(b, "title", key);
      b.append(svgFrom(ICON[vote]));
      b.onclick = async () => {
        for (const x of box.querySelectorAll("button")) x.disabled = true;
        try {
          await api("/api/hank/feedback", { answer_id: answerId, vote });
          box.replaceChildren(say(el("span", "hk-meta"), "hank.feedback.thanks"));
        } catch (err) {
          for (const x of box.querySelectorAll("button")) x.disabled = false;
          box.append(el("span", "hk-meta hk-bad", t("hank.error.ask", { error: message(err) })));
        }
      };
      box.append(b);
    }
    return box;
  }

  // ---------- suggestion cards: a tap sends the ordinary /api/cmd with every lock of the cockpit ----------

  // HTTP acceptance is not device confirmation. Follow the ordinary state reports without sending another command.
  const commandWatches = new Map();
  function commandTarget(body, snap) {
    switch (body.action) {
      case "light": return { field: "light.level", matches: snap?.light?.level === body.level };
      case "dfan": return { field: "dFan.level", matches: snap?.dfan?.step === body.step };
      case "cfan": return { field: "cFan.level", matches: body.natural_wind ? snap?.cfan?.natural_wind === true : snap?.cfan?.step === body.step };
      case "port": return { field: `port.${body.port}`, matches: snap?.ports?.[body.port]?.on === body.on };
      default: return null;
    }
  }
  function commandFeedback(fb, key, style) {
    say(fb, key); fb.className = `hk-prop-fb ${style}`;
  }
  function settleCommands(snap, fromPoll = false) {
    for (const [fb, watch] of commandWatches) {
      if (fromPoll) watch.seenReport = true;
      const target = commandTarget(watch.body, snap), pending = snap?.pending?.[target.field];
      let key = null, style = "bad";
      if (!fb.isConnected) key = "gone";
      else if (pending?.status === "rejected") key = "hank.proposal.rejected";
      else if (pending?.status === "timeout") key = "hank.proposal.unconfirmed";
      else if (watch.seenReport && snap?.online === true && snap?.mqtt === true && typeof snap.shadow_age === "number"
          && snap.shadow_age >= 0 && snap.shadow_age <= 300 && target.matches && pending?.status !== "pending") {
        key = "hank.proposal.confirmed"; style = "ok";
      } else if (Date.now() >= watch.deadline) key = "hank.proposal.unconfirmed";
      if (!key) continue;
      clearTimeout(watch.timer); commandWatches.delete(fb);
      if (key !== "gone") commandFeedback(fb, key, style);
    }
  }
  function watchCommand(body, fb) {
    if (!fb) return;
    if (!commandTarget(body, null)) {
      commandFeedback(fb, body.action === "plant_apply" ? "hank.proposal.accepted" : "hank.proposal.saved", "ok");
      return;
    }
    commandFeedback(fb, "hank.proposal.sent", "");
    const old = commandWatches.get(fb); if (old) clearTimeout(old.timer);
    const watch = { body: { ...body }, deadline: Date.now() + 20000, timer: null, seenReport: false };
    watch.timer = setTimeout(() => settleCommands(H.app), 20050);
    commandWatches.set(fb, watch);
    // Wait for a state fetched after the POST; H.app may still describe the state before this command.
  }

  function expired(p) { return typeof p.expires === "number" && Date.now() / 1000 > p.expires; }

  function markExpired(card) {
    for (const b of card.querySelectorAll("button")) b.disabled = true;
    const fb = card.querySelector(".hk-prop-fb");
    if (fb && !fb.dataset.hk) { say(fb, "hank.proposal.expired"); fb.className = "hk-prop-fb"; }
  }

  // The run button of a card or of the traffic light; a two-tap card asks once more first.
  function runButton(p, cls, key, fb, onSent) {
    const b = button(cls);
    if (key) say(b, key); else b.textContent = p.label || "";
    let armed = 0;
    let disarm = null;
    b.onclick = async () => {
      if (expired(p)) { markExpired(b.closest(".hk-prop") || b.parentElement); return; }
      if (p.two_taps && Date.now() > armed) {
        armed = Date.now() + ARM_MS;
        b.classList.add("arm");
        say(b, "hank.proposal.confirm");
        clearTimeout(disarm);
        disarm = setTimeout(() => {
          armed = 0;
          b.classList.remove("arm");
          if (key) say(b, key); else { delete b.dataset.hk; b.textContent = p.label || ""; }
        }, ARM_MS);
        return;
      }
      clearTimeout(disarm);
      b.classList.remove("arm");
      b.disabled = true;
      try {
        await api("/api/cmd", { ...(p.body || {}), proposal: p.id });
        watchCommand(p.body || {}, fb);
        if (onSent) onSent();
        if (typeof poll === "function") poll(true);  // the cockpit shows the outcome in its own pending line
      } catch (err) {
        b.disabled = false;
        if (key) say(b, key); else { delete b.dataset.hk; b.textContent = p.label || ""; }
        if (fb) {
          delete fb.dataset.hk;
          fb.textContent = t("hank.proposal.failed", { error: message(err) });
          fb.className = "hk-prop-fb bad";
        }
      }
    };
    return b;
  }

  function proposalCard(p) {
    const card = el("div", "hk-prop");
    card.dataset.id = p.id || "";
    if (typeof p.expires === "number") card.dataset.expires = String(p.expires);
    card.append(say(el("b", "hk-prop-h"), "hank.proposal.title"), el("p", "hk-prop-l", p.label || ""));
    const fb = el("p", "hk-prop-fb");
    const row = el("div", "hk-actions");
    const skip = button("btn sm", "hank.proposal.skip");
    const go = runButton(p, "btn primary sm", "hank.proposal.run", fb, () => { skip.disabled = true; });
    skip.onclick = async () => {
      go.disabled = true;
      skip.disabled = true;
      try {
        const res = await api("/api/hank/proposal", { id: p.id, outcome: "declined" });
        row.remove();
        const text = res && (typeof res.message === "string" ? res.message : res.message && res.message.text);
        if (text) {  // the consequence, said once, comes back as Hank's own message
          const { row: r, bubble } = botRow("think");
          renderAnswer(r, bubble, { route: "ledger", text, sources: res.message.sources || [] });
        }
      } catch (err) {
        go.disabled = false;
        skip.disabled = false;
        fb.textContent = t("hank.proposal.failed", { error: message(err) });
        fb.className = "hk-prop-fb bad";
      }
    };
    row.append(go, skip);
    card.append(row, fb);
    if (expired(p)) markExpired(card);
    return card;
  }

  function sweepExpired() {
    const now = Date.now() / 1000;
    for (const card of ui.log.querySelectorAll(".hk-prop[data-expires]")) {
      if (now > Number(card.dataset.expires)) markExpired(card);
    }
  }

  // ---------- asking ----------

  function submit() {
    const text = ui.q.value.replace(/\s+$/, "");
    ui.q.value = "";
    grow();
    ask(text);
  }

  function ask(text) {
    if (H.mode === "warnings") return;  // the field is closed in this mode; the server would only say so
    for (const box of ui.log.querySelectorAll(".hk-hello-chips")) box.remove();  // the chat is no longer empty
    if (text.trim()) userRow(text.trim());
    run("/api/hank/ask", { q: text, client_id: H.client, lang: LANG });
  }

  // "Beheben": a tap on the traffic-light line or on a note's button. The drawer shows the question as the owner's
  // own line ("Was kann ich dagegen tun?") and Hank's fix answer for exactly those problems (keys), cards included.
  // Also in "nur Warnungen": the fix answer belongs to a warning. A green line asks whether all is on target.
  function fixAsk(keys, green) {
    if (H.mode === "off") return;
    if (!H.open) open();
    for (const box of ui.log.querySelectorAll(".hk-hello-chips")) box.remove();
    const key = green ? "hank.fix.askGreen" : "hank.fix.ask";
    userRow(t(key));
    run("/api/hank/ask", { q: t(key), client_id: H.client, lang: LANG,
      fix: Array.isArray(keys) ? keys.filter((k) => typeof k === "string") : [] });
  }

  function lightTapped() {
    const light = H.light;
    if (!light || !LIGHT[light.level]) return;
    fixAsk(light.level === "green" ? [] : light.fix, light.level === "green");
  }

  // The answer from the code shows at once; the model may polish it afterwards (a job), and the bubble says so
  // with the typing line under the draft.
  function showDraft(job, draft, stage, seconds) {
    if (!draft || typeof draft !== "object") return;
    if (!job.draft) {
      job.draft = true;
      renderAnswer(job.row, job.bubble, draft, { pending: true });
    }
    showTyping(job.bubble, stage, seconds);
  }

  async function run(path, body) {
    const { row, bubble } = botRow("think");
    const job = { row, bubble, draft: false };
    showTyping(bubble, "search", 0);
    try {
      const res = await api(path, body);
      if (res && res.done) finish(job, res.answer);
      else if (res && res.id) {
        showDraft(job, res.draft, res.stage, 0);
        H.jobs.set(res.id, job);
        clearTimeout(H.jobTimer);
        H.jobTimer = setTimeout(pollJobs, POLL_BUSY);
        restartState();
      } else failed(job, new Error("no answer"));
    } catch (err) {
      failed(job, err);
    }
  }

  async function pollJobs() {
    clearTimeout(H.jobTimer);
    for (const [id, job] of [...H.jobs]) {
      try {
        const j = await api(`/api/hank/job/${encodeURIComponent(id)}`);
        if (j.done) {
          H.jobs.delete(id);
          finish(job, j.answer);
        } else if (j.draft) {
          showDraft(job, j.draft, j.stage, j.elapsed_s || 0);
        } else {
          showTyping(job.bubble, j.stage, j.elapsed_s || 0);
        }
      } catch (err) {
        H.jobs.delete(id);
        if (job.draft) keepDraft(job); else failed(job, err);
      }
    }
    scrollDown(false);
    if (H.jobs.size && !document.hidden) H.jobTimer = setTimeout(pollJobs, POLL_BUSY);
    else if (!H.jobs.size) restartState();
  }

  // ---------- hints: Hank's own notes arrive as chat messages ----------

  // The server hands out its newest notes (ids counting up), the read ones too. The first time only the unread
  // ones come into the chat, so a reload does not pile up old notes; after that every note with a newer id.
  // No flood (owner, 23.10.2026): the server keeps one live note. An update in place keeps its id and counts up its
  // revision (rev), so the row is drawn again where it is; a new note names the older ones it collapsed (replaces),
  // and those rows leave the chat. Every note has a "Beheben" button for exactly its problems (fix).
  function hintRow(id) { return ui.log.querySelector(`.hk-hintmsg[data-hint="${Number(id)}"]`); }

  function drawHint(row, bubble, item, quiet) {
    const expr = (LIGHT[item.level] || [])[1] || "think";
    renderAnswer(row, bubble, { route: "hint", text: item.text, sources: item.sources, proposals: item.proposals },
      { quiet });
    setFace(row, expr);
    const box = el("div", "hk-actions hk-fix-row");
    const fix = button("btn sm hk-fix", "hank.fix.button");
    fix.onclick = () => fixAsk(item.fix, false);
    box.append(fix);
    bubble.append(box);
    row.dataset.rev = String(Number(item.rev) || 0);
    row.dataset.keys = JSON.stringify(Array.isArray(item.fix) ? item.fix.map(String) : []);
    pilotNote(row);
  }

  function addHints(inbox) {
    const items = ((inbox && Array.isArray(inbox.items)) ? inbox.items : [])
      .filter((item) => item && Number.isFinite(Number(item.id)))
      .sort((a, b) => Number(a.id) - Number(b.id));
    const unread = inbox && Number(inbox.unread) > 0 ? Number(inbox.unread) : 0;
    // the first load also shows the newest note when it was read but the light is not green (review 1, S7): the
    // owner sees the problem and its "Beheben" button after a reload, not just the greeting
    const first = Math.max(unread, H.light && H.light.level !== "green" ? 1 : 0);
    const fresh = H.lastHint == null ? items.slice(items.length - Math.min(first, items.length))
      : items.filter((item) => Number(item.id) > H.lastHint);
    for (const item of items) {
      for (const gone of Array.isArray(item.replaces) ? item.replaces : []) {
        const old = hintRow(gone);
        if (old) old.remove();
      }
      const row = hintRow(item.id);
      if (row && row.dataset.rev !== String(Number(item.rev) || 0)) {
        drawHint(row, row.querySelector(".hk-bubble"), item, true);
      }
    }
    for (const item of fresh) {
      if (hintRow(item.id)) continue;
      const { row, bubble } = botRow((LIGHT[item.level] || [])[1] || "think", "hk-hintmsg");
      row.dataset.hint = String(Number(item.id));
      drawHint(row, bubble, item, false);
    }
    if (items.length) H.lastHint = Math.max(H.lastHint == null ? 0 : H.lastHint, Number(items[items.length - 1].id));
    H.unread = unread;
    if (H.open) ack();
    badge();
  }

  function badge() {
    const b = ui.fab.querySelector(".hk-badge");
    const n = H.open ? 0 : H.unread;
    b.hidden = !n;
    b.textContent = n ? String(n) : "";
    ui.fab.setAttribute("aria-label", n ? `${t("hank.fab")}, ${tn("hank.unread", n)}` : t("hank.fab"));
  }

  async function ack() {
    if (!H.unread || H.lastHint == null) return;
    H.unread = 0;
    badge();
    try { await api("/api/hank/ack", { upto: H.lastHint }); } catch (_) { /* counted again on the next state */ }
  }

  // ---------- mode ----------

  // "aus": the first tap says what it means, the second within ARM_MS switches off.
  function offTapped(b) {
    if (H.mode === "off") return;
    if (b.dataset.armed === "1") {
      disarmOff();
      setMode("off");
      return;
    }
    b.dataset.armed = "1";
    b.classList.add("armed");
    ui.modeHint.hidden = false;
    clearTimeout(H.offTimer);
    H.offTimer = setTimeout(disarmOff, ARM_MS * 2);
  }

  function disarmOff() {
    clearTimeout(H.offTimer);
    const b = ui.seg.querySelector('button[data-mode="off"]');
    if (b) { delete b.dataset.armed; b.classList.remove("armed"); }
    ui.modeHint.hidden = true;
  }

  async function setMode(m) {
    if (!MODES.includes(m)) return;
    try {
      const res = await api("/api/hank/mode", { mode: m });
      applyMode((res && res.mode) || m);
      if (m === "off") { close(); ui.wake.focus({ preventScroll: true }); }
      if (m !== "off" && H.mode !== "off") restartState();
    } catch (err) {
      ui.err.hidden = false;
    }
  }

  function applyMode(m) {
    H.mode = MODES.includes(m) ? m : "on";
    const off = H.mode === "off";
    ui.fab.hidden = off;
    ui.wake.hidden = !off;
    if (off) { close(); syncDrawerLayout(); }
    for (const b of ui.seg.querySelectorAll("button")) {
      const on = b.dataset.mode === H.mode;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    }
    ui.light.hidden = off || !ui.light.dataset.level;
    // "nur Warnungen": no chat, so the field is closed and the greeting says why (screens review, finding 4)
    const warn = H.mode === "warnings";
    ui.q.disabled = warn;
    ui.send.disabled = warn;
    sayAttr(ui.q, "placeholder", warn ? "hank.input.placeholderWarnings" : "hank.input.placeholder");
    const hello = ui.log.querySelector(".hk-hello .hk-text");
    if (hello) say(hello, warn ? "hank.mode.warningsChat" : "hank.empty");
    for (const box of ui.log.querySelectorAll(".hk-hello-chips")) box.hidden = warn;
    if (H.mode !== "on") disarmOff();
    syncScenes();
  }

  // ---------- traffic light ----------

  // The server's sentence may open with the level's own words ("Take a closer look: ..."), which the line already
  // says in bold: they are left out once, so nothing shows twice.
  function withoutLead(text, lead) {
    const s = String(text || "").trim();
    const l = String(lead || "").trim();
    if (!l || s.toLowerCase().indexOf(l.toLowerCase()) !== 0) return s;
    return s.slice(l.length).replace(/^[\s.:,!]+/, "");
  }

  function renderLight(light) {
    H.lightRaw = light || null;
    const box = ui.light;
    const level = light && LIGHT[light.level] ? light.level : "";
    box.dataset.level = level;
    box.hidden = !level || H.mode === "off";
    H.light = level ? { level, fix: Array.isArray(light.fix) ? light.fix : [] } : null;
    if (!level) { H.lightSig = ""; return; }
    const p = light.proposal && light.proposal.id ? light.proposal : null;
    // what Hank does about the line's problems now ("Hank regelt das: Befeuchter."), from the autopilot's answer
    const doing = aboutFor(H.light.fix).map((a) => a.text).join(" ");
    const sig = JSON.stringify([LANG, level, light.text || "", p && p.id, p && p.label, doing]);
    if (sig === H.lightSig) return;  // drawn anew only when something changed: no flicker, no lost focus
    H.lightSig = sig;
    const [key, expr] = LIGHT[level];
    box.className = `statusline hk-scope hk-light hk-${level}${ui.lightScene ? " hk-has-sc" : ""}`;
    let art = ui.lightArt;
    if (!ui.lightScene) {
      art = el("span", "hk-light-art");
      art.append(face(expr));
    }
    const dot = el("i", "hk-dot");
    dot.setAttribute("aria-hidden", "true");
    const words = el("span", "hk-light-t");
    words.append(say(el("b", "hk-light-l"), key));
    const rest = withoutLead(light.text, t(key));
    if (rest) words.append(el("span", "hk-light-s", rest));
    if (doing) words.append(el("span", "hk-light-p", doing));
    // what a tap does, for a screen reader (the title says it to a mouse); the chevron says it to the eye
    words.append(say(el("span", "hk-sr"), "hank.light.open"));
    const more = el("span", "hk-light-more");
    more.append(svgFrom(ICON.go));
    // the kept stage stays in place: taking it out and back in would restart its CSS animation
    const btn = ui.lightBtn;
    if (btn.firstChild === art) {
      while (art.nextSibling) art.nextSibling.remove();
      btn.append(dot, words, more);
    } else btn.replaceChildren(art, dot, words, more);
    while (btn.nextSibling) btn.nextSibling.remove();  // the red card's button of the last drawing
    if (level === "red" && p) {
      const fb = el("span", "hk-prop-fb");
      box.append(runButton(p, "btn primary sm hk-light-go", null, fb), fb);
    }
  }

  // ---------- "Hank regelt": the autopilot's mode in Hank's window ----------

  // The sentence for each problem key that a device can act on, from the server (vgrow/hankpilot.py about).
  function aboutFor(keys) {
    const about = (H.pilot && H.pilot.about) || {};
    const out = [];
    const seen = new Set();
    for (const key of keys || []) {
      const a = about[key];
      if (a && a.text && !seen.has(a.text)) { seen.add(a.text); out.push(a); }
    }
    return out;
  }

  // Under a note: what Hank does about its problems now, with the one tap that unblocks him.
  function pilotNote(row) {
    const bubble = row.querySelector(".hk-bubble");
    if (!bubble) return;
    for (const old of bubble.querySelectorAll(".hk-pilot-note")) old.remove();
    let keys = [];
    try { keys = JSON.parse(row.dataset.keys || "[]"); } catch (_) { keys = []; }
    const found = aboutFor(keys);
    if (!found.length) return;
    const box = el("div", "hk-pilot-note");
    for (const a of found) {
      const line = el("p", "hk-pilot-says", a.text);
      if (a.step && a.label) {
        const go = el("button", "btn sm hk-step", String(a.label));
        go.type = "button";
        go.onclick = () => openStep(a.step);
        line.append(" ", go);
      }
      box.append(line);
    }
    const fixRow = bubble.querySelector(".hk-fix-row");
    if (fixRow) fixRow.before(box); else bubble.append(box);
  }

  function pilotArmed() { return Date.now() < H.pilotArm; }

  function disarmPilot() {
    clearTimeout(H.pilotTimer);
    H.pilotArm = 0;
    if (H.built) drawPilot(true);
  }

  // Off and Observe switch no light and no fan: one tap. Auto switches: two taps, as on the autopilot card.
  function pilotTapped(m) {
    const p = H.pilot;
    if (!p || m === p.mode) return;
    if (m !== "off" && p.forced_off) return;  // as the card: only VGROW_AUTOPILOT=0 refuses a mode
    if (m === "auto" && !pilotArmed()) {
      H.pilotArm = Date.now() + ARM_MS;
      clearTimeout(H.pilotTimer);
      H.pilotTimer = setTimeout(disarmPilot, ARM_MS + 100);
      drawPilot(true);
      return;
    }
    H.pilotArm = 0;
    clearTimeout(H.pilotTimer);
    sendPilot(m);
  }

  // The card's own action, so the card and Hank show the one state: the next poll comes right after.
  async function sendPilot(m) {
    const p = H.pilot;
    if (!p) return;
    ui.pilotFb.hidden = true;
    drawPilot(true);
    try {
      await api("/api/cmd", { action: "autopilot_mode", mode: m, box: p.box });
      if (typeof poll === "function") poll(true);
    } catch (err) {
      ui.pilotFb.textContent = t("hank.proposal.failed", { error: message(err) });
      ui.pilotFb.hidden = false;
      drawPilot(true);
    }
  }

  // A step that keeps Hank from regulating. "mode" is Hank's own switch; every other step lives on the page (the
  // stage, USB 2, the lamp distance, the humidifier rule, the ports): the drawer closes and app.js pilotStep opens
  // it there, armed, so the owner's next tap confirms it where it always is.
  function openStep(step) {
    const p = H.pilot;
    if (!p || !step) return;
    if (step === "mode") {
      if (!H.open) open();
      const want = p.mode === "observe" ? "auto" : "observe";
      if (want === "auto" && !pilotArmed()) pilotTapped("auto");
      const b = ui.pilotSeg.querySelector(`button[data-pilot="${want}"]`);
      if (b) b.focus({ preventScroll: true });
      return;
    }
    if (typeof pilotStep !== "function") return;
    close();
    pilotStep(p.box, step);
  }

  function pilotFromApp(s) {
    const boxes = s && s.autopilot && Array.isArray(s.autopilot.boxes) ? s.autopilot.boxes : [];
    const box = boxes.find((b) => b && b.hank && typeof b.hank === "object");
    return box ? Object.assign({ box: box.id }, box.hank) : null;
  }

  function drawPilot(force) {
    const p = H.pilot;
    ui.pilot.hidden = !p;
    if (!p) { H.pilotSig = ""; return; }
    const armed = pilotArmed();
    const sig = JSON.stringify([LANG, p, armed, ui.pilotFb.hidden]);
    if (!force && sig === H.pilotSig) return;  // drawn anew only when something changed: no lost focus
    H.pilotSig = sig;
    for (const b of ui.pilotSeg.querySelectorAll("button")) {
      const on = b.dataset.pilot === p.mode;
      b.classList.toggle("on", on);
      b.classList.toggle("armed", armed && b.dataset.pilot === "auto");
      b.setAttribute("aria-pressed", String(on));
      b.disabled = b.dataset.pilot !== "off" && !!p.forced_off;  // the card's own rule (web/app.js apModeSwitch)
    }
    say(ui.pilotNeed, armed ? "hank.ctl.armAuto" : "hank.ctl.need");
    const parts = [];
    const blockers = Array.isArray(p.blockers) ? p.blockers : [];
    if (blockers.length) {
      parts.push(say(el("p", "hk-pilot-h"), "hank.ctl.blocked"));
      const list = el("ul", "hk-steps");
      for (const b of blockers) {
        const li = el("li");
        const why = el("span", "hk-step-why", [b.text, b.holds].filter(Boolean).join(" \u00b7 "));
        if (b.step && b.label) {
          const go = el("button", "hk-step-go");
          go.type = "button";
          go.dataset.step = String(b.step);
          const words = el("span", "hk-step-t");
          words.append(el("b", "", String(b.label)), why);
          go.append(words, svgFrom(ICON.go));
          go.onclick = () => openStep(b.step);
          li.append(go);
        } else {
          li.className = "hk-step-plain";
          li.append(why);
        }
        list.append(li);
      }
      parts.push(list);
    }
    const now = el("p", "hk-pilot-now", String(p.regulates || ""));
    if (p.last && p.last.text) {
      now.append(" ", el("span", "hk-pilot-last", t("hank.ctl.last", { time: p.last.when || "", text: p.last.text })));
    }
    parts.push(now);
    ui.pilotBody.replaceChildren(...parts);
  }

  // The app's answer arrived (every poll of app.js, and right after a command): Hank's panel, his notes and his light
  // follow at once.
  function takeApp(s) {
    H.app = s;
    settleCommands(s, true);
    if (!H.built) return;
    const before = JSON.stringify(H.pilot && H.pilot.about);
    H.pilot = pilotFromApp(s);
    if (H.pilot && H.pilot.mode === "auto") H.pilotArm = 0;  // switched: nothing left to confirm
    drawPilot(false);
    if (JSON.stringify(H.pilot && H.pilot.about) !== before) {
      for (const row of ui.log.querySelectorAll(".hk-hintmsg")) pilotNote(row);
      if (H.lightRaw) { H.lightSig = ""; renderLight(H.lightRaw); }
    }
  }

  document.addEventListener("vgrow:state", (e) => takeApp(e.detail));
  document.addEventListener("workspace:context", () => { if (H.built && window.PlantWorkspace?.assistantAllowed() === false) close(); });

  // ---------- scenes ----------

  // The server picks the scene from its own verdicts (nudges.scene: sleep, umbrella, fan, scarf, point, idle) and
  // sends none outside the mode "on"; the page adds no thresholds of its own.
  function takeScene(s) {
    const sc = s.scene && typeof s.scene.id === "string" ? s.scene : null;
    H.scene = { id: sc ? sc.id : null, text: sc && typeof sc.text === "string" ? sc.text : "",
      level: s.light && LIGHT[s.light.level] ? s.light.level : "" };
    syncScenes();
  }

  // Chores only in the open drawer, in the mode "on", while the box is on target (scene idle at green).
  // Outside "on" the stages go still at once, without waiting for the next poll (the cached scene is the old mode's).
  // A stage that throws costs the scenes, never the poll: Hank goes back to his still faces (dropScenes).
  function syncScenes() {
    try {
      const sc = H.scene;
      const off = H.mode === "off";
      const id = H.mode === "on" ? sc.id : null;
      if (ui.lightScene) {
        if (off || !sc.level) ui.lightScene.stop();
        else ui.lightScene.setState(id, LIGHT[sc.level][1]);
        ui.lightScene.setActive(!off && !ui.light.hidden);
      }
      if (ui.headScene) {
        if (off) ui.headScene.stop();
        else ui.headScene.setState(id, sc.level === "yellow" ? "think" : sc.level === "red" ? "alert" : "hello");
        ui.headScene.setActive(H.open);
        ui.headScene.allowChores(H.open && id === "idle" && sc.level === "green");
      }
      sceneLabel(ui.headScene && ui.headScene.now());
    } catch (_) {
      dropScenes();
    }
  }

  // The scenes are Hank's guest: if one breaks, both stages go and his still faces come back.
  function dropScenes() {
    for (const stage of [ui.lightScene, ui.headScene]) {
      try { if (stage) stage.stop(); } catch (_) { /* already broken: it is dropped anyway */ }
    }
    ui.lightScene = ui.headScene = null;
    ui.light.classList.remove("hk-has-sc");
    for (const host of [ui.lightArt, ui.headFace]) host.classList.remove("hk-sc", "hk-sc-mini", "hk-sc-head", "hk-sc-paused");
    ui.headFace.replaceChildren(face("hello"));
    ui.headFace.removeAttribute("role");
    ui.headFace.removeAttribute("aria-label");
    H.lightSig = "";  // the next renderLight() draws a still face again
  }

  // The drawer's avatar says what Hank does: the chore, or the server's sentence for the scene, or nothing.
  function sceneLabel(chore) {
    const av = ui.headFace;
    if (!av || !ui.headScene) return;
    const key = chore && CHORE_TEXT[chore];
    delete av.dataset.hkAttr;
    if (key) {
      av.setAttribute("role", "img");
      sayAttr(av, "aria-label", key);
    } else if (H.mode === "on" && H.scene.text) {
      av.setAttribute("role", "img");
      av.setAttribute("aria-label", H.scene.text);
    } else {
      av.removeAttribute("role");
      av.removeAttribute("aria-label");
    }
  }

  // ---------- polling ----------

  function restartState() {
    clearTimeout(H.stateTimer);
    if (!document.hidden) H.stateTimer = setTimeout(pollState, H.jobs.size ? POLL_BUSY : POLL_IDLE);
  }

  async function pollState() {
    clearTimeout(H.stateTimer);
    try {
      const s = await api("/api/hank/state");
      if (!s || typeof s !== "object" || !s.mode) throw new Error("no state");
      if (!H.built) { build(); if (H.app) takeApp(H.app); }
      H.failed = false;
      ui.err.hidden = true;
      applyMode(s.mode);
      renderLight(s.light);
      takeScene(s);
      addHints(s.inbox);
      sweepExpired();
    } catch (err) {
      // Never answered: Hank stays hidden and the page runs on. Answered before: say so in the drawer.
      if (H.built) { H.failed = true; ui.err.hidden = false; }
    }
    restartState();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    pollState();
    if (H.jobs.size) pollJobs();
  });

  // app.js writes <html lang> on a language switch: every Hank text follows.
  new MutationObserver(() => {
    if (!H.built) return;
    retext(ui.root);
    retext(ui.light);
    H.lightSig = "";
    H.pilotSig = "";
    badge();
    pollState();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });

  pollState();
})();
