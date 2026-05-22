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

        document.querySelectorAll("form[data-confirm]").forEach((form) => {
            form.addEventListener("submit", (event) => {
                const message = form.getAttribute("data-confirm") || "Are you sure?";
                if (!window.confirm(message)) event.preventDefault();
            });
        });
    });
})();
