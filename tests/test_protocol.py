import pytest

from s300d import protocol as p


def make_stream(data, chunk=None):
    """Fake recv(n) over ``data``; ``chunk`` caps bytes returned per call."""
    buf = bytearray(data)

    def recv(n):
        take = n if chunk is None else min(n, chunk)
        out = bytes(buf[:take])
        del buf[:take]
        return out

    return recv


def test_pack_header_layout():
    assert p.pack_header(0x35, 0x0104) == b"\x00\x35\x04\x01"


def test_unpack_header_roundtrip():
    assert p.unpack_header(p.pack_header(0x31, 40)) == (0x00, 0x31, 40)


def test_unpack_header_rejects_bad_protocol():
    with pytest.raises(p.ProtocolError):
        p.unpack_header(b"\x01\x02\x04\x00")


def test_unpack_header_rejects_size_below_header():
    with pytest.raises(p.ProtocolError):
        p.unpack_header(b"\x00\x02\x03\x00")


def test_unpack_header_rejects_short_buffer():
    with pytest.raises(p.ProtocolError):
        p.unpack_header(b"\x00\x02")


@pytest.mark.parametrize("builder,cmd", [
    (p.cmd_device_info, 0x02),
    (p.cmd_get_datalog_info, 0x30),
    (p.cmd_get_datalog_channel_ids, 0x31),
    (p.cmd_get_datalog_packet, 0x35),
])
def test_commands_are_exactly_four_bytes(builder, cmd):
    assert builder() == bytes([0x00, cmd, 0x04, 0x00])


def test_read_exact_reassembles_one_byte_chunks():
    packet = p.pack_header(0x35, 12) + bytes(range(8))
    recv = make_stream(packet, chunk=1)
    assert p.read_exact(recv, 12) == packet


def test_read_exact_raises_on_early_eof():
    recv = make_stream(b"\x00\x02\x04")
    with pytest.raises(p.ProtocolError):
        p.read_exact(recv, 4)


def test_read_packet_splits_two_packets_in_one_recv():
    a = p.pack_header(0x02, 6) + b"\xC0\x01"
    b = p.pack_header(0x30, 8) + b"\x05\x00\x08\x00"
    recv = make_stream(a + b)  # single recv returns both packets
    buf = bytearray()
    assert p.read_packet(recv, buf) == (0x02, b"\xC0\x01")
    assert bytes(buf) == b  # leftover retained, not consumed from stream
    assert p.read_packet(recv, buf) == (0x30, b"\x05\x00\x08\x00")
    assert buf == b""


def test_read_packet_handles_packet_split_across_recvs():
    packet = p.pack_header(0x35, 9) + b"\x01\x02\x03\x04\x05"
    buf = bytearray()
    assert p.read_packet(make_stream(packet, chunk=3), buf) == (0x35, b"\x01\x02\x03\x04\x05")


def test_read_packet_header_only():
    buf = bytearray()
    assert p.read_packet(make_stream(p.pack_header(0x35, 4)), buf) == (0x35, b"")


def test_read_packet_eof_mid_body():
    buf = bytearray()
    with pytest.raises(p.ProtocolError):
        p.read_packet(make_stream(p.pack_header(0x35, 10) + b"\x00"), buf)


def test_parse_device_info():
    assert p.parse_device_info(b"\xC0\x01") == ("S300", True)
    assert p.parse_device_info(b"\xC1\x00") == ("KPro", False)
    assert p.parse_device_info(b"\xC2\x01") == ("FlashPro", True)
    assert p.parse_device_info(b"\x99\x00") == ("unknown_0x99", False)


def test_parse_datalog_info_little_endian():
    assert p.parse_datalog_info(b"\x21\x00\x40\x01") == (33, 0x0140)
