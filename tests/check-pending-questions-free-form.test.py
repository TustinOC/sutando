#!/usr/bin/env python3
"""Tests for check-pending-questions.py free-form section handling.

Regression test for the bug where sections without an explicit
`**Status:** unanswered` marker were silently skipped. In practice,
pending-questions.md uses free-form prose sections (no Status field);
they must be treated as unanswered.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Bootstrap so the module can import its siblings
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_module(pq_path: Path):
    """Import check-pending-questions with PQ_FILE patched to pq_path."""
    import importlib
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_pending_questions",
        Path(__file__).parent.parent / "src" / "check-pending-questions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # Patch globals before exec so resolve_workspace / personal_path don't
    # touch the real filesystem.
    with patch("workspace_default.resolve_workspace", return_value=Path(pq_path).parent.parent):
        spec.loader.exec_module(mod)

    mod.PQ_FILE = pq_path
    return mod


class TestGetWaitingQuestions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        # check-pending-questions reads PQ_FILE directly; we patch it below
        self.pq = self.workspace / "pending-questions.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _get(self, content: str):
        self.pq.write_text(content)
        mod = _make_module(self.pq)
        return mod.get_waiting_questions()

    # ------------------------------------------------------------------ #
    # Free-form sections (no Status field) must be treated as unanswered. #
    # ------------------------------------------------------------------ #

    def test_free_form_section_is_detected(self):
        """A free-form ## section with no Status field should be returned."""
        content = (
            "# Pending Questions\n\n"
            "## [2026-05-27] configure-relay needs AG2 Space credentials\n\n"
            "Some description without a Status marker.\n"
        )
        qs = self._get(content)
        self.assertEqual(len(qs), 1)
        self.assertIn("configure-relay", qs[0]["title"])

    def test_multiple_free_form_sections(self):
        content = (
            "# Pending\n\n"
            "## First question\n\nSome text.\n\n"
            "## Second question\n\nMore text.\n"
        )
        qs = self._get(content)
        self.assertEqual(len(qs), 2)

    def test_empty_file_returns_empty(self):
        qs = self._get("")
        self.assertEqual(qs, [])

    def test_missing_file_returns_empty(self):
        mod = _make_module(self.workspace / "nonexistent.md")
        self.assertEqual(mod.get_waiting_questions(), [])

    # ------------------------------------------------------------------ #
    # Legacy structured format (explicit Status field) still works.        #
    # ------------------------------------------------------------------ #

    def test_explicit_unanswered_status_detected(self):
        content = (
            "## Q1 — some question\n\n"
            "- **Status:** unanswered\n\n"
            "Details here.\n"
        )
        qs = self._get(content)
        self.assertEqual(len(qs), 1)

    def test_explicit_waiting_status_detected(self):
        content = (
            "## Q2 — another question\n\n"
            "- **Status:** waiting for owner\n\n"
            "Details.\n"
        )
        qs = self._get(content)
        self.assertEqual(len(qs), 1)

    def test_resolved_status_skipped(self):
        content = (
            "## Q3 — already done\n\n"
            "- **Status:** resolved\n\n"
            "Details.\n"
        )
        qs = self._get(content)
        self.assertEqual(qs, [])

    def test_answered_status_skipped(self):
        content = (
            "## Q4 — closed\n\n"
            "- **Status:** answered\n\n"
            "Details.\n"
        )
        qs = self._get(content)
        self.assertEqual(qs, [])

    def test_done_status_skipped(self):
        content = "## Q5 — done\n\n- **Status:** done\n\n"
        qs = self._get(content)
        self.assertEqual(qs, [])

    # ------------------------------------------------------------------ #
    # Mixed: free-form + structured in same file.                          #
    # ------------------------------------------------------------------ #

    def test_mixed_file(self):
        content = (
            "# Pending Questions\n\n"
            "## Free-form question one\n\n"
            "Needs an answer.\n\n"
            "## Structured unanswered\n\n"
            "- **Status:** unanswered\n\n"
            "## Structured resolved\n\n"
            "- **Status:** resolved\n\n"
            "## Free-form question two\n\n"
            "Also needs an answer.\n"
        )
        qs = self._get(content)
        titles = [q["title"] for q in qs]
        self.assertEqual(len(qs), 3)
        self.assertIn("Free-form question one", titles)
        self.assertIn("Structured unanswered", titles)
        self.assertIn("Free-form question two", titles)
        self.assertNotIn("Structured resolved", titles)


if __name__ == "__main__":
    unittest.main()
