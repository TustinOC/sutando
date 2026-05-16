/**
 * Sutando — Output Router
 *
 * Picks which surface(s) of a connected device render the agent's reply.
 * Implements the §5 Next item "Device-aware output routing" from
 * `vision and roadmap.md`:
 *
 *   "When Sutando replies, pick the surface (HUD, voice, voice+HUD,
 *    voice+attached file) based on the active device profile and the reply
 *    shape. Short status → HUD; research result → voice + push file to
 *    laptop; alert → all surfaces."
 *
 * This module is the contract; callers (voice-agent's onTurnCompleted
 * hook + bridges' explicit reply paths) consume it in a follow-up PR.
 *
 * Cross-references:
 * - `src/device-session.ts` — `DeviceSession` + `OutputChannel` types.
 * - `OpenCompanion/device-profiles/*.yaml` — concrete profile shape;
 *   `hudBudget()` mirrors what each yaml declares.
 * - `notes/devicesession-design-2026-05-16.md` §6 — the routing sketch
 *   this implements.
 */

import type { DeviceSession, OutputChannel, HudZone } from './device-session.js';

/** Shape of an agent reply at the router boundary. The router doesn't
 *  generate text — it dispatches an already-rendered reply. */
export interface AgentReply {
	/** The primary text content. May be empty when only an attachment matters. */
	text: string;
	/** Hint for surface selection. Defaults to 'narrative' when omitted. */
	kind?: ReplyKind;
	/** Files to deliver alongside (e.g. a transcript path, a generated image). */
	attachments?: string[];
}

export type ReplyKind = 'short' | 'status' | 'narrative' | 'alert';

/** Result of dispatching a reply — for telemetry + tests. */
export interface RouteResult {
	deviceId: string;
	used: ReadonlyArray<'voice' | 'hud' | 'file_push' | 'task'>;
	dropped: ReadonlyArray<'voice' | 'hud' | 'file_push' | 'task'>;
	hudTextChars?: number;
	hudZone?: HudZone;
}

/** Maximum characters per HUD render for a given device. Conservative
 *  per-profile budgets that match what each device's color/resolution can
 *  legibly display. These mirror the §7 fleet matrix in the roadmap. */
export function hudBudget(profile: DeviceSession['profile']): number {
	if (!profile.hud.enabled) return 0;
	switch (profile.id) {
		case 'mentra-live':    return 80;  // 400×240 green center
		case 'even-g2':        return 80;  // 576×288 green lower-right
		case 'brilliant-halo': return 160; // 640×400 color OLED — richer
		case 'meta-display':   return 60;  // ~640×480, used sparingly per Meta UX
		case 'full-ar':        return 240; // large transparent overlay
		case 'laptop':         return 1000; // full screen — effectively unbounded
		default:               return 80;   // safe default for unknown HUDs
	}
}

/** Default HUD render zone for a profile. Mentra is center; Even G2 is
 *  lower-right (their respective UX defaults). */
export function hudZone(profile: DeviceSession['profile']): HudZone {
	if (profile.hud.position === 'lower-right' || profile.hud.position === 'right') return 'right';
	if (profile.hud.position === 'bottom') return 'bottom';
	if (profile.hud.position === 'top') return 'top';
	if (profile.hud.position === 'left') return 'left';
	return 'center';
}

/** Pick which surfaces should render the reply for this device.
 *
 *  Policy (intentionally simple — refine when real device dogfood
 *  reveals what feels right):
 *  - kind=short / status → HUD if available (truncated to budget),
 *    voice only if no HUD.
 *  - kind=narrative → voice + file_push for long bodies (>1500 chars)
 *    or any attachments.
 *  - kind=alert → all surfaces — HUD + voice + file_push.
 *  - attachments → always include file_push when the device has it.
 */
export function pickSurfaces(
	session: DeviceSession,
	reply: AgentReply,
): { useHud: boolean; useVoice: boolean; useFilePush: boolean; useTaskFallback: boolean } {
	const kind: ReplyKind = reply.kind ?? 'narrative';
	const hasHud = session.output.some((o) => o.kind === 'hud') && session.profile.hud.enabled;
	const hasVoice = session.output.some((o) => o.kind === 'voice');
	const hasFilePush = session.output.some((o) => o.kind === 'file_push');
	const hasTask = session.output.some((o) => o.kind === 'task');
	const hasAttachments = (reply.attachments?.length ?? 0) > 0;
	const longText = reply.text.length > 1500;

	let useHud = false;
	let useVoice = false;
	let useFilePush = false;
	let useTaskFallback = false;

	if (kind === 'short' || kind === 'status') {
		useHud = hasHud;
		// Only fall through to voice when there's no HUD — short statuses
		// shouldn't double-deliver.
		useVoice = !hasHud && hasVoice;
	} else if (kind === 'narrative') {
		useVoice = hasVoice;
		useFilePush = hasFilePush && (longText || hasAttachments);
	} else if (kind === 'alert') {
		useHud = hasHud;
		useVoice = hasVoice;
		// `useFilePush = true` here is intentionally permissive — the
		// "alert fans to all surfaces" rule from the roadmap. The actual
		// `file_push.push()` invocation in `routeReply()` is gated on
		// `reply.attachments?.length`, so an attachment-less alert
		// produces NO spurious file write — the permissive flag here
		// just says "we're willing to fire file_push IF there's something
		// to send." If a future alert kind needs to send a file even
		// without explicit attachments (e.g. "alert + auto-attach the
		// triggering log line"), the caller passes attachments and this
		// stays correct.
		useFilePush = hasFilePush;
	}

	// Attachments always opt-in to file push when the surface exists,
	// regardless of kind — losing an attachment because the kind tag was
	// 'short' would be a contract violation from the caller's POV.
	if (hasAttachments && hasFilePush) useFilePush = true;

	// Last-resort fallback: if no real surface picked but the device has a
	// task-file output (always-on recorders, headless bridges), use it so
	// the reply isn't dropped silently.
	if (!useHud && !useVoice && !useFilePush && hasTask) useTaskFallback = true;

	return { useHud, useVoice, useFilePush, useTaskFallback };
}

function findChannel(session: DeviceSession, kind: OutputChannel['kind']): OutputChannel | undefined {
	return session.output.find((o) => o.kind === kind);
}

function truncate(text: string, n: number): string {
	if (text.length <= n) return text;
	// Trim at a word boundary when possible, then append a single ellipsis.
	const slice = text.slice(0, n - 1);
	const lastSpace = slice.lastIndexOf(' ');
	const stop = lastSpace > n * 0.6 ? lastSpace : slice.length;
	return slice.slice(0, stop) + '…';
}

/** Dispatch a reply to the chosen surfaces. Returns a RouteResult for
 *  telemetry. Errors in any single channel are swallowed (logged) so a
 *  HUD render failure doesn't suppress the voice reply. */
export async function routeReply(session: DeviceSession, reply: AgentReply): Promise<RouteResult> {
	const pick = pickSurfaces(session, reply);
	const used: Array<'voice' | 'hud' | 'file_push' | 'task'> = [];
	const dropped: Array<'voice' | 'hud' | 'file_push' | 'task'> = [];
	let hudTextChars: number | undefined;
	let zone: HudZone | undefined;

	if (pick.useHud) {
		const c = findChannel(session, 'hud');
		if (c && c.kind === 'hud') {
			const budget = hudBudget(session.profile);
			const text = budget ? truncate(reply.text, budget) : reply.text;
			zone = hudZone(session.profile);
			hudTextChars = text.length;
			try { await c.render(text, zone); used.push('hud'); }
			catch (err) { console.error('[OutputRouter] hud render failed:', err); dropped.push('hud'); }
		} else { dropped.push('hud'); }
	}

	if (pick.useVoice) {
		const c = findChannel(session, 'voice');
		if (c && c.kind === 'voice') {
			try {
				c.transport.sendContent?.([{ role: 'assistant', text: reply.text }], true);
				used.push('voice');
			} catch (err) { console.error('[OutputRouter] voice send failed:', err); dropped.push('voice'); }
		} else { dropped.push('voice'); }
	}

	if (pick.useFilePush && reply.attachments?.length) {
		const c = findChannel(session, 'file_push');
		if (c && c.kind === 'file_push') {
			for (const path of reply.attachments) {
				try { await c.push(path); }
				catch (err) { console.error(`[OutputRouter] file_push failed for ${path}:`, err); }
			}
			used.push('file_push');
		} else { dropped.push('file_push'); }
	}

	if (pick.useTaskFallback) {
		const c = findChannel(session, 'task');
		if (c && c.kind === 'task') {
			try { c.writeTask(reply.text); used.push('task'); }
			catch (err) { console.error('[OutputRouter] task fallback failed:', err); dropped.push('task'); }
		}
	}

	return {
		deviceId: session.deviceId,
		used,
		dropped,
		hudTextChars,
		hudZone: zone,
	};
}
