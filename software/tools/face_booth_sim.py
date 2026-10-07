#!/usr/bin/env python3
"""The booth simulator with face squares, fed by this computer's webcam.

The window is the real booth UI from booth/sim.py (800x480 screen plus printer panel).
The camera is the laptop webcam, wrapped by booth/faces.FaceBoxCamera - the same wrapper
the Pi would put around BoothCamera - so the squares on the preview come from the exact
code and model the booth would run. The squares are drawn into the stills too, so they print.

    .venv-sim/bin/python tools/face_booth_sim.py              # first camera with a picture
    .venv-sim/bin/python tools/face_booth_sim.py --webcam 1   # a particular camera
    .venv-sim/bin/python tools/face_booth_sim.py --photos-dir ~/Pictures/booth-test   # no webcam
    .venv-sim/bin/python tools/face_booth_sim.py --every 2    # detect on every 2nd frame
    .venv-sim/bin/python tools/face_booth_sim.py --face-style kawaii   # start on a square style

F cycles the square styles (booth/faces.STYLES) while it runs; the current one is shown at
the bottom of the printer panel, and the next photo prints in it.

Every booth option works too (--theme, --photos, ...). The terminal prints the detector's
time per frame every few seconds; that is this computer's speed, not the Pi's.
macOS asks once for camera access for the app running this (Terminal or VS Code).
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pygame  # noqa: E402

from booth import app as booth_app  # noqa: E402
from booth.app import build_parser, config_from_args  # noqa: E402
from booth.camera import CameraTimeout  # noqa: E402
from booth.faces import STYLES, FaceBoxCamera, FaceSettings  # noqa: E402
from booth.sim import PANEL_BG, PANEL_FG, SIM_DIR, FakeCamera, FakePrinter, SimulatorApp  # noqa: E402


class WebcamCamera(FakeCamera):
    """FakeCamera's interface over a real webcam: newest frame, cropped to cover 800x480."""

    def __init__(self, settings, index: int) -> None:
        super().__init__(settings)
        self.index = index
        self._cap: cv2.VideoCapture | None = None
        self._latest: np.ndarray | None = None          # full-size RGB
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        super().start()
        if self.index is None:
            self.index = pick_webcam()
        self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            raise SystemExit(f"webcam {self.index} did not open - on macOS allow camera access for "
                             "Terminal/VS Code in System Settings > Privacy & Security > Camera")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self._running = True
        threading.Thread(target=self._grab, daemon=True).start()

    def _grab(self) -> None:
        while self._running:
            ok, bgr = self._cap.read()
            if ok:
                with self._lock:
                    self._latest = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def stop(self) -> None:
        self._running = False
        time.sleep(0.1)
        if self._cap is not None:
            self._cap.release()

    def _full(self) -> np.ndarray:
        if self.fail:
            raise CameraTimeout("simulated camera fault (press C to restore)")
        deadline = time.monotonic() + 3
        while True:
            with self._lock:
                if self._latest is not None:
                    return self._latest
            if time.monotonic() > deadline:
                raise CameraTimeout("webcam sent no frames")
            time.sleep(0.02)

    def preview_frame(self) -> np.ndarray:
        img = self._full()
        (w, h), (fh, fw) = self.size, img.shape[:2]
        k = max(w / fw, h / fh)
        big = cv2.resize(img, (round(fw * k), round(fh * k)), interpolation=cv2.INTER_AREA)
        y0, x0 = (big.shape[0] - h) // 2, (big.shape[1] - w) // 2
        return big[y0:y0 + h, x0:x0 + w]

    def capture_still(self, path) -> dict:
        started = time.monotonic()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        img = self._full()
        cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality])
        return {"CaptureSeconds": round(time.monotonic() - started, 3), "Path": str(path), "Simulated": True}


def pick_webcam(tries: int = 5) -> int:
    """The first camera whose frames are not black. Virtual cameras (OBS, Streamfog, Camo) with
    nothing feeding them open fine and send black frames, and macOS can list one of them first."""
    for i in range(tries):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            t, bright = time.monotonic(), 0.0
            while time.monotonic() - t < 1.5 and bright < 15:
                ok, frame = cap.read()
                bright = float(frame.mean()) if ok else 0.0
            cap.release()
            print(f"camera {i}: {'picture' if bright >= 15 else 'black'}", flush=True)
            if bright >= 15:
                return i
    raise SystemExit("no camera sent a picture - check camera access for Terminal/VS Code in "
                     "System Settings > Privacy & Security > Camera, or pass --webcam N")


class FaceSimApp(SimulatorApp):
    """The simulator plus one key: F steps through the square styles."""

    def _filtered_event_get(self, *args, **kwargs):
        kept = []
        for ev in super()._filtered_event_get(*args, **kwargs):
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_f:
                names = list(STYLES)
                settings = self.camera.tracker.settings
                settings.style = names[(names.index(settings.style) + 1) % len(names)]
                print(f"square style: {settings.style}", flush=True)
            else:
                kept.append(ev)
        return kept

    def _draw_panel(self) -> None:
        super()._draw_panel()
        label = self.panel_font.render(f"square style: {self.camera.tracker.settings.style}   (F changes it)",
                                       True, PANEL_FG)
        w, h = self.SCREEN_SIZE
        self.window.fill(PANEL_BG, pygame.Rect(w + 2, h - 24, 400, 24))      # over the "saves to" line
        self.window.blit(label, (w + 14, h - 21))


def report(camera: FaceBoxCamera) -> None:
    while True:
        time.sleep(3)
        t = camera.tracker
        print(f"faces {len(t.faces)}   detect {t.detect_ms:5.1f} ms/frame (this computer)", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    ap.description = "Booth simulator with face squares over a webcam (booth/faces.py)."
    ap.add_argument("--webcam", type=int, default=None, metavar="INDEX",
                    help="which camera (default: the first one that sends a picture)")
    ap.add_argument("--photos-dir", type=Path, default=None, help="pictures instead of a webcam")
    ap.add_argument("--every", type=int, default=1, metavar="N", help="detect on every Nth frame (default 1)")
    ap.add_argument("--face-style", choices=list(STYLES), default=FaceSettings.style,
                    help=f"square style to start with (default {FaceSettings.style}); F cycles them")
    ap.add_argument("--print-speed", type=float, default=50.0, metavar="MM_PER_S")
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    booth_app.CAPTURE_DIR = SIM_DIR / "captures"
    raw = FakeCamera(cfg.camera, args.photos_dir) if args.photos_dir else WebcamCamera(cfg.camera, args.webcam)
    camera = FaceBoxCamera(raw, FaceSettings(every=args.every, style=args.face_style))
    app = FaceSimApp(cfg, camera, FakePrinter(cfg.printer, args.print_speed))
    app.sim_camera = raw                                   # the C key toggles faults on the real camera object
    threading.Thread(target=report, args=(camera,), daemon=True).start()
    print("face squares from booth/faces.py; keys: F square style, C camera fault, Q quits, Escape cancels")
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
