(function () {
  var cfgEl = document.getElementById('dt-gis-config'), mapEl = document.getElementById('dt-gis-map');
  if (!cfgEl || !mapEl || !window.L) return;
  var cfg = JSON.parse(cfgEl.textContent);
  var SEV = { CRITICAL: '#b91c1c', HIGH: '#c2410c', MEDIUM: '#a16207', LOW: '#1d4ed8', INFO: '#64748b' };
  // Weak -> strong. A neutral sequential palette on purpose: it visualises a distribution, it does not judge compliance.
  var SCALE = ['#440154', '#3b528b', '#21908d', '#5dc863', '#fde725'];
  var NEUTRAL = '#9ca3af', BASE = '#2563eb';

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function $(id) { return document.getElementById(id); }
  function fmt(n) { return Number(n).toLocaleString(); }

  var params = new URLSearchParams(window.location.search);
  var filterParams = new URLSearchParams(params);
  ['metric', 'page', 'bbox', 'layer'].forEach(function (k) { filterParams.delete(k); });   // metric only restyles points
  var metric = params.get('metric') || '';

  // ── map ───────────────────────────────────────────────────────────
  var map = L.map(mapEl, { preferCanvas: true });
  var tileNote = null;
  if (cfg.tileUrl) {
    var tiles = L.tileLayer(cfg.tileUrl, { maxZoom: 19, attribution: cfg.attribution || '' }).addTo(map);
    var errs = 0;
    tiles.on('tileerror', function () {
      if (++errs === 4 && !tileNote) { tileNote = el('div', 'dt-gis-legend-note', 'Base map tiles are unavailable; map data is still shown.'); $('dt-gis-layer-note').appendChild(tileNote); }
    });
  } else {
    $('dt-gis-layer-note').appendChild(el('div', '', 'No basemap configured. Showing data points only.'));
  }
  map.setView([cfg.defaultView[0], cfg.defaultView[1]], cfg.defaultView[2]);

  var groups = { route: L.layerGroup(), measurements: L.layerGroup(), sites: L.layerGroup(), cells: L.layerGroup(),
                 sectors: L.layerGroup(), findings: L.layerGroup(), unmatched: L.layerGroup() };
  var enabled = {};
  document.querySelectorAll('[data-layer]').forEach(function (cb) {
    enabled[cb.dataset.layer] = cb.checked;
    if (cb.checked) groups[cb.dataset.layer].addTo(map);
  });

  // ── popups (DOM only, so values are always escaped) ───────────────
  function popup(title, rows, link) {
    var w = el('div', 'dt-gis-pop');
    w.appendChild(el('div', 'h', title));
    var t = el('table');
    rows.forEach(function (r) {
      var tr = el('tr'); tr.appendChild(el('td', '', r[0]));
      tr.appendChild(el('td', '', r[1] === '' || r[1] == null ? '—' : String(r[1])));
      t.appendChild(tr);
    });
    w.appendChild(t);
    if (link) { var d = el('div', 'act'), a = el('a', '', link[0]); a.href = link[1]; d.appendChild(a); w.appendChild(d); }
    return w;
  }
  function metricText(p, key) {
    var m = cfg.metrics[key];
    return p.m && p.m[key] != null ? p.m[key] + ' ' + m.unit : '';
  }
  function measPopup(p, c) {
    var rows = [['Time', p.t + ' UTC'], ['Operator', p.op], ['Technology', p.tech || '']];
    Object.keys(cfg.metrics).forEach(function (k) { if (p.m && p.m[k] != null) rows.push([cfg.metrics[k].label, metricText(p, k)]); });
    rows.push(['Cell', p.cell || ''], ['Match', p.match]);
    if (p.method) rows.push(['Method', p.method]);
    if (p.conf != null) rows.push(['Confidence', p.conf]);
    rows.push(['Session', p.sess], ['Location', c[1].toFixed(5) + ', ' + c[0].toFixed(5)]);
    var w = popup('Measurement', rows, ['View Measurement', cfg.measurementUrl.replace(/0\/$/, p.id + '/')]);
    return w;
  }

  // ── metric colouring ──────────────────────────────────────────────
  var stats = null;   // {min, max} of the selected metric over the loaded points
  function computeStats(features) {
    stats = null;
    if (!metric) return;
    var min = Infinity, max = -Infinity;
    features.forEach(function (f) {
      var v = f.properties.m && f.properties.m[metric];
      if (v != null) { if (v < min) min = v; if (v > max) max = v; }
    });
    if (min <= max) stats = { min: min, max: max };
  }
  function binOf(v) {
    if (stats.max === stats.min) return SCALE.length - 1;
    return Math.min(SCALE.length - 1, Math.floor((v - stats.min) / (stats.max - stats.min) * SCALE.length));
  }
  function colourOf(p) {
    if (!metric) return BASE;
    var v = p.m && p.m[metric];
    return v == null || !stats ? NEUTRAL : SCALE[binOf(v)];
  }
  function renderLegend() {
    var box = $('dt-gis-legend'); box.textContent = '';
    if (!metric) { box.appendChild(el('div', 'dt-gis-legend-note', 'Choose a metric to colour measurements by an actual radio value.')); return; }
    var info = cfg.metrics[metric];
    box.appendChild(el('div', 'dt-gis-legend-cap', info.label + ' (' + info.unit + ')'));
    if (!stats) { box.appendChild(el('div', 'dt-gis-legend-note', 'No ' + info.label + ' values in the current selection.')); return; }
    var span = (stats.max - stats.min) / SCALE.length;
    for (var i = SCALE.length - 1; i >= 0; i--) {
      var lo = stats.min + span * i, hi = i === SCALE.length - 1 ? stats.max : stats.min + span * (i + 1);
      var row = el('div', 'dt-gis-legend-row'), sw = el('i'); sw.style.background = SCALE[i];
      row.appendChild(sw);
      row.appendChild(el('span', '', (stats.max === stats.min ? lo.toFixed(1) : lo.toFixed(1) + ' to ' + hi.toFixed(1)) + (i === SCALE.length - 1 ? '  (strongest)' : i === 0 ? '  (weakest)' : '')));
      box.appendChild(row);
      if (stats.max === stats.min) break;
    }
    var nv = el('div', 'dt-gis-legend-row'), s2 = el('i'); s2.style.background = NEUTRAL; nv.appendChild(s2); nv.appendChild(el('span', '', 'No value for this metric'));
    box.appendChild(nv);
    box.appendChild(el('div', 'dt-gis-legend-note', 'Equal-interval bins of the observed range in the current selection. A visual aid only; it is not a regulatory threshold.'));
  }

  // ── data loading ──────────────────────────────────────────────────
  function q(layer, bbox) {
    var p = new URLSearchParams(filterParams);
    p.set('layer', layer);
    if (bbox) p.set('bbox', bbox);
    return cfg.dataUrl + '?' + p.toString();
  }
  function getJson(url) {
    return fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }
  function viewBbox() {
    var b = map.getBounds().pad(0.15);
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(function (n) { return n.toFixed(6); }).join(',');
  }
  function setCount(layer, text) { var n = $('dt-gis-n-' + layer); if (n) n.textContent = text || ''; }
  var notes = {};
  function setNote(key, text) { if (text) notes[key] = text; else delete notes[key]; renderNotes(); }
  function renderNotes() {
    var box = $('dt-gis-layer-note');
    Array.prototype.slice.call(box.querySelectorAll('.dt-gis-n')).forEach(function (n) { n.remove(); });
    Object.keys(notes).forEach(function (k) { box.appendChild(el('div', 'dt-gis-n', notes[k])); });
  }

  var measTok = 0, measData = null, firstFit = true;
  function loadMeasurements(bbox) {
    var tok = ++measTok;
    return getJson(q('measurements', bbox)).then(function (d) {
      if (tok !== measTok) return;
      measData = d;
      var feats = d.features;
      computeStats(feats);
      renderLegend();
      groups.route.clearLayers(); groups.measurements.clearLayers(); groups.unmatched.clearLayers();
      d.route.forEach(function (seg) {
        L.polyline(seg.map(function (c) { return [c[1], c[0]]; }), { color: '#475569', weight: 2, opacity: 0.6, interactive: false }).addTo(groups.route);
      });
      var pts = [];
      feats.forEach(function (f) {
        var c = f.geometry.coordinates, p = f.properties, ll = [c[1], c[0]];
        pts.push(ll);
        L.circleMarker(ll, { radius: 5, fillColor: colourOf(p), color: '#fff', weight: 1, fillOpacity: 0.9 })
          .bindPopup(function () { return measPopup(p, c); }).addTo(groups.measurements);
        if (p.match === 'UNMATCHED') {
          L.circleMarker(ll, { radius: 8, fill: false, color: '#475569', weight: 2, dashArray: '3 3' })
            .bindPopup(function () { return measPopup(p, c); }).addTo(groups.unmatched);
        }
      });
      setCount('measurements', fmt(feats.length) + (d.thinned ? ' of ' + fmt(d.total_located) : ''));
      setCount('route', d.route.length ? fmt(d.route.length) + (d.route.length > 1 ? ' segments' : ' segment') : '');
      setCount('unmatched', fmt(groups.unmatched.getLayers().length));
      setNote('route', d.route.length ? '' : 'Route unavailable: not enough consecutive GPS fixes.');
      setNote('thin', d.thinned ? 'Showing an evenly thinned ' + fmt(feats.length) + ' of ' + fmt(d.total_located) + ' points. Zoom in to load more detail.' : '');
      if (firstFit) {
        firstFit = false;
        if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom: 17 });
      }
    });
  }

  var refCfg = {
    sites: function (f) {
      var c = f.geometry.coordinates, p = f.properties;
      return L.circleMarker([c[1], c[0]], { radius: 6, fillColor: '#0f766e', color: '#fff', weight: 2, fillOpacity: 0.95 })
        .bindPopup(function () { return popup('Site', [['Site code', p.site_id], ['Name', p.name], ['Operator', p.op], ['Region', p.region], ['District', p.district], ['Coordinates', c[1].toFixed(5) + ', ' + c[0].toFixed(5)]], ['View Site', p.url]); });
    },
    cells: function (f) {
      var c = f.geometry.coordinates, p = f.properties;
      return L.circleMarker([c[1], c[0]], { radius: 5, fillColor: '#7c3aed', color: '#fff', weight: 1.5, fillOpacity: 0.9 })
        .bindPopup(function () { return popup('Cell', [['Cell ID', p.cell_id], ['Technology', p.tech], ['Operator', p.op], ['Site', p.site], ['Sector', p.sector], ['Band', p.band], ['Status', p.active ? 'Active' : 'Inactive']], ['View Cell', p.url]); });
    },
    sectors: function (f) {
      // Sectors have no coordinates of their own: the line starts at the parent site and only shows the azimuth.
      var c = f.geometry.coordinates, p = f.properties, len = 150, az = p.azimuth * Math.PI / 180;
      var dest = [c[1] + (len / 111320) * Math.cos(az), c[0] + (len / (111320 * Math.cos(c[1] * Math.PI / 180))) * Math.sin(az)];
      return L.polyline([[c[1], c[0]], dest], { color: '#b45309', weight: 4, opacity: 0.9 })
        .bindPopup(function () { return popup('Sector', [['Sector', p.sector_id], ['Site', p.site], ['Operator', p.op], ['Azimuth', p.azimuth + '°'], ['Height', p.height != null ? p.height + ' m' : ''], ['Location', 'Site location (sectors have no coordinates of their own)'], ['Drawn as', 'Azimuth direction only, not coverage']], ['View Sector', p.url]); });
    },
    findings: function (f) {
      var c = f.geometry.coordinates, p = f.properties;
      return L.circleMarker([c[1], c[0]], { radius: 7, fillColor: SEV[p.severity] || '#64748b', color: '#111827', weight: 1.5, fillOpacity: p.resolved ? 0.4 : 0.9 })
        .bindPopup(function () { return popup('Finding', [['Category', p.category], ['Severity', p.severity], ['Technology', p.tech], ['Location', c[1].toFixed(5) + ', ' + c[0].toFixed(5)], ['Cell', p.cell], ['Site', p.site], ['Session', p.sess], ['Status', p.resolved ? 'Resolved' : 'Open']], ['View Finding', p.url]); });
    }
  };
  var refTok = { sites: 0, cells: 0, sectors: 0, findings: 0 };
  function loadRef(layer) {
    var tok = ++refTok[layer];
    return getJson(q(layer, viewBbox())).then(function (d) {
      if (tok !== refTok[layer]) return;
      groups[layer].clearLayers();
      d.features.forEach(function (f) { refCfg[layer](f).addTo(groups[layer]); });
      setCount(layer, fmt(d.features.length) + ' in view');
      setNote('trunc-' + layer, d.truncated ? layer + ': first ' + fmt(d.features.length) + ' in view. Zoom in to see the rest.' : '');
      if (layer === 'findings') setNote('empty-findings', d.features.length ? '' : 'No geographically located findings available.');
    }).catch(function () { setNote('err-' + layer, 'Could not load the ' + layer + ' layer.'); });
  }

  // ── layer toggles ─────────────────────────────────────────────────
  document.querySelectorAll('[data-layer]').forEach(function (cb) {
    cb.addEventListener('change', function () {
      var name = cb.dataset.layer;
      enabled[name] = cb.checked;
      if (cb.checked) { groups[name].addTo(map); if (refCfg[name]) loadRef(name); }
      else { map.removeLayer(groups[name]); if (name === 'findings') setNote('empty-findings', ''); }
    });
  });

  // ── refresh on pan/zoom (viewport queries) ────────────────────────
  var moveTimer = null;
  map.on('moveend', function () {
    clearTimeout(moveTimer);
    moveTimer = setTimeout(function () {
      Object.keys(refCfg).forEach(function (l) { if (enabled[l]) loadRef(l); });
      if (measData && measData.thinned) loadMeasurements(viewBbox()).catch(function () {});
    }, 400);
  });

  // ── metric selector restyles without reloading ────────────────────
  var metricSel = $('g-metric');
  metricSel.addEventListener('change', function () {
    metric = metricSel.value;
    var u = new URL(window.location.href);
    if (metric) u.searchParams.set('metric', metric); else u.searchParams.delete('metric');
    window.history.replaceState(null, '', u.toString());
    loadMeasurements(measData && measData.thinned ? viewBbox() : null).catch(function () {});
  });

  // ── summary and messages ──────────────────────────────────────────
  function renderSummary(s) {
    var dl = $('dt-gis-summary'); dl.textContent = '';
    [['Measurements', s.total], ['Mapped', s.mapped], ['Without location', s.without_location],
     ['Matched', s.matched], ['Unmatched', s.unmatched], ['Unknown (not yet matched)', s.unknown],
     ['Invalid records excluded', s.invalid], ['Sites', s.sites], ['Cells', s.cells], ['Sectors (with azimuth)', s.sectors],
     ['Findings', s.findings_total], ['Findings with location', s.findings_located]].forEach(function (r) {
      var d = el('div'); d.appendChild(el('dt', '', r[0])); d.appendChild(el('dd', '', fmt(r[1]))); dl.appendChild(d);
    });
    var msg = $('dt-gis-msg');
    if (s.total === 0) { msg.textContent = 'No geographic measurements match the selected filters.'; msg.hidden = false; }
    else if (s.mapped === 0) { msg.textContent = 'Measurements found, but no valid geographic coordinates are available.'; msg.hidden = false; }
    else msg.hidden = true;
  }

  getJson(q('summary')).then(function (s) {
    summaryData = s; renderSummary(s);
    setCount('sites', '');
  }).catch(function () { $('dt-gis-summary').textContent = 'Summary unavailable.'; });

  renderLegend();
  loadMeasurements(null).catch(function () { setNote('err-meas', 'Could not load measurements.'); })
    .then(function () {
      Object.keys(refCfg).forEach(function (l) { if (enabled[l]) loadRef(l); });
    });

  // ── collapsible layer panel (small screens) ───────────────────────
  var panel = $('dt-gis-panel'), toggle = $('dt-gis-toggle');
  toggle.addEventListener('click', function () {
    var c = panel.classList.toggle('is-collapsed');
    toggle.setAttribute('aria-expanded', c ? 'false' : 'true');
    setTimeout(function () { map.invalidateSize(); }, 50);
  });
  if (window.matchMedia('(max-width: 991px)').matches) { panel.classList.add('is-collapsed'); toggle.setAttribute('aria-expanded', 'false'); }
  window.addEventListener('resize', function () { map.invalidateSize(); });
})();
