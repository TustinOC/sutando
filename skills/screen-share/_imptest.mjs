import { chromium } from 'playwright';
const b = await chromium.launch({ channel:'chrome', headless:true });
const pg = await (await b.newContext()).newPage();
await pg.goto('https://ag2.space/bet/og.png',{waitUntil:'domcontentloaded'}).catch(()=>{});
for (const spec of ['https://esm.sh/matrix-widget-api@1.10.0','https://esm.sh/matrix-widget-api@1.10.0?bundle','https://esm.sh/matrix-widget-api']) {
  const r = await pg.evaluate(async (s)=>{ try{ const m=await import(s); return {ok:1, keys:Object.keys(m).slice(0,12), WidgetApi:typeof m.WidgetApi, def:typeof m.default, defKeys:(m.default&&typeof m.default==='object')?Object.keys(m.default).slice(0,12):null, defWA:(m.default&&typeof m.default.WidgetApi)}; }catch(e){ return {ok:0, err:String(e)}; } }, spec);
  console.log(spec, '=>', JSON.stringify(r));
}
await b.close();
