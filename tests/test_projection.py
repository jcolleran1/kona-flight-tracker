"""Audit F: map geometry."""
import math

import pytest

from render.projection import Projection, fit_bounds

# the panel: right half of a 1920x1080 screen
PANEL = dict(x0=960, y0=0, width=960, height=1080)


def test_corners_and_centre():
    p = Projection(19.0, 20.0, -156.5, -155.5, **PANEL)
    assert p.project(20.0, -156.5) == (960, 0)            # top-left
    assert p.project(19.0, -155.5) == (1920, 1080)        # bottom-right
    assert p.project(19.5, -156.0) == (1440, 540)
    # north is up, east is right
    assert p.project(19.9, -156.0)[1] < p.project(19.1, -156.0)[1]
    assert p.project(19.5, -155.6)[0] > p.project(19.5, -156.4)[0]


def test_off_panel_positions_are_none():
    p = Projection(19.0, 20.0, -156.5, -155.5, **PANEL)
    assert p.project(25.0, -156.0) is None
    assert p.project(19.5, -150.0) is None
    assert p.project(20.01, -156.0) is not None          # inside the small margin
    assert p.project(20.01, -156.0, margin=0.0) is None


def test_shipped_bounds_are_reported_as_stretched_until_phase_3(cfg):
    p = Projection(cfg.map_lat_min, cfg.map_lat_max, cfg.map_lon_min, cfg.map_lon_max, **PANEL)
    assert p.stretch == pytest.approx(1.0, abs=0.40)


def test_fit_bounds_gives_a_true_shape():
    lat_min, lat_max, lon_min, lon_max = fit_bounds(19.6, -155.9, 96.0, 960, 1080)
    p = Projection(lat_min, lat_max, lon_min, lon_max, **PANEL)
    assert p.stretch == pytest.approx(1.0, abs=0.001)
    assert (lat_max - lat_min) * 60 == pytest.approx(96.0)
    assert (lat_min + lat_max) / 2 == pytest.approx(19.6)
    assert (lon_min + lon_max) / 2 == pytest.approx(-155.9)


def test_range_rings_are_circles_on_fitted_bounds():
    from enrich.deadreckon import advance
    lat_min, lat_max, lon_min, lon_max = fit_bounds(19.74, -156.05, 90.0, 960, 1080)
    p = Projection(lat_min, lat_max, lon_min, lon_max, **PANEL)
    cx, cy = p.to_xy(19.74, -156.05)
    radii = []
    for brg in range(0, 360, 30):
        lat, lon = advance(19.74, -156.05, 3600.0, float(brg), 10.0)     # 10 nm out
        x, y = p.to_xy(lat, lon)
        radii.append(math.hypot(x - cx, y - cy))
    assert max(radii) - min(radii) < 1.0                   # within a pixel of round
    assert radii[0] == pytest.approx(10 * p.px_per_nm_y, abs=0.5)
