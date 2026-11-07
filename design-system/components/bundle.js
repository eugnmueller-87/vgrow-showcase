/* @ds-bundle: {"format":4,"namespace":"VGrowCockpit","components":[{"name":"Button"},{"name":"Toggle"},{"name":"Input"},{"name":"Range"},{"name":"Stepper"},{"name":"Chips"},{"name":"SegmentedControl"},{"name":"StatusChip"},{"name":"Banner"},{"name":"Statusline"},{"name":"Disclosure"},{"name":"Card"},{"name":"DeviceCard"},{"name":"EmptyState"},{"name":"Timeline"},{"name":"Tile"},{"name":"Gauge"},{"name":"StageTrack"},{"name":"Ladder"},{"name":"RulesList"},{"name":"PortsList"},{"name":"DataTable"},{"name":"Camera"},{"name":"SidebarNav"},{"name":"Topbar"},{"name":"PageTabs"},{"name":"HankDrawer"}]} */
/* VGrow Cockpit — vanilla element factories. No framework, no network: each function returns a DOM element
   styled by bundle.css and tokens.css. The markup it builds is the markup the component READMEs document. */
(function () {
  'use strict';
  var SVG = 'http://www.w3.org/2000/svg';
  var ICON_PATHS = {
    info: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M12 11v5M12 8h.01'],
    warn: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M12 8v5M12 16h.01'],
    bad: ['M12 3 2 20h20L12 3Z', 'M12 10v4M12 17h.01'],
    ok: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'm8 12 3 3 5-6'],
    chevron: ['m9 6 6 6-6 6']
  };
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name, width) {
    var s = document.createElementNS(SVG, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
    s.setAttribute('stroke-width', String(width || 1.8)); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('aria-hidden', 'true');
    (ICON_PATHS[name] || []).forEach(function (d) { var p = document.createElementNS(SVG, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  function append(parent, children) {
    [].concat(children || []).forEach(function (c) { if (c == null) return; parent.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return parent;
  }

  /* Button({label, variant: 'primary'|'soft'|'arm'|undefined, small, disabled, locked, onClick}) */
  function Button(o) {
    var b = el('button', 'btn' + (o.variant ? ' ' + o.variant : '') + (o.small ? ' sm' : '') + (o.locked ? ' is-locked' : ''), o.label);
    b.type = o.type || 'button';
    if (o.disabled) b.disabled = true;
    if (o.locked) b.setAttribute('aria-disabled', 'true');
    if (o.onClick && !o.locked) b.addEventListener('click', o.onClick);
    return b;
  }

  /* Input({label, hint, invalid, ...attrs}) → <label class="field"> */
  function Input(o) {
    var f = el('label', 'field' + (o.invalid ? ' invalid' : ''));
    append(f, el('span', 'label', o.label));
    var i = el('input', 'input');
    Object.keys(o).forEach(function (k) { if (['label', 'hint', 'invalid'].indexOf(k) < 0 && o[k] != null) i.setAttribute(k, String(o[k])); });
    if (o.invalid) i.setAttribute('aria-invalid', 'true');
    append(f, i);
    if (o.hint) append(f, el('span', 'hint', o.hint));
    return f;
  }

  /* StatusChip({state: 'ok'|'warn'|'info'|'bad'|'demo'|undefined, text, onSide}) */
  function StatusChip(o) {
    return el('span', 'status' + (o.state ? ' ' + o.state : '') + (o.onSide ? ' on-side' : ''), o.text);
  }

  /* Banner({variant: 'warn'|'bad'|'info'|'ok', lead, text, action: ButtonOptions}) */
  function Banner(o) {
    var v = o.variant || 'warn';
    var b = el('div', 'banner' + (v === 'warn' ? '' : ' ' + v));
    b.setAttribute('role', v === 'bad' ? 'alert' : 'status');
    append(b, icon(v));
    var body = el('div', 'banner-body');
    var p = el('p');
    if (o.lead) append(p, [el('b', null, o.lead + ':'), ' ']);
    append(p, o.text);
    append(body, p);
    if (o.action) append(body, Button(Object.assign({ small: true }, o.action)));
    return append(b, body);
  }

  /* SegmentedControl({label, options: [{value, label, arm}], value, block, quiet, locked, onChange}) */
  function SegmentedControl(o) {
    var g = el('div', 'seg' + (o.block ? ' block' : '') + (o.quiet ? ' quiet' : '') + (o.locked ? ' is-locked' : ''));
    g.setAttribute('role', 'group'); if (o.label) g.setAttribute('aria-label', o.label);
    o.options.forEach(function (opt) {
      var b = el('button', opt.arm ? 'arm' : '', opt.label); b.type = 'button';
      b.setAttribute('aria-pressed', String(opt.value === o.value));
      b.addEventListener('click', function () {
        if (o.locked) return;
        g.querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', 'false'); });
        b.setAttribute('aria-pressed', 'true');
        if (o.onChange) o.onChange(opt.value);
      });
      g.appendChild(b);
    });
    return g;
  }

  /* Disclosure({title, description, chip: StatusChipOptions, open, body: Node|string}) */
  function Disclosure(o) {
    var d = el('details', 'disclosure'); if (o.open) d.open = true;
    var s = el('summary');
    var chev = icon('chevron'); chev.setAttribute('class', 'chev');
    var t = append(el('span', 't'), [el('b', null, o.title), o.description ? el('span', null, o.description) : null]);
    append(s, [chev, t, o.chip ? StatusChip(o.chip) : null]);
    append(d, [s, append(el('div', 'disclosure-body'), o.body)]);
    return d;
  }

  /* Card({title, hero, link: {label, href}, body}) */
  function Card(o) {
    var c = el('section', 'card');
    var h = el('div', 'card-h');
    append(h, el('h3', o.hero ? 'hero' : '', o.title));
    if (o.link) { var a = el('a', 'text-link', o.link.label); a.href = o.link.href; h.appendChild(a); }
    return append(c, [h, o.body]);
  }

  /* DeviceCard({name, state, stateText, reading, reason, rule, action: ButtonOptions, compact, attention}) */
  function DeviceCard(o) {
    var attention = o.attention != null ? o.attention : o.state === 'warn';
    if (o.compact) {
      var r = el('article', 'device compact');
      return append(r, [el('b', null, o.name), StatusChip({ state: o.state, text: o.stateText }), el('span', 'reading', o.reading == null ? '–' : String(o.reading))]);
    }
    var c = el('article', 'device' + (attention ? ' attention' : '') + (o.state === 'bad' ? ' stopped' : ''));
    var h = append(el('div', 'device-h'), [el('b', null, o.name), StatusChip({ state: o.state, text: o.stateText }), el('span', 'reading', o.reading == null ? '–' : String(o.reading))]);
    append(c, [h, o.reason ? el('p', null, o.reason) : null, o.rule ? el('p', 'rule', o.rule) : null, o.action ? Button(o.action) : null]);
    return c;
  }

  /* Timeline({entries: [{kind: 'step'|'suggest'|'manual'|'hold'|'failed'|undefined, time, text, count}]}) */
  function Timeline(o) {
    var ol = el('ol', 'timeline');
    o.entries.forEach(function (e) {
      var li = el('li', e.kind || '');
      append(li, [el('time', null, e.time), e.count ? append(el('span', 'grp'), [e.text + ' ', StatusChip({ text: e.count + '×' })]) : el('span', null, e.text)]);
      ol.appendChild(li);
    });
    return ol;
  }

  /* SidebarNav({brand: {name, sub, markSrc}, groups: [{caption, items: [{label, href, active, iconSvg, plain}]}], foot: Node[]}) */
  function SidebarNav(o) {
    var n = el('nav', 'side'); n.setAttribute('aria-label', 'Main');
    var brand = el('a', 'brand'); brand.href = o.brand.href || '#';
    if (o.brand.markSrc) { var img = el('img'); img.src = o.brand.markSrc; img.alt = ''; brand.appendChild(img); }
    append(brand, append(el('span'), [el('b', null, o.brand.name), o.brand.sub ? el('small', null, o.brand.sub) : null]));
    n.appendChild(brand);
    o.groups.forEach(function (g) {
      append(n, el('div', 'nav-caption', g.caption));
      var ul = el('ul', 'side-nav');
      g.items.forEach(function (it) {
        var a = el('a', (it.active ? 'active' : '') + (it.plain ? ' plain' : '')); a.href = it.href || '#';
        if (it.active) a.setAttribute('aria-current', 'page');
        if (it.iconSvg) { var span = el('span'); span.innerHTML = it.iconSvg; a.appendChild(span.firstElementChild); }
        append(a, it.label); ul.appendChild(el('li')).appendChild(a);
      });
      n.appendChild(ul);
    });
    if (o.foot) append(n, append(el('div', 'side-foot'), o.foot));
    return n;
  }

  /* Topbar({title, subtitle, health: {state, text, age}, tools: Node[]}) */
  function Topbar(o) {
    var h = el('header', 'topbar');
    append(h, append(el('div', 'title'), [el('h1', null, o.title), o.subtitle ? el('small', null, o.subtitle) : null]));
    if (o.health) append(h, append(el('div', 'health'), [StatusChip({ state: o.health.state, text: o.health.text }), o.health.age ? el('span', 'age', o.health.age) : null]));
    if (o.tools) append(h, append(el('div', 'tools'), o.tools));
    return h;
  }

  /* Tile({key, source, value, unit, state, stateText, series: 'temp'|'hum'|'vpd'|'water', points: number[], stale, alarm}) */
  function Tile(o) {
    var t = el('div', 'tile' + (o.stale ? ' stale' : '') + (o.alarm ? ' alarm' : ''));
    append(t, [el('span', 'k', o.key), o.source ? el('span', 'src', o.source) : null,
      append(el('span', 'val'), [el('span', 'v', String(o.value)), o.unit ? el('span', 'u', o.unit) : null]),
      o.stateText ? append(el('span', 'meta'), StatusChip({ state: o.state, text: o.stateText })) : null]);
    if (o.points && o.points.length > 1) {
      var s = document.createElementNS(SVG, 'svg'); s.setAttribute('class', 'spark ' + (o.series || '')); s.setAttribute('viewBox', '0 0 160 22'); s.setAttribute('preserveAspectRatio', 'none');
      var min = Math.min.apply(null, o.points), max = Math.max.apply(null, o.points), span = max - min || 1;
      var d = o.points.map(function (v, i) { return (i ? 'L' : 'M') + (i * 160 / (o.points.length - 1)).toFixed(1) + ' ' + (20 - (v - min) / span * 18).toFixed(1); }).join(' ');
      var p = document.createElementNS(SVG, 'path'); p.setAttribute('d', d); s.appendChild(p); t.appendChild(s);
    }
    return t;
  }


  /* Toggle({label, pressed, arm, locked, disabled, onChange}) */
  function Toggle(o) {
    var b = el('button', 'toggle' + (o.arm ? ' arm' : '') + (o.locked ? ' is-locked' : ''), o.label); b.type = 'button';
    b.setAttribute('aria-pressed', String(!!o.pressed));
    if (o.disabled) b.disabled = true;
    if (o.locked) b.setAttribute('aria-disabled', 'true');
    b.addEventListener('click', function () {
      if (o.locked || o.disabled) return;
      var next = b.getAttribute('aria-pressed') !== 'true';
      b.setAttribute('aria-pressed', String(next));
      if (o.onChange) o.onChange(next);
    });
    return b;
  }

  /* Range({id, label, min, max, step, value, format, locked, onInput}) */
  function Range(o) {
    var w = el('div', 'range'), fmt = o.format || function (v) { return String(v); };
    var l = el('label', null, o.label); l.htmlFor = o.id;
    var i = el('input', o.locked ? 'is-locked' : ''); i.type = 'range'; i.id = o.id; i.min = o.min; i.max = o.max; i.step = o.step || 1; i.value = o.value;
    if (o.locked) i.setAttribute('aria-disabled', 'true');
    var out = el('output', null, fmt(o.value)); out.htmlFor = o.id;
    i.addEventListener('input', function () { if (o.locked) { i.value = o.value; return; } out.textContent = fmt(i.value); if (o.onInput) o.onInput(Number(i.value)); });
    return append(w, [l, i, out]);
  }

  /* Stepper({label, level, max, onChange}) — ten bars */
  function Stepper(o) {
    var max = o.max || 10, g = el('div', 'stepper'); g.setAttribute('role', 'group'); if (o.label) g.setAttribute('aria-label', o.label);
    var bars = el('div', 'bars'), lvl = el('span', 'level'), minus = el('button', 'st', '−'), plus = el('button', 'st', '+');
    minus.type = plus.type = 'button'; minus.setAttribute('aria-label', 'Lower'); plus.setAttribute('aria-label', 'Raise'); bars.setAttribute('aria-hidden', 'true');
    function draw(level) {
      bars.textContent = ''; lvl.textContent = String(level);
      for (var k = 1; k <= 10; k++) { var i = el('i', k <= Math.round(level / max * 10) ? 'on' : ''); i.style.height = (30 + k * 7) + '%'; bars.appendChild(i); }
      minus.disabled = level <= 0; plus.disabled = level >= max;
    }
    var level = o.level || 0; draw(level);
    minus.addEventListener('click', function () { level = Math.max(0, level - 1); draw(level); if (o.onChange) o.onChange(level); });
    plus.addEventListener('click', function () { level = Math.min(max, level + 1); draw(level); if (o.onChange) o.onChange(level); });
    return append(g, [minus, bars, plus, lvl]);
  }

  /* Chips({label, options: [{value, label, locked}], chosen: string[], onChange}) */
  function Chips(o) {
    var w = el('div'), g = el('div', 'chips'); g.setAttribute('role', 'group'); if (o.label) { g.setAttribute('aria-label', o.label); append(w, el('span', 'label', o.label)).firstChild.style.cssText = 'display:block;font:600 13px/18px var(--font-sans);margin-bottom:8px'; }
    var chosen = (o.chosen || []).slice();
    o.options.forEach(function (opt) {
      var b = el('button', opt.locked ? 'is-locked' : '', opt.label); b.type = 'button'; b.setAttribute('aria-pressed', String(chosen.indexOf(opt.value) >= 0));
      if (opt.locked) b.setAttribute('aria-disabled', 'true');
      b.addEventListener('click', function () {
        if (opt.locked) return;
        var i = chosen.indexOf(opt.value); if (i >= 0) chosen.splice(i, 1); else chosen.push(opt.value);
        b.setAttribute('aria-pressed', String(i < 0)); if (o.onChange) o.onChange(chosen.slice());
      });
      g.appendChild(b);
    });
    return append(w, g);
  }

  /* Ladder({levels: [{title, text}], current: 0|1|2}) */
  function Ladder(o) {
    var ol = el('ol', 'ladder');
    o.levels.forEach(function (lv, i) {
      var li = el('li', i === o.current ? 'on' : ''); li.setAttribute('data-lvl', String(i));
      append(li, [append(el('span', 'lvl'), [el('i'), el('i'), el('i')]), el('b', null, lv.title), el('small', null, lv.text)]);
      ol.appendChild(li);
    });
    return ol;
  }

  /* RulesList({rules: [{text, detail, need, pressed, onChange}]}) */
  function RulesList(o) {
    var ol = el('ol', 'rules');
    o.rules.forEach(function (r) {
      var li = el('li'), what = append(el('span', 'what'), [r.text, r.detail ? el('small', r.need ? 'need' : '', r.detail) : null]);
      append(li, [what, Toggle({ label: r.pressed ? 'On' : 'Off', pressed: r.pressed, onChange: function (next) { li.querySelector('.toggle').textContent = next ? 'On' : 'Off'; if (r.onChange) r.onChange(next); } })]);
      ol.appendChild(li);
    });
    return ol;
  }

  /* PortsList({ports: [{name, detail, control: Node}]}) */
  function PortsList(o) {
    var w = el('div', 'ports');
    o.ports.forEach(function (p) { w.appendChild(append(el('div', 'port'), [append(el('span', 'name'), [p.name, p.detail ? el('small', null, p.detail) : null]), p.control])); });
    return w;
  }

  /* Gauge({label, value, unit, min, max, band: [lo, hi] | tick: number, state: 'high'|'low'|'none'|undefined, targetText}) */
  function Gauge(o) {
    var pct = function (v) { return Math.max(0, Math.min(100, (v - o.min) / (o.max - o.min) * 100)); };
    var g = el('div', 'gauge');
    append(g, append(el('div', 'g-head'), [el('span', 'lbl', o.label), append(el('span', 'val', o.value == null ? '–' : String(o.value)), o.unit ? el('small', null, o.unit) : null)]));
    var bar = el('div', 'g-bar');
    if (o.band) { var band = el('span', 'g-band'); band.style.left = pct(o.band[0]) + '%'; band.style.width = (pct(o.band[1]) - pct(o.band[0])) + '%'; bar.appendChild(band); }
    if (o.tick != null) { var tick = el('span', 'g-tick'); tick.style.left = pct(o.tick) + '%'; bar.appendChild(tick); }
    var mark = el('span', 'g-mark' + (o.value == null ? ' none' : o.state ? ' ' + o.state : '')); mark.style.left = pct(o.value == null ? o.min : o.value) + '%'; bar.appendChild(mark);
    append(g, [bar, append(el('div', 'g-foot'), [el('span', null, String(o.min)), el('span', null, o.targetText || ''), el('span', null, String(o.max))])]);
    return g;
  }

  /* EmptyState({title, text, action: ButtonOptions}) */
  function EmptyState(o) {
    var d = el('div', 'empty-state'); d.setAttribute('role', 'status');
    return append(d, [el('b', null, o.title), el('p', null, o.text), o.action ? Button(Object.assign({ variant: 'primary' }, o.action)) : null]);
  }

  /* PageTabs({label, tabs: [{label, href, active}]}) */
  function PageTabs(o) {
    var n = el('nav', 'page-tabs'); if (o.label) n.setAttribute('aria-label', o.label);
    o.tabs.forEach(function (t) { var a = el('a', t.active ? 'active' : '', t.label); a.href = t.href || '#'; if (t.active) a.setAttribute('aria-current', 'page'); n.appendChild(a); });
    return n;
  }

  /* Statusline({text, state, stateText, href, artSvg}) */
  function Statusline(o) {
    var a = el('a', 'statusline'); a.href = o.href || '#';
    if (o.artSvg) { var art = el('span', 'sl-art'); art.innerHTML = o.artSvg; a.appendChild(art); }
    append(a, [el('span', 'sl-text', o.text), o.stateText ? StatusChip({ state: o.state, text: o.stateText }) : null]);
    var chev = icon('chevron', 2); chev.setAttribute('class', 'sl-go'); a.appendChild(chev);
    return a;
  }

  window.VGrowCockpit = {
    version: 1,
    icon: icon,
    Button: Button, Input: Input, StatusChip: StatusChip, Banner: Banner, SegmentedControl: SegmentedControl,
    Disclosure: Disclosure, Card: Card, DeviceCard: DeviceCard, Timeline: Timeline, SidebarNav: SidebarNav, Topbar: Topbar, Tile: Tile,
    Toggle: Toggle, Range: Range, Stepper: Stepper, Chips: Chips, Ladder: Ladder, RulesList: RulesList, PortsList: PortsList, Gauge: Gauge,
    EmptyState: EmptyState, PageTabs: PageTabs, Statusline: Statusline
  };
})();
