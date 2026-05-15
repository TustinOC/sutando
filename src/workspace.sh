#!/usr/bin/env bash
# Shell twin of src/util_paths.{py,ts} — workspace path resolution for scripts.
#
# Source this from any sutando bash script to get consistent workspace paths.
# Reads $SUTANDO_WORKSPACE; falls back to the repo root (parent of src/) so
# legacy installs where folders sit beside src/ keep working.
#
# See docs/storage-layout.md for the full three-layer model.
#
# Usage:
#   . "$(dirname "${BASH_SOURCE[0]}")/workspace.sh"
#   echo "$TASKS_DIR/foo.txt"
#
# Exported: WORKSPACE_DIR, NOTES_DIR, TASKS_DIR, RESULTS_DIR, STATE_DIR, LOGS_DIR

_workspace_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_workspace_repo_root="$(cd "$_workspace_sh_dir/.." && pwd)"

WORKSPACE_DIR="${SUTANDO_WORKSPACE:-$_workspace_repo_root}"
if [ "$WORKSPACE_DIR" = "~" ]; then
	WORKSPACE_DIR="$HOME"
elif [ "${WORKSPACE_DIR:0:2}" = "~/" ]; then
	WORKSPACE_DIR="$HOME/${WORKSPACE_DIR:2}"
fi

NOTES_DIR="$WORKSPACE_DIR/notes"
TASKS_DIR="$WORKSPACE_DIR/tasks"
RESULTS_DIR="$WORKSPACE_DIR/results"
STATE_DIR="$WORKSPACE_DIR/state"
LOGS_DIR="$WORKSPACE_DIR/logs"

export WORKSPACE_DIR NOTES_DIR TASKS_DIR RESULTS_DIR STATE_DIR LOGS_DIR
unset _workspace_sh_dir _workspace_repo_root
