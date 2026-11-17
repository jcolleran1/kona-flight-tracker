import dataclasses
import datetime as dt

import pytest

from config import PROJECT_ROOT, ConfigError, load_config
from render.timeutil import (EARLIEST_PLAUSIBLE, clock_is_set, fmt_clock, in_quiet_hours,
                             local_now, zone)

SHIPPED = (PROJECT_ROOT / "config.toml").read_text()


@pytest.fixture
def write(tmp_path):
    def _write(text, local=None):
        p = tmp_path / "config.toml"
        p.write_text(text)
        if local is not None:
            (tmp_path / "config.local.toml").write_text(local)
        return p
    return _write


def test_shipped_config_is_ready_for_the_pi(cfg):
    assert "replay" not in cfg.sources, "config.toml must not be committed pointing at replay"
    assert (cfg.width, cfg.height, cfg.fullscreen) == (1920, 1080, True)
    assert cfg.timezone == "Pacific/Honolulu"
    # polling stays far inside the 1 request/second the feeds allow
    assert cfg.interval_seconds >= 2.0
    assert cfg.enrich_min_interval_seconds >= 1.0


def test_decisions_from_plan(cfg):
    # 1: home point stays KOA.  2: knots + statute miles.
    assert cfg.lat == pytest.approx(19.7388, abs=0.01)
    assert cfg.lon == pytest.approx(-156.0456, abs=0.01)
    assert (cfg.airport_iata, cfg.airport_icao) == ("KOA", "PHKO")
    assert (cfg.speed_units, cfg.distance_units, cfg.altitude_units) == ("kt", "mi", "ft")
    assert cfg.is_home_airport("koa") and cfg.is_home_airport("PHKO")
    assert not cfg.is_home_airport("HNL") and not cfg.is_home_airport("")


def test_map_bounds_contain_home(cfg):
    assert cfg.map_lat_min < cfg.lat < cfg.map_lat_max
    assert cfg.map_lon_min < cfg.lon < cfg.map_lon_max


@pytest.mark.parametrize("old,new,needle", [
    ('speed_units = "kt"', 'speed_units = "furlongs"', "speed_units"),
    ("interval_seconds = 5 ", "interval_seconds = 0.2 ", "interval_seconds"),
    ("fps = 6", 'fps = "fast"', "fps"),
    ("fullscreen = true", 'fullscreen = "yes"', "fullscreen"),
    ('start = "22:00"', 'start = "10pm"', "start"),
    ('sources = ["airplanes_live", "adsb_fi"]', 'sources = ["flightradar"]', "flightradar"),
    ('sources = ["airplanes_live", "adsb_fi"]', 'sources = ["replay", "adsb_fi"]', "replay"),
    ("lat_max = 20.5000", "lat_max = 10.0", "lat_min"),
    ("lat = 19.7388", "lat = 197.388", "lat"),
])
def test_bad_values_give_a_readable_error(write, old, new, needle):
    assert old in SHIPPED
    with pytest.raises(ConfigError) as e:
        load_config(write(SHIPPED.replace(old, new)))
    assert needle in str(e.value)


def test_broken_toml_is_a_config_error(write):
    with pytest.raises(ConfigError):
        load_config(write("[display\nwidth = "))


def test_minimal_file_gets_defaults(write):
    cfg = load_config(write("[location]\nlat = 19.7\nlon = -156.0\n"))
    assert cfg.sources == ("airplanes_live",) and cfg.interval_seconds == 5
    assert cfg.quiet_start == dt.time(22, 0)
    with pytest.raises(ConfigError):
        load_config(write("[display]\nfps = 6\n"))        # no location at all


def test_local_overlay_overrides_single_keys(write):
    path = write(SHIPPED, local='[display]\nmax_rows = 4\n[fetch]\nsources = ["adsb_fi"]\n')
    cfg = load_config(path)
    assert cfg.max_rows == 4 and cfg.sources == ("adsb_fi",)
    assert cfg.width == 1920                                  # untouched keys survive
    assert load_config(path, local=False).max_rows == 7


def test_old_style_config_still_loads(write):
    old = ('[location]\nlat = 19.7\nlon = -156.0\n[fetch]\nsource = "adsb_fi"\n'
           '[display]\nunits = "metric"\n')
    cfg = load_config(write(old))
    assert cfg.sources == ("adsb_fi",) and cfg.source == "adsb_fi"
    assert (cfg.speed_units, cfg.distance_units, cfg.altitude_units) == ("kmh", "km", "m")


# ---- clock and quiet hours ----

def test_quiet_hours_cross_midnight(cfg):
    day = dt.date(2026, 11, 16)

    def at(h, m=0):
        return dt.datetime.combine(day, dt.time(h, m))

    assert in_quiet_hours(cfg, at(23)) and in_quiet_hours(cfg, at(2, 30))
    assert in_quiet_hours(cfg, at(6, 59)) and in_quiet_hours(cfg, at(22))
    assert not in_quiet_hours(cfg, at(7)) and not in_quiet_hours(cfg, at(12))
    assert not in_quiet_hours(cfg, at(21, 59))
    daytime = dataclasses.replace(cfg, quiet_start=dt.time(9), quiet_end=dt.time(17))
    assert in_quiet_hours(daytime, at(12)) and not in_quiet_hours(daytime, at(18))
    assert not in_quiet_hours(dataclasses.replace(cfg, quiet_enabled=False), at(23))


def test_clock_uses_the_configured_zone_not_the_systems(cfg):
    # 2026-11-17 02:00:00 UTC is 4 pm the previous day in Hawaii (UTC-10, no DST)
    epoch = dt.datetime(2026, 11, 17, 2, 0, tzinfo=dt.timezone.utc).timestamp()
    now = local_now(cfg, epoch)
    assert (now.month, now.day, now.hour, now.minute) == (11, 16, 16, 0)
    july = dt.datetime(2026, 7, 4, 2, 0, tzinfo=dt.timezone.utc).timestamp()
    assert local_now(cfg, july).hour == 16                   # no daylight saving


def test_honolulu_works_even_without_a_tz_database(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_zoneinfo(name, *a, **k):
        if name == "zoneinfo":
            raise ImportError("no tzdata")
        return real_import(name, *a, **k)
    zone.cache_clear()
    monkeypatch.setattr(builtins, "__import__", no_zoneinfo)
    tz = zone("Pacific/Honolulu")
    assert tz.utcoffset(None) == dt.timedelta(hours=-10)
    assert zone("Mars/Olympus_Mons") is None
    zone.cache_clear()


def test_unset_clock_is_detected_and_never_triggers_quiet_hours(cfg, monkeypatch):
    assert clock_is_set(EARLIEST_PLAUSIBLE + 1)
    assert not clock_is_set(0) and not clock_is_set(1_600_000_000)     # 2020: stale RTC-less boot
    import render.timeutil as tu
    monkeypatch.setattr(tu.time, "time", lambda: 1_600_000_000.0)
    always = dataclasses.replace(cfg, quiet_start=dt.time(0, 0), quiet_end=dt.time(23, 59))
    assert not in_quiet_hours(always)


def test_clock_formats():
    t = dt.datetime(2026, 11, 16, 14, 5, 9)
    assert fmt_clock(t, True) == "14:05" and fmt_clock(t, True, True) == "14:05:09"
    assert fmt_clock(t, False) == "2:05 PM"
    assert fmt_clock(t.replace(hour=0), False) == "12:05 AM"
    assert fmt_clock(t.replace(hour=12), False, True) == "12:05:09 PM"
