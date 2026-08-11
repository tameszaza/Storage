from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition
import time


QUALITY_PRESETS = {
    "low": {
        "label": "Low",
        "max_width": 640,
        "jpeg_quality": 0.45,
    },
    "balanced": {
        "label": "Balanced",
        "max_width": 960,
        "jpeg_quality": 0.58,
    },
    "high": {
        "label": "High",
        "max_width": 1280,
        "jpeg_quality": 0.72,
    },
}

FPS_PRESETS = {
    "5": {"label": "5 fps", "target_fps": 5, "interval_ms": 200},
    "10": {"label": "10 fps", "target_fps": 10, "interval_ms": 100},
    "15": {"label": "15 fps", "target_fps": 15, "interval_ms": 67},
    "20": {"label": "20 fps", "target_fps": 20, "interval_ms": 50},
}


@dataclass(frozen=True)
class CameraFrame:
    data: bytes
    captured_at: float
    sequence: int


class CameraStream:
    """Keep the latest browser-uploaded frame and remote camera settings."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._frame: CameraFrame | None = None
        self._sequence = 0
        self._published_at: deque[float] = deque(maxlen=64)
        self._control_sequence = 0
        self._commands: deque[tuple[int, str]] = deque(maxlen=32)
        self._camera_enabled = False
        self._audio_enabled = False
        self._quality_preset = "balanced"
        self._fps_preset = "10"

    def publish(self, data: bytes) -> CameraFrame:
        now = time.time()
        with self._condition:
            self._sequence += 1
            self._frame = CameraFrame(data=data, captured_at=now, sequence=self._sequence)
            self._camera_enabled = True
            self._published_at.append(now)
            self._trim_published_times(now)
            self._condition.notify_all()
            return self._frame

    def _trim_published_times(self, now: float) -> None:
        while self._published_at and now - self._published_at[0] >= 1:
            self._published_at.popleft()

    def wait_for_frame(self, after_sequence: int = 0, timeout_seconds: float = 15) -> CameraFrame | None:
        deadline = time.monotonic() + max(0, timeout_seconds)
        with self._condition:
            while self._frame is None or self._frame.sequence <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._frame

    def status(self, active_for_seconds: float = 3) -> dict[str, float | int | bool | None | str]:
        with self._condition:
            frame = self._frame
            now = time.time()
            age_seconds = max(0.0, now - frame.captured_at) if frame else None
            self._trim_published_times(now)
            return {
                "active": bool(frame and age_seconds is not None and age_seconds <= active_for_seconds),
                "age_seconds": round(age_seconds, 2) if age_seconds is not None else None,
                "fps": round(float(len(self._published_at)), 1),
                "camera_enabled": self._camera_enabled,
                "audio_enabled": self._audio_enabled,
                "quality_preset": self._quality_preset,
                "fps_preset": self._fps_preset,
                "target_fps": FPS_PRESETS[self._fps_preset]["target_fps"],
                "sequence": frame.sequence if frame else 0,
            }

    def _queue_command(self, action: str) -> dict[str, object]:
        self._control_sequence += 1
        self._commands.append((self._control_sequence, action))
        self._condition.notify_all()
        return self._control_locked(after_sequence=self._control_sequence - 1)

    def request_switch(self) -> dict[str, object]:
        with self._condition:
            return self._queue_command("switch_camera")

    def request_start(self) -> dict[str, object]:
        with self._condition:
            self._camera_enabled = True
            return self._queue_command("start_camera")

    def request_stop(self) -> dict[str, object]:
        with self._condition:
            self._camera_enabled = False
            self._audio_enabled = False
            return self._queue_command("stop_camera")

    def set_quality(self, preset: str) -> dict[str, object]:
        if preset not in QUALITY_PRESETS:
            raise ValueError(f"Unknown camera quality preset: {preset}")
        with self._condition:
            self._quality_preset = preset
            return self._queue_command("set_quality")

    def set_fps(self, preset: str) -> dict[str, object]:
        if preset not in FPS_PRESETS:
            raise ValueError(f"Unknown camera FPS preset: {preset}")
        with self._condition:
            self._fps_preset = preset
            return self._queue_command("set_fps")

    def set_audio(self, enabled: bool) -> dict[str, object]:
        with self._condition:
            self._audio_enabled = enabled
            return self._queue_command("set_audio")

    def _control_locked(self, after_sequence: int = 0, bootstrap: bool = False) -> dict[str, object]:
        pending = []
        if not bootstrap:
            pending = [
                {"sequence": sequence, "action": action}
                for sequence, action in self._commands
                if sequence > after_sequence
            ]
        quality = dict(QUALITY_PRESETS[self._quality_preset])
        fps = dict(FPS_PRESETS[self._fps_preset])
        return {
            "sequence": pending[-1]["sequence"] if pending else self._control_sequence,
            "latest_sequence": self._control_sequence,
            "action": pending[0]["action"] if pending else None,
            "commands": pending,
            "camera_enabled": self._camera_enabled,
            "audio_enabled": self._audio_enabled,
            "quality_preset": self._quality_preset,
            "quality": quality,
            "fps_preset": self._fps_preset,
            "fps": fps,
            "presets": {name: dict(config) for name, config in QUALITY_PRESETS.items()},
            "fps_presets": {name: dict(config) for name, config in FPS_PRESETS.items()},
        }

    def control(self, after_sequence: int = 0, bootstrap: bool = False) -> dict[str, object]:
        with self._condition:
            return self._control_locked(after_sequence=after_sequence, bootstrap=bootstrap)


camera_stream = CameraStream()
