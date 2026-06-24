"""Configuration loading. Single source of truth: config.toml at project root."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


def resolve_asset(name: str) -> Path:
    """Resolve an asset path: absolute paths used as-is, bare names looked up
    in data/. Lets config.toml point at /home/you/kona-tracker/my_map.png OR
    just 'big_island_map.png' bundled in data/."""
    p = Path(name).expanduser()
    return p if p.is_absolute() else DATA_DIR / name


@dataclass(frozen=True)
class Config:
    # location
    lat: float
    lon: float
    # fetch
    source: str
    radius_nm: float
    interval_seconds: float
    timeout_seconds: float
    stale_after_seconds: float
    watchdog_seconds: float
    # display
    width: int
    height: int
    fps: int
    units: str
    max_rows: int
    fullscreen: bool
    # quiet hours
    quiet_enabled: bool
    quiet_start: str
    quiet_end: str
    quiet_brightness: float
    # map (right panel)
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


def load_config(path: Path | None = None) -> Config:
    path = path or PROJECT_ROOT / "config.toml"
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    loc, fetch = raw["location"], raw["fetch"]
    disp, quiet = raw["display"], raw["quiet_hours"]
    mp = raw.get("map", {})

    units = disp.get("units", "imperial")
    if units not in ("imperial", "metric"):
        raise ValueError(f"display.units must be 'imperial' or 'metric', got {units!r}")

    return Config(
        lat=float(loc["lat"]),
        lon=float(loc["lon"]),
        source=fetch.get("source", "airplanes_live"),
        radius_nm=float(fetch.get("radius_nm", 30)),
        interval_seconds=max(2.0, float(fetch.get("interval_seconds", 20))),
        timeout_seconds=float(fetch.get("timeout_seconds", 10)),
        stale_after_seconds=float(fetch.get("stale_after_seconds", 90)),
        watchdog_seconds=float(fetch.get("watchdog_seconds", 300)),
        width=int(disp.get("width", 1920)),
        height=int(disp.get("height", 1080)),
        fps=int(disp.get("fps", 6)),
        units=units,
        max_rows=int(disp.get("max_rows", 8)),
        fullscreen=bool(disp.get("fullscreen", True)),
        quiet_enabled=bool(quiet.get("enabled", True)),
        quiet_start=quiet.get("start", "22:00"),
        quiet_end=quiet.get("end", "07:00"),
        quiet_brightness=float(quiet.get("brightness", 0.30)),
        map_enabled=bool(mp.get("enabled", True)),
        map_image=str(mp.get("image", "big_island_map.png")),
        map_lat_min=float(mp.get("lat_min", 18.85)),
        map_lat_max=float(mp.get("lat_max", 20.25)),
        map_lon_min=float(mp.get("lon_min", -156.35)),
        map_lon_max=float(mp.get("lon_max", -155.35)),
        plane_icon=str(mp.get("plane_icon", "plane.png")),
        plane_size=int(mp.get("plane_size", 34)),
        show_range_rings=bool(mp.get("show_range_rings", True)),
        range_ring_nm=float(mp.get("range_ring_nm", 10)),
        show_home=bool(mp.get("show_home", True)),
        show_labels=bool(mp.get("show_labels", True)),
    )
