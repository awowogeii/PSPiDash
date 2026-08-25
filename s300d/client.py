"""Connection lifecycle for the Hondata S300 over Bluetooth RFCOMM.

Two sources implement the same interface so consumers never branch on which
one they hold:

    source.frames()       -> infinite iterator of Frame(t, raw, values)
    source.state          -> current State
    source.channel_list   -> [(channel_id, size_and_type), ...] once known
    source.release()      -> drop the link and hold DISCONNECTED (frees the ECU)
    source.resume()       -> allow reconnection after release()
    source.close()        -> shut down for good

``LiveSource`` lives here; ``tools.replay.ReplaySource`` is the other one.

State machine:
    DISCONNECTED -> CONNECTING -> HANDSHAKE -> STREAMING
    any of the above -> IGNITION_OFF | ERROR -> DISCONNECTED (then backoff + retry)

Only the four read-only commands 0x02 / 0x30 / 0x31 / 0x35 are ever sent.
"""
import enum
import logging
import socket
import time
from collections import namedtuple

from s300d import channels, protocol

log = logging.getLogger("s300d.client")

Frame = namedtuple("Frame", "t raw values")


class State(enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    HANDSHAKE = "HANDSHAKE"
    STREAMING = "STREAMING"
    IGNITION_OFF = "IGNITION_OFF"
    ERROR = "ERROR"


SOCKET_TIMEOUT = 2.0        # every socket op; a hung recv() is a black screen
MAX_POLL_INTERVAL = 0.5     # ECU stops sampling if no 0x35 for 1 s
DISCARD_WINDOW = 0.010      # ignore datalog packets in first 10 ms after handshake
RATE_SAMPLE_PACKETS = 100
BACKOFF_INITIAL = 0.5
BACKOFF_CAP = 5.0
IGNITION_OFF_POLL = 10.0
RELEASE_POLL = 0.5


def next_backoff(current, last_state):
    """Pure backoff schedule.

    ``current`` is the delay used for the previous attempt (None for the first
    failure). Returns the delay to sleep before the next attempt.
    """
    if last_state is State.IGNITION_OFF:
        return IGNITION_OFF_POLL
    if current is None or current >= BACKOFF_CAP:
        return BACKOFF_INITIAL if current is None else BACKOFF_CAP
    return min(current * 2.0, BACKOFF_CAP)


def poll_interval(poll_hz):
    """Seconds between 0x35 polls, never exceeding MAX_POLL_INTERVAL."""
    if not poll_hz or poll_hz <= 0:
        return MAX_POLL_INTERVAL
    return min(1.0 / poll_hz, MAX_POLL_INTERVAL)


def default_socket_factory():
    return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)


class LiveSource:
    """Streams decoded datalog frames from the ECU, reconnecting forever."""

    def __init__(self, mac, channel, poll_hz=10.0, scaling_overrides=None,
                 socket_factory=default_socket_factory, clock=time.monotonic,
                 sleep=time.sleep, socket_timeout=SOCKET_TIMEOUT):
        self.mac = mac
        self.channel = channel
        self.interval = poll_interval(poll_hz)
        self.scaling_overrides = scaling_overrides or {}
        self._socket_factory = socket_factory
        self._clock = clock
        self._sleep = sleep
        self._timeout = socket_timeout

        self.state = State.DISCONNECTED
        self.channel_list = None
        self.table = None
        self.device_type = None
        self.measured_rate = None
        self._sock = None
        self._buf = bytearray()
        self._released = False
        self._closed = False
        self._backoff = None
        self._last_terminal = None

    # -- public interface ---------------------------------------------------

    def release(self):
        """Drop the RFCOMM link so SManager / the phone can take it."""
        self._released = True
        self._teardown(State.DISCONNECTED)

    def resume(self):
        self._released = False

    def close(self):
        self._closed = True
        self._teardown(State.DISCONNECTED)

    def frames(self):
        """Infinite generator of Frame. Never raises on link errors."""
        while not self._closed:
            if self._released:
                self._sleep(RELEASE_POLL)
                continue
            try:
                self._connect()
                self._handshake()
                self._backoff = None
                yield from self._stream()
            except (OSError, protocol.ProtocolError, ValueError) as exc:
                if self.state is not State.IGNITION_OFF:
                    log.warning("link error in %s: %s", self.state.value, exc)
                    self._set_state(State.ERROR)
            finally:
                self._teardown(State.DISCONNECTED)
            if self._closed or self._released:
                continue
            self._backoff = next_backoff(self._backoff, self._last_terminal)
            log.info("retrying in %.1fs", self._backoff)
            self._sleep(self._backoff)

    # -- internals ----------------------------------------------------------

    def _set_state(self, new):
        if new is self.state:
            return
        log.info("state %s -> %s", self.state.value, new.value)
        if new in (State.ERROR, State.IGNITION_OFF):
            self._last_terminal = new
        elif new is State.STREAMING:
            self._last_terminal = None
        self.state = new

    def _teardown(self, new_state):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buf = bytearray()
        self._set_state(new_state)

    def _connect(self):
        self._set_state(State.CONNECTING)
        sock = self._socket_factory()
        sock.settimeout(self._timeout)
        sock.connect((self.mac, self.channel))
        self._sock = sock

    def _send(self, data):
        self._sock.sendall(data)

    def _recv_reply(self, expected_cmd):
        cmd, payload = protocol.read_packet(self._sock.recv, self._buf)
        if cmd != expected_cmd:
            raise protocol.ProtocolError(
                "expected reply 0x%02X, got 0x%02X" % (expected_cmd, cmd))
        return payload

    def _handshake(self):
        """Runs on EVERY connection: the channel list can change between them."""
        self._set_state(State.HANDSHAKE)
        self._send(protocol.cmd_device_info())
        self.device_type, ignition = protocol.parse_device_info(
            self._recv_reply(protocol.CMD_DEVICE_INFO))
        if not ignition:
            self._set_state(State.IGNITION_OFF)
            raise protocol.ProtocolError("ignition off")

        self._send(protocol.cmd_get_datalog_info())
        count, packet_size = protocol.parse_datalog_info(
            self._recv_reply(protocol.CMD_GET_DATALOG_INFO))

        self._send(protocol.cmd_get_datalog_channel_ids())
        channel_list = channels.parse_channel_ids(
            self._recv_reply(protocol.CMD_GET_DATALOG_CHANNEL_IDS))
        if len(channel_list) != count:
            raise protocol.ProtocolError(
                "0x30 reports %d channels, 0x31 returned %d" % (count, len(channel_list)))
        table = channels.build_offset_table(channel_list, self.scaling_overrides)
        if table[0].size != packet_size:
            raise protocol.ProtocolError(
                "0x30 packet size %d != offset table size %d" % (packet_size, table[0].size))
        self.channel_list = channel_list
        self.table = table
        log.info("handshake ok: %s, %d channels, %d-byte packets",
                 self.device_type, count, packet_size)

    def _stream(self):
        self._set_state(State.STREAMING)
        table = self.table
        packet_size = table[0].size
        started = self._clock()
        count = 0
        rate_t0 = None
        self.measured_rate = None
        while self._sock is not None and not self._closed and not self._released:
            t_sent = self._clock()
            self._send(protocol.cmd_get_datalog_packet())
            raw = self._recv_reply(protocol.CMD_GET_DATALOG_PACKET)
            now = self._clock()
            if len(raw) != packet_size:
                raise protocol.ProtocolError(
                    "datalog packet %d bytes, expected %d" % (len(raw), packet_size))
            if now - started >= DISCARD_WINDOW:
                if rate_t0 is None:
                    rate_t0 = now
                count += 1
                if count == RATE_SAMPLE_PACKETS and now > rate_t0:
                    self.measured_rate = (count - 1) / (now - rate_t0)
                    log.info("achieved poll rate: %.1f packets/s over first %d packets",
                             self.measured_rate, count)
                yield Frame(now, raw, channels.decode_packet(table, raw))
            remaining = self.interval - (self._clock() - t_sent)
            if remaining > 0:
                self._sleep(remaining)
