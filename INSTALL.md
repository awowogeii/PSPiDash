# Installing the cluster on the PSPi 6 (CM4, Raspberry Pi OS Trixie)

Works on Trixie **Lite** (recommended) and **standard/desktop**. On the
desktop image the installer switches the boot target to the cluster and
disables the graphical login — the desktop stays installed. To get it back:

    sudo systemctl set-default graphical.target && sudo systemctl enable lightdm

Two routes to the same result. Route A if the Pi already boots and you can SSH
to it; Route B from a blank eMMC. Both end with the same `install.sh`.

What you end up with:

| piece | where | notes |
|---|---|---|
| code | `/opt/s300-cluster` | read-only, replaced by `install.sh --update` |
| config | `/boot/firmware/s300-cluster/config.yaml` | on the FAT boot partition so it stays writable when the root fs is locked, and editable from a laptop with the eMMC/SD mounted |
| daemon | `s300d.service` | Bluetooth → decode → alarms → `ws://127.0.0.1:8080/ws` |
| cluster | `s300ui.service` | pygame straight to DRM/KMS on tty1, no X/Wayland |
| settings | `http://10.42.0.1:8081` | on the Pi's own WiFi hotspot `delsol-cluster` |

## Before you start

1. Install the PSPi 6 image/overlays first, exactly per the PSPi 6 project's
   instructions (display DPI timings, power/battery service, buttons). This
   installer deliberately doesn't touch `config.txt` display settings.
2. Change the hotspot password. Either edit `hotspot.password` in
   `config.yaml` before pushing, or later on the phone page is *not* enough —
   re-run `sudo deploy/hotspot.sh on` after changing it.
3. Have the S300's Bluetooth MAC (it shows in the Hondata Mobile app, or
   `bluetoothctl scan on` with the ignition on).

## Route A — Pi is up, SSH works

On your laptop, in the repo:

    deploy/push.sh <pi-hostname-or-ip>          # PI_USER=youruser if not 'pi'

That rsyncs the repo to `/tmp/s300-cluster` on the Pi and runs
`sudo deploy/install.sh`, which:

- installs `python3-aiohttp python3-yaml python3-pygame fonts-dejavu-core
  network-manager dnsmasq-base` (dnsmasq-base is the hotspot's DHCP server —
  without it phones connect but never get an IP)
- creates the `cluster` user (groups video/render/input/tty/bluetooth)
- copies the code, installs and starts both services, removes the tty1 login
- on the desktop image: boots to the cluster instead of the desktop; disables
  the on-screen keyboard (Squeekboard); disables screen blanking; masks
  suspend/hibernate so the device stays on as long as it has power; makes
  sure SSH is enabled
- sets the WiFi country to AU if none is set (fresh images ship rfkill-blocked,
  which silently kills the hotspot), then brings up the WiFi hotspot
- applies boot-time tuning (`boot_delay=0`, `disable_splash=1`, quiet kernel
  cmdline, disables apt/man-db timers, eeprom updater, ModemManager, timesyncd,
  and `NetworkManager-wait-online`)

**Heads-up:** once the hotspot is on, the Pi no longer joins your home WiFi.
To SSH afterwards, join the `delsol-cluster` network and `ssh <user>@10.42.0.1`.
`sudo deploy/hotspot.sh off` restores normal WiFi. Use `--no-hotspot` on the
first run if you'd rather keep SSH on your LAN until everything works.

Then on the Pi:

    sudo /opt/s300-cluster/deploy/pair.sh AA:BB:CC:DD:EE:FF     # ignition ON, phone app closed
    sdptool browse AA:BB:CC:DD:EE:FF | grep -A3 "Serial Port"   # note the RFCOMM channel

Open `http://10.42.0.1:8081` on your phone, set the MAC and RFCOMM channel,
Save. The Live panel should go green and show rpm within a few seconds.

When you're happy:

    sudo deploy/overlay.sh lock && sudo reboot

Root is now read-only with a RAM overlay: yanking power can't corrupt it.
`config.yaml` is still writable (it lives on the boot partition). To do
`apt` or change code again: `sudo deploy/overlay.sh unlock && sudo reboot`,
then `deploy/push.sh 10.42.0.1 --update`, then lock again.

## Route B — blank eMMC

1. Put the CM4 in USB-boot mode per the PSPi 6 docs (rpiboot) so the eMMC
   appears as a drive.
2. Raspberry Pi Imager → *Raspberry Pi OS Lite (64-bit)* (Trixie). In the
   customisation screen: set hostname `cluster-pi`, a username, **enable SSH**,
   and enter your home WiFi. Write it.
3. Boot the PSPi 6, install the PSPi 6 display/power bits per their docs, reboot.
4. Continue with Route A.

Optional bench mode without a car: put a capture next to the config and edit
`/boot/firmware/s300-cluster/s300d.env`:

    S300D_ARGS=--replay /boot/firmware/s300-cluster/capture.jsonl --loop

then `sudo systemctl restart s300d`. Record a capture on the car with
`python3 -m tools.record --out capture.jsonl --seconds 120` from `/opt/s300-cluster`.

## Adjusting values, alarms and calibration

Everything the alarm engine and shift light use is in `config.yaml` and
editable from the phone page:

- **Alarms** — enable/disable each rule, warn/critical/clear thresholds,
  samples-to-trip, and the rpm/tps gates. The turbo-era `overboost` and
  `boost_cut` rules ship disabled; tick them on when the turbo goes in.
- **Shift light** — amber/red/flash rpm.
- **Calibration (scaling overrides)** — per-type scale factors. The two that
  need checking on the car are `CT_RPM` (compare the Live rpm on the phone to
  the tacho) and `CT_RETARD`. Blank = built-in factor. Saving a calibration
  change reconnects to the ECU so the new table is built from a fresh
  handshake.
- **Bluetooth** — MAC, RFCOMM channel, poll rate, and Release/Resume buttons
  to hand the ECU to SManager or the Hondata app without stopping the dash.

Saves are atomic (temp file + rename) and hot-reloaded; nothing restarts.

## Buttons on the PSPi 6

The PSPi gamepad driver presents a "PS3 Controller"; the cluster uses:

- **× cross** (button 0): acknowledge latched critical alarms
- **SELECT** (button 8): release / resume the Bluetooth link
- **START** (button 9): toggle the second sensor-only page (2×4 tiles,
  no rpm bar / VTEC / shift light; pick its sensors on the phone page)
- **HOME / PS** (button 10): switch between the cluster and the desktop.
  Handled by `cluster-toggle.service` (desktop images only) — it works from
  either side, and the installer sets lightdm auto-login so you never land
  on a password prompt.

On a laptop: Enter/Space = ack, R = release/resume, Tab/P = page toggle.

If a different driver build numbers the buttons differently, watch
`journalctl -fu s300ui` while pressing and override under `ui.buttons`:

    ui:
      buttons: {ack: 0, release: 8, resume: 8, page: 9}

## Boot time

Measured targets on a CM4 with these settings: kernel+firmware ≈ 3 s,
userspace to `s300ui` first frame ≈ 4–5 s. Check with:

    systemd-analyze
    systemd-analyze blame | head -15
    systemd-analyze critical-chain s300ui.service

If Bluetooth is the long pole, that's `bluetooth.service`; the daemon starts
after it but the UI doesn't wait — it shows "NO DAEMON"/"CONNECTING" until
data arrives.

## Troubleshooting

    journalctl -fu s300d        # handshake, state changes, poll rate, alarms
    journalctl -fu s300ui       # SDL driver in use, joystick count
    ls -l /dev/dri/             # card0 must exist for kmsdrm
    bluetoothctl info <MAC>     # Paired: yes, Trusted: yes

- Hotspot visible, phone connects, but SSH/settings page unreachable: the
  phone almost certainly has no IP (check its WiFi details). Install
  `dnsmasq-base` and re-run `sudo deploy/hotspot.sh on` — the script now
  checks for this and for rfkill and says exactly what is wrong.
- No hotspot at boot: it was never created (install-time attempt failed,
  usually rfkill). `sudo deploy/hotspot.sh on` creates the NetworkManager
  profile with autoconnect, so once it succeeds it persists across reboots.
- Settings page dead right after boot but hotspot fine: the daemon now
  retries the bind every 5 s until the hotspot IP exists, so give it a few
  seconds; `journalctl -u s300d | grep bind` shows the retries.
- "BT ERROR" forever: something else holds the RFCOMM channel (phone app /
  SManager). Close it; the daemon retries with backoff automatically.
- "IGN OFF": normal with the key off; the daemon polls every 10 s.
- Blank screen but daemon fine: `SDL_VIDEODRIVER=kmsdrm` needs the `cluster`
  user in `video`/`render` and no other process owning the DRM device.
