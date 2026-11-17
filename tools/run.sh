#!/bin/sh
# Launcher for the wall display: the ONE way the app gets started on the Pi.
#
# Runs app.py inside the desktop session (so it inherits the display), sends
# its log to the system journal instead of a file (kind to the SD card), and
# restarts it if it ever exits unexpectedly.
#
#   started by:  ~/.config/labwc/autostart   (tools/install.sh sets that up)
#   read log:    journalctl -t kona-flight-tracker -f
#   stop it:     pkill -TERM -f 'python3 .*app\.py'
#   restart it:  ~/flight-tracker/tools/update.sh   (or: pkill -HUP -f 'python3 .*app\.py')
#
# Exit codes from app.py:
#   0   quit on purpose (Esc, Q, SIGTERM)      -> stop
#   2   config.toml is broken                  -> wait, try again (so a fix takes effect)
#   3   another copy already has the screen    -> stop
#   75  "restart me" (update, memory ceiling)  -> restart at once
#   anything else is a crash                   -> restart, backing off if it keeps happening

cd "$(dirname "$0")/.." || exit 1

# Log to the journal when it is there and working; otherwise run without a log
# rather than not at all.
if command -v systemd-cat >/dev/null 2>&1 && systemd-cat -t kona-flight-tracker true 2>/dev/null; then
    use_journal=1
else
    use_journal=0
fi

run_app() {
    if [ "$use_journal" = 1 ]; then
        systemd-cat -t kona-flight-tracker python3 app.py
    else
        python3 app.py >/dev/null 2>&1
    fi
}

delay=5
while :; do
    started=$(date +%s)
    run_app
    code=$?
    case "$code" in
        0|3) exit 0 ;;
        75)  delay=5; sleep 1; continue ;;
    esac
    # A run that lasted a while was healthy: forget earlier crashes.
    if [ $(( $(date +%s) - started )) -ge 120 ]; then
        delay=5
    fi
    sleep "$delay"
    delay=$(( delay * 2 ))
    if [ "$delay" -gt 60 ]; then
        delay=60
    fi
done
