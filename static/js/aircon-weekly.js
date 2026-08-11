(function () {
    "use strict";

    const body = document.body;
    const initialNode = document.getElementById("airconInitialData");
    const initial = initialNode ? JSON.parse(initialNode.textContent || "{}") : {};
    const dayNames = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
    const shortNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const weeklyCard = document.querySelector(".aircon-weekly-card");
    const weeklyEnabled = document.getElementById("weeklyEnabled");
    const weeklyDays = document.getElementById("weeklyDays");
    const weeklyState = document.getElementById("weeklyState");
    const saveWeekly = document.getElementById("saveWeekly");
    const historyList = document.getElementById("airconHistory");
    const historyCount = document.getElementById("historyCount");
    const weeklyEstimatedSpent = document.getElementById("weeklyEstimatedSpent");
    const weeklyEstimatedSaved = document.getElementById("weeklyEstimatedSaved");
    const weeklyEstimatedHours = document.getElementById("weeklyEstimatedHours");
    const weeklyEstimatedDays = document.getElementById("weeklyEstimatedDays");
    const weeklyEstimatedRate = document.getElementById("weeklyEstimatedRate");
    const presets = {
        "1": { on: document.getElementById("preset1On"), off: document.getElementById("preset1Off") },
        "2": { on: document.getElementById("preset2On"), off: document.getElementById("preset2Off") },
    };

    let config = initial.weekly || {};
    let ratePerHour = Number(initial.rate_per_hour) || 0.39;
    let busy = false;
    let dirty = false;

    function durationText(seconds) {
        const value = Math.max(0, Math.round(Number(seconds) || 0));
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor((value % 3600) / 60);
        const secs = value % 60;
        if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
    }

    function parseDuration(value) {
        const parts = String(value || "").trim().split(":").map(Number);
        if (parts.some((part) => !Number.isFinite(part) || part < 0)) {
            throw new Error("Use MM:SS or HH:MM:SS.");
        }

        let seconds;
        if (parts.length === 2) {
            if (parts[1] >= 60) throw new Error("Seconds must be below 60.");
            seconds = parts[0] * 60 + parts[1];
        } else if (parts.length === 3) {
            if (parts[1] >= 60 || parts[2] >= 60) throw new Error("Minutes and seconds must be below 60.");
            seconds = parts[0] * 3600 + parts[1] * 60 + parts[2];
        } else {
            throw new Error("Use MM:SS or HH:MM:SS.");
        }

        if (seconds < 5 || seconds > 86400) throw new Error("Duration must be 5 seconds to 24 hours.");
        return Math.round(seconds);
    }

    function clockSeconds(value) {
        const parts = String(value || "").split(":").map(Number);
        if (parts.length !== 2 || parts.some((part) => !Number.isFinite(part))) {
            throw new Error("Times must use HH:MM.");
        }
        const [hours, minutes] = parts;
        if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
            throw new Error("Times must use HH:MM.");
        }
        return hours * 3600 + minutes * 60;
    }

    function moneyRoundUp(rawValue) {
        const value = Math.max(0, Number(rawValue) || 0);
        return Math.ceil((value - 1e-12) * 100) / 100;
    }

    function formatMoney(rawValue) {
        return `$${moneyRoundUp(rawValue).toFixed(2)}`;
    }

    function windowEstimate(windowSeconds, mode, presetConfig) {
        if (!["1", "2"].includes(mode) || !presetConfig[mode]) {
            return {
                onSeconds: 0,
                offSeconds: windowSeconds,
                estimatedSpent: 0,
                estimatedSaved: moneyRoundUp((windowSeconds / 3600) * ratePerHour),
            };
        }

        const preset = presetConfig[mode];
        const onDuration = Math.max(1, Number(preset.on_duration_seconds) || 1);
        const offDuration = Math.max(1, Number(preset.off_duration_seconds) || 1);
        let remaining = windowSeconds;
        let onSeconds = 0;
        let offSeconds = 0;
        let estimatedSpent = 0;
        let estimatedSaved = 0;

        while (remaining > 0) {
            const onSegment = Math.min(onDuration, remaining);
            onSeconds += onSegment;
            estimatedSpent += moneyRoundUp((onSegment / 3600) * ratePerHour);
            remaining -= onSegment;
            if (remaining <= 0) break;

            const offSegment = Math.min(offDuration, remaining);
            offSeconds += offSegment;
            estimatedSaved += moneyRoundUp((offSegment / 3600) * ratePerHour);
            remaining -= offSegment;
        }

        return { onSeconds, offSeconds, estimatedSpent, estimatedSaved };
    }

    function estimateWeeklyCost(nextConfig) {
        let onSeconds = 0;
        let offSeconds = 0;
        let estimatedSpent = 0;
        let estimatedSaved = 0;
        let activeDays = 0;
        let activeWindows = 0;
        const presetConfig = nextConfig.presets || {};

        dayNames.forEach((day) => {
            const rule = (nextConfig.days || {})[day] || {};
            if (!rule.enabled) return;
            activeDays += 1;

            const dayStart = clockSeconds(rule.day_start || "07:30");
            const nightStart = clockSeconds(rule.night_start || "22:00");
            const windows = [
                { seconds: nightStart - dayStart, mode: rule.day_mode || "off" },
                { seconds: (24 * 60 * 60) - nightStart + dayStart, mode: rule.night_mode || "1" },
            ];

            windows.forEach((window) => {
                if (["1", "2"].includes(window.mode) && presetConfig[window.mode]) activeWindows += 1;
                const estimate = windowEstimate(window.seconds, window.mode, presetConfig);
                onSeconds += estimate.onSeconds;
                offSeconds += estimate.offSeconds;
                estimatedSpent += estimate.estimatedSpent;
                estimatedSaved += estimate.estimatedSaved;
            });
        });

        return {
            activeDays,
            activeWindows,
            onSeconds,
            offSeconds,
            estimatedSpent,
            estimatedSaved,
        };
    }

    function renderWeeklyEstimate() {
        try {
            const estimate = estimateWeeklyCost(config);
            const onHours = estimate.onSeconds / 3600;
            const offHours = estimate.offSeconds / 3600;
            weeklyEstimatedSpent.textContent = formatMoney(estimate.estimatedSpent);
            weeklyEstimatedSaved.textContent = formatMoney(estimate.estimatedSaved);
            weeklyEstimatedHours.textContent = `${onHours.toFixed(1)} hr ON · ${offHours.toFixed(1)} hr OFF`;
            weeklyEstimatedDays.textContent = estimate.activeDays
                ? `${estimate.activeDays} active day${estimate.activeDays === 1 ? "" : "s"} · ${estimate.activeWindows} active windows`
                : "No active days";
            weeklyEstimatedRate.textContent = `Based on ${formatMoney(ratePerHour)} per ON hour.`;
        } catch (error) {
            weeklyEstimatedSpent.textContent = "—";
            weeklyEstimatedSaved.textContent = "—";
            weeklyEstimatedHours.textContent = "Check the plan times";
            weeklyEstimatedDays.textContent = error.message;
        }
    }

    function modeOptions(selected) {
        return [
            ["off", "Off"],
            ["1", "P1 Night"],
            ["2", "P2 Day"],
        ].map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
    }

    function rowFor(day, index, rule) {
        const row = document.createElement("tr");
        row.dataset.day = day;
        row.innerHTML = `
            <td><label class="aircon-day-toggle"><input type="checkbox" data-field="enabled" ${rule.enabled ? "checked" : ""}><strong>${shortNames[index]}</strong></label></td>
            <td><input type="time" data-field="day_start" value="${rule.day_start || "07:30"}" aria-label="${shortNames[index]} day start"></td>
            <td><select data-field="day_mode" aria-label="${shortNames[index]} day mode">${modeOptions(rule.day_mode || "off")}</select></td>
            <td><input type="time" data-field="night_start" value="${rule.night_start || "22:00"}" aria-label="${shortNames[index]} night start"></td>
            <td><select data-field="night_mode" aria-label="${shortNames[index]} night mode">${modeOptions(rule.night_mode || "1")}</select></td>`;
        return row;
    }

    function renderConfig(nextConfig) {
        config = nextConfig || config;
        weeklyEnabled.checked = Boolean(config.enabled);
        const presetConfig = config.presets || {};
        ["1", "2"].forEach((id) => {
            const preset = presetConfig[id] || {};
            presets[id].on.value = durationText(preset.on_duration_seconds);
            presets[id].off.value = durationText(preset.off_duration_seconds);
        });

        weeklyDays.replaceChildren();
        dayNames.forEach((day, index) => {
            weeklyDays.appendChild(rowFor(day, index, (config.days || {})[day] || {}));
        });
        renderWeeklyEstimate();
    }

    function collectConfig() {
        const days = {};
        weeklyDays.querySelectorAll("tr[data-day]").forEach((row) => {
            const get = (field) => row.querySelector(`[data-field="${field}"]`);
            days[row.dataset.day] = {
                enabled: get("enabled").checked,
                day_start: get("day_start").value,
                day_mode: get("day_mode").value,
                night_start: get("night_start").value,
                night_mode: get("night_mode").value,
            };
        });

        return {
            enabled: weeklyEnabled.checked,
            presets: {
                "1": {
                    name: "Night",
                    on_duration_seconds: parseDuration(presets["1"].on.value),
                    off_duration_seconds: parseDuration(presets["1"].off.value),
                },
                "2": {
                    name: "Day",
                    on_duration_seconds: parseDuration(presets["2"].on.value),
                    off_duration_seconds: parseDuration(presets["2"].off.value),
                },
            },
            days,
        };
    }

    function renderWeeklyStatus(status) {
        const active = status && status.active_window;
        if (!active) {
            weeklyState.textContent = weeklyEnabled.checked ? "Armed" : "Off";
            weeklyState.classList.remove("is-active");
            return;
        }

        const dayLabel = shortNames[dayNames.indexOf(active.day)] || active.day;
        const period = active.period === "night" ? "Night" : "Day";
        if (active.mode === "off") {
            weeklyState.textContent = `${dayLabel} · ${period} off · until ${active.end}`;
            weeklyState.classList.remove("is-active");
        } else {
            weeklyState.textContent = `${dayLabel} · ${period} P${active.preset_id} · until ${active.end}`;
            weeklyState.classList.add("is-active");
        }
    }

    function formatHistoryLabel(entry) {
        const labels = {
            schedule_started: "Started",
            schedule_stopped: "Stopped",
            duration_adjusted: "Timing changed",
            weekly_schedule_updated: "Weekly updated",
            weekly_preset_changed: "Preset changed",
            direct_control: "Direct control",
        };
        return labels[entry.event] || entry.summary || entry.event.replaceAll("_", " ");
    }

    function renderHistory(entries) {
        const values = Array.isArray(entries) ? entries : [];
        historyCount.textContent = String(values.length);
        historyList.replaceChildren();

        values.slice(0, 40).forEach((entry) => {
            const item = document.createElement("li");
            const time = document.createElement("time");
            const strong = document.createElement("strong");
            const source = document.createElement("span");
            const date = new Date(entry.timestamp);
            time.dateTime = entry.timestamp;
            time.textContent = Number.isNaN(date.getTime())
                ? ""
                : date.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
            strong.textContent = formatHistoryLabel(entry);
            source.textContent = entry.source === "weekly" ? "Auto" : "User";
            item.append(time, strong, source);
            historyList.appendChild(item);
        });

        if (!values.length) {
            const item = document.createElement("li");
            item.className = "is-empty";
            item.textContent = "No changes yet";
            historyList.appendChild(item);
        }
    }

    async function jsonRequest(url, options) {
        const response = await fetch(url, {
            cache: "no-store",
            headers: { Accept: "application/json", ...(options && options.headers ? options.headers : {}) },
            ...options,
        });
        if (response.status === 401) {
            window.location.assign(body.dataset.loginUrl);
            throw new Error("Session expired.");
        }
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.ok === false) throw new Error(payload.error || "Request failed.");
        return payload;
    }

    function setStateError(label, error) {
        weeklyState.textContent = label;
        weeklyState.title = error && error.message ? error.message : String(error || "");
        weeklyState.classList.remove("is-active");
    }

    async function save() {
        if (busy) return;

        let payload;
        try {
            payload = collectConfig();
        } catch (error) {
            setStateError("Check times", error);
            return;
        }

        busy = true;
        saveWeekly.disabled = true;
        try {
            const result = await jsonRequest(body.dataset.weeklyUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            renderConfig(result.weekly);
            dirty = false;
            weeklyState.title = "";
            renderWeeklyStatus(result.weekly_status);
            saveWeekly.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i> Saved';
            window.setTimeout(() => {
                saveWeekly.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i> Save';
            }, 1400);
            await refreshHistory();
        } catch (error) {
            setStateError("Save failed", error);
        } finally {
            busy = false;
            saveWeekly.disabled = false;
        }
    }

    async function refreshWeekly() {
        try {
            const result = await jsonRequest(body.dataset.weeklyUrl, { method: "GET" });
            if (!dirty) renderConfig(result.weekly);
            renderWeeklyStatus(result.weekly_status);
        } catch (error) {
            setStateError("Offline", error);
        }
    }

    async function refreshHistory() {
        try {
            const result = await jsonRequest(body.dataset.historyUrl, { method: "GET" });
            renderHistory(result.history);
        } catch (_error) {
            // The main aircon connection indicator already reports connectivity.
        }
    }

    renderConfig(config);
    renderWeeklyStatus(initial.weekly_status || {});
    renderHistory(initial.history || []);
    weeklyCard?.addEventListener("input", () => { dirty = true; renderWeeklyEstimate(); });
    weeklyCard?.addEventListener("change", () => { dirty = true; renderWeeklyEstimate(); });
    saveWeekly.addEventListener("click", save);
    window.addEventListener("aircon:weekly-updated", (event) => {
        const result = event.detail || {};
        if (!result.weekly) return;
        if (dirty) {
            config.enabled = Boolean(result.weekly.enabled);
            weeklyEnabled.checked = config.enabled;
            renderWeeklyEstimate();
        } else {
            renderConfig(result.weekly);
        }
        renderWeeklyStatus(result.weekly_status || {});
        refreshHistory();
    });
    window.setInterval(refreshWeekly, 15000);
    window.setInterval(refreshHistory, 10000);
})();
