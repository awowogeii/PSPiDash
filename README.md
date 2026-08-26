# PSPiDash — Hondata S300 gauge cluster on a PSPi 6

A self-contained digital dash for a Honda running a **Hondata S300 V3**,
built on a **PSPi 6** handheld (Raspberry Pi CM4, 800×480 DPI display).
A daemon reads live engine data over Bluetooth RFCOMM using the Hondata
binary protocol; a native pygame UI draws straight to DRM/KMS (no desktop
needed, ~7–8 s power-on to gauges); a phone-facing settings page runs on the
Pi's own WiFi hotspot.

    S300 --BT RFCOMM--> s300d (daemon) --ws://127.0.0.1:8080/ws--> s300ui (pygame/KMS)
                          |
                          +-- http://10.42.0.1:8081  settings page on the Pi's hotspot

The ECU link is strictly **read-only**: only DeviceInfo, DatalogInfo,
ChannelIDs and DatalogPacket commands are ever sent. No writes, no DTC
operations. [docs/PROTOCOL.md](docs/PROTOCOL.md) is the protocol ground truth.

## Features

- **Every datalog channel as a gauge** — rpm, speed, gear, MAP/boost/baro,
  TPS, injector duration/duty, ignition advance/dwell, coolant/intake temps,
  battery, VTEC, knock (level/threshold/retard/count), wideband lambda,
  rev/boost/launch/shift cuts, boost control duty, analog inputs.
- **Configurable layout** — pick which sensor sits in each tile (4 big + 3
  small), hide the RPM bar for bigger tiles, and a **second sensor-only page
  (START button)** with 8 more configurable tiles.
- **Digital, analog, or hybrid gauges** — needle gauges with warn/critical
  zones painted from your alarm thresholds, optionally with the digital
  value in the centre.
- **Themable** — every colour overridable from the phone (colour pickers);
  angular street-racing default look.
- **Alarms** — config-driven warn/critical engine alarms with deadband,
  debounce, latching and rpm/tps gates, evaluated in the daemon so they
  survive UI restarts. Optional full-screen **danger-to-manifold** warning
  at a set rpm.
- **Phone settings page** — thresholds, shift light, tiles, theme, Bluetooth
  and calibration; atomic saves, hot-applied within seconds (no restarts).
- **Car-safe** — read-only root filesystem option for ignition-cut power
  safety, screen never blanks, device never sleeps, Bluetooth link
  release/resume for handing the ECU to SManager or the Hondata phone app.

## PSPi 6 buttons

| Button | Action |
|---|---|
| **×** (cross) | acknowledge latched critical alarms |
| **SELECT** | release / resume the Bluetooth link |
| **START** | toggle the second sensor page |
| **HOME / PS** | switch cluster ↔ desktop (desktop images) |

## Installing on the PSPi 6

Full walkthrough (fresh eMMC or existing install): **[INSTALL.md](INSTALL.md)**.
Works on Raspberry Pi OS **Trixie Lite or standard/desktop**; on desktop
images the installer boots the Pi straight into the cluster (desktop stays
installed — the HOME button or one command brings it back).

The short version, from a laptop that can SSH to the Pi:

    deploy/push.sh <pi-host-or-ip>       # rsync + sudo deploy/install.sh
    sudo /opt/s300-cluster/deploy/pair.sh <S300 MAC>   # on the Pi, ignition on

then join the Pi's hotspot and open `http://10.42.0.1:8081` on your phone to
set the ECU MAC/RFCOMM channel and everything else. The installer handles
packages, services, the hotspot (DHCP + WiFi country included), boot-time
tuning, no-sleep/no-blanking, SSH, and desktop handoff.

When it all works, lock the root filesystem so yanking power can't corrupt it:

    sudo deploy/overlay.sh lock && sudo reboot

## Testing without the car

Everything runs on a laptop — see [LOCAL_TESTING.md](LOCAL_TESTING.md):

    pip install -r requirements.txt
    python -m tools.synth --out capture.jsonl            # synthetic engine data
    python -m s300d --replay capture.jsonl --loop --settings-host 127.0.0.1
    python -m s300ui --windowed                          # second terminal
    # settings page: http://127.0.0.1:8081
    # keys: Enter=ack  R=release/resume  Tab=page 2  Esc=quit

Record a real capture on the car (`python -m tools.record --out capture.jsonl
--seconds 60`) and replay it the same way.

## Tests

    pip install pytest
    pytest        # no hardware needed

The suite covers protocol framing, channel decoding/scaling, the client
state machine (fake sockets), alarms, the WebSocket server, the settings
API, and the UI's pure layout logic.

## Repo layout

    s300d/       daemon: protocol framing, channel decode, RFCOMM client state
                 machine, alarm engine, WebSocket hub, phone settings service
    s300ui/      pygame cluster: pure layout/format logic + renderer + WS client
    tools/       synth (bench data), record (capture), replay (off-car source)
    deploy/      install.sh, push.sh, systemd units, hotspot/overlay/pair
                 helpers, Home-button cluster<->desktop toggle
    tests/       pytest suite
    docs/        PROTOCOL.md — Hondata protocol ground truth
    config.yaml  the single config file (on the Pi: /boot/firmware/s300-cluster/)

## Calibration notes

`CT_RPM` and `CT_RETARD` scaling factors are provisional until verified
against the car (spec text and worked example disagree). Both are overridable
from the settings page — compare the live rpm readout with the tacho and
adjust. Boost is computed as MAP minus the barometric-pressure channel, never
a hardcoded constant.
