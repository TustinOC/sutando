#!/usr/bin/env bash
# Resolves ONE test file's instrumented-run budget. Sole owner of that policy:
# the gate's report message and the runner both come here, so they cannot drift.
#
#   coverage-file-timeout.sh --print <file>      -> prints the budget in seconds
#   coverage-file-timeout.sh <cmd...> <file>     -> execs <cmd...> <file> under it
#
# A file opts out of the shared default by declaring its own budget in the first
# 25 lines:  # coverage-gate: timeout=420
set -eu

DEFAULT="${COVERAGE_GATE_FILE_TIMEOUT:-120}"

budget_for() {
    local f="$1" declared=""
    if [ -r "$f" ]; then
        declared="$(sed -n '1,25p' "$f" 2>/dev/null \
            | sed -nE 's/^#[[:space:]]*coverage-gate:[[:space:]]*timeout=([0-9]{1,4})[[:space:]]*$/\1/p' \
            | head -1)"
    fi
    # A declaration of 0 is not "no cap"; it is malformed, so it falls through.
    if [ -n "$declared" ] && [ "$declared" -gt 0 ] 2>/dev/null; then
        printf '%s' "$declared"
    else
        printf '%s' "$DEFAULT"
    fi
}

if [ "${1:-}" = "--print" ]; then
    budget_for "${2:-}"
    echo
    exit 0
fi

[ "$#" -ge 1 ] || { echo "coverage-file-timeout: no command given" >&2; exit 2; }
_file="${!#}"
_budget="$(budget_for "$_file")"
if command -v timeout >/dev/null 2>&1; then
    exec timeout -k 5 "$_budget" "$@"
fi
exec "$@"
