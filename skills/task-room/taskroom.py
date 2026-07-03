#!/usr/bin/env python3
"""taskroom.py — task room as state machine (spec-task-room-v0.2, stdlib only).

A long-running task's durable state lives in a Matrix room as state events
(`space.ag2.task.*`); the timeline carries progress messages. This CLI is the
core's room-writer: it lets the agent create the room, advance the state
machine, and rebuild context after a restart (the resume rule).

Transport: the relay's POST /v1/room endpoint (PR #122) — the machine never
holds a Matrix token; ops run server-side through the agent's relay session.
Credentials: the agent's relay bearer, from (in order) $REMOTE_TASK_TOKEN,
$TASKROOM_TOKEN, or the channel env file at
<claude-home>/channels/ag2space/.env. Relay URL: $REMOTE_TASK_URL
(default https://chat.ag2.space/relay).

"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RELAY = (os.environ.get("REMOTE_TASK_URL") or "https://chat.ag2.space/relay").rstrip("/")
NS = "space.ag2.task"


def _channel_env() -> dict:
    out = {}
    try:
        repo = Path(__file__).resolve().parent.parent.parent
        p = subprocess.run(
            ["bash", str(repo / "scripts" / "sutando-config.sh"),
             "claude-home-path", "channels/ag2space/.env"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        for line in Path(p).read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def _token() -> str:
    tok = (os.environ.get("REMOTE_TASK_TOKEN") or os.environ.get("TASKROOM_TOKEN") or "").strip()
    if tok:
        return tok
    tok = _channel_env().get("REMOTE_TASK_TOKEN", "")
    if tok:
        return tok
    sys.exit("taskroom: no relay bearer — set $REMOTE_TASK_TOKEN or the ag2space channel .env")


def _room_op(op: str, **params) -> dict:
    body = {"op": op, **{k: v for k, v in params.items() if v is not None}}
    req = urllib.request.Request(
        f"{RELAY}/v1/room", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_token()}",
                 "Content-Type": "application/json",
                 "User-Agent": "sutando-taskroom/1.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"taskroom: {op} → HTTP {e.code}: {e.read()[:200]!r}")


def _set_state(room: str, etype: str, state_key: str, content: dict) -> None:
    _room_op("state", room_id=room, type=etype, state_key=state_key, content=content)


def _get_states(room: str) -> dict:
    """All space.ag2.* state, keyed by (type, state_key)."""
    res = _room_op("get_state", room_id=room)
    return {(ev["type"], ev.get("state_key", "")): ev["content"]
            for ev in res.get("events", [])}


def _whoami() -> str:
    return _channel_env().get("AGENT_ID", "") or "@unknown:ag2.space"


_REGISTRY = None
def _registry_path() -> "Path":
    global _REGISTRY
    if _REGISTRY:
        return _REGISTRY
    try:
        repo = Path(__file__).resolve().parent.parent.parent
        ws = subprocess.run(["bash", str(repo / "scripts" / "sutando-config.sh"), "workspace"],
                            capture_output=True, text=True, timeout=15).stdout.strip()
        _REGISTRY = Path(ws) / "state" / "task-rooms.json"
    except Exception:
        _REGISTRY = Path.home() / ".sutando-task-rooms.json"
    return _REGISTRY


def _registry_load() -> list:
    try:
        return json.loads(_registry_path().read_text())
    except Exception:
        return []


def _registry_add(room: str, goal: str) -> None:
    reg = _registry_load()
    if any(r.get("room") == room for r in reg):
        return
    reg.append({"room": room, "goal": goal, "added_at": int(time.time())})
    p = _registry_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(reg, indent=2))
    except Exception:
        pass


def cmd_queue(a) -> None:
    """The autonomous loop's room view: list registered task rooms with their
    lifecycle status + schedule gate, grouped so `ready` work is obvious. This
    is what an idle loop tick consults to decide what to pick up (and what's
    waiting on the owner). Honors the owner directive: when no new task files
    to process, work the `ready`/`next` rooms; skip needs_human/backlog."""
    rows = []
    for r in _registry_load():
        room = r["room"]
        try:
            st = _get_states(room).get((f"{NS}.status", ""), {})
        except Exception:
            st = {}
        life = st.get("status", "?")
        sch = st.get("schedule") or {}
        when = sch.get("when", "")
        pri = sch.get("priority", "")
        gate = when + (f" @ {sch['at']}" if sch.get("at") else "") \
               + (f" after {sch['after_room']}" if sch.get("after_room") else "") \
               + (f" (needs: {sch['needs']})" if sch.get("needs") else "")
        rows.append((life, when, gate, room, r.get("goal", "")[:50], pri))
    # ready/next first, then awaiting-*, then running/done/backlog;
    # within a gate, higher priority first (the loop picks the top ready row).
    order = {"now": 0, "next": 1, "ready": 1, "after": 3, "awaiting-task": 3,
             "needs_human": 4, "awaiting-you": 4, "scheduled": 5, "awaiting-time": 5,
             "backlog": 9, "": 6}
    prio = {"urgent": 0, "high": 1, "normal": 2, "low": 3, "": 2}
    rows.sort(key=lambda x: (x[0] == "completed", order.get(x[1], 6), prio.get(x[5], 2)))
    if a.filter:
        rows = [r for r in rows if r[1] == a.filter]
    for life, when, gate, room, goal, pri in rows:
        ptag = f"!{pri}" if pri else ""
        print(f"[{life:<9}] {ptag:<7} {gate or '(no schedule)':<28} {room}  {goal}")
    if not rows:
        print("(no task rooms registered)")


def cmd_needs_you(a) -> None:
    """Gate-badge roll-up (owner-approved 2026-06-12): the ONE view that
    surfaces everything waiting on the owner, so blockers are visible up front
    instead of discovered mid-execution. Lists rooms gated on a human
    (schedule.when == needs_human / awaiting-you) with the specific ask."""
    waiting = []
    for r in _registry_load():
        try:
            st = _get_states(r["room"]).get((f"{NS}.status", ""), {})
        except Exception:
            st = {}
        if st.get("status") == "completed":
            continue
        sch = st.get("schedule") or {}
        if sch.get("when") in ("needs_human", "awaiting-you"):
            waiting.append((r["room"], r.get("goal", ""), sch.get("needs", ""),
                            sch.get("priority", "")))
    n = len(waiting)
    print(f"⛔ Needs you ({n})" if n else "✅ Nothing waiting on you")
    pri = {"urgent": 0, "high": 1, "normal": 2, "low": 3, "": 2}
    for room, goal, needs, p in sorted(waiting, key=lambda x: pri.get(x[3], 2)):
        ptag = f"!{p} " if p else ""
        print(f"  • {ptag}{goal}\n      → {needs or '(no detail)'}\n      {room}")


def cmd_create(a) -> None:
    me = _whoami()
    # Auto-invite the owner so the task room is visible to them — otherwise a
    # freshly-created room is invisible and the owner-schedules/agent-executes
    # model breaks ("didn't see the room", owner 2026-06-12). Owner id from
    # $TASKROOM_OWNER or the channel .env; merged with any explicit --invite.
    owner = (os.environ.get("TASKROOM_OWNER") or _channel_env().get("TASKROOM_OWNER") or "").strip()
    invites = list(a.invite or [])
    if owner and owner not in invites:
        invites.append(owner)
    desc = (getattr(a, "description", "") or "").strip()
    room = _room_op("create", name=f"task: {a.goal[:60]}", topic=a.goal,
                    invite=invites)["room_id"]
    _set_state(room, f"{NS}.goal", "", {
        "goal": a.goal, "description": desc, "requested_by": me,
        "created_at": int(time.time()), "version": "0.2"})
    _set_state(room, f"{NS}.status", "", {
        "version": "0.2", "task_id": room, "status": "submitted",
        "assigned_to": me, "updated_at": int(time.time())})
    # Post the detailed description to the timeline so vanilla Element shows it.
    if desc:
        _room_op("message", room_id=room, body=f"[task] description:\n{desc}")
    _registry_add(room, a.goal)
    print(room)


def cmd_describe(a) -> None:
    """Set/replace the task's detailed description — the 'what & why' a one-line
    goal can't carry. Stored on the goal state event; surfaced by resume. Lets
    an existing room be backfilled after `create`."""
    goal = _get_states(a.room).get((f"{NS}.goal", ""), {})
    goal["description"] = a.text.strip()
    goal.setdefault("version", "0.2")
    _set_state(a.room, f"{NS}.goal", "", goal)
    _room_op("message", room_id=a.room, body=f"[task] description:\n{a.text.strip()}")
    print("description set")


def cmd_plan(a) -> None:
    needs = dict(kv.split("=", 1) for kv in (a.needs or []))
    prev = _get_states(a.room).get((f"{NS}.plan", ""), {})
    steps = []
    for s in a.steps:
        sid, title = s.split(":", 1)
        steps.append({"id": sid, "title": title, "status": "todo",
                      **({"needs": needs[sid]} if sid in needs else {})})
    _set_state(a.room, f"{NS}.plan", "", {
        "version": int(prev.get("version", 0)) + 1, "steps": steps})
    print(f"plan v{int(prev.get('version', 0)) + 1}: {len(steps)} steps")


def cmd_claim(a) -> None:
    cur = _get_states(a.room).get((f"{NS}.claim", a.step))
    if cur and cur.get("claimed_at", 0) + cur.get("lease_s", 0) > time.time() \
            and cur.get("agent") != a.agent:
        sys.exit(f"taskroom: step {a.step} held by {cur['agent']} "
                 f"(lease live for {int(cur['claimed_at'] + cur['lease_s'] - time.time())}s)")
    _set_state(a.room, f"{NS}.claim", a.step, {
        "agent": a.agent, "claimed_at": int(time.time()), "lease_s": a.lease})
    print(f"claimed {a.step} for {a.agent} (lease {a.lease}s)")


def cmd_step(a) -> None:
    plan = _get_states(a.room).get((f"{NS}.plan", ""))
    if not plan:
        sys.exit("taskroom: no plan in room")
    hit = False
    for s in plan.get("steps", []):
        if s["id"] == a.step:
            s["status"] = a.status
            hit = True
    if not hit:
        sys.exit(f"taskroom: step {a.step} not in plan")
    plan["version"] = int(plan.get("version", 0)) + 1
    _set_state(a.room, f"{NS}.plan", "", plan)
    print(f"step {a.step} → {a.status} (plan v{plan['version']})")


def cmd_status(a) -> None:
    cur = _get_states(a.room).get((f"{NS}.status", ""), {})
    cur.update({"status": a.status, "updated_at": int(time.time()), "version": "0.2",
                "task_id": cur.get("task_id", a.room)})
    if a.assigned_to:
        cur["assigned_to"] = a.assigned_to
    _set_state(a.room, f"{NS}.status", "", cur)
    # Observability: transitions also post as plain messages so vanilla
    # Element shows a readable history (spec v0.1 behavior).
    _room_op("message", room_id=a.room, body=f"[task] status → {a.status}")
    print(f"status → {a.status}")
    # Harvest-on-completion (owner design 2026-06-12): digest → vault notes/ +
    # portable share link. Best-effort; --no-harvest opts out.
    if a.status == "completed" and not getattr(a, "no_harvest", False):
        url = _harvest(a.room, summary=getattr(a, "summary", "") or "")
        if url:
            print(f"digest: {url}")


def cmd_schedule(a) -> None:
    """Set WHEN / under what condition the task runs (orthogonal to lifecycle
    status). Stored as a `schedule` object on the status state event. States
    (owner vocabulary 2026-06-12) — and what the autonomous loop does with each:
      now         → actively working
      next        → queued for the next idle session, NO blocker → agent picks up when owner away
      after       → queued until another room's task is done (--after-room); agent starts when that dep completes
      needs_human → queued, requires human in the loop (--needs) → agent does NOT auto-start; waits on owner
      scheduled   → at a time/condition (--at)
      backlog     → someday / no trigger yet
    """
    cur = _get_states(a.room).get((f"{NS}.status", ""), {})
    prev = cur.get("schedule") or {}
    sched = {"when": a.when, "updated_at": int(time.time())}
    if a.at:
        sched["at"] = a.at
    if a.after_room:
        sched["after_room"] = a.after_room
    if a.needs:
        sched["needs"] = a.needs
    if a.blocked_by:
        sched["blocked_by"] = a.blocked_by
    # Priority: agent suggests, owner adjusts. An owner-set priority is sticky —
    # a later agent suggestion must NOT clobber it. Carry forward unless this
    # call explicitly sets one (and never downgrade owner→agent authorship).
    prev_pri, prev_by = prev.get("priority"), prev.get("priority_by")
    if a.priority:
        if prev_by == "owner" and a.priority_by == "agent":
            sched["priority"], sched["priority_by"] = prev_pri, prev_by  # owner wins
        else:
            sched["priority"], sched["priority_by"] = a.priority, a.priority_by
    elif prev_pri:
        sched["priority"], sched["priority_by"] = prev_pri, prev_by
    cur["schedule"] = sched
    cur.setdefault("status", "submitted")
    cur.setdefault("task_id", a.room)
    cur["version"] = "0.2"
    _set_state(a.room, f"{NS}.status", "", cur)
    pri = sched.get("priority")
    extra = (f" @ {a.at}" if a.at else "") + (f" after {a.after_room}" if a.after_room else "") \
            + (f" (needs: {a.needs})" if a.needs else "") + (f" (blocked by {a.blocked_by})" if a.blocked_by else "") \
            + (f" [pri:{pri}·{sched.get('priority_by')}]" if pri else "")
    _room_op("message", room_id=a.room, body=f"[task] schedule → {a.when}{extra}")
    print(f"schedule → {a.when}{extra}")


def cmd_artifact(a) -> None:
    ref, mime = a.ref, None
    if a.file:
        import base64
        import mimetypes
        p = Path(a.file)
        raw = p.read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            sys.exit("taskroom: file exceeds the 8MB upload cap")
        # mimetypes misses common doc/code suffixes (e.g. .md → None), which
        # made the vault path fall back to a .bin extension. Fill the gaps.
        _EXTRA_MIME = {".md": "text/markdown", ".markdown": "text/markdown",
                       ".txt": "text/plain", ".json": "application/json",
                       ".py": "text/x-python", ".ts": "text/x-typescript",
                       ".js": "text/javascript", ".sh": "text/x-shellscript",
                       ".csv": "text/csv", ".yaml": "text/yaml", ".yml": "text/yaml"}
        mime = (mimetypes.guess_type(p.name)[0]
                or _EXTRA_MIME.get(p.suffix.lower())
                or "application/octet-stream")
        up = _room_op("upload", room_id=a.room, artifact_id=a.id, filename=p.name,
                      mime=mime, data_b64=base64.b64encode(raw).decode(), post=True)
        ref = up.get("mxc") or sys.exit("taskroom: upload returned no mxc")
        vault_ref = up.get("vault_ref")
        vault_rev = up.get("vault_rev")
    if not ref:
        sys.exit("taskroom: provide --ref or --file")
    content = {"title": a.title, "ref": ref, "step_id": a.step,
               "produced_by": _whoami(), "at": int(time.time())}
    if mime:
        content["mime"] = mime
    if a.file:
        if vault_ref:
            content["vault_ref"] = vault_ref
        if vault_rev:
            content["vault_rev"] = vault_rev
    _set_state(a.room, f"{NS}.artifact", a.id, content)
    print(f"artifact {a.id} recorded → {ref}")


def cmd_context(a) -> None:
    """Record a room asset: key parameter (kind=param) or intermediate
    conclusion (kind=conclusion). Admission rule (owner-ratified 2026-06-12):
    only what the next person/agent entering the room would do wrong without.
    Same key overwrites (supersede); history stays in room state history."""
    content = {"key": a.key, "value": a.value, "kind": a.kind,
               "by": _whoami(), "at": int(time.time())}
    for f in ("unit", "source", "confidence", "evidence"):
        v = getattr(a, f)
        if v:
            content[f] = v
    _set_state(a.room, f"{NS}.context", a.key, content)
    print(f"context[{a.kind}] {a.key} = {a.value[:60]}")


def cmd_decision(a) -> None:
    """Record a ratified decision — who, when, what, why (one line), link to
    the discussion. NOT for leanings or open questions (those live in the
    timeline / schedule.needs)."""
    content = {"what": a.what, "by": a.by, "at": int(time.time())}
    if a.why:
        content["why"] = a.why
    if a.link:
        content["link"] = a.link
    _set_state(a.room, f"{NS}.decision", a.id, content)
    print(f"decision {a.id}: {a.what[:60]}")


def cmd_resource(a) -> None:
    """Record a durable pointer: source path, host, dashboard, doc — or the
    LOCATION of a secret (vault:KEY_NAME). Never the secret itself."""
    content = {"title": a.title, "ref": a.ref, "kind": a.kind,
               "by": _whoami(), "at": int(time.time())}
    if a.note:
        content["note"] = a.note
    _set_state(a.room, f"{NS}.resource", a.id, content)
    print(f"resource {a.id} → {a.ref}")


def _assets(states: dict) -> dict:
    """Group the room's asset state events by class."""
    out = {"context": [], "decisions": [], "resources": []}
    for (et, key), v in states.items():
        if et == f"{NS}.context":
            out["context"].append({"key": key, **v})
        elif et == f"{NS}.decision":
            out["decisions"].append({"id": key, **v})
        elif et == f"{NS}.resource":
            out["resources"].append({"id": key, **v})
    for lst in out.values():
        lst.sort(key=lambda x: x.get("at", 0))
    return out


def cmd_say(a) -> None:
    # Accept literal "\n" (shells don't expand backslash-escapes inside
    # double quotes) as real newlines so messages aren't mangled in-room.
    body = a.text.replace("\\n", "\n")
    _room_op("message", room_id=a.room, body=body)
    print("sent")


def cmd_policy(a) -> None:
    # No-mention control = the room's `space.ag2.policy` state event (the broker's
    # canonical source of truth, read by policy_for). Settable by anyone with the
    # power level to write room state — no operator, no relay-agents.json edit.
    if a.respond == "default":
        _set_state(a.room, "space.ag2.policy", "", {})  # clear → back to @-mention gating
        print(f"{a.room}: mention policy reset to default (@-mention required in group rooms)")
    else:
        _set_state(a.room, "space.ag2.policy", "", {"respond": a.respond})
        msg = ("reply WITHOUT an @-mention" if a.respond == "always" else "stay silent")
        print(f"{a.room}: respond={a.respond} — the agent will {msg} here")


def cmd_share(a) -> None:
    res = _room_op("share", room_id=a.room, artifact_id=a.id, ttl_days=a.ttl_days)
    print(res.get("url") or "(no url returned)")


def _harvest(room: str, ttl_days: int = 365, depth: "str | None" = None,
             summary: str = "") -> "str | None":
    """Harvest-on-completion: write the room's digest to vault notes/, attach
    it to the room as the `digest` artifact, and mint a long-TTL portable
    share link (owner design 2026-06-12: portable = any browser, no Matrix
    login). Returns the share URL, or None if anything fails (best-effort).

    Depth (owner ask 2026-06-12) — explicit arg wins, else the room's
    `digest-depth` context asset, else "standard":
      minimal  → decisions + resources (pointer card)
      standard → + context, artifacts, final plan
      full     → + per-step needs and the last 30 timeline messages"""
    states = _get_states(room)
    goal = states.get((f"{NS}.goal", ""), {})
    status = states.get((f"{NS}.status", ""), {})
    plan = states.get((f"{NS}.plan", ""), {})
    assets = _assets(states)
    if depth not in ("minimal", "standard", "full"):
        room_pref = next((c.get("value") for c in assets["context"]
                          if c["key"] == "digest-depth"), "")
        depth = room_pref if room_pref in ("minimal", "standard", "full") else "standard"
    arts = [(k[1], v) for k, v in states.items() if k[0] == f"{NS}.artifact"]
    day = time.strftime("%Y-%m-%d")
    out = [f"# Task digest — {goal.get('goal', room)}",
           f"\nRoom: `{room}` · status: {status.get('status', '?')} · harvested {day}"
           f" · depth: {depth}\n"]
    # Purpose & outcome (owner ask 2026-06-12): a 2-3 sentence narrative —
    # explicit arg wins, else the room's `purpose` context asset.
    if not summary:
        summary = next((c.get("value", "") for c in assets["context"]
                        if c["key"] == "purpose"), "")
    if summary:
        out.append(f"## Purpose & outcome\n{summary}\n")
    if assets["decisions"]:
        out.append("## Decisions")
        out += [f"- **{d['id']}** ({d.get('by', '?')}): {d.get('what', '')}"
                + (f" — {d['why']}" if d.get("why") else "") for d in assets["decisions"]]
    if assets["context"] and depth != "minimal":
        out.append("\n## Context (params + conclusions)")
        out += [f"- `{c['key']}` = {c.get('value', '')}"
                + (f" [{c['kind']}]" if c.get("kind") else "")
                + (f" ({c['source']})" if c.get("source") else "") for c in assets["context"]]
    if assets["resources"]:
        out.append("\n## Resources")
        out += [f"- {r.get('title', r['id'])}: `{r.get('ref', '')}`"
                + (f" — {r['note']}" if r.get("note") else "") for r in assets["resources"]]
    if arts and depth != "minimal":
        out.append("\n## Artifacts")
        out += [f"- {aid}: {v.get('title', '')}" for aid, v in arts]
    steps = plan.get("steps", [])
    if steps and depth != "minimal":
        out.append("\n## Plan (final)")
        out += [f"- [{'x' if s.get('status') == 'done' else ' '}] {s.get('title', s.get('id'))}"
                + (f" (needs: {s['needs']})" if depth == "full" and s.get("needs") else "")
                for s in steps]
    if depth == "full":
        try:
            msgs = _room_op("context", room_id=room, limit=30).get("messages", [])
            if msgs:
                out.append("\n## Timeline (last 30)")
                out += [f"- **{m.get('sender', '?')}:** {m.get('body', '')[:400]}" for m in msgs]
        except SystemExit:
            pass
    text = "\n".join(out) + "\n"
    try:
        repo = Path(__file__).resolve().parent.parent.parent
        ws = subprocess.run(["bash", str(repo / "scripts" / "sutando-config.sh"), "workspace"],
                            capture_output=True, text=True, timeout=15).stdout.strip()
        slug = room.split(":", 1)[0].lstrip("!")[:12]
        path = Path(ws) / "notes" / f"taskroom-digest-{slug}-{day}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except Exception as e:  # noqa: BLE001
        print(f"harvest: vault write failed ({e})", file=sys.stderr)
        return None
    try:
        import base64
        up = _room_op("upload", room_id=room, artifact_id="digest",
                      filename=path.name, mime="text/markdown",
                      data_b64=base64.b64encode(text.encode()).decode(), post=False)
        _set_state(room, f"{NS}.artifact", "digest", {
            "title": "Task digest", "ref": up.get("mxc", ""), "mime": "text/markdown",
            "vault_ref": str(path), "produced_by": _whoami(), "at": int(time.time())})
        share = _room_op("share", room_id=room, artifact_id="digest", ttl_days=ttl_days)
        url = share.get("url")
        if url:
            _set_state(room, f"{NS}.resource", "digest-link", {
                "title": "Portable digest link", "ref": url, "kind": "doc",
                "by": _whoami(), "at": int(time.time())})
            _room_op("message", room_id=room,
                     body=f"[task] harvested ({depth}) → {path.name} · portable digest: {url}")
            # Fleet discovery: register in the relay's digest index so any
            # agent can GET /v1/digests before starting related work.
            try:
                req = urllib.request.Request(
                    f"{RELAY}/v1/digests",
                    data=json.dumps({"room": room, "url": url,
                                     "goal": goal.get("goal", ""),
                                     "summary": summary[:500],
                                     "completed_at": int(time.time())}).encode(),
                    headers={"Authorization": f"Bearer {_token()}",
                             "Content-Type": "application/json",
                             "User-Agent": "sutando-taskroom/1.0"},
                    method="POST")
                urllib.request.urlopen(req, timeout=15)
            except Exception as e:  # noqa: BLE001
                print(f"harvest: digest-index registration failed ({e})", file=sys.stderr)
        return url
    except SystemExit:
        # _room_op exits on HTTP errors — harvest must stay best-effort.
        print(f"harvest: digest saved to {path}, but upload/share failed", file=sys.stderr)
        return None


def cmd_harvest(a) -> None:
    url = _harvest(a.room, a.ttl_days, a.depth or None, a.summary)
    print(url or "(harvested to vault; no share url)")


def cmd_digests(a) -> None:
    """List the fleet's harvested digests (relay index) — consult before
    opening a room on related work."""
    req = urllib.request.Request(
        f"{RELAY}/v1/digests",
        headers={"Authorization": f"Bearer {_token()}",
                 "User-Agent": "sutando-taskroom/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        recs = json.loads(r.read() or "{}").get("digests", [])
    for d in recs:
        day = time.strftime("%Y-%m-%d", time.localtime(d.get("completed_at") or d.get("ts", 0)))
        print(f"{day}  {d.get('goal', '')[:70]}")
        if d.get("summary"):
            print(f"      {d['summary'][:160]}")
        print(f"      {d.get('room', '')}\n      {d.get('url', '')}")
    if not recs:
        print("(no digests registered)")


def cmd_resume(a) -> None:
    """The resume rule: goal → plan → claims → last N messages, as markdown."""
    states = _get_states(a.room)
    goal = states.get((f"{NS}.goal", ""), {})
    status = states.get((f"{NS}.status", ""), {})
    out = [f"# Task room {a.room}",
           f"**Goal:** {goal.get('goal', '(none)')}  \n**Status:** {status.get('status', '?')}"]
    if goal.get("description"):
        out.append(f"## Description\n{goal['description']}")
    plan = states.get((f"{NS}.plan", ""), {})
    if plan.get("steps"):
        out.append("\n## Plan")
        for s in plan["steps"]:
            claim = states.get((f"{NS}.claim", s["id"]))
            who = f" ← {claim['agent']}" if claim else ""
            out.append(f"- [{'x' if s['status'] == 'done' else ' '}] "
                       f"{s['id']}: {s['title']} ({s['status']}{who})")
    assets = _assets(states)
    if assets["decisions"]:
        out.append("\n## Decisions")
        out.extend(f"- **{d['id']}** ({d.get('by', '?')}): {d.get('what', '')}"
                   + (f" — {d['why']}" if d.get("why") else "") for d in assets["decisions"])
    if assets["context"]:
        out.append("\n## Context")
        out.extend(f"- `{c['key']}` = {c.get('value', '')}"
                   + (f" [{c['kind']}]" if c.get("kind") else "") for c in assets["context"])
    if assets["resources"]:
        out.append("\n## Resources")
        out.extend(f"- {r.get('title', r['id'])}: {r.get('ref', '')}" for r in assets["resources"])
    arts = [(k[1], v) for k, v in states.items() if k[0] == f"{NS}.artifact"]
    if arts:
        out.append("\n## Artifacts")
        out.extend(f"- {aid}: {v.get('title', '')} → {v.get('ref', '')}" for aid, v in arts)
    msgs = _room_op("context", room_id=a.room, limit=a.messages).get("messages", [])
    if msgs:
        out.append("\n## Recent messages")
        out.extend(f"**{m['sender']}:** {m['body']}" for m in msgs)
    print("\n\n".join(out))


def main() -> None:
    ap = argparse.ArgumentParser(description="task room state machine CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--goal", required=True)
    c.add_argument("--description", default="",
                   help="detailed task description (the what & why a one-line goal can't carry)")
    c.add_argument("--invite", nargs="*"); c.set_defaults(f=cmd_create)
    c = sub.add_parser("describe"); c.add_argument("--room", required=True)
    c.add_argument("--text", required=True); c.set_defaults(f=cmd_describe)
    c = sub.add_parser("plan"); c.add_argument("--room", required=True)
    c.add_argument("--steps", nargs="+", required=True)
    c.add_argument("--needs", nargs="*"); c.set_defaults(f=cmd_plan)
    c = sub.add_parser("claim"); c.add_argument("--room", required=True)
    c.add_argument("--step", required=True); c.add_argument("--agent", required=True)
    c.add_argument("--lease", type=int, default=3600); c.set_defaults(f=cmd_claim)
    c = sub.add_parser("step"); c.add_argument("--room", required=True)
    c.add_argument("--step", required=True)
    c.add_argument("--status", required=True, choices=["todo", "doing", "done", "blocked"])
    c.set_defaults(f=cmd_step)
    c = sub.add_parser("status"); c.add_argument("--room", required=True)
    c.add_argument("--status", required=True,
                   choices=["submitted", "working", "completed", "failed"])
    c.add_argument("--assigned-to")
    c.add_argument("--no-harvest", dest="no_harvest", action="store_true",
                   help="skip the automatic digest harvest on status=completed")
    c.add_argument("--summary", default="",
                   help="2-3 sentence purpose & outcome for the digest (else room's `purpose` context asset)")
    c.set_defaults(f=cmd_status)
    c = sub.add_parser("context"); c.add_argument("--room", required=True)
    c.add_argument("--key", required=True); c.add_argument("--value", required=True)
    c.add_argument("--kind", default="param", choices=["param", "conclusion"])
    c.add_argument("--unit", default=""); c.add_argument("--source", default="")
    c.add_argument("--confidence", default=""); c.add_argument("--evidence", default="")
    c.set_defaults(f=cmd_context)
    c = sub.add_parser("decision"); c.add_argument("--room", required=True)
    c.add_argument("--id", required=True); c.add_argument("--what", required=True)
    c.add_argument("--by", default="owner"); c.add_argument("--why", default="")
    c.add_argument("--link", default=""); c.set_defaults(f=cmd_decision)
    c = sub.add_parser("resource"); c.add_argument("--room", required=True)
    c.add_argument("--id", required=True); c.add_argument("--title", required=True)
    c.add_argument("--ref", required=True)
    c.add_argument("--kind", default="doc",
                   choices=["code", "host", "key", "dashboard", "doc", "other"])
    c.add_argument("--note", default=""); c.set_defaults(f=cmd_resource)
    c = sub.add_parser("harvest"); c.add_argument("--room", required=True)
    c.add_argument("--ttl-days", type=int, default=365)
    c.add_argument("--depth", default="", choices=["", "minimal", "standard", "full"],
                   help="digest richness; default = room's digest-depth context asset, else standard")
    c.add_argument("--summary", default="",
                   help="2-3 sentence purpose & outcome (else room's `purpose` context asset)")
    c.set_defaults(f=cmd_harvest)
    c = sub.add_parser("digests"); c.set_defaults(f=cmd_digests)
    c = sub.add_parser("schedule"); c.add_argument("--room", required=True)
    c.add_argument("--when", required=True,
                   choices=["now", "next", "after", "needs_human", "scheduled", "backlog"])
    c.add_argument("--at", default="", help="time/condition for when=scheduled")
    c.add_argument("--after-room", dest="after_room", default="", help="dep room id for when=after")
    c.add_argument("--needs", default="", help="what human input is needed, for when=needs_human")
    c.add_argument("--blocked-by", dest="blocked_by", default="", help="freeform blocker note")
    c.add_argument("--priority", default="", choices=["", "low", "normal", "high", "urgent"],
        help="agent suggests; owner adjusts. owner-set priority is sticky (agent won't clobber)")
    c.add_argument("--priority-by", dest="priority_by", default="agent", choices=["agent", "owner"],
        help="who set --priority (default agent; pass owner to lock it)")
    c.set_defaults(f=cmd_schedule)
    c = sub.add_parser("artifact"); c.add_argument("--room", required=True)
    c.add_argument("--id", required=True); c.add_argument("--title", required=True)
    c.add_argument("--ref", default=""); c.add_argument("--file", default="")
    c.add_argument("--step", default="")
    c.set_defaults(f=cmd_artifact)
    c = sub.add_parser("say"); c.add_argument("--room", required=True)
    c.add_argument("--text", required=True); c.set_defaults(f=cmd_say)
    c = sub.add_parser("policy", help="set the room's no-mention policy (space.ag2.policy)")
    c.add_argument("--room", required=True)
    c.add_argument("--respond", required=True, choices=["always", "never", "default"],
                   help="always = reply without @-mention; never = stay silent; default = require @-mention")
    c.set_defaults(f=cmd_policy)
    c = sub.add_parser("share"); c.add_argument("--room", required=True)
    c.add_argument("--id", required=True); c.add_argument("--ttl-days", type=int, default=7)
    c.set_defaults(f=cmd_share)
    c = sub.add_parser("queue"); c.add_argument("--filter", default="",
        help="only show rooms with this schedule.when (e.g. ready, next, needs_human)")
    c.set_defaults(f=cmd_queue)
    c = sub.add_parser("needs-you"); c.set_defaults(f=cmd_needs_you)
    c = sub.add_parser("resume"); c.add_argument("--room", required=True)
    c.add_argument("--messages", type=int, default=20); c.set_defaults(f=cmd_resume)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
