#!/usr/bin/env python3
"""Addressable light-strip probe for the photobooth. Run on the Pi:

    python3 tools/led_check.py --white          # key light, white die, full duty
    python3 tools/led_check.py --chase          # walk one pixel, counts the strip
    python3 tools/led_check.py --heartbeat      # wiggle-test a suspect data line
    python3 tools/led_check.py --probe          # re-confirm RGBW vs RGB
    python3 tools/led_check.py --scan           # bouncing scanner, finds the strip's end
    python3 tools/led_check.py --off

Strip (confirmed on the bench 2026-09-14): SK6812 **RGBW**, 5 V, one-wire, so
bpp=4 and colour order GRBW. It is **146 pixels** long (counted 2026-09-18) --
this file defaulted to 30 for a long time, which made two thirds of the strip
look dead in every test; pass `--count` only to address less than the whole run. Pads read `+5V DIN GND` in, `+5V DO GND` out; the
arrows must point AWAY from the feed. The fourth die is a real white phosphor,
which renders skin far better than RGB-mixed white and draws about a quarter
the current -- use it, not mixed white, as the key light.

Driver: RP1 PIO via `adafruit-blinka-raspberry-pi5-neopixel`, which generates
the waveform in hardware. The old `rpi_ws281x` PWM/DMA path does not exist on
a Pi 5. The SPI route (`neopixel_spi` on GPIO10/MOSI) also works but ties up
five header pins for nothing, so PIO is preferred and any GPIO will do.

WIRING -- the ground bond is not optional:

    strip +5V  -> its own 5 V supply (never the Pi's 5 V pins)
    strip GND  -> supply -  AND  a Pi GND pin, as a wire paired with the data
    strip DIN  -> the --pin GPIO, short run, through 330-470 ohm

That second line cost a full bench session. The data signal is a voltage
measured against ground: the Pi swings 0-3.3 V against ITS ground while the
strip decides high-vs-low against the SUPPLY's ground. With the two references
untied the signal floats on stray coupling and you get bit-level corruption
that mimics every other fault -- values mangled, colours in channels never
sent, frames latching only sometimes, the display freezing on its last good
frame, and the symptoms changing whenever anything is touched. None of the
plausible software causes (reset gap, byte alignment, MOSI idle state, bit
timing, SPI vs PIO) matter until the grounds are bonded. Meter Pi GND to
supply negative and expect a dead short before debugging anything else.

Power: an SK6812 RGBW pixel draws up to ~80 mA with all four dies at full,
~20 mA on the white die alone. 30 pixels of white is ~0.6 A; a full 5 m reel
at full RGBW would be ~24 A, far past what the strip's own copper can carry
from one end. Cut to length before any bright test, and give the strip its own
supply branch. Measured on the bench 2026-09-18: 140 pixels at full brightness
in a mixed red/green/blue/yellow/white pattern drew **7 W** (~1.4 A) from the
bank with no sag, so the worst-case arithmetic above is far above real use.

Capture note: the booth nulls 120 Hz mains flicker with a fixed exposure
(`AeFlickerPeriod`, see booth/camera.py). A PWM-dimmed lamp adds a second rate
no exposure can null, so the strip is driven at full duty here -- dim by
lowering colour values, never by PWM on the supply. Save fades for the idle
state between sessions.
"""
from __future__ import annotations

import argparse
import sys
import time

DEFAULT_PIN = 10


def make_strip(count: int, pin: int, bpp: int, order: str):
    try:
        import board
        import neopixel
    except ImportError:
        sys.exit(
            "neopixel is missing. On the Pi:\n"
            "    sudo pip install --break-system-packages \\\n"
            "        adafruit-circuitpython-neopixel \\\n"
            "        adafruit-blinka-raspberry-pi5-neopixel"
        )
    try:
        gpio = getattr(board, f"D{pin}")
    except AttributeError:
        sys.exit(f"no such pin: D{pin}")
    return neopixel.NeoPixel(
        gpio, count, bpp=bpp,
        pixel_order=getattr(neopixel, order),
        auto_write=False,
    )


def _palette(bpp: int):
    """(off, white, red, green) in the library's (R,G,B,W) tuple form.

    NOTE the tuples are R,G,B[,W] regardless of --order: the library reorders
    to wire order itself. Getting this backwards silently swaps red and green.
    """
    if bpp == 4:
        return (0, 0, 0, 0), (0, 0, 0, 255), (200, 0, 0, 0), (0, 200, 0, 0)
    return (0, 0, 0), (255, 255, 255), (200, 0, 0), (0, 200, 0)


def probe(strip, bpp: int) -> None:
    """Pixel 1 blank, pixel 2 every channel full.

    4-byte chips -> ONE WHITE at position 2.
    3-byte chips -> the stream is chopped into threes and slides out of
    phase, giving MAGENTA then YELLOW. Leading the frame with a blank pixel
    keeps the opening bits away from the line's idle level.
    """
    off, _, _, _ = _palette(bpp)
    strip.fill(off)
    strip[1] = (255, 255, 255, 255) if bpp == 4 else (255, 255, 255)
    strip.show()
    print("ONE WHITE at position 2  -> bpp 4, GRBW (SK6812 RGBW)")
    print("MAGENTA then YELLOW      -> bpp 3, GRB  (WS2812B)")
    time.sleep(20)
    strip.fill(off)
    strip.show()


def chase(strip, count: int, bpp: int, delay: float) -> None:
    """Walk one lit pixel down the strip: confirms the real LED count."""
    off, white, _, _ = _palette(bpp)
    for i in range(count):
        strip.fill(off)
        strip[i] = white
        strip.show()
        print(f"\rpixel {i + 1}/{count}", end="", flush=True)
        time.sleep(delay)
    print("\nif the last pixel is not the physical end, re-run with the count you saw.")
    strip.fill(off)
    strip.show()


def heartbeat(strip, bpp: int, seconds: int) -> None:
    """Alternate pixel 1 between two colours so a bad data line is obvious.

    steady alternation  -> link is solid
    stalls, then jumps  -> intermittent connection
    black in between    -> frames of zeros are latching; data is corrupted
    never changes       -> nothing arriving; the chain holds its last frame
    """
    off, _, red, green = _palette(bpp)
    print(f"{seconds}s heartbeat - wiggle the data wire at both ends")
    end = time.time() + seconds
    i = 0
    while time.time() < end:
        strip.fill(off)
        strip[0] = red if i % 2 == 0 else green
        strip.show()
        i += 1
        time.sleep(0.5)
    strip.fill(off)
    strip.show()
    print(f"sent {i} frames")


def scan(strip, count: int, bpp: int, delay: float, seconds: float) -> None:
    """Larson scanner: red head with green then blue trailing, bouncing.

    Only three pixels are lit at any moment, so the draw is ~60 mA over idle
    no matter how far it travels -- which makes it a safe way to find the
    physical end of an uncut reel. If the head turns around before the count,
    the strip is shorter than `count`.
    """
    off, _, red, green = _palette(bpp)
    blue = (0, 0, 255, 0) if bpp == 4 else (0, 0, 255)
    pos, d = 0, 1
    print(f"scanner across {count} pixels, {delay*1000:.0f}ms/step "
          f"(~{count*delay:.1f}s per sweep), {seconds:.0f}s")
    end = time.time() + seconds
    while time.time() < end:
        strip.fill(off)
        strip[pos] = red
        t1, t2 = pos - d, pos - 2 * d
        if 0 <= t1 < count:
            strip[t1] = green
        if 0 <= t2 < count:
            strip[t2] = blue
        strip.show()
        pos += d
        if pos >= count - 1:
            pos, d = count - 1, -1
        elif pos <= 0:
            pos, d = 0, 1
        time.sleep(delay)
    strip.fill(off)
    strip.show()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--white", action="store_true", help="key light, full duty")
    ap.add_argument("--chase", action="store_true", help="walk one pixel, counts the strip")
    ap.add_argument("--heartbeat", type=int, metavar="SECS", nargs="?", const=60,
                    help="alternate one pixel to test a suspect data line")
    ap.add_argument("--probe", action="store_true", help="re-confirm RGBW vs RGB")
    ap.add_argument("--scan", type=float, metavar="SECS", nargs="?", const=40.0,
                    help="bouncing scanner; safe way to find the strip's physical end")
    ap.add_argument("--off", action="store_true", help="blank the strip")
    ap.add_argument("--count", type=int, default=146, help="pixels on the strip (default 146, the physical length)")
    ap.add_argument("--pin", type=int, default=DEFAULT_PIN, help="BCM data pin (default 10)")
    ap.add_argument("--bpp", type=int, default=4, choices=(3, 4), help="bytes per pixel")
    ap.add_argument("--order", default="GRBW", help="colour order, e.g. GRBW or GRB")
    ap.add_argument("--level", type=int, default=255, help="0-255 white level for --white")
    ap.add_argument("--delay", type=float, default=0.025,
                    help="seconds per step in --chase / --scan")
    args = ap.parse_args()

    if args.bpp == 3 and args.order.endswith("W"):
        sys.exit("--bpp 3 needs a three-letter order such as GRB")

    count = max(args.count, 2)
    strip = make_strip(count, args.pin, args.bpp, args.order)
    off, _, _, _ = _palette(args.bpp)

    if args.off:
        strip.fill(off)
        strip.show()
        return 0

    if args.probe:
        probe(strip, args.bpp)
        return 0

    if args.scan:
        scan(strip, args.count, args.bpp, args.delay, args.scan)
        return 0

    if args.heartbeat:
        heartbeat(strip, args.bpp, args.heartbeat)
        return 0

    if args.chase:
        chase(strip, args.count, args.bpp, args.delay)
        return 0

    if args.white:
        lvl = max(0, min(255, args.level))
        strip.fill(off)
        for i in range(args.count):
            strip[i] = (0, 0, 0, lvl) if args.bpp == 4 else (lvl, lvl, lvl)
        strip.show()
        per_led = 20 if args.bpp == 4 else 60
        draw = args.count * per_led * (lvl / 255) / 1000
        print(f"{args.count} pixels at white {lvl}/255 -> roughly {draw:.2f} A")
        print("full duty, no PWM: safe to shoot against. --off clears.")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
