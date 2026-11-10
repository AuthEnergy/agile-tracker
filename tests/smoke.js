// Browser smoke test: mocks every external API, loads the page, checks rendering, nav, theme, overflow and caching.
// Playwright is an optional dependency: without it this test is skipped (exit 0) unless REQUIRE_BROWSER=1.
let chromium;
try { ({ chromium } = require('playwright')); }
catch { skipOrFail('playwright is not installed. Run "npm install" (or "npm i playwright" then "npx playwright install chromium") to run the browser tests.'); }
function skipOrFail(msg) { if (process.env.REQUIRE_BROWSER) { console.error('FAIL: ' + msg); process.exit(1); } console.log('SKIPPED browser tests: ' + msg); process.exit(0); }
const path = require('path');
const fs = require('fs');
const PAGE_URL = 'file://' + path.join(__dirname, '..', 'index.html');
const NOW = Date.parse('2026-11-06T17:15:00Z');           // fixed "now": inside the 16:00-19:00 peak
const DATA_START = Date.parse('2026-10-01T00:00:00Z'), DATA_END = Date.parse('2026-11-07T23:59:00Z'); // tomorrow published
const H = { 'access-control-allow-origin': '*', 'content-type': 'application/json' };
const json = (r, o) => r.fulfill({ headers: H, body: JSON.stringify(o) });
fs.mkdirSync(path.join(__dirname, '..', 'test-results'), { recursive: true });

function price(t) { const hr = (t % 864e5) / 36e5, day = Math.floor(t / 864e5); return +((hr < 5 ? -3 : 15) + (hr >= 16 && hr < 19 ? 30 : 0) + Math.sin(day) * 4).toFixed(2); }
function forecastFile() {
  const days = [], hours = [], p50 = [];
  for (let d = 0; d < 13; d++) { const day0 = Date.parse('2026-11-08T00:00:00Z') + d * 864e5, mean = 150 + 25 * Math.sin(d / 2) + (d > 8 ? 30 : 0);
    days.push({ date: new Date(day0).toISOString().slice(0, 10), h: d + 2, mean, p10: mean - 20, p90: mean + 20, published: false });
    for (let h = 0; h < 24; h++) { hours.push(new Date(day0 + h * 36e5).toISOString()); p50.push(mean + (h >= 16 && h < 20 ? 30 : h < 6 ? -25 : 0)); } }
  const regions = {}, exp = {}; for (const r of 'ABCDEFGHJKLMNP') { regions[r] = { m: 1.2, adder: 12, c: 3, cap: 100 }; exp[r] = { m: 0.97, adder: 0, c: 0.4, cap: null, floor: null }; }
  return { version: 1, issue_date: '2026-11-06', generated: '2026-11-06T11:30:00Z', status: 'ok', notes: [], days, wholesale: { hours_utc: hours, p50 }, regions, export: exp,
    accuracy: { backtest: [{ h: 1, daily_mae_pct: 12.4, trend_hit_pct: 90 }, { h: 14, daily_mae_pct: 18.3, trend_hit_pct: 70 }], logged: [] } };
}
async function mock(page, counter, noForecast) {
  await page.route('https://far.cx/uk-forecast.json', r => noForecast ? r.fulfill({ status: 404, body: 'nope' }) : json(r, forecastFile()));
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
      for (let t = Math.max(from, DATA_START); t < to && t < DATA_END; t += 1800e3) res.push({ valid_from: new Date(t).toISOString(), valid_to: new Date(t + 1800e3).toISOString(), value_inc_vat: u.includes('OUTGOING') ? +(price(t) / 2.5).toFixed(2) : price(t) });
      return json(r, { results: res.reverse(), next: null }); }
    if (/\/products\/(FLEX|AGILE|FLUX)[^/]*\/$/.test(u)) return json(r, { single_register_electricity_tariffs: { _C: { direct_debit_monthly: { code: u.includes('FLEX') ? 'E-1R-FLEX-C' : 'E-1R-AGILE-24-10-01-C' } } } });
    return json(r, { results: [{ code: 'AGILE-24-10-01', display_name: 'Agile Octopus', full_name: 'Agile Octopus October 2024 v1', available_to: null, is_business: false, is_prepay: false }, { code: 'FLEX-1', display_name: 'Flexible Octopus', available_to: null, is_business: false, is_prepay: false }, { code: 'AGILE-OUTGOING-19-05-13', display_name: 'Agile Outgoing Octopus', full_name: 'Agile Outgoing Octopus May 2019', direction: 'EXPORT', available_to: null, is_business: false, is_prepay: false }, { code: 'FLUX-EXPORT-23-02-14', display_name: 'Octopus Flux Export', direction: 'EXPORT', available_to: null, is_business: false, is_prepay: false }], next: null }); });
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
  let browser; try { browser = await chromium.launch(); } catch (e) { skipOrFail('could not start Chromium (' + String(e.message).split('\n')[0] + '). Run "npx playwright install chromium".'); }
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
    check(await page.locator('#month rect').count() >= 40, `${sc.name}: 30-day + 14-day chart has too few bars (${await page.locator('#month rect').count()})`);
    check(await page.locator('#month rect[fill-opacity]').count() >= 12, `${sc.name}: forecast bars (faded) missing`);
    check(!(await page.locator('#outlook').isHidden()) && /Prices look set to|No clear rise/.test(await t('#outlook')), `${sc.name}: outlook summary missing (${await t('#outlook')})`);
    check((await t('#monthtitle')).includes('outlook'), `${sc.name}: month title should mention the outlook`);
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
      await page.mouse.move(b.x + b.width / 2, b.y - 40);
      // touch scrub: lifting the finger on a bar must switch the viewed day
      const before = await t('#daytitle');
      await page.locator('#month').evaluate((el, [x, y]) => { const o = { pointerId: 7, pointerType: 'touch', isPrimary: true, bubbles: true, clientX: x, clientY: y };
        el.dispatchEvent(new PointerEvent('pointerdown', { ...o, clientX: x + 80 })); el.dispatchEvent(new PointerEvent('pointermove', o)); el.dispatchEvent(new PointerEvent('pointerup', o)); }, [b.x + b.width * 0.5, b.y + b.height / 2]);
      await page.waitForFunction(v => document.querySelector('#daytitle').textContent !== v, before, { timeout: 5000 }).catch(() => {});
      check((await t('#daytitle')) !== before, `${sc.name}: touch-lift on a 30-day bar should change the viewed day`);
      await page.locator('#todaybtn').click().catch(() => {}); }
    check(!(await page.locator('#why').isHidden()) && (await t('#whyh')).startsWith('Why today'), `${sc.name}: explanation missing`);
    check(/Next two weeks/.test(await t('#whyl')), `${sc.name}: Why box should carry a one-line forecast comment (${await t('#whyl')})`);
    check(await page.locator('#month rect[fill="var(--accent)"]').count() === 1, `${sc.name}: today's bar should be blue (accent) in the 30-day chart`);
    check(await page.locator('details:has-text("How is the 14-day forecast calculated?")').count() === 1, `${sc.name}: forecast explainer missing from About`);
    check(/Agile Octopus October 2024 v1 · AGILE-24-10-01 · region C/.test(await t('#tname')), `${sc.name}: tariff name missing at the top (${await t('#tname')})`);
    check(await page.locator('details:has-text("Source code and feedback") a[href="https://github.com/AuthEnergy/agile-tracker"]').count() === 1, `${sc.name}: About should link to the repo`);
    check(await page.locator('#whymix .mixbar i').count() >= 3, `${sc.name}: generation mix bar missing`);
    check(await page.isEnabled('#nextd'), `${sc.name}: next-day should be enabled when tomorrow is published`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    check(overflow <= 1, `${sc.name}: horizontal overflow of ${overflow}px`);
    // navigation + theme
    await page.click('#prev'); await page.waitForTimeout(500);
    check((await t('#daytitle')) === 'Yesterday', `${sc.name}: prev day title is "${await t('#daytitle')}"`);
    await page.click('#todaybtn'); await page.waitForTimeout(300);
    check((await t('#daytitle')) === 'Today', `${sc.name}: Today button did not return to today`);
    { // forecast days open in the 24h chart; < Today > work across history and forecast
      await page.locator('#month').scrollIntoViewIfNeeded(); const b = await page.locator('#month').boundingBox();
      await page.waitForTimeout(700);                       // the page ignores a click right after a touch-lift
      if (sc.ctx.hasTouch) await page.touchscreen.tap(b.x + b.width * 0.995, b.y + b.height / 2); else await page.mouse.click(b.x + b.width * 0.995, b.y + b.height / 2);
      await page.waitForFunction(() => /forecast/.test(document.querySelector('#daysub').textContent), null, { timeout: 5000 }).catch(() => {});
      check(/forecast/.test(await t('#daysub')), `${sc.name}: clicking a forecast day should show it in the 24h chart (sub: ${await t('#daysub')})`);
      check(await page.locator('#chart rect[fill-opacity]').count() >= 40, `${sc.name}: forecast 24h chart should have faded half-hour bars`);
      check(/FORECAST/.test(await page.locator('#chart').textContent()), `${sc.name}: forecast chart should be labelled`);
      check(await page.isDisabled('#nextd'), `${sc.name}: next should be disabled on the last forecast day`);
      await page.click('#prev'); await page.waitForTimeout(300);
      check(/forecast/.test(await t('#daysub')), `${sc.name}: previous from the last forecast day should stay in the forecast`);
      check(await page.isEnabled('#nextd'), `${sc.name}: next should work after going back one forecast day`);
      await page.click('#nextd'); await page.waitForTimeout(300);
      check(await page.isDisabled('#nextd'), `${sc.name}: next returns to the last forecast day`);
      await page.click('#todaybtn'); await page.waitForTimeout(400);
      check((await t('#daytitle')) === 'Today' && !/forecast/.test(await t('#daysub')), `${sc.name}: Today should return from a forecast day`);
      await page.locator('#chart').scrollIntoViewIfNeeded();
      await page.mouse.click(b.x + b.width * 0.2, b.y + b.height / 2).catch(() => {});
    }
    { // theme cycles System -> Light -> Dark -> System; only Light/Dark are saved
      const st = () => page.evaluate(() => ({ pref: document.documentElement.dataset.pref, eff: document.documentElement.dataset.eff, theme: document.documentElement.dataset.theme || null, saved: localStorage.getItem('theme') }));
      const s0 = await st(); check(s0.pref === 'system' && s0.saved === null, `${sc.name}: theme should start on System (${JSON.stringify(s0)})`);
      await page.click('#theme'); await page.waitForTimeout(150); const s1 = await st();
      check(s1.pref === 'light' && s1.saved === 'light' && s1.eff === 'light', `${sc.name}: first click should choose Light (${JSON.stringify(s1)})`);
      await page.click('#theme'); await page.waitForTimeout(150); const s2 = await st();
      check(s2.pref === 'dark' && s2.saved === 'dark' && s2.eff === 'dark', `${sc.name}: second click should choose Dark (${JSON.stringify(s2)})`);
      await page.click('#theme'); await page.waitForTimeout(150); const s3 = await st();
      check(s3.pref === 'system' && s3.saved === null && s3.theme === null && s3.eff === s0.eff, `${sc.name}: third click should return to System, following the device (${JSON.stringify(s3)})`);
      check(/System/.test(await page.getAttribute('#theme', 'title')), `${sc.name}: theme button should say which mode is active`);
      await page.click('#theme'); await page.waitForTimeout(150); // leave an explicit choice so the screenshot below covers a forced theme too
    }
    await page.screenshot({ path: path.join(__dirname, '..', 'test-results', sc.name + '.png'), fullPage: true });
    check(errs.length === 0, `${sc.name}: page/console errors: ${errs.join(' | ')}`);
    if (sc.name === 'desktop-light') { // caching: a reload must paint without calling any API
      for (let attempt = 0; attempt < 2; attempt++) { // a first reload may still be topping up the cache; the second must be fully cached
        counter.n = 0; await page.reload(); await page.waitForFunction(() => /\d/.test(document.querySelector('#now').textContent), null, { timeout: 10000 }); await page.waitForTimeout(1500);
        if (counter.n === 0) break; }
      check(counter.n === 0, `${sc.name}: reload made ${counter.n} network requests (expected 0, cached)`); }
    await ctx.close();
  }
  { // forecast file unreachable: the page must fall back to the plain 30-day chart without errors
    const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } }), page = await ctx.newPage(), errs = [];
    page.on('pageerror', e => errs.push(e.message));
    await page.addInitScript(`(()=>{const R=Date,off=${NOW}-R.now();class D extends R{constructor(...a){if(a.length===0)super(R.now()+off);else super(...a);}static now(){return R.now()+off}}window.Date=D;})()`);
    await mock(page, { n: 0 }, true); await page.goto(PAGE_URL);
    await page.waitForFunction(() => /\d/.test(document.querySelector('#now').textContent), null, { timeout: 10000 }); await page.waitForTimeout(1500);
    check(await page.locator('#month rect[fill-opacity]').count() === 0, 'nofc: no forecast bars expected');
    check(await page.locator('#outlook').isHidden(), 'nofc: outlook should be hidden');
    check((await page.textContent('#monthtitle')) === 'Last 30 days', 'nofc: title should stay "Last 30 days"');
    check(errs.length === 0, 'nofc: page errors ' + errs.join('|')); await ctx.close();
  }
  { // export view (Agile Outgoing): toggle, reversed colours, no VAT, forecast from the export formula, persistence
    const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } }), page = await ctx.newPage(), errs = [];
    page.on('pageerror', e => errs.push(e.message));
    await page.addInitScript(`(()=>{const R=Date,off=${NOW}-R.now();class D extends R{constructor(...a){if(a.length===0)super(R.now()+off);else super(...a);}static now(){return R.now()+off}}window.Date=D;})()`);
    await mock(page, { n: 0 }); await page.goto(PAGE_URL);
    await page.waitForFunction(() => /\d/.test(document.querySelector('#now').textContent), null, { timeout: 10000 }); await page.waitForTimeout(1200);
    const t = id => page.textContent(id), impNow = parseFloat((await t('#now')).replace('−', '-'));
    check(await page.getAttribute('#m-imp', 'aria-pressed') === 'true', 'export: import should be the default view');
    await page.click('#m-exp');
    await page.waitForFunction(() => /export/i.test(document.querySelector('#status').textContent), null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(1200);
    const expNow = parseFloat((await t('#now')).replace('−', '-'));
    check(await page.getAttribute('#m-exp', 'aria-pressed') === 'true', 'export: toggle should show export pressed');
    check(/export/i.test(await t('#status')), `export: status should speak of export rates (${await t('#status')})`);
    check(expNow > 0 && expNow < impNow, `export: export rate (${expNow}) should differ from, and be below, the import price (${impNow})`);
    check(/no VAT/.test(await t('#daysub')) && /no VAT/.test(await t('#nowslot')), 'export: labels should say no VAT');
    { const opts = await page.locator('#cmpsel option').allTextContents();
      check(opts.includes('Octopus Flux Export') && opts.includes('Outgoing Octopus') && !opts.includes('Cosy Octopus'), `export: compare list should hold export tariffs only (${opts.join(', ')})`);
      const p0 = await page.locator('#chart path').count();
      await page.selectOption('#cmpsel', 'Octopus Flux Export');
      await page.waitForFunction(n => document.querySelectorAll('#chart path').length > n, p0, { timeout: 5000 }).catch(() => {});
      check(await page.locator('#chart path').count() > p0, 'export: choosing an export tariff should draw the comparison line');
      await page.selectOption('#cmpsel', 'none'); await page.waitForTimeout(300); }
    check((await t('#whyh')).startsWith('Why export rates'), `export: Why box heading (${await t('#whyh')})`);
    check(/Best 2 hours to export/.test(await t('#whyl')), `export: Why box should name the best export window (${await t('#whyl')})`);
    check(/Excellent/.test(await t('#legend')), 'export: legend should be the export one');
    check(/Agile Outgoing Octopus May 2019 · AGILE-OUTGOING-19-05-13/.test(await t('#tname')), `export: tariff name at the top (${await t('#tname')})`);
    check(await page.locator('#chart rect').count() >= 48, 'export: main chart should still have 48 bars');
    check(await page.locator('#month rect[fill-opacity]').count() >= 12, 'export: forecast bars missing in the 30-day chart');
    check(/Best export day ahead/.test(await t('#outlook')), `export: outlook wording (${await t('#outlook')})`);
    { await page.locator('#month').scrollIntoViewIfNeeded(); const b = await page.locator('#month').boundingBox(); await page.waitForTimeout(700);
      await page.mouse.click(b.x + b.width * 0.995, b.y + b.height / 2);
      await page.waitForFunction(() => /forecast/.test(document.querySelector('#daysub').textContent), null, { timeout: 5000 }).catch(() => {});
      check(/forecast/.test(await t('#daysub')) && /no VAT/.test(await t('#daysub')), `export: forecast day should open in the 24h chart (${await t('#daysub')})`); }
    await page.reload(); await page.waitForFunction(() => /\d/.test(document.querySelector('#now').textContent), null, { timeout: 10000 });
    check(await page.getAttribute('#m-exp', 'aria-pressed') === 'true', 'export: choice should persist across a reload');
    await page.click('#m-imp'); await page.waitForTimeout(1200);
    check(!/export/i.test(await t('#status')) && (await t('#whyh')).startsWith('Why today'), 'export: switching back should restore the import view');
    { const opts = await page.locator('#cmpsel option').allTextContents();
      check(opts.includes('Cosy Octopus') && !opts.includes('Octopus Flux Export'), `export: import compare list should return (${opts.join(', ')})`); }
    check(Math.abs(parseFloat((await t('#now')).replace('−', '-')) - impNow) < 0.01, 'export: import price should be unchanged after toggling back');
    check(errs.length === 0, 'export: page errors ' + errs.join('|')); await ctx.close();
  }
  await browser.close();
  if (failures.length) { console.error(`\n${failures.length} smoke check(s) failed`); process.exit(1); }
  console.log('\nAll smoke checks passed');
})();
