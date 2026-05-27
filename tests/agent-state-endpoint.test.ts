import { describe, it, before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import type { Server } from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';
import { writeFileSync, unlinkSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { setupTempWorkspace } from './_helpers/temp-workspace.js';
import { startWebServer } from '../src/web-server.js';

// Integration test for PR #418 / #419 agent-state plumbing.
// Boots src/web-server.ts in-process, exercises /sse-status + /mute-state,
// asserts the `state` field flows through the 5-value enum + rejects invalid
// values. Prevents regression of the avatar-animation chain that shipped
// 2026-04-17.
//
// Pre-migration this spawned `tsx src/web-client.ts` as a subprocess. The
// React-client migration folded the HTTP server into voice-agent.ts via a
// `startWebServer()` export, so the test now runs the same code path
// in-process — faster boot + no subprocess lifecycle / pipe-buffer pitfalls.

const PORT = 18081; // well above the 8080 dev server + 9900 voice-agent

// Each test process gets its own SUTANDO_WORKSPACE temp dir so concurrent
// test files (notably tests/get-core-status-tool.test.ts, which writes
// core-status.json with status:'running' as a fixture) can't race with our
// reads. Before #840 fix: both tests shared <REPO_ROOT>/core-status.json
// and node:test parallel file runs caused intermittent
// `expected listening, got working` on the agent-state assertions.
const { workspace: TEMP_WORKSPACE, cleanup: cleanupTempWorkspace } =
	setupTempWorkspace('agent-state');
// Status files live under <workspace>/state/ — web-server reads them via
// statusReadPath. The temp helper doesn't pre-create subdirs, so make state/.
mkdirSync(join(TEMP_WORKSPACE, 'state'), { recursive: true });
const CORE_STATUS_PATH = join(TEMP_WORKSPACE, 'state', 'core-status.json');

// voice-state.json is read by web-server's readVoiceState() as the authoritative
// voiceConnected source (browser POST cache is the fallback). Lives in the
// same per-test-process TEMP_WORKSPACE as core-status.json — no stash/restore
// needed, the temp dir didn't exist before this test and gets rm'd on teardown.
const VOICE_STATE_PATH = join(TEMP_WORKSPACE, 'state', 'voice-state.json');

let server: Server;

async function fetchJson(path: string): Promise<any> {
	const res = await fetch(`http://localhost:${PORT}${path}`);
	return res.json();
}

describe('/sse-status + /mute-state — agent state plumbing (PR #418)', () => {
	before(async () => {
		// Write idle core-status into the per-test-process workspace temp dir.
		// No stash/restore needed — the temp dir didn't exist before this test
		// and gets rm'd on teardown.
		writeFileSync(CORE_STATUS_PATH, JSON.stringify({ status: 'idle', ts: Math.floor(Date.now() / 1000) }) + '\n');

		// Ensure absent before the in-process server reads it for the
		// "missing voice-state.json falls back to cache" subtest baseline.
		try { unlinkSync(VOICE_STATE_PATH); } catch { /* already gone */ }

		// SUTANDO_WORKSPACE env is read by resolveWorkspace() inside
		// startWebServer; set it BEFORE startWebServer() so the in-process
		// reader picks up the temp dir.
		process.env.SUTANDO_WORKSPACE = TEMP_WORKSPACE;

		server = startWebServer({ port: PORT, host: '127.0.0.1', wsPort: 19900 });
		await new Promise<void>((resolve, reject) => {
			if (server.listening) return resolve();
			server.once('listening', () => resolve());
			server.once('error', reject);
		});
	});

	after(async () => {
		await new Promise<void>((resolve) => {
			if (!server || !server.listening) return resolve();
			server.close(() => resolve());
		});
		// Remove the per-test-process workspace temp dir wholesale.
		cleanupTempWorkspace();
	});

	// Reset both tracks to idle before every subtest so order-dependence can't
	// flake on CI. The describe block shares one spawned web-client child for
	// all subtests, so the tool-track 'working' from one test would otherwise
	// linger into the next if the prior teardown raced with CI scheduling.
	// Issue #840: 2 unrelated PRs flaked on this same suite same day.
	beforeEach(async () => {
		await fetchJson('/mute-state?state=idle&source=tool');  // clear tool track
		await fetchJson('/mute-state?state=idle');               // clear browser track
		await delay(20);                                          // small flush window
	});

	it('default /sse-status returns state:"idle"', async () => {
		const body = await fetchJson('/sse-status');
		assert.equal(body.state, 'idle');
		assert.equal(body.muted, false);
		assert.equal(body.voiceConnected, false);
		assert.equal(typeof body.clients, 'number');
	});

	it('accepts all 5 valid agent states via the correct track', async () => {
		// Browser track (no source=tool): idle / listening / speaking only.
		for (const state of ['idle', 'listening', 'speaking']) {
			const body = await fetchJson(`/mute-state?state=${state}`);
			assert.equal(body.state, state, `POST state=${state} should echo back`);
			const status = await fetchJson('/sse-status');
			assert.equal(status.state, state, `/sse-status should reflect ${state}`);
		}
		// Tool track (source=tool): working / seeing.
		for (const state of ['working', 'seeing']) {
			const body = await fetchJson(`/mute-state?state=${state}&source=tool`);
			assert.equal(body.state, state, `POST state=${state}&source=tool should echo back`);
			const status = await fetchJson('/sse-status');
			assert.equal(status.state, state, `/sse-status should reflect ${state}`);
			// Clear tool track before next iteration so seeing's TTL
			// auto-revert doesn't race with the working assertion above.
			await fetchJson('/mute-state?state=idle&source=tool');
		}
	});

	it('clamps browser-sourced working/seeing to listening (tool track only)', async () => {
		// Prime browser track to a known value, and make sure tool track is idle
		await fetchJson('/mute-state?state=idle&source=tool');
		await fetchJson('/mute-state?state=listening');
		// Browser mis-posts working (without source=tool) — should clamp to listening
		const body = await fetchJson('/mute-state?state=working');
		assert.equal(body.state, 'listening', 'browser-sourced working must clamp to listening');
		// Same for seeing
		const body2 = await fetchJson('/mute-state?state=seeing');
		assert.equal(body2.state, 'listening', 'browser-sourced seeing must clamp to listening');
	});

	it('tool track takes precedence over browser track', async () => {
		await fetchJson('/mute-state?state=listening');
		await fetchJson('/mute-state?state=working&source=tool');
		const body = await fetchJson('/mute-state?state=listening'); // browser keeps pinging
		assert.equal(body.state, 'working', 'tool track must not be overwritten by browser');
		// Release tool track → falls through to browser track
		await fetchJson('/mute-state?state=idle&source=tool');
		const body2 = await fetchJson('/sse-status');
		assert.equal(body2.state, 'listening', 'clearing tool track reveals browser track');
	});

	it('rejects invalid agent state (keeps previous value)', async () => {
		// Set a known baseline
		await fetchJson('/mute-state?state=listening');
		// Try invalid
		const body = await fetchJson('/mute-state?state=bogus');
		assert.equal(body.state, 'listening', 'invalid value should not overwrite');
		const status = await fetchJson('/sse-status');
		assert.equal(status.state, 'listening');
	});

	it('mute/voice params continue working independently of state', async () => {
		const body = await fetchJson('/mute-state?muted=true&voice=true&state=working&source=tool');
		assert.equal(body.muted, true);
		assert.equal(body.voiceConnected, true);
		assert.equal(body.state, 'working');
	});

	// voice-state.json — authoritative over the browser-reported _voiceState cache.
	// Written by voice-agent on client connect/disconnect; web-client reads it in
	// /sse-status. Covers the regression from 2026-04-19 where a web-client restart
	// left voiceConnected=false in the cache even though voice was still connected.
	it('/sse-status uses voice-state.json as authoritative voiceConnected', async () => {
		// Prime the _voiceState cache to false (the desync baseline)
		await fetchJson('/mute-state?voice=false');
		let status = await fetchJson('/sse-status');
		assert.equal(status.voiceConnected, false, 'cache baseline');

		// Write a fresh connected=true file; readVoiceState should return true
		writeFileSync(VOICE_STATE_PATH, JSON.stringify({ connected: true, ts: Math.floor(Date.now() / 1000) }));
		status = await fetchJson('/sse-status');
		assert.equal(status.voiceConnected, true, 'fresh voice-state.json overrides cache');

		// Flip the file to disconnected; readVoiceState should return false
		writeFileSync(VOICE_STATE_PATH, JSON.stringify({ connected: false, ts: Math.floor(Date.now() / 1000) }));
		status = await fetchJson('/sse-status');
		assert.equal(status.voiceConnected, false, 'disconnected voice-state.json overrides cache');

		try { unlinkSync(VOICE_STATE_PATH); } catch {}
	});

	it('stale voice-state.json with connected=true falls back to cache', async () => {
		// Prime cache = false
		await fetchJson('/mute-state?voice=false');

		// Write a stale (>120s old) connected=true file — should NOT trust it
		const staleTs = Math.floor(Date.now() / 1000) - 200;
		writeFileSync(VOICE_STATE_PATH, JSON.stringify({ connected: true, ts: staleTs }));
		const status = await fetchJson('/sse-status');
		assert.equal(status.voiceConnected, false, 'stale connected=true should defer to _voiceState=false');

		// Flip cache to true and re-check — stale file still shouldn't matter,
		// we fall back to cache which is now true
		await fetchJson('/mute-state?voice=true');
		const status2 = await fetchJson('/sse-status');
		assert.equal(status2.voiceConnected, true, 'stale file falls back to cache=true');

		try { unlinkSync(VOICE_STATE_PATH); } catch {}
	});

	it('missing voice-state.json falls back to cache', async () => {
		try { unlinkSync(VOICE_STATE_PATH); } catch {}
		await fetchJson('/mute-state?voice=true');
		const status = await fetchJson('/sse-status');
		assert.equal(status.voiceConnected, true, 'no file → _voiceState cache wins');
	});
});
