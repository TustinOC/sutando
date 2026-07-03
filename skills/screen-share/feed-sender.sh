#!/usr/bin/env bash
# feed-sender.sh — stream the Mac screen as 640x360 JPEG frames to the
# matrixrtc daemon's screen-feed file on EC2 (daemon-proxy screenshare mode).
#
# Why this path: Cinny only renders call participants announced via
# m.call.member, so a direct second-device LiveKit publisher is invisible.
# The daemon's own video track DOES render — this feeds it real frames
# (its loop prefers state/screen-feed/<identity>.jpg when fresh, see
# livekit-audio.ts B2 block; stale >10s falls back to the identity card).
#
# Usage: bash feed-sender.sh [fps]   (default 2)
set -euo pipefail
FPS="${1:-2}"
EC2="${SCREEN_SHARE_EC2:-ubuntu@34.222.153.133}"
KEY="${SCREEN_SHARE_KEY:-$HOME/.ssh/sutando-ag2-space.pem}"
HERE="$(cd "$(dirname "$0")" && pwd)"
FFMPEG="$HERE/node_modules/ffmpeg-static/ffmpeg"
# Daemon-side path: encodeURIComponent("@sutando-wu-air.agent:ag2.space").jpg
FEED='/srv/matrixrtc-conversation/state/screen-feed/%40sutando-wu-air.agent%3Aag2.space.jpg'

# ffmpeg -list_devices always exits non-zero — guard it or set -e kills us here.
SCREEN_IDX=$({ "$FFMPEG" -f avfoundation -list_devices true -i "" 2>&1 || true; } \
  | sed -n 's/.*\[\([0-9]\)\] Capture screen 0.*/\1/p' | head -1)
: "${SCREEN_IDX:?no capture screen device — Screen Recording permission?}"

# Letterbox the screen into exactly 640x360 (the daemon's VideoSource size).
# (no exec — exec inside a pipeline runs in a subshell and broke nohup'd runs)
# Supervised: a dropped ssh leg (broken pipe killed the feed mid-session
# 2026-06-12 20:21) restarts within 2s instead of silently dying — the daemon
# falls back to the identity card after 10s stale, so a blip self-heals.
while true; do
"$FFMPEG" -hide_banner -loglevel error \
  -f avfoundation -capture_cursor 1 -framerate "$FPS" -i "${SCREEN_IDX}:none" \
  -vf "fps=${FPS},scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2" \
  -q:v 7 -f image2pipe -vcodec mjpeg - \
| ssh -i "$KEY" -o BatchMode=yes -o ServerAliveInterval=15 "$EC2" "python3 -u -c '
import os, sys
FEED = \"$FEED\"
buf = b\"\"
r = sys.stdin.buffer
while True:
    chunk = r.read(65536)
    if not chunk:
        break
    buf += chunk
    while True:
        eoi = buf.find(b\"\xff\xd9\")
        if eoi < 0:
            break
        frame, buf = buf[:eoi+2], buf[eoi+2:]
        soi = frame.find(b\"\xff\xd8\")
        if soi < 0:
            continue
        tmp = FEED + \".tmp\"
        with open(tmp, \"wb\") as f:
            f.write(frame[soi:])
        os.replace(tmp, FEED)
'" || true
echo "[feed-sender] pipeline exited $(date) — restarting in 2s" >&2
sleep 2
done
