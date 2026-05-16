import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
	defaultPolicyFor,
	attachPolicy,
	checkPolicy,
	reloadOverrides,
	__resetOverridesForTests,
	type DeviceAccessPolicy,
} from '../src/device-access-policy.ts';
import type { DeviceSession, DeviceProfile } from '../src/device-session.ts';

function profile(overrides: Partial<DeviceProfile> & Pick<DeviceProfile, 'id' | 'category'>): DeviceProfile {
	return {
		name: overrides.id,
		mic: true,
		camera: { enabled: true },
		hud: { enabled: false },
		...overrides,
	} as DeviceProfile;
}

function emptySession(p: DeviceProfile): DeviceSession {
	return {
		deviceId: p.id + '-test',
		profile: p,
		createdAt: Date.now(),
		lastFrameAt: null,
		pushMode: false,
		pushSourceLabel: null,
		frameCount: 0,
		output: [],
		close: async () => {},
	};
}

describe('defaultPolicyFor — category matrix', () => {
	beforeEach(() => __resetOverridesForTests());

	it('laptop gets the full capability set + owner_approved', () => {
		const p = defaultPolicyFor(profile({ id: 'mac', category: 'laptop' }));
		assert.equal(p.owner_approved, true);
		assert.ok(p.allowed.has('camera_capture'));
		assert.ok(p.allowed.has('audio_listen'));
		assert.ok(p.allowed.has('hud_render'));
		assert.ok(p.allowed.has('file_push'));
		assert.ok(p.allowed.has('vision_frame_send'));
		assert.ok(p.allowed.has('task_write'));
		assert.ok(p.allowed.has('voice_passthrough'));
	});

	it('glasses get HUD + camera + voice but no file_push', () => {
		const p = defaultPolicyFor(profile({ id: 'mentra-live', category: 'glasses' }));
		assert.ok(p.allowed.has('hud_render'));
		assert.ok(p.allowed.has('camera_capture'));
		assert.ok(p.allowed.has('voice_passthrough'));
		assert.ok(!p.allowed.has('file_push'));
	});

	it('audio_wearable: voice only, no camera, no HUD', () => {
		const p = defaultPolicyFor(profile({ id: 'aerofit-2', category: 'audio_wearable' }));
		assert.ok(p.allowed.has('voice_passthrough'));
		assert.ok(p.allowed.has('audio_listen'));
		assert.ok(!p.allowed.has('camera_capture'));
		assert.ok(!p.allowed.has('hud_render'));
	});

	it('recorder: task_write only by default; audio_listen requires explicit approval', () => {
		const p = defaultPolicyFor(profile({ id: 'plaud-notepin', category: 'recorder' }));
		assert.ok(p.allowed.has('task_write'));
		assert.ok(!p.allowed.has('audio_listen'));
		assert.ok(!p.allowed.has('voice_passthrough'));
		assert.equal(p.owner_approved, false);
	});

	it('recorder: voice_passthrough stays denied even with owner override of other caps', () => {
		// Privacy-critical assertion. The CATEGORY_DEFAULTS comment explicitly
		// states "Sutando never broadcasts through a recording pendant" —
		// if a future change adds voice_passthrough to the recorder default
		// list (or owner accidentally grants it), this test fails. Owner can
		// still opt in via state/device-policy.json explicitly if they want
		// to, but the default must stay denied.
		const p = defaultPolicyFor(profile({ id: 'omi-pendant', category: 'recorder' }));
		assert.ok(!p.allowed.has('voice_passthrough'),
			'CATEGORY_DEFAULTS.recorder must NOT include voice_passthrough — Sutando does not broadcast through pendants');
	});

	it('unknown category (other) → empty allow set, owner_approved=false', () => {
		const p = defaultPolicyFor(profile({ id: 'mystery-device', category: 'other' }));
		assert.equal(p.allowed.size, 0);
		assert.equal(p.owner_approved, false);
	});
});

describe('attachPolicy + checkPolicy', () => {
	beforeEach(() => __resetOverridesForTests());

	it('attaches the default policy and gates via checkPolicy', () => {
		const s = emptySession(profile({ id: 'mac', category: 'laptop' }));
		attachPolicy(s);
		assert.equal(checkPolicy(s, 'camera_capture'), true);
	});

	it('caller-side policyOverride replaces the allowed set', () => {
		const s = emptySession(profile({ id: 'mac', category: 'laptop' }));
		attachPolicy(s, { allowed: new Set(['voice_passthrough']) });
		assert.equal(checkPolicy(s, 'voice_passthrough'), true);
		assert.equal(checkPolicy(s, 'camera_capture'), false);
	});

	it('checkPolicy denies before attachPolicy (safe default)', () => {
		const s = emptySession(profile({ id: 'mac', category: 'laptop' }));
		assert.equal(checkPolicy(s, 'voice_passthrough'), false);
	});
});

describe('owner overrides via state/device-policy.json', () => {
	let tempDir: string;
	let savedWorkspace: string | undefined;

	beforeEach(() => {
		__resetOverridesForTests();
		tempDir = mkdtempSync(join(tmpdir(), 'sutando-policy-'));
		mkdirSync(join(tempDir, 'state'), { recursive: true });
		savedWorkspace = process.env.SUTANDO_WORKSPACE;
		process.env.SUTANDO_WORKSPACE = tempDir;
	});

	afterEach(() => {
		if (savedWorkspace === undefined) delete process.env.SUTANDO_WORKSPACE;
		else process.env.SUTANDO_WORKSPACE = savedWorkspace;
		try { rmSync(tempDir, { recursive: true, force: true }); } catch {}
		__resetOverridesForTests();
	});

	it('owner can grant audio_listen to a recorder by id', () => {
		writeFileSync(
			join(tempDir, 'state', 'device-policy.json'),
			JSON.stringify({ 'plaud-notepin': ['task_write', 'audio_listen'] }),
		);
		reloadOverrides();
		const p = defaultPolicyFor(profile({ id: 'plaud-notepin', category: 'recorder' }));
		assert.equal(p.owner_approved, true);
		assert.ok(p.allowed.has('audio_listen'));
		assert.ok(p.allowed.has('task_write'));
	});

	it('override for an unknown device makes it owner_approved', () => {
		writeFileSync(
			join(tempDir, 'state', 'device-policy.json'),
			JSON.stringify({ 'mystery-device': ['voice_passthrough'] }),
		);
		reloadOverrides();
		const p = defaultPolicyFor(profile({ id: 'mystery-device', category: 'other' }));
		assert.equal(p.owner_approved, true);
		assert.ok(p.allowed.has('voice_passthrough'));
	});

	it('corrupt overrides file → fall back to category defaults', () => {
		writeFileSync(join(tempDir, 'state', 'device-policy.json'), 'not json {{');
		reloadOverrides();
		const p = defaultPolicyFor(profile({ id: 'mac', category: 'laptop' }));
		assert.ok(p.allowed.has('camera_capture'));  // laptop default
	});

	it('missing overrides file is a clean no-op', () => {
		if (existsSync(join(tempDir, 'state', 'device-policy.json'))) {
			rmSync(join(tempDir, 'state', 'device-policy.json'));
		}
		reloadOverrides();
		const p = defaultPolicyFor(profile({ id: 'plaud-notepin', category: 'recorder' }));
		assert.ok(p.allowed.has('task_write'));
		assert.ok(!p.allowed.has('audio_listen'));
	});

	it('attachPolicy auto-reloads overrides — owner edits picked up without restart', () => {
		// First attach: no override file, recorder gets task-only default.
		const s1 = emptySession(profile({ id: 'plaud-notepin', category: 'recorder' }));
		const p1 = attachPolicy(s1);
		assert.ok(!p1.allowed.has('audio_listen'));
		assert.equal(p1.owner_approved, false);

		// Owner edits state/device-policy.json mid-process to grant
		// audio_listen to that device id.
		writeFileSync(
			join(tempDir, 'state', 'device-policy.json'),
			JSON.stringify({ 'plaud-notepin': ['task_write', 'audio_listen'] }),
		);

		// Second attach (e.g. recorder bridge re-registered, or a different
		// pendant of the same profile id reconnects). Without auto-reload
		// this would still see the cached old policy. With it, the owner
		// edit lands without restart.
		const s2 = emptySession(profile({ id: 'plaud-notepin', category: 'recorder' }));
		const p2 = attachPolicy(s2);
		assert.ok(p2.allowed.has('audio_listen'),
			'attachPolicy must reload overrides — owner-edited grant should be visible');
		assert.equal(p2.owner_approved, true);
	});
});
