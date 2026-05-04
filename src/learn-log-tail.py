#!/usr/bin/env python3
"""learn-log-tail — emit JSONL events from filtered log lines.

Phase 2a of the loop-redesign learning system. Pre-filters bridge / voice /
phone logs into a tight JSONL of high-signal events so the summarizer
(Phase 2c subagent) can read pre-cooked events instead of grep-ing
raw 30MB logs at run-time.

Tails (configurable via state/learn-filters.json):
- logs/discord-bridge.log
- logs/voice-agent.log
- logs/conversation-server.log
- logs/telegram-bridge.log

Each tailer maintains a byte-offset cursor in
state/learning-<instance>.log-cursor so restart resumes from where it
left off (no replay, no skip).

Schema (matches learn-collector.py):
  {"ts","instance","source","kind","data":{"byte_offset","excerpt"}}

Source is the relative log path. Kind is one of the configured filter
labels (skip / replied / error / msg / etc.). Excerpt is the first
~250 chars of the matched line — pointer-style; full context recoverable
from {log_path, byte_offset}.

State files (gitignored):
  state/learning-<instance>.jsonl       — shared with learn-collector
  state/learning-<instance>.log-cursor  — per-log byte-offset
  state/learning-<instance>.heartbeat   — touched every poll (shared with collector)

Env vars:
  SUTANDO_ROOT                       override repo root
  LEARN_LOG_TAIL_INTERVAL_S          poll cadence (default 5)

Run:
  python3 src/learn-log-tail.py
"""

import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT_ENV = os.environ.get("SUTANDO_ROOT")
ROOT = Path(ROOT_ENV).resolve() if ROOT_ENV else Path(__file__).resolve().parents[1]

STATE_DIR = ROOT / "state"
LOGS_DIR = ROOT / "logs"

POLL_INTERVAL_S = int(os.environ.get("LEARN_LOG_TAIL_INTERVAL_S", "5"))
EXCERPT_MAX = 250


# Default filter set. Each entry: (log_filename, kind_label, regex).
# A line emits an event tagged with the FIRST matching kind. Lines that
# match no filter are silently dropped (not the same as appended).
DEFAULT_FILTERS = [
    # discord-bridge: routing + outgoing reply confirmations
    ("discord-bridge.log", "skip",    r"\[skip\]"),
    ("discord-bridge.log", "replied", r"^\s*Replied:"),
    ("discord-bridge.log", "error",   r"\b(ERROR|Exception|Traceback|TypeError)\b"),
    # voice-agent: transport health + tool errors
    ("voice-agent.log",    "transport", r"transport (1006|1011|1007|connected|closed)"),
    ("voice-agent.log",    "error",     r"\b(ERROR|Exception|Traceback)\b"),
    ("voice-agent.log",    "delegated", r"\bdelegated\b"),
    # conversation-server: phone call lifecycle
    ("conversation-server.log", "call_event", r"\b(call_started|call_ended|hangup|summary)"),
    ("conversation-server.log", "error",      r"\b(ERROR|Exception|Traceback)\b"),
    # telegram-bridge: same shape as discord
    ("telegram-bridge.log", "skip",    r"\[skip\]"),
    ("telegram-bridge.log", "replied", r"^\s*Replied:"),
    ("telegram-bridge.log", "error",   r"\b(ERROR|Exception|Traceback)\b"),
]


def resolve_instance() -> str:
    identity_path = ROOT / "stand-identity.json"
    if identity_path.exists():
        try:
            data = json.loads(identity_path.read_text())
            machine = data.get("machine") or data.get("instance")
            if machine:
                return machine
        except Exception:
            pass
    host = socket.gethostname().lower().replace(".local", "")
    if "mini" in host:
        return "mini"
    if "macbook" in host or "mbp" in host:
        return "macbook"
    return host or "unknown"


INSTANCE = resolve_instance()
JSONL_PATH = STATE_DIR / f"learning-{INSTANCE}.jsonl"
CURSOR_PATH = STATE_DIR / f"learning-{INSTANCE}.log-cursor"
HEARTBEAT_PATH = STATE_DIR / f"learning-{INSTANCE}.heartbeat"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(source: str, kind: str, data: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    event = {
        "ts": now_iso(),
        "instance": INSTANCE,
        "source": source,
        "kind": kind,
        "data": data,
    }
    with JSONL_PATH.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_cursor() -> dict:
    if not CURSOR_PATH.exists():
        return {}
    try:
        return json.loads(CURSOR_PATH.read_text())
    except Exception:
        return {}


def save_cursor(cursor: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = CURSOR_PATH.with_suffix(".log-cursor.tmp")
    tmp.write_text(json.dumps(cursor))
    tmp.replace(CURSOR_PATH)


def load_filters() -> list:
    """Custom filters can override the default set via state/learn-filters.json.

    Format: [["log_filename", "kind", "regex"], ...]
    """
    custom = STATE_DIR / "learn-filters.json"
    if custom.exists():
        try:
            return [tuple(t) for t in json.loads(custom.read_text())]
        except Exception:
            pass
    return DEFAULT_FILTERS


def compile_filters(filters: list) -> dict:
    """Group filters by log filename, compile regexes."""
    grouped: dict = {}
    for log_name, kind, pattern in filters:
        grouped.setdefault(log_name, []).append((kind, re.compile(pattern)))
    return grouped


def rel_path(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def tail_log(log_path: Path, kind_filters: list, cursor: dict) -> int:
    """Read new bytes from log_path beyond the cursor, emit one event per
    matching line. Returns count emitted.

    Handles log rotation (file shorter than cursor): resets to 0, emits a
    `rotated` event so the summarizer knows.
    """
    if not log_path.exists():
        return 0
    key = log_path.name
    last_offset = cursor.get(key, 0)
    try:
        size = log_path.stat().st_size
    except OSError:
        return 0
    if size < last_offset:
        # Log was rotated/truncated; reset.
        emit(rel_path(log_path), "rotated", {"prev_offset": last_offset, "new_size": size})
        last_offset = 0
    if size == last_offset:
        return 0
    count = 0
    with log_path.open("rb") as f:
        f.seek(last_offset)
        chunk = f.read()
        new_offset = last_offset + len(chunk)
    text = chunk.decode("utf-8", errors="replace")
    pos = last_offset
    for line in text.splitlines(keepends=True):
        line_offset = pos
        pos += len(line.encode("utf-8"))
        stripped = line.rstrip("\n").rstrip("\r")
        if not stripped:
            continue
        for kind, pattern in kind_filters:
            if pattern.search(stripped):
                excerpt = stripped[:EXCERPT_MAX]
                emit(rel_path(log_path), kind, {
                    "byte_offset": line_offset,
                    "excerpt": excerpt,
                })
                count += 1
                break
    cursor[key] = new_offset
    return count


def main() -> int:
    cursor = load_cursor()
    is_first_run = not cursor
    filters_grouped = compile_filters(load_filters())

    if is_first_run:
        # Bootstrap: don't replay history. Set cursor to current end-of-file
        # for each watched log.
        for log_name in filters_grouped:
            log_path = LOGS_DIR / log_name
            if log_path.exists():
                try:
                    cursor[log_name] = log_path.stat().st_size
                except OSError:
                    cursor[log_name] = 0
        save_cursor(cursor)

    emit(
        "log-tail",
        "started",
        {
            "interval_s": POLL_INTERVAL_S,
            "filters_loaded": sum(len(v) for v in filters_grouped.values()),
            "logs_watched": list(filters_grouped.keys()),
            "cursor_loaded": not is_first_run,
        },
    )

    while True:
        try:
            for log_name, kind_filters in filters_grouped.items():
                log_path = LOGS_DIR / log_name
                tail_log(log_path, kind_filters, cursor)
            save_cursor(cursor)
            HEARTBEAT_PATH.write_text(now_iso() + "\n")
        except Exception as e:
            emit("log-tail", "loop_error", {"error": repr(e)})
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        emit("log-tail", "stopped", {})
        sys.exit(0)
