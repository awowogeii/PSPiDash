#!/usr/bin/env python3
"""PSPi Home/PS button -> toggle between the gauge cluster and the desktop.

Runs as root (deploy/cluster-toggle.service) so it works no matter which UI
currently owns the screen: it stops s300ui and starts lightdm, or the other
way round. Reads the evdev device the PSPi gamepad driver creates directly
(stdlib only - no python-evdev dependency).

On an image without lightdm (Trixie Lite) the service parks itself and the
Home button does nothing.
"""
import os
import struct
import subprocess
import time

DEVICE_NAME = "PS3 Controller"   # uinput name from the PSPi gamepad driver
BTN_MODE = 0x13C                 # Home / PS button keycode (316)
EVENT_FMT = "llHHi"              # struct input_event: timeval + type/code/value
EVENT_SIZE = struct.calcsize(EVENT_FMT)
DEBOUNCE_S = 1.5


def find_device():
    base = "/sys/class/input"
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    for entry in entries:
        if not entry.startswith("event"):
            continue
        try:
            with open(os.path.join(base, entry, "device", "name")) as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name == DEVICE_NAME:
            return "/dev/input/" + entry
    return None


def _run(*args):
    subprocess.call(["systemctl", *args])


def have_desktop():
    return subprocess.call(["systemctl", "cat", "lightdm.service"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def toggle():
    on_desktop = subprocess.call(
        ["systemctl", "is-active", "--quiet", "lightdm.service"]) == 0
    if on_desktop:
        print("home: desktop -> cluster", flush=True)
        _run("stop", "lightdm.service")
        _run("start", "s300ui.service")
    else:
        print("home: cluster -> desktop", flush=True)
        _run("stop", "s300ui.service")
        _run("start", "lightdm.service")


def main():
    if not have_desktop():
        print("no lightdm on this image; Home toggle inactive", flush=True)
        while True:
            time.sleep(3600)
    last = 0.0
    while True:
        path = find_device()
        if path is None:
            time.sleep(2)
            continue
        try:
            with open(path, "rb", buffering=0) as fh:
                print("watching %s for Home button" % path, flush=True)
                while True:
                    data = fh.read(EVENT_SIZE)
                    if not data or len(data) < EVENT_SIZE:
                        break  # device went away; rescan
                    _, _, etype, code, value = struct.unpack(EVENT_FMT, data)
                    if etype == 1 and code == BTN_MODE and value == 1:
                        now = time.monotonic()
                        if now - last > DEBOUNCE_S:
                            last = now
                            toggle()
        except OSError:
            pass
        time.sleep(2)


if __name__ == "__main__":
    main()
