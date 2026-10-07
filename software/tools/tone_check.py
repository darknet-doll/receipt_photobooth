#!/usr/bin/env python3
"""Check the print tone lift against the captures on disk. Needs no printer.

    python3 tools/tone_check.py                 # table: every capture, gamma chosen, face white before/after
    python3 tools/tone_check.py --sheet out.png # side-by-side dithered tiles, current vs lifted
    python3 tools/tone_check.py --print 20260909_150050   # print that session's receipt for a bench comparison

Thermal paper spreads every dot, so faces that are mid-grey on screen print as
shadow. ``booth.strip.tone_lift`` lifts the centre of the frame to
``TONE_TARGET`` with a gamma clamped at ``TONE_MIN_GAMMA``; this tool shows what
that does to real captures so the two constants can be tuned from prints
instead of guesses. "face white" is the fraction of white dots in the middle
of the dithered tile; about 0.5 to 0.65 prints as a natural skin tone.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from booth.strip import TONE_MIN_GAMMA, TONE_TARGET, tone_lift  # noqa: E402

CAPTURES = Path.home() / "photobooth" / "captures"
TILE = (360, 270)


def centre_white(one_bit: Image.Image) -> float:
    a = np.asarray(one_bit.convert("L"), dtype=np.float32) / 255.0
    h, w = a.shape
    return float(a[h // 4: 3 * h // 4, w // 4: 3 * w // 4].mean())


def variants(path: Path) -> tuple[Image.Image, Image.Image, float]:
    g = ImageOps.autocontrast(ImageOps.exif_transpose(Image.open(path)).convert("L"), cutoff=1)
    lifted, gamma = tone_lift(g)
    fit = lambda im: ImageOps.fit(im, TILE, Image.LANCZOS).convert("1", dither=Image.FLOYDSTEINBERG)
    return fit(g), fit(lifted), gamma


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet", metavar="PNG", help="save a comparison sheet")
    ap.add_argument("--print", dest="session", metavar="SESSION", help="print that capture folder's receipt")
    ap.add_argument("--limit", type=int, default=12, help="newest sessions to include (default 12)")
    args = ap.parse_args()

    if args.session:
        from booth.printer import BoothPrinter
        from booth.receipt import load_settings, make_receipt, preset
        photos = sorted((CAPTURES / args.session).glob("photo_*.jpg"))
        if not photos:
            print(f"no photos in {CAPTURES / args.session}")
            return 1
        image = make_receipt(photos, load_settings() or preset("receipt"), when=datetime.now())
        printer = BoothPrinter()
        if not printer.is_writable():
            print(f"printer not writable at {printer.device}")
            return 1
        printer.print_image(image)
        print(f"printed {len(photos)} photo(s) from {args.session} (target {TONE_TARGET}, min gamma {TONE_MIN_GAMMA})")
        return 0

    sessions = sorted((d for d in CAPTURES.iterdir() if d.is_dir()), reverse=True)[: args.limit]
    rows = []
    print(f"target {TONE_TARGET}  min gamma {TONE_MIN_GAMMA}")
    print(f"{'session':16} {'photo':8} {'gamma':>6} {'before':>7} {'after':>6}")
    for d in sessions:
        for p in sorted(d.glob("photo_*.jpg")):
            before, after, gamma = variants(p)
            b, a = centre_white(before), centre_white(after)
            print(f"{d.name:16} {p.stem:8} {gamma:6.2f} {b:7.2f} {a:6.2f}")
            rows.append((f"{d.name} {p.stem}", before, after, gamma, b, a))
    if args.sheet and rows:
        sheet = Image.new("L", (TILE[0] * 2 + 10, (TILE[1] + 18) * len(rows)), 255)
        draw = ImageDraw.Draw(sheet)
        for i, (label, before, after, gamma, b, a) in enumerate(rows):
            y = i * (TILE[1] + 18)
            draw.text((4, y + 3), f"{label}  current {b:.2f}", fill=0)
            draw.text((TILE[0] + 14, y + 3), f"lifted gamma {gamma:.2f}  {a:.2f}", fill=0)
            sheet.paste(before.convert("L"), (0, y + 18))
            sheet.paste(after.convert("L"), (TILE[0] + 10, y + 18))
        sheet.save(args.sheet)
        print(f"sheet: {args.sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
