#!/usr/bin/env python3
"""Lighting-flicker probe for the photobooth camera. Run on the Pi with the
booth stopped (`booth.sh stop`; the camera is exclusive):

    python3 tools/flicker_check.py            # measure, sweep, save frames to /tmp/flicker
    python3 tools/flicker_check.py --quick    # just report what the booth's own settings give

Mains-powered lamps flicker at twice the mains frequency. On a rolling-shutter
sensor that shows as horizontal bands rolling through the preview whenever the
exposure is not a whole multiple of the flicker period. The Pi runs on DC and
has no mains reference, so this script finds the flicker period from the
picture: it grabs frames at the booth's preview configuration, subtracts the
static scene from each row's brightness, and reports the residual band
amplitude. It then sweeps fixed exposures; the ones that null the bands give
the period:

    nulls at 8333 and 16667 us, not 10000  -> 120 Hz flicker, 60 Hz mains (default guard)
    nulls at 10000 and 20000 us, not 8333  -> 100 Hz flicker, 50 Hz mains (--mains 50)
    no exposure nulls it                   -> PWM LED driver at another rate; no exposure
                                              choice fixes that, light the subject yourself

Amplitudes under about 1 are clean; the untreated 2026-09-09 bench reading was
11.5 with 240-row bands (two per 480-row frame).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libcamera import controls  # noqa: E402
from picamera2 import Picamera2  # noqa: E402

from booth.camera import BoothCamera, CameraSettings, PREVIEW_SENSOR_MODE, PREVIEW_SIZE  # noqa: E402

OUT = Path("/tmp/flicker")
SWEEP_US = (4000, 5000, 6000, 7000, 8333, 10000, 12500, 16667, 20000)


def band_stats(frames: list[np.ndarray]) -> tuple[float, float]:
    """Residual band amplitude and its dominant period in rows."""
    rows = np.stack([f[:, :, 1].astype(np.float32) for f in frames]).mean(axis=2)  # frames x rows
    resid = rows - rows.mean(axis=0)              # remove the static scene
    resid -= resid.mean(axis=1, keepdims=True)    # and per-frame brightness
    spec = np.abs(np.fft.rfft(resid, axis=1)).mean(axis=0)
    spec[:2] = 0
    k = int(spec.argmax())
    return float(resid.std()), (rows.shape[1] / k if k else float("nan"))


def quick() -> int:
    """What the booth itself produces with its flicker guard in place."""
    with BoothCamera(CameraSettings()) as cam:
        time.sleep(1.5)
        frames = [cam.preview_frame() for _ in range(12)]
        md = cam.metadata()
        amp, period = band_stats(frames)
        print(f"booth settings: exposure {md.get('ExposureTime')} us  gain {md.get('AnalogueGain', 0):.2f}  "
              f"band amplitude {amp:.2f}  period {period:.0f} rows  ({'clean' if amp < 1 else 'BANDING'})")
    return 0 if amp < 1 else 1


def sweep() -> int:
    OUT.mkdir(exist_ok=True)
    from PIL import Image
    cam = Picamera2()
    cam.configure(cam.create_preview_configuration(main={"size": PREVIEW_SIZE, "format": "BGR888"},
                                                   raw={"size": PREVIEW_SENSOR_MODE}, buffer_count=4))
    cam.start()
    time.sleep(1.0)

    def grab(n: int = 12):
        frames, md = [], {}
        for _ in range(n):
            frames.append(cam.capture_array("main"))
            md = cam.capture_metadata()
        return frames, md

    report = []

    def record(mode: str, frames, md, save: str | None = None) -> None:
        amp, period = band_stats(frames)
        row = dict(mode=mode, exposure=md.get("ExposureTime"), gain=round(md.get("AnalogueGain", 0), 2),
                   amp=round(amp, 2), period_rows=round(period))
        report.append(row)
        print(row)
        if save:
            Image.fromarray(frames[0]).save(OUT / f"{save}.png")

    cam.set_controls({"AeFlickerMode": controls.AeFlickerModeEnum.Off})
    time.sleep(0.8)
    record("auto, no guard", *grab(), save="auto")
    for hz, per in ((120, 8333), (100, 10000)):
        cam.set_controls({"AeFlickerMode": controls.AeFlickerModeEnum.Manual, "AeFlickerPeriod": per})
        time.sleep(0.8)
        record(f"guard {hz} Hz", *grab(), save=f"guard{hz}")
    cam.set_controls({"AeFlickerMode": controls.AeFlickerModeEnum.Off})
    gain = float(report[0]["gain"]) or 2.0
    for exp in SWEEP_US:
        cam.set_controls({"AeEnable": False, "ExposureTime": exp, "AnalogueGain": gain})
        time.sleep(0.6)
        record(f"fixed {exp}", *grab())
    cam.stop()
    cam.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=1))

    fixed = {r["mode"]: r["amp"] for r in report if r["mode"].startswith("fixed")}
    floor = min(fixed.values())
    null120 = fixed["fixed 8333"] <= 2 * floor + 0.5 and fixed["fixed 16667"] <= 2 * floor + 0.5
    null100 = fixed["fixed 10000"] <= 2 * floor + 0.5 and fixed["fixed 20000"] <= 2 * floor + 0.5
    if report[0]["amp"] < 1:
        verdict = "no flicker in this light"
    elif null120 and not null100:
        verdict = "120 Hz flicker: 60 Hz mains, the default --mains 60 guard is right"
    elif null100 and not null120:
        verdict = "100 Hz flicker: 50 Hz mains, start the booth with --mains 50"
    else:
        verdict = "flicker that no exposure nulls: likely a PWM LED driver; light the subject with your own lamp"
    print(f"\nverdict: {verdict}\nframes and report in {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="only measure the booth's own settings")
    args = ap.parse_args()
    if os.path.exists("/proc/1") and os.system("pgrep -f 'photobooth\\.p[y]|camera_preview\\.p[y]' >/dev/null") == 0:
        print("the booth app holds the camera; run `booth.sh stop` first")
        return 2
    return quick() if args.quick else sweep()


if __name__ == "__main__":
    raise SystemExit(main())
