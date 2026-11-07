The cockpit for a VGrow grow box: a calm, green utility interface where every automatic decision shows its reason and a named person owns every threshold. Hank, the grow assistant, advises; the autopilot's code decides; the person confirms. Build every screen so that reads true.

## Voice and content

- Write in plain sentences to one person, as "you": "You set the Exhaust fan yourself. The autopilot leaves it alone until 9:37 AM."
- Every automatic step carries its reason in the same sentence or the next one. Never show a state without a why: "Off at 55%, back on below 45%. At least 15 min off before restarting."
- Say what the system will do, not what it is: "Automatic starts the plan again", "suggestions only, you confirm".
- Name things by their function and port: "Humidifier (USB 2)", "USB 1 · Pump". Use the middle dot `·` to join facts on one line.
- Sentence case everywhere, including buttons: "Check lamp distance", "Save the pot". Title case only for proper names (VIVOSUN, VGrow Y18, Ice Cream Cake Automatic).
- Numbers are tabular (`font-variant-numeric: tabular-nums`) wherever they line up. Write one decimal only when the sensor delivers one: "55%", not "55.0%". Times as "9:37 AM", dates as "Oct 25, 7:32 AM".
- No emoji in the interface. Hank's face is the only illustration that speaks.
- One fact per line in a card. A repeated clause ("You took over / paused until 9:37 AM, you took over") is a bug, not emphasis.
- "unofficial · not by VIVOSUN" appears once, in the sidebar footer.

## Colour

Three themes: `light` is primary, `dark` follows the system preference or `data-theme="dark"`, and `safe` is the added colour-blind-safe variant below. Every token exists in all three (a token without its own safe value inherits light); the notes in the token list name the grounds each text colour is checked on.

- Page ground is `bg`; everything that holds content sits on `card` with a `line` border. Text on both is `ink`; secondary text is `muted`. On a soft fill (`accent-soft`, `warn-soft`, `bad-soft`, `info-soft`) secondary text is `muted-on-soft`.
- The sidebar is `side`. On it, links are `side-ink`, the brand name and the active link `side-ink-strong`, captions `side-muted`, the active bar `nav-active`, fills `side-hover` and `side-active`.
- Green has three jobs and three tokens. `accent` paints graphics only (chart bands, gauge marks, timeline dots, the cover's leaves). `accent-strong` is the only green for text, links, the primary button and the focus ring. `accent-soft` is the tint behind ok states and explanation panels. `brand-forest` and `brand-emerald` are identity colours for the cover, print and marketing, not for controls.
- Status colours come in pairs and are only ever used as a pair: `warn` on `warn-soft`, `bad` on `bad-soft`, `info` on `info-soft`, `accent-strong` on `accent-soft`. Never put a status ink on `bg` as a fill, and never put status text on a status fill of another family.
- `bad-fill` with `on-bad-fill` is for a button that stops, overrides or arms something. It is the only saturated red fill.
- `sel-bg` with `sel-ink` marks an exclusive choice (the chosen chip, the active legend). It is not a button.
- `temp`, `hum`, `vpd`, `water` are reserved for sensor series and their legends. Do not reuse them for UI states.
- The plant drawings and Hank keep their own palette (`plant-*`, `hank-*`) in both themes. Never theme them.
- Colour never carries a status alone. Every chip and banner has a word and, in a banner, an icon; in the brand's own themes `accent-strong` and `bad` sit at the same lightness (1.05:1 between them), so a red–green colour-blind reader tells ok from bad by the word, not the hue.

### Colour-blind-safe theme

`safe` is a third, added theme (`<html data-theme="safe">`), built on the light theme with the status family moved onto Okabe & Ito's set: ok leaves the red–green axis to blue (`accent` #0072b2, `accent-strong` #005f96, `accent-soft` a blue tint), bad is vermillion (`bad` #8f3e00 text, `bad-fill` #b84f00, `bad-soft` a vermillion tint), warn is ochre (`warn` #7a5c00 on a yellow `warn-soft`), info is a plain grey (`info` #4f5b56), and the sidebar signals follow (`dot-ok` and `nav-active` sky blue, `dot-bad` vermillion). Every pair keeps 4.5:1 or better; the brand's own light and dark values stay exact. Offer it as a setting next to Light / System / Dark; it is a light theme, so pair it with `prefers-color-scheme: light`. Warn and bad still share a warm family, so the icon and the lead word on banners stay mandatory in every theme. The logo, the plant drawings and Hank do not change.

## Type

One family, `sans` (Inter where installed, otherwise the system face). No font file ships with the system; if the Inter look must be identical on every machine, add the Inter variable woff2 under `fonts/` and list it in `tokens.json`.

The scale has six text sizes and three weights. Use the named styles, never ad-hoc sizes:

| Style | Size | Use |
| --- | --- | --- |
| `display` | 24/30 · 700 | the page title in the top bar |
| `heading` | 20/26 · 700 | the hero card's title, headings between cards |
| `title` | 16/22 · 600 | card titles |
| `body-strong` | 14/21 · 600 | labels, buttons, device names, sub-headings inside a card |
| `body` | 14/21 · 400 | all running text |
| `small-strong` / `small` | 13/18 | field labels and chip text / the line under a name, hints, footers |
| `caption` / `eyebrow` | 12/16 | metadata that is not a sentence / uppercase group captions |
| `number` / `reading` | 30/33 · 700 / 14/21 · 700 | tile values / a device reading in a row |

- 14px is the floor for a sentence; 12px is the floor for anything. Nothing on the page is 11px or 10px.
- Inside a card nothing is larger than the card's own `title`. A line like "Hank needs your help with these devices" inside the Autopilot card is `body-strong`, with the counts as status chips beside it.
- Running text never runs wider than `measure` (68ch), however wide the card is.
- Weights are 400, 600, 700. Do not use 500 or 650.

## Spacing, radius, borders, shadows

- Space on a 4px base: `space-1` to `space-12`. Cards use `space-5` (20px) for padding and for the gap between them from 700px up, `space-4` below.
- Three radii: `radius-sm` (6) for inputs, chips and segments; `radius-md` (10) for buttons, links and banners; `radius-lg` (16) for cards and panels; `radius-pill` for pills. Nested corners stay concentric: a segment of `radius-sm` sits inside a `radius-md` track with `space-1` padding.
- Borders, not shadows, separate surfaces: `line` for cards and dividers, `line-strong` for controls. `shadow-card` is near-invisible by design; `shadow-raised` is for Hank's button and drawer only.
- A control's border is never its only cue: the fill (`card` on `bg`) and the text carry the contrast.

## Layout

- Fixed sidebar of `sidebar-w` (248px from 1200px, 220px from 960px, a 264px drawer below), a sticky top bar of `topbar-h` on `card` with a `line` rule, and a main column of at most `content-max` padded `space-6` / 28px.
- Breakpoints from the live shell: 700, 960, 1200, 1280 (room for Hank's panel), 1848 (main column centres).
- Cards stack in a two-column grid (`device-grid`) from 700px up; a banner that belongs to the grid spans both columns.
- Hank's panel is `hank-panel-w` on the right from 960px; from 1280px the main column shrinks to make room instead of being covered.

## Status language

Each device and each rule is in exactly one state, and the state is always a word plus a chip:

| State | Word | Chip | Tokens |
| --- | --- | --- | --- |
| monitoring, on target | "Monitoring" | ok | `accent-strong` on `accent-soft` |
| needs the person | "Needs you" | warn | `warn` on `warn-soft` |
| paused by the person | "Paused until 9:37 AM" | info | `info` on `info-soft` |
| stopped by a fail-safe, offline | "Stopped: humidity did not rise" | bad | `bad` on `bad-soft` |
| off, not configured | "Off" / "Setup needed" | neutral / warn | `ink` on `line` / `warn` on `warn-soft` |

Order devices by state: needs-you first, then stopped, paused, monitoring, off.

## Iconography

- The nav icons are a 24px grid, 1.6px stroke, round caps and joins, `fill="none"`, drawn in `currentColor` in the app (the asset copies are inked in `ink` so they show in the asset view). Add icons in the same grammar; never mix in a filled set.
- The mark (`assets/Logos/vgrow-mark.svg`) is a dark rounded square with a two-leaf sprout: `logo-ground`, `logo-stem`, `logo-leaf`. Use it at 36px in the sidebar and 30px in the phone top bar with a `side-line` inset ring. Do not recolour, outline or animate it.
- Hank (`assets/Hank/hank-avatar.svg`) is the only character. He appears once per screen, in the Autopilot card header or the floating button, never both.
- No emoji, no stock illustration.

## States and motion

- Focus: a 3px solid `accent-strong` outline, offset 3px, on `bg` and `card`; on `side` the outline is `nav-active`. Never remove the ring.
- Disabled controls fade to `opacity-disabled`. A locked control (the box is offline, the person must confirm first) does not fade: it keeps full opacity, a dashed `line-strong` border, `muted` text and a reason line next to it.
- A paused card fades everything but its header to `opacity-paused`.
- Touch targets are 44px (`control-h-touch`); on a pointer device buttons may be `control-h`.
- Motion is limited to the sidebar drawer (180ms), the stage-track fill, and Hank's typing dots; all of it stops under `prefers-reduced-motion`.
- `scrim` sits behind the REC badge on video and the phone sidebar; `backdrop` behind Hank's drawer below 1280px.

## Adopting in cockpit.css

The token names match the variables the stylesheet already declares (`--ink`, `--muted`, `--accent-strong`, `--warn-soft`…), so `tokens.css` replaces the `:root` blocks of `style.css` and `cockpit.css` without renaming. What changes:

- `--r-sm/--r-md/--r-lg/--radius` become `radius-sm/md/lg`; 7, 9, 12, 13 and 14px corners snap to the nearest of 6, 10, 16.
- `body` moves from 15px to the `body` style at 14/21; 13px paragraphs move up to 14; 12.5 and 13.5px disappear; 11 and 10px captions become `caption`.
- Weights 500 and 650 become 600 and 700.
- `.pill.sm` with a status class becomes the StatusChip; `.ap-device-status` (the 11px coloured word) is replaced by a chip in the card header.
- Inputs are `card` on every ground; `.tank-fields input` and `.light-numeric input` drop their `bg` fill.
- `components/bundle.js` is plain JavaScript (no framework): load it after `tokens.css` and `bundle.css` and build with `window.VGrowCockpit.StatusChip({ state: 'warn', text: 'Needs you' })` and the other factories listed in `components/index.d.ts`; the elements they return are the markup the component pages document.
