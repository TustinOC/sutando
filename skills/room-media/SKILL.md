# room-media — native media send/fetch for an agent

Closes the **native-media parity gap** vs a chat bot-client (e.g. `src/discord-bridge.py`,
which does inbound `att.save`→inbox and outbound `[file:]` upload). URL-in-text
image rendering already works on Matrix; this adds *native uploaded media* both
ways. Slice 2 of the agent-capability parity epic (slice 1 = `room-read`).

**Usage:**
```bash
# inbound: fetch a shared media ref -> a local path the agent can read
python3 skills/room-media/media.py fetch 'mxc://hs/abc' --room '!room:hs' --agent '@a:hs'
# outbound: upload a local file into a room (relay posts it as the agent)
python3 skills/room-media/media.py send '!room:hs' /path/to/pic.png --agent '@a:hs' --caption 'fig 1'
```

Returns JSON `{ok, room_id, ref, path, bytes, reason}`. `ok:false` + `reason` on
any expected failure; never raises. The CLI **exits 0 for any structured result**
(a graceful no-op is not a failed task); usage errors exit 2.

## Design — same boundary + pattern as `room-read`

- **Orthogonal to the task file bridge** (`tasks/`→`results/`): a separate
  synchronous call, the async loop is untouched.
- **Relay-only client.** It speaks only the stable relay `/v1` protocol and holds
  no platform/AppService token. The relay/broker (box-side) owns the platform
  creds and does the actual Matrix media-repo upload/download + membership
  enforcement.
  - inbound: `GET {RELAY_URL}/v1/media/fetch?ref=…&room_id=…` → bytes → saved to
    the inbox → local path returned (mirrors discord's `att.save`).
  - outbound: `POST {RELAY_URL}/v1/rooms/{room}/media` with the file base64'd in
    a JSON body (stdlib-friendly); the relay decodes + uploads + posts.
- **Membership enforced relay-side** (a non-member fetch/upload → `403`); the
  optional local gate (`ROOM_MEDIA_GATE`, same shape as room-read's) is
  defense-in-depth, not the boundary.
- **Graceful degrade.** Missing relay config, gate-deny, oversize, disallowed
  path, `404` (verb unimplemented), `403` (not a member), network → structured
  `ok:false`, never raises. Additive + versioned: a relay without the verb just
  `404`s and the client no-ops.

## Safety rails (outbound)

- **Path allowlist** — outbound files must live under a prefix in
  `ROOM_MEDIA_ALLOW` (`os.pathsep`-separated; default: the OS temp dir + an
  optional `ROOM_MEDIA_INBOX`). A caller can't upload `/etc/passwd` or arbitrary
  local files.
- **Size ceiling** — `MAX_BYTES` (25 MiB) on both fetch and send, so a caller
  can't push/pull a huge blob.

## Configuration

| env | meaning |
| --- | --- |
| `RELAY_URL` / `REMOTE_TASK_URL` | relay base |
| `RELAY_TOKEN` / `REMOTE_TASK_TOKEN` | relay bearer (optional) |
| `AGENT_MXID` | the agent identity (relay resolves membership) |
| `ROOM_MEDIA_INBOX` | where fetched media is written (default: OS temp `sutando-media-inbox`) |
| `ROOM_MEDIA_ALLOW` | allowed outbound path prefixes (default: OS temp + inbox) |
| `ROOM_MEDIA_GATE` | optional client gate JSON (defense-in-depth) |

No platform token on the client — the AppService/bot creds stay box-side.

## Tests

`python3 skills/room-media/test_media.py` — 25 unit tests: gate, outbound path
allowlist + size, fetch/send relay verbs, graceful degrade (404/403/network/
oversize), CLI exit-0. No network.

## Status

- Client tool (fetch + send) + gate + safety rails + tests: done (this skill).
- Relay-side `/v1/media/fetch` + `POST /v1/rooms/{room}/media` (membership-
  enforced, Matrix media-repo) + live e2e: the paired half, tracked in the epic.
- Remaining parity slices: reactions, delivery/routing markers (then
  Matrix-surpass).
