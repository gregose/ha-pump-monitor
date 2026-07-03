#!/usr/bin/env python3
"""
seed_thresholds.py — Task A (plan §6c): derive adaptive-threshold seed values
from a pump's own recorded on/off history, so the no-run watchdog and short-
interval floors start from MEASURED cadence instead of the guessed ~30 min.

INPUT
  A CSV of the pump's running binary_sensor state changes. The script is
  tolerant of the two common Home Assistant export shapes:

    1. Developer Tools → History → "Download Data":
         entity_id,state,last_changed
    2. A recorder SQL dump of states:
         last_changed,state           (or last_updated,state)

  Only two things are needed per row: a timestamp and a state (on/off). Column
  names are auto-detected; extra columns are ignored. Rows whose state is not
  exactly on/off (e.g. unavailable) are dropped, and consecutive duplicate
  states are collapsed so only real edges remain.

USAGE
  python3 seed_thresholds.py sump_history.csv
  python3 seed_thresholds.py sump_history.csv --pump sump   # label output

OUTPUT
  A summary table plus a ready-to-paste block of input_number initials for
  packages/pump_monitor_helpers.yaml, and the value to set *_no_run_history_ready
  to ON once applied. Nothing is written back automatically — you review first.

No third-party dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import datetime


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
TS_KEYS = ("last_changed", "last_updated", "timestamp", "time", "date")
STATE_KEYS = ("state", "value")


def parse_ts(raw: str) -> float | None:
    """Return an epoch-seconds float from an ISO-ish timestamp, or None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    # normalise trailing Z to +00:00 for fromisoformat
    txt = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(txt).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
    return None


def load_edges(path: str) -> list[tuple[float, str]]:
    """Load (epoch, state) rows, keep only on/off, collapse duplicate states."""
    with open(path, newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        # sniff header vs headerless
        reader = csv.reader(fh)
        rows = list(reader)

    if not rows:
        sys.exit(f"error: {path} is empty")

    header = [c.strip().lower() for c in rows[0]]
    has_header = any(h in TS_KEYS + STATE_KEYS for h in header)

    if has_header:
        ts_i = next((header.index(k) for k in TS_KEYS if k in header), None)
        st_i = next((header.index(k) for k in STATE_KEYS if k in header), None)
        data = rows[1:]
        if ts_i is None or st_i is None:
            sys.exit(f"error: could not find timestamp/state columns in {header}")
    else:
        # headerless: assume [timestamp, state] or [entity_id, state, ts]
        ncols = len(rows[0])
        ts_i, st_i = (0, 1) if ncols == 2 else (2, 1)
        data = rows

    edges: list[tuple[float, str]] = []
    for r in data:
        if len(r) <= max(ts_i, st_i):
            continue
        state = r[st_i].strip().lower()
        if state not in ("on", "off"):
            continue
        ts = parse_ts(r[ts_i])
        if ts is None:
            continue
        edges.append((ts, state))

    edges.sort(key=lambda e: e[0])

    # collapse consecutive identical states → only transitions survive
    collapsed: list[tuple[float, str]] = []
    for ts, st in edges:
        if not collapsed or collapsed[-1][1] != st:
            collapsed.append((ts, st))
    return collapsed


# --------------------------------------------------------------------------- #
# cycle reconstruction
# --------------------------------------------------------------------------- #
def reconstruct(edges):
    """From on/off edges build lists of durations, intervals, idle gaps (secs)."""
    starts = [ts for ts, st in edges if st == "on"]
    stops = [ts for ts, st in edges if st == "off"]

    # run durations: each on paired with the next off
    durations = []
    for ts, st in edges:
        if st == "on":
            nxt = next((t for t, s in edges if s == "off" and t > ts), None)
            if nxt is not None:
                durations.append(nxt - ts)

    intervals = [b - a for a, b in zip(starts, starts[1:])]

    # idle gap: previous stop → next start
    idle_gaps = []
    for start in starts:
        prev_stop = max((t for t in stops if t < start), default=None)
        if prev_stop is not None:
            idle_gaps.append(start - prev_stop)

    return durations, intervals, idle_gaps


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def fmt(sec: float) -> str:
    return f"{sec:8.0f}s ({sec/60:6.1f} min)"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="path to the pump running-history CSV")
    ap.add_argument("--pump", default="sump", choices=("sump", "ejector"),
                    help="which pump these values are for (labels the output)")
    args = ap.parse_args()

    edges = load_edges(args.csv)
    starts = [e for e in edges if e[1] == "on"]
    if len(starts) < 5:
        sys.exit(f"error: only {len(starts)} start edges found — need more history "
                 "for meaningful percentiles; keep using the warm-up fallback.")

    durations, intervals, idle_gaps = reconstruct(edges)
    span_days = (edges[-1][0] - edges[0][0]) / 86400

    interval_median = statistics.median(intervals) if intervals else 0
    interval_p05 = pct(intervals, 5)
    interval_p95 = pct(intervals, 95)
    interval_p99 = pct(intervals, 99)
    dur_median = statistics.median(durations) if durations else 0
    dur_std = statistics.pstdev(durations) if len(durations) > 1 else 0
    idle_median = statistics.median(idle_gaps) if idle_gaps else 0

    p = args.pump

    print(f"\n=== Task A: {p} threshold seeding =========================")
    print(f"history span         : {span_days:.1f} days")
    print(f"completed starts     : {len(starts)}")
    print(f"run durations        : n={len(durations)}")
    print(f"start-to-start ints  : n={len(intervals)}")
    print("-----------------------------------------------------------")
    print(f"interval  median     : {fmt(interval_median)}")
    print(f"interval  p05        : {fmt(interval_p05)}   (short-interval floor)")
    print(f"interval  p95        : {fmt(interval_p95)}")
    print(f"interval  p99        : {fmt(interval_p99)}   (≈ longest-normal gap)")
    print(f"duration  median     : {fmt(dur_median)}")
    print(f"duration  stddev     : {fmt(dur_std)}")
    print(f"idle gap  median     : {fmt(idle_median)}")
    print("-----------------------------------------------------------")

    # Derived helper seeds. These clamp/anchor the ADAPTIVE no-run watchdog,
    # which already tracks the live 14-day median × multiplier at runtime:
    #  no_run_ceiling_min  → p99 of interval (minutes): the longest NORMAL gap,
    #                        so a legitimately-long-but-normal quiet spell can't
    #                        trip the watchdog. Padded 20%.
    #  no_run_floor_min    → ~half the median cadence (floored at 15 min): keeps
    #                        the watchdog from firing on a single skipped cycle.
    #
    # short_interval_seconds is deliberately NOT overwritten from p05. p05 over a
    # rain-free window ≈ the median (see the numbers above) — writing it into the
    # FIXED floor would fire the alert on the bottom 5% of perfectly normal
    # cycles. The binary sensor already folds the LIVE sensor.{pump}_interval_p05_14d
    # in via max(fixed, live-p05); over a window that includes storms that live
    # p05 drops to the rain-driven fast-cycle band, which is the real "approaching
    # capacity" signal. Keep the fixed floor at its physical default (30 s) and
    # let the live p05 do the data-driven work.
    ceiling_min = max(30, round(interval_p99 * 1.2 / 60))
    floor_min = max(15, round((interval_median / 60) / 2))

    print("Paste into packages/pump_monitor_helpers.yaml (input_number initials):\n")
    print(f"  {p}_no_run_ceiling_min:      initial: {ceiling_min}")
    print(f"  {p}_no_run_floor_min:        initial: {floor_min}")
    print(f"  # {p}_short_interval_seconds: leave at physical default (30);"
          f" live p05={interval_p05/60:.1f} min is consumed at runtime.")
    print(f"\nThen set input_boolean.{p}_no_run_history_ready → ON (adaptive mode).")
    print("(no_run_multiplier stays at its default 3× the live 14-day median.)\n")


if __name__ == "__main__":
    main()
