document.addEventListener("DOMContentLoaded", () => {
    const CHART_COLORS = ["#1678ef", "#16ad6a", "#ff852f", "#8749da", "#98a7b9"];

    if (typeof Chart !== "undefined") {
        Chart.defaults.font.family = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        Chart.defaults.color = "#536b83";
    }

    /* =====================================================
       DOUGHNUT BUILDER
    ====================================================== */
    function createDoughnut(canvasId, legendId, labels, values, colors) {
        const context = document.getElementById(canvasId);
        if (!context) return;

        new Chart(context, {
            type: "doughnut",
            data: {
                labels: labels,
                datasets: [
                    {
                        data: values,
                        backgroundColor: colors,
                        borderColor: "#ffffff",
                        borderWidth: 1.5,
                        hoverOffset: 3
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "65%",
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return `${context.label}: ${context.parsed}%`;
                            }
                        }
                    }
                }
            }
        });

        const legend = document.getElementById(legendId);
        if (legend) {
            legend.innerHTML = "";
            labels.forEach((label, index) => {
                const row = document.createElement("div");
                row.classList.add("legend-item");
                row.innerHTML = `
                    <span class="legend-dot" style="background:${colors[index]}"></span>
                    <span>${label}</span>
                    <span class="legend-value">${values[index]}%</span>
                `;
                legend.appendChild(row);
            });
        }
    }

    /* =====================================================
       FILTER HELPERS
    ====================================================== */
    function getFilterParams() {
        const params = new URLSearchParams(window.location.search);
        return {
            operator: params.get("operator") || "",
            period: params.get("period") || "today"
        };
    }

    function applyFilters(overrides) {
        const current = getFilterParams();
        const merged = {...current, ...overrides};
        const params = new URLSearchParams();
        if (merged.operator) params.set("operator", merged.operator);
        if (merged.period && merged.period !== "today") params.set("period", merged.period);
        window.location.search = params.toString();
    }

    /* =====================================================
       LOAD CHART DATA FROM API
    ====================================================== */
    async function loadChartData() {
        try {
            const filters = getFilterParams();
            const periodDays = {today: 1, "7": 7, "30": 30};
            const days = periodDays[filters.period] || 7;
            const periodParam = filters.period || "today";
            let chartUrl = `/monitoring/api/chart-data?days=${days}&period=${encodeURIComponent(periodParam)}`;
            if (filters.operator) chartUrl += `&operator=${encodeURIComponent(filters.operator)}`;

            // Update header period badge if present
            const periodBadge = document.getElementById("processingVolumePeriodBadge");
            if (periodBadge) {
                if (periodParam === "today") periodBadge.textContent = "Today (Hourly)";
                else if (periodParam === "30") periodBadge.textContent = "Last 30 Days";
                else periodBadge.textContent = "Last 7 Days";
            }

            const response = await fetch(chartUrl, {
                headers: {"Accept": "application/json"}
            });
            if (!response.ok) return;
            const data = await response.json();

            if (typeof Chart === "undefined") return;

            /* Processing Volume (Bar + Line) */
            const processingCtx = document.getElementById("processingChart");
            if (processingCtx && data.processing) {
                const existingChart = Chart.getChart(processingCtx);
                if (existingChart) existingChart.destroy();

                new Chart(processingCtx, {
                    data: {
                        labels: data.processing.labels,
                        datasets: [
                            {
                                type: "bar",
                                label: "Files",
                                data: data.processing.files,
                                backgroundColor: "rgba(37, 99, 235, 0.75)",
                                hoverBackgroundColor: "rgba(29, 78, 216, 0.95)",
                                borderColor: "#2563eb",
                                borderWidth: 1,
                                borderRadius: { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 },
                                maxBarThickness: 22,
                                categoryPercentage: 0.6,
                                barPercentage: 0.7,
                                order: 2,
                                yAxisID: "y"
                            },
                            {
                                type: "line",
                                label: "Records",
                                data: data.processing.records,
                                borderColor: "#10B981",
                                backgroundColor: "rgba(16, 185, 129, 0.08)",
                                fill: false,
                                tension: 0.35,
                                pointRadius: 3.5,
                                pointHoverRadius: 6,
                                pointBackgroundColor: "#ffffff",
                                pointBorderColor: "#10B981",
                                pointBorderWidth: 2,
                                borderWidth: 2.5,
                                order: 1,
                                yAxisID: "y1"
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        layout: {
                            padding: {
                                top: 8,
                                right: 32,
                                bottom: 4,
                                left: 6
                            }
                        },
                        interaction: {
                            mode: "index",
                            intersect: false
                        },
                        plugins: {
                            legend: {
                                position: "top",
                                align: "center",
                                labels: {
                                    boxWidth: 12,
                                    boxHeight: 12,
                                    borderRadius: 3,
                                    usePointStyle: false,
                                    font: { size: 11, weight: "500" },
                                    color: "#475569",
                                    padding: 14
                                }
                            },
                            tooltip: {
                                backgroundColor: "rgba(15, 23, 42, 0.92)",
                                titleFont: { size: 12, weight: "600" },
                                bodyFont: { size: 11 },
                                padding: 10,
                                cornerRadius: 6,
                                callbacks: {
                                    label: function(context) {
                                        let label = context.dataset.label || '';
                                        if (label) label += ': ';
                                        if (context.parsed.y !== null) {
                                            label += Number(context.parsed.y).toLocaleString();
                                            if (context.dataset.label === 'Files') label += ' files';
                                            else if (context.dataset.label === 'Records') label += ' records';
                                        }
                                        return label;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { display: false },
                                ticks: {
                                    font: { size: 10 },
                                    color: "#64748b",
                                    maxRotation: 0,
                                    autoSkip: true,
                                    maxTicksLimit: 12
                                }
                            },
                            y: {
                                position: "left",
                                beginAtZero: true,
                                title: {
                                    display: true,
                                    text: "Files",
                                    color: "#64748b",
                                    font: { size: 10.5, weight: "600" },
                                    padding: { top: 0, bottom: 4 }
                                },
                                ticks: {
                                    font: { size: 10 },
                                    color: "#64748b",
                                    precision: 0
                                },
                                grid: { color: "rgba(226, 232, 240, 0.6)" }
                            },
                            y1: {
                                position: "right",
                                beginAtZero: true,
                                title: {
                                    display: true,
                                    text: "Records",
                                    color: "#64748b",
                                    font: { size: 10.5, weight: "600" },
                                    padding: { top: 0, bottom: 4 }
                                },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    font: { size: 10 },
                                    color: "#64748b",
                                    callback: function(value) {
                                        if (value >= 1000000) return (value / 1000000).toFixed(1).replace(/\.0$/, '') + "M";
                                        if (value >= 1000) return (value / 1000).toFixed(0) + "K";
                                        return value;
                                    }
                                }
                            }
                        }
                    }
                });
            }

            /* Stream Breakdown */
            if (data.streams && data.streams.labels.length > 0) {
                const colors = data.streams.labels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]);
                createDoughnut("streamChart", "streamLegend", data.streams.labels, data.streams.values, colors);
            } else {
                createDoughnut("streamChart", "streamLegend",
                    ["No data"], [100], ["#d0d7de"]);
            }

            /* File Status Breakdown */
            const STATUS_COLORS = ["#17b568", "#1678ef", "#ff9f1c", "#ec3947", "#98a7b9"];
            if (data.file_status && data.file_status.labels.length > 0) {
                const sColors = data.file_status.labels.map((_, i) => STATUS_COLORS[i % STATUS_COLORS.length]);
                createDoughnut("statusChart", "statusLegend", data.file_status.labels, data.file_status.values, sColors);
            } else {
                createDoughnut("statusChart", "statusLegend",
                    ["No data"], [100], ["#d0d7de"]);
            }

        } catch (error) {
            console.error("Chart data load error:", error);
        }
    }

    loadChartData();

    /* =====================================================
       OPERATOR DROPDOWN & PERIOD BUTTONS
    ====================================================== */
    const operatorSelect = document.getElementById("operatorFilter");
    if (operatorSelect) {
        operatorSelect.addEventListener("change", () => {
            applyFilters({operator: operatorSelect.value});
        });
    }

    const periodButtons = document.querySelectorAll(".period-btn");
    periodButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            applyFilters({period: btn.dataset.period});
        });
    });

    /* =====================================================
       UMP SYSTEM HEALTH & SERVICE MONITORING (Auto 10s)
    ====================================================== */
    function bytesToGB(bytes) {
        if (!bytes || bytes <= 0) return "0.0";
        return (bytes / 1024 / 1024 / 1024).toFixed(1);
    }

    function setHealthProgress(element, percent) {
        if (!element) return;
        element.style.width = Math.min(100, Math.max(0, percent)) + "%";
        element.classList.remove("warning", "danger");

        if (percent >= 90) {
            element.classList.add("danger");
        } else if (percent >= 75) {
            element.classList.add("warning");
        }
    }

    function updateServiceCards(services) {
        if (!services) return;
        document.querySelectorAll(".service-monitor").forEach(card => {
            const serviceKey = card.dataset.service;
            const service = services[serviceKey];
            if (!service) return;

            const statusElement = card.querySelector(".service-status");
            const dot = card.querySelector(".service-state-dot");

            if (statusElement) {
                statusElement.textContent = service.status;
            }

            if (dot) {
                dot.classList.remove("running", "failed");
                if (service.healthy) {
                    dot.classList.add("running");
                } else {
                    dot.classList.add("failed");
                }
            }
        });
    }

    async function loadSystemHealth() {
        try {
            const response = await fetch("/monitoring/api/system-health", {
                headers: {
                    "Accept": "application/json"
                }
            });

            if (!response.ok) {
                throw new Error("System health API returned " + response.status);
            }

            const data = await response.json();

            /* CPU */
            const cpuVal = document.getElementById("cpuValue");
            const cpuDet = document.getElementById("cpuDetail");
            const cpuProg = document.getElementById("cpuProgress");
            if (cpuVal) cpuVal.textContent = data.cpu.percent + "%";
            if (cpuDet) cpuDet.textContent = `${data.cpu.cores} cores / ${data.cpu.threads} threads`;
            if (cpuProg) setHealthProgress(cpuProg, data.cpu.percent);

            /* MEMORY */
            const memVal = document.getElementById("memoryValue");
            const memDet = document.getElementById("memoryDetail");
            const memProg = document.getElementById("memoryProgress");
            if (memVal) memVal.textContent = data.memory.percent + "%";
            if (memDet) memDet.textContent = `${bytesToGB(data.memory.used)} GB / ${bytesToGB(data.memory.total)} GB`;
            if (memProg) setHealthProgress(memProg, data.memory.percent);

            /* DISK */
            const diskVal = document.getElementById("diskValue");
            const diskDet = document.getElementById("diskDetail");
            const diskProg = document.getElementById("diskProgress");
            if (diskVal) diskVal.textContent = data.disk.percent + "%";
            if (diskDet) diskDet.textContent = `${bytesToGB(data.disk.used)} GB / ${bytesToGB(data.disk.total)} GB`;
            if (diskProg) setHealthProgress(diskProg, data.disk.percent);

            /* SERVICES SUMMARY */
            const running = data.services_summary ? data.services_summary.running : 0;
            const total = data.services_summary ? data.services_summary.total : 0;
            const srvVal = document.getElementById("servicesValue");
            const srvDet = document.getElementById("servicesDetail");
            const srvProg = document.getElementById("servicesProgress");

            if (srvVal) srvVal.textContent = `${running} / ${total}`;
            const servicePercent = total > 0 ? (running / total) * 100 : 0;
            if (srvProg) setHealthProgress(srvProg, servicePercent);
            if (srvDet) {
                srvDet.textContent = running === total
                    ? "All services operational"
                    : `${total - running} service(s) unavailable`;
            }

            /* OVERALL INDICATOR */
            const indicator = document.getElementById("systemHealthIndicator");
            if (indicator) {
                indicator.classList.remove("healthy", "failed");
                indicator.classList.add(data.services_summary && data.services_summary.healthy ? "healthy" : "failed");
            }

            /* UPDATE TIME */
            const updateEl = document.getElementById("lastHealthUpdate");
            if (updateEl) {
                updateEl.textContent = "Updated " + new Date().toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit"
                });
            }

            /* INDIVIDUAL SERVICE CARDS */
            updateServiceCards(data.services);
        } catch (error) {
            console.error("UMP health monitoring error:", error);
            const indicator = document.getElementById("systemHealthIndicator");
            if (indicator) {
                indicator.classList.remove("healthy");
                indicator.classList.add("failed");
            }
            const updateEl = document.getElementById("lastHealthUpdate");
            if (updateEl) {
                updateEl.textContent = "Monitoring offline";
            }
        }
    }

    /* Initial load */
    loadSystemHealth();

    /* Refresh every 10 seconds */
    setInterval(loadSystemHealth, 10000);
});
