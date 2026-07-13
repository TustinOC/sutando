#!/usr/bin/env python3
"""Regression test: health-check.py's memory-dir check must NOT be redirected
by a stale SUTANDO_MEMORY_DIR env value (see _default_memory_dir()'s own
docstring in src/health-check.py).

That env var was a stopgap for a pre-#1454 bug in _default_memory_dir() (it
used to hardcode ~/.claude, ignoring CLAUDE_CONFIG_DIR). #1564 fixed the
underlying computation, but a leftover `os.environ.get("SUTANDO_MEMORY_DIR",
_default_memory_dir())` wrapper kept honoring any stale value left over from
that era — silently pointing the memory-dir health check at a defunct
directory instead of Claude Code's real, actively-used project-memory tree.
util_paths.py's SUTANDO_MEMORY_DIR (personal_path's per-machine asset root)
is a separate, legitimate concern; claude_home_path() never consults it.

Run: python3 tests/health-check-memory-dir-override.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations
import contextlib
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_health_check():
    """Fresh import so module-level MEMORY_DIR picks up current env vars."""
    spec = importlib.util.spec_from_file_location(
        "health_check_memdir_test", REPO / "src" / "health-check.py"
    )
    hc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hc)
    return hc


class TestMemoryDirIgnoresStaleOverride(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("SUTANDO_MEMORY_DIR", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SUTANDO_MEMORY_DIR", None)
        else:
            os.environ["SUTANDO_MEMORY_DIR"] = self._saved

    def test_memory_dir_matches_computed_default_when_env_unset(self):
        hc = _load_health_check()
        self.assertEqual(hc.MEMORY_DIR, Path(hc._default_memory_dir()))

    def test_stale_env_override_does_not_redirect_memory_dir(self):
        """A leftover SUTANDO_MEMORY_DIR (e.g. from a pre-#1454 install) must
        not hijack this check — it should still report on the real,
        computed Claude Code memory dir."""
        os.environ["SUTANDO_MEMORY_DIR"] = "/tmp/some-stale-legacy-memory-dir"
        hc = _load_health_check()
        expected = Path(hc._default_memory_dir())
        self.assertEqual(hc.MEMORY_DIR, expected)
        self.assertNotEqual(hc.MEMORY_DIR, Path("/tmp/some-stale-legacy-memory-dir"))

    def test_stale_env_override_prints_deprecation_warning(self):
        """Setting SUTANDO_MEMORY_DIR should still surface a stderr warning —
        silently ignoring it would leave a user with the (unrelated)
        util_paths.py use case set wondering why this check doesn't reflect
        it. Matches the SUTANDO_WORKSPACE / SUTANDO_PRIVATE_DIR precedent."""
        os.environ["SUTANDO_MEMORY_DIR"] = "/tmp/some-stale-legacy-memory-dir"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            _load_health_check()
        self.assertIn("DEPRECATION", stderr.getvalue())
        self.assertIn("SUTANDO_MEMORY_DIR", stderr.getvalue())

    def test_no_warning_when_env_unset(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            _load_health_check()
        self.assertNotIn("DEPRECATION", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
