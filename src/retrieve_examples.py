#!/usr/bin/env python3
"""Retrieve top-K most similar past (input, output) pairs from the extracted
corpus. Keyword-similarity-only (difflib) — no embeddings yet. The bridge or
proactive-loop calls this with the current task body to get a few-shot
context of owner-style replies to similar past tasks.

Personalization design §6.2 — "RAG-first" stand-up. Output JSONL pairs feed
naturally into a Claude prompt: "Here are 5 past tasks like this one and how
you replied; reply to the new one in the same voice."

CLI:
    python3 src/retrieve_examples.py "draft an email about the meeting" --k 3
    python3 src/retrieve_examples.py --query-file path/to/prompt.txt --k 5

Output: JSON list to stdout. Each row = a corpus row with an added `score`.

Corpus selection (most-recent paired file by default):
    Picks the newest `<workspace>/data/corpus/extracted-paired-*.jsonl`.
    Override with --corpus <path>.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_default import resolve_workspace  # noqa: E402

WORKSPACE = resolve_workspace(migrate=False)


def _newest_paired_corpus(workspace: Path) -> Path | None:
    corpus_dir = workspace / "data" / "corpus"
    if not corpus_dir.is_dir():
        return None
    candidates = sorted(
        corpus_dir.glob("extracted-paired-*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _load_jsonl(path: Path) -> list:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Skip malformed lines rather than crash — a future writer that
            # appends partially is not a query-time concern.
            continue
    return rows


# Mirrors `result_markers._SKIP_PATTERNS` (CLAUDE.md "Result-body protocol
# markers"). If/when PR #874 lands, import that module's constant instead of
# duplicating here.
_PROTOCOL_MARKER_PREFIXES = ("[dedup", "[no-send]", "[REPLIED]")


def score(query: str, row: dict) -> float:
    """Difflib SequenceMatcher ratio against the row's `input`. Returns 0.0
    if the row is unusable as a few-shot example: no input (orphan samples-
    mode rows), or output is a bridge protocol marker (`[dedup: ...]`,
    `[no-send]`, etc — see CLAUDE.md "Result-body protocol markers"). The
    markers are deliveries-affecting metadata, not owner-voice replies."""
    inp = row.get("input") or ""
    if not inp:
        return 0.0
    out = (row.get("output") or "").lstrip()
    if any(out.startswith(p) for p in _PROTOCOL_MARKER_PREFIXES):
        return 0.0
    return difflib.SequenceMatcher(a=query.lower(), b=inp.lower()).ratio()


def retrieve(query: str, *, corpus_path: Path | None = None, k: int = 5,
             workspace: Path | None = None) -> list:
    if workspace is None:
        workspace = WORKSPACE
    if corpus_path is None:
        corpus_path = _newest_paired_corpus(workspace)
    if corpus_path is None or not corpus_path.is_file():
        return []
    rows = _load_jsonl(corpus_path)
    scored = [(score(query, r), r) for r in rows]
    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for s, r in scored[:max(0, k)]:
        if s <= 0:
            break
        row = dict(r)
        row["score"] = round(s, 4)
        out.append(row)
    return out


def _parse_args(argv: list | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("query", nargs="?", default=None,
                   help="query string (positional). Mutually exclusive with --query-file.")
    p.add_argument("--query-file", type=Path, default=None,
                   help="read query from this file instead of CLI arg")
    p.add_argument("--k", type=int, default=5, help="top-K results (default: 5)")
    p.add_argument("--corpus", type=Path, default=None,
                   help="corpus JSONL path (default: newest extracted-paired-*.jsonl in workspace)")
    return p.parse_args(argv)


def main(argv: list | None = None) -> int:
    args = _parse_args(argv)
    if args.query_file:
        query = args.query_file.read_text().strip()
    elif args.query:
        query = args.query
    else:
        print("error: provide a positional query or --query-file", file=sys.stderr)
        return 2
    results = retrieve(query, corpus_path=args.corpus, k=args.k)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
