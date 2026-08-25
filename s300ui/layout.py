"""Pure layout/format helpers for the cluster UI (no pygame, unit-testable)."""

RPM_MAX = 9000
SCREEN = (800, 480)

# theme — every colour is overridable from ui.theme in config.yaml. accent
# is the "good/active" colour (LIVE dot, VTEC, stage-0 shift bar); warn and
# critical should stay high-contrast amber/red-ish so alarms read at a glance.
DEFAULT_THEME = {
    "bg": "#0b0712",        # near-black with a purple cast
    "tile": "#1d1430",      # dark purple card
    "fg": "#efe9fa",        # lavender-white
    "muted": "#8d7fae",     # dim violet-grey
    "accent": "#a06bff",    # purple
    "warn": "#ffaa1e",
    "critical": "#e5324b",
}


def parse_color(value, fallback=None):
    """'#rrggbb' or [r, g, b] -> (r, g, b); anything invalid -> fallback."""
    try:
        if isinstance(value, (list, tuple)) and len(value) == 3:
            color = tuple(int(v) for v in value)
        else:
            s = str(value).strip().lstrip("#")
            color = tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) if len(s) == 6 else None
        if color and all(0 <= v <= 255 for v in color):
            return color
    except (TypeError, ValueError):
        pass
    return fallback


def set_theme(overrides=None):
    """Apply theme colours module-wide; bad or missing entries keep defaults."""
    global BG, FG, MUTED, TILE, AMBER, RED, GREEN, BLUE, STAGE_COLORS
    o = overrides or {}

    def pick(key):
        return parse_color(o.get(key), parse_color(DEFAULT_THEME[key]))

    BG, TILE, FG, MUTED = pick("bg"), pick("tile"), pick("fg"), pick("muted")
    AMBER, RED = pick("warn"), pick("critical")
    GREEN = BLUE = pick("accent")
    STAGE_COLORS = {0: GREEN, 1: AMBER, 2: RED, 3: RED}


set_theme()

# Tileable sensors, keyed by their name in the WS "d" dict (every channel the
# daemon publishes). label is the tile caption; rule names the alarm rule whose
# thresholds colour the tile; sub is (d-key, decimals, unit) shown small in a
# corner; bar adds a 0-100% bar; flag renders ON/OFF (alert flags go critical-
# coloured when ON); speed converts kph -> mph in imperial; needs_afr tiles
# show "no wideband" until 0x0329 exists. Lambda is deliberately uncoloured
# (the lean rule is tps-gated, tiles are not) and knock_count too (its rate
# rule thresholds apply to the increase, not the raw counter).
SENSORS = {
    "rpm":             {"label": "RPM", "decimals": 0},
    "speed_kph":       {"label": "SPEED", "speed": True},
    "gear":            {"label": "GEAR", "decimals": 0},
    "map_kpa":         {"label": "MAP", "decimals": 0, "unit": " kPa"},
    "map_v":           {"label": "MAP SENSOR", "decimals": 2, "unit": " V"},
    "boost_psi":       {"label": "BOOST psi", "decimals": 1, "rule": "overboost",
                        "sub": ("map_kpa", 0, " kPa")},
    "baro_kpa":        {"label": "BARO", "decimals": 0, "unit": " kPa"},
    "tps":             {"label": "THROTTLE", "decimals": 0, "unit": "%", "bar": True},
    "inj_ms":          {"label": "INJECTOR", "decimals": 2, "unit": " ms"},
    "inj_duty":        {"label": "INJ DUTY", "decimals": 0, "unit": "%", "bar": True},
    "ign_adv":         {"label": "IGN ADVANCE", "decimals": 1, "unit": "°"},
    "ign_dwell":       {"label": "DWELL", "decimals": 2, "unit": " ms"},
    "ect_c":           {"label": "COOLANT", "temp": True, "rule": "ect_high"},
    "iat_c":           {"label": "INTAKE", "temp": True, "rule": "iat_high"},
    "vbat":            {"label": "BATTERY", "decimals": 1, "unit": " V", "rule": "battery_low"},
    "vtec":            {"label": "VTEC", "flag": True},
    "vtec_oil":        {"label": "VTEC OIL", "flag": True},
    "target_lambda":   {"label": "TARGET λ", "decimals": 2},
    "wideband_v":      {"label": "WB SENSOR", "decimals": 2, "unit": " V"},
    "wideband_lambda": {"label": "LAMBDA", "decimals": 2, "needs_afr": True},
    "knock_level":     {"label": "KNOCK LEVEL", "decimals": 0},
    "knock_threshold": {"label": "KNOCK THRESH", "decimals": 0},
    "knock_retard":    {"label": "KNOCK RETARD", "decimals": 1, "unit": "°", "rule": "knock_retard"},
    "knock_count":     {"label": "KNOCK COUNT", "decimals": 0},
    "rev_limiter":     {"label": "REV LIMIT", "flag": True, "alert": True},
    "ignition_cut":    {"label": "IGN CUT", "flag": True, "alert": True},
    "boost_cut":       {"label": "BOOST CUT", "flag": True, "alert": True},
    "launch_cut":      {"label": "LAUNCH CUT", "flag": True, "alert": True},
    "shift_cut":       {"label": "SHIFT CUT", "flag": True, "alert": True},
    "boost_duty":      {"label": "BOOST DUTY", "decimals": 0, "unit": "%"},
    "analog1":         {"label": "ANALOG 1", "decimals": 2, "unit": " V"},
    "analog2":         {"label": "ANALOG 2", "decimals": 2, "unit": " V"},
    "shift_stage":     {"label": "SHIFT STAGE", "decimals": 0},
}

DEFAULT_TILES = {"big": ["boost_psi", "ect_c", "iat_c", "vbat"],
                 "small": ["tps", "knock_retard", "wideband_lambda"]}


def rpm_fraction(rpm, rpm_max=RPM_MAX):
    if rpm is None:
        return 0.0
    return max(0.0, min(1.0, float(rpm) / rpm_max))


def rpm_bar_color(stage, blink_on):
    """Bar color for a shift stage; stage 3 alternates red/white."""
    if stage >= 3:
        return FG if blink_on else RED
    return STAGE_COLORS.get(stage, GREEN)


def fmt(value, decimals=0, unit=""):
    if value is None:
        return "--"
    text = "%.*f" % (decimals, value)
    return text + unit if unit else text


def c_to_f(c):
    return None if c is None else c * 9.0 / 5.0 + 32.0


def kph_to_mph(kph):
    return None if kph is None else kph * 0.621371


def temp_text(deg_c, units):
    if units == "imperial":
        return fmt(c_to_f(deg_c), 0, "°F")
    return fmt(deg_c, 0, "°C")


def tile_color(value, warn, critical, direction="above"):
    """Color a tile by where its value sits relative to alarm thresholds."""
    if value is None or warn is None:
        return FG
    beyond = (lambda v, t: v < t) if direction == "below" else (lambda v, t: v > t)
    if critical is not None and beyond(value, critical):
        return RED
    if beyond(value, warn):
        return AMBER
    return FG


def alarm_banner(alarms):
    """(level, text) for the banner, or None. Critical outranks warn."""
    if not alarms:
        return None
    crit = [a for a in alarms if a["level"] == "critical"]
    picked = crit or alarms
    level = "critical" if crit else "warn"
    text = "  ".join(a["id"].replace("_", " ").upper() for a in picked)
    if crit and any(a.get("latched") for a in crit):
        text += "  [press A to ack]"
    return level, text


def state_label(state, stale):
    if state == "STREAMING" and not stale:
        return "LIVE", GREEN
    if state == "STREAMING":
        return "STALE", AMBER
    if state == "IGNITION_OFF":
        return "IGN OFF", MUTED
    if state in ("CONNECTING", "HANDSHAKE"):
        return "CONNECTING", AMBER
    if state == "ERROR":
        return "BT ERROR", RED
    return "NO ECU", MUTED


def normalize_tiles(names, defaults):
    """A validated tile list: exactly ``len(defaults)`` slots (the visible
    sensor count is fixed); unknown or missing entries fall back per-slot."""
    names = list(names) if isinstance(names, (list, tuple)) else []
    return [names[i] if i < len(names) and names[i] in SENSORS else default
            for i, default in enumerate(defaults)]


def sensor_text(key, d, units):
    """Formatted value text for a sensor tile."""
    spec = SENSORS[key]
    value = d.get(key)
    if spec.get("flag"):
        return "--" if value is None else ("ON" if value else "OFF")
    if spec.get("temp"):
        return temp_text(value, units)
    if spec.get("speed"):
        if units == "imperial":
            return fmt(kph_to_mph(value), 0, " mph")
        return fmt(value, 0, " km/h")
    return fmt(value, spec.get("decimals", 0), spec.get("unit", ""))


def tile_rows(show_rpm):
    """((x, y, w, h) big row, (x, y, w, h) small row). With the rpm bar
    hidden the rows grow to claim its space; the footer stays at y=428."""
    if show_rpm:
        return (20, 205, 760, 128), (20, 345, 760, 72)
    return (20, 14, 760, 258), (20, 284, 760, 132)


def grid(cols, x0, y0, width, height, gap=10):
    """Rects (x, y, w, h) for ``cols`` equal tiles in a row."""
    w = (width - gap * (cols - 1)) / cols
    return [(int(x0 + i * (w + gap)), y0, int(w), height) for i in range(cols)]
