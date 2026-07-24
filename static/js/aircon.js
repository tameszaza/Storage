(function () {
    "use strict";

    const initialElement = document.getElementById("airconInitialData");
    const initialData = initialElement ? JSON.parse(initialElement.textContent || "{}") : {};
    const body = document.body;
    const UNIT_SECONDS = { seconds: 1, minutes: 60, hours: 3600 };
    const MIN_DURATION_SECONDS = 5;
    const MAX_DURATION_SECONDS = 86400;

    let settings = initialData.settings || {};
    let status = initialData.status || {};
    let ratePerHour = Number(initialData.rate_per_hour) || 0.39;
    let dirty = false;
    let busy = false;
    let toastTimer = null;

    const elements = {
        connectionStatus: document.getElementById("connectionStatus"),
        stateIcon: document.getElementById("stateIcon"),
        currentState: document.getElementById("currentState"),
        currentPhase: document.getElementById("currentPhase"),
        nextChange: document.getElementById("nextChange"),
        phaseProgressText: document.getElementById("phaseProgressText"),
        cyclePosition: document.getElementById("cyclePosition"),
        cycleDetail: document.getElementById("cycleDetail"),
        sessionSpent: document.getElementById("sessionSpent"),
        sessionSaved: document.getElementById("sessionSaved"),
        scheduleSummary: document.getElementById("scheduleSummary"),
        saveState: document.getElementById("saveState"),
        applyChanges: document.getElementById("applyChanges"),
        startSchedule: document.getElementById("startSchedule"),
        stopSchedule: document.getElementById("stopSchedule"),
        skipPhase: document.getElementById("skipPhase"),
        onTimelineSegment: document.getElementById("onTimelineSegment"),
        offTimelineSegment: document.getElementById("offTimelineSegment"),
        onTimelineLabel: document.getElementById("onTimelineLabel"),
        offTimelineLabel: document.getElementById("offTimelineLabel"),
        onProgress: document.getElementById("onProgress"),
        offProgress: document.getElementById("offProgress"),
        timelinePlayhead: document.getElementById("timelinePlayhead"),
        timelineTrack: document.getElementById("timelineTrack"),
        onDurationValue: document.getElementById("onDurationValue"),
        onDurationUnit: document.getElementById("onDurationUnit"),
        offDurationValue: document.getElementById("offDurationValue"),
        offDurationUnit: document.getElementById("offDurationUnit"),
        cycleMode: document.getElementById("cycleMode"),
        cycleCountField: document.getElementById("cycleCountField"),
        maxCycles: document.getElementById("maxCycles"),
        dutyCycle: document.getElementById("dutyCycle"),
        cycleSpent: document.getElementById("cycleSpent"),
        cycleSaved: document.getElementById("cycleSaved"),
        projectedLabel: document.getElementById("projectedLabel"),
        projectedTotal: document.getElementById("projectedTotal"),
        turnOnNow: document.getElementById("turnOnNow"),
        turnOffNow: document.getElementById("turnOffNow"),
        rootUrl: document.getElementById("rootUrl"),
        endpointPreview: document.getElementById("endpointPreview"),
        requestTimeout: document.getElementById("requestTimeout"),
        offOnStop: document.getElementById("offOnStop"),
        lastResult: document.getElementById("lastResult"),
        toast: document.getElementById("airconToast"),
    };

    function setConnection(online) {
        if (!elements.connectionStatus) return;
        elements.connectionStatus.classList.toggle("is-online", online);
        elements.connectionStatus.classList.toggle("is-offline", !online);
        elements.connectionStatus.lastChild.textContent = online ? " Online" : " Offline";
    }

    function cleanRootUrl(value) {
        return String(value || "").trim().replace(/\/+$/, "");
    }

    function formatDuration(totalSeconds) {
        const seconds = Math.max(0, Math.round(Number(totalSeconds) || 0));
        if (seconds < 60) return `${seconds} sec`;
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const remaining = seconds % 60;
        const parts = [];
        if (hours) parts.push(`${hours} hr`);
        if (minutes) parts.push(`${minutes} min`);
        if (remaining && !hours) parts.push(`${remaining} sec`);
        return parts.join(" ") || "0 sec";
    }

    function formatNextChange(timestamp) {
        if (!timestamp) return "None scheduled";
        const target = new Date(timestamp).getTime();
        if (!Number.isFinite(target)) return "Unknown";
        const seconds = Math.max(0, Math.ceil((target - Date.now()) / 1000));
        return seconds <= 0 ? "Now" : `In ${formatDuration(seconds)}`;
    }

    function moneyRoundUp(rawValue) {
        const value = Math.max(0, Number(rawValue) || 0);
        return Math.ceil((value - 1e-12) * 100) / 100;
    }

    function formatMoney(rawValue) {
        return `$${moneyRoundUp(rawValue).toFixed(2)}`;
    }

    function costForSeconds(seconds) {
        return (Math.max(0, Number(seconds) || 0) / 3600) * ratePerHour;
    }

    function chooseDurationUnit(seconds) {
        const numeric = Math.max(MIN_DURATION_SECONDS, Number(seconds) || MIN_DURATION_SECONDS);
        if (numeric % 3600 === 0) return { value: numeric / 3600, unit: "hours" };
        if (numeric % 60 === 0) return { value: numeric / 60, unit: "minutes" };
        return { value: numeric, unit: "seconds" };
    }

    function readDurationSeconds(prefix) {
        const valueElement = elements[`${prefix}DurationValue`];
        const unitElement = elements[`${prefix}DurationUnit`];
        const value = Number.parseFloat(valueElement ? valueElement.value : "0");
        const multiplier = UNIT_SECONDS[unitElement ? unitElement.value : "seconds"] || 1;
        if (!Number.isFinite(value)) return 0;
        return Math.round(value * multiplier);
    }

    function writeDurationSeconds(prefix, totalSeconds, preserveUnit) {
        const valueElement = elements[`${prefix}DurationValue`];
        const unitElement = elements[`${prefix}DurationUnit`];
        if (!valueElement || !unitElement) return;

        const seconds = Math.max(MIN_DURATION_SECONDS, Math.min(MAX_DURATION_SECONDS, Math.round(totalSeconds)));
        const unit = preserveUnit ? unitElement.value : chooseDurationUnit(seconds).unit;
        const multiplier = UNIT_SECONDS[unit] || 1;
        const rawValue = seconds / multiplier;
        unitElement.value = unit;
        valueElement.value = Number.isInteger(rawValue) ? String(rawValue) : rawValue.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    }

    function collectSettings() {
        const onSeconds = readDurationSeconds("on");
        const offSeconds = readDurationSeconds("off");
        const timeout = Number.parseInt(elements.requestTimeout.value, 10);
        const maxCycles = Number.parseInt(elements.maxCycles.value, 10);

        if (onSeconds < MIN_DURATION_SECONDS || onSeconds > MAX_DURATION_SECONDS) {
            throw new Error("ON duration must be between 5 seconds and 24 hours.");
        }
        if (offSeconds < MIN_DURATION_SECONDS || offSeconds > MAX_DURATION_SECONDS) {
            throw new Error("OFF duration must be between 5 seconds and 24 hours.");
        }
        if (!Number.isInteger(timeout) || timeout < 2 || timeout > 60) {
            throw new Error("Request timeout must be between 2 and 60 seconds.");
        }
        if (elements.cycleMode.value === "fixed" && (!Number.isInteger(maxCycles) || maxCycles < 1 || maxCycles > 10000)) {
            throw new Error("Cycle count must be between 1 and 10000.");
        }

        return {
            root_url: cleanRootUrl(elements.rootUrl.value),
            on_duration_seconds: onSeconds,
            off_duration_seconds: offSeconds,
            cycle_mode: elements.cycleMode.value,
            max_cycles: elements.cycleMode.value === "fixed" ? maxCycles : 0,
            request_timeout_seconds: timeout,
            turn_off_on_stop: Boolean(elements.offOnStop.checked),
        };
    }

    function syncControls(nextSettings) {
        settings = { ...nextSettings };
        elements.rootUrl.value = settings.root_url || "http://127.0.0.1:8080";
        writeDurationSeconds("on", settings.on_duration_seconds || 600, false);
        writeDurationSeconds("off", settings.off_duration_seconds || 1200, false);
        elements.cycleMode.value = Number(settings.max_cycles) > 0 ? "fixed" : "continuous";
        elements.maxCycles.value = Number(settings.max_cycles) > 0 ? String(settings.max_cycles) : "1";
        elements.requestTimeout.value = String(settings.request_timeout_seconds || 5);
        elements.offOnStop.checked = Boolean(settings.turn_off_on_stop);
        updateCycleCountVisibility();
        renderPreview();
    }

    function updateCycleCountVisibility() {
        const fixed = elements.cycleMode.value === "fixed";
        elements.cycleCountField.hidden = !fixed;
        elements.maxCycles.disabled = !fixed;
    }

    function markDirty() {
        dirty = true;
        renderSaveState();
        renderPreview();
    }

    function renderSaveState() {
        elements.saveState.classList.toggle("is-dirty", dirty && !busy);
        elements.saveState.classList.toggle("is-saving", busy);
        elements.saveState.textContent = busy ? "Applying changes" : dirty ? "Changes not applied" : "All changes applied";
        elements.applyChanges.disabled = !dirty || busy;
    }

    function setBusy(nextBusy) {
        busy = nextBusy;
        renderSaveState();
        renderButtons();
    }

    function setProgress(element, percent) {
        if (element) element.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    }

    function currentElapsedSeconds() {
        const startedAt = new Date(status.phase_started_at || "").getTime();
        if (!Number.isFinite(startedAt)) return 0;
        return Math.max(0, (Date.now() - startedAt) / 1000);
    }

    function sessionDurations() {
        let onSeconds = Number(status.on_seconds_accumulated) || 0;
        let offSeconds = Number(status.off_seconds_accumulated) || 0;
        const elapsed = currentElapsedSeconds();
        if (String(status.phase || "").toUpperCase() === "AC ON") onSeconds += elapsed;
        if (String(status.phase || "").toUpperCase() === "AC OFF") offSeconds += elapsed;
        return { onSeconds, offSeconds };
    }

    function renderPreview() {
        const onSeconds = Math.max(MIN_DURATION_SECONDS, readDurationSeconds("on") || MIN_DURATION_SECONDS);
        const offSeconds = Math.max(MIN_DURATION_SECONDS, readDurationSeconds("off") || MIN_DURATION_SECONDS);
        const totalSeconds = onSeconds + offSeconds;
        const fixed = elements.cycleMode.value === "fixed";
        const maxCycles = Math.max(1, Number.parseInt(elements.maxCycles.value, 10) || 1);
        const duty = totalSeconds ? (onSeconds / totalSeconds) * 100 : 0;

        elements.onTimelineSegment.style.setProperty("--weight", onSeconds);
        elements.offTimelineSegment.style.setProperty("--weight", offSeconds);
        elements.onTimelineLabel.textContent = `ON · ${formatDuration(onSeconds)}`;
        elements.offTimelineLabel.textContent = `OFF · ${formatDuration(offSeconds)}`;
        elements.scheduleSummary.textContent = `${formatDuration(totalSeconds)} per cycle. Changes apply to the current phase immediately.`;
        elements.dutyCycle.textContent = `${duty.toFixed(duty >= 10 ? 0 : 1)}%`;
        elements.cycleSpent.textContent = formatMoney(costForSeconds(onSeconds));
        elements.cycleSaved.textContent = formatMoney(costForSeconds(offSeconds));
        elements.projectedLabel.textContent = fixed ? "Planned total" : "Projected total";
        elements.projectedTotal.textContent = fixed
            ? `${formatMoney(costForSeconds(onSeconds) * maxCycles)} spent · ${formatMoney(costForSeconds(offSeconds) * maxCycles)} saved`
            : "Continuous";

        const root = cleanRootUrl(elements.rootUrl.value) || "http://127.0.0.1:8080";
        elements.endpointPreview.textContent = `${root}/ac_on · ${root}/ac_off`;
        elements.timelineTrack.setAttribute(
            "aria-label",
            `Aircon ON for ${formatDuration(onSeconds)}, then OFF for ${formatDuration(offSeconds)}.`
        );
        renderTimelineProgress();
    }

    function renderTimelineProgress() {
        setProgress(elements.onProgress, 0);
        setProgress(elements.offProgress, 0);
        elements.onTimelineSegment.classList.remove("is-active");
        elements.offTimelineSegment.classList.remove("is-active");
        elements.timelinePlayhead.hidden = true;

        if (!status.active) {
            elements.phaseProgressText.textContent = "Waiting to start";
            return;
        }

        const phase = String(status.phase || "").toUpperCase();
        const elapsed = currentElapsedSeconds();
        const onSeconds = Math.max(MIN_DURATION_SECONDS, readDurationSeconds("on") || MIN_DURATION_SECONDS);
        const offSeconds = Math.max(MIN_DURATION_SECONDS, readDurationSeconds("off") || MIN_DURATION_SECONDS);
        const totalSeconds = Math.max(1, onSeconds + offSeconds);
        let overall = 0;

        if (phase === "AC ON") {
            const progress = Math.min(1, elapsed / onSeconds);
            setProgress(elements.onProgress, progress * 100);
            elements.onTimelineSegment.classList.add("is-active");
            overall = (progress * onSeconds) / totalSeconds;
            elements.phaseProgressText.textContent = `${formatDuration(Math.max(0, onSeconds - elapsed))} left in ON`;
        } else if (phase === "AC OFF") {
            const progress = Math.min(1, elapsed / offSeconds);
            setProgress(elements.onProgress, 100);
            setProgress(elements.offProgress, progress * 100);
            elements.offTimelineSegment.classList.add("is-active");
            overall = (onSeconds + progress * offSeconds) / totalSeconds;
            elements.phaseProgressText.textContent = `${formatDuration(Math.max(0, offSeconds - elapsed))} left in OFF`;
        } else {
            elements.phaseProgressText.textContent = status.phase || "Running";
            return;
        }

        elements.timelinePlayhead.hidden = false;
        elements.timelinePlayhead.style.left = `${Math.max(0, Math.min(100, overall * 100))}%`;
    }

    function renderStatus() {
        const state = ["on", "off"].includes(status.current_state) ? status.current_state : "unknown";
        elements.currentState.textContent = state === "unknown" ? "Unknown" : state.toUpperCase();
        elements.currentPhase.textContent = status.phase || "Stopped";
        elements.stateIcon.classList.remove("is-on", "is-off", "is-unknown");
        elements.stateIcon.classList.add(`is-${state}`);
        elements.stateIcon.querySelector("i").className = state === "on"
            ? "fa-solid fa-snowflake"
            : state === "off"
                ? "fa-regular fa-moon"
                : "fa-solid fa-circle-question";

        elements.nextChange.textContent = formatNextChange(status.next_action_at);
        if (status.active) {
            elements.cyclePosition.textContent = status.max_cycles
                ? `${status.current_cycle || 1} of ${status.max_cycles}`
                : String(status.current_cycle || 1);
            elements.cycleDetail.textContent = status.max_cycles
                ? `${status.completed_cycles || 0} completed`
                : `${status.completed_cycles || 0} completed · continuous`;
        } else {
            elements.cyclePosition.textContent = status.completed_cycles ? String(status.completed_cycles) : "Not running";
            elements.cycleDetail.textContent = status.completed_cycles ? "Completed this session" : "Continuous";
        }

        const durations = sessionDurations();
        elements.sessionSpent.textContent = formatMoney(costForSeconds(durations.onSeconds));
        elements.sessionSaved.textContent = formatMoney(costForSeconds(durations.offSeconds));

        const resultText = elements.lastResult.querySelector("span");
        const resultIcon = elements.lastResult.querySelector("i");
        const hasError = Boolean(status.last_error);
        elements.lastResult.classList.toggle("is-error", hasError);
        resultText.textContent = status.last_result || "Ready.";
        resultIcon.className = hasError ? "fa-solid fa-triangle-exclamation" : "fa-solid fa-circle-info";
        renderButtons();
        renderTimelineProgress();
    }

    function renderButtons() {
        const active = Boolean(status.active);
        elements.startSchedule.disabled = active || busy;
        elements.stopSchedule.disabled = !active || busy;
        elements.skipPhase.disabled = !status.can_skip_phase || busy;
        elements.turnOnNow.disabled = active || busy;
        elements.turnOffNow.disabled = active || busy;
    }

    function showToast(message, isError) {
        window.clearTimeout(toastTimer);
        elements.toast.textContent = message;
        elements.toast.classList.toggle("is-error", Boolean(isError));
        elements.toast.hidden = false;
        toastTimer = window.setTimeout(() => {
            elements.toast.hidden = true;
        }, 3200);
    }

    async function requestJson(url, options) {
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
        if (!response.ok || payload.ok === false) {
            throw new Error(payload.error || "The request failed.");
        }
        return payload;
    }

    async function saveSettings(showSuccess) {
        let payload;
        try {
            payload = collectSettings();
        } catch (error) {
            showToast(error.message, true);
            throw error;
        }

        setBusy(true);
        try {
            const result = await requestJson(body.dataset.settingsUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            settings = result.settings;
            status = result.status;
            dirty = false;
            renderSaveState();
            renderStatus();
            if (showSuccess) showToast(result.message || "Changes applied.", false);
            return true;
        } catch (error) {
            showToast(error.message, true);
            throw error;
        } finally {
            setBusy(false);
        }
    }

    async function runCommand(url, successFallback) {
        setBusy(true);
        try {
            const result = await requestJson(url, { method: "POST" });
            if (result.status) status = result.status;
            renderStatus();
            showToast(result.message || successFallback, false);
        } catch (error) {
            showToast(error.message, true);
        } finally {
            setBusy(false);
        }
    }

    async function startSchedule() {
        try {
            if (dirty) await saveSettings(false);
            await runCommand(body.dataset.startUrl, "Schedule started.");
        } catch (_error) {
            // saveSettings already displayed the validation or server error.
        }
    }

    async function pollStatus() {
        try {
            const result = await requestJson(body.dataset.statusUrl, { method: "GET" });
            setConnection(true);
            ratePerHour = Number(result.rate_per_hour) || ratePerHour;
            status = result.status || status;
            if (!dirty && !busy && result.settings) syncControls(result.settings);
            renderStatus();
        } catch (_error) {
            setConnection(false);
        }
    }

    function bindControls() {
        const editable = [
            elements.onDurationValue,
            elements.onDurationUnit,
            elements.offDurationValue,
            elements.offDurationUnit,
            elements.cycleMode,
            elements.maxCycles,
            elements.rootUrl,
            elements.requestTimeout,
            elements.offOnStop,
        ];

        editable.forEach((control) => {
            const eventName = control.tagName === "SELECT" || control.type === "checkbox" ? "change" : "input";
            control.addEventListener(eventName, () => {
                updateCycleCountVisibility();
                markDirty();
            });
        });

        document.querySelectorAll("[data-adjust][data-seconds]").forEach((button) => {
            button.addEventListener("click", () => {
                const prefix = button.dataset.adjust;
                const delta = Number(button.dataset.seconds) || 0;
                const current = readDurationSeconds(prefix);
                writeDurationSeconds(prefix, current + delta, true);
                markDirty();
            });
        });

        document.querySelectorAll("[data-focus]").forEach((button) => {
            button.addEventListener("click", () => {
                const target = document.getElementById(button.dataset.focus);
                if (target) target.focus();
            });
        });

        elements.applyChanges.addEventListener("click", () => saveSettings(true).catch(() => {}));
        elements.startSchedule.addEventListener("click", startSchedule);
        elements.stopSchedule.addEventListener("click", () => runCommand(body.dataset.stopUrl, "Schedule stopped."));
        elements.skipPhase.addEventListener("click", () => runCommand(body.dataset.skipUrl, "Moving to the next phase."));
        elements.turnOnNow.addEventListener("click", () => runCommand(body.dataset.onUrl, "AC ON sent."));
        elements.turnOffNow.addEventListener("click", () => runCommand(body.dataset.offUrl, "AC OFF sent."));
    }

    function tick() {
        elements.nextChange.textContent = formatNextChange(status.next_action_at);
        const durations = sessionDurations();
        elements.sessionSpent.textContent = formatMoney(costForSeconds(durations.onSeconds));
        elements.sessionSaved.textContent = formatMoney(costForSeconds(durations.offSeconds));
        renderTimelineProgress();
    }

    syncControls(settings);
    bindControls();
    renderStatus();
    renderSaveState();
    setConnection(true);
    window.setInterval(tick, 1000);
    window.setInterval(pollStatus, 2000);
})();
