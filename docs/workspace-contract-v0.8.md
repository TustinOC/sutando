# Workspace Contract v0.8

**Status**: design proposal, 2026-05-31 23:30Z. Self-contained spec; supersedes v0.7 by flipping the workspace location default and making the durability layer (vault) explicit. Open clarifications inside marked **[OPEN]** — author defaulted; owner can override.

**The flip in one sentence**: v0.7 placed `$SUTANDO_WORKSPACE` outside the repo (`~/.sutando/workspace/` default) and added an opt-in cwd-overlay subsystem (§2.5.7/§2.5.8) to bring workspace content into the agent's cwd for native Claude Code discovery. **v0.8 reverses this**: workspace lives inside the repo by default (`<repo>/workspace/`, gitignored), so it is naturally cwd-anchored and gets all native Claude Code privileges without any mount/overlay machinery. The durability layer becomes an **explicit user-created vault** — a private git remote — synced via `sync-engine.sh`. If you have no vault, workspace is local-only. If you have one, cross-host sync works.

**One-sentence summary**: v0.8 organizes a Sutando user's per-user workspace along two orthogonal axes — **tier** (durability + sync) and **scope** (collaboration boundary) — with the workspace living inside the repo as a gitignored subdirectory (so Claude Code's native cwd-anchored discovery activates over workspace content), and an **explicit user-created vault** providing the cross-host / cross-machine durability layer; v0.7's opt-in cwd-overlay subsystem is retired (workspace is already in cwd).

---

## Part 1. Problem and framing

Sutando's per-user workspace stores three classes of state that have to be managed independently: **runtime data** (live tasks, ephemeral state — never leaves this disk), **per-machine state** (host-specific configuration that syncs between the user's own devices), and **agent-level knowledge** (notes, memory, skills shared across all the user's devices).

That works for a single user managing their own stuff. What was missing: **the user collaborates**. They join rooms, take notes with teammates, accumulate knowledge that should benefit their personal agent without polluting their private memory.

Two further constraints surfaced in the v0.6 → v0.7 → v0.8 conversation:

1. **The workspace needs Claude Code's native cwd-anchored discovery** (CLAUDE.md auto-discovery, `.claude/settings.json` layering, project skills/commands/agents, `.mcp.json`, plugin discovery) to activate over workspace content without a complex overlay layer. v0.7 tried to bridge this with `<repo>/.workspace-mount/` symlinks — but at the cost of `rm -rf` safety, slug-canonicalization risk, and a substantial sync engine on top.
2. **The durability layer should be explicit, not implicit**. v0.7 made the "personal vault" git remote a half-implicit thing the sync engine managed. v0.8 surfaces it: the user creates a vault when they want cross-machine persistence; otherwise workspace is local-only.

v0.8's goal: organize the contract so workspace lives inside the repo (gitignored, naturally cwd-anchored) and the user opts into a vault for durability/sync. Multi-room collaboration via per-room vaults. All without the v0.7 overlay machinery.

### 1.1 The clean mental model

> **Workspace is the local working copy. Vault is the durable upstream. Cwd already contains workspace, so Claude Code's native discovery just works.**

Three concentric layers:

| Layer | Path on this machine | Role |
|---|---|---|
| **Code** | `$SUTANDO_REPO_DIR` (the git checkout) | Source code, skills, scripts. Public/shareable. |
| **Workspace** | `<repo>/workspace/` (gitignored subdir) | Local working copy of agent state. Ephemeral — recoverable from vault. |
| **Vault** (optional) | per-user, BYO git remote | Durable cross-machine source of truth. User creates explicitly. |

Inside the workspace, the v0.6/v0.7 tier+scope model is preserved:

| Concept | Dimension | What lives under it |
|---|---|---|
| `<repo>/workspace/hosts/<H>/` | **Physical / material** | Per-machine slices for devices you own. Identity = hostname. |
| `<repo>/workspace/rooms/<X>/` | **Virtual / social** | Per-collaboration slices for spaces you join. Identity = `room_id`. |
| (workspace root) | **You** | Personal agent — single user identity participating in both physical AND virtual contexts. |

The path location encodes the tier:

| Path you walked into | Tier |
|---|---|
| `<repo>/workspace/hosts/<H>/local/` | LOCAL |
| `<repo>/workspace/hosts/<H>/` (but not under `local/`) | MACHINE |
| `<repo>/workspace/rooms/<X>/` | ROOM |
| (none of the above; workspace root) | AGENT |

---

## Part 2. The contract

### §2.0 Agent-digestible summary

Put this in `$SUTANDO_REPO_DIR/CLAUDE.md` under `## Workspace contract (v0.8)`:

```
$SUTANDO_REPO_DIR/                     ← the git checkout (code)
├── CLAUDE.md, src/, skills/, ...      ← source tree (git-tracked)
├── .gitignore                         ← includes /workspace/
└── workspace/                         ← workspace root (gitignored)
    ├── identity_agent.json
    ├── CLAUDE.md                      ← agent orient (auto-generated)
    ├── memory/, notes/, skills/, context/, assets/, logs/
    │                                  ← AGENT (personal; cross-host within YOU)
    ├── public/                        ← publishable content carve-out
    │
    ├── hosts/<hostname>/              ← per-host slices (physical context)
    │   ├── identity_machine.json
    │   ├── memory/, notes/, ...       ← MACHINE
    │   ├── tasks/, results/           ← MACHINE (cross-host-visible task log)
    │   └── local/                     ← LOCAL (never syncs)
    │       └── state/, tasks/, results/, logs/, secrets/, ...
    │
    └── rooms/<room-id>/               ← per-room slices (virtual context)
        ├── identity_room.json
        ├── memory/, notes/, ...       ← ROOM
        ├── tasks/<member-id>/, results/<member-id>/  ← per-member partition
        └── members/<member-id>/       ← per-member free-form slice
```

**Vault** (optional, user-created):
- A private git remote (e.g., `git@github.com:<user>/sutando-vault-personal.git`).
- `sync-engine.sh create-vault --remote <url>` (or similar — see §2.5 [OPEN]) initializes a vault and registers it in `identity_agent.json`.
- Workspace's durable subtree (everything except `hosts/<H>/local/`) syncs to the vault.
- If no vault is configured: Sutando works fully on one machine, no cross-host sync, no remote backup.

**Where do I write?**

| Concern | Path | Tier |
|---|---|---|
| Live task → core agent | `workspace/hosts/<H>/local/tasks/task-{ts}.txt` | LOCAL |
| Result ← core agent | `workspace/hosts/<H>/local/results/task-{ts}.txt` | LOCAL |
| Runtime state | `workspace/hosts/<H>/local/state/` | LOCAL |
| Secrets | `workspace/hosts/<H>/local/secrets/` (never tracked) | LOCAL |
| Host-specific note | `workspace/hosts/<H>/notes/<name>.md` | MACHINE |
| Cross-host fact (personal) | `workspace/memory/<slug>.md` | AGENT |
| Cross-host note (personal) | `workspace/notes/<name>.md` | AGENT |
| Room-shared fact | `workspace/rooms/<X>/memory/<slug>.md` | ROOM |
| My contribution to room X | `workspace/rooms/<X>/members/<my-id>/...` | ROOM (write-partitioned) |

**Hard rules**:
1. **Read-union, write-scoped** for AGENT and ROOM tiers (see §2.5.3).
2. **LOCAL never syncs**. `.gitignore` excludes `workspace/hosts/<H>/local/**`.
3. **Each vault is its own git repo**. Personal vault for `<workspace root, excluding rooms/>`; one room vault per `rooms/<X>/`.
4. **Secrets never tracked**. Push-gate scans every push (§2.5.5).
5. **Private zone never appears in public outputs**. Workspace + DMs + private channels + email + internal designs are private. PRs / issues / public Discord channels / tweets are public. Bots paraphrase, never quote, private-zone content. See §2.2.1.
6. **Tier inferred from path location, never from a directory-name suffix**. Files keep `identity_<scope>.json` suffix for grep-ability; directories don't.

### §2.1 Layout (full)

```
$SUTANDO_REPO_DIR/                         ← cwd = git checkout
│
├── .gitignore                             ← lists /workspace/
├── src/, skills/, scripts/, docs/, ...    ← source tree
├── CLAUDE.md                              ← repo's agent orient
│
└── workspace/                             ← (gitignored) workspace root
    │
    ├── CLAUDE.md                          ← auto-generated workspace agent orient
    ├── identity_agent.json                ← user identity + vault registry
    │   { user_id, display_name,
    │     personal_vault: { remote_url, last_sync_ts } | null,
    │     rooms: [ { room_id, vault_url, member_id, last_sync_ts }, … ] }
    │
    ├── memory/                            ← AGENT
    │   ├── MEMORY.md
    │   └── *.md (per-fact files)
    ├── notes/, skills/, context/, assets/, logs/
    ├── public/                            ← AGENT publishability carve-out
    │
    ├── rooms/                             ← per-room slices (virtual context)
    │   ├── room-A/                        ← scope = room-A
    │   │   ├── identity_room.json
    │   │   ├── memory/, notes/, skills/, context/, assets/, logs/
    │   │   ├── public/                    ← ROOM publishability carve-out
    │   │   ├── tasks/<member-id>/, results/<member-id>/  ← per-member partition
    │   │   └── members/<my-member-id>/    ← per-member free-form
    │   └── room-B/                        ← (same shape)
    │
    ├── hosts/                             ← per-physical-machine slices
    │   ├── mac-mini/
    │   ├── macbook-pro/
    │   └── air-laptop/
    │       ├── identity_machine.json
    │       ├── memory/, notes/, skills/, context/, logs/, tasks/, results/  ← MACHINE
    │       └── local/                     ← LOCAL (this disk only, NEVER syncs)
    │           ├── state/, tasks/, results/, logs/, secrets/, notes/, context/, memory/
    │           └── .gitkeep
    │
    └── machine -> hosts/<this-hostname>/   ← stable local alias
```

### §2.2 Path resolution

Two distinct directories, two distinct concerns:

- **`SUTANDO_REPO_DIR`** — the Sutando source repo checkout. Holds code, skills source, project rules, CLAUDE.md. Pushed to the public `sonichi/sutando` GitHub repo (or wherever the user clones from).
- **`SUTANDO_WORKSPACE`** — per-user mutable state. Lives at `$SUTANDO_REPO_DIR/workspace/` by default. Synced (when a vault is configured) to private vault git remotes.

**Resolution** (every service reads the same):
1. `$SUTANDO_WORKSPACE` env var (override; `~` is expanded).
2. `$SUTANDO_REPO_DIR/workspace/` (default — derived from repo location).
3. **Fallback** if `SUTANDO_REPO_DIR` is also unset: `<script's repo>/workspace/` resolved via the helper's standard probe (per the helpers' existing logic).

**Helper functions** (do not reinvent):
- Python: `from workspace_default import resolve_workspace` → `Path`.
- TypeScript: `import { resolveWorkspace } from './workspace_default.js'` → `string`.
- Swift: `AppDelegate.workspace` property.

These helpers are **updated for v0.8**: previously defaulted to `~/.sutando/workspace/`; now default to `$SUTANDO_REPO_DIR/workspace/`. The env var override is preserved.

**Why this change is safe**:
- `workspace/` is gitignored at repo root (`.gitignore` entry shipped with the repo).
- `git clean -dfx` will wipe it — but a configured vault is the recovery path. If no vault: user has explicitly accepted local-only mode.
- `git status` stays clean because everything inside is gitignored.

**Sutando-plus submodule compatibility** [OPEN]: if user uses sutando-plus (private overlay with sonichi/sutando as submodule), the workspace ends up at `<sutando-plus>/sutando/workspace/`. Owner directive needed: keep that path or surface to `<sutando-plus>/workspace/` (one level up).

#### §2.2.1 Private zone vs public zone

(Unchanged from v0.7 §2.2.1 — same table, same bot rule, same tier-privacy mapping. Reproduced below for self-contained reference.)

| Surface | Private | Public |
|---|---|---|
| `$SUTANDO_WORKSPACE/**` | ✅ | — |
| Discord DMs (owner DM, team DM) | ✅ | — |
| Discord private channels (owner ↔ team) | ✅ | — |
| Email content (received + drafts) | ✅ | — |
| Internal designs / pre-publication notes | ✅ | — |
| GitHub PRs / issues / commits / comments | — | ✅ |
| Public Discord channels (open server channels) | — | ✅ |
| Tweets / blog posts / website copy | — | ✅ |

**Bot rule**: when drafting content for a public surface, you may *use* private-zone content as reasoning context but must NOT paste verbatim. The single exception is the `public/` subdir convention — content inside any `public/` folder is publish-ready and may be quoted with attribution.

**Privacy default by tier**:

| Tier | Privacy default | Publishability override | Bot-behavior note |
|---|---|---|---|
| **LOCAL** | ALWAYS private | None | Two-layer: sync-private + author-private. Includes secrets, raw task bodies, runtime state. |
| **MACHINE** | Private | None recommended | Per-host slice; only YOU see it across YOUR devices. |
| **AGENT** | Private | `$WORKSPACE/public/` is the publishable carve-out | Bot paraphrases AGENT content for public output; may quote `public/` with `[from $WORKSPACE/public/]`. |
| **ROOM** | Private (members only) | `rooms/<X>/public/` is the room's publishable carve-out | Bot paraphrases ROOM; may quote `rooms/<X>/public/` with `[from room <name>/public/]`. |

LOCAL is the strictest. Quotability rule of thumb: a bot may include verbatim content if and only if the source path contains `/public/` AND lives under AGENT or ROOM tier.

**Room membership privacy**: `identity_room.json` carries `privacy: "private" | "public"` describing who can join (orthogonal to content publishability).

### §2.3 Notation — scope and tier

(Unchanged from v0.7 §2.3.)

Two orthogonal axes:

**Scope** — the COLLABORATION boundary:
- `personal` — implicit at workspace root.
- `<room-id>` — at `workspace/rooms/<room-id>/`, one per subscribed room.

**Tier** — inferred from path location:
- **LOCAL** — `workspace/hosts/<H>/local/**` — never syncs.
- **MACHINE** — `workspace/hosts/<H>/<concept>/` — per-host slice; this host's slice syncs cross-host within YOU (via personal vault).
- **AGENT** — root-level `workspace/<concept>/` (personal) OR `workspace/rooms/<X>/<concept>/` minus room-only subdirs.
- **ROOM** — same shape under `workspace/rooms/<X>/`.

**Identity files keep their scope suffix** for grep:
- `identity_agent.json` at workspace root
- `identity_machine.json` under `hosts/<H>/`
- `identity_room.json` under `rooms/<X>/`
- `identity_member.json` under `rooms/<X>/members/<id>/`

### §2.4 Promotion via example skills

(Unchanged from v0.7 §2.4.)

`workspace_promotion_skill_machine` (LOCAL → MACHINE → AGENT) and `workspace_promotion_skill_agent` (personal ↔ room) are reserved example skill slots.

### §2.5 Sync engine — vault-explicit

Single converged engine `sync-engine.sh`. Modes: `create-vault` / `sync` / `pull` / `push` / `migrate` / `status`. **Workspace can run without a vault** (local-only mode). Vault is opt-in via explicit `create-vault` (or registration of an existing remote).

#### §2.5.1 Per-scope vault target

Each scope is a SEPARATE vault (git repo), opt-in per scope:

| Path | Vault remote | Driver |
|---|---|---|
| `<workspace>/` excluding `rooms/` — `identity_agent.json`, `memory/`, `notes/`, …, `hosts/<H>/**` | **personal vault** (`identity_agent.json#personal_vault.remote_url`, NULL if not configured) | `sync-engine.sh sync` |
| `<workspace>/rooms/<room-id>/**` | **room vault** (`identity_agent.json#rooms[].vault_url`, per-room) | `sync-engine.sh sync` (loops subscribed rooms) |

`rooms/` itself is a meta-dir — not committed to the personal vault. Each `rooms/<X>/` is its own git worktree (when its vault is configured).

**[OPEN]** — vault creation flow:
- `sync-engine.sh create-vault --kind personal --remote <git-url>` ?
- `sync-engine.sh create-vault --kind room --room-id <id> --remote <git-url>` ?
- Bring-your-own remote (GitHub/GitLab/self-hosted), with later optional hosted-vault layer at the commercial level.

**Cross-host coordination** (when vault is configured): each host pushes its MACHINE slice + the shared AGENT content to the personal vault. `sync-engine.sh pull` on another host fetches changes; conflicts demote via §2.5.4.

#### §2.5.2 LOCAL exclusion (gitignore at workspace root)

Generated `.gitignore` under `<repo>/workspace/.gitignore` (NOT the repo's top-level `.gitignore`, which already gitignores `/workspace/`). The workspace's internal `.gitignore` controls what the **vault** sees — not what the repo's git tracks.

Allows only the durable allowlist; everything else is excluded by default. `hosts/<H>/local/**` excluded in every scope.

Two carve-outs make the layout discoverable in the vault:
- `!hosts/<H>/local/**/` — re-include directories (so git walks into them)
- `!hosts/<H>/local/**/README.md` and `!hosts/<H>/local/**/.gitkeep` — keep per-folder layout markers tracked.

#### §2.5.3 Read-union semantics

(Unchanged from v0.7 §2.5.3.)

**Sync is per-scope, READ is union across scopes.** Applies to AGENT and ROOM tiers.

```python
def load_concept(concept):  # concept ∈ {memory, notes, skills, context, assets, logs}
    facts = []
    # AGENT (personal)
    facts += walk(f"$WORKSPACE/{concept}/", scope="personal", tier="AGENT")
    # ROOM (each subscribed room)
    for room in list_rooms("$WORKSPACE/rooms/"):
        facts += walk(f"$WORKSPACE/rooms/{room}/{concept}/", scope=room, tier="ROOM")
    # MACHINE (this host only)
    facts += walk(f"$WORKSPACE/hosts/{my_hostname}/{concept}/",
                  scope=f"host:{my_hostname}", tier="MACHINE")
    return merge_with_provenance(facts)
```

Provenance is automatic. Conflict surface both with scope tags. Asymmetric flow: Room → Personal is automatic via read-union; Personal → Room is explicit publish/lift.

#### §2.5.4 Memory subsystem — symlink + demotion-on-conflict

(Unchanged semantics from v0.7 §2.5.4; paths are now `<repo>/workspace/...`.)

Memory has a special arrangement because Claude Code's own layer at `~/.claude/projects/<slug>/memory/` is the live writer.

**Slug selection**: the symlink target slug is derived from `$SUTANDO_REPO_DIR` (Sutando's convention is "always launch claude from the repo cwd"; that slug is the one M-1 targets).

**Data flow**: identical to v0.7.

**Without a vault configured**: memory still works locally — `hosts/<H>/local/memory/` is the symlink to Claude Code's slug-anchored memory dir, and `<repo>/workspace/memory/` is the AGENT-tier mirror. The mirror is only relevant when a personal vault is configured and pushes happen.

**Generalize M-4**: same symlink+demotion mechanism applies to ALL AGENT/ROOM-tier concepts. Phased rollout — memory first.

#### §2.5.5 Secret-scan push-gate

(Unchanged from v0.7 §2.5.5.) Every push, sync engine runs a secret-scan over staged paths. Filename and content patterns; a match ABORTS the push.

#### §2.5.6 Auto-generated layout documentation

(Unchanged from v0.7 §2.5.6.) `ensure_layout` drops a workspace-root `CLAUDE.md` + per-folder `README.md` headers (`Tier`, `Sync`, `Mandatory`).

The workspace-root `CLAUDE.md` is **discoverable via Claude Code's native ancestor-walk** in v0.8 (because cwd = repo, workspace is a subdir, and any session that `cd`s into workspace would pick it up — but the default agent launch from repo cwd does NOT auto-pick it up). [OPEN] — should v0.8 wire `--append-system-prompt-file <workspace>/CLAUDE.md` into the agent launch by default? Author lean: yes; matches v0.7 §2.5.7 plumbing minus the mount.

#### §2.5.7 (retired in v0.8)

v0.7 §2.5.7 specified an opt-in cwd-overlay subsystem (`$SUTANDO_REPO_DIR/.workspace-mount/`) to bring workspace content into the agent's cwd via symlinks. **In v0.8 this is unnecessary** — workspace is *already* in the cwd subtree (`<repo>/workspace/`). Claude Code's native cwd-anchored discovery activates over workspace content directly.

What v0.7 §2.5.7 protected against and how v0.8 handles it:

| v0.7 §2.5.7 risk | v0.8 status |
|---|---|
| R1 `rm -rf .workspace-mount/` destroys workspace data | Largely sidestepped — `rm -rf <repo>/workspace/` deletes the working copy, but the vault preserves durable content. User can `sync-engine.sh pull` to restore. |
| R2 slug canonicalization for symlinked skills | Eliminated — `<repo>/workspace/skills/*` are real files in the cwd subtree, no symlink, deterministic slug. |
| R3 CLAUDE.md ancestor discovery via symlinks | Not applicable — no symlinks. |
| R4 multi-host MACHINE tier scoping | Handled by §2.5.3 read-union as before. |
| R5 tools that don't follow symlinks | Not applicable. |
| R6 CI pollution | Workspace is gitignored; CI checks out the repo without workspace content (no `<repo>/workspace/` after `git clone` until the agent runs and creates it). |

#### §2.5.8 (retired in v0.8)

v0.7 §2.5.8 specified a skills-overlay (cp variant with `ws-<name>` / `room-<X>-<name>` prefixes) to surface workspace-supplied skills to Claude Code via the `<cwd>/.claude/skills/` discovery path. **In v0.8 this is unnecessary** because workspace skills already live under `<cwd>/workspace/skills/...`.

Open: does Claude Code auto-discover skills nested at `<cwd>/workspace/skills/...`? [OPEN] — empirical test needed. If Claude Code's skills discovery requires the canonical `<cwd>/.claude/skills/` path:
- **Option A** — auto-symlink `<cwd>/.claude/skills/ws-<name>` → `<cwd>/workspace/skills/<name>/` at startup. This brings back a thin overlay for SKILLS ONLY (no general mount needed). Slug is deterministic (real cwd; symlink target is also in cwd subtree). Cheap.
- **Option B** — write Sutando's app layer to read `<cwd>/workspace/skills/*` and inject as `--add-dir` or `--append-system-prompt-file` style. App-layer loader; non-Claude-Code-native.
- **Option C** — rely on Claude Code's `--add-dir` to expose `<cwd>/workspace/` and trust that skills inside that subdir get picked up. May or may not work depending on Claude Code's discovery rules.

Default recommendation: Option A as a small `ensure_skills_overlay()` step in `ensure_layout`, dropped from `<repo>/.gitignore` along with `<repo>/workspace/`.

### §2.6 Migration phasing

v0.7 → v0.8 and v0.5/v0.6 → v0.8 paths:

| Phase | Action | Cost |
|---|---|---|
| **Phase 0** — write the contract | This doc lands on `feat/workspace-contract-v0.8` (renaming from v0.7 branch). | 0 |
| **Phase 1** — relocate workspace default | Update `workspace_default.{py,ts,swift}` helpers: default = `$SUTANDO_REPO_DIR/workspace/`. Add `<repo>/.gitignore` entry `/workspace/`. Backwards-compat: `$SUTANDO_WORKSPACE` env var override still wins. | low |
| **Phase 2** — naming sweep (v0.6-C still applies) | `sutando-migrate.sh --to v0.6-C` from v0.7 plan still applies. Pure `mv`. | low |
| **Phase 3** — per-host migrate to v0.8 layout | `sutando-migrate.sh --to v0.8` moves `~/.sutando/workspace/<content>` → `$SUTANDO_REPO_DIR/workspace/<content>`. Deletes empty `~/.sutando/workspace/`. Per-host sign-off gate. | low per host |
| **Phase 4** — vault opt-in (per scope) | User runs `sync-engine.sh create-vault --kind personal --remote <url>` to opt in for the personal scope. Same for each room scope. Until then, workspace is local-only. | per-user |
| **Phase 5** — retire dual-resolve | Remove the `~/.sutando/workspace/` fallback from resolver helpers. v0.7's `.workspace-mount/` mount code (if any landed) deletes. | low |

Per sonichi's flag ("migration is dangerous"): Phase 3 has a manual sign-off gate per host. No automatic rollout.

### §2.7 Keeping `CLAUDE.md` summaries in sync

(Largely unchanged from v0.7 §2.7.)

Three CLAUDE.md files now exist, with different roles:

1. **Repo `CLAUDE.md`** at `$SUTANDO_REPO_DIR/CLAUDE.md` — project rules + §2.0 of this doc verbatim under `## Workspace contract (v0.8)`. Hand-synced.
2. **Workspace-root `CLAUDE.md`** at `$SUTANDO_REPO_DIR/workspace/CLAUDE.md` — agent orient. Auto-generated by sync engine. Cross-refs the repo CLAUDE.md. [OPEN] Delivered via `--append-system-prompt-file` (proposed §2.5.6) or relied on the ancestor-walk (not auto for default agent launch from repo cwd).
3. **Per-scope `CLAUDE.md`** (`<workspace>/rooms/<X>/CLAUDE.md`) — scope-local orient. Generated by sync engine from per-scope template.

### §2.8 Locked decisions

Carried from v0.7 §2.8 with revisions:

| # | Lock | Status |
|---|---|---|
| 1 | **Layout shape** — workspace root = personal scope; `rooms/` peer of `hosts/`; 0 path moves for personal data | Unchanged (still applies) |
| 2 | **`hosts/` placement** — at workspace root, peer of `rooms/` | Unchanged |
| 3 | **`identity_room.json` per room** | Unchanged |
| 4 | **Membership lifecycle** | Unchanged |
| 5 (v0.6-C) | **Suffix convention** — directories drop scope suffix; files keep `identity_<scope>.json` | Unchanged |
| 6 | **R-1 Read-union** for AGENT/ROOM cross-scope | Unchanged |
| 7 | **(B) per-member partition** | Unchanged |
| 8–11 | **M-1..M-4 memory symlink + demotion** | Unchanged |
| 12 | **Named tiers** LOCAL/MACHINE/AGENT/ROOM | Unchanged |
| 13 | **ROOM tier activated** | Unchanged |
| 14 | **Private zone wider than disk paths** | Unchanged |
| 15 | **Tier-privacy mapping** | Unchanged |
| 16 | **`public/` folder convention** | Unchanged |
| **17 v0.8 NEW** | **`$SUTANDO_WORKSPACE` default = `$SUTANDO_REPO_DIR/workspace/`**. Workspace inside repo, gitignored. Env var override preserved. | NEW |
| **18 v0.8 NEW** | **Vault is explicit user-created**. Per-scope vaults (one personal + N room). `sync-engine.sh create-vault` is the canonical entry point. Workspace runs without a vault (local-only). | NEW |
| **19 v0.8 NEW** | **Skills overlay = `<cwd>/.claude/skills/ws-<name>/` symlinks → `<cwd>/workspace/skills/<name>/`** [pending §2.5.8 Option A empirical confirmation]. Other workspace content discoverable via cwd-anchored ancestor walk. v0.7 `.workspace-mount/` mount/cp split retired. | NEW (replaces v0.7 #17/#18/#19) |
| v0.7 #17 (opt-in overlay) | Retired — workspace is in cwd by default. | RETIRED |
| v0.7 #18 (mount/cp split) | Retired — workspace skills are real files, no mount mechanism needed. | RETIRED |
| v0.7 #19 (`.workspace-mount/` naming) | Retired — no mount. | RETIRED |

### §2.9 Still open (implementation-phase concerns)

1. **§2.5.8 empirical test**: does Claude Code auto-register skills nested at `<cwd>/workspace/skills/`, or do we need the `<cwd>/.claude/skills/ws-<name>` symlink (Option A)? [BLOCKING for §2.5.8 lock.]
2. **Sutando-plus submodule path**: confirm workspace at `<sutando-plus>/sutando/workspace/` is acceptable, or surface to `<sutando-plus>/workspace/`. [§2.2]
3. **Vault creation UX**: command shape, hosted vs BYO remote, per-scope vs combined, hosted-layer commercial model. [§2.5.1]
4. **CLAUDE.md delivery to default agent launch**: `--append-system-prompt-file` plumbing in `scripts/start-cli.sh` to load `<workspace>/CLAUDE.md`. [§2.5.6, §2.7]
5. **Cross-scope task routing**: when a ROOM task lands on my LOCAL queue. [Carried from v0.7]
6. **`.mcp.json` merge semantics + secret-handling**: per-room MCP server tokens must not leak. [Carried from v0.7]
7. **Sync-back aggressiveness on SessionEnd**: stage only vs auto-commit-and-push. Recommend explicit `sync-engine.sh push`. [Carried from v0.7]

---

## Part 3. The whole design picture

### 3.1 What v0.8 solves

User collaborates AND wants Claude Code's native cwd-anchored mechanisms (skills, commands, MCP) to activate over their workspace content. v0.8 codifies this with the **2-axis model (scopes × tiers, path-encoded)** AND **workspace placed inside the repo by default**, so native discovery activates without an overlay subsystem. The durability layer is **explicit**: vault is a user-created git remote, opt-in per scope.

### 3.2 Architecture in one picture

```
   ┌──────────────────────────────────────────────────────────┐
   │  $SUTANDO_REPO_DIR  (cwd, the git checkout)             │
   │                                                           │
   │   src/, skills/, scripts/, CLAUDE.md, ...                │
   │                                                           │
   │   workspace/                       (gitignored)         │
   │   │                                                       │
   │   │ identity_agent.json                                  │
   │   │ memory/, notes/, skills/, ...     ← AGENT            │
   │   │                                                       │
   │   ├──────────────────────┐    ┌──────────────────────┐   │
   │   │ rooms/               │    │ hosts/               │   │
   │   │  virtual context     │    │  physical context    │   │
   │   │                      │    │                      │   │
   │   │  <room-A>/           │    │  <hostname>/         │   │
   │   │   identity_room.json │    │   identity_machine.. │   │
   │   │   memory/, notes/,...│    │   memory/, notes/,…  │   │
   │   │   members/<id>/      │    │   tasks/, results/   │   │
   │   │   (AGENT or ROOM)    │    │   local/  ← LOCAL    │   │
   │   └──────────────────────┘    └──────────────────────┘   │
   └──────────────────────────────────────────────────────────┘
                            │
                            │  if vault is configured:
                            ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Personal vault (user-created BYO git remote)            │
   │   workspace/ (excl. hosts/<H>/local/) pushed here         │
   └──────────────────────────────────────────────────────────┘
                            │
                            │  one vault per room (similar)
                            ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Room-A vault (user-created BYO git remote per room)     │
   │   workspace/rooms/room-A/ pushed here                     │
   └──────────────────────────────────────────────────────────┘
   
   Claude Code's native cwd-anchored discovery (CLAUDE.md, skills,
   MCP, plugins) activates over <repo>/workspace/* by default.
   No overlay machinery needed — workspace already lives in cwd subtree.
```

### 3.3 Three orthogonal axes (a meta-invariant)

(Unchanged from v0.7 §3.3.)

| Axis | Values | Manifestation |
|---|---|---|
| **Public/Private** | Code repo (public) vs Workspace + vaults (private) | §2.2 |
| **Scope** | personal vs `<room-id>` | path prefix |
| **Tier** | LOCAL / MACHINE / AGENT / ROOM | parent-path location |

### 3.4 Four invariants

(Unchanged from v0.7 §3.4.)

1. **Write is scoped, read is unioned** (AGENT and ROOM tiers only).
2. **Conflicts demote, never silently merge**.
3. **LOCAL is sacred ground**. No vault, no sync. (Bridges + watchers + daemons write here; mount has nothing to do with it.)
4. **Workspace root identity is portable**. `identity_agent.json` names YOU.

### 3.5 Voice-note end-to-end (worked example)

A user dictates "team-A uses TypeScript strict mode" while at mac-mini, in the middle of a coding session for team-A's project. Team-A's room vault is configured.

1. Voice agent transcribes → produces a memory write candidate.
2. Promotion skill classifies: scope = `room-A`, tier = ROOM. Path: `<repo>/workspace/rooms/room-A/memory/team_a_conventions.md`.
3. File lands in this host's `<repo>/workspace/rooms/room-A/` directly.
4. `sync-engine.sh push` on session end → room-A vault gets the new commit.
5. Alice on her laptop runs `sync-engine.sh pull` → receives the file.
6. Alice's agent loads memory: walks personal + `rooms/room-A/memory/` → convention visible with `scope=room-A` provenance.
7. Alice starts a personal task ("review my own code"): agent context already knows team-A's TS-strict convention.

### 3.5.1 New install — zero-config workflow

1. User clones the Sutando repo (`git clone https://github.com/sonichi/sutando.git`).
2. User runs `bash src/startup.sh`. Workspace appears at `<repo>/workspace/` (auto-created by `ensure_layout`). LOCAL/MACHINE/AGENT tiers populated as needed.
3. Agent runs. Skills under `<repo>/workspace/skills/` (when added) auto-register via Claude Code's native cwd-anchored discovery (assuming §2.5.8 Option A symlinks).
4. (Optional) User runs `sync-engine.sh create-vault --kind personal --remote git@github.com:<user>/sutando-vault-personal.git` to add a personal vault. From then on, `SessionEnd` hook pushes workspace deltas to the vault.

**Without a vault**: Sutando works on this one machine. `git clean -dfx` wipes workspace. User accepts that.

**With a vault**: cross-machine sync works. Disaster recovery works. New-machine onboarding = clone repo + register existing vault + pull.

### 3.6 Non-goals

(Unchanged from v0.7 §3.6 minus the overlay items.)

- Cross-room federation.
- Real-time collaboration.
- Public room discoverability.
- Workspace OUTSIDE cwd as default (v0.7's mode, retired).
- Implicit vault setup. User must explicitly create.

### 3.7 Migration (recap)

Phase 0 → 1 (relocate default) → 2 (v0.6-C naming sweep) → 3 (per-host migrate to `<repo>/workspace/`) → 4 (vault opt-in per scope) → 5 (retire dual-resolve).

### 3.8 What ships with v0.8

- `sync-engine.sh` — converged sync engine with `create-vault` + sync/pull/push/migrate/status.
- `workspace_default.{py,ts,swift}` — updated default = `$SUTANDO_REPO_DIR/workspace/`.
- `sutando-migrate.sh` — `--to v0.6-C` (naming sweep) + `--to v0.8` (relocate workspace).
- `<repo>/.gitignore` — `/workspace/` entry shipped in the repo.
- `<repo>/workspace/.gitignore` — auto-generated allowlist for vault sync (excludes LOCAL).
- (Optional) `ensure_skills_overlay()` from §2.5.8 Option A — drops `<cwd>/.claude/skills/ws-<name>/` symlinks for native skill registration.
- Repo `CLAUDE.md` — `## Workspace contract (v0.8)` section verbatim from §2.0.
- This doc: `docs/workspace-contract-v0.8.md` (self-contained; supersedes v0.7).

### 3.9 Elevator pitch

> v0.8 puts the workspace where Claude Code looks for it — inside the repo as a gitignored subdirectory. All native cwd-anchored discovery (CLAUDE.md, skills, commands, agents, MCP, plugins) activates over workspace content with no overlay machinery. Durability is an **explicit user choice**: create a vault (BYO git remote) per scope when you want cross-machine sync. Without a vault, Sutando works as a local-only personal agent. With a vault, every device pulls the same identity, every room member shares the same room knowledge. Migration from v0.7 is additive: workspace_default helpers change their default, an existing-state mover relocates `~/.sutando/workspace/<content>` → `<repo>/workspace/<content>`, and v0.7's opt-in `.workspace-mount/` overlay retires.

---

## Appendix A. Conversation thread reference (key v0.6→v0.7→v0.8 moments)

- **2026-05-29 23:08Z** — v0.6 layout shape locked.
- **2026-05-30** — v0.6 design-locked.
- **2026-05-31 17:13Z–17:18Z** — PR #1366 follow-up + cwd-binding deep dive.
- **2026-05-31 19:08Z** — 10 cwd privileges enumerated; path-anchored vs content-anchored distinction.
- **2026-05-31 19:59Z** — PR #1374 merged (SessionStop→SessionEnd auto-heal).
- **2026-05-31 21:25Z** — `workspace-contract-v0.7-overlay-proposal.md` drafted (opt-in cwd-overlay).
- **2026-05-31 21:36Z** — Option C (path-derived tier, drop suffix) locked.
- **2026-05-31 22:00Z** — full v0.7 spec drafted, integrating v0.6 + naming-iteration + overlay-proposal.
- **2026-05-31 22:03Z** — owner: "Why does it say symlink? I want git-as-diff." → reply proposed git-worktree variant.
- **2026-05-31 23:19Z** — Sutando-Mini info-share on guild membership.
- **2026-05-31 23:25Z** — **owner: "v0.8 — workspace defaults to inside repo, vault is user-created"** → this doc lands.

## Appendix B. PR / branch state

- `feat/workspace-contract-v0.7` (in sutando-v07 clone) — contains `docs/workspace-contract-v0.7.md` (727 lines).
- Issue `sonichi/sutando#1376` — v0.7 implementation plan filed earlier today. [OPEN] decision: close + reopen for v0.8, or comment + amend in place?
- This doc: `docs/workspace-contract-v0.8.md` on the same `feat/workspace-contract-v0.7` branch (or rename to `feat/workspace-contract-v0.8`?)

## Appendix C. Connection to PR #1374

PR #1374 (merged 2026-05-31 19:59Z, commit `cfa07f6`) shipped:
1. Universal `SessionStop` → `SessionEnd` migration (auto-heals on every catchup).
2. Atomic settings.json writes.
3. Mini bot verified.

**Connection to v0.8**: `SessionEnd` remains the reliable hook for "session ended cleanly." v0.8's `sync-engine.sh push` (when a vault is configured) plugs into the same SessionEnd lifecycle. Without #1374's fix, the push would have been silently no-op'd on machines with the stale `SessionStop` key.

PR #1374 + v0.8 are the same design theme delivered in two slices: today (rename-aware) → tomorrow (vault-aware-and-cwd-native).
