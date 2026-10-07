#!/usr/bin/env bash
# Copy the software folder to the Raspberry Pi. Run from a Mac/PC on the same network.
#   software/tools/deploy.sh              # sync to pi@raspberrypi.local:~/photobooth/software
#   PI=booth@192.168.1.50 software/tools/deploy.sh
set -euo pipefail
PI="${PI:-pi@raspberrypi.local}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
ssh "$PI" "mkdir -p ~/photobooth/software"
rsync -az --delete --exclude '__pycache__' --exclude '.DS_Store' --exclude '.venv*' --exclude 'ui-review' --exclude 'archive' "$SRC/" "$PI:~/photobooth/software/"
ssh "$PI" "~/photobooth/software/tools/install_desktop.sh"
echo "synced to $PI:~/photobooth/software"
