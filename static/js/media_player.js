(function () {
    function formatTime(value) {
        if (!Number.isFinite(value) || value < 0) return "0:00";
        const minutes = Math.floor(value / 60);
        const seconds = Math.floor(value % 60).toString().padStart(2, "0");
        return `${minutes}:${seconds}`;
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-audio-player]").forEach((player) => {
            const audio = player.querySelector("[data-audio-element]");
            const play = player.querySelector("[data-audio-play]");
            const rewind = player.querySelector("[data-audio-rewind]");
            const forward = player.querySelector("[data-audio-forward]");
            const seek = player.querySelector("[data-audio-seek]");
            const current = player.querySelector("[data-audio-current]");
            const duration = player.querySelector("[data-audio-duration]");
            const mute = player.querySelector("[data-audio-mute]");
            const volume = player.querySelector("[data-audio-volume]");
            const rate = player.querySelector("[data-audio-rate]");
            const status = player.querySelector("[data-audio-status]");
            if (!audio || !play || !seek) return;

            function announce(message) {
                if (status) status.textContent = message;
            }

            function updatePlayButton() {
                const playing = !audio.paused;
                play.querySelector("i").className = playing ? "fa-solid fa-pause" : "fa-solid fa-play";
                play.setAttribute("aria-label", playing ? "Pause audio" : "Play audio");
                player.classList.toggle("is-playing", playing);
            }

            function updateTimeline() {
                const percent = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
                seek.value = String(percent);
                seek.style.setProperty("--range-progress", `${percent}%`);
                if (current) current.textContent = formatTime(audio.currentTime);
                if (duration) duration.textContent = formatTime(audio.duration);
            }

            function updateVolumeIcon() {
                const icon = mute?.querySelector("i");
                if (!icon) return;
                icon.className = audio.muted || audio.volume === 0 ? "fa-solid fa-volume-xmark" : audio.volume < 0.5 ? "fa-solid fa-volume-low" : "fa-solid fa-volume-high";
                mute.setAttribute("aria-label", audio.muted ? "Unmute audio" : "Mute audio");
            }

            play.addEventListener("click", async () => {
                if (audio.paused) {
                    try { await audio.play(); announce("Playing audio"); } catch (error) { announce("Audio playback could not start"); }
                } else {
                    audio.pause();
                    announce("Audio paused");
                }
            });
            rewind?.addEventListener("click", () => { audio.currentTime = Math.max(0, audio.currentTime - 10); updateTimeline(); });
            forward?.addEventListener("click", () => { audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 10); updateTimeline(); });
            seek.addEventListener("input", () => { if (audio.duration) audio.currentTime = (Number(seek.value) / 100) * audio.duration; updateTimeline(); });
            volume?.addEventListener("input", () => { audio.volume = Number(volume.value); audio.muted = false; volume.style.setProperty("--range-progress", `${audio.volume * 100}%`); updateVolumeIcon(); });
            mute?.addEventListener("click", () => { audio.muted = !audio.muted; updateVolumeIcon(); announce(audio.muted ? "Audio muted" : "Audio unmuted"); });
            rate?.addEventListener("change", () => { audio.playbackRate = Number(rate.value); announce(`Playback speed ${rate.value} times`); });

            audio.addEventListener("loadedmetadata", updateTimeline);
            audio.addEventListener("timeupdate", updateTimeline);
            audio.addEventListener("play", updatePlayButton);
            audio.addEventListener("pause", updatePlayButton);
            audio.addEventListener("ended", () => { updatePlayButton(); announce("Audio finished"); });
            audio.addEventListener("volumechange", updateVolumeIcon);
            audio.addEventListener("error", () => announce("This audio file could not be played in the browser"));

            player.addEventListener("keydown", (event) => {
                if (event.code === "Space" && event.target.tagName !== "INPUT" && event.target.tagName !== "SELECT") {
                    event.preventDefault();
                    play.click();
                }
            });

            if ("mediaSession" in navigator) {
                navigator.mediaSession.setActionHandler("play", () => audio.play());
                navigator.mediaSession.setActionHandler("pause", () => audio.pause());
                navigator.mediaSession.setActionHandler("seekbackward", () => { audio.currentTime = Math.max(0, audio.currentTime - 10); });
                navigator.mediaSession.setActionHandler("seekforward", () => { audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 10); });
            }

            volume?.style.setProperty("--range-progress", "100%");
            updatePlayButton();
            updateVolumeIcon();
            updateTimeline();
        });
    });
})();
