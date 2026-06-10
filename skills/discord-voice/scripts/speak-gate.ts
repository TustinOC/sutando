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
/**
 * The AI addressing/wake classifier — THE "AI gate" (#1427). Runs once per
 * user utterance in the STT pipeline (discord-voice-server.ts,
 * transcribeAndRecordUtterance): one Gemini call that BOTH transcribes the
 * audio and judges addressed / wake / actionIntent. Its outputs are stamped
 * onto the session (_aiWakeAt, _namedThisBotAt, _actionIntentAt) and consumed
 * here via hasFreshWake()/namedRecently() and decideSpeak().
 *
 * The prompt TEXT lives in this module so the gate's definition is visible
 * alongside its consumers (Susan 2026-06-10); the CALL stays in the STT
 * pipeline because the same request carries the audio bytes for
 * transcription. Verbatim move — prompt tuning rules apply (do not edit
 * without a live test).
 */
export function addressingClassifierPrompt(standName: string): string {
	return `Transcribe this audio verbatim. Then decide: is the speaker directly CALLING ON or ADDRESSING an assistant named "${standName || 'the assistant'}" to get its attention or have it act/respond? Account for speech-to-text errors that garble the name into a similar-sounding word — treat a clearly-intended call to that assistant as addressed=true. Do NOT set addressed=true when the name merely appears as an ordinary unrelated word, when the speaker is only telling it to be quiet / stand by, or when addressing someone else. ALSO set "wake":true when the speaker is GREETING / RE-ESTABLISHING CONTACT to bring the assistant active OR calling on it by name to answer or do something now — e.g. "hi <name>", "hey <name>", "<name>, can you hear me", "<name>, you there", or "hi <name>, <a question or request>". Set "wake":false when the name is only mentioned in passing mid-conversation (not being asked anything), and set "wake":false when they are telling it to be quiet / take notes / stay silent / stand by / enter meeting mode (the OPPOSITE of a wake). A bare name with no greeting and no request is NOT a wake. ALSO set "actionIntent":true when the speaker EXPLICITLY asks for a concrete action to be performed now — e.g. open a URL/app/tab, capture/describe the screen, read or set the clipboard, change brightness/volume, save a note, search the web, or delegate a task (verbs like open / go to / show me / capture / screenshot / copy / paste / set / turn up / play / save / look up / find / check). Set "actionIntent":false for ordinary discussion, thinking out loud, story-telling, or merely MENTIONING a thing (a repo, a file, a website) without asking for an action on it — naming something is not requesting an action. Reply with ONLY a JSON object, no prose: {"transcript":"<verbatim>","addressed":true|false,"wake":true|false,"actionIntent":true|false}. If silence/no speech: {"transcript":"","addressed":false,"wake":false,"actionIntent":false}.`;
}

/** Default freshness windows for the classifier's session stamps. */
export const WAKE_FRESH_MS_DEFAULT = 12_000;
export const NAMED_FRESH_MS_DEFAULT = 10_000;

/**
 * Fresh WAKE signal? True when the AI gate judged wake=true (OR the
 * deterministic isWakePhrase matched) within the window — the requirement
 * for switch_mode("active") to leave meeting mode.
 */
export function hasFreshWake(s: any, windowMs: number = Number(process.env.SUTANDO_WAKE_FRESH_MS) || WAKE_FRESH_MS_DEFAULT): boolean {
	return Date.now() - ((s as any)._aiWakeAt || 0) < windowMs;
}

/**
 * Was the bot NAMED recently? True when the AI gate judged addressed=true
 * (OR the deterministic name matcher hit) within the window — the group-
 * regime requirement for ANY mode switch.
 */
export function namedRecently(s: any, windowMs: number = NAMED_FRESH_MS_DEFAULT): boolean {
	return Date.now() - ((s as any)._namedThisBotAt || 0) < windowMs;
}

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
