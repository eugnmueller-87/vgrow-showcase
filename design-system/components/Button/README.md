# Button

One action, in sentence case, sized `control-h` (44px on touch) with `radius-md`.

**Markup**: `<button class="btn [primary|soft|arm] [sm] [is-locked]">` or `<a class="btn">`. Icon buttons: `<button class="icon-btn" aria-label="…"><svg/><span class="count">1</span></button>`.

**Variants**

- `primary`: `accent-strong` fill, `on-accent` text. One per view. The action the page exists for: Save the pot, Distance measured.
- default: `card` fill, `line-strong` border, `ink` text. Everything else: History & archives, Confirm refill.
- `soft`: `accent-soft` fill, `accent-strong` text. A step the autopilot asks for inside a card (Check lamp distance, Name USB 2 again). Never next to a primary.
- `arm`: `bad-fill` with `on-bad-fill`. Stops, overrides, takes over. Place it last in its card.
- `sm`: `control-h-sm`, 13px. Only inside dense rows.
- `:disabled`: fades to `opacity-disabled`. A busy primary keeps its label and turns `accent-soft`.
- `is-locked`: full opacity, dashed `line-strong` border, `muted` text, with a reason line beside it. Locked is not disabled: the person can read why.

**Rules**

- Label is a verb phrase in sentence case: "Save the pot", not "SAVE" or "Pot".
- Buttons never wrap to two lines except a stage-confirmation whose stage name is long; then `white-space: normal; text-align: left`.
- The consumer provides the label, the handler, and `aria-label` for icon buttons. The count badge on an icon button is the only counter in the top bar.
- Focus ring is inherited from `.vg` (3px `accent-strong`); never remove it.
