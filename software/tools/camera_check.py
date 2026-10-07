#!/usr/bin/env python3
"""Camera diagnostic for the photobooth. Run this on the Raspberry Pi.

    cd ~/photobooth/software
    python3 tools/camera_check.py            # detect, focus sweep, capture a still
    python3 tools/camera_check.py --show 8   # also show the live preview on the
                                             # touchscreen for 8 seconds (pygame)
    python3 tools/camera_check.py --no-capture

Point the camera at something with texture (a face, a bookshelf, printed text)
about 0.5-1.5 m away before judging the autofocus numbers. A blank wall or
ceiling gives a FocusFoM under ~100 and a "failed" AF state, which is normal.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libcamera import controls  # noqa: E402

from booth.camera import BoothCamera, CameraSettings, describe_cameras  # noqa: E402

AF_STATE = {0: "idle", 1: "scanning", 2: "focused", 3: "failed"}
CAPTURE_DIR = Path.home() / "photobooth" / "captures"


def section(title: str) -> None:
    print(f"\n== {title}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=float, default=0, metavar="SECONDS", help="show live preview on screen")
    ap.add_argument("--no-capture", action="store_true", help="skip the full-resolution still")
    ap.add_argument("--hflip", action="store_true")
    ap.add_argument("--vflip", action="store_true")
    args = ap.parse_args()

    section("cameras")
    cams = describe_cameras()
    if not cams:
        print("No camera detected. Check the ribbon cable seating and orientation at both ends,")
        print("then run: rpicam-hello --list-cameras")
        return 1
    for i, c in enumerate(cams):
        print(f"  {i}: {c.get('Model')}  id={c.get('Id')}")

    settings = CameraSettings(hflip=args.hflip, vflip=args.vflip)
    with BoothCamera(settings) as camera:
        section("preview stream")
        t0 = time.monotonic()
        frames = 0
        while time.monotonic() - t0 < 2.0:
            frame = camera.preview_frame()
            frames += 1
        fps = frames / (time.monotonic() - t0)
        print(f"  frame shape {frame.shape} (H, W, RGB)   ~{fps:.1f} fps")
        md = camera.metadata()
        print(f"  exposure {md.get('ExposureTime')} us  gain {md.get('AnalogueGain', 0):.2f}  "
              f"colour temp {md.get('ColourTemperature')} K  lux {md.get('Lux', 0):.0f}")

        if camera.has_autofocus:
            section("autofocus (continuous, 3 s)")
            time.sleep(3)
            md = camera.cam.capture_metadata()
            print(f"  state {AF_STATE.get(md.get('AfState'), md.get('AfState'))}  "
                  f"lens {md.get('LensPosition', 0):.2f}  FocusFoM {md.get('FocusFoM')}")

            section("manual lens sweep (FocusFoM per position; the peak is the in-focus distance)")
            for lp in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0):
                camera.cam.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": lp})
                time.sleep(0.5)
                md = camera.cam.capture_metadata()
                print(f"  {lp:4.1f} dioptres (~{(1/lp if lp else float('inf')):.2f} m) -> FocusFoM {md.get('FocusFoM')}")
            camera.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
            time.sleep(1.5)
        else:
            print("\n  (no autofocus control on this camera)")

        if args.show > 0:
            section(f"on-screen preview for {args.show:.0f} s")
            os.environ.setdefault("SDL_VIDEODRIVER", "wayland")
            import pygame  # noqa: E402

            pygame.init()
            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
            font = pygame.font.SysFont(None, 28)
            print(f"  pygame driver {pygame.display.get_driver()} screen {screen.get_size()}")
            t0 = time.monotonic()
            frames = 0
            while time.monotonic() - t0 < args.show:
                for ev in pygame.event.get():
                    if ev.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                        t0 = -1e9
                frame = camera.preview_frame()
                surf = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
                if surf.get_size() != screen.get_size():
                    surf = pygame.transform.smoothscale(surf, screen.get_size())
                screen.blit(surf, (0, 0))
                frames += 1
                fps = frames / max(time.monotonic() - t0, 1e-6)
                label = font.render(f"{fps:4.1f} fps  focus {camera.focus_score()}  tap to exit", True, (255, 255, 255))
                screen.blit(label, (12, 12))
                pygame.display.flip()
            pygame.quit()
            print(f"  displayed {frames} frames")

        if not args.no_capture:
            section("full-resolution still")
            path = CAPTURE_DIR / f"check_{datetime.now():%Y%m%d_%H%M%S}.jpg"
            md = camera.capture_still(path)
            size_kb = path.stat().st_size // 1024
            print(f"  {path}  ({size_kb} kB) in {md['CaptureSeconds']} s")
            print(f"  lens {md.get('LensPosition', 0):.2f}  FocusFoM {md.get('FocusFoM')}  "
                  f"exposure {md.get('ExposureTime')} us  gain {md.get('AnalogueGain', 0):.2f}")
            try:
                from PIL import Image

                with Image.open(path) as im:
                    print(f"  image {im.size[0]}x{im.size[1]} {im.mode}")
            except ImportError:
                pass

            # Preview should be running again after the capture.
            camera.preview_frame()
            print("  preview resumed OK")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
