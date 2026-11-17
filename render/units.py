"""Display formatting for distances, altitudes and speeds.

Speed, distance and altitude units are chosen independently in config.toml
(the wall runs knots + statute miles + feet: knots because that is what
FlightRadar24 shows, miles because that is what people think in).
"""
from __future__ import annotations

NM_TO_MI = 1.15078
NM_TO_KM = 1.852
KT_TO_MPH = 1.15078
KT_TO_KMH = 1.852
FT_TO_M = 0.3048

# Below this an airborne aircraft is on the runway or seconds from it; the
# exact number is noise (and after clamping would often read "0 ft").
NEAR_GROUND_FT = 100.0


def distance_value(nm: float, unit: str) -> float:
    return nm * {"mi": NM_TO_MI, "km": NM_TO_KM}.get(unit, 1.0)


def fmt_distance(nm: float, unit: str = "mi") -> str:
    return f"{distance_value(nm, unit):.0f} {unit}"


def fmt_speed(kt: float | None, unit: str = "kt") -> str:
    if kt is None:
        return "---"
    if unit == "mph":
        return f"{kt * KT_TO_MPH:.0f} mph"
    if unit == "kmh":
        return f"{kt * KT_TO_KMH:.0f} km/h"
    return f"{kt:.0f} kt"


def fmt_altitude(ft: float | None, on_ground: bool, unit: str = "ft", *,
                 phase: str = "", vertical_rate_fpm: float | None = None) -> str:
    """Altitude text. Never negative, never a bare "0 ft" for a plane that is
    in the middle of taking off or landing."""
    if on_ground:
        return "GROUND"
    if ft is None:
        return "---"
    ft = max(0.0, ft)
    if ft < NEAR_GROUND_FT:
        rate = vertical_rate_fpm or 0.0
        if phase == "departing" or rate > 100:
            return "TAKEOFF"
        if phase == "arriving" or rate < -100:
            return "LANDING"
        return "LOW"
    if unit == "m":
        return f"{round(ft * FT_TO_M / 5.0) * 5:,.0f} m"
    step = 25.0 if ft < 18000 else 100.0
    return f"{round(ft / step) * step:,.0f} ft"
