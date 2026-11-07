#!/bin/bash
# Hank's eval run with the model, by hand on the Mac (docs/hank-v1-spec.md 11.1, 10.4 WP11; addendum D10).
#
# Usage (on the Mac, inside the repo):
#   bash scripts/hank_eval_mac.sh [--url http://127.0.0.1:11434] [--case grow-01 ...] [--dense] [--no-save]
#
# What it does: only the cases that need the model (the active grow and live cases), one request at a time, at the
# lowest CPU priority (nice 19), and it stops after the first answer that takes longer than 240 s (spec 4.7). The
# result lands in STATE_DIR/hank/eval/ (<time>.json, latest.json, latest.md) like scripts/hank_eval.py.
#
# Limits, said plainly: this is a separate process, so it cannot take the cockpit's model lane (spec 4.9). Ollama
# runs one request at a time; while an eval answer runs (measured 45 to 73 s on 23.10.2026), a chat or a photo
# reading of the cockpit waits behind it. That is why it is not scheduled (addendum D10: no nightly run until it is
# measured that it does not disturb the other night jobs). scripts/de.vgrow.hank-eval.plist.template is a template
# only; nothing installs it.
#
# The server log is checked before and after: new ERROR lines or a traceback are counted and reported, never printed.
# The access key is never read.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no .venv in $REPO (run scripts/install_mac.sh first)" >&2; exit 2; }
LOG="$HOME/.vgrow-control/server.log"

URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
CASES=()
EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --case) CASES+=("--case" "$2"); shift 2 ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done
if [ ${#CASES[@]} -eq 0 ]; then
  # the cases that reach the model (route grow, status active or live_only), as of 23.10.2026
  for c in grow-01 grow-02 grow-03 grow-04 grow-05 grow-06 grow-07 grow-08 grow-17 \
           live-01 live-02 live-03 live-04 live-05 live-06; do
    CASES+=("--case" "$c")
  done
fi

errors_now() { if [ -f "$LOG" ]; then grep -E -c ' ERROR |Traceback' "$LOG" || true; else echo 0; fi; }
BEFORE="$(errors_now)"
if ! curl -fsS --max-time 3 "$URL/api/version" >/dev/null; then
  echo "Ollama does not answer at $URL" >&2
  exit 3
fi

set +e
nice -n 19 "$PY" scripts/hank_eval.py --model --url "$URL" --stop-after 240 "${CASES[@]}" ${EXTRA[@]+"${EXTRA[@]}"}
CODE=$?
set -e

AFTER="$(errors_now)"
if [ "$AFTER" -gt "$BEFORE" ]; then
  echo "server log: $((AFTER - BEFORE)) new ERROR or Traceback lines during the run (see $LOG)" >&2
fi
exit "$CODE"
