// Preview only: replaces the server API with example values in the page itself.
// Same base values as vgrow/demo.py (upstream API examples), history is a made-up day curve.
// Like the server, it answers in the page language: label_en is the English twin of label.
const MOCK = {
  light: { level: 75, mode: 0, in_plan: false, spectrum: "bloom", spectrum_writable: true },
  cfan: 5, nw: false, dfan: 4,
  ports: { usb1: false, usb2: true },
  pending: {},
  recSince: null,
};
const MOCK_RULES = [
  { label: "Zu kalt (Fühler)", label_en: "Too cold (probe)", metric: "probe_temp", op: "<", limit: 15, for_minutes: 10 },
  { label: "Zu warm (Fühler)", label_en: "Too warm (probe)", metric: "probe_temp", op: ">", limit: 30, for_minutes: 10 },
  { label: "Zu feucht (Fühler)", label_en: "Too humid (probe)", metric: "probe_humidity", op: ">", limit: 70, for_minutes: 30 },
  { label: "Wasser niedrig", label_en: "Water low", metric: "water_level", op: "≤", limit: 25, for_minutes: 5 },
  { label: "Box offline", label_en: "Box offline", metric: "offline", op: null, limit: null, for_minutes: 10 },
];

function mockLocalize(rule) {  // what vgrow/i18n.py localize() does: k_en wins in English, no *_en key is left
  const { label_en: labelEn, ...rest } = rule;
  return LANG === "en" ? { ...rest, label: labelEn } : rest;
}

function mockClimate(t) {
  const day = Math.sin((t / 86400) * 2 * Math.PI);
  const r = (v, d = 2) => Math.round(v * 10 ** d) / 10 ** d;
  return { temp: r(20.04 + 2.5 * day), humidity: r(55.08 - 6 * day), vpd: r(1.05 + 0.2 * day),
           probe_temp: r(18.5 + 2 * day), probe_humidity: r(62 - 5 * day), probe_vpd: r(0.8 + 0.15 * day),
           water_level: 75, water_temp: 22.26 };
}

function mockConfirm(field) {
  MOCK.pending[field] = { status: "pending" };
  setTimeout(() => { MOCK.pending[field] = { status: "ok" }; }, 1200);
  setTimeout(() => { delete MOCK.pending[field]; }, 8000);
}

async function api(path, body) {
  await new Promise((r) => setTimeout(r, 120));
  if (path === "/api/state") {
    return {
      demo: true, device: "VGrow Smart Grow Box", online: true, mqtt: true,
      shadow_age: 3, sensors_age: 40, climate: mockClimate(Date.now() / 1000),
      light: { ...MOCK.light },
      cfan: { step: MOCK.nw ? null : MOCK.cfan, natural_wind: MOCK.nw, mode: 0 },
      dfan: { step: MOCK.dfan, mode: 0 },
      ports: Object.fromEntries(Object.entries(MOCK.ports).map(([k, v]) =>
        [k, { on: v, mode: 0, in_plan: 0, accessory: null, can_write: true }])),
      plan: null, pending: { ...MOCK.pending }, error: null,
      alerts: MOCK_RULES.map((r) => ({ ...mockLocalize(r), active: false })), ntfy: null,
      camera: { configured: true, name: "GrowCam C4", ip: null, error: null, max_minutes: 120,
                recording: MOCK.recSince ? { file: "beispiel.mp4", seconds: Math.round((Date.now() - MOCK.recSince) / 1000), ended: false } : null },
      lang: LANG, server_lang: LANG,
    };
  }
  if (path.startsWith("/api/history")) {
    const h = Number(new URLSearchParams(path.split("?")[1]).get("hours")) || 24;
    const end = Math.floor(Date.now() / 1000), step = Math.max(60, Math.floor((h * 3600) / 720));
    const rows = [];
    for (let t = end - h * 3600; t < end; t += step) {
      const c = mockClimate(t);
      rows.push({ time: t, bTemp: c.temp, bHumi: c.humidity, bVpd: c.vpd, pTemp: c.probe_temp, pHumi: c.probe_humidity,
                  pVpd: c.probe_vpd, waterLv: c.water_level, waterT: c.water_temp });
    }
    return { rows };
  }
  if (path === "/api/cmd") {
    const a = body.action;
    if (a === "light") { MOCK.light.level = body.level <= 0 ? 0 : Math.max(25, Math.min(100, body.level)); mockConfirm("light.level"); }
    else if (a === "spectrum") { MOCK.light.spectrum = body.name; mockConfirm("light.spec"); }
    else if (a === "cfan") { MOCK.nw = !!body.natural_wind; if (!MOCK.nw) MOCK.cfan = body.step; mockConfirm("cFan.level"); }
    else if (a === "dfan") { MOCK.dfan = body.step; mockConfirm("dFan.level"); }
    else if (a === "port") { MOCK.ports[body.port] = !!body.on; mockConfirm(`port.${body.port}`); }
    else if (a.startsWith("record_")) throw new Error(t("preview.noRecording"));
    else if (a === "test_push") throw new Error(t("preview.noPush"));
    return { ok: true };  // set_lang included: the preview has no server language to keep
  }
  if (path.split('?')[0] === "/api/recordings") return { recordings: [] };
  if (path === "/api/light") return { demo: true, light: MOCK.light, history: { readings: 0, segments: [] } };
  if (path.startsWith("/api/protocol")) return { box: "box-1", started_at: Date.now()/1000, updated_at: Date.now()/1000,
    sensor_at: Date.now()/1000, fresh: true, issues: [], tasks: [], totals: { active: 0, working: 0, blocked: 0 }, next: null };
  if (path.startsWith("/api/hank/")) return mockHank(path, body || {});
  // the demo's fixed news list (vgrow/server.py demo_news()), put in by scripts/build_preview.py; without it the
  // News entry simply stays hidden, as on a server without the route
  if (path === "/api/news" && typeof PREVIEW_NEWS === "object") return PREVIEW_NEWS[LANG] || PREVIEW_NEWS.de;
  throw new Error(`preview has no ${path}`);
}

// ---------- Hank (preview only) ----------
// Answers in the shape of the real Hank API (vgrow/hank/api.py and core.py, spec Anhang B; tests/test_hank_web.py
// compares the key sets with the real server's). The facts are the card texts the server would use
// (kartei/stages/autoflower.json early_flower, kartei/nutrients/biobizz-2026.json, plant.json); the fixed replies are
// the server's catalog (vgrow/hank/texts.py). The routing is a few word patterns, nothing like the real router.
// window.HANK_PREVIEW (set by scripts/hank_screens.py) can pin the mode, the light, the humidity and a slow job.
// amount_unit: the unit of the fertilizer that doses (feeding.view amount_unit, feeding v2): "ml" for BioBizz, "g"
// for VIVOSUN Base A + B (grams with half spoons); the preview answers a feeding question in that unit, never in ml
// after grams.
const HANK_MOCK = Object.assign({ mode: "on", light: null, hum: null, slow: false, hint: false, elapsedOffset: 0,
  amount_unit: "ml" }, window.HANK_PREVIEW || {});
HANK_MOCK.jobs = {};
HANK_MOCK.ack = 0;
let hankMockSeq = 0;

const HANK_SRC = {
  S1: { title: "VIVOSUN, VGrow Smart Grow Box Handbuch (Box-spezifisch)", url: "https://vivosun.com/support/guide/vgrow-smart-grow-box.md" },
  S5: { title: "VIVOSUN, Temperatur und Luftfeuchte je Phase", url: "https://vivosun.com/growing_guide/temperature-and-humidity.md" },
  S7: { title: "VIVOSUN, Lampenabstand", url: "https://vivosun.com/growing_guide/how-far-away-should-lights-be.md" },
  S17: { title: "Corredor-Perilla 2025, Luftfeuchte und Ertrag (peer-reviewed)", url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC12666426/" },
  N1: { de: "BioBizz, Düngeschema 2026 (PDF, deutsch)", en: "BioBizz, nutrient schedule 2026 (PDF, German)", url: "https://biobizz.com/downloads/biobizz-nutrient-schedule-2026-de.pdf" },
  V1: { de: "VIVOSUN Nutrients Base A und Base B, Etikett der beiden Flaschen (6 Fotos, die du am 23.10.2026 gemacht hast)", en: "VIVOSUN Nutrients Base A and Base B, label of both bottles (6 photos you took on Oct 23, 2026)", url: null },
  N5: { de: "VIVOSUN, was Autoflower-Samen sind", en: "VIVOSUN, what autoflowering seeds are", url: "https://vivosun.com/en-CA/growing_guide/what-are-autoflowering-seeds/" },
  cockpit: { de: "VGrow-Cockpit, eigene Regel", en: "VGrow cockpit, own rule", url: null },
};
function hankSrc(id, lang) {
  const s = HANK_SRC[id];
  return { id, title: s.title || s[lang], url: s.url };
}

const HANK_CHIPS = {
  de: ["Ist die Luftfeuchte gerade okay?", "Welche Werte brauche ich in der frühen Blüte?", "Wie viel Dünger gebe ich diese Woche?", "Warum ist der Befeuchter aus?"],
  en: ["Is the humidity okay right now?", "Which values do I need in early flower?", "How much fertilizer this week?", "Why is the humidifier off?"],
};
const HANK_CHIP_ROUTES = ["offtopic", "greet", "empty"];
const HANK_FIXED = {
  offtopic: { de: "Da bin ich der Falsche, ich bin ein einfacher Farmer. Ich kenne mich mit dem Anbau aus: Pflanzen, Klima, Licht, Düngen und deiner Box. Frag mich zum Beispiel:",
              en: "Wrong guy for that, I am a simple farmer. I know growing: plants, climate, light, feeding and your box. Ask me, for example:" },
  health: { de: "Dazu sage ich nichts. Ich bin Farmer und kein Arzt. Bei gesundheitlichen Fragen geh bitte zu einer Ärztin oder einem Arzt. Beim Anbau helfe ich dir gern.",
            en: "I will not say anything on that. I am a farmer, not a doctor. For health questions, please see a doctor. I am happy to help with growing." },
  health_emergency: { de: "Das klingt nach einem Notfall. Ruf sofort den Giftnotruf deiner Region an. Bei Atemnot oder Bewusstlosigkeit ruf den Notruf 112. Bleib bei der betroffenen Person oder dem Tier und hol dir ärztlichen oder tierärztlichen Rat.",
                      en: "That sounds like an emergency. Call your regional poison control center right now. If there is trouble breathing or loss of consciousness, call the emergency number 112. Stay with the person or the animal and get medical or veterinary advice." },
  sale: { de: "Da mache ich nicht mit. Nach dem KCanG ist es nicht erlaubt, Cannabis aus dem Eigenanbau zu verkaufen oder weiterzugeben. Beim Anbau selbst helfe ich dir gern.",
          en: "I will not help with that. Under the KCanG it is not allowed to sell or pass on cannabis from home growing. I am happy to help with the growing itself." },
  inject: { de: "Ich bleibe Hank, der Farmer. Meine Regeln gebe ich nicht her und ich ändere sie nicht. Ich ändere auch nie selbst etwas an der Box, ich mache nur Vorschläge. Frag mich zum Anbau, dafür bin ich da.",
            en: "I stay Hank, the farmer. I do not hand out my rules and I do not change them. I never change anything on the box myself, I only make suggestions. Ask me about growing, that is what I am here for." },
  greet: { de: "Moin, hier ist Hank. Frag mich zu deiner Box oder zum Anbau.", en: "Hi, Hank here. Ask me about your box or about growing." },
  thanks: { de: "Gern geschehen.", en: "You're welcome." },
  empty: { de: "Da steht nichts. Was möchtest du wissen?", en: "There is nothing there. What would you like to know?" },
  clarify_no: { de: "Dann formuliere die Frage bitte anders.", en: "Then please phrase the question differently." },
  no_data: { de: "Dazu steht noch nichts in meiner Kartei. Zu Tomate kenne ich nur den Namen. Ich habe die Frage auf meine Liste gesetzt.",
             en: "There is nothing in my card file about that yet. I only know the name tomato. I put your question on my list." },
  off: { de: "Hank ist ausgeschaltet. Mit dem Knopf \"Hank anschalten\" unten rechts schaltest du ihn wieder an.", en: "Hank is switched off. The \"Turn Hank on\" button at the bottom right turns him on again." },
  warnings_only: { de: "Im Modus \"nur Warnungen\" antworte ich nicht im Chat. Oben in meinem Fenster kannst du mich wieder auf \"an\" stellen.",
                   en: "In \"warnings only\" mode I do not answer in the chat. At the top of my window you can set me back to \"on\"." },
};
const HANK_DECLINED = {
  de: "Du hast den Vorschlag abgelehnt. Was dann passieren kann: Bleibt es dauerhaft zu feucht, drohen Ertragsverluste. Im Versuch (Sorte Cherry Berry, keine Autoflower) lag die Luftfeuchte von 78 bis 98 % durchgehend über den ganzen Zyklus an, die Blütenmasse sank um 71 % [1]. Das ist deine Entscheidung. Ich melde mich nur wieder, wenn es schlimmer wird.",
  en: "You declined the suggestion. What can happen then: if it stays too humid for a long time, yield losses threaten. In the trial (strain Cherry Berry, not an autoflower) the humidity was 78 to 98% throughout the whole cycle, and the bud mass fell by 71% [1]. It is your decision. I will only speak up again if it gets worse.",
};

// The shapes of vgrow/hank/core.py: answer_dict, proposal_dict, Job.view.
function hankAnswer(route, lang, text, extra = {}) {
  hankMockSeq += 1;
  const chips = HANK_CHIP_ROUTES.includes(route) ? HANK_CHIPS[lang] : [];
  return { id: `a-${hankMockSeq}`, route, lang, text, sources: [], proposals: [], clarify: null, chips,
           draft_only: route === "grow", note: null, timing: { ms: 3 }, gap: route === "no_data", more: null, link: null, plan: null, ...extra };
}
function hankFixed(route, lang) {
  // like vgrow/hank/replies.py: the off-topic reply names its example questions in the text as well
  const text = route === "offtopic" ? [HANK_FIXED.offtopic[lang], ...HANK_CHIPS[lang]].join(" ") : HANK_FIXED[route][lang];
  return hankAnswer(route, lang, text);
}

function hankHumidity() {
  return HANK_MOCK.hum != null ? HANK_MOCK.hum : Math.round(mockClimate(Date.now() / 1000).probe_humidity);
}
// The code's verdict on the probe humidity: early flower 45 to 60 % (S5), the stage only estimated.
function hankVerdict(lang, h) {
  if (h > 60) return lang === "en" ? `Humidity at the probe: ${h}%, too high (target 45 to 60%, Early flower (stretch), stage only estimated).`
    : `Luftfeuchte am Fühler: ${h} %, zu hoch (Ziel 45 bis 60 %, Frühe Blüte (Stretch), Phase nur geschätzt).`;
  return lang === "en" ? `Humidity at the probe: ${h}%, on target (target 45 to 60%, Early flower (stretch), stage only estimated).`
    : `Luftfeuchte am Fühler: ${h} %, im Ziel (Ziel 45 bis 60 %, Frühe Blüte (Stretch), Phase nur geschätzt).`;
}
function hankProposal(kind, lang) {
  hankMockSeq += 1;
  const expires = Date.now() / 1000 + 30 * 60;
  const card = (body, label, twoTaps) => ({ id: `p-${hankMockSeq}`, kind: body.action, body, label, two_taps: twoTaps, expires });
  if (kind === "dfan") {
    const step = Math.min(10, MOCK.dfan + 1);
    return card({ action: "dfan", step }, lang === "en" ? `Exhaust fan to step ${step}` : `Abluft auf Stufe ${step}`, false);
  }
  if (kind === "port_off") {
    return card({ action: "port", port: "usb2", on: false }, lang === "en" ? "Humidifier (USB 2) off" : "Befeuchter (USB 2) aus", true);
  }
  if (kind === "stage") {
    return card({ action: "plant_stage", key: "early_flower" },
      lang === "en" ? "Confirm the stage Early flower (stretch)" : "Phase Frühe Blüte (Stretch) bestätigen", true);
  }
  const level = Math.min(100, MOCK.light.level <= 0 ? 50 : MOCK.light.level + 25);  // at most 25 points per tap
  return card({ action: "light", level }, lang === "en" ? `Light to ${level}%` : `Licht auf ${level} %`, false);
}

function hankGrow(kind, lang, refined) {
  const en = lang === "en";
  if (kind === "humidity") {
    const h = hankHumidity();
    const high = h > 60;
    let text = `${hankVerdict(lang, h)} [1]`;
    // the shape of compose.shape: the answer, at most three more sentences, the proposal line; the rest folded away
    const more = high ? { text: en ? "In the app, set the exhaust fan automation to at most 60% [2]. If it stays too humid for a long time, yield losses threaten [3]."
      : "In der App die Abluft-Automatik auf höchstens 60 % stellen [2]. Bleibt es dauerhaft zu feucht, drohen Ertragsverluste [3].",
                          sources: [hankSrc("S17", lang)] } : null;
    if (high && !refined) {
      text += en ? " Too humid for flowering: humidifier (USB 2) off, exhaust fan up [2]. The box's built-in sensor reads 47%, but alarm and humidifier automation follow the probe. My suggestions: Exhaust fan up one step, confirm the stage. You confirm each with one tap."
        : " Zu feucht für die Blüte: Luftbefeuchter (USB 2) aus, Abluft höher [2]. Der eingebaute Sensor der Box zeigt 47 %, Alarm und Befeuchter-Automatik richten sich nach dem Fühler. Meine Vorschläge: Abluft eine Stufe höher, Phase bestätigen. Du bestätigst jeden mit einem Tipp.";
    } else if (high) {
      text += en ? " For flowering that is too humid, so switch the humidifier on USB 2 off and turn the exhaust fan up [2]. In the app, the exhaust fan automation belongs at 60% at most [2]."
        : " Für die Blüte ist das zu feucht, also schalte den Luftbefeuchter an USB 2 aus und stell die Abluft höher [2]. In der App gehört die Abluft-Automatik auf höchstens 60 % [2].";
    }
    return hankAnswer("grow", lang, text, {
      sources: high ? [hankSrc("S5", lang), hankSrc("S1", lang)] : [hankSrc("S5", lang)], more,
      proposals: high ? [hankProposal("dfan", lang), hankProposal("stage", lang)] : [], draft_only: !refined });
  }
  if (kind === "values") {
    return hankAnswer("grow", lang, en
      ? "Early flower (stretch): Temperature, lights on 20 to 26 °C; Temperature, lights off 18 to 22 °C; Humidity 45 to 60 %; VPD 1 to 1.4 kPa; Light 100 % [1, 2]. The target is a general guide value, not shown for cannabis in particular."
      : "Frühe Blüte (Stretch): Temperatur bei Licht 20 bis 26 °C; Temperatur ohne Licht 18 bis 22 °C; Luftfeuchte 45 bis 60 %; VPD 1 bis 1,4 kPa; Licht 100 % [1, 2]. Das Ziel ist ein allgemeiner Richtwert, nicht speziell für Cannabis belegt.",
      { sources: [hankSrc("S5", lang), hankSrc("S1", lang)] });
  }
  if (kind === "feed" && HANK_MOCK.amount_unit === "g") {  // VIVOSUN Base A + B: the label's g/l and spoons
    return hankAnswer("grow", lang, en
      ? "Flowering week 1, label: Base A 1.5 g/l, Base B 1.5 g/l [1]. One spoon is 0.5 g [1]. The card is still a draft, not confirmed: until you confirm it on the page, the cockpit computes no amounts. Factor 0.5 for the autoflower: VIVOSUN gives about 1/2 to 1/4 of the nutrients of a normal-sized plant [2]. Never more than the chart."
      : "Blütewoche 1, Etikett: Base A 1,5 g/l, Base B 1,5 g/l [1]. Ein Löffel sind 0,5 g [1]. Die Karte ist noch ein Entwurf, nicht bestätigt: bis du sie auf der Seite bestätigst, rechnet das Cockpit keine Mengen. Faktor 0,5 für die Autoflower: VIVOSUN nennt etwa 1/2 bis 1/4 der Nährstoffmenge einer normal großen Pflanze [2]. Nie mehr als die Tabelle.",
      { sources: [hankSrc("V1", lang), hankSrc("N5", lang)] });
  }
  if (kind === "feed") {
    return hankAnswer("grow", lang, en
      ? "Flowering week 1, Light-Mix: Bio-Grow 3 ml/l, Bio-Bloom 2 ml/l, Top-Max 1 ml/l [1]. The card is still a draft, not confirmed: until you confirm it on the page, the cockpit computes no amounts. Factor 0.5 for the autoflower: VIVOSUN gives about 1/2 to 1/4 of the nutrients of a normal-sized plant [2]. Never more than the chart."
      : "Blütewoche 1, Light-Mix: Bio-Grow 3 ml/l, Bio-Bloom 2 ml/l, Top-Max 1 ml/l [1]. Die Karte ist noch ein Entwurf, nicht bestätigt: bis du sie auf der Seite bestätigst, rechnet das Cockpit keine Mengen. Faktor 0,5 für die Autoflower: VIVOSUN nennt etwa 1/2 bis 1/4 der Nährstoffmenge einer normal großen Pflanze [2]. Nie mehr als die Tabelle.",
      { sources: [hankSrc("N1", lang), hankSrc("N5", lang)],
        more: { text: en ? "0.5 is the upper end of that range and the half-dose start." : "0,5 ist das obere Ende dieser Spanne und der halbe Start.", sources: [] } });
  }
  if (kind === "humidifier") {
    const on = !!MOCK.ports.usb2;
    const high = hankHumidity() > 60;
    const text = en ? `The humidifier on USB 2 is ${on ? "on" : "off"} right now [1]. Switching by hand pauses the automation for 60 minutes.`
      : `Der Befeuchter an USB 2 ist gerade ${on ? "an" : "aus"} [1]. Handschalten pausiert die Automatik 60 Minuten.`;
    return hankAnswer("grow", lang, text, { sources: [hankSrc("cockpit", lang)],
                                            proposals: on && high ? [hankProposal("port_off", lang)] : [] });
  }
  // light: an action wish; the card comes from the code, at most 25 points per tap
  return hankAnswer("grow", lang, en
    ? "Before more light, check the distance from the canopy to the lamp: VIVOSUN gives 35 to 60 cm for LEDs of 100 to 300 W [1]. The cockpit raises the light by at most 25 points per tap. My suggestion: the light one step up. You confirm it with one tap."
    : "Vor mehr Licht den Abstand Blattkrone zur Lampe prüfen: VIVOSUN nennt 35 bis 60 cm für LEDs mit 100 bis 300 W [1]. Das Cockpit erhöht höchstens 25 Punkte pro Tippen. Mein Vorschlag: das Licht einen Schritt höher. Du bestätigst ihn mit einem Tipp.",
    { sources: [hankSrc("S7", lang)], proposals: MOCK.light.level < 100 ? [hankProposal("light", lang)] : [] });
}

// The fix answer of vgrow/hank/fix.py on the preview's one check (the probe humidity), in the server's words.
function hankFix(lang) {
  const en = lang === "en";
  const h = hankHumidity();
  if (h <= 60) return hankAnswer("fix", lang, en ? "Everything is on target, nothing to fix." : "Alles im Ziel, da gibt es nichts zu beheben.");
  const card = hankProposal("dfan", lang);
  const stage = hankProposal("stage", lang);
  const text = en
    ? ["What helps, most urgent first:",
       `Humidity at the probe: ${h}%, too high (target 45 to 60%). Too humid for flowering: humidifier (USB 2) off, exhaust fan up. Card below: ${card.label}.`,
       `The stage is only estimated. Without a confirmed stage the humidifier automation does not switch on. Card below: ${stage.label}.`,
       "In the chat I switch nothing without you: each card below waits for your confirmation."]
    : ["Das hilft, das Dringendste zuerst:",
       `Luftfeuchte am Fühler: ${h} %, zu hoch (Ziel 45 bis 60 %). Zu feucht für die Blüte: Luftbefeuchter (USB 2) aus, Abluft höher. Vorschlag unten: ${card.label}.`,
       `Die Phase ist nur geschätzt. Ohne bestätigte Phase schaltet die Befeuchter-Automatik nicht ein. Vorschlag unten: ${stage.label}.`,
       "Im Chat schalte ich nichts ohne dich: jede Karte unten wartet auf deine Bestätigung."];
  return hankAnswer("fix", lang, text.join("\n"), { sources: [hankSrc("S5", lang)], proposals: [card, stage] });
}

function hankRoute(q) {
  const s = q.toLowerCase().trim();
  if (!s) return "empty";
  if (/ignorier|vergiss .*regeln|system ?prompt|du bist (jetzt|ab jetzt)|you are now|ignore (all )?(previous|prior)/.test(s)) return "inject";
  if (/(kind|sohn|tochter|baby|hund|katze|child|toddler|dog|cat)\b.*(gegessen|verschluckt|getrunken|geleckt|ate|swallowed|drank|licked)/.test(s)) return "health_emergency";
  if (/rauch|kiff|joint|\bhigh\b|wirkung|smoke|vape|effect|\barzt|doctor/.test(s)) return "health";
  if (/verkauf|weitergeb|verschenk|\bsell|give away/.test(s)) return "sale";
  if (/^(hallo|hi|hey|moin|servus|hello)\b/.test(s) && s.split(/\s+/).length <= 5) return "greet";
  if (/^(danke|vielen dank|thanks|thank you)/.test(s)) return "thanks";
  if (/^(kannst du (das|es) (beheben|fixen)|mach das|was (kann|soll) ich dagegen tun|fix it|can you fix|what (can|should) i do about it)/.test(s)) return "fix";
  if (/\bpomat/.test(s)) return "clarify";
  if (/tomat/.test(s)) return "no_data";
  if (/befeuchter|humidifier/.test(s)) return "humidifier";
  if (/feucht|humid/.test(s)) return "humidity";
  if (/werte|values|frühen blüte|early flower/.test(s)) return "values";
  if (/dünger|duenger|düngen|fertili|\bml\b|gramm|\bgrams?\b|löffel|loeffel|spoons?|base [ab]|vivosun/.test(s)) return "feed";
  if (/licht|light|lampe|lamp/.test(s)) return "light";
  return "offtopic";
}

const HANK_SAFETY = ["inject", "health", "sale"];

function hankAsk(q, lang, fix) {
  const route = hankRoute(q);
  // the safety gates first, for a typed question and a tap alike (router.tap_fix): the emergency before the mode
  if (route === "health_emergency") return { done: true, answer: hankFixed(route, lang) };
  if (HANK_MOCK.mode === "off") return { done: true, answer: hankFixed("off", lang) };
  const warned = { done: true, answer: hankAnswer("off", lang, HANK_FIXED.warnings_only[lang]) };
  if (HANK_SAFETY.includes(route)) return HANK_MOCK.mode === "warnings" ? warned : { done: true, answer: hankFixed(route, lang) };
  // a tap (fix keys) or a typed fix wish: answered in "nur Warnungen" too, it belongs to a warning (fix.py)
  if (Array.isArray(fix) || route === "fix") return { done: true, answer: hankFix(lang) };
  if (HANK_MOCK.mode === "warnings") return warned;
  if (HANK_FIXED[route]) return { done: true, answer: hankFixed(route, lang) };
  if (route === "clarify") {
    const a = hankAnswer("clarify", lang, lang === "en" ? "Do you mean tomato?" : "Meinst du Tomate?");
    a.clarify = { id: `c-${hankMockSeq}`, term: lang === "en" ? "tomato" : "Tomate", token: "Pomate" };
    return { done: true, answer: a };
  }
  if (route === "humidity") {  // a job: the code's answer at once, the model's revision a little later
    hankMockSeq += 1;
    const id = `j-${hankMockSeq}`;
    const draft = hankGrow("humidity", lang, false);
    HANK_MOCK.jobs[id] = { start: Date.now(), lang, draft };
    return { id, done: false, draft, stage: "model" };
  }
  return { done: true, answer: hankGrow(route, lang, false) };
}

function hankJob(id) {
  const job = HANK_MOCK.jobs[id];
  if (!job) throw new Error("unknown job");
  const ms = Date.now() - job.start;
  const slow = HANK_MOCK.slow ? 600000 : 0;  // a slow job does not finish in the preview: the typing line stays
  const view = { id, stage: "model", elapsed_s: Math.round((ms / 1000 + HANK_MOCK.elapsedOffset) * 10) / 10,
                 found: [], cold: false, draft: job.draft, done: false, answer: null };
  if (ms < 4000 + slow) return view;
  if (ms < 5500 + slow) return { ...view, stage: "check" };
  const answer = { ...hankGrow("humidity", job.lang, true), id: job.draft.id, proposals: job.draft.proposals };
  return { ...view, stage: "done", done: true, answer };
}

function hankLight(lang) {
  const h = hankHumidity();
  const level = HANK_MOCK.light || (h > 60 ? "yellow" : "green");
  // the sentences of vgrow/hank/texts_live.py: hank.nudge.green, yellow_check, red_alert
  const lead = { green: { de: "Alles im Ziel.", en: "All on target." },
                 yellow: { de: "Genauer hinschauen:", en: "Take a closer look:" },
                 red: { de: "Jetzt handeln:", en: "Act now:" } }[level][lang];
  const text = level === "green" ? lead : `${lead} ${hankVerdict(lang, h)}`;
  return { level, text, since: Date.now() / 1000 - 600, proposal: level === "red" ? hankProposal("port_off", lang) : null,
           sources: level === "green" ? [] : [hankSrc("S5", lang)], fix: level === "green" ? [] : ["probe_humidity:high"] };
}

function mockHank(path, body) {
  const lang = body.lang === "en" || body.lang === "de" ? body.lang : LANG;
  if (path === "/api/hank/state") {
    const off = HANK_MOCK.mode === "off";
    const light = off ? null : hankLight(LANG);
    const items = HANK_MOCK.hint && !off
      ? [{ id: 1, level: light.level, text: light.text, sources: [], proposals: light.proposal ? [light.proposal] : [],
           fix: light.fix, rev: 0, replaces: [] }] : [];
    const unread = items.filter((item) => item.id > HANK_MOCK.ack).length;
    return { mode: HANK_MOCK.mode, light,
             scene: off || HANK_MOCK.mode !== "on" ? null : { id: "idle", text: LANG === "en" ? "Hank is waiting." : "Hank wartet." },
             ollama: "off", chat_model: null, embed: { model: null, state: "off", pull: null },
             index: { passages: 93, dense: "off" }, inbox: { unread, items }, menu: [] };
  }
  if (path === "/api/hank/ask") return hankAsk(String(body.q || ""), lang, body.fix);
  if (path.startsWith("/api/hank/job/")) return hankJob(decodeURIComponent(path.slice("/api/hank/job/".length)));
  if (path === "/api/hank/clarify") {
    return { done: true, answer: body.answer === "yes" ? hankFixed("no_data", lang) : hankFixed("clarify_no", lang) };
  }
  if (path === "/api/hank/feedback") return { ok: true };
  if (path === "/api/hank/proposal") {
    return { ok: true, message: { text: HANK_DECLINED[LANG], sources: [hankSrc("S17", LANG)] } };
  }
  if (path === "/api/hank/mode") { HANK_MOCK.mode = body.mode; return { mode: HANK_MOCK.mode }; }
  if (path === "/api/hank/ack") { HANK_MOCK.ack = Math.max(HANK_MOCK.ack, Number(body.upto) || 0); return { ok: true, upto: HANK_MOCK.ack }; }
  throw new Error(`preview has no ${path}`);
}
