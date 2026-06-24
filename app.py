#!/usr/bin/env python3
"""Flight tracker entry point.

Usage:
  python3 app.py            # full dashboard (framebuffer on Pi, window on dev)
  python3 app.py --once     # one fetch, print enriched aircraft to terminal (Phase 2 test)
  python3 app.py --windowed # force windowed mode regardless of config
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading

from config import load_config
from state import SharedState

log = logging.getLogger("app")


def setup_logging() -> None:
    # journald captures stdout; no file writes -> kind to the SD card.
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def run_once(cfg) -> int:
    """Phase 2 smoke test: fetch once, print the enriched board to stdout."""
    from enrich.lookups import Lookups
    from enrich.trend import TrendTracker
    from fetch.sources import make_source, FetchError
    from fetch.worker import fetch_once
    from enrich.geo import compass_point

    source = make_source(cfg.source, cfg.lat, cfg.lon, cfg.radius_nm, cfg.timeout_seconds)
    try:
        aircraft = fetch_once(source, cfg, Lookups.load(), TrendTracker())
    except FetchError as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        print("check network connectivity and that the API is reachable:", file=sys.stderr)
        print(f"  curl -s '{getattr(source, 'url', '?')}' | head -c 300", file=sys.stderr)
        return 1
    print(f"\n{len(aircraft)} aircraft within {cfg.radius_nm:g} nm of "
          f"({cfg.lat}, {cfg.lon})\n")
    print(f"{'FLIGHT':<10}{'AIRLINE':<26}{'AIRCRAFT':<26}{'ALT':>9}{'SPD':>7}{'DIST':>8}  BRG")
    for ac in aircraft:
        alt = "GROUND" if ac.on_ground else (f"{ac.altitude_ft:,.0f}" if ac.altitude_ft else "---")
        spd = f"{ac.ground_speed_kt:.0f}" if ac.ground_speed_kt else "---"
        print(f"{(ac.callsign or ac.hex):<10}{(ac.airline or '-'):<26}"
              f"{(ac.type_name or ac.type_code or '-'):<26}{alt:>9}{spd:>7}"
              f"{ac.distance_nm:>7.1f}  {compass_point(ac.bearing_deg)}")
    print()
    return 0


class FetchSupervisor:
    """Owns the fetch thread; can kill and respawn it (watchdog action)."""

    def __init__(self, state: SharedState, cfg):
        self.state, self.cfg = state, cfg
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        from fetch.worker import run as fetch_run
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=fetch_run, args=(self.state, self.cfg, self.stop_event),
            name="fetch", daemon=True,
        )
        self.thread.start()

    def restart(self) -> None:
        log.warning("restarting fetch thread")
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.start()

    def stop(self) -> None:
        self.stop_event.set()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="fetch once, print, exit")
    parser.add_argument("--windowed", action="store_true", help="force windowed mode")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config()
    if args.windowed:
        import dataclasses
        cfg = dataclasses.replace(cfg, fullscreen=False)

    if args.once:
        return run_once(cfg)

    from render.renderer import Renderer

    state = SharedState()
    supervisor = FetchSupervisor(state, cfg)
    supervisor.start()

    stopping = threading.Event()
    renderer = Renderer(cfg, state, on_watchdog=supervisor.restart)
    try:
        renderer.run(stop_check=stopping.is_set)
    finally:
        supervisor.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
