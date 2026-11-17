#!/usr/bin/env python3
"""Generate the bundled replay scenario: data/fixtures/kona_scenario.jsonl.gz

A scripted 15 minutes of traffic around Kona, written in the same recording
format tools/record.py produces, so replay mode and the tests exercise the real
parsing path. Everything is synthetic (no live feed data is committed) but the
record shapes are copied from real readsb / ADSBExchange-v2 responses.

The scenario deliberately contains every awkward case the tracker has to get
right:

  * an arrival that rolls out with a NEGATIVE alt_baro and no "ground" flag
  * a departure whose takeoff roll also reports negative pressure altitude
  * a Hawaiian 717 flying under an Alaska ASA callsign
  * an FL360 overflight that points at Kona and must not be called an arrival
  * a descending jet that is landing at Hilo, not Kona, with a stale-database
    route (Seattle to San Francisco) that must be rejected
  * a record with no callsign and no type, an MLAT position, an anonymous
    TIS-B target, a stale position, a ground vehicle, a record with no position,
    and one that only has geometric altitude

Deterministic: running it twice produces a byte-identical file.

    python3 tools/make_scenario.py
"""
from __future__ import annotations

import gzip
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_DIR  # noqa: E402

OUT = DATA_DIR / "fixtures" / "kona_scenario.jsonl.gz"

T0 = 1794880800.0        # arbitrary fixed epoch for frame timestamps
DURATION = 900           # seconds
STEP = 5                 # seconds between frames
QNH_HPA = 1017.9         # scenario altimeter (30.06 inHg): a normal Kona day
FT_PER_HPA = 27.3

# KOA runway 17/35 (OurAirports, public domain)
RWY17 = (19.75390, -156.04400)
RWY35 = (19.72370, -156.04700)
APRON = (19.73850, -156.04050)
FIELD_ELEV = 47

AIRPORTS = {
    "KOA": ("PHKO", "Kailua-Kona", 19.738783, -156.045603),
    "HNL": ("PHNL", "Honolulu", 21.318387, -157.92567),
    "ITO": ("PHTO", "Hilo", 19.72135, -155.045428),
    "OGG": ("PHOG", "Kahului", 20.896263, -156.431837),
    "SFO": ("KSFO", "San Francisco", 37.619, -122.375),
    "OAK": ("KOAK", "Oakland", 37.7213, -122.221),
    "LAX": ("KLAX", "Los Angeles", 33.9425, -118.408),
    "SYD": ("YSSY", "Sydney", -33.9461, 151.177),
    "SEA": ("KSEA", "Seattle", 47.449, -122.309),
    "ONT": ("KONT", "Ontario", 34.056, -117.601),
}


def _route(callsign, iata_flight, airline, a_icao, a_iata, origin, dest):
    def ap(code):
        icao, city, lat, lon = AIRPORTS[code]
        return {"iata": code, "icao": icao, "city": city, "lat": lat, "lon": lon}
    return callsign, {
        "callsign_iata": iata_flight,
        "airline": {"name": airline, "icao": a_icao, "iata": a_iata},
        "origin": ap(origin), "destination": ap(dest),
    }


# Offline answers the replay hands to the enrichment layer instead of adsbdb.
ROUTES = dict([
    _route("ASA1121", "AS1121", "Alaska Airlines", "ASA", "AS", "HNL", "KOA"),
    _route("UAL1158", "UA1158", "United Airlines", "UAL", "UA", "KOA", "SFO"),
    _route("QFA12", "QF12", "Qantas", "QFA", "QF", "LAX", "SYD"),
    _route("SWA2231", "WN2231", "Southwest Airlines", "SWA", "WN", "OAK", "KOA"),
    _route("UPS34", "5X34", "UPS Airlines", "UPS", "5X", "HNL", "ONT"),
    # Deliberately WRONG, exactly as adsbdb answered for a real interisland
    # Hawaiian 717 in November 2026. Route validation must throw it away.
    _route("ASA1071", "AS1071", "Alaska Airlines", "ASA", "AS", "SEA", "SFO"),
])

# Offline answers for hex lookups (only for records whose feed data is thin).
AIRCRAFT_DB = {
    "a6c5d1": {"registration": "N522SA", "type_code": "C208",
               "type_name": "Cessna 208 Caravan", "owner": "Southern Airways Express"},
}


def nm_between(a, b):
    dlat = (b[0] - a[0]) * 60.0
    dlon = (b[1] - a[1]) * 60.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def bearing(a, b):
    dlat = (b[0] - a[0]) * 60.0
    dlon = (b[1] - a[1]) * 60.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.degrees(math.atan2(dlon, dlat)) % 360.0


class Flight:
    """A scripted aircraft: straight legs between timed waypoints.

    waypoints: (t_seconds, lat, lon, true_altitude_ft, state)
    state is "air", "roll" (on the runway, transponder still in air mode: the
    negative-altitude case) or "ground" (transponder reports the ground bit).
    """

    def __init__(self, hex_id, waypoints, **static):
        self.hex = hex_id
        self.wp = waypoints
        self.static = static

    def at(self, t):
        wp = self.wp
        if t < wp[0][0] or t > wp[-1][0]:
            return None
        for i in range(len(wp) - 1):
            a, b = wp[i], wp[i + 1]
            if a[0] <= t <= b[0]:
                break
        span = max(b[0] - a[0], 1e-9)
        f = (t - a[0]) / span
        lat = a[1] + (b[1] - a[1]) * f
        lon = a[2] + (b[2] - a[2]) * f
        alt = a[3] + (b[3] - a[3]) * f
        dist = nm_between((a[1], a[2]), (b[1], b[2]))
        gs = dist / span * 3600.0
        trk = bearing((a[1], a[2]), (b[1], b[2])) if dist > 1e-4 else None
        rate = (b[3] - a[3]) / span * 60.0
        return lat, lon, alt, gs, trk, rate, a[4]


def _base(**kw):
    rec = {"type": "adsb_icao", "squawk": "1200", "emergency": "none",
           "category": "A3", "nic": 8, "rc": 186, "version": 2, "nac_p": 9,
           "sil": 3, "alert": 0, "spi": 0, "mlat": [], "tisb": []}
    rec.update(kw)
    return rec


FLIGHTS = [
    # 1. Hawaiian 717 on an Alaska callsign, HNL -> KOA, lands runway 17 and
    #    rolls out reporting a negative pressure altitude.
    Flight("a5e2f1", [
        (0, 20.220, -156.520, 11500, "air"),
        (330, 19.980, -156.200, 5200, "air"),
        (480, 19.880, -156.075, 2500, "air"),
        (650, RWY17[0], RWY17[1], FIELD_ELEV + 15, "air"),
        (690, 19.7340, -156.0460, FIELD_ELEV, "roll"),
        (700, 19.7300, -156.0464, FIELD_ELEV, "ground"),
        (780, APRON[0], APRON[1], FIELD_ELEV, "ground"),
        (900, APRON[0], APRON[1] + 0.0001, FIELD_ELEV, "ground"),
    ], flight="ASA1121 ", r="N479HA", t="B712", desc="BOEING 717-200",
        ownOp="HAWAIIAN AIRLINES INC", squawk="2153"),

    # 2. United departure KOA -> SFO off runway 17, right turn out to the north.
    Flight("a4b1c9", [
        (0, 19.7480, -156.0425, FIELD_ELEV, "ground"),
        (95, RWY17[0], RWY17[1], FIELD_ELEV, "ground"),
        (100, RWY17[0], RWY17[1] - 0.00002, FIELD_ELEV, "roll"),
        (138, 19.7345, -156.0459, FIELD_ELEV, "roll"),
        (200, 19.6800, -156.0600, 2900, "air"),
        (262, 19.6500, -156.1400, 6200, "air"),
        (400, 19.9000, -156.3200, 13500, "air"),
        (900, 20.6500, -155.7000, 31000, "air"),
    ], flight="UAL1158 ", r="N68823", t="B739", desc="BOEING 737-900",
        ownOp="UNITED AIRLINES INC", squawk="3301"),

    # 3. Qantas LAX -> SYD at FL360, crossing the island: points at Kona for a
    #    while and must stay an overflight.
    Flight("7c1472", [
        (0, 20.250, -155.350, 36000 + 127, "air"),
        (900, 19.050, -156.950, 36000 + 127, "air"),
    ], flight="QFA12   ", r="VH-ZNK", t="B789", desc="BOEING 787-9 Dreamliner",
        ownOp="QANTAS AIRWAYS LTD", squawk="4405", category="A5"),

    # 4. Hawaiian 717 on an ASA callsign landing at HILO. Descends past Kona;
    #    the database route for this callsign is wrong (SEA-SFO).
    Flight("a5ff93", [
        (0, 20.220, -156.200, 14000, "air"),
        (500, 20.050, -155.500, 6200, "air"),
        (800, 19.800, -155.100, 1500, "air"),
        (900, 19.7214, -155.0454, 120, "air"),
    ], flight="ASA1071 ", r="N486HA", t="B712", desc="BOEING 717-200",
        ownOp="HAWAIIAN AIRLINES INC", squawk="2204"),

    # 5. Tour helicopter along the Kohala coast.
    Flight("a0c7de", [
        (0, 19.914, -155.862, 1500, "air"),
        (300, 20.050, -155.820, 1900, "air"),
        (600, 20.180, -155.800, 2400, "air"),
        (900, 20.060, -155.700, 3600, "air"),
    ], flight="N130BH  ", r="N130BH", t="EC30", desc="EUROCOPTER EC-130",
        ownOp="BLUE HAWAIIAN HELICOPTERS", category="A7"),

    # 6. Cessna 172 in the pattern north of the field. geom_rate only.
    Flight("a9ed37", [
        (0, 19.858, -155.951, 1500, "air"),
        (200, 19.810, -156.060, 1300, "air"),
        (400, 19.870, -156.100, 1300, "air"),
        (650, 19.930, -155.990, 1600, "air"),
        (900, 19.858, -155.951, 1500, "air"),
    ], flight="N739HP  ", r="N739HP", t="C172", desc="CESSNA 172 Skyhawk",
        ownOp="HAWAII PACIFIC AVIATION INC", category="A1", _no_baro_rate=True,
        _no_qnh=True),

    # 7. Mokulele Caravan (Southern Airways Express, tail-number callsign)
    #    climbing out to the north.
    Flight("a6d36d", [
        (0, 19.820, -156.060, 2800, "air"),
        (300, 20.060, -156.180, 7000, "air"),
        (900, 20.560, -156.420, 8000, "air"),
    ], flight="FDY539SA", r="N539SA", t="C208", desc="CESSNA 208 Caravan",
        ownOp="SOUTHERN AIRWAYS EXPRESS LLC", category="A1", squawk="5120"),

    # 8. No callsign, no type: a Caravan inbound from the north.
    Flight("a6c5d1", [
        (0, 20.150, -156.120, 5500, "air"),
        (300, 19.900, -156.070, 2000, "air"),
        (405, RWY17[0], RWY17[1], FIELD_ELEV + 15, "air"),
        (440, 19.7400, -156.0454, FIELD_ELEV, "roll"),
        (450, 19.7385, -156.0455, FIELD_ELEV, "ground"),
        (520, APRON[0] - 0.001, APRON[1], FIELD_ELEV, "ground"),
        (900, APRON[0] - 0.001, APRON[1] + 0.0001, FIELD_ELEV, "ground"),
    ], category="A1", squawk="5133", _no_flight=True),

    # 9. Southwest OAK -> KOA, long descent in from the northeast.
    Flight("ac101f", [
        (0, 20.450, -155.400, 22000, "air"),
        (420, 20.050, -156.020, 8000, "air"),
        (600, 19.930, -156.090, 3800, "air"),
        (860, RWY17[0], RWY17[1], FIELD_ELEV + 15, "air"),
        (896, 19.7340, -156.0460, FIELD_ELEV, "roll"),
        (900, 19.7325, -156.0461, FIELD_ELEV, "roll"),
    ], flight="SWA2231 ", r="N8768Q", t="B38M", desc="BOEING 737 MAX 8",
        ownOp="SOUTHWEST AIRLINES CO", squawk="1746"),

    # 10. Military C-17, MLAT-only position, passing south of the island.
    Flight("ae1464", [
        (0, 19.150, -156.700, 24000 + 127, "air"),
        (900, 19.300, -155.000, 24000 + 127, "air"),
    ], flight="RCH321  ", t="C17", desc="BOEING C-17A Globemaster III",
        type="mlat", dbFlags=1, category="A5", mlat=["lat", "lon", "gs", "track"],
        _no_r=True),

    # 11. Piper with only geometric altitude, near Waimea.
    Flight("a1f0b2", [
        (0, 20.000, -155.720, 4200, "air"),
        (900, 19.700, -155.820, 4600, "air"),
    ], flight="N205KM  ", r="N205KM", t="P28A", desc="PIPER PA-28 Cherokee",
        category="A1", _geom_only=True),

    # 12. UPS HNL -> ONT in cruise north of the island, heading away.
    Flight("a8d2e4", [
        (0, 20.300, -156.450, 37000 + 127, "air"),
        (900, 20.950, -154.700, 37000 + 127, "air"),
    ], flight="UPS34   ", r="N615UP", t="B748", desc="BOEING 747-8F",
        ownOp="UNITED PARCEL SERVICE CO", category="A5", squawk="6630"),

    # 13. NetJets Citation arriving from the northeast (no route in the DB).
    Flight("a3c9a7", [
        (300, 20.380, -155.450, 16000, "air"),
        (700, 19.990, -156.030, 5000, "air"),
        (900, 19.860, -156.080, 2200, "air"),
    ], flight="EJA555  ", r="N355QS", t="C68A", desc="CESSNA 680A Citation Latitude",
        ownOp="NETJETS SALES INC", category="A2", squawk="4471"),

    # 14/15. On the ground at KOA: one parked, one taxiing.
    Flight("ab6f0c", [
        (0, 19.7362, -156.0403, FIELD_ELEV, "ground"),
        (900, 19.7362, -156.0402, FIELD_ELEV, "ground"),
    ], flight="SWA1297 ", r="N8540V", t="B738", desc="BOEING 737-800",
        ownOp="SOUTHWEST AIRLINES CO"),
    Flight("a58fdf", [
        (0, 19.7400, -156.0408, FIELD_ELEV, "ground"),
        (240, 19.7470, -156.0425, FIELD_ELEV, "ground"),
        (900, 19.7471, -156.0425, FIELD_ELEV, "ground"),
    ], flight="AAL663  ", r="N458AL", t="A21N", desc="AIRBUS A-321neo",
        ownOp="AMERICAN AIRLINES INC"),

    # --- records that must never reach the screen ---
    # Stale position (seen_pos far past the freshness limit).
    Flight("a11111", [
        (0, 19.600, -156.200, 9000, "air"),
        (900, 19.600, -156.210, 9000, "air"),
    ], flight="N818PH  ", r="N818PH", t="BE20", _stale=True),
    # Airport ground vehicle.
    Flight("a0f001", [
        (0, 19.7420, -156.0440, FIELD_ELEV, "ground"),
        (900, 19.7300, -156.0463, FIELD_ELEV, "ground"),
    ], flight="OPS12   ", category="C2", _no_r=True),
    # Anonymous TIS-B target.
    Flight("~2a1f3c", [
        (0, 19.500, -155.900, 3000, "air"),
        (900, 19.450, -155.700, 3000, "air"),
    ], type="tisb_other", category="A1", _no_flight=True, _no_r=True),
]


def record_for(fl: Flight, t: float, rng: random.Random) -> dict | None:
    p = fl.at(t)
    if p is None:
        return None
    lat, lon, alt_true, gs, trk, rate, state = p
    st = dict(fl.static)
    flags = {k: st.pop(k) for k in list(st) if k.startswith("_")}
    rec = _base(hex=fl.hex, **st)
    if flags.get("_no_flight"):
        rec.pop("flight", None)
    if flags.get("_no_r"):
        rec.pop("r", None)

    pressure_alt = alt_true - (QNH_HPA - 1013.25) * FT_PER_HPA
    baro = int(round(pressure_alt / 25.0) * 25)
    if state == "ground":
        rec["alt_baro"] = "ground"
    elif flags.get("_geom_only"):
        rec["alt_geom"] = int(round((alt_true + 60) / 25.0) * 25)
    else:
        rec["alt_baro"] = baro
        rec["alt_geom"] = int(round((alt_true + 60) / 25.0) * 25)
        if not flags.get("_no_qnh"):
            # the pilot-selected altimeter setting, in 0.8 hPa steps
            rec["nav_qnh"] = round(round(QNH_HPA / 0.8) * 0.8, 1) if baro < 18000 else 1013.6

    rec["gs"] = round(gs, 1)
    if trk is not None:
        rec["track"] = round(trk, 2)
    if state == "air":
        r64 = int(round(rate / 64.0) * 64)
        if flags.get("_no_baro_rate"):
            rec["geom_rate"] = r64
        else:
            rec["baro_rate"] = r64
    rec["lat"] = round(lat, 6)
    rec["lon"] = round(lon, 6)
    seen = round(rng.uniform(0.1, 1.6), 1)
    if flags.get("_stale"):
        seen = round(rng.uniform(48.0, 75.0), 1)
    rec["seen_pos"] = seen
    rec["seen"] = min(seen, 0.4)
    rec["messages"] = 4000 + int(t * 9)
    rec["rssi"] = -round(rng.uniform(8.0, 28.0), 1)
    return rec


def frame(t: float, rng: random.Random) -> dict:
    ac = [r for fl in FLIGHTS if (r := record_for(fl, t, rng)) is not None]
    # a record with an identity but no position at all
    ac.append(_base(hex="abc999", flight="N9999X  ", r="N9999X", t="C182",
                    alt_baro=5500, gs=120.0, seen=3.1, messages=77, rssi=-30.2,
                    category="A1"))
    now_ms = int((T0 + t) * 1000)
    return {"ac": ac, "msg": "No error", "now": now_ms, "total": len(ac),
            "ctime": now_ms, "ptime": 0}


def build_lines() -> list[str]:
    rng = random.Random(19738)
    meta = {"meta": {
        "description": "Synthetic Kona scenario (tools/make_scenario.py)",
        "qnh_hpa": QNH_HPA,
        "routes": ROUTES,
        "aircraft": AIRCRAFT_DB,
    }}
    lines = [json.dumps(meta, separators=(",", ":"), sort_keys=True)]
    for t in range(0, DURATION + 1, STEP):
        rec = {"t": T0 + t, "source": "scenario", "data": frame(float(t), rng)}
        lines.append(json.dumps(rec, separators=(",", ":"), sort_keys=True))
    return lines


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(build_lines()) + "\n").encode("utf-8")
    with open(OUT, "wb") as raw:
        # mtime=0 and no filename keep the gzip header stable between runs
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(payload)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB, "
          f"{DURATION // STEP + 1} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
