# Agile Tracker

Live Octopus Agile prices in a single static page: today's half-hourly chart with a "now" marker (UK time), day navigation, a 30-day overview extended by a 14-day price forecast, an overlay comparing other Octopus tariffs, and a plain-English explanation of why the day is cheap or expensive. An **Import / Export** toggle switches between Agile Octopus (what you pay) and Agile Outgoing (what you are paid to export). Light, dark or system theme; works on desktop and mobile.

**Live:** https://authenergy.github.io/agile-tracker/

It started as a personal tool and is experimental: provided as is, with no warranty, and not affiliated with Octopus Energy. No sign-up, no cookies, no analytics, no tracking.

## How it works

No backend for the page itself. `index.html` is the whole app, and everything is fetched directly in the browser:

- **Prices and tariffs:** Octopus Energy public API (`api.octopus.energy`). The live product is discovered by display name (Agile Octopus for import, Agile Outgoing Octopus for export), never hard-coded, as in [agile-compare](https://github.com/AuthEnergy/agile-compare). The tariff name and product code are shown at the top of the page.
- **Export view:** Agile Outgoing rates (no VAT), with colours reversed (green is a high rate), and a comparison against Outgoing Octopus, Flux Export, Prime Outgoing and the Smart Export Guarantee.
- **Why the price is high or low:** national generation mix and carbon intensity from the NESO Carbon Intensity API (`api.carbonintensity.org.uk`); regional weather from Open-Meteo (`api.open-meteo.com`).
- **14-day forecast:** a public JSON file (`https://dev.auth.energy/uk-forecast.json`, see below). The browser applies your region's formula to it.
- **Region:** auto-detected on first visit from your IP address (`ipapi.co`, falling back to `ipwho.is`) and mapped to a distribution region via Octopus's postcode lookup. Choose a region manually to override; the choice is remembered.

Prices and lookups are cached in `localStorage`, so repeat visits paint instantly. Nothing is sent to the author.

## The 14-day forecast (`server/`)

A small job (`server/outlook.py`, run from cron on a VPS) predicts Great Britain's hourly day-ahead wholesale price for the next 14 days with a gradient-boosted model trained on about two years of N2EX prices, NESO demand forecasts, Open-Meteo weather and National Gas SAP gas prices. It publishes one file, `uk-forecast.json`, containing the wholesale forecast with uncertainty bands, a fitted per-region formula for Agile import (`regions`) and for Agile Outgoing export (`export`), a health status, and measured accuracy. The browser downloads that file and does the regional maths. If the file is unavailable the page simply shows the 30-day history without a forecast. A health gate falls back to a naive forecast if the model stops beating it. Setup, cron and CORS notes are in [`server/DEPLOY.md`](server/DEPLOY.md). It is an estimate, not advice.

## Run locally

Open `index.html`, or serve the folder (`python3 -m http.server`). Add `?demo` to the URL for sample data, and `?mode=export` to open the Export view.

## Tests

- `npm test` runs the static checks (syntax, self-contained page, network allow-list, SEO and PWA essentials). It needs nothing installed.
- `npm run test:full` also runs the browser smoke tests with mocked APIs. These use Playwright, which is an optional dependency: `npm install && npx playwright install chromium`, then `npm run test:full`. Without Playwright, `node tests/smoke.js` skips itself.

## Deploy

Every push to `main` runs `.github/workflows/deploy.yml`, which publishes the site to GitHub Pages. Run `npm test` before pushing (the workflow does not run it). The forecast file is produced separately on the VPS (see `server/DEPLOY.md`).

## SEO and install

`index.html` carries a title, description, canonical URL, Open Graph and Twitter cards (`og-image.png`), JSON-LD (`WebApplication`, with a feature list and the repository) and an About section that is crawlable without JavaScript. Icons are `favicon.svg`, `apple-touch-icon.png` and PNGs for the web app manifest, which also defines Import and Export shortcuts, so the page can be installed to a phone home screen. The static check fails if any of these go missing.

After the first deploy: add `https://authenergy.github.io/agile-tracker/` in Google Search Console and Bing Webmaster Tools and submit `sitemap.xml`. Note that crawlers only read `robots.txt` at the root of `authenergy.github.io`, not from this project path.

## Licence

MIT, see [LICENSE](LICENSE).
