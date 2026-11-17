"""Things that make this an appliance rather than a script: a single-instance
lock, memory accounting, and the self-test.

stdlib only. Everything here degrades quietly on a non-Linux dev machine.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LOCK_NAME = "kona-flight-tracker.lock"

# Exit codes the launcher (tools/run.sh) understands.
EXIT_OK = 0              # quit on purpose (Esc / Q / SIGTERM): do not restart
EXIT_ALREADY_RUNNING = 3 # another copy holds the lock: do not restart
EXIT_RESTART = 75        # restart me (SIGHUP from tools/update.sh, memory ceiling)


def runtime_dir() -> Path:
    """$XDG_RUNTIME_DIR (a per-user tmpfs, gone at logout) or /tmp."""
    path = os.environ.get("XDG_RUNTIME_DIR")
    if path and os.path.isdir(path) and os.access(path, os.W_OK):
        return Path(path)
    return Path("/tmp")


def lock_path() -> Path:
    return runtime_dir() / LOCK_NAME


class SingleInstance:
    """An exclusive lock held for the life of the process.

    Two autostart entries (or a second manual launch over SSH) would otherwise
    put two copies on the screen, fighting over it. The kernel drops the lock
    when the process dies for any reason, so a crash can never leave a stale
    lock behind. The file holds the PID of the running copy.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or lock_path()
        self._fh = None

    def acquire(self) -> bool:
        try:
            import fcntl
        except ImportError:          # not a POSIX system: nothing to guard
            return True
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()         # closing drops the lock
            self._fh = None

    def holder_pid(self) -> int | None:
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None


def rss_mb() -> float | None:
    """This process's resident memory in MB, or None where /proc isn't available."""
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


def available_mb() -> float | None:
    """System-wide memory still available, in MB."""
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


class MemoryWatch:
    """Logs memory use once an hour and asks for a restart if it ever passes
    the ceiling. The app is built not to grow (every cache is bounded), so the
    ceiling is a backstop, not a routine."""

    def __init__(self, ceiling_mb: float, interval: float = 3600.0, clock=time.monotonic,
                 reader=rss_mb):
        self.ceiling_mb = ceiling_mb
        self.interval = interval
        self._clock = clock
        self._reader = reader
        self._next = clock() + 60.0          # first report a minute after start
        self.last_mb: float | None = None

    def check(self, log, **context) -> bool:
        """Call often; does work only when due. True = over the ceiling."""
        now = self._clock()
        if now < self._next:
            return False
        self._next = now + self.interval
        self.last_mb = self._reader()
        if self.last_mb is None:
            return False
        extra = " ".join(f"{k}={v}" for k, v in context.items())
        log.info("memory: rss=%.0f MB (ceiling %.0f) %s", self.last_mb, self.ceiling_mb, extra)
        return self.ceiling_mb > 0 and self.last_mb > self.ceiling_mb


# ----------------------------------------------------------------------
# self-test


class Report:
    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.failed = 0
        self.warned = 0

    def line(self, status: str, name: str, detail: str = "") -> None:
        if status == "FAIL":
            self.failed += 1
        elif status == "WARN":
            self.warned += 1
        print(f"[{status:<4}] {name:<24} {detail}".rstrip(), file=self.out)

    def ok(self, name, detail=""):
        self.line("PASS", name, detail)

    def warn(self, name, detail=""):
        self.line("WARN", name, detail)

    def fail(self, name, detail=""):
        self.line("FAIL", name, detail)


def selftest(cfg, *, offline: bool = False, out=None) -> int:
    """Check everything the wall depends on and print one line per check.

        python3 app.py --selftest            # on the Pi, over SSH
        python3 app.py --selftest --offline  # skip the network checks

    Returns 0 when nothing failed (warnings don't fail it).
    """
    import datetime as dt

    from config import DATA_DIR, resolve_asset
    r = Report(out)

    # --- runtime ---
    v = sys.version_info
    (r.ok if v >= (3, 11) else r.fail)("python", f"{v.major}.{v.minor}.{v.micro}")
    try:
        import pygame
        r.ok("pygame", f"{pygame.version.ver} (SDL {'.'.join(map(str, pygame.get_sdl_version()))})")
    except Exception as e:
        pygame = None
        r.fail("pygame", f"{e}  ->  sudo apt install -y python3-pygame")

    # --- files ---
    for label, path in (("font regular", DATA_DIR / "B612Mono-Regular.ttf"),
                        ("font bold", DATA_DIR / "B612Mono-Bold.ttf"),
                        ("airlines table", DATA_DIR / "airlines.csv"),
                        ("map image", resolve_asset(cfg.map_image))):
        (r.ok if path.exists() else r.fail)(label, str(path.name) if path.exists()
                                             else f"missing: {path}")
    if pygame is not None and resolve_asset(cfg.map_image).exists():
        try:
            size = pygame.image.load(str(resolve_asset(cfg.map_image))).get_size()
            want = (cfg.width // 2, cfg.height)
            (r.ok if size == want else r.warn)(
                "map size", f"{size[0]}x{size[1]}" + ("" if size == want else
                                                      f", panel is {want[0]}x{want[1]} (it will be rescaled)"))
        except Exception as e:
            r.fail("map size", str(e))
    from render.projection import Projection
    stretch = Projection(cfg.map_lat_min, cfg.map_lat_max, cfg.map_lon_min, cfg.map_lon_max,
                         0, 0, cfg.width // 2, cfg.height).stretch
    (r.ok if abs(stretch - 1) <= 0.03 else r.warn)(
        "map shape", f"east-west stretch {100 * (stretch - 1):+.1f}%")

    # --- clock and timezone ---
    from render.timeutil import clock_is_set, local_now, zone
    if clock_is_set():
        r.ok("system clock", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    else:
        r.fail("system clock", "not set (no NTP sync yet?); the wall hides its clock until it is")
    tz = zone(cfg.timezone)
    if tz is None:
        r.fail("timezone", f"{cfg.timezone!r} unknown  ->  sudo apt install -y tzdata")
    else:
        wall = local_now(cfg)
        r.ok("wall timezone", f"{cfg.timezone}: {wall.strftime('%a %H:%M')}")
        system = dt.datetime.now().astimezone()
        same = system.utcoffset() == wall.utcoffset()
        (r.ok if same else r.warn)(
            "pi timezone", f"UTC{system.strftime('%z')}" + ("" if same else
            f" differs from {cfg.timezone}; the wall uses config, but fix it with: "
            f"sudo timedatectl set-timezone {cfg.timezone}"))

    # --- display ---
    session = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
    if session:
        r.ok("display session", f"{'wayland' if os.environ.get('WAYLAND_DISPLAY') else 'x11'} ({session})")
    else:
        r.warn("display session", "no WAYLAND_DISPLAY/DISPLAY here (normal over SSH; "
                                  "the autostart entry runs inside the desktop session)")
    if pygame is not None:
        try:
            pygame.display.init()
            sizes = pygame.display.get_desktop_sizes()
            r.ok("display driver", f"{pygame.display.get_driver()} {sizes[0][0]}x{sizes[0][1]}"
                 if sizes else pygame.display.get_driver())
            if sizes and sizes[0] != (cfg.width, cfg.height) and session:
                r.warn("resolution", f"screen is {sizes[0][0]}x{sizes[0][1]}, config says "
                                     f"{cfg.width}x{cfg.height}")
            pygame.display.quit()
        except Exception as e:
            (r.warn if not session else r.fail)("display driver", str(e))

    # --- memory ---
    avail, mine = available_mb(), rss_mb()
    if avail is None:
        r.warn("memory", "cannot read /proc/meminfo on this system")
    else:
        (r.ok if avail >= 120 else r.warn)("memory available", f"{avail:.0f} MB")
    if mine is not None:
        r.ok("this process", f"{mine:.0f} MB resident")

    # --- single instance ---
    lock = SingleInstance()
    if lock.acquire():
        lock.release()
        r.ok("instance lock", f"free ({lock.path})")
    else:
        r.ok("instance lock", f"held by PID {lock.holder_pid()} (the wall is running)")

    # --- network ---
    if offline:
        r.warn("network", "skipped (--offline)")
    else:
        _selftest_network(cfg, r)

    print(f"\n{'FAILED' if r.failed else 'OK'}: {r.failed} failed, {r.warned} warnings", file=r.out)
    return 1 if r.failed else 0


def _selftest_network(cfg, r: Report) -> None:
    from enrich.metar import METAR_URL, parse_metar
    from fetch.http import FetchError, http_get_json
    from fetch.sources import SOURCE_NAMES, make_source

    working = 0
    for name in SOURCE_NAMES:
        if name == "replay" or (name == "local" and "local" not in cfg.sources):
            continue
        enabled = name in cfg.sources
        tag = f"source {name}"
        try:
            poll = make_source(name, cfg).fetch()
            with_pos = sum(1 for a in poll.aircraft if a.get("lat") is not None)
            working += enabled
            r.ok(tag, f"{with_pos} aircraft" + ("" if enabled else "  (works; not enabled in config)"))
        except FetchError as e:
            if enabled:
                r.fail(tag, f"{e}")
            else:
                r.warn(tag, f"{e}  (not enabled, so harmless)")
        time.sleep(1.1)              # stay inside every feed's 1 request/second
    if not working:
        r.fail("sources", "no enabled source answered: the wall would show NO FLIGHT DATA")

    station = cfg.metar_station or cfg.airport_icao
    try:
        wx = parse_metar(http_get_json(METAR_URL.format(station=station), 8.0), station)
        if wx and wx.qnh_hpa:
            r.ok("METAR altimeter", f"{station} {wx.qnh_hpa:.1f} hPa ({wx.qnh_hpa / 33.8639:.2f} inHg)")
        else:
            r.warn("METAR altimeter", f"{station}: no usable observation (altitudes fall back "
                                      f"to each aircraft's own setting)")
    except FetchError as e:
        r.warn("METAR altimeter", f"{e} (altitudes fall back to each aircraft's own setting)")

    try:
        http_get_json("https://api.adsbdb.com/v0/callsign/UAL1", 8.0)
        r.ok("adsbdb routes", "reachable")
    except FetchError as e:
        if e.status == 404:
            r.ok("adsbdb routes", "reachable")
        else:
            r.warn("adsbdb routes", f"{e} (routes will be blank until it returns)")
