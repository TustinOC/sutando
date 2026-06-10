// Population-aware speak gate — pure decision logic, no I/O (#1427, Susan's
// 2026-06-09 spec: "utterance ended → answer" only holds for one human; with
// more people the bot must be ADDRESSED to speak, and every decision must be
// auditable in sqlite).
//
// Layering (owner-approved 2026-06-09): the MODE layer stays an LLM tool
// (switch_mode, solo/group handler selected by population in the server); the
// SPEAK layer is THIS code function, run at the audio-output gate — explicit,
// deterministic, zero added latency, one sqlite row per turn.

export type Regime = 'solo' | 'group';

/** solo = at most one human in the VC (the clean 1:1 path, behavior frozen). */
export function regimeFor(humanCount: number): Regime {
	return humanCount <= 1 ? 'solo' : 'group';
}

export interface SpeakInput {
	/** Humans (non-bot members) currently in the voice channel. */
	humanCount: number;
	/** Bot is in silent meeting/note-taker mode. */
	meetingMode: boolean;
	/** The current turn's speaker has addressed the bot (sticky included). */
	addressedToMe: boolean;
	/** Mode-switch ack in flight — always audible. */
	allowAck: boolean;
	/** Post-switch force-audible window is open. */
	forceAudible: boolean;
}

export interface SpeakDecision {
	speak: boolean;
	regime: Regime;
	/** Short audit token — lands in the sqlite speak_decision row. */
	reason: string;
}

/**
 * One decision per turn:
 *   ack/force-audible        → speak (both regimes; mode-switch UX invariant)
 *   solo + active            → speak (today's verified-clean behavior, frozen)
 *   solo + meeting           → speak iff addressed (today's name-gate behavior)
 *   group + active|meeting   → speak iff addressed — "被叫才答", the new rule
 */
export function decideSpeak(inp: SpeakInput): SpeakDecision {
	const regime = regimeFor(inp.humanCount);
	if (inp.allowAck) return { speak: true, regime, reason: 'ack' };
	if (inp.forceAudible) return { speak: true, regime, reason: 'force-audible' };
	if (regime === 'solo') {
		if (!inp.meetingMode) return { speak: true, regime, reason: 'solo-active' };
		return inp.addressedToMe
			? { speak: true, regime, reason: 'solo-meeting-addressed' }
			: { speak: false, regime, reason: 'solo-meeting-silent' };
	}
	return inp.addressedToMe
		? { speak: true, regime, reason: inp.meetingMode ? 'group-meeting-addressed' : 'group-active-addressed' }
		: { speak: false, regime, reason: inp.meetingMode ? 'group-meeting-silent' : 'group-active-unaddressed' };
}
