document.addEventListener("DOMContentLoaded", function () {
    initGSTTrend();
    initTaxableRevenue();
    initRevenueComparison();
    initGSTVariance();
    initFilters();
});

function getJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); }
    catch (e) { console.error("JSON parse error for " + id, e); return null; }
}

var COLORS = {
    blue: "#1687f8",
    green: "#19b86a",
    red: "#f13a43",
    orange: "#ff8c14",
    yellow: "#f5ba00",
    darkGreen: "#17b76d",
    purple: "#9955f7"
};

function autoScale(val) {
    var abs = Math.abs(val);
    if (abs >= 1e9)  return { v: val / 1e9,  u: "B" };
    if (abs >= 1e6)  return { v: val / 1e6,  u: "M" };
    if (abs >= 1e3)  return { v: val / 1e3,  u: "K" };
    return { v: val, u: "" };
}

function formatSLE(val) {
    var s = autoScale(val);
    return "SLE " + s.v.toFixed(2) + (s.u ? " " + s.u : "");
}

function yAxisLabel(dataArrays) {
    var maxVal = 0;
    dataArrays.forEach(function (arr) {
        arr.forEach(function (v) { if (Math.abs(v) > maxVal) maxVal = Math.abs(v); });
    });
    var s = autoScale(maxVal);
    return "Amount (SLE" + (s.u ? " " + s.u : "") + ")";
}

if (typeof Chart !== "undefined") {
    Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
    Chart.defaults.color = "#536b83";
}

/* ============ GST TREND ============ */

function initGSTTrend() {
    var canvas = document.getElementById("gstTrendChart");
    if (!canvas) return;
    var src = getJSON("gst-trend-data");
    if (!src) return;

    new Chart(canvas, {
        data: {
            labels: src.labels,
            datasets: [
                {
                    type: "bar",
                    label: "Expected GST",
                    data: src.expected,
                    backgroundColor: COLORS.blue,
                    borderRadius: 3,
                    maxBarThickness: 40,
                    order: 2
                },
                {
                    type: "bar",
                    label: "Declared GST",
                    data: src.declared,
                    backgroundColor: COLORS.green,
                    borderRadius: 3,
                    maxBarThickness: 40,
                    order: 2
                },
                {
                    type: "line",
                    label: "Variance",
                    data: src.variance,
                    borderColor: COLORS.red,
                    backgroundColor: COLORS.red,
                    borderWidth: 2,
                    tension: 0.35,
                    pointRadius: 3,
                    order: 1
                }
            ]
        },
        options: chartOpts({ yTitle: yAxisLabel([src.expected, src.declared, src.variance]) })
    });
}

/* ============ TAXABLE REVENUE DONUT ============ */

function initTaxableRevenue() {
    var canvas = document.getElementById("taxableRevenueChart");
    if (!canvas) return;
    var src = getJSON("taxable-revenue-data");
    if (!src) return;

    var labels = src.map(function (d) { return d.operator; });
    var values = src.map(function (d) { return d.percentage; });
    var amounts = src.map(function (d) { return d.amount; });
    var palette = [COLORS.orange, COLORS.blue, COLORS.yellow, COLORS.darkGreen, COLORS.purple];

    var total = amounts.reduce(function (a, b) { return a + b; }, 0);
    var donutEl = document.getElementById("donutTotalValue");
    if (donutEl) donutEl.textContent = formatSLE(total);

    new Chart(canvas, {
        type: "doughnut",
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: palette,
                borderWidth: 2,
                borderColor: "#ffffff",
                hoverOffset: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: "64%",
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "rgba(0,0,0,0.8)",
                    callbacks: {
                        label: function (ctx) {
                            return ctx.label + ": " + ctx.parsed + "%";
                        }
                    }
                }
            }
        }
    });

    var legendEl = document.getElementById("revenueDonutLegend");
    if (legendEl) {
        legendEl.innerHTML = "";
        labels.forEach(function (name, i) {
            var row = document.createElement("div");
            row.className = "legend-row";
            row.innerHTML =
                '<span class="legend-dot" style="background:' + palette[i % palette.length] + '"></span>' +
                '<span class="legend-name">' + name + '</span>' +
                '<span class="legend-pct">' + values[i] + '%</span>' +
                '<span class="legend-amt">' + formatSLE(amounts[i]) + '</span>';
            legendEl.appendChild(row);
        });
    }
}

/* ============ REVENUE COMPARISON ============ */

function initRevenueComparison() {
    var canvas = document.getElementById("revenueComparisonChart");
    if (!canvas) return;
    var src = getJSON("revenue-comparison-data");
    if (!src) return;

    new Chart(canvas, {
        type: "bar",
        data: {
            labels: src.labels,
            datasets: [
                {
                    label: "Expected Revenue",
                    data: src.expected,
                    backgroundColor: COLORS.blue,
                    borderRadius: 3,
                    maxBarThickness: 50
                },
                {
                    label: "Declared Revenue",
                    data: src.declared,
                    backgroundColor: COLORS.green,
                    borderRadius: 3,
                    maxBarThickness: 50
                }
            ]
        },
        options: chartOpts({ yTitle: yAxisLabel([src.expected, src.declared]) })
    });
}

/* ============ GST VARIANCE ============ */

function initGSTVariance() {
    var canvas = document.getElementById("gstVarianceChart");
    if (!canvas) return;
    var src = getJSON("gst-variance-data");
    if (!src) return;

    new Chart(canvas, {
        type: "bar",
        data: {
            labels: src.labels,
            datasets: [
                {
                    label: "Expected GST",
                    data: src.expected,
                    backgroundColor: COLORS.blue,
                    borderRadius: 3,
                    maxBarThickness: 60
                },
                {
                    label: "Declared GST",
                    data: src.declared,
                    backgroundColor: COLORS.green,
                    borderRadius: 3,
                    maxBarThickness: 60
                }
            ]
        },
        options: chartOpts({ yTitle: yAxisLabel([src.expected, src.declared]) })
    });
}

/* ============ SHARED OPTIONS ============ */

function chartOpts(cfg) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
            legend: {
                position: "bottom",
                labels: { usePointStyle: true, boxWidth: 8, padding: 12, font: { size: 11 } }
            },
            tooltip: {
                backgroundColor: "rgba(0,0,0,0.8)",
                titleFont: { size: 12 },
                bodyFont: { size: 11 },
                padding: 10,
                cornerRadius: 4
            }
        },
        scales: {
            y: {
                beginAtZero: true,
                title: cfg && cfg.yTitle ? { display: true, text: cfg.yTitle, font: { size: 10 }, color: "#8795aa" } : undefined,
                grid: { color: "#edf1f5" },
                ticks: { font: { size: 10 } }
            },
            x: {
                grid: { display: false },
                ticks: { font: { size: 10 } }
            }
        }
    };
}

/* ============ FILTERS ============ */

function initFilters() {
    var refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", function () {
            applyFilters();
        });
    }

    var opFilter = document.getElementById("operatorFilter");
    if (opFilter) {
        opFilter.addEventListener("change", function () { applyFilters(); });
    }

    var svcFilter = document.getElementById("serviceFilter");
    if (svcFilter) {
        svcFilter.addEventListener("change", function () { applyFilters(); });
    }
}

function applyFilters() {
    var params = new URLSearchParams();
    var op = document.getElementById("operatorFilter");
    var svc = document.getElementById("serviceFilter");
    var sd = document.querySelector('[name="start_date"]');
    var ed = document.querySelector('[name="end_date"]');

    if (op && op.value) params.set("operator", op.value);
    if (svc && svc.value) params.set("service", svc.value);
    if (sd && sd.value) params.set("start_date", sd.value);
    if (ed && ed.value) params.set("end_date", ed.value);

    var qs = params.toString();
    window.location.search = qs;
}
