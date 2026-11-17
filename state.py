"""Shared state between the fetch thread and the render loop.

The fetch thread is the only writer. The render loop is the only reader.
A Snapshot is built once, published, and never changed afterwards, so the
renderer can hold one for a whole frame without a lock.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class Aircraft:
    hex: str
    callsign: str = ""           # as broadcast (ICAO form, e.g. UAL1759), may be ""
    flight: str = ""             # what the wall shows: UA1759, else callsign, else tail number
    airline: str = ""            # may be ""
    registration: str = ""       # tail number, may be ""
    type_code: str = ""          # e.g. "B738", may be ""
    type_name: str = ""          # may be ""
    category: str = ""           # ADS-B emitter category ("A3" large, "A7" rotorcraft...)
    military: bool = False

    altitude_ft: float | None = None    # corrected, never negative; None = unknown / on ground
    on_ground: bool = False
    vertical_rate_fpm: float | None = None
    ground_speed_kt: float | None = None
    track_deg: float | None = None
    track_rate: float | None = None     # deg/s, positive = turning right
    lat: float = 0.0
    lon: float = 0.0
    pos_mono: float = 0.0        # time.monotonic() when this position was true
    pos_source: str = "adsb"     # "adsb" | "mlat" | "other"
    source: str = ""             # which feed supplied this position

    distance_nm: float = 0.0     # from the home point
    bearing_deg: float = 0.0     # from the home point, true

    phase: str = "local"         # ground | arriving | departing | overflight | local
    direction: str = "toward"    # toward | away  (relative to the home point)

    # route, only ever filled in after validation (enrich/routes.py)
    origin_code: str = ""
    origin_name: str = ""
    dest_code: str = ""
    dest_name: str = ""

    first_seen_mono: float = 0.0
    trail: tuple = ()            # ((lat, lon), ...) oldest first

    @property
    def label(self) -> str:
        """Best short identity for a map label or table row; may be ""."""
        return self.flight or self.registration


@dataclass(frozen=True)
class SourceHealth:
    name: str
    ok: bool = False
    aircraft: int = 0            # records with a position in its last response
    last_ok: float | None = None # monotonic time of its last successful poll; None = never
    error: str = ""

    def age(self, now: float) -> float | None:
        """Seconds since this source last answered. Computed by the reader at
        draw time, so a frozen fetch thread can't leave a dot stuck on green."""
        return None if self.last_ok is None else max(0.0, now - self.last_ok)


@dataclass(frozen=True)
class Snapshot:
    aircraft: tuple = ()             # Aircraft, sorted by distance
    featured_hex: str | None = None
    fetched_at: float = 0.0          # monotonic time of last successful poll
    last_attempt_at: float = 0.0     # monotonic time of last attempt (success or fail)
    consecutive_failures: int = 0
    ok: bool = False                 # last attempt succeeded
    sources: tuple = ()              # SourceHealth per source
    weather: object | None = None    # enrich.metar.Weather or None
    seq: int = 0                     # increases with every publish

    @property
    def featured(self) -> Aircraft | None:
        if self.featured_hex is None:
            return None
        return next((a for a in self.aircraft if a.hex == self.featured_hex), None)


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = Snapshot()

    def publish(self, aircraft, *, featured_hex=None, ok: bool, fetched_at: float | None = None,
                sources=(), weather=None) -> None:
        """Replace the snapshot. `ok` says whether the poll that led to this
        publish succeeded; `fetched_at` is the time of the newest successful
        poll (unchanged on failure)."""
        now = time.monotonic()
        with self._lock:
            prev = self._snap
            self._snap = Snapshot(
                aircraft=tuple(aircraft),
                featured_hex=featured_hex,
                fetched_at=(fetched_at if fetched_at is not None
                            else (now if ok else prev.fetched_at)),
                last_attempt_at=now,
                consecutive_failures=0 if ok else prev.consecutive_failures + 1,
                ok=ok,
                sources=tuple(sources),
                weather=weather,
                seq=prev.seq + 1,
            )

    def read(self) -> Snapshot:
        with self._lock:
            return self._snap
