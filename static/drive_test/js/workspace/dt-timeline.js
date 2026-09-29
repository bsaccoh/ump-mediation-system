/*
 * Workspace event timeline.
 *
 * Events are positioned on the same index domain as the charts, so clicking a
 * marker moves every pane to that sample. Events are never decimated — dropping
 * one would hide the thing the engineer opened the session to find.
 */
(function () {
  'use strict';

  var host = document.getElementById('dt-ws-timeline');
  if (!host || !window.DTWorkspace) return;

  var DT = window.DT;
  var WS = window.DTWorkspace;

  var track = null;
  var cursorLine = null;

  function render() {
    DT.clear(host);
    var data = WS.getData();
    if (!data || !data.t.length) return;

    var bar = DT.el('div', 'dt-ws-timeline-bar');

    track = DT.el('div', 'dt-ws-timeline-track');
    bar.appendChild(track);

    cursorLine = DT.el('div', 'dt-ws-timeline-cursor');
    track.appendChild(cursorLine);

    var span = Math.max(1, data.t.length - 1);

    if (!data.events.length) {
      track.appendChild(DT.el('span', 'dt-ws-timeline-none',
        'No events were recorded for this session.'));
    }

    data.events.forEach(function (ev) {
      if (ev.idx === null || ev.idx === undefined) return;
      var marker = DT.el('button', 'dt-ws-event');
      marker.type = 'button';
      marker.style.left = ((ev.idx / span) * 100) + '%';
      marker.style.background = DT.severityColour(ev.severity);
      marker.title = ev.type + (ev.label ? ' — ' + ev.label : '') +
                     ' @ ' + DT.fmtClock(ev.t);
      marker.setAttribute('aria-label', marker.title);
      marker.addEventListener('click', function () { WS.setCursor(ev.idx); });
      track.appendChild(marker);
    });

    // Clicking anywhere on the track scrubs.
    track.addEventListener('click', function (event) {
      if (event.target !== track) return;
      var rect = track.getBoundingClientRect();
      var ratio = (event.clientX - rect.left) / rect.width;
      WS.setCursor(Math.round(ratio * span));
    });

    host.appendChild(bar);

    var footer = DT.el('div', 'dt-ws-timeline-meta');
    footer.appendChild(DT.el('span', null,
      data.events.length + ' event' + (data.events.length === 1 ? '' : 's')));
    footer.appendChild(DT.el('span', 'dt-ws-clock', DT.fmtClock(0)));
    var meta = data.meta;
    footer.appendChild(DT.el('span', null,
      meta.decimated
        ? meta.returned.toLocaleString() + ' of ' + meta.source_count.toLocaleString() + ' samples shown'
        : meta.source_count.toLocaleString() + ' samples'));
    host.appendChild(footer);
  }

  function moveCursor(index, sample) {
    var data = WS.getData();
    if (!data || !cursorLine) return;
    var span = Math.max(1, data.t.length - 1);
    cursorLine.style.left = ((index / span) * 100) + '%';

    var clock = host.querySelector('.dt-ws-clock');
    if (clock && sample) clock.textContent = DT.fmtClock(sample.t);
  }

  WS.on('dt:data-loaded', render);
  WS.on('dt:cursor', function (e) { moveCursor(e.detail.index, e.detail.sample); });
}());
