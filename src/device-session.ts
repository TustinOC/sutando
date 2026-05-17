/**
 * Sutando — DeviceSession
 *
 * Single source of truth for "which device is connected and what can it do?"
 * Replaces the four module-level globals in `vision-tools.ts` (sessionRef,
 * ticker, activeSource, pushMode) which today encode "the one device sending
 * us frames" and force every push-mode sender — browser tab, Mentra glasses,
 * Discord/Telegram photo helper, phone agent — to race for one slot.
 *
 * Roadmap anchor:
 * - §5 Now item 3 ("Define DeviceSession in src/voice-agent.ts"), explicit
 *   TODO at `vision-tools.ts:144-150`.
 * - §5 Now item 2 (Mentra wire-up): `mentra-bridge.ts` registers via the
 *   `/vision/register` endpoint added in this PR.
 * - §5 Next ("Device-aware output routing"): output channels declared per
 *   session; `routeReply` (separate PR) consumes them.
 * - §7 device fleet matrix: each row corresponds to a `DeviceProfile` shape.
 *
 * Design doc: `notes/devicesession-design-2026-05-16.md`.
 *
 * Scope of this file:
 * - Defines the data types (`DeviceProfile`, `OutputChannel`, `DeviceSession`).
 * - In-process registry keyed by `deviceId`.
 * - `activeForVision()` resolver — the one device that owns the vision slot
 *   right now. Last-activated wins.
 *
 * Out of scope (follow-up PRs):
 * - Output routing (`routeReply`) — declared shape only; no caller wired yet.
 * - OC `device_register` envelope consumption — Mentra bridge calls the
 *   in-repo HTTP register endpoint instead, mirrors the same payload.
 * - Multi-tenant — `deviceId` is process-local; tenant boundary wraps later.
 */

// --- Types --------------------------------------------------------------

/** A voice-agent transport that can ferry frames + hidden context turns
 *  into the upstream realtime model. Subset of bodhi's VoiceSession
 *  transport; redeclared here to avoid a circular import with vision-tools. */
export interface VoiceTransport {
	sendFile?: (base64: string, mimeType: string) => void;
	sendContent?: (
		turns: Array<{ role: 'user' | 'assistant'; text: string }>,
		turnComplete?: boolean,
	) => void;
	isConnected?: boolean;
}

/** Declared capabilities of the device, sourced from OpenCompanion's
 *  `device-profiles/<category>/<id>.yaml`. Mic/camera/HUD presence dictates
 *  what tools are valid for this session. Mirrors the fields of OC's yaml
 *  with the bits we actually consume today. */
export interface DeviceProfile {
	id: string;
	name: string;
	category: 'glasses' | 'audio_wearable' | 'recorder' | 'phone' | 'laptop' | 'other';
	mic: boolean;
	camera: { enabled: boolean; mode?: 'stream' | 'capture'; resolution?: string };
	hud: { enabled: boolean; resolution?: string; color?: string; position?: string };
	gestures?: string[];
	/** Free-text from §7 — what Sutando does with this device. */
	role?: string;
}

/** Per-output declarations. A session can carry any subset of these; the
 *  output router (separate PR) picks the right one per reply shape. */
export type OutputChannel =
	| { kind: 'voice'; transport: VoiceTransport }
	| { kind: 'hud'; render: (text: string, zone?: HudZone) => Promise<void> }
	| { kind: 'file_push'; push: (path: string) => Promise<void> }
	| { kind: 'task'; writeTask: (text: string) => string };

export type HudZone = 'center' | 'top' | 'bottom' | 'left' | 'right';

/** Per-device access policy declared by `src/device-access-policy.ts`.
 *  Defined here so DeviceSession can carry the field without a circular
 *  import from device-access-policy.ts (which uses DeviceProfile). */
export interface DeviceAccessPolicyRef {
	/** Capabilities this device is granted. Set of capability strings —
	 *  see `device-access-policy.ts` for the canonical Capability union. */
	allowed: Set<string>;
	label?: string;
	owner_approved: boolean;
}

/** One connected device. Owns the slice of state today's vision-tools
 *  globals encode (push mode, source label, frame counts). */
export interface DeviceSession {
	deviceId: string;
	profile: DeviceProfile;
	createdAt: number;
	lastFrameAt: number | null;
	pushMode: boolean;
	pushSourceLabel: string | null;
	frameCount: number;
	output: OutputChannel[];
	/** Optional — populated by `attachPolicy()` from device-access-policy.ts.
	 *  When absent, `checkPolicy()` denies everything (safe default). */
	policy?: DeviceAccessPolicyRef;
	/** Idempotent teardown. Called by `unregister()`. */
	close: () => Promise<void>;
}

// --- Registry -----------------------------------------------------------

const sessions = new Map<string, DeviceSession>();
let activeForVisionId: string | null = null;
const listeners = new Set<(event: SessionEvent) => void>();

export type SessionEvent =
	| { kind: 'registered'; deviceId: string; profile: DeviceProfile }
	| { kind: 'unregistered'; deviceId: string }
	| { kind: 'activated'; deviceId: string };

/** Register a new device session. Throws on duplicate `deviceId`; callers
 *  are responsible for unique IDs (typically `${profile.id}-${sessionId}`). */
export function register(session: DeviceSession): void {
	if (sessions.has(session.deviceId)) {
		throw new Error(`DeviceSession ${session.deviceId} already registered`);
	}
	sessions.set(session.deviceId, session);
	emit({ kind: 'registered', deviceId: session.deviceId, profile: session.profile });
}

/** Remove a device session. Calls its `close()` first. Safe to call with
 *  unknown deviceIds (no-op). */
export function unregister(deviceId: string): void {
	const s = sessions.get(deviceId);
	if (!s) return;
	sessions.delete(deviceId);
	if (activeForVisionId === deviceId) activeForVisionId = null;
	void s.close().catch(() => {});
	emit({ kind: 'unregistered', deviceId });
}

export function get(deviceId: string): DeviceSession | undefined {
	return sessions.get(deviceId);
}

export function list(): DeviceSession[] {
	return Array.from(sessions.values());
}

/** Mark a session as the current vision target. Frames POSTed to the
 *  vision control server route here. Last call wins. */
export function activate(deviceId: string): void {
	if (!sessions.has(deviceId)) {
		throw new Error(`cannot activate unknown device ${deviceId}`);
	}
	activeForVisionId = deviceId;
	emit({ kind: 'activated', deviceId });
}

/** Which session owns the vision slot? Returns null when no session has
 *  been activated yet (or after the active one was unregistered). */
export function activeForVision(): DeviceSession | null {
	if (!activeForVisionId) return null;
	return sessions.get(activeForVisionId) ?? null;
}

/** Subscribe to lifecycle events. Returns an unsubscribe handle.
 *  Useful for the vision control server to react to register/activate
 *  without polling. */
export function onEvent(fn: (event: SessionEvent) => void): () => void {
	listeners.add(fn);
	return () => listeners.delete(fn);
}

function emit(event: SessionEvent): void {
	for (const fn of listeners) {
		try { fn(event); }
		catch (err) { console.error('[DeviceSession] listener threw:', err); }
	}
}

// --- Convenience constructors ------------------------------------------

/** Build a DeviceSession for the local laptop — voice-agent boots this
 *  by default so today's call sites (which assume "there is one device")
 *  still see a session to route through. */
export function newLaptopSession(transport: VoiceTransport, deviceId = 'laptop'): DeviceSession {
	const profile: DeviceProfile = {
		id: 'laptop',
		name: 'Mac (laptop / Studio)',
		category: 'laptop',
		mic: true,
		camera: { enabled: true, mode: 'capture', resolution: 'screen' },
		hud: { enabled: true, resolution: 'full', position: 'center' },
		role: 'Full engine, full output. The cockpit.',
	};
	return {
		deviceId,
		profile,
		createdAt: Date.now(),
		lastFrameAt: null,
		pushMode: false,
		pushSourceLabel: null,
		frameCount: 0,
		output: [{ kind: 'voice', transport }],
		close: async () => {},
	};
}

// --- Test hooks (intentionally not exported via package) ---------------

/** @internal — test-only reset. Do not call from production paths. */
export function __resetForTests(): void {
	sessions.clear();
	activeForVisionId = null;
	listeners.clear();
}
