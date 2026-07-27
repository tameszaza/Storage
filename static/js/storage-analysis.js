(function () {
    "use strict";

    const dataElement = document.getElementById("storageAnalysisData");
    const svg = document.getElementById("storageSunburstSvg");
    if (!dataElement || !svg) return;

    let payload;
    try {
        payload = JSON.parse(dataElement.textContent || "{}");
    } catch (_error) {
        return;
    }

    const root = payload.tree;
    if (!root || typeof root !== "object") return;

    const elements = {
        arcs: document.getElementById("sunburstArcs"),
        labels: document.getElementById("sunburstLabels"),
        center: document.getElementById("sunburstCenter"),
        centerName: document.getElementById("sunburstCenterName"),
        centerSize: document.getElementById("sunburstCenterSize"),
        centerHint: document.getElementById("sunburstCenterHint"),
        breadcrumbs: document.getElementById("sunburstBreadcrumbs"),
        back: document.getElementById("sunburstBack"),
        home: document.getElementById("sunburstHome"),
        open: document.getElementById("sunburstOpen"),
        branchList: document.getElementById("storageBranchList"),
        currentKind: document.getElementById("storageCurrentKind"),
        currentSize: document.getElementById("storageCurrentSize"),
        stage: document.getElementById("storageSunburst"),
        tooltip: document.getElementById("storageChartTooltip"),
        fileTypeArcs: document.getElementById("fileTypeArcs"),
        fileTypeList: document.getElementById("fileTypeList"),
        fileTypeCenterName: document.getElementById("fileTypeCenterName"),
        fileTypeCenterSize: document.getElementById("fileTypeCenterSize"),
    };

    const CENTER_RADIUS = 88;
    const OUTER_RADIUS = 282;
    const MAX_RINGS = 4;
    const MAX_VISIBLE_ARCS = 2500;
    const FULL_CIRCLE = Math.PI * 2;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    const state = {
        current: root,
        selected: null,
        geometry: new Map(),
        paths: new Map(),
        layout: [],
        animating: false,
        animationToken: 0,
        palette: [],
        paletteRgb: [],
        chartBackgroundRgb: [255, 255, 255],
        fileTypeSelected: null,
    };

    function formatBytes(value) {
        const bytes = Math.max(0, Number(value) || 0);
        if (bytes === 0) return "0 B";
        const units = ["B", "KB", "MB", "GB", "TB", "PB"];
        const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const amount = bytes / (1024 ** index);
        const digits = amount >= 100 || index === 0 ? 0 : amount >= 10 ? 1 : 2;
        return `${amount.toFixed(digits)} ${units[index]}`;
    }

    function percent(value, total) {
        if (!total) return "0%";
        const result = (Number(value) / Number(total)) * 100;
        return `${result >= 10 ? result.toFixed(1) : result.toFixed(2)}%`;
    }

    function normalizePath(value) {
        return String(value || "")
            .replace(/\\/g, "/")
            .split("/")
            .filter(Boolean)
            .join("/");
    }

    function joinPath(...parts) {
        return normalizePath(parts.filter(Boolean).join("/"));
    }

    function routePath(path) {
        return normalizePath(path)
            .split("/")
            .filter(Boolean)
            .map((part) => encodeURIComponent(part))
            .join("/");
    }

    function browserUrl(node) {
        const base = String(payload.browserBaseUrl || "/index").replace(/\/+$/, "");
        const path = routePath(joinPath(payload.basePath, node?.path));
        return path ? `${base}/${path}` : base;
    }

    function attachTreeMetadata(node, parent, depth) {
        node._parent = parent || null;
        node._depth = depth;
        const path = normalizePath(node.path);
        node.path = path;
        node.size = Math.max(0, Number(node.size) || 0);
        node.file_count = Math.max(0, Number(node.file_count) || 0);
        node.kind = "folder";
        node._uid = `folder:${path || "@root"}`;
        node.children = (Array.isArray(node.children) ? node.children : [])
            .filter((child) => child && child.kind !== "files");
        node.children.forEach((child) => attachTreeMetadata(child, node, depth + 1));
    }

    attachTreeMetadata(root, null, 0);

    function cssColor(name, fallback) {
        const value = getComputedStyle(document.body).getPropertyValue(name).trim();
        return value || fallback;
    }

    function parseColor(value) {
        const probe = document.createElement("span");
        probe.style.color = value;
        probe.style.position = "absolute";
        probe.style.visibility = "hidden";
        document.body.appendChild(probe);
        const resolved = getComputedStyle(probe).color;
        probe.remove();
        const match = resolved.match(/[\d.]+/g);
        if (!match || match.length < 3) return [91, 94, 230];
        return match.slice(0, 3).map(Number);
    }

    function mixRgb(sourceRgb, targetRgb, amount) {
        const clamped = Math.max(0, Math.min(1, amount));
        const rgb = sourceRgb.map((component, index) => Math.round(component + (targetRgb[index] - component) * clamped));
        return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
    }

    function refreshPalette() {
        state.palette = [
            cssColor("--primary", "#5b5ee6"),
            cssColor("--primary-2", "#1597d4"),
            cssColor("--success", "#16a34a"),
            cssColor("--warning", "#f59e0b"),
            cssColor("--danger", "#e11d48"),
            "#8b5cf6",
            "#0f9f9a",
            "#ea7c2b",
            "#d9469f",
            "#64748b",
            "#2563eb",
            "#65a30d",
        ];
        state.paletteRgb = state.palette.map(parseColor);
        state.chartBackgroundRgb = parseColor(cssColor("--card-solid", "#ffffff"));
    }

    function nodeColor(arc) {
        const base = state.paletteRgb[arc.branchIndex % state.paletteRgb.length] || [91, 94, 230];
        return mixRgb(base, state.chartBackgroundRgb, Math.min(0.48, (arc.depth - 1) * 0.13));
    }

    function treeDepth(node, remaining) {
        if (remaining <= 0) return 0;
        const folders = node.children.filter((child) => child.size > 0 && child.children.length > 0);
        if (!folders.length) return node.children.some((child) => child.size > 0) ? 1 : 0;
        return 1 + Math.max(...folders.map((child) => treeDepth(child, remaining - 1)));
    }

    function layoutSunburst(node) {
        const depthCount = Math.max(1, Math.min(MAX_RINGS, treeDepth(node, MAX_RINGS)));
        const ringWidth = (OUTER_RADIUS - CENTER_RADIUS) / depthCount;
        const arcs = [];
        let visibleCount = 0;

        function visit(parent, startAngle, endAngle, depth, inheritedBranch) {
            if (depth > depthCount || visibleCount >= MAX_VISIBLE_ARCS) return;
            const children = parent.children
                .filter((child) => child.size > 0)
                .slice()
                .sort((a, b) => b.size - a.size || a.name.localeCompare(b.name));
            const total = children.reduce((sum, child) => sum + child.size, 0);
            if (!total) return;

            let cursor = startAngle;
            children.forEach((child, index) => {
                if (visibleCount >= MAX_VISIBLE_ARCS) return;
                const childSpan = (endAngle - startAngle) * (child.size / total);
                if (childSpan <= 0) return;
                const branchIndex = depth === 1 ? index : inheritedBranch;
                const arc = {
                    uid: child._uid,
                    node: child,
                    start: cursor,
                    end: cursor + childSpan,
                    inner: CENTER_RADIUS + ((depth - 1) * ringWidth),
                    outer: CENTER_RADIUS + (depth * ringWidth),
                    depth,
                    branchIndex,
                };
                arcs.push(arc);
                visibleCount += 1;
                if (child.children.length && depth < depthCount) {
                    visit(child, arc.start, arc.end, depth + 1, branchIndex);
                }
                cursor += childSpan;
            });
        }

        visit(node, 0, FULL_CIRCLE, 1, 0);
        return arcs;
    }

    function polar(radius, angle) {
        const adjusted = angle - (Math.PI / 2);
        return {
            x: radius * Math.cos(adjusted),
            y: radius * Math.sin(adjusted),
        };
    }

    function arcPath(geometry) {
        const start = Number(geometry.start) || 0;
        const rawEnd = Number(geometry.end) || start;
        const end = Math.min(rawEnd, start + FULL_CIRCLE - 0.00001);
        const inner = Math.max(0.01, Number(geometry.inner) || CENTER_RADIUS);
        const outer = Math.max(inner + 0.01, Number(geometry.outer) || inner + 0.01);
        const span = Math.max(0.00001, end - start);
        const large = span > Math.PI ? 1 : 0;
        const outerStart = polar(outer, start);
        const outerEnd = polar(outer, end);
        const innerEnd = polar(inner, end);
        const innerStart = polar(inner, start);
        return [
            `M ${outerStart.x.toFixed(3)} ${outerStart.y.toFixed(3)}`,
            `A ${outer.toFixed(3)} ${outer.toFixed(3)} 0 ${large} 1 ${outerEnd.x.toFixed(3)} ${outerEnd.y.toFixed(3)}`,
            `L ${innerEnd.x.toFixed(3)} ${innerEnd.y.toFixed(3)}`,
            `A ${inner.toFixed(3)} ${inner.toFixed(3)} 0 ${large} 0 ${innerStart.x.toFixed(3)} ${innerStart.y.toFixed(3)}`,
            "Z",
        ].join(" ");
    }

    function interpolateGeometry(from, to, progress) {
        return {
            start: from.start + ((to.start - from.start) * progress),
            end: from.end + ((to.end - from.end) * progress),
            inner: from.inner + ((to.inner - from.inner) * progress),
            outer: from.outer + ((to.outer - from.outer) * progress),
        };
    }

    function collapsedGeometry(anchor) {
        const middle = anchor ? (anchor.start + anchor.end) / 2 : 0;
        const radius = anchor ? (anchor.inner + anchor.outer) / 2 : CENTER_RADIUS;
        return {
            start: middle,
            end: middle + 0.00001,
            inner: radius,
            outer: radius + 0.01,
        };
    }

    function easeInOutCubic(value) {
        return value < 0.5
            ? 4 * value * value * value
            : 1 - (Math.pow(-2 * value + 2, 3) / 2);
    }

    function truncate(value, maximum) {
        const text = String(value || "");
        if (text.length <= maximum) return text;
        return `${text.slice(0, Math.max(1, maximum - 1)).trim()}…`;
    }

    function accessibleNodeLabel(node) {
        const action = node.children.length ? "Activate to zoom in." : "Activate to inspect.";
        return `Folder: ${node.name}. ${formatBytes(node.size)}. ${action}`;
    }

    function createSegment(uid) {
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.classList.add("sunburst-segment");
        path.setAttribute("role", "button");
        path.setAttribute("tabindex", "0");
        path.dataset.uid = uid;
        path.addEventListener("click", () => activateNode(path._node));
        path.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                activateNode(path._node);
            }
        });
        path.addEventListener("pointerenter", (event) => showNodeTooltip(path._node, event));
        path.addEventListener("pointermove", positionTooltip);
        path.addEventListener("pointerleave", hideTooltip);
        path.addEventListener("focus", () => {
            const node = path._node;
            elements.centerName.textContent = truncate(node.name, 20);
            elements.centerSize.textContent = formatBytes(node.size);
        });
        path.addEventListener("blur", () => updateCenter());
        elements.arcs.appendChild(path);
        return path;
    }

    function renderLabels(layout) {
        elements.labels.replaceChildren();
        layout.forEach((arc) => {
            const span = arc.end - arc.start;
            const ringWidth = arc.outer - arc.inner;
            if (span < 0.18 || ringWidth < 34) return;
            const middle = (arc.start + arc.end) / 2;
            const radius = arc.inner + (ringWidth * 0.55);
            const point = polar(radius, middle);
            let degrees = (middle * 180 / Math.PI) - 90;
            if (degrees > 90 && degrees < 270) degrees += 180;
            const available = Math.max(5, Math.floor((span * radius) / 7.5));
            const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
            text.classList.add("sunburst-label");
            text.setAttribute("x", point.x.toFixed(2));
            text.setAttribute("y", point.y.toFixed(2));
            text.setAttribute("text-anchor", "middle");
            text.setAttribute("dominant-baseline", "middle");
            text.setAttribute("transform", `rotate(${degrees.toFixed(2)} ${point.x.toFixed(2)} ${point.y.toFixed(2)})`);
            text.textContent = truncate(arc.node.name, Math.min(18, available));
            elements.labels.appendChild(text);
        });
    }

    function renderSunburst(options) {
        const direction = options?.direction || "none";
        const previousRoot = options?.previousRoot || null;
        const nextLayout = layoutSunburst(state.current);
        const nextGeometry = new Map(nextLayout.map((arc) => [arc.uid, arc]));
        const previousGeometry = state.geometry;
        let anchor = null;

        if (direction === "in") {
            anchor = previousGeometry.get(state.current._uid) || null;
        } else if (direction === "out" && previousRoot) {
            anchor = nextGeometry.get(previousRoot._uid) || null;
        }

        const allUids = new Set([...previousGeometry.keys(), ...nextGeometry.keys()]);
        const transitions = [];
        allUids.forEach((uid) => {
            const oldArc = previousGeometry.get(uid);
            const newArc = nextGeometry.get(uid);
            let path = state.paths.get(uid);
            if (!path) {
                path = createSegment(uid);
                state.paths.set(uid, path);
            }
            const node = newArc?.node || oldArc?.node;
            path._node = node;
            path.setAttribute("aria-label", accessibleNodeLabel(node));
            path.style.fill = newArc ? nodeColor(newArc) : oldArc ? nodeColor(oldArc) : state.palette[0];
            path.classList.toggle("is-selected", state.selected?._uid === uid);
            path.classList.toggle("is-dimmed", Boolean(state.selected && state.selected._uid !== uid));
            path.hidden = false;
            elements.arcs.appendChild(path);

            transitions.push({
                uid,
                path,
                from: oldArc || collapsedGeometry(anchor || newArc),
                to: newArc || collapsedGeometry(anchor || oldArc),
                entering: !oldArc && Boolean(newArc),
                leaving: Boolean(oldArc) && !newArc,
            });
        });

        elements.labels.replaceChildren();
        const token = ++state.animationToken;
        const duration = reducedMotion.matches || direction === "none" ? 0 : 430;
        const startedAt = performance.now();
        state.animating = duration > 0;

        function frame(now) {
            if (token !== state.animationToken) return;
            const rawProgress = duration === 0 ? 1 : Math.min(1, (now - startedAt) / duration);
            const progress = easeInOutCubic(rawProgress);
            transitions.forEach((transition) => {
                transition.path.setAttribute("d", arcPath(interpolateGeometry(transition.from, transition.to, progress)));
                if (transition.entering) transition.path.style.opacity = String(progress);
                else if (transition.leaving) transition.path.style.opacity = String(1 - progress);
                else transition.path.style.opacity = "1";
            });

            if (rawProgress < 1) {
                requestAnimationFrame(frame);
                return;
            }

            transitions.forEach((transition) => {
                if (transition.leaving) {
                    transition.path.remove();
                    state.paths.delete(transition.uid);
                } else {
                    transition.path.style.opacity = "1";
                }
            });
            state.geometry = nextGeometry;
            state.layout = nextLayout;
            state.animating = false;
            renderLabels(nextLayout);
        }

        requestAnimationFrame(frame);
        updateNavigation();
        renderBranchList();
        updateCenter();
    }

    function activateNode(node) {
        if (!node || state.animating) return;
        if (node.kind === "folder" && node.children.some((child) => child.size > 0)) {
            navigateTo(node, "in");
            return;
        }
        state.selected = state.selected?._uid === node._uid ? null : node;
        applySelection();
        updateCenter();
        renderBranchList();
    }

    function applySelection() {
        state.paths.forEach((path, uid) => {
            path.classList.toggle("is-selected", state.selected?._uid === uid);
            path.classList.toggle("is-dimmed", Boolean(state.selected && state.selected._uid !== uid));
        });
    }

    function navigateTo(node, direction) {
        if (!node || node === state.current || state.animating) return;
        const previousRoot = state.current;
        state.current = node;
        state.selected = null;
        hideTooltip();
        renderSunburst({ direction, previousRoot });
    }

    function navigateParent() {
        if (state.current._parent) navigateTo(state.current._parent, "out");
    }

    function updateCenter() {
        const node = state.selected || state.current;
        elements.centerName.textContent = truncate(node.name || "Root", 20);
        elements.centerSize.textContent = formatBytes(node.size);
        const canGoBack = Boolean(state.current._parent);
        elements.centerHint.textContent = state.selected
            ? "Selected"
            : canGoBack ? `\u2191 ${truncate(state.current._parent.name, 15)}` : "Folder";
        elements.center.classList.toggle("is-root", !canGoBack);
        elements.center.setAttribute("aria-disabled", canGoBack ? "false" : "true");
        elements.currentKind.textContent = state.selected ? "Folder" : "Current folder";
        elements.currentSize.textContent = formatBytes(node.size);
    }

    function updateNavigation() {
        const atRoot = state.current === root;
        elements.back.disabled = atRoot;
        elements.home.disabled = atRoot;
        elements.open.href = browserUrl(state.current);

        const chain = [];
        let cursor = state.current;
        while (cursor) {
            chain.unshift(cursor);
            cursor = cursor._parent;
        }
        elements.breadcrumbs.replaceChildren();
        chain.forEach((node, index) => {
            if (index > 0) {
                const separator = document.createElement("i");
                separator.className = "fa-solid fa-chevron-right";
                separator.setAttribute("aria-hidden", "true");
                elements.breadcrumbs.appendChild(separator);
            }
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = node.name || "Root";
            if (node === state.current) button.setAttribute("aria-current", "page");
            button.addEventListener("click", () => {
                if (node !== state.current) navigateTo(node, "out");
            });
            elements.breadcrumbs.appendChild(button);
        });
        requestAnimationFrame(() => {
            elements.breadcrumbs.scrollLeft = elements.breadcrumbs.scrollWidth;
        });
    }

    function immediateArc(node) {
        return state.layout.find((arc) => arc.node === node) || null;
    }

    function renderBranchList() {
        elements.branchList.replaceChildren();
        const children = state.current.children
            .filter((child) => child.size > 0)
            .slice()
            .sort((a, b) => b.size - a.size || a.name.localeCompare(b.name));
        if (!children.length) {
            const empty = document.createElement("p");
            empty.className = "storage-empty-state";
            empty.textContent = "No subfolders";
            elements.branchList.appendChild(empty);
            return;
        }

        children.forEach((node) => {
            const arc = immediateArc(node);
            const color = arc ? nodeColor(arc) : state.palette[0];
            const button = document.createElement("button");
            button.type = "button";
            button.className = "storage-branch-item";
            button.classList.toggle("is-selected", state.selected?._uid === node._uid);
            button.setAttribute("aria-label", accessibleNodeLabel(node));

            const swatch = document.createElement("span");
            swatch.className = "storage-branch-swatch";
            swatch.style.background = color;
            swatch.style.color = color;

            const copy = document.createElement("span");
            copy.className = "storage-branch-copy";
            const name = document.createElement("strong");
            name.textContent = node.name;
            const bar = document.createElement("span");
            bar.className = "storage-branch-bar";
            const fill = document.createElement("span");
            fill.style.background = color;
            fill.style.width = percent(node.size, state.current.size);
            bar.appendChild(fill);
            copy.append(name, bar);

            const value = document.createElement("span");
            value.className = "storage-branch-value";
            value.textContent = formatBytes(node.size);

            button.append(swatch, copy, value);
            button.addEventListener("click", () => activateNode(node));
            button.addEventListener("pointerenter", () => highlightSegment(node._uid, true));
            button.addEventListener("pointerleave", () => highlightSegment(node._uid, false));
            elements.branchList.appendChild(button);
        });
    }

    function highlightSegment(uid, highlighted) {
        const path = state.paths.get(uid);
        if (path) path.classList.toggle("is-selected", highlighted || state.selected?._uid === uid);
    }

    function showNodeTooltip(node, event) {
        if (!node || !elements.tooltip) return;
        elements.tooltip.replaceChildren();
        const title = document.createElement("strong");
        title.textContent = node.name;
        const detail = document.createElement("span");
        detail.textContent = `${formatBytes(node.size)} · ${percent(node.size, state.current.size)}`;
        elements.tooltip.append(title, detail);
        elements.tooltip.hidden = false;
        positionTooltip(event);
    }

    function positionTooltip(event) {
        if (!elements.tooltip || elements.tooltip.hidden || !event) return;
        const bounds = elements.stage.getBoundingClientRect();
        const tooltipBounds = elements.tooltip.getBoundingClientRect();
        let left = event.clientX - bounds.left;
        let top = event.clientY - bounds.top;
        if (left + tooltipBounds.width + 22 > bounds.width) left -= tooltipBounds.width + 24;
        if (top + tooltipBounds.height + 22 > bounds.height) top -= tooltipBounds.height + 24;
        elements.tooltip.style.left = `${Math.max(4, left)}px`;
        elements.tooltip.style.top = `${Math.max(4, top)}px`;
    }

    function hideTooltip() {
        if (elements.tooltip) elements.tooltip.hidden = true;
    }

    function prepareFileTypes(rows) {
        const items = Array.isArray(rows)
            ? rows
                .map((row) => ({ name: String(row.name || "Unknown"), size: Math.max(0, Number(row.size) || 0) }))
                .filter((row) => row.size > 0)
                .sort((a, b) => b.size - a.size)
            : [];
        if (items.length <= 12) return items;
        const visible = items.slice(0, 11);
        visible.push({
            name: "Other",
            size: items.slice(11).reduce((sum, item) => sum + item.size, 0),
        });
        return visible;
    }

    function donutPath(start, end, inner, outer) {
        return arcPath({ start, end, inner, outer });
    }

    function renderFileTypes() {
        const items = prepareFileTypes(payload.fileTypes);
        const total = items.reduce((sum, item) => sum + item.size, 0);
        elements.fileTypeArcs.replaceChildren();
        elements.fileTypeList.replaceChildren();
        elements.fileTypeCenterSize.textContent = formatBytes(total);
        if (!items.length || !total) {
            const empty = document.createElement("p");
            empty.className = "storage-empty-state";
            empty.textContent = "No files";
            elements.fileTypeList.appendChild(empty);
            return;
        }

        let cursor = 0;
        const segments = [];
        items.forEach((item, index) => {
            const span = FULL_CIRCLE * (item.size / total);
            const segment = document.createElementNS("http://www.w3.org/2000/svg", "path");
            segment.classList.add("file-type-segment");
            segment.setAttribute("role", "button");
            segment.setAttribute("tabindex", "0");
            segment.setAttribute("aria-label", `${item.name}: ${formatBytes(item.size)}, ${percent(item.size, total)}. Activate to inspect.`);
            const color = state.palette[index % state.palette.length];
            segment.style.fill = color;
            const target = { start: cursor, end: cursor + span, inner: 92, outer: 164 };
            segment.setAttribute("d", donutPath(cursor, cursor + 0.00001, 92, 164));
            segment._item = item;
            segment._target = target;
            segment.addEventListener("click", () => selectFileType(item, segment, segments));
            segment.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    selectFileType(item, segment, segments);
                }
            });
            segment.addEventListener("pointerenter", () => previewFileType(item));
            segment.addEventListener("pointerleave", restoreFileTypeCenter);
            segment.addEventListener("focus", () => previewFileType(item));
            segment.addEventListener("blur", restoreFileTypeCenter);
            elements.fileTypeArcs.appendChild(segment);
            segments.push(segment);

            const button = document.createElement("button");
            button.type = "button";
            button.className = "file-type-item";
            button.dataset.fileType = item.name;
            const swatch = document.createElement("span");
            swatch.className = "file-type-swatch";
            swatch.style.background = color;
            swatch.style.color = color;
            const copy = document.createElement("span");
            copy.className = "file-type-copy";
            const name = document.createElement("strong");
            name.textContent = item.name;
            const bar = document.createElement("span");
            bar.className = "file-type-bar";
            const fill = document.createElement("span");
            fill.style.background = color;
            fill.style.width = percent(item.size, total);
            bar.appendChild(fill);
            copy.append(name, bar);
            const value = document.createElement("span");
            value.className = "file-type-value";
            value.textContent = formatBytes(item.size);
            button.append(swatch, copy, value);
            button.addEventListener("click", () => selectFileType(item, segment, segments));
            button.addEventListener("pointerenter", () => previewFileType(item));
            button.addEventListener("pointerleave", restoreFileTypeCenter);
            elements.fileTypeList.appendChild(button);
            cursor += span;
        });

        const duration = reducedMotion.matches ? 0 : 520;
        const startedAt = performance.now();
        function animate(now) {
            const raw = duration === 0 ? 1 : Math.min(1, (now - startedAt) / duration);
            const progress = easeInOutCubic(raw);
            segments.forEach((segment) => {
                const target = segment._target;
                segment.setAttribute("d", donutPath(target.start, target.start + ((target.end - target.start) * progress), target.inner, target.outer));
            });
            if (raw < 1) requestAnimationFrame(animate);
        }
        requestAnimationFrame(animate);

        function selectFileType(item, segment, allSegments) {
            const deselecting = state.fileTypeSelected === item.name;
            state.fileTypeSelected = deselecting ? null : item.name;
            allSegments.forEach((candidate) => {
                const selected = !deselecting && candidate === segment;
                candidate.classList.toggle("is-selected", selected);
                candidate.classList.toggle("is-dimmed", !deselecting && candidate !== segment);
            });
            elements.fileTypeList.querySelectorAll(".file-type-item").forEach((button) => {
                button.classList.toggle("is-selected", !deselecting && button.dataset.fileType === item.name);
            });
            restoreFileTypeCenter();
        }

        function previewFileType(item) {
            elements.fileTypeCenterName.textContent = truncate(item.name, 17);
            elements.fileTypeCenterSize.textContent = `${formatBytes(item.size)} · ${percent(item.size, total)}`;
        }

        function restoreFileTypeCenter() {
            const selected = items.find((item) => item.name === state.fileTypeSelected);
            if (selected) previewFileType(selected);
            else {
                elements.fileTypeCenterName.textContent = "All files";
                elements.fileTypeCenterSize.textContent = formatBytes(total);
            }
        }
    }

    elements.back.addEventListener("click", navigateParent);
    elements.home.addEventListener("click", () => {
        if (state.current !== root) navigateTo(root, "out");
    });
    elements.center.addEventListener("click", navigateParent);
    elements.center.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            navigateParent();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && state.current._parent && !state.animating) navigateParent();
    });

    refreshPalette();
    renderSunburst({ direction: "none" });
    renderFileTypes();

    const themeObserver = new MutationObserver(() => {
        refreshPalette();
        state.layout.forEach((arc) => {
            const path = state.paths.get(arc.uid);
            if (path) path.style.fill = nodeColor(arc);
        });
        renderBranchList();
        renderFileTypes();
    });
    themeObserver.observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });
})();
