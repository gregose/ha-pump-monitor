# DESIGN — layered architecture

The full build spec is [`../IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md);
this is the architecture summary. Each layer depends only on the layer below it,
so the packages are built and read bottom-up.

```
Layer 0  Inputs ......... existing CT amps + *_pump_running binary sensors + flood sensors
Layer 1  Per-cycle ...... trigger template sensors, one number per completed cycle
Layer 2  Aggregates ..... history_stats (counts / on-time) + statistics (mean / median / stddev)
Layer 3  Health/anomaly . template binary sensors reading Layer 2
Layer 4  Alerts ......... alert integration (acknowledge + re-notify)
Layer 5  Dashboard ...... YAML-mode Lovelace (core cards only)
```

The two pumps share **identical logic but independent baselines** — no derived
entity is shared between them. `pump_monitor_sump.yaml` is the reference;
`pump_monitor_ejector.yaml` is the same structure with `sump` → `ejector`, CT2 →
CT1, its own flood/battery IDs, and the §6a per-pump differences.

## Key design decisions

**Idle gap vs. interval.** `interval_between_starts` is start-to-start (idle +
run blended). `idle_gap` is previous-stop → next-start — the **inflow proxy**.
Splitting them is the core analytical upgrade: idle gap ≈ inflow rate, run
duration ≈ pump output. `inflow_index` (≈ 3600 / idle_gap) is the storm-signature
trace available today, before any weather station.

**Adaptive no-run watchdog.** For a sump that cycles regularly from groundwater,
*silence is the strongest fault signal*. The watchdog fires when time-since-last-
run exceeds `clamp(floor, ceiling, multiplier × 14-day median interval)`. Median
(not mean) because the interval distribution is right-skewed; the rolling 14-day
window tracks seasonal wet/dry drift automatically. A warm-up fixed fallback
(90 min) protects the system until history is seeded (Task A flips
`*_no_run_history_ready`). **ON for the sump, OFF for the ejector** (household-
driven; long quiet stretches are normal).

**Water-safety layer outranks everything.** Every current-derived signal watches
the *pump's electrical behavior*. The flood sensors watch the *water directly*
and are the safety backbone: direct flood alert, `running_but_flooding`, and the
`protection_compromised` composite (a primary fault and/or wet flood sensor →
the battery backup is now load-bearing — the most important escalation state,
constructible today with no backup metering).

**Alert gating.** Layer-3 binary sensors are **pure physical conditions** —
truthful for dashboard badges, the composite, and the status tile regardless of
toggle state. Because the `alert` integration can't take a condition, each alert
instead watches a thin gated wrapper `binary_sensor.*_alert` = (condition) AND
`monitoring_enabled` AND its per-alert toggle. The `protection_compromised`
composite and `status` tile read the **pure** sensors, so disabling an alert
never masks a real escalation on the dashboard.

**Availability-safe & idempotent.** Every template guards `unknown`/`unavailable`
source states and defaults every `float()`; the z-score stddev is floored so a
very consistent pump doesn't trip on micro-variance. Every derived entity has a
stable `pm_`-prefixed `unique_id`, so regenerating a package never duplicates
entities.

## Reload vs. restart

- **Reload live** (Developer Tools → YAML): `template:` entities, `input_number`,
  `input_boolean`, `automation`.
- **Full restart:** `statistics`, `history_stats`, `counter`, `alert` — these are
  platform/integration config. Batch these edits so you restart once.

Trigger-based template sensors restore their last state on restart but do not
back-fill missed events; `time_since_last_run` keys off a stored `last_start`
timestamp (not raw `last_changed`) to survive restarts.
