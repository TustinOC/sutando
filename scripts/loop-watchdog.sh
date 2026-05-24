#!/bin/bash
# loop-watchdog.sh — detect and surface stuck proactive-loop passes.
#
# The proactive loop writes core-status.json at every state transition.
# If status="running" but the timestamp hasn't advanced in THRESHOLD_SEC,
# the pass crashed mid-execution and the loop never re-armed. Tasks won't
# be processed until someone restarts the session.
#
# This script runs outside of Claude Code (e.g. via launchd TimerInterval
# or a system cron) so it fires even when the Claude session is hung or dead.
#
# Usage:
#   bash scripts/loop-watchdog.sh              # check + notify if stuck
#   bash scripts/loop-watchdog.sh --threshold 900  # override threshold (seconds)
#   bash scripts/loop-watchdog.sh --dry-run    # print status, no notification
#
# Setup (run once to install as a launchd job every 5 minutes):
#   bash scripts/loop-watchdog.sh --install
#
# Env vars:
#   SUTANDO_WORKSPACE  — workspace dir (default: ~/.sutando/workspace)

set -euo pipefail

THRESHOLD_SEC=900      # 15 minutes — normal pass finishes in < 5 min
DRY_RUN=0
INSTALL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --threshold) THRESHOLD_SEC="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --install)   INSTALL=1; shift ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"
# Load .env to pick up SUTANDO_WORKSPACE override
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"
fi

STATUS_FILE="$WORKSPACE/state/core-status.json"

# --- Install mode: write a launchd plist ---
if [ "$INSTALL" = "1" ]; then
    PLIST="$HOME/Library/LaunchAgents/com.sutando.loop-watchdog.plist"
    SCRIPT_ABS="$(cd "$SCRIPT_DIR" && pwd)/loop-watchdog.sh"
    cat > "$PLIST" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.sutando.loop-watchdog</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT_ABS}</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>StandardOutPath</key>
    <string>/tmp/sutando-loop-watchdog.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/sutando-loop-watchdog.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST_EOF
    launchctl load "$PLIST" 2>/dev/null || true
    echo "loop-watchdog: installed as $PLIST (fires every 5 minutes)"
    exit 0
fi

# --- Check mode ---
if [ ! -f "$STATUS_FILE" ]; then
    [ "$DRY_RUN" = "1" ] && echo "loop-watchdog: no core-status.json at $STATUS_FILE — skip"
    exit 0
fi

# Parse JSON without jq dependency
STATUS=$(python3 -c "
import json, sys
try:
    d = json.loads(open('$STATUS_FILE').read())
    print(d.get('status','?'))
except: print('error')
" 2>/dev/null)

TS=$(python3 -c "
import json, sys
try:
    d = json.loads(open('$STATUS_FILE').read())
    print(int(d.get('ts', 0)))
except: print(0)
" 2>/dev/null)

STEP=$(python3 -c "
import json, sys
try:
    d = json.loads(open('$STATUS_FILE').read())
    print(d.get('step','?'))
except: print('?')
" 2>/dev/null)

NOW=$(date +%s)
AGE=$((NOW - TS))
AGE_MIN=$((AGE / 60))

if [ "$DRY_RUN" = "1" ]; then
    echo "loop-watchdog: status=$STATUS ts=$TS age=${AGE}s step='$STEP' threshold=${THRESHOLD_SEC}s"
fi

# Only alert if status=running AND age > threshold
if [ "$STATUS" != "running" ]; then
    [ "$DRY_RUN" = "1" ] && echo "loop-watchdog: status='$STATUS' — not stuck"
    exit 0
fi

if [ "$AGE" -le "$THRESHOLD_SEC" ]; then
    [ "$DRY_RUN" = "1" ] && echo "loop-watchdog: running for ${AGE}s — within threshold, OK"
    exit 0
fi

# Loop is stuck
MSG="Proactive loop stuck for ${AGE_MIN}min on: $STEP"
echo "loop-watchdog: STUCK — $MSG"

if [ "$DRY_RUN" = "1" ]; then
    echo "loop-watchdog: (dry-run) would notify: $MSG"
    exit 0
fi

# Fire macOS notification (visible even when Claude is unresponsive)
osascript -e "display notification \"${MSG}. Restart may be needed.\" with title \"Sutando — Loop Watchdog\"" 2>/dev/null || true

# Write a sentinel file so health-check.py or a human can see the alert was fired
ALERT_FILE="$WORKSPACE/state/loop-stuck-alert.json"
python3 -c "
import json, time
data = {'ts': int(time.time()), 'age_sec': $AGE, 'step': '$STEP', 'alerted': True}
open('$ALERT_FILE', 'w').write(json.dumps(data))
" 2>/dev/null || true

exit 0
