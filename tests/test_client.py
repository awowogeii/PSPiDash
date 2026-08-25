import io
import json

import pytest

from s300d import channels as c
from s300d import client as cl
from s300d import protocol as p
from s300d.client import State
from tools import record as rec
from tools.replay import ReplaySource

CHANNELS = [(0x0100, 0x83), (0x0101, 0x84), (0x0102, 0x42), (0x0110, 0x85), (0x0120, 0x47)]
PACKET = bytes.fromhex("2012000001230155")
PACKET_SIZE = 8


def reply(cmd, payload):
    return p.pack_header(cmd, 4 + len(payload)) + payload


def channel_payload(chs):
    return b"".join(bytes([cid & 0xFF, cid >> 8, sat]) for cid, sat in chs)


class StopTest(Exception):
    """Raised by the fake sleeper to break the client's infinite loop."""


class FakeSocket:
    """Scripted ECU. ``script`` maps command id -> reply bytes (or an Exception)."""

    def __init__(self, script, connect_error=None, packets=None, fail_after=None):
        self.script = script
        self.connect_error = connect_error
        self.packets = list(packets) if packets is not None else None
        self.fail_after = fail_after
        self.sent = []
        self.rx = bytearray()
        self.closed = False
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def connect(self, addr):
        self.addr = addr
        if self.connect_error:
            raise self.connect_error

    def sendall(self, data):
        cmd = data[1]
        self.sent.append(cmd)
        if self.fail_after is not None and len(self.sent) > self.fail_after:
            raise OSError("link dropped")
        if cmd == p.CMD_GET_DATALOG_PACKET and self.packets is not None:
            if not self.packets:
                raise OSError("link dropped")
            self.rx += reply(cmd, self.packets.pop(0))
            return
        r = self.script[cmd]
        if isinstance(r, Exception):
            raise r
        self.rx += r

    def recv(self, n):
        if not self.rx:
            raise TimeoutError("timed out")
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def close(self):
        self.closed = True


def good_script(chs=CHANNELS, ignition=True, packet_size=PACKET_SIZE):
    return {
        p.CMD_DEVICE_INFO: reply(0x02, bytes([0xC0, 1 if ignition else 0])),
        p.CMD_GET_DATALOG_INFO: reply(0x30, bytes([len(chs), 0, packet_size, 0])),
        p.CMD_GET_DATALOG_CHANNEL_IDS: reply(0x31, channel_payload(chs)),
        p.CMD_GET_DATALOG_PACKET: reply(0x35, PACKET),
    }


class Harness:
    """Injects a fake clock, sleeper, and a queue of fake sockets."""

    def __init__(self, sockets, stop_after_sleeps=50):
        self.sockets = list(sockets)
        self.created = []
        self.sleeps = []
        self.now = 1000.0
        self.stop_after = stop_after_sleeps

    def factory(self):
        if not self.sockets:
            raise StopTest("no more sockets")
        s = self.sockets.pop(0)
        self.created.append(s)
        return s

    def clock(self):
        return self.now

    def sleep(self, dt):
        self.sleeps.append(dt)
        self.now += dt
        if len(self.sleeps) >= self.stop_after:
            raise StopTest("sleep budget exhausted")

    def source(self, **kw):
        return cl.LiveSource("AA:BB:CC:DD:EE:FF", 1, poll_hz=10, socket_factory=self.factory,
                             clock=self.clock, sleep=self.sleep, **kw)


# --- pure helpers ---------------------------------------------------------

def test_backoff_doubles_to_cap():
    seq, cur = [], None
    for _ in range(7):
        cur = cl.next_backoff(cur, State.ERROR)
        seq.append(cur)
    assert seq == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0, 5.0]


def test_backoff_flat_when_ignition_off():
    assert cl.next_backoff(None, State.IGNITION_OFF) == 10.0
    assert cl.next_backoff(4.0, State.IGNITION_OFF) == 10.0


def test_poll_interval_never_exceeds_half_second():
    assert cl.poll_interval(10) == pytest.approx(0.1)
    assert cl.poll_interval(1) == 0.5
    assert cl.poll_interval(0) == 0.5
    assert cl.poll_interval(None) == 0.5


# --- handshake & streaming ------------------------------------------------

def test_handshake_sends_only_read_commands_in_order():
    h = Harness([FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    h.now += 1.0  # step clock so the discard window has elapsed on first packet
    frame = next(gen)
    sock = h.created[0]
    assert sock.sent[:4] == [0x02, 0x30, 0x31, 0x35]
    assert set(sock.sent) <= {0x02, 0x30, 0x31, 0x35}
    assert sock.timeout == cl.SOCKET_TIMEOUT
    assert src.state is State.STREAMING
    assert src.channel_list == CHANNELS
    assert frame.raw == PACKET
    assert frame.values["RPM"] == pytest.approx(1160)


def test_packets_in_first_10ms_are_discarded():
    sock = FakeSocket(good_script())
    h = Harness([sock])
    src = h.source()
    gen = src.frames()
    # clock does not advance except through sleep(); the poll interval is 0.1 s,
    # so packet 1 is inside the 10 ms window and packet 2 is beyond it.
    next(gen)
    assert sock.sent.count(0x35) == 2


def test_measured_rate_logged_after_100_packets(caplog):
    h = Harness([FakeSocket(good_script())], stop_after_sleeps=500)
    src = h.source()
    gen = src.frames()
    with caplog.at_level("INFO", logger="s300d.client"):
        for _ in range(100):
            next(gen)
    assert src.measured_rate == pytest.approx(10.0, rel=0.05)
    assert any("achieved poll rate" in r.message for r in caplog.records)


def test_handshake_runs_on_every_connection_and_updates_table():
    new_chs = [(0x0100, 0x83), (0x0160, 0x50)]  # RPM word + ECT byte
    first = FakeSocket(good_script(), packets=[PACKET, PACKET])
    second = FakeSocket(good_script(new_chs, packet_size=3), packets=[b"\x20\x12\xB4"] * 5)
    h = Harness([first, second])
    src = h.source()
    gen = src.frames()
    h.now += 1
    next(gen)
    assert src.channel_list == CHANNELS
    h.now += 1
    frame = next(gen)  # first socket drops after 2 packets -> reconnect + re-handshake
    assert second.sent[:3] == [0x02, 0x30, 0x31]
    assert src.channel_list == new_chs
    assert frame.values == {"RPM": 1160, "ECT": 180}


def test_packet_size_mismatch_is_rejected():
    h = Harness([FakeSocket(good_script(packet_size=9)), FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    h.now += 1
    next(gen)
    assert h.created[0].closed
    assert len(h.created) == 2  # first handshake failed, second succeeded


# --- errors, backoff, ignition ----------------------------------------------

def test_connect_failures_back_off_then_recover(caplog):
    fails = [FakeSocket({}, connect_error=OSError("host down")) for _ in range(5)]
    h = Harness(fails + [FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    with caplog.at_level("INFO", logger="s300d.client"):
        h.now += 1
        frame = next(gen)
    assert h.sleeps[:5] == [0.5, 1.0, 2.0, 4.0, 5.0]
    assert frame.values["Gear"] == 1
    assert src.state is State.STREAMING
    # transitions logged once per change, not per iteration
    msgs = [r.message for r in caplog.records if r.message.startswith("state ")]
    assert msgs.count("state DISCONNECTED -> CONNECTING") == 6
    assert msgs.count("state CONNECTING -> ERROR") == 5


def test_link_drop_mid_stream_does_not_raise():
    dropping = FakeSocket(good_script(), packets=[PACKET, PACKET, PACKET])
    h = Harness([dropping, FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    h.now += 1
    for _ in range(4):
        next(gen)
    assert dropping.closed
    assert src.state is State.STREAMING
    assert 0.5 in h.sleeps


def test_ignition_off_polls_every_10s_without_error_logging(caplog):
    h = Harness([FakeSocket(good_script(ignition=False)) for _ in range(3)], stop_after_sleeps=3)
    src = h.source()
    with caplog.at_level("WARNING", logger="s300d.client"):
        with pytest.raises(StopTest):
            next(src.frames())
    assert h.sleeps == [10.0, 10.0, 10.0]
    assert not caplog.records  # ignition off is not an error
    assert all(s.sent == [0x02] for s in h.created)


def test_ignition_on_after_off_resets_backoff():
    h = Harness([FakeSocket(good_script(ignition=False)), FakeSocket(good_script()),
                 FakeSocket({}, connect_error=OSError("x")), FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    h.now += 1
    next(gen)
    assert h.sleeps[0] == 10.0
    h.created[1].packets = []  # force drop on next poll
    h.created[1].fail_after = len(h.created[1].sent)
    next(gen)
    assert h.sleeps[-3:] == [0.5, 1.0, 0.1]


def test_socket_timeout_is_caught():
    hang = FakeSocket({0x02: b""})  # replies nothing -> recv times out
    h = Harness([hang, FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    h.now += 1
    next(gen)
    assert hang.closed and src.state is State.STREAMING


# --- release / resume ---------------------------------------------------------

def test_release_closes_socket_and_holds_disconnected():
    sock = FakeSocket(good_script())
    h = Harness([sock, FakeSocket(good_script())], stop_after_sleeps=6)
    src = h.source()
    gen = src.frames()
    h.now += 1
    next(gen)
    src.release()
    assert sock.closed
    assert src.state is State.DISCONNECTED
    with pytest.raises(StopTest):
        next(gen)
    assert len(h.created) == 1  # no reconnect while released
    assert all(s == cl.RELEASE_POLL for s in h.sleeps[-3:])


def test_resume_reconnects_and_rehandshakes():
    h = Harness([FakeSocket(good_script()), FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    h.now += 1
    next(gen)
    src.release()
    src.resume()
    h.now += 1
    next(gen)
    assert len(h.created) == 2
    assert h.created[1].sent[:3] == [0x02, 0x30, 0x31]


def test_close_terminates_generator():
    h = Harness([FakeSocket(good_script())])
    src = h.source()
    gen = src.frames()
    h.now += 1
    next(gen)
    src.close()
    assert src.state is State.DISCONNECTED
    with pytest.raises(StopIteration):
        next(gen)


# --- record / replay ------------------------------------------------------------

def test_record_then_replay_produces_identical_frames(tmp_path):
    h = Harness([FakeSocket(good_script())], stop_after_sleeps=100)
    src = h.source()
    h.now += 1
    fh = io.StringIO()
    n = rec.record(src, fh, seconds=0.45, clock=h.clock)
    assert n >= 4
    lines = [json.loads(l) for l in fh.getvalue().splitlines()]
    assert lines[0]["type"] == "channels" and lines[0]["packet_size"] == 8
    assert lines[0]["channels"] == [list(ch) for ch in CHANNELS]
    assert all(l["type"] == "packet" for l in lines[1:])

    path = tmp_path / "cap.jsonl"
    path.write_text(fh.getvalue())
    live = [(l["t"], bytes.fromhex(l["raw"])) for l in lines[1:]]
    live_values = [c.decode_packet(src.table, raw) for _, raw in live]

    rh = Harness([])
    rp = ReplaySource(str(path), clock=rh.clock, sleep=rh.sleep)
    got = list(rp.frames())
    assert [(f.t, f.raw) for f in got] == live
    assert [f.values for f in got] == live_values
    assert rp.channel_list == CHANNELS
    assert rp.state is State.DISCONNECTED


def test_replay_speed_multiplier(tmp_path):
    path = tmp_path / "cap.jsonl"
    hdr = {"type": "channels", "device": "S300", "channels": [list(ch) for ch in CHANNELS],
           "packet_size": 8}
    pkts = [{"type": "packet", "t": 100.0 + i, "raw": PACKET.hex()} for i in range(4)]
    path.write_text("\n".join(json.dumps(x) for x in [hdr] + pkts) + "\n")
    rh = Harness([])
    list(ReplaySource(str(path), speed=2.0, clock=rh.clock, sleep=rh.sleep).frames())
    assert rh.sleeps == pytest.approx([0.5, 0.5, 0.5])


def test_replay_release_and_resume(tmp_path):
    path = tmp_path / "cap.jsonl"
    hdr = {"type": "channels", "device": "S300", "channels": [list(ch) for ch in CHANNELS],
           "packet_size": 8}
    pkts = [{"type": "packet", "t": 100.0 + i * 0.1, "raw": PACKET.hex()} for i in range(3)]
    path.write_text("\n".join(json.dumps(x) for x in [hdr] + pkts) + "\n")
    rh = Harness([])
    rp = ReplaySource(str(path), clock=rh.clock, sleep=rh.sleep)
    gen = rp.frames()
    next(gen)
    rp.release()
    calls = {"n": 0}

    def sleep(dt):
        calls["n"] += 1
        rh.now += dt
        if calls["n"] == 3:
            rp.resume()

    rp._sleep = sleep
    f = next(gen)
    assert f.t == pytest.approx(100.1)
    assert rp.state is State.STREAMING
