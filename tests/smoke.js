// Browser smoke test: mocks every external API, loads the page, checks rendering, nav, theme, overflow and caching.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const PAGE_URL = 'file://' + path.join(__dirname, '..', 'index.html');
const NOW = Date.parse('2026-11-06T17:15:00Z');           // fixed "now": inside the 16:00-19:00 peak
const DATA_START = Date.parse('2026-10-01T00:00:00Z'), DATA_END = Date.parse('2026-11-07T23:59:00Z'); // tomorrow published
const H = { 'access-control-allow-origin': '*', 'content-type': 'application/json' };
const json = (r, o) => r.fulfill({ headers: H, body: JSON.stringify(o) });
fs.mkdirSync(path.join(__dirname, '..', 'test-results'), { recursive: true });

function price(t) { const hr = (t % 864e5) / 36e5, day = Math.floor(t / 864e5); return +((hr < 5 ? -3 : 15) + (hr >= 16 && hr < 19 ? 30 : 0) + Math.sin(day) * 4).toFixed(2); }
async function mock(page, counter) {
  await page.route('https://ipapi.co/**', r => { counter.n++; r.fulfill({ headers: H, body: JSON.stringify({ country_code: 'GB', postal: 'N15 4AB' }) }); });
  await page.route('https://ipwho.is/**', r => { counter.n++; r.fulfill({ headers: H, body: JSON.stringify({ success: true, country_code: 'GB', postal: 'N15 4AB' }) }); });
  await page.route('https://api.carbonintensity.org.uk/**', r => {
    counter.n++; const gen = r.request().url().includes('/generation/'), d = [];
    for (let i = 0; i < 48; i++) { const f = new Date(Date.parse('2026-11-06T00:00:00Z') + i * 1800e3).toISOString().slice(0, 16) + 'Z';
      d.push(gen ? { from: f, to: f, generationmix: [{ fuel: 'wind', perc: 30 }, { fuel: 'gas', perc: 30 }, { fuel: 'solar', perc: 5 }, { fuel: 'nuclear', perc: 14 }, { fuel: 'imports', perc: 9 }] }
                 : { from: f, to: f, intensity: { forecast: 150, actual: 140, index: 'moderate' } }); }
    json(r, { data: d }); });
  await page.route('https://api.open-meteo.com/**', r => { counter.n++; const times = [], z = [];
    for (let i = 0; i < 72; i++) { times.push(new Date(Date.parse('2026-11-05T12:00:00Z') + i * 3600e3).toISOString().slice(0, 13) + ':00'); z.push(5); }
    json(r, { hourly: { time: times, temperature_2m: z, wind_speed_100m: z, cloud_cover: z, shortwave_radiation: z } }); });
  await page.route('https://api.octopus.energy/**', r => {
    counter.n++; const u = r.request().url();
    if (u.includes('grid-supply-points')) return json(r, { results: [{ group_id: '_C' }] });
    if (u.includes('night-unit-rates')) return json(r, { results: [], next: null });
    if (u.includes('/standard-unit-rates/')) {
      if (u.includes('FLEX')) return json(r, { results: [{ valid_from: '2026-04-01T00:00:00Z', valid_to: null, value_inc_vat: 26.3, payment_method: 'DIRECT_DEBIT' }], next: null });
      const q = new URL(u).searchParams, from = Date.parse(q.get('period_from')), to = Date.parse(q.get('period_to')), res = [];
      for (let t = Math.max(from, DATA_START); t < to && t < DATA_END; t += 1800e3) res.push({ valid_from: new Date(t).toISOString(), valid_to: new Date(t + 1800e3).toISOString(), value_inc_vat: price(t) });
      return json(r, { results: res.reverse(), next: null }); }
    if (/\/products\/(FLEX|AGILE)[^/]*\/$/.test(u)) return json(r, { single_register_electricity_tariffs: { _C: { direct_debit_monthly: { code: u.includes('FLEX') ? 'E-1R-FLEX-C' : 'E-1R-AGILE-24-10-01-C' } } } });
    return json(r, { results: [{ code: 'AGILE-24-10-01', display_name: 'Agile Octopus', available_to: null, is_business: false, is_prepay: false }, { code: 'FLEX-1', display_name: 'Flexible Octopus', available_to: null, is_business: false, is_prepay: false }], next: null }); });
}

const scenarios = [
  { name: 'desktop-light', ctx: { viewport: { width: 1100, height: 900 }, colorScheme: 'light' } },
  { name: 'desktop-dark', ctx: { viewport: { width: 1100, height: 900 }, colorScheme: 'dark' } },
  { name: 'mobile-light', ctx: { viewport: { width: 375, height: 800 }, colorScheme: 'light', isMobile: true, hasTouch: true } },
  { name: 'mobile-dark', ctx: { viewport: { width: 375, height: 800 }, colorScheme: 'dark', isMobile: true, hasTouch: true } },
];
const failures = [];
const check = (cond, msg) => { if (!cond) { failures.push(msg); console.error('  FAIL:', msg); } };

(async () => {
  const browser = await chromium.launch();
  for (const sc of scenarios) {
    console.log('scenario', sc.name);
    const ctx = await browser.newContext(sc.ctx), page = await ctx.newPage(), counter = { n: 0 }, errs = [];
    page.on('pageerror', e => errs.push(e.message)); page.on('console', m => m.type() === 'error' && !/manifest/i.test(m.text()) && errs.push(m.text()));
    await page.addInitScript(`(()=>{const R=Date,off=${NOW}-R.now();class D extends R{constructor(...a){if(a.length===0)super(R.now()+off);else super(...a);}static now(){return R.now()+off}}window.Date=D;})()`);
    await mock(page, counter);
    await page.goto(PAGE_URL);
    try { await page.waitForFunction(() => /\d/.test(document.querySelector('#now').textContent), null, { timeout: 10000 }); await page.waitForTimeout(1200); }
    catch { check(false, `${sc.name}: price never rendered`); }
    const t = id => page.textContent(id);
    check(/^−?\d+\.\d\d$/.test(await t('#now')), `${sc.name}: current price not rendered (${await t('#now')})`);
    check((await t('#status')).length > 3, `${sc.name}: status chip empty`);
    check(await page.locator('#chart rect').count() >= 48, `${sc.name}: main chart has fewer than 48 bars`);
    check(await page.locator('#month rect').count() >= 28, `${sc.name}: 30-day chart has too few bars`);
    check(await page.inputValue('#cmpsel') === 'none', `${sc.name}: compare should default to none`);
    const p0 = await page.locator('#chart path').count();
    await page.selectOption('#cmpsel', 'Flexible Octopus');
    await page.waitForFunction(n => document.querySelectorAll('#chart path').length > n, p0, { timeout: 5000 }).catch(() => {});
    await page.waitForLoadState('networkidle').catch(() => {});
    check(await page.locator('#chart path').count() > p0, `${sc.name}: tariff overlay line missing after selecting a tariff`);
    { await page.locator('#month').scrollIntoViewIfNeeded(); const b = await page.locator('#month').boundingBox();
      await page.mouse.move(b.x + b.width * 0.3, b.y + b.height / 2); await page.mouse.move(b.x + b.width * 0.31, b.y + b.height / 2);
      const hov = await page.locator('#month rect[fill="none"]').count();
      check(hov >= 2, `${sc.name}: 30-day view should outline hovered day plus selected day (got ${hov})`);
      await page.mouse.move(b.x + b.width / 2, b.y - 40); }
    check(!(await page.locator('#why').isHidden()) && (await t('#whyh')).startsWith('Why today'), `${sc.name}: explanation missing`);
    check(await page.locator('#whymix .mixbar i').count() >= 3, `${sc.name}: generation mix bar missing`);
    check(await page.isEnabled('#nextd'), `${sc.name}: next-day should be enabled when tomorrow is published`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    check(overflow <= 1, `${sc.name}: horizontal overflow of ${overflow}px`);
    // navigation + theme
    await page.click('#prev'); await page.waitForTimeout(500);
    check((await t('#daytitle')) === 'Yesterday', `${sc.name}: prev day title is "${await t('#daytitle')}"`);
    await page.click('#todaybtn'); await page.waitForTimeout(300);
    check((await t('#daytitle')) === 'Today', `${sc.name}: Today button did not return to today`);
    const before = await page.evaluate(() => document.documentElement.dataset.eff);
    await page.click('#theme'); await page.waitForTimeout(200);
    check(before !== await page.evaluate(() => document.documentElement.dataset.eff), `${sc.name}: theme toggle had no effect`);
    await page.screenshot({ path: path.join(__dirname, '..', 'test-results', sc.name + '.png'), fullPage: true });
    check(errs.length === 0, `${sc.name}: page/console errors: ${errs.join(' | ')}`);
    if (sc.name === 'desktop-light') { // caching: a reload must paint without calling any API
      counter.n = 0; await page.reload(); await page.waitForFunction(() => /\d/.test(document.querySelector('#now').textContent), null, { timeout: 10000 }); await page.waitForTimeout(1500);
      check(counter.n === 0, `${sc.name}: reload made ${counter.n} network requests (expected 0, cached)`); }
    await ctx.close();
  }
  await browser.close();
  if (failures.length) { console.error(`\n${failures.length} smoke check(s) failed`); process.exit(1); }
  console.log('\nAll smoke checks passed');
})();
