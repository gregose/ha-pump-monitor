#!/usr/bin/env python3
"""
rain_correlation.py — correlate sump cycling with public rainfall.

Uses public hourly precipitation (Open-Meteo, no API key) as a stand-in weather
station to (a) prove the storm→inflow signal, (b) split the pump's cadence into a
true DRY-weather baseline vs storm cadence, (c) measure the rain dose-response,
and (d) flag fast-cycling-WITHOUT-rain episodes (leak / check-valve / intrusion).

This is the Phase-1 preview of what the GW3001 + WH40BH will do natively once the
Ecowitt local integration is wired (plan §11 weather seam / `runtime_per_mm`).

INPUT
  Same pump running-history CSV as seed_thresholds.py (entity_id/state/timestamp;
  UTC 'Z' timestamps as exported by HA's History → Download Data).

USAGE
  python3 rain_correlation.py sump_history.csv                 # pass --lat/--lon for your location
  python3 rain_correlation.py sump_history.csv --lat 41.88 --lon -87.63
  python3 rain_correlation.py sump_history.csv --archive       # ERA5 archive (lags ~5d)

NETWORK
  Needs api.open-meteo.com (recent, incl. current storm) or, with --archive,
  archive-api.open-meteo.com. In the sandbox both must be allowlisted:
    sbx policy allow network api.open-meteo.com,archive-api.open-meteo.com

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _HAVE_ZI = True
except Exception:  # pragma: no cover
    _HAVE_ZI = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_thresholds import load_edges, pct  # reuse the tolerant CSV loader


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #
def local_hour(epoch: float, tzinfo, fixed_offset: float) -> datetime:
    """Epoch → naive local datetime truncated to the hour (matches Open-Meteo)."""
    if tzinfo is not None:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tzinfo)
    else:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc) + timedelta(hours=fixed_offset)
    return dt.replace(minute=0, second=0, microsecond=0, tzinfo=None)


# --------------------------------------------------------------------------- #
# rainfall fetch
# --------------------------------------------------------------------------- #
def fetch_precip(lat, lon, start_date, end_date, tzname, use_archive):
    """Return {naive-local-hour datetime: inches} for [start_date, end_date]."""
    common = (f"latitude={lat}&longitude={lon}&hourly=precipitation"
              f"&precipitation_unit=inch&timezone={urllib_quote(tzname)}")
    if use_archive:
        url = (f"https://archive-api.open-meteo.com/v1/archive?{common}"
               f"&start_date={start_date}&end_date={end_date}")
    else:
        # forecast endpoint with past_days covers recent data incl. the current storm
        today = datetime.now(ZoneInfo(tzname)).date() if _HAVE_ZI else datetime.utcnow().date()
        past_days = max(1, min(92, (today - _d(start_date)).days + 1))
        url = (f"https://api.open-meteo.com/v1/forecast?{common}"
               f"&past_days={past_days}&forecast_days=1")

    req = urllib.request.Request(url, headers={"User-Agent": "ha-pump-monitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 403 and "network policy" in body:
            sys.exit("error: blocked by sandbox firewall. On your host run:\n"
                     "  sbx policy allow network api.open-meteo.com,archive-api.open-meteo.com")
        sys.exit(f"error: Open-Meteo HTTP {e.code}: {body[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"error: could not reach Open-Meteo: {e.reason}")

    h = data.get("hourly", {})
    out = {}
    for ts, val in zip(h.get("time", []), h.get("precipitation", [])):
        out[datetime.fromisoformat(ts)] = float(val or 0.0)
    return out, url


def urllib_quote(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="")


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


# --------------------------------------------------------------------------- #
# stats helpers
# --------------------------------------------------------------------------- #
def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def fmt_secs(s):
    return f"{s:6.0f}s ({s/60:5.1f}m)" if s else "     n/a"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="pump running-history CSV")
    ap.add_argument("--lat", type=float, default=41.88, help="latitude (default: Chicago; set to your location)")
    ap.add_argument("--lon", type=float, default=-87.63, help="longitude (default: Chicago; set to your location)")
    ap.add_argument("--tz", default="America/Chicago", help="IANA tz for alignment/display")
    ap.add_argument("--pump", default="sump")
    ap.add_argument("--archive", action="store_true", help="use ERA5 archive (higher quality, lags ~5d)")
    ap.add_argument("--wet-window", type=int, default=3, help="hours (this + prior) counted as 'wet' around a start")
    ap.add_argument("--wet-thresh", type=float, default=0.01, help="inches over the window to call a cycle 'wet'")
    ap.add_argument("--anomaly-lookback", type=int, default=0, help="hours of dryness required to flag a leak (0 = auto: best-lag + wet-window)")
    ap.add_argument("--utc-offset", type=float, default=-5.0, help="fallback offset if zoneinfo unavailable")
    args = ap.parse_args()

    tzinfo = ZoneInfo(args.tz) if _HAVE_ZI else None
    if tzinfo is None:
        print(f"note: zoneinfo unavailable; using fixed offset {args.utc_offset}h", file=sys.stderr)

    edges = load_edges(args.csv)
    starts = [ts for ts, st in edges if st == "on"]
    stops = [ts for ts, st in edges if st == "off"]
    if len(starts) < 5:
        sys.exit(f"error: only {len(starts)} starts — need more history.")

    lo = local_hour(edges[0][0], tzinfo, args.utc_offset)
    hi = local_hour(edges[-1][0], tzinfo, args.utc_offset)
    precip, url = fetch_precip(args.lat, args.lon,
                               lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"),
                               args.tz, args.archive)

    # window precip around a start (this hour + prior N-1 hours)
    def window_precip(epoch):
        h0 = local_hour(epoch, tzinfo, args.utc_offset)
        return sum(precip.get(h0 - timedelta(hours=k), 0.0) for k in range(args.wet_window))

    # per-interval + per-idle-gap, tagged wet/dry by the later start
    dry_iv, wet_iv, dry_gap, wet_gap = [], [], [], []
    for i in range(1, len(starts)):
        iv = starts[i] - starts[i - 1]
        prev_stop = max((s for s in stops if s < starts[i]), default=None)
        gap = (starts[i] - prev_stop) if prev_stop is not None else None
        if window_precip(starts[i]) >= args.wet_thresh:
            wet_iv.append(iv)
            if gap is not None:
                wet_gap.append(gap)
        else:
            dry_iv.append(iv)
            if gap is not None:
                dry_gap.append(gap)

    # hourly aligned series (starts/hour vs precip/hour) over the whole range
    starts_by_hour = {}
    for s in starts:
        h = local_hour(s, tzinfo, args.utc_offset)
        starts_by_hour[h] = starts_by_hour.get(h, 0) + 1
    hours = []
    cur = lo
    while cur <= hi:
        hours.append(cur)
        cur += timedelta(hours=1)
    s_series = [starts_by_hour.get(h, 0) for h in hours]
    p_series = [precip.get(h, 0.0) for h in hours]

    # lagged correlation (rain leads pump by k hours)
    best = (0, pearson(p_series, s_series))
    for lag in range(1, 7):
        r = pearson(p_series[:-lag], s_series[lag:])
        if r > best[1]:
            best = (lag, r)

    p = args.pump
    print(f"\n=== Rain × {p} cadence — {args.lat:.3f},{args.lon:.3f} ({args.tz}) ===")
    print(f"history : {lo.date()} → {hi.date()}  ({len(starts)} starts)")
    print(f"rainfall: {'archive ERA5' if args.archive else 'forecast/past_days'} — {sum(p_series):.2f} in total over window")
    print(f"          {url}")

    print("\n--- correlation (hourly rain vs starts) ---")
    print(f"Pearson r (lag 0h) : {pearson(p_series, s_series):+.2f}")
    print(f"best lag           : {best[0]}h   r={best[1]:+.2f}   (rain leads cycling)")

    print(f"\n--- DRY vs WET cadence (wet = ≥{args.wet_thresh}\" over {args.wet_window}h window) ---")
    def col(vals):
        return (f"{len(vals):>5}  "
                f"{fmt_secs(statistics.median(vals)) if vals else '   n/a':>16}")
    print(f"                     n   median interval")
    print(f"  dry cycles     {col(dry_iv)}")
    print(f"  wet cycles     {col(wet_iv)}")
    if dry_gap and wet_gap:
        print(f"  dry idle-gap median : {fmt_secs(statistics.median(dry_gap))}")
        print(f"  wet idle-gap median : {fmt_secs(statistics.median(wet_gap))}")

    print("\n--- rain dose-response (mean starts/hr by hourly intensity) ---")
    buckets = [(0.0, 0.001, "0.00\""), (0.001, 0.05, "trace–0.05\""),
               (0.05, 0.15, "0.05–0.15\""), (0.15, 99, ">0.15\"")]
    for lob, hib, lbl in buckets:
        sel = [s for s, pr in zip(s_series, p_series) if lob <= pr < hib]
        if sel:
            print(f"  {lbl:12} : {statistics.mean(sel):5.1f} starts/hr   (n={len(sel):>3} hrs)")

    # lag-aware dryness window: to call fast cycling "dry" we must see NO rain
    # across the whole rain→cycling drainage window (measured lag + spread),
    # otherwise storm runoff gets mislabeled as a leak.
    lookback = args.anomaly_lookback or max(12, best[0] + args.wet_window)
    print(f"\n--- fast cycling WITHOUT rain (possible leak / check-valve; dry over prior {lookback}h) ---")
    if dry_iv:
        dry_med = statistics.median(dry_iv)
        base_rate = 3600 / dry_med
        flagged = 0
        for h, cnt in sorted(starts_by_hour.items()):
            win = sum(precip.get(h - timedelta(hours=k), 0.0) for k in range(lookback))
            if cnt >= 2 * base_rate and win < args.wet_thresh:
                print(f"  {h}  —  {cnt} starts/hr  vs dry-baseline ~{base_rate:.1f}/hr,  {win:.2f}\" prior {lookback}h")
                flagged += 1
        if not flagged:
            print("  (none — every fast-cycling episode had rain within the drainage window;")
            print("   check valve & seals look fine)")

    print(f"\n--- re-seeded no-run clamps from the DRY baseline ---")
    if dry_iv:
        dry_p99 = pct(dry_iv, 99)
        print(f"  {p}_no_run_ceiling_min:  initial: {max(30, round(dry_p99 * 1.2 / 60))}   (dry p99 {dry_p99/60:.1f}m +20%)")
        print(f"  {p}_no_run_floor_min:    initial: {max(15, round((statistics.median(dry_iv)/60)/2))}")
        print(f"  dry-weather median interval = {statistics.median(dry_iv)/60:.1f} min "
              f"(vs blended Task A seed; storms pull the blended value shorter)")
    print()


if __name__ == "__main__":
    main()
