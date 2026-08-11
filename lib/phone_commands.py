from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

PHONE_COMMAND_INTERVAL_SECONDS = 12.0


class PhoneCommandCancelled(RuntimeError):
    """Raised when a queued phone command is canceled before dispatch."""


class _Command:
    def __init__(self, callback: Callable[[], Any], command_type: str):
        self.callback = callback
        self.command_type = command_type
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.cancelled = False
        self.dispatched = False
        self.result: Any = None
        self.error: BaseException | None = None

    def cancel(self) -> bool:
        with self.lock:
            if self.dispatched or self.done.is_set():
                return False
            self.cancelled = True
            self.error = PhoneCommandCancelled(
                f"Queued {self.command_type or 'phone'} command was canceled."
            )
            self.done.set()
            return True

    def run(self) -> None:
        with self.lock:
            if self.cancelled:
                return
            self.dispatched = True
        try:
            self.result = self.callback()
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def wait(self) -> T:
        self.done.wait()
        if self.error is not None:
            raise self.error
        return self.result


class PhoneCommandQueue:
    """Run phone webhook commands FIFO with cancellation and a dispatch interval."""

    def __init__(self, interval_seconds: float = PHONE_COMMAND_INTERVAL_SECONDS):
        self._interval_seconds = max(0.0, float(interval_seconds))
        self._condition = threading.Condition(threading.Lock())
        self._pending: deque[_Command] = deque()
        self._current: _Command | None = None
        self._last_dispatched_at: float | None = None
        self._worker = threading.Thread(
            target=self._run,
            name="tamestorage-phone-command-queue",
            daemon=True,
        )
        self._worker.start()

    def submit(self, callback: Callable[[], T], command_type: str = "") -> _Command:
        command = _Command(callback, command_type)
        with self._condition:
            self._pending.append(command)
            self._condition.notify_all()
        return command

    def execute(self, callback: Callable[[], T], command_type: str = "") -> T:
        return self.submit(callback, command_type).wait()

    def cancel_pending(self, command_type: str | None = None) -> int:
        """Cancel queued commands, including one waiting for the interval."""
        canceled = 0
        with self._condition:
            candidates = list(self._pending)
            if self._current is not None:
                candidates.append(self._current)
            for command in candidates:
                if command_type is not None and command.command_type != command_type:
                    continue
                if command.cancel():
                    canceled += 1
            self._condition.notify_all()
        return canceled

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending:
                    self._condition.wait()
                command = self._pending.popleft()
                self._current = command

                while not command.cancelled and self._last_dispatched_at is not None:
                    remaining = self._interval_seconds - (time.monotonic() - self._last_dispatched_at)
                    if remaining <= 0:
                        break
                    self._condition.wait(timeout=remaining)

                if not command.cancelled:
                    self._last_dispatched_at = time.monotonic()

            if not command.cancelled:
                command.run()
            with self._condition:
                self._current = None
                self._condition.notify_all()


phone_command_queue = PhoneCommandQueue()
