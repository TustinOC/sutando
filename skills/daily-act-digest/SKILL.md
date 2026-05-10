---
name: daily-act-digest
description: "Once-per-day autonomous routine that scans recent commits, merged PRs, memory writes, and act-log substantive entries, then appends a structured section to build_log.md. Replaces ad-hoc 'remember to update build_log' with a scheduled cron-driven artifact."
user-invocable: true
status: draft (created 2026-05-09, awaiting owner ack on cron schedule)
---

# Daily ACT Digest

Once-per-day routine that produces a build_log entry summarizing the previous 24h of substantive output across (a) git commits, (b) merged PRs, (c) memory dir writes, (d) notes/ adds, (e) ACT autonomous artifacts. Auto_safe — appends to build_log only.

**Usage**: `/daily-act-digest [--dry-run]`

ARGUMENTS: $ARGUMENTS

## Why

Per `notes/act-autonomy-thin-diagnosis-2026-05-09.md` Fix 2: ACT autonomy stays thin in part because there's no scheduled routine that surfaces what was actually shipped. Build_log went 4 days stale before being manually refreshed 2026-05-09 evening. This skill closes the loop.

## What to gather (24h window, since previous digest)

1. **Git commits** — `git log --since "1 day ago" --oneline --no-merges`
2. **Merged PRs** — `gh pr list --state merged --limit 30 --json number,title,mergedAt --jq '.[] | select(.mergedAt > <cutoff>)'`
3. **Memory dir writes** — `find ~/.claude/projects/-Users-wangchi-Desktop-sutando/memory -name "*.md" -newer <yesterday-marker>`
4. **Notes additions** — `find notes -maxdepth 1 -name "*.md" -newer <yesterday-marker>`
5. **ACT autonomous substantive entries** — grep `state/act-log.jsonl` for entries with `success: true` and `action != owner_intent` and `kind not in {real_pass, mini_ping_meta, no_emit, cursor_advance}`
6. **Skill changes** — `find skills/ -name "SKILL.md" -newer <yesterday-marker>` and `find ~/.sutando-memory-sync/skills/ -name "SKILL.md" -newer <yesterday-marker>`

## What to write

Append section to `build_log.md` at top (after the title), formatted:

```markdown
## YYYY-MM-DD daily digest — N PRs merged, N commits, N memory writes, N notes added

**PRs merged ({N}):**
- #NNN ({HH:MMZ}): one-line title

**Commits ({N}, excluding merges):**
- {sha-short}: one-line subject

**Memory writes ({N}):**
- {filename}: short description from frontmatter `description:` field

**Notes added/edited ({N}):**
- `notes/{filename}.md` ({title} from frontmatter)

**ACT autonomous artifacts ({N}):**
- {ts}: {kind} — {message-truncated}

**Skill changes ({N}):**
- {skill-name}: {modification summary from SKILL.md diff first 200 chars}
```

## Cron schedule (proposed, awaiting Chi ack)

```
7 8 * * * /daily-act-digest
```

(Daily at 08:07 AM PT, before morning-briefing's typical 09:00 PT delivery so the digest is included in the briefing.)

The off-minute `:07` follows CronCreate guidance to avoid load-spike on `:00`.

## Auto-safe disposition

- **auto_safe=true**: this only appends to `build_log.md` (low blast, recoverable).
- Owner doesn't need to ack each daily run; only needs to ack the cron-create itself once.

## Failure modes

- **Build_log not present**: skill creates it with the standard header.
- **No activity in 24h**: writes a "## YYYY-MM-DD daily digest — quiet day" entry. Better than skipping (preserves cadence as audit trail).
- **gh / git commands fail**: skill captures the error, writes a partial digest with the failed-source listed, and surfaces a `surface_owner` note (not an ack-DM, just a result file).

## Implementation notes

For the v0.1 implementation, this skill runs in the same Claude Code session as the rest of ACT — invoked via Skill tool. It uses Read/Bash/Edit on local files only. No external API calls beyond `gh pr list` (already authenticated).

Future v0.2: extract the digest-generation into `scripts/daily-act-digest.py` for deterministic invocation.

## Acceptance criteria

After this skill runs successfully for 7 consecutive days:
- `build_log.md` has 7 daily-digest sections.
- Owner can `grep "daily digest" build_log.md | wc -l` and see 7.
- The substantive:bookkeeping ratio in `state/act-log.jsonl` improves because the daily-digest run is itself a substantive autonomous artifact.

## Iteration log

- v0.1.0 — 2026-05-09 — draft created in response to "ACT autonomy too thin" challenge from Chi 22:21Z PT. Awaiting cron-create ack.
