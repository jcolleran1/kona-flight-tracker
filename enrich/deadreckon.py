"""Dead reckoning: where an aircraft is NOW, given where it was a few seconds
ago and how it was moving.

Positions arrive every few seconds and are already a second or two old when
they do. Drawing them as-is makes every plane lag and then jump. Projecting
each one forward along its track at its ground speed is what the big trackers
do, and at these time spans (a few seconds, capped at 30) it is accurate to
well under an icon's width.

Map only. The numbers printed on the left are always the reported values.
Pure functions plus one tiny per-aircraft smoother; no I/O.
"""
from __future__ import annotations

import math

NM_PER_DEG_LAT = 60.0
MIN_SPEED_KT = 3.0          # below this the reported track is noise (taxi, hover)
MAX_TURN_RATE = 6.0         # deg/s; anything faster is a bad decode
SNAP_DISTANCE_NM = 4.0      # a correction bigger than this is a jump, not a glide


def advance(lat: float, lon: float, gs_kt: float | None, track_deg: float | None,
            dt: float, track_rate: float | None = None) -> tuple[float, float]:
    """Position after `dt` seconds on the given ground speed and track.

    With a turn rate (degrees/second, positive = right) the path is an arc,
    which keeps a plane on a curved approach from sliding off its turn.
    """
    if dt <= 0 or gs_kt is None or track_deg is None or gs_kt < MIN_SPEED_KT:
        return lat, lon
    dist_nm = gs_kt * dt / 3600.0
    rate = track_rate if track_rate is not None and abs(track_rate) <= MAX_TURN_RATE else 0.0
    if abs(rate) < 0.05:
        north = dist_nm * math.cos(math.radians(track_deg))
        east = dist_nm * math.sin(math.radians(track_deg))
    else:
        # constant-rate turn: integrate the velocity vector over the arc
        t0 = math.radians(track_deg)
        w = math.radians(rate)                  # rad/s
        t1 = t0 + w * dt
        speed = gs_kt / 3600.0                  # nm/s
        north = speed * (math.sin(t1) - math.sin(t0)) / w
        east = speed * (math.cos(t0) - math.cos(t1)) / w
    new_lat = lat + north / NM_PER_DEG_LAT
    coslat = max(0.01, math.cos(math.radians(lat)))
    new_lon = lon + east / (NM_PER_DEG_LAT * coslat)
    return new_lat, new_lon


def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


class Smoother:
    """Per-aircraft display position: dead-reckoned, with new fixes blended in.

    When a new fix arrives it rarely lands exactly where the projection said
    the plane would be. Rather than snapping, the difference is kept as an
    offset that decays to zero over `tween_seconds`.
    """

    def __init__(self, tween_seconds: float = 1.0, max_extrapolate: float = 30.0,
                 enabled: bool = True):
        self.tween = max(0.0, tween_seconds)
        self.max_dt = max_extrapolate
        self.enabled = enabled
        # hex -> [fix_time, offset_lat, offset_lon, offset_start, last_lat, last_lon]
        self._state: dict[str, list[float]] = {}

    def _project(self, ac, now: float) -> tuple[float, float]:
        if not self.enabled or ac.on_ground and (ac.ground_speed_kt or 0) < MIN_SPEED_KT:
            return ac.lat, ac.lon
        dt = min(max(0.0, now - ac.pos_mono), self.max_dt)
        return advance(ac.lat, ac.lon, ac.ground_speed_kt, ac.track_deg, dt, ac.track_rate)

    def position(self, ac, now: float) -> tuple[float, float]:
        """Where to draw `ac` at monotonic time `now`."""
        lat, lon = self._project(ac, now)
        st = self._state.get(ac.hex)
        if st is None:
            self._state[ac.hex] = [ac.pos_mono, 0.0, 0.0, now, lat, lon]
            return lat, lon
        if st[0] != ac.pos_mono:
            # new fix: carry the gap between what was on screen and the new
            # projection, then let it shrink
            off_lat, off_lon = st[4] - lat, st[5] - lon
            gap_nm = math.hypot(off_lat * 60.0,
                                off_lon * 60.0 * math.cos(math.radians(lat)))
            if self.tween <= 0 or gap_nm > SNAP_DISTANCE_NM:
                off_lat = off_lon = 0.0
            st[0], st[1], st[2], st[3] = ac.pos_mono, off_lat, off_lon, now
        if st[1] or st[2]:
            k = 1.0 - smoothstep((now - st[3]) / self.tween) if self.tween > 0 else 0.0
            if k <= 0.0:
                st[1] = st[2] = 0.0
            else:
                lat += st[1] * k
                lon += st[2] * k
        st[4], st[5] = lat, lon
        return lat, lon

    def prune(self, live_hexes) -> None:
        for k in [k for k in self._state if k not in live_hexes]:
            del self._state[k]
