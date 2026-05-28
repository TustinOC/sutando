"""Tests for the migration runner (no pending migrations case).

Validates the harness itself — the green-at-baseline test that proves
the test infrastructure works before any real migrations are added.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent.parent / "src" / "run_migrations.py"


def run_migration(workspace: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Invoke the migration runner against a temporary workspace."""
    cmd = [sys.executable, str(RUNNER), "--workspace", str(workspace)] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True)


class TestRunnerNoMigrationsYet:
    """Runner behavior when migrations/ has no pending entries."""

    def test_exit_zero_on_empty_workspace(self, tmp_path: Path) -> None:
        """Fresh install with no schema-version.json and empty migrations/."""
        result = run_migration(tmp_path)
        assert result.returncode == 0, result.stderr

    def test_exit_zero_on_existing_schema_at_current(self, tmp_path: Path) -> None:
        """Workspace already at the latest schema version — no-op."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "schema-version.json").write_text(
            json.dumps({"applied": [], "current": 0}) + "\n"
        )
        result = run_migration(tmp_path)
        assert result.returncode == 0, result.stderr

    def test_dry_run_exit_zero(self, tmp_path: Path) -> None:
        """--dry-run on no pending migrations exits 0."""
        result = run_migration(tmp_path, ["--dry-run"])
        assert result.returncode == 0, result.stderr

    def test_schema_file_not_created_when_no_migrations(self, tmp_path: Path) -> None:
        """Runner does not create schema-version.json when there are no migrations."""
        run_migration(tmp_path)
        assert not (tmp_path / "state" / "schema-version.json").exists()

    def test_schema_preserved_when_no_pending(self, tmp_path: Path) -> None:
        """Existing schema is not modified when there are no pending migrations."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        original = {"applied": [1], "current": 1}
        (state_dir / "schema-version.json").write_text(json.dumps(original) + "\n")
        result = run_migration(tmp_path)
        assert result.returncode == 0
        on_disk = json.loads((state_dir / "schema-version.json").read_text())
        assert on_disk["current"] == 1
        assert on_disk["applied"] == [1]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
