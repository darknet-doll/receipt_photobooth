#!/usr/bin/env bash
# Start, stop, and inspect the photobooth apps. Works from the Pi desktop
# (the launcher icons call it) and from an SSH session.
#
#   booth.sh start            # photobooth loop (kills any running booth app first)
#   booth.sh preview          # camera preview + one-photo print app
#   booth.sh stop             # stop whichever booth app is running, even if frozen
#   booth.sh restart          # stop, then start the photobooth loop
#   booth.sh status           # what is running, camera + printer health
#   booth.sh log              # tail the current log
#   booth.sh studio           # Receipt Studio web editor on port 8765 (booth.sh studio stop)
#   booth.sh snapshots        # one unattended --no-print cycle, a PNG per screen in ~/photobooth/snapshots
#   booth.sh screen upside-down  # rotate the touchscreen 180 (portrait / portrait-flipped / landscape); restart the app after
#   booth.sh lock              # carry-lock for transport: stops the booth, pulsing screen + strip, radios off
#   booth.sh lock --unlock-to "start --theme grimoire" --unlock-name Grimoire
#                              # ...and the hold opens THAT booth instead of quitting to the desktop.
#                              # One lock icon per booth passes its own pair (install_desktop.sh).
#   booth.sh radios on|off     # put WiFi/Bluetooth back (any other command does this too)
#
# Logs go to ~/photobooth/logs/. Extra arguments after start/preview are passed
# to the app (e.g. booth.sh start --once, booth.sh preview --no-print).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/photobooth/logs"
mkdir -p "$LOG_DIR"
# Everything that can still be running and hold a resource: the booth, the preview/check tools,
# the carry-lock screen, rpicam, the Receipt Studio web server, and led_check's long modes --
# led_check holds GPIO10, so leaving it running stops carry-lock lighting the strip at all.
APPS='photoboot[h]\.py|camera_previe[w]\.py|camera_chec[k]\.py|carry_loc[k]\.py|receipt_studi[o]\.py|led_chec[k]\.py|rpicam-[a-z]+'

# From SSH the desktop's Wayland display has to be named explicitly.
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

running() { pgrep -f "$APPS" 2>/dev/null; }

stop_apps() {
  local pids
  pids="$(running)" || true
  if [ -z "$pids" ]; then
    echo "nothing running"
    return 0
  fi
  echo "stopping: $(pgrep -af "$APPS" | sed 's/^\([0-9]*\) .*\(photobooth\.py\|camera_preview\.py\|camera_check\.py\|rpicam-[a-z]*\).*/\1 \2/' | tr '\n' ' ')"
  kill $pids 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.5
    [ -z "$(running)" ] && { echo "stopped"; return 0; }
  done
  echo "still running, forcing"
  kill -9 $(running) 2>/dev/null
  sleep 0.5
  [ -z "$(running)" ] && echo "stopped (forced)" || { echo "could not stop"; return 1; }
}

start_app() {
  local script="$1"; shift
  stop_apps >/dev/null
  local log="$LOG_DIR/$(basename "$script" .py).log"
  : > "$log"
  # setsid + nohup so the app survives the launcher/SSH session ending.
  setsid nohup /usr/bin/python3 "$HERE/$script" "$@" >"$log" 2>&1 < /dev/null &
  local pid=$!
  sleep 3
  if kill -0 "$pid" 2>/dev/null; then
    echo "started $script (pid $pid), log: $log"
  else
    echo "failed to start $script; last log lines:"
    grep -v -E 'INFO|WARN' "$log" | tail -8
    return 1
  fi
}

start_studio() {
  local log="$LOG_DIR/receipt_studio.log"
  pkill -f 'receipt_studi[o]\.py' 2>/dev/null && sleep 0.5
  if [ "${1:-}" = "stop" ]; then echo "receipt studio stopped"; return 0; fi
  : > "$log"
  setsid nohup /usr/bin/python3 "$HERE/receipt_studio.py" "$@" >"$log" 2>&1 < /dev/null &
  local pid=$!
  sleep 1.5
  if kill -0 "$pid" 2>/dev/null; then
    head -1 "$log"; echo "log: $log; stop with: booth.sh studio stop"
  else
    echo "failed to start receipt_studio.py:"; tail -5 "$log"; return 1
  fi
}

screen_orientation() {
  local cfg="$HOME/.config/kanshi/config" tr
  case "${1:-}" in
    portrait)         tr=90 ;;
    portrait-flipped) tr=270 ;;
    landscape)        tr=normal ;;
    upside-down)      tr=180 ;;
    "")               wlr-randr 2>/dev/null | grep -E "^[A-Z]|Transform|current"; return 0 ;;
    *) echo "usage: booth.sh screen upside-down|landscape|portrait|portrait-flipped"; return 2 ;;
  esac
  mkdir -p "$(dirname "$cfg")"
  [ -s "$cfg" ] && cp "$cfg" "$cfg.bak"
  printf 'profile booth {\n    output HDMI-A-1 enable mode 800x480@60Hz transform %s\n}\n' "$tr" > "$cfg"
  if pgrep -x kanshi >/dev/null; then
    kanshictl reload 2>/dev/null || kill -HUP "$(pgrep -x kanshi)"
  else
    setsid nohup kanshi >/dev/null 2>&1 < /dev/null &
  fi
  sleep 1.5
  wlr-randr 2>/dev/null | grep -E "Transform|current"
  echo "wrote $cfg (kanshi applies it at every login); the touchscreen follows the output."
  echo "restart the booth app so it lays itself out for the new shape: booth.sh restart"
}

# carry_lock.py soft-blocks the radios and leaves this marker naming what it blocked. An rfkill
# block SURVIVES A REBOOT (systemd-rfkill persists it), so if that app is ever force-killed the Pi
# would come back with no WiFi and no SSH. Every command here except `lock` puts them back first,
# which makes tapping the Photobooth desktop icon a rescue that needs no terminal.
RADIO_MARKER="$LOG_DIR/radios_off"

radios_restore() {
  [ -f "$RADIO_MARKER" ] || return 0
  local name restored=""
  while read -r name; do
    [ -n "$name" ] || continue
    sudo -n rfkill unblock "$name" 2>/dev/null && restored="$restored $name"
  done < "$RADIO_MARKER"
  rm -f "$RADIO_MARKER"
  [ -n "$restored" ] && echo "radios back on:$restored"
  return 0
}

radios() {
  case "${1:-on}" in
    on)  sudo -n rfkill unblock bluetooth 2>/dev/null; sudo -n rfkill unblock wifi 2>/dev/null
         rm -f "$RADIO_MARKER"; echo "radios on (bluetooth, wifi)" ;;
    off) printf 'bluetooth\nwifi\n' > "$RADIO_MARKER"
         sudo -n rfkill block bluetooth 2>/dev/null; sudo -n rfkill block wifi 2>/dev/null
         echo "radios off (bluetooth, wifi) - no SSH until 'booth.sh radios on' or the Photobooth icon" ;;
    *)   echo "usage: booth.sh radios on|off"; return 2 ;;
  esac
}

lock_screen() {
  # Carry-lock is its own tiny app, not a state of the booth: nothing else stays running, so no
  # camera is held open, no LED thread, no printer probe. start_app stops whatever is running first.
  # The desktop icon runs this with Terminal=false, so nothing here reaches a screen - keep a trace.
  # Append to the log directly: `exec > >(tee ...)` holds stdout open and hangs the caller, which
  # over SSH means booth.sh lock never returns and from the icon leaves a stuck process.
  local log prev="" target=""
  # Read --unlock-to out of the arguments WITHOUT consuming any: carry_lock.py is what acts on
  # it, this is only so the log says where the hold goes. Two lock icons differ only in this value.
  for a in "$@"; do [ "$prev" = "--unlock-to" ] && target="$a"; prev="$a"; done
  mkdir -p "$LOG_DIR"; log="$LOG_DIR/lock.log"
  say() { echo "$*"; printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$log"; }
  say "booth.sh lock${target:+ -> booth.sh $target}"
  say "$(start_app carry_lock.py "$@")"
  if [ -n "$target" ]; then
    say "hold the screen for 3s to unlock; it then runs: booth.sh $target"
  else
    say "hold the screen for 3s to unlock; it quits to the desktop, the booth is not running"
  fi
}



status() {
  echo "== apps"
  pgrep -af "$APPS" | sed 's/^\([0-9]*\) .*python3 [^ ]*\/\([a-z_]*\.py\)/\1 \2/' || echo "none running"
  pgrep -f 'receipt_studi[o]\.py' >/dev/null && echo "receipt studio: http://$(hostname).local:8765"
  echo "== display"
  wlopm 2>/dev/null || echo "wlopm not available"
  echo "== camera"
  if [ -n "$(running)" ]; then
    echo "in use by the running app (see log for errors)"
  elif timeout 8 rpicam-hello -t 500 --nopreview >/dev/null 2>&1; then
    echo "ok"
  else
    echo "NOT responding: reseat the ribbon cable (see manual page 2, step 3)"
  fi
  echo "== printer"
  if [ -w /dev/usb/lp0 ]; then echo "ok (/dev/usb/lp0 writable)"
  elif [ -e /dev/usb/lp0 ]; then echo "present but not writable: sudo usermod -aG lp \$USER, then log in again"
  else echo "NOT found: check printer power and USB"; fi
  echo "== last log errors"
  for f in "$LOG_DIR"/*.log; do
    [ -f "$f" ] || continue
    grep -h -i -E "error|traceback|timed out" "$f" | tail -2 | sed "s|^|$(basename "$f"): |"
  done
}

case "${1:-}" in
  # Every command but `lock` puts the radios back first: that is what makes the Photobooth icon
  # a rescue when carry-lock has been killed with WiFi blocked and there is no SSH to fix it.
  start)   radios_restore; shift; start_app photobooth.py "$@" ;;
  preview) radios_restore; shift; start_app camera_preview.py "$@" ;;
  stop)    radios_restore; stop_apps ;;
  restart) radios_restore; shift; start_app photobooth.py "$@" ;;
  status)  radios_restore; status ;;
  studio)  radios_restore; shift; start_studio "$@" ;;   # the web editor is useless without WiFi
  screen)  radios_restore; shift; screen_orientation "$@" ;;
  lock)    shift; lock_screen "$@" ;;
  radios)  shift; radios "$@" ;;
  snapshots) shift; stop_apps >/dev/null; /usr/bin/python3 "$HERE/ui_snapshots.py" "$@" 2>&1 | grep -v -E 'INFO|WARN' ;;
  log)     tail -n 40 "$LOG_DIR"/*.log 2>/dev/null | sed "s|$HOME|~|g" ;;
  *)       sed -n '2,19p' "$0"; exit 2 ;;
esac
