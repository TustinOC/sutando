#!/usr/bin/env python3
"""
WhatsApp self-chat bridge for Sutando (inbound channel for the `whatsapp` skill).

The Mac running Sutando is a *linked device* on the owner's own WhatsApp
account, so the owner "messages Sutando" by writing to their own self-chat
("Message Yourself"). This daemon is the inbound half of the whatsapp skill
(the outbound/query half is `wacli send`/`list`/`search`, see SKILL.md):

  INBOUND  — polls the self-chat for new owner messages (from-me, no bot
             prefix) and writes them as owner-tier task files into the
             Sutando task pipeline (same shape every bridge uses).
  OUTBOUND — watches results/ for results of the tasks it created and sends
             the reply back into the self-chat, prefixed "🤖 Sutando:".

Echo-suppression: outbound replies carry the 🤖 prefix; inbound polling skips
any message starting with 🤖 (so the bot never reacts to its own output).

Config (all optional — sensible defaults):
  WA_SELF_JID   — the self-chat JID. Default: auto-detected from
                  `wacli auth status` ("Authenticated as <jid>"). No owner
                  identifier is baked into the repo.
  WA_POLL_S     — poll interval seconds (default 8).

Launched by src/startup.sh (whatsapp block, gated on wacli being authenticated
and SKIP_WHATSAPP). Single-instance via flock, so a startup.sh launch and a
launchd/manual launch can't double-process.
"""
import json, os, re, subprocess, sys, time, fcntl
from pathlib import Path

# Repo root derived from this file's location: skills/whatsapp/scripts/ -> repo.
REPO = Path(__file__).resolve().parents[3]

# Single-instance guard — prevents a double-launch (startup.sh + launchd/manual)
# from creating duplicate tasks. Hold an exclusive flock for the process lifetime.
_LOCK_PATH = Path.home() / ".wa-selfchat-bridge.lock"
_lock_fh = open(_LOCK_PATH, "w")
try:
    fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("[wa-bridge] another instance is already running — exiting", flush=True)
    sys.exit(0)

BOT_PREFIX = "🤖 Sutando:"
ECHO_MARKER = "🤖"          # any reply we send starts with this
POLL_S = int(os.environ.get("WA_POLL_S", "8"))
STATE_FILE = Path.home() / ".wa-selfchat-bridge.state.json"


def wacli(args, timeout=60):
    return subprocess.run(["wacli"] + args, capture_output=True, text=True, timeout=timeout)


def detect_self_jid():
    """WA_SELF_JID env wins; else parse `wacli auth status` ('Authenticated as <jid>')."""
    env = os.environ.get("WA_SELF_JID", "").strip()
    if env:
        return env
    try:
        r = wacli(["auth", "status"], timeout=20)
        m = re.search(r"Authenticated as\s+(\S+@\S+)", (r.stdout or "") + (r.stderr or ""))
        if m:
            return m.group(1)
    except Exception as e:
        print(f"[wa-bridge] auth-status detect failed: {e}", flush=True)
    return ""


def _ws():
    """Resolve the Sutando workspace via the repo helper (M0 contract)."""
    try:
        out = subprocess.run(["bash", "scripts/sutando-config.sh", "workspace"],
                             cwd=REPO, capture_output=True, text=True, timeout=15)
        p = out.stdout.strip()
        if p:
            return Path(p)
    except Exception:
        pass
    return REPO / "workspace"


WS = _ws()
TASKS = WS / "tasks"
RESULTS = WS / "results"
ARCHIVE = TASKS / "archive" / time.strftime("%Y-%m")
for d in (TASKS, RESULTS, ARCHIVE):
    d.mkdir(parents=True, exist_ok=True)


def log(m):
    print(f"[wa-bridge {time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        # first run: only act on messages from NOW onward (don't replay history)
        return {"last_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "seen": [], "pending": {}}


def save_state(s):
    s["seen"] = s["seen"][-500:]  # cap seen-list growth
    STATE_FILE.write_text(json.dumps(s))


def sync_once():
    try:
        wacli(["sync", "--once", "--idle-exit", "5s"], timeout=40)
    except Exception as e:
        log(f"sync error (continuing): {e}")


def poll_inbound(state, self_jid):
    """Read new self-chat owner messages → write task files."""
    try:
        r = wacli(["messages", "list", "--chat", self_jid, "--from-me",
                   "--asc", "--after", state["last_iso"], "--json", "--limit", "50"], timeout=40)
        data = json.loads(r.stdout or "{}")
        msgs = (data.get("data") or {}).get("messages") or []
    except Exception as e:
        log(f"inbound list error: {e}")
        return
    for m in msgs:
        mid = m.get("MsgID") or ""
        text = (m.get("Text") or "").strip()
        ts = m.get("Timestamp") or ""
        if not mid or mid in state["seen"]:
            continue
        state["seen"].append(mid)
        if ts and ts > state["last_iso"]:
            state["last_iso"] = ts
        if not text:
            continue  # media/non-text — skip (text-command channel for now)
        if text.startswith(ECHO_MARKER):
            continue  # our own reply — echo-suppress
        tid = f"task-{int(time.time()*1000)}"
        body = (
            f"id: {tid}\n"
            f"timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"task: [WhatsApp self-chat] {text}\n"
            f"source: whatsapp\n"
            f"chat_id: {self_jid}\n"
            f"user_id: {self_jid}\n"
            f"access_tier: owner\n"
            f"priority: normal\n"
        )
        tmp = TASKS / f"{tid}.txt.tmp"
        tmp.write_text(body)
        tmp.rename(TASKS / f"{tid}.txt")
        state["pending"][tid] = mid
        log(f"inbound → {tid}: {text[:60]!r}")


def deliver_outbound(state, self_jid):
    """Send results of our tasks back to the self-chat, prefixed."""
    for tid in list(state["pending"].keys()):
        rf = RESULTS / f"{tid}.txt"
        if not rf.exists():
            continue
        try:
            reply = rf.read_text().strip()
        except Exception:
            continue
        if reply.startswith("[no-send]") or reply.startswith("[deduped:") or reply.startswith("[replied]"):
            log(f"{tid}: no-send marker — archiving without sending")
        elif reply:
            msg = f"{BOT_PREFIX} {reply}"
            try:
                s = wacli(["send", "text", "--to", self_jid, "--message", msg], timeout=60)
                if s.returncode == 0:
                    log(f"outbound ← {tid}: sent {len(reply)} chars")
                else:
                    log(f"outbound {tid} send rc={s.returncode}: {s.stderr[:120]}")
                    continue  # retry next loop
            except Exception as e:
                log(f"outbound {tid} send error: {e}")
                continue
        try:
            (TASKS / f"{tid}.txt").rename(ARCHIVE / f"{tid}.txt")
        except Exception:
            pass
        try:
            rf.rename(ARCHIVE / f"{tid}.result.txt")
        except Exception:
            rf.unlink(missing_ok=True)
        state["pending"].pop(tid, None)


def main():
    self_jid = detect_self_jid()
    if not self_jid:
        log("FATAL: no self JID (set WA_SELF_JID or run `wacli auth`) — exiting")
        sys.exit(1)
    log(f"starting — self={self_jid} ws={WS}")
    state = load_state()
    save_state(state)
    while True:
        sync_once()
        poll_inbound(state, self_jid)
        deliver_outbound(state, self_jid)
        save_state(state)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
