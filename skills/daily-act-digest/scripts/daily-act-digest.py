#!/usr/bin/env python3
"""daily-act-digest — once-per-day routine that produces a build_log entry.

Usage:
    python3 skills/daily-act-digest/scripts/daily-act-digest.py [--dry-run]

Reads:
    - git log --since "1 day ago" --oneline --no-merges
    - gh pr list --state merged (filtered to last 24h)
    - memory/ + notes/ files newer than 24h
    - state/act-log.jsonl substantive entries last 24h
    - skills/ + ~/.sutando-memory-sync/skills/ SKILL.md modified last 24h

Writes:
    - Prepends a "## YYYY-MM-DD daily digest" section to build_log.md
      (after line 3, the title block).
    - On --dry-run, prints to stdout instead.

Auto_safe by design: this only appends to build_log; recoverable via git.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def run(cmd, cwd=None):
    """Run a shell command, return stdout. Empty string on error."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=cwd or REPO_ROOT,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_recent_commits(since_hours=24):
    """Recent git commits, excluding merges."""
    since = f"{since_hours} hours ago"
    out = run(["git", "log", f"--since={since}", "--oneline", "--no-merges"])
    return [l for l in out.split("\n") if l.strip()]


def get_recent_merged_prs(since_hours=24):
    """PRs merged in last N hours via gh CLI."""
    out = run(["gh", "pr", "list", "--state", "merged", "--limit", "50",
               "--json", "number,title,mergedAt"])
    if not out:
        return []
    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    recent = []
    for p in prs:
        try:
            dt = datetime.fromisoformat(p["mergedAt"].replace("Z", "+00:00"))
            if dt > cutoff:
                recent.append((p["number"], dt.strftime("%H:%MZ"), p["title"]))
        except Exception:
            continue
    return recent


def get_recent_memory_writes(since_hours=24):
    """Memory files modified in last N hours."""
    memdir = Path.home() / ".claude/projects/-Users-wangchi-Desktop-sutando/memory"
    if not memdir.exists():
        return []
    cutoff = time.time() - since_hours * 3600
    files = []
    for p in memdir.glob("*.md"):
        if p.name == "MEMORY.md":
            continue  # MEMORY.md changes are noise (index updates)
        if p.stat().st_mtime > cutoff:
            desc = ""
            try:
                with open(p) as f:
                    text = f.read(800)
                for line in text.split("\n"):
                    if line.startswith("description:"):
                        desc = line.replace("description:", "").strip()
                        desc = desc.strip('"').strip("'")[:100]
                        break
            except Exception:
                pass
            files.append((p.name, desc))
    return files


def get_recent_notes(since_hours=24):
    """notes/ files added or modified in last N hours."""
    notes_dir = REPO_ROOT / "notes"
    if not notes_dir.exists():
        return []
    cutoff = time.time() - since_hours * 3600
    files = []
    for p in notes_dir.glob("*.md"):
        if p.stat().st_mtime > cutoff:
            title = p.name
            try:
                with open(p) as f:
                    text = f.read(500)
                for line in text.split("\n"):
                    if line.startswith("title:"):
                        title = line.replace("title:", "").strip()[:100]
                        break
            except Exception:
                pass
            files.append((p.name, title))
    return files


def get_substantive_act_entries(since_hours=24):
    """ACT autonomous substantive log entries last N hours.

    Excludes: real_pass, mini_ping_meta, no_emit, cursor_advance, owner_intent.
    """
    log_path = REPO_ROOT / "state/act-log.jsonl"
    if not log_path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    excluded_kinds = {"real_pass", "mini_ping_meta", "no_emit", "cursor_advance",
                      "sync", "audit_note", "verification"}
    excluded_actions = {"owner_intent", "no_reply"}
    entries = []
    try:
        with open(log_path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                ts_str = e.get("ts", "")
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                if dt < cutoff:
                    continue
                if e.get("action") in excluded_actions:
                    continue
                if e.get("kind") in excluded_kinds:
                    continue
                if not e.get("success"):
                    continue
                msg = e.get("message", "")[:120]
                entries.append((dt.strftime("%H:%MZ"), e.get("kind", ""), msg))
    except Exception:
        pass
    return entries


def get_recent_skill_changes(since_hours=24):
    """SKILL.md files modified in last N hours across skills/ + private mirror."""
    cutoff = time.time() - since_hours * 3600
    candidates = [REPO_ROOT / "skills",
                  Path.home() / ".sutando-memory-sync/skills"]
    files = []
    for base in candidates:
        if not base.exists():
            continue
        for p in base.glob("*/SKILL.md"):
            if p.stat().st_mtime > cutoff:
                skill_name = p.parent.name
                title_line = ""
                try:
                    with open(p) as f:
                        text = f.read(300)
                    for line in text.split("\n"):
                        if line.startswith("name:"):
                            title_line = line.replace("name:", "").strip()
                            break
                except Exception:
                    pass
                files.append((skill_name, title_line))
    return files


def build_digest_section(date_str=None):
    """Build the markdown digest section."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    commits = get_recent_commits()
    prs = get_recent_merged_prs()
    mem = get_recent_memory_writes()
    notes = get_recent_notes()
    act_entries = get_substantive_act_entries()
    skills = get_recent_skill_changes()

    # Quiet day check
    total = len(commits) + len(prs) + len(mem) + len(notes) + len(act_entries) + len(skills)
    if total == 0:
        return f"## {date_str} daily digest — quiet day\n\nNo substantive activity recorded in the last 24h.\n"

    out = []
    out.append(f"## {date_str} daily digest — {len(prs)} PRs merged, {len(commits)} commits, "
               f"{len(mem)} memory writes, {len(notes)} notes added, {len(act_entries)} ACT artifacts")
    out.append("")

    if prs:
        out.append(f"**PRs merged ({len(prs)}):**")
        for n, ts, title in prs:
            out.append(f"- #{n} ({ts}): {title[:90]}")
        out.append("")

    if commits:
        out.append(f"**Commits ({len(commits)}, excluding merges):**")
        for c in commits[:15]:  # cap at 15
            out.append(f"- {c[:120]}")
        if len(commits) > 15:
            out.append(f"- ... + {len(commits) - 15} more")
        out.append("")

    if mem:
        out.append(f"**Memory writes ({len(mem)}):**")
        for name, desc in mem[:10]:
            out.append(f"- `{name}`: {desc[:90]}")
        if len(mem) > 10:
            out.append(f"- ... + {len(mem) - 10} more")
        out.append("")

    if notes:
        out.append(f"**Notes added/edited ({len(notes)}):**")
        for name, title in notes[:15]:
            out.append(f"- `notes/{name}` ({title[:80]})")
        if len(notes) > 15:
            out.append(f"- ... + {len(notes) - 15} more")
        out.append("")

    if act_entries:
        out.append(f"**ACT autonomous artifacts ({len(act_entries)}):**")
        for ts, kind, msg in act_entries[:15]:
            out.append(f"- {ts}: `{kind}` — {msg[:100]}")
        if len(act_entries) > 15:
            out.append(f"- ... + {len(act_entries) - 15} more")
        out.append("")

    if skills:
        out.append(f"**Skill changes ({len(skills)}):**")
        for name, title in skills:
            out.append(f"- `{name}`: {title[:80]}")
        out.append("")

    return "\n".join(out) + "\n"


def write_to_build_log(section):
    """Prepend the digest section to build_log.md after the title block."""
    log = REPO_ROOT / "build_log.md"
    if not log.exists():
        log.write_text("# Sutando build log\n\nTracks what has actually been built. "
                       "Updated each loop pass.\n\n" + section)
        return
    text = log.read_text()
    # Insert after line 3 (after "Tracks..." line)
    lines = text.split("\n")
    if len(lines) >= 3:
        # Find the first ## section
        insert_idx = None
        for i, l in enumerate(lines):
            if l.startswith("## "):
                insert_idx = i
                break
        if insert_idx is None:
            insert_idx = 4
    else:
        insert_idx = 4
    new_lines = lines[:insert_idx] + section.split("\n") + lines[insert_idx:]
    log.write_text("\n".join(new_lines))


def main():
    parser = argparse.ArgumentParser(description="Daily ACT digest")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to stdout instead of writing build_log.md")
    parser.add_argument("--date", help="Override date (YYYY-MM-DD); defaults to today UTC")
    args = parser.parse_args()

    section = build_digest_section(args.date)

    if args.dry_run:
        print("=== DRY RUN — would prepend the following to build_log.md ===\n")
        print(section)
        return 0

    write_to_build_log(section)
    print(f"daily-act-digest: appended section ({len(section)} bytes) to build_log.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
