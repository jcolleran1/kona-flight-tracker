"""Audit D: arriving / departing / overflight, and which flight is featured."""
import pytest

from enrich.motion import (FeaturedSelector, PhaseTracker, closing_speed_kt,
                           combine_with_route, commitment_holds, direction_of,
                           kinematic_phase, miss_distance_nm, movement_color_key)
from state import Aircraft
from tests.helpers import FakeEnricher, HNL, ITO, KOA, OAK, SFO, route, track_one


def kin(**kw):
    base = dict(on_ground=False, altitude_ft=3000, vertical_rate_fpm=0, ground_speed_kt=180,
                track_deg=180, distance_nm=10, bearing_from_home=0, field_elevation_ft=47)
    base.update(kw)
    return kinematic_phase(**base)


def test_closing_speed_and_miss_distance():
    # due north of home, flying south = straight at it
    assert closing_speed_kt(200, 180, 0) == pytest.approx(200)
    assert closing_speed_kt(200, 0, 0) == pytest.approx(-200)
    assert closing_speed_kt(200, 90, 0) == pytest.approx(0, abs=1e-6)
    assert closing_speed_kt(None, 90, 0) is None
    assert miss_distance_nm(180, 0, 10) == pytest.approx(0, abs=1e-6)
    assert miss_distance_nm(150, 0, 10) == pytest.approx(5.0)
    assert miss_distance_nm(0, 0, 10) == 10       # already heading away


def test_descending_toward_the_field_is_arriving():
    assert kin(altitude_ft=2500, vertical_rate_fpm=-800, distance_nm=8) == "arriving"
    assert kin(altitude_ft=11500, vertical_rate_fpm=-1150, distance_nm=39, track_deg=175) == "arriving"


def test_fl360_overflight_pointing_at_kona_is_not_an_arrival():
    assert kin(altitude_ft=36000, vertical_rate_fpm=0, distance_nm=20, ground_speed_kt=460) == "overflight"
    # even while descending slowly: it cannot possibly get down in time
    assert kin(altitude_ft=36000, vertical_rate_fpm=-500, distance_nm=20) == "overflight"


def test_descending_past_kona_to_hilo_is_not_an_arrival():
    # 30 nm NNW of Kona, tracking ESE: the course misses Kona by ~25 nm
    assert kin(altitude_ft=14000, vertical_rate_fpm=-960, distance_nm=30,
               bearing_from_home=343, track_deg=105, ground_speed_kt=294) == "overflight"
    assert kin(altitude_ft=7000, vertical_rate_fpm=-960, distance_nm=33,
               bearing_from_home=60, track_deg=120) == "local"


def test_climbing_away_is_departing():
    assert kin(altitude_ft=2300, vertical_rate_fpm=2400, distance_nm=4,
               bearing_from_home=190, track_deg=187) == "departing"
    assert kin(altitude_ft=13500, vertical_rate_fpm=2100, distance_nm=18,
               bearing_from_home=305, track_deg=320, ground_speed_kt=380) == "departing"


def test_climbing_traffic_from_somewhere_else_is_not_a_kona_departure():
    # 20 nm NE (near Waimea), climbing northwest: not on a radial from Kona
    assert kin(altitude_ft=6000, vertical_rate_fpm=1200, distance_nm=20,
               bearing_from_home=45, track_deg=315) == "local"
    # far too high for its distance to have just left Kona
    assert kin(altitude_ft=16000, vertical_rate_fpm=1500, distance_nm=10,
               bearing_from_home=270, track_deg=270) == "overflight"


def test_level_traffic_is_local_or_overflight_by_altitude():
    assert kin(altitude_ft=1500, track_deg=20, bearing_from_home=40, distance_nm=15) == "local"
    assert kin(altitude_ft=24000, track_deg=84, bearing_from_home=200, distance_nm=35) == "overflight"
    assert kin(altitude_ft=None, vertical_rate_fpm=None) == "local"
    assert kin(on_ground=True) == "ground"


def test_low_and_close_and_inbound_counts_as_arriving_even_when_level():
    assert kin(altitude_ft=1300, vertical_rate_fpm=0, distance_nm=4, ground_speed_kt=110) == "arriving"
    assert kin(altitude_ft=1300, vertical_rate_fpm=0, distance_nm=4, track_deg=0) == "local"


def test_route_can_promote_but_never_overrule_motion():
    assert combine_with_route("overflight", origin_is_home=False, dest_is_home=True) == "arriving"
    assert combine_with_route("local", origin_is_home=True, dest_is_home=False) == "departing"
    assert combine_with_route("overflight", origin_is_home=False, dest_is_home=False) == "overflight"
    assert combine_with_route("departing", origin_is_home=False, dest_is_home=True) == "departing"
    assert combine_with_route("ground", origin_is_home=False, dest_is_home=True) == "ground"


def test_direction_has_a_dead_band():
    assert direction_of(100) == "toward" and direction_of(-100) == "away"
    assert direction_of(5, "away") == "away" and direction_of(-5, "toward") == "toward"
    assert direction_of(None, "away") == "away"


def test_three_colours_only():
    keys = {movement_color_key(p, d) for p in ("ground", "arriving", "departing", "overflight", "local")
            for d in ("toward", "away")}
    assert keys == {"ground", "toward", "away"}
    assert movement_color_key("overflight", "away") == "away"
    assert movement_color_key("arriving", "away") == "toward"


# ---- hysteresis ----

def test_arrival_needs_confirmation_then_sticks_through_a_level_off():
    pt = PhaseTracker(confirm_seconds=12)
    assert pt.update("arriving", 0.0) == "local"          # one sample proves nothing
    assert pt.update("arriving", 5.0) == "local"
    assert pt.update("arriving", 12.0) == "arriving"
    # levels off on a dogleg: still closing, still low enough -> holds
    for t in range(15, 300, 5):
        assert pt.update("local", float(t), holds=True) == "arriving"
    # turns away for real
    assert pt.update("local", 300.0, holds=False) == "arriving"
    assert pt.update("local", 313.0, holds=False) == "local"


def test_one_noisy_sample_does_not_flip_the_phase():
    pt = PhaseTracker(confirm_seconds=12)
    pt.update("overflight", 0.0, neutral="overflight")
    assert pt.update("arriving", 5.0, neutral="overflight") == "overflight"
    assert pt.update("overflight", 10.0, neutral="overflight") == "overflight"
    assert pt.update("arriving", 15.0, neutral="overflight") == "overflight"   # timer restarted
    assert pt.update("overflight", 26.0, neutral="overflight") == "overflight"


def test_liftoff_from_home_is_a_departure_immediately_and_survives_the_roll():
    pt = PhaseTracker()
    assert pt.update("ground", 0.0, near_home=True) == "ground"
    # the takeoff roll looks like a low inbound pass for a few seconds
    assert pt.update("arriving", 5.0, near_home=True) == "departing"
    for t in range(10, 120, 5):
        assert pt.update("arriving", float(t)) == "departing"
    assert pt.update("departing", 200.0) == "departing"


def test_liftoff_somewhere_else_is_not_a_kona_departure():
    pt = PhaseTracker()
    pt.update("ground", 0.0, near_home=False)
    assert pt.update("local", 5.0) == "local"


def test_touchdown_is_immediate():
    pt = PhaseTracker()
    pt.update("arriving", 0.0)
    pt.update("arriving", 20.0)
    assert pt.update("ground", 25.0, near_home=True) == "ground"


def test_commitment_holds():
    common = dict(distance_nm=15, field_elevation_ft=47)
    assert commitment_holds("arriving", altitude_ft=5000, vertical_rate_fpm=0, closing_kt=120, **common)
    assert not commitment_holds("arriving", altitude_ft=5000, vertical_rate_fpm=0, closing_kt=-120, **common)
    assert not commitment_holds("arriving", altitude_ft=5000, vertical_rate_fpm=1500, closing_kt=120, **common)
    assert not commitment_holds("arriving", altitude_ft=30000, vertical_rate_fpm=0, closing_kt=120, **common)
    assert commitment_holds("departing", altitude_ft=9000, vertical_rate_fpm=100, closing_kt=-200, **common)
    assert not commitment_holds("departing", altitude_ft=9000, vertical_rate_fpm=100, closing_kt=200, **common)
    assert not commitment_holds("local", altitude_ft=1, vertical_rate_fpm=0, closing_kt=1, **common)


# ---- featured flight ----

def plane(hex_id, phase, dist, ground=False):
    return Aircraft(hex=hex_id, phase=phase, distance_nm=dist, on_ground=ground)


def test_featured_prefers_a_real_arrival_over_a_nearer_overflight():
    sel = FeaturedSelector()
    planes = [plane("over", "overflight", 5), plane("arr", "arriving", 12),
              plane("gnd", "ground", 0.3, ground=True)]
    assert sel.select(planes, 0.0) == "arr"


def test_featured_falls_back_to_nearest_airborne_then_ground():
    sel = FeaturedSelector()
    assert sel.select([plane("a", "overflight", 30), plane("b", "local", 9),
                       plane("g", "ground", 0.2, ground=True)], 0.0) == "b"
    assert FeaturedSelector().select([plane("g1", "ground", 0.5, True),
                                      plane("g2", "ground", 0.2, True)], 0.0) == "g2"
    assert FeaturedSelector().select([], 0.0) is None


def test_featured_does_not_flap_between_two_similar_flights():
    sel = FeaturedSelector(dwell_seconds=20)
    a, b = plane("a", "arriving", 10.0), plane("b", "arriving", 10.4)
    assert sel.select([a, b], 0.0) == "a"
    a.distance_nm, b.distance_nm = 10.2, 9.9          # b edges ahead
    assert sel.select([a, b], 5.0) == "a"
    b.distance_nm = 6.0                                # clearly closer, but dwell not over
    assert sel.select([a, b], 10.0) == "a"
    assert sel.select([a, b], 25.0) == "b"            # dwell over


def test_featured_switches_at_once_for_a_better_tier_or_when_it_leaves():
    sel = FeaturedSelector(dwell_seconds=20)
    assert sel.select([plane("x", "local", 5)], 0.0) == "x"
    assert sel.select([plane("x", "local", 5), plane("arr", "arriving", 30)], 1.0) == "arr"
    assert sel.select([plane("x", "local", 5)], 2.0) == "x"


# ---- through the tracker, on fixtures ----

def test_fixture_arrival_and_departure(nasty, cfg):
    assert track_one(cfg, nasty["a5000a"], repeats=4).phase == "arriving"   # 2,400 ft, 8 nm N, -832
    assert track_one(cfg, nasty["a50009"], repeats=4).phase == "departing"  # 2,200 ft, 4 nm S, +2432


def test_fixture_overflight_stays_an_overflight(nasty, cfg):
    ac = track_one(cfg, nasty["7c1472"], repeats=6)
    assert ac.phase == "overflight" and ac.direction == "toward"


def test_route_to_kona_makes_a_distant_descent_an_arrival(nasty, cfg):
    # 40 nm out at 9,200 ft on a course that misses by more than the kinematic test allows
    raw = dict(nasty["a50007"], track=95.0)
    assert track_one(cfg, raw, repeats=4).phase == "local"
    rt = FakeEnricher(routes={"ASA1121": route(HNL, KOA)})
    ac = track_one(cfg, raw, enricher=rt, repeats=4)
    assert ac.phase == "arriving" and (ac.origin_code, ac.dest_code) == ("HNL", "KOA")
