#!/usr/bin/env python3
"""Tests for src/extract_corpus.py — owner-style (task, reply) pair extractor.

Run: python3 tests/extract-corpus.test.py
Exit: 0 on pass, 1 on fail.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _write_task(ws: Path, task_id: str, *, source: str = "voice",
                access_tier: str = "owner", body: str = "draft an email") -> Path:
    (ws / "tasks" / "processed").mkdir(parents=True, exist_ok=True)
    p = ws / "tasks" / "processed" / f"{task_id}.txt"
    p.write_text(
        f"id: {task_id}\n"
        f"timestamp: 2026-05-13T23:34:32.855Z\n"
        f"task: {body}\n"
        f"source: {source}\n"
        f"channel_id: local-{source}\n"
        f"user_id: {source}-local\n"
        f"access_tier: {access_tier}\n"
    )
    return p


def _write_reply(ws: Path, task_id: str, *, month: str = "2026-05",
                 body: str = "Drafted. Here's the email.\n") -> Path:
    (ws / "results" / "archive" / month).mkdir(parents=True, exist_ok=True)
    p = ws / "results" / "archive" / month / f"{task_id}.txt"
    p.write_text(body)
    return p


class CommonSetup(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("SUTANDO_WORKSPACE",)}
        self.tmp = Path(tempfile.mkdtemp(prefix="extract-corpus-"))
        os.environ["SUTANDO_WORKSPACE"] = str(self.tmp)
        sys.modules.pop("extract_corpus", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            elif k in os.environ:
                del os.environ[k]
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        sys.modules.pop("extract_corpus", None)


class TestPairedMode(CommonSetup):
    def test_empty_workspace_yields_zero_rows(self):
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp)
        self.assertEqual(rows, [])

    def test_task_with_matching_reply_yields_one_row(self):
        _write_task(self.tmp, "task-abc", body="draft email")
        _write_reply(self.tmp, "task-abc", body="Drafted.\n")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], "task-abc")
        self.assertEqual(row["source"], "voice")
        self.assertEqual(row["access_tier"], "owner")
        self.assertEqual(row["input"], "draft email")
        self.assertEqual(row["output"], "Drafted.\n")

    def test_task_without_reply_is_skipped(self):
        _write_task(self.tmp, "task-orphan")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp)
        self.assertEqual(rows, [])

    def test_non_owner_tier_filtered_by_default(self):
        _write_task(self.tmp, "task-team", access_tier="team")
        _write_reply(self.tmp, "task-team")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp)
        self.assertEqual(rows, [],
                         "team-tier task should be filtered out by default")

    def test_non_owner_tier_included_with_flag(self):
        _write_task(self.tmp, "task-team", access_tier="team")
        _write_reply(self.tmp, "task-team")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp, include_non_owner=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["access_tier"], "team")

    def test_multiple_pairs_extracted_in_id_order(self):
        _write_task(self.tmp, "task-a", body="a-prompt")
        _write_task(self.tmp, "task-b", body="b-prompt")
        _write_reply(self.tmp, "task-a", body="a-reply\n")
        _write_reply(self.tmp, "task-b", body="b-reply\n")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["task-a", "task-b"])

    def test_reply_in_different_month_dir_is_found(self):
        """The pair extractor must scan ALL month-partitioned dirs, not just
        the current month — replies may have aged into earlier partitions."""
        _write_task(self.tmp, "task-aged")
        _write_reply(self.tmp, "task-aged", month="2026-04")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp)
        self.assertEqual(len(rows), 1)

    def test_most_recent_reply_wins_when_id_appears_in_multiple_months(self):
        _write_task(self.tmp, "task-rerun")
        old = _write_reply(self.tmp, "task-rerun", month="2026-04", body="OLD\n")
        new = _write_reply(self.tmp, "task-rerun", month="2026-05", body="NEW\n")
        # Backdate the old one explicitly so mtime ordering is unambiguous.
        os.utime(old, (time.time() - 86400, time.time() - 86400))
        os.utime(new, (time.time(), time.time()))
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["output"], "NEW\n")


class TestSamplesOnlyMode(CommonSetup):
    def test_orphan_reply_yields_a_row(self):
        """A reply whose task header is no longer in tasks/processed/ — the
        common case (replies retained longer than task headers)."""
        _write_reply(self.tmp, "task-orphan", body="bot voice prose\n")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp, samples_only=True)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], "task-orphan")
        self.assertIsNone(row["input"])
        self.assertEqual(row["output"], "bot voice prose\n")

    def test_paired_replies_excluded_in_samples_mode(self):
        """When a task header IS still around, the row would also land in
        paired mode — we exclude it in samples-only to avoid duplication."""
        _write_task(self.tmp, "task-paired")
        _write_reply(self.tmp, "task-paired", body="paired-output\n")
        _write_reply(self.tmp, "task-orphan-too", body="orphan-output\n")
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp, samples_only=True)
        ids = [r["id"] for r in rows]
        self.assertNotIn("task-paired", ids)
        self.assertIn("task-orphan-too", ids)

    def test_samples_mode_emits_nothing_when_archive_empty(self):
        import extract_corpus
        rows = extract_corpus.extract(workspace=self.tmp, samples_only=True)
        self.assertEqual(rows, [])


class TestOutputPaths(CommonSetup):
    """Paired and samples-only modes must default to DIFFERENT filenames so
    one doesn't silently overwrite the other when both are run same-day.
    (Real bug caught during the script's first smoke test, 2026-05-19.)"""

    def test_default_paths_differ_by_mode(self):
        import extract_corpus
        paired = extract_corpus._default_output(self.tmp, samples_only=False)
        samples = extract_corpus._default_output(self.tmp, samples_only=True)
        self.assertNotEqual(paired, samples)
        self.assertIn("paired", paired.name)
        self.assertIn("samples", samples.name)

    def test_write_jsonl_round_trip(self):
        rows = [
            {"id": "task-a", "input": "in-a", "output": "out-a"},
            {"id": "task-b", "input": "in-b", "output": "out-b"},
        ]
        out = self.tmp / "data" / "corpus" / "test.jsonl"
        import extract_corpus
        extract_corpus.write_jsonl(rows, out)
        self.assertTrue(out.is_file())
        loaded = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual(loaded, rows)
        # Tmp file should be gone (atomic rename completed)
        self.assertFalse(out.with_suffix(".jsonl.tmp").exists())


class TestHeaderParsing(CommonSetup):
    def test_parse_task_with_blank_body(self):
        """When the task file has no body lines after the header, the `task:`
        header value is used as input."""
        p = _write_task(self.tmp, "task-no-body", body="just-header-task")
        import extract_corpus
        parsed = extract_corpus._parse_task_file(p)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["body"], "just-header-task")

    def test_parse_task_with_body_overrides_inline(self):
        """When BOTH a `task:` line and a separate body exist, the body wins —
        bridges sometimes put a stub in task: and the real prompt below."""
        p = self.tmp / "tasks" / "processed" / "task-explicit.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "id: task-explicit\n"
            "task: short stub\n"
            "source: discord\n"
            "access_tier: owner\n"
            "\n"
            "This is the real long prompt body that came from Discord.\n"
        )
        import extract_corpus
        parsed = extract_corpus._parse_task_file(p)
        self.assertIsNotNone(parsed)
        self.assertIn("real long prompt body", parsed["body"])

    def test_parse_task_missing_id_returns_none(self):
        p = self.tmp / "tasks" / "processed" / "task-bad.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("source: voice\nno id here\n")
        import extract_corpus
        self.assertIsNone(extract_corpus._parse_task_file(p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
