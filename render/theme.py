"""Theme: colors, fonts, cached text rendering.

Aesthetic: Kona vacation house. Deep ocean-teal background washing to dusk,
warm sand text, sunset-coral and turquoise accents. B612 Mono is bundled in
data/ — the OFL font Airbus drew for cockpit displays — kept for legibility.

The old amber constant names are reused with island values so call sites in
renderer.py don't all have to change: AMBER -> warm sand-gold, etc.
"""
from __future__ import annotations

import pygame

from config import DATA_DIR

# Island palette (legacy names kept; values retuned)
BG = (12, 28, 34)               # deep ocean teal (solid fallback / fill)
BG_TOP = (20, 46, 52)           # lighter teal at the top of the gradient
BG_BOT = (9, 20, 26)            # darker ocean at the bottom
AMBER = (255, 206, 138)         # primary readouts -> warm sand-gold
AMBER_DIM = (150, 186, 178)     # secondary labels -> muted seafoam
AMBER_FAINT = (44, 74, 76)      # rules / hints -> faint teal
WHITE_WARM = (245, 236, 214)    # hero callsign / pulse -> warm sand
GREEN = (90, 214, 168)          # status ok -> tropical green
STALE = (255, 206, 138)         # stale dot -> sand-gold
RED_DIM = (255, 130, 102)       # reconnecting -> coral

# Island accents used by the map + banner
TEAL = (96, 216, 206)           # approaching (coming toward Kona)
CORAL = (255, 142, 98)          # departing (leaving Kona)
SAND = (216, 202, 168)          # on the ground / neutral

FONT_REGULAR = DATA_DIR / "B612Mono-Regular.ttf"
FONT_BOLD = DATA_DIR / "B612Mono-Bold.ttf"


class Theme:
    """Fonts scaled to display height; text surfaces cached aggressively.

    The cache is the key ARMv6 optimization: at 6 fps most frames are
    identical, so nearly every draw is a cached blit, not a re-render.
    """

    def __init__(self, height: int):
        u = height / 1080.0  # scale unit

        def font(path, px, fallback_bold=False):
            try:
                return pygame.font.Font(str(path), max(10, round(px)))
            except (FileNotFoundError, pygame.error):
                f = pygame.font.SysFont("monospace", max(10, round(px)))
                f.set_bold(fallback_bold)
                return f

        self.hero_callsign = font(FONT_BOLD, 92 * u, True)
        self.hero_big = font(FONT_BOLD, 54 * u, True)
        self.hero_mid = font(FONT_BOLD, 40 * u, True)
        self.hero_small = font(FONT_REGULAR, 30 * u)
        self.hero_label = font(FONT_REGULAR, 25 * u)
        self.row = font(FONT_REGULAR, 31 * u)
        self.row_bold = font(FONT_BOLD, 31 * u, True)
        self.header = font(FONT_REGULAR, 23 * u)
        self.status = font(FONT_REGULAR, 26 * u)
        self.clock_big = font(FONT_BOLD, 90 * u, True)
        self.splash = font(FONT_BOLD, 64 * u, True)
        self.map_label = font(FONT_REGULAR, 19 * u)
        self.map_label_bold = font(FONT_BOLD, 21 * u, True)
        self.u = u

        self._cache: dict[tuple, pygame.Surface] = {}

    def text(self, s: str, font: pygame.font.Font, color: tuple) -> pygame.Surface:
        key = (s, id(font), color)
        surf = self._cache.get(key)
        if surf is None:
            surf = font.render(s, True, color)
            if len(self._cache) > 600:   # bound memory on 512MB
                self._cache.clear()
            self._cache[key] = surf
        return surf

    def blit_text(self, screen, s, font, color, pos, align="left"):
        surf = self.text(s, font, color)
        x, y = pos
        if align == "right":
            x -= surf.get_width()
        elif align == "center":
            x -= surf.get_width() // 2
        screen.blit(surf, (x, y))
        return surf.get_rect(topleft=(x, y))


def make_gradient(w: int, h: int) -> pygame.Surface:
    """A vertical ocean gradient (lighter teal up top to deep ocean below),
    built once and blitted each frame as the background wash."""
    surf = pygame.Surface((w, h))
    for y in range(h):
        f = y / max(1, h - 1)
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * f) for i in range(3))
        pygame.draw.line(surf, c, (0, y), (w, y))
    return surf
