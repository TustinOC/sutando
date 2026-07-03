import { chromium } from 'playwright';
const b = await chromium.launch({ channel:'chrome', headless:true });
const pg = await (await b.newContext({ viewport:{width:1200,height:630}, deviceScaleFactor:1 })).newPage();
await pg.goto('file:///tmp/og.html', { waitUntil:'networkidle' });
await pg.screenshot({ path:'/tmp/bet-og.png' });
await b.close(); console.log('og card written');
