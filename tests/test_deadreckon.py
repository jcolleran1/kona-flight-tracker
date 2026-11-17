"""Audit B: planes must glide between updates, not sit still and jump."""
import pytest

from enrich.deadreckon import Smoother, advance
from enrich.geo import bearing_deg, haversine_nm
from state import Aircraft


def test_straight_line_distance_and_direction():
    lat, lon = advance(19.7, -156.0, 360.0, 90.0, 10.0)        # 360 kt for 10 s = 1 nm east
    assert haversine_nm(19.7, -156.0, lat, lon) == pytest.approx(1.0, abs=0.005)
    assert bearing_deg(19.7, -156.0, lat, lon) == pytest.approx(90.0, abs=0.2)
    lat, lon = advance(19.7, -156.0, 180.0, 0.0, 20.0)         # 1 nm north
    assert lat == pytest.approx(19.7 + 1 / 60) and lon == pytest.approx(-156.0)


def test_no_movement_without_speed_track_or_time():
    here = (19.7, -156.0)
    assert advance(*here, None, 90.0, 10.0) == here
    assert advance(*here, 300.0, None, 10.0) == here
    assert advance(*here, 300.0, 90.0, 0.0) == here
    assert advance(*here, 300.0, 90.0, -5.0) == here
    assert advance(*here, 1.5, 90.0, 60.0) == here              # taxi-speed track is noise


def test_turning_path_curves():
    # standard-rate right turn (3 deg/s) from north for 30 s = 90 degrees
    straight = advance(19.7, -156.0, 180.0, 0.0, 30.0)
    curved = advance(19.7, -156.0, 180.0, 0.0, 30.0, track_rate=3.0)
    assert curved[1] > straight[1]                              # ended up east of straight ahead
    assert curved[0] < straight[0]
    # the arc is the same length as the straight path, the chord shorter
    assert haversine_nm(19.7, -156.0, *curved) < haversine_nm(19.7, -156.0, *straight)
    # an absurd turn rate is ignored rather than drawn
    assert advance(19.7, -156.0, 180.0, 0.0, 30.0, track_rate=45.0) == straight


def plane(pos_mono, lat=19.9, lon=-156.1, gs=240.0, track=180.0, **kw):
    return Aircraft(hex="abc123", lat=lat, lon=lon, ground_speed_kt=gs, track_deg=track,
                    pos_mono=pos_mono, **kw)


def test_position_advances_between_polls():
    sm = Smoother(tween_seconds=1.0)
    ac = plane(100.0)
    p0 = sm.position(ac, 100.0)
    p3 = sm.position(ac, 103.0)
    assert p0 == (19.9, -156.1)
    assert haversine_nm(*p0, *p3) == pytest.approx(240 * 3 / 3600, abs=0.002)   # 0.2 nm
    assert p3[0] < p0[0]                                        # heading south


def test_position_is_already_advanced_by_the_age_of_the_fix():
    sm = Smoother()
    lat, _ = sm.position(plane(100.0), 104.0)                   # first sight, fix is 4 s old
    assert lat == pytest.approx(19.9 - 240 * 4 / 3600 / 60, abs=1e-6)


def test_extrapolation_is_capped():
    sm = Smoother(max_extrapolate=30.0)
    ac = plane(100.0)
    assert sm.position(ac, 130.0) == sm.position(ac, 500.0)


def test_new_fix_is_blended_in_not_snapped():
    sm = Smoother(tween_seconds=1.0)
    old = plane(100.0)
    shown = sm.position(old, 105.0)                             # 5 s of dead reckoning
    # the real aircraft slowed down: the new fix is 0.1 nm short of the projection
    new = plane(105.0, lat=shown[0] + 0.1 / 60)
    at_arrival = sm.position(new, 105.0)
    assert at_arrival == pytest.approx(shown, abs=1e-9)         # no jump on the frame it arrives
    mid = sm.position(new, 105.5)
    done = sm.position(new, 106.2)
    target = advance(new.lat, new.lon, 240.0, 180.0, 1.2)
    assert done == pytest.approx(target, abs=1e-9)              # fully converged after the tween
    assert abs(mid[0] - advance(new.lat, new.lon, 240.0, 180.0, 0.5)[0]) > 1e-6   # mid-blend


def test_a_huge_correction_snaps_instead_of_sliding_across_the_map():
    sm = Smoother(tween_seconds=1.0)
    sm.position(plane(100.0), 100.0)
    far = plane(105.0, lat=20.5)                                # 36 nm away: bad fix or hex reuse
    assert sm.position(far, 105.0) == (20.5, -156.1)


def test_parked_aircraft_do_not_drift():
    sm = Smoother()
    ac = plane(100.0, gs=0.0, track=None, on_ground=True)
    assert sm.position(ac, 100.0) == sm.position(ac, 125.0) == (19.9, -156.1)


def test_disabled_and_pruned():
    sm = Smoother(enabled=False)
    ac = plane(100.0)
    assert sm.position(ac, 110.0) == (19.9, -156.1)
    sm.prune(set())
    assert sm._state == {}
