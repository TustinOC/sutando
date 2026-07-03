import { chromium } from 'playwright';
const rid=process.argv[2];
const url=`https://ag2.space/bet/${rid}?widgetId=t1&roomId=${encodeURIComponent('!x:ag2.space')}&userId=${encodeURIComponent('@qingyun:ag2.space')}&displayName=qingyun`;
const b=await chromium.launch({channel:'chrome',headless:true});
const pg=await (await b.newContext()).newPage();
await pg.goto(url,{waitUntil:'domcontentloaded'}); await pg.waitForTimeout(2500);
await pg.$$eval('.opt',els=>els[0]&&els[0].click());   // click first option
await pg.waitForTimeout(1500);
const first=await pg.$$eval('.opt',els=>els[0]?.textContent||'');
const meCls=await pg.$$eval('.opt.me',els=>els.length);
console.log('after click → .me count:',meCls,'| first opt text:',JSON.stringify(first.slice(0,40)));
await b.close();
