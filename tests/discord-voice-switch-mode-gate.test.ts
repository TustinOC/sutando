/**
 * #1600 M4 — gate the model's spurious switch_mode("meeting") + solo bare-name
 * wake. 2026-06-10 live test: 24s into an ACTIVE session, with the user only
 * saying "Lucy, 能听到吗? Okay.", the model self-called switch_mode→meeting and
 * muted the user; then a bare "Lucy" couldn't wake it back out. These pin both
 * halves: meeting entry now requires a real user cue, and a solo bare name
 * counts as a wake out of meeting mode.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { enterMeetingKeyword } from '../skills/discord-voice/scripts/speak-gate.ts';
import { executeSoloSwitch } from '../skills/discord-voice/scripts/discord-voice-tools.ts';

const OPTS = { voiceModeFile: '/tmp/__test_voice_mode_m4.txt', standName: 'Lucy', nameAliases: ['Loosey', '露西'] };
const mkSession = (over = {}) => ({
	meetingMode: false, meetingEntered: false, allowAckAudible: false,
	sessionId: 'test', client: { user: { id: 'bot' } },
	_recentUserSpeech: [], _humanCount: 1,
	...over,
});
const now = () => Date.now();

describe('#1600 M4 — enterMeetingKeyword', () => {
	it('true on explicit meeting / silent / notes cues', () => {
		assert.equal(enterMeetingKeyword(['can you take notes']), true);
		assert.equal(enterMeetingKeyword(['Lucy, go silent please']), true);
		assert.equal(enterMeetingKeyword(['进入会议模式']), true);
		assert.equal(enterMeetingKeyword(['安静一下']), true);
		assert.equal(enterMeetingKeyword(['stand by for a sec']), true);
	});
	it('false on the spurious-fire context (no meeting cue at all)', () => {
		assert.equal(enterMeetingKeyword(['Lucy, 能听到吗?', 'Okay.']), false);
		assert.equal(enterMeetingKeyword(['show me the dog breeds']), false);
		assert.equal(enterMeetingKeyword([]), false);
	});
});

describe('#1600 M4 — switch_mode("meeting") requires a user cue', () => {
	it('REFUSED when the model self-fires with no meeting cue', () => {
		const s = mkSession({ _recentUserSpeech: [{ text: 'Lucy, 能听到吗?', at: now() }, { text: 'Okay.', at: now() }] });
		const r = executeSoloSwitch(s, 'meeting', OPTS);
		assert.equal(r.status, 'stayed_active', 'spurious meeting fire is refused');
		assert.equal(s.meetingMode, false);
		assert.equal((s as any)._pendingMeeting ?? false, false, 'no pending meeting engaged');
	});
	it('engaged when the user actually asked to take notes / go silent', () => {
		const s = mkSession({ _recentUserSpeech: [{ text: 'Lucy, can you take notes for this meeting', at: now() }] });
		const r = executeSoloSwitch(s, 'meeting', OPTS);
		assert.equal(r.status, 'meeting_mode', 'legit meeting request goes through');
		assert.equal((s as any)._pendingMeeting, true);
	});
});

describe('#1600 M4 — solo bare-name wakes out of meeting mode', () => {
	it('a bare "Lucy" exits meeting (solo)', () => {
		const s = mkSession({ meetingMode: true, meetingEntered: true, _recentUserSpeech: [{ text: 'Lucy', at: now() }] });
		const r = executeSoloSwitch(s, 'active', OPTS);
		assert.equal(r.status, 'active_mode', 'bare name is a valid solo wake');
		assert.equal(s.meetingMode, false);
	});
	it('a CJK bare name "露西" also wakes (solo)', () => {
		const s = mkSession({ meetingMode: true, meetingEntered: true, _recentUserSpeech: [{ text: '露西', at: now() }] });
		assert.equal(executeSoloSwitch(s, 'active', OPTS).status, 'active_mode');
	});
	it('a passing mention (not a bare name, no wake) is still REFUSED', () => {
		const s = mkSession({ meetingMode: true, meetingEntered: true, _recentUserSpeech: [{ text: 'I was telling Lucy about the thing', at: now() }] });
		const r = executeSoloSwitch(s, 'active', OPTS);
		assert.equal(r.status, 'stayed_meeting', 'passing mention does not wake — no false exit');
		assert.equal(s.meetingMode, true);
	});
});
