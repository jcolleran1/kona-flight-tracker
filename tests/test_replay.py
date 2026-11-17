import gzip
import json

import pytest

from fetch.replay import ReplaySource, load_recording, resolve_recording
from fetch.sources import FetchError


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _write(path, lines, gz=True):
    text = "\n".join(json.dumps(x) if not isinstance(x, str) else x for x in lines) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _frame(t, hexes):
    return {"t": t, "source": "x", "data": {"now": int(t * 1000), "ac": [{"hex": h} for h in hexes]}}


def test_bundled_scenario_loads():
    meta, frames = load_recording(resolve_recording(None))
    assert len(frames) == 181
    assert frames[0][0] < frames[-1][0]
    assert meta["qnh_hpa"] == pytest.approx(1017.9)
    assert "ASA1121" in meta["routes"]


def test_plays_a_sequence_in_order_and_loops(tmp_path):
    path = _write(tmp_path / "r.jsonl.gz", [
        {"meta": {"qnh_hpa": 1015.0}},
        _frame(100.0, ["a"]), _frame(105.0, ["a", "b"]), _frame(110.0, ["b"]),
    ])
    clock = FakeClock()
    src = ReplaySource(recording=str(path), clock=clock)
    assert src.meta == {"qnh_hpa": 1015.0}
    assert src.duration == pytest.approx(15.0)       # 10 s of frames + one gap

    def hexes():
        return [a["hex"] for a in src.fetch().aircraft]

    assert hexes() == ["a"]
    clock.t += 4.9
    assert hexes() == ["a"]
    clock.t += 0.2          # 5.1 s in
    assert hexes() == ["a", "b"]
    clock.t += 5.0          # 10.1 s in
    assert hexes() == ["b"]
    clock.t += 5.0          # 15.1 s in: wrapped to the start
    assert hexes() == ["a"]


def test_reports_how_old_the_frame_is(tmp_path):
    path = _write(tmp_path / "r.jsonl", [_frame(0.0, ["a"]), _frame(5.0, ["a"])], gz=False)
    clock = FakeClock()
    src = ReplaySource(recording=str(path), clock=clock)
    clock.t += 3.0
    poll = src.fetch()
    assert poll.age_seconds == pytest.approx(3.0)
    assert poll.source == "replay"


def test_speed_and_start_offset(tmp_path):
    path = _write(tmp_path / "r.jsonl", [_frame(float(t), [str(t)]) for t in range(0, 50, 5)],
                  gz=False)
    clock = FakeClock()
    src = ReplaySource(recording=str(path), clock=clock, speed=2.0, start_at=20.0)
    assert src.fetch().aircraft[0]["hex"] == "20"
    clock.t += 5.0          # 10 s of recording at 2x
    assert src.fetch().aircraft[0]["hex"] == "30"


def test_torn_last_line_is_skipped(tmp_path):
    path = _write(tmp_path / "r.jsonl", [_frame(0.0, ["a"]), '{"t": 5.0, "source": "x", "da'],
                  gz=False)
    _meta, frames = load_recording(path)
    assert len(frames) == 1


def test_empty_or_missing_recording_is_a_fetch_error(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(FetchError):
        ReplaySource(recording=str(empty))
    with pytest.raises(FetchError):
        ReplaySource(recording=str(tmp_path / "nope.jsonl"))


def test_filter_to_one_source(tmp_path):
    lines = [{"t": 0.0, "source": "one", "data": {"ac": [{"hex": "1"}]}},
             {"t": 1.0, "source": "two", "data": {"ac": [{"hex": "2"}]}},
             {"t": 5.0, "source": "one", "data": {"ac": [{"hex": "3"}]}}]
    path = _write(tmp_path / "r.jsonl", lines, gz=False)
    clock = FakeClock()
    src = ReplaySource(recording=str(path), clock=clock, only_source="one")
    clock.t += 2.0
    assert src.fetch().aircraft[0]["hex"] == "1"


def test_readsb_style_frames_use_the_aircraft_key(tmp_path):
    path = _write(tmp_path / "r.jsonl",
                  [{"t": 0.0, "source": "local", "data": {"now": 1.0, "aircraft": [{"hex": "9"}]}}],
                  gz=False)
    assert ReplaySource(recording=str(path)).fetch().aircraft == [{"hex": "9"}]
