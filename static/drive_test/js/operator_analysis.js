(function () {
  var root = document.getElementById('dt-oa');
  if (!root) return;

  // Shared with every other Drive Test page (charts.js) so a technology's color
  // never changes depending on which page you're looking at.
  var DTC = window.DriveTestCharts || null;
  var TECH_COLOURS = DTC ? DTC.TECH_COLORS : { '2G': '#7c3aed', '3G': '#0891b2', '4G': '#16a34a', '5G': '#d97706' };
  var PALETTE = DTC ? DTC.PALETTE : ['#1a237e', '#0891b2', '#16a34a', '#d97706', '#dc2626', '#7c3aed'];

  function readJsonScript(id) {
    var node = document.getElementById(id);
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (e) { return null; }
  }

  // Keep the active tab across a full page reload (filter/metric form submits).
  var tabInput = document.getElementById('oa-tab-input');
  document.querySelectorAll('#oa-tabs button[data-tab]').forEach(function (btn) {
    btn.addEventListener('shown.bs.tab', function () {
      if (tabInput) tabInput.value = btn.dataset.tab;
    });
  });

  // Metric selector updates the chart/matrix on change (server-rendered — no KPI math in JS).
  var metricSelect = document.getElementById('oa-metric');
  if (metricSelect) metricSelect.addEventListener('change', function () { metricSelect.form.submit(); });

  // ── Technology Distribution (doughnut) ──────────────────────────────────
  (function () {
    var canvas = document.getElementById('oa-tech-dist-chart');
    if (!canvas || !window.Chart) return;
    var rows = readJsonScript('oa-tech-dist-data');
    if (!rows || !rows.length) return;
    new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: rows.map(function (r) { return r.technology; }),
        datasets: [{
          data: rows.map(function (r) { return r.measurements; }),
          backgroundColor: rows.map(function (r) { return TECH_COLOURS[r.technology] || '#64748b'; }),
          borderWidth: 2, borderColor: '#ffffff',
        }],
      },
      options: { responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { display: false } } },
    });
  })();

  // ── Measurements by Operator (doughnut) ─────────────────────────────────
  (function () {
    var canvas = document.getElementById('oa-op-dist-chart');
    if (!canvas || !window.Chart) return;
    var rows = readJsonScript('oa-op-dist-data');
    if (!rows || !rows.length) return;
    new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: rows.map(function (r) { return r.name; }),
        datasets: [{
          data: rows.map(function (r) { return r.measurements; }),
          backgroundColor: rows.map(function (_r, i) { return PALETTE[i % PALETTE.length]; }),
          borderWidth: 2, borderColor: '#ffffff',
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '55%',
        plugins: { legend: { position: 'right', labels: { boxWidth: 10, font: { size: 10 } } } },
      },
    });
  })();

  // ── Selected metric by Operator (bar) — descriptive only, no ranking colour ──
  (function () {
    var canvas = document.getElementById('oa-op-metric-chart');
    if (!canvas || !window.Chart) return;
    var rows = readJsonScript('oa-op-metric-data');
    if (!rows || !rows.length) return;
    var labels = rows.map(function (r) { return r.label; });
    var values = rows.map(function (r) { return r.value; });
    if (!values.some(function (v) { return v !== null && v !== undefined; })) return;
    new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{ data: values, backgroundColor: labels.map(function (_l, i) { return PALETTE[i % PALETTE.length]; }), borderRadius: 3 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  })();
})();
