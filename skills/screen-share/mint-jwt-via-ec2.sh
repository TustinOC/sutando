#!/usr/bin/env bash
# mint-jwt-via-ec2.sh <matrix-room-id> [device-id]
#
# Mints a LiveKit JWT for the given Matrix room AS the wu-air agent, running
# the whole exchange (bot token → OpenID token → lk-jwt-service) ON the EC2
# box so no Matrix credential ever lands on this machine (WORKER-PROTOCOL).
# Prints the lk-jwt-service response JSON: {"url": "wss://...", "jwt": "..."}.
#
# The JWT is short-lived and scoped to one room — safe to pass to
# publisher.mjs on the command line.
set -euo pipefail
ROOM="${1:?usage: mint-jwt-via-ec2.sh <matrix-room-id> [device-id]}"
DEVICE="${2:-sutando-screen-mac}"
EC2="${SCREEN_SHARE_EC2:-ubuntu@34.222.153.133}"
KEY="${SCREEN_SHARE_KEY:-$HOME/.ssh/sutando-ag2-space.pem}"

ssh -i "$KEY" -o BatchMode=yes "$EC2" sudo python3 - "$ROOM" "$DEVICE" <<'PY'
import json, subprocess, sys, urllib.parse, urllib.request

room, device = sys.argv[1], sys.argv[2]
agent = "@sutando-wu-air.agent:ag2.space"
bot = json.load(open("/home/ubuntu/ag2space-task-broker/relay-agents.json"))[agent]["bot_token"]

openid_url = ("https://chat.ag2.space/_matrix/client/v3/user/"
              + urllib.parse.quote(agent, safe="") + "/openid/request_token")
req = urllib.request.Request(openid_url, data=b"{}", method="POST", headers={
    "Authorization": f"Bearer {bot}", "content-type": "application/json",
    "User-Agent": "sutando-screen-share/1.0"})
openid = json.load(urllib.request.urlopen(req, timeout=10))

body = json.dumps({"room": room, "openid_token": openid, "device_id": device}).encode()
req = urllib.request.Request("https://chat.ag2.space/livekit/jwt/sfu/get", data=body,
                             headers={"content-type": "application/json",
                                      "User-Agent": "sutando-screen-share/1.0"})
print(urllib.request.urlopen(req, timeout=10).read().decode())
PY
