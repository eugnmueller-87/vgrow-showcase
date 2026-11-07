# StageTrack

Six plant stages on a rail, each a drawing over a dot and a label; the rail fills to the current stage and a strip below asks the person to confirm it.

**Markup**: `<div class="stage-track"><span class="fill" style="width:50%"></span><button class="st-step done|current|next" [aria-current="step"]><span class="art">(the app's plant drawing)</span><span class="dot"></span><span class="lbl">Early flower</span></button>…</div>` followed by `<div class="stage-confirm"><b>Early flower since Oct 24…</b><div class="btn-row">…</div></div>`.

**Tokens**

- Rail `line`, fill `accent`; dots `card` on `line`, done dots `accent`, the current dot `accent` with a 5px `accent-soft` halo. Labels `caption`; the current label `ink` weight 700, others `muted`. The confirmation strip is `accent-soft` with `body` text.
- The drawings use `plant-pot`, `plant-leaf`, `plant-leaf-dark`, `plant-bud`, `plant-pistil`, `plant-pistil-ripe`, `plant-amber`, the same in every theme.

**Rules**

- The stage drawings in the live app are its own art (`app.js` builds them, with the sway animation on the current one); the shapes in this preview are stand-ins to show the track, not the drawings. Copy the app's SVGs into `.art` when consuming.
- Stages, in order: Seedling, Vegetative, Stretch, Early flower, Late flower, Harvest. Done stages and next stages fade to 50%.
- The strip under the track is where the person confirms or corrects the stage; the autopilot waits for it ("Waiting for the stage" in the Timeline). It carries one primary and one secondary Button.
- The consumer provides the current stage, the days, and the two handlers.
