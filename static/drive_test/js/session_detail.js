(function () {
  var root = document.getElementById('dt-sd');
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var cfg = root.dataset;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function getJson(url) {
    return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }
  function qs(obj) {
    var p = new URLSearchParams();
    Object.keys(obj).forEach(function (k) { if (obj[k] !== '' && obj[k] != null) p.set(k, obj[k]); });
    return p.toString();
  }
  function num(v, dec, unit) {
    if (v === null || v === undefined) return '—';
    return Number(v).toFixed(dec) + (unit || '');
  }

  // ── processing banner ──
  if (cfg.pollUrl) {
    var stageEl = $('dt-sd-stage');
    var tick = function () {
      getJson(cfg.pollUrl).then(function (s) {
        if (s.file_status === 'COMPLETED' || s.file_status === 'FAILED') { window.location.reload(); return; }
        if (stageEl) stageEl.textContent = s.file_status.charAt(0) + s.file_status.slice(1).toLowerCase();
        setTimeout(tick, 3000);
      }).catch(function () { setTimeout(tick, 6000); });
    };
    setTimeout(tick, 3000);
  }

  if (cfg.hasData !== '1') return;

  // ── tabs ──
  var tabButtons = document.querySelectorAll('#dt-sd-tabs [data-tab]');
  function showTab(name) {
    var b = document.querySelector('#dt-sd-tabs [data-tab="' + name + '"]');
    if (b && window.bootstrap) window.bootstrap.Tab.getOrCreateInstance(b).show();
  }
  tabButtons.forEach(function (b) {
    b.addEventListener('shown.bs.tab', function () {
      history.replaceState(null, '', location.pathname + location.search + '#' + b.dataset.tab);
      onTab(b.dataset.tab);
    });
  });

  // ── KPIs ──
  function setKpi(name, value, sub) {
    ['data-kpi', 'data-kpi2'].forEach(function (attr) {
      var v = document.querySelector('[' + attr + '="' + name + '"]');
      var s = document.querySelector('[' + attr + '="' + name + '-sub"]');
      if (v) { v.textContent = value; v.classList.toggle('is-missing', value === '—'); }
      if (s) s.textContent = sub || '';
    });
  }
  var rssiChart = null;
  function renderKpis(d) {
    var sig = d.signal || {}, voice = d.voice || {}, mob = d.mobility || {}, net = d.network || {}, meas = d.measurements || {};
    var cov = sig.coverage || {};
    var NA = 'Insufficient data';
    setKpi('coverage', cov.coverage_percent != null ? num(cov.coverage_percent, 1, '%') : '—',
      cov.coverage_percent != null ? cov.covered_samples + ' of ' + cov.valid_samples + ' samples' : NA);
    setKpi('cssr', num(voice.cssr_percent, 1, '%'), voice.cssr_percent != null ? voice.attempted + ' attempts' : NA);
    setKpi('dcr', num(voice.dcr_percent, 1, '%'), voice.dcr_percent != null ? voice.connected + ' connected' : NA);
    setKpi('mos', num(voice.mean_mos, 2), voice.mean_mos != null ? voice.mos_sample_count + ' samples' : NA);
    setKpi('speed', mob.mean_speed_kmh != null ? num(mob.mean_speed_kmh, 1, ' km/h') : '—',
      mob.max_speed_kmh != null ? 'max ' + num(mob.max_speed_kmh, 1, ' km/h') : NA);
    setKpi('rssi', sig.mean_rssi != null ? num(sig.mean_rssi, 1, ' dBm') : '—',
      sig.mean_rssi != null ? (sig.rssi_sample_count != null ? sig.rssi_sample_count : meas.with_rssi) + ' samples' : NA);
    var calls = document.querySelector('[data-kpi="calls"]');
    if (calls) calls.textContent = voice.total_calls != null ? String(voice.total_calls) : '—';

    var detail = $('kpi-detail');
    detail.textContent = '';
    var techs = Object.keys(net.technology_breakdown || {}).map(function (k) { return k + ': ' + net.technology_breakdown[k]; }).join(' · ');
    [['Technology', techs || '—'],
     ['Unique Cells', net.unique_cells != null ? String(net.unique_cells) : '—'],
     ['RSSI Min / Max', sig.min_rssi != null ? num(sig.min_rssi, 1) + ' / ' + num(sig.max_rssi, 1) + ' dBm' : '—'],
     ['Distance', mob.total_distance_km != null ? num(mob.total_distance_km, 2, ' km') : '—'],
     ['Attempted / Connected / Dropped', voice.total_calls ? voice.attempted + ' / ' + voice.connected + ' / ' + voice.dropped : '—'],
     ['Mean Call Duration', num(voice.mean_call_duration_s, 1, ' s')]
    ].forEach(function (r) {
      var d1 = el('div'); d1.appendChild(el('dt', '', r[0])); d1.appendChild(el('dd', '', r[1])); detail.appendChild(d1);
    });

    var dist = sig.rssi_distribution || {};
    var keys = ['good', 'fair', 'poor', 'very_poor', 'no_signal'];
    var counts = keys.map(function (k) { return dist[k] ? dist[k].count : 0; });
    var has = counts.some(function (c) { return c > 0; });
    $('rssi-empty').hidden = has;
    $('rssi-chart').parentNode.hidden = !has;
    if (has && window.Chart) {
      if (rssiChart) rssiChart.destroy();
      rssiChart = new Chart($('rssi-chart').getContext('2d'), {
        type: 'bar',
        data: { labels: ['Good', 'Fair', 'Poor', 'Very Poor', 'No Signal'],
                datasets: [{ label: 'Measurements', data: counts,
                             backgroundColor: '#1a237e', borderWidth: 0 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
                   scales: { y: { beginAtZero: true, title: { display: true, text: 'Measurements' } }, x: { grid: { display: false } } } }
      });
    }
  }
  getJson(cfg.kpiUrl).then(renderKpis).catch(function () {
    ['coverage', 'cssr', 'dcr', 'mos', 'speed', 'rssi'].forEach(function (k) { setKpi(k, '—', 'Unavailable'); });
    var calls = document.querySelector('[data-kpi="calls"]'); if (calls) calls.textContent = '—';
    var e = $('kpi-error'); e.hidden = false; e.textContent = 'Unable to load KPI data.';
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // PROFESSIONAL MAP ENGINE
  // ═══════════════════════════════════════════════════════════════════════════
  var TILE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';
  var geoPromise = null, maps = {}, findingMarkers = {};
  function geo() { return geoPromise || (geoPromise = getJson(cfg.mapUrl)); }

  // Signal colour scales per metric
  var SCALES = {
    rsrp: [
      { min: -85,  max: Infinity, color: '#22c55e', label: '≥ -85 Excellent' },
      { min: -95,  max: -85,      color: '#eab308', label: '-95 to -85 Good' },
      { min: -105, max: -95,      color: '#f97316', label: '-105 to -95 Fair' },
      { min: -115, max: -105,     color: '#ef4444', label: '-115 to -105 Poor' },
      { min: -Infinity, max: -115, color: '#991b1b', label: '< -115 No Signal' }
    ],
    rsrq: [
      { min: -10,  max: Infinity, color: '#22c55e', label: '≥ -10 Excellent' },
      { min: -15,  max: -10,      color: '#eab308', label: '-15 to -10 Good' },
      { min: -20,  max: -15,      color: '#f97316', label: '-20 to -15 Fair' },
      { min: -Infinity, max: -20, color: '#ef4444', label: '< -20 Poor' }
    ],
    sinr: [
      { min: 20,  max: Infinity, color: '#22c55e', label: '≥ 20 Excellent' },
      { min: 10,  max: 20,       color: '#84cc16', label: '10 to 20 Good' },
      { min: 0,   max: 10,       color: '#eab308', label: '0 to 10 Fair' },
      { min: -5,  max: 0,        color: '#f97316', label: '-5 to 0 Poor' },
      { min: -Infinity, max: -5, color: '#ef4444', label: '< -5 Very Poor' }
    ],
    rssi: [
      { min: -75,  max: Infinity, color: '#22c55e', label: '≥ -75 Excellent' },
      { min: -85,  max: -75,      color: '#eab308', label: '-85 to -75 Good' },
      { min: -95,  max: -85,      color: '#f97316', label: '-95 to -85 Fair' },
      { min: -Infinity, max: -95, color: '#ef4444', label: '< -95 Poor' }
    ],
    tech: [
      { match: '5G', color: '#7c3aed', label: '5G NR' },
      { match: '4G', color: '#2563eb', label: '4G LTE' },
      { match: 'LTE', color: '#2563eb', label: '4G LTE' },
      { match: '3G', color: '#d97706', label: '3G UMTS' },
      { match: '2G', color: '#dc2626', label: '2G GSM' }
    ],
    speed: [
      { min: 80,  max: Infinity, color: '#22c55e', label: '≥ 80 km/h' },
      { min: 40,  max: 80,       color: '#eab308', label: '40–80 km/h' },
      { min: 10,  max: 40,       color: '#f97316', label: '10–40 km/h' },
      { min: 0,   max: 10,       color: '#60a5fa', label: '< 10 km/h' }
    ],
    dl_tp: [
      { min: 10000, max: Infinity, color: '#22c55e', label: '≥ 10 Mbps' },
      { min: 5000,  max: 10000,    color: '#84cc16', label: '5–10 Mbps' },
      { min: 1000,  max: 5000,     color: '#eab308', label: '1–5 Mbps' },
      { min: 0,     max: 1000,     color: '#ef4444', label: '< 1 Mbps' }
    ]
  };

  function colorFor(metric, p) {
    var scale = SCALES[metric];
    if (!scale) return '#6b7280';
    var val;
    if (metric === 'tech') {
      val = p.tech || '';
      for (var i = 0; i < scale.length; i++) {
        if (val.indexOf(scale[i].match) !== -1) return scale[i].color;
      }
      return '#6b7280';
    }
    val = p[metric];
    if (val === null || val === undefined) return '#6b7280';
    for (var j = 0; j < scale.length; j++) {
      if (val >= scale[j].min && val < scale[j].max) return scale[j].color;
    }
    return '#6b7280';
  }

  function updateLegend(metric) {
    var scale = SCALES[metric] || SCALES.rsrp;
    var title = $('legend-title');
    var bar = $('legend-bar');
    var labels = { rsrp: 'RSRP (dBm)', rsrq: 'RSRQ (dB)', sinr: 'SINR (dB)', rssi: 'RSSI (dBm)',
                   tech: 'Technology', speed: 'Speed (km/h)', dl_tp: 'DL Throughput' };
    title.textContent = labels[metric] || metric.toUpperCase();
    bar.innerHTML = '';
    scale.forEach(function (s) {
      var sp = el('span', 'dt-sd-legend-item');
      sp.style.background = s.color;
      sp.dataset.label = s.label;
      bar.appendChild(sp);
    });
  }

  function popupTable(title, rows) {
    var wrap = el('div', 'dt-sd-popup');
    wrap.appendChild(el('strong', '', title));
    var t = el('table');
    rows.forEach(function (r) {
      if (r[1] === null || r[1] === undefined || r[1] === '') return;
      var tr = el('tr'); tr.appendChild(el('td', '', r[0])); tr.appendChild(el('td', '', String(r[1]))); t.appendChild(tr);
    });
    wrap.appendChild(t);
    return wrap;
  }
  function unit(v, u) { return v !== null && v !== undefined ? v + ' ' + u : null; }

  // Overview map (simple, unchanged)
  function drawOverviewMap() {
    var host = $('dt-map-overview');
    geo().then(function (data) {
      var pts = data.features.filter(function (f) { return f.properties.ftype === 'measurement'; });
      if (!pts.length) { host.hidden = true; $('dt-map-overview-empty').hidden = false; return; }
      var map = L.map('dt-map-overview');
      L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(map);
      var bounds = [];
      var coords = pts.map(function (f) {
        var c = f.geometry.coordinates;
        bounds.push([c[1], c[0]]);
        return [c[1], c[0]];
      });
      L.polyline(coords, { color: '#1a237e', weight: 3, opacity: 0.7 }).addTo(map);
      pts.forEach(function (f) {
        var c = f.geometry.coordinates, p = f.properties;
        L.circleMarker([c[1], c[0]], { radius: 3, fillColor: p.colour, color: '#fff', weight: 0.5, fillOpacity: 0.9 }).addTo(map);
      });
      map.fitBounds(L.latLngBounds(bounds), { padding: [20, 20], maxZoom: 17 });
      maps['dt-map-overview'] = { map: map, bounds: bounds };
    }).catch(function () { host.hidden = true; $('dt-map-overview-empty').hidden = false; });
  }
  drawOverviewMap();

  // ── Professional Map (full tab) ──
  var fullMapBuilt = false;
  var mapState = {
    map: null, metric: 'rsrp', pts: [], towers: [], finds: [], handovers: [],
    measLayer: null, heatLayer: null, towerLayer: null, findLayer: null, hoLayer: null,
    replayPos: 100, playing: false, animFrame: null, routeSummary: null
  };

  function buildFullMap() {
    if (fullMapBuilt) return;
    fullMapBuilt = true;
    geo().then(function (data) {
      mapState.pts = data.features.filter(function (f) { return f.properties.ftype === 'measurement'; });
      mapState.towers = data.features.filter(function (f) { return f.properties.ftype === 'cell_tower'; });
      mapState.finds = data.features.filter(function (f) { return f.properties.ftype === 'finding'; });
      mapState.handovers = data.features.filter(function (f) { return f.properties.ftype === 'handover'; });
      mapState.routeSummary = data.route_summary || {};

      if (!mapState.pts.length) {
        $('dt-map-full').hidden = true;
        $('dt-map-full-empty').hidden = false;
        return;
      }

      var map = L.map('dt-map-full', { zoomControl: true });
      L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(map);
      mapState.map = map;

      // Stats
      var statPts = $('map-stat-pts');
      var statCells = $('map-stat-cells');
      if (statPts) statPts.textContent = mapState.pts.length.toLocaleString() + ' points';
      if (statCells) statCells.textContent = mapState.towers.length + ' cells';

      // Fit bounds
      var bounds = mapState.pts.map(function (f) {
        var c = f.geometry.coordinates;
        return [c[1], c[0]];
      });
      map.fitBounds(L.latLngBounds(bounds), { padding: [30, 30], maxZoom: 17 });

      // Set slider max
      $('replay-slider').max = mapState.pts.length;
      $('replay-slider').value = mapState.pts.length;

      drawMeasurements();
      drawTowers();
      drawFindings();
      drawHandovers();
      updateLegend(mapState.metric);
      maps['dt-map-full'] = { map: map, bounds: bounds };

      // Build signal analysis charts from route_summary
      buildSignalCharts(mapState.routeSummary, mapState.pts);
    }).catch(function () {
      $('dt-map-full').hidden = true;
      $('dt-map-full-empty').hidden = false;
    });
  }

  function drawMeasurements() {
    var map = mapState.map;
    if (mapState.measLayer) { map.removeLayer(mapState.measLayer); mapState.measLayer = null; }
    if (mapState.heatLayer) { map.removeLayer(mapState.heatLayer); mapState.heatLayer = null; }

    var metric = mapState.metric;
    var pts = mapState.pts;
    var limit = mapState.replayPos;
    var visible = (limit >= pts.length) ? pts : pts.slice(0, limit);

    // Signal-colored route line (polyline with per-segment color)
    mapState.measLayer = L.layerGroup().addTo(map);

    // Draw colored segments
    for (var i = 1; i < visible.length; i++) {
      var prev = visible[i - 1], curr = visible[i];
      var c1 = prev.geometry.coordinates, c2 = curr.geometry.coordinates;
      var col = colorFor(metric, curr.properties);
      L.polyline([[c1[1], c1[0]], [c2[1], c2[0]]], {
        color: col, weight: 4, opacity: 0.85
      }).addTo(mapState.measLayer);
    }

    // Measurement dots
    visible.forEach(function (f) {
      var c = f.geometry.coordinates, p = f.properties;
      var col = colorFor(metric, p);
      L.circleMarker([c[1], c[0]], {
        radius: 4, fillColor: col, color: '#fff', weight: 0.5, fillOpacity: 0.9
      }).bindPopup(function () {
        return popupTable('Measurement #' + p.seq, [
          ['Time', p.ts], ['Technology', p.tech],
          ['RSRP', unit(p.rsrp, 'dBm')], ['RSRQ', unit(p.rsrq, 'dB')],
          ['SINR', unit(p.sinr, 'dB')], ['RSSI', unit(p.rssi, 'dBm')],
          ['DL Throughput', unit(p.dl_tp, 'kbps')], ['UL Throughput', unit(p.ul_tp, 'kbps')],
          ['Cell', p.cell_id || 'Unmatched'], ['Site', p.site_name],
          ['Speed', unit(p.speed, 'km/h')]
        ]);
      }).addTo(mapState.measLayer);
    });

    // Current position marker for replay
    if (limit < pts.length && visible.length > 0) {
      var last = visible[visible.length - 1];
      var lc = last.geometry.coordinates;
      L.circleMarker([lc[1], lc[0]], {
        radius: 8, fillColor: '#2563eb', color: '#fff', weight: 3, fillOpacity: 1
      }).addTo(mapState.measLayer);
    }

    // Heatmap overlay
    if ($('map-heatmap').checked && visible.length > 0) {
      var heatData = [];
      visible.forEach(function (f) {
        var p = f.properties, c = f.geometry.coordinates;
        var val = p[metric];
        if (val === null || val === undefined) return;
        var intensity;
        if (metric === 'rsrp') intensity = Math.max(0, (val + 130) / 60);
        else if (metric === 'rsrq') intensity = Math.max(0, (val + 25) / 20);
        else if (metric === 'sinr') intensity = Math.max(0, (val + 10) / 40);
        else if (metric === 'rssi') intensity = Math.max(0, (val + 110) / 50);
        else intensity = 0.5;
        heatData.push([c[1], c[0], Math.min(1, intensity)]);
      });
      if (heatData.length > 0) {
        mapState.heatLayer = L.heatLayer(heatData, {
          radius: 20, blur: 15, maxZoom: 17, max: 1.0,
          gradient: { 0.0: '#991b1b', 0.25: '#ef4444', 0.5: '#f97316', 0.75: '#eab308', 1.0: '#22c55e' }
        }).addTo(map);
      }
    }
  }

  function drawTowers() {
    var map = mapState.map;
    if (mapState.towerLayer) map.removeLayer(mapState.towerLayer);
    if (!$('map-towers').checked) { mapState.towerLayer = null; return; }
    mapState.towerLayer = L.layerGroup().addTo(map);
    mapState.towers.forEach(function (f) {
      var c = f.geometry.coordinates, p = f.properties;
      var towerIcon = L.divIcon({
        className: 'dt-tower-icon',
        html: '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M12 2L8 10h8L12 2zM12 10v12M8 22h8" stroke="#0f766e" stroke-width="2" fill="#d1fae5"/></svg>',
        iconSize: [24, 24], iconAnchor: [12, 24]
      });
      L.marker([c[1], c[0]], { icon: towerIcon })
        .bindPopup(popupTable('Cell Tower', [
          ['Cell ID', p.cell_id], ['Technology', p.technology], ['Site', p.site_name],
          ['Azimuth', p.azimuth != null ? p.azimuth + '°' : null]
        ])).addTo(mapState.towerLayer);
    });
  }

  function drawFindings() {
    var map = mapState.map;
    if (mapState.findLayer) map.removeLayer(mapState.findLayer);
    findingMarkers = {};
    if (!$('map-findings').checked) { mapState.findLayer = null; return; }
    mapState.findLayer = L.layerGroup().addTo(map);
    mapState.finds.forEach(function (f) {
      var c = f.geometry.coordinates, p = f.properties;
      var sevColor = { CRITICAL: '#991b1b', HIGH: '#dc2626', MEDIUM: '#d97706', LOW: '#2563eb', INFO: '#6b7280' };
      var m = L.circleMarker([c[1], c[0]], {
        radius: 8, fillColor: sevColor[p.severity] || '#dc2626', color: '#fff', weight: 2, fillOpacity: 0.95
      }).bindPopup(popupTable('Finding', [
        ['Severity', p.severity], ['Category', p.category],
        ['Description', p.description], ['Status', p.resolved ? 'Resolved' : 'Open']
      ])).addTo(mapState.findLayer);
      findingMarkers[p.id] = m;
    });
  }

  function drawHandovers() {
    var map = mapState.map;
    if (mapState.hoLayer) map.removeLayer(mapState.hoLayer);
    if (!$('map-handovers').checked) { mapState.hoLayer = null; return; }
    mapState.hoLayer = L.layerGroup().addTo(map);
    mapState.handovers.forEach(function (f) {
      var c = f.geometry.coordinates, p = f.properties;
      L.circleMarker([c[1], c[0]], {
        radius: 6, fillColor: '#7c3aed', color: '#fff', weight: 2, fillOpacity: 0.9
      }).bindPopup(popupTable('Handover', [
        ['Time', p.ts], ['Target Cell', p.to_cell]
      ])).addTo(mapState.hoLayer);
    });
  }

  // Map toolbar events
  if ($('map-metric')) {
    $('map-metric').addEventListener('change', function () {
      mapState.metric = this.value;
      updateLegend(this.value);
      if (mapState.map) drawMeasurements();
    });
  }
  ['map-heatmap', 'map-towers', 'map-findings', 'map-handovers'].forEach(function (id) {
    var cb = $(id);
    if (cb) cb.addEventListener('change', function () {
      if (!mapState.map) return;
      if (id === 'map-heatmap') drawMeasurements();
      else if (id === 'map-towers') drawTowers();
      else if (id === 'map-findings') drawFindings();
      else if (id === 'map-handovers') drawHandovers();
    });
  });

  // Route replay
  var slider = $('replay-slider');
  var replayLabel = $('replay-label');
  var replayBtn = $('replay-btn');
  var replayIcon = $('replay-icon');

  if (slider) {
    slider.addEventListener('input', function () {
      var val = parseInt(this.value);
      mapState.replayPos = val;
      if (mapState.pts.length > 0) {
        if (val >= mapState.pts.length) {
          replayLabel.textContent = 'All (' + mapState.pts.length + ')';
        } else {
          var p = mapState.pts[Math.max(0, val - 1)];
          replayLabel.textContent = p ? p.properties.ts.split(' ').slice(3).join(' ') || (val + '/' + mapState.pts.length) : '';
        }
      }
      if (mapState.map) drawMeasurements();
    });
  }

  if (replayBtn) {
    replayBtn.addEventListener('click', function () {
      if (mapState.playing) {
        mapState.playing = false;
        replayBtn.classList.remove('is-playing');
        replayIcon.setAttribute('points', '2,0 14,7 2,14');
        if (mapState.animFrame) cancelAnimationFrame(mapState.animFrame);
        return;
      }
      mapState.playing = true;
      replayBtn.classList.add('is-playing');
      replayIcon.setAttribute('points', '2,0 5,0 5,14 2,14');

      if (mapState.replayPos >= mapState.pts.length) {
        mapState.replayPos = 0;
        slider.value = 0;
      }

      var speed = Math.max(1, Math.floor(mapState.pts.length / 300));
      function step() {
        if (!mapState.playing) return;
        mapState.replayPos = Math.min(mapState.replayPos + speed, mapState.pts.length);
        slider.value = mapState.replayPos;
        slider.dispatchEvent(new Event('input'));
        if (mapState.replayPos >= mapState.pts.length) {
          mapState.playing = false;
          replayBtn.classList.remove('is-playing');
          replayIcon.setAttribute('points', '2,0 14,7 2,14');
          return;
        }
        mapState.animFrame = requestAnimationFrame(step);
      }
      mapState.animFrame = requestAnimationFrame(step);
    });
  }

  if ($('replay-reset')) {
    $('replay-reset').addEventListener('click', function () {
      mapState.playing = false;
      replayBtn.classList.remove('is-playing');
      replayIcon.setAttribute('points', '2,0 14,7 2,14');
      mapState.replayPos = mapState.pts.length;
      slider.value = mapState.pts.length;
      slider.dispatchEvent(new Event('input'));
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // SIGNAL ANALYSIS CHARTS (CDF, Histogram, Scatter, Timeline, Handover, Stats)
  // ═══════════════════════════════════════════════════════════════════════════
  var signalChartsBuilt = false;
  var chartInstances = {};

  function buildSignalCharts(summary, pts) {
    if (signalChartsBuilt) return;
    signalChartsBuilt = true;
    if (!window.Chart) return;

    // ── CDF Chart builder ──
    function buildCdf(canvasId, emptyId, data, label, unit, thresholds) {
      if (!data || !data.values || data.values.length < 2) {
        $(canvasId).parentNode.hidden = true;
        $(emptyId).hidden = false;
        return;
      }
      var vals = data.values;
      var n = vals.length;
      var step = Math.max(1, Math.floor(n / 500));
      var labels = [], cdfData = [];
      for (var i = 0; i < n; i += step) {
        labels.push(vals[i]);
        cdfData.push(((i + 1) / n * 100).toFixed(1));
      }
      if (labels[labels.length - 1] !== vals[n - 1]) {
        labels.push(vals[n - 1]);
        cdfData.push('100.0');
      }

      var datasets = [{
        label: label + ' CDF',
        data: cdfData,
        borderColor: '#2563eb',
        backgroundColor: 'rgba(37, 99, 235, 0.1)',
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.3
      }];

      if (thresholds) {
        thresholds.forEach(function (th) {
          var idx = labels.findIndex(function (v) { return v >= th.value; });
          if (idx === -1) return;
          datasets.push({
            label: th.label,
            data: labels.map(function () { return cdfData[idx]; }),
            borderColor: th.color,
            borderDash: [5, 5],
            borderWidth: 1,
            pointRadius: 0,
            fill: false
          });
        });
      }

      chartInstances[canvasId] = new Chart($(canvasId).getContext('2d'), {
        type: 'line',
        data: { labels: labels, datasets: datasets },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { display: true, position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } },
            tooltip: {
              callbacks: {
                title: function (ctx) { return ctx[0].label + ' ' + unit; },
                label: function (ctx) { return ctx.dataset.label + ': ' + ctx.parsed.y + '%'; }
              }
            }
          },
          scales: {
            x: { title: { display: true, text: label + ' (' + unit + ')' }, ticks: { maxTicksLimit: 12, font: { size: 10 } } },
            y: { title: { display: true, text: 'CDF (%)' }, min: 0, max: 100, ticks: { font: { size: 10 } } }
          }
        }
      });
    }

    buildCdf('cdf-rsrp', 'cdf-rsrp-empty', summary.rsrp, 'RSRP', 'dBm', [
      { value: -85, label: 'Good (-85)', color: '#22c55e' },
      { value: -105, label: 'Fair (-105)', color: '#f97316' },
      { value: -115, label: 'Poor (-115)', color: '#ef4444' }
    ]);
    buildCdf('cdf-rsrq', 'cdf-rsrq-empty', summary.rsrq, 'RSRQ', 'dB', [
      { value: -10, label: 'Good (-10)', color: '#22c55e' },
      { value: -15, label: 'Fair (-15)', color: '#f97316' }
    ]);
    buildCdf('cdf-sinr', 'cdf-sinr-empty', summary.sinr, 'SINR', 'dB', [
      { value: 10, label: 'Good (10)', color: '#22c55e' },
      { value: 0, label: 'Fair (0)', color: '#f97316' }
    ]);
    buildCdf('cdf-rssi', 'cdf-rssi-empty', summary.rssi, 'RSSI', 'dBm', [
      { value: -75, label: 'Good (-75)', color: '#22c55e' },
      { value: -85, label: 'Fair (-85)', color: '#f97316' },
      { value: -95, label: 'Poor (-95)', color: '#ef4444' }
    ]);

    // ── Generic Histogram builder ──
    function buildHistogram(canvasId, emptyId, data, label, unit, bins) {
      if (!data || !data.values || data.values.length === 0) {
        $(canvasId).parentNode.hidden = true;
        $(emptyId).hidden = false;
        return;
      }
      var binCounts = bins.map(function () { return 0; });
      data.values.forEach(function (v) {
        for (var i = 0; i < bins.length; i++) {
          if (v >= bins[i].min && v < bins[i].max) { binCounts[i]++; break; }
        }
      });
      chartInstances[canvasId] = new Chart($(canvasId).getContext('2d'), {
        type: 'bar',
        data: {
          labels: bins.map(function (b) { return b.label; }),
          datasets: [{
            label: 'Samples',
            data: binCounts,
            backgroundColor: bins.map(function (b) { return b.color; }),
            borderWidth: 0
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { title: { display: true, text: label + ' (' + unit + ')' }, ticks: { font: { size: 9 }, maxRotation: 45 } },
            y: { title: { display: true, text: 'Samples' }, beginAtZero: true, ticks: { font: { size: 10 } } }
          }
        }
      });
    }

    buildHistogram('hist-rsrp', 'hist-rsrp-empty', summary.rsrp, 'RSRP', 'dBm', [
      { min: -Infinity, max: -120, label: '< -120', color: '#991b1b' },
      { min: -120, max: -115, label: '-120…-115', color: '#dc2626' },
      { min: -115, max: -110, label: '-115…-110', color: '#ef4444' },
      { min: -110, max: -105, label: '-110…-105', color: '#f97316' },
      { min: -105, max: -100, label: '-105…-100', color: '#fb923c' },
      { min: -100, max: -95, label: '-100…-95', color: '#eab308' },
      { min: -95, max: -90, label: '-95…-90', color: '#a3e635' },
      { min: -90, max: -85, label: '-90…-85', color: '#84cc16' },
      { min: -85, max: -80, label: '-85…-80', color: '#22c55e' },
      { min: -80, max: Infinity, label: '≥ -80', color: '#059669' }
    ]);

    buildHistogram('hist-rsrq', 'hist-rsrq-empty', summary.rsrq, 'RSRQ', 'dB', [
      { min: -Infinity, max: -20, label: '< -20', color: '#991b1b' },
      { min: -20, max: -17, label: '-20…-17', color: '#dc2626' },
      { min: -17, max: -15, label: '-17…-15', color: '#ef4444' },
      { min: -15, max: -13, label: '-15…-13', color: '#f97316' },
      { min: -13, max: -10, label: '-13…-10', color: '#eab308' },
      { min: -10, max: -7, label: '-10…-7', color: '#84cc16' },
      { min: -7, max: -3, label: '-7…-3', color: '#22c55e' },
      { min: -3, max: Infinity, label: '≥ -3', color: '#059669' }
    ]);

    buildHistogram('hist-sinr', 'hist-sinr-empty', summary.sinr, 'SINR', 'dB', [
      { min: -Infinity, max: -5, label: '< -5', color: '#991b1b' },
      { min: -5, max: 0, label: '-5…0', color: '#dc2626' },
      { min: 0, max: 5, label: '0…5', color: '#f97316' },
      { min: 5, max: 10, label: '5…10', color: '#eab308' },
      { min: 10, max: 15, label: '10…15', color: '#84cc16' },
      { min: 15, max: 20, label: '15…20', color: '#22c55e' },
      { min: 20, max: 30, label: '20…30', color: '#059669' },
      { min: 30, max: Infinity, label: '≥ 30', color: '#047857' }
    ]);

    buildHistogram('hist-rssi', 'hist-rssi-empty', summary.rssi, 'RSSI', 'dBm', [
      { min: -Infinity, max: -105, label: '< -105', color: '#991b1b' },
      { min: -105, max: -95, label: '-105…-95', color: '#dc2626' },
      { min: -95, max: -85, label: '-95…-85', color: '#f97316' },
      { min: -85, max: -75, label: '-85…-75', color: '#eab308' },
      { min: -75, max: -65, label: '-75…-65', color: '#84cc16' },
      { min: -65, max: -55, label: '-65…-55', color: '#22c55e' },
      { min: -55, max: Infinity, label: '≥ -55', color: '#059669' }
    ]);

    // ── Scatter plots ──
    function buildScatter(canvasId, emptyId, xKey, yKey, xLabel, xUnit, yLabel, yUnit) {
      var data = [];
      var techColors = { '5G': '#7c3aed', '4G': '#2563eb', 'LTE': '#2563eb', '3G': '#d97706', '2G': '#dc2626' };
      pts.forEach(function (f) {
        var p = f.properties;
        if (p[xKey] !== null && p[xKey] !== undefined && p[yKey] !== null && p[yKey] !== undefined) {
          data.push({ x: p[xKey], y: p[yKey], tech: p.tech || 'Unknown' });
        }
      });
      if (data.length < 5) {
        $(canvasId).parentNode.hidden = true;
        $(emptyId).hidden = false;
        return;
      }
      // Group by technology for color coding
      var techGroups = {};
      data.forEach(function (d) {
        var t = d.tech;
        if (!techGroups[t]) techGroups[t] = [];
        // Downsample per group to max 500 points
        if (techGroups[t].length < 500) techGroups[t].push({ x: d.x, y: d.y });
      });
      var datasets = Object.keys(techGroups).map(function (t) {
        return {
          label: t,
          data: techGroups[t],
          backgroundColor: (techColors[t] || '#6b7280') + '80',
          borderColor: techColors[t] || '#6b7280',
          borderWidth: 0.5,
          pointRadius: 3,
          pointHoverRadius: 5
        };
      });
      chartInstances[canvasId] = new Chart($(canvasId).getContext('2d'), {
        type: 'scatter',
        data: { datasets: datasets },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } },
            tooltip: {
              callbacks: {
                label: function (ctx) { return ctx.dataset.label + ': ' + ctx.parsed.x + ' ' + xUnit + ', ' + ctx.parsed.y + ' ' + yUnit; }
              }
            }
          },
          scales: {
            x: { title: { display: true, text: xLabel + ' (' + xUnit + ')' }, ticks: { font: { size: 10 } } },
            y: { title: { display: true, text: yLabel + ' (' + yUnit + ')' }, ticks: { font: { size: 10 } }, beginAtZero: true }
          }
        }
      });
    }

    buildScatter('scatter-rsrp-tp', 'scatter-rsrp-tp-empty', 'rsrp', 'dl_tp', 'RSRP', 'dBm', 'DL Throughput', 'kbps');
    buildScatter('scatter-sinr-tp', 'scatter-sinr-tp-empty', 'sinr', 'dl_tp', 'SINR', 'dB', 'DL Throughput', 'kbps');

    // ── Signal along route (time series) ──
    if (pts.length > 0) {
      var step2 = Math.max(1, Math.floor(pts.length / 600));
      var tlLabels = [], tlRsrp = [], tlRsrq = [], tlSinr = [];
      for (var k = 0; k < pts.length; k += step2) {
        var pp = pts[k].properties;
        tlLabels.push(pp.ts ? pp.ts.split(' ').slice(3).join(' ') : String(k));
        tlRsrp.push(pp.rsrp);
        tlRsrq.push(pp.rsrq);
        tlSinr.push(pp.sinr);
      }
      chartInstances['signal-timeline'] = new Chart($('signal-timeline').getContext('2d'), {
        type: 'line',
        data: {
          labels: tlLabels,
          datasets: [
            { label: 'RSRP (dBm)', data: tlRsrp, borderColor: '#2563eb', borderWidth: 1.5, pointRadius: 0, tension: 0.2, yAxisID: 'y' },
            { label: 'RSRQ (dB)', data: tlRsrq, borderColor: '#d97706', borderWidth: 1.5, pointRadius: 0, tension: 0.2, yAxisID: 'y' },
            { label: 'SINR (dB)', data: tlSinr, borderColor: '#059669', borderWidth: 1.5, pointRadius: 0, tension: 0.2, yAxisID: 'y2' }
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } },
          scales: {
            x: { title: { display: true, text: 'Time' }, ticks: { maxTicksLimit: 15, font: { size: 9 } } },
            y: { type: 'linear', position: 'left', title: { display: true, text: 'RSRP / RSRQ (dBm/dB)' }, ticks: { font: { size: 10 } } },
            y2: { type: 'linear', position: 'right', title: { display: true, text: 'SINR (dB)' }, grid: { drawOnChartArea: false }, ticks: { font: { size: 10 } } }
          }
        }
      });
    } else {
      $('signal-timeline').parentNode.hidden = true;
      $('signal-timeline-empty').hidden = false;
    }

    // ── Technology switch timeline ──
    buildTechTimeline(pts);

    // ── Handover sequence diagram ──
    buildHandoverDiagram(mapState.handovers);

    // ── Signal statistics table ──
    buildStatsTable(summary);
  }

  // ── Technology switch timeline chart ──
  function buildTechTimeline(pts) {
    var canvasId = 'tech-timeline', emptyId = 'tech-timeline-empty';
    if (pts.length === 0) { $(canvasId).parentNode.hidden = true; $(emptyId).hidden = false; return; }

    var techMap = { '2G': 1, 'GSM': 1, '3G': 2, 'UMTS': 2, 'WCDMA': 2, '4G': 3, 'LTE': 3, '5G': 4, 'NR': 4 };
    var techLabels = { 1: '2G GSM', 2: '3G UMTS', 3: '4G LTE', 4: '5G NR' };
    var techColors = { 1: '#dc2626', 2: '#d97706', 3: '#2563eb', 4: '#7c3aed' };

    var step = Math.max(1, Math.floor(pts.length / 800));
    var labels = [], data = [], bgColors = [], segments = [];
    var prevLevel = null, segStart = 0;

    for (var i = 0; i < pts.length; i += step) {
      var p = pts[i].properties;
      var tech = p.tech || '';
      var level = techMap[tech] || 0;
      labels.push(p.ts ? p.ts.split(' ').slice(3).join(' ') : String(i));
      data.push(level || null);
      bgColors.push(techColors[level] || '#9ca3af');

      if (level !== prevLevel && prevLevel !== null && i > 0) {
        segments.push({ from: segStart, to: labels.length - 1, level: prevLevel });
        segStart = labels.length - 1;
      }
      prevLevel = level;
    }
    if (prevLevel !== null) segments.push({ from: segStart, to: labels.length - 1, level: prevLevel });

    chartInstances[canvasId] = new Chart($(canvasId).getContext('2d'), {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'Technology',
          data: data,
          backgroundColor: bgColors,
          borderWidth: 0,
          barPercentage: 1.0,
          categoryPercentage: 1.0
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var lv = ctx.parsed.y;
                return techLabels[lv] || 'Unknown';
              }
            }
          }
        },
        scales: {
          x: {
            title: { display: true, text: 'Time' },
            ticks: { maxTicksLimit: 15, font: { size: 9 } },
            grid: { display: false }
          },
          y: {
            title: { display: true, text: 'Technology' },
            min: 0.5, max: 4.5,
            ticks: {
              stepSize: 1,
              font: { size: 10 },
              callback: function (v) { return techLabels[v] || ''; }
            }
          }
        }
      }
    });
  }

  // ── Handover sequence diagram ──
  function buildHandoverDiagram(handovers) {
    var canvasId = 'ho-diagram', emptyId = 'ho-diagram-empty';
    if (!handovers || handovers.length === 0) {
      $(canvasId).parentNode.hidden = true;
      $(emptyId).hidden = false;
      return;
    }

    var ho = handovers.map(function (f) { return f.properties; });
    var maxShow = 60;
    if (ho.length > maxShow) ho = ho.slice(0, maxShow);

    var canvas = $(canvasId);
    var minWidth = ho.length * 100 + 60;
    if (minWidth > canvas.parentNode.clientWidth) {
      canvas.style.width = minWidth + 'px';
      canvas.width = minWidth;
    }

    var labels = ho.map(function (h, i) { return h.ts || '#' + (i + 1); });
    var cells = [];
    var cellIndex = {};
    ho.forEach(function (h) {
      if (h.from_cell && !cellIndex.hasOwnProperty(h.from_cell)) { cellIndex[h.from_cell] = cells.length; cells.push(h.from_cell); }
      if (h.to_cell && !cellIndex.hasOwnProperty(h.to_cell)) { cellIndex[h.to_cell] = cells.length; cells.push(h.to_cell); }
    });

    var fromData = ho.map(function (h) { return cellIndex.hasOwnProperty(h.from_cell) ? cellIndex[h.from_cell] : null; });
    var toData = ho.map(function (h) { return cellIndex.hasOwnProperty(h.to_cell) ? cellIndex[h.to_cell] : null; });

    chartInstances[canvasId] = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Source Cell',
            data: fromData,
            borderColor: '#6366f1',
            backgroundColor: '#6366f180',
            pointRadius: 5,
            pointStyle: 'circle',
            borderWidth: 1.5,
            tension: 0,
            fill: false,
            segment: { borderDash: [4, 4] }
          },
          {
            label: 'Target Cell',
            data: toData,
            borderColor: '#059669',
            backgroundColor: '#05966980',
            pointRadius: 6,
            pointStyle: 'triangle',
            borderWidth: 2,
            tension: 0,
            fill: false
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var cellName = cells[ctx.parsed.y] || '?';
                return ctx.dataset.label + ': ' + cellName;
              },
              title: function (ctx) { return 'Handover @ ' + ctx[0].label; }
            }
          }
        },
        scales: {
          x: {
            title: { display: true, text: 'Time' },
            ticks: { maxTicksLimit: 20, font: { size: 9 }, maxRotation: 45 }
          },
          y: {
            title: { display: true, text: 'Cell' },
            min: -0.5,
            max: Math.max(cells.length - 0.5, 0.5),
            ticks: {
              stepSize: 1,
              font: { size: 9 },
              callback: function (v) {
                var idx = Math.round(v);
                if (idx >= 0 && idx < cells.length) {
                  var c = cells[idx];
                  return c.length > 12 ? c.substring(0, 12) + '…' : c;
                }
                return '';
              }
            }
          }
        }
      }
    });
  }

  function buildStatsTable(summary) {
    var container = $('signal-stats-table');
    if (!container) return;
    var metrics = [
      { key: 'rsrp', label: 'RSRP', unit: 'dBm', goodMin: -85, warnMin: -105 },
      { key: 'rsrq', label: 'RSRQ', unit: 'dB', goodMin: -10, warnMin: -15 },
      { key: 'sinr', label: 'SINR', unit: 'dB', goodMin: 10, warnMin: 0 },
      { key: 'rssi', label: 'RSSI', unit: 'dBm', goodMin: -75, warnMin: -95 }
    ];
    var hasAny = metrics.some(function (m) { return summary[m.key] && summary[m.key].count > 0; });
    if (!hasAny) { $('signal-stats-empty').hidden = false; return; }

    var t = el('table', 'dt-sd-signal-stats');
    var thead = el('thead');
    var hr = el('tr');
    ['Metric', 'Samples', 'Min', 'P5', 'P10', 'Median', 'Mean', 'P90', 'P95', 'Max'].forEach(function (h) {
      hr.appendChild(el('th', '', h));
    });
    thead.appendChild(hr);
    t.appendChild(thead);

    var tbody = el('tbody');
    metrics.forEach(function (m) {
      var d = summary[m.key];
      if (!d || !d.count) return;
      var tr = el('tr');
      tr.appendChild(el('td', '', m.label + ' (' + m.unit + ')'));
      tr.appendChild(el('td', 'num', d.count.toLocaleString()));
      var vals = [d.min, d.p5, d.p10, d.p50, d.mean, d.p90, d.p95, d.max];
      vals.forEach(function (v) {
        var td = el('td', 'num', v != null ? String(v) : '—');
        if (v != null) {
          if (v >= m.goodMin) td.classList.add('good');
          else if (v >= m.warnMin) td.classList.add('warn');
          else td.classList.add('bad');
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    t.appendChild(tbody);
    container.appendChild(t);
  }

  // ── pager helper ──
  function renderPager(nav, data, go) {
    nav.textContent = '';
    if (data.num_pages <= 1) return;
    function btn(label, page, cls) {
      var b = el(page ? 'a' : 'span', cls || (page ? '' : 'disabled'), label);
      if (page) { b.href = '#'; b.addEventListener('click', function (e) { e.preventDefault(); go(page); }); }
      return b;
    }
    nav.appendChild(btn('‹ Previous', data.has_prev ? data.page - 1 : 0));
    for (var p = Math.max(1, data.page - 2); p <= Math.min(data.num_pages, data.page + 2); p++) {
      nav.appendChild(p === data.page ? btn(String(p), 0, 'active') : btn(String(p), p));
    }
    nav.appendChild(btn('Next ›', data.has_next ? data.page + 1 : 0));
  }

  // ── Measurements ──
  var measForm = $('meas-filters'), measLoaded = false;
  var COLS = ['ts', 'lat', 'lon', 'tech', 'rssi', 'rsrp', 'rsrq', 'sinr', 'cell', 'speed'];
  var OPTIONAL = { rssi: 1, rsrp: 1, rsrq: 1, sinr: 1, speed: 1 };
  function loadMeas(page) {
    var params = { page: page || 1 };
    new FormData(measForm).forEach(function (v, k) { params[k] = v; });
    getJson(cfg.measUrl + '?' + qs(params)).then(function (d) {
      var tb = $('meas-tbody'); tb.textContent = '';
      $('meas-empty').hidden = d.rows.length > 0;
      $('meas-table').hidden = d.rows.length === 0;
      $('meas-range').textContent = d.total ? 'Showing ' + d.start + '–' + d.end + ' of ' + d.total.toLocaleString() + ' measurements' : '';
      d.rows.forEach(function (m) {
        var tr = el('tr');
        COLS.forEach(function (c) {
          var td = el('td'); td.dataset.col = c;
          if (['lat', 'lon', 'rssi', 'rsrp', 'rsrq', 'sinr', 'speed'].indexOf(c) !== -1) td.className = 'dt-sl-num';
          if (c === 'cell') {
            if (m.cell_id) { td.textContent = m.cell_id; if (m.site) td.title = m.site; }
            else { td.textContent = 'Unmatched'; td.className = 'dt-sd-cell-un'; }
          } else if (c === 'tech') td.textContent = m.tech || '—';
          else if (c === 'ts') td.textContent = m.ts || '—';
          else td.textContent = m[c] === null || m[c] === undefined ? '—' : m[c];
          tr.appendChild(td);
        });
        tb.appendChild(tr);
      });
      Object.keys(OPTIONAL).forEach(function (c) {
        var any = d.rows.some(function (m) { return m[c] !== null && m[c] !== undefined; });
        document.querySelectorAll('#meas-table [data-col="' + c + '"]').forEach(function (n) { n.hidden = !any; });
      });
      renderPager($('meas-pager'), d, loadMeas);
    }).catch(function () { $('meas-empty').hidden = false; $('meas-empty').textContent = 'Measurements could not be loaded.'; });
  }
  measForm.addEventListener('submit', function (e) { e.preventDefault(); loadMeas(1); });
  $('meas-clear').addEventListener('click', function () { measForm.reset(); loadMeas(1); });

  // ── Findings ──
  var findLoaded = false, modal = null, currentFinding = null;
  function sevBadge(f) { return el('span', 'dt-sd-sev dt-sd-sev-' + f.severity.toLowerCase(), f.severity); }
  function loc(f) {
    if (f.lat === null || f.lon === null) return '—';
    return f.lat.toFixed(4) + ', ' + f.lon.toFixed(4);
  }
  function findingRow(f) {
    var tr = el('tr');
    var td = el('td'); td.appendChild(sevBadge(f)); tr.appendChild(td);
    tr.appendChild(el('td', 'dt-sd-desc', f.description));
    tr.lastChild.title = f.description;
    tr.appendChild(el('td', '', f.category));
    tr.appendChild(el('td', '', f.technology || '—'));
    tr.appendChild(el('td', '', loc(f)));
    tr.appendChild(el('td', f.resolved ? '' : 'dt-sd-open', f.resolved ? 'RESOLVED' : 'OPEN'));
    tr.appendChild(el('td', '', f.created));
    tr.addEventListener('click', function () { openFinding(f); });
    return tr;
  }
  function buildFindingsTable(rows) {
    var t = el('table', 'dt-sl-table dt-sd-clickable');
    var h = el('thead'), hr = el('tr');
    ['Severity', 'Finding', 'Category', 'Technology', 'Location', 'Status', 'Created'].forEach(function (x) { hr.appendChild(el('th', '', x)); });
    h.appendChild(hr); t.appendChild(h);
    var b = el('tbody'); rows.forEach(function (f) { b.appendChild(findingRow(f)); }); t.appendChild(b);
    var wrap = el('div', 'dt-sl-scroll'); wrap.appendChild(t);
    return wrap;
  }
  function loadFindings(page) {
    getJson(cfg.findingsUrl + '?' + qs({ page: page || 1 })).then(function (d) {
      var tb = $('find-tbody'); tb.textContent = '';
      $('find-empty').hidden = d.total > 0;
      $('find-wrap').hidden = d.total === 0;
      $('find-range').textContent = d.total ? 'Showing ' + d.start + '–' + d.end + ' of ' + d.total.toLocaleString() : '';
      d.rows.forEach(function (f) { tb.appendChild(findingRow(f)); });
      renderPager($('find-pager'), d, loadFindings);
    }).catch(function () { $('find-empty').hidden = false; $('find-empty').textContent = 'Findings could not be loaded.'; });
  }
  function loadOverviewFindings() {
    getJson(cfg.findingsUrl + '?per_page=5').then(function (d) {
      var box = $('ov-findings'); box.textContent = '';
      if (!d.total) {
        var e = el('div', 'dt-sd-empty');
        e.appendChild(el('span', 'dt-sd-ok', '✓ No findings')); e.appendChild(el('br'));
        e.appendChild(el('span', 'dt-sd-muted', 'No findings were generated for this session.'));
        box.appendChild(e); return;
      }
      box.appendChild(buildFindingsTable(d.rows));
    }).catch(function () { $('ov-findings').textContent = 'Findings could not be loaded.'; });
  }
  function openFinding(f) {
    currentFinding = f;
    $('finding-modal-title').textContent = f.category + ' — ' + f.severity;
    var body = $('finding-modal-body'); body.textContent = '';

    var lin = el('div', 'dt-sd-lineage');
    var chain = [['Finding #' + f.id, true],
                 [f.measurement_seq !== null ? 'Measurement #' + f.measurement_seq : 'Measurement', f.measurement_seq !== null],
                 [f.cell ? 'Cell ' + f.cell : 'Cell', !!f.cell],
                 [f.sector ? 'Sector ' + f.sector : 'Sector', !!f.sector],
                 [f.site ? 'Site ' + f.site : 'Site', !!f.site],
                 ['Operator ' + f.operator, true]];
    chain.forEach(function (c, i) {
      if (i) lin.appendChild(el('i', '', '→'));
      lin.appendChild(el('span', c[1] ? '' : 'off', c[0]));
    });
    body.appendChild(el('div', 'dt-sd-sectiontitle', 'Data lineage'));
    body.appendChild(lin);

    var dl = el('dl', 'dt-sd-list');
    [['Severity', f.severity_label], ['Status', f.resolved ? 'Resolved' : 'Open'],
     ['Category', f.category], ['Technology', f.technology || '—'],
     ['Operator', f.operator], ['Location', loc(f) + (f.region ? ' (' + f.region + ')' : '')],
     ['Measured value', f.measured_value !== null ? String(f.measured_value) : '—'],
     ['Threshold value', f.threshold_value !== null ? String(f.threshold_value) : '—'],
     ['Measurement', f.measurement_seq !== null ? '#' + f.measurement_seq : '—'],
     ['Timestamp', f.measurement_time || '—'],
     ['Cell', f.cell || '—'], ['Sector', f.sector || '—'],
     ['Site', f.site ? f.site + (f.site_code ? ' (' + f.site_code + ')' : '') : '—'],
     ['Created', f.created_full]
    ].forEach(function (r) {
      var d = el('div'); d.appendChild(el('dt', '', r[0])); d.appendChild(el('dd', '', r[1])); dl.appendChild(d);
    });
    body.appendChild(el('div', 'dt-sd-sectiontitle', 'Evidence'));
    body.appendChild(dl);
    var dh = el('div', 'dt-sd-sectiontitle', 'Description'); dh.style.marginTop = '12px'; body.appendChild(dh);
    body.appendChild(el('p', '', f.description));
    if (f.notes) { body.appendChild(el('div', 'dt-sd-sectiontitle', 'Notes')); body.appendChild(el('p', '', f.notes)); }

    $('finding-show-map').hidden = f.lat === null || f.lon === null;
    if (!modal) modal = new bootstrap.Modal($('finding-modal'));
    modal.show();
  }
  $('finding-show-map').addEventListener('click', function () {
    if (!currentFinding) return;
    modal.hide();
    showTab('map');
    setTimeout(function () {
      if (!mapState.map) return;
      mapState.map.setView([currentFinding.lat, currentFinding.lon], 17);
      var mk = findingMarkers[currentFinding.id];
      if (mk) mk.openPopup();
    }, 300);
  });
  loadOverviewFindings();

  // ── lazy tab loading ──
  function onTab(name) {
    if (name === 'measurements' && !measLoaded) { measLoaded = true; loadMeas(1); }
    if (name === 'findings' && !findLoaded) { findLoaded = true; loadFindings(1); }
    if (name === 'map') {
      buildFullMap();
      setTimeout(function () { if (mapState.map) mapState.map.invalidateSize(); }, 100);
    }
    if (name === 'signal') {
      buildFullMap();
      Object.keys(chartInstances).forEach(function (k) {
        if (chartInstances[k] && chartInstances[k].resize) chartInstances[k].resize();
      });
    }
    if (name === 'overview' && maps['dt-map-overview']) maps['dt-map-overview'].map.invalidateSize();
  }
  var initial = (location.hash || '').replace('#', '');
  if (['measurements', 'kpis', 'map', 'signal', 'findings'].indexOf(initial) !== -1) showTab(initial);
})();
