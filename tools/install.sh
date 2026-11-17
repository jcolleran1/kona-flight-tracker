#!/bin/sh
# One-time setup, run ON THE PI as the desktop user (not with sudo):
#
#     ~/flight-tracker/tools/install.sh
#
# Makes tools/run.sh the single way the wall starts:
#   * removes every line that launches app.py directly from the lxsession
#     and labwc autostart files, after saving a backup copy of each file it
#     changes next to it (autostart.bak)
#   * writes one launcher entry in ~/.config/labwc/autostart
#
# The lxsession entry matters most. A line like
#     @python3 /home/colleran/flight-tracker/app.py
# starts a second copy, and the leading @ tells lxsession to start it again
# every time it exits. With the single-instance lock that copy exits at once,
# so lxsession would respawn it in a tight loop forever.
#
# Safe to run again: it never adds a second entry and never overwrites the
# first backup.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
entry="$here/tools/run.sh &"

# Lines that start this app with python, in either autostart syntax:
#   @python3 /home/colleran/flight-tracker/app.py      (lxsession)
#   python3 /home/colleran/flight-tracker/app.py &     (labwc)
OLD_LAUNCH='python[0-9.]*[[:space:]].*app\.py'

remove_old() {
    file=$1
    [ -f "$file" ] || return 0
    grep -q "$OLD_LAUNCH" "$file" || return 0
    # Back up first. Keep the very first backup: it is the pre-install original.
    backup="$file.bak"
    if [ -e "$backup" ]; then
        backup="$file.bak.$(date +%Y%m%d-%H%M%S)"
    fi
    cp -p "$file" "$backup"
    sed -i "/$OLD_LAUNCH/d" "$file"
    echo "removed the old app.py launch line from $file (backup: $backup)"
}

remove_old "$HOME/.config/lxsession/LXDE-pi/autostart"
remove_old "$HOME/.config/labwc/autostart"

# System-wide autostart files need sudo, so they are only reported.
for sys_file in /etc/xdg/lxsession/LXDE-pi/autostart /etc/xdg/labwc/autostart; do
    if [ -f "$sys_file" ] && grep -q "$OLD_LAUNCH" "$sys_file"; then
        echo "WARNING: $sys_file also launches app.py. Remove that line by hand:"
        echo "    sudo nano $sys_file"
    fi
done

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
