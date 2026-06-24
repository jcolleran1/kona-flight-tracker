#!/usr/bin/env python3
"""Bake a stylized default map for the right panel: data/big_island_map.png.

This is a placeholder you can replace with your own calibrated map. It is
drawn with the SAME lat/lon -> pixel projection the renderer uses, reading
the bounds from config.toml, so the coastline is guaranteed to sit correctly
under whatever bounds you set. Re-run after changing [map] bounds:

    python3 tools/make_map.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import load_config, DATA_DIR  # noqa: E402

W, H = 960, 1080
S = 2  # supersample

cfg = load_config()
LAT_MIN, LAT_MAX = cfg.map_lat_min, cfg.map_lat_max
LON_MIN, LON_MAX = cfg.map_lon_min, cfg.map_lon_max


def proj(lat, lon):
    rx = (lon - LON_MIN) / (LON_MAX - LON_MIN)
    ry = 1.0 - (lat - LAT_MIN) / (LAT_MAX - LAT_MIN)
    return rx * W * S, ry * H * S


# Big Island, Kona (west) side. The user's bounds crop the east coast off the
# right edge, so we route the eastern polygon edge along lon_max to fill land.
COAST = [
    (20.24, -155.88),   # Upolu Point (north tip)
    (20.03, -155.83),   # Kawaihae
    (19.85, -155.92),
    (19.73, -156.06),   # Keahole / KOA coast (west bulge)
    (19.64, -156.00),   # Kailua-Kona
    (19.50, -155.95),
    (19.42, -155.91),   # Honaunau
    (19.28, -155.88),
    (19.10, -155.83),   # Manuka
    (18.95, -155.74),
    (18.91, -155.68),   # Ka Lae (South Point)
    (18.93, -155.55),
    (18.95, -155.35),   # bottom-right, on the east edge (off-map east)
    (20.05, -155.35),   # top-right, on the east edge
    (20.16, -155.55),   # north coast
    (20.24, -155.88),   # close
]

OCEAN_TOP = (16, 46, 56)     # tropical teal up north
OCEAN_BOT = (10, 30, 42)     # deeper ocean toward the bottom
LAND = (34, 60, 44)          # lush island green
LAND_EDGE = (96, 150, 110)   # sunlit coastline
GRID = (22, 48, 56)


def main():
    img = Image.new("RGB", (W * S, H * S), OCEAN_TOP)
    d = ImageDraw.Draw(img)

    # vertical ocean gradient
    for y in range(H * S):
        f = y / (H * S)
        c = tuple(int(OCEAN_TOP[i] + (OCEAN_BOT[i] - OCEAN_TOP[i]) * f) for i in range(3))
        d.line([(0, y), (W * S, y)], fill=c)

    # faint lat/lon grid (whole-degree and half-degree lines)
    lon = LON_MIN
    while lon <= LON_MAX + 1e-9:
        x = proj(LAT_MIN, lon)[0]
        d.line([(x, 0), (x, H * S)], fill=GRID, width=1)
        lon += 0.25
    lat = LAT_MIN
    while lat <= LAT_MAX + 1e-9:
        y = proj(lat, LON_MIN)[1]
        d.line([(0, y), (W * S, y)], fill=GRID, width=1)
        lat += 0.25

    # landmass
    poly = [proj(la, lo) for la, lo in COAST]
    d.polygon(poly, fill=LAND, outline=LAND_EDGE)
    d.line(poly + [poly[0]], fill=LAND_EDGE, width=2 * S)

    # place markers
    try:
        font = ImageFont.truetype(str(DATA_DIR / "B612Mono-Regular.ttf"), 22 * S)
    except Exception:
        font = ImageFont.load_default()

    def dot(lat, lon, label, color=(150, 170, 180), r=4):
        x, y = proj(lat, lon)
        d.ellipse([x - r * S, y - r * S, x + r * S, y + r * S], fill=color)
        d.text((x + 9 * S, y - 13 * S), label, fill=color, font=font)

    dot(19.6406, -155.9956, "KAILUA-KONA", color=(232, 214, 178))
    dot(19.4969, -155.9219, "CAPTAIN COOK", color=(232, 214, 178))
    dot(20.0247, -155.8294, "KAWAIHAE", color=(232, 214, 178))

    # KOA airport: small runway tick + ring
    ax, ay = proj(19.7388, -156.0456)
    d.line([(ax - 16 * S, ay + 6 * S), (ax + 16 * S, ay - 6 * S)],
           fill=(120, 200, 230), width=3 * S)
    d.ellipse([ax - 7 * S, ay - 7 * S, ax + 7 * S, ay + 7 * S],
              outline=(120, 200, 230), width=2 * S)
    d.text((ax + 12 * S, ay + 8 * S), "KOA", fill=(120, 200, 230), font=font)

    out = DATA_DIR / "big_island_map.png"
    img.resize((W, H), Image.LANCZOS).save(out)
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
