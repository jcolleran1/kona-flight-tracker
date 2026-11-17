"""Map projection: lat/lon -> pixels inside the map panel. Pure math, no pygame.

Equirectangular over the configured bounds. Over one island that is accurate
to a fraction of a pixel, provided the bounds' aspect ratio matches the panel:
a degree of longitude is only cos(latitude) as long as a degree of latitude,
so the bounds must be chosen with that in mind or the island is stretched and
range rings come out as ellipses. `fit_bounds` computes bounds that are right
by construction; `stretch` reports how far off a given set is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

NM_PER_DEG_LAT = 60.0


@dataclass(frozen=True)
class Projection:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    x0: int          # panel's left edge on screen
    y0: int          # panel's top edge on screen
    width: int
    height: int

    @property
    def mid_lat(self) -> float:
        return (self.lat_min + self.lat_max) / 2.0

    @property
    def px_per_nm_y(self) -> float:
        return self.height / ((self.lat_max - self.lat_min) * NM_PER_DEG_LAT)

    @property
    def px_per_nm_x(self) -> float:
        return self.width / ((self.lon_max - self.lon_min) * NM_PER_DEG_LAT
                             * math.cos(math.radians(self.mid_lat)))

    @property
    def stretch(self) -> float:
        """Horizontal scale / vertical scale. 1.0 = true shape."""
        return self.px_per_nm_x / self.px_per_nm_y

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        """Unclipped screen coordinates (may be far outside the panel)."""
        rx = (lon - self.lon_min) / (self.lon_max - self.lon_min)
        ry = 1.0 - (lat - self.lat_min) / (self.lat_max - self.lat_min)
        return self.x0 + rx * self.width, self.y0 + ry * self.height

    def project(self, lat: float, lon: float, margin: float = 0.02) -> tuple[int, int] | None:
        """Screen pixel for a position, or None when it is off the panel
        (beyond a small margin, so an icon can slide off the edge)."""
        x, y = self.to_xy(lat, lon)
        mx, my = self.width * margin, self.height * margin
        if not (self.x0 - mx <= x <= self.x0 + self.width + mx
                and self.y0 - my <= y <= self.y0 + self.height + my):
            return None
        return int(round(x)), int(round(y))


def fit_bounds(center_lat: float, center_lon: float, height_nm: float,
               width_px: int, height_px: int) -> tuple[float, float, float, float]:
    """Bounds (lat_min, lat_max, lon_min, lon_max) centred on a point, covering
    `height_nm` north-south, with exactly the panel's aspect ratio."""
    half_lat = height_nm / NM_PER_DEG_LAT / 2.0
    width_nm = height_nm * width_px / height_px
    half_lon = width_nm / (NM_PER_DEG_LAT * math.cos(math.radians(center_lat))) / 2.0
    return (center_lat - half_lat, center_lat + half_lat,
            center_lon - half_lon, center_lon + half_lon)
