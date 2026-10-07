"""Film-strip photostrip: photos as frames in a length of 35 mm film.

The look is the reference strips: rounded-corner frames down a black film base,
sprocket holes punched along a rail, edge markings, and the names and date set
vertically up the opposite rail the way a film stock prints its own edge code.

Unlike ``strip`` and ``receipt``, most of this layout is INK. The print head has
no density control (see ``printer`` — no ``ESC 7``), so a solid black base is
the one thing that can make this design fail on paper: a sustained run of
high-coverage rows heats the head, and a hot head prints the rest of the tape
grey and streaky. ``ink_coverage`` measures that before anything is printed, and
``base`` trades film for paper: "full" is the reference look, "rails" keeps the
inked rails that carry the sprockets and the type but stands the frames on paper,
and "outline" draws the film in line on white.

Composed at the printer's native 384 dots so it prints 1:1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .printer import WIDTH_PX
from .receipt import font_for          # the per-glyph fallback: one place knows the .notdef trick
from .strip import prepare_photo

# The rail type sets what runs down each side of the film.
RAIL_STYLES = ("sprockets", "marks", "text", "plain")
BASE_STYLES = ("full", "rails", "outline")

SERIF_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",   # macOS previews
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
]
SANS_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


@dataclass
class FilmSettings:
    width: int = WIDTH_PX
    rail_left: int = 40                  # dots
    rail_right: int = 58                 # wider: it carries the vertical type
    left_style: str = "sprockets"
    right_style: str = "text"
    frame_inset: int = 8                 # black between a frame and the window edge
    frame_gap: int = 14                  # black between frames
    frame_radius: int = 16               # rounded corners on each frame
    frame_aspect: tuple[int, int] = (1, 1)   # (w, h): square frames, like the reference
    frame_keyline: int = 0               # ring around each frame, in the hole colour; 0 for none
    end_pad: int = 22                    # black above the first frame and below the last

    # base: how much of the film is ink
    #   full    the whole tape is film, frames sit on black          — the reference look
    #   rails   only the rails are film, frames sit on paper         — the same silhouette, far less ink
    #   outline white paper, the film drawn in line                  — least ink of all
    base: str = "full"
    rail_fill: str = "solid"             # solid | half | quarter — see module docstring

    # sprockets
    hole_w: int = 22
    hole_h: int = 18
    hole_radius: int = 5
    hole_pitch: int = 34
    hole_stroke: int = 0                 # 0 fills the hole; >0 draws it as a ring that thick

    # edge markings, as fractions of the strip height: (position, width in dots, bar count)
    marks: list[tuple[float, int, int]] = field(default_factory=lambda: [
        (0.06, 20, 4), (0.21, 14, 2), (0.38, 20, 3), (0.53, 14, 2),
        (0.69, 20, 4), (0.86, 14, 3),
    ])

    # vertical type up the text rail
    names: str = "ALEX & SAM"
    date_format: str = "{date:%m.%d.%Y}"
    name_size: int = 30
    date_size: int = 26
    name_tracking: int = 5               # extra dots between letters, before rotation
    text_gap: int = 26                   # between the runs: names, symbol, date
    serif: bool = True
    text_rail_marks: bool = True         # edge bars above and below the type, as on real stock
    # What sits between the two runs of type. "heart" is drawn as geometry (see _heart) and so
    # prints the same everywhere; any other string is SET, in the rail face where it has the
    # character and a fallback face where it does not. None for nothing.
    ornament: str | None = None
    ornament_size: int = 26              # dots across
    text_rail_sprockets: bool = False    # perforate the text rail too, opening up around the type
    text_clear: int = 16                 # dots of clear paper between the type and the nearest hole

    dither: bool = True
    caption: str | None = None           # optional line on white paper under the film


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from pathlib import Path
    for path in candidates:
        name, _, index = path.partition("#")
        if Path(name).exists():
            try:
                return ImageFont.truetype(name, size, index=int(index or 0))
            except (OSError, ValueError):
                continue
    return ImageFont.load_default()


def _screen(size: tuple[int, int], fill: str) -> Image.Image:
    """The film base as an "L" tile: solid black, or a dither screen that prints cooler."""
    w, h = size
    if fill == "solid":
        return Image.new("L", size, 0)
    a = np.full((h, w), 255, dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    if fill == "half":                       # checkerboard: every other dot burns
        a[(xx + yy) % 2 == 0] = 0
    elif fill == "quarter":                  # one dot in four
        a[(xx % 2 == 0) & (yy % 2 == 0)] = 0
    else:
        raise ValueError(f"rail_fill must be solid, half or quarter (got {fill!r})")
    return Image.fromarray(a, "L")


def _heart(size: int, ink: int) -> Image.Image:
    """A heart drawn as geometry, upright in the strip's own frame.

    Drawn rather than set: ♥ is missing from DejaVu Serif and from most of the faces this
    runs against, and a glyph that falls back to another face prints at another weight.
    Two lobes and a point, at 4x then scaled down, which is all the antialiasing a
    1-bit tape needs.

    It is deliberately NOT turned with ``_vertical_text``: the strip hangs portrait and is
    read that way, so the heart stands up even though the names beside it lie down.

    The triangle's top edge sits on the line through both lobe centres. Any lower and the
    two tangent circles leave an uncovered pixel between them, which prints as a white
    speck in the middle of the heart.
    """
    s4 = size * 4
    flat = Image.new("L", (s4, s4), 255 - ink)
    d = ImageDraw.Draw(flat)
    r = s4 // 4
    d.ellipse([0, r // 2, 2 * r, r // 2 + 2 * r], fill=ink)
    d.ellipse([s4 - 2 * r, r // 2, s4, r // 2 + 2 * r], fill=ink)
    d.polygon([(0, 3 * r // 2), (s4 // 2, s4 - 1), (s4, 3 * r // 2)], fill=ink)
    flat = flat.resize((size, size), Image.LANCZOS)
    return flat.point(lambda v: ink if abs(v - ink) < 128 else 255 - ink)   # back to two tones


def _symbol(text: str, size: int, ink: int, serif: bool) -> Image.Image:
    """A character (or few) drawn upright, each in a face that has it.

    The rail face is a serif, and it carries ♥ ♡ ☀ ♪ but not ★ ✿ ❀ ✦ — those come from
    another font on the machine, so they print at a different weight to the letters beside
    them. Nothing here can draw a character no installed font has, and printing a .notdef
    box on a keepsake is worse than saying so, hence the refusal.
    """
    fonts = SERIF_FONTS if serif else SANS_FONTS
    base = _font(fonts, size)
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    parts = []
    for ch in text:
        face = font_for(ch, base)
        if face is None:
            raise ValueError(f"the symbol {ch!r} (U+{ord(ch):04X}) has no glyph in any font here — "
                             f"pick another, or use \"heart\", which is drawn rather than set")
        parts.append((ch, face, d.textlength(ch, font=face)))
    w = int(sum(p[2] for p in parts)) + 4
    h = max(sum(p[1].getmetrics()) for p in parts) + 4
    img = Image.new("L", (max(w, 1), max(h, 1)), 255 - ink)
    dr = ImageDraw.Draw(img)
    x = 2.0
    for ch, face, adv in parts:
        dr.text((x, 2), ch, font=face, fill=ink, anchor="la")
        x += adv
    return img


def _vertical_text(text: str, font, tracking: int, ink: int) -> Image.Image:
    """``text`` drawn on its side, reading top to bottom, as an "L" image (255 = clear)."""
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    widths = [d.textlength(ch, font=font) for ch in text]
    w = int(sum(widths)) + tracking * max(len(text) - 1, 0) + 4
    ascent, descent = font.getmetrics()
    h = ascent + descent + 4
    flat = Image.new("L", (w, h), 255 - ink)
    fd = ImageDraw.Draw(flat)
    x = 2.0
    for ch, cw in zip(text, widths):
        fd.text((x, 2), ch, font=font, fill=ink)
        x += cw + tracking
    return flat.rotate(-90, expand=True)     # -90: the line reads downward


def make_film_strip(photos: list[Image.Image | str], settings: FilmSettings | None = None,
                    when: datetime | None = None) -> Image.Image:
    """Compose the film strip and return it as a mode "1" image ``settings.width`` wide."""
    s = settings or FilmSettings()
    when = when or datetime.now()
    if s.base not in BASE_STYLES:
        raise ValueError(f"base must be one of {BASE_STYLES} (got {s.base!r})")
    # A hole is the absence of film: white where the rail is inked, black where the paper shows.
    hole_ink = 0 if s.base == "outline" else 255
    # A frame's keyline has to contrast with what the frame sits on, which is not always the rail.
    keyline_ink = 255 if s.base == "full" else 0

    window = s.width - s.rail_left - s.rail_right
    frame_w = window - 2 * s.frame_inset
    frame_h = round(frame_w * s.frame_aspect[1] / s.frame_aspect[0])
    n = len(photos)
    height = 2 * s.end_pad + n * frame_h + (n - 1) * s.frame_gap

    canvas = Image.new("L", (s.width, height), 255)
    if s.base == "outline":
        ImageDraw.Draw(canvas).rectangle([0, 0, s.width - 1, height - 1], outline=0, width=3)
    elif s.base == "rails":
        canvas.paste(_screen((s.rail_left, height), s.rail_fill), (0, 0))
        canvas.paste(_screen((s.rail_right, height), s.rail_fill), (s.width - s.rail_right, 0))
    else:
        canvas.paste(_screen((s.width, height), s.rail_fill), (0, 0))

    draw = ImageDraw.Draw(canvas)

    def _mask(run: Image.Image, ink: int) -> Image.Image:
        """Paste only the strokes, so the rail's screen shows through the gaps."""
        return run.point(lambda v: 255 if (v < 128) == (ink == 0) else 0)

    def bars(cx: int, y: int, mw: int, count: int) -> None:
        """A cluster of edge bars, the markings a film stock carries between frames."""
        for i in range(count):
            draw.rectangle([cx - mw // 2, y + i * 9, cx + mw // 2, y + i * 9 + 4], fill=hole_ink)

    # One grid for both rails: that shared phase is what makes the two edges mirror rather
    # than merely resemble each other, so nothing below may move a hole off it.
    grid = list(range(s.end_pad + (s.hole_pitch - s.hole_h) // 2,
                      height - s.end_pad - s.hole_h, s.hole_pitch))

    def holes_for(top: int, length: int) -> tuple[list[int], int, int]:
        """The holes left when the type sits at ``top``, and the clear rows at each end."""
        lo, hi = top - s.text_clear, top + length + s.text_clear
        kept = [y for y in grid if not (y + s.hole_h > lo and y < hi)]
        above = [y for y in kept if y < top]
        below = [y for y in kept if y > top + length]
        gap_a = top - (above[-1] + s.hole_h) if above else top - s.end_pad
        gap_b = (below[0] if below else height - s.end_pad) - (top + length)
        return kept, gap_a, gap_b

    def sprockets(cx: int, kept: list[int] | None = None) -> None:
        """A run of perforations down a rail, or just the holes in ``kept``."""
        for y in (grid if kept is None else kept):
            box = [cx - s.hole_w // 2, y, cx + s.hole_w // 2, y + s.hole_h]
            if s.hole_stroke:            # a drawn hole rather than a filled one
                draw.rounded_rectangle(box, radius=s.hole_radius, outline=hole_ink,
                                       width=s.hole_stroke)
            else:
                draw.rounded_rectangle(box, radius=s.hole_radius, fill=hole_ink)

    def rail(x0: int, x1: int, style: str) -> None:
        """Fill one rail, x0 inclusive to x1 exclusive."""
        if style == "sprockets":
            sprockets((x0 + x1) // 2)
        elif style == "marks":
            cx = (x0 + x1) // 2
            for pos, mw, count in s.marks:
                bars(cx, int(pos * height), mw, count)
        elif style == "text":
            runs = []
            if s.names:
                runs.append(_vertical_text(s.names, _font(SERIF_FONTS if s.serif else SANS_FONTS,
                                                          s.name_size), s.name_tracking, hole_ink))
            if s.ornament and runs:
                runs.append(_heart(s.ornament_size, hole_ink) if s.ornament == "heart"
                            else _symbol(s.ornament, s.ornament_size, hole_ink, s.serif))
            if s.date_format:
                runs.append(_vertical_text(s.date_format.format(date=when),
                                           _font(SERIF_FONTS if s.serif else SANS_FONTS, s.date_size),
                                           s.name_tracking, hole_ink))
            widest = max((r.width for r in runs), default=0)
            if widest > x1 - x0:
                raise ValueError(f"vertical type is {widest} dots wide and the rail is {x1 - x0}: "
                                 f"widen the rail or drop name_size/date_size")
            total = sum(r.height for r in runs) + s.text_gap * max(len(runs) - 1, 0)
            run_length = height - 2 * s.end_pad
            if total > run_length:
                raise ValueError(f"vertical type runs {total} dots down a {run_length} dot rail: "
                                 f"shorten the names, drop name_size, or add a photo to lengthen the strip")
            # Centre the type, then nudge it within half a pitch. The grid is fixed, so where the
            # opening falls on it decides how much of it is wasted: centred exactly, the type can
            # clear a hole at one end and miss the next by a dot or two at the other, leaving a gap
            # two holes wide at one end and none at the other. Prefer the offset that keeps the most
            # holes, then the one whose two ends match.
            natural = max(s.end_pad, (height - total) // 2)
            lo = max(s.end_pad, natural - s.hole_pitch // 2)
            hi = min(height - s.end_pad - total, natural + s.hole_pitch // 2)
            top = min(range(lo, max(lo, hi) + 1),
                      key=lambda t: (-len(holes_for(t, total)[0]),
                                     abs(holes_for(t, total)[1] - holes_for(t, total)[2]),
                                     abs(t - natural)))
            cx = (x0 + x1) // 2
            if s.text_rail_marks:
                bars(cx, top - 46, 20, 4)
            y = top
            for run in runs:
                canvas.paste(run, (x0 + (x1 - x0 - run.width) // 2, y), _mask(run, hole_ink))
                y += run.height + s.text_gap
            bottom = y - s.text_gap
            if s.text_rail_marks:
                bars(cx, bottom + 20, 14, 3)
            if s.text_rail_sprockets:
                sprockets(cx, holes_for(top, bottom - top)[0])
        elif style != "plain":
            raise ValueError(f"rail style must be one of {RAIL_STYLES} (got {style!r})")

    rail(0, s.rail_left, s.left_style)
    rail(s.width - s.rail_right, s.width, s.right_style)

    corner = Image.new("L", (frame_w, frame_h), 0)
    ImageDraw.Draw(corner).rounded_rectangle([0, 0, frame_w - 1, frame_h - 1],
                                             radius=s.frame_radius, fill=255)
    x = s.rail_left + s.frame_inset
    y = s.end_pad
    for photo in photos:
        if not isinstance(photo, Image.Image):
            photo = Image.open(photo)
        tile = prepare_photo(photo, (frame_w, frame_h), s.dither).convert("L")
        canvas.paste(tile, (x, y), corner)
        if s.frame_keyline:
            draw.rounded_rectangle([x - 1, y - 1, x + frame_w, y + frame_h], radius=s.frame_radius,
                                   outline=keyline_ink, width=s.frame_keyline)
        y += frame_h + s.frame_gap

    if s.caption:
        cap_font = _font(SANS_FONTS, 20)
        bbox = cap_font.getbbox(s.caption)
        strip_h = bbox[3] - bbox[1] + 24
        out = Image.new("L", (s.width, height + strip_h), 255)
        out.paste(canvas, (0, 0))
        cd = ImageDraw.Draw(out)
        cw = cd.textlength(s.caption, font=cap_font)
        cd.text(((s.width - cw) / 2, height + 12 - bbox[1]), s.caption, fill=0, font=cap_font)
        canvas = out

    return canvas.convert("1", dither=Image.NONE)


def type_extent(settings: FilmSettings, height: int, when: datetime | None = None) -> tuple[int, int]:
    """(dots the rail type runs, dots the rail has) — how close the names are to not fitting.

    ``make_film_strip`` refuses when the first exceeds the second; this is the same
    measurement offered before it comes to that, so an editor can show the headroom.
    """
    when = when or datetime.now()
    fonts = SERIF_FONTS if settings.serif else SANS_FONTS
    runs = []
    if settings.names:
        runs.append(_vertical_text(settings.names, _font(fonts, settings.name_size),
                                   settings.name_tracking, 255).height)
    if settings.ornament and runs:
        runs.append(settings.ornament_size if settings.ornament == "heart"
                    else _symbol(settings.ornament, settings.ornament_size, 255, settings.serif).height)
    if settings.date_format:
        runs.append(_vertical_text(settings.date_format.format(date=when),
                                   _font(fonts, settings.date_size), settings.name_tracking, 255).height)
    total = sum(runs) + settings.text_gap * max(len(runs) - 1, 0)
    return total, height - 2 * settings.end_pad


def ink_coverage(img: Image.Image, band: int = 24) -> tuple[float, float]:
    """(overall black fraction, worst fraction over any ``band`` rows).

    The second number is the one that matters for heat: the head survives a dark
    band, not a dark tape. Anything sustained near 1.0 is asking for trouble.
    """
    a = np.asarray(img.convert("1"), dtype=bool)
    black = ~a                                   # mode "1": True is white
    per_row = black.mean(axis=1)
    if len(per_row) < band:
        return float(black.mean()), float(per_row.mean())
    window = np.convolve(per_row, np.ones(band) / band, mode="valid")
    return float(black.mean()), float(window.max())


# -- presets and the saved copy ------------------------------------------------

# The build signed off on the bench 2026-09-21 ("N · Mirrored"): paper as the base with the film
# drawn in line, both rails perforated on the same pitch, the right run opening only for the names
# and the date with a heart between them. Defined here rather than in the review script so the
# studio, the booth and the proofs are all the same strip.
FILM = FilmSettings(
    frame_aspect=(4, 3),                 # the capture's own shape: a square frame crops 25% of its width
    rail_left=48, rail_right=48,
    left_style="sprockets", right_style="text",
    base="outline", frame_keyline=2,
    text_rail_sprockets=True, text_rail_marks=False, text_clear=4,
    ornament="heart",
    names="ALEX & SAM", date_format="{date:%m.%d.%Y}",
    name_size=34, date_size=28,
)

PRESETS = {"film": FILM}

_TUPLE_FIELDS = {"frame_aspect"}


def to_dict(settings: FilmSettings) -> dict:
    return asdict(settings)


def from_dict(data: dict, base: FilmSettings | None = None) -> FilmSettings:
    """Settings from a JSON-style dict; unknown keys are ignored, missing ones come from ``base``."""
    s = replace(base or FILM)
    known = {f.name for f in fields(FilmSettings)}
    for key, value in data.items():
        if key not in known:
            continue
        if key in _TUPLE_FIELDS and value is not None:
            value = tuple(value)
        elif key == "marks":
            value = [tuple(mark) for mark in value]
        setattr(s, key, value)
    return s
