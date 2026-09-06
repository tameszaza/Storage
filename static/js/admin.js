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

    document.addEventListener("DOMContentLoaded", () => {
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
