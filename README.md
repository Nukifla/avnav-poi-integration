# AvNav Customizations

All changes live in `~/avnav/` so they survive upstream updates and can be copied to another instance.

To replicate on a new instance: copy the entire `~/avnav/user/` and `~/avnav/plugins/` directories, then apply any manual settings listed below.

---

## 1. Boat shape icon

**What it does:** Replaces the default arrow marker with a top-down boat silhouette that rotates to show heading.
- Red boat with white bow mark = heading mode (compass bearing)
- Red boat with orange bow mark = COG mode (no heading available, using course over ground)
- Grey boat = stationary / no direction data (points north)

**Files added:**
- `user/viewer/boat-hdg.svg` — boat icon for heading mode
- `user/viewer/boat-cog.svg` — boat icon for COG mode
- `user/viewer/boat-steady.svg` — boat icon for stationary state

**Files modified:**
- `user/viewer/images.json` — added `boatImageHdg`, `boatImage`, `boatImageSteady` entries pointing to the SVGs above

**Manual setting required:**
In the avnav UI: Settings → Boat Direction → change from `cog` to `hdt`
This enables the heading → COG → north fallback priority.

---

## 2. Nautical POI overlay — NoForeignLand (primary) + Mapbox guide areas

**What it does:** Displays anchorages, marinas, fuel stations, harbours, boat yards, nav warnings, tide stations, NoForeignLand sailing guide area polygons, and NFL community markers (events, warnings, questions, crew available). Single-tap on any POI opens a full-screen popup; NFL places load rich detail (description, star rating, reviews, fuel price) from the NFL API on demand.

### Data sources

| Source | URL | Auth | Format |
|---|---|---|---|
| NFL places (primary) | `/api/v1/places?zoom=10` | None | AES-128-ECB encrypted JSON |
| NFL place detail | `/api/v1/place?placeId=ID` | None | AES-128-ECB encrypted JSON |
| NFL guide areas | Mapbox Vector Tiles `steve-neal.guides-areas` zoom 6 | Mapbox token | gzipped PBF |
| NFL community markers | `/api/v1/community/markers` | None | AES-128-ECB encrypted JSON |
| NFL tide stations | `/api/v1/tidestations` | None | Plain JSON |

The AES-128-ECB key (`YjdiZTEyMDctY2MwMC00ZA==`) was extracted from the NFL web bundle's `EC` constant — no login or API key needed for any of the above endpoints.

### Files — all in one plugin folder

- `plugins/avnav-poi-integration/plugin.py` — Python backend: NFL places/community/tide station fetching, AES decrypt, PBF decoder for guide areas, disk cache, GeoJSON overlay writer, per-place detail API proxy
- `plugins/avnav-poi-integration/plugin.js` — JS frontend: feature formatter (icons + full-screen popup with async detail loading)
- `plugins/avnav-poi-integration/plugin.css` — dialog body, star ratings, meta grid, CTA button styles
- `plugins/avnav-poi-integration/plugin.json` — (empty — overlay registered programmatically via int@default.cfg)
- `plugins/avnav-poi-integration/cache/` — auto-created: `nfl_places.json.gz` (24h, ~2 MB) + `nfl_tidestations.json` (24h, ~200 kB)
- `plugins/avnav-poi-integration/icons/anchorage.svg` — blue circle, anchor
- `plugins/avnav-poi-integration/icons/marina.svg` — green circle, anchor-in-ring
- `plugins/avnav-poi-integration/icons/fuel.svg` — orange circle, fuel pump
- `plugins/avnav-poi-integration/icons/harbour.svg` — grey circle, bollard
- `plugins/avnav-poi-integration/icons/generic.svg` — grey circle, pin
- `plugins/avnav-poi-integration/icons/guide_area.svg` — purple circle, book
- `plugins/avnav-poi-integration/icons/warning.svg` — red circle, !
- `plugins/avnav-poi-integration/icons/info.svg` — blue circle, i
- `plugins/avnav-poi-integration/icons/medical.svg` — red circle, +
- `plugins/avnav-poi-integration/icons/event.svg` — blue circle, calendar
- `plugins/avnav-poi-integration/icons/question.svg` — teal circle, ?
- `plugins/avnav-poi-integration/icons/crew.svg` — green circle, person
- `plugins/avnav-poi-integration/icons/tide_station.svg` — teal circle, wave

### Generated at runtime (do not need to copy)

- `overlays/avnav-poi-integration.geojson` — GeoJSON written by the plugin, filtered to the download radius around the current GPS position (falls back to a 1° viewport if GPS is unavailable or `downloadRadius = 0`); served at `/api/overlay/download?name=avnav-poi-integration.geojson`

### Dependencies

`requests` + `cryptography` — both available in avnav's Python env (no pip install needed). Stdlib only for everything else.

### Setup (automatic)

On first load, `plugin.py` writes `~/avnav/charts/int@default.cfg` adding `avnav-poi-integration.geojson` to avnav's default overlay config. Only touched if the poi entry is absent; other overlays are preserved.

### When copying to a new instance

Also copy `~/avnav/charts/int@default.cfg`, or let the plugin recreate it on first run. The NFL places + tide station caches will be downloaded automatically on first startup (one HTTP request each; takes ~5–10 s on a typical connection).

### Plugin settings (avnav server status page → avnav-poi-integration plugin)

| Setting | Description | Default |
|---|---|---|
| `showAnchorages` | Anchorages & mooring buoys | true |
| `showMarinas` | Marinas & harbours | true |
| `showFuel` | Fuel & gas stations | true |
| `showServices` | Dinghy docks, water, boat yards, showers, laundry | true |
| `showWarnings` | Nav warnings & info markers | true |
| `showNFLAreas` | NFL guide area polygons (needs mapboxToken) | true |
| `showNFLCommunity` | NFL community markers | true |
| `showTideStations` | Tide stations | true |
| `mapboxToken` | Mapbox token for guide areas — get from noforeignland.com/map DevTools → Network → pbf → access_token= | (empty) |
| `cacheTTL` | Cache lifetime in minutes for community markers | 120 |
| `downloadRadius` | Radius (km) to download all place details + photos for offline use (0 = disabled) | 20 |

### Offline download

On startup (after ~90 s), a background thread runs every 30 min. It:
1. Reads the GPS position from avnav.
2. For every NFL place within `downloadRadius` km: fetches the full place JSON (24 h disk cache at `cache/places/{id}.json`) and downloads **all** photos.
3. For every community marker within radius: fetches the post JSON (2 h TTL) and downloads the banner image.
4. **Deletes** JSON + images for any place now outside the radius (keeps disk usage bounded as the boat moves).
5. Images stored in `cache/images/{sha256_hash}` (no extension — content-type detected from magic bytes).

All images in popups are routed through `/api/image?url=ENCODED_URL`:
- **Downloaded** → served from disk (200 + binary; works offline).
- **Not yet downloaded** → HTTP 302 redirect to the original CDN URL (transparent fallback while online).

Google lh3.googleusercontent.com URLs are automatically requested at `=s800` (800 px max dimension) to limit storage.

### avnav bug workaround — per-feature icons

avnav's `geojsonchartsource.js` initialises `this.userIcons = {}` but then calls
`this.userIcons(sym)` treating it as a function, which throws and makes features invisible.

**Fix (in `user/viewer/user.mjs`)**: a one-time `Object.defineProperty(Object.prototype, 'userIcons', { set ... })` intercepts the assignment before GeoJsonChartSource instances are created and replaces `{}` with a self-indexing callable:
`fn = function(sym){ return fn[sym]; }` — calling `fn(sym)` is equivalent to `fn[sym]`, so the same object acts as both function and icon cache.

This runs at module top-level (before the `export default` function) so it is in place before the map and overlays initialise.

### NFL place type → icon mapping

| NFL display_type | Icon |
|---|---|
| ANCHORAGE, MOORING_BUOYS | anchorage (blue) |
| MARINA, MARINA_OFFICE | marina (green) |
| HARBOUR | harbour (grey) |
| FUEL, GAS | fuel (orange) |
| DINGHY_DOCK, WATER, PUMP_OUT, WASTE_DISPOSAL | service (grey) |
| BOAT_YARD, BOAT_SERVICE, CHANDLER | boatservice (grey) |
| LAUNDRY, SHOWER | amenity (grey) |
| NAV_WARNING | warning (red) |
| NAV_INFO, LOCK, GENERAL_INFO | info (blue i) |
| MEDICAL | medical (red +) |
| RESTAURANT | restaurant (generic) |
| SHOP | shop (generic) |
| anything else | generic (grey pin) |

---
