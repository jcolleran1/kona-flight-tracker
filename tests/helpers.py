"""Small builders shared by the tests."""
from __future__ import annotations

import dataclasses

from enrich.lookups import Lookups
from enrich.routes import Airport, Route
from enrich.tracker import Tracker
from enrich.worker import StaticEnricher

QNH = 1017.9       # hPa; the altimeter setting the fixtures were written for

KOA = Airport("KOA", "PHKO", "Kailua-Kona", 19.738783, -156.045603)
HNL = Airport("HNL", "PHNL", "Honolulu", 21.318387, -157.92567)
ITO = Airport("ITO", "PHTO", "Hilo", 19.72135, -155.045428)
SFO = Airport("SFO", "KSFO", "San Francisco", 37.619, -122.375)
SEA = Airport("SEA", "KSEA", "Seattle", 47.449, -122.309)
OAK = Airport("OAK", "KOAK", "Oakland", 37.7213, -122.221)

_LOOKUPS = None


def lookups() -> Lookups:
    global _LOOKUPS
    if _LOOKUPS is None:
        _LOOKUPS = Lookups.load()
    return _LOOKUPS


class FakeEnricher(StaticEnricher):
    """Offline answers, plus a record of what the tracker asked for."""

    def __init__(self, qnh=QNH, routes=None, aircraft=None):
        super().__init__({"qnh_hpa": qnh} if qnh is not None else {})
        self._routes.update(routes or {})
        self._aircraft.update(aircraft or {})
        self.asked_routes: list[str] = []
        self.asked_aircraft: list[str] = []

    def request_route(self, callsign, priority=0.0):
        self.asked_routes.append(callsign)

    def request_aircraft(self, hex_id, priority=0.0):
        self.asked_aircraft.append(hex_id)


def make_tracker(cfg, enricher=None, **overrides) -> Tracker:
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    return Tracker(cfg, lookups(), enricher or FakeEnricher())


def stamped(raw: dict, now: float = 1000.0, source: str = "test") -> dict:
    """A raw record as fetch.merge would hand it to the tracker."""
    rec = dict(raw)
    seen = rec.get("seen_pos", 0.0) if isinstance(rec.get("seen_pos"), (int, float)) else 0.0
    rec["_pos_mono"] = now - seen
    rec["_source"] = source
    return rec


def track_one(cfg, raw: dict, *, enricher=None, now: float = 1000.0, repeats: int = 1,
              step: float = 5.0, **overrides):
    """Run one raw record through a fresh tracker `repeats` times (so phase
    hysteresis can settle) and return the resulting Aircraft, or None."""
    tracker = make_tracker(cfg, enricher, **overrides)
    out = None
    for i in range(repeats):
        t = now + i * step
        aircraft, _ = tracker.update([stamped(raw, t)], t)
        out = aircraft[0] if aircraft else None
    return out


def route(origin: Airport, dest: Airport, callsign_iata="", airline=("", "", "")) -> Route:
    return Route(origin, dest, callsign_iata, airline[0], airline[1], airline[2])
