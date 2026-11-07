# Topbar

The sticky bar over the main column: the page title, the box, the box's health, and the icon buttons.

**Markup**: `<header class="topbar"><div class="title"><h1>Automation</h1><small>VGrow Y18</small></div><div class="health"><span class="status ok">Online</span><span class="age">Readings 51 s ago · Device status 8 min ago</span></div><div class="tools"><button class="icon-btn">…</button></div></header>`.

**Tokens**

- `card` ground, `line` rule below, `topbar-h` tall, padding `space-4` 28px, `z-topbar` when sticky.
- Title in `display` (24/30), the box name in `small` `muted`. The health block right-aligns an ok/bad StatusChip and the freshness line in `caption` (12px, tabular), never smaller.

**Rules**

- The freshness line is the page's trust signal: keep it at `caption` size and in the top bar on every width. Below 700px it wraps under the title in its own row.
- One counter in the top bar: unread items on the bell. Hank's floating button carries no second badge; the box selector shows no bare number.
- Page-level secondary buttons (Add box, History & archives) belong to the page intro under the bar, not to the bar. The bar has no `btn primary`.
- Below 960px a menu icon button (the `menu` asset) opens the sidebar drawer; the settings icon button hides.
- The consumer provides the title, the box name, the health state and the icon buttons with `aria-label`s.
