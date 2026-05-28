#!/usr/bin/env python3
"""
Read Claude Code quota state from quota-state.json.

Usage:
  python3 read-quota.py              # human readable
  python3 read-quota.py --json       # machine readable
  python3 read-quota.py --gate       # exit 1 if exhausted
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


def _burn_info(util_frac: float, reset_ts_str: str, window_seconds: int) -> dict:
    """Compute burn rate, budget-per-pass, and time-until-exhausted from utilization data."""
    if not reset_ts_str:
        return {}
    reset_ts = int(reset_ts_str)
    now = time.time()
    mins_until_reset = max(0.0, (reset_ts - now) / 60)
    time_elapsed = window_seconds - (reset_ts - now)
    util_pct = util_frac * 100
    remaining_pct = (1 - util_frac) * 100
    info: dict = {"mins_until_reset": round(mins_until_reset)}
    if time_elapsed > 60:
        burn_pct_per_hr = util_pct / (time_elapsed / 3600)
        info["burn_pct_per_hr"] = round(burn_pct_per_hr, 1)
        info["hrs_until_exhausted"] = (
            round(remaining_pct / burn_pct_per_hr, 1) if burn_pct_per_hr > 0 else None
        )
    else:
        info["burn_pct_per_hr"] = None
        info["hrs_until_exhausted"] = None
    if mins_until_reset > 0:
        info["budget_pct_per_pass"] = round(remaining_pct / (mins_until_reset / 5), 1)
    else:
        info["budget_pct_per_pass"] = 0.0
    return info


def _budget_tier(budget: float | None) -> str:
    if budget is None:
        return ""
    if budget >= 3.0:
        return "full"
    if budget >= 1.0:
        return "medium"
    if budget >= 0.25:
        return "light"
    return "minimal"


def main():
    data = json.loads(QUOTA_FILE.read_text())
    headers = data.get("headers", {})

    status = headers.get("anthropic-ratelimit-unified-status", "unknown")
    util_5h = float(headers.get("anthropic-ratelimit-unified-5h-utilization", 0))
    util_7d = float(headers.get("anthropic-ratelimit-unified-7d-utilization", 0))
    reset_5h = headers.get("anthropic-ratelimit-unified-5h-reset", "")
    reset_7d = headers.get("anthropic-ratelimit-unified-7d-reset", "")

    burn = _burn_info(util_5h, reset_5h, 18000)

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

    result.update({
        "mins_until_reset_5h": burn.get("mins_until_reset"),
        "burn_pct_per_hr_5h": burn.get("burn_pct_per_hr"),
        "hrs_until_exhausted_5h": burn.get("hrs_until_exhausted"),
        "budget_pct_per_pass": burn.get("budget_pct_per_pass"),
        "budget_tier": _budget_tier(burn.get("budget_pct_per_pass")),
    })

    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
        return

    if "--gate" in sys.argv:
        sys.exit(0 if result["available"] else 1)

    # Human readable
    print(f"Status: {status}")
    print(f"5h window: {int(util_5h * 100)}% used, {result['remaining_5h_pct']}% remaining")
    if reset_5h:
        mins = burn.get("mins_until_reset", 0)
        print(f"  Resets: {datetime.fromtimestamp(int(reset_5h)).strftime('%H:%M %b %d')} (in {mins} min)")
    budget = burn.get("budget_pct_per_pass")
    burn_hr = burn.get("burn_pct_per_hr")
    if budget is not None and burn_hr is not None:
        tier = _budget_tier(budget)
        print(f"  Budget: {budget}%/pass · {burn_hr}%/hr burn · {tier}")
    print(f"7d window: {int(util_7d * 100)}% used, {result['remaining_7d_pct']}% remaining")
    if reset_7d:
        print(f"  Resets: {datetime.fromtimestamp(int(reset_7d)).strftime('%H:%M %b %d')}")


if __name__ == "__main__":
    main()
