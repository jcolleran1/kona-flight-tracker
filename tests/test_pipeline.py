"""The bundled scenario through the real fetch -> merge -> enrich pipeline,
then through the renderer, headless. This is the regression net for
"what would the wall show"."""
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import preview  # noqa: E402


@pytest.fixture(scope="module")
def replay_cfg(cfg):
    return dataclasses.replace(cfg, sources=("replay",), fullscreen=False)


@pytest.fixture(scope="module")
def timeline(replay_cfg):
    """{t: {flight-or-hex: Aircraft}} sampled through the whole scenario."""
    from fetch.worker import FetchLoop
    clock = preview.SimClock()
    loop = FetchLoop(replay_cfg, sources=[preview.LimitedReplay(clock, None)], clock=clock)
    out = {}
    for t in range(0, 900, 5):
        clock.t = float(t)
        for st in loop.sources:
            st.next_due = clock.t
        loop.tick()
        out[t] = ({(a.flight or a.hex): a for a in loop.aircraft}, loop.featured_hex)
    return out


def test_nothing_negative_and_nothing_forbidden_ever_appears(timeline):
    for t, (planes, _) in timeline.items():
        for name, a in planes.items():
            assert a.altitude_ft is None or a.altitude_ft >= 0, (t, name)
            assert not a.hex.startswith("~"), (t, name)
            assert name not in ("OPS12", "N818PH", "N9999X"), (t, name)   # vehicle, stale, no position
            assert a.category[:1] != "C"


def test_arrival_is_followed_all_the_way_to_the_gate(timeline):
    phases = [timeline[t][0]["AS1121"].phase for t in range(0, 900, 5)]
    assert phases[0] == "overflight"          # one sample, not yet believed
    assert set(phases[3:130]) == {"arriving"}
    assert phases[-1] == "ground"
    ac = timeline[400][0]["AS1121"]
    assert (ac.airline, ac.origin_code, ac.dest_code) == ("Hawaiian Airlines", "HNL", "KOA")
    # rolling out at 100+ kt with a negative pressure altitude: LANDING, not "-75 ft"
    from render.units import fmt_altitude
    rollout = timeline[670][0]["AS1121"]
    assert not rollout.on_ground and rollout.altitude_ft < 100
    assert fmt_altitude(rollout.altitude_ft, False, phase=rollout.phase) == "LANDING"


def test_departure_is_a_departure_from_the_takeoff_roll_onward(timeline):
    assert timeline[90][0]["UA1158"].phase == "ground"
    seen = 0
    for t in range(110, 900, 5):
        ac = timeline[t][0].get("UA1158")
        if ac is None:                 # climbed out past the fetch radius
            continue
        seen += 1
        assert ac.phase == "departing", t
        assert (ac.origin_code, ac.dest_code) == ("KOA", "SFO"), t
    assert seen > 100


def test_overflight_never_becomes_an_arrival_or_the_featured_flight(timeline):
    qantas_hex = "7c1472"
    seen = 0
    for t, (planes, featured) in timeline.items():
        assert featured != qantas_hex, t
        if "QF12" in planes:
            seen += 1
            assert planes["QF12"].phase == "overflight", t
    assert seen > 100


def test_hilo_arrival_passes_by_with_no_route_and_the_right_airline(timeline):
    seen = 0
    for t in range(0, 900, 5):
        ac = timeline[t][0].get("AS1071")
        if ac is None:
            continue
        seen += 1
        assert ac.phase in ("overflight", "local"), t
        assert ac.origin_code == "" and ac.dest_code == "", t     # SEA>SFO rejected
        assert ac.airline == "Hawaiian Airlines"
    assert seen > 100


def test_featured_flight_is_steady(timeline):
    picks = [timeline[t][1] for t in range(0, 900, 5)]
    changes = sum(1 for a, b in zip(picks, picks[1:]) if a != b)
    assert changes <= 6
    assert all(p is not None for p in picks)


def test_type_fallback_and_missing_callsign(timeline):
    ac = timeline[100][0]["N522SA"]
    assert ac.callsign == "" and ac.type_code == "C208" and ac.airline == "Mokulele Airlines"
