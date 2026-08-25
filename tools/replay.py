"""Replay a capture file through the same interface as the live client.

    python -m tools.replay capture.bin [--speed 2.0] [--loop]

Prints decoded frames as JSON lines on stdout, byte-for-byte the same shape
``tools.record --print`` emits live.
"""
import argparse
import json
import time

from s300d import channels
from s300d.client import Frame, State
from tools._common import add_common_args, print_frame, setup_logging


class ReplaySource:
    """Same interface as LiveSource, fed from a capture file."""

    def __init__(self, path, speed=1.0, loop=False, scaling_overrides=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.path = path
        self.speed = speed if speed and speed > 0 else 1.0
        self.loop = loop
        self.scaling_overrides = scaling_overrides or {}
        self._clock = clock
        self._sleep = sleep
        self.state = State.DISCONNECTED
        self.channel_list = None
        self.table = None
        self.device_type = None
        self._released = False
        self._closed = False

    def release(self):
        self._released = True
        self.state = State.DISCONNECTED

    def resume(self):
        self._released = False

    def close(self):
        self._closed = True
        self.state = State.DISCONNECTED

    def _records(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def frames(self):
        while not self._closed:
            self.state = State.HANDSHAKE
            origin_capture = None
            origin_wall = None
            for rec in self._records():
                while self._released and not self._closed:
                    self.state = State.DISCONNECTED
                    self._sleep(0.5)
                if self._closed:
                    return
                if self.table is not None:
                    self.state = State.STREAMING
                if rec["type"] == "channels":
                    self.device_type = rec.get("device")
                    self.channel_list = [tuple(c) for c in rec["channels"]]
                    self.table = channels.build_offset_table(self.channel_list,
                                                             self.scaling_overrides)
                    self.state = State.STREAMING
                    continue
                if self.table is None:
                    raise ValueError("packet before channels header in %s" % self.path)
                t = rec["t"]
                if origin_capture is None:
                    origin_capture, origin_wall = t, self._clock()
                due = origin_wall + (t - origin_capture) / self.speed
                delay = due - self._clock()
                if delay > 0:
                    self._sleep(delay)
                raw = bytes.fromhex(rec["raw"])
                yield Frame(t, raw, channels.decode_packet(self.table, raw))
            if not self.loop:
                break
        self.state = State.DISCONNECTED


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_args(parser)
    parser.add_argument("capture")
    parser.add_argument("--speed", type=float, default=1.0, help="time multiplier")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    from s300d import config as cfg
    conf = cfg.load_config(args.config)
    source = ReplaySource(args.capture, args.speed, args.loop, conf.scaling_overrides)
    try:
        for frame in source.frames():
            print_frame(frame)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        source.close()


if __name__ == "__main__":
    main()
