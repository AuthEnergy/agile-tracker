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
