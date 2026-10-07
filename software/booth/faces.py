"""Face-tracking squares over the live preview.

A white square around every face the camera sees, on the live preview and in the printed
photos: ``FaceBoxCamera`` wraps a camera (BoothCamera on the Pi, a simulator camera on a
laptop) and passes everything through except ``preview_frame``, which comes back with the
squares drawn on, and ``capture_still``, which draws them into the saved still so every
layout made from it prints them. The untouched still stays beside it as photo_N.clean.jpg.
``FaceSettings(on_stills=False)`` keeps the prints clean.

Detector: OpenCV's YuNet (``cv2.FaceDetectorYN``), model in booth/models/ (230 KB, from
opencv_zoo). It runs on a copy of the frame DETECT_W pixels wide; boxes are scaled back
up. Between detections each square eases toward its new position and survives HOLD
frames without a match, so it glides instead of flickering.

Needs OpenCV: ``sudo apt install python3-opencv`` on the Pi, ``opencv-python-headless``
in the laptop venv. Nothing in booth/ imports this yet; tools/face_booth_sim.py runs it
inside the simulator with a webcam.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"

WHITE, BLACK = (255, 255, 255), (0, 0, 0)
HOT, INK = (240, 98, 161), (74, 52, 66)        # cute.HOT and cute.INK, kept as plain tuples so this needs no pygame


@dataclass(frozen=True)
class SquareStyle:
    """How one face square looks. Colours are RGB, like the camera frames."""
    colour: tuple[int, int, int] = WHITE
    edge: tuple[int, int, int] | None = BLACK   # a darker rim either side of the line; None = no rim
    shape: str = "square"                       # "square", "corners" (viewfinder brackets) or "rounded"
    label: bool = False                         # a "FACE 98%" tag on the top edge
    weight: float = 1.0                         # line width multiplier


# The looks to pick from: --face-style NAME, or F in tools/face_booth_sim.py to cycle them.
STYLES = {
    "box": SquareStyle(edge=None),
    "outlined": SquareStyle(),
    "corners": SquareStyle(shape="corners", weight=1.3),
    "label": SquareStyle(label=True),
    "kawaii": SquareStyle(colour=HOT, edge=INK, shape="rounded", weight=1.3),
}


@dataclass
class FaceSettings:
    style: str = "outlined"        # a key of STYLES; the black rim keeps a white line visible over a pale wall on paper
    detect_w: int = 400            # width the detector sees; 400 found every face in the stress set
    score: float = 0.6             # YuNet confidence floor
    every: int = 1                 # detect on every Nth preview frame; squares coast in between
    smooth: float = 0.5            # share of the new position a square takes each detection
    hold: int = 4                  # detections a square survives without a match
    line: int = 3                  # px at the 800x480 preview
    on_stills: bool = True         # draw the squares into the saved photos too, so they print
    still_detect_w: int = 800      # stills are detected once, so they can afford a sharper look
    still_line: float = 1 / 120    # line width as a share of the photo width: ~3 dots on the 384-dot paper
    keep_clean: bool = True        # keep the untouched still beside it as photo_N.clean.jpg


@dataclass
class Face:
    box: np.ndarray                # x, y, w, h in frame pixels
    score: float
    missed: int = 0

    @property
    def centre(self) -> np.ndarray:
        return self.box[:2] + self.box[2:] / 2


@dataclass
class FaceTracker:
    settings: FaceSettings = field(default_factory=FaceSettings)
    faces: list[Face] = field(default_factory=list)
    detect_ms: float = 0.0         # running average, for the console readout

    def __post_init__(self) -> None:
        if not MODEL.exists():
            raise FileNotFoundError(f"{MODEL} is missing (opencv_zoo face_detection_yunet)")
        self.net = cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), self.settings.score, 0.3, 50)
        self._frame_no = 0

    def detect(self, rgb: np.ndarray, detect_w: int | None = None) -> list[Face]:
        """Every face in one RGB frame, no tracking."""
        h, w = rgb.shape[:2]
        k = min(1.0, (detect_w or self.settings.detect_w) / w)
        small = cv2.resize(rgb, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else rgb
        self.net.setInputSize((small.shape[1], small.shape[0]))
        _, rows = self.net.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))   # YuNet was trained on BGR
        return [Face(r[0:4] / k, float(r[14])) for r in (rows if rows is not None else [])]

    def update(self, rgb: np.ndarray) -> list[Face]:
        """Track across frames: match each square to the nearest new face and ease toward it."""
        self._frame_no += 1
        if (self._frame_no - 1) % self.settings.every:
            return self.faces
        t = time.perf_counter()
        new = self.detect(rgb)
        self.detect_ms = 0.9 * self.detect_ms + 0.1 * (time.perf_counter() - t) * 1000
        kept = []
        for old in self.faces:
            best = min(new, key=lambda f: np.linalg.norm(f.centre - old.centre), default=None)
            if best is not None and np.linalg.norm(best.centre - old.centre) < old.box[2]:
                new.remove(best)
                old.box += self.settings.smooth * (best.box - old.box)
                old.score, old.missed = best.score, 0
                kept.append(old)
            elif old.missed < self.settings.hold:
                old.missed += 1
                kept.append(old)
        self.faces = kept + new
        return self.faces

    def draw(self, rgb: np.ndarray, faces: list[Face] | None = None, line: int | None = None,
             style: str | None = None) -> np.ndarray:
        """A square (the longer side of the box) centred on each face, drawn on a copy."""
        out = np.ascontiguousarray(rgb).copy()
        look = STYLES[style or self.settings.style]
        lw = max(1, round((line or self.settings.line * out.shape[1] / 800) * look.weight))
        for f in self.faces if faces is None else faces:
            side = float(max(f.box[2], f.box[3]))
            x0, y0 = f.centre - side / 2
            draw_square(out, look, round(x0), round(y0), round(side), lw, f.score)
        return out


def _strokes(shape: str, x: int, y: int, s: int) -> tuple[list, list]:
    """The square's outline as straight segments and quarter arcs (centre, radius, start angle)."""
    x1, y1 = x + s, y + s
    if shape == "corners":
        a = max(4, round(s * 0.24))
        return [((x, y), (x + a, y)), ((x, y), (x, y + a)), ((x1, y), (x1 - a, y)), ((x1, y), (x1, y + a)),
                ((x, y1), (x + a, y1)), ((x, y1), (x, y1 - a)), ((x1, y1), (x1 - a, y1)),
                ((x1, y1), (x1, y1 - a))], []
    if shape == "rounded":
        r = max(3, round(s * 0.16))
        lines = [((x + r, y), (x1 - r, y)), ((x + r, y1), (x1 - r, y1)),
                 ((x, y + r), (x, y1 - r)), ((x1, y + r), (x1, y1 - r))]
        arcs = [((x + r, y + r), r, 180), ((x1 - r, y + r), r, 270), ((x1 - r, y1 - r), r, 0), ((x + r, y1 - r), r, 90)]
        return lines, arcs
    return [((x, y), (x1, y)), ((x1, y), (x1, y1)), ((x1, y1), (x, y1)), ((x, y1), (x, y))], []


def draw_square(img: np.ndarray, look: SquareStyle, x: int, y: int, s: int, lw: int, score: float) -> None:
    """One face square in place: the rim first (wider, in the edge colour), then the line over it."""
    lines, arcs = _strokes(look.shape, x, y, s)
    rim = max(1, round(lw * 0.45))
    passes = ([(look.edge, lw + 2 * rim)] if look.edge else []) + [(look.colour, lw)]
    for colour, width in passes:
        for a, b in lines:
            cv2.line(img, a, b, colour, width, cv2.LINE_AA)
        for c, r, start in arcs:
            cv2.ellipse(img, c, (r, r), 0, start, start + 90, colour, width, cv2.LINE_AA)
    if look.label:
        text = f"FACE {round(score * 100)}%"
        scale = max(0.35, s / 260)
        thick = max(1, round(scale * 2))
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        pad = max(2, th // 3)
        top = max(0, y - th - 2 * pad - lw // 2)
        cv2.rectangle(img, (x - lw // 2, top), (x - lw // 2 + tw + 2 * pad, y - lw // 2), look.colour, -1)
        if look.edge:
            cv2.rectangle(img, (x - lw // 2, top), (x - lw // 2 + tw + 2 * pad, y - lw // 2), look.edge, rim)
        cv2.putText(img, text, (x - lw // 2 + pad, y - lw // 2 - pad), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    look.edge or BLACK, thick, cv2.LINE_AA)


class FaceBoxCamera:
    """A camera whose preview frames carry face squares; stills and everything else pass through."""

    def __init__(self, camera, settings: FaceSettings | None = None) -> None:
        self._camera = camera
        self.tracker = FaceTracker(settings or FaceSettings())

    def preview_frame(self) -> np.ndarray:
        frame = self._camera.preview_frame()
        return self.tracker.draw(frame, self.tracker.update(frame))

    def capture_still(self, path) -> dict:
        """The wrapped camera's still, then (``on_stills``) the same squares drawn into the file.

        The still is detected on its own rather than reusing the preview squares: it is a
        different size and framing, and the preview is mirrored while the still is not."""
        meta = self._camera.capture_still(path)
        s = self.tracker.settings
        if s.on_stills:
            path = Path(path)
            bgr = cv2.imread(str(path))
            if bgr is not None:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                faces = self.tracker.detect(rgb, s.still_detect_w)
                if s.keep_clean:
                    path.replace(path.with_suffix(".clean" + path.suffix))
                boxed = self.tracker.draw(rgb, faces, line=max(2, round(rgb.shape[1] * s.still_line)))
                quality = getattr(getattr(self._camera, "settings", None), "jpeg_quality", 93)
                cv2.imwrite(str(path), cv2.cvtColor(boxed, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
                meta = {**meta, "Faces": len(faces)}
        return meta

    def __enter__(self) -> "FaceBoxCamera":
        self._camera.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._camera.__exit__(*exc)

    def __getattr__(self, name: str):
        return getattr(self._camera, name)
