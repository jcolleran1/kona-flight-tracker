"""Phase 4: the things that keep an unattended wall running."""
import io
import logging
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import appliance
from appliance import (EXIT_ALREADY_RUNNING, EXIT_OK, EXIT_RESTART, MemoryWatch, Report,
                       SingleInstance, selftest)

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


# ---- single instance ----

def test_second_instance_is_refused_and_first_survives(tmp_path):
    path = tmp_path / "wall.lock"
    first, second = SingleInstance(path), SingleInstance(path)
    assert first.acquire()
    assert not second.acquire()
    assert second.holder_pid() == os.getpid()
    first.release()
    assert second.acquire()                 # free again the moment the holder lets go
    second.release()


def test_lock_dies_with_its_process(tmp_path):
    path = tmp_path / "wall.lock"
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            f"from appliance import SingleInstance; from pathlib import Path; "
            f"assert SingleInstance(Path({str(path)!r})).acquire(); import os; os._exit(9)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 9    # "crashed" while holding it
    assert SingleInstance(path).acquire()   # no stale lock left behind


def test_lock_lives_in_the_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert appliance.lock_path() == tmp_path / "kona-flight-tracker.lock"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    assert appliance.lock_path().parent == Path("/tmp")


def test_running_the_app_twice_exits_3(tmp_path):
    env = dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path), SDL_VIDEODRIVER="dummy")
    holder = SingleInstance(tmp_path / "kona-flight-tracker.lock")
    assert holder.acquire()
    try:
        done = subprocess.run([sys.executable, str(ROOT / "app.py")], env=env, cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
    finally:
        holder.release()
    assert done.returncode == EXIT_ALREADY_RUNNING == 3
    assert "already running" in done.stdout


# ---- memory ----

def test_memory_is_logged_hourly_and_ceiling_asks_for_restart(caplog):
    t = [0.0]
    readings = iter([80.0, 95.0, 410.0])
    watch = MemoryWatch(300.0, interval=3600.0, clock=lambda: t[0], reader=lambda: next(readings))
    log = logging.getLogger("test.memory")
    with caplog.at_level(logging.INFO, logger="test.memory"):
        assert watch.check(log) is False                    # not due yet: reader untouched
        t[0] = 61.0
        assert watch.check(log, aircraft=7) is False        # 80 MB
        t[0] = 120.0
        assert watch.check(log) is False                    # same hour: no read
        t[0] = 61.0 + 3600.0
        assert watch.check(log) is False                    # 95 MB
        t[0] = 61.0 + 7200.0
        assert watch.check(log) is True                     # 410 MB > 300
    assert len(caplog.records) == 3 and "rss=80 MB" in caplog.text and "aircraft=7" in caplog.text


def test_memory_watch_disabled_or_unreadable():
    t = [100.0]
    off = MemoryWatch(0.0, clock=lambda: t[0], reader=lambda: 9999.0)
    t[0] = 200.0
    assert off.check(logging.getLogger("x")) is False       # ceiling 0 = never restart
    blind = MemoryWatch(300.0, clock=lambda: t[0], reader=lambda: None)
    t[0] = 500.0
    assert blind.check(logging.getLogger("x")) is False


def test_rss_readable_on_linux():
    if not Path("/proc/self/status").exists():
        pytest.skip("no /proc")
    assert 5 < appliance.rss_mb() < 2000
    assert appliance.available_mb() > 0


def test_whole_pipeline_stays_small_in_memory(cfg):
    """A day of polling must not grow the process: caches are bounded."""
    import dataclasses
    import gc
    sys.path.insert(0, str(TOOLS))
    import preview
    from fetch.worker import FetchLoop
    from state import SharedState
    if appliance.rss_mb() is None:
        pytest.skip("no /proc")
    rcfg = dataclasses.replace(cfg, sources=("replay",))
    clock = preview.SimClock()
    loop = FetchLoop(rcfg, SharedState(), sources=[preview.LimitedReplay(clock, None)], clock=clock)

    def run(seconds):
        end = clock.t + seconds
        while clock.t < end:
            for st in loop.sources:
                st.next_due = clock.t
            loop.tick()
            clock.t += 5.0
    run(1800)
    gc.collect()
    before = appliance.rss_mb()
    run(6 * 3600)                                            # six more hours of polls
    gc.collect()
    assert appliance.rss_mb() - before < 8.0
    assert len(loop.tracker._mem) < 40


# ---- self-test ----

def test_selftest_offline_passes_on_the_shipped_config(cfg):
    out = io.StringIO()
    assert selftest(cfg, offline=True, out=out) == 0
    text = out.getvalue()
    for needle in ("python", "pygame", "map image", "map shape", "wall timezone",
                   "instance lock", "OK: 0 failed"):
        assert needle in text
    assert "[FAIL" not in text


def test_selftest_fails_loudly_on_a_missing_map_and_bad_timezone(cfg):
    import dataclasses
    out = io.StringIO()
    bad = dataclasses.replace(cfg, map_image="no_such_map.png", timezone="Mars/Olympus_Mons")
    assert selftest(bad, offline=True, out=out) == 1
    text = out.getvalue()
    assert "[FAIL] map image" in text and "[FAIL] timezone" in text and "FAILED: 2 failed" in text


def test_selftest_network_checks(cfg, monkeypatch):
    import fetch.http as http_mod
    import fetch.sources as sources_mod
    from fetch.http import FetchError
    monkeypatch.setattr(appliance.time, "sleep", lambda s: None)

    def fake(url, timeout=10.0):
        if "airplanes.live" in url:
            return {"ac": [{"hex": "a", "lat": 1.0, "lon": 2.0}, {"hex": "b"}], "now": 1}
        if "adsb.fi" in url:
            raise FetchError("HTTP 429", status=429)
        if "adsb.lol" in url:
            raise FetchError("connection reset")
        if "aviationweather" in url:
            return [{"icaoId": "PHKO", "altim": 1016.7, "obsTime": 1}]
        raise FetchError("HTTP 404", status=404)             # adsbdb: unknown callsign = reachable
    monkeypatch.setattr(sources_mod, "http_get_json", fake)
    monkeypatch.setattr(http_mod, "http_get_json", fake)
    out = io.StringIO()
    assert selftest(cfg, out=out) == 1                       # an ENABLED source (adsb.fi) failed
    text = out.getvalue()
    assert "[PASS] source airplanes_live    1 aircraft" in text
    assert "[FAIL] source adsb_fi" in text
    assert "[WARN] source adsb_lol" in text and "not enabled" in text
    assert "[PASS] METAR altimeter          PHKO 1016.7 hPa (30.02 inHg)" in text
    assert "[PASS] adsbdb routes" in text


def test_report_counts():
    r = Report(io.StringIO())
    r.ok("a"); r.warn("b", "hmm"); r.fail("c", "boom")
    assert (r.failed, r.warned) == (1, 1)
    assert "[FAIL] c" in r.out.getvalue()


def test_exit_codes_are_the_ones_the_launcher_expects():
    assert (EXIT_OK, EXIT_ALREADY_RUNNING, EXIT_RESTART) == (0, 3, 75)
    script = (TOOLS / "run.sh").read_text()
    assert "0|3) exit 0" in script and "75)" in script


# ---- launcher (tools/run.sh), with a fake python3 that exits on a script ----

@pytest.fixture
def sandbox(tmp_path):
    """A copy of tools/ with stub python3 and sleep on PATH."""
    app = tmp_path / "flight-tracker"
    (app / "tools").mkdir(parents=True)
    for name in ("run.sh", "install.sh", "update.sh"):
        shutil.copy(TOOLS / name, app / "tools" / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def stub(name, body):
        p = bin_dir / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    # python3: each run pops the next exit code from a file; "runs" are logged
    stub("python3", f'echo "run $*" >> {tmp_path}/runs\n'
                    f'code=$(head -n 1 {tmp_path}/codes)\n'
                    f'sed -i 1d {tmp_path}/codes\n'
                    f'exit ${{code:-0}}\n')
    stub("sleep", f'echo "$1" >> {tmp_path}/sleeps\n')
    # a journal that works: run the command it was given
    stub("systemd-cat", 'shift 2\nexec "$@"\n')
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home")}
    (tmp_path / "home").mkdir()
    return tmp_path, app, env, stub


def run_launcher(sandbox, codes):
    tmp, app, env, _ = sandbox
    (tmp / "codes").write_text("".join(f"{c}\n" for c in codes))
    for name in ("runs", "sleeps"):
        (tmp / name).unlink(missing_ok=True)
    done = subprocess.run(["/bin/sh", str(app / "tools" / "run.sh")], env=env, timeout=20)
    runs = (tmp / "runs").read_text().splitlines() if (tmp / "runs").exists() else []
    sleeps = (tmp / "sleeps").read_text().split() if (tmp / "sleeps").exists() else []
    return done.returncode, len(runs), sleeps


def test_launcher_restarts_after_a_crash_and_stops_on_a_clean_quit(sandbox):
    code, runs, sleeps = run_launcher(sandbox, [1, 139, 0])
    assert (code, runs) == (0, 3)
    assert sleeps == ["5", "10"]             # backs off between crashes


def test_launcher_restarts_immediately_on_75_and_stops_when_already_running(sandbox):
    code, runs, sleeps = run_launcher(sandbox, [75, 75, 3])
    assert (code, runs) == (0, 3)
    assert sleeps == ["1", "1"]


def test_launcher_backoff_is_capped(sandbox):
    code, runs, sleeps = run_launcher(sandbox, [1] * 8 + [0])
    assert runs == 9
    assert sleeps == ["5", "10", "20", "40", "60", "60", "60", "60"]


def test_launcher_keeps_trying_on_a_broken_config(sandbox):
    code, runs, _ = run_launcher(sandbox, [2, 2, 0])
    assert runs == 3


def test_launcher_still_starts_the_app_when_the_journal_is_unavailable(sandbox):
    tmp, _app, _env, stub = sandbox
    stub("systemd-cat", "exit 1\n")                 # present but broken
    code, runs, _ = run_launcher(sandbox, [1, 0])
    assert (code, runs) == (0, 2)


# ---- install.sh ----

LXSESSION_BEFORE = """@lxpanel --profile LXDE-pi
@pcmanfm --desktop --profile LXDE-pi
@python3 /home/colleran/flight-tracker/app.py
@xscreensaver -no-splash
"""


def install(sandbox):
    _tmp, app, env, _ = sandbox
    return subprocess.run(["/bin/sh", str(app / "tools/install.sh")], env=env, check=True,
                          capture_output=True, text=True).stdout


def autostart_files(sandbox):
    home = Path(sandbox[2]["HOME"])
    labwc = home / ".config/labwc/autostart"
    lx = home / ".config/lxsession/LXDE-pi/autostart"
    labwc.parent.mkdir(parents=True, exist_ok=True)
    lx.parent.mkdir(parents=True, exist_ok=True)
    return labwc, lx


def test_install_removes_the_respawning_lxsession_entry(sandbox):
    """`@python3 .../app.py` in the lxsession autostart starts a second copy
    and, because of the @, restarts it every time the instance lock makes it
    exit. install.sh must take that line out, and only that line."""
    labwc, lx = autostart_files(sandbox)
    lx.write_text(LXSESSION_BEFORE)
    out = install(sandbox)

    after = lx.read_text()
    assert "app.py" not in after                               # removed, not commented out
    assert after == LXSESSION_BEFORE.replace("@python3 /home/colleran/flight-tracker/app.py\n", "")
    backup = lx.with_name("autostart.bak")
    assert backup.read_text() == LXSESSION_BEFORE              # the original, saved first
    assert "removed the old app.py launch line" in out and str(backup) in out
    # the only thing left that starts the app is the launcher, exactly once, in labwc
    app = sandbox[1]
    assert labwc.read_text().count(f"{app}/tools/run.sh &") == 1
    assert "app.py" not in labwc.read_text()


def test_install_twice_keeps_the_original_backup_and_adds_nothing(sandbox):
    labwc, lx = autostart_files(sandbox)
    lx.write_text(LXSESSION_BEFORE)
    install(sandbox)
    first_lx, first_labwc = lx.read_text(), labwc.read_text()
    out = install(sandbox)
    install(sandbox)
    assert lx.read_text() == first_lx and labwc.read_text() == first_labwc
    assert lx.with_name("autostart.bak").read_text() == LXSESSION_BEFORE
    assert [p.name for p in lx.parent.iterdir()] .count("autostart.bak") == 1
    assert len(list(lx.parent.iterdir())) == 2                 # autostart + the one backup
    assert "launcher already present" in out


def test_install_never_overwrites_the_first_backup(sandbox):
    labwc, lx = autostart_files(sandbox)
    lx.write_text(LXSESSION_BEFORE)
    install(sandbox)
    # someone puts the line back later and runs the installer again
    lx.write_text(lx.read_text() + "@python3 /home/colleran/flight-tracker/app.py\n")
    install(sandbox)
    assert "app.py" not in lx.read_text()
    assert lx.with_name("autostart.bak").read_text() == LXSESSION_BEFORE
    assert len([p for p in lx.parent.iterdir() if p.name.startswith("autostart.bak.")]) == 1


def test_install_removes_old_labwc_entry_too_and_leaves_other_lines(sandbox):
    labwc, lx = autostart_files(sandbox)
    labwc.write_text("sleep 5\npython3 /home/colleran/flight-tracker/app.py &\nwf-panel-pi &\n")
    lx.write_text("@lxpanel --profile LXDE-pi\n   @python3.13 /home/colleran/flight-tracker/app.py\n")
    install(sandbox)
    app = sandbox[1]
    live = [ln for ln in labwc.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert live == ["sleep 5", "wf-panel-pi &", f"{app}/tools/run.sh &"]
    assert lx.read_text() == "@lxpanel --profile LXDE-pi\n"
    assert labwc.with_name("autostart.bak").read_text().count("app.py") == 1


def test_install_with_no_autostart_files_at_all(sandbox):
    home = Path(sandbox[2]["HOME"])
    install(sandbox)
    labwc = home / ".config/labwc/autostart"
    assert labwc.read_text().count("tools/run.sh &") == 1
    assert not (home / ".config/lxsession").exists()           # nothing created that wasn't needed
    assert not labwc.with_name("autostart.bak").exists()


def test_install_leaves_unrelated_python_programs_alone(sandbox):
    labwc, lx = autostart_files(sandbox)
    keep = "@python3 /home/colleran/weather/station.py\n@lxterminal\n"
    lx.write_text(keep)
    install(sandbox)
    assert lx.read_text() == keep and not lx.with_name("autostart.bak").exists()


# ---- update.sh ----

def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def deployed(sandbox):
    """An 'origin' repo and a clone of it standing in for the Pi's checkout."""
    tmp, app, env, stub = sandbox
    origin = tmp / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "tools").mkdir()
    shutil.copy(TOOLS / "update.sh", origin / "tools/update.sh")
    (origin / "app.py").write_text("v1\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "v1")
    pi = tmp / "pi"
    git(tmp, "clone", "-q", str(origin), str(pi))
    stub("pkill", f'echo "pkill $*" >> {tmp}/pkills\n')
    env = dict(env, PATH=f"{tmp / 'bin'}:/usr/bin:/bin", GIT_CONFIG_GLOBAL="/dev/null")
    return tmp, origin, pi, env


def update(deployed, codes):
    tmp, _origin, pi, env = deployed
    (tmp / "codes").write_text("".join(f"{c}\n" for c in codes))
    return subprocess.run(["/bin/sh", str(pi / "tools/update.sh")], env=env,
                          capture_output=True, text=True, timeout=60)


def publish(origin, text):
    (origin / "app.py").write_text(text)
    git(origin, "commit", "-q", "-am", text.strip())


def test_update_when_already_current_does_nothing(deployed):
    done = update(deployed, [])
    assert done.returncode == 0 and "already up to date" in done.stdout
    assert not (deployed[0] / "pkills").exists()


def test_update_pulls_checks_then_restarts(deployed):
    tmp, origin, pi, _ = deployed
    publish(origin, "v2\n")
    # python3 calls: `import pytest` probe, pytest run, selftest -> all succeed
    done = update(deployed, [0, 0, 0])
    assert done.returncode == 0, done.stderr
    assert (pi / "app.py").read_text() == "v2\n"
    assert "pkill -HUP" in (tmp / "pkills").read_text()


def test_update_rolls_back_and_leaves_the_wall_alone_when_checks_fail(deployed):
    tmp, origin, pi, _ = deployed
    publish(origin, "broken\n")
    done = update(deployed, [0, 1])                           # pytest present, pytest fails
    assert done.returncode == 1 and "CHECKS FAILED" in done.stderr
    assert (pi / "app.py").read_text() == "v1\n"              # exactly as it was
    assert not (tmp / "pkills").exists()                      # never restarted


def test_update_refuses_to_run_over_local_edits(deployed):
    tmp, origin, pi, _ = deployed
    publish(origin, "v2\n")
    (pi / "app.py").write_text("hand edit on the pi\n")
    done = update(deployed, [0, 0, 0])
    assert done.returncode == 1 and "config.local.toml" in done.stderr
    assert (pi / "app.py").read_text() == "hand edit on the pi\n"
