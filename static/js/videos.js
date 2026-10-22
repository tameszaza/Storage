(() => {
    "use strict";

    const page = document.querySelector(".video-page");
    if (!page) return;
    const list = document.querySelector("#video-list");
    const feedback = document.querySelector("#video-feedback");
    const search = document.querySelector("#video-search");
    const filter = document.querySelector("#video-filter");
    const pager = document.querySelector("#video-pagination");
    const previous = document.querySelector("#video-previous");
    const next = document.querySelector("#video-next");
    const pageRange = document.querySelector("#video-page-range");
    const pageSize = 12;
    let items = [];
    let signature = "";
    let pageIndex = 0;
    let deleteId = null;

    const bytes = (value) => value >= 1073741824
        ? `${(value / 1073741824).toFixed(1)} GB`
        : `${(value / 1048576).toFixed(1)} MB`;

    function say(message) {
        feedback.textContent = message;
        feedback.hidden = false;
    }

    async function action(data) {
        const response = await fetch("/videos/action", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": page.dataset.token },
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || "Could not complete the action. Refresh and try again.");
        }
        await refresh();
    }

    function button(label, icon, onClick, className = "btn btn-outline-secondary") {
        const element = document.createElement("button");
        element.type = "button";
        element.className = className;
        element.title = label;
        element.setAttribute("aria-label", label);
        const glyph = document.createElement("i");
        glyph.className = `fa-solid ${icon}`;
        glyph.setAttribute("aria-hidden", "true");
        element.append(glyph);
        element.addEventListener("click", onClick);
        return element;
    }

    function statusCopy(item) {
        const message = String(item.message || "");
        if (item.status === "queued") return { summary: "Waiting in queue" };
        if (item.status === "downloading") {
            return { summary: `${message || "Downloading"} · ${bytes(item.received)}${item.total ? ` of ${bytes(item.total)}` : ""}` };
        }
        if (item.status === "ready") {
            const detail = message.length > 100 ? message : "";
            const summary = detail
                ? (message.toLowerCase().includes("original kept") ? "Ready to watch · original kept" : "Ready to watch")
                : (message || "Ready to watch");
            return { summary: `${summary} · ${bytes(item.received)}`, detail };
        }
        if (item.status === "failed") return { summary: "Download failed", detail: message };
        return { summary: message || item.status };
    }

    function videoCard(item) {
        const card = document.createElement("article");
        card.className = "video-item";
        const icon = document.createElement("span");
        icon.className = "video-item-icon";
        const glyph = document.createElement("i");
        glyph.className = `fa-solid ${item.status === "ready" ? "fa-play" : item.status === "failed" ? "fa-exclamation" : "fa-arrow-down"}`;
        glyph.setAttribute("aria-hidden", "true");
        icon.append(glyph);

        const copy = document.createElement("div");
        const title = document.createElement("h3");
        title.textContent = item.title;
        const meta = document.createElement("p");
        const status = statusCopy(item);
        meta.textContent = status.summary;
        copy.append(title, meta);
        if (status.detail) {
            const detail = document.createElement("details");
            detail.className = "video-status-detail";
            const toggle = document.createElement("summary");
            toggle.textContent = "Details";
            const explanation = document.createElement("p");
            explanation.textContent = status.detail;
            detail.append(toggle, explanation);
            copy.append(detail);
        }
        const path = document.createElement("a");
        path.className = "video-path";
        path.href = item.folder_url;
        path.title = "Open this folder in My Files";
        const folderIcon = document.createElement("i");
        folderIcon.className = "fa-regular fa-folder-open";
        folderIcon.setAttribute("aria-hidden", "true");
        path.append(folderIcon, document.createTextNode(` ${item.folder_label || "Admin / Movies"}`));
        copy.append(path);
        if (item.status === "downloading") {
            const progress = document.createElement("progress");
            progress.setAttribute("aria-label", "Download progress");
            if (item.total) {
                progress.max = item.total;
                progress.value = item.received;
            }
            copy.append(progress);
        }

        const actions = document.createElement("div");
        actions.className = "video-actions";
        if (item.status === "ready") {
            actions.append(button("Play video", "fa-play", () => {
                const player = document.querySelector("#video-player");
                player.querySelector("video").src = `/videos/${item.id}/file`;
                player.showModal();
            }, "btn btn-primary"));
            const save = document.createElement("a");
            save.href = `/videos/${item.id}/file?download=1`;
            save.className = "btn btn-outline-secondary";
            save.textContent = "Save";
            save.setAttribute("aria-label", `Save ${item.title}`);
            actions.append(save);
        }
        if (item.status === "failed") {
            actions.append(button("Retry download", "fa-rotate-right", () => {
                action({ action: "retry", id: item.id }).catch((error) => say(error.message));
            }));
        }
        if (item.status !== "deleting") {
            actions.append(button("Delete permanently", "fa-trash-can", () => {
                deleteId = item.id;
                document.querySelector("#video-delete-title").textContent = item.title;
                document.querySelector("#video-delete").showModal();
            }, "btn video-delete-btn"));
        }
        card.append(icon, copy, actions);
        return card;
    }

    function matchesFilter(item) {
        const query = search.value.trim().toLocaleLowerCase();
        const matchesText = !query || `${item.title} ${item.folder_label || ""}`.toLocaleLowerCase().includes(query);
        const selected = filter.value;
        const matchesStatus = selected === "all"
            || (selected === "active" && !["ready", "failed", "deleting"].includes(item.status))
            || item.status === selected;
        return matchesText && matchesStatus;
    }

    function render() {
        const matching = items.filter(matchesFilter);
        pageIndex = Math.min(pageIndex, Math.max(0, Math.ceil(matching.length / pageSize) - 1));
        const start = pageIndex * pageSize;
        list.replaceChildren(...matching.slice(start, start + pageSize).map(videoCard));
        document.querySelector("#video-count").textContent = items.length;
        document.querySelector("#video-empty").hidden = items.length > 0;
        document.querySelector("#video-no-results").hidden = items.length === 0 || matching.length > 0;
        pager.hidden = matching.length <= pageSize;
        previous.disabled = pageIndex === 0;
        next.disabled = start + pageSize >= matching.length;
        pageRange.textContent = `${start + 1}–${Math.min(start + pageSize, matching.length)} of ${matching.length}`;
    }

    async function refresh() {
        try {
            const response = await fetch("/videos/status");
            if (!response.ok || response.redirected) throw new Error("Session expired. Refresh the page to sign in.");
            const data = await response.json();
            const nextSignature = JSON.stringify(data);
            if (signature === nextSignature) return;
            signature = nextSignature;
            items = data.items || [];
            render();
        } catch (error) {
            say(error.message);
        }
    }

    search.addEventListener("input", () => { pageIndex = 0; render(); });
    filter.addEventListener("change", () => { pageIndex = 0; render(); });
    previous.addEventListener("click", () => { pageIndex -= 1; render(); pager.scrollIntoView({ block: "nearest" }); });
    next.addEventListener("click", () => { pageIndex += 1; render(); pager.scrollIntoView({ block: "nearest" }); });

    document.querySelector("#video-add").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const submit = form.querySelector("button[type=submit]");
        submit.disabled = true;
        try {
            await action({
                action: "add",
                title: form.elements.namedItem("title").value,
                url: form.elements.namedItem("url").value,
            });
            form.reset();
            say("Added to your download queue.");
        } catch (error) {
            say(error.message);
        } finally {
            submit.disabled = false;
        }
    });
    document.querySelector("#video-delete").addEventListener("close", (event) => {
        if (event.target.returnValue === "delete") {
            action({ action: "delete", id: deleteId }).catch((error) => say(error.message));
        }
        event.target.returnValue = "";
    });
    document.querySelector("#video-player-close").addEventListener("click", () => document.querySelector("#video-player").close());
    document.querySelector("#video-player").addEventListener("close", (event) => {
        const video = event.target.querySelector("video");
        video.pause();
        video.removeAttribute("src");
        video.load();
    });

    refresh();
    setInterval(() => {
        if (!document.hidden && !document.querySelector("dialog[open]")) refresh();
    }, 3000);
})();
