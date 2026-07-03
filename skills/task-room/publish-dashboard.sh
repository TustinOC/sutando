#!/usr/bin/env bash
# publish-dashboard.sh — regenerate the task-dashboard data and push it to the
# EC2 box so https://ag2.space/tasks reflects current task-room state.
# Idempotent + fail-soft: safe to call on every autonomous loop tick.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KEY="${SUTANDO_AG2_KEY:-$HOME/.ssh/sutando-ag2-space.pem}"
HOST="${SUTANDO_AG2_HOST:-ubuntu@34.222.153.133}"
DEST="/home/ubuntu/sutando-infra/data/element-web/tasks/data.json"
TMP="/tmp/tasks-data.json"

python3 "$HERE/dashboard-data.py" -o "$TMP" >/dev/null
# copy to box tmp, then into the served dir (needs sudo for the root-owned dir)
scp -q -i "$KEY" -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$TMP" "$HOST:/tmp/tasks-data.json"
ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" "sudo cp /tmp/tasks-data.json '$DEST'"
echo "dashboard data published ($(wc -c < "$TMP") bytes)"
