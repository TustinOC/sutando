import { chromium } from 'playwright';
const url = process.argv[2], out = process.argv[3];
const b = await chromium.launch({ channel: 'chrome', headless: true });
const pg = await (await b.newContext({ viewport:{width:720,height:1280}, deviceScaleFactor:2 })).newPage();
const errs=[]; pg.on('pageerror',e=>errs.push(e.message)); pg.on('console',m=>{if(m.type()==='error')errs.push(m.text());});
await pg.goto(url,{waitUntil:'networkidle',timeout:30000});
try{ await pg.fill('#name','Chi'); await pg.click('button.go'); }catch{}
await pg.waitForTimeout(2500);
const cards=await pg.$$eval('.card',e=>e.length), opts=await pg.$$eval('.opt',e=>e.length);
const firstQ=await pg.$$eval('.q',e=>e.map(x=>x.textContent)).catch(()=>[]);
console.log('CARDS:',cards,'OPTIONS:',opts,'JS_ERRORS:',errs.length, errs.slice(0,2).join(' | '));
console.log('questions:', JSON.stringify(firstQ));
await pg.screenshot({path:out,fullPage:true}); await b.close();
