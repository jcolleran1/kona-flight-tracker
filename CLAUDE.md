# CLAUDE.md — Kona Flight Tracker

Standing instructions for any Claude Code session in this repo.

## What this is
A wall-mounted flight tracker for Kailua-Kona International (KOA, ICAO PHKO),
Big Island, Hawaii, built as a gift for Josh's parents. Split screen at
1920×1080: **info board on the LEFT, calibrated map with live aircraft on the
RIGHT.** It runs unattended 24/7, so reliability beats features.

## Hardware and runtime (hard constraints)
- **Raspberry Pi Zero 2 W**, quad-core ARM64, **512 MB RAM**. Every feature
  has to fit that budget.
- Raspberry Pi OS (Debian Trixie), Python 3.13, pygame 2.6 (from apt
  `python3-pygame`). Display runs under a labwc session.
- Deployed at `/home/colleran/flight-tracker`, user `colleran`,
  host `kona-tracker.local`.
- **Dependencies:** pygame + Python stdlib only at runtime. Tests may use
  pytest. Adding any other runtime dependency needs a stated reason and Josh's OK.

## Architecture (keep it)
- `app.py`: entry point, fetch supervisor and watchdog, `--once`, `--windowed`
- `config.py` / `config.toml`: every tunable lives in config, nothing hardcoded
- `fetch/`: data sources (all return the ADSBExchange-v2 / readsb record
  shape) plus the poll loop. The fetch thread is the only writer of `SharedState`.
- `enrich/`: geo math, lookups, routes, aircraft DB, movement classification.
  Network enrichment must **never block the fetch loop**.
- `render/`: pygame, main thread, reads immutable snapshots, never touches the
  network.
- `data/`: bundled CSVs, fonts, map, icons, fixtures.

## Product rules (don't change without asking Josh)
- Map stays on the right, info on the left.
- Three movement colors only: **sand = ground, teal = toward Kona,
  coral = leaving Kona.** Overflights may be dimmer/smaller, not a new color.
- Island palette + B612 Mono font.
- **Never show wrong data. Blank beats wrong.** A missing route shows direction
  text; a negative altitude never reaches the screen.
- No stale/frozen "ghost" aircraft: anything not heard recently is dropped,
  not frozen.

## Data-source rules
- Respect every source's published rate limits and terms. Check the current
  terms before adding a new source.
- **No API keys or secrets in this repo, ever.** No key rotation schemes or
  multiple accounts to get around quotas.
- Identify with the existing `USER_AGENT` string.
- Cache enrichment lookups with bounded LRU+TTL caches. Don't cache transient
  network errors.

## How to work
- Work on the feature branch Josh created. **Never push to `main`.** Never
  force-push.
- Follow `PLAN.md` phase by phase. Commit at the end of each logical step with
  a clear message. Summarize each phase before starting the next.
- Ask before acting on anything in PLAN.md's "Decisions needed" list.
- Test without the Pi: set `source = "replay"` in config, or use the fixtures.
  - `python3 -m pytest -q`
  - `python3 app.py --once`
  - Render preview PNGs headless (`SDL_VIDEODRIVER=dummy`) and look at them
    before claiming UI work is done. Check 0, 1, 5, and 15 aircraft.
- Keep per-frame render cost low: cache text, rotated icons, and the map
  surface. Target ≤ 6–10 fps without pegging a core.
- Don't leave `config.toml` pointed at `replay` in a commit.

## Deploying to the Pi
Once `tools/update.sh` exists (PLAN Phase 4), deploying is:
`ssh colleran@kona-tracker.local`, then `~/flight-tracker/tools/update.sh`.
Until then, Josh deploys by hand. Give him exact commands and say which machine
each one runs on (Windows PowerShell vs. the Pi over SSH).
