#!/usr/bin/env python3
"""learn-collector — emit JSONL events to state/learning-{instance}.jsonl.

Layer 1 of the loop-redesign learning system (per
notes/loop-redesign-goal-iteration-2026-05-03.md).

Watches:
- tasks/        for new task files                  -> kind="incoming"
- results/      for new result files                -> kind="produced"
- tasks/archive/ for task->archive transitions      -> kind="archived"

One file per instance, append-only. Sync-safe under rsync-mtime-wins
because each instance writes only to its own learning-<instance>.jsonl.

Run: python3 src/learn-collector.py
"""

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results"
ARCHIVE_DIR = TASKS_DIR / "archive"

POLL_INTERVAL_S = int(os.environ.get("LEARN_COLLECTOR_INTERVAL_S", "5"))


def resolve_instance() -> str:
    identity_path = ROOT / "stand-identity.json"
    if identity_path.exists():
        try:
            data = json.loads(identity_path.read_text())
            machine = data.get("machine") or data.get("instance")
            if machine:
                return machine
        except Exception:
            pass
    host = socket.gethostname().lower().replace(".local", "")
    if "mini" in host:
        return "mini"
    if "macbook" in host or "mbp" in host:
        return "macbook"
    return host or "unknown"


INSTANCE = resolve_instance()
JSONL_PATH = STATE_DIR / f"learning-{INSTANCE}.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(source: str, kind: str, data: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    event = {
        "ts": now_iso(),
        "instance": INSTANCE,
        "source": source,
        "kind": kind,
        "data": data,
    }
    with JSONL_PATH.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def parse_task_meta(path: Path) -> dict:
    meta: dict = {"task_id": path.stem}
    try:
        text = path.read_text(errors="replace")
        meta["size_bytes"] = len(text)
        for line in text.splitlines()[:12]:
            if line.startswith("source:"):
                meta["channel_source"] = line.split(":", 1)[1].strip()
            elif line.startswith("user_id:"):
                meta["user_id"] = line.split(":", 1)[1].strip()
            elif line.startswith("channel_id:"):
                meta["channel_id"] = line.split(":", 1)[1].strip()
            elif line.startswith("access_tier:"):
                meta["access_tier"] = line.split(":", 1)[1].strip()
    except Exception as e:
        meta["parse_error"] = str(e)
    return meta


def bootstrap_seen(directory: Path, glob: str) -> set:
    if not directory.exists():
        return set()
    return {p.name for p in directory.glob(glob)}


def main() -> int:
    seen_tasks = bootstrap_seen(TASKS_DIR, "task-*.txt")
    seen_results = bootstrap_seen(RESULTS_DIR, "task-*.txt")
    seen_archived = bootstrap_seen(ARCHIVE_DIR, "task-*.txt")
    emit(
        "collector",
        "started",
        {
            "interval_s": POLL_INTERVAL_S,
            "jsonl": str(JSONL_PATH),
            "bootstrap_counts": {
                "tasks": len(seen_tasks),
                "results": len(seen_results),
                "archived": len(seen_archived),
            },
        },
    )
    while True:
        try:
            for p in TASKS_DIR.glob("task-*.txt"):
                if p.name in seen_tasks:
                    continue
                seen_tasks.add(p.name)
                emit("tasks", "incoming", parse_task_meta(p))

            for p in RESULTS_DIR.glob("task-*.txt"):
                if p.name in seen_results:
                    continue
                seen_results.add(p.name)
                try:
                    size = p.stat().st_size
                except OSError:
                    size = -1
                emit("results", "produced", {"task_id": p.stem, "size_bytes": size})

            if ARCHIVE_DIR.exists():
                for p in ARCHIVE_DIR.glob("task-*.txt"):
                    if p.name in seen_archived:
                        continue
                    seen_archived.add(p.name)
                    emit("tasks", "archived", {"task_id": p.stem})
        except Exception as e:
            emit("collector", "loop_error", {"error": repr(e)})
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        emit("collector", "stopped", {})
        sys.exit(0)
