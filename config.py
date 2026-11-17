"""Configuration loading.

Single source of truth: config.toml at the project root. An optional
config.local.toml next to it (git-ignored) overrides individual keys, so
settings changed on the Pi survive `git pull` and private values stay out of
the repo.

Every value is validated here, once, at startup. A typo in the file produces a
one-line message naming the section and key instead of a traceback from deep
inside the render loop.
"""
from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"

SPEED_UNITS = ("kt", "mph", "kmh")
DISTANCE_UNITS = ("mi", "nm", "km")
ALTITUDE_UNITS = ("ft", "m")


class ConfigError(ValueError):
    pass


def resolve_asset(name: str) -> Path:
    """Absolute paths are used as-is; bare names are looked up in data/."""
    p = Path(name).expanduser()
    return p if p.is_absolute() else DATA_DIR / name


@dataclass(frozen=True)
class Config:
    # --- location: the point distances and bearings are measured from ---
    lat: float
    lon: float
    # --- airport: what counts as "arriving" and "departing" ---
    airport_icao: str
    airport_iata: str
    airport_name: str
    airport_elevation_ft: float
    # --- fetch ---
    sources: tuple[str, ...]
    radius_nm: float
    interval_seconds: float
    timeout_seconds: float
    max_position_age_seconds: float
    stale_after_seconds: float
    watchdog_seconds: float
    show_anonymous: bool
    replay_file: str
    replay_speed: float
    # --- enrichment (adsbdb lookups, METAR) ---
    enrich_enabled: bool
    enrich_min_interval_seconds: float
    metar_station: str
    metar_interval_minutes: float
    # --- altitude ---
    transition_altitude_ft: float
    ground_max_altitude_ft: float
    ground_max_speed_kt: float
    # --- movement classification ---
    vertical_rate_threshold_fpm: float
    overflight_min_altitude_ft: float
    feature_dwell_seconds: float
    # --- motion on the map ---
    dead_reckoning: bool
    tween_seconds: float
    trail_seconds: float
    # --- display ---
    width: int
    height: int
    fps: int
    speed_units: str
    distance_units: str
    altitude_units: str
    max_rows: int
    fullscreen: bool
    timezone: str
    clock_24h: bool
    # --- quiet hours ---
    quiet_enabled: bool
    quiet_start: dt.time
    quiet_end: dt.time
    quiet_brightness: float
    # --- map (right panel) ---
    map_enabled: bool
    map_image: str
    map_lat_min: float
    map_lat_max: float
    map_lon_min: float
    map_lon_max: float
    plane_icon: str
    plane_size: int
    show_range_rings: bool
    range_ring_nm: float
    show_home: bool
    show_labels: bool

    # Kept so older call sites and docs that say "cfg.source" keep working.
    @property
    def source(self) -> str:
        return self.sources[0]

    def is_home_airport(self, code: str) -> bool:
        code = (code or "").strip().upper()
        return bool(code) and code in (self.airport_iata, self.airport_icao)


class _Section:
    """Typed, range-checked access to one TOML table."""

    def __init__(self, raw: dict, name: str):
        table = raw.get(name, {})
        if not isinstance(table, dict):
            raise ConfigError(f"[{name}] must be a table")
        self.table, self.name = table, name

    def _where(self, key: str) -> str:
        return f"[{self.name}] {key}"

    def num(self, key, default, lo=None, hi=None, integer=False):
        v = self.table.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(f"{self._where(key)} must be a number, got {v!r}")
        if integer and float(v) != int(v):
            raise ConfigError(f"{self._where(key)} must be a whole number, got {v!r}")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ConfigError(f"{self._where(key)} = {v!r} is outside {lo}..{hi}")
        return int(v) if integer else float(v)

    def flag(self, key, default: bool) -> bool:
        v = self.table.get(key, default)
        if not isinstance(v, bool):
            raise ConfigError(f"{self._where(key)} must be true or false, got {v!r}")
        return v

    def text(self, key, default: str, choices=None) -> str:
        v = self.table.get(key, default)
        if not isinstance(v, str):
            raise ConfigError(f"{self._where(key)} must be text in quotes, got {v!r}")
        if choices and v not in choices:
            raise ConfigError(f"{self._where(key)} must be one of {list(choices)}, got {v!r}")
        return v

    def clock(self, key, default: str) -> dt.time:
        v = self.text(key, default)
        try:
            h, m = v.split(":")
            return dt.time(int(h), int(m))
        except ValueError:
            raise ConfigError(f'{self._where(key)} must look like "22:00", got {v!r}') from None


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name}: {e}") from None


def _sources(fetch: _Section) -> tuple[str, ...]:
    from fetch.sources import SOURCE_NAMES
    raw = fetch.table.get("sources", fetch.table.get("source", ["airplanes_live"]))
    names = [raw] if isinstance(raw, str) else raw
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ConfigError('[fetch] sources must be a list like ["airplanes_live", "adsb_fi"]')
    for n in names:
        if n not in SOURCE_NAMES:
            raise ConfigError(f"[fetch] sources: unknown source {n!r}; options: {SOURCE_NAMES}")
    if len(set(names)) != len(names):
        raise ConfigError("[fetch] sources lists the same source twice")
    if "replay" in names and len(names) > 1:
        raise ConfigError('[fetch] sources: "replay" cannot be mixed with live sources')
    return tuple(names)


def _units(disp: _Section) -> tuple[str, str, str]:
    """Explicit speed/distance/altitude units; the old single `units` key still
    works as a preset for anyone carrying an older config.toml."""
    preset = disp.text("units", "imperial", ("imperial", "metric"))
    d_speed, d_dist, d_alt = ("kmh", "km", "m") if preset == "metric" else ("mph", "mi", "ft")
    return (disp.text("speed_units", d_speed, SPEED_UNITS),
            disp.text("distance_units", d_dist, DISTANCE_UNITS),
            disp.text("altitude_units", d_alt, ALTITUDE_UNITS))


def load_config(path: Path | None = None, local: Path | None | bool = None) -> Config:
    """Load config.toml, overlaid with config.local.toml when present.

    `local=False` skips the overlay (tests check the committed file alone).
    """
    path = path or PROJECT_ROOT / "config.toml"
    raw = _read_toml(path)
    if local is not False:
        local_path = local if isinstance(local, Path) else path.with_name("config.local.toml")
        if local_path.exists():
            raw = _merge(raw, _read_toml(local_path))

    loc, apt = _Section(raw, "location"), _Section(raw, "airport")
    fetch, enrich = _Section(raw, "fetch"), _Section(raw, "enrich")
    alt, cls, motion = _Section(raw, "altitude"), _Section(raw, "classify"), _Section(raw, "motion")
    disp, quiet, mp = _Section(raw, "display"), _Section(raw, "quiet_hours"), _Section(raw, "map")

    if "lat" not in loc.table or "lon" not in loc.table:
        raise ConfigError("[location] needs both lat and lon")
    speed_units, distance_units, altitude_units = _units(disp)

    timezone = disp.text("timezone", "Pacific/Honolulu")
    interval = fetch.num("interval_seconds", 5, 2, 3600)

    cfg = Config(
        lat=loc.num("lat", 0.0, -90, 90),
        lon=loc.num("lon", 0.0, -180, 180),
        airport_icao=apt.text("icao", "PHKO").strip().upper(),
        airport_iata=apt.text("iata", "KOA").strip().upper(),
        airport_name=apt.text("name", "Kona").strip(),
        airport_elevation_ft=apt.num("elevation_ft", 47, -1500, 15000),
        sources=_sources(fetch),
        radius_nm=fetch.num("radius_nm", 40, 1, 250),
        interval_seconds=interval,
        timeout_seconds=fetch.num("timeout_seconds", 8, 1, 60),
        max_position_age_seconds=fetch.num("max_position_age_seconds", 30, 5, 600),
        stale_after_seconds=fetch.num("stale_after_seconds", 30, 5, 3600),
        watchdog_seconds=fetch.num("watchdog_seconds", 300, 30, 86400),
        show_anonymous=fetch.flag("show_anonymous", False),
        replay_file=fetch.text("replay_file", "fixtures/kona_scenario.jsonl.gz"),
        replay_speed=fetch.num("replay_speed", 1.0, 0.05, 60),
        enrich_enabled=enrich.flag("enabled", True),
        enrich_min_interval_seconds=enrich.num("min_interval_seconds", 2.0, 1, 600),
        metar_station=enrich.text("metar_station", "").strip().upper(),
        metar_interval_minutes=enrich.num("metar_interval_minutes", 30, 10, 720),
        transition_altitude_ft=alt.num("transition_altitude_ft", 18000, 0, 60000),
        ground_max_altitude_ft=alt.num("ground_max_altitude_ft", 100, 0, 15000),
        ground_max_speed_kt=alt.num("ground_max_speed_kt", 60, 0, 200),
        vertical_rate_threshold_fpm=cls.num("vertical_rate_threshold_fpm", 250, 50, 2000),
        overflight_min_altitude_ft=cls.num("overflight_min_altitude_ft", 10000, 1000, 60000),
        feature_dwell_seconds=cls.num("feature_dwell_seconds", 20, 0, 600),
        dead_reckoning=motion.flag("dead_reckoning", True),
        tween_seconds=motion.num("tween_seconds", 1.0, 0, 10),
        trail_seconds=motion.num("trail_seconds", 180, 0, 1800),
        width=disp.num("width", 1920, 320, 7680, integer=True),
        height=disp.num("height", 1080, 240, 4320, integer=True),
        fps=disp.num("fps", 6, 1, 60, integer=True),
        speed_units=speed_units,
        distance_units=distance_units,
        altitude_units=altitude_units,
        max_rows=disp.num("max_rows", 8, 0, 40, integer=True),
        fullscreen=disp.flag("fullscreen", True),
        timezone=timezone,
        clock_24h=disp.flag("clock_24h", True),
        quiet_enabled=quiet.flag("enabled", True),
        quiet_start=quiet.clock("start", "22:00"),
        quiet_end=quiet.clock("end", "07:00"),
        quiet_brightness=quiet.num("brightness", 0.30, 0.0, 1.0),
        map_enabled=mp.flag("enabled", True),
        map_image=mp.text("image", "big_island_map.png"),
        map_lat_min=mp.num("lat_min", 18.85, -90, 90),
        map_lat_max=mp.num("lat_max", 20.25, -90, 90),
        map_lon_min=mp.num("lon_min", -156.35, -180, 180),
        map_lon_max=mp.num("lon_max", -155.35, -180, 180),
        plane_icon=mp.text("plane_icon", "plane.png"),
        plane_size=mp.num("plane_size", 34, 8, 200, integer=True),
        show_range_rings=mp.flag("show_range_rings", True),
        range_ring_nm=mp.num("range_ring_nm", 10, 1, 250),
        show_home=mp.flag("show_home", True),
        show_labels=mp.flag("show_labels", True),
    )
    if cfg.map_lat_min >= cfg.map_lat_max or cfg.map_lon_min >= cfg.map_lon_max:
        raise ConfigError("[map] lat_min/lon_min must be smaller than lat_max/lon_max")
    return cfg
