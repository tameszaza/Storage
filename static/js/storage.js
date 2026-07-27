(function () {
    function readLocalPreference(key, fallback) {
        try {
            return localStorage.getItem(key) || fallback;
        } catch (_error) {
            return fallback;
        }
    }

    function writeLocalPreference(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (_error) {
            // View changes still apply for the current page.
        }
    }

    const state = {
        progressModal: null,
        renameModal: null,
        moveCopyModal: null,
        selectedItems: [],
        draggedPath: null,
        draggedPaths: [],
        dragPreview: null,
        uploadXhr: null,
        uploadStartedAt: 0,
        uploadPreflightActive: false,
        uploadConflictModal: null,
        pendingConflictItems: [],
        pendingConflictNames: [],
        exportProgressModal: null,
        exportAbortController: null,
        exportStartedAt: 0,
    };

    function getBrowser() {
        return document.getElementById("fileBrowser");
    }

    function getCurrentPath() {
        return getBrowser()?.dataset.currentPath || "";
    }

    function showToast(message, type = "info") {
        let stack = document.getElementById("toastStack");
        if (!stack) {
            stack = document.createElement("div");
            stack.id = "toastStack";
            stack.className = "toast-stack";
            stack.setAttribute("aria-live", "polite");
            stack.setAttribute("aria-atomic", "true");
            document.body.appendChild(stack);
        }
        const toast = document.createElement("div");
        toast.className = `mini-toast ${type}`;
        toast.setAttribute("role", type === "error" ? "alert" : "status");
        toast.textContent = message;
        stack.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add("show"));
        setTimeout(() => {
            toast.classList.remove("show");
            setTimeout(() => toast.remove(), 250);
        }, 2400);
    }

    function setView(view) {
        const browser = getBrowser();
        const icon = document.getElementById("viewToggleIcon");
        const button = document.getElementById("viewToggleBtn");
        if (!browser || !icon) return;
        browser.classList.toggle("browser-grid", view === "grid");
        browser.classList.toggle("browser-list", view === "list");
        icon.className = view === "grid" ? "fa-solid fa-list" : "fa-solid fa-table-cells-large";
        const nextLabel = view === "grid" ? "Switch to list view" : "Switch to grid view";
        if (button) {
            button.setAttribute("aria-label", nextLabel);
            button.title = nextLabel;
            button.setAttribute("aria-pressed", String(view === "list"));
        }
        writeLocalPreference("storageView", view);
        requestAnimationFrame(refreshDuplicateMenuActions);
    }

    function isElementDisplayed(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
    }

    function menuEntryWrapper(item) {
        const form = item.closest("form");
        return form && form.closest(".file-menu-panel") ? form : item;
    }

    function setMenuEntryHidden(item, hidden) {
        const wrapper = menuEntryWrapper(item);
        item.hidden = hidden;
        item.setAttribute("aria-hidden", hidden ? "true" : "false");
        if (wrapper !== item) {
            wrapper.hidden = hidden;
            wrapper.setAttribute("aria-hidden", hidden ? "true" : "false");
        }
    }

    function isVisibleMenuAction(element) {
        if (!element || element.hidden || element.classList.contains("dropdown-divider")) return false;
        if (element.matches("form")) {
            const action = element.querySelector(".dropdown-item");
            return !!action && !action.hidden;
        }
        return element.matches(".dropdown-item") && !element.hidden;
    }

    function refreshMenuDividers(panel) {
        const children = Array.from(panel.children);
        children.forEach((child, index) => {
            if (!child.classList.contains("dropdown-divider")) return;
            const hasActionBefore = children.slice(0, index).some(isVisibleMenuAction);
            const hasActionAfter = children.slice(index + 1).some(isVisibleMenuAction);
            child.hidden = !(hasActionBefore && hasActionAfter);
        });
    }

    function refreshDuplicateMenuActions() {
        document.querySelectorAll(".file-card").forEach((card) => {
            const panel = card.querySelector(".file-menu-panel");
            if (!panel) return;

            const quickActions = new Set();
            const quickActionWrap = card.querySelector(".file-inline-actions");
            if (isElementDisplayed(quickActionWrap)) {
                card.querySelectorAll("[data-quick-action]").forEach((action) => {
                    if (isElementDisplayed(action)) quickActions.add(action.dataset.quickAction);
                });
            }

            panel.querySelectorAll("[data-menu-action]").forEach((item) => {
                setMenuEntryHidden(item, quickActions.has(item.dataset.menuAction));
            });
            refreshMenuDividers(panel);
        });
    }

    function describeSelection(items) {
        const count = items.length;
        if (count === 0) return "No file selected";

        const folderNames = new Set();
        items.forEach((item) => {
            const file = item.file || item;
            const relativePath = item.relativePath || file.webkitRelativePath || "";
            const topFolder = relativePath.includes("/") ? relativePath.split("/")[0] : "";
            if (topFolder) folderNames.add(topFolder);
        });

        if (folderNames.size === 1) return `${count} file${count === 1 ? "" : "s"} from ${Array.from(folderNames)[0]}`;
        if (folderNames.size > 1) return `${count} files from ${folderNames.size} folders`;
        return count === 1 ? (items[0].name || items[0].file?.name || "1 file selected") : `${count} files selected`;
    }

    function formatBytes(value) {
        const bytes = Number(value) || 0;
        if (bytes === 0) return "0 B";
        const units = ["B", "KB", "MB", "GB", "TB"];
        const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const amount = bytes / (1024 ** index);
        return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
    }

    function renderUploadQueue() {
        const queue = document.getElementById("uploadQueue");
        const container = document.getElementById("uploadQueueItems");
        if (!queue || !container) return;
        const items = state.selectedItems.slice(0, 6);
        queue.hidden = state.selectedItems.length === 0;
        container.innerHTML = "";
        items.forEach((item) => {
            const file = item.file || item;
            const relativeName = item.relativePath || file.webkitRelativePath || file.name;
            const row = document.createElement("div");
            row.className = "upload-queue-item";
            row.innerHTML = `<span class="upload-queue-file-icon"><i class="fa-regular fa-file" aria-hidden="true"></i></span><span><strong></strong><small>${formatBytes(file.size)}</small></span>`;
            row.querySelector("strong").textContent = relativeName;
            container.appendChild(row);
        });
        if (state.selectedItems.length > items.length) {
            const more = document.createElement("p");
            more.className = "upload-queue-more";
            more.textContent = `and ${state.selectedItems.length - items.length} more file${state.selectedItems.length - items.length === 1 ? "" : "s"}`;
            container.appendChild(more);
        }
    }

    function setSelectedItems(items) {
        state.selectedItems = Array.from(items || []);
        const fileLabel = document.getElementById("fileLabel");
        const uploadButton = document.getElementById("uploadButton");
        if (fileLabel) fileLabel.textContent = describeSelection(state.selectedItems);
        if (uploadButton) uploadButton.disabled = state.selectedItems.length === 0;
        renderUploadQueue();
    }

    function appendUploadItem(formData, item) {
        const file = item.file || item;
        const relativeName = item.relativePath || file.webkitRelativePath || file.name;
        formData.append("file", file, relativeName);
    }

    function uploadItemName(item) {
        const file = item.file || item;
        return item.relativePath || file.webkitRelativePath || file.name;
    }

    async function checkUploadConflicts(form, items) {
        const url = form?.dataset.conflictUrl;
        if (!url) return [];
        const response = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body: JSON.stringify({ filenames: items.map(uploadItemName) }),
        });
        if (!response.ok) throw new Error("Could not check duplicate filenames.");
        const payload = await response.json();
        return Array.isArray(payload.conflicts) ? payload.conflicts : [];
    }

    function renderUploadProgressFiles(items, conflictAction = "", conflictNames = []) {
        const container = document.getElementById("uploadProgressFiles");
        if (!container) return;
        const conflicts = new Set(conflictNames);
        const visibleItems = items.slice(0, 8);
        container.innerHTML = "";
        visibleItems.forEach((item) => {
            const name = uploadItemName(item);
            const isConflict = conflicts.has(name);
            const row = document.createElement("div");
            row.className = "upload-progress-file";

            const icon = document.createElement("span");
            icon.className = "upload-progress-file-icon";
            icon.innerHTML = '<i class="fa-regular fa-file" aria-hidden="true"></i>';

            const label = document.createElement("span");
            label.className = "upload-progress-file-name";
            label.textContent = name;

            const badge = document.createElement("span");
            badge.className = `upload-progress-file-status${isConflict ? " conflict" : ""}`;
            if (isConflict && conflictAction === "replace") badge.textContent = "Replace";
            else if (isConflict && conflictAction === "rename") badge.textContent = "Keep both";
            else badge.textContent = "Upload";

            row.append(icon, label, badge);
            container.appendChild(row);
        });
        if (items.length > visibleItems.length) {
            const more = document.createElement("div");
            more.className = "upload-progress-file-more";
            more.textContent = `+ ${items.length - visibleItems.length} more file${items.length - visibleItems.length === 1 ? "" : "s"}`;
            container.appendChild(more);
        }
    }

    function clearOrphanedModalState() {
        if (document.querySelector(".modal.show")) return;
        document.querySelectorAll(".modal-backdrop").forEach((backdrop) => backdrop.remove());
        document.body.classList.remove("modal-open");
        document.body.style.removeProperty("overflow");
        document.body.style.removeProperty("padding-right");
    }

    function afterModalCloses(element, instance, callback) {
        let finished = false;
        let fallbackTimer = null;
        const finish = () => {
            if (finished) return;
            finished = true;
            if (fallbackTimer) window.clearTimeout(fallbackTimer);
            element?.removeEventListener("hidden.bs.modal", finish);
            if (element) {
                element.classList.remove("show");
                element.style.display = "none";
                element.setAttribute("aria-hidden", "true");
                element.removeAttribute("aria-modal");
                element.removeAttribute("role");
            }
            clearOrphanedModalState();
            window.requestAnimationFrame(() => window.requestAnimationFrame(callback));
        };

        if (!element || !instance || !element.classList.contains("show")) {
            finish();
            return;
        }

        element.addEventListener("hidden.bs.modal", finish, { once: true });
        instance.hide();
        fallbackTimer = window.setTimeout(finish, 500);
    }

    function showUploadConflicts(items, conflicts) {
        state.pendingConflictItems = Array.from(items || []);
        state.pendingConflictNames = Array.from(conflicts || []);
        const list = document.getElementById("uploadConflictList");
        if (list) {
            list.innerHTML = "";
            const visibleConflicts = Array.from(conflicts || []).slice(0, 8);
            visibleConflicts.forEach((name) => {
                const item = document.createElement("div");
                item.className = "list-group-item";
                item.textContent = name;
                list.appendChild(item);
            });
            if ((conflicts || []).length > visibleConflicts.length) {
                const more = document.createElement("div");
                more.className = "list-group-item text-muted";
                more.textContent = `and ${conflicts.length - visibleConflicts.length} more`;
                list.appendChild(more);
            }
        }

        const showConflictModal = () => state.uploadConflictModal?.show();
        const progressElement = document.getElementById("progressModal");
        afterModalCloses(progressElement, state.progressModal, showConflictModal);
    }

    function retryConflictingUpload(action) {
        const items = state.pendingConflictItems.slice();
        const conflicts = state.pendingConflictNames.slice();
        state.pendingConflictItems = [];
        state.pendingConflictNames = [];
        if (items.length === 0) return;

        const conflictElement = document.getElementById("uploadConflictModal");
        const retry = () => uploadFiles(items, action, true, conflicts);
        afterModalCloses(conflictElement, state.uploadConflictModal, retry);
    }

    function updateExportProgress(receivedBytes, totalBytes) {
        const bar = document.getElementById("exportProgressBar");
        const track = bar?.closest('[role="progressbar"]');
        const percentage = document.getElementById("exportProgressPercentage");
        const bytes = document.getElementById("exportProgressBytes");
        const speed = document.getElementById("exportProgressSpeed");
        const eta = document.getElementById("exportProgressEta");
        const status = document.getElementById("exportProgressStatus");
        const elapsedSeconds = Math.max((performance.now() - state.exportStartedAt) / 1000, 0.1);
        const bytesPerSecond = receivedBytes / elapsedSeconds;

        bar?.classList.remove("progress-bar-striped", "progress-bar-animated");
        if (totalBytes > 0) {
            const percent = Math.min(100, Math.round((receivedBytes / totalBytes) * 100));
            const remainingSeconds = bytesPerSecond > 0 ? (totalBytes - receivedBytes) / bytesPerSecond : 0;
            if (bar) bar.style.width = `${percent}%`;
            if (track) track.setAttribute("aria-valuenow", String(percent));
            if (percentage) percentage.textContent = `${percent}%`;
            if (bytes) bytes.textContent = `${formatBytes(receivedBytes)} of ${formatBytes(totalBytes)}`;
            if (eta) eta.textContent = remainingSeconds > 1 ? `About ${Math.ceil(remainingSeconds)}s left` : "Almost done";
            if (status) status.textContent = `Downloading ZIP, ${percent}% complete`;
        } else {
            if (bar) bar.style.width = "100%";
            if (track) track.removeAttribute("aria-valuenow");
            if (percentage) percentage.textContent = "…";
            if (bytes) bytes.textContent = formatBytes(receivedBytes);
            if (eta) eta.textContent = "Calculating…";
            if (status) status.textContent = "Downloading ZIP…";
        }
        if (speed) speed.textContent = `${formatBytes(bytesPerSecond)}/s`;
    }

    async function downloadSelectedFiles(names, url) {
        if (!url || names.length === 0 || state.exportAbortController) return;

        const bar = document.getElementById("exportProgressBar");
        const track = bar?.closest('[role="progressbar"]');
        const percentage = document.getElementById("exportProgressPercentage");
        const bytes = document.getElementById("exportProgressBytes");
        const speed = document.getElementById("exportProgressSpeed");
        const eta = document.getElementById("exportProgressEta");
        const status = document.getElementById("exportProgressStatus");
        const summary = document.getElementById("exportProgressSummary");
        const cancelButton = document.getElementById("cancelExportButton");
        const downloadButton = document.getElementById("bulkDownloadBtn");
        const formData = new FormData();
        formData.append("current_path", getCurrentPath());
        names.forEach((name) => formData.append("selected_files", name));

        if (summary) summary.textContent = `${names.length} item${names.length === 1 ? "" : "s"} selected`;
        if (percentage) percentage.textContent = "…";
        if (bytes) bytes.textContent = "Waiting for archive";
        if (speed) speed.textContent = "Preparing…";
        if (eta) eta.textContent = "Please wait";
        if (status) status.textContent = "Preparing ZIP on the server…";
        if (bar) {
            bar.style.width = "100%";
            bar.classList.add("progress-bar-striped", "progress-bar-animated");
        }
        if (track) {
            track.removeAttribute("aria-valuenow");
            track.setAttribute("aria-label", "Preparing ZIP archive");
        }
        if (cancelButton) cancelButton.disabled = false;
        if (downloadButton) downloadButton.disabled = true;

        const controller = new AbortController();
        state.exportAbortController = controller;
        state.exportStartedAt = performance.now();
        state.exportProgressModal?.show();

        try {
            const response = await fetch(url, {
                method: "POST",
                body: formData,
                signal: controller.signal,
            });
            if (!response.ok) {
                let message = "Could not prepare the ZIP download.";
                try {
                    const payload = await response.json();
                    message = payload.message || message;
                } catch (_error) {
                    // Keep the generic error when the server did not return JSON.
                }
                throw new Error(message);
            }

            const totalBytes = Number(response.headers.get("Content-Length")) || 0;
            const chunks = [];
            let receivedBytes = 0;

            if (response.body?.getReader) {
                const reader = response.body.getReader();
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    chunks.push(value);
                    receivedBytes += value.byteLength;
                    updateExportProgress(receivedBytes, totalBytes);
                }
            } else {
                const fallbackBlob = await response.blob();
                chunks.push(fallbackBlob);
                receivedBytes = fallbackBlob.size;
                updateExportProgress(receivedBytes, totalBytes || receivedBytes);
            }

            const archive = new Blob(chunks, { type: response.headers.get("Content-Type") || "application/zip" });
            const objectUrl = URL.createObjectURL(archive);
            const link = document.createElement("a");
            link.href = objectUrl;
            link.download = "selected_files.zip";
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);

            if (bar) bar.style.width = "100%";
            if (track) {
                track.setAttribute("aria-valuenow", "100");
                track.setAttribute("aria-label", "ZIP download complete");
            }
            if (percentage) percentage.textContent = "100%";
            if (status) status.textContent = "ZIP download complete";
            if (eta) eta.textContent = "Complete";
            window.setTimeout(() => state.exportProgressModal?.hide(), 700);
        } catch (error) {
            state.exportProgressModal?.hide();
            if (error.name === "AbortError") showToast("ZIP export cancelled.", "info");
            else showToast(error.message || "Could not download selected files.", "error");
        } finally {
            state.exportAbortController = null;
            if (cancelButton) cancelButton.disabled = true;
            if (downloadButton) downloadButton.disabled = false;
        }
    }

    async function uploadFiles(items, conflictAction = "", skipConflictCheck = false, knownConflicts = []) {
        const form = document.getElementById("uploadForm");
        const progressBar = document.getElementById("progressBar");
        const progressPercentage = document.getElementById("progressPercentage");
        const progressTrack = progressBar?.closest('[role="progressbar"]');
        const progressBytes = document.getElementById("progressBytes");
        const progressSpeed = document.getElementById("progressSpeed");
        const progressEta = document.getElementById("progressEta");
        const progressStatus = document.getElementById("progressStatus");
        const progressFileCount = document.getElementById("progressFileCount");
        const progressCurrentFile = document.getElementById("progressCurrentFile");
        const uploadButton = document.getElementById("uploadButton");
        const cancelButton = document.getElementById("cancelUploadButton");
        const progressFooter = document.getElementById("progressModalFooter");
        const uploadItems = Array.from(items || []).filter(Boolean);
        if (!form || uploadItems.length === 0 || state.uploadPreflightActive) return;

        if (!conflictAction && !skipConflictCheck) {
            state.uploadPreflightActive = true;
            if (uploadButton) uploadButton.disabled = true;
            try {
                const conflicts = await checkUploadConflicts(form, uploadItems);
                if (conflicts.length > 0) {
                    if (uploadButton) uploadButton.disabled = false;
                    showUploadConflicts(uploadItems, conflicts);
                    return;
                }
            } catch (_error) {
                // The upload endpoint performs the same authoritative check.
            } finally {
                state.uploadPreflightActive = false;
            }
        }

        const formData = new FormData();
        uploadItems.forEach((item) => appendUploadItem(formData, item));
        if (conflictAction) formData.append("conflict_action", conflictAction);
        const totalFileBytes = uploadItems.reduce((sum, item) => sum + Number((item.file || item).size || 0), 0);

        if (uploadButton) uploadButton.disabled = true;
        if (cancelButton) cancelButton.disabled = false;
        if (progressFooter) progressFooter.hidden = false;
        if (progressBar) progressBar.style.width = "0%";
        if (progressTrack) progressTrack.setAttribute("aria-valuenow", "0");
        if (progressPercentage) progressPercentage.textContent = "0%";
        if (progressBytes) progressBytes.textContent = `0 B of ${formatBytes(totalFileBytes)}`;
        if (progressSpeed) progressSpeed.textContent = "Calculating speed…";
        if (progressEta) progressEta.textContent = "Estimating time…";
        if (progressStatus) progressStatus.textContent = "Connecting to Tamestorage…";
        if (progressFileCount) progressFileCount.textContent = `${uploadItems.length} file${uploadItems.length === 1 ? "" : "s"} selected`;
        if (progressCurrentFile) progressCurrentFile.textContent = uploadItems.length === 1 ? (uploadItems[0].relativePath || uploadItems[0].name || uploadItems[0].file?.name) : "Uploading as one secure transfer";
        renderUploadProgressFiles(uploadItems, conflictAction, knownConflicts);

        // The upload chooser is displayed through the #dropArea target. Remove the
        // hash before opening Bootstrap's progress dialog so the two overlays can
        // never stack on top of one another.
        if (window.location.hash === "#dropArea") {
            window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
        }
        const uploadPanel = document.getElementById("dropArea");
        uploadPanel?.classList.remove("dragging");
        uploadPanel?.classList.add("upload-panel-hidden");
        if (state.progressModal) state.progressModal.show();

        const xhr = new XMLHttpRequest();
        state.uploadXhr = xhr;
        state.uploadStartedAt = performance.now();
        xhr.open("POST", form.action, true);
        xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
        xhr.setRequestHeader("Accept", "application/json");
        xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable) return;
            const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
            const elapsedSeconds = Math.max((performance.now() - state.uploadStartedAt) / 1000, 0.1);
            const bytesPerSecond = event.loaded / elapsedSeconds;
            const remainingSeconds = bytesPerSecond > 0 ? (event.total - event.loaded) / bytesPerSecond : 0;
            if (progressBar) progressBar.style.width = `${percent}%`;
            if (progressTrack) progressTrack.setAttribute("aria-valuenow", String(percent));
            if (progressPercentage) progressPercentage.textContent = `${percent}%`;
            if (progressBytes) progressBytes.textContent = `${formatBytes(event.loaded)} of ${formatBytes(event.total)}`;
            if (progressSpeed) progressSpeed.textContent = `${formatBytes(bytesPerSecond)}/s`;
            if (progressEta) progressEta.textContent = remainingSeconds > 1 ? `About ${Math.ceil(remainingSeconds)}s left` : "Almost done";
            if (progressStatus) progressStatus.textContent = `Uploading, ${percent}% complete`;
        };
        xhr.upload.onload = () => {
            if (progressStatus) progressStatus.textContent = "Upload transferred. Saving files…";
            if (progressEta) progressEta.textContent = "Finishing…";
        };
        xhr.onload = () => {
            state.uploadXhr = null;
            let payload = {};
            try { payload = JSON.parse(xhr.responseText || "{}"); } catch (error) { payload = {}; }
            if (xhr.status >= 200 && xhr.status < 400 && payload.success !== false) {
                if (progressBar) progressBar.style.width = "100%";
                if (progressTrack) progressTrack.setAttribute("aria-valuenow", "100");
                if (progressPercentage) progressPercentage.textContent = "100%";
                if (progressStatus) progressStatus.textContent = payload.message || "Upload complete";
                if (progressEta) progressEta.textContent = "Complete";
                document.querySelectorAll(".upload-progress-file-status").forEach((badge) => {
                    badge.classList.remove("conflict");
                    badge.textContent = "Done";
                });
                if (cancelButton) cancelButton.disabled = true;
                if (progressFooter) progressFooter.hidden = true;
                window.setTimeout(() => { window.location.href = payload.redirect_url || window.location.href; }, 1000);
            } else if (xhr.status === 409 && payload.conflict) {
                if (uploadButton) uploadButton.disabled = false;
                showUploadConflicts(uploadItems, payload.conflicts || []);
            } else {
                if (state.progressModal) state.progressModal.hide();
                if (uploadButton) uploadButton.disabled = false;
                showToast(payload.message || "Upload failed. Please try again.", "error");
            }
        };
        xhr.onerror = () => {
            state.uploadXhr = null;
            if (state.progressModal) state.progressModal.hide();
            if (uploadButton) uploadButton.disabled = false;
            showToast("Upload failed because the connection was interrupted.", "error");
        };
        xhr.onabort = () => {
            state.uploadXhr = null;
            if (state.progressModal) state.progressModal.hide();
            if (uploadButton) uploadButton.disabled = false;
            showToast("Upload cancelled.", "info");
        };
        xhr.send(formData);
    }

    function readEntry(entry, prefix = "") {
        return new Promise((resolve) => {
            if (!entry) {
                resolve([]);
                return;
            }

            if (entry.isFile) {
                entry.file((file) => {
                    resolve([{ file, relativePath: prefix + file.name }]);
                }, () => resolve([]));
                return;
            }

            if (entry.isDirectory) {
                const reader = entry.createReader();
                const entries = [];
                const readBatch = () => {
                    reader.readEntries(async (batch) => {
                        if (!batch.length) {
                            const nested = await Promise.all(entries.map((child) => readEntry(child, prefix + entry.name + "/")));
                            resolve(nested.flat());
                            return;
                        }
                        entries.push(...batch);
                        readBatch();
                    }, () => resolve([]));
                };
                readBatch();
                return;
            }

            resolve([]);
        });
    }

    async function filesFromDataTransfer(dataTransfer) {
        const items = Array.from(dataTransfer.items || []);
        const entries = items
            .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
            .filter(Boolean);

        if (entries.length > 0) {
            const nested = await Promise.all(entries.map((entry) => readEntry(entry)));
            return nested.flat();
        }

        return Array.from(dataTransfer.files || []);
    }

    function isInternalDrag(event) {
        return state.draggedPaths.length > 0
            || Boolean(state.draggedPath)
            || Array.from(event.dataTransfer?.types || []).includes("application/x-tamestorage-path");
    }

    function hasFilePayload(event) {
        const dataTransfer = event.dataTransfer;
        if (!dataTransfer) return false;
        return Array.from(dataTransfer.types || []).includes("Files")
            || (dataTransfer.files && dataTransfer.files.length > 0);
    }

    function getSelectedCheckboxes() {
        return Array.from(document.querySelectorAll(".select-item:checked"));
    }

    function getSelectedNames() {
        return getSelectedCheckboxes().map((box) => box.value).filter(Boolean);
    }

    function getSelectedPaths() {
        return getSelectedCheckboxes().map((box) => box.dataset.path).filter(Boolean);
    }

    function updateSelectionBar() {
        const selected = getSelectedCheckboxes();
        const bar = document.getElementById("selectionBar");
        const count = document.getElementById("selectionCount");
        if (!bar || !count) return;
        bar.hidden = selected.length === 0;
        count.textContent = `${selected.length} item${selected.length === 1 ? "" : "s"} selected`;
        document.querySelectorAll(".file-card").forEach((card) => {
            const box = card.querySelector(".select-item");
            card.classList.toggle("selected-card", !!box && box.checked);
        });
    }

    function submitDynamicForm(action, fields) {
        const form = document.createElement("form");
        form.method = "POST";
        form.action = action;
        form.style.display = "none";
        Object.entries(fields).forEach(([name, value]) => {
            const values = Array.isArray(value) ? value : [value];
            values.forEach((item) => {
                const input = document.createElement("input");
                input.type = "hidden";
                input.name = name;
                input.value = item;
                form.appendChild(input);
            });
        });
        document.body.appendChild(form);
        form.submit();
    }

    function clearSelection() {
        document.querySelectorAll(".select-item:checked").forEach((box) => {
            box.checked = false;
        });
        updateSelectionBar();
    }

    function setupBulkActions() {
        let lastSelectionBox = null;
        document.querySelectorAll(".select-item").forEach((box) => {
            box.addEventListener("click", (event) => {
                if (event.shiftKey && lastSelectionBox) {
                    const boxes = Array.from(document.querySelectorAll(".select-item"));
                    const start = boxes.indexOf(lastSelectionBox);
                    const end = boxes.indexOf(box);
                    if (start >= 0 && end >= 0) {
                        boxes.slice(Math.min(start, end), Math.max(start, end) + 1).forEach((item) => {
                            item.checked = box.checked;
                        });
                    }
                }
                lastSelectionBox = box;
            });
            box.addEventListener("change", updateSelectionBar);
        });

        document.querySelectorAll(".file-card[data-path]").forEach((card) => {
            card.addEventListener("click", (event) => {
                if (!(event.ctrlKey || event.metaKey)) return;
                if (event.target.closest("button, input, textarea, select, .dropdown-menu")) return;
                const box = card.querySelector(".select-item");
                if (!box) return;
                event.preventDefault();
                box.checked = !box.checked;
                lastSelectionBox = box;
                updateSelectionBar();
            });
        });

        document.getElementById("selectAllVisible")?.addEventListener("click", () => {
            document.querySelectorAll(".select-item").forEach((box) => { box.checked = true; });
            updateSelectionBar();
            showToast("Selected all visible items", "success");
        });

        document.getElementById("invertSelection")?.addEventListener("click", () => {
            document.querySelectorAll(".select-item").forEach((box) => { box.checked = !box.checked; });
            updateSelectionBar();
        });

        document.getElementById("clearSelection")?.addEventListener("click", clearSelection);

        document.getElementById("bulkDownloadBtn")?.addEventListener("click", () => {
            const names = getSelectedNames();
            const url = getBrowser()?.dataset.downloadSelectedUrl;
            if (!url || names.length === 0) return;
            downloadSelectedFiles(names, url);
        });

        document.getElementById("bulkTrashBtn")?.addEventListener("click", async () => {
            const names = getSelectedNames();
            const url = getBrowser()?.dataset.deleteSelectedUrl;
            if (!url || names.length === 0) return;
            if (!confirm(`Move ${names.length} selected item(s) to trash?`)) return;
            const formData = new FormData();
            formData.append("current_path", getCurrentPath());
            names.forEach((name) => formData.append("selected_files", name));
            const response = await fetch(url, { method: "POST", body: formData });
            if (response.ok) window.location.reload();
            else alert("Could not move the selected items to trash.");
        });

        document.getElementById("bulkMoveBtn")?.addEventListener("click", () => openMoveCopyModal({
            paths: getSelectedPaths(),
            operation: "move",
        }));

        document.getElementById("bulkCopyBtn")?.addEventListener("click", () => openMoveCopyModal({
            paths: getSelectedPaths(),
            operation: "copy",
        }));

        updateSelectionBar();
    }

    function draggedPathsFromEvent(event) {
        if (state.draggedPaths.length > 0) return state.draggedPaths.slice();
        const serialized = event.dataTransfer?.getData("application/x-tamestorage-paths");
        if (serialized) {
            try {
                const paths = JSON.parse(serialized);
                if (Array.isArray(paths)) return paths.filter(Boolean);
            } catch (_error) {
                // Fall back to the legacy single-path payload.
            }
        }
        const path = event.dataTransfer?.getData("application/x-tamestorage-path");
        return path ? [path] : [];
    }

    function canDropPathsInto(paths, destination) {
        return paths.length > 0 && paths.every((source) => (
            source !== destination && !destination.startsWith(source + "/")
        ));
    }

    async function moveItems(sourcePaths, destinationFolder) {
        const url = getBrowser()?.dataset.moveUrl;
        const paths = Array.from(sourcePaths || []).filter(Boolean);
        if (!url || paths.length === 0) return;
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                source_paths: paths,
                destination_folder: destinationFolder || "",
                operation: "move",
            }),
        });
        let payload = {};
        try { payload = await response.json(); } catch (error) { payload = {}; }
        if (response.ok && payload.success !== false) {
            clearSelection();
            showToast(payload.message || `Moved ${paths.length} item${paths.length === 1 ? "" : "s"}`, "success");
            window.setTimeout(() => window.location.reload(), 450);
            return;
        }
        alert(payload.message || "Move failed.");
    }

    function createDragPreview(card, paths) {
        const preview = document.createElement("div");
        preview.className = "multi-drag-preview";
        const label = paths.length === 1 ? (card.dataset.name || "1 item") : `${paths.length} items`;
        preview.innerHTML = '<i class="fa-solid fa-layer-group" aria-hidden="true"></i><strong></strong>';
        preview.querySelector("strong").textContent = label;
        document.body.appendChild(preview);
        return preview;
    }

    function resetCardDragState() {
        document.querySelectorAll(".dragging-card").forEach((card) => card.classList.remove("dragging-card"));
        document.querySelectorAll(".drop-target-active").forEach((target) => target.classList.remove("drop-target-active"));
        document.querySelectorAll(".folder-drop-hint[data-default-text]").forEach((hint) => {
            hint.innerHTML = '<i class="fa-solid fa-arrow-down"></i> ' + hint.dataset.defaultText;
        });
        state.dragPreview?.remove();
        state.dragPreview = null;
        state.draggedPath = null;
        state.draggedPaths = [];
    }

    function setupCardDragDrop() {
        document.querySelectorAll(".file-card[data-path]").forEach((card) => {
            card.addEventListener("dragstart", (event) => {
                if (event.target.closest("button, input, textarea, select, .dropdown-menu")) {
                    event.preventDefault();
                    return;
                }
                const path = card.dataset.path;
                if (!path) return;
                const checkbox = card.querySelector(".select-item");
                const selectedPaths = getSelectedPaths();
                const paths = checkbox?.checked && selectedPaths.length > 0 ? selectedPaths : [path];
                state.draggedPath = paths[0];
                state.draggedPaths = paths;
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("application/x-tamestorage-path", path);
                event.dataTransfer.setData("application/x-tamestorage-paths", JSON.stringify(paths));
                event.dataTransfer.setData("text/plain", path);
                state.dragPreview = createDragPreview(card, paths);
                event.dataTransfer.setDragImage(state.dragPreview, 20, 20);
                requestAnimationFrame(() => {
                    const dragged = new Set(paths);
                    document.querySelectorAll(".file-card[data-path]").forEach((candidate) => {
                        candidate.classList.toggle("dragging-card", dragged.has(candidate.dataset.path));
                    });
                });
            });

            card.addEventListener("dragend", resetCardDragState);
        });

        document.querySelectorAll(".drop-target[data-drop-path]").forEach((target) => {
            const hint = target.querySelector(".folder-drop-hint");
            if (hint && !hint.dataset.defaultText) hint.dataset.defaultText = hint.textContent.trim();

            target.addEventListener("dragover", (event) => {
                if (!isInternalDrag(event)) return;
                const paths = draggedPathsFromEvent(event);
                const destination = target.dataset.dropPath || "";
                if (!canDropPathsInto(paths, destination)) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                target.classList.add("drop-target-active");
                if (hint) {
                    hint.innerHTML = `<i class="fa-solid fa-folder-arrow-down" aria-hidden="true"></i> Move ${paths.length} item${paths.length === 1 ? "" : "s"} here`;
                }
            });

            target.addEventListener("dragleave", () => {
                target.classList.remove("drop-target-active");
                if (hint?.dataset.defaultText) {
                    hint.innerHTML = '<i class="fa-solid fa-arrow-down"></i> ' + hint.dataset.defaultText;
                }
            });

            target.addEventListener("drop", (event) => {
                if (!isInternalDrag(event)) return;
                event.preventDefault();
                target.classList.remove("drop-target-active");
                const paths = draggedPathsFromEvent(event);
                const destination = target.dataset.dropPath || "";
                if (!canDropPathsInto(paths, destination)) return;
                if (hint?.dataset.defaultText) {
                    hint.innerHTML = '<i class="fa-solid fa-arrow-down"></i> ' + hint.dataset.defaultText;
                }
                moveItems(paths, destination);
            });
        });
    }

    function setHiddenSelectedPaths(paths) {
        const holder = document.getElementById("moveCopySelectedInputs");
        if (!holder) return;
        holder.innerHTML = "";
        paths.forEach((path) => {
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = "source_paths";
            input.value = path;
            holder.appendChild(input);
        });
    }

    function setOperation(value) {
        document.querySelectorAll('input[name="operation"]').forEach((radio) => {
            radio.checked = radio.value === value;
            radio.closest(".operation-card")?.classList.toggle("active", radio.checked);
        });
    }

    function refreshOperationCards() {
        document.querySelectorAll('input[name="operation"]').forEach((radio) => {
            radio.closest(".operation-card")?.classList.toggle("active", radio.checked);
        });
        const modal = document.getElementById("moveCopyModal");
        const count = Number(modal?.dataset.itemCount || "1");
        const operation = document.querySelector('input[name="operation"]:checked')?.value === "copy" ? "Copy" : "Move";
        const submit = document.getElementById("moveCopySubmit");
        if (submit) {
            const icon = operation === "Copy" ? "copy" : "folder-arrow-right";
            submit.innerHTML = `<i class="fa-solid fa-${icon}" aria-hidden="true"></i> ${operation} ${count > 1 ? `${count} items` : "item"}`;
        }
        const title = document.getElementById("moveCopyTitle");
        if (title && count > 1) title.textContent = `${operation} ${count} selected items`;
    }

    function openMoveCopyModal(options) {
        const paths = Array.from(options.paths || []).filter(Boolean);
        if (paths.length === 0) return;

        const source = document.getElementById("moveCopySource");
        const name = document.getElementById("newMoveName");
        const nameGroup = document.getElementById("moveCopyNameGroup");
        const title = document.getElementById("moveCopyTitle");
        const subtitle = document.getElementById("moveCopySubtitle");
        const destination = document.getElementById("destinationFolder");
        const countLabel = document.getElementById("moveCopyCountLabel");
        const itemList = document.getElementById("moveCopyItemList");
        const modal = document.getElementById("moveCopyModal");

        const isBulk = paths.length > 1 || options.bulk;
        const operationValue = options.operation || "move";
        if (modal) modal.dataset.itemCount = String(paths.length);
        if (source) source.value = isBulk ? "" : paths[0];
        setHiddenSelectedPaths(isBulk ? paths : []);
        if (name) {
            name.value = isBulk ? "" : (options.name || paths[0].split("/").pop() || "");
            name.disabled = isBulk;
        }
        if (nameGroup) nameGroup.hidden = isBulk;
        setOperation(operationValue);
        if (title) title.textContent = isBulk ? `${operationValue === "copy" ? "Copy" : "Move"} ${paths.length} selected items` : `Move or copy ${options.name || "item"}`;
        if (subtitle) subtitle.textContent = isBulk ? "Bulk operation" : "Single item operation";
        if (countLabel) countLabel.textContent = `${paths.length} item${paths.length === 1 ? "" : "s"} selected`;
        if (itemList) {
            const previewNames = paths.slice(0, 4).map((path) => path.split("/").pop()).join(", ");
            itemList.textContent = paths.length > 4 ? `${previewNames}, and ${paths.length - 4} more` : previewNames;
        }
        if (destination) destination.value = getCurrentPath();
        window.TamestorageFolderPicker?.setValue(destination, getCurrentPath(), { open: true }).catch(() => null);
        if (state.moveCopyModal) state.moveCopyModal.show();
        refreshOperationCards();
    }

    function setupMoveCopyModal() {
        const moveCopyElement = document.getElementById("moveCopyModal");
        state.moveCopyModal = moveCopyElement && window.bootstrap ? new bootstrap.Modal(moveCopyElement) : null;

        document.querySelectorAll('input[name="operation"]').forEach((radio) => {
            radio.addEventListener("change", refreshOperationCards);
        });
        refreshOperationCards();

        document.querySelectorAll(".js-move-copy").forEach((button) => {
            button.addEventListener("click", () => {
                openMoveCopyModal({
                    paths: [button.dataset.path || ""],
                    name: button.dataset.name || "",
                    operation: "move",
                });
            });
        });

        document.getElementById("moveCopyForm")?.addEventListener("submit", (event) => {
            const singleSource = document.getElementById("moveCopySource")?.value;
            const selectedPaths = Array.from(document.querySelectorAll('#moveCopySelectedInputs input[name="source_paths"]'));
            if (!singleSource && selectedPaths.length === 0) {
                event.preventDefault();
                alert("No source item selected.");
            }
        });
    }

    function setupQolActions() {
        document.querySelectorAll(".js-copy-path").forEach((button) => {
            button.addEventListener("click", async () => {
                const value = button.dataset.copy || "";
                if (!value) return;
                try {
                    await navigator.clipboard.writeText(value);
                    showToast("Path copied", "success");
                } catch (error) {
                    window.prompt("Copy this path", value);
                }
            });
        });

        document.querySelectorAll(".js-toggle-star").forEach((button) => {
            button.addEventListener("click", async () => {
                const url = getBrowser()?.dataset.toggleStarUrl;
                const path = button.dataset.path || "";
                if (!url || !path) return;
                const form = new FormData();
                form.append("target", path);
                const response = await fetch(url, { method: "POST", body: form });
                let payload = {};
                try { payload = await response.json(); } catch (error) { payload = {}; }
                if (!response.ok || payload.success === false) {
                    alert(payload.message || "Could not update starred state.");
                    return;
                }
                showToast(payload.starred ? "Added to starred" : "Removed from starred", "success");
                window.location.reload();
            });
        });
    }



    function setupKeyboardQol() {
        document.addEventListener("keydown", (event) => {
            const active = document.activeElement;
            const typing = active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName);
            if (typing) return;

            if (event.key === "/") {
                const search = document.querySelector(".bottom-search-form input[type='search']");
                if (search) {
                    event.preventDefault();
                    search.focus();
                }
            }

            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
                const boxes = Array.from(document.querySelectorAll(".select-item"));
                if (boxes.length) {
                    event.preventDefault();
                    boxes.forEach((box) => { box.checked = true; });
                    updateSelectionBar();
                    showToast("Selected all visible items", "success");
                }
            }

            if (event.key === "Escape") {
                clearSelection();
                document.querySelectorAll(".dropdown-menu.show").forEach((menu) => menu.classList.remove("show"));
            }
        });

        document.querySelectorAll(".file-card").forEach((card) => {
            card.addEventListener("dblclick", (event) => {
                if (event.target.closest("button, a, input, label, .dropdown-menu")) return;
                const main = card.querySelector(".file-main");
                if (main && main.href) window.location.href = main.href;
            });
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const progressElement = document.getElementById("progressModal");
        const renameElement = document.getElementById("renameModal");
        const uploadConflictElement = document.getElementById("uploadConflictModal");
        const exportProgressElement = document.getElementById("exportProgressModal");
        if (progressElement && window.bootstrap) state.progressModal = new bootstrap.Modal(progressElement);
        if (renameElement && window.bootstrap) state.renameModal = new bootstrap.Modal(renameElement);
        if (uploadConflictElement && window.bootstrap) state.uploadConflictModal = new bootstrap.Modal(uploadConflictElement);
        if (exportProgressElement && window.bootstrap) state.exportProgressModal = new bootstrap.Modal(exportProgressElement);
        document.getElementById("replaceUploadConflicts")?.addEventListener("click", () => retryConflictingUpload("replace"));
        document.getElementById("renameUploadConflicts")?.addEventListener("click", () => retryConflictingUpload("rename"));
        document.getElementById("cancelExportButton")?.addEventListener("click", () => state.exportAbortController?.abort());
        uploadConflictElement?.addEventListener("hidden.bs.modal", () => {
            if (!state.uploadXhr) {
                state.pendingConflictItems = [];
                state.pendingConflictNames = [];
            }
        });

        const openNew = getBrowser()?.dataset.openNew;
        if (openNew && window.bootstrap) {
            const modalElement = document.getElementById(openNew === "folder" ? "newFolderModal" : "newTextModal");
            if (modalElement) {
                const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
                modal.show();
                modalElement.addEventListener("shown.bs.modal", () => modalElement.querySelector("input")?.focus(), { once: true });
            }
        }

        setView(readLocalPreference("storageView", "grid"));
        const viewToggle = document.getElementById("viewToggleBtn");
        if (viewToggle) {
            viewToggle.addEventListener("click", () => {
                const browser = getBrowser();
                const nextView = browser && browser.classList.contains("browser-grid") ? "list" : "grid";
                setView(nextView);
            });
        }

        const fileInput = document.getElementById("fileInput");
        const folderInput = document.getElementById("folderInput");
        const clearUploadSelection = document.getElementById("clearUploadSelection");
        const cancelUploadButton = document.getElementById("cancelUploadButton");
        clearUploadSelection?.addEventListener("click", () => {
            if (fileInput) fileInput.value = "";
            if (folderInput) folderInput.value = "";
            setSelectedItems([]);
        });
        cancelUploadButton?.addEventListener("click", () => state.uploadXhr?.abort());

        if (fileInput) {
            fileInput.addEventListener("change", () => {
                if (folderInput) folderInput.value = "";
                setSelectedItems(Array.from(fileInput.files || []));
            });
        }

        if (folderInput) {
            folderInput.addEventListener("change", () => {
                if (fileInput) fileInput.value = "";
                setSelectedItems(Array.from(folderInput.files || []));
            });
        }

        const uploadForm = document.getElementById("uploadForm");
        if (uploadForm) {
            uploadForm.addEventListener("submit", (event) => {
                event.preventDefault();
                uploadFiles(state.selectedItems);
            });
        }

        const dropArea = document.getElementById("dropArea");
        document.querySelectorAll('a[href="#dropArea"]').forEach((link) => {
            link.addEventListener("click", () => dropArea?.classList.remove("upload-panel-hidden"));
        });
        if (dropArea) {
            ["dragenter", "dragover"].forEach((eventName) => {
                dropArea.addEventListener(eventName, (event) => {
                    if (isInternalDrag(event)) return;
                    event.preventDefault();
                    dropArea.classList.add("dragging");
                });
            });
            ["dragleave", "drop"].forEach((eventName) => {
                dropArea.addEventListener(eventName, (event) => {
                    if (isInternalDrag(event)) return;
                    event.preventDefault();
                    dropArea.classList.remove("dragging");
                });
            });
            dropArea.addEventListener("drop", async (event) => {
                if (isInternalDrag(event)) return;
                const items = await filesFromDataTransfer(event.dataTransfer);
                setSelectedItems(items);
                uploadFiles(items);
            });
        }

        // Accept files dropped anywhere in the file manager. The upload panel
        // remains available as a chooser, but it does not need to be open for a
        // desktop file or folder drop to start uploading.
        document.addEventListener("dragover", (event) => {
            if (isInternalDrag(event) || !hasFilePayload(event)) return;
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        });
        document.addEventListener("drop", async (event) => {
            if (isInternalDrag(event) || !hasFilePayload(event)) return;
            event.preventDefault();

            // The visible upload panel has its own drop handler.
            if (event.target.closest?.("#dropArea")) return;

            const items = await filesFromDataTransfer(event.dataTransfer);
            if (items.length === 0) {
                showToast("No files were found in that drop.", "error");
                return;
            }
            setSelectedItems(items);
            uploadFiles(items);
        });

        const shareElement = document.getElementById("shareModal");
        const shareModal = shareElement && window.bootstrap ? new bootstrap.Modal(shareElement) : null;
        const shareAccessMode = document.getElementById("shareAccessMode");
        const shareRestrictedField = document.querySelector(".share-restricted-field");

        const updateRestrictedField = () => {
            if (!shareRestrictedField || !shareAccessMode) return;
            shareRestrictedField.style.display = shareAccessMode.value === "restricted" ? "block" : "none";
        };

        if (shareAccessMode) {
            shareAccessMode.addEventListener("change", updateRestrictedField);
            updateRestrictedField();
        }

        document.querySelectorAll(".js-share").forEach((button) => {
            button.addEventListener("click", () => {
                const targetPath = document.getElementById("shareTargetPath");
                const targetName = document.getElementById("shareTargetName");
                const pathLabel = document.getElementById("shareTargetPathLabel");
                const title = document.getElementById("shareTitle");
                const icon = document.getElementById("shareTargetIcon");
                const permission = document.getElementById("sharePermission");
                const kind = button.dataset.kind || "file";

                if (targetPath) targetPath.value = button.dataset.path || "";
                if (targetName) targetName.textContent = button.dataset.name || "Selected item";
                if (pathLabel) pathLabel.textContent = button.dataset.path || "";
                if (title) title.textContent = `Share ${button.dataset.name || "item"}`;
                if (icon) icon.className = kind === "folder" ? "fa-solid fa-folder" : "fa-solid fa-file";
                if (permission && kind !== "folder" && ["upload", "manage"].includes(permission.value)) permission.value = "download";
                if (shareModal) shareModal.show();
            });
        });

        document.querySelectorAll(".js-copy").forEach((button) => {
            button.addEventListener("click", async () => {
                const value = button.dataset.copy || "";
                if (!value) return;
                try {
                    await navigator.clipboard.writeText(value);
                    const oldText = button.innerHTML;
                    button.innerHTML = '<i class="fa-solid fa-check"></i> Copied';
                    setTimeout(() => { button.innerHTML = oldText; }, 1600);
                } catch (error) {
                    window.prompt("Copy this link", value);
                }
            });
        });

        setupMoveCopyModal();
        setupBulkActions();
        setupCardDragDrop();
        setupQolActions();
        setupKeyboardQol();
        refreshDuplicateMenuActions();

        let resizeTimer = null;
        window.addEventListener("resize", () => {
            window.clearTimeout(resizeTimer);
            resizeTimer = window.setTimeout(refreshDuplicateMenuActions, 120);
        });
        document.addEventListener("show.bs.dropdown", refreshDuplicateMenuActions);

        document.querySelectorAll(".js-rename").forEach((button) => {
            button.addEventListener("click", () => {
                const form = document.getElementById("renameForm");
                const input = document.getElementById("newName");
                const title = document.getElementById("renameTitle");
                if (!form || !input || !title) return;
                form.action = button.dataset.action;
                input.value = button.dataset.name || "";
                title.textContent = "Rename " + (button.dataset.type || "item");
                if (state.renameModal) state.renameModal.show();
                setTimeout(() => input.focus(), 150);
            });
        });
    });
})();
