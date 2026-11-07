#!/bin/bash
# Installs VGrow Cockpit (unofficial) as an always-on launchd agent on a Mac.
# Usage (on the Mac, inside the repo):  bash scripts/install_mac.sh [--demo]
# Again after code changes: the same command, it rebuilds nothing it does not need and restarts.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
PY="${PY:-/usr/local/bin/python3.12}"  # override: PY=/path/to/python3 bash scripts/install_mac.sh
[ -x "$PY" ] || PY="$(command -v python3.12 || command -v python3)"
LABEL=de.vgrow.control
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/.vgrow-control/server.log"
EXTRA="${1:-}"

mkdir -p "$HOME/.vgrow-control"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt

ARGS="<string>$REPO/.venv/bin/python</string><string>-m</string><string>vgrow</string>"
[ "$EXTRA" = "--demo" ] && ARGS="$ARGS<string>--demo</string>"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>$ARGS</array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

# bootstrap + kickstart: a plain "launchctl load" over SSH registers the job but never starts it
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
sleep 1

# Log redaction, while the server is stopped: older versions printed the phone link with the access key into
# the log (start banner, access log). Every key=<value> becomes key=<redacted>; the same file is rewritten in
# place (same inode and permissions). Without a log there is nothing to do.
if [ -f "$LOG" ]; then
  sed -E "s/key=[^[:space:]&\"'<>]+/key=<redacted>/g" "$LOG" > "$LOG.redacting"
  cat "$LOG.redacting" > "$LOG"
  rm -f "$LOG.redacting"
fi
# The log is for the owner only, like the key file next to it: launchd would create it 0644, and it keeps the
# permissions of a file that already exists.
touch "$LOG"
chmod 600 "$LOG"

launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart -k "$DOMAIN/$LABEL"
echo "launchd $LABEL geladen ${EXTRA:+($EXTRA)}. Log: $LOG"
