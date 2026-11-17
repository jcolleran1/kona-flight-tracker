"""The left info panel: featured flight, the table of everything else, status.

The panel is described as a flat list of draw operations (text here, a line
there). That list does two jobs: it is what gets drawn, and it is the cache
key. Most frames produce exactly the same list as the frame before (nothing on
the left changes between polls), so the panel surface is redrawn only when the
list differs. On a Pi Zero that turns ~40 text blits per frame into zero.

Nothing here touches the network or the clock directly; everything it shows is
passed in, which also makes it easy to test.
"""
from __future__ import annotations

import pygame

from config import Config
from enrich.altitude import vertical_trend
from enrich.geo import compass_point
from enrich.motion import movement_color_key
from render.theme import (BAD, BRIGHT, FAINT, MOVEMENT, OK, PRIMARY, RULE, SECONDARY, WARN,
                          Theme, scale)
from render.units import fmt_altitude, fmt_distance, fmt_speed
from state import Aircraft, Snapshot

PULSE_SECONDS = 1.2          # a new table row is highlighted this long
HOME_GROUND_NM = 3.0

PHASE_WORDS = {
    "ground": "ON THE GROUND",
    "arriving": "ARRIVING",
    "departing": "DEPARTING",
    "overflight": "OVERFLIGHT",
    "local": "IN THE AREA",
}
ROW_ORDER = {"arriving": 0, "departing": 1, "local": 2, "overflight": 3, "ground": 4}
COMPASS_WORDS = ["NORTH", "NORTHEAST", "EAST", "SOUTHEAST", "SOUTH", "SOUTHWEST", "WEST",
                 "NORTHWEST"]
SOURCE_LABELS = {"airplanes_live": "AIRPLANES.LIVE", "adsb_fi": "ADSB.FI", "adsb_lol": "ADSB.LOL",
                 "local": "RECEIVER", "replay": "REPLAY"}
TREND_ARROWS = {"climb": " ↑", "descend": " ↓"}


def accent_for(ac: Aircraft):
    return MOVEMENT[movement_color_key(ac.phase, ac.direction)]


def altitude_text(ac: Aircraft, cfg: Config, arrow: bool = True) -> str:
    text = fmt_altitude(ac.altitude_ft, ac.on_ground, cfg.altitude_units, phase=ac.phase,
                        vertical_rate_fpm=ac.vertical_rate_fpm)
    if arrow and text[:1].isdigit():
        text += TREND_ARROWS.get(vertical_trend(ac.vertical_rate_fpm,
                                                cfg.vertical_rate_threshold_fpm), "")
    return text


def heading_words(track_deg: float | None) -> str:
    if track_deg is None:
        return ""
    return COMPASS_WORDS[int((track_deg + 22.5) % 360 // 45)]


def fallback_route_text(ac: Aircraft, cfg: Config) -> str:
    """What the route slot says when there is no trustworthy route: only
    things the aircraft's own motion tells us."""
    name = cfg.airport_name.upper()
    if ac.phase == "arriving":
        return f"INBOUND TO {name}"
    if ac.phase == "departing":
        return f"OUTBOUND FROM {name}"
    if ac.phase == "ground":
        return f"AT {name} AIRPORT" if ac.distance_nm <= HOME_GROUND_NM else ""
    words = heading_words(ac.track_deg)
    return f"HEADING {words}" if words else ""


def table_order(aircraft, featured_hex: str | None) -> list[Aircraft]:
    """Rows in an order that doesn't reshuffle as planes move: Kona arrivals,
    then departures, then other traffic, each group by when it first appeared."""
    rest = [a for a in aircraft if a.hex != featured_hex]
    rest.sort(key=lambda a: (ROW_ORDER.get(a.phase, 9), a.first_seen_mono, a.hex))
    return rest


def fit_words(theme: Theme, text: str, font, max_px: int) -> str:
    """Shorten to whole words where possible ("BLUE HAWAIIAN" rather than
    "BLUE HAWAIIAN HELICOPT")."""
    limit = max(1, max_px // theme.char_width(font))
    if len(text) <= limit:
        return text
    words = text.split()
    while len(words) > 1 and len(" ".join(words)) > limit:
        words.pop()
    return " ".join(words)[:limit].rstrip()


class LeftPanel:
    def __init__(self, cfg: Config, theme: Theme, width: int, height: int,
                 background: pygame.Surface):
        self.cfg, self.theme = cfg, theme
        self.w, self.h = width, height
        self._bg = background.subsurface((0, 0, width, height)).copy()
        self.surface = self._bg.copy()
        self._last_key = None
        self._row_seen: dict[str, float] = {}       # hex -> when it first got a table row

    # ------------------------------------------------------------------
    # building the op list

    def build(self, aircraft, featured: Aircraft | None, snap: Snapshot, mono: float, *,
              date_text: str, clock_text: str, reconnecting: bool) -> tuple:
        ops: list[tuple] = []
        u = self.theme.u
        pad = int(46 * u)
        stale = bool(snap.fetched_at) and mono - snap.fetched_at > self.cfg.stale_after_seconds
        healthy = snap.ok and not stale and not reconnecting

        if featured is not None:
            self._hero(ops, featured, pad)
            self._table(ops, table_order(aircraft, featured.hex), pad, mono)
        else:
            self._idle(ops, healthy, clock_text)
        self._status(ops, snap, pad, mono, len(aircraft), date_text, clock_text,
                     reconnecting, stale)
        # divider between the info panel and the map
        ops.append(("l", RULE, self.w - 1, 0, self.w - 1, self.h, max(1, int(2 * u))))
        return tuple(ops)

    def _hero(self, ops, ac: Aircraft, pad: int) -> None:
        t, cfg = self.theme, self.cfg
        u = t.u
        inner = self.w - 2 * pad
        accent = accent_for(ac)

        # phase line
        y = int(26 * u)
        x = pad
        if ac.phase in ("arriving", "departing"):
            ops.append(("tri", accent, pad + int(11 * u), y + int(17 * u), int(11 * u),
                        ac.phase == "departing"))
            x = pad + int(34 * u)
        ops.append(("t", PHASE_WORDS[ac.phase], "body_bold", accent, x, y, "left"))

        # flight number, with the broadcast callsign small beside it when it differs
        y = int(60 * u)
        flight = t.fit(ac.label or "UNKNOWN", t.flight, inner)
        ops.append(("t", flight, "flight", BRIGHT, pad, y, "left"))
        if ac.callsign and ac.callsign != flight:
            fx = pad + len(flight) * t.char_width(t.flight) + int(22 * u)
            if fx + len(ac.callsign) * t.char_width(t.small) <= self.w - pad:
                ops.append(("t", ac.callsign, "small", FAINT, fx, y + int(76 * u), "left"))

        y = int(176 * u)
        if ac.airline:
            ops.append(("t", fit_words(t, ac.airline.upper(), t.title, inner), "title", PRIMARY,
                        pad, y, "left"))
        y = int(228 * u)
        kind = (ac.type_name or ac.type_code).upper()
        reg = ac.registration if ac.registration and ac.registration != ac.label else ""
        detail = " · ".join(x for x in (fit_words(t, kind, t.body, inner - int(200 * u)), reg) if x)
        if detail:
            ops.append(("t", detail, "body", SECONDARY, pad, y, "left"))

        # route: codes large, city names beneath; or what its own motion tells us
        y = int(286 * u)
        if ac.origin_code and ac.dest_code:
            col = int(360 * u)
            ops.append(("t", ac.origin_code, "value", PRIMARY, pad, y, "left"))
            ops.append(("arrow", accent, pad + int(150 * u), y + int(27 * u), int(150 * u),
                        max(2, int(4 * u))))
            ops.append(("t", ac.dest_code, "value", PRIMARY, pad + col, y, "left"))
            cy = y + int(56 * u)
            if ac.origin_name:
                ops.append(("t", t.fit(ac.origin_name.upper(), t.small, col - int(24 * u)),
                            "small", SECONDARY, pad, cy, "left"))
            if ac.dest_name:
                ops.append(("t", t.fit(ac.dest_name.upper(), t.small, inner - col),
                            "small", SECONDARY, pad + col, cy, "left"))
        else:
            text = fallback_route_text(ac, cfg)
            if text:
                ops.append(("t", text, "title", accent, pad, y + int(10 * u), "left"))

        # three readouts
        y = int(392 * u)
        cols = (pad, pad + int(350 * u), pad + int(590 * u))
        dist = (f"{fmt_distance(ac.distance_nm, cfg.distance_units)} "
                f"{compass_point(ac.bearing_deg)}")
        for x, label, value in (
            (cols[0], "ALTITUDE", altitude_text(ac, cfg)),
            (cols[1], "SPEED", fmt_speed(ac.ground_speed_kt, cfg.speed_units)),
            (cols[2], f"FROM {cfg.airport_iata}", dist),
        ):
            ops.append(("t", label, "small", SECONDARY, x, y, "left"))
            ops.append(("t", value, "value", PRIMARY, x, y + int(28 * u), "left"))

        ry = int(492 * u)
        ops.append(("l", RULE, pad, ry, self.w - pad, ry, max(1, int(2 * u))))

    def _table(self, ops, rows, pad: int, mono: float) -> None:
        t, cfg = self.theme, self.cfg
        u = t.u
        top = int(510 * u)
        row_h = int(62 * u)
        bottom = self.h - int(80 * u)
        x_flight = pad + int(28 * u)
        x_mid = pad + int(250 * u)
        x_alt = self.w - pad

        ops.append(("t", "FLIGHT", "small", FAINT, x_flight, top, "left"))
        ops.append(("t", "ROUTE", "small", FAINT, x_mid, top, "left"))
        ops.append(("t", "ALTITUDE", "small", FAINT, x_alt, top, "right"))
        top += int(38 * u)

        max_rows = max(0, min(cfg.max_rows, (bottom - top) // row_h))
        shown = rows[:max_rows]
        mid_px = x_alt - x_mid - 12 * t.char_width(t.row)      # leave room for the altitude
        for ac in shown:
            first = self._row_seen.setdefault(ac.hex, mono)
            color = BRIGHT if 0 <= mono - first < PULSE_SECONDS else PRIMARY
            pip = accent_for(ac)
            if ac.phase == "overflight":
                pip = scale(pip, 0.6)
            ops.append(("c", pip, pad + int(8 * u), top + int(19 * u), int(7 * u)))
            ops.append(("t", t.fit(ac.label or "—", t.row_bold, x_mid - x_flight - int(12 * u)),
                        "row_bold", color, x_flight, top, "left"))
            if ac.origin_code and ac.dest_code:
                ops.append(("t", f"{ac.origin_code} → {ac.dest_code}", "row", color,
                            x_mid, top, "left"))
            else:
                # no trusted route: say who or what it is instead, quieter
                who = (ac.airline or ac.type_name or ac.type_code).upper()
                if who:
                    ops.append(("t", fit_words(t, who, t.row, mid_px), "row", SECONDARY,
                                x_mid, top, "left"))
            ops.append(("t", altitude_text(ac, cfg), "row", color, x_alt, top, "right"))
            ry = top + row_h - int(14 * u)
            ops.append(("l", RULE, pad, ry, self.w - pad, ry, 1))
            top += row_h

        if len(rows) > len(shown):
            ops.append(("t", f"+ {len(rows) - len(shown)} MORE", "small", FAINT, x_alt,
                        top - int(6 * u), "right"))

        live = {a.hex for a in rows}
        for k in [k for k in self._row_seen if k not in live]:
            del self._row_seen[k]

    def _idle(self, ops, healthy: bool, clock_text: str) -> None:
        """Nothing to feature. Says "quiet skies" only when we actually know
        the sky is quiet; with no data coming in, it says that instead."""
        u = self.theme.u
        cx = self.w // 2
        if clock_text:
            ops.append(("t", clock_text, "clock_big", PRIMARY, cx, int(330 * u), "center"))
        if healthy:
            ops.append(("t", "QUIET SKIES", "title", SECONDARY, cx, int(500 * u), "center"))
            ops.append(("t", f"NO AIRCRAFT NEAR {self.cfg.airport_name.upper()} RIGHT NOW",
                        "small", FAINT, cx, int(556 * u), "center"))
        else:
            ops.append(("t", "NO FLIGHT DATA", "title", BAD, cx, int(500 * u), "center"))
            ops.append(("t", "RECONNECTING . . .", "small", SECONDARY, cx, int(556 * u), "center"))
        self._row_seen.clear()

    def _status(self, ops, snap: Snapshot, pad: int, mono: float, count: int,
                date_text: str, clock_text: str, reconnecting: bool, stale: bool) -> None:
        t = self.theme
        u = t.u
        y = self.h - int(44 * u)
        ops.append(("l", RULE, pad, y - int(18 * u), self.w - pad, y - int(18 * u), 1))
        if clock_text:
            ops.append(("t", f"{date_text}   {clock_text}", "small", SECONDARY, pad, y, "left"))

        # per-source health, right to left: dot, name, how many aircraft it sees
        x = self.w - pad
        cw = t.char_width(t.tiny)
        for src in reversed(snap.sources):
            age = src.age(mono)
            good = src.ok and age is not None and age <= self.cfg.stale_after_seconds
            color = OK if good else (WARN if age is not None
                                     and age <= self.cfg.watchdog_seconds else BAD)
            label = SOURCE_LABELS.get(src.name, src.name.upper())
            text = f"{label} {src.aircraft}" if good else label
            x -= len(text) * cw
            ops.append(("t", text, "tiny", SECONDARY if good else FAINT, x, y + int(4 * u), "left"))
            x -= int(16 * u)
            ops.append(("c", color, x + int(4 * u), y + int(15 * u), int(5 * u)))
            x -= int(22 * u)
        if reconnecting or (stale and not snap.sources):
            ops.append(("t", "RECONNECTING", "tiny", BAD, x, y + int(4 * u), "right"))

    # ------------------------------------------------------------------
    # drawing

    def render(self, ops: tuple, brightness: float = 1.0) -> bool:
        """Redraw self.surface if `ops` changed. Returns True when it did."""
        key = (ops, round(brightness, 3))
        if key == self._last_key:
            return False
        self._last_key = key
        s, t = self.surface, self.theme
        s.blit(self._bg, (0, 0))
        for op in ops:
            kind = op[0]
            if kind == "t":
                _, text, font, color, x, y, align = op
                t.blit_text(s, text, getattr(t, font), color, (x, y), align)
            elif kind == "l":
                _, color, x1, y1, x2, y2, width = op
                pygame.draw.line(s, color, (x1, y1), (x2, y2), width)
            elif kind == "c":
                _, color, x, y, r = op
                pygame.draw.circle(s, color, (x, y), r)
            elif kind == "tri":
                _, color, cx, cy, r, up = op
                pts = [(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)] if up else \
                      [(cx, cy + r), (cx - r, cy - r), (cx + r, cy - r)]
                pygame.draw.polygon(s, color, pts)
            elif kind == "arrow":
                _, color, x, y, length, width = op
                head = width * 3
                pygame.draw.line(s, color, (x, y), (x + length - head, y), width)
                pygame.draw.polygon(s, color, [(x + length, y), (x + length - head * 2, y - head),
                                               (x + length - head * 2, y + head)])
        if brightness < 0.999:
            k = max(0, min(255, int(brightness * 255)))
            s.fill((k, k, k), special_flags=pygame.BLEND_RGB_MULT)
        return True
