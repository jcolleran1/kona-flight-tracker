"""Phase 3: the renderer, headless. Layout must hold at 0, 1, 5 and 15
aircraft, nothing may leave its panel, and an unchanged scene must cost
nothing to draw."""
import dataclasses
import sys
from pathlib import Path

import pygame
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import preview  # noqa: E402
from render.panel import (LeftPanel, altitude_text, fallback_route_text, fit_words,
                          heading_words, table_order)
from render.renderer import Renderer, live_aircraft, pick_featured
from state import Aircraft, SharedState, Snapshot, SourceHealth

SCENES = [(0, 300), (1, 300), (5, 300), (15, 460), (None, 135), (None, 670), (None, 899)]


@pytest.fixture(scope="module")
def replay_cfg(cfg):
    return dataclasses.replace(cfg, sources=("replay",), fullscreen=False)


@pytest.fixture(scope="module")
def renderer(replay_cfg):
    r = Renderer(replay_cfg, SharedState())
    r.init_display()
    yield r
    pygame.display.quit()


@pytest.fixture(scope="module")
def scenes(replay_cfg):
    return {(count, at): preview.build_snapshot(replay_cfg, at, count, warmup=120)
            for count, at in SCENES}


def text_rects(renderer, ops):
    t = renderer.theme
    out = []
    for op in ops:
        if op[0] != "t":
            continue
        _, text, font, _color, x, y, align = op
        w, h = getattr(t, font).size(text)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w // 2
        out.append((text, pygame.Rect(x, y, w, h)))
    return out


def left_ops(renderer, snap, now, reconnecting=False):
    aircraft = live_aircraft(snap, now, renderer.cfg.max_position_age_seconds)
    return renderer.left.build(aircraft, pick_featured(snap, aircraft), snap, now,
                               date_text="MON NOV 16", clock_text="16:38",
                               reconnecting=reconnecting)


@pytest.mark.parametrize("count,at", SCENES)
def test_frames_render(renderer, scenes, count, at):
    snap, now = scenes[(count, at)]
    dirty = renderer.draw_frame(snap, False, now + 1.0, force=True)
    assert len(dirty) == 2 and renderer.screen.get_size() == (1920, 1080)
    renderer.draw_frame(snap, True, now + 3.0)                 # reconnecting
    renderer.draw_frame(snap, False, now + 3.0, brightness=0.3)  # quiet hours


@pytest.mark.parametrize("count,at", SCENES)
def test_left_panel_text_stays_on_the_panel_and_never_overlaps(renderer, scenes, count, at):
    snap, now = scenes[(count, at)]
    rects = text_rects(renderer, left_ops(renderer, snap, now))
    panel = pygame.Rect(0, 0, renderer.panel_w, 1080)
    for text, r in rects:
        assert panel.contains(r), f"{text!r} leaves the panel: {r}"
    for i, (ta, ra) in enumerate(rects):
        for tb, rb in rects[i + 1:]:
            # glyph boxes carry a few px of built-in leading; shrink before testing
            assert not ra.inflate(-2, -10).colliderect(rb.inflate(-2, -10)), \
                f"{ta!r} overlaps {tb!r}"


def test_long_names_are_fitted_not_spilled(renderer, scenes):
    snap, now = scenes[(15, 460)]
    long = [dataclasses.replace(a, airline="Extraordinarily Long Airline Name Holdings Of The Pacific",
                                type_name="Experimental Amateur-Built Flying Machine Mark Seven",
                                flight="ABCDEFGH", callsign="ABCDEFGH", registration="N123456789",
                                origin_name="San Francisco International", dest_name="Kailua-Kona Keahole")
            for a in snap.aircraft]
    snap2 = dataclasses.replace(snap, aircraft=tuple(long))
    rects = text_rects(renderer, left_ops(renderer, snap2, now))
    panel = pygame.Rect(0, 0, renderer.panel_w, 1080)
    assert all(panel.contains(r) for _, r in rects)
    for i, (ta, ra) in enumerate(rects):
        for tb, rb in rects[i + 1:]:
            assert not ra.inflate(-2, -10).colliderect(rb.inflate(-2, -10)), f"{ta!r} / {tb!r}"


def test_map_never_draws_on_the_info_panel(renderer, scenes):
    """Range rings used to sweep across the left panel."""
    snap, now = scenes[(15, 460)]
    renderer.draw_frame(snap, False, now, force=True)
    before = renderer.screen.subsurface((0, 0, renderer.panel_w, 1080)).copy()
    aircraft = live_aircraft(snap, now, 30)
    # put a labelled aircraft right on the seam and paint the map again
    edge = dataclasses.replace(aircraft[0], lat=19.74, lon=renderer.cfg.map_lon_min + 0.001,
                               pos_mono=now, trail=((19.74, -157.5), (19.74, -156.6)))
    draw_list = renderer.mapview.build([edge] + aircraft[1:], edge.hex, now)
    renderer.mapview.paint(renderer.screen, draw_list)
    after = renderer.screen.subsurface((0, 0, renderer.panel_w, 1080)).copy()
    assert before.get_buffer().raw == after.get_buffer().raw


def test_map_labels_stay_inside_the_map_panel(renderer, scenes):
    snap, now = scenes[(15, 460)]
    aircraft = live_aircraft(snap, now, 30)
    draw_list = renderer.mapview.build(aircraft, snap.featured_hex, now)
    labels = [op for op in draw_list if op[0] == "label"]
    assert len(labels) >= 6
    rects = [pygame.Rect(op[4], op[5], op[6], op[7]) for op in labels]
    assert all(renderer.mapview.rect.contains(r) for r in rects)
    for i, a in enumerate(rects):
        assert not any(a.colliderect(b) for b in rects[i + 1:])


# ---- cost: an unchanged scene must not be repainted ----

def test_unchanged_scene_repaints_nothing(renderer, scenes):
    snap, now = scenes[(0, 300)]
    assert renderer.draw_frame(snap, False, now, force=True)
    for dt in (0.17, 0.33, 0.5, 5.0):
        assert renderer.draw_frame(snap, False, now + dt) == []


def test_parked_aircraft_do_not_cause_repaints(renderer, replay_cfg):
    parked = Aircraft(hex="abc001", flight="WN1297", lat=19.7362, lon=-156.0403, on_ground=True,
                      phase="ground", ground_speed_kt=0.0, pos_mono=1000.0, distance_nm=0.3)
    snap = Snapshot(aircraft=(parked,), featured_hex="abc001", fetched_at=1000.0, ok=True, seq=1)
    assert renderer.draw_frame(snap, False, 1000.0, force=True)
    assert renderer.draw_frame(snap, False, 1002.0) == []
    assert renderer.draw_frame(snap, False, 1004.0) == []


def test_moving_aircraft_repaint_only_the_map(renderer):
    jet = Aircraft(hex="abc002", flight="UA1158", lat=19.9, lon=-156.2, ground_speed_kt=420.0,
                   track_deg=45.0, altitude_ft=12000.0, phase="departing", direction="away",
                   pos_mono=1000.0, distance_nm=13.0, bearing_deg=318.0)
    snap = Snapshot(aircraft=(jet,), featured_hex="abc002", fetched_at=1000.0, ok=True, seq=2)
    renderer.draw_frame(snap, False, 1000.0, force=True)
    assert renderer.draw_frame(snap, False, 1000.05) == []        # under a pixel of movement
    dirty = renderer.draw_frame(snap, False, 1004.0)              # ~0.5 nm = 5 px later
    assert dirty == [renderer.mapview.rect]


def test_left_panel_surface_is_cached_by_content(renderer, scenes):
    snap, now = scenes[(5, 300)]
    ops = left_ops(renderer, snap, now)
    renderer.left.render(ops)
    assert renderer.left.render(left_ops(renderer, snap, now + 0.4)) is False
    assert renderer.left.render(ops, brightness=0.3) is True       # dimming is part of the key
    assert renderer.left.render(ops, brightness=0.3) is False


def test_quiet_hours_dim_is_baked_into_the_layers(renderer, scenes):
    snap, now = scenes[(15, 460)]
    renderer.draw_frame(snap, False, now, force=True)
    bright = pygame.transform.average_color(renderer.screen)
    renderer.draw_frame(snap, False, now, brightness=0.3)
    dim = pygame.transform.average_color(renderer.screen)
    assert sum(dim[:3]) < sum(bright[:3]) * 0.45
    renderer.draw_frame(snap, False, now, brightness=1.0)
    assert pygame.transform.average_color(renderer.screen) == bright


def test_text_cache_is_a_bounded_lru(renderer):
    t = renderer.theme
    for i in range(1000):
        t.text(f"FLIGHT{i}", t.row, (255, 255, 255))
    assert len(t._cache) <= 384
    hot = t.text("FLIGHT999", t.row, (255, 255, 255))
    assert t.text("FLIGHT999", t.row, (255, 255, 255)) is hot


# ---- content rules ----

def test_no_ghosts_and_no_false_quiet_skies(renderer, scenes):
    snap, now = scenes[(15, 460)]
    later = now + 120.0                         # two minutes with no data
    assert live_aircraft(snap, later, 30) == []
    texts = [op[1] for op in left_ops(renderer, snap, later, reconnecting=True) if op[0] == "t"]
    assert "NO FLIGHT DATA" in texts and "QUIET SKIES" not in texts
    assert "RECONNECTING" in texts
    # healthy and genuinely empty is the only time it says the sky is quiet
    empty, t0 = scenes[(0, 300)]
    texts = [op[1] for op in left_ops(renderer, empty, t0) if op[0] == "t"]
    assert "QUIET SKIES" in texts and "NO FLIGHT DATA" not in texts


def test_featured_falls_back_when_the_pick_has_aged_out():
    old = Aircraft(hex="old", pos_mono=0.0, distance_nm=1.0)
    fresh_far = Aircraft(hex="far", pos_mono=100.0, distance_nm=30.0)
    fresh_gnd = Aircraft(hex="gnd", pos_mono=100.0, distance_nm=0.2, on_ground=True)
    snap = Snapshot(aircraft=(fresh_gnd, old, fresh_far), featured_hex="old")
    live = live_aircraft(snap, 100.0, 30.0)
    assert pick_featured(snap, live).hex == "far"            # airborne before parked
    assert pick_featured(snap, []) is None


def test_hero_shows_iata_flight_route_and_units(renderer, scenes):
    snap, now = scenes[(15, 460)]
    texts = [op[1] for op in left_ops(renderer, snap, now) if op[0] == "t"]
    assert "AS1121" in texts and "ASA1121" in texts            # IATA primary, ICAO secondary
    assert "HAWAIIAN AIRLINES" in texts and "HNL" in texts and "KOA" in texts
    assert "HONOLULU" in texts and "KAILUA-KONA" in texts
    assert "FROM KOA" in texts and "ARRIVING" in texts
    assert any(t.endswith(" kt") for t in texts) and any(" mi " in t for t in texts)
    assert any(t.endswith("↓") for t in texts)            # descending arrow
    assert not any("mph" in t for t in texts)
    assert not any(t.startswith("-") or " -" in t for t in texts)     # no negative numbers


def test_rejected_route_shows_direction_text_instead(renderer, scenes):
    snap, now = scenes[(15, 460)]
    hilo = next(a for a in snap.aircraft if a.flight == "AS1071")
    assert hilo.origin_code == ""
    one = dataclasses.replace(snap, aircraft=(hilo,), featured_hex=hilo.hex)
    texts = [op[1] for op in left_ops(renderer, one, now) if op[0] == "t"]
    assert any(t.startswith("HEADING ") for t in texts)
    assert "SEA" not in texts and "SFO" not in texts


def test_table_order_is_stable_and_puts_kona_traffic_first():
    def ac(h, phase, first, dist):
        return Aircraft(hex=h, phase=phase, first_seen_mono=first, distance_nm=dist)
    planes = [ac("o", "overflight", 1, 5), ac("g", "ground", 2, 0.2), ac("a2", "arriving", 9, 3),
              ac("l", "local", 3, 8), ac("d", "departing", 4, 20), ac("a1", "arriving", 5, 30),
              ac("f", "arriving", 0, 1)]
    order = [a.hex for a in table_order(planes, "f")]
    assert order == ["a1", "a2", "d", "l", "o", "g"]
    # distances change every poll; the order must not
    for p in planes:
        p.distance_nm = 50 - p.distance_nm
    assert [a.hex for a in table_order(planes, "f")] == order


def test_more_aircraft_than_rows_says_how_many_are_hidden(renderer, scenes):
    snap, now = scenes[(15, 460)]
    texts = [op[1] for op in left_ops(renderer, snap, now) if op[0] == "t"]
    live = len(live_aircraft(snap, now, 30))
    assert f"+ {live - 1 - renderer.cfg.max_rows} MORE" in texts


def test_status_strip_names_each_source(renderer):
    snap = Snapshot(aircraft=(), fetched_at=1000.0, ok=True, sources=(
        SourceHealth("airplanes_live", True, 12, 1000.0), SourceHealth("adsb_fi", False, 0, 901.0, "HTTP 429"),
        SourceHealth("adsb_lol", False, 0, None, "timed out")))
    ops = renderer.left.build([], None, snap, 1001.0, date_text="MON NOV 16", clock_text="16:38",
                              reconnecting=False)
    texts = [op[1] for op in ops if op[0] == "t"]
    assert "AIRPLANES.LIVE 12" in texts and "ADSB.FI" in texts and "ADSB.LOL" in texts
    assert "MON NOV 16   16:38" in texts
    dots = [op[1] for op in ops if op[0] == "c"]
    assert len(set(dots)) == 3                       # healthy, stale, failing: three colours


def test_source_dot_goes_stale_even_if_the_fetch_thread_is_frozen(renderer):
    from render.theme import OK
    snap = Snapshot(aircraft=(), fetched_at=1000.0, ok=True,
                    sources=(SourceHealth("adsb_fi", True, 9, 1000.0),))

    def dot(now):
        ops = renderer.left.build([], None, snap, now, date_text="", clock_text="",
                                  reconnecting=False)
        return next(op[1] for op in ops if op[0] == "c")
    assert dot(1002.0) == OK
    assert dot(1200.0) != OK          # same snapshot, three minutes later


def test_clock_is_blank_until_the_system_clock_is_set(renderer, monkeypatch):
    import render.renderer as rr
    monkeypatch.setattr(rr, "clock_is_set", lambda: False)
    assert renderer._clock_texts() == ("", "")
    ops = renderer.left.build([], None, Snapshot(ok=True, fetched_at=1.0), 2.0, date_text="",
                              clock_text="", reconnecting=False)
    assert not any(op[0] == "t" and ":" in op[1] for op in ops)


def test_small_text_helpers(renderer, cfg):
    t = renderer.theme
    assert fit_words(t, "BLUE HAWAIIAN HELICOPTERS", t.row, 15 * t.char_width(t.row)) == "BLUE HAWAIIAN"
    assert fit_words(t, "SUPERCALIFRAGILISTIC", t.row, 5 * t.char_width(t.row)) == "SUPER"
    assert heading_words(0) == "NORTH" and heading_words(225) == "SOUTHWEST"
    assert heading_words(359) == "NORTH" and heading_words(None) == ""
    climbing = Aircraft(hex="x", altitude_ft=8200.0, vertical_rate_fpm=3200.0, phase="departing")
    assert altitude_text(climbing, cfg) == "8,200 ft ↑"
    assert altitude_text(dataclasses.replace(climbing, altitude_ft=40.0), cfg) == "TAKEOFF"
    assert altitude_text(Aircraft(hex="y", on_ground=True), cfg) == "GROUND"
    assert fallback_route_text(Aircraft(hex="z", phase="arriving"), cfg) == "INBOUND TO KONA"
    assert fallback_route_text(Aircraft(hex="z", phase="ground", distance_nm=0.4), cfg) == "AT KONA AIRPORT"
    assert fallback_route_text(Aircraft(hex="z", phase="ground", distance_nm=40), cfg) == ""
    assert fallback_route_text(Aircraft(hex="z", phase="local", track_deg=90.0), cfg) == "HEADING EAST"


# ---- map ----

def test_overflights_are_smaller_and_dimmer_in_the_same_colours(renderer, scenes):
    from render.mapview import plane_color
    from render.theme import CORAL, SAND, TEAL
    assert plane_color(Aircraft(hex="a", phase="arriving")) == TEAL
    assert plane_color(Aircraft(hex="a", phase="departing")) == CORAL
    assert plane_color(Aircraft(hex="a", phase="ground")) == SAND
    assert plane_color(Aircraft(hex="a", phase="local", direction="away")) == CORAL
    dim = plane_color(Aircraft(hex="a", phase="overflight", direction="toward"))
    assert dim != TEAL and all(d < c for d, c in zip(dim, TEAL))
    assert max(dim) / max(TEAL) == pytest.approx(dim[0] / TEAL[0], abs=0.02)   # same hue
    snap, now = scenes[(15, 460)]
    draw_list = renderer.mapview.build(live_aircraft(snap, now, 30), snap.featured_hex, now)
    assert any(op[0] == "icon" and op[4] for op in draw_list)          # some small icons
    assert sum(1 for op in draw_list if op[0] == "ring") == 1          # exactly one featured ring
    assert any(op[0] == "trail" for op in draw_list)


def test_off_map_aircraft_get_an_edge_pointer(renderer):
    north = Aircraft(hex="n1", flight="DL480", lat=21.4, lon=-156.0, altitude_ft=20000.0,
                     ground_speed_kt=400.0, track_deg=180.0, pos_mono=500.0, distance_nm=58.0)
    draw_list = renderer.mapview.build([north], None, 500.0)
    ptr = [op for op in draw_list if op[0] == "icon" and op[1] == "pointer"]
    assert len(ptr) == 1
    _, _, _, bucket, _, x, y = ptr[0]
    assert renderer.mapview.rect.collidepoint(x, y) and y < 40          # on the top edge
    assert bucket >= 340 or bucket <= 5                                  # pointing up and out
    assert not any(op[0] == "label" for op in draw_list)


def test_aircraft_parked_at_kona_are_drawn_small_and_unlabelled(renderer):
    gate = [Aircraft(hex=f"g{i}", flight=f"WN{i}", lat=19.7362 + i * 0.001, lon=-156.0403,
                     on_ground=True, phase="ground", pos_mono=10.0, distance_nm=0.3)
            for i in range(4)]
    arrival = Aircraft(hex="arr", flight="AS1121", lat=19.9, lon=-156.1, altitude_ft=3000.0,
                       ground_speed_kt=200.0, track_deg=170.0, phase="arriving", pos_mono=10.0,
                       distance_nm=10.0)
    draw_list = renderer.mapview.build(gate + [arrival], "arr", 10.0)
    assert [op[1] for op in draw_list if op[0] == "label"] == ["AS1121"]
    assert sum(1 for op in draw_list if op[0] == "icon" and op[4]) == 4


def test_icon_shapes_follow_the_emitter_category():
    from render.icons import icon_kind
    assert icon_kind("A7") == "heli" and icon_kind("A1") == "prop" and icon_kind("B1") == "prop"
    assert icon_kind("A3") == "jet" and icon_kind("A5") == "jet" and icon_kind("") == "jet"


def test_icon_shapes_are_on_by_default_and_can_be_switched_off(replay_cfg, renderer):
    from render.mapview import MapView
    assert replay_cfg.icon_shapes is True
    fleet = [
        Aircraft(hex="h1", flight="N130BH", category="A7", lat=19.9, lon=-155.9, altitude_ft=1500.0,
                 ground_speed_kt=100.0, track_deg=20.0, pos_mono=10.0, distance_nm=12.0),
        Aircraft(hex="p1", flight="N739HP", category="A1", lat=19.8, lon=-156.2, altitude_ft=1400.0,
                 ground_speed_kt=110.0, track_deg=240.0, pos_mono=10.0, distance_nm=10.0),
        Aircraft(hex="j1", flight="AS1121", category="A3", lat=20.0, lon=-156.3, altitude_ft=6000.0,
                 ground_speed_kt=250.0, track_deg=130.0, pos_mono=10.0, distance_nm=21.0),
    ]

    def kinds(view):
        return sorted(op[1] for op in view.build(fleet, None, 10.0) if op[0] == "icon")
    assert kinds(renderer.mapview) == ["heli", "jet", "prop"]
    renderer.mapview.paint(renderer.screen, renderer.mapview.build(fleet, None, 10.0))
    plain = MapView(dataclasses.replace(replay_cfg, icon_shapes=False), 1920, 1080, renderer.theme)
    assert kinds(plain) == ["jet", "jet", "jet"]
    # shape is information only: the three movement colours are unchanged
    colours = {op[2] for op in renderer.mapview.build(fleet, None, 10.0) if op[0] == "icon"}
    assert colours == {op[2] for op in plain.build(fleet, None, 10.0) if op[0] == "icon"}


def test_shipped_map_is_true_shape_and_matches_the_panel(cfg):
    from render.projection import Projection
    p = Projection(cfg.map_lat_min, cfg.map_lat_max, cfg.map_lon_min, cfg.map_lon_max,
                   960, 0, 960, 1080)
    assert p.stretch == pytest.approx(1.0, abs=0.005)        # rings are circles
    assert p.px_per_nm_y == pytest.approx(10.0, abs=0.01)
    img = pygame.image.load(str(ROOT / "data" / cfg.map_image))
    assert img.get_size() == (960, 1080)
    # KOA is on the map with open ocean to its west
    x, _ = p.to_xy(cfg.lat, cfg.lon)
    assert 960 + 200 < x < 960 + 400


def test_splash_then_board(renderer, scenes):
    renderer._showing = ""
    assert renderer.draw_splash() and renderer.draw_splash() == []
    snap, now = scenes[(1, 300)]
    assert len(renderer.draw_frame(snap, False, now)) == 2    # full repaint after the splash
