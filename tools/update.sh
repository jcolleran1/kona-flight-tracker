#!/bin/sh
# One-command deploy, run ON THE PI:
#
#     ssh colleran@kona-tracker.local
#     ~/flight-tracker/tools/update.sh
#
# Pulls the newest commit of whatever branch this checkout is on, runs the
# checks, and only then restarts the wall. If the checks fail, the code is put
# back exactly as it was and the running wall is never touched.
#
# Local settings belong in config.local.toml (git-ignored), so pulls never
# conflict with them.
set -eu

cd "$(dirname "$0")/.."
branch=$(git rev-parse --abbrev-ref HEAD)
old=$(git rev-parse HEAD)

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "This checkout has local edits to tracked files:" >&2
    git status --short --untracked-files=no >&2
    echo "Move settings into config.local.toml, then run: git checkout -- ." >&2
    exit 1
fi

echo "== fetching origin/$branch"
git fetch --quiet origin "$branch"
new=$(git rev-parse "origin/$branch")
if [ "$old" = "$new" ]; then
    echo "already up to date ($(git log -1 --format='%h %s'))"
    exit 0
fi
git merge --quiet --ff-only "origin/$branch"
echo "== $(git log --oneline "$old..HEAD" | wc -l) new commit(s):"
git log --oneline "$old..HEAD"

rollback() {
    echo "== CHECKS FAILED: going back to $(git log -1 --format=%h "$old"); the wall was not restarted" >&2
    git reset --quiet --hard "$old"
    exit 1
}

echo "== checking"
if python3 -c "import pytest" 2>/dev/null; then
    python3 -m pytest -q -x || rollback
else
    echo "(pytest not installed; running the self-test only."
    echo " For the full checks: sudo apt install -y python3-pytest)"
fi
python3 app.py --selftest --offline || rollback

echo "== restarting the wall"
if pkill -HUP -f 'python3 .*app\.py'; then
    echo "restart signal sent; follow along with: journalctl -t kona-flight-tracker -f"
else
    echo "the app is not running. Start it by logging the desktop session out and in,"
    echo "or by hand from the desktop: ~/flight-tracker/tools/run.sh &"
fi
