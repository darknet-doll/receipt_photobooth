#!/usr/bin/env python3
"""Thermal printer diagnostic. Run on the Pi.

    python3 tools/printer_check.py              # device check + text + test pattern
    python3 tools/printer_check.py --image photo.jpg   # also print a photo at 384 px
    python3 tools/printer_check.py --strip a.jpg b.jpg c.jpg   # compose + print a receipt (default theme)
    python3 tools/printer_check.py --theme classic --strip a.jpg b.jpg c.jpg   # the plain titled strip
    python3 tools/printer_check.py --no-print   # just report and save the images

Everything printed is also saved as PNG under ~/photobooth/captures so the
on-paper result can be compared with what was sent.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from booth.printer import WIDTH_PX, BoothPrinter, PrinterSettings  # noqa: E402
from booth.receipt import PRESETS, make_receipt, preset  # noqa: E402
from booth.strip import StripSettings, load_font, make_strip  # noqa: E402

CAPTURE_DIR = Path.home() / "photobooth" / "captures"


def test_pattern(width: int = WIDTH_PX) -> Image.Image:
    """Gray ramp, line-width ladder, and checkerboard: shows dither + head health."""
    img = Image.new("L", (width, 260), 255)
    d = ImageDraw.Draw(img)
    font = load_font(18)
    d.text((6, 4), "gray ramp (dithered)", fill=0, font=font)
    for x in range(width):
        d.line([(x, 28), (x, 88)], fill=int(255 * x / (width - 1)))
    d.text((6, 96), "line ladder 1..8 px", fill=0, font=font)
    y = 122
    for w in range(1, 9):
        d.rectangle([(6, y), (width - 7, y + w - 1)], fill=0)
        y += w + 6
    d.text((6, 196), "checker + solid block", fill=0, font=font)
    for cy in range(220, 256, 4):
        for cx in range(6, width // 2, 4):
            if ((cx // 4) + (cy // 4)) % 2 == 0:
                d.rectangle([(cx, cy), (cx + 3, cy + 3)], fill=0)
    d.rectangle([(width // 2 + 6, 220), (width - 7, 256)], fill=0)
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=PrinterSettings.device)
    ap.add_argument("--image", type=Path, help="print this photo scaled to the paper width")
    ap.add_argument("--strip", nargs="+", type=Path, metavar="PHOTO",
                    help="compose these photos into a receipt/strip and print it")
    ap.add_argument("--theme", choices=(*PRESETS, "classic"), default="receipt",
                    help="layout for --strip: receipt (default), birthday, mayhem, or classic")
    ap.add_argument("--title", default=None, help="shop name (receipt themes) or strip title (classic)")
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("--no-print", action="store_true", help="only report and save PNGs")
    ap.add_argument("--skip-basic", action="store_true", help="skip the text + pattern prints")
    args = ap.parse_args()

    printer = BoothPrinter(PrinterSettings(device=args.device, dither=not args.no_dither))
    print(f"device   : {printer.device}")
    print(f"present  : {printer.is_present()}")
    print(f"writable : {printer.is_writable()}  (if False: sudo usermod -aG lp $USER, then log in again)")
    if not printer.is_writable() and not args.no_print:
        print("cannot print; use --no-print to only generate the images")
        return 1
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def send(label: str, fn) -> None:
        if args.no_print:
            print(f"{label:9}: skipped (--no-print)")
            return
        t0 = time.monotonic()
        fn()
        print(f"{label:9}: sent in {time.monotonic() - t0:.2f}s")

    if not args.skip_basic:
        send("text", lambda: printer.print_text(["PHOTOBOOTH", "printer check", stamp], big=False))
        pattern = test_pattern()
        pattern.save(CAPTURE_DIR / f"printcheck_pattern_{stamp}.png")
        send("pattern", lambda: printer.print_image(pattern))

    if args.image:
        prepared = printer.print_image(args.image) if not args.no_print else None
        if prepared is None:
            from booth.printer import prepare_image
            prepared = prepare_image(Image.open(args.image), dither=not args.no_dither)
        out = CAPTURE_DIR / f"printcheck_image_{stamp}.png"
        prepared.save(out)
        print(f"image    : {args.image.name} -> {prepared.size[0]}x{prepared.size[1]} dots, saved {out.name}")

    if args.strip:
        if args.theme == "classic":
            strip = make_strip(args.strip, StripSettings(dither=not args.no_dither,
                                                         title=args.title or StripSettings.title))
        else:
            strip = make_receipt(args.strip, preset(args.theme, shop=args.title, dither=not args.no_dither))
        out = CAPTURE_DIR / f"{args.theme}_{stamp}.png"
        strip.save(out)
        print(f"{args.theme:9}: {len(args.strip)} photos -> {strip.size[0]}x{strip.size[1]} dots "
              f"({strip.size[1] / 8:.0f} mm), saved {out.name}")
        send("strip", lambda: printer.print_image(strip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
