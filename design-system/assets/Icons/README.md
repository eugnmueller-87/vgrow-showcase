The cockpit's own icon set, copied from the live sidebar and top bar: a 24px grid, 1.6px stroke, round caps and joins, `fill="none"`. In the app the stroke is `currentColor`, so an icon takes the colour of its link (`side-ink` in the sidebar, `muted` in an icon button, `ink` on hover); these asset copies are inked `ink` (#172b23) so they show here, and a consumer swaps the stroke back to `currentColor` when inlining. Single-ink marks: do not add fills or a second colour.

- overview: four rounded squares
- boxes-plants: a two-leaf sprout
- automation: a bolt
- devices: a controller with a dial
- analytics: an axis with a rising line
- alerts: a bell
- settings: a gear
- close and menu: the drawer's close cross and the menu's three bars (below 960px)

Render at `icon` (20px) beside text and in icon buttons; never scale below 16px. New icons follow the same grammar; do not mix in a filled or 2px set.
