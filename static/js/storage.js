(function () {
    const state = {
        progressModal: null,
        renameModal: null,
        moveCopyModal: null,
        selectedItems: [],
        draggedPath: null,
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
            document.body.appendChild(stack);
        }
        const toast = document.createElement("div");
        toast.className = `mini-toast ${type}`;
        toast.textContent = message;
        stack.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add("show"));
        setTimeout(() => {
            toast.classList.remove("show");
            setTimeout(() => toast.remove(), 250);
        }, 2400);
    }

    function parentOf(path) {
        const parts = String(path || "").split("/").filter(Boolean);
        parts.pop();
        return parts.join("/");
    }

    function setView(view) {
        const browser = getBrowser();
        const icon = document.getElementById("viewToggleIcon");
        if (!browser || !icon) return;
        browser.classList.toggle("browser-grid", view === "grid");
        browser.classList.toggle("browser-list", view === "list");
        icon.className = view === "grid" ? "fa-solid fa-list" : "fa-solid fa-table-cells-large";
        localStorage.setItem("storageView", view);
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

    function setSelectedItems(items) {
        state.selectedItems = Array.from(items || []);
        const fileLabel = document.getElementById("fileLabel");
        const uploadButton = document.getElementById("uploadButton");
        if (fileLabel) fileLabel.textContent = describeSelection(state.selectedItems);
        if (uploadButton) uploadButton.disabled = state.selectedItems.length === 0;
    }

    function appendUploadItem(formData, item) {
        const file = item.file || item;
        const relativeName = item.relativePath || file.webkitRelativePath || file.name;
        formData.append("file", file, relativeName);
    }

    function uploadFiles(items) {
        const form = document.getElementById("uploadForm");
        const progressBar = document.getElementById("progressBar");
        const progressPercentage = document.getElementById("progressPercentage");
        const uploadButton = document.getElementById("uploadButton");
        const uploadItems = Array.from(items || []).filter(Boolean);
        if (!form || uploadItems.length === 0) return;

        const formData = new FormData();
        uploadItems.forEach((item) => appendUploadItem(formData, item));

        if (uploadButton) uploadButton.disabled = true;
        if (progressBar) progressBar.style.width = "0%";
        if (progressPercentage) progressPercentage.textContent = "0%";
        if (state.progressModal) state.progressModal.show();

        const xhr = new XMLHttpRequest();
        xhr.open("POST", form.action, true);
        xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable) return;
            const percent = Math.round((event.loaded / event.total) * 100);
            if (progressBar) progressBar.style.width = percent + "%";
            if (progressPercentage) progressPercentage.textContent = percent + "%";
        };
        xhr.onload = () => {
            if (state.progressModal) state.progressModal.hide();
            if (xhr.status >= 200 && xhr.status < 400) window.location.reload();
            else {
                if (uploadButton) uploadButton.disabled = false;
                alert("Upload failed. Please try again.");
            }
        };
        xhr.onerror = () => {
            if (state.progressModal) state.progressModal.hide();
            if (uploadButton) uploadButton.disabled = false;
            alert("Upload failed because the connection was interrupted.");
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
        return Array.from(event.dataTransfer?.types || []).includes("application/x-tamestorage-path");
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
        document.querySelectorAll(".select-item").forEach((box) => {
            box.addEventListener("change", updateSelectionBar);
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
            submitDynamicForm(url, { current_path: getCurrentPath(), selected_files: names });
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

    async function moveItem(sourcePath, destinationFolder) {
        const url = getBrowser()?.dataset.moveUrl;
        if (!url || !sourcePath) return;
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                source_path: sourcePath,
                destination_folder: destinationFolder || "",
                operation: "move",
            }),
        });
        let payload = {};
        try { payload = await response.json(); } catch (error) { payload = {}; }
        if (response.ok && payload.success !== false) {
            showToast(payload.message || "Moved item", "success");
            window.location.reload();
            return;
        }
        alert(payload.message || "Move failed.");
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
                state.draggedPath = path;
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("application/x-tamestorage-path", path);
                event.dataTransfer.setData("text/plain", path);
                requestAnimationFrame(() => card.classList.add("dragging-card"));
            });

            card.addEventListener("dragend", () => {
                card.classList.remove("dragging-card");
                state.draggedPath = null;
                document.querySelectorAll(".drop-target-active").forEach((target) => target.classList.remove("drop-target-active"));
            });
        });

        document.querySelectorAll(".drop-target[data-drop-path]").forEach((target) => {
            target.addEventListener("dragover", (event) => {
                if (!isInternalDrag(event)) return;
                const source = state.draggedPath || event.dataTransfer.getData("application/x-tamestorage-path");
                const destination = target.dataset.dropPath || "";
                if (!source || source === destination || destination.startsWith(source + "/")) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                target.classList.add("drop-target-active");
            });

            target.addEventListener("dragleave", () => {
                target.classList.remove("drop-target-active");
            });

            target.addEventListener("drop", (event) => {
                if (!isInternalDrag(event)) return;
                event.preventDefault();
                target.classList.remove("drop-target-active");
                const source = state.draggedPath || event.dataTransfer.getData("application/x-tamestorage-path");
                const destination = target.dataset.dropPath || "";
                if (!source || source === destination || destination.startsWith(source + "/")) return;
                moveItem(source, destination);
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

        const isBulk = paths.length > 1 || options.bulk;
        const operationValue = options.operation || "move";
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
        if (state.moveCopyModal) state.moveCopyModal.show();
        setTimeout(() => destination?.focus(), 150);
    }

    function setupMoveCopyModal() {
        const moveCopyElement = document.getElementById("moveCopyModal");
        state.moveCopyModal = moveCopyElement && window.bootstrap ? new bootstrap.Modal(moveCopyElement) : null;

        document.querySelectorAll('input[name="operation"]').forEach((radio) => {
            radio.addEventListener("change", refreshOperationCards);
        });
        refreshOperationCards();

        document.getElementById("useCurrentFolderBtn")?.addEventListener("click", () => {
            const destination = document.getElementById("destinationFolder");
            if (destination) destination.value = getCurrentPath();
        });
        document.getElementById("useParentFolderBtn")?.addEventListener("click", () => {
            const destination = document.getElementById("destinationFolder");
            if (destination) destination.value = parentOf(getCurrentPath());
        });
        document.getElementById("useRootFolderBtn")?.addEventListener("click", () => {
            const destination = document.getElementById("destinationFolder");
            if (destination) destination.value = "";
        });

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

    document.addEventListener("DOMContentLoaded", () => {
        const progressElement = document.getElementById("progressModal");
        const renameElement = document.getElementById("renameModal");
        if (progressElement && window.bootstrap) state.progressModal = new bootstrap.Modal(progressElement);
        if (renameElement && window.bootstrap) state.renameModal = new bootstrap.Modal(renameElement);

        setView(localStorage.getItem("storageView") || "grid");
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
