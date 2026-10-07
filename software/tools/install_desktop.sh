#!/usr/bin/env bash
# Run ON THE PI. Installs the photobooth launchers.
#
# ONE EVENT, two desktop icons: the booth, and the lock that opens back into that same booth.
# "Photobooth" + "Carry Lock" on the top row beside the Wastebasket - the booth is an appliance
# and those two are the things you actually tap at an event: start it, and lock it for the bag
# between sessions. (Grimoire was a second event with its own pair; retired 2026-09-30.)
# One icon per EVENT, not per layout: within a booth, which keepsake gets printed is the
# GUEST's choice, made on the confirm screen (--theme pick), not something you pick by
# A lock is not a third thing either - it is the way back into ONE of those two booths, so
# there are exactly as many lock icons as booths, and each carries its booth's own name and
# start command (see EVENTS below: neither is typed twice).
# "Camera Preview" and "Stop Photobooth" are setup and rescue tools, so they live in the
# application menu instead, two clicks away.
#
# Also sets pcmanfm's quick_exec, which is what stops the desktop asking "Execute?"
# every time you tap a launcher.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR" "$APPS_DIR" "$HOME/photobooth/captures"

# entry <basename> <Name> <Comment> <booth.sh subcommand> <icon> <categories> <on-desktop yes|no>
entry() {
  local file="$APPS_DIR/$1.desktop"
  cat > "$file" <<DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=$2
Comment=$3
Exec=$HERE/booth.sh $4
Path=$HERE
Icon=$5
Terminal=false
Categories=$6
Keywords=photobooth;
DESK
  chmod +x "$file"
  if [ "$7" = "yes" ]; then
    cp "$file" "$DESKTOP_DIR/$1.desktop"
    chmod +x "$DESKTOP_DIR/$1.desktop"
  else
    rm -f "$DESKTOP_DIR/$1.desktop"      # clean up icons installed by earlier versions
  fi
}

retire() {                                    # an icon this version no longer installs
  rm -f "$APPS_DIR/$1.desktop" "$DESKTOP_DIR/$1.desktop"
}

# THE ICON RUNS --theme duet (2026-09-30, after the faire): a guest takes THREE photos and then
# chooses, on the confirm screen, between the newest receipt sent in from the Tape Bench
# (~/photobooth/receipt_choices.json) and the saved film strip (~/photobooth/receipt.json). Both
# sides re-read at the start of every session, so changing what the booth offers is a push from the
# bench, never an edit here. The photo count comes from the theme, so the icon passes no --photos.
# For the faire it was "start --theme pick" (two photos on the pinned Maker Mayhem tape, the guest
# picks which photo prints); "start --theme choice" is the stamps-or-film pair. Any of them is one
# edit to BOOTH_START below, because the Carry Lock icon takes the same string and opens back into
# the same booth.
# Either way it stays ONE icon: what varies is what the guest chooses on the confirm screen, never
# which launcher you tap.
retire photobooth-stamps        # 2026-09-21: briefly one icon per layout, now the guest chooses
retire photobooth-film
retire photobooth-grimoire         # 2026-09-30: Grimoire off the desktop, booth/grimoire.py stays
retire photobooth-lock-grimoire

# One NAME and one booth.sh command per event, written once here. The booth icon starts it; the
# lock icon hands the screen back to it on unlock and names it on the unlock ring. Typing the
# theme into the lock entry as well is how the two would quietly drift apart.
BOOTH_NAME="Photobooth"; BOOTH_START="start"

entry photobooth "$BOOTH_NAME" \
      "Tap to start: countdown, three photos, then choose which print comes out" \
      "$BOOTH_START" camera-photo "Graphics;Photography;" yes

# ONE LOCK PER BOOTH (2026-09-25). Carry-lock stops everything and shows a breathing heart; the
# lock names the booth its 3-second hold lets you back into. Before this it let you out onto
# the DESKTOP, and getting back into a booth meant finding its icon behind
# the lock screen you had just dismissed - two taps and a hunt, mid-event, with a guest waiting.
# Now the lock you tapped is the booth you come back to, and the unlock ring says which one.
# `booth.sh lock` with no arguments still quits to the desktop: that is the SSH/rescue path, and
# it is what you want when the lock is the last thing you do before the lid goes on.
entry photobooth-lock "Carry Lock" \
      "Lock the touchscreen for transport: hold to unlock, which opens $BOOTH_NAME" \
      "lock --unlock-to \"$BOOTH_START\" --unlock-name $BOOTH_NAME" \
      system-lock-screen "Graphics;Utility;" yes
entry photobooth-camera-preview "Camera Preview" \
      "Live camera preview with focus readout and a test-photo button" \
      preview camera-photo "Graphics;Photography;" no
entry photobooth-stop "Stop Photobooth" \
      "Close the Photobooth or Camera Preview app, even if it is frozen" \
      stop process-stop "Graphics;Utility;" no

# WHERE THE ICONS SIT. The booth and its lock go on the top row beside the Wastebasket. The
# retired Grimoire pair is still named in PLACED so its stale sections are dropped from the
# config on the way past, not left behind holding cells.
#
# pcmanfm keeps one section per desktop item in
#   ~/.config/pcmanfm/default/desktop-items-0.conf
#       [photobooth.desktop]
#       x=6
#       y=118
# x,y is the top-left of the item's CELL in the DESKTOP WINDOW's coordinates - that window
# starts below the taskbar, so y=6 is the first row, not six pixels down the screen. An item
# with no section is auto-placed into the first free cell, and pcmanfm skips cells a placed
# item already holds. That is how the Wastebasket keeps the corner without us writing anything
# for it: it is a trash:/// item, not a file in ~/Desktop, so it is the one icon on this desktop
# that has no filename to place it by. Leave it unplaced and it falls into the free top-left.
#
# The cell is measured from pcmanfm's own auto-layout at big_icon_size=48 on the 800x480 screen:
# icons landed 112 px apart in a column starting at x=6, four rows to a screen. To re-measure on
# a different screen or icon size, move this file aside, restart the desktop, and read the gaps
# off a screenshot (grim -t png -).
CELL_W=112; CELL_H=112; ORIGIN_X=6; ORIGIN_Y=6
ITEMS="$HOME/.config/pcmanfm/default/desktop-items-0.conf"
PLACED="photobooth.desktop photobooth-grimoire.desktop photobooth-lock.desktop photobooth-lock-grimoire.desktop"

place() {                                     # place <basename> <column> <row>
  printf '[%s.desktop]\nx=%d\ny=%d\n\n' "$1" "$((ORIGIN_X + $2 * CELL_W))" "$((ORIGIN_Y + $3 * CELL_H))"
}

layout_icons() {
  local tmp="$ITEMS.new"
  mkdir -p "$(dirname "$ITEMS")"
  {
    # Keep every section that is not ours - [*] carries the wallpaper, the desktop font and the
    # colours, and anything the user has dragged into place stays where they put it.
    [ -f "$ITEMS" ] && awk -v names="$PLACED" '
        BEGIN { n = split(names, a, " "); for (i = 1; i <= n; i++) drop["[" a[i] "]"] = 1 }
        /^\[/ { skip = ($0 in drop) }
        !skip' "$ITEMS"
    place photobooth               1 0        # top row, next to the Wastebasket
    place photobooth-lock          2 0
  } > "$tmp"
  [ -f "$ITEMS" ] && cp "$ITEMS" "$ITEMS.bak-$(date +%Y%m%d%H%M%S)"
  mv "$tmp" "$ITEMS"
  echo "wrote ${ITEMS/#$HOME/~}: Photobooth and Carry Lock on the top row"
}

restart_desktop() {
  # pcmanfm reads the positions once, at startup, and writes the file back whenever an icon is
  # DRAGGED - not on the way out, so writing the file first and killing it after is safe and
  # the layout is live in a second or two rather than at the next login.
  # pgrep -x, not -f 'pcmanfm --desktop': the -f pattern also matches the shell running this
  # script if it was started with that text on its command line, and killing your own shell
  # halfway through an install is a bad trade for matching a couple more characters.
  if ! pgrep -x pcmanfm >/dev/null; then
    echo "desktop (pcmanfm) not running; the layout applies the next time it starts"
    return 0
  fi
  pkill -x pcmanfm                            # lwrespawn -> pcmanfm-pi brings it straight back
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.5
    pgrep -x pcmanfm >/dev/null && { echo "desktop restarted, icons in place"; return 0; }
  done
  echo "desktop did not come back by itself: run 'pcmanfm --desktop &' or log out and in" >&2
  return 0
}

# pcmanfm draws the Pi desktop. Two settings matter on a touchscreen:
#   quick_exec   - do not prompt "Execute?" before running an executable .desktop file
#   single_click - ONE tap opens an icon. Without it the desktop wants a double-click, and a
#                  touch user falls back to a long press, which is a right-click: that is the
#                  menu offering "Execute". One tap removes the need for it entirely.
# Both keys exist in TWO files and must be set in both:
#   ~/.config/pcmanfm/default/pcmanfm.conf - per-profile, what the Preferences dialog shows
#   ~/.config/libfm/libfm.conf             - shared libfm defaults, and this is the one that
#                                             actually wins at runtime. Setting only pcmanfm.conf
#                                             leaves libfm.conf's single_click=0/quick_exec=0 in
#                                             effect and the long-press behavior persists.
conf_backed_up=""

set_conf() {                                  # set_conf <file> <key> <value>, idempotent
  file="$1"; key="$2"; val="$3"
  mkdir -p "$(dirname "$file")"
  [ -f "$file" ] || printf '[config]\n' > "$file"
  if grep -q "^$key=$val\$" "$file"; then
    echo "$key=$val already set in ${file/#$HOME/~}"
    return
  fi
  cp "$file" "$file.bak-$(date +%Y%m%d%H%M%S)"
  if grep -q "^$key=" "$file"; then
    sed -i "s/^$key=.*/$key=$val/" "$file"
  elif grep -q '^\[config\]' "$file"; then
    sed -i "0,/^\[config\]/s//[config]\n$key=$val/" "$file"
  else
    printf '\n[config]\n%s=%s\n' "$key" "$val" >> "$file"
  fi
  echo "set $key=$val in ${file/#$HOME/~} (backup alongside it); applies when the desktop restarts"
}

for CONF in "$HOME/.config/pcmanfm/default/pcmanfm.conf" "$HOME/.config/libfm/libfm.conf"; do
  set_conf "$CONF" quick_exec 1
  set_conf "$CONF" single_click 1
done

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" || true
layout_icons
restart_desktop            # also what makes the click settings above take effect

echo "desktop top row: Photobooth, Carry Lock (beside the Wastebasket)."
echo "In the application menu as well: Camera Preview, Stop Photobooth."
echo "If the desktop did not restart above, reboot: the click settings and the icon positions"
echo "are both read when pcmanfm starts."
