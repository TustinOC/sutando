#!/usr/bin/env python3
"""Tests that `workspace_default._migrate_inrepo_notes` does NOT touch the
in-repo `notes/` dir when it's a symlink.

Closes #1149.

Background: on 2026-05-25 a test in #1148 set `SUTANDO_WORKSPACE` to a
`tempfile.TemporaryDirectory()`. The legacy-notes migration walked
`<repo>/notes/` (which on the canonical memory-sync setup is a symlink
to `~/.sutando/memory-sync/notes/`), saw 38 `.md` files, and ran
`shutil.move` on each — moving them OUT of the memory-sync target and
INTO the tmpdir. When the test exited, `tempfile`'s cleanup deleted
the tmpdir, destroying the files. 37 of 38 were recoverable from git
(`git checkout HEAD -- notes/`); the 38th was an uncommitted file
(`notes/competitive-matrix-2026-05-26.md`) and unrecoverable.

The fix: if `<repo>/notes/` is a symlink, skip the migration. This is
the canonical memory-sync setup, and the files belong to the symlink
target (memory-sync repo), not to the in-repo location.

Run: python3 tests/workspace_default_symlink_skip.test.py
Exit: 0 on pass, 1 on fail.

Stdlib-only (no pytest) — matches the repo's standalone-test convention.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


_FAILURES: list[str] = []


def fail(msg: str) -> None:
    _FAILURES.append(msg)
    print(f"FAIL: {msg}", file=sys.stderr)


def expect(cond: bool, label: str) -> None:
    if not cond:
        fail(label)


def test_symlinked_notes_dir_is_skipped():
    """The bug scenario: `<repo>/notes/` is a symlink to a separate dir
    holding the real files. `_migrate_inrepo_notes` MUST leave both
    locations untouched and just drop the sentinel."""
    import workspace_default as wd

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Pretend repo
        fake_repo = tmp_path / "repo"
        fake_repo.mkdir()
        # The "memory-sync" target where notes really live
        sync_target = tmp_path / "memory-sync" / "notes"
        sync_target.mkdir(parents=True)
        (sync_target / "important.md").write_text("# important — must survive\n")
        (sync_target / "other.md").write_text("# other\n")
        # Repo's notes/ symlinks to the sync target
        (fake_repo / "notes").symlink_to(sync_target)

        # Workspace = a different dir
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Stub _legacy_repo_root() to point at our fake repo
        original_repo_root = wd._legacy_repo_root
        wd._legacy_repo_root = lambda: fake_repo
        try:
            result = wd._migrate_inrepo_notes(workspace)
        finally:
            wd._legacy_repo_root = original_repo_root

        expect(result is False, "_migrate_inrepo_notes returned True; should be False on symlink skip")
        expect(
            (sync_target / "important.md").exists(),
            "symlink target file was MOVED — should remain at link target",
        )
        expect(
            (sync_target / "other.md").exists(),
            "symlink target file was MOVED — should remain at link target",
        )
        expect(
            not (workspace / "notes" / "important.md").exists(),
            "file appeared in workspace — migration should have been skipped",
        )
        expect(
            (workspace / ".notes-migrated").exists(),
            "sentinel should still be dropped so we short-circuit next call",
        )


def test_real_directory_still_migrates():
    """Sanity: when `notes/` is a real dir (not a symlink), migration
    still runs as before. The symlink-skip is the *only* added gate."""
    import workspace_default as wd

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fake_repo = tmp_path / "repo"
        fake_repo.mkdir()
        # Real dir, not symlink
        inrepo_notes = fake_repo / "notes"
        inrepo_notes.mkdir()
        (inrepo_notes / "note1.md").write_text("# note1\n")

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        original_repo_root = wd._legacy_repo_root
        wd._legacy_repo_root = lambda: fake_repo
        try:
            result = wd._migrate_inrepo_notes(workspace)
        finally:
            wd._legacy_repo_root = original_repo_root

        expect(result is True, "real-dir migration should have returned True")
        expect(
            (workspace / "notes" / "note1.md").exists(),
            "real-dir file should have moved to workspace",
        )
        expect(
            not (inrepo_notes / "note1.md").exists(),
            "real-dir source file should be gone after move",
        )


def test_sentinel_short_circuits_subsequent_calls():
    """Even on the symlink-skip path, the sentinel must be written so
    repeated `resolve_workspace()` calls don't re-traverse the symlink."""
    import workspace_default as wd

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fake_repo = tmp_path / "repo"
        fake_repo.mkdir()
        sync_target = tmp_path / "memory-sync" / "notes"
        sync_target.mkdir(parents=True)
        (sync_target / "x.md").write_text("# x\n")
        (fake_repo / "notes").symlink_to(sync_target)

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        original_repo_root = wd._legacy_repo_root
        wd._legacy_repo_root = lambda: fake_repo
        try:
            wd._migrate_inrepo_notes(workspace)
            # Second call should short-circuit via sentinel and return False
            result2 = wd._migrate_inrepo_notes(workspace)
        finally:
            wd._legacy_repo_root = original_repo_root

        expect(result2 is False, "sentinel short-circuit failed (returned non-False)")
        # And the file should still be intact
        expect((sync_target / "x.md").exists(), "second-call regression: file went missing")


def main() -> int:
    tests = [
        test_symlinked_notes_dir_is_skipped,
        test_real_directory_still_migrates,
        test_sentinel_short_circuits_subsequent_calls,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            fail(f"{t.__name__} raised: {exc!r}")

    if _FAILURES:
        print(f"\n{len(_FAILURES)} failure(s) in {len(tests)} tests", file=sys.stderr)
        return 1
    print(f"PASS: {len(tests)} workspace-default symlink-skip tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
