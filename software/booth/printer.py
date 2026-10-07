"""Thermal printer output for the photobooth.

The printer (sold as a Pi Hut CSN-A4L, reports itself as a Gprinter GP-58) is a
USB printer-class device. The kernel's ``usblp`` driver exposes it as
``/dev/usb/lp0`` and it understands ESC/POS, so printing is just writing bytes
to that file. The handful of commands a photobooth needs is small enough that
this module speaks ESC/POS directly instead of pulling in python-escpos:

* ``ESC @``        initialise
* ``ESC a n``      alignment, ``ESC ! n`` text size
* ``GS v 0``       raster bit image, 384 dots (48 bytes) per row
* ``ESC d n``      feed n lines

Images are converted with Pillow: grayscale, auto-contrast, resized to the
384-dot print width, Floyd-Steinberg dithered, then packed 1 bit per dot with
1 = black. Raster data is sent in bands so the printer's buffer never sees one
giant command.

Verified on Raspberry Pi 5 / Raspberry Pi OS Trixie on 2026-09-05.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

DEVICE = "/dev/usb/lp0"
WIDTH_PX = 384          # 8 dots/mm x 48 mm printable width
BAND_ROWS = 128         # rows per GS v 0 command

ESC = b"\x1b"
GS = b"\x1d"
INIT = ESC + b"@"


@dataclass
class PrinterSettings:
    device: str = DEVICE
    width_px: int = WIDTH_PX
    dither: bool = True
    autocontrast: bool = True
    feed_lines: int = 4          # blank lines after a print so it can be torn off


def prepare_image(img: Image.Image, width: int = WIDTH_PX, dither: bool = True,
                  autocontrast: bool = True) -> Image.Image:
    """Return a 1-bit image ``width`` dots wide, ready for the print head."""
    img = ImageOps.exif_transpose(img).convert("L")
    if autocontrast:
        img = ImageOps.autocontrast(img, cutoff=1)
    if img.width != width:
        height = max(1, round(img.height * width / img.width))
        img = img.resize((width, height), Image.LANCZOS)
    return img.convert("1", dither=Image.FLOYDSTEINBERG if dither else Image.NONE)


def raster_commands(img: Image.Image, band_rows: int = BAND_ROWS) -> bytes:
    """ESC/POS ``GS v 0`` commands for a 1-bit image, one command per band."""
    if img.mode != "1":
        img = img.convert("1", dither=Image.NONE)
    row_bytes = (img.width + 7) // 8
    out = bytearray()
    for top in range(0, img.height, band_rows):
        band = img.crop((0, top, img.width, min(top + band_rows, img.height)))
        rows = band.height
        # In mode "1" a set bit means white; the printer wants 1 = black.
        data = bytes(b ^ 0xFF for b in band.tobytes())
        out += GS + b"v0" + bytes([0, row_bytes & 0xFF, row_bytes >> 8, rows & 0xFF, rows >> 8])
        out += data
    return bytes(out)


class BoothPrinter:
    """Writes ESC/POS to the usblp device. Each call opens and closes the device."""

    def __init__(self, settings: PrinterSettings | None = None) -> None:
        self.settings = settings or PrinterSettings()

    @property
    def device(self) -> str:
        return self.settings.device

    def is_present(self) -> bool:
        return os.path.exists(self.device)

    def is_writable(self) -> bool:
        return self.is_present() and os.access(self.device, os.W_OK)

    def raw(self, payload: bytes) -> int:
        """Write bytes to the printer; returns the byte count."""
        with open(self.device, "wb", buffering=0) as fh:
            view = memoryview(payload)
            sent = 0
            while sent < len(view):
                sent += fh.write(view[sent:])
        return sent

    def feed(self, lines: int | None = None) -> None:
        n = self.settings.feed_lines if lines is None else lines
        self.raw(ESC + b"d" + bytes([max(0, min(n, 255))]))

    def print_text(self, lines: list[str] | str, *, center: bool = True, big: bool = False,
                   feed: bool = True) -> None:
        if isinstance(lines, str):
            lines = lines.splitlines() or [lines]
        body = "\n".join(lines) + "\n"
        payload = INIT + ESC + b"a" + (b"\x01" if center else b"\x00")
        payload += ESC + b"!" + (b"\x30" if big else b"\x00")   # 0x30 = double width + height
        payload += body.encode("ascii", "replace")
        payload += ESC + b"!" + b"\x00"
        if feed:
            payload += ESC + b"d" + bytes([self.settings.feed_lines])
        self.raw(payload)

    def print_image(self, image: Image.Image | str | Path, *, feed: bool = True) -> Image.Image:
        """Print an image scaled to the paper width. Returns the 1-bit image sent."""
        if not isinstance(image, Image.Image):
            image = Image.open(image)
        s = self.settings
        prepared = prepare_image(image, s.width_px, s.dither, s.autocontrast)
        payload = INIT + ESC + b"a\x00" + raster_commands(prepared)
        if feed:
            payload += ESC + b"d" + bytes([s.feed_lines])
        self.raw(payload)
        return prepared
