"""Native cluster UI: ``python -m s300ui [--windowed] [--config config.yaml]``.

On the Pi it draws straight to the DRM/KMS framebuffer (no X/Wayland). Use
--windowed on a laptop. Reads alarm thresholds + ui settings from config.yaml
so tiles colour themselves consistently with the daemon's alarms.
"""
import argparse
import logging
import os
import sys
import time

import yaml

from s300ui.wsclient import DaemonClient

log = logging.getLogger("s300ui")

# PSPi 6 gamepad driver exposes a "PS3 Controller": b0=cross b8=select
# b9=start b10=home. Home is handled by deploy/cluster_toggle.py (root),
# not here. Keep keyboard equivalents and make indices configurable under
# ui.buttons in case of a different driver build.
DEFAULT_BUTTONS = {"ack": 0, "release": 8, "resume": 8, "page": 9, "quit": None}


def read_conf(path):
    try:
        return yaml.safe_load(open(path, encoding="utf-8")) or {}
    except OSError:
        return {}


def conf_mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="s300ui")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--windowed", action="store_true", help="run in a window (laptop dev)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    conf = read_conf(args.config)
    ui = conf.get("ui") or {}
    buttons = dict(DEFAULT_BUTTONS, **(ui.get("buttons") or {}))

    if not args.windowed:
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame
    from s300ui import layout as L
    from s300ui.render import Cluster
    pygame.display.init()
    pygame.joystick.init()
    flags = 0 if args.windowed else pygame.FULLSCREEN
    screen = pygame.display.set_mode(L.SCREEN, flags)
    pygame.display.set_caption("s300 cluster")
    pygame.mouse.set_visible(False)
    sticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
    for s in sticks:
        s.init()
    log.info("display %s, %d joystick(s)", pygame.display.get_driver(), len(sticks))

    client = DaemonClient(ui.get("ws", "ws://127.0.0.1:8080/ws"))
    client.start()
    cluster = Cluster(screen, conf)
    clock = pygame.time.Clock()
    released = False
    running = True
    # Re-read config when the settings page (or anyone) rewrites it, so tile
    # layout / units / alarm thresholds apply without restarting the UI.
    mtime = conf_mtime(args.config)
    next_check = time.monotonic() + 2.0
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q) and args.windowed:
                    running = False
                elif ev.key in (pygame.K_RETURN, pygame.K_a, pygame.K_SPACE):
                    client.send("ack_alarms")
                elif ev.key == pygame.K_r:
                    released = not released
                    client.send("release_bt" if released else "resume_bt")
                elif ev.key in (pygame.K_TAB, pygame.K_p):
                    cluster.toggle_page()
            elif ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == buttons["ack"]:
                    client.send("ack_alarms")
                elif ev.button in (buttons["release"], buttons["resume"]):
                    released = not released
                    client.send("release_bt" if released else "resume_bt")
                elif ev.button == buttons.get("page"):
                    cluster.toggle_page()
                elif buttons["quit"] is not None and ev.button == buttons["quit"]:
                    running = False
        now = time.monotonic()
        if now >= next_check:
            next_check = now + 2.0
            m = conf_mtime(args.config)
            if m != mtime:
                mtime = m
                conf = read_conf(args.config)
                cluster.configure(conf)
                log.info("config reloaded")
        msg = client.message()
        connected = client.connected and client.received_at is not None and \
            time.monotonic() - client.received_at < 2.0
        cluster.draw(msg, connected)
        pygame.display.flip()
        clock.tick(args.fps)
    client.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
