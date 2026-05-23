(function () {
    const applyTheme = (theme) => {
        document.body.setAttribute("data-theme", theme);
        localStorage.setItem("theme", theme);
        const icon = document.getElementById("themeIcon");
        if (icon) icon.className = theme === "dark" ? "fa-solid fa-sun" : "fa-solid fa-moon";
    };

    document.addEventListener("DOMContentLoaded", () => {
        applyTheme(localStorage.getItem("theme") || "light");
        const themeToggle = document.getElementById("themeToggle");
        if (themeToggle) {
            themeToggle.addEventListener("click", () => {
                const current = document.body.getAttribute("data-theme") || "light";
                applyTheme(current === "dark" ? "light" : "dark");
            });
        }

        const topbar = document.querySelector(".topbar");
        const mobileNavToggle = document.getElementById("mobileNavToggle");
        const topbarActions = document.getElementById("topbarActions");

        const setMobileMenu = (isOpen) => {
            if (!topbar || !mobileNavToggle) return;
            topbar.classList.toggle("menu-open", isOpen);
            mobileNavToggle.setAttribute("aria-expanded", String(isOpen));
            const icon = mobileNavToggle.querySelector("i");
            if (icon) icon.className = isOpen ? "fa-solid fa-xmark" : "fa-solid fa-bars";
        };

        if (mobileNavToggle && topbar) {
            mobileNavToggle.addEventListener("click", () => {
                setMobileMenu(!topbar.classList.contains("menu-open"));
            });

            if (topbarActions) {
                topbarActions.querySelectorAll("a").forEach((link) => {
                    link.addEventListener("click", () => setMobileMenu(false));
                });
            }

            window.addEventListener("resize", () => {
                if (window.innerWidth > 720) setMobileMenu(false);
            });

            document.addEventListener("keydown", (event) => {
                if (event.key === "Escape") setMobileMenu(false);
            });
        }

        document.querySelectorAll("form[data-confirm]").forEach((form) => {
            form.addEventListener("submit", (event) => {
                const message = form.getAttribute("data-confirm") || "Are you sure?";
                if (!window.confirm(message)) event.preventDefault();
            });
        });
    });
})();
