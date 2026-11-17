"""Pygame renderer. Runs on the main thread.

Split layout:
  LEFT half  — Solari-style info: hero (nearest aircraft) + board table + status
  RIGHT half — calibrated geographic map with live aircraft (see render/mapview)

Display init order:
  1. Whatever SDL picks by default (windowed dev on Windows/macOS/X)
  2. kmsdrm (Raspberry Pi OS Lite, modern KMS driver)
  3. fbcon (legacy framebuffer fallback)

Never blocks on the network. Reads immutable snapshots from SharedState.
"""
from __future__ import annotations

import logging
import os
import time

import pygame

from config import Config
from enrich.geo import compass_point
from enrich.motion import movement_color_key
from render.theme import (
    Theme, make_gradient, BG, AMBER, AMBER_DIM, AMBER_FAINT, WHITE_WARM,
    GREEN, STALE, RED_DIM, TEAL, CORAL, SAND,
)
from render.timeutil import clock_is_set, fmt_clock, in_quiet_hours, local_now
from render.units import fmt_distance, fmt_altitude, fmt_speed
from render.mapview import MapView
from state import Aircraft, SharedState, Snapshot

log = logging.getLogger("render")

PULSE_SECONDS = 1.2  # row brightness pulse duration after a row first appears

PHASE_WORDS = {
    "ground": "ON THE GROUND",
    "arriving": "ARRIVING",
    "departing": "DEPARTING",
    "overflight": "OVERFLIGHT",
    "local": "IN THE AREA",
}
ACCENTS = {"ground": SAND, "toward": TEAL, "away": CORAL}


def accent_for(ac: Aircraft) -> tuple[int, int, int]:
    return ACCENTS[movement_color_key(ac.phase, ac.direction)]


def live_aircraft(snap: Snapshot, now: float, max_age: float) -> list[Aircraft]:
    """Aircraft whose position is still fresh. Applied at draw time, so a dead
    network connection can never leave ghosts frozen on the wall."""
    return [a for a in snap.aircraft if now - a.pos_mono <= max_age]


class Renderer:
    def __init__(self, cfg: Config, state: SharedState, on_watchdog=None):
        self.cfg = cfg
        self.state = state
        self.on_watchdog = on_watchdog  # called when fetch looks dead
        self._last_watchdog_fire = 0.0
        self._started = time.monotonic()
        self._last_frame_error = 0.0
        self._row_seen: dict[str, tuple[str, float]] = {}  # hex -> (row text, change time)
        self.screen: pygame.Surface | None = None
        self.theme: Theme | None = None
        self.mapview: MapView | None = None
        self.panel_w = 0  # width of the left info panel
        self._bg: pygame.Surface | None = None
        self._dim_overlay: pygame.Surface | None = None

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
                pygame.display.set_caption("Flight Tracker")
                pygame.mouse.set_visible(False)
                log.info("display up via driver=%s at %dx%d",
                         drv or "default", *self.screen.get_size())
                break
            except pygame.error as e:
                last_err = e
                pygame.display.quit()
        if self.screen is None:
            raise RuntimeError(f"could not init any video driver: {last_err}")

        w, h = self.screen.get_size()
        self.theme = Theme(h)
        self.panel_w = w // 2 if self.cfg.map_enabled else w
        if self.cfg.map_enabled:
            self.mapview = MapView(self.cfg, w, h, self.theme)
        self._bg = make_gradient(w, h)
        self._dim_overlay = pygame.Surface((w, h))
        self._dim_overlay.fill((0, 0, 0))

    # ---------- full-screen states ----------

    def draw_splash(self) -> None:
        t, s = self.theme, self.screen
        s.blit(self._bg, (0, 0))
        w, h = s.get_size()
        t.blit_text(s, "KONA SKIES", t.splash, AMBER, (w // 2, int(h * 0.42)), "center")
        t.blit_text(s, "WATCHING FOR AIRCRAFT . . .", t.status, AMBER_DIM,
                    (w // 2, int(h * 0.55)), "center")

    # ---------- left panel ----------

    def draw_left(self, aircraft: list[Aircraft], featured: Aircraft | None) -> None:
        if featured is not None:
            self._draw_hero(featured)
            rest = [a for a in aircraft if a.hex != featured.hex]
            self._draw_rows(rest)
        else:
            self._draw_quiet_left()

    def _draw_quiet_left(self) -> None:
        t, s = self.theme, self.screen
        u, pw = t.u, self.panel_w
        t.blit_text(s, "QUIET SKIES", t.hero_mid, AMBER_DIM, (pw // 2, int(360 * u)), "center")
        if clock_is_set():
            t.blit_text(s, fmt_clock(local_now(self.cfg), self.cfg.clock_24h), t.clock_big,
                        AMBER, (pw // 2, int(440 * u)), "center")
        t.blit_text(s, "NO AIRCRAFT IN RANGE", t.status, AMBER_FAINT,
                    (pw // 2, int(600 * u)), "center")

    def _draw_hero(self, ac: Aircraft) -> None:
        t, s = self.theme, self.screen
        u = t.u
        pad = int(46 * u)

        t.blit_text(s, (ac.label or "UNKNOWN")[:12], t.hero_callsign, WHITE_WARM,
                    (pad, int(18 * u)))

        ident = " / ".join(x for x in (ac.airline, ac.type_name or ac.type_code) if x)
        if ident:
            t.blit_text(s, ident.upper()[:34], t.hero_mid, AMBER, (pad, int(140 * u)))

        stat_y = int(212 * u)
        t.blit_text(s, "ALTITUDE", t.hero_label, AMBER_DIM, (pad, stat_y))
        t.blit_text(s, self._altitude_text(ac), t.hero_big, AMBER, (pad, stat_y + int(30 * u)))
        spd_x = pad + int(420 * u)
        t.blit_text(s, "GROUND SPEED", t.hero_label, AMBER_DIM, (spd_x, stat_y))
        t.blit_text(s, fmt_speed(ac.ground_speed_kt, self.cfg.speed_units),
                    t.hero_big, AMBER, (spd_x, stat_y + int(30 * u)))

        dist_y = int(322 * u)
        dist_txt = (f"{fmt_distance(ac.distance_nm, self.cfg.distance_units)} "
                    f"{compass_point(ac.bearing_deg)} FROM {self.cfg.airport_iata}")
        r = t.blit_text(s, dist_txt + "   ·   ", t.hero_small, AMBER, (pad, dist_y))
        t.blit_text(s, PHASE_WORDS[ac.phase], t.hero_small, accent_for(ac), (r.right, dist_y))

        rule_y = int(386 * u)
        pygame.draw.line(s, AMBER_FAINT, (pad, rule_y),
                         (self.panel_w - pad, rule_y), max(1, int(2 * u)))

    def _draw_rows(self, aircraft: list[Aircraft]) -> None:
        t, s = self.theme, self.screen
        h = s.get_height()
        u, pw = t.u, self.panel_w
        pad = int(46 * u)
        top = int(406 * u)
        row_h = int(62 * u)
        status_h = int(72 * u)

        # Three columns only — flight, airline, altitude. Roomy now.
        x_flight = pad
        x_airline = pad + int(210 * u)
        x_alt = pw - pad           # right-aligned anchor

        t.blit_text(s, "FLIGHT", t.header, AMBER_FAINT, (x_flight, top))
        t.blit_text(s, "AIRLINE", t.header, AMBER_FAINT, (x_airline, top))
        t.blit_text(s, "ALTITUDE", t.header, AMBER_FAINT, (x_alt, top), "right")
        top += int(42 * u)

        max_rows = min(self.cfg.max_rows, (h - status_h - top) // row_h)
        now = time.monotonic()
        for ac in aircraft[:max_rows]:
            # pulse a row once, when the aircraft first shows up in the table
            seen = self._row_seen.setdefault(ac.hex, ("", now))
            color = WHITE_WARM if (now - seen[1]) < PULSE_SECONDS else AMBER

            t.blit_text(s, (ac.label or "—")[:8], t.row_bold, color, (x_flight, top))
            t.blit_text(s, (ac.airline or "—")[:18].upper(), t.row, color, (x_airline, top))
            t.blit_text(s, self._altitude_text(ac), t.row, color, (x_alt, top), "right")

            ry = top + row_h - int(14 * u)
            pygame.draw.line(s, AMBER_FAINT, (pad, ry), (pw - pad, ry), 1)
            top += row_h

        live = {a.hex for a in aircraft}
        for k in list(self._row_seen):
            if k not in live:
                del self._row_seen[k]

    # ---------- status strip (left panel) ----------

    def _draw_status(self, snap: Snapshot, reconnecting: bool, count: int,
                     mono: float) -> None:
        t, s = self.theme, self.screen
        h = s.get_height()
        u, pw = t.u, self.panel_w
        pad = int(46 * u)
        y = h - int(46 * u)

        if clock_is_set():
            now = local_now(self.cfg)
            t.blit_text(s, now.strftime("%a %b %d   ") + fmt_clock(now, self.cfg.clock_24h, True),
                        t.status, AMBER_DIM, (pad, y))

        if snap.fetched_at:
            age = max(0, int(mono - snap.fetched_at))
            mid = f"{count} AIRCRAFT · {age}s"
        else:
            mid = "WAITING . . ."
        if reconnecting:
            mid = "RECONNECTING · " + mid

        stale = snap.fetched_at and (mono - snap.fetched_at) > self.cfg.stale_after_seconds
        dot_color = GREEN if (snap.ok and not stale) else STALE
        if reconnecting:
            dot_color = RED_DIM
        dot_x = pw - pad - int(8 * u)
        t.blit_text(s, mid, t.status, RED_DIM if reconnecting else AMBER_DIM,
                    (dot_x - int(20 * u), y), "right")
        pygame.draw.circle(s, dot_color, (dot_x, y + int(14 * u)), int(8 * u))

    # ---------- route banner (right panel, top) ----------

    def _draw_route_banner(self, featured: Aircraft | None) -> None:
        t, s = self.theme, self.screen
        u = t.u
        w = s.get_width()
        x0 = self.panel_w
        bh = int(116 * u)
        pad = int(34 * u)

        # translucent island-dark band so it reads over the ocean
        band = pygame.Surface((w - x0, bh), pygame.SRCALPHA)
        band.fill((10, 26, 32, 200))
        s.blit(band, (x0, 0))
        pygame.draw.line(s, AMBER_FAINT, (x0, bh), (w, bh), max(1, int(2 * u)))

        if featured is None:
            t.blit_text(s, "KONA · KOA", t.hero_mid, AMBER, (x0 + pad, int(34 * u)))
            t.blit_text(s, "KAILUA-KONA, HAWAII", t.hero_label, AMBER_DIM,
                        (x0 + pad, int(82 * u)))
            return

        phase = featured.phase
        callsign = featured.label or "UNKNOWN"
        accent, word = accent_for(featured), PHASE_WORDS[phase]
        name = self.cfg.airport_name.upper()
        origin = self._route_place(featured, end="origin")
        dest = self._route_place(featured, end="dest")

        if phase == "departing":
            tail = f"TO {dest}" if dest else f"LEAVING {name}"
            self._banner_triangle(x0 + pad, int(40 * u), accent, up=True)
        elif phase == "arriving":
            tail = f"FROM {origin}" if origin else f"INBOUND TO {name}"
            self._banner_triangle(x0 + pad, int(40 * u), accent, up=False)
        elif phase == "ground":
            tail = f"AT {name} INTERNATIONAL" if featured.distance_nm <= 3 else ""
        else:  # overflight / local: a route if we trust one, else just the heading
            if origin and dest:
                tail = f"{origin} TO {dest}"
            else:
                tail = "HEADING " + ("TOWARD " if featured.direction == "toward" else "AWAY FROM ") + name

        tx = x0 + pad + int(40 * u)
        line1 = t.blit_text(s, word, t.hero_small, accent, (tx, int(20 * u)))
        t.blit_text(s, callsign, t.hero_mid, WHITE_WARM, (line1.right + int(20 * u), int(14 * u)))
        t.blit_text(s, tail, t.hero_small, AMBER, (tx, int(66 * u)))

    def _banner_triangle(self, cx, cy, color, up: bool) -> None:
        u = self.theme.u
        r = int(13 * u)
        if up:
            pts = [(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
        else:
            pts = [(cx, cy + r), (cx - r, cy - r), (cx + r, cy - r)]
        pygame.draw.polygon(self.screen, color, pts)

    @staticmethod
    def _route_place(ac: Aircraft, end: str) -> str:
        name = ac.origin_name if end == "origin" else ac.dest_name
        code = ac.origin_code if end == "origin" else ac.dest_code
        if name and code:
            return f"{name} ({code})"
        return name or code or ""

    # ---------- compose a full frame ----------

    def draw_frame(self, snap: Snapshot, reconnecting: bool, now: float | None = None) -> None:
        s = self.screen
        now = time.monotonic() if now is None else now
        s.blit(self._bg, (0, 0))
        aircraft = live_aircraft(snap, now, self.cfg.max_position_age_seconds)
        featured = next((a for a in aircraft if a.hex == snap.featured_hex), None)
        if featured is None and aircraft:
            # the featured flight just aged out; fall back to the nearest live one
            featured = min(aircraft, key=lambda a: (a.on_ground, a.distance_nm))
        self.draw_left(aircraft, featured)
        self._draw_status(snap, reconnecting, len(aircraft), now)
        if self.mapview is not None:
            self.mapview.draw(s, aircraft, featured.hex if featured else None, now)
            pygame.draw.line(s, AMBER_FAINT, (self.panel_w, 0),
                             (self.panel_w, s.get_height()), max(1, int(2 * self.theme.u)))
            self._draw_route_banner(featured)

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
            stale = age is not None and age > self.cfg.stale_after_seconds
            dead = (age if age is not None else now - self._started) > self.cfg.watchdog_seconds

            if dead and self.on_watchdog and (now - self._last_watchdog_fire) > 60:
                self._last_watchdog_fire = now
                log.error("watchdog: no fresh data for %ds -- requesting fetch restart",
                          int(age if age is not None else now - self._started))
                self.on_watchdog()

            try:
                if not snap.fetched_at:
                    self.draw_splash()
                else:
                    self.draw_frame(snap, dead, now)
            except Exception:
                # A bad frame must never take the wall down. Log it (at most
                # once a minute) and try again next frame.
                if now - self._last_frame_error > 60 or not self._last_frame_error:
                    self._last_frame_error = now
                    log.exception("error drawing frame; continuing")
                self.screen.blit(self._bg, (0, 0))

            dim = 0.0
            if in_quiet_hours(self.cfg):
                dim = max(dim, 1.0 - self.cfg.quiet_brightness)
            if dim > 0:
                self._dim_overlay.set_alpha(int(dim * 255))
                self.screen.blit(self._dim_overlay, (0, 0))

            pygame.display.flip()
            clock.tick(self.cfg.fps)

    def _altitude_text(self, ac: Aircraft) -> str:
        return fmt_altitude(ac.altitude_ft, ac.on_ground, self.cfg.altitude_units,
                            phase=ac.phase, vertical_rate_fpm=ac.vertical_rate_fpm)
