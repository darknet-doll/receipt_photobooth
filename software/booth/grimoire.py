"""Grimoire keepsake: one photograph in the arched window of a spellbook cover.

The look is the reference cover: a double rule around the whole tape with a
screened band between the two lines, an arched window cut through the middle for
the photograph, a crescent moon and a sparkle in each shoulder the arch leaves
empty, a vine either side of a pentacle below it, and -- where the frame OPENS
at the foot -- a cartouche carrying the place, the date and the handle.

Like ``film`` this is a design that could ask for a lot of ink, and for the same
reason it does not: the print head has no density control, so a sustained
high-coverage run comes off grey and streaky. The band between the rules is the
only large area of tone here and it is a SCREEN rather than a fill
(``band_fill``); ``film.ink_coverage``, re-exported below, is the measure.

Every ornament is drawn as geometry rather than set as a character, so it prints
the same on the Pi and on a laptop preview -- the reasoning is ``film._heart``'s.
Curves are drawn at ``SS`` times the printer's resolution and thresholded back
down, which is what keeps the arch from going to staircases at 384 dots.

Composed at the printer's native 384 dots so it prints 1:1.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw

from .film import SANS_FONTS, SERIF_FONTS, ink_coverage  # noqa: F401  (ink_coverage re-exported: one measure of heat)
from .printer import WIDTH_PX
from .stamp import _centre_baseline, _draw, _fit, _width  # the tracked-text setter, one copy of it
from .strip import prepare_photo

BAND_FILLS = ("none", "quarter", "half", "solid")

SS = 4          # curves are drawn this many times over and thresholded back to printer dots


@dataclass
class GrimoireSettings:
    width: int = WIDTH_PX

    # the cover: two rules with a band of tone between them. frame=False drops all of it and
    # the arch stands on bare paper instead, held by the vines rooted at the edges.
    frame: bool = True
    margin: int = 8                           # white paper outside the frame; below it either way
    outer_rule: int = 3
    band: int = 16
    band_fill: str = "quarter"                # none | quarter | half | solid -- see the module docstring
    inner_rule: int = 2
    corner_radius: int = 12
    panel_pad: int = 6                        # inner rule -> the window's bounding box

    # the arched window the photograph sits in
    window_inset: int = 6                     # panel edge -> window, left and right (framed)
    edge_inset: int = 56                      # paper edge -> window, left and right (unframed)
    top_pad: int = 26                         # paper above the crown, and below the type (unframed)
    # Square, and the furniture above is cut back to suit it (2026-09-25). The camera is 16:9
    # landscape, so a portrait window throws away most of the capture's width before anything is
    # printed -- at 4:5 it kept 45%, at 1:1 it keeps 56% -- and squaring it also makes the print
    # SHORTER, not longer, which paying for a bigger picture usually does not.
    window_aspect: tuple[int, int] = (1, 1)   # (w, h) of the window's bounding box, arch included
    window_foot_radius: int = 18
    window_rule: int = 3
    window_keyline: int = 2                   # a second, thinner arch outside the first; 0 for none
    window_keyline_gap: int = 6
    dither: bool = True

    # ornament
    moons: bool = True
    moon_size: int = 28
    sprigs: bool = True
    sprig_size: int = 58
    stars: bool = True
    star_size: int = 14
    # Vines rooted off the paper edge, growing up and in beside the window. Each is placed at a
    # fraction of the window's height and sized down to the paper the arch leaves it at its FOOT,
    # which is where the curve comes closest -- see _shoulder.
    edge_vines: bool = False
    edge_vine_size: int = 66
    edge_vine_depths: list[float] = field(default_factory=lambda: [0.10, 0.36, 0.62, 0.88])
    edge_vine_bleed: int = 8                  # how far the stems run off the edge of the paper
    # Woven vines: two stems half a period out of phase, so they cross at known points and the
    # one passing UNDER is cut there, parity alternating down the braid. HEXCODE's floral rule is
    # weave, don't layer -- strands cross, they never merge, and a blob is a bug not a style.
    vine_weave: bool = False
    # Proportions are ratios of the braid's own column, not fixed dots: the same numbers then
    # read right on a 45-dot margin run and a 28-dot run beside the title. Stem and break stay
    # absolute -- those are set by what the print head can hold, not by the composition.
    braid_pitch: float = 1.55                 # half period as a multiple of the column width
    braid_amp: float = 0.19                   # stem swing off the centre line, ditto
    braid_leaf: float = 0.30
    braid_leaf_aspect: float = 0.52       # fatter than a thorn; a narrow blade reads as a spike
    braid_stem: int = 3
    braid_break: int = 4                      # white cut either side of the strand passing over

    # A vine border: the frame is the vine itself, grown once around the whole perimeter.
    vine_border: bool = False
    border_width: int = 44                    # the band the vine occupies, paper edge inward
    border_radius: int = 26                   # how far the corners are rounded
    pentacle: bool = False
    pentacle_size: int = 56
    pentacle_rule: int = 2
    ornament_gap: int = 18                    # window -> pentacle -> cartouche

    # the opening at the foot
    plaque: bool = True                       # the cartouche's outline; off sets the type on bare paper
    title_row: bool = True                    # set the title BETWEEN two vines instead of above the rest
    title_row_gap: int = 14                   # between a vine and the title
    title_date_gap: int = 6                   # title row -> whatever is set under it
    title_row_margin: int = 6                 # paper outside the vines in that row
    title_vine_width: int = 22                # how wide the woven run in that row is
    plaque_width: int = 296
    plaque_radius: int = 16
    plaque_rule: int = 2
    plaque_pad: int = 12                      # inside the cartouche, above and below the type
    line_gap: int = 4
    title: str = "GRIMOIRE"
    title_size: int = 38
    title_tracking: int = 8
    date_format: str = "{date:%m.%d.%Y}"
    date_size: int = 21                       # deliberately smaller than the title above it
    handle: str = ""                          # dropped 2026-09-25: the date carries the foot alone
    handle_size: int = 20
    serif: bool = True


# -- tone and silhouettes ------------------------------------------------------

def _screen(size: tuple[int, int], fill: str) -> Image.Image:
    """The band as an "L" tile: paper, a dot screen that prints cool, or solid ink."""
    w, h = size
    if fill == "solid":
        return Image.new("L", size, 0)
    if fill == "none":
        return Image.new("L", size, 255)
    ys, xs = np.mgrid[0:h, 0:w]
    on = (xs + ys) % 2 == 0 if fill == "half" else ((xs % 2 == 0) & (ys % 2 == 0))
    return Image.fromarray(np.where(on, 0, 255).astype("uint8"), "L")


def _hard(mask: Image.Image) -> Image.Image:
    """A supersampled mask back at printer dots, and back to two tones."""
    return mask.point(lambda v: 255 if v > 127 else 0)


def _arch(size: tuple[int, int], foot_radius: int) -> Image.Image:
    """The window silhouette: a half-round top on straight sides, rounded at the foot.

    Solid white on black, at printer dots. Offsetting this shape inward by ``t``
    is exactly ``_arch((w - 2t, h - 2t), foot_radius - t)`` placed at ``(t, t)``
    -- the arch is a semicircle, so its radius loses ``t`` with the sides. That
    identity is what ``_arch_ring`` and the keyline are built on.
    """
    w, h = max(size[0], 1), max(size[1], 1)
    big = (w * SS, h * SS)
    r = min(big[0] // 2, big[1] // 2)
    fr = max(foot_radius, 0) * SS
    mask = Image.new("L", big, 0)
    d = ImageDraw.Draw(mask)
    d.pieslice([0, 0, big[0] - 1, 2 * r - 1], 180, 360, fill=255)
    d.rounded_rectangle([0, r, big[0] - 1, big[1] - 1], radius=min(fr, r), fill=255)
    d.rectangle([0, r, big[0] - 1, min(r + fr, big[1] - 1)], fill=255)   # square off the join
    return _hard(mask.resize((w, h), Image.LANCZOS))


def _shoulder(win_w: int, depth: int) -> float:
    """Free paper between the window's edge and the arch, ``depth`` dots below the crown.

    The arch is a semicircle of radius ``win_w / 2``, so the shoulder is widest at the
    crown and gone by the time the curve meets the straight side. Ornament is placed
    against this rather than against the window's bounding box: the box says there is
    almost no room at the top corners, and the curve says there is plenty.
    """
    r = win_w / 2
    dy = r - min(depth, r)
    return r - math.sqrt(max(r * r - dy * dy, 0.0))


def _arch_ring(size: tuple[int, int], foot_radius: int, stroke: int) -> Image.Image:
    """``_arch`` as a stroke ``stroke`` dots thick, drawn inward from ``size``."""
    ring = _arch(size, foot_radius)
    if stroke > 0:
        inner = _arch((size[0] - 2 * stroke, size[1] - 2 * stroke), foot_radius - stroke)
        ring.paste(0, (stroke, stroke), inner)
    return ring


# -- ornament, all of it geometry ---------------------------------------------

def _crescent(size: int) -> Image.Image:
    """A waxing crescent, horns to the left. Mirror it for the other shoulder."""
    d = size * SS
    m = Image.new("L", (d, d), 0)
    g = ImageDraw.Draw(m)
    g.ellipse([0, 0, d - 1, d - 1], fill=255)
    bite = round(d * 0.34)
    g.ellipse([bite, -round(d * 0.04), bite + d - 1, d - 1 - round(d * 0.04)], fill=0)
    return _hard(m.resize((size, size), Image.LANCZOS))


def _leaf(length: int, width: int, angle: float) -> Image.Image:
    leaf = Image.new("L", (max(length, 1), max(width, 1)), 0)
    ImageDraw.Draw(leaf).ellipse([0, 0, length - 1, width - 1], fill=255)
    return leaf.rotate(angle, expand=True, resample=Image.BICUBIC)


def _sprig(size: int) -> Image.Image:
    """A vine: a stem curving up and out, three leaves springing off it, a bud at the tip.

    Leaves alternate sides and are pushed clear of the stem along their own direction --
    centred on it they merge with it and the whole sprig prints as one blob at this size.
    """
    d = size * SS
    m = Image.new("L", (d, d), 0)
    g = ImageDraw.Draw(m)
    stroke = max(2, round(d * 0.030))
    cx, cy, r = d * 0.95, d * 0.95, d * 0.86          # the stem's circle, centred off the bottom-right
    a0, a1 = 186.0, 264.0                             # degrees along it: bottom-left up to top-right
    g.arc([cx - r, cy - r, cx + r, cy + r], a0, a1, fill=255, width=stroke)
    for i, (t, scale) in enumerate(((0.16, 0.30), (0.48, 0.32), (0.74, 0.26))):
        ang = math.radians(a0 + t * (a1 - a0))
        px, py = cx + r * math.cos(ang), cy + r * math.sin(ang)
        # The leaf points off the stem's direction of travel; screen y grows down, so the
        # image is rotated by -theta to end up pointing along (cos theta, sin theta).
        theta = math.degrees(ang) + 90 + (52 if i % 2 == 0 else -52)
        length = round(d * scale)
        leaf = _leaf(length, max(round(length * 0.38), 2), -theta)
        ox = math.cos(math.radians(theta)) * length * 0.45
        oy = math.sin(math.radians(theta)) * length * 0.45
        m.paste(255, (round(px + ox - leaf.width / 2), round(py + oy - leaf.height / 2)), leaf)
    tip = math.radians(a1)
    bud = round(d * 0.075)
    bx, by = cx + r * math.cos(tip), cy + r * math.sin(tip)
    g.ellipse([bx - bud, by - bud, bx + bud, by + bud], fill=255)
    return _hard(m.resize((size, size), Image.LANCZOS))


def _pentacle(size: int, stroke: int) -> Image.Image:
    d = size * SS
    w = max(2, stroke * SS)
    m = Image.new("L", (d, d), 0)
    g = ImageDraw.Draw(m)
    g.ellipse([w // 2, w // 2, d - 1 - w // 2, d - 1 - w // 2], outline=255, width=w)
    c, r = d / 2, d / 2 - w * 1.8
    pts = [(c + r * math.cos(math.radians(-90 + i * 72)),
            c + r * math.sin(math.radians(-90 + i * 72))) for i in range(5)]
    g.line([pts[i] for i in (0, 2, 4, 1, 3, 0)], fill=255, width=w, joint="curve")
    return _hard(m.resize((size, size), Image.LANCZOS))


def _star(size: int) -> Image.Image:
    """A four-point sparkle with concave sides."""
    d = size * SS
    m = Image.new("L", (d, d), 0)
    c, k = d / 2, d * 0.14
    ImageDraw.Draw(m).polygon([(c, 0), (c + k, c - k), (d - 1, c), (c + k, c + k),
                               (c, d - 1), (c - k, c + k), (0, c), (c - k, c - k)], fill=255)
    return _hard(m.resize((size, size), Image.LANCZOS))


def _woven(size: tuple[int, int], samples: list, k: float, s: "GrimoireSettings",
           stem: int, amp: float, leaf: int, leaf_step: float,
           ds: float, closed: bool = False) -> Image.Image:
    """A two-stem weave laid along a centre line, at SS resolution.

    ``samples`` is the centre line as ``(x, y, nx, ny, arc)`` with ``(nx, ny)`` the unit
    normal pointing outward. The stems ride +/- ``amp * sin(k * arc)`` off it, so they meet
    wherever the sine is zero, and each crossing takes the opposite parity from the last:
    over, under, over. The stem passing UNDER is cut with a white break rather than drawn
    beneath -- HEXCODE's floral rule is weave, don't layer, and a merge is a bug.

    Leaves ride the stems at ``leaf_step`` intervals, pointing away from the centre line and
    tilting alternately up and down the run. None is placed near a crossing: the break needs
    that paper, and a leaf in it reads as the blob the break exists to prevent.
    """
    def ride(strand: int, i: int) -> tuple[float, float]:
        x, y, nx, ny, arc = samples[i]
        d = (amp if strand == 0 else -amp) * math.sin(k * arc)
        return x + nx * d, y + ny * d

    strands, breaks = [], []
    for strand in (0, 1):
        img = Image.new("L", size, 0)
        pts = [ride(strand, i) for i in range(len(samples))]
        if closed:
            pts.append(pts[0])
        ImageDraw.Draw(img).line(pts, fill=255, width=stem, joint="curve")
        strands.append(img)
        breaks.append(Image.new("L", size, 0))

    radius = stem / 2 + s.braid_break * SS
    parity, previous = 0, math.sin(k * samples[0][4])
    for i in range(1, len(samples)):
        here = math.sin(k * samples[i][4])
        if (previous < 0) != (here < 0):                  # the stems cross here
            x, y = samples[i][0], samples[i][1]
            ImageDraw.Draw(breaks[parity % 2]).ellipse(
                [x - radius, y - radius, x + radius, y + radius], fill=255)
            parity += 1
        previous = here

    m = Image.new("L", size, 0)
    for strand in (0, 1):
        cut = strands[strand].copy()
        cut.paste(0, (0, 0), breaks[strand])
        m.paste(255, (0, 0), cut)

    total = samples[-1][4]
    for j in range(1, int(total // leaf_step) + 1):
        arc = j * leaf_step
        i = int(round(arc / ds))
        if i >= len(samples):
            break
        swing = math.sin(k * arc)
        if abs(swing) < 0.33:                             # too near a crossing to take a leaf
            continue
        x, y, nx, ny, _ = samples[i]
        tx, ty = -ny, nx                                  # unit tangent
        tilt = math.radians(34 if j % 2 == 0 else -34)
        for strand in (0, 1):
            d = (1 if strand == 0 else -1) * swing
            px, py = x + nx * amp * d, y + ny * amp * d
            out = 1.0 if d >= 0 else -1.0                 # away from the centre line
            dx = nx * out * math.cos(tilt) + tx * math.sin(tilt)
            dy = ny * out * math.cos(tilt) + ty * math.sin(tilt)
            theta = math.degrees(math.atan2(dy, dx))
            blade = _leaf(leaf, max(round(leaf * s.braid_leaf_aspect), 3), -theta)
            m.paste(255, (round(px + dx * leaf * 0.5 - blade.width / 2),
                          round(py + dy * leaf * 0.5 - blade.height / 2)), blade)
    return m


def _braid(width: int, length: int, s: "GrimoireSettings", bud: bool = False) -> Image.Image:
    """A woven vine running straight down a column of ``width``, root at the top."""
    w, length = max(width, 8), max(length, 8)
    big = (w * SS, length * SS)
    stem = max(2, s.braid_stem * SS)
    amp = max(w * s.braid_amp, 3) * SS
    leaf = max(round(w * s.braid_leaf), 6) * SS
    # Few wide slow curves, not many tight ones: the pitch fixes the half period and the run
    # takes as many as it has room for, never fewer than the two a single crossing needs.
    waves = max(2, round(length / max(w * s.braid_pitch, 8)))
    k = math.pi * waves / big[1]
    samples = [(big[0] / 2, float(y), 1.0, 0.0, float(y)) for y in range(0, big[1] + 1, SS)]
    m = _woven(big, samples, k, s, stem, amp, leaf, (math.pi / k) / 4, SS)
    if bud:
        r = round(big[0] * 0.06)
        x = big[0] / 2 + amp * math.sin(k * big[1])
        ImageDraw.Draw(m).ellipse([x - r, big[1] - 1 - 2 * r, x + r, big[1] - 1], fill=255)
    return _hard(m.resize((w, length), Image.LANCZOS))


def _perimeter(rect: tuple[int, int, int, int], radius: float, ds: float) -> list:
    """The centre line of a rounded rectangle, sampled evenly, with outward normals.

    The border is grown ONCE around the whole perimeter rather than as four runs meeting at
    the corners: four runs would have to overlap where they meet, and an overlap is exactly
    the merge the weave is built to avoid. Arc length is the parameter, so the corners are
    no different from the straight edges as far as the weave is concerned.
    """
    x0, y0, x1, y1 = rect
    r = max(min(radius, (x1 - x0) / 2, (y1 - y0) / 2), 0.0)
    path: list[tuple[float, float]] = []

    def corner(cx: float, cy: float, a0: float, a1: float) -> None:
        for i in range(1, 17):
            a = math.radians(a0 + (a1 - a0) * i / 16)
            path.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    path.append((x0 + r, y0))
    path.append((x1 - r, y0))                  # clockwise on screen: y grows down
    corner(x1 - r, y0 + r, -90, 0)
    path.append((x1, y1 - r))
    corner(x1 - r, y1 - r, 0, 90)
    path.append((x0 + r, y1))
    corner(x0 + r, y1 - r, 90, 180)
    path.append((x0, y0 + r))
    corner(x0 + r, y0 + r, 180, 270)

    segs = []
    for i in range(len(path)):
        ax, ay = path[i]
        bx, by = path[(i + 1) % len(path)]
        length = math.hypot(bx - ax, by - ay)
        if length > 1e-9:
            segs.append((ax, ay, (bx - ax) / length, (by - ay) / length, length))
    total = sum(seg[4] for seg in segs)

    samples, index, walked = [], 0, 0.0
    for j in range(max(int(total // ds), 16)):
        arc = j * ds
        while index < len(segs) - 1 and walked + segs[index][4] < arc:
            walked += segs[index][4]
            index += 1
        ax, ay, tx, ty, _ = segs[index]
        along = arc - walked
        samples.append((ax + tx * along, ay + ty * along, ty, -tx, arc))   # outward normal
    return samples


def _vine_border(size: tuple[int, int], rect: tuple[int, int, int, int],
                 band: int, s: "GrimoireSettings") -> Image.Image:
    """The whole border as one closed woven vine, at printer dots."""
    big = (size[0] * SS, size[1] * SS)
    stem = max(2, s.braid_stem * SS)
    amp = max(band * s.braid_amp, 3) * SS
    leaf = max(round(band * s.braid_leaf), 6) * SS
    ds = 2 * SS
    samples = _perimeter(tuple(v * SS for v in rect), s.border_radius * SS, ds)
    total = samples[-1][4] + ds
    # The wave has to close on itself: a whole number of periods around the loop, or the
    # weave meets its own start out of phase and the last crossing is a merge.
    periods = max(4, round(total / (2 * band * s.braid_pitch * SS)))
    k = 2 * math.pi * periods / total
    m = _woven(big, samples, k, s, stem, amp, leaf, (math.pi / k) / 4, ds, closed=True)
    return _hard(m.resize(size, Image.LANCZOS))


def _vine_size(win_w: int, win_left: int, foot_depth: float, want: int, clear: int) -> int:
    """The largest vine up to ``want`` that clears the arch, rooted ``foot_depth`` below the crown.

    A vine grows up and in from the paper edge, so its box is widest where it is DEEPEST --
    and _shoulder narrows going down. The foot is therefore the only constraint, and it does
    not move when the vine is scaled. 0 means there is no room worth drawing in.
    """
    size = min(want, int(win_left + _shoulder(win_w, foot_depth) - clear))
    return size if size >= 24 else 0


def _stamp(canvas: Image.Image, mask: Image.Image, xy: tuple[int, int]) -> None:
    """Ink ``mask`` onto the cover at ``xy``, clipped by the canvas as usual."""
    canvas.paste(0, (round(xy[0]), round(xy[1])), mask)


# -- the cover -----------------------------------------------------------------

def make_grimoire(photos: list, settings: GrimoireSettings | None = None,
                  when: datetime | None = None) -> Image.Image:
    """Compose the cover and return it as a mode "1" image ``settings.width`` wide."""
    s = settings or GRIMOIRE
    when = when or datetime.now()
    if s.band_fill not in BAND_FILLS:
        raise ValueError(f"unknown band_fill {s.band_fill!r}; choose from {', '.join(BAND_FILLS)}")
    if not photos:
        raise ValueError("the grimoire prints one photograph and none was given")

    rules = s.outer_rule + s.band + s.inner_rule
    frame_left, frame_right = s.margin, s.width - 1 - s.margin
    if s.frame:
        panel_left, panel_right = frame_left + rules + s.panel_pad, frame_right - rules - s.panel_pad
        win_left, win_right = panel_left + s.window_inset, panel_right - s.window_inset
        win_top = s.margin + rules + s.panel_pad
    elif s.vine_border:
        # The border IS the vine: it takes a band off every edge and the window sits inside it.
        inset = s.margin + s.border_width + s.panel_pad
        win_left, win_right = inset, s.width - 1 - inset
        panel_left, panel_right = win_left, win_right
        win_top = inset
    else:
        # No cover: the window sits on the paper itself, and the margin the frame used to
        # occupy becomes the run the edge vines grow through.
        win_left, win_right = s.edge_inset, s.width - 1 - s.edge_inset
        panel_left, panel_right = win_left, win_right
        win_top = s.top_pad
    win_w = win_right - win_left + 1
    win_h = round(win_w * s.window_aspect[1] / s.window_aspect[0])
    win_bottom = win_top + win_h

    fonts = SERIF_FONTS if s.serif else SANS_FONTS
    probe = ImageDraw.Draw(Image.new("L", (s.width, 8), 255))
    pad = s.plaque_pad + s.plaque_rule if s.plaque else 0
    room = s.plaque_width - 2 * pad
    lines = [(s.title, s.title_size, s.title_tracking, fonts),
             (s.date_format.format(date=when), s.date_size, 0, SANS_FONTS),
             (s.handle, s.handle_size, 0, SANS_FONTS)]
    set_lines = []
    for text, size, tracking, faces in lines:
        if not text:
            continue
        # Nothing here is worth setting below 12 dots: at 8 dots to the millimetre the tape
        # gives it back as a grey smear. _fit returns None rather than go there.
        font = _fit(probe, text, faces, size, room, 12, tracking)
        if font is None:
            raise ValueError(f"{text!r} does not fit the opening; widen plaque_width or shorten it")
        set_lines.append((text, font, tracking))

    # With title_row the first line leaves the block and is set in the ornament row instead,
    # so what stays below it is everything else -- the date on its own, in the open preset.
    row_line = set_lines[0] if (s.title_row and set_lines) else None
    block_lines = set_lines[1:] if row_line is not None else set_lines

    y = win_bottom + s.ornament_gap
    pent_top = y
    if s.pentacle:
        y += s.pentacle_size + s.ornament_gap
    row_top, row_h = y, 0
    if row_line is not None:
        row_h = max(sum(row_line[1].getmetrics()), s.title_vine_width)
        y += row_h + s.title_date_gap
    plaque_top = y
    plaque_h = (2 * pad
                + sum(sum(f.getmetrics()) for _, f, _ in block_lines)
                + s.line_gap * max(len(block_lines) - 1, 0))
    plaque_bottom = plaque_top + plaque_h
    if s.frame and s.plaque:
        # The cartouche straddles the foot, so the frame stops at its middle and the opening
        # is where the two rules break.
        frame_bottom = plaque_top + plaque_h // 2
        height = plaque_bottom + s.margin + 1
    elif s.frame:
        # No cartouche: the border runs all the way round and closes below the type.
        frame_bottom = plaque_bottom + s.panel_pad + rules
        height = frame_bottom + s.margin + 1
    else:
        frame_bottom = plaque_bottom
        foot = s.panel_pad + s.border_width + s.margin if s.vine_border else s.top_pad
        height = plaque_bottom + foot + 1

    canvas = Image.new("L", (s.width, height), 255)
    d = ImageDraw.Draw(canvas)

    def box(inset: int) -> list[int]:
        return [frame_left + inset, s.margin + inset, frame_right - inset, frame_bottom - inset]

    def radius(inset: int) -> int:
        return max(s.corner_radius - inset, 2)

    # the band of tone, then the two rules over its edges
    if s.frame:
        band = Image.new("L", (s.width, height), 0)
        bd = ImageDraw.Draw(band)
        bd.rounded_rectangle(box(s.outer_rule), radius(s.outer_rule), fill=255)
        bd.rounded_rectangle(box(s.outer_rule + s.band), radius(s.outer_rule + s.band), fill=0)
        canvas.paste(_screen((s.width, height), s.band_fill), (0, 0), band)
        d.rounded_rectangle(box(0), radius(0), outline=0, width=s.outer_rule)
        d.rounded_rectangle(box(s.outer_rule + s.band), radius(s.outer_rule + s.band),
                            outline=0, width=s.inner_rule)

    if s.vine_border:
        half = s.border_width // 2
        _stamp(canvas, _vine_border((s.width, height),
                                    (s.margin + half, s.margin + half,
                                     s.width - 1 - s.margin - half, height - 1 - s.margin - half),
                                    s.border_width, s), (0, 0))

    # the photograph, cut to the arch, and the arch drawn round it
    photo = photos[0]
    if not isinstance(photo, Image.Image):
        photo = Image.open(photo)
    tile = prepare_photo(photo, (win_w, win_h), s.dither).convert("L")
    shape = _arch((win_w, win_h), s.window_foot_radius)
    canvas.paste(255, (win_left, win_top), Image.new("L", (win_w, win_h), 255))   # clear the band behind it
    canvas.paste(tile, (win_left, win_top), shape)
    _stamp(canvas, _arch_ring((win_w, win_h), s.window_foot_radius, s.window_rule),
           (win_left, win_top))
    if s.window_keyline:
        t = s.window_keyline_gap + s.window_keyline
        _stamp(canvas, _arch_ring((win_w + 2 * t, win_h + 2 * t), s.window_foot_radius + t,
                                  s.window_keyline), (win_left - t, win_top - t))

    # The shoulders: the paper the arch's curve leaves either side of its crown. A moon sits
    # there with a star under it, each only as far down as _shoulder still has room for it --
    # the free width runs out fast, which is why the sprigs are at the foot and not here.
    clear = s.window_keyline_gap + s.window_keyline + 3       # the keyline runs outside the arch

    def shoulder_fits(top: int, size: int) -> bool:
        return _shoulder(win_w, top + size) - clear >= size

    if s.moons and shoulder_fits(6, s.moon_size):
        moon = _crescent(s.moon_size)
        _stamp(canvas, moon, (win_left + 3, win_top + 6))
        _stamp(canvas, moon.transpose(Image.FLIP_LEFT_RIGHT),
               (win_right - s.moon_size - 2, win_top + 6))
    star_top = 6 + (s.moon_size + 8 if s.moons else 0)
    if s.stars and shoulder_fits(star_top, s.star_size):
        star = _star(s.star_size)
        _stamp(canvas, star, (win_left + 6, win_top + star_top))
        _stamp(canvas, star, (win_right - s.star_size - 5, win_top + star_top))

    # Vines rooted off both edges, growing up and in alongside the window.
    if s.edge_vines and s.vine_weave:
        # One woven run down each margin instead of four separate sprigs: seeded off the top
        # edge and cut by the foot of the window, so both ends land on something rather than
        # tapering out in the middle of the paper.
        column = int(win_left - clear)
        start = -s.edge_vine_bleed                 # seeded off the sheet: the top end is a cut edge
        if column >= 24:
            vine = _braid(column, win_bottom - start, s, bud=True)
            _stamp(canvas, vine, (-s.edge_vine_bleed, start))
            _stamp(canvas, vine.transpose(Image.FLIP_LEFT_RIGHT),
                   (s.width - column + s.edge_vine_bleed, start))
    elif s.edge_vines:
        for depth in s.edge_vine_depths:
            foot = win_top + depth * win_h
            size = _vine_size(win_w, win_left, foot - win_top, s.edge_vine_size, clear)
            if not size:
                continue
            vine = _sprig(size)
            top = round(foot) - size
            _stamp(canvas, vine, (-s.edge_vine_bleed, top))
            _stamp(canvas, vine.transpose(Image.FLIP_LEFT_RIGHT),
                   (s.width - size + s.edge_vine_bleed, top))

    # The foot: a sprig either side of the pentacle, the row centred on the tape.
    if s.pentacle or not s.title_row:
        sprig_size = min(s.sprig_size, (s.width // 2 - s.pentacle_size // 2 - 12) - panel_left)
        show_sprigs = s.sprigs and sprig_size >= 24
        row = (s.pentacle_size if s.pentacle else 0) + (2 * (sprig_size + 12) if show_sprigs else 0)
        x = (s.width - row) // 2
        if show_sprigs:
            sprig = _sprig(sprig_size)
            top = pent_top + (s.pentacle_size - sprig_size) // 2
            _stamp(canvas, sprig, (x, top))
            _stamp(canvas, sprig.transpose(Image.FLIP_LEFT_RIGHT), (x + row - sprig_size, top))
            x += sprig_size + 12
        if s.pentacle:
            _stamp(canvas, _pentacle(s.pentacle_size, s.pentacle_rule), (x, pent_top))

    # The title set between two vines, where the pentacle used to be: the vines are given
    # whatever the type does not need, so the copy decides how far they run.
    if row_line is not None:
        text, font, tracking = row_line
        text_w = _width(d, text, font, tracking)
        # The row is bounded by whatever is already holding the tape: the panel inside a frame
        # or a vine border, the paper edge when there is neither. Running to the paper edge
        # inside a frame would put the vines straight through its rules.
        held = s.frame or s.vine_border
        row_left = panel_left if held else s.title_row_margin
        row_right = panel_right if held else s.width - 1 - s.title_row_margin
        vine_run = (row_right - row_left + 1 - text_w - 2 * s.title_row_gap) // 2
        _draw(d, ((s.width - text_w) // 2, _centre_baseline(row_top, row_top + row_h, font)),
              text, font, 0, tracking)
        if s.sprigs and vine_run >= 28:
            # A single vine here, not a braid. A weave needs run to read -- over this length it
            # gets one crossing and prints as a knot of leaves. The long runs carry the weave.
            size = min(vine_run, row_h + 6)
            sprig = _sprig(size)
            top = row_top + (row_h - size) // 2
            slack = (vine_run - size) // 2
            _stamp(canvas, sprig, (row_left + slack, top))
            _stamp(canvas, sprig.transpose(Image.FLIP_LEFT_RIGHT),
                   (row_right - vine_run + 1 + slack, top))

    # the opening at the foot: the cartouche is drawn in paper, so it cuts the frame
    pl_left = (s.width - s.plaque_width) // 2
    if s.plaque:
        d.rounded_rectangle([pl_left, plaque_top, pl_left + s.plaque_width - 1, plaque_bottom],
                            radius=s.plaque_radius, fill=255, outline=0, width=s.plaque_rule)
    y = plaque_top + pad
    for text, font, tracking in block_lines:
        line_h = sum(font.getmetrics())
        _draw(d, ((s.width - _width(d, text, font, tracking)) // 2,
                  _centre_baseline(y, y + line_h, font)), text, font, 0, tracking)
        y += line_h + s.line_gap

    canvas.info["first_photo_bottom"] = win_bottom
    return canvas.convert("1", dither=Image.NONE)


def paper_length_mm(image: Image.Image) -> float:
    """How much tape one cover takes, at the printer's 8 dots to the millimetre."""
    return image.height / 8.0


# -- presets and the saved copy ------------------------------------------------

GRIMOIRE = replace(GrimoireSettings(), plaque=False)   # two rules and a band, closed all the way round

# The open cover: no rules, no band, no cartouche outline, and no pentacle -- the title stands
# where that was, between two woven vines, with the date alone underneath. The arch is held by
# ornament rather than by a frame: a woven run down each margin, seeded off the top edge and
# cut by the foot of the window. Lines get more air without a box around them (line_gap).
# B keeps the airier gap under the title it was reviewed with; A and C are tighter by request.
OPEN = replace(GRIMOIRE, frame=False, plaque=False, edge_vines=True, vine_weave=True,
               line_gap=8, title_date_gap=18)

# The vine border: no rules anywhere, the frame is the vine itself and it is grown once around
# the whole perimeter (see _perimeter). The title row takes no vines of its own -- the border is
# already carrying that job, and a second run beside the title reads as clutter rather than growth.
VINE = replace(GRIMOIRE, frame=False, plaque=False, vine_border=True, sprigs=False,
               moons=True, stars=True, line_gap=8, title_date_gap=2, ornament_gap=10)

PRESETS = {"grimoire": GRIMOIRE, "open": OPEN, "vine": VINE}

_TUPLE_FIELDS = {"window_aspect"}


def to_dict(settings: GrimoireSettings) -> dict:
    return asdict(settings)


def from_dict(data: dict, base: GrimoireSettings | None = None) -> GrimoireSettings:
    """Settings from a JSON-style dict; unknown keys are ignored, missing ones come from ``base``."""
    s = replace(base or GRIMOIRE)
    known = {f.name for f in fields(GrimoireSettings)}
    for key, value in data.items():
        if key not in known:
            continue
        if key in _TUPLE_FIELDS and value is not None:
            value = tuple(value)
        setattr(s, key, value)
    return s
