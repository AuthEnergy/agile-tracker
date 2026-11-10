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
  'api.octopus.energy', 'api.carbonintensity.org.uk', 'api.open-meteo.com', 'ipapi.co', 'ipwho.is', 'far.cx',          // data (far.cx = the daily 14-day outlook file)
  'www.w3.org', 'schema.org', 'github.com', 'authenergy.github.io', 'auth.energy',                     // namespaces / links
]);
const hosts = new Set([...html.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)].map(m => m[1].toLowerCase()));
for (const h of hosts) if (!ALLOWED.has(h)) errs.push(`unexpected host in index.html: ${h} (add it to tools/check.js ALLOWED if intended)`);

// 4. SEO / PWA essentials
const dir = require('path').join(__dirname, '..');
const need = (re, msg) => { if (!re.test(html)) errs.push(msg); };
const title = (html.match(/<title>([^<]*)<\/title>/) || [])[1] || '';
if (title.length < 20 || title.length > 70) errs.push(`<title> should be 20-70 chars (is ${title.length})`);
const desc = (html.match(/<meta name="description" content="([^"]*)"/) || [])[1] || '';
if (desc.length < 70 || desc.length > 170) errs.push(`meta description should be 70-170 chars (is ${desc.length})`);
need(/<html[^>]*\slang=/, '<html lang> missing');
need(/<link rel="canonical" href="https:\/\/[^"]+"/, 'canonical link missing');
need(/<meta property="og:title"/, 'og:title missing'); need(/<meta property="og:description"/, 'og:description missing');
need(/<meta property="og:image" content="https:\/\/[^"]+\.png"/, 'og:image missing'); need(/<meta name="twitter:card"/, 'twitter:card missing');
need(/<link rel="icon" href="favicon\.svg"/, 'favicon link missing'); need(/<link rel="apple-touch-icon"/, 'apple-touch-icon missing');
need(/<link rel="manifest" href="manifest\.webmanifest"/, 'manifest link missing'); need(/<meta name="theme-color"/, 'theme-color missing');
need(/<script type="application\/ld\+json">/, 'JSON-LD missing');
if ((html.match(/<h1[\s>]/g) || []).length !== 1) errs.push('page must have exactly one <h1>');
try { JSON.parse((html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/) || [])[1]); } catch (e) { errs.push('JSON-LD is not valid JSON'); }
for (const f of ['favicon.svg', 'apple-touch-icon.png', 'icon-192.png', 'icon-512.png', 'icon-maskable-512.png', 'og-image.png', 'manifest.webmanifest', 'sitemap.xml'])
  if (!fs.existsSync(require('path').join(dir, f))) errs.push(`missing asset: ${f}`);
try { const mf = JSON.parse(fs.readFileSync(require('path').join(dir, 'manifest.webmanifest'), 'utf8')); (mf.icons || []).forEach(i => { if (!fs.existsSync(require('path').join(dir, i.src))) errs.push(`manifest icon not found: ${i.src}`); }); } catch (e) { errs.push('manifest.webmanifest invalid: ' + e.message); }

// 5. size budget
const kb = Buffer.byteLength(html) / 1024;
if (kb > 200) errs.push(`index.html is ${kb.toFixed(0)} KB (budget 200 KB)`);

// 6. required elements
for (const id of ['now', 'status', 'chart', 'month', 'why', 'region', 'cmpsel', 'prev', 'nextd', 'todaybtn', 'theme'])
  if (!new RegExp(`id=["']${id}["']`).test(html)) errs.push(`missing element #${id}`);

if (errs.length) { console.error('Static checks FAILED:\n - ' + errs.join('\n - ')); process.exit(1); }
console.log(`Static checks passed (${kb.toFixed(1)} KB, hosts: ${[...hosts].join(', ')})`);
