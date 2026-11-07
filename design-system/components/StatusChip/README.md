# StatusChip

A pill that names a state with a dot and a word; the only way a status is shown in a header row.

**Markup**: `<span class="status [ok|warn|info|bad]">Word</span>`. On the sidebar or phone top bar: `class="status on-side [ok|bad|demo]"`.

**States and tokens**

- `ok`: `accent-strong` on `accent-soft`: Monitoring, Online, On target.
- `warn`: `warn` on `warn-soft`: Needs you, Setup needed, set up the pot.
- `info`: `info` on `info-soft`: Paused until 9:37 AM, Waiting for the stage, Suggestion.
- `bad`: `bad` on `bad-soft`: Stopped: humidity did not rise, Offline, Failed.
- no class: `ink` on `line`: Off, Unknown.
- `on-side`: `side-fill` with `side-ink-strong`; the dot takes `dot-ok`, `dot-bad`, `dot-demo`.

**Rules**

- The word is the status; the colour repeats it. Never a chip without text, never a dot alone.
- Text is `small-strong` (13/600); the dot is 8px of the ink. Height 24px, `radius-pill`.
- Counts ("1 needs you") are the same chip; a row of them is `.counts`.
- One chip per device per row. A chip does not open anything; if the state needs a next step, the step is a Button under it.
- The consumer provides the state class and the text; include the time or reason in the text when it exists ("Paused until 9:37 AM").
