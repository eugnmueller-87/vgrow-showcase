// The pixels of Hank's garden (web/garden.js). Everything is drawn here in whole pixels from code: no image files, no library,
// nothing to download. One floating isometric garden on a 9 x 9 grid, Hank as a small rig (limbs drawn per frame, head and shirt as
// pixel rows, so any pose is one set of points), his anime portrait, a bitmap font with umlauts. No texts and no data here.
//
// Grid: gx runs to the lower right, gy to the lower left, (0, 0) is the back corner. Island coordinates are pixels from that corner.
(function () {
  "use strict";

  // ---------- raster helpers ----------

  function mk(w, h) { const cv = document.createElement("canvas"); cv.width = w; cv.height = h; return cv; }
  function rect(c, x, y, w, h, col) { c.fillStyle = col; c.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); }
  function dot(c, x, y, col) { c.fillStyle = col; c.fillRect(Math.round(x), Math.round(y), 1, 1); }

  function brush(c, x0, y0, x1, y1, size, col) {
    x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1, half = Math.floor(size / 2);
    let err = dx + dy;
    c.fillStyle = col;
    for (;;) {
      c.fillRect(x0 - half, y0 - half, size, size);
      if (x0 === x1 && y0 === y1) return;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }
  function line(c, x0, y0, x1, y1, col) { brush(c, x0, y0, x1, y1, 1, col); }

  // A filled polygon without smoothing: one run per row.
  function poly(c, pts, col) {
    c.fillStyle = col;
    let minY = Infinity, maxY = -Infinity;
    for (const p of pts) { if (p[1] < minY) minY = p[1]; if (p[1] > maxY) maxY = p[1]; }
    for (let y = Math.floor(minY); y < Math.ceil(maxY); y++) {
      const sy = y + 0.5, xs = [];
      for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
        const a = pts[j], b = pts[i];
        if ((a[1] <= sy && b[1] > sy) || (b[1] <= sy && a[1] > sy)) xs.push(a[0] + (sy - a[1]) / (b[1] - a[1]) * (b[0] - a[0]));
      }
      xs.sort((p, q) => p - q);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const l = Math.round(xs[k]), r = Math.round(xs[k + 1]);
        if (r > l) c.fillRect(l, y, r - l, 1);
      }
    }
  }

  function ell(c, cx, cy, rx, ry, col) {
    c.fillStyle = col;
    const rows_ = Math.max(0, Math.round(ry));
    for (let y = -rows_; y <= rows_; y++) {
      const half = Math.round(rx * Math.sqrt(Math.max(0, 1 - (y / (rows_ + 0.35)) * (y / (rows_ + 0.35)))));
      c.fillRect(Math.round(cx) - half, Math.round(cy) + y, half * 2 + 1, 1);
    }
  }

  function hash(x, y) { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); }
  function noise(x, y) {
    const xi = Math.floor(x), yi = Math.floor(y), fx = x - xi, fy = y - yi;
    const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    const a = hash(xi, yi), b = hash(xi + 1, yi), d = hash(xi, yi + 1), e = hash(xi + 1, yi + 1);
    return a + (b - a) * u + (d - a) * v + (a - b - d + e) * u * v;
  }

  function hex(col) { return [parseInt(col.slice(1, 3), 16), parseInt(col.slice(3, 5), 16), parseInt(col.slice(5, 7), 16)]; }
  function mix(a, b, t) {
    const p = hex(a), q = hex(b);
    return `rgb(${Math.round(p[0] + (q[0] - p[0]) * t)},${Math.round(p[1] + (q[1] - p[1]) * t)},${Math.round(p[2] + (q[2] - p[2]) * t)})`;
  }

  function rows(lines, pal) {
    const cv = mk(lines[0].length, lines.length), c = cv.getContext("2d");
    lines.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const col = pal[row[x]];
        if (col) dot(c, x, y, col);
      }
    });
    return cv;
  }

  // One dark pixel around everything drawn: the look of a sprite, and it keeps objects apart on the grass.
  function outline(cv, col) {
    const c = cv.getContext("2d"), w = cv.width, h = cv.height, img = c.getImageData(0, 0, w, h), d = img.data;
    const on = (x, y) => x >= 0 && y >= 0 && x < w && y < h && d[(y * w + x) * 4 + 3] > 0;
    const out = [];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (!on(x, y) && (on(x - 1, y) || on(x + 1, y) || on(x, y - 1) || on(x, y + 1))) out.push([x, y]);
      }
    }
    for (const p of out) dot(c, p[0], p[1], col);
  }

  // ---------- the grid ----------

  const X = (gx, gy) => (gx - gy) * 16;
  const Y = (gx, gy, z) => (gx + gy) * 8 - (z || 0);
  const P3 = (gx, gy, z) => [X(gx, gy), Y(gx, gy, z)];
  const SIZE = 9;

  function isoBox(c, gx, gy, w, d, z, h, top, left, right) {
    const a = P3(gx, gy, z + h), b = P3(gx + w, gy, z + h), e = P3(gx + w, gy + d, z + h), f = P3(gx, gy + d, z + h);
    const b0 = P3(gx + w, gy, z), e0 = P3(gx + w, gy + d, z), f0 = P3(gx, gy + d, z);
    if (left) poly(c, [f, e, e0, f0], left);
    if (right) poly(c, [e, b, b0, e0], right);
    if (top) poly(c, [a, b, e, f], top);
  }
  function isoLine(c, g0, g1, z, col) { const p = P3(g0[0], g0[1], z), q = P3(g1[0], g1[1], z); line(c, p[0], p[1], q[0], q[1], col); }

  // ---------- palette ----------

  const INK = "#2b1b18";
  const WOOD = { hi: "#d9ac72", top: "#c6935a", left: "#a87643", right: "#855a30", dark: "#684524" };
  const STONE = { hi: "#c9c6ba", top: "#aaa79c", left: "#8f8c82", right: "#716e67" };
  const LEAF = ["#1f6b38", "#2f9a4a", "#48b85a", "#8ae06e"];

  // ---------- the bitmap font: my own 5 x 7 cells, two rows below the line for g, j, p, q, y ----------

  const GLYPHS = {
    " ": "...|...|...|...|...|...|...",
    A: ".###.|#...#|#...#|#####|#...#|#...#|#...#", B: "####.|#...#|#...#|####.|#...#|#...#|####.", C: ".###.|#...#|#....|#....|#....|#...#|.###.",
    D: "####.|#...#|#...#|#...#|#...#|#...#|####.", E: "#####|#....|#....|####.|#....|#....|#####", F: "#####|#....|#....|####.|#....|#....|#....",
    G: ".###.|#...#|#....|#.###|#...#|#...#|.###.", H: "#...#|#...#|#...#|#####|#...#|#...#|#...#", I: "###|.#.|.#.|.#.|.#.|.#.|###",
    J: "..###|...#.|...#.|...#.|...#.|#..#.|.##..", K: "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#", L: "#....|#....|#....|#....|#....|#....|#####",
    M: "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#", N: "#...#|##..#|#.#.#|#..##|#...#|#...#|#...#", O: ".###.|#...#|#...#|#...#|#...#|#...#|.###.",
    P: "####.|#...#|#...#|####.|#....|#....|#....", Q: ".###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#", R: "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
    S: ".###.|#...#|#....|.###.|....#|#...#|.###.", T: "#####|..#..|..#..|..#..|..#..|..#..|..#..", U: "#...#|#...#|#...#|#...#|#...#|#...#|.###.",
    V: "#...#|#...#|#...#|#...#|#...#|.#.#.|..#..", W: "#...#|#...#|#...#|#.#.#|#.#.#|##.##|#...#", X: "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
    Y: "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..", Z: "#####|....#|...#.|..#..|.#...|#....|#####",
    a: ".....|.....|.###.|....#|.####|#...#|.####", b: "#....|#....|####.|#...#|#...#|#...#|####.", c: ".....|.....|.###.|#...#|#....|#...#|.###.",
    d: "....#|....#|.####|#...#|#...#|#...#|.####", e: ".....|.....|.###.|#...#|#####|#....|.###.", f: "..##|.#..|.#..|###.|.#..|.#..|.#..",
    g: ".....|.....|.####|#...#|#...#|#...#|.####|....#|####.", h: "#....|#....|####.|#...#|#...#|#...#|#...#", i: ".#.|...|##.|.#.|.#.|.#.|###",
    j: "...#|....|..##|...#|...#|...#|...#|#..#|.##.", k: "#....|#....|#..#.|#.#..|##...|#.#..|#..#.", l: "##.|.#.|.#.|.#.|.#.|.#.|###",
    m: ".....|.....|##.#.|#.#.#|#.#.#|#.#.#|#.#.#", n: ".....|.....|####.|#...#|#...#|#...#|#...#", o: ".....|.....|.###.|#...#|#...#|#...#|.###.",
    p: ".....|.....|####.|#...#|#...#|#...#|####.|#....|#....", q: ".....|.....|.####|#...#|#...#|#...#|.####|....#|....#", r: ".....|.....|#.##.|##..#|#....|#....|#....",
    s: ".....|.....|.####|#....|.###.|....#|####.", t: ".#..|.#..|###.|.#..|.#..|.#.#|..#.", u: ".....|.....|#...#|#...#|#...#|#...#|.####",
    v: ".....|.....|#...#|#...#|#...#|.#.#.|..#..", w: ".....|.....|#...#|#...#|#.#.#|#.#.#|.#.#.", x: ".....|.....|#...#|.#.#.|..#..|.#.#.|#...#",
    y: ".....|.....|#...#|#...#|#...#|#...#|.####|....#|.###.", z: ".....|.....|#####|...#.|..#..|.#...|#####",
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.", "1": "..#..|.##..|..#..|..#..|..#..|..#..|.###.", "2": ".###.|#...#|....#|...#.|..#..|.#...|#####",
    "3": "#####|...#.|..#..|...#.|....#|#...#|.###.", "4": "...#.|..##.|.#.#.|#..#.|#####|...#.|...#.", "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.", "7": "#####|....#|...#.|..#..|.#...|.#...|.#...", "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.",
    "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    ".": ".|.|.|.|.|.|#", ",": ".|.|.|.|.|.|#|#", "!": "#|#|#|#|#|.|#", "?": ".###.|#...#|....#|...#.|..#..|.....|..#..", ":": ".|.|#|.|.|#|.", ";": ".|.|#|.|.|#|#",
    "-": "....|....|....|####|....|....|....", "+": ".....|..#..|..#..|#####|..#..|..#..|.....", "/": "....#|....#|...#.|..#..|.#...|#....|#....", "'": "#|#|.|.|.|.|.",
    "\"": "#.#|#.#|...|...|...|...|...", "(": ".#|#.|#.|#.|#.|#.|.#", ")": "#.|.#|.#|.#|.#|.#|#.", "%": "##..#|##..#|...#.|..#..|.#...|#..##|#..##", "°": ".#.|#.#|.#.|...|...|...|...",
    "=": ".....|.....|#####|.....|#####|.....|.....", "~": ".....|.....|.##.#|#..#.|.....|.....|.....", "·": ".|.|.|#|.|.|.", "&": ".##..|#..#.|#.#..|.#...|#.#.#|#..#.|.##.#",
    "ä": ".#.#.|.....|.###.|....#|.####|#...#|.####", "ö": ".#.#.|.....|.###.|#...#|#...#|#...#|.###.", "ü": ".#.#.|.....|#...#|#...#|#...#|#...#|.####",
    "Ä": ".#.#.|.....|.###.|#...#|#####|#...#|#...#", "Ö": ".#.#.|.###.|#...#|#...#|#...#|#...#|.###.", "Ü": ".#.#.|.....|#...#|#...#|#...#|#...#|.###.",
    "ß": ".##..|#..#.|#..#.|#.#..|#..#.|#..#.|#.##.",
  };
  const FONT = {};
  for (const ch of Object.keys(GLYPHS)) {
    const lines = GLYPHS[ch].split("|");
    FONT[ch] = { w: lines[0].length, lines };
  }
  // What the server's sentences can hold that the font does not draw.
  const SWAPS = { "\u2248": "~", "\u2013": "-", "\u2212": "-", "\u2014": "-", "\u2019": "'", "\u2018": "'", "\u201c": "\"", "\u201d": "\"", "\u201e": "\"", "\u00a0": " ", "\u202f": " ", "\u2026": "..." };

  function clean(str) { return String(str).replace(/[\u2248\u2013\u2212\u2014\u2019\u2018\u201c\u201d\u201e\u00a0\u202f\u2026]/g, (m) => SWAPS[m]); }
  function textWidth(str) {
    let w = 0;
    for (const ch of clean(str)) w += ((FONT[ch] || FONT["?"]).w) + 1;
    return Math.max(0, w - 1);
  }
  const TEXT_CACHE = new Map();
  function textImage(str, col) {
    const key = `${col}|${str}`;
    let cv = TEXT_CACHE.get(key);
    if (cv) return cv;
    const s = clean(str);
    cv = mk(Math.max(1, textWidth(s)), 9);
    const c = cv.getContext("2d");
    c.fillStyle = col;
    let x = 0;
    for (const ch of s) {
      const g = FONT[ch] || FONT["?"];
      g.lines.forEach((row, y) => { for (let i = 0; i < row.length; i++) if (row[i] === "#") c.fillRect(x + i, y, 1, 1); });
      x += g.w + 1;
    }
    if (TEXT_CACHE.size > 400) TEXT_CACHE.clear();
    TEXT_CACHE.set(key, cv);
    return cv;
  }
  function text(c, str, x, y, col) { c.drawImage(textImage(str, col), Math.round(x), Math.round(y)); }
  function wrap(str, maxW) {
    const out = [];
    let cur = "";
    for (const word of clean(str).split(" ")) {
      const next = cur ? `${cur} ${word}` : word;
      if (textWidth(next) <= maxW) cur = next;
      else { if (cur) out.push(cur); cur = word; }
    }
    if (cur) out.push(cur);
    return out;
  }

  // ---------- Hank: head, hat and shirt as pixel rows, limbs per frame ----------

  const HP = {
    k: INK, y: "#f8de8e", h: "#e9bd60", H: "#b5842f", g: "#37a86c", G: "#1f6e45", v: "#7ad06f",
    f: "#b0663c", F: "#6d3c23", s: "#f9cfaa", S: "#e2a27a", r: "#d4503f", R: "#9a332b", b: "#3b2a1d", B: "#e0b94f", n: "#fff4dd",
    o: "#7c4d2d", O: "#4c2e1b",
  };
  const HEAD_F = rows([
    "...kkkkkkkk..",
    "..kFfFfFfFFk.",
    ".kFffFfffFffk",
    ".kFfFsfFssFsk",
    "kFfFsssssssSk",
    "kFfSsssssssSk",
    "kSkSsssssssSk",
    "kFkSsssssssSk",
    ".kkSsssssssSk",
    "..kSssssssSk.",
    "...kSSSSSSk..",
    "....kkkkkk...",
  ], HP);
  const HEAD_B = rows([
    "...kkkkkkkk..",
    "..kFfFfFfFFk.",
    ".kFffFfffFffk",
    ".kFfFfFffFffk",
    "kFfFfFfFfFfFk",
    "kfFfFfFFfFfFk",
    "kfFFfFfFFfFfk",
    ".kFfFfFfFfFk.",
    "..kFFfFFfFFk.",
    "..kSsssssSk..",
    "...kSSSSSk...",
    "....kkkkk....",
  ], HP);
  const HAIR_TOP = rows([
    "..k...k..k...",
    ".kFk.kfkkfk..",
    ".kfFkfFffFfk.",
    "..kFfFfFfFFk.",
  ], HP);
  const HAT_F = rows([
    "......kkkkkkk......",
    ".....kyyyyhhHk.....",
    "....kyyyhhhhhHk....",
    "....kyhhhhvvhHk....",
    "....kgggggGGGGk....",
    ".kkkkgggggGGGGkkkk.",
    "kyyyyyyyhhhhhhhhHHk",
    ".kkkHHHHHHHHHHHkkk.",
    "....kkkkkkkkkkk....",
  ], HP);
  const HAT_B = rows([
    "......kkkkkkk......",
    ".....kyyyyhhHk.....",
    "....kyyyhhhhhHk....",
    "....kyhhhhhhhHk....",
    "....kgggggGGGGk....",
    ".kkkkgggggGGGGkkkk.",
    "kyyyyyyyhhhhhhhhHHk",
    ".kkkHHHHHHHHHHHkkk.",
    "....kkkkkkkkkkk....",
  ], HP);
  const TORSO_F = rows([
    "....kkkkkk....",
    "...kkgggGGkk..",
    "..krrgggGGrRk.",
    ".krrrrgGrrrRRk",
    ".krRrrrrrRrRRk",
    ".kRRRRRRRRRRRk",
    ".krRrrrrrRrRRk",
    ".krRrrrrrRrRRk",
    "..kRRRRRRRRRk.",
    "..kbbbbBbbbbk.",
    "..kkkkkkkkkkk.",
  ], HP);
  const TORSO_B = rows([
    "....kkkkkk....",
    "...kkggGGkk...",
    "..krrrggrrrRk.",
    ".krrrRrrRrrRRk",
    ".krRrRrrRrrRRk",
    ".kRRRRRRRRRRRk",
    ".krRrRrrRrrRRk",
    ".krRrRrrRrrRRk",
    "..kRRRRRRRRRk.",
    "..kbbbbbbbbbk.",
    "..kkkkkkkkkkk.",
  ], HP);
  const BOOT = rows([
    "kOOOk.",
    "koooOk",
    "kkkkkk",
  ], HP);

  const SKIN = HP.s, SKIN_D = HP.S, SHIRT = HP.r, SHIRT_D = HP.R, DENIM = "#5079b3", DENIM_D = "#365484";
  const EYE = "#1d1418", WHITE = "#fff8ea", IRIS = "#3f8a66", MOUSTACHE = "#6b3f24", MOUTH = "#a8403a";

  function drawEyes(c, hx, hy, kind) {
    const at = (x, y, col) => dot(c, hx + x, hy + y, col);
    if (kind === "closed") {
      for (const x of [6, 7, 10, 11]) at(x, 6, EYE);
    } else if (kind === "happy") {
      at(5, 7, EYE); at(6, 6, EYE); at(7, 6, EYE); at(8, 7, EYE); at(10, 6, EYE); at(11, 6, EYE); at(9, 7, EYE);
    } else if (kind === "half") {
      for (const x of [5, 6, 7, 10, 11]) at(x, 6, EYE);
      at(6, 7, IRIS); at(7, 7, EYE); at(10, 7, IRIS); at(11, 7, EYE);
      for (const x of [5, 6, 7, 10, 11]) at(x, 5, SKIN_D);
    } else {
      for (const x of [6, 7, 10, 11]) at(x, 5, EYE);
      at(6, 6, WHITE); at(7, 6, EYE); at(10, 6, WHITE); at(11, 6, EYE);
      at(6, 7, kind === "wide" ? WHITE : IRIS); at(7, 7, EYE); at(10, 7, kind === "wide" ? WHITE : IRIS); at(11, 7, EYE);
    }
    const brow = HP.F;
    if (kind === "worry" || kind === "wide") { at(5, 3, brow); at(6, 4, brow); at(7, 4, brow); at(10, 4, brow); at(11, 3, brow); }
    else if (kind !== "half") { for (const x of [6, 7, 10, 11]) at(x, 4, brow); }
  }
  function drawMouth(c, hx, hy, kind) {
    const at = (x, y, col) => dot(c, hx + x, hy + y, col);
    for (const x of [6, 7, 8, 9, 10, 11]) at(x, 8, MOUSTACHE);
    at(5, 9, SKIN_D); at(4, 9, SKIN_D);
    if (kind === "open") { at(8, 9, MOUTH); at(9, 9, MOUTH); at(8, 10, MOUTH); at(9, 10, MOUTH); }
    else if (kind === "smile") { at(7, 9, MOUTH); at(8, 9, MOUTH); at(9, 9, MOUTH); at(10, 9, MOUTH); }
    else if (kind === "flat") { at(8, 9, MOUTH); at(9, 9, MOUTH); }
    else { at(8, 9, MOUTH); at(9, 9, MOUTH); }
  }

  function arm(c, s, e, h, sleeve) {
    brush(c, s[0], s[1], e[0], e[1], 5, INK);
    brush(c, e[0], e[1], h[0], h[1], 4, INK);
    rect(c, h[0] - 2, h[1] - 2, 5, 5, INK);
    brush(c, s[0], s[1], e[0], e[1], 3, sleeve || SHIRT);
    brush(c, s[0] + 1, s[1] + 1, e[0] + 1, e[1] + 1, 1, sleeve ? SKIN_D : SHIRT_D);
    brush(c, e[0], e[1], h[0], h[1], 2, SKIN);
    rect(c, h[0] - 1, h[1] - 1, 3, 3, SKIN);
    dot(c, h[0] + 1, h[1] + 1, SKIN_D);
  }
  function leg(c, h, k, f) {
    brush(c, h[0], h[1], k[0], k[1], 6, INK);
    brush(c, k[0], k[1], f[0], f[1], 5, INK);
    brush(c, h[0], h[1], k[0], k[1], 4, DENIM);
    brush(c, k[0], k[1], f[0], f[1], 3, DENIM);
    brush(c, h[0] + 1, h[1], k[0] + 1, k[1], 1, DENIM_D);
    brush(c, k[0] + 1, k[1], f[0] + 1, f[1], 1, DENIM_D);
    c.drawImage(BOOT, Math.round(f[0]) - 2, Math.round(f[1]) - 1);
  }

  // Props, in the hand's coordinates, a = turn in radians. Each returns the point effects start from.
  function turn(x, y, ox, oy, a) { const co = Math.cos(a), si = Math.sin(a); return [ox + x * co - y * si, oy + x * si + y * co]; }
  function can(c, x, y, a) {
    const p = (u, v) => turn(u, v, x, y, a);
    poly(c, [p(-5, 0), p(5, 0), p(5, 9), p(-5, 9)], INK);
    poly(c, [p(-4, 1), p(4, 1), p(4, 8), p(-4, 8)], "#7fb6a6");
    poly(c, [p(1, 1), p(4, 1), p(4, 8), p(1, 8)], "#5d9385");
    line(c, ...p(-4, 3), ...p(4, 3), "#a9d8c9");
    line(c, ...p(-3, 0), ...p(-3, -3), INK); line(c, ...p(-3, -3), ...p(3, -3), INK); line(c, ...p(3, -3), ...p(3, 0), INK);
    brush(c, ...p(5, 7), ...p(12, 1), 2, INK);
    line(c, ...p(5, 7), ...p(12, 1), "#7fb6a6");
    brush(c, ...p(12, -1), ...p(14, 2), 2, INK);
    return p(14, 2);
  }
  function bottle(c, x, y, a) {
    const p = (u, v) => turn(u, v, x, y, a);
    poly(c, [p(-2, -3), p(2, -3), p(2, 5), p(-2, 5)], INK);
    poly(c, [p(-1, -2), p(1, -2), p(1, 4), p(-1, 4)], "#8a4f1f");
    line(c, ...p(0, -3), ...p(0, -7), INK);
    dot(c, ...p(0, -7), "#e0b94f");
    line(c, ...p(-1, 1), ...p(1, 1), "#f1e6c6");
    return p(0, -7);
  }
  function glass(c, x, y) {
    rect(c, x - 2, y - 4, 5, 8, INK);
    rect(c, x - 1, y - 3, 3, 6, "#f49a2e");
    dot(c, x - 1, y - 3, "#ffc069");
    line(c, x + 1, y - 7, x + 1, y - 3, "#7ad06f");
    return [x + 1, y - 7];
  }
  function cig(c, x, y, tipCol) {
    dot(c, x, y, "#d9a05b"); dot(c, x + 1, y, "#d9a05b");
    line(c, x + 2, y, x + 4, y, "#fff8ea");
    dot(c, x + 5, y, tipCol);
    return [x + 5, y - 1];
  }
  function spray(c, x, y) {
    rect(c, x - 3, y - 2, 6, 9, INK);
    rect(c, x - 2, y - 1, 4, 7, "#5ab0d9");
    rect(c, x - 2, y - 5, 7, 3, INK);
    line(c, x - 1, y - 4, x + 3, y - 4, "#e8e2d0");
    return [x + 5, y - 4];
  }
  function heldHat(c, x, y) {
    ell(c, x, y, 7, 3, INK); ell(c, x, y, 6, 2, HP.h);
    ell(c, x, y - 2, 4, 2, INK); ell(c, x, y - 2, 3, 1, HP.y);
    return [x, y - 3];
  }
  function umbrella(c, x, y) {
    line(c, x, y + 3, x, y - 13, INK);
    const top = y - 14;
    for (let u = -13; u <= 13; u++) {
      const h = Math.round(7 * Math.sqrt(1 - (u / 13.5) * (u / 13.5)));
      rect(c, x + u, top - h, 1, h + 1, (Math.floor((u + 13) / 5) % 2) ? "#f3e6c8" : "#c24a3e");
      dot(c, x + u, top - h - 1, INK);
      dot(c, x + u, top + 1, INK);
    }
    return [x, top];
  }

  const HANK_W = 72, HANK_H = 72, HANK_X = 36, HANK_Y = 64;
  const hankCanvas = mk(HANK_W, HANK_H);
  const HIP = -12;
  const STAND = { dy: 0, kneeL: [-3, -6], footL: [-3, -2], kneeR: [3, -6], footR: [3, -2], A: [[7, -14], [7, -9]], B: [[-7, -14], [-7, -9]] };
  const HIP_HAND = [[-10, -15], [-7, -12]];
  const OSC = (t, f, a) => Math.round(Math.sin(t * f) * a);

  // The points of one pose. t is seconds, for what moves on the spot.
  function rig(name, t) {
    const base = { ...STAND };
    const sit = { dy: 6, kneeL: [-4, -5], footL: [-4, -2], kneeR: [5, -5], footR: [5, -2] };
    switch (name) {
      case "walk": {
        const a = t * Math.PI * 2, s = Math.sin(a), up = Math.cos(a);
        return { dy: Math.abs(s) > 0.6 ? 0 : -1, kneeL: [-3 + s * 2 + 1, -7], footL: [-3 + s * 4, -2 - Math.max(0, up) * 3], kneeR: [3 - s * 2 + 1, -7], footR: [3 - s * 4, -2 - Math.max(0, -up) * 3],
          A: [[7, -14], [7 - s * 3, -9]], B: [[-7, -14], [-7 + s * 3, -9]] };
      }
      case "pour": return { ...base, A: [[11, -19], [15, -17]], B: HIP_HAND };
      case "fill": return { ...base, A: [[10, -15], [13, -11]], B: HIP_HAND };
      case "carry": return { ...base, A: [[9, -14], [11, -11]] };
      case "reach": return { ...base, A: [[10, -22], [14, -21]], B: HIP_HAND };
      case "sit": return { ...base, ...sit, A: [[9, -13], [8, -8]], B: [[-9, -13], [-8, -8]] };
      case "sip": return { ...base, ...sit, A: [[10, -12], [5, -18]], B: [[-9, -13], [-8, -8]] };
      case "hold": return { ...base, ...sit, A: [[10, -12], [12, -11]], B: [[-9, -13], [-8, -8]] };
      case "nap": return { ...base, ...sit, A: [[8, -11], [2, -9]], B: [[-8, -11], [-2, -9]] };
      case "type": return { ...base, ...sit, A: [[8, -11], [6, -15 + OSC(t, 17, 1)]], B: [[-8, -11], [-6, -15 - OSC(t, 17, 1)]] };
      case "cheer": return { ...base, ...sit, A: [[9, -22], [10, -29 + OSC(t, 9, 1)]], B: [[-9, -22], [-10, -29 - OSC(t, 9, 1)]] };
      case "crouch": return { dy: 9, kneeL: [-8, -7], footL: [-5, -2], kneeR: [8, -6], footR: [5, -2], A: [[11, -10], [16, -5]], B: [[-9, -8], [-7, -5]] };
      case "fan": return { ...base, A: [[11, -21], [9 + OSC(t, 11, 2), -28]], B: HIP_HAND };
      case "shiver": return { ...base, A: [[8, -15], [2, -18]], B: [[-8, -15], [-2, -18]] };
      case "spray": return { ...base, A: [[10, -21], [15, -22]], B: HIP_HAND };
      case "umbrella": return { ...base, A: [[9, -24], [8, -30]] };
      case "scratch": return { ...base, A: [[10, -27], [3 + OSC(t, 12, 1), -33]] };
      case "lookin": return { ...base, A: [[11, -16], [15, -14]], B: HIP_HAND };
      case "point": return { ...base, A: [[11, -23], [16, -27]], B: HIP_HAND };
      case "wave": return { ...base, A: [[10, -24], [12 + OSC(t, 10, 2), -32]] };
      case "hip": return { ...base, A: [[10, -15], [7, -12]], B: HIP_HAND };
      default: return base;
    }
  }

  // Draws Hank with his feet at (x, y). o: pose, t, view "F" | "B", flip, eyes, mouth, hat ("on", "low", "off"), scarf, prop, propAngle, ember.
  // Returns where effects start, in the same coordinates as x and y: tip of what he holds, his mouth, the top of his head.
  function hank(c, x, y, o) {
    const hc = hankCanvas.getContext("2d");
    hc.clearRect(0, 0, HANK_W, HANK_H);
    hc.save();
    hc.translate(HANK_X, HANK_Y);
    const p = rig(o.pose, o.t || 0);
    const dy = p.dy || 0;
    const back = o.view === "B";
    const hipY = HIP + dy;
    const sA = [6, -20 + dy], sB = [-6, -20 + dy];
    const armsBehind = back && (o.pose === "type" || o.pose === "reach");
    let tip = null;
    const drawProp = (hand) => {
      const [hx, hy] = hand;
      switch (o.prop) {
        case "can": tip = can(hc, hx, hy + 1, o.propAngle || 0); break;
        case "beer": tip = bottle(hc, hx, hy - 1, o.propAngle || 0); break;
        case "juice": tip = glass(hc, hx, hy - 2); break;
        case "cig": tip = cig(hc, hx, hy - 1, o.ember || "#e8622a"); break;
        case "spray": tip = spray(hc, hx, hy); break;
        case "hat": tip = heldHat(hc, hx, hy - 2); break;
        case "umbrella": tip = umbrella(hc, hx, hy); break;
        default:
      }
    };
    if (!back) arm(hc, sB, p.B[0], p.B[1]);
    if (armsBehind) { arm(hc, sA, p.A[0], p.A[1]); arm(hc, sB, p.B[0], p.B[1]); }
    leg(hc, [-3, hipY], p.kneeL, p.footL);
    leg(hc, [3, hipY], p.kneeR, p.footR);
    hc.drawImage(back ? TORSO_B : TORSO_F, -7, -23 + dy);
    if (o.scarf) {
      rect(hc, -5, -22 + dy, 11, 3, INK); rect(hc, -4, -21 + dy, 9, 1, "#f1e6c6"); rect(hc, 2, -20 + dy, 3, 6, INK); rect(hc, 3, -20 + dy, 1, 5, "#c24a3e");
    }
    const headX = -5, headY = -34 + dy + (o.pose === "nap" ? 1 : 0);
    hc.drawImage(back ? HEAD_B : HEAD_F, headX, headY);
    if (!back) {
      if (o.hat !== "low") drawEyes(hc, headX, headY, o.eyes || "open");
      drawMouth(hc, headX, headY, o.mouth || "calm");
    }
    if (o.hat === "off") hc.drawImage(HAIR_TOP, headX, headY - 3);
    else hc.drawImage(back ? HAT_B : HAT_F, -8, -39 + dy + (o.hat === "low" ? 3 : 0) + (o.pose === "nap" ? 1 : 0));
    if (back && !armsBehind) { arm(hc, sB, p.B[0], p.B[1]); }
    if (!armsBehind) arm(hc, sA, p.A[0], p.A[1]);
    drawProp(p.A[1]);
    hc.restore();
    const jitter = o.jitter ? OSC(o.t || 0, 40, 1) : 0;
    const left = Math.round(x) - HANK_X + jitter, top = Math.round(y) - HANK_Y;
    if (o.flip) {
      c.save();
      c.translate(Math.round(x) + jitter, 0);
      c.scale(-1, 1);
      c.drawImage(hankCanvas, -HANK_X, top);
      c.restore();
    } else {
      c.drawImage(hankCanvas, left, top);
    }
    const side = o.flip ? -1 : 1;
    const local = (pt) => [x + side * pt[0], y + pt[1]];
    return { tip: tip ? local(tip) : null, hand: local(p.A[1]), mouth: local([4, -24 + dy]), head: local([1, -40 + dy]) };
  }

  // ---------- plants: the real plant's height decides how they are drawn ----------

  // One fan leaf, the shape a grower knows at any size: pointed fingers from one point. Baked once per size, angle and tone.
  const TAU = Math.PI * 2, LEAF_TURNS = 32;
  const LEAF_TONES = { low: ["#2a8a46", "#1f6b38"], mid: ["#3aa552", "#2a8a46"], top: ["#74dc62", "#3aa552"], dusk: ["#5d5a9e", "#463f7c"] };
  const leafCache = new Map();
  function leafSprite(size, angle, tone) {
    const turn = Math.round((((angle % TAU) + TAU) % TAU) / TAU * LEAF_TURNS) % LEAF_TURNS;
    const key = `${size}|${turn}|${tone}`;
    let cv = leafCache.get(key);
    if (cv) return cv;
    const a = turn / LEAF_TURNS * TAU, box = size * 2 + 6, o = box / 2;
    cv = mk(box, box);
    const c = cv.getContext("2d"), cols = LEAF_TONES[tone];
    const fingers = size >= 11 ? [[1.38, 0.4], [-1.38, 0.4], [0.92, 0.7], [-0.92, 0.7], [0.46, 0.92], [-0.46, 0.92], [0, 1]]
      : size >= 4 ? [[1.3, 0.55], [-1.3, 0.55], [0.65, 0.86], [-0.65, 0.86], [0, 1]] : [[0.85, 0.75], [-0.85, 0.75], [0, 1]];
    for (const [off, share] of fingers) {
      const d = a + off, len = size * share, co = Math.cos(d), si = Math.sin(d), w = Math.max(0.55, size * 0.075);
      const m = 0.42;
      line(c, o, o, o + co * len, o + si * len, cols[0]);
      poly(c, [[o, o], [o + co * len * m - si * w, o + si * len * m + co * w], [o + co * len, o + si * len], [o + co * len * m + si * w, o + si * len * m - co * w]], cols[0]);
      if (size >= 7) line(c, o + co * 2, o + si * 2, o + co * len * 0.72, o + si * len * 0.72, cols[1]);
    }
    if (size >= 5) outline(cv, "#17512e");
    leafCache.set(key, cv);
    return cv;
  }
  function leaf(c, x, y, angle, size, tone) {
    const s = Math.max(3, Math.min(16, Math.round(size)));
    const cv = leafSprite(s, angle, tone);
    c.drawImage(cv, Math.round(x - cv.width / 2), Math.round(y - cv.height / 2));
  }

  // o: t seconds, sway in pixels at the top, flap in radians, droop 0..1 (thirst), flower 0..1, purple
  function plant(c, x, y, h, o) {
    const nodes = Math.max(2, Math.round(h / 8));
    const sway = (u) => Math.sin(o.t * 1.7 + x * 0.3) * (o.sway || 0) * u * u;
    const droop = (o.droop || 0) * 0.8;
    const flapBy = o.flap || 0;
    const stem = o.purple && o.flower > 0 ? "#5f4a78" : "#3a8a44";
    let px = x, py = y;
    for (let i = 1; i <= nodes; i++) {
      const u = i / nodes, nx = x + sway(u), ny = y - h * u;
      brush(c, px + 1, py, nx + 1, ny, h > 42 ? 2 : 1, "#1f6b38");
      brush(c, px, py, nx, ny, h > 42 ? 2 : 1, stem);
      px = nx; py = ny;
    }
    for (let i = 0; i < nodes; i++) {
      const u = (i + 0.6) / nodes, nx = x + sway(u), ny = y - h * u;
      const size = Math.max(3.5, (1.2 - u) * (6 + h * 0.22));
      const tone = o.flower > 0 && o.purple && u > 0.55 ? "dusk" : u < 0.35 ? "low" : u < 0.7 ? "mid" : "top";
      const flap = Math.sin(o.t * 2.3 + i * 1.9) * flapBy;
      const lift = i % 2 ? 0.14 : 0.38;
      leaf(c, nx, ny, Math.PI + lift - droop + flap, size, tone);
      leaf(c, nx, ny, -lift + droop - flap, size, tone);
    }
    const topX = x + sway(1), topY = y - h;
    leaf(c, topX, topY + 1, -Math.PI / 2 + Math.sin(o.t * 1.3) * flapBy, Math.max(4, (6 + h * 0.22) * 0.5), o.flower > 0 && o.purple ? "dusk" : "top");
    if (o.flower > 0) {
      const bud = o.purple ? ["#7d5aa6", "#a585cf"] : ["#7fb95c", "#b4d97a"];
      for (let i = Math.floor(nodes * 0.4); i < nodes; i++) {
        const u = (i + 0.6) / nodes, r = Math.max(1, Math.round(o.flower * (1 + u * 2.6)));
        const nx = x + sway(u), ny = y - h * u;
        ell(c, nx, ny - 1, r + 1, r + 2, "#123f24");
        ell(c, nx, ny - 1, r, r + 1, bud[0]);
        dot(c, nx - 1, ny - 2, bud[1]);
        dot(c, nx + r, ny, "#f09a3e"); dot(c, nx - r, ny - r, "#f09a3e");
      }
      const r = Math.max(2, Math.round(o.flower * 4));
      ell(c, topX, topY, r + 1, r + 2, "#123f24");
      ell(c, topX, topY, r, r + 1, bud[0]);
      dot(c, topX - 1, topY - 1, bud[1]); dot(c, topX + r, topY - 1, "#f09a3e"); dot(c, topX - r, topY + 1, "#f09a3e");
    }
  }

  // ---------- the island ----------

  function onPath(gx, gy) {
    const w = (noise(gx * 2.3, gy * 2.3) - 0.5) * 0.22;
    return (gx > 1.45 + w && gx < 6.05 && gy > 2.95 + w && gy < 3.75 - w)
      || (gx > 1.45 + w && gx < 2.35 - w && gy > 2.95 && gy < 5.75 + w)
      || (gx > 5.15 + w && gx < 6.0 - w && gy > 0.72 + w && gy < 3.75)
      || (gx > 2.75 + w && gx < 6.0 && gy > 0.72 + w && gy < 1.46 - w)
      || (gx > 5.2 + w && gx < 6.0 - w && gy > 3.6 && gy < 5.1 + w)
      || (gx > 5.2 + w && gx < 8.35 + w && gy > 4.4 + w && gy < 5.08 - w);
  }

  const HOSE = [[2.72, 5.78], [2.72, 2.98], [2.5, 2.72]];
  const BOARD = [6.4, 5.4];

  function paintIsland() {
    const cv = mk(330, 230), c = cv.getContext("2d");
    const ox = 165, oy = 4;
    const grass = ["#8ccb5e", "#79bd55", "#68ad4c", "#589b45", "#4a893e"];
    const sand = ["#ecd29a", "#ddbf85", "#ccab70", "#b89660"];
    // underside first
    for (let u = -144; u <= 144; u++) {
      const edge = 144 - Math.abs(u) / 2;
      const taper = 1 - Math.abs(u) / 150;
      const depth = Math.round(6 + 24 * taper * taper + noise(u / 11, 3) * 8 + noise(u / 3.3, 9) * 4 - 1);
      const lip = 2 + Math.floor(hash(u, 4) * 3);
      for (let d = 0; d < depth; d++) {
        const y = oy + edge + d;
        let col;
        const n = noise(u / 7, d / 4.5);
        if (d < lip) col = u < 0 ? "#57a047" : "#478a3e";
        else if (d < lip + 1) col = "#3c2a1c";
        else if (hash(u * 3 + 7, d * 5) > 0.975) col = "#a89c84";
        else {
          const band = n + d / 62;
          const left = band < 0.62 ? "#a26b3e" : band < 0.9 ? "#8d5a33" : "#75492a";
          const right = band < 0.62 ? "#7f5430" : band < 0.9 ? "#6b4427" : "#573620";
          col = u < 0 ? left : right;
        }
        dot(c, ox + u, y, col);
      }
      dot(c, ox + u, oy + edge + depth, INK);
    }
    // the top
    for (let y = 0; y <= 144; y++) {
      const hw = (y <= 72 ? y : 144 - y) * 2;
      for (let u = -hw; u <= hw; u++) {
        const gx = (u / 16 + y / 8) / 2, gy = (y / 8 - u / 16) / 2;
        let col;
        if (onPath(gx, gy)) {
          const n = noise(u / 5, y / 3);
          col = sand[Math.min(3, Math.floor(n * 3.2 + hash(u, y) * 0.8))];
          if (hash(u * 2, y * 7) > 0.986) col = "#9c7f50";
        } else {
          const n = noise(u / 15, y / 7.5) * 0.65 + noise(u / 5, y / 2.5) * 0.35;
          const idx = n * 4.4 + (hash(u, y) - 0.5) * 0.9;
          col = grass[Math.max(0, Math.min(4, Math.floor(idx)))];
        }
        dot(c, ox + u, oy + y, col);
      }
    }
    // tufts and flowers, only on grass
    c.save();
    c.translate(ox, oy);
    for (let i = 0; i < 300; i++) {
      const gx = hash(i, 1) * SIZE, gy = hash(i, 2) * SIZE;
      if (onPath(gx, gy) || (gx > 5.9 && gy < 3.6)) continue;
      const x = X(gx, gy), y = Y(gx, gy);
      const kind = hash(i, 3);
      if (kind < 0.6) {
        dot(c, x, y, "#3f7a38"); dot(c, x - 1, y - 1, "#3f7a38"); dot(c, x + 1, y - 1, "#4a893e");
      } else if (kind < 0.8) {
        dot(c, x, y, "#a5da68"); dot(c, x + 1, y - 1, "#a5da68");
      } else if (kind < 0.9) {
        const col = ["#fff4dd", "#ffd54f", "#f48fb1", "#b39ddb"][Math.floor(hash(i, 4) * 4)];
        dot(c, x, y - 1, col); dot(c, x - 1, y, col); dot(c, x + 1, y, col); dot(c, x, y + 1, col); dot(c, x, y, "#e8a93e");
      }
    }
    // the hose from the barrel to the bed lies on the ground
    for (let i = 0; i + 1 < HOSE.length; i++) {
      const a = P3(HOSE[i][0], HOSE[i][1]), b = P3(HOSE[i + 1][0], HOSE[i + 1][1]);
      brush(c, a[0], a[1], b[0], b[1], 2, "#1d4a35");
      line(c, a[0], a[1] - 1, b[0], b[1] - 1, "#3f8a66");
    }
    // the deck
    const dx = 6, dy = 0.3, dw = 2.98, dd = 3.3;
    isoBox(c, dx, dy, dw, dd, 0, 2, null, WOOD.left, WOOD.right);
    poly(c, [P3(dx, dy, 2), P3(dx + dw, dy, 2), P3(dx + dw, dy + dd, 2), P3(dx, dy + dd, 2)], "#d0a06a");
    for (let g = 0; g < 10; g++) {
      const v = dy + g * 0.33;
      if (g % 2) poly(c, [P3(dx, v, 2), P3(dx + dw, v, 2), P3(dx + dw, v + 0.33, 2), P3(dx, v + 0.33, 2)], "#c39359");
      isoLine(c, [dx, v], [dx + dw, v], 2, "#97693a");
    }
    c.restore();
    return { img: cv, x: -ox, y: -oy };
  }

  function bake(bx, by, bw, bh, foot, draw, plain) {
    bx = Math.round(bx); by = Math.round(by);  // a fractional offset would smear every edge of the sprite
    const cv = mk(bw, bh), c = cv.getContext("2d");
    c.translate(-bx, -by);
    draw(c);
    c.setTransform(1, 0, 0, 1, 0, 0);
    if (!plain) outline(cv, INK);
    return { img: cv, x: bx, y: by, w: bw, h: bh, foot };
  }

  function fence(c) {
    const run = (g0, g1, steps) => {
      for (let i = 0; i <= steps; i++) {
        const t = i / steps, gx = g0[0] + (g1[0] - g0[0]) * t, gy = g0[1] + (g1[1] - g0[1]) * t;
        const p = P3(gx, gy);
        rect(c, p[0] - 1, p[1] - 11, 3, 11, "#c9a06c");
        dot(c, p[0] + 1, p[1] - 11, "#e0bf8c");
        rect(c, p[0] + 1, p[1] - 10, 1, 10, "#8f6a3f");
      }
      for (const z of [4, 8]) {
        const a = P3(g0[0], g0[1], z), b = P3(g1[0], g1[1], z);
        brush(c, a[0], a[1], b[0], b[1], 2, "#a67d4c");
      }
    };
    run([0.25, 0.08], [5.75, 0.08], 6);
    run([0.08, 0.25], [0.08, 8.6], 9);
  }

  function tree(c, x, y) {
    poly(c, [[x - 4, y], [x + 4, y], [x + 3, y - 18], [x - 3, y - 18]], "#6f4a2a");
    poly(c, [[x, y], [x + 4, y], [x + 3, y - 18], [x, y - 18]], "#533620");
    const blobs = [[0, -30, 21, 13, "#2f7a41"], [-12, -24, 11, 8, "#2f7a41"], [12, -25, 12, 8, "#2a6e3b"], [-3, -34, 15, 9, "#3d9a50"], [7, -30, 10, 7, "#358a47"], [-8, -38, 8, 5, "#55b561"], [4, -39, 7, 4, "#55b561"]];
    for (const b of blobs) ell(c, x + b[0], y + b[1], b[2], b[3], b[4]);
    for (let i = 0; i < 70; i++) {
      const u = (hash(i, 8) - 0.5) * 38, v = (hash(i, 9) - 0.5) * 22;
      dot(c, x + u, y - 30 + v, hash(i, 10) > 0.5 ? "#6fcf6c" : "#256338");
    }
  }

  function windmillBody(c, x, y) {
    poly(c, [[x - 9, y], [x + 9, y], [x + 5, y - 30], [x - 5, y - 30]], "#e6d3ad");
    poly(c, [[x + 1, y], [x + 9, y], [x + 5, y - 30], [x + 1, y - 30]], "#c2a97f");
    for (let v = 5; v < 30; v += 5) line(c, x - 9 + v * 0.13, y - v, x + 9 - v * 0.13, y - v, "#a68c63");
    rect(c, x - 3, y - 8, 5, 8, "#7c4d2d");
    dot(c, x + 1, y - 4, "#e0b94f");
    poly(c, [[x - 7, y - 30], [x + 7, y - 30], [x, y - 40]], "#b5483f");
    poly(c, [[x + 1, y - 30], [x + 7, y - 30], [x + 1, y - 40]], "#8f332c");
  }

  function fountain(c, x, y) {
    ell(c, x, y, 13, 7, STONE.right);
    ell(c, x, y - 3, 13, 7, STONE.top);
    ell(c, x - 1, y - 4, 11, 5, STONE.hi);
    ell(c, x, y - 3, 10, 5, "#3d82b8");
    ell(c, x - 1, y - 4, 7, 3, "#68aede");
    rect(c, x - 2, y - 14, 5, 11, STONE.left);
    rect(c, x + 1, y - 14, 2, 11, STONE.right);
    ell(c, x, y - 15, 4, 2, STONE.hi);
  }

  function thermometer(c, x, y) {
    rect(c, x - 1, y - 24, 3, 24, WOOD.left);
    rect(c, x + 1, y - 24, 1, 24, WOOD.dark);
    rect(c, x - 3, y - 26, 7, 18, "#f3ead6");
    rect(c, x, y - 24, 1, 14, "#b9aa8e");
    for (let i = 0; i < 4; i++) dot(c, x + 2, y - 23 + i * 4, "#7c6c52");
    ell(c, x, y - 9, 2, 2, "#c9403a");
  }

  function board(c, gx, gy) {
    const z0 = 0;
    for (const u of [0, 1.25]) {
      const p = P3(gx + u, gy, z0);
      rect(c, p[0] - 1, p[1] - 26, 3, 26, WOOD.left);
      rect(c, p[0] + 1, p[1] - 26, 1, 26, WOOD.dark);
    }
    poly(c, [P3(gx - 0.06, gy, 27), P3(gx + 1.31, gy, 27), P3(gx + 1.31, gy, 9), P3(gx - 0.06, gy, 9)], "#b98a55");
    for (const z of [13, 18, 23]) isoLine(c, [gx - 0.06, gy], [gx + 1.31, gy], z, "#8f6a3f");
    poly(c, [P3(gx - 0.12, gy, 29), P3(gx + 1.37, gy, 29), P3(gx + 1.37, gy, 27), P3(gx - 0.12, gy, 27)], "#7c4d2d");
  }

  function bedBox(c, wet) {
    const gx = 1.6, gy = 1.6, w = 3, d = 1, h = 7;
    isoBox(c, gx, gy, w, d, 0, h, WOOD.top, WOOD.left, WOOD.right);
    for (const z of [2.5, 5]) {
      isoLine(c, [gx, gy + d], [gx + w, gy + d], z, WOOD.dark);
      isoLine(c, [gx + w, gy], [gx + w, gy + d], z, WOOD.dark);
    }
    const soil = wet === 2 ? ["#3e2a1c", "#322116", "#4b3423"] : wet === 1 ? ["#5a3e29", "#4b3221", "#6a4a31"] : ["#7f6243", "#6d5338", "#94754f"];
    poly(c, [P3(gx + 0.1, gy + 0.1, h), P3(gx + w - 0.1, gy + 0.1, h), P3(gx + w - 0.1, gy + d - 0.1, h), P3(gx + 0.1, gy + d - 0.1, h)], soil[0]);
    for (let i = 0; i < 180; i++) {
      const u = gx + 0.12 + hash(i, 21) * (w - 0.24), v = gy + 0.12 + hash(i, 22) * (d - 0.24);
      dot(c, X(u, v), Y(u, v, h), hash(i, 23) > 0.5 ? soil[1] : soil[2]);
    }
    isoLine(c, [gx, gy], [gx + w, gy], h, WOOD.hi);
    isoLine(c, [gx, gy], [gx, gy + d], h, WOOD.hi);
    // a drip line along the plants
    isoLine(c, [gx + 0.15, gy + 0.5], [gx + w - 0.15, gy + 0.5], h + 1, "#1d4a35");
    // the name tag in the middle
    const tag = P3(2.65, 2.48, h);
    rect(c, tag[0], tag[1] - 5, 1, 5, WOOD.dark);
    rect(c, tag[0] - 2, tag[1] - 8, 5, 4, "#fff4dd");
  }

  function barrelBody(c, x, y, full) {
    const rx = 10, ry = 5, h = 19;
    for (let u = -rx; u <= rx; u++) {
      const curve = Math.sqrt(1 - (u / (rx + 0.5)) * (u / (rx + 0.5))) * ry;
      const top = y - h + curve, bot = y + curve;
      const col = u < -5 ? "#b78350" : u < 2 ? "#a06e3e" : u < 6 ? "#855a30" : "#6c4726";
      rect(c, x + u, top, 1, bot - top, (u + 20) % 5 === 0 ? "#5d3d20" : col);
      for (const t of [0.18, 0.78]) rect(c, x + u, top + (bot - top) * t, 1, 2, "#4b4f55");
    }
    ell(c, x, y - h, rx, ry, "#c79a63");
    ell(c, x, y - h, rx - 2, ry - 1, "#3a2a1d");
    if (full) {
      ell(c, x, y - h + 1, rx - 3, ry - 2, "#3d82b8");
      ell(c, x - 2, y - h, rx - 6, 1, "#8fcdf0");
    }
    // the pump box with its light, and the tap
    rect(c, x + 7, y - 9, 7, 8, "#3c4148");
    rect(c, x + 8, y - 8, 5, 2, "#5a616b");
    rect(c, x - 13, y - 7, 5, 2, "#8a8f96");
    rect(c, x - 13, y - 9, 2, 2, "#c24a3e");
  }

  function fridge(c, open) {
    const gx = 6.2, gy = 0.5, w = 0.8, d = 0.7, z = 2, h = 27;
    const body = { top: "#c8eee0", left: "#92d4c2", right: "#62ac9b" };
    isoBox(c, gx, gy, w, d, z, h, body.top, body.left, body.right);
    isoLine(c, [gx, gy + d], [gx + w, gy + d], z + 19, "#4f8f80");
    isoBox(c, gx + 0.05, gy + 0.05, w - 0.1, d - 0.05, z - 2, 2, null, "#4c5157", "#3a3e43");
    if (open) {
      const inset = 0.05;
      poly(c, [P3(gx + inset, gy + d, z + 18), P3(gx + w - inset, gy + d, z + 18), P3(gx + w - inset, gy + d, z + 2), P3(gx + inset, gy + d, z + 2)], "#fdf7d4");
      for (const zz of [z + 7, z + 12]) isoLine(c, [gx + inset, gy + d], [gx + w - inset, gy + d], zz, "#b9c8c4");
      const shelf = (u, zz, col) => { const p = P3(gx + u, gy + d, zz); rect(c, p[0], p[1] - 4, 2, 4, col); };
      shelf(0.15, z + 7, "#8a4f1f"); shelf(0.27, z + 7, "#8a4f1f"); shelf(0.5, z + 7, "#f49a2e"); shelf(0.25, z + 12, "#8a4f1f"); shelf(0.55, z + 12, "#7fb95c");
      poly(c, [P3(gx, gy + d, z + 18), P3(gx, gy + d + 0.72, z + 18), P3(gx, gy + d + 0.72, z + 1), P3(gx, gy + d, z + 1)], "#7cc2b1");
      isoLine(c, [gx, gy + d + 0.72], [gx, gy + d], z + 18, "#c8eee0");
    } else {
      const handle = P3(gx + w - 0.12, gy + d, z + 12);
      rect(c, handle[0], handle[1] - 1, 1, 6, "#e8e8e0");
      const top = P3(gx + w - 0.12, gy + d, z + 23);
      rect(c, top[0], top[1], 1, 3, "#e8e8e0");
      const sticker = P3(gx + 0.25, gy + d, z + 13);
      dot(c, sticker[0], sticker[1], "#37a86c"); dot(c, sticker[0] + 1, sticker[1], "#37a86c"); dot(c, sticker[0], sticker[1] + 1, "#37a86c");
      const note = P3(gx + 0.48, gy + d, z + 15);
      rect(c, note[0], note[1], 3, 3, "#fff4dd");
    }
  }

  function desk(c) {
    const gx = 7.6, gy = 0.5, w = 1.25, d = 0.6, z = 2;
    for (const [u, v] of [[0, 0], [w - 0.1, 0], [0, d - 0.1], [w - 0.1, d - 0.1]]) isoBox(c, gx + u, gy + v, 0.1, 0.1, z, 10, WOOD.left, WOOD.left, WOOD.dark);
    isoBox(c, gx, gy, w, d, z + 10, 2, WOOD.top, WOOD.left, WOOD.right);
    const top = z + 12;
    isoBox(c, gx + 0.28, gy + 0.1, 0.42, 0.3, top, 2, "#b5ae9c", "#a39c8a", "#837d6e");
    isoBox(c, gx + 0.18, gy + 0.05, 0.62, 0.42, top + 2, 12, "#ded8c8", "#cbc4b2", "#a8a190");
    poly(c, [P3(gx + 0.23, gy + 0.47, top + 13), P3(gx + 0.75, gy + 0.47, top + 13), P3(gx + 0.75, gy + 0.47, top + 4), P3(gx + 0.23, gy + 0.47, top + 4)], "#1a1f2b");
    isoBox(c, gx + 0.9, gy + 0.06, 0.25, 0.44, top, 14, "#ded8c8", "#cbc4b2", "#a8a190");
    const led = P3(gx + 1.03, gy + 0.5, top + 11);
    dot(c, led[0], led[1], "#7ad06f");
    const slot = P3(gx + 0.95, gy + 0.5, top + 8);
    rect(c, slot[0], slot[1], 3, 1, "#7d7869");
    isoBox(c, gx + 0.2, gy + 0.49, 0.6, 0.11, top, 1, "#e8e2d0", "#bdb6a4", "#a8a190");
  }

  function stool(c) {
    isoBox(c, 7.68, 1.48, 0.34, 0.34, 2, 9, null, WOOD.dark, WOOD.dark);
    isoBox(c, 7.61, 1.41, 0.48, 0.48, 11, 2, "#7c4d2d", "#5f3a21", "#4c2e1b");
  }

  const LEATHER = { top: "#d9784a", left: "#b85f38", right: "#94492b", hi: "#ec9a6a" };
  function chairRear(c) {
    isoBox(c, 7.35, 2.45, 0.2, 1.0, 2, 13, LEATHER.hi, LEATHER.left, LEATHER.right);
    isoBox(c, 7.4, 2.27, 1.1, 0.26, 2, 21, LEATHER.hi, LEATHER.left, LEATHER.right);
    isoLine(c, [7.4, 2.53], [8.5, 2.53], 12, "#a5532f");
    isoLine(c, [7.4, 2.53], [8.5, 2.53], 17, "#a5532f");
    isoBox(c, 7.55, 2.53, 0.95, 0.92, 2, 9, LEATHER.top, LEATHER.left, LEATHER.right);
    isoLine(c, [7.6, 2.55], [8.45, 2.55], 11, LEATHER.hi);
  }
  function chairNear(c) {
    isoBox(c, 8.45, 2.45, 0.2, 1.0, 2, 13, LEATHER.hi, LEATHER.left, LEATHER.right);
  }

  function sideTable(c) {
    const p = P3(6.95, 3.1);
    rect(c, p[0] - 1, p[1] - 12, 3, 10, WOOD.dark);
    ell(c, p[0], p[1] - 1, 4, 2, WOOD.dark);
    ell(c, p[0], p[1] - 13, 8, 4, WOOD.right);
    ell(c, p[0], p[1] - 14, 8, 4, WOOD.top);
    ell(c, p[0] - 2, p[1] - 15, 3, 1, "#a8a59a");
    dot(c, p[0] - 2, p[1] - 15, "#55524b");
    rect(c, p[0] + 2, p[1] - 21, 3, 6, "#8a4f1f");
    rect(c, p[0] + 3, p[1] - 24, 1, 3, "#8a4f1f");
    dot(c, p[0] + 3, p[1] - 25, "#e0b94f");
    rect(c, p[0] + 2, p[1] - 19, 3, 1, "#f1e6c6");
  }

  function lanternPost(c) {
    const p = P3(6.12, 3.42, 2);
    rect(c, p[0] - 1, p[1] - 27, 2, 27, WOOD.dark);
    rect(c, p[0] - 1, p[1] - 28, 5, 1, WOOD.dark);
    rect(c, p[0] + 2, p[1] - 27, 3, 2, "#3c4148");
    rect(c, p[0] + 2, p[1] - 25, 3, 4, "#f2c14e");
    rect(c, p[0] + 2, p[1] - 21, 3, 1, "#3c4148");
  }

  // The lit corners: name, island position. garden.js lights them at night.
  const LAMPS = { lantern: [X(6.12, 3.42) + 3, Y(6.12, 3.42, 2) - 23] };
  const BULB_LINE = (function () {
    const a = P3(6.05, 0.36, 38), b = P3(8.95, 0.36, 36), out = [];
    for (let i = 0; i <= 9; i++) {
      const t = i / 9, sag = Math.sin(t * Math.PI) * 6;
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t + sag]);
    }
    return out;
  }());
  const BULB_COLS = ["#ffd54f", "#ff8a65", "#81d4fa", "#a5d6a7"];

  function stringLights(c) {
    for (const g of [[6.05, 0.36], [8.95, 0.36]]) {
      const p = P3(g[0], g[1], 2);
      rect(c, p[0] - 1, p[1] - 38, 2, 38, WOOD.dark);
    }
    for (let i = 0; i + 1 < BULB_LINE.length; i++) line(c, BULB_LINE[i][0], BULB_LINE[i][1], BULB_LINE[i + 1][0], BULB_LINE[i + 1][1], "#3b2a1d");
  }

  function rocks(c) {
    const stone = (gx, gy, r) => {
      const p = P3(gx, gy);
      ell(c, p[0], p[1], r, Math.max(1, r - 2), STONE.left);
      ell(c, p[0] - 1, p[1] - 1, r - 1, Math.max(1, r - 3), STONE.top);
      dot(c, p[0] - 2, p[1] - 2, STONE.hi);
    };
    stone(4.0, 6.9, 4); stone(4.45, 7.15, 3); stone(7.6, 6.2, 5); stone(2.2, 7.6, 3);
  }

  function shovel(c) {
    const p = P3(4.85, 2.85);
    line(c, p[0], p[1] - 3, p[0] + 4, p[1] - 21, "#8f6a3f");
    rect(c, p[0] + 2, p[1] - 23, 5, 2, "#7c4d2d");
    poly(c, [[p[0] - 2, p[1] - 4], [p[0] + 3, p[1] - 3], [p[0] + 2, p[1] + 2], [p[0] - 1, p[1] + 3]], "#8a8f96");
  }

  function sunflower(c, gx, gy, h) {
    const p = P3(gx, gy);
    line(c, p[0], p[1], p[0], p[1] - h, "#3a8a44");
    dot(c, p[0] - 1, p[1] - h / 2, "#52ad52"); dot(c, p[0] + 1, p[1] - h / 2 + 2, "#52ad52");
    ell(c, p[0], p[1] - h, 3, 3, "#f7c531");
    ell(c, p[0], p[1] - h, 1, 1, "#7c4d2d");
  }

  let world = null;
  function buildWorld() {
    if (world) return world;
    const objs = [];
    const add = (name, box, foot, draw, plain) => { const o = bake(box[0], box[1], box[2], box[3], foot, draw, plain); o.name = name; objs.push(o); return o; };
    const treeAt = P3(0.92, 0.82), millAt = P3(0.62, 4.75), fountAt = P3(0.98, 3.0), thermoAt = P3(3.4, 4.05), barrelAt = P3(2.1, 6.25);
    add("fence", [-150, -14, 250, 100], [0, 0, 0, 0], fence);
    add("tree", [treeAt[0] - 26, treeAt[1] - 50, 52, 56], [0.5, 0.5, 1.3, 1.3], (c) => tree(c, treeAt[0], treeAt[1]));
    add("flowers", [-120, -6, 180, 60], [0, 0, 0, 0], (c) => { sunflower(c, 1.9, 0.5, 15); sunflower(c, 2.4, 0.42, 18); sunflower(c, 3.1, 0.45, 14); sunflower(c, 0.5, 6.6, 16); sunflower(c, 0.45, 7.3, 13); }, true);
    add("windmill", [millAt[0] - 12, millAt[1] - 44, 24, 48], [0.2, 4.4, 1.0, 5.1], (c) => windmillBody(c, millAt[0], millAt[1]));
    add("fountain", [fountAt[0] - 16, fountAt[1] - 20, 32, 30], [0.6, 2.6, 1.4, 3.4], (c) => fountain(c, fountAt[0], fountAt[1]));
    const fridgeClosed = bake(60, 10, 70, 70, [6.2, 0.5, 7.0, 1.2], (c) => fridge(c, false));
    const fridgeOpen = bake(40, 10, 90, 70, [6.2, 0.5, 7.0, 1.95], (c) => fridge(c, true));
    fridgeClosed.name = "fridge"; objs.push(fridgeClosed);
    add("desk", [80, 10, 80, 66], [7.6, 0.5, 8.85, 1.1], desk);
    add("stool", [76, 40, 50, 36], [7.61, 1.41, 8.09, 1.89], stool);
    add("lights", [90, 4, 60, 70], [6, 0.3, 9, 0.4], stringLights, true);
    const beds = [0, 1, 2].map((wet) => bake(-30, 12, 100, 60, [1.6, 1.6, 4.6, 2.6], (c) => bedBox(c, wet)));
    beds[1].name = "bed"; objs.push(beds[1]);
    objs.push({ name: "plantA", plant: 0.7, gx: 2.05, gy: 2.1, foot: [2.05, 2.1, 2.05, 2.1] });
    objs.push({ name: "plantB", plant: 1, gx: 3.1, gy: 2.1, foot: [3.1, 2.1, 3.1, 2.1] });
    objs.push({ name: "plantC", plant: 0.82, gx: 4.15, gy: 2.1, foot: [4.15, 2.1, 4.15, 2.1] });
    add("shovel", [20, 20, 30, 40], [4.8, 2.8, 5.0, 3.0], shovel);
    add("chairRear", [46, 22, 76, 76], [7.35, 2.27, 8.5, 3.45], chairRear);
    add("table", [38, 46, 44, 44], [6.8, 2.95, 7.15, 3.25], sideTable);
    add("chairNear", [66, 50, 44, 50], [8.45, 2.45, 8.65, 3.45], chairNear);
    add("thermo", [thermoAt[0] - 6, thermoAt[1] - 30, 12, 34], [3.3, 3.95, 3.5, 4.15], (c) => thermometer(c, thermoAt[0], thermoAt[1]));
    const barrelFull = bake(barrelAt[0] - 17, barrelAt[1] - 28, 36, 38, [1.7, 5.85, 2.5, 6.65], (c) => barrelBody(c, barrelAt[0], barrelAt[1], true));
    const barrelEmpty = bake(barrelAt[0] - 17, barrelAt[1] - 28, 36, 38, [1.7, 5.85, 2.5, 6.65], (c) => barrelBody(c, barrelAt[0], barrelAt[1], false));
    barrelFull.name = "barrel"; objs.push(barrelFull);
    add("board", [X(BOARD[0], BOARD[1]) - 6, Y(BOARD[0], BOARD[1]) - 34, 40, 50], [BOARD[0], BOARD[1], BOARD[0] + 1.3, BOARD[1] + 0.1], (c) => board(c, BOARD[0], BOARD[1]));
    add("lantern", [40, 30, 20, 40], [6.05, 3.35, 6.2, 3.5], lanternPost);
    add("rocks", [-60, 70, 170, 60], [0, 0, 0, 0], rocks);
    world = { island: paintIsland(), objs, beds, fridgeClosed, fridgeOpen, barrelFull, barrelEmpty,
      at: { mill: [millAt[0], millAt[1] - 30], fountain: [fountAt[0], fountAt[1] - 16], thermo: thermoAt, barrel: barrelAt }, hose: HOSE };
    return world;
  }

  function rotor(c, x, y, angle) {
    for (let i = 0; i < 4; i++) {
      const a = angle + i * Math.PI / 2, co = Math.cos(a), si = Math.sin(a);
      const ex = x + co * 16, ey = y + si * 16;
      line(c, x, y, ex, ey, "#6b4427");
      const nx = -si, ny = co;
      poly(c, [[x + co * 5 + nx, y + si * 5 + ny], [ex + nx, ey + ny], [ex + nx * 5, ey + ny * 5], [x + co * 5 + nx * 4, y + si * 5 + ny * 4]], "#f3ead6");
    }
    rect(c, x - 1, y - 1, 3, 3, INK);
  }

  function mercury(c, at, share) {
    const h = Math.max(1, Math.round(share * 13));
    rect(c, at[0], at[1] - 10 - h, 1, h, "#c9403a");
  }

  // Up to four notes on the notice board: one for each open step of the plan.
  function boardNotes(c, count) {
    const spots = [[0.12, 25], [0.48, 26], [0.25, 18], [0.75, 17]];
    for (let i = 0; i < Math.min(4, count); i++) {
      const g = BOARD[0] + spots[i][0], z = spots[i][1];
      poly(c, [P3(g, BOARD[1], z), P3(g + 0.28, BOARD[1], z), P3(g + 0.28, BOARD[1], z - 6), P3(g, BOARD[1], z - 6)], "#fff4dd");
      const pin = P3(g + 0.14, BOARD[1], z - 1);
      dot(c, pin[0], pin[1], "#c24a3e");
    }
  }

  // The tube's screen. mode: "game" moves, "idle" is a dim prompt, "off" is dark.
  function monitor(c, mode, t) {
    const gx = 7.6, gy = 0.5, top = 14;
    const o = P3(gx + 0.26, gy + 0.47, top + 12.4);
    const W = Math.round((0.46) * 16), H = 8;
    const at = (u, v, col) => dot(c, o[0] + u, o[1] + Math.floor(u / 2) + v, col);
    if (mode === "off") return;
    if (mode === "idle") { if (Math.floor(t * 2) % 2) at(1, 1, "#7ad06f"); at(0, 1, "#3f8a66"); return; }
    for (let u = 0; u < W; u++) for (let v = 0; v < H; v++) at(u, v, v > 5 ? "#254d34" : "#16233f");
    for (let u = 0; u < W; u++) if ((u + Math.floor(t * 6)) % 4 === 0) at(u, 6, "#3f8a66");
    const px = Math.floor(t * 5) % W, py = 4 - Math.abs(Math.floor(t * 8) % 5 - 2);
    at(px, py, "#ffd54f");
    at((W - 1 - Math.floor(t * 3) % W), 2, "#ff6b6b");
    at(1 + Math.floor(t * 2) % 3, 5, "#81d4fa");
  }

  // ---------- the sky ----------

  const SKIES = {
    night: ["#0b1036", "#1b2262", "#40397f"],
    dim: ["#7fa7ea", "#f0c7a6", "#ffe3b0"],
    day: ["#5fb1ee", "#a0d8f2", "#e4f6ef"],
  };

  function skyColor(stops, t) { return t < 0.55 ? mix(stops[0], stops[1], t / 0.55) : mix(stops[1], stops[2], (t - 0.55) / 0.45); }

  // A sky in bands with a checker between them. d: 0 night, 0.5 a low lamp, 1 a full lamp.
  function bakeSky(w, h, d) {
    const cv = mk(w, h), c = cv.getContext("2d");
    const pick = (t) => {
      if (d <= 0) return skyColor(SKIES.night, t);
      if (d < 0.5) return null;
      return null;
    };
    const stops = (t) => {
      const night = SKIES.night, dim = SKIES.dim, day = SKIES.day;
      const a = d < 0.5 ? night : dim, b = d < 0.5 ? dim : day, u = d < 0.5 ? d / 0.5 : (d - 0.5) / 0.5;
      const cA = hexOf(skyColor(a, t)), cB = hexOf(skyColor(b, t));
      return `rgb(${Math.round(cA[0] + (cB[0] - cA[0]) * u)},${Math.round(cA[1] + (cB[1] - cA[1]) * u)},${Math.round(cA[2] + (cB[2] - cA[2]) * u)})`;
    };
    pick(0);
    const BANDS = 14;
    for (let i = 0; i < BANDS; i++) {
      const y0 = Math.floor(h * i / BANDS), y1 = Math.floor(h * (i + 1) / BANDS);
      c.fillStyle = stops((i + 0.5) / BANDS);
      c.fillRect(0, y0, w, y1 - y0);
      if (i + 1 < BANDS) {
        c.fillStyle = stops((i + 1.5) / BANDS);
        for (let x = 0; x < w; x += 2) { c.fillRect(x, y1 - 1, 1, 1); }
      }
    }
    return cv;
  }
  function hexOf(rgb) { const m = rgb.match(/\d+/g); return [Number(m[0]), Number(m[1]), Number(m[2])]; }

  function sun(c, x, y, t) {
    const a = t * 0.5;
    for (let i = 0; i < 8; i++) {
      const r = a + i * Math.PI / 4, co = Math.cos(r), si = Math.sin(r);
      brush(c, x + co * 14, y + si * 14, x + co * 18, y + si * 18, i % 2 ? 1 : 2, "#ffe082");
    }
    ell(c, x, y, 11, 11, "#ffb300");
    ell(c, x, y, 10, 10, "#ffd54f");
    ell(c, x - 2, y - 2, 6, 6, "#fff1a8");
  }

  const MOON = (function () {
    const cv = mk(26, 26), c = cv.getContext("2d");
    ell(c, 13, 13, 11, 11, "#fff3c4");
    dot(c, 8, 10, "#e5d597"); dot(c, 11, 16, "#e5d597"); dot(c, 7, 15, "#e5d597");
    c.globalCompositeOperation = "destination-out";
    ell(c, 18, 10, 10, 10, "#000");
    c.globalCompositeOperation = "source-over";
    return cv;
  }());
  const CLOUDS = [[38, 12], [54, 15], [28, 10]].map(([w, h], i) => {
    const cv = mk(w, h), c = cv.getContext("2d");
    const n = 3 + i;
    for (let k = 0; k < n; k++) {
      const cx = (k + 0.7) / (n + 0.4) * w, r = (Math.sin(k * 2.4 + i) * 0.3 + 0.8) * h * 0.42;
      ell(c, cx, h - r - 1, r * 1.4, r, "#d9e7f2");
    }
    for (let k = 0; k < n; k++) {
      const cx = (k + 0.7) / (n + 0.4) * w, r = (Math.sin(k * 2.4 + i) * 0.3 + 0.8) * h * 0.42;
      ell(c, cx, h - r - 3, r * 1.25, Math.max(1, r - 1), "#ffffff");
    }
    return cv;
  });

  // Far away, for depth: a small rock with grass, lighter than the garden.
  const ISLET = (function () {
    const cv = mk(46, 26), c = cv.getContext("2d");
    for (let u = -21; u <= 21; u++) {
      const depth = Math.round((1 - Math.abs(u) / 22) * 12 + noise(u / 4, 2) * 5);
      rect(c, 23 + u, 8, 1, depth + 1, u < 0 ? "#b98c64" : "#9a7450");
    }
    ell(c, 23, 7, 22, 4, "#8fc774");
    ell(c, 21, 6, 17, 2, "#a7d98a");
    ell(c, 30, 2, 5, 3, "#6fae6c");
    rect(c, 30, 3, 1, 4, "#8a6a4a");
    return cv;
  }());

  // ---------- windows and the speech bubble ----------

  function windowBox(c, x, y, w, h) {
    rect(c, x + 1, y, w - 2, h, INK);
    rect(c, x, y + 1, w, h - 2, INK);
    rect(c, x + 1, y + 1, w - 2, h - 2, "#7c4d2d");
    rect(c, x + 2, y + 2, w - 4, h - 4, "#fff1cf");
    rect(c, x + 2, y + 2, w - 4, 1, "#fffbea");
    rect(c, x + 2, y + h - 3, w - 4, 1, "#ecd7a6");
  }

  function bubble(c, cx, tipY, lines, minX, maxX, tipX) {
    const tw = Math.max(...lines.map(textWidth));
    const w = tw + 10, h = lines.length * 10 + 7;
    const x = Math.max(minX, Math.min(maxX - w, Math.round(cx - w / 2)));
    const y = Math.round(tipY - h - 5);
    rect(c, x + 1, y, w - 2, h, INK);
    rect(c, x, y + 1, w, h - 2, INK);
    rect(c, x + 1, y + 1, w - 2, h - 2, "#fffdf4");
    rect(c, x + 1, y + h - 3, w - 2, 1, "#e9e2cc");
    const tx = Math.max(x + 4, Math.min(x + w - 6, Math.round(tipX)));
    for (let i = 0; i < 5; i++) {
      rect(c, tx - (4 - i), y + h - 1 + i, (4 - i) * 2 + 1, 1, INK);
      if (i < 4) rect(c, tx - (3 - i), y + h - 2 + i, (3 - i) * 2 + 1, 1, "#fffdf4");
    }
    lines.forEach((row, i) => text(c, row, x + 5, y + 3 + i * 10, "#2b1b18"));
    return { x, y, w, h };
  }

  // ---------- small icons for the status window, 7 x 7 ----------

  const ICON_PAL = { y: "#f2b632", o: "#d98a1f", r: "#d4503f", w: "#fff8ea", b: "#3f8fd0", B: "#2a6496", g: "#3a9449", G: "#2c7a3f", k: "#3b2a1d", s: "#8d5a33", m: "#8a8f96" };
  const ICONS = {
    light: rows(["y..y..y", ".y.y.y.", "..yyy..", "yyyyyyy", "..yyy..", ".y.y.y.", "y..y..y"], ICON_PAL),
    temp: rows(["..kkk..", "..krk..", "..krk..", "..krk..", ".krrrk.", ".krrrk.", "..kkk.."], ICON_PAL),
    hum: rows(["...b...", "..bbb..", "..bbb..", ".bbwbb.", ".bwbbb.", ".bbbbB.", "..bBB.."], ICON_PAL),
    vpd: rows(["...g...", "g..g..g", "gg.g.gg", ".ggggg.", "..ggg..", "...G...", "...G..."], ICON_PAL),
    soil: rows(["...b...", "..bbb..", "..bbb..", "...b...", "sssssss", "s.s.s.s", "sssssss"], ICON_PAL),
    tank: rows([".mmmmm.", ".m...m.", ".mbbbm.", ".mbbbm.", ".mbbbm.", ".mbbbm.", ".mmmmm."], ICON_PAL),
    height: rows(["kkk....", "k......", "kk.....", "k...g.g", "kkk.ggg", "k....g.", "kk...g."], ICON_PAL),
    clock: rows(["..kkk..", ".k.k.k.", "k..k..k", "k..kk.k", "k.....k", ".k...k.", "..kkk.."], ICON_PAL),
  };

  // ---------- his portrait: bigger pixels of the same man, for the window under the garden ----------

  const PORTRAIT_SIZE = 44;
  const portraits = new Map();
  function portrait(mood) {
    if (portraits.has(mood)) return portraits.get(mood);
    const cv = mk(PORTRAIT_SIZE, PORTRAIT_SIZE), c = cv.getContext("2d");
    const hair = "#b0663c", hairD = "#6d3c23", skin = "#f9cfaa", skinD = "#e2a27a";
    rect(c, 0, 0, PORTRAIT_SIZE, PORTRAIT_SIZE, mood === "sleep" ? "#2f3a68" : "#a9dcf0");
    if (mood !== "sleep") { for (let i = 0; i < 26; i++) dot(c, hash(i, 31) * 44, 30 + hash(i, 32) * 14, "#94cfe6"); }
    // shoulders and collar
    poly(c, [[3, 44], [8, 35], [18, 32], [28, 32], [38, 35], [42, 44]], "#d4503f");
    for (const u of [11, 17, 28, 34]) line(c, u, 35, u - 1, 44, "#9a332b");
    line(c, 5, 40, 41, 40, "#9a332b");
    poly(c, [[17, 31], [29, 31], [31, 37], [23, 41], [15, 37]], "#37a86c");
    poly(c, [[23, 34], [29, 31], [31, 37], [23, 41]], "#1f6e45");
    poly(c, [[19, 26], [27, 26], [27, 33], [23, 35], [19, 33]], skinD);
    // hair behind
    poly(c, [[9, 16], [7, 25], [10, 23], [10, 29], [14, 27], [14, 14]], hairD);
    poly(c, [[35, 16], [37, 24], [34, 23], [34, 27], [31, 26], [31, 14]], hairD);
    // face: a grown man's jaw, not a round child's cheek
    poly(c, [[11, 13], [33, 13], [34, 21], [32, 27], [27, 32], [18, 32], [13, 27], [11, 21]], skin);
    poly(c, [[33, 13], [34, 21], [32, 27], [27, 32], [24, 32], [29, 25], [30, 13]], skinD);
    ell(c, 11, 21, 2, 3, skinD); ell(c, 34, 21, 1, 3, skinD);
    // stubble along the jaw
    for (let i = 0; i < 26; i++) {
      const u = 14 + hash(i, 41) * 17, v = 26 + hash(i, 42) * 6;
      if (v < 32 - Math.abs(u - 22.5) * 0.45) dot(c, u, v, "#d4986f");
    }
    // eyes: narrow and sharp, with the highlight every anime eye has
    const eye = (x, flipX) => {
      const dir = flipX ? -1 : 1;
      if (mood === "sleep") { line(c, x, 21, x + 5, 21, INK); return; }
      if (mood === "joy") { line(c, x, 21, x + 2, 19, INK); line(c, x + 3, 19, x + 5, 21, INK); return; }
      const top = mood === "chill" ? 20 : 17;
      if (mood === "chill") rect(c, x - 1, 18, 8, 2, skinD);
      rect(c, x, top, 7, 23 - top, WHITE);
      rect(c, x + 2, top, 4, 23 - top, IRIS);
      rect(c, x + 3, top, 3, 22 - top, EYE);
      rect(c, x + 2, 22, 4, 1, "#5fb88a");
      dot(c, x + 3, top + 1, WHITE);
      if (mood !== "chill") { dot(c, x + 4, top + 1, WHITE); dot(c, x + 5, top + 3, WHITE); }
      line(c, x, top - 1, x + 6, top - 1, INK);
      line(c, x, top - 2 + (dir > 0 ? 1 : 0), x + 6, top - 2 + (dir > 0 ? 0 : 1), INK);
      dot(c, dir > 0 ? x - 1 : x + 7, top, INK);
      line(c, x + 2, 23, x + 5, 23, "#b8724e");
    };
    eye(13, false); eye(25, true);
    // brows
    if (mood === "worry" || mood === "alert") {
      brush(c, 13, 15, 19, 17, 1, hairD); brush(c, 13, 14, 19, 16, 1, hairD);
      brush(c, 25, 16, 31, 14, 1, hairD); brush(c, 25, 15, 31, 13, 1, hairD);
    } else if (mood !== "chill" && mood !== "sleep") {
      line(c, 13, 16, 19, 15, hairD); line(c, 13, 15, 19, 14, hairD); line(c, 25, 15, 31, 16, hairD); line(c, 25, 14, 31, 15, hairD);
    }
    // nose, moustache, mouth
    line(c, 23, 21, 24, 25, skinD);
    poly(c, [[15, 28], [19, 25], [23, 26], [27, 25], [31, 28], [27, 28], [23, 27], [19, 28]], "#6b3f24");
    if (mood === "alert") { ell(c, 23, 30, 2, 1, "#7a2c2c"); }
    else if (mood === "worry") { line(c, 20, 30, 26, 30, "#7a2c2c"); }
    else if (mood === "joy") { poly(c, [[18, 29], [28, 29], [26, 31], [20, 31]], "#7a2c2c"); line(c, 19, 29, 27, 29, WHITE); }
    else if (mood === "sleep") { line(c, 21, 30, 24, 30, "#7a2c2c"); }
    else { line(c, 19, 29, 22, 30, "#7a2c2c"); line(c, 22, 30, 27, 29, "#7a2c2c"); }
    // the fringe: spikes over the forehead
    poly(c, [[10, 13], [34, 13], [34, 17], [30, 15], [28, 18], [25, 14], [22, 18], [19, 14], [16, 18], [14, 15], [11, 19]], hair);
    poly(c, [[10, 13], [17, 13], [14, 15], [11, 19]], hairD);
    line(c, 22, 14, 22, 18, hairD); line(c, 28, 14, 28, 18, hairD);
    // the hat
    const hatY = mood === "sleep" ? 3 : 0;
    poly(c, [[12, 12 + hatY], [14, 3 + hatY], [22, 1 + hatY], [31, 3 + hatY], [33, 12 + hatY]], "#e9bd60");
    poly(c, [[14, 3 + hatY], [22, 1 + hatY], [24, 1 + hatY], [20, 12 + hatY], [12, 12 + hatY]], "#f8de8e");
    poly(c, [[28, 2 + hatY], [31, 3 + hatY], [33, 12 + hatY], [29, 12 + hatY]], "#c99a44");
    rect(c, 12, 9 + hatY, 21, 3, "#37a86c");
    rect(c, 27, 9 + hatY, 6, 3, "#1f6e45");
    poly(c, [[2, 14 + hatY], [12, 11 + hatY], [33, 11 + hatY], [43, 14 + hatY], [34, 16 + hatY], [10, 16 + hatY]], "#e9bd60");
    poly(c, [[2, 14 + hatY], [12, 11 + hatY], [22, 11 + hatY], [18, 14 + hatY]], "#f8de8e");
    poly(c, [[10, 16 + hatY], [34, 16 + hatY], [39, 15 + hatY], [6, 15 + hatY]], "#b5842f");
    poly(c, [[26, 8 + hatY], [31, 4 + hatY], [33, 7 + hatY], [29, 9 + hatY]], "#7ad06f");
    portraits.set(mood, cv);
    return cv;
  }

  window.GardenArt = {
    buildWorld, hank, plant, rotor, mercury, boardNotes, monitor, bakeSky, sun, MOON, CLOUDS, ISLET, text, textWidth, wrap, windowBox, bubble, ICONS, portrait,
    PORTRAIT_SIZE, rect, dot, line, ell, brush, X, Y, P3, hash, BULB_LINE, BULB_COLS, LAMPS, INK, SIZE,
  };
})();
