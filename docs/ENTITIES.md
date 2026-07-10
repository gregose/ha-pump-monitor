# ENTITIES — data dictionary

Every derived entity, for **both pumps**. Each row exists twice: once as
`…sump_…` and once as `…ejector_…` (the ejector mirrors the sump with CT1 and its
own baselines). The `{pump}` placeholder below stands for `sump` or `ejector`.
Per-pump differences are called out in the [§6a table](#6a-per-pump-differences).

Entity IDs are the slug of the entity name; every derived entity also carries a
stable `pm_{pump}_…` `unique_id` so it is registry-tracked and renameable.

## Layer 0 — inputs (existing; read-only, never recreated)

| Entity ID | Meaning |
|-----------|---------|
| `sensor.energy_meter_eth_ws_f3a598_ct2_amps` | Sump current (CT2) |
| `sensor.energy_meter_eth_ws_f3a598_ct1_amps` | Ejector current (CT1) |
| `binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running` | Sump running (>~1 A, ~1 Hz damped) |
| `binary_sensor.circuitsetup_energy_meter_f3a598_ejector_pump_running` | Ejector running |
| `binary_sensor.0x282c02bfffefb418_water_leak` | Sump flood sensor (above rim) |
| `binary_sensor.water_b3eb_water_leak` | Ejector flood sensor (above rim) |
| `sensor.0x282c02bfffefb418_battery` | Sump flood-sensor battery |
| `sensor.water_b3eb_battery` | Ejector flood-sensor battery |

## Layer 1 — per-cycle (trigger + regular template sensors)

| Entity ID (`sensor.{pump}_…`) | Type | Unit | Source(s) | Meaning |
|---|---|---|---|---|
| `{pump}_last_start_time` | timestamp | — | running edge off→on | Last start time (restart-robust anchor) |
| `{pump}_last_stop_time` | timestamp | — | running edge on→off | Last stop time |
| `{pump}_last_run_duration` | duration | s | on→off edge | How long the last run lasted (pump output proxy) |
| `{pump}_interval_between_starts` | duration | s | off→on edge | Start-to-start gap (idle + run blended) |
| `{pump}_idle_gap` | duration | s | prev stop → next start | **Inflow proxy** — how long the pit sat empty |
| `{pump}_inflow_index` | measurement | /h | idle_gap | Refills/hour ≈ 3600/idle_gap — storm-signature trace |
| `{pump}_run_peak_current` | current | A | CT while running | Per-run peak current (jam / load) |
| `{pump}_run_current_active` | current | A | CT (only while running) | Clean current feed for baselines (idle 0 A excluded) |
| `{pump}_time_since_last_run` | duration | s | now() − last_start | Drives the no-run watchdog; re-evals ~1/min |
| `{pump}_lifetime_runtime` | duration (total_increasing) | s | Σ run durations | Cumulative runtime odometer (wear) |
| `{pump}_duty_cycle_24h` | measurement | % | ontime_24h/24 | 24h duty cycle — "keeping up vs losing" |

## Layer 2 — aggregates

`history_stats` (counts / on-time):

| Entity ID (`sensor.{pump}_…`) | Type | Unit | Window | Meaning |
|---|---|---|---|---|
| `{pump}_runs_1h` | count | starts | 1 h | Starts in the last hour |
| `{pump}_runs_10m` | count | starts | 10 min | Short-cycle window (feeds short_cycling) |
| `{pump}_runs_24h` | count | starts | 24 h | Starts in the last day |
| `{pump}_ontime_24h` | time | h | 24 h | On-time in the last day (duty-cycle base) |

`statistics` (self-tuning baselines):

| Entity ID (`sensor.{pump}_…`) | Characteristic | Source | Age | Meaning |
|---|---|---|---|---|
| `{pump}_duration_mean` | mean | last_run_duration | 14 d | Typical run length |
| `{pump}_duration_stddev` | standard_deviation | last_run_duration | 14 d | Run-length spread (z-score, floored) |
| `{pump}_interval_mean` | mean | interval_between_starts | 14 d | Mean cadence |
| `{pump}_interval_stddev` | standard_deviation | interval_between_starts | 14 d | Cadence spread |
| `{pump}_interval_median_14d` | median | interval_between_starts | 14 d | **Adaptive no-run baseline** (skew-robust) |
| `{pump}_interval_p05_14d` | percentile 5 | interval_between_starts | 14 d | Data-driven "too short" floor |
| `{pump}_idle_gap_median_14d` | median | idle_gap | 14 d | Typical inflow gap |
| `{pump}_current_baseline_30d` | mean | run_current_active | 30 d | Long current baseline |
| `{pump}_current_short_7d` | mean | run_current_active | 7 d | Recent current window (drift) |
| `{pump}_current_stddev_30d` | standard_deviation | run_current_active | 30 d | Current spread |

Lifetime odometers:

| Entity ID | Type | Meaning |
|---|---|---|
| `counter.{pump}_lifetime_cycles` | counter (+1 per on→off) | Cumulative cycle count (replacement prediction; user-resettable on pump swap) |
| `sensor.{pump}_lifetime_runtime` | total_increasing | Cumulative runtime (see Layer 1) |

## Layer 3 — health / anomaly (pure template binary sensors)

All read helpers so thresholds tune live; all availability-safe; stddev floored.

| Entity ID (`binary_sensor.{pump}_…`) | Fires when | Signature |
|---|---|---|
| `{pump}_run_duration_anomaly` | last duration outside mean ± z·max(stddev, 2) | odd run length |
| `{pump}_short_interval` | interval < max(fixed floor, live p05) | overwhelm / fast refill |
| `{pump}_short_cycling` | runs_10m > max_starts | stuck float / undersized |
| `{pump}_continuous_run` | on AND elapsed > max_run_seconds | stuck float / inflow ≥ outflow |
| `{pump}_current_load_high` | current_short_7d > baseline_30d·(1+drift%) | wear / partial clog |
| `{pump}_current_load_low` | current_short_7d < baseline_30d·low% | sheared impeller / airlock / cavitation / stuck valve |
| `{pump}_current_spike` | run_peak_current > spike_a (0 = disabled) | jam / locked rotor |
| `{pump}_high_duty_cycle` | duty_cycle_24h > high_duty_cycle_pct | losing the battle |
| `{pump}_sensor_unavailable` | CT or running sensor unavailable >60 s | silent monitoring failure |
| `{pump}_no_run_watchdog` | since_last_run > adaptive/​warm-up threshold | dead pump / stuck float / breaker |
| `{pump}_high_frequency` | runs_24h > `{pump}_high_frequency_max` (sump 1500 / ejector 60) | **built but alert OFF** (Phase 2). Fixed 60 always tripped the sump's ~215/day cadence — now a live helper |

Water-safety (§6b):

| Entity ID (`binary_sensor.{pump}_…`) | Fires when | Priority |
|---|---|---|
| `{pump}_running_but_flooding` | running AND flood sensor wet | CRITICAL — losing / blocked discharge |
| `{pump}_flood_sensor_fault` | flood sensor unavailable >60 s OR battery < low_batt% | WARNING — last line of defense blind |
| `{pump}_protection_compromised` | no_run OR continuous_run OR current_load_low OR flood wet | CRITICAL — backup likely load-bearing |

Composite:

| Entity ID | Type | Meaning |
|---|---|---|
| `sensor.{pump}_status` | template (ok / warning / critical) | At-a-glance health; `reason` attribute lists active conditions. Reads **pure** sensors, so toggles never mask it. |

Gated alert-source wrappers `binary_sensor.{pump}_*_alert` mirror the above but
AND in `{pump}_monitoring_enabled` and the per-alert toggle; the `alert:` entities
watch these. Not shown on the dashboard.

## Layer 4 — alerts

All target `notify.gregs_iphone`. Flood/safety tier uses the iOS critical
interruption sound. Ordered by priority (plan §9):

| Alert | Watches (`binary_sensor.{pump}_…`) | Severity | Repeat |
|---|---|---|---|
| Flooding | `flooding_alert` (raw flood input) | CRITICAL | until ack |
| Running but flooding | `running_but_flooding_alert` | CRITICAL | until ack |
| Protection compromised | `protection_compromised_alert` | CRITICAL | until ack |
| Continuous run | `continuous_run_alert` | CRITICAL | until ack |
| Sensor offline | `sensor_unavailable_alert` | CRITICAL | repeat |
| No run | `no_run_alert` | CRITICAL (sump ON / ejector OFF) | until ack |
| Flood sensor fault | `flood_sensor_fault_alert` | WARNING | ~daily |
| Short interval | `short_interval_alert` | WARNING (sump ON / ejector OFF) | moderate |
| Short cycling | `short_cycling_alert` | WARNING | moderate |
| Duration anomaly | `duration_anomaly_alert` | WARNING | reminder |
| Current load high | `current_load_high_alert` | WARNING | ~daily |
| Current load low | `current_load_low_alert` | WARNING | moderate |
| Current spike | `current_spike_alert` | WARNING | moderate |
| High duty cycle | `high_duty_cycle_alert` | WARNING | moderate |
| High frequency | — (no alert; sensor only) | INFO | OFF — Phase 2 |

## 6a. Per-pump differences

| Behavior | Sump | Ejector |
|---|---|---|
| Baseline cycling | ~6.7 min median from groundwater (Task A measured) | irregular, household-driven |
| `no_run_watchdog` | **ON**, adaptive (3× median, clamped) | **OFF** (detection gated off) |
| `short_interval` | **ON**, fixed 30 s / live p05 floor | **OFF** (detection gated off) |
| Flood / safety (§6b) | **ON** | **ON** |

All other layers apply identically to both pumps.

## Helpers

See [`../packages/pump_monitor_helpers.yaml`](../packages/pump_monitor_helpers.yaml)
for the full `input_number` (thresholds) and `input_boolean` (enable toggles)
catalog and their defaults.

## Task A — seeded threshold values

Measured from `docs/analysis/seed_thresholds.py` over 4.9 days of recorded
history (`sump_history.csv`, `ejector_history.csv`):

| Helper / metric | Sump (n=1166 cycles) | Ejector (n=16 cycles — sparse) |
|---|---|---|
| interval median | 400 s (6.7 min) | 13990 s (233 min) |
| interval p05 | 81 s (1.3 min) | 3142 s (52 min) |
| interval p99 (≈ longest-normal) | 764 s (12.7 min) | 68546 s (1142 min) |
| duration median / stddev | 8 s / ~0 s | 6 s / ~1 s |
| idle-gap median | 392 s (6.5 min) | 19077 s (318 min) |
| `{pump}_no_run_floor_min` set | **15** | 120 (provisional) |
| `{pump}_no_run_ceiling_min` set | **30** | 1200 (provisional) |
| `{pump}_no_run_history_ready` | **ON** (adaptive live) | OFF (too few samples) |

Notes:
- The sump cycles far faster than the earlier "~30 min" guess — **~6.7 min
  median, ~8 s runs**. The no-run clamps are seeded accordingly; the watchdog
  now fires at `clamp(15, 30, 3× live median)` minutes of silence.
- `short_interval_seconds` stays at the physical 30 s default; the live
  `sensor.sump_interval_p05_14d` (~81 s) is folded in at runtime. `skip_first` +
  the 30-min repeat on the alert mean only *sustained* fast cycling pages you,
  not a lone fast double-cycle.
- Ejector history is too sparse (16 cycles) for reliable percentiles, so its
  `history_ready` stays OFF (warm-up fallback) — moot in practice since the
  ejector no-run watchdog is OFF by design (§6a). Re-run the script after more
  history accrues to seed it properly.

### Observed live storm behavior (sump)

Recorded during an active storm on first deployment (monitoring silenced, data
still collecting). Useful reference for tuning `short_interval` once storm data
lands in the rolling 14-day window:

| Metric | Value |
|---|---|
| Typical storm interval | ~60 s |
| Storm-peak interval (fastest observed) | **~58 s** |
| Run duration during storm | still ~8–9 s (unchanged from baseline) |
| Duty cycle at storm peak | ~13 % (≪ 50 % high-duty threshold) |

Reads at storm cadence: `short_cycling` fires (≈9–10 starts/10 min ≫ 4);
`short_interval` does **not** yet (58 s > the 30 s fixed floor, and p05 not warm).
As storms enter the 14-day window the live `interval_p05_14d` will settle near
this ~58–80 s band — at which point `short_interval` begins flagging storm peaks
(its intended "approaching capacity" heads-up). Run duration holding ~8 s with a
positive idle gap confirms the pump keeps up even at peak — the worry signs would
be duration climbing or idle gap shrinking toward 0.
