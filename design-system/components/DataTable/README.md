# DataTable

A numeric table with a sticky header and a sticky first column, right-aligned tabular figures, and a highlighted "now" column; used for the sensor history, the feed chart and the dose table.

**Markup**: `<div class="table-scroll"><table class="data-table"><thead><tr><th>Product</th><th class="now">W 7<small>Early flower</small></th>…</tr></thead><tbody><tr [class="muted-row|total"]><th>Grow A</th><td class="now">1 ml</td>…</tr></tbody></table></div>`. Cell modifiers: `ref` (a reference figure in `muted`), `warn-cell`, `bad-cell`; `total b` in `accent-strong`.

**Tokens**

- `card` cells divided by `line`; the scroll frame has a `line` border and `radius-md`, max height 360px. Header and first column stick on `card`. The now column is `accent-soft`, its header `accent-strong`.
- Text `small` (13px) tabular; headers `muted` weight 600; row headers `ink` weight 600.

**Rules**

- Numbers right-aligned with their unit in the cell ("2 ml", "1.4 mS/cm"); a missing value is an en dash, never empty.
- The first column names the row and stays visible while the table scrolls sideways on a phone; the header stays visible while it scrolls down.
- One highlighted column at most: the current week, or today.
- The history table is at most 360px tall and scrolls inside its frame; the page never scrolls because of a table.
- The consumer provides headers, rows and the now column; the table does not sort or filter.
