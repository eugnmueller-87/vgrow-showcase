# Range

A native slider with its label on the left and its value on the right, for a device level the person sets by hand (light percent, fan step).

**Markup**: `<div class="range"><label for="id">Light</label><input id="id" type="range" min max step value [class="is-locked"]><output for="id">87%</output></div>`.

**Tokens**

- The thumb and the filled track take `accent-strong` through `accent-color`; the control is `control-h` tall. Locked: `muted`.
- Label in `small-strong` `muted`; value in `reading` (14/700, tabular).

**Rules**

- The value is always shown as text; the slider alone is not enough to read a level.
- A slider that the autopilot owns right now is `is-locked` with a reason line under it, not disabled.
- Steps match the device: 0 to 3 for the exhaust fan, 0 to 100 in steps of 1 for the light. The consumer provides min, max, step, the value and the change handler.
- Never put a slider inside a Banner or a compact DeviceCard; it needs a full-width row.
