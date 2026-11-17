"""Turn the feed's altitude fields into the one number the wall shows.

`alt_baro` in ADS-B is PRESSURE altitude: height in the standard atmosphere
above the 1013.25 hPa (29.92 inHg) level, not above the sea. On an ordinary
Kona day the altimeter setting is around 1017-1019 hPa, which puts sea level
120-160 ft *below* the standard level, so a jet sitting on the runway at 47 ft
reports about -100 ft. Pilots (and FlightRadar24's "calibrated altitude") fix
that by dialling in the local altimeter setting; we do the same.

Order of preference for the setting, below the transition altitude:
  1. the airport's METAR altimeter      (what tower gives every arriving pilot)
  2. the aircraft's own selected setting (`nav_qnh`)
  3. no setting known: geometric (GPS) altitude if the aircraft sends it
  4. raw pressure altitude as a last resort
At and above the transition altitude everyone flies on 1013.25, so pressure
altitude is already the right thing to show.

The result is never negative. Pure functions, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

STD_HPA = 1013.25
HPA_PER_INHG = 33.8639

# Plausible sea-level pressures. Anything outside is a bad decode, not weather:
# the lowest ever measured is ~870 hPa (inside a typhoon), the highest ~1084.
QNH_MIN, QNH_MAX = 940.0, 1060.0


def is_valid_qnh(qnh_hpa) -> bool:
    return isinstance(qnh_hpa, (int, float)) and not isinstance(qnh_hpa, bool) \
        and QNH_MIN <= qnh_hpa <= QNH_MAX


def qnh_correction_ft(qnh_hpa: float) -> float:
    """Feet to ADD to pressure altitude to get altitude on the local setting.

    From the standard-atmosphere pressure/altitude relation; works out to about
    27 ft per hPa (roughly 1000 ft per inHg) near sea level.
    """
    return 145366.45 * ((qnh_hpa / STD_HPA) ** 0.190284 - 1.0)


def _number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return float(v)


@dataclass(frozen=True)
class Altitude:
    feet: float | None       # None = unknown or on the ground. Never negative.
    on_ground: bool
    basis: str               # "ground" | "metar" | "nav_qnh" | "geom" | "pressure" | "baro" | ""


def resolve_altitude(raw: dict, station_qnh_hpa: float | None = None, *,
                     transition_ft: float = 18000.0,
                     ground_max_altitude_ft: float = 100.0,
                     ground_max_speed_kt: float = 60.0) -> Altitude:
    """Display altitude and ground state for one raw feed record."""
    alt_baro = raw.get("alt_baro")
    if alt_baro == "ground":
        return Altitude(None, True, "ground")

    baro = _number(alt_baro)
    geom = _number(raw.get("alt_geom"))

    if baro is None:
        feet, basis = (geom, "geom") if geom is not None else (None, "")
    elif baro >= transition_ft:
        feet, basis = baro, "pressure"
    elif is_valid_qnh(station_qnh_hpa):
        feet, basis = baro + qnh_correction_ft(station_qnh_hpa), "metar"
    elif is_valid_qnh(raw.get("nav_qnh")):
        feet, basis = baro + qnh_correction_ft(raw["nav_qnh"]), "nav_qnh"
    elif geom is not None:
        feet, basis = geom, "geom"
    else:
        feet, basis = baro, "baro"

    if feet is None:
        return Altitude(None, False, "")

    gs = _number(raw.get("gs"))
    if feet < ground_max_altitude_ft and gs is not None and gs < ground_max_speed_kt:
        return Altitude(None, True, "ground")
    return Altitude(max(0.0, feet), False, basis)


def vertical_rate(raw: dict) -> float | None:
    """Feet per minute, positive up. Barometric rate preferred (it is what the
    autopilot flies); geometric as the fallback."""
    for key in ("baro_rate", "geom_rate"):
        v = _number(raw.get(key))
        if v is not None and abs(v) <= 20000:
            return v
    return None


def vertical_trend(rate_fpm: float | None, threshold_fpm: float = 250.0) -> str:
    """"climb" | "descend" | "level" | "" (unknown)."""
    if rate_fpm is None:
        return ""
    if rate_fpm >= threshold_fpm:
        return "climb"
    if rate_fpm <= -threshold_fpm:
        return "descend"
    return "level"
