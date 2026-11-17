"""Audit A: negative altitudes. Nothing below zero may reach the screen."""
import pytest

from enrich.altitude import (qnh_correction_ft, resolve_altitude, vertical_rate,
                             vertical_trend)
from render.units import fmt_altitude
from tests.helpers import QNH, track_one


def test_correction_is_about_1000_ft_per_inhg():
    assert qnh_correction_ft(1013.25) == pytest.approx(0.0, abs=0.01)
    assert qnh_correction_ft(1013.25 + 33.8639) == pytest.approx(910, abs=25)
    assert qnh_correction_ft(QNH) == pytest.approx(127, abs=2)        # 30.06 inHg
    assert qnh_correction_ft(1009.0) < 0                               # a low-pressure day


def test_jet_on_the_runway_reads_field_elevation_not_minus_100():
    # KOA is at 47 ft; with the altimeter at 30.06 the transponder says -75.
    alt = resolve_altitude({"alt_baro": -75, "gs": 130}, QNH)
    assert not alt.on_ground and alt.basis == "metar"
    assert alt.feet == pytest.approx(52, abs=3)


@pytest.mark.parametrize("baro", [-1000, -325, -150, -127, -1])
def test_never_negative(baro):
    for qnh in (None, 1005.0, QNH, 1030.0):
        for raw in ({"alt_baro": baro, "gs": 140},
                    {"alt_baro": baro, "gs": 140, "nav_qnh": 1002.4},
                    {"alt_baro": baro, "gs": 140, "alt_geom": baro - 50}):
            alt = resolve_altitude(raw, qnh)
            assert alt.feet is None or alt.feet >= 0


def test_setting_preference_metar_then_aircraft_then_gps_then_raw():
    raw = {"alt_baro": 5000, "gs": 200, "nav_qnh": 1020.0, "alt_geom": 5400}
    assert resolve_altitude(raw, QNH).basis == "metar"
    assert resolve_altitude(raw, None).basis == "nav_qnh"
    assert resolve_altitude(raw, None).feet == pytest.approx(5000 + qnh_correction_ft(1020.0))
    no_qnh = {"alt_baro": 5000, "gs": 200, "alt_geom": 5400}
    assert resolve_altitude(no_qnh, None) == resolve_altitude(no_qnh, 2000.0)  # absurd METAR ignored
    assert resolve_altitude(no_qnh, None).feet == 5400
    assert resolve_altitude({"alt_baro": 5000, "gs": 200}, None).basis == "baro"


def test_flight_levels_are_left_alone():
    alt = resolve_altitude({"alt_baro": 36000, "gs": 460, "nav_qnh": 1013.6}, QNH)
    assert alt.feet == 36000 and alt.basis == "pressure"
    assert resolve_altitude({"alt_baro": 17975, "gs": 300}, QNH).feet == pytest.approx(18102, abs=3)


def test_ground_detection():
    assert resolve_altitude({"alt_baro": "ground", "gs": 14}, QNH).on_ground
    assert resolve_altitude({"alt_baro": "ground"}, None).on_ground
    # no ground flag, but at field elevation and taxi speed
    assert resolve_altitude({"alt_baro": -100, "gs": 22}, QNH).on_ground
    # fast on the runway: takeoff or landing roll, not "on the ground" yet
    assert not resolve_altitude({"alt_baro": -100, "gs": 128}, QNH).on_ground
    # low but with no speed reported: not enough to call it
    assert not resolve_altitude({"alt_baro": -100}, QNH).on_ground
    # a Cessna at 1,500 ft doing 55 kt into a headwind is flying
    assert not resolve_altitude({"alt_baro": 1400, "gs": 55}, QNH).on_ground


def test_unknown_and_garbage_altitudes():
    assert resolve_altitude({}, QNH).feet is None
    assert resolve_altitude({"alt_baro": None, "gs": None}, QNH).feet is None
    assert resolve_altitude({"alt_baro": "banana"}, QNH).feet is None
    assert resolve_altitude({"alt_baro": float("nan")}, QNH).feet is None
    assert resolve_altitude({"alt_baro": True}, QNH).feet is None
    assert resolve_altitude({"alt_geom": 4250, "gs": 104}, QNH).basis == "geom"


def test_vertical_rate_and_trend():
    assert vertical_rate({"baro_rate": -832, "geom_rate": -700}) == -832
    assert vertical_rate({"geom_rate": 64}) == 64
    assert vertical_rate({}) is None
    assert vertical_rate({"baro_rate": 99999}) is None
    assert vertical_trend(1500) == "climb"
    assert vertical_trend(-900) == "descend"
    assert vertical_trend(100) == "level"
    assert vertical_trend(None) == ""


@pytest.mark.parametrize("ft,ground,kw,text", [
    (None, True, {}, "GROUND"),
    (None, False, {}, "---"),
    (2850.4, False, {}, "2,850 ft"),
    (8469, False, {}, "8,475 ft"),
    (36000, False, {}, "36,000 ft"),
    (22775, False, {}, "22,800 ft"),
    (52, False, {"phase": "departing"}, "TAKEOFF"),
    (52, False, {"phase": "arriving"}, "LANDING"),
    (40, False, {"vertical_rate_fpm": 900}, "TAKEOFF"),
    (40, False, {"vertical_rate_fpm": -600}, "LANDING"),
    (0, False, {}, "LOW"),
    (-300, False, {}, "LOW"),          # even a value that should be impossible
])
def test_altitude_text(ft, ground, kw, text):
    assert fmt_altitude(ft, ground, "ft", **kw) == text


def test_no_altitude_text_ever_contains_a_minus_sign():
    for ft in (-5000, -150, -1, 0, 1, 99, 100, 5000):
        for unit in ("ft", "m"):
            assert "-" not in fmt_altitude(ft, False, unit).replace("---", "")


# ---- through the whole tracker, on the fixture records ----

def test_fixture_takeoff_roll_is_not_negative(nasty, cfg):
    ac = track_one(cfg, nasty["a50001"])           # alt_baro -150 at 128 kt
    assert not ac.on_ground and ac.altitude_ft == 0.0


def test_fixture_slow_negative_altitude_is_ground(nasty, cfg):
    ac = track_one(cfg, nasty["a50002"])           # alt_baro -100 at 22 kt
    assert ac.on_ground and ac.altitude_ft is None and ac.phase == "ground"


def test_fixture_geometric_only(nasty, cfg):
    assert track_one(cfg, nasty["a5000c"]).altitude_ft == 4250


def test_without_a_metar_the_aircrafts_own_setting_is_used(nasty, cfg):
    from tests.helpers import FakeEnricher
    ac = track_one(cfg, nasty["a50007"], enricher=FakeEnricher(qnh=None))   # 9,200 / nav_qnh 1017.6
    assert ac.altitude_ft == pytest.approx(9200 + qnh_correction_ft(1017.6))
