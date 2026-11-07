# SegmentedControl

An exclusive choice between two to four modes where the chosen one is filled `accent-strong`, so the mode of the autopilot is readable from across the room.

**Markup**: `<div class="seg [block] [quiet] [is-locked]" role="group" aria-label="…"><button aria-pressed="true|false" [class="arm"]>Mode</button>…</div>` followed by an optional `<p class="seg-note">` that explains the chosen mode.

**Tokens**

- Track: `bg` with a `line-strong` border, `radius-md`, padding `space-1`. Segments: `radius-sm` (concentric), `control-h-sm` tall (`control-h` on touch), `body-strong` text in `muted`.
- Chosen (`aria-pressed="true"`): `accent-strong` fill, `on-accent` text.
- `arm` on a segment that stops something (Off while the autopilot runs): `bad` text at rest, `bad-fill` with `on-bad-fill` when chosen.
- `quiet`: for low-stakes switches (theme, language, chart range): chosen segment is `card` with `shadow-segment`.
- `is-locked`: dashed track, chosen segment `line` with `ink` text; add a reason line beside it.

**Rules**

- Use it for a mode, never for navigation (that is the page tab) and never for an action (that is a Button).
- The Off / Observe / Automatic control is the most consequential control on the Automation page: it is never `quiet`, and it always has a `seg-note` that says what the chosen mode does in one sentence.
- `block` stretches the segments to the container; use it inside a card's full width. Otherwise the control is as wide as its labels.
- Labels are one word each where possible. If a label must wrap (a long stage name), allow `white-space: normal` on that control only.
- The consumer provides the labels, the chosen state and the handler; the control does not keep state.
