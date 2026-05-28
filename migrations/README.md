# Sutando Migrations

Numbered migration scripts that transform workspace state on `git pull` instead of silently breaking installs.

## How migrations work

1. On startup, `src/run_migrations.py` reads `state/schema-version.json` from the workspace.
2. It walks this directory numerically, running each `NNNN-*.{sh,py}` whose N > `current`.
3. On success: appends N to `applied`, bumps `current`, writes the updated state atomically (tmp + rename).
4. On any failure: stops loudly (path + exit code + stderr tail) and returns non-zero → `startup.sh` aborts.

## Writing a migration

- Name: `NNNN-<slug>.sh` or `NNNN-<slug>.py` where NNNN = 4-digit number, one higher than the current max.
- The script receives the workspace root as `$1` (sh) or `sys.argv[1]` (py).
- **Must be idempotent** — it may run more than once (e.g. if the process crashed mid-write).
- Exit 0 = applied successfully. Non-zero = failure; startup aborts.
- Keep migrations small. One logical change per script.

## schema-version.json

Written to `$SUTANDO_WORKSPACE/state/schema-version.json`:

```json
{
  "applied": [1, 2, 3],
  "current": 3,
  "engine_version_at_apply": "v0.1.0"
}
```

`applied` is the ordered list of migration numbers that have been applied.
`current` is the highest applied number (the "schema version" of this workspace).
Default on missing file: `{"applied": [], "current": 0}`.

## Collision prevention

`tests/migrations/test_no_collision.py` fails CI if a PR's proposed migration
number is already used by another open PR or is not `max(main) + 1`. Every
PR that adds a migration must pass this check.

## Running migrations manually

```bash
python3 src/run_migrations.py --dry-run    # preview what would run, no changes
python3 src/run_migrations.py              # apply pending migrations
```
