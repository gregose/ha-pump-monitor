# ha-pump-monitor

Home Assistant feature that derives **health / anomaly sensors, alerts, and a
dashboard** for a sump pump and an ejector pump from existing CircuitSetup
current-transformer (CT) sensors and two above-rim zigbee flood sensors.

Phase 1: current-monitoring foundation. No weather station required — the sump's
regular baseline cycling makes its frequency/no-run alerts weather-independent.
Weather, in-pit float, and backup metering are documented seams for Phase 2
(see [`docs/ROADMAP.md`](docs/ROADMAP.md)).

- **What it watches:** run duration, start-to-start interval, **idle gap /
  inflow index**, per-run peak & baseline current, duty cycle, lifetime wear.
- **What it protects against:** stuck float / continuous run, dead pump (adaptive
  no-run watchdog), fast cycling / overwhelm, wear & clog (rising current),
  loss-of-load (sheared impeller / airlock / stuck check valve), and — top
  priority — **active flooding** and **backup-load-bearing** escalation.
- **Core cards only** — renders on a stock HA install, no HACS required.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the layered architecture and
[`docs/ENTITIES.md`](docs/ENTITIES.md) for the full data dictionary.

## Layout

```
ha-pump-monitor/
├── README.md
├── AGENTS.md                       # how to work in this repo
├── IMPLEMENTATION_PLAN.md          # the build spec
├── docs/
│   ├── DESIGN.md                   # layered architecture
│   ├── ENTITIES.md                 # data dictionary (both pumps)
│   ├── ROADMAP.md                  # Phase 2 seams (float / backup / weather)
│   └── analysis/
│       ├── README.md               # Task A instructions
│       └── seed_thresholds.py      # history → adaptive-threshold seeds
├── packages/
│   ├── pump_monitor_helpers.yaml   # input_number / input_boolean, both pumps
│   ├── pump_monitor_sump.yaml      # sump: templates, stats, binary, alerts
│   └── pump_monitor_ejector.yaml   # ejector: same structure, own baselines
└── dashboards/
    └── pump_monitor.yaml           # Lovelace YAML-mode dashboard + remote view
```

## Install

The feature is a **standalone git repo living inside `/config`** at
`/config/ha-pump-monitor/`. `/config` itself is **not** a git repo and must not
become one. Two equivalent workflows — the in-HA layout is identical:

- **Clone-in-place** (requires git on the HA host, e.g. the Advanced SSH add-on):
  ```bash
  git clone <url> /config/ha-pump-monitor/
  # update later with:  cd /config/ha-pump-monitor && git pull
  ```
- **Deploy-from-workstation** (no git on HA host): develop/push on a dev machine,
  then sync `packages/` and `dashboards/` into `/config/ha-pump-monitor/` via the
  Samba or VS Code add-on.

### 1. Wire it into `configuration.yaml`

Add (merge, don't refactor existing keys). Includes resolve relative to
`/config`:

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

recorder:
  purge_keep_days: 30   # so statistics baselines survive restarts
```

### 2. Confirm the input entities exist

These are **read-only** inputs the packages reference (do not recreate them):

| Role | Entity ID |
|------|-----------|
| Sump current (CT2) | `sensor.energy_meter_eth_ws_f3a598_ct2_amps` |
| Ejector current (CT1) | `sensor.energy_meter_eth_ws_f3a598_ct1_amps` |
| Sump running | `binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running` |
| Ejector running | `binary_sensor.circuitsetup_energy_meter_f3a598_ejector_pump_running` |
| Sump flood (above rim) | `binary_sensor.0x282c02bfffefb418_water_leak` |
| Ejector flood (above rim) | `binary_sensor.water_b3eb_water_leak` |
| Sump flood battery | `sensor.0x282c02bfffefb418_battery` |
| Ejector flood battery | `sensor.water_b3eb_battery` |

CT mapping is confirmed: **sump = CT2, ejector = CT1**. Notifications target
`notify.gregs_iphone`.

### 3. Seed the adaptive thresholds from history (Task A)

Before relying on the no-run watchdog, seed it from your own recorded cadence so
it starts adaptive instead of on the 90-minute warm-up fallback. See
[`docs/analysis/README.md`](docs/analysis/README.md): export the sump running
history to CSV, run `seed_thresholds.py`, paste the suggested helper initials,
and flip `input_boolean.sump_no_run_history_ready` → ON.

Until seeded, the watchdog uses the documented warm-up fallback — safe, just less
tuned. Nothing here fabricates a "measured" value.

### 4. Validate → restart

**Always validate before applying** (Developer Tools → YAML → Check
Configuration, or `ha core check`). `statistics`, `history_stats`, `counter`,
and `alert` are platform config and need a **full restart** to load; template
entities reload live. After restart, confirm the entities exist and carry no
template errors (check the HA log, not just "no crash").

## Testing without waiting for a real cycle

- **Drive the trigger sensors:** Developer Tools → States, set the
  `*_pump_running` binary sensor to `on`, wait, set to `off` (overwritten on the
  next real update — fine for a quick test).
- **Force an alert:** temporarily lower the relevant `input_number` so the
  condition trips, confirm one push, then restore.
- **Flood/safety:** set the `*_water_leak` sensor to `on` to exercise the flood
  alert and composites; set it `unavailable` (or low battery) for the fault
  watchdog.
- **Don't spam your phone:** use the per-alert toggles, keep one target device,
  and acknowledge/clear between tests.

## Optional: ApexCharts enhancements (not installed)

With ApexCharts (HACS) the state+current overlay becomes single-axis, duration
gains mean±stddev bands and a true histogram, and an hour-of-day runs heatmap
becomes possible. The delivered dashboard stays core-only and complete — these
are documented, never required.

## Safety notes

- The repo contains only YAML + Markdown — nothing secret — safe to host public
  or private. Never commit anything from `/config/secrets.yaml`, `.storage/`, or
  the recorder DB.
- Current monitoring is **load-trend detection, not transient protection**: the
  ~1 Hz damped CT data catches slow load trends and steady-state signatures, not
  millisecond inrush / locked-rotor spikes.
