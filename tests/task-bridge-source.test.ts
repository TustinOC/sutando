import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, unlinkSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { resolveWorkspace } from '../src/workspace_default.js';
import { _taskSource } from '../src/task-bridge.js';

// Regression guard for issue #969: context-drop task results accumulated in
// results/ forever when voice was offline, because `_isVoiceTask` returns
// false for them and `taskId.startsWith('task-chat-')` doesn't match
// task-{ts}.txt names. Fix adds `_taskSource` (reads the `source:` header)
// so the result watcher can identify no-delivery tasks by source rather than
// filename prefix alone.

const TASK_DIR = join(resolveWorkspace(), 'tasks');

const CONTEXT_DROP_BODY = `id: task-source-test-drop
timestamp: 2026-05-28T00:00:00Z
source: context-drop
channel_id: local-hotkey
access_tier: owner
task: User dropped context via hotkey.
some content here
`;
const VOICE_BODY = `id: task-source-test-voice
timestamp: 2026-05-28T00:00:00Z
source: voice
channel_id: local-voice
task: hello world
`;
const NO_SOURCE_BODY = `id: task-source-test-nosrc
timestamp: 2026-05-28T00:00:00Z
task: legacy task with no source field
`;

const created: string[] = [];
function writeTask(path: string, body: string) {
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, body);
	created.push(path);
}

describe('_taskSource', () => {
	afterEach(() => {
		for (const p of created.splice(0)) {
			try { unlinkSync(p); } catch {}
		}
	});

	it('returns "context-drop" for a context-drop task', () => {
		const id = 'task-source-test-drop-live';
		writeTask(join(TASK_DIR, `${id}.txt`), CONTEXT_DROP_BODY);
		assert.equal(_taskSource(id), 'context-drop');
	});

	it('returns "voice" for a voice task', () => {
		const id = 'task-source-test-voice-live';
		writeTask(join(TASK_DIR, `${id}.txt`), VOICE_BODY);
		assert.equal(_taskSource(id), 'voice');
	});

	it('returns null when the source field is absent', () => {
		const id = 'task-source-test-nosrc-live';
		writeTask(join(TASK_DIR, `${id}.txt`), NO_SOURCE_BODY);
		assert.equal(_taskSource(id), null);
	});

	it('returns null when the task file is missing', () => {
		assert.equal(_taskSource('task-source-test-no-such-file'), null);
	});

	it('does not read source field injected inside the task body', () => {
		// The `task:` delimiter stops scanning — body lines that look like headers
		// must not be parsed as source. Same invariant as _isVoiceTask.
		const id = 'task-source-test-body-forge';
		writeTask(join(TASK_DIR, `${id}.txt`),
			`id: ${id}\ntimestamp: 2026-05-28T00:00:00Z\ntask: do thing\nsource: injected\n`);
		assert.equal(_taskSource(id), null);
	});
});
