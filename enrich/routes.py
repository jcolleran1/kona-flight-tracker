"""Routes (origin and destination) and the checks that keep wrong ones off the
wall.

ADS-B carries no route. The city pair comes from adsbdb.com, a community
database keyed by callsign. It is often right and sometimes badly wrong,
especially for interisland flight numbers that get reused: in November 2026
it answered "Seattle to San Francisco" for a Hawaiian 717 that was between
Honolulu and Kona at the time.

So every route is checked against what the aircraft is actually doing before
it is shown, and thrown away if it doesn't fit. A missing route shows the
direction text instead. Pure functions, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

from enrich.geo import angle_diff, bearing_deg, haversine_nm, valid_position
from enrich.lookups import clean_text


@dataclass(frozen=True)
class Airport:
    iata: str = ""
    icao: str = ""
    city: str = ""
    lat: float | None = None
    lon: float | None = None

    @property
    def code(self) -> str:
        return self.iata or self.icao

    @property
    def has_position(self) -> bool:
        return valid_position(self.lat, self.lon)

    def matches(self, *codes: str) -> bool:
        return any(c and c in (self.iata, self.icao) for c in codes)


@dataclass(frozen=True)
class Route:
    origin: Airport
    destination: Airport
    callsign_iata: str = ""
    airline_name: str = ""
    airline_icao: str = ""
    airline_iata: str = ""


def _airport(d) -> Airport:
    if not isinstance(d, dict):
        return Airport()

    def code(*keys, n):
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and len(v.strip()) == n and v.strip().isalnum():
                return v.strip().upper()
        return ""

    def num(*keys):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return None

    return Airport(
        iata=code("iata_code", "iata", n=3),
        icao=code("icao_code", "icao", n=4),
        city=clean_text(d.get("municipality") or d.get("city") or d.get("name") or "", 28),
        lat=num("latitude", "lat"),
        lon=num("longitude", "lon"),
    )


def route_from_dict(d) -> Route | None:
    """Build a Route from an adsbdb `flightroute` object, or from the compact
    form used in recording metadata. None if either end is unusable."""
    if not isinstance(d, dict):
        return None
    origin, dest = _airport(d.get("origin")), _airport(d.get("destination"))
    if not origin.code or not dest.code:
        return None
    airline = d.get("airline") if isinstance(d.get("airline"), dict) else {}
    return Route(
        origin=origin, destination=dest,
        callsign_iata=clean_text(d.get("callsign_iata") or "", 8).upper(),
        airline_name=clean_text(airline.get("name") or "", 32),
        airline_icao=clean_text(airline.get("icao") or "", 3).upper(),
        airline_iata=clean_text(airline.get("iata") or "", 2).upper(),
    )


# Tolerances for the corridor test. A flight is rarely more than a few dozen
# miles off the direct line between its airports; arrivals being vectored
# around the pattern need some slack.
CORRIDOR_SLACK_NM = 60.0
CORRIDOR_SLACK_RATIO = 0.25
WRONG_WAY_DEG = 100.0
WRONG_WAY_MIN_DEST_NM = 80.0
HOME_GROUND_RADIUS_NM = 3.0


def validate_route(route: Route | None, *, lat: float, lon: float,
                   track_deg: float | None, altitude_ft: float | None,
                   on_ground: bool, kinematic: str, distance_home_nm: float,
                   home_codes: tuple[str, ...]) -> tuple[bool, str]:
    """Does this route fit what the aircraft is doing? -> (ok, reason).

    `kinematic` is the phase from the aircraft's own motion (enrich.motion).
    """
    if route is None:
        return False, "no route"
    o, d = route.origin, route.destination
    if o.code == d.code:
        return False, "origin equals destination"

    origin_home, dest_home = o.matches(*home_codes), d.matches(*home_codes)

    # 1. What we can see at the home airport outranks any database.
    if kinematic == "arriving" and not dest_home:
        return False, "landing here but the route ends elsewhere"
    if kinematic == "departing" and not origin_home:
        return False, "departed here but the route starts elsewhere"
    if on_ground and distance_home_nm <= HOME_GROUND_RADIUS_NM \
            and not (origin_home or dest_home):
        return False, "on the ground here but the route doesn't include this airport"

    # 2. The aircraft has to be somewhere near the line between the airports.
    if o.has_position and d.has_position:
        leg = haversine_nm(o.lat, o.lon, d.lat, d.lon)
        via = haversine_nm(o.lat, o.lon, lat, lon) + haversine_nm(lat, lon, d.lat, d.lon)
        if via - leg > max(CORRIDOR_SLACK_NM, CORRIDOR_SLACK_RATIO * leg):
            return False, "aircraft is nowhere near this route"

    # 3. In cruise, it has to be flying toward the destination, not back to
    #    the origin (the return leg reusing the outbound flight number).
    if (d.has_position and track_deg is not None and not on_ground
            and altitude_ft is not None and altitude_ft >= 10000
            and haversine_nm(lat, lon, d.lat, d.lon) >= WRONG_WAY_MIN_DEST_NM
            and angle_diff(track_deg, bearing_deg(lat, lon, d.lat, d.lon)) > WRONG_WAY_DEG):
        return False, "flying away from the destination"

    return True, "ok"
