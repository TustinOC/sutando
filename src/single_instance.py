#!/usr/bin/env python3
"""
Single-instance guard for Sutando bridge processes (closes #1257).

Usage:
    from single_instance import acquire
    acquire("telegram-bridge")  # exits cleanly if another instance holds the lock
"""
from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_default import resolve_workspace  # noqa: E402

_held_fds: list[int] = []  # keep refs so GC doesn't close them


def acquire(name: str) -> None:
    """Acquire an exclusive per-name file lock.

    Exits cleanly (os._exit(0)) if another process already holds it so
    launchd / startup.sh don't restart-loop on a normal second-instance
    rejection.  Lock auto-releases on process death because the OS closes
    all fds at that point (SIGTERM, crash, or clean exit).
    """
    lock_dir = resolve_workspace() / "state" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            f"[{name}] another instance already holds the lock at {lock_path}; "
            "exiting cleanly.",
            file=sys.stderr,
        )
        os._exit(0)  # exit 0 so launchd doesn't restart-loop
    # Write PID for debugging.
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _held_fds.append(fd)  # lock auto-releases on process death (OS closes all fds)
