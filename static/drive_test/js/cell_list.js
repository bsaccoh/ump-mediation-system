(function () {
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  var TILE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';
  function popup(title, url, rows) {
    var wrap = el('div', 'dt-st-popup');
    var a = el('a', '', title); a.href = url; a.style.fontWeight = '700'; wrap.appendChild(a);
    var t = el('table');
    rows.forEach(function (r) {
      if (!r[1]) return;
      var tr = el('tr'); tr.appendChild(el('td', '', r[0])); tr.appendChild(el('td', '', r[1])); t.appendChild(tr);
    });
    wrap.appendChild(t);
    return wrap;
  }

  // ── Cell detail: single location map ──────────────────────────────
  var dm = document.getElementById('dt-ce-detail-map');
  if (dm && window.L) {
    var m = L.map(dm);
    L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(m);
    var pts = [];
    if (dm.dataset.cellLat) {
      var cp = [parseFloat(dm.dataset.cellLat), parseFloat(dm.dataset.cellLon)];
      pts.push(cp);
      L.circleMarker(cp, { radius: 8, fillColor: '#1a237e', color: '#fff', weight: 2, fillOpacity: 0.95 }).bindTooltip('Cell ' + dm.dataset.label).addTo(m);
    }
    if (dm.dataset.siteLat) {
      var sp = [parseFloat(dm.dataset.siteLat), parseFloat(dm.dataset.siteLon)];
      pts.push(sp);
      L.circleMarker(sp, { radius: 6, fillColor: '#0f766e', color: '#fff', weight: 2, fillOpacity: 0.95 }).bindTooltip('Site ' + dm.dataset.siteLabel).addTo(m);
    }
    m.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom: 16 });
  }

  // ── Cell form: show the fields that apply to the chosen technology ──
  var tech = document.getElementById('id_technology');
  var fields = document.querySelectorAll('[data-techs]');
  if (tech && fields.length) {
    var apply = function () {
      fields.forEach(function (f) {
        var inp = f.querySelector('input,select');
        var has = inp && inp.value !== '';          // never hide a field that already holds a value
        f.hidden = !(f.dataset.techs.split(' ').indexOf(tech.value) !== -1 || !tech.value || has);
      });
    };
    tech.addEventListener('change', apply);
    apply();
  }

  // ── Cell list map ─────────────────────────────────────────────────
  var root = document.getElementById('dt-ce');
  var mapEl = document.getElementById('dt-ce-map');
  if (!root || !mapEl || !window.L) return;
  var emptyEl = document.getElementById('dt-ce-map-empty'), noteEl = document.getElementById('dt-ce-map-note');
  var params = new URLSearchParams(window.location.search);
  params.delete('page'); params.delete('per_page');
  var url = root.dataset.mapUrl + (params.toString() ? '?' + params.toString() : '');
  var markers = {}, map = null;

  fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (data) {
      var cells = data.features.filter(function (f) { return f.properties.ftype === 'cell'; });
      var sites = data.features.filter(function (f) { return f.properties.ftype === 'site'; });
      if (!cells.length) { mapEl.hidden = true; emptyEl.hidden = false; return; }
      map = L.map(mapEl);
      L.tileLayer(TILE, { attribution: 'Tiles &copy; Esri', maxZoom: 19 }).addTo(map);
      var bounds = [], overlays = {};
      var cl = L.layerGroup().addTo(map);
      cells.forEach(function (f) {
        var c = f.geometry.coordinates, p = f.properties;
        bounds.push([c[1], c[0]]);
        markers[p.id] = L.circleMarker([c[1], c[0]], { radius: 6, fillColor: p.active ? '#1a237e' : '#9ca3af', color: '#fff', weight: 1.5, fillOpacity: 0.9 })
          .bindPopup(function () {
            return popup(p.cell_id, p.url, [['Operator', p.operator], ['Technology', p.technology],
              ['Site', p.site + (p.sector ? ' / ' + p.sector : '')], ['Identifier', p.identifier], ['Band', p.band]]);
          }).addTo(cl);
      });
      overlays['Cells'] = cl;
      if (sites.length) {
        var sl = L.layerGroup();
        sites.forEach(function (f) {
          var c = f.geometry.coordinates, p = f.properties;
          L.circleMarker([c[1], c[0]], { radius: 5, fillColor: '#0f766e', color: '#fff', weight: 1.5, fillOpacity: 0.9 })
            .bindPopup(function () { return popup(p.site_id + ' — ' + p.name, p.url, [['Operator', p.operator]]); }).addTo(sl);
        });
        overlays['Sites'] = sl;
      }
      map.fitBounds(L.latLngBounds(bounds), { padding: [24, 24], maxZoom: 16 });
      L.control.layers(null, overlays, { collapsed: false }).addTo(map);
      noteEl.textContent = cells.length.toLocaleString() + ' mapped' + (data.truncated ? ' (first 5,000)' : '');
    })
    .catch(function () { mapEl.hidden = true; emptyEl.hidden = false; emptyEl.textContent = 'Cell map could not be loaded.'; });

  document.querySelectorAll('.dt-st-pin[data-cell]').forEach(function (b) {
    b.addEventListener('click', function () {
      var mk = markers[b.dataset.cell];
      if (!mk || !map) return;
      mapEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      map.setView(mk.getLatLng(), 16); mk.openPopup();
    });
  });
})();
