# Statusline

One line that sums up the plant (strain, day, stage) or the water state, as a link to its page: a small plant drawing, the sentence, a StatusChip and a chevron.

**Markup**: `<a class="statusline" href="…"><span class="sl-art"><svg/></span><span class="sl-text">Ice Cream Cake Automatic · day 62 · early flower, day 9</span><span class="status ok">On track</span><svg class="sl-go"/></a>`.

**Tokens**

- `card` on `line`, `radius-lg`, 52px minimum; hover turns the border `line-strong`. Text `body-strong` tabular; the chevron `muted` 16px. The art is 22 × 40 in the `plant-*` palette.

**Rules**

- Facts joined by the middle dot, no verbs: the chip carries the judgement ("On track", "Pot not set up").
- The whole line is the link; the chip is never a separate control.
- One statusline per card at most, under the tiles on Overview; on phones the sentence wraps and the chip stays on the first line.
- The consumer provides the sentence, the chip state and the href.
