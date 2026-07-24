(function () {
    "use strict";

    const page = document.querySelector(".voice-page");
    if (!page) return;

    const MAX_AUDIO_QUEUE_CHUNKS = 160;

    const elements = {
        status: document.getElementById("voiceStatusBadge"),
        timer: document.getElementById("voiceCallTimer"),
        start: document.getElementById("voiceStartButton"),
        end: document.getElementById("voiceEndButton"),
        send: document.getElementById("sendToServer"),
        receive: document.getElementById("receiveFromServer"),
        warning: document.getElementById("voiceSecureWarning"),
        muteMic: document.getElementById("voiceMuteMic"),
        muteSpeaker: document.getElementById("voiceMuteSpeaker"),
        volume: document.getElementById("voiceVolume"),
        audio: document.getElementById("serverAudio"),
        inputDevice: document.getElementById("voiceInputDevice"),
        outputDevice: document.getElementById("voiceOutputDevice"),
        outputRow: document.getElementById("voiceOutputDeviceRow"),
        browserMicState: document.getElementById("browserMicState"),
        serverMicState: document.getElementById("serverMicState"),
        browserLevel: document.getElementById("browserLevel"),
        serverLevel: document.getElementById("serverLevel"),
        message: document.getElementById("voiceMessage"),
    };

    let socket = null;
    let stream = null;
    let recorder = null;
    let mediaSource = null;
    let sourceBuffer = null;
    let audioQueue = [];
    let audioUrl = "";
    let timerInterval = null;
    let callStartedAt = 0;
    let micMuted = false;
    let speakerMuted = false;
    let browserAnalyser = null;
    let serverAnalyser = null;
    let browserAudioContext = null;
    let serverAudioContext = null;
    let serverMediaSourceNode = null;
    let animationFrame = null;

    function buildBars(container) {
        container.innerHTML = "";
        for (let index = 0; index < 24; index += 1) container.appendChild(document.createElement("span"));
    }

    buildBars(elements.browserLevel);
    buildBars(elements.serverLevel);

    function setMessage(text, error) {
        const span = elements.message.querySelector("span");
        if (span) span.textContent = text;
        elements.message.classList.toggle("is-error", Boolean(error));
        const icon = elements.message.querySelector("i");
        if (icon) icon.className = error ? "fa-solid fa-triangle-exclamation" : "fa-solid fa-circle-info";
    }

    function setStatus(label, state) {
        elements.status.className = `voice-status-badge is-${state}`;
        elements.status.lastChild.textContent = ` ${label}`;
    }

    function websocketUrl() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        return `${protocol}//${window.location.host}/admin/voice/ws`;
    }

    function formatTimer(seconds) {
        const minutes = Math.floor(seconds / 60);
        const remainder = seconds % 60;
        return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
    }

    function startTimer() {
        callStartedAt = Date.now();
        elements.timer.textContent = "00:00";
        timerInterval = window.setInterval(() => {
            elements.timer.textContent = formatTimer(Math.floor((Date.now() - callStartedAt) / 1000));
        }, 1000);
    }

    function stopTimer() {
        window.clearInterval(timerInterval);
        timerInterval = null;
        elements.timer.textContent = "00:00";
    }

    function setCalling(calling) {
        elements.start.disabled = calling;
        elements.end.disabled = !calling;
        elements.send.disabled = calling;
        elements.receive.disabled = calling;
        elements.inputDevice.disabled = calling;
        elements.outputDevice.disabled = calling;
        elements.muteMic.disabled = !calling || !elements.send.checked;
        elements.muteSpeaker.disabled = !calling || !elements.receive.checked;
        elements.browserMicState.textContent = calling && elements.send.checked ? "Live" : "Off";
        elements.serverMicState.textContent = calling && elements.receive.checked ? "Live" : "Off";
    }

    function chooseRecorderMime() {
        const options = ["audio/webm;codecs=opus", "audio/webm"];
        return options.find((value) => window.MediaRecorder && MediaRecorder.isTypeSupported(value)) || "";
    }

    async function loadDevices() {
        if (!navigator.mediaDevices?.enumerateDevices) return;
        const devices = await navigator.mediaDevices.enumerateDevices();
        const currentInput = elements.inputDevice.value;
        elements.inputDevice.innerHTML = '<option value="">Default microphone</option>';
        devices.filter((device) => device.kind === "audioinput").forEach((device, index) => {
            const option = document.createElement("option");
            option.value = device.deviceId;
            option.textContent = device.label || `Microphone ${index + 1}`;
            elements.inputDevice.appendChild(option);
        });
        elements.inputDevice.value = currentInput;

        if (typeof elements.audio.setSinkId === "function") {
            elements.outputRow.hidden = false;
            elements.outputDevice.hidden = false;
            const currentOutput = elements.outputDevice.value;
            elements.outputDevice.innerHTML = '<option value="">Default speaker</option>';
            devices.filter((device) => device.kind === "audiooutput").forEach((device, index) => {
                const option = document.createElement("option");
                option.value = device.deviceId;
                option.textContent = device.label || `Speaker ${index + 1}`;
                elements.outputDevice.appendChild(option);
            });
            elements.outputDevice.value = currentOutput;
        }
    }

    function setupMediaSource() {
        if (!elements.receive.checked) return;
        mediaSource = new MediaSource();
        audioUrl = URL.createObjectURL(mediaSource);
        elements.audio.src = audioUrl;
        mediaSource.addEventListener("sourceopen", () => {
            const mime = "audio/webm;codecs=opus";
            if (!MediaSource.isTypeSupported(mime)) {
                setMessage("This browser cannot play the server WebM/Opus stream.", true);
                return;
            }
            sourceBuffer = mediaSource.addSourceBuffer(mime);
            try {
                sourceBuffer.mode = "sequence";
            } catch (_error) {
                // Some browsers expose only the default segments mode.
            }
            sourceBuffer.addEventListener("updateend", appendNextAudioChunk);
            appendNextAudioChunk();
            setupServerAnalyser();
        }, { once: true });
    }

    function appendNextAudioChunk() {
        if (!sourceBuffer || sourceBuffer.updating || !audioQueue.length) return;
        try {
            sourceBuffer.appendBuffer(audioQueue.shift());
        } catch (error) {
            setMessage(`Could not decode server audio: ${error.message}`, true);
        }
    }

    function setupBrowserAnalyser() {
        if (!stream) return;
        browserAudioContext?.close().catch(() => {});
        browserAudioContext = new AudioContext();
        const source = browserAudioContext.createMediaStreamSource(stream);
        browserAnalyser = browserAudioContext.createAnalyser();
        browserAnalyser.fftSize = 128;
        source.connect(browserAnalyser);
        browserAudioContext.resume().catch(() => {});
    }

    function setupServerAnalyser() {
        try {
            if (!serverAudioContext) serverAudioContext = new AudioContext();
            if (!serverMediaSourceNode) {
                serverMediaSourceNode = serverAudioContext.createMediaElementSource(elements.audio);
            }
            serverMediaSourceNode.disconnect();
            serverAnalyser = serverAudioContext.createAnalyser();
            serverAnalyser.fftSize = 128;
            serverMediaSourceNode.connect(serverAnalyser);
            serverAnalyser.connect(serverAudioContext.destination);
            serverAudioContext.resume().catch(() => {});
        } catch (_error) {
            serverAnalyser = null;
        }
    }

    function levelValue(analyser) {
        if (!analyser) return 0;
        const data = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(data);
        return data.reduce((sum, value) => sum + value, 0) / Math.max(1, data.length) / 255;
    }

    function renderLevel(container, level) {
        const bars = Array.from(container.children);
        bars.forEach((bar, index) => {
            const threshold = index / bars.length;
            const active = level > threshold * 0.8;
            bar.style.height = `${Math.max(6, (index + 1) / bars.length * 100)}%`;
            bar.style.opacity = active ? "1" : "0.16";
        });
    }

    function animateLevels() {
        renderLevel(elements.browserLevel, micMuted ? 0 : levelValue(browserAnalyser));
        renderLevel(elements.serverLevel, speakerMuted ? 0 : levelValue(serverAnalyser));
        animationFrame = requestAnimationFrame(animateLevels);
    }

    async function startBrowserMicrophone() {
        if (!elements.send.checked) return;
        if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
            throw new Error("HTTPS is required for browser microphone access on this address.");
        }
        const deviceId = elements.inputDevice.value;
        stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                deviceId: deviceId ? { exact: deviceId } : undefined,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });
        await loadDevices();
        setupBrowserAnalyser();
        const mimeType = chooseRecorderMime();
        if (!mimeType) throw new Error("This browser cannot record WebM/Opus audio.");
        recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 48000 });
        recorder.addEventListener("dataavailable", async (event) => {
            if (!event.data.size || !socket || socket.readyState !== WebSocket.OPEN) return;
            socket.send(await event.data.arrayBuffer());
        });
        recorder.start(250);
    }

    async function startCall() {
        if (!elements.send.checked && !elements.receive.checked) {
            setMessage("Choose at least one audio direction.", true);
            return;
        }
        setStatus("Connecting", "connecting");
        setMessage("Connecting to the server audio bridge.", false);
        elements.warning.hidden = true;

        try {
            await startBrowserMicrophone();
            setupMediaSource();
            socket = new WebSocket(websocketUrl());
            socket.binaryType = "arraybuffer";
            socket.addEventListener("open", () => {
                socket.send(JSON.stringify({
                    type: "start",
                    send_to_server: elements.send.checked,
                    receive_from_server: elements.receive.checked,
                }));
            });
            socket.addEventListener("message", (event) => {
                if (typeof event.data === "string") {
                    let payload;
                    try {
                        payload = JSON.parse(event.data);
                    } catch (_error) {
                        return;
                    }
                    if (payload.type === "started") {
                        setCalling(true);
                        setStatus("Live", "live");
                        setMessage("Call connected.", false);
                        startTimer();
                        animateLevels();
                        elements.audio.play().catch(() => {});
                    } else if (payload.type === "error") {
                        setMessage(payload.message || "Voice bridge error.", true);
                        endCall();
                    }
                } else if (event.data instanceof ArrayBuffer) {
                    if (audioQueue.length >= MAX_AUDIO_QUEUE_CHUNKS) audioQueue.shift();
                    audioQueue.push(event.data);
                    appendNextAudioChunk();
                }
            });
            socket.addEventListener("close", () => {
                if (elements.end.disabled === false) endCall(false);
            });
            socket.addEventListener("error", () => {
                setMessage("Could not open the voice WebSocket.", true);
                endCall();
            });
        } catch (error) {
            if (String(error.message).includes("HTTPS")) elements.warning.hidden = false;
            setMessage(error.message || "Could not start the call.", true);
            endCall(false);
        }
    }

    function cleanupMedia() {
        if (recorder && recorder.state !== "inactive") {
            try {
                recorder.stop();
            } catch (_error) {
                // Recorder may already be stopping after a socket failure.
            }
        }
        recorder = null;
        stream?.getTracks().forEach((track) => track.stop());
        stream = null;
        browserAnalyser = null;
        serverAnalyser = null;
        browserAudioContext?.close().catch(() => {});
        browserAudioContext = null;
        if (animationFrame !== null) cancelAnimationFrame(animationFrame);
        animationFrame = null;
        audioQueue = [];
        sourceBuffer = null;
        mediaSource = null;
        elements.audio.pause();
        elements.audio.removeAttribute("src");
        elements.audio.load();
        if (audioUrl) URL.revokeObjectURL(audioUrl);
        audioUrl = "";
        renderLevel(elements.browserLevel, 0);
        renderLevel(elements.serverLevel, 0);
    }

    function endCall(sendStop = true) {
        if (sendStop && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
        socket?.close();
        socket = null;
        cleanupMedia();
        stopTimer();
        micMuted = false;
        speakerMuted = false;
        elements.muteMic.setAttribute("aria-pressed", "false");
        elements.muteSpeaker.setAttribute("aria-pressed", "false");
        elements.muteMic.querySelector("span").textContent = "Mute mic";
        elements.muteMic.querySelector("i").className = "fa-solid fa-microphone";
        elements.muteSpeaker.querySelector("span").textContent = "Mute speaker";
        elements.muteSpeaker.querySelector("i").className = "fa-solid fa-volume-high";
        setCalling(false);
        setStatus("Ready", "idle");
        elements.browserMicState.textContent = "Off";
        elements.serverMicState.textContent = "Off";
    }

    elements.start.addEventListener("click", startCall);
    elements.end.addEventListener("click", () => {
        setMessage("Call ended.", false);
        endCall();
    });
    elements.muteMic.addEventListener("click", () => {
        micMuted = !micMuted;
        stream?.getAudioTracks().forEach((track) => { track.enabled = !micMuted; });
        elements.muteMic.setAttribute("aria-pressed", String(micMuted));
        elements.muteMic.querySelector("span").textContent = micMuted ? "Unmute mic" : "Mute mic";
        elements.muteMic.querySelector("i").className = micMuted ? "fa-solid fa-microphone-slash" : "fa-solid fa-microphone";
        elements.browserMicState.textContent = micMuted ? "Muted" : "Live";
    });
    elements.muteSpeaker.addEventListener("click", () => {
        speakerMuted = !speakerMuted;
        elements.audio.muted = speakerMuted;
        elements.muteSpeaker.setAttribute("aria-pressed", String(speakerMuted));
        elements.muteSpeaker.querySelector("span").textContent = speakerMuted ? "Unmute speaker" : "Mute speaker";
        elements.muteSpeaker.querySelector("i").className = speakerMuted ? "fa-solid fa-volume-xmark" : "fa-solid fa-volume-high";
        elements.serverMicState.textContent = speakerMuted ? "Muted" : "Live";
    });
    elements.volume.addEventListener("input", () => { elements.audio.volume = Number(elements.volume.value); });
    elements.outputDevice.addEventListener("change", async () => {
        if (typeof elements.audio.setSinkId === "function") await elements.audio.setSinkId(elements.outputDevice.value);
    });
    elements.send.addEventListener("change", () => {
        elements.warning.hidden = !(elements.send.checked && !window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1");
    });
    navigator.mediaDevices?.addEventListener?.("devicechange", () => {
        if (!socket) loadDevices().catch(() => {});
    });

    setCalling(false);
    loadDevices().catch(() => {});
})();
