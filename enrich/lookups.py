"""Local enrichment: airline names and aircraft type names from bundled CSVs.

Costs zero API calls. CSVs live in data/ and ship with the app.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from config import DATA_DIR

log = logging.getLogger("enrich")


def _load_csv_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            for row in reader:
                if not row or not row[0] or row[0].lstrip().startswith("#"):
                    continue  # blank or comment line
                if len(row) >= 2 and row[0]:
                    out[row[0].strip().upper()] = row[1].strip()
    except FileNotFoundError:
        log.warning("lookup file missing: %s (enrichment will be blank)", path)
    return out


@dataclass(frozen=True)
class Lookups:
    airlines: dict[str, str]
    types: dict[str, str]

    @classmethod
    def load(cls) -> "Lookups":
        airlines = _load_csv_map(DATA_DIR / "airlines.csv")
        overrides = _load_csv_map(DATA_DIR / "overrides.csv")
        airlines.update(overrides)  # user overrides win over OpenFlights
        types = _load_csv_map(DATA_DIR / "aircraft_types.csv")
        log.info("loaded %d airlines (%d overrides), %d aircraft types",
                 len(airlines), len(overrides), len(types))
        return cls(airlines=airlines, types=types)

    def airline_for_callsign(self, callsign: str) -> str:
        """ICAO callsigns are PREFIX + flight number, e.g. UAL123 -> United Airlines.

        GA registrations (N123AB) won't match a 3-letter alpha prefix and
        correctly fall through to "".
        """
        cs = callsign.strip().upper()
        if len(cs) >= 4 and cs[:3].isalpha() and cs[3].isdigit():
            return self.airlines.get(cs[:3], "")
        return ""

    def type_name(self, code: str) -> str:
        return self.types.get(code.strip().upper(), "")
