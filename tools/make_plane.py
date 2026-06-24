#!/usr/bin/env python3
"""Bake a default plane icon (data/plane.png), white, pointing straight up
(north / 0 deg) so the renderer's rotate-by-track logic aligns. Replace with
your own plane.png if you prefer; the renderer also draws a vector fallback
if the file is missing, so this is purely cosmetic.

    python3 tools/make_plane.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_DIR  # noqa: E402

SIZE = 96  # baked large; renderer scales down to config plane_size


def main():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = SIZE / 2
    pts = [
        (c, SIZE * 0.06),
        (c + SIZE * 0.07, SIZE * 0.40),
        (c + SIZE * 0.46, SIZE * 0.60),
        (c + SIZE * 0.07, SIZE * 0.66),
        (c + SIZE * 0.06, SIZE * 0.86),
        (c + SIZE * 0.22, SIZE * 0.94),
        (c, SIZE * 0.86),
        (c - SIZE * 0.22, SIZE * 0.94),
        (c - SIZE * 0.06, SIZE * 0.86),
        (c - SIZE * 0.07, SIZE * 0.66),
        (c - SIZE * 0.46, SIZE * 0.60),
        (c - SIZE * 0.07, SIZE * 0.40),
    ]
    d.polygon(pts, fill=(255, 255, 255, 255))
    out = DATA_DIR / "plane.png"
    img.save(out)
    print(f"wrote {out} ({SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
