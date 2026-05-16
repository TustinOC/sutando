"""Resolve personal-asset paths with private-dir-first lookup.

As of PR #748+#753 (TypeScript) / this PR (Python), these helpers delegate
to `tenant_path` (src/tenant.py) which handles the three-level resolution
(per-tenant / per-device / fleet-shared) plus the single-tenant legacy
fallback. Single-tenant installs (no SUTANDO_TENANT_ID, no state/tenant.json)
see no behavior change.

Two public helpers preserved:

    personal_path(filename)        — per-machine state (stand-identity.json,
                                     pending-questions.md). Maps to
                                     tenant_path(..., 'device').
    shared_personal_path(filename) — fleet-shared state (notes, build_log).
                                     Maps to tenant_path(..., 'shared').

Special case: stand-avatar.png also lives under <workspace>/assets/ in the
public repo. The avatar fallback is preserved here.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

# Local import so the file is usable both as `from util_paths import ...`
# (the existing convention in src/ scripts) and as `from src.util_paths
# import ...` from the repo root.
try:
    from tenant import tenant_path
except ImportError:
    from .tenant import tenant_path  # type: ignore[no-redef]

REPO_DIR = Path(__file__).resolve().parent.parent


def personal_path(filename: str, workspace: Optional[Path] = None) -> Path:
    """Resolve a personal-asset path. Delegates to tenant_path(..., 'device').

    Avatar fallback: stand-avatar.png ships under assets/ in the public
    repo. Preserve the special case so a fresh install with no private
    dir still resolves the asset.
    """
    ws = workspace if workspace is not None else REPO_DIR

    if filename == "stand-avatar.png":
        in_assets = ws / "assets" / filename
        if in_assets.exists():
            return in_assets

    return tenant_path(filename, "device", workspace=ws)


def shared_personal_path(filename: str, workspace: Optional[Path] = None) -> Path:
    """Resolve a shared-private path. Delegates to tenant_path(..., 'shared')."""
    return tenant_path(filename, "shared", workspace=workspace)
