"""Camera access for the photobooth, built on Picamera2 and libcamera.

Design:

* The sensor runs in its 2304x1296 mode while previewing so the preview stream
  stays smooth (the full 4608x2592 mode only manages ~14 fps).
* Each still is taken with ``switch_mode_and_capture_file`` at full resolution,
  then the camera returns to the preview mode. The mode switch costs well under
  a second, which is fine inside a countdown.
* Autofocus runs continuously during the preview. When a still is requested the
  lens position the preview settled on is locked with ``AfMode.Manual`` for the
  capture, so a failed or hunting focus cycle can never stall the capture.
* Preview frames are returned as RGB numpy arrays (Picamera2 calls this layout
  ``BGR888``), ready for ``pygame.surfarray.make_surface`` or Pillow.
* ``hflip``/``vflip`` describe how the module is physically mounted and apply to
  preview and stills alike. Mirroring the on-screen preview like a mirror is a
  UI concern and is left to the caller (flip the array, not the camera).

Verified on Raspberry Pi 5 / Raspberry Pi OS Trixie with Picamera2 0.3.32 and an
Arducam IMX708 (Camera Module 3 clone) on 2026-09-05.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

try:
    from libcamera import Transform, controls
    from picamera2 import Picamera2
except ImportError as exc:      # not on a Raspberry Pi; booth/sim.py provides a FakeCamera instead
    Transform = controls = Picamera2 = None  # type: ignore[assignment]
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

FRAME_TIMEOUT = 3.0                # seconds without a frame before CameraTimeout
STILL_TIMEOUT = 10.0
PREVIEW_SIZE = (800, 480)          # matches the ELECROW 5" touchscreen
PREVIEW_SENSOR_MODE = (2304, 1296)  # IMX708 2x2 binned mode, up to 56 fps
CAPTURE_SIZE = (4608, 2592)         # IMX708 full resolution


class CameraTimeout(RuntimeError):
    """The sensor stopped delivering frames (usually a loose CSI ribbon cable)."""


@dataclass
class CameraSettings:
    preview_size: tuple[int, int] = PREVIEW_SIZE
    frame_timeout: float = FRAME_TIMEOUT
    still_timeout: float = STILL_TIMEOUT
    capture_size: tuple[int, int] = CAPTURE_SIZE
    hflip: bool = False
    vflip: bool = False
    autofocus: bool = True
    jpeg_quality: int = 93
    # Period of the room lighting's flicker in microseconds, or 0 to disable.
    # Mains-driven lamps flicker at twice the mains frequency: 8333 for 60 Hz
    # mains (Americas), 10000 for 50 Hz. libcamera's auto-exposure then only
    # picks exposures that are whole multiples of the period, so rolling-shutter
    # bands cannot form. Measured on the bench 2026-09-09: residual banding fell
    # from 11.5 to 0.4 with 8333 under a 60 Hz ceiling lamp.
    flicker_period_us: int = 8333
    # Expose for the person, not the room (added 2026-09-09). A booth subject is
    # always in the middle of the frame, and venues put windows and lamps
    # behind them; with plain averaging a backlit face came out at a quarter
    # of full scale and printed as shadow. CentreWeighted metering biases the
    # AGC to the middle, and the "Shadows" constraint (from the Pi tuning file)
    # stops the darker half of the frame dropping below its floor even if that
    # clips a bright background. Set expose_for_subject=False for plain metering.
    expose_for_subject: bool = True
    # Extra libcamera controls applied while previewing, e.g. {"Sharpness": 1.2}.
    preview_controls: dict[str, Any] = field(default_factory=dict)


class BoothCamera:
    """Thin, photobooth-shaped wrapper around Picamera2."""

    def __init__(self, settings: CameraSettings | None = None) -> None:
        self.settings = settings or CameraSettings()
        self._cam: Picamera2 | None = None
        self._preview_config: dict | None = None
        self._last_metadata: dict = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._cam is not None:
            return
        if Picamera2 is None:
            raise RuntimeError(f"picamera2 is not available on this machine ({_IMPORT_ERROR}); "
                               "run tools/simulate.py for a fake camera")
        cam = Picamera2()
        transform = Transform(hflip=int(self.settings.hflip), vflip=int(self.settings.vflip))
        self._preview_config = cam.create_preview_configuration(
            main={"size": self.settings.preview_size, "format": "BGR888"},
            raw={"size": PREVIEW_SENSOR_MODE},
            transform=transform,
            buffer_count=4,
        )
        cam.configure(self._preview_config)
        cam.options["quality"] = self.settings.jpeg_quality
        cam.start()
        self._cam = cam
        self._apply_preview_controls()

    def stop(self) -> None:
        if self._cam is None:
            return
        try:
            self._cam.stop()
        finally:
            self._cam.close()
            self._cam = None

    def __enter__(self) -> "BoothCamera":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def cam(self) -> Picamera2:
        if self._cam is None:
            raise RuntimeError("BoothCamera.start() has not been called")
        return self._cam

    @property
    def has_autofocus(self) -> bool:
        return "AfMode" in self.cam.camera_controls

    def _flicker_controls(self) -> dict[str, Any]:
        period = int(self.settings.flicker_period_us)
        if period <= 0:
            return {"AeFlickerMode": controls.AeFlickerModeEnum.Off}
        return {"AeFlickerMode": controls.AeFlickerModeEnum.Manual, "AeFlickerPeriod": period}

    def _exposure_controls(self) -> dict[str, Any]:
        ctrls = self._flicker_controls()
        if self.settings.expose_for_subject:
            ctrls["AeMeteringMode"] = controls.AeMeteringModeEnum.CentreWeighted
            ctrls["AeConstraintMode"] = controls.AeConstraintModeEnum.Shadows
        return ctrls

    def _apply_preview_controls(self) -> None:
        ctrls: dict[str, Any] = dict(self.settings.preview_controls)
        # The still inherits the AGC state the preview converged on, so the
        # subject bias has to run during the preview too, not only on the still.
        ctrls.update(self._exposure_controls())
        if self.settings.autofocus and self.has_autofocus:
            ctrls["AfMode"] = controls.AfModeEnum.Continuous
            ctrls["AfSpeed"] = controls.AfSpeedEnum.Fast
        if ctrls:
            self.cam.set_controls(ctrls)

    # -- preview -------------------------------------------------------------

    def preview_frame(self) -> np.ndarray:
        """Return the newest preview frame as an (H, W, 3) RGB uint8 array.

        Raises CameraTimeout if no frame arrives within ``frame_timeout`` so a
        UI loop can show an error instead of freezing.
        """
        try:
            request = self.cam.capture_request(wait=self.settings.frame_timeout)
        except TimeoutError as exc:
            raise CameraTimeout(
                f"no camera frame for {self.settings.frame_timeout:.0f}s; check the ribbon cable") from exc
        if request is None:
            raise CameraTimeout("camera returned no frame; check the ribbon cable")
        try:
            frame = request.make_array("main")
            self._last_metadata = request.get_metadata()
        finally:
            request.release()
        return frame

    def metadata(self) -> dict:
        """Metadata from the most recent preview frame (or a fresh one)."""
        if not self._last_metadata:
            try:
                self._last_metadata = self.cam.capture_metadata(wait=self.settings.frame_timeout)
            except TimeoutError as exc:
                raise CameraTimeout(
                    f"no camera metadata for {self.settings.frame_timeout:.0f}s; check the ribbon cable") from exc
        return self._last_metadata

    def focus_score(self) -> int:
        """libcamera's focus figure of merit for the last frame; higher is sharper."""
        return int(self.metadata().get("FocusFoM", 0))

    # -- stills --------------------------------------------------------------

    def capture_still(self, path: str | Path) -> dict:
        """Capture a full-resolution JPEG to ``path`` and return its metadata.

        The preview keeps running before and after; the sensor mode switch is
        handled by Picamera2. Takes roughly 0.5-1 s on a Pi 5.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # The still is its own configuration, so it needs the flicker guard and
        # the subject metering too.
        still_controls: dict[str, Any] = self._exposure_controls()
        if self.has_autofocus:
            # Freeze focus where continuous AF left it so the still cannot hunt.
            lens = self.metadata().get("LensPosition")
            if lens is not None:
                still_controls = {
                    "AfMode": controls.AfModeEnum.Manual,
                    "LensPosition": float(lens),
                }

        transform = Transform(hflip=int(self.settings.hflip), vflip=int(self.settings.vflip))
        still_config = self.cam.create_still_configuration(
            main={"size": self.settings.capture_size},
            transform=transform,
            controls=still_controls,
            buffer_count=1,
        )
        started = time.monotonic()
        try:
            metadata = self.cam.switch_mode_and_capture_file(
                still_config, str(path), wait=self.settings.still_timeout)
        except TimeoutError as exc:
            raise CameraTimeout(
                f"still capture timed out after {self.settings.still_timeout:.0f}s; check the ribbon cable") from exc
        metadata = dict(metadata or {})
        metadata["CaptureSeconds"] = round(time.monotonic() - started, 3)
        metadata["Path"] = str(path)

        # switch_mode_and_capture_file restores the preview config but not the
        # runtime controls, so hand focus back to the continuous algorithm.
        self._apply_preview_controls()
        self._last_metadata = {}
        return metadata


def describe_cameras() -> list[dict]:
    """Return the camera list libcamera sees, without opening a camera."""
    if Picamera2 is None:
        return []
    return Picamera2.global_camera_info()
