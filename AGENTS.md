# AGENTS.md — How to work in this repo

This file is about **how to work**, not what to build. The *what* lives in
`IMPLEMENTATION_PLAN.md` — read that first, in full, before writing anything. If this
file and the plan ever disagree, the plan wins on scope; this file wins on process
and safety.

---

## 1. Orientation

- **Goal:** a Home Assistant feature that derives health/anomaly sensors, alerts, and
  a dashboard for a sump pump and an ejector pump, from existing CircuitSetup current
  sensors and two zigbee flood sensors. Phase 1 only.
- **Repo location at runtime:** `/config/ha-pump-monitor/` (a standalone git repo
  inside the HA config dir). `/config` itself is **not** a git repo and must not
  become one.
- **You build:** the YAML packages, the dashboard, and the generated docs
  (`ENTITIES.md`, etc.) per the plan's build order (§12).
- **You do NOT build:** anything in the plan's Roadmap (§11) — weather/rain,
  in-pit float, backup metering. Leave the marked seams as comments only.

---

## 2. Golden rules (safety — never violate)

1. **Never read, write, commit, or print anything from `/config/secrets.yaml`,
   `/config/.storage/`, or the recorder database.** They are outside this repo and
   off-limits. Nothing secret ever enters the repo (it's YAML + Markdown only).
2. **Do not modify `/config/configuration.yaml` beyond the exact additions the plan
   specifies** (the `packages:` include, the `lovelace:` dashboard entry, the
   `recorder: purge_keep_days` line). Show the user the diff; do not refactor their
   config.
3. **The existing input entities are READ-ONLY. Do not recreate or redefine them.**
   They are: the two CT current sensors, the two `*_pump_running` binary sensors, and
   the two `*_water_leak` flood sensors. Their exact IDs are fixed in plan §2.
4. **CT mapping is settled: sump = CT2, ejector = CT1.** Do not re-derive or swap.
5. **Never leave HA in a broken-config state.** Validate before every apply (§4). A
   bad package can take down the whole instance.
6. **Do not guess safety-relevant thresholds.** The no-run, short-interval, and
   spike thresholds come from the history-seeding task (§6c / Task A) or are left for
   the user. If history is unavailable, use the documented warm-up fallback — do not
   invent a number and present it as derived.
7. **Do not install or depend on HACS/custom cards.** The dashboard is core-cards-only
   (plan §10). ApexCharts is documented as optional, never required.

---

## 3. Environment

- HA config root: `/config`. This feature: `/config/ha-pump-monitor/`.
- Read-only mounts you may reference but never edit in place: the user's existing
  config and entities.
- Two deployment workflows exist (clone-in-place vs deploy-from-workstation, plan §4);
  do not assume git is present on the HA host — if a git command fails, fall back to
  file operations and tell the user.
- Notification target for all alerts: `notify.gregs_iphone`.

---

## 4. Validate / reload / restart discipline

**Always validate before applying.** Use whichever is available:
- UI: Developer Tools → YAML → **Check Configuration**.
- CLI (if present): `ha core check`, or
  `python -m homeassistant --script check_config -c /config`.

**Know what reloads vs. what needs a full restart** (verify against the running HA
version — reloadability has changed across releases):
- **Reload live** (Developer Tools → YAML): modern `template:` entities, `input_number`,
  `input_boolean`, `automation`, `script`.
- **Generally require a full restart:** `statistics`, `history_stats`, `counter`,
  `utility_meter`, and `alert` (these are platform/integration config). Plan the test
  loop around this — batch these so you restart once, not per edit.

After any change: validate → reload or restart → confirm entities exist and carry no
template errors (check the HA log and the entity state, not just "no crash").

---

## 5. Work loop (one task at a time)

Follow the plan's build order (§12). For **each** task:
1. Implement only that task's scope.
2. Validate config.
3. Reload/restart as needed.
4. Run the task's **acceptance criteria** (in §12). Don't move on until they pass.
5. Commit with a clear message referencing the task (e.g. `Task 3: sump Layer 1
   per-cycle sensors`).

**Simulating pump activity for tests** (you can't always wait for a real cycle):
- Force an on/off edge: Developer Tools → States → set the `*_pump_running` binary
  sensor to `on`, wait, set to `off`. This drives the trigger-based sensors. (Note:
  manually-set states are overwritten on the next real update — fine for a quick test.)
- Force an alert: temporarily lower the relevant `input_number` (e.g.
  `*_max_run_seconds`) so the condition trips, confirm the notification, then restore.
- Flood/safety: set the `*_water_leak` sensor to `on` to exercise the flood alert and
  composites; set it `unavailable` (or simulate low battery) to exercise the fault
  watchdog.

**Don't spam the user's phone while testing.** Use the per-alert enable toggles, keep
a single device as target, and acknowledge/clear between tests. Confirm one push works
rather than firing every alert repeatedly.

---

## 6. Conventions

- Per-pump prefix `sump_` / `ejector_`; stable `unique_id` prefixed `pm_`.
- `state_class: measurement` on every numeric sensor (feeds Long-Term Statistics).
- `device_class`/`unit_of_measurement` correct for durations (s) and currents (A).
- **Availability-safe templates:** guard every source read against
  `unknown`/`unavailable`; default every `float()`; floor stddev in z-score math.
- **Idempotent:** regenerating a package must not duplicate entities — rely on stable
  `unique_id`s.
- Keep the two packages structurally parallel; honor the §6a/§6b per-pump differences
  (no-run & short-interval OFF for ejector; flood/safety ON for both).
- Comment the Roadmap seams with `# ROADMAP` / `# PHASE 2 SEAM` exactly where future
  rain/float/backup logic will attach.

---

## 7. Git workflow

- Conventional, descriptive commits, one logical task per commit.
- Never force-push; never rewrite shared history.
- Never commit secrets, tokens, the recorder DB, or anything from `/config` outside
  this repo. Add a `.gitignore` if any stray artifacts appear.
- The repo is safe to host public or private — keep it that way.

---

## 8. When blocked or uncertain

- If a fact you need isn't in the plan (e.g. the exact flood-sensor **battery entity
  ID**), surface it to the user and wait — don't guess. Task A explicitly includes
  confirming that ID.
- If history for threshold-seeding (Task A) isn't available, say so and proceed with
  the warm-up fallback, clearly flagged — don't fabricate "measured" values.
- If an acceptance criterion can't be met, stop and report rather than working around
  it silently.
- Anything touching the Roadmap scope (weather, float, backup) → leave a seam and
  move on; do not implement.

---

## 9. Definition of done

See plan §14. In short: both packages complete and passing all task checks; thresholds
seeded from real history (or warm-up-flagged); flood/safety alerts as the top tier on
a critical channel; core-only dashboard + remote panel rendering; all alerts routing
to `notify.gregs_iphone`; docs complete; Roadmap items left as commented seams.
