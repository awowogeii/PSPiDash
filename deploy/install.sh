#!/bin/bash
# s300-cluster installer for Raspberry Pi OS Trixie (Lite or standard/desktop)
# on a CM4 / PSPi 6. On the desktop image the graphical login is disabled so
# the cluster owns the display (re-enable: see INSTALL.md).
#
#   sudo ./deploy/install.sh              full install (packages, user, services, hotspot, boot tuning)
#   sudo ./deploy/install.sh --update     just re-copy the code + restart services
#   flags: --no-hotspot  --no-boot-tuning  --no-overlay-hint
#
# Idempotent: safe to run again. Never touches the PSPi 6 display/power overlays
# (install those first from the PSPi 6 project) and never touches Bluetooth
# pairing (see deploy/pair.sh).
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
DEST=/opt/s300-cluster
DATA=/boot/firmware/s300-cluster            # writable even when the root fs is locked
USER_NAME=cluster
UPDATE=0; HOTSPOT=1; BOOT_TUNING=1
for a in "$@"; do case $a in
  --update) UPDATE=1;; --no-hotspot) HOTSPOT=0;; --no-boot-tuning) BOOT_TUNING=0;;
  *) echo "unknown flag $a"; exit 1;; esac; done

[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
if grep -q overlayroot /proc/cmdline; then
  echo "!! root filesystem is locked (overlay). Run: sudo deploy/overlay.sh unlock && sudo reboot"; exit 1
fi

step() { echo; echo "==> $*"; }

if [ $UPDATE = 0 ]; then
  step "packages"
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
    python3 python3-aiohttp python3-yaml python3-pygame fonts-dejavu-core \
    bluez network-manager dnsmasq-base rsync
  # aiohttp on Trixie is new enough (>=3.9) for the daemon.
  # dnsmasq-base is NM's DHCP server for the hotspot: without it phones
  # associate but never get an IP (--no-install-recommends skips it).

  step "user '$USER_NAME'"
  id "$USER_NAME" >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin "$USER_NAME"
  usermod -aG video,render,input,tty,bluetooth "$USER_NAME"
fi

step "code -> $DEST"
mkdir -p "$DEST"
rsync -a --delete --exclude tests --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '*.zip' "$SRC"/ "$DEST"/
chown -R root:root "$DEST"; chmod -R a+rX "$DEST"

step "config -> $DATA"
mkdir -p "$DATA"
if [ ! -f "$DATA/config.yaml" ]; then
  cp "$SRC/config.yaml" "$DATA/config.yaml"
  echo "   installed default config.yaml - edit MAC/channel from the phone page or with nano"
else
  echo "   keeping existing $DATA/config.yaml"
fi
[ -f "$DATA/s300d.env" ] || cat > "$DATA/s300d.env" <<'EOF'
# Extra args for the daemon. Bench mode example (loops a capture, no ECU needed):
# S300D_ARGS=--replay /boot/firmware/s300-cluster/capture.jsonl --loop
S300D_ARGS=
EOF
mkdir -p /etc/s300-cluster; ln -sfn "$DATA/config.yaml" /etc/s300-cluster/config.yaml

step "systemd services"
install -m 644 "$SRC/deploy/s300d.service" "$SRC/deploy/s300ui.service" \
  "$SRC/deploy/cluster-toggle.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable s300d.service s300ui.service >/dev/null
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
systemctl restart s300d.service s300ui.service

if [ $UPDATE = 0 ]; then
  step "display + power behaviour"
  # standard (desktop) image: boot to the cluster, not the desktop session.
  # The desktop stays installed; re-enable per INSTALL.md.
  systemctl set-default multi-user.target >/dev/null 2>&1 || true
  if systemctl is-enabled lightdm.service >/dev/null 2>&1; then
    systemctl disable lightdm.service >/dev/null 2>&1 || true
    echo "   desktop login disabled - the Pi now boots straight to the cluster"
  fi
  if systemctl cat lightdm.service >/dev/null 2>&1; then
    # Home/PS button toggles cluster <-> desktop (deploy/cluster_toggle.py)
    systemctl enable --now cluster-toggle.service >/dev/null 2>&1 || true
    echo "   Home button toggles cluster <-> desktop"
    if [ -n "${SUDO_USER:-}" ]; then
      # land on the desktop, not a greeter you can't type a password into
      mkdir -p /etc/lightdm/lightdm.conf.d
      printf '[Seat:*]\nautologin-user=%s\n' "$SUDO_USER" \
        > /etc/lightdm/lightdm.conf.d/90-s300-autologin.conf
      echo "   lightdm auto-login: $SUDO_USER"
    fi
  fi
  raspi-config nonint do_squeekboard S3 >/dev/null 2>&1 || true   # no on-screen keyboard
  raspi-config nonint do_blanking 1 >/dev/null 2>&1 || true       # never blank the screen
  # never suspend: powered on means screen + services on
  systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target \
    >/dev/null 2>&1 || true
  # keep SSH reachable on the hotspot IP
  raspi-config nonint do_ssh 0 >/dev/null 2>&1 || systemctl enable --now ssh >/dev/null 2>&1 || true
fi

if [ $UPDATE = 0 ] && [ $HOTSPOT = 1 ]; then
  step "wifi hotspot for the phone settings page"
  # a fresh image ships WiFi rfkill-blocked until a country is set
  if [ -z "$(raspi-config nonint get_wifi_country 2>/dev/null)" ]; then
    raspi-config nonint do_wifi_country AU >/dev/null 2>&1 || true
    echo "   WiFi country set to AU (change: sudo raspi-config nonint do_wifi_country <CC>)"
  fi
  rfkill unblock wifi 2>/dev/null || true
  CFG="$DATA/config.yaml" "$SRC/deploy/hotspot.sh" on || echo "   (hotspot failed - is wlan0 present? re-run deploy/hotspot.sh on later)"
fi

if [ $UPDATE = 0 ] && [ $BOOT_TUNING = 1 ]; then
  step "boot-time tuning"
  CFGTXT=/boot/firmware/config.txt; CMD=/boot/firmware/cmdline.txt
  cp -n "$CFGTXT" "$CFGTXT.s300.bak"; cp -n "$CMD" "$CMD.s300.bak"
  grep -q '^# s300-cluster' "$CFGTXT" || cat >> "$CFGTXT" <<'EOF'

# s300-cluster: faster boot. Display/DPI settings for the PSPi 6 are NOT set here.
boot_delay=0
disable_splash=1
EOF
  for opt in quiet loglevel=3 logo.nologo vt.global_cursor_default=0 consoleblank=0 plymouth.enable=0; do
    grep -qw "$opt" "$CMD" || sed -i "1 s/\$/ $opt/" "$CMD"
  done
  # services that add nothing on a car dash
  for s in apt-daily.timer apt-daily-upgrade.timer man-db.timer e2scrub_all.timer fstrim.timer \
           rpi-eeprom-update.service ModemManager.service triggerhappy.service \
           systemd-timesyncd.service; do
    systemctl disable --now "$s" >/dev/null 2>&1 || true
  done
  # don't let the boot wait for a network that may never come
  systemctl disable NetworkManager-wait-online.service >/dev/null 2>&1 || true
  echo "   done. Measure with: systemd-analyze && systemd-analyze blame | head"
fi

step "done"
cat <<EOF
   daemon:   journalctl -fu s300d       ui:  journalctl -fu s300ui
   config:   $DATA/config.yaml  (phone: http://$(python3 -c "import yaml;print((yaml.safe_load(open('$DATA/config.yaml')).get('hotspot') or {}).get('ip','10.42.0.1'))"):8081)
   next:     1) sudo deploy/pair.sh <S300 MAC>   2) set mac/channel in config   3) sudo deploy/overlay.sh lock && sudo reboot
EOF
