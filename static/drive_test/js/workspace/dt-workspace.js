/*
 * Drive Test workspace — the controller.
 *
 * The ONLY stateful module. Owns the fetched columns and the cursor, and is the
 * only thing allowed to mutate them. Panes are pure subscribers: they read
 * event.detail, never each other, and call a mutator on user interaction.
 * Strictly unidirectional — intent up, state down.
 *
 * The DOM is the event bus. With no build step and no ES modules, a shared
 * state object would have to live on window; dispatching CustomEvents on the
 * workspace root is inspectable in devtools and keeps every pane a standalone
 * script, exactly like the existing files.
 *
 * Events, all dispatched on #dt-workspace:
 *   dt:data-loaded  { data }
 *   dt:cursor       { index, sample }
 *   dt:selection    { from, to }        indices, or null to clear
 *   dt:metric       { metric }
 *   dt:error        { message }
 *
 * Three rules keep this from rotting:
 *   1. Mutators are idempotent and re-entrancy guarded. Map pan -> cursor ->
 *      map fit is the obvious infinite loop.
 *   2. dt:cursor is rAF-coalesced; hovering a chart fires continuously and the
 *      map must not redraw per event.
 *   3. Every event carries an INDEX, never a timestamp, so no pane re-searches
 *      for the nearest point.
 */
(function () {
  'use strict';

  var root = document.getElementById('dt-workspace');
  if (!root) return;

  var DT = window.DT;
  var config = DT.readJsonScript('dt-workspace-config') || {};

  var state = {
    sessionRef: config.sessionRef || '',
    data: null,
    cursorIndex: null,
    selection: null,
    activeMetric: 'rsrp'
  };

  var emitting = false;      // re-entrancy guard
  var pendingCursor = undefined;  // coalescing; undefined = nothing queued
  var cursorFrame = null;
  var cursorTimer = null;

  function emit(name, detail) {
    if (emitting) {
      // A subscriber called a mutator while handling an event. Defer so the
      // current dispatch completes first, rather than recursing.
      setTimeout(function () { emit(name, detail); }, 0);
      return;
    }
    emitting = true;
    try {
      root.dispatchEvent(new CustomEvent(name, { detail: detail }));
    } finally {
      emitting = false;
    }
  }

  /* ── Read-only accessors ─────────────────────────────────────────── */

  var api = {
    root: root,
    config: config,

    getData: function () { return state.data; },
    getMetric: function () { return state.activeMetric; },
    getCursor: function () { return state.cursorIndex; },
    getSelection: function () { return state.selection; },

    /** Every column for one index, assembled into a single object. */
    sampleAt: function (index) {
      var data = state.data;
      if (!data || index === null || index === undefined) return null;
      if (index < 0 || index >= data.t.length) return null;

      var sample = {
        index: index,
        id: data.ids[index],
        t: data.t[index],
        lat: data.lat[index],
        lon: data.lon[index],
        tech: data.tech[index],
        cell: data.cell[index],
        metrics: {}
      };
      Object.keys(data.series).forEach(function (key) {
        sample.metrics[key] = data.series[key][index];
      });
      return sample;
    },

    /** Metrics present in this session's data, in declared order. */
    availableMetrics: function () {
      return (state.data && state.data.meta.available_metrics) || [];
    },

    /** Metrics with no values in this session — rendered as absent, not zero. */
    absentMetrics: function () {
      return (state.data && state.data.meta.absent_metrics) || [];
    },

    /** Metrics the source FORMAT cannot carry at all, a subset of absent. */
    unsupportedMetrics: function () {
      return (state.data && state.data.meta.unsupported_metrics) || [];
    }
  };

  /* ── Mutators — the only way state changes ───────────────────────── */

  api.setCursor = function (index) {
    if (!state.data) return;
    if (index !== null && index !== undefined) {
      index = Math.max(0, Math.min(index | 0, state.data.t.length - 1));
    }
    if (index === state.cursorIndex) return;   // idempotent

    state.cursorIndex = index;
    pendingCursor = index;
    scheduleCursor();
  };

  /* Coalesce cursor updates to one per frame — hovering a chart fires
     continuously and the map must not redraw per event.
     requestAnimationFrame does NOT run in a hidden tab, so a timer backs it up:
     without that, opening the workspace in a background tab (ctrl-click from
     the session list) leaves every pane empty until the tab is focused. */
  function scheduleCursor() {
    if (cursorFrame !== null || cursorTimer !== null) return;

    if (typeof requestAnimationFrame === 'function') {
      cursorFrame = requestAnimationFrame(deliverCursor);
    }
    cursorTimer = setTimeout(deliverCursor, 32);
  }

  function deliverCursor() {
    if (cursorFrame !== null) {
      cancelAnimationFrame(cursorFrame);
      cursorFrame = null;
    }
    if (cursorTimer !== null) {
      clearTimeout(cursorTimer);
      cursorTimer = null;
    }
    if (pendingCursor === undefined) return;

    var at = pendingCursor;
    pendingCursor = undefined;
    emit('dt:cursor', { index: at, sample: api.sampleAt(at) });
  }

  api.setSelection = function (from, to) {
    if (from === null || to === null || from === undefined || to === undefined) {
      if (state.selection === null) return;
      state.selection = null;
      emit('dt:selection', { from: null, to: null });
      return;
    }
    var lo = Math.min(from, to);
    var hi = Math.max(from, to);
    if (state.selection && state.selection.from === lo && state.selection.to === hi) return;

    state.selection = { from: lo, to: hi };
    emit('dt:selection', { from: lo, to: hi });
  };

  api.setMetric = function (metric) {
    if (!metric || metric === state.activeMetric) return;
    state.activeMetric = metric;
    emit('dt:metric', { metric: metric });
  };

  /* ── Loading ─────────────────────────────────────────────────────── */

  api.load = function () {
    var url = config.timeseriesUrl;
    if (!url) return Promise.resolve(null);

    root.setAttribute('data-loading', '1');
    return DT.getJson(url)
      .then(function (data) {
        state.data = data;

        // Prefer the requested metric, but fall back to whatever this session
        // actually carries rather than rendering an empty chart.
        var available = data.meta.available_metrics || [];
        if (available.length && available.indexOf(state.activeMetric) === -1) {
          state.activeMetric = data.meta.primary_metric || available[0];
        }

        root.removeAttribute('data-loading');
        emit('dt:data-loaded', { data: data });
        emit('dt:metric', { metric: state.activeMetric });

        if (data.t.length) api.setCursor(0);
        return data;
      })
      .catch(function (err) {
        console.error('DT workspace: load failed', err);
        root.removeAttribute('data-loading');
        root.setAttribute('data-error', '1');
        emit('dt:error', { message: 'Unable to load measurement data.' });
      });
  };

  /* Subscribe helper so panes do not repeat addEventListener boilerplate. */
  api.on = function (name, handler) {
    root.addEventListener(name, handler);
  };

  window.DTWorkspace = api;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { api.load(); });
  } else {
    api.load();
  }
}());
