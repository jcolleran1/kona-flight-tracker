"""Airline (registered owner) + registration lookup by Mode S hex.

A plane broadcasts its position constantly but its *callsign* much less often,
especially just after takeoff. Until the callsign arrives we can't derive the
airline from the flight number (UAL1759 -> United). But the hex code is a
permanent hardware ID, so we can resolve the operator straight from it via
adsbdb's aircraft endpoint — the same community database we use for routes.

    GET https://api.adsbdb.com/v0/aircraft/{MODE_S_HEX}
    -> {"response": {"aircraft": {"registered_owner": "United Airlines",
                                  "registration": "N41140", ...}}}
       or {"response": "unknown aircraft"} when not found.

Cached per hex with a long TTL (an airframe's owner rarely changes). Never
raises: any failure returns None and the caller keeps whatever it already had.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger("aircraftdb")

USER_AGENT = "flight-tracker-wall-display/1.0 (personal, noncommercial)"


class AircraftLookup:
    BASE = "https://api.adsbdb.com/v0/aircraft/"

    def __init__(self, timeout: float = 4.0, ttl: float = 86400.0):
        self.timeout = timeout
        self.ttl = ttl
        self._cache: dict[str, tuple[float, dict | None]] = {}

    def cached(self, hex_id: str) -> dict | None:
        """Return a cached result without ever hitting the network (None if absent)."""
        key = (hex_id or "").strip().upper()
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def lookup(self, hex_id: str) -> dict | None:
        key = (hex_id or "").strip().upper()
        if not key:
            return None
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

        info = None
        try:
            url = self.BASE + urllib.parse.quote(key)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    info = self._parse(json.load(resp))
        except Exception as e:  # HTTPError(404), timeout, JSON error, etc.
            log.debug("aircraft lookup failed for %s: %s", key, e)
            info = None

        self._cache[key] = (now + self.ttl, info)
        return info

    @staticmethod
    def _parse(data: dict) -> dict | None:
        try:
            ac = data["response"]["aircraft"]
        except (KeyError, TypeError):
            return None  # "unknown aircraft" or unexpected shape
        owner = (ac.get("registered_owner") or "").strip()
        reg = (ac.get("registration") or "").strip()
        if not owner and not reg:
            return None
        return {"airline": owner, "registration": reg}
