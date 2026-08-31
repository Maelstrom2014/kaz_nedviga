    // === Map init ===
    const map = L.map('map').setView([43.2567, 76.9286], 11);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap', maxZoom: 18
    }).addTo(map);

    let heatLayer = null;
    let districtLayers = [];  // polygons + markers
    let listingMarkers = null;  // MarkerClusterGroup for listings
    let listingMarkerRefs = [];  // marker objects keyed by index
    let activeCardIdx = null;  // currently highlighted card
    let districtsVisible = true;
    let heatVisible = true;
    let favoritesOnlyMode = false;  // when true, map shows only favorite markers
    let districtsData = [];
    let currentResults = [];
    let displayedResults = [];  // filtered subset shown in the grid + map

    // === Load districts ===
    async function loadDistricts() {
      const resp = await fetch('/api/districts');
      districtsData = await resp.json();
      drawDistricts();
    }

    function drawDistricts() {
      districtLayers.forEach(l => map.removeLayer(l));
      districtLayers = [];
      if (!districtsVisible) return;
      districtsData.forEach(d => {
        const count = countListingsInDistrict(d.name);
        const polygon = L.polygon(d.polygon, {
          color: '#3b82f6', weight: 2, opacity: 0.6,
          fillColor: '#3b82f6', fillOpacity: count > 0 ? 0.15 : 0.05
        }).addTo(map);
        polygon.bindPopup(`
          <div class="district-popup">
            <h3>${d.name}</h3>
            <p>${d.description || ''}</p>
            <div class="stat"><span>Найдено квартир:</span><b>${count}</b></div>
          </div>
        `);
        const label = L.marker([d.lat, d.lon], {
          icon: L.divIcon({
            className: 'district-label',
            html: `<div style="background:rgba(15,23,42,.85);color:#f1f5f9;padding:4px 8px;border-radius:6px;font-size:.75rem;white-space:nowrap;text-align:center;border:1px solid #3b82f6;">
              <b>${d.name}</b>${count > 0 ? `<br><span style="color:#22c55e;font-weight:700;">квартир: ${count}</span>` : ''}
            </div>`,
            iconSize: [130, 45], iconAnchor: [65, 22]
          })
        }).addTo(map);
        districtLayers.push(polygon, label);
      });
    }

    function countListingsInDistrict(districtName) {
      if (!displayedResults || !districtName) return 0;
      // Match the FULL district name with word boundaries so "ул. Ауэзова"
      // does NOT match "Ауэзовский" (shared root, different word).
      const escaped = districtName.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp('\\b' + escaped + '\\b');
      return displayedResults.filter(r => {
        const hay = ((r.address || '') + ' ' + (r.title || '')).toLowerCase();
        return re.test(hay);
      }).length;
    }

    function drawListingMarkers() {
      if (listingMarkers) { map.removeLayer(listingMarkers); listingMarkers = null; }
      listingMarkerRefs = [];
      if (!displayedResults || !displayedResults.length) return;
      listingMarkers = L.layerGroup();
      displayedResults.forEach((r, i) => {
        const coords = geocodeListing(r, i);
        if (!coords) return;
        const priceStr = r.price ? r.price.toLocaleString('ru-RU') + ' ₸' : '—';
        const photo = r.photo ? r.photo.split('|')[0] : '';
        const photoHtml = photo ? `<img src="${safeUrl(photo)}" style="width:100%;max-height:110px;object-fit:contain;border-radius:6px;margin-bottom:6px;" onerror="this.style.display='none'">` : '';
        const altParts = [];
        if (r.price_rub != null) altParts.push(r.price_rub.toLocaleString('ru-RU') + ' ₽');
        if (r.price_usd != null) altParts.push('$' + r.price_usd);
        const altPrice = altParts.join(' · ');
        const addrLine = [escapeHtml(r.address || ''), r.source].filter(Boolean).join(' · ');
        // Compact 3-currency label for the marker (thousands abbreviated: 250к ₸ · 48к ₽ · $532)
        const fmtK = v => v >= 1000 ? Math.round(v / 1000) + 'к' : String(v);
        let markerLabel = priceStr;
        if (r.price) {
          const parts = [fmtK(r.price) + ' ₸'];
          if (r.price_rub != null) parts.push(fmtK(Math.round(r.price_rub)) + ' ₽');
          if (r.price_usd != null) parts.push('$' + Math.round(r.price_usd));
          markerLabel = parts.join(' · ');
        }
        const marker = L.marker(coords, {
          icon: L.divIcon({
            className: 'listing-marker',
            html: `<div style="background:var(--accent);color:#fff;padding:2px 7px;border-radius:10px;font-size:.62rem;font-weight:700;white-space:nowrap;box-shadow:0 2px 5px rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.25);">${markerLabel}</div>`,
            iconSize: [120, 16], iconAnchor: [60, 8]
          })
        });
        marker.on('click', () => focusCard(i));
        marker.bindPopup(`
          <div style="min-width:190px;max-width:260px;">
            ${photoHtml}
            <div style="font-weight:600;font-size:.85rem;margin-bottom:4px;">${escapeHtml(r.title)}</div>
            <div style="color:#3b82f6;font-weight:800;font-size:1.05rem;">${priceStr}</div>
            ${altPrice ? `<div style="font-size:.78rem;color:#666;font-weight:600;margin-top:2px;">${altPrice}</div>` : ''}
            ${addrLine ? `<div style="font-size:.75rem;color:#666;margin-top:4px;">${addrLine}</div>` : ''}
             <a href="${safeUrl(r.url)}" target="_blank" rel="noopener" style="display:inline-block;margin-top:6px;color:#3b82f6;font-size:.8rem;font-weight:600;text-decoration:none;">Открыть →</a>
          </div>
        `);
        marker._listingIdx = i;
        marker.addTo(listingMarkers);
        listingMarkerRefs[i] = marker;
      });
      listingMarkers.addTo(map);
    }

    function focusMarker(idx) {
      const marker = listingMarkerRefs[idx];
      if (!marker) return;
      const ll = marker.getLatLng();
      map.flyTo([ll.lat, ll.lng], Math.max(map.getZoom(), 15), { duration: .5 });
      marker.openPopup();
      // Highlight the card
      document.querySelectorAll('.listing-card.active').forEach(el => el.classList.remove('active'));
      const card = document.querySelector(`.listing-card[data-idx="${idx}"]`);
      if (card) card.classList.add('active');
      activeCardIdx = idx;
    }

    // Reverse of focusMarker: marker clicked -> bring its card into view.
    function focusCard(idx) {
      document.querySelectorAll('.listing-card.active').forEach(el => el.classList.remove('active'));
      const card = document.querySelector(`.listing-card[data-idx="${idx}"]`);
      if (card) {
        card.classList.add('active');
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      activeCardIdx = idx;
    }

    function geocodeListing(r, idx) {
      // Real coordinates from the listing detail page (parser-enriched).
      if (r.lat != null && r.lon != null) {
        return [r.lat, r.lon];
      }
      // Try to match listing address to a district's center coords
      const addr = (r.address || '') + ' ' + (r.title || '');
      for (const d of districtsData) {
        if (addr.toLowerCase().includes(d.name.toLowerCase())) {
          // Deterministic offset derived from the URL hash so a marker
          // doesn't jump between renders (random offsets did before).
          let h = 0;
          const s = r.url || (r.title + idx);
          for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
          const offLat = ((h & 0xff) / 255 - 0.5) * 0.008;
          const offLon = (((h >>> 8) & 0xff) / 255 - 0.5) * 0.008;
          return [d.lat + offLat, d.lon + offLon];
        }
      }
      // Fallback: distribute around city center based on index
      const angle = (idx * 137.5) * Math.PI / 180; // golden angle
      const radius = 0.02 + (idx % 5) * 0.005;
      return [43.2567 + Math.sin(angle) * radius, 76.9286 + Math.cos(angle) * radius];
    }

    // === Heatmap ===
    // Server-side heatmap re-runs the parsers, so it is expensive. Cache the
    // response per (property_type, search params) and reuse it on layer
    // toggles — only a real param change triggers a new request.
    const heatmapCache = {};
    function heatmapKey(propertyType, params) {
      const p = Object.assign({}, params);
      delete p.property_type;
      return JSON.stringify({ t: propertyType, p });
    }
    async function loadHeatmap(propertyType, force) {
      // Use current search form params
      const params = getSearchParams();
      params.property_type = propertyType;
      const key = heatmapKey(propertyType, params);
      if (!force && heatmapCache[key]) {
        updateHeatmap(heatmapCache[key]);
        return heatmapCache[key];
      }
      const resp = await fetch('/api/heatmap', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(params)
      });
      const data = await resp.json();
      heatmapCache[key] = data;
      updateHeatmap(data);
      return data;
    }

    function updateHeatmap(data) {
      if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
      // Update district polygons with heat colors
      districtLayers.forEach(l => map.removeLayer(l));
      districtLayers = [];
      data.district_stats.forEach(d => {
        const intensity = d.intensity;
        const color = heatColor(intensity, d.avg_price);
        const polygon = L.polygon(d.polygon, {
          color: color.border, weight: 2, opacity: 0.8,
          fillColor: color.fill, fillOpacity: 0.35 + intensity * 0.4
        });
        if (districtsVisible) polygon.addTo(map);
        const avgPriceStr = d.avg_price > 0 ? d.avg_price.toLocaleString('ru-RU') + ' тг' : 'нет данных';
        // Use the server-side count (d.count) — it's the authoritative
        // count from the heatmap's own parser run. The client-side
        // countListingsInDistrict uses currentResults (a different dataset
        // from /api/search) so it would show 0 before any search and a
        // different number after — confusing and wrong here.
        const count = d.count;
        polygon.bindPopup(`
          <div class="district-popup">
            <h3>${d.name}</h3>
            <p>${d.description || ''}</p>
            <div class="stat"><span>Средняя цена:</span><b>${avgPriceStr}</b></div>
            <div class="stat"><span>Объявлений:</span><b style="color:#22c55e;">${count}</b></div>
          </div>
        `);
        const label = L.marker([d.lat, d.lon], {
          icon: L.divIcon({
            className: 'district-label',
            html: `<div style="background:rgba(15,23,42,.85);color:#f1f5f9;padding:4px 8px;border-radius:6px;font-size:.75rem;white-space:nowrap;text-align:center;border:1px solid ${color.border};">
              <b>${d.name}</b><br><span style="color:${color.text}">${avgPriceStr}</span>
              ${count > 0 ? `<br><span style="color:#22c55e;font-weight:700;">квартир: ${count}</span>` : ''}
            </div>`,
            iconSize: [130, 55], iconAnchor: [65, 27]
          })
        });
        if (districtsVisible) {
          label.addTo(map);
          districtLayers.push(polygon, label);
        } else {
          districtLayers.push(label);
        }
      });
      // Heat points overlay
      if (heatVisible && data.heat_points.some(p => p.price > 0)) {
        const points = data.heat_points
          .filter(p => p.price > 0)
          .map(p => [p.lat, p.lon, p.intensity]);
        heatLayer = L.heatLayer(points, {
          radius: 60, blur: 35, maxZoom: 13,
          gradient: {0.0: '#22c55e', 0.5: '#eab308', 1.0: '#ef4444'}
        }).addTo(map);
      }
    }

    function heatColor(intensity, price) {
      // green -> yellow -> red
      let r, g, b;
      if (intensity < 0.5) {
        const t = intensity * 2;
        r = Math.round(34 + (234 - 34) * t);
        g = Math.round(197 + (179 - 197) * t);
        b = Math.round(94 + (8 - 94) * t);
      } else {
        const t = (intensity - 0.5) * 2;
        r = Math.round(234 + (239 - 234) * t);
        g = Math.round(179 + (68 - 179) * t);
        b = Math.round(8 + (68 - 8) * t);
      }
      const fill = `rgb(${r},${g},${b})`;
      const border = `rgb(${Math.min(r+40,255)},${Math.min(g+40,255)},${Math.min(b+40,255)})`;
      const text = `rgb(${Math.min(r+80,255)},${Math.min(g+80,255)},${Math.min(b+80,255)})`;
      return {fill, border, text};
    }

    // === Toggles ===
    // The toggles only show/hide layers — they must NOT re-run the parsers
    // (loadHeatmap fetches from the server, which re-parses every site).
    // Re-parsing on a mere visibility toggle made the buttons feel broken
    // (long hangs, flicker, lost state). Reuse the cached heatmap data.
    document.getElementById('heatToggle').addEventListener('click', function() {
      heatVisible = !heatVisible;
      this.classList.toggle('active', heatVisible);
      const key = heatmapKey(document.getElementById('propertyType').value, getSearchParams());
      const cached = heatmapCache[key];
      if (cached) updateHeatmap(cached);
      else if (!heatVisible && heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
    });
    document.getElementById('districtsToggle').addEventListener('click', function() {
      districtsVisible = !districtsVisible;
      this.classList.toggle('active', districtsVisible);
      const key = heatmapKey(document.getElementById('propertyType').value, getSearchParams());
      const cached = heatmapCache[key];
      if (cached) updateHeatmap(cached);
      else drawDistricts();
    });
    document.getElementById('propertyType').addEventListener('change', function() {
      loadHeatmap(this.value);
    });
    document.getElementById('favoritesOnlyToggle').addEventListener('click', async function() {
      favoritesOnlyMode = !favoritesOnlyMode;
      this.classList.toggle('active', favoritesOnlyMode);
      if (favoritesOnlyMode) {
        // Load favorites from server and draw their markers on the map
        await drawFavoritesOnly();
      } else {
        // Back to normal search results
        drawListingMarkers();
      }
    });

    async function drawFavoritesOnly() {
      // Fetch all favorites and draw them as markers on the map
      try {
        const resp = await fetch('/api/favorites');
        const data = await resp.json();
        const favs = data.favorites || [];
        // Use favorites as the marker dataset
        if (listingMarkers) { map.removeLayer(listingMarkers); listingMarkers = null; }
        listingMarkerRefs = [];
        if (!favs.length) return;
        listingMarkers = L.layerGroup();
        favs.forEach((r, i) => {
          const coords = geocodeListing(r, i);
          if (!coords) return;
          const priceStr = r.price ? r.price.toLocaleString('ru-RU') + ' ₸' : '—';
          const photo = r.photo ? r.photo.split('|')[0] : '';
          const photoHtml = photo ? `<img src="${safeUrl(photo)}" style="width:100%;max-height:110px;object-fit:contain;border-radius:6px;margin-bottom:6px;" onerror="this.style.display='none'">` : '';
          const altParts = [];
          if (r.price_rub != null) altParts.push(r.price_rub.toLocaleString('ru-RU') + ' ₽');
          if (r.price_usd != null) altParts.push('$' + r.price_usd);
          const altPrice = altParts.join(' · ');
          const addrLine = [escapeHtml(r.address || ''), r.source].filter(Boolean).join(' · ');
          const fmtK = v => v >= 1000 ? Math.round(v / 1000) + 'к' : String(v);
          let markerLabel = priceStr;
          if (r.price) {
            const parts = ['★ ' + fmtK(r.price) + ' ₸'];
            if (r.price_rub != null) parts.push(fmtK(Math.round(r.price_rub)) + ' ₽');
            if (r.price_usd != null) parts.push('$' + Math.round(r.price_usd));
            markerLabel = parts.join(' · ');
          }
          const marker = L.marker(coords, {
            icon: L.divIcon({
              className: 'listing-marker',
              html: `<div style="background:#f59e0b;color:#fff;padding:2px 7px;border-radius:10px;font-size:.62rem;font-weight:700;white-space:nowrap;box-shadow:0 2px 5px rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.25);">${markerLabel}</div>`,
              iconSize: [130, 16], iconAnchor: [65, 8]
            })
          });
          marker.bindPopup(`
            <div style="min-width:190px;max-width:260px;">
              ${photoHtml}
              <div style="font-weight:600;font-size:.85rem;margin-bottom:4px;">${escapeHtml(r.title)}</div>
              <div style="color:#f59e0b;font-weight:800;font-size:1.05rem;">★ ${priceStr}</div>
              ${altPrice ? `<div style="font-size:.78rem;color:#666;font-weight:600;margin-top:2px;">${altPrice}</div>` : ''}
              ${addrLine ? `<div style="font-size:.75rem;color:#666;margin-top:4px;">${addrLine}</div>` : ''}
              <a href="${safeUrl(r.url)}" target="_blank" rel="noopener" style="display:inline-block;margin-top:6px;color:#3b82f6;font-size:.8rem;font-weight:600;text-decoration:none;">Открыть →</a>
            </div>
          `);
          marker.addTo(listingMarkers);
          listingMarkerRefs[i] = marker;
        });
        listingMarkers.addTo(map);
      } catch (err) {
        console.error('drawFavoritesOnly:', err);
        alert('Ошибка загрузки избранного: ' + err.message);
        favoritesOnlyMode = false;
        document.getElementById('favoritesOnlyToggle').classList.remove('active');
      }
    }

    // === Search ===
    async function clearResults() {
      if (!confirm('Очистить кэш результатов? Ранее найденные квартиры будут удалены, останутся только новые при следующем поиске.')) return;
      try {
        await fetch('/api/results/clear', {method: 'POST'});
        currentResults = [];
        applyClientFilters();
      } catch (err) {
        alert('Ошибка: ' + err.message);
      }
    }

    // Parser (crawl) vs search engine (filter) are split:
    //  - getCrawlParams(): sources + max_pages — collects listings into the cache.
    //  - getSearchParams(): filter criteria for the heatmap cache key / server.
    //  - applyClientFilters(): narrows the cached set live for the grid + map.
    function getCrawlParams() {
      const params = {};
      document.querySelectorAll('#sourcesGroup input[name="sources"]:checked').forEach(cb => {
        (params.sources = params.sources || []).push(cb.value);
      });
      const mp = document.getElementById('maxPagesInput');
      if (mp && mp.value) params.max_pages = mp.value;
      return params;
    }

    function getSearchParams() {
      const form = document.getElementById('searchForm');
      const fd = new FormData(form);
      const params = {};
      const rooms = fd.getAll('rooms').map(Number);
      if (rooms.length) params.rooms = rooms;
      const fields = ['query', 'price_min', 'price_max', 'district', 'floor_min', 'floor_max', 'area_min', 'area_max'];
      fields.forEach(f => {
        const v = fd.get(f);
        if (v) params[f] = v;
      });
      return params;
    }

    function getFilterCriteria() {
      const form = document.getElementById('searchForm');
      const fd = new FormData(form);
      const num = (name) => {
        const v = fd.get(name);
        const n = (v !== null && v !== '') ? Number(v) : NaN;
        return Number.isFinite(n) ? n : null;
      };
      const rooms = fd.getAll('rooms').map(Number);
      const district = (fd.get('district') || '').toString().trim();
      return {
        query: (fd.get('query') || '').toString().trim().toLowerCase(),
        rooms: rooms.length ? new Set(rooms) : null,
        price_min: num('price_min'),
        price_max: num('price_max'),
        district,
        floor_min: num('floor_min'),
        floor_max: num('floor_max'),
        area_min: num('area_min'),
        area_max: num('area_max'),
      };
    }

    function applyClientFilters() {
      const c = getFilterCriteria();
      const escaped = c.district ? c.district.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : '';
      const districtRe = escaped ? new RegExp('\\b' + escaped + '\\b') : null;
      displayedResults = currentResults.filter(r => {
        if (c.query) {
          const hay = ((r.title || '') + ' ' + (r.description || '') + ' ' + (r.address || '')).toLowerCase();
          if (!hay.includes(c.query)) return false;
        }
        if (c.rooms && !(r.rooms != null && c.rooms.has(r.rooms))) return false;
        if (c.price_min != null && (r.price == null || r.price < c.price_min)) return false;
        if (c.price_max != null && (r.price == null || r.price > c.price_max)) return false;
        if (c.area_min != null && (r.area == null || r.area < c.area_min)) return false;
        if (c.area_max != null && (r.area == null || r.area > c.area_max)) return false;
        if (c.floor_min != null && (r.floor == null || r.floor < c.floor_min)) return false;
        if (c.floor_max != null && (r.floor == null || r.floor > c.floor_max)) return false;
        if (districtRe) {
          const hay = ((r.address || '') + ' ' + (r.title || '')).toLowerCase();
          if (!districtRe.test(hay)) return false;
        }
        return true;
      });
      // Sort by date (forward/reverse) when a date-sort option is selected.
      // ISO YYYY-MM-DD dates sort correctly as plain strings; undated last.
      const dateSort = (document.getElementById('dateSort') || {}).value || '';
      if (dateSort) {
        displayedResults = displayedResults.slice().sort((a, b) => {
          const da = a.date_updated || a.date_published || '';
          const db = b.date_updated || b.date_published || '';
          if (da === db) return 0;
          if (da < db) return dateSort === 'asc' ? -1 : 1;
          return dateSort === 'asc' ? 1 : -1;
        });
      }
      renderResults(displayedResults, displayedResults.length);
      document.getElementById('exportButtons').style.display = displayedResults.length ? 'flex' : 'none';
      try { drawListingMarkers(); } catch(mErr) { console.error('drawListingMarkers:', mErr); }
      try { drawDistricts(); } catch(dErr) { console.error('drawDistricts:', dErr); }
    }

    async function runCrawl() {
      const btn = document.getElementById('crawlBtn');
      btn.disabled = true; btn.textContent = 'Идёт поиск...';
      document.getElementById('loading').classList.add('active');
      try {
        const params = getCrawlParams();
        const resp = await fetch('/api/search', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(params)
        });
        const data = await resp.json();
        currentResults = data.results;
        applyClientFilters();
        document.getElementById('loading').classList.remove('active');
        if (data.parser_stats) {
          lastParserStats = data.parser_stats;
          renderParserStats(lastParserStats);
          const errCount = data.parser_stats.filter(s => ['http_error','ssl_error','connection_error','timeout','error'].includes(s.status)).length;
          const badge = document.getElementById('analyzerBadge');
          if (badge) {
            if (errCount > 0) { badge.textContent = errCount; badge.style.display = 'inline-block'; }
            else { badge.style.display = 'none'; }
          }
        }
        loadHeatmap(document.getElementById('propertyType').value).catch(hErr => console.error('loadHeatmap:', hErr));
      } catch (err) {
        alert('Ошибка поиска: ' + err.message);
      } finally {
        btn.disabled = false; btn.textContent = 'Поиск';
        document.getElementById('loading').classList.remove('active');
      }
    }

    document.getElementById('crawlBtn').addEventListener('click', runCrawl);
    // Sidebar: Enter submits the form → just re-apply filters (live already does).
    document.getElementById('searchForm').addEventListener('submit', function(e) {
      e.preventDefault();
      applyClientFilters();
    });
    let _filterDebounce = null;
    document.getElementById('searchForm').addEventListener('input', function() {
      clearTimeout(_filterDebounce);
      _filterDebounce = setTimeout(applyClientFilters, 150);
    });
    document.getElementById('searchForm').addEventListener('change', function() {
      clearTimeout(_filterDebounce);
      _filterDebounce = setTimeout(applyClientFilters, 150);
    });
    document.getElementById('resetFiltersBtn').addEventListener('click', function() {
      const form = document.getElementById('searchForm');
      form.querySelectorAll('input[type="text"], input[type="number"]').forEach(el => { el.value = ''; });
      form.querySelectorAll('input[name="rooms"]').forEach(cb => { cb.checked = false; });
      const dist = form.querySelector('select[name="district"]');
      if (dist) dist.selectedIndex = 0;
      applyClientFilters();
    });

    function phoneKey(p) { return (p || '').replace(/\D/g, ''); }
function buildPhoneCounts(list) {
  const counts = {};
  (list || []).forEach(it => { const k = phoneKey(it.phone); if (k) counts[k] = (counts[k] || 0) + 1; });
  return counts;
}
function phoneRepeatHtml(item, counts) {
  const c = counts ? (counts[phoneKey(item.phone)] || 0) : 0;
  return c > 1 ? ' <span class="phone-repeat">[' + c + ']</span>' : '';
}
function renderResults(results, total) {
  const phoneCounts = buildPhoneCounts(results);
      const grid = document.getElementById('resultsGrid');
      if (total === undefined) total = results.length;
      document.getElementById('resultsTitle').textContent = `Результаты: ${total} объявлений`;
      if (!results.length) {
        grid.innerHTML = '<div class="empty-state">Ничего не найдено. Попробуйте изменить параметры.</div>';
        return;
      }
      grid.innerHTML = results.map((r, i) => {
        const photos = (r.photo || '').split('|').filter(p => p);
        let photoHtml;
        if (photos.length > 1) {
          const slides = photos.map((p, idx) => `<img src="${safeUrl(p)}" onerror="this.style.display='none'">`).join('');
          photoHtml = `
            <div class="photo-carousel" id="carousel-${i}">
              <div class="photo-carousel-track" id="track-${i}">${slides}</div>
              <button class="photo-nav prev" onclick="event.stopPropagation(); slidePhoto(${i},-1)">&#8249;</button>
              <button class="photo-nav next" onclick="event.stopPropagation(); slidePhoto(${i},1)">&#8250;</button>
              <div class="photo-counter" id="counter-${i}">1 / ${photos.length}</div>
            </div>`;
        } else if (photos.length === 1) {
          photoHtml = `<img class="listing-img" src="${safeUrl(photos[0])}" onerror="this.style.display='none'">`;
        } else {
          photoHtml = `<div class="listing-img" style="display:flex;align-items:center;justify-content:center;color:var(--text-dim);">Нет фото</div>`;
        }
        const roomsStr = r.rooms === 0 ? 'Студия' : (r.rooms ? r.rooms + ' комн.' : '');
        const areaStr = r.area ? r.area + ' м²' : '';
        const floorStr = r.floor ? `эт. ${r.floor}${r.total_floors ? '/' + r.total_floors : ''}` : '';
        const pricePerM2 = (r.price && r.area) ? Math.round(r.price / r.area).toLocaleString('ru-RU') + ' тг/м²' : '';
        // Slice the raw text FIRST: slicing escaped HTML can cut an entity in half.
        const descPreview = r.description ? escapeHtml(r.description.slice(0, 120)) + (r.description.length > 120 ? '…' : '') : '';
        const isSaved = savedKeys.has(r.listing_key);
        const newBadge = r.is_new ? `<div class="badge-new">NEW</div>` : '';
        let priceBadge = '';
        if (r.price_changed && r.prev_price != null) {
          const diff = r.price - r.prev_price;
          const sign = diff >= 0 ? '+' : '';
          priceBadge = `<div class="badge-price-change">цена: ${sign}${diff.toLocaleString('ru-RU')}</div>`;
        }
        // Validity check for cached cards: mark removed/expired ads, like favorites.
        let checkBadge = '';
        if (r.unavailable) {
          checkBadge += `<div class="badge-unavailable">недоступно</div>`;
        }
        if (r.check_status) {
          checkBadge += `<div class="badge-check-status" title="${escapeHtml(r.check_status)}">${escapeHtml(r.check_status)}</div>`;
        }
        return `
          <div class="listing-card ${r.unavailable ? 'unavailable' : ''}" data-idx="${i}" onclick="focusMarker(${i})">
            <button class="btn-fav ${isSaved ? 'saved' : ''}" onclick="event.stopPropagation(); toggleFavorite(${i})" title="Сохранить в избранное">${isSaved ? '★' : '☆'}</button>
            ${newBadge}
            ${priceBadge}
            ${checkBadge}
            ${photoHtml}
            <div class="listing-body">
              <div class="listing-title">${escapeHtml(r.title)}</div>
              <div class="listing-price">${r.price_str}${r.price_rub ? ` <span class="price-conv" data-price="${r.price}">· ${r.price_rub.toLocaleString('ru-RU')} руб · $${r.price_usd}</span>` : ''}</div>
              ${pricePerM2 ? `<div class="price-per-m2">${pricePerM2}</div>` : ''}
              <div class="card-meta-grid">
                ${roomsStr ? `<span class="card-chip"><b>${escapeHtml(roomsStr)}</b></span>` : ''}
                ${areaStr ? `<span class="card-chip">${escapeHtml(areaStr)}</span>` : ''}
                ${floorStr ? `<span class="card-chip">${escapeHtml(floorStr)}</span>` : ''}
              </div>
              ${r.address ? `<div style="font-size:.8rem;color:var(--text-dim);margin-top:6px;">${escapeHtml(r.address)}</div>` : ''}
              ${r.phone ? `<div class="listing-phone">Тел: <a href="tel:${r.phone.replace(/[^\d+]/g, '')}">${escapeHtml(r.phone)}</a>${phoneRepeatHtml(r, phoneCounts)}</div>` : ''}
              ${descPreview ? `<div class="desc-preview">${descPreview}</div>` : ''}
              ${(r.date_published || r.date_updated) ? `<div class="date-line">${r.date_published ? `<span>Опубликовано: ${r.date_published}</span>` : ''}${r.date_updated ? `<span>Обновлено: ${r.date_updated}</span>` : ''}</div>` : ''}
              <div style="display:flex;align-items:center;gap:8px;margin-top:6px;">
                <span class="listing-source">${r.source}</span>
                <a class="listing-link" href="${safeUrl(r.url)}" target="_blank" rel="noopener">Открыть →</a>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }

    const carouselState = {};

    function slidePhoto(idx, dir) {
      const track = document.getElementById('track-' + idx);
      if (!track) return;
      const photos = track.children.length;
      if (!carouselState[idx]) carouselState[idx] = 0;
      carouselState[idx] = (carouselState[idx] + dir + photos) % photos;
      track.style.transform = `translateX(-${carouselState[idx] * 100}%)`;
      const counter = document.getElementById('counter-' + idx);
      if (counter) counter.textContent = `${carouselState[idx] + 1} / ${photos}`;
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text || '';
      return div.innerHTML;
    }

    // Only http(s) URLs may reach href/src — blocks javascript:/data: payloads
    // from scraped listing fields.
    function safeUrl(url) {
      try {
        const u = new URL(url, window.location.origin);
        if (u.protocol === 'http:' || u.protocol === 'https:') return u.href;
      } catch (e) { /* fall through */ }
      return '#';
    }

    // === Search defaults ===
    let searchDefaults = null;

    async function loadSearchDefaults() {
      try {
        const resp = await fetch('/api/search-defaults');
        searchDefaults = await resp.json();
        fillDefaultsForm(searchDefaults);
        applyDefaultsToForm();
      } catch (err) {
        console.error('loadSearchDefaults:', err);
      }
    }

    function fillDefaultsForm(sd) {
      if (!sd) return;
      // rooms checkboxes
      const rooms = Array.isArray(sd.rooms) ? sd.rooms.map(String) : [];
      [0,1,2,3,4].forEach(n => {
        const cb = document.getElementById('def-rooms-' + n);
        if (cb) cb.checked = rooms.includes(String(n));
      });
      // numeric/text fields
      const fields = ['price_min','price_max','district','floor_min','floor_max','area_min','area_max'];
      fields.forEach(f => {
        const el = document.getElementById('def-' + f);
        if (el) el.value = sd[f] != null ? sd[f] : '';
      });
    }

    function collectDefaultsFromForm() {
      const sd = {};
      const rooms = [];
      [0,1,2,3,4].forEach(n => {
        const cb = document.getElementById('def-rooms-' + n);
        if (cb && cb.checked) rooms.push(n);
      });
      sd.rooms = rooms;
      ['price_min','price_max','floor_min','floor_max','area_min','area_max'].forEach(f => {
        const el = document.getElementById('def-' + f);
        sd[f] = el && el.value !== '' ? el.value : null;
      });
      const districtEl = document.getElementById('def-district');
      sd.district = districtEl ? districtEl.value : '';
      return sd;
    }

    async function saveSearchDefaults() {
      const btn = document.getElementById('saveDefaultsBtn');
      btn.disabled = true; btn.textContent = 'Сохранение...';
      try {
        const sd = collectDefaultsFromForm();
        const resp = await fetch('/api/search-defaults', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(sd)
        });
        const data = await resp.json();
        if (data.ok) {
          searchDefaults = data.search_defaults;
          alert('Параметры по умолчанию сохранены');
        } else {
          alert('Ошибка: ' + (data.error || 'неизвестная'));
        }
      } catch (err) {
        alert('Ошибка: ' + err.message);
      } finally {
        btn.disabled = false; btn.textContent = 'Сохранить параметры';
      }
    }

    function applyDefaultsToForm() {
      if (!searchDefaults) return;
      // rooms
      const form = document.getElementById('searchForm');
      const rooms = Array.isArray(searchDefaults.rooms) ? searchDefaults.rooms.map(String) : [];
      form.querySelectorAll('input[name="rooms"]').forEach(cb => {
        cb.checked = rooms.includes(cb.value);
      });
      // price
      setFormValue(form, 'price_min', searchDefaults.price_min);
      setFormValue(form, 'price_max', searchDefaults.price_max);
      // district
      setFormValue(form, 'district', searchDefaults.district);
      // floor
      setFormValue(form, 'floor_min', searchDefaults.floor_min);
      setFormValue(form, 'floor_max', searchDefaults.floor_max);
      // area
      setFormValue(form, 'area_min', searchDefaults.area_min);
      setFormValue(form, 'area_max', searchDefaults.area_max);
      // Programmatic value changes don't fire 'input', so re-filter explicitly.
      applyClientFilters();
    }

    function setFormValue(form, name, val) {
      const el = form.querySelector(`[name="${name}"]`);
      if (el) el.value = (val != null && val !== '') ? val : '';
    }

    async function resetSearchDefaults() {
      if (!confirm('Сбросить параметры поиска к заводским (100-300т, студии и 1к)?')) return;
      const sd = {price_min: 100000, price_max: 300000, rooms: [0,1], district: '',
                  floor_min: null, floor_max: null, area_min: null, area_max: null};
      try {
        await fetch('/api/search-defaults', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(sd)
        });
        searchDefaults = sd;
        fillDefaultsForm(sd);
        applyDefaultsToForm();
      } catch (err) {
        alert('Ошибка: ' + err.message);
      }
    }

    // === Parser Analyzer ===
    let lastParserStats = [];

    const STATUS_LABELS = {
      ok: 'OK', empty: 'Пусто', http_error: 'HTTP ошибка',
      ssl_error: 'SSL ошибка', connection_error: 'Нет связи',
      timeout: 'Таймаут', error: 'Ошибка', pending: 'Ожидание',
    };
    const ERROR_STATUSES = new Set(['http_error','ssl_error','connection_error','timeout','error']);

    async function loadParserStats() {
      try {
        const resp = await fetch('/api/parser-status');
        const data = await resp.json();
        lastParserStats = data.parsers || [];
        renderParserStats(lastParserStats);
      } catch (err) {
        console.error('loadParserStats:', err);
      }
    }

    function renderParserStats(stats) {
      const grid = document.getElementById('analyzerGrid');
      const summary = document.getElementById('analyzerSummary');
      if (!stats || !stats.length) {
        grid.innerHTML = '<div class="empty-state">Запустите поиск — здесь появится детальная статистика по каждому парсеру.</div>';
        summary.innerHTML = '';
        return;
      }
      const ok = stats.filter(s => s.status === 'ok').length;
      const empty = stats.filter(s => s.status === 'empty').length;
      const errors = stats.filter(s => ERROR_STATUSES.has(s.status)).length;
      const totalResults = stats.reduce((sum, s) => sum + (s.results_count || 0), 0);
      const totalMs = stats.reduce((sum, s) => sum + (s.duration_ms || 0), 0);
      summary.innerHTML = `
        <div class="analyzer-summary-card"><div class="num" style="color:var(--success);">${ok}</div><div class="label">успешно</div></div>
        <div class="analyzer-summary-card"><div class="num" style="color:var(--text-dim);">${empty}</div><div class="label">пусто</div></div>
        <div class="analyzer-summary-card"><div class="num" style="color:var(--danger);">${errors}</div><div class="label">ошибок</div></div>
        <div class="analyzer-summary-card"><div class="num" style="color:var(--accent);">${totalResults}</div><div class="label">объявлений</div></div>
        <div class="analyzer-summary-card"><div class="num">${(totalMs/1000).toFixed(1)}с</div><div class="label">общее время</div></div>
      `;
      grid.innerHTML = stats.map(s => {
        const isErr = ERROR_STATUSES.has(s.status);
        const label = STATUS_LABELS[s.status] || s.status;
        const durStr = s.duration_ms ? (s.duration_ms >= 1000 ? (s.duration_ms/1000).toFixed(1)+'с' : Math.round(s.duration_ms)+'мс') : '—';
        const ts = s.timestamp ? s.timestamp.slice(11) : '';
        return `
          <div class="parser-card">
            <div class="parser-card-header">
              <div>
                <div class="parser-name">${escapeHtml(s.name)}</div>
                <div class="parser-url">${escapeHtml(s.base_url)}</div>
              </div>
              <span class="status-badge ${isErr ? 'error' : s.status === 'ok' ? 'ok' : 'empty'}">${label}</span>
            </div>
            <div class="parser-metrics">
              <span class="parser-metric">Результатов: <b>${s.results_count}</b></span>
              <span class="parser-metric">Страниц: <b>${s.pages_fetched}</b></span>
              <span class="parser-metric">Время: <b>${durStr}</b></span>
              ${s.http_status ? `<span class="parser-metric">HTTP: <b>${escapeHtml(s.http_status)}</b></span>` : ''}
              ${s.error_type ? `<span class="parser-metric">Тип: <b>${escapeHtml(s.error_type)}</b></span>` : ''}
            </div>
            ${isErr && s.error ? `<div class="parser-error">${escapeHtml(s.error)}</div>` : ''}
            ${ts ? `<div style="font-size:.68rem;color:var(--text-dim);margin-top:6px;">Время запуска: ${escapeHtml(ts)}</div>` : ''}
          </div>
        `;
      }).join('');
    }

    // === Export ===
    document.getElementById('exportTxt').addEventListener('click', () => exportFile('txt'));
    document.getElementById('exportPdfPortrait').addEventListener('click', () => exportPdf('portrait'));
    document.getElementById('exportPdfLandscape').addEventListener('click', () => exportPdf('landscape'));

    function exportBtnBusy(on) {
      for (const id of ['exportTxt', 'exportPdfPortrait', 'exportPdfLandscape']) {
        const b = document.getElementById(id);
        if (!b) continue;
        b.disabled = on;
        b.style.opacity = on ? 0.5 : '';
      }
    }

    async function exportFile(type) {
      exportBtnBusy(true);
      try {
        const resp = await fetch(`/api/export/${type}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({results: currentResults})
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        downloadBlob(blob, resp.headers.get('Content-Disposition'));
      } catch (e) {
        alert('Не удалось сформировать файл: ' + e.message);
      } finally {
        exportBtnBusy(false);
      }
    }
    async function exportPdf(orientation) {
      exportBtnBusy(true);
      try {
        const resp = await fetch('/api/export/pdf', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({results: currentResults, orientation})
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        downloadBlob(blob, resp.headers.get('Content-Disposition'));
      } catch (e) {
        alert('Не удалось сформировать PDF: ' + e.message);
      } finally {
        exportBtnBusy(false);
      }
    }
    function downloadBlob(blob, disposition) {
      let filename = 'export';
      if (disposition) {
        const m = disposition.match(/filename="?([^"]+)"?/);
        if (m) filename = m[1];
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    }

    // === Favorites export ===
    function favExportBtnBusy(on) {
      for (const id of ['favExportTxt', 'favExportPdfPortrait', 'favExportPdfLandscape']) {
        const b = document.getElementById(id);
        if (!b) continue;
        b.disabled = on;
        b.style.opacity = on ? 0.5 : '';
      }
    }

    async function fetchFavoritesData() {
      const resp = await fetch('/api/favorites');
      const data = await resp.json();
      return data.favorites || [];
    }

    document.getElementById('favExportTxt').addEventListener('click', async () => {
      favExportBtnBusy(true);
      try {
        const favs = await fetchFavoritesData();
        const resp = await fetch('/api/export/txt', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({results: favs})
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        downloadBlob(blob, resp.headers.get('Content-Disposition'));
      } catch (e) {
        alert('Не удалось сформировать TXT: ' + e.message);
      } finally {
        favExportBtnBusy(false);
      }
    });
    document.getElementById('favExportPdfPortrait').addEventListener('click', async () => {
      favExportBtnBusy(true);
      try {
        const favs = await fetchFavoritesData();
        const resp = await fetch('/api/export/pdf', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({results: favs, orientation: 'portrait'})
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        downloadBlob(blob, resp.headers.get('Content-Disposition'));
      } catch (e) {
        alert('Не удалось сформировать PDF: ' + e.message);
      } finally {
        favExportBtnBusy(false);
      }
    });
    document.getElementById('favExportPdfLandscape').addEventListener('click', async () => {
      favExportBtnBusy(true);
      try {
        const favs = await fetchFavoritesData();
        const resp = await fetch('/api/export/pdf', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({results: favs, orientation: 'landscape'})
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        downloadBlob(blob, resp.headers.get('Content-Disposition'));
      } catch (e) {
        alert('Не удалось сформировать PDF: ' + e.message);
      } finally {
        favExportBtnBusy(false);
      }
    });

    // === Favorites ===
    let savedKeys = new Set();
    let priceChartInstance = null;
    let lastCheckResults = {};  // listing_key → {changed, error, new_price, etc.}

    // Favorite keys come from the server (listing_key in the API response).
    // Never re-implement the hash on the client — it must match db.listing_key.

    function switchTab(tab) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      if (tab === 'search') {
        document.getElementById('tabSearch').classList.add('active');
        document.getElementById('searchTab').classList.add('active');
        setTimeout(() => map.invalidateSize(), 100);
      } else if (tab === 'favorites') {
        document.getElementById('tabFav').classList.add('active');
        document.getElementById('favTab').classList.add('active');
        loadFavorites();
      } else if (tab === 'analyzer') {
        document.getElementById('tabAnalyzer').classList.add('active');
        document.getElementById('analyzerTab').classList.add('active');
        const badge = document.getElementById('analyzerBadge');
        if (badge) badge.style.display = 'none';
        loadParserStats();
      } else {
        document.getElementById('tabSettings').classList.add('active');
        document.getElementById('settingsTab').classList.add('active');
        markActiveTheme();
        loadPhotoCacheSetting();
      }
    }

    function currentTheme() {
      return document.documentElement.getAttribute('data-theme') || 'midnight';
    }

    function applyThemeLocal(t) {
      document.documentElement.setAttribute('data-theme', t);
      try { localStorage.setItem('theme', t); } catch (e) {}
      markActiveTheme();
    }

    function markActiveTheme() {
      const cur = currentTheme();
      document.querySelectorAll('.theme-swatch').forEach(s => {
        s.classList.toggle('active', s.dataset.theme === cur);
      });
    }

    async function setTheme(t) {
      applyThemeLocal(t);
      try {
        await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({theme: t})
        });
      } catch (err) {
        console.error('setTheme:', err);
      }
    }

    async function setHideNoPhoto(v) {
      try {
        await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({hide_no_photo: v})
        });
      } catch (err) {
      }
    }
    async function setOlxPhonePageOnly(v) {
      try {
        await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({olx_phone_page_only: v})
        });
      } catch (err) {
      }
    }
    async function setOlxPhonePlaywright(v) {
      try {
        await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({olx_phone_playwright: v})
        });
      } catch (err) {
      }
    }

    // === Per-parser max_pages settings ===
    let parserMaxPages = {};
    const PARSER_LABELS = {
      'krisha.kz': 'krisha.kz', 'olx.kz': 'olx.kz', 'kn.kz': 'kn.kz',
      'etagi.com': 'etagi.com', 'kvartirka.kz': 'kvartirka.kz',
      'telegram': 'telegram'
    };

    async function loadParserMaxPages() {
      try {
        const resp = await fetch('/api/settings');
        const s = await resp.json();
        parserMaxPages = s.parser_max_pages || {};
        renderParserMaxPages();
      } catch (err) {
        console.error('loadParserMaxPages:', err);
      }
    }

    function renderParserMaxPages() {
      const grid = document.getElementById('parserMaxPagesGrid');
      if (!grid) return;
      grid.innerHTML = '';
      Object.keys(PARSER_LABELS).forEach(name => {
        const val = parserMaxPages[name] || 1;
        const div = document.createElement('div');
        div.className = 'form-group';
        div.innerHTML = `
          <label>${name}</label>
          <div style="display:flex;align-items:center;gap:6px;">
            <input type="number" min="1" max="30" value="${val}" id="pmp-${name}"
                   onchange="setParserMaxPages('${name}', this.value)" style="width:70px;">
            <span style="font-size:.75rem;color:var(--text-dim);">стр.</span>
          </div>`;
        grid.appendChild(div);
      });
    }

    async function setParserMaxPages(name, val) {
      const n = parseInt(val, 10);
      if (isNaN(n) || n < 1 || n > 30) {
        alert('Введите число от 1 до 30');
        loadParserMaxPages();
        return;
      }
      parserMaxPages[name] = n;
      try {
        await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({parser_max_pages: parserMaxPages})
        });
      } catch (err) {
        console.error('setParserMaxPages:', err);
      }
      // Server re-applies the filter to the cached results
      loadCachedResults();
    }

    // === Photo cache settings ===
    async function loadPhotoCacheSetting() {
      try {
        const resp = await fetch('/api/settings');
        const s = await resp.json();
        const input = document.getElementById('photoCacheMbInput');
        if (input) input.value = s.photo_cache_mb || 500;
        await updatePhotoCacheInfo();
      } catch (err) {
        console.error('loadPhotoCacheSetting:', err);
      }
    }

    async function updatePhotoCacheInfo() {
      try {
        const resp = await fetch('/api/photo-cache');
        const data = await resp.json();
        const el = document.getElementById('photoCacheInfo');
        if (el) {
          el.innerHTML = `Занято: <b>${data.size_mb} МБ</b> (${data.files} файлов) · Лимит: ${data.max_mb} МБ`;
        }
      } catch (err) {
        console.error('updatePhotoCacheInfo:', err);
      }
    }

    async function setPhotoCacheMb(val) {
      const n = parseInt(val, 10);
      if (isNaN(n) || n < 50 || n > 10000) {
        alert('Введите число от 50 до 10000');
        loadPhotoCacheSetting();
        return;
      }
      try {
        await fetch('/api/settings', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({photo_cache_mb: n})
        });
        await updatePhotoCacheInfo();
      } catch (err) {
        console.error('setPhotoCacheMb:', err);
      }
    }

    async function clearPhotoCache() {
      if (!confirm('Удалить все картинки из кэша? При следующем экспорте они скачаются заново.')) return;
      try {
        const resp = await fetch('/api/photo-cache/clear', {method: 'POST'});
        const data = await resp.json();
        await updatePhotoCacheInfo();
      } catch (err) {
        alert('Ошибка: ' + err.message);
      }
    }

    async function clearFavorites() {
      if (!confirm('Удалить ВСЁ избранное, историю цен и кэш результатов? Это действие необратимо.')) return;
      const btn = document.getElementById('clearFavBtn');
      btn.disabled = true; btn.textContent = 'Удаление...';
      try {
        const resp = await fetch('/api/favorites/clear', {method: 'POST'});
        const data = await resp.json();
        savedKeys.clear();
        updateFavCount();
        loadFavorites();
        currentResults = [];
        renderResults([], 0);
        document.getElementById('exportButtons').style.display = 'none';
        drawListingMarkers();
        alert('Удалено объявлений: ' + (data.removed || 0));
      } catch (err) {
        alert('Ошибка: ' + err.message);
      } finally {
        btn.disabled = false; btn.textContent = 'Удалить всё избранное';
      }
    }

    async function resetDatabase() {
      if (!confirm('Удалить ВСЮ базу данных (избранное + история цен + кэш результатов)? Файл БД будет пересоздан. Действие необратимо.')) return;
      const btn = document.getElementById('resetDbBtn');
      btn.disabled = true; btn.textContent = 'Удаление...';
      try {
        const resp = await fetch('/api/database/reset', {method: 'POST'});
        const data = await resp.json();
        savedKeys.clear();
        updateFavCount();
        loadFavorites();
        currentResults = [];
        renderResults([], 0);
        document.getElementById('exportButtons').style.display = 'none';
        drawListingMarkers();
        alert(data.message || 'База данных удалена');
      } catch (err) {
        alert('Ошибка: ' + err.message);
      } finally {
        btn.disabled = false; btn.textContent = 'Удалить всю базу';
      }
    }

    async function toggleFavorite(index) {
      const r = displayedResults[index];
      if (!r) return;
      const key = r.listing_key;
      if (!key) return;
      try {
        if (savedKeys.has(key)) {
          await fetch(`/api/favorites/${key}`, {method: 'DELETE'});
          savedKeys.delete(key);
        } else {
          await fetch('/api/favorites', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({...r, listing_key: key})
          });
          savedKeys.add(key);
        }
      } catch (err) {
        // Without this the rejected promise is unhandled and the star
        // silently stops working after a network error.
        alert('Ошибка избранного: ' + err.message);
        return;
      }
      renderResults(displayedResults);
      updateFavCount();
    }

    async function loadFavorites() {
      try {
        const resp = await fetch('/api/favorites');
        const data = await resp.json();
        savedKeys.clear();
        data.favorites.forEach(f => savedKeys.add(f.listing_key));
        renderFavorites(data.favorites);
        updateFavCount();
      } catch (err) {
        console.error('loadFavorites:', err);
      }
    }

    function updateFavCount() {
      document.getElementById('favCount').textContent = savedKeys.size;
    }

    function renderFavorites(favs) {
  const favPhoneCounts = buildPhoneCounts(favs);
      const list = document.getElementById('favoritesList');
      const header = document.querySelector('.favorites-header h2');
      header.textContent = `Избранные квартиры (${favs.length}) — оценки, комментарии и мониторинг цен`;
      const favExportBtns = document.getElementById('favExportButtons');
      if (favExportBtns) favExportBtns.style.display = favs.length ? 'flex' : 'none';
      if (!favs.length) {
        list.innerHTML = '<div class="empty-state">Нет сохранённых вариантов. Нажмите на звёздочку на карточке объявления.</div>';
        return;
      }
      list.innerHTML = favs.map(f => {
        // f.photo can be a "|"-joined gallery — take the first photo only.
        const firstPhoto = f.photo ? f.photo.split('|')[0] : '';
        const photo = firstPhoto ? `<img class="fav-card-img" src="${safeUrl(firstPhoto)}" onerror="this.style.display='none'">` : '';
        let changeBadge = '';
        if (f.price_change) {
          const c = f.price_change;
          const arrow = c.direction === 'up' ? '↑' : (c.direction === 'down' ? '↓' : '=');
          changeBadge = `<span class="price-change ${c.direction}">${arrow} ${c.diff > 0 ? '+' : ''}${c.diff.toLocaleString('ru-RU')} (${c.percent}%)</span>`;
        }
        // Check-price results: mark listings that are no longer available
        // (error from re-fetch — page gone, 404, listing not found).
        const check = lastCheckResults[f.listing_key];
        const isUnavailable = check && check.error;
        const unavailableBadge = isUnavailable
          ? `<span class="fav-badge-unavailable" title="${escapeHtml(check.error)}">недоступно</span>`
          : '';
        const statusBadge = f.check_status
          ? `<span class="fav-badge-status" title="${escapeHtml(f.check_status)}">${escapeHtml(f.check_status)}</span>`
          : '';
        const cardClass = isUnavailable ? 'fav-card unavailable' : 'fav-card';
        const stars = [1,2,3,4,5].map(n =>
          `<span class="star ${n <= f.rating ? 'active' : ''}" onclick="rateFavorite('${f.listing_key}', ${n})">★</span>`
        ).join('');
        return `
          <div class="${cardClass}" style="margin-bottom:12px;">
            ${photo}
            <div class="fav-card-body">
              <div class="fav-card-title">${escapeHtml(f.title)}${unavailableBadge}${statusBadge}</div>
              <div class="fav-card-price">${f.price_str}</div>
      <div style="font-size:.75rem;color:var(--text-dim);margin-top:2px;">${f.price && f.price_rub != null ? f.price_rub.toLocaleString('ru-RU', {maximumFractionDigits:0}) + ' руб · $' + (f.price_usd || 0).toFixed(0) : ''}</div>
              <div class="fav-card-meta">${escapeHtml(f.address || '—')} · ${f.source}</div>
              ${f.phone ? `<div class="listing-phone">Тел: <a href="tel:${f.phone.replace(/[^\d+]/g, '')}">${escapeHtml(f.phone)}</a>${phoneRepeatHtml(f, favPhoneCounts)}</div>` : ''}
              ${(f.date_published || f.date_updated) ? `<div class="date-line">${f.date_published ? `<span>Опубликовано: ${f.date_published}</span>` : ''}${f.date_updated ? `<span>Обновлено: ${f.date_updated}</span>` : ''}</div>` : ''}
              <div class="fav-info-row">
                <div class="stars" data-key="${f.listing_key}">${stars}</div>
                ${changeBadge}
              </div>
              <textarea class="comment-box" placeholder="Ваш комментарий..." onchange="saveComment('${f.listing_key}', this.value)">${escapeHtml(f.comment || '')}</textarea>
              <div class="fav-actions">
                <button class="btn-show-chart" onclick="showChart('${f.listing_key}')">График цен</button>
                <a class="listing-link" href="${safeUrl(f.url)}" target="_blank" rel="noopener">Открыть →</a>
                <button class="btn-del-fav" onclick="deleteFavorite('${f.listing_key}')">Удалить</button>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }

    async function rateFavorite(key, rating) {
      await fetch(`/api/favorites/${key}/rate`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({rating})
      });
      loadFavorites();
    }

    async function saveComment(key, comment) {
      await fetch(`/api/favorites/${key}/comment`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({comment})
      });
    }

    async function deleteFavorite(key) {
      await fetch(`/api/favorites/${key}`, {method: 'DELETE'});
      savedKeys.delete(key);
      loadFavorites();
      renderResults(displayedResults);
    }

    document.getElementById('checkPricesBtn').addEventListener('click', async function() {
      const btn = this;
      btn.disabled = true; btn.textContent = 'Проверка...';
      let failed = false;
      try {
        const resp = await fetch('/api/favorites/check-prices', {method: 'POST'});
        const data = await resp.json();
        // Store check results keyed by listing_key so renderFavorites can
        // mark unavailable listings (error = page gone / 404 / not found).
        lastCheckResults = {};
        (data.results || []).forEach(r => {
          if (r.listing_key) lastCheckResults[r.listing_key] = r;
        });
        // Results appear on the cards (price, unavailable badge, status line).
        // No separate alert window for the check results.
      } catch (err) {
        failed = true;
        btn.textContent = 'Ошибка: ' + err.message;
      } finally {
        btn.disabled = false;
        if (!failed) btn.textContent = 'Проверить цены';
        loadFavorites();
      }
    });

    let chartTitleEl = document.getElementById('chartTitle');
    let chartSummaryEl = document.getElementById('chartSummary');

    async function showChart(key) {
      try {
        const resp = await fetch(`/api/favorites/${key}/history`);
        const data = await resp.json();
        chartTitleEl.textContent = 'Динамика цен: ' + (data.title || '').slice(0, 50);
        const labels = data.history.map(h => h.checked_at.slice(5, 16));
        const prices = data.history.map(h => h.price);
        if (priceChartInstance) priceChartInstance.destroy();
        const ctx = document.getElementById('priceChart').getContext('2d');
        priceChartInstance = new Chart(ctx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: 'Цена (тг)',
              data: prices,
              borderColor: '#3b82f6',
              backgroundColor: 'rgba(59,130,246,.15)',
              fill: true, tension: 0.3,
              pointRadius: 5, pointBackgroundColor: '#8b5cf6',
            }]
          },
          options: {
            responsive: true,
            plugins: {
              legend: {display: false},
              tooltip: {callbacks: {
                label: ctx => ctx.parsed.y ? ctx.parsed.y.toLocaleString('ru-RU') + ' тг' : '—'
              }}
            },
            scales: {
              y: {
                ticks: {color: '#94a3b8', callback: v => v.toLocaleString('ru-RU') + ' тг'},
                grid: {color: 'rgba(71,85,105,.3)'}
              },
              x: {ticks: {color: '#94a3b8'}, grid: {color: 'rgba(71,85,105,.3)'}}
            }
          }
        });
        let summary = '';
        if (data.price_change) {
          const c = data.price_change;
          const arrow = c.direction === 'up' ? '↑' : (c.direction === 'down' ? '↓' : '=');
          summary = `Первая цена: ${c.old_price.toLocaleString('ru-RU')} тг → Текущая: ${c.new_price.toLocaleString('ru-RU')} тг. ` +
                    `Изменение: ${arrow} ${c.diff > 0 ? '+' : ''}${c.diff.toLocaleString('ru-RU')} (${c.percent}%)`;
        } else {
          summary = 'Недостаточно данных для анализа динамики.';
        }
        chartSummaryEl.textContent = summary;
        document.getElementById('chartModal').classList.add('active');
      } catch (err) {
        alert('Ошибка загрузки графика: ' + err.message);
      }
    }

    function closeChart() {
      document.getElementById('chartModal').classList.remove('active');
      if (priceChartInstance) { priceChartInstance.destroy(); priceChartInstance = null; }
    }

    // === Exchange rates ===
    let currentRates = null;
    let ratesTimer = null;

    async function loadRates() {
      try {
        const resp = await fetch('/api/rates');
        const data = await resp.json();
        currentRates = data;
        renderRates(data);
        // If results are already shown, re-render so cards pick up new rates
        if (currentResults.length) renderResults(currentResults);
      } catch (err) {
        console.error('loadRates:', err);
        document.getElementById('rateUpdated').textContent = 'ошибка загрузки';
      }
    }

    function renderRates(data) {
      document.getElementById('rateRub').textContent = (data.RUB || 0).toFixed(4);
      document.getElementById('rateUsd').textContent = (data.USD || 0).toFixed(5);
      let upd = data.updated || '';
      if (upd && upd.length > 16) upd = upd.slice(5, 16);
      document.getElementById('rateUpdated').textContent = upd ? 'обновлено: ' + upd : 'обновлено';
      // Live-update price spans in result cards without full re-render
      if (currentRates && currentResults.length) {
        const rub = data.RUB || 0;
        const usd = data.USD || 0;
        document.querySelectorAll('.listing-price .price-conv').forEach(el => {
          const price = parseFloat(el.dataset.price);
          if (price) {
            el.textContent = `· ${Math.round(price * rub).toLocaleString('ru-RU')} руб · $${Math.round(price * usd)}`;
          }
        });
      }
    }

    async function refreshRates() {
      const btn = document.getElementById('ratesRefreshBtn');
      btn.disabled = true;
      try {
        const resp = await fetch('/api/rates/refresh', {method: 'POST'});
        const data = await resp.json();
        currentRates = data;
        renderRates(data);
        // Re-render cards with fresh rates
        if (currentResults.length) renderResults(currentResults);
        loadFavorites();
      } catch (err) {
        console.error('refreshRates:', err);
      } finally {
        btn.disabled = false;
      }
    }

    // === Init ===
    markActiveTheme();
    // Sync settings from server (source of truth across restarts)
    fetch('/api/settings').then(r => r.json()).then(s => {
      if (s.theme) applyThemeLocal(s.theme);
      const cb = document.getElementById('hideNoPhoto');
      if (cb) cb.checked = !!s.hide_no_photo;
      const cbP1 = document.getElementById('olxPhonePageOnly');
      if (cbP1) cbP1.checked = !!s.olx_phone_page_only;
      const cbP2 = document.getElementById('olxPhonePlaywright');
      if (cbP2) cbP2.checked = !!s.olx_phone_playwright;
      parserMaxPages = s.parser_max_pages || {};
      renderParserMaxPages();
    }).catch(() => {});
    loadSearchDefaults();
    loadRates();
    // Auto-refresh rates every 10 minutes
    if (ratesTimer) clearInterval(ratesTimer);
    ratesTimer = setInterval(loadRates, 600000);
    loadDistricts().then(() => {
      loadHeatmap('all');
      // Load cached results after districts (needed for map markers)
      loadCachedResults();
    });
    loadFavorites();

    async function loadCachedResults() {
      try {
        const resp = await fetch('/api/results');
        const data = await resp.json();
        const res = data.results || [];
        currentResults = res;
        applyClientFilters();
      } catch (err) {
        console.error('loadCachedResults:', err);
      }
    }
