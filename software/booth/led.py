"""Addressable light-strip cues for the photobooth.

Hardware: SK6812 RGBW, 146 pixels, one-wire PIO on GPIO10 -- see
tools/led_check.py for the bench probes this driver setup is copied from
(bpp=4, colour order GRBW, arrows away from the feed).

Modes, picked on the bench 2026-09-18:

    idle       hue sweeps along the strip while the whole strip's brightness
               breathes -- shown whenever nobody is mid-session
    capture    the whole strip flashes flat green fast, right at the shutter
    printing   fast red/purple sparkle-twinkle against a dim purple base,
               while the print job is in flight
    off        strip dark -- carry-lock, so a jostled bag doesn't light up

Runs its own thread so the pygame loop never blocks on LED timing --
``set_mode`` just flips what the thread reads each frame. Missing hardware
(dev laptop, booth/sim.py) degrades to a no-op, the same pattern as
booth/camera.py.
"""
from __future__ import annotations

import colorsys
import math
import random
import threading
import time
from dataclasses import dataclass

try:
    import board
    import neopixel
except ImportError as exc:      # not on a Pi; sim.py runs with the light off
    board = neopixel = None  # type: ignore[assignment]
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

FRAME_DT = 0.02             # thread tick; fast enough for the printing sparkle
IDLE_HUE_RATE = 0.01 / 0.03  # cycles/sec -- the bench-confirmed idle pace, kept independent of FRAME_DT
PRINT_BASE = (10, 0, 12)                       # dim purple background
PRINT_SPARKS = [(200, 0, 0), (140, 0, 180)]    # bright red, bright purple
PRINT_DECAY = 0.55           # fast sparkle-twinkle, confirmed on the bench 2026-09-18
PRINT_SPAWN_CHANCE = 0.35
CAPTURE_GREEN = (0, 200, 0)
CAPTURE_HZ = 6.0


def _lerp(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


@dataclass
class LedSettings:
    pin: int = 10
    count: int = 146         # physical length of the strip, counted on the bench 2026-09-18
    bpp: int = 4
    order: str = "GRBW"


class BoothLed:
    """Background-threaded strip driver with a small set of named modes."""

    def __init__(self, settings: LedSettings | None = None) -> None:
        self.settings = settings or LedSettings()
        self._strip = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._mode = "idle"
        self._revert_at: float | None = None

    @property
    def available(self) -> bool:
        return neopixel is not None

    def set_mode(self, mode: str, duration: float | None = None) -> None:
        """Switch pattern. ``duration``, if given, reverts to idle after that long."""
        with self._lock:
            self._mode = mode
            self._revert_at = (time.monotonic() + duration) if duration else None

    def __enter__(self) -> "BoothLed":
        if not self.available:
            return self
        s = self.settings
        gpio = getattr(board, f"D{s.pin}")
        self._strip = neopixel.NeoPixel(gpio, s.count, bpp=s.bpp,
                                        pixel_order=getattr(neopixel, s.order),
                                        auto_write=False)
        self._thread = threading.Thread(target=self._run, name="led", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._blank()

    def _rgb(self, rgb: tuple[int, int, int]) -> tuple[int, ...]:
        return (*rgb, 0) if self.settings.bpp == 4 else rgb

    def _fill(self, rgb: tuple[int, int, int]) -> None:
        self._strip.fill(self._rgb(rgb))

    def _blank(self) -> None:
        self._fill((0, 0, 0))
        self._strip.show()

    def _run(self) -> None:
        hue_t = 0.0
        last = time.monotonic()
        start = last
        level = [0.0] * self.settings.count
        color = [PRINT_SPARKS[0]] * self.settings.count
        while not self._stop.is_set():
            now = time.monotonic()
            dt, last = now - last, now
            with self._lock:
                mode, revert_at = self._mode, self._revert_at
                if revert_at is not None and now >= revert_at:
                    mode = self._mode = "idle"
                    self._revert_at = None
            if mode == "idle":
                hue_t = self._render_idle(hue_t, dt)
            elif mode == "capture":
                self._render_capture(start)
            elif mode == "printing":
                self._render_printing(level, color)
            else:                    # "off", and anything unrecognised
                self._blank()
            time.sleep(FRAME_DT)

    def _render_idle(self, hue_t: float, dt: float) -> float:
        now = time.monotonic()
        level = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(now * 1.6))
        count = self.settings.count
        for i in range(count):
            hue = (hue_t + i / count) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, level)
            self._strip[i] = self._rgb((int(r * 255), int(g * 255), int(b * 255)))
        self._strip.show()
        return (hue_t + dt * IDLE_HUE_RATE) % 1.0

    def _render_capture(self, start: float) -> None:
        on = int((time.monotonic() - start) * CAPTURE_HZ * 2) % 2 == 0
        self._fill(CAPTURE_GREEN if on else (0, 0, 0))
        self._strip.show()

    def _render_printing(self, level: list[float], color: list[tuple[int, int, int]]) -> None:
        for i in range(self.settings.count):
            if level[i] < 0.05 and random.random() < PRINT_SPAWN_CHANCE:
                level[i] = 1.0
                color[i] = random.choice(PRINT_SPARKS)
            self._strip[i] = self._rgb(_lerp(PRINT_BASE, color[i], level[i]))
            level[i] *= PRINT_DECAY
        self._strip.show()
