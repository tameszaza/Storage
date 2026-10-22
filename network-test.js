(function () {
    "use strict";

    const HISTORY_KEY = "tamestorage.network-test.history.v1";
    const page = document.querySelector(".network-test-page");
    if (!page) return;

    const elements = {
        pingCount: document.getElementById("pingCount"),
        packetSize: document.getElementById("packetSize"),
        transferSize: document.getElementById("transferSize"),
        start: document.getElementById("startNetworkTest"),
        cancel: document.getElementById("cancelNetworkTest"),
        progress: document.getElementById("networkProgress"),
        stage: document.getElementById("networkStage"),
        detail: document.getElementById("networkDetail"),
        latency: document.getElementById("latencyResult"),
        latencyDetail: document.getElementById("latencyDetail"),
        jitter: document.getElementById("jitterResult"),
        loss: document.getElementById("lossResult"),
        lossDetail: document.getElementById("lossDetail"),
        download: document.getElementById("downloadResult"),
        upload: document.getElementById("uploadResult"),
        latencyRange: document.getElementById("latencyRange"),
        latencyLine: document.getElementById("latencyLine"),
        latencyDots: document.getElementById("latencyDots"),
        detailPacketSize: document.getElementById("detailPacketSize"),
        detailSamples: document.getElementById("detailSamples"),
        detailP95: document.getElementById("detailP95"),
        detailTransferSize: document.getElementById("detailTransferSize"),
        detailCompleted: document.getElementById("detailCompleted"),
        history: document.getElementById("networkHistory"),
        clearHistory: document.getElementById("clearNetworkHistory"),
    };

    let activeController = null;
    let cancelled = false;

    function percentile(values, p) {
        if (!values.length) return 0;
        const sorted = [...values].sort((a, b) => a - b);
        const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
        return sorted[index];
    }

    function median(values) {
        if (!values.length) return 0;
        const sorted = [...values].sort((a, b) => a - b);
        const middle = Math.floor(sorted.length / 2);
        return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }

    function jitter(values) {
        if (values.length < 2) return 0;
        let sum = 0;
        for (let index = 1; index < values.length; index += 1) {
            sum += Math.abs(values[index] - values[index - 1]);
        }
        return sum / (values.length - 1);
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(value % (1024 * 1024) ? 1 : 0)} MB`;
        if (value >= 1024) return `${(value / 1024).toFixed(value % 1024 ? 1 : 0)} KB`;
        return `${value} B`;
    }

    function setStage(label, detail, progress) {
        elements.stage.textContent = label;
        elements.detail.textContent = detail;
        elements.progress.style.width = `${Math.max(0, Math.min(100, progress))}%`;
    }

    function resetResults() {
        [elements.latency, elements.jitter, elements.loss, elements.download, elements.upload].forEach((element) => {
            element.textContent = "—";
        });
        elements.latencyLine.setAttribute("points", "");
        elements.latencyDots.innerHTML = "";
        elements.latencyRange.textContent = "—";
    }

    async function timedFetch(url, options = {}, timeoutMs = 5000) {
        const controller = new AbortController();
        activeController = controller;
        const timer = window.setTimeout(() => controller.abort(), timeoutMs);
        const started = performance.now();
        try {
            const response = await fetch(url, { ...options, cache: "no-store", signal: controller.signal });
            const buffer = await response.arrayBuffer();
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return { durationMs: performance.now() - started, bytes: buffer.byteLength, response };
        } finally {
            window.clearTimeout(timer);
            activeController = null;
        }
    }

    function streamCount(bytes) {
        // Four streams are enough to keep a gigabit LAN busy without creating
        // a large connection storm on the server.
        return Math.min(4, Math.max(2, Math.ceil(bytes / (16 * 1024 * 1024))));
    }

    function splitBytes(bytes, count) {
        const base = Math.floor(bytes / count);
        const remainder = bytes % count;
        return Array.from({ length: count }, (_, index) => base + (index < remainder ? 1 : 0));
    }

    async function streamDownload(url, bytes) {
        const count = streamCount(bytes);
        const controller = new AbortController();
        activeController = controller;
        const timer = window.setTimeout(() => controller.abort(), 30000);
        const started = performance.now();
        try {
            const sizes = splitBytes(bytes, count);
            await Promise.all(sizes.map(async (size, index) => {
                const response = await fetch(`${url}?size=${size}&stream=${index}&n=${Date.now()}`, {
                    cache: "no-store",
                    signal: controller.signal,
                });
                if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
                const reader = response.body.getReader();
                while (true) {
                    const { done } = await reader.read();
                    if (done) break;
                }
            }));
            return (bytes * 8) / ((performance.now() - started) / 1000) / 1_000_000;
        } finally {
            window.clearTimeout(timer);
            activeController = null;
        }
    }

    async function runPings(count, packetSize) {
        const samples = [];
        let failures = 0;
        for (let index = 0; index < count; index += 1) {
            if (cancelled) throw new Error("Test cancelled.");
            setStage("Measuring latency", `Sample ${index + 1} of ${count}`, 5 + (index / count) * 45);
            try {
                const result = await timedFetch(`${page.dataset.pingUrl}?size=${packetSize}&n=${Date.now()}-${index}`, {}, 3500);
                samples.push(result.durationMs);
            } catch (error) {
                if (cancelled) throw error;
                failures += 1;
            }
            await new Promise((resolve) => window.setTimeout(resolve, 80));
        }
        return { samples, failures };
    }

    async function runDownload(bytes) {
        const streams = streamCount(bytes);
        setStage("Measuring download", `${formatBytes(bytes)} · ${streams} streams`, 55);
        await timedFetch(`${page.dataset.downloadUrl}?size=1048576&w=${Date.now()}`, {}, 8000);
        return streamDownload(page.dataset.downloadUrl, bytes);
    }

    async function runUpload(bytes) {
        const streams = streamCount(bytes);
        setStage("Measuring upload", `${formatBytes(bytes)} · ${streams} streams`, 78);
        const sizes = splitBytes(bytes, streams);
        // Blob bodies can be reused by parallel fetches and avoid constructing
        // a separate full-size typed array for every request.
        const payload = new Blob([new Uint8Array(sizes[0])], { type: "application/octet-stream" });
        const started = performance.now();
        const controller = new AbortController();
        activeController = controller;
        const timer = window.setTimeout(() => controller.abort(), 30000);
        try {
            await Promise.all(sizes.map(async (size, index) => {
                const streamPayload = index === 0 ? payload : new Blob([new Uint8Array(size)], { type: "application/octet-stream" });
                const response = await fetch(`${page.dataset.uploadUrl}?stream=${index}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/octet-stream", Accept: "application/json" },
                    body: streamPayload,
                    cache: "no-store",
                    signal: controller.signal,
                });
                const data = await response.json();
                if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
            }));
            const durationMs = performance.now() - started;
            return (bytes * 8) / (durationMs / 1000) / 1_000_000;
        } finally {
            window.clearTimeout(timer);
            activeController = null;
        }
    }

    function renderLatencyChart(samples) {
        elements.latencyDots.innerHTML = "";
        if (!samples.length) {
            elements.latencyLine.setAttribute("points", "");
            elements.latencyRange.textContent = "No successful samples";
            return;
        }

        const width = 666;
        const height = 160;
        const xStart = 34;
        const yBottom = 190;
        const maxValue = Math.max(...samples, 1);
        const minValue = Math.min(...samples);
        const range = Math.max(1, maxValue - minValue);
        const points = samples.map((value, index) => {
            const x = xStart + (samples.length === 1 ? width / 2 : (index / (samples.length - 1)) * width);
            const y = yBottom - 10 - ((value - minValue) / range) * height;
            const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            circle.setAttribute("cx", x.toFixed(2));
            circle.setAttribute("cy", y.toFixed(2));
            circle.setAttribute("r", "3.5");
            elements.latencyDots.appendChild(circle);
            return `${x.toFixed(2)},${y.toFixed(2)}`;
        });
        elements.latencyLine.setAttribute("points", points.join(" "));
        elements.latencyRange.textContent = `${minValue.toFixed(1)}–${maxValue.toFixed(1)} ms`;
    }

    function readHistory() {
        try {
            const value = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
            return Array.isArray(value) ? value : [];
        } catch (error) {
            return [];
        }
    }

    function renderHistory() {
        const history = readHistory();
        elements.history.innerHTML = "";
        history.slice(0, 5).forEach((item) => {
            const row = document.createElement("div");
            row.className = "network-history-item";
            row.innerHTML = `<strong>${item.download.toFixed(1)}↓ · ${item.upload.toFixed(1)}↑ Mbps</strong><time>${new Date(item.completedAt).toLocaleString()}</time><span>${item.latency.toFixed(1)} ms · ${item.loss.toFixed(0)}% loss</span><span>${formatBytes(item.transferSize)}</span>`;
            elements.history.appendChild(row);
        });
    }

    function saveHistory(result) {
        const history = readHistory();
        history.unshift(result);
        try {
            localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 10)));
        } catch (_error) {
            // The test still completes when browser storage is unavailable.
        }
        renderHistory();
    }

    async function runFullTest() {
        if (activeController) return;
        cancelled = false;
        resetResults();
        elements.start.disabled = true;
        elements.cancel.disabled = false;

        const count = Number.parseInt(elements.pingCount.value, 10) || 20;
        const packetSize = Number.parseInt(elements.packetSize.value, 10) || 256;
        const transferSize = Number.parseInt(elements.transferSize.value, 10) || 5 * 1024 * 1024;

        try {
            const ping = await runPings(count, packetSize);
            if (!ping.samples.length) throw new Error("All latency requests failed.");
            const latencyValue = median(ping.samples);
            const p95 = percentile(ping.samples, 0.95);
            const jitterValue = jitter(ping.samples);
            const lossValue = (ping.failures / count) * 100;

            renderLatencyChart(ping.samples);
            elements.latency.textContent = `${latencyValue.toFixed(1)} ms`;
            elements.latencyDetail.textContent = `P95 ${p95.toFixed(1)} ms`;
            elements.jitter.textContent = `${jitterValue.toFixed(1)} ms`;
            elements.loss.textContent = `${lossValue.toFixed(0)}%`;
            elements.lossDetail.textContent = `${ping.samples.length}/${count} replies`;

            const download = await runDownload(transferSize);
            elements.download.textContent = `${download.toFixed(1)} Mbps`;
            const upload = await runUpload(transferSize);
            elements.upload.textContent = `${upload.toFixed(1)} Mbps`;

            const completedAt = Date.now();
            elements.detailPacketSize.textContent = formatBytes(packetSize);
            elements.detailSamples.textContent = `${ping.samples.length} of ${count}`;
            elements.detailP95.textContent = `${p95.toFixed(1)} ms`;
            elements.detailTransferSize.textContent = formatBytes(transferSize);
            elements.detailCompleted.textContent = new Date(completedAt).toLocaleString();
            setStage("Complete", "Browser-to-server test finished", 100);
            saveHistory({ latency: latencyValue, jitter: jitterValue, loss: lossValue, download, upload, packetSize, transferSize, completedAt });
        } catch (error) {
            setStage(cancelled ? "Cancelled" : "Test failed", error.message || "Could not complete the test.", 0);
        } finally {
            activeController = null;
            elements.start.disabled = false;
            elements.cancel.disabled = true;
        }
    }

    elements.start.addEventListener("click", runFullTest);
    elements.cancel.addEventListener("click", () => {
        cancelled = true;
        activeController?.abort();
    });
    elements.clearHistory.addEventListener("click", () => {
        try {
            localStorage.removeItem(HISTORY_KEY);
        } catch (_error) {
            // Ignore private-mode storage restrictions.
        }
        renderHistory();
    });
    renderHistory();
})();
