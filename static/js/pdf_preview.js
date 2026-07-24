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
        const stage = viewer.querySelector(".pdf-canvas-stage");
        if (!url || !canvas || !stage) return;

        let pdf;
        let pageNumber = 1;
        let scale = 1;
        let rendering = false;
        let pendingPage = null;

        function updateControls() {
            if (pageLabel) pageLabel.textContent = String(pageNumber);
            if (pagesLabel) pagesLabel.textContent = pdf ? String(pdf.numPages) : "?";
            if (previousButton) previousButton.disabled = !pdf || pageNumber <= 1;
            if (nextButton) nextButton.disabled = !pdf || pageNumber >= pdf.numPages;
            if (zoomOutButton) zoomOutButton.disabled = scale <= 0.5;
            if (zoomInButton) zoomInButton.disabled = scale >= 3;
        }

        async function calculateFitScale(page) {
            const unscaled = page.getViewport({ scale: 1 });
            const availableWidth = Math.max(280, stage.clientWidth - 42);
            return Math.min(1.35, Math.max(0.5, availableWidth / unscaled.width));
        }

        async function renderPage(number, fit = false) {
            if (!pdf) return;
            rendering = true;
            viewer.classList.add("is-loading");
            try {
                const page = await pdf.getPage(number);
                if (fit) scale = await calculateFitScale(page);
                const viewport = page.getViewport({ scale });
                const ratio = Math.min(window.devicePixelRatio || 1, 2);
                const context = canvas.getContext("2d", { alpha: false });
                canvas.width = Math.floor(viewport.width * ratio);
                canvas.height = Math.floor(viewport.height * ratio);
                canvas.style.width = Math.floor(viewport.width) + "px";
                canvas.style.height = Math.floor(viewport.height) + "px";
                context.setTransform(ratio, 0, 0, ratio, 0, 0);
                await page.render({ canvasContext: context, viewport }).promise;
                pageNumber = number;
                updateControls();
                if (fallback) fallback.hidden = true;
            } catch (error) {
                console.error("PDF page could not be rendered", error);
                if (fallback) fallback.hidden = false;
            } finally {
                rendering = false;
                viewer.classList.remove("is-loading");
                if (pendingPage !== null) {
                    const queued = pendingPage;
                    pendingPage = null;
                    renderPage(queued);
                }
            }
        }

        function queuePage(number) {
            const bounded = Math.max(1, Math.min(pdf.numPages, number));
            if (rendering) pendingPage = bounded;
            else renderPage(bounded);
        }

        try {
            pdf = await pdfjsLib.getDocument(url).promise;
            updateControls();
            await renderPage(1, true);
        } catch (error) {
            console.error("PDF document could not be opened", error);
            if (fallback) fallback.hidden = false;
            return;
        }

        previousButton?.addEventListener("click", () => queuePage(pageNumber - 1));
        nextButton?.addEventListener("click", () => queuePage(pageNumber + 1));
        zoomOutButton?.addEventListener("click", () => {
            scale = Math.max(0.5, Number((scale - 0.2).toFixed(2)));
            renderPage(pageNumber);
        });
        zoomInButton?.addEventListener("click", () => {
            scale = Math.min(3, Number((scale + 0.2).toFixed(2)));
            renderPage(pageNumber);
        });

        viewer.addEventListener("keydown", (event) => {
            if (event.key === "ArrowLeft") queuePage(pageNumber - 1);
            if (event.key === "ArrowRight") queuePage(pageNumber + 1);
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
