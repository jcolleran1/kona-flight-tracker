"""Aircraft icons for the map: white silhouettes pointing up (north), tinted
and rotated by the map view.

Three shapes, chosen from the ADS-B emitter category the aircraft itself
broadcasts, so a tour helicopter doesn't look like a 737:
  jet   airliners, bizjets, anything else
  prop  light aircraft (category A1) and gliders / ultralights (B*)
  heli  rotorcraft (category A7)
Shape is information only; colour still means movement and nothing else.
"""
from __future__ import annotations

import math

import pygame

SUPERSAMPLE = 4


def icon_kind(category: str) -> str:
    cat = (category or "").upper()
    if cat == "A7":
        return "heli"
    if cat == "A1" or cat.startswith("B"):
        return "prop"
    return "jet"


def _canvas(size: int):
    s = size * SUPERSAMPLE
    return pygame.Surface((s, s), pygame.SRCALPHA), s


def _finish(surf: pygame.Surface, size: int) -> pygame.Surface:
    return pygame.transform.smoothscale(surf, (size, size))


def _mirror(right, cx):
    """Right-hand outline (nose first, tail centre last) -> full symmetric polygon."""
    return right + [(2 * cx - x, y) for x, y in reversed(right[1:-1])]


def build_jet(size: int) -> pygame.Surface:
    surf, s = _canvas(size)
    cx = s / 2
    right = [
        (cx, s * 0.06),                       # nose
        (cx + s * 0.07, s * 0.40),            # fuselage shoulder
        (cx + s * 0.46, s * 0.60),            # wingtip
        (cx + s * 0.07, s * 0.66),            # wing root, trailing edge
        (cx + s * 0.06, s * 0.86),            # tailplane root
        (cx + s * 0.22, s * 0.94),            # tailplane tip
        (cx, s * 0.86),                       # tail centre
    ]
    pygame.draw.polygon(surf, (255, 255, 255, 255), _mirror(right, cx))
    return _finish(surf, size)


def build_prop(size: int) -> pygame.Surface:
    """Straight high wing, slim fuselage: reads as a Cessna at a glance."""
    surf, s = _canvas(size)
    cx = s / 2
    white = (255, 255, 255, 255)
    pygame.draw.polygon(surf, white, [                        # fuselage
        (cx, s * 0.10), (cx + s * 0.065, s * 0.24), (cx + s * 0.05, s * 0.62),
        (cx + s * 0.02, s * 0.90), (cx - s * 0.02, s * 0.90), (cx - s * 0.05, s * 0.62),
        (cx - s * 0.065, s * 0.24)])
    pygame.draw.polygon(surf, white, [                        # wing
        (cx - s * 0.47, s * 0.34), (cx + s * 0.47, s * 0.34),
        (cx + s * 0.47, s * 0.44), (cx + s * 0.06, s * 0.48),
        (cx - s * 0.06, s * 0.48), (cx - s * 0.47, s * 0.44)])
    pygame.draw.polygon(surf, white, [                        # tailplane
        (cx - s * 0.19, s * 0.80), (cx + s * 0.19, s * 0.80),
        (cx + s * 0.19, s * 0.88), (cx - s * 0.19, s * 0.88)])
    return _finish(surf, size)


def build_heli(size: int) -> pygame.Surface:
    """Teardrop body, tail boom, and a rotor drawn as two crossed blades."""
    surf, s = _canvas(size)
    cx = s / 2
    white = (255, 255, 255, 255)
    hub = (cx, s * 0.42)
    pygame.draw.ellipse(surf, white, (cx - s * 0.13, s * 0.22, s * 0.26, s * 0.40))   # cabin
    pygame.draw.polygon(surf, white, [                                                  # boom
        (cx - s * 0.035, s * 0.55), (cx + s * 0.035, s * 0.55),
        (cx + s * 0.02, s * 0.92), (cx - s * 0.02, s * 0.92)])
    pygame.draw.polygon(surf, white, [                                                  # tail rotor
        (cx - s * 0.11, s * 0.86), (cx + s * 0.11, s * 0.86),
        (cx + s * 0.11, s * 0.92), (cx - s * 0.11, s * 0.92)])
    blade = s * 0.46
    width = max(2, int(s * 0.055))
    for deg in (40, 130):                                                               # rotor
        dx, dy = blade * math.cos(math.radians(deg)), blade * math.sin(math.radians(deg))
        pygame.draw.line(surf, white, (hub[0] - dx, hub[1] - dy), (hub[0] + dx, hub[1] + dy), width)
    return _finish(surf, size)


BUILDERS = {"jet": build_jet, "prop": build_prop, "heli": build_heli}


def build_pointer(size: int) -> pygame.Surface:
    """A chevron pointing up: marks an aircraft that is off the edge of the map."""
    surf, s = _canvas(size)
    cx = s / 2
    pygame.draw.polygon(surf, (255, 255, 255, 255), [
        (cx, s * 0.12), (cx + s * 0.40, s * 0.80), (cx, s * 0.58), (cx - s * 0.40, s * 0.80)])
    return _finish(surf, size)
