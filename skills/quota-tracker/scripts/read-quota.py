#!/usr/bin/env python3
"""
Read Claude Code quota state from quota-state.json.

Usage:
  python3 read-quota.py              # human readable
  python3 read-quota.py --json       # machine readable
  python3 read-quota.py --gate       # exit 1 if exhausted
  python3 read-quota.py --budget     # print budget tier + per-pass budget
  python3 read-quota.py --budget-json  # machine-readable budget tier

Budget tiers (--budget / --budget-json):
  FULL   = >3% per pass  — subagents, heavy research, code all fair game
  MEDIUM = 1-3% per pass — code fixes, monitoring, no subagents
  LIGHT  = <1% per pass  — task processing + health checks only
  MINIMAL = 0% remaining — process owner tasks + health + update log

Budget per pass = remaining_5h_pct / (minutes_to_reset / 5)
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Canonical (and only) home is <workspace>/state/quota-state.json, written by
# the credential proxy. The skill-dir / cwd fallbacks were removed: a stale
# leftover quota-state.json under skills/quota-tracker/ silently shadowed the
# fresh file and froze the dashboard for ~12h (2026-05-21). One path, one
# source of truth — if it's missing, say so rather than read a stale copy.
# NOTE: `.resolve()` follows the ~/.claude/skills symlink into the repo, so the
# path is <repo>/skills/quota-tracker/scripts/read-quota.py — four levels deep.
# Three .parent landed on <repo>/skills (no src/ there), so the workspace_default
# import silently failed (→ except below → "not found") and quota read as missing
# regardless of where the proxy wrote. Walk up four to reach <repo>/src.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
try:
    from workspace_default import status_read_path  # noqa: E402
    _canonical = status_read_path("quota-state.json")
except Exception:
    _canonical = None

if _canonical is not None and _canonical.exists():
    QUOTA_FILE = _canonical
else:
    print("No quota-state.json found. Is the credential proxy running?")
    sys.exit(1)


def main():
    data = json.loads(QUOTA_FILE.read_text())
    headers = data.get("headers", {})

    status = headers.get("anthropic-ratelimit-unified-status", "unknown")
    util_5h = float(headers.get("anthropic-ratelimit-unified-5h-utilization", 0))
    util_7d = float(headers.get("anthropic-ratelimit-unified-7d-utilization", 0))
    reset_5h = headers.get("anthropic-ratelimit-unified-5h-reset", "")
    reset_7d = headers.get("anthropic-ratelimit-unified-7d-reset", "")

    result = {
        "status": status,
        "available": status == "allowed",
        "utilization_5h": util_5h,
        "utilization_7d": util_7d,
        "remaining_5h_pct": round((1 - util_5h) * 100),
        "remaining_7d_pct": round((1 - util_7d) * 100),
    }

    if reset_5h:
        result["reset_5h"] = datetime.fromtimestamp(int(reset_5h)).isoformat()
    if reset_7d:
        result["reset_7d"] = datetime.fromtimestamp(int(reset_7d)).isoformat()

    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
        return

    if "--gate" in sys.argv:
        sys.exit(0 if result["available"] else 1)

    if "--budget" in sys.argv or "--budget-json" in sys.argv:
        budget = _compute_budget(result, reset_5h)
        if "--budget-json" in sys.argv:
            print(json.dumps(budget, indent=2))
        else:
            tier = budget["tier"]
            bpp = budget["budget_per_pass"]
            mins = budget["minutes_to_reset"]
            rem = result["remaining_5h_pct"]
            print(f"{tier} ({rem}% remaining, {bpp:.1f}%/pass, reset in {mins:.0f}min)")
        return

    # Human readable
    print(f"Status: {status}")
    print(f"5h window: {int(util_5h * 100)}% used, {result['remaining_5h_pct']}% remaining")
    if reset_5h:
        print(f"  Resets: {datetime.fromtimestamp(int(reset_5h)).strftime('%H:%M %b %d')}")
    print(f"7d window: {int(util_7d * 100)}% used, {result['remaining_7d_pct']}% remaining")
    if reset_7d:
        print(f"  Resets: {datetime.fromtimestamp(int(reset_7d)).strftime('%H:%M %b %d')}")


def _compute_budget(result: dict, reset_5h: str) -> dict:
    """Compute budget tier and per-pass budget from quota state."""
    remaining = result["remaining_5h_pct"]
    if remaining == 0:
        return {"tier": "MINIMAL", "budget_per_pass": 0.0, "minutes_to_reset": 0}

    now = time.time()
    reset_ts = int(reset_5h) if reset_5h else (now + 300 * 60)
    minutes_to_reset = max(5, (reset_ts - now) / 60)
    passes_to_reset = minutes_to_reset / 5
    budget_per_pass = remaining / passes_to_reset

    if budget_per_pass > 3:
        tier = "FULL"
    elif budget_per_pass >= 1:
        tier = "MEDIUM"
    else:
        tier = "LIGHT"

    return {
        "tier": tier,
        "budget_per_pass": round(budget_per_pass, 2),
        "remaining_pct": remaining,
        "minutes_to_reset": round(minutes_to_reset),
        "passes_to_reset": round(passes_to_reset, 1),
    }


if __name__ == "__main__":
    main()
