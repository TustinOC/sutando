"""Tests for migration 0001: SUTANDO_PRIVATE_DIR → SUTANDO_MEMORY_DIR (#956)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

MIGRATION = Path(__file__).resolve().parent.parent.parent / "migrations" / "0001-rename-private-dir-to-memory-dir.sh"


def _run(workspace: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(MIGRATION), workspace], capture_output=True, text=True)


class TestMigration0001(unittest.TestCase):
    def test_renames_old_var(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            env = Path(ws) / ".env"
            env.write_text("SUTANDO_PRIVATE_DIR=/some/path\nOTHER_VAR=keep\n")
            result = _run(ws)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = env.read_text()
            self.assertIn("SUTANDO_MEMORY_DIR=/some/path", content)
            self.assertNotIn("SUTANDO_PRIVATE_DIR", content)
            self.assertIn("OTHER_VAR=keep", content)

    def test_idempotent_when_already_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            env = Path(ws) / ".env"
            env.write_text("SUTANDO_MEMORY_DIR=/some/path\n")
            result = _run(ws)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = env.read_text()
            self.assertIn("SUTANDO_MEMORY_DIR=/some/path", content)
            self.assertNotIn("SUTANDO_PRIVATE_DIR", content)

    def test_idempotent_when_neither_set(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            env = Path(ws) / ".env"
            env.write_text("OTHER_VAR=foo\n")
            result = _run(ws)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("OTHER_VAR=foo", env.read_text())

    def test_no_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            result = _run(ws)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(ws) / ".env").exists())

    def test_preserves_other_vars(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            env = Path(ws) / ".env"
            env.write_text(
                "SUTANDO_PRIVATE_DIR=/mem\n"
                "DISCORD_BOT_TOKEN=abc123\n"
                "SUTANDO_REPO_DIR=/repo\n"
            )
            result = _run(ws)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = env.read_text()
            self.assertIn("SUTANDO_MEMORY_DIR=/mem", content)
            self.assertIn("DISCORD_BOT_TOKEN=abc123", content)
            self.assertIn("SUTANDO_REPO_DIR=/repo", content)
            self.assertNotIn("SUTANDO_PRIVATE_DIR", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
