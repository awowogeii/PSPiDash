"""Record raw datalog packets from the live ECU to a capture file.

    python -m tools.record --out capture.bin --seconds 60 [--print]

Format: see tools/CAPTURE_FORMAT.md.
"""
import argparse
import json
import sys
import time

from s300d import config as cfg
from s300d.client import LiveSource
from tools._common import add_channel_args, add_common_args, print_frame, setup_logging


def write_channels(fh, source):
    fh.write(json.dumps({"type": "channels", "device": source.device_type,
                         "channels": [list(c) for c in source.channel_list],
                         "packet_size": source.table[0].size}) + "\n")


def record(source, fh, seconds, clock=time.monotonic, on_frame=None):
    """Write frames from ``source`` for ``seconds``; returns packet count."""
    deadline = clock() + seconds
    written_list = None
    count = 0
    for frame in source.frames():
        if source.channel_list != written_list:
            write_channels(fh, source)
            written_list = list(source.channel_list)
        fh.write(json.dumps({"type": "packet", "t": frame.t, "raw": frame.raw.hex()}) + "\n")
        count += 1
        if on_frame:
            on_frame(frame)
        if clock() >= deadline:
            break
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_args(parser)
    add_channel_args(parser)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    conf = cfg.load_config(args.config)
    source = LiveSource(conf.mac, conf.channel, conf.poll_hz, conf.scaling_overrides)
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            n = record(source, fh, args.seconds,
                       on_frame=print_frame if args.print_frames else None)
    finally:
        source.close()
    print("wrote %d packets to %s" % (n, args.out), file=sys.stderr)


if __name__ == "__main__":
    main()
