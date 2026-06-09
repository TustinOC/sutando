// stt-gate.ts — dispatch-gate / STT calibration-race decision (#1427 follow-up, Susan 2026-06-09)
//
// The problem (measured 2026-06-09 live test): two parallel paths run from the user's audio.
//   1. audio → Gemini Live: reacts to speech in real time and fires a tool the instant the
//      user asks (e.g. "open the repo" → open_url). FAST.
//   2. audio → per-speaker STT (fire-and-forget on AfterSilence): transcribes the SAME
//      utterance ~3s later and appends it to the recent-speech buffer. SLOW.
// The DispatchGate's live intent-check reads the recent-speech buffer (path 2) at the moment a
// tool fires (path 1). Because path 1 is faster, the utterance that TRIGGERED the tool is not
// in the buffer yet — the gate judges against stale speech and wrongly blocks a legit request.
// (Observed: burst ended 13:38:33.968 → tool fired 13:38:34.6 → gate judged 13:38:35.8 →
//  STT landed 13:38:37.374; the gate decided ~2.8s too early and blocked a real "open repo".)
//
// Fix: when a gated tool fires, if a speech burst just ended but its transcript has not landed
// yet, WAIT briefly for it, then judge against the transcript that actually triggered the tool.
// If it never lands within the window we KNOW the user just spoke (a burst ended) → fail OPEN,
// because blocking a likely-real request is the worse error (the codebase already biases to
// fail-open: false-negatives are the user's pain).

export type SttGateDecision = 'proceed' | 'wait' | 'failopen';

export interface SttGateState {
  /** ms-epoch when the user last STOPPED speaking (0 = no burst seen). */
  speechEndAt: number;
  /** ms-epoch when the last STT transcript landed in the recent-speech buffer (0 = none). */
  sttAt: number;
  /** current ms-epoch. */
  now: number;
  /** how long to wait for a pending transcript before failing open. */
  maxLagMs: number;
}

// Decision:
//  - No burst pending transcription (the latest burst is already transcribed, or there is no
//    burst — e.g. true silence) → 'proceed' to the intent-check. A genuinely spurious tool fire
//    during silence still gets checked and blocked, so protection is preserved.
//  - A burst ended more recently than the last STT landed, AND that was within maxLag → 'wait':
//    the triggering utterance is mid-transcription; poll until it lands.
//  - Still pending after maxLag elapsed → 'failopen': STT did not catch up for an utterance the
//    user demonstrably just spoke; allow rather than block a likely-real request.
export function sttGateDecision(st: SttGateState): SttGateDecision {
  const { speechEndAt, sttAt, now, maxLagMs } = st;
  const burstPending = speechEndAt > 0 && sttAt < speechEndAt;
  if (!burstPending) return 'proceed';
  return (now - speechEndAt) < maxLagMs ? 'wait' : 'failopen';
}
