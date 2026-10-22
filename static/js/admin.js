(function () {
    "use strict";

    function fetchSystemUsage() {
        fetch("/system_usage", { headers: { Accept: "application/json" }, cache: "no-store" })
            .then((response) => {
                if (!response.ok) throw new Error("Could not load system usage.");
                return response.json();
            })
            .then((data) => {
                const cpu = document.getElementById("cpuUsage");
                const memory = document.getElementById("memoryUsage");
                const disk = document.getElementById("diskUsage");
                const uptime = document.getElementById("uptime");
                if (cpu) cpu.textContent = data.cpu_usage + "%";
                if (memory) memory.textContent = data.memory_usage + "%";
                if (disk) disk.textContent = data.disk_usage + "%";
                if (uptime) uptime.textContent = data.uptime;
            })
            .catch(() => {
                ['cpuUsage','memoryUsage','diskUsage'].forEach(id=>{const el=document.getElementById(id);if(el){el.textContent='Unavailable';el.title='Unable to reach system metrics. Retrying automatically.';}});
            });
    }

    function setupExitNodeControl() {
        const root = document.querySelector("[data-exit-node-control]");
        if (!root) return;
        const toggle = root.querySelector("[data-exit-node-toggle]");
        const status = root.querySelector("[data-exit-node-status]");
        const url = root.dataset.url;
        const render = (enabled) => {
            toggle.checked = enabled;
            toggle.disabled = false;
            status.textContent = enabled ? "On" : "Off";
            root.classList.toggle("is-enabled", enabled);
        };
        const fail = (message) => {
            toggle.disabled = true;
            status.textContent = message || "Unavailable";
        };
        fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" })
            .then(async (response) => {
                const data = await response.json();
                if (!response.ok || !data.ok) throw new Error(data.error);
                render(Boolean(data.enabled));
            })
            .catch((error) => fail(error.message));
        toggle.addEventListener("change", () => {
            const enabled = toggle.checked;
            toggle.disabled = true;
            status.textContent = "Updating…";
            fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify({ enabled }),
            })
                .then(async (response) => {
                    const data = await response.json();
                    if (!response.ok || !data.ok) throw new Error(data.error);
                    render(Boolean(data.enabled));
                })
                .catch((error) => {
                    toggle.checked = !enabled;
                    fail(error.message);
                });
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        setupExitNodeControl();
        if (document.getElementById("cpuUsage")) {
            fetchSystemUsage();
            window.setInterval(fetchSystemUsage, 5000);
        }

        document.querySelectorAll("[data-log-auto-submit]").forEach((control) => {
            control.addEventListener("change", () => control.form?.requestSubmit());
        });

        document.querySelectorAll("[data-log-refresh]").forEach((button) => {
            button.addEventListener("click", () => window.location.reload());
        });
    });
})();
