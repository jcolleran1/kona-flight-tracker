"""The bundled scenario is generated code; make sure it stays in step with the
generator and keeps containing every case the other tests rely on."""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import make_scenario  # noqa: E402


def _committed_lines():
    with gzip.open(make_scenario.OUT, "rt", encoding="utf-8") as f:
        return f.read().splitlines()


def test_committed_file_matches_generator():
    assert _committed_lines() == make_scenario.build_lines(), \
        "run: python3 tools/make_scenario.py"


def test_generator_is_deterministic():
    assert make_scenario.build_lines() == make_scenario.build_lines()


def _frames():
    return [json.loads(line) for line in _committed_lines()[1:]]


def test_contains_the_awkward_cases():
    seen_negative_alt = seen_ground = seen_stale = False
    seen_mlat = seen_no_flight = seen_no_pos = seen_geom_only = seen_vehicle = False
    for fr in _frames():
        for a in fr["data"]["ac"]:
            alt = a.get("alt_baro")
            if isinstance(alt, (int, float)) and alt < 0:
                seen_negative_alt = True
            seen_ground |= alt == "ground"
            seen_stale |= a.get("seen_pos", 0) > 30
            seen_mlat |= a.get("type") == "mlat"
            seen_no_flight |= "flight" not in a and "lat" in a and not a["hex"].startswith("~")
            seen_no_pos |= "lat" not in a
            seen_geom_only |= "alt_baro" not in a and "alt_geom" in a
            seen_vehicle |= a.get("category") == "C2"
    assert all([seen_negative_alt, seen_ground, seen_stale, seen_mlat, seen_no_flight,
                seen_no_pos, seen_geom_only, seen_vehicle])


def test_hawaiian_airframe_flies_under_an_alaska_callsign():
    first = _frames()[0]["data"]["ac"]
    rec = next(a for a in first if a["hex"] == "a5e2f1")
    assert rec["flight"].strip() == "ASA1121"
    assert rec["r"] == "N479HA"
    assert "HAWAIIAN" in rec["ownOp"]


def test_speeds_are_physically_plausible():
    for fr in _frames():
        for a in fr["data"]["ac"]:
            if "gs" in a and a.get("type") != "tisb_other":
                assert 0 <= a["gs"] < 520, (a["hex"], a["gs"])
