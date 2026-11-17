"""A small bounded cache: least-recently-used eviction plus a per-entry TTL.

Used for every enrichment lookup so memory stays flat on the 512 MB Pi no
matter how long the app runs. Thread-safe (the enrichment thread writes, the
fetch thread reads).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

_MISSING = object()


class LruTtlCache:
    def __init__(self, max_entries: int = 512, clock=time.monotonic):
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._lock = threading.Lock()
        self._data: OrderedDict = OrderedDict()   # key -> (expires_at, value)

    def get(self, key, default=None):
        """The cached value, or `default` when absent or expired. A cached
        None is a real answer ("looked up, nothing known") and is returned."""
        with self._lock:
            hit = self._data.get(key, _MISSING)
            if hit is _MISSING:
                return default
            if hit[0] <= self._clock():
                del self._data[key]
                return default
            self._data.move_to_end(key)
            return hit[1]

    def __contains__(self, key) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def put(self, key, value, ttl: float) -> None:
        with self._lock:
            self._data[key] = (self._clock() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
