#!/usr/bin/env python3
"""dashboard-data.py — collect task-room state into one JSON for the
client-agnostic task dashboard (re-home of the Element-injected task card).

Reads the local room registry (state/task-rooms.json) and, for each room,
pulls its `space.ag2.task.*` state via the relay (taskroom helpers). Emits a
JSON document the static dashboard renders. Read-only: no state is mutated.

Usage:
  python3 dashboard-data.py            # print JSON to stdout
  python3 dashboard-data.py -o out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import taskroom as t  # noqa: E402

NS = t.NS

_GATE_ORDER = {"now": 0, "next": 1, "ready": 1, "after": 3, "awaiting-task": 3,
               "needs_human": 4, "awaiting-you": 4, "scheduled": 5,
               "awaiting-time": 5, "backlog": 9, "": 6}
_PRI_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3, "": 2}


def collect() -> dict:
    rooms = []
    for r in t._registry_load():
        room = r["room"]
        try:
            st = t._get_states(room)
        except Exception as e:  # noqa: BLE001
            rooms.append({"room": room, "goal": r.get("goal", ""), "error": str(e)})
            continue
        goal = st.get((f"{NS}.goal", ""), {})
        status = st.get((f"{NS}.status", ""), {})
        plan = st.get((f"{NS}.plan", ""), {})
        sched = status.get("schedule") or {}
        artifacts = [c for (et, _k), c in st.items() if et == f"{NS}.artifact"]
        assets = t._assets(st)
        steps = plan.get("steps", [])
        done = sum(1 for s in steps if s.get("status") == "done")
        rooms.append({
            "room": room,
            "goal": goal.get("goal") or r.get("goal", ""),
            "status": status.get("status", "submitted"),
            "when": sched.get("when", ""),
            "priority": sched.get("priority", ""),
            "needs": sched.get("needs", ""),
            "at": sched.get("at", ""),
            "after_room": sched.get("after_room", ""),
            "steps": [{"id": s.get("id"), "title": s.get("title", s.get("id")),
                       "status": s.get("status", "todo"),
                       "needs": s.get("needs", "")} for s in steps],
            "progress": [done, len(steps)],
            "artifacts": [{"title": a.get("title", ""), "mime": a.get("mime", "")}
                          for a in artifacts],
            # Room assets (v1, room !kQRxkWDICYxuQZRONo): decisions / context
            # (params + conclusions) / resources, straight from task.* state.
            "assets": {
                "decisions": [{"id": d["id"], "what": d.get("what", ""),
                               "by": d.get("by", ""), "why": d.get("why", "")}
                              for d in assets["decisions"]],
                "context": [{"key": c["key"], "value": c.get("value", ""),
                             "kind": c.get("kind", "param")}
                            for c in assets["context"]],
                "resources": [{"id": r["id"], "title": r.get("title", ""),
                               "ref": r.get("ref", ""), "kind": r.get("kind", "")}
                              for r in assets["resources"]],
            },
            "updated_at": status.get("updated_at", 0),
        })
    # sort: completed last; then by gate; then by priority
    rooms.sort(key=lambda x: (x.get("status") == "completed",
                              _GATE_ORDER.get(x.get("when", ""), 6),
                              _PRI_ORDER.get(x.get("priority", ""), 2)))
    needs_you = [r for r in rooms if r.get("when") in ("needs_human", "awaiting-you")
                 and r.get("status") != "completed"]
    return {"generated_at": int(time.time()), "rooms": rooms,
            "needs_you_count": len(needs_you)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="")
    a = ap.parse_args()
    data = json.dumps(collect(), indent=2)
    if a.out:
        Path(a.out).write_text(data)
        print(f"wrote {a.out}")
    else:
        print(data)


if __name__ == "__main__":
    main()
