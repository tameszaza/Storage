from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any


class VoiceBridgeError(RuntimeError):
    pass


def command_parts(command: str) -> list[str]:
    try:
        parts = shlex.split(str(command or "").strip())
    except ValueError as error:
        raise VoiceBridgeError(f"Invalid audio command: {error}") from error
    if not parts:
        raise VoiceBridgeError("Audio command is not configured.")
    return parts


def command_status(command: str) -> dict[str, Any]:
    try:
        parts = command_parts(command)
    except VoiceBridgeError as error:
        return {"available": False, "command": str(command or ""), "error": str(error)}
    executable = shutil.which(parts[0])
    return {
        "available": bool(executable),
        "command": str(command or ""),
        "executable": executable,
        "error": None if executable else f"{parts[0]} was not found.",
    }


@dataclass(frozen=True)
class VoiceBridgeConfig:
    playback_command: str
    capture_command: str


class VoiceBridgeSession:
    """Duplex browser/server audio bridge over one WebSocket.

    Browser audio arrives as WebM/Opus MediaRecorder chunks and is piped to the
    configured playback process. Server microphone audio is expected as WebM/Opus
    from the configured capture process and is streamed back to the browser.
    """

    def __init__(self, websocket, config: VoiceBridgeConfig):
        self._websocket = websocket
        self._config = config
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._playback: subprocess.Popen | None = None
        self._capture: subprocess.Popen | None = None
        self._capture_thread: threading.Thread | None = None

    def _send_json(self, payload: dict[str, Any]) -> None:
        with self._send_lock:
            self._websocket.send(json.dumps(payload))

    def _send_binary(self, payload: bytes) -> None:
        with self._send_lock:
            self._websocket.send(payload)

    def _spawn_playback(self) -> None:
        parts = command_parts(self._config.playback_command)
        if not shutil.which(parts[0]):
            raise VoiceBridgeError(f"Playback command is unavailable: {parts[0]}")
        self._playback = subprocess.Popen(
            parts,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def _capture_reader(self) -> None:
        assert self._capture is not None and self._capture.stdout is not None
        try:
            while not self._stop_event.is_set():
                chunk = self._capture.stdout.read(16 * 1024)
                if not chunk:
                    break
                self._send_binary(chunk)
        except Exception as error:
            if not self._stop_event.is_set():
                logging.exception("Server microphone stream failed")
                try:
                    self._send_json({"type": "error", "message": f"Server microphone stream failed: {error}"})
                except Exception:
                    pass

    def _spawn_capture(self) -> None:
        parts = command_parts(self._config.capture_command)
        if not shutil.which(parts[0]):
            raise VoiceBridgeError(f"Capture command is unavailable: {parts[0]}")
        self._capture = subprocess.Popen(
            parts,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._capture_thread = threading.Thread(
            target=self._capture_reader,
            name="tamestorage-voice-capture",
            daemon=True,
        )
        self._capture_thread.start()

    def start(self, send_to_server: bool, receive_from_server: bool) -> None:
        if send_to_server:
            self._spawn_playback()
        if receive_from_server:
            self._spawn_capture()
        self._send_json(
            {
                "type": "started",
                "send_to_server": send_to_server,
                "receive_from_server": receive_from_server,
            }
        )

    def write_browser_audio(self, payload: bytes) -> None:
        process = self._playback
        if not process or not process.stdin:
            return
        if process.poll() is not None:
            raise VoiceBridgeError("Server playback process stopped unexpectedly.")
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise VoiceBridgeError("Could not play browser audio on the server.") from error

    @staticmethod
    def _terminate(process: subprocess.Popen | None) -> None:
        if not process:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def close(self) -> None:
        self._stop_event.set()
        self._terminate(self._playback)
        self._terminate(self._capture)
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=1)
