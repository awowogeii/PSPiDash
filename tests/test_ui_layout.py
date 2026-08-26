from s300ui import layout as L


def test_rpm_fraction_clamps():
    assert L.rpm_fraction(None) == 0.0
    assert L.rpm_fraction(4500) == 0.5
    assert L.rpm_fraction(20000) == 1.0
    assert L.rpm_fraction(-5) == 0.0


def test_bar_color_by_stage_and_blink():
    assert L.rpm_bar_color(0, True) == L.GREEN
    assert L.rpm_bar_color(1, True) == L.AMBER
    assert L.rpm_bar_color(2, False) == L.RED
    assert L.rpm_bar_color(3, True) == L.FG and L.rpm_bar_color(3, False) == L.RED


def test_fmt_and_temps():
    assert L.fmt(None) == "--"
    assert L.fmt(14.06, 1, " V") == "14.1 V"
    assert L.temp_text(100, "imperial") == "212°F"
    assert L.temp_text(88.4, "metric") == "88°C"


def test_tile_color_follows_thresholds():
    assert L.tile_color(90, 105, 112) == L.FG
    assert L.tile_color(106, 105, 112) == L.AMBER
    assert L.tile_color(113, 105, 112) == L.RED
    assert L.tile_color(12.0, 13.0, 11.8, "below") == L.AMBER
    assert L.tile_color(11.0, 13.0, 11.8, "below") == L.RED
    assert L.tile_color(90, None, None) == L.FG  # disabled rule


def test_alarm_banner_prefers_critical_and_flags_latch():
    assert L.alarm_banner([]) is None
    lvl, txt = L.alarm_banner([{"id": "ect_high", "level": "warn", "latched": False}])
    assert lvl == "warn" and txt == "ECT HIGH"
    lvl, txt = L.alarm_banner([{"id": "iat_high", "level": "warn", "latched": False},
                               {"id": "ect_high", "level": "critical", "latched": True}])
    assert lvl == "critical" and txt.startswith("ECT HIGH") and "ack" in txt


def test_state_labels():
    assert L.state_label("STREAMING", False)[0] == "LIVE"
    assert L.state_label("STREAMING", True)[0] == "STALE"
    assert L.state_label("IGNITION_OFF", True)[0] == "IGN OFF"
    assert L.state_label("ERROR", True)[0] == "BT ERROR"


def test_default_tiles_are_registered_sensors():
    assert set(L.DEFAULT_TILES["big"] + L.DEFAULT_TILES["small"]) <= set(L.SENSORS)


def test_sensor_registry_matches_daemon_public_keys():
    from s300d import alarms as al
    assert set(L.SENSORS) == set(al.PUBLIC_KEYS) | {"wideband_lambda"}


def test_theme_default_override_and_fallback():
    try:
        assert L.BG == (0x0B, 0x0D, 0x10)          # gunmetal black default
        assert L.GREEN == L.BLUE == (0xFF, 0xB4, 0x00)  # amber accent
        assert L.STAGE_COLORS[0] == L.GREEN
        L.set_theme({"bg": "#102030", "accent": [1, 2, 3], "warn": "junk",
                     "nonsense": "#ffffff"})
        assert L.BG == (0x10, 0x20, 0x30)
        assert L.GREEN == (1, 2, 3) and L.STAGE_COLORS[0] == (1, 2, 3)
        assert L.AMBER == (0xFF, 0x6A, 0x00)       # bad value keeps default
    finally:
        L.set_theme()
    assert L.BG == (0x0B, 0x0D, 0x10)


def test_parse_color():
    assert L.parse_color("#a06bff") == (0xA0, 0x6B, 0xFF)
    assert L.parse_color([10, 20, 30]) == (10, 20, 30)
    assert L.parse_color("nope", (1, 1, 1)) == (1, 1, 1)
    assert L.parse_color([300, 0, 0], (1, 1, 1)) == (1, 1, 1)


def test_normalize_tiles_validates_per_slot():
    d = L.DEFAULT_TILES["big"]
    assert L.normalize_tiles(["rpm", "map_kpa", "vtec", "tps"], d) == ["rpm", "map_kpa", "vtec", "tps"]
    assert L.normalize_tiles(None, d) == d                      # missing config
    assert L.normalize_tiles("boost_psi", d) == d               # not a list
    assert L.normalize_tiles(["nope", "map_kpa"], d) == ["boost_psi", "map_kpa", "iat_c", "vbat"]
    assert L.normalize_tiles(["rpm"] * 9, d) == ["rpm"] * 4     # slot count is fixed


def test_sensor_text_formats_each_kind():
    d = {"rpm": 3420, "ect_c": 88.4, "vbat": 14.06, "tps": 55.2,
         "vtec": True, "wideband_lambda": 0.876}
    assert L.sensor_text("rpm", d, "metric") == "3420"
    assert L.sensor_text("ect_c", d, "metric") == "88°C"
    assert L.sensor_text("ect_c", d, "imperial") == "191°F"
    assert L.sensor_text("vbat", d, "metric") == "14.1 V"
    assert L.sensor_text("tps", d, "metric") == "55%"
    assert L.sensor_text("vtec", d, "metric") == "ON"
    assert L.sensor_text("vtec", {}, "metric") == "--"
    assert L.sensor_text("vtec", {"vtec": False}, "metric") == "OFF"
    assert L.sensor_text("wideband_lambda", d, "metric") == "0.88"
    assert L.sensor_text("boost_psi", {}, "metric") == "--"
    assert L.sensor_text("speed_kph", {"speed_kph": 100.0}, "metric") == "100 km/h"
    assert L.sensor_text("speed_kph", {"speed_kph": 100.0}, "imperial") == "62 mph"
    assert L.sensor_text("speed_kph", {}, "metric") == "--"
    assert L.sensor_text("boost_cut", {"boost_cut": True}, "metric") == "ON"
    assert L.sensor_text("inj_ms", {"inj_ms": 2.345}, "metric") == "2.35 ms"


def test_tile_rows_fit_between_rpm_and_footer():
    for show_rpm in (True, False):
        big, small = L.tile_rows(show_rpm)
        assert big[1] + big[3] < small[1]        # rows don't overlap
        assert small[1] + small[3] < 428         # footer starts at 428
    big, small = L.tile_rows(False)
    assert big[1] < 100 and big[3] > 200         # hidden rpm frees real space


def test_sensor_ranges_are_sane():
    for key, spec in L.SENSORS.items():
        if "range" in spec:
            lo, hi = spec["range"]
            assert hi > lo, key
            assert not spec.get("flag"), key  # flags never get a needle


def test_normalize_style():
    assert L.normalize_style("analog") == "analog"
    assert L.normalize_style("analog_digital") == "analog_digital"
    assert L.normalize_style(None) == "digital"
    assert L.normalize_style("bogus") == "digital"


def test_gauge_fraction_clamps():
    assert L.gauge_fraction(50, 0, 100) == 0.5
    assert L.gauge_fraction(-10, 0, 100) == 0.0
    assert L.gauge_fraction(500, 0, 100) == 1.0
    assert L.gauge_fraction(None, 0, 100) is None
    assert L.gauge_fraction(1, 5, 5) is None  # degenerate range
    assert L.gauge_fraction(0, -15, 15) == 0.5  # boost gauge centred at 0 psi


def test_gauge_zones_from_thresholds():
    # ect_high style: above, warn 105, critical 112 on a 40-130 gauge
    zones = L.gauge_zones(40, 130, 105, 112, "above")
    assert zones == [((105 - 40) / 90, (112 - 40) / 90, "warn"),
                     ((112 - 40) / 90, 1.0, "critical")]
    # battery_low style: below
    zones = L.gauge_zones(8, 16, 13.0, 11.8, "below")
    assert [z[2] for z in zones] == ["critical", "warn"]
    assert zones[0][0] == 0.0 and zones[1][1] == (13.0 - 8) / 8
    assert L.gauge_zones(0, 100, None, None) == []
    assert L.gauge_zones(0, 100, 50, None, "above") == [(0.5, 1.0, "warn")]
    # thresholds beyond the range clamp instead of exploding
    assert L.gauge_zones(0, 10, 20, 30, "above") == []


def test_page2_defaults_and_geometry():
    assert len(L.DEFAULT_TILES["page2"]) == 8
    assert set(L.DEFAULT_TILES["page2"]) <= set(L.SENSORS)
    top, bottom = L.page2_rows()
    assert top[1] + top[3] < bottom[1]           # rows don't overlap
    assert bottom[1] + bottom[3] < 428           # footer starts at 428
    assert L.normalize_tiles(["rpm"], L.DEFAULT_TILES["page2"])[0] == "rpm"
    assert len(L.normalize_tiles(None, L.DEFAULT_TILES["page2"])) == 8


def test_danger_state_hysteresis():
    assert L.danger_state(False, 8000, 8000) is True    # trips at the line
    assert L.danger_state(False, 7999, 8000) is False
    assert L.danger_state(True, 7800, 8000) is True     # holds inside the deadband
    assert L.danger_state(True, 7750, 8000) is False    # clears 250 below
    assert L.danger_state(True, 9000, None) is False    # feature off
    assert L.danger_state(True, None, 8000) is False    # no data, no takeover


def test_grid_fills_width():
    rects = L.grid(4, 20, 0, 760, 100, gap=10)
    assert len(rects) == 4 and rects[0][0] == 20
    assert rects[-1][0] + rects[-1][2] <= 780
