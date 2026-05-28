"""Tests for src/single_instance.py — fcntl-based single-instance guard."""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import single_instance


class TestAcquire(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._ws = Path(self._td.name)
        # Patch the name as imported into single_instance (from workspace_default import resolve_workspace)
        patcher = patch.object(single_instance, "resolve_workspace", return_value=self._ws)
        self._mock = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._td.cleanup)
        single_instance._held_fds.clear()

    def test_lock_file_created(self) -> None:
        single_instance.acquire("test-bridge")
        lock_path = self._ws / "state" / "locks" / "test-bridge.lock"
        self.assertTrue(lock_path.exists())

    def test_lock_file_contains_pid(self) -> None:
        single_instance.acquire("test-bridge")
        lock_path = self._ws / "state" / "locks" / "test-bridge.lock"
        content = lock_path.read_text().strip()
        self.assertEqual(content, str(os.getpid()))

    def test_fd_kept_alive(self) -> None:
        initial_count = len(single_instance._held_fds)
        single_instance.acquire("test-bridge-2")
        self.assertEqual(len(single_instance._held_fds), initial_count + 1)

    def test_second_acquire_exits_zero(self) -> None:
        """Second process trying the same lock must exit 0 (not crash-loop launchd)."""
        lock_dir = self._ws / "state" / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "test-bridge.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                f"import sys\n"
                f"sys.path.insert(0, r'{SRC}')\n"
                f"import single_instance\n"
                f"from pathlib import Path\n"
                f"from unittest.mock import patch\n"
                f"with patch.object(single_instance, 'resolve_workspace', return_value=Path(r'{self._ws}')):\n"
                f"    single_instance.acquire('test-bridge')\n"
            )
            script_path = f.name

        try:
            result = subprocess.run([sys.executable, script_path], capture_output=True)
        finally:
            os.unlink(script_path)
            os.close(fd)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"already holds the lock", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
