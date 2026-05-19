# Multi-core agent pool — design

**Status:** Design. Tracks #880. Implementation deferred; this doc is the contract that has to land before code does.

## Why

Today there's one core agent — a single `claude` process that reads tasks from `tasks/`, runs the proactive loop, and writes results. When tasks queue up (voice batching, multi-turn delegations, owner pasting several items in a row), wall-clock time scales linearly with queue depth. The bottleneck is concurrency, not per-task cost.

Owner directive 2026-05-18 23:03 PT: ship a multi-core pool so N parallel claude sessions can drain the queue. Default **N=3**.

## Default sizing

**N=3** by owner directive. Sweet spot:
- N=1 is today's behavior (no parallelism).
- N=2 helps but a single long-running task still pegs half the pool.
- N=3 covers the common "queue of 3-5 chat tasks land in 30s" workload while keeping quota burn bounded.
- N≥4 starts crossing the Claude per-account 5h quota into "drain it in <2h" territory — fine for a feature push, not the steady-state default.

Implementations should read N from `$SUTANDO_CORE_POOL_SIZE` env (default `3`) so the operator can scale up for a focused session or down to 1 to disable parallelism without removing the code.

## Architecture

```
                        ┌─────────────────────────────────┐
                        │      $SUTANDO_WORKSPACE         │
                        │                                 │
                  ┌─────┤  tasks/                         ├─────┐
                  │     │  results/                       │     │
                  │     │  state/cores/                   │     │
                  │     │    core-1/                      │     │
                  │     │    core-2/                      │     │
                  │     │    core-3/                      │     │
                  │     │  state/last-owner-activity.json │     │
                  │     └─────────────────────────────────┘     │
                  │                       ▲                     │
              read/write              read/write             read/write
                  │                       │                     │
       ┌──────────┴────────┐    ┌────────┴───────┐    ┌────────┴───────┐
       │ claude session 1  │    │ claude session 2 │   │ claude session 3 │
       │ SUTANDO_CORE_ID=1 │    │ SUTANDO_CORE_ID=2│   │ SUTANDO_CORE_ID=3│
       │ (launchd plist)   │    │ (launchd plist)  │   │ (launchd plist)  │
       └───────────────────┘    └──────────────────┘   └──────────────────┘
```

All three sessions:
- Share the same workspace.
- Read from the same `tasks/` dir.
- Write to the same `results/` dir.
- Have their own per-session subspace at `state/cores/core-<N>/` for scratch + done-flags + alive heartbeats.

The coordination layer between them is **filesystem primitives only** — no message passing, no socket between sessions. Each session is independent.

## Coordination primitives

### 1. Claim-via-atomic-rename

A new task lands as `tasks/task-<id>.txt`. The session claims it by atomic rename:

```
tasks/task-<id>.txt
  → tasks/task-<id>.claimed-core-<n>.txt   (POSIX-atomic rename)
```

POSIX `rename()` on the same filesystem is atomic — no two sessions both succeed. The losing session's `rename()` returns ENOENT and the session moves on.

**Why this works on single-Mac**: same filesystem, kernel-level inode swap. No race window. (This is the strict subset of the #872 cross-Mac design — without the sync gap, last-rename-wins collapses to first-rename-wins.)

**Tie-breaking**: not needed. The kernel decides.

**Implementation note**: the task watcher (`src/watch-tasks-stream.sh`) currently emits `TASK_FILE: <name>` per new file. With N sessions, each gets the same event. The claim attempt at read-time is the dedup. No watcher change needed.

### 2. Per-session done-flag

Before any side-effect tool fires (`gh pr comment`, `gh pr create`, Gmail send, Discord post, Slack post, Telegram send, etc.), the session writes:

```
state/cores/core-<n>/done/task-<id>.flag
```

Side-effect helpers are wrapped: read the flag first; if exists, refuse to re-fire. The flag's existence is the durable "this is handled, stop touching it" signal that survives session crash + restart.

**Why before, not after**: a session crash after side-effect-fired-but-flag-not-written would let the next session re-fire. Writing the flag *first* means at worst a side effect doesn't actually fire (recoverable: nothing happened) — never that it fires twice. Idempotency floor: at-most-once delivery.

**Cross-session view**: each session has its own `done/` dir, but the side-effect helper checks **all** session dirs (`state/cores/*/done/task-<id>.flag`). One flag anywhere = task is handled, all sessions noop.

### 3. Claim lease + crash recovery

A session that claims a task and then crashes leaves `task-<id>.claimed-core-<n>.txt` stranded forever. Recovery:

- Each session writes `state/cores/core-<n>.alive` every 30s (existing `src/core_heartbeat.py`, just per-session-scoped).
- On startup, each session scans `tasks/task-*.claimed-core-<M>.txt` files where `core-<M>.alive` is missing or older than 90s. For each, atomic-rename back to `tasks/task-<id>.txt` and the next claim cycle picks it up.

**Why 90s**: heartbeat is 30s; 90s = 3 missed beats = process is gone, not just blocked on a slow tool call.

**Why startup-only, not continuous**: a session shouldn't constantly check sibling sessions — that's polling overhead and a race surface in itself. The watchdog at boot is enough: when any session restarts (launchd KeepAlive), it scans for orphans. Owner-induced restart of any session sweeps the whole pool's orphans.

**Edge case**: long-running task (10+ minutes) keeps heartbeat fresh; lease never expires. No double-process risk.

## Worker pool — launchd shape

Three plists at `~/Library/LaunchAgents/com.sutando.core-<n>.plist` (n = 1, 2, 3):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sutando.core-1</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/claude</string>
    <string>--print</string>
    <string>/proactive-loop</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SUTANDO_CORE_ID</key><string>1</string>
    <key>SUTANDO_CORE_POOL_SIZE</key><string>3</string>
    <key>SUTANDO_WORKSPACE</key><string>/Users/<owner>/.sutando/workspace</string>
  </dict>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key>
  <string>/Users/<owner>/.sutando/workspace/logs/core-1.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/<owner>/.sutando/workspace/logs/core-1.err</string>
</dict>
</plist>
```

The three plists are otherwise identical except for the `core-<n>` label, ID, and log paths. Single template + a `bash scripts/install-core-pool.sh N` generator script that creates the N plists.

### Lifecycle

- `scripts/install-core-pool.sh 3` writes the 3 plists + `launchctl bootstrap`s each.
- `scripts/uninstall-core-pool.sh` stops + removes them.
- Sutando.app (the menu-bar UI) doesn't change — it still owns the desktop UI; the cores are agent-loop processes only.

## Side-effect helper changes

Every tool that produces an externally-visible side effect needs the done-flag gate. Concrete inventory:

| Tool | Where lived | Side effect | Done-flag check needed |
|---|---|---|---|
| `gh pr comment` / `gh pr review` / `gh pr create` | inline Bash | comment / PR on GitHub | Yes |
| `gh issue comment` / `gh issue create` | inline Bash | comment / issue on GitHub | Yes |
| Gmail draft → send | `mcp__claude_ai_Gmail__create_draft` (today: draft-only; if send is added) | email | Yes if/when send lands |
| Discord post via `[channel:]` redirect | discord-bridge.py result watcher | post in channel | Yes |
| Slack post via redirect | slack-bridge.py result watcher | post in channel | Yes |
| Telegram send | telegram-bridge.py result watcher | DM | Yes |
| iMessage send | iMessage skill | DM | Yes |
| Memory writes (file) | Write tool to memory dir | local file change | **No** — local-only, multiple writes is fine (last-writer-wins) |
| Result file write (file) | Write tool to results/ | local file change | **No** — bridge reads result whoever wrote it |

The gate is implemented at the **tool wrapper** layer, not the underlying API. That way new side-effect tools added later inherit the gate by living in the wrapper-aware helper module.

## Quota trade

Claude per-account quota is a 5h sliding window, shared across all sessions on the account. N=3 sessions don't get 3× the budget — they share the same budget.

| Scenario | Single core | N=3 pool |
|---|---|---|
| 3 chat tasks land in 30s, each 30s of work | 90s wall-clock | 30s wall-clock (3× speedup) |
| 1 owner ask, 5min of research | 5min wall-clock | 5min wall-clock (no benefit; bottleneck is single-task latency, not concurrency) |
| Steady-state idle (cron loop fires every 5min) | 1 pass / 5min | 3 passes / 5min, 3× quota burn for the same idle work |

**Mitigation**: the proactive-loop already has skip conditions for active-engagement / quota-light / external-wait. When N=3 sessions all wake up at the same cron fire, they each check those conditions independently. With proper gating, idle-tax should be ~30-50% above N=1, not 3×.

A future refinement: claim leader-election (one session does the idle-loop health-check work, the others fast-path skip when they have no claimed task). Not in v1.

## State transitions

```
tasks/task-<id>.txt                            (new)
  ↓ claim (atomic rename, one session wins)
tasks/task-<id>.claimed-core-<n>.txt           (claimed by core-n)
  ↓ side-effect attempted
state/cores/core-<n>/done/task-<id>.flag       (claim's session writes the flag BEFORE the side effect)
  ↓ side effect fires (if first time)
results/task-<id>.txt                          (any session writes; bridge delivers)
  ↓ bridge picks up + delivers
results/archive/...                            (bridge moves)
tasks/archive/...                              (post-result cleanup, claim file goes here too)
```

A session that crashes between "claim file written" and "done flag written":
1. Heartbeat goes stale within 90s.
2. Next session restart scans, finds `tasks/task-<id>.claimed-core-<n>.txt` with stale heartbeat.
3. Atomic-renames back to `tasks/task-<id>.txt`.
4. Next claim cycle picks up; processor runs again. If side effect was *attempted* but not flagged, it re-attempts (rare double-fire window; acceptable).
5. If side effect was flagged, processor reads flag and noops.

## Failure modes

| Mode | What happens | Mitigation |
|---|---|---|
| Two sessions race to claim same task | One wins via POSIX-atomic rename. Other gets ENOENT and moves on. | Built into the kernel; no code needed. |
| Session crashes mid-task | Claim file stranded; heartbeat stops. | Watchdog scan on next restart releases the claim. |
| Side effect fires, done-flag write fails (disk full, etc.) | Next session sees no flag, re-fires side effect — duplicate. | Write the flag BEFORE the side effect; acceptable "side effect didn't fire" beats "side effect fired twice." |
| All N sessions wake up simultaneously on cron, all run the same health-check | 3× idle-pass cost. | Skip-when-no-claimed-task heuristic; deferred to v2. |
| User restarts Sutando.app, doesn't restart core pool | App and cores diverge. | `bash src/startup.sh` should `launchctl kickstart` all `com.sutando.core-*` plists, not just core-1. |
| Disk fills up | All sessions fail to write claim/flag/result. | Out of scope — same failure mode as N=1. |
| Quota exhausted during a pass | Per-pass skip conditions handle gracefully (already in `/proactive-loop`). | Existing behavior. Document that N=3 makes quota run out ~3× faster under load. |
| Task with `priority: urgent` gets stuck behind 2 `priority: normal` tasks the other 2 sessions claimed | Three claimed tasks block the 4th `urgent` until one finishes. | Per-pass priority calculator already orders the queue. With N=3 the worst case is 3 normal tasks each ≥urgent's wait. Acceptable for v1; revisit if it becomes a pain point. |

## Open questions

1. **Should the watchdog be startup-only or continuous?** Startup is cheaper but means a session that runs 24h has no chance to claim sibling orphans. Counter: launchd `KeepAlive` restarts crashed sessions immediately, so "session running 24h" while a sibling is crashed is exotic. Lean startup-only for v1.

2. **Should the done-flag dir be per-session or shared?** Per-session (`state/cores/core-<n>/done/`) means each session has its own flag dir; the side-effect-gate checks all of them. Shared (`state/done/`) is simpler but means rsync-style fleet sync (#872) gets harder to reason about. Lean per-session for v1.

3. **How to handle owner-DM responses where the receiver only sees the LAST result?** Today voice agent over-delegates and the dedup marker handles thread consolidation. With N=3, three sessions could each produce a partial result for related tasks. Falls under existing thread-dedup logic (CLAUDE.md "Result-body protocol markers"). Verify in implementation.

4. **Per-session log placement.** v1 plan: `logs/core-<n>.log`. If logs get noisy, rotate per-session.

## Implementation plan (Phase 2, separate PR)

1. **Refactor side-effect helpers** to use a `with_done_flag()` wrapper that reads/writes the flag.
2. **Add `src/claim_task.py` / `.ts`** — atomic-rename claim primitive, used by the task-bridge consumer.
3. **Add `scripts/install-core-pool.sh N`** — generates N plists from a template + `launchctl bootstrap` them.
4. **Add `scripts/uninstall-core-pool.sh`** — `launchctl bootout` + plist removal.
5. **Update `src/startup.sh`** to kickstart all `com.sutando.core-*` plists, not just core-1.
6. **Add `state/cores/core-<n>/` to per-session scaffolding** — heartbeat already writes there; just generalize the path.
7. **Add boot-time orphan scan** — find stale claim files (heartbeat older than 90s), atomic-rename back to `tasks/`.
8. **Tests** — claim race (two sessions, atomic-rename, one wins), done-flag gate (mock side effect, gate refuses second call), crash recovery (kill session mid-task, restart, verify claim released).
9. **Doc the `SUTANDO_CORE_POOL_SIZE` env in CLAUDE.md.**

## Out of scope (don't bring back in v1)

- Leader-election for idle-pass deduplication. Worth-it only if quota burn under N=3 idle becomes a real pain point.
- Per-task affinity ("voice tasks always go to core-1"). Adds protocol surface; the round-robin-via-race is fine for v1.
- Cross-Mac coordination. That's #872. v1 is single-Mac POSIX-atomic.
- Adaptive pool sizing. Static N is enough until we have real load data.
- Slot-based scheduling (e.g. only let one session run /morning-briefing at a time). Idempotent helpers handle this automatically once the done-flag is in.

## Relationship to other docs

- [`docs/workspace-design.md`](workspace-design.md) — 3-space model. This pool lives entirely inside the State space (per-session subspaces under `state/cores/core-<n>/`).
- [`docs/state-sync-allowlist.md`](state-sync-allowlist.md) — #872 design for cross-Mac state sync. This doc is the single-Mac strict subset — same primitives, no sync gap.
- [`CLAUDE.md`](../CLAUDE.md) "Core liveness signal" section — per-host alive file already exists; v1 just generalizes the path to per-session.

Tracks #880.
