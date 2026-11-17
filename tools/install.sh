#!/bin/sh
# One-time setup, run ON THE PI as the desktop user (not with sudo):
#
#     ~/flight-tracker/tools/install.sh
#
# Makes tools/run.sh the single way the wall starts:
#   * writes one entry in ~/.config/labwc/autostart
#   * disables any older entry that launched app.py directly, in both the
#     labwc and the lxsession autostart files (a backup of each file it
#     changes is left next to it as *.bak)
# Safe to run again; it never adds a second entry.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
entry="$here/tools/run.sh &"

disable_old() {
    file=$1
    [ -f "$file" ] || return 0
    if grep -q 'app\.py' "$file"; then
        cp "$file" "$file.bak"
        # comment the old launch lines out rather than deleting them
        sed -i '/app\.py/ s/^[[:space:]]*\([^#]\)/# (replaced by tools\/run.sh) \1/' "$file"
        echo "disabled old app.py entry in $file (backup: $file.bak)"
    fi
}

disable_old "$HOME/.config/lxsession/LXDE-pi/autostart"
disable_old "$HOME/.config/labwc/autostart"

auto="$HOME/.config/labwc/autostart"
mkdir -p "$(dirname "$auto")"
touch "$auto"
if grep -qF "$here/tools/run.sh" "$auto"; then
    echo "launcher already present in $auto"
else
    printf '\n# Kona flight tracker wall display\n%s\n' "$entry" >> "$auto"
    echo "added launcher to $auto"
fi
chmod +x "$here/tools/run.sh" "$here/tools/update.sh" "$here/tools/install.sh"

echo
echo "Check the setup now with:   python3 $here/app.py --selftest"
echo "Then reboot to start the wall: sudo reboot"
