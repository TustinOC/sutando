// Tests for the dispatch-gate STT calibration-race decision (#1427 follow-up, Susan 2026-06-09).
// Bug: Gemini fires a tool off live audio faster than the per-speaker STT transcribes, so the
// dispatch gate judged a legit "open the repo" against stale speech and wrongly blocked it.
// The decision helper says whether to wait for the pending transcript, proceed, or fail open.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sttGateDecision } from '../skills/discord-voice/scripts/stt-gate.ts';

const MAXLAG = 5000;

test('no burst seen → proceed to intent-check', () => {
	assert.equal(sttGateDecision({ speechEndAt: 0, sttAt: 0, now: 1_000_000, maxLagMs: MAXLAG }), 'proceed');
});

test('latest burst already transcribed (sttAt >= speechEndAt) → proceed', () => {
	// STT landed at or after the last burst ended → buffer is current.
	assert.equal(sttGateDecision({ speechEndAt: 1_000_000, sttAt: 1_000_500, now: 1_001_000, maxLagMs: MAXLAG }), 'proceed');
	assert.equal(sttGateDecision({ speechEndAt: 1_000_000, sttAt: 1_000_000, now: 1_001_000, maxLagMs: MAXLAG }), 'proceed');
});

test('burst just ended, STT not landed, within window → wait (the 13:38 race)', () => {
	// Reproduces the measured case: burst ended ~0.6s ago, no STT yet, tool fired.
	const speechEndAt = 1_000_000;
	const now = speechEndAt + 650; // ~0.65s after burst, like 13:38:34.6 vs 13:38:33.968
	assert.equal(sttGateDecision({ speechEndAt, sttAt: 999_000, now, maxLagMs: MAXLAG }), 'wait');
});

test('still pending right up to the deadline → wait', () => {
	const speechEndAt = 1_000_000;
	assert.equal(sttGateDecision({ speechEndAt, sttAt: 0, now: speechEndAt + MAXLAG - 1, maxLagMs: MAXLAG }), 'wait');
});

test('pending past the deadline (STT never landed) → fail open', () => {
	const speechEndAt = 1_000_000;
	assert.equal(sttGateDecision({ speechEndAt, sttAt: 0, now: speechEndAt + MAXLAG, maxLagMs: MAXLAG }), 'failopen');
	assert.equal(sttGateDecision({ speechEndAt, sttAt: 0, now: speechEndAt + MAXLAG + 2000, maxLagMs: MAXLAG }), 'failopen');
});

test('transcript landing flips wait → proceed (the poll resolves)', () => {
	const speechEndAt = 1_000_000;
	const now = speechEndAt + 800;
	// before STT lands: pending → wait
	assert.equal(sttGateDecision({ speechEndAt, sttAt: 999_000, now, maxLagMs: MAXLAG }), 'wait');
	// after STT lands for this burst: proceed (judge the real utterance)
	assert.equal(sttGateDecision({ speechEndAt, sttAt: speechEndAt + 750, now: now + 100, maxLagMs: MAXLAG }), 'proceed');
});

test('spurious fire during true silence (old transcribed burst) → proceed (gets blocked downstream)', () => {
	// A burst long ago, already transcribed; nothing pending → the intent-check still runs and
	// can block a genuinely spurious fire. Protection preserved.
	assert.equal(sttGateDecision({ speechEndAt: 1_000_000, sttAt: 1_000_200, now: 1_030_000, maxLagMs: MAXLAG }), 'proceed');
});
