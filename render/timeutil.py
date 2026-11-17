"""Wall-clock helpers: local time in the configured zone, quiet hours, and
whether the system clock can be trusted yet.

The Pi Zero has no battery-backed clock. Until NTP syncs after boot the system
time is whatever was saved at the last shutdown, so the display must not show
it, and must not decide it is "quiet hours" from it. The zone comes from
config rather than the Pi's own setting, which has never been verified.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from functools import lru_cache

log = logging.getLogger("render")

# Zones without daylight saving that we can serve even if the system has no
# tz database at all.
FIXED_OFFSETS = {"Pacific/Honolulu": -10.0, "UTC": 0.0}

# Any system time before this is an unset clock, not the real date.
EARLIEST_PLAUSIBLE = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()


@lru_cache(maxsize=4)
def zone(name: str) -> dt.tzinfo | None:
    """tzinfo for an IANA zone name; None means "use the system's local time"."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:           # no tzdata installed, or an unknown name
        if name in FIXED_OFFSETS:
            return dt.timezone(dt.timedelta(hours=FIXED_OFFSETS[name]), name)
        log.warning("timezone %r unavailable; using the system's local time", name)
        return None


def clock_is_set(now_epoch: float | None = None) -> bool:
    return (time.time() if now_epoch is None else now_epoch) >= EARLIEST_PLAUSIBLE


def local_now(cfg, now_epoch: float | None = None) -> dt.datetime:
    epoch = time.time() if now_epoch is None else now_epoch
    tz = zone(cfg.timezone)
    if tz is None:
        return dt.datetime.fromtimestamp(epoch)
    return dt.datetime.fromtimestamp(epoch, tz)


def in_quiet_hours(cfg, now: dt.datetime | None = None) -> bool:
    """True during the configured dim period. Never true while the clock is
    unset: a dark screen at noon is worse than a bright one at night."""
    if not cfg.quiet_enabled:
        return False
    if now is None:
        if not clock_is_set():
            return False
        now = local_now(cfg)
    t, start, end = now.time().replace(tzinfo=None), cfg.quiet_start, cfg.quiet_end
    if start <= end:
        return start <= t < end
    return t >= start or t < end          # crosses midnight


def fmt_clock(now: dt.datetime, clock_24h: bool, seconds: bool = False) -> str:
    if clock_24h:
        return now.strftime("%H:%M:%S" if seconds else "%H:%M")
    hour = now.hour % 12 or 12
    tail = now.strftime(":%M:%S" if seconds else ":%M")
    return f"{hour}{tail} {'AM' if now.hour < 12 else 'PM'}"
