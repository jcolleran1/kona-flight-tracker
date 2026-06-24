"""Movement classification relative to home (Kona).

Three states, always resolvable so the map only ever needs three colours:
  ground       — on the ground
  approaching  — coming toward Kona
  departing    — leaving Kona

Uses the established approaching/departing trend when we have it (two polls of
distance history), and falls back to pure geometry — does the aircraft's track
point toward home or away — on first sight, so a plane is never left uncoloured.
"""
from __future__ import annotations

from state import Aircraft


def movement_class(ac: Aircraft) -> str:
    if ac.on_ground:
        return "ground"
    if ac.trend == "approaching":
        return "approaching"
    if ac.trend == "departing":
        return "departing"
    # No trend history yet: infer from heading vs. the direction back to home.
    if ac.track_deg is None:
        return "approaching"
    dir_to_home = (ac.bearing_deg + 180.0) % 360.0
    diff = abs((ac.track_deg - dir_to_home + 180.0) % 360.0 - 180.0)
    return "approaching" if diff < 90.0 else "departing"


def select_featured(aircraft: list[Aircraft]) -> Aircraft | None:
    """The flight the display features: the nearest *airborne* aircraft (an
    actual arrival/departure), or the nearest of any kind if all are grounded."""
    if not aircraft:
        return None
    for ac in aircraft:  # list is sorted nearest-first
        if not ac.on_ground:
            return ac
    return aircraft[0]
