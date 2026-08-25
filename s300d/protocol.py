"""Hondata binary protocol: header framing and command builders.

Transport-agnostic. The stream reader helpers take a ``recv`` callable
(``recv(n) -> bytes``) so they can be driven by any byte stream, including
in-memory fakes in tests. This module performs no I/O of its own.
"""
import struct

PROTOCOL_HD = 0x00
HEADER_SIZE = 4
_HEADER = struct.Struct("<BBH")  # protocol, command, total size (incl. header)

# Read-only commands used by this project.
CMD_DEVICE_INFO = 0x02          # DL_DeviceInfo
CMD_GET_DATALOG_INFO = 0x30     # DL_GetDatalogInfo
CMD_GET_DATALOG_CHANNEL_IDS = 0x31  # DL_GetDatalogChannelIDs
CMD_GET_DATALOG_PACKET = 0x35   # DL_GetDatalogPacket

COMMAND_SIZE = 4

DEVICE_TYPES = {0xC0: "S300", 0xC1: "KPro", 0xC2: "FlashPro"}


class ProtocolError(Exception):
    """Malformed header or unexpected end of stream."""


def pack_header(command_id, total_size):
    """Return the 4-byte header for a packet of ``total_size`` bytes (header included)."""
    return _HEADER.pack(PROTOCOL_HD, command_id, total_size)


def unpack_header(buf):
    """Return (protocol, command_id, total_size) from the first 4 bytes of ``buf``."""
    if len(buf) < HEADER_SIZE:
        raise ProtocolError("header needs %d bytes, got %d" % (HEADER_SIZE, len(buf)))
    protocol, command_id, total_size = _HEADER.unpack_from(buf)
    if protocol != PROTOCOL_HD:
        raise ProtocolError("bad protocol byte 0x%02X" % protocol)
    if total_size < HEADER_SIZE:
        raise ProtocolError("packet size %d smaller than header" % total_size)
    return protocol, command_id, total_size


def build_command(command_id):
    """Every command is exactly 4 bytes: {0x00, id, 0x04, 0x00}."""
    return pack_header(command_id, COMMAND_SIZE)


def cmd_device_info():
    return build_command(CMD_DEVICE_INFO)


def cmd_get_datalog_info():
    return build_command(CMD_GET_DATALOG_INFO)


def cmd_get_datalog_channel_ids():
    return build_command(CMD_GET_DATALOG_CHANNEL_IDS)


def cmd_get_datalog_packet():
    return build_command(CMD_GET_DATALOG_PACKET)


def read_exact(recv, n):
    """Read exactly ``n`` bytes from ``recv``, reassembling partial reads.

    Raises ProtocolError if the stream ends early.
    """
    out = bytearray()
    while len(out) < n:
        chunk = recv(n - len(out))
        if not chunk:
            raise ProtocolError("stream closed after %d of %d bytes" % (len(out), n))
        out += chunk
    return bytes(out)


def read_packet(recv, buf):
    """Read one complete packet from the stream.

    ``buf`` is a bytearray that persists across calls and holds any bytes
    received beyond the current packet (e.g. when two packets arrive in one
    recv()). Returns (command_id, payload) where payload excludes the header.
    """
    _fill(recv, buf, HEADER_SIZE)
    _, command_id, total_size = unpack_header(buf)
    _fill(recv, buf, total_size)
    payload = bytes(buf[HEADER_SIZE:total_size])
    del buf[:total_size]
    return command_id, payload


def _fill(recv, buf, n):
    while len(buf) < n:
        chunk = recv(4096)
        if not chunk:
            raise ProtocolError("stream closed with %d of %d bytes buffered" % (len(buf), n))
        buf += chunk


def parse_device_info(payload):
    """0x02 response payload -> (device_type_name, ignition_on)."""
    if len(payload) < 2:
        raise ProtocolError("DeviceInfo payload too short")
    device_type, ignition = payload[0], payload[1]
    return DEVICE_TYPES.get(device_type, "unknown_0x%02X" % device_type), bool(ignition)


def parse_datalog_info(payload):
    """0x30 response payload -> (channel_count, packet_size)."""
    if len(payload) < 4:
        raise ProtocolError("DatalogInfo payload too short")
    return struct.unpack_from("<HH", payload)
