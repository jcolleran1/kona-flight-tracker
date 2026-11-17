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


# ---- renderer smoke ----

@pytest.fixture(scope="module")
def renderer(replay_cfg):
    import pygame
    from render.renderer import Renderer
    from state import SharedState
    r = Renderer(replay_cfg, SharedState())
    r.init_display()
    yield r
    pygame.display.quit()


@pytest.mark.parametrize("count,at", [(0, 300), (1, 300), (5, 300), (15, 460), (None, 135),
                                      (None, 670), (None, 899)])
def test_frames_render_headless(replay_cfg, renderer, count, at):
    snap, now = preview.build_snapshot(replay_cfg, at, count, warmup=60)
    renderer.draw_frame(snap, False, now + 1.0)
    renderer.draw_frame(snap, True, now + 3.0)       # "reconnecting" state
    assert renderer.screen.get_size() == (1920, 1080)


def test_nothing_is_drawn_outside_the_map_panel(replay_cfg, renderer):
    """Range rings used to sweep across the info panel."""
    snap, now = preview.build_snapshot(replay_cfg, 460, 0, warmup=10)
    renderer.draw_frame(snap, False, now)
    area = (0, 0, 955, 1000)                         # info panel, above the ticking clock
    left = renderer.screen.subsurface(area).copy()
    renderer.screen.blit(renderer._bg, (0, 0))
    renderer.draw_left([], None)
    assert left.get_buffer().raw == renderer.screen.subsurface(area).copy().get_buffer().raw


def test_frame_with_stale_snapshot_shows_no_aircraft(replay_cfg, renderer):
    from render.renderer import live_aircraft
    snap, now = preview.build_snapshot(replay_cfg, 460, None, warmup=60)
    assert len(live_aircraft(snap, now, 30)) >= 10
    renderer.draw_frame(snap, True, now + 120.0)     # two minutes with no data
    assert live_aircraft(snap, now + 120.0, 30) == []


def test_splash_renders(renderer):
    renderer.draw_splash()
