# screen-share — agent shares the Mac screen into an AG2 Space call

Publishes the **full Mac screen** as a LiveKit SCREENSHARE track into a live
MatrixRTC call, so the owner watches the agent operate the computer in real
time (open apps, Gmail, web pages — GUI control runs in parallel via the
existing built-in macOS control tools). Owner decision 2026-06-12: full
screen, not a single window — everything visible on screen enters the stream.

Task room: `!IRFFSZUgpGLulIVNbg:ag2.space`.

## How it works

- LiveKit room name == the Matrix room id (same convention as the
  matrixrtc-conversation daemon on EC2).
- `mint-jwt-via-ec2.sh <matrix-room-id>` mints a short-lived LiveKit JWT as
  the agent (device `sutando-screen-mac`) — the Matrix-token exchange runs on
  EC2; only `{url, jwt}` reaches this machine.
- `publisher.mjs --url <url> --token <jwt>` joins as a second device of the
  agent (does NOT kick the daemon's audio connection — different device id →
  different LiveKit identity) and streams ffmpeg avfoundation capture
  (default 1280-wide, 10fps, cursor included) as I420 frames.
- Auto-stops when the call empties, or on SIGTERM.

## Share during a call

```bash
cd "$(dirname "$0")"   # skills/screen-share/
JWT_JSON=$(bash mint-jwt-via-ec2.sh '!roomid:ag2.space')
node publisher.mjs \
  --url  "$(echo "$JWT_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["url"])')" \
  --token "$(echo "$JWT_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["jwt"])')" \
  > /tmp/screen-share.log 2>&1 &
echo $! > /tmp/screen-share.pid
```

Stop: `kill "$(cat /tmp/screen-share.pid)"` (unpublishes + disconnects cleanly).

## Requirements

- `ffmpeg` (brew), Node 20+, `npm install` in this dir (`@livekit/rtc-node`).
- The invoking terminal must hold macOS **Screen Recording** permission (same
  grant src/screen-capture-server.py uses; `screencapture -x` succeeding is
  the probe).

## Known-unknown (verify step)

Cinny/Element Call may render the extra device's tile without a matching
`m.call.member` state event — confirmed working ⇒ done; if the tile is
hidden, fallback is daemon-proxy mode: ship frames to EC2 and push through
the daemon's existing `agent-video` SCREENSHARE track (proven to render).
