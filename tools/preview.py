#!/usr/bin/env python3
"""Render preview PNGs of the wall display without a screen, a Pi, or a network.

    python3 tools/preview.py                     # 0, 1, 5 and 15 aircraft
    python3 tools/preview.py --count 15 --at 420 # one frame, 7 min into the scenario
    python3 tools/preview.py --count 15 --night  # quiet-hours brightness
    python3 tools/preview.py --count 15 --no-data --name nodata
    python3 tools/preview.py --count 15 --plain-icons --name plain
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


class SimClock:
    """A clock the preview advances by hand, so a scene 7 minutes into the
    scenario renders in milliseconds and always comes out the same."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class LimitedReplay(ReplaySource):
    """The bundled scenario on a simulated clock, trimmed to `count` aircraft."""

    def __init__(self, clock: SimClock, count: int | None, recording: str | None = None):
        super().__init__(recording=recording, clock=clock)
        self.sim = clock
        self.count = count

    def fetch(self):
        poll = super().fetch()
        poll.received_mono = self.sim()
        if self.count is not None:
            poll.aircraft = poll.aircraft[:self.count]
        return poll


def build_snapshot(cfg, at: float, count: int | None, warmup: float = 240.0):
    """Run the real fetch -> merge -> enrich pipeline over the scenario up to
    time `at` (so phases, trails and the featured pick have their history) and
    return (snapshot, simulated monotonic time)."""
    from fetch.worker import FetchLoop
    from state import SharedState

    clock = SimClock()
    state = SharedState()
    source = LimitedReplay(clock, count)
    loop = FetchLoop(cfg, state, sources=[source], clock=clock)
    t = max(0.0, at - warmup)
    while True:
        clock.t = t
        for st in loop.sources:
            st.next_due = t
        loop.tick()
        if t >= at:
            break
        t = min(at, t + cfg.interval_seconds)
    return state.read(), clock.t


def render(cfg, at: float, count: int | None, out: Path, *, night: bool = False,
           no_data: bool = False) -> Path:
    from render.renderer import Renderer
    from state import SharedState

    snap, now = build_snapshot(cfg, at, count)
    renderer = Renderer(cfg, SharedState())
    renderer.init_display()
    if no_data:
        now += 300.0          # five minutes with nothing from any source
    renderer.draw_frame(snap, no_data, now + 1.0, force=True,
                        brightness=cfg.quiet_brightness if night else 1.0)
    out.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(renderer.screen, str(out))
    pygame.display.quit()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--count", type=int, action="append",
                    help="number of aircraft (repeatable); default 0, 1, 5, 15")
    ap.add_argument("--at", type=float, default=460.0,
                    help="seconds into the scenario (default 460)")
    ap.add_argument("--out", type=Path, default=ROOT / "previews")
    ap.add_argument("--plain-icons", action="store_true",
                    help="one plane icon for everything ([map] icon_shapes = false)")
    ap.add_argument("--night", action="store_true", help="quiet-hours brightness")
    ap.add_argument("--no-data", action="store_true", help="what a dead connection looks like")
    ap.add_argument("--clock-24h", action="store_true", help="24-hour clock")
    ap.add_argument("--name", default="preview", help="file name prefix")
    args = ap.parse_args()

    cfg = dataclasses.replace(load_config(local=False), sources=("replay",), fullscreen=False)
    if args.plain_icons:
        cfg = dataclasses.replace(cfg, icon_shapes=False)
    if args.clock_24h:
        cfg = dataclasses.replace(cfg, clock_24h=True)
    for n in (args.count or DEFAULT_COUNTS):
        path = render(cfg, args.at, n, args.out / f"{args.name}-{n:02d}.png",
                      night=args.night, no_data=args.no_data)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
