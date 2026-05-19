#!/usr/bin/env python3
"""Per-slot heartbeat for sutando-core sessions.

Writes a small JSON file at `<workspace>/state/cores/core-<id>.alive` every
30 seconds while running. `<id>` is `$SUTANDO_CORE_ID` (set per-slot by the
pool launchd plist) — falls back to `default` for the legacy single-core
install where the env var is unset. The file's content reports the core's
pid, host, start time, last beat, and a free-form status string; the file's
mtime is the "is this core still up?" signal that the claim primitive
(`src/claim_task.py`) consumes.

Why per-slot, not per-host
--------------------------
Today's "is the core alive?" check reads `core-status.json` at the workspace
root — a single file written by the proactive-loop each pass. That's fine
for a single-machine, single-core install. The moment we want multi-core
(N parallel claude sessions on one Mac sharing a workspace, #880), one file
can no longer represent N processes — and the original per-host scheme
(`<hostname>.alive`, pre-#885) had all N pool cores overwriting the same
file, so `claim_task.py:_is_alive(core_id)` (which reads `core-<id>.alive`
per #884's channel-affinity) saw "file missing → dead" for every check.
Channel-affinity silently fell through to plain race-claim on every task.

Per-slot file at `state/cores/core-<id>.alive`:
  • Each running core writes only its own file (no contention).
  • Any process can read the directory to see who's alive across the pool.
  • mtime is the authoritative liveness signal (younger than ~90s = alive).
  • Filename matches what `claim_task.py:_alive_path()` reads — no drift.

This script is intentionally tiny and standalone — startup.sh launches it as
a background process. SIGTERM/SIGINT clean up the .alive file so a graceful
shutdown is visible immediately (vs. waiting for mtime-staleness timeout).

Usage:
  python3 src/core_heartbeat.py                  # default 30s interval
  python3 src/core_heartbeat.py --interval 10    # for tests
  python3 src/core_heartbeat.py --status busy    # set the status string

Runs forever until killed. Exit codes:
  0 — clean shutdown (SIGTERM/SIGINT received)
  Other — fatal write error (unrecoverable; supervisor should restart)
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

# Resolve workspace path with the same precedence as the rest of Sutando.
# Inlined (not imported) so this script can run before any other Sutando
# module is loaded — keeps the heartbeat dep-free.
_workspace_env = os.environ.get("SUTANDO_WORKSPACE", "").strip()
if _workspace_env:
    WORKSPACE = Path(_workspace_env).expanduser()
else:
    WORKSPACE = Path.home() / ".sutando" / "workspace"

CORES_DIR = WORKSPACE / "state" / "cores"


def _hostname() -> str:
    """Short hostname without domain. Mirrors what sync-memory.sh uses for
    machine-<host>/ dirs, so the .alive file's payload is recognizable across
    the fleet's other state files."""
    return socket.gethostname().split(".")[0]


def _core_id() -> str:
    """Resolve the per-slot id used in the .alive filename.

    Reads `$SUTANDO_CORE_ID` (set by the pool launchd plist, see
    `scripts/install-core-pool.sh`). For the legacy single-core install
    (startup.sh path, env unset), falls back to `default` — a stable sentinel
    that won't collide with pool slot numbers (1..N) and keeps the filename
    parseable. Reject empty/whitespace-only env to avoid `core-.alive`."""
    raw = os.environ.get("SUTANDO_CORE_ID", "").strip()
    return raw if raw else "default"


def _alive_path() -> Path:
    return CORES_DIR / f"core-{_core_id()}.alive"


def write_beat(status: str = "running") -> None:
    """Write one heartbeat record. Atomic-via-tmp-then-rename so a concurrent
    reader never sees a partial file."""
    CORES_DIR.mkdir(parents=True, exist_ok=True)
    target = _alive_path()
    payload = {
        "host": _hostname(),
        "core_id": _core_id(),
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "last_beat_at": time.time(),
        "status": status,
        "schema_version": 1,
    }
    tmp = target.with_suffix(".alive.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(target)


_STARTED_AT: float = time.time()
_SHUTDOWN_REQUESTED = False


def _handle_signal(signum: int, frame) -> None:
    """Mark shutdown so the loop exits at the top of the next sleep; also
    unlink the .alive file so peers see this core leave immediately rather
    than wait for mtime staleness."""
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True
    try:
        _alive_path().unlink(missing_ok=True)
    except Exception:  # pragma: no cover — best-effort cleanup
        pass


def run_forever(interval: float = 30.0, status: str = "running") -> int:
    """Heartbeat loop. Returns the exit code (0 on graceful shutdown)."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    while not _SHUTDOWN_REQUESTED:
        try:
            write_beat(status=status)
        except Exception as e:
            # Don't die on transient FS hiccups — log + retry next tick.
            print(f"core_heartbeat: write failed: {e}", file=sys.stderr, flush=True)
        # Sleep in small slices so SIGTERM is responsive (signal handler
        # sets the flag; we check it between slices instead of blocking
        # for the full `interval`).
        slept = 0.0
        slice_s = min(1.0, interval)
        while slept < interval and not _SHUTDOWN_REQUESTED:
            time.sleep(slice_s)
            slept += slice_s
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--interval", type=float, default=30.0, help="seconds between beats (default: 30)")
    p.add_argument("--status", type=str, default="running", help="status string written into the .alive file")
    p.add_argument("--once", action="store_true", help="write a single beat and exit (for tests/debugging)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.once:
        write_beat(status=args.status)
        return 0
    return run_forever(interval=args.interval, status=args.status)


if __name__ == "__main__":
    sys.exit(main())
