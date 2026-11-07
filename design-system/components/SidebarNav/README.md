# SidebarNav

The fixed dark column: the mark and name, two captioned groups of links with icons, and a footer that says which box and whether it is online.

**Markup**: `<nav class="side"><a class="brand"><img src="…mark…"><span><b>VGrow Cockpit</b><small>unofficial</small></span></a><div class="nav-caption">Workspace</div><ul class="side-nav"><li><a class="[active]" aria-current="page"><svg/>Label</a></li>…</ul>…<div class="side-foot">…</div></nav>`.

**Tokens**

- Ground `side`, width `sidebar-w`, padding 28px `space-4` `space-5`. Brand text `side-ink-strong`, the mark at 36px with `radius-sm` and a `side-line` inset ring.
- Captions in `eyebrow` on `side-muted`. Links 46px tall, `radius-md`, `body-strong` in `side-ink`; hover `side-hover`; active `side-active` with a 3px inset `nav-active` bar and `side-ink-strong` text. Resource links are `plain` (400 weight, 42px).
- Focus ring on this surface is `nav-active`.

**Rules**

- Seven workspace links in this order: Overview, Boxes & plants, Automation, Devices, Analytics, Alerts & Activity, Settings. Resources below: News, Wiki, Community. Adding a link means adding an icon in the same 24px/1.6 grammar.
- Exactly one link is `active` and carries `aria-current="page"`.
- The footer holds the online StatusChip (`on-side`), the box name and the one "unofficial · not by VIVOSUN" line. The brand's `small` says only "unofficial".
- Below 960px the column becomes a 264px drawer behind a menu button with a `z-backdrop` scrim; the markup does not change.
- The consumer provides the links, the active one and the box state.
