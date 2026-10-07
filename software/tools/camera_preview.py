#!/usr/bin/env python3
"""Fullscreen live camera preview for the touchscreen, launched from the Pi desktop.

Two big touch buttons along the bottom:

* PHOTO  - take a full-resolution still into ~/photobooth/captures, show it
           for a moment, and print it on the thermal printer (one photo,
           384 dots wide, dithered). Pass --no-print to only save.
* EXIT   - close the app (Escape or Q on a keyboard also works)

The overlay shows frames per second, the autofocus state, the focus score, and
the print status.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SDL_VIDEODRIVER", "wayland")

import pygame  # noqa: E402

from booth.camera import BoothCamera, CameraSettings, CameraTimeout  # noqa: E402
from booth.printer import BoothPrinter  # noqa: E402

CAPTURE_DIR = Path.home() / "photobooth" / "captures"
AF_STATE = {0: "idle", 1: "scanning", 2: "focused", 3: "failed"}
BUTTON_H = 84
MIRROR_PREVIEW = True  # show the preview like a mirror; stills stay un-mirrored


def draw_button(screen, font, rect, label, colour):
    pygame.draw.rect(screen, colour, rect, border_radius=18)
    text = font.render(label, True, (255, 255, 255))
    screen.blit(text, text.get_rect(center=rect.center))


def main() -> int:
    pygame.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("Photobooth camera preview")
    pygame.mouse.set_visible(False)
    width, height = screen.get_size()
    small = pygame.font.SysFont(None, 26)
    big = pygame.font.SysFont(None, 44)

    photo_btn = pygame.Rect(16, height - BUTTON_H - 12, width // 2 - 24, BUTTON_H)  # PHOTO + PRINT
    exit_btn = pygame.Rect(width // 2 + 8, height - BUTTON_H - 12, width // 2 - 24, BUTTON_H)

    settings = CameraSettings(hflip="--hflip" in sys.argv, vflip="--vflip" in sys.argv)
    state = {"running": True, "print": "", "print_until": 0.0}
    printer = BoothPrinter()
    printing_enabled = "--no-print" not in sys.argv

    def print_photo(path: Path) -> None:
        try:
            state["print"] = f"printing {path.name}..."
            state["print_until"] = time.monotonic() + 60
            prepared = printer.print_image(path)
            state["print"] = f"printed {path.name} ({prepared.height} rows)"
        except Exception as exc:  # noqa: BLE001 - shown on screen
            state["print"] = f"print failed: {type(exc).__name__}: {exc}"
        state["print_until"] = time.monotonic() + 8

    def request_exit(signum, frame):  # SIGTERM/SIGINT from the desktop or a shell
        state["running"] = False

    signal.signal(signal.SIGTERM, request_exit)
    signal.signal(signal.SIGINT, request_exit)

    with BoothCamera(settings) as camera:
        clock = pygame.time.Clock()
        message, message_until = "", 0.0
        camera_error = ""
        while state["running"]:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    state["running"] = False
                elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    state["running"] = False
                elif ev.type == pygame.MOUSEBUTTONDOWN or ev.type == pygame.FINGERDOWN:
                    if ev.type == pygame.FINGERDOWN:
                        pos = (int(ev.x * width), int(ev.y * height))
                    else:
                        pos = ev.pos
                    if exit_btn.collidepoint(pos):
                        state["running"] = False
                    elif photo_btn.collidepoint(pos) and not camera_error:
                        path = CAPTURE_DIR / f"preview_{datetime.now():%Y%m%d_%H%M%S}.jpg"
                        try:
                            md = camera.capture_still(path)
                        except CameraTimeout as exc:
                            camera_error = str(exc)
                            continue
                        shot = pygame.image.load(str(path))
                        shot = pygame.transform.smoothscale(shot, (width, height))
                        screen.blit(shot, (0, 0))
                        pygame.display.flip()
                        time.sleep(1.5)
                        message = f"saved {path.name}  focus {md.get('FocusFoM')}  {md['CaptureSeconds']:.2f}s"
                        message_until = time.monotonic() + 4
                        if printing_enabled and printer.is_writable():
                            threading.Thread(target=print_photo, args=(path,), daemon=True).start()
                        elif printing_enabled:
                            state["print"] = "printer not found: photo saved only"
                            state["print_until"] = time.monotonic() + 6

            try:
                frame = camera.preview_frame()
                camera_error = ""
            except CameraTimeout as exc:
                camera_error = str(exc)
                frame = None
            if frame is not None:
                if MIRROR_PREVIEW:
                    frame = frame[:, ::-1]
                surf = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
                if surf.get_size() != (width, height):
                    surf = pygame.transform.scale(surf, (width, height))
                screen.blit(surf, (0, 0))
            else:
                screen.fill((36, 27, 40))
                err = big.render("camera not responding", True, (230, 80, 80))
                screen.blit(err, err.get_rect(center=(width // 2, height // 2 - 30)))
                hint = small.render("power off and reseat the camera ribbon cable (manual p.2 step 3)", True, (255, 255, 255))
                screen.blit(hint, hint.get_rect(center=(width // 2, height // 2 + 16)))

            md = camera.metadata() if not camera_error else {}
            info = (f"{clock.get_fps():4.1f} fps   AF {AF_STATE.get(md.get('AfState'), '?')}   "
                    f"lens {md.get('LensPosition', 0):.1f}   focus {md.get('FocusFoM', 0)}")
            label = small.render(info, True, (255, 255, 255))
            bg = pygame.Surface((label.get_width() + 16, label.get_height() + 8), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 140))
            screen.blit(bg, (8, 8))
            screen.blit(label, (16, 12))
            if time.monotonic() < message_until:
                msg = small.render(message, True, (255, 255, 120))
                screen.blit(msg, (16, 44))
            if time.monotonic() < state["print_until"]:
                pm = small.render(state["print"], True, (255, 170, 220))
                screen.blit(pm, (16, 70))

            draw_button(screen, big, photo_btn, "PHOTO + PRINT" if printing_enabled else "PHOTO", (40, 140, 90))
            draw_button(screen, big, exit_btn, "EXIT", (170, 50, 50))
            pygame.display.flip()
            clock.tick(60)

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
