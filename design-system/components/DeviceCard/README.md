# DeviceCard

One device, its state as a StatusChip in the header row, its reading at the right, and at most two lines and one button; a device with nothing to say is one compact row.

**Markup**: `<article class="device [attention|stopped]"><div class="device-h"><b>Name</b><span class="status …">State</span><span class="reading">87%</span></div><p>reason</p>[<p class="rule">limits</p>][<button class="btn …">]</article>`. Compact: `<article class="device compact"><b>Name</b><span class="status …">State</span><span class="reading">0</span></article>`. Lay several out in `.device-grid`.

**Tokens**

- `card` on `line`, `radius-lg`, `space-4` padding. `attention` turns the border `warn`; `stopped` turns it `bad`. No left-border accent.
- Name in `body-strong`, reading in `reading` (14/700, tabular), the reason in `body` `muted`, the rule line in `body` `ink`.

**Rules**

- Order: needs-you, stopped, paused, monitoring, off. The card that needs the person is first and carries `attention`.
- The chip is the status. The body never repeats it ("You took over" under a "Paused" chip is a repeat): it says why and what comes next.
- The confirmation the autopilot is waiting for is this card's `btn primary`. A step Hank suggests is `btn soft`. An override is `btn arm`, last.
- Compact rows for monitoring, off and paused devices without a next step. Do not stretch an empty card to the height of a full one.
- The reading is the device's own unit as the app reports it (0%, Off, 3); a missing reading shows an en dash, not an empty cell.
- The consumer provides name, state, reading, up to two sentences and one action.
