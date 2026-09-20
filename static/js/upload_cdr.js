/**
 * Upload CDR File Script
 * Manages drag-and-drop, folder/file mode switching, file picker triggers,
 * and selection preview.
 */
document.addEventListener("DOMContentLoaded", function () {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("fileInput");
    const chooseFilesBtn = document.getElementById("chooseFilesBtn");
    const modeFiles = document.getElementById("modeFiles");
    const modeFolder = document.getElementById("modeFolder");
    const clearBtn = document.getElementById("clearBtn");
    const uploadForm = document.getElementById("uploadForm");

    const summaryBox = document.getElementById("selectedFilesSummary");
    const countEl = document.getElementById("selectedFilesCount");
    const sizeEl = document.getElementById("selectedFilesSize");
    const namesEl = document.getElementById("selectedFilesNames");
    const clearSelectionLink = document.getElementById("clearSelectionLink");

    if (!dropzone || !fileInput) return;

    function formatFileSize(bytes) {
        if (!bytes || bytes <= 0) return "0 B";
        const k = 1024;
        const sizes = ["B", "KB", "MB", "GB"];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
    }

    function updateFilePreview(files) {
        if (!summaryBox || !countEl || !sizeEl || !namesEl) return;

        if (!files || files.length === 0) {
            summaryBox.classList.add("d-none");
            countEl.textContent = "0 files";
            sizeEl.textContent = "0 B";
            namesEl.textContent = "";
            return;
        }

        let totalBytes = 0;
        for (let i = 0; i < files.length; i++) {
            totalBytes += files[i].size;
        }

        countEl.textContent = `${files.length} file${files.length > 1 ? "s" : ""}`;
        sizeEl.textContent = formatFileSize(totalBytes);

        const displayedNames = Array.from(files).slice(0, 3).map(f => f.name).join(", ");
        const remaining = files.length > 3 ? ` +${files.length - 3} more` : "";
        namesEl.textContent = `${displayedNames}${remaining}`;

        summaryBox.classList.remove("d-none");
    }

    function clearFiles() {
        fileInput.value = "";
        updateFilePreview([]);
    }

    // Trigger file picker
    if (chooseFilesBtn) {
        chooseFilesBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            fileInput.click();
        });
    }

    dropzone.addEventListener("click", function (e) {
        if (e.target !== chooseFilesBtn && !chooseFilesBtn.contains(e.target)) {
            fileInput.click();
        }
    });

    // Drag and drop listeners
    ["dragenter", "dragover"].forEach(eventName => {
        dropzone.addEventListener(eventName, function (e) {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("dragover");
        });
    });

    ["dragleave", "drop"].forEach(eventName => {
        dropzone.addEventListener(eventName, function (e) {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragover");
        });
    });

    dropzone.addEventListener("drop", function (e) {
        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files.length) {
            fileInput.files = dt.files;
            updateFilePreview(dt.files);
        }
    });

    fileInput.addEventListener("change", function () {
        updateFilePreview(this.files);
    });

    // Clear controls
    if (clearBtn) {
        clearBtn.addEventListener("click", function () {
            if (uploadForm) uploadForm.reset();
            clearFiles();
            // Restore default mode
            if (modeFiles) modeFiles.checked = true;
            updateUploadMode();
        });
    }

    if (clearSelectionLink) {
        clearSelectionLink.addEventListener("click", function (e) {
            e.preventDefault();
            clearFiles();
        });
    }

    // Toggle between Multiple Files and Entire Folder
    function updateUploadMode() {
        if (!modeFolder || !fileInput) return;
        if (modeFolder.checked) {
            fileInput.setAttribute("webkitdirectory", "");
            fileInput.setAttribute("directory", "");
            fileInput.removeAttribute("multiple");
        } else {
            fileInput.removeAttribute("webkitdirectory");
            fileInput.removeAttribute("directory");
            fileInput.setAttribute("multiple", "");
        }
        clearFiles();
    }

    if (modeFiles && modeFolder) {
        modeFiles.addEventListener("change", updateUploadMode);
        modeFolder.addEventListener("change", updateUploadMode);
        updateUploadMode();
    }
});
