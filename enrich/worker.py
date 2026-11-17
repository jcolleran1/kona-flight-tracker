"""Background enrichment: adsbdb route / airframe lookups and the METAR.

The fetch loop never waits on any of this. It asks for what it wants
(`request_route`, `request_aircraft`), reads whatever is already cached
(`route`, `aircraft`), and carries on. A separate thread works through the
requests at a polite pace, so a slow or dead adsbdb can never delay a position
update.

    GET https://api.adsbdb.com/v0/callsign/{CALLSIGN}
        -> {"response": {"flightroute": {...}}}   or HTTP 404 "unknown callsign"
    GET https://api.adsbdb.com/v0/aircraft/{MODE_S_HEX}
        -> {"response": {"aircraft": {...}}}      or HTTP 404 "unknown aircraft"

Caching: bounded LRU with a TTL. A real answer, including a real "unknown",
is cached. A network error is NOT cached: that key is retried later, with the
whole client backing off exponentially while errors continue.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from dataclasses import dataclass

from enrich.cache import LruTtlCache
from enrich.lookups import clean_registration, clean_text
from enrich.metar import METAR_URL, Weather, parse_metar
from enrich.routes import Route, route_from_dict
from fetch.http import FetchError, http_get_json

log = logging.getLogger("enrich")

ADSBDB = "https://api.adsbdb.com/v0"

ROUTE_TTL = 12 * 3600.0
AIRCRAFT_TTL = 7 * 86400.0
UNKNOWN_TTL = 30 * 60.0
KEY_RETRY_SECONDS = 60.0
BACKOFF_BASE = 10.0
BACKOFF_MAX = 600.0
MAX_PENDING = 64
WEATHER_MAX_AGE = 3 * 3600.0       # after this long without a fresh METAR, stop trusting it
METAR_RETRY_SECONDS = 300.0

_MISSING = object()


@dataclass(frozen=True)
class AircraftInfo:
    registration: str = ""
    type_code: str = ""
    type_name: str = ""
    owner: str = ""


def aircraft_from_dict(d) -> AircraftInfo | None:
    """Build AircraftInfo from an adsbdb `aircraft` object or recording metadata."""
    if not isinstance(d, dict):
        return None
    maker = clean_text(d.get("manufacturer") or "", 20)
    model = clean_text(d.get("type") or "", 20)
    name = clean_text(d.get("type_name") or " ".join(x for x in (maker, model) if x), 34)
    info = AircraftInfo(
        registration=clean_registration(d.get("registration") or ""),
        type_code=clean_text(d.get("icao_type") or d.get("type_code") or "", 4).upper(),
        type_name=name,
        owner=clean_text(d.get("registered_owner") or d.get("owner") or "", 40),
    )
    return info if (info.registration or info.type_code or info.owner) else None


class NullEnricher:
    """No lookups at all (enrichment disabled). Also the base interface."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def request_route(self, callsign: str, priority: float = 0.0) -> None: ...
    def request_aircraft(self, hex_id: str, priority: float = 0.0) -> None: ...

    def route(self, callsign: str) -> Route | None:
        return None

    def aircraft(self, hex_id: str) -> AircraftInfo | None:
        return None

    def weather(self) -> Weather | None:
        return None

    def qnh(self) -> float | None:
        w = self.weather()
        return w.qnh_hpa if w else None

    def idle(self) -> bool:
        return True


class StaticEnricher(NullEnricher):
    """Answers from a recording's metadata. Replay mode never touches the network."""

    def __init__(self, meta: dict | None = None):
        meta = meta or {}
        self._routes = {str(k).upper(): r for k, v in (meta.get("routes") or {}).items()
                        if (r := route_from_dict(v))}
        self._aircraft = {str(k).lower(): a for k, v in (meta.get("aircraft") or {}).items()
                          if (a := aircraft_from_dict(v))}
        qnh = meta.get("qnh_hpa")
        self._weather = Weather(station="replay", qnh_hpa=float(qnh)) \
            if isinstance(qnh, (int, float)) else None

    def route(self, callsign):
        return self._routes.get((callsign or "").upper())

    def aircraft(self, hex_id):
        return self._aircraft.get((hex_id or "").lower())

    def weather(self):
        return self._weather


class NetworkEnricher(NullEnricher):
    def __init__(self, *, min_interval: float = 2.0, timeout: float = 5.0,
                 metar_station: str = "", metar_interval: float = 1800.0,
                 fetcher=http_get_json, clock=time.monotonic):
        self.min_interval = max(1.0, min_interval)
        self.timeout = timeout
        self.metar_station = metar_station
        self.metar_interval = metar_interval
        self._fetch = fetcher
        self._clock = clock

        self._routes = LruTtlCache(512, clock)
        self._aircraft = LruTtlCache(512, clock)
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], float] = {}     # (kind, key) -> priority
        self._retry_at: dict[tuple[str, str], float] = {}
        self._failures = 0
        self._next_request_at = 0.0
        self._weather: Weather | None = None
        self._weather_at = 0.0
        self._metar_due = 0.0
        self._metar_failures = 0

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- read side (fetch thread) ----

    def route(self, callsign):
        return self._routes.get((callsign or "").upper())

    def aircraft(self, hex_id):
        return self._aircraft.get((hex_id or "").lower())

    def weather(self):
        w = self._weather
        if w is None or self._clock() - self._weather_at > WEATHER_MAX_AGE:
            return None
        return w

    def idle(self) -> bool:
        """Nothing left to fetch right now (used by --once to know when to print)."""
        if self.metar_station and self._metar_due == 0.0:
            return False                 # the first METAR hasn't been tried yet
        with self._lock:
            return not self._pending

    def request_route(self, callsign, priority=0.0):
        self._request("route", (callsign or "").upper(), self._routes, priority)

    def request_aircraft(self, hex_id, priority=0.0):
        self._request("aircraft", (hex_id or "").lower(), self._aircraft, priority)

    def _request(self, kind, key, cache, priority) -> None:
        if not key or key in cache:
            return
        item = (kind, key)
        with self._lock:
            if self._retry_at.get(item, 0.0) > self._clock():
                return
            if item not in self._pending and len(self._pending) >= MAX_PENDING:
                worst = min(self._pending, key=self._pending.get)
                if self._pending[worst] >= priority:
                    return
                del self._pending[worst]
            self._pending[item] = max(priority, self._pending.get(item, priority))
        self._wake.set()

    # ---- worker thread ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="enrich", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        log.info("enrichment thread up (adsbdb every >=%.0fs, METAR %s every %.0f min)",
                 self.min_interval, self.metar_station or "off", self.metar_interval / 60)
        while not self._stop.is_set():
            try:
                wait = self.step()
            except Exception:
                log.exception("unexpected error in enrichment loop")
                wait = 30.0
            self._wake.clear()
            self._wake.wait(wait)

    def step(self) -> float:
        """Do at most one network request. Returns seconds until there may be
        more to do. Split out from the thread loop so tests can drive it."""
        now = self._clock()
        if self.metar_station and now >= self._metar_due:
            self._fetch_metar(now)
            return 0.5

        with self._lock:
            ready = {k: p for k, p in self._pending.items()
                     if self._retry_at.get(k, 0.0) <= now}
            item = max(ready, key=ready.get) if ready else None
        next_metar = max(1.0, self._metar_due - now) if self.metar_station else 3600.0
        if item is None:
            return min(next_metar, 30.0)
        if now < self._next_request_at:
            return min(next_metar, self._next_request_at - now)

        self._lookup(item, now)
        return min(next_metar, max(0.1, self._next_request_at - self._clock()))

    def _lookup(self, item, now) -> None:
        kind, key = item
        path = "callsign" if kind == "route" else "aircraft"
        url = f"{ADSBDB}/{path}/{urllib.parse.quote(key.upper())}"
        cache = self._routes if kind == "route" else self._aircraft
        result = _MISSING
        try:
            data = self._fetch(url, self.timeout)
            body = data.get("response") if isinstance(data, dict) else None
            if kind == "route":
                result = route_from_dict(body.get("flightroute")) if isinstance(body, dict) else None
            else:
                result = aircraft_from_dict(body.get("aircraft")) if isinstance(body, dict) else None
        except FetchError as e:
            if e.status == 404:
                result = None            # a real answer: adsbdb doesn't know it
            else:
                self._failures += 1
                delay = e.retry_after or min(BACKOFF_BASE * 2 ** (self._failures - 1), BACKOFF_MAX)
                if e.status == 429:
                    delay = max(delay, 60.0)
                self._next_request_at = now + delay
                with self._lock:
                    self._retry_at[item] = now + max(KEY_RETRY_SECONDS, delay)
                    self._pending.pop(item, None)
                    if len(self._retry_at) > 256:
                        self._retry_at = {k: t for k, t in self._retry_at.items() if t > now}
                log.warning("adsbdb %s lookup failed (%s); backing off %.0fs", kind, e, delay)
                return

        self._failures = 0
        self._next_request_at = now + self.min_interval
        cache.put(key, result, UNKNOWN_TTL if result is None
                  else (ROUTE_TTL if kind == "route" else AIRCRAFT_TTL))
        with self._lock:
            self._pending.pop(item, None)
            self._retry_at.pop(item, None)
        log.debug("adsbdb %s %s -> %s", kind, key, "unknown" if result is None else "ok")

    def _fetch_metar(self, now) -> None:
        try:
            payload = self._fetch(METAR_URL.format(station=self.metar_station), self.timeout)
            wx = parse_metar(payload, self.metar_station)
        except FetchError as e:
            wx = None
            log.warning("METAR fetch failed: %s", e)
        if wx is not None and wx.qnh_hpa is not None:
            if self._weather is None or wx.qnh_hpa != self._weather.qnh_hpa:
                log.info("METAR %s: altimeter %.1f hPa (%.2f inHg)", wx.station,
                         wx.qnh_hpa, wx.qnh_hpa / 33.8639)
            self._weather, self._weather_at = wx, now
            self._metar_failures = 0
            self._metar_due = now + self.metar_interval
        else:
            self._metar_failures += 1
            self._metar_due = now + min(METAR_RETRY_SECONDS * 2 ** (self._metar_failures - 1),
                                        self.metar_interval)


def make_enricher(cfg, sources) -> NullEnricher:
    """Replay sources get their recording's offline answers; live sources get
    the network client (unless enrichment is switched off)."""
    replay = [s for s in sources if getattr(s, "kind", "") == "replay"]
    if replay:
        meta: dict = {}
        for s in replay:
            meta.update(getattr(s, "meta", {}) or {})
        return StaticEnricher(meta)
    if not cfg.enrich_enabled:
        return NullEnricher()
    return NetworkEnricher(
        min_interval=cfg.enrich_min_interval_seconds,
        timeout=min(cfg.timeout_seconds, 6.0),
        metar_station=cfg.metar_station or cfg.airport_icao,
        metar_interval=cfg.metar_interval_minutes * 60.0,
    )
