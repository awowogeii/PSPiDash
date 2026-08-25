"""s300d daemon: ``python -m s300d [--replay capture.bin [--speed N] [--loop]]``."""
import argparse
import logging
import sys

import yaml

from s300d import server, settings
from s300d.client import LiveSource


def load_full_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_source(args, conf):
    overrides = conf.get("scaling_overrides") or {}
    if args.replay:
        from tools.replay import ReplaySource
        return ReplaySource(args.replay, args.speed, args.loop, overrides)
    return LiveSource(conf["mac"], int(conf.get("rfcomm_channel", 1)),
                      float(conf.get("poll_hz", 10)), overrides)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="s300d", description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--replay", metavar="CAPTURE", help="use a capture file instead of the ECU")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--ui-dir", default="ui")
    parser.add_argument("--settings-host", help="override settings.host (use 127.0.0.1 on a laptop)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)

    conf = load_full_config(args.config)
    source = build_source(args, conf)
    hub = server.Hub(source, conf.get("alarms"), conf.get("shift_light"))
    hub.start()
    extra = []
    scfg = conf.get("settings") or {}
    if scfg.get("enabled", True):
        extra.append((settings.make_app(hub, args.config),
                      args.settings_host or scfg.get("host", "10.42.0.1"),
                      int(scfg.get("port", 8081))))
    try:
        server.run(hub, conf.get("server"), args.ui_dir, extra)
    finally:
        hub.stop()


if __name__ == "__main__":
    main()
