"""pygame renderer: 800x480 gauge cluster."""
import time

import pygame

from s300ui import layout as L


class Cluster:
    def __init__(self, screen, conf=None):
        self.screen = screen
        self.configure(conf or {})
        pygame.font.init()
        name = pygame.font.match_font("dejavusans,dejavusansmono,freesans") or None
        self.f_huge = pygame.font.Font(name, 118)
        self.f_big = pygame.font.Font(name, 60)
        self.f_mid = pygame.font.Font(name, 30)
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

    def rpm_bar(self, d, blink_on):
        rpm = d.get("rpm")
        stage = d.get("shift_stage", 0) or 0
        x, y, w, h = 20, 14, 760, 46
        pygame.draw.rect(self.screen, L.TILE, (x, y, w, h), border_radius=8)
        fill = int(w * L.rpm_fraction(rpm))
        if fill > 0:
            pygame.draw.rect(self.screen, L.rpm_bar_color(stage, blink_on), (x, y, fill, h),
                             border_radius=8)
        for k in range(1, 9):
            tx = x + int(w * k * 1000 / L.RPM_MAX)
            pygame.draw.line(self.screen, L.BG, (tx, y), (tx, y + h), 2)
            self.text(self.f_tiny, str(k), L.MUTED, (tx, y + h + 2), "midtop")
        self.text(self.f_tiny, "x1000 rpm", L.MUTED, (x, y + h + 2), "topleft")
        color = L.FG if stage < 3 or blink_on else L.RED
        self.text(self.f_huge, L.fmt(rpm, 0), color, (400, 152), "center")
        vtec = d.get("vtec")
        col = L.BLUE if vtec else L.TILE
        pygame.draw.rect(self.screen, col, (660, 105, 120, 44), border_radius=8)
        self.text(self.f_mid, "VTEC", L.FG if vtec else L.MUTED, (720, 127), "center")
        stage_col = L.STAGE_COLORS.get(stage, L.GREEN) if stage else L.TILE
        pygame.draw.rect(self.screen, stage_col if (stage < 3 or blink_on) else L.TILE,
                         (20, 105, 120, 44), border_radius=8)
        self.text(self.f_mid, "SHIFT" if stage else "", L.FG, (80, 127), "center")

    def tile(self, rect, label, value_text, color, sub=None):
        x, y, w, h = rect
        pygame.draw.rect(self.screen, L.TILE, rect, border_radius=12)
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
            pygame.draw.rect(self.screen, L.TILE, rect, border_radius=12)
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
        pygame.draw.rect(self.screen, L.TILE, rect, border_radius=12)
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
            self.big_tile(rect, key, d, afr_available)
        for rect, key in zip(L.grid(3, *small_row), self.tiles_small):
            self.small_tile(rect, key, d, afr_available)

    def footer(self, msg, connected, blink_on):
        state = msg.get("state", "DISCONNECTED") if msg else "DISCONNECTED"
        stale = msg.get("stale", True) if msg else True
        label, color = L.state_label(state, stale) if connected else ("NO DAEMON", L.RED)
        pygame.draw.rect(self.screen, L.TILE, (20, 428, 760, 40), border_radius=10)
        pygame.draw.circle(self.screen, color, (42, 448), 8)
        self.text(self.f_small, label, color, (58, 448), "midleft")
        banner = L.alarm_banner(msg.get("a") if msg else None)
        if banner:
            level, text = banner
            col = L.RED if level == "critical" else L.AMBER
            if level == "critical" and not blink_on:
                col = L.TILE
            pygame.draw.rect(self.screen, col, (200, 428, 580, 40), border_radius=10)
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
        if self.show_rpm:
            self.rpm_bar(d, blink_on)
        self.gauges(d, (msg or {}).get("afr_available", False))
        self.footer(msg, connected, blink_on)
        if stale and d:
            veil = pygame.Surface(L.SCREEN, pygame.SRCALPHA)
            veil.fill(L.BG + (110,))
            self.screen.blit(veil, (0, 0))
