from __future__ import annotations

from dataclasses import dataclass
from threading import Condition
import struct
import time


@dataclass(frozen=True)
class AudioChunk:
    data: bytes
    captured_at: float
    sequence: int
    sample_rate: int
    channels: int


class AudioStream:
    """Keep the newest raw PCM microphone chunk for browser playback."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._chunk: AudioChunk | None = None
        self._sequence = 0

    def publish(self, data: bytes, sample_rate: int, channels: int) -> AudioChunk:
        now = time.time()
        with self._condition:
            self._sequence += 1
            self._chunk = AudioChunk(
                data=data,
                captured_at=now,
                sequence=self._sequence,
                sample_rate=sample_rate,
                channels=channels,
            )
            self._condition.notify_all()
            return self._chunk

    def wait_for_chunk(self, after_sequence: int = 0, timeout_seconds: float = 1) -> AudioChunk | None:
        deadline = time.monotonic() + max(0, timeout_seconds)
        with self._condition:
            while self._chunk is None or self._chunk.sequence <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._chunk

    def status(self, active_for_seconds: float = 2) -> dict[str, float | int | bool | None]:
        with self._condition:
            chunk = self._chunk
            age_seconds = max(0.0, time.time() - chunk.captured_at) if chunk else None
            return {
                "active": bool(chunk and age_seconds is not None and age_seconds <= active_for_seconds),
                "age_seconds": round(age_seconds, 2) if age_seconds is not None else None,
                "sequence": chunk.sequence if chunk else 0,
                "sample_rate": chunk.sample_rate if chunk else None,
                "channels": chunk.channels if chunk else None,
            }

    @staticmethod
    def wav_bytes(chunk: AudioChunk) -> bytes:
        data_size = len(chunk.data)
        byte_rate = chunk.sample_rate * chunk.channels * 2
        block_align = chunk.channels * 2
        header = b"RIFF" + struct.pack("<I", 36 + data_size)
        header += b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, chunk.channels, chunk.sample_rate, byte_rate, block_align, 16)
        return header + b"data" + struct.pack("<I", data_size) + chunk.data


audio_stream = AudioStream()
