#!/usr/bin/env python3
"""Stress and speed checks for the booth's face squares (booth/faces.py), on still pictures.

Runs the booth's own FaceTracker on a picture framed the way the preview shows it
(800x480, cropped to cover, mirrored) and on harder variants of it: dark, backlit,
small in the frame, tilted, blurred, grainy. For the live version use
tools/face_booth_sim.py (webcam inside the booth simulator).

    .venv-sim/bin/python tools/face_track_sim.py --stress pic.png --out /tmp/faces
    .venv-sim/bin/python tools/face_track_sim.py --bench pic.png     # ms per frame by detect width
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from booth.faces import FaceSettings, FaceTracker  # noqa: E402

SCREEN = (800, 480)


def booth_view(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    k = max(SCREEN[0] / w, SCREEN[1] / h)
    big = cv2.resize(rgb, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
    y0, x0 = (big.shape[0] - SCREEN[1]) // 2, (big.shape[1] - SCREEN[0]) // 2
    return big[y0:y0 + SCREEN[1], x0:x0 + SCREEN[0]][:, ::-1].copy()


def variants(img: np.ndarray) -> dict[str, np.ndarray]:
    h, w = img.shape[:2]
    rot = cv2.warpAffine(img, cv2.getRotationMatrix2D((w / 2, h / 2), 20, 1.0), (w, h),
                         borderMode=cv2.BORDER_REFLECT)
    small = cv2.resize(img, (w // 3, h // 3), interpolation=cv2.INTER_AREA)
    far = cv2.copyMakeBorder(small, h // 3, h - h // 3 - small.shape[0], w // 3,
                             w - w // 3 - small.shape[1], cv2.BORDER_CONSTANT, value=(205, 205, 205))
    noise = np.clip(img.astype(np.int16) + np.random.default_rng(1).normal(0, 18, img.shape), 0, 255)
    return {
        "as-is": img,
        "dark-0.35": (img * 0.35).astype(np.uint8),
        "backlit": cv2.addWeighted((img * 0.4).astype(np.uint8), 1, np.full_like(img, 90), 0.6, 0),
        "far-third": far,
        "tilt-20": rot,
        "blur": cv2.GaussianBlur(img, (0, 0), 3),
        "noisy-iso": noise.astype(np.uint8),
    }


def load(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path))
    if bgr is None:
        sys.exit(f"{path.name}: not an image")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def timed(tracker: FaceTracker, img: np.ndarray, runs: int = 30):
    faces = tracker.detect(img)
    t = time.perf_counter()
    for _ in range(runs):
        tracker.detect(img)
    return faces, (time.perf_counter() - t) / runs * 1000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stress", nargs="+", type=Path, metavar="IMAGE")
    mode.add_argument("--bench", type=Path, metavar="IMAGE")
    ap.add_argument("--out", type=Path, default=Path("face_sim_out"))
    a = ap.parse_args()
    if a.bench:
        img = booth_view(load(a.bench))
        print(f"{'detect width':>12} {'1 thread ms':>12} {'all threads ms':>15} faces")
        for w in (320, 400, 480, 640, 800):
            tracker = FaceTracker(replace(FaceSettings(), detect_w=w))
            cv2.setNumThreads(1)
            faces, one = timed(tracker, img)
            cv2.setNumThreads(-1)
            _, many = timed(tracker, img)
            print(f"{w:12} {one:12.1f} {many:15.1f} {len(faces):5}")
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    tracker = FaceTracker()
    print(f"{'image':28} {'variant':11} faces  ms/frame  min score")
    for p in a.stress:
        for name, v in variants(booth_view(load(p))).items():
            faces, ms = timed(tracker, v)
            lo = min((f.score for f in faces), default=0)
            print(f"{p.name[:28]:28} {name:11} {len(faces):5}  {ms:8.1f}  {lo:9.2f}")
            cv2.imwrite(str(a.out / f"{p.stem}_{name}.png"), cv2.cvtColor(tracker.draw(v, faces), cv2.COLOR_RGB2BGR))
    print(f"{len(a.stress) * 7} drawn frames written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
