"""Fetch thread: polls the source, enriches records, publishes snapshots.

Never raises out of run(); the renderer must keep going no matter what
happens here. Exponential backoff on failure so a down endpoint is never
hammered.
"""
from __future__ import annotations

import logging
import threading
import time

from config import Config
from enrich.lookups import Lookups
from enrich.geo import haversine_nm, bearing_deg
from enrich.trend import TrendTracker
from fetch.sources import make_source, FetchError
from state import Aircraft, SharedState

log = logging.getLogger("fetch")

BACKOFF_BASE = 5.0     # seconds
BACKOFF_MAX = 300.0    # never wait longer than 5 min between retries


def parse_record(raw: dict, cfg: Config, lookups: Lookups, trends: TrendTracker) -> Aircraft | None:
    """Convert one v2-format record into an enriched Aircraft, or None to skip."""
    lat, lon = raw.get("lat"), raw.get("lon")
    if lat is None or lon is None:
        return None  # no position, nothing to display

    hex_id = str(raw.get("hex", "")).strip()
    callsign = str(raw.get("flight", "") or "").strip()

    alt = raw.get("alt_baro")
    on_ground = alt == "ground"
    altitude_ft = None if (alt is None or on_ground) else float(alt)

    gs = raw.get("gs")
    ground_speed_kt = float(gs) if gs is not None else None

    track = raw.get("track")
    track_deg = float(track) if track is not None else None

    dist = haversine_nm(cfg.lat, cfg.lon, lat, lon)
    brg = bearing_deg(cfg.lat, cfg.lon, lat, lon)
    trend = trends.update(hex_id, dist)

    type_code = str(raw.get("t", "") or "").strip().upper()

    return Aircraft(
        hex=hex_id,
        callsign=callsign,
        airline=lookups.airline_for_callsign(callsign),
        type_code=type_code,
        type_name=lookups.type_name(type_code),
        altitude_ft=altitude_ft,
        on_ground=on_ground,
        ground_speed_kt=ground_speed_kt,
        track_deg=track_deg,
        lat=float(lat),
        lon=float(lon),
        distance_nm=dist,
        bearing_deg=brg,
        trend=trend,
    )


def fetch_once(source, cfg: Config, lookups: Lookups, trends: TrendTracker,
               routes=None) -> list[Aircraft]:
    raw_list = source.fetch()
    aircraft = []
    for raw in raw_list:
        try:
            ac = parse_record(raw, cfg, lookups, trends)
        except (TypeError, ValueError) as e:
            log.debug("skipping malformed record: %s", e)
            continue
        if ac is not None:
            aircraft.append(ac)
    aircraft.sort(key=lambda a: a.distance_nm)
    trends.prune({a.hex for a in aircraft})

    # Resolve the city pair for the one featured flight (cheap, cached).
    if routes is not None:
        from enrich.motion import select_featured
        featured = select_featured(aircraft)
        if featured is not None and featured.callsign:
            r = routes.lookup(featured.callsign)
            if r:
                featured.origin_code = r["origin_code"]
                featured.origin_name = r["origin_name"]
                featured.dest_code = r["dest_code"]
                featured.dest_name = r["dest_name"]
    return aircraft


def run(state: SharedState, cfg: Config, stop: threading.Event) -> None:
    lookups = Lookups.load()
    trends = TrendTracker()
    from enrich.routes import RouteLookup
    routes = RouteLookup(timeout=min(cfg.timeout_seconds, 5.0))
    source = make_source(cfg.source, cfg.lat, cfg.lon, cfg.radius_nm, cfg.timeout_seconds)
    log.info("fetch thread up: %s every %ss", cfg.source, cfg.interval_seconds)

    failures = 0
    while not stop.is_set():
        try:
            aircraft = fetch_once(source, cfg, lookups, trends, routes)
            state.publish_success(aircraft)
            if failures:
                log.info("recovered after %d failures", failures)
            failures = 0
            wait = cfg.interval_seconds
        except FetchError as e:
            failures += 1
            state.publish_failure()
            wait = min(BACKOFF_BASE * (2 ** (failures - 1)), BACKOFF_MAX)
            log.warning("fetch failed (%d in a row): %s -- retrying in %.0fs", failures, e, wait)
        except Exception:
            # Truly unexpected; log it, back off, keep the thread alive.
            failures += 1
            state.publish_failure()
            wait = min(BACKOFF_BASE * (2 ** (failures - 1)), BACKOFF_MAX)
            log.exception("unexpected error in fetch loop -- retrying in %.0fs", wait)

        stop.wait(wait)

    log.info("fetch thread stopping")
