# HankDrawer

Hank's panel: a head with his avatar, the mode switch and the traffic light, steps he needs from you, the conversation as bubbles with sources and suggestion cards, and the composer.

**Markup**: `<aside class="hk-drawer"><div class="hk-head">…</div><div class="hk-top">(seg, hk-mode-hint, hk-light, hk-steps)</div><div class="hk-log">(hk-msg hk-bot | hk-me, hk-typing)</div><div class="hk-foot"><textarea class="hk-q"><button class="btn primary hk-send"></div><p class="hk-hint">…</p></aside>`. Inside a bot bubble: paragraphs, `hk-ref` superscripts, a `hk-src` list, a `hk-prop` suggestion card with a `btn-row`, and `hk-chips`.

**Tokens**

- The drawer is `card` with a `line` left border and `shadow-raised`, `hank-panel-w` wide from 960px; below, a sheet from the bottom with `radius-lg` top corners over `backdrop`.
- Bot bubbles `bg` on `line` with `radius-lg` (bottom-left `radius-sm`); your bubbles `accent-strong` with `on-accent` (bottom-right `radius-sm`). Text `body`.
- The traffic light: `accent` dot at rest, `plant-amber` for yellow, `bad` on `bad-soft` with a `bad` border for red. A step row has a `warn` dot and a `line-strong` border; hover `warn-soft`.
- Suggestion card: `card` on `line-strong`, `radius-md`, an `eyebrow` "Suggestion", the proposal in `body-strong`, then Do it / Not now. Chips: `accent-strong` outline pills, `small-strong`.
- The composer is `bg` on `line-strong` at 16px (so phones do not zoom), 44px minimum; the send button is a primary Button square.

**Rules**

- Hank proposes; the person confirms. Every suggestion is a card with Do it and Not now; Hank never switches anything from the chat without the card.
- A bot answer that uses the box's data cites it under the bubble (Sources); the superscript links to the entry.
- The traffic light is one line and a button: tap it to see what helps. Red is the only state that tints the row.
- Steps Hank needs (confirm the stage, the lamp distance) sit above the log as rows, each opening the card on the page where the step is done.
- The mode switch here mirrors the Autopilot card's; it is `quiet` because the card is the primary place to change it, and its hint repeats what the chosen mode does.
- The consumer provides the messages, the mode, the light state, the steps and the handlers; the drawer scrolls only its log.
