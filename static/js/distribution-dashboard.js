document.addEventListener("DOMContentLoaded", () => {
    const table = document.getElementById("distributionTable");
    const checkboxes = [...document.querySelectorAll(".delivery-checkbox")];
    const selectAll = document.getElementById("selectAll");
    const selectAllTop = document.getElementById("selectAllTop");
    const selectedCount = document.getElementById("selectedCount");
    const retrySelectedBtn = document.getElementById("retrySelectedBtn");
    const exportSelectedBtn = document.getElementById("exportSelectedBtn");
    const exportFilteredBtn = document.getElementById("exportFilteredBtn");
    const filenameSearch = document.getElementById("filenameSearch");
    const rowsPerPage = document.getElementById("rowsPerPage");

    /* =====================================================
       CSRF
    ====================================================== */
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie) {
            const cookies = document.cookie.split(";");
            for (const cookie of cookies) {
                const trimmed = cookie.trim();
                if (trimmed.startsWith(name + "=")) {
                    cookieValue = decodeURIComponent(trimmed.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    /* =====================================================
       SELECTION
    ====================================================== */
    function selectedRows() {
        return checkboxes.filter(checkbox => checkbox.checked);
    }

    function updateSelectionUI() {
        const selected = selectedRows();
        if (selectedCount) {
            selectedCount.textContent = `${selected.length} selected`;
        }

        if (exportSelectedBtn) {
            exportSelectedBtn.disabled = selected.length === 0;
        }

        const failedSelected = selected.filter(checkbox => {
            const row = checkbox.closest("tr");
            return row && row.dataset.status === "FAILED";
        });

        /*
         * Retry is enabled only when:
         * 1. something is selected
         * 2. every selected row is FAILED
         */
        if (retrySelectedBtn) {
            retrySelectedBtn.disabled = selected.length === 0 || failedSelected.length !== selected.length;
        }

        const allSelected = checkboxes.length > 0 && selected.length === checkboxes.length;

        if (selectAll) selectAll.checked = allSelected;
        if (selectAllTop) selectAllTop.checked = allSelected;

        const someSelected = selected.length > 0 && !allSelected;

        if (selectAll) selectAll.indeterminate = someSelected;
        if (selectAllTop) selectAllTop.indeterminate = someSelected;
    }

    function setAllSelection(checked) {
        checkboxes.forEach(cb => {
            cb.checked = checked;
        });
        updateSelectionUI();
    }

    selectAll?.addEventListener("change", event => {
        setAllSelection(event.target.checked);
    });

    selectAllTop?.addEventListener("change", event => {
        setAllSelection(event.target.checked);
    });

    checkboxes.forEach(cb => {
        cb.addEventListener("change", updateSelectionUI);
    });

    /* =====================================================
       ROWS PER PAGE
    ====================================================== */
    rowsPerPage?.addEventListener("change", function () {
        const url = new URL(window.location.href);
        url.searchParams.set("per_page", this.value);
        url.searchParams.set("page", "1");
        window.location.href = url.toString();
    });

    /* =====================================================
       ENTER IN SEARCH
    ====================================================== */
    filenameSearch?.addEventListener("keydown", event => {
        if (event.key === "Enter") {
            document.getElementById("distributionFilterForm")?.requestSubmit();
        }
    });

    /* =====================================================
       EXPORT FILTERED
    ====================================================== */
    exportFilteredBtn?.addEventListener("click", () => {
        const current = new URL(window.location.href);
        const exportUrl = new URL(window.distributionConfig.exportUrl, window.location.origin);
        current.searchParams.forEach((value, key) => {
            exportUrl.searchParams.set(key, value);
        });
        window.location.href = exportUrl.toString();
    });

    /* =====================================================
       EXPORT SELECTED
    ====================================================== */
    exportSelectedBtn?.addEventListener("click", () => {
        const ids = selectedRows().map(cb => cb.value);
        if (!ids.length) {
            return;
        }
        const exportUrl = new URL(window.distributionConfig.exportUrl, window.location.origin);
        exportUrl.searchParams.set("ids", ids.join(","));
        window.location.href = exportUrl.toString();
    });

    /* =====================================================
       RETRY SELECTED
    ====================================================== */
    retrySelectedBtn?.addEventListener("click", async () => {
        const selected = selectedRows();
        const ids = selected.map(cb => cb.value);
        if (!ids.length) {
            return;
        }

        const invalid = selected.some(cb => cb.closest("tr").dataset.status !== "FAILED");
        if (invalid) {
            alert("Only failed deliveries can be retried.");
            return;
        }

        const confirmed = confirm(
            `Retry ${ids.length} selected failed deliver${ids.length === 1 ? "y" : "ies"}?`
        );
        if (!confirmed) {
            return;
        }

        retrySelectedBtn.disabled = true;

        try {
            const response = await fetch(window.distributionConfig.retrySelectedUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCookie("csrftoken")
                },
                body: JSON.stringify({
                    ids: ids
                })
            });

            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.message || "Retry failed");
            }
            window.location.reload();
        } catch (error) {
            console.error(error);
            alert(error.message || "Unable to retry selected deliveries.");
            updateSelectionUI();
        }
    });

    /* =====================================================
       SINGLE RETRY
    ====================================================== */
    document.querySelectorAll(".retry-row-btn").forEach(button => {
        button.addEventListener("click", async function () {
            const id = this.dataset.id;
            if (!confirm("Retry this failed distribution?")) {
                return;
            }

            try {
                const response = await fetch(window.distributionConfig.retryUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCookie("csrftoken")
                    },
                    body: JSON.stringify({
                        id: id
                    })
                });

                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.message || "Retry failed");
                }
                window.location.reload();
            } catch (error) {
                console.error(error);
                alert(error.message || "Unable to retry delivery.");
            }
        });
    });

    updateSelectionUI();
});
