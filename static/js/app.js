(function () {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

    function readPreference(key, fallback) {
        try {
            return localStorage.getItem(key) || fallback;
        } catch (_error) {
            return fallback;
        }
    }

    function writePreference(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (_error) {
            // The interface remains usable when storage is restricted.
        }
    }

    function resolveTheme(preference) {
        if (preference === "system") return mediaQuery.matches ? "dark" : "light";
        return preference === "dark" ? "dark" : "light";
    }

    function applyTheme(preference, persist = true) {
        const normalized = ["light", "dark", "system"].includes(preference) ? preference : "light";
        const resolved = resolveTheme(normalized);
        document.documentElement.setAttribute("data-theme", resolved);
        document.documentElement.setAttribute("data-theme-preference", normalized);
        document.documentElement.style.colorScheme = resolved;
        document.documentElement.style.backgroundColor = resolved === "dark" ? "#0b1120" : "#f8fafc";
        document.body.setAttribute("data-theme", resolved);
        document.body.setAttribute("data-theme-preference", normalized);
        const themeColor = document.querySelector('meta[name="theme-color"]');
        if (themeColor) themeColor.content = resolved === "dark" ? "#0b1120" : "#ffffff";
        if (persist) writePreference("theme", normalized);

        const icon = document.getElementById("themeIcon");
        if (icon) icon.className = resolved === "dark" ? "fa-regular fa-sun" : "fa-regular fa-moon";

        document.querySelectorAll("[data-set-theme]").forEach((button) => {
            const active = button.dataset.setTheme === normalized;
            button.classList.toggle("active", active);
            button.setAttribute("aria-pressed", String(active));
        });
    }

    function closeSidebar() {
        document.body.classList.remove("sidebar-open");
        const toggle = document.getElementById("mobileNavToggle");
        if (toggle) toggle.setAttribute("aria-expanded", "false");
    }

    function closeUploadDialog() {
        if (window.location.hash !== "#dropArea") return;
        window.location.hash = "";
        window.setTimeout(() => {
            history.replaceState(null, "", window.location.pathname + window.location.search);
        }, 0);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const storedTheme = readPreference("theme", "light");
        applyTheme(storedTheme, false);

        mediaQuery.addEventListener?.("change", () => {
            if (readPreference("theme", "light") === "system") applyTheme("system", false);
        });

        const themeToggle = document.getElementById("themeToggle");
        if (themeToggle) {
            themeToggle.addEventListener("click", () => {
                const current = document.body.getAttribute("data-theme") || "light";
                applyTheme(current === "dark" ? "light" : "dark");
            });
        }

        document.querySelectorAll("[data-set-theme]").forEach((button) => {
            button.addEventListener("click", () => applyTheme(button.dataset.setTheme || "light"));
        });

        const mobileNavToggle = document.getElementById("mobileNavToggle");
        const sidebarBackdrop = document.getElementById("sidebarBackdrop");
        if (mobileNavToggle) {
            mobileNavToggle.addEventListener("click", () => {
                const open = !document.body.classList.contains("sidebar-open");
                document.body.classList.toggle("sidebar-open", open);
                mobileNavToggle.setAttribute("aria-expanded", String(open));
            });
        }
        if (sidebarBackdrop) sidebarBackdrop.addEventListener("click", closeSidebar);
        document.querySelectorAll(".app-sidebar a").forEach((link) => link.addEventListener("click", closeSidebar));
        window.addEventListener("resize", () => { if (window.innerWidth > 860) closeSidebar(); });

        const globalSearch = document.querySelector(".global-search input");
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closeSidebar();
                closeUploadDialog();
            }
            if (event.key === "/" && globalSearch && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
                event.preventDefault();
                globalSearch.focus();
            }
        });

        const uploadPanel = document.getElementById("dropArea");
        if (uploadPanel) {
            uploadPanel.addEventListener("click", (event) => {
                if (event.target === uploadPanel) closeUploadDialog();
            });
        }

        document.querySelectorAll("button, a, [role='button']").forEach((element) => {
            const ariaLabel = element.getAttribute("aria-label");
            const title = element.getAttribute("title");
            const visibleText = (element.textContent || "").trim();
            if (ariaLabel && !title) element.setAttribute("title", ariaLabel);
            if (title && !ariaLabel && !visibleText) element.setAttribute("aria-label", title);
        });

        if (window.bootstrap?.Tooltip) {
            document.querySelectorAll("[data-tooltip='true']").forEach((element) => {
                bootstrap.Tooltip.getOrCreateInstance(element, { container: "body", trigger: "hover focus", delay: { show: 350, hide: 80 } });
            });
        }

        document.querySelectorAll("form[data-confirm]").forEach((form) => {
            form.addEventListener("submit", (event) => {
                const message = form.getAttribute("data-confirm") || "Are you sure?";
                if (!window.confirm(message)) event.preventDefault();
            });
        });

        document.querySelectorAll(".js-copy").forEach((button) => {
            button.addEventListener("click", async () => {
                const value = button.dataset.copy || "";
                if (!value) return;
                try {
                    await navigator.clipboard.writeText(value);
                    const original = button.innerHTML;
                    button.innerHTML = '<i class="fa-solid fa-check"></i> Copied';
                    setTimeout(() => { button.innerHTML = original; }, 1400);
                } catch (error) {
                    window.prompt("Copy this", value);
                }
            });
        });

        if ("serviceWorker" in navigator) {
            navigator.serviceWorker.register("/static/service-worker.js").catch(() => {});
        }
    });
})();
