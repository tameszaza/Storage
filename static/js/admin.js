(function () {
    function fetchSystemUsage() {
        fetch("/system_usage")
            .then((response) => response.json())
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
            .catch(() => {});
    }
    document.addEventListener("DOMContentLoaded", () => {
        fetchSystemUsage();
        setInterval(fetchSystemUsage, 5000);
    });
})();
