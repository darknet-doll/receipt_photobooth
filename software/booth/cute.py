"""Sticker Kawaii: the booth's on-screen look.

The palette, the fonts and the handful of drawing primitives every screen in
``booth/app.py`` is built from. The idea is vinyl stickers on cream paper: each
card and button is a rounded rectangle outlined in ink, sitting on a solid offset
shadow of the same ink, with sakura scattered in the margins.

Two rules hold the screens together:

* **One column.** On idle the camera card and the start button share a left and a
  right edge and the same corner radius, so the screen reads as a single object.
* **Red means broken.** Cancelling is a quiet cream sticker; ``ALERT`` is reserved
  for a camera or printer that has actually failed.

Fonts are resolved from a candidate list the way ``booth/receipt.py`` resolves its
mono face: Quicksand if the Pi has it (``sudo apt install fonts-quicksand``), then
Comfortaa, then DejaVu Sans Bold, which is always present. The macOS paths keep the
simulator looking like the booth.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import pygame

# -- palette -------------------------------------------------------------------

CREAM = (255, 246, 240)      # the ground
MILK = (250, 232, 225)       # card behind the camera window
INK = (74, 52, 66)           # outlines, shadows and body text
DIM = (150, 120, 138)        # secondary text, quiet button labels
HOT = (240, 98, 161)         # the primary action
PETAL = (253, 222, 232)      # scattered blossoms, inactive dots
LILAC = (179, 157, 219)      # a second blossom colour
MINT = (126, 207, 168)       # PRINT
MINT_DARK = (46, 130, 95)    # mint is too pale for text on cream
MINT_TRACK = (178, 224, 199)  # the unspent part of the countdown ring
WHITE = (255, 255, 255)
ALERT = (206, 74, 74)        # faults only, never "cancel"

SHADOW = 7                   # how far a sticker's shadow sits below it
EDGE = 4                     # outline width

# -- fonts ---------------------------------------------------------------------

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/quicksand/Quicksand-Bold.ttf",
    "/usr/share/fonts/truetype/quicksand/Quicksand-SemiBold.ttf",
    "/usr/share/fonts/opentype/quicksand/Quicksand-Bold.otf",
    "/usr/share/fonts/truetype/comfortaa/Comfortaa-Bold.ttf",
    "/usr/share/fonts/opentype/comfortaa/Comfortaa-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",       # always on the Pi
    "/System/Library/Fonts/SFNSRounded.ttf",                      # macOS, for the simulator
    "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf",
)

_font_cache: dict[int, pygame.font.Font] = {}
_font_path: str | None = None


def font_path() -> str | None:
    """The first candidate that exists, or None to fall back to pygame's default."""
    global _font_path
    if _font_path is None:
        _font_path = next((p for p in FONT_CANDIDATES if Path(p).exists()), "")
    return _font_path or None


def reset_fonts() -> None:
    """Drop cached faces. pygame.quit() frees the Font objects behind them, so anything
    that re-initialises pygame must call this first or rendering segfaults on a stale handle."""
    _font_cache.clear()


def font(size: int) -> pygame.font.Font:
    if size not in _font_cache:
        path = font_path()
        _font_cache[size] = pygame.font.Font(path, size) if path else pygame.font.SysFont(None, size)
    return _font_cache[size]


def font_name() -> str:
    """For the --check banner: which face actually got loaded."""
    path = font_path()
    return Path(path).stem if path else "pygame default"


# -- primitives ----------------------------------------------------------------

def rr(surf: pygame.Surface, colour, rect: pygame.Rect, radius: int, width: int = 0) -> None:
    """Rounded rect. Note pygame.draw ignores alpha - use a SRCALPHA surface for that."""
    pygame.draw.rect(surf, colour, rect, width, border_radius=radius)


def sticker(surf: pygame.Surface, rect: pygame.Rect, fill, radius: int, label: str = "",
            label_font: pygame.font.Font | None = None, ink=WHITE, outline=INK) -> None:
    """A button: solid offset shadow, fill, thick outline, centred label."""
    rr(surf, outline, rect.move(0, SHADOW), radius)
    rr(surf, fill, rect, radius)
    rr(surf, outline, rect, radius, EDGE)
    if label:
        t = (label_font or font(34)).render(label, True, ink)
        surf.blit(t, t.get_rect(center=rect.center))


def heart(surf: pygame.Surface, centre: tuple[int, int], r: float, colour) -> None:
    """A small heart, drawn rather than typed - Quicksand has no U+2661 and would tofu."""
    cx, cy = centre
    lobe = max(1, int(r * 0.54))
    pygame.draw.circle(surf, colour, (int(cx - r * 0.46), int(cy - r * 0.28)), lobe)
    pygame.draw.circle(surf, colour, (int(cx + r * 0.46), int(cy - r * 0.28)), lobe)
    pygame.draw.polygon(surf, colour, [(cx - r, cy - r * 0.16), (cx + r, cy - r * 0.16), (cx, cy + r)])


def blossom(surf: pygame.Surface, centre: tuple[int, int], r: float, colour, rot: float = 0.0) -> None:
    """A five-petal sakura."""
    cx, cy = centre
    for i in range(5):
        a = rot + i * 2 * math.pi / 5
        pygame.draw.circle(surf, colour, (int(cx + math.cos(a) * r * 0.62), int(cy + math.sin(a) * r * 0.62)),
                           max(1, int(r * 0.52)))
    pygame.draw.circle(surf, colour, (int(cx), int(cy)), max(1, int(r * 0.42)))


def petal_field(width: int, height: int, keep_out: pygame.Rect | None = None,
                count: int = 9, seed: int = 7) -> list[tuple[int, int, float, float]]:
    """Deterministic blossom positions in the margins, clear of ``keep_out``."""
    rng = random.Random(seed)
    out: list[tuple[int, int, float, float]] = []
    for _ in range(count * 40):
        if len(out) >= count:
            break
        x = int(rng.uniform(0.03, 0.97) * width)
        y = int(rng.uniform(0.03, 0.97) * height)
        r = rng.uniform(0.34, 1.0) * 17
        if keep_out is not None and keep_out.inflate(30, 30).collidepoint(x, y):
            continue
        if any(abs(x - px) < 64 and abs(y - py) < 64 for px, py, _, _ in out):
            continue
        out.append((x, y, r, rng.uniform(0, math.pi)))
    return out


def scatter(surf: pygame.Surface, field, colour=PETAL, accent=LILAC) -> None:
    for i, (x, y, r, rot) in enumerate(field):
        blossom(surf, (x, y), r, accent if i % 5 == 4 else colour, rot)


def mask_round(src: pygame.Surface, size: tuple[int, int], radius: int) -> pygame.Surface:
    """``src`` clipped to a rounded rectangle of ``size``."""
    mask = pygame.Surface(size, pygame.SRCALPHA)
    rr(mask, (255, 255, 255, 255), mask.get_rect(), radius)
    out = pygame.Surface(size, pygame.SRCALPHA)
    out.blit(src, (0, 0))
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return out


def fit_cover(surf: pygame.Surface, size: tuple[int, int], smooth: bool = False) -> pygame.Surface:
    """Scale ``surf`` to cover ``size`` and crop to it, biased slightly above centre."""
    w, h = size
    sw, sh = surf.get_size()
    k = max(w / sw, h / sh)
    scaled = (pygame.transform.smoothscale if smooth else pygame.transform.scale)(
        surf, (max(1, round(sw * k)), max(1, round(sh * k))))
    x = (scaled.get_width() - w) // 2
    y = int((scaled.get_height() - h) * 0.40)
    return scaled.subsurface(pygame.Rect(x, y, w, h)).copy()


def ring(surf: pygame.Surface, centre: tuple[int, int], radius: int, fraction: float,
         track, colour, width: int = 7) -> None:
    """A progress ring that drains clockwise from twelve o'clock."""
    pygame.draw.circle(surf, track, centre, radius, width)
    fraction = max(0.0, min(1.0, fraction))
    if fraction <= 0:
        return
    steps = max(2, int(72 * fraction))
    pts = [(centre[0] + math.cos(-math.pi / 2 + 2 * math.pi * fraction * i / steps) * radius,
            centre[1] + math.sin(-math.pi / 2 + 2 * math.pi * fraction * i / steps) * radius)
           for i in range(steps + 1)]
    pygame.draw.lines(surf, colour, False, pts, width)


def paper(surf: pygame.Surface, rect: pygame.Rect) -> None:
    """The white card the receipt sits on: a soft drop shadow, then white."""
    pad = rect.inflate(16, 16)
    shade = pygame.Surface((pad.w, pad.h), pygame.SRCALPHA)
    rr(shade, (*INK, 44), shade.get_rect(), 10)
    surf.blit(shade, (pad.x, pad.y + SHADOW))
    rr(surf, WHITE, pad, 10)


def text(surf: pygame.Surface, s: str, f: pygame.font.Font, centre: tuple[int, int], colour=INK) -> pygame.Rect:
    t = f.render(s, True, colour)
    rect = t.get_rect(center=centre)
    surf.blit(t, rect)
    return rect
