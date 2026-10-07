#!/usr/bin/env python3
"""Launch the photobooth loop on the touchscreen. See booth/app.py for options."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from booth.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
