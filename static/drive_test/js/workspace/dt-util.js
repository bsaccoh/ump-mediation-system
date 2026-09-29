/*
 * Drive Test workspace — shared utilities and palette.
 *
 * The single home for helpers that were copy-pasted across seven scripts
 * (el, getJson, readJsonScript), and the ONE source of truth for colour.
 *
 * Colour lives here rather than only in CSS because Chart.js and Leaflet need
 * real values, not custom properties. Three different 4G colours exist in the
 * older scripts; everything new reads from this file.
 *
 * Load first. Exposes window.DT.
 */
(function () {
  'use strict';

  var DT = window.DT || (window.DT = {});

  /* ── DOM ─────────────────────────────────────────────────────────── */

  DT.el = function (tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };

  DT.clear = function (node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
    return node;
  };

  DT.readJsonScript = function (id) {
    var node = document.getElementById(id);
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (err) {
      console.error('DT: bad JSON in #' + id, err);
      return null;
    }
  };

  DT.getJson = function (url) {
    return fetch(url, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'same-origin'
    }).then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status + ' for ' + url);
      return response.json();
    });
  };

  /* ── Formatting ──────────────────────────────────────────────────── */

  /* Missing is never zero. Everything absent renders as an em dash. */
  DT.DASH = '—';

  DT.fmt = function (value, digits, unit) {
    if (value === null || value === undefined || value === '') return DT.DASH;
    if (typeof value === 'number' && !isFinite(value)) return DT.DASH;
    var out = (typeof value === 'number' && digits !== undefined)
      ? value.toFixed(digits)
      : String(value);
    return unit ? out + ' ' + unit : out;
  };

  DT.fmtClock = function (ms) {
    if (ms === null || ms === undefined) return DT.DASH;
    var total = Math.max(0, Math.round(ms / 1000));
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return (h > 0 ? h + ':' : '') + pad(m) + ':' + pad(s);
  };

  /* ── Palette, read from the CSS tokens ─────────────────────────────
   * Colour is DEFINED ONCE, in design-system.css, and read here. Charts and
   * Leaflet need real values rather than custom properties, so without this
   * bridge the palette would have to exist twice and drift — which is how
   * three different 4G colours ended up in the older scripts.
   *
   * Reading the tokens also means a theme change needs no JS change: swap the
   * tokens and call DT.refreshPalette().
   * ----------------------------------------------------------------- */

  function token(name, fallback) {
    try {
      var value = getComputedStyle(document.documentElement)
        .getPropertyValue(name).trim();
      return value || fallback;
    } catch (err) {
      return fallback;
    }
  }

  DT.TECH = {};
  DT.SEVERITY = {};
  var SIGNAL_RAMP = [];

  DT.refreshPalette = function () {
    DT.TECH = {
      0: { label: DT.DASH, colour: token('--dt-neutral', '#94a3b8') },
      1: { label: '2G', colour: token('--dt-tech-2g', '#7c3aed') },
      2: { label: '3G', colour: token('--dt-tech-3g', '#0891b2') },
      3: { label: '4G', colour: token('--dt-tech-4g', '#2563eb') },
      4: { label: '5G', colour: token('--dt-tech-5g', '#d97706') }
    };

    DT.SEVERITY = {
      CRITICAL: token('--dt-sev-critical', '#b91c1c'),
      HIGH:     token('--dt-sev-high', '#c2410c'),
      MEDIUM:   token('--dt-sev-medium', '#a16207'),
      LOW:      token('--dt-sev-low', '#1d4ed8'),
      INFO:     token('--dt-sev-info', '#64748b')
    };

    SIGNAL_RAMP = [
      token('--dt-signal-1', '#dc2626'),
      token('--dt-signal-2', '#ea580c'),
      token('--dt-signal-3', '#eab308'),
      token('--dt-signal-4', '#16a34a'),
      token('--dt-signal-5', '#1d4ed8')
    ];

    // Metric specs hold a ramp reference; repoint them at the new values.
    Object.keys(DT.METRICS || {}).forEach(function (key) {
      var spec = DT.METRICS[key];
      spec.ramp = spec.reversed ? SIGNAL_RAMP.slice().reverse() : SIGNAL_RAMP;
    });
  };

  DT.techLabel = function (code) { return (DT.TECH[code] || DT.TECH[0]).label; };
  DT.techColour = function (code) { return (DT.TECH[code] || DT.TECH[0]).colour; };
  DT.severityColour = function (name) { return DT.SEVERITY[name] || DT.SEVERITY.INFO; };

  /* ── Metric scales ───────────────────────────────────────────────────
   * Bin edges are ordered worst → best, matching the drive-test convention.
   * `unit` and `digits` drive every readout so a metric is formatted the same
   * way in the chart, the map popup and the detail pane.
   * ------------------------------------------------------------------ */

  /* `reversed: true` means LOWER is better, so the ramp is flipped. RxQual is
     the case that matters: 0 is perfect and 7 is unusable, the opposite of
     every other quality metric here. Getting this wrong makes unusable 2G
     coverage render as excellent. */
  DT.METRICS = {
    rsrp:     { label: 'RSRP',     unit: 'dBm',    digits: 1, bins: [-110, -100, -90, -80] },
    rsrq:     { label: 'RSRQ',     unit: 'dB',     digits: 1, bins: [-20, -15, -12, -9] },
    sinr:     { label: 'SINR',     unit: 'dB',     digits: 1, bins: [0, 5, 13, 20] },
    rssi:     { label: 'RSSI',     unit: 'dBm',    digits: 1, bins: [-95, -85, -75, -65] },
    rscp:     { label: 'RSCP',     unit: 'dBm',    digits: 1, bins: [-100, -90, -85, -75] },
    ecio:     { label: 'Ec/Io',    unit: 'dB',     digits: 1, bins: [-16, -12, -9, -6] },
    cqi:      { label: 'CQI',      unit: '',       digits: 0, bins: [3, 6, 9, 12] },
    ss_rsrp:  { label: 'SS-RSRP',  unit: 'dBm',    digits: 1, bins: [-110, -100, -90, -80] },
    ss_rsrq:  { label: 'SS-RSRQ',  unit: 'dB',     digits: 1, bins: [-20, -15, -12, -9] },
    ss_sinr:  { label: 'SS-SINR',  unit: 'dB',     digits: 1, bins: [0, 5, 13, 20] },
    rxqual:   { label: 'RxQual',   unit: '',       digits: 0, bins: [1, 3, 5, 6], reversed: true },
    c_over_i: { label: 'C/I',      unit: 'dB',     digits: 1, bins: [3, 6, 9, 12] },
    dl_kbps:  { label: 'DL',       unit: 'kbps',   digits: 0, bins: [128, 1024, 5120, 20480] },
    ul_kbps:  { label: 'UL',       unit: 'kbps',   digits: 0, bins: [64, 512, 2048, 10240] },
    speed:    { label: 'Speed',    unit: 'km/h',   digits: 1, bins: [5, 20, 50, 80] },
    heading:  { label: 'Heading',  unit: '°', digits: 0, bins: [90, 180, 270, 360] },
    altitude: { label: 'Altitude', unit: 'm',      digits: 0, bins: [50, 150, 300, 600] }
  };

  DT.refreshPalette();

  DT.metric = function (key) {
    return DT.METRICS[key] ||
      { label: key, unit: '', digits: 2, bins: [], ramp: SIGNAL_RAMP };
  };

  DT.metricColour = function (key, value) {
    if (value === null || value === undefined) return '#cbd5e1';
    var spec = DT.metric(key);
    var bins = spec.bins || [];
    for (var i = 0; i < bins.length; i++) {
      if (value < bins[i]) return spec.ramp[i];
    }
    return spec.ramp[spec.ramp.length - 1];
  };

  DT.formatMetric = function (key, value) {
    var spec = DT.metric(key);
    return DT.fmt(value, spec.digits, spec.unit);
  };

  /* Legend rows for the active metric, worst → best. */
  DT.legendRows = function (key) {
    var spec = DT.metric(key);
    var bins = spec.bins || [];
    var rows = [];
    for (var i = 0; i <= bins.length; i++) {
      var label;
      if (i === 0) label = '< ' + bins[0];
      else if (i === bins.length) label = '≥ ' + bins[bins.length - 1];
      else label = bins[i - 1] + ' – ' + bins[i];
      rows.push({ colour: spec.ramp[i], label: label });
    }
    return rows;
  };
}());
