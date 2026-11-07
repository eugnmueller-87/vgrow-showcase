// Hank's scenes: the mascot as a small animated stage (rig, plant, lamp, props) in Hank's drawer head and in the
// traffic-light line. Ported from the checked prototype hank-szenen.html (22.10.2026), timings unchanged. The man was redrawn on 06.11.2026
// (owner: the same Hank as in his garden, web/garden-art.js), every joint where it was.
// A guest of Hank: web/hank.js mounts it if it loaded and keeps his still faces if not; nothing else depends on it.
// No texts here: hank.js owns every caption and aria-label (keys in web/hank-i18n.js), this file only draws.
// The markup is fixed in this file, never data, and is parsed once per stage with DOMParser, as hank.js does.
//
// API (window.HankScenes):
//   mount(element, {size: "head" | "mini", loops, onScene(name)}) -> stage
//                             loops: how often a state scene repeats before it holds its still frame (default: until
//                             the state changes); the always visible mini takes a small number (spec 9.2: "selten und kurz")
//   stage.setState(id, face)  id from the server (GET /api/hank/state, scene.id): sleep, umbrella, fan, scarf, point,
//                             idle; anything else is a still pose. face = the rest face for idle and still.
//   stage.allowChores(on)     chores (water, prune, feed, lens, coffee, gnat) only while on and the state is idle,
//                             at most one every few minutes, never two at once, never with reduced motion
//   stage.playChore(name)     one chore now (state idle only); returns its name or null
//   stage.setActive(on)       off pauses everything (closed drawer, hidden line); a hidden tab pauses on its own
//   stage.stop()              back to a still pose, nothing scheduled, until the next setState
//   stage.moment(name, still) after the harvest (web/workspace.js): harvest, dry, cure, ready, archive or clean plays once and
//                             keeps its last frame; with still (and always with reduced motion) only its still frame
(function () {
  "use strict";

  if (window.HankScenes || typeof Element === "undefined" || !Element.prototype.animate) return;

  const EYES = {
    hello: '<path d="M37 66 L53 64.5 V77 H40.5 Z" fill="var(--eye-white)"/><path d="M43 64.9 L52.2 64.6 V77 H43 Z" fill="var(--iris)"/><path d="M45.3 64.8 L51.4 64.6 V74.5 H45.3 Z" fill="var(--eye)"/><rect x="46" y="66" width="2.8" height="2.8" fill="var(--hi)"/><rect x="49.4" y="70.4" width="1.6" height="1.6" fill="var(--hi)"/><path d="M35 67.5 L37.5 64.5 L54 62.8" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 64.5 L83 66 L79.5 77 H67 Z" fill="var(--eye-white)"/><path d="M68.8 64.6 L78 64.9 V77 H68.8 Z" fill="var(--iris)"/><path d="M69.6 64.6 L75.7 64.8 V74.5 H69.6 Z" fill="var(--eye)"/><rect x="70.3" y="66" width="2.8" height="2.8" fill="var(--hi)"/><rect x="73.9" y="70.4" width="1.6" height="1.6" fill="var(--hi)"/><path d="M66 62.8 L82.5 64.5 L85 67.5" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    think: '<path d="M37 66 L53 64.5 V77 H40.5 Z" fill="var(--eye-white)"/><path d="M44.4 64.30000000000001 L53.6 63.99999999999999 V77 H44.4 Z" fill="var(--iris)"/><path d="M46.699999999999996 64.2 L52.8 63.99999999999999 V73.9 H46.699999999999996 Z" fill="var(--eye)"/><rect x="47.4" y="65.4" width="2.8" height="2.8" fill="var(--hi)"/><rect x="50.8" y="69.80000000000001" width="1.6" height="1.6" fill="var(--hi)"/><path d="M35 67.5 L37.5 64.5 L54 62.8" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 64.5 L83 66 L79.5 77 H67 Z" fill="var(--eye-white)"/><path d="M70.2 63.99999999999999 L79.4 64.30000000000001 V77 H70.2 Z" fill="var(--iris)"/><path d="M71.0 63.99999999999999 L77.10000000000001 64.2 V73.9 H71.0 Z" fill="var(--eye)"/><rect x="71.7" y="65.4" width="2.8" height="2.8" fill="var(--hi)"/><rect x="75.30000000000001" y="69.80000000000001" width="1.6" height="1.6" fill="var(--hi)"/><path d="M66 62.8 L82.5 64.5 L85 67.5" stroke="var(--eye)" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    alert: '<path d="M37 64 L53 62.5 V78 H40 Z" fill="var(--eye-white)"/><path d="M45.5 67 H50.5 V74 H45.5 Z" fill="var(--eye)"/><rect x="46.2" y="67.8" width="1.8" height="1.8" fill="var(--hi)"/><path d="M35.5 65.5 L37.5 62.5 L54 61" stroke="var(--eye)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 62.5 L83 64 L80 78 H67 Z" fill="var(--eye-white)"/><path d="M69.5 67 H74.5 V74 H69.5 Z" fill="var(--eye)"/><rect x="70.2" y="67.8" width="1.8" height="1.8" fill="var(--hi)"/><path d="M66 61 L82.5 62.5 L84.5 65.5" stroke="var(--eye)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    joy: '<path d="M38 72.5 L45.5 65.5 L53 72.5" stroke="var(--eye)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 72.5 L74.5 65.5 L82 72.5" stroke="var(--eye)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    sleep: '<path d="M38 69.5 L45.5 73 L53 69.5" stroke="var(--eye)" stroke-width="2.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 69.5 L74.5 73 L82 69.5" stroke="var(--eye)" stroke-width="2.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  };
  const BROWS = {
    hello: '<path d="M37.5 61 L53 60" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 60 L82.5 61" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    think: '<path d="M38 61 L53 60" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 57.5 L82 56.8" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    alert: '<path d="M37 62 L53 57.8" stroke="var(--beard)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 57.8 L83 62" stroke="var(--beard)" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    joy: '<path d="M38 59 L53 58" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M67 58 L82 59" stroke="var(--beard)" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  };
  const MOUTH = {
    hello: '<path d="M52 91.5 L60 94.2 L68 91.5" stroke="var(--mouth)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    think: '<ellipse cx="62" cy="93.2" rx="2.4" ry="2.6" fill="var(--mouth)"/>',
    alert: '<path d="M53 93.2 H67" stroke="var(--mouth)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    joy: '<path d="M50 90 H70 L65 97.5 H55 Z" fill="var(--mouth)"/><path d="M51.5 90.4 H68.5 L67.4 92.2 H52.6 Z" fill="var(--hi)"/><path d="M55.5 97 L60 94.4 L64.5 97 Z" fill="var(--blush)"/>',
    sleep: '<path d="M55.5 93 L60 95 L64.5 93" stroke="var(--mouth)" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  };
  const EXTRA = {
    hello: '',
    think: '<circle cx="99" cy="31" r="2.6" fill="var(--muted)"/><circle cx="105" cy="24" r="3.3" fill="var(--muted)"/><circle cx="111" cy="15" r="4.2" fill="var(--muted)"/>',
    alert: '<circle cx="104" cy="20" r="11" fill="var(--warn)"/><path d="M104 13.5 V21.5" stroke="var(--alert-ink)" stroke-width="3.2" stroke-linecap="round"/><circle cx="104" cy="26.5" r="1.9" fill="var(--alert-ink)"/>',
    joy: '<path d="M14 26 l2.4 5.6 5.6 2.4 -5.6 2.4 -2.4 5.6 -2.4 -5.6 -5.6 -2.4 5.6 -2.4z" fill="var(--straw)"/><path d="M106 22 l1.8 4.2 4.2 1.8 -4.2 1.8 -1.8 4.2 -1.8 -4.2 -4.2 -1.8 4.2 -1.8z" fill="var(--accent)"/>',
    sleep: '',
  };
  BROWS.sleep = BROWS.hello;
  EYES.worry = EYES.alert; BROWS.worry = BROWS.alert; MOUTH.worry = MOUTH.alert; EXTRA.worry = "";
  const EXPRS = ["hello", "think", "alert", "joy", "sleep", "worry"];

  const SHIRT = '<path d="M27 120 C29 103 43 97 60 97 C77 97 91 103 93 120 Z" fill="var(--shirt)"/>' +
    '<path d="M36 106 H84 M33 113 H87 M47 99 V120 M73 99 V120" stroke="var(--shirt-line)" stroke-width="1.6" opacity=".7"/>' +
    '<path d="M51 86 H69 V100 L60 104 L51 100 Z" fill="var(--skin-shade)"/>' +
    '<path d="M44 97 L60 103 L76 97 L80 108 L60 119 L40 108 Z" fill="var(--band)" stroke="var(--hk-line)" stroke-width="1.4" stroke-linejoin="round"/>' +
    '<path d="M60 103 L76 97 L80 108 L60 119 Z" fill="var(--band-dark)"/>';
  const EARS_FACE = '<path d="M25.5 57.7 L20.5 80.6 L28 75.5 L28 90.8 L38.3 85.7 L38.3 52.6 Z" fill="var(--hair-dark)"/><path d="M94.5 57.7 L99.5 78 L92 75.5 L92 85.7 L81.7 83.2 L81.7 52.6 Z" fill="var(--hair-dark)"/>' +
    '<ellipse cx="29.5" cy="71" rx="4.6" ry="7" fill="var(--skin-shade)"/><ellipse cx="90.5" cy="71" rx="4.6" ry="7" fill="var(--skin-shade)"/>' +
    '<path d="M31 50 H89 L89.5 70 L84 86 L71.5 98.5 H48.5 L36 86 L30.5 70 Z" fill="var(--skin)" stroke="var(--hk-line)" stroke-width="2" stroke-linejoin="round"/>' +
    '<path d="M88.5 51 L88.6 70 L83.2 85.5 L71 97.5 H64 L77 81 L80 51 Z" fill="var(--skin-shade)"/>' +
    '<path d="M36.8 85.8 L48.9 97.5 H71.1 L83.3 85.8 L77 83 L70 91.5 H50 L43 83 Z" fill="var(--stubble)"/>';
  const HAT_SHADE = '<path d="M31 56 H89 L89.3 63.5 H30.7 Z" fill="var(--skin-shade)" opacity=".55"/>';
  const NOSE = '<path d="M60.5 70 L62 80 L58.5 80.8" stroke="var(--skin-shade)" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
  const MUSTACHE = '<path d="M39.6 88.3 L49.8 80.6 L60 83.2 L70.2 80.6 L80.4 88.3 L70.2 88.3 L60 85.7 L49.8 88.3 Z" fill="var(--beard)" stroke="var(--hk-line)" stroke-width="1" stroke-linejoin="round"/>';
  const HAT = '<path d="M7.7 52.6 L33.2 44.9 L38.3 24.5 L58.7 19.4 L81.7 24.5 L86.8 44.9 L112.3 52.6 L89.3 57.7 H28.1 Z" fill="var(--straw)" stroke="var(--hk-line)" stroke-width="2.4" stroke-linejoin="round"/>' +
    '<path d="M38.8 25 L58.7 20.4 L63.8 20.4 L53.6 47.5 H33.8 Z" fill="var(--straw-hi)"/><path d="M8.8 52.4 L33.5 45.4 H58.7 L48.5 52.4 Z" fill="var(--straw-hi)"/>' +
    '<path d="M74 23 L81 25 L85.8 47.5 H76.6 Z" fill="var(--straw-mid)"/><path d="M28.3 56.9 H89.2 L102 54.6 H18.5 Z" fill="var(--straw-dark)"/>' +
    '<path d="M33.6 39.8 H86 L87 47.5 H33 Z" fill="var(--band)"/><path d="M71.5 39.8 H86 L87 47.5 H71.5 Z" fill="var(--band-dark)"/>' +
    '<path d="M68.9 37.3 L81.7 27 L86.8 34.7 L76.6 39.8 Z" fill="var(--hat-leaf)"/><path d="M70 37 L83 31" stroke="var(--leaf-dark)" stroke-width="1.2"/>';


  // ---- scene rig: stage 240 x 150, Hank's 120 box sits at OFF, arms hang from SH ----
  const D = Math.PI / 180;
  const f = (n) => Math.round(n * 100) / 100;
  const OFF = [14, 16];
  const SH = { R: [84, 110], L: [36, 110] };
  const A1 = 23, A2 = 22;
  function rot(x, y, deg) { const a = deg * D; return [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a)]; }

  // The fringe shows under the hat, the spikes on top only when the hat is off.
  const HAIR = '<path d="M29 57 L32 44 L39 47 L44 35 L52 43 L60 32 L67 43 L76 35 L81 47 L88 44 L91 57 Z" fill="var(--hair)"/>' +
    '<path d="M28 50 H90 V60.2 L79.1 55.1 L74 62.8 L66.4 52.6 L58.7 62.8 L51.1 52.6 L43.4 62.8 L38.3 55.1 L30.7 65.3 Z" fill="var(--hair)"/>' +
    '<path d="M28 50 H46 L38.3 55.1 L30.7 65.3 Z" fill="var(--hair-dark)"/>' +
    '<path d="M58.7 52.6 V62.8 M74 53 V62.8" stroke="var(--hair-dark)" stroke-width="1.4"/>';
  const SWEAT = '<g class="fx fx-sweat"><path d="M85 63 C87.4 66.5 88.4 69 88.4 70.6 A3.4 3.4 0 0 1 81.6 70.6 C81.6 69 82.6 66.5 85 63 Z" fill="var(--sweat)"/><circle cx="83.7" cy="70" r=".9" fill="var(--hi)"/></g>';
  // Mitten hand; its centre is the grip point of every prop. Two short finger lines on the side the props leave,
  // not a centred crease (that read as a little face).
  const FIST = '<ellipse cx="-4.4" cy="-3.6" rx="2.8" ry="2.4" fill="var(--skin)" stroke="var(--skin-shade)" stroke-width="1.2"/>' +
    '<circle r="6.2" fill="var(--skin)" stroke="var(--skin-shade)" stroke-width="1.2"/>' +
    '<path d="M1.4 -2.4 Q3.6 -2.9 5.4 -1.9 M1.4 1.4 Q3.6 0.9 5.4 1.9" stroke="var(--skin-shade)" stroke-width="1.2" fill="none" stroke-linecap="round"/>';

  // Props in hand coordinates, drawn upright, grip at 0 0. Points that effects need are named here.
  const CAN_TIP = [41, -10], BOTTLE_MOUTH = [0, -28], HAT_GRIP = [100, 57], SHEARS_CUT = [23.4, 0];
  // Measuring jug in its own coordinates: bottom centre at 0 0, handle on the left, pour lip top right.
  const JUG_GRIP = [-11, -8], JUG_LIP = [10.6, -16.8];
  const JUG_BODY = "M-7 -15 H6.2 L10.6 -16.8 L7 -12 V-1.6 Q7 0 5.4 0 H-5.4 Q-7 0 -7 -1.6 Z";
  const JUG = '<path d="M-7 -12.6 C-12.8 -12.6 -12.8 -3.4 -7 -3.4" stroke="var(--metal-dark)" stroke-width="2.6" fill="none" stroke-linecap="round"/>' +
    `<path d="${JUG_BODY}" fill="var(--jug)"/>` +
    '<g transform="translate(0 -0.8)"><g class="jug-liq"><path d="M-6.3 -10.4 H6.3 V0 H-6.3 Z" fill="var(--fert)"/></g></g>' +
    '<path d="M2.2 -3.6 H6.2 M3.8 -7.2 H6.2 M2.2 -10.8 H6.2" stroke="var(--metal-dark)" stroke-width="1.5" stroke-linecap="round"/>' +
    `<path d="${JUG_BODY}" fill="none" stroke="var(--metal-dark)" stroke-width="1.4" stroke-linejoin="round"/>`;
  // After the harvest. A bud is a small round cluster with orange hairs; a cut branch stands upright, base at 0 0.
  const BUD = (x, y, r) => `<circle cx="${x}" cy="${y}" r="${r}" fill="var(--sc-bud)"/>` +
    `<circle cx="${f(x - r * 0.3)}" cy="${f(y - r * 0.3)}" r="${f(r * 0.5)}" fill="var(--sc-bud-dark)" opacity=".35"/>` +
    `<path d="M${f(x - r * 0.5)} ${f(y)} l-1.6 -1.2 M${f(x + r * 0.3)} ${f(y - r * 0.5)} l1.2 -1.6 M${f(x + r * 0.4)} ${f(y + r * 0.4)} l1.6 0.8" ` +
    'stroke="var(--sc-pistil)" stroke-width="1" stroke-linecap="round"/>';
  const BRANCH = '<path d="M0 2 C0.6 -12 -0.8 -26 0 -36" stroke="var(--leaf-dark)" stroke-width="2.4" fill="none" stroke-linecap="round"/>' +
    '<path d="M0 -15 Q-6 -18 -10 -23 M0 -21 Q6 -25 9 -30" stroke="var(--leaf-dark)" stroke-width="1.6" fill="none" stroke-linecap="round"/>' +
    `<path d="${leaf(0, -7, -14, -13, 3.6)}" fill="var(--leaf)"/><path d="${leaf(0, -10, 13, -16, 3.4)}" fill="var(--leaf)"/>` +
    BUD(-10, -24, 4) + BUD(9, -31, 4) + BUD(-2, -30, 3.6) + BUD(0, -39, 5.4);
  const PROPS_AFTER =
    `<g class="p p-branch"><g transform="translate(0 7) scale(1.25)">${BRANCH}</g></g>` +
    // the jar's lid, held from above
    '<g class="p p-lid"><rect x="-11" y="3" width="22" height="6" rx="2" fill="var(--sc-lid)"/><path d="M-11 6 H11" stroke="var(--sc-lid-dark)" stroke-width="1"/></g>' +
    '<g class="p p-label"><rect x="4" y="-5.5" width="14" height="11" rx="1.6" fill="var(--hi)" stroke="var(--metal-dark)" stroke-width=".8"/>' +
      '<path d="M7.4 0.1 L9.8 2.5 L14 -2.5" stroke="var(--sc-check)" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>' +
    // a trigger spray bottle, held at the trigger, nozzle to the right; and a cloth for wiping
    '<g class="p p-spray"><rect x="-6" y="3" width="12" height="20" rx="3" fill="var(--sc-spray)"/><rect x="-3.4" y="-1" width="6.8" height="5" fill="var(--sc-spray-dark)"/>' +
      '<path d="M-5 -1 H6 L13 -5 V-8 H-5 Z" fill="var(--sc-spray-dark)"/><path d="M2 -1 L4 6" stroke="var(--sc-spray-dark)" stroke-width="2.2" stroke-linecap="round"/>' +
      '<rect x="-3.6" y="9" width="7.2" height="8" rx="1.2" fill="var(--hi)" opacity=".85"/></g>' +
    '<g class="p p-cloth"><path d="M-9 2 Q-10 10 -7 13 Q0 15 8 12 Q10 6 8 1 Q0 3 -9 2 Z" fill="var(--sc-cloth)"/>' +
      '<path d="M-6 6 Q0 8 6 5 M-5 10 Q0 11.5 5 9" stroke="var(--sc-cloth-dark)" stroke-width="1" fill="none" opacity=".7"/></g>' +
    // the record card, held between the fingers
    '<g class="p p-card"><rect x="-7" y="-22" width="14" height="18" rx="1.6" fill="var(--sc-card)" stroke="var(--metal-dark)" stroke-width=".9"/>' +
      '<path d="M-4 -17.5 H4 M-4 -13.5 H4 M-4 -9.5 H1" stroke="var(--metal-dark)" stroke-width="1" stroke-linecap="round" opacity=".7"/></g>';
  const STEAM = (x, i) => `<g transform="translate(${x} -10)"><g class="fx steam s${i}"><path d="M0 0 C-1.8 -2.6 1.8 -4.4 0 -7 C-1.8 -9.6 1.8 -11.4 0 -12" stroke="var(--steam)" stroke-width="1.6" fill="none" stroke-linecap="round"/></g></g>`;
  const PROPS = {
    R:
      '<g class="p p-can"><path d="M5 -7 C-2 -7 -2 7 5 7" stroke="var(--can-dark)" stroke-width="3.2" fill="none" stroke-linecap="round"/>' +
        '<path d="M25 4 L39.5 -8.5" stroke="var(--can)" stroke-width="3.6" stroke-linecap="round"/>' +
        '<ellipse cx="41" cy="-10" rx="2.2" ry="3.8" transform="rotate(-40 41 -10)" fill="var(--can-dark)"/>' +
        '<path d="M5 -9 H25 L27 10 H3 Z" fill="var(--can)"/><path d="M8 -12 H22 V-9 H8 Z" fill="var(--can-dark)"/>' +
        '<path d="M8.5 -5 V6" stroke="var(--hi)" stroke-width="2" stroke-linecap="round" opacity=".35"/></g>' +
      '<g class="p p-shears"><g transform="scale(1.3)"><path d="M3 -1.2 L-8 -4.4 M3 1.2 L-8 4.4" stroke="var(--grip)" stroke-width="3.6" stroke-linecap="round"/>' +
        '<g transform="translate(8 0)"><g class="blade-a"><path d="M-2 -1.6 Q6 -4.6 15.5 -0.6 L-2 1 Z" fill="var(--metal)" stroke="var(--metal-dark)" stroke-width="1" stroke-linejoin="round"/></g></g>' +
        '<g transform="translate(8 0)"><g class="blade-b"><path d="M-2 1.6 Q5 4 13.5 0.6 L-2 -1 Z" fill="var(--metal-dark)"/></g></g>' +
        '<circle cx="8" cy="0" r="1.7" fill="var(--metal-dark)"/></g></g>' +
      // squat plant-food bottle, green, white label with a leaf, pointed dosing cap
      '<g class="p p-bottle"><rect x="-6" y="-20" width="12" height="20" rx="3" fill="var(--bottle)"/>' +
        '<rect x="-3" y="-23.6" width="6" height="4.4" fill="var(--bottle)"/><path d="M-3.2 -23.4 H3.2 L1.1 -28 H-1.1 Z" fill="var(--bottle-dark)"/>' +
        '<rect x="-4.4" y="-17.6" width="8.8" height="9" rx="1.4" fill="var(--hi)"/>' +
        `<path d="${leaf(-2.5, -10, 2.5, -16.2, 1.9)}" fill="var(--leaf)"/></g>` +
      `<g class="p p-jug"><g transform="translate(${-JUG_GRIP[0]} ${-JUG_GRIP[1]})">${JUG}</g></g>` +
      '<g class="p p-lens"><path d="M-4 4 L7.5 -7.5" stroke="var(--wood)" stroke-width="4.2" stroke-linecap="round"/>' +
        '<circle cx="13.5" cy="-13.5" r="8.6" fill="var(--glass)" opacity=".45"/>' +
        '<circle cx="13.5" cy="-13.5" r="8.6" fill="none" stroke="var(--metal-dark)" stroke-width="2.4"/>' +
        '<path d="M8.6 -16.8 Q10.6 -19.8 14.2 -20" stroke="var(--hi)" stroke-width="1.4" fill="none" stroke-linecap="round" opacity=".85"/></g>' +
      '<g class="p p-mug"><path d="M4 -4.5 C-2.8 -4.5 -2.8 4.5 4 4.5" stroke="var(--mug)" stroke-width="3.2" fill="none" stroke-linecap="round"/>' +
        '<rect x="3" y="-8" width="14" height="16" rx="2.6" fill="var(--mug)"/><path d="M3 -1.5 H17" stroke="var(--hi)" stroke-width="2.2" opacity=".75"/>' +
        '<ellipse cx="10" cy="-8" rx="6.2" ry="1.6" fill="var(--coffee)"/>' + STEAM(8, 1) + STEAM(12, 2) + "</g>" +
      '<g class="p p-umb"><path d="M0 0 V-86" stroke="var(--metal-dark)" stroke-width="2"/>' +
        '<path d="M0 0 V5 C0 10 -6 10 -6 5" stroke="var(--wood)" stroke-width="3" fill="none" stroke-linecap="round"/>' +
        '<path class="umb-furl" d="M0 -88 C3.2 -76 3.2 -58 0 -46 C-3.2 -58 -3.2 -76 0 -88 Z" fill="var(--umb)"/>' +
        '<g transform="translate(0 -88)"><g class="umb-open">' +
          '<path d="M-44 16 C-44 4 -24 0 0 0 C24 0 44 4 44 16 Q33 12 22 16 Q11 12 0 16 Q-11 12 -22 16 Q-33 12 -44 16 Z" fill="var(--umb)"/>' +
          '<path d="M0 0 L-22 16 M0 0 V16 M0 0 L22 16" stroke="var(--umb-dark)" stroke-width="1.3" fill="none"/>' +
          '<circle cx="0" cy="-1.5" r="1.8" fill="var(--umb-dark)"/></g></g></g>' +
      // winter scarf: blue with white stripes, so it says cold, not straw
      '<g class="p p-scarf"><path d="M-6 -1 H6 L5.4 25 H-4.6 Z" fill="var(--scarf)"/>' +
        '<path d="M-5.6 8 H5.8 M-5.2 16 H5.6" stroke="var(--hi)" stroke-width="2.2" opacity=".9"/>' +
        '<path d="M-3.6 25 V28.5 M-0.8 25 V28.5 M2 25 V28.5 M4.6 25 V28.5" stroke="var(--scarf-dark)" stroke-width="1.3" stroke-linecap="round"/></g>' +
      `<g class="p p-hat"><g transform="translate(${-HAT_GRIP[0]} ${-HAT_GRIP[1]})">${HAT}</g></g>` +
      '<g class="p p-point"><rect x="2" y="-2.4" width="18" height="4.8" rx="2.4" fill="var(--skin)" stroke="var(--skin-shade)" stroke-width="1.2"/></g>' +
      PROPS_AFTER,
    L:
      '<g class="p p-fan"><g transform="scale(1.25)"><path d="M0 0 L-4.2 -5.6 M0 0 L-2.2 -6.6 M0 0 V-7 M0 0 L2.2 -6.6 M0 0 L4.2 -5.6" stroke="var(--wood)" stroke-width="1.3"/>' +
        '<path d="M-12.6 -16.8 A21 21 0 0 1 12.6 -16.8 L4.2 -5.6 A7 7 0 0 0 -4.2 -5.6 Z" fill="var(--fan)" stroke="var(--fan-dark)" stroke-width="1" stroke-linejoin="round"/>' +
        '<path d="M-2.2 -6.6 L-6.7 -19.9 M0 -7 V-21 M2.2 -6.6 L6.7 -19.9" stroke="var(--fan-dark)" stroke-width="1"/></g></g>',
  };
  const SCARF_WORN = '<g class="p p-scarfw"><path d="M33 101 C43 109 77 109 87 101 L89 108 C78 116 42 116 31 108 Z" fill="var(--scarf)"/>' +
    '<path d="M33.5 104.8 C44 111.5 76 111.5 86.5 104.8" stroke="var(--hi)" stroke-width="2" fill="none" opacity=".9"/>' +
    '<path d="M69 108 L80 107 L82.5 123 L71.5 123.5 Z" fill="var(--scarf)"/>' +
    '<path d="M70 113 L81 112.4 M70.8 118.4 L81.8 117.8" stroke="var(--hi)" stroke-width="2" opacity=".9"/>' +
    '<path d="M73 124 V127 M76 124 V127 M79 123.6 V126.6" stroke="var(--scarf-dark)" stroke-width="1.3" stroke-linecap="round"/></g>';

  // Sleeves carry an outline in the plaid colour, so an arm in front of the shirt still reads as an arm.
  const SLEEVE = (w, len, line) => `<path d="M0 0 V${len}" stroke="var(--shirt-line)" stroke-width="${w + 3.2}" stroke-linecap="round"/>` +
    `<path d="M0 0 V${len}" stroke="var(--shirt)" stroke-width="${w}" stroke-linecap="round"/>` + line;
  const UPPER = SLEEVE(11, A1, `<path d="M-5.5 ${A1 * 0.55} H5.5" stroke="var(--shirt-line)" stroke-width="1.6" opacity=".7"/>`);
  // The shoulder never moves. For a high reach the upper arm stretches (.j-ua) and the elbow moves out with it (.j-ex).
  // Upper arm behind the shirt; forearm, hand and prop in front of the head. Both shoulder copies get the same animation.
  function armBack(side) {
    const [sx, sy] = SH[side];
    return `<g transform="translate(${sx} ${sy})"><g class="j-sh${side}"><g class="j-ua${side}">${UPPER}</g></g></g>`;
  }
  function armFront(side) {
    const [sx, sy] = SH[side];
    return `<g transform="translate(${sx} ${sy})"><g class="j-sh${side}">` +
      `<g class="j-ex${side}"><g transform="translate(0 ${A1})"><g class="j-el${side}">` +
      SLEEVE(10, A2 - 6, `<path d="M-4.6 ${A2 - 6.5} H4.6" stroke="var(--shirt-line)" stroke-width="2.2" stroke-linecap="round"/>`) +
      `<g transform="translate(0 ${A2})"><g class="j-ha${side}">${PROPS[side]}${FIST}</g></g>` +
      "</g></g></g></g></g>";
  }
  function hankRig() {
    const faces = EXPRS.map((e) => `<g class="f f-${e}">${EYES[e]}${BROWS[e]}${MOUTH[e]}</g>`).join("");
    const extras = EXPRS.map((e) => `<g class="x x-${e}">${EXTRA[e]}</g>`).join("");
    return `<g transform="translate(${OFF[0]} ${OFF[1]})"><g class="j-body">` +
      armBack("L") + armBack("R") +
      '<path d="M27 119 H93 V142 H27 Z" fill="var(--shirt)"/><path d="M47 119 V142 M73 119 V142" stroke="var(--shirt-line)" stroke-width="1.6" opacity=".7"/>' +
      SHIRT + SCARF_WORN +
      '<g transform="translate(60 100)"><g class="j-head"><g transform="translate(-60 -100)">' +
      EARS_FACE + `<g class="hshade">${HAT_SHADE}</g>` + HAIR + NOSE + faces + MUSTACHE + SWEAT +
      `<g transform="translate(60 49)"><g class="j-hat"><g transform="translate(-60 -49)"><g class="head-hat">${HAT}</g></g></g></g>` +
      extras + "</g></g></g>" +
      armFront("L") + armFront("R") + "</g></g>";
  }

  // Plant: a generic leafy pot plant. Leaves are base, tip, half width.
  function leaf(bx, by, tx, ty, hw) {
    const dx = tx - bx, dy = ty - by, L = Math.hypot(dx, dy), n = hw / 0.75;
    const nx = (-dy / L) * n, ny = (dx / L) * n;
    const p = (u, s) => `${f(bx + dx * u + nx * s)} ${f(by + dy * u + ny * s)}`;
    return `M${bx} ${by} C${p(0.15, 1)} ${p(0.72, 1)} ${tx} ${ty} C${p(0.72, -1)} ${p(0.15, -1)} ${bx} ${by} Z`;
  }
  const vein = (bx, by, tx, ty) => `<path d="M${bx} ${by} L${f(bx + (tx - bx) * 0.8)} ${f(by + (ty - by) * 0.8)}" stroke="var(--leaf-dark)" stroke-width="1.1" opacity=".5" fill="none"/>`;
  const CUT_LEAF = [156, 104, 136, 111, 5.2];
  function plant() {
    const back = [[173, 74, 196, 58, 6], [171, 76, 149, 61, 6]];
    const front = [[172, 66, 175, 43, 5.6], [171, 88, 145, 81, 6.2], [173, 86, 200, 79, 6.2], [173, 99, 197, 100, 5.6]];
    return '<path d="M155 113 H189 L185 138 H159 Z" fill="var(--pot)"/>' +
      '<path d="M155 113 H189 L188.3 117 H155.7 Z" fill="var(--pot-dark)" opacity=".55"/>' +
      '<rect x="152" y="106" width="40" height="8" rx="2.5" fill="var(--pot)"/>' +
      '<ellipse cx="172" cy="107" rx="17" ry="2.6" fill="var(--soil)"/>' +
      '<g transform="translate(172 106)"><g class="j-plant"><g transform="translate(-172 -106)">' +
      back.map((l) => `<path d="${leaf(...l)}" fill="var(--leaf-dark)"/>`).join("") +
      '<path d="M172 106 C171 92 173 80 172 64" stroke="var(--leaf-dark)" stroke-width="3" fill="none" stroke-linecap="round"/>' +
      '<path d="M171 100 Q164 101.5 156.5 104" stroke="var(--leaf-dark)" stroke-width="2" fill="none" stroke-linecap="round"/>' +
      // the low leaf is green like the rest; only the prune scene turns it yellow, and after the cut a green one grows back
      `<g transform="translate(${CUT_LEAF[0]} ${CUT_LEAF[1]})"><g class="leaf-cut"><g transform="translate(${-CUT_LEAF[0]} ${-CUT_LEAF[1]})">` +
      `<path d="${leaf(...CUT_LEAF)}" fill="var(--leaf)"/>${vein(...CUT_LEAF.slice(0, 4))}` +
      `<g class="leaf-yel"><path d="${leaf(...CUT_LEAF)}" fill="var(--leaf-old)"/>${vein(...CUT_LEAF.slice(0, 4))}</g></g></g></g>` +
      front.map((l) => `<path d="${leaf(...l)}" fill="var(--leaf)"/>${vein(...l.slice(0, 4))}`).join("") +
      "</g></g></g>";
  }
  // Scenery after the harvest, hidden until a moment shows it (.set). Behind Hank: the drying line with two branches
  // head down, the jar, the inside and the open flap of the archive box. In front of him: the box's front and its shut lid.
  const HANG = [[156, 29.3], [190, 29.6]];                      // pegs on the line, the branches' pivots
  const JAR_LID = [149, 91];                                    // where the hand holds the lid
  const JAR_LABEL = [149, 119.5];
  function setBack() {
    const line = '<g class="set set-line"><path d="M136 27 Q176 33 216 27" stroke="var(--sc-string)" stroke-width="1.2" fill="none"/>' +
      HANG.map(([x, y], i) => `<g transform="translate(${x} ${y})"><g class="sway${i + 1}"><g transform="rotate(180)">${BRANCH}</g>` +
        '<rect x="-1.6" y="-3.4" width="3.2" height="5" rx="1" fill="var(--sc-peg)"/></g></g>').join("") + "</g>";
    const jar = '<g class="set set-jar"><rect x="137" y="104" width="24" height="34" rx="5" fill="var(--glass)" opacity=".55"/>' +
      BUD(143, 131, 4.4) + BUD(152.5, 131.5, 4.6) + BUD(147, 123, 4.2) + BUD(156, 122, 3.8) + BUD(142, 115, 3.6) + BUD(151, 113, 4) +
      '<rect x="137" y="104" width="24" height="34" rx="5" fill="none" stroke="var(--sc-glass-edge)" stroke-width="1.4"/>' +
      '<path d="M140 104 V100 H158 V104" fill="var(--glass)" fill-opacity=".55" stroke="var(--sc-glass-edge)" stroke-width="1.2"/>' +
      '<path d="M140.4 108 V128" stroke="var(--hi)" stroke-width="1.6" opacity=".6" stroke-linecap="round"/>' +
      `<g class="jar-label"><g transform="translate(${JAR_LABEL[0] - 11} ${JAR_LABEL[1]})"><rect x="4" y="-5.5" width="14" height="11" rx="1.6" fill="var(--hi)" stroke="var(--metal-dark)" stroke-width=".8"/>` +
        '<path d="M7.4 0.1 L9.8 2.5 L14 -2.5" stroke="var(--sc-check)" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g></g>' +
      `<g class="jar-lid"><g transform="translate(${JAR_LID[0]} ${JAR_LID[1]})"><rect x="-11" y="3" width="22" height="6" rx="2" fill="var(--sc-lid)"/>` +
        '<path d="M-11 6 H11" stroke="var(--sc-lid-dark)" stroke-width="1"/></g></g></g>';
    const box = '<g class="set set-boxback"><rect x="128" y="106" width="44" height="7" fill="var(--sc-box-in)"/>' +
      '<g transform="translate(150 106)"><g class="box-flap"><rect x="-22" y="-22" width="44" height="22" rx="1.5" fill="var(--sc-box)" stroke="var(--sc-box-dark)" stroke-width="1.2"/></g></g></g>';
    return line + jar + box;
  }
  function setFront() {
    return '<g class="set set-box"><rect x="126" y="112" width="48" height="26" rx="2" fill="var(--sc-box)"/><path d="M126 112.6 H174" stroke="var(--sc-box-dark)" stroke-width="1.6"/>' +
      '<rect x="140" y="119" width="20" height="12" rx="1.6" fill="var(--hi)" stroke="var(--sc-box-dark)" stroke-width=".8"/>' +
      '<path d="M144 123.4 H156 M144 126.8 H152" stroke="var(--metal-dark)" stroke-width="1" stroke-linecap="round" opacity=".7"/></g>' +
      '<g class="set set-shut"><rect x="124" y="105" width="52" height="8" rx="2" fill="var(--sc-box-dark)"/></g>';
  }
  const SPARK = '<path d="M0 -4.6 L1.2 -1.2 L4.6 0 L1.2 1.2 L0 4.6 L-1.2 1.2 L-4.6 0 L-1.2 -1.2 Z" fill="var(--sc-spark)"/>';
  // Where Hank stands the measuring jug while he doses: bottom centre on the bench top, between him and the pot.
  const JUG_AT = [132, 138];
  const DROP = "M0 -2.4 C1.5 -0.4 2.1 0.7 2.1 1.5 A2.1 2.1 0 0 1 -2.1 1.5 C-2.1 0.7 -1.5 -0.4 0 -2.4 Z";
  const RAIN_X = [6, 16, 148, 206, 220, 232];   // clear of the open umbrella
  const SNOW = '<path d="M0 -5 V5 M-4.33 -2.5 L4.33 2.5 M-4.33 2.5 L4.33 -2.5" stroke="var(--snow)" stroke-width="1.5" stroke-linecap="round"/>';
  function effects(emit) {
    const drops = (at, cls, n, fill) => `<g transform="translate(${f(at[0])} ${f(at[1])})">` +
      Array.from({ length: n }, (_, i) => `<g class="fx ${cls}${i + 1}"><path d="${DROP}" fill="${fill}"/></g>`).join("") + "</g>";
    return drops(emit.water, "fx-w", 4, "var(--water)") + drops(emit.fert, "fx-d", 4, "var(--fert)") + drops(emit.dose, "fx-j", 4, "var(--fert)") +
      `<g transform="translate(${CUT_LEAF[0]} ${CUT_LEAF[1]})"><g class="fx fx-leaf"><g transform="translate(${-CUT_LEAF[0]} ${-CUT_LEAF[1]})"><path d="${leaf(...CUT_LEAF)}" fill="var(--leaf-old)"/>${vein(...CUT_LEAF.slice(0, 4))}</g></g></g>` +
      RAIN_X.map((x, i) => `<g transform="translate(${x} 0)"><g class="fx fx-r${i + 1}"><path d="M0 0 V6" stroke="var(--water)" stroke-width="1.8" stroke-linecap="round"/></g></g>`).join("") +
      [1, 2].map((i) => `<g class="fx fx-snow${i}">${SNOW}</g>`).join("") +
      // air from the opened jar, and the sparkles of a finished one
      [[145, 97], [150, 96], [154, 97]].map(([x, y], i) => `<g transform="translate(${x} ${y})"><g class="fx steam fx-puff${i + 1}">` +
        '<path d="M0 0 C-1.8 -2.6 1.8 -4.4 0 -7 C-1.8 -9.6 1.8 -11.4 0 -12" stroke="var(--steam)" stroke-width="1.6" fill="none" stroke-linecap="round"/></g></g>').join("") +
      [1, 2, 3].map((i) => `<g class="fx fx-spark${i}">${SPARK}</g>`).join("") +
      `<g transform="translate(${f(EMIT_SPRAY[0])} ${f(EMIT_SPRAY[1])})">` +
        MIST.map((_, i) => `<g class="fx fx-m${i + 1}"><circle r="2.3" fill="var(--water)"/></g>`).join("") + "</g>" +
      // fungus gnat, 1.5 times the first draft so it reads on the stage; dark body in both themes, light edge in dark mode
      '<g class="fx fx-gnat"><g transform="scale(1.5)"><g class="wings"><ellipse cx="-2" cy="-2.6" rx="2.3" ry="3.3" transform="rotate(-25 -2 -2.6)" fill="var(--wing)" opacity=".85" stroke="var(--bug-edge)" stroke-width=".8"/>' +
        '<ellipse cx="2" cy="-2.6" rx="2.3" ry="3.3" transform="rotate(25 2 -2.6)" fill="var(--wing)" opacity=".85" stroke="var(--bug-edge)" stroke-width=".8"/></g>' +
        '<ellipse rx="2.9" ry="1.8" fill="var(--bug)" stroke="var(--bug-edge)" stroke-width=".7"/><circle cx="3" cy="-.5" r="1.3" fill="var(--bug)" stroke="var(--bug-edge)" stroke-width=".7"/></g></g>' +
      // the problem marker is a warning triangle, so it does not repeat the round badge on Hank's hat
      '<g class="fx fx-alarm"><g class="ping"><circle r="13" fill="none" stroke="var(--warn)" stroke-width="2"/></g>' +
        '<path d="M0 -11 L11 8 H-11 Z" fill="var(--warn)" stroke="var(--warn)" stroke-width="3" stroke-linejoin="round"/>' +
        '<path d="M0 -4.4 V1.8" stroke="var(--alert-ink)" stroke-width="2.6" stroke-linecap="round"/><circle cy="5.2" r="1.5" fill="var(--alert-ink)"/></g>';
  }
  function stageSvg(emit, label, vb) {
    const aria = label ? `role="img" aria-label="${label}"` : 'aria-hidden="true" focusable="false"';
    return `<svg class="rig rest" viewBox="${vb || "0 0 240 150"}" data-face="hello" data-state="normal" ${aria}>` +
      '<rect width="240" height="150" fill="var(--stage)"/><path d="M80 0 V138 M160 0 V138" stroke="var(--stage-line)" stroke-width="1.2"/>' +
      '<path class="glow" d="M153 12 H195 L216 138 H132 Z" fill="var(--glow)"/>' +
      '<path d="M158 0 V7 M190 0 V7" stroke="var(--lamp)" stroke-width="1.4"/><rect x="149" y="5" width="50" height="7" rx="3.5" fill="var(--lamp)"/>' +
      '<rect class="lamp-on" x="152" y="9.6" width="44" height="2.6" rx="1.3" fill="var(--glow)"/>' +
      `<g class="pot">${plant()}</g>` + setBack() + `<g transform="translate(${JUG_AT[0]} ${JUG_AT[1]})"><g class="p jug-bench">${JUG}</g></g>` +
      hankRig() + setFront() +
      '<rect x="0" y="138" width="240" height="12" fill="var(--bench)"/><rect x="0" y="138" width="240" height="2.6" fill="var(--bench-top)"/>' +
      '<path d="M0 145.5 H240" stroke="var(--bench-top)" stroke-width=".8" opacity=".6"/>' +
      effects(emit) +
      '<rect class="night" width="240" height="150" fill="var(--night)"/>' +
      '<path class="moon" d="M30 9 A9 9 0 1 0 38.5 22.5 A7.4 7.4 0 0 1 30 9 Z" fill="var(--moon)"/>' +
      [1, 2, 3].map((i) => `<g class="fx fx-z${i}"><path d="M-3 -3.5 H3 L-3 3.5 H3" stroke="var(--zzz)" stroke-width="1.9" fill="none" stroke-linejoin="round" stroke-linecap="round"/></g>`).join("") +
      "</svg>";
  }
  // ---- poses: a hand is placed in stage coordinates, the two arm joints are solved ----
  // "ext" lengthens the upper arm for a high reach (hat, can); the shoulder stays where the shirt is.
  const REST = "rest";
  const H = (x, y, tilt, o) => Object.assign({ x, y, tilt: tilt || 0 }, o);
  function solve(side, p, lean) {
    const ext = p.ext || 0, a1 = A1 + ext;
    const sx = SH[side][0], sy = SH[side][1];
    const x = p.x - OFF[0] - lean[0] - sx, y = p.y - OFF[1] - lean[1] - sy;
    const d = Math.min(Math.max(Math.hypot(x, y), Math.abs(a1 - A2) + 0.5), a1 + A2 - 0.01);
    const phi = Math.atan2(y, x) / D;
    const alpha = Math.acos((a1 * a1 + d * d - A2 * A2) / (2 * a1 * d)) / D;
    const up = phi + (p.bend || (side === "R" ? 1 : -1)) * alpha;
    const fore = Math.atan2(y - a1 * Math.sin(up * D), x - a1 * Math.cos(up * D)) / D;
    return { sh: up - 90, el: fore - up, ha: p.tilt - (fore - 90), ext };
  }
  // A point on Hank's head when the head is tipped by deg around the neck, in stage coordinates (no lean).
  const headPt = (x, y, deg) => { const r = rot(x - 60, y - 100, deg); return [f(60 + r[0] + OFF[0]), f(100 + r[1] + OFF[1])]; };
  const wrap = (a, prev) => { while (a - prev > 180) a -= 360; while (a - prev < -180) a += 360; return a; };
  const tip = (p, l) => { const r = rot(l[0], l[1], p.tilt); return [p.x + r[0], p.y + r[1]]; };
  function at(list, t) {
    let a = list[0];
    for (const b of list) {
      if (b[0] >= t) { if (b[0] === a[0]) return b[1]; const u = (t - a[0]) / (b[0] - a[0]); return a[1].map((v, i) => v + (b[1][i] - v) * u); }
      a = b;
    }
    return a[1];
  }

  // ---- tracks: every scene is a list of keyframes on one shared timeline ----
  const EASE = "ease-in-out";
  const JOINTS0 = { sh: 0, el: 0, ha: 0, ext: 0 };
  const INIT = { R: JOINTS0, L: JOINTS0, body: [0, 0], head: 0, hat: [0, 0] };
  const f3 = (n) => Math.round(n * 1000) / 1000;
  function channels(keys, dur, init) {
    const ch = { R: [], L: [], body: [], head: [], hat: [] };
    for (const [t, k] of keys) for (const c in k) ch[c].push([t, k[c]]);
    for (const c in ch) {
      const l = ch[c];
      l.sort((a, b) => a[0] - b[0]);
      if (!l.length || l[0][0] > 0) l.unshift([0, init[c]]);
      if (l[l.length - 1][0] < dur) l.push([dur, l[l.length - 1][1]]);
    }
    const tracks = [], end = {};
    for (const side of ["R", "L"]) {
      const sh = [], ua = [], ex = [], el = [], ha = [];
      let prev = null;
      for (const [t, p] of ch[side]) {
        let j = p === REST ? Object.assign({}, JOINTS0) : "sh" in p ? Object.assign({ ext: 0 }, p) : solve(side, p, at(ch.body, t));
        if (prev) { // unwrap the joints, then the prop's own angle, so a held prop never spins on the way
          const sh = wrap(j.sh, prev.sh), el = wrap(j.el, prev.el);
          const total = wrap(j.sh + j.el + j.ha, prev.sh + prev.el + prev.ha);
          j = { sh, el, ha: total - sh - el, ext: j.ext };
        }
        prev = j;
        const o = t / dur;
        sh.push({ offset: o, transform: `rotate(${f(j.sh)}deg)`, easing: EASE });
        ua.push({ offset: o, transform: `scale(1, ${f3((A1 + j.ext) / A1)})`, easing: EASE });
        ex.push({ offset: o, transform: `translate(0px, ${f(j.ext)}px)`, easing: EASE });
        el.push({ offset: o, transform: `rotate(${f(j.el)}deg)`, easing: EASE });
        ha.push({ offset: o, transform: `rotate(${f(j.ha)}deg)`, easing: EASE });
      }
      end[side] = prev;
      tracks.push([`.j-sh${side}`, sh], [`.j-ua${side}`, ua], [`.j-ex${side}`, ex], [`.j-el${side}`, el], [`.j-ha${side}`, ha]);
    }
    const simple = (c, sel, fmt) => {
      tracks.push([sel, ch[c].map(([t, v]) => ({ offset: t / dur, transform: fmt(v), easing: EASE }))]);
      end[c] = ch[c][ch[c].length - 1][1];
    };
    simple("body", ".j-body", (v) => `translate(${f(v[0])}px, ${f(v[1])}px)`);
    simple("head", ".j-head", (v) => `rotate(${f(v)}deg)`);
    simple("hat", ".j-hat", (v) => `translate(0px, ${f(v[0])}px) rotate(${f(v[1])}deg)`);
    return { tracks, end };
  }
  // Faces switch instantly. "think." means the face without its extra (the thought dots).
  function faceTracks(face, dur) {
    const out = [];
    for (const e of EXPRS) for (const layer of ["f", "x"]) {
      const kf = [];
      let prev = null;
      for (const [t, spec] of face) {
        const v = spec.replace(".", "") === e && !(layer === "x" && spec.endsWith(".")) ? 1 : 0;
        if (prev !== null && prev !== v) kf.push({ offset: t / dur, opacity: prev });
        kf.push({ offset: t / dur, opacity: v });
        prev = v;
      }
      if (kf[kf.length - 1].offset < 1) kf.push({ offset: 1, opacity: prev });
      out.push([`.${layer}-${e}`, kf]);
    }
    return out;
  }
  function raw([sel, list, o], dur) {
    if (o && o.kf) return [sel, list, o];
    const d = (o && o.timing && o.timing.duration) || dur, ease = (o && o.easing) || "linear";
    const kf = list.map(([t, p]) => Object.assign({ offset: Math.min(t / d, 1), easing: ease }, p));
    if (kf[0].offset > 0) kf.unshift(Object.assign({}, kf[0], { offset: 0 }));
    if (kf[kf.length - 1].offset < 1) kf.push(Object.assign({}, kf[kf.length - 1], { offset: 1 }));
    return [sel, kf, o];
  }
  const op = (v) => ({ opacity: v });
  const tf = (s) => ({ transform: s });
  const mv = (o, x, y, r) => ({ opacity: o, transform: `translate(${x}px, ${y}px) rotate(${r}deg)` });
  const mvy = (o, y) => ({ opacity: o, transform: `translate(0px, ${y}px)` });
  const ts = (o, x, y, s) => ({ opacity: o, transform: `translate(${x}px, ${y}px) scale(${s})` });
  const hold = (sel, props) => [sel, [[0, props]]];
  // A held prop fades only while the hand is still behind the bench.
  const show = (sel, a, b, fin = 120, fout = 160) => [sel, [[0, op(0)], [a, op(0)], [a + fin, op(1)], [b - fout, op(1)], [b, op(0)]]];
  function falls(start, period, count, ms, dy) {
    const k = [[0, mvy(0, 0)]];
    for (let i = 0; i < count; i++) {
      const s = start + i * period;
      k.push([s, mvy(0, 0)], [s + 60, mvy(1, f(dy * 60 / ms))], [s + ms - 70, mvy(1, f(dy * (ms - 70) / ms))], [s + ms, mvy(0, dy)]);
    }
    return k;
  }
  function rise(start, period, count, ms, dy) {
    const k = [[0, mvy(0, 0)]];
    for (let i = 0; i < count; i++) {
      const s = start + i * period;
      k.push([s, mvy(0, 0)], [s + ms * 0.35, mvy(0.85, f(dy * 0.35))], [s + ms, mvy(0, dy)]);
    }
    return k;
  }
  const shiver = (s, step, n) => Array.from({ length: n + 1 }, (_, i) => [s + i * step, { body: i === 0 || i === n ? [0, 0] : [i % 2 ? -2 : 2, 0] }]);

  // ---- the poses that effects depend on ----
  const W_POSE = H(129, 73, 50, { ext: 8 });                    // can tipped over the pot, spout points down at the soil
  const CAN_UP = H(128, 86, -4, { ext: 4 });                    // can lifted beside the face, clear of the ear
  const JUG_SET = H(JUG_AT[0] + JUG_GRIP[0], JUG_AT[1] + JUG_GRIP[1], 0); // hand on the jug handle, jug standing on the bench
  const B_POSE = H(121.4, 95.7, 160);                           // bottle upside down over the jug, nozzle at bench height
  const J_POSE = H(139, 86, 55, { ext: 2 });                    // jug tipped over the pot rim, the dose goes into the soil
  const JUG_LIP_H = [JUG_LIP[0] - JUG_GRIP[0], JUG_LIP[1] - JUG_GRIP[1]];
  const MUG_POSE = H(118, 124, 0);                              // mug at bench height, well below the face
  const CUT_AT = [159, 103.3];                                  // the stalk of the low leaf
  const CUT_POSE = (() => { const r = rot(SHEARS_CUT[0], SHEARS_CUT[1], 10); return H(f(CUT_AT[0] - r[0]), f(CUT_AT[1] - r[1]), 10); })();
  const HEAD_TIP = 10;                                          // Hank tips his head to the hand to take the hat off and put it back
  const HAT_POSE = H(...headPt(HAT_GRIP[0], HAT_GRIP[1], HEAD_TIP), HEAD_TIP, { ext: 12 });
  const WAVE_IN = H(116, 78, 26, { ext: 12 }), WAVE_OUT = H(120, 74, 38, { ext: 12 });
  const BESIDE = H(116, 104, 0, { ext: 4 });                    // on the way up and down the hand stays beside the face
  const UMB_POSE = H(114, 102, -10);                            // shaft right of the crown, canopy above it, its tip inside VIEW
  const FAN_A = H(30, 98, 30), FAN_B = H(32, 96, 4);
  const MARK = [202, 40];                                       // where the problem sits
  const POINT_POSE = H(126, 96, f(Math.atan2(MARK[1] - 96, MARK[0] - 126) / D));
  const SCARF_POSE = H(96, 114, 0);
  const EMIT = { water: tip(W_POSE, CAN_TIP), fert: tip(B_POSE, BOTTLE_MOUTH), dose: tip(J_POSE, JUG_LIP_H) };
  const SOIL_Y = (x) => 107 - 2.6 * Math.sqrt(Math.max(0, 1 - ((x - 172) / 17) ** 2)); // top edge of the soil
  const WATER_FALL = f(SOIL_Y(EMIT.water[0]) - EMIT.water[1]), DOSE_FALL = f(SOIL_Y(EMIT.dose[0]) - EMIT.dose[1]);
  // tipped a little further for the last of the dose; the lip stays where the drops start
  const J_POSE2 = (() => { const r = rot(JUG_LIP_H[0], JUG_LIP_H[1], 62); return H(f(EMIT.dose[0] - r[0]), f(EMIT.dose[1] - r[1]), 62, { ext: 5 }); })();
  // the plant perks up once it got water or food
  const PERK = (s) => [".j-plant", [[0, tf("rotate(0deg) scale(1)")], [s, tf("rotate(0deg) scale(1)")], [s + 250, tf("rotate(-2.5deg) scale(1.035)")],
    [s + 550, tf("rotate(1.5deg) scale(1.02)")], [s + 900, tf("rotate(0deg) scale(1)")]], { easing: EASE }];
  const waves = (s, step, n) => Array.from({ length: n * 2 }, (_, i) => [s + i * step, { L: i % 2 ? FAN_A : FAN_B }]);
  // The gnat climbs out of the soil, loops round the plant, dodges the hat beside Hank's head, then leaves.
  const GNAT = [[0, 172, 104, 0], [150, 172, 100, 1], [400, 163, 88, 1], [650, 152, 78, 1], [900, 160, 66, 1], [1100, 147, 60, 1],
    [1300, 140, 68, 1], [1500, 146, 60, 1], [1700, 137, 55, 1], [1850, 145, 66, 1], [2000, 136, 60, 1], [2150, 147, 53, 1],
    [2300, 139, 63, 1], [2450, 147, 57, 1], [2650, 160, 44, 1], [2900, 194, 26, 1], [3150, 238, 6, 1], [3200, 250, 0, 0]];
  const rain = () => [[0, mvy(0, -8)], [80, mvy(0.9, 2)], [700, mvy(0.9, 124)], [760, mvy(0, 132)], [1000, mvy(0, 132)]];
  const zzz = () => [[0, ts(0, 116, 46, 0.55)], [500, ts(1, 120, 40, 0.7)], [3000, ts(1, 134, 16, 1.05)], [3600, ts(0, 137, 10, 1.1)]];

  // ---- scenes: chores play when the box is fine, state scenes say what the box reports ----
  const SCENES = {
    water: {
      dur: 4400, still: 1900,
      face: [[0, "hello"], [800, "think."], [3100, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0] }],
        [700, { R: CAN_UP, body: [10, -6] }],
        [1050, { R: W_POSE }], [2700, { R: H(129.5, 73.5, 51, { ext: 8 }) }],
        [3100, { R: CAN_UP, body: [10, -6] }],
        [3800, { R: REST, body: [0, 0] }],
      ],
      tracks: [
        show(".p-can", 0, 3800, 120, 200),
        ...[1, 2, 3, 4].map((i) => [`.fx-w${i}`, falls(1050 + i * 100, 400, 4, 260, WATER_FALL)]),
        PERK(3000),
      ],
    },
    prune: {
      dur: 4400, still: 1560,
      face: [[0, "hello"], [600, "think."], [2000, "joy"], [3600, "hello"]],
      keys: [
        [0, { R: REST, body: [0, 0] }],
        [750, { R: H(137, 98, 4), body: [8, -2] }],
        [1150, { R: CUT_POSE }], [1700, { R: CUT_POSE }],
        [2100, { R: H(135, 96, -12) }], [2700, { R: H(128, 100, -20), body: [8, -2] }],
        [3500, { R: REST, body: [0, 0] }],
      ],
      tracks: [
        show(".p-shears", 0, 3500),
        [".blade-a", [[0, tf("rotate(-16deg)")], [1350, tf("rotate(-16deg)")], [1480, tf("rotate(0deg)")], [1850, tf("rotate(0deg)")], [2050, tf("rotate(-16deg)")]]],
        [".blade-b", [[0, tf("rotate(14deg)")], [1350, tf("rotate(14deg)")], [1480, tf("rotate(0deg)")], [1850, tf("rotate(0deg)")], [2050, tf("rotate(14deg)")]]],
        // the low leaf turns yellow while Hank looks at it, is cut, and a fresh green one grows from the stalk
        [".leaf-yel", [[0, op(0)], [150, op(0)], [750, op(1)], [1480, op(1)], [1480, op(0)]]],
        [".leaf-cut", [[0, ts(1, 0, 0, 1)], [1480, ts(1, 0, 0, 1)], [1480, ts(0, 0, 0, 1)], [3300, ts(0, 0, 0, 0.1)], [3300, ts(1, 0, 0, 0.1)], [4200, ts(1, 0, 0, 1)]], { easing: "ease-out" }],
        [".fx-leaf", [[0, mv(0, 0, 0, 0)], [1480, mv(0, 0, 0, 0)], [1480, mv(1, 0, 0, 0)], [2400, mv(1, -6, 30, 20)], [3000, mv(1, -6, 30, 20)], [3400, mv(0, -6, 30, 20)]], { easing: "ease-in" }],
      ],
    },
    feed: {
      // Jug on the bench, dose from the bottle at bench height, then the jug goes over the pot rim, never up to the face.
      dur: 4900, still: 1800,
      face: [[0, "hello"], [450, "think."], [3800, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0] }],
        [550, { R: JUG_SET, body: [4, -1] }], [620, { R: JUG_SET }],     // sets the jug down, lets go at 580
        [880, { R: REST }],                                               // reaches down for the bottle
        [960, { R: { sh: 0, el: 0, ha: 160 } }],                          // turns it cap-down while still behind the bench
        [1300, { R: B_POSE }], [2150, { R: H(121.6, 95.4, 163) }],        // the dose drops into the jug
        [2450, { R: { sh: 0, el: 0, ha: 163 } }], [2520, { R: REST }],    // bottle away, turned back only behind the bench
        [2880, { R: JUG_SET }], [2980, { R: JUG_SET, body: [4, -1] }],    // takes the jug again at 2930
        [3450, { R: J_POSE, body: [10, -6] }], [4000, { R: J_POSE2, body: [10, -6] }], // pours it onto the soil
        [4400, { R: H(126, 120, 10), body: [4, -1] }],
        [4800, { R: REST, body: [0, 0] }],
      ],
      tracks: [
        [".p-jug", [[0, op(0)], [60, op(0)], [160, op(1)], [580, op(1)], [580, op(0)], [2930, op(0)], [2930, op(1)], [4700, op(1)], [4800, op(0)]]],
        [".jug-bench", [[0, op(0)], [580, op(0)], [580, op(1)], [2930, op(1)], [2930, op(0)]]],
        [".p-bottle", [[0, op(0)], [880, op(0)], [930, op(1)], [2450, op(1)], [2520, op(0)]]],
        [".jug-liq", [[0, tf("scale(1, 0)")], [1350, tf("scale(1, 0)")], [2150, tf("scale(1, 1)")], [3500, tf("scale(1, 1)")], [4050, tf("scale(1, 0)")]]],
        ...[1, 2, 3, 4].map((i) => [`.fx-d${i}`, falls(1150 + i * 200, 200, 1, 240, 7)]),
        ...[1, 2, 3, 4].map((i) => [`.fx-j${i}`, falls(3360 + i * 140, 140, 1, 260, DOSE_FALL)]),
        PERK(3850),
      ],
    },
    lens: {
      dur: 3900, still: 1300,
      face: [[0, "hello"], [700, "think"], [2800, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0] }],
        [800, { R: H(136, 98, 0), body: [8, -3] }],
        [1200, { R: H(139, 95, 0) }], [1900, { R: H(133, 113, 0) }], [2500, { R: H(138, 97, -4) }],
        [2900, { R: H(132, 100, 0), body: [8, -3] }],
        // turns the lens down before the hand goes behind the bench, so it never hangs above it alone
        [3250, { R: H(124, 130, 155), body: [4, -1] }],
        [3450, { R: { sh: 0, el: 0, ha: 155 } }],
        [3650, { R: REST, body: [0, 0] }],
      ],
      tracks: [show(".p-lens", 0, 3650, 120, 150)],
    },
    coffee: {
      dur: 3600, still: 1700,
      face: [[0, "hello"]],
      keys: [
        [0, { R: REST }], [650, { R: MUG_POSE }], [1650, { R: H(118.5, 123.2, 3) }], [2650, { R: MUG_POSE }], [3250, { R: REST }],
      ],
      // two short wisps; the last one is gone before the mug goes down
      tracks: [show(".p-mug", 0, 3250), ...[1, 2].map((i) => [`.s${i}`, rise(750 + (i - 1) * 350, 850, 2, 800, -8)])],
    },
    gnat: {
      dur: 4500, still: 1950,
      face: [[0, "hello"], [350, "think."], [800, "worry"], [2900, "joy"], [4300, "hello"]],
      keys: [
        [0, { R: REST, head: 0 }],
        [650, { R: BESIDE }],
        [1150, { R: HAT_POSE, head: HEAD_TIP }], [1250, { R: HAT_POSE, head: HEAD_TIP }],
        [1500, { R: WAVE_IN, head: 0 }],
        [1700, { R: WAVE_OUT }], [1900, { R: WAVE_IN }], [2100, { R: WAVE_OUT }], [2300, { R: WAVE_IN }], [2500, { R: WAVE_OUT }],
        [2800, { R: H(117, 78, 30, { ext: 12 }), head: 0 }],
        [3200, { R: HAT_POSE, head: HEAD_TIP }], [3300, { R: HAT_POSE, head: HEAD_TIP }],
        [3700, { R: BESIDE, head: 0 }],
        [4200, { R: REST }],
      ],
      tracks: [
        // the hat changes from head to hand only while the hand sits still on the brim and the head is tipped the same way
        [".head-hat", [[0, op(1)], [1200, op(1)], [1200, op(0)], [3250, op(0)], [3250, op(1)]]],
        [".hshade", [[0, op(1)], [1200, op(1)], [1200, op(0)], [3250, op(0)], [3250, op(1)]]],
        [".p-hat", [[0, op(0)], [1200, op(0)], [1200, op(1)], [3250, op(1)], [3250, op(0)]]],
        [".fx-gnat", GNAT.map(([t, x, y, o]) => [t, { opacity: o, transform: `translate(${x}px, ${y}px)` }]), { easing: EASE }],
        [".wings", [{ transform: "scale(1, 1)" }, { transform: "scale(1, 0.3)" }, { transform: "scale(1, 1)" }], { kf: true, timing: { duration: 90, iterations: 40 } }],
      ],
    },
    sleep: {
      dur: 1600, still: 1600,
      face: [[0, "hello"], [500, "sleep"]],
      keys: [[0, { hat: [0, 0], head: 0 }], [1200, { hat: [9, -6], head: 3 }]],
      loop: {
        // positive delays: every Z starts small at the mouth level instead of appearing mid-flight
        dur: 3600, still: 2700, face: [[0, "sleep"]],
        keys: [[0, { body: [0, 0] }], [1800, { body: [0, -1.2] }], [3600, { body: [0, 0] }]],
        tracks: [1, 2, 3].map((i) => [`.fx-z${i}`, zzz(), { timing: { delay: (i - 1) * 1200 } }]),
      },
    },
    umbrella: {
      dur: 1600, still: 1600,
      face: [[0, "hello"], [300, "think."]],
      // the umbrella starts lying flat behind the bench and swings up on the right side, never past his face
      keys: [[0, { R: { sh: 0, el: 0, ha: 90 } }], [900, { R: UMB_POSE }]],
      tracks: [
        [".p-umb", [[0, op(0)], [50, op(1)]]],
        [".umb-furl", [[0, op(1)], [900, op(1)], [1150, op(0)]]],
        [".umb-open", [[0, ts(0, 0, 0, "0.12, 0.6")], [900, ts(0, 0, 0, "0.12, 0.6")], [910, ts(1, 0, 0, "0.12, 0.6")], [1250, ts(1, 0, 0, "1, 1")]], { easing: "ease-out" }],
      ],
      loop: {
        dur: 4000, still: 2300, face: [[0, "think."]], keys: [],
        tracks: [
          hold(".p-umb", op(1)), hold(".umb-furl", op(0)), hold(".umb-open", ts(1, 0, 0, "1, 1")),
          ...RAIN_X.map((x, i) => [`.fx-r${i + 1}`, rain(), { timing: { duration: 1000, delay: i * 170 } }]),
        ],
      },
    },
    fan: {
      dur: 2600, still: 1300,
      face: [[0, "hello"], [300, "worry"]],
      keys: [[0, { L: REST }], [350, { L: H(36, 134, 20) }], [650, { L: FAN_A }], ...waves(800, 160, 5), [2600, { L: FAN_A }]],
      tracks: [
        [".p-fan", [[0, op(0)], [100, op(1)]]],
        [".fx-sweat", [[0, mvy(0, 0)], [400, mvy(0, 0)], [600, mvy(1, 0)], [2200, mvy(1, 6)], [2500, mvy(0, 7)]]],
      ],
      loop: {
        dur: 6000, still: 1500, face: [[0, "worry"]],
        keys: [[0, { L: FAN_A }], ...waves(150, 160, 3), [6000, { L: FAN_A }]],
        tracks: [hold(".p-fan", op(1)), [".fx-sweat", [[0, mvy(0, 0)], [1200, mvy(0, 0)], [1400, mvy(1, 0)], [3000, mvy(1, 6)], [3300, mvy(0, 7)]]]],
      },
    },
    scarf: {
      dur: 2600, still: 1250,
      face: [[0, "worry"]],
      keys: [
        ...shiver(0, 80, 9),
        [0, { R: REST }], [600, { R: H(122, 100, 0) }], [1150, { R: SCARF_POSE }], [1400, { R: SCARF_POSE }],
        [1800, { R: H(110, 122, 0) }], [2400, { R: REST }],
      ],
      tracks: [
        [".p-scarf", [[0, op(0)], [80, op(1)], [1150, op(1)], [1350, op(0)]]],
        [".p-scarfw", [[0, op(0)], [1150, op(0)], [1350, op(1)]]],
      ],
      loop: {
        // a slow snowflake on each side says cold; no breath cloud, that would read as smoke
        dur: 6000, still: 3000, face: [[0, "worry"]],
        keys: [...shiver(200, 80, 7), [6000, { body: [0, 0] }]],
        tracks: [
          hold(".p-scarfw", op(1)),
          [".fx-snow1", [[0, mv(0, 30, 12, 0)], [400, mv(1, 29, 16, 20)], [3200, mv(1, 25, 40, 120)], [3600, mv(0, 24, 43, 140)]], { easing: "ease-in-out" }],
          [".fx-snow2", [[0, mv(0, 136, 16, 0)], [2400, mv(0, 136, 16, 0)], [2800, mv(1, 137, 20, 20)], [5400, mv(1, 140, 42, 110)], [5800, mv(0, 141, 45, 130)]], { easing: "ease-in-out" }],
        ],
      },
    },
    point: {
      dur: 1400, still: 1400,
      face: [[0, "alert"]],
      keys: [[0, { R: REST, body: [0, 0] }], [700, { R: POINT_POSE, body: [5, -2] }]],
      tracks: [
        [".p-point", [[0, op(0)], [100, op(1)]]],
        [".fx-alarm", [[0, ts(0, MARK[0], MARK[1], 0.6)], [500, ts(0, MARK[0], MARK[1], 0.6)], [800, ts(1, MARK[0], MARK[1], 1)]], { easing: "ease-out" }],
        hold(".ping", op(0)),   // the ring only pulses in the loop
      ],
      loop: {
        dur: 2400, still: 600, face: [[0, "alert"]], keys: [],
        tracks: [
          hold(".p-point", op(1)), hold(".fx-alarm", ts(1, MARK[0], MARK[1], 1)),
          [".ping", [[0, Object.assign(op(0.8), tf("scale(1)"))], [1500, Object.assign(op(0), tf("scale(2)"))], [2400, Object.assign(op(0), tf("scale(2)"))]]],
        ],
      },
    },
  };

  // ---- after the harvest: one moment per step (web/workspace.js), played once; a plant's card shows its still frame ----
  const HARVEST_CUT = H(f(171.6 - SHEARS_CUT[0]), 101.5, 0, { ext: 6 });  // the shears' tip on the main stem above the soil
  const LIFT = H(136, 84, 8, { ext: 12 }), LIFT2 = H(138, 80, 14, { ext: 12 });  // the harvest held up, clear of the hat
  const LENS_DRY = H(142.5, 81.5, 0, { ext: 10 });              // the lens on the lowest bud of the first branch
  const LID_POSE = H(JAR_LID[0], JAR_LID[1], 0, { ext: 8 }), LID_UP = H(140, 80, -12, { ext: 12 });
  const LABEL_POSE = H(JAR_LABEL[0] - 11, JAR_LABEL[1], 0);
  const CARD_OVER = H(150, 100, 0, { ext: 6 }), CARD_IN = H(150, 120, 0, { ext: 2 });
  // pot, plant and lamp go once the plant is harvested: drying and curing happen in the dark
  const AFTER = [hold(".pot", op(0)), hold(".glow", op(0)), hold(".lamp-on", op(0))];
  const SWAY = (sign) => [[0, tf("rotate(0deg)")], [1000, tf(`rotate(${3 * sign}deg)`)], [2100, tf(`rotate(${-2.5 * sign}deg)`)],
    [3200, tf(`rotate(${2 * sign}deg)`)], [4200, tf("rotate(0deg)")]];
  const twinkle = (i, x, y, start) => [`.fx-spark${i}`, [[0, ts(0, x, y, 0.3)], [start, ts(0, x, y, 0.3)], [start + 250, ts(1, x, y, 1.1)],
    [start + 550, ts(0, x, y, 0.3)]], { easing: EASE }];
  Object.assign(SCENES, {
    harvest: {
      // the shears cut the main stem at the soil, the plant comes out of the pot, Hank holds the harvest up
      dur: 3800, still: 3000,
      face: [[0, "hello"], [500, "think."], [1700, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0] }],
        [700, { R: H(137, 100, 0), body: [8, -2] }],
        [1100, { R: HARVEST_CUT, body: [10, -4] }], [1600, { R: HARVEST_CUT }],
        [2300, { R: LIFT, body: [6, -3] }], [2800, { R: LIFT2 }], [3300, { R: LIFT }], [3800, { R: LIFT }],
      ],
      tracks: [
        [".p-shears", [[0, op(0)], [120, op(1)], [1550, op(1)], [1650, op(0)]]],
        [".blade-a", [[0, tf("rotate(-16deg)")], [1300, tf("rotate(-16deg)")], [1430, tf("rotate(0deg)")]]],
        [".blade-b", [[0, tf("rotate(14deg)")], [1300, tf("rotate(14deg)")], [1430, tf("rotate(0deg)")]]],
        [".p-branch", [[0, op(0)], [1550, op(0)], [1650, op(1)]]],
        [".j-plant", [[0, mv(1, 0, 0, 0)], [1500, mv(1, 0, 0, 0)], [1750, mv(0, -6, -10, -6)]], { easing: "ease-in" }],
        twinkle(1, 166, 52, 2500), twinkle(2, 118, 40, 2700),
      ],
    },
    dry: {
      // head down on the line in the dark; Hank checks the lowest bud with the lens and is happy with it
      dur: 4200, still: 1900,
      face: [[0, "hello"], [450, "think."], [2700, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0] }],
        [700, { R: H(132, 98, 0), body: [8, -4] }],
        [1150, { R: LENS_DRY, body: [10, -6] }], [1900, { R: H(LENS_DRY.x + 3, LENS_DRY.y - 3, 0, { ext: 10 }) }],
        [2500, { R: LENS_DRY }],
        [2900, { R: H(132, 100, 0), body: [8, -3] }],
        // turns the lens down before the hand goes behind the bench, as in the lens chore
        [3250, { R: H(124, 130, 155), body: [4, -1] }], [3450, { R: { sh: 0, el: 0, ha: 155 } }],
        [3650, { R: REST, body: [0, 0] }],
      ],
      tracks: [...AFTER, hold(".set-line", op(1)), show(".p-lens", 0, 3650, 120, 150),
        [".sway1", SWAY(1), { easing: EASE }], [".sway2", SWAY(-1), { easing: EASE }]],
    },
    cure: {
      // the jar is opened for a moment, a little air comes out, Hank smells and closes it again
      dur: 4800, still: 2300,
      face: [[0, "hello"], [500, "think."], [1900, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0], head: 0 }],
        [650, { R: H(140, 100, 0), body: [8, -3] }],
        [1000, { R: LID_POSE, body: [10, -4] }], [1150, { R: LID_POSE }],
        [1600, { R: LID_UP, head: 7 }], [2700, { R: LID_UP, head: 7 }],
        [3100, { R: LID_POSE, head: 0 }], [3250, { R: LID_POSE }],
        [3700, { R: H(132, 104, 0), body: [6, -2] }],
        [4300, { R: REST, body: [0, 0] }],
      ],
      tracks: [...AFTER, hold(".set-jar", op(1)), hold(".jar-label", op(0)),
        // lid on the jar and lid in the hand swap while the hand sits still on it
        [".jar-lid", [[0, op(1)], [1100, op(1)], [1100, op(0)], [3200, op(0)], [3200, op(1)]]],
        [".p-lid", [[0, op(0)], [1100, op(0)], [1100, op(1)], [3200, op(1)], [3200, op(0)]]],
        ...[1, 2, 3].map((i) => [`.fx-puff${i}`, rise(1500 + (i - 1) * 300, 900, 1, 1100, -14)])],
    },
    ready: {
      // the label goes on the jar, it sparkles, Hank raises his hat to it
      dur: 6000, still: 2000,
      face: [[0, "hello"], [450, "think."], [1450, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0], head: 0 }],
        [600, { R: H(130, 116, 0), body: [6, -2] }],
        [1000, { R: LABEL_POSE, body: [8, -2] }], [1450, { R: LABEL_POSE }],
        [1800, { R: H(128, 112, 0), body: [4, -1] }],
        [2400, { R: BESIDE, body: [0, 0] }],
        [2900, { R: HAT_POSE, head: HEAD_TIP }], [3000, { R: HAT_POSE, head: HEAD_TIP }],
        [3400, { R: WAVE_IN, head: 0 }], [3800, { R: WAVE_OUT }], [4200, { R: WAVE_IN }],
        [4600, { R: HAT_POSE, head: HEAD_TIP }], [4700, { R: HAT_POSE, head: HEAD_TIP }],
        [5100, { R: BESIDE, head: 0 }],
        [5600, { R: REST }],
      ],
      tracks: [...AFTER, hold(".set-jar", op(1)),
        [".jar-label", [[0, op(0)], [1400, op(0)], [1400, op(1)]]],
        [".p-label", [[0, op(0)], [150, op(0)], [300, op(1)], [1400, op(1)], [1400, op(0)]]],
        // the hat swaps as in the gnat chore: only while the hand sits still on the brim
        [".head-hat", [[0, op(1)], [2950, op(1)], [2950, op(0)], [4650, op(0)], [4650, op(1)]]],
        [".hshade", [[0, op(1)], [2950, op(1)], [2950, op(0)], [4650, op(0)], [4650, op(1)]]],
        [".p-hat", [[0, op(0)], [2950, op(0)], [2950, op(1)], [4650, op(1)], [4650, op(0)]]],
        twinkle(1, 167, 108, 1450), twinkle(2, 131, 100, 1650), twinkle(3, 163, 92, 1850)],
    },
    archive: {
      // the record card goes into the box, the flap closes, Hank pats the lid and waves goodbye
      dur: 5600, still: 2600,
      face: [[0, "hello"], [450, "think."], [2300, "joy"]],
      keys: [
        [0, { R: REST, body: [0, 0] }],
        [650, { R: H(132, 100, 0), body: [6, -3] }],
        [1050, { R: CARD_OVER, body: [8, -3] }], [1300, { R: CARD_OVER }],
        [1700, { R: CARD_IN }], [1900, { R: CARD_IN }],
        [2300, { R: CARD_OVER }],
        [2650, { R: H(150, 102, 0, { ext: 6 }) }], [2800, { R: H(150, 98, 0, { ext: 6 }) }], [2950, { R: H(150, 102, 0, { ext: 6 }) }],
        [3400, { R: BESIDE, body: [0, 0] }],
        [3700, { R: WAVE_IN }], [3900, { R: WAVE_OUT }], [4100, { R: WAVE_IN }], [4300, { R: WAVE_OUT }],
        [4700, { R: BESIDE }],
        [5300, { R: REST }],
      ],
      tracks: [...AFTER, hold(".set-box", op(1)), hold(".set-boxback", op(1)),
        [".p-card", [[0, op(0)], [150, op(0)], [300, op(1)], [1850, op(1)], [1950, op(0)]]],
        [".box-flap", [[0, tf("scale(1, 1)")], [2100, tf("scale(1, 1)")], [2450, tf("scale(1, 0)")]]],
        [".set-shut", [[0, op(0)], [2400, op(0)], [2450, op(1)]]]],
    },
  });
  // between two grows: the box is empty, Hank sprays the wall, wipes it down, and it sparkles
  // (effects() reads these when a stage is drawn) the spray bottle's nozzle in hand coordinates, the pose that sprays, and where the mist drifts from there
  const SPRAY_TIP = [13, -6.5], SPRAY_POSE = H(126, 98, 0);
  const EMIT_SPRAY = (() => [SPRAY_POSE.x + SPRAY_TIP[0], SPRAY_POSE.y + SPRAY_TIP[1]])();
  const MIST = [[20, -7], [26, -2], [23, 4], [30, 1]];
  const WIPE = [H(144, 86, 0, { ext: 8 }), H(150, 96, 0, { ext: 8 }), H(140, 100, 0, { ext: 6 }), H(148, 110, 0, { ext: 6 }), H(140, 114, 0, { ext: 4 })];
  const mist = (vec, i) => {
    const k = [[0, mv(0, 0, 0, 0)]];
    for (const s of [800, 1200, 1600]) k.push([s + i * 40, mv(0, 0, 0, 0)], [s + i * 40 + 60, mv(0.9, f(vec[0] * 0.15), f(vec[1] * 0.15), 0)], [s + i * 40 + 340, mv(0, vec[0], vec[1], 0)]);  // one burst ends before the next
    return k;
  };
  SCENES.clean = {
    dur: 5600, still: 1300,
    face: [[0, "hello"], [400, "think."], [3900, "joy"]],
    keys: [
      [0, { R: REST, body: [0, 0] }],
      [650, { R: SPRAY_POSE, body: [6, -3] }],
      [1000, { R: H(126, 98, -3) }], [1150, { R: SPRAY_POSE }], [1400, { R: H(126, 98, -3) }], [1550, { R: SPRAY_POSE }], [1750, { R: SPRAY_POSE }],
      // bottle down behind the bench, the cloth comes up
      [2150, { R: REST, body: [2, 0] }],
      [2600, { R: WIPE[0], body: [10, -6] }], [2900, { R: WIPE[1] }], [3200, { R: WIPE[2] }], [3500, { R: WIPE[3] }], [3800, { R: WIPE[4] }],
      [4300, { R: H(130, 110, 0), body: [4, -2] }],
      [4900, { R: REST, body: [0, 0] }],
    ],
    tracks: [...AFTER, show(".p-spray", 0, 2150), show(".p-cloth", 2150, 4900),
      ...MIST.map((vec, i) => [`.fx-m${i + 1}`, mist(vec, i), { easing: "ease-out" }]),
      twinkle(1, 152, 86, 3000), twinkle(2, 157, 104, 3400), twinkle(3, 146, 116, 3800)],
  };
  const MOMENTS = ["harvest", "dry", "cure", "ready", "archive", "clean"];

  // ---- engine: one scene at a time per stage; a new wish first brings the old scene back to rest ----
  const RM = matchMedia("(prefers-reduced-motion: reduce)");
  const CHORES = ["water", "prune", "feed", "lens", "coffee", "gnat"];
  // The server's scene ids (vgrow/hank/nudges.py scene()); "still" is the pose without a scene (mode "warnings").
  // face is the rest face; null takes the face hank.js passes (it follows the traffic light).
  const STATES = {
    still: { scene: null, face: null },
    idle: { scene: null, face: null, bob: true },
    sleep: { scene: "sleep", face: "sleep" },
    umbrella: { scene: "umbrella", face: "think" },
    fan: { scene: "fan", face: "worry" },
    scarf: { scene: "scarf", face: "worry" },
    point: { scene: "point", face: "alert" },
  };
  const FIRST_MS = 20000;      // the first chore after the drawer opens
  const GAP_MS = 240000;       // then at least four minutes between two chores
  const JITTER_MS = 60000;     // plus up to one minute, so it never feels like a clock
  // The small stage is cropped to Hank, plant and lamp (no bench, no empty margin), as in the prototype's nudge.
  const VIEW = "12 4 204 134";
  const PARSER = new DOMParser();
  const LIVE = [];

  function compile(ph, init) {
    const c = channels(ph.keys || [], ph.dur, init);
    return { dur: ph.dur, end: c.end, tracks: c.tracks.concat(faceTracks(ph.face, ph.dur), (ph.tracks || []).map((t) => raw(t, ph.dur))) };
  }
  const freeze = (anims, t) => anims.forEach((a) => { a.pause(); a.currentTime = t; });

  function stageNode() {
    const svg = document.importNode(PARSER.parseFromString(stageSvg(EMIT, "", VIEW), "text/html").body.firstElementChild, true);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    return svg;
  }

  function mount(host, opts) {
    const o = opts || {};
    const size = o.size === "head" ? "head" : "mini";
    const rig = stageNode();
    host.replaceChildren(rig);
    host.classList.add("hk-sc", `hk-sc-${size}`);
    const E = { state: "still", face: "hello", cur: null, idle: 0, last: "", busy: false, next: null,
      chores: false, active: true, stopped: false, lastEnd: 0 };
    const running = () => E.active && !E.stopped && !document.hidden;
    const restFace = () => STATES[E.state].face || E.face;
    const target = () => STATES[E.state].scene;

    function tell(name) {
      if (typeof o.onScene !== "function") return;
      try { o.onScene(name); } catch (_) { /* a label is never worth a broken stage */ }
    }

    // Only transform and opacity, all on real elements, so the browser can run them off the main thread.
    function animateAll(tracks, timing) {
      const anims = [];
      for (const [sel, kf, t] of tracks) {
        const tm = Object.assign({}, timing, t && t.timing);
        rig.querySelectorAll(sel).forEach((node) => anims.push(node.animate(kf, tm)));
      }
      if (!running()) anims.forEach((a) => a.pause());
      return anims;
    }

    function start(name) {
      const sc = SCENES[name];
      clearTimeout(E.idle);
      rig.classList.remove("rest");
      const enter = compile(sc, INIT);
      const cur = { name, sc, enter, loop: null, anims: [], frozen: false, timer: 0 };
      E.cur = cur;
      if (CHORES.includes(name)) E.last = name;
      tell(name);
      if (RM.matches) {  // still pose, no movement
        cur.frozen = true;
        if (sc.loop) {
          cur.loop = compile(sc.loop, enter.end);
          cur.anims = animateAll(cur.loop.tracks, { duration: cur.loop.dur, iterations: Infinity, fill: "both" });
          freeze(cur.anims, sc.loop.still);
        } else {
          cur.anims = animateAll(enter.tracks, { duration: enter.dur, fill: "both" });
          freeze(cur.anims, sc.still);
          cur.timer = setTimeout(() => done(cur), sc.dur);
        }
        return;
      }
      cur.anims = animateAll(enter.tracks, { duration: enter.dur, fill: sc.loop ? "forwards" : "none" });
      // every scene moves the body over its whole length, so the end of that animation is the end of the scene
      const body = cur.anims.find((a) => a.effect.target.matches(".j-body"));
      body.finished.then(() => { if (E.cur === cur) (sc.loop ? startLoop(cur) : done(cur)); }, () => {});
    }
    // State scenes: after the entrance Hank holds the pose with a small repeat, as long as the state lasts, or
    // o.loops times and then the loop's still frame (the same picture reduced motion shows).
    const LOOPS = Number.isInteger(o.loops) && o.loops > 0 ? o.loops : Infinity;
    function holdStill(cur) {
      const old = cur.anims;
      cur.anims = animateAll(cur.loop.tracks, { duration: cur.loop.dur, iterations: Infinity, fill: "both" });
      freeze(cur.anims, cur.sc.loop.still);
      old.forEach((a) => a.cancel());
      cur.frozen = true;
    }
    function startLoop(cur) {
      cur.loop = compile(cur.sc.loop, cur.enter.end);
      const old = cur.anims;
      cur.anims = animateAll(cur.loop.tracks, { duration: cur.loop.dur, iterations: LOOPS, fill: "both" });
      old.forEach((a) => a.cancel());
      if (LOOPS === Infinity) return;
      const mine = cur.anims;
      Promise.all(mine.map((a) => a.finished)).then(() => {
        if (E.cur === cur && cur.anims === mine && !cur.frozen) holdStill(cur);
      }, () => {});
    }
    function done(cur) {
      if (E.cur !== cur) return;
      cur.anims.forEach((a) => a.cancel());
      E.cur = null;
      if (CHORES.includes(cur.name)) E.lastEnd = Date.now();
      rest();
    }
    // Bring every moved part back to rest in 0.4 s; props stay in the hand and fade on the way down.
    // The two hats swap at once (never two half hats), and the face shows the box state at once (no smile between warnings).
    const SWAP_NOW = ".p-hat, .head-hat, .hshade";
    function settle() {
      const cur = E.cur;
      E.cur = null;
      clearTimeout(cur.timer);
      if (CHORES.includes(cur.name)) E.lastEnd = Date.now();
      rig.dataset.face = restFace();
      if (RM.matches || !running()) { cur.anims.forEach((a) => a.cancel()); run(E.next); return; }
      const els = [];
      cur.anims.forEach((a) => { const n = a.effect && a.effect.target; if (n && !els.includes(n) && !n.matches(".f, .x")) els.push(n); });
      const from = els.map((n) => { const cs = getComputedStyle(n); return [cs.transform, cs.opacity]; });
      cur.anims.forEach((a) => a.cancel());
      // read every resting value in one pass, then start the animations: one style pass instead of one per element
      const to = els.map((n, i) => { const cs = getComputedStyle(n); return [n.matches(".fx") ? from[i][0] : cs.transform, cs.opacity]; });
      E.busy = true;
      const back = els.map((n, i) => n.animate([{ transform: from[i][0], opacity: from[i][1] }, { transform: to[i][0], opacity: to[i][1] }],
        { duration: n.matches(SWAP_NOW) ? 0 : n.matches(".p") ? 180 : 380, easing: EASE }));
      setTimeout(() => { back.forEach((a) => a.cancel()); E.busy = false; run(E.next); }, 400);
    }
    function run(name) { E.next = null; if (name && !E.stopped) start(name); else rest(); }
    function go(name) {
      E.next = name || null;
      if (E.busy) return;  // the running settle picks up the latest wish
      if (E.cur) settle(); else run(E.next);
    }

    function rest() {
      E.cur = null;
      rig.dataset.face = restFace();
      rig.classList.toggle("rest", !!STATES[E.state].bob && !E.stopped && !RM.matches);
      tell(null);
      scheduleIdle();
    }
    function pick() { const pool = CHORES.filter((c) => c !== E.last); return pool[Math.floor(Math.random() * pool.length)]; }
    function scheduleIdle() {
      clearTimeout(E.idle);
      if (!E.chores || E.state !== "idle" || E.cur || E.busy || RM.matches || !running()) return;
      const wait = Math.max(FIRST_MS, E.lastEnd + GAP_MS - Date.now()) + Math.random() * JITTER_MS;
      E.idle = setTimeout(() => {
        if (!E.cur && !E.busy && E.chores && E.state === "idle" && !RM.matches && running()) go(pick());
      }, wait);
    }

    // Pause and resume with the drawer and the tab; a frozen still (reduced motion) stays where it is.
    function refresh() {
      const on = running();
      host.classList.toggle("hk-sc-paused", !on);
      const cur = E.cur;
      if (cur && !cur.frozen) {
        for (const a of cur.anims) {
          if (on && a.playState === "paused") a.play();
          else if (!on && a.playState === "running") a.pause();
        }
      }
      if (on) scheduleIdle(); else clearTimeout(E.idle);
    }

    const stage = {
      element: host,
      setState(id, face) {
        const next = id !== "still" && Object.prototype.hasOwnProperty.call(STATES, id) ? id : "still";
        E.face = EXPRS.includes(face) ? face : "hello";
        const woke = E.stopped;
        E.stopped = false;
        if (next === E.state && !woke) {
          if (!E.cur && !E.busy) rig.dataset.face = restFace();
          return;
        }
        E.state = next;
        rig.dataset.state = next;
        refresh();
        go(target());
      },
      allowChores(on) {
        E.chores = !!on;
        if (E.chores) scheduleIdle(); else clearTimeout(E.idle);
      },
      playChore(name) {
        if (E.state !== "idle" || E.stopped || E.cur || E.busy) return null;
        const n = CHORES.includes(name) ? name : pick();
        go(n);
        return n;
      },
      setActive(on) {
        E.active = !!on;
        refresh();
      },
      stop() {
        E.stopped = true;
        E.chores = false;
        E.next = null;
        clearTimeout(E.idle);
        const cur = E.cur;
        if (cur) { clearTimeout(cur.timer); cur.anims.forEach((a) => a.cancel()); E.cur = null; }
        E.state = "still";
        rig.dataset.state = "still";
        rig.dataset.face = E.face;
        rig.classList.remove("rest");
        tell(null);
        refresh();
      },
      moment(name, still) {
        if (!MOMENTS.includes(name)) return false;
        E.chores = false;
        E.next = null;
        clearTimeout(E.idle);
        if (E.cur) { clearTimeout(E.cur.timer); E.cur.anims.forEach((a) => a.cancel()); }
        E.stopped = false;
        E.state = "still";
        rig.dataset.state = "still";
        rig.classList.remove("rest");
        const sc = SCENES[name];
        const cur = { name, sc, enter: compile(sc, INIT), loop: null, anims: [], frozen: false, timer: 0 };
        E.cur = cur;
        stage.transient = true;
        tell(name);
        cur.anims = animateAll(cur.enter.tracks, { duration: cur.enter.dur, fill: "forwards" });
        if (still || RM.matches) { freeze(cur.anims, sc.still); cur.frozen = true; }
        return true;
      },
      // the reduced-motion switch of the system: show the current wish again, still or moving
      motion() { if (!(E.cur && MOMENTS.includes(E.cur.name))) go(E.cur ? (CHORES.includes(E.cur.name) ? null : E.cur.name) : target()); },
      refresh,
      // for the render check only (see below): freeze the running scene at a time
      seek(ms) {
        const cur = E.cur;
        if (!cur) return false;
        clearTimeout(cur.timer);
        if (cur.sc.loop && ms >= cur.sc.dur) { if (!cur.loop) startLoop(cur); freeze(cur.anims, ms - cur.sc.dur); }
        else freeze(cur.anims, ms);
        cur.frozen = true;
        return true;
      },
      now: () => (E.cur ? E.cur.name : null),
    };
    rig.dataset.state = "still";
    rig.dataset.face = E.face;
    // the plant cards and dialogs after the harvest come and go: drop their stages once they left the page
    for (let i = LIVE.length - 1; i >= 0; i--) if (LIVE[i].transient && !LIVE[i].element.isConnected) LIVE.splice(i, 1);
    LIVE.push(stage);
    return stage;
  }

  document.addEventListener("visibilitychange", () => { for (const s of LIVE) s.refresh(); });
  RM.addEventListener("change", () => { for (const s of LIVE) s.motion(); });

  const api = { mount, CHORES: CHORES.slice() };
  // Hooks for the render check only (play a chore, freeze a scene at a time). They exist only when the page is
  // opened with #hank-test, so the cockpit has no console handle on the stages otherwise.
  if (location.hash === "#hank-test") api.test = { stages: () => LIVE.slice() };
  window.HankScenes = api;
})();
