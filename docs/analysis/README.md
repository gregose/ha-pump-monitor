# Task A — threshold seeding from history

`seed_thresholds.py` derives the adaptive no-run clamps from a pump's own
recorded on/off history, so the watchdog starts from **measured** cadence
instead of the guessed ~30 min (plan §6c).

## 1. Export the history CSV

In Home Assistant → **Developer Tools → History**, select
`binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running`, set the
longest range you have (ideally 2–4 weeks), then use the **Download Data**
button and save it here as `sump_history.csv`. Repeat for the ejector
(`..._ejector_pump_running`) if you want to seed it too.

Any CSV with a timestamp column (`last_changed` / `last_updated`) and a `state`
column of `on`/`off` works — column order and extra columns don't matter.

## 2. Run

```bash
python3 seed_thresholds.py sump_history.csv --pump sump
```

It prints the measured cadence (median / p05 / p99 interval, run-duration
spread, median idle gap) and a paste-ready block of `input_number` initials.

## 3. Apply

- Paste the suggested `*_no_run_ceiling_min` / `*_no_run_floor_min` initials into
  `packages/pump_monitor_helpers.yaml` (or set them live in the UI).
- Flip `input_boolean.<pump>_no_run_history_ready` → **ON** to switch the no-run
  watchdog from the 90-min warm-up fallback to adaptive (3× live 14-day median,
  clamped by those floor/ceiling values).
- Leave `*_short_interval_seconds` at its physical default — the data-driven
  part is handled live by `sensor.<pump>_interval_p05_14d`; see the note the
  script prints.

Commit the CSV + the resulting helper values so the seeding is reproducible.

## Rain correlation (preview of the Phase 2 weather seam)

`rain_correlation.py` correlates the sump's cycling with public hourly rainfall
(Open-Meteo, no API key) — a stand-in weather station until the GW3001 + WH40BH
arrive. It reuses the same CSV, pulls precip for the export's date range, and
reports:

- **rain → cycling correlation** with the best lag (rain leads cycling by the
  soil-percolation time);
- a **dry-weather vs storm cadence split** — the dry median is the *true*
  baseline for the no-run watchdog (the Task A seed is storm-blended and runs a
  bit fast);
- a **rain dose-response** (starts/hr by hourly intensity);
- **fast-cycling-without-rain** episodes over a conservative ~12h drainage window
  (the leak / check-valve / intrusion signature — groundwater recedes for hours
  after rain, so a short window mislabels storm tails as leaks).

```bash
python3 rain_correlation.py sump_history.csv           # set --lat/--lon to your location
python3 rain_correlation.py sump_history.csv --lat 41.88 --lon -87.63 --tz America/Chicago
```

Needs `api.open-meteo.com` reachable (in the sandbox: `sbx policy allow network
api.open-meteo.com,archive-api.open-meteo.com`). A ~30-day export spanning
several storms and multi-day dry spells makes the dry baseline and any true leak
signature far clearer than a short window can.

> The CSV contains only pump on/off timestamps — no secrets — so it is safe to
> commit. Do **not** commit anything from `/config/secrets.yaml`, `.storage/`,
> or the recorder DB.
