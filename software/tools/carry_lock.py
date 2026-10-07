#!/usr/bin/env python3
"""Standalone carry-lock screen: a breathing heart on a near-black screen, nothing else.

    tools/carry_lock.py                # hold the screen 3 s to unlock and quit
    tools/carry_lock.py --period 6     # slower breathe
    tools/carry_lock.py --plain        # no pulse, flat screen
    tools/carry_lock.py --unlock-to "start --theme grimoire"   # unlock straight into a booth

``--unlock-to`` is what makes ONE lock screen serve two events: the desktop carries a lock
icon per booth (Carry Lock -> Photobooth, Grimoire Lock -> Grimoire), each passing the
booth.sh command its own booth is started with. Only the HOLD runs it. Quitting any other
way -- SIGTERM from ``booth.sh stop``, q, Escape -- leaves you on the desktop, so stopping
the lock never starts a booth behind your back.

This is NOT the booth's LOCKED state. The booth's own carry-lock (booth/app.py) keeps
photobooth.py alive with the camera merely paused, which is right when you lock mid-event
and want the session and the printer state preserved. For carrying the booth in a bag
there is nothing to preserve, and the cheapest thing to run is nothing: no camera object
is ever opened, no LED thread, no printer probe, no receipt config. Just pygame drawing
one heart at ``--fps`` frames a second, and one strip write alongside it.

The LED strip breathes the same beat as the heart, from off up to ``--led-level`` of HOT, and
is blanked and released on the way out -- SK6812s hold their last colour with no data arriving,
so leaving without blanking keeps 146 pixels lit for the rest of the carry. ``--no-leds`` keeps
the strip dark and pulses the screen only.

Power: the screen pulse is free (the backlight is lit regardless and an LCD costs the same
behind any frame), but the STRIP is not. Draw scales with pixels x level, and all 146 at full
is amps, not milliamps -- 140 px at full in a mixed pattern measured 7 W on the bench. Hence a
low default peak. Raise it with a meter on the bank, not by eye.

The screen stays lit either way -- the ELECROW RC050S is an external monitor with no
brightness control the Pi can reach, and dropping HDMI makes it paint its own "No Signal"
card with no way back except SSH (see booth/app.py's _enter_locked). So the backlight is
a fixed cost and the pulse on top of it is close to free; what this saves over the booth's
own LOCKED state is the camera and everything else the app holds open.

Exits on a sustained press, on SIGTERM (booth.sh stop), or on q/Escape with a keyboard.
"""
from __future__ import annotations

import argparse
import colorsys
import math
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame  # noqa: E402

from booth import cute  # noqa: E402

HOLD_DEFAULT = 3.0


GAMMA = 2.2          # perceived brightness ~ duty ** (1/GAMMA); a linear ramp reads as a snap-on
PHI = 0.6180339887   # golden ratio: per-pixel dither thresholds that never fall into a pattern


class Strip:
    """The SK6812 strip, breathing in step with the screen. Open it or don't; never crash over it.

    Three things make a dim breathe look steppy, and this class deals with each:

    * **Too few levels.** At a 0.10 peak the channels only span 0-24, so the strip lurches through
      ten-odd values. Fixed by dithering SPATIALLY: each pixel carries a fixed fractional threshold,
      so a target of 5.4 lights 40% of pixels at 6 and the rest at 5 and the eye averages the two.
      Spatial, not temporal - dithering a uniform strip over time would just flicker it.
    * **Linear ramps.** The eye is far more sensitive at the bottom, so equal duty steps are not
      equal brightness steps. Corrected with GAMMA.
    * **Hue swing.** Channels reach zero at different times when scaled linearly, so the colour
      drifts as it fades. The dither keeps fractional values alive instead of truncating them away.

    Dimming is done by lowering colour VALUES, never by PWM on the supply: the booth nulls 120 Hz
    mains flicker with a fixed exposure and a PWM-dimmed lamp adds a rate no exposure can null
    (tools/led_check.py). No camera runs while locked, but the strip is driven at full duty
    everywhere else and there is no reason to make this the exception.

    Power scales with pixels x level: all 146 at full is amps, so ``level`` caps the peak low.
    """

    def __init__(self, level: float, count: int | None = None) -> None:
        self.level = max(0.0, min(1.0, level))
        self.strip = None
        self.count = 0
        self.bpp = 4
        self.status = "strip: not opened"
        try:
            import board
            import neopixel

            from booth.led import LedSettings      # pin/count/order live there, never retyped here
        except ImportError as exc:
            self.status = f"strip: no LED driver here ({exc})"
            return
        cfg = LedSettings()
        self.count = count or cfg.count
        self.bpp = cfg.bpp
        self._dither = [((i + 1) * PHI) % 1.0 for i in range(self.count)]
        try:
            self.strip = neopixel.NeoPixel(getattr(board, f"D{cfg.pin}"), self.count, bpp=cfg.bpp,
                                           pixel_order=getattr(neopixel, cfg.order), auto_write=False)
            self.status = f"strip: {self.count} px on GPIO{cfg.pin}, peak level {self.level:.2f}"
        except Exception as exc:                    # noqa: BLE001 - never block the lock screen
            self.status = f"strip: FAILED to open ({exc})"

    @staticmethod
    def gamma(eased: float) -> float:
        """Perceptual 0-1 in, duty 0-1 out."""
        return max(0.0, min(1.0, eased)) ** GAMMA

    def _q(self, value: float, threshold: float) -> int:
        """Round a fractional channel up or down according to this pixel's threshold."""
        whole = int(value)
        return min(255, whole + (1 if (value - whole) > threshold else 0))

    def show_pixels(self, pixels) -> None:
        """``pixels``: one float (r, g, b) per LED, 0-255. Dithered per pixel, then written once."""
        if self.strip is None:
            return
        try:
            for i, (r, g, b) in enumerate(pixels):
                t = self._dither[i]
                rgb = (self._q(r, t), self._q(g, t), self._q(b, t))
                self.strip[i] = (*rgb, 0) if self.bpp == 4 else rgb
            self.strip.show()
        except Exception:                           # noqa: BLE001 - a dead strip must not end the lock
            self.strip = None

    def show(self, colour: tuple[float, float, float]) -> None:
        """Every pixel the same colour (still dithered, so a dim solid stays smooth)."""
        if self.strip is not None:
            self.show_pixels([colour] * self.count)

    def blank(self) -> None:
        if self.strip is None:
            return
        try:
            self.strip.fill((0,) * self.bpp)
            self.strip.show()
        except Exception:                           # noqa: BLE001
            self.strip = None

    def close(self) -> None:
        """Blank and let the pin go. SK6812s latch, so leaving without this keeps them lit."""
        if self.strip is None:
            return
        self.blank()
        try:
            self.strip.deinit()
        except Exception:                           # noqa: BLE001
            pass
        self.strip = None


BOOTH_SH = Path(__file__).resolve().parent / "booth.sh"


def open_after_unlock(spec: str) -> str:
    """Hand the screen to a booth once it has been held to unlock. Returns a line for the log.

    ``spec`` is a booth.sh command line ("start --theme grimoire"), not a theme name: the lock
    screen has no business knowing what themes exist, and the icon that starts the lock already
    knows which booth it belongs to (tools/install_desktop.sh derives both from one name).

    Detached into its own session because booth.sh's start_app stops any running booth app
    first, and that pattern still matches THIS process for the moment it takes us to exit; a
    child in our session would be killed along with us. stdout is inherited on purpose, so
    booth.sh's own "started/failed to start" line lands in the lock's log where the icon,
    running with Terminal=false, leaves no other trace.
    """
    cmd = [str(BOOTH_SH), *shlex.split(spec)]
    try:
        subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL)
    except (OSError, ValueError) as exc:          # noqa: BLE001 - unlocking must still happen
        return f"unlock: FAILED to start {' '.join(cmd)} ({exc})"
    return f"unlock: {' '.join(cmd)}"


MARKER = Path.home() / "photobooth" / "logs" / "radios_off"


class Radios:
    """Soft-block the radios for the length of the carry, then put them back.

    Nothing in this build uses either one to run: the touchscreen is USB, the camera CSI, the
    printer USB, the strip GPIO. Capture, print and receipt rendering are all local.

    WiFi is the dangerous one and is treated with more care than Bluetooth. An rfkill soft block
    is PERSISTED by systemd-rfkill across a reboot, so a lock app that is force-killed would leave
    the Pi offline even after a power cycle, with SSH gone and only physical access left. Two
    guards: the block is undone in a ``finally``, and a marker file records what we blocked so
    ``booth.sh`` can put the radios back on any later command -- tapping the Photobooth icon is
    therefore a rescue that needs no terminal.

    rfkill lives in /usr/sbin: on sudo's secure_path but NOT on a login PATH, so reads and writes
    both go through ``sudo -n`` or they merely report "command not found".
    """

    def __init__(self, wanted: list[str]) -> None:
        self.blocked: list[str] = []
        self.notes: list[str] = []
        for name in ("bluetooth", "wifi"):
            if name not in wanted:
                self.notes.append(f"{name}: left alone")
                continue
            if self._is_blocked(name) is True:
                self.notes.append(f"{name}: already off")
                continue
            if self._run(["rfkill", "block", name]):
                self.blocked.append(name)
                self.notes.append(f"{name}: off for the carry (restored on unlock)")
            else:
                self.notes.append(f"{name}: could not switch off (needs rfkill + sudo)")
        self._write_marker()

    @property
    def status(self) -> str:
        return "; ".join(self.notes)

    @staticmethod
    def _run(cmd: list[str]) -> bool:
        try:
            return subprocess.run(["sudo", "-n", *cmd], capture_output=True, timeout=5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _is_blocked(name: str) -> bool | None:
        """True/False if we can tell, None if rfkill cannot be read at all."""
        try:
            out = subprocess.run(["sudo", "-n", "rfkill", "list", name],
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return "Soft blocked: yes" in out.stdout

    def _write_marker(self) -> None:
        """Leave a note of what we blocked, so booth.sh can undo it even if we are killed."""
        try:
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            if self.blocked:
                MARKER.write_text("\n".join(self.blocked) + "\n")
            elif MARKER.exists():
                MARKER.unlink()
        except OSError:
            pass

    def restore(self) -> None:
        for name in list(self.blocked):
            self._run(["rfkill", "unblock", name])
            self.blocked.remove(name)
        self._write_marker()


def power_report(strip: "Strip", leds_on: bool, radios: "Radios") -> str:
    """What carry-lock switched off, and what is deliberately still on.

    Written to the log on every start because the desktop icon runs with Terminal=false: without
    this, nobody can ever see what the lock actually did. Honest about the things it cannot fix.
    """
    off = [
        "photobooth app (camera, printer and receipt threads all go with it)",
        "camera: never opened -- no sensor stream, no autofocus, no ISP work",
        "printer: never probed, no USB writes",
        "Receipt Studio web server and led_check, if either was running (booth.sh stops them)",
        radios.status,
    ]
    off.append(f"LED strip: breathing at peak {strip.level:.2f}" if leds_on
               else "LED strip: blanked and released")
    on = [
        "NOTE: with WiFi off there is no SSH. booth.sh start/stop/status puts the radios back, so "
        "tapping the Photobooth icon is the rescue; --keep-wifi skips the block entirely",
        "backlight -- the RC050S has no brightness control the Pi can reach, and dropping HDMI "
        "makes it show its own 'No Signal' card with SSH as the only way back",
        "printer's own 5 V branch off the bank -- not switchable in software, unplug it for a long carry",
        "the desktop compositor, which has to stay up to show this screen",
        "pipewire (~1.8% of one core) -- stopping and restoring user audio services is a moving "
        "part in the recovery path for well under 0.1 W, so it is deliberately left running",
    ]
    return ("carry-lock off: " + "; ".join(off) + "\n"
            + "carry-lock left on: " + "; ".join(on))


def hue_table(steps: int = 512) -> list:
    """Saturated RGB for a full hue turn, precomputed: colorsys per pixel per frame is wasteful."""
    return [tuple(c * 255.0 for c in colorsys.hsv_to_rgb(i / steps, 1.0, 1.0)) for i in range(steps)]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Carry-lock screen: hold to unlock, nothing else running.")
    ap.add_argument("--hold", type=float, default=HOLD_DEFAULT, metavar="SECONDS",
                    help=f"how long to hold the screen to unlock (default {HOLD_DEFAULT})")
    ap.add_argument("--period", type=float, default=4.0, metavar="SECONDS",
                    help="seconds per breathe, peak to peak (default 4)")
    ap.add_argument("--fps", type=int, default=30,
                    help="frames a second (default 30; below ~24 a dim strip breathe visibly steps)")
    ap.add_argument("--radius", type=float, default=38.0, help="heart radius at the trough (default 38)")
    ap.add_argument("--range", dest="rng", type=float, nargs=2, default=(0.12, 0.85), metavar=("LO", "HI"),
                    help="how far the heart moves from INK toward PINK (default 0.12 0.85)")
    ap.add_argument("--plain", action="store_true", help="no pulse, just a flat screen")
    ap.add_argument("--unlock-to", metavar="BOOTH_SH_ARGS", default="",
                    help="on a hold-to-unlock, run booth.sh with these arguments instead of "
                         "quitting to the desktop (e.g. --unlock-to \"start --theme grimoire\"). "
                         "Only the hold does this: stop, q and Escape still quit to the desktop")
    ap.add_argument("--unlock-name", metavar="NAME", default="",
                    help="what the unlock ring says it opens (e.g. Grimoire). Two lock icons look "
                         "identical once the screen is dark, so the ring names the booth the hold "
                         "is about to start; without it the ring reads \"hold to unlock\"")
    ap.add_argument("--led-level", type=float, default=0.10, metavar="0-1",
                    help="peak strip brightness (default 0.10; the whole strip at full is amps, "
                         "so raise this with a meter on the bank, not by eye)")
    ap.add_argument("--no-leds", action="store_true", help="keep the strip dark; pulse the screen only")
    ap.add_argument("--keep-bluetooth", action="store_true",
                    help="leave the Bluetooth radio alone; by default it is soft-blocked for the "
                         "carry and unblocked again on unlock")
    ap.add_argument("--keep-wifi", action="store_true",
                    help="leave WiFi alone. By default it is soft-blocked too, which means NO SSH "
                         "for the duration -- booth.sh start/stop/status restores it, so the "
                         "Photobooth icon is the way back if this app is ever force-killed")
    ap.add_argument("--solid", action="store_true",
                    help="one pink for the whole strip instead of the default hue sweep. Measurably "
                         "steppier at low levels: with every pixel on the same value, each "
                         "quantisation step lands strip-wide at once instead of being spread "
                         "across neighbours (4.00 / 20 levels undithered, 1.78 / 56 solid, "
                         "1.51 / 97 rainbow, over one breathe at level 0.10)")
    ap.add_argument("--rainbow-turns", type=float, default=1.0, metavar="N",
                    help="hue turns across the strip's length (default 1.0)")
    ap.add_argument("--rainbow-drift", type=float, default=0.04, metavar="TURNS_PER_SEC",
                    help="how fast the hue pattern travels along the strip (default 0.04)")
    return ap


class CarryLock:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.running = True
        self.unlocked = False           # held to unlock, as opposed to stopped or quit
        self.hold_start: float | None = None
        self.started = time.monotonic()

    def _init_screen(self) -> None:
        pygame.init()
        pygame.mouse.set_visible(False)
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.width, self.height = self.screen.get_size()
        pygame.display.set_caption("Carry Lock")
        self.font = cute.font(30)

    def _events(self, now: float) -> None:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_q, pygame.K_ESCAPE):
                self.running = False
            elif ev.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
                if self.hold_start is None:
                    self.hold_start = now
            elif ev.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
                self.hold_start = None      # a jostle lets go long before the hold is up

    def _eased(self, now: float) -> float:
        """0 at the trough, 1 at the peak, cosine-eased so the turns have no corner."""
        return (1 - math.cos(2 * math.pi * (now - self.started) / max(0.5, self.args.period))) / 2

    def _draw_pulse(self, eased: float) -> None:
        lo, hi = self.args.rng
        level = lo + (hi - lo) * eased
        colour = tuple(int(a + (b - a) * level) for a, b in zip(cute.INK, cute.HOT))
        cute.heart(self.screen, (self.width // 2, self.height // 2),
                   self.args.radius * (1 + 0.22 * eased), colour)

    def _pulse_strip(self, eased: float, now: float) -> None:
        """The strip breathes the heart's beat, gamma-corrected so the ramp is perceptually even."""
        level = self.args.led_level * Strip.gamma(eased)
        if self.args.solid or not self._hues:      # no table -> solid, never a divide by zero
            self.strip.show(tuple(c * level for c in cute.HOT))
            return
        n = max(1, self.strip.count)
        steps = len(self._hues)
        drift = now * self.args.rainbow_drift
        span = self.args.rainbow_turns
        self._strip_buf = [
            tuple(c * level for c in self._hues[int(((i / n) * span + drift) * steps) % steps])
            for i in range(n)
        ]
        self.strip.show_pixels(self._strip_buf)

    def _draw_unlock(self, held: float) -> None:
        cute.ring(self.screen, (self.width // 2, self.height // 2), 70,
                  held / self.args.hold, cute.MINT_TRACK, cute.HOT, 8)
        label = f"hold to open {self.args.unlock_name}" if self.args.unlock_name else "hold to unlock"
        t = self.font.render(label, True, cute.WHITE)
        self.screen.blit(t, t.get_rect(center=(self.width // 2, self.height // 2 + 110)))

    def run(self) -> int:
        self.strip = Strip(0.0 if self.args.no_leds else self.args.led_level)
        self._hues = [] if self.args.solid else hue_table()
        wanted = ([] if self.args.keep_bluetooth else ["bluetooth"]) + \
                 ([] if self.args.keep_wifi else ["wifi"])
        self.radios = Radios(wanted)
        print(self.strip.status, flush=True)
        print(power_report(self.strip, not self.args.no_leds, self.radios), flush=True)
        print(f"unlock: hold {self.args.hold:g}s, then "
              + (f"booth.sh {self.args.unlock_to}" if self.args.unlock_to else "quit to the desktop"),
              flush=True)
        if self.args.no_leds:
            self.strip.blank()
        self._init_screen()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        clock = pygame.time.Clock()
        try:
            while self.running:
                now = time.monotonic()
                self._events(now)
                self.screen.fill(cute.INK)
                eased = self._eased(now)
                if self.hold_start is not None:
                    held = now - self.hold_start
                    if held >= self.args.hold:
                        self.unlocked = True
                        self.running = False
                        break
                    self._draw_unlock(held)
                elif not self.args.plain:
                    self._draw_pulse(eased)
                if not self.args.no_leds:
                    self._pulse_strip(eased, now)
                pygame.display.flip()
                # A dark screen still needs frames while the strip breathes, so --plain only drops the
                # rate when the strip is off too; otherwise the strip is what sets the floor.
                idle_fps = self.args.fps if (not self.args.plain or not self.args.no_leds) else 5
                clock.tick(30 if self.hold_start is not None else idle_fps)
        finally:
            # Always, even on a crash: SK6812s latch, so a strip left lit burns the whole carry,
            # and a radio left blocked outlives the lock -- an rfkill block survives a reboot.
            self.strip.close()
            self.radios.restore()
        pygame.quit()
        # After the strip is blanked and the radios are back, never before: the booth needs the
        # WiFi it is about to be logged about, and a strip still held here would be fought over.
        if self.unlocked and self.args.unlock_to:
            print(open_after_unlock(self.args.unlock_to), flush=True)
        return 0


def main(argv: list[str] | None = None) -> int:
    return CarryLock(build_parser().parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
