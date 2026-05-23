(function () {
    const state = {
        progressModal: null,
        renameModal: null,
        selectedItems: [],
    };

    function setView(view) {
        const browser = document.getElementById("fileBrowser");
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

    document.addEventListener("DOMContentLoaded", () => {
        const progressElement = document.getElementById("progressModal");
        const renameElement = document.getElementById("renameModal");
        if (progressElement && window.bootstrap) state.progressModal = new bootstrap.Modal(progressElement);
        if (renameElement && window.bootstrap) state.renameModal = new bootstrap.Modal(renameElement);

        setView(localStorage.getItem("storageView") || "grid");
        const viewToggle = document.getElementById("viewToggleBtn");
        if (viewToggle) {
            viewToggle.addEventListener("click", () => {
                const browser = document.getElementById("fileBrowser");
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
                    event.preventDefault();
                    dropArea.classList.add("dragging");
                });
            });
            ["dragleave", "drop"].forEach((eventName) => {
                dropArea.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    dropArea.classList.remove("dragging");
                });
            });
            dropArea.addEventListener("drop", async (event) => {
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

        

        const moveCopyElement = document.getElementById("moveCopyModal");
        const moveCopyModal = moveCopyElement && window.bootstrap ? new bootstrap.Modal(moveCopyElement) : null;
        document.querySelectorAll(".js-move-copy").forEach((button) => {
            button.addEventListener("click", () => {
                const source = document.getElementById("moveCopySource");
                const name = document.getElementById("newMoveName");
                if (source) source.value = button.dataset.path || "";
                if (name) name.value = button.dataset.name || "";
                if (moveCopyModal) moveCopyModal.show();
            });
        });

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
