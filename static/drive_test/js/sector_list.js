(function () {
  var TILE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // Sector detail: the parent site location, labelled as such.
  var dm = document.getElementById('dt-sc-detail-map');
  if (dm && window.L) {
    var pos = [parseFloat(dm.dataset.lat), parseFloat(dm.dataset.lon)];
    var m = L.map(dm).setView(pos, 16);
    L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(m);
    L.circleMarker(pos, { radius: 7, fillColor: '#0f766e', color: '#fff', weight: 2, fillOpacity: 0.95 })
      .bindTooltip('Site location: ' + dm.dataset.label).addTo(m);
  }

  // Sector list: site locations of the filtered sectors.
  var root = document.getElementById('dt-sc'), mapEl = document.getElementById('dt-sc-map');
  if (!root || !mapEl || !window.L) return;
  var emptyEl = document.getElementById('dt-sc-map-empty'), noteEl = document.getElementById('dt-sc-map-note');
  var params = new URLSearchParams(window.location.search);
  params.delete('page'); params.delete('per_page');
  var url = root.dataset.mapUrl + (params.toString() ? '?' + params.toString() : '');

  fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (data) {
      if (!data.features.length) { mapEl.hidden = true; emptyEl.hidden = false; return; }
      var map = L.map(mapEl), bounds = [];
      L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(map);
      data.features.forEach(function (f) {
        var c = f.geometry.coordinates, p = f.properties;
        bounds.push([c[1], c[0]]);
        L.circleMarker([c[1], c[0]], { radius: 6, fillColor: '#0f766e', color: '#fff', weight: 1.5, fillOpacity: 0.9 })
          .bindPopup(function () {
            var wrap = el('div', 'dt-st-popup');
            var a = el('a', '', p.site_id + ' — ' + p.name); a.href = p.url; a.style.fontWeight = '700'; wrap.appendChild(a);
            var t = el('table');
            [['Operator', p.operator], ['Sectors', String(p.sectors)], ['Location', 'Site location']].forEach(function (r) {
              var tr = el('tr'); tr.appendChild(el('td', '', r[0])); tr.appendChild(el('td', '', r[1])); t.appendChild(tr);
            });
            wrap.appendChild(t);
            return wrap;
          }).addTo(map);
      });
      map.fitBounds(L.latLngBounds(bounds), { padding: [24, 24], maxZoom: 15 });
      noteEl.textContent = data.features.length.toLocaleString() + ' sites' + (data.truncated ? ' (first 5,000)' : '');
    })
    .catch(function () { mapEl.hidden = true; emptyEl.hidden = false; emptyEl.textContent = 'Site map could not be loaded.'; });
})();
