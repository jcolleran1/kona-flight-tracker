"""Movement classification relative to the home airport (Kona), and the choice
of which flight to feature.

Phases:
  ground     on the ground
  arriving   landing at Kona
  departing  just left Kona
  overflight high traffic passing by
  local      low traffic that isn't using Kona (tours, Hilo/Waimea traffic)

Direction ("toward" / "away") is tracked separately and is what picks between
the teal and coral map colours for overflight and local traffic, so the map
still only ever uses three colours: sand = ground, teal = toward Kona,
coral = leaving Kona.

"Is the distance shrinking?" is not enough to call something an arrival: a
jet at 36,000 ft crossing the island gets closer to Kona too. An arrival has
to be descending, on a course that actually passes the airport, and low
enough for its distance that it could land. Pure functions, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from enrich.geo import angle_diff

PHASES = ("ground", "arriving", "departing", "overflight", "local")


@dataclass(frozen=True)
class ClassifyParams:
    rate_fpm: float = 250.0            # steeper than this = climbing / descending
    overflight_min_alt_ft: float = 10000.0
    closing_min_kt: float = 20.0       # slower than this toward/away = neither
    glide_ft_per_nm: float = 600.0     # steepest believable descent to the field
    arrival_alt_margin_ft: float = 3500.0
    departure_alt_margin_ft: float = 4000.0
    arrival_miss_nm: float = 8.0       # course must pass this close to the field...
    arrival_miss_ratio: float = 0.30   # ...or within this fraction of the distance
    pattern_radius_nm: float = 6.0     # low and this close = in the traffic pattern
    pattern_alt_ft: float = 3000.0
    departure_radius_nm: float = 45.0
    departure_radial_deg: float = 50.0


def closing_speed_kt(gs_kt: float | None, track_deg: float | None,
                     bearing_from_home: float) -> float | None:
    """Speed toward home (positive) or away (negative), or None if unknown."""
    if gs_kt is None or track_deg is None:
        return None
    to_home = (bearing_from_home + 180.0) % 360.0
    return gs_kt * math.cos(math.radians(angle_diff(track_deg, to_home)))


def miss_distance_nm(track_deg: float, bearing_from_home: float, distance_nm: float) -> float:
    """How close the current straight-line course passes to home."""
    to_home = (bearing_from_home + 180.0) % 360.0
    off = angle_diff(track_deg, to_home)
    if off >= 90.0:
        return distance_nm          # already heading away: closest point is now
    return distance_nm * math.sin(math.radians(off))


def direction_of(closing_kt: float | None, previous: str = "",
                 dead_band_kt: float = 15.0) -> str:
    """"toward" or "away", holding the previous answer inside a dead band so a
    plane passing abeam doesn't flicker between colours."""
    if closing_kt is None:
        return previous or "toward"
    if closing_kt > dead_band_kt:
        return "toward"
    if closing_kt < -dead_band_kt:
        return "away"
    return previous or ("toward" if closing_kt >= 0 else "away")


def kinematic_phase(*, on_ground: bool, altitude_ft: float | None,
                    vertical_rate_fpm: float | None, ground_speed_kt: float | None,
                    track_deg: float | None, distance_nm: float, bearing_from_home: float,
                    field_elevation_ft: float = 0.0,
                    p: ClassifyParams = ClassifyParams()) -> str:
    """What the aircraft's own motion says, ignoring any route database."""
    if on_ground:
        return "ground"

    closing = closing_speed_kt(ground_speed_kt, track_deg, bearing_from_home)
    height = None if altitude_ft is None else altitude_ft - field_elevation_ft

    if closing is not None and height is not None:
        reachable = height <= p.glide_ft_per_nm * distance_nm + p.arrival_alt_margin_ft
        descending = vertical_rate_fpm is not None and vertical_rate_fpm <= -p.rate_fpm
        in_pattern = (distance_nm <= p.pattern_radius_nm and height <= p.pattern_alt_ft
                      and (vertical_rate_fpm is None or vertical_rate_fpm < p.rate_fpm))
        if closing > p.closing_min_kt and reachable and (descending or in_pattern):
            miss = miss_distance_nm(track_deg, bearing_from_home, distance_nm)
            if miss <= max(p.arrival_miss_nm, p.arrival_miss_ratio * distance_nm):
                return "arriving"

        climbing = vertical_rate_fpm is not None and vertical_rate_fpm >= p.rate_fpm
        if (climbing and closing < -p.closing_min_kt
                and distance_nm <= p.departure_radius_nm
                and height <= p.glide_ft_per_nm * distance_nm + p.departure_alt_margin_ft):
            radial = angle_diff(track_deg, bearing_from_home)
            if distance_nm <= p.pattern_radius_nm or radial <= p.departure_radial_deg:
                return "departing"

    if altitude_ft is not None and altitude_ft >= p.overflight_min_alt_ft:
        return "overflight"
    return "local"


def combine_with_route(kinematic: str, *, origin_is_home: bool, dest_is_home: bool) -> str:
    """Fold a (validated) route into the kinematic answer. The aircraft's own
    motion wins whenever the two disagree."""
    if kinematic in ("ground", "arriving", "departing"):
        return kinematic
    if dest_is_home and not origin_is_home:
        return "arriving"
    if origin_is_home and not dest_is_home:
        return "departing"
    return kinematic


def neutral_phase(altitude_ft: float | None, p: ClassifyParams = ClassifyParams()) -> str:
    """The phase for traffic that isn't committed to the home airport."""
    if altitude_ft is not None and altitude_ft >= p.overflight_min_alt_ft:
        return "overflight"
    return "local"


def commitment_holds(phase: str, *, altitude_ft: float | None,
                     vertical_rate_fpm: float | None, closing_kt: float | None,
                     distance_nm: float, field_elevation_ft: float = 0.0,
                     p: ClassifyParams = ClassifyParams()) -> bool:
    """Is an aircraft already called an arrival (or departure) still behaving
    like one? Arrivals fly doglegs and level off; departures level off and
    turn. Neither stops being what it is until the motion really contradicts it.
    """
    if phase == "arriving":
        if closing_kt is not None and closing_kt < 0:
            return False
        if vertical_rate_fpm is not None and vertical_rate_fpm >= p.rate_fpm:
            return False
        if altitude_ft is not None:
            height = altitude_ft - field_elevation_ft
            return height <= p.glide_ft_per_nm * distance_nm + p.arrival_alt_margin_ft
        return True
    if phase == "departing":
        if closing_kt is not None and closing_kt > 0:
            return False
        return vertical_rate_fpm is None or vertical_rate_fpm > -p.rate_fpm
    return False


class PhaseTracker:
    """Hysteresis for one aircraft's phase.

    * "arriving" / "departing" must be seen continuously for `confirm_seconds`
      before they are believed, so one noisy sample can't put a passing plane
      in the featured panel.
    * Once believed they stick for as long as the motion still fits
      (see commitment_holds).
    * Touching down is immediate. An aircraft seen on the ground at the home
      field that then gets airborne is a departure, no further evidence
      needed, and stays one through the takeoff roll and the first turns.
    """

    __slots__ = ("phase", "confirm_seconds", "liftoff_hold_seconds", "_candidate",
                 "_since", "_hold_until", "_was_ground_at_home")

    def __init__(self, confirm_seconds: float = 12.0, liftoff_hold_seconds: float = 180.0):
        self.phase = ""
        self.confirm_seconds = confirm_seconds
        self.liftoff_hold_seconds = liftoff_hold_seconds
        self._candidate = ""
        self._since = 0.0
        self._hold_until = 0.0
        self._was_ground_at_home = False

    def update(self, candidate: str, now: float, *, neutral: str = "local",
               near_home: bool = False, holds: bool = False) -> str:
        if candidate == "ground":
            self._was_ground_at_home = near_home
            self.phase, self._candidate, self._hold_until = "ground", "", 0.0
            return self.phase

        if self.phase == "ground":                     # just got airborne
            self._candidate = ""
            if self._was_ground_at_home:
                self.phase = "departing"
                self._hold_until = now + self.liftoff_hold_seconds
                return self.phase
            self.phase = neutral
        elif not self.phase:                           # first sight
            self.phase = neutral

        if now < self._hold_until:
            return self.phase
        if candidate == self.phase:
            self._candidate = ""
            return self.phase
        if self.phase in ("arriving", "departing"):
            if candidate in ("local", "overflight") and holds:
                self._candidate = ""
                return self.phase
        elif candidate in ("local", "overflight"):
            # moving between the two neutral classes needs no ceremony
            self.phase, self._candidate = candidate, ""
            return self.phase

        if candidate != self._candidate:
            self._candidate, self._since = candidate, now
        if now - self._since >= self.confirm_seconds:
            self.phase, self._candidate = candidate, ""
        return self.phase


def movement_color_key(phase: str, direction: str) -> str:
    """"ground" | "toward" | "away": the three map colours."""
    if phase == "ground":
        return "ground"
    if phase == "arriving":
        return "toward"
    if phase == "departing":
        return "away"
    return "away" if direction == "away" else "toward"


def _feature_tier(ac) -> int:
    if ac.phase in ("arriving", "departing"):
        return 0
    if not ac.on_ground:
        return 1
    return 2


class FeaturedSelector:
    """Picks the flight the display features, and keeps it steady.

    Best candidate = nearest actual Kona arrival or departure; with none of
    those, the nearest airborne aircraft; with nothing airborne, the nearest
    on the ground. The current pick is kept until it disappears, something in
    a better tier turns up, or (after a minimum dwell) another flight in the
    same tier is clearly closer. Without this, two planes at similar distance
    swap the big panel back and forth on every poll.
    """

    def __init__(self, dwell_seconds: float = 20.0, closer_ratio: float = 0.7,
                 closer_nm: float = 2.0):
        self.dwell = dwell_seconds
        self.closer_ratio = closer_ratio
        self.closer_nm = closer_nm
        self.hex: str | None = None
        self._since = 0.0

    def select(self, aircraft, now: float) -> str | None:
        if not aircraft:
            self.hex = None
            return None
        best = min(aircraft, key=lambda a: (_feature_tier(a), a.distance_nm))
        current = next((a for a in aircraft if a.hex == self.hex), None)
        if current is not None and current.hex != best.hex:
            same_tier = _feature_tier(best) == _feature_tier(current)
            clearly_closer = (best.distance_nm <= current.distance_nm * self.closer_ratio
                              and current.distance_nm - best.distance_nm >= self.closer_nm)
            if same_tier and not (clearly_closer and now - self._since >= self.dwell):
                return self.hex
        if best.hex != self.hex:
            self.hex, self._since = best.hex, now
        return self.hex
