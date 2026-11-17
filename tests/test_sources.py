"""Phase 2: more than one source, and a receiver on the local network."""
import json

import pytest

import fetch.sources as sources_mod
from fetch.merge import merge_polls
from fetch.sources import AdsbLolSource, FetchError, Poll, ReadsbSource, make_source
from fetch.worker import FetchLoop
from tests.conftest import FIXTURES
from tests.helpers import FakeEnricher, lookups, make_tracker


@pytest.fixture
def local_json():
    with open(FIXTURES / "readsb_aircraft.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def served(monkeypatch):
    """Serve canned JSON for URLs instead of the network."""
    answers: dict[str, object] = {}
    calls: list[str] = []

    def fake_get(url, timeout=10.0):
        calls.append(url)
        for key, value in answers.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise FetchError("HTTP 404", status=404)
    monkeypatch.setattr(sources_mod, "http_get_json", fake_get)
    answers["_calls"] = calls
    return answers


def test_adsb_lol_url_and_cautious_interval():
    src = AdsbLolSource(19.7388, -156.0456, 40)
    assert src.url == "https://api.adsb.lol/v2/point/19.738800/-156.045600/40"
    assert src.min_interval >= 10.0


def test_adsb_lol_parses_the_documented_v2_shape(served, cfg, monkeypatch):
    with open(FIXTURES / "adsb_lol_point.json", encoding="utf-8") as f:
        payload = json.load(f)
    served["api.adsb.lol/v2/point"] = payload
    monkeypatch.setattr(sources_mod.time, "time", lambda: payload["now"] / 1000 + 0.8)
    poll = AdsbLolSource(cfg.lat, cfg.lon, cfg.radius_nm).fetch()
    assert poll.source == "adsb_lol" and len(poll.aircraft) == 3
    assert poll.age_seconds == pytest.approx(0.8)          # `now` is in milliseconds
    now = poll.received_mono
    out, _ = make_tracker(cfg).update(merge_polls([poll], now, 30.0), now)
    by = {a.flight: a for a in out}
    assert set(by) == {"AS1121", "RCH321"}                 # UPS34 is 40.7 nm out: past the radius
    assert by["AS1121"].airline == "Hawaiian Airlines" and by["AS1121"].source == "adsb_lol"
    assert by["RCH321"].pos_source == "mlat"


def test_adsb_lol_refusal_puts_it_to_sleep_not_into_a_retry_loop(served, cfg):
    """Its README: 'If you get 4xx errors, you are doing something wrong', and
    an API key is planned. A 4xx must never be retried quickly."""
    from fetch.worker import backoff_seconds
    for status in (401, 403):
        served["api.adsb.lol"] = FetchError(f"HTTP {status}", status=status)
        with pytest.raises(FetchError) as e:
            AdsbLolSource(cfg.lat, cfg.lon, cfg.radius_nm).fetch()
        assert backoff_seconds(e.value, 1, 10.0) == 900.0
    assert backoff_seconds(FetchError("HTTP 429", status=429), 1, 10.0) == 60.0


def test_make_source_knows_every_configured_name(cfg):
    import dataclasses
    for name in ("airplanes_live", "adsb_fi", "adsb_lol", "local", "replay"):
        assert make_source(name, cfg).name == name
    with pytest.raises(ValueError):
        make_source("flightradar24", cfg)
    custom = dataclasses.replace(cfg, local_url="http://skyview.local:8080/data/aircraft.json")
    assert make_source("local", custom).url == "http://skyview.local:8080/data/aircraft.json"


def test_local_receiver_reads_recorded_aircraft_json(served, local_json, monkeypatch):
    served["aircraft.json"] = local_json
    src = ReadsbSource("http://127.0.0.1:8080/data/aircraft.json")
    # receiver clock and ours agree to within 1.5 s
    monkeypatch.setattr(sources_mod.time, "time", lambda: local_json["now"] + 1.5)
    poll = src.fetch()
    assert poll.source == "local" and len(poll.aircraft) == 7
    assert poll.age_seconds == pytest.approx(1.5)         # `now` is in seconds here
    assert src.kind == "local"


def test_local_receiver_through_the_whole_pipeline(served, local_json, cfg):
    served["aircraft.json"] = local_json
    src = ReadsbSource("http://127.0.0.1:8080/data/aircraft.json")
    poll = src.fetch()
    now = poll.received_mono
    tracker = make_tracker(cfg)
    out, _ = tracker.update(merge_polls([poll], now, cfg.max_position_age_seconds), now)
    by = {a.hex: a for a in out}
    # no-position Mode S target, far-away jet (260 nm) and stale position are gone
    assert set(by) == {"a5e2f1", "a0c7de", "a9ed37", "ab6f0c"}
    assert by["a5e2f1"].flight == "AS1121"
    assert by["a5e2f1"].registration == ""                # a bare receiver knows no tail numbers
    assert by["ab6f0c"].on_ground
    assert by["a9ed37"].label == ""                       # no callsign, no registration: blank
    assert all(a.source == "local" for a in out)


def test_local_wins_and_borrows_identity_from_the_network(served, local_json, cfg):
    served["aircraft.json"] = local_json
    local = ReadsbSource("http://127.0.0.1:8080/data/aircraft.json").fetch()
    now = local.received_mono
    net = Poll("adsb_fi", [
        {"hex": "a5e2f1", "flight": "ASA1121 ", "r": "N479HA", "t": "B712",
         "ownOp": "HAWAIIAN AIRLINES INC", "alt_baro": 2500, "gs": 175.0, "track": 168.0,
         "baro_rate": -800, "lat": 19.8760, "lon": -156.0740, "seen_pos": 0.1},
        {"hex": "a9ed37", "flight": "N739HP  ", "r": "N739HP", "t": "C172", "alt_baro": 1400,
         "gs": 108.0, "track": 240.0, "lat": 19.8590, "lon": -155.9500, "seen_pos": 0.3},
        {"hex": "abcdef", "flight": "DAL480  ", "r": "N301DN", "t": "A321", "alt_baro": 11000,
         "gs": 280.0, "track": 200.0, "baro_rate": -1500, "lat": 20.2, "lon": -156.0,
         "seen_pos": 0.4},
    ], received_mono=now)
    merged = merge_polls([net, local], now, 30.0, preferred=("local",))
    tracker = make_tracker(cfg)
    out, _ = tracker.update(merged, now)
    by = {a.hex: a for a in out}
    assert len(out) == 5                                  # union of both, no duplicates
    assert by["a5e2f1"].source == "local" and by["a5e2f1"].lat == pytest.approx(19.8702)
    assert by["a5e2f1"].registration == "N479HA"          # identity filled in from adsb.fi
    assert by["a5e2f1"].airline == "Hawaiian Airlines"
    assert by["a9ed37"].label == "N739HP"
    assert by["abcdef"].source == "adsb_fi"               # only the network heard this one


def test_merged_view_is_at_least_any_single_feed(cfg):
    one = Poll("a", [{"hex": f"a0000{i}", "lat": 19.8 + i * 0.01, "lon": -156.1, "alt_baro": 3000,
                      "gs": 100.0, "seen_pos": 0.5} for i in range(4)], received_mono=100.0)
    two = Poll("b", [{"hex": f"a0000{i}", "lat": 19.8 + i * 0.01, "lon": -156.1, "alt_baro": 3000,
                      "gs": 100.0, "seen_pos": 0.9} for i in range(2, 7)], received_mono=100.0)
    merged = merge_polls([one, two], 100.0, 30.0)
    hexes = [r["hex"] for r in merged]
    assert len(hexes) == len(set(hexes)) == 7
    assert len(merged) >= max(len(one.aircraft), len(two.aircraft))


def test_local_receiver_polls_on_its_own_interval(served, local_json, cfg):
    import dataclasses
    served["aircraft.json"] = local_json
    cfg2 = dataclasses.replace(cfg, sources=("local",), local_interval_seconds=2.0)

    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t
    clock = Clock()
    loop = FetchLoop(cfg2, enricher=FakeEnricher(), lookups=lookups(), clock=clock)
    assert loop.preferred == ("local",)
    for _ in range(40):                                   # 20 seconds
        loop.tick()
        clock.t += 0.5
    assert 9 <= len(served["_calls"]) <= 11


def test_unexpected_payloads_are_fetch_errors(served):
    src = ReadsbSource("http://127.0.0.1:8080/data/aircraft.json")
    for bad in ({"ac": []}, [], "html error page", {"aircraft": "nope"}):
        served["aircraft.json"] = bad
        with pytest.raises(FetchError):
            src.fetch()
    served["aircraft.json"] = {"now": 1.0, "aircraft": [{"hex": "a"}, "junk", None]}
    assert src.fetch().aircraft == [{"hex": "a"}]


def test_shipped_config_merges_two_verified_feeds(cfg):
    assert cfg.sources == ("airplanes_live", "adsb_fi")
    assert "local" not in cfg.sources                     # hardware not bought yet
