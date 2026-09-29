(function () {
  var root = document.getElementById('dt-dq');
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
  function readJsonScript(id) {
    var node = $(id);
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (e) { return null; }
  }
  // Missing stays missing: null/undefined is shown as '—', never as 0.
  function num(v) { return (v === null || v === undefined) ? '—' : Number(v).toLocaleString(); }

  // ── Measurement Quality chart ──────────────────────────────────────────
  (function renderChart() {
    var canvas = $('dq-chart');
    if (!canvas || !window.Chart) return;
    var data = readJsonScript('dq-chart-data');
    if (!data) return;
    var labels = ['Valid', 'Invalid', 'Without GPS', 'Unmatched'];
    var values = [data.valid || 0, data.invalid || 0, data.without_gps || 0, data.unmatched || 0];
    if (!values.some(function (v) { return v > 0; })) return;
    new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: ['#10b981', '#dc2626', '#94a3b8', '#d97706'],
          borderWidth: 2, borderColor: '#ffffff',
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '68%',
        plugins: { legend: { display: false } },
      },
    });
  })();

  // ── Session detail drawer ───────────────────────────────────────────────
  var modalEl = $('dq-modal');
  var modal = null;
  var bodyEl = $('dq-modal-body');
  var titleEl = $('dq-modal-title');
  var sessionLink = $('dq-modal-session');

  function apiUrl(sessionRef) {
    return cfg.apiTemplate.replace('SESSION_REF', encodeURIComponent(sessionRef));
  }

  function renderIssues(issues) {
    if (!issues || !issues.length) return el('div', 'dt-st-muted', 'No data-quality issues detected for this session.');
    var table = el('table', 'dt-sl-table');
    var thead = el('thead');
    var hr = el('tr');
    ['Issue', 'Category', 'Records', '% of Records'].forEach(function (h) { hr.appendChild(el('th', null, h)); });
    thead.appendChild(hr); table.appendChild(thead);
    var tbody = el('tbody');
    issues.forEach(function (i) {
      var tr = el('tr');
      tr.appendChild(el('td', null, i.issue));
      tr.appendChild(el('td', null, i.category));
      tr.appendChild(el('td', 'dt-sl-num', num(i.count)));
      tr.appendChild(el('td', 'dt-sl-num', i.pct != null ? i.pct + '%' : '—'));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function renderFiles(files) {
    var wrap = el('div');
    if (!files || !files.length) { wrap.appendChild(el('div', 'dt-st-muted', 'No files attached.')); return wrap; }
    var table = el('table', 'dt-sl-table');
    var thead = el('thead'); var hr = el('tr');
    ['File', 'Status', 'Parser', 'Records', 'Valid', 'Invalid', 'Matched', 'Unmatched', 'Overall'].forEach(function (h) {
      hr.appendChild(el('th', null, h));
    });
    thead.appendChild(hr); table.appendChild(thead);
    var tbody = el('tbody');
    files.forEach(function (f) {
      var tr = el('tr');
      tr.appendChild(el('td', 'wrap', f.filename));
      tr.appendChild(el('td', null, f.status_label || f.status));
      tr.appendChild(el('td', null, f.parser || '—'));
      var q = f.quality;
      ['total', 'valid', 'invalid', 'matched', 'unmatched'].forEach(function (k) {
        tr.appendChild(el('td', 'dt-sl-num', q ? num(q[k]) : '—'));
      });
      tr.appendChild(el('td', 'dt-sl-num', q && q.overall_score != null ? q.overall_score + '%' : '—'));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function renderCategory(cat) {
    var grid = el('dl', 'dt-sd-list');
    function row(label, value) {
      grid.appendChild(el('dt', null, label));
      grid.appendChild(el('dd', null, num(value)));
    }
    row('Total measurements', cat.measurement.total);
    row('Valid', cat.measurement.valid);
    row('Invalid', cat.measurement.invalid);
    row('With GPS', cat.location.with_gps);
    row('Without GPS', cat.location.without_gps);
    row('Invalid coordinates', cat.location.invalid_coords);
    row('Missing technology', cat.measurement.missing_technology);
    row('Missing radio data', cat.measurement.missing_radio);
    row('Matched', cat.reference.matched);
    row('Unmatched', cat.reference.unmatched);
    row('Unknown (pending)', cat.reference.unknown);
    return grid;
  }

  function openDrawer(sessionRef) {
    titleEl.textContent = 'Data Quality — ' + sessionRef;
    bodyEl.innerHTML = ''; bodyEl.appendChild(el('div', 'dt-st-muted', 'Loading…'));
    sessionLink.hidden = true;
    if (!modal) modal = new bootstrap.Modal(modalEl);
    modal.show();

    getJson(apiUrl(sessionRef)).then(function (d) {
      bodyEl.innerHTML = '';
      bodyEl.appendChild(el('div', 'dt-sd-sectiontitle', 'Category Breakdown'));
      bodyEl.appendChild(renderCategory(d.category));
      bodyEl.appendChild(el('div', 'dt-sd-sectiontitle', 'Quality Issues'));
      bodyEl.appendChild(renderIssues(d.issues));
      bodyEl.appendChild(el('div', 'dt-sd-sectiontitle', 'Files'));
      var scroll = el('div', 'dt-sl-scroll'); scroll.appendChild(renderFiles(d.files));
      bodyEl.appendChild(scroll);

      var links = el('div', 'dt-dq-legend');
      [['Measurements', d.measurements_url], ['Cell Matching', d.cell_matching_url], ['GIS / Map', d.gis_url]]
        .forEach(function (pair) {
          var a = el('a', 'dt-sl-link', pair[0]);
          a.href = pair[1];
          var wrap = el('div'); wrap.appendChild(a); links.appendChild(wrap);
        });
      bodyEl.appendChild(links);

      sessionLink.href = d.session_url;
      sessionLink.hidden = false;
    }).catch(function () {
      bodyEl.innerHTML = '';
      bodyEl.appendChild(el('div', 'dt-st-muted', 'Could not load data-quality details for this session. Please try again.'));
    });
  }

  root.addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('.dt-dq-view') : null;
    if (!btn) return;
    openDrawer(btn.dataset.session);
  });
})();
