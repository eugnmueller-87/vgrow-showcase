# PageTabs

A row of text links under the page title that switch between views of one page, the active one underlined in `accent-strong`.

**Markup**: `<nav class="page-tabs" aria-label="…"><a href class="[active]" [aria-current="page"]>Climate</a>…</nav>`.

**Tokens**

- A `line` rule under the row; tabs `body` in `muted`, 46px tall, `space-6` apart; the active tab `accent-strong` weight 600 with a 2px `accent-strong` underline.

**Rules**

- Tabs are views of the same page (Climate, Light, Water on Analytics); sections of the sidebar are not tabs, and modes are a SegmentedControl.
- Two to six tabs, one word or two each. The row scrolls sideways on a phone rather than wrapping.
- Exactly one tab is `active` and carries `aria-current="page"`; the URL hash follows it so a view can be linked.
- The consumer provides the tabs and the active one.
