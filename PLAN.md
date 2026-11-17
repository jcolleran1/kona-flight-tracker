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

### Answers (Josh, 2026-11-16)

1. **Home point:** keep KOA; label distances "FROM KOA". A git-ignored
   `config.local.toml` overlay exists for anyone who wants a private home point.
2. **Units:** speed in **knots**, distance in **statute miles**, altitude in feet.
3. **Hawaiian-on-Alaska:** flight number in the Alaska form (`AS1111`), airline
   shown as **Hawaiian Airlines** when the airframe is Hawaiian's. Rule lives in
   `data/brand_rules.csv`.
4. **Local receiver:** undecided. The readsb source is written and tested but
   ships disabled.

---

## Status

| Phase | State | Notes |
|---|---|---|
| 0 Safety net | done | `python3 -m pytest -q`; scripted Kona scenario replaces the Seattle sample |
| 1 Data correctness | done | see below |
| 2 Coverage | done (code) | airplanes.live + adsb.fi merged by default; adsb.lol and local receiver written, off |
| 3 UI polish | built, awaiting Josh's OK | previews in `previews/`; nothing here is final until approved |
| 4 Appliance hardening | not started | |
| 5 Optional extras | not started | |

**Phase 1 as built** (anything that differs from the plan text below):

- METAR altimeter arrives in hPa from aviationweather.gov; the correction uses
  the standard-atmosphere formula (about 27 ft per hPa), not the inHg rule of
  thumb. Same result, no unit conversion to get wrong.
- Phases are `ground / arriving / departing / overflight / local`. "Local" is
  low traffic that isn't using Kona (tour helicopters, Hilo and Waimea
  traffic); it is drawn at full size in its direction colour and reads
  "IN THE AREA". Only overflights are dimmed.
- An arrival must be descending, on a course that passes within reach of the
  field, and low enough for its distance to land. A departure is anything seen
  on the ground at Kona that lifts off, or climbing outbound on a radial.
  Both need 12 s of agreement before they are believed and then hold through
  level-offs and turns.
- Routes are validated geometrically (aircraft near the great-circle corridor,
  not flying away from the destination, consistent with what it is doing at
  Kona). A rejected route is blanked.
- Airline names: brand rules (`data/brand_rules.csv`), then the hand-checked
  `data/overrides.csv`, then adsbdb's airline record, then OpenFlights rows
  marked active. OpenFlights is the weakest link; add an override row for
  anything it gets wrong.
- Anonymous TIS-B targets (`~` hex) and ADS-B category C (vehicles, obstacles)
  are dropped. `show_anonymous = true` brings the first group back.
- Aircraft beyond `radius_nm` are dropped even if a source reports them.
- Enrichment requests go to adsbdb only for plain airline+number callsigns
  (routes) and for airframes whose feed record lacks a tail number or type.

**Phase 2 as built:**

- Default sources are `airplanes_live` + `adsb_fi`, each polled every 5 s,
  staggered, merged by hex (freshest position wins, identity fields pooled).
- `adsb_lol` is implemented but not enabled: it could not be reached from the
  build machine (connection reset), so it is unverified. Its README says an API
  key will be required in future; when that happens it should simply stay off.
  Turn it on only after `python3 app.py --once` shows it answering from the Pi.
- `local` reads a readsb / dump1090 / tar1090 `aircraft.json`. Tested against
  `data/fixtures/readsb_aircraft.json`. Off until the hardware exists.
- Not done: the measured CPU/RAM cost of readsb on the Zero 2 W (needs the
  hardware). The README says how to measure it.
- Per-source health is in every snapshot; the status strip draws it in Phase 3.
- Found on live data while testing: adsbdb returns a bare `692` as the "IATA"
  flight for operators with no IATA code. Fixed and pinned by a test.

**Phase 3 as built (not final until Josh approves the previews):**

- Basemap baked by `tools/make_map.py` from Natural Earth 1:10m land polygons
  (public domain), lightly smoothed; runways to scale from OurAirports (public
  domain); towns and summits labelled. Natural Earth has about one vertex per
  3 km on this coast. `--land` accepts any more detailed GeoJSON (US Census
  cartographic boundaries or an OSM coastline extract) without code changes.
- Bounds are aspect-correct: 108 nm tall, 96 nm wide, exactly 10 px per nm, the
  whole island in frame with KOA at about 27% from the left. Range rings are
  circles, drawn in the display's distance unit and labelled. `radius_nm` went
  from 40 to 60 so the feed covers what the map shows.
- The route banner across the top of the map is gone: the featured panel now
  carries the route (`HNL -> KOA` with city names), so the banner only repeated
  it and covered the north coast.
- Left panel: phase line, IATA flight number with the broadcast callsign small
  beside it, airline, type and tail, route (or what the aircraft's own motion
  says when no route is trusted), altitude with a climb/descent arrow, speed in
  knots, distance "FROM KOA". Table: colour pip, flight, route (or airline /
  type when there is no route), altitude. Rows are ordered Kona arrivals,
  departures, other traffic, by first appearance, so they don't reshuffle.
- Status strip: date and time (no seconds), one health dot per source with its
  name and aircraft count. With no data it says NO FLIGHT DATA instead of
  QUIET SKIES.
- Map: trails, edge pointers for aircraft outside the crop, aircraft parked at
  KOA drawn small and unlabelled so the airport isn't buried.
- Cost: the left panel is redrawn only when its content changes (about once a
  poll); the map only when something moved a pixel; quiet-hours dimming is
  baked into the cached layers; 2 fps overnight.

Waiting on Josh:
1. Whole-island crop (default) or the tighter Kona-side crop (`crop-kona-15.png`).
2. Aircraft-shaped icons for helicopters and light props (`shapes-15.png`,
   `[map] icon_shapes = true`). Off by default.
3. Banner removal, as described above.
4. 12-hour clock (`[display] clock_24h = false`, shown in `takeoff-15.png`).

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

## Fable's additions

A second audit of the whole repo, ranked by impact. Items marked **(ask)** change
how the display looks or bend a product rule, so they wait for Josh's OK; the
rest are folded into the phase shown in brackets.

### Tier 1: can put wrong data on the wall, or take the wall down

1. **Ghost aircraft on any outage.** `SharedState.publish_failure()` keeps the
   last good aircraft list forever and the renderer keeps drawing it, with only a
   25% dim after 90 s. With backoff up to 300 s, a Wi-Fi blip freezes planes on
   the map for minutes. That breaks the "no ghosts" rule. Fix: every aircraft
   carries the monotonic time of its last position; the renderer drops anything
   older than the freshness limit no matter what the fetch thread is doing. [P1]
2. **adsbdb routes are wrong for exactly the flights this wall shows most.**
   Checked live on 2026-11-16: `ASA1082` was Hawaiian 717 `N484HA` flying
   interisland, and adsbdb answered **Seattle to San Francisco**. The plan's
   check ("neither end is KOA while clearly landing") doesn't catch that for a
   plane in cruise. Add a geometric test that needs no extra data: the aircraft
   must sit near the great-circle corridor between the two airports, and must
   not be flying away from its "destination". Fail either one and the route is
   blanked. [P1]
3. **One uncaught exception kills the display.** Nothing wraps the per-frame
   draw, and the labwc autostart has no restart. A single bad record that slips
   past parsing leaves a dead black screen until someone power-cycles it. Fix:
   guard the frame, log once, keep going; systemd restart as the backstop. [P1/P4]
4. **Watchdog can make things worse.** `FetchSupervisor.restart()` runs on the
   render thread and blocks it for up to 5 s (frozen screen). If the old thread
   is stuck in a socket call it is abandoned but can still publish later, so two
   fetch threads can end up writing. Also `dead` uses `time.monotonic()` (system
   uptime) as "time since start", so it misfires on a Pi that has been up for
   more than 5 minutes when the app starts. Fix: non-blocking restart, a
   per-thread stop token checked before every publish, app-relative clock. [P1]
5. **Stale airline table shows the wrong airline.** `airlines.csv` is the
   unmaintained OpenFlights dump including defunct carriers. Live example near
   Kona: callsign `STT303` (a Cessna SkyCourier) maps to "Air St. Thomas", an
   airline that stopped flying years ago, and `MKU` maps to "Island Air", gone
   since 2017. Fix: keep only rows OpenFlights marks active, grow the
   hand-checked override list for carriers that actually fly in Hawaii, and
   prefer adsbdb's airline record when a lookup returns one. [P1]
6. **Callsign is not always a flight number.** `FDY539SA` (Mokulele, flown by
   Southern Airways Express) uses the tail number as its callsign. Blindly
   swapping the prefix would print a flight number that doesn't exist. Only
   translate when the suffix is purely numeric; otherwise show the callsign. [P1]
7. **airplanes.live refuses some networks outright.** From a cloud address it
   returns HTTP 403 with a "contact us" body. The Pi's home connection is fine
   today, but it means a single-source wall has a single point of failure the
   owner can't fix. Phase 2's merge is the fix; the app must also treat 403 as
   "back off for a long time", never as a reason to retry fast. [P2]
8. **adsb.fi's `/v2/lat/.../lon/.../dist/...` endpoint is deprecated.** Their
   README now points to `/v3/`. The existing `AdsbFiSource` uses the old path.
   Their terms also require citing adsb.fi; add data credits to the README and
   a small credit line on the display. [P2]

### Tier 2: visibly wrong or rough on screen

9. **Range rings bleed across the left panel.** The ellipses are drawn straight
   onto the screen with no clip, so rings centred on KOA sweep through the hero
   text and the table (visible in the committed `preview.png`). Fix: clip all
   map drawing to the map panel. [P3]
10. **Rows reshuffle and flash constantly.** The table is sorted by distance
    every poll, so rows swap places as planes move, and the "changed" pulse
    keys off distance and altitude, which change every poll, so every row is
    permanently pulsing. Fix: stable ordering (arrivals, departures, then the
    rest, each by time first seen) and pulse only when a row is new. [P3]
11. **Featured flight flaps.** Two planes at similar distance trade the hero
    panel back and forth every poll. Add stickiness: the featured flight stays
    until it leaves, lands, or something clearly more relevant shows up. [P1]
12. **Map is cropped around the wrong thing.** KOA sits 17 nm from the west
    edge, but arrivals from Honolulu and the mainland come in over the ocean
    from the north and west, so they are in the table long before they appear
    on the map. New bounds should put KOA nearer the centre-left with open
    ocean to the west and north. **(ask: preview both crops)** [P3]
13. **Clock depends on the Pi's timezone and on NTP.** The Zero has no
    battery clock. Until NTP syncs after boot the wall shows a wrong time and
    quiet hours misfire. Fix: timezone comes from config (`Pacific/Honolulu`,
    which has no daylight saving), and the clock stays blank until the system
    clock is plausibly set. [P1/P4]
14. **Text the font can't draw.** Airline and city names from adsbdb go
    straight to the screen. B612 Mono has no CJK or many accented glyphs, so
    "Tōkyō" style names render as boxes. Sanitise to what the font covers. [P1]
15. **Non-aircraft show up as planes.** Ground vehicles and fixed obstacles
    (ADS-B category `C1`-`C3`), and anonymous `~` hex TIS-B targets with no
    identity, are listed as aircraft. Filter the first group; never spend an
    adsbdb lookup on the second. [P1]

### Tier 3: performance and wear on the Zero 2 W

16. **Every frame repaints all 2 million pixels**, six times a second, even
    when nothing moved: gradient blit, map blit, all text, full-screen flip,
    plus a full-screen alpha blend whenever quiet hours or the stale dim is on
    (which is 9 hours a night). Fix: draw the left panel to a cached surface
    and only rebuild it when its content changes; skip the frame entirely when
    no aircraft moved a whole pixel and no text changed; bake the dim into the
    cached layers instead of blending per frame; drop to ~1 fps overnight. [P3]
17. **Per-frame allocations.** The banner band and every label pill create a
    new `SRCALPHA` surface each frame. Cache them. [P3]
18. **Text cache thrash.** The seconds clock and the "Ns" age counter create a
    new cached surface every second and the cache is wiped wholesale at 600
    entries. Use a small LRU and keep once-a-second strings out of it. [P3]
19. **Blocking enrichment and unbounded caches** are already in the plan
    (B, H). Extra detail found: transient failures are cached for 24 h in
    `aircraftdb.py`, so one timeout hides an airline for a day. [P1]
20. **Screen burn-in / image retention.** A static layout 24/7 on a consumer
    panel leaves marks over months. A 1-2 px drift of the whole frame every
    few minutes is invisible and prevents it. **(ask)** [P4]

### Tier 4: appliance and repo hygiene

21. **No clean shutdown.** SIGTERM from systemd kills mid-frame; the `stopping`
    event in `app.py` is created and never set. Add signal handlers. [P4]
22. **Config errors crash with a KeyError** and `quiet_hours` strings are
    re-parsed every frame. Validate once at load with readable messages. [P1]
23. **`git pull` deploys will fight local edits.** Once `tools/update.sh`
    exists, any tweak made to `config.toml` on the Pi blocks the pull. A
    git-ignored `config.local.toml` overlay solves that and keeps private
    values (a house location, a LAN receiver address) out of the repo. [P1]
24. **The systemd unit doesn't match the device.** `aircraft.service` is a
    system unit set up for kmsdrm, but the Pi runs the app inside a labwc
    session. Phase 4 replaces it with a user service that belongs to the
    graphical session. [P4]
25. **`USER_AGENT` is copy-pasted in three files**; one constant. [P1]
26. **Replay isn't Kona.** `sample_response.json` is Seattle traffic shifted
    onto Hawaii and jittered randomly, so it can't exercise arrivals,
    departures, or dead reckoning. Replace with a scripted Kona scenario
    (arrival on the 17 approach, departure, FL360 overflight, tour helicopter,
    Hawaiian-on-ASA, every nasty case from Phase 0). [P0]
27. **No preview tool.** CLAUDE.md asks for headless preview PNGs at 0/1/5/15
    aircraft but nothing renders them. Add `tools/preview.py`. [P0]
28. **Recorded live data must not be committed.** adsb.fi and airplanes.live
    data is licensed for personal use; `data/fixtures/live/` goes in
    `.gitignore` and committed fixtures are synthetic. [P0]

### Ideas that would make it feel like a finished product

Each is small, uses data already in the feed, and stays inside the three
colours and the island palette. All **(ask)**, shown in preview PNGs first.

- **Aircraft-shaped icons.** The feed's category field separates helicopters,
  light props, and jets. The Big Island sky is full of tour helicopters; a
  rotor icon for those and a slimmer prop icon for Cessnas makes the map
  readable at a glance. Same three colours.
- **Off-map pointers.** A small chevron on the map edge, in the movement
  colour, for a tracked plane that is outside the map crop, so the table and
  map never disagree.
- **Runway in use.** Draw the KOA runway to scale and mark the landing
  direction (17 or 35) from the METAR wind.
- **12-hour clock option** (`2:05 PM`) for a household display, and a
  sunrise/sunset based dim instead of fixed quiet hours.
- **A "next arrival" line in the quiet state** is not possible without
  schedule data and would risk wrong data, so it is deliberately left out.

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
