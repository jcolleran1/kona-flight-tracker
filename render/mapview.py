"""Right-panel map: the Big Island with live aircraft.

Projects each aircraft onto the baked basemap (render/projection.py), rotates
its icon to its true track, and colours it by movement: sand = on the ground,
teal = toward Kona, coral = leaving Kona. Overflights use the same colours,
dimmer and smaller. Between position updates aircraft are moved along their
track (enrich/deadreckon.py) so they glide instead of sitting still and
jumping, and each leaves a short fading trail.

Cost control, because this runs on a Pi Zero 2 W:
  * the basemap, range rings and home marker are composited once at startup
  * a frame is first described as a draw list of integer pixel positions;
    the renderer only repaints when that list differs from the last one, and
    a jet moves about one pixel a second at this scale
  * tinted, rotated icons and label backings are cached
"""
from __future__ import annotations

import logging
import math

import pygame

from config import Config, resolve_asset
from enrich.deadreckon import Smoother
from enrich.motion import movement_color_key
from render import icons
from render.projection import Projection
from render.theme import BRIGHT, MOVEMENT, Theme, scale
from render.units import NM_TO_KM, NM_TO_MI
from state import Aircraft

log = logging.getLogger("map")

RING_COLOR = (46, 84, 88)
RING_LABEL = (84, 128, 128)
HOME_COLOR = (150, 186, 178)
LABEL_COLOR = (208, 224, 216)
LABEL_BACKING = (8, 16, 22, 176)

OVERFLIGHT_DIM = 0.62       # overflights: same colour, dimmer...
OVERFLIGHT_SCALE = 0.74     # ...and smaller
ANGLE_STEP = 5              # icon rotation is quantised to this many degrees
TRAIL_FADE = (0.10, 0.70)   # how much of the icon colour a trail carries, oldest to newest
TRAIL_BASE = (26, 52, 54)   # what a trail fades into: a tone between ocean and land
PARKED_RADIUS_NM = 2.5      # ground traffic this close to home is drawn small, unlabelled
ICON_CACHE_ENTRIES = 600


def plane_color(ac: Aircraft) -> tuple[int, int, int]:
    """One of the three movement colours; overflights get a dimmed shade of
    the colour for their direction, never a fourth colour."""
    base = MOVEMENT[movement_color_key(ac.phase, ac.direction)]
    return scale(base, OVERFLIGHT_DIM) if ac.phase == "overflight" else base


class MapView:
    def __init__(self, cfg: Config, screen_w: int, screen_h: int, theme: Theme):
        self.cfg = cfg
        self.theme = theme
        self.map_w = screen_w // 2
        self.map_h = screen_h
        self.x0 = screen_w - self.map_w          # left edge of the map panel
        self.rect = pygame.Rect(self.x0, 0, self.map_w, self.map_h)
        self.proj = Projection(cfg.map_lat_min, cfg.map_lat_max, cfg.map_lon_min,
                               cfg.map_lon_max, self.x0, 0, self.map_w, self.map_h)
        if abs(self.proj.stretch - 1.0) > 0.03:
            log.warning("map bounds stretch the map %+.0f%% east-west; see tools/make_map.py --fit",
                        100 * (self.proj.stretch - 1.0))

        self.base = self._compose_base()          # basemap + rings + home marker, panel-local
        self._dimmed: tuple[float, pygame.Surface] | None = None
        self._shapes = self._load_shapes()
        self._icon_cache: dict[tuple, pygame.Surface] = {}
        self._backing_cache: dict[tuple, pygame.Surface] = {}
        self.smoother = Smoother(cfg.tween_seconds, cfg.max_position_age_seconds,
                                 enabled=cfg.dead_reckoning)

    # ------------------------------------------------------------------
    # one-time composition

    def _load_map(self) -> pygame.Surface:
        path = resolve_asset(self.cfg.map_image)
        try:
            img = pygame.image.load(str(path)).convert()
            if img.get_size() != (self.map_w, self.map_h):
                img = pygame.transform.smoothscale(img, (self.map_w, self.map_h))
            return img
        except (FileNotFoundError, pygame.error) as e:
            log.warning("map image '%s' unavailable (%s); using plain ocean", path, e)
            surf = pygame.Surface((self.map_w, self.map_h))
            surf.fill((9, 22, 32))
            return surf

    def _compose_base(self) -> pygame.Surface:
        surf = self._load_map()
        home = self.proj.to_xy(self.cfg.lat, self.cfg.lon)
        hx, hy = int(round(home[0] - self.x0)), int(round(home[1]))
        u = self.theme.u
        if self.cfg.show_range_rings:
            self._draw_rings(surf, hx, hy)
        if self.cfg.show_home:
            pygame.draw.circle(surf, HOME_COLOR, (hx, hy), int(15 * u), max(1, int(2 * u)))
            label = self.theme.text(self.cfg.airport_iata, self.theme.map_label_bold, HOME_COLOR)
            surf.blit(label, (hx - label.get_width() - int(22 * u), hy - label.get_height() // 2))
        return surf

    def _draw_rings(self, surf: pygame.Surface, hx: int, hy: int) -> None:
        """Distance rings in the display's own distance unit, labelled."""
        unit = self.cfg.distance_units
        nm_per_unit = {"mi": 1 / NM_TO_MI, "km": 1 / NM_TO_KM}.get(unit, 1.0)
        step_nm = self.cfg.range_ring_step * nm_per_unit
        reach_nm = self.cfg.radius_nm + step_nm
        n = 1
        while n <= 40 and n * step_nm <= reach_nm:
            rx = int(round(n * step_nm * self.proj.px_per_nm_x))
            ry = int(round(n * step_nm * self.proj.px_per_nm_y))
            if rx > 2 * self.map_w and ry > 2 * self.map_h:
                break
            pygame.draw.ellipse(surf, RING_COLOR, (hx - rx, hy - ry, 2 * rx, 2 * ry), 1)
            text = self.theme.text(f"{n * self.cfg.range_ring_step:g} {unit}", self.theme.tiny,
                                   RING_LABEL)
            # Label each ring out to the southwest: open ocean, and clear of the
            # approach paths from the north where aircraft labels need the room.
            tx = hx - int(rx * 0.7071) - text.get_width() - 4
            ty = hy + int(ry * 0.7071) + 2
            if 4 <= tx and ty <= self.map_h - text.get_height() - 4:
                surf.blit(text, (tx, ty))
            n += 1

    def _load_shapes(self) -> dict[str, tuple[pygame.Surface, pygame.Surface]]:
        """White silhouettes, (normal, small) per kind."""
        size = self.cfg.plane_size
        small = max(8, int(size * OVERFLIGHT_SCALE))
        jet = None
        path = resolve_asset(self.cfg.plane_icon)
        try:
            jet = pygame.transform.smoothscale(pygame.image.load(str(path)).convert_alpha(),
                                               (size, size))
        except (FileNotFoundError, pygame.error):
            log.info("plane icon '%s' not found; drawing vector aircraft", path)
        shapes = {}
        for kind, builder in icons.BUILDERS.items():
            normal = jet if (kind == "jet" and jet is not None) else builder(size)
            shapes[kind] = (normal, pygame.transform.smoothscale(normal, (small, small)))
        ptr = icons.build_pointer(max(10, int(size * 0.62)))
        shapes["pointer"] = (ptr, ptr)
        return shapes

    # ------------------------------------------------------------------
    # per-frame: describe, then paint

    def project(self, lat: float, lon: float) -> tuple[int, int] | None:
        """lat/lon -> (px, py) on screen, or None if outside the map frame."""
        return self.proj.project(lat, lon)

    def build(self, aircraft, hero_hex: str | None, now: float) -> tuple:
        """The frame as a draw list. Equal lists mean an identical picture."""
        cfg, t = self.cfg, self.theme
        trails, marks, labels = [], [], []
        placed: list[pygame.Rect] = []
        positions: dict[str, tuple[int, int]] = {}
        # At this scale the whole airport is a few pixels: aircraft parked there
        # would bury the home marker under a pile of labels. They are in the table.
        parked_hexes: set[str] = set()

        # farthest first so the nearest ends up on top; overflights underneath everything
        for ac in sorted(aircraft, key=lambda a: (a.hex == hero_hex, a.phase != "overflight",
                                                  -a.distance_nm)):
            lat, lon = self.smoother.position(ac, now)
            pos = self.proj.project(lat, lon)
            color = plane_color(ac)
            is_hero = ac.hex == hero_hex
            if pos is None:
                if cfg.show_offmap_pointers and not ac.on_ground:
                    marks.append(self._pointer(lat, lon, color))
                continue
            positions[ac.hex] = pos
            if cfg.show_trails and len(ac.trail) >= 1 and not ac.on_ground:
                pts = [p for p in (self.proj.project(la, lo, margin=0.5) for la, lo in ac.trail)
                       if p is not None]
                pts.append(pos)
                if len(pts) >= 2:
                    trails.append(("trail", color, tuple(pts)))
            kind = icons.icon_kind(ac.category) if cfg.icon_shapes else "jet"
            bucket = 0 if ac.track_deg is None else int(round(ac.track_deg / ANGLE_STEP)) \
                * ANGLE_STEP % 360
            parked = ac.on_ground and ac.distance_nm <= PARKED_RADIUS_NM and not is_hero
            if parked:
                parked_hexes.add(ac.hex)
            small = (ac.phase == "overflight" or parked) and not is_hero
            if is_hero:
                marks.append(("ring", pos[0], pos[1], int(cfg.plane_size * 0.78)))
            marks.append(("icon", kind, color, bucket, small, pos[0], pos[1]))

        if cfg.show_labels:
            off = int(cfg.plane_size * 0.58)
            for ac in sorted(aircraft, key=lambda a: (a.hex != hero_hex, a.phase == "overflight",
                                                      a.distance_nm)):
                pos = positions.get(ac.hex)
                if pos is None or not ac.label or ac.hex in parked_hexes:
                    continue          # no identity known: icon only, never a guess
                is_hero = ac.hex == hero_hex
                font = "map_label_bold" if is_hero else "map_label"
                color = BRIGHT if is_hero else (scale(LABEL_COLOR, 0.72)
                                                if ac.phase == "overflight" else LABEL_COLOR)
                w, h = t.text(ac.label, getattr(t, font), color).get_size()
                spot = self._place_label(pos, w, h, off, placed, force=is_hero)
                if spot is not None:
                    placed.append(spot)
                    labels.append(("label", ac.label, font, color, spot.x, spot.y, spot.w, spot.h))

        self.smoother.prune({a.hex for a in aircraft})
        return tuple(trails) + tuple(marks) + tuple(labels)

    def _place_label(self, pos, w, h, off, placed, force: bool) -> pygame.Rect | None:
        """Right, left, above, below the icon; first spot that is on the panel
        and clear of other labels. Dense traffic loses labels, not legibility."""
        px, py = pos
        bw, bh = w + 8, h + 4
        candidates = ((px + off, py - bh // 2), (px - off - bw, py - bh // 2),
                      (px - bw // 2, py - off - bh), (px - bw // 2, py + off))
        for x, y in candidates:
            r = pygame.Rect(x, y, bw, bh)
            if self.rect.contains(r) and not any(r.colliderect(p) for p in placed):
                return r
        if force:
            r = pygame.Rect(candidates[0][0], candidates[0][1], bw, bh)
            return r.clamp(self.rect)
        return None

    def _pointer(self, lat: float, lon: float, color) -> tuple:
        """A chevron on the panel edge, pointing toward an aircraft that is off the map."""
        x, y = self.proj.to_xy(lat, lon)
        inset = int(self.cfg.plane_size * 0.5)
        cx, cy = self.rect.centerx, self.rect.centery
        dx, dy = x - cx, y - cy
        half_w, half_h = self.map_w / 2 - inset, self.map_h / 2 - inset
        k = min(half_w / abs(dx) if dx else math.inf, half_h / abs(dy) if dy else math.inf)
        ex, ey = int(round(cx + dx * k)), int(round(cy + dy * k))
        angle = math.degrees(math.atan2(dx, -dy)) % 360          # compass-style: 0 = up
        bucket = int(round(angle / ANGLE_STEP)) * ANGLE_STEP % 360
        return ("icon", "pointer", color, bucket, False, ex, ey)

    # ------------------------------------------------------------------

    def _background(self, brightness: float) -> pygame.Surface:
        if brightness >= 0.999:
            return self.base
        if self._dimmed is None or self._dimmed[0] != brightness:
            surf = self.base.copy()
            k = max(0, min(255, int(brightness * 255)))
            surf.fill((k, k, k), special_flags=pygame.BLEND_RGB_MULT)
            self._dimmed = (brightness, surf)
        return self._dimmed[1]

    def _icon(self, kind: str, color, bucket: int, small: bool) -> pygame.Surface:
        key = (kind, color, bucket, small)
        surf = self._icon_cache.get(key)
        if surf is None:
            tinted = self._shapes[kind][1 if small else 0].copy()
            tinted.fill(color + (255,), special_flags=pygame.BLEND_RGBA_MULT)
            # pygame rotates counter-clockwise; track is clockwise from north
            surf = pygame.transform.rotate(tinted, -bucket) if bucket else tinted
            if len(self._icon_cache) >= ICON_CACHE_ENTRIES:
                self._icon_cache.clear()
            self._icon_cache[key] = surf
        return surf

    def _backing(self, w: int, h: int) -> pygame.Surface:
        surf = self._backing_cache.get((w, h))
        if surf is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            surf.fill(LABEL_BACKING)
            if len(self._backing_cache) >= 200:
                self._backing_cache.clear()
            self._backing_cache[(w, h)] = surf
        return surf

    def paint(self, screen: pygame.Surface, draw_list: tuple, brightness: float = 1.0) -> None:
        """Draw the map panel. Everything is clipped to the panel so nothing
        near the edge can spill onto the info panel."""
        t = self.theme
        previous_clip = screen.get_clip()
        screen.set_clip(self.rect)
        try:
            screen.blit(self._background(brightness), (self.x0, 0))
            for op in draw_list:
                kind = op[0]
                if kind == "trail":
                    _, color, pts = op
                    n = len(pts) - 1
                    lo, hi = TRAIL_FADE
                    for i in range(n):
                        k = lo + (hi - lo) * (i + 1) / n
                        shade = scale((int(TRAIL_BASE[0] + (color[0] - TRAIL_BASE[0]) * k),
                                       int(TRAIL_BASE[1] + (color[1] - TRAIL_BASE[1]) * k),
                                       int(TRAIL_BASE[2] + (color[2] - TRAIL_BASE[2]) * k)),
                                      brightness)
                        pygame.draw.line(screen, shade, pts[i], pts[i + 1], 2)
                elif kind == "ring":
                    _, x, y, r = op
                    pygame.draw.circle(screen, scale(BRIGHT, brightness), (x, y), r,
                                       max(1, int(2 * t.u)))
                elif kind == "icon":
                    _, shape, color, bucket, small, x, y = op
                    icon = self._icon(shape, scale(color, brightness), bucket, small)
                    screen.blit(icon, icon.get_rect(center=(x, y)))
                elif kind == "label":
                    _, text, font, color, x, y, w, h = op
                    screen.blit(self._backing(w, h), (x, y))
                    screen.blit(t.text(text, getattr(t, font), scale(color, brightness)),
                                (x + 4, y + 2))
        finally:
            screen.set_clip(previous_clip)
