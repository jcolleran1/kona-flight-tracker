import datetime as dt

import pytest

from config import PROJECT_ROOT, load_config


def test_shipped_config_loads_and_is_not_left_on_replay(cfg):
    assert cfg.source != "replay", "config.toml must not be committed pointing at replay"
    assert cfg.width == 1920 and cfg.height == 1080
    assert cfg.fullscreen is True
    # polling stays far inside the 1 request/second the feeds allow
    assert cfg.interval_seconds >= 2.0


def test_home_point_is_kona(cfg):
    assert cfg.lat == pytest.approx(19.7388, abs=0.01)
    assert cfg.lon == pytest.approx(-156.0456, abs=0.01)


def test_map_bounds_contain_home(cfg):
    assert cfg.map_lat_min < cfg.lat < cfg.map_lat_max
    assert cfg.map_lon_min < cfg.lon < cfg.map_lon_max


def test_bad_units_rejected(tmp_path):
    text = (PROJECT_ROOT / "config.toml").read_text().replace('units = "imperial"',
                                                              'units = "furlongs"')
    assert "furlongs" in text
    p = tmp_path / "config.toml"
    p.write_text(text)
    with pytest.raises(ValueError):
        load_config(p)


def test_quiet_hours_cross_midnight(cfg):
    from render.renderer import in_quiet_hours
    assert cfg.quiet_start == "22:00" and cfg.quiet_end == "07:00"
    day = dt.date(2026, 11, 16)

    def at(h, m=0):
        return dt.datetime.combine(day, dt.time(h, m))

    assert in_quiet_hours(cfg, at(23))
    assert in_quiet_hours(cfg, at(2, 30))
    assert in_quiet_hours(cfg, at(6, 59))
    assert not in_quiet_hours(cfg, at(7))
    assert not in_quiet_hours(cfg, at(12))
    assert not in_quiet_hours(cfg, at(21, 59))
    assert in_quiet_hours(cfg, at(22))
