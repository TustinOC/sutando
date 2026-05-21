#!/usr/bin/env python3
"""Extract owner-style (task, reply) pairs from the workspace into JSONL.

Personalization design §6.1 first concrete step. Walks
`<workspace>/tasks/processed/` and pairs each task with its matching
`<workspace>/results/archive/<yyyy-mm>/task-<id>.txt`. Output is a single
JSONL file ready to feed an embedding-index (RAG) or an instruction-tune
(LoRA) — same shape works for both.

Output schema (one JSON object per line):
    {
      "id": "task-1778715272855",
      "source": "voice|discord|telegram|slack|api|...",
      "ts": "2026-05-13T23:34:32.855Z",
      "channel_id": "local-voice",
      "user_id": "voice-local",
      "access_tier": "owner",
      "input": "Draft an email based on user's input...",
      "output": "Drafted. Vasiliy's email is..."
    }

Modes:
  --paired (default): only emit rows where BOTH the task and its archive
      reply exist on disk. Highest-signal supervised training data.
  --samples-only: emit reply-only rows for archived `task-*.txt` whose
      task header is no longer in tasks/processed/. Lower-signal but
      useful for tone-cloning where the prompt is unrecoverable.

Filters:
  --owner-only (default): only access_tier=owner. Personalization
      shouldn't train on other people's reply style.
  --include-non-owner: opt-in to drop the filter (e.g. team-tier corpus
      for a different model).

Output path:
  `<workspace>/data/corpus/extracted-<YYYY-MM-DD>.jsonl` by default.
  --output <path> overrides.

CLI examples:
    python3 src/extract_corpus.py                 # paired, owner-only, default path
    python3 src/extract_corpus.py --json-summary  # also print a count summary
    python3 src/extract_corpus.py --samples-only  # reply-only mode
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_default import resolve_workspace  # noqa: E402

WORKSPACE = resolve_workspace(migrate=False)


# Header keys we care about extracting. Anything else in the task file
# header is preserved in `metadata` so downstream tooling can grep.
_HEADER_KEYS = ("id", "timestamp", "source", "channel_id", "user_id", "access_tier", "priority")


def _parse_task_file(path: Path) -> dict | None:
    """Parse a task-<id>.txt header + body. The format is `key: value` lines
    until a blank line OR end-of-file, followed by an optional free-form body.
    Returns None if the file is unparseable (no `id:` line)."""
    try:
        text = path.read_text()
    except OSError:
        return None
    header: dict = {}
    body_lines: list = []
    in_body = False
    for line in text.splitlines():
        if in_body:
            body_lines.append(line)
            continue
        if not line.strip():
            in_body = True
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip().lower()
            v = v.strip()
            header[k] = v
        else:
            # Header looks like prose — treat as body from here on.
            in_body = True
            body_lines.append(line)
    if "id" not in header:
        return None
    # The `task:` line often IS the input; when the file body is empty,
    # fall through to it. When both exist (some bridges duplicate), keep
    # the body — it's the longer, fuller form.
    body = "\n".join(body_lines).strip()
    task_inline = header.get("task", "")
    if not body and task_inline:
        body = task_inline
    return {"header": header, "body": body, "path": str(path)}


def _build_reply_index(workspace: Path) -> dict:
    """Walk `results/archive/<yyyy-mm>/` once, return `{task_id: latest_path}`.
    Cheaper than per-task scans when extracting many pairs — extract() calls
    this once and looks up by id."""
    archive_root = workspace / "results" / "archive"
    index: dict = {}
    if not archive_root.is_dir():
        return index
    for month_dir in archive_root.iterdir():
        if not month_dir.is_dir():
            continue
        for path in month_dir.glob("task-*.txt"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            existing = index.get(path.stem)
            if existing is None or mtime > existing[1]:
                index[path.stem] = (path, mtime)
    return {k: v[0] for k, v in index.items()}


def _row_for_pair(task: dict, reply_path: Path) -> dict:
    """Compose one output row from a parsed task + its reply file."""
    header = task["header"]
    try:
        reply_text = reply_path.read_text()
    except OSError as e:
        reply_text = f"[unreadable: {e}]"
    return {
        "id": header.get("id", reply_path.stem),
        "source": header.get("source", "unknown"),
        "ts": header.get("timestamp"),
        "channel_id": header.get("channel_id"),
        "user_id": header.get("user_id"),
        "access_tier": header.get("access_tier"),
        "input": task["body"] or header.get("task", ""),
        "output": reply_text,
    }


def _row_for_orphan_reply(reply_path: Path) -> dict:
    """Compose a row from an archived reply whose task header is no longer
    on disk (samples-only mode). Source/ts unknown — that's the cost."""
    task_id = reply_path.stem
    try:
        reply_text = reply_path.read_text()
    except OSError as e:
        reply_text = f"[unreadable: {e}]"
    return {
        "id": task_id,
        "source": "unknown",
        "ts": None,
        "channel_id": None,
        "user_id": None,
        "access_tier": "owner",  # archive replies are pre-owner-filtered by bridges
        "input": None,
        "output": reply_text,
    }


def extract(
    *,
    workspace: Path | None = None,
    samples_only: bool = False,
    include_non_owner: bool = False,
) -> list:
    """Walk the workspace and return a list of rows ready to write."""
    if workspace is None:
        workspace = WORKSPACE
    rows: list = []
    if not samples_only:
        processed = workspace / "tasks" / "processed"
        if processed.is_dir():
            reply_index = _build_reply_index(workspace)
            for task_file in sorted(processed.glob("task-*.txt")):
                parsed = _parse_task_file(task_file)
                if parsed is None:
                    continue
                if not include_non_owner and parsed["header"].get("access_tier") != "owner":
                    continue
                reply = reply_index.get(parsed["header"]["id"])
                if reply is None:
                    continue
                rows.append(_row_for_pair(parsed, reply))
    else:
        archive_root = workspace / "results" / "archive"
        # Index task IDs that DO have a processed header so we can EXCLUDE
        # them in samples-only mode (avoids duplicating paired rows).
        processed_ids: set = set()
        processed = workspace / "tasks" / "processed"
        if processed.is_dir():
            processed_ids = {p.stem for p in processed.glob("task-*.txt")}
        if archive_root.is_dir():
            for month_dir in sorted(archive_root.iterdir()):
                if not month_dir.is_dir():
                    continue
                for reply_path in sorted(month_dir.glob("task-*.txt")):
                    if reply_path.stem in processed_ids:
                        continue
                    rows.append(_row_for_orphan_reply(reply_path))
    return rows


def write_jsonl(rows: list, output: Path) -> Path:
    """Atomic write of one row per line. Same tmp+rename pattern as
    pool_status.write_record()."""
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    with tmp.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
    tmp.replace(output)
    return output


def _default_output(workspace: Path, samples_only: bool = False) -> Path:
    """Default to a mode-tagged filename so paired and samples-only runs
    don't silently overwrite each other when both are run on the same day."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    tag = "samples" if samples_only else "paired"
    return workspace / "data" / "corpus" / f"extracted-{tag}-{today}.jsonl"


def _parse_args(argv: list | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--samples-only", action="store_true",
                   help="emit reply-only rows (orphan archived replies) instead of paired rows")
    p.add_argument("--include-non-owner", action="store_true",
                   help="drop the access_tier=owner filter (default: owner-only)")
    p.add_argument("--output", type=Path, default=None,
                   help="output path (default: <workspace>/data/corpus/extracted-YYYY-MM-DD.jsonl)")
    p.add_argument("--json-summary", action="store_true",
                   help="print a JSON summary (row counts by source) after writing")
    return p.parse_args(argv)


def _summary(rows: list) -> dict:
    by_source: dict = {}
    for r in rows:
        s = r.get("source") or "unknown"
        by_source[s] = by_source.get(s, 0) + 1
    return {
        "total_rows": len(rows),
        "by_source": by_source,
        "approx_tokens": sum(len(r.get("input") or "") + len(r.get("output") or "") for r in rows) // 4,
    }


def main(argv: list | None = None) -> int:
    args = _parse_args(argv)
    rows = extract(samples_only=args.samples_only, include_non_owner=args.include_non_owner)
    output = args.output or _default_output(WORKSPACE, samples_only=args.samples_only)
    write_jsonl(rows, output)
    summary = _summary(rows)
    if args.json_summary:
        print(json.dumps({"output": str(output), "summary": summary}, indent=2))
    else:
        mode = "samples-only" if args.samples_only else "paired"
        print(
            f"corpus: {summary['total_rows']} rows ({mode}, "
            f"~{summary['approx_tokens']} tokens) → {output}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
