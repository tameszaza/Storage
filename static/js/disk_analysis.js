(() => {
    "use strict";

    const palette = ["#265bd7", "#14a89c", "#8059e8", "#ec9b19", "#dc4d82", "#1497b8", "#718096"];
    const otherColor = "#94a3b8";
    const formatBytes = (value) => {
        let amount = Number(value) || 0;
        let unit = 0;
        while (amount >= 1024 && unit < 4) { amount /= 1024; unit += 1; }
        return `${amount.toFixed(unit ? 2 : 0)} ${["B", "KB", "MB", "GB", "TB"][unit]}`;
    };

    function setup(panel) {
        const status = panel.querySelector(".disk-analysis-status");
        if (!status) return;
        const list = panel.querySelector(".disk-analysis-list");
        const ring = panel.querySelector(".disk-analysis-ring");
        const crumbs = panel.querySelector(".disk-analysis-path");
        const refreshButton = panel.querySelector("[data-rescan]");
        let path = "";
        let busy = false;
        let timer = null;
        let loaded = false;
        let queuedLoad = false;
        let queuedRefresh = false;

        function navigate(nextPath) {
            path = nextPath;
            loaded = false;
            list.replaceChildren();
            ring.replaceChildren();
            renderPath();
            load();
        }

        function renderPath() {
            crumbs.replaceChildren();
            const parts = path ? path.split("/") : [];
            const labels = ["Disk", ...parts];
            labels.forEach((label, index) => {
                if (index) { const separator = document.createElement("span"); separator.textContent = "/"; crumbs.append(separator); }
                const button = document.createElement("button");
                button.type = "button";
                button.textContent = label;
                button.disabled = index === labels.length - 1;
                button.addEventListener("click", () => navigate(parts.slice(0, index).join("/")));
                crumbs.append(button);
            });
        }

        function render(data) {
            list.replaceChildren();
            ring.replaceChildren();
            const ordered = [...data.rows].sort((a, b) => b.bytes - a.bytes);
            const prominent = ordered.slice(0, 7);
            const remaining = ordered.slice(7);
            const remainingBytes = remaining.reduce((total, row) => total + row.bytes, 0);
            const chartRows = remainingBytes
                ? [...prominent, { name: "Other", bytes: remainingBytes }]
                : prominent;
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            svg.setAttribute("viewBox", "0 0 240 240");
            svg.setAttribute("role", "img");
            svg.setAttribute("aria-label", "Folder sizes, also listed beside the chart");
            const center = document.createElement("div");
            center.className = "disk-analysis-ring-label";
            const amount = document.createElement("strong");
            const caption = document.createElement("small");
            amount.textContent = formatBytes(data.total);
            caption.textContent = path ? path.split("/").at(-1) : "In files";
            center.append(amount, caption);
            let offset = 0;
            chartRows.filter(row => row.bytes > 0).forEach((row, index) => {
                const fraction = data.total ? row.bytes / data.total : 0;
                const color = index >= prominent.length ? otherColor : palette[index % palette.length];
                const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                for (const [name, value] of Object.entries({ cx: 120, cy: 120, r: 92, fill: "none", stroke: color, "stroke-width": 32, pathLength: 100, "stroke-dasharray": `${fraction * 100} ${100 - fraction * 100}`, "stroke-dashoffset": -offset })) circle.setAttribute(name, String(value));
                offset += fraction * 100;
                const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
                title.textContent = `${row.name}: ${formatBytes(row.bytes)}`;
                circle.append(title);
                svg.append(circle);
            });

            function appendRow(row, color, holder) {
                const fraction = data.total ? row.bytes / data.total : 0;
                const line = document.createElement(row.kind === "folder" ? "button" : "div");
                line.className = "disk-analysis-row";
                if (row.kind === "folder") {
                    line.type = "button";
                    line.title = `Open ${row.name}`;
                    line.addEventListener("click", () => navigate(row.path));
                }
                const swatch = document.createElement("span"); swatch.className = "swatch"; swatch.style.background = color;
                const name = document.createElement("span"); name.className = "name"; name.textContent = row.name;
                if (row.kind === "folder") { const arrow = document.createElement("i"); arrow.className = "fa-solid fa-chevron-right"; arrow.setAttribute("aria-hidden", "true"); name.append(arrow); }
                const size = document.createElement("span"); size.className = "size"; size.textContent = formatBytes(row.bytes);
                const percentage = document.createElement("small"); percentage.textContent = `${(fraction * 100).toFixed(1)}%`; size.append(percentage);
                line.append(swatch, name, size);
                holder.append(line);
            }
            prominent.forEach((row, index) => appendRow(row, palette[index % palette.length], list));
            if (remaining.length) {
                const more = document.createElement("details");
                more.className = "disk-analysis-more";
                const summary = document.createElement("summary");
                summary.textContent = `${remaining.length} more ${remaining.length === 1 ? "item" : "items"}`;
                const extra = document.createElement("div");
                remaining.forEach(row => appendRow(row, otherColor, extra));
                more.append(summary, extra);
                list.append(more);
            }
            if (!data.rows.length) status.textContent = "No files found in this folder.";
            ring.append(svg, center);
            renderPath();
        }

        async function load(refresh = false) {
            if (busy) {
                queuedLoad = true;
                queuedRefresh ||= refresh;
                return;
            }
            busy = true;
            const requestedPath = path;
            refreshButton.disabled = true;
            clearTimeout(timer);
            status.textContent = "Analysing folders…";
            try {
                const url = new URL(panel.dataset.url, window.location.origin);
                if (path) url.searchParams.set("path", path);
                const response = await fetch(url, { method: refresh ? "POST" : "GET", headers: { Accept: "application/json" }, cache: "no-store" });
                const data = await response.json();
                if (requestedPath !== path) {
                    queuedLoad = true;
                    return;
                }
                if (!response.ok || data.state === "error") throw new Error(data.error || "Analysis unavailable.");
                if (data.state === "scanning") {
                    status.textContent = "Analysing folders… You can keep using this page.";
                    if (panel.open) timer = window.setTimeout(() => load(), 2000);
                    return;
                }
                render(data);
                loaded = true;
                status.textContent = `${formatBytes(data.total)} in files · ${new Date(data.checked_at * 1000).toLocaleString()}${data.partial ? " · Partial scan" : ""}`;
            } catch (error) {
                if (requestedPath === path) status.textContent = error.message;
            } finally {
                busy = false;
                refreshButton.disabled = false;
                if (queuedLoad && panel.open) {
                    const refreshNext = queuedRefresh;
                    queuedLoad = false;
                    queuedRefresh = false;
                    queueMicrotask(() => load(refreshNext));
                }
            }
        }

        panel.addEventListener("toggle", () => { if (panel.open && !loaded) load(); else if (!panel.open) clearTimeout(timer); });
        refreshButton.addEventListener("click", () => load(true));
    }

    document.querySelectorAll(".disk-analysis").forEach(setup);
})();
