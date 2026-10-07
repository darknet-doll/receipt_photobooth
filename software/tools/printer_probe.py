#!/usr/bin/env python3
"""Print one labelled sample per bitmap command so the working one can be picked.

Each block is: printer reset, a text label, then a 384 x 48 test image sent with
one ESC/POS bitmap command. Read the paper: the label under which a bar with a
white diagonal and the word "OK" appears is the command this printer supports.

    python3 tools/printer_probe.py            # print all four
    python3 tools/printer_probe.py A C        # only these
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from booth.printer import ESC, GS, INIT, WIDTH_PX, BoothPrinter, raster_commands  # noqa: E402
from booth.strip import load_font  # noqa: E402

DC2 = b"\x12"


def sample(width: int = WIDTH_PX, height: int = 48) -> Image.Image:
    img = Image.new("1", (width, height), 0)               # black
    d = ImageDraw.Draw(img)
    d.line([(0, height - 1), (width - 1, 0)], fill=1, width=6)  # white diagonal
    d.rectangle([(8, 8), (140, height - 9)], fill=1)
    d.text((14, 10), "OK", fill=0, font=load_font(28))
    return img


def bits_rows(img: Image.Image) -> tuple[bytes, int]:
    """Row-major bytes, 1 = black, MSB = leftmost dot."""
    data = bytes(b ^ 0xFF for b in img.tobytes())
    return data, (img.width + 7) // 8


def cmd_gs_v0(img: Image.Image) -> bytes:                 # A: GS v 0 raster
    return raster_commands(img)


def cmd_dc2_star(img: Image.Image) -> bytes:              # B: DC2 * r n (Adafruit / CSN family)
    data, row_bytes = bits_rows(img)
    return DC2 + b"*" + bytes([img.height, row_bytes]) + data


def cmd_esc_star(img: Image.Image, mode: int) -> bytes:   # C/D: ESC * column format
    dots = 24 if mode >= 32 else 8
    px = img.load()
    out = bytearray(ESC + b"3" + bytes([dots]))           # line spacing = stripe height
    for top in range(0, img.height, dots):
        out += ESC + b"*" + bytes([mode, img.width & 0xFF, img.width >> 8])
        for x in range(img.width):
            col = 0
            for y in range(dots):
                yy = top + y
                black = yy < img.height and px[x, yy] == 0
                col = (col << 1) | (1 if black else 0)
            out += col.to_bytes(dots // 8, "big")
        out += b"\n"
    out += ESC + b"2"                                     # default line spacing
    return bytes(out)


PROBES = {
    "A": ("GS v 0 raster (48 bytes/row)", cmd_gs_v0),
    "B": ("DC2 * r n row bitmap", cmd_dc2_star),
    "C": ("ESC * 33 column 24-dot", lambda im: cmd_esc_star(im, 33)),
    "D": ("ESC * 1 column 8-dot", lambda im: cmd_esc_star(im, 1)),
}


def main() -> int:
    wanted = [a.upper() for a in sys.argv[1:]] or list(PROBES)
    printer = BoothPrinter()
    img = sample()
    for key in wanted:
        label, fn = PROBES[key]
        payload = INIT + ESC + b"a\x00" + f"[{key}] {label}\n".encode() + fn(img) + b"\n" + ESC + b"d\x02"
        printer.raw(payload)
        print(f"sent {key}: {label} ({len(payload)} bytes)")
    printer.raw(ESC + b"d\x04")
    return 0


if __name__ == "__main__":
    sys.exit(main())
