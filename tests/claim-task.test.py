#!/usr/bin/env python3
"""Tests for the claim primitive (src/claim_task.py).

Run: python3 tests/claim-task.test.py
Exit: 0 on pass, 1 on fail.

Covers:
  1. Happy path — claim an existing task, file gets renamed.
  2. Race — two processes both try to claim the same task, exactly one wins.
  3. Missing task — claim a task that doesn't exist returns None.
  4. Already claimed — second claim attempt on the same task returns None.
  5. Different core_id format — claim files distinguish by core id.
  6. Input validation — empty / dotted / path-separator inputs rejected.
"""
import multiprocessing as mp
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from claim_task import claim  # noqa: E402


def _try_claim(ws_str: str, task_id: str, core_id: str, q: "mp.Queue[str]") -> None:
    """Subprocess entry point — try to claim and post the result on the
    queue. Used by the race test to fork two true OS processes that both
    attempt the rename concurrently. We can't use threads here because the
    `os.rename` atomicity guarantee is per-process via the kernel, and
    threads in CPython would serialize via the GIL and obscure the race."""
    sys.path.insert(0, str(Path(ws_str).parent / "src"))  # safety
    sys.path.insert(0, str(ROOT / "src"))
    from claim_task import claim as _claim

    result = _claim(task_id, core_id, workspace=Path(ws_str))
    q.put(f"{core_id}:{'won' if result is not None else 'lost'}:{result}")


class ClaimTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "tasks").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_task(self, task_id: str, content: str = "body\n") -> Path:
        p = self.ws / "tasks" / f"task-{task_id}.txt"
        p.write_text(content)
        return p

    def test_happy_path_claim_renames_file(self):
        original = self._write_task("abc123", "hello\n")
        result = claim("abc123", "1", workspace=self.ws)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "task-abc123.claimed-core-1.txt")
        self.assertTrue(result.exists())
        self.assertFalse(original.exists())
        # Content preserved through rename.
        self.assertEqual(result.read_text(), "hello\n")

    def test_missing_task_returns_none(self):
        result = claim("doesnotexist", "1", workspace=self.ws)
        self.assertIsNone(result)

    def test_double_claim_second_returns_none(self):
        self._write_task("dup")
        first = claim("dup", "1", workspace=self.ws)
        second = claim("dup", "2", workspace=self.ws)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        # First claim file still present, no second claim file.
        self.assertTrue((self.ws / "tasks" / "task-dup.claimed-core-1.txt").exists())
        self.assertFalse((self.ws / "tasks" / "task-dup.claimed-core-2.txt").exists())

    def test_race_exactly_one_wins(self):
        """Two subprocesses race to claim the same task — exactly one wins.

        This is the core correctness guarantee. If multiple sessions in the
        pool ever both claim the same task, the down-stream done-flag gate
        is the only thing preventing duplicate side effects — but that gate
        is for crash recovery, not for live race-loss. The claim itself
        MUST be exclusive.
        """
        self._write_task("race")
        ctx = mp.get_context("fork")
        q: "mp.Queue[str]" = ctx.Queue()
        p1 = ctx.Process(target=_try_claim, args=(str(self.ws), "race", "1", q))
        p2 = ctx.Process(target=_try_claim, args=(str(self.ws), "race", "2", q))
        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)
        self.assertFalse(p1.is_alive())
        self.assertFalse(p2.is_alive())
        outcomes = [q.get_nowait(), q.get_nowait()]
        won = [o for o in outcomes if ":won:" in o]
        lost = [o for o in outcomes if ":lost:" in o]
        self.assertEqual(len(won), 1, f"expected exactly 1 win, got {outcomes!r}")
        self.assertEqual(len(lost), 1, f"expected exactly 1 loss, got {outcomes!r}")
        # The won-by core's claim file exists, the other's doesn't.
        won_core = won[0].split(":")[0]
        lost_core = lost[0].split(":")[0]
        self.assertTrue(
            (self.ws / "tasks" / f"task-race.claimed-core-{won_core}.txt").exists()
        )
        self.assertFalse(
            (self.ws / "tasks" / f"task-race.claimed-core-{lost_core}.txt").exists()
        )

    def test_different_core_ids_distinguish_claim_files(self):
        self._write_task("a")
        self._write_task("b")
        a = claim("a", "1", workspace=self.ws)
        b = claim("b", "2", workspace=self.ws)
        self.assertEqual(a.name, "task-a.claimed-core-1.txt")
        self.assertEqual(b.name, "task-b.claimed-core-2.txt")

    def test_validation_rejects_path_traversal(self):
        # A hostile task_id with `/` or `..` must NOT be allowed to escape
        # the tasks/ dir. Regression guard for the CodeQL py/path-injection
        # class; same sanitizer shape as the bridges' file-attach allowlist.
        for bad in ["../etc", "a/b", "..", ".", "", ".dotfile"]:
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                claim(bad, "1", workspace=self.ws)
        for bad in ["../1", "a/1", ""]:
            with self.assertRaises(ValueError, msg=f"should reject core_id {bad!r}"):
                claim("ok", bad, workspace=self.ws)

    def test_validation_allows_realistic_ids(self):
        self._write_task("1779170589673")
        result = claim("1779170589673", "1", workspace=self.ws)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
