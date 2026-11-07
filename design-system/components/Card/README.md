# Card

The surface everything on a page lives on: `card` on `bg`, a `line` border, `radius-lg`, `space-5` padding.

**Markup**: `<section class="card"><div class="card-h"><h3>Title</h3>[<a class="text-link">]</div>…</section>`. The hero card of a page adds a kicker: `<div class="kicker"><img src="…hank…"><div><small>Hank is watching over your grow</small><h3 class="hero">Autopilot</h3></div></div>`.

**Rules**

- Title in `title` (16/22); the one hero card per page may use `heading` (20/26). Nothing inside a card is larger than its title: a health line inside is `body-strong` with StatusChips, not a 20px heading.
- `card-h` holds the title, an optional StatusChip, an optional `text-link` at the right (Configure light timer) and nothing else. Actions go at the end of the body.
- Running text inside stays within `measure` even when the card spans two columns.
- Cards are the grid unit: `device-grid` lays them out two across from 700px, one below, with `space-4` gaps (`space-5` from 700px in the live shell).
- Shadows stay `shadow-card`; depth comes from the border. Never add a coloured left border to a card.
- The consumer provides the title, the body, and any chip or link; the card does not scroll on its own.
