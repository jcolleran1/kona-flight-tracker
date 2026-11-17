"""Replay a recorded sequence of feed responses, in real time, on a loop.

Recording format (written by tools/record.py and tools/make_scenario.py):
JSON Lines, optionally gzipped. One object per line:

    {"meta": {...}}                                   optional, first line
    {"t": 1794880800.0, "source": "adsb_fi", "data": {<raw API response>}}

`t` is the wall-clock time the response was received. `data` is the response
exactly as the source returned it, so replay exercises the same parsing path
as live data. The optional meta line carries offline enrichment answers
(routes, aircraft, altimeter) so replay never needs the network.

Playback maps "seconds since the app started" onto the recording's timeline and
wraps at the end. Each poll returns the newest frame at or before the playback
position, plus how old that frame is, so position ages (and therefore dead
reckoning and freshness filtering) behave exactly as they do live.
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path

from config import DATA_DIR
from fetch.sources import FetchError, Poll

log = logging.getLogger("fetch")

DEFAULT_RECORDING = "fixtures/kona_scenario.jsonl.gz"


def resolve_recording(name: str | None) -> Path:
    p = Path(name or DEFAULT_RECORDING).expanduser()
    return p if p.is_absolute() else DATA_DIR / p


def load_recording(path: Path) -> tuple[dict, list[tuple[float, str, dict]]]:
    """Read a recording -> (meta, [(t, source, payload), ...]) sorted by time."""
    opener = gzip.open if path.suffix == ".gz" else open
    meta: dict = {}
    frames: list[tuple[float, str, dict]] = []
    try:
        with opener(path, "rt", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # a recorder killed mid-write leaves a torn last line
                    log.warning("%s line %d is not valid JSON; skipped", path.name, n)
                    continue
                if not isinstance(obj, dict):
                    continue
                if "meta" in obj and isinstance(obj["meta"], dict):
                    meta.update(obj["meta"])
                elif isinstance(obj.get("data"), dict) and "t" in obj:
                    frames.append((float(obj["t"]), str(obj.get("source", "")), obj["data"]))
    except (OSError, EOFError) as e:
        raise FetchError(f"cannot read recording {path}: {e}") from e
    frames.sort(key=lambda fr: fr[0])
    return meta, frames


class ReplaySource:
    """Plays a recording back. Drop-in for a network source."""

    kind = "replay"

    def __init__(self, name: str = "replay", recording: str | None = None,
                 speed: float = 1.0, start_at: float = 0.0, clock=time.monotonic,
                 only_source: str | None = None):
        self.name = name
        self.path = resolve_recording(recording)
        self.meta, frames = load_recording(self.path)
        if only_source:
            frames = [fr for fr in frames if fr[1] == only_source]
        if not frames:
            raise FetchError(f"recording {self.path} has no frames")
        self._frames = frames
        self._t0 = frames[0][0]
        # Loop length: last frame time plus one typical frame gap, so the final
        # frame gets its share of screen time before the wrap.
        gaps = [b[0] - a[0] for a, b in zip(frames, frames[1:]) if b[0] > a[0]]
        self._gap = sorted(gaps)[len(gaps) // 2] if gaps else 5.0
        self.duration = (frames[-1][0] - self._t0) + self._gap
        self.speed = max(0.01, float(speed))
        self._clock = clock
        self._start = clock() - start_at / self.speed
        self.url = f"replay:{self.path.name}"

    def position(self) -> float:
        """Seconds into the recording right now."""
        return ((self._clock() - self._start) * self.speed) % self.duration

    def frame_at(self, pos: float) -> tuple[float, dict]:
        """Newest frame at or before `pos` -> (frame offset in seconds, payload)."""
        target = self._t0 + pos
        lo, hi = 0, len(self._frames) - 1
        while lo < hi:                      # last frame with t <= target
            mid = (lo + hi + 1) // 2
            if self._frames[mid][0] <= target:
                lo = mid
            else:
                hi = mid - 1
        t, _src, payload = self._frames[lo]
        return t - self._t0, payload

    def fetch(self) -> Poll:
        pos = self.position()
        offset, payload = self.frame_at(pos)
        aircraft = payload.get("ac")
        if aircraft is None:
            aircraft = payload.get("aircraft") or []
        age = max(0.0, (pos - offset) / self.speed)
        return Poll(source=self.name, aircraft=list(aircraft),
                    received_mono=time.monotonic(), age_seconds=age)
