# Ladder

Three connected tiles that explain the autonomy levels (Manual, Suggest, Automatic) with a three-dot level mark, the current level outlined.

**Markup**: `<ol class="ladder"><li data-lvl="0|1|2" [class="on"]><span class="lvl"><i></i><i></i><i></i></span><b>Level</b><small>what it means</small></li>…</ol>`.

**Tokens**

- Tiles are `bg` with `radius-md`, joined by a 2px `line-strong` connector. The level dot lights `ink` for Manual, `info` for Suggest, `accent` for Automatic. The current tile has a 2px `accent-strong` outline.
- Title in `body-strong`, explanation in `caption` `muted`.

**Rules**

- The Ladder explains; it does not switch. The switch is the SegmentedControl above it. Show it once per page, inside the "What manual, suggest and automatic mean" Disclosure.
- Keep each explanation to one sentence that says who acts.
- The consumer provides the three texts and which level is current.
