/*
 * Workspace detail pane — every column for the sample under the cursor.
 *
 * Renders an em dash for anything absent. Missing is never zero.
 */
(function () {
  'use strict';

  var host = document.getElementById('dt-ws-detail');
  if (!host || !window.DTWorkspace) return;

  var DT = window.DT;
  var WS = window.DTWorkspace;

  function row(parent, label, value, colour) {
    var item = DT.el('div', 'dt-ws-detail-row');
    item.appendChild(DT.el('dt', null, label));
    var dd = DT.el('dd', null, value);
    if (colour) dd.style.color = colour;
    item.appendChild(dd);
    parent.appendChild(item);
  }

  function render(sample) {
    DT.clear(host);

    if (!sample) {
      host.appendChild(DT.el('p', 'dt-ws-empty',
        'Select a point on the map or a chart to inspect it.'));
      return;
    }

    var list = DT.el('dl', 'dt-ws-detail-list');

    row(list, 'Time', DT.fmtClock(sample.t));
    row(list, 'Technology', DT.techLabel(sample.tech), DT.techColour(sample.tech));
    row(list, 'Position',
      (sample.lat === null || sample.lon === null)
        ? DT.DASH
        : sample.lat.toFixed(5) + ', ' + sample.lon.toFixed(5));
    row(list, 'Serving cell', sample.cell === null ? DT.DASH : '#' + sample.cell);

    host.appendChild(list);

    var metrics = DT.el('dl', 'dt-ws-detail-list');
    var keys = Object.keys(sample.metrics);
    if (!keys.length) {
      metrics.appendChild(DT.el('p', 'dt-ws-empty', 'No radio data at this sample.'));
    }
    keys.forEach(function (key) {
      var value = sample.metrics[key];
      row(metrics, DT.metric(key).label, DT.formatMetric(key, value),
          value === null || value === undefined ? null : DT.metricColour(key, value));
    });
    host.appendChild(metrics);

    if (sample.id) {
      var link = DT.el('a', 'dt-ws-detail-link', 'Open measurement →');
      link.href = (WS.config.detailUrlBase || '/drive-test/measurements/') + sample.id + '/';
      host.appendChild(link);
    }
  }

  WS.on('dt:cursor', function (e) { render(e.detail.sample); });
  WS.on('dt:data-loaded', function () { render(WS.sampleAt(WS.getCursor())); });

  render(null);
}());
