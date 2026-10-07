"""Postage-stamp photostrip: each photo issued as a stamp, perforated edge and all.

The look is a sheet torn off a roll: a scalloped perforated edge punched right
through the tape, a selvedge of clear paper, a framed vignette carrying the
photo, the post's name across the top, a denomination and a date underneath,
and a duplex cancel — ring plus wavy killer bars — struck across one corner.

The edge is the whole trick and it is made once, geometrically: the stamp bodies
are drawn as one mask, perforation circles are punched along every stamp edge
(so the seam between two joined stamps is punched from both sides and lands on
the same holes), and the line that gets printed is that mask minus an eroded
copy of itself. One mechanism draws the outer scallops and the rings around the
interior seam holes, so they cannot drift apart.

Like ``film`` this is mostly paper: only the perf line, the frame and the type
carry ink, so the worst band stays well under what heats the head — except in
``frame_style="band"``, where the frame is solid. ``ink_coverage`` (defined in
``film``, imported here so there is one measure) says what any build costs.

Composed at the printer's native 384 dots so it prints 1:1.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .film import FILM, SANS_FONTS, SERIF_FONTS, ink_coverage   # noqa: F401  (ink_coverage re-exported: one measure of heat)
from .printer import WIDTH_PX
from .receipt import font_for                                # the per-glyph fallback lives there
from .strip import prepare_photo

SHEET_STYLES = ("strip", "singles")
FRAME_STYLES = ("keyline", "band", "none", "bleed")
POSTMARK_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


@dataclass
class StampSettings:
    width: int = WIDTH_PX
    margin: int = 10                 # paper outside the perforated edge, left/right/top/bottom

    # how the stamps sit together
    sheet: str = "strip"             # strip: joined, sharing a perforated seam | singles: cut apart
    gap: int = 26                    # paper between stamps when sheet == "singles"

    # the perforated edge
    perf_pitch: int = 17             # dots between hole centres (adjusted to divide each edge evenly)
    perf_radius: int = 6
    edge_stroke: int = 2             # how thick the printed edge line is

    # the design
    selvedge: int = 13               # clear paper between the perforations and the frame
    # keyline: a ruled frame | band: a solid border with reversed type | none: type on paper
    # bleed: no frame and no selvedge — the picture runs to the perforations on three sides and the
    #        holes bite into it, with only the header line left on paper
    frame_style: str = "keyline"
    frame_stroke: int = 2
    band: int = 30                   # the inked border's thickness when frame_style == "band"
    pad: int = 7                     # breathing room between the frame and the type
    photo_aspect: tuple[int, int] = (4, 3)
    photo_rule: int = 1              # hairline around the vignette; 0 for none

    # the type
    legend: str = "PHOTOBOOTH POST"
    # What each stamp carries over its picture, when they are not all to say the same thing:
    # one line per stamp, ``str.format``ed with date=, falling back to ``legend`` past the end
    # of the list. A stamp torn off on its own keeps whatever its own line says.
    headers: list[str] = field(default_factory=list)
    legend_size: int = 22
    legend_tracking: int = 3
    denomination: str = "44¢"
    denom_size: int = 27
    date_format: str = "{date:%d %b %Y}"
    date_size: int = 20
    serif: bool = True

    # the cancel
    postmark: str | None = "top-right"
    postmark_ring: bool = True       # False strikes the killer bars alone, with no dated ring
    postmark_d: int = 92             # ring diameter
    postmark_city: str = "LUNA"
    postmark_date: str = "{date:%d.%m.%y}"
    postmark_bars: int = 5           # wavy killer bars trailing off the ring; 0 for a ring alone
    postmark_bar_len: int = 132

    dither: bool = True
    caption: str | None = None       # a line on plain paper under the sheet


# -- small type helpers --------------------------------------------------------
# The per-glyph fallback (a face that has ¢ is not always the face the rest of the line is set in)
# is receipt's; these are the two calls this layout needs, over its ``font_for``.

def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        name, _, index = path.partition("#")
        if Path(name).exists():
            try:
                return ImageFont.truetype(name, size, index=int(index or 0))
            except (OSError, ValueError):
                continue
    return ImageFont.load_default()


def _runs(text: str, font) -> list[tuple[str, object]]:
    """``text`` split into runs, each in a face that can draw it; undrawable characters go."""
    runs: list[tuple[str, object]] = []
    for ch in text:
        face = font_for(ch, font)
        if face is None:
            continue
        if runs and runs[-1][1] is face:
            runs[-1] = (runs[-1][0] + ch, face)
        else:
            runs.append((ch, face))
    return runs


def _width(draw: ImageDraw.ImageDraw, text: str, font, tracking: int = 0) -> int:
    runs = _runs(text, font)
    total = sum(draw.textlength(t, font=f) for t, f in runs)
    n = sum(len(t) for t, _ in runs)
    return int(total) + tracking * max(n - 1, 0)


def _draw(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill: int,
          tracking: int = 0) -> None:
    """Draw ``text`` with its left edge at ``xy``, ``xy[1]`` being the baseline."""
    x, baseline = xy
    for t, face in _runs(text, font):
        if not tracking:
            draw.text((x, baseline), t, font=face, fill=fill, anchor="ls")
            x += int(draw.textlength(t, font=face))
            continue
        for ch in t:
            draw.text((x, baseline), ch, font=face, fill=fill, anchor="ls")
            x += int(draw.textlength(ch, font=face)) + tracking


def _fit(draw: ImageDraw.ImageDraw, text: str, candidates: list[str], size: int, room: int,
         floor: int, tracking: int = 0):
    """The largest face at or under ``size`` that sets ``text`` within ``room`` dots, or None.

    None means it does not fit at ``floor`` either. Shrinking past that is no kindness: the
    tape prints at 8 dots to the millimetre and type below it comes off as a grey smear.
    """
    while size >= floor:
        font = _font(candidates, size)
        if _width(draw, text, font, tracking) <= room:
            return font
        size -= 1
    return None


def _centre_baseline(top: int, bottom: int, font) -> int:
    """The baseline that centres one line of ``font`` between ``top`` and ``bottom``."""
    ascent, descent = font.getmetrics()
    return top + ((bottom - top + 1) - (ascent + descent)) // 2 + ascent


# -- the cancel ----------------------------------------------------------------

def _shrink(img: Image.Image, k: int) -> Image.Image:
    """Back down from the 3x drawing to printer dots, and back to two tones."""
    return img.resize((img.width // k, img.height // k), Image.LANCZOS).point(
        lambda v: 255 if v > 96 else 0)


def _bars(img: Image.Image, s: StampSettings, d: int, bar_len: int, k: int,
          bars_left: bool) -> None:
    """The killer bars, drawn into ``img`` on the side the cancel trails off to."""
    if not s.postmark_bars:
        return
    bars = Image.new("L", (min(bar_len + d // 2, img.width), d), 0)
    g = ImageDraw.Draw(bars)
    spread, amp = d * 0.62, d * 0.045
    step = spread / max(s.postmark_bars - 1, 1)
    for i in range(s.postmark_bars):
        y0 = d / 2 - spread / 2 + i * step
        pts = [(x, y0 + amp * math.sin(x / (d * 0.13))) for x in range(0, bars.width, max(1, k))]
        g.line(pts, fill=255, width=3 * k, joint="curve")
    if bars_left:
        img.paste(bars.transpose(Image.FLIP_LEFT_RIGHT), (0, 0))
    else:
        img.paste(bars, (img.width - bars.width, 0))


def _postmark_mask(s: StampSettings, when: datetime,
                   bars_left: bool) -> tuple[Image.Image, Image.Image]:
    """The duplex cancel as a stroke mask: 255 where ink goes, 0 elsewhere.

    Built as a mask rather than drawn straight onto the sheet because it lands on a
    dithered photo, where a black line on its own is worth very little. The caller
    dilates this for a white halo and prints the strokes inside it, so the cancel
    reads over a face as well as over the paper.

    Only the BARS change side — the ring is composed last and upright, because a mask
    flipped whole puts the city and the date on backwards.

    Returns (strokes, disc): the second is the ring's interior, which the caller clears to
    paper first. A cancel struck over a dithered face is otherwise read off a tape at arm's
    length as a smudge, and the date in the middle of it is the one thing on the stamp that
    says which day this was.

    Drawn at 3x and shrunk: the ring is the one curve here whose steps would show.
    """
    k = 3
    d = s.postmark_d * k
    bar_len = s.postmark_bar_len * k if s.postmark_bars else 0

    ring = Image.new("L", (d, d), 0)
    g = ImageDraw.Draw(ring)
    if not s.postmark_ring:
        # Bars alone, and the mask is only as wide as they are: leave the ring's width in and
        # they hang a ring's worth inboard of the corner, reading as a scribble over the face
        # rather than as a cancel across the edge.
        img = Image.new("L", (bar_len, d), 0)
        _bars(img, s, d, bar_len, k, bars_left)
        return _shrink(img, k), Image.new("L", (img.width // k, img.height // k), 0)
    g.ellipse([0, 0, d - 1, d - 1], outline=255, width=3 * k)
    inset = 7 * k
    g.ellipse([inset, inset, d - 1 - inset, d - 1 - inset], outline=255, width=2 * k)
    cx = d // 2
    city = s.postmark_city
    date_text = s.postmark_date.format(date=when) if s.postmark_date else ""
    # the two rules that box the date in, stopping short of the inner ring
    rules = (round(d * 0.44), round(d * 0.78))
    for y in rules:
        half = int(math.sqrt(max((d / 2 - inset - 2 * k) ** 2 - (y - d / 2) ** 2, 0)))
        g.line([cx - half, y, cx + half, y], fill=255, width=2 * k)
    # Both lines are set to the chord they sit on, not to a fixed size: a long city name or a
    # four-digit year would otherwise run into the inner ring, where there is nowhere to go.
    def chord(y: float) -> int:
        return 2 * int(math.sqrt(max((d / 2 - inset - 3 * k) ** 2 - (y - d / 2) ** 2, 0)))

    floor = max(3 * k, round(d * 0.085))            # about a millimetre of cap height on paper
    if city:
        face = _fit(g, city, SANS_FONTS, min(round(d * 0.15), int((rules[0] - inset) * 0.60)),
                    chord(rules[0] * 0.75), floor, k)
        if face is None:
            raise ValueError(f"postmark_city {city!r} will not fit a {s.postmark_d}-dot ring at a "
                             f"size that prints — shorten it or raise postmark_d")
        _draw(g, (cx - _width(g, city, face, k) // 2,
                  _centre_baseline(inset + 3 * k, rules[0] - 2 * k, face)), city, face, 255, k)
    if date_text:
        face = _fit(g, date_text, SANS_FONTS,
                    min(round(d * 0.185), int((rules[1] - rules[0]) * 0.72)),
                    min(chord(rules[0]), chord(rules[1])) - 2 * k, floor)
        if face is None:
            raise ValueError(f"the postmark date {date_text!r} will not fit a {s.postmark_d}-dot "
                             f"ring at a size that prints — shorten postmark_date or raise postmark_d")
        _draw(g, (cx - _width(g, date_text, face) // 2,
                  _centre_baseline(rules[0] + 2 * k, rules[1] - 2 * k, face)), date_text, face, 255)

    img = Image.new("L", (d + bar_len, d), 0)
    _bars(img, s, d, bar_len, k, bars_left)
    rx = bar_len if bars_left else 0
    # the bars run up to the ring and stop there, as a duplex hand stamp's do; left to cross it
    # they print straight through the city and the date
    ImageDraw.Draw(img).ellipse([rx - k, -k, rx + d + k, d + k], fill=0)
    img.paste(ring, (rx, 0), ring)

    disc = Image.new("L", img.size, 0)
    ImageDraw.Draw(disc).ellipse([rx, 0, rx + d - 1, d - 1], fill=255)
    return _shrink(img, k), _shrink(disc, k)


def _place_postmark(canvas: Image.Image, s: StampSettings, when: datetime,
                    photo: tuple[int, int, int, int], box: tuple[int, int, int, int]) -> None:
    """Strike the cancel across one corner of the vignette, halo first.

    The ring hangs a quarter of itself off the photo — over the frame and into the selvedge,
    the way a hand stamp lands — and the bars run INWARD across the picture, so nothing runs
    off the side of the tape and gets sheared by the print head's last dot.
    """
    if not s.postmark or not (s.postmark_ring or s.postmark_bars):
        return
    if s.postmark not in POSTMARK_CORNERS:
        raise ValueError(f"postmark must be None or one of {POSTMARK_CORNERS} (got {s.postmark!r})")
    vertical, horizontal = s.postmark.split("-")
    mark, disc = _postmark_mask(s, when, bars_left=horizontal == "right")
    px0, py0, px1, py1 = photo
    ring = s.postmark_d
    off = ring // 5
    x = (px1 + off - mark.width + 1) if horizontal == "right" else (px0 - off)
    y = (py0 - off) if vertical == "top" else (py1 + off - ring + 1)
    # never over the perforations: a cancel that eats the edge line reads as a misprint
    x = max(box[0] + 2, min(x, box[2] - 2 - mark.width))
    y = max(box[1] + 2, min(y, box[3] - 2 - mark.height))
    canvas.paste(255, (x, y), ImageChops.lighter(disc, mark.filter(ImageFilter.MaxFilter(5))))
    canvas.paste(0, (x, y), mark)


# -- the sheet -----------------------------------------------------------------

def _edge_points(a: int, b: int, pitch: int) -> list[float]:
    """Hole centres from ``a`` to ``b`` inclusive, on the pitch that divides the edge evenly."""
    n = max(1, round((b - a) / pitch))
    return [a + (b - a) * i / n for i in range(n + 1)]


def _perforate(mask: Image.Image, boxes: list[tuple[int, int, int, int]], s: StampSettings) -> None:
    """Punch the perforations along all four edges of every stamp box.

    Both stamps either side of a joined seam punch it, on the same points, so the seam is
    one row of holes rather than two that nearly line up.
    """
    d = ImageDraw.Draw(mask)
    r = s.perf_radius
    for x0, y0, x1, y1 in boxes:
        centres = [(x, y0) for x in _edge_points(x0, x1, s.perf_pitch)]
        centres += [(x, y1) for x in _edge_points(x0, x1, s.perf_pitch)]
        centres += [(x0, y) for y in _edge_points(y0, y1, s.perf_pitch)]
        centres += [(x1, y) for y in _edge_points(y0, y1, s.perf_pitch)]
        for cx, cy in centres:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)


@dataclass(frozen=True)
class _Metrics:
    """Every length one stamp is built from, worked out once from the settings."""
    height: int          # the whole stamp, perforation to perforation
    top: int             # design rect down to the top of the vignette
    photo_w: int
    photo_h: int
    bottom: int          # vignette bottom down to the design rect
    side: int            # design rect in to the side of the vignette


def metrics(s: StampSettings) -> _Metrics:
    """The stamp's lengths. The width is the driver: everything else follows the photo's aspect."""
    if s.frame_style not in FRAME_STYLES:
        raise ValueError(f"frame_style must be one of {FRAME_STYLES} (got {s.frame_style!r})")
    legend_font = _font(SERIF_FONTS if s.serif else SANS_FONTS, s.legend_size)
    denom_font = _font(SERIF_FONTS if s.serif else SANS_FONTS, s.denom_size)
    date_font = _font(SANS_FONTS, s.date_size)
    line = lambda f: sum(f.getmetrics())                              # noqa: E731
    legend_h = line(legend_font) if (s.legend or any(s.headers)) else 0
    foot_h = max((line(denom_font) if s.denomination else 0),
                 (line(date_font) if s.date_format else 0))

    if s.frame_style == "bleed":
        # The picture is the stamp. Only the header keeps its band of paper; the sides and the
        # foot run to the edge, so there is no selvedge and no side inset to take off the width.
        top = (s.selvedge + legend_h + s.pad) if legend_h else 0
        photo_w = s.width - 2 * s.margin
        photo_h = round(photo_w * s.photo_aspect[1] / s.photo_aspect[0])
        return _Metrics(top + photo_h, top, photo_w, photo_h, 0, 0)

    if s.frame_style == "bleed":
        # The picture is the stamp. Only the header keeps its band of paper; the sides and the foot
        # run to the edge, so there is no selvedge and no side inset to take off the width.
        top = (s.selvedge + legend_h + s.pad) if legend_h else 0
        photo_w = s.width - 2 * s.margin
        photo_h = round(photo_w * s.photo_aspect[1] / s.photo_aspect[0])
        return _Metrics(top + photo_h, top, photo_w, photo_h, 0, 0)

    if s.frame_style == "band":
        # the border is solid and the type is reversed out of it, so each band has to be at
        # least as deep as the line it carries
        side = s.band
        top = max(s.band, legend_h + 2 * s.pad) if legend_h else s.band
        bottom = max(s.band, foot_h + 2 * s.pad) if foot_h else s.band
    else:
        rule = s.frame_stroke if s.frame_style == "keyline" else 0
        side = rule + s.pad
        top = (rule + 2 * s.pad + legend_h) if legend_h else side
        # With nothing under the picture, the band below it matches the band above rather than
        # closing up to a margin: a vignette with type over it and a hairline under it reads as
        # a crop, not as a design.
        bottom = (rule + 2 * s.pad + foot_h) if foot_h else (top if legend_h else side)

    photo_w = s.width - 2 * s.margin - 2 * s.selvedge - 2 * side
    if photo_w < 80:
        raise ValueError(f"the vignette is only {photo_w} dots across: drop margin, selvedge "
                         f"or the frame")
    photo_h = round(photo_w * s.photo_aspect[1] / s.photo_aspect[0])
    return _Metrics(2 * s.selvedge + top + photo_h + bottom, top, photo_w, photo_h, bottom, side)


def sheet_length(s: StampSettings) -> int | None:
    """How many stamps this copy is written for — one per header line — or None for any number.

    ``PAIR`` is a pair because it has two lines to say, the day and the names; a caller with more
    photos than that takes the first ``sheet_length`` of them rather than printing stamps the copy
    has nothing to put over.
    """
    return len(s.headers) or None


def stamp_height(s: StampSettings) -> int:
    """One stamp's height in dots — 8 to the millimetre."""
    return metrics(s).height


def make_stamp_sheet(photos: list[Image.Image | str | Path], settings: StampSettings | None = None,
                     when: datetime | None = None) -> Image.Image:
    """Compose the sheet and return it as a mode "1" image ``settings.width`` wide."""
    s = settings or StampSettings()
    when = when or datetime.now()
    if s.sheet not in SHEET_STYLES:
        raise ValueError(f"sheet must be one of {SHEET_STYLES} (got {s.sheet!r})")
    if s.sheet == "singles" and s.gap < 2 * s.perf_radius + 4:
        raise ValueError(f"gap {s.gap} is too small for a {s.perf_radius}-dot perforation: "
                         f"neighbouring stamps would punch into each other — use at least "
                         f"{2 * s.perf_radius + 4}, or sheet='strip'")
    if not photos:
        raise ValueError("a sheet needs at least one photo")

    m = metrics(s)
    stamp_h = m.height
    n = len(photos)
    pitch = stamp_h if s.sheet == "strip" else stamp_h + s.gap
    height = s.margin * 2 + stamp_h * n + (s.gap * (n - 1) if s.sheet == "singles" else 0)

    x0 = s.margin
    x1 = s.width - s.margin - 1
    boxes = [(x0, s.margin + i * pitch, x1, s.margin + i * pitch + stamp_h - 1) for i in range(n)]

    # the perforated edge: one mask for every body, punched, then printed as mask-minus-erosion
    body = Image.new("L", (s.width, height), 0)
    bd = ImageDraw.Draw(body)
    for box in boxes:
        bd.rectangle(box, fill=255)
    _perforate(body, boxes, s)
    eroded = body.filter(ImageFilter.MinFilter(2 * s.edge_stroke + 1))
    edge = ImageChops.subtract(body, eroded)

    canvas = Image.new("L", (s.width, height), 255)
    draw = ImageDraw.Draw(canvas)

    legend_font = _font(SERIF_FONTS if s.serif else SANS_FONTS, s.legend_size)
    denom_font = _font(SERIF_FONTS if s.serif else SANS_FONTS, s.denom_size)
    date_font = _font(SANS_FONTS, s.date_size)
    date_text = s.date_format.format(date=when) if s.date_format else ""
    ink = 255 if s.frame_style == "band" else 0        # type colour: reversed out of a solid border

    def header_for(i: int) -> str:
        """The line over stamp ``i``: its own, or the legend once the list runs out."""
        text = s.headers[i] if i < len(s.headers) else s.legend
        return text.format(date=when) if text else ""

    for i, (box, photo) in enumerate(zip(boxes, photos)):
        bx0, by0, bx1, by1 = box
        dx0, dy0 = bx0 + s.selvedge, by0 + s.selvedge
        dx1, dy1 = bx1 - s.selvedge, by1 - s.selvedge
        if s.frame_style == "band":
            draw.rectangle([dx0, dy0, dx1, dy1], fill=0)
        elif s.frame_style == "keyline":
            draw.rectangle([dx0, dy0, dx1, dy1], outline=0, width=s.frame_stroke)

        if s.frame_style == "bleed":
            px0, py0, px1, py1 = bx0, by0 + m.top, bx1, by1
        else:
            px0, py0 = dx0 + m.side, dy0 + m.top
            px1, py1 = px0 + m.photo_w - 1, py0 + m.photo_h - 1

        if not isinstance(photo, Image.Image):
            photo = Image.open(photo)
        # through the eroded body: a picture that runs to the edge is cut by the perforations
        # rather than filling them in, and the edge line goes on over it at the end
        canvas.paste(prepare_photo(photo, (m.photo_w, m.photo_h), s.dither).convert("L"),
                     (px0, py0), eroded.crop((px0, py0, px1 + 1, py1 + 1)))
        if s.photo_rule:
            draw.rectangle([px0 - s.photo_rule, py0 - s.photo_rule,
                            px1 + s.photo_rule, py1 + s.photo_rule], outline=ink, width=s.photo_rule)

        header = header_for(i)
        if header:
            head_top = by0 if s.frame_style == "bleed" else dy0
            face = _fit(draw, header, SERIF_FONTS if s.serif else SANS_FONTS, s.legend_size,
                        m.photo_w, s.legend_size // 2, s.legend_tracking)
            if face is None:
                raise ValueError(f"the header {header!r} is wider than the {m.photo_w}-dot stamp "
                                 f"even set small — shorten it or drop legend_size")
            w = _width(draw, header, face, s.legend_tracking)
            _draw(draw, ((s.width - w) // 2, _centre_baseline(head_top, py0 - 1, face)),
                  header, face, ink, s.legend_tracking)
        if s.denomination:
            _draw(draw, (px0 + 2, _centre_baseline(py1 + 1, dy1, denom_font)),
                  s.denomination, denom_font, ink)
        if date_text:
            w = _width(draw, date_text, date_font)
            _draw(draw, (px1 - 1 - w, _centre_baseline(py1 + 1, dy1, date_font)),
                  date_text, date_font, ink)

        _place_postmark(canvas, s, when, (px0, py0, px1, py1), box)

    canvas.paste(0, (0, 0), edge)          # last: nothing prints over the perforated edge

    if s.caption:
        cap_font = _font(SANS_FONTS, 20)
        ascent, descent = cap_font.getmetrics()
        strip_h = ascent + descent + 20
        out = Image.new("L", (s.width, height + strip_h), 255)
        out.paste(canvas, (0, 0))
        cd = ImageDraw.Draw(out)
        cw = _width(cd, s.caption, cap_font)
        _draw(cd, ((s.width - cw) // 2, height + 10 + ascent), s.caption, cap_font, 0)
        canvas = out

    return canvas.convert("1", dither=Image.NONE)


# -- presets and the saved copy ------------------------------------------------

# The issue: joined stamps down the tape, ruled frames, a cancel struck across the top-right
# corner of each. Defined here so proofs, any editor and the booth all print the same stamp.
STAMP = StampSettings()

# Chosen on the bench 2026-09-21 ("H · Pair"): two stamps cut apart rather than joined, no cancel
# at all, no value, the day set over the first picture and the names over the second. Everything
# that is left is the perforated edge, the ruled frame and one line of type per stamp. A picture run
# out to the perforations (frame_style="bleed") was tried against this and turned down. Both of those lines are the FILM strip's own settings rather than copies —
# change the names on the tape bench and the stamps follow, which is the whole point of the pair
# coming out of one session.
PAIR = replace(STAMP, sheet="singles", postmark=None, denomination="", date_format="",
               headers=[FILM.date_format, FILM.names])

# One large stamp for a single photo, portrait, as a souvenir single.
SINGLE = StampSettings(photo_aspect=(3, 4), denomination="1 KISS", sheet="singles")

PRESETS = {"pair": PAIR, "stamp": STAMP, "single": SINGLE}

_TUPLE_FIELDS = {"photo_aspect"}


def to_dict(settings: StampSettings) -> dict:
    return asdict(settings)


def from_dict(data: dict, base: StampSettings | None = None) -> StampSettings:
    """Settings from a JSON-style dict; unknown keys are ignored, missing ones come from ``base``."""
    s = replace(base or STAMP)
    known = {f.name for f in fields(StampSettings)}
    for key, value in data.items():
        if key not in known:
            continue
        if key in _TUPLE_FIELDS and value is not None:
            value = tuple(value)
        setattr(s, key, value)
    return s
