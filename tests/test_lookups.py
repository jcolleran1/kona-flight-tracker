"""Audit C: flight numbers, airline names, brand rules."""
import pytest

from enrich.lookups import (airline_prefix, clean_callsign, clean_registration,
                            clean_text)
from tests.helpers import FakeEnricher, HNL, KOA, lookups, route, track_one


@pytest.mark.parametrize("callsign,flight", [
    ("UAL1759", "UA1759"),
    ("HAL21", "HA21"),
    ("ASA1121", "AS1121"),
    ("SWA0042", "WN42"),        # leading zeros are not part of the flight number
    ("DAL480", "DL480"),
    ("QFA12", "QF12"),
    ("FDX1309", "FX1309"),
    # not a plain airline + digits callsign: shown exactly as broadcast
    ("FDY539SA", "FDY539SA"),   # Mokulele: prefix + tail number
    ("BAW12C", "BAW12C"),       # alphanumeric ATC callsign, not a flight number
    ("N739HP", "N739HP"),
    ("RCH321", "RCH321"),       # known operator with no IATA code
    ("QXQ99", "QXQ99"),         # unknown prefix
    ("", ""),
])
def test_icao_callsign_to_iata_flight_number(callsign, flight):
    assert lookups().flight_number(callsign) == flight


def test_adsbdb_translation_fills_gaps_but_cannot_change_the_number():
    L = lookups()
    assert L.flight_number("QXQ99", "Q999") == "Q999"            # we had nothing
    assert L.flight_number("UAL1759", "UA1759") == "UA1759"
    assert L.flight_number("UAL1759", "XX123") == "UA1759"       # different number: ignored
    assert L.flight_number("UAL1759", "garbage!") == "UA1759"
    assert L.flight_number("ASA1121", "HA121") == "AS1121"       # hand-checked row wins


def test_airline_names_and_overrides():
    L = lookups()
    assert L.airline_for_callsign("HAL21").name == "Hawaiian Airlines"
    assert L.airline_for_callsign("ASA1121").name == "Alaska Airlines"
    assert L.airline_for_callsign("FDY539SA").name == "Mokulele Airlines"
    assert L.airline_for_callsign("UAL1").curated
    assert L.airline_for_callsign("N739HP") is None
    assert L.airline_for_callsign("QXQ99") is None


def test_stale_openflights_rows_are_silenced():
    L = lookups()
    assert L.airline_for_callsign("STT303") is None      # was "Air St. Thomas"
    assert L.airline_for_callsign("MKU12") is None       # was "Island Air", closed 2017
    assert L.flight_number("MKU12") == "MKU12"           # and no WP12 either


def test_airlines_csv_has_iata_column_and_only_sane_codes():
    L = lookups()
    assert len(L.airlines) > 500
    for a in L.airlines.values():
        assert len(a.icao) == 3 and a.icao.isalpha()
        assert a.iata == "" or (len(a.iata) == 2 and a.iata.isalnum())


def test_hawaiian_airframe_on_alaska_callsign_is_branded_hawaiian():
    L = lookups()
    assert L.brand("ASA1121", "N479HA", "HAWAIIAN AIRLINES INC") == "Hawaiian Airlines"
    assert L.brand("ASA1121", "N494HA", "") == "Hawaiian Airlines"           # tail alone
    assert L.brand("ASA1121", "", "Hawaiian Airlines") == "Hawaiian Airlines"  # owner alone
    assert L.flight_number("ASA1121") == "AS1121"                            # number stays AS
    # a real Alaska jet is still Alaska
    assert L.brand("ASA319", "N319AS", "ALASKA AIRLINES INC") == ""
    assert L.brand("ASA319", "N1HAX", "") == ""


def test_owner_rules_never_rename_another_airlines_flight():
    L = lookups()
    assert L.brand("N130BH", "N130BH", "BLUE HAWAIIAN HELICOPTERS") == "Blue Hawaiian Helicopters"
    assert L.brand("", "N522SA", "SOUTHERN AIRWAYS EXPRESS LLC") == "Mokulele Airlines"
    assert L.brand("UAL1158", "N1", "NETJETS SALES INC") == ""


def test_fixture_hawaiian_on_asa(nasty, cfg):
    ac = track_one(cfg, nasty["a50007"])
    assert (ac.flight, ac.callsign, ac.airline) == ("AS1121", "ASA1121", "Hawaiian Airlines")
    assert ac.type_name == "Boeing 717-200"


def test_missing_callsign_falls_back_to_tail_number(nasty, cfg):
    ac = track_one(cfg, nasty["a50005"])
    assert ac.callsign == "" and ac.flight == "N522SA" and ac.label == "N522SA"


def test_missing_type_is_requested_then_filled_from_the_airframe_lookup(nasty, cfg):
    from enrich.worker import AircraftInfo
    asking = FakeEnricher()
    ac = track_one(cfg, nasty["a50005"], enricher=asking)
    assert ac.type_code == "" and asking.asked_aircraft == ["a50005"]

    known = FakeEnricher(aircraft={"a50005": AircraftInfo("N522SA", "C208", "Cessna 208",
                                                          "Southern Airways Express")})
    ac = track_one(cfg, nasty["a50005"], enricher=known)
    assert ac.type_code == "C208" and ac.type_name == "Cessna 208 Caravan"
    assert ac.airline == "Mokulele Airlines" and known.asked_aircraft == []


def test_feed_description_is_the_type_fallback(nasty, cfg):
    raw = dict(nasty["a50011"], t="ZZZZ")
    assert track_one(cfg, raw).type_name == "EUROCOPTER EC-130"


def test_unknown_airline_name_comes_from_the_route_database(cfg, nasty):
    raw = dict(nasty["a5000a"], flight="XYZ77   ", r="", ownOp="")
    rt = route(HNL, KOA, "XY77", ("Example Air", "XYZ", "XY"))
    ac = track_one(cfg, raw, enricher=FakeEnricher(routes={"XYZ77": rt}))
    assert ac.airline == "Example Air" and ac.flight == "XY77"
    # but never for a prefix the override file deliberately blanked
    raw = dict(raw, flight="STT303  ")
    rt = route(HNL, KOA, "", ("Air St. Thomas", "STT", ""))
    assert track_one(cfg, raw, enricher=FakeEnricher(routes={"STT303": rt})).airline == ""


def test_garbage_identity_is_blank_not_shown(nasty, cfg):
    ac = track_one(cfg, nasty["a5000f"])          # '@@@@@@@@', no reg, no type
    assert ac is not None and ac.callsign == "" and ac.flight == "" and ac.label == ""
    assert ac.altitude_ft is None and ac.ground_speed_kt is None


def test_text_cleaning():
    assert clean_text("Keāhole") == "Keahole"
    assert clean_text("東京 Tōkyō") == "Tokyo"
    assert clean_text("  a   b  ") == "a b"
    assert clean_text(None) == "" and clean_text(42) == ""
    assert clean_text("x" * 100, 10) == "x" * 10
    assert clean_callsign("ual1759 ") == "UAL1759"
    assert clean_callsign("@@@@@@@@") == "" and clean_callsign("00000000") == ""
    assert clean_callsign(None) == ""
    assert clean_registration("n479ha") == "N479HA" and clean_registration("VH-ZNK") == "VH-ZNK"
    assert clean_registration("") == "" and clean_registration("???") == ""
    assert airline_prefix("UAL1759") == "UAL" and airline_prefix("FDY539SA") == "FDY"
    assert airline_prefix("N739HP") == "" and airline_prefix("") == ""
