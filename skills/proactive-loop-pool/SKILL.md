---
name: proactive-loop-pool
description: "Pool-aware variant of /proactive-loop for the multi-core agent pool (#880). Each session in the pool runs this skill; the only behavioral diff vs /proactive-loop is a claim step before processing each task."
user-invocable: true
---

# Proactive Loop (Pool-Aware)

Variant of `/proactive-loop` that's safe to run in N parallel claude sessions sharing one workspace. The **only behavioral difference** from `/proactive-loop` is step 1 — task pickup goes through the atomic-rename claim before reading the task file. Losing the claim race means another session is processing the task; this session walks away. The rest of the loop body is unchanged.

This skill exists for the multi-core pool installed by `bash scripts/install-core-pool.sh N`. Each launchd-managed core session in the pool invokes `/proactive-loop-pool` instead of `/proactive-loop`.

**Single-core users**: keep using `/proactive-loop`. This skill is only useful when N > 1 sessions exist; in single-core mode it adds claim overhead with no benefit.

**Usage**: `/proactive-loop-pool [interval]`

ARGUMENTS: $ARGUMENTS

## Required env vars

Each pool session sets these via its launchd plist (see `scripts/install-core-pool.sh`):
- `SUTANDO_CORE_ID` — this session's 1-based core ID (e.g. `1`, `2`, `3`).
- `SUTANDO_CORE_POOL_SIZE` — total pool size (informational; not enforced by this skill).
- `SUTANDO_WORKSPACE` — shared workspace path (same as all other Sutando components).

If `SUTANDO_CORE_ID` is unset, abort with a clear error: "proactive-loop-pool requires SUTANDO_CORE_ID — are you sure you meant to invoke this instead of /proactive-loop?"

## Activation, scheduling, watcher

Identical to `/proactive-loop` — `/schedule-crons` and the streaming task watcher start the same way. See `~/.claude/skills/proactive-loop/SKILL.md` for the full body, or follow these steps verbatim:

1. `/schedule-crons` to set up recurring crons.
2. Start the streaming task watcher via the `Monitor` tool.

## The claim step (what's different from /proactive-loop)

When the task watcher emits `TASK_FILE: <basename>` for a new task, **before** reading the file:

1. Extract the task ID from the filename (`task-<id>.txt` → `<id>`).
2. Run: `python3 src/claim_task.py <id> $SUTANDO_CORE_ID`
3. Outcomes:
   - **Exit 0** → claim won. The script prints the renamed path (`tasks/task-<id>.claimed-core-<n>.txt`). Read THIS path, not the original.
   - **Exit 1** → claim lost (another pool session won). Skip this task entirely — no Read, no processing, no result file.
   - **Exit 2** → usage / validation error. Log and skip.

Use the renamed `task-<id>.claimed-core-<n>.txt` path for all subsequent reads + result writes. The bridges look for results by task ID, so writing to `results/task-<id>.txt` (without the `claimed-core-<n>` suffix) still routes correctly.

**Initial sweep on session start**: the watcher's initial sweep emits TASK_FILE events for any pre-existing files. Run the claim step on each; expect to win some and lose others depending on which sibling session got there first.

## The rest of the loop

Identical to `/proactive-loop`'s numbered steps 2-11:

2. Check pending questions.
3. Check system health.
4. Read the build log.
5. Pick highest-ROI work.
6. Act on it.
7. Update build_log.
8. If blocked, ask.
9. Ensure the streaming watcher is running.
10. Monitor Discord.
11. Heartbeat.

These steps run independently per session. Quota / active-engagement / presenter-mode skip conditions all apply per-session — each pool member checks them on its own pass.

## Phase 2a known limitations

This skill ships in Phase 2a of #880. Two pieces are NOT yet wired in:

- **Done-flag side-effect gate** (Phase 2b). Without it, the rare crash-then-replay window can fire a side effect twice. Mitigation today: rare crashes within the few-second window between claim and side-effect-completion.
- **Boot-time orphan watchdog** (Phase 2b). If a pool session crashes after claiming but before processing, the claim file is stranded until owner manually renames it back. Mitigation today: `launchctl bootout <core> && launchctl bootstrap <core>` re-runs the session which won't re-claim a stale file (but won't release it either — manual rename needed).

For "let me try it tonight" the limitations above are acceptable. Phase 2b ships the watchdog + done-flag gate.

## Disabling the pool

To revert to single-core:
1. Remove `SUTANDO_CORE_POOL_SIZE` from `.env` (or set to 1).
2. Run `bash scripts/uninstall-core-pool.sh` to remove the launchd plists.
3. `bash src/restart.sh` to restart the foreground core.
