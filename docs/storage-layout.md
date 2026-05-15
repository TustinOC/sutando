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

When `$SUTANDO_WORKSPACE` is unset, these folders default to the repo root (legacy behavior; preserves single-machine installs out of the box). Set `$SUTANDO_WORKSPACE` in `.env` to move them outside the repo — recommended for any setup that wants a clean code/data boundary.

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

## Migration

To move an existing install's runtime data out of the repo:

1. Pick a workspace location (e.g. `~/Library/Application Support/sutando`).
2. Move `tasks/`, `results/`, `state/`, `logs/`, and optionally `notes/` there.
3. Add `SUTANDO_WORKSPACE=/path/to/workspace` to `.env`.
4. Restart Sutando.

Setting `$SUTANDO_WORKSPACE` is the only change required — the helpers handle the rest.
