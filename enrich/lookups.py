"""Local enrichment from the bundled CSVs: airline names, IATA flight numbers,
brand rules, aircraft type names. Costs zero network calls.

The rule that shapes everything here is "blank beats wrong":
  * a callsign is only rewritten as a flight number when it has the plain
    AIRLINE + DIGITS shape; anything else is shown exactly as broadcast
  * an airline the hand-checked override list doesn't know is trusted less
    than one it does (see Airline.curated)
  * text from outside sources is reduced to characters the font can draw
"""
from __future__ import annotations

import csv
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from config import DATA_DIR

log = logging.getLogger("enrich")

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")
# AIRLINE (3 letters) + flight number (1-4 digits, leading zeros allowed)
AIRLINE_FLIGHT_RE = re.compile(r"^([A-Z]{3})0*(\d{1,4})$")
# anything that starts with a 3-letter airline prefix and then a digit
AIRLINE_PREFIX_RE = re.compile(r"^([A-Z]{3})\d[A-Z0-9]*$")
REGISTRATION_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,8}$")


def clean_text(value, max_len: int = 48) -> str:
    """Reduce outside text to plain printable ASCII the bundled font can draw.

    Accented letters lose their accents (Keāhole -> Keahole); anything else
    outside ASCII is dropped rather than rendered as an empty box.
    """
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if 32 <= ord(ch) < 127)
    return " ".join(text.split())[:max_len].strip()


def clean_callsign(value) -> str:
    """Trimmed, upper-cased callsign, or "" if it isn't a believable one
    (transponders that haven't been set send things like '@@@@@@@@')."""
    if not isinstance(value, str):
        return ""
    cs = value.strip().upper()
    return cs if CALLSIGN_RE.match(cs) and not cs.isdigit() else ""


def clean_registration(value) -> str:
    if not isinstance(value, str):
        return ""
    reg = value.strip().upper()
    return reg if REGISTRATION_RE.match(reg) else ""


def airline_prefix(callsign: str) -> str:
    """'UAL' for UAL1759 or FDY539SA; '' for N739HP or anything else."""
    m = AIRLINE_PREFIX_RE.match(callsign or "")
    return m.group(1) if m else ""


@dataclass(frozen=True)
class Airline:
    icao: str
    iata: str
    name: str
    curated: bool        # from the hand-checked overrides, not the OpenFlights dump


@dataclass(frozen=True)
class BrandRule:
    prefix: str
    registration: re.Pattern | None
    owner: str
    airline: str

    def matches(self, prefix: str, registration: str, owner: str) -> bool:
        if self.prefix != prefix:
            return False
        if self.registration is not None and registration and self.registration.search(registration):
            return True
        return bool(self.owner) and self.owner in owner.upper()


def _rows(path: Path):
    """CSV rows as lists, skipping the header, blanks and # comments."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if not row or not row[0].strip() and len(row) == 1:
                    continue
                if row[0].lstrip().startswith("#"):
                    continue
                yield [c.strip() for c in row]
    except FileNotFoundError:
        log.warning("lookup file missing: %s (enrichment will be blank)", path)


def _load_airlines(path: Path, curated: bool) -> dict[str, Airline]:
    """icao,iata,name  (the older two-column icao,name layout still loads)."""
    out: dict[str, Airline] = {}
    for row in _rows(path):
        icao = row[0].upper()
        if len(icao) != 3 or not icao.isalpha():
            continue
        if len(row) >= 3:
            iata, name = row[1].upper(), row[2]
        elif len(row) == 2:
            iata, name = "", row[1]
        else:
            continue
        out[icao] = Airline(icao, iata, clean_text(name), curated)
    return out


def _load_brand_rules(path: Path) -> tuple[BrandRule, ...]:
    rules = []
    for row in _rows(path):
        if len(row) < 4 or not row[3]:
            continue
        prefix, pattern, owner, airline = row[0].upper(), row[1], row[2].upper(), row[3]
        if not pattern and not owner:
            continue
        try:
            compiled = re.compile(pattern) if pattern else None
        except re.error as e:
            log.warning("brand_rules.csv: bad pattern %r skipped (%s)", pattern, e)
            continue
        rules.append(BrandRule(prefix, compiled, owner, clean_text(airline)))
    return tuple(rules)


def _load_types(path: Path) -> dict[str, str]:
    return {row[0].upper(): clean_text(row[1]) for row in _rows(path) if len(row) >= 2 and row[0]}


@dataclass(frozen=True)
class Lookups:
    airlines: dict[str, Airline]
    types: dict[str, str]
    brand_rules: tuple[BrandRule, ...] = ()

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "Lookups":
        airlines = _load_airlines(data_dir / "airlines.csv", curated=False)
        overrides = _load_airlines(data_dir / "overrides.csv", curated=True)
        airlines.update(overrides)       # hand-checked rows win
        types = _load_types(data_dir / "aircraft_types.csv")
        rules = _load_brand_rules(data_dir / "brand_rules.csv")
        log.info("loaded %d airlines (%d hand-checked), %d aircraft types, %d brand rules",
                 len(airlines), len(overrides), len(types), len(rules))
        return cls(airlines=airlines, types=types, brand_rules=rules)

    # ---- airlines ----

    def airline(self, prefix: str) -> Airline | None:
        """The airline for an ICAO prefix. A hand-checked row with a blank name
        means "known wrong upstream, print nothing" and returns None."""
        a = self.airlines.get((prefix or "").upper())
        return a if a and a.name else None

    def airline_for_callsign(self, callsign: str) -> Airline | None:
        return self.airline(airline_prefix(callsign))

    def brand(self, callsign: str, registration: str, owner: str) -> str:
        """Airline name decided by the airframe (see data/brand_rules.csv), or ''."""
        prefix = airline_prefix(callsign)
        if prefix and not any(r.prefix == prefix for r in self.brand_rules):
            # an airline callsign with no rule of its own: owner-only rules
            # don't get to rename someone else's flight
            return ""
        key = prefix if any(r.prefix == prefix for r in self.brand_rules) else ""
        for rule in self.brand_rules:
            if rule.matches(key, registration or "", owner or ""):
                return rule.airline
        return ""

    # ---- flight numbers ----

    def flight_number(self, callsign: str, callsign_iata: str = "") -> str:
        """What the wall shows as the flight: UA1759 for UAL1759.

        `callsign_iata` is adsbdb's own translation. It is used for airlines
        the hand-checked list doesn't cover, and only when it is exactly a
        two-character airline code followed by the same number (adsbdb sends
        a bare "692" for operators with no IATA code). Anything that isn't a
        plain airline + digits callsign is returned unchanged.
        """
        if not callsign:
            return ""
        m = AIRLINE_FLIGHT_RE.match(callsign)
        if not m:
            return callsign
        prefix, number = m.group(1), m.group(2)
        entry = self.airlines.get(prefix)
        if entry is not None and entry.curated:
            # Hand-checked: its IATA code is the answer, and a blank one means
            # "this operator has no flight numbers, show the callsign".
            return f"{entry.iata}{number}" if entry.iata else callsign
        theirs = (callsign_iata or "").strip().upper()
        code = theirs[:-len(number)] if theirs.endswith(number) else ""
        if len(code) == 2 and code.isalnum() and not code.isdigit():
            return theirs                 # adsbdb's airline table is the more current one
        if entry is not None and entry.iata:
            return f"{entry.iata}{number}"
        return callsign

    # ---- aircraft types ----

    def type_name(self, code: str) -> str:
        return self.types.get((code or "").strip().upper(), "")
