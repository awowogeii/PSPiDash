"""Shared helpers for the tools package (config + logging + output)."""
import argparse
import json
import logging
import sys

from s300d import config as cfg


def add_common_args(parser):
    parser.add_argument("--config", default=cfg.DEFAULT_CONFIG_PATH)
    parser.add_argument("-v", "--verbose", action="store_true")


def setup_logging(verbose):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)


def print_frame(frame):
    """Decoded frame as one JSON line on stdout — identical for live and replay."""
    sys.stdout.write(json.dumps({"t": round(frame.t, 6), "values": frame.values}) + "\n")
    sys.stdout.flush()


def add_channel_args(parser):
    parser.add_argument("--print", dest="print_frames", action="store_true",
                        help="print decoded frames as JSON lines on stdout")
