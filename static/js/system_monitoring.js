/**
 * UMP System Monitoring Controller
 * Handles Chart.js instances, SVG circular gauges, time-series range filtering,
 * auto-refresh polling, and interactive diagnostic modals.
 */
document.addEventListener("DOMContentLoaded", function () {
    // 1. Chart.js Global Configuration
    Chart.defaults.font.family = "'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = "#64748B";

    let processingTrendChart = null;
    let processingTimeChart = null;
    let successRateChart = null;
    let hardwareHistoryChart = null;
    let alarmsSeverityChart = null;
    let alarmSummaryChart = null;

    let currentRange = "24h";

    // Mediation Flow Monitoring Variables
    let flowCollectionChart = null;
    let flowProcessingChart = null;
    let flowDistributionChart = null;
    let flowColGroup = "portal";
    let flowProcGroup = "stream";
    let flowDistGroup = "downstream";
    let flowCurrentRange = "24h";
    let flowProcRawItems = [];
    let flowDistRawItems = [];

    // Modals
    const healthModalEl = document.getElementById("healthDetailsModal");
    const healthModal = healthModalEl ? new bootstrap.Modal(healthModalEl) : null;
    const btnHealthDetails = document.getElementById("btnViewHealthDetails");
    if (btnHealthDetails && healthModal) {
        btnHealthDetails.addEventListener("click", () => healthModal.show());
    }

    const errModalEl = document.getElementById("errorDetailModal");
    const errModal = errModalEl ? new bootstrap.Modal(errModalEl) : null;

    // Refresh Button & Icon
    const refreshBtn = document.getElementById("refreshMonitoringBtn");
    const refreshIcon = document.getElementById("refreshMonIcon");

    // 2. Circular Gauge SVG Renderer
    function renderCircularGauge(containerId, percent, strokeColor) {
        const wrap = document.getElementById(containerId);
        if (!wrap) return;

        const size = 68;
        const radius = 27;
        const strokeWidth = 5;
        const circumference = 2 * Math.PI * radius; // ~169.64
        const safePct = Math.min(100, Math.max(0, percent || 0));
        const offset = circumference - (safePct / 100) * circumference;

        wrap.innerHTML = `
            <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" style="transform: rotate(-90deg);">
                <!-- Track -->
                <circle cx="${size/2}" cy="${size/2}" r="${radius}" fill="none" stroke="#EDF2F7" stroke-width="${strokeWidth}" />
                <!-- Progress Arc -->
                <circle cx="${size/2}" cy="${size/2}" r="${radius}" fill="none" stroke="${strokeColor}" stroke-width="${strokeWidth}"
                    stroke-linecap="round" stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"
                    style="transition: stroke-dashoffset 0.8s ease;" />
            </svg>
            <span class="ump-mon-gauge-text">${safePct}%</span>
        `;
    }

    // 3. Initialize Charts
    function initCharts() {
        // Chart 1: File Processing Trend
        const ctxTrend = document.getElementById("processingTrendChart");
        if (ctxTrend) {
            processingTrendChart = new Chart(ctxTrend, {
                type: "line",
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: "Collected",
                            data: [],
                            borderColor: "#1677EE",
                            backgroundColor: "#1677EE",
                            borderWidth: 2,
                            tension: 0.35,
                            pointRadius: 2.5,
                            pointHoverRadius: 5,
                        },
                        {
                            label: "Decoded",
                            data: [],
                            borderColor: "#16A34A",
                            backgroundColor: "#16A34A",
                            borderWidth: 2,
                            tension: 0.35,
                            pointRadius: 2.5,
                            pointHoverRadius: 5,
                        },
                        {
                            label: "Distributed",
                            data: [],
                            borderColor: "#FF8A1F",
                            backgroundColor: "#FF8A1F",
                            borderWidth: 2,
                            tension: 0.35,
                            pointRadius: 2.5,
                            pointHoverRadius: 5,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: { mode: "index", intersect: false }
                    },
                    scales: {
                        x: { grid: { display: false } },
                        y: {
                            beginAtZero: true,
                            grid: { color: "#F1F5F9" },
                            ticks: { precision: 0 }
                        }
                    }
                }
            });
        }

        // Chart 2: Processing Time (Minutes)
        const ctxTime = document.getElementById("processingTimeChart");
        if (ctxTime) {
            const ctx2d = ctxTime.getContext("2d");
            const grad = ctx2d.createLinearGradient(0, 0, 0, 180);
            grad.addColorStop(0, "rgba(22, 119, 238, 0.22)");
            grad.addColorStop(1, "rgba(22, 119, 238, 0.0)");

            processingTimeChart = new Chart(ctxTime, {
                type: "line",
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: "Minutes",
                            data: [],
                            borderColor: "#1677EE",
                            backgroundColor: grad,
                            borderWidth: 2,
                            fill: true,
                            tension: 0.35,
                            pointRadius: 3,
                            pointBackgroundColor: "#1677EE",
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: (item) => `${item.parsed.y} min`
                            }
                        }
                    },
                    scales: {
                        x: { grid: { display: false } },
                        y: {
                            beginAtZero: true,
                            grid: { color: "#F1F5F9" },
                            ticks: {
                                callback: (v) => `${v}m`
                            }
                        }
                    }
                }
            });
        }

        // Chart 3: File Success Rate Doughnut
        const ctxSuccess = document.getElementById("successRateChart");
        if (ctxSuccess) {
            successRateChart = new Chart(ctxSuccess, {
                type: "doughnut",
                data: {
                    labels: ["Successful", "Failed", "Skipped"],
                    datasets: [
                        {
                            data: [1, 0, 0],
                            backgroundColor: ["#16A34A", "#EF4444", "#94A3B8"],
                            borderWidth: 0,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "78%",
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: (item) => ` ${item.label}: ${item.parsed.toLocaleString()} files`
                            }
                        }
                    }
                }
            });
        }

        // Chart 4: Hardware History
        const ctxHw = document.getElementById("hardwareHistoryChart");
        if (ctxHw) {
            hardwareHistoryChart = new Chart(ctxHw, {
                type: "line",
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: "CPU",
                            data: [],
                            borderColor: "#16A34A",
                            borderWidth: 1.8,
                            tension: 0.35,
                            pointRadius: 2,
                        },
                        {
                            label: "Memory",
                            data: [],
                            borderColor: "#1677EE",
                            borderWidth: 1.8,
                            tension: 0.35,
                            pointRadius: 2,
                        },
                        {
                            label: "Disk",
                            data: [],
                            borderColor: "#FF8A1F",
                            borderWidth: 1.8,
                            tension: 0.35,
                            pointRadius: 2,
                        },
                        {
                            label: "Network",
                            data: [],
                            borderColor: "#8B5CF6",
                            borderWidth: 1.8,
                            tension: 0.35,
                            pointRadius: 2,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: { grid: { display: false } },
                        y: {
                            min: 0,
                            max: 100,
                            grid: { color: "#F1F5F9" },
                            ticks: {
                                callback: (v) => `${v}%`,
                                stepSize: 25
                            }
                        }
                    }
                }
            });
        }

        // Chart 5: Alarms by Severity Doughnut
        const ctxSev = document.getElementById("alarmsSeverityChart");
        if (ctxSev) {
            alarmsSeverityChart = new Chart(ctxSev, {
                type: "doughnut",
                data: {
                    labels: ["Critical", "Major", "Minor", "Warning"],
                    datasets: [
                        {
                            data: [0, 0, 0, 0],
                            backgroundColor: ["#EF4444", "#FF8A1F", "#F4B400", "#3488F4"],
                            borderWidth: 0,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "75%",
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }

        // Chart 6: Alarm Summary Stacked Bar
        const ctxSummary = document.getElementById("alarmSummaryChart");
        if (ctxSummary) {
            alarmSummaryChart = new Chart(ctxSummary, {
                type: "bar",
                data: {
                    labels: [],
                    datasets: [
                        { label: "Critical", data: [], backgroundColor: "#EF4444", stack: "s" },
                        { label: "Major", data: [], backgroundColor: "#FF8A1F", stack: "s" },
                        { label: "Minor", data: [], backgroundColor: "#F4B400", stack: "s" },
                        { label: "Warning", data: [], backgroundColor: "#3488F4", stack: "s" }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false } },
                        y: {
                            stacked: true,
                            beginAtZero: true,
                            grid: { color: "#F1F5F9" },
                            ticks: { precision: 0 }
                        }
                    }
                }
            });
        }

        // Initialize Mediation Flow Monitoring Dual-Axis Combination Charts
        initFlowCharts();
    }

    // 4. Fetch Live Overview Data
    async function fetchOverview() {
        try {
            const resp = await fetch(`/monitoring/api/overview/?range=${currentRange}`, {
                headers: { "X-Requested-With": "XMLHttpRequest" }
            });
            if (!resp.ok) throw new Error("Overview API status " + resp.status);
            const data = await resp.json();

            // Status Badge
            const dot = document.getElementById("systemStatusDot");
            const text = document.getElementById("systemStatusText");
            if (dot && data.health) dot.style.background = data.health.overall_color;
            if (text && data.health) text.textContent = data.health.overall_text;

            // Top 5 KPIs
            if (data.kpis) {
                const kp = data.kpis;
                if (kp.total_files_processed) {
                    document.getElementById("kpiTotalProcessed").textContent = kp.total_files_processed.value;
                    const el = document.getElementById("kpiTotalProcessedDelta");
                    el.textContent = kp.total_files_processed.delta;
                    el.className = `ump-mon-kpi-delta ${kp.total_files_processed.delta_class}`;
                }
                if (kp.files_decoded) {
                    document.getElementById("kpiFilesDecoded").textContent = kp.files_decoded.value;
                    const el = document.getElementById("kpiFilesDecodedDelta");
                    el.textContent = kp.files_decoded.delta;
                    el.className = `ump-mon-kpi-delta ${kp.files_decoded.delta_class}`;
                }
                if (kp.files_distributed) {
                    document.getElementById("kpiFilesDistributed").textContent = kp.files_distributed.value;
                    const el = document.getElementById("kpiFilesDistributedDelta");
                    el.textContent = kp.files_distributed.delta;
                    el.className = `ump-mon-kpi-delta ${kp.files_distributed.delta_class}`;
                }
                if (kp.avg_processing_time) {
                    document.getElementById("kpiAvgDuration").textContent = kp.avg_processing_time.value;
                    const el = document.getElementById("kpiAvgDurationDelta");
                    el.textContent = kp.avg_processing_time.delta;
                    el.className = `ump-mon-kpi-delta ${kp.avg_processing_time.delta_class}`;
                }
                if (kp.active_alarms) {
                    const alarmsEl = document.getElementById("kpiActiveAlarms");
                    alarmsEl.textContent = kp.active_alarms.value;
                    alarmsEl.className = `ump-mon-kpi-val ${kp.active_alarms.raw > 0 ? 'text-danger' : ''}`;
                    const el = document.getElementById("kpiActiveAlarmsDelta");
                    el.textContent = kp.active_alarms.delta;
                    el.className = `ump-mon-kpi-delta ${kp.active_alarms.delta_class}`;
                }
            }

            // Circular Hardware Gauges
            if (data.gauges) {
                renderCircularGauge("gaugeCpuWrap", data.gauges.cpu.percent, data.gauges.cpu.color);
                renderCircularGauge("gaugeMemWrap", data.gauges.memory.percent, data.gauges.memory.color);
                renderCircularGauge("gaugeDiskWrap", data.gauges.disk.percent, data.gauges.disk.color);
                renderCircularGauge("gaugeNetWrap", data.gauges.network.percent, data.gauges.network.color);
            }

            // System Health Rows
            if (data.health && Array.isArray(data.health.components)) {
                const list = document.getElementById("systemHealthList");
                if (list) {
                    list.innerHTML = data.health.components.map(c => `
                        <div class="ump-mon-health-row">
                            <div class="ump-mon-health-name">
                                <span class="ump-mon-status-dot ${c.badge_color === 'danger' ? 'bg-danger' : (c.badge_color === 'warning' ? 'bg-warning' : 'bg-success')}"></span>
                                <span>${c.name}</span>
                            </div>
                            <div>
                                <span class="ump-mon-health-badge ${c.badge_color}">${c.badge}</span>
                            </div>
                            <div class="ump-mon-health-detail">${c.detail}</div>
                        </div>
                    `).join("");
                }
            }

            // Recent Processing Activity Table
            if (Array.isArray(data.recent_activity)) {
                const tbody = document.getElementById("recentActivityTbody");
                if (tbody) {
                    if (data.recent_activity.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-4">No recent processing activity in selected period.</td></tr>`;
                    } else {
                        tbody.innerHTML = data.recent_activity.map(a => `
                            <tr>
                                <td class="fw-semibold text-truncate" style="max-width: 170px;" title="${a.filename}">${a.filename}</td>
                                <td>${a.source}</td>
                                <td><span class="badge bg-light text-dark border">${a.decoder}</span></td>
                                <td><span class="ump-pill-${a.status_color}">${a.status}</span></td>
                                <td class="font-monospace">${a.records}</td>
                                <td class="text-muted small">${a.start_time}</td>
                                <td class="text-muted small">${a.duration}</td>
                            </tr>
                        `).join("");
                    }
                }
            }

            // Recent Errors Table
            if (Array.isArray(data.recent_errors)) {
                const badge = document.getElementById("recentErrorsBadge");
                if (badge) badge.textContent = data.recent_errors_count || 0;

                const tbody = document.getElementById("recentErrorsTbody");
                if (tbody) {
                    if (data.recent_errors.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="4" class="text-center text-muted py-4">No recent errors reported. System functioning normally.</td></tr>`;
                    } else {
                        tbody.innerHTML = data.recent_errors.map(e => `
                            <tr class="error-row-clickable" data-error-id="${e.id}" style="cursor: pointer;" title="Click for diagnostic detail">
                                <td class="text-muted small text-nowrap">${e.time}</td>
                                <td><span class="fw-semibold text-dark">${e.component}</span></td>
                                <td><span class="ump-sev-${e.severity.toLowerCase()}">${e.severity}</span></td>
                                <td class="text-truncate" style="max-width: 200px;">${e.message}</td>
                            </tr>
                        `).join("");
                        bindErrorClicks();
                    }
                }
            }
        } catch (err) {
            console.error("Failed to fetch monitoring overview:", err);
        }
    }

    // 5. Fetch Timeseries Chart Data
    async function fetchTimeseries(range) {
        try {
            const resp = await fetch(`/monitoring/api/timeseries/?range=${range}`, {
                headers: { "X-Requested-With": "XMLHttpRequest" }
            });
            if (!resp.ok) throw new Error("Timeseries status " + resp.status);
            const data = await resp.json();

            // Update Chart 1: Processing Trend
            if (processingTrendChart && data.processing_trend) {
                processingTrendChart.data.labels = data.labels;
                processingTrendChart.data.datasets[0].data = data.processing_trend.collected;
                processingTrendChart.data.datasets[1].data = data.processing_trend.decoded;
                processingTrendChart.data.datasets[2].data = data.processing_trend.distributed;
                processingTrendChart.update();
            }

            // Update Chart 2: Processing Time
            if (processingTimeChart && data.processing_time) {
                processingTimeChart.data.labels = data.labels;
                processingTimeChart.data.datasets[0].data = data.processing_time.durations;
                processingTimeChart.update();
            }

            // Update Chart 3: Success Rate Doughnut
            if (successRateChart && data.success_rate) {
                successRateChart.data.datasets[0].data = [
                    data.success_rate.successful,
                    data.success_rate.failed,
                    data.success_rate.skipped
                ];
                successRateChart.update();

                const pctEl = document.getElementById("successRateCenterPct");
                if (pctEl) pctEl.textContent = `${data.success_rate.percent}%`;
                document.getElementById("successCountVal").textContent = data.success_rate.successful.toLocaleString();
                document.getElementById("failedCountVal").textContent = data.success_rate.failed.toLocaleString();
                document.getElementById("skippedCountVal").textContent = data.success_rate.skipped.toLocaleString();
            }

            // Update Chart 4: Hardware History
            if (hardwareHistoryChart && data.hardware_trend) {
                hardwareHistoryChart.data.labels = data.labels;
                hardwareHistoryChart.data.datasets[0].data = data.hardware_trend.cpu;
                hardwareHistoryChart.data.datasets[1].data = data.hardware_trend.memory;
                hardwareHistoryChart.data.datasets[2].data = data.hardware_trend.disk;
                hardwareHistoryChart.data.datasets[3].data = data.hardware_trend.network;
                hardwareHistoryChart.update();
            }

            // Update Chart 5: Alarms Severity Doughnut
            if (alarmsSeverityChart && data.alarms_by_severity) {
                const as = data.alarms_by_severity;
                alarmsSeverityChart.data.datasets[0].data = [as.critical, as.major, as.minor, as.warning];
                alarmsSeverityChart.update();

                document.getElementById("activeAlarmsCenterCount").textContent = as.total;
                document.getElementById("sevCritCount").textContent = as.critical;
                document.getElementById("sevMajorCount").textContent = as.major;
                document.getElementById("sevMinorCount").textContent = as.minor;
                document.getElementById("sevWarnCount").textContent = as.warning;
            }

            // Update Chart 6: Alarm Summary Stacked Bar
            if (alarmSummaryChart && data.alarm_summary) {
                alarmSummaryChart.data.labels = data.labels;
                alarmSummaryChart.data.datasets[0].data = data.alarm_summary.critical;
                alarmSummaryChart.data.datasets[1].data = data.alarm_summary.major;
                alarmSummaryChart.data.datasets[2].data = data.alarm_summary.minor;
                alarmSummaryChart.data.datasets[3].data = data.alarm_summary.warning;
                alarmSummaryChart.update();
            }
        } catch (err) {
            console.error("Failed to fetch timeseries data:", err);
        }
    }

    // 6. Bind Error Clicks for Modal
    function bindErrorClicks() {
        document.querySelectorAll(".error-row-clickable").forEach(row => {
            row.addEventListener("click", async function () {
                const errId = this.getAttribute("data-error-id");
                if (!errId || !errModal) return;

                try {
                    const resp = await fetch(`/monitoring/api/error-detail/${errId}/`, {
                        headers: { "X-Requested-With": "XMLHttpRequest" }
                    });
                    if (!resp.ok) throw new Error("Error detail not found");
                    const data = await resp.json();

                    document.getElementById("errModalComponent").textContent = data.component;
                    document.getElementById("errModalTime").textContent = data.time;
                    const sevEl = document.getElementById("errModalSeverity");
                    sevEl.textContent = data.severity;
                    sevEl.className = `badge bg-${data.severity.toLowerCase() === 'critical' ? 'danger' : 'warning'}`;
                    document.getElementById("errModalTarget").textContent = `Target: ${data.filename} (${data.stage})`;
                    document.getElementById("errModalMessage").textContent = data.message;

                    errModal.show();
                } catch (err) {
                    console.error("Error modal load error:", err);
                }
            });
        });
    }

    // 7. Time-range select change listener
    document.querySelectorAll(".time-range-select").forEach(sel => {
        sel.addEventListener("change", function () {
            currentRange = this.value;
            // Sync all selects to same range for visual consistency
            document.querySelectorAll(".time-range-select").forEach(s => s.value = currentRange);
            fetchTimeseries(currentRange);
            fetchOverview();
        });
    });

    // 8. Manual Refresh Button
    if (refreshBtn) {
        refreshBtn.addEventListener("click", async function (e) {
            e.preventDefault();
            if (refreshIcon) refreshIcon.classList.add("spin-clockwise");
            refreshBtn.disabled = true;

            await Promise.all([
                fetchOverview(),
                fetchTimeseries(currentRange),
                fetchFlowMonitoringSummary(flowCurrentRange)
            ]);

            setTimeout(() => {
                if (refreshIcon) refreshIcon.classList.remove("spin-clockwise");
                refreshBtn.disabled = false;
            }, 400);
        });
    }

    // =========================================================================
    // 9. MEDIATION FLOW MONITORING & RECONCILIATION LOGIC
    // =========================================================================

    function createFlowComboChart(ctx, barColor, lineColor, emptyMsg, callbacks) {
        return new Chart(ctx, {
            type: "bar",
            data: {
                labels: [],
                datasets: [
                    {
                        type: "bar",
                        label: "Files (Count)",
                        data: [],
                        backgroundColor: barColor,
                        borderRadius: { topLeft: 2, topRight: 2 },
                        maxBarThickness: 22,
                        yAxisID: "yFiles",
                        order: 2,
                    },
                    {
                        type: "line",
                        label: "Records (Count)",
                        data: [],
                        borderColor: lineColor,
                        backgroundColor: lineColor,
                        borderWidth: 2,
                        tension: 0.25,
                        pointRadius: 3.5,
                        pointHoverRadius: 6,
                        pointBackgroundColor: "#FFFFFF",
                        pointBorderColor: lineColor,
                        pointBorderWidth: 2,
                        yAxisID: "yRecords",
                        order: 1,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                emptyMessage: emptyMsg,
                interaction: {
                    mode: "index",
                    intersect: false,
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#1E293B",
                        titleColor: "#FFFFFF",
                        bodyColor: "#E2E8F0",
                        borderColor: "#334155",
                        borderWidth: 1,
                        padding: 8,
                        boxPadding: 4,
                        callbacks: callbacks,
                    }
                },
                layout: {
                    padding: {
                        left: 2,
                        right: 14,
                        top: 6,
                        bottom: 0,
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: {
                            color: "#64748B",
                            font: { size: 10, weight: "500" },
                            maxRotation: 20,
                            minRotation: 0,
                            autoSkip: true,
                            maxTicksLimit: 12,
                        }
                    },
                    yFiles: {
                        type: "linear",
                        position: "left",
                        beginAtZero: true,
                        grid: { color: "#F1F5F9" },
                        ticks: {
                            color: "#64748B",
                            font: { size: 9.5 },
                            precision: 0,
                            maxTicksLimit: 5,
                        },
                        title: {
                            display: true,
                            text: "Files",
                            color: "#64748B",
                            font: { size: 9.5, weight: "600" },
                            padding: { top: 0, bottom: 2 }
                        }
                    },
                    yRecords: {
                        type: "linear",
                        position: "right",
                        beginAtZero: true,
                        grid: { drawOnChartArea: false },
                        ticks: {
                            color: "#64748B",
                            font: { size: 9.5 },
                            maxTicksLimit: 5,
                            callback: function (v) {
                                if (v >= 1000000) return (v / 1000000).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "M";
                                if (v >= 1000) return (v / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "k";
                                return v;
                            }
                        },
                        title: {
                            display: true,
                            text: "Records",
                            color: "#64748B",
                            font: { size: 9.5, weight: "600" },
                            padding: { top: 0, bottom: 2 }
                        }
                    }
                }
            }
        });
    }

    function initFlowCharts() {
        // Chart 1: Collection Volume (Files: Blue, Records: Green)
        const ctxCol = document.getElementById("flowCollectionChart");
        if (ctxCol) {
            flowCollectionChart = createFlowComboChart(
                ctxCol,
                "#1677EE",
                "#16A34A",
                "No collection data for selected period.",
                {
                    title: (items) => items[0].label,
                    label: (item) => {
                        const val = item.parsed.y.toLocaleString();
                        return item.datasetIndex === 0 ? `Files Collected: ${val}` : `Records Collected: ${val}`;
                    }
                }
            );
        }

        // Chart 2: Processing Volume (Files: Green, Records: Blue)
        const ctxProc = document.getElementById("flowProcessingChart");
        if (ctxProc) {
            flowProcessingChart = createFlowComboChart(
                ctxProc,
                "#16A34A",
                "#1677EE",
                "No processing data for selected period.",
                {
                    title: (items) => items[0].label,
                    label: (item) => {
                        const val = item.parsed.y.toLocaleString();
                        return item.datasetIndex === 0 ? `Files Processed: ${val}` : `Records Processed: ${val}`;
                    },
                    afterBody: (items) => {
                        const idx = items[0].dataIndex;
                        const it = flowProcRawItems[idx];
                        if (it && it.failed !== undefined) {
                            return [`Failed Files: ${it.failed.toLocaleString()}`];
                        }
                        return [];
                    }
                }
            );
        }

        // Chart 3: Distribution Volume (Files: Orange, Records: Purple)
        const ctxDist = document.getElementById("flowDistributionChart");
        if (ctxDist) {
            flowDistributionChart = createFlowComboChart(
                ctxDist,
                "#FF8A1F",
                "#8B5CF6",
                "No distribution data for selected period.",
                {
                    title: (items) => items[0].label,
                    label: (item) => {
                        const val = item.parsed.y.toLocaleString();
                        return item.datasetIndex === 0 ? `Files Delivered: ${val}` : `Records Delivered: ${val}`;
                    },
                    afterBody: (items) => {
                        const idx = items[0].dataIndex;
                        const it = flowDistRawItems[idx];
                        if (it) {
                            const lines = [];
                            if (it.failed !== undefined) lines.push(`Failed: ${it.failed.toLocaleString()}`);
                            if (it.pending !== undefined) lines.push(`Pending: ${it.pending.toLocaleString()}`);
                            if (it.retries !== undefined) lines.push(`Retries: ${it.retries.toLocaleString()}`);
                            return lines;
                        }
                        return [];
                    }
                }
            );
        }
    }

    function updateReconciliationUI(rec) {
        if (!rec) return;

        // 1. Time range labels
        if (rec.time_label) {
            document.querySelectorAll(".flow-time-label").forEach(el => {
                el.textContent = rec.time_label;
            });
        }

        // 2. End-to-End Metrics
        if (rec.e2e) {
            const e2e = rec.e2e;
            const setVal = (id, val) => {
                const el = document.getElementById(id);
                if (el && val !== undefined) el.textContent = val;
            };
            if (e2e.collected) {
                setVal("e2eColFiles", e2e.collected.files);
                setVal("e2eColRecords", e2e.collected.records);
            }
            if (e2e.processed) {
                setVal("e2eProcFiles", e2e.processed.files);
                setVal("e2eProcRecords", e2e.processed.records);
            }
            if (e2e.distributed) {
                setVal("e2eDistFiles", e2e.distributed.files);
                setVal("e2eDistRecords", e2e.distributed.records);
            }
            if (e2e.variance) {
                const varEl = document.getElementById("e2eVarianceVal");
                if (varEl) {
                    varEl.textContent = e2e.variance.value;
                    varEl.className = `ump-flow-var-val ${e2e.variance.color || "text-success"}`;
                }
            }
            if (e2e.pending) {
                setVal("e2ePendingVal", e2e.pending.files);
            }
        }

        // 3. Per-Stream Reconciliation Table
        const tbody = document.getElementById("perStreamTbody");
        if (tbody) {
            if (!rec.per_stream || rec.per_stream.length === 0) {
                tbody.innerHTML = `<tr><td colspan="10" class="text-center text-muted py-4">No reconciliation data available.</td></tr>`;
            } else {
                tbody.innerHTML = rec.per_stream.map(r => `
                    <tr>
                        <td><span class="badge bg-light text-dark border fw-semibold">${r.stream}</span></td>
                        <td class="text-end font-monospace">${r.collected_files}</td>
                        <td class="text-end font-monospace">${r.collected_records}</td>
                        <td class="text-end font-monospace">${r.processed_files}</td>
                        <td class="text-end font-monospace">${r.processed_records}</td>
                        <td class="text-end font-monospace text-muted">${r.failed_files}</td>
                        <td class="text-end font-monospace">${r.distributed_files}</td>
                        <td class="text-end font-monospace">${r.distributed_records}</td>
                        <td class="text-end font-monospace text-muted">${r.pending}</td>
                        <td class="text-end fw-semibold ${r.success_rate_color || 'text-success'}">${r.success_rate}</td>
                    </tr>
                `).join("");
            }
        }
    }

    async function fetchFlowMonitoringSummary(range) {
        try {
            const url = `/monitoring/api/flow/summary/?range=${encodeURIComponent(range || flowCurrentRange)}&col_group=${encodeURIComponent(flowColGroup)}&proc_group=${encodeURIComponent(flowProcGroup)}&dist_group=${encodeURIComponent(flowDistGroup)}`;
            const resp = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
            if (!resp.ok) throw new Error("Flow summary status " + resp.status);
            const data = await resp.json();

            // 1. Collection Chart
            if (flowCollectionChart && data.collection) {
                flowCollectionChart.data.labels = data.collection.labels || [];
                flowCollectionChart.data.datasets[0].data = data.collection.files || [];
                flowCollectionChart.data.datasets[1].data = data.collection.records || [];
                flowCollectionChart.update();
            }

            // 2. Processing Chart
            if (flowProcessingChart && data.processing) {
                flowProcRawItems = data.processing.items || [];
                flowProcessingChart.data.labels = data.processing.labels || [];
                flowProcessingChart.data.datasets[0].data = data.processing.files || [];
                flowProcessingChart.data.datasets[1].data = data.processing.records || [];
                flowProcessingChart.update();
            }

            // 3. Distribution Chart
            if (flowDistributionChart && data.distribution) {
                flowDistRawItems = data.distribution.items || [];
                flowDistributionChart.data.labels = data.distribution.labels || [];
                flowDistributionChart.data.datasets[0].data = data.distribution.files || [];
                flowDistributionChart.data.datasets[1].data = data.distribution.records || [];
                flowDistributionChart.update();
            }

            // 4. Reconciliation
            if (data.reconciliation) {
                updateReconciliationUI(data.reconciliation);
            }
        } catch (err) {
            console.error("Failed to fetch flow monitoring summary:", err);
        }
    }

    async function fetchFlowCollectionOnly() {
        try {
            const url = `/monitoring/api/flow/collection/?range=${encodeURIComponent(flowCurrentRange)}&group_by=${encodeURIComponent(flowColGroup)}`;
            const resp = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
            if (!resp.ok) throw new Error("Collection API status " + resp.status);
            const data = await resp.json();

            if (flowCollectionChart) {
                flowCollectionChart.data.labels = data.labels || [];
                flowCollectionChart.data.datasets[0].data = data.files || [];
                flowCollectionChart.data.datasets[1].data = data.records || [];
                flowCollectionChart.update();
            }
        } catch (err) {
            console.error("Failed to fetch flow collection:", err);
        }
    }

    async function fetchFlowProcessingOnly() {
        try {
            const url = `/monitoring/api/flow/processing/?range=${encodeURIComponent(flowCurrentRange)}&group_by=${encodeURIComponent(flowProcGroup)}`;
            const resp = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
            if (!resp.ok) throw new Error("Processing API status " + resp.status);
            const data = await resp.json();

            if (flowProcessingChart) {
                flowProcRawItems = data.items || [];
                flowProcessingChart.data.labels = data.labels || [];
                flowProcessingChart.data.datasets[0].data = data.files || [];
                flowProcessingChart.data.datasets[1].data = data.records || [];
                flowProcessingChart.update();
            }
        } catch (err) {
            console.error("Failed to fetch flow processing:", err);
        }
    }

    async function fetchFlowDistributionOnly() {
        try {
            const url = `/monitoring/api/flow/distribution/?range=${encodeURIComponent(flowCurrentRange)}&group_by=${encodeURIComponent(flowDistGroup)}`;
            const resp = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
            if (!resp.ok) throw new Error("Distribution API status " + resp.status);
            const data = await resp.json();

            if (flowDistributionChart) {
                flowDistRawItems = data.items || [];
                flowDistributionChart.data.labels = data.labels || [];
                flowDistributionChart.data.datasets[0].data = data.files || [];
                flowDistributionChart.data.datasets[1].data = data.records || [];
                flowDistributionChart.update();
            }
        } catch (err) {
            console.error("Failed to fetch flow distribution:", err);
        }
    }

    // Bind Flow Section Controls
    const flowTimeSelect = document.getElementById("flowTimeRangeSelect");
    if (flowTimeSelect) {
        flowTimeSelect.addEventListener("change", function () {
            flowCurrentRange = this.value;
            fetchFlowMonitoringSummary(flowCurrentRange);
        });
    }

    document.querySelectorAll("#colGroupByBtnGroup button").forEach(btn => {
        btn.addEventListener("click", function () {
            document.querySelectorAll("#colGroupByBtnGroup button").forEach(b => b.classList.remove("active"));
            this.classList.add("active");
            flowColGroup = this.getAttribute("data-group") || "portal";
            fetchFlowCollectionOnly();
        });
    });

    document.querySelectorAll("#procGroupDropdown .dropdown-item").forEach(item => {
        item.addEventListener("click", function (e) {
            e.preventDefault();
            document.querySelectorAll("#procGroupDropdown .dropdown-item").forEach(i => i.classList.remove("active"));
            this.classList.add("active");
            flowProcGroup = this.getAttribute("data-group") || "stream";
            const btnText = document.getElementById("procGroupBtnText");
            if (btnText) btnText.textContent = this.textContent.trim();
            fetchFlowProcessingOnly();
        });
    });

    document.querySelectorAll("#distGroupDropdown .dropdown-item").forEach(item => {
        item.addEventListener("click", function (e) {
            e.preventDefault();
            document.querySelectorAll("#distGroupDropdown .dropdown-item").forEach(i => i.classList.remove("active"));
            this.classList.add("active");
            flowDistGroup = this.getAttribute("data-group") || "downstream";
            const btnText = document.getElementById("distGroupBtnText");
            if (btnText) btnText.textContent = this.textContent.trim();
            fetchFlowDistributionOnly();
        });
    });

    // =========================================================
    //  ACTIVE ALARMS, BACKLOG & STORAGE PANELS
    // =========================================================

    const sevColors = { 1: '#EF4444', 2: '#FF8A1F', 3: '#F4B400', 4: '#3488F4' };
    const sevLabels = { 1: 'CRITICAL', 2: 'MAJOR', 3: 'MINOR', 4: 'WARNING' };

    async function fetchActiveAlarms() {
        try {
            const resp = await fetch('/monitoring/api/alarms/', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            if (!resp.ok) throw new Error('Alarms API ' + resp.status);
            const data = await resp.json();
            const tbody = document.getElementById('activeAlarmsTbody');
            if (!tbody) return;
            if (!data.alarms || data.alarms.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3"><i class="bi bi-check-circle text-success"></i> No active alarms. System running normally.</td></tr>';
                return;
            }
            let html = '';
            data.alarms.forEach(a => {
                const sev = a.severity || 4;
                const label = sevLabels[sev] || 'INFO';
                const color = sevColors[sev] || '#94a3b8';
                html += `<tr>
                    <td><span class="badge" style="background:${color};color:#fff;font-size:10px;font-weight:700;letter-spacing:.3px;">${label}</span></td>
                    <td class="small fw-semibold">${a.category || '--'}</td>
                    <td class="small">${a.source || '--'}</td>
                    <td class="small text-truncate" style="max-width:220px;" title="${(a.message||'').replace(/"/g,'&quot;')}">${a.message || '--'}</td>
                    <td class="text-muted small text-nowrap">${a.timestamp || '--'}</td>
                </tr>`;
            });
            tbody.innerHTML = html;
        } catch (e) {
            console.error('fetchActiveAlarms:', e);
        }
    }

    async function fetchBacklog() {
        try {
            const resp = await fetch('/monitoring/api/backlog/', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            if (!resp.ok) throw new Error('Backlog API ' + resp.status);
            const data = await resp.json();
            const panel = document.getElementById('backlogPanelBody');
            if (!panel) return;

            const totals = data.totals || {};
            let html = '<div class="d-flex flex-column gap-3">';

            html += buildBacklogSection('Collection Backlog', totals.collection || 0, data.collection || []);
            html += buildBacklogSection('Processing Backlog', totals.processing || 0, data.processing || []);
            html += buildBacklogSection('Output Staging', totals.output_staging || 0, data.output_staging || []);

            html += '</div>';
            panel.innerHTML = html;
        } catch (e) {
            console.error('fetchBacklog:', e);
        }
    }

    function buildBacklogSection(title, totalCount, items) {
        let maxAge = 0, totalSize = 0, oldestDisplay = '—';
        items.forEach(it => {
            totalSize += it.total_size_mb || 0;
            if ((it.oldest_age_seconds || 0) > maxAge) {
                maxAge = it.oldest_age_seconds || 0;
                oldestDisplay = it.oldest_age_display || '—';
            }
        });
        const countCls = totalCount > 100 ? 'danger' : totalCount > 20 ? 'warn' : '';
        const ageCls = maxAge > 3600 ? 'danger' : maxAge > 900 ? 'warn' : '';

        let detailRows = '';
        if (items.length > 0) {
            detailRows = '<div style="margin-top:6px;font-size:11px;color:#64748b;">';
            items.forEach(it => {
                detailRows += `<div>${it.operator || ''}/${it.stream || ''}: ${it.count} files, ${(it.total_size_mb||0).toFixed(1)} MB, oldest ${it.oldest_age_display||'—'}</div>`;
            });
            detailRows += '</div>';
        }

        return `<div class="ump-backlog-item">
            <div class="ump-backlog-item-title">${title}</div>
            <div class="ump-backlog-metrics">
                <div class="ump-backlog-metric">
                    <span class="ump-backlog-metric-label">Files</span>
                    <span class="ump-backlog-metric-val ${countCls}">${totalCount.toLocaleString()}</span>
                </div>
                <div class="ump-backlog-metric">
                    <span class="ump-backlog-metric-label">Total Size</span>
                    <span class="ump-backlog-metric-val">${totalSize.toFixed(1)} MB</span>
                </div>
                <div class="ump-backlog-metric">
                    <span class="ump-backlog-metric-label">Oldest Age</span>
                    <span class="ump-backlog-metric-val ${ageCls}">${oldestDisplay}</span>
                </div>
            </div>
            ${detailRows}
        </div>`;
    }

    async function fetchStorage() {
        try {
            const resp = await fetch('/monitoring/api/storage/', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            if (!resp.ok) throw new Error('Storage API ' + resp.status);
            const data = await resp.json();
            const panel = document.getElementById('storagePanelBody');
            if (!panel) return;

            if (!data.locations || data.locations.length === 0) {
                panel.innerHTML = '<div class="text-center text-muted py-3">No storage locations configured.</div>';
                return;
            }

            let html = '';
            data.locations.forEach(loc => {
                const pct = loc.percent || 0;
                const cls = pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : 'ok';
                const usedStr = (loc.used_gb || 0).toFixed(1) + ' GB';
                const capStr = (loc.capacity_gb || 0).toFixed(1) + ' GB';
                html += `<div class="ump-storage-row">
                    <div class="ump-storage-label">${loc.name || loc.path}</div>
                    <div class="ump-storage-bar-track">
                        <div class="ump-storage-bar-fill ${cls}" style="width: ${Math.min(pct, 100)}%;"></div>
                    </div>
                    <div class="ump-storage-detail">
                        ${pct.toFixed(1)}% — ${usedStr} / ${capStr}
                    </div>
                </div>`;
            });
            panel.innerHTML = html;
        } catch (e) {
            console.error('fetchStorage:', e);
        }
    }

    // Bind refresh buttons
    const refreshAlarmsBtn = document.getElementById('refreshAlarmsBtn');
    if (refreshAlarmsBtn) refreshAlarmsBtn.addEventListener('click', fetchActiveAlarms);
    const refreshBacklogBtn = document.getElementById('refreshBacklogBtn');
    if (refreshBacklogBtn) refreshBacklogBtn.addEventListener('click', fetchBacklog);
    const refreshStorageBtn = document.getElementById('refreshStorageBtn');
    if (refreshStorageBtn) refreshStorageBtn.addEventListener('click', fetchStorage);

    // 10. Startup Execution
    initCharts();
    fetchOverview();
    fetchTimeseries(currentRange);
    fetchFlowMonitoringSummary(flowCurrentRange);
    bindErrorClicks();
    fetchActiveAlarms();
    fetchBacklog();
    fetchStorage();

    // 11. Background Polling: 15s overview, 60s charts/flow/alarms, 120s backlog/storage
    setInterval(fetchOverview, 15000);
    setInterval(() => {
        fetchTimeseries(currentRange);
        fetchFlowMonitoringSummary(flowCurrentRange);
        fetchActiveAlarms();
    }, 60000);
    setInterval(() => {
        fetchBacklog();
        fetchStorage();
    }, 120000);
});
