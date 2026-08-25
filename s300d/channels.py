"""Datalog channel registry, SizeAndType decoding, unit scaling and packet decoding.

The hot path is ``decode_packet(table, payload)``: a pure function that does
one struct.unpack over the whole payload and a linear scale per channel. Build
the table once per connection with ``build_offset_table`` and reuse it.
"""
import struct

# --- Channel IDs -----------------------------------------------------------

CHANNEL_NAMES = {
    0x0100: "RPM",
    0x0101: "Speed",
    0x0102: "Gear",
    0x0110: "MAP",
    0x0111: "MAPVoltage",
    0x0120: "TPS",
    0x0130: "InjectorDuration",
    0x0132: "InjectorDuty",
    0x0140: "IgnitionAdvance",
    0x0141: "IgnitionDwell",
    0x0150: "IAT",
    0x0160: "ECT",
    0x0170: "BarometricPressure",
    0x0180: "BatteryVoltage",
    0x0200: "VtecSpool",
    0x0201: "VtecPressure",
    0x0320: "Lambda",
    0x0321: "CorrectedLambda",
    0x0322: "TargetLambda",
    0x0328: "WidebandVoltage",
    0x0329: "WidebandLambda",
    0x0400: "KnockLevel",
    0x0402: "KnockThreshold",
    0x0410: "KnockRetard",
    0x0420: "KnockCount",
    0x0710: "RevLimiter",
    0x0711: "IgnitionCut",
    0x0712: "BoostCut",
    0x0713: "LaunchCut",
    0x0715: "ShiftCut",
    0x0730: "BoostControlDuty",
    0x0900: "AnalogInput1",
    0x0901: "AnalogInput2",
}


def channel_name(channel_id):
    return CHANNEL_NAMES.get(channel_id, "unknown_0x%04X" % channel_id)


# --- SizeAndType -----------------------------------------------------------

SIZE_MASK = 0xC0
TYPE_MASK = 0x3F
CS_BYTE = 0x40
CS_WORD = 0x80
CS_DWORD = 0xC0

SIZE_BYTES = {CS_BYTE: 1, CS_WORD: 2, CS_DWORD: 4}

TYPE_NAMES = {
    0x01: "CT_BIT",
    0x02: "CT_NUMBER",
    0x03: "CT_RPM",
    0x04: "CT_SPEED",
    0x05: "CT_MBAR",
    0x06: "CT_KPA",
    0x07: "CT_TPS",
    0x08: "CT_INJ",
    0x09: "CT_IGN",
    0x0B: "CT_RETARD",
    0x10: "CT_TEMP",
    0x11: "CT_PCT",
    0x12: "CT_PCT_SIGNED",
    0x13: "CT_PCT_CHG",
    0x16: "CT_MASSFLOW",
    0x18: "CT_5V",
    0x19: "CT_19V",
    0x1E: "CT_LAMBDA",
    0x20: "CT_BAR",
    0x21: "CT_MM",
    0x22: "CT_GFORCE",
    0x23: "CT_SIGNED",
    0x24: "CT_SIGNED100",
}
TYPE_CODES = {v: k for k, v in TYPE_NAMES.items()}

# Every conversion is linear: value = raw * scale + offset.
# CT_BIT is special-cased to bool. Unknown types fall back to raw.
SCALING = {
    "CT_BIT": (1.0, 0.0),
    "CT_NUMBER": (1.0, 0.0),
    # TODO: verify CT_RPM against a physical tachometer. The Hondata spec text
    # and its worked example disagree on this factor; 0.25 is provisional.
    "CT_RPM": (0.25, 0.0),
    "CT_SPEED": (0.01, 0.0),
    "CT_MBAR": (0.1, 0.0),
    "CT_KPA": (0.5, 0.0),
    "CT_TPS": (0.5, -10.0),
    "CT_INJ": (0.001, 0.0),
    "CT_IGN": (0.5, -10.0),
    # TODO: verify CT_RETARD against a known timing value. The Hondata spec
    # text and its worked example disagree on this factor; 0.5 is provisional.
    "CT_RETARD": (0.5, 0.0),
    "CT_TEMP": (1.0, 0.0),           # degF; convert to degC in presentation layer
    "CT_PCT": (1.0 / 2.56, 0.0),
    "CT_PCT_SIGNED": (1.0 / 1.28, -100.0),  # (raw - 128) / 1.28
    "CT_PCT_CHG": (0.01, 0.0),
    "CT_MASSFLOW": (1.0, 0.0),
    "CT_5V": (5.0 / 256.0, 0.0),
    "CT_19V": (0.05, 6.0),           # 6.0 + raw / 20
    "CT_LAMBDA": (1.0 / 32768.0, 0.0),
    "CT_BAR": (1.0, 0.0),
    "CT_MM": (1.0, 0.0),
    "CT_GFORCE": (1.0, 0.0),
    "CT_SIGNED": (1.0, 0.0),
    "CT_SIGNED100": (0.01, 0.0),
}

SIGNED_TYPES = frozenset(("CT_SIGNED", "CT_SIGNED100"))

# Integer-valued types return int rather than float.
INT_TYPES = frozenset(("CT_NUMBER", "CT_TEMP", "CT_MASSFLOW", "CT_BAR", "CT_MM",
                       "CT_GFORCE", "CT_SIGNED"))

_FMT = {(1, False): "B", (2, False): "H", (4, False): "I",
        (1, True): "b", (2, True): "h", (4, True): "i"}


def decode_size_and_type(sat):
    """SizeAndType byte -> (size_in_bytes, type_code)."""
    size = SIZE_BYTES.get(sat & SIZE_MASK)
    if size is None:
        raise ValueError("invalid storage size in SizeAndType 0x%02X" % sat)
    return size, sat & TYPE_MASK


def parse_channel_ids(payload):
    """0x31 response payload -> list of (channel_id, size_and_type)."""
    if len(payload) % 3:
        raise ValueError("channel ID payload length %d not a multiple of 3" % len(payload))
    return [struct.unpack_from("<HB", payload, i) for i in range(0, len(payload), 3)]


def resolve_scaling(overrides=None):
    """Merge config ``scaling_overrides`` into SCALING.

    An override value may be a number (scale only, offset 0) or a mapping with
    ``scale`` and/or ``offset`` keys.
    """
    table = dict(SCALING)
    for name, value in (overrides or {}).items():
        if name not in TYPE_CODES:
            raise ValueError("unknown type name in scaling_overrides: %r" % name)
        base_scale, base_offset = table[name]
        if isinstance(value, dict):
            table[name] = (float(value.get("scale", base_scale)),
                           float(value.get("offset", base_offset)))
        else:
            table[name] = (float(value), 0.0)
    return table


def build_offset_table(channel_list, scaling_overrides=None):
    """Build the decode table from the 0x31 channel list.

    Returns (packet_struct, entries, offsets) where entries is a tuple of
    (name, type_name, scale, offset) in packet order and offsets maps channel
    name -> byte offset (useful for diagnostics; not used by decode_packet).
    """
    scaling = resolve_scaling(scaling_overrides)
    fmt = "<"
    entries = []
    offsets = {}
    pos = 0
    for channel_id, sat in channel_list:
        size, type_code = decode_size_and_type(sat)
        type_name = TYPE_NAMES.get(type_code, "unknown_type_0x%02X" % type_code)
        fmt += _FMT[(size, type_name in SIGNED_TYPES)]
        scale, offset = scaling.get(type_name, (1.0, 0.0))
        name = channel_name(channel_id)
        entries.append((name, type_name, scale, offset))
        offsets[name] = pos
        pos += size
    return struct.Struct(fmt), tuple(entries), offsets


def decode_packet(table, payload):
    """Pure decoder: (offset_table, packet_bytes) -> {channel_name: value}."""
    packet_struct, entries, _ = table
    raws = packet_struct.unpack_from(payload)
    out = {}
    for (name, type_name, scale, offset), raw in zip(entries, raws):
        if type_name == "CT_BIT":
            out[name] = bool(raw)
        elif type_name in INT_TYPES and scale == 1.0 and offset == 0.0:
            out[name] = raw
        else:
            out[name] = raw * scale + offset
    return out
