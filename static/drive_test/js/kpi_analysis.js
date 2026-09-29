(function () {
  var root = document.getElementById('dt-ka');
  if (!root) return;

  function readJsonScript(id) {
    var node = document.getElementById(id);
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (e) { return null; }
  }

  // Auto-submit the session selector on change — one click instead of pick + View.
  ['k-op', 'k-tech', 'k-sess'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', function () { el.form.submit(); });
  });

  // ── RSSI Distribution (bar) ─────────────────────────────────────────────
  (function renderRssiChart() {
    var canvas = document.getElementById('ka-rssi-chart');
    if (!canvas || !window.Chart) return;
    var dist = readJsonScript('ka-rssi-data');
    if (!dist) return;
    var order = [
      ['good', 'Excellent (≥ -85 dBm)', '#10b981'],
      ['fair', 'Good (≥ -95 dBm)', '#60a5fa'],
      ['poor', 'Fair (≥ -105 dBm)', '#d97706'],
      ['very_poor', 'Poor (< -105 dBm)', '#dc2626'],
      ['no_signal', 'No signal recorded', '#94a3b8'],
    ];
    var labels = order.map(function (o) { return o[1]; });
    var values = order.map(function (o) { return (dist[o[0]] && dist[o[0]].count) || 0; });
    var colors = order.map(function (o) { return o[2]; });
    if (!values.some(function (v) { return v > 0; })) return;
    new Chart(canvas, {
      type: 'bar',
      data: { labels: labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 3 }] },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, title: { display: true, text: 'Measurements' } } },
      },
    });
  })();

  // ── Technology Distribution (doughnut) ──────────────────────────────────
  (function renderTechChart() {
    var canvas = document.getElementById('ka-tech-chart');
    if (!canvas || !window.Chart) return;
    var rows = readJsonScript('ka-tech-data');
    if (!rows || !rows.length) return;
    new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: rows.map(function (r) { return r.technology; }),
        datasets: [{
          data: rows.map(function (r) { return r.count; }),
          backgroundColor: rows.map(function (r) { return r.color; }),
          borderWidth: 2, borderColor: '#ffffff',
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '68%',
        plugins: { legend: { display: false } },
      },
    });
  })();
})();
