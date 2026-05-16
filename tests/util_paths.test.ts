import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir, hostname } from 'node:os';
import { join } from 'node:path';
import { personalPath, sharedPersonalPath } from '../src/util_paths.ts';
import { __resetForTests as resetTenant } from '../src/tenant.ts';

// Regression coverage for the personalPath / sharedPersonalPath →
// tenantPath delegation. Verifies that:
//
// 1. Pre-migration single-tenant installs (no tenant-default/, no
//    SUTANDO_TENANT_ID) keep resolving to the legacy
//    `$SUTANDO_PRIVATE_DIR/machine-<host>/<file>` shape.
// 2. With a tenant-default/ provisioned, paths resolve under it.
// 3. The stand-avatar.png assets/ fallback is preserved.
// 4. Workspace fallback when SUTANDO_PRIVATE_DIR is unset.

const localHost = hostname().split('.')[0];

let savedEnv: Record<string, string | undefined>;

function snap(): Record<string, string | undefined> {
	return {
		SUTANDO_TENANT_ID: process.env.SUTANDO_TENANT_ID,
		SUTANDO_PRIVATE_DIR: process.env.SUTANDO_PRIVATE_DIR,
		SUTANDO_WORKSPACE: process.env.SUTANDO_WORKSPACE,
	};
}

function restore(s: Record<string, string | undefined>): void {
	for (const k of Object.keys(s)) {
		if (s[k] === undefined) delete process.env[k];
		else process.env[k] = s[k];
	}
}

describe('personalPath / sharedPersonalPath (delegate to tenantPath)', () => {
	let priv: string;
	let ws: string;

	beforeEach(() => {
		savedEnv = snap();
		resetTenant();
		priv = mkdtempSync(join(tmpdir(), 'sutando-paths-priv-'));
		ws = mkdtempSync(join(tmpdir(), 'sutando-paths-ws-'));
		process.env.SUTANDO_PRIVATE_DIR = priv;
		process.env.SUTANDO_WORKSPACE = ws;
		delete process.env.SUTANDO_TENANT_ID;
	});

	afterEach(() => {
		restore(savedEnv);
		resetTenant();
		try { rmSync(priv, { recursive: true, force: true }); } catch {}
		try { rmSync(ws, { recursive: true, force: true }); } catch {}
	});

	it('legacy single-tenant: personalPath resolves machine-<host>/<file> at top level when tenant-default/ absent', () => {
		const legacyMachine = join(priv, `machine-${localHost}`);
		mkdirSync(legacyMachine, { recursive: true });
		writeFileSync(join(legacyMachine, 'stand-identity.json'), '{}');
		assert.equal(personalPath('stand-identity.json'), join(legacyMachine, 'stand-identity.json'));
	});

	it('legacy single-tenant: sharedPersonalPath resolves top-level file when tenant-default/ absent', () => {
		writeFileSync(join(priv, 'build_log.md'), 'log');
		assert.equal(sharedPersonalPath('build_log.md'), join(priv, 'build_log.md'));
	});

	it('migrated single-tenant: personalPath resolves under tenant-default/machine-<host>/', () => {
		const tenantMachine = join(priv, 'tenant-default', `machine-${localHost}`);
		mkdirSync(tenantMachine, { recursive: true });
		writeFileSync(join(tenantMachine, 'stand-identity.json'), '{}');
		assert.equal(personalPath('stand-identity.json'), join(tenantMachine, 'stand-identity.json'));
	});

	it('migrated single-tenant: sharedPersonalPath resolves under tenant-default/', () => {
		const tenantDir = join(priv, 'tenant-default');
		mkdirSync(tenantDir, { recursive: true });
		writeFileSync(join(tenantDir, 'build_log.md'), 'log');
		assert.equal(sharedPersonalPath('build_log.md'), join(tenantDir, 'build_log.md'));
	});

	it('stand-avatar.png assets/ fallback preserved', () => {
		mkdirSync(join(ws, 'assets'), { recursive: true });
		writeFileSync(join(ws, 'assets', 'stand-avatar.png'), 'png');
		assert.equal(personalPath('stand-avatar.png', ws), join(ws, 'assets', 'stand-avatar.png'));
	});

	it('no SUTANDO_PRIVATE_DIR: workspace fallback', () => {
		delete process.env.SUTANDO_PRIVATE_DIR;
		writeFileSync(join(ws, 'config.json'), '{}');
		assert.equal(sharedPersonalPath('config.json'), join(ws, 'config.json'));
	});

	it('non-default tenant: paths route under tenant-<id>/', () => {
		process.env.SUTANDO_TENANT_ID = 'acme';
		resetTenant();
		const tenantMachine = join(priv, 'tenant-acme', `machine-${localHost}`);
		mkdirSync(tenantMachine, { recursive: true });
		writeFileSync(join(tenantMachine, 'pending-questions.md'), 'q');
		assert.equal(personalPath('pending-questions.md'), join(tenantMachine, 'pending-questions.md'));
	});

	it('non-existent target returns preferred private path so caller existsSync fails gracefully', () => {
		// Default tenant + nothing exists yet → preferred path is
		// tenant-default/<scope>/file (the canonical write target).
		const p = sharedPersonalPath('new-file.md');
		assert.equal(p, join(priv, 'tenant-default', 'new-file.md'));
	});
});
