# Tile

A climate reading as a card: an uppercase key, its source and age, the value in `number`, a status chip, and the series' sparkline along the bottom edge.

**Markup**: `<div class="tile [stale|alarm]"><span class="k">Humidity</span><span class="src">Probe · 51 s ago</span><span class="val"><span class="v">68</span><span class="u">%</span></span><span class="meta"><span class="status …">…</span></span><svg class="spark hum">…</svg></div>` inside `.tiles`.

**Tokens**

- `card` on `line`, `radius-lg`, `shadow-card`, min-height 140px, padding `space-4` with `space-6` at the bottom for the sparkline.
- Key in `eyebrow` `muted`, source in `caption` `muted`, value in `number` (30/33/700, tabular), unit in `small` `muted`.
- Sparkline stroke is the series token: `temp`, `hum`, `vpd`, `water`; opacity .6.
- `stale`: dashed `warn` border and a `muted` value. `alarm`: `bad` border on `bad-soft`.

**Rules**

- Four tiles across from 960px, two below 700px; the water tile spans the row on phones.
- The value is the sensor's own precision ("24.6", "68", "0.94"); the unit is never inside the number.
- The chip states the band ("In band", "Above 62%", "Stale reading"); the sparkline is context, not the message.
- A tile may link to its detail page; then the whole tile is the link and the chip stays a span.
- The consumer provides key, source, value, unit, state and the last hours of the series.
