import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
	hudBudget,
	hudZone,
	pickSurfaces,
	routeReply,
	type AgentReply,
} from '../src/output-router.ts';
import type { DeviceSession, DeviceProfile } from '../src/device-session.ts';

// Unit tests for the output router. Profile-specific budgets, surface
// selection per ReplyKind, and the dispatch fallthrough behavior.

function profile(overrides: Partial<DeviceProfile> & Pick<DeviceProfile, 'id' | 'category'>): DeviceProfile {
	return {
		name: overrides.id,
		mic: true,
		camera: { enabled: false },
		hud: { enabled: false },
		...overrides,
	} as DeviceProfile;
}

function session(prof: DeviceProfile, outputs: DeviceSession['output']): DeviceSession {
	return {
		deviceId: prof.id + '-test',
		profile: prof,
		createdAt: Date.now(),
		lastFrameAt: null,
		pushMode: false,
		pushSourceLabel: null,
		frameCount: 0,
		output: outputs,
		close: async () => {},
	};
}

describe('hudBudget', () => {
	it('returns 0 when HUD disabled', () => {
		assert.equal(hudBudget(profile({ id: 'mentra-live', category: 'glasses', hud: { enabled: false } })), 0);
	});
	it('Mentra Live = 80 chars', () => {
		assert.equal(hudBudget(profile({ id: 'mentra-live', category: 'glasses', hud: { enabled: true } })), 80);
	});
	it('Even G2 = 80 chars', () => {
		assert.equal(hudBudget(profile({ id: 'even-g2', category: 'glasses', hud: { enabled: true } })), 80);
	});
	it('Brilliant Halo = 160 chars (richer color HUD)', () => {
		assert.equal(hudBudget(profile({ id: 'brilliant-halo', category: 'glasses', hud: { enabled: true } })), 160);
	});
	it('unknown profile falls back to safe 80', () => {
		assert.equal(hudBudget(profile({ id: 'unseen-device', category: 'other', hud: { enabled: true } })), 80);
	});
});

describe('hudZone', () => {
	it('defaults to center', () => {
		assert.equal(hudZone(profile({ id: 'x', category: 'other' })), 'center');
	});
	it('maps lower-right → right', () => {
		assert.equal(hudZone(profile({ id: 'x', category: 'other', hud: { enabled: true, position: 'lower-right' } })), 'right');
	});
});

describe('pickSurfaces', () => {
	const voiceChan = { kind: 'voice' as const, transport: { sendContent: () => {} } };
	const hudChan = { kind: 'hud' as const, render: async () => {} };
	const filePushChan = { kind: 'file_push' as const, push: async () => {} };
	const taskChan = { kind: 'task' as const, writeTask: () => 'path' };

	it('short → HUD if available, no voice', () => {
		const s = session(
			profile({ id: 'mentra-live', category: 'glasses', hud: { enabled: true } }),
			[voiceChan, hudChan],
		);
		const r = pickSurfaces(s, { text: 'ok', kind: 'short' });
		assert.deepEqual(r, { useHud: true, useVoice: false, useFilePush: false, useTaskFallback: false });
	});

	it('short → voice if no HUD', () => {
		const s = session(profile({ id: 'audio-only', category: 'audio_wearable' }), [voiceChan]);
		const r = pickSurfaces(s, { text: 'ok', kind: 'short' });
		assert.equal(r.useVoice, true);
		assert.equal(r.useHud, false);
	});

	it('narrative → voice; file_push when long body', () => {
		const s = session(profile({ id: 'laptop', category: 'laptop', hud: { enabled: true } }), [voiceChan, hudChan, filePushChan]);
		const long = { text: 'x'.repeat(2000), kind: 'narrative' as const };
		const r = pickSurfaces(s, long);
		assert.equal(r.useVoice, true);
		assert.equal(r.useFilePush, true);
		assert.equal(r.useHud, false); // narrative doesn't fan to HUD
	});

	it('narrative → voice only when body short and no attachments', () => {
		const s = session(profile({ id: 'laptop', category: 'laptop' }), [voiceChan, filePushChan]);
		const r = pickSurfaces(s, { text: 'short reply', kind: 'narrative' });
		assert.equal(r.useVoice, true);
		assert.equal(r.useFilePush, false);
	});

	it('alert → fans to all surfaces', () => {
		const s = session(profile({ id: 'mentra-live', category: 'glasses', hud: { enabled: true } }), [voiceChan, hudChan, filePushChan]);
		const r = pickSurfaces(s, { text: 'fire alarm', kind: 'alert' });
		assert.deepEqual(r, { useHud: true, useVoice: true, useFilePush: true, useTaskFallback: false });
	});

	it('attachments force file_push even on short replies', () => {
		const s = session(profile({ id: 'laptop', category: 'laptop' }), [voiceChan, filePushChan]);
		const r = pickSurfaces(s, { text: 'screenshot', kind: 'short', attachments: ['/tmp/img.png'] });
		assert.equal(r.useFilePush, true);
	});

	it('task fallback when device has no real surfaces', () => {
		const s = session(profile({ id: 'headless-bridge', category: 'other' }), [taskChan]);
		const r = pickSurfaces(s, { text: 'msg', kind: 'narrative' });
		assert.equal(r.useTaskFallback, true);
	});

	it('no task fallback when a real surface picks up', () => {
		const s = session(profile({ id: 'audio-only', category: 'audio_wearable' }), [voiceChan, taskChan]);
		const r = pickSurfaces(s, { text: 'msg', kind: 'narrative' });
		assert.equal(r.useTaskFallback, false);
		assert.equal(r.useVoice, true);
	});
});

describe('routeReply dispatch', () => {
	it('truncates HUD text to budget + collects telemetry', async () => {
		let hudText = '';
		const s = session(
			profile({ id: 'even-g2', category: 'glasses', hud: { enabled: true, position: 'lower-right' } }),
			[
				{ kind: 'hud', render: async (text) => { hudText = text; } },
			],
		);
		const longReply: AgentReply = { text: 'a'.repeat(200), kind: 'short' };
		const result = await routeReply(s, longReply);
		// Budget for even-g2 is 80; truncate appends ellipsis.
		assert.ok(hudText.length <= 80);
		assert.equal(hudText.endsWith('…'), true);
		assert.deepEqual(result.used, ['hud']);
		assert.equal(result.hudZone, 'right');
	});

	it('alert dispatches to all surfaces; HUD failure does not block voice', async () => {
		let voiceCalled = false;
		const s = session(
			profile({ id: 'mentra-live', category: 'glasses', hud: { enabled: true } }),
			[
				{ kind: 'voice', transport: { sendContent: () => { voiceCalled = true; } } },
				{ kind: 'hud', render: async () => { throw new Error('display offline'); } },
			],
		);
		const result = await routeReply(s, { text: 'alert', kind: 'alert' });
		assert.equal(voiceCalled, true);
		assert.ok(result.dropped.includes('hud'));
		assert.ok(result.used.includes('voice'));
	});

	it('task fallback fires when nothing else picks up', async () => {
		let taskText = '';
		const s = session(
			profile({ id: 'headless', category: 'other' }),
			[{ kind: 'task', writeTask: (t) => { taskText = t; return '/tmp/x.txt'; } }],
		);
		await routeReply(s, { text: 'no surfaces', kind: 'narrative' });
		assert.equal(taskText, 'no surfaces');
	});
});
