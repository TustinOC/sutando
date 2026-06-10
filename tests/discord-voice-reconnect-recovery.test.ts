/**
 * #1600 M1 — part-b INTEGRATION harness (Maddy's GREEN threshold, part b).
 *
 * Part-a (discord-voice-reconnect-latch-reset.test.ts) pins the reset in
 * isolation: resetTurnLatchesOnReconnect clears the 6 latch fields. That proves
 * the reset DOES something — not that the something REVIVES the dead pipeline.
 *
 * This file closes that gap WITHOUT a 10-minute natural hang and without a real
 * Gemini session. It drives the exact runtime sequence the bug lives in:
 *
 *   pre-hang turn leaves latches stuck  →  watchdog reconnect (calls the reset)
 *   →  synthetic audio chunk arrives    →  assert a speak_decision row is written
 *
 * The assertion is the audit row, because that row IS the observable signal the
 * 2026-06-10 Chi session lost: "speak_decision stayed dead from 10:58 to session
 * end" (discord-voice-server.ts:2167). If the reset re-arms the pipeline, the
 * first post-reconnect chunk writes exactly one row; if it doesn't, zero rows —
 * which is precisely the "叫不醒" failure.
 *
 * FIDELITY: feedAudioChunk() below mirrors the handleAudioOutput turn-start gate
 * (discord-voice-server.ts:1830–1855) and the no-controller branch of
 * shouldEmitAudio (:205–213) line-for-line, and calls the REAL decideSpeak +
 * resetTurnLatchesOnReconnect from speak-gate.ts. Only the per-chunk sequencing
 * is replicated (those lines live inside a closure bound to the live Discord/
 * Gemini wiring and can't be imported); the M1 decision logic under test is the
 * genuine article. The field-list no-drift guard in part-a's test catches reset
 * drift; a comment-anchor below flags the sequencing if those server lines move.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { decideSpeak, resetTurnLatchesOnReconnect } from '../skills/discord-voice/scripts/speak-gate.ts';

/** A captured speak_decision audit row (the recordEvent payload at :1852). */
interface SpeakRow { reason: string; speak: boolean; regime: string; humans: number; meeting: boolean; }

/**
 * Faithful replica of handleAudioOutput's per-chunk turn-start gate
 * (discord-voice-server.ts:1830–1855). Returns nothing; pushes one SpeakRow to
 * `rows` iff the real code would have written a speak_decision row this chunk.
 * `gapMs` is the time since the bot's own last audio chunk — <1500 means
 * CONTINUOUS audio, so the :1830 gap-reset does NOT fire (this is the case the
 * gap-reset can't rescue and the reconnect reset must).
 */
function feedAudioChunk(s: Record<string, any>, nowMs: number, rows: SpeakRow[]): void {
	// :1830 — reset latches only on a real >1.5s gap in the bot's OWN output.
	if (nowMs - (s._lastAudioTs || 0) > 1500) {
		s._turnAudioAllowed = false; s._audioPlayedThisTurn = false; s._recvThisTurn = 0; s._squelchThisTurn = false;
	}
	// :1835 — squelched turn drops every remaining chunk (one of the stuck states).
	if (s._squelchThisTurn) return;
	// :1839 — count chunks RECEIVED this turn.
	s._recvThisTurn = (s._recvThisTurn || 0) + 1;
	// :1845 → shouldEmitAudio no-controller branch (:205–213): decideSpeak + stash.
	const d = decideSpeak({
		humanCount: s._humanCount ?? 1,
		meetingMode: !!s.meetingMode,
		addressedToMe: s.gate?.lastAddressedToMe === true,
		allowAck: false,
		forceAudible: false,
	});
	s._lastSpeakDecision = d;
	// :1850 — ONE audit row per turn, at the FIRST received chunk.
	if (s._recvThisTurn === 1 && s._lastSpeakDecision) {
		rows.push({ ...d, humans: s._humanCount ?? 1, meeting: !!s.meetingMode });
	}
	// :1854 — open the latch when the gate says speak.
	if (d.speak) { s._turnAudioAllowed = true; s._lastAudioTs = nowMs; s._audioPlayedThisTurn = true; }
}

/** A session as the watchdog reconnect handler receives it, mid-stuck-turn. */
function stuckSession(baseTs: number): Record<string, any> {
	return {
		// group regime, bot was addressed → decideSpeak WILL say speak: the gate
		// is willing; only the stuck counters keep the audit pipeline dead.
		_humanCount: 2,
		meetingMode: false,
		meetingEntered: false,
		gate: { lastAddressedToMe: true },
		// the 2026-06-10 hang signature: counters left high from the pre-hang turn.
		_recvThisTurn: 44,
		_turnAudioAllowed: true,
		_audioPlayedThisTurn: true,
		_squelchThisTurn: false,
		utterancesSinceTurn: 44,
		_lastAudioTs: baseTs,        // recent → continuous audio, NO >1.5s gap rescue
	};
}

describe('#1600 M1 part-b — reconnect revives the speak_decision pipeline', () => {
	it('BEFORE reset: continuous post-hang audio writes NO speak_decision row (the bug)', () => {
		const base = 1_000_000;
		const s = stuckSession(base);
		const rows: SpeakRow[] = [];
		// Three back-to-back chunks, each <1.5s after the last → gap-reset never
		// fires, _recvThisTurn climbs 45,46,47 — never === 1 → never logs.
		feedAudioChunk(s, base + 200, rows);
		feedAudioChunk(s, base + 400, rows);
		feedAudioChunk(s, base + 600, rows);
		assert.equal(rows.length, 0, 'stuck counter ⇒ audit pipeline dead despite willing gate — reproduces 叫不醒');
	});

	it('AFTER reset: the very next chunk writes exactly one speak_decision row (the fix)', () => {
		const base = 1_000_000;
		const s = stuckSession(base);
		const rows: SpeakRow[] = [];
		feedAudioChunk(s, base + 200, rows);          // still dead (recv→45)
		assert.equal(rows.length, 0);

		// Watchdog reconnect path runs the M1 reset (discord-voice-server.ts:2176).
		resetTurnLatchesOnReconnect(s, base + 300);
		assert.equal(s._recvThisTurn, 0, 'reset re-armed the turn counter');

		// Next chunk of the bot's reply (continuous, gap 200ms — gap-reset would
		// NOT have saved this): recv 0→1 → row written.
		feedAudioChunk(s, base + 500, rows);
		assert.equal(rows.length, 1, 'pipeline resumed: first post-reconnect chunk logs the decision');
		assert.equal(rows[0].reason, 'group-active-addressed');
		assert.equal(rows[0].speak, true);
		assert.equal(rows[0].regime, 'group');
	});

	it('AFTER reset: a SQUELCH-stuck turn (every chunk dropped) also recovers', () => {
		const base = 2_000_000;
		const s = stuckSession(base);
		s._squelchThisTurn = true;                    // stuck stop-word squelch from pre-hang turn
		const rows: SpeakRow[] = [];
		// While squelched, chunks return at :1835 — no recv, no row, bot stays mute.
		feedAudioChunk(s, base + 200, rows);
		feedAudioChunk(s, base + 400, rows);
		assert.equal(rows.length, 0, 'squelch latch ⇒ permanently muted across the hang');

		resetTurnLatchesOnReconnect(s, base + 500);
		assert.equal(s._squelchThisTurn, false, 'reset cleared the squelch latch');

		feedAudioChunk(s, base + 700, rows);
		assert.equal(rows.length, 1, 'squelch cleared ⇒ pipeline resumes');
		assert.equal(rows[0].speak, true);
	});

	it('reset does not FORCE a row when the gate would legitimately stay silent', () => {
		// Scope check: the reset re-arms the pipeline, it does not override the
		// decision. Group regime, NOT addressed → decideSpeak says silent → the row
		// is still written (audit row logs silences too) but speak=false. This
		// guards against a "reset == always speak" misread of the fix.
		const base = 3_000_000;
		const s = stuckSession(base);
		s.gate.lastAddressedToMe = false;             // nobody named the bot
		const rows: SpeakRow[] = [];
		resetTurnLatchesOnReconnect(s, base + 100);
		feedAudioChunk(s, base + 300, rows);
		assert.equal(rows.length, 1, 'audit row still written (decision is auditable either way)');
		assert.equal(rows[0].speak, false, 'gate verdict preserved: unaddressed group stays silent');
		assert.equal(rows[0].reason, 'group-active-unaddressed');
	});
});
