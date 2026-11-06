# Agile Tracker

Live Octopus Agile prices in a single static page: today's half-hourly chart with a "now" marker (UK time), day navigation, a 30-day overview, an overlay comparing other Octopus tariffs, and a plain-English explanation of why the day is cheap or expensive. Light and dark themes; works on desktop and mobile.

**Live:** https://authenergy.github.io/agile-tracker/

## How it works

No backend. `index.html` is the whole app, and everything is fetched directly in the browser:

- **Prices and tariffs:** Octopus Energy public API (`api.octopus.energy`). The live Agile product is discovered by display name, as in [agile-compare](https://github.com/AuthEnergy/agile-compare).
- **Why the price is high or low:** national generation mix and carbon intensity from the NESO Carbon Intensity API (`api.carbonintensity.org.uk`); regional weather from Open-Meteo (`api.open-meteo.com`).
- **Region:** auto-detected on first visit from your IP address (`ipapi.co`, falling back to `ipwho.is`) and mapped to a distribution region via Octopus's postcode lookup. Choose a region manually to override; the choice is remembered.

Prices and lookups are cached in `localStorage`, so repeat visits paint instantly. Nothing is sent to the author.

## Run locally

Open `index.html`, or serve the folder (`python3 -m http.server`). Add `?demo` to the URL for sample data.

## CI/CD

Every push to `main` (and every pull request) runs `.github/workflows/ci.yml`:

1. **Static checks** (`npm run check`): inline JS parses, no external scripts or stylesheets, every URL host is on an allow-list (privacy gate), size under 200 KB, required elements present.
2. **Smoke tests** (`npm test`): Playwright loads the page with all APIs mocked, in desktop and mobile, light and dark. It checks the price, chart, 30-day view, tariff overlay, explanation and mix bar render, day navigation and theme toggle work, there's no horizontal overflow or console errors, and a reload makes zero network requests.
3. **Deploy** (main only, after the checks pass): publishes `index.html` to GitHub Pages.

Run the same checks locally: `npm ci && npx playwright install chromium && npm run ci`. Screenshots from each CI run are attached as an artifact.

## SEO and install

`index.html` carries a title, description, canonical URL, Open Graph and Twitter cards (`og-image.png`), JSON-LD (`WebApplication`) and an FAQ that is crawlable without JavaScript. Icons are `favicon.svg`, `apple-touch-icon.png` and PNGs for the web app manifest, so the page can be installed to a phone home screen. The static check fails the build if any of these go missing.

After the first deploy: add `https://authenergy.github.io/agile-tracker/` in Google Search Console and Bing Webmaster Tools and submit `sitemap.xml`. Note that crawlers only read `robots.txt` at the root of `authenergy.github.io`, not from this project path.
