/* Drive Test Charts — shared helpers so every page's Chart.js code uses the same
 * technology color mapping and the same empty-state markup, instead of each page
 * defining (and sometimes contradicting) its own. This file computes nothing —
 * every value passed in still comes from the server; it only standardises how
 * charts are drawn and styled.
 */
window.DriveTestCharts = (function () {
  'use strict';

  // The one technology → color mapping reused on every chart/badge/legend across
  // the whole module (dashboard, operator analysis, GIS, session detail, ...).
  // Keyed by technology code, never by array position, so a color always means
  // the same technology no matter which page or how the rows happen to be sorted.
  var TECH_COLORS = { '2G': '#7c3aed', '3G': '#0891b2', '4G': '#16a34a', '5G': '#d97706' };
  var PALETTE = ['#1a237e', '#0891b2', '#16a34a', '#d97706', '#dc2626', '#7c3aed', '#0f766e', '#be185d'];

  function techColor(tech) {
    return TECH_COLORS[tech] || '#64748b';
  }

  function paletteColor(i) {
    return PALETTE[i % PALETTE.length];
  }

  function readJsonScript(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function applyDefaults() {
    if (!window.Chart) return;
    Chart.defaults.font.family = "'Segoe UI', system-ui, -apple-system, sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = '#64748b';
    Chart.defaults.borderColor = '#e5e8f0';
  }

  /** Renders the standard "no data" panel in place of an empty chart — never an
   * empty canvas. `reason` distinguishes "no data" from "not supported" etc. */
  function emptyState(container, title, reason) {
    if (!container) return;
    container.innerHTML =
      '<div class="dt-chart-empty"><i class="bi bi-bar-chart"></i>' +
      '<div>' + (title || 'No data available') + '</div>' +
      (reason ? '<div style="font-size:0.74rem;margin-top:4px">' + reason + '</div>' : '') +
      '</div>';
  }

  return {
    TECH_COLORS: TECH_COLORS,
    PALETTE: PALETTE,
    techColor: techColor,
    paletteColor: paletteColor,
    readJsonScript: readJsonScript,
    applyDefaults: applyDefaults,
    emptyState: emptyState,
  };
})();
