// action-lease.ts — provenance action lease for tool dispatch (#1585, Susan 2026-06-09)
//
// Problem: in native-audio live mode the model auto-regressively continues the dialogue
// script and fabricates the USER's next turn (literal `user: switch models`), then fires a
// tool off its own fabrication. The fix must make fabrication HARMLESS at the action
// boundary, not try to stop the model from generating it (it's model-class, can't prompt out).
//
// Mechanism (Mini's provenance separation, #1+#2): a gated tool may dispatch ONLY if the
// assistant holds a LEASE minted from a REAL user STT turn. Model-generated turns never run
// the STT path (transcribeAndRecordUtterance, fed by inbound Discord audio/VAD), so they can
// NEVER mint a lease → they can never act. The lease is short-lived (TTL) and re-minted on
// each real user turn, so after the assistant answers, a fabricated "next user turn" finds no
// fresh lease and its tool call is dropped.
//
// This is the STRUCTURAL gate. It composes with the existing semantic content-check
// (_liveIntentCheck, which confirms the real recent speech actually requested THIS tool) —
// lease = "a real user turn happened", content-check = "and it asked for this".

export interface ActionLease {
  id: string;
  mintedAt: number;   // ms-epoch when a real user STT turn landed (mint time)
  transcript: string; // the real user utterance (audit / content-match)
}

// Mint a lease from a REAL user STT turn. Call ONLY from the STT path
// (transcribeAndRecordUtterance), never from model output.
export function mintLease(transcript: string, now: number): ActionLease {
  return { id: `lease-${now}`, mintedAt: now, transcript };
}

// Structural gate: does the assistant hold a fresh, real-STT-minted lease right now?
// No lease (e.g. the tool call came from a model-fabricated turn with no preceding real
// audio) OR an expired lease → false → drop the tool call.
export function leaseValid(lease: ActionLease | null | undefined, now: number, ttlMs: number): boolean {
  if (!lease) return false;
  const age = now - lease.mintedAt;
  return age >= 0 && age < ttlMs;
}
