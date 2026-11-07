# Gauge

A reading shown against its target band on a horizontal bar: the band is the stage's target, the mark is now.

**Markup**: `<div class="gauge"><div class="g-head"><span class="lbl">Humidity</span><span class="val">68<small>%</small></span></div><div class="g-bar"><span class="g-band" style="left:35%;width:30%"></span><span class="g-mark [high|low|none]" style="left:78%"></span></div><div class="g-foot"><span>min</span><span>50–62 target</span><span>max</span></div></div>`, several inside `.gauges`. A single-value target (the light preset) is `.g-tick` instead of a band.

**Tokens**

- `card` on `line`, `radius-md`. Bar track `bg`, band `accent-soft` outlined 1px `accent`, mark `accent`; `high` turns the mark `bad`, `low` `warn`, `none` `muted`.
- Label `small` `muted`; value 20px/700 tabular with the unit in `small` `muted`; the foot in `caption` `muted`.

**Rules**

- The band and the mark are positioned in percent of the bar's range; the foot names the range ends and the target in words, so the picture is never the only cue.
- Two gauges per row from 560px, one below.
- A gauge shows one reading; it never animates except the mark's position (500ms, off under reduced motion).
- The consumer provides label, value, unit, range, band or tick, and the mark state.
