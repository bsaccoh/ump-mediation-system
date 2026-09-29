/* Processing Monitor — auto-refresh while there are active (in-progress) jobs.
   Server-rendered page; this only reloads it, it never recomputes state client-side. */
(function () {
  var root = document.getElementById('dt-pm');
  if (!root) return;
  var hasActive = root.getAttribute('data-has-active') === '1';
  if (!hasActive) return;

  var toggle = document.getElementById('dt-pm-autorefresh-toggle');
  var timer = null;
  var INTERVAL_MS = 8000;

  function start() {
    if (timer) return;
    timer = setInterval(function () { window.location.reload(); }, INTERVAL_MS);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  if (toggle) {
    toggle.addEventListener('change', function () {
      if (toggle.checked) start(); else stop();
    });
    if (toggle.checked) start();
  } else {
    start();
  }

  // ── Processing Duration — real durations of completed jobs on this page only ──
  (function () {
    var canvas = document.getElementById('dt-pm-duration-chart');
    if (!canvas || !window.Chart) return;
    var DTC = window.DriveTestCharts;
    var rows = DTC ? DTC.readJsonScript('dt-pm-duration-data') : null;
    if (!rows || !rows.length) {
      canvas.closest('div').hidden = true;
      var empty = document.getElementById('dt-pm-duration-empty');
      if (empty) empty.hidden = false;
      return;
    }
    if (DTC) DTC.applyDefaults();
    new Chart(canvas, {
      type: 'bar',
      data: {
        labels: rows.map(function (r) { return r.label; }),
        datasets: [{
          label: 'Duration (s)', data: rows.map(function (r) { return r.seconds; }),
          backgroundColor: '#1a237e', borderRadius: 4, maxBarThickness: 22,
        }],
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) { return ctx.raw + 's'; } } } },
        scales: { x: { beginAtZero: true, ticks: { callback: function (v) { return v + 's'; } } } },
      },
    });
  })();
})();
