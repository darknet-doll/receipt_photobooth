"""Photobooth simulator: the real booth UI on a laptop, with a fake camera and printer.

    tools/simulate.sh                     # creates a venv with pygame the first time
    tools/simulate.sh --theme birthday    # every photobooth.py option works here too
    tools/simulate.sh --photos-dir ~/Pictures/booth-test   # use your own pictures as the "camera"

The window shows the 800x480 touchscreen exactly as ``booth/app.py`` draws it
(click where you would tap) plus a side panel with a simulated thermal printer:
the receipt feeds out of the slot at the configured paper speed and every print
is saved under ~/photobooth/sim/prints/. Captures go to ~/photobooth/sim/captures/.

Fault keys, to see how the booth copes:

    C   camera fault on/off (the "camera not responding" screen)
    P   printer offline on/off (the "printer not found" banner, "print failed")
    J   paper jam: the next print stops a third of the way through
    T   tear off the paper in the panel
    mouse wheel over the panel scrolls a long receipt

Nothing here touches the Pi-only modules: ``FakeCamera`` and ``FakePrinter``
implement the same methods ``BoothCamera`` and ``BoothPrinter`` expose, and
``SimulatorApp`` only overrides how the display is created and presented.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pygame
from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import app as booth_app
from .app import BoothConfig, PhotoboothApp, build_parser, config_from_args
from .camera import CameraSettings, CameraTimeout
from .printer import PrinterSettings, prepare_image

SIM_DIR = Path.home() / "photobooth" / "sim"
PANEL_W = 320
PAPER_W = 280                 # on-screen width of the 384-dot paper
SIM_KEYS = {pygame.K_c: "camera", pygame.K_p: "printer", pygame.K_j: "jam", pygame.K_t: "tear"}

PANEL_BG = (28, 28, 34)
PANEL_FG = (225, 225, 230)
PANEL_DIM = (140, 140, 150)
SLOT = (70, 70, 80)
OK = (90, 200, 130)
BAD = (235, 90, 90)
WARN = (240, 190, 70)


class FakeCamera:
    """Synthetic live feed (or a folder of pictures) with BoothCamera's interface."""

    def __init__(self, settings: CameraSettings | None = None, photos_dir: Path | None = None) -> None:
        self.settings = settings or CameraSettings()
        self.size = self.settings.preview_size
        self.fail = False
        self.photos: list[Path] = []
        if photos_dir:
            self.photos = sorted(p for p in Path(photos_dir).iterdir()
                                 if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        self._frames: dict[int, np.ndarray] = {}
        self._t0 = time.monotonic()
        w, h = self.size
        yy, xx = np.mgrid[0:h, 0:w]
        bg = np.empty((h, w, 3), np.uint8)
        bg[..., 0] = (70 + 90 * xx / w).astype(np.uint8)
        bg[..., 1] = (60 + 70 * yy / h).astype(np.uint8)
        bg[..., 2] = (120 + 80 * (1 - xx / w)).astype(np.uint8)
        self._bg = bg
        try:
            self._font = ImageFont.load_default(size=22)
        except TypeError:
            self._font = ImageFont.load_default()

    # lifecycle
    def start(self) -> None:
        self._t0 = time.monotonic()

    def stop(self) -> None:
        pass

    def __enter__(self) -> "FakeCamera":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def has_autofocus(self) -> bool:
        return True

    # frames
    def _elapsed(self) -> float:
        return time.monotonic() - self._t0

    def _photo_index(self) -> int:
        return int(self._elapsed() / 2.5) % len(self.photos)

    def _photo_frame(self) -> np.ndarray:
        idx = self._photo_index()
        if idx not in self._frames:
            img = ImageOps.exif_transpose(Image.open(self.photos[idx])).convert("RGB")
            self._frames[idx] = np.asarray(ImageOps.fit(img, self.size, Image.LANCZOS))
        return self._frames[idx]

    def _synthetic_frame(self) -> np.ndarray:
        t = self._elapsed()
        w, h = self.size
        img = Image.fromarray(self._bg.copy())
        d = ImageDraw.Draw(img)
        cx = w / 2 + math.sin(t * 0.9) * w * 0.22
        cy = h / 2 + math.cos(t * 1.3) * h * 0.15
        r = 95 + 10 * math.sin(t * 2.1)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(242, 214, 196), outline=(40, 30, 30), width=4)
        for ex in (-0.38, 0.38):
            d.ellipse([cx + ex * r - 11, cy - 0.3 * r - 11, cx + ex * r + 11, cy - 0.3 * r + 11], fill=(30, 25, 30))
        d.arc([cx - 0.55 * r, cy - 0.1 * r, cx + 0.55 * r, cy + 0.65 * r], 20, 160, fill=(40, 30, 30), width=6)
        d.rectangle([0, h - 34, w, h], fill=(20, 20, 26))
        d.text((12, h - 28), f"SIMULATED CAMERA   {t:6.1f} s   {datetime.now():%H:%M:%S}",
               fill=(230, 230, 235), font=self._font)
        return np.asarray(img)

    def preview_frame(self) -> np.ndarray:
        if self.fail:
            raise CameraTimeout("simulated camera fault (press C to restore)")
        return self._photo_frame() if self.photos else self._synthetic_frame()

    def metadata(self) -> dict:
        return {"LensPosition": 2.5, "FocusFoM": 1500, "AfState": 2, "Simulated": True}

    def focus_score(self) -> int:
        return 1500

    def capture_still(self, path: str | Path) -> dict:
        if self.fail:
            raise CameraTimeout("simulated camera fault during capture (press C to restore)")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        if self.photos:
            img = ImageOps.exif_transpose(Image.open(self.photos[self._photo_index()])).convert("RGB")
        else:
            img = Image.fromarray(self._synthetic_frame())
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)   # pretend it is a bigger still
        time.sleep(0.3)                                                    # the real mode switch + capture
        img.save(path, quality=self.settings.jpeg_quality)
        return {"CaptureSeconds": round(time.monotonic() - started, 3), "Path": str(path), "Simulated": True}


class FakePrinter:
    """Feeds a print row by row at a paper speed; the panel draws it coming out of the slot."""

    def __init__(self, settings: PrinterSettings | None = None, speed_mm_s: float = 50.0,
                 out_dir: Path = SIM_DIR / "prints") -> None:
        self.settings = settings or PrinterSettings()
        self.speed_mm_s = max(1.0, speed_mm_s)
        self.out_dir = out_dir
        self.online = True
        self.jam = False
        self.paper: Image.Image | None = None      # the current sheet, mode "1"
        self.progress = 0                          # rows that have left the slot
        self.printing = False
        self.last_result = "nothing printed yet"
        self.count = 0

    @property
    def device(self) -> str:
        return "simulated printer"

    def is_present(self) -> bool:
        return self.online

    def is_writable(self) -> bool:
        return self.online

    def raw(self, payload: bytes) -> int:
        if not self.online:
            raise OSError("simulated printer offline (press P)")
        return len(payload)

    def feed(self, lines: int | None = None) -> None:
        self.raw(b"")

    def print_text(self, lines: list[str] | str, *, center: bool = True, big: bool = False,
                   feed: bool = True) -> None:
        if isinstance(lines, str):
            lines = lines.splitlines() or [lines]
        img = Image.new("L", (self.settings.width_px, 26 * len(lines) + 12), 255)
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=22 if not big else 30)
        except TypeError:
            font = ImageFont.load_default()
        for i, line in enumerate(lines):
            w = d.textlength(line, font=font)
            d.text(((img.width - w) / 2 if center else 4, 6 + 26 * i), line, fill=0, font=font)
        self.print_image(img, feed=feed)

    def tear_off(self) -> None:
        if not self.printing:
            self.paper = None
            self.progress = 0

    def print_image(self, image: Image.Image | str | Path, *, feed: bool = True) -> Image.Image:
        if not self.online:
            raise OSError("simulated printer offline (press P)")
        if not isinstance(image, Image.Image):
            image = Image.open(image)
        s = self.settings
        prepared = prepare_image(image, s.width_px, s.dither, s.autocontrast)
        self.paper, self.progress, self.printing = prepared, 0, True
        rows_per_s = self.speed_mm_s * 8
        started = time.monotonic()
        try:
            while self.progress < prepared.height:
                time.sleep(0.03)
                self.progress = min(prepared.height, int((time.monotonic() - started) * rows_per_s))
                if self.jam and self.progress > prepared.height // 3:
                    self.last_result = f"jammed after {self.progress} rows (press J to clear, T to tear off)"
                    raise OSError("simulated paper jam (press J to clear)")
            self.out_dir.mkdir(parents=True, exist_ok=True)
            out = self.out_dir / f"print_{datetime.now():%Y%m%d_%H%M%S}.png"
            prepared.save(out)
            self.count += 1
            self.last_result = (f"#{self.count}: {prepared.height} rows = {prepared.height / 8:.0f} mm "
                                f"in {time.monotonic() - started:.1f} s, saved {out.name}")
        finally:
            self.printing = False
        return prepared


class SimulatorApp(PhotoboothApp):
    """The booth app in a window, with the printer panel drawn alongside."""

    SCREEN_SIZE = (800, 480)

    def __init__(self, config: BoothConfig, camera: FakeCamera, printer: FakePrinter) -> None:
        super().__init__(config, camera=camera, printer=printer)
        self.sim_camera, self.sim_printer = camera, printer
        self.window: pygame.Surface | None = None
        self.paper_scroll = 0
        self._paper_surface: pygame.Surface | None = None
        self._paper_source: Image.Image | None = None

    # -- display ----------------------------------------------------------------

    def _create_display(self) -> pygame.Surface:
        w, h = self.SCREEN_SIZE
        self.window = pygame.display.set_mode((w + PANEL_W, h))
        pygame.display.set_caption("Photobooth simulator")
        pygame.mouse.set_visible(True)
        return self.window.subsurface(pygame.Rect(0, 0, w, h))

    def _init_screen(self) -> None:
        super()._init_screen()
        self.panel_font = pygame.font.SysFont(None, 22)
        self.panel_font_bold = pygame.font.SysFont(None, 24, bold=True)
        self._orig_event_get = pygame.event.get
        pygame.event.get = self._filtered_event_get          # simulator keys never reach the booth

    def _filtered_event_get(self, *args, **kwargs):
        kept = []
        for ev in self._orig_event_get(*args, **kwargs):
            if ev.type == pygame.KEYDOWN and ev.key in SIM_KEYS:
                self._sim_key(SIM_KEYS[ev.key])
            elif ev.type == pygame.MOUSEWHEEL:
                self.paper_scroll = max(0, self.paper_scroll + ev.y * 40)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.pos[0] >= self.SCREEN_SIZE[0]:
                pass                                          # clicks on the panel are not taps
            else:
                kept.append(ev)
        return kept

    def _sim_key(self, action: str) -> None:
        if action == "camera":
            self.sim_camera.fail = not self.sim_camera.fail
        elif action == "printer":
            self.sim_printer.online = not self.sim_printer.online
        elif action == "jam":
            self.sim_printer.jam = not self.sim_printer.jam
        elif action == "tear":
            self.sim_printer.tear_off()
            self.paper_scroll = 0

    def _flip(self) -> None:
        self._draw_panel()
        pygame.display.flip()

    # -- printer panel ----------------------------------------------------------

    def _paper(self) -> pygame.Surface | None:
        paper = self.sim_printer.paper
        if paper is None:
            self._paper_surface = self._paper_source = None
            return None
        if paper is not self._paper_source:
            rgb = paper.convert("RGB")
            scaled = rgb.resize((PAPER_W, max(1, round(rgb.height * PAPER_W / rgb.width))), Image.LANCZOS)
            self._paper_surface = pygame.image.fromstring(scaled.tobytes(), scaled.size, "RGB")
            self._paper_source = paper
        return self._paper_surface

    def _panel_text(self, text: str, y: int, colour=PANEL_FG, bold: bool = False, x: int | None = None) -> int:
        font = self.panel_font_bold if bold else self.panel_font
        label = font.render(text, True, colour)
        self.window.blit(label, (x if x is not None else self.SCREEN_SIZE[0] + 16, y))
        return y + label.get_height() + 4

    def _wrap(self, text: str, width: int, max_lines: int = 2) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if self.panel_font.size(trial)[0] <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][:-1] + "…"
        return lines

    def _draw_panel(self) -> None:
        assert self.window is not None
        x0 = self.SCREEN_SIZE[0]
        panel = pygame.Rect(x0, 0, PANEL_W, self.SCREEN_SIZE[1])
        self.window.fill(PANEL_BG, panel)
        pygame.draw.line(self.window, SLOT, (x0, 0), (x0, panel.height), 2)

        y = self._panel_text("SIMULATOR", 10, PANEL_DIM, bold=True)
        cam = self.sim_camera
        y = self._panel_text("camera: FAULT (C to restore)" if cam.fail else
                             f"camera: {'pictures' if cam.photos else 'synthetic'} feed", y, BAD if cam.fail else OK)
        prn = self.sim_printer
        if not prn.online:
            y = self._panel_text("printer: OFFLINE (P to power on)", y, BAD)
        elif prn.printing and prn.paper is not None:
            pct = 100 * prn.progress // max(1, prn.paper.height)
            y = self._panel_text(f"printer: printing {pct}%  ({prn.speed_mm_s:.0f} mm/s)", y, WARN)
        else:
            y = self._panel_text("printer: ready" + ("  [jam armed: J]" if prn.jam else ""), y, WARN if prn.jam else OK)
        for line in self._wrap(prn.last_result, PANEL_W - 32):
            y = self._panel_text(line, y, PANEL_DIM)

        # the slot
        slot_y = y + 8
        pygame.draw.rect(self.window, SLOT, (x0 + 10, slot_y, PANEL_W - 20, 14), border_radius=4)
        pygame.draw.rect(self.window, (10, 10, 12), (x0 + 18, slot_y + 5, PANEL_W - 36, 4))

        # the paper
        hint_y = panel.height - 62
        region = pygame.Rect(x0 + (PANEL_W - PAPER_W) // 2, slot_y + 14, PAPER_W, hint_y - slot_y - 22)
        paper = self._paper()
        if paper is not None and prn.paper is not None:
            scale = PAPER_W / prn.paper.width
            shown = min(paper.get_height(), int(prn.progress * scale))
            if prn.printing:
                self.paper_scroll = 0
            overflow = max(0, shown - region.height)
            offset = max(0, overflow - self.paper_scroll)
            visible = min(region.height, shown - offset)
            if visible > 0:
                self.window.blit(paper, (region.x, region.y), pygame.Rect(0, offset, PAPER_W, visible))
                if shown >= paper.get_height() and offset + visible >= shown:
                    edge_y = region.y + visible        # finished and fully scrolled: torn edge under the last row
                    if edge_y < region.bottom:
                        pts = [(region.x + i * 10, edge_y + (4 if i % 2 else 0)) for i in range(PAPER_W // 10 + 1)]
                        pygame.draw.lines(self.window, (200, 200, 205), False, pts, 2)
            if overflow > 0 and not prn.printing:
                label = self.panel_font.render(f"wheel to scroll ({offset}/{overflow})", True, PANEL_BG)
                bg = pygame.Rect(region.x, region.bottom - 22, PAPER_W, 22)
                pygame.draw.rect(self.window, (200, 200, 205), bg)
                self.window.blit(label, (region.x + 8, region.bottom - 20))
        y = hint_y
        y = self._panel_text("C camera fault   P printer off   J jam", y, PANEL_DIM)
        y = self._panel_text("T tear off       wheel: scroll paper", y, PANEL_DIM)
        self._panel_text(f"saves to {str(SIM_DIR).replace(str(Path.home()), '~')}", y, PANEL_DIM)


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    ap.description = "Photobooth simulator: the booth UI in a window with a fake camera and printer."
    ap.add_argument("--photos-dir", type=Path, default=None,
                    help="folder of JPG/PNG pictures to use as the camera feed and the stills")
    ap.add_argument("--print-speed", type=float, default=50.0, metavar="MM_PER_S",
                    help="simulated paper speed (default 50 mm/s)")
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    booth_app.CAPTURE_DIR = SIM_DIR / "captures"           # keep simulator sessions apart from real ones
    camera = FakeCamera(cfg.camera, args.photos_dir)
    printer = FakePrinter(cfg.printer, args.print_speed)
    print(f"simulator: captures -> {booth_app.CAPTURE_DIR}, prints -> {printer.out_dir}".replace(str(Path.home()), "~"))
    print("keys: C camera fault, P printer offline, J paper jam, T tear off; Q quits, Escape cancels")
    return SimulatorApp(cfg, camera, printer).run()
