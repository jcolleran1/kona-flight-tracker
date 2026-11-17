# Kona Flight Tracker — Overhaul Plan

Audit of `main` @ `5c7f5c1`, plus a phased plan to make the wall display match
what a real flight-tracker site shows. Work happens on a feature branch; each
phase is its own commit (or a few), tested in replay mode before it touches the Pi.

Hardware reality: **Raspberry Pi Zero 2 W** (quad-core ARM64, **512 MB RAM**),
1920x1080 HDMI, Raspberry Pi OS Trixie, labwc session, user `colleran`,
app at `~/flight-tracker`, hostname `kona-tracker.local`.

---

## Decisions needed from Josh before/while executing

Ask these up front; don't guess.

1. **Home point.** `config.toml` `[location]` is set to KOA airport coordinates,
   not the parents' house. Every distance/bearing on screen is "from the
   airport." Use the house coordinates, or keep KOA and label it "FROM KOA"?
2. **Units.** The wall shows **mph and statute miles**; FlightRadar24 shows
   **knots** by default. A 430 kt jet reads "495 mph" on the wall, which looks
   wrong next to FR24 even though it's correct. Switch speed to knots (match
   FR24), or keep mph?
3. **Hawaiian-on-Alaska flights.** The live feed shows Hawaiian Airlines 717s
   (`N479HA`, `N494HA`) flying under **Alaska `ASA` callsigns**
   (`ASA1111`, `ASA1121`). The wall currently labels them "Alaska Airlines."
   Check 2–3 of these on FR24 and match how FR24 names them (airline and
   flight number), then encode that rule.
4. **Local receiver.** Is Josh buying an RTL-SDR (+ micro-USB OTG adapter,
   ideally a powered hub) for the house? Phase 2.2 code gets written either way;
   hardware decides whether it's turned on.

---

## Audit: what's wrong today, and why

### A. Negative altitudes
- `fetch/worker.py` uses `alt_baro` raw. `alt_baro` is **pressure altitude**
  (referenced to 29.92 inHg), not true altitude. KOA sits at ~47 ft; on a
  normal high-pressure Hawaii day (altimeter ~30.05–30.15) a plane on the
  runway or just lifting off reports **–100 to –200 ft**. Nothing clamps or
  corrects it.
- Ground detection only trusts the literal string `"ground"`, which many
  transponders don't send during the takeoff/landing roll.

### B. Delay / "lagging behind FR24"
- Poll interval is 15 s, and **positions are drawn exactly where they were at
  fetch time** until the next poll. Planes sit still for 15 s, then jump.
  Trackers like FR24 dead-reckon between updates, so they always look "ahead."
- Records carry `seen_pos` (seconds since that position was heard). The code
  ignores it, so a position that's already 30–60 s old is drawn as current.
- Enrichment is **synchronous inside the fetch loop**: `fetch_once()` calls
  adsbdb for aircraft and route lookups (up to ~5 s timeout each). A slow
  adsbdb response delays the position update for every plane.

### C. "Flight numbers are wrong / don't translate"
- ADS-B broadcasts **ICAO** callsigns (`UAL1759`, `HAL21`, `ASA1121`). FR24
  and airport boards show **IATA** flight numbers (`UA1759`, `HA21`, `AS1121`).
  The wall shows the ICAO form, so it never matches what people look up.
- `data/airlines.csv` only has `icao,name`, with **no IATA column**, so there's
  nothing to translate with.
- Brand vs. operator mismatch (Hawaiian 717s under `ASA`, see Decision 3).
- `aircraftdb` airline fill-in only runs live for the **featured** flight;
  table rows for planes without a callsign stay blank until a cache hit happens.
- Aircraft type is missing when the feed's `t` is empty (seen live:
  `N522SA type=None`); there's no fallback.

### D. Wrong "arriving / departing" and the wrong featured flight
- Approaching/departing is just "did the distance to home shrink since the last
  poll." A 36,000 ft overflight crossing toward the island gets labeled
  **"TOWARD KONA"** and colored as an arrival.
- `select_featured()` picks the **nearest airborne** plane, which is often a
  high overflight instead of the jet actually landing at KOA.
- Vertical rate (`baro_rate` / `geom_rate`) is in the feed and unused.

### E. Route banner shows wrong cities sometimes
- adsbdb's callsign→route data is community-maintained and can be stale,
  especially for reused interisland flight numbers. Nothing sanity-checks it
  (e.g., a plane descending into KOA "FROM X TO Y" where neither end is KOA).
- Misses **and transient errors** are cached for 1 hour, so a single timeout
  blanks a route for an hour.

### F. Map geometry is distorted
- Bounds are 1.40° lat × 1.00° lon on a 960×1080 panel. At 19.7°N that
  works out to ~9.2 px/km east-west vs ~6.9 px/km north-south: the map is
  **stretched ~32% horizontally**. That's why range rings are drawn as ellipses.
  Planes still line up (the image was calibrated to the same bounds), but the
  island is the wrong shape and headings look skewed.
- The bundled `big_island_map.png` is a stylized polygon, not a real coastline.

### G. Coverage (the honest limit)
- airplanes.live relies on volunteer receivers; Big Island coverage is thin,
  especially low near KOA. FR24 has more receivers, MLAT, satellite, and
  estimated positions. No code change fixes coverage by itself; Phase 2 does.

### H. Appliance / ops issues
- **Two autostart entries** (`~/.config/labwc/autostart` and
  `~/.config/lxsession/LXDE-pi/autostart`) both launch `app.py`. Today only one
  fires, but if both ever do, two instances fight over the screen. No
  single-instance lock exists.
- Caches in `routes.py` / `aircraftdb.py` are unbounded dicts on a 512 MB box.
- No tests in the repo (the README claims unit tests; there's no `tests/`).
- README describes a Pi 4 as primary; the device is a Zero 2 W.
- Deploying requires zip + scp + unzip by hand.
- The Pi's timezone has never been verified. Quiet hours and the clock assume
  local Hawaii time (`Pacific/Honolulu`).
- **Security:** the old AirLabs key lives in the force-pushed commit `d2abfc2`,
  which GitHub may still serve by SHA. Make sure that key has been rotated at
  AirLabs. Never add API keys to this repo.

---

## Phase 0 — Safety net (do first)

1. Add `tests/` (pytest, stdlib-only otherwise). Cover: altitude correction,
   ground detection, freshness filtering, ICAO→IATA translation, movement
   classification, route validation, map projection, dead reckoning.
2. Add fixture JSON files under `data/fixtures/` with nasty cases: negative
   `alt_baro`, `"ground"` string, stale `seen_pos`, missing callsign, missing
   `t`, `type: "mlat"`, Hawaiian 717 on an `ASA` callsign, overflight at FL360,
   KOA departure climbing, KOA arrival descending.
3. Add `tools/record.py`: run on the Pi during a busy morning, saves raw
   responses (timestamped) to `data/fixtures/live/` for regression tests and
   replay.
4. `ReplaySource` can replay a recorded sequence, not just one jittered frame.

**Done when:** `pytest` passes locally and `python3 app.py --once` works in replay.

## Phase 1 — Data correctness

1. **Altitude.**
   - Fetch the PHKO METAR altimeter from aviationweather.gov every 30 min
     (cached, stays within their published rate limits). Fallbacks, in order:
     aircraft's own `nav_qnh`, then `alt_geom`, then raw `alt_baro`.
   - Below 18,000 ft: show QNH-corrected altitude
     (`true ≈ alt_baro + (altimeter_inHg − 29.92) × 1000`). At/above 18,000 ft:
     show pressure altitude (matches what trackers show in cruise).
   - Ground = `alt_baro == "ground"` OR (corrected alt < 100 ft AND gs < 60 kt).
   - **Never display a negative altitude.** Clamp at 0; near-zero airborne
     shows as e.g. "TAKEOFF" / "LANDING" depending on vertical rate.
2. **Freshness.** Drop records with `seen_pos` > 30 s (configurable). Use the
   response's `now` timestamp as the position reference time. Mark
   `type: mlat` / `tisb_*` positions internally (they're jumpier).
3. **Dead reckoning + smoothing.** Keep each aircraft's last fix (lat, lon, gs,
   track, fix time). At render time, project forward using gs/track for
   elapsed time (cap ~30 s). When a new fix arrives, tween to it over ~1 s
   instead of snapping. Map only; text values stay as reported.
4. **Poll faster.** Default `interval_seconds = 5` (still 5× under
   airplanes.live's 1 req/s limit). Handle HTTP 429 with `Retry-After`.
5. **Decouple enrichment.** Move adsbdb aircraft and route lookups to a
   background enrichment worker with a queue and its own rate limit (~1 req /
   2 s). The fetch loop never waits on enrichment. Enrich every visible aircraft
   over time, not just the featured one. Bounded LRU+TTL caches. Cache real
   "unknown" answers ~30 min; **don't cache network errors** (retry with backoff).
6. **IATA flight numbers.** Regenerate `data/airlines.csv` as `icao,iata,name`
   from OpenFlights `airlines.dat` (keep `overrides.csv` winning). Display the
   **IATA form as primary** (`UA1759`), ICAO small/secondary. Prefer adsbdb's
   `callsign_iata` / airline IATA when a route lookup returns it.
7. **Brand rule** for Hawaiian-on-ASA (per Decision 3), keyed off the
   registration/owner (adsbdb `registered_owner`, or a registration-pattern
   override file), configurable, not hardcoded.
8. **Type fallback.** If `t` is empty, use the adsbdb aircraft type.
9. **Movement classification** using route + vertical rate + altitude:
   - *arriving*: dest = KOA, or descending below ~12,000 ft toward KOA
   - *departing*: origin = KOA, or climbing below ~12,000 ft away from KOA
   - *overflight*: level/cruising, neither route endpoint is KOA
   - *ground*
   Keep the **three map colors** (sand / teal / coral). Overflights use the
   color for their direction but render smaller and dimmer, labeled
   "OVERFLIGHT" in text.
10. **Featured flight** = nearest *actual KOA arrival/departure*; fall back to
    nearest airborne only when there are none.
11. **Route validation.** Reject a route if the aircraft is clearly arriving at
    or departing KOA (low, near, climbing/descending) and neither endpoint is
    KOA/PHKO. Fall back to the direction text.
12. Apply Decisions 1 and 2 (home point, units).

**Done when:** fixtures pass; no negative altitude can reach the screen; replay
shows smooth motion; table shows `UA1759`-style numbers.

## Phase 2 — Coverage (closing the gap with FR24)

1. **Multi-source merge.** Add sources for adsb.fi and adsb.lol (same v2
   shape) alongside airplanes.live. **Before adding each, read that service's
   current terms and rate limits and follow them.** Poll them staggered, merge
   by `hex`, keep the record with the smallest `seen_pos`. Feeds share some
   receivers but not all, so the merged view catches more.
2. **Local receiver source.** Add a `readsb` / `dump1090` source reading
   `http://<host>:8080/data/aircraft.json` (configurable host, so the receiver
   can live on the Zero 2 W or a separate box on the LAN). Local data wins in
   the merge. Write it now; enable when hardware exists. Include a README
   section: RTL-SDR + OTG adapter + powered hub, readsb install, measured CPU
   and RAM cost on the Zero 2 W (if pygame + readsb is too heavy, document
   running the receiver on a second device).
3. **Per-source health** in the status strip (which feeds are up, plane count each).

**Done when:** merged feed shows ≥ the planes any single feed shows, with no
duplicates; local source works against a recorded `aircraft.json` fixture.

## Phase 3 — UI polish (map stays on the right)

1. **Real basemap.** Rewrite `tools/make_map.py` to bake a PNG once, offline,
   from Natural Earth or OSM coastline data (check the license, credit it in the
   README; no live tile fetching). Real coastline, KOA runway 17/35, a few town
   labels, subtle depth shading. **Choose bounds with a correct aspect ratio**
   (scale longitude by cos(lat)) so range rings are circles. Keep the bounds in
   `config.toml`.
2. **Trails.** Last ~3 min of positions per aircraft as a fading line in its
   movement color.
3. **Hero panel:** IATA flight number large; airline; route `KOA → DEN` with
   city names; aircraft type; altitude with ↑/↓ vertical-trend arrow; speed;
   distance.
4. **Table rows:** flight (IATA) · route (`KOA→HNL`) · altitude + ↑/↓. Keep it
   uncluttered; nothing redundant with the hero.
5. Keep the island palette and B612 Mono. Render a preview PNG of the new
   layout from replay and show Josh before deploying.

**Done when:** preview PNGs at 1920×1080 look clean with 0, 1, 5, and 15
aircraft; no text overlaps; nothing renders off-panel.

## Phase 4 — Appliance hardening

1. **One launcher.** Pick a single launch path (a systemd *user* service with
   `Restart=always`, or one autostart entry), document it, and remove the
   other. Add a single-instance lock (`fcntl.flock` on a file in
   `$XDG_RUNTIME_DIR`) so a second copy exits immediately.
2. **One-command deploy.** `tools/update.sh` on the Pi: `git pull` on the
   deployed branch, run tests, restart the app. No more zip/scp.
3. **`python3 app.py --selftest`:** checks network, each source, METAR, Pi
   timezone (`Pacific/Honolulu`), display driver, and free memory; prints
   pass/fail lines.
4. Bounded caches everywhere; log RSS once an hour; stay well under 512 MB.
5. README rewrite: Zero 2 W as the target, current deploy flow, sources, receiver.

## Phase 5 — Optional extras (only after 0–4 are solid)

- Weather chip from the PHKO METAR (wind, conditions). Active runway inferred
  from wind or recent departure tracks.
- Highlight notable aircraft (military, heavies, rare types).
- "Flights today" counter.
- Tiny LAN-only status page for checking health from a phone.

---

## Not doing: rotating API keys

The idea was to cycle through several free API keys to stay under monthly limits.
That's **not part of this plan**:
- The current stack doesn't use keys at all. airplanes.live, adsb.fi, adsb.lol,
  and adsbdb are keyless with **rate** limits, not monthly quotas. The app runs
  far under them.
- Free tiers on keyed services (AirLabs, AviationStack, etc.) allow one free
  account per person. Rotating multiple accounts to get around the cap breaks
  their terms and gets keys banned.
- The legitimate ways to get "more and better data" are Phase 2: merge keyless
  feeds and add a local receiver. Feeding a local receiver's data to these
  networks also earns perks on some of them (check each one's current program).

---

## Verifying against FR24 (acceptance test, morning arrival bank)

Run `tools/compare.py`, which logs once a second what the wall shows (flight,
alt, speed, position, route). Josh takes FR24 screenshots of the same flights
at the same moments. Target for 10 flights near KOA:
- flight number format matches FR24
- altitude within ±200 ft below 18,000 ft; never negative
- map position visually within one icon-width of FR24
- correct arriving/departing/overflight label
- route correct, or blank (never wrong)
