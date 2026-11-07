# RulesList

A numbered list of alert rules, each one line plus a `small` detail and a Toggle at the right.

**Markup**: `<ol class="rules"><li><span class="what">Rule<small [class="need"]>channel, last fired, or what is missing</small></span><button class="toggle" aria-pressed="…">On</button></li>…</ol>`.

**Tokens**

- Rows divided by `line`, numbered `01`, `02` in `caption` `muted` (tabular). The rule in `body`, the detail in `small` `muted`; a detail that names a missing precondition is `small.need` in `warn`, weight 600.

**Rules**

- A rule reads as a condition with its threshold and duration ("Temperature above 30 °C for 10 minutes"); the detail names the channel and the last time it fired.
- The number is for reference in Hank's answers ("rule 03"); keep the order stable.
- Two columns from 960px when the card spans the page (the live `.card.wide .rules`); the markup does not change.
- The consumer provides the rules in order, each with its state and handler.
