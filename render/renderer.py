"""Pygame renderer. Runs on the main thread.

Split layout:
  LEFT half   featured flight, table of other traffic, status   (render/panel.py)
  RIGHT half  the map with live aircraft                         (render/mapview.py)

Never blocks on the network. Reads immutable snapshots from SharedState.

Built for a Pi Zero 2 W. Each frame is first *described* (a list of draw
operations per panel); a panel is only repainted when its description changed,
and the screen is only updated where something was repainted. On a quiet
frame, which is most of them, the cost is building two small tuples and
comparing them. Quiet-hours dimming is baked into the cached layers rather
than blended over the whole screen every frame.
"""
from __future__ import annotations

import logging
import os
import time

import pygame

from config import Config
from render.mapview import MapView
from render.panel import LeftPanel
from render.theme import PRIMARY, SECONDARY, Theme, make_gradient, scale
from render.timeutil import clock_is_set, fmt_clock, in_quiet_hours, local_now
from state import Aircraft, SharedState, Snapshot

log = logging.getLogger("render")

WATCHDOG_REFIRE_SECONDS = 60
NIGHT_FPS = 2          # nobody is watching at 3 am; halve the work


def live_aircraft(snap: Snapshot, now: float, max_age: float) -> list[Aircraft]:
    """Aircraft whose position is still fresh. Applied at draw time, so a dead
    network connection can never leave ghosts frozen on the wall."""
    return [a for a in snap.aircraft if now - a.pos_mono <= max_age]


def pick_featured(snap: Snapshot, aircraft: list[Aircraft]) -> Aircraft | None:
    """The fetch thread's pick if it is still live; otherwise the nearest live
    aircraft (airborne before parked), so the panel never features a ghost."""
    for a in aircraft:
        if a.hex == snap.featured_hex:
            return a
    return min(aircraft, key=lambda a: (a.on_ground, a.distance_nm)) if aircraft else None


class Renderer:
    def __init__(self, cfg: Config, state: SharedState, on_watchdog=None, on_tick=None):
        self.cfg = cfg
        self.state = state
        self.on_watchdog = on_watchdog  # called when fetch looks dead
        self.on_tick = on_tick          # called once per loop for housekeeping
        self._last_watchdog_fire = 0.0
        self._started = time.monotonic()
        self._last_frame_error = 0.0
        self.screen: pygame.Surface | None = None
        self.theme: Theme | None = None
        self.mapview: MapView | None = None
        self.left: LeftPanel | None = None
        self.panel_w = 0  # width of the left info panel
        self._bg: pygame.Surface | None = None
        self._last_map: tuple | None = None
        self._showing = ""         # "splash" or "board": what is currently on screen
        self.frames_painted = 0    # for tests and the hourly log line

    # ---------- setup ----------

    def init_display(self) -> None:
        os.environ.setdefault("SDL_NOMOUSE", "1")
        drivers = [None, "kmsdrm", "fbcon"]
        last_err = None
        for drv in drivers:
            if drv:
                os.environ["SDL_VIDEODRIVER"] = drv
            try:
                pygame.display.init()
                pygame.font.init()
                flags = pygame.FULLSCREEN if self.cfg.fullscreen else 0
                self.screen = pygame.display.set_mode(
                    (self.cfg.width, self.cfg.height), flags
                )
                pygame.display.set_caption("Kona Flight Tracker")
                pygame.mouse.set_visible(False)
                log.info("display up via driver=%s at %dx%d",
                         pygame.display.get_driver(), *self.screen.get_size())
                break
            except pygame.error as e:
                last_err = e
                pygame.display.quit()
        if self.screen is None:
            raise RuntimeError(f"could not init any video driver: {last_err}")

        w, h = self.screen.get_size()
        self.theme = Theme(h)
        self.panel_w = w // 2 if self.cfg.map_enabled else w
        self._bg = make_gradient(w, h).convert()
        self.left = LeftPanel(self.cfg, self.theme, self.panel_w, h, self._bg)
        if self.cfg.map_enabled:
            self.mapview = MapView(self.cfg, w, h, self.theme)

    # ---------- what to show ----------

    def brightness(self) -> float:
        return self.cfg.quiet_brightness if in_quiet_hours(self.cfg) else 1.0

    def _clock_texts(self) -> tuple[str, str]:
        """(date, time) for the status strip; blank until the system clock is set."""
        if not clock_is_set():
            return "", ""
        now = local_now(self.cfg)
        return now.strftime("%a %b %-d").upper(), fmt_clock(now, self.cfg.clock_24h)

    # ---------- drawing ----------

    def draw_splash(self, brightness: float = 1.0) -> list[pygame.Rect]:
        if self._showing == "splash":
            return []
        t, s = self.theme, self.screen
        s.blit(self._bg, (0, 0))
        w, h = s.get_size()
        t.blit_text(s, "KONA SKIES", t.splash, scale(PRIMARY, brightness),
                    (w // 2, int(h * 0.42)), "center")
        t.blit_text(s, "WATCHING FOR AIRCRAFT . . .", t.small, scale(SECONDARY, brightness),
                    (w // 2, int(h * 0.55)), "center")
        self._showing = "splash"
        return [s.get_rect()]

    def draw_frame(self, snap: Snapshot, reconnecting: bool, now: float | None = None,
                   brightness: float = 1.0, force: bool = False) -> list[pygame.Rect]:
        """Bring the screen up to date for `snap` at monotonic time `now`.
        Returns the rectangles that were repainted (empty = nothing changed)."""
        s = self.screen
        now = time.monotonic() if now is None else now
        if self._showing != "board":
            force, self._showing = True, "board"

        aircraft = live_aircraft(snap, now, self.cfg.max_position_age_seconds)
        featured = pick_featured(snap, aircraft)
        date_text, clock_text = self._clock_texts()
        dirty: list[pygame.Rect] = []

        ops = self.left.build(aircraft, featured, snap, now, date_text=date_text,
                              clock_text=clock_text, reconnecting=reconnecting)
        if self.left.render(ops, brightness) or force:
            s.blit(self.left.surface, (0, 0))
            dirty.append(pygame.Rect(0, 0, self.panel_w, s.get_height()))

        if self.mapview is not None:
            draw_list = (self.mapview.build(aircraft, featured.hex if featured else None, now),
                         round(brightness, 3))
            if draw_list != self._last_map or force:
                self._last_map = draw_list
                self.mapview.paint(s, draw_list[0], brightness)
                dirty.append(self.mapview.rect)
        if dirty:
            self.frames_painted += 1
        return dirty

    # ---------- main loop ----------

    def run(self, stop_check) -> None:
        self.init_display()
        clock = pygame.time.Clock()

        while not stop_check():
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                    return

            snap = self.state.read()
            now = time.monotonic()
            age = (now - snap.fetched_at) if snap.fetched_at else None
            silent_for = age if age is not None else now - self._started
            dead = silent_for > self.cfg.watchdog_seconds

            if dead and self.on_watchdog and \
                    (now - self._last_watchdog_fire) > WATCHDOG_REFIRE_SECONDS:
                self._last_watchdog_fire = now
                log.error("watchdog: no fresh data for %ds -- requesting fetch restart",
                          int(silent_for))
                self.on_watchdog()

            brightness = self.brightness()
            try:
                if not snap.last_attempt_at and not dead:
                    dirty = self.draw_splash(brightness)
                else:
                    dirty = self.draw_frame(snap, dead, now, brightness)
            except Exception:
                # A bad frame must never take the wall down. Log it (at most
                # once a minute), force a clean repaint next time, carry on.
                if now - self._last_frame_error > 60 or not self._last_frame_error:
                    self._last_frame_error = now
                    log.exception("error drawing frame; continuing")
                self._showing, self._last_map = "", None
                dirty = []

            if dirty:
                pygame.display.update(dirty)
            if self.on_tick:
                self.on_tick()
            clock.tick(NIGHT_FPS if brightness < 0.999 else self.cfg.fps)
