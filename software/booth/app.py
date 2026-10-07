"""The photobooth loop: preview -> tap -> countdown -> photos -> receipt -> confirm -> print -> reset.

A small pygame state machine drawn fullscreen on the touchscreen, in landscape
(800x480) or portrait (480x800, ``booth.sh screen portrait``): the layout is
chosen from the screen shape at start-up.

    IDLE       mirrored live preview with a big TAP TO START button
    COUNTDOWN  3-2-1 over the preview, then a white flash and a full-res still,
               repeated for each photo. A CANCEL button aborts the session.
    CONFIRM    the finished receipt on screen with PRINT and CANCEL buttons;
               prints automatically after ``confirm_seconds`` if nobody taps
    REVIEW     the receipt on screen while it prints in a background thread
    DONE       "take your receipt" for a moment, then back to IDLE
    LOCKED     carry-lock, for the bag at a fair: a black screen, input ignored except a
               long press, which shows a "hold to unlock" ring. Armed from IDLE by holding
               the exit corner for ``carry_lock_hold`` seconds (a quick tap there still
               quits, as before) or by ``booth.sh lock`` / the "Carry Lock" desktop icon.

Stills go to ~/photobooth/captures/<session>/, the composed receipt next to them.
Printing failures never stop the loop: the image is still saved and the error is
shown on screen. Cancelling keeps whatever was already saved but prints nothing.

Themes: "receipt" (default, the register-tape design with each photo as a line
item), "birthday" (single-photo Classic Register variant), "mayhem" (the
single-photo Maker Faire tape; it ignores a saved receipt.json, see receipt.PINNED),
"classic" (the plain titled strip from booth/strip.py), "pick" (two photos on
the Maker Faire tape; the guest chooses which photo prints), "grimoire" (two
photos on the framed spellbook cover; the guest chooses which one fills its window),
"duet" (what the desktop icon runs: three photos, then the guest chooses between the
newest receipt sent in from the Tape Bench and the saved film strip).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

if sys.platform.startswith("linux"):            # the Pi desktop is Wayland; leave macOS/Windows alone
    os.environ.setdefault("SDL_VIDEODRIVER", "wayland")

import pygame  # noqa: E402

from . import cute
from .camera import BoothCamera, CameraSettings, CameraTimeout
from .led import BoothLed, LedSettings
from .printer import BoothPrinter, PrinterSettings
from . import tape
from .receipt import (CONFIG_PATH, PINNED, PRESETS, ReceiptSettings, first_photo_bottom,
                      load_choices, load_settings, make_receipt, preset)
from .film import FILM, FilmSettings, make_film_strip
from .grimoire import GRIMOIRE, GrimoireSettings, make_grimoire
from .stamp import PAIR, StampSettings, make_stamp_sheet, sheet_length
from .strip import StripSettings, make_strip, strip_to_screen

CAPTURE_DIR = Path.home() / "photobooth" / "captures"
THEMES = (*PRESETS, "classic", "film", "stamp", "grimoire", "choice", "duet", "pick")
# Themes whose confirm screen offers two LAYOUTS rather than two receipts or two photos: one
# pair each, named here rather than typed into the branches that read them. The pair is what
# varies; everything downstream - the heading, the noun, the photo count, where each side's
# copy comes from - is the same code for both.
CHOICE_PAIRS = {"choice": ("stamp", "film"), "duet": ("receipt", "film")}
# Themes that are not presets themselves but print a preset's copy. Like a pinned preset
# (receipt.PINNED) these never take a saved ~/photobooth/receipt.json: they are not in PRESETS,
# so neither the start-up load nor the per-session reload looks for one.
THEME_PRESET = {"pick": "mayhem"}

# The Sticker Kawaii palette lives in booth/cute.py; these are the roles app.py draws with.
WHITE = cute.WHITE
CREAM = cute.CREAM        # every background that is not a camera frame
INK = cute.INK            # outlines, shadows, body text
PINK = cute.HOT           # the primary action
GREEN = cute.MINT         # PRINT
RED = cute.ALERT          # faults only - cancelling is a quiet cream sticker
CHOOSE_TEXT = "CHOOSE RECEIPT"
CHOOSE_TEXT_CHOICE = "CHOOSE YOUR PRINT"    # a CHOICE_PAIRS theme picks between two layouts
CHOOSE_TEXT_PICK = "CHOOSE YOUR PHOTO"      # one layout over two photos: the picture is the choice
PHOTO_THEMES = ("pick", "grimoire")         # ...which is these two
PAPER_PAD = 8             # cute.paper() inflates its rect by 16, so a card shows 8px above it

# The confirm screen's proportions live in ~/photobooth/ui.json so they can be tuned on the booth
# without editing code or redeploying. Delete the file (or a key) to go back to these numbers.
UI_PATH = Path.home() / "photobooth" / "ui.json"
UI_DEFAULTS = {
    "tape_width": 240,        # how wide each receipt head is drawn - this is the zoom
    "button_height": 86,      # PRINT and START OVER
    "button_width": 0,        # 0 = the width of the words CHOOSE RECEIPT, less button_inset
    "button_inset": 26,       # how much narrower than those words the buttons sit
    "tape_gap": 16,           # space between the two receipts
    "heading_band": 90,       # the strip above the receipts; the heading is centred in it
    "tape_margin": 22,        # gap from the screen edge to a receipt
    "tape_min_width": 170,    # a whole tape narrower than this is cropped to its head instead
}
UI_LIMITS = {"tape_width": (90, 300), "button_height": (52, 150), "button_width": (0, 700),
             "button_inset": (0, 200), "tape_gap": (0, 120), "heading_band": (40, 220),
             "tape_margin": (4, 120), "tape_min_width": (0, 300)}


def load_ui(path: Path = UI_PATH) -> dict:
    """Layout numbers from ~/photobooth/ui.json, falling back per key.

    Anything missing, non-numeric or out of range is ignored rather than raised: a typo in a
    hand-edited file must never stop the booth from opening.
    """
    values = dict(UI_DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return values
    if not isinstance(raw, dict):
        return values
    for key, default in UI_DEFAULTS.items():
        v = raw.get(key)
        lo, hi = UI_LIMITS[key]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            values[key] = int(v)
    return values
GREY = cute.DIM
MILK = cute.MILK          # the card behind the camera window


@dataclass
class BoothConfig:
    photos_per_strip: int = 3
    countdown_seconds: int = 3
    review_seconds: float = 1.2      # how long each captured photo is shown
    done_seconds: float = 5.0
    confirm_before_print: bool = True
    confirm_seconds: float | None = 20.0   # auto-print after this long on the confirm screen; None = wait
    print_enabled: bool = True
    theme: str = "receipt"
    mirror_preview: bool = True
    tap_debounce: float = 0.35       # touch + synthesised mouse events arrive together
    camera: CameraSettings = None    # type: ignore[assignment]
    printer: PrinterSettings = None  # type: ignore[assignment]
    led: LedSettings = None          # type: ignore[assignment]
    strip: StripSettings = None      # type: ignore[assignment]
    receipt: ReceiptSettings = None  # type: ignore[assignment]
    film: FilmSettings = None        # type: ignore[assignment]
    stamp: StampSettings = None      # type: ignore[assignment]
    grimoire: GrimoireSettings = None  # type: ignore[assignment]
    reload_saved: bool = True        # re-read ~/photobooth/receipt.json for every session (no restart after a save)
    title: str | None = None         # --title override applied on top of the saved copy
    auto_start_after: float | None = None   # seconds; for unattended testing
    exit_after_one: bool = False
    carry_lock_hold: float = 3.0     # seconds: hold the exit corner from IDLE to arm, hold the screen from LOCKED to unlock
    start_locked: bool = False       # boot straight into LOCKED (booth.sh lock, when nothing was already running)

    def __post_init__(self) -> None:
        if self.theme not in THEMES:
            raise ValueError(f"unknown theme {self.theme!r}; choose from {', '.join(THEMES)}")
        self.camera = self.camera or CameraSettings()
        self.printer = self.printer or PrinterSettings()
        self.led = self.led or LedSettings()
        self.strip = self.strip or StripSettings()
        preset_name = THEME_PRESET.get(self.theme, self.theme)
        self.receipt = self.receipt or PRESETS.get(preset_name, PRESETS["receipt"])
        self.film = self.film or FILM
        self.stamp = self.stamp or PAIR
        self.grimoire = self.grimoire or GRIMOIRE

    @property
    def noun(self) -> str:
        if self.theme in CHOICE_PAIRS:
            return "print"                       # the guest picks the layout, so name neither
        if self.theme == "stamp":
            return "stamps"                      # the pair is what comes out, not one of them
        if self.theme == "grimoire":
            return "grimoire"
        return "strip" if self.theme in ("classic", "film") else "receipt"

    @property
    def nouns(self) -> str:
        """The noun in the plural, for the lines that talk about more than one session."""
        return self.noun if self.noun.endswith("s") else self.noun + "s"


class SessionCancelled(Exception):
    """Raised inside a session when CANCEL is tapped (or Escape pressed)."""


class PhotoboothApp:
    def __init__(self, config: BoothConfig | None = None, camera=None, printer=None, led=None) -> None:
        """``camera``, ``printer`` and ``led`` default to the real hardware; booth/sim.py passes fakes."""
        self.cfg = config or BoothConfig()
        self.camera = camera if camera is not None else BoothCamera(self.cfg.camera)
        self.printer = printer if printer is not None else BoothPrinter(self.cfg.printer)
        self.led = led if led is not None else BoothLed(self.cfg.led)
        self.running = True
        self.state = "IDLE"
        self.state_since = time.monotonic()
        self.last_tap = 0.0
        self.print_thread: threading.Thread | None = None
        self.print_error: str | None = None
        self.print_skipped = False
        self.strip_image = None
        self.strip_surface: pygame.Surface | None = None
        self.strip_path: Path | None = None
        self.options: list[tuple[str, ReceiptSettings | None]] = []   # booth default + the latest two
        self.option_images: list = []
        self.option_surfaces: list[pygame.Surface] = []
        self.option_index = 0
        self.option_thumbs: list[pygame.Surface] = []
        self.tape_rects: list[pygame.Rect] = []
        self.two_up = False
        self.session_dir: Path | None = None
        self.ui = load_ui()                          # ~/photobooth/ui.json, read once at start-up
        self.choose_bar_y, self.choose_bar_x = 32, 0
        self.count_label_y, self.count_value_y = 0, 0
        self.order_number = 0
        self.camera_error: str | None = None
        self.notice: tuple[str, float] | None = None   # (text, show-until monotonic time)
        self._hold_start: float | None = None       # monotonic time a press began: exit corner (IDLE) or anywhere (LOCKED)
        self._lock_requested = False     # SIGUSR1 (booth.sh lock / the desktop icon): arm carry-lock next time IDLE runs

    # -- setup ---------------------------------------------------------------

    def _create_display(self) -> pygame.Surface:
        """Fullscreen on the booth's touchscreen. The simulator overrides this with a window."""
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.display.set_caption("Photobooth")
        pygame.mouse.set_visible(False)
        return screen

    def _init_screen(self) -> None:
        pygame.init()
        cute.reset_fonts()      # fonts cached before a previous pygame.quit() are dangling handles
        self.screen = self._create_display()
        self.width, self.height = self.screen.get_size()
        self.portrait = self.height > self.width
        self.font_small = cute.font(22)
        self._pill_font = cute.font(18)                  # receipt-style pills, for names that do not fit
        self.font_mid = cute.font(30)
        self.font_big = cute.font(40)
        self.font_huge = cute.font(150)
        self.font_heading = cute.font(34 if self.portrait else 44)   # one-line messages
        self._layout()
        self.clock = pygame.time.Clock()

    def _layout(self) -> None:
        """Button positions for the screen shape: 800x480 landscape or 480x800 portrait.

        On idle the camera card and START share a left and a right edge: ``cam_card`` is
        the outer sticker, ``cam_win`` the window the live frame is clipped into, and
        ``start_btn`` is dropped straight below the card at the same width.
        """
        w, h = self.width, self.height
        self.exit_btn = pygame.Rect(w - 74, 14, 58, 34)
        self.cancel_btn = pygame.Rect(22, h - 86, 180, 62)                # during the countdown
        if self.portrait:
            self.cam_win = pygame.Rect(0, 0, w - 80, 460)
            self.cam_win.midtop = (w // 2, 92)
            btn_h, gap = 104, 32
        else:
            self.cam_win = pygame.Rect(0, 0, 460, 254)
            self.cam_win.midtop = (w // 2, 70)
            btn_h, gap = 88, 28
        self.cam_card = self.cam_win.inflate(24, 24)
        self.start_btn = pygame.Rect(self.cam_card.left, self.cam_card.bottom + gap, self.cam_card.w, btn_h)
        self.caption_y = max(18, self.cam_card.top - 28)
        self.petals = cute.petal_field(w, h, self.cam_card.union(self.start_btn))
        # while shooting, the frame goes nearly edge to edge behind a thin outline so you
        # can actually see yourself; the count and the shot number sit in the top corners.
        self.cam_big = pygame.Rect(10, 10, w - 20, h - 20)
        badge = 68 if not self.portrait else 76
        self.count_badge = pygame.Rect(0, 0, badge, badge)
        self.count_badge.center = (self.cam_big.right - badge // 2 - 18, self.cam_big.top + badge // 2 + 18)

        if self.portrait:
            # confirm screen: the receipt across the top, PRINT and START OVER side by side underneath
            self.print_btn = pygame.Rect(0, 0, w // 2 - 30, 92)
            self.print_btn.center = (w // 4 + 5, h - 100)
            self.confirm_cancel_btn = pygame.Rect(0, 0, w // 2 - 30, 92)
            self.confirm_cancel_btn.center = (w - w // 4 - 5, h - 100)
            self.preview_max = (w - 40, h - 250)
        else:
            # confirm screen: the receipt on the left, the two buttons stacked beside it.
            # _place_confirm_buttons() re-centres them once the composed image's width is known.
            self.print_btn = pygame.Rect(0, 0, 416, 104)
            self.print_btn.center = (w - 240, 192)
            self.confirm_cancel_btn = pygame.Rect(0, 0, 300, 76)
            self.confirm_cancel_btn.center = (w - 240, 310)
            self.preview_max = (w - 40, h - 48)

    def _place_confirm_buttons(self) -> None:
        """Centre PRINT / START OVER in whatever space the composed image leaves beside it."""
        if self.portrait or self.strip_surface is None:
            return
        self.two_up = len(self.option_thumbs) > 1
        self.tape_rects = []
        if self.two_up:                   # one receipt against each edge, controls down the middle
            edge = self.ui["tape_margin"]
            # the heading is centred between the top of the screen and the top of the cards
            top = self.ui["heading_band"]
            self.choose_bar_y = (top - PAPER_PAD) // 2
            left = self.option_thumbs[0].get_rect()
            left.topleft = (edge, top)
            right = self.option_thumbs[1].get_rect()
            right.topleft = (left.right + self.ui["tape_gap"], top)
            self.tape_rects = [left, right]
            self.choose_bar_x = (left.left + right.right) // 2
            mid, bh = top + left.height // 2, self.ui["button_height"]
            zone = right.right + 24                      # the controls live to the right of both
            cx = (zone + self.width) // 2
            want = self.ui["button_width"] or self._choose_text_width() - self.ui["button_inset"]
            bw = max(150, min(want, self.width - zone - 22))
            self.print_btn = pygame.Rect(0, 0, bw, bh)
            self.confirm_cancel_btn = pygame.Rect(0, 0, bw, bh)      # same size as PRINT, as asked
            counter = 84 if self.cfg.confirm_seconds else 0          # "Printing in" plus the number
            block = counter + bh + 12 + bh
            stack_top = mid - block // 2
            self.count_label_y = stack_top + 13
            self.count_value_y = stack_top + 55
            self.print_btn.center = (cx, stack_top + counter + bh // 2)
            self.confirm_cancel_btn.center = (cx, self.print_btn.centery + bh + 12)
            return
        right_of_tape = 34 + self.strip_surface.get_width() + 8
        cx = (right_of_tape + 30 + self.width) // 2
        self.print_btn.center = (cx, 192)
        self.confirm_cancel_btn.center = (cx, 310)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_since = time.monotonic()
        if state == "IDLE":
            self.led.set_mode("idle")

    def _next_order_number(self) -> int:
        try:
            existing = sum(1 for p in CAPTURE_DIR.iterdir() if p.is_dir())
        except OSError:
            existing = 0
        return max(existing, self.order_number) + 1

    # -- drawing helpers -----------------------------------------------------

    def _ground(self) -> None:
        """Cream paper with sakura in the margins - the background of every screen."""
        self.screen.fill(CREAM)
        cute.scatter(self.screen, self.petals)

    def _draw_preview(self, big: bool = False) -> None:
        """The live feed: inside its sticker card on idle, near-fullscreen while shooting."""
        try:
            frame = self.camera.preview_frame()
        except CameraTimeout as exc:
            self.camera_error = str(exc)
            self._ground()
            cute.sticker(self.screen, self.cam_card, MILK, 20)
            cy = self.cam_card.centery
            self._draw_text("camera not responding", self.font_heading, (self.width // 2, cy - 40), RED)
            lines = (["power off and reseat", "the camera ribbon cable"] if self.portrait
                     else ["power off and reseat the camera ribbon cable"]) + ["(manual page 2, step 3)"]
            for i, line in enumerate(lines):
                self._draw_text(line, self.font_small, (self.width // 2, cy + 10 + 26 * i), GREY)
            return
        self.camera_error = None
        if self.cfg.mirror_preview:
            frame = frame[:, ::-1]
        self._blit_frame(pygame.surfarray.make_surface(frame.swapaxes(0, 1)), big=big)

    def _draw_camera_card(self, surf: pygame.Surface, smooth: bool = False) -> None:
        """Blit a camera frame into the rounded window, cropped to cover it."""
        cute.rr(self.screen, INK, self.cam_card.move(0, cute.SHADOW), 20)
        cute.rr(self.screen, MILK, self.cam_card, 20)
        shown = cute.fit_cover(surf, self.cam_win.size, smooth)
        self.screen.blit(cute.mask_round(shown, self.cam_win.size, 12), self.cam_win.topleft)
        cute.rr(self.screen, INK, self.cam_card, 20, cute.EDGE)
        cute.blossom(self.screen, (self.cam_card.left - 6, self.cam_card.top - 4), 20, PINK)
        cute.blossom(self.screen, (self.cam_card.right + 8, self.cam_card.bottom + 2), 16, cute.LILAC)

    def _blit_frame(self, surf: pygame.Surface, smooth: bool = False, big: bool = False) -> None:
        """Cream ground, then the frame - in the idle card, or nearly edge to edge."""
        self._ground()
        if big:
            self._draw_full_frame(surf, smooth)
        else:
            self._draw_camera_card(surf, smooth)

    def _draw_full_frame(self, surf: pygame.Surface, smooth: bool = False) -> None:
        """The shooting view: as much of the screen as possible behind a thin ink outline."""
        rect = self.cam_big
        shown = cute.fit_cover(surf, rect.size, smooth)
        self.screen.blit(cute.mask_round(shown, rect.size, 14), rect.topleft)
        cute.rr(self.screen, INK, rect, 14, 5)

    def _draw_text(self, text: str, font: pygame.font.Font, center: tuple[int, int],
                   colour=INK, shadow=False) -> None:
        """Ink on cream needs no drop shadow; ``shadow`` is kept for text over a camera frame."""
        if shadow:
            sh = font.render(text, True, INK)
            self.screen.blit(sh, sh.get_rect(center=(center[0] + 2, center[1] + 2)))
        label = font.render(text, True, colour)
        self.screen.blit(label, label.get_rect(center=center))

    def _draw_button(self, rect: pygame.Rect, label: str, colour, font=None, radius: int = 14,
                     ink=WHITE) -> None:
        cute.sticker(self.screen, rect, colour, radius, label, font or self.font_big, ink)

    def _draw_quiet_button(self, rect: pygame.Rect, label: str, font=None) -> None:
        """A cream sticker for anything that is not the primary action."""
        cute.sticker(self.screen, rect, CREAM, rect.h // 2, label, font or self.font_mid, GREY)

    def _banner(self, text: str, colour=RED) -> None:
        lines = [text]
        if ": " in text and self.font_small.size(text)[0] > self.exit_btn.left - 34:   # keep clear of EXIT
            head, tail = text.split(": ", 1)
            lines = [head + ":", tail]
        labels = [self.font_small.render(line, True, WHITE) for line in lines]
        rect = pygame.Rect(12, 12, max(l.get_width() for l in labels) + 26,
                           sum(l.get_height() for l in labels) + 14)
        cute.sticker(self.screen, rect, colour, 12)
        y = rect.top + 7
        for label in labels:
            self.screen.blit(label, (rect.left + 13, y))
            y += label.get_height()

    def _draw_strip_preview(self) -> tuple[int, int]:
        """Blit the composed image on its paper card; return the centre of the free area for text."""
        self._ground()
        if self.strip_surface is None:
            return self.width // 2, self.height // 2
        rect = self.strip_surface.get_rect()
        if self.portrait:
            rect.midtop = (self.width // 2, 28)
            cute.paper(self.screen, rect)
            self.screen.blit(self.strip_surface, rect)
            return self.width // 2, rect.bottom + 40
        rect.midleft = (34, self.height // 2)
        cute.paper(self.screen, rect)
        self.screen.blit(self.strip_surface, rect)
        return rect.right + (self.width - rect.right) // 2, self.height // 2

    def _draw_confirm_tapes(self) -> None:
        """Both receipts side by side. The chosen one is lifted and outlined, the other rests back."""
        self._ground()
        for i, rect in enumerate(self.tape_rects):
            chosen = i == self.option_index
            cute.paper(self.screen, rect)
            self.screen.blit(self.option_thumbs[i], rect)
            if chosen:
                cute.rr(self.screen, PINK, rect.inflate(12, 12), 10, cute.EDGE)
            else:
                veil = pygame.Surface(rect.size, pygame.SRCALPHA)
                veil.fill((*CREAM, 138))                 # rests back without going grey
                self.screen.blit(veil, rect)

    @property
    def choose_text(self) -> str:
        if self.cfg.theme in CHOICE_PAIRS:
            return CHOOSE_TEXT_CHOICE
        if self.cfg.theme in PHOTO_THEMES:
            return CHOOSE_TEXT_PICK
        return CHOOSE_TEXT

    def _choose_text_width(self) -> int:
        """The width of the words alone - what PRINT and START OVER are cut to, less the inset."""
        return self.font_mid.size(self.choose_text)[0]

    def _draw_countdown(self, cx: int, left: float | None) -> None:
        """"Printing in" with the seconds under it, above PRINT. No ring - just the number."""
        if left is None or not self.cfg.confirm_seconds:
            return
        self._draw_text("Printing in", self.font_small, (cx, self.count_label_y), GREY)
        self._draw_text(str(max(0, int(left) + 1)), self.font_big, (cx, self.count_value_y), PINK)

    def _draw_choose_bar(self, cx: int, cy: int) -> None:
        """The heading, centred over the pair of receipts."""
        self._draw_text(self.choose_text, self.font_mid, (cx, cy), INK)

    def _draw_shot_pill(self, shot: int, n: int, topleft: tuple[int, int] | None = None) -> None:
        """PHOTO n OF m on a cream sticker: straddling the idle card, or tucked in a corner."""
        label = self.font_small.render(f"PHOTO {shot} OF {n}", True, INK)
        pad, gap, step = 22, 20, 17
        rect = pygame.Rect(0, 0, pad * 2 + label.get_width() + gap + n * step, 48)
        if topleft is None:
            rect.center = (self.width // 2, self.cam_card.top)
        else:
            rect.topleft = topleft
        cute.sticker(self.screen, rect, CREAM, 24)
        self.screen.blit(label, label.get_rect(midleft=(rect.left + pad, rect.centery)))
        x = rect.left + pad + label.get_width() + gap
        for i in range(n):
            pygame.draw.circle(self.screen, PINK if i < shot else cute.PETAL, (x + i * step, rect.centery), 6)

    def _draw_countdown_digit(self, count: int) -> None:
        """The count as a small sakura badge in the top corner, clear of your face."""
        centre = self.count_badge.center
        r = self.count_badge.w // 2
        cute.blossom(self.screen, centre, r, cute.PETAL)
        inner = int(r * 0.74)
        pygame.draw.circle(self.screen, CREAM, centre, inner)
        pygame.draw.circle(self.screen, INK, centre, inner, 3)
        self._draw_text(str(count), cute.font(max(22, int(inner * 1.5))),
                        (centre[0], centre[1] + 1), PINK)

    def _draw_print_button(self, left: float | None, centred: bool = False) -> None:
        """PRINT, with the auto-print timer draining as a ring on its right.

        ``centred`` puts the label on the button's own centre line instead of centring it in the
        space beside the ring - the two-up screen wants it to line up with START OVER below.
        """
        rect = self.print_btn
        label_font = self.font_mid if (self.portrait or centred) else self.font_big
        cute.sticker(self.screen, rect, GREEN, rect.h // 2)
        if left is None or not self.cfg.confirm_seconds:
            self._draw_text("PRINT", label_font, rect.center, WHITE)
            return
        r = min(20 if centred else 30, rect.h // 2 - 16)
        cx = rect.right - r - (12 if centred else 30)
        label_x = rect.centerx if centred else (rect.left + cx - r - 8) // 2
        self._draw_text("PRINT", label_font, (label_x, rect.centery), WHITE)
        cute.ring(self.screen, (cx, rect.centery), r, left / self.cfg.confirm_seconds,
                  cute.MINT_TRACK, WHITE, 6)
        self._draw_text(str(max(0, int(left) + 1)), self.font_small, (cx, rect.centery), WHITE)

    def _flip(self) -> None:
        pygame.display.flip()

    # -- input ---------------------------------------------------------------

    def _tap_position(self, ev: pygame.event.Event) -> tuple[int, int] | None:
        """Screen position of a debounced tap/click event, else None."""
        if ev.type == pygame.FINGERDOWN:
            pos = int(ev.x * self.width), int(ev.y * self.height)
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            pos = ev.pos
        else:
            return None
        now = time.monotonic()
        if now - self.last_tap < self.cfg.tap_debounce:
            return None
        self.last_tap = now
        return pos

    def _pump(self, cancel_rect: pygame.Rect | None = None) -> None:
        """Handle events during a session step. Raises SessionCancelled on CANCEL / Escape."""
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_q:
                    self.running = False
                elif ev.key == pygame.K_ESCAPE:
                    raise SessionCancelled
            else:
                pos = self._tap_position(ev)
                if pos and cancel_rect is not None and cancel_rect.collidepoint(pos):
                    raise SessionCancelled

    def _handle_idle_events(self) -> None:
        """exit is a tap to quit, same as ever, but holding it for carry_lock_hold arms carry-lock instead."""
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running = False
                elif ev.key in (pygame.K_SPACE, pygame.K_RETURN):
                    self._set_state("COUNTDOWN")
            elif ev.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
                if self._hold_start is not None:
                    held = time.monotonic() - self._hold_start
                    self._hold_start = None
                    if held < self.cfg.carry_lock_hold:
                        self.running = False       # a quick tap still exits
            else:
                pos = self._tap_position(ev)
                if pos is None:
                    continue
                if self.exit_btn.collidepoint(pos):
                    self._hold_start = time.monotonic()
                elif self.start_btn.collidepoint(pos) and not self.camera_error:
                    self._set_state("COUNTDOWN")
        if self._hold_start is not None and time.monotonic() - self._hold_start >= self.cfg.carry_lock_hold:
            self._hold_start = None
            self._enter_locked()

    # -- states --------------------------------------------------------------

    def _idle(self) -> None:
        if self._lock_requested:
            self._lock_requested = False
            self._enter_locked()
            return
        self._handle_idle_events()
        self._draw_preview()
        if not self.cfg.print_enabled:
            self._banner("printing off (--no-print): images are only saved", GREY)
        elif not self.printer.is_writable():
            self._banner(f"printer not found: {self.cfg.nouns} will only be saved" if not self.printer.is_present()
                         else "printer not writable: add yourself to the lp group")
        elif self.print_error:
            self._banner(f"last print failed: {self.print_error}")
        if not self.camera_error:
            self._draw_button(self.start_btn, "START", PINK)
        holding = self._hold_start is not None
        if holding:
            self._draw_text("hold to carry-lock…", self.font_small, (self.width // 2, self.caption_y), GREY)
        elif self.notice and time.monotonic() < self.notice[1]:
            # a notice takes the caption's slot, so START stays tappable throughout
            self._draw_text(self.notice[0], self.font_small, (self.width // 2, self.caption_y), GREY)
        else:
            cap = self.font_mid.render("SMILE", True, GREY)
            cap_rect = cap.get_rect(center=(self.width // 2, self.caption_y))
            self.screen.blit(cap, cap_rect)
            cute.heart(self.screen, (cap_rect.left - 26, cap_rect.centery), 9, PINK)
            cute.heart(self.screen, (cap_rect.right + 26, cap_rect.centery), 9, PINK)
        cute.sticker(self.screen, self.exit_btn, cute.PETAL, 17, "exit", self.font_small, GREY, outline=GREY)
        if holding:
            frac = (time.monotonic() - self._hold_start) / self.cfg.carry_lock_hold
            cute.ring(self.screen, self.exit_btn.center, self.exit_btn.h // 2 + 8, frac, cute.MINT_TRACK, PINK, 4)
        self._flip()
        if (self.cfg.auto_start_after is not None and not self.camera_error
                and time.monotonic() - self.state_since >= self.cfg.auto_start_after):
            self._set_state("COUNTDOWN")

    def _enter_locked(self) -> None:
        """Carry-lock: for the bag at a fair, so a jostled touchscreen can't wake the printer or start a session.

        The HDMI signal stays up throughout - this booth's screen is an external monitor (ELECROW
        RC050S / MPI5001) with touch and power over a separate USB link and no brightness control
        the Pi can reach, so ``wlopm --off`` doesn't dim it: it drops the connector, the monitor
        paints its own "No Signal" placeholder, and there is no way back in except SSH. A plain
        black frame gets the same "looks off in a bag" result without that risk.
        """
        self._hold_start = None
        self.led.set_mode("off")
        self._set_state("LOCKED")

    def _locked(self) -> None:
        """Input ignored except a long press, shown as a "hold to unlock" ring on a black screen."""
        now = time.monotonic()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
                if self._hold_start is None:
                    self._hold_start = now
            elif ev.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
                self._hold_start = None

        self.screen.fill(cute.INK)
        if self._hold_start is not None:
            held = now - self._hold_start
            if held >= self.cfg.carry_lock_hold:
                self._hold_start = None
                self.led.set_mode("idle")
                self._set_state("IDLE")
                return
            cute.ring(self.screen, (self.width // 2, self.height // 2), 70,
                      held / self.cfg.carry_lock_hold, cute.MINT_TRACK, PINK, 8)
            self._draw_text("hold to unlock", self.font_mid, (self.width // 2, self.height // 2 + 110), WHITE)
        self._flip()
        self.clock.tick(30 if self._hold_start is not None else 5)

    def _cancel(self, text: str) -> None:
        self.notice = (text, time.monotonic() + 2.5)
        self._set_state("IDLE")

    def _countdown_and_capture(self) -> None:
        self.order_number = self._next_order_number()
        session = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = CAPTURE_DIR / session
        try:
            photos = self._take_photos(session_dir)
        except SessionCancelled:
            self._cancel("cancelled, nothing printed")
            return
        if photos is None:
            return
        self._compose(photos, session_dir)
        if self.cfg.confirm_before_print:
            self._set_state("CONFIRM")
        else:
            self._start_print()

    def _take_photos(self, session_dir: Path) -> list[Path] | None:
        """Countdown + capture for each photo. Returns None if the camera failed or the app is quitting."""
        photos: list[Path] = []
        n = self.cfg.photos_per_strip
        for shot in range(1, n + 1):
            if self.camera_error:
                self._set_state("IDLE")
                return None
            end = time.monotonic() + self.cfg.countdown_seconds
            while self.running:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                self._pump(self.cancel_btn)
                self._draw_preview(big=True)
                self._draw_shot_pill(shot, n, topleft=(self.cam_big.left + 18, self.cam_big.top + 18))
                self._draw_countdown_digit(int(remaining) + 1)
                self._draw_quiet_button(self.cancel_btn, "CANCEL")
                self._flip()
                self.clock.tick(60)
            if not self.running:
                return None
            # flash + capture
            self.led.set_mode("capture", duration=0.6)
            self.screen.fill(WHITE)
            self._flip()
            path = session_dir / f"photo_{shot}.jpg"
            try:
                self.camera.capture_still(path)
            except CameraTimeout as exc:
                self.camera_error = str(exc)
                self._set_state("IDLE")
                return None
            photos.append(path)
            # show the shot briefly
            shot_img = pygame.image.load(str(path))
            if self.cfg.mirror_preview:
                shot_img = pygame.transform.flip(shot_img, True, False)
            self._blit_frame(shot_img, smooth=True, big=True)
            self._draw_shot_pill(shot, n, topleft=(self.cam_big.left + 18, self.cam_big.top + 18))
            self._draw_quiet_button(self.cancel_btn, "CANCEL")
            self._flip()
            wait_until = time.monotonic() + self.cfg.review_seconds
            while self.running and time.monotonic() < wait_until:
                self._pump(self.cancel_btn)
                self.clock.tick(30)
        return photos if self.running else None

    def _compose(self, photos: list[Path], session_dir: Path) -> None:
        self._ground()
        self._draw_text(f"MAKING YOUR {self.cfg.noun.upper()}...", self.font_heading,
                        (self.width // 2, self.height // 2), PINK)
        self._flip()
        self.session_dir = session_dir
        if self.cfg.theme == "classic":
            self.options = [("CLASSIC", None)]
            self.option_images = [make_strip(photos, self.cfg.strip)]
        elif self.cfg.theme == "film":
            self.options = [("FILM", None)]
            self.option_images = [make_film_strip(photos, self._saved_or_builtin("film", self.cfg.film))]
        elif self.cfg.theme == "stamp":
            self.options = [("STAMP", None)]
            self.option_images = [make_stamp_sheet(photos, self._saved_or_builtin("stamp", self.cfg.stamp))]
        elif self.cfg.theme == "grimoire":
            # Two photographs, one cover. The window holds a single picture, so the session takes
            # two and the guest chooses which goes in it. As with `pick` the choice is the PICTURE,
            # so everything else is held identical: one settings object (the saved copy resolved
            # once, with one notice if it is the wrong layout) and one timestamp in both.
            settings = self._saved_or_builtin("grimoire", self.cfg.grimoire)
            when = datetime.now()
            frames = photos[:2]
            self.options = [(f"PHOTO {i + 1}", settings) for i in range(len(frames))]
            self.option_images = [make_grimoire([frame], settings, when=when) for frame in frames]
        elif self.cfg.theme in CHOICE_PAIRS:
            # The session takes three frames because the film strip wants three. A side written
            # for fewer says so (_photos_for) and takes the first of the same capture rather than
            # a session of its own — the stamp pair is two, the day and the names.
            self.options = self._choice_options()
            self.option_images = [tape.render(st, photos[:self._photos_for(st)] or photos)
                                  for _, st in self.options]
        elif self.cfg.theme == "pick":
            # Same tape twice, one photo in each: here the guest is choosing the PICTURE, not the
            # layout, so everything else about the two options is held identical - one timestamp and
            # one order number. Only the first two frames are offered; the confirm screen is two-up.
            when = datetime.now()
            frames = photos[:2]
            self.options = [(f"PHOTO {i + 1}", self.cfg.receipt) for i in range(len(frames))]
            self.option_images = [make_receipt([frame], self.cfg.receipt, when=when,
                                               number=self.order_number)
                                  for frame in frames]
        else:
            self.options = self._receipt_options()
            when = datetime.now()                        # one timestamp, so the options differ only in style
            self.option_images = [make_receipt(photos, st, when=when, number=self.order_number)
                                  for _, st in self.options]
        self.option_surfaces = [self._preview_surface(im) for im in self.option_images]
        self.option_thumbs = ([] if len(self.option_images) < 2 else
                              [self._tape_thumb(im) for im in self.option_images])
        self._select_option(0)

    def _tape_thumb(self, image) -> pygame.Surface:
        """As much of the receipt as stays readable, as large as fits beside its twin.

        A long tape scaled whole is far too small to read - three photos come to about 188 mm, which
        lands around 98 dots wide here - so for those only the head is shown, everything down to the
        first photo, much larger. A short tape is a different matter: the 96 mm Maker Faire receipt
        fits top to bottom and still reads, so it is shown END TO END rather than cropped, and the
        guest chooses between two finished prints instead of two previews.

        The switch is how wide the whole tape would land: under ``tape_min_width`` it is cropped.
        Setting that key to 0 in ui.json always shows the whole tape, and to 300 never does.
        """
        box = (self.ui["tape_width"], self.height - 96)
        whole = min(box[0] / image.width, box[1] / image.height) * image.width
        if whole >= self.ui["tape_min_width"]:
            return self._preview_surface(image, box)
        head = image.crop((0, 0, image.width, first_photo_bottom(image)))
        return self._preview_surface(head, box)

    def _preview_surface(self, image, max_size: tuple[int, int] | None = None) -> pygame.Surface:
        preview = strip_to_screen(image, max_size or self.preview_max)
        return pygame.image.fromstring(preview.tobytes(), preview.size, "RGB")

    def _receipt_options(self) -> list[tuple[str, ReceiptSettings]]:
        """What the guest can choose between: the booth's own copy, then the latest two sent in."""
        options: list[tuple[str, ReceiptSettings]] = [("DEFAULT", self._receipt_settings())]
        options.extend(load_choices(limit=1))
        return options

    def _select_option(self, index: int) -> None:
        """Show option ``index`` on the confirm screen; it is what PRINT will send."""
        if not self.option_images:
            return
        self.option_index = max(0, min(index, len(self.option_images) - 1))
        self.strip_image = self.option_images[self.option_index]
        self.strip_surface = self.option_surfaces[self.option_index]
        if self.session_dir is not None:
            self.strip_path = self.session_dir / f"{self.cfg.noun}.png"
            self.strip_image.save(self.strip_path)       # the saved PNG always matches the screen
        self._place_confirm_buttons()

    def _saved_copy(self):
        """``(layout, settings)`` as last saved to ~/photobooth/receipt.json, or None.

        A file the booth cannot read is a notice on screen, not a crash: the built-in copy of
        whatever the theme wants still prints.
        """
        if not self.cfg.reload_saved:
            return None
        try:
            return tape.load()
        except (OSError, ValueError) as exc:              # unreadable or malformed receipt.json
            self.notice = (f"saved copy ignored: {type(exc).__name__}", time.monotonic() + 4)
            return None

    def _saved_or_builtin(self, layout: str, builtin):
        """The copy to print for ``layout``: the saved one if it is that layout, else ``builtin``.

        One file holds one layout, so a saved copy of a different one is not an error — the booth
        says which it is ignoring and prints its own.
        """
        saved = self._saved_copy()
        if saved is None:
            return builtin
        if saved[0] == layout:
            return saved[1]
        self.notice = (f"saved copy is a {saved[0]}; printing the built-in {layout}",
                       time.monotonic() + 4)
        return builtin

    @staticmethod
    def _photos_for(settings) -> int | None:
        """How many of the session's photos this layout is written for; None means all of them."""
        return sheet_length(settings) if isinstance(settings, StampSettings) else None

    def _choice_options(self) -> list[tuple[str, object]]:
        """The two prints a guest chooses between, for whichever pair this theme names.

        The saved copy (~/photobooth/receipt.json) is read once and lands on whichever side it
        belongs to; the other keeps its built-in, and neither counts as the wrong layout, so no
        notice. The RECEIPT side is the exception: one file holds one layout, so when the film
        side is using it the receipt side would have nothing left to read. It takes the newest
        set sent in from the Tape Bench (receipt_choices.json) instead, under that set's own
        name — which is also what the guest reads on the button.
        """
        saved = self._saved_copy()
        builtin = {"receipt": self.cfg.receipt, "film": self.cfg.film,
                   "stamp": self.cfg.stamp, "grimoire": self.cfg.grimoire}
        names = {"receipt": "RECEIPT", "film": "FILM", "stamp": "STAMPS", "grimoire": "GRIMOIRE"}
        options: list[tuple[str, object]] = []
        for layout in CHOICE_PAIRS[self.cfg.theme]:
            label, settings = names[layout], builtin[layout]
            if layout == "receipt":
                sent = load_choices(limit=1)
                if sent:
                    label, settings = sent[0][0].upper(), sent[0][1]
            elif saved is not None and saved[0] == layout:
                settings = saved[1]
            options.append((label, settings))
        return options

    def _receipt_settings(self) -> ReceiptSettings:
        """The copy to print: whatever Receipt Studio / Tape Bench saved most recently, else the preset."""
        settings = self.cfg.receipt
        if self.cfg.reload_saved and self.cfg.theme in PRESETS and self.cfg.theme not in PINNED:
            try:
                saved = load_settings()
            except (OSError, ValueError) as exc:          # unreadable or malformed receipt.json
                self.notice = (f"saved receipt ignored: {type(exc).__name__}", time.monotonic() + 4)
                saved = None
            if saved is not None:
                settings = replace(saved, shop=self.cfg.title) if self.cfg.title else saved
        return settings

    def _start_print(self) -> None:
        """Send the composed image to the printer in a background thread, then show REVIEW."""
        self.print_error = None
        self.print_skipped = not self.cfg.print_enabled
        self.print_thread = None
        if self.print_skipped:
            pass
        elif self.printer.is_writable():
            image = self.strip_image

            def job() -> None:
                try:
                    self.printer.print_image(image)
                except Exception as exc:  # noqa: BLE001 - shown on screen
                    self.print_error = f"{type(exc).__name__}: {exc}"
            self.print_thread = threading.Thread(target=job, name="print", daemon=True)
            self.print_thread.start()
        else:
            self.print_error = "printer not available"
        self.led.set_mode("printing" if self.print_thread is not None else "idle")
        self._set_state("REVIEW")

    def _confirm(self) -> None:
        """Show the result with PRINT / CANCEL; auto-print when the timer runs out."""
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_q:
                    self.running = False
                elif ev.key == pygame.K_ESCAPE:
                    self._cancel("cancelled, nothing printed")
                    return
                elif ev.key in (pygame.K_SPACE, pygame.K_RETURN):
                    self._start_print()
                    return
            else:
                pos = self._tap_position(ev)
                if pos is None:
                    continue
                picked = next((i for i, r in enumerate(self.tape_rects) if r.inflate(16, 16).collidepoint(pos)), None)
                if picked is not None:
                    if picked != self.option_index:
                        self._select_option(picked)
                    self.state_since = time.monotonic()   # still choosing: give them the full timer back
                    continue
                if self.print_btn.collidepoint(pos):
                    self._start_print()
                    return
                if self.confirm_cancel_btn.collidepoint(pos):
                    self._cancel("cancelled, nothing printed")
                    return
        if self.two_up:
            self._draw_confirm_tapes()
            self._draw_choose_bar(self.choose_bar_x, self.choose_bar_y)
        else:
            self._draw_strip_preview()
        left = (None if self.cfg.confirm_seconds is None
                else self.cfg.confirm_seconds - (time.monotonic() - self.state_since))
        self._draw_print_button(None if self.two_up else left, centred=self.two_up)
        if self.two_up:
            self._draw_countdown(self.print_btn.centerx, left)      # matched to PRINT, and red by request - cute.ALERT is otherwise faults only
            self._draw_button(self.confirm_cancel_btn, "START OVER", RED,
                              self.font_mid, self.confirm_cancel_btn.h // 2)
        else:
            self._draw_quiet_button(self.confirm_cancel_btn, "START OVER")
        if left is not None and left <= 0:
            self._start_print()
            return
        self._flip()
        self.clock.tick(30)

    def _review(self) -> None:
        try:
            self._pump()
        except SessionCancelled:
            pass    # too late to cancel once the bytes are on their way
        tx, ty = self._draw_strip_preview()
        y1, y2 = (ty + 10, ty + 48) if self.portrait else (ty - 30, ty + 20)
        printing = self.print_thread is not None and self.print_thread.is_alive()
        if printing:
            self._draw_text("PRINTING...", self.font_heading, (tx, y1), PINK)
        elif self.print_skipped:
            self._draw_text(f"{self.cfg.noun.upper()} SAVED", self.font_heading, (tx, y1), cute.MINT_DARK)
            self._draw_text("printing is off (--no-print)", self.font_small, (tx, y2), GREY)
        elif self.print_error:
            self._draw_text("PRINT FAILED", self.font_heading, (tx, y1), RED)
            self._draw_text(f"{self.cfg.noun} saved", self.font_small, (tx, y2), GREY)
        else:
            self._draw_text(f"TAKE YOUR {self.cfg.noun.upper()}!", self.font_heading, (tx, y1), cute.MINT_DARK)
        self._flip()
        if not printing and self.state == "REVIEW":
            self.led.set_mode("idle")
            self._set_state("DONE")
        self.clock.tick(30)

    def _done(self) -> None:
        self._review()
        if time.monotonic() - self.state_since >= self.cfg.done_seconds:
            if self.cfg.exit_after_one:
                self.running = False
            else:
                self._set_state("IDLE")

    # -- main loop -----------------------------------------------------------

    def run(self) -> int:
        self._init_screen()

        def request_exit(signum, frame):
            self.running = False

        def request_lock(signum, frame):
            self._lock_requested = True     # armed once IDLE next sees it, in _idle()

        signal.signal(signal.SIGTERM, request_exit)
        signal.signal(signal.SIGINT, request_exit)
        signal.signal(signal.SIGUSR1, request_lock)   # booth.sh lock / the "Carry Lock" desktop icon
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with self.camera, self.led:
                if self.cfg.start_locked:
                    self._enter_locked()
                while self.running:
                    if self.state == "IDLE":
                        self._idle()
                        self.clock.tick(60)
                    elif self.state == "COUNTDOWN":
                        self._countdown_and_capture()
                    elif self.state == "CONFIRM":
                        self._confirm()
                    elif self.state == "REVIEW":
                        self._review()
                    elif self.state == "DONE":
                        self._done()
                    elif self.state == "LOCKED":
                        self._locked()
        finally:
            if self.print_thread is not None and self.print_thread.is_alive():
                self.print_thread.join(timeout=30)
            pygame.quit()
        return 0


def build_parser():
    """The booth's command line; booth/sim.py adds its own options on top."""
    import argparse

    ap = argparse.ArgumentParser(description="Photobooth: preview, countdown, photos, confirm, print a receipt.")
    ap.add_argument("--theme", choices=THEMES, default="receipt",
                    help="receipt (default): register-tape design; birthday: single-photo Classic Register; "
                         "classic: plain titled strip; mayhem: the Maker Faire tape, one photo, 96 mm; "
                         "film: 35 mm film strip with the names up the rail; "
                         "stamp: postage stamps, the date over one and the names over the next; "
                         "grimoire: two photos on the framed spellbook cover, then the guest "
                         "picks which one fills its arched window; "
                         "window of a spellbook cover, the place, the date and the handle in the "
                         "cartouche at its foot; "
                         "choice: three photos, then the guest picks stamps or film on the confirm "
                         "screen; duet (what the desktop icon runs): three photos, then the guest picks "
                         "the newest receipt sent in from the Tape Bench or the saved film strip; "
                         "pick: two photos on the Maker Faire tape, "
                         "then the guest picks which photo prints")
    ap.add_argument("--photos", type=int, default=None,
                    help="photos per session (default 3; 1 for the birthday and mayhem themes, "
                         "2 for stamp, pick and grimoire)")
    ap.add_argument("--countdown", type=int, default=3, help="seconds per countdown (default 3)")
    ap.add_argument("--title", default=None, help="shop name on the receipt / title of the classic strip")
    ap.add_argument("--hflip", action="store_true", help="camera is mounted mirrored")
    ap.add_argument("--vflip", action="store_true", help="camera is mounted upside down")
    ap.add_argument("--no-mirror", action="store_true", help="do not mirror the on-screen preview")
    ap.add_argument("--mains", type=int, choices=(60, 50, 0), default=60,
                    help="mains frequency of the venue's lighting for anti-flicker (default 60; 0 disables)")
    ap.add_argument("--no-confirm", action="store_true", help="print straight away, without the PRINT/CANCEL screen")
    ap.add_argument("--confirm-timeout", type=float, default=20.0, metavar="SECONDS",
                    help="auto-print after this long on the confirm screen (default 20; 0 waits for a tap)")
    ap.add_argument("--no-print", action="store_true", help="never send anything to the printer, only save")
    ap.add_argument("--feed", type=int, default=None, metavar="LINES",
                    help="blank lines fed after each print so there is something to grip and tear "
                         "(default 4; the grimoire theme uses 9, about 35 mm)")
    ap.add_argument("--ignore-saved", action="store_true",
                    help=f"use the built-in theme copy even if Receipt Studio saved {CONFIG_PATH}")
    ap.add_argument("--once", action="store_true",
                    help="start automatically after 2 s, skip the confirm screen, print one, then exit (for testing)")
    ap.add_argument("--locked", action="store_true",
                    help="boot straight into carry-lock: black screen, wakes only on a long press (booth.sh lock)")
    return ap


def config_from_args(args) -> BoothConfig:
    receipt = None
    if args.theme in PRESETS:
        receipt = preset(args.theme, shop=args.title)
        saved = None if (args.ignore_saved or args.theme in PINNED) else load_settings()
        if saved is not None:
            receipt = replace(saved, shop=args.title) if args.title else saved
            print(f"receipt copy: {CONFIG_PATH} (saved by Receipt Studio; --ignore-saved for the built-in theme)")
    film_settings = stamp_settings = grimoire_settings = None
    if (args.theme in ("film", "stamp", "grimoire") or args.theme in CHOICE_PAIRS) \
            and not args.ignore_saved:
        saved = tape.load()
        if saved is not None and saved[0] in ("film", "stamp", "grimoire"):
            print(f"{saved[0]} copy: {CONFIG_PATH} (saved copy; --ignore-saved for the built-in)")
            if saved[0] == "film":
                film_settings = saved[1]
            elif saved[0] == "grimoire":
                grimoire_settings = saved[1]
            else:
                stamp_settings = saved[1]
    # A stamp pair is two pictures: the date goes over the first and the names over the second,
    # and a third would fall back to the post's own name over it.
    # A CHOICE_PAIRS theme offers the film strip, which needs three frames, so it takes three;
    # a side written for fewer (the stamp pair) makes its print out of the first of them.
    default_photos = {"birthday": 1, "mayhem": 1,
                      "stamp": 2, "pick": 2, "grimoire": 2}.get(args.theme, 3)
    photos = args.photos if args.photos is not None else default_photos
    # How much blank paper follows a print. The tear bar sits a fixed distance past the print head,
    # so a short tail leaves almost nothing sticking out beyond it to take hold of -- which is what
    # makes a print hard to tear, not how long the print itself is. Per THEME rather than globally:
    # the faire booth is signed off and running, and its tape is long enough to grip already.
    feed = args.feed if args.feed is not None else {"grimoire": 9}.get(args.theme,
                                                                       PrinterSettings.feed_lines)
    return BoothConfig(
        theme=args.theme,
        photos_per_strip=photos,
        countdown_seconds=args.countdown,
        mirror_preview=not args.no_mirror,
        confirm_before_print=not (args.no_confirm or args.once),
        confirm_seconds=args.confirm_timeout if args.confirm_timeout > 0 else None,
        print_enabled=not args.no_print,
        printer=PrinterSettings(feed_lines=feed),
        camera=CameraSettings(hflip=args.hflip, vflip=args.vflip,
                              flicker_period_us=round(1_000_000 / (2 * args.mains)) if args.mains else 0),
        strip=StripSettings(title=args.title or StripSettings.title),
        receipt=receipt,
        film=film_settings,
        stamp=stamp_settings,
        grimoire=grimoire_settings,
        reload_saved=not args.ignore_saved,
        title=args.title,
        auto_start_after=2.0 if args.once else None,
        exit_after_one=args.once,
        start_locked=args.locked,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return PhotoboothApp(config_from_args(args)).run()
