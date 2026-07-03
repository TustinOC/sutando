// verify-cinny-render.mjs — "agent with eyes" for the AG2 Space CINNY FORK.
//
// Headless-loads the DEPLOYED cinny client (ag2.space) logged in AS an agent,
// opens a room, optionally switches a room view tab + drills into an artifact,
// then DOM-probes + screenshots — so a UI/UX change can be self-verified with
// MEASURED evidence (px geometry, element presence) instead of eyeballing, and
// WITHOUT waiting for a human to look and report back.
//
// This is the cinny-fork counterpart to verify-room-render.mjs. CRITICAL
// difference: the deployed client is the cinny fork, whose login session lives
// in `cinny_*` localStorage keys (src/app/state/sessions.ts), NOT Element's
// `mx_*` keys. Injecting mx_* (as the older harness does) does NOT log the fork
// in — that gap is why this script exists.
//
// Usage (run FROM the sutando repo so playwright resolves — it lives in
// sutando/node_modules, not cinny's):
//   MX_TOKEN=<agent matrix access token> \
//     npm run verify -- <roomId> [--tab Artifacts] \
//       [--artifact notes] [--md] [--selector .vault-md-body] [--viewport 1600] \
//       [--out /tmp/render.png] [--close-members]
//
// <roomId> accepts "!tehxp...:ag2.space", "!tehxp...", or the localpart "tehxp...".
//
// Token: the agent's Matrix access token (syt_…). Must belong to an agent that is
// a MEMBER of the room (e.g. @sutando-wu-air.agent for !tehxp; the broker's
// @sutando-qingyun agent is NOT in every room and can't render those). Source it
// from the box `relay-agents.json[<agent>].bot_token` via SSM, or any member's
// token. A deviceless token still renders the timeline (read-only is enough).
//
// Prints JSON: {found, viewport, bodyWidth, bodyLeft, maxBlockChildWidth,
//   textRightEdge, textFillOfBody, selectorText, errors, screenshot}.
// textFillOfBody < ~95 with a full-width container => a real render bug (text
// not reflowing to the container) — the exact class of bug the eyeball misses.
import { chromium } from 'playwright';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

function arg(name, def = null) {
  const i = process.argv.indexOf(name);
  return i > -1 ? (process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : true) : def;
}

const HS = process.env.MX_HS || 'https://chat.ag2.space';
const USER = process.env.MX_USER || '@sutando-wu-air.agent:ag2.space';
const TOKEN = process.env.MX_TOKEN;
const ROOM_RAW = process.argv[2];
if (!TOKEN || !ROOM_RAW || ROOM_RAW.startsWith('--')) {
  console.error('usage: MX_TOKEN=<tok> node verify-cinny-render.mjs <roomId> [--tab X] [--artifact name] [--md] [--selector S] [--viewport N] [--out path] [--close-members]');
  process.exit(2);
}
// normalise the room localpart for the href match cinny uses
const RID = ROOM_RAW.replace(/^!/, '').split(':')[0];

const TAB = arg('--tab');                 // Chat | Tasks | Agents | Artifacts
const ARTIFACT = arg('--artifact');       // folder/button text to click (e.g. "notes")
const WANT_MD = arg('--md') === true;     // drill into the first *.md file button
const SELECTOR = arg('--selector', '.vault-md-body');
const VW = parseInt(arg('--viewport', '1600'), 10);
const OUT = arg('--out', join(tmpdir(), 'sutando-screenshots', `cinny-render-${VW}.png`));
const CLOSE_MEMBERS = arg('--close-members') === true;

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await (await browser.newContext({ viewport: { width: VW, height: 1000 } })).newPage();
const errors = [];
page.on('pageerror', (e) => errors.push((e.stack || e.message || '').split('\n').slice(0, 2).join(' | ')));

// AS-the-agent login: cinny_* keys (NOT Element mx_*)
await page.addInitScript(([u, t, hs]) => {
  localStorage.setItem('cinny_hs_base_url', hs);
  localStorage.setItem('cinny_user_id', u);
  localStorage.setItem('cinny_device_id', 'CINNYVERIFY');
  localStorage.setItem('cinny_access_token', t);
}, [USER, TOKEN, HS]);

await page.goto('https://ag2.space/', { waitUntil: 'domcontentloaded', timeout: 45000 });
// Wait for initial sync to ACTUALLY finish before probing. The app shell
// ("Home"/"Explore") appears BEFORE sync completes, so the old check broke out
// early and probed mid-"Heating up"/"Connection Lost" → false negatives.
// Readiness = no sync/connection banner AND the target room link has appeared.
// Loop up to ~60s so a transient "Connection Lost" reconnect is ridden out.
let synced = false;
for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(1500);
  const t = await page.evaluate(() => document.body.innerText);
  const busy = /Connecting|Heating up|Connection Lost|Syncing/i.test(t);
  const roomLinkReady = await page.locator(`a[href*="${RID}"]`).count().catch(() => 0);
  if (!busy && roomLinkReady > 0) { synced = true; break; }
}
if (!synced) errors.push('sync-wait: never reached a synced state with the room link visible (timed out ~60s)');
await page.locator(`a[href*="${RID}"]`).first().click({ timeout: 8000 }).catch(() => {});
// After entering the room, poll for the banner to clear instead of a fixed sleep.
for (let i = 0; i < 12; i++) {
  await page.waitForTimeout(1000);
  const busy = await page.evaluate(() => /Connecting|Heating up|Connection Lost|Syncing/i.test(document.body.innerText));
  if (!busy) break;
}

if (TAB) {
  await page.locator(`button:has-text("${TAB}"), [aria-label="${TAB}"]`).first().click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(4000);
}
if (ARTIFACT) {
  await page.locator(`button:has-text("${ARTIFACT}")`).first().click({ timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(3000);
}
if (WANT_MD) {
  await page.evaluate(() => { const f = [...document.querySelectorAll('button')].find((b) => /\.md$/.test((b.textContent || '').trim())); if (f) f.click(); });
  await page.waitForTimeout(4000);
}
if (CLOSE_MEMBERS) {
  // top-right narrow toggle = members panel; close it so the measure reflects the real content allotment
  await page.evaluate((vw) => { const x = [...document.querySelectorAll('button,[role=button]')].find((el) => { const r = el.getBoundingClientRect(); return r.left > vw - 120 && r.top < 70 && r.width < 60; }); if (x) x.click(); }, VW);
  await page.waitForTimeout(1500);
}

const diag = await page.evaluate((sel) => {
  const b = document.querySelector(sel);
  if (!b) return { found: false, selector: sel };
  const rect = b.getBoundingClientRect();
  const bw = Math.round(rect.width), bleft = rect.left;
  let maxChildW = 0, maxTextRight = 0;
  b.querySelectorAll('p,h1,h2,h3,li,td').forEach((el) => { maxChildW = Math.max(maxChildW, Math.round(el.getBoundingClientRect().width)); });
  const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (!node.textContent.trim()) continue;
    const r = document.createRange(); r.selectNodeContents(node);
    for (const rc of r.getClientRects()) { if (rc.width > 1) maxTextRight = Math.max(maxTextRight, rc.right); }
  }
  b.style.outline = '3px solid red'; // make the measured box visible in the shot
  return {
    found: true, selector: sel, viewport: window.innerWidth,
    bodyWidth: bw, bodyLeft: Math.round(bleft),
    maxBlockChildWidth: maxChildW, textRightEdge: Math.round(maxTextRight),
    textFillOfBody: bw ? Math.round(((maxTextRight - bleft) / bw) * 100) : null,
    selectorText: b.innerText.replace(/\s+/g, ' ').slice(0, 200),
  };
}, SELECTOR);

await page.screenshot({ path: OUT });
console.log(JSON.stringify({ synced, ...diag, errors: errors.slice(0, 5), screenshot: OUT }, null, 2));
await browser.close();
