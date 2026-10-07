#!/usr/bin/env python3
"""Run the photobooth UI on this machine with a simulated camera and printer. See booth/sim.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from booth.sim import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
