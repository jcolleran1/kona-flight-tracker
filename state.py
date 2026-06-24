"""Shared state between the fetch thread and the render loop.

The fetch thread is the only writer. The render loop is the only reader.
Reads return a snapshot so the renderer never holds the lock while drawing.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Aircraft:
    hex: str
    callsign: str            # trimmed, may be ""
    airline: str             # enriched, may be ""
    type_code: str           # e.g. "B738", may be ""
    type_name: str           # enriched, may be ""
    altitude_ft: float | None    # None = unknown; -1 sentinel never used
    on_ground: bool
    ground_speed_kt: float | None
    track_deg: float | None
    lat: float
    lon: float
    distance_nm: float       # from home
    bearing_deg: float       # from home, true
    trend: str               # "approaching" | "departing" | "" (unknown/first sight)
    registration: str = ""   # tail number from the feed (e.g. "N41140"), may be ""
    # route (filled only for the featured flight; from adsbdb, may stay blank)
    origin_code: str = ""
    origin_name: str = ""
    dest_code: str = ""
    dest_name: str = ""


@dataclass
class Snapshot:
    aircraft: list[Aircraft] = field(default_factory=list)  # sorted by distance
    fetched_at: float = 0.0          # monotonic time of last successful fetch
    fetched_wall: float = 0.0        # wall time of last successful fetch
    last_attempt_at: float = 0.0     # monotonic time of last attempt (success or fail)
    consecutive_failures: int = 0
    ok: bool = False                 # last attempt succeeded


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = Snapshot()

    def publish_success(self, aircraft: list[Aircraft]) -> None:
        now = time.monotonic()
        with self._lock:
            self._snap = Snapshot(
                aircraft=aircraft,
                fetched_at=now,
                fetched_wall=time.time(),
                last_attempt_at=now,
                consecutive_failures=0,
                ok=True,
            )

    def publish_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            s = self._snap
            self._snap = Snapshot(
                aircraft=s.aircraft,            # keep last good data
                fetched_at=s.fetched_at,
                fetched_wall=s.fetched_wall,
                last_attempt_at=now,
                consecutive_failures=s.consecutive_failures + 1,
                ok=False,
            )

    def read(self) -> Snapshot:
        with self._lock:
            return self._snap  # Snapshot is replaced wholesale, never mutated
