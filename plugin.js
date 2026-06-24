/*
 * Nautical POI overlay — client side
 * Legacy plugin (plugin.js): uses AVNAV_BASE_URL injected by avnav server.
 * Icons: NFL CDN  https://www.noforeignland.com/nfl-web-images/markers/{name}-60.png
 * Popup: direct DOM overlay (avnav.api.showDialog not available in legacy plugins)
 */
(function () {
    'use strict';

    var BASE    = AVNAV_BASE_URL;  // injected by avnav server, e.g. '/plugins/user-avnav-poi-integration'
    var NFL_MAP      = 'https://www.noforeignland.com/map/place/';
    var NFL_MAP_POST = 'https://www.noforeignland.com/map/post/';

    // NFL display_type → CDN icon filename stem
    var NFL_ICON_NAME = {
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
        // Community marker types
        'EVENT':                 'communityEvent',
        'WARNING':               'communityWarning',
        'QUESTION':              'communityQuestion',
        'CREW_AVAILABLE':        'communityCrew',
        // Guide areas
        'guide_area':            'book',
    };

    // Fallback icons (our own SVGs) for types without an NFL CDN icon
    var FALLBACK_ICON = BASE + '/icons/generic.svg';
    var FALLBACK_ICONS = {
        'tide_station': BASE + '/icons/tide_station.svg',
    };

    function getIconUrl(props) {
        var dtype = props.display_type || props.type || '';
        var stem = NFL_ICON_NAME[dtype];
        if (stem) return BASE + '/icons/nfl/' + stem + '-60.png';
        return FALLBACK_ICONS[props.type] || FALLBACK_ICON;
    }

    // Captured when extended=true fires for the feature avnav picks per tap
    var _tapNflId = null;
    var _tapLat   = null;
    var _tapLon   = null;

    avnav.api.registerFeatureFormatter('avnav-poi-integration', function (props, extended) {
        if (!props || !props.source) return {};

        var type  = props.type   || 'generic';
        var name  = props.name   || 'Unknown';
        var nflId = props.nfl_id || '';

        if (!extended) {
            return { name: name, sym: getIconUrl(props) };
        }

        var label = (props.display_type || type).replace(/_/g, ' ');

        if (!nflId) {
            return { name: name };
        }

        // Remember which feature avnav picked and where, so the feature-list
        // watcher can tag and expand entries for nearby POIs
        _tapNflId = String(nflId);
        _tapLat   = props.lat;
        _tapLon   = props.lon;

        // Pre-fetch detail so it's ready when the user opens the popup
        var dtype = props.display_type || type || '';
        if (COMMUNITY_TYPES[dtype]) {
            _getCommunityPost(String(nflId), function() {});
        } else {
            _getPlace(String(nflId), function() {});
        }

        // Store name+label+type keyed by nflId (type needed for icon lookup)
        window._poiStore = window._poiStore || {};
        window._poiStore[String(nflId)] = {
            name: name, label: label,
            display_type: props.display_type || '',
            type: type
        };

        return { name: name };
    });

    // ------------------------------------------------------------------
    // Nearly-full-screen modal overlay
    // Semi-transparent backdrop; card with margin so avnav shows behind.
    // Tapping the backdrop closes it.
    // ------------------------------------------------------------------
    function _openDialog(title, bodyHtml) {
        var existing = document.getElementById('poi-overlay');
        if (existing) existing.parentNode.removeChild(existing);

        // Backdrop
        var backdrop = document.createElement('div');
        backdrop.id = 'poi-overlay';
        backdrop.className = 'poi-backdrop';
        backdrop.onclick = function (e) {
            if (e.target === backdrop) backdrop.parentNode.removeChild(backdrop);
        };

        // Card
        var card = document.createElement('div');
        card.className = 'poi-card';

        // Header
        var header = document.createElement('div');
        header.className = 'poi-header';

        var titleEl = document.createElement('div');
        titleEl.className = 'poi-title';
        titleEl.textContent = title;

        var closeBtn = document.createElement('button');
        closeBtn.className = 'poi-close';
        closeBtn.textContent = '✕';
        closeBtn.onclick = function () {
            if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
        };

        header.appendChild(titleEl);
        header.appendChild(closeBtn);

        // Scrollable body
        var body = document.createElement('div');
        body.className = 'poi-body';
        body.innerHTML = bodyHtml;

        card.appendChild(header);
        card.appendChild(body);
        backdrop.appendChild(card);
        document.body.appendChild(backdrop);
        return body;
    }

    // Community marker types that use the community/post API instead of place API
    var COMMUNITY_TYPES = { EVENT: 1, WARNING: 1, QUESTION: 1, CREW_AVAILABLE: 1 };

    // ------------------------------------------------------------------
    // Data caches (in-memory; server has disk cache for persistence)
    // ------------------------------------------------------------------
    var _placeCache     = {};
    var _communityCache = {};

    function _makeCache(apiUrl, store) {
        return function(id, cb) {
            var key = String(id);
            var entry = store[key];
            if (entry && entry.status === 'ready') { cb(entry.data); return; }
            if (entry && entry.status === 'error') { cb(null);       return; }
            if (!entry) {
                store[key] = { status: 'loading', data: null, cbs: [] };
                fetch(BASE + apiUrl + encodeURIComponent(key))
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        store[key].status = 'ready';
                        store[key].data   = data;
                        store[key].cbs.forEach(function(f) { f(data); });
                        store[key].cbs = [];
                    })
                    .catch(function() {
                        store[key].status = 'error';
                        store[key].cbs.forEach(function(f) { f(null); });
                        store[key].cbs = [];
                    });
            }
            store[key].cbs.push(cb);
        };
    }

    var _getPlace         = _makeCache('/api/place?id=',          _placeCache);
    var _getCommunityPost = _makeCache('/api/community-post?id=', _communityCache);

    // ------------------------------------------------------------------
    // Feature list — patch poi entries and intercept clicks
    //
    // avnav's featureListToInfo returns only ONE feature per overlay source,
    // so multiple nearby POIs collapse to one "avnav-poi-integration.geojson"
    // entry. Strategy:
    //   1. Tag the base entry immediately (sync) so clicks work right away
    //   2. Fetch /api/nearby and clone an entry per additional POI
    //   3. One persistent capture-phase listener on document handles all clicks
    //   4. Re-patch when _tapNflId changes (handles React reusing same dialog DOM)
    // ------------------------------------------------------------------
    var OVERLAY_NAME = 'avnav-poi-integration.geojson';
    var _patchedFor  = null;   // last _tapNflId we successfully patched for

    function _openPoiPopup(nflId) {
        var ctx    = (window._poiStore || {})[String(nflId)] || {};
        var bodyEl = _openDialog(ctx.name || 'POI', '<p class="poi-loading">Loading…</p>');
        var dtype  = ctx.display_type || ctx.type || '';
        if (COMMUNITY_TYPES[dtype]) {
            _getCommunityPost(String(nflId), function(post) {
                if (!bodyEl.isConnected) return;
                if (post && post.id) {
                    bodyEl.innerHTML = _buildCommunityHtml(post, ctx.label || dtype, nflId);
                } else {
                    bodyEl.innerHTML = _buildBasicHtml({ nfl_id: nflId }, ctx.label || '', nflId);
                }
            });
        } else {
            _getPlace(String(nflId), function(place) {
                if (!bodyEl.isConnected) return;
                if (place && place.name) {
                    bodyEl.innerHTML = _buildDetailHtml(place, ctx.label || '', nflId);
                    _wireGallery(bodyEl);
                } else {
                    bodyEl.innerHTML = _buildBasicHtml({ nfl_id: nflId }, ctx.label || '', nflId);
                }
            });
        }
    }

    // Single persistent listener — handles every poi click regardless of how
    // many times the dialog DOM is reused across taps
    document.addEventListener('click', function(e) {
        var entry = e.target.closest('[data-poi-id]');
        if (!entry) return;
        e.stopImmediatePropagation();
        e.preventDefault();
        _openPoiPopup(entry.dataset.poiId);
    }, true);

    function _setListIcon(entry, props) {
        var slot = entry.querySelector('.listSlot:not(.listMain)');
        if (!slot) return;
        var iconDiv = slot.querySelector('.icon');
        if (!iconDiv) return;
        // Override the CSS-class background with our PNG, keeping the div in-place
        iconDiv.style.backgroundImage    = 'url(' + getIconUrl(props) + ')';
        iconDiv.style.backgroundSize     = 'contain';
        iconDiv.style.backgroundRepeat   = 'no-repeat';
        iconDiv.style.backgroundPosition = 'center';
        iconDiv.className = 'icon';  // drop type class so CSS doesn't clobber inline style
    }

    function _patchFeatureList(dialog, nflId, lat, lon) {
        // Find the one poi entry avnav inserted
        var baseEntry = null;
        var entries = dialog.querySelectorAll('.listEntry');
        for (var i = 0; i < entries.length; i++) {
            var p = entries[i].querySelector('.primary');
            if (p && p.textContent.trim() === OVERLAY_NAME) { baseEntry = entries[i]; break; }
        }
        if (!baseEntry) return;

        // Remove stale clones from a previous tap
        var stale = dialog.querySelectorAll('.listEntry[data-poi-clone]');
        for (var s = 0; s < stale.length; s++) stale[s].parentNode.removeChild(stale[s]);

        // Tag immediately so the click handler works before the async fetch
        var ctx = (window._poiStore || {})[nflId] || {};
        baseEntry.dataset.poiId = nflId;
        baseEntry.querySelector('.primary').textContent = ctx.name || 'POI';
        _setListIcon(baseEntry, { display_type: ctx.display_type || '', type: ctx.type || '' });

        // Expand with nearby results
        if (lat == null || lon == null) return;
        var base = baseEntry;
        fetch(BASE + '/api/nearby?lat=' + lat + '&lon=' + lon + '&r=0.2')
            .then(function(r) { return r.json(); })
            .then(function(nearby) {
                if (!Array.isArray(nearby) || nearby.length <= 1) return;
                var insertAfter = base;
                nearby.forEach(function(p, idx) {
                    var id = String(p.nfl_id);
                    window._poiStore = window._poiStore || {};
                    if (!window._poiStore[id]) {
                        window._poiStore[id] = {
                            name:         p.name,
                            label:        (p.display_type || p.type || '').replace(/_/g, ' '),
                            display_type: p.display_type || '',
                            type:         p.type || ''
                        };
                    }
                    _getPlace(id, function() {});
                    var c  = window._poiStore[id];
                    var el;
                    if (idx === 0) {
                        el = base;
                        delete el.dataset.poiClone;
                    } else {
                        el = base.cloneNode(true);
                        el.dataset.poiClone = '1';
                        insertAfter.parentNode.insertBefore(el, insertAfter.nextSibling);
                    }
                    el.querySelector('.primary').textContent = c.name || p.name || 'POI';
                    el.dataset.poiId = id;
                    _setListIcon(el, { display_type: c.display_type || p.display_type || '', type: c.type || p.type || '' });
                    insertAfter = el;
                });
            })
            .catch(function() {});
    }

    // Re-patch whenever the feature list changes AND we have a new tap.
    // Reset _patchedFor when the dialog is gone so the same POI can be re-patched
    // if the user closes and reopens it.
    new MutationObserver(function() {
        if (!_tapNflId) return;
        var dialog = document.querySelector('.featureListDialog');
        if (!dialog) { _patchedFor = null; return; }
        if (_patchedFor === _tapNflId) return;
        _patchedFor = _tapNflId;
        _patchFeatureList(dialog, _tapNflId, _tapLat, _tapLon);
    }).observe(document.body, { childList: true, subtree: true });

    // ------------------------------------------------------------------
    // Lightbox
    // ------------------------------------------------------------------
    function _wireGallery(bodyEl) {
        var gallery = bodyEl.querySelector('.poi-gallery');
        if (!gallery) return;
        var imgs = Array.prototype.slice.call(gallery.querySelectorAll('.poi-gallery-img'));
        var urls = imgs.map(function(img) { return img.getAttribute('data-full') || img.src; });
        imgs.forEach(function(img, idx) {
            img.style.cursor = 'pointer';
            img.onclick = function(e) {
                e.stopPropagation();
                _openLightbox(urls, idx);
            };
        });
    }

    function _openLightbox(urls, startIdx) {
        var existing = document.getElementById('poi-lightbox');
        if (existing) existing.parentNode.removeChild(existing);

        var cur = startIdx;
        var lb = document.createElement('div');
        lb.id = 'poi-lightbox';
        lb.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;' +
            'background:rgba(0,0,0,0.92);z-index:19999999;display:flex;' +
            'align-items:center;justify-content:center;';

        var img = document.createElement('img');
        img.style.cssText = 'max-width:95%;max-height:90vh;object-fit:contain;' +
            'border-radius:6px;display:block;user-select:none;';

        function show(idx) {
            cur = (idx + urls.length) % urls.length;
            img.src = urls[cur];
            counter.textContent = (cur + 1) + ' / ' + urls.length;
        }

        // Close on backdrop tap (not on image or buttons)
        lb.onclick = function(e) {
            if (e.target === lb) lb.parentNode.removeChild(lb);
        };

        // Close button
        var closeBtn = document.createElement('button');
        closeBtn.textContent = '✕';
        closeBtn.style.cssText = 'position:absolute;top:14px;right:16px;background:rgba(255,255,255,0.18);' +
            'border:none;color:#fff;border-radius:50%;width:38px;height:38px;font-size:20px;' +
            'cursor:pointer;display:flex;align-items:center;justify-content:center;';
        closeBtn.onclick = function() { lb.parentNode.removeChild(lb); };

        // Counter label
        var counter = document.createElement('div');
        counter.style.cssText = 'position:absolute;bottom:18px;left:50%;transform:translateX(-50%);' +
            'color:rgba(255,255,255,0.7);font-size:13px;font-family:sans-serif;';

        lb.appendChild(img);
        lb.appendChild(closeBtn);
        lb.appendChild(counter);

        // Prev / next only when more than one image
        if (urls.length > 1) {
            function navBtn(label, side, delta) {
                var btn = document.createElement('button');
                btn.textContent = label;
                btn.style.cssText = 'position:absolute;top:50%;' + side + ':12px;transform:translateY(-50%);' +
                    'background:rgba(255,255,255,0.18);border:none;color:#fff;border-radius:50%;' +
                    'width:42px;height:42px;font-size:22px;cursor:pointer;' +
                    'display:flex;align-items:center;justify-content:center;';
                btn.onclick = function(e) { e.stopPropagation(); show(cur + delta); };
                return btn;
            }
            lb.appendChild(navBtn('‹', 'left',  -1));
            lb.appendChild(navBtn('›', 'right', +1));

            // Swipe support
            var touchStartX = 0;
            lb.addEventListener('touchstart', function(e) {
                touchStartX = e.touches[0].clientX;
            }, {passive: true});
            lb.addEventListener('touchend', function(e) {
                var dx = e.changedTouches[0].clientX - touchStartX;
                if (Math.abs(dx) > 40) show(cur + (dx < 0 ? 1 : -1));
            }, {passive: true});
        }

        document.body.appendChild(lb);
        show(cur);
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------
    function _fmtDate(ms) {
        if (!ms) return '';
        var d = new Date(parseInt(ms, 10));
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'});
    }

    // ------------------------------------------------------------------
    // Wind protection compass rose
    // ------------------------------------------------------------------
    var WIND_DIRS   = ['N','NE','E','SE','S','SW','W','NW'];
    var WIND_ANGLES = {N:0,NE:45,E:90,SE:135,S:180,SW:225,W:270,NW:315};

    function _windRose(windStr) {
        if (!windStr) return '';
        var prot = {};
        windStr.toUpperCase().split(',').forEach(function(d) { prot[d.trim()] = true; });
        var cx = 44, cy = 44, r = 32;
        var svg = '<svg class="poi-windrose" viewBox="0 0 88 88" xmlns="http://www.w3.org/2000/svg">';
        svg += '<circle cx="44" cy="44" r="38" fill="#f0f4fa" stroke="#cdd5e0" stroke-width="1.5"/>';
        WIND_DIRS.forEach(function(dir) {
            if (!prot[dir]) return;
            var a1 = (WIND_ANGLES[dir] - 22.5) * Math.PI / 180;
            var a2 = (WIND_ANGLES[dir] + 22.5) * Math.PI / 180;
            var x1 = cx + r * Math.sin(a1), y1 = cy - r * Math.cos(a1);
            var x2 = cx + r * Math.sin(a2), y2 = cy - r * Math.cos(a2);
            svg += '<path d="M' + cx + ',' + cy + ' L' + x1.toFixed(1) + ',' + y1.toFixed(1) +
                   ' A' + r + ',' + r + ' 0 0,1 ' + x2.toFixed(1) + ',' + y2.toFixed(1) + ' Z"' +
                   ' fill="#3a8fd8" opacity="0.75"/>';
        });
        svg += '<text x="44" y="10"  text-anchor="middle" font-size="9" fill="#444" font-family="sans-serif">N</text>';
        svg += '<text x="78" y="47"  text-anchor="middle" font-size="9" fill="#444" font-family="sans-serif">E</text>';
        svg += '<text x="44" y="82"  text-anchor="middle" font-size="9" fill="#444" font-family="sans-serif">S</text>';
        svg += '<text x="10" y="47"  text-anchor="middle" font-size="9" fill="#444" font-family="sans-serif">W</text>';
        svg += '<circle cx="44" cy="44" r="3" fill="#1a6bb0"/>';
        svg += '</svg>';
        return svg;
    }

    // ------------------------------------------------------------------
    // Shore facilities  (real keys from NFL: elec, pump, bikes …)
    // ------------------------------------------------------------------
    var FACILITY_LABELS = {
        water:       'Water',
        elec:        'Electricity',
        electricity: 'Electricity',
        showers:     'Showers',
        toilets:     'Toilets',
        wifi:        'WiFi',
        pump:        'Pump-out',
        pump_out:    'Pump-out',
        garbage:     'Garbage',
        laundry:     'Laundry',
        restaurant:  'Restaurant',
        food:        'Food',
        bar:         'Bar',
        fuel:        'Fuel',
        bikes:       'Bikes',
        slip:        'Slipway',
        crane:       'Crane',
        travel_lift: 'Travel lift',
        chandler:    'Chandler',
        pool:        'Pool',
        signal:      'Customs',
        dinghy_dock: 'Dinghy dock',
        gas:         'Gas',
        ice:         'Ice',
        supermarket: 'Supermarket',
        atm:         'ATM',
        beach:       'Beach',
        provisions:  'Provisions',
    };

    function _facilities(facStr) {
        if (!facStr) return '';
        var items = facStr.split(',').map(function(s) { return s.trim().toLowerCase(); }).filter(Boolean);
        if (!items.length) return '';
        var h = '<div class="poi-facilities">';
        items.forEach(function(f) {
            h += '<span class="poi-fac-chip">' + _esc(FACILITY_LABELS[f] || f.replace(/_/g,' ')) + '</span>';
        });
        return h + '</div>';
    }

    // ------------------------------------------------------------------
    // Detail HTML (after NFL place API fetch)
    // ------------------------------------------------------------------
    function _buildDetailHtml(place, label, nflId) {
        var h = '';
        var pm = place.meta || {};

        h += '<div class="poi-label">' + _esc(label) + '</div>';

        var stars = parseFloat(place.stars || 0);
        if (stars > 0) {
            h += '<div class="poi-stars">' + _stars(stars) +
                 ' <span class="poi-stars-num">(' + stars.toFixed(1) + ')</span></div>';
        }

        // All images — horizontal scrollable strip
        var images = place.images || [];
        if (images.length) {
            h += '<div class="poi-gallery">';
            images.forEach(function(img) {
                if (!img.servingUrl) return;
                var aspect = img.width && img.height ? (img.width / img.height) : 1.5;
                var w = Math.round(150 * aspect);
                var src = _imgUrl(img.servingUrl);
                h += '<img class="poi-gallery-img" src="' + _esc(src) +
                     '" data-full="' + _esc(src) +
                     '" width="' + w + '" height="150"' +
                     (img.caption ? ' alt="' + _esc(img.caption) + '"' : '') + '>';
            });
            h += '</div>';
        }

        // Description
        var desc = place.comments || '';
        if (desc) h += '<div class="poi-desc">' + desc + '</div>';

        // Wind protection + ashore facilities
        var windHtml = _windRose(pm.wind);
        var facHtml  = _facilities(pm.facilities);
        if (windHtml || facHtml) {
            h += '<div class="poi-env">';
            if (windHtml) {
                h += '<div class="poi-wind-block"><div class="poi-section-title">Wind protection</div>' + windHtml + '</div>';
            }
            if (facHtml) {
                h += '<div class="poi-fac-block"><div class="poi-section-title">Ashore</div>' + facHtml + '</div>';
            }
            h += '</div>';
        }

        // Key facts grid
        var meta = [];
        if (pm.vhf) meta.push(['VHF', 'Ch ' + pm.vhf]);

        var diesel = parseFloat(pm.fuelDieselPrice || 0);
        if (diesel > 0) {
            var dDate = _fmtDate(pm.fuelDieselPriceUpdatedMs);
            meta.push(['Diesel', diesel.toFixed(2) + '/L' + (dDate ? ' (updated ' + dDate + ')' : '')]);
        }
        var petrol = parseFloat(pm.fuelGasolinePrice || 0);
        if (petrol > 0) {
            var gDate = _fmtDate(pm.fuelGasolinePriceUpdatedMs);
            meta.push(['Gasoline', petrol.toFixed(2) + '/L' + (gDate ? ' (updated ' + gDate + ')' : '')]);
        }
        if (pm.paymentMethod) meta.push(['Payment', pm.paymentMethod]);

        var hiPrice = (pm.priceHighSeason || '').trim();
        var loPrice = (pm.priceLowSeason  || '').trim();
        if (hiPrice) meta.push(['Price (high season)', hiPrice]);
        if (loPrice) meta.push(['Price (low season)',  loPrice]);

        if (pm.depth)     meta.push(['Max depth',  pm.depth + ' m']);
        if (pm.maxLength) meta.push(['Max length', pm.maxLength + ' m']);
        if (pm.maxBeam)   meta.push(['Max beam',   pm.maxBeam + ' m']);
        if (pm.berths)    meta.push(['Berths',      pm.berths]);

        if (pm.tel)   meta.push(['Tel',   '<a href="tel:' + _esc(pm.tel) + '">' + _esc(pm.tel) + '</a>']);
        if (pm.email) meta.push(['Email', '<a href="mailto:' + _esc(pm.email) + '">' + _esc(pm.email) + '</a>']);

        var winterOpen = (pm.winterCommunity || '').toLowerCase() === 'true';
        if (winterOpen) meta.push(['Winter', 'Open year-round']);

        if (place.addedBy) {
            var addedDate = _fmtDate(place.addedMs);
            meta.push(['Added by', place.addedBy + (addedDate ? ', ' + addedDate : '')]);
        }
        if (place.updatedBy) {
            var updDate = _fmtDate(place.updatedMs);
            meta.push(['Updated by', place.updatedBy + (updDate ? ', ' + updDate : '')]);
        }

        if (meta.length) {
            h += '<div class="poi-meta">';
            for (var i = 0; i < meta.length; i++) {
                h += '<span class="poi-meta-key">' + _esc(meta[i][0]) + '</span>';
                // tel/email values already contain HTML — don't escape them
                var val = meta[i][1];
                h += '<span>' + (val.indexOf('<a ') === 0 ? val : _esc(String(val))) + '</span>';
            }
            h += '</div>';
        }

        // External links from meta.links (URL-encoded, comma-separated)
        var rawLinks = (pm.links || '').split(',').map(function(l) {
            try { return decodeURIComponent(l.trim()); } catch(e) { return l.trim(); }
        }).filter(function(l) { return l && l.indexOf('http') === 0; });
        if (rawLinks.length) {
            h += '<div class="poi-links"><div class="poi-section-title">Links</div>';
            rawLinks.forEach(function(url) {
                var display = url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
                h += '<a class="poi-link" href="' + _esc(url) + '" target="_blank">' + _esc(display) + '</a>';
            });
            h += '</div>';
        }

        // Reviews
        var reviews = place.reviews || [];
        if (reviews.length) {
            h += '<div class="poi-reviews"><div class="poi-section-title">Reviews (' + reviews.length + ')</div>';
            for (var j = 0; j < reviews.length; j++) {
                var rv = reviews[j];
                var rvDate = _fmtDate(rv.time);
                h += '<div class="poi-review">';
                h += '<div class="poi-review-header">';
                if (rv.stars)    h += '<span class="poi-stars poi-stars-sm">' + _stars(rv.stars) + '</span>';
                if (rv.userName) h += '<b>' + _esc(rv.userName) + '</b>';
                if (rvDate)      h += '<span class="poi-review-date">' + _esc(rvDate) + '</span>';
                h += '</div>';
                if (rv.comments) h += '<div class="poi-review-text">' + rv.comments + '</div>';
                h += '</div>';
            }
            h += '</div>';
        }

        h += '<a class="poi-btn" href="' + NFL_MAP + _esc(String(nflId)) +
             '" target="_blank">View on NoForeignLand →</a>';
        return h;
    }

    // ------------------------------------------------------------------
    // Basic HTML (community markers, guide areas, or fetch failure)
    // ------------------------------------------------------------------
    function _buildCommunityHtml(post, label, nflId) {
        var h = '';
        h += '<div class="poi-label">' + _esc(label || post.type || '') + '</div>';

        // Title
        if (post.title) {
            h += '<h3 style="margin:0 0 8px;font-size:16px;font-weight:700">' + _esc(post.title) + '</h3>';
        }

        // Author + date
        var author = (post.addedByUser && post.addedByUser.displayName) || post.addedBy || '';
        var dateStr = post.addedMs ? _fmtDate(post.addedMs) : '';
        if (author || dateStr) {
            h += '<div style="font-size:12px;color:#888;margin-bottom:10px">';
            if (author) h += 'By ' + _esc(author);
            if (author && dateStr) h += ' &nbsp;·&nbsp; ';
            if (dateStr) h += dateStr;
            h += '</div>';
        }

        // Crew skills
        var skills = post.addedByUser && post.addedByUser.skills;
        if (skills) {
            h += '<div class="poi-meta" style="margin-bottom:10px">';
            h += '<span class="poi-meta-key">Skills</span><span>' + _esc(skills) + '</span>';
            h += '</div>';
        }

        // Event dates
        if (post.eventDatesMs && post.eventDatesMs.length) {
            h += '<div class="poi-meta" style="margin-bottom:10px">';
            h += '<span class="poi-meta-key">Dates</span><span>';
            h += post.eventDatesMs.map(function(ms) { return _fmtDate(ms); }).join(' – ');
            h += '</span></div>';
        }

        // Expiry (crew / question)
        if (post.expiresMs && post.expiresMs > Date.now()) {
            h += '<div class="poi-meta" style="margin-bottom:10px">';
            h += '<span class="poi-meta-key">Expires</span><span>' + _fmtDate(post.expiresMs) + '</span>';
            h += '</div>';
        }

        // Banner image
        if (post.banner) {
            h += '<img src="' + _esc(_imgUrl(post.banner)) + '" style="width:100%;border-radius:8px;margin-bottom:10px" loading="lazy"/>';
        }

        // Description (HTML from NFL)
        if (post.description) {
            h += '<div class="poi-desc">' + post.description + '</div>';
        }

        // Replies count
        if (post.messageCount) {
            h += '<div style="font-size:12px;color:#888;margin-top:6px">' + post.messageCount + ' repl' + (post.messageCount === 1 ? 'y' : 'ies') + '</div>';
        }

        h += '<a class="poi-btn" href="' + NFL_MAP_POST + _esc(String(nflId)) +
             '" target="_blank">View on NoForeignLand →</a>';
        return h;
    }

    function _buildBasicHtml(props, label, nflId) {
        var h = '';
        h += '<div class="poi-label">' + _esc(label) + '</div>';
        var desc = props.description || '';
        if (desc) h += '<div class="poi-desc">' + _esc(desc) + '</div>';
        var url = props.url || '';
        if (url) {
            h += '<a class="poi-btn" href="' + _esc(url) + '" target="_blank">View on NoForeignLand →</a>';
        } else if (nflId) {
            h += '<a class="poi-btn" href="' + NFL_MAP + _esc(String(nflId)) +
                 '" target="_blank">View on NoForeignLand →</a>';
        }
        return h;
    }

    function _imgUrl(rawUrl) {
        if (!rawUrl) return '';
        return BASE + '/api/image?url=' + encodeURIComponent(rawUrl);
    }

    function _stars(n) {
        var s = ''; var f = Math.round(n);
        for (var i = 1; i <= 5; i++) s += i <= f ? '★' : '☆';
        return s;
    }

    function _esc(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    avnav.api.log('avnav-poi-integration formatter registered (NFL CDN icons, DOM overlay)');

})();
