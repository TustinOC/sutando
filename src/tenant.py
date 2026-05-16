"""Sutando — Per-Tenant Identity (Python twin of src/tenant.ts).

Mirrors the TypeScript module shipped in PR #748. Both languages implement
the same design — the layout, fallback ordering, and validation rules are
identical, and changes must land in both to stay aligned. See `src/tenant.ts`
for the parallel implementation.

Three-level path model:

    $SUTANDO_PRIVATE_DIR/
      ├── tenant-default/                ← today (single-tenant)
      │   ├── machine-<host>/            ← per-device (scope='device')
      │   └── ...                        ← fleet-shared (scope='shared')
      └── tenant-acme/                   ← future second tenant
          └── ...

Tenant id resolution (high → low):
    1. SUTANDO_TENANT_ID env var.
    2. <workspace>/state/tenant.json   {"id": "...", "name": "..."}
    3. 'default'.

`tenant_path(filename, scope, workspace=None, for_tenant=None)` builds the
three-level path with legacy-pre-migration fallback when the tenant
directory doesn't exist yet.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Literal, Optional

TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_cached_tenant_id: Optional[str] = None


def _workspace_dir() -> Path:
    """Mirrors src/util_paths.py:REPO_DIR + SUTANDO_WORKSPACE env override."""
    ws = os.environ.get("SUTANDO_WORKSPACE")
    if ws:
        return Path(os.path.expanduser(ws))
    return Path(__file__).resolve().parent.parent


def _read_from_state_file(workspace: Path) -> Optional[str]:
    path = workspace / "state" / "tenant.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        tid = data.get("id") if isinstance(data, dict) else None
        if isinstance(tid, str) and TENANT_ID_PATTERN.match(tid):
            return tid
        if tid is not None:
            print(f"[Tenant] state/tenant.json id {tid!r} failed validation; using default", file=sys.stderr)
    except Exception as e:
        print(f"[Tenant] failed to read state/tenant.json: {e}", file=sys.stderr)
    return None


def tenant_id(workspace: Optional[Path] = None) -> str:
    """Resolve the current tenant id. Cached after first call.

    Call `_reset_for_tests()` in tests that switch IDs.
    """
    global _cached_tenant_id
    if _cached_tenant_id is not None:
        return _cached_tenant_id
    ws = workspace if workspace is not None else _workspace_dir()
    env_id = os.environ.get("SUTANDO_TENANT_ID")
    if env_id and TENANT_ID_PATTERN.match(env_id):
        _cached_tenant_id = env_id
        return env_id
    if env_id:
        print(f"[Tenant] SUTANDO_TENANT_ID {env_id!r} failed validation; using default", file=sys.stderr)
    from_state = _read_from_state_file(ws)
    if from_state:
        _cached_tenant_id = from_state
        return from_state
    _cached_tenant_id = "default"
    return "default"


PathScope = Literal["device", "shared"]


def tenant_path(
    filename: str,
    scope: PathScope = "shared",
    workspace: Optional[Path] = None,
    for_tenant: Optional[str] = None,
) -> Path:
    """Per-tenant path resolver. See module docstring for the layout.

    Workspace fallback (mirrors existing personal_path semantics): if
    SUTANDO_PRIVATE_DIR is unset OR the resolved path doesn't exist,
    returns `<workspace>/<file>` for single-tenant installs that haven't
    migrated yet. Callers do `.exists()` checks against the return.

    Returns the FIRST existing path along the priority chain. When nothing
    exists, returns the *preferred* private-dir path so the caller's
    `.exists()` check fails gracefully and a subsequent write lands there.

    `for_tenant`: cross-tenant queries pass it explicitly; default uses the
    current cached tenant id. Keeping both shapes lets a caller read
    another tenant's data without invalidating their own cache.
    """
    ws = workspace if workspace is not None else _workspace_dir()
    tid = for_tenant if for_tenant is not None else tenant_id(ws)
    private_root_env = os.environ.get("SUTANDO_PRIVATE_DIR")
    host = socket.gethostname().split(".")[0]

    if private_root_env:
        root = Path(os.path.expanduser(private_root_env))
        tenant_root = root / f"tenant-{tid}"
        candidate = (
            tenant_root / f"machine-{host}" / filename
            if scope == "device"
            else tenant_root / filename
        )
        if candidate.exists():
            return candidate
        # Single-tenant pre-migration fallback: an existing install may
        # still have files at $SUTANDO_PRIVATE_DIR/machine-<host>/<file>
        # (no tenant-default prefix). Try that path before workspace.
        if tid == "default":
            legacy = (
                root / f"machine-{host}" / filename
                if scope == "device"
                else root / filename
            )
            if legacy.exists():
                return legacy

    ws_path = ws / filename
    if ws_path.exists():
        return ws_path

    if private_root_env:
        root = Path(os.path.expanduser(private_root_env))
        tenant_root = root / f"tenant-{tid}"
        return (
            tenant_root / f"machine-{host}" / filename
            if scope == "device"
            else tenant_root / filename
        )
    return ws_path


def _reset_for_tests() -> None:
    """Test-only: clear the tenant-id cache between tests."""
    global _cached_tenant_id
    _cached_tenant_id = None
