"""Receipt-style photostrip: the "Receipt" direction from assets/themes/photostrip.

The strip is drawn as a shop receipt on the register tape itself: a letter-spaced
shop header, a dashed rule, DATE/TIME and REG/TXN rows, a QTY ITEM PRICE table
where every line item is one photo, SUBTOTAL / DISCOUNT / TAX / TOTAL, a real
Code 39 barcode, and a thank-you footer with fine print. Everything is composed
at the printer's native 384-dot width in a monospace face, so it prints 1:1.

Two presets ship: ``RECEIPT`` (the generic photobooth register tape, photos as
line items) and ``BIRTHDAY`` (the "Classic Register" birthday variant: one big
photo above sentimental line items). Every string is a field on
``ReceiptSettings`` so the copy can be changed without touching the layout.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .printer import WIDTH_PX
from .strip import prepare_photo

MONO_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",            # macOS, for previews on the Mac
    "/System/Library/Fonts/Courier.ttc",
]
MONO_BOLD_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
    # macOS previews: "#1" is the Bold face inside the collection. Without the index PIL
    # loads face 0 (Regular) and every bold row previews un-bold while the Pi prints it bold.
    "/System/Library/Fonts/Menlo.ttc#1",
    "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
]

# Fonts tried, in order, for a character the mono face does not have (decorative
# symbols, other scripts). A character no font can draw is left out rather than
# printed as a tofu box.
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/droid/DroidSansFallback.ttf",
    "/System/Library/Fonts/Apple Symbols.ttf",                    # macOS previews
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]

# Code 39: 9 elements per character (bar, space, bar, ...), 1 = wide.
CODE39 = {
    "0": "000110100", "1": "100100001", "2": "001100001", "3": "101100000",
    "4": "000110001", "5": "100110000", "6": "001110000", "7": "000100101",
    "8": "100100100", "9": "001100100", "A": "100001001", "B": "001001001",
    "C": "101001000", "D": "000011001", "E": "100011000", "F": "001011000",
    "G": "000001101", "H": "100001100", "I": "001001100", "J": "000011100",
    "K": "100000011", "L": "001000011", "M": "101000010", "N": "000010011",
    "O": "100010010", "P": "001010010", "Q": "000000111", "R": "100000110",
    "S": "001000110", "T": "000010110", "U": "110000001", "V": "011000001",
    "W": "111000000", "X": "010010001", "Y": "110010000", "Z": "011010000",
    "-": "010000101", ".": "110000100", " ": "011000100", "$": "010101000",
    "/": "010100010", "+": "010001010", "%": "000101010", "*": "010010100",
}


# Places a separator rule can appear, top to bottom. ``rules`` lists the ones that are drawn.
RULE_SLOTS = {
    "header": "below the shop header",
    "photos": "below stacked photos (when photos are not line items)",
    "table_top": "above the QTY ITEM PRICE header",
    "table_bottom": "below the QTY ITEM PRICE header",
    "items": "below the line items, above the totals",
    "total": "above the big TOTAL line",
    "footer": "above the thank-you footer",
    "barcode": "above the barcode",
}
RULE_SLOTS_DEFAULT = ["header", "photos", "table_top", "table_bottom", "items", "total"]
RULE_STYLES = ("dashed", "dotted", "solid", "double", "text")


@dataclass
class ReceiptSettings:
    width: int = WIDTH_PX
    margin: int = 12                     # white border left/right; also top/bottom padding
    photo_aspect: tuple[int, int] = (4, 3)
    photo_border: int = 2                # black frame around each photo
    dither: bool = True
    torn_edges: bool = True              # zigzag "torn tape" line at the top and bottom

    # header
    shop: str = "PHOTO BOOTH CO."
    shop_tracking: int = 4               # extra dots between header letters
    address: list[str] = field(default_factory=lambda: ["123 CAPTURE LANE, UNIT 4", "EST. 2026"])

    # transaction meta: pairs of (left, right); {date}, {time}, {txn}, {n} are filled in
    meta: list[tuple[str, str]] = field(default_factory=lambda: [
        ("DATE {date:%m/%d/%Y}", "TIME {date:%-I:%M %p}"),
        ("REG 02", "TXN #{txn:04d}"),
    ])

    # item table
    table_header: tuple[str, str] | None = ("QTY ITEM", "PRICE")
    photos_as_items: bool = True         # each photo is a line item; else photos stack above the items
    photo_items: list[str] = field(default_factory=lambda: [
        "GOOD TIMES", "YOUR BEST SIDE", "THE GOOFY ONE", "ONE MORE TAKE",
    ])
    photo_item_qty: str = "1"
    photo_item_price: str = "INCL"
    items: list[tuple[str, str]] = field(default_factory=list)   # extra text-only line items

    # totals
    totals: list[tuple[str, str]] = field(default_factory=lambda: [
        ("SUBTOTAL", "{n} FRAMES"), ("DISCOUNT (SMILE)", "-0.00"), ("TAX", "0.00"),
    ])
    total: tuple[str, str] | None = ("TOTAL", "PRICELESS")

    # barcode + footer
    barcode: str | None = "{date:%y%m%d}{txn:04d}"   # Code 39 payload; None for no barcode
    barcode_caption: str | None = "*{txn:04d} {date:%Y %m%d}*"
    footer: list[str] = field(default_factory=lambda: ["THANK YOU FOR VISITING", "*** PLEASE COME AGAIN ***"])
    fine_print: str | None = "NO REFUNDS ON CANDID MOMENTS"

    # rules: where the separator lines go and what they look like
    rules: list[str] = field(default_factory=lambda: list(RULE_SLOTS_DEFAULT))
    rule_style: str = "dashed"           # dashed | dotted | solid | double | text
    rule_thickness: int = 2              # dots
    rule_dash: int = 6                   # dash length (dashed) in dots
    rule_gap: int = 4                    # gap between dashes/dots, or between the two lines of a double rule
    rule_pad: int = 8                    # white space above and below a rule
    rule_text: str = "- "                # repeated across the tape when rule_style == "text"

    # type sizes in dots (8 dots = 1 mm)
    size_shop: int = 30
    size_small: int = 16
    size_body: int = 18
    size_total: int = 26
    size_fine: int = 14


RECEIPT = ReceiptSettings()

BIRTHDAY = ReceiptSettings(
    shop="THE BIRTHDAY SHOP",
    shop_tracking=2,
    size_shop=24,
    address=[],
    meta=[("Guest:", "Best Friend"), ("Date:", "{date:%B %-d, %Y}")],
    table_header=None,
    photos_as_items=False,
    items=[("1  Amazing Friendship", "$0.00"), ("99 Great Memories", "$0.00"), ("   Birthday Wishes", "PRICELESS")],
    totals=[("SUBTOTAL", "LOVE"), ("TAX", "100%")],
    total=("TOTAL", "ONE HUG"),
    footer=["Thank you for being here!", "", "Order #{txn:04d}", "NO RETURNS * FRIENDS FOREVER"],
    fine_print=None,
    barcode="{txn:04d}",
    barcode_caption="* birthday barcode *",
)

# A Maker Faire tape: one photo, four
# line items, the faire line as the header block, the Pi credit at the foot, and
# rules only above SUBTOTAL and TOTAL. 96 mm a print, ~104 to a 10 m roll.
MAYHEM = ReceiptSettings(
    shop="MAKER MAYHEM",
    shop_tracking=6,
    size_shop=32,
    address=["MAKER FAIRE · {date:%m/%d/%Y}"],
    meta=[],
    photos_as_items=False,
    photo_items=[],
    table_header=("QTY ITEM", ""),
    items=[("1  THINK IT", ""), ("1  BUILD IT", ""),
           ("1  BREAK IT", ""), ("1  MAKE IT BETTER", "")],
    totals=[("SUBTOTAL", "PASSION"), ("SERVICE (INCL)", "WONDER & WHIMSY")],
    total=("TOTAL", "MAGIC"),
    footer=[],
    barcode="{date:%y%m%d}{txn:04d}",
    barcode_caption="PRINTED ON A RASPBERRY PI 5",
    fine_print=None,
    rule_style="dashed",
    rules=["items", "total"],
)

PRESETS = {"receipt": RECEIPT, "birthday": BIRTHDAY, "mayhem": MAYHEM}

# Themes that always print their built-in copy: a saved ~/photobooth/receipt.json is ignored for
# these, both at start-up and on the per-session reload. "mayhem" is a signed-off faire design, and
# a Receipt Studio save (or a stale pushed copy) silently replacing it at the booth is the failure
# this prevents. Receipt Studio still edits "receipt"/"birthday" as before.
PINNED = ("mayhem",)


_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_glyph_cache: dict[tuple[str, int, str], bool] = {}


def _load(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    key = (path, size)
    if key not in _font_cache:
        name, _, index = path.partition("#")      # "Menlo.ttc#1" picks a face inside a collection
        try:
            _font_cache[key] = (ImageFont.truetype(name, size, index=int(index or 0))
                                if Path(name).exists() else None)
        except (OSError, ValueError):
            _font_cache[key] = None
    return _font_cache[key]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in MONO_BOLD_FONTS if bold else MONO_FONTS:
        font = _load(path, size)
        if font is not None:
            return font
    return ImageFont.load_default()


def _glyph_image(font, ch: str) -> tuple[tuple[int, int], bytes]:
    left, top, right, bottom = font.getbbox(ch)
    img = Image.new("L", (max(right - left, 1) + 4, max(bottom - top, 1) + 4), 0)
    ImageDraw.Draw(img).text((2 - left, 2 - top), ch, font=font, fill=255)
    return img.size, img.tobytes()


def has_glyph(font, ch: str) -> bool:
    """True if ``font`` draws ``ch`` with a real glyph rather than the missing-glyph box."""
    if not isinstance(font, ImageFont.FreeTypeFont):
        return True
    key = (font.path, font.size, ch)
    if key not in _glyph_cache:
        # FreeType draws the same .notdef box for every missing character; U+10FFFF is in no font.
        _glyph_cache[key] = _glyph_image(font, ch) != _glyph_image(font, "\U0010ffff")
    return _glyph_cache[key]


def font_for(ch: str, font):
    """The font to draw ``ch`` with: ``font`` itself, a fallback that has the glyph, or None (unprintable)."""
    if ch.isspace():
        return font
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):      # stray combining marks / format chars print as boxes
        return None
    if has_glyph(font, ch):
        return font
    size = font.size if isinstance(font, ImageFont.FreeTypeFont) else 16
    for path in FALLBACK_FONTS:
        fb = _load(path, size)
        if fb is not None and has_glyph(fb, ch):
            return fb
    return None


def printable(text: str, font) -> str:
    """``text`` without the characters no available font can draw."""
    return "".join(ch for ch in text if font_for(ch, font) is not None)


class _Tape:
    """Draws receipt rows top to bottom on a canvas that grows as needed."""

    def __init__(self, width: int, margin: int) -> None:
        self.width = width
        self.margin = margin
        self.inner = width - 2 * margin
        self.img = Image.new("L", (width, 4000), 255)
        self.d = ImageDraw.Draw(self.img)
        self.y = 0
        self.first_photo_bottom: int | None = None    # landmark for the booth's preview crop

    def _ensure(self, rows: int) -> None:
        if self.y + rows > self.img.height:
            bigger = Image.new("L", (self.width, self.img.height + max(rows, 2000)), 255)
            bigger.paste(self.img, (0, 0))
            self.img, self.d = bigger, ImageDraw.Draw(bigger)

    def space(self, rows: int) -> None:
        self._ensure(rows)
        self.y += rows

    def _line_height(self, font) -> int:
        asc, desc = font.getmetrics()
        return asc + desc

    def _runs(self, text: str, font) -> list[tuple[str, object]]:
        """Split text into runs drawn with one font each; characters no font can draw are dropped."""
        runs: list[tuple[str, object]] = []
        for ch in text:
            f = font_for(ch, font)
            if f is None:
                continue
            if runs and runs[-1][1] is f:
                runs[-1] = (runs[-1][0] + ch, f)
            else:
                runs.append((ch, f))
        return runs

    def text_width(self, text: str, font, tracking: int = 0) -> int:
        runs = self._runs(text, font)
        if tracking:
            n = sum(len(t) for t, _ in runs)
            return sum(int(self.d.textlength(ch, font=f)) for t, f in runs for ch in t) + tracking * max(n - 1, 0)
        return sum(int(self.d.textlength(t, font=f)) for t, f in runs)

    def _draw_text(self, x: int, text: str, font, tracking: int = 0) -> None:
        baseline = self.y + font.getmetrics()[0]          # fallback fonts share the mono face's baseline
        for t, f in self._runs(text, font):
            if not tracking:
                self.d.text((x, baseline), t, fill=0, font=f, anchor="ls")
                x += int(self.d.textlength(t, font=f))
                continue
            for ch in t:
                self.d.text((x, baseline), ch, fill=0, font=f, anchor="ls")
                x += int(self.d.textlength(ch, font=f)) + tracking

    def centered(self, text: str, font, tracking: int = 0, gap: int = 2) -> None:
        h = self._line_height(font)
        self._ensure(h + gap)
        w = self.text_width(text, font, tracking)
        self._draw_text(self.margin + (self.inner - w) // 2, text, font, tracking)
        self.y += h + gap

    def headline(self, text: str, size: int, bold: bool, tracking: int, min_size: int, gap: int = 4) -> None:
        """Centered text that shrinks (then wraps onto two lines) until it fits the tape."""
        font = _font(size, bold)
        while (self.text_width(text, font, tracking) > self.inner) and (size > min_size or tracking > 0):
            if tracking > 0:
                tracking -= 1
            else:
                size -= 1
                font = _font(size, bold)
        if self.text_width(text, font, tracking) <= self.inner or " " not in text:
            self.centered(text, font, tracking, gap)
            return
        words = text.split()
        best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
        for line in (" ".join(words[:best]), " ".join(words[best:])):
            self.centered(line, font, tracking, gap)

    def row(self, left: str, right: str, font, gap: int = 2) -> None:
        """Left and right-aligned text on one line; the left part is clipped with '..' if they overlap."""
        h = self._line_height(font)
        self._ensure(h + gap)
        rw = self.text_width(right, font)
        room = self.inner - rw - self.text_width(" ", font)
        while left and self.text_width(left, font) > room:
            left = left[:-1]
        self._draw_text(self.margin, left, font)
        self._draw_text(self.margin + self.inner - rw, right, font)
        self.y += h + gap

    def rule(self, style: str = "dashed", thickness: int = 2, dash: int = 6, gap: int = 4, pad: int = 8,
             text: str = "- ", font=None) -> None:
        """A separator line: dashed, dotted, solid, double, or a text pattern repeated across the tape."""
        thickness, dash, gap, pad = max(1, thickness), max(1, dash), max(0, gap), max(0, pad)
        right = self.margin + self.inner
        if style == "text":
            font = font or _font(16)
            unit = text or "-"
            w = max(1, self.text_width(unit, font))
            line = (unit * max(1, self.inner // w)).rstrip()
            while line and self.text_width(line, font) > self.inner:
                line = line[:-1]
            self.space(pad)
            self.centered(line, font, gap=pad)
            return
        lines = 2 if style == "double" else 1
        self._ensure(thickness * lines + gap * (lines - 1) + 2 * pad)
        y0 = self.y + pad
        for k in range(lines):
            yy = y0 + k * (thickness + gap)
            if style in ("solid", "double"):
                self.d.rectangle([(self.margin, yy), (right - 1, yy + thickness - 1)], fill=0)
            else:
                step = thickness if style == "dotted" else dash
                x = self.margin
                while x < right:
                    x1 = min(x + step, right) - 1
                    self.d.rectangle([(x, yy), (x1, yy + thickness - 1)], fill=0)
                    x += step + gap
        self.y += thickness * lines + gap * (lines - 1) + 2 * pad

    def zigzag(self, tooth: int = 12, height: int = 7, pad: int = 4) -> None:
        self._ensure(height + 2 * pad + 1)
        y0 = self.y + pad
        pts = []
        x = self.margin
        up = True
        while x <= self.margin + self.inner:
            pts.append((x, y0 if up else y0 + height))
            x += tooth
            up = not up
        self.d.line(pts, fill=0, width=2)
        self.y += height + 2 * pad + 1

    def photo(self, img: Image.Image, aspect: tuple[int, int], border: int, dither: bool, gap: int = 6) -> None:
        w = self.inner
        h = round(w * aspect[1] / aspect[0])
        self._ensure(h + gap)
        tile = prepare_photo(img, (w - 2 * border, h - 2 * border), dither).convert("L")
        if border:
            self.d.rectangle([(self.margin, self.y), (self.margin + w - 1, self.y + h - 1)], fill=0)
        self.img.paste(tile, (self.margin + border, self.y + border))
        if self.first_photo_bottom is None:
            self.first_photo_bottom = self.y + h
        self.y += h + gap

    def barcode(self, payload: str, height: int = 44, narrow: int = 2, wide: int = 5, gap: int = 4) -> None:
        payload = "".join(ch for ch in payload.upper() if ch in CODE39 and ch != "*")
        if not payload:
            return
        code = f"*{payload}*"
        pattern: list[tuple[bool, int]] = []              # (is_bar, width)
        for i, ch in enumerate(code):
            for j, bit in enumerate(CODE39[ch]):
                pattern.append((j % 2 == 0, wide if bit == "1" else narrow))
            if i < len(code) - 1:
                pattern.append((False, narrow))
        total = sum(w for _, w in pattern)
        if total > self.inner:                              # shrink the wide bars until it fits
            wide_n = sum(1 for _, w in pattern if w == wide)
            wide = max(narrow + 1, (self.inner - (total - wide_n * wide)) // max(wide_n, 1))
            pattern = [(b, wide if w != narrow else narrow) for b, w in pattern]
            total = sum(w for _, w in pattern)
        self._ensure(height + gap)
        x = self.margin + (self.inner - total) // 2
        for is_bar, w in pattern:
            if is_bar:
                self.d.rectangle([(x, self.y), (x + w - 1, self.y + height - 1)], fill=0)
            x += w
        self.y += height + gap

    def finish(self) -> Image.Image:
        out = self.img.crop((0, 0, self.width, self.y))
        if self.first_photo_bottom is not None:
            out.info["first_photo_bottom"] = self.first_photo_bottom
        return out


def make_receipt(photos: list[Image.Image | str | Path], settings: ReceiptSettings | None = None,
                 when: datetime | None = None, number: int | None = None) -> Image.Image:
    """Compose the receipt and return it as a mode "1" image ``settings.width`` wide."""
    s = settings or RECEIPT
    when = when or datetime.now()
    txn = number if number is not None else int(when.strftime("%H%M"))
    n = len(photos)
    fmt = dict(date=when, txn=txn, n=n)

    def f(text: str) -> str:
        return text.format(**fmt)

    small, body = _font(s.size_small), _font(s.size_body)
    body_bold, total_font = _font(s.size_body, bold=True), _font(s.size_total, bold=True)
    fine = _font(s.size_fine)

    t = _Tape(s.width, s.margin)

    def rule(slot: str) -> None:
        if slot in s.rules:
            t.rule(s.rule_style, s.rule_thickness, s.rule_dash, s.rule_gap, s.rule_pad, s.rule_text, small)

    t.space(s.margin)
    if s.torn_edges:
        t.zigzag()
        t.space(8)

    # shop header
    t.headline(f(s.shop), s.size_shop, True, s.shop_tracking, min_size=s.size_body)
    for line in s.address:
        t.centered(f(line), small, tracking=1, gap=0)
    rule("header")

    # transaction meta
    for left, right in s.meta:
        t.row(f(left), f(right), body)

    def photo_rows() -> None:
        for i, photo in enumerate(photos):
            if not isinstance(photo, Image.Image):
                photo = Image.open(photo)
            if s.photos_as_items:
                name = s.photo_items[i % len(s.photo_items)] if s.photo_items else f"PHOTO {i + 1}"
                t.row(f"{s.photo_item_qty}  {f(name)}", f(s.photo_item_price), body)
                t.space(4)
            t.photo(photo, s.photo_aspect, s.photo_border, s.dither, gap=14 if i < n - 1 else 4)

    if not s.photos_as_items:
        t.space(10)
        photo_rows()
        rule("photos")
    if s.table_header:
        rule("table_top")
        t.row(f(s.table_header[0]), f(s.table_header[1]), body_bold)
        rule("table_bottom")
        t.space(2)
    if s.photos_as_items:
        photo_rows()
    for left, right in s.items:
        t.row(f(left), f(right), body)
    rule("items")

    # totals
    for left, right in s.totals:
        t.row(f(left), f(right), body)
    if s.total:
        rule("total")
        t.row(f(s.total[0]), f(s.total[1]), total_font, gap=4)

    # footer
    t.space(10)
    rule("footer")
    for line in s.footer:
        if line:
            t.centered(f(line), body_bold if line == s.footer[0] else small, tracking=1)
        else:
            t.space(6)
    if s.barcode:
        t.space(10)
        rule("barcode")
        t.barcode(f(s.barcode))
        if s.barcode_caption:
            t.centered(f(s.barcode_caption), fine, tracking=2)
    if s.fine_print:
        t.space(8)
        t.centered(f(s.fine_print), fine, tracking=1)
    if s.torn_edges:
        t.space(6)
        t.zigzag()
    t.space(s.margin)
    return t.finish().convert("1", dither=Image.NONE)


def preset(name: str, **overrides) -> ReceiptSettings:
    """A copy of the named preset with field overrides, e.g. preset("receipt", shop="MY BOOTH")."""
    return replace(PRESETS[name], **{k: v for k, v in overrides.items() if v is not None})


# -- saved copy (written by tools/receipt_studio.py, read by the booth app) ---

CONFIG_PATH = Path.home() / "photobooth" / "receipt.json"
_TUPLE_FIELDS = {"photo_aspect", "table_header", "total"}
_PAIR_LIST_FIELDS = {"meta", "items", "totals"}


def to_dict(settings: ReceiptSettings) -> dict:
    return asdict(settings)


def from_dict(data: dict, base: ReceiptSettings | None = None) -> ReceiptSettings:
    """Settings from a JSON-style dict; unknown keys are ignored, missing ones come from ``base``."""
    s = replace(base or RECEIPT)
    known = {f.name for f in fields(ReceiptSettings)}
    for key, value in data.items():
        if key not in known:
            continue
        if key in _TUPLE_FIELDS and value is not None:
            value = tuple(value)
        elif key in _PAIR_LIST_FIELDS:
            value = [tuple(pair) for pair in value]
        setattr(s, key, value)
    return s


def first_photo_bottom(image: Image.Image, margin: int = 16) -> int:
    """The row just below the first photo, for a preview that shows the head of the tape.

    ``make_receipt`` records this while drawing, so it is exact whatever the photo looks like.
    Falls back to the full height for an image that carries no landmark.
    """
    y = image.info.get("first_photo_bottom")
    if not isinstance(y, int) or not 0 < y <= image.height:
        return image.height
    return min(image.height, y + margin)


CHOICES_PATH = Path.home() / "photobooth" / "receipt_choices.json"


def load_choices(path: Path = CHOICES_PATH, limit: int = 2) -> list[tuple[str, ReceiptSettings]]:
    """Recent receipts sent in from the Tape Bench, newest first, as ``(label, settings)``.

    Written by tools/receipt_sync.py. Anything malformed is skipped rather than raised: a bad
    row from the outside world must never stop the booth from printing.
    """
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = data.get("receipts") if isinstance(data, dict) else data
    out: list[tuple[str, ReceiptSettings]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        raw = row.get("settings")
        if not isinstance(raw, dict):
            continue
        try:
            settings = from_dict(raw)
        except (TypeError, ValueError):
            continue
        label = str(row.get("name") or raw.get("shop") or "Saved").strip()[:22] or "Saved"
        out.append((label, settings))
        if len(out) >= limit:
            break
    return out


def load_settings(path: Path = CONFIG_PATH) -> ReceiptSettings | None:
    """The copy saved by Receipt Studio, or None if nothing has been saved.

    Raises if the file holds another layout (the film strip saves to the same path and
    says so in a ``layout`` key). The layouts share no fields, so parsing one as the other
    would quietly return an unchanged default and the booth would print the wrong tape.
    ``booth.tape.load`` is the one that reads either.
    """
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    layout = data.get("layout") or "receipt"
    if layout != "receipt":
        raise ValueError(f"{path} holds a {layout} layout, not a receipt")
    return from_dict(data)


def save_settings(settings: ReceiptSettings, path: Path = CONFIG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_dict(settings), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path
