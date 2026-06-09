// Tests for the discord-voice provenance action lease (#1585).
// A gated tool may dispatch ONLY while a lease minted from a REAL user STT turn is held.
// Model-fabricated `user:` turns never run the STT path → never mint a lease → can't act.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mintLease, leaseValid } from '../skills/discord-voice/scripts/action-lease.ts';

const TTL = 30_000;

test('mintLease stamps mint time + transcript', () => {
	const l = mintLease('open the repo', 1_000_000);
	assert.equal(l.mintedAt, 1_000_000);
	assert.equal(l.transcript, 'open the repo');
	assert.match(l.id, /^lease-1000000$/);
});

test('no lease → invalid (the fabrication case: no real STT ever minted one)', () => {
	assert.equal(leaseValid(null, 1_000_000, TTL), false);
	assert.equal(leaseValid(undefined, 1_000_000, TTL), false);
});

test('fresh lease → valid', () => {
	const l = mintLease('hi', 1_000_000);
	assert.equal(leaseValid(l, 1_000_000, TTL), true);          // same instant
	assert.equal(leaseValid(l, 1_000_000 + TTL - 1, TTL), true); // just inside TTL
});

test('expired lease → invalid (model continues script after the real turn aged out)', () => {
	const l = mintLease('hi', 1_000_000);
	assert.equal(leaseValid(l, 1_000_000 + TTL, TTL), false);      // exactly at TTL
	assert.equal(leaseValid(l, 1_000_000 + TTL + 5_000, TTL), false);
});

test('negative age (clock skew) → invalid, never allow', () => {
	const l = mintLease('hi', 1_000_000);
	assert.equal(leaseValid(l, 999_000, TTL), false);
});

test('scenario: fabricated tool fire with no real user turn is blocked', () => {
	// Session just started, no real STT yet → actionLease is null → any gated tool blocked.
	const sessionLease: ReturnType<typeof mintLease> | null = null;
	assert.equal(leaseValid(sessionLease, 1_000_000, TTL), false);
});

test('scenario: real turn mints lease → tool within TTL allowed; later fabrication blocked', () => {
	const real = mintLease('open the sutando repo', 1_000_000);
	// real request fires open_github_url 2s later → within lease → allowed
	assert.equal(leaseValid(real, 1_002_000, TTL), true);
	// model keeps writing the script and fabricates a tool 40s later (past TTL) → blocked
	assert.equal(leaseValid(real, 1_040_000, TTL), false);
});
