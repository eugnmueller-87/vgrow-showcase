# Banner

A condition that blocks or needs attention, stated once per card with an icon, a bold lead and the reason.

**Markup**: `<div class="banner [bad|info|ok]" role="status|alert"><svg …/><div class="banner-body"><p><b>Lead:</b> reason.</p>[<button class="btn sm">]</div></div>`.

**Variants and tokens**

- default (warn): `warn` on `warn-soft`: setup needed, a check the person must do.
- `bad`: `bad` on `bad-soft`, `role="alert"`: a fail-safe fired, the box is offline.
- `info`: `info` on `info-soft`: paused, waiting, a suggestion.
- `ok`: `accent-strong` on `accent-soft`: on target, confirmed.

**Rules**

- One banner per card, at the top of the body. A second condition is a second sentence in the same banner, never a second banner.
- The lead names the situation in two or three words and ends with a colon; the rest says why and what helps. Severity is carried by the icon and the lead, not by the colour alone.
- Text is `body` (14/21), at most `measure` wide, `radius-md`, padding `space-3` `space-4`.
- The icon is 20px, 1.8 stroke, in the banner's ink: circle-i for warn and info, triangle for bad, circle-check for ok. The consumer passes the icon that matches the variant.
- A banner may hold one small Button that resolves it. Disabled buttons that belong to the card stay outside the banner.
- Not a banner: a fact the person need not act on ("The Mac controller must stay running"). That is `small muted` text under the control.
