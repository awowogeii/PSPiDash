"""pygame renderer: 800x480 gauge cluster."""
import math
import time

import pygame

from s300ui import layout as L


class Cluster:
    def __init__(self, screen, conf=None):
        self.screen = screen
        self.configure(conf or {})
        pygame.font.init()
        name = pygame.font.match_font("dejavusans,dejavusansmono,freesans") or None
        # display numerals lean bold-italic for the street-racing look
        speed = pygame.font.match_font("dejavusans,dejavusansmono,freesans",
                                       bold=True, italic=True) or name
        self.f_huge = pygame.font.Font(speed, 112)
        self.f_big = pygame.font.Font(speed, 56)
        self.f_mid = pygame.font.Font(speed, 30)
        self.f_small = pygame.font.Font(name, 20)
        self.f_tiny = pygame.font.Font(name, 15)
        self._cache = {}

    def configure(self, conf):
        """(Re)apply config: alarm thresholds, units, tile layout. Called at
        startup and again whenever config.yaml changes on disk."""
        ui = conf.get("ui") or {}
        L.set_theme(ui.get("theme"))
        self.rules = conf.get("alarms") or {}
        self.units = ui.get("units", "metric")
        self.show_rpm = bool(ui.get("show_rpm", True))
        self.tile_style = L.normalize_style(ui.get("tile_style"))
        try:
            self.danger_rpm = float(ui.get("danger_rpm")) or None
        except (TypeError, ValueError):
            self.danger_rpm = None
        self.tiles_big = L.normalize_tiles(ui.get("tiles_big"), L.DEFAULT_TILES["big"])
        self.tiles_small = L.normalize_tiles(ui.get("tiles_small"), L.DEFAULT_TILES["small"])

    # -- text helpers ---------------------------------------------------------

    def text(self, font, s, color, pos, anchor="topleft"):
        key = (id(font), s, color)
        surf = self._cache.get(key)
        if surf is None:
            surf = font.render(s, True, color)
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[key] = surf
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def thresholds(self, rule_id):
        r = self.rules.get(rule_id) or {}
        if not r.get("enabled", True):
            return None, None, "above"
        return r.get("warn"), r.get("critical"), r.get("direction", "above")

    # -- widgets --------------------------------------------------------------

    def panel(self, rect, color, cut=12):
        """Angular card: rect with the top-left and bottom-right corners cut."""
        x, y, w, h = (int(v) for v in rect)
        c = min(cut, w // 4, h // 4)
        pygame.draw.polygon(self.screen, color,
                            [(x + c, y), (x + w, y), (x + w, y + h - c),
                             (x + w - c, y + h), (x, y + h), (x, y + c)])

    # analog gauge sweep: 270°, from lower-left (225°) clockwise to lower-right
    @staticmethod
    def _angle(frac):
        return math.radians(225.0 - 270.0 * frac)

    def _arc_point(self, cx, cy, r, frac):
        a = self._angle(frac)
        return (cx + math.cos(a) * r, cy - math.sin(a) * r)

    def _arc(self, cx, cy, r, f0, f1, color, width):
        steps = max(2, int((f1 - f0) * 48))
        pts = [self._arc_point(cx, cy, r, f0 + (f1 - f0) * i / steps)
               for i in range(steps + 1)]
        pygame.draw.lines(self.screen, color, False, pts, width)

    def gauge_tile(self, rect, key, d, show_number):
        """Analog needle gauge; the sensor must have a range."""
        spec = L.SENSORS[key]
        x, y, w, h = rect
        self.panel(rect, L.TILE)
        self.text(self.f_small, spec["label"], L.MUTED, (x + 14, y + 10))
        lo, hi = spec["range"]
        # keep the arc clear of the label row; centre the leftover space.
        # the 270° sweep spans r above the hub and ~0.71r below it.
        r = min(w * 0.40, (h - 48) * 0.58)
        extra = max(0.0, (h - 40 - 1.71 * r) / 2)
        cx, cy = x + w / 2, y + 36 + extra + r
        self._arc(cx, cy, r, 0.0, 1.0, L.BG, 6)
        rule = spec.get("rule")
        if rule:
            warn, crit, direction = self.thresholds(rule)
            for f0, f1, level in L.gauge_zones(lo, hi, warn, crit, direction):
                self._arc(cx, cy, r, f0, f1,
                          L.AMBER if level == "warn" else L.RED, 6)
        for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
            pygame.draw.line(self.screen, L.MUTED,
                             self._arc_point(cx, cy, r - 9, tick),
                             self._arc_point(cx, cy, r + 1, tick), 2)
        frac = L.gauge_fraction(d.get(key), lo, hi)
        if frac is not None:
            color = self.sensor_color(key, d)
            needle = L.GREEN if color == L.FG else color  # accent unless alarming
            pygame.draw.line(self.screen, needle, (cx, cy),
                             self._arc_point(cx, cy, r - 5, frac), 4)
        pygame.draw.circle(self.screen, L.MUTED, (int(cx), int(cy)), 5)
        if show_number:
            font = self.f_big if h > 180 else self.f_mid
            self.text(font, L.sensor_text(key, d, self.units),
                      self.sensor_color(key, d), (cx, cy + r * 0.52), "center")

    def rpm_bar(self, d, blink_on):
        rpm = d.get("rpm")
        stage = d.get("shift_stage", 0) or 0
        x, y, w, h = 20, 14, 760, 46
        self.panel((x, y, w, h), L.TILE)
        fill = int(w * L.rpm_fraction(rpm))
        if fill > 26:  # slanted leading edge, matching the panel's cut corner
            pygame.draw.polygon(self.screen, L.rpm_bar_color(stage, blink_on),
                                [(x + 12, y), (x + fill, y), (x + fill - 14, y + h),
                                 (x, y + h), (x, y + 12)])
        elif fill > 0:
            pygame.draw.polygon(self.screen, L.rpm_bar_color(stage, blink_on),
                                [(x, y + 12), (x + fill, y + 12), (x + fill, y + h),
                                 (x, y + h)])
        for k in range(1, 9):
            tx = x + int(w * k * 1000 / L.RPM_MAX)
            pygame.draw.line(self.screen, L.BG, (tx, y), (tx, y + h), 2)
            self.text(self.f_tiny, str(k), L.MUTED, (tx, y + h + 2), "midtop")
        self.text(self.f_tiny, "x1000 rpm", L.MUTED, (x, y + h + 2), "topleft")
        color = L.FG if stage < 3 or blink_on else L.RED
        self.text(self.f_huge, L.fmt(rpm, 0), color, (400, 152), "center")
        vtec = d.get("vtec")
        col = L.BLUE if vtec else L.TILE
        self.panel((660, 105, 120, 44), col)
        self.text(self.f_mid, "VTEC", L.BG if vtec else L.MUTED, (720, 127), "center")
        stage_col = L.STAGE_COLORS.get(stage, L.GREEN) if stage else L.TILE
        self.panel((20, 105, 120, 44), stage_col if (stage < 3 or blink_on) else L.TILE)
        self.text(self.f_mid, "SHIFT" if stage else "", L.FG, (80, 127), "center")

    def tile(self, rect, label, value_text, color, sub=None):
        x, y, w, h = rect
        self.panel(rect, L.TILE)
        self.text(self.f_small, label, L.MUTED, (x + 14, y + 10))
        self.text(self.f_big, value_text, color, (x + w / 2, y + h / 2 + 8), "center")
        if sub:
            self.text(self.f_tiny, sub, L.MUTED, (x + w - 12, y + h - 8), "bottomright")

    def sensor_color(self, key, d):
        spec = L.SENSORS[key]
        if spec.get("flag"):
            if not d.get(key):
                return L.MUTED
            return L.RED if spec.get("alert") else L.BLUE
        if key == "shift_stage":
            stage = d.get("shift_stage") or 0
            return L.STAGE_COLORS.get(stage, L.FG) if stage else L.FG
        rule = spec.get("rule")
        if not rule:
            return L.FG
        w, c, dr = self.thresholds(rule)
        return L.tile_color(d.get(key), w, c, dr)

    def big_tile(self, rect, key, d, afr_available):
        spec = L.SENSORS[key]
        if spec.get("needs_afr") and not afr_available:
            x, y, w, h = rect
            self.panel(rect, L.TILE)
            self.text(self.f_small, "AFR", L.MUTED, (x + 14, y + 10))
            self.text(self.f_tiny, "no wideband", L.MUTED, (x + w / 2, y + h / 2 + 8), "center")
            return
        sub = spec.get("sub")
        self.tile(rect, spec["label"], L.sensor_text(key, d, self.units),
                  self.sensor_color(key, d),
                  L.fmt(d.get(sub[0]), sub[1], sub[2]) if sub else None)

    def small_tile(self, rect, key, d, afr_available):
        spec = L.SENSORS[key]
        x, y, tw, th = rect
        self.panel(rect, L.TILE)
        if spec.get("needs_afr") and not afr_available:
            self.text(self.f_small, "AFR", L.MUTED, (x + 14, y + 8))
            self.text(self.f_tiny, "no wideband", L.MUTED, (x + tw - 14, y + 14), "topright")
            return
        self.text(self.f_small, spec["label"], L.MUTED, (x + 14, y + 8))
        self.text(self.f_mid, L.sensor_text(key, d, self.units), self.sensor_color(key, d),
                  (x + tw - 14, y + 8), "topright")
        if spec.get("bar"):
            v = d.get(key)
            bar = (x + 14, y + th - 22, tw - 28, 10)
            pygame.draw.rect(self.screen, L.BG, bar, border_radius=5)
            if v:
                pygame.draw.rect(self.screen, L.FG,
                                 (bar[0], bar[1], int(bar[2] * max(0, min(100, v)) / 100), bar[3]),
                                 border_radius=5)

    def gauges(self, d, afr_available):
        big_row, small_row = L.tile_rows(self.show_rpm)
        for rect, key in zip(L.grid(4, *big_row), self.tiles_big):
            spec = L.SENSORS[key]
            analog = self.tile_style != "digital" and "range" in spec \
                and not spec.get("flag") \
                and not (spec.get("needs_afr") and not afr_available)
            if analog:
                self.gauge_tile(rect, key, d, self.tile_style == "analog_digital")
            else:
                self.big_tile(rect, key, d, afr_available)
        for rect, key in zip(L.grid(3, *small_row), self.tiles_small):
            self.small_tile(rect, key, d, afr_available)

    def danger_screen(self, rpm, blink_on):
        """Full-screen takeover above danger_rpm: black, hazard stripes,
        blinking red warning. Own palette on purpose — it must not blend in."""
        red, dark = (225, 20, 20), (26, 26, 26)
        self.screen.fill((0, 0, 0))
        sw, sh = L.SCREEN
        band_h, step = 44, 44
        for band_y in (0, sh - band_h):
            for i in range(-1, sw // step + 2):
                x0 = i * step
                pygame.draw.polygon(self.screen, red if i % 2 == 0 else dark,
                                    [(x0, band_y), (x0 + step, band_y),
                                     (x0 + step - 18, band_y + band_h),
                                     (x0 - 18, band_y + band_h)])
        self.text(self.f_mid, "! WARNING !", (255, 255, 255), (sw / 2, 110), "center")
        flash = red if blink_on else (120, 10, 10)  # pulse, never fully blank
        self.text(self.f_huge, "DANGER TO", flash, (sw / 2, 205), "center")
        self.text(self.f_huge, "MANIFOLD", flash, (sw / 2, 315), "center")
        self.text(self.f_mid, L.fmt(rpm, 0) + " rpm", (255, 255, 255),
                  (sw / 2, 400), "center")

    def footer(self, msg, connected, blink_on):
        state = msg.get("state", "DISCONNECTED") if msg else "DISCONNECTED"
        stale = msg.get("stale", True) if msg else True
        label, color = L.state_label(state, stale) if connected else ("NO DAEMON", L.RED)
        self.panel((20, 428, 760, 40), L.TILE)
        pygame.draw.circle(self.screen, color, (42, 448), 8)
        self.text(self.f_small, label, color, (58, 448), "midleft")
        banner = L.alarm_banner(msg.get("a") if msg else None)
        if banner:
            level, text = banner
            col = L.RED if level == "critical" else L.AMBER
            if level == "critical" and not blink_on:
                col = L.TILE
            self.panel((200, 428, 580, 40), col)
            self.text(self.f_small, text, L.BG if col != L.TILE else L.RED, (490, 448), "center")
        else:
            self.text(self.f_tiny, "A: ack   SELECT: release/resume BT", L.MUTED,
                      (770, 448), "midright")

    # -- frame ----------------------------------------------------------------

    def draw(self, msg, connected):
        blink_on = int(time.monotonic() * 6) % 2 == 0
        self.screen.fill(L.BG)
        d = (msg or {}).get("d") or {}
        stale = (msg or {}).get("stale", True) or not connected
        self._danger = L.danger_state(getattr(self, "_danger", False),
                                      d.get("rpm"), self.danger_rpm)
        if self._danger and not stale:
            self.danger_screen(d.get("rpm"), int(time.monotonic() * 8) % 2 == 0)
            return
        if self.show_rpm:
            self.rpm_bar(d, blink_on)
        self.gauges(d, (msg or {}).get("afr_available", False))
        self.footer(msg, connected, blink_on)
        if stale and d:
            veil = pygame.Surface(L.SCREEN, pygame.SRCALPHA)
            veil.fill(L.BG + (110,))
            self.screen.blit(veil, (0, 0))
