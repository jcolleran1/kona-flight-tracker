"""Theme: colours, fonts, cached text rendering.

Aesthetic: Kona vacation house. Deep ocean-teal background washing to dusk,
warm sand text, sunset-coral and turquoise accents. B612 Mono is bundled in
data/ (the OFL font Airbus drew for cockpit displays) for legibility across a
room.

Three movement colours carry meaning everywhere, on the map and in the text:
sand = on the ground, teal = toward Kona, coral = leaving Kona.
"""
from __future__ import annotations

from collections import OrderedDict

import pygame

from config import DATA_DIR

# --- background ---
BG = (12, 28, 34)               # deep ocean teal (solid fallback / fill)
BG_TOP = (20, 46, 52)           # lighter teal at the top of the gradient
BG_BOT = (9, 20, 26)            # darker ocean at the bottom

# --- text ---
PRIMARY = (255, 206, 138)       # main readouts: warm sand-gold
SECONDARY = (150, 186, 178)     # labels and supporting text: muted seafoam
FAINT = (74, 112, 112)          # column headers, hints
RULE = (44, 74, 76)             # hairlines
BRIGHT = (245, 236, 214)        # the featured flight number, new-row pulse

# --- status ---
OK = (90, 214, 168)             # source healthy
WARN = (255, 206, 138)          # stale
BAD = (255, 130, 102)           # failing / reconnecting

# --- the three movement colours ---
TEAL = (96, 216, 206)           # toward Kona
CORAL = (255, 142, 98)          # leaving Kona
SAND = (216, 202, 168)          # on the ground

MOVEMENT = {"ground": SAND, "toward": TEAL, "away": CORAL}

FONT_REGULAR = DATA_DIR / "B612Mono-Regular.ttf"
FONT_BOLD = DATA_DIR / "B612Mono-Bold.ttf"

TEXT_CACHE_ENTRIES = 384


def scale(color, k: float):
    """A colour dimmed by factor k (quiet hours, overflights)."""
    if k >= 0.999:
        return color
    return (int(color[0] * k), int(color[1] * k), int(color[2] * k))


class Theme:
    """Fonts scaled to display height; rendered text cached in a small LRU.

    Rendering a string with SDL_ttf is the most expensive thing the display
    does per character, and almost every string on screen is the same from one
    frame to the next. The LRU keeps the hot ones and lets old flight numbers
    fall out one at a time instead of flushing everything at once.
    """

    def __init__(self, height: int):
        u = height / 1080.0  # scale unit
        self.u = u

        def font(path, px, bold=False):
            try:
                return pygame.font.Font(str(path), max(10, round(px * u)))
            except (FileNotFoundError, pygame.error):
                f = pygame.font.SysFont("monospace", max(10, round(px * u)))
                f.set_bold(bold)
                return f

        self.flight = font(FONT_BOLD, 96, True)        # the featured flight number
        self.value = font(FONT_BOLD, 46, True)         # big stat values, route codes
        self.title = font(FONT_BOLD, 38, True)         # airline
        self.body = font(FONT_REGULAR, 28)
        self.body_bold = font(FONT_BOLD, 28, True)
        self.small = font(FONT_REGULAR, 22)
        self.tiny = font(FONT_REGULAR, 18)
        self.row = font(FONT_REGULAR, 30)
        self.row_bold = font(FONT_BOLD, 30, True)
        self.clock_big = font(FONT_BOLD, 120, True)
        self.splash = font(FONT_BOLD, 64, True)
        self.map_label = font(FONT_REGULAR, 19)
        self.map_label_bold = font(FONT_BOLD, 21, True)

        self._cache: OrderedDict = OrderedDict()

    def text(self, s: str, font: pygame.font.Font, color: tuple) -> pygame.Surface:
        key = (s, id(font), color)
        surf = self._cache.get(key)
        if surf is None:
            surf = font.render(s, True, color)
            self._cache[key] = surf
            if len(self._cache) > TEXT_CACHE_ENTRIES:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return surf

    def char_width(self, font: pygame.font.Font) -> int:
        """Width of one character (the fonts are monospaced)."""
        return max(1, font.size("0")[0])

    def fit(self, s: str, font: pygame.font.Font, max_px: int) -> str:
        """Trim a string so it fits in max_px, without re-rendering to find out."""
        n = max(1, max_px // self.char_width(font))
        return s if len(s) <= n else s[:n].rstrip()

    def blit_text(self, surface, s, font, color, pos, align="left"):
        surf = self.text(s, font, color)
        x, y = pos
        if align == "right":
            x -= surf.get_width()
        elif align == "center":
            x -= surf.get_width() // 2
        surface.blit(surf, (x, y))
        return surf.get_rect(topleft=(x, y))


def make_gradient(w: int, h: int) -> pygame.Surface:
    """A vertical ocean gradient (lighter teal up top to deep ocean below),
    built once and used as the background wash."""
    surf = pygame.Surface((w, h))
    for y in range(h):
        f = y / max(1, h - 1)
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * f) for i in range(3))
        pygame.draw.line(surf, c, (0, y), (w, y))
    return surf
