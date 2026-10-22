(function () {
    async function loadPdfJs() {
        if (window.pdfjsLib) return window.pdfjsLib;
        try {
            const module = await import("/static/vendor/pdfjs/pdf.min.mjs");
            module.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.mjs";
            window.pdfjsLib = module;
            return module;
        } catch (error) {
            console.error("PDF.js could not be loaded", error);
            return null;
        }
    }

    async function renderThumb(link, pdfjsLib) {
        const url = link.dataset.pdfUrl;
        const canvas = link.querySelector(".pdf-thumb-canvas");
        const fallback = link.querySelector(".pdf-thumb-fallback");
        if (!url || !canvas) return;
        try {
            const pdf = await pdfjsLib.getDocument(url).promise;
            const page = await pdf.getPage(1);
            const viewport = page.getViewport({ scale: 0.35 });
            const ratio = window.devicePixelRatio || 1;
            canvas.width = Math.floor(viewport.width * ratio);
            canvas.height = Math.floor(viewport.height * ratio);
            canvas.style.width = Math.floor(viewport.width) + "px";
            canvas.style.height = Math.floor(viewport.height) + "px";
            const context = canvas.getContext("2d");
            context.setTransform(ratio, 0, 0, ratio, 0, 0);
            await page.render({ canvasContext: context, viewport }).promise;
            link.classList.add("loaded");
            if (fallback) fallback.hidden = true;
        } catch (error) {
            link.classList.add("pdf-thumb-error");
        }
    }

    async function initializeViewer(viewer, pdfjsLib) {
        const url = viewer.dataset.pdfUrl;
        const canvas = viewer.querySelector("[data-pdf-canvas]");
        const fallback = viewer.querySelector("[data-pdf-fallback]");
        const pageLabel = viewer.querySelector("[data-pdf-page]");
        const pagesLabel = viewer.querySelector("[data-pdf-pages]");
        const previousButton = viewer.querySelector("[data-pdf-prev]");
        const nextButton = viewer.querySelector("[data-pdf-next]");
        const zoomOutButton = viewer.querySelector("[data-pdf-zoom-out]");
        const zoomInButton = viewer.querySelector("[data-pdf-zoom-in]");
        const fitButton = viewer.querySelector("[data-pdf-fit]");
        const expandButton = viewer.querySelector("[data-pdf-expand]");
        const zoomLabel = viewer.querySelector("[data-pdf-zoom-level]");
        const stage = viewer.querySelector(".pdf-canvas-stage");
        const previewPage = viewer.closest(".preview-page");
        if (!url || !canvas || !stage) return;

        let pdf;
        let pageNumber = 1;
        let zoomFactor = 1;
        let displayedZoomFactor = 1;
        let rendering = false;
        let pendingPage = null;
        let activePageNumber = null;
        let activePosition = null;
        let dragOrigin = null;

        function updateControls() {
            if (pageLabel) pageLabel.textContent = String(pageNumber);
            if (pagesLabel) pagesLabel.textContent = pdf ? String(pdf.numPages) : "?";
            if (previousButton) previousButton.disabled = !pdf || pageNumber <= 1;
            if (nextButton) nextButton.disabled = !pdf || pageNumber >= pdf.numPages;
            if (zoomOutButton) zoomOutButton.disabled = !pdf || zoomFactor <= 0.5;
            if (zoomInButton) zoomInButton.disabled = !pdf || zoomFactor >= 3;
            if (fitButton) fitButton.disabled = !pdf || Math.abs(zoomFactor - 1) < 0.01;
            if (zoomLabel) zoomLabel.textContent = `${Math.round(displayedZoomFactor * 100)}%`;
        }

        function calculateFitScale(page) {
            const unscaled = page.getViewport({ scale: 1 });
            const style = getComputedStyle(stage);
            const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
            const availableWidth = Math.max(1, stage.clientWidth - padding - 2);
            return Math.min(1.35, Math.max(0.3, availableWidth / unscaled.width));
        }

        function readingPosition() {
            const pageBounds = canvas.getBoundingClientRect();
            const stageBounds = stage.getBoundingClientRect();
            if (!pageBounds.width || !pageBounds.height) return { x: 0.5, y: 0.5 };
            return {
                x: Math.max(0, Math.min(1, (stageBounds.left + stage.clientWidth / 2 - pageBounds.left) / pageBounds.width)),
                y: Math.max(0, Math.min(1, (stageBounds.top + stage.clientHeight / 2 - pageBounds.top) / pageBounds.height)),
            };
        }

        function centerReadingPosition(position) {
            if (!position) return;
            const pageBounds = canvas.getBoundingClientRect();
            const stageBounds = stage.getBoundingClientRect();
            stage.scrollLeft += pageBounds.left + position.x * pageBounds.width - stageBounds.left - stage.clientWidth / 2;
            stage.scrollTop += pageBounds.top + position.y * pageBounds.height - stageBounds.top - stage.clientHeight / 2;
        }

        function updatePanState() {
            stage.classList.toggle("can-pan", stage.scrollWidth > stage.clientWidth + 1 || stage.scrollHeight > stage.clientHeight + 1);
        }

        async function renderPage(number, factor, position = null) {
            if (!pdf) return;
            rendering = true;
            activePageNumber = number;
            activePosition = position;
            viewer.classList.add("is-loading");
            try {
                const page = await pdf.getPage(number);
                const fitScale = calculateFitScale(page);
                const viewport = page.getViewport({ scale: fitScale * factor });
                const ratio = Math.min(window.devicePixelRatio || 1, 2);
                const context = canvas.getContext("2d", { alpha: false });
                canvas.width = Math.floor(viewport.width * ratio);
                canvas.height = Math.floor(viewport.height * ratio);
                canvas.style.width = Math.floor(viewport.width) + "px";
                canvas.style.height = Math.floor(viewport.height) + "px";
                context.setTransform(ratio, 0, 0, ratio, 0, 0);
                await page.render({ canvasContext: context, viewport }).promise;
                pageNumber = number;
                displayedZoomFactor = factor;
                updateControls();
                centerReadingPosition(position);
                updatePanState();
                if (fallback) fallback.hidden = true;
            } catch (error) {
                console.error("PDF page could not be rendered", error);
                if (fallback) fallback.hidden = false;
            } finally {
                rendering = false;
                activePageNumber = null;
                activePosition = null;
                viewer.classList.remove("is-loading");
                if (pendingPage !== null) {
                    const queued = pendingPage;
                    pendingPage = null;
                    renderPage(queued.number, queued.factor, queued.position);
                }
            }
        }

        function queuePage(number, position = null) {
            const bounded = Math.max(1, Math.min(pdf.numPages, number));
            const request = { number: bounded, factor: zoomFactor, position };
            if (rendering) pendingPage = request;
            else renderPage(request.number, request.factor, request.position);
        }

        function requestedPageNumber() {
            return pendingPage?.number ?? activePageNumber ?? pageNumber;
        }

        function zoomPosition() {
            return rendering ? (pendingPage?.position || activePosition || readingPosition()) : readingPosition();
        }

        try {
            pdf = await pdfjsLib.getDocument(url).promise;
            updateControls();
            await renderPage(1, zoomFactor);
        } catch (error) {
            console.error("PDF document could not be opened", error);
            if (fallback) fallback.hidden = false;
            return;
        }

        previousButton?.addEventListener("click", () => queuePage(requestedPageNumber() - 1));
        nextButton?.addEventListener("click", () => queuePage(requestedPageNumber() + 1));
        zoomOutButton?.addEventListener("click", () => {
            const position = zoomPosition();
            zoomFactor = Math.max(0.5, Number((zoomFactor / 1.2).toFixed(3)));
            queuePage(requestedPageNumber(), position);
        });
        zoomInButton?.addEventListener("click", () => {
            const position = zoomPosition();
            zoomFactor = Math.min(3, Number((zoomFactor * 1.2).toFixed(3)));
            queuePage(requestedPageNumber(), position);
        });
        fitButton?.addEventListener("click", () => {
            const position = zoomPosition();
            zoomFactor = 1;
            queuePage(requestedPageNumber(), position);
        });
        expandButton?.addEventListener("click", () => {
            const position = zoomPosition();
            const expanded = previewPage?.classList.toggle("pdf-expanded") || false;
            expandButton.setAttribute("aria-pressed", String(expanded));
            expandButton.setAttribute("aria-label", expanded ? "Show file details" : "Expand PDF preview");
            expandButton.setAttribute("title", expanded ? "Show file details" : "Expand PDF preview");
            expandButton.querySelector("i")?.classList.toggle("fa-compress", expanded);
            expandButton.querySelector("i")?.classList.toggle("fa-expand", !expanded);
            requestAnimationFrame(() => queuePage(requestedPageNumber(), position));
        });

        stage.addEventListener("pointerdown", (event) => {
            if (event.pointerType !== "mouse" || event.button !== 0 || !stage.classList.contains("can-pan")) return;
            dragOrigin = { x: event.clientX, y: event.clientY, left: stage.scrollLeft, top: stage.scrollTop };
            stage.setPointerCapture(event.pointerId);
            stage.classList.add("is-panning");
            event.preventDefault();
        });
        stage.addEventListener("pointermove", (event) => {
            if (!dragOrigin) return;
            stage.scrollLeft = dragOrigin.left - (event.clientX - dragOrigin.x);
            stage.scrollTop = dragOrigin.top - (event.clientY - dragOrigin.y);
        });
        function stopPanning() {
            dragOrigin = null;
            stage.classList.remove("is-panning");
        }
        stage.addEventListener("pointerup", stopPanning);
        stage.addEventListener("pointercancel", stopPanning);
        stage.addEventListener("lostpointercapture", stopPanning);

        viewer.addEventListener("keydown", (event) => {
            if (event.key === "ArrowLeft") queuePage(requestedPageNumber() - 1);
            if (event.key === "ArrowRight") queuePage(requestedPageNumber() + 1);
        });
    }

    document.addEventListener("DOMContentLoaded", async () => {
        const thumbLinks = Array.from(document.querySelectorAll(".pdf-thumb-link[data-pdf-url]"));
        const viewers = Array.from(document.querySelectorAll("[data-pdf-viewer]"));
        if (!thumbLinks.length && !viewers.length) return;

        const pdfjsLib = await loadPdfJs();
        if (!pdfjsLib) {
            viewers.forEach((viewer) => {
                const fallback = viewer.querySelector("[data-pdf-fallback]");
                if (fallback) fallback.hidden = false;
            });
            return;
        }

        thumbLinks.slice(0, 40).forEach((link) => renderThumb(link, pdfjsLib));
        viewers.forEach((viewer) => initializeViewer(viewer, pdfjsLib));
    });
})();
