(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", () => {
        const button = document.getElementById("moreFiltersButton");
        const panel = document.getElementById("moreFilters");
        const form = document.getElementById("searchForm");
        if (!button || !panel || !form) return;

        button.addEventListener("click", () => {
            const expanded = button.getAttribute("aria-expanded") === "true";
            button.setAttribute("aria-expanded", String(!expanded));
            panel.hidden = expanded;
            if (!expanded) panel.querySelector("input")?.focus();
        });

        form.querySelectorAll("select").forEach((select) => {
            select.addEventListener("change", () => form.requestSubmit());
        });
    });
})();
