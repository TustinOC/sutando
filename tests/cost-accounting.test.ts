import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
	record,
	rollup,
	ledgerExists,
	__ledgerPathForTests,
	__resetLedgerForTests,
} from '../src/cost-accounting.ts';
import { __resetForTests as resetTenant } from '../src/tenant.ts';

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

describe('cost-accounting ledger', () => {
	let tempPrivate: string;
	let tempWs: string;

	beforeEach(() => {
		savedEnv = snapshotEnv();
		resetTenant();
		tempPrivate = mkdtempSync(join(tmpdir(), 'sutando-cost-priv-'));
		tempWs = mkdtempSync(join(tmpdir(), 'sutando-cost-ws-'));
		process.env.SUTANDO_PRIVATE_DIR = tempPrivate;
		process.env.SUTANDO_WORKSPACE = tempWs;
		delete process.env.SUTANDO_TENANT_ID;
		__resetLedgerForTests();
	});

	afterEach(() => {
		__resetLedgerForTests();
		restoreEnv(savedEnv);
		resetTenant();
		try { rmSync(tempPrivate, { recursive: true, force: true }); } catch {}
		try { rmSync(tempWs, { recursive: true, force: true }); } catch {}
	});

	it('records a single token event + ledger appears', () => {
		assert.equal(ledgerExists(), false);
		record({
			source: 'voice-agent',
			kind: 'token',
			amount: 100,
			unit: 'in_tokens',
			model: 'gemini-2.5-flash',
		});
		assert.equal(ledgerExists(), true);
		const r = rollup();
		assert.equal(r.event_count, 1);
		assert.equal(r.totals.in_tokens, 100);
		assert.equal(r.by_model['gemini-2.5-flash'].in_tokens, 100);
	});

	it('aggregates in + out tokens + tool_calls + requests separately', () => {
		record({ source: 'voice', kind: 'token', amount: 500, unit: 'in_tokens', model: 'a' });
		record({ source: 'voice', kind: 'token', amount: 50, unit: 'out_tokens', model: 'a' });
		record({ source: 'voice', kind: 'tool', amount: 1, unit: 'tool_call', tool: 'triage_email' });
		record({ source: 'voice', kind: 'tool', amount: 1, unit: 'tool_call', tool: 'triage_email' });
		record({ source: 'voice', kind: 'api_call', amount: 1, unit: 'request', model: 'a' });
		const r = rollup();
		assert.equal(r.event_count, 5);
		assert.equal(r.totals.in_tokens, 500);
		assert.equal(r.totals.out_tokens, 50);
		assert.equal(r.totals.tool_calls, 2);
		assert.equal(r.totals.requests, 1);
		assert.equal(r.by_tool['triage_email'], 2);
		assert.equal(r.by_model['a'].in_tokens, 500);
		assert.equal(r.by_model['a'].out_tokens, 50);
		assert.equal(r.by_model['a'].requests, 1);
	});

	it('rollup is per-tenant — events for one tenant don\'t leak into another', () => {
		process.env.SUTANDO_TENANT_ID = 'acme';
		resetTenant();
		record({ source: 's', kind: 'token', amount: 100, unit: 'in_tokens', model: 'm' });
		// Switch tenants.
		process.env.SUTANDO_TENANT_ID = 'beta';
		resetTenant();
		record({ source: 's', kind: 'token', amount: 200, unit: 'in_tokens', model: 'm' });
		assert.equal(rollup({ tenant_id: 'acme' }).totals.in_tokens, 100);
		assert.equal(rollup({ tenant_id: 'beta' }).totals.in_tokens, 200);
	});

	it('time-range filter respects from/to', () => {
		// Three events with synthetic but real timestamps.
		const path = __ledgerPathForTests();
		// We can't easily inject fake timestamps via `record()` because it
		// stamps with `new Date()`; instead write three events with real
		// timestamps spaced over the test.
		record({ source: 's', kind: 'token', amount: 1, unit: 'in_tokens', model: 'm' });
		const t1 = new Date().toISOString();
		record({ source: 's', kind: 'token', amount: 1, unit: 'in_tokens', model: 'm' });
		const t2 = new Date().toISOString();
		record({ source: 's', kind: 'token', amount: 1, unit: 'in_tokens', model: 'm' });
		const all = rollup();
		assert.equal(all.event_count, 3);
		// From t2: should include only events at or after t2 — at minimum the
		// third event.
		const fromT2 = rollup({ from: t2 });
		assert.ok(fromT2.event_count >= 1 && fromT2.event_count <= 2);
		// Sanity: file path exists.
		assert.ok(path.endsWith('.jsonl'));
	});

	it('by_device fanout', () => {
		record({ source: 's', device_id: 'mac', kind: 'token', amount: 100, unit: 'in_tokens' });
		record({ source: 's', device_id: 'mac', kind: 'token', amount: 50, unit: 'out_tokens' });
		record({ source: 's', device_id: 'glasses', kind: 'tool', amount: 1, unit: 'tool_call', tool: 't' });
		const r = rollup();
		assert.equal(r.by_device['mac'], 150);
		assert.equal(r.by_device['glasses'], 1);
	});

	it('rollup with no ledger returns zeros', () => {
		const r = rollup();
		assert.equal(r.event_count, 0);
		assert.equal(r.totals.in_tokens, 0);
		assert.deepEqual(r.by_model, {});
		assert.deepEqual(r.by_tool, {});
		assert.deepEqual(r.by_device, {});
	});

	it('explicit tenant_id on record routes to that tenant', () => {
		record({ source: 's', tenant_id: 'override', kind: 'token', amount: 999, unit: 'in_tokens' });
		assert.equal(rollup({ tenant_id: 'override' }).totals.in_tokens, 999);
		assert.equal(rollup({ tenant_id: 'default' }).totals.in_tokens, 0);
	});

	it('corrupt jsonl line is skipped (doesn\'t throw)', () => {
		const path = __ledgerPathForTests();
		// Write one valid + one corrupt line via record() then append corrupt.
		record({ source: 's', kind: 'token', amount: 10, unit: 'in_tokens' });
		// Append a corrupt line directly.
		appendFileSync(path, 'this is not json\n');
		record({ source: 's', kind: 'token', amount: 20, unit: 'in_tokens' });
		const r = rollup();
		assert.equal(r.event_count, 2);
		assert.equal(r.totals.in_tokens, 30);
	});
});
