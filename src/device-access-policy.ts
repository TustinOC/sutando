/**
 * Sutando — Per-Device Access Policy
 *
 * Implements §5 Next: "Per-device access policy. Today's 3-tier (owner /
 * verified / unverified) generalizes to per-device: an always-on recording
 * pendant has tighter capability bands than a deliberately-summoned glasses
 * HUD. Default-deny for new device types until owner approves."
 *
 * The 3-tier remains for *who* (channel-level: owner / team / other). This
 * module adds *what* the device may do — per-device capability gating —
 * orthogonal to the tier system. Together they answer: "this person on this
 * device is asking to do X — yes/no?"
 *
 * Design doc anchor: `notes/devicesession-design-2026-05-16.md` §10 hooks.
 *
 * Capabilities (string-typed so the contract is stable across device
 * categories that grow over time):
 *
 *  - `camera_capture`     — pull camera frames into the vision pipeline.
 *  - `audio_listen`       — stream microphone audio into the model.
 *  - `hud_render`         — write text to the device's HUD zone.
 *  - `file_push`          — receive files (e.g. laptop receiving a PDF).
 *  - `vision_frame_send`  — accept push-mode frames POSTed to /vision/frame.
 *  - `task_write`         — emit task files via the task bridge.
 *  - `voice_passthrough`  — speak via the voice transport (most devices).
 *
 * Default capability matrix is keyed by **device category**. The category
 * has higher confidence than the per-id name (which can be vendor-renamed)
 * and matches OC's profile yamls.
 *
 * Override layers (low → high precedence):
 *  1. Built-in defaults (this file).
 *  2. Workspace config — `state/device-policy.json` (gitignored, per-user).
 *  3. Caller hint — passing `policyOverride` to `attachPolicy`. Used when a
 *     bridge knows it should restrict itself further than the category
 *     defaults (e.g. an experimental glasses adapter wants HUD-only).
 *
 * Out-of-scope (intentionally): per-user fan-out (multi-tenant). The
 * §5 Next item "Per-tenant identity scaffold" wraps this when it lands.
 */

import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { DeviceSession, DeviceProfile } from './device-session.js';

export type Capability =
	| 'camera_capture'
	| 'audio_listen'
	| 'hud_render'
	| 'file_push'
	| 'vision_frame_send'
	| 'task_write'
	| 'voice_passthrough';

export interface DeviceAccessPolicy {
	/** Capabilities this device is granted. Missing = denied. */
	allowed: Set<Capability>;
	/** Optional human-readable label for audit logs / `/vision/devices`. */
	label?: string;
	/** Whether owner explicit approval was required + given for this device. */
	owner_approved: boolean;
}

const CATEGORY_DEFAULTS: Record<DeviceProfile['category'], Capability[]> = {
	// Laptop = the owner's primary cockpit. Full capability set.
	laptop: [
		'camera_capture', 'audio_listen', 'hud_render', 'file_push',
		'vision_frame_send', 'task_write', 'voice_passthrough',
	],

	// Phone = full but no HUD (uses the device's screen instead).
	phone: [
		'camera_capture', 'audio_listen', 'file_push',
		'vision_frame_send', 'task_write', 'voice_passthrough',
	],

	// Glasses — deliberately summoned; HUD render + camera (model-dependent)
	// + voice. No file_push because there's no place to land a file. Audio
	// passes through bodhi which gates upstream — `audio_listen` true.
	glasses: [
		'camera_capture', 'audio_listen', 'hud_render',
		'vision_frame_send', 'task_write', 'voice_passthrough',
	],

	// Audio wearable — private voice, no camera, no HUD. Replies go to the
	// user's ears only; recording on the device itself is out-of-scope.
	audio_wearable: [
		'audio_listen', 'task_write', 'voice_passthrough',
	],

	// Recording pendant — always-on capture is the privacy-critical surface.
	// Default denies everything except task_write (so the device can submit
	// transcripts as tasks for the agent to triage). Owner must opt the
	// device into `audio_listen` explicitly. `voice_passthrough` is denied
	// — Sutando never *broadcasts* through a recording pendant.
	recorder: [
		'task_write',
	],

	// Unknown device — default-deny. Owner must approve before any cap is
	// granted. Matches the roadmap's "Default-deny for new device types
	// until owner approves" requirement.
	other: [],
};

/** Resolve the user override file once at module load. The file is small
 *  and read-when-needed; we don't watch it. Owner edits it and triggers a
 *  policy reload via `reloadOverrides()`. */
let overrides: Record<string, Capability[]> | null = null;

function workspaceDir(): string {
	return process.env.SUTANDO_WORKSPACE || process.cwd();
}

function overridePath(): string {
	return join(workspaceDir(), 'state', 'device-policy.json');
}

function loadOverrides(): Record<string, Capability[]> {
	const path = overridePath();
	if (!existsSync(path)) return {};
	try {
		const raw = readFileSync(path, 'utf-8');
		const parsed = JSON.parse(raw) as Record<string, Capability[]>;
		return parsed && typeof parsed === 'object' ? parsed : {};
	} catch (err) {
		console.error(`[DeviceAccessPolicy] failed to read ${path}: ${(err as Error).message}`);
		return {};
	}
}

export function reloadOverrides(): void {
	overrides = loadOverrides();
}

function getOverrides(): Record<string, Capability[]> {
	if (overrides === null) overrides = loadOverrides();
	return overrides;
}

/** Build a policy for a profile. Owner overrides (keyed by profile.id) win
 *  over category defaults. Owner-approval is implied by the presence of an
 *  override entry; absent → category default + owner_approved=false unless
 *  the category is laptop/phone (the user's own primary devices). */
export function defaultPolicyFor(profile: DeviceProfile): DeviceAccessPolicy {
	const ov = getOverrides();
	const fromOverride = ov[profile.id];
	const fromCategory = CATEGORY_DEFAULTS[profile.category] ?? [];
	const allowed = new Set<Capability>(fromOverride ?? fromCategory);
	const ownerApproved =
		fromOverride !== undefined ||
		profile.category === 'laptop' ||
		profile.category === 'phone';
	return { allowed, owner_approved: ownerApproved, label: profile.name };
}

/** Attach a policy to a DeviceSession. Callers may pass a fully-formed
 *  policy (precedence layer 3) instead of the defaulted one. */
export function attachPolicy(session: DeviceSession, override?: Partial<DeviceAccessPolicy>): DeviceAccessPolicy {
	const base = defaultPolicyFor(session.profile);
	if (override?.allowed) base.allowed = new Set(override.allowed);
	if (override?.label) base.label = override.label;
	if (override?.owner_approved !== undefined) base.owner_approved = override.owner_approved;
	(session as DeviceSession & { policy?: DeviceAccessPolicy }).policy = base;
	return base;
}

/** Is the device allowed to perform `capability`? Returns false (deny) if
 *  no policy has been attached — this is the safe default. */
export function checkPolicy(session: DeviceSession, capability: Capability): boolean {
	const p = (session as DeviceSession & { policy?: DeviceAccessPolicy }).policy;
	if (!p) return false;
	return p.allowed.has(capability);
}

/** Console + future audit-log emit on denial. The voice-agent + vision
 *  control server call this when they reject a request so the trace exists
 *  even when no human is watching. */
export function logDenial(session: DeviceSession, capability: Capability, context?: string): void {
	const tag = `[DeviceAccessPolicy] DENY ${session.deviceId} cap=${capability}`;
	const ctx = context ? ` (${context})` : '';
	console.warn(`${tag}${ctx}`);
}

/** Internal — test-only reset for the override cache. */
export function __resetOverridesForTests(): void {
	overrides = null;
}
