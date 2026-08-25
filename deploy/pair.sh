#!/bin/bash
# One-time Bluetooth pairing with the S300. Usage: pair.sh AA:BB:CC:DD:EE:FF
# Do this with the ignition ON and the phone app / SManager disconnected.
set -euo pipefail
MAC=${1:?usage: pair.sh MAC}
bluetoothctl power on >/dev/null
echo "scanning 12s for $MAC ..."
timeout 12 bluetoothctl scan on >/dev/null || true
bluetoothctl pair "$MAC"     # enter the PIN from the S300 / Hondata docs when prompted
bluetoothctl trust "$MAC"
echo "paired + trusted. Link key stored in /var/lib/bluetooth (never in this repo)."
echo "RFCOMM channel: run  sdptool browse $MAC  and look for the Serial Port channel."
