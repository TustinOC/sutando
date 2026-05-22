"""Small helper for pushing text into an active discord-voice session.

Used by the Discord bridge (and any other Python integration) to inject text
the bridge delivers into a Discord channel into Gemini Live's conversation
context when a discord-voice session is live in that same channel. No-op when
no voice session is reachable — the message still posts to the channel through
the regular delivery path.

discord-voice-server (skills/discord-voice/scripts/discord-voice-server.ts) has
no `messageCreate` handler, so text posted in its voice channel never reaches
the voice agent. This helper closes that gap by injecting the text directly
into the live VoiceSession's conversation context.

Contract mirrors `vision_push.py` (which pushes image frames into voice via the
vision-tools HTTP control server). The discord-voice-server exposes a tiny
localhost-only control server:
  - GET  /voice/state  →  {channelId, sessionReady}   liveness + which channel
  - POST /voice/text   {text, source}                 inject one text turn

Single-shot helper: checks the session is up AND serving the target channel,
then POSTs the text. Fail-soft on every error path — a push failure must never
break message delivery.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


# discord-voice-server's text control server. 7845 is screen-capture, 7846 is
# credential-proxy, 7847 is the vision control server (voice-agent) — 7848 free.
VOICE_TEXT_PORT = int(os.environ.get("DISCORD_VOICE_CONTROL_PORT", "7848"))
VOICE_TEXT_BASE = f"http://127.0.0.1:{VOICE_TEXT_PORT}"


def _post(path: str, body: bytes, content_type: str, timeout: float = 3.0) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{VOICE_TEXT_BASE}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError):
        return 0, b""


def active_voice_channel(timeout: float = 1.0) -> str | None:
    """Return the channel id of the active discord-voice session, or None.

    None when no voice session is up, the control server isn't listening, or
    the session reports it isn't ready to accept content.
    """
    try:
        with urllib.request.urlopen(f"{VOICE_TEXT_BASE}/voice/state", timeout=timeout) as resp:
            data = json.loads(resp.read())
        if not data.get("sessionReady"):
            return None
        ch = data.get("channelId")
        return str(ch) if ch else None
    except Exception:
        return None


def push_text(text: str, channel_id: str | int, source: str = "discord-bridge") -> bool:
    """Inject *text* into the active discord-voice session for *channel_id*.

    Returns True if the text was accepted. Silently returns False if no voice
    session is live for that channel — callers must not break on this, because
    the message has already been (or will be) delivered to the channel through
    the regular bridge path.
    """
    text = (text or "").strip()
    if not text:
        return False

    target = str(channel_id)
    active = active_voice_channel()
    # No session, or the session is serving a different channel — no-op.
    if active is None or active != target:
        return False

    payload = json.dumps({"text": text, "source": source}).encode("utf-8")
    status, _ = _post("/voice/text", payload, "application/json")
    return 200 <= status < 300
