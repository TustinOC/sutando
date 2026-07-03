# task-room — long task state in a Matrix room

Implements `notes/spec-task-room-v0.2.md`: one long-running task = one Matrix
room. State events (`space.ag2.task.goal/plan/status/claim/artifact`) hold the
durable "now"; the timeline holds progress. Survives compaction, restarts, and
machine switches — any agent (or a human in Element) reads the room and
continues.

## When to use

A task is **long** when it spans multiple work rounds, needs owner decisions
mid-flight, or should survive this session dying. Short tasks never touch a
room — the file task bus alone is correct for them.

## Setup (once)

The agent identity's Matrix token (e.g. `@sutando-wu-air.agent:ag2.space`):

```
vault set TASKROOM_MATRIX_TOKEN <matrix access token>
```

(or `export TASKROOM_TOKEN=...` per-invocation). Homeserver defaults to
`https://chat.ag2.space`; override with `$TASKROOM_HOMESERVER`.

## Driving a task

```bash
T=skills/task-room/taskroom.py
ROOM=$(python3 $T create --goal "Bring sutando-wu-air online" --invite @qingyun:ag2.space)
python3 $T plan --room $ROOM --steps "fix:concierge fixes" "deploy:deploy" "wire:room-writer" \
                --needs wire=compute
python3 $T claim  --room $ROOM --step fix --agent @sutando-wu-air.agent:ag2.space
python3 $T step   --room $ROOM --step fix --status doing    # ... work ...
python3 $T step   --room $ROOM --step fix --status done
python3 $T say    --room $ROOM --text "fix deployed; starting wire step"
python3 $T artifact --room $ROOM --id pr120 --title "PR #120" --ref "https://github.com/.../120" --step fix
python3 $T status --room $ROOM --status completed
```

## Room assets (v1 — room !kQRxkWDICYxuQZRONo design)

Structured per-room state beyond the plan: what the next person/agent entering
the room would do wrong without. Admission rule + classes (owner-ratified
2026-06-12): **decision** (ratified choices: who/when/what/why), **context**
(behavior-affecting params + load-bearing conclusions), **resource** (durable
pointers — source paths, hosts, dashboards; for secrets ONLY the vault
location `vault:KEY`, never the value). Same key overwrites (supersede).

```bash
python3 $T decision --room $ROOM --id share-scope --what "full screen" --by @qingyun:ag2.space --why "demo value"
python3 $T context  --room $ROOM --key stream-params --value "1280x832@10fps" --kind param
python3 $T context  --room $ROOM --key lk-naming --value "LK room == matrix room id" --kind conclusion --source "server.ts:829"
python3 $T resource --room $ROOM --id ec2 --title "EC2 box" --ref ubuntu@1.2.3.4 --kind host
```

Rendered in `resume`, and on https://ag2.space/tasks under each card's
"🧩 Assets" panel.

`status --status completed` auto-harvests: digest (decisions/context/
resources/artifacts/plan) → `<workspace>/notes/taskroom-digest-*.md` + room
artifact `digest` + a 365-day portable share link (any browser, no Matrix
login; the raw endpoint is agent-curl-able). Opt out with `--no-harvest`;
run manually with `python3 $T harvest --room $ROOM [--depth minimal|standard|full]`.
ALWAYS pass `--summary "2-3 sentence purpose & outcome"` when completing (or
set a `purpose` context asset) — it heads the digest and shows in the fleet
index listing so agents can triage without opening links.
Depth default = the room's `digest-depth` context asset, else standard
(minimal = decisions+resources card; full = + step needs + last 30 messages).

Harvest also registers the digest in the relay's fleet index
(`POST /v1/digests`, owner-scoped). **Before opening a NEW room on related
work, run `python3 $T digests`** — it lists every harvested digest in the
fleet (goal / room / portable URL); curl the URL and carry the context in.

## Resume rule (after restart/compaction/machine switch)

```bash
python3 $T resume --room $ROOM
```

Prints goal → plan (with claim holders) → recent messages as markdown. That
output is the authoritative context; do not reconstruct task state from any
other source.

## Conventions

- Owner questions: `say` a message mentioning the owner; their in-room reply
  unblocks the step (pending-questions re-targeted).
- Claims carry a lease (default 1h); an expired lease is takeable by any
  capability-matching agent. The `foreman` role is claimed the same way
  (step-id `foreman`).
- Status transitions also post as `m.notice` so vanilla Element shows history.
