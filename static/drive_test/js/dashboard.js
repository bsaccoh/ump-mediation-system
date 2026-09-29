/* ============================================================
   UMP DRIVE TEST DASHBOARD
   ============================================================ */

(function () {
    "use strict";

    const cfg = window.DRIVE_TEST_MAP_CONFIG || {};

    /* Read JSON embedded with Django's json_script tag */
    function readJsonScript(id) {
        try {
            const el = document.getElementById(id);
            return el ? JSON.parse(el.textContent) : null;
        } catch (e) {
            console.error("dashboard: failed to parse #" + id, e);
            return null;
        }
    }

    /* Shared technology color mapping (charts.js) — keyed by technology code, so
       a color always means the same technology on every Drive Test page, never
       just "whichever technology happens to sort first here". */
    const DTC = window.DriveTestCharts || null;
    const TECH_COLOURS = DTC ? DTC.TECH_COLORS : { '2G': '#7c3aed', '3G': '#0891b2', '4G': '#16a34a', '5G': '#d97706' };

    /* ========================================================
       CHART DEFAULTS
       ======================================================== */

    if (DTC) { DTC.applyDefaults(); } else if (window.Chart) {
        Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
        Chart.defaults.font.size = 11;
        Chart.defaults.color = "#738096";
        Chart.defaults.borderColor = "#e7ebf0";
    }

    /* ========================================================
       MEASUREMENT TREND
       ======================================================== */

    function showTrendEmpty(container, message) {
        container.innerHTML =
            '<div class="dt-empty-panel">' +
            '<i class="bi bi-bar-chart dt-empty-panel-icon"></i>' +
            '<div class="dt-empty-panel-title">No measurement data</div>' +
            '<div class="dt-empty-panel-sub">' + (message || 'No measurements recorded for this period.') + '</div>' +
            '</div>';
    }

    function renderTrendChart(container, trendData) {
        if (!window.Chart) return;
        const source = Array.isArray(trendData) ? trendData : [];
        const values = source.map(function (item) { return Number(item.measurements || 0); });
        const hasData = values.some(function (v) { return v > 0; });

        if (!hasData) {
            showTrendEmpty(container, 'No measurements found for this period.');
            return;
        }

        const labels = source.map(function (item) { return item.label; });

        container.innerHTML = '<canvas id="measurementTrendChart"></canvas>';
        const canvas = container.querySelector('#measurementTrendChart');

        new Chart(canvas, {
            type: "line",
            data: {
                labels: labels,
                datasets: [{
                    label: "Measurements",
                    data: values,
                    borderColor: "#1769e0",
                    backgroundColor: "rgba(23,105,224,.07)",
                    borderWidth: 2,
                    tension: 0.35,
                    pointRadius: 2,
                    pointHoverRadius: 4,
                    fill: true,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { intersect: false, mode: "index" },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        padding: 9,
                        displayColors: false,
                        callbacks: {
                            label: function (context) {
                                return " Measurements: " + Number(context.raw || 0).toLocaleString();
                            },
                        },
                    },
                },
                scales: {
                    x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
                    y: {
                        beginAtZero: true,
                        grid: { color: "#edf0f4" },
                        ticks: {
                            callback: function (value) {
                                return Number(value).toLocaleString();
                            },
                        },
                    },
                },
            },
        });
    }

    function initializeMeasurementChart() {
        const container = document.getElementById("trend-container");
        if (!container) return;

        const trendData = readJsonScript("dt-trend-data");
        renderTrendChart(container, trendData);
    }

    /* ========================================================
       TECHNOLOGY DISTRIBUTION
       ======================================================== */

    function initializeTechnologyChart() {
        const canvas = document.getElementById("technologyChart");
        if (!canvas || !window.Chart) return;

        const techData = readJsonScript("dt-tech-data");
        const source = Array.isArray(techData) ? techData : [];
        if (source.length === 0) return;  /* server-side empty state already rendered */

        const labels = source.map(function (item) { return item.technology; });
        const values = source.map(function (item) { return Number(item.measurements || 0); });

        const hasData = values.some(function (v) { return v > 0; });
        if (!hasData) return;

        new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: labels.map(function (t) { return TECH_COLOURS[t] || '#64748b'; }),
                    borderWidth: 2,
                    borderColor: "#ffffff",
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "68%",
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return " " + context.label + ": " + Number(context.raw || 0).toLocaleString();
                            },
                        },
                    },
                },
            },
        });

        /* Colour the legend dots — by the technology each row actually names,
           never by row position, so a color always means the same technology. */
        document.querySelectorAll('.dt-tech-dot').forEach(function (dot) {
            const tech = dot.getAttribute('data-tech');
            dot.style.background = TECH_COLOURS[tech] || '#64748b';
        });
    }

    /* ========================================================
       MAP
       ======================================================== */

    function initializeMap() {
        const element = document.getElementById("driveTestMap");
        if (!element || !window.L) return;

        /* Remove loading overlay */
        const loadingEl = document.getElementById("map-loading");
        if (loadingEl) loadingEl.remove();

        const tileUrl = cfg.tileUrl || '';
        const attribution = cfg.attribution || '';

        const map = L.map(element, { zoomControl: true, attributionControl: !!attribution });

        if (tileUrl) {
            L.tileLayer(tileUrl, {
                maxZoom: 19,
                attribution: attribution,
            }).addTo(map);
        } else {
            /* No basemap — show data-only note */
            const note = document.createElement('div');
            note.className = 'dt-map-no-basemap';
            note.textContent = 'No basemap configured — showing data points only';
            element.appendChild(note);
            /* Grey canvas background already set via CSS */
        }

        const dataUrl = cfg.dataUrl;
        if (!dataUrl) {
            map.setView([8.46, -13.23], 8);
            return;
        }

        fetch(dataUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (data) {
                const sigColour = {
                    green: "#22c55e",
                    yellow: "#eab308",
                    orange: "#f97316",
                    red: "#ef4444",
                    grey: "#9ca3af",
                };

                const layer = L.geoJSON(data, {
                    pointToLayer: function (feature, latlng) {
                        const colour = sigColour[feature.properties.colour] || sigColour.grey;
                        return L.circleMarker(latlng, {
                            radius: 4,
                            fillColor: colour,
                            color: "#fff",
                            weight: 1,
                            opacity: 0.9,
                            fillOpacity: 0.8,
                        });
                    },
                    onEachFeature: function (feature, mapLayer) {
                        const p = feature.properties || {};
                        if (p.ftype === "measurement") {
                            mapLayer.bindTooltip(
                                (p.tech || "—") + " · RSSI " + (p.rssi != null ? p.rssi.toFixed(1) + " dBm" : "—"),
                                { direction: "top", className: "dt-map-tip" }
                            );
                        } else if (p.ftype === "session_marker") {
                            mapLayer.bindTooltip(p.label || "Session", { direction: "top" });
                        }
                    },
                }).addTo(map);

                if (layer.getBounds().isValid()) {
                    map.fitBounds(layer.getBounds(), { padding: [20, 20] });
                } else {
                    map.setView([8.46, -13.23], 8);
                }
            })
            .catch(function (error) {
                console.error("Drive Test dashboard map error:", error);
                map.setView([8.46, -13.23], 8);
            });
    }

    /* ========================================================
       PERIOD FILTER — real AJAX call
       ======================================================== */

    function initializePeriodFilter() {
        const select = document.getElementById("measurementPeriod");
        if (!select) return;

        select.addEventListener("change", function () {
            const days = this.value;
            const trendUrl = cfg.trendUrl;
            if (!trendUrl) return;

            const container = document.getElementById("trend-container");
            if (container) {
                container.innerHTML =
                    '<div class="dt-empty-panel"><div class="spinner-border spinner-border-sm text-muted"></div></div>';
            }

            fetch(trendUrl + "?days=" + encodeURIComponent(days), {
                headers: { "X-Requested-With": "XMLHttpRequest" },
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("HTTP " + response.status);
                    return response.json();
                })
                .then(function (data) {
                    if (container) renderTrendChart(container, data.trend || []);
                })
                .catch(function (error) {
                    console.error("Dashboard trend fetch failed:", error);
                    if (container) {
                        showTrendEmpty(container, 'Could not load trend data. Please try again.');
                    }
                });
        });
    }

    /* ========================================================
       INITIALIZE
       ======================================================== */

    document.addEventListener("DOMContentLoaded", function () {
        initializeMeasurementChart();
        initializeTechnologyChart();
        initializeMap();
        initializePeriodFilter();
    });

})();
