(function () {
    "use strict";

    const DEFAULT_QUALITY = {
        max_width: 960,
        jpeg_quality: 0.58,
        label: "Balanced",
    };
    const DEFAULT_FPS = {
        target_fps: 10,
        interval_ms: 100,
        label: "10 fps",
    };
    const FRAME_REQUEST_TIMEOUT_MS = 1800;
    const TTS_SEQUENCE_KEY = "tamestorage.remote-voice.sequence";
    const TTS_UNLOCK_KEY = "tamestorage.remote-voice.unlocked";
    const TTS_RETRY_INITIAL_MS = 500;
    const TTS_RETRY_MAX_MS = 10000;

    function readStoredNumber(key, fallback = 0) {
        try {
            const value = Number(window.localStorage.getItem(key));
            return Number.isFinite(value) && value >= 0 ? value : fallback;
        } catch (_error) {
            return fallback;
        }
    }

    function writeStoredNumber(key, value) {
        try {
            window.localStorage.setItem(key, String(Math.max(0, Math.floor(value))));
        } catch (_error) {
            // Private browsing/storage restrictions should not stop voice playback.
        }
    }

    function hasStoredFlag(key) {
        try {
            return window.localStorage.getItem(key) === "1";
        } catch (_error) {
            return false;
        }
    }

    function writeStoredFlag(key) {
        try {
            window.localStorage.setItem(key, "1");
        } catch (_error) {
            // Private browsing/storage restrictions should not stop voice playback.
        }
    }

    function initCapture(root) {
        const preview = root.querySelector("[data-camera-preview]");
        const status = root.querySelector("[data-camera-status]");
        const connection = root.querySelector("[data-camera-connection]");
        const fpsDisplay = root.querySelector("[data-camera-fps]");
        const targetFpsDisplay = root.querySelector("[data-camera-target-fps]");
        const facingDisplay = root.querySelector("[data-camera-facing]");
        const qualityDisplay = root.querySelector("[data-camera-quality]");
        const startButton = root.querySelector("[data-camera-start]");
        const stopButton = root.querySelector("[data-camera-stop]");
        const switchButton = root.querySelector("[data-camera-switch]");
        const audioStateDisplay = root.querySelector("[data-camera-audio-state]");
        const ttsChunkUrl = root.dataset.ttsChunkUrl;
        const ttsEnableButton = root.querySelector("[data-camera-tts-enable]");
        const ttsStateDisplay = root.querySelector("[data-camera-tts-state]");
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d", { alpha: false });
        const frameUrl = root.dataset.frameUrl;
        const audioFrameUrl = root.dataset.audioFrameUrl;
        const controlUrl = root.dataset.controlUrl;
        let mediaStream = null;
        let facingMode = "environment";
        let videoDevices = [];
        let videoDeviceIndex = 0;
        let qualityConfig = { ...DEFAULT_QUALITY };
        let fpsConfig = { ...DEFAULT_FPS };
        let lastControlSequence = 0;
        let controlsReady = false;
        let controlRequestInFlight = false;
        let serverAudioEnabled = false;
        let audioEnabled = false;
        let audioGeneration = 0;
        let audioStream = null;
        let audioContext = null;
        let audioSource = null;
        let audioProcessor = null;
        let audioMute = null;
        let audioUploadInFlight = false;
        let pendingAudioChunk = null;
        let ttsEnabled = false;
        let ttsSequence = readStoredNumber(TTS_SEQUENCE_KEY, 0);
        let ttsAudioContext = null;
        let ttsNextAudioTime = 0;
        let ttsPollInFlight = false;
        let ttsPollTimer = null;
        let ttsRetryDelay = TTS_RETRY_INITIAL_MS;
        let stopping = false;
        let nextFrameTimer = null;
        let uploadedFrames = [];

        function updateFps() {
            const now = performance.now();
            uploadedFrames = uploadedFrames.filter((timestamp) => now - timestamp < 1000);
            if (fpsDisplay) fpsDisplay.textContent = uploadedFrames.length.toFixed(1);
        }

        function resetMetrics() {
            uploadedFrames = [];
            updateFps();
            updateCameraDisplay();
        }

        function updateCameraDisplay() {
            if (!facingDisplay) return;
            const selectedDevice = videoDevices[videoDeviceIndex];
            facingDisplay.textContent = selectedDevice?.label || (videoDevices.length ? `Camera ${videoDeviceIndex + 1}` : facingMode === "environment" ? "Rear" : "Front");
        }

        async function refreshVideoDevices() {
            if (!navigator.mediaDevices?.enumerateDevices) return;
            try {
                videoDevices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "videoinput");
                const activeDeviceId = mediaStream?.getVideoTracks()[0]?.getSettings().deviceId;
                const activeIndex = videoDevices.findIndex((device) => device.deviceId === activeDeviceId);
                if (activeIndex >= 0) videoDeviceIndex = activeIndex;
                else if (videoDeviceIndex >= videoDevices.length) videoDeviceIndex = 0;
                updateCameraDisplay();
            } catch (_error) {
                // The facing-mode fallback remains available when enumeration is blocked.
            }
        }

        function applyQuality(config, preset) {
            if (!config) return;
            qualityConfig = { ...qualityConfig, ...config };
            if (qualityDisplay) qualityDisplay.textContent = config.label || preset || qualityConfig.label;
        }

        function applyFps(config, preset) {
            if (!config) return;
            fpsConfig = { ...fpsConfig, ...config };
            if (targetFpsDisplay) targetFpsDisplay.textContent = `${config.target_fps || preset} fps`;
        }

        function setAudioDisplay(active, message) {
            if (audioStateDisplay) audioStateDisplay.textContent = message || (active ? "Sound is on." : "Sound is off.");
        }

        function setTtsDisplay(message) {
            if (ttsStateDisplay) ttsStateDisplay.textContent = message;
        }

        function scheduleTtsPoll(delay = 120) {
            if (!ttsEnabled || ttsPollTimer !== null) return;
            ttsPollTimer = window.setTimeout(() => {
                ttsPollTimer = null;
                pollTts();
            }, delay);
        }

        async function enableTtsPlayback() {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!ttsChunkUrl || !AudioContextClass) throw new Error("This browser cannot play remote voice.");
            if (!ttsAudioContext) ttsAudioContext = new AudioContextClass();
            if (ttsAudioContext.state === "suspended") await ttsAudioContext.resume();
            if (ttsAudioContext.state !== "running") throw new Error("Tap Enable remote voice once to allow phone playback.");
            ttsEnabled = true;
            writeStoredFlag(TTS_UNLOCK_KEY);
            ttsRetryDelay = TTS_RETRY_INITIAL_MS;
            if (ttsEnableButton) {
                ttsEnableButton.textContent = "Remote voice enabled";
                ttsEnableButton.disabled = true;
            }
            setTtsDisplay("Remote voice is ready.");
            pollTts();
        }

        async function unlockTtsVoice() {
            try {
                await enableTtsPlayback();
            } catch (error) {
                ttsEnabled = false;
                setTtsDisplay(error.message);
            }
        }

        async function restoreTtsPlayback() {
            if (!hasStoredFlag(TTS_UNLOCK_KEY)) return;
            setTtsDisplay("Restoring remote voice…");
            try {
                await enableTtsPlayback();
            } catch (_error) {
                setTtsDisplay("Remote voice will retry automatically. Tap Enable once if the browser blocks playback.");
            }
        }

        function stopTtsPlayback() {
            ttsEnabled = false;
            if (ttsPollTimer !== null) {
                window.clearTimeout(ttsPollTimer);
                ttsPollTimer = null;
            }
            ttsNextAudioTime = 0;
            if (ttsAudioContext && ttsAudioContext.state !== "closed") ttsAudioContext.close();
            ttsAudioContext = null;
        }

        async function pollTts() {
            if (!ttsEnabled || ttsPollInFlight || !ttsChunkUrl) return;
            ttsPollInFlight = true;
            try {
                const audioContext = ttsAudioContext;
                if (!audioContext) return;
                if (audioContext.state === "suspended") await audioContext.resume();
                if (audioContext.state !== "running") throw new Error("Remote voice is paused; retrying playback.");
                const response = await fetch(`${ttsChunkUrl}?after=${ttsSequence}`, { credentials: "same-origin", cache: "no-store" });
                if (response.status === 204) {
                    if (response.headers.get("X-TTS-Sequence-Reset") === "1") {
                        ttsSequence = 0;
                        writeStoredNumber(TTS_SEQUENCE_KEY, ttsSequence);
                    }
                    ttsRetryDelay = TTS_RETRY_INITIAL_MS;
                    return;
                }
                if (!response.ok) throw new Error("Remote voice is unavailable.");
                const sequence = Number(response.headers.get("X-TTS-Sequence") || 0);
                const audioBuffer = await audioContext.decodeAudioData(await response.arrayBuffer());
                if (!ttsEnabled || audioContext !== ttsAudioContext) return;
                const source = audioContext.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(audioContext.destination);
                ttsNextAudioTime = Math.max(ttsNextAudioTime, audioContext.currentTime + 0.03);
                source.start(ttsNextAudioTime);
                ttsNextAudioTime += audioBuffer.duration;
                ttsSequence = Math.max(ttsSequence, sequence);
                writeStoredNumber(TTS_SEQUENCE_KEY, ttsSequence);
                ttsRetryDelay = TTS_RETRY_INITIAL_MS;
                setTtsDisplay("Voice is playing on the phone.");
            } catch (error) {
                if (ttsEnabled) {
                    setTtsDisplay("Voice connection failed; retrying automatically…");
                    ttsRetryDelay = Math.min(ttsRetryDelay * 2, TTS_RETRY_MAX_MS);
                }
            } finally {
                ttsPollInFlight = false;
                if (ttsEnabled) scheduleTtsPoll(ttsRetryDelay);
            }
        }

        function encodePcm16(samples) {
            const pcm = new Int16Array(samples.length);
            for (let index = 0; index < samples.length; index += 1) {
                const sample = Math.max(-1, Math.min(1, samples[index]));
                pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            }
            return pcm.buffer;
        }

        async function uploadAudioChunk(chunk) {
            if (!audioFrameUrl || !audioEnabled || !audioStream) return;
            if (audioUploadInFlight) {
                pendingAudioChunk = chunk;
                return;
            }
            audioUploadInFlight = true;
            try {
                const response = await fetch(audioFrameUrl, {
                    method: "POST",
                    body: chunk.data,
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "audio/pcm",
                        "X-Audio-Channels": "1",
                        "X-Audio-Sample-Rate": String(chunk.sampleRate),
                    },
                    cache: "no-store",
                });
                if (!response.ok) throw new Error("The server rejected the audio chunk.");
            } catch (error) {
                if (audioEnabled) setAudioDisplay(false, error.message);
            } finally {
                audioUploadInFlight = false;
                const nextChunk = pendingAudioChunk;
                pendingAudioChunk = null;
                if (nextChunk) uploadAudioChunk(nextChunk);
            }
        }

        function stopAudio() {
            audioEnabled = false;
            audioGeneration += 1;
            pendingAudioChunk = null;
            if (audioProcessor) audioProcessor.disconnect();
            if (audioSource) audioSource.disconnect();
            if (audioMute) audioMute.disconnect();
            if (audioStream) audioStream.getTracks().forEach((track) => track.stop());
            if (audioContext && audioContext.state !== "closed") audioContext.close();
            audioProcessor = null;
            audioSource = null;
            audioMute = null;
            audioStream = null;
            audioContext = null;
            setAudioDisplay(false);
        }

        async function startAudio() {
            if (!audioFrameUrl || audioStream) {
                if (audioStream) setAudioDisplay(true);
                return;
            }
            const generation = ++audioGeneration;
            audioEnabled = true;
            try {
                const stream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                    },
                    video: false,
                });
                if (!audioEnabled || generation !== audioGeneration) {
                    stream.getTracks().forEach((track) => track.stop());
                    return;
                }
                audioStream = stream;
                const AudioContextClass = window.AudioContext || window.webkitAudioContext;
                if (!AudioContextClass) throw new Error("This browser does not support audio capture.");
                audioContext = new AudioContextClass();
                if (audioContext.state === "suspended") await audioContext.resume();
                audioSource = audioContext.createMediaStreamSource(audioStream);
                audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);
                audioMute = audioContext.createGain();
                audioMute.gain.value = 0;
                audioProcessor.onaudioprocess = (event) => {
                    if (audioEnabled && audioStream && audioContext) {
                        uploadAudioChunk({ data: encodePcm16(event.inputBuffer.getChannelData(0)), sampleRate: audioContext.sampleRate });
                    }
                };
                audioSource.connect(audioProcessor);
                audioProcessor.connect(audioMute);
                audioMute.connect(audioContext.destination);
                setAudioDisplay(true);
            } catch (error) {
                audioEnabled = false;
                setAudioDisplay(false, error.name === "NotAllowedError" ? "Microphone permission was denied." : `Could not start sound: ${error.message}`);
            }
        }

        async function applyAudioSetting(enabled) {
            serverAudioEnabled = enabled;
            if (enabled) await startAudio();
            else stopAudio();
        }

        function recordUploadedFrame() {
            uploadedFrames.push(performance.now());
            updateFps();
        }

        function setStatus(message, kind) {
            if (status) {
                status.textContent = message;
                status.dataset.state = kind || "";
            }
            if (connection) {
                connection.dataset.state = kind || "";
                const label = connection.querySelector("span");
                if (label) label.textContent = message;
            }
        }

        function setControls(active) {
            if (startButton) startButton.disabled = active;
            if (stopButton) stopButton.disabled = !active;
            if (switchButton) switchButton.hidden = !active;
        }

        function stopCamera(stopSound = false) {
            stopping = true;
            if (nextFrameTimer) {
                window.clearTimeout(nextFrameTimer);
                nextFrameTimer = null;
            }
            if (mediaStream) {
                mediaStream.getTracks().forEach((track) => track.stop());
            }
            mediaStream = null;
            if (preview) {
                preview.pause();
                preview.srcObject = null;
            }
            if (stopSound) stopAudio();
            setControls(false);
            resetMetrics();
            setStatus("Camera is off.", "idle");
        }

        function canvasBlob() {
            return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", qualityConfig.jpeg_quality));
        }

        function scheduleNextFrame(delay = fpsConfig.interval_ms) {
            if (nextFrameTimer) window.clearTimeout(nextFrameTimer);
            if (mediaStream && !stopping) nextFrameTimer = window.setTimeout(sendFrame, delay);
        }

        async function sendFrame() {
            nextFrameTimer = null;
            const sourceStream = mediaStream;
            if (!sourceStream || stopping || !preview || !context) return;
            if (!preview.videoWidth || !preview.videoHeight) {
                scheduleNextFrame(250);
                return;
            }

            canvas.width = Math.min(preview.videoWidth, qualityConfig.max_width);
            canvas.height = Math.round(canvas.width * (preview.videoHeight / preview.videoWidth));
            context.drawImage(preview, 0, 0, canvas.width, canvas.height);
            const blob = await canvasBlob();
            if (!blob || sourceStream !== mediaStream || stopping) return;

            const controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), FRAME_REQUEST_TIMEOUT_MS);
            try {
                const response = await fetch(frameUrl, {
                    method: "POST",
                    body: blob,
                    credentials: "same-origin",
                    headers: { "Content-Type": "image/jpeg", Accept: "application/json" },
                    signal: controller.signal,
                    cache: "no-store",
                });
                if (!response.ok) throw new Error("The server rejected the camera frame.");
                if (sourceStream === mediaStream && !stopping) {
                    recordUploadedFrame();
                    setStatus("Camera is live and sharing.", "live");
                }
            } catch (error) {
                if (!stopping) setStatus(error.name === "AbortError" ? "Server is slow; retrying…" : error.message, "error");
            } finally {
                window.clearTimeout(timeout);
                if (sourceStream === mediaStream && !stopping) scheduleNextFrame();
            }
        }

        async function startCamera() {
            if (!navigator.mediaDevices?.getUserMedia) {
                setStatus("This browser does not support camera access.", "error");
                return;
            }
            stopCamera();
            stopping = false;
            setStatus("Requesting camera permission…", "starting");
            try {
                const videoConstraints = {
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                };
                const selectedDevice = videoDevices[videoDeviceIndex];
                const preferredCamera = selectedDevice?.deviceId
                    ? { deviceId: { exact: selectedDevice.deviceId } }
                    : { facingMode: { exact: facingMode } };
                try {
                    mediaStream = await navigator.mediaDevices.getUserMedia({
                        audio: false,
                        video: { ...videoConstraints, ...preferredCamera },
                    });
                } catch (error) {
                    if (!['OverconstrainedError', 'NotFoundError'].includes(error.name)) throw error;
                    mediaStream = await navigator.mediaDevices.getUserMedia({
                        audio: false,
                        video: { ...videoConstraints, facingMode: { ideal: facingMode } },
                    });
                }
                if (stopping) {
                    mediaStream.getTracks().forEach((track) => track.stop());
                    mediaStream = null;
                    return;
                }
                preview.srcObject = mediaStream;
                await preview.play();
                const settings = mediaStream.getVideoTracks()[0]?.getSettings();
                if (settings?.facingMode) facingMode = settings.facingMode;
                await refreshVideoDevices();
                resetMetrics();
                setControls(true);
                setStatus("Camera started. Sharing compressed frames…", "live");
                sendFrame();
            } catch (error) {
                mediaStream = null;
                setControls(false);
                setStatus(error.name === "NotAllowedError" ? "Camera permission was denied." : `Could not start camera: ${error.message}`, "error");
            }
        }

        async function switchCamera() {
            if (!videoDevices.length) await refreshVideoDevices();
            if (videoDevices.length > 1) {
                videoDeviceIndex = (videoDeviceIndex + 1) % videoDevices.length;
            } else {
                facingMode = facingMode === "environment" ? "user" : "environment";
            }
            await startCamera();
        }

        async function pollControls() {
            if (!controlUrl || controlRequestInFlight) return;
            controlRequestInFlight = true;
            try {
                const query = controlsReady ? `?after=${lastControlSequence}` : "?bootstrap=1";
                const response = await fetch(`${controlUrl}${query}`, {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                    cache: "no-store",
                });
                if (!response.ok) throw new Error("Remote camera controls unavailable.");
                const data = await response.json();
                applyQuality(data.quality, data.quality_preset);
                applyFps(data.fps, data.fps_preset);
                const requestedAudio = Boolean(data.audio_enabled);
                if (!controlsReady) {
                    lastControlSequence = Number(data.latest_sequence || data.sequence || 0);
                    controlsReady = true;
                    if (requestedAudio) await applyAudioSetting(true);
                    return;
                }
                if (requestedAudio !== serverAudioEnabled) await applyAudioSetting(requestedAudio);
                const commands = Array.isArray(data.commands)
                    ? data.commands
                    : data.action
                        ? [{ sequence: data.sequence, action: data.action }]
                        : [];
                for (const command of commands) {
                    if (Number(command.sequence || 0) <= lastControlSequence) continue;
                    if (command.action === "switch_camera") {
                        await switchCamera();
                    } else if (command.action === "start_camera" && !mediaStream) {
                        await startCamera();
                    } else if (command.action === "stop_camera") {
                        stopCamera(true);
                    }
                }
                if (commands.length) {
                    lastControlSequence = Math.max(...commands.map((command) => Number(command.sequence || 0)));
                    if (mediaStream && !stopping) {
                        setStatus(`Remote settings applied: ${data.quality?.label || data.quality_preset}, ${data.fps?.label || data.fps_preset}.`, "live");
                    }
                } else {
                    lastControlSequence = Math.max(lastControlSequence, Number(data.latest_sequence || 0));
                }
            } catch (_error) {
                // The capture loop continues; the next poll can pick up the command.
            } finally {
                controlRequestInFlight = false;
            }
        }

        ttsEnableButton?.addEventListener("click", unlockTtsVoice);
        startButton?.addEventListener("click", () => {
            unlockTtsVoice();
            startCamera();
        });
        stopButton?.addEventListener("click", () => stopCamera(true));
        switchButton?.addEventListener("click", switchCamera);
        window.addEventListener("pagehide", () => {
            stopCamera(true);
            stopTtsPlayback();
        });
        window.addEventListener("pageshow", restoreTtsPlayback);
        window.setInterval(updateFps, 500);
        window.setInterval(pollControls, 1000);
        pollControls();
        restoreTtsPlayback();
        resetMetrics();
        setControls(false);
    }

    function initViewer(root) {
        const statusUrl = root.dataset.statusUrl;
        const title = root.querySelector("[data-camera-viewer-title]");
        const message = root.querySelector("[data-camera-viewer-status]");
        const dot = root.querySelector("[data-camera-viewer-dot]");
        const placeholder = root.querySelector("[data-camera-viewer-placeholder]");
        const stream = root.querySelector("[data-camera-stream]");
        const controlUrl = root.dataset.controlUrl;
        const audioUrl = root.dataset.audioUrl;
        const ttsUrl = root.dataset.ttsUrl;
        const ttsMaxChars = Number(root.dataset.ttsMaxChars || 1000);
        const remoteStart = root.querySelector("[data-camera-remote-start]");
        const remoteStop = root.querySelector("[data-camera-remote-stop]");
        const remoteOpen = root.querySelector("[data-camera-remote-open]");
        const remoteSwitch = root.querySelector("[data-camera-remote-switch]");
        const qualitySelect = root.querySelector("[data-camera-quality]");
        const fpsSelect = root.querySelector("[data-camera-fps-select]");
        const controlStatus = root.querySelector("[data-camera-control-status]");
        const audioToggle = root.querySelector("[data-camera-audio-toggle]");
        const audioToggleLabel = root.querySelector("[data-camera-audio-label]");
        const audioStatus = root.querySelector("[data-camera-audio-status]");
        const ttsForm = root.querySelector("[data-camera-tts-form]");
        const ttsInput = root.querySelector("[data-camera-tts-input]");
        const ttsSubmit = root.querySelector("[data-camera-tts-submit]");
        const ttsStatus = root.querySelector("[data-camera-tts-status]");
        let controlRequestInFlight = false;
        let serverAudioEnabled = false;
        let audioPlaying = false;
        let audioSequence = 0;
        let audioContext = null;
        let nextAudioTime = 0;
        let audioPollInFlight = false;

        function setAudioStatus(message) {
            if (audioStatus) audioStatus.textContent = message;
        }

        async function startAudioPlayback() {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!audioUrl || !AudioContextClass) {
                setAudioStatus("This browser cannot play streamed sound.");
                return;
            }
            if (!audioContext) audioContext = new AudioContextClass();
            if (audioContext.state === "suspended") await audioContext.resume();
            audioPlaying = true;
            setAudioStatus("Listening for phone sound…");
            pollAudio();
        }

        function stopAudioPlayback() {
            audioPlaying = false;
            audioSequence = 0;
            nextAudioTime = 0;
            if (audioContext && audioContext.state !== "closed") audioContext.close();
            audioContext = null;
            setAudioStatus("Sound is off.");
        }

        async function sendTts(event) {
            event.preventDefault();
            if (!ttsUrl || !ttsInput || !ttsSubmit) return;
            const text = ttsInput.value.trim();
            if (!text) {
                if (ttsStatus) ttsStatus.textContent = "Type a message first.";
                ttsInput.focus();
                return;
            }
            if (text.length > ttsMaxChars) {
                if (ttsStatus) ttsStatus.textContent = `Text is limited to ${ttsMaxChars} characters.`;
                return;
            }
            ttsInput.disabled = true;
            ttsSubmit.disabled = true;
            const requestId = window.crypto?.randomUUID
                ? window.crypto.randomUUID()
                : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
            try {
                let data = null;
                let lastError = null;
                let succeeded = false;
                const retryDelays = [0, 700, 1800];
                for (let attempt = 0; attempt < retryDelays.length; attempt += 1) {
                    if (retryDelays[attempt]) await new Promise((resolve) => window.setTimeout(resolve, retryDelays[attempt]));
                    if (ttsStatus) ttsStatus.textContent = attempt ? `Voice request retry ${attempt}/${retryDelays.length - 1}…` : "Generating voice…";
                    try {
                        const response = await fetch(ttsUrl, {
                            method: "POST",
                            credentials: "same-origin",
                            headers: {
                                "Content-Type": "application/json",
                                Accept: "application/json",
                                "X-TTS-Request-Id": requestId,
                            },
                            body: JSON.stringify({ text, request_id: requestId }),
                        });
                        data = await response.json().catch(() => ({}));
                        if (response.ok) {
                            succeeded = true;
                            break;
                        }
                        lastError = new Error(data.error || "Could not generate voice.");
                        if (![408, 425, 429, 500, 502, 503, 504].includes(response.status)) {
                            lastError.retryable = false;
                            throw lastError;
                        }
                    } catch (error) {
                        lastError = error;
                        if (error.retryable === false || attempt === retryDelays.length - 1) throw error;
                    }
                }
                if (!succeeded) throw lastError || new Error("Could not generate voice.");
                ttsInput.value = "";
                if (ttsStatus) ttsStatus.textContent = data.message || "Voice queued for the phone speaker.";
            } catch (error) {
                if (ttsStatus) ttsStatus.textContent = `${error.message} Any clip already queued will still be delivered when the phone reconnects.`;
            } finally {
                ttsInput.disabled = false;
                ttsSubmit.disabled = false;
                ttsInput.focus();
            }
        }

        async function pollAudio() {
            if (!audioPlaying || audioPollInFlight || !audioUrl) return;
            audioPollInFlight = true;
            try {
                const response = await fetch(`${audioUrl}?after=${audioSequence}`, { credentials: "same-origin", cache: "no-store" });
                if (response.status === 204) return;
                if (!response.ok) throw new Error("Sound stream unavailable.");
                const sequence = Number(response.headers.get("X-Audio-Sequence") || 0);
                const audioBuffer = await audioContext.decodeAudioData(await response.arrayBuffer());
                if (!audioPlaying || !audioContext) return;
                const source = audioContext.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(audioContext.destination);
                nextAudioTime = Math.max(nextAudioTime, audioContext.currentTime + 0.03);
                source.start(nextAudioTime);
                nextAudioTime += audioBuffer.duration;
                audioSequence = Math.max(audioSequence, sequence);
                setAudioStatus("Sound is live.");
            } catch (error) {
                if (audioPlaying) setAudioStatus(error.message);
            } finally {
                audioPollInFlight = false;
                if (audioPlaying) window.setTimeout(pollAudio, 40);
            }
        }

        function updateViewer(data) {
            const active = Boolean(data.active);
            root.dataset.state = active ? "live" : "idle";
            if (title) title.textContent = active ? "Phone camera is live" : "Waiting for phone camera";
            if (message) {
                message.textContent = active
                    ? `Receiving frames · ${Number(data.fps || 0).toFixed(1)} / ${Number(data.target_fps || 0)} fps · ${Number(data.age_seconds || 0).toFixed(1)}s ago`
                    : "Open the camera page on the phone, then press Start camera.";
            }
            if (dot) dot.setAttribute("aria-label", active ? "Live" : "Offline");
            if (placeholder) placeholder.hidden = active;
            if (qualitySelect && data.quality_preset) qualitySelect.value = data.quality_preset;
            if (fpsSelect && data.fps_preset) fpsSelect.value = data.fps_preset;
            const cameraEnabled = typeof data.camera_enabled === "boolean" ? data.camera_enabled : active;
            if (remoteStart) remoteStart.disabled = cameraEnabled;
            if (remoteStop) remoteStop.disabled = !cameraEnabled;
            serverAudioEnabled = Boolean(data.audio_enabled);
            if (audioToggleLabel) audioToggleLabel.textContent = serverAudioEnabled ? "Mute sound" : "Stream sound";
            if (data.audio?.active) setAudioStatus("Sound is live.");
            else if (!serverAudioEnabled) setAudioStatus("Sound is off.");
            else setAudioStatus("Waiting for phone microphone…");
        }

        async function sendRemoteControl(payload) {
            if (!controlUrl || controlRequestInFlight) return;
            controlRequestInFlight = true;
            if (remoteStart) remoteStart.disabled = true;
            if (remoteStop) remoteStop.disabled = true;
            if (remoteOpen) remoteOpen.disabled = true;
            if (remoteSwitch) remoteSwitch.disabled = true;
            if (qualitySelect) qualitySelect.disabled = true;
            if (fpsSelect) fpsSelect.disabled = true;
            try {
                if (payload.action === "set_audio") {
                    if (payload.enabled) await startAudioPlayback();
                    else stopAudioPlayback();
                }
                const response = await fetch(controlUrl, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "Content-Type": "application/json", Accept: "application/json" },
                    body: JSON.stringify(payload),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || "Remote camera control failed.");
                if (typeof data.audio_enabled === "boolean") serverAudioEnabled = data.audio_enabled;
                if (payload.action === "stop_camera") stopAudioPlayback();
                if (audioToggleLabel) audioToggleLabel.textContent = serverAudioEnabled ? "Mute sound" : "Stream sound";
                if (controlStatus) {
                    controlStatus.textContent = payload.action === "open_camera"
                        ? "MacroDroid open-camera trigger sent."
                        : payload.action === "start_camera"
                        ? "Camera start requested."
                        : payload.action === "stop_camera"
                            ? "Camera stop requested."
                            : payload.action === "switch_camera"
                        ? "Camera change requested."
                        : payload.action === "set_quality"
                            ? `Quality: ${data.quality?.label || payload.preset}.`
                            : payload.action === "set_fps"
                                ? `FPS: ${data.fps?.label || payload.preset}.`
                                : `Sound: ${data.audio_enabled ? "on" : "off"}.`;
                }
            } catch (error) {
                if (payload.action === "set_audio" && payload.enabled) stopAudioPlayback();
                if (controlStatus) controlStatus.textContent = error.message;
            } finally {
                controlRequestInFlight = false;
                if (remoteStart) remoteStart.disabled = false;
                if (remoteStop) remoteStop.disabled = false;
                if (remoteOpen) remoteOpen.disabled = false;
                if (remoteSwitch) remoteSwitch.disabled = false;
                if (qualitySelect) qualitySelect.disabled = false;
                if (fpsSelect) fpsSelect.disabled = false;
            }
        }

        async function refresh() {
            try {
                const response = await fetch(statusUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
                if (!response.ok) throw new Error("Status unavailable");
                updateViewer(await response.json());
            } catch (_error) {
                updateViewer({ active: false, age_seconds: null });
            }
        }

        stream?.addEventListener("error", () => {
            window.setTimeout(() => {
                if (stream) stream.src = `${stream.src.split("?")[0]}?t=${Date.now()}`;
            }, 1000);
        });
        remoteOpen?.addEventListener("click", () => sendRemoteControl({ action: "open_camera" }));
        remoteStart?.addEventListener("click", () => sendRemoteControl({ action: "start_camera" }));
        remoteStop?.addEventListener("click", () => sendRemoteControl({ action: "stop_camera" }));
        remoteSwitch?.addEventListener("click", () => sendRemoteControl({ action: "switch_camera" }));
        qualitySelect?.addEventListener("change", () => sendRemoteControl({ action: "set_quality", preset: qualitySelect.value }));
        fpsSelect?.addEventListener("change", () => sendRemoteControl({ action: "set_fps", preset: fpsSelect.value }));
        audioToggle?.addEventListener("click", () => sendRemoteControl({ action: "set_audio", enabled: !serverAudioEnabled }));
        ttsForm?.addEventListener("submit", sendTts);
        refresh();
        window.setInterval(refresh, 1500);
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-camera-capture]").forEach(initCapture);
        document.querySelectorAll("[data-camera-viewer]").forEach(initViewer);
    });
})();
