import { describe, it, before, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _isVoiceTask } from '../src/task-bridge.js';

// Regression test for the archive-path mismatch Mini caught 2026-05-06:
// `archiveFile()` writes to `tasks/archive/<YYYY-MM>/<taskId>.txt`, but
// the original `_isVoiceTask()` only checked `tasks/archive/<taskId>.txt`
// (no YYYY-MM subdir). After archive, voice-task detection silently
// returned false even when the archived task was voice-originated. Fix:
// derive YYYY-MM from the taskId's epoch-ms suffix and check that path.

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const TASK_DIR = join(REPO_ROOT, 'tasks');
const VOICE_BODY = 'channel_id: local-voice\nsource: voice\n';
const NON_VOICE_BODY = 'channel_id: 1234567890\nsource: discord\n';

describe('_isVoiceTask — archive-path resolution', () => {
	const created: string[] = [];

	afterEach(() => {
		for (const p of created.splice(0)) {
			try { rmSync(p, { force: true }); } catch { /* already gone */ }
		}
	});

	it('finds voice task in archive/<YYYY-MM>/ (the path archiveFile actually writes)', () => {
		const id = `task-1778120000000`; // ms = 2026-05-06T19:13:20Z → YYYY-MM = 2026-05
		const ym = '2026-05';
		const dir = join(TASK_DIR, 'archive', ym);
		const path = join(dir, `${id}.txt`);
		mkdirSync(dir, { recursive: true });
		writeFileSync(path, VOICE_BODY);
		created.push(path);
		assert.equal(_isVoiceTask(id), true);
	});

	it('returns false for non-voice archived task', () => {
		const id = `task-1778120000001`;
		const ym = '2026-05';
		const dir = join(TASK_DIR, 'archive', ym);
		const path = join(dir, `${id}.txt`);
		mkdirSync(dir, { recursive: true });
		writeFileSync(path, NON_VOICE_BODY);
		created.push(path);
		assert.equal(_isVoiceTask(id), false);
	});

	it('still finds voice task at legacy flat archive/ path (pre-YYYY-MM split)', () => {
		const id = `task-1778120000002`;
		const dir = join(TASK_DIR, 'archive');
		const path = join(dir, `${id}.txt`);
		mkdirSync(dir, { recursive: true });
		writeFileSync(path, VOICE_BODY);
		created.push(path);
		assert.equal(_isVoiceTask(id), true);
	});

	it('returns false on missing taskId (no candidates exist)', () => {
		assert.equal(_isVoiceTask('task-9999999999999'), false);
	});

	it('returns false on malformed taskId (no epoch-ms suffix to derive YYYY-MM)', () => {
		// Should NOT throw — just falls back to legacy candidates which are missing.
		assert.equal(_isVoiceTask('not-a-real-task-id'), false);
	});

	it('finds voice task in tasks/ (live, not archived) — pre-existing path', () => {
		const id = `task-1778120000003`;
		const path = join(TASK_DIR, `${id}.txt`);
		writeFileSync(path, VOICE_BODY);
		created.push(path);
		assert.equal(_isVoiceTask(id), true);
	});
});
