(function () {
  var dm = document.getElementById('dt-st-detail-map');
  if (dm && window.L) {
    var pos = [parseFloat(dm.dataset.lat), parseFloat(dm.dataset.lon)];
    var m = L.map(dm).setView(pos, 16);
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
                { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(m);
    L.circleMarker(pos, { radius: 7, fillColor: '#1a237e', color: '#fff', weight: 2, fillOpacity: 0.95 })
      .bindTooltip(dm.dataset.label).addTo(m);
  }
})();
(function () {
  var root = document.getElementById('dt-st');
  var mapEl = document.getElementById('dt-st-map');
  if (!root || !mapEl || !window.L) return;
  var emptyEl = document.getElementById('dt-st-map-empty');
  var noteEl = document.getElementById('dt-st-map-note');

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function popup(p) {
    var wrap = el('div', 'dt-st-popup');
    var a = el('a', '', p.site_id + ' — ' + p.name); a.href = p.url; a.style.fontWeight = '700';
    wrap.appendChild(a);
    var t = el('table');
    [['Operator', p.operator], ['Coordinates', p.lat + ', ' + p.lon], ['Technology', p.technology]].forEach(function (r) {
      if (!r[1]) return;
      var tr = el('tr'); tr.appendChild(el('td', '', r[0])); tr.appendChild(el('td', '', r[1])); t.appendChild(tr);
    });
    wrap.appendChild(t);
    return wrap;
  }

  // The map follows the same filters as the table (page number is irrelevant).
  var params = new URLSearchParams(window.location.search);
  params.delete('page'); params.delete('per_page');
  var url = root.dataset.mapUrl + (params.toString() ? '?' + params.toString() : '');
  var markers = {}, map = null;

  fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (data) {
      if (!data.features.length) { mapEl.hidden = true; emptyEl.hidden = false; return; }
      map = L.map(mapEl);
      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
                  { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(map);
      var bounds = [];
      data.features.forEach(function (f) {
        var c = f.geometry.coordinates, p = f.properties;
        bounds.push([c[1], c[0]]);
        markers[p.id] = L.circleMarker([c[1], c[0]], {
          radius: 6, fillColor: p.active ? '#1a237e' : '#9ca3af', color: '#fff', weight: 1.5, fillOpacity: 0.9
        }).bindPopup(function () { return popup(p); }).addTo(map);
      });
      map.fitBounds(L.latLngBounds(bounds), { padding: [24, 24], maxZoom: 15 });
      noteEl.textContent = data.features.length.toLocaleString() + ' mapped' + (data.truncated ? ' (first 5,000)' : '');
    })
    .catch(function () { mapEl.hidden = true; emptyEl.hidden = false; emptyEl.textContent = 'Site map could not be loaded.'; });

  // Location icon in the table: focus the site on the map.
  document.querySelectorAll('.dt-st-pin').forEach(function (b) {
    b.addEventListener('click', function () {
      var m = markers[b.dataset.site];
      if (!m || !map) return;
      mapEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      map.setView(m.getLatLng(), 16);
      m.openPopup();
    });
  });
})();
