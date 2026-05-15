# Storage layout — three layers

Sutando stores data in three distinct places, each with a different purpose and lifecycle. Keeping these boundaries crisp is what lets the repo itself stay shippable / open-sourceable.

> **Status:** this doc describes the target architecture. Migration is in progress (see `cleanup` branch). Some code still resolves runtime folders from the repo root; until every call-site is migrated, both behaviors coexist via fallbacks in the helpers below.

## The three layers

| Layer | Env var | Default location | Purpose | Synced across machines? |
|---|---|---|---|---|
| **Code** | — | the repo dir | shippable Sutando product | via public git |
| **Workspace** | `$SUTANDO_WORKSPACE` | repo root (legacy) / `~/Library/Application Support/sutando` (recommended) | per-machine mutable runtime state | no |
| **Private** | `$SUTANDO_PRIVATE_DIR` | unset by default | personal data that follows you across Macs | yes, via private git repo |

### Code (the repo)

Only code goes here. Open-source-ready. No personal data, no runtime state, no logs.

### Workspace

Per-machine mutable runtime state. Holds:

- `tasks/` — incoming task queue from voice / Telegram / Discord bridges
- `results/` — outgoing task results
- `state/` — ephemeral per-machine state (voice-mode flags, sentinels, etc.)
- `logs/` — runtime logs
- `notes/` — second brain (fallback location; private dir takes precedence — see below)

When `$SUTANDO_WORKSPACE` is unset, these folders currently default to the repo root (legacy behavior; preserves single-machine installs out of the box). Set `$SUTANDO_WORKSPACE` in `.env` to move them outside the repo — recommended for any setup that wants a clean code/data boundary.

The default will change to a platform-appropriate location (`~/Library/Application Support/sutando/` on macOS, `${XDG_DATA_HOME:-~/.local/share}/sutando/` on Linux) once the migration in the rollout plan below completes.

### Private

Personal data synced across all your Macs via a private GitHub repo (see `docs/memory-sync.md`). Holds:

- Per-machine identity files (under `machine-<host>/`) — `stand-identity.json`, avatars
- Cross-machine content — `notes/`, `build_log.md`, memory
- Anything else you want a durable, owner-controlled audit log for

`$SUTANDO_PRIVATE_DIR` is opt-in. When set, lookups for files like `notes/` check the private dir first and fall back to the workspace.

## Lookup helpers

| Helper | Use for | Python | TypeScript |
|---|---|---|---|
| Workspace path | Files that always go to the workspace (`tasks/`, `results/`, `state/`, `logs/`) | `workspace_path()` | `workspacePath()` |
| Per-machine private | Identity, avatars (per-host) | `personal_path()` | `personalPath()` |
| Shared private | `notes/`, `build_log.md` (cross-host) | `shared_personal_path()` | `sharedPersonalPath()` |

Shell scripts source `src/workspace.sh` to get `$WORKSPACE_DIR`, `$TASKS_DIR`, `$RESULTS_DIR`, `$STATE_DIR`, `$LOGS_DIR`, `$NOTES_DIR`.

All helpers fall back to the repo root when their respective env var is unset, so existing installs keep working without configuration.

## Why these specific boundaries

- **Code vs workspace**: lets the repo go public without scrubbing every commit for personal data.
- **Workspace vs private**: two different sync strategies. The task queue should NOT sync across machines (yesterday's voice tasks shouldn't replay on a different Mac); your notes SHOULD.
- **Per-machine vs shared private**: machine identity (avatar, stand identity) should differ per host; long-form notes shouldn't.

## Rollout plan

The refactor lands in stages so existing installs keep working at every commit:

1. **Step 1 — helpers (done).** Add `WORKSPACE_DIR` / `workspace_path()` / `workspacePath()` / `src/workspace.sh`. No call sites changed; default falls back to repo root.
2. **Step 2 — migrate callers.** Replace hardcoded `tasks/`, `results/`, `state/`, `logs/` paths across src/, skills/, and scripts/ with calls into the helpers. Default still = repo root, so behavior is unchanged.
3. **Step 3 — flip the default.** Change the fallback in all three helpers from "repo root" to platform-appropriate (`~/Library/Application Support/sutando/` on macOS). Add a one-shot migration in `init.sh` that detects existing data at the repo root and either moves it (if no workspace exists yet) or warns.
4. **Step 4 — drop legacy fallback.** Once every install is confirmed migrated, remove the repo-root fallback entirely. `$SUTANDO_WORKSPACE` becomes the single source of truth.

## User migration (set `$SUTANDO_WORKSPACE` ahead of step 3)

To move an existing install's runtime data out of the repo today, ahead of the default flip:

1. Pick a workspace location (e.g. `~/Library/Application Support/sutando`).
2. Move `tasks/`, `results/`, `state/`, `logs/`, and optionally `notes/` there.
3. Add `SUTANDO_WORKSPACE=/path/to/workspace` to `.env`.
4. Restart Sutando.

Setting `$SUTANDO_WORKSPACE` is the only change required — the helpers handle the rest.
