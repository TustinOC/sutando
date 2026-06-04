#!/usr/bin/env bash
# Bash wrapper around src/sutando_config.py.
#
# Shell scripts can call this instead of inlining `${SUTANDO_WORKSPACE:-...}`
# defaults — keeping the resolution contract in one place (the Python loader)
# and avoiding the split-brain bug class where bash + Python compute different
# workspace paths from the same env.
#
# Usage:
#   bash scripts/sutando-config.sh workspace     # print resolved workspace path
#   bash scripts/sutando-config.sh vault-enabled # print "true" or "false"
#   bash scripts/sutando-config.sh vault-url     # print vault remote_url (may be empty)
#   bash scripts/sutando-config.sh dump          # print full merged config as JSON
#   bash scripts/sutando-config.sh subdirs       # print canonical workspace subdir list (one per line)
#   bash scripts/sutando-config.sh bootstrap     # mkdir -p the canonical subdirs in the resolved workspace
#
# `bootstrap` is the idempotent setup step for the in-repo workspace introduced
# in M0 (PR #1395). startup.sh runs this transitively via init.sh --auto, but
# any context that doesn't go through startup.sh (e.g. a workspace path change
# without service restart, a fresh clone where the user pokes at workspace/
# directly) can call this to ensure the canonical layout exists.
#
# Stdout is the value (no trailing newline for scalar getters); stderr
# carries any warnings from the loader (legacy env, .env drift). Returns
# non-zero only on malformed config.
#
# Migration target — replace patterns like:
#   WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"
# with:
#   WORKSPACE="$(bash scripts/sutando-config.sh workspace)"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cmd="${1:-workspace}"

# --------------------------------------------------------------------------- #
# Layer 1 cache (workspace path only)                                          #
#                                                                              #
# `workspace` is the hot getter — called by init.sh, startup.sh, every cron    #
# pass, fixture-heavy test runs. Each cold call pays ~28 ms (subshell fork +  #
# python interpreter startup + module load). Cache hits drop to ~4 ms.        #
# Boot wall-clock saves ~400-600 ms across the 15-25 helper calls during      #
# init.sh + service launch.                                                   #
#                                                                              #
# Correctness:                                                                 #
#   - Cache file is per-user (UID) + per-repo (REPO_ROOT with /->_ slug) so   #
#     multi-repo workflows don't cross-contaminate.                            #
#   - Invalidated when sutando.config.{json,local.json} or .env is touched     #
#     (stat -nt — strictly newer; same-second edge case accepts a rare false   #
#     hit, recovered on the next mtime tick).                                  #
#   - Bypassed entirely when $SUTANDO_WORKSPACE is set in the env, so the     #
#     legacy-env warning fires on every call (operator cleanup guidance) AND  #
#     so the SUTANDO_TEST_MODE=1 escape hatch (which honors env in tests)     #
#     never poisons the steady-state cache with a per-test path.              #
#                                                                              #
# Best-effort: cache write failures are silently ignored (correct value is     #
# always emitted; cache is a perf optimization, not a contract).              #
# --------------------------------------------------------------------------- #
_workspace_cache_file() {
  local cache_dir="${TMPDIR:-/tmp}"
  # Bash builtin string substitution for per-repo namespacing — `/` → `_`.
  # No subprocess fork (shasum was ~19 ms / call, which would have eaten most
  # of the cache savings). Filename uniqueness is per repo-path, not crypto —
  # collisions only matter across two checkouts that differ by exactly one
  # `/` ↔ `_` swap, which is vanishingly rare on real systems. UID prefix
  # keeps the cache per-user so multi-tenant hosts don't cross-contaminate.
  local repo_slug="${REPO_ROOT//\//_}"
  printf '%s/sutando-workspace-cache-%s-%s' "${cache_dir%/}" "$(id -u 2>/dev/null || echo 0)" "$repo_slug"
}

_workspace_cache_valid() {
  local cache_file="$1"
  [ -s "$cache_file" ] || return 1
  local cfg
  for cfg in "$REPO_ROOT/sutando.config.local.json" "$REPO_ROOT/sutando.config.json" "$REPO_ROOT/.env"; do
    if [ -f "$cfg" ] && [ "$cfg" -nt "$cache_file" ]; then
      return 1
    fi
  done
  return 0
}

case "$cmd" in
  workspace)
    # Layer 1 cache hot path — bypass when env is set so warnings fire AND so
    # TEST_MODE-honored paths don't leak into the steady-state cache.
    if [ -z "${SUTANDO_WORKSPACE:-}" ]; then
      _cache_file="$(_workspace_cache_file)"
      if [ -n "$_cache_file" ] && _workspace_cache_valid "$_cache_file"; then
        cat "$_cache_file"
        exit 0
      fi
    fi

    # `python3 -c` instead of `-m` so we don't pollute argv[0] with a module
    # path that confuses the loader's exe-anchored repo discovery.
    _ws_value="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from src.sutando_config import resolve_workspace
print(resolve_workspace(), end='')
")"
    printf '%s' "$_ws_value"

    # Best-effort cache write (atomic via tmp+rename). Skip when env is set
    # (see header comment).
    if [ -z "${SUTANDO_WORKSPACE:-}" ] && [ -n "${_cache_file:-}" ]; then
      _tmp="${_cache_file}.tmp.$$"
      if printf '%s' "$_ws_value" > "$_tmp" 2>/dev/null; then
        mv "$_tmp" "$_cache_file" 2>/dev/null || rm -f "$_tmp" 2>/dev/null || true
      fi
    fi
    ;;

  vault-enabled)
    python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from src.sutando_config import resolve_vault
print('true' if resolve_vault().get('enabled') else 'false', end='')
"
    ;;

  vault-url)
    python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from src.sutando_config import resolve_vault
print(resolve_vault().get('remote_url', ''), end='')
"
    ;;

  claude-sutando-config-dir)
    # M2 — print the absolute CLAUDE_CONFIG_DIR target used by the
    # `claude-sutando` shell alias. Always a sub-folder of resolve_workspace()
    # (the loader enforces; absolute paths and `..` escapes rejected).
    python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from src.sutando_config import resolve_claude_sutando_config_dir
print(resolve_claude_sutando_config_dir(), end='')
"
    ;;

  dump)
    python3 -m src.sutando_config
    ;;

  subdirs)
    # Canonical workspace subdir list. Single source of truth — keep in sync
    # with src/init.sh tier1's create_dir_if_missing calls AND with
    # docs/workspace-config.md's layout section. If you add a subdir here,
    # also document it (and consider whether init.sh / sutando-migrate.sh
    # need to mention it).
    printf 'state\ntasks\nresults\nresults/archive\nresults/calls\nnotes\nlogs\ndata\nconfig\ntelegram-inbox\n'
    ;;

  bootstrap)
    # Resolve workspace, then mkdir -p the canonical subdirs. Idempotent.
    # M1 (post-M0): ensures the in-repo workspace has the expected layout
    # for any path resolved by the loader, regardless of whether startup.sh
    # / init.sh have run since the path was set.
    ws="$(bash "$0" workspace)"
    if [ -z "$ws" ]; then echo "bootstrap: workspace path empty — config error" >&2; exit 1; fi
    bash "$0" subdirs | while IFS= read -r d; do
      mkdir -p "$ws/$d"
    done
    echo "workspace bootstrapped: $ws" >&2
    ;;

  *)
    echo "usage: $0 {workspace|vault-enabled|vault-url|dump|subdirs|bootstrap}" >&2
    exit 2
    ;;
esac
