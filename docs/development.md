# Development and verification

[Repository](../README.md) · [Architecture](architecture.md) · [Documentation](README.md)

## Local environment

The application uses Python 3.12 and the packages in `requirements.txt`. The browser interface is HTML, CSS,
JavaScript and vendored uPlot; there is no npm build step for the cockpit.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
.venv/bin/python -m vgrow --demo --host 127.0.0.1
```

On Windows use `py -3.12` to create the environment and `.venv\Scripts\python.exe` for subsequent commands.
The demo is isolated from real state and hardware. It is the correct target for UI experiments and mutation tests.

## Follow the boundary being changed

| Area | Useful source / existing checks |
|---|---|
| Device protocol and reports | `vgrow/box.py`, `vgrow/vivosun/`, `tests/` |
| Light / pump scheduling | `vgrow/light_schedule.py`, `vgrow/watering.py`, `scripts/light_screens.py`, `scripts/watering_screens.py` |
| Water demand and calibration | `vgrow/thirst.py`, `tests/test_thirst.py`, `scripts/thirst_screens.py` (isolated demos) |
| Camera views | `vgrow/usb_camera.py`, `vgrow/camera.py`, `scripts/usb_camera_screens.py` |
| Plant records and history | `vgrow/workspace.py`, `vgrow/record_export.py`, `scripts/workspace_screens.py` |
| Navigation and layout | `web/cockpit.js`, `web/cockpit.css`, `scripts/cockpit_screens.py` |
| Wiki and reading tools | `vgrow/wiki.py`, `vgrow/wiki_tools.py`, `scripts/lexicon_screens.py`, `scripts/wiki_tools_screens.py` |
| Community | `vgrow/community.py`, `scripts/community_screens.py` |
| Hank and drawer | `vgrow/hank/`, `scripts/hank_panel_screens.py`, `scripts/hank_screens.py` |
| Issue/task protocol | `vgrow/hank_protocol.py`, `web/protocol.js`, `tests/test_hank_protocol.py`, `scripts/protocol_screens.py` |

The offline suite checks the existing application; test counts are intentionally not embedded in a badge that
would drift with the next commit. Browser checks use actual page routing and browser behavior in addition to
API-level tests.

## Browser checks

The browser scripts use the Chrome DevTools Protocol through `websockets`, with Microsoft Edge as the default
executable on Windows. `EDGE_PATH` selects another compatible executable. Read the selected script's module
docstring for its arguments and side effects.

For example, against a local authenticated demo:

```powershell
# Set VGROW_URL to your local demo address with its demo key before this command.
.venv\Scripts\python.exe scripts\cockpit_screens.py _shots\cockpit
```

The acceptance scripts refuse non-demo mutation targets. Some create test records in the demo state; use a
disposable demo state when checking lifecycle and persistence. Do not point mutation checks at the live box.
Authenticated URLs, cookies and personal environment files belong outside committed output.
`scripts/light_automatic_screens.py` checks the Automatic button, manual override and immediate resume through
the demo API, plus unavailable states and 16 DE/EN, light/dark and viewport combinations. Use a disposable local
demo with early flower confirmed in My Grow before running it. It sends three demo light commands and refuses
remote URLs and non-demo state. The backend timer tests cover dark-period resume and subsequent stage changes.

For JavaScript changes, run `node --check` on the changed scripts where Node is available. For Python changes,
use the relevant existing tests, then the offline suite when shared behavior changes. The project does not
currently include a GitHub Actions workflow, so the README does not display a CI-success badge.

For protocol browser acceptance, run `python scripts/protocol_fixture.py` in a second terminal. It creates a
temporary state directory, fake sensor timestamps and historical receipts, with no hardware or model connection.
Set `VGROW_URL=http://127.0.0.1:18748/?key=protocol-demo-only-not-real` only in the test terminal, then run
`python scripts/protocol_screens.py _shots/protocol-check`. The fixture key is public test data, never an
installation key. Restart the fixture for each run and stop it afterwards. The browser check sends one demo
exhaust command, injects later demo readings, verifies acceptance versus confirmation versus measured outcome,
old missing evidence, filters, pagination and cached-error display, then checks 16 DE/EN, light/dark and viewport
combinations. It rejects remote URLs and non-demo state. Real installation verification must use GET requests
only; never run this mutation check against live hardware.

## Documentation images

The checked-in gallery contains actual browser captures from the installation, not the old preview or demo
camera drawings. Every capture was inspected for framing, legibility and sensitive content. See
[the gallery's provenance](gallery.md#capture-provenance) and [asset notes](images/README.md).

When replacing an image, verify the camera source, wait for fonts and frames to load, capture enough resolution
for the intended display, and finish the crop at a meaningful card boundary. Do not enlarge an unsuitable source
or insert decorative placeholder photography. Keep failed states and incomplete setup honest in captions.

## Vendored and licensed material

Update the protocol integration from a recorded upstream revision using [UPSTREAM.md](../vgrow/vivosun/UPSTREAM.md).
Keep its license and uPlot's license. The application has no repository-wide license declaration; this documentation
does not grant new rights to original code, reference content or third-party photos.
