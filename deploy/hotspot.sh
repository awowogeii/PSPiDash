#!/bin/bash
# Bring the Pi's WiFi up as an access point (phone settings page) or back to
# normal client mode. Usage: hotspot.sh on|off  [reads ssid/password/ip from config.yaml]
set -euo pipefail
CFG=${CFG:-/boot/firmware/s300-cluster/config.yaml}
NAME=delsol-hotspot
get() { python3 -c "import yaml,sys;print((yaml.safe_load(open('$CFG')).get('hotspot') or {}).get('$1',''))"; }
case "${1:-}" in
  on)
    rfkill unblock wifi 2>/dev/null || true
    if rfkill list wifi 2>/dev/null | grep -q "blocked: yes"; then
      echo "!! WiFi is rfkill-blocked. Set a country first:"
      echo "   sudo raspi-config nonint do_wifi_country AU"; exit 1
    fi
    if [ ! -x /usr/sbin/dnsmasq ] && ! command -v dnsmasq >/dev/null 2>&1; then
      echo "!! dnsmasq-base is missing - phones would connect but never get an IP."
      echo "   sudo apt-get install -y dnsmasq-base"; exit 1
    fi
    SSID=$(get ssid); PSK=$(get password); IP=$(get ip)
    nmcli -t -f NAME con show | grep -qx "$NAME" && nmcli con delete "$NAME" >/dev/null
    nmcli con add type wifi ifname wlan0 con-name "$NAME" autoconnect yes ssid "$SSID" \
      802-11-wireless.mode ap 802-11-wireless.band bg \
      ipv4.method shared ipv4.addresses "$IP/24" \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" >/dev/null
    nmcli con modify "$NAME" connection.autoconnect-priority 100
    nmcli con up "$NAME" >/dev/null
    echo "hotspot '$SSID' up on $IP  (settings: http://$IP:8081)"
    ;;
  off)
    nmcli con down "$NAME" 2>/dev/null || true
    nmcli con modify "$NAME" autoconnect no 2>/dev/null || true
    echo "hotspot down; WiFi will rejoin its normal network"
    ;;
  *) echo "usage: $0 on|off"; exit 1;;
esac
