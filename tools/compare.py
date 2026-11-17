#!/usr/bin/env python3
"""Log, once a second, exactly what the wall would show, for checking against
FlightRadar24 (the acceptance test at the end of PLAN.md).

    python3 tools/compare.py                  # 10 minutes, aircraft within 25 nm
    python3 tools/compare.py --minutes 30 --within 40

Run it during a busy stretch, take FR24 screenshots of the same flights at the
same moments (the clock time is in every row), and compare:
  * flight number format matches FR24
  * altitude within about 200 ft below 18,000 ft, never negative
  * position within an icon's width
  * arriving / departing / overflight label is right
  * route is right, or blank (never wrong)

Writes compare-<date>-<time>.csv in the current directory and prints a short
table as it goes. It polls the same sources at the same rate as the wall, so
running it next to the wall doubles the requests from this address; that is
still far inside every feed's limit, but don't leave it running.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import load_config  # noqa: E402
from enrich.deadreckon import Smoother  # noqa: E402
from fetch.worker import FetchLoop  # noqa: E402
from render.timeutil import local_now  # noqa: E402
from render.units import fmt_altitude, fmt_speed  # noqa: E402

FIELDS = ["time", "flight", "callsign", "registration", "airline", "type", "altitude",
          "altitude_ft", "speed", "lat", "lon", "position_age_s", "distance_nm", "phase",
          "origin", "dest", "source"]


def rows(loop: FetchLoop, cfg, smoother: Smoother, within_nm: float, now: float, stamp: str):
    for ac in loop.aircraft:
        if ac.distance_nm > within_nm or now - ac.pos_mono > cfg.max_position_age_seconds:
            continue
        lat, lon = smoother.position(ac, now)         # where the map draws it right now
        yield {
            "time": stamp, "flight": ac.label, "callsign": ac.callsign,
            "registration": ac.registration, "airline": ac.airline,
            "type": ac.type_code or ac.type_name,
            "altitude": fmt_altitude(ac.altitude_ft, ac.on_ground, cfg.altitude_units,
                                     phase=ac.phase, vertical_rate_fpm=ac.vertical_rate_fpm),
            "altitude_ft": "" if ac.altitude_ft is None else f"{ac.altitude_ft:.0f}",
            "speed": fmt_speed(ac.ground_speed_kt, cfg.speed_units),
            "lat": f"{lat:.5f}", "lon": f"{lon:.5f}",
            "position_age_s": f"{now - ac.pos_mono:.1f}",
            "distance_nm": f"{ac.distance_nm:.1f}", "phase": ac.phase,
            "origin": ac.origin_code, "dest": ac.dest_code, "source": ac.source,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--within", type=float, default=25.0, help="nautical miles from the airport")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    cfg = load_config()
    loop = FetchLoop(cfg)
    loop.enricher.start()
    smoother = Smoother(cfg.tween_seconds, cfg.max_position_age_seconds, enabled=cfg.dead_reckoning)
    out = args.out or Path(f"compare-{dt.datetime.now():%Y%m%d-%H%M%S}.csv")
    deadline = time.monotonic() + args.minutes * 60.0
    print(f"logging aircraft within {args.within:g} nm of {cfg.airport_iata} to {out} "
          f"for {args.minutes:g} min (Ctrl-C to stop)")
    written = 0
    try:
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            next_row = time.monotonic()
            while time.monotonic() < deadline:
                if loop.next_due() <= time.monotonic():
                    loop.tick()
                now = time.monotonic()
                if now >= next_row:
                    next_row += 1.0
                    stamp = local_now(cfg).strftime("%H:%M:%S")
                    batch = list(rows(loop, cfg, smoother, args.within, now, stamp))
                    writer.writerows(batch)
                    written += len(batch)
                    f.flush()
                    if batch and int(now) % 5 == 0:
                        print(f"\n{stamp}")
                        for r in batch:
                            route = f"{r['origin']}>{r['dest']}" if r["origin"] else "-"
                            print(f"  {r['flight'] or '?':<9}{r['altitude']:>10}{r['speed']:>8}"
                                  f"  {r['lat']},{r['lon']}  {r['phase']:<10} {route}")
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        loop.enricher.stop()
    print(f"\n{written} rows written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
