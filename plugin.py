"""
AvNav Nautical POI plugin — NoForeignLand data
Primary:   NFL /api/v1/places  — 109k sailor-contributed POIs (anchorages, marinas, fuel, etc.)
Guide areas: NFL Mapbox Vector Tiles (optional, needs token)
Community: NFL /api/v1/community/markers — events/warnings/questions/crew
Tides:     NFL /api/v1/tidestations — 7283 global tide station locations
"""

import base64
import gzip
import hashlib
import json
import math
import os
import threading
import time

import requests

NFL_BASE               = 'https://www.noforeignland.com/api/v1'
NFL_PLACES_URL         = NFL_BASE + '/places'
NFL_COMMUNITY_URL      = NFL_BASE + '/community/markers'
NFL_COMMUNITY_POST_URL = NFL_BASE + '/community/post'
NFL_TIDES_URL          = NFL_BASE + '/tidestations'
NFL_PLACE_URL          = NFL_BASE + '/place'
NFL_PLACES_TTL    = 86400   # 24 h
NFL_TIDES_TTL     = 86400   # 24 h
# AES-128-ECB key from NFL's client-side JS bundle (EC constant in index-*.js)
NFL_AES_KEY_B64   = 'YjdiZTEyMDctY2MwMC00ZA=='
NFL_ICON_CDN      = 'https://www.noforeignland.com/nfl-web-images/markers/'

# Minimal user.mjs injected into ~/avnav/user/viewer/ if missing.
# Fixes the avnav GeoJsonChartSource bug where constructor sets
# this.userIcons={} but styleFunction calls this.userIcons(sym) as a function.
_USER_MJS_MARKER = 'patchAvnavUserIcons'
_USER_MJS_PATCH = r"""// Injected by avnav-poi-integration: fix GeoJsonChartSource icon bug.
(function patchAvnavUserIcons() {
    if (Object.getOwnPropertyDescriptor(Object.prototype, 'userIcons')) return;
    var ICON_SCALE = 0.4;
    Object.defineProperty(Object.prototype, 'userIcons', {
        configurable: true,
        set(value) {
            var fn;
            if (value !== null && typeof value === 'object' && !(value instanceof Function)) {
                var base = function userIcons(sym) { return base[sym]; };
                fn = new Proxy(base, {
                    set(target, prop, icon) {
                        if (icon && typeof icon.setScale === 'function') {
                            var _ss = icon.setScale.bind(icon);
                            icon.setScale = function(s) {
                                _ss(typeof s === 'number' ? s * ICON_SCALE : ICON_SCALE);
                            };
                            icon.setScale(1);
                        }
                        return Reflect.set(target, prop, icon);
                    }
                });
                Object.assign(base, value);
            } else {
                fn = value;
            }
            Object.defineProperty(this, 'userIcons', {
                value: fn, writable: true, configurable: true, enumerable: true
            });
        }
    });
})();
"""
_USER_MJS_STUB = _USER_MJS_PATCH + "\nexport default async (api) => {};\n"

# All icon stems referenced in plugin.js NFL_ICON_NAME map
NFL_ICON_STEMS = [
    'anchorage', 'mooringBuoys', 'marina', 'harbour', 'hurricaneHole',
    'fuel', 'gas', 'dinghyDock', 'water', 'pumpOut', 'wasteDisposal',
    'boatYard', 'boatService', 'chandler', 'laundry', 'shower',
    'navWarning', 'navInfo', 'lock', 'generalInfo', 'medical',
    'restaurant', 'shop', 'dive', 'beach', 'atm', 'natureReserve',
    'historicalSite', 'placeOfWorship', 'touristAttraction', 'transport',
    'fitness', 'hike', 'kidsActivity', 'watersport', 'bridge',
    'shoreSupport', 'portOfEntryOffice', 'yachtClearanceAgent',
    'parcelDrop', 'office', 'sim', 'pets', 'hairdresser', 'hardware',
    'communityEvent', 'communityWarning', 'communityQuestion', 'communityCrew',
    'book',
]

# NFL display_type / community type → CDN icon filename stem
_NFL_CDN_ICON = {
    'ANCHORAGE':             'anchorage',
    'MOORING_BUOYS':         'mooringBuoys',
    'MARINA':                'marina',
    'MARINA_OFFICE':         'marina',
    'HARBOUR':               'harbour',
    'HURRICANE_HOLE':        'hurricaneHole',
    'FUEL':                  'fuel',
    'GAS':                   'gas',
    'DINGHY_DOCK':           'dinghyDock',
    'WATER':                 'water',
    'PUMP_OUT':              'pumpOut',
    'WASTE_DISPOSAL':        'wasteDisposal',
    'BOAT_YARD':             'boatYard',
    'BOAT_SERVICE':          'boatService',
    'CHANDLER':              'chandler',
    'LAUNDRY':               'laundry',
    'SHOWER':                'shower',
    'NAV_WARNING':           'navWarning',
    'NAV_INFO':              'navInfo',
    'LOCK':                  'lock',
    'GENERAL_INFO':          'generalInfo',
    'MEDICAL':               'medical',
    'RESTAURANT':            'restaurant',
    'SHOP':                  'shop',
    'DIVE':                  'dive',
    'BEACH':                 'beach',
    'ATM':                   'atm',
    'NATURE_RESERVE':        'natureReserve',
    'HISTORICAL_SITE':       'historicalSite',
    'PLACE_OF_WORSHIP':      'placeOfWorship',
    'TOURIST_ATTRACTION':    'touristAttraction',
    'TRANSPORT':             'transport',
    'FITNESS':               'fitness',
    'HIKE':                  'hike',
    'KIDS_ACTIVITY':         'kidsActivity',
    'WATERSPORT':            'watersport',
    'BRIDGE':                'bridge',
    'SHORE_SUPPORT':         'shoreSupport',
    'PORT_OF_ENTRY_OFFICE':  'portOfEntryOffice',
    'YACHT_CLEARANCE_AGENT': 'yachtClearanceAgent',
    'PARCEL_DROP':           'parcelDrop',
    'OFFICE':                'office',
    'SIM':                   'sim',
    'PETS':                  'pets',
    'HAIRDRESSER':           'hairdresser',
    'HARDWARE':              'hardware',
    # Community types
    'EVENT':                 'communityEvent',
    'WARNING':               'communityWarning',
    'QUESTION':              'communityQuestion',
    'CREW_AVAILABLE':        'communityCrew',
    # Guide areas
}

def _cdn_sym(dtype):
    stem = _NFL_CDN_ICON.get(dtype)
    return (NFL_ICON_CDN + stem + '-60.png') if stem else None

# NFL display_type → our icon type
_NFL_PLACE_TYPE_MAP = {
    'ANCHORAGE':          'anchorage',
    'MOORING_BUOYS':      'anchorage',
    'MARINA':             'marina',
    'MARINA_OFFICE':      'marina',
    'HARBOUR':            'harbour',
    'FUEL':               'fuel',
    'GAS':                'fuel',
    'DINGHY_DOCK':        'service',
    'WATER':              'service',
    'PUMP_OUT':           'service',
    'WASTE_DISPOSAL':     'service',
    'BOAT_YARD':          'boatservice',
    'BOAT_SERVICE':       'boatservice',
    'CHANDLER':           'boatservice',
    'LAUNDRY':            'amenity',
    'SHOWER':             'amenity',
    'NAV_WARNING':        'warning',
    'NAV_INFO':           'info',
    'LOCK':               'info',
    'GENERAL_INFO':       'info',
    'MEDICAL':            'medical',
    'RESTAURANT':         'restaurant',
    'SHOP':               'shop',
}

# Which icon types map to which config toggle
_ACTIVE_TYPE_GROUPS = {
    'showAnchorages': {'anchorage'},
    'showMarinas':    {'marina', 'harbour'},
    'showFuel':       {'fuel'},
    'showServices':   {'service', 'boatservice', 'amenity'},
    'showWarnings':   {'warning', 'info'},
}


# ---------------------------------------------------------------------------
# Tile math (kept for guide-area Mapbox tiles)
# ---------------------------------------------------------------------------

def _lon_to_x(lon, z):
    return int((lon + 180.0) / 360.0 * (1 << z))

def _lat_to_y(lat, z):
    lr = math.radians(lat)
    return int((1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * (1 << z))

def _tile_bbox(x, y, z):
    n = 1 << z
    w = x / n * 360.0 - 180.0
    e = (x + 1) / n * 360.0 - 180.0
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return w, lat_s, e, lat_n

def _bbox_to_tiles(west, south, east, north, z):
    x0 = _lon_to_x(west, z);  x1 = _lon_to_x(east, z)
    y0 = _lat_to_y(north, z); y1 = _lat_to_y(south, z)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


# ---------------------------------------------------------------------------
# NFL API helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# NFL API helpers
# ---------------------------------------------------------------------------

def _nfl_decrypt(data):
    """Decrypt a base64-encoded AES-128-ECB response from the NFL public API."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    ct = base64.b64decode(data)
    key = base64.b64decode(NFL_AES_KEY_B64)
    c = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    pt = c.decryptor().update(ct)
    pad = pt[-1]
    if 1 <= pad <= 16:
        pt = pt[:-pad]
    return pt

def _nfl_get(url, params=None):
    resp = requests.get(url, params=params, timeout=30,
                        headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return json.loads(_nfl_decrypt(resp.content))


# ---------------------------------------------------------------------------
# NFL places fetcher  (primary POI source)
# ---------------------------------------------------------------------------

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fetch_nfl_places():
    """Download the full NFL place index (~109k entries). Returns list of
    [id, name, internal_type, lat, lon, display_type] arrays."""
    data = _nfl_get(NFL_PLACES_URL, params={'zoom': 10})
    return data.get('places', [])


# ---------------------------------------------------------------------------
# NFL community markers
# ---------------------------------------------------------------------------

_NFL_COMMUNITY_TYPE_MAP = {
    'EVENT':          'event',
    'WARNING':        'warning',
    'QUESTION':       'question',
    'CREW_AVAILABLE': 'crew_available',
}

def _fetch_nfl_community():
    data = _nfl_get(NFL_COMMUNITY_URL)
    features = []
    for m in data.get('markers', []):
        if len(m) < 5:
            continue
        mid, mtype, _, lat, lon = m[0], m[1], m[2], m[3], m[4]
        poi_type = _NFL_COMMUNITY_TYPE_MAP.get(mtype, 'generic')
        sym = _cdn_sym(mtype)
        cprops = {
            'name':         mtype.replace('_', ' ').title(),
            'type':         poi_type,
            'display_type': mtype,
            'source':       'NoForeignLand',
            'nfl_id':       mid,
            'lat':          lat,
            'lon':          lon,
        }
        if sym:
            cprops['sym'] = sym
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [round(lon, 6), round(lat, 6)]},
            'properties': cprops,
        })
    return features


# ---------------------------------------------------------------------------
# NFL tide stations
# ---------------------------------------------------------------------------

def _fetch_nfl_tidestations():
    """Returns list of [id, name, lat_str, lon_str] — plain JSON, no decrypt."""
    resp = requests.get(NFL_TIDES_URL, timeout=20,
                        headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return resp.json().get('stations', [])


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class Plugin:
    CONFIG = [
        {'name': 'showAnchorages',  'description': 'Show anchorages & mooring buoys',         'type': 'BOOLEAN', 'default': 'true'},
        {'name': 'showMarinas',     'description': 'Show marinas & harbours',                  'type': 'BOOLEAN', 'default': 'true'},
        {'name': 'showFuel',        'description': 'Show fuel & gas stations',                 'type': 'BOOLEAN', 'default': 'true'},
        {'name': 'showServices',    'description': 'Show dinghy docks, water, boat yards, laundry, showers', 'type': 'BOOLEAN', 'default': 'true'},
        {'name': 'showWarnings',    'description': 'Show nav warnings & info markers',         'type': 'BOOLEAN', 'default': 'true'},
        {'name': 'showNFLCommunity','description': 'Show NoForeignLand community markers',      'type': 'BOOLEAN', 'default': 'true'},
        {'name': 'showTideStations','description': 'Show tide stations',                        'type': 'BOOLEAN', 'default': 'true'},
        {'name': 'cacheTTL',        'description': 'Cache duration in minutes (for community markers)', 'type': 'NUMBER', 'default': '120'},
        {'name': 'downloadRadius',  'description': 'Radius (km) to download place details + all photos for offline use (0 = disabled)', 'type': 'NUMBER', 'default': '20'},
    ]

    @classmethod
    def pluginInfo(cls):
        return {
            'description': 'Nautical POI overlay: NoForeignLand places, guide areas, community, tide stations',
            'version': '2.0',
            'config': cls.CONFIG,
        }

    def __init__(self, api):
        self.api = api
        self._lock = threading.Lock()
        # NFL places (primary POI source)
        self._nfl_places = []       # [[id, name, itype, lat, lon, dtype], ...]
        self._nfl_places_ts = 0.0
        # Guide-area polygons (Mapbox tiles, optional)
        # Community markers
        self._nfl_community = []
        self._nfl_community_ts = 0.0
        # Tide stations
        self._tidestations = []
        self._tidestation_ts = 0.0
        # Current viewport bbox for overlay filtering
        self._last_bbox = (-180.0, -90.0, 180.0, 90.0)
        self._gps_lat = None
        self._gps_lon = None
        self._stopped = False
        self._fetch_thread = None
        self._last_fetch_trigger = 0.0

        self._cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
        data_dir = api.getDataDir()
        overlays_dir = os.path.join(data_dir, 'overlays')
        os.makedirs(overlays_dir, exist_ok=True)
        os.makedirs(self._cache_dir, exist_ok=True)
        self._overlay_file = os.path.join(overlays_dir, 'avnav-poi-integration.geojson')
        self._places_cache      = os.path.join(self._cache_dir, 'nfl_places.json.gz')
        self._tides_cache       = os.path.join(self._cache_dir, 'nfl_tidestations.json')
        self._place_detail_dir  = os.path.join(self._cache_dir, 'places')
        self._image_dir         = os.path.join(self._cache_dir, 'images')
        os.makedirs(self._place_detail_dir, exist_ok=True)
        os.makedirs(self._image_dir, exist_ok=True)

        # Remove old overlay file from before the rename
        old_overlay = os.path.join(overlays_dir, 'navily-pois.geojson')
        if os.path.exists(old_overlay):
            try:
                os.remove(old_overlay)
            except Exception:
                pass

        if not os.path.exists(self._overlay_file):
            seed = {
                'type': 'FeatureCollection',
                'features': [{
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [0.0, 0.0]},
                    'properties': {'name': 'Loading…', 'type': 'generic', 'source': 'avnav-poi-integration'},
                }]
            }
            with open(self._overlay_file, 'w') as f:
                json.dump(seed, f)

        api.registerRequestHandler(self.handleApiRequest)
        api.registerRestart(self.stop)
        self._ensure_default_overlay_cfg(data_dir)
        self._ensure_user_mjs(data_dir)

    def _ensure_default_overlay_cfg(self, data_dir):
        cfg_path = os.path.join(data_dir, 'charts', 'int@default.cfg')
        poi_entry = {
            'name':             'avnav-poi-integration.geojson',
            'type':             'overlay',
            'featureFormatter': 'avnav-poi-integration',
            'enabled':          True,
            'allowOnline':      True,
        }
        try:
            cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
            overlays = cfg.get('overlays', [])
            # Remove legacy navily-pois.geojson entry if present
            overlays = [o for o in overlays if o.get('name') != 'navily-pois.geojson']
            # Update existing entry or append new
            existing = next((o for o in overlays if o.get('name') == poi_entry['name']), None)
            changed = True
            if existing is None:
                overlays.append(poi_entry)
                cfg['overlays'] = overlays
            elif not existing.get('allowOnline'):
                existing['allowOnline'] = True
            else:
                changed = False
            if changed:
                tmp = cfg_path + '.tmp'
                with open(tmp, 'w') as f:
                    json.dump(cfg, f, indent=2)
                os.replace(tmp, cfg_path)
        except Exception as ex:
            self.api.error('avnav-poi: could not update default overlay cfg: %s', str(ex))

    def _ensure_user_mjs(self, data_dir):
        path = os.path.join(data_dir, 'user', 'viewer', 'user.mjs')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.exists(path):
                content = open(path).read()
                if _USER_MJS_MARKER not in content:
                    tmp = path + '.tmp'
                    with open(tmp, 'w') as f:
                        f.write(_USER_MJS_PATCH + '\n' + content)
                    os.replace(tmp, path)
            else:
                with open(path, 'w') as f:
                    f.write(_USER_MJS_STUB)
        except Exception as ex:
            self.api.error('avnav-poi: could not write user.mjs: %s', str(ex))

    def stop(self):
        self._stopped = True

    def _download_nfl_icons(self):
        icon_dir = os.path.join(os.path.dirname(__file__), 'icons', 'nfl')
        os.makedirs(icon_dir, exist_ok=True)
        downloaded = 0
        for stem in NFL_ICON_STEMS:
            dest = os.path.join(icon_dir, stem + '-60.png')
            if os.path.exists(dest):
                continue
            try:
                url = NFL_ICON_CDN + stem + '-60.png'
                resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                if resp.status_code == 200 and resp.content:
                    with open(dest, 'wb') as f:
                        f.write(resp.content)
                    downloaded += 1
            except Exception as ex:
                self.api.error('avnav-poi: icon download %s: %s', stem, str(ex))
        if downloaded:
            self.api.setStatus('RUNNING', f'avnav-poi: downloaded {downloaded} NFL icons')

    def run(self):
        self.api.setStatus('STARTED', 'Nautical POI plugin — loading NFL data…')
        POLL = 60
        MIN_SHIFT = 0.25
        last_lat = last_lon = None

        # Download NFL icons in background (only missing ones)
        threading.Thread(target=self._download_nfl_icons, daemon=True).start()
        # Download place details + images within radius for offline use
        threading.Thread(target=self._download_loop, daemon=True).start()

        # Trigger initial data load immediately
        self._trigger_fetch()

        while not self._stopped:
            try:
                lat = self.api.getSingleValue('gps.lat')
                lon = self.api.getSingleValue('gps.lon')
                if lat is not None and lon is not None:
                    lat = float(lat); lon = float(lon)
                    shifted = (last_lat is None
                               or abs(lat - last_lat) > MIN_SHIFT
                               or abs(lon - last_lon) > MIN_SHIFT)
                    if shifted:
                        last_lat, last_lon = lat, lon
                        pad = 1.0
                        with self._lock:
                            self._last_bbox = (lon - pad, lat - pad,
                                               lon + pad, lat + pad)
                            self._gps_lat = lat
                            self._gps_lon = lon
                        self._write_overlay_file()
                        self._trigger_fetch()
            except Exception:
                pass
            time.sleep(POLL)

    def _trigger_fetch(self):
        now = time.time()
        if now - self._last_fetch_trigger < 15:
            return
        t = self._fetch_thread
        if t and t.is_alive():
            return
        self._last_fetch_trigger = now
        self._fetch_thread = threading.Thread(target=self._do_fetch, daemon=True)
        self._fetch_thread.start()

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------

    def handleApiRequest(self, url, handler, args):
        if url.startswith('icons/'):
            return self._serve_icon(url, handler)

        if url == 'status':
            with self._lock:
                return {
                    'status':        'OK',
                    'nfl_places':    len(self._nfl_places),
                    'nfl_community': len(self._nfl_community),
                    'tide_stations': len(self._tidestations),
                }

        if url == 'nearby':
            try:
                lat = float((args.get('lat') or [0])[0])
                lon = float((args.get('lon') or [0])[0])
                radius_km = float((args.get('r') or [0.2])[0])
            except Exception:
                return {'error': 'invalid params'}
            nearby = []
            with self._lock:
                for p in self._nfl_places:
                    if len(p) < 6:
                        continue
                    pid, name, itype, plat, plon, dtype = (
                        p[0], p[1], p[2], float(p[3]), float(p[4]), p[5])
                    if _haversine_km(lat, lon, plat, plon) <= radius_km:
                        nearby.append({
                            'nfl_id':       pid,
                            'name':         name,
                            'type':         itype,
                            'display_type': dtype,
                            'lat':          plat,
                            'lon':          plon,
                        })
            return nearby

        if url == 'place':
            pid = (args.get('id') or [''])[0]
            if not pid:
                return {'error': 'missing id'}
            place = self._get_place_cached(pid)
            return place if place is not None else {'error': 'fetch failed'}

        if url == 'community-post':
            pid = (args.get('id') or [''])[0]
            if not pid:
                return {'error': 'missing id'}
            post = self._get_community_cached(pid)
            return post if post is not None else {'error': 'fetch failed'}

        if url == 'image':
            raw_url = (args.get('url') or [''])[0]
            if not raw_url:
                return {'error': 'missing url'}
            path = self._fetch_image(raw_url)
            if path and os.path.exists(path):
                with open(path, 'rb') as f:
                    data = f.read()
                ctype = 'image/jpeg'
                if data[:8] == b'\x89PNG\r\n\x1a\n':
                    ctype = 'image/png'
                elif len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                    ctype = 'image/webp'
                handler.send_response(200)
                handler.send_header('Content-Type', ctype)
                handler.send_header('Content-Length', str(len(data)))
                handler.send_header('Cache-Control', 'public, max-age=2592000')
                handler.end_headers()
                handler.wfile.write(data)
                return True
            # Not yet cached — redirect to original so it loads online
            handler.send_response(302)
            handler.send_header('Location', raw_url)
            handler.end_headers()
            return True

        return {'status': 'error', 'message': 'unknown: ' + url}

    # ------------------------------------------------------------------
    # Place / community-post detail helpers (shared by HTTP handler + precacher)
    # ------------------------------------------------------------------

    def _get_place_cached(self, pid):
        """Return place dict from disk cache (24 h) or NFL API; None on error."""
        cache_file = os.path.join(self._place_detail_dir, f'{pid}.json')
        try:
            if os.path.exists(cache_file):
                if time.time() - os.path.getmtime(cache_file) < 86400:
                    with open(cache_file) as f:
                        return json.load(f)
        except Exception:
            pass
        try:
            resp = requests.get(NFL_PLACE_URL, params={'placeId': pid},
                                timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            place = json.loads(_nfl_decrypt(resp.content)).get('place', {})
            try:
                with open(cache_file, 'w') as f:
                    json.dump(place, f)
            except Exception:
                pass
            return place
        except Exception:
            return None

    def _get_community_cached(self, pid):
        """Return community post dict from disk cache (2 h) or NFL API; None on error."""
        cache_file = os.path.join(self._place_detail_dir, f'community_{pid}.json')
        try:
            if os.path.exists(cache_file):
                if time.time() - os.path.getmtime(cache_file) < 7200:
                    with open(cache_file) as f:
                        return json.load(f)
        except Exception:
            pass
        try:
            resp = requests.get(NFL_COMMUNITY_POST_URL, params={'communityPostId': pid},
                                timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            post = json.loads(_nfl_decrypt(resp.content)).get('communityPost', {})
            try:
                with open(cache_file, 'w') as f:
                    json.dump(post, f)
            except Exception:
                pass
            return post
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Image caching
    # ------------------------------------------------------------------

    def _img_cache_path(self, url):
        return os.path.join(self._image_dir, hashlib.sha256(url.encode()).hexdigest()[:32])

    def _fetch_image(self, url):
        """Return local path of cached image (download if missing). None on failure."""
        path = self._img_cache_path(url)
        if os.path.exists(path):
            return path
        dl_url = url
        if 'googleusercontent.com' in url:
            dl_url = url.split('=')[0] + '=s800'
        try:
            resp = requests.get(dl_url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200 and resp.content:
                with open(path, 'wb') as f:
                    f.write(resp.content)
                return path
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Background pre-caching: details + images within cacheRadius km
    # ------------------------------------------------------------------

    def _download_loop(self):
        time.sleep(90)  # let plugin finish initializing
        while not self._stopped:
            try:
                lat = self.api.getSingleValue('gps.lat')
                lon = self.api.getSingleValue('gps.lon')
                if lat is not None and lon is not None:
                    try:
                        radius_km = float(self.api.getConfigValue('downloadRadius', '20') or 0)
                    except Exception:
                        radius_km = 20.0
                    if radius_km > 0:
                        self._download_nearby(float(lat), float(lon), radius_km)
            except Exception as ex:
                self.api.error('avnav-poi: download error: %s', str(ex))
            # Sleep 30 min in short increments so stop() is responsive
            for _ in range(180):
                if self._stopped:
                    return
                time.sleep(10)

    def _download_nearby(self, lat, lon, radius_km):
        """Download all place details + images within radius, delete outside."""
        place_ids = []
        community_ids = []
        with self._lock:
            for p in self._nfl_places:
                if len(p) < 5:
                    continue
                if _haversine_km(lat, lon, float(p[3]), float(p[4])) <= radius_km:
                    place_ids.append(str(p[0]))
            for feat in self._nfl_community:
                coords = feat.get('geometry', {}).get('coordinates', [])
                if len(coords) >= 2:
                    if _haversine_km(lat, lon, float(coords[1]), float(coords[0])) <= radius_km:
                        pid = feat.get('properties', {}).get('nfl_id')
                        if pid:
                            community_ids.append(str(pid))

        # Remove data for places now outside the radius
        removed = self._delete_outside_radius(set(place_ids), set(community_ids))

        total = len(place_ids) + len(community_ids)
        if not total:
            return

        self.api.setStatus('RUNNING',
            f'avnav-poi: downloading {total} POIs within {radius_km:.0f} km…'
            + (f' ({removed} deleted outside)' if removed else ''))
        imgs = 0

        for pid in place_ids:
            if self._stopped:
                return
            place = self._get_place_cached(pid)
            if not place:
                continue
            for img in (place.get('images') or []):
                if self._stopped:
                    return
                su = img.get('servingUrl', '')
                if su and not os.path.exists(self._img_cache_path(su)):
                    if self._fetch_image(su):
                        imgs += 1
                    time.sleep(0.1)
            banner = place.get('banner', '')
            if banner and not os.path.exists(self._img_cache_path(banner)):
                if self._fetch_image(banner):
                    imgs += 1
                time.sleep(0.1)

        for pid in community_ids:
            if self._stopped:
                return
            post = self._get_community_cached(pid)
            if not post:
                continue
            banner = post.get('banner', '')
            if banner and not os.path.exists(self._img_cache_path(banner)):
                if self._fetch_image(banner):
                    imgs += 1
                time.sleep(0.1)

        self.api.setStatus('RUNNING',
            f'avnav-poi: {total} POIs downloaded, {imgs} new images ({radius_km:.0f} km)')

    def _delete_image(self, url):
        if not url:
            return
        p = self._img_cache_path(url)
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    def _delete_outside_radius(self, keep_place_ids, keep_community_ids):
        """Delete JSON + images for places no longer inside the download radius."""
        removed = 0
        try:
            for fname in os.listdir(self._place_detail_dir):
                if not fname.endswith('.json'):
                    continue
                path = os.path.join(self._place_detail_dir, fname)
                is_community = fname.startswith('community_')
                pid = fname[len('community_'):-5] if is_community else fname[:-5]
                keep = keep_community_ids if is_community else keep_place_ids
                if pid in keep:
                    continue
                # Collect image URLs from the JSON before removing it
                try:
                    with open(path) as f:
                        data = json.load(f)
                    if is_community:
                        self._delete_image(data.get('banner', ''))
                    else:
                        for img in (data.get('images') or []):
                            self._delete_image(img.get('servingUrl', ''))
                        self._delete_image(data.get('banner', ''))
                except Exception:
                    pass
                try:
                    os.remove(path)
                    removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        return removed

    def _serve_icon(self, url, handler):
        path = os.path.join(os.path.dirname(__file__), url)
        if not os.path.exists(path):
            return {'status': 'error', 'message': 'not found'}
        with open(path, 'rb') as f:
            data = f.read()
        ctype = 'image/png' if url.endswith('.png') else 'image/svg+xml'
        handler.send_response(200)
        handler.send_header('Content-Type', ctype)
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Cache-Control', 'public, max-age=86400')
        handler.end_headers()
        handler.wfile.write(data)
        return True

    # ------------------------------------------------------------------
    # Overlay file writer
    # ------------------------------------------------------------------

    def _active_place_types(self):
        types = set()
        for cfg_key, icon_types in _ACTIVE_TYPE_GROUPS.items():
            if self._bool(cfg_key):
                types.update(icon_types)
        return types

    def _write_overlay_file(self):
        active = self._active_place_types()
        features = []

        try:
            radius_km = float(self.api.getConfigValue('downloadRadius', '20') or 0)
        except Exception:
            radius_km = 20.0

        with self._lock:
            west, south, east, north = self._last_bbox
            gps_lat = self._gps_lat
            gps_lon = self._gps_lon
            use_radius = radius_km > 0 and gps_lat is not None and gps_lon is not None
            seen = set()

            # NFL places — radius-filtered when GPS available, else viewport
            for p in self._nfl_places:
                if len(p) < 6:
                    continue
                pid, name, itype, lat, lon, dtype = p[0], p[1], p[2], float(p[3]), float(p[4]), p[5]
                if use_radius:
                    if _haversine_km(gps_lat, gps_lon, lat, lon) > radius_km:
                        continue
                else:
                    if not (south <= lat <= north and west <= lon <= east):
                        continue
                icon_type = _NFL_PLACE_TYPE_MAP.get(dtype, 'generic')
                if icon_type not in active and icon_type != 'generic':
                    continue
                ck = f'{lon:.5f},{lat:.5f}'
                if ck in seen:
                    continue
                seen.add(ck)
                sym = _cdn_sym(dtype)
                props = {
                    'name':         name or 'Unknown',
                    'type':         icon_type,
                    'display_type': dtype,
                    'nfl_id':       pid,
                    'source':       'NoForeignLand',
                    'lat':          lat,
                    'lon':          lon,
                }
                if sym:
                    props['sym'] = sym
                features.append({
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [round(lon, 6), round(lat, 6)]},
                    'properties': props,
                })

            # Guide-area polygons (viewport-based — tile data, not per-POI download)

            # Community markers — radius-filtered when GPS available, else all
            if self._bool('showNFLCommunity'):
                for feat in self._nfl_community:
                    if use_radius:
                        coords = feat.get('geometry', {}).get('coordinates', [])
                        if len(coords) < 2:
                            continue
                        if _haversine_km(gps_lat, gps_lon, float(coords[1]), float(coords[0])) > radius_km:
                            continue
                    features.append(feat)

            # Tide stations — radius-filtered when GPS available, else viewport
            if self._bool('showTideStations'):
                for s in self._tidestations:
                    if len(s) < 4:
                        continue
                    sid, sname, slat, slon = s[0], s[1], float(s[2]), float(s[3])
                    if use_radius:
                        if _haversine_km(gps_lat, gps_lon, slat, slon) > radius_km:
                            continue
                    else:
                        if not (south <= slat <= north and west <= slon <= east):
                            continue
                    features.append({
                        'type': 'Feature',
                        'geometry': {'type': 'Point', 'coordinates': [round(slon, 6), round(slat, 6)]},
                        'properties': {
                            'name':   sname,
                            'type':   'tide_station',
                            'nfl_id': sid,
                            'source': 'NoForeignLand',
                            'lat':    slat,
                            'lon':    slon,
                        },
                    })

        fc = {'type': 'FeatureCollection', 'features': features}
        try:
            tmp = self._overlay_file + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(fc, f)
            os.replace(tmp, self._overlay_file)
            self.api.setStatus('RUNNING', f'avnav-poi: {len(features)} POIs in overlay')
        except Exception as ex:
            self.api.error('avnav-poi: overlay write failed: %s', str(ex))

    # ------------------------------------------------------------------
    # Background fetch
    # ------------------------------------------------------------------

    def _do_fetch(self):
        now = time.time()

        # NFL places — primary data, 24h cache
        if now - self._nfl_places_ts > NFL_PLACES_TTL:
            places = self._load_places_cache()
            if places is not None:
                with self._lock:
                    self._nfl_places = places
                    self._nfl_places_ts = now
                self.api.setStatus('RUNNING', f'avnav-poi: {len(places)} NFL places loaded from cache')
            else:
                try:
                    self.api.setStatus('RUNNING', 'avnav-poi: downloading NFL places…')
                    places = _fetch_nfl_places()
                    self._save_places_cache(places)
                    with self._lock:
                        self._nfl_places = places
                        self._nfl_places_ts = time.time()
                    self.api.setStatus('RUNNING', f'avnav-poi: {len(places)} NFL places loaded')
                except Exception as ex:
                    self.api.error('NFL places fetch failed: %s', str(ex))

        # Community markers — TTL from config
        if self._bool('showNFLCommunity') and not self._stopped:
            ttl = self._int('cacheTTL', 120) * 60
            if now - self._nfl_community_ts > ttl:
                try:
                    community = _fetch_nfl_community()
                    with self._lock:
                        self._nfl_community = community
                        self._nfl_community_ts = time.time()
                except Exception as ex:
                    self.api.error('NFL community fetch failed: %s', str(ex))


        # Tide stations — 24h cache
        if self._bool('showTideStations') and not self._stopped:
            if now - self._tidestation_ts > NFL_TIDES_TTL:
                stations = self._load_tides_cache()
                if stations is not None:
                    with self._lock:
                        self._tidestations = stations
                        self._tidestation_ts = now
                else:
                    try:
                        stations = _fetch_nfl_tidestations()
                        self._save_tides_cache(stations)
                        with self._lock:
                            self._tidestations = stations
                            self._tidestation_ts = time.time()
                    except Exception as ex:
                        self.api.error('Tide station fetch failed: %s', str(ex))

        self._write_overlay_file()

    # ------------------------------------------------------------------
    # Disk cache helpers
    # ------------------------------------------------------------------

    def _load_places_cache(self):
        try:
            if os.path.exists(self._places_cache):
                age = time.time() - os.path.getmtime(self._places_cache)
                if age < NFL_PLACES_TTL:
                    with gzip.open(self._places_cache, 'rt') as f:
                        return json.load(f)
        except Exception:
            pass
        return None

    def _save_places_cache(self, places):
        try:
            with gzip.open(self._places_cache, 'wt') as f:
                json.dump(places, f)
        except Exception as ex:
            self.api.error('places cache write: %s', str(ex))

    def _load_tides_cache(self):
        try:
            if os.path.exists(self._tides_cache):
                age = time.time() - os.path.getmtime(self._tides_cache)
                if age < NFL_TIDES_TTL:
                    with open(self._tides_cache) as f:
                        return json.load(f)
        except Exception:
            pass
        return None

    def _save_tides_cache(self, stations):
        try:
            with open(self._tides_cache, 'w') as f:
                json.dump(stations, f)
        except Exception as ex:
            self.api.error('tides cache write: %s', str(ex))

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _bool(self, key, default='true'):
        return str(self.api.getConfigValue(key, default)).lower() not in ('false', '0', 'no')

    def _int(self, key, default):
        try:   return int(self.api.getConfigValue(key, str(default)))
        except: return default
