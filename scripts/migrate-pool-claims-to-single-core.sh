#!/bin/bash
# Migrate the workspace task queue from the multi-core pool layout (pre-rollback)
# to the single-core layout used by main.
#
# WHY: the multi-core experiment (#880) shipped briefly on a feat branch with
#   src/claim_task.py + scripts/install-core-pool.sh. Pool cores claimed tasks
#   with `.claimed-core-{N}.txt` suffix and processed them via the
#   `/proactive-loop-pool` skill. When the experiment was rolled back to main,
#   claim_task.py + the pool skill went with it — but the pool launchd plists
#   (com.sutando.core-{1,2,3}.plist) and any in-flight claimed files survived.
#   The plist cores keep writing heartbeats but have no claim path, so they
#   never produce results. Net: 100+ orphaned `.claimed-core-{0,1,2,3}.txt`
#   files sit in tasks/ forever, tripping `health-check.py`'s stale-task warn.
#
# WHAT THIS SCRIPT DOES (default: dry-run; pass --apply to execute):
#   1. Scan tasks/ for `task-<id>.claimed-core-<N>.txt` where N != legacy.
#   2. For each, write a `[no-send]` result so bridges archive silently
#      (NO user-visible reply — orphaned tasks are days old; replaying them
#      would surface stale conversations).
#   3. Move the claimed task into processed/ for audit.
#   4. Clean up `state/cores/core-{0,1,2,3,4}.alive` (stale, no-op heartbeats).
#   5. Clean up `state/cores/channel-*.handler` whose core_id != legacy.
#   6. (Optional, --remove-launchd) bootout + remove pool launchd plists.
#
# WHAT IT DOES NOT TOUCH:
#   - `.claimed-core-legacy.txt` files (this is the live single-core's namespace).
#   - The current core's heartbeat / handler files.
#   - .env (you may still want to remove `SUTANDO_CORE_POOL_SIZE=3` and
#     `SUTANDO_CORE_ID=legacy` manually after this).

set -euo pipefail

# ── args ──
APPLY=0
REMOVE_LAUNCHD=0
INCLUDE_LEGACY=0  # also archive .claimed-core-legacy.* if set
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --remove-launchd) REMOVE_LAUNCHD=1 ;;
    --include-legacy) INCLUDE_LEGACY=1 ;;
    -h|--help)
      sed -n '2,32p' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"
[ -d "$WORKSPACE/tasks" ] || { echo "no workspace tasks/ at $WORKSPACE" >&2; exit 2; }
cd "$WORKSPACE"
mkdir -p processed results state/cores

mode="DRY-RUN"
[ "$APPLY" = "1" ] && mode="APPLY"
echo "── migrate-pool-claims-to-single-core ($mode) ──"
echo "workspace: $WORKSPACE"
echo

# ── 1. Inventory claimed tasks ──
shopt -s nullglob
pool_claims=(tasks/task-*.claimed-core-[0-9]*.txt)
legacy_claims=(tasks/task-*.claimed-core-legacy.txt)
echo "pool-orphan claims (.claimed-core-{0..9}.txt): ${#pool_claims[@]}"
echo "legacy claims (this core's): ${#legacy_claims[@]}"
echo

to_archive=("${pool_claims[@]}")
if [ "$INCLUDE_LEGACY" = "1" ]; then
  echo "--include-legacy: also archiving legacy claims"
  to_archive+=("${legacy_claims[@]}")
fi
echo "total to archive: ${#to_archive[@]}"
echo

# ── 2. Archive each ──
archived=0
skipped=0
for f in "${to_archive[@]}"; do
  base=$(basename "$f")
  # task-<id>.claimed-core-<N>.txt → task-<id>
  tid="${base%.claimed-core-*}"
  result="results/${tid}.txt"
  if [ -f "$result" ]; then
    # Already has a result file — don't clobber. Just move the claim out.
    if [ "$APPLY" = "1" ]; then
      mv "$f" "processed/$base"
    fi
    skipped=$((skipped+1))
    continue
  fi
  if [ "$APPLY" = "1" ]; then
    # [no-send] marker → bridges archive silently, no user-facing reply
    printf '[no-send]\norphaned by multi-core→single-core migration on %s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$result"
    mv "$f" "processed/$base"
  fi
  archived=$((archived+1))
done
echo "  archived (wrote [no-send] result + moved to processed/): $archived"
echo "  skipped (result already existed, just moved claim): $skipped"
echo

# ── 3. Clean stale core .alive files ──
stale_alive=()
for alive in state/cores/core-[0-9]*.alive; do
  stale_alive+=("$alive")
done
echo "stale pool .alive files: ${#stale_alive[@]}"
if [ ${#stale_alive[@]} -gt 0 ] && [ "$APPLY" = "1" ]; then
  rm -f "${stale_alive[@]}"
  echo "  removed."
fi
echo

# ── 4. Clean channel handlers pointing to non-legacy cores ──
dead_handlers=()
for h in state/cores/channel-*.handler; do
  [ -f "$h" ] || continue
  core_id=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('core_id',''))" "$h" 2>/dev/null || echo "")
  if [ -n "$core_id" ] && [ "$core_id" != "legacy" ]; then
    dead_handlers+=("$h")
  fi
done
echo "channel-handler files bound to dead pool cores: ${#dead_handlers[@]}"
if [ ${#dead_handlers[@]} -gt 0 ] && [ "$APPLY" = "1" ]; then
  rm -f "${dead_handlers[@]}"
  echo "  removed (next message on each channel will re-bind to legacy)."
fi
echo

# ── 5. Optional: bootout + remove pool launchd plists ──
if [ "$REMOVE_LAUNCHD" = "1" ]; then
  echo "── --remove-launchd: removing pool launchd plists ──"
  uid=$(id -u)
  for p in "$HOME/Library/LaunchAgents"/com.sutando.core-[0-9]*.plist; do
    [ -f "$p" ] || continue
    label=$(basename "$p" .plist)
    echo "  $label"
    if [ "$APPLY" = "1" ]; then
      launchctl bootout "gui/$uid/$label" 2>/dev/null || true
      rm -f "$p"
    fi
  done
fi

echo
if [ "$APPLY" = "1" ]; then
  echo "DONE. Run \`python3 src/health-check.py\` to confirm task-queue warn is gone."
else
  echo "DRY-RUN only. Re-run with --apply to execute."
fi
