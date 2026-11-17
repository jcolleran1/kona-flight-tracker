"""Turns merged raw feed records into the Aircraft the display shows.

Owned by the fetch thread. Holds the small amount of per-aircraft memory that
makes the display steady: phase hysteresis, direction, trail, first-seen time.
Everything that decides *what* to show lives in the pure modules next to this
one (altitude, lookups, motion, routes); this file is the plumbing between
them. It never touches the network: enrichment answers are read from cache.
"""
from __future__ import annotations

import logging
from collections import deque

from config import Config
from enrich.altitude import resolve_altitude, vertical_rate
from enrich.geo import bearing_deg, haversine_nm, valid_position
from enrich.lookups import (AIRLINE_FLIGHT_RE, Lookups, airline_prefix, clean_callsign,
                            clean_registration, clean_text)
from enrich.motion import (ClassifyParams, FeaturedSelector, PhaseTracker, closing_speed_kt,
                           combine_with_route, commitment_holds, direction_of,
                           kinematic_phase, neutral_phase)
from enrich.routes import HOME_GROUND_RADIUS_NM, validate_route
from state import Aircraft

log = logging.getLogger("enrich")

FORGET_AFTER_SECONDS = 180.0     # per-aircraft memory kept this long after last sight
TRAIL_MIN_STEP_NM = 0.25
TRAIL_MAX_POINTS = 48


def _num(v, lo: float, hi: float) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or not (lo <= v <= hi):
        return None
    return float(v)


def _pos_source(raw: dict) -> str:
    kind = str(raw.get("type") or "")
    if kind.startswith("mlat") or "lat" in (raw.get("mlat") or ()):
        return "mlat"
    if kind.startswith(("adsb", "adsr")):
        return "adsb"
    return "other"


class _Memory:
    __slots__ = ("phase", "direction", "first_seen", "last_seen", "trail")

    def __init__(self, now: float, confirm_seconds: float):
        self.phase = PhaseTracker(confirm_seconds)
        self.direction = ""
        self.first_seen = now
        self.last_seen = now
        self.trail: deque = deque(maxlen=TRAIL_MAX_POINTS)     # (lat, lon, t)


class Tracker:
    def __init__(self, cfg: Config, lookups: Lookups, enricher,
                 confirm_seconds: float = 12.0):
        self.cfg = cfg
        self.confirm_seconds = confirm_seconds
        self.lookups = lookups
        self.enricher = enricher
        self.params = ClassifyParams(
            rate_fpm=cfg.vertical_rate_threshold_fpm,
            overflight_min_alt_ft=cfg.overflight_min_altitude_ft,
        )
        self.home_codes = (cfg.airport_iata, cfg.airport_icao)
        self.featured = FeaturedSelector(cfg.feature_dwell_seconds)
        self._mem: dict[str, _Memory] = {}

    # ------------------------------------------------------------------

    def update(self, records: list[dict], now: float) -> tuple[list[Aircraft], str | None]:
        """Records from fetch.merge -> (aircraft sorted by distance, featured hex)."""
        qnh = self.enricher.qnh()
        aircraft = []
        for raw in records:
            try:
                ac = self._build(raw, now, qnh)
            except Exception:
                # one malformed record must never take the whole update down
                log.debug("skipping malformed record %r", raw.get("hex"), exc_info=True)
                continue
            if ac is not None:
                aircraft.append(ac)
        aircraft.sort(key=lambda a: a.distance_nm)

        for hex_id in [h for h, m in self._mem.items() if now - m.last_seen > FORGET_AFTER_SECONDS]:
            del self._mem[hex_id]
        return aircraft, self.featured.select(aircraft, now)

    # ------------------------------------------------------------------

    def _build(self, raw: dict, now: float, qnh: float | None) -> Aircraft | None:
        cfg = self.cfg
        hex_id = str(raw.get("hex") or "").strip().lower()
        if not hex_id:
            return None
        anonymous = hex_id.startswith("~")
        if anonymous and not cfg.show_anonymous:
            return None
        category = clean_text(raw.get("category") or "", 2).upper()
        if category.startswith("C"):
            return None                      # surface vehicles and fixed obstacles
        lat, lon = raw.get("lat"), raw.get("lon")
        if not valid_position(lat, lon):
            return None
        pos_mono = float(raw.get("_pos_mono", now))
        if now - pos_mono > cfg.max_position_age_seconds:
            return None                      # stale: dropped, never drawn
        distance = haversine_nm(cfg.lat, cfg.lon, lat, lon)
        if distance > cfg.radius_nm:
            return None                      # a local receiver hears far past the area we show
        bearing = bearing_deg(cfg.lat, cfg.lon, lat, lon)

        # ---- identity ----
        callsign = clean_callsign(raw.get("flight"))
        registration = clean_registration(raw.get("r"))
        type_code = clean_text(raw.get("t") or "", 4).upper()
        type_desc = clean_text(raw.get("desc") or "", 34)
        owner = clean_text(raw.get("ownOp") or "", 40)

        info = None if anonymous else self.enricher.aircraft(hex_id)
        if info is not None:
            registration = registration or info.registration
            type_code = type_code or info.type_code
            type_desc = type_desc or info.type_name
            owner = owner or info.owner
        elif not anonymous and (not registration or not type_code):
            self.enricher.request_aircraft(hex_id, priority=-distance)
        type_name = self.lookups.type_name(type_code) or type_desc

        # ---- state ----
        alt = resolve_altitude(
            raw, qnh, transition_ft=cfg.transition_altitude_ft,
            ground_max_altitude_ft=cfg.ground_max_altitude_ft,
            ground_max_speed_kt=cfg.ground_max_speed_kt)
        rate = None if alt.on_ground else vertical_rate(raw)
        gs = _num(raw.get("gs"), 0.0, 2500.0)
        track = _num(raw.get("track"), 0.0, 360.0)
        if track is None and alt.on_ground:
            track = _num(raw.get("true_heading"), 0.0, 360.0)
        track_rate = _num(raw.get("track_rate"), -10.0, 10.0)

        mem = self._mem.get(hex_id)
        if mem is None:
            mem = self._mem[hex_id] = _Memory(now, self.confirm_seconds)
        mem.last_seen = now

        # ---- movement ----
        kin = kinematic_phase(
            on_ground=alt.on_ground, altitude_ft=alt.feet, vertical_rate_fpm=rate,
            ground_speed_kt=gs, track_deg=track, distance_nm=distance,
            bearing_from_home=bearing, field_elevation_ft=cfg.airport_elevation_ft,
            p=self.params)

        route = None
        if callsign and AIRLINE_FLIGHT_RE.match(callsign):
            route = self.enricher.route(callsign)
            if route is None:
                tier = 100.0 if kin in ("arriving", "departing") else 0.0
                self.enricher.request_route(callsign, priority=tier - distance)
        shown_route = route
        ok, why = validate_route(
            route, lat=lat, lon=lon, track_deg=track, altitude_ft=alt.feet,
            on_ground=alt.on_ground, kinematic=kin, distance_home_nm=distance,
            home_codes=self.home_codes)
        if not ok:
            if route is not None:
                log.debug("route for %s rejected: %s", callsign, why)
            shown_route = None
        origin_home = bool(shown_route and shown_route.origin.matches(*self.home_codes))
        dest_home = bool(shown_route and shown_route.destination.matches(*self.home_codes))

        closing = closing_speed_kt(gs, track, bearing)
        candidate = combine_with_route(kin, origin_is_home=origin_home, dest_is_home=dest_home)
        phase = mem.phase.update(
            candidate, now,
            neutral=neutral_phase(alt.feet, self.params),
            near_home=distance <= HOME_GROUND_RADIUS_NM,
            holds=commitment_holds(
                mem.phase.phase, altitude_ft=alt.feet, vertical_rate_fpm=rate,
                closing_kt=closing, distance_nm=distance,
                field_elevation_ft=cfg.airport_elevation_ft, p=self.params))
        # The settled phase gets the last word on the route: a takeoff roll
        # looks like a landing for a few seconds, and the route must not blink
        # off (or a wrong one on) while it does.
        if route is not None and phase != kin and phase in ("arriving", "departing"):
            ok, why = validate_route(
                route, lat=lat, lon=lon, track_deg=track, altitude_ft=alt.feet,
                on_ground=alt.on_ground, kinematic=phase, distance_home_nm=distance,
                home_codes=self.home_codes)
            shown_route = route if ok else None

        mem.direction = direction_of(closing, mem.direction)

        # ---- names ----
        prefix = airline_prefix(callsign)
        airline = self.lookups.brand(callsign, registration, owner)
        if not airline and prefix:
            entry = self.lookups.airline(prefix)
            known_blank = prefix in self.lookups.airlines and entry is None
            if entry is not None and entry.curated:
                airline = entry.name
            elif route is not None and route.airline_name and route.airline_icao == prefix \
                    and not known_blank:
                airline = route.airline_name
            elif entry is not None:
                airline = entry.name
        flight = self.lookups.flight_number(callsign, route.callsign_iata if route else "") \
            or registration

        # ---- trail ----
        if alt.on_ground:
            mem.trail.clear()
        else:
            last = mem.trail[-1] if mem.trail else None
            if last is None or haversine_nm(last[0], last[1], lat, lon) >= TRAIL_MIN_STEP_NM:
                mem.trail.append((lat, lon, pos_mono))
            while mem.trail and now - mem.trail[0][2] > cfg.trail_seconds:
                mem.trail.popleft()

        return Aircraft(
            hex=hex_id, callsign=callsign, flight=flight, airline=airline,
            registration=registration, type_code=type_code, type_name=type_name,
            category=category, military=bool(int(_num(raw.get("dbFlags"), 0, 255) or 0) & 1),
            altitude_ft=alt.feet, on_ground=alt.on_ground, vertical_rate_fpm=rate,
            ground_speed_kt=gs, track_deg=track, track_rate=track_rate,
            lat=float(lat), lon=float(lon), pos_mono=pos_mono,
            pos_source=_pos_source(raw), source=str(raw.get("_source") or ""),
            distance_nm=distance, bearing_deg=bearing,
            phase=phase, direction=mem.direction,
            origin_code=shown_route.origin.code if shown_route else "",
            origin_name=shown_route.origin.city if shown_route else "",
            dest_code=shown_route.destination.code if shown_route else "",
            dest_name=shown_route.destination.city if shown_route else "",
            first_seen_mono=mem.first_seen,
            trail=tuple((p[0], p[1]) for p in mem.trail),
        )
