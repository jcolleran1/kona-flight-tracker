"""Data sources.

Every source returns a Poll: a list of raw aircraft records in the
ADSBExchange-v2 / readsb shape (airplanes.live and adsb.fi both use it), plus
when it was received and how old it already was. Swap or add sources here
without touching enrichment or rendering.

Terms, as read in November 2026. Re-read them before changing anything here.
  airplanes.live  https://airplanes.live/api-guide/
                  1 request/second, non-commercial use, no SLA. No key or
                  feeder needed today ("that might change in the future").
  adsb.fi         https://github.com/adsbfi/opendata
                  Public endpoints: 1 request/second, personal non-commercial
                  use only, adsb.fi must be cited with a link. Failed requests
                  (4xx) count toward the limit.
  adsb.lol        https://github.com/adsblol/api
                  "Rate limits are dynamic based on the environment load. If
                  you get 4xx errors, you are doing something wrong." An API
                  key (earned by feeding) is planned; until then it is open.
                  Polled no faster than every 10 s here to stay well clear.
  local receiver  your own readsb / dump1090 / tar1090. No limits but your own.

All of them are polled at [fetch] interval_seconds (5 s by default), far under
every published limit, and any 401/403/429 puts that source to sleep (see
fetch/worker.py) rather than retrying into a refusal.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from fetch.http import USER_AGENT, FetchError, http_get_json  # noqa: F401  (re-exported)

log = logging.getLogger("fetch")

MAX_RADIUS_NM = 250


@dataclass
class Poll:
    """One response from one source."""
    source: str
    aircraft: list[dict] = field(default_factory=list)
    received_mono: float = 0.0     # time.monotonic() when the response arrived
    age_seconds: float = 0.0       # how old the data already was on arrival


def _payload_age(now_field, received_wall: float) -> float:
    """How stale a response already is, from its own `now` timestamp.

    The aggregators cache responses for a moment, and each record's seen_pos
    is relative to the response's `now`, not to when we received it. Only
    trusted when it is small and positive: a Pi whose clock hasn't synced yet
    would otherwise shift every aircraft by minutes.
    """
    try:
        now_s = float(now_field)
    except (TypeError, ValueError):
        return 0.0
    if now_s > 1e11:            # milliseconds (v2 API); readsb files use seconds
        now_s /= 1000.0
    age = received_wall - now_s
    return age if 0.0 <= age <= 10.0 else 0.0


class V2Source:
    """Shared behaviour for the ADSBExchange-v2 style point/radius endpoints."""

    kind = "network"
    min_interval = 1.0          # never poll faster than this, whatever config says
    poll_interval: float | None = None      # None = use [fetch] interval_seconds
    list_key = "ac"
    name = "v2"
    url = ""

    def __init__(self, lat: float, lon: float, radius_nm: float, timeout: float = 10.0,
                 name: str | None = None):
        self.timeout = timeout
        if name:
            self.name = name
        self.url = self.build_url(lat, lon, min(radius_nm, MAX_RADIUS_NM))

    def build_url(self, lat: float, lon: float, radius_nm: float) -> str:
        raise NotImplementedError

    def fetch_raw(self) -> dict:
        """The decoded response, unmodified. Used by tools/record.py."""
        payload = http_get_json(self.url, self.timeout)
        if not isinstance(payload, dict) or self.list_key not in payload:
            raise FetchError(f"unexpected response shape: {str(payload)[:200]}")
        if not isinstance(payload[self.list_key] or [], list):
            raise FetchError(f"{self.list_key!r} is not a list")
        return payload

    def fetch(self) -> Poll:
        payload = self.fetch_raw()
        received_mono, received_wall = time.monotonic(), time.time()
        return Poll(
            source=self.name,
            aircraft=[r for r in (payload[self.list_key] or []) if isinstance(r, dict)],
            received_mono=received_mono,
            age_seconds=_payload_age(payload.get("now"), received_wall),
        )


class AirplanesLiveSource(V2Source):
    """GET /v2/point/{lat}/{lon}/{radius_nm}  ->  {"ac": [...], "now": ms, ...}"""

    name = "airplanes_live"
    BASE = "https://api.airplanes.live/v2"

    def build_url(self, lat, lon, radius_nm):
        return f"{self.BASE}/point/{lat:.6f}/{lon:.6f}/{radius_nm:g}"


class AdsbFiSource(V2Source):
    """GET /api/v3/lat/{lat}/lon/{lon}/dist/{radius_nm}

    The older /api/v2/lat/... path is deprecated upstream; v3 returns the same
    record shape.
    """

    name = "adsb_fi"
    BASE = "https://opendata.adsb.fi/api/v3"

    def build_url(self, lat, lon, radius_nm):
        return f"{self.BASE}/lat/{lat:.6f}/lon/{lon:.6f}/dist/{radius_nm:g}"


class AdsbLolSource(V2Source):
    """GET /v2/point/{lat}/{lon}/{radius_nm}  (ADSBExchange-compatible)."""

    name = "adsb_lol"
    min_interval = 10.0
    BASE = "https://api.adsb.lol/v2"

    def build_url(self, lat, lon, radius_nm):
        return f"{self.BASE}/point/{lat:.6f}/{lon:.6f}/{radius_nm:g}"


class ReadsbSource(V2Source):
    """A receiver on the local network: readsb, dump1090-fa or tar1090.

        GET http://<host>:8080/data/aircraft.json
        -> {"now": 1794880800.1, "messages": 123, "aircraft": [ {...}, ... ]}

    Same per-aircraft fields as the aggregators (hex, flight, alt_baro, gs,
    track, baro_rate, lat, lon, seen_pos, category...), minus the database
    fields (registration, type, owner), which the merge fills in from the
    other sources or adsbdb. Hears everything in line of sight with no network
    delay, so it wins the merge while its position is fresh.
    """

    kind = "local"
    name = "local"
    min_interval = 1.0
    list_key = "aircraft"

    def __init__(self, url: str, timeout: float = 3.0, interval: float | None = None,
                 name: str | None = None):
        self.url = url
        self.timeout = timeout
        self.poll_interval = interval
        if name:
            self.name = name


NETWORK_SOURCES = {
    "airplanes_live": AirplanesLiveSource,
    "adsb_fi": AdsbFiSource,
    "adsb_lol": AdsbLolSource,
}

SOURCE_NAMES = sorted([*NETWORK_SOURCES, "local", "replay"])


def make_source(name: str, cfg):
    """Build one source by name from the loaded Config."""
    if name == "replay":
        from fetch.replay import ReplaySource
        return ReplaySource(recording=cfg.replay_file, speed=cfg.replay_speed)
    if name == "local":
        return ReadsbSource(cfg.local_url, timeout=min(cfg.timeout_seconds, 3.0),
                            interval=cfg.local_interval_seconds)
    try:
        cls = NETWORK_SOURCES[name]
    except KeyError:
        raise ValueError(f"unknown source {name!r}; options: {SOURCE_NAMES}") from None
    return cls(cfg.lat, cfg.lon, cfg.radius_nm, cfg.timeout_seconds)
