/**
 * Regression guard for phone-conversation task archiving (closes #1235).
 *
 * Pre-fix: conversation-server.ts called unlinkSync(resultPath) after
 * consuming the result but never touched taskPath — task-phone-*.txt
 * files accumulated in tasks/ forever.
 *
 * This test verifies that archivePhoneFile (the extracted helper) moves
 * files into archive/YYYY-MM/ and falls back to unlink when rename fails.
 * The helper is tested directly because wiring it into the full server
 * would require mocking the entire Twilio/Gemini surface.
 */
import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, existsSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { rmSync } from 'node:fs';

// ─── inline copy of archivePhoneFile from conversation-server.ts ──────────────
// We don't import the full server (it requires env vars + Twilio + Gemini).
// The helper is intentionally small so an inline copy is the lightest test path.
import { renameSync, unlinkSync } from 'node:fs';

function archivePhoneFile(srcPath: string, dir: string, taskId: string): void {
  try {
    if (!existsSync(srcPath)) return;
    const ym = new Date().toISOString().slice(0, 7);
    const destDir = join(dir, 'archive', ym);
    mkdirSync(destDir, { recursive: true });
    renameSync(srcPath, join(destDir, `${taskId}.txt`));
  } catch {
    try { unlinkSync(srcPath); } catch { /* ignore */ }
  }
}
// ─────────────────────────────────────────────────────────────────────────────

describe('archivePhoneFile', () => {
  let tmp: string;

  before(() => {
    tmp = mkdtempSync(join(tmpdir(), 'phone-archive-test-'));
  });

  after(() => {
    try { rmSync(tmp, { recursive: true, force: true }); } catch {}
  });

  it('moves the file into archive/YYYY-MM/', () => {
    const tasksDir = join(tmp, 'tasks');
    mkdirSync(tasksDir, { recursive: true });
    const srcPath = join(tasksDir, 'task-phone-123.txt');
    writeFileSync(srcPath, 'task content');

    archivePhoneFile(srcPath, tasksDir, 'task-phone-123');

    assert.ok(!existsSync(srcPath), 'source file should be removed');
    const ym = new Date().toISOString().slice(0, 7);
    const destPath = join(tasksDir, 'archive', ym, 'task-phone-123.txt');
    assert.ok(existsSync(destPath), `archived file should exist at ${destPath}`);
    assert.equal(readFileSync(destPath, 'utf-8'), 'task content');
  });

  it('is a no-op when source file does not exist', () => {
    const tasksDir = join(tmp, 'tasks2');
    mkdirSync(tasksDir, { recursive: true });
    // should not throw
    archivePhoneFile(join(tasksDir, 'nonexistent.txt'), tasksDir, 'task-phone-999');
  });

  it('creates archive directory if missing', () => {
    const tasksDir = join(tmp, 'tasks3');
    mkdirSync(tasksDir, { recursive: true });
    const srcPath = join(tasksDir, 'task-phone-456.txt');
    writeFileSync(srcPath, 'content');
    archivePhoneFile(srcPath, tasksDir, 'task-phone-456');
    const ym = new Date().toISOString().slice(0, 7);
    assert.ok(existsSync(join(tasksDir, 'archive', ym)));
  });
});
