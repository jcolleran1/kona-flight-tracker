"""Audit E: a wrong route must never reach the screen."""
import pytest

from enrich.routes import route_from_dict, validate_route
from tests.helpers import FakeEnricher, HNL, ITO, KOA, OAK, SEA, SFO, route, track_one

HOME = ("KOA", "PHKO")


def check(rt, **kw):
    base = dict(lat=20.0, lon=-156.2, track_deg=140.0, altitude_ft=6000.0, on_ground=False,
                kinematic="local", distance_home_nm=18.0, home_codes=HOME)
    base.update(kw)
    return validate_route(rt, **base)


def test_parse_adsbdb_flightroute():
    rt = route_from_dict({
        "callsign": "UAL1759", "callsign_icao": "UAL1759", "callsign_iata": "UA1759",
        "airline": {"name": "United Airlines", "icao": "UAL", "iata": "UA"},
        "origin": {"iata_code": "KOA", "icao_code": "PHKO", "latitude": 19.7388,
                   "longitude": -156.0456, "municipality": "Kailua-Kona",
                   "name": "Ellison Onizuka Kona International Airport at Keāhole"},
        "destination": {"iata_code": "SFO", "icao_code": "KSFO", "latitude": 37.619,
                        "longitude": -122.375, "municipality": "San Francisco"},
    })
    assert rt.origin.code == "KOA" and rt.destination.city == "San Francisco"
    assert rt.callsign_iata == "UA1759" and rt.airline_name == "United Airlines"
    assert rt.origin.matches("KOA", "PHKO") and not rt.destination.matches("KOA", "PHKO")


@pytest.mark.parametrize("bad", [None, "unknown callsign", {}, {"origin": {}, "destination": {}},
                                 {"origin": {"iata_code": "KOA"}}, {"origin": 3, "destination": []}])
def test_unusable_routes_parse_to_none(bad):
    assert route_from_dict(bad) is None


def test_the_real_bad_answer_seattle_to_san_francisco_is_rejected():
    # What adsbdb returned for ASA1082 while it was a Hawaiian 717 north of Kona.
    ok, why = check(route(SEA, SFO), lat=20.43, lon=-155.79, track_deg=116, altitude_ft=22775,
                    distance_home_nm=44)
    assert not ok and "nowhere near" in why


def test_good_routes_pass():
    assert check(route(HNL, KOA), kinematic="arriving")[0]
    assert check(route(KOA, SFO), lat=19.9, lon=-156.3, track_deg=30, kinematic="departing")[0]
    assert check(route(HNL, ITO), lat=20.2, lon=-156.2, track_deg=105, altitude_ft=14000,
                 kinematic="overflight", distance_home_nm=30)[0]
    assert check(route(OAK, KOA), lat=20.45, lon=-155.4, track_deg=236, altitude_ft=22000,
                 kinematic="overflight", distance_home_nm=56)[0]


def test_landing_here_but_route_ends_elsewhere():
    ok, why = check(route(HNL, ITO), kinematic="arriving")
    assert not ok and "landing here" in why


def test_departed_here_but_route_starts_elsewhere():
    assert not check(route(HNL, ITO), kinematic="departing")[0]
    # the inbound flight number still attached to the outbound leg
    assert not check(route(SFO, KOA), kinematic="departing")[0]
    assert not check(route(KOA, SFO), kinematic="arriving")[0]


def test_parked_at_kona_needs_a_route_that_includes_kona():
    assert not check(route(HNL, ITO), on_ground=True, kinematic="ground", distance_home_nm=0.3,
                     lat=19.738, lon=-156.04)[0]
    assert check(route(KOA, SFO), on_ground=True, kinematic="ground", distance_home_nm=0.3,
                 lat=19.738, lon=-156.04)[0]


def test_flying_away_from_the_destination_in_cruise_is_rejected():
    # route says KOA -> SFO but the jet is at FL340 heading southwest
    ok, why = check(route(KOA, SFO), lat=20.3, lon=-155.6, track_deg=235, altitude_ft=34000,
                    kinematic="overflight", distance_home_nm=42)
    assert not ok and "away from the destination" in why
    # low and maneuvering near the airport is not judged on heading
    assert check(route(KOA, SFO), lat=19.68, lon=-156.06, track_deg=187, altitude_ft=2900,
                 kinematic="departing", distance_home_nm=4)[0]


def test_same_airport_both_ends_and_missing_route():
    assert not check(route(KOA, KOA))[0]
    assert check(None) == (False, "no route")


def test_route_without_coordinates_is_judged_on_the_remaining_rules():
    from enrich.routes import Airport
    bare = route(Airport("HNL", "PHNL", "Honolulu"), Airport("KOA", "PHKO", "Kailua-Kona"))
    assert check(bare, kinematic="arriving")[0]
    assert not check(bare, kinematic="departing")[0]


# ---- through the tracker ----

def test_tracker_blanks_a_rejected_route_and_keeps_direction(nasty, cfg):
    bad = FakeEnricher(routes={"ASA1082": route(SEA, SFO, "AS1082", ("Alaska Airlines", "ASA", "AS"))})
    ac = track_one(cfg, nasty["a5000b"], enricher=bad, repeats=3)
    assert (ac.origin_code, ac.dest_code, ac.origin_name, ac.dest_name) == ("", "", "", "")
    assert ac.flight == "AS1082" and ac.airline == "Hawaiian Airlines"   # identity unaffected
    assert ac.direction in ("toward", "away")


def test_tracker_asks_for_routes_only_for_real_flight_numbers(nasty, cfg):
    e = FakeEnricher()
    track_one(cfg, nasty["a50009"], enricher=e)          # UAL1158
    assert e.asked_routes == ["UAL1158"]
    for hex_id in ("a50011", "a50005", "a5000f"):        # tail-number callsign, none, garbage
        e = FakeEnricher()
        track_one(cfg, nasty[hex_id], enricher=e)
        assert e.asked_routes == []


def test_tracker_shows_a_validated_route(nasty, cfg):
    e = FakeEnricher(routes={"UAL1158": route(KOA, SFO, "UA1158")})
    ac = track_one(cfg, nasty["a50009"], enricher=e, repeats=4)
    assert ac.phase == "departing"
    assert (ac.origin_code, ac.dest_code, ac.dest_name) == ("KOA", "SFO", "San Francisco")
    assert e.asked_routes == []                          # already cached: no request
