# Disclosure

A full-width row that folds a section away and says what is inside, replacing the bare ▶ triangles.

**Markup**: `<details class="disclosure" [open]><summary><svg class="chev"…/><span class="t"><b>Title</b><span>what is inside</span></span>[<span class="status warn">1 needs you</span>]</summary><div class="disclosure-body">…</div></details>`.

**Tokens**

- A `line` rule above each row (and below the last); summary 52px tall with `space-2` vertical padding; the chevron is `icon` in `muted` and turns 90° when open.
- Title in `body-strong`, the description in `small` `muted`. The body is indented `space-8` so it lines up with the title.

**Rules**

- The description names what is inside in one line ("Policy per device, the humidifier rule, command quotas"), so nobody opens a row to find out.
- A row that holds something in a `warn` or `bad` state shows a StatusChip at its right and is open by default. Otherwise rows are closed.
- A Disclosure never hides a blocking action. The confirmation the autopilot waits for lives in its device card; the Disclosure holds the explanation and the settings.
- Rows stack directly; never put a Disclosure inside another one, and never more than six in one card.
- The consumer provides title, description, the optional chip and the body. Native `<details>` keeps keyboard and screen-reader behaviour; do not replace it with a div.
