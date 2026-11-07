# Stepper

Minus and plus buttons around ten rising bars, for a device with a small number of discrete levels (fan speed 0 to 10).

**Markup**: `<div class="stepper" role="group" aria-label="…"><button class="st">−</button><div class="bars"><i class="on"></i>…</div><button class="st">+</button><span class="level">3</span></div>`. Each `<i>` gets its height inline (30% to 100%); the first N carry `on`.

**Tokens**

- Buttons are 44px squares, `card` on `line-strong`, `radius-md`, a 22px glyph. Bars are `line`, lit bars `accent`; the level in `reading`.

**Rules**

- The level is always written out beside the bars; the bars are the picture, the number is the value.
- Buttons at the ends disable at the limits (`disabled`, `opacity-disabled`). A stepper the autopilot owns is locked at the row level with a reason, not disabled.
- Use a Stepper for up to ten levels; a Range for percentages.
- The consumer provides the level, the limits and the handlers; the bars are drawn from the level.
