# Sump & Ejector Pump Monitoring — Implementation Plan

**Phase 1: Current-monitoring foundation (no weather station yet)**

This document is the build spec for a coding agent. It defines a self-contained
Home Assistant feature repo that derives health/anomaly sensors, alerts, and a
dashboard from existing CircuitSetup energy-meter current sensors. Weather
integration is deliberately deferred to Phase 2; seams are marked but stubbed.

---

## 1. Context & goal

The user monitors a sump pump and an ejector pump via a CircuitSetup energy
meter. Each pump's circuit has a current transformer (CT). A threshold on each CT
already produces a `*_pump_running` binary sensor (on when the circuit draws
> ~1 A, damped at ~1 Hz). HA records the on/off time series.

**Phase 1 goals:**
1. Derive per-cycle metrics (run duration, **idle gap**, interval between starts,
   peak/avg current, inflow index).
2. Maintain self-tuning baselines (median/mean/stddev) for those metrics, **seeded
   from the user's own run history** (§6c) rather than warming up from scratch.
3. Detect anomalies in **run duration** and **run frequency / duty cycle**.
4. Detect **pump health via current**: rising load (wear/clog), spikes (jam),
   and below-normal load (sheared impeller, airlock, cavitation, stuck check valve).
5. **Integrate the flood sensors** as top-priority safety alerts + ground-truth, and
   infer when the **battery backup** is load-bearing (§6b).
6. Track **lifetime wear** (cumulative cycle count + total runtime).
7. Fire actionable alerts with acknowledge + re-notify.
8. Provide a YAML-mode Lovelace dashboard **and a compact remote-panel view**.

**Explicit non-goals for Phase 1:** anything requiring rainfall data. NOTE: the
"hasn't run in X hours" and "interval too short" alerts are **active in Phase 1 for
the sump** — its ~30-min baseline cycling (groundwater, rain or not) makes them
weather-independent and reliable (see §6a). Weather data in Phase 2 adds *context*
(distinguishing rain-driven frequency from a leak) and the `runtime_per_mm` wear
metric — it is not required to make the frequency alerts work. The only genuinely
deferred items are rainfall-normalized metrics and any rain-gating, marked with a
clear seam (§11).

---

## 2. Data sources (entity inventory)

Existing entities (inputs — do not recreate):

| Role | Entity ID |
|------|-----------|
| Current sensor, channel 1 | `sensor.energy_meter_eth_ws_f3a598_ct1_amps` |
| Current sensor, channel 2 | `sensor.energy_meter_eth_ws_f3a598_ct2_amps` |
| Sump running (binary) | `binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running` |
| Ejector running (binary) | `binary_sensor.circuitsetup_energy_meter_f3a598_ejector_pump_running` |
| Sump flood sensor (zigbee leak, **above rim**) | `binary_sensor.0x282c02bfffefb418_water_leak` |
| Ejector flood sensor (zigbee leak, **above rim**) | `binary_sensor.water_b3eb_water_leak` |

The flood sensors are mounted **above the pit rim**, so they are last-resort flood
confirmation (water already at floor level), NOT early warning. They are wired in as
the highest-priority CRITICAL alerts and as ground-truth validation of the inferred
signals — see §6b. The agent must also locate each flood sensor's **battery entity**
(zigbee leak sensors expose `sensor.<device>_battery`); confirm the exact IDs in HA
and record them for the flood-sensor fault watchdog (§6b).

**Battery backup pump:** present but **not currently monitored** (no CT, no sensor).
It cannot be observed directly today; §6b derives a "protection compromised" state
that infers when the backup is load-bearing, and §11 roadmaps direct metering.

Notification target: `notify.gregs_iphone`

### ⚠️ TASK 0 (BLOCKING): Confirm CT → pump mapping

The CT channel that backs each `*_pump_running` binary sensor is **not** inferable
from names. Before wiring per-run current logic, confirm which CT belongs to which
pump:

> Verification: with both pumps idle, manually run the **sump** pump (pour water in
> the pit, or jump the float). Watch Developer Tools → States for `ct1_amps` vs
> `ct2_amps` — whichever rises is the sump's CT. The other is the ejector's.

Record the result here and use it everywhere a pump's current sensor is referenced:

```yaml
# CONFIRMED by user
sump_current_sensor:    sensor.energy_meter_eth_ws_f3a598_ct2_amps   # sump = CT2
ejector_current_sensor: sensor.energy_meter_eth_ws_f3a598_ct1_amps   # ejector = CT1
```

✅ Task 0 is COMPLETE — mapping confirmed. The agent may proceed.

---

## 3. Architecture (layered)

Each layer depends only on the layer below it. Build bottom-up.

```
Layer 0  Inputs ............ existing CT amps + *_pump_running binary sensors
Layer 1  Per-cycle .......... trigger template sensors, one number per completed cycle
Layer 2  Aggregates ......... history_stats (counts/on-time) + statistics (mean/stddev)
Layer 3  Health/anomaly ..... template binary sensors reading Layer 2
Layer 4  Alerts ............. alert integration (ack + re-notify)
Layer 5  Dashboard .......... YAML-mode Lovelace
```

The two pumps share identical logic but get **independent baselines and helpers**.
Generate two parallel package files from one spec — do NOT share derived entities
between pumps.

---

## 4. Repo layout

A self-contained feature repo that lives inside `/config` so HA can read it, with
its own git history.

```
ha-pump-monitor/
├── README.md                      # install steps + overview
├── AGENTS.md                      # agent onboarding / conventions
├── docs/
│   ├── DESIGN.md                  # this architecture (link back here)
│   ├── ENTITIES.md                # data dictionary (see §8)
│   └── ROADMAP.md                 # Phase 2 weather seam (see §11)
├── packages/
│   ├── pump_monitor_helpers.yaml  # input_number / input_boolean for BOTH pumps
│   ├── pump_monitor_sump.yaml     # sump: templates, stats, binary, alerts
│   └── pump_monitor_ejector.yaml  # ejector: same structure, own baselines
└── dashboards/
    └── pump_monitor.yaml          # Lovelace YAML-mode dashboard
```

### HA wiring (add to `/config/configuration.yaml`)

```yaml
homeassistant:
  packages: !include_dir_named ha-pump-monitor/packages

lovelace:
  dashboards:
    pump-monitor:
      mode: yaml
      filename: ha-pump-monitor/dashboards/pump_monitor.yaml
      title: Pump Monitor
      icon: mdi:water-pump
      show_in_sidebar: true
```

Recorder retention (add/merge under `recorder:`):

```yaml
recorder:
  purge_keep_days: 30   # so statistics baselines survive restarts
```

Includes resolve relative to `/config`.

### Repository placement & updates

`/config` is **NOT** a git repo and does not need to become one. This feature is a
**standalone git repo living in the `/config/ha-pump-monitor/` subdirectory** — with
no parent repo there are no submodule/nesting concerns. Do not add `/config` itself
to version control (it contains `secrets.yaml`, the recorder DB, and `.storage/`).

Two equivalent deployment workflows (user's choice; the in-HA layout is identical):
- **Clone-in-place:** `git clone <url> /config/ha-pump-monitor/`, update via
  `git pull` in that directory. Requires git on the HA host (e.g. the Advanced SSH &
  Web Terminal add-on includes it).
- **Deploy-from-workstation:** develop/push the repo on a dev machine, then sync the
  `packages/` and `dashboards/` directories into `/config/ha-pump-monitor/` via the
  Samba or VS Code add-on. No git required on the HA host.

The repo contains only YAML + Markdown — nothing secret — so it is safe to host
publicly or privately as-is.

---

## 5. Naming conventions

- Per-pump prefix: `sump_` or `ejector_`.
- All numeric sensors that should feed Long-Term Statistics MUST set
  `state_class: measurement`.
- Durations: `device_class: duration`, `unit_of_measurement: "s"`.
- Currents: `device_class: current`, `unit_of_measurement: "A"`.
- Every derived entity needs a stable `unique_id` (prefix `pm_`, e.g.
  `pm_sump_last_run_duration`) so it's renameable/registry-tracked.
- Templates must be availability-safe: default every `float()` and guard against
  `unknown`/`unavailable` source states so math never errors.

---

## 6. Derived-entity specification

The following is the full Layer 1–3 catalog for the **sump** pump. The ejector
package is identical with `sump` → `ejector` and its own CT sensor.

### Layer 1 — per-cycle (trigger template sensors)

**`sensor.sump_last_run_duration`** (seconds) — fires on running on→off edge.
```yaml
- trigger:
    - platform: state
      entity_id: binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running
      from: "on"
      to: "off"
  sensor:
    - name: "Sump Last Run Duration"
      unique_id: pm_sump_last_run_duration
      device_class: duration
      unit_of_measurement: "s"
      state_class: measurement
      state: >
        {{ (as_timestamp(trigger.to_state.last_changed)
            - as_timestamp(trigger.from_state.last_changed)) | round(1) }}
```

**`sensor.sump_interval_between_starts`** (seconds) — fires on off→on edge; stores
the previous start time in an attribute so it survives across triggers.
```yaml
- trigger:
    - platform: state
      entity_id: binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running
      from: "off"
      to: "on"
  sensor:
    - name: "Sump Interval Between Starts"
      unique_id: pm_sump_interval
      device_class: duration
      unit_of_measurement: "s"
      state_class: measurement
      state: >
        {% set prev = this.attributes.get('last_start') %}
        {{ (as_timestamp(trigger.to_state.last_changed) - as_timestamp(prev)) | round(0)
           if prev else 0 }}
      attributes:
        last_start: "{{ trigger.to_state.last_changed }}"
```

**`sensor.sump_run_peak_current`** (A) — running max during the active run; holds
after the run until the next start. Triggers on start edge, every CT update, and
stop edge. Use the CONFIRMED CT sensor from Task 0.
```yaml
- trigger:
    - platform: state
      entity_id: binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running
      to: "on"
      id: start
    - platform: state
      entity_id: sensor.energy_meter_eth_ws_f3a598_ct2_amps   # sump = CT2
      id: sample
  sensor:
    - name: "Sump Run Peak Current"
      unique_id: pm_sump_run_peak_current
      device_class: current
      unit_of_measurement: "A"
      state_class: measurement
      state: >
        {% set cur = states('sensor.energy_meter_eth_ws_f3a598_ct2_amps') | float(0) %}
        {% if trigger.id == 'start' %}
          {{ cur }}
        {% elif is_state('binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running','on') %}
          {{ [ this.state | float(0), cur ] | max }}
        {% else %}
          {{ this.state | float(0) }}
        {% endif %}
```

**`sensor.sump_run_avg_current`** (A, OPTIONAL) — per-run mean via attribute
accumulation (sum + count reset on start, divide on stop). Implement if low-cost;
otherwise the 30-day running-current statistics sensor (below) covers the baseline
need. Mark optional in code comments.

**`sensor.sump_run_current_active`** (A) — passthrough of the CT current **only
while running**, else `unknown`. This is the feed for current baselines so idle
0 A readings don't pollute the stats.
```yaml
- sensor:
    - name: "Sump Run Current Active"
      unique_id: pm_sump_run_current_active
      device_class: current
      unit_of_measurement: "A"
      state_class: measurement
      availability: >
        {{ is_state('binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running','on') }}
      state: "{{ states('sensor.energy_meter_eth_ws_f3a598_ct2_amps') | float(0) }}"
```

**`sensor.sump_time_since_last_run`** (template, duration) — now() minus the last
start timestamp (use the `last_start` attribute pattern, not raw `last_changed`,
to be restart-robust). Re-evaluates every minute because it references `now()`, so
the no-run watchdog trips on schedule. Feeds the no-run watchdog and the dashboard.

**`sensor.sump_idle_gap`** (seconds) — fires on off→on edge; the **off→on** rest time
(how long the pit sat empty before refilling). This is the **inflow proxy**, distinct
from `interval_between_starts` (start-to-start, which blends idle + run). Splitting
these is the key analytical upgrade: idle gap ≈ inflow rate; run duration ≈ pump
output. Compute as `now_start − previous_stop`, storing the previous stop time in an
attribute (mirror the `interval_between_starts` pattern but key off the prior off
edge).

**`sensor.sump_inflow_index`** (template) — a normalized inflow rate ≈
`3600 / max(idle_gap, floor)` (refills per hour). Higher = more water entering the
pit (heavy rain / high groundwater). `state_class: measurement`. This is your
storm-signature trace **today**, before any weather station — plot it over time and
every wet event is visible. Floor the denominator (~5 s) to avoid divide-by-zero on
back-to-back cycles.

### Layer 2 — aggregates

`history_stats` (counts & on-time):
- `sensor.sump_runs_1h` — type `count`, window now-1h..now.
- `sensor.sump_runs_24h` — type `count`, window now-24h..now.
- `sensor.sump_ontime_24h` — type `time` (hours), window now-24h..now. **This is the
  duty-cycle base.**

`statistics` (self-tuning baselines; set sane `sampling_size` and `max_age`).
Use `state_characteristic` values supported by the statistics integration —
`median` and `percentile` ARE supported and are used deliberately here because the
interval distribution is right-skewed (occasional long gaps pull the mean up):
- `sensor.sump_duration_mean` — source `sensor.sump_last_run_duration`, mean, 14d.
- `sensor.sump_duration_stddev` — source same, standard_deviation, 14d.
- `sensor.sump_interval_mean` / `sensor.sump_interval_stddev` — source interval, 14d.
- `sensor.sump_interval_median_14d` — source interval, **median**, 14d. ← cadence
  baseline for the adaptive no-run watchdog (median, not mean).
- `sensor.sump_interval_p05_14d` — source interval, **percentile** (percentile: 5),
  14d. ← data-driven "too short" floor for the short-interval alert.
- `sensor.sump_idle_gap_median_14d` — source `sensor.sump_idle_gap`, median, 14d.
- `sensor.sump_current_baseline_30d` — source `sensor.sump_run_current_active`,
  mean, 30d. ← long baseline.
- `sensor.sump_current_short_7d` — source same, mean, 7d. ← recent window for drift.
- `sensor.sump_current_stddev_30d` — source same, standard_deviation, 30d.

Lifetime odometers (wear gauges — pumps are rated for finite cycles/hours):
- `sensor.sump_lifetime_cycles` — a `counter` (or `utility_meter` on a per-run pulse)
  incremented once per on→off completion. Never resets automatically; survives
  restarts. This is your replacement-prediction metric.
- `sensor.sump_lifetime_runtime` — cumulative total runtime via `utility_meter`
  (source = on-time) with no cycle/reset, or a `history_stats` `time` over a very
  long window. Document which approach the agent chose.

Derived template:
- `sensor.sump_duty_cycle_24h` (%) = `sump_ontime_24h / 24 * 100`,
  `state_class: measurement`. Track over time (LTS) as the "keeping up vs losing"
  trend.

### Layer 3 — health / anomaly (template binary sensors)

All read helpers from §7 so thresholds are tunable live. All must be
availability-safe and floor stddev to avoid noise-triggering.

- **`binary_sensor.sump_run_duration_anomaly`** — last duration outside
  `mean ± (zmult × max(stddev, floor))`. Floor stddev (~2 s) so consistent pumps
  don't false-positive.
- **`binary_sensor.sump_short_interval`** — fires when the start-to-start interval is
  below the "too short" floor. Use **two complementary floors, whichever is higher of
  a fixed and a data-driven bound**: the physical `short_interval_seconds` (default
  30 s, maps to a concrete "overwhelmed" meaning the user cares about) AND the
  learned `sump_interval_p05_14d` (5th-percentile of normal). Firing when interval is
  below the chosen floor = pit refilling/restarting abnormally fast = inflow
  approaching pump capacity. Require it **sustained** to avoid noise from a single
  check-valve burp — fire only when the last 2+ consecutive intervals are short, OR
  apply a `for:` hold on the alert (§9). NOTE: a *single* short interval after a
  long-normal stretch points more at a leaking check valve (water flows back,
  immediate restart); *sustained* short intervals during weather = overwhelmed. The
  idle-gap split (Layer 1) is what lets Phase 2 tell these apart.
- **`binary_sensor.sump_short_cycling`** — `sump_runs_1h` implies start rate above
  `short_cycle_max_starts` within `short_cycle_window_min`. Complementary to
  `short_interval`: this is the count-in-window view (stuck-float / undersized-pump
  signature). Implement via runs-in-window count; document the exact rule chosen.
- **`binary_sensor.sump_continuous_run`** — pump on AND current run elapsed >
  `max_run_seconds`. **Highest-priority signal** (stuck float or inflow ≥ outflow).
  Compute elapsed from the binary sensor's `last_changed` while state is `on`.
- **`binary_sensor.sump_current_load_high`** — `sump_current_short_7d` exceeds
  `sump_current_baseline_30d × (1 + current_drift_pct/100)`. Wear/partial clog
  early-warning.
- **`binary_sensor.sump_current_load_low`** — most recent `sump_run_peak_current`
  (or short-7d mean) below `baseline_30d × current_low_pct/100`. Loss of load
  (sheared impeller / airlock / cavitation / stuck-open check valve).
- **`binary_sensor.sump_current_spike`** — `sump_run_peak_current` >
  `current_spike_a` (absolute). Jam / locked rotor.
- **`binary_sensor.sump_high_duty_cycle`** — `sump_duty_cycle_24h` >
  `high_duty_cycle_pct`. "Losing the battle" indicator.
- **`binary_sensor.sump_sensor_unavailable`** (watchdog) — CT sensor OR binary
  sensor is `unavailable`/`unknown` for > a short debounce. Silent-failure guard.
- **`binary_sensor.sump_no_run_watchdog`** — **adaptive**, weather-independent for
  the sump. The sump cycles regularly from baseline groundwater, so silence = fault
  (dead pump, stuck float, tripped breaker, check valve stuck closed). Logic:
    - **Warm-up (first ~7–14 days, or until `sump_interval_median_14d` has enough
      samples):** use the fixed fallback `sump_no_run_warmup_fixed_min` (default
      90 min) so you're protected while history accumulates.
    - **Steady state:** fire when `sump_time_since_last_run >
      max(no_run_floor_min, min(no_run_ceiling_min, no_run_multiplier ×
      sump_interval_median_14d))`. Default multiplier 3, clamped to a floor/ceiling.
      Median (not mean) because the interval distribution is right-skewed.
    - **Seasonal drift is handled automatically** — the 14-day rolling median tracks
      wet/dry seasons, so "normal cadence" legitimately shifts across the year and the
      threshold follows. A fixed wall could not do this.
  Gate the warm-up vs steady-state switch on a sample-count or an "enough history"
  input_boolean the history-seeding task (§6c) can flip. Default ON for the sump;
  **OFF for the ejector** (household-driven; long quiet stretches are normal).
- **`binary_sensor.sump_high_frequency`** — runs/24h above an expected ceiling.
  Largely **superseded in Phase 1 by `short_interval`** (real-time interval is a
  better "overwhelmed" signal than a 24h count). Leave built but OFF; revisit in
  Phase 2 only if rain-normalization makes a daily-count view useful.

### 6a. Per-pump differences (sump vs ejector)

The two packages are structurally identical EXCEPT for these deliberate
differences, driven by how each pump behaves:

| Behavior | Sump | Ejector |
|----------|------|---------|
| Baseline cycling | ~every 30 min from groundwater, rain or not | irregular, driven by household wastewater use |
| `no_run_watchdog` | **ON**, adaptive (3× median, clamped) | **OFF** (long quiet stretches are normal; would false-positive) |
| `short_interval` | **ON**, 30 s / p05 floor (heavy-rain overwhelm signal) | **OFF** by default (enable only if a meaningful "too frequent" bound exists) |
| Flood sensor | `binary_sensor.0x282c02bfffefb418_water_leak` | `binary_sensor.water_b3eb_water_leak` |
| Flood/safety alerts (§6b) | **ON** for both pumps | **ON** for both pumps |

All other layers (duration anomaly, continuous run, current health, duty cycle,
sensor watchdog) apply identically to both pumps. The agent should parameterize the
two enable-toggle defaults above rather than copy-paste blindly.

### 6b. Water-safety layer (flood sensors & backup inference)

Every signal above is *inferred from the pump's electrical behavior* — it watches the
pump, not the water. This layer watches the water directly and is the safety backbone.
**These alerts outrank all current-derived alerts.**

Flood sensors are `binary_sensor.0x282c02bfffefb418_water_leak` (sump) and
`binary_sensor.water_b3eb_water_leak` (ejector). They sit **above the rim** = active
flooding when wet (not early warning). Derived entities:

- **Direct flood alert** — wire each existing flood `binary_sensor` straight into a
  CRITICAL, repeat-until-ack alert (§9) with a distinct notification. No derived
  sensor needed; it is the single most important alert in the system.
- **`binary_sensor.sump_running_but_flooding`** — pump running AND flood sensor wet.
  High-value diagnostic the current trace alone cannot give: the pump has power and is
  cycling but water is still at the floor → losing the battle, blocked/airlocked
  discharge, or recirculating. CRITICAL.
- **`binary_sensor.sump_protection_compromised`** (composite) — TRUE when a primary
  fault is active (`no_run_watchdog` OR `continuous_run` OR `current_load_low`) AND/OR
  the flood sensor is wet. Meaning: the primary has likely failed and the **battery
  backup is now load-bearing** — the most important escalation state, constructible
  today with no backup metering. CRITICAL.
- **`binary_sensor.sump_flood_sensor_fault`** (watchdog on the watchdog) — the flood
  `binary_sensor` is `unavailable`/`unknown` for > a debounce, OR its battery entity
  (`sensor.<device>_battery`, confirm exact ID per §2) is below
  `flood_sensor_low_battery_pct` (default 20%). A dead flood sensor is a silent loss
  of the last line of defense, so this is its own WARNING alert.

**Ground-truth cross-check:** when a current-derived alert (e.g. `no_run_watchdog`)
is followed by a flood-sensor trip, that confirms the inference was a real failure —
log/annotate it so the user can tune thresholds with confidence over time.

### 6c. Threshold seeding from history (run FIRST, see Task A)

Do not warm up the adaptive thresholds from an empty baseline if history already
exists. Before/at install, derive the real cadence from the user's recorded
`*_pump_running` history and seed the helpers:

1. Export or query the sump `*_pump_running` state history (the user has days–weeks
   already). Acceptable sources: HA's `history`/`logbook`, a recorder SQL query, or a
   CSV the user exports.
2. Reconstruct completed cycles (on→off pairs) and compute, for the sump:
   - **median** start-to-start interval (seeds `interval_median` expectation),
   - **5th percentile** interval (seeds `short_interval_seconds` sanity / floor),
   - median + spread of **run duration**,
   - median **idle gap**,
   - longest normal gap observed (sanity-bounds `no_run_ceiling_min`).
3. Write the resulting numbers into the `input_number` defaults (or set them live)
   and flip the "enough history" boolean so the no-run watchdog uses adaptive logic
   immediately instead of the 90-min warm-up fallback.

This replaces the guessed "~30 min" (the user notes 30 min was a rough longest-case
guess) with measured values. The agent can implement step 2 as a one-off Python/SQL
analysis or a Jupyter-style scratch script committed under `docs/analysis/`.

---

## 7. Helpers catalog (`pump_monitor_helpers.yaml`)

One set per pump. `input_number` for thresholds, `input_boolean` for enable
toggles. Suggested defaults (the user will tune live during the first storms):

`input_number` (per pump, e.g. `sump_*`):
| Helper | Default | Unit | Meaning |
|--------|---------|------|---------|
| `sump_max_run_seconds` | 120 | s | continuous-run hard cap |
| `sump_short_interval_seconds` | 30 | s | min healthy start-to-start gap (overwhelm alert) |
| `sump_short_cycle_window_min` | 10 | min | short-cycle observation window |
| `sump_short_cycle_max_starts` | 4 | starts | starts-in-window ceiling |
| `sump_duration_zscore_mult` | 3 | σ | duration anomaly sensitivity |
| `sump_current_drift_pct` | 20 | % | high-load threshold over baseline |
| `sump_current_low_pct` | 60 | % | low-load threshold (× baseline) |
| `sump_current_spike_a` | (set after observing) | A | absolute spike cutoff |
| `sump_high_duty_cycle_pct` | 50 | % | sustained duty-cycle warning |
| `sump_no_run_multiplier` | 3 | × | no-run = mult × rolling median interval |
| `sump_no_run_warmup_fixed_min` | 90 | min | fixed fallback before history learned |
| `sump_no_run_floor_min` | 45 | min | adaptive clamp floor |
| `sump_no_run_ceiling_min` | 120 | min | adaptive clamp ceiling (seed from history) |
| `flood_sensor_low_battery_pct` | 20 | % | flood-sensor low-battery warning (shared) |

`input_boolean` (helper, not per pump): `sump_no_run_history_ready` /
`ejector_no_run_history_ready` — flipped by the history-seeding task (§6c) to switch
the no-run watchdog from warm-up fallback to adaptive logic.

`input_boolean` (per pump):
- `sump_monitoring_enabled` (master, default ON)
- `sump_alert_continuous_run_enabled` (ON)
- `sump_alert_short_interval_enabled` (ON for sump / **OFF for ejector**)
- `sump_alert_short_cycle_enabled` (ON)
- `sump_alert_duration_anomaly_enabled` (ON)
- `sump_alert_current_load_enabled` (ON)
- `sump_alert_duty_cycle_enabled` (ON)
- `sump_alert_sensor_unavailable_enabled` (ON)
- `sump_alert_no_run_enabled` (**ON for sump** — weather-independent / **OFF for ejector**)
- `sump_alert_high_freq_enabled` (OFF — superseded by short_interval; revisit Phase 2)
- `sump_alert_flood_enabled` (ON — both pumps; §6b)
- `sump_alert_running_but_flooding_enabled` (ON — both pumps)
- `sump_alert_protection_compromised_enabled` (ON — both pumps)
- `sump_alert_flood_sensor_fault_enabled` (ON — both pumps)

Per §6a, the ejector package sets `*_alert_no_run_enabled` and
`*_alert_short_interval_enabled` to OFF by default; **all §6b flood/safety toggles are
ON for both pumps.** Everything else matches the sump.

Defaults above are starting points only; the spike cutoff in particular should be
left for the user to set once a few normal runs have been observed. The 30 s
short-interval default may fire during genuinely heavy rain (which is the intent —
it's an "approaching capacity" heads-up); lower it if it proves too chatty.

---

## 8. `docs/ENTITIES.md` (data dictionary)

The agent must produce a table listing every created entity: entity_id, layer,
type, unit, source(s), and a one-line meaning. This is the contract the dashboard
and alerts reference, and the map the user reads. Generate it from the §6 catalog
for both pumps.

---

## 9. Alerts specification (Layer 4)

Use the `alert` integration (not bare automations) for acknowledge + re-notify.
All alerts gated by `{pump}_monitoring_enabled` AND their per-alert toggle. Target
`notify.gregs_iphone`. Each alert references a source binary sensor. **Ordered by
priority — the flood/safety tier (§6b) outranks everything inferred from current.**

| Alert | Source binary sensor | Severity | Repeat | Notes |
|-------|----------------------|----------|--------|-------|
| **Flooding** | `*_water_leak` (existing input) | **CRITICAL** | repeat until ack | distinct sound; active flooding NOW |
| **Running but flooding** | `*_running_but_flooding` | **CRITICAL** | repeat until ack | pump on yet water at floor = losing/blocked |
| **Protection compromised** | `*_protection_compromised` | **CRITICAL** | repeat until ack | primary failed → backup load-bearing |
| Continuous run | `*_continuous_run` | CRITICAL | repeat until ack | stuck float / inflow ≥ outflow |
| Sensor offline | `*_sensor_unavailable` | CRITICAL | repeat | monitoring-failure guard |
| No run | `*_no_run_watchdog` | CRITICAL | repeat until ack | sump ON (adaptive); ejector OFF |
| Flood sensor fault | `*_flood_sensor_fault` | WARNING | daily | dead/low-battery flood sensor = blind |
| Short interval | `*_short_interval` | WARNING | moderate | heavy-rain overwhelm; `for:` ~2–3 min hold |
| Short cycling | `*_short_cycling` | WARNING | moderate | stuck float / undersized |
| Duration anomaly | `*_run_duration_anomaly` | WARNING | once + reminder | |
| Current load high | `*_current_load_high` | WARNING | daily | wear/clog early-warning |
| Current load low | `*_current_load_low` | WARNING | moderate | loss of load |
| Current spike | `*_current_spike` | WARNING | moderate | jam |
| High duty cycle | `*_high_duty_cycle` | WARNING | moderate | losing the battle |
| High frequency | `*_high_frequency` | INFO | once | OFF; superseded by short interval |

The three **flood/safety alerts are the top tier** and should use a distinct
notification channel/sound on the iPhone (critical interruption level) so they cut
through Do-Not-Disturb — they mean water is at the floor or the backup is your only
remaining defense. The **No run** alert is CRITICAL because for a regularly-cycling
sump, silence is the strongest fault signal and the failure most likely to flood.
Apply a `for:` hold (~2–3 min) on **Short interval** so a lone fast double-cycle
doesn't page you.

Notification messages should include the pump name, the triggering metric's current
value, and its baseline where relevant (e.g. "Sump running current 7-day avg 9.2 A
vs 30-day baseline 7.1 A (+30%)"; "Sump has not run in 1h 38m — adaptive normal
~32 min").

---

## 10. Dashboard specification (Layer 5)

YAML-mode Lovelace (`dashboards/pump_monitor.yaml`). **Build with built-in core
cards only — ApexCharts/custom HACS cards are NOT installed.** The dashboard must
render fully and error-free on a stock HA install. Per-pump section layout, with
the specific core card to use for each panel:

1. **Composite health tile (top of each section)** — a single `*_status_ok` template
   sensor rendered prominently: GREEN when the pump ran within the expected adaptive
   window AND last duration in-band AND current in-band AND no active alerts AND flood
   sensor dry AND flood sensor healthy; otherwise YELLOW/RED with the reason. This is
   the at-a-glance "confirm proper operation" answer and the anchor of the remote
   panel.
2. **Safety row** — flood sensor state (`*_water_leak`), `*_protection_compromised`,
   `*_flood_sensor_fault`, and flood-sensor battery, as an `entities` card. Place it
   ABOVE the operational data — it's the most important row.
3. **Status row** — `gauge` card for live current; `glance`/`entities` for state,
   `*_time_since_last_run`, `*_runs_24h`, `*_duty_cycle_24h`.
4. **State + current history** — `history-graph` listing BOTH `*_pump_running` and the
   CT current sensor (binary timeline + current line on a shared time axis).
5. **Time between runs** — `history-graph`/`statistics-graph` of
   `*_interval_between_starts`, with `*_interval_median_14d` shown alongside in an
   `entities` card (most-recent-vs-typical, directly answering the user's two asks).
6. **Idle gap & inflow index** — `history-graph` of `*_idle_gap` and
   `*_inflow_index` — the storm-signature / inflow trace available today.
7. **Run duration over time** — `statistics-graph` of `*_last_run_duration`, with
   `*_duration_mean`/`*_duration_stddev` in an adjacent `entities` card (core can't
   draw bands; surface the numbers). A true duration *histogram* needs a custom card —
   list as optional enhancement.
8. **Current baseline panel** — `history-graph` of `*_current_short_7d` vs
   `*_current_baseline_30d` (wear trend) plus `*_run_peak_current`.
9. **Lifetime / wear** — `entities` card: `*_lifetime_cycles`, `*_lifetime_runtime`,
   duty-cycle-24h. The long-term review + replacement-prediction panel.
10. **Health badges** — `entities` card of all Layer-3 binary sensors.
11. **Controls** — `entities` card with all helpers for live tuning.

Include a top-level summary row spanning both pumps (composite tiles + flood states)
before the per-pump sections.

### Remote panel view (compact)

Add a second YAML dashboard (or a dedicated view) sized for the wall/ESP panel:
the **composite health tile**, pump state, `*_time_since_last_run`,
most-recent-interval-vs-median, live current, and the flood/safety states. No
controls, no deep history — just "is everything OK right now" at a glance, matching
the §1 goal of a remote at-a-glance confirmation.

### Optional ApexCharts/custom-card enhancements (do NOT build now; README only)

With ApexCharts (HACS): panel 4 becomes a true single-axis state+current overlay,
panel 7 gains mean±stddev bands and a real duration histogram, and an **hour-of-day
runs heatmap** (`config-template-card`/`apexcharts-card`) becomes possible — the
diurnal-pattern view that core cards can't render. Document with entity names
pre-wired; the delivered dashboard stays core-only and complete.

---

## 11. Roadmap — `docs/ROADMAP.md` (future hardware & weather)

Do NOT implement now; document precisely so each drops in cleanly. Ordered by safety
value, NOT by when it was discussed:

**Priority #1 — High-water float IN the pit (early warning).** The flood sensors sit
above the rim = they only confirm flooding after it reaches the floor. A float/level
sensor partway up the pit (a float switch on an ESP, or the Ecowitt WH55 on the
future gateway) is the missing **early warning**: it catches a stuck float *during* a
storm with minutes to spare. This is the single highest-value addition — higher than
the weather station for safety. Reserve `binary_sensor.sump_high_water` and make it a
CRITICAL alert above the rim-level flood alert.

**Priority #2 — Backup pump metering.** The battery backup is unmonitored today. Add a
CT on its circuit (if AC) or read its controller output, exposing
`binary_sensor.sump_backup_running`. A backup-ran event is itself **CRITICAL** — it
means the primary failed silently and you are one backup failure from a flood. Until
then, `*_protection_compromised` (§6b) infers this from primary fault + flood state.

**Priority #3 — Weather station (context, not gating).**
- `binary_sensor.rain_recent` (WS90 piezo) + rolling rainfall total (WH40H) via the
  Ecowitt local integration.
- The sump `no_run_watchdog` does NOT need rain-gating (regular baseline cycling).
  Note the portability caveat: a sump without steady inflow *would* want the
  `AND NOT rain_recent` gate.
- Weather adds context: distinguish "short intervals because heavy rain (expected)"
  from "short intervals with little rain (leak / intrusion / failing check valve)" —
  exactly the ambiguity the idle-gap split sets up.
- New `sensor.*_runtime_per_mm` (on-time normalized by rainfall) = premier wear
  metric; rising trend = pump moving less water per unit rain. Reserve the name.
- `*_high_frequency` may return as a rain-normalized daily view.

Mark the exact lines in the sump/ejector packages where these plug in with a
`# PHASE 2 SEAM` / `# ROADMAP` comment.

---

## 12. Build order (ordered tasks with acceptance criteria)

Work top-to-bottom; do not start a task until the prior one passes its check.

- **Task 0 — Confirm CT mapping** (§2). _Accept:_ user-confirmed sump/ejector CT IDs
  recorded; both packages reference the correct CT. ✅ done (sump=CT2, ejector=CT1).
- **Task A — Seed thresholds from history** (§6c). Pull the existing sump
  `*_pump_running` history, reconstruct cycles, compute median/p05 interval, duration
  spread, median idle gap, and longest-normal gap. Commit the analysis under
  `docs/analysis/` and write results into the helper defaults. _Accept:_ helper
  defaults reflect measured values (not the guessed 30 min); `*_no_run_history_ready`
  set true; the derived numbers recorded in ENTITIES.md / a NOTES file. Also confirm
  each flood sensor's **battery entity ID** here.
- **Task 1 — Repo scaffold + HA wiring.** Create the tree (§4), README, AGENTS.md,
  empty package files, configuration.yaml + recorder edits. _Accept:_ HA restarts
  clean with empty packages loaded; dashboard appears in sidebar (blank).
- **Task 2 — Helpers package.** _Accept:_ all `input_number`/`input_boolean` for
  both pumps (incl. adaptive no-run + flood/safety toggles) exist with seeded
  defaults; visible in Developer Tools → States.
- **Task 3 — Sump Layer 1 sensors.** _Accept:_ run the sump (or simulate via
  Developer Tools state set); `last_run_duration`, `interval_between_starts`,
  `idle_gap`, `inflow_index`, `run_peak_current`, `run_current_active`,
  `time_since_last_run` all populate correctly (idle vs running behavior verified).
- **Task 4 — Sump Layer 2 aggregates.** _Accept:_ counts/on-time, duty cycle, the
  median/percentile statistics, and lifetime odometers compute; odometers increment
  once per completed cycle and survive a restart.
- **Task 5 — Sump Layer 3 binary sensors.** _Accept:_ each evaluates without template
  errors; force-trigger `continuous_run`, `sensor_unavailable`, and the **adaptive
  `no_run_watchdog`** (in both warm-up and history-ready modes) to confirm they flip.
- **Task 5b — Water-safety layer (§6b).** Wire flood alerts, `running_but_flooding`,
  `protection_compromised`, and `flood_sensor_fault`. _Accept:_ wetting/simulating the
  flood `binary_sensor` fires the CRITICAL alert with the distinct channel; making the
  flood sensor `unavailable` or low-battery fires the fault watchdog; the composite
  `protection_compromised` flips when a primary fault coincides with flood/while-on.
- **Task 6 — Sump alerts.** _Accept:_ tripping each binary sensor sends one
  `notify.gregs_iphone` push at the correct severity; flood tier uses the critical
  interruption channel; clearing + toggle-off suppresses; re-notify works on criticals.
- **Task 7 — Ejector package.** Duplicate Tasks 3–6 + 5b with ejector entities/CT and
  the §6a/§6b differences (no-run & short-interval OFF; flood/safety ON). _Accept:_
  same checks pass; no entity-ID collisions with sump.
- **Task 8 — Dashboard + remote panel.** _Accept:_ all cards render using **core cards
  only** with no "custom element doesn't exist" errors; composite health tile,
  safety row, time-between-runs, idle-gap/inflow, duration, current-baseline, and
  lifetime panels all show real data; the compact remote-panel view renders; controls
  change thresholds live.
- **Task 9 — Docs.** ENTITIES.md complete for both pumps (incl. §6b + lifetime +
  inflow); ROADMAP.md documents the float / backup-metering / weather priorities;
  README install + history-seeding steps verified by a clean clone test.

---

## 13. Constraints & gotchas for the agent

- **Reload behavior:** trigger-based template entities and template binary sensors
  reload via Developer Tools → YAML (Template Entities). `statistics` and
  `history_stats` are `sensor:` platforms and generally need a **full restart** to
  pick up config changes — plan the test loop accordingly.
- **Restart restore:** trigger-based template sensors restore their last state on
  restart but won't back-fill missed events. `time_since_last_run` should key off a
  stored `last_start` attribute, not raw `last_changed`, to survive restarts.
- **Statistics warm-up:** statistics/history_stats sensors read `unknown` until they
  have data; downstream templates must default safely (don't let `unknown` break a
  comparison).
- **Stddev floor:** always `max(stddev, small_floor)` in z-score checks so a very
  consistent pump doesn't trip on micro-variance.
- **Sampling limits:** the ~1 Hz damped current data is ideal for slow load-trend
  and steady-state signatures but will NOT catch millisecond inrush/locked-rotor
  transients. Treat current monitoring as **load-trend detection, not transient
  protection** — don't design alerts that assume sub-second resolution.
- **state_class:** every numeric derived sensor needs `state_class: measurement` to
  feed HA Long-Term Statistics (the no-Influx long-term trend store).
- **Availability:** guard every template against `unknown`/`unavailable` source
  states; prefer `availability:` blocks plus `float(default)`.
- **Idempotency:** the agent should be able to re-run/regenerate a package without
  duplicating entities — keep stable `unique_id`s.

---

## 14. Definition of done

- Both pumps have full Layer 1–4 entities (incl. idle-gap/inflow, lifetime odometers,
  water-safety layer) and pass every Task acceptance check.
- Thresholds seeded from the user's real history (Task A), not guessed.
- Flood/safety alerts are the top tier, on a critical notification channel; adaptive
  no-run works in both warm-up and history-ready modes.
- Dashboard (core-only) + remote panel render; composite health tile answers
  "operating normally?" at a glance; controls tune thresholds live.
- All alerts route to `notify.gregs_iphone`; weather/float/backup items are roadmap
  only, with seams commented in YAML.
- Repo installs via `git clone` into `/config/ha-pump-monitor/` + the documented
  configuration.yaml lines (or workstation-deploy), and updates via `git pull`.
- ENTITIES.md and ROADMAP.md complete.
