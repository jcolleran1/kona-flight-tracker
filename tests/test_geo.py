import math

import pytest

from enrich.geo import bearing_deg, compass_point, haversine_nm

KOA = (19.738783, -156.045603)
HNL = (21.318387, -157.92567)
ITO = (19.72135, -155.045428)


def test_haversine_known_distances():
    assert haversine_nm(*KOA, *KOA) == 0
    assert haversine_nm(*KOA, *HNL) == pytest.approx(142, abs=2)   # ~163 statute miles
    assert haversine_nm(*KOA, *ITO) == pytest.approx(56.5, abs=1)


def test_one_degree_of_latitude_is_sixty_nm():
    assert haversine_nm(19.0, -156.0, 20.0, -156.0) == pytest.approx(60.0, abs=0.1)


def test_bearing_cardinals():
    assert bearing_deg(19, -156, 20, -156) == pytest.approx(0, abs=0.01)
    assert bearing_deg(19, -156, 19, -155) == pytest.approx(90, abs=0.3)
    assert bearing_deg(19, -156, 18, -156) == pytest.approx(180, abs=0.01)
    assert bearing_deg(19, -156, 19, -157) == pytest.approx(270, abs=0.3)


def test_bearing_koa_to_hnl_is_northwest():
    assert 300 < bearing_deg(*KOA, *HNL) < 315


@pytest.mark.parametrize("deg,label", [
    (0, "N"), (11.24, "N"), (11.26, "NNE"), (45, "NE"), (90, "E"), (180, "S"),
    (270, "W"), (348.74, "NNW"), (348.76, "N"), (359.9, "N"), (360, "N"),
])
def test_compass_point(deg, label):
    assert compass_point(deg) == label


def test_bearing_always_in_range():
    for i in range(0, 360, 7):
        lat = 19.7 + 0.3 * math.cos(math.radians(i))
        lon = -156.0 + 0.3 * math.sin(math.radians(i))
        assert 0 <= bearing_deg(*KOA, lat, lon) < 360
