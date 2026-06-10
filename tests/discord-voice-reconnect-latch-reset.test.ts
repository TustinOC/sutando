/**
 * #1600 M1 — unit test for the reconnect turn-latch reset (Maddy's GREEN
 * threshold, part a). Verifies the reset clears every turn-level latch/counter
 * that, if left set across a watchdog-reconnect, would prevent the next turn's
 * start (and thus the speak_decision write) from re-firing → the bot stays deaf
 * after the transport is restored. The full runtime hang→reconnect→recover is a
 * staging live-test (part b); this pins the reset logic deterministically.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { resetTurnLatchesOnReconnect, RECONNECT_TURN_LATCH_FIELDS } from '../skills/discord-voice/scripts/speak-gate.ts';

describe('resetTurnLatchesOnReconnect — M1 decision-pipeline recovery', () => {
	it('clears every stuck turn-level latch/counter', () => {
		// Session left in mid-turn state by the pre-hang turn.
		const s: Record<string, any> = {
			_turnAudioAllowed: true,
			_audioPlayedThisTurn: true,
			_recvThisTurn: 7,
			_squelchThisTurn: true,
			utterancesSinceTurn: 44,        // the 2026-06-10 hang signature
			lastTurnActivityTs: 1000,
			gate: { lastAddressedToMe: true },  // unrelated field — must be untouched
		};
		resetTurnLatchesOnReconnect(s, 5000);
		assert.equal(s._turnAudioAllowed, false);
		assert.equal(s._audioPlayedThisTurn, false);
		assert.equal(s._recvThisTurn, 0);
		assert.equal(s._squelchThisTurn, false);
		assert.equal(s.utterancesSinceTurn, 0, 'watchdog counter must re-baseline');
		assert.equal(s.lastTurnActivityTs, 5000, 'watchdog re-baselines to now, not immediate re-trip');
	});

	it('re-baselines lastTurnActivityTs so the watchdog does not instantly re-fire', () => {
		const s: Record<string, any> = { utterancesSinceTurn: 50, lastTurnActivityTs: 0 };
		resetTurnLatchesOnReconnect(s, 99999);
		assert.equal(s.utterancesSinceTurn, 0);
		assert.equal(s.lastTurnActivityTs, 99999);
	});

	it('does NOT touch unrelated session state (scope guard)', () => {
		const s: Record<string, any> = { _turnAudioAllowed: true, meetingMode: true, gate: { lastAddressedToMe: true } };
		resetTurnLatchesOnReconnect(s, 1);
		assert.equal(s.meetingMode, true, 'mode handled separately by shouldRestoreActiveOnReconnect');
		assert.equal(s.gate.lastAddressedToMe, true);
	});

	it('field list is the documented set (no silent drift)', () => {
		assert.deepEqual([...RECONNECT_TURN_LATCH_FIELDS], [
			'_turnAudioAllowed', '_audioPlayedThisTurn', '_recvThisTurn', '_squelchThisTurn', 'utterancesSinceTurn',
		]);
	});
});
