/**
 * Sutando — OpenCompanion Device Profile Catalog Reader
 *
 * Loads `OpenCompanion/device-profiles/<category>/<id>.yaml` into our
 * `DeviceProfile` shape so bridges (Mentra, Even G2, Meta Ray-Ban, etc.)
 * can register with just `profileId` instead of repeating the profile
 * fields in every POST to `/vision/register`.
 *
 * Roadmap anchor: §8 pitfall "Forking the OC substrate. It's tempting to
 * just write our own protocol. Contribute fixes upstream to OC; consume
 * the protocol unchanged." This module is the seam where Sutando consumes
 * the OC profile catalog without forking it.
 *
 * YAML parsing strategy:
 * - Shells out to `python3 -c "import yaml, json, sys; print(json.dumps(yaml.safe_load(sys.stdin.read())))"`.
 *   Python is always present on the dev machine + on launchd-managed hosts;
 *   yaml is in the stdlib via `pyyaml` (already in Sutando's deps).
 * - Avoids adding `js-yaml` (~50KB npm dep) for this single consumer.
 * - In-memory cache: each profileId is parsed at most once per process.
 *   Owner edits to a profile yaml require a process restart, which is
 *   fine — profile yamls are essentially a public-config catalog, not
 *   live state.
 *
 * Failure modes (all return null + warn, never throw):
 * - OC repo not present → null. Caller falls back to inline-profile shape
 *   (the `/vision/register` payload still works without catalog).
 * - profileId not found in catalog → null.
 * - YAML parse failure → null + warn.
 *
 * NOT in scope (yet):
 * - Watching the catalog dir for changes. Profile yamls are stable; a
 *   process restart on owner edit is acceptable.
 * - Consuming OC's `device_register` envelope. Bridges still POST to
 *   in-repo `/vision/register`; this module just enriches the payload
 *   server-side when only `profileId` is provided.
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { homedir } from 'node:os';
import type { DeviceProfile } from './device-session.js';

const CATEGORY_DIRS = ['glasses', 'audio_wearables', 'recorders', 'other'] as const;

const _cache = new Map<string, DeviceProfile | null>();
let _catalogRoot: string | null | undefined = undefined;

function expandHome(p: string): string {
	return p.replace(/^~/, process.env.HOME || homedir());
}

/** Resolve the catalog root once per process. Order:
 *   1. `OC_PROFILES_DIR` env var (deployment knob).
 *   2. `~/Documents/github/OpenCompanion/device-profiles` (dev default).
 *   3. null if neither exists.
 */
function catalogRoot(): string | null {
	if (_catalogRoot !== undefined) return _catalogRoot;
	const envRoot = process.env.OC_PROFILES_DIR;
	if (envRoot) {
		const expanded = expandHome(envRoot);
		if (existsSync(expanded)) { _catalogRoot = expanded; return expanded; }
	}
	const devDefault = join(homedir(), 'Documents', 'github', 'OpenCompanion', 'device-profiles');
	if (existsSync(devDefault)) { _catalogRoot = devDefault; return devDefault; }
	_catalogRoot = null;
	return null;
}

function parseYaml(text: string): Record<string, unknown> | null {
	try {
		const out = execFileSync(
			'python3',
			['-c', "import yaml, json, sys; print(json.dumps(yaml.safe_load(sys.stdin.read())))"],
			{ input: text, encoding: 'utf-8', timeout: 5_000, stdio: ['pipe', 'pipe', 'pipe'] },
		);
		const parsed = JSON.parse(out);
		return (parsed && typeof parsed === 'object') ? parsed as Record<string, unknown> : null;
	} catch (err) {
		console.warn(`[OCProfileCatalog] YAML parse failed: ${(err as Error)?.message ?? err}`);
		return null;
	}
}

function locateProfileYaml(id: string, root: string): string | null {
	for (const cat of CATEGORY_DIRS) {
		const candidate = join(root, cat, `${id}.yaml`);
		if (existsSync(candidate)) return candidate;
	}
	// Fall through: scan all subdirs in case the OC catalog adds a new
	// category before our `CATEGORY_DIRS` constant catches up.
	try {
		for (const entry of readdirSync(root, { withFileTypes: true })) {
			if (!entry.isDirectory()) continue;
			const candidate = join(root, entry.name, `${id}.yaml`);
			if (existsSync(candidate)) return candidate;
		}
	} catch { /* unreadable root */ }
	return null;
}

/** Translate an OC profile yaml's shape into our DeviceProfile.
 *
 *  OC yamls have nested `display:` / `camera:` / `audio:` / `input:` blocks
 *  per `OpenCompanion/device-profiles/glasses/mentra-live.yaml`. We
 *  flatten what we consume (mic, camera, hud, gestures) and ignore the
 *  rest (physical dimensions, connectivity, price) for now.
 */
function toDeviceProfile(id: string, raw: Record<string, unknown>): DeviceProfile {
	const display = (raw.display as Record<string, unknown>) ?? {};
	const camera = (raw.camera as Record<string, unknown>) ?? {};
	const audio = (raw.audio as Record<string, unknown>) ?? {};
	const input = (raw.input as Record<string, unknown>) ?? {};
	const rawCategory = typeof raw.category === 'string' ? raw.category : 'other';
	// OC uses plural category names (matching their dir layout —
	// 'audio_wearables', 'recorders'); our DeviceProfile union uses
	// singular. Map both.
	const CATEGORY_MAP: Record<string, DeviceProfile['category']> = {
		audio_wearables: 'audio_wearable',
		recorders: 'recorder',
	};
	const category = CATEGORY_MAP[rawCategory] ?? (rawCategory as DeviceProfile['category']);
	return {
		id,
		name: typeof raw.name === 'string' ? raw.name : id,
		category,
		mic: !!audio.mic || !!audio.mics || !!audio.microphone,
		camera: {
			enabled: !!camera.enabled,
			mode: camera.mode === 'stream' || camera.mode === 'capture' ? camera.mode : undefined,
			resolution: typeof camera.resolution === 'string' ? camera.resolution : undefined,
		},
		hud: {
			enabled: !!display.enabled,
			resolution: typeof display.resolution === 'string' ? display.resolution : undefined,
			color: typeof display.color === 'string' ? display.color : undefined,
			position: typeof display.position === 'string' ? display.position : undefined,
		},
		gestures: Array.isArray(input.gestures) ? (input.gestures as string[]) : undefined,
	};
}

/** Load a device profile by id. Returns null on miss (catalog absent,
 *  profileId unknown, yaml malformed). Caller falls back to inline
 *  registration shape. */
export function loadProfile(id: string): DeviceProfile | null {
	if (_cache.has(id)) return _cache.get(id) ?? null;
	const root = catalogRoot();
	if (!root) { _cache.set(id, null); return null; }
	const path = locateProfileYaml(id, root);
	if (!path) { _cache.set(id, null); return null; }
	let text: string;
	try { text = readFileSync(path, 'utf-8'); }
	catch { _cache.set(id, null); return null; }
	const raw = parseYaml(text);
	if (!raw) { _cache.set(id, null); return null; }
	const profile = toDeviceProfile(id, raw);
	_cache.set(id, profile);
	return profile;
}

/** List the profile IDs present in the catalog. Owner diagnostics +
 *  /vision/devices completion. */
export function listProfileIds(): string[] {
	const root = catalogRoot();
	if (!root) return [];
	const ids: string[] = [];
	for (const cat of CATEGORY_DIRS) {
		const dir = join(root, cat);
		if (!existsSync(dir)) continue;
		try {
			for (const entry of readdirSync(dir)) {
				if (entry.endsWith('.yaml')) ids.push(entry.replace(/\.yaml$/, ''));
			}
		} catch { /* unreadable */ }
	}
	return ids.sort();
}

/** @internal — test-only cache reset + catalog-root reset. */
export function __resetForTests(): void {
	_cache.clear();
	_catalogRoot = undefined;
}
