"""Sutando migration runner — applies pending workspace state migrations.

Usage:
    python3 src/run_migrations.py              # apply pending migrations
    python3 src/run_migrations.py --dry-run    # preview, no changes
    python3 src/run_migrations.py --workspace /path  # explicit workspace

Called by src/startup.sh as a pre-service step. Non-zero exit aborts startup.

Schema-version layout (state/schema-version.json):
    {"applied": [1, 2], "current": 2, "engine_version_at_apply": "..."}

Default for a fresh install (missing file):
    {"applied": [], "current": 0}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA_FILE_NAME = "schema-version.json"

# Migration script name pattern: NNNN-<slug>.sh or NNNN-<slug>.py
_MIGRATION_RE = re.compile(r'^(\d{4})-(.+)\.(sh|py)$')


def _resolve_workspace() -> Path:
    """Mirror workspace_default.py resolution without importing it."""
    env = os.environ.get("SUTANDO_WORKSPACE", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_absolute() else (Path.cwd() / p).resolve()
    return Path.home() / ".sutando" / "workspace"


def _read_schema(workspace: Path) -> dict:
    schema_file = workspace / "state" / SCHEMA_FILE_NAME
    if not schema_file.exists():
        return {"applied": [], "current": 0}
    try:
        data = json.loads(schema_file.read_text())
        return {
            "applied": list(data.get("applied", [])),
            "current": int(data.get("current", 0)),
        }
    except Exception as e:
        print(f"  [migrations] WARNING: could not read {schema_file}: {e}", file=sys.stderr)
        return {"applied": [], "current": 0}


def _write_schema(workspace: Path, schema: dict) -> None:
    state_dir = workspace / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    schema_file = state_dir / SCHEMA_FILE_NAME
    payload = json.dumps(schema, indent=2) + "\n"
    # Atomic write via tmp + rename.
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, prefix=".schema-version-tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, schema_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _list_pending(schema: dict) -> list[tuple[int, Path]]:
    """Return (N, path) tuples for migrations with N > schema['current'], sorted."""
    if not MIGRATIONS_DIR.exists():
        return []
    pending = []
    for entry in MIGRATIONS_DIR.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if not m:
            continue
        n = int(m.group(1))
        if n > schema["current"]:
            pending.append((n, entry))
    return sorted(pending, key=lambda x: x[0])


def _run_migration(n: int, script: Path, workspace: Path) -> bool:
    """Execute a single migration script. Returns True on success."""
    is_py = script.suffix == ".py"
    if is_py:
        cmd = [sys.executable, str(script), str(workspace)]
    else:
        cmd = ["bash", str(script), str(workspace)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n  [migrations] FAILED: {script.name} (exit {result.returncode})", file=sys.stderr)
        if result.stderr.strip():
            # Show last 20 lines of stderr for diagnosis.
            lines = result.stderr.strip().splitlines()
            for line in lines[-20:]:
                print(f"    {line}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply pending Sutando workspace migrations.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without applying.")
    parser.add_argument("--workspace", help="Workspace path (default: auto-resolved).")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).expanduser() if args.workspace else _resolve_workspace()

    schema = _read_schema(workspace)
    pending = _list_pending(schema)

    if not pending:
        # Completely silent on no-op — startup.sh runs this every boot.
        return 0

    print(f"  [migrations] workspace: {workspace}")
    print(f"  [migrations] current schema version: {schema['current']}")
    print(f"  [migrations] pending: {[p.name for _, p in pending]}")

    if args.dry_run:
        print("  [migrations] dry-run — no changes made.")
        return 0

    for n, script in pending:
        print(f"  [migrations] applying {script.name} ...", flush=True)
        if not _run_migration(n, script, workspace):
            print(
                "  [migrations] ABORTING — startup requires all migrations to succeed.",
                file=sys.stderr,
            )
            return 1
        schema["applied"].append(n)
        schema["current"] = n
        try:
            _write_schema(workspace, schema)
        except Exception as e:
            print(f"  [migrations] FAILED to write schema after {script.name}: {e}", file=sys.stderr)
            return 1
        print(f"  [migrations] ✓ {script.name}")

    print(f"  [migrations] schema version now: {schema['current']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
