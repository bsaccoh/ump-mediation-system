/*
 * Workspace map pane. A pure subscriber — reads state, never writes it except
 * through a mutator when the user clicks.
 */
(function () {
  'use strict';

  var host = document.getElementById('dt-ws-map');
  if (!host || !window.L || !window.DTWorkspace) return;

  var DT = window.DT;
  var WS = window.DTWorkspace;
  var cfg = WS.config;

  var map = L.map(host, { preferCanvas: true, zoomControl: true });
  var tileUrl = cfg.tileUrl || 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';
  L.tileLayer(tileUrl, {
    attribution: cfg.tileAttribution || '',
    maxZoom: 19
  }).addTo(map);
  map.setView([8.484, -13.23], 12);

  var routeLayer = L.layerGroup().addTo(map);
  var eventLayer = L.layerGroup().addTo(map);
  var cursorMarker = null;
  var dots = [];

  var legend = document.getElementById('dt-ws-legend');

  /* Guards the map -> cursor -> map loop: only recentre when the cursor moved
     because of another pane, never because of a click on this map. */
  var selfDriven = false;

  function drawRoute() {
    var data = WS.getData();
    routeLayer.clearLayers();
    dots = [];
    if (!data || !data.t.length) return;

    var metric = WS.getMetric();
    var series = data.series[metric] || [];
    var points = [];

    for (var i = 0; i < data.t.length; i++) {
      var lat = data.lat[i];
      var lon = data.lon[i];
      if (lat === null || lon === null || (lat === 0 && lon === 0)) continue;
      points.push([lat, lon]);

      var dot = L.circleMarker([lat, lon], {
        radius: 4,
        color: DT.metricColour(metric, series[i]),
        fillColor: DT.metricColour(metric, series[i]),
        fillOpacity: 0.85,
        weight: 1
      });
      dot.__index = i;
      dot.on('click', function () {
        selfDriven = true;
        WS.setCursor(this.__index);
        selfDriven = false;
      });
      dot.addTo(routeLayer);
      dots.push(dot);
    }

    if (points.length) {
      L.polyline(points, { color: '#1e2b4a', weight: 1.5, opacity: 0.35 })
        .addTo(routeLayer)
        .bringToBack();
      map.fitBounds(L.latLngBounds(points), { padding: [24, 24] });
    }
  }

  function drawEvents() {
    var data = WS.getData();
    eventLayer.clearLayers();
    if (!data) return;

    data.events.forEach(function (ev) {
      if (ev.lat === null || ev.lon === null) return;
      var marker = L.circleMarker([ev.lat, ev.lon], {
        radius: 6,
        color: '#ffffff',
        weight: 2,
        fillColor: DT.severityColour(ev.severity),
        fillOpacity: 1
      });
      marker.bindTooltip(ev.type + (ev.label ? ' — ' + ev.label : ''));
      marker.on('click', function () {
        if (ev.idx !== null) {
          selfDriven = true;
          WS.setCursor(ev.idx);
          selfDriven = false;
        }
      });
      marker.addTo(eventLayer);
    });
  }

  function renderLegend() {
    if (!legend) return;
    DT.clear(legend);

    var metric = WS.getMetric();
    var spec = DT.metric(metric);
    var title = DT.el('span', 'dt-ws-legend-title',
      spec.label + (spec.unit ? ' (' + spec.unit + ')' : ''));
    legend.appendChild(title);

    DT.legendRows(metric).forEach(function (row) {
      var item = DT.el('span', 'dt-ws-legend-item');
      var swatch = DT.el('i');
      swatch.style.background = row.colour;
      item.appendChild(swatch);
      item.appendChild(DT.el('span', null, row.label));
      legend.appendChild(item);
    });
  }

  function moveCursor(index) {
    var sample = WS.sampleAt(index);
    if (!sample || sample.lat === null || sample.lon === null) return;

    var position = [sample.lat, sample.lon];
    if (!cursorMarker) {
      cursorMarker = L.circleMarker(position, {
        radius: 8,
        color: '#0b5fb0',
        weight: 3,
        fillColor: '#ffffff',
        fillOpacity: 1
      }).addTo(map);
    } else {
      cursorMarker.setLatLng(position);
    }
    cursorMarker.bringToFront();

    // Only follow the cursor when another pane moved it, and only when the
    // sample has gone off screen — otherwise the map fights the user.
    if (!selfDriven && !map.getBounds().contains(position)) {
      map.panTo(position, { animate: true });
    }
  }

  WS.on('dt:data-loaded', function () { drawRoute(); drawEvents(); });
  WS.on('dt:metric', function () { drawRoute(); renderLegend(); });
  WS.on('dt:cursor', function (e) { moveCursor(e.detail.index); });

  // Leaflet needs a resize nudge whenever its container changes size.
  window.addEventListener('resize', function () { map.invalidateSize(); });
  setTimeout(function () { map.invalidateSize(); }, 80);
}());
