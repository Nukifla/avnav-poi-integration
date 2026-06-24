# avnav-poi-integration

AvNav plugin that overlays NoForeignLand POIs (anchorages, marinas, fuel, community markers, tide stations) on the chart.

## Installation

Copy this folder to `~/avnav/plugins/avnav-poi-integration/` and restart AvNav.

On first start the plugin automatically:
- Writes `~/avnav/user/viewer/user.mjs` (or patches it if it already exists) with a fix for an avnav icon-rendering bug
- Downloads the NFL places and tide-station data (~2 MB, cached 24 h)

## Enabling the overlay on a chart

1. Open the chart you want to use in AvNav
2. Tap the chart name → **Edit overlays**
3. Press **Insert after** (or **Add**)
4. Set **Type** → `overlay`
5. Set **Name** → `avnav-poi-integration`
6. Set **Feature formatter** → `avnav-poi-integration`
7. Leave everything else as default → **Save**

The POI icons will appear immediately on the chart.

## Settings

Available on the AvNav server status page under the plugin entry:

| Setting | Description | Default |
|---|---|---|
| `showAnchorages` | Anchorages & mooring buoys | true |
| `showMarinas` | Marinas & harbours | true |
| `showFuel` | Fuel & gas stations | true |
| `showServices` | Dinghy docks, water, boat yards, showers, laundry | true |
| `showWarnings` | Nav warnings & info markers | true |
| `showNFLCommunity` | NFL community markers (events/warnings/questions/crew) | true |
| `showTideStations` | Tide stations | true |
| `cacheTTL` | Cache lifetime in minutes for community markers | 120 |
| `downloadRadius` | Radius (km) around GPS position to pre-download place details and photos for offline use (0 = disabled) | 20 |

## Data sources

| Source | Endpoint | Auth |
|---|---|---|
| NFL places | `/api/v1/places` | None (AES-128-ECB) |
| NFL place detail | `/api/v1/place?placeId=ID` | None (AES-128-ECB) |
| NFL community markers | `/api/v1/community/markers` | None (AES-128-ECB) |
| NFL tide stations | `/api/v1/tidestations` | None (plain JSON) |

The AES-128-ECB key is extracted from NFL's public web bundle — no account or API key needed.

## Dependencies

- `requests` — HTTP client (included in avnav's Python env)
- `cryptography` — AES decrypt (included in avnav's Python env)
