"""Origin/destination lookup by callsign.

ADS-B broadcasts position, not a flight's city pair, so we resolve routes from
adsbdb.com — a free, community route database. One cached GET per *featured*
flight per poll at most; results (and misses) are cached so we never hammer it.
Never raises: any failure returns None and the banner falls back to a compass
direction.

    GET https://api.adsbdb.com/v0/callsign/{CALLSIGN}
    -> {"response": {"flightroute": {"origin": {...}, "destination": {...}}}}
       or {"response": "unknown callsign"} when not found.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger("routes")

USER_AGENT = "flight-tracker-wall-display/1.0 (personal, noncommercial)"


class RouteLookup:
    BASE = "https://api.adsbdb.com/v0/callsign/"

    def __init__(self, timeout: float = 4.0, ttl: float = 3600.0):
        self.timeout = timeout
        self.ttl = ttl
        self._cache: dict[str, tuple[float, dict | None]] = {}

    def lookup(self, callsign: str) -> dict | None:
        cs = (callsign or "").strip().upper()
        if not cs:
            return None
        now = time.monotonic()
        hit = self._cache.get(cs)
        if hit and hit[0] > now:
            return hit[1]

        route = None
        try:
            url = self.BASE + urllib.parse.quote(cs)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    route = self._parse(json.load(resp))
        except Exception as e:  # HTTPError(404), timeout, JSON error, etc.
            log.debug("route lookup failed for %s: %s", cs, e)
            route = None

        self._cache[cs] = (now + self.ttl, route)
        return route

    @staticmethod
    def _parse(data: dict) -> dict | None:
        try:
            fr = data["response"]["flightroute"]
            o, d = fr["origin"], fr["destination"]
        except (KeyError, TypeError):
            return None  # "unknown callsign" or unexpected shape

        def code(ap):
            return (ap.get("iata_code") or ap.get("icao_code") or "").strip()

        def place(ap):
            return (ap.get("municipality") or ap.get("name") or "").strip()

        return {
            "origin_code": code(o), "origin_name": place(o),
            "dest_code": code(d), "dest_name": place(d),
        }
