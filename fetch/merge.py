"""Merge responses from several sources into one record per aircraft.

The community feeds share some receivers but not all, so each one sees planes
the others miss, especially low near Kona. Per aircraft (keyed by its ICAO
hex) the freshest position wins. A local receiver, when there is one, wins
outright while its position is fresh: it has no network delay at all.

Identity fields the winning record lacks (a local receiver knows nothing about
registrations or types) are filled in from the other sources' records for the
same aircraft.
"""
from __future__ import annotations

from fetch.sources import Poll

# Fields that describe the airframe rather than its current state.
IDENTITY_KEYS = ("flight", "r", "t", "desc", "ownOp", "category", "dbFlags")


def _seen_pos(rec: dict) -> float:
    for key in ("seen_pos", "seen"):
        v = rec.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            return float(v)
    return 0.0


def position_time(rec: dict, poll: Poll) -> float:
    """Monotonic time at which this record's position was true."""
    return poll.received_mono - poll.age_seconds - _seen_pos(rec)


def merge_polls(polls, now: float, max_age: float,
                preferred: tuple[str, ...] = ()) -> list[dict]:
    """One record per hex from all `polls`.

    Each returned record is a shallow copy carrying two extra keys:
      _pos_mono  monotonic time its position was true
      _source    name of the source that supplied the position
    Records with no position, or one older than `max_age`, are left out.
    """
    best: dict[str, dict] = {}
    others: dict[str, list[dict]] = {}
    for poll in polls:
        if poll is None:
            continue
        for rec in poll.aircraft:
            hex_id = rec.get("hex")
            if not isinstance(hex_id, str) or not hex_id:
                continue
            hex_id = hex_id.strip().lower()
            if rec.get("lat") is None or rec.get("lon") is None:
                others.setdefault(hex_id, []).append(rec)    # identity may still be useful
                continue
            t = position_time(rec, poll)
            if now - t > max_age:
                others.setdefault(hex_id, []).append(rec)
                continue
            cand = dict(rec)
            cand["hex"] = hex_id
            cand["_pos_mono"] = t
            cand["_source"] = poll.source
            cur = best.get(hex_id)
            if cur is None:
                best[hex_id] = cand
                continue
            cur_pref, cand_pref = cur["_source"] in preferred, poll.source in preferred
            if (cand_pref, t) > (cur_pref, cur["_pos_mono"]):
                best[hex_id] = cand
                others.setdefault(hex_id, []).append(cur)
            else:
                others.setdefault(hex_id, []).append(cand)

    for hex_id, rec in best.items():
        for other in others.get(hex_id, ()):
            for key in IDENTITY_KEYS:
                if rec.get(key) in (None, "") and other.get(key) not in (None, ""):
                    rec[key] = other[key]
    return list(best.values())
