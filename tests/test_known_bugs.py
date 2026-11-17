"""Bugs from the PLAN.md audit, pinned as failing tests.

Each is marked xfail(strict=True): the suite stays green while the bug exists,
and goes red the moment the bug is fixed without this file being updated. As
Phase 1 lands, each test moves out of here into the real test for that feature.
"""
import pytest

from enrich.lookups import Lookups
from enrich.motion import movement_class, select_featured
from enrich.trend import TrendTracker
from fetch.worker import parse_record


@pytest.fixture(scope="module")
def lookups():
    return Lookups.load()


def _parse(raw, cfg, lookups):
    return parse_record(raw, cfg, lookups, TrendTracker())


@pytest.mark.xfail(strict=True, reason="audit A: pressure altitude shown raw")
def test_negative_altitude_never_reaches_the_screen(nasty, cfg, lookups):
    ac = _parse(nasty["a50001"], cfg, lookups)
    assert ac.altitude_ft is None or ac.altitude_ft >= 0


@pytest.mark.xfail(strict=True, reason="audit A: ground only detected from the 'ground' string")
def test_slow_and_at_field_elevation_is_on_the_ground(nasty, cfg, lookups):
    assert _parse(nasty["a50002"], cfg, lookups).on_ground


@pytest.mark.xfail(strict=True, reason="audit B: seen_pos ignored")
def test_stale_position_is_dropped(nasty, cfg, lookups):
    assert _parse(nasty["a50004"], cfg, lookups) is None


@pytest.mark.xfail(strict=True, reason="audit D: overflight labelled as an arrival")
def test_fl360_overflight_is_not_featured_over_a_real_arrival(nasty, cfg, lookups):
    arrival = _parse(nasty["a5000a"], cfg, lookups)      # 2,400 ft, 8 nm north, descending
    overflight = _parse(nasty["7c1472"], cfg, lookups)   # FL360
    arrival.distance_nm, overflight.distance_nm = 8.0, 5.0
    assert select_featured([overflight, arrival]).hex == arrival.hex


@pytest.mark.xfail(strict=True, reason="additions 15: ground vehicles listed as aircraft")
def test_ground_vehicle_is_not_an_aircraft(nasty, cfg, lookups):
    assert _parse(nasty["a0f001"], cfg, lookups) is None


def test_ground_string_is_ground_today(nasty, cfg, lookups):
    ac = _parse(nasty["a50003"], cfg, lookups)
    assert ac.on_ground and movement_class(ac) == "ground"


def test_records_without_a_position_are_skipped_today(nasty, cfg, lookups):
    assert _parse(nasty["a5000d"], cfg, lookups) is None
