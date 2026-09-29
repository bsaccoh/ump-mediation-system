/*
 * Workspace chart pane — stacked metric charts on a shared X axis.
 *
 * Every chart is driven by the same index domain as the map, so a cursor at
 * index i means the same sample everywhere. Dragging selects a range and calls
 * setSelection; hovering calls setCursor.
 */
(function () {
  'use strict';

  var host = document.getElementById('dt-ws-charts');
  if (!host || !window.Chart || !window.DTWorkspace) return;

  var DT = window.DT;
  var WS = window.DTWorkspace;

  var charts = [];
  var selfDriven = false;

  /* Draws the cursor line and the selection band across every chart. */
  var overlayPlugin = {
    id: 'dtOverlay',
    afterDatasetsDraw: function (chart) {
      var ctx = chart.ctx;
      var area = chart.chartArea;
      var xScale = chart.scales.x;
      if (!area || !xScale) return;

      var selection = WS.getSelection();
      if (selection) {
        var left = xScale.getPixelForValue(selection.from);
        var right = xScale.getPixelForValue(selection.to);
        ctx.save();
        ctx.fillStyle = 'rgba(11, 95, 176, 0.12)';
        ctx.fillRect(Math.min(left, right), area.top,
                     Math.abs(right - left), area.bottom - area.top);
        ctx.restore();
      }

      var index = WS.getCursor();
      if (index === null || index === undefined) return;
      var x = xScale.getPixelForValue(index);
      if (x < area.left || x > area.right) return;

      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.lineWidth = 1;
      ctx.strokeStyle = '#0b5fb0';
      ctx.stroke();
      ctx.restore();
    }
  };

  function buildChart(metric) {
    var data = WS.getData();
    var spec = DT.metric(metric);

    var panel = DT.el('div', 'dt-ws-chart');
    var head = DT.el('div', 'dt-ws-chart-head');
    head.appendChild(DT.el('span', 'dt-ws-chart-title',
      spec.label + (spec.unit ? ' (' + spec.unit + ')' : '')));
    var readout = DT.el('span', 'dt-ws-chart-value', DT.DASH);
    head.appendChild(readout);
    panel.appendChild(head);

    var canvasWrap = DT.el('div', 'dt-ws-chart-canvas');
    var canvas = document.createElement('canvas');
    canvasWrap.appendChild(canvas);
    panel.appendChild(canvasWrap);
    host.appendChild(panel);

    var values = data.series[metric] || [];
    // parsing:false is the fast path, but it requires {x, y} points rather than
    // raw values — a bare null in the array makes Chart.js read `.x` of null.
    var points = values.map(function (value, i) { return { x: i, y: value }; });

    var chart = new Chart(canvas, {
      type: 'line',
      data: {
        datasets: [{
          data: points,
          borderColor: '#0b5fb0',
          backgroundColor: 'rgba(11, 95, 176, 0.10)',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          spanGaps: false,
          tension: 0.15
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        normalized: true,
        parsing: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { type: 'linear', display: false, min: 0, max: Math.max(0, data.t.length - 1) },
          y: {
            ticks: { font: { size: 10 }, maxTicksLimit: 4 },
            grid: { color: 'rgba(148, 163, 184, 0.18)' }
          }
        }
      },
      plugins: [overlayPlugin]
    });

    chart.__metric = metric;
    chart.__readout = readout;
    attachInteraction(canvas, chart);
    charts.push(chart);
  }

  function indexFromEvent(chart, event) {
    var rect = chart.canvas.getBoundingClientRect();
    var x = event.clientX - rect.left;
    var value = chart.scales.x.getValueForPixel(x);
    if (value === undefined || value === null || isNaN(value)) return null;
    var data = WS.getData();
    return Math.max(0, Math.min(Math.round(value), data.t.length - 1));
  }

  function attachInteraction(canvas, chart) {
    var dragFrom = null;

    canvas.addEventListener('mousemove', function (event) {
      var index = indexFromEvent(chart, event);
      if (index === null) return;
      if (dragFrom !== null) {
        selfDriven = true;
        WS.setSelection(dragFrom, index);
        selfDriven = false;
      }
      selfDriven = true;
      WS.setCursor(index);
      selfDriven = false;
    });

    canvas.addEventListener('mousedown', function (event) {
      dragFrom = indexFromEvent(chart, event);
    });

    window.addEventListener('mouseup', function () {
      dragFrom = null;
    });

    canvas.addEventListener('dblclick', function () {
      WS.setSelection(null, null);
    });
  }

  function rebuild() {
    charts.forEach(function (chart) { chart.destroy(); });
    charts = [];
    DT.clear(host);

    var data = WS.getData();
    if (!data || !data.t.length) {
      host.appendChild(DT.el('div', 'dt-ws-empty',
        'No measurements are available for this session.'));
      return;
    }

    // The active metric first, then a few companions that this session has.
    var available = WS.availableMetrics();
    var active = WS.getMetric();
    var order = [active].concat(
      ['sinr', 'rsrq', 'dl_kbps', 'speed'].filter(function (m) {
        return m !== active && available.indexOf(m) !== -1;
      })
    );

    order.slice(0, 4).forEach(function (metric) {
      if (available.indexOf(metric) !== -1) buildChart(metric);
    });

    var absent = WS.absentMetrics();
    if (absent.length) {
      var note = DT.el('div', 'dt-ws-absent');
      note.appendChild(DT.el('strong', null, 'Not available in source data: '));
      note.appendChild(DT.el('span', null, absent.map(function (m) {
        return DT.metric(m).label;
      }).join(', ')));
      host.appendChild(note);
    }
  }

  function updateReadouts(sample) {
    charts.forEach(function (chart) {
      var value = sample ? sample.metrics[chart.__metric] : null;
      chart.__readout.textContent = DT.formatMetric(chart.__metric, value);
      chart.draw();
    });
  }

  WS.on('dt:data-loaded', rebuild);
  WS.on('dt:metric', rebuild);
  WS.on('dt:cursor', function (e) { updateReadouts(e.detail.sample); });
  WS.on('dt:selection', function () {
    charts.forEach(function (chart) { chart.draw(); });
  });
  WS.on('dt:error', function (e) {
    DT.clear(host);
    host.appendChild(DT.el('div', 'dt-ws-empty', e.detail.message));
  });
}());
