(function () {
    const state = {
        progressModal: null,
        renameModal: null,
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

    function uploadFiles(files) {
        const form = document.getElementById("uploadForm");
        const progressBar = document.getElementById("progressBar");
        const progressPercentage = document.getElementById("progressPercentage");
        if (!form || !files || files.length === 0) return;

        const formData = new FormData();
        Array.from(files).forEach((file) => formData.append("file", file));

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
            else alert("Upload failed. Please try again.");
        };
        xhr.onerror = () => {
            if (state.progressModal) state.progressModal.hide();
            alert("Upload failed because the connection was interrupted.");
        };
        xhr.send(formData);
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
        const fileLabel = document.getElementById("fileLabel");
        const uploadButton = document.getElementById("uploadButton");
        if (fileInput) {
            fileInput.addEventListener("change", () => {
                const count = fileInput.files.length;
                if (fileLabel) fileLabel.textContent = count === 0 ? "No file selected" : count === 1 ? fileInput.files[0].name : count + " files selected";
                if (uploadButton) uploadButton.disabled = count === 0;
            });
        }

        const uploadForm = document.getElementById("uploadForm");
        if (uploadForm) {
            uploadForm.addEventListener("submit", (event) => {
                event.preventDefault();
                uploadFiles(fileInput.files);
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
            dropArea.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));
        }

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
