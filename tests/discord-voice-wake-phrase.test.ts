// Deterministic meeting→active wake matcher (#1427). These lock the exact
// live-test failures from 2026-06-09: "Hi Lucy, wake up" / "Hi Lucy, can you
// hear me?" left the bot stuck in meeting mode because (1) the LLM audio
// classifier missed wake=true and (2) the fixed-phrase fallback compared raw
// punctuated STT text with `includes('hi lucy')`. isWakePhrase normalizes
// punctuation and is OR-ed with the classifier, so either signal wakes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isWakePhrase, normalizeSpoken } from '../skills/discord-voice/scripts/name-gate.js';

// Same env-driven identity convention as discord-voice-namegate.test.ts —
// no real bot identity baked into the test source.
const SELF = process.env.SUTANDO_STAND_NAME || 'self';
const ALIAS = (process.env.SUTANDO_STAND_ALIASES || 'selfie').split(',')[0].trim();
const NAMES = [SELF, ALIAS];

// --- normalizeSpoken -------------------------------------------------------
test('normalizeSpoken: strips punctuation, collapses spaces, lowercases', () => {
	assert.equal(normalizeSpoken('Hi, Lucy!  Wake   up.'), 'hi lucy wake up');
	assert.equal(normalizeSpoken("Lucy? Can't you hear me?"), 'lucy can t you hear me');
});
test('normalizeSpoken: CJK survives', () => {
	assert.equal(normalizeSpoken('会议模式。'), '会议模式');
});

// --- the live-test failures (punctuated STT) -------------------------------
test('wake: punctuated greeting + wake up (the stuck-in-meeting repro)', () => {
	assert.equal(isWakePhrase(`Hi ${SELF}, wake up.`, NAMES), true);
	assert.equal(isWakePhrase(`Hi, ${SELF}! Wake up!`, NAMES), true);
	assert.equal(isWakePhrase(`${SELF}, wake up`, NAMES), true);
});
test('wake: can-you-hear-me / are-you-there re-establish contact', () => {
	assert.equal(isWakePhrase(`Hi ${SELF}, can you hear me?`, NAMES), true);
	assert.equal(isWakePhrase(`${SELF}, can you hear me`, NAMES), true);
	assert.equal(isWakePhrase(`${SELF}, are you there?`, NAMES), true);
	assert.equal(isWakePhrase(`${SELF}, you there?`, NAMES), true);
});
test('wake: other deliberate forms', () => {
	assert.equal(isWakePhrase(`wake up, ${SELF}`, NAMES), true);
	assert.equal(isWakePhrase(`${SELF}, you can talk now`, NAMES), true);
	assert.equal(isWakePhrase(`okay ${SELF}, resume`, NAMES), true);
	assert.equal(isWakePhrase(`${SELF}, come back`, NAMES), true);
	assert.equal(isWakePhrase(`${SELF} active mode`, NAMES), true);
	assert.equal(isWakePhrase('stop meeting mode', NAMES), true);
});
test('wake: ASR alias works like the stand name', () => {
	assert.equal(isWakePhrase(`hi ${ALIAS}, wake up`, NAMES), true);
	assert.equal(isWakePhrase(`hey ${ALIAS}`, NAMES), true);
});

// --- must NOT wake ----------------------------------------------------------
test('no-wake: bare verb without the name (channel-echo over-fire guard)', () => {
	assert.equal(isWakePhrase("I'm in active mode now", NAMES), false);
	assert.equal(isWakePhrase('wake up everyone', NAMES), false);
	assert.equal(isWakePhrase('let me switch to active mode', NAMES), false);
});
test('no-wake: bare name / passing mention', () => {
	assert.equal(isWakePhrase(`${SELF}.`, NAMES), false);
	assert.equal(isWakePhrase(`thanks ${SELF}`, NAMES), false);
	assert.equal(isWakePhrase(`I told ${SELF} about it yesterday`, NAMES), false);
});
test('no-wake: quiet commands are not wakes', () => {
	assert.equal(isWakePhrase(`${SELF}, stay silent`, NAMES), false);
	assert.equal(isWakePhrase(`${SELF}, take notes`, NAMES), false);
});
test('no-wake: name as prefix of a longer word (word boundary)', () => {
	// e.g. alias "may" must not fire inside "maybe wake up"
	assert.equal(isWakePhrase('maybe wake up later', ['may']), false);
});
test('no-wake: empty / no names', () => {
	assert.equal(isWakePhrase('', NAMES), false);
	assert.equal(isWakePhrase(`hi ${SELF}, wake up`, []), false);
});
