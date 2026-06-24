"""Right-panel geographic map: the calibrated Big Island view with live aircraft.

Projects real ADS-B lat/lon onto a static map image using the bounds from
config (your original calibration), rotates each icon by its true track,
color-codes by altitude, and overlays range rings + a home marker so the
panel reads like a standard flight-tracker map.

Everything expensive is done once or cached:
  * the map image is loaded and scaled a single time
  * rotated+tinted plane icons are cached by (color, angle bucket)
This keeps the per-frame cost to one blit plus a handful of cached blits,
which is what makes it viable on a low-power Pi.
"""
from __future__ import annotations

import logging
import math

import pygame

from config import Config, resolve_asset
from enrich.motion import movement_class
from state import Aircraft

log = logging.getLogger("map")

# Three movement colours, matching the banner and the island palette.
COLOR_GROUND = (210, 196, 165)      # sand  — on the ground
COLOR_APPROACH = (96, 216, 206)     # teal  — coming toward Kona
COLOR_DEPART = (255, 142, 98)       # coral — leaving Kona
FEATURED_RING = (245, 236, 214)     # warm sand ring on the featured flight
RING_COLOR = (54, 92, 92)
HOME_COLOR = (120, 224, 214)
LABEL_COLOR = (208, 224, 216)


def plane_color(ac: Aircraft) -> tuple[int, int, int]:
    return {
        "ground": COLOR_GROUND,
        "approaching": COLOR_APPROACH,
        "departing": COLOR_DEPART,
    }[movement_class(ac)]


def _build_vector_plane(size: int) -> pygame.Surface:
    """A clean top-down aircraft silhouette, white, pointing up (north/0deg).
    Drawn at 4x then smooth-scaled down for antialiased edges."""
    ss = 4
    s = size * ss
    surf = pygame.Surface((s, s), pygame.SRCALPHA)
    cx = s / 2
    # Proportions relative to the box. Nose up = toward y=0.
    pts = [
        (cx, s * 0.06),                       # nose
        (cx + s * 0.07, s * 0.40),            # right fuselage shoulder
        (cx + s * 0.46, s * 0.60),            # right wingtip
        (cx + s * 0.07, s * 0.66),            # right wing root back
        (cx + s * 0.06, s * 0.86),            # right tailplane root
        (cx + s * 0.22, s * 0.94),            # right tailplane tip
        (cx, s * 0.86),                       # tail centre
        (cx - s * 0.22, s * 0.94),            # left tailplane tip
        (cx - s * 0.06, s * 0.86),
        (cx - s * 0.07, s * 0.66),
        (cx - s * 0.46, s * 0.60),            # left wingtip
        (cx - s * 0.07, s * 0.40),
    ]
    pygame.draw.polygon(surf, (255, 255, 255, 255), pts)
    return pygame.transform.smoothscale(surf, (size, size))


class MapView:
    def __init__(self, cfg: Config, screen_w: int, screen_h: int, theme):
        self.cfg = cfg
        self.theme = theme
        self.map_w = screen_w // 2
        self.map_h = screen_h
        self.x0 = screen_w - self.map_w  # left edge of the map panel

        self.lat_min, self.lat_max = cfg.map_lat_min, cfg.map_lat_max
        self.lon_min, self.lon_max = cfg.map_lon_min, cfg.map_lon_max
        self.lat_range = self.lat_max - self.lat_min
        self.lon_range = self.lon_max - self.lon_min

        self.bg = self._load_map()
        self.base_icon = self._load_icon(cfg)
        # cache: (color, angle_bucket_deg) -> rotated, tinted surface
        self._icon_cache: dict[tuple, pygame.Surface] = {}

    # ---------- asset loading (once) ----------

    def _load_map(self) -> pygame.Surface:
        path = resolve_asset(self.cfg.map_image)
        try:
            img = pygame.image.load(str(path)).convert()
            return pygame.transform.smoothscale(img, (self.map_w, self.map_h))
        except (FileNotFoundError, pygame.error) as e:
            log.warning("map image '%s' unavailable (%s); using plain ocean", path, e)
            surf = pygame.Surface((self.map_w, self.map_h))
            surf.fill((9, 18, 30))
            return surf

    def _load_icon(self, cfg: Config) -> pygame.Surface:
        path = resolve_asset(cfg.plane_icon)
        try:
            img = pygame.image.load(str(path)).convert_alpha()
            return pygame.transform.smoothscale(img, (cfg.plane_size, cfg.plane_size))
        except (FileNotFoundError, pygame.error):
            log.info("plane icon '%s' not found; drawing vector aircraft", path)
            return _build_vector_plane(cfg.plane_size)

    # ---------- projection ----------

    def project(self, lat: float, lon: float) -> tuple[int, int] | None:
        """lat/lon -> (px, py) on screen, or None if outside the map frame."""
        rel_x = (lon - self.lon_min) / self.lon_range
        rel_y = 1.0 - (lat - self.lat_min) / self.lat_range
        if not (-0.02 <= rel_x <= 1.02 and -0.02 <= rel_y <= 1.02):
            return None
        return self.x0 + int(rel_x * self.map_w), int(rel_y * self.map_h)

    def _tinted_rotated(self, color, track_deg: float | None) -> pygame.Surface:
        angle = 0.0 if track_deg is None else track_deg
        bucket = round(angle / 5.0) * 5 % 360
        key = (color, bucket)
        surf = self._icon_cache.get(key)
        if surf is None:
            tinted = self.base_icon.copy()
            tinted.fill(color + (255,), special_flags=pygame.BLEND_RGBA_MULT)
            # pygame rotates counter-clockwise; track is clockwise-from-north.
            surf = pygame.transform.rotate(tinted, -bucket)
            if len(self._icon_cache) > 700:
                self._icon_cache.clear()
            self._icon_cache[key] = surf
        return surf

    # ---------- drawing ----------

    def draw(self, screen: pygame.Surface, aircraft: list[Aircraft],
             hero_hex: str | None) -> None:
        screen.blit(self.bg, (self.x0, 0))

        home = self.project(self.cfg.lat, self.cfg.lon)
        if self.cfg.show_range_rings and home:
            self._draw_rings(screen, home)
        if self.cfg.show_home and home:
            self._draw_home(screen, home)

        t = self.theme
        # Pass 1: icons. Draw farthest first so the nearest (hero) ends on top.
        # Cache projected positions so the label pass doesn't reproject.
        projected: dict[str, tuple[int, int]] = {}
        for ac in sorted(aircraft, key=lambda a: -a.distance_nm):
            pos = self.project(ac.lat, ac.lon)
            if pos is None:
                continue
            projected[ac.hex] = pos
            is_hero = ac.hex == hero_hex
            color = plane_color(ac)
            icon = self._tinted_rotated(color, ac.track_deg)
            rect = icon.get_rect(center=pos)
            if is_hero:
                pygame.draw.circle(screen, FEATURED_RING, pos,
                                   int(self.cfg.plane_size * 0.75), max(1, int(2 * t.u)))
            screen.blit(icon, rect)

        # Pass 2: labels, de-conflicted. Real trackers thin out tags where
        # traffic is dense rather than letting callsigns overprint. Place the
        # hero first, then nearest-first; skip any label that can't find a
        # clear spot (its icon still shows).
        if self.cfg.show_labels:
            self._draw_labels(screen, aircraft, hero_hex, projected)

    def _draw_labels(self, screen, aircraft, hero_hex, projected) -> None:
        t = self.theme
        off = int(self.cfg.plane_size * 0.55)
        placed: list[pygame.Rect] = []
        ordered = sorted(aircraft,
                         key=lambda a: (a.hex != hero_hex, a.distance_nm))
        for ac in ordered:
            pos = projected.get(ac.hex)
            if pos is None:
                continue
            is_hero = ac.hex == hero_hex
            label = ac.callsign or ac.registration or ac.hex.upper()
            if not label:
                continue
            font = t.map_label_bold if is_hero else t.map_label
            col = FEATURED_RING if is_hero else LABEL_COLOR
            surf = t.text(label, font, col)
            w, h = surf.get_size()
            px, py = pos
            # candidate anchors: right, left, above, below the icon
            candidates = (
                (px + off, py - h // 2),
                (px - off - w, py - h // 2),
                (px - w // 2, py - off - h),
                (px - w // 2, py + off),
            )
            chosen = None
            for lx, ly in candidates:
                r = pygame.Rect(lx - 4, ly - 2, w + 8, h + 4)
                if (r.left < self.x0 or r.right > self.x0 + self.map_w
                        or r.top < 0 or r.bottom > self.map_h):
                    continue
                if any(r.colliderect(pr) for pr in placed):
                    continue
                chosen = (lx, ly, r)
                break
            if chosen is None:
                if not is_hero:
                    continue  # too crowded; icon alone, like the real sites
                lx, ly = candidates[0]
                chosen = (lx, ly, pygame.Rect(lx - 4, ly - 2, w + 8, h + 4))
            lx, ly, r = chosen
            pill = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
            pill.fill((9, 11, 15, 168))
            screen.blit(pill, (r.left, r.top))
            screen.blit(surf, (lx, ly))
            placed.append(r)

    def _draw_rings(self, screen, home) -> None:
        hx, hy = home
        # nautical miles -> pixels (lat and lon scales differ; use an ellipse)
        px_per_nm_y = self.map_h / (self.lat_range * 60.0)
        px_per_nm_x = self.map_w / (self.lon_range * 60.0 *
                                    math.cos(math.radians(self.cfg.lat)))
        n = 1
        while True:
            r_nm = self.cfg.range_ring_nm * n
            rx, ry = int(r_nm * px_per_nm_x), int(r_nm * px_per_nm_y)
            if rx > self.map_w and ry > self.map_h:
                break
            if r_nm > self.cfg.radius_nm + self.cfg.range_ring_nm:
                break
            rect = pygame.Rect(hx - rx, hy - ry, rx * 2, ry * 2)
            pygame.draw.ellipse(screen, RING_COLOR, rect, 1)
            n += 1
            if n > 12:
                break

    def _draw_home(self, screen, home) -> None:
        hx, hy = home
        u = self.theme.u
        pygame.draw.circle(screen, HOME_COLOR, home, int(6 * u))
        pygame.draw.circle(screen, HOME_COLOR, home, int(13 * u), max(1, int(2 * u)))
        arm = int(20 * u)
        pygame.draw.line(screen, HOME_COLOR, (hx - arm, hy), (hx + arm, hy), 1)
        pygame.draw.line(screen, HOME_COLOR, (hx, hy - arm), (hx, hy + arm), 1)
