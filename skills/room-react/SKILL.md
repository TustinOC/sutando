# room-react — add / remove an agent's reaction on a room event

Closes the **reaction parity gap** vs a chat bot-client (`src/discord-bridge.py`
auto-reacts 👀 on receipt and removes it when the reply posts — an instant-ack
UX). Adds native Matrix `m.reaction` add + remove for an agent. Slice 3 of the
agent-capability parity epic (slices 1–2 = `room-read`, `room-media`).

**Usage:**
```bash
# ack receipt on the message that triggered the task (event_id = source_message_id)
python3 skills/room-react/react.py react '!room:hs' '$evt' --ack received --agent '@a:hs'
# flip to done, removing the receipt ack
python3 skills/room-react/react.py react   '!room:hs' '$evt' --ack done --agent '@a:hs'
python3 skills/room-react/react.py unreact '!room:hs' '$evt' --ack received --agent '@a:hs'
# or an arbitrary emoji
python3 skills/room-react/react.py react '!room:hs' '$evt' --key '🎉' --agent '@a:hs'
```

Named acks (`--ack`): `received` 👀 · `working` ⏳ · `done` ✅ · `fail` ⚠️ (any
emoji via `--key`). The event to react to is typically the task's
`source_message_id`. Returns JSON `{ok, room_id, event_id, key, reason}`;
`ok:false` + reason on any expected failure, never raises. CLI exits 0 for any
structured result; usage errors exit 2.

## Design — same boundary + pattern as room-read / room-media

- **Orthogonal to the task file bridge**; a separate synchronous call.
- **Relay-only client** — speaks only the `/v1` relay protocol, holds no
  platform/AppService token. The relay/broker (box-side) does the actual
  `m.reaction` send (`react`) and redact (`unreact`) + membership enforcement.
  - `POST {RELAY_URL}/v1/rooms/{room}/react`   `{event_id, key}`
  - `POST {RELAY_URL}/v1/rooms/{room}/unreact` `{event_id, key}`
- **Membership enforced relay-side** (`403` for a non-member); optional local
  `ROOM_REACT_GATE` is defense-in-depth, not the boundary.
- **Graceful degrade** — missing relay / gate-deny / `404` (verb unimplemented) /
  `403` / network → structured `ok:false`, never raises. Additive + versioned.
- **No platform literals**; relay coords from env/vault.

## Configuration

| env | meaning |
| --- | --- |
| `RELAY_URL` / `REMOTE_TASK_URL` | relay base |
| `RELAY_TOKEN` / `REMOTE_TASK_TOKEN` | relay bearer (optional) |
| `AGENT_MXID` | the agent identity (relay resolves membership) |
| `ROOM_REACT_GATE` | optional client gate JSON (defense-in-depth) |

## Tests

`python3 skills/room-react/test_react.py` — 16 unit tests: gate, react/unreact
endpoints, graceful degrade (404/403/network), arg validation, ack mapping, CLI
exit-0. No network.

## Status

- Client tool (react + unreact) + gate + tests: done (this skill).
- Relay-side `POST /v1/rooms/{room}/react|unreact` (membership-enforced,
  `m.reaction` send/redact) + live e2e: the paired box-side half, tracked in the
  epic. Remaining slice: delivery/routing markers (then Matrix-surpass).
