from __future__ import annotations

import threading
import time
import unittest
from collections.abc import Callable

from lib.phone_commands import (
    PHONE_COMMAND_INTERVAL_SECONDS,
    PhoneCommandCancelled,
    PhoneCommandQueue,
)


class PhoneCommandQueueTests(unittest.TestCase):
    def test_default_buffer_is_twelve_seconds(self):
        self.assertEqual(PHONE_COMMAND_INTERVAL_SECONDS, 12.0)

    def test_commands_are_serialized_and_spaced(self):
        interval = 0.04
        queue = PhoneCommandQueue(interval_seconds=interval)
        dispatched_at: list[float] = []
        active = 0
        maximum_active = 0
        state_lock = threading.Lock()

        def command() -> None:
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                dispatched_at.append(time.monotonic())
            time.sleep(0.01)
            with state_lock:
                active -= 1

        threads = [threading.Thread(target=lambda: queue.execute(command)) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(maximum_active, 1)
        self.assertEqual(len(dispatched_at), 3)
        for previous, current in zip(dispatched_at, dispatched_at[1:]):
            self.assertGreaterEqual(current - previous, interval - 0.005)

    def test_failed_command_does_not_block_the_queue(self):
        queue = PhoneCommandQueue(interval_seconds=0)

        with self.assertRaisesRegex(RuntimeError, "failed"):
            queue.execute(lambda: (_ for _ in ()).throw(RuntimeError("failed")))

        self.assertEqual(queue.execute(lambda: "sent"), "sent")

    def test_pending_command_can_be_canceled_without_stalling_queue(self):
        queue = PhoneCommandQueue(interval_seconds=0)
        first_started = threading.Event()
        release_first = threading.Event()
        second_error: list[BaseException] = []

        def first() -> str:
            first_started.set()
            release_first.wait(timeout=1)
            return "first"

        def second() -> str:
            return "second"

        first_thread = threading.Thread(target=lambda: queue.execute(first, "ac"))
        second_thread = threading.Thread(
            target=lambda: self._capture_error(
                second_error, lambda: queue.execute(second, "ac")
            )
        )
        first_thread.start()
        self.assertTrue(first_started.wait(timeout=1))
        second_thread.start()
        time.sleep(0.01)
        self.assertEqual(queue.cancel_pending("ac"), 1)
        release_first.set()
        first_thread.join(timeout=1)
        second_thread.join(timeout=1)

        self.assertEqual(len(second_error), 1)
        self.assertIsInstance(second_error[0], PhoneCommandCancelled)
        self.assertEqual(queue.execute(lambda: "after", "ac"), "after")

    @staticmethod
    def _capture_error(target: list[BaseException], callback: Callable[[], object]) -> None:
        try:
            callback()
        except BaseException as error:
            target.append(error)


if __name__ == "__main__":
    unittest.main()
