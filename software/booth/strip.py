"""Photostrip layout: several photos stacked into one tall black-and-white image.

The strip is composed at the printer's native width (384 dots) so nothing is
resampled again at print time. Photos are converted to grayscale, auto-contrast
stretched, tone-lifted for thermal paper (see ``tone_lift``), centre-cropped to
4:3 and Floyd-Steinberg dithered; the caption text
is drawn solid black on top. The result is a mode "1" image that prints 1:1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .printer import WIDTH_PX

# Print tone lift (added 2026-09-09). Thermal paper spreads every dot, so a
# mid-grey face that is fine on screen prints as shadow. Skin in the centre of
# the frame is lifted to TONE_TARGET (fraction of white) with a gamma curve,
# never darkened, and never stronger than TONE_MIN_GAMMA so a face against a
# bright window does not turn into a white blob. Measured on real captures:
# a backlit shot went from 0.32 to 0.54 white on the faces, a well-lit one was
# left untouched (median already above the target). Tune with
# tools/tone_check.py against the captures on disk; it needs no printer.
TONE_TARGET = 0.62
TONE_MIN_GAMMA = 0.45


def tone_lift(img: Image.Image, target: float = TONE_TARGET, min_gamma: float = TONE_MIN_GAMMA) -> tuple[Image.Image, float]:
    """Lift a grayscale image so the centre region's median lands at ``target``.

    Returns the adjusted image and the gamma used (1.0 means unchanged).
    """
    a = np.asarray(img, dtype=np.float32) / 255.0
    h, w = a.shape
    centre = a[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    median = float(np.median(centre))
    if median <= 0.0 or median >= target:
        return img, 1.0
    gamma = max(min_gamma, float(np.log(target) / np.log(median)))
    lut = [int(round(255.0 * (i / 255.0) ** gamma)) for i in range(256)]
    return img.point(lut), gamma

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS, for the simulator
    "/System/Library/Fonts/Helvetica.ttc",
]


@dataclass
class StripSettings:
    width: int = WIDTH_PX
    margin: int = 8                     # white border, left/right/top/bottom
    gap: int = 10                       # space between photos
    photo_aspect: tuple[int, int] = (4, 3)
    title: str = "PHOTOBOOTH"
    title_size: int = 34
    footer: str = "{date:%d %b %Y  %H:%M}"   # str.format with date=datetime
    footer_size: int = 20
    dither: bool = True
    font_paths: list[str] = field(default_factory=lambda: list(FONT_CANDIDATES))


def load_font(size: int, candidates: list[str] | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates or FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def prepare_photo(img: Image.Image, size: tuple[int, int], dither: bool = True,
                  tone: bool = True) -> Image.Image:
    """Grayscale, auto-contrast, tone lift, centre-crop to ``size``'s aspect, resize, dither."""
    img = ImageOps.exif_transpose(img).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    if tone:
        img, _ = tone_lift(img)
    img = ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.5))
    return img.convert("1", dither=Image.FLOYDSTEINBERG if dither else Image.NONE)


def make_strip(photos: list[Image.Image | str | Path], settings: StripSettings | None = None,
               when: datetime | None = None) -> Image.Image:
    """Compose the strip and return it as a mode "1" image ``settings.width`` wide."""
    s = settings or StripSettings()
    when = when or datetime.now()
    inner = s.width - 2 * s.margin
    photo_h = round(inner * s.photo_aspect[1] / s.photo_aspect[0])
    title_font = load_font(s.title_size, s.font_paths)
    footer_font = load_font(s.footer_size, s.font_paths)
    footer_text = s.footer.format(date=when)

    def text_height(font, text):
        left, top, right, bottom = font.getbbox(text)
        return bottom - top, top

    title_h, title_top = text_height(title_font, s.title) if s.title else (0, 0)
    footer_h, footer_top = text_height(footer_font, footer_text) if footer_text else (0, 0)

    height = s.margin
    height += (title_h + s.gap) if s.title else 0
    height += len(photos) * photo_h + (len(photos) - 1) * s.gap
    height += (s.gap + footer_h) if footer_text else 0
    height += s.margin

    canvas = Image.new("L", (s.width, height), 255)
    draw = ImageDraw.Draw(canvas)
    y = s.margin
    if s.title:
        w = draw.textlength(s.title, font=title_font)
        draw.text(((s.width - w) / 2, y - title_top), s.title, fill=0, font=title_font)
        y += title_h + s.gap
    for i, photo in enumerate(photos):
        if not isinstance(photo, Image.Image):
            photo = Image.open(photo)
        tile = prepare_photo(photo, (inner, photo_h), s.dither).convert("L")
        canvas.paste(tile, (s.margin, y))
        y += photo_h + (s.gap if i < len(photos) - 1 else 0)
    if footer_text:
        y += s.gap
        w = draw.textlength(footer_text, font=footer_font)
        draw.text(((s.width - w) / 2, y - footer_top), footer_text, fill=0, font=footer_font)
    return canvas.convert("1", dither=Image.NONE)


def strip_to_screen(strip: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    """An RGB copy of the strip scaled to fit ``max_size`` for on-screen preview."""
    preview = strip.convert("RGB")
    preview.thumbnail(max_size, Image.LANCZOS)
    return preview
