#!/usr/bin/env python3
"""Flight tracker entry point.

Usage:
  python3 app.py            # full dashboard (fullscreen on the Pi)
  python3 app.py --once     # fetch once, print the enriched board to the terminal
  python3 app.py --windowed # force windowed mode regardless of config
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from config import ConfigError, load_config
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


def run_once(cfg, wait_for_enrichment: float = 8.0) -> int:
    """Smoke test: poll every source once, give the background lookups a few
    seconds, and print what the wall would show. No display needed."""
    from enrich.geo import compass_point
    from fetch.worker import FetchLoop
    from render.units import fmt_altitude, fmt_distance, fmt_speed

    # One sample is all --once gets, so don't wait for a phase to be confirmed.
    loop = FetchLoop(cfg, confirm_seconds=0.0)
    ok = 0
    for st in loop.sources:
        if loop.poll(st):
            ok += 1
            print(f"{st.name}: {st.count} aircraft with a position")
        else:
            print(f"{st.name}: FAILED: {st.last_error}", file=sys.stderr)
            print(f"  try: curl -s '{getattr(st.source, 'url', '?')}' | head -c 300",
                  file=sys.stderr)
        time.sleep(0.2)
    if not ok:
        print("no source answered; check the network connection", file=sys.stderr)
        return 1

    # First pass queues the route / airframe lookups; let them come back.
    loop.rebuild()
    loop.enricher.start()
    deadline = time.monotonic() + wait_for_enrichment
    while time.monotonic() < deadline and not loop.enricher.idle():
        time.sleep(0.25)
    loop.rebuild()
    loop.enricher.stop()

    qnh = loop.enricher.qnh()
    print(f"\n{len(loop.aircraft)} aircraft within {cfg.radius_nm:g} nm of "
          f"{cfg.airport_iata} ({cfg.lat}, {cfg.lon})")
    print("altimeter: " + (f"{qnh:.1f} hPa ({qnh / 33.8639:.2f} inHg)" if qnh
                           else "unknown (using each aircraft's own setting)") + "\n")
    print(f"{'':2}{'FLIGHT':<9}{'CALLSIGN':<9}{'AIRLINE':<24}{'AIRCRAFT':<24}"
          f"{'ALT':>10}{'SPD':>8}{'DIST':>7} {'BRG':<4}{'PHASE':<11}ROUTE")
    for ac in loop.aircraft:
        route = f"{ac.origin_code}>{ac.dest_code}" if ac.origin_code else "-"
        print(f"{'*' if ac.hex == loop.featured_hex else ' ':2}"
              f"{(ac.label or ac.hex):<9}{(ac.callsign or '-'):<9}"
              f"{(ac.airline or '-')[:23]:<24}{(ac.type_name or ac.type_code or '-')[:23]:<24}"
              f"{fmt_altitude(ac.altitude_ft, ac.on_ground, cfg.altitude_units, phase=ac.phase, vertical_rate_fpm=ac.vertical_rate_fpm):>10}"
              f"{fmt_speed(ac.ground_speed_kt, cfg.speed_units):>8}"
              f"{fmt_distance(ac.distance_nm, cfg.distance_units):>7} "
              f"{compass_point(ac.bearing_deg):<4}{ac.phase:<11}{route}")
    print("\n* = featured flight")
    return 0


class FetchSupervisor:
    """Owns the fetch thread; can replace it (watchdog action).

    A replaced thread may be stuck inside a socket call and can't be killed,
    so it is abandoned: its stop token is set, and it checks that token before
    every publish, so it can never write to the display again. restart() does
    not wait for it, because it is called from the render loop.
    """

    def __init__(self, state: SharedState, cfg):
        self.state, self.cfg = state, cfg
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._generation = 0

    def start(self) -> None:
        from fetch.worker import run as fetch_run
        self._generation += 1
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=fetch_run, args=(self.state, self.cfg, self.stop_event),
            name=f"fetch-{self._generation}", daemon=True,
        )
        self.thread.start()

    def restart(self) -> None:
        log.warning("restarting fetch thread")
        self.stop_event.set()
        self.start()

    def stop(self) -> None:
        self.stop_event.set()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="fetch once, print, exit")
    parser.add_argument("--windowed", action="store_true", help="force windowed mode")
    args = parser.parse_args()

    setup_logging()
    try:
        cfg = load_config()
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
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

    def _shutdown(signum, _frame):
        log.info("signal %s: shutting down", signal.Signals(signum).name)
        stopping.set()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    renderer = Renderer(cfg, state, on_watchdog=supervisor.restart)
    try:
        renderer.run(stop_check=stopping.is_set)
    finally:
        supervisor.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
