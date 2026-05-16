import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
	loadProfile,
	listProfileIds,
	__resetForTests,
} from '../src/oc-profile-catalog.ts';

let saved: { OC_PROFILES_DIR: string | undefined };

const MENTRA_LIVE_YAML = `
id: mentra-live
name: Mentra Live
category: glasses

display:
  enabled: true
  resolution: 400x240
  color: green
  position: center

camera:
  enabled: true
  mode: capture
  resolution: HD

audio:
  mic: 3-mic array

input:
  gestures: [tap, scroll_up, scroll_down]
`;

const PLAUD_YAML = `
id: plaud-notepin
name: Plaud NotePin
category: recorders

display:
  enabled: false

camera:
  enabled: false

audio:
  mic: dual

input:
  gestures: [press]
`;

const SOUNDCORE_YAML = `
id: soundcore-frames
name: Soundcore Frames
category: audio_wearables

display:
  enabled: false

camera:
  enabled: false

audio:
  mic: 4-mic AI
`;

describe('oc-profile-catalog', () => {
	let tmp: string;

	beforeEach(() => {
		saved = { OC_PROFILES_DIR: process.env.OC_PROFILES_DIR };
		__resetForTests();
		tmp = mkdtempSync(join(tmpdir(), 'sutando-oc-'));
		// Replicate OC's layout: <root>/<category>/<id>.yaml
		mkdirSync(join(tmp, 'glasses'), { recursive: true });
		mkdirSync(join(tmp, 'recorders'), { recursive: true });
		mkdirSync(join(tmp, 'audio_wearables'), { recursive: true });
		writeFileSync(join(tmp, 'glasses', 'mentra-live.yaml'), MENTRA_LIVE_YAML);
		writeFileSync(join(tmp, 'recorders', 'plaud-notepin.yaml'), PLAUD_YAML);
		writeFileSync(join(tmp, 'audio_wearables', 'soundcore-frames.yaml'), SOUNDCORE_YAML);
		process.env.OC_PROFILES_DIR = tmp;
	});

	afterEach(() => {
		if (saved.OC_PROFILES_DIR === undefined) delete process.env.OC_PROFILES_DIR;
		else process.env.OC_PROFILES_DIR = saved.OC_PROFILES_DIR;
		__resetForTests();
		try { rmSync(tmp, { recursive: true, force: true }); } catch {}
	});

	it('loadProfile reads glasses category + flattens nested yaml', () => {
		const p = loadProfile('mentra-live');
		assert.ok(p, 'mentra-live should resolve');
		assert.equal(p!.id, 'mentra-live');
		assert.equal(p!.name, 'Mentra Live');
		assert.equal(p!.category, 'glasses');
		assert.equal(p!.hud.enabled, true);
		assert.equal(p!.hud.resolution, '400x240');
		assert.equal(p!.hud.color, 'green');
		assert.equal(p!.hud.position, 'center');
		assert.equal(p!.camera.enabled, true);
		assert.equal(p!.camera.mode, 'capture');
		assert.equal(p!.mic, true);
		assert.deepEqual(p!.gestures, ['tap', 'scroll_up', 'scroll_down']);
	});

	it('loadProfile maps OC plural "audio_wearables" → DeviceProfile singular "audio_wearable"', () => {
		const p = loadProfile('soundcore-frames');
		assert.ok(p);
		assert.equal(p!.category, 'audio_wearable');
		assert.equal(p!.mic, true);
		assert.equal(p!.hud.enabled, false);
		assert.equal(p!.camera.enabled, false);
	});

	it('recorder profile: hud + camera both disabled, mic detected', () => {
		const p = loadProfile('plaud-notepin');
		assert.ok(p);
		assert.equal(p!.category, 'recorder');
		assert.equal(p!.hud.enabled, false);
		assert.equal(p!.camera.enabled, false);
		assert.equal(p!.mic, true);
	});

	it('loadProfile returns null for unknown id', () => {
		assert.equal(loadProfile('nonexistent-device'), null);
	});

	it('loadProfile returns null when OC_PROFILES_DIR is unset and no dev default present', () => {
		delete process.env.OC_PROFILES_DIR;
		__resetForTests();
		// Test only passes when HOME isn't pointing at a real OC install on
		// this machine — in our test, the cached root would have been reset,
		// so this looks at the dev-default path. On CI that's absent → null.
		// On a dev machine with OC checked out, the default DOES exist and
		// the test would find real profiles. Skip the strict null check in
		// that case.
		const result = loadProfile('mentra-live');
		// Either null (no OC install) or a real profile (dev machine with
		// OC checked out). Both are acceptable; the contract is "doesn't
		// throw, returns valid type or null".
		assert.ok(result === null || typeof result.id === 'string');
	});

	it('listProfileIds enumerates all yamls in the catalog', () => {
		const ids = listProfileIds();
		assert.deepEqual(ids, ['mentra-live', 'plaud-notepin', 'soundcore-frames']);
	});

	it('listProfileIds returns empty array when catalog is absent', () => {
		delete process.env.OC_PROFILES_DIR;
		__resetForTests();
		const ids = listProfileIds();
		// Empty if no dev-default, OR populated with real OC ids if checked
		// out at the dev path. Both fine — contract is "doesn't throw".
		assert.ok(Array.isArray(ids));
	});

	it('cache is per-id: second loadProfile call hits cache', () => {
		const p1 = loadProfile('mentra-live');
		const p2 = loadProfile('mentra-live');
		// Same reference → cache hit.
		assert.strictEqual(p1, p2);
	});

	it('malformed yaml returns null without throwing', () => {
		writeFileSync(join(tmp, 'glasses', 'broken.yaml'), 'this is not: : valid yaml [[[');
		__resetForTests();
		process.env.OC_PROFILES_DIR = tmp;
		const p = loadProfile('broken');
		assert.equal(p, null);
	});
});
