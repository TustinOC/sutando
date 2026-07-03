// verify-room-render.mjs — "agent with eyes": render the deployed Element as
// the agent, screenshot a room, and probe the DOM — so visual fixes can be
// self-verified WITHOUT a human looking and reporting back (the blocker that
// makes agent sessions stall). Born 2026-06-11 debugging the task-room card.
//
// Usage (from the sutando repo so playwright resolves):
//   MX_TOKEN=<agent matrix access token> node skills/task-room/verify-room-render.mjs <!roomId:server> [checkElementId]
// Get the token (deviceless admin login-as token works for non-E2EE read):
//   ssh EC2 → relay-agents.json[<agent>].bot_token   (or any member's token)
// Prints JSON: {cardPresent/elementFound, text, scriptSrc, errors, screenshot}.
//
// NOTE: a deviceless token lands on Element's "Verify this device" banner but
// the room timeline still renders — fine for read-only visual checks.
import { chromium } from 'playwright';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const HS = process.env.MX_HS || 'https://chat.ag2.space';
const USER = process.env.MX_USER || '@sutando-wu-air.agent:ag2.space';
const TOKEN = process.env.MX_TOKEN;
const ROOM = process.argv[2];
const CHECK_ID = process.argv[3] || 'taskroom-card-root';
if (!TOKEN || !ROOM) {
  console.error('usage: MX_TOKEN=<tok> node verify-room-render.mjs <!room:server> [elementId]');
  process.exit(2);
}
const out = join(tmpdir(), 'sutando-screenshots', 'room-render.png');

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const errors = [];
page.on('pageerror', (e) => errors.push((e.stack || e.message || '').split('\n').slice(0, 2).join(' | ')));

await page.addInitScript(([hs, user, token]) => {
  localStorage.setItem('mx_hs_url', hs);
  localStorage.setItem('mx_user_id', user);
  localStorage.setItem('mx_access_token', token);
  localStorage.setItem('mx_device_id', 'VERIFYAGENT');
}, [HS, USER, TOKEN]);

await page.goto(HS.replace('chat.', '') + '/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(async () => {
  await page.goto('https://ag2.space/', { waitUntil: 'domcontentloaded', timeout: 45000 });
});
await page.waitForTimeout(6000);
await page.goto('https://ag2.space/#/room/' + encodeURIComponent(ROOM), { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(10000);

const probe = await page.evaluate((id) => {
  const el = document.getElementById(id);
  const scr = (Array.from(document.scripts).find((s) => /ag2-taskroom|ag2-agent-info/.test(s.src)) || {}).src;
  return { elementFound: !!el, text: el ? el.innerText.replace(/\s+/g, ' ').slice(0, 240) : null, scriptSrc: scr };
}, CHECK_ID);

await page.screenshot({ path: out });
console.log(JSON.stringify({ ...probe, errors: errors.slice(0, 5), screenshot: out }, null, 2));
await browser.close();
