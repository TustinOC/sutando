#!/usr/bin/env python3
"""Report metrics for the multi-core agent pool (#880, #884).

Pairs task files with their corresponding result files via task ID, then
reports total task time (sum of per-task processing latencies), wallclock
time (max end - min start), per-channel breakdown, and per-core distribution.

Usage:
    python3 scripts/pool-metrics.py [--since=MINUTES]

Defaults to --since=15 (last 15 minutes). Reads from $SUTANDO_WORKSPACE
(or ~/.sutando/workspace).

Output:
    A summary table per channel, then the pool-level rollup.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path


def workspace_root() -> Path:
    env = os.environ.get("SUTANDO_WORKSPACE")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".sutando" / "workspace"


TASK_FILENAME_RE = re.compile(r"^task-(?P<id>[a-zA-Z0-9._-]+?)(?:\.claimed-core-(?P<core>[a-zA-Z0-9._-]+))?\.txt$")


def parse_task_metadata(text: str) -> dict[str, str]:
    """Pull simple `key: value` fields from a task body.

    Tasks have the format:
        id: ...
        timestamp: ...
        task: [Discord @qingyunwu] ...
        source: discord
        channel_id: 1485653767402553457
        ...

    We only need a few fields, so a flat line-scan is sufficient.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if ":" in raw:
            k, _, v = raw.partition(":")
            k = k.strip()
            v = v.strip()
            if k and v:
                out[k] = v
    return out


def collect_pairs(ws: Path, since_sec: float) -> list[dict]:
    """Scan tasks (live + archived) and results, pair them by ID, return
    a list of paired records. `since_sec` filters to tasks whose mtime is
    within the last `since_sec` seconds (use a wide value for a full pool
    test run)."""
    now = time.time()
    cutoff = now - since_sec
    tasks_dirs = [ws / "tasks", ws / "tasks" / "archive"]
    results_dirs = [ws / "results", ws / "results" / "archive"]

    # Index results by task ID for fast lookup.
    result_index: dict[str, tuple[Path, float]] = {}
    for rdir in results_dirs:
        if not rdir.exists():
            continue
        for p in rdir.rglob("task-*.txt"):
            m = TASK_FILENAME_RE.match(p.name)
            if not m:
                continue
            tid = m.group("id")
            mtime = p.stat().st_mtime
            # Keep the latest result for any given task id
            if tid not in result_index or mtime > result_index[tid][1]:
                result_index[tid] = (p, mtime)

    pairs: list[dict] = []
    for tdir in tasks_dirs:
        if not tdir.exists():
            continue
        for p in tdir.rglob("task-*.txt"):
            m = TASK_FILENAME_RE.match(p.name)
            if not m:
                continue
            tid = m.group("id")
            assigned_core = m.group("core")  # may be None if not yet claimed
            mtime = p.stat().st_mtime
            if mtime < cutoff:
                continue
            try:
                body = p.read_text(errors="replace")
            except OSError:
                continue
            meta = parse_task_metadata(body)
            result = result_index.get(tid)
            pairs.append({
                "task_id": tid,
                "task_path": p,
                "task_mtime": mtime,
                "core": assigned_core or meta.get("claimed_core", "?"),
                "channel_id": meta.get("channel_id", "(none)"),
                "source": meta.get("source", "?"),
                "priority": meta.get("priority", "normal"),
                "result_path": result[0] if result else None,
                "result_mtime": result[1] if result else None,
                "latency_sec": (result[1] - mtime) if result else None,
            })
    return pairs


def fmt_seconds(s: float | None) -> str:
    if s is None:
        return "—"
    if s < 1:
        return f"{s*1000:.0f}ms"
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{s/60:.1f}min"
    return f"{s/3600:.1f}h"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", type=int, default=15, help="Look back N minutes (default 15)")
    args = ap.parse_args(argv[1:])

    ws = workspace_root()
    since_sec = args.since * 60
    pairs = collect_pairs(ws, since_sec)

    if not pairs:
        print(f"No tasks in the last {args.since} min under {ws}")
        return 0

    completed = [p for p in pairs if p["result_mtime"] is not None]
    pending = [p for p in pairs if p["result_mtime"] is None]

    # Per-channel rollup
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_channel[p["channel_id"]].append(p)

    # Per-core rollup
    by_core: dict[str, list[dict]] = defaultdict(list)
    for p in completed:
        by_core[p["core"]].append(p)

    print(f"Pool metrics — last {args.since} min under {ws}")
    print(f"  Window: {args.since*60}s")
    print(f"  Tasks observed: {len(pairs)} ({len(completed)} completed, {len(pending)} pending)")
    print()

    # Pool-level totals
    if completed:
        total_task_time = sum(p["latency_sec"] for p in completed)
        wallclock_start = min(p["task_mtime"] for p in completed)
        wallclock_end = max(p["result_mtime"] for p in completed)
        wallclock = wallclock_end - wallclock_start
        speedup = total_task_time / wallclock if wallclock > 0 else 0
        print("Pool totals:")
        print(f"  total task time (sum of per-task latency): {fmt_seconds(total_task_time)}")
        print(f"  wallclock time (max end - min start):      {fmt_seconds(wallclock)}")
        print(f"  speedup ratio (total / wallclock):         {speedup:.2f}x  (1.00 = serial, >1.00 = parallel)")
        print()

    # Per-channel
    if by_channel:
        print("Per-channel:")
        print(f"  {'channel_id':40} {'tasks':>6} {'avg latency':>12} {'cores':>10}")
        for ch, items in sorted(by_channel.items()):
            done = [i for i in items if i["latency_sec"] is not None]
            avg = sum(i["latency_sec"] for i in done) / len(done) if done else None
            cores_seen = sorted({i["core"] for i in done if i["core"] != "?"})
            cores_str = ",".join(cores_seen) if cores_seen else "—"
            print(f"  {ch[:40]:40} {len(items):>6} {fmt_seconds(avg):>12} {cores_str:>10}")
        print()

    # Per-core
    if by_core:
        print("Per-core:")
        print(f"  {'core':>6} {'tasks':>6} {'total time':>12} {'channels handled':>20}")
        for core, items in sorted(by_core.items()):
            total = sum(i["latency_sec"] for i in items)
            channels = sorted({i["channel_id"] for i in items})
            ch_str = ",".join(c[-8:] for c in channels)  # last 8 chars to keep table narrow
            print(f"  {core:>6} {len(items):>6} {fmt_seconds(total):>12} {ch_str:>20}")
        print()

    # Affinity verification — for each channel, did all tasks go to one core?
    print("Channel-affinity health (within this window):")
    bad = 0
    for ch, items in sorted(by_channel.items()):
        done = [i for i in items if i["core"] != "?" and i["latency_sec"] is not None]
        if not done:
            continue
        cores = sorted({i["core"] for i in done})
        if len(cores) > 1:
            print(f"  ⚠ {ch}: split across cores {cores} ({len(done)} tasks). Possible cause: handler-died or idle-window-elapsed.")
            bad += 1
    if bad == 0:
        print("  ✓ every channel served by exactly one core within the window")
    print()

    if pending:
        print("Pending tasks (not yet completed):")
        for p in pending[:10]:
            age = time.time() - p["task_mtime"]
            print(f"  task-{p['task_id']}  channel={p['channel_id']}  age={fmt_seconds(age)}  core={p['core']}")
        if len(pending) > 10:
            print(f"  ... ({len(pending) - 10} more)")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
