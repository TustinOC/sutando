/**
 * Sutando — Cost Accounting
 *
 * Implements §5 Next: "Cost accounting. Per-tenant + per-device token +
 * tool usage metering. Required to price anything; useful even single-
 * tenant."
 *
 * Design shape: append-only JSONL ledger per tenant. Same pattern as
 * `src/learn-collector.py`'s `state/learning-<instance>.jsonl` — atomic
 * writes via `os.O_APPEND`, single-line events under PIPE_BUF (~512 on
 * macOS / 4096 on Linux), no in-process aggregation that could be lost
 * to a crash. Downstream summarizers roll up usage when the owner needs
 * a report.
 *
 * Event shape (JSON line):
 *   { ts, tenant_id, device_id?, source, kind, amount, unit, model?,
 *     tool?, request_id? }
 *
 *   ts:          ISO-8601 UTC timestamp.
 *   tenant_id:   from tenant.ts. Routes the event to its tenant ledger.
 *   device_id:   optional — the DeviceSession that originated the call.
 *   source:      free-form ("voice-agent", "mentra-bridge", "skill:gws-gmail").
 *   kind:        "token" | "tool" | "api_call".
 *   amount:      numeric (tokens, ms, calls).
 *   unit:        "in_tokens" | "out_tokens" | "tool_call" | "ms" | "request".
 *   model:       optional model name when relevant (gemini-2.5-flash, etc.).
 *   tool:        optional tool name for kind="tool".
 *   request_id:  optional client-side correlator.
 *
 * The ledger is owner-readable (the only data is the owner's own usage),
 * gitignored, and survives across processes via append-only writes.
 * Aggregation lives in a separate summarizer (this PR does not ship one).
 *
 * Not in scope:
 *   - Aggregation / reporting UI — that's the §5 Next "Audit UI" item.
 *   - Quota-tracker integration — quota-tracker measures the Anthropic
 *     window separately; this is for Sutando's own per-tenant usage
 *     view. Both can coexist.
 *   - Cross-tenant rollup — single-tenant today; cross-tenant aggregation
 *     waits until a second tenant exists.
 */

import { openSync, writeSync, closeSync, existsSync, readFileSync, mkdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tenantId, tenantPath } from './tenant.js';

export type UsageKind = 'token' | 'tool' | 'api_call';

export interface UsageEvent {
	tenant_id: string;
	device_id?: string;
	source: string;
	kind: UsageKind;
	amount: number;
	unit: 'in_tokens' | 'out_tokens' | 'tool_call' | 'ms' | 'request';
	model?: string;
	tool?: string;
	request_id?: string;
}

function nowIso(): string {
	// Keep millisecond precision — downstream rollup queries filter by
	// timestamp range, and second-precision ties would collapse multiple
	// events at the same second into an ambiguous boundary.
	return new Date().toISOString();
}

function ledgerPath(forTenant: string): string {
	// Pass `forTenant` explicitly so cross-tenant rollup queries route to
	// the right tenant directory regardless of the current cached id. The
	// 'shared' scope keeps the ledger fleet-wide within a tenant — usage
	// from any machine in the tenant rolls up into the same JSONL.
	return tenantPath(`cost-accounting/usage-${forTenant}.jsonl`, 'shared', undefined, forTenant);
}

// Memoize mkdir targets so the per-record `mkdirSync` doesn't fire a
// redundant syscall on every emit. Recursive mkdir is cheap but
// non-zero; record() is called per tool/session-end, so the cache
// saves N-1 syscalls per ledger across the lifetime of the process.
const _ensuredDirs = new Set<string>();
function ensureDir(path: string): void {
	const dir = dirname(path);
	if (_ensuredDirs.has(dir)) return;
	try { mkdirSync(dir, { recursive: true }); _ensuredDirs.add(dir); } catch {}
}

/** Emit one usage event. Atomic single write — `O_APPEND` guarantees
 *  the kernel orders the bytes correctly even under concurrent writers
 *  (mirrors `learn-collector.py`'s ledger discipline). */
export function record(event: Omit<UsageEvent, 'tenant_id'> & { tenant_id?: string }): void {
	const tid = event.tenant_id ?? tenantId();
	const full: UsageEvent = { ...event, tenant_id: tid };
	const line = JSON.stringify({ ts: nowIso(), ...full }) + '\n';
	const path = ledgerPath(tid);
	ensureDir(path);
	const fd = openSync(path, 'a', 0o644);
	try { writeSync(fd, line); }
	finally { closeSync(fd); }
}

export interface UsageRollup {
	tenant_id: string;
	from?: string;
	to?: string;
	totals: {
		in_tokens: number;
		out_tokens: number;
		tool_calls: number;
		requests: number;
		ms: number;
	};
	by_model: Record<string, { in_tokens: number; out_tokens: number; requests: number; ms: number }>;
	by_tool: Record<string, number>;
	by_device: Record<string, number>;
	event_count: number;
}

/** Read + aggregate the ledger for a tenant over an optional date range.
 *  This is the read path; for owner reports, daily summaries, etc.
 *
 *  `from` / `to` are ISO timestamps (inclusive). Omit to roll up all.
 *  Returns zeros + empty maps when no ledger exists yet.
 *
 *  TODO(scale): currently reads the whole ledger into memory. Fine for
 *  near-term (weeks of events per tenant). When a long-running tenant
 *  accumulates months of events and rollup() crosses ~50MB+, switch to
 *  `createReadStream` + `readline` for streaming aggregation. Trigger:
 *  audit-summary on multi-month windows starts feeling slow.
 */
export function rollup(opts: { tenant_id?: string; from?: string; to?: string } = {}): UsageRollup {
	const tid = opts.tenant_id ?? tenantId();
	const path = ledgerPath(tid);
	const empty: UsageRollup = {
		tenant_id: tid,
		from: opts.from,
		to: opts.to,
		totals: { in_tokens: 0, out_tokens: 0, tool_calls: 0, requests: 0, ms: 0 },
		by_model: {},
		by_tool: {},
		by_device: {},
		event_count: 0,
	};
	if (!existsSync(path)) return empty;
	let raw: string;
	try { raw = readFileSync(path, 'utf-8'); }
	catch { return empty; }
	const lines = raw.split('\n').filter((l) => l.length > 0);
	for (const line of lines) {
		let event: UsageEvent & { ts: string };
		try { event = JSON.parse(line); }
		catch { continue; }
		if (event.tenant_id !== tid) continue;
		if (opts.from && event.ts < opts.from) continue;
		if (opts.to && event.ts > opts.to) continue;
		empty.event_count++;
		if (event.unit === 'in_tokens') empty.totals.in_tokens += event.amount;
		if (event.unit === 'out_tokens') empty.totals.out_tokens += event.amount;
		if (event.unit === 'tool_call') empty.totals.tool_calls += event.amount;
		if (event.unit === 'request') empty.totals.requests += event.amount;
		if (event.unit === 'ms') empty.totals.ms += event.amount;
		if (event.model) {
			const m = (empty.by_model[event.model] ??= { in_tokens: 0, out_tokens: 0, requests: 0, ms: 0 });
			if (event.unit === 'in_tokens') m.in_tokens += event.amount;
			if (event.unit === 'out_tokens') m.out_tokens += event.amount;
			if (event.unit === 'request') m.requests += event.amount;
			if (event.unit === 'ms') m.ms += event.amount;
		}
		if (event.tool) {
			empty.by_tool[event.tool] = (empty.by_tool[event.tool] ?? 0) + event.amount;
		}
		if (event.device_id) {
			empty.by_device[event.device_id] = (empty.by_device[event.device_id] ?? 0) + event.amount;
		}
	}
	return empty;
}

/** @internal — test-only helper to inspect the ledger path. */
export function __ledgerPathForTests(forTenant?: string): string {
	return ledgerPath(forTenant ?? tenantId());
}

/** @internal — test-only helper to drop the ledger between tests. */
export function __resetLedgerForTests(forTenant?: string): void {
	const path = ledgerPath(forTenant ?? tenantId());
	try {
		if (existsSync(path)) {
			// Truncate by re-opening for write. Avoids fs.rmSync because the
			// ledger path may be under a private dir we don't own outright in
			// tests; truncation is enough to reset state.
			closeSync(openSync(path, 'w'));
		}
	} catch {}
}

/** Check whether the ledger file exists yet (mostly for diagnostics). */
export function ledgerExists(forTenant?: string): boolean {
	const path = ledgerPath(forTenant ?? tenantId());
	return existsSync(path);
}

/** Approximate ledger size in bytes (for owner diagnostics). */
export function ledgerSizeBytes(forTenant?: string): number {
	const path = ledgerPath(forTenant ?? tenantId());
	if (!existsSync(path)) return 0;
	try { return statSync(path).size; }
	catch { return 0; }
}
