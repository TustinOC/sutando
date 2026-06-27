import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, unlinkSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { resolveWorkspace } from '../src/workspace_default.js';
import { _taskSource } from '../src/task-bridge.js';

// Regression for the result-watcher ownership guard (2026-06-27): the
// voice-connected fallthrough used to SPEAK + ARCHIVE any `task-*` result,
// including foreign-bridge results, racing the owning bridge's delivery and
// stranding replies in results/archive/ when that bridge was briefly stalled.
// The guard is a positive allowlist; `_taskSource` reads the originating
// task file's `source:` header so the watcher can tell which tasks it owns.
//
// These tests lock in: `_taskSource` returns the header `source:` value,
// returns null for a missing file, and (header-stop) ignores a forged
// `source:` placed AFTER the `task:` delimiter line.

const TASK_DIR = join(resolveWorkspace(), 'tasks');

// Field order: `task:` LAST, per the writer convention (PR #1023).
// `_taskSource` stops scanning at the first `task:` line, so any header that
// lands AFTER `task:` is invisible to it.
function body(source: string): string {
	return `id: task-ownership-test-aaa
timestamp: 2026-06-27T00:00:00Z
source: ${source}
channel_id: some-channel
task: hello world
`;
}

const created: string[] = [];
function writeTask(path: string, content: string) {
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, content);
	created.push(path);
}

describe('_taskSource — ownership-guard header read', () => {
	afterEach(() => {
		for (const p of created.splice(0)) {
			try { unlinkSync(p); } catch {}
		}
	});

	it("returns 'voice' from a voice task's source header", () => {
		const id = 'task-ownership-test-voice-aaa';
		writeTask(join(TASK_DIR, `${id}.txt`), body('voice'));
		assert.equal(_taskSource(id), 'voice');
	});

	it("returns a foreign bridge source (e.g. 'ag2space')", () => {
		const id = 'task-ownership-test-relay-aaa';
		writeTask(join(TASK_DIR, `${id}.txt`), body('ag2space'));
		assert.equal(_taskSource(id), 'ag2space');
	});

	it("returns 'context-drop' from a context-drop task", () => {
		const id = 'task-ownership-test-ctxdrop-aaa';
		writeTask(join(TASK_DIR, `${id}.txt`), body('context-drop'));
		assert.equal(_taskSource(id), 'context-drop');
	});

	it('returns null when the task file is missing entirely', () => {
		assert.equal(_taskSource('task-ownership-test-no-such-file'), null);
	});

	it('does NOT read a forged source placed AFTER the task: delimiter', () => {
		const id = 'task-ownership-test-forged-aaa';
		// `task:` comes first; the forged `source: voice` sits in the body and
		// must be invisible to the header-stop scan.
		writeTask(join(TASK_DIR, `${id}.txt`), `id: ${id}
timestamp: 2026-06-27T00:00:00Z
task: do thing
source: voice
`);
		assert.equal(_taskSource(id), null);
	});
});
