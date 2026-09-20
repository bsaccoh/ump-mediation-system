/**
 * NatCA Dashboard JavaScript
 *
 * Initializes charts and interactivity for the NatCA Regulatory Dashboard.
 */

document.addEventListener("DOMContentLoaded", function () {
    initTrafficTrend();
    initOperatorTraffic();
    initServiceContribution();
    initFilters();
});


function parseJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); }
    catch (e) { console.error("Failed to parse " + id, e); return null; }
}


var CHART_COLORS = ["#1678ef", "#16ad6a", "#ff852f", "#8749da", "#ec3c47", "#98a7b9"];

if (typeof Chart !== "undefined") {
    Chart.defaults.font.family = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    Chart.defaults.color = "#536b83";
}


/**
 * Traffic Trend Line Chart
 */
function initTrafficTrend() {
    var canvas = document.getElementById("trafficTrendChart");
    if (!canvas) return;

    var data = parseJSON("traffic-trend-data");
    if (!data || !data.labels || !data.labels.length) return;

    new Chart(canvas, {
        type: "line",
        data: {
            labels: data.labels,
            datasets: [
                {
                    label: "Voice (Mins)",
                    data: data.voice,
                    borderColor: CHART_COLORS[0],
                    backgroundColor: CHART_COLORS[0],
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 3,
                    fill: false
                },
                {
                    label: "SMS (Count)",
                    data: data.sms,
                    borderColor: CHART_COLORS[1],
                    backgroundColor: CHART_COLORS[1],
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 3,
                    fill: false
                },
                {
                    label: "Data (GB)",
                    data: data.data,
                    borderColor: CHART_COLORS[2],
                    backgroundColor: CHART_COLORS[2],
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 3,
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    position: "bottom",
                    labels: { usePointStyle: true, boxWidth: 8, padding: 15, font: { size: 11 } }
                },
                tooltip: {
                    backgroundColor: "rgba(0,0,0,0.8)",
                    titleFont: { size: 12 }, bodyFont: { size: 11 },
                    padding: 10, cornerRadius: 4
                }
            },
            scales: {
                y: { beginAtZero: true, grid: { color: "#edf1f5" }, ticks: { font: { size: 10 } } },
                x: { grid: { color: "#f1f3f5" }, ticks: { font: { size: 10 } } }
            }
        }
    });
}


/**
 * Traffic by Operator Donut Chart
 */
function initOperatorTraffic() {
    var canvas = document.getElementById("operatorTrafficChart");
    if (!canvas) return;

    var source = parseJSON("operator-traffic-data");
    if (!source || !source.length) return;

    var labels = source.map(function (i) { return i.name; });
    var values = source.map(function (i) { return i.percentage; });

    new Chart(canvas, {
        type: "doughnut",
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: CHART_COLORS,
                borderWidth: 2, borderColor: "#ffffff", hoverOffset: 3
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: "64%",
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "rgba(0,0,0,0.8)",
                    titleFont: { size: 12 }, bodyFont: { size: 11 },
                    padding: 10, cornerRadius: 4,
                    callbacks: {
                        label: function (ctx) { return ctx.label + ": " + ctx.parsed + "%"; }
                    }
                }
            }
        }
    });

    var legend = document.getElementById("operatorLegend");
    if (legend) {
        legend.innerHTML = "";
        labels.forEach(function (label, idx) {
            var row = document.createElement("div");
            row.classList.add("legend-item");
            row.innerHTML =
                '<span class="legend-dot" style="background:' + CHART_COLORS[idx % CHART_COLORS.length] + '"></span>' +
                '<span>' + label + '</span>' +
                '<span class="legend-value">' + values[idx] + '%</span>';
            legend.appendChild(row);
        });
    }
}


/**
 * Service Contribution Bar Chart
 */
function initServiceContribution() {
    var canvas = document.getElementById("serviceContributionChart");
    if (!canvas) return;

    var data = parseJSON("service-contribution-data");
    if (!data || !data.labels) return;

    new Chart(canvas, {
        type: "bar",
        data: {
            labels: data.labels,
            datasets: [{
                label: "Records",
                data: data.values,
                backgroundColor: CHART_COLORS,
                borderRadius: 4, borderWidth: 0
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "rgba(0,0,0,0.8)",
                    titleFont: { size: 12 }, bodyFont: { size: 11 },
                    padding: 10, cornerRadius: 4,
                    callbacks: {
                        label: function (ctx) {
                            var v = ctx.parsed.y;
                            if (v >= 1000000) return (v / 1000000).toFixed(1) + "M records";
                            if (v >= 1000) return (v / 1000).toFixed(1) + "K records";
                            return v + " records";
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: "#edf1f5" },
                    ticks: {
                        font: { size: 10 },
                        callback: function (v) {
                            if (v >= 1000000) return (v / 1000000).toFixed(0) + "M";
                            if (v >= 1000) return (v / 1000).toFixed(0) + "K";
                            return v;
                        }
                    }
                },
                x: { grid: { display: false }, ticks: { font: { size: 10 } } }
            }
        }
    });
}


/**
 * Filter Handlers
 */
function initFilters() {
    var operatorFilter = document.getElementById("operatorFilter");
    if (operatorFilter) {
        operatorFilter.addEventListener("change", function () {
            applyFilters({ operator: this.value });
        });
    }

    var periodButtons = document.querySelectorAll(".period-btn");
    periodButtons.forEach(function (btn) {
        btn.addEventListener("click", function () {
            var period = this.dataset.period;
            if (period) applyFilters({ period: period, trend_granularity: "" });
        });
    });

    var trendSelect = document.getElementById("trendGranularity");
    if (trendSelect) {
        trendSelect.addEventListener("change", function () {
            applyFilters({ trend_granularity: this.value });
        });
    }

    var refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", function () { location.reload(); });
    }
}


function applyFilters(overrides) {
    var params = new URLSearchParams(window.location.search);

    if (overrides.operator !== undefined) {
        if (overrides.operator) params.set("operator", overrides.operator);
        else params.delete("operator");
    }

    if (overrides.period !== undefined) {
        if (overrides.period && overrides.period !== "all") {
            params.set("period", overrides.period);
        } else {
            params.delete("period");
        }
    }

    if (overrides.trend_granularity !== undefined) {
        if (overrides.trend_granularity && overrides.trend_granularity !== "monthly") {
            params.set("trend_granularity", overrides.trend_granularity);
        } else {
            params.delete("trend_granularity");
        }
    }

    window.location.search = params.toString();
}
