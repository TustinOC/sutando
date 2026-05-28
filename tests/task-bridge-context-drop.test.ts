/**
 * Regression guard for context-drop result archival (closes #969).
 *
 * Pre-fix: the offline branch in the result watcher archived task-chat-* and
 * forwarded voice tasks, but context-drop results (source: context-drop /
 * source-less legacy Swift tasks) fell through to `continue` and accumulated
 * in results/ indefinitely.
 *
 * This test locks in _isContextDropTask detection across all three task-file
 * locations: live, processed, and month-partitioned archive.
 */
import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, unlinkSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { resolveWorkspace } from '../src/workspace_default.js';
import { _isContextDropTask } from '../src/task-bridge.js';

const TASK_DIR = join(resolveWorkspace(), 'tasks');

const CONTEXT_DROP_BODY = `id: task-ctxdrop-test-aaa
timestamp: 2026-05-28T00:00:00Z
source: context-drop
channel_id: local-hotkey
access_tier: owner
task: User dropped context via hotkey. Process this:
some content
`;

const LEGACY_SWIFT_BODY = `id: task-ctxdrop-test-bbb
timestamp: 2026-05-28T00:00:00Z
task: User dropped context via hotkey. Process this:
some content
`;

const VOICE_BODY = `id: task-ctxdrop-test-ccc
timestamp: 2026-05-28T00:00:00Z
source: voice
channel_id: local-voice
task: hello world
`;

const DISCORD_BODY = `id: task-ctxdrop-test-ddd
timestamp: 2026-05-28T00:00:00Z
source: discord
channel_id: 1490906927675474030
task: hello world
`;

const created: string[] = [];
function writeTask(path: string, body: string) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, body);
  created.push(path);
}

describe('_isContextDropTask', () => {
  afterEach(() => {
    for (const p of created.splice(0)) {
      try { unlinkSync(p); } catch {}
    }
  });

  it('returns true for source: context-drop in live tasks/', () => {
    const id = 'task-ctxdrop-live-aaa';
    writeTask(join(TASK_DIR, `${id}.txt`), CONTEXT_DROP_BODY);
    assert.equal(_isContextDropTask(id), true);
  });

  it('returns true for legacy Swift task (no source field)', () => {
    const id = 'task-ctxdrop-legacy-bbb';
    writeTask(join(TASK_DIR, `${id}.txt`), LEGACY_SWIFT_BODY);
    assert.equal(_isContextDropTask(id), true);
  });

  it('returns true for context-drop task in month-partitioned archive', () => {
    const id = 'task-ctxdrop-archive-ccc';
    const ym = new Date().toISOString().slice(0, 7);
    writeTask(join(TASK_DIR, 'archive', ym, `${id}.txt`), CONTEXT_DROP_BODY);
    assert.equal(_isContextDropTask(id), true);
  });

  it('returns false for voice task', () => {
    const id = 'task-ctxdrop-voice-ddd';
    writeTask(join(TASK_DIR, `${id}.txt`), VOICE_BODY);
    assert.equal(_isContextDropTask(id), false);
  });

  it('returns false for discord task', () => {
    const id = 'task-ctxdrop-discord-eee';
    writeTask(join(TASK_DIR, `${id}.txt`), DISCORD_BODY);
    assert.equal(_isContextDropTask(id), false);
  });

  it('returns false when task file does not exist', () => {
    assert.equal(_isContextDropTask('task-ctxdrop-nonexistent-zzz'), false);
  });
});
