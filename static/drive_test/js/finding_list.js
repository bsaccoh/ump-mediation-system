(function () {
  var TILE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';
  var SEV = { CRITICAL: '#b91c1c', HIGH: '#c2410c', MEDIUM: '#a16207', LOW: '#1d4ed8', INFO: '#64748b' };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // Finding detail: the finding, plus the parent site when known.
  var dm = document.getElementById('dt-fd-detail-map');
  if (dm && window.L) {
    var pts = [], m = L.map(dm);
    L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(m);
    var fp = [parseFloat(dm.dataset.lat), parseFloat(dm.dataset.lon)];
    pts.push(fp);
    L.circleMarker(fp, { radius: 8, fillColor: SEV[dm.dataset.severity] || '#64748b', color: '#fff', weight: 2, fillOpacity: 0.95 }).bindTooltip('Finding location').addTo(m);
    if (dm.dataset.siteLat) {
      var sp = [parseFloat(dm.dataset.siteLat), parseFloat(dm.dataset.siteLon)];
      pts.push(sp);
      L.circleMarker(sp, { radius: 6, fillColor: '#0f766e', color: '#fff', weight: 2, fillOpacity: 0.95 }).bindTooltip('Site ' + dm.dataset.siteLabel).addTo(m);
    }
    m.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom: 17 });
  }

  // Findings list map.
  var root = document.getElementById('dt-fd'), mapEl = document.getElementById('dt-fd-map');
  if (!root || !mapEl || !window.L) return;
  var emptyEl = document.getElementById('dt-fd-map-empty'), noteEl = document.getElementById('dt-fd-map-note');
  var params = new URLSearchParams(window.location.search);
  params.delete('page'); params.delete('per_page');
  var url = root.dataset.mapUrl + (params.toString() ? '?' + params.toString() : '');
  var markers = {}, map = null;

  fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (data) {
      if (!data.features.length) { mapEl.hidden = true; emptyEl.hidden = false; return; }
      map = L.map(mapEl);
      L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(map);
      var bounds = [];
      data.features.forEach(function (f) {
        var c = f.geometry.coordinates, p = f.properties;
        bounds.push([c[1], c[0]]);
        markers[p.id] = L.circleMarker([c[1], c[0]], { radius: 6, fillColor: SEV[p.severity] || '#64748b', color: '#fff', weight: 1.5, fillOpacity: p.resolved ? 0.4 : 0.9 })
          .bindPopup(function () {
            var w = el('div', 'dt-st-popup');
            var a = el('a', '', p.category + ' — ' + p.severity); a.href = p.url; a.style.fontWeight = '700'; w.appendChild(a);
            var t = el('table');
            [['Session', p.session], ['Status', p.resolved ? 'Resolved' : 'Open'], ['Detail', p.description]].forEach(function (r) {
              if (!r[1]) return; var tr = el('tr'); tr.appendChild(el('td', '', r[0])); tr.appendChild(el('td', '', r[1])); t.appendChild(tr);
            });
            w.appendChild(t);
            return w;
          }).addTo(map);
      });
      map.fitBounds(L.latLngBounds(bounds), { padding: [24, 24], maxZoom: 16 });
      noteEl.textContent = data.features.length.toLocaleString() + ' located' + (data.truncated ? ' (first 5,000)' : '');
    })
    .catch(function () { mapEl.hidden = true; emptyEl.hidden = false; emptyEl.textContent = 'Finding map could not be loaded.'; });

  document.querySelectorAll('.dt-st-pin[data-finding]').forEach(function (b) {
    b.addEventListener('click', function () {
      var mk = markers[b.dataset.finding];
      if (!mk || !map) return;
      mapEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      map.setView(mk.getLatLng(), 17); mk.openPopup();
    });
  });
})();
