# Workspace Contract v0.8

**Status**: design proposal, 2026-06-01 simplified pass. Supersedes the earlier v0.8 draft (which still carried v0.7 host/room/tier machinery). The host/room sync layer is **out of scope** here — that's the vault-design doc, not the workspace contract.

> **Implementation status (2026-06-03):** v0.8 is the **target contract**, not yet shipped end-to-end. Today's M0 resolver (`src/workspace_default.{py,ts}`) still honors `$SUTANDO_WORKSPACE` as a **deprecation-warned legacy escape hatch** — exactly the override this spec retires. Wherever this doc says "retired" / "no env override" / "no env check," read it as the post-Path-B target. The Path B code change that makes v0.8 true is tracked in a follow-up PR off `staging-workspace-revamp` (links here once opened). Until then, the resolver retains the legacy escape with a one-time stderr warning.

**One-sentence summary**: The workspace is `<repo>/workspace/`, period. It is gitignored, computed (no env override), and ephemeral; durability is a separate concern handled by the vault.

---

## High-level decision

**Use `$SUTANDO_REPO_DIR/workspace/` to save all per-user state and data.** The decision to put the workspace at an in-repo address is intentional: it preserves **Claude Code's cwd-anchored privileges** (CLAUDE.md auto-load, skills auto-discovery, project-slug for session transcripts, hook scope, no `--add-dir` needed). Any other location forfeits these and reintroduces overlay machinery v0.7 already tried and backed out.

**Consequence**: the user-specified `$SUTANDO_WORKSPACE` env override is **retired**. There is no path to override the workspace location. (Edge cases — shared workspace across multiple checkouts — are addressed via filesystem-level symlink, not env config.)

### Mitigations for the downside of putting data inside the repo

| Concern | Mitigation |
|---|---|
| **Data durability** — workspace dies with the repo (delete the repo, lose the state) | **`$SUTANDO_VAULT`** is the separate durability layer. Sync mechanism around it: cron (today's 30-min cadence via `sync-memory.sh`), SessionStart / SessionEnd hooks, or manual invocation. Memory syncs by default; user can extend the allowlist. See §4. |
| **Data volume** — accumulated state could distract Claude Code's cwd-anchored discovery (slow project-slug indexing, large `git status`, etc.) | **Nightly archive cron** (`scripts/archive-workspace.sh`) rotates stale `tasks/processed/` + `logs/` (>30 days old) into `archive/<yyyy-mm>/`. See §2.6. Other content (notes/, data/) is user-managed. |

These two mitigations are what make the in-repo decision safe to make as a default.

---

## 1. Goal

Define exactly two things:
1. **Where** workspace state lives on disk.
2. **What** structure it has.

Everything about *durability*, *cross-host sync*, *room collaboration*, and *per-tier git remotes* lives in the vault-design doc.

## 2. The contract

### 2.1 Location

```
$SUTANDO_REPO_DIR/workspace/    ← workspace root (gitignored, no env override)
```

**Hard rules:**
- The workspace is **always** at `<repo>/workspace/`. There is no `$SUTANDO_WORKSPACE` env override (retired in v0.8).
- The whole `/workspace/` tree is gitignored (single entry in repo `.gitignore`).
- Workspace is computed by the helpers — never by a per-script fallback. See §3.

**Why in-repo:** the workspace inherits Claude Code's cwd privileges (CLAUDE.md auto-load, skills auto-discovery, project-slug, hook scope, no `--add-dir` needed). Putting it anywhere else forfeits these.

### 2.2 Layout

```
<repo>/workspace/
├── tasks/                  ← inbound message queue (bridges write, agent reads)
│   ├── archive/<yyyy-mm>/  ← processed task files (auto-rotated)
│   └── processed/          ← in-flight working state
├── results/                ← outbound reply queue (agent writes, bridges deliver)
├── state/                  ← cross-process status JSON (one writer per file)
│   ├── core-status.json
│   ├── voice-state.json
│   ├── contextual-chips.json
│   ├── quota-state.json
│   └── cores/<host>.alive  ← per-host liveness signal
├── logs/                   ← append-only chrono streams (bridges, watchers, sync)
├── notes/                  ← long-form human-readable content
├── data/                   ← durable input data (datasets, fetched feeds)
├── build_log.md            ← single-file done/in-flight/next snapshot (append-only)
└── pending-questions.md    ← unanswered questions awaiting owner input
```

The workspace root holds **only** top-level dirs + the two markdown files. Loose `.json` files belong under `state/`. The repo root holds code/skills/config (a separate concern).

### 2.3 Decision guide

When the agent writes a new file under `<repo>/workspace/`, walk this list top-to-bottom and stop at the first match:

1. **Inbound channel message?** → `tasks/task-{id}.txt`. The bridges write these.
2. **Reply to a task?** → `results/task-{id}.txt`. Bridge polls + delivers.
3. **Cross-process status JSON another component polls?** → `state/`.
4. **Append-only chrono event stream?** → `logs/<component>.log`.
5. **Long-form human-readable content?** → `notes/<slug>.md`.
6. **Done/in-flight/next snapshot?** → append to `build_log.md`.
7. **Blocked question for the owner?** → append to `pending-questions.md`.
8. **Durable input data?** → `data/<topic>/`.

If two layers seem to fit, prefer the more specific one (state JSON beats logs beats notes).

### 2.4 Confidentiality

The workspace is user-specific. **NEVER disclose workspace content** — tasks, results, notes, state, build_log, pending-questions — to any party outside the owner without explicit per-disclosure approval. Default-deny: when in doubt, ask first. Strategic / competitive / financial / personal content stays owner-DM only.

This applies even when a public PR / issue would benefit from quoting workspace content. Bots **paraphrase** workspace state into public artifacts; they never quote verbatim.

### 2.5 Expansion (user-side)

The user can add their own top-level subdirs (e.g. `drafts/`, `research/`, `screenshots/`, `inbox/`). New dirs are automatically gitignored (the whole `/workspace/` tree is) and inherit the §2.4 default-deny posture.

**Agent-facing rules for custom subdirs** — what content goes there, when the agent reads/writes them, retention — belong in `PERSONAL_CLAUDE.md` (per-user overrides). CLAUDE.md (shared) describes the built-in shape; PERSONAL_CLAUDE.md describes the per-user extension.

### 2.6 Archive / cleanup

Workspace bloat distracts Claude Code's cwd-anchored discovery (large `git status`, slow project-slug indexing, big tab-completion sets). Mitigation: a nightly cron archives older content.

- `tasks/processed/` and `logs/` older than 30 days → `archive/<yyyy-mm>/`.
- Suggested cron: 03:30 local. Script: `scripts/archive-workspace.sh`.
- `notes/` and `data/` are user content — never auto-archived. User manages.
- `build_log.md` is append-only — never archived. Owner manually rotates if it gets unwieldy (>500KB).

## 3. Resolution (implementation)

Path computation is centralized; do NOT reinvent the fallback per-script.

- Python: `from workspace_default import resolve_workspace` → `Path`
- TypeScript: `import { resolveWorkspace } from './workspace_default.js'` → `string`
- Swift: `AppDelegate.workspace` (split alongside `repoRoot` for code-adjacent paths)

In v0.8 the helpers do exactly `Path(repo_dir) / "workspace"` — no env check, no `~/.sutando/workspace/` fallback. Legacy code paths reading `$SUTANDO_WORKSPACE` are migrated to the helpers.

## 4. Vault (overview only)

The workspace is intentionally ephemeral: delete the repo and the workspace dies. The **vault** is the separate durability layer that persists content across reclones and syncs across hosts.

- `$SUTANDO_VAULT` — the durability env var. Defaults exist; user can override.
- Default content: memories. The user can extend the sync allowlist.
- Sync mechanism: scripts/cron-driven (today `scripts/sync-memory.sh`; future `sync-vault.sh`).
- Cross-host topology, room collaboration, allowlist format, secret push-gate, conflict policy — **all out of scope here**. See `docs/vault-design.md` (forthcoming).

## 5. Migration from earlier versions

For users running with `$SUTANDO_WORKSPACE` pointing somewhere outside the repo (any pre-v0.8 version):

1. Sutando detects the legacy env var at startup and logs a one-time deprecation warning naming the old path.
2. `scripts/sutando-migrate-to-v0.8.sh` (forthcoming) moves content `~/.sutando/workspace/<contents>` → `<repo>/workspace/<contents>` with safety checks (no overwrite; leaves `MIGRATED.md` breadcrumb at the legacy path).
3. After migration the user unsets `$SUTANDO_WORKSPACE` in their `.env`.

Edge case: users who pointed multiple repo checkouts at one shared workspace lose that pattern. Workaround: pick one canonical checkout, OR manually symlink `<repoB>/workspace → <repoA>/workspace` (no tooling).

## 6. Locked decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Workspace = `<repo>/workspace/`, **computed**, no env override | Claude Code cwd-privilege capture; structural simplicity |
| 2 | Whole `/workspace/` tree gitignored | Prevents tracked-pollution anti-pattern (the historic `Path(__file__).parent.parent` regression) |
| 3 | 8 built-in top-level dirs (tasks/results/state/logs/notes/data + 2 .md files) | Matches the decision guide; agent has a single answer for each write |
| 4 | Confidentiality default-deny | Workspace = owner-private; bots paraphrase, never quote |
| 5 | Custom user dirs allowed, documented in PERSONAL_CLAUDE.md | Per-user expansion without polluting shared CLAUDE.md |
| 6 | Nightly archive of stale `tasks/processed/` + `logs/` | Bounds cwd bloat that would otherwise erode cwd-discovery performance |
| 7 | Vault is a separate doc / separate concern | Workspace contract = location + structure only; durability is its own design |

## 7. What's NOT in this doc

These belong in the vault-design doc, not here:
- Multi-host sync topology
- Room collaboration (multi-user shared content)
- Tier model (LOCAL / MACHINE / AGENT / ROOM)
- Per-tier git remote layout
- Allowlist format + push-gate (secret scanning)
- Conflict resolution policy
- Vault creation / invite flow
- `identity_<scope>.json` and the scopes registry

This is intentional. v0.8 (this doc) defines the workspace. The vault doc defines durability + sharing. Keeping them separate lets each evolve at its own pace.
