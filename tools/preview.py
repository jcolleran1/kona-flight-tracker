#!/usr/bin/env python3
"""Render preview PNGs of the wall display without a screen, a Pi, or a network.

    python3 tools/preview.py                     # 0, 1, 5 and 15 aircraft
    python3 tools/preview.py --count 15 --at 420 # one frame, 7 min into the scenario
    python3 tools/preview.py --out previews/     # where the PNGs go (default)

Uses the bundled replay scenario and the real fetch -> enrich -> render path, so
what you see is what the Pi would draw for the same data. Look at these before
calling any UI change done.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from config import load_config  # noqa: E402
from fetch.replay import ReplaySource  # noqa: E402

DEFAULT_COUNTS = (0, 1, 5, 15)


class LimitedReplay(ReplaySource):
    """Replay frozen at one moment, trimmed to the first `count` aircraft."""

    def __init__(self, at: float, count: int | None):
        super().__init__(start_at=at, clock=lambda: 0.0)
        self.count = count

    def fetch(self):
        poll = super().fetch()
        if self.count is not None:
            poll.aircraft = poll.aircraft[:self.count]
        return poll


def render(cfg, at: float, count: int | None, out: Path) -> Path:
    from enrich.lookups import Lookups
    from enrich.trend import TrendTracker
    from fetch.worker import fetch_once
    from render.renderer import Renderer
    from state import SharedState

    state = SharedState()
    source = LimitedReplay(at, count)
    aircraft = fetch_once(source, cfg, Lookups.load(), TrendTracker())
    state.publish_success(aircraft)

    renderer = Renderer(cfg, state)
    renderer.init_display()
    renderer.draw_frame(state.read(), False)
    out.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(renderer.screen, str(out))
    pygame.display.quit()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--count", type=int, action="append",
                    help="number of aircraft (repeatable); default 0, 1, 5, 15")
    ap.add_argument("--at", type=float, default=420.0,
                    help="seconds into the scenario (default 420)")
    ap.add_argument("--out", type=Path, default=ROOT / "previews")
    args = ap.parse_args()

    cfg = dataclasses.replace(load_config(), source="replay", fullscreen=False)
    for n in (args.count or DEFAULT_COUNTS):
        path = render(cfg, args.at, n, args.out / f"preview-{n:02d}.png")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
