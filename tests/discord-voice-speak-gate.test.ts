// Population-aware speak gate (#1427, Susan's 2026-06-09 spec). Locks the
// regime matrix: solo behavior is FROZEN (active → always answer, meeting →
// addressed-only — tonight's verified-clean 1:1 path), group (≥2 humans)
// requires addressed-or-sticky in BOTH modes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decideSpeak, regimeFor } from '../skills/discord-voice/scripts/speak-gate.js';

const base = { humanCount: 1, meetingMode: false, addressedToMe: false, allowAck: false, forceAudible: false };

test('regimeFor: ≤1 human is solo, ≥2 is group', () => {
	assert.equal(regimeFor(0), 'solo');
	assert.equal(regimeFor(1), 'solo');
	assert.equal(regimeFor(2), 'group');
	assert.equal(regimeFor(5), 'group');
});

test('solo active: always speak (frozen 1:1 behavior)', () => {
	const d = decideSpeak({ ...base });
	assert.equal(d.speak, true);
	assert.equal(d.regime, 'solo');
	assert.equal(d.reason, 'solo-active');
	// even unaddressed — utterance end means "answer me" with one human
	assert.equal(decideSpeak({ ...base, addressedToMe: false }).speak, true);
});

test('solo meeting: addressed-only (frozen name-gate behavior)', () => {
	assert.equal(decideSpeak({ ...base, meetingMode: true }).speak, false);
	assert.equal(decideSpeak({ ...base, meetingMode: true, addressedToMe: true }).speak, true);
});

test('group active: unaddressed chatter stays silent — the new rule', () => {
	const d = decideSpeak({ ...base, humanCount: 2 });
	assert.equal(d.speak, false);
	assert.equal(d.regime, 'group');
	assert.equal(d.reason, 'group-active-unaddressed');
});

test('group active: addressed (or sticky) speaks', () => {
	assert.equal(decideSpeak({ ...base, humanCount: 2, addressedToMe: true }).speak, true);
	assert.equal(decideSpeak({ ...base, humanCount: 3, addressedToMe: true }).speak, true);
});

test('group meeting: addressed-only, same as solo meeting', () => {
	assert.equal(decideSpeak({ ...base, humanCount: 2, meetingMode: true }).speak, false);
	assert.equal(decideSpeak({ ...base, humanCount: 2, meetingMode: true, addressedToMe: true }).speak, true);
});

test('ack and force-audible always pass in every regime', () => {
	for (const humanCount of [1, 2]) {
		for (const meetingMode of [false, true]) {
			assert.equal(decideSpeak({ ...base, humanCount, meetingMode, allowAck: true }).speak, true);
			assert.equal(decideSpeak({ ...base, humanCount, meetingMode, forceAudible: true }).speak, true);
		}
	}
});

test('every decision carries an audit reason', () => {
	for (const humanCount of [1, 2]) {
		for (const meetingMode of [false, true]) {
			for (const addressedToMe of [false, true]) {
				const d = decideSpeak({ ...base, humanCount, meetingMode, addressedToMe });
				assert.ok(d.reason.length > 0);
			}
		}
	}
});

// --- soloExitKeyword (#1427 round-3 fix: solo exit needs a mode word, not a name) ---
import { soloExitKeyword } from '../skills/discord-voice/scripts/speak-gate.js';

test('soloExitKeyword: explicit mode words pass without a name', () => {
	assert.equal(soloExitKeyword(['active mode']), true);
	assert.equal(soloExitKeyword(['okay, active mode.']), true);
	assert.equal(soloExitKeyword(['can you wake up please']), true);
	assert.equal(soloExitKeyword(['退出会议模式']), true);
	assert.equal(soloExitKeyword(['chatter', 'more chatter', 'stop meeting mode']), true);
});

test('soloExitKeyword: passing name-mention without a mode word stays refused', () => {
	assert.equal(soloExitKeyword(['Lucy 刚才说的这个问题']), false);
	assert.equal(soloExitKeyword(['so as Lucy mentioned earlier']), false);
	assert.equal(soloExitKeyword(['we were quite active yesterday']), false);
	assert.equal(soloExitKeyword([]), false);
	assert.equal(soloExitKeyword(['']), false);
});

// --- isStopWord (#1427: interrupt word, only checked while bot is speaking) ---
import { isStopWord } from '../skills/discord-voice/scripts/speak-gate.js';

test('isStopWord: short stop commands fire', () => {
	assert.equal(isStopWord('stop'), true);
	assert.equal(isStopWord('Stop!'), true);
	assert.equal(isStopWord('okay stop'), true);
	assert.equal(isStopWord('stop talking'), true);
	assert.equal(isStopWord('闭嘴'), true);
	assert.equal(isStopWord('别说了'), true);
	assert.equal(isStopWord('停'), true);
});

test('isStopWord: narrative speech containing stop does not fire', () => {
	assert.equal(isStopWord('we should stop by the store later tonight on the way'), false);
	assert.equal(isStopWord('I could not stop thinking about that PR review yesterday'), false);
	assert.equal(isStopWord(''), false);
	assert.equal(isStopWord('keep going please'), false);
});

// --- shouldResilenceAtTurnEnd (#1427 single-turn summon: meeting answers 1 turn → re-silence) ---
import { shouldResilenceAtTurnEnd } from '../skills/discord-voice/scripts/speak-gate.js';

test('shouldResilenceAtTurnEnd: meeting + addressed + no ack → re-silence (one-shot summon)', () => {
	assert.equal(shouldResilenceAtTurnEnd(true, false, true), true);
});
test('shouldResilenceAtTurnEnd: active mode never re-silences (sticky is intentional)', () => {
	assert.equal(shouldResilenceAtTurnEnd(false, false, true), false);
});
test('shouldResilenceAtTurnEnd: meeting but not addressed → nothing to reset', () => {
	assert.equal(shouldResilenceAtTurnEnd(true, false, false), false);
});
test('shouldResilenceAtTurnEnd: ack in flight → do not cut the ack turn', () => {
	assert.equal(shouldResilenceAtTurnEnd(true, true, true), false);
});
