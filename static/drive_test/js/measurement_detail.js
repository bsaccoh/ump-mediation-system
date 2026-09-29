(function () {
  var TILE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';
  var el = document.getElementById('dt-ms-map');
  if (!el || !window.L) return;
  var d = el.dataset, pts = [], map = L.map(el);
  L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(map);
  function dot(lat, lon, color, label) {
    var p = [parseFloat(lat), parseFloat(lon)];
    pts.push(p);
    L.circleMarker(p, { radius: 7, fillColor: color, color: '#fff', weight: 2, fillOpacity: 0.95 }).bindTooltip(label).addTo(map);
  }
  dot(d.lat, d.lon, '#1d4ed8', 'Measurement');
  if (d.cellLat) dot(d.cellLat, d.cellLon, '#7c3aed', 'Reference cell ' + d.cellLabel);
  if (d.siteLat) dot(d.siteLat, d.siteLon, '#0f766e', 'Site ' + d.siteLabel);
  map.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom: 17 });
})();
