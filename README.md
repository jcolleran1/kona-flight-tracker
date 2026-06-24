# Kona Flight Tracker

Wall-mounted ADS-B flight display. A single Python process, Pygame straight
to the framebuffer. No browser, no X, no Flask.

**Split-screen layout (1920×1080), styled like a Kona vacation house:**

- **Left** — a board readout in warm sand on an ocean-teal wash: the featured
  flight (callsign, airline, type, altitude, ground speed, and whether it's
  heading toward or leaving Kona), then a clean table of everything else in
  range (flight, airline, altitude), and a status strip.
- **Right** — your calibrated Big Island map with live aircraft as rotating
  icons in **three colors**: sand = on the ground, **teal = coming toward
  Kona**, **coral = leaving Kona**. Range rings, a home/KOA marker, and
  de-conflicted callsign labels. Across the top, a banner names the active
  arrival/departure and where it's **coming from** (arriving) or **going to**
  (departing).

Data: [airplanes.live](https://airplanes.live/api-guide/) — real-time ADS-B,
the same class of feed the big tracking sites use (personal/noncommercial,
1 req/sec; we poll every 15s). **No API key.**
Routes (the city pair in the banner): [adsbdb.com](https://www.adsbdb.com/),
a free community route database — cached, and gracefully degrades to a compass
direction when a route isn't known.
Font: B612 Mono (OFL). Airline names: OpenFlights (ODbL) + your
`data/overrides.csv`. Types: bundled CSV.

---

## What changed from the old AirLabs version

- **Data source** is now airplanes.live (real-time ADS-B) instead of AirLabs.
  This is what makes the map match FlightRadar24 / adsb.fi. It also kills the
  old "ghost flight recycler" bug for free: ADS-B tracks each physical
  airframe by hex ID, so a parked plane's next flight number can't be
  recycled onto a phantom position.
- **No hardcoded key.** Everything is in `config.toml`.
- **Threaded fetch** with exponential backoff, a watchdog, and last-good-data
  retention, so a flaky network never freezes or blanks the screen.
- **Your map and plane icons are preserved** — same projection and calibration
  approach as your original `main.py`, now config-driven.
- **Your airline overrides are preserved** in `data/overrides.csv` and win
  over the generic OpenFlights names (which mislabel Mokulele, Aloha, etc.).

---

## Hardware note (read this first)

This renders 1920×1080 and blits a half-screen map every frame. That is fine
on a **Pi 4** (your original board) or a **Pi Zero 2 W**. The *original*
single-core **Pi Zero W** (ARMv6, 512 MB) will struggle at this resolution —
if that's the target, drop `display.width/height` to 1280×720 in config.toml
and expect a few fps. Everything is tuned to make that survivable (cached text,
cached rotated icons, 6 fps), but a Pi 4 is the comfortable choice.

---

## Phase 1 — Flash and headless boot

1. Raspberry Pi Imager → choose your Pi model → **Raspberry Pi OS Lite
   (64-bit on Pi 4 / Zero 2 W; 32-bit on an original Zero W)** → your microSD.
2. Edit Settings: hostname (e.g. `flighttracker`), username `colleran` +
   password, Wi-Fi SSID/password (Zero W is **2.4 GHz only**), country `US`,
   and on Services enable **SSH**.
3. Flash, boot, `ssh colleran@flighttracker.local`.
4. `sudo apt update && sudo apt full-upgrade -y && sudo reboot`

## Phase 2 — Install and confirm live data

```bash
sudo apt install -y python3-pygame
# copy the project over from your laptop:
#   scp -r flight-tracker colleran@flighttracker.local:~
cd ~/flight-tracker
nano config.toml          # set [location] lat/lon to the house; check units
python3 app.py --once     # one fetch, printed as a table — NO display needed
```

`--once` is the smoke test: it should print live aircraft with airline/type,
distance, and bearing. Do this before touching the display.

## Phase 3 — The map (right panel)

The map is fully config-driven under `[map]` in config.toml:

- **Your own map:** point `image` at an absolute path, e.g.
  `image = "/home/colleran/kona-tracker/big_island_map.png"`, or drop your PNG
  into `data/` and use its bare filename. A stylized Big Island (Kona side) is
  bundled as the default so it works out of the box.
- **Calibration:** `lat_min/lat_max/lon_min/lon_max` are the geographic edges
  of the image — these are carried over verbatim from your original
  `main.py`. If planes sit offshore or shifted, nudge these four numbers (same
  tuning you did before).
- **Plane icon:** `plane_icon` works the same way. If the file is missing, a
  clean vector aircraft is drawn instead — it can never break on a missing
  asset.
- Toggles: `show_range_rings`, `range_ring_nm`, `show_home`, `show_labels`.

To regenerate the bundled default map after changing bounds:
`python3 tools/make_map.py`.

## Phase 4 — Renderer dev on a laptop (no Pi, no network)

```bash
pip install pygame
# in config.toml:  source = "replay"   fullscreen = false
python3 app.py            # Esc or Q to quit
```

Replay plays back `data/sample_response.json`, re-centred on your home point
and jittered each poll, so trends, the hero, the table, and the map icons all
animate. Set `source = "airplanes_live"` and `fullscreen = true` before deploying.

## Phase 5 — Run it as an appliance

Two options. **systemd is recommended** (cleaner than the labwc autostart hack).

### Option A — systemd (recommended)

```bash
sudo cp ~/flight-tracker/service/aircraft.service /etc/systemd/system/
sudo cp ~/flight-tracker/service/aircraft-restart.{service,timer} /etc/systemd/system/
# edit User= and the two paths in aircraft.service if they differ
sudo usermod -aG video,render,input colleran
sudo systemctl daemon-reload
sudo systemctl enable --now aircraft.service aircraft-restart.timer
journalctl -u aircraft.service -f
```

### Option B — labwc autostart (your original Pi 4 setup)

If you're keeping the lightdm/labwc desktop you already configured, you can
skip systemd and reuse your autostart file — just point it at the new path:

```bash
# ~/.config/labwc/autostart
sleep 5
python3 /home/colleran/flight-tracker/app.py &
```

### Never blank the screen

Append ` consoleblank=0` to the single line in `/boot/firmware/cmdline.txt`,
and add `hdmi_force_hotplug=1` to `/boot/firmware/config.txt`. Reboot.

### Cap journald (protect the SD card)

```bash
sudo nano /etc/systemd/journald.conf   # SystemMaxUse=30M  (Storage=volatile for RAM-only)
sudo systemctl restart systemd-journald
```

---

## Project layout

```
app.py                  entry point, thread wiring, --once test mode
config.py / config.toml  all settings; nothing hardcoded
state.py                lock-guarded snapshot handoff (fetch → render)
fetch/sources.py        airplanes.live + adsb.fi + replay sources
fetch/worker.py         poll loop, backoff, parse, publish
enrich/geo.py           haversine, bearing, compass points
enrich/lookups.py       airline + type lookups (overrides.csv wins)
enrich/motion.py        ground / toward-Kona / leaving-Kona classifier
enrich/routes.py        origin/destination by callsign (adsbdb, cached)
enrich/trend.py         approaching/departing with hysteresis
render/renderer.py      split layout: hero + table + status + route banner
render/mapview.py       right panel: projection, icons, rings, home, labels
render/theme.py         palette, fonts, cached text rendering
render/units.py         imperial/metric formatting
data/                   airlines.csv, overrides.csv, aircraft_types.csv,
                        B612 Mono, big_island_map.png, plane.png, sample response
tools/                  make_map.py, make_plane.py (regenerate default assets)
service/                systemd unit + nightly restart timer
```

## Swapping data sources

`fetch/sources.py` defines a tiny interface: anything with
`.fetch() -> list[dict]` in ADSB-Exchange-v2 shape. `adsb_fi` is included as a
drop-in alternate (`source = "adsb_fi"`). Adding OpenSky later is one class,
no renderer changes.
