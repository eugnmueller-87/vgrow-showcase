# Camera

The box camera's 16:9 frame on `side` with its placeholder, a REC badge while recording, and a row of controls under it.

**Markup**: `<div class="cam-frame"><img …> or <div class="cam-ph"><svg/><span>why there is no picture</span></div>[<span class="cam-badge"><i class="rec-dot"></i>REC 02:15</span>]</div><div class="cam-side"><div class="camera-zoom">…</div><button class="btn rec [on]"><i class="rec-dot"></i>Record</button>…<p class="camera-details">…</p></div>`.

**Tokens**

- Frame `side`, `radius-md`, `aspect-ratio: 16 / 9`, picture `object-fit: cover`. Placeholder text `side-ink`, its icon at 75%.
- Badge on `scrim` with `side-ink-strong` text in `caption` weight 700, letter-spaced; the dot `dot-bad`, blinking (off under reduced motion).
- Zoom control: `bg` track, `line-strong` border, `radius-md`, 36px buttons with `radius-sm`, hover `accent-soft`. The record button is a Button that turns `bad-fill` while recording.
- The details line is `small` `muted`.

**Rules**

- The placeholder says why there is no picture and when the next one comes; never an empty black box.
- The REC badge is the only overlay on the picture; no timestamps, no crosshairs on the frame.
- Controls sit under the frame in one wrapping row: zoom, record, fullscreen, then the details line across the full width.
- In fullscreen the frame fills the screen and the control row sits at the bottom on `card`.
- The consumer provides the picture or the placeholder reason, the recording state, the zoom state and the handlers.
