#!/usr/bin/env python3
"""room-react — add / remove an agent's reaction on a room event, via the relay.

Closes the **reaction parity gap** vs a chat bot-client (discord-bridge auto-reacts
👀 on receipt and removes it when the reply posts — an instant-ack UX). This adds
native Matrix `m.reaction` add + remove for an agent.

The event to react to is typically the task's `source_message_id` (the message
that triggered the agent), so the agent can ack receipt (👀 / ⏳) immediately and
flip to done (✅) / fail (❌) when finished.

Two operations, orthogonal to the task file bridge (tasks/ -> results/):

  - react(room, event_id, key)    add an m.reaction (key = emoji) as the agent
  - unreact(room, event_id, key)  remove (redact) the agent's own reaction

## Architecture boundary (same as room-read / room-media)

The local client speaks ONLY the stable relay `/v1` protocol; it holds no
platform/AppService token and never talks to a homeserver directly. The
relay/broker (box-side) owns the creds and does the actual `m.reaction` send +
redact + membership enforcement. Membership is enforced relay-side (a non-member
reaction is denied 403); the optional local gate (`ROOM_REACT_GATE`) is
defense-in-depth, not the boundary.

Graceful degrade: missing relay config, gate-deny, 404 (verb unimplemented), 403
(not a member), network → structured `ok:false`, never raises. The CLI exits 0
for any structured result; usage errors exit 2. No platform literals here.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HTTP_TIMEOUT = 15

# Convenience ack keys (the function still accepts any emoji/key).
ACK = {"received": "👀", "working": "⏳", "done": "✅", "fail": "⚠️"}


def _result(ok, *, room_id=None, event_id=None, key=None, reason=None):
    return {"ok": bool(ok), "room_id": room_id, "event_id": event_id, "key": key, "reason": reason}


# --------------------------------------------------------------------------- #
# Optional client gate (defense-in-depth; relay is the real enforcer)
# --------------------------------------------------------------------------- #
def _gate_path():
    return os.environ.get("ROOM_REACT_GATE") or os.path.join(os.getcwd(), "room-react-gate.json")


def load_gate(path=None):
    """Missing file -> None (defer to relay). Present -> dict (default-deny)."""
    path = path or _gate_path()
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return {}


def gate_allows(agent_mxid, room_id, gate):
    if gate is None:
        return True  # no client gate -> relay enforces membership
    entry = gate.get(agent_mxid)
    if not isinstance(entry, dict):
        return False
    if room_id and room_id in (entry.get("rooms") or []):
        return True
    return bool(entry.get("all_member_rooms"))


# --------------------------------------------------------------------------- #
# Relay
# --------------------------------------------------------------------------- #
def _relay():
    base = (os.environ.get("RELAY_URL") or os.environ.get("REMOTE_TASK_URL") or "").rstrip("/")
    token = os.environ.get("RELAY_TOKEN") or os.environ.get("REMOTE_TASK_TOKEN")
    headers = {"User-Agent": "sutando-room-react/1", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return base, headers


def _http_post(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.status, resp.read()


def _degrade(code):
    if code == 404:
        return "verb unimplemented (404)"
    if code in (401, 403):
        return f"denied — agent not a joined member ({code})"
    return f"HTTP {code}"


def _op(verb, room_id, event_id, key, agent_mxid, gate):
    agent_mxid = agent_mxid or os.environ.get("AGENT_MXID")
    if not room_id or not event_id or not key:
        return _result(False, room_id=room_id, event_id=event_id, key=key,
                       reason="room_id, event_id and key are all required")
    gate = load_gate() if gate is None else gate
    if not gate_allows(agent_mxid, room_id, gate):
        return _result(False, room_id=room_id, event_id=event_id, key=key,
                       reason=f"client gate denied for {agent_mxid}")
    base, headers = _relay()
    if not base:
        return _result(False, room_id=room_id, event_id=event_id, key=key,
                       reason="no RELAY_URL configured")
    url = f"{base}/v1/rooms/{urllib.parse.quote(room_id, safe='')}/{verb}"
    try:
        _http_post(url, headers, {"event_id": event_id, "key": key})
    except urllib.error.HTTPError as e:
        return _result(False, room_id=room_id, event_id=event_id, key=key, reason=_degrade(e.code))
    except (urllib.error.URLError, TimeoutError) as e:
        return _result(False, room_id=room_id, event_id=event_id, key=key, reason=f"network error: {e}")
    return _result(True, room_id=room_id, event_id=event_id, key=key)


def react(room_id, event_id, key, agent_mxid=None, *, gate=None):
    """Add an m.reaction (key = emoji) on `event_id` as the agent."""
    return _op("react", room_id, event_id, key, agent_mxid, gate)


def unreact(room_id, event_id, key, agent_mxid=None, *, gate=None):
    """Remove (redact) the agent's own `key` reaction on `event_id`."""
    return _op("unreact", room_id, event_id, key, agent_mxid, gate)


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Add/remove an agent reaction on a room event via the relay (gated).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("react", "unreact"):
        p = sub.add_parser(name)
        p.add_argument("room_id")
        p.add_argument("event_id")
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--key", help="emoji / reaction key")
        g.add_argument("--ack", choices=sorted(ACK), help="named ack key (received/working/done/fail)")
        p.add_argument("--agent", dest="agent_mxid", default=os.environ.get("AGENT_MXID"))
    args = ap.parse_args(argv)
    key = args.key or ACK[args.ack]
    fn = react if args.cmd == "react" else unreact
    res = fn(args.room_id, args.event_id, key, args.agent_mxid)
    print(json.dumps(res, indent=2))
    return 0  # structured result -> exit 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
