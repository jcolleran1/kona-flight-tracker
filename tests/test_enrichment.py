"""Enrichment must be cached, bounded, polite, and never cache a network error."""
import pytest

from enrich.cache import LruTtlCache
from enrich.metar import parse_metar, qnh_from_raw
from enrich.worker import (AIRCRAFT_TTL, UNKNOWN_TTL, NetworkEnricher, StaticEnricher,
                           aircraft_from_dict, make_enricher)
from fetch.http import FetchError


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


ROUTE_BODY = {"response": {"flightroute": {
    "callsign_iata": "UA1158", "airline": {"name": "United Airlines", "icao": "UAL", "iata": "UA"},
    "origin": {"iata_code": "KOA", "icao_code": "PHKO", "latitude": 19.74, "longitude": -156.05,
               "municipality": "Kailua-Kona"},
    "destination": {"iata_code": "SFO", "icao_code": "KSFO", "latitude": 37.62,
                    "longitude": -122.38, "municipality": "San Francisco"}}}}
AIRCRAFT_BODY = {"response": {"aircraft": {
    "type": "717 22A", "icao_type": "B712", "manufacturer": "Boeing", "mode_s": "A5F825",
    "registration": "N484HA", "registered_owner": "Hawaiian Airlines"}}}
METAR_BODY = [{"icaoId": "PHKO", "obsTime": 1794880380, "temp": 27.8, "wdir": 280, "wspd": 7,
               "altim": 1016.7, "rawOb": "METAR PHKO 170153Z 28007KT 10SM SCT040 28/19 A3002",
               "cover": "SCT", "fltCat": "VFR"}]


class FakeNet:
    def __init__(self):
        self.calls: list[str] = []
        self.answers: dict[str, object] = {}

    def __call__(self, url, timeout):
        self.calls.append(url)
        for key, answer in self.answers.items():
            if key in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise FetchError("HTTP 404", status=404)


def make(net, clock, **kw):
    return NetworkEnricher(min_interval=2.0, fetcher=net, clock=clock, **kw)


def drain(enricher, clock, limit=50):
    for _ in range(limit):
        clock.t += enricher.step()
        if enricher.idle():
            return
    raise AssertionError("enricher never went idle")


# ---- cache ----

def test_cache_lru_and_ttl():
    clock = Clock()
    c = LruTtlCache(max_entries=2, clock=clock)
    c.put("a", 1, ttl=10)
    c.put("b", None, ttl=10)                 # a cached "unknown" is a real entry
    assert c.get("a") == 1 and "b" in c and c.get("b", "dflt") is None
    c.put("c", 3, ttl=10)                    # evicts the least recently used: "a"
    assert "a" not in c and len(c) == 2
    clock.t += 11
    assert "b" not in c and c.get("c") is None and len(c) == 0


def test_cache_never_grows_past_its_bound():
    c = LruTtlCache(max_entries=50)
    for i in range(5000):
        c.put(i, i, ttl=3600)
    assert len(c) == 50


# ---- adsbdb ----

def test_route_lookup_is_cached_and_not_repeated():
    net, clock = FakeNet(), Clock()
    net.answers["/callsign/UAL1158"] = ROUTE_BODY
    e = make(net, clock)
    assert e.route("UAL1158") is None
    e.request_route("UAL1158")
    drain(e, clock)
    assert e.route("UAL1158").destination.code == "SFO"
    e.request_route("UAL1158")               # already cached: no new request
    assert e.idle() and len(net.calls) == 1


def test_unknown_answer_is_cached_for_a_while_then_asked_again():
    net, clock = FakeNet(), Clock()
    e = make(net, clock)
    e.request_route("ZZZ1")
    drain(e, clock)
    assert e.route("ZZZ1") is None and "ZZZ1" in e._routes       # cached as "unknown"
    e.request_route("ZZZ1")
    assert e.idle() and len(net.calls) == 1
    clock.t += UNKNOWN_TTL + 1
    e.request_route("ZZZ1")
    assert not e.idle()


def test_network_errors_are_not_cached_and_back_off():
    net, clock = FakeNet(), Clock()
    net.answers["/callsign/UAL1158"] = FetchError("timed out")
    e = make(net, clock)
    e.request_route("UAL1158")
    e.step()
    assert "UAL1158" not in e._routes                            # nothing cached
    assert e._next_request_at - clock.t == pytest.approx(10.0)   # first backoff step
    e.request_route("UAL1158")
    assert e.idle()                                              # this key is resting
    # the service comes back; after the rest period the key can be asked again
    net.answers["/callsign/UAL1158"] = ROUTE_BODY
    clock.t += 61
    e.request_route("UAL1158")
    drain(e, clock)
    assert e.route("UAL1158") is not None


def test_backoff_grows_and_honours_retry_after():
    net, clock = FakeNet(), Clock()
    e = make(net, clock)
    waits = []
    for i in range(4):
        net.answers[f"/callsign/AAA{i}"] = FetchError("boom")
        e.request_route(f"AAA{i}")
        clock.t = max(clock.t, e._next_request_at)
        e.step()
        waits.append(e._next_request_at - clock.t)
    assert waits == [10.0, 20.0, 40.0, 80.0]
    net.answers["/callsign/BBB1"] = FetchError("HTTP 429", status=429, retry_after=300.0)
    e.request_route("BBB1")
    clock.t = e._next_request_at
    e.step()
    assert e._next_request_at - clock.t == pytest.approx(300.0)


def test_requests_are_spaced_by_the_minimum_interval():
    net, clock = FakeNet(), Clock()
    e = make(net, clock)
    for i in range(5):
        e.request_route(f"UAL{i}")
    stamps = []
    for _ in range(40):
        before = len(net.calls)
        wait = e.step()
        if len(net.calls) > before:
            stamps.append(clock.t)
        if e.idle():
            break
        clock.t += wait
    assert len(stamps) == 5
    assert all(b - a >= 2.0 - 1e-9 for a, b in zip(stamps, stamps[1:]))


def test_pending_queue_is_bounded_and_keeps_the_important_requests():
    net, clock = FakeNet(), Clock()
    e = make(net, clock)
    for i in range(500):
        e.request_route(f"AAL{i}", priority=float(i))
    assert len(e._pending) == 64
    assert ("route", "AAL499") in e._pending and ("route", "AAL0") not in e._pending
    e.step()
    assert net.calls[-1].endswith("/callsign/AAL499")            # highest priority first


def test_aircraft_lookup_and_parse():
    net, clock = FakeNet(), Clock()
    net.answers["/aircraft/A5F825"] = AIRCRAFT_BODY
    e = make(net, clock)
    e.request_aircraft("a5f825")
    drain(e, clock)
    info = e.aircraft("A5F825")
    assert (info.registration, info.type_code, info.owner) == ("N484HA", "B712", "Hawaiian Airlines")
    assert info.type_name == "Boeing 717 22A"
    clock.t += AIRCRAFT_TTL - 10
    assert e.aircraft("a5f825") is not None
    assert aircraft_from_dict({}) is None and aircraft_from_dict("unknown aircraft") is None


def test_unexpected_response_shapes_are_treated_as_unknown():
    net, clock = FakeNet(), Clock()
    net.answers["/callsign/UAL1"] = {"response": "unknown callsign"}
    net.answers["/callsign/UAL2"] = ["nonsense"]
    e = make(net, clock)
    e.request_route("UAL1")
    e.request_route("UAL2")
    drain(e, clock)
    assert e.route("UAL1") is None and e.route("UAL2") is None


# ---- METAR ----

def test_metar_parse():
    wx = parse_metar(METAR_BODY, "PHKO")
    assert wx.qnh_hpa == pytest.approx(1016.7) and wx.wind_dir_deg == 280 and wx.wind_kt == 7
    assert wx.flight_category == "VFR" and wx.cover == "SCT" and wx.temp_c == 27.8


def test_metar_altimeter_falls_back_to_the_raw_text():
    body = [dict(METAR_BODY[0], altim=None)]
    assert parse_metar(body).qnh_hpa == pytest.approx(30.02 * 33.8639, abs=0.01)
    assert qnh_from_raw("EGLL 170150Z 24008KT 9999 Q1009") == 1009.0
    assert qnh_from_raw("no setting here") is None
    # inches of mercury in the numeric field are converted, nonsense is dropped
    assert parse_metar([dict(METAR_BODY[0], altim=30.02)]).qnh_hpa == pytest.approx(1016.6, abs=0.1)
    assert parse_metar([dict(METAR_BODY[0], altim=5.0, rawOb="")]).qnh_hpa is None


@pytest.mark.parametrize("bad", [None, [], {}, "error", [None], [{"icaoId": "KSFO"}]])
def test_metar_garbage_is_none(bad):
    assert parse_metar(bad, "PHKO") is None


def test_variable_wind_and_calm():
    wx = parse_metar([dict(METAR_BODY[0], wdir="VRB", wspd=3)])
    assert wx.wind_dir_deg is None and wx.wind_kt == 3


def test_metar_is_fetched_first_then_on_its_interval_and_expires():
    net, clock = FakeNet(), Clock()
    net.answers["metar"] = METAR_BODY
    e = make(net, clock, metar_station="PHKO", metar_interval=1800.0)
    assert e.qnh() is None
    e.step()
    assert e.qnh() == pytest.approx(1016.7) and "ids=PHKO" in net.calls[0]
    clock.t += 600
    e.step()
    assert len(net.calls) == 1                                   # not due yet
    clock.t += 1300
    e.step()
    assert len(net.calls) == 2
    # the weather service goes away: after three hours the old setting is dropped
    net.answers["metar"] = FetchError("down")
    clock.t += 3 * 3600 + 1
    e.step()
    assert e.qnh() is None and e.weather() is None


def test_metar_failure_retries_sooner_than_the_normal_interval():
    net, clock = FakeNet(), Clock()
    net.answers["metar"] = FetchError("down")
    e = make(net, clock, metar_station="PHKO", metar_interval=1800.0)
    e.step()
    assert e._metar_due - clock.t == pytest.approx(300.0)


# ---- offline ----

def test_static_enricher_from_recording_meta():
    e = StaticEnricher({"qnh_hpa": 1017.9,
                        "routes": {"UAL1158": {"origin": {"iata": "KOA", "icao": "PHKO"},
                                               "destination": {"iata": "SFO", "icao": "KSFO"}}},
                        "aircraft": {"A6C5D1": {"registration": "N522SA", "type_code": "C208"}}})
    assert e.qnh() == 1017.9
    assert e.route("ual1158").destination.code == "SFO" and e.route("XXX1") is None
    assert e.aircraft("a6c5d1").type_code == "C208"
    e.request_route("XXX1")
    assert e.idle()


def test_replay_never_gets_a_network_enricher(cfg):
    from fetch.replay import ReplaySource
    assert isinstance(make_enricher(cfg, [ReplaySource()]), StaticEnricher)
    assert isinstance(make_enricher(cfg, []), NetworkEnricher)
    import dataclasses
    off = make_enricher(dataclasses.replace(cfg, enrich_enabled=False), [])
    assert not isinstance(off, (NetworkEnricher, StaticEnricher)) and off.qnh() is None
