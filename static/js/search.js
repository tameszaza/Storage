(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", () => {
        const button = document.getElementById("moreFiltersButton");
        const panel = document.getElementById("moreFilters");
        const form = document.getElementById("searchForm");
        if (!button || !panel || !form) return;

        const mobileButton = document.getElementById("mobileFiltersButton");
        form.classList.add("mobile-filters-ready");
        mobileButton?.addEventListener("click", () => {
            const open = form.classList.toggle("mobile-filters-open");
            mobileButton.setAttribute("aria-expanded", String(open));
            if (open) form.querySelector(".search-filter-row select")?.focus();
        });

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
