#!/usr/bin/env bash
# Run the photobooth simulator on a Mac/PC (not on the Pi). The first run creates a
# private virtualenv at software/.venv-sim with pygame, Pillow and numpy using uv.
#   software/tools/simulate.sh                       # synthetic camera, 3 photos, receipt theme
#   software/tools/simulate.sh --photos-dir ~/Pictures/booth-test --theme birthday
#   software/tools/simulate.sh --help                # all photobooth.py options plus --photos-dir, --print-speed
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$(dirname "$HERE")/.venv-sim"
if [ ! -x "$VENV/bin/python" ]; then
  UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
  if [ ! -x "$UV" ]; then
    echo "uv not found. Install it (https://docs.astral.sh/uv/) or run: python3 -m pip install pygame pillow numpy && python3 $HERE/simulate.py" >&2
    exit 1
  fi
  echo "creating $VENV with pygame, pillow, numpy (one-time)..."
  "$UV" venv "$VENV" -p 3.12 -q
  "$UV" pip install -p "$VENV/bin/python" -q pygame pillow numpy
fi
exec "$VENV/bin/python" "$HERE/simulate.py" "$@"
