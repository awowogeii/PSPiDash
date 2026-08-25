#!/bin/bash
# From your laptop: copy this repo to the Pi and (re)install.
#   deploy/push.sh cluster-pi.local            # first time -> full install
#   deploy/push.sh 10.42.0.1 --update          # later -> code refresh + restart
set -euo pipefail
HOST=${1:?usage: push.sh <pi-host-or-ip> [install flags...]}; shift || true
USER_AT=${PI_USER:-pi}@$HOST
SRC=$(cd "$(dirname "$0")/.." && pwd)
rsync -az --delete --exclude '.git' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.zip' \
  "$SRC"/ "$USER_AT":/tmp/s300-cluster/
ssh -t "$USER_AT" "sudo /tmp/s300-cluster/deploy/install.sh $*"
