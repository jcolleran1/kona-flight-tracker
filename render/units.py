"""Display formatting for distances, altitudes, speeds in either unit system."""
from __future__ import annotations

NM_TO_MI = 1.15078
NM_TO_KM = 1.852
KT_TO_MPH = 1.15078
KT_TO_KMH = 1.852
FT_TO_M = 0.3048


def fmt_distance(nm: float, units: str) -> str:
    if units == "metric":
        return f"{nm * NM_TO_KM:.0f} km"
    return f"{nm * NM_TO_MI:.0f} mi"


def fmt_distance_short(nm: float, units: str) -> str:
    if units == "metric":
        return f"{nm * NM_TO_KM:.0f}km"
    return f"{nm * NM_TO_MI:.0f}mi"


def fmt_altitude(ft: float | None, on_ground: bool, units: str) -> str:
    if on_ground:
        return "GROUND"
    if ft is None:
        return "---"
    if units == "metric":
        return f"{ft * FT_TO_M:,.0f} m"
    return f"{ft:,.0f} ft"


def fmt_speed(kt: float | None, units: str) -> str:
    if kt is None:
        return "---"
    if units == "metric":
        return f"{kt * KT_TO_KMH:.0f} km/h"
    return f"{kt * KT_TO_MPH:.0f} mph"
