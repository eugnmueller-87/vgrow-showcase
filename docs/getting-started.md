# Getting started

[Repository](../README.md) · [Documentation](README.md) · [German operating reference](operations.md)

## Start with the demo

The demo is useful for learning the interface and for development. It has synthetic readings, labelled example
images, and a separate state directory. It makes no VIVOSUN login and sends no hardware commands.

The project is developed with Python 3.12. From a checkout on macOS:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m vgrow --demo --host 127.0.0.1
```

Windows / PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m vgrow --demo --host 127.0.0.1
```

Open the authenticated address printed in the terminal. Its access key belongs to the demo. The default port is
8742; add `--port 18742` when another instance already occupies it. Ctrl+C stops the foreground server.

## Connect the real box

The current integration connects one VIVOSUN VGrow Y18 (`VSCTLY18`). You need an account with that box, an
always-on host with internet access, and a browser on your phone or computer. Google sign-in alone does not
give the background service a usable password: configure an email/password login for the account.

Create `~/.vgrow.env` outside the repository (`%USERPROFILE%\.vgrow.env` on Windows):

```dotenv
VIVOSUN_EMAIL=your-account-email
VIVOSUN_PASSWORD=your-account-password
```

Keep the file accessible only to the host's owner. Optional configuration includes:

| Setting | Purpose |
|---|---|
| `VGROW_LANG=de` or `en` | Initial server language; saved settings take precedence |
| `GROWCAM_IP` | Fixed local address for the GrowCam; reserve it in the router if used |
| `ECOWITT_IP` | Local address of an Ecowitt gateway with a WH51 soil sensor; without it there is no soil tile. Reserve it in the router |
| `NTFY_SERVER`, `VGROW_NTFY_TOPIC` | Optional push notification destination |
| `OLLAMA_URL` | Local inference service, default `http://127.0.0.1:11434` |
| `HANK_MODEL` / `VISION_MODEL` | Shared chat/vision model override |
| `HANK_EMBED_MODEL` | Optional semantic-retrieval embedding model |

Use the read-only connection check before starting control:

```sh
.venv/bin/python -m vgrow --check
```

The check logs in with its own connection identifiers, reads a snapshot, and exits without sending actuator
commands. Then start the foreground application with `.venv/bin/python -m vgrow`.

The normal server binds to `0.0.0.0` by default. Use a trusted LAN or Tailscale for access; the browser's shared
access key is the cockpit's authentication. It is not a separate account for each person. Keep the authenticated
link private. The [operating reference](operations.md) describes stored key and log behavior.

## Run continuously on macOS

From the checkout on the Mac:

```sh
bash scripts/install_mac.sh
```

The installer creates/reuses the Python environment, installs dependencies, and registers the launchd agent
`de.vgrow.control`. It restarts the service. Finish active recordings and pump work before running it on an
existing installation. Running it with `--demo` installs a demo service instead.

The log and state live under `~/.vgrow-control/`. Check service registration with:

```sh
launchctl list | grep de.vgrow.control
```

Use one real server per account. A Windows development server and Mac service do not share controller locks.
Keep the host awake and connected if you depend on its timers. A working browser connection is not proof that
the cloud-device path is available.

Updates to the checkout and service installation are separate steps. This repository also supports pushing a
checkout to a Mac over SSH; the existing deployment method is documented in [the operating reference](operations.md#einrichten-macbook-der-server).
There is no automatic GitHub-to-Mac deployment configured by this documentation.

## Add the cameras

GrowCam uses the VIVOSUN account and a local-network address. macOS may require a Local Network permission
for the relevant Python process. The camera has its own power supply in the documented installation, leaving
the box's USB ports for the pump and humidifier.

Connect the C920 to the Mac, using a data-capable USB adapter or hub where needed:

```sh
.venv/bin/python scripts/install_usb_camera.py --device 'HD Pro Webcam C920'
```

Allow the normal **VGrow Camera** macOS permission prompt from the logged-in desktop session. Restart the
cockpit service after configuration, then verify that each camera shows the intended view. Follow the full
[USB camera guide](usb-camera.md) for the helper lifecycle and capture settings.

## Enable local AI if wanted

Ollama runs separately from the cockpit. With Ollama installed and running, the default model configuration uses:

```sh
ollama pull gemma4:12b
ollama pull granite-embedding:278m
```

`HANK_MODEL` takes precedence over `VISION_MODEL`; both otherwise use the shared default. The embedding model
is optional: lexical retrieval remains available when semantic retrieval cannot run. Match model size to the
host's memory. See [measured results](hank-v1-messung.md) for the actual Mac's latency and memory constraints.

## First-use checklist

1. Verify that the box and plant probe readings are fresh and belong to the expected controller.
2. Use **Start here** to create/inspect plant records and dates. Unknown observed stage dates can remain unknown.
3. Confirm accessory assignments against the physical USB connections.
4. Inspect automation decisions in Observe before choosing Automatic. The humidifier has its own rule switch.
5. Test actual pump switching before enabling watering. A powered USB port does not measure delivered water.
6. Save and explicitly enable a light timer only after reviewing its times and brightness. It begins disabled.
7. Back up the whole state directory, including databases, media, and controller state.

These steps configure the software and verify hardware behavior. The application does not infer a water dose,
stage onset, or suitable daily schedule from a single photograph.
