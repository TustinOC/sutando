#!/usr/bin/env python3
"""Tests for src/retrieve_examples.py — top-K similarity retrieval over the
extracted corpus.

Run: python3 tests/retrieve-examples.test.py
Exit: 0 on pass, 1 on fail.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class CommonSetup(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("SUTANDO_WORKSPACE",)}
        self.tmp = Path(tempfile.mkdtemp(prefix="retrieve-examples-"))
        os.environ["SUTANDO_WORKSPACE"] = str(self.tmp)
        sys.modules.pop("retrieve_examples", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            elif k in os.environ:
                del os.environ[k]
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        sys.modules.pop("retrieve_examples", None)


class TestScore(CommonSetup):
    def test_input_match_returns_positive_score(self):
        import retrieve_examples
        row = {"input": "draft an email", "output": "Drafted."}
        s = retrieve_examples.score("draft an email", row)
        self.assertGreater(s, 0.5)

    def test_no_input_returns_zero(self):
        import retrieve_examples
        s = retrieve_examples.score("anything", {"input": None, "output": "voice prose"})
        self.assertEqual(s, 0.0)

    def test_protocol_marker_outputs_return_zero(self):
        """Bridge protocol markers ([dedup], [no-send], etc) aren't owner-
        voice replies — they shouldn't count as few-shot examples. Marker
        set matches CLAUDE.md "Result-body protocol markers" and
        `src/result_markers.py` (PR #874)."""
        import retrieve_examples
        for marker in ("[dedup: task-X]", "[no-send]", "[REPLIED]"):
            row = {"input": "draft an email", "output": marker + "\n"}
            self.assertEqual(retrieve_examples.score("draft an email", row), 0.0,
                             f"marker {marker!r} should yield score 0")

    def test_case_insensitive(self):
        """Query and input case shouldn't matter for matching."""
        import retrieve_examples
        s1 = retrieve_examples.score("Draft An Email", {"input": "draft an email", "output": "x"})
        s2 = retrieve_examples.score("draft an email", {"input": "Draft An Email", "output": "x"})
        self.assertEqual(s1, s2)


class TestRetrieve(CommonSetup):
    def _seed(self, rows: list) -> Path:
        path = self.tmp / "data" / "corpus" / "extracted-paired-2026-05-19.jsonl"
        _write_jsonl(path, rows)
        return path

    def test_returns_empty_when_corpus_missing(self):
        import retrieve_examples
        results = retrieve_examples.retrieve("anything", workspace=self.tmp)
        self.assertEqual(results, [])

    def test_returns_top_k_in_score_order(self):
        self._seed([
            {"id": "a", "input": "exact match string", "output": "ra"},
            {"id": "b", "input": "completely different topic", "output": "rb"},
            {"id": "c", "input": "exact match strung", "output": "rc"},
        ])
        import retrieve_examples
        results = retrieve_examples.retrieve("exact match string", workspace=self.tmp, k=2)
        ids = [r["id"] for r in results]
        # a (perfect match) wins, c (one-char swap) second, b excluded by k=2
        self.assertEqual(ids[0], "a")
        self.assertEqual(len(results), 2)
        # Scores monotonically descending
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_zero_score_rows_excluded(self):
        """Rows with score==0 (no input, or protocol marker) are dropped
        even when k would allow them."""
        self._seed([
            {"id": "real", "input": "draft an email", "output": "Drafted."},
            {"id": "marker", "input": "draft an email", "output": "[dedup: x]"},
            {"id": "no-input", "input": None, "output": "orphan"},
        ])
        import retrieve_examples
        results = retrieve_examples.retrieve("draft an email", workspace=self.tmp, k=10)
        ids = [r["id"] for r in results]
        self.assertEqual(ids, ["real"])

    def test_picks_newest_corpus_when_multiple(self):
        """Workspace has both an old and a new paired corpus → use newest."""
        old = self.tmp / "data" / "corpus" / "extracted-paired-2026-05-18.jsonl"
        new = self.tmp / "data" / "corpus" / "extracted-paired-2026-05-19.jsonl"
        _write_jsonl(old, [{"id": "old-row", "input": "query", "output": "OLD"}])
        _write_jsonl(new, [{"id": "new-row", "input": "query", "output": "NEW"}])
        # Backdate the older file so mtime ordering is unambiguous.
        import time
        os.utime(old, (time.time() - 86400, time.time() - 86400))
        os.utime(new, (time.time(), time.time()))
        import retrieve_examples
        results = retrieve_examples.retrieve("query", workspace=self.tmp, k=5)
        ids = [r["id"] for r in results]
        self.assertEqual(ids, ["new-row"])

    def test_explicit_corpus_path_overrides_default(self):
        """--corpus <path> bypasses workspace auto-discovery."""
        custom = self.tmp / "elsewhere.jsonl"
        _write_jsonl(custom, [{"id": "custom", "input": "exact query", "output": "out"}])
        import retrieve_examples
        results = retrieve_examples.retrieve("exact query", corpus_path=custom,
                                              workspace=self.tmp, k=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "custom")

    def test_malformed_lines_skipped(self):
        """Defensive: a corrupted line in the corpus doesn't crash retrieval."""
        path = self.tmp / "data" / "corpus" / "extracted-paired-2026-05-19.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"id": "ok", "input": "query", "output": "x"}\n'
            "not-json-at-all\n"
            '{"id": "ok2", "input": "query2", "output": "y"}\n'
        )
        import retrieve_examples
        results = retrieve_examples.retrieve("query", workspace=self.tmp, k=5)
        # The good rows came through; the malformed line was silently dropped.
        ids = sorted(r["id"] for r in results)
        self.assertEqual(ids, ["ok", "ok2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
