# Chips

A row of pill buttons for picking one or several options, where the chosen ones fill `sel-bg`.

**Markup**: a `small-strong` label, then `<div class="chips" role="group" aria-label="…"><button aria-pressed="true|false" [class="is-locked"]>option</button>…</div>`.

**Tokens**

- `card` on `line-strong`, `radius-pill`, `control-h-sm` tall (44px wide at least), `body` text. Chosen: `sel-bg` with `sel-ink`, weight 600. Locked: dashed, `muted`; a locked chosen chip shows `line` with `ink`.

**Rules**

- Chips choose; they never navigate and never act. A chip that triggers something is a Button.
- The row has a label above it that says what the choice means ("What fits the pot"), so a chosen chip is readable without the surrounding form.
- Options are two or three words in lowercase sentence form ("drain holes", "can be lifted"); no icons.
- Multi-select by default; for an exclusive choice of two to four modes use SegmentedControl.
- The consumer provides the options, the chosen set and the handler.
