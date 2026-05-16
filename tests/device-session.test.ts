import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
	register,
	unregister,
	get,
	list,
	activate,
	activeForVision,
	onEvent,
	newLaptopSession,
	__resetForTests,
	type DeviceSession,
	type DeviceProfile,
	type SessionEvent,
} from '../src/device-session.ts';

// Unit tests for the DeviceSession registry. Covers the contract the
// `/vision/register` endpoint and downstream callers (mentra-bridge,
// phone-conversation, voice-agent) depend on.

function makeSession(deviceId: string, profileId = 'test-device'): DeviceSession {
	const profile: DeviceProfile = {
		id: profileId,
		name: 'Test',
		category: 'other',
		mic: true,
		camera: { enabled: true },
		hud: { enabled: false },
	};
	return {
		deviceId,
		profile,
		createdAt: Date.now(),
		lastFrameAt: null,
		pushMode: false,
		pushSourceLabel: null,
		frameCount: 0,
		output: [{ kind: 'voice', transport: {} }],
		close: async () => {},
	};
}

describe('DeviceSession registry', () => {
	beforeEach(() => __resetForTests());

	it('register stores and get retrieves', () => {
		const s = makeSession('alpha');
		register(s);
		assert.equal(get('alpha')?.deviceId, 'alpha');
		assert.equal(list().length, 1);
	});

	it('register throws on duplicate deviceId', () => {
		register(makeSession('alpha'));
		assert.throws(() => register(makeSession('alpha')), /already registered/);
	});

	it('unregister removes and is idempotent on unknown id', () => {
		register(makeSession('alpha'));
		unregister('alpha');
		assert.equal(get('alpha'), undefined);
		assert.equal(list().length, 0);
		// Idempotent — unknown ID is a no-op.
		unregister('alpha');
		unregister('never-existed');
		assert.equal(list().length, 0);
	});

	it('unregister calls close() on the session', async () => {
		let closed = false;
		const s = makeSession('alpha');
		s.close = async () => { closed = true; };
		register(s);
		unregister('alpha');
		// close() is fire-and-forget — give the microtask a tick to run.
		await new Promise((r) => setImmediate(r));
		assert.equal(closed, true);
	});

	it('activate marks the device as the vision target', () => {
		register(makeSession('alpha'));
		register(makeSession('beta'));
		assert.equal(activeForVision(), null);
		activate('beta');
		assert.equal(activeForVision()?.deviceId, 'beta');
		activate('alpha');
		assert.equal(activeForVision()?.deviceId, 'alpha');
	});

	it('activate throws on unknown deviceId', () => {
		assert.throws(() => activate('nope'), /unknown device/);
	});

	it('unregistering the active device clears activeForVision', () => {
		register(makeSession('alpha'));
		activate('alpha');
		assert.equal(activeForVision()?.deviceId, 'alpha');
		unregister('alpha');
		assert.equal(activeForVision(), null);
	});

	it('emits register/activate/unregister events to subscribers', () => {
		const events: SessionEvent[] = [];
		const off = onEvent((e) => events.push(e));
		register(makeSession('alpha'));
		activate('alpha');
		unregister('alpha');
		off();
		// Verify another emit after unsubscribe does not reach us.
		register(makeSession('beta'));
		assert.deepEqual(
			events.map((e) => e.kind),
			['registered', 'activated', 'unregistered'],
		);
		assert.equal(events[0].kind, 'registered');
		if (events[0].kind === 'registered') {
			assert.equal(events[0].deviceId, 'alpha');
			assert.equal(events[0].profile.id, 'test-device');
		}
	});

	it('listener exceptions do not break emit (other listeners still fire)', () => {
		const seen: string[] = [];
		onEvent(() => { throw new Error('boom'); });
		onEvent((e) => seen.push(e.kind));
		register(makeSession('alpha'));
		assert.deepEqual(seen, ['registered']);
	});

	it('newLaptopSession constructs a laptop-profile session with voice output', () => {
		const transport = { sendFile: () => {}, isConnected: true };
		const s = newLaptopSession(transport, 'laptop-test');
		assert.equal(s.deviceId, 'laptop-test');
		assert.equal(s.profile.category, 'laptop');
		assert.equal(s.profile.hud.enabled, true);
		assert.equal(s.profile.camera.enabled, true);
		const voice = s.output.find((o) => o.kind === 'voice');
		assert.ok(voice && voice.kind === 'voice');
		assert.equal(voice.transport, transport);
	});
});
