// TypeScript twin of src/util_paths.py — personal-asset path resolution.
//
// Two helpers, one for per-machine state, one for shared-across-fleet state:
//
//   personalPath(filename)          — `$SUTANDO_PRIVATE_DIR/machine-<host>/<filename>`
//                                     For files where each Mac has its own copy
//                                     (stand-identity.json, pending-questions.md).
//
//   sharedPersonalPath(filename)    — `$SUTANDO_PRIVATE_DIR/<filename>`
//                                     For files synced across the whole fleet
//                                     (notes/, build_log.md).
//
// Both fall back to `<workspace>/<filename>` so existing installs keep working
// until they migrate.

import { existsSync } from 'node:fs';
import { hostname } from 'node:os';
import { join } from 'node:path';

function expandHome(p: string): string {
	return p.replace(/^~/, process.env.HOME || '');
}

/**
 * Workspace root — per-machine mutable runtime state (tasks/, results/, state/,
 * logs/, and notes/ when $SUTANDO_PRIVATE_DIR is unset). Falls back to the repo
 * root (parent of src/) so legacy installs where these folders sit beside src/
 * keep working. See docs/storage-layout.md.
 */
export const WORKSPACE_DIR: string = (() => {
	const raw = process.env.SUTANDO_WORKSPACE;
	if (raw) return expandHome(raw);
	return new URL('..', import.meta.url).pathname.replace(/\/$/, '');
})();

/** Resolve a path under the workspace root ($SUTANDO_WORKSPACE). */
export function workspacePath(...parts: string[]): string {
	return join(WORKSPACE_DIR, ...parts);
}

/** Per-machine resolver. */
export function personalPath(filename: string, workspace?: string): string {
	const ws = workspace ?? process.cwd();
	const privateRoot = process.env.SUTANDO_PRIVATE_DIR;
	if (privateRoot) {
		const root = expandHome(privateRoot);
		const host = hostname().split('.')[0];
		const candidate = join(root, `machine-${host}`, filename);
		if (existsSync(candidate)) return candidate;
	}
	// stand-avatar.png lives under assets/ in the public workspace.
	if (filename === 'stand-avatar.png') {
		const inAssets = join(ws, 'assets', filename);
		if (existsSync(inAssets)) return inAssets;
	}
	const wsPath = join(ws, filename);
	if (existsSync(wsPath)) return wsPath;
	// Nothing exists; return preferred private path so caller's existsSync()
	// check fails gracefully.
	if (privateRoot) {
		const root = expandHome(privateRoot);
		const host = hostname().split('.')[0];
		return join(root, `machine-${host}`, filename);
	}
	if (filename === 'stand-avatar.png') return join(ws, 'assets', filename);
	return wsPath;
}

/** Shared-across-fleet resolver (top-level private dir, not per-machine). */
export function sharedPersonalPath(filename: string, workspace?: string): string {
	const ws = workspace ?? process.cwd();
	const privateRoot = process.env.SUTANDO_PRIVATE_DIR;
	if (privateRoot) {
		const root = expandHome(privateRoot);
		const candidate = join(root, filename);
		if (existsSync(candidate)) return candidate;
		const wsPath = join(ws, filename);
		if (existsSync(wsPath)) return wsPath;
		return candidate;
	}
	return join(ws, filename);
}
