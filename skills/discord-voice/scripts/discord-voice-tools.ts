/**
 * discord-voice-tools.ts — voice-session-scoped inline tools for the discord-voice
 * skill.
 *
 * These tools need the live `DiscordVoiceSession` (specifically its connected
 * discord.js `client`), so they cannot live in `src/inline-tools.ts` (which has no
 * session in scope). They are defined here as factories that take the session `s`
 * and are imported + pushed by `discord-voice-server.ts` buildAgent. Tier
 * classification (owner/team/open) and the dispatch gate are applied by the caller.
 */
import { execFileSync } from 'node:child_process';
import { writeFileSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { z } from 'zod';
import type { ToolDefinition } from 'bodhi-realtime-agent';
import { recordConversation, recordEvent } from '../../../src/conversation-store.js';
import { type GithubKind, CANONICAL_REPO, resolveGithubTarget } from './github-url.js';

const ts = () => new Date().toLocaleTimeString('en-US', { hour12: false });

// Read a name→id map from a user-private Discord config file (NOT in the repo). These
// ids are private info (Susan 2026-06-09: "don't hardcode my mini/maddy secret info —
// read it from access.json or similar"), so channel/user ids live under
// ~/.claude/channels/discord/ alongside access.json, never in source.
function _readDiscordConfig(filename: string): Record<string, string> {
	try {
		const o = JSON.parse(readFileSync(join(homedir(), '.claude', 'channels', 'discord', filename), 'utf-8'));
		const out: Record<string, string> = {};
		for (const k in o) out[k.toLowerCase()] = String(o[k]);
		return out;
	} catch { return {}; }
}

/**
 * Allowlisted send-target channels: keyword -> channel id. Read from
 * ~/.claude/channels/discord/send-channels.json (user-private); env
 * `SUTANDO_SEND_CHANNELS` (JSON) overrides. NO ids hardcoded in source.
 */
export function sendChannels(): Record<string, string> {
	const base = _readDiscordConfig('send-channels.json');
	try {
		const o = JSON.parse(process.env.SUTANDO_SEND_CHANNELS || '{}');
		for (const k in o) base[k.toLowerCase()] = String(o[k]);
	} catch { /* keep file values */ }
	return base;
}

/**
 * Allowlisted @-mention targets: handle/name -> Discord user id. Lets the model
 * @-mention a user BY NAME (it can't look up ids itself). Read from
 * ~/.claude/channels/discord/mention-users.json (user-private); env
 * `SUTANDO_MENTION_USERS` (JSON) overrides. NO ids hardcoded in source (Susan
 * 2026-06-09: these handle→id mappings are private, must not live in the repo).
 */
export function mentionUsers(): Record<string, string> {
	const base = _readDiscordConfig('mention-users.json');
	try {
		const o = JSON.parse(process.env.SUTANDO_MENTION_USERS || '{}');
		for (const k in o) base[k.toLowerCase()] = String(o[k]);
	} catch { /* keep file values */ }
	return base;
}

/**
 * send_discord_message — post a message DIRECTLY to an allowlisted text channel via
 * the voice bot's discord.js client (no round-trip through core/`work`). Channels AND
 * @-mention users are allowlist-resolved (never guess an id). Owner-tier + dispatch-gated.
 */
export function makeSendDiscordMessageTool(s: any): ToolDefinition {
	const CHANNELS = sendChannels();
	const USERS = mentionUsers();
	return {
		name: 'send_discord_message',
		description:
			'Send a text message to a Discord CHANNEL the user names. Use ONLY when the user EXPLICITLY asks to send/post a message to a specific channel — ' +
			'e.g. "tell the team in #dev that…", "post in general …", "send a message to susan_private …", "tell mini in #susan that…". ' +
			'The channel is resolved by name against an allowlist (dev / general / susan / susan_private). ' +
			'To @-MENTION someone (e.g. "tell mini …", "let maddy know …"), pass their name in `mention` (mini / maddy / lucy / susan) — it is resolved to a proper <@id> mention prepended to the message. ' +
			'If the named channel or mention is NOT in the allowlist, this returns an error asking the user to clarify — it NEVER guesses an id. ' +
			'Send exactly ONE message per explicit user request; do not re-send.',
		parameters: z.object({
			channel: z.string().describe('Target channel name/keyword: dev, general, susan, or susan_private'),
			text: z.string().describe('The exact message text to send'),
			mention: z.string().optional().describe('Optional: a user to @-mention by name (mini / maddy / lucy / susan). Resolved to <@id> and prepended to the message.'),
		}),
		execution: 'inline',
		async execute(args) {
			const { channel, text, mention } = args as { channel: string; text: string; mention?: string };
			const key = String(channel || '').toLowerCase().replace(/^#/, '').replace(/[\s-]+/g, '_').trim();
			const id = CHANNELS[key];
			if (!id) return { status: 'unknown_channel', message: `No channel called "${channel}". Known: ${Object.keys(CHANNELS).join(', ')}. Ask the user which one — do NOT guess.` };
			if (!text || !text.trim()) return { status: 'empty', message: 'No message text — nothing was sent.' };
			let body = text;
			if (mention && mention.trim()) {
				const mkey = mention.toLowerCase().replace(/^@/, '').replace(/[\s-]+/g, '_').trim();
				const uid = USERS[mkey];
				if (!uid) return { status: 'unknown_user', message: `No user called "${mention}". Known: ${Object.keys(USERS).join(', ')}. Ask the user who to mention — do NOT guess an id.` };
				body = `<@${uid}> ${text}`;
			}
			try {
				const ch: any = await s.client.channels.fetch(id);
				if (!ch || typeof ch.send !== 'function') return { status: 'not_sendable', message: `Channel "${channel}" can't receive messages.` };
				await ch.send(body);
				console.log(`${ts()} [SendMsg] → #${key} (${id})${mention ? ' @' + mention : ''}: ${body.slice(0, 80)}`);
				return { status: 'sent', channel: key, mentioned: mention || null };
			} catch (e) {
				return { status: 'error', message: `Send failed: ${e instanceof Error ? e.message : String(e)}` };
			}
		},
	};
}

/**
 * open_github_url — OPEN a browser tab to a canonical-repo page (no `s` needed).
 * Generic-ish, but kept voice-scoped here (not in src/inline-tools.ts) so it can't
 * name-clash with the voice/phone shared inline tools (Susan 2026-06-09).
 */
export const openGithubUrlTool: ToolDefinition = {
	name: 'open_github_url',
	description:
		'OPEN a browser TAB to a page in the canonical Sutando repo (sonichi/sutando). ' +
		'Use this ONLY when the user explicitly wants to OPEN / SHOW / GO TO a GitHub page in the browser — ' +
		'"open the repo" / "open PR 1409" / "show me issue 900 on GitHub" / "go to GitHub / our repo" / ' +
		'"打开仓库网页 / 打开 PR N / 打开 issue N / 指向 repository". Prefer this over the generic open_url for those OPEN requests. ' +
		'Do NOT use this to ANSWER a QUESTION about GitHub. A status / info question — ' +
		'"what\'s the status of PR N", "how is this PR doing", "is it merged / approved", ' +
		'"what are the recent PRs", "who opened what" — is an information lookup, NOT an open request: ' +
		'route those to the `work` tool, which runs gh / the GitHub API and ANSWERS without opening any tab. ' +
		'kind=open_repo → open the repo homepage; ' +
		'kind=open_pr (n) → open PR #n in a tab; ' +
		'kind=open_issue (n) → open issue #n in a tab. ' +
		'(kind=recent_prs / issues_by exist but are list lookups — prefer routing those through `work` too.) ' +
		'ALWAYS resolves to sonichi/sutando — never a private mirror or a guessed URL. ' +
		'Default to kind=open_repo when the user explicitly asks to OPEN the repository without naming a PR or issue.',
	parameters: z.object({
		kind: z.enum(['open_repo', 'open_pr', 'open_issue', 'recent_prs', 'issues_by']),
		n: z.number().int().positive().optional().describe('PR or issue number (open_pr / open_issue)'),
		who: z.string().optional().describe('GitHub username (issues_by)'),
		limit: z.number().int().positive().optional().describe('max items for recent_prs (default 5)'),
	}),
	execution: 'inline',
	async execute(args) {
		const { kind, n, who, limit } = args as { kind: GithubKind; n?: number; who?: string; limit?: number };
		const openUrl = (u: string): boolean => {
			try { execFileSync('open', [u], { timeout: 4_000 }); return true; } catch { return false; }
		};
		const ghJson = (a: string[]): any => {
			try { return JSON.parse(execFileSync('gh', a, { timeout: 8_000, encoding: 'utf-8', env: process.env }).toString() || 'null'); }
			catch { return null; }
		};
		try {
			// Pure resolution (canonical repo, no remote guessing) — see github-url.ts.
			const tgt = resolveGithubTarget(kind, { n, who, limit });
			if (tgt.error) return { error: tgt.error };
			if (tgt.url) {
				const ok = openUrl(tgt.url);
				if (kind === 'open_pr' && n) {
					const pr = ghJson(['pr', 'view', String(n), '--repo', CANONICAL_REPO, '--json', 'number,title,author,state']);
					return { opened: tgt.url, ok, summary: pr?.number ? `PR #${pr.number}: "${pr.title}" by ${pr.author?.login ?? '?'} — ${pr.state}` : undefined };
				}
				if (kind === 'open_issue' && n) {
					const is = ghJson(['issue', 'view', String(n), '--repo', CANONICAL_REPO, '--json', 'number,title,state']);
					return { opened: tgt.url, ok, summary: is?.number ? `Issue #${is.number}: "${is.title}" — ${is.state}` : undefined };
				}
				return { opened: tgt.url, ok };
			}
			if (tgt.ghArgs) {
				const rows = ghJson(tgt.ghArgs);
				if (!Array.isArray(rows)) return { error: 'gh unavailable', repo: CANONICAL_REPO };
				if (kind === 'recent_prs') return { repo: CANONICAL_REPO, prs: rows.map((p: any) => ({ n: p.number, title: p.title, author: p.author?.login })) };
				return { repo: CANONICAL_REPO, author: who, issues: rows.map((i: any) => ({ n: i.number, title: i.title })) };
			}
			return { error: 'unknown kind' };
		} catch (e) { return { error: e instanceof Error ? e.message : String(e) }; }
	},
};

/**
 * switch_mode — flip THIS voice bot between active and silent meeting/note-taking
 * mode. Needs `s` + the session's mode-file path and stand name (passed in to avoid
 * a circular import back into discord-voice-server.ts). Body preserved verbatim.
 */
export function makeSwitchModeTool(s: any, opts: { voiceModeFile: string; standName: string; getHumanCount?: () => number }): ToolDefinition {
	return {
		name: 'switch_mode',
		description:
			'Switch THIS bot between active mode and silent meeting / note-taking mode. ' +
			'Call switch_mode("meeting") when the user asks you to take notes, go silent, be quiet, stand by / standby, hold on, wait, or enter meeting/passive mode. ' +
			'Call switch_mode("active") when the user asks you to come back, resume, wake up, or be active. ' +
			'IMPORTANT: only call this when the user is addressing YOU BY NAME in the command (e.g. "Hi <your name>, …" / "<your name>, …"). If they say a switch command WITHOUT your name, do NOT call it — it is meant for the other bot. ' +
			'And only call it when they genuinely mean to switch your mode — not when "stand by" appears inside a narrative sentence ("…I told you to stand by so…"). ' +
			'Prefer THIS tool over inferring the mode from raw words — speech-to-text mangles phrases like "meeting mode".',
		parameters: z.object({ mode: z.enum(['active', 'meeting']) }),
		execution: 'inline',
		async execute(args) {
			const { mode } = args as { mode: 'active' | 'meeting' };
			// #1427 regime selection (Susan's 2026-06-09 spec: solo keeps the
			// verified-clean switch path untouched; group gets its own handler
			// rules). Group (≥2 humans) requires the bot to have been NAMED in
			// the last 10s for ANY mode switch — with several people talking, a
			// bare "meeting mode" / "wake up" heard in the room is not for us.
			// Which regime handled the switch is recorded for sqlite auditing.
			const _humans = opts.getHumanCount?.() ?? 1;
			const _regime = _humans <= 1 ? 'solo' : 'group';
			if (_regime === 'group') {
				const _namedFresh = Date.now() - ((s as any)._namedThisBotAt || 0) < 10_000;
				if (!_namedFresh) {
					console.log(`${ts()} [Meeting] switch_mode("${mode}") REFUSED (group regime) — bot not addressed by name`);
					return { status: 'ignored', instruction: 'Multiple people are in the channel and you were not addressed by name — that mode command was not meant for you. Stay exactly as you are; do not speak.' };
				}
			}
			try { recordEvent('discord-voice', 'mode_switch_regime', JSON.stringify({ regime: _regime, humans: _humans, requested: mode }), s.sessionId); } catch {}
			(s as any)._forceAudibleUntil = Date.now() + 6000;  // #1456: guarantee the mode-switch ack is heard (allowAck races)
			if (mode === 'meeting') {
				if (!s.meetingMode && !(s as any)._pendingMeeting) {
					(s as any)._pendingMeeting = true; s.allowAckAudible = true; (s as any)._ackEmitted = false;
					console.log(`${ts()} [Meeting] switch_mode → speak ack first, meeting pending`);
				}
				// #1456: do NOT tell Gemini it is going silent — when the
				// model thinks it is entering silent mode it emits the ack as TEXT-ONLY (no audio),
				// so the confirmation is never heard. Ask for a NORMAL spoken reply; the CODE
				// engages silence at turn.end (deferred _pendingMeeting), AFTER the ack is voiced.
				return { status: 'meeting_mode', instruction: 'Reply OUT LOUD with ONE short, normal sentence acknowledging you will take notes (e.g. "Got it, I\'ll take notes."). Speak it naturally — exactly as you would voice any other reply. Do NOT say you are going silent, stopping, or being quiet; just the brief spoken acknowledgement.' };
			}
			if (s.meetingMode || s.meetingEntered) {
				// #1427 over-fire guard: do NOT exit meeting mode just because the
				// bot's name was said. The audio classifier (above) sets _aiWakeAt
				// ONLY on a genuine WAKE — a greeting / re-establish-contact or a
				// by-name request to respond ("hi Lucy", "Lucy can you hear me",
				// "hi Lucy, <question>") — and NOT on a bare passing "Lucy" or a quiet
				// command ("Lucy, stay silent / take notes"). (Susan 2026-06-07: a name
				// alone is not a wake; "Lucy, stay silent" is the OPPOSITE of a wake —
				// the model kept flipping meeting→active on bare names and wouldn't stay
				// quiet.) Require a FRESH wake signal to leave meeting, so the model
				// can't pull itself out of the silence the user asked for.
				const _WAKE_FRESH_MS = Number(process.env.SUTANDO_WAKE_FRESH_MS) || 12000;
				const _wokenFresh = Date.now() - ((s as any)._aiWakeAt || 0) < _WAKE_FRESH_MS;
				if (!_wokenFresh) {
					console.log(`${ts()} [Meeting] switch_mode("active") REFUSED — no fresh wake signal (bare name / quiet command is not a wake)`);
					return { status: 'stayed_meeting', instruction: 'Stay silent and keep taking notes. The user has not greeted you or asked you anything to bring you active — they only mentioned your name or told you to stay quiet. Do NOT speak, do NOT call tools; keep listening.' };
				}
				s.meetingEntered = false; s.meetingMode = false; s.allowAckAudible = true; (s as any)._ackEmitted = false;
				try { recordConversation('discord-agent', '⇄ MODE → active (switch_mode tool)', s.sessionId, { speakerId: s.client.user?.id, speakerName: opts.standName || 'bot', speakerType: 'agent' }); } catch {}
				try { writeFileSync(opts.voiceModeFile, 'active'); } catch {}
				console.log(`${ts()} [Meeting] switch_mode tool → active`);
			}
			return { status: 'active_mode', instruction: 'Back to active mode. Respond normally and use tools as needed.' };
		},
	};
}

/**
 * dismiss — skill-local override: leave the Discord voice channel by SIGTERM-ing
 * the session (so cleanupSession runs). Controller-gated. Needs `s` + the controller
 * id (passed in to avoid a circular import). Body preserved verbatim.
 */
export function makeDismissTool(s: any, opts: { voiceController: string | undefined }): ToolDefinition {
	return {
		name: 'dismiss',
		description:
			'Leave the current Discord voice channel and exit the voice session. ' +
			'Use when user says "dismiss", "leave", "leave discord", "log off", "bye", "end this", "退出", "下线", "你走吧". ' +
			'NOT for ending an in-progress task or hanging up a phone call.',
		parameters: z.object({}),
		execution: 'inline',
		async execute() {
			// #1456: only the designated controller may dismiss the bot.
			// dismiss SIGTERMs the session out of the channel; the tier gate alone
			// is insufficient because a relay account (a peer bot via a relay user-id) is owner-tier
			// in the allowlist, so it (or a misfired tool-call) was making the bot
			// dismiss ITSELF mid-session (observed: the bot dismissed itself —
			// that's a bug). Mirror the meeting-mode controller-gate: require the
			// controller to be the sole human speaker of the current turn.
			if (opts.voiceController) {
				const _humans = [...s.turnSpeakers].filter(
					(id: string) => s.speakerNameCache.get(id)?.type !== 'agent');
				const _byController = _humans.includes(opts.voiceController);  // controller participated (not necessarily sole)
				if (!_byController) {
					console.log(`${ts()} [Dismiss] refused — not the controller (last=${s.lastSpeaker}, humans=${JSON.stringify(_humans)})`);
					return { status: 'refused', message: 'Only the controller can dismiss this bot.' };
				}
			}
			console.log(`${ts()} [Dismiss] Discord voice context — SIGTERM`);
			setTimeout(() => { try { process.kill(process.pid, 'SIGTERM'); } catch {} }, 400);
			return { status: 'left_discord_voice' };
		},
	};
}

/**
 * share_screen — stub: upstream sutando ships no screen-share impl (it lives in the
 * operator's private repo), so this always returns unavailable rather than letting
 * Gemini silently route "share my screen" to a sibling tool. No `s` needed.
 */
export const shareScreenTool: ToolDefinition = {
	name: 'share_screen',
	description:
		'Reply that screen share is NOT available in this build of sutando. ' +
		'Match for any "share screen" / "share my screen" / "screen share" / "屏幕共享" / "分享屏幕" utterance. ' +
		'In this (upstream) build the share-screen implementation is not installed; the tool always returns unavailable so the user gets an explicit message instead of a silent no-op.',
	parameters: z.object({}),
	execution: 'inline',
	async execute() {
		return { status: 'unavailable',
		         message: 'Screen share is not available in this build of sutando — the share-screen implementation lives in the operator\'s private repo and is not installed. Tell the user briefly that screen share is unavailable in this version.' };
	},
};
