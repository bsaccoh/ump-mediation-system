/* Drive Test — Map Analysis controller.
 *
 * One stateful controller owns { metric, technology, cursorId } and the fetched
 * data; the map, timeline and inspector are its subscribers. Signal colours are
 * read from CSS custom properties (no hard-coded hex), so dark mode / rebrands
 * need no JS change. The backend classifies and decimates; this only paints.
 */
(function () {
  'use strict';
  var cfgEl = document.getElementById('dt-config');
  if (!cfgEl || typeof L === 'undefined') return;
  var CFG = JSON.parse(cfgEl.textContent);

  // Colour tokens resolved from the stylesheet.
  function token(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  var COLORS = {
    excellent: token('--dt-signal-excellent', '#1a9850'),
    good:      token('--dt-signal-good', '#91cf60'),
    fair:      token('--dt-signal-fair', '#fee08b'),
    poor:      token('--dt-signal-poor', '#fc8d59'),
    critical:  token('--dt-signal-critical', '#d73027'),
    none:      token('--dt-signal-none', '#9ca3af')
  };

  var state = { metric: CFG.defaultMetric, technology: '', cursorId: null };

  // --- Map ------------------------------------------------------------------
  var map = L.map('dt-map', { preferCanvas: true }).setView([8.484, -13.23], 12);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 19, subdomains: 'abcd'
  }).addTo(map);
  var canvas = L.canvas({ padding: 0.5 });
  var routeLayer = L.layerGroup().addTo(map);
  var pointLayer = L.layerGroup().addTo(map);
  var markerById = {};
  var highlight = null;

  var showRoute = true, showPoints = true;
  var timeseries = null, chart = null;

  function el(id) { return document.getElementById(id); }

  function renderLegend(bands) {
    var box = el('dt-legend');
    box.innerHTML = '';
    (bands || []).forEach(function (b) {
      var span = document.createElement('span');
      span.className = 'item';
      span.innerHTML = '<span class="swatch" style="background:' + (COLORS[b.color] || COLORS.none) + '"></span>' + b.label;
      box.appendChild(span);
    });
  }

  function loadMap() {
    var url = CFG.mapUrl + '?metric=' + encodeURIComponent(state.metric) +
      '&technology=' + encodeURIComponent(state.technology);
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      routeLayer.clearLayers();
      pointLayer.clearLayers();
      markerById = {};

      if (showRoute && d.route && d.route.length > 1) {
        L.polyline(d.route, { color: '#334155', weight: 3, opacity: 0.6 }).addTo(routeLayer);
      }
      if (showPoints && d.points) {
        d.points.forEach(function (p) {
          var m = L.circleMarker([p.lat, p.lon], {
            renderer: canvas, radius: 4, weight: 0,
            fillColor: COLORS[p.color] || COLORS.none, fillOpacity: 0.85
          });
          m.dtId = p.id;
          m.bindTooltip(d.meta.label + ': ' + fmt(p.v) + ' ' + (d.meta.unit || '') + ' (' + p.band + ')');
          m.on('click', function () { inspect(p.id, true); });
          m.addTo(pointLayer);
          markerById[p.id] = m;
        });
      }
      renderLegend(d.meta.bands);
      if (d.center) {
        var pts = (d.route && d.route.length) ? d.route : (d.points || []).map(function (p) { return [p.lat, p.lon]; });
        if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.1));
      }
    });
  }

  function fmt(v) { return (v === null || v === undefined) ? '—' : v; }

  // --- Timeline (Chart.js linear axis over ms-from-start) -------------------
  function loadTimeline() {
    var url = CFG.timeseriesUrl + '?metrics=' + encodeURIComponent(state.metric) +
      '&technology=' + encodeURIComponent(state.technology);
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      timeseries = d;
      var arr = (d.series && d.series[state.metric]) || [];
      var pts = [];
      for (var i = 0; i < arr.length; i++) {
        if (arr[i] !== null && d.t[i] !== null) pts.push({ x: d.t[i] / 1000, y: arr[i], id: d.ids[i] });
      }
      var label = (d.meta.labels && d.meta.labels[state.metric]) || state.metric;
      var unit = (d.meta.units && d.meta.units[state.metric]) || '';
      if (chart) chart.destroy();
      if (typeof Chart === 'undefined') return;
      chart = new Chart(el('dt-chart').getContext('2d'), {
        type: 'line',
        data: { datasets: [{
          label: label + (unit ? ' (' + unit + ')' : ''),
          data: pts, parsing: false, showLine: true,
          borderColor: token('--dt-accent', '#1a56db'), borderWidth: 1.2,
          pointRadius: 0, pointHitRadius: 6, tension: 0
        }] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          scales: {
            x: { type: 'linear', title: { display: true, text: 'Seconds from start' },
                 ticks: { maxTicksLimit: 8 } },
            y: { title: { display: true, text: unit || label } }
          },
          plugins: { legend: { display: true } },
          onClick: function (evt, els) {
            if (els && els.length) {
              var p = pts[els[0].index];
              if (p) inspect(p.id, false);
            }
          }
        }
      });
    });
  }

  // --- Inspector ------------------------------------------------------------
  function inspect(id, fromMap) {
    state.cursorId = id;
    if (highlight) { pointLayer.removeLayer(highlight); highlight = null; }
    var m = markerById[id];
    if (m) {
      highlight = L.circleMarker(m.getLatLng(), { radius: 8, weight: 2,
        color: token('--dt-accent', '#1a56db'), fill: false }).addTo(pointLayer);
      if (!fromMap) map.panTo(m.getLatLng());
    }
    var url = CFG.sampleUrlTemplate.replace('999999', id);
    fetch(url).then(function (r) { return r.json(); }).then(renderInspector);
  }

  function kv(pairs) {
    var rows = pairs.filter(function (p) { return p.v !== null && p.v !== undefined && p.v !== ''; });
    if (!rows.length) return '<div class="dt-na" style="font-size:var(--dt-fs-xs)">Not available in source data</div>';
    return '<div class="dt-kv">' + rows.map(function (p) {
      return '<span class="k">' + p.k + '</span><span class="v">' + p.v + '</span>';
    }).join('') + '</div>';
  }

  function renderInspector(d) {
    var html = '';
    html += '<div class="dt-kv"><span class="k">Sample</span><span class="v">#' + d.id + '</span>';
    html += '<span class="k">Time</span><span class="v">' + (d.timestamp ? d.timestamp.replace('T', ' ').slice(0, 19) : '—') + '</span>';
    html += '<span class="k">Technology</span><span class="v">' + (d.technology || '—') + '</span></div>';
    html += '<div class="grp"><h4>Position</h4>' + kv(d.position) + '</div>';
    html += '<div class="grp"><h4>Identity</h4>' + kv(d.identity) + '</div>';
    html += '<div class="grp"><h4>Serving Cell <span class="dt-prov observed">Observed</span></h4>' + kv(d.serving_cell.observed);
    if (d.serving_cell.matched) {
      html += '<div style="margin-top:6px"><span class="dt-prov authoritative">Matched</span> ' +
        d.serving_cell.match_method + (d.serving_cell.match_confidence != null ? ' (' + d.serving_cell.match_confidence + ')' : '') + '</div>';
      html += kv(d.serving_cell.matched);
    }
    html += '</div>';
    html += '<div class="grp"><h4>RF</h4>' + kv(d.rf) + '</div>';
    html += '<div class="grp"><h4>Data</h4>' + kv(d.data) + '</div>';
    if (d.quality_flags && d.quality_flags.length) {
      html += '<div class="grp"><h4>Quality flags</h4><div style="font-size:var(--dt-fs-xs);color:var(--dt-text-muted)">' +
        d.quality_flags.join(', ') + '</div></div>';
    }
    el('dt-inspect').innerHTML = html;
  }

  // --- Wiring ---------------------------------------------------------------
  el('dt-metric').addEventListener('change', function (e) {
    state.metric = e.target.value; loadMap(); loadTimeline();
  });
  el('dt-tech').addEventListener('change', function (e) {
    state.technology = e.target.value; loadMap(); loadTimeline();
  });
  el('dt-layer-route').addEventListener('change', function (e) { showRoute = e.target.checked; loadMap(); });
  el('dt-layer-points').addEventListener('change', function (e) { showPoints = e.target.checked; loadMap(); });

  loadMap();
  loadTimeline();
})();
