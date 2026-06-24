"""Approaching/departing detection.

Compares each aircraft's distance against its previous poll. A small dead
band stops the label flapping when an aircraft is passing abeam the house.
"""
from __future__ import annotations

DEAD_BAND_NM = 0.05  # ignore changes smaller than this between polls


class TrendTracker:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}      # hex -> last distance
        self._trend: dict[str, str] = {}       # hex -> last stable trend

    def update(self, hex_id: str, distance_nm: float) -> str:
        prev = self._last.get(hex_id)
        self._last[hex_id] = distance_nm
        if prev is None:
            return self._trend.get(hex_id, "")
        delta = distance_nm - prev
        if delta < -DEAD_BAND_NM:
            self._trend[hex_id] = "approaching"
        elif delta > DEAD_BAND_NM:
            self._trend[hex_id] = "departing"
        # within dead band: keep previous trend
        return self._trend.get(hex_id, "")

    def prune(self, live_hexes: set[str]) -> None:
        """Drop aircraft that left the radius so the dicts don't grow forever."""
        for d in (self._last, self._trend):
            for k in list(d.keys()):
                if k not in live_hexes:
                    del d[k]
