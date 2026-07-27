(function () {
    const endpointCache = new Map();

    function fetchFolders(endpoint) {
        if (!endpointCache.has(endpoint)) {
            endpointCache.set(endpoint, fetch(endpoint, {
                headers: { "Accept": "application/json" },
            }).then(async (response) => {
                const payload = await response.json();
                if (!response.ok || !payload.success) {
                    throw new Error(payload.message || "Could not load folders.");
                }
                return payload;
            }));
        }
        return endpointCache.get(endpoint);
    }

    function pathLabel(path, state) {
        const folder = state.byPath.get(path);
        if (folder) return folder.label;
        return path || state.rootLabel || "Storage root";
    }

    function setPanelOpen(picker, open) {
        if (picker.dataset.folderPickerExpanded === "true") open = true;
        const trigger = picker.querySelector("[data-folder-picker-trigger]");
        const panel = picker.querySelector("[data-folder-picker-panel]");
        if (!trigger || !panel) return;
        panel.hidden = !open;
        trigger.setAttribute("aria-expanded", String(open));
    }

    function renderBreadcrumbs(picker, state) {
        const holder = picker.querySelector("[data-folder-picker-breadcrumbs]");
        if (!holder) return;
        holder.innerHTML = "";

        const chain = [];
        let cursor = state.byPath.get(state.currentPath);
        while (cursor) {
            chain.unshift(cursor);
            cursor = cursor.parent === null ? null : state.byPath.get(cursor.parent);
        }
        if (!chain.length && state.byPath.has(state.rootPath)) chain.push(state.byPath.get(state.rootPath));

        chain.forEach((folder, index) => {
            if (index) {
                const separator = document.createElement("i");
                separator.className = "fa-solid fa-chevron-right folder-picker-crumb-separator";
                separator.setAttribute("aria-hidden", "true");
                holder.appendChild(separator);
            }
            const button = document.createElement("button");
            button.type = "button";
            button.className = "folder-picker-crumb";
            button.textContent = folder.name;
            button.addEventListener("click", () => navigate(picker, state, folder.path));
            holder.appendChild(button);
        });
    }

    function makeFolderRow(picker, state, folder, searching) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "folder-picker-row";
        button.setAttribute("role", "option");
        button.innerHTML = `
            <span class="folder-picker-row-icon"><i class="fa-solid fa-folder" aria-hidden="true"></i></span>
            <span class="folder-picker-row-copy">
                <strong></strong>
                <small></small>
            </span>
            <i class="fa-solid fa-chevron-right" aria-hidden="true"></i>`;
        button.querySelector("strong").textContent = folder.name;
        button.querySelector("small").textContent = searching ? folder.label : (folder.child_count ? `${folder.child_count} folder${folder.child_count === 1 ? "" : "s"}` : "Empty folder");
        button.addEventListener("click", () => {
            const search = picker.querySelector("[data-folder-picker-search]");
            if (search) search.value = "";
            navigate(picker, state, folder.path);
        });
        return button;
    }

    function renderList(picker, state) {
        const holder = picker.querySelector("[data-folder-picker-list]");
        const search = picker.querySelector("[data-folder-picker-search]");
        const clear = picker.querySelector("[data-folder-picker-clear-search]");
        if (!holder) return;

        const query = (search?.value || "").trim().toLocaleLowerCase();
        if (clear) clear.hidden = !query;
        const folders = query
            ? state.folders.filter((folder) => folder.path !== state.rootPath && folder.label.toLocaleLowerCase().includes(query))
            : state.folders.filter((folder) => folder.parent === state.currentPath);

        holder.innerHTML = "";
        folders.forEach((folder) => holder.appendChild(makeFolderRow(picker, state, folder, Boolean(query))));
        if (!folders.length) {
            const empty = document.createElement("div");
            empty.className = "folder-picker-empty";
            empty.innerHTML = query
                ? '<span><i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i><br>No matching folders</span>'
                : '<span><i class="fa-regular fa-folder-open" aria-hidden="true"></i><br>No folders inside this location</span>';
            holder.appendChild(empty);
        }
    }

    function commitCurrent(picker, state) {
        const input = picker.querySelector('input[type="hidden"]');
        const selectedLabel = picker.querySelector("[data-folder-picker-selected-label]");
        const label = pathLabel(state.currentPath, state);
        if (input && input.value !== state.currentPath) {
            input.value = state.currentPath;
            input.dispatchEvent(new Event("change", { bubbles: true }));
        }
        if (selectedLabel) selectedLabel.textContent = label;
        picker.dataset.folderPickerSelected = state.currentPath;
    }

    function navigate(picker, state, path, options = {}) {
        if (!state.byPath.has(path)) return;
        state.currentPath = path;
        const currentLabel = picker.querySelector("[data-folder-picker-current-label]");
        if (currentLabel) currentLabel.textContent = pathLabel(path, state);
        if (options.commit !== false) commitCurrent(picker, state);
        renderBreadcrumbs(picker, state);
        renderList(picker, state);
    }

    function selectCurrent(picker, state) {
        commitCurrent(picker, state);
        setPanelOpen(picker, false);
    }

    async function initializePicker(picker) {
        if (picker.dataset.folderPickerReady === "true") return picker._folderPickerState;
        const list = picker.querySelector("[data-folder-picker-list]");
        if (list) list.innerHTML = '<div class="folder-picker-loading"><span><i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i><br>Loading folders…</span></div>';

        try {
            const payload = await fetchFolders(picker.dataset.folderEndpoint);
            const state = {
                folders: payload.folders || [],
                rootPath: payload.root_path || "",
                rootLabel: payload.root_label || "Storage root",
                byPath: new Map(),
                currentPath: "",
            };
            state.folders.forEach((folder) => state.byPath.set(folder.path, folder));
            const input = picker.querySelector('input[type="hidden"]');
            const initialPath = input?.value || state.rootPath;
            state.currentPath = state.byPath.has(initialPath) ? initialPath : state.rootPath;
            picker._folderPickerState = state;
            picker.dataset.folderPickerReady = "true";
            navigate(picker, state, state.currentPath, { commit: true });

            picker.querySelector("[data-folder-picker-search]")?.addEventListener("input", () => renderList(picker, state));
            picker.querySelector("[data-folder-picker-clear-search]")?.addEventListener("click", () => {
                const search = picker.querySelector("[data-folder-picker-search]");
                if (search) {
                    search.value = "";
                    search.focus();
                }
                renderList(picker, state);
            });
            picker.querySelector("[data-folder-picker-choose]")?.addEventListener("click", () => selectCurrent(picker, state));
            picker.closest("form")?.addEventListener("submit", () => commitCurrent(picker, state));
            return state;
        } catch (error) {
            if (list) list.innerHTML = `<div class="folder-picker-error">${error.message || "Could not load folders."}</div>`;
            throw error;
        }
    }

    document.querySelectorAll("[data-folder-picker]").forEach((picker) => {
        const trigger = picker.querySelector("[data-folder-picker-trigger]");
        trigger?.addEventListener("click", async () => {
            const panel = picker.querySelector("[data-folder-picker-panel]");
            const shouldOpen = panel?.hidden !== false;
            setPanelOpen(picker, shouldOpen);
            if (shouldOpen) {
                await initializePicker(picker).catch(() => null);
                picker.querySelector("[data-folder-picker-search]")?.focus();
            }
        });
        if (picker.dataset.folderPickerExpanded === "true") initializePicker(picker).catch(() => null);
    });

    window.TamestorageFolderPicker = {
        async setValue(target, path, options = {}) {
            const input = typeof target === "string" ? document.querySelector(target) : target;
            const picker = input?.closest("[data-folder-picker]");
            if (!picker) return;
            const state = await initializePicker(picker);
            const safePath = state.byPath.has(path) ? path : state.rootPath;
            state.currentPath = safePath;
            navigate(picker, state, safePath, { commit: true });
            if (options.open) setPanelOpen(picker, true);
        },
    };
})();
