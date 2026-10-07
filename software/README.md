# Software

The booth app is Python on a Raspberry Pi 5: a fullscreen pygame UI on the touchscreen, picamera2 for the
camera, and ESC/POS written straight to the thermal printer. It needs no printer driver and no CUPS.

**What a guest sees:** a mirrored live preview and TAP TO START. Then three 3-2-1 countdowns, each with a
flash and a still. The receipt appears with **PRINT** and **CANCEL**; it prints on PRINT, or after 20 s if
the guest walks off. Then "take your receipt!" and back to the start.

Every session is saved on the Pi under `~/photobooth/captures/<timestamp>/`: the photos plus the
receipt PNG.

## Try it on a laptop first

```bash
software/tools/simulate.sh            # Mac or PC: the real UI in a window, synthetic camera
software/tools/simulate.sh --help     # every booth option, plus --photos-dir to use your own pictures
```

The first run makes a private virtualenv (`software/.venv-sim`) with pygame, Pillow and numpy using
[uv](https://docs.astral.sh/uv/). Without uv, run `python3 -m pip install -r software/requirements.txt` and
then `python3 software/tools/simulate.py`. Prints are saved as PNGs under `~/photobooth/sim/`.

## Set up the Pi

Tested on **Raspberry Pi OS Trixie (Debian 13), 64-bit, with the desktop**, Python 3.13. picamera2, pygame,
Pillow and numpy come with the OS image.

1. **Flash** Raspberry Pi OS (64-bit, with desktop) with Raspberry Pi Imager. Set a username, Wi-Fi and SSH
   in the Imager's settings.
2. **Get the code onto the Pi.** Either clone this repo on the Pi, or from your computer on the same network:

   ```bash
   PI=youruser@raspberrypi.local software/tools/deploy.sh
   ```

   `deploy.sh` copies `software/` to `~/photobooth/software` on the Pi and installs the desktop icons.
   After a `git clone`, run `software/tools/install_desktop.sh` on the Pi yourself.
3. **Optional: OpenCV**, only for the face-tracking experiment (`tools/face_booth_sim.py`, which draws squares
   round faces). The booth itself doesn't need it.

   ```bash
   sudo apt install python3-opencv
   ```

4. **Printer access.** The printer appears as `/dev/usb/lp0`, owned by group `lp`. CUPS polls it and briefly
   unplugs it, so turn CUPS off:

   ```bash
   sudo usermod -aG lp $USER          # then log out and back in
   sudo systemctl disable --now cups cups.socket cups.path && sudo systemctl mask cups
   ```

5. **Clock.** Receipts are stamped with the Pi's time. Set your own time zone:
   `sudo raspi-config` → Localisation Options → Timezone.
6. **Check the hardware:**

   ```bash
   cd ~/photobooth/software
   python3 tools/camera_check.py --show 8      # 8 s live preview on the touchscreen, then one still
   python3 tools/printer_check.py              # device check, a line of text and a test pattern
   python3 tools/flicker_check.py              # finds the lamp flicker rate (sets --mains)
   ```

   Over SSH, name the desktop's display first:
   `export WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000`.
7. **Optional LED strip** (see `hardware/README.md` for the wiring):

   ```bash
   pip install --break-system-packages adafruit-blinka-raspberry-pi5-neopixel adafruit-circuitpython-neopixel
   python3 tools/led_check.py --white
   ```

## Run it

Tap the **Photobooth** icon on the Pi's desktop. Or, from SSH or a terminal:

```bash
tools/booth.sh start              # the photobooth (stops any running booth first)
tools/booth.sh stop
tools/booth.sh status             # what is running, camera + printer health
tools/booth.sh log                # follow the current log
tools/booth.sh screen upside-down # rotate the screen (portrait / portrait-flipped / landscape / upside-down)
tools/booth.sh lock               # carry lock for transport: booth stopped, Wi-Fi + Bluetooth off; hold the screen 3 s to unlock
tools/booth.sh radios on          # Wi-Fi + Bluetooth back on (any other booth.sh command does this too)
```

Everything after `start` is passed to the app (`python3 tools/photobooth.py --help` lists it all):

| Option | Effect |
|---|---|
| `--theme receipt` (default) | register-tape receipt, each photo a line item |
| `--theme classic` | a plain titled photo strip |
| `--theme film` | 35 mm film strip with names and date up the rail |
| `--theme stamp` | postage stamps, the date over one and the names over the next |
| `--theme grimoire` | a spellbook cover; the guest picks which of two photos goes in its window |
| `--theme birthday` / `--theme mayhem` | example single-photo receipts |
| `--photos N`, `--countdown S` | photos per session, seconds per countdown |
| `--title TEXT` | the shop name on the receipt |
| `--confirm-timeout S` | auto-print delay on the PRINT/CANCEL screen (default 20; 0 waits for a tap) |
| `--no-print` | save the receipt PNG but don't print (for testing without paper) |
| `--hflip`, `--vflip`, `--no-mirror` | camera mounting and preview mirroring |
| `--mains 60\|50\|0` | hold exposure to the mains flicker period of your lights (60 Hz in North America, 50 Hz in most other countries) |

Keyboard: Space or Enter starts a session, Escape cancels, Q quits.

## Design your own receipt

- **Tape Bench** (in a browser, nothing to install): https://darknet-doll.github.io/receipt_photobooth/.
  Design it, press **Download receipt.json**, and copy the file to `~/photobooth/receipt.json` on the Pi.
- **Receipt Studio** (on the Pi): `tools/booth.sh studio`, then open `http://raspberrypi.local:8765` from any
  device on the network. It previews with your real photos and can send a test print.
- **In code:** every string on the receipt is a field of `ReceiptSettings` in `booth/receipt.py`.

The booth re-reads `~/photobooth/receipt.json` at the start of every session, so changes need no restart.

## What's where

```text
software/
├── booth/                the app
│   ├── app.py            the fullscreen loop: preview, countdown, capture, confirm, print
│   ├── camera.py         picamera2: smooth preview + full-resolution stills, flicker guard
│   ├── printer.py        ESC/POS over /dev/usb/lp0: raster bands, dithering, feed
│   ├── receipt.py        the receipt layout and its presets
│   ├── strip.py, film.py, stamp.py, grimoire.py   the other layouts
│   ├── faces.py          face-tracking squares, YuNet (models/); used by tools/face_booth_sim.py only
│   ├── led.py            LED strip cues
│   ├── cute.py           the UI's look
│   └── sim.py            the fake camera + printer for the simulator
└── tools/                launch, deploy, install and hardware-check scripts
```

## Troubleshooting

- **No `/dev/usb/lp0`:** the printer isn't powered. Its USB cable carries data only; it needs its own 5 V supply.
- **Prints come out blank or faint:** check the paper is in thermal side out, and that the printer gets at least
  1.5 A while printing.
- **Horizontal bands rolling through photos:** set `--mains` to your country's mains frequency.
- **Taps land in the wrong place, or don't register:** in `~/.config/labwc/rc.xml`, set the touch device's
  `mouseEmulation` to `no`, then log out and back in.
