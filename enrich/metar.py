"""Airport weather from the aviationweather.gov METAR API.

Used for one thing that matters to correctness, the altimeter setting that
turns pressure altitude into the altitude people expect to read, and for the
optional weather chip. One request every 30 minutes: METARs are issued hourly
and the service asks clients to keep request rates low.

    GET https://aviationweather.gov/api/data/metar?ids=PHKO&format=json
    -> [{"icaoId": "PHKO", "obsTime": 1794880380, "altim": 1016.7 (hPa),
         "wdir": 280, "wspd": 7, "wgst": null, "temp": 27.8, "visib": "10+",
         "clouds": [{"cover": "SCT", "base": 4000}], "fltCat": "VFR",
         "rawOb": "METAR PHKO 170153Z 28007KT 10SM SCT040 28/19 A3002 ..."}]
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from enrich.altitude import HPA_PER_INHG, is_valid_qnh

METAR_URL = "https://aviationweather.gov/api/data/metar?ids={station}&format=json"
_ALTIMETER_RE = re.compile(r"\bA(\d{4})\b")
_QNH_RE = re.compile(r"\bQ(\d{4})\b")


@dataclass(frozen=True)
class Weather:
    station: str = ""
    qnh_hpa: float | None = None
    wind_dir_deg: float | None = None      # None = calm or variable
    wind_kt: float | None = None
    gust_kt: float | None = None
    temp_c: float | None = None
    cover: str = ""                        # CLR / FEW / SCT / BKN / OVC
    flight_category: str = ""              # VFR / MVFR / IFR / LIFR
    obs_time: float = 0.0                  # epoch seconds of the observation


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def qnh_from_raw(raw_ob: str) -> float | None:
    """Altimeter setting straight from the METAR text (A3002 or Q1017)."""
    if not isinstance(raw_ob, str):
        return None
    m = _ALTIMETER_RE.search(raw_ob)
    if m:
        return int(m.group(1)) / 100.0 * HPA_PER_INHG
    m = _QNH_RE.search(raw_ob)
    return float(m.group(1)) if m else None


def parse_metar(payload, station: str = "") -> Weather | None:
    """Parse the API's JSON list. None if there is no usable observation."""
    if not isinstance(payload, list) or not payload:
        return None
    ob = None
    for item in payload:
        if isinstance(item, dict) and (not station or item.get("icaoId") == station):
            if ob is None or (_num(item.get("obsTime")) or 0) > (_num(ob.get("obsTime")) or 0):
                ob = item
    if ob is None:
        return None

    qnh = _num(ob.get("altim"))
    if qnh is not None and 25.0 <= qnh <= 33.0:       # inHg, just in case
        qnh *= HPA_PER_INHG
    if not is_valid_qnh(qnh):
        qnh = qnh_from_raw(ob.get("rawOb"))
    if not is_valid_qnh(qnh):
        qnh = None

    wdir = _num(ob.get("wdir"))            # the API sends "VRB" for variable
    cover = ob.get("cover") if isinstance(ob.get("cover"), str) else ""
    cat = ob.get("fltCat") if isinstance(ob.get("fltCat"), str) else ""
    return Weather(
        station=str(ob.get("icaoId") or station),
        qnh_hpa=qnh,
        wind_dir_deg=wdir if wdir is not None and 0 <= wdir <= 360 else None,
        wind_kt=_num(ob.get("wspd")),
        gust_kt=_num(ob.get("wgst")),
        temp_c=_num(ob.get("temp")),
        cover=cover[:4].upper(),
        flight_category=cat[:4].upper(),
        obs_time=_num(ob.get("obsTime")) or 0.0,
    )
