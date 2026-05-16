// TypeScript twin of src/util_paths.py — personal-asset path resolution.
//
// As of PR #748's tenant scaffold, these helpers delegate to `tenantPath`
// (src/tenant.ts) which handles the three-level resolution
// (per-tenant / per-device / fleet-shared) plus the single-tenant legacy
// fallback. Single-tenant installs (no SUTANDO_TENANT_ID, no
// state/tenant.json) see no behavior change — `tenantPath` falls through
// to the legacy `$SUTANDO_PRIVATE_DIR/machine-<host>/<file>` shape when
// `tenant-default/` hasn't been provisioned.
//
// Two helpers preserved as the public surface:
//
//   personalPath(filename)          — per-machine state
//                                     (stand-identity.json, pending-questions.md).
//                                     Maps to tenantPath(..., 'device').
//   sharedPersonalPath(filename)    — fleet-shared state (notes, build_log).
//                                     Maps to tenantPath(..., 'shared').
//
// Special case: `stand-avatar.png` also lives under `<workspace>/assets/`
// in the public repo. The avatar fallback is preserved here.

import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { tenantPath } from './tenant.js';

/** Per-machine resolver. Delegates to `tenantPath(..., 'device')`. */
export function personalPath(filename: string, workspace?: string): string {
	const ws = workspace ?? process.cwd();
	// Avatar fallback — stand-avatar.png ships under assets/ in the public
	// repo. Preserve the special case so a fresh install with no private
	// dir still resolves the asset.
	//
	// TODO(classification): the avatar is conceptually fleet-shared (every
	// Mac uses the same image), so it would arguably belong under
	// `sharedPersonalPath`. Moving it would subtly change resolution order
	// (`tenantPath(..., 'shared')` would check `tenant-<id>/` before the
	// assets/ fallback) which could mask a future per-tenant-avatar feature.
	// Leaving here for now; revisit if/when avatars get per-tenant
	// customization.
	if (filename === 'stand-avatar.png') {
		const inAssets = join(ws, 'assets', filename);
		if (existsSync(inAssets)) return inAssets;
	}
	return tenantPath(filename, 'device', ws);
}

/** Shared-across-fleet resolver. Delegates to `tenantPath(..., 'shared')`. */
export function sharedPersonalPath(filename: string, workspace?: string): string {
	return tenantPath(filename, 'shared', workspace);
}
