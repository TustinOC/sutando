#!/usr/bin/env python3
from __future__ import annotations
"""Material-ready threshold check v1 — Piece 2 of WIRE recurring loop.

Reads candidate files emitted by overnight-news-survey.py across all fleet
beats, applies Air's editorial gate v1, and emits a material-ready file when
the gate fires.

Air's editorial gate v1 (from #bot2bot 2026-06-06 21:28Z):
  HARD GATE — fires only when ALL three are true:
    1. >=3 verified receipts (verifiability >= 0.8, fresh_hours <= 7*24)
    2. >=1 fresh hook (fresh_hours <= 168 = 7d; ideal sub-24h tracked separately)
    3. >=2 beats represented in the above-threshold pool

  SOFT TIE-BREAKERS (used to rank when multiple topics qualify):
    a. corroborating 2nd case for same root cause
    b. cross-lane convergence (multiple beats ranking the same topic high)
    c. live-demo fix available (in-flight Sutando PR that demos the fix)

  Below-bar = HOLD (do not ship-thin).

Output: notes/sutando-wire/material-ready/YYYY-MM-DD-<topic>.md (only when gate fires).

Run nightly after overnight-news-survey.py:
  03:23 local: python3 skills/wire-newsroom/scripts/material-ready-check.py
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

WORKSPACE = Path(os.environ.get("SUTANDO_WORKSPACE", str(Path.home() / ".sutando" / "workspace")))
WIRE_DIR = WORKSPACE / "notes" / "sutando-wire"
CANDIDATES_DIR = WIRE_DIR / "candidates"
MATERIAL_READY_DIR = WIRE_DIR / "material-ready"
WINDOW_DAYS = 7  # consider last 7 days of candidate files


def load_recent_candidates() -> dict[tuple[str, str], list[dict]]:
    """Read candidate-list files emitted in the last WINDOW_DAYS days.

    Returns {(bot, beat): [items]} with items parsed back from the markdown.
    """
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    if not CANDIDATES_DIR.exists():
        return out
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - WINDOW_DAYS * 86400
    for f in CANDIDATES_DIR.glob("*.md"):
        if f.stat().st_mtime < cutoff:
            continue
        content = f.read_text(errors="ignore")
        bot, beat = _parse_frontmatter(content)
        if not bot:
            continue
        items = _parse_above_threshold_section(content)
        out[(bot, beat)].extend(items)
    return out


def _parse_frontmatter(content: str) -> tuple[str | None, str | None]:
    m = re.search(r"^bot:\s*(\S+)", content, re.M)
    bot = m.group(1) if m else None
    m = re.search(r"^beat:\s*(\S+)", content, re.M)
    beat = m.group(1) if m else None
    return bot, beat


def _parse_above_threshold_section(content: str) -> list[dict]:
    """Pull the above-threshold bullet list back out of the markdown."""
    lines = content.splitlines()
    in_section = False
    items = []
    for line in lines:
        if line.startswith("## Above-threshold"):
            in_section = True
            continue
        if line.startswith("## ") and in_section:
            break
        if not in_section:
            continue
        # bullet pattern: - **[title](url)** — score=X.XX (fresh=N.Nh, thesis=X.XX, verif=X.XX) [source]
        m = re.match(
            r"- \*\*\[([^\]]+)\]\(([^)]+)\)\*\* — score=([\d.]+) "
            r"\(fresh=([\d.]+)h, thesis=([\d.]+), verif=([\d.]+)\) \[([^\]]+)\]",
            line,
        )
        if not m:
            continue
        items.append({
            "title": m.group(1),
            "url": m.group(2),
            "score": float(m.group(3)),
            "fresh_hours": float(m.group(4)),
            "thesis_score": float(m.group(5)),
            "verifiability": float(m.group(6)),
            "source": m.group(7),
        })
    return items


def apply_gate(all_items_by_beat: dict[tuple[str, str], list[dict]]) -> dict:
    """Apply Air's hard gate.

    Returns {
        "ready": bool,
        "reasons": list[str],         # why we're at this verdict
        "supporting_receipts": list,  # items satisfying receipts gate
        "fresh_hook": dict | None,
        "beats_represented": list[str],
    }
    """
    # Flatten everything above-threshold across beats
    all_items: list[dict] = []
    for (_bot, beat), items in all_items_by_beat.items():
        for i in items:
            i = dict(i)
            i["beat"] = beat
            all_items.append(i)

    # Hard gate 1: verified receipts (verifiability >= 0.8, fresh_hours <= 7d)
    receipts = [i for i in all_items if i["verifiability"] >= 0.8 and i["fresh_hours"] <= WINDOW_DAYS * 24]
    # Hard gate 2: fresh hook (fresh_hours <= 7d per Air's gate v1; sub-24h is the ideal but not the hard bar)
    fresh_hooks = [i for i in all_items if i["fresh_hours"] <= WINDOW_DAYS * 24]
    # Sub-24h hooks tracked separately for the "ideal" soft signal
    sub_24h_hooks = [i for i in fresh_hooks if i["fresh_hours"] <= 24]
    # Hard gate 3: beats represented (in above-threshold pool)
    beats = sorted({i["beat"] for i in all_items})

    reasons = []
    ready = True
    if len(receipts) < 3:
        ready = False
        reasons.append(f"hard-gate FAIL: only {len(receipts)} verified receipts (need >= 3)")
    else:
        reasons.append(f"hard-gate PASS: {len(receipts)} verified receipts")

    if not fresh_hooks:
        ready = False
        reasons.append("hard-gate FAIL: no fresh <=7d hook available")
    else:
        ideal_note = f", incl. {len(sub_24h_hooks)} sub-24h" if sub_24h_hooks else ", none sub-24h"
        reasons.append(f"hard-gate PASS: {len(fresh_hooks)} fresh <=7d hooks{ideal_note}")

    if len(beats) < 2:
        ready = False
        reasons.append(f"hard-gate FAIL: only 1 beat represented ({beats[0] if beats else '-'}); need >= 2")
    else:
        reasons.append(f"hard-gate PASS: {len(beats)} beats represented ({', '.join(beats)})")

    return {
        "ready": ready,
        "reasons": reasons,
        "receipts": receipts,
        "fresh_hooks": fresh_hooks,
        "beats_represented": beats,
        "total_above_threshold": len(all_items),
    }


def write_material_ready(verdict: dict) -> Path | None:
    date = datetime.now(timezone.utc).date()
    MATERIAL_READY_DIR.mkdir(parents=True, exist_ok=True)
    if verdict["ready"]:
        topic_slug = _propose_topic_slug(verdict)
        out_path = MATERIAL_READY_DIR / f"{date}-READY-{topic_slug}.md"
    else:
        out_path = MATERIAL_READY_DIR / f"{date}-HOLD.md"

    now = datetime.now(timezone.utc).isoformat()
    with out_path.open("w") as f:
        f.write("---\n")
        f.write(f"title: material-ready check {date}\n")
        f.write(f"generated_at: {now}\n")
        f.write(f"ready: {'true' if verdict['ready'] else 'false'}\n")
        f.write(f"beats_represented: [{', '.join(verdict['beats_represented'])}]\n")
        f.write("tags: [sutando-wire, material-ready, recurring-loop]\n")
        f.write("---\n\n")
        f.write(f"# Material-ready check — {date}\n\n")
        f.write("## Verdict\n\n")
        for reason in verdict["reasons"]:
            sym = "✅" if "PASS" in reason else "❌"
            f.write(f"- {sym} {reason}\n")
        f.write("\n")
        if verdict["ready"]:
            f.write("## Receipts (verified, fresh ≤7d)\n\n")
            for r in verdict["receipts"][:8]:
                f.write(f"- **[{r['title']}]({r['url']})** — score={r['score']:.2f} ({r['beat']}, fresh={r['fresh_hours']:.0f}h)\n")
            f.write("\n## Fresh hooks (≤7d; ideal sub-24h tagged)\n\n")
            for h in verdict["fresh_hooks"][:5]:
                ideal = " ⭐sub-24h" if h["fresh_hours"] <= 24 else ""
                f.write(f"- **[{h['title']}]({h['url']})** — {h['beat']}, fresh={h['fresh_hours']:.1f}h{ideal}\n")
            f.write("\n## Next: angle call (human)\n\n")
            f.write("This material cleared the gate. Fleet should propose 1-2 angle picks in #design within 12h, "
                    "Chi adjudicates the final angle, then Air's script template fills in.\n")
        else:
            f.write("## Status\n\n")
            f.write(f"HOLD — not ship-thin. {verdict['total_above_threshold']} items above 0.7 threshold this cycle. "
                    "Re-checking nightly.\n")
    return out_path


def _propose_topic_slug(verdict: dict) -> str:
    """Pick a topic slug from the highest-scoring receipt's title."""
    if not verdict["receipts"]:
        return "topic"
    top = max(verdict["receipts"], key=lambda r: r["score"])
    slug = re.sub(r"[^a-z0-9-]+", "-", top["title"].lower())
    return slug[:48].strip("-")


def main():
    items_by_beat = load_recent_candidates()
    if not items_by_beat:
        print("no candidate files in last 7d — run overnight-news-survey.py first")
        sys.exit(0)
    verdict = apply_gate(items_by_beat)
    out_path = write_material_ready(verdict)
    print(f"wrote {out_path} (ready={verdict['ready']})")


if __name__ == "__main__":
    main()
