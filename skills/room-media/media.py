#!/usr/bin/env python3
"""room-media — send / fetch native media for an agent, via the relay.

Closes the native-media parity gap vs a chat bot-client (e.g. discord-bridge,
which does inbound `att.save`→inbox and outbound `[file:]` upload). URL-in-text
rendering already works on Matrix; this adds **native uploaded media** both ways.

Two operations, both orthogonal to the task file bridge (tasks/ -> results/):

  - fetch_media(ref)        inbound  — ask the relay to fetch a shared m.file /
                                       m.image and hand the agent a LOCAL PATH
                                       (like discord's att.save -> inbox).
  - send_media(room, path)  outbound — agent uploads a file/image; the relay does
                                       the Matrix media upload + posts it.

## Architecture boundary (same as room-read)

The local client speaks ONLY the stable relay `/v1` protocol; it holds no
platform/AppService token and never talks to a homeserver directly. The
relay/broker (box-side) owns the platform creds and does the actual Matrix media
repo upload/download + membership enforcement.

    Matrix room <-> relay/broker/AppService <-> /v1 protocol <-> this client
                 <-> tasks/results file queue <-> agent core

Membership is enforced relay-side (a non-member upload/fetch is denied 403); the
optional local gate (`ROOM_MEDIA_GATE`) is a defense-in-depth pre-filter, not the
boundary. Graceful degrade: missing relay config, gate-deny, oversize, path not
allowed, 404 (verb unimplemented), 403 (not a member), network error all return a
structured `ok:false` result and never raise. The CLI exits 0 for any structured
result (a graceful no-op is not a failed task); usage errors exit 2.

Outbound paths are constrained to an allowlist of directory prefixes
(`ROOM_MEDIA_ALLOW`, default: the workspace inbox + the OS temp dir) and a size
ceiling (`MAX_BYTES`), so a caller can't exfiltrate arbitrary local files or push
a huge upload.

No platform literals in this file — relay URL/token come from env/vault.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 25 * 1024 * 1024  # 25 MiB upload ceiling
HTTP_TIMEOUT = 60  # media can be larger than a text read


def _result(ok, *, path=None, ref=None, room_id=None, reason=None, bytes_=None):
    return {"ok": bool(ok), "room_id": room_id, "ref": ref, "path": path,
            "bytes": bytes_, "reason": reason}


# --------------------------------------------------------------------------- #
# Optional client gate (defense-in-depth; relay is the real enforcer)
# --------------------------------------------------------------------------- #
def _gate_path():
    return os.environ.get("ROOM_MEDIA_GATE") or os.path.join(os.getcwd(), "room-media-gate.json")


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
# Outbound path allowlist
# --------------------------------------------------------------------------- #
def _allowed_prefixes():
    env = os.environ.get("ROOM_MEDIA_ALLOW")
    if env:
        return [os.path.realpath(p) for p in env.split(os.pathsep) if p]
    prefixes = [os.path.realpath(tempfile.gettempdir())]
    inbox = os.environ.get("ROOM_MEDIA_INBOX")
    if inbox:
        prefixes.append(os.path.realpath(inbox))
    return prefixes


def _path_allowed(path):
    real = os.path.realpath(path)
    return any(real == p or real.startswith(p + os.sep) for p in _allowed_prefixes())


# --------------------------------------------------------------------------- #
# Relay coordinates
# --------------------------------------------------------------------------- #
def _relay():
    base = (os.environ.get("RELAY_URL") or os.environ.get("REMOTE_TASK_URL") or "").rstrip("/")
    token = os.environ.get("RELAY_TOKEN") or os.environ.get("REMOTE_TASK_TOKEN")
    headers = {"User-Agent": "sutando-room-media/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return base, headers


def _http(method, url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def _degrade_reason(code):
    if code == 404:
        return "verb unimplemented (404)"
    if code in (401, 403):
        return f"denied — agent not a joined member ({code})"
    return f"HTTP {code}"


# --------------------------------------------------------------------------- #
# Inbound: fetch a shared media ref -> local path
# --------------------------------------------------------------------------- #
def _inbox_dir(dest_dir=None):
    d = dest_dir or os.environ.get("ROOM_MEDIA_INBOX") or os.path.join(tempfile.gettempdir(), "sutando-media-inbox")
    os.makedirs(d, exist_ok=True)
    return d


def fetch_media(ref, agent_mxid=None, room_id=None, *, gate=None, dest_dir=None):
    """Ask the relay to fetch media `ref` for the agent and save it locally.

    Returns {ok, path, bytes, reason}. `path` is a local file the agent can read.
    """
    agent_mxid = agent_mxid or os.environ.get("AGENT_MXID")
    if not ref:
        return _result(False, reason="no media ref given", room_id=room_id)
    gate = load_gate() if gate is None else gate
    if not gate_allows(agent_mxid, room_id, gate):
        return _result(False, ref=ref, room_id=room_id,
                       reason=f"client gate denied for {agent_mxid}")
    base, headers = _relay()
    if not base:
        return _result(False, ref=ref, room_id=room_id, reason="no RELAY_URL configured")
    q = {"ref": ref}
    if room_id:
        q["room_id"] = room_id
    url = f"{base}/v1/media/fetch?" + urllib.parse.urlencode(q)
    try:
        status, body, hdrs = _http("GET", url, headers)
    except urllib.error.HTTPError as e:
        return _result(False, ref=ref, room_id=room_id, reason=_degrade_reason(e.code))
    except (urllib.error.URLError, TimeoutError) as e:
        return _result(False, ref=ref, room_id=room_id, reason=f"network error: {e}")
    if len(body) > MAX_BYTES:
        return _result(False, ref=ref, room_id=room_id,
                       reason=f"media exceeds {MAX_BYTES} bytes")
    # filename: prefer a relay-provided one, else derive from ref
    fname = _safe_name(hdrs.get("X-Media-Filename") or os.path.basename(urllib.parse.urlparse(ref).path) or "media.bin")
    out = os.path.join(_inbox_dir(dest_dir), fname)
    try:
        with open(out, "wb") as f:
            f.write(body)
    except OSError as e:
        return _result(False, ref=ref, room_id=room_id, reason=f"write failed: {e}")
    return _result(True, path=out, ref=ref, room_id=room_id, bytes_=len(body))


def _safe_name(name):
    name = os.path.basename(name or "media.bin").replace("\x00", "")
    return name or "media.bin"


# --------------------------------------------------------------------------- #
# Outbound: upload a local file -> posted into the room by the relay
# --------------------------------------------------------------------------- #
def send_media(room_id, path, agent_mxid=None, *, gate=None, caption=None):
    """Upload a local file via the relay, which posts it as the agent.

    The file body is base64-encoded into a JSON POST (stdlib-friendly; the relay
    decodes + does the Matrix media-repo upload). Returns {ok, reason}.
    """
    agent_mxid = agent_mxid or os.environ.get("AGENT_MXID")
    if not room_id:
        return _result(False, reason="no room_id given")
    if not path or not os.path.isfile(path):
        return _result(False, room_id=room_id, reason="file not found")
    if not _path_allowed(path):
        return _result(False, room_id=room_id, path=path,
                       reason="path not in ROOM_MEDIA_ALLOW")
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return _result(False, room_id=room_id, reason=f"stat failed: {e}")
    if size > MAX_BYTES:
        return _result(False, room_id=room_id, path=path,
                       reason=f"file exceeds {MAX_BYTES} bytes")
    gate = load_gate() if gate is None else gate
    if not gate_allows(agent_mxid, room_id, gate):
        return _result(False, room_id=room_id, reason=f"client gate denied for {agent_mxid}")
    base, headers = _relay()
    if not base:
        return _result(False, room_id=room_id, reason="no RELAY_URL configured")
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError as e:
        return _result(False, room_id=room_id, reason=f"read failed: {e}")
    payload = json.dumps({
        "filename": _safe_name(os.path.basename(path)),
        "content_b64": base64.b64encode(content).decode("ascii"),
        "caption": caption,
    }).encode()
    headers = {**headers, "Content-Type": "application/json"}
    url = f"{base}/v1/rooms/{urllib.parse.quote(room_id, safe='')}/media"
    try:
        status, body, _ = _http("POST", url, headers, data=payload)
    except urllib.error.HTTPError as e:
        return _result(False, room_id=room_id, path=path, reason=_degrade_reason(e.code))
    except (urllib.error.URLError, TimeoutError) as e:
        return _result(False, room_id=room_id, path=path, reason=f"network error: {e}")
    return _result(True, room_id=room_id, path=path, bytes_=size)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Send/fetch native media for an agent via the relay (gated).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="fetch a shared media ref -> local path")
    f.add_argument("ref")
    f.add_argument("--room", dest="room_id", default=None)
    f.add_argument("--agent", dest="agent_mxid", default=os.environ.get("AGENT_MXID"))
    s = sub.add_parser("send", help="upload a local file into a room")
    s.add_argument("room_id")
    s.add_argument("path")
    s.add_argument("--agent", dest="agent_mxid", default=os.environ.get("AGENT_MXID"))
    s.add_argument("--caption", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        res = fetch_media(args.ref, args.agent_mxid, args.room_id)
    else:
        res = send_media(args.room_id, args.path, args.agent_mxid, caption=args.caption)
    print(json.dumps(res, indent=2))
    return 0  # structured result -> exit 0 (graceful no-op is not a failed task)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
