(function () {
    function parseEvent(button) {
        try {
            return JSON.parse(button.dataset.event || "{}");
        } catch (_error) {
            return null;
        }
    }

    function readableDate(item) {
        const raw = item.date || "";
        const date = new Date(`${raw}T00:00:00`);
        const dateText = Number.isNaN(date.getTime())
            ? raw
            : new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }).format(date);
        if (item.all_day) return `${dateText} · All day`;
        if (item.start_time && item.end_time) return `${dateText} · ${item.start_time}–${item.end_time}`;
        if (item.start_time) return `${dateText} · ${item.start_time}`;
        return dateText;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const eventModalElement = document.getElementById("eventCreateModal");
        const eventModal = eventModalElement && window.bootstrap ? bootstrap.Modal.getOrCreateInstance(eventModalElement) : null;
        const eventDate = document.getElementById("eventDate");
        const eventTitle = document.getElementById("eventTitle");
        const allDay = document.getElementById("eventAllDay");
        const timeFields = document.querySelector("[data-event-time-fields]");
        const startTime = document.getElementById("eventStart");

        document.querySelectorAll("[data-create-date]").forEach((button) => {
            button.addEventListener("click", () => {
                if (eventDate) eventDate.value = button.dataset.createDate || "";
                eventModal?.show();
            });
        });
        eventModalElement?.addEventListener("shown.bs.modal", () => eventTitle?.focus());

        function updateAllDay() {
            const hidden = Boolean(allDay?.checked);
            if (timeFields) timeFields.hidden = hidden;
            if (startTime) startTime.required = !hidden;
        }
        allDay?.addEventListener("change", updateAllDay);
        updateAllDay();

        const detailElement = document.getElementById("eventDetailModal");
        const detailModal = detailElement && window.bootstrap ? bootstrap.Modal.getOrCreateInstance(detailElement) : null;
        const titleTarget = detailElement?.querySelector("[data-event-title]");
        const dateTarget = detailElement?.querySelector("[data-event-date]");
        const locationRow = detailElement?.querySelector("[data-event-location-row]");
        const locationTarget = detailElement?.querySelector("[data-event-location]");
        const notesRow = detailElement?.querySelector("[data-event-notes-row]");
        const notesTarget = detailElement?.querySelector("[data-event-notes]");
        const sourceTarget = detailElement?.querySelector("[data-event-source]");
        const deleteForm = detailElement?.querySelector("[data-event-delete-form]");
        const microsoftLink = detailElement?.querySelector("[data-event-open-microsoft]");

        document.querySelectorAll("[data-event]").forEach((button) => {
            button.addEventListener("click", () => {
                const item = parseEvent(button);
                if (!item) return;
                if (titleTarget) titleTarget.textContent = item.title || "Event";
                if (dateTarget) dateTarget.textContent = readableDate(item);
                if (locationTarget) locationTarget.textContent = item.location || "";
                if (locationRow) locationRow.hidden = !item.location;
                if (notesTarget) notesTarget.textContent = item.notes || "";
                if (notesRow) notesRow.hidden = !item.notes;

                const microsoft = item.source === "microsoft";
                if (sourceTarget) {
                    sourceTarget.textContent = microsoft ? "Microsoft Calendar" : "";
                    sourceTarget.classList.toggle("is-visible", microsoft);
                }
                if (deleteForm) {
                    deleteForm.hidden = microsoft;
                    if (!microsoft) {
                        const template = deleteForm.dataset.deleteTemplate || "";
                        deleteForm.action = template.replace("__EVENT_ID__", encodeURIComponent(item.id || ""));
                    }
                }
                if (microsoftLink) {
                    microsoftLink.hidden = !microsoft || !item.web_link;
                    microsoftLink.href = item.web_link || "#";
                }
                detailModal?.show();
            });
        });

        const filterButtons = Array.from(document.querySelectorAll("[data-todo-filter]"));
        const todoItems = Array.from(document.querySelectorAll("[data-todo-state]"));
        const filterEmpty = document.querySelector("[data-filter-empty]");
        function applyFilter(filter) {
            let visible = 0;
            todoItems.forEach((item) => {
                const show = filter === "all" || item.dataset.todoState === filter;
                item.hidden = !show;
                if (show) visible += 1;
            });
            if (filterEmpty) filterEmpty.hidden = visible !== 0 || todoItems.length === 0;
            filterButtons.forEach((button) => {
                const active = button.dataset.todoFilter === filter;
                button.classList.toggle("active", active);
                button.setAttribute("aria-pressed", String(active));
            });
        }
        filterButtons.forEach((button) => button.addEventListener("click", () => applyFilter(button.dataset.todoFilter || "open")));
        applyFilter("open");

        const syncForm = document.getElementById("microsoftSyncForm");
        const syncButton = document.getElementById("microsoftSyncButton");
        async function syncMicrosoft(event) {
            event?.preventDefault();
            if (!syncForm || syncForm.dataset.configured !== "true" || syncButton?.classList.contains("is-syncing")) return;
            syncButton?.classList.add("is-syncing");
            syncButton?.classList.remove("is-synced");
            syncButton?.setAttribute("disabled", "disabled");
            try {
                const response = await fetch(syncForm.action, {
                    method: "POST",
                    body: new FormData(syncForm),
                    headers: { "Accept": "application/json", "X-Requested-With": "fetch" },
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || !payload.success) throw new Error(payload.error || "Microsoft Calendar sync failed.");
                syncButton?.classList.add("is-synced");
                window.location.reload();
            } catch (error) {
                if (syncButton) {
                    syncButton.title = error.message || "Microsoft Calendar sync failed.";
                    bootstrap.Tooltip.getInstance(syncButton)?.dispose();
                    bootstrap.Tooltip.getOrCreateInstance(syncButton, { container: "body", trigger: "hover focus" });
                }
            } finally {
                syncButton?.classList.remove("is-syncing");
                syncButton?.removeAttribute("disabled");
            }
        }
        syncForm?.addEventListener("submit", syncMicrosoft);
        if (syncForm?.dataset.autoSync === "true") window.setTimeout(() => syncMicrosoft(), 350);

        const calendarButtons = Array.from(document.querySelectorAll(".planner-day-number"));
        calendarButtons.forEach((button, index) => {
            button.addEventListener("keydown", (event) => {
                const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
                if (!(event.key in offsets)) return;
                const target = calendarButtons[index + offsets[event.key]];
                if (!target) return;
                event.preventDefault();
                target.focus();
            });
        });
    });
})();
