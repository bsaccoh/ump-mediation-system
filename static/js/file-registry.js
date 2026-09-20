document.addEventListener("DOMContentLoaded", () => {
    const rowCheckboxes = [...document.querySelectorAll(".file-checkbox")];
    const selectAllFiles = document.getElementById("selectAllFiles");
    const tableSelectAll = document.getElementById("tableSelectAll");
    const selectedCount = document.getElementById("selectedFilesCount");
    const bulkActionsBtn = document.getElementById("bulkActionsBtn");
    const detailButton = document.getElementById("showFileDetailsBtn");

    /* =============================================
       CSRF Helper
    ============================================== */
    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) {
            return parts.pop().split(";").shift();
        }
        return "";
    }

    /* =============================================
       SELECTION MANAGEMENT
    ============================================== */
    function getSelected() {
        return rowCheckboxes.filter(checkbox => checkbox.checked);
    }

    function updateSelectionUI() {
        const selected = getSelected();
        if (selectedCount) {
            selectedCount.textContent = `${selected.length} selected`;
        }

        if (bulkActionsBtn) {
            bulkActionsBtn.disabled = selected.length === 0;
        }

        if (detailButton) {
            detailButton.disabled = selected.length !== 1;
        }

        const allSelected = rowCheckboxes.length > 0 && selected.length === rowCheckboxes.length;
        const partial = selected.length > 0 && !allSelected;

        if (selectAllFiles) {
            selectAllFiles.checked = allSelected;
            selectAllFiles.indeterminate = partial;
        }

        if (tableSelectAll) {
            tableSelectAll.checked = allSelected;
            tableSelectAll.indeterminate = partial;
        }
    }

    function selectAllRows(checked) {
        rowCheckboxes.forEach(checkbox => {
            checkbox.checked = checked;
        });
        updateSelectionUI();
    }

    if (selectAllFiles) {
        selectAllFiles.addEventListener("change", (event) => {
            selectAllRows(event.target.checked);
        });
    }

    if (tableSelectAll) {
        tableSelectAll.addEventListener("change", (event) => {
            selectAllRows(event.target.checked);
        });
    }

    rowCheckboxes.forEach(checkbox => {
        checkbox.addEventListener("change", updateSelectionUI);
    });

    /* =============================================
       SHOW FILE DETAILS
    ============================================== */
    if (detailButton) {
        detailButton.addEventListener("click", () => {
            const selected = getSelected();
            if (selected.length !== 1) {
                return;
            }
            const row = selected[0].closest("tr");
            const detailUrl = row?.dataset?.detailUrl;
            if (detailUrl) {
                window.location.href = detailUrl;
            }
        });
    }

    /* =============================================
       ROWS PER PAGE CHANGE
    ============================================== */
    const rowsPerPageSelect = document.getElementById("registryRowsPerPage");
    if (rowsPerPageSelect) {
        rowsPerPageSelect.addEventListener("change", function () {
            const url = new URL(window.location.href);
            url.searchParams.set("per_page", this.value);
            url.searchParams.set("page", "1");
            window.location.href = url.toString();
        });
    }

    /* =============================================
       REPROCESS SELECTED
    ============================================== */
    const reprocessSelectedBtn = document.getElementById("reprocessSelected");
    if (reprocessSelectedBtn) {
        reprocessSelectedBtn.addEventListener("click", async () => {
            const ids = getSelected().map(checkbox => checkbox.value);
            if (!ids.length) {
                return;
            }

            if (!confirm(`Submit ${ids.length} file(s) for reprocessing?`)) {
                return;
            }

            const reprocessUrl = window.fileRegistryConfig?.reprocessUrl;
            if (!reprocessUrl) {
                alert("Reprocess URL is not configured.");
                return;
            }

            try {
                const response = await fetch(reprocessUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCookie("csrftoken")
                    },
                    body: JSON.stringify({ file_ids: ids })
                });

                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "Reprocessing request failed.");
                }

                window.location.reload();
            } catch (error) {
                console.error("Reprocess error:", error);
                alert(error.message);
            }
        });
    }

    /* =============================================
       SINGLE FILE REPROCESS
    ============================================== */
    document.querySelectorAll(".reprocess-single").forEach(button => {
        button.addEventListener("click", async function (e) {
            e.preventDefault();
            const id = this.dataset.fileId;
            if (!id) return;

            if (!confirm("Submit this file for reprocessing?")) {
                return;
            }

            const reprocessUrl = window.fileRegistryConfig?.reprocessUrl;
            if (!reprocessUrl) {
                alert("Reprocess URL is not configured.");
                return;
            }

            try {
                const response = await fetch(reprocessUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCookie("csrftoken")
                    },
                    body: JSON.stringify({ file_ids: [id] })
                });

                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "Unable to submit file.");
                }

                window.location.reload();
            } catch (error) {
                console.error("Single reprocess error:", error);
                alert(error.message);
            }
        });
    });

    /* =============================================
       EXPORT SELECTED
    ============================================== */
    const exportSelectedBtn = document.getElementById("exportSelected");
    if (exportSelectedBtn) {
        exportSelectedBtn.addEventListener("click", () => {
            const ids = getSelected().map(checkbox => checkbox.value);
            if (!ids.length) {
                return;
            }

            const exportSelectedUrl = window.fileRegistryConfig?.exportSelectedUrl;
            if (!exportSelectedUrl) {
                alert("Export URL is not configured.");
                return;
            }

            const url = new URL(exportSelectedUrl, window.location.origin);
            url.searchParams.set("ids", ids.join(","));
            window.location.href = url.toString();
        });
    }

    // Initialize UI on load
    updateSelectionUI();
});
