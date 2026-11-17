"""The fetch loop: freshness, merging, backoff, and no ghosts."""
import dataclasses

import pytest

from fetch.http import FetchError, _retry_after
from fetch.merge import merge_polls
from fetch.sources import AdsbFiSource, AirplanesLiveSource, Poll, _payload_age
from fetch.worker import FetchLoop, backoff_seconds
from state import SharedState
from tests.helpers import FakeEnricher, lookups, stamped, make_tracker


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeSource:
    kind = "network"
    min_interval = 1.0

    def __init__(self, name, clock):
        self.name, self.clock = name, clock
        self.records: list[dict] = []
        self.error: FetchError | None = None
        self.calls = 0
        self.age = 0.0

    def fetch(self):
        self.calls += 1
        if self.error:
            raise self.error
        return Poll(self.name, [dict(r) for r in self.records], self.clock(), self.age)


def rec(hex_id="a1b2c3", lat=19.9, lon=-156.1, seen_pos=0.5, **kw):
    base = {"hex": hex_id, "flight": "UAL1158 ", "r": "N68823", "t": "B739", "alt_baro": 5000,
            "gs": 250.0, "track": 180.0, "baro_rate": 0, "lat": lat, "lon": lon,
            "seen_pos": seen_pos}
    base.update(kw)
    return base


def make_loop(cfg, clock, *sources, **overrides):
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    state = SharedState()
    loop = FetchLoop(cfg, state, sources=list(sources), enricher=FakeEnricher(),
                     lookups=lookups(), clock=clock)
    return loop, state


def advance(loop, clock, seconds, step=1.0):
    end = clock.t + seconds
    while clock.t < end:
        loop.tick()
        clock.t += step
    loop.tick()


# ---- URLs and timestamps ----

def test_source_urls(cfg):
    assert AirplanesLiveSource(19.7388, -156.0456, 40).url == \
        "https://api.airplanes.live/v2/point/19.738800/-156.045600/40"
    assert AdsbFiSource(19.7388, -156.0456, 40).url == \
        "https://opendata.adsb.fi/api/v3/lat/19.738800/lon/-156.045600/dist/40"
    assert AirplanesLiveSource(19.7, -156.0, 9999).url.endswith("/250")     # API maximum


def test_payload_age_is_only_trusted_when_small():
    assert _payload_age(1794880800000, 1794880801.5) == pytest.approx(1.5)   # ms
    assert _payload_age(1794880800.0, 1794880802.0) == pytest.approx(2.0)    # s (readsb)
    assert _payload_age(1794880800000, 1794880800.0 - 3600) == 0.0           # our clock is behind
    assert _payload_age(1794880800000, 1794880800.0 + 3600) == 0.0           # our clock is ahead
    assert _payload_age(None, 1.0) == 0.0 and _payload_age("x", 1.0) == 0.0


def test_retry_after_header():
    assert _retry_after({"Retry-After": "120"}) == 120.0
    assert _retry_after({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert _retry_after({}) is None and _retry_after({"Retry-After": "-5"}) is None


def test_backoff_rules():
    assert [backoff_seconds(FetchError("x"), n, 5.0) for n in (1, 2, 3, 4, 5, 9)] == \
        [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]
    assert backoff_seconds(FetchError("HTTP 429", 429), 1, 5.0) == 60.0
    assert backoff_seconds(FetchError("HTTP 429", 429, retry_after=7.0), 1, 5.0) == 7.0
    assert backoff_seconds(FetchError("HTTP 429", 429, retry_after=1.0), 1, 5.0) == 5.0
    assert backoff_seconds(FetchError("HTTP 403", 403), 1, 5.0) == 900.0     # refused: leave it alone


# ---- merge ----

def test_merge_keeps_the_freshest_position_per_aircraft():
    a = Poll("one", [rec(seen_pos=4.0, lat=19.90)], received_mono=1000.0)
    b = Poll("two", [rec(seen_pos=0.5, lat=19.89)], received_mono=1000.0)
    out = merge_polls([a, b], 1000.0, 30.0)
    assert len(out) == 1 and out[0]["lat"] == 19.89 and out[0]["_source"] == "two"
    assert out[0]["_pos_mono"] == pytest.approx(999.5)
    # an older response from the "better" source loses to a newer one from the other
    b_old = Poll("two", [rec(seen_pos=0.5, lat=19.89)], received_mono=990.0)
    assert merge_polls([a, b_old], 1000.0, 30.0)[0]["_source"] == "one"


def test_merge_is_a_union_with_no_duplicates():
    a = Poll("one", [rec("aaaaaa"), rec("bbbbbb")], received_mono=1000.0)
    b = Poll("two", [rec("BBBBBB "), rec("cccccc")], received_mono=1000.0)
    out = merge_polls([a, b, None], 1000.0, 30.0)
    assert sorted(r["hex"] for r in out) == ["aaaaaa", "bbbbbb", "cccccc"]


def test_merge_drops_stale_and_positionless_records_but_uses_their_identity():
    bare = {"hex": "aaaaaa", "lat": 19.9, "lon": -156.1, "alt_baro": 3000, "seen_pos": 0.2}
    named = {"hex": "aaaaaa", "flight": "N739HP  ", "r": "N739HP", "t": "C172", "seen": 2.0}
    stale = rec("bbbbbb", seen_pos=45.0)
    out = merge_polls([Poll("local", [bare], 1000.0), Poll("net", [named, stale], 1000.0)],
                      1000.0, 30.0)
    assert len(out) == 1
    assert out[0]["r"] == "N739HP" and out[0]["t"] == "C172" and out[0]["_source"] == "local"


def test_merge_accounts_for_response_age():
    fresh_but_cached = Poll("one", [rec(seen_pos=1.0)], received_mono=1000.0, age_seconds=8.0)
    assert merge_polls([fresh_but_cached], 1000.0, 30.0)[0]["_pos_mono"] == pytest.approx(991.0)
    assert merge_polls([fresh_but_cached], 1025.0, 30.0) == []


def test_preferred_source_wins_while_fresh():
    local = Poll("local", [rec(seen_pos=3.0, lat=19.80)], received_mono=1000.0)
    net = Poll("net", [rec(seen_pos=0.2, lat=19.81)], received_mono=1000.0)
    assert merge_polls([net, local], 1000.0, 30.0, preferred=("local",))[0]["_source"] == "local"
    stale_local = Poll("local", [rec(seen_pos=40.0)], received_mono=1000.0)
    assert merge_polls([net, stale_local], 1000.0, 30.0, preferred=("local",))[0]["_source"] == "net"


# ---- tracker filtering (audit B, additions 15) ----

def test_stale_vehicle_anonymous_and_positionless_records_never_become_aircraft(nasty, cfg):
    tracker = make_tracker(cfg)
    now = 1000.0
    out, _ = tracker.update([stamped(r, now) for r in nasty.values()], now)
    hexes = {a.hex for a in out}
    assert "a50004" not in hexes          # seen_pos 61 s
    assert "a0f001" not in hexes          # airport ground vehicle (category C2)
    assert "~2a1f3c" not in hexes         # anonymous TIS-B target
    assert "a5000d" not in hexes          # no position
    assert "a50010" not in hexes          # position is not a number
    assert len(out) == 13
    assert all(a.altitude_ft is None or a.altitude_ft >= 0 for a in out)
    assert [a.distance_nm for a in out] == sorted(a.distance_nm for a in out)


def test_aircraft_beyond_the_configured_radius_are_dropped(cfg):
    tracker = make_tracker(cfg, radius_nm=40.0)
    near, far = rec("aaaaaa", lat=20.3), rec("bbbbbb", lat=20.5)     # 34 nm and 46 nm north
    out, _ = tracker.update([stamped(near), stamped(far)], 1000.0)
    assert [a.hex for a in out] == ["aaaaaa"]


def test_anonymous_targets_can_be_switched_on(nasty, cfg):
    tracker = make_tracker(cfg, show_anonymous=True)
    out, _ = tracker.update([stamped(nasty["~2a1f3c"])], 1000.0)
    assert len(out) == 1 and out[0].label == "" and out[0].pos_source == "other"


def test_mlat_positions_are_marked(nasty, cfg):
    tracker = make_tracker(cfg)
    out, _ = tracker.update([stamped(nasty["ae1464"]), stamped(nasty["a50007"])], 1000.0)
    by = {a.hex: a for a in out}
    assert by["ae1464"].pos_source == "mlat" and by["ae1464"].military
    assert by["a50007"].pos_source == "adsb" and not by["a50007"].military


def test_one_malformed_record_does_not_sink_the_rest(cfg):
    tracker = make_tracker(cfg)
    bad = {"hex": "badbad", "lat": 19.9, "lon": -156.1, "_pos_mono": "yesterday", "gs": [],
           "alt_baro": {}, "flight": 12, "category": 7, "dbFlags": "x"}
    out, _ = tracker.update([bad, stamped(rec())], 1000.0)
    assert [a.hex for a in out] == ["a1b2c3"]


def test_tracker_memory_is_bounded(cfg):
    tracker = make_tracker(cfg)
    for i in range(300):
        tracker.update([stamped(rec(f"{i:06x}"), 1000.0 + i * 5)], 1000.0 + i * 5)
    assert len(tracker._mem) <= 40        # only what was seen in the last 3 minutes


def test_trail_grows_with_movement_and_is_capped_in_time(cfg):
    tracker = make_tracker(cfg, trail_seconds=60.0)
    out = None
    for i in range(40):                   # 200 s flying south at 250 kt
        now = 1000.0 + i * 5
        out, _ = tracker.update([stamped(rec(lat=19.9 - i * 0.0058), now)], now)
    trail = out[0].trail
    assert 5 <= len(trail) <= 14          # ~60 s of points, not all 40
    assert trail[0][0] > trail[-1][0]     # oldest first


# ---- the loop ----

def test_publishes_aircraft_with_position_time(cfg):
    clock = Clock()
    src = FakeSource("one", clock)
    src.records = [rec(seen_pos=2.0)]
    loop, state = make_loop(cfg, clock, src)
    loop.tick()
    snap = state.read()
    assert snap.ok and len(snap.aircraft) == 1 and snap.featured_hex == "a1b2c3"
    assert snap.aircraft[0].pos_mono == pytest.approx(998.0)
    assert snap.aircraft[0].flight == "UA1158"
    assert snap.sources[0].ok and snap.sources[0].aircraft == 1


def test_polls_at_the_configured_interval_not_faster(cfg):
    clock = Clock()
    src = FakeSource("one", clock)
    loop, _ = make_loop(cfg, clock, src)
    advance(loop, clock, 60.0, step=0.25)
    assert 12 <= src.calls <= 13          # every 5 s


def test_sources_are_staggered(cfg):
    clock = Clock()
    a, b = FakeSource("one", clock), FakeSource("two", clock)
    loop, _ = make_loop(cfg, clock, a, b)
    loop.tick()
    assert (a.calls, b.calls) == (1, 0)
    clock.t += 2.5
    loop.tick()
    assert (a.calls, b.calls) == (1, 1)


def test_no_ghosts_when_the_network_dies(cfg):
    clock = Clock()
    src = FakeSource("one", clock)
    src.records = [rec()]
    loop, state = make_loop(cfg, clock, src)
    loop.tick()
    assert len(state.read().aircraft) == 1
    src.error = FetchError("network unreachable")
    advance(loop, clock, 20.0)
    snap = state.read()
    assert not snap.ok and snap.consecutive_failures >= 1
    assert len(snap.aircraft) == 1        # still inside the freshness window: dead reckoned
    advance(loop, clock, 25.0)
    assert state.read().aircraft == ()    # 45 s with no update: gone, not frozen


def test_renderer_side_filter_drops_ghosts_even_if_the_fetch_thread_is_frozen(cfg):
    from render.renderer import live_aircraft
    clock = Clock()
    src = FakeSource("one", clock)
    src.records = [rec()]
    loop, state = make_loop(cfg, clock, src)
    loop.tick()
    snap = state.read()                   # ...and the fetch thread never runs again
    assert len(live_aircraft(snap, clock.t + 10, cfg.max_position_age_seconds)) == 1
    assert live_aircraft(snap, clock.t + 31, cfg.max_position_age_seconds) == []


def test_failed_source_backs_off_and_recovers(cfg):
    clock = Clock()
    src = FakeSource("one", clock)
    src.error = FetchError("timed out")
    loop, state = make_loop(cfg, clock, src)
    advance(loop, clock, 120.0)
    assert src.calls <= 6                 # 5, 10, 20, 40, 60... not 24 polls
    assert not state.read().ok and state.read().sources[0].error == "timed out"
    src.error, src.records = None, [rec()]
    advance(loop, clock, 61.0)
    assert state.read().ok and len(state.read().aircraft) == 1


def test_rate_limited_source_waits_for_retry_after(cfg):
    clock = Clock()
    src = FakeSource("one", clock)
    src.error = FetchError("HTTP 429", status=429, retry_after=90.0)
    loop, _ = make_loop(cfg, clock, src)
    advance(loop, clock, 80.0)
    assert src.calls == 1
    advance(loop, clock, 15.0)
    assert src.calls == 2


def test_refused_source_is_left_alone_for_a_long_time(cfg):
    clock = Clock()
    src = FakeSource("one", clock)
    src.error = FetchError("HTTP 403", status=403)
    loop, _ = make_loop(cfg, clock, src)
    advance(loop, clock, 600.0, step=5.0)
    assert src.calls == 1


def test_one_dead_source_does_not_stop_the_other(cfg):
    clock = Clock()
    dead, alive = FakeSource("dead", clock), FakeSource("alive", clock)
    dead.error = FetchError("down")
    alive.records = [rec()]
    loop, state = make_loop(cfg, clock, dead, alive)
    advance(loop, clock, 60.0)
    snap = state.read()
    assert snap.ok and len(snap.aircraft) == 1
    health = {s.name: s for s in snap.sources}
    assert not health["dead"].ok and health["alive"].ok
    assert alive.calls >= 11


def test_two_sources_never_show_the_same_aircraft_twice(cfg):
    clock = Clock()
    a, b = FakeSource("one", clock), FakeSource("two", clock)
    a.records = [rec("aaaaaa"), rec("bbbbbb", lat=19.8)]
    b.records = [rec("bbbbbb", lat=19.8), rec("cccccc", lat=19.7)]
    loop, state = make_loop(cfg, clock, a, b)
    advance(loop, clock, 12.0)
    hexes = [x.hex for x in state.read().aircraft]
    assert sorted(hexes) == ["aaaaaa", "bbbbbb", "cccccc"]


def test_abandoned_thread_cannot_publish(cfg):
    clock = Clock()
    src = FakeSource("one", clock)
    src.records = [rec()]
    loop, state = make_loop(cfg, clock, src)
    loop.tick(should_publish=lambda: False)
    assert state.read().seq == 0 and state.read().aircraft == ()
