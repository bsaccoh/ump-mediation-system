/* Regulatory Reports — renders the report's real matched-site geography on a Leaflet
   map. All points come from the server (report_map_data); nothing is computed here. */
(function () {
  var root = document.getElementById('dt-rp');
  var mapEl = document.getElementById('dt-rp-map');
  if (!root || !mapEl || !window.L) return;

  var url = root.getAttribute('data-map-url');
  if (!url) return;

  fetch(url).then(function (r) { return r.json(); }).then(function (geojson) {
    var features = (geojson && geojson.features) || [];
    if (!features.length) {
      mapEl.hidden = true;
      var empty = document.getElementById('dt-rp-map-empty');
      if (empty) empty.hidden = false;
      return;
    }
    var map = L.map(mapEl, { scrollWheelZoom: false });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors', maxZoom: 18,
    }).addTo(map);

    var layer = L.geoJSON(geojson, {
      pointToLayer: function (feature, latlng) {
        return L.circleMarker(latlng, { radius: 6, color: '#1a237e', fillColor: '#1a237e', fillOpacity: 0.7 });
      },
      onEachFeature: function (feature, lyr) {
        var p = feature.properties || {};
        lyr.bindPopup('<strong>' + (p.site_id || '') + '</strong><br>' + (p.name || '') + '<br>' + (p.operator || ''));
      },
    }).addTo(map);
    map.fitBounds(layer.getBounds(), { padding: [20, 20], maxZoom: 14 });
  }).catch(function () {
    mapEl.hidden = true;
  });
})();
