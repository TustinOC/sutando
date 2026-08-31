#!/usr/bin/env python3
"""One wall-clock ceiling for every test file makes the cap a merge gate.

A concurrency test that spends its time spawning instrumented subprocesses is
measured against a budget sized for ordinary unit tests, so it fails while
asserting nothing wrong. A file may declare its own budget; everything else is
untouched.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "coverage-file-timeout.sh"
DEFAULT = "120"


def budget(path: Path, env: dict | None = None) -> str:
    e = dict(os.environ)
    e.pop("COVERAGE_GATE_FILE_TIMEOUT", None)
    e.update(env or {})
    out = subprocess.run(["bash", str(SCRIPT), "--print", str(path)],
                         capture_output=True, text=True, env=e, check=True)
    return out.stdout.strip()


class BudgetResolution(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)

    def _write(self, name: str, body: str) -> Path:
        f = self.d / name
        f.write_text(body)
        return f

    def test_undeclared_file_gets_the_shared_default(self):
        f = self._write("plain.test.py", "print(1)\n")
        self.assertEqual(budget(f), DEFAULT)

    def test_a_declared_budget_is_honoured(self):
        f = self._write("slow.test.py", "# coverage-gate: timeout=420\nprint(1)\n")
        self.assertEqual(budget(f), "420")

    def test_env_still_moves_the_default_for_undeclared_files(self):
        f = self._write("plain.test.py", "print(1)\n")
        self.assertEqual(budget(f, {"COVERAGE_GATE_FILE_TIMEOUT": "77"}), "77")

    def test_a_declaration_beats_the_env_default(self):
        f = self._write("slow.test.py", "# coverage-gate: timeout=420\nprint(1)\n")
        self.assertEqual(budget(f, {"COVERAGE_GATE_FILE_TIMEOUT": "77"}), "420")

    def test_malformed_declarations_fall_through_rather_than_disable_the_cap(self):
        # zero is the dangerous one: read as a number it would mean "no time at
        # all", and a lenient parser could turn it into "no cap".
        for body in ("# coverage-gate: timeout=0\n",
                     "# coverage-gate: timeout=abc\n",
                     "# coverage-gate: timeout=\n",
                     "# coverage-gate:timeout=300 trailing\n"):
            with self.subTest(body=body.strip()):
                self.assertEqual(budget(self._write("m.test.py", body)), DEFAULT)

    def test_a_declaration_below_the_scanned_window_does_not_count(self):
        f = self._write("late.test.py", "\n" * 40 + "# coverage-gate: timeout=999\n")
        self.assertEqual(budget(f), DEFAULT)

    def test_an_unreadable_path_is_the_default_not_an_error(self):
        self.assertEqual(budget(self.d / "nope.test.py"), DEFAULT)


class CommandConstruction(unittest.TestCase):
    """What the runner execs — asserted without needing coreutils present."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        stub = self.d / "timeout"
        stub.write_text('#!/usr/bin/env bash\necho "ARGS: $*"\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.env = dict(os.environ)
        self.env.pop("COVERAGE_GATE_FILE_TIMEOUT", None)
        self.env["PATH"] = f"{self.d}:{self.env['PATH']}"

    def _run(self, f: Path) -> str:
        return subprocess.run(["bash", str(SCRIPT), "python3", "-m", "coverage", "run", str(f)],
                              capture_output=True, text=True, env=self.env).stdout.strip()

    def test_the_declared_budget_reaches_timeout(self):
        f = self.d / "slow.test.py"
        f.write_text("# coverage-gate: timeout=420\n")
        self.assertEqual(self._run(f), f"ARGS: -k 5 420 python3 -m coverage run {f}")

    def test_an_undeclared_file_still_gets_the_default(self):
        f = self.d / "plain.test.py"
        f.write_text("print(1)\n")
        self.assertEqual(self._run(f), f"ARGS: -k 5 {DEFAULT} python3 -m coverage run {f}")


class TheFileThisWasOpenedFor(unittest.TestCase):
    def test_outbox_race_declares_a_budget(self):
        f = REPO / "tests" / "outbox-race.test.py"
        self.assertTrue(f.is_file())
        self.assertEqual(budget(f), "300")

    def test_a_neighbouring_test_is_unaffected(self):
        for name in ("cron-notify.test.py", "cwd-lint.test.py"):
            f = REPO / "tests" / name
            if f.is_file():
                self.assertEqual(budget(f), DEFAULT, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
