# ROADMAP — Phase 2 (future hardware & weather)

Not implemented in Phase 1. Documented precisely so each drops in cleanly. In
the packages, the exact attach points are marked with `# ROADMAP` /
`# PHASE 2 SEAM` comments. **Ordered by safety value, not by discussion order.**

## Priority #1 — High-water float IN the pit (early warning)

The flood sensors sit **above the pit rim** — they only confirm flooding after
water reaches the floor. A float/level sensor partway up the pit (a float switch
on an ESP, or an Ecowitt WH55 on a future gateway) is the missing **early
warning**: it catches a stuck float *during* a storm with minutes to spare. This
is the single highest-value addition — higher than the weather station for
safety.

- Reserve `binary_sensor.sump_high_water`.
- Wire it as a **CRITICAL** alert **above** the rim-level flood alert.

## Priority #2 — Backup pump metering

The battery backup is unmonitored today. Add a CT on its circuit (if AC) or read
its controller output, exposing `binary_sensor.sump_backup_running`.

- A backup-ran event is itself **CRITICAL** — the primary failed silently and you
  are one backup failure from a flood.
- Until then there's no direct backup signal; the individual primary-fault
  alerts (no_run, continuous_run, current_load_low = running-but-not-pumping,
  flood) each page critically on their own as the proxy.

## Priority #3 — Weather station (context, not gating)

Ecowitt local integration: `binary_sensor.rain_recent` (WS90 piezo) + a rolling
rainfall total (WH40H).

- The sump `no_run_watchdog` does **not** need rain-gating — it cycles from
  baseline groundwater regardless of weather. Portability caveat: a sump *without*
  steady inflow would want an `AND NOT rain_recent` gate.
- Weather adds **context**: distinguish "short intervals because heavy rain
  (expected)" from "short intervals with little rain (leak / intrusion / failing
  check valve)" — exactly the ambiguity the idle-gap split sets up.
- New `sensor.sump_runtime_per_mm` (on-time normalized by rainfall) becomes the
  premier wear metric; a rising trend = pump moving less water per unit rain.
  Reserve the name.
- `binary_sensor.sump_high_frequency` (built but OFF in Phase 1) may return as a
  rain-normalized daily-count view.

## Optional dashboard enhancements (ApexCharts / HACS)

Not required; the delivered dashboard is core-only and complete. With ApexCharts:
a single-axis state+current overlay, mean±stddev duration bands and a true
duration histogram, and an **hour-of-day runs heatmap** (the diurnal-pattern view
core cards can't render). Entity names are already stable for pre-wiring.
