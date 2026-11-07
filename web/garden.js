// Hank's garden: after a minute without input the cockpit shows what Hank is doing, as a small pixel world on top of the page.
// What he does comes from web/garden-logic.js, so every action on screen is something the box really reported: the pump runs and he
// waters, the tank is dry and he says so, the lamp goes off and it is night. Nothing here switches a device or writes anything.
// The owner can play: a click sends Hank to a thing or a spot (Scene.click). That is animation and a reading of real values, the
// watering can in his hand is for show. What the box reports comes first: a new alarm takes him away from whatever he was told.
// A guest of the page: it reads the event "vgrow:state" and GET /api/hank/state, stays until its button or Esc closes it, and without
// its art file the cockpit runs on as before. Loaded after app.js and uses its globals t(), fmt(), when(), api().
(function () {
  "use strict";

  const A = window.GardenArt, L = window.GardenLogic;
  if (!A || !L) return;

  const IDLE_MS = 60 * 1000;
  const FPS = 15;
  const PACE = 0.65;  // the world's seconds per real second: the owner wants to stay a while and follow what happens (06.11.2026)
  const WALK_SPEED = 1.5;  // grid tiles per second of the world
  const HANK_POLL_MS = 15 * 1000;
  const SOFT_WORK_S = 150, SOFT_BREAK_S = 120;  // the climate is off for hours at a time: he works on it, then takes a break
  const SOFT_HOLD_S = 45;  // a value on the band's edge must not make him run back and forth
  const TODO_EVERY_S = 300;
  const STEP_SHOWN_S = 1800;  // the autopilot's last exhaust step is the line for half an hour, its gap before the next one (DFAN_SAME_GAP)
  const PIN_KEY = "vgrow.garden.pin";
  const BAR_GAP = 2, BAR_OUTLINE = 2;  // scene pixels between a text and the two buttons; their CSS outline, which lies outside their box

  // ---------- where he can walk ----------

  const NODES = {
    hub: [5.6, 3.3], thermo: [3.3, 3.3], west: [1.9, 3.3], barrel: [1.9, 5.45], fountain: [1.9, 3.05],
    back: [5.6, 1.08], bed: [3.55, 1.08], deck: [6.3, 3.56], lane: [6.3, 1.92], fridge: [6.42, 1.92], desk: [7.85, 1.98],
    front: [8.0, 3.6], board: [5.6, 4.75], boardSpot: [8.0, 4.75],
  };
  const EDGES = [["hub", "thermo"], ["thermo", "west"], ["west", "barrel"], ["west", "fountain"], ["hub", "back"], ["back", "bed"], ["hub", "deck"],
    ["deck", "lane"], ["lane", "fridge"], ["fridge", "desk"], ["deck", "front"], ["hub", "board"], ["board", "boardSpot"]];
  const SEATS = { chair: [8.02, 2.92], stool: [7.85, 1.62] };

  function route(from, to) {
    const seen = { [from]: null }, queue = [from];
    while (queue.length) {
      const cur = queue.shift();
      if (cur === to) break;
      for (const [a, b] of EDGES) {
        const next = a === cur ? b : b === cur ? a : null;
        if (next && !(next in seen)) { seen[next] = cur; queue.push(next); }
      }
    }
    const path = [];
    for (let cur = to; cur; cur = seen[cur]) path.unshift(cur);
    return path;
  }

  function nearestNode(gx, gy, allowed) {
    let best = null, dist = Infinity;
    for (const name of Object.keys(NODES)) {
      const d = Math.hypot(NODES[name][0] - gx, NODES[name][1] - gy);
      if (d < dist && (!allowed || allowed(NODES[name]))) { dist = d; best = name; }
    }
    return best;
  }

  const FACE = { SE: ["F", false], SW: ["F", true], NE: ["B", false], NW: ["B", true] };

  // ---------- what he does, as steps ----------

  const go = (to, prop) => ({ go: to, prop });
  const act = (pose, seconds, more) => ({ pose, left: seconds, ...more });
  const leaveSeat = (more) => ({ pose: "stand", left: 0.3, ...more });

  const CHILL = {
    beer: { say: "garden.say.beer", doing: "garden.do.beer", mood: "joy" },
    juice: { say: "garden.say.juice", doing: "garden.do.juice", mood: "ok" },
    cig: { say: "garden.say.cig", doing: "garden.do.cig", mood: "chill" },
    game: { say: "garden.say.game", doing: "garden.do.game", mood: "joy" },
    inspect: { say: "garden.say.inspect", doing: "garden.do.inspect", mood: "ok" },
    nap: { say: "garden.say.nap", doing: "garden.do.nap", mood: "sleep" },
  };

  function drinkSteps(prop) {
    const out = [go("fridge"), act("reach", 1.8, { face: "NE", fridge: true }), act("stand", 0.6, { face: "NE", prop, fridge: true }), go("front", prop), { seat: "chair", face: "SW", prop, talk: true }];
    for (let i = 0; i < 5; i++) {
      out.push(act("hold", 5 + i, { prop, eyes: i > 2 ? "half" : "open", mouth: "smile" }));
      out.push(act("sip", 1.8, { prop, angle: -2.1, eyes: "closed" }));
    }
    out.push(act("hold", 5, { prop, mouth: "smile" }), leaveSeat({ prop }));
    return out;
  }
  function smokeSteps(prop, rounds) {
    const out = [go("front"), { seat: "chair", face: "SW", prop, talk: true }];
    for (let i = 0; i < rounds; i++) {
      out.push(act("sip", 2.2, { prop, eyes: "half", inhale: true }));
      out.push(act("hold", 6, { prop, eyes: "half", mouth: "smile", exhale: true, smoke: true }));
    }
    out.push(leaveSeat());
    return out;
  }

  function chillSteps(name) {
    switch (name) {
      case "beer": return drinkSteps("beer");
      case "juice": return drinkSteps("juice");
      case "cig": return smokeSteps("cig", 4);
      case "game": return [go("desk"), { seat: "stool", face: "NE", talk: true }, act("type", 24, { view: "B", screen: true }), act("cheer", 2.2, { view: "B", screen: true }),
        act("type", 26, { view: "B", screen: true }), act("cheer", 2.2, { view: "B", screen: true }), act("type", 18, { view: "B", screen: true }), leaveSeat()];
      case "inspect": return [go("bed"), act("crouch", 6, { face: "SW", talk: true, eyes: "open", mouth: "smile" }), act("stand", 1.5, { face: "SW", mouth: "smile" })];
      case "nap": return [go("front"), { seat: "chair", face: "SW", talk: true }, act("nap", 80, { hat: "low", eyes: "closed", zzz: true }), leaveSeat()];
      default: return [act("stand", 4, { face: "SE" })];
    }
  }

  function jobSteps(job) {
    switch (job) {
      case "water": return [go("barrel"), act("fill", 2.6, { face: "SW", prop: "can", angle: 0, fill: true }), go("bed", "can"), act("pour", 7, { face: "SW", prop: "can", angle: 0.62, pour: true, mouth: "smile" })];
      case "tank": return [go("barrel"), act("lookin", 5, { face: "SW", eyes: "wide", mouth: "open", mark: "!" }), act("scratch", 4, { face: "SE", eyes: "worry", mouth: "open", mark: "?" }), act("wave", 3, { face: "SE", eyes: "wide", mouth: "open" })];
      case "thirsty": return [go("bed"), act("crouch", 4, { face: "SW", eyes: "worry", mouth: "flat" }), act("wave", 3, { face: "SW", eyes: "worry", mouth: "open" }), act("hip", 3, { face: "SW", eyes: "worry", mouth: "flat" })];
      case "soggy": return [go("bed"), act("crouch", 5, { face: "SW", eyes: "worry", mouth: "flat" }), act("hip", 4, { face: "SW", eyes: "worry", mouth: "flat" })];
      case "poke": return [go("bed"), act("crouch", 7, { face: "SW", mark: "?" }), act("wave", 2.5, { face: "SW", mouth: "open" })];
      case "hot": return [go("thermo"), act("fan", 9, { face: "SE", prop: "hat", hat: "off", eyes: "half", mouth: "open", sweat: true })];
      case "cold": return [go("thermo"), act("shiver", 9, { face: "SE", scarf: true, jitter: true, eyes: "worry", mouth: "flat" })];
      case "dry": return [go("bed"), act("spray", 6, { face: "SW", prop: "spray", spray: true }), act("stand", 1.2, { face: "SW", prop: "spray" })];
      case "humid": return [go("bed"), act("umbrella", 9, { face: "SW", prop: "umbrella", eyes: "half", mouth: "flat", drips: true })];
      case "mist": return [go("fountain"), act("lookin", 5, { face: "NW", eyes: "worry", mark: "!" }), act("hip", 3, { face: "SE", eyes: "worry", mouth: "flat" })];
      case "lost": return [go("boardSpot"), act("scratch", 7, { face: "SW", eyes: "worry", mouth: "flat", mark: "?" })];
      case "todo": return [go("boardSpot"), act("point", 9, { face: "SW", talk: true, mouth: "open" }), act("stand", 1.5, { face: "SW" })];
      default: return [act("stand", 3, { face: "SE" })];
    }
  }

  // ---------- what a click means ----------

  // Boxes in island pixels (X, Y of web/garden-art.js), front to back: the first hit counts. Each thing tells what it really stands for.
  const THINGS = [
    { name: "hank", label: "garden.thing.hank" },
    { name: "board", label: "garden.thing.board", box: [10, 62, 34, 46] },
    { name: "table", label: "garden.thing.table", box: [52, 55, 12, 27] },
    { name: "chair", label: "garden.thing.chair", box: [64, 54, 38, 41] },
    { name: "lantern", label: "garden.thing.lamp", box: [39, 48, 11, 30] },
    { name: "thermo", label: "garden.thing.thermo", box: [-17, 28, 14, 36] },
    { name: "barrel", label: "garden.thing.barrel", box: [-83, 39, 36, 38] },
    { name: "pc", label: "garden.thing.pc", box: [104, 34, 32, 44] },
    { name: "fridge", label: "garden.thing.fridge", box: [80, 24, 24, 41] },
    { name: "plants", label: "garden.thing.plants" },
    { name: "fountain", label: "garden.thing.fountain", box: [-48, 11, 32, 30] },
    { name: "windmill", label: "garden.thing.windmill", box: [-82, -3, 32, 50] },
    { name: "tree", label: "garden.thing.tree", box: [-24, -36, 52, 50] },
  ];
  const SUN = { name: "sun", label: "garden.thing.lamp" };
  const OVER_WINDOW = { name: "window" };

  // ---------- the scene ----------

  const STARS = Array.from({ length: 90 }, (_, i) => [A.hash(i, 51), A.hash(i, 52), A.hash(i, 53)]);
  const num = (value) => (typeof value === "number" && isFinite(value) ? value : null);
  const DIGITS = { temp: 1, hum: 0, vpd: 2, soil: 0, height: 0, litres: 1, depth: 0, level: 0, dfan: 0, cfan: 0 };

  function textOf(key, params) {
    const out = {};
    for (const name of Object.keys(params || {})) {
      const value = params[name];
      out[name] = typeof value === "number" ? fmt(value, DIGITS[name] === undefined ? 1 : DIGITS[name]) : (value === null || value === undefined ? "?" : value);
    }
    return t(key, out);
  }

  function offscreen(w, h) { const cv = document.createElement("canvas"); cv.width = w; cv.height = h; return cv; }

  class Scene {
    constructor(canvas, options) {
      this.cv = canvas;
      this.c = canvas.getContext("2d");
      this.still = Boolean(options && options.still);
      this.world = A.buildWorld();
      this.time = 0;
      this.a = { gx: NODES.hub[0], gy: NODES.hub[1], view: "F", flip: false, pose: "stand", prop: null, walk: 0, step: null };
      this.queue = [];
      this.parts = [];
      this.path = [];
      this.state = null;
      this.hank = null;
      this.decision = null;
      this.lastChill = null;
      this.saying = null;
      this.doing = null;
      this.mood = "ok";
      this.fridgeUntil = 0;
      this.rotor = 0;
      this.d = null;
      this.skyD = null;
      this.sky = null;
      this.flies = Array.from({ length: 12 }, (_, i) => ({ x: (A.hash(i, 61) - 0.5) * 240, y: 20 + A.hash(i, 62) * 110, p: A.hash(i, 63) * 9 }));
      this.mode = "work";
      this.softFlip = 0;
      this.pending = null;
      this.nextTodo = TODO_EVERY_S / 3;
      this.nextSay = 0;
      this.talkKey = null;
      this.order = null;
      this.target = null;
      this.hover = null;
      this.sunAt = null;
      this.bar = null;
      this.besideBar = false;
      this.boardNext = 0;
      this.fridgeNext = 0;
      this.resize(canvas.width, canvas.height);
    }

    resize(w, h) {
      this.W = w; this.H = h;
      this.cv.width = w; this.cv.height = h;
      this.layer = offscreen(w, h);
      this.copy = offscreen(w, h);
      this.skyD = null;
      this.tall = h > w * 1.05;
      if (this.tall) {
        const statsH = 58, dialogH = 58;
        this.dialog = { x: 3, y: h - dialogH - 3, w: w - 6, h: dialogH };
        this.stats = { x: 3, y: h - dialogH - statsH - 5, w: w - 6, h: statsH, cols: 2 };
        const free = this.stats.y;
        this.oy = Math.max(52, Math.round((free - 186) / 2 + 26));
      } else {
        const dialogH = 50;
        this.dialog = { x: 3, y: h - dialogH - 2, w: w - 6, h: dialogH };
        this.stats = { x: 3, y: 3, w: 84, h: 92, cols: 1 };
        this.oy = Math.max(40, Math.round((h - dialogH - 150) / 2 + 8));  // at the raised bed two lines fit above his head; what the window hides then is only grass
      }
      this.ox = Math.round(w / 2) + (this.tall ? 0 : 18);
    }

    // ---------- from the box ----------

    feed(state, hank) {
      this.state = state;
      if (hank !== undefined) this.hank = hank;
      const next = L.decide(state, this.hank);
      const cur = this.decision;
      if (!cur) { this.adopt(next); return; }
      if (next.urgent || cur.urgent) {
        const same = next.job === cur.job && next.say === cur.say;
        if (same) { this.decision = next; this.refreshTalk(); }
        else if (this.order && !next.urgent) this.decision = next;  // what was urgent is over: what he was told runs on
        else this.adopt(next);
        this.pending = null;
        return;
      }
      if (next.job === cur.job) { this.decision = next; this.pending = null; this.refreshTalk(); return; }
      if (!this.pending || this.pending.job !== next.job) this.pending = { job: next.job, at: this.time };
      else if (this.time - this.pending.at >= SOFT_HOLD_S && !this.order) { this.pending = null; this.adopt(next); }
    }

    adopt(decision) {
      const wasChill = this.decision && this.decision.job === "chill";
      this.decision = decision;
      this.mode = "work";
      this.softFlip = this.time + SOFT_WORK_S;
      this.order = null;
      this.saying = null;
      this.talkKey = null;
      this.lastChill = wasChill ? this.lastChill : null;
      this.queue = [];
      this.a.step = null;
      this.path = [];
      this.a.prop = null;
      this.startNext();
    }

    refreshTalk() {
      const d = this.decision;
      if (d && d.urgent && d.say && !this.order && this.mode === "work") this.say(textOf(d.say, d.params), Infinity);
    }

    say(textValue, seconds) {
      if (!textValue) { this.saying = null; return; }
      const maxW = this.tall ? Math.min(this.W - 20, 170) : 168;
      this.saying = { lines: A.wrap(textValue, maxW), until: this.time + seconds, text: textValue };
      this.sr = textValue;
    }

    // What he says when the plan's numbers are in order: the routine's line, with real values.
    chillParams() {
      const c = L.climate(this.state || {});
      return { temp: c.temp, hum: c.hum, height: L.heightCm(this.state || {}) };
    }

    pickChill() {
      const pool = L.chillPool(this.state || {}, L.lightsOn(this.state || {}));
      const options = pool.filter((name) => name !== this.lastChill);
      const name = (options.length ? options : pool)[Math.floor(Math.random() * (options.length || pool.length))];
      this.lastChill = name;
      return name;
    }

    // What hangs on the notice board, alarms nobody can act on right now first. Each visit takes the next note.
    boardNote() {
      const s = this.state || {};
      const notes = [...L.notices(s).map((label) => ["garden.say.notice", { label }]), ...L.openTodos(s).map((row) => ["garden.say.todo", { title: row.title }])];
      return notes.length ? notes[this.boardNext++ % notes.length] : null;
    }

    refill() {
      this.order = null;
      const d = this.decision || { job: "chill" };
      if (d.job === "chill" || this.onBreak(d)) {
        if (d.job === "chill" && this.time >= this.nextTodo) {
          const note = this.boardNote();
          if (note) {
            this.nextTodo = this.time + TODO_EVERY_S;
            this.queue = jobSteps("todo");
            this.talkKey = note[0];
            this.talkParams = note[1];
            this.doing = "garden.do.todo";
            this.mood = "think";
            return;
          }
        }
        const name = this.pickChill();
        this.queue = chillSteps(name);
        const row = CHILL[name];
        if (d.job === "chill") {
          this.talkKey = row.say;
          this.talkParams = this.chillParams();
        } else if (d.urgent) {
          this.talkKey = d.say;  // on his break he keeps saying what is wrong
          this.talkParams = d.params;
          this.nextSay = this.time + 8;
        } else {
          this.talkKey = "garden.say.break";
          this.talkParams = {};
        }
        this.doing = row.doing;
        this.mood = row.mood;
        return;
      }
      this.queue = jobSteps(d.job);
      this.doing = d.doing;
      this.mood = d.mood;
      this.talkKey = d.soft ? d.say : null;
      this.talkParams = d.params;
      if (d.soft) this.nextSay = this.time + 2;
      if (d.urgent && d.say) this.say(textOf(d.say, d.params), Infinity);
    }

    // Nothing keeps him on one spot for good: the box can stay off for hours, or wait for something in the post. Only the pump is
    // the end of it by itself: as long as it runs, he waters.
    takesBreaks(d) { return Boolean(d && (d.soft || d.urgent) && d.job !== "water"); }
    onBreak(d) { return this.takesBreaks(d) && this.mode === "break"; }

    startNext() {
      const d = this.decision;
      if (!this.order && this.takesBreaks(d) && this.time >= this.softFlip) {
        this.mode = this.mode === "work" ? "break" : "work";
        this.softFlip = this.time + (this.mode === "work" ? SOFT_WORK_S : SOFT_BREAK_S);
        if (d.urgent) { this.saying = null; this.queue = []; }
      }
      if (!this.queue.length) { this.nextSay = Infinity; this.refill(); }
      const step = this.queue.shift();
      const a = this.a;
      a.step = step;
      if (step.prop !== undefined) a.prop = step.prop;
      else if (!step.go) a.prop = null;
      if (step.go) {
        const start = nearestNode(a.gx, a.gy, (p) => this.clear([a.gx, a.gy], p)) || nearestNode(a.gx, a.gy);
        const nodes = route(start, step.go);
        this.path = nodes.map((name) => NODES[name]);
        if (step.to) this.path.push(step.to);
        if (this.still) {
          const last = this.path[this.path.length - 1];
          a.gx = last[0]; a.gy = last[1];
          this.path = [];
        }
        a.pose = "walk";
        return;
      }
      if (step.seat) {
        const seat = SEATS[step.seat];
        a.gx = seat[0]; a.gy = seat[1];
        a.pose = step.seat === "stool" ? "type" : "hold";
        if (step.face) { a.view = FACE[step.face][0]; a.flip = FACE[step.face][1]; }
        step.left = 0.15;
      } else {
        a.pose = step.pose;
        if (step.face) { a.view = FACE[step.face][0]; a.flip = FACE[step.face][1]; }
        if (step.view) a.view = step.view;
      }
      if (step.fridge) this.fridgeUntil = this.time + step.left + 0.2;
      if (step.talk && this.talkKey) { this.say(textOf(this.talkKey, this.talkParams), 8); this.nextSay = this.time + 45; }
    }

    // ---------- time ----------

    step(realDt) {
      const dt = realDt * PACE;
      this.time += dt;
      const s = this.state || {};
      const a = this.a;
      if (!a.step) this.startNext();
      const stepNow = a.step;
      if (stepNow && stepNow.go) {
        let budget = WALK_SPEED * dt;
        while (budget > 0 && this.path.length) {
          const target = this.path[0], dx = target[0] - a.gx, dy = target[1] - a.gy, dist = Math.hypot(dx, dy);
          if (dist > 0.02) {
            if (Math.abs(dx) >= Math.abs(dy)) { a.view = dx > 0 ? "F" : "B"; a.flip = dx <= 0; }
            else { a.view = dy > 0 ? "F" : "B"; a.flip = dy > 0; }
          }
          if (dist <= budget) { a.gx = target[0]; a.gy = target[1]; budget -= dist; this.path.shift(); }
          else { a.gx += dx / dist * budget; a.gy += dy / dist * budget; budget = 0; }
        }
        a.walk += dt * 2.1;
        if (!this.path.length) { a.step = null; this.startNext(); }
      } else if (stepNow) {
        stepNow.left -= dt;
        if (stepNow.left <= 0) { a.step = null; this.startNext(); }
      }
      if (this.saying && this.time > this.saying.until) this.saying = null;
      const d = this.decision;
      const chatty = d && (!d.urgent || this.order || this.mode === "break") && this.talkKey;
      if (chatty && !this.saying && this.time > this.nextSay && a.step && !a.step.go) {
        this.say(textOf(this.talkKey, this.talkParams), 7);
        this.nextSay = this.time + 40 + Math.random() * 25;
      }
      this.rotor += dt * (0.12 + (num(s.dfan && s.dfan.step) || 0) * 0.1);  // the owner: "way too fast". Still shows the exhaust step
      this.stepDaylight(dt);
      if (!this.still) this.stepParts(dt);
    }

    stepDaylight(dt) {
      const s = this.state || {};
      const level = num(s.light && s.light.level);
      const target = level === null ? 0.8 : level <= 0 ? 0 : 0.35 + 0.65 * (level / 100);
      if (this.d === null || this.still) this.d = target;
      else {
        const change = dt / 4;
        this.d += Math.max(-change, Math.min(change, target - this.d));
      }
    }

    spawn(kind, x, y, vx, vy, life, extra) { if (this.parts.length < 260) this.parts.push({ kind, x, y, vx, vy, life, age: 0, ...extra }); }

    stepParts(dt) {
      const s = this.state || {};
      const list = this.parts;
      for (let i = list.length - 1; i >= 0; i--) {
        const p = list[i];
        p.age += dt;
        if (p.age >= p.life) { list.splice(i, 1); continue; }
        p.x += p.vx * dt;
        p.y += p.vy * dt;
        if (p.kind === "drop" || p.kind === "sweat") p.vy += 120 * dt;
        if (p.kind === "smoke") { p.vx += Math.sin(this.time * 2 + p.y) * dt * 5; }
      }
      const chance = (rate) => Math.random() < rate * dt;
      const a = this.a, st = a.step;
      const fx = this.fx || {};
      if (st && st.pour && fx.tip) {
        for (let i = 0; i < 3; i++) if (chance(14)) this.spawn("drop", fx.tip[0] + (Math.random() - 0.5) * 2, fx.tip[1], (a.flip ? -1 : 1) * (10 + Math.random() * 14), 4, 0.42);
      }
      if (st && st.fill) { if (chance(14)) this.spawn("drop", fx.hand ? fx.hand[0] + (a.flip ? -3 : 3) : 0, fx.hand ? fx.hand[1] - 7 : 0, 0, 10, 0.2); }
      if (st && st.spray && fx.tip) { if (chance(26)) this.spawn("mist", fx.tip[0], fx.tip[1], (a.flip ? -1 : 1) * (16 + Math.random() * 12), (Math.random() - 0.5) * 8, 0.8); }
      if (st && st.smoke && fx.tip) { if (chance(6)) this.spawn("smoke", fx.tip[0], fx.tip[1], (Math.random() - 0.5) * 2, -7 - Math.random() * 4, 2.6); }
      if (st && st.inhale && fx.tip) { if (chance(3)) this.spawn("smoke", fx.tip[0], fx.tip[1], 0, -6, 1.6); }
      if (st && st.exhale && st.left > 4.8 && fx.mouth) { if (chance(16)) this.spawn("smoke", fx.mouth[0], fx.mouth[1], (a.flip ? -1 : 1) * (8 + Math.random() * 6), -5 - Math.random() * 4, 2.4, { big: true }); }
      if (st && st.sweat && fx.head) { if (chance(2.4)) this.spawn("sweat", fx.head[0] + (Math.random() - 0.5) * 12, fx.head[1] + 5, (Math.random() - 0.5) * 12, -14, 0.6); }
      if (st && st.zzz && fx.head) { if (chance(0.55)) this.spawn("z", fx.head[0] + 4, fx.head[1] + 2, 3, -6, 3.2); }
      if (st && st.drips) { if (chance(6)) this.spawn("drop", A.X(a.gx, a.gy) + (Math.random() - 0.5) * 34, A.Y(a.gx, a.gy) - 46, 0, 28, 0.8); }
      // the pump: water runs down the drip line, whoever holds the can
      if (L.pumpOn(s)) {
        for (const o of this.world.objs) {
          if (o.plant && chance(2.5)) this.spawn("drop", A.X(o.gx, o.gy) + (Math.random() - 0.5) * 6, A.Y(o.gx, o.gy, 12), 0, 6, 0.24);
        }
      }
      // the humidifier breathes out of the fountain
      if (L.humidifierOn(s) && chance(5)) {
        const at = this.world.at.fountain;
        this.spawn("mist", at[0] + (Math.random() - 0.5) * 4, at[1] - 1, 5 + Math.random() * 8, 1 - Math.random() * 3, 3);
      }
      // circulation: a leaf or a bit of seed in the air
      const wind = num(s.cfan && s.cfan.step) || 0;
      if (wind > 0 && chance(0.09 * wind)) this.spawn("leaf", -150 + Math.random() * 60, 10 + Math.random() * 100, 20 + wind * 5, 2 + Math.random() * 6, 5);
    }

    // ---------- the owner's clicks ----------

    // Nothing of what stands on the island in the way of a straight walk.
    clear(from, to) {
      const steps = Math.ceil(Math.hypot(to[0] - from[0], to[1] - from[1]) / 0.1);
      for (let i = 3; i <= steps; i++) {
        const u = i / steps, gx = from[0] + (to[0] - from[0]) * u, gy = from[1] + (to[1] - from[1]) * u;
        for (const o of this.world.objs) {
          const f = o.foot;
          if (!f || o.plant || (f[0] === f[2] && f[1] === f[3])) continue;
          if (gx > f[0] - 0.12 && gx < f[2] + 0.12 && gy > f[1] - 0.12 && gy < f[3] + 0.12) return false;
        }
      }
      return true;
    }

    // A walk to any spot: along the paths, the last piece straight if the way is free. Otherwise as close as the paths get.
    leg(gx, gy, more) {
      const node = nearestNode(gx, gy, (p) => this.clear(p, [gx, gy]));
      return node ? { go: node, to: [gx, gy], ...more } : { go: nearestNode(gx, gy), ...more };
    }

    boxOf(row) {
      if (row.name === "hank") return [A.X(this.a.gx, this.a.gy) - 10, A.Y(this.a.gx, this.a.gy) - 42, 20, 44];
      if (row.name === "plants") {
        const height = L.heightCm(this.state || {});
        const tall = height === null ? 0 : Math.min(72, 6 + height * 0.8);
        return [-16, 18 - tall, 64, 40 + tall];
      }
      return row.box;
    }

    thingAt(x, y) {
      const inside = (b, px, py) => px >= b[0] && py >= b[1] && px < b[0] + b[2] && py < b[1] + b[3];
      for (const w of [this.stats, this.dialog]) if (inside([w.x, w.y, w.w, w.h], x, y)) return OVER_WINDOW;
      if (this.sunAt && Math.hypot(x - this.sunAt[0], y - this.sunAt[1]) < 18) return SUN;
      const ix = x - this.ox, iy = y - this.oy;
      return THINGS.find((row) => inside(this.boxOf(row), ix, iy)) || null;
    }

    // The grid under a point, or null off the island.
    groundAt(x, y) {
      const ix = x - this.ox, iy = y - this.oy;
      const gx = (iy / 8 + ix / 16) / 2, gy = (iy / 8 - ix / 16) / 2;
      return gx >= 0.35 && gx <= 8.75 && gy >= 0.35 && gy <= 8.75 ? [gx, gy] : null;
    }

    hoverAt(x, y) {
      const row = this.thingAt(x, y);
      this.hover = row && row !== OVER_WINDOW ? { label: row.label, x, y } : null;
      return Boolean(this.hover || (row !== OVER_WINDOW && this.groundAt(x, y)));
    }

    command(order) {
      this.order = order;
      this.queue = order.steps;
      this.talkKey = order.key || null;
      this.talkParams = order.params || {};
      this.doing = order.doing;
      this.mood = order.mood || "ok";
      this.saying = null;
      this.nextSay = Infinity;
      this.path = [];
      this.a.step = null;
      this.a.prop = null;
      this.pending = null;
      this.startNext();
    }

    // A click on the canvas. Returns what it hit, for the tests.
    click(x, y) {
      const row = this.thingAt(x, y);
      this.hover = null;
      if (row === OVER_WINDOW) return null;
      if (row) { this.command(this.orderFor(row.name)); return row.name; }
      const spot = this.groundAt(x, y);
      if (!spot) return null;
      this.target = { x: A.X(spot[0], spot[1]), y: A.Y(spot[0], spot[1]), until: this.time + 1.6 };
      this.command({ steps: [this.leg(spot[0], spot[1]), act("stand", 12, { face: "SE" })], doing: "garden.do.walk" });
      return "ground";
    }

    orderFor(name) {
      const s = this.state || {}, own = !s.demo, c = L.climate(s), d = this.decision;
      const read = (way, face, key, params) => ({ steps: [way, act("hip", 8, { face, talk: true }), act("stand", 3, { face })], key, params, doing: "garden.do.read" });
      const free = (what, key) => ({ steps: chillSteps(what), key, params: {}, doing: CHILL[what].doing, mood: CHILL[what].mood });
      const lamp = () => {
        const level = num(s.light && s.light.level), plan = (s.light_schedule && s.light_schedule.settings) || {};
        if (level !== null && level > 0) return plan.off_time ? ["garden.act.lampOn", { level, off: plan.off_time }] : ["garden.act.lampPlain", { level }];
        return plan.on_time ? ["garden.act.lampOff", { on: plan.on_time }] : ["garden.act.lampDark", {}];
      };
      switch (name) {
        case "hank": {
          const trouble = d && d.say;
          return { steps: [act("wave", 3.5, { face: "SE", talk: true, mouth: "open" }), act("stand", 5, { face: "SE" })],
            key: trouble ? d.say : "garden.act.hello", params: trouble ? d.params : {}, doing: "garden.do.talk", mood: trouble ? d.mood : "joy" };
        }
        case "plants": {
          const label = (s.autopilot && s.autopilot.boxes && s.autopilot.boxes[0] && s.autopilot.boxes[0].plant) || t("garden.thing.plants");
          return { steps: [go("bed"), act("crouch", 8, { face: "SW", talk: true, mouth: "smile" }), act("stand", 3, { face: "SW" })],
            key: "garden.act.plants", params: { name: String(label), height: L.heightCm(s), soil: L.soilText(s) }, doing: "garden.do.inspect" };
        }
        case "barrel":
          if (L.tankDry(s)) return { steps: [go("barrel"), act("lookin", 7, { face: "SW", eyes: "wide", mouth: "open", talk: true }), act("hip", 3, { face: "SW" })], key: "garden.act.barrelDry", doing: "garden.do.tank", mood: "worry" };
          return { steps: [go("barrel"), act("fill", 3.2, { face: "SW", prop: "can", angle: 0, fill: true, talk: true }), go("bed", "can"),
            act("pour", 7, { face: "SW", prop: "can", angle: 0.62, pour: true, mouth: "smile" }), act("stand", 3, { face: "SW" })], key: "garden.act.barrel", doing: "garden.do.show" };
        case "fridge": {
          const what = own && this.fridgeNext++ % 2 === 0 ? "beer" : "juice";
          return free(what, what === "beer" ? "garden.act.beer" : "garden.act.juice");
        }
        case "chair": return free("nap", "garden.act.nap");
        case "table": return own ? free("cig", "garden.act.cig") : free("nap", "garden.act.nap");
        case "pc": return free("game", "garden.act.game");
        case "lantern": {
          const said = lamp();
          return read(go("hub"), "SE", said[0], said[1]);
        }
        case "sun": {
          const said = lamp();
          return { steps: [act("point", 6, { face: "SE", talk: true, mouth: "open" }), act("stand", 3, { face: "SE" })], key: said[0], params: said[1], doing: "garden.do.read" };
        }
        case "thermo": return read(go("thermo"), "SW", "garden.act.thermo", { temp: c.temp, hum: c.hum, vpd: c.vpd });
        case "fountain": return read(go("fountain"), "NW", L.humidifierOn(s) ? "garden.act.mistOn" : "garden.act.mistOff", {});
        case "windmill": return read(this.leg(1.5, 4.8), "NW", "garden.act.fans", { dfan: num(s.dfan && s.dfan.step), cfan: num(s.cfan && s.cfan.step) });
        case "tree": return read(this.leg(1.55, 1.12), "NW", "garden.act.tree", {});
        case "board": {
          const note = this.boardNote();
          return { steps: [go("boardSpot"), act("point", 9, { face: "SW", talk: true, mouth: "open" }), act("stand", 2, { face: "SW" })],
            key: note ? note[0] : "garden.act.boardEmpty", params: note ? note[1] : {}, doing: "garden.do.todo", mood: "think" };
        }
        default: return { steps: [act("stand", 2, { face: "SE" })], doing: null };
      }
    }

    // ---------- drawing ----------

    worldTo(c) {
      const s = this.state || {};
      const w = this.world, a = this.a;
      c.clearRect(0, 0, this.W, this.H);
      c.save();
      c.translate(this.ox, this.oy);
      c.drawImage(w.island.img, w.island.x, w.island.y);
      const soil = s.soil || {};
      const soilValue = num(soil.relative) !== null ? soil.relative / 100 : num(soil.percent) !== null ? soil.percent / 32 : 0.6;
      const wet = L.pumpOn(s) || soilValue > 0.85 ? 2 : soilValue > 0.45 ? 1 : 0;
      const height = L.heightCm(s);
      const planted = height !== null;
      const plantPx = planted ? Math.min(72, 6 + height * 0.8) : 0;
      const stageKey = (s.grow_plan && s.grow_plan.stage && s.grow_plan.stage.key) || "";
      const flower = /flower|bloom|ripen/.test(stageKey) ? 1 : 0;
      const name = String((s.autopilot && s.autopilot.boxes && s.autopilot.boxes[0] && s.autopilot.boxes[0].plant) || "");
      const wind = num(s.cfan && s.cfan.step) || 0;
      const thirsty = this.decision && this.decision.job === "thirsty";
      const opts = { t: this.time, sway: 0.2 + wind * 0.1, flap: 0.08 + wind * 0.022, droop: thirsty ? 0.9 : 0, flower, purple: /purple/i.test(name) };
      const objs = w.objs;
      const hx = A.X(a.gx, a.gy), hy = A.Y(a.gx, a.gy, a.gx >= 6 && a.gy >= 0.3 && a.gy <= 3.6 ? 2 : 0);  // the deck stands 2 pixels high
      let insert = 0;
      objs.forEach((o, i) => {
        const box = o.plant ? { x: A.X(o.gx, o.gy) - 18, y: A.Y(o.gx, o.gy, 7) - plantPx, w: 36, h: plantPx + 4 } : o;
        const overlap = box.x < hx + 18 && box.x + box.w > hx - 18 && box.y < hy + 4 && box.y + box.h > hy - 44;
        if (overlap && !(a.gx < o.foot[0] || a.gy < o.foot[1])) insert = i + 1;
      });
      objs.forEach((o, i) => {
        if (i === insert) this.drawHank(c, hx, hy);
        this.drawObject(c, o, { wet, plantPx, opts, planted });
      });
      if (insert >= objs.length) this.drawHank(c, hx, hy);
      if (!this.still) this.drawParts(c);
      if (this.target && this.time < this.target.until) this.drawTarget(c, this.target);
      c.restore();
    }

    drawTarget(c, m) {
      const size = 4 + Math.round((m.until - this.time) * 2);
      A.line(c, m.x - size, m.y, m.x, m.y - size / 2, "#fff8ea"); A.line(c, m.x, m.y - size / 2, m.x + size, m.y, "#fff8ea");
      A.line(c, m.x - size, m.y, m.x, m.y + size / 2, "#fff8ea"); A.line(c, m.x, m.y + size / 2, m.x + size, m.y, "#fff8ea");
    }

    drawObject(c, o, v) {
      const w = this.world, s = this.state || {};
      if (o.plant) {
        if (v.planted) A.plant(c, A.X(o.gx, o.gy), A.Y(o.gx, o.gy, 7), v.plantPx * o.plant, { ...v.opts, t: v.opts.t + o.gx });
        return;
      }
      let img = o;
      if (o.name === "bed") img = w.beds[v.wet];
      if (o.name === "fridge" && this.time < this.fridgeUntil) img = w.fridgeOpen;
      if (o.name === "barrel" && L.tankDry(s)) img = w.barrelEmpty;
      c.drawImage(img.img, img.x, img.y);
      if (o.name === "windmill") A.rotor(c, w.at.mill[0], w.at.mill[1] - 3, this.rotor);
      if (o.name === "barrel") {
        const on = L.pumpOn(s);
        A.dot(c, w.at.barrel[0] + 12, w.at.barrel[1] - 7, on ? "#7dff7a" : "#7a2c2c");
        if (on) {
          // water moving through the hose
          const hose = w.hose;
          for (let i = 0; i + 1 < hose.length; i++) {
            const p = A.P3(hose[i][0], hose[i][1]), q = A.P3(hose[i + 1][0], hose[i + 1][1]), len = Math.hypot(q[0] - p[0], q[1] - p[1]);
            for (let u = (this.time * 26) % 6; u < len; u += 6) A.dot(c, p[0] + (q[0] - p[0]) * u / len, p[1] + (q[1] - p[1]) * u / len - 1, "#8fd8ff");
          }
        }
      }
      if (o.name === "thermo") {
        const temp = L.climate(s).temp;
        if (temp !== null) A.mercury(c, w.at.thermo, Math.max(0, Math.min(1, (temp - 10) / 25)));
      }
      if (o.name === "board") A.boardNotes(c, L.openTodos(s).length + L.notices(s).length);
      if (o.name === "desk") {
        const st = this.a.step;
        A.monitor(c, st && st.screen ? "game" : this.d > 0.05 ? "idle" : "idle", this.time);
      }
      if (o.name === "lights") {
        A.BULB_LINE.forEach((p, i) => {
          A.rect(c, p[0], p[1] + 1, 2, 2, A.BULB_COLS[i % A.BULB_COLS.length]);
        });
      }
    }

    drawHank(c, x, y) {
      const a = this.a, st = a.step || {};
      const blink = !st.eyes && (this.time % 4.3) < 0.14;
      const eyes = st.eyes || (blink ? "closed" : "open");
      const pose = a.pose === "walk" ? "walk" : a.pose;
      const carrying = pose === "walk" && a.prop === "can";
      this.fx = A.hank(c, x, y, {
        pose: carrying ? "carry" : pose,
        t: pose === "walk" && !carrying ? a.walk : this.time,
        view: a.view, flip: a.flip, eyes, mouth: st.mouth, hat: st.hat, scarf: st.scarf, jitter: st.jitter, prop: a.prop, propAngle: st.angle,
        ember: st.inhale ? "#ffd27a" : "#e8622a",
      });
      if (st.mark && this.fx) A.text(c, st.mark, this.fx.head[0] - 2, this.fx.head[1] - 9 + Math.round(Math.sin(this.time * 6)), st.mark === "!" ? "#c0392b" : "#fff8ea");
      if (carrying && this.fx && this.fx.tip) { /* the can swings with the walk through the arm's pose */ }
    }

    drawParts(c) {
      for (const p of this.parts) {
        const fade = 1 - p.age / p.life;
        if (p.kind === "drop") { A.rect(c, p.x, p.y, 1, 2, "#8fd8ff"); }
        else if (p.kind === "sweat") { A.rect(c, p.x, p.y, 1, 2, "#c9efff"); }
        else if (p.kind === "smoke") {
          c.globalAlpha = 0.62 * fade;
          const r = Math.round((p.big ? 1.2 : 0.6) + p.age * 1.1);
          A.ell(c, p.x, p.y, r, r, "#e8e8ea");
          c.globalAlpha = 1;
        } else if (p.kind === "mist") {
          c.globalAlpha = 0.3 * fade;
          const r = Math.round(1 + p.age * 0.9);
          A.ell(c, p.x, p.y, r + 1, r, "#f0faff");
          c.globalAlpha = 1;
        } else if (p.kind === "z") {
          c.globalAlpha = Math.min(1, fade * 2);
          A.text(c, "z", p.x + Math.sin(p.age * 2) * 2, p.y, "#fff8ea");
          c.globalAlpha = 1;
        } else if (p.kind === "leaf") {
          A.dot(c, p.x, p.y + Math.sin(p.age * 3) * 2, "#a5da68");
          A.dot(c, p.x + 1, p.y + Math.sin(p.age * 3) * 2, "#79c75f");
        }
      }
    }

    glow(c, x, y, r, col, alpha) {
      c.save();
      c.globalCompositeOperation = "lighter";
      for (let i = 3; i >= 1; i--) {
        c.globalAlpha = alpha * (0.34 + (3 - i) * 0.12);
        A.ell(c, x, y, Math.round(r * i / 3), Math.round(r * 0.72 * i / 3), col);
      }
      c.restore();
    }

    draw() {
      const c = this.c, W = this.W, H = this.H;
      const d = this.d === null ? 0.8 : this.d;
      if (this.skyD === null || Math.abs(this.skyD - d) > 0.008) { this.sky = A.bakeSky(W, H, d); this.skyD = d; }
      c.drawImage(this.sky, 0, 0);
      const night = Math.max(0, Math.min(1, (0.3 - d) / 0.3));
      if (night > 0) {
        c.globalAlpha = night;
        STARS.forEach((st, i) => {
          if (st[2] > 0.92 && Math.sin(this.time * 2 + i) > 0.4) return;
          A.dot(c, st[0] * W, st[1] * H * 0.62, st[2] > 0.7 ? "#fff8d8" : "#b8c4ff");
        });
        c.globalAlpha = 1;
      }
      const skyW = this.tall ? W - 40 : W - 26;
      if (d > 0.12) {
        const level = Math.max(0, Math.min(1, (d - 0.35) / 0.65));
        const low = this.tall ? this.oy + 8 : this.oy + 74, high = 34;
        const sunY = Math.round(low + (high - low) * level);
        c.globalAlpha = Math.min(1, (d - 0.12) / 0.2);
        A.sun(c, skyW, sunY, this.time);
        c.globalAlpha = 1;
        this.sunAt = [skyW, sunY];
      } else this.sunAt = [skyW, 34];
      if (night > 0) { c.globalAlpha = night; c.drawImage(A.MOON, skyW - 13, 21); c.globalAlpha = 1; }
      A.CLOUDS.forEach((cloud, i) => {
        const speed = 1.2 + i * 0.8, span = W + cloud.width + 40;
        const x = ((this.time * speed + i * 137) % span) - cloud.width - 20;
        const y = [14, Math.min(this.H * 0.8, this.oy + 158), 42][i];
        c.globalAlpha = 0.12 + 0.88 * Math.min(1, d * 1.3);
        c.drawImage(cloud, Math.round(x), Math.round(y));
        c.globalAlpha = 1;
      });
      // two far islands, only where the screen has room for them
      c.globalAlpha = 0.25 + 0.5 * Math.min(1, d * 1.4);
      if (this.tall) c.drawImage(A.ISLET, Math.round(W * 0.08), Math.round(this.oy + 186 + Math.sin(this.time * 0.4) * 2));
      c.drawImage(A.ISLET, Math.round(W * (this.tall ? 0.66 : 0.8)), Math.round((this.tall ? this.oy - 60 : H - 104) + Math.sin(this.time * 0.3 + 2) * 2));
      c.globalAlpha = 1;
      const lc = this.layer.getContext("2d");
      this.worldTo(lc);
      if (d < 0.995) {
        const cc = this.copy.getContext("2d");
        cc.clearRect(0, 0, W, H);
        cc.drawImage(this.layer, 0, 0);
        const warm = d >= 0.5 ? this.mix("#ffe9cc", "#ffffff", (d - 0.5) / 0.5) : this.mix("#46528f", "#ffe9cc", d / 0.5);
        lc.save();
        lc.globalCompositeOperation = "multiply";
        lc.fillStyle = warm;
        lc.fillRect(0, 0, W, H);
        lc.globalCompositeOperation = "destination-in";
        lc.drawImage(this.copy, 0, 0);
        lc.restore();
      }
      c.drawImage(this.layer, 0, 0);
      if (d < 0.4) this.lights(c, Math.max(0, Math.min(1, (0.4 - d) / 0.3)));
      if (!this.still) this.flying(c, d);
      this.drawStats(c);
      this.drawDialog(c);
      this.drawBubble(c);
      this.drawHover(c);
    }

    // The name of what the mouse is on, so the owner sees what can be clicked.
    drawHover(c) {
      if (!this.hover) return;
      const label = t(this.hover.label), w = A.textWidth(label) + 8, h = 15;
      const x = Math.max(2, Math.min(this.W - w - 2, Math.round(this.hover.x - w / 2)));
      let y = Math.max(2, Math.round(this.hover.y) - h - 8);
      if (this.bar && x + w > this.bar.left - BAR_GAP && y < this.bar.bottom + BAR_GAP) y = this.bar.bottom + BAR_GAP;
      A.windowBox(c, x, y, w, h);
      A.text(c, label, x + 4, y + 3, "#2b1b18");
    }

    mix(a, b, u) {
      const p = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16)), q = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
      return `rgb(${p.map((v, i) => Math.round(v + (q[i] - v) * u)).join(",")})`;
    }

    lights(c, power) {
      c.save();
      c.translate(this.ox, this.oy);
      const lamp = A.LAMPS.lantern;
      this.glow(c, lamp[0], lamp[1] + 10, 26, "#ff9d3a", 0.16 * power);
      A.BULB_LINE.forEach((p, i) => {
        this.glow(c, p[0], p[1] + 2, 4, A.BULB_COLS[i % A.BULB_COLS.length], 0.3 * power);
      });
      const st = this.a.step;
      const screen = A.P3(7.97, 0.97, 22);
      this.glow(c, screen[0], screen[1], st && st.screen ? 22 : 6, st && st.screen ? "#5aa2ff" : "#3f8a66", (st && st.screen ? 0.22 : 0.14) * power);
      if (this.time < this.fridgeUntil) { const door = A.P3(6.6, 1.25, 12); this.glow(c, door[0], door[1], 16, "#fff5c0", 0.25 * power); }
      if (this.fx && this.fx.tip && this.a.prop === "cig") this.glow(c, this.fx.tip[0], this.fx.tip[1] + 1, 3, "#ff7a2a", 0.45 * power);
      c.restore();
    }

    flying(c, d) {
      c.save();
      c.translate(this.ox, this.oy);
      this.flies.forEach((f, i) => {
        const x = f.x + Math.sin(this.time * 0.7 + f.p) * 16, y = f.y + Math.cos(this.time * 0.9 + f.p * 2) * 9;
        if (d < 0.25) {
          if (Math.sin(this.time * 2.6 + f.p * 3) > 0) { this.glow(c, x, y, 2, "#d9ff6a", 0.4); A.dot(c, x, y, "#f4ffb0"); }
        } else if (d > 0.45 && i < 3) {
          const flap = Math.sin(this.time * 14 + i) > 0;
          const col = ["#ffd54f", "#f48fb1", "#fff8ea"][i];
          A.dot(c, x, y, "#2b1b18");
          A.dot(c, x - 1, y - (flap ? 1 : 0), col);
          A.dot(c, x + 1, y - (flap ? 1 : 0), col);
        }
      });
      c.restore();
    }

    drawBubble(c) {
      if (!this.saying || !this.fx) return null;
      const lines = this.saying.lines, head = this.fx.head, tipX = this.ox + head[0];
      const room = this.bubbleRoom(lines, this.oy + head[1] - 4);
      return A.bubble(c, tipX, room.top, lines, room.left, room.right, tipX);
    }

    // Above his head and on the screen, not across the status window, and never under the two buttons: they lie on top of the canvas.
    bubbleRoom(lines, tipY) {
      const bar = this.bar, height = lines.length * 10 + 12, width = Math.max(...lines.map(A.textWidth)) + 10;  // the box and its tail
      const room = (top, nearBar) => ({ top, left: !this.tall && top - height < this.stats.y + this.stats.h ? this.stats.x + this.stats.w + 2 : 3, right: nearBar ? bar.left - BAR_GAP : this.W - 3 });
      const top = Math.max(tipY, height + 4);
      if (!bar) return room(top, false);
      // his head bobs: the bubble does not jump to and fro when it runs just along the bottom of the buttons
      const clear = top - height - bar.bottom - BAR_GAP;
      if (clear < 0) this.besideBar = true;
      else if (clear > 3) this.besideBar = false;
      const high = room(top, this.besideBar);
      return high.right - high.left >= width ? high : room(Math.max(top, bar.bottom + BAR_GAP + height), false);
    }

    statRows() {
      const s = this.state || {};
      const c = L.climate(s);
      const level = (metric) => { const st = L.status(s, metric); return st === "high" ? "#b8332a" : st === "low" ? "#b86e00" : st === "ok" ? "#2c7a3f" : "#3a2a1d"; };
      const light = num(s.light && s.light.level);
      const soil = L.soilText(s);
      const height = L.heightCm(s);
      const dry = L.tankDry(s);
      const tank = (s.climate || {}).water_state || (num((s.climate || {}).water_level) !== null ? "level" : null);
      return [
        ["light", light === null ? "-" : `${fmt(light, 0)} %`, "#3a2a1d"],
        ["temp", c.temp === null ? "-" : `${fmt(c.temp, 1)} °C`, level("probe_temp")],
        ["hum", c.hum === null ? "-" : `${fmt(c.hum, 0)} %`, level("probe_humidity")],
        ["vpd", c.vpd === null ? "-" : `${fmt(c.vpd, 2)} kPa`, level("probe_vpd")],
        ["soil", soil === null ? "-" : `${fmt(soil, 0)} %`, (s.soil || {}).state === "low" ? "#b86e00" : "#3a2a1d"],
        ["tank", tank === null ? "-" : t(dry ? "garden.hud.tankDry" : "garden.hud.tankWet"), dry ? "#b8332a" : "#2c7a3f"],
        ["height", height === null ? "-" : `${fmt(height, 0)} cm`, "#3a2a1d"],
      ];
    }

    drawStats(c) {
      const box = this.stats, s = this.state || {};
      A.windowBox(c, box.x, box.y, box.w, box.h);
      const name = String((s.autopilot && s.autopilot.boxes && s.autopilot.boxes[0] && s.autopilot.boxes[0].plant) || t("garden.open"));
      const clock = when(new Date());
      const rows = this.statRows();
      if (box.cols === 1) {
        A.text(c, name.length > 13 ? `${name.slice(0, 12)}.` : name, box.x + 4, box.y + 4, "#7c4d2d");
        rows.forEach((row, i) => {
          const y = box.y + 15 + i * 10.5;
          c.drawImage(A.ICONS[row[0] === "hum" ? "hum" : row[0]], box.x + 4, Math.round(y));
          A.text(c, row[1], box.x + 14, Math.round(y), row[2]);
        });
      } else {
        A.text(c, name, box.x + 4, box.y + 4, "#7c4d2d");
        A.text(c, clock, box.x + box.w - 5 - A.textWidth(clock), box.y + 4, "#7c4d2d");
        const half = Math.floor((box.w - 8) / 2);
        rows.forEach((row, i) => {
          const col = i % 2, line = Math.floor(i / 2);
          const x = box.x + 4 + col * half, y = box.y + 14 + line * 10;
          c.drawImage(A.ICONS[row[0]], x, Math.round(y));
          A.text(c, row[1], x + 10, Math.round(y), row[2]);
        });
      }
    }

    detail() {
      const s = this.state || {}, h = this.hank || {}, d = this.decision;
      if (d && d.job !== "chill" && !(d.soft && this.mode === "break")) {
        const thirst = s.thirst || {};
        if (d.job === "water") return (thirst.pump && thirst.pump.flow_text) || "";
        if (d.job === "thirsty" || d.job === "soggy" || d.job === "poke") return (thirst.decision && thirst.decision.text) || "";
        if (d.job === "tank" || d.job === "lost") return "";
        const auto = this.autoLine(d.job);
        if (auto !== null) return auto;
        // while an alarm is active his traffic light names only the alarm: a camera says nothing about what he does here
        const fix = ((h.light && h.light.fix) || []).map(String);
        const onNote = fix.some((key) => key.startsWith("alert:") && L.noteAlarm(key.slice(6)));
        if (h.light && h.light.level !== "green" && !onNote && fix.some((key) => !key.startsWith("light_percent"))) return h.light.text;
        const row = ((s.plant && s.plant.checks) || []).find((r) => (r.status === "high" || r.status === "low") && r.metric !== "light_percent");
        return (row && row.advice) || "";
      }
      if (this.talkKey === "garden.say.todo") return "";
      return (s.grow_plan && s.grow_plan.light && s.grow_plan.light.text) || "";
    }

    // What the autopilot does about the air while it regulates the exhaust itself, never the card's "turn the exhaust fan
    // up" (owner, 07.11.2026: "it's on auto, he should do everything himself, not telling me to do it"): its exhaust step
    // of the last half hour, else what the exhaust waits for, worded as Hank's fix line words it. Against dry air the
    // running humidifier comes first. Null when the exhaust is not the autopilot's: then the advice is the owner's.
    autoLine(job) {
      const s = this.state || {}, fan = L.regulated(s, "dfan");
      if (!fan || !["hot", "cold", "dry", "humid"].includes(job)) return null;
      const step = (s.autopilot.boxes[0].log || []).find((e) => e && e.kind === "step" && e.device === "dfan");
      const fresh = step && num(step.at) !== null && Date.now() / 1000 - step.at < STEP_SHOWN_S;
      const status = String(fan.status || "").replace(/\.$/, "");
      const line = fresh ? String(step.text || "") : status ? t("garden.auto.device", { device: fan.name, status }) : "";
      return job === "dry" && L.humidifierOn(s) ? [t("garden.auto.humidifier"), line].filter(Boolean).join(" ") : line;
    }

    drawDialog(c) {
      const box = this.dialog;
      A.windowBox(c, box.x, box.y, box.w, box.h);
      const size = A.PORTRAIT_SIZE, px = box.x + 3, py = box.y + Math.floor((box.h - size) / 2);
      const st = this.a.step || {};
      const mood = st.zzz ? "sleep" : this.mood === "joy" && st.eyes === "closed" ? "joy" : this.mood;
      A.rect(c, px - 1, py - 1, size + 2, size + 2, A.INK);
      c.drawImage(A.portrait(mood === "think" ? "ok" : mood), px, py);
      const tx = px + size + 5, width = box.x + box.w - tx - 4;
      const doing = this.doing ? t(this.doing) : "";
      A.text(c, "Hank", tx, box.y + 4, "#7c4d2d");
      if (doing) A.text(c, doing, tx + A.textWidth("Hank") + 7, box.y + 4, "#2b1b18");
      // Rows beside the hint; a line that needs one more takes the hint's place instead of losing its end (07.11.2026:
      // the autopilot's status runs to three rows in the wide window, which has two)
      const room = box.h > 54 ? 3 : 2, rows = A.wrap(this.detail(), width);
      const lines = rows.slice(0, rows.length > room ? room + 1 : room);
      lines.forEach((row, i) => A.text(c, row, tx, box.y + 15 + i * 10, "#5a4632"));
      const hint = t("garden.back");
      if (lines.length <= room) A.text(c, hint, box.x + box.w - 5 - A.textWidth(hint), box.y + box.h - 12, "#a28c6a");
      if (!this.tall) {
        const clock = when(new Date());
        A.text(c, clock, box.x + box.w - 5 - A.textWidth(clock), box.y + 4, "#7c4d2d");
      }
    }
  }

  // ---------- on top of the page ----------

  let latest = null, hankState = null, root = null, canvas = null, live = null, scene = null, bar = null, pinButton = null, exitButton = null;
  let isOpen = false, frame = 0, lastFrame = 0, lastInput = Date.now(), hankTimer = 0, opener = null, pixel = 1, pinChecked = false;
  const reduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

  function fit() {
    const dpr = window.devicePixelRatio || 1;
    const devW = Math.round(window.innerWidth * dpr), devH = Math.round(window.innerHeight * dpr);
    const tall = devH > devW * 1.05;
    const needW = tall ? 292 : 340, needH = tall ? 380 : 216;
    const k = Math.max(1, Math.floor(Math.min(devW / needW, devH / needH)));
    const w = Math.ceil(devW / k), h = Math.ceil(devH / k);
    if (scene) scene.resize(w, h);
    canvas.style.width = `${(w * k) / dpr}px`;
    canvas.style.height = `${(h * k) / dpr}px`;
    pixel = Math.max(1, Math.ceil(k * 0.6)) / dpr;
    labelButtons();
  }

  // Remembered on this device only: with the pin on, the page opens in the garden, also after it reloaded for a new version.
  function pinned() { try { return localStorage.getItem(PIN_KEY) === "1"; } catch (_) { return false; } }
  function setPinned(on) { try { if (on) localStorage.setItem(PIN_KEY, "1"); else localStorage.removeItem(PIN_KEY); } catch (_) { /* then it holds until the page goes */ } }

  function labelButton(button, key) {
    const label = t(key), cv = offscreen(A.textWidth(label), 9);
    A.text(cv.getContext("2d"), label, 0, 0, "#2b1b18");
    cv.style.width = `${cv.width * pixel}px`;
    cv.style.height = `${cv.height * pixel}px`;
    button.setAttribute("aria-label", label);
    button.replaceChildren(cv);
  }

  function labelButtons() {
    const on = pinned();
    labelButton(pinButton, on ? "garden.pinned" : "garden.pin");
    pinButton.setAttribute("aria-pressed", on ? "true" : "false");
    labelButton(exitButton, "garden.exit");
    measureBar();
  }

  // Where the two buttons lie, in the scene's pixels and with their outline, so that nothing he says ends up under them.
  function measureBar() {
    if (!scene) return;
    const box = bar.getBoundingClientRect(), view = canvas.getBoundingClientRect();
    scene.bar = { left: Math.floor((box.left - BAR_OUTLINE - view.left) * scene.W / view.width), bottom: Math.ceil((box.bottom + BAR_OUTLINE - view.top) * scene.H / view.height) };
  }

  function toScene(event) {
    const box = canvas.getBoundingClientRect();
    return [(event.clientX - box.left) * scene.W / box.width, (event.clientY - box.top) * scene.H / box.height];
  }

  function build() {
    root = document.createElement("div");
    root.className = "garden";
    root.hidden = true;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", t("garden.open"));
    canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    live = document.createElement("p");
    live.className = "garden-sr";
    live.setAttribute("aria-live", "polite");
    bar = document.createElement("div");
    bar.className = "garden-bar";
    pinButton = document.createElement("button");
    pinButton.type = "button";
    pinButton.addEventListener("click", () => { setPinned(!pinned()); labelButtons(); });
    exitButton = document.createElement("button");
    exitButton.type = "button";
    exitButton.addEventListener("click", () => close());
    bar.append(pinButton, exitButton);
    root.append(canvas, live, bar);
    canvas.addEventListener("click", (event) => { if (scene) scene.click(...toScene(event)); });
    canvas.addEventListener("pointermove", (event) => {
      if (!scene || event.pointerType === "touch") return;
      canvas.style.cursor = scene.hoverAt(...toScene(event)) ? "pointer" : "default";
    });
    canvas.addEventListener("pointerleave", () => { if (scene) scene.hover = null; });
    document.body.append(root);
    window.addEventListener("resize", () => { if (isOpen) fit(); });
  }

  function tick(now) {
    if (!isOpen) return;
    frame = requestAnimationFrame(tick);
    const fps = reduced.matches ? 1 : FPS;
    if (now - lastFrame < 1000 / fps - 2) return;
    const dt = Math.min(0.25, (now - lastFrame) / 1000);
    lastFrame = now;
    try {
      scene.step(reduced.matches ? 1 : dt);
      scene.draw();
      const line = [scene.doing ? t(scene.doing) : "", scene.saying ? scene.saying.text : ""].filter(Boolean).join(". ");
      if (live.textContent !== line) live.textContent = line;
    } catch (err) { console.error(err); close(); }
  }

  async function loadHank() {
    try {
      hankState = await api("/api/hank/state");
      if (scene && latest) scene.feed(latest, hankState);
    } catch (_) { /* his sentence is a bonus, the garden lives on /api/state */ }
  }

  function open() {
    if (isOpen || !latest) return;
    if (!root) build();
    root.setAttribute("aria-label", t("garden.open"));
    root.hidden = false;
    isOpen = true;
    opener = document.activeElement;
    if (opener && opener.blur) opener.blur();
    if (!scene) scene = new Scene(canvas, { still: reduced.matches });
    fit();
    scene.feed(latest, hankState);
    loadHank();
    hankTimer = setInterval(loadHank, HANK_POLL_MS);
    lastFrame = performance.now();
    frame = requestAnimationFrame(tick);
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    root.hidden = true;
    cancelAnimationFrame(frame);
    clearInterval(hankTimer);
    lastInput = Date.now();
    const back = opener;
    opener = null;
    if (back && back.focus) back.focus({ preventScroll: true });
  }

  // Someone is typing, or watching in fullscreen, or reading Hank's answer: the garden does not jump in.
  function inTheWay() {
    if (document.hidden || document.fullscreenElement) return true;
    const el = document.activeElement;
    if (el && (el.isContentEditable || ((el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.value))) return true;
    const drawer = document.getElementById("hk-drawer"), wiki = document.getElementById("wiki-view");
    return Boolean((drawer && !drawer.hidden) || (wiki && !wiki.hidden) || document.querySelector("dialog[open]"));
  }

  function input() { lastInput = Date.now(); }

  for (const name of ["pointerdown", "wheel", "touchstart", "scroll"]) document.addEventListener(name, input, { capture: true, passive: true });
  // Open, the garden has the keys: the page below sees none of them. Only Esc goes back, and Tab stays on its two buttons.
  document.addEventListener("keydown", (event) => {
    input();
    if (!isOpen) return;
    event.stopPropagation();
    if (event.key === "Escape") { event.preventDefault(); close(); }
    if (event.key === "Tab") {
      event.preventDefault();
      (document.activeElement === pinButton ? exitButton : pinButton).focus();
    }
  }, { capture: true });
  // The mouse counts as use of the page, and never closes the garden: the owner plays in it.
  document.addEventListener("pointermove", (event) => { if (event.pointerType !== "touch") input(); }, { capture: true, passive: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) input(); });

  document.addEventListener("vgrow:state", (event) => {
    latest = event.detail;
    if (isOpen && scene) scene.feed(latest);
    if (!pinChecked) { pinChecked = true; if (pinned()) open(); }
  });

  setInterval(() => {
    if (!isOpen && latest && Date.now() - lastInput >= IDLE_MS && !inTheWay()) open();
  }, 1000);

  document.addEventListener("click", (event) => {
    if (event.target && event.target.closest && event.target.closest("[data-garden-open]")) { event.stopPropagation(); open(); }
  });

  for (const button of document.querySelectorAll("[data-garden-open]")) button.hidden = false;

  window.HankGarden = { open, close, isOpen: () => isOpen, Scene, THINGS };
})();
