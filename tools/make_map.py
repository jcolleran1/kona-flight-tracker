#!/usr/bin/env python3
"""Bake the basemap for the right panel: data/big_island_map.png

Run once, offline, on a laptop (needs Pillow; the Pi never runs this):

    python3 tools/make_map.py                 # bake with the bounds in config.toml
    python3 tools/make_map.py --fit 108       # print aspect-correct bounds covering
                                              # 108 nm north-south, then bake with them
    python3 tools/make_map.py --land other.geojson   # use a more detailed coastline

The image is drawn with the SAME projection the renderer uses, from the [map]
bounds in config.toml, so the coastline sits exactly under the live aircraft.
If you change the bounds, re-run this.

Data
  Coastline: Natural Earth 1:10m land polygons (naturalearthdata.com), public
  domain, extracted to data/geo/hawaii_land.geojson. Any GeoJSON of land
  polygons in lon/lat works via --land (US Census cartographic boundary files
  and OpenStreetMap coastlines are both more detailed; mind their licenses).
  Runways and airport positions: OurAirports (ourairports.com), public domain.
  Summits and towns: well-known published coordinates.
No map tiles are fetched, here or at runtime.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_DIR, load_config  # noqa: E402
from render.projection import Projection, fit_bounds  # noqa: E402

S = 3   # supersample factor

# ---- island palette (matches render/theme.py) ----
OCEAN_DEEP = (9, 26, 36)
OCEAN_SHELF = (20, 58, 68)        # shallow water hugging the coast
LAND_COAST = (44, 78, 58)         # lush lowlands
LAND_HIGH = (58, 66, 50)          # dry uplands and lava toward the summits
COASTLINE = (118, 170, 132)
RUNWAY = (232, 220, 190)
TOWN = (214, 200, 168)
PEAK = (150, 168, 150)
AIRPORT = (150, 186, 178)

# ---- features: (lat, lon, label, label side) ----
TOWNS = [
    (19.6400, -155.9969, "KAILUA-KONA", "E"),
    (19.4969, -155.9217, "CAPTAIN COOK", "E"),
    (19.9369, -155.7906, "WAIKOLOA", "E"),
    (20.0230, -155.6717, "WAIMEA", "W"),
    (20.2411, -155.8328, "HAWI", "E"),
    (19.7074, -155.0885, "HILO", "W"),
    (19.4264, -155.2386, "VOLCANO", "W"),
    (19.0653, -155.5861, "NAALEHU", "N"),
]
# (lat, lon, name, elevation ft, label above or below the marker)
PEAKS = [
    (19.8206, -155.4681, "MAUNA KEA", "13,803", "below"),
    (19.4721, -155.5922, "MAUNA LOA", "13,679", "below"),
    (19.6920, -155.8700, "HUALALAI", "8,271", "above"),
]
# runway ends (lat, lon, lat, lon) and label, from OurAirports
RUNWAYS = [
    ((19.75390, -156.04401, 19.72370, -156.04700), None),          # KOA 17/35 (label drawn live)
    ((19.72140, -155.05800, 19.72140, -155.02901), "ITO"),         # Hilo 08/26
    ((19.71250, -155.06200, 19.72420, -155.05200), None),          # Hilo 03/21
    ((19.99690, -155.67400, 20.00570, -155.66200), "MUE"),         # Waimea-Kohala 04/22
    ((20.26400, -155.86501, 20.26650, -155.85500), None),          # Upolu 07/25
]


def load_rings(path: Path) -> list[list[tuple[float, float]]]:
    """Outer rings (lon, lat) of every polygon in a GeoJSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rings = []
    for feat in data.get("features", []):
        geom = feat.get("geometry") or {}
        polys = geom.get("coordinates", [])
        if geom.get("type") == "Polygon":
            polys = [polys]
        elif geom.get("type") != "MultiPolygon":
            continue
        for poly in polys:
            if poly and len(poly[0]) >= 4:
                rings.append([(float(x), float(y)) for x, y in poly[0]])
    return rings


def smooth(ring, passes: int = 2):
    """Chaikin corner cutting. The 1:10m coastline has a vertex every few km,
    which draws as visible straight facets at this zoom; two passes round them
    off while staying within a few hundred metres of the source line."""
    pts = ring[:-1] if ring[0] == ring[-1] else ring[:]
    for _ in range(passes):
        out = []
        n = len(pts)
        for i in range(n):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        pts = out
    return pts + [pts[0]]


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def colorize(mask: Image.Image, low, high) -> Image.Image:
    """Greyscale mask -> RGB ramp from `low` (0) to `high` (255)."""
    lut = []
    for ch in range(3):
        lut += [int(round(low[ch] + (high[ch] - low[ch]) * i / 255.0)) for i in range(256)]
    return Image.merge("RGB", (mask, mask, mask)).point(lut)


def bake(cfg, land_path: Path, out: Path, width: int, height: int) -> None:
    proj = Projection(cfg.map_lat_min, cfg.map_lat_max, cfg.map_lon_min, cfg.map_lon_max,
                      0, 0, width * S, height * S)
    if abs(proj.stretch - 1.0) > 0.02:
        print(f"WARNING: these bounds stretch the map {100 * (proj.stretch - 1):+.0f}% "
              f"east-west; range rings will be ellipses. Try --fit.")

    def xy(lat, lon):
        return proj.to_xy(lat, lon)

    rings = [smooth(r) for r in load_rings(land_path)]
    polys = [[xy(lat, lon) for lon, lat in ring] for ring in rings]

    # --- land mask ---
    mask = Image.new("L", (width * S, height * S), 0)
    md = ImageDraw.Draw(mask)
    for poly in polys:
        md.polygon(poly, fill=255)

    # --- ocean: deep water with a lighter shelf that fades away from shore ---
    px_per_nm = proj.px_per_nm_y
    shelf = mask.filter(ImageFilter.GaussianBlur(radius=px_per_nm * 3.2))
    wide = mask.filter(ImageFilter.GaussianBlur(radius=px_per_nm * 9.0))
    glow = ImageChops.add(shelf.point(lambda v: int(v * 0.75)), wide.point(lambda v: int(v * 0.45)))
    img = colorize(glow, OCEAN_DEEP, OCEAN_SHELF)
    # gentle top-to-bottom wash so the panel isn't flat
    wash = Image.linear_gradient("L").resize((width * S, height * S))
    img = Image.composite(img, Image.new("RGB", img.size, (6, 18, 26)),
                          wash.point(lambda v: 255 - int(v * 0.22)))

    # --- land: lowlands at the coast shading to uplands inland ---
    inland = mask.filter(ImageFilter.GaussianBlur(radius=px_per_nm * 5.0))
    # inside the island the blurred mask runs ~128 (at the shore) .. 255 (deep inland)
    ramp = inland.point(lambda v: max(0, min(255, int((v - 128) * 2.0))))
    land = colorize(ramp, LAND_COAST, LAND_HIGH)
    img.paste(land, (0, 0), mask)

    d = ImageDraw.Draw(img)
    for poly in polys:
        d.line(poly, fill=COASTLINE, width=max(1, int(1.4 * S)), joint="curve")

    def font(px):
        try:
            return ImageFont.truetype(str(DATA_DIR / "B612Mono-Regular.ttf"), int(px * S))
        except OSError:
            return ImageFont.load_default()

    f_town, f_small = font(19), font(15)

    def on_map(x, y, margin=20):
        return margin * S < x < (width - margin) * S and margin * S < y < (height - margin) * S

    # --- summits ---
    for lat, lon, name, feet, where in PEAKS:
        x, y = xy(lat, lon)
        if not on_map(x, y):
            continue
        r = 6 * S
        d.polygon([(x, y - r), (x - r, y + r * 0.8), (x + r, y + r * 0.8)], outline=PEAK,
                  width=max(1, S))
        faint = lerp(PEAK, LAND_HIGH, 0.35)
        if where == "above":
            d.text((x, y - r - 21 * S), name, fill=PEAK, font=f_small, anchor="md")
            d.text((x, y - r - 4 * S), f"{feet} FT", fill=faint, font=f_small, anchor="md")
        else:
            d.text((x, y + r + 3 * S), name, fill=PEAK, font=f_small, anchor="ma")
            d.text((x, y + r + 20 * S), f"{feet} FT", fill=faint, font=f_small, anchor="ma")

    # --- runways, to scale ---
    for (la1, lo1, la2, lo2), label in RUNWAYS:
        a, b = xy(la1, lo1), xy(la2, lo2)
        if not (on_map(*a, margin=0) or on_map(*b, margin=0)):
            continue
        d.line([a, b], fill=RUNWAY, width=max(2, int(2.6 * S)))
        if label:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            d.text((mx, my + 9 * S), label, fill=AIRPORT, font=f_small, anchor="ma")

    # --- towns ---
    for lat, lon, name, side in TOWNS:
        x, y = xy(lat, lon)
        if not on_map(x, y):
            continue
        r = 3.2 * S
        d.ellipse([x - r, y - r, x + r, y + r], fill=TOWN)
        gap = 9 * S
        pos, anchor = {
            "E": ((x + gap, y), "lm"), "W": ((x - gap, y), "rm"),
            "N": ((x, y - gap), "mb"), "S": ((x, y + gap), "mt"),
        }[side]
        d.text(pos, name, fill=TOWN, font=f_town, anchor=anchor)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.resize((width, height), Image.LANCZOS).save(out, optimize=True)
    print(f"wrote {out} ({width}x{height}, {out.stat().st_size / 1024:.0f} KB), "
          f"{proj.px_per_nm_y / S:.2f} px per nm, stretch {proj.stretch:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--land", type=Path, default=DATA_DIR / "geo" / "hawaii_land.geojson")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--fit", type=float, metavar="NM",
                    help="compute aspect-correct bounds this many nm tall and bake with them")
    ap.add_argument("--center", type=float, nargs=2, metavar=("LAT", "LON"),
                    help="centre for --fit (default: centre of the configured bounds)")
    args = ap.parse_args()

    cfg = load_config(local=False)
    width, height = cfg.width // 2, cfg.height
    if args.fit:
        import dataclasses
        clat = args.center[0] if args.center else (cfg.map_lat_min + cfg.map_lat_max) / 2
        clon = args.center[1] if args.center else (cfg.map_lon_min + cfg.map_lon_max) / 2
        lat_min, lat_max, lon_min, lon_max = fit_bounds(clat, clon, args.fit, width, height)
        print("put these in config.toml [map]:")
        print(f"  lat_max = {lat_max:.4f}\n  lat_min = {lat_min:.4f}\n"
              f"  lon_min = {lon_min:.4f}\n  lon_max = {lon_max:.4f}")
        cfg = dataclasses.replace(cfg, map_lat_min=round(lat_min, 4), map_lat_max=round(lat_max, 4),
                                  map_lon_min=round(lon_min, 4), map_lon_max=round(lon_max, 4))
    bake(cfg, args.land, args.out or DATA_DIR / cfg.map_image, width, height)
    return 0


if __name__ == "__main__":
    sys.exit(main())
