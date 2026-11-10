# Deploying the 14-day outlook on the VPS

Files needed in one folder (e.g. `/opt/agile-outlook`): `outlook.py backtest.py fetch_inputs.py check_elexon_price.py`.

```
sudo apt install python3-pandas python3-numpy python3-sklearn python3-certifi   # or pip install pandas numpy scikit-learn
mkdir -p /opt/agile-outlook/{cache,logs}
```

## One-off: seed the history (from your Mac, ~100 MB, saves re-downloading 2 years)
```
rsync -av ~/github/auth.energy/agile-tracker/server/cache/ vps:/opt/agile-outlook/cache/
```
(includes n2ex, demand archive, 2 years of Open-Meteo, sap.csv and the backtest files used for the uncertainty bands).

## Test
```
cd /opt/agile-outlook && python3 outlook.py --cache cache --logs logs --out /tmp/uk-forecast.json
```

## Cron (UK time; N2EX day-ahead lands about 12:45, NESO demand about 09:45)
```
CRON_TZ=Europe/London
30 5,11,16 * * *  cd /opt/agile-outlook && /usr/bin/python3 outlook.py --cache cache --logs logs --out /var/www/far.cx/uk-forecast.json >> run.log 2>&1
```
Only the first run of each day writes a new log in `logs/`; later runs overwrite that day's file with a better one.

## Apache: public + CORS for the page
```
<Location "/uk-forecast.json">
    Header set Access-Control-Allow-Origin "https://authenergy.github.io"
    Header set Cache-Control "public, max-age=900"
</Location>
```
`sudo a2enmod headers && sudo systemctl reload apache2`

## Agile Outgoing (export) formula
`outlook.py` also fits the Agile Outgoing export formula per region (`export` block in the JSON, `_export` in `cache/regions.json`).
It reuses the same wholesale forecast, so the model and cron job are unchanged. Deploying just means copying the new `outlook.py`;
the first run after that adds ~40 Octopus API calls to fit the export block, then it refits weekly with the import fit.
If the export fit fails the import forecast is unaffected (the page shows no export forecast).

## Health
`status` in the JSON is `ok` or `degraded` (a feed failed, or the 6-week health gate failed and the naive forecast is published). `notes` says why.
Measured accuracy (`accuracy.logged`) replaces the backtest estimate per horizon once 30 scored days exist.
