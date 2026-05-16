import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir, hostname } from 'node:os';
import { join } from 'node:path';
import { tenantId, tenantPath, __resetForTests } from '../src/tenant.ts';

const localHost = hostname().split('.')[0];

// Snapshot env vars; restore on each cleanup so tests stay isolated.
let savedEnv: Record<string, string | undefined>;

function snapshotEnv(): Record<string, string | undefined> {
	return {
		SUTANDO_TENANT_ID: process.env.SUTANDO_TENANT_ID,
		SUTANDO_PRIVATE_DIR: process.env.SUTANDO_PRIVATE_DIR,
		SUTANDO_WORKSPACE: process.env.SUTANDO_WORKSPACE,
	};
}

function restoreEnv(snap: Record<string, string | undefined>): void {
	for (const k of Object.keys(snap)) {
		if (snap[k] === undefined) delete process.env[k];
		else process.env[k] = snap[k];
	}
}

describe('tenantId resolution', () => {
	let tempDir: string;

	beforeEach(() => {
		savedEnv = snapshotEnv();
		__resetForTests();
		tempDir = mkdtempSync(join(tmpdir(), 'sutando-tenant-'));
		delete process.env.SUTANDO_TENANT_ID;
		delete process.env.SUTANDO_PRIVATE_DIR;
		process.env.SUTANDO_WORKSPACE = tempDir;
	});

	afterEach(() => {
		restoreEnv(savedEnv);
		__resetForTests();
		try { rmSync(tempDir, { recursive: true, force: true }); } catch {}
	});

	it('falls back to "default" with no env, no state file', () => {
		assert.equal(tenantId(), 'default');
	});

	it('reads SUTANDO_TENANT_ID env var when set + valid', () => {
		process.env.SUTANDO_TENANT_ID = 'acme';
		assert.equal(tenantId(), 'acme');
	});

	it('rejects invalid SUTANDO_TENANT_ID + falls back', () => {
		process.env.SUTANDO_TENANT_ID = 'BAD ID!';
		assert.equal(tenantId(), 'default');
	});

	it('reads state/tenant.json when env unset', () => {
		mkdirSync(join(tempDir, 'state'), { recursive: true });
		writeFileSync(join(tempDir, 'state', 'tenant.json'), JSON.stringify({ id: 'beta-tenant' }));
		assert.equal(tenantId(), 'beta-tenant');
	});

	it('env wins over state file', () => {
		mkdirSync(join(tempDir, 'state'), { recursive: true });
		writeFileSync(join(tempDir, 'state', 'tenant.json'), JSON.stringify({ id: 'from-file' }));
		process.env.SUTANDO_TENANT_ID = 'from-env';
		assert.equal(tenantId(), 'from-env');
	});

	it('corrupt state/tenant.json → fall back to default', () => {
		mkdirSync(join(tempDir, 'state'), { recursive: true });
		writeFileSync(join(tempDir, 'state', 'tenant.json'), 'not json {{');
		assert.equal(tenantId(), 'default');
	});

	it('cached after first call', () => {
		process.env.SUTANDO_TENANT_ID = 'first';
		assert.equal(tenantId(), 'first');
		// Mutate env after caching; result unchanged until reset.
		process.env.SUTANDO_TENANT_ID = 'second';
		assert.equal(tenantId(), 'first');
		__resetForTests();
		assert.equal(tenantId(), 'second');
	});
});

describe('tenantPath resolution', () => {
	let tempPrivate: string;
	let tempWs: string;

	beforeEach(() => {
		savedEnv = snapshotEnv();
		__resetForTests();
		tempPrivate = mkdtempSync(join(tmpdir(), 'sutando-priv-'));
		tempWs = mkdtempSync(join(tmpdir(), 'sutando-ws-'));
		process.env.SUTANDO_PRIVATE_DIR = tempPrivate;
		process.env.SUTANDO_WORKSPACE = tempWs;
		delete process.env.SUTANDO_TENANT_ID;
	});

	afterEach(() => {
		restoreEnv(savedEnv);
		__resetForTests();
		try { rmSync(tempPrivate, { recursive: true, force: true }); } catch {}
		try { rmSync(tempWs, { recursive: true, force: true }); } catch {}
	});

	it('shared scope: returns tenant-<id>/<file> when private dir + tenant present', () => {
		process.env.SUTANDO_TENANT_ID = 'acme';
		const tenantDir = join(tempPrivate, 'tenant-acme');
		mkdirSync(tenantDir, { recursive: true });
		writeFileSync(join(tenantDir, 'build_log.md'), 'acme log');
		const p = tenantPath('build_log.md', 'shared');
		assert.equal(p, join(tenantDir, 'build_log.md'));
	});

	it('device scope: returns tenant-<id>/machine-<host>/<file>', () => {
		process.env.SUTANDO_TENANT_ID = 'acme';
		const tenantDir = join(tempPrivate, 'tenant-acme');
		const machineDir = join(tenantDir, 'machine-' + localHost);
		mkdirSync(machineDir, { recursive: true });
		writeFileSync(join(machineDir, 'pending-questions.md'), 'q');
		const p = tenantPath('pending-questions.md', 'device');
		assert.equal(p, join(machineDir, 'pending-questions.md'));
	});

	it('legacy fallback: default tenant + pre-migration files at top level', () => {
		// Old install: $SUTANDO_PRIVATE_DIR/notes.md (no tenant prefix).
		writeFileSync(join(tempPrivate, 'notes.md'), 'legacy notes');
		const p = tenantPath('notes.md', 'shared');
		assert.equal(p, join(tempPrivate, 'notes.md'));
	});

	it('legacy fallback for device scope: machine-<host>/<file> at top level', () => {
		const legacyMachine = join(tempPrivate, 'machine-' + localHost);
		mkdirSync(legacyMachine, { recursive: true });
		writeFileSync(join(legacyMachine, 'stand-identity.json'), '{}');
		const p = tenantPath('stand-identity.json', 'device');
		assert.equal(p, join(legacyMachine, 'stand-identity.json'));
	});

	it('workspace fallback when nothing exists in private dir', () => {
		writeFileSync(join(tempWs, 'README.md'), 'in workspace');
		const p = tenantPath('README.md', 'shared');
		assert.equal(p, join(tempWs, 'README.md'));
	});

	it('non-existent target returns preferred private path for graceful caller existsSync', () => {
		process.env.SUTANDO_TENANT_ID = 'acme';
		const p = tenantPath('does-not-exist.md', 'shared');
		assert.equal(p, join(tempPrivate, 'tenant-acme', 'does-not-exist.md'));
	});

	it('no SUTANDO_PRIVATE_DIR → workspace path always', () => {
		delete process.env.SUTANDO_PRIVATE_DIR;
		writeFileSync(join(tempWs, 'x.md'), 'x');
		assert.equal(tenantPath('x.md', 'shared'), join(tempWs, 'x.md'));
	});
});
