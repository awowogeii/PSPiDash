import os

import pytest

from s300d import channels as c
from s300d import config as cfg

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_defaults_when_file_missing(tmp_path):
    conf = cfg.load_config(str(tmp_path / "nope.yaml"))
    assert conf == cfg.Config()
    assert conf.scaling_overrides == {}


def test_parse_scalars_and_comments():
    data = cfg.parse_simple_yaml(
        'mac: "AA:BB:CC:DD:EE:FF"  # bt mac\n'
        "rfcomm_channel: 3\n"
        "poll_hz: 12.5\n"
        "flag: true\n"
        "nothing: ~\n"
    )
    assert data == {"mac": "AA:BB:CC:DD:EE:FF", "rfcomm_channel": 3,
                    "poll_hz": 12.5, "flag": True, "nothing": None}


def test_parse_nested_overrides(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "mac: 11:22:33:44:55:66\n"
        "scaling_overrides:\n"
        "  CT_RPM: 1.0\n"
        "  CT_RETARD:\n"
        "    scale: 0.25\n"
        "    offset: 1\n"
    )
    conf = cfg.load_config(str(path))
    assert conf.mac == "11:22:33:44:55:66"
    assert conf.channel == 1  # default
    assert conf.scaling_overrides == {"CT_RPM": 1.0,
                                      "CT_RETARD": {"scale": 0.25, "offset": 1}}


def test_repo_config_yaml_loads_with_placeholders():
    conf = cfg.load_config(os.path.join(REPO_ROOT, "config.yaml"))
    assert conf.mac == "00:00:00:00:00:00"
    assert conf.channel == 1
    assert conf.poll_hz == 10.0
    assert conf.scaling_overrides == {}


def test_overrides_from_config_feed_decoder(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("scaling_overrides:\n  CT_RPM: 0.5\n")
    conf = cfg.load_config(str(path))
    table = c.build_offset_table([(0x0100, c.CS_WORD | 0x03)], conf.scaling_overrides)
    assert c.decode_packet(table, b"\x20\x12")["RPM"] == 2320


def test_bad_line_raises():
    with pytest.raises(ValueError):
        cfg.parse_simple_yaml("just a bare line\n")


def test_parse_lists_block_and_flow():
    # block style, dash at the parent key's indent — how the settings page saves
    data = cfg.parse_simple_yaml(
        "ui:\n"
        "  tiles_big:\n"
        "  - boost_psi\n"
        "  - ect_c\n"
        "  tiles_small:\n"
        "    - tps\n"
        "  theme:\n"
        "    bg: '#0b0712'\n"
        "flow: [1, two, 3.5]\n"
        "empty: []\n"
    )
    assert data["ui"]["tiles_big"] == ["boost_psi", "ect_c"]
    assert data["ui"]["tiles_small"] == ["tps"]
    assert data["ui"]["theme"] == {"bg": "#0b0712"}
    assert data["flow"] == [1, "two", 3.5]
    assert data["empty"] == []
    with pytest.raises(ValueError):
        cfg.parse_simple_yaml("- orphan item\n")
