/**
 * #1600 — Susan's one-layer rule (2026-06-10: "switch mode应该只有一层，就是
 * user说了没有"): in SOLO, the user's actual speech is the ONLY authority for
 * entering meeting mode. The model self-fired switch_mode("meeting") whenever
 * the conversation merely DISCUSSED modes (16:31 / 16:39 live drops); the code
 * now validates a model-fired meeting switch against recent user speech and
 * refuses when no cue was said. Matcher is CJK-adjacency-aware so an English
 * cue inside a Chinese sentence counts — the round-1 M4 bug refused Susan's
 * legit "你能進到meeting mode嗎" (15:02:29).
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { enterMeetingKeyword, soloExitKeyword } from '../skills/discord-voice/scripts/speak-gate.ts';
import { executeSoloSwitch } from '../skills/discord-voice/scripts/discord-voice-tools.ts';

const OPTS = { voiceModeFile: '/tmp/__test_voice_mode_guard.txt', standName: 'Lucy' };
const mkSession = (over = {}) => ({
	meetingMode: false, meetingEntered: false, allowAckAudible: false,
	sessionId: 'test', client: { user: { id: 'bot' } },
	_recentUserSpeech: [], _humanCount: 1,
	...over,
});
const now = () => Date.now();

describe('#1600 one-layer — enterMeetingKeyword', () => {
	it('true on explicit meeting / silent / notes cues', () => {
		assert.equal(enterMeetingKeyword(['enter meeting mode, Lucy']), true);
		assert.equal(enterMeetingKeyword(['can you take notes']), true);
		assert.equal(enterMeetingKeyword(['进入会议模式']), true);
		assert.equal(enterMeetingKeyword(['安静一下']), true);
	});
	it('true on an English cue EMBEDDED in a CJK sentence (round-1 M4 bug)', () => {
		assert.equal(enterMeetingKeyword(['你能進到meeting mode嗎']), true);
		assert.equal(enterMeetingKeyword(['Okay, 你能 進到 meeting mode嗎？']), true);
	});
	it('false on the real spurious-fire contexts (no cue said)', () => {
		assert.equal(enterMeetingKeyword(['把1602关了就行了', '刚才是不是要关1601咱还没关呢']), false);
		assert.equal(enterMeetingKeyword(['能看到吗?']), false);
		assert.equal(enterMeetingKeyword([]), false);
	});
	it('does not match inside larger English words', () => {
		assert.equal(enterMeetingKeyword(['the submeeting moderator spoke']), false);
	});
});

describe('#1600 one-layer — solo switch_mode("meeting") requires the user to have said it', () => {
	it('REFUSED when the model self-fires with no user cue', () => {
		const s = mkSession({ _recentUserSpeech: [{ text: '把1602关了就行了', at: now() }, { text: '能看到吗?', at: now() }] });
		const r = executeSoloSwitch(s, 'meeting', OPTS);
		assert.equal(r.status, 'stayed_active');
		assert.equal(s.meetingMode, false);
		assert.equal((s as any)._pendingMeeting ?? false, false, 'no pending meeting engaged');
		assert.equal((s as any)._forceAudibleUntil ?? 0, 0, 'refusal must NOT open a talk window (15:37:48 leak)');
	});
	it('engaged on a real request, including CJK-embedded phrasing', () => {
		const s = mkSession({ _recentUserSpeech: [{ text: '你能進到meeting mode嗎', at: now() }] });
		const r = executeSoloSwitch(s, 'meeting', OPTS);
		assert.equal(r.status, 'meeting_mode');
		assert.equal((s as any)._pendingMeeting, true);
		assert.ok(((s as any)._forceAudibleUntil ?? 0) > now() - 1000, 'ack window armed on the proceed path');
	});
});

describe('#1600 — soloExitKeyword CJK-adjacency (same matcher)', () => {
	it('exit cue embedded in CJK counts', () => {
		assert.equal(soloExitKeyword(['切回active mode吧']), true);
		assert.equal(soloExitKeyword(['退出会议']), true);
	});
	it('no cue → false', () => {
		assert.equal(soloExitKeyword(['他在active的时候活跃过头']), false, 'bare "active" without "mode" is not an exit cue');
		assert.equal(soloExitKeyword(['随便聊聊别的']), false);
		assert.equal(soloExitKeyword([]), false);
	});
});
