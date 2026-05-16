/**
 * Sutando — Per-Tenant Identity
 *
 * Implements §5 Next: "Per-tenant identity scaffold. Introduce `tenant_id`
 * in path resolution. Single-tenant for now, but the resolver becomes
 * three-level (per-tenant / per-device / fleet-shared). Unblocks any
 * non-owner deployment."
 *
 * The three-level model:
 *
 *   $SUTANDO_PRIVATE_DIR/
 *     ├── tenant-default/                 ← today (single-tenant)
 *     │   ├── machine-<host>/             ← per-device (existing personalPath)
 *     │   │   ├── stand-identity.json
 *     │   │   └── pending-questions.md
 *     │   ├── notes/                      ← fleet-shared (existing sharedPersonalPath)
 *     │   └── build_log.md
 *     └── tenant-acme/                    ← future second tenant
 *         ├── machine-<host>/
 *         └── ...
 *
 * Today's single-tenant install has no `tenant-default/` prefix on disk
 * (the existing `personalPath` writes directly to `$SUTANDO_PRIVATE_DIR/
 * machine-<host>/`). This scaffold introduces the resolver function but
 * does NOT migrate existing call sites — that's a deferred follow-up.
 * `tenantPath()` is the new opt-in entry point; `personalPath` /
 * `sharedPersonalPath` keep working unchanged.
 *
 * Migration plan (separate PR after this lands):
 *   1. Backfill `$SUTANDO_PRIVATE_DIR/tenant-default/` symlinked to the
 *      existing layout (or moved). The existing personalPath() falls back
 *      to the workspace anyway, so a one-time move + symlink would migrate.
 *   2. Switch personalPath/sharedPersonalPath to delegate to tenantPath().
 *   3. Single owner becomes "the default tenant"; the engine is ready for
 *      a second tenant without sprinkling `tenant_id` through 30 callers.
 *
 * Tenant ID resolution (high → low precedence):
 *   1. `SUTANDO_TENANT_ID` env var (deployment knob).
 *   2. `<workspace>/state/tenant.json` { "id": "...", "name": "..." }.
 *   3. Falls back to `"default"` (today's single-tenant reality).
 *
 * NOT in scope here:
 *   - Tenant-scoped access policy / auth — that's the §5 Next/Later
 *     "Per-device access policy" + auth boundaries.
 *   - Tenant directory provisioning — owner creates the directory.
 *   - Cross-tenant audit isolation — `audit.jsonl` per tenant is a
 *     follow-up.
 */

import { existsSync, readFileSync } from 'node:fs';
import { hostname } from 'node:os';
import { join } from 'node:path';

const TENANT_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

let cachedTenantId: string | null = null;

function expandHome(p: string): string {
	return p.replace(/^~/, process.env.HOME || '');
}

// Memoize the short hostname — called 3× per tenantPath() invocation,
// and tenantPath() fires on every personalPath/sharedPersonalPath call
// after the #753 migration. Hostname doesn't change at runtime; cache.
const HOSTNAME_SHORT = hostname().split('.')[0];

// Track filenames where we've already warned about both new + legacy
// paths existing simultaneously. Without de-dup, every existsSync
// check during partial migration would log a warning — owner would
// see N×M repeated lines for N readers × M filenames.
const _partialMigrationWarned = new Set<string>();

function workspaceDir(): string {
	return process.env.SUTANDO_WORKSPACE || process.cwd();
}

function readFromStateFile(workspace: string): string | null {
	const path = join(workspace, 'state', 'tenant.json');
	if (!existsSync(path)) return null;
	try {
		const data = JSON.parse(readFileSync(path, 'utf-8')) as { id?: string };
		if (typeof data.id === 'string' && TENANT_ID_PATTERN.test(data.id)) {
			return data.id;
		}
		console.warn(`[Tenant] state/tenant.json id "${data.id}" failed validation; using default`);
	} catch (err) {
		console.warn(`[Tenant] failed to read state/tenant.json: ${(err as Error).message}`);
	}
	return null;
}

/** Resolve the current tenant id. Cached after first call to avoid disk
 *  hits in the hot path. Call `__resetForTests()` in tests that switch IDs. */
export function tenantId(workspace?: string): string {
	if (cachedTenantId !== null) return cachedTenantId;
	const ws = workspace ?? workspaceDir();
	const envId = process.env.SUTANDO_TENANT_ID;
	if (envId && TENANT_ID_PATTERN.test(envId)) {
		cachedTenantId = envId;
		return envId;
	}
	if (envId) {
		console.warn(`[Tenant] SUTANDO_TENANT_ID "${envId}" failed validation; using default`);
	}
	const fromState = readFromStateFile(ws);
	if (fromState) {
		cachedTenantId = fromState;
		return fromState;
	}
	cachedTenantId = 'default';
	return 'default';
}

export type PathScope = 'device' | 'shared';

/** Per-tenant path resolver. Three-level structure:
 *
 *  - `device`: `$SUTANDO_PRIVATE_DIR/tenant-<id>/machine-<host>/<file>`
 *  - `shared`: `$SUTANDO_PRIVATE_DIR/tenant-<id>/<file>`
 *
 *  Workspace fallback (mirrors existing personalPath semantics): if
 *  `SUTANDO_PRIVATE_DIR` is unset OR the resolved path doesn't exist,
 *  returns `<workspace>/<file>` for single-tenant installs that haven't
 *  migrated yet. Callers do `existsSync()` checks against the return.
 *
 *  Returns the FIRST existing path along the priority chain. When nothing
 *  exists, returns the *preferred* private-dir path so an existsSync
 *  check fails gracefully and a subsequent write lands in the right place.
 */
export function tenantPath(filename: string, scope: PathScope = 'shared', workspace?: string, forTenant?: string): string {
	const ws = workspace ?? workspaceDir();
	// Cross-tenant queries pass `forTenant` explicitly; default uses the
	// current cached tenant id. Keeping both shapes lets a caller read
	// another tenant's data without invalidating their own cache.
	const id = forTenant ?? tenantId(ws);
	const privateRoot = process.env.SUTANDO_PRIVATE_DIR;

	if (privateRoot) {
		const root = expandHome(privateRoot);
		const tenantRoot = join(root, `tenant-${id}`);
		const candidate = scope === 'device'
			? join(tenantRoot, `machine-${HOSTNAME_SHORT}`, filename)
			: join(tenantRoot, filename);
		// Single-tenant pre-migration fallback: an existing install may
		// still have files at $SUTANDO_PRIVATE_DIR/machine-<host>/<file>
		// (no tenant-default prefix). Try that path too before workspace.
		const candidateExists = existsSync(candidate);
		if (id === 'default') {
			const legacy = scope === 'device'
				? join(root, `machine-${HOSTNAME_SHORT}`, filename)
				: join(root, filename);
			const legacyExists = existsSync(legacy);
			// Partial-migration warn: both paths exist for the same filename
			// → owner has moved some files but not all. Resolver picks the
			// new tenant path silently; warn-once so owner notices the
			// inconsistency. Cheap (one log per filename per process).
			if (candidateExists && legacyExists && !_partialMigrationWarned.has(filename)) {
				_partialMigrationWarned.add(filename);
				console.warn(
					`[Tenant] partial migration: '${filename}' exists at BOTH ` +
					`'${candidate}' AND legacy '${legacy}'. Resolver picks the tenant path. ` +
					`Move or remove one to silence this warning.`,
				);
			}
			if (candidateExists) return candidate;
			if (legacyExists) return legacy;
		} else {
			if (candidateExists) return candidate;
		}
	}

	const wsPath = join(ws, filename);
	if (existsSync(wsPath)) return wsPath;

	// Nothing exists; return preferred path so the caller's existsSync
	// check fails gracefully and a write lands in the right place.
	if (privateRoot) {
		const root = expandHome(privateRoot);
		const tenantRoot = join(root, `tenant-${id}`);
		return scope === 'device'
			? join(tenantRoot, `machine-${HOSTNAME_SHORT}`, filename)
			: join(tenantRoot, filename);
	}
	return wsPath;
}

/** @internal — test-only reset for the tenant-id cache + partial-migration warn dedup. */
export function __resetForTests(): void {
	cachedTenantId = null;
	_partialMigrationWarned.clear();
}
