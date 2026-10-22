document.querySelectorAll(".calendar-review-row").forEach((row) => {
    const timing = row.querySelector("[data-timing]");
    const selected = row.querySelector("input[name='selected']");
    const times = row.querySelector(".calendar-review-times");
    const start = times?.querySelector("input[name^='start_']");
    const date = row.querySelector("input[type='date']");
    const weekday = row.querySelector("[data-weekday]");
    if (!timing || !times) return;
    const syncWeekday = () => {
        if (!weekday) return;
        const chosen = date?.value ? new Date(`${date.value}T12:00:00`) : null;
        weekday.textContent = chosen && !Number.isNaN(chosen.getTime())
            ? chosen.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric", year: "numeric" })
            : "";
    };
    const sync = () => {
        const timed = timing.value === "timed";
        times.hidden = !timed;
        row.querySelectorAll("input[name^='title_'], input[name^='date_']").forEach((input) => {
            input.required = Boolean(selected?.checked);
        });
        if (start) start.required = timed && Boolean(selected?.checked);
    };
    date?.addEventListener("change", syncWeekday);
    selected?.addEventListener("change", () => {
        const group = row.dataset.conflictGroup;
        if (!selected.checked || !group) return;
        document.querySelectorAll(".calendar-review-row[data-conflict-group]").forEach((other) => {
            if (other !== row && other.dataset.conflictGroup === group) {
                const checkbox = other.querySelector("input[name='selected']");
                if (checkbox?.checked) {
                    checkbox.checked = false;
                    checkbox.dispatchEvent(new Event("change", { bubbles: true }));
                }
            }
        });
    });
    timing.addEventListener("change", sync);
    selected?.addEventListener("change", sync);
    sync();
    syncWeekday();
});
