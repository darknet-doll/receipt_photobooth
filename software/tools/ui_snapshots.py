#!/usr/bin/env python3
"""Run one unattended booth cycle and save a screenshot of every screen state.

    python3 tools/ui_snapshots.py                 # ~/photobooth/snapshots/<state>_<n>.png
    python3 tools/ui_snapshots.py --theme birthday --out /tmp/snaps

Takes the booth's normal options (see photobooth.py --help). Nothing is printed:
the session auto-starts after 2 s, the confirm screen auto-continues after 5 s,
the receipt is saved as usual, and the app exits after one cycle. Run it from
SSH with WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 so it draws on
the touchscreen (booth.sh does this for you: booth.sh snapshots).
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame  # noqa: E402

from booth.app import PhotoboothApp, build_parser, config_from_args  # noqa: E402


def main() -> int:
    ap = build_parser()
    ap.add_argument("--out", default=str(Path.home() / "photobooth" / "snapshots"), help="where the PNGs go")
    ap.add_argument("--per-state", type=int, default=6, help="max screenshots per state, one per second")
    args = ap.parse_args()
    cfg = replace(config_from_args(args), print_enabled=False, auto_start_after=2.0, exit_after_one=True,
                  confirm_before_print=True, confirm_seconds=5.0, done_seconds=3.0)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    app = PhotoboothApp(cfg)
    orig_flip = app._flip
    counts: dict[str, int] = {}
    last: dict[str, float] = {}

    def flip() -> None:
        orig_flip()
        state, now = app.state, time.monotonic()
        if now - last.get(state, 0.0) >= 1.0 and counts.get(state, 0) < args.per_state:
            n = counts.get(state, 0)
            pygame.image.save(app.screen, str(out / f"{state.lower()}_{n}.png"))
            counts[state], last[state] = n + 1, now

    app._flip = flip
    rc = app.run()
    print(f"{sum(counts.values())} screenshots in {out} ({app.width}x{app.height})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
