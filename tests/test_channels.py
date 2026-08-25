import pytest

from s300d import channels as c

CAPTURE_CHANNELS = [
    (0x0100, c.CS_WORD | 0x03),  # RPM, CT_RPM
    (0x0101, c.CS_WORD | 0x04),  # Speed, CT_SPEED
    (0x0102, c.CS_BYTE | 0x02),  # Gear, CT_NUMBER
    (0x0110, c.CS_WORD | 0x05),  # MAP, CT_MBAR
    (0x0120, c.CS_BYTE | 0x07),  # TPS, CT_TPS
]
CAPTURE_PAYLOAD = bytes.fromhex("20 12 00 00 01 23 01 55")


def test_decode_size_and_type():
    assert c.decode_size_and_type(0x43) == (1, 0x03)
    assert c.decode_size_and_type(0x84) == (2, 0x04)
    assert c.decode_size_and_type(0xDE) == (4, 0x1E)


def test_decode_size_and_type_rejects_zero_size():
    with pytest.raises(ValueError):
        c.decode_size_and_type(0x03)


def test_parse_channel_ids():
    payload = b"\x00\x01\x83" + b"\x20\x03\x9E"
    assert c.parse_channel_ids(payload) == [(0x0100, 0x83), (0x0320, 0x9E)]


def test_parse_channel_ids_rejects_bad_length():
    with pytest.raises(ValueError):
        c.parse_channel_ids(b"\x00\x01")


def test_channel_name_known_and_unknown():
    assert c.channel_name(0x0160) == "ECT"
    assert c.channel_name(0x0ABC) == "unknown_0x0ABC"


def test_offset_table_accumulates_sizes():
    _, entries, offsets = c.build_offset_table(CAPTURE_CHANNELS)
    assert [e[0] for e in entries] == ["RPM", "Speed", "Gear", "MAP", "TPS"]
    assert offsets == {"RPM": 0, "Speed": 2, "Gear": 4, "MAP": 5, "TPS": 7}


def test_decode_real_capture():
    table = c.build_offset_table(CAPTURE_CHANNELS)
    out = c.decode_packet(table, CAPTURE_PAYLOAD)
    assert out["RPM"] == pytest.approx(1160)
    assert out["Speed"] == pytest.approx(0.0)
    assert out["Gear"] == 1
    assert out["MAP"] == pytest.approx(29.1)
    assert out["TPS"] == pytest.approx(32.5)


def test_decoder_is_pure_and_reusable():
    table = c.build_offset_table(CAPTURE_CHANNELS)
    first = c.decode_packet(table, CAPTURE_PAYLOAD)
    second = c.decode_packet(table, CAPTURE_PAYLOAD)
    assert first == second and first is not second


def test_unknown_channel_passthrough_keeps_later_offsets():
    channel_list = [
        (0x0100, c.CS_WORD | 0x03),   # RPM
        (0x0ABC, c.CS_DWORD | 0x02),  # unknown, 4 bytes
        (0x0102, c.CS_BYTE | 0x02),   # Gear
    ]
    payload = bytes.fromhex("20 12") + bytes.fromhex("78 56 34 12") + b"\x03"
    out = c.decode_packet(c.build_offset_table(channel_list), payload)
    assert out["RPM"] == pytest.approx(1160)
    assert out["unknown_0x0ABC"] == 0x12345678
    assert out["Gear"] == 3


def test_unknown_type_code_falls_back_to_raw():
    table = c.build_offset_table([(0x0900, c.CS_WORD | 0x3A)])
    assert c.decode_packet(table, b"\x34\x12") == {"AnalogInput1": 0x1234}


def test_scaling_override_changes_output():
    table = c.build_offset_table(CAPTURE_CHANNELS, {"CT_RPM": 1.0})
    assert c.decode_packet(table, CAPTURE_PAYLOAD)["RPM"] == 4640


def test_scaling_override_mapping_form():
    table = c.build_offset_table([(0x0120, c.CS_BYTE | 0x07)],
                                 {"CT_TPS": {"scale": 1.0, "offset": 0.0}})
    assert c.decode_packet(table, b"\x55")["TPS"] == 85


def test_scaling_override_unknown_type_rejected():
    with pytest.raises(ValueError):
        c.resolve_scaling({"CT_NOPE": 2.0})


@pytest.mark.parametrize("type_name,raw,expected", [
    ("CT_KPA", 200, 100.0),
    ("CT_INJ", 2500, 2.5),
    ("CT_IGN", 60, 20.0),
    ("CT_RETARD", 7, 3.5),
    ("CT_TEMP", 180, 180),
    ("CT_PCT", 256, 100.0),
    ("CT_PCT_SIGNED", 128, 0.0),
    ("CT_PCT_CHG", 1500, 15.0),
    ("CT_5V", 128, 2.5),
    ("CT_19V", 160, 14.0),
    ("CT_LAMBDA", 32768, 1.0),
    ("CT_SIGNED100", 250, 2.5),
])
def test_scaling_functions(type_name, raw, expected):
    scale, offset = c.SCALING[type_name]
    assert raw * scale + offset == pytest.approx(expected)


def test_bit_type_decodes_to_bool():
    table = c.build_offset_table([(0x0200, c.CS_BYTE | 0x01), (0x0711, c.CS_BYTE | 0x01)])
    assert c.decode_packet(table, b"\x01\x00") == {"VtecSpool": True, "IgnitionCut": False}


def test_signed_types_use_signed_unpack():
    table = c.build_offset_table([(0x0410, c.CS_WORD | 0x23), (0x0420, c.CS_BYTE | 0x24)])
    out = c.decode_packet(table, b"\xFE\xFF\x9C")
    assert out["KnockRetard"] == -2
    assert out["KnockCount"] == pytest.approx(-1.0)


def test_all_required_channel_ids_registered():
    required = [0x0100, 0x0101, 0x0102, 0x0110, 0x0111, 0x0120, 0x0130, 0x0132,
                0x0140, 0x0141, 0x0150, 0x0160, 0x0170, 0x0180, 0x0200, 0x0201,
                0x0320, 0x0321, 0x0322, 0x0328, 0x0329, 0x0400, 0x0402, 0x0410,
                0x0420, 0x0710, 0x0711, 0x0712, 0x0713, 0x0715, 0x0730, 0x0900, 0x0901]
    assert all(cid in c.CHANNEL_NAMES for cid in required)
    assert len(c.CHANNEL_NAMES) == len(required)
