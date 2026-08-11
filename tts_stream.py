from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition
import time


@dataclass(frozen=True)
class TtsChunk:
    data: bytes
    created_at: float
    sequence: int
    request_id: str | None = None


class TtsStream:
    """Queue a small number of cloud-generated voice clips for the phone browser."""

    def __init__(self, max_chunks: int = 8, max_age_seconds: int = 15 * 60) -> None:
        self._condition = Condition()
        self._chunks: deque[TtsChunk] = deque(maxlen=max_chunks)
        self._max_age_seconds = max(1, max_age_seconds)
        self._sequence = 0

    def _prune_locked(self, now: float | None = None) -> None:
        cutoff = (time.time() if now is None else now) - self._max_age_seconds
        while self._chunks and self._chunks[0].created_at < cutoff:
            self._chunks.popleft()

    def publish(self, data: bytes, request_id: str | None = None) -> TtsChunk:
        now = time.time()
        with self._condition:
            self._prune_locked(now)
            if request_id:
                for chunk in self._chunks:
                    if chunk.request_id == request_id:
                        return chunk
            self._sequence += 1
            chunk = TtsChunk(data=data, created_at=now, sequence=self._sequence, request_id=request_id)
            self._chunks.append(chunk)
            self._condition.notify_all()
            return chunk

    def find_by_request_id(self, request_id: str) -> TtsChunk | None:
        with self._condition:
            self._prune_locked()
            return next((chunk for chunk in self._chunks if chunk.request_id == request_id), None)

    def latest_sequence(self) -> int:
        with self._condition:
            self._prune_locked()
            return self._sequence

    def wait_for_chunk(self, after_sequence: int = 0, timeout_seconds: float = 1) -> TtsChunk | None:
        deadline = time.monotonic() + max(0, timeout_seconds)
        with self._condition:
            while True:
                self._prune_locked()
                for chunk in self._chunks:
                    if chunk.sequence > after_sequence:
                        return chunk
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)


tts_stream = TtsStream()
