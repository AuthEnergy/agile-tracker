// Static gate for the single-file app: syntax, no external resources, network allow-list, size, required elements.
const fs = require('fs');
const html = fs.readFileSync(require('path').join(__dirname, '..', 'index.html'), 'utf8');
const errs = [];

// 1. inline scripts parse
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if (!scripts.length) errs.push('no inline <script> found');
scripts.forEach((m, i) => { try { new Function(m[1]); } catch (e) { errs.push(`inline script #${i + 1} has a syntax error: ${e.message}`); } });

// 2. self-contained: no external scripts or stylesheets, no CSS imports/remote urls
if (/<script[^>]*\ssrc=/i.test(html)) errs.push('external <script src> found; the page must stay self-contained');
if (/<link[^>]*rel=["']?stylesheet/i.test(html)) errs.push('external stylesheet found; the page must stay self-contained');
if (/@import|url\(\s*["']?https?:/i.test(html)) errs.push('remote CSS import/url() found');

// 3. network allow-list: every absolute URL host must be known
const ALLOWED = new Set([
  'api.octopus.energy', 'api.carbonintensity.org.uk', 'api.open-meteo.com', 'ipapi.co', 'ipwho.is', // data
  'www.w3.org', 'github.com', 'authenergy.github.io',                                             // namespaces / links
]);
const hosts = new Set([...html.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)].map(m => m[1].toLowerCase()));
for (const h of hosts) if (!ALLOWED.has(h)) errs.push(`unexpected host in index.html: ${h} (add it to tools/check.js ALLOWED if intended)`);

// 4. size budget
const kb = Buffer.byteLength(html) / 1024;
if (kb > 200) errs.push(`index.html is ${kb.toFixed(0)} KB (budget 200 KB)`);

// 5. required elements
for (const id of ['now', 'status', 'chart', 'month', 'why', 'region', 'cmpsel', 'prev', 'nextd', 'todaybtn', 'theme'])
  if (!new RegExp(`id=["']${id}["']`).test(html)) errs.push(`missing element #${id}`);

if (errs.length) { console.error('Static checks FAILED:\n - ' + errs.join('\n - ')); process.exit(1); }
console.log(`Static checks passed (${kb.toFixed(1)} KB, hosts: ${[...hosts].join(', ')})`);
