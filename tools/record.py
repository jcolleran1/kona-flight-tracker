#!/usr/bin/env python3
"""Record raw feed responses for regression tests and replay.

Run this on the Pi (or any machine on the home network) during a busy stretch,
for example the morning arrival bank:

    python3 tools/record.py --minutes 90

It polls the configured source(s) at the normal interval and appends every raw
response, timestamped, to a gzipped JSON Lines file under data/fixtures/live/.
Play it back with:

    [fetch]
    source = "replay"
    replay_file = "fixtures/live/<file>.jsonl.gz"

Recordings are for personal testing only and are git-ignored: the feeds'
terms don't allow redistributing their data.

Stays inside the same rate limits as the app (never faster than one request
every 5 seconds per source). Ctrl-C stops cleanly; a recording cut short by a
power loss is still readable up to its last complete line.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_DIR, load_config  # noqa: E402
from fetch.http import FetchError, http_get_json  # noqa: E402
from fetch.sources import NETWORK_SOURCES, make_source  # noqa: E402

MIN_INTERVAL = 5.0
METAR_URL = "https://aviationweather.gov/api/data/metar?ids={icao}&format=json"


def fetch_qnh(icao: str) -> float | None:
    """One METAR lookup so the replay knows the altimeter setting of the day."""
    try:
        data = http_get_json(METAR_URL.format(icao=icao), timeout=10)
        return float(data[0]["altim"])
    except (FetchError, LookupError, TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", action="append", choices=sorted(NETWORK_SOURCES),
                    help="source to record (repeatable); default: the configured one")
    ap.add_argument("--minutes", type=float, default=60.0, help="how long to record")
    ap.add_argument("--interval", type=float, default=None,
                    help=f"seconds between polls per source (minimum {MIN_INTERVAL:g})")
    ap.add_argument("--radius", type=float, default=None, help="radius in nm (default: config)")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "fixtures" / "live")
    ap.add_argument("--metar", default="PHKO", help="station for the altimeter setting")
    args = ap.parse_args()

    cfg = load_config()
    if args.radius:
        import dataclasses
        cfg = dataclasses.replace(cfg, radius_nm=args.radius)
    names = args.source or [cfg.source if cfg.source in NETWORK_SOURCES else "airplanes_live"]
    sources = [make_source(n, cfg) for n in names]
    interval = max(MIN_INTERVAL, args.interval or cfg.interval_seconds)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = args.out / f"{stamp}-{'+'.join(names)}.jsonl.gz"

    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    deadline = time.monotonic() + args.minutes * 60.0
    frames = errors = 0
    print(f"recording {names} every {interval:g}s for {args.minutes:g} min -> {path}")
    with gzip.open(path, "wt", encoding="utf-8") as out:
        meta = {"description": f"live recording {stamp}", "sources": names,
                "center": [cfg.lat, cfg.lon], "radius_nm": cfg.radius_nm}
        qnh = fetch_qnh(args.metar)
        if qnh:
            meta["qnh_hpa"] = qnh
        out.write(json.dumps({"meta": meta}, separators=(",", ":")) + "\n")

        while not stop and time.monotonic() < deadline:
            cycle_start = time.monotonic()
            for i, src in enumerate(sources):
                if stop:
                    break
                if i:
                    time.sleep(min(1.0, interval / len(sources)))  # stagger
                try:
                    payload = src.fetch_raw()
                except FetchError as e:
                    errors += 1
                    wait = e.retry_after or (60.0 if e.status in (403, 429) else 0.0)
                    print(f"  {src.name}: {e}" + (f" (pausing {wait:.0f}s)" if wait else ""))
                    if wait:
                        time.sleep(min(wait, 600.0))
                    continue
                out.write(json.dumps({"t": time.time(), "source": src.name, "data": payload},
                                     separators=(",", ":")) + "\n")
                frames += 1
            out.flush()
            if frames and frames % 60 == 0:
                print(f"  {frames} frames, {errors} errors")
            remaining = interval - (time.monotonic() - cycle_start)
            while remaining > 0 and not stop:
                time.sleep(min(0.5, remaining))
                remaining -= 0.5
    print(f"done: {frames} frames, {errors} errors, {path.stat().st_size / 1024:.0f} KB")
    return 0 if frames else 1


if __name__ == "__main__":
    sys.exit(main())
