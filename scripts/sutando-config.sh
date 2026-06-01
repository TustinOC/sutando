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

case "$cmd" in
  workspace)
    # `python3 -c` instead of `-m` so we don't pollute argv[0] with a module
    # path that confuses the loader's exe-anchored repo discovery.
    python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from src.sutando_config import resolve_workspace
print(resolve_workspace(), end='')
"
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

  dump)
    python3 -m src.sutando_config
    ;;

  *)
    echo "usage: $0 {workspace|vault-enabled|vault-url|dump}" >&2
    exit 2
    ;;
esac
