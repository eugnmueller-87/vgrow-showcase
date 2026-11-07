# Cockpit redesign

The frontend stays framework-free: semantic HTML, shared CSS tokens, JavaScript and the existing uPlot charts.
`app.js` owns data, localization and commands; `cockpit.js` owns presentation and hash routes. The Python/aiohttp
API, VIVOSUN REST/MQTT integration, persisted plant data, feeding calculations and controller algorithms stay intact.

## Feature preservation map

| Capability | New location |
| --- | --- |
| Probe/box temperature, humidity, VPD, wet/dry or level sensor | Overview metrics; Analytics |
| Live/still camera, recording, recording list and constraints | Overview; Devices camera detail |
| Plant profile, stage timeline, dates, height, presets | My Grow / Growth |
| Water pH/EC, measurements and water log | My Grow / Growth |
| Product selection, chart confirmation, weekly dose, liters calculator, completed feedings, sources | My Grow / Feeding |
| AI photograph, assessment, evidence, limitations, run and stage acceptance | My Grow / AI Insights |
| Progress photographs | My Grow / Journal |
| Off/Observe/Automatic, device reasons, setup, thresholds, ownership, undo, complete returned log | Automation |
| Light, spectrum, fan speed, natural wind limitation, USB1 pump, USB2 humidifier | Devices |
| Light overview tile, reported plan status and stored light reports | Overview → Devices / Light schedule (`#devices/light`); brightness controls remain under Controls |
| Separate climate/VPD/water charts, probe/box, target bands, series toggles | Analytics |
| Active alerts and healthy count | Alerts & Activity / Active |
| All returned automation events, including acknowledgement states and undo | Alerts & Activity / History |
| Original alert conditions, push status and push test | Alerts & Activity / Rules & Notifications |
| Language, Auto/Light/Dark, connection/device identity, attribution | Settings; global status bar |
| Hank chat, ownership, contextual steps and confirmations | Existing persistent Hank drawer |
| News and complete Wiki with index | Sidebar resources; existing views |
| VIVOSUN Community, official website sign-in, Facebook and Discord | Sidebar resources / Community |

The Light tile replaces the former Automation tile. Automation remains in the sidebar and the overview summary.
The Light schedule page distinguishes a reported brightness level from daily timing. The controller snapshot supplies
a plan-active flag, not verified switch times; unavailable on/off times, daily duration and next switch stay unknown.
The past 24-hour bar reads local light reports, marks gaps and never projects a future schedule. This view sends no
actuator command. The generic controller and existing algorithms are unchanged.

## Boundaries

- Hash routes work on the existing authenticated server and preserve browser Back/Forward. Legacy anchors resolve
  to their new pages, including links from Hank. Navigation never sends actuator commands.
- A single camera element moves between Overview and its Devices detail; no duplicate MJPEG connections.
- Sensor and device freshness remain distinct. API failure marks the cached snapshot unavailable and pauses controls.
- Device command outcomes still come from `/api/state.pending`, not a successful HTTP submission alone.
- The API clamps history to seven days: no fictitious 30-day choice. Alert configuration continues to use the
  existing installation configuration; no inert editor is introduced. Activity shows the complete log returned by
  the server, not an invented lifetime audit archive.
- The alert API does not return activation timestamps. The UI reports the actual trigger conditions and separate
  readings/device ages, without manufacturing alert times. Notifications and integration credentials retain the
  existing server-side configuration rather than presenting unsupported save forms.
- Separate stage/date/height saves retain their individual confirmation and validation semantics. AI suggestions
  never overwrite the user's stage on page load.

## Verification

Run the offline pytest suite and `node --check` on changed scripts. `scripts/cockpit_screens.py` exercises real
browser routing, themes, languages, responsive layouts, status/error states and command acknowledgements against
an explicitly marked local demo. The demo uses its own state and never connects to VIVOSUN hardware.

The browser matrix covers 360 page/layout combinations (15 destinations × 4 sizes × 2 languages × 3 themes),
with screenshots, Back/Forward, legacy bookmarks, mobile focus containment, pending/confirmed/timeout/rejected
command states, disconnected hardware, stale and incomplete readings, API failure and recovery. It verifies that
navigation issues no POST requests, and checks the semantic text colors against the AA contrast threshold.
`ruff check scripts/cockpit_screens.py` passes. The existing `server.py` has three E702 findings in its command
handler, also present in the original revision; the only backend edit here registers the two new static assets.

Hank's chat has a permanent title/close row above its independently scrolling mode controls and conversation.
Its layer sits above the cockpit header. At 1280px and wider it reserves dashboard space; smaller screens use
a dismissible overlay with contained keyboard focus. Close, Escape and outside taps restore access to the page
without changing chat mode or automation. `scripts/hank_panel_screens.py` checks 108 page/layout combinations
with actual pointer clicks, long conversations, short/landscape screens, DE/EN and light/dark themes.

Community has since grown from an outbound entry point into a native public-metadata feed with search,
saved items, source health and cached results. Full provider pages and account sign-in open in top-level
companion windows after an explicit click. The earlier cross-site login preview was removed after repeated
cookie and sign-in failures. The cockpit does not receive social credentials or claim private-feed access.
Current source, image-quality, caching, window and video behavior is documented in [Community desk](community-hub.md).
