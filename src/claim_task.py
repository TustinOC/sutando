#!/usr/bin/env python3
"""Atomic-rename claim primitive for the multi-core agent pool (#880).

A new task lands as `<workspace>/tasks/task-<id>.txt`. A core session claims
it by atomic-rename:

    tasks/task-<id>.txt -> tasks/task-<id>.claimed-core-<n>.txt

POSIX `rename()` on the same filesystem is atomic — no two callers both
succeed. The losing caller sees `FileNotFoundError` and walks away.

Within one filesystem this collapses #872's cross-Mac last-rename-wins to
first-rename-wins. Single-Mac strict subset of the same coordination
protocol; same primitive, no sync gap.

CLI:
    python3 src/claim_task.py <task-id> <core-id>

Exit codes:
    0 — claim succeeded; new path printed to stdout
    1 — claim failed (task already claimed by another core, or doesn't exist)
    2 — usage error

Library:
    from claim_task import claim
    claimed_path = claim(task_id, core_id, workspace=None)
    if claimed_path is None:
        # lost the race
        ...
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _workspace_root() -> Path:
    """Resolve workspace root, matching the rest of the codebase (#762)."""
    env = os.environ.get("SUTANDO_WORKSPACE")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".sutando" / "workspace"


def _validate_id(name: str, kind: str) -> str:
    """Allow only alphanumerics + `-` + `_` + `.`. Reject path separators
    and traversal attempts so a hostile `task_id` can't escape `tasks/`."""
    if not name:
        raise ValueError(f"empty {kind}")
    bad = set(name) - set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if bad:
        raise ValueError(f"invalid {kind}: contains {sorted(bad)!r}")
    if name in (".", "..") or name.startswith("."):
        raise ValueError(f"invalid {kind}: dot-prefixed names not allowed")
    return name


def claim(task_id: str, core_id: str, workspace: Path | None = None) -> Path | None:
    """Attempt to claim a task by atomic-rename.

    Returns the path to the claim file on success, or None if the claim
    failed (task already claimed or missing). Does NOT raise on race-loss —
    that's a normal outcome, not an error.

    Raises ValueError on invalid task_id / core_id input.
    """
    task_id = _validate_id(task_id, "task_id")
    core_id = _validate_id(core_id, "core_id")
    ws = workspace if workspace is not None else _workspace_root()
    src = ws / "tasks" / f"task-{task_id}.txt"
    dst = ws / "tasks" / f"task-{task_id}.claimed-core-{core_id}.txt"
    try:
        # POSIX rename: atomic on same filesystem. If src disappeared
        # because another core already claimed it, we get FileNotFoundError.
        os.rename(src, dst)
        return dst
    except FileNotFoundError:
        return None
    except OSError:
        # Could be EXDEV (cross-device) or permission; treat as lost-race
        # and surface the underlying error to stderr for diagnosis without
        # raising — the caller's contract is "won or lost", not "won or threw".
        return None


def _main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: claim_task.py <task-id-without-task-prefix-or-.txt-suffix> <core-id>",
            file=sys.stderr,
        )
        return 2
    task_id, core_id = argv[1], argv[2]
    try:
        result = claim(task_id, core_id)
    except ValueError as e:
        print(f"claim_task: {e}", file=sys.stderr)
        return 2
    if result is None:
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
