#!/bin/bash
# lock   -> root filesystem read-only with a tmpfs overlay (survives power cuts)
# unlock -> normal writable root (for apt / editing files). Reboot required either way.
# /boot/firmware stays writable in both modes, so config.yaml is always editable.
set -euo pipefail
case "${1:-}" in
  lock)   raspi-config nonint enable_overlayfs;  echo "overlay ENABLED - reboot to apply";;
  unlock) raspi-config nonint disable_overlayfs; echo "overlay DISABLED - reboot to apply";;
  status) grep -q overlayroot /proc/cmdline && echo "root is READ-ONLY (overlay)" || echo "root is WRITABLE";;
  *) echo "usage: $0 lock|unlock|status"; exit 1;;
esac
