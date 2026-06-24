"""Data source abstraction.

A source is anything with .fetch() -> list[dict] of raw aircraft records in
ADSBExchange-v2-compatible shape (airplanes.live, adsb.fi, adsb.lol all conform).
Swap sources here without touching enrichment or rendering.

Uses stdlib urllib so the Pi needs zero HTTP dependencies.
"""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("fetch")

USER_AGENT = "flight-tracker-wall-display/1.0 (personal, noncommercial)"


class FetchError(Exception):
    pass


class AirplanesLiveSource:
    """https://airplanes.live/api-guide/  -- 1 req/sec limit, noncommercial.

    Endpoint: /v2/point/{lat}/{lon}/{radius_nm}  (radius capped at 250 nm)
    Response: {"ac": [...], "msg": "No error", ...}
    """

    BASE = "https://api.airplanes.live/v2"

    def __init__(self, lat: float, lon: float, radius_nm: float, timeout: float = 10.0):
        radius_nm = min(radius_nm, 250)
        self.url = f"{self.BASE}/point/{lat:.6f}/{lon:.6f}/{radius_nm:g}"
        self.timeout = timeout

    def fetch(self) -> list[dict]:
        req = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                payload = json.load(resp)
        except FetchError:
            raise
        except Exception as e:  # URLError, timeout, JSON decode, etc.
            raise FetchError(str(e)) from e

        if not isinstance(payload, dict) or "ac" not in payload:
            raise FetchError(f"unexpected response shape: {str(payload)[:200]}")
        ac = payload["ac"] or []
        if not isinstance(ac, list):
            raise FetchError("'ac' is not a list")
        return ac


class AdsbFiSource(AirplanesLiveSource):
    """adsb.fi community feed; same v2 response shape, different host/path.

    https://github.com/adsbfi/opendata -- be polite, similar limits apply.
    """

    BASE = "https://opendata.adsb.fi/api/v2"

    def __init__(self, lat: float, lon: float, radius_nm: float, timeout: float = 10.0):
        radius_nm = min(radius_nm, 250)
        self.url = f"{self.BASE}/lat/{lat:.6f}/lon/{lon:.6f}/dist/{radius_nm:g}"
        self.timeout = timeout


class ReplaySource:
    """Plays back a recorded API response from data/sample_response.json.

    For developing the renderer on a laptop with no network / no Pi.
    Set fetch.source = "replay" in config.toml. Positions get a small
    random walk each poll so trends and row-change pulses are exercised.
    """

    def __init__(self, lat: float, lon: float, radius_nm: float, timeout: float = 0.0):
        import copy
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "sample_response.json"
        with open(path, encoding="utf-8") as f:
            self._base = json.load(f)["ac"]
        # re-center the recorded traffic around the configured home point
        if self._base:
            avg_lat = sum(a["lat"] for a in self._base) / len(self._base)
            avg_lon = sum(a["lon"] for a in self._base) / len(self._base)
            for a in self._base:
                a["lat"] += lat - avg_lat
                a["lon"] += lon - avg_lon
        self._copy = copy.deepcopy

    def fetch(self) -> list[dict]:
        import random
        out = self._copy(self._base)
        for a in out:
            a["lat"] += random.uniform(-0.02, 0.02)
            a["lon"] += random.uniform(-0.02, 0.02)
            if isinstance(a.get("alt_baro"), (int, float)):
                a["alt_baro"] += random.randint(-200, 200)
        return out


SOURCES = {
    "airplanes_live": AirplanesLiveSource,
    "adsb_fi": AdsbFiSource,
    "replay": ReplaySource,
}


def make_source(name: str, lat: float, lon: float, radius_nm: float, timeout: float):
    try:
        cls = SOURCES[name]
    except KeyError:
        raise ValueError(f"unknown source {name!r}; options: {sorted(SOURCES)}")
    return cls(lat, lon, radius_nm, timeout)
