"""Non-blocking, cross-worker cache for the shell's approximate storage total."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from lib.storage import get_folder_size


MAX_AGE_SECONDS = 300


def _read(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".usage-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def approximate_usage(path: str, cache_directory: str) -> int | None:
    """Return the latest known total immediately, refreshing stale values in a thread."""
    if not os.path.isdir(path):
        return 0
    root = Path(cache_directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = hashlib.sha256(os.path.realpath(path).encode("utf-8")).hexdigest()[:24]
    state_path, lock_path = root / f"{key}.json", root / f"{key}.lock"
    state = _read(state_path)
    old_value = state.get("bytes") if isinstance(state.get("bytes"), int) else None
    if time.time() - float(state.get("checked_at") or 0) < MAX_AGE_SECONDS:
        return old_value

    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return old_value

    # A concurrent request may have completed the refresh before this lock.
    state = _read(state_path)
    old_value = state.get("bytes") if isinstance(state.get("bytes"), int) else None
    if time.time() - float(state.get("checked_at") or 0) < MAX_AGE_SECONDS:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        return old_value

    def refresh() -> None:
        try:
            value = get_folder_size(path)
            _write(state_path, {"bytes": value, "checked_at": time.time()})
        except OSError:
            # Keep an earlier value, and avoid retrying on every page request.
            _write(state_path, {"bytes": old_value, "checked_at": time.time() - MAX_AGE_SECONDS + 60})
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    threading.Thread(target=refresh, name="shell-storage-usage", daemon=True).start()
    return old_value
