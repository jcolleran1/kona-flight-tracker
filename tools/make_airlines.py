#!/usr/bin/env python3
"""Regenerate data/airlines.csv (icao,iata,name) from OpenFlights airlines.dat.

    python3 tools/make_airlines.py                 # downloads airlines.dat
    python3 tools/make_airlines.py --from airlines.dat

OpenFlights data is (c) OpenFlights contributors, Open Database License (ODbL).
The dump is no longer maintained and lists thousands of airlines that stopped
flying years ago, so only rows it marks active are kept, and anything this
tracker actually sees in Hawaii should be pinned in data/overrides.csv, which
always wins.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_DIR  # noqa: E402

URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
ICAO_RE = re.compile(r"^[A-Z]{3}$")
IATA_RE = re.compile(r"^[A-Z0-9]{2}$")


def build(text: str) -> list[tuple[str, str, str]]:
    rows: dict[str, tuple[str, str, str]] = {}
    # columns: id, name, alias, iata, icao, callsign, country, active
    for rec in csv.reader(io.StringIO(text)):
        if len(rec) < 8:
            continue
        name, iata, icao, active = rec[1].strip(), rec[3].strip().upper(), \
            rec[4].strip().upper(), rec[7].strip().upper()
        if active != "Y" or not ICAO_RE.match(icao) or not name or name == "\\N":
            continue
        if not IATA_RE.match(iata) or iata.isdigit():
            iata = ""
        prev = rows.get(icao)
        # one row per ICAO code: prefer the one that also has an IATA code
        if prev is None or (not prev[1] and iata):
            rows[icao] = (icao, iata, name)
    return sorted(rows.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", type=Path, help="local airlines.dat")
    args = ap.parse_args()
    if args.src:
        text = args.src.read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(URL, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    rows = build(text)
    out = DATA_DIR / "airlines.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["icao", "iata", "name"])
        w.writerows(rows)
    print(f"wrote {out}: {len(rows)} active airlines, "
          f"{sum(1 for r in rows if r[1])} with an IATA code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
