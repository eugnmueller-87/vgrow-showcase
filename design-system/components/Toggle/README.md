# Toggle

A two-state switch button whose pressed state is the `accent-strong` fill, used for a rule or a device that is on or off.

**Markup**: `<button class="toggle [arm] [is-locked]" aria-pressed="true|false">On</button>`. In a settings list: `<div class="toggle-row"><span class="what">Rule<small>what it does</small></span><button class="toggle" …></div>`.

**Tokens**

- At rest like a secondary Button: `card`, `line-strong`, `radius-md`, `control-h`, `body-strong` text. Pressed: `accent-strong` with `on-accent`.
- `arm` is always `bad-fill`: it takes over or stops something (Take over, Stop).
- `is-locked`: dashed, `muted`; a locked pressed toggle shows `line` fill with `ink` text so the state still reads. Put the reason beside it.

**Rules**

- The label is the state the toggle is in, not the action ("On", not "Turn on"). A toggle that changes its label is a Button.
- Use a Toggle for binary rules and devices; use SegmentedControl for three or more modes; never both for the same thing.
- `aria-pressed` is the state. The consumer provides it and the handler; the toggle does not keep state.
- One toggle per row at the right; the row's text says what it switches and, in `small`, the rule it follows.
