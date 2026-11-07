# EmptyState

A dashed card for a place that has nothing yet: a title, one paragraph that says why and what will fill it, and the one action.

**Markup**: `<div class="empty-state" role="status"><b>No box yet</b><p>…</p><button class="btn primary">Add box</button></div>`.

**Tokens**

- `card` with a dashed `line-strong` border, `radius-lg`, padding `space-8` `space-6`, min height 160px. Title in `title` `ink`; text in `body` `muted` within `measure`.

**Rules**

- This is where "Start here" and "Add box" belong: the empty state owns the page's primary action, so the top bar does not.
- The text says what will appear and what has to happen first ("once the first reading arrives"); no illustration, no emoji.
- One action, at most two. A list of things to do is a RulesList or a Disclosure, not an empty state.
- A card that is empty because the box is offline is not an EmptyState; it is a paused Card (header kept, body at `opacity-paused`) with a Banner.
