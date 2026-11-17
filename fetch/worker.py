"""Fetch thread: polls the sources, merges and enriches, publishes snapshots.

One loop, one thread, the only writer of SharedState. Sources are polled in
turn on a staggered schedule, each at its own interval and with its own
backoff, so one dead or slow feed never stops the others. Nothing here waits
on enrichment (see enrich/worker.py).

Never raises out of run(): the renderer must keep going whatever happens here.
"""
from __future__ import annotations

import logging
import threading
import time

from config import Config
from enrich.lookups import Lookups
from enrich.tracker import Tracker
from enrich.worker import make_enricher
from fetch.merge import merge_polls
from fetch.sources import FetchError, Poll, make_source
from state import SharedState, SourceHealth

log = logging.getLogger("fetch")

BACKOFF_BASE = 5.0       # seconds
BACKOFF_MAX = 60.0       # a home Wi-Fi blip should never cost more than a minute
REFUSED_BACKOFF = 900.0  # HTTP 401/403: the service doesn't want us right now; leave it alone
RATE_LIMIT_BACKOFF = 60.0


def backoff_seconds(error: FetchError, failures: int, interval: float) -> float:
    """How long to leave a source alone after a failed poll."""
    if error.retry_after is not None:
        wait = error.retry_after
    elif error.status in (401, 403):
        wait = REFUSED_BACKOFF
    elif error.status == 429:
        wait = RATE_LIMIT_BACKOFF
    else:
        wait = min(BACKOFF_BASE * 2 ** max(0, failures - 1), BACKOFF_MAX)
    return max(wait, interval)


class _SourceState:
    def __init__(self, source, interval: float, first_due: float):
        self.source = source
        self.name = source.name
        wanted = getattr(source, "poll_interval", None) or interval
        self.interval = max(wanted, getattr(source, "min_interval", 0.0))
        self.next_due = first_due
        self.failures = 0
        self.last_ok: float | None = None
        self.last_poll: Poll | None = None
        self.last_error = ""
        self.last_attempt_ok = False
        self.count = 0

    def health(self) -> SourceHealth:
        return SourceHealth(name=self.name, ok=self.last_attempt_ok, aircraft=self.count,
                            last_ok=self.last_ok, error=self.last_error)


class FetchLoop:
    """The polling state machine, separate from the thread so tests (and
    --once, and the preview tool) can drive it one step at a time."""

    def __init__(self, cfg: Config, state: SharedState | None = None, *, sources=None,
                 enricher=None, lookups: Lookups | None = None, clock=time.monotonic,
                 confirm_seconds: float = 12.0):
        self.cfg = cfg
        self.state = state
        self.clock = clock
        srcs = list(sources) if sources is not None else [make_source(n, cfg) for n in cfg.sources]
        now = clock()
        stagger = cfg.interval_seconds / max(1, len(srcs))
        self.sources = [_SourceState(s, cfg.interval_seconds, now + i * stagger)
                        for i, s in enumerate(srcs)]
        self.preferred = tuple(s.name for s in srcs if getattr(s, "kind", "") == "local")
        self.enricher = enricher if enricher is not None else make_enricher(cfg, srcs)
        self.tracker = Tracker(cfg, lookups or Lookups.load(), self.enricher, confirm_seconds)
        self.aircraft: list = []
        self.featured_hex: str | None = None

    def next_due(self) -> float:
        return min(s.next_due for s in self.sources)

    def poll(self, st: _SourceState) -> bool:
        """Poll one source and update its state. True on success."""
        try:
            poll = st.source.fetch()
        except FetchError as e:
            st.failures += 1
            st.last_attempt_ok = False
            st.last_error = str(e)[:80]
            wait = backoff_seconds(e, st.failures, st.interval)
            st.next_due = self.clock() + wait
            log.warning("%s: fetch failed (%d in a row): %s -- retrying in %.0fs",
                        st.name, st.failures, e, wait)
            return False
        now = self.clock()
        if st.failures:
            log.info("%s: recovered after %d failures", st.name, st.failures)
        st.failures, st.last_error, st.last_attempt_ok = 0, "", True
        st.last_ok, st.last_poll = now, poll
        st.count = sum(1 for r in poll.aircraft if r.get("lat") is not None)
        st.next_due = now + st.interval
        return True

    def rebuild(self) -> None:
        """Merge every source's latest response and run enrichment over it."""
        now = self.clock()
        max_age = self.cfg.max_position_age_seconds
        merged = merge_polls([s.last_poll for s in self.sources], now, max_age, self.preferred)
        self.aircraft, self.featured_hex = self.tracker.update(merged, now)

    def publish(self, ok: bool) -> None:
        if self.state is None:
            return
        now = self.clock()
        stamps = [s.last_ok for s in self.sources if s.last_ok is not None]
        self.state.publish(
            self.aircraft, featured_hex=self.featured_hex, ok=ok,
            fetched_at=max(stamps) if stamps else 0.0,
            sources=[s.health() for s in self.sources],
            weather=self.enricher.weather())

    def tick(self, should_publish=lambda: True) -> float:
        """Poll whichever source is due (at most one), publish, and return the
        number of seconds until the next source is due."""
        now = self.clock()
        due = [s for s in self.sources if s.next_due <= now]
        if due:
            st = min(due, key=lambda s: s.next_due)
            ok = self.poll(st)
            self.rebuild()
            # Healthy overall if this poll worked, or another source has
            # delivered within its last couple of cycles.
            recent = any(s.last_ok is not None and self.clock() - s.last_ok
                         <= 2 * s.interval + self.cfg.timeout_seconds for s in self.sources)
            if should_publish():
                self.publish(ok or recent)
        return max(0.05, self.next_due() - self.clock())


def run(state: SharedState, cfg: Config, stop: threading.Event) -> None:
    """Thread entry point. `stop` is this thread's own token: once it is set
    the thread publishes nothing more, even if it was stuck in a socket call
    when the watchdog replaced it."""
    loop: FetchLoop | None = None
    failures = 0
    while not stop.is_set():
        try:
            if loop is None:
                loop = FetchLoop(cfg, state)
                loop.enricher.start()
                log.info("fetch thread up: %s every %gs", ", ".join(cfg.sources),
                         cfg.interval_seconds)
            wait = loop.tick(should_publish=lambda: not stop.is_set())
            failures = 0
        except Exception:
            # Truly unexpected; log it, back off, keep the thread alive.
            failures += 1
            wait = min(BACKOFF_BASE * 2 ** (failures - 1), BACKOFF_MAX)
            log.exception("unexpected error in fetch loop -- retrying in %.0fs", wait)
            if not stop.is_set():
                state.publish((), ok=False, fetched_at=state.read().fetched_at)
        stop.wait(wait)
    if loop is not None:
        loop.enricher.stop()
    log.info("fetch thread stopping")
