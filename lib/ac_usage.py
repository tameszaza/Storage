from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_USAGE: dict[str, Any] = {
    "version": 1,
    "reset_at": None,
    "updated_at": None,
    "total_on_seconds": 0.0,
    "total_off_seconds": 0.0,
    "total_cycles": 0,
    "schedule_starts": 0,
    "manual_on_commands": 0,
    "manual_off_commands": 0,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, parsed)


def _integer(value: Any, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, parsed)


class AcUsageStore:
    """Thread-safe durable AC usage counters.

    Writes are atomic and owner-only. The controller checkpoints active phases so
    totals survive schedule restarts and normal server restarts.
    """

    def __init__(self, path: str):
        self._path = Path(path).expanduser()
        self._lock = threading.RLock()
        self._data = self._load()
        if not self._path.exists():
            try:
                with self._lock:
                    self._write_locked()
            except OSError:
                # Usage will be retried on the first successful update.
                pass

    def _load(self) -> dict[str, Any]:
        data = deepcopy(DEFAULT_USAGE)
        if self._path.is_file():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                data.update(loaded)

        now = utc_now_iso()
        data["version"] = 1
        data["reset_at"] = str(data.get("reset_at") or now)
        data["updated_at"] = str(data.get("updated_at") or now)
        data["total_on_seconds"] = _number(data.get("total_on_seconds"))
        data["total_off_seconds"] = _number(data.get("total_off_seconds"))
        data["total_cycles"] = _integer(data.get("total_cycles"))
        data["schedule_starts"] = _integer(data.get("schedule_starts"))
        data["manual_on_commands"] = _integer(data.get("manual_on_commands"))
        data["manual_off_commands"] = _integer(data.get("manual_off_commands"))
        return data

    def _write_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def add_time(self, state: str, seconds: float) -> None:
        seconds = _number(seconds)
        if seconds <= 0:
            return
        key = "total_on_seconds" if state == "on" else "total_off_seconds"
        with self._lock:
            self._data[key] = _number(self._data.get(key)) + seconds
            self._data["updated_at"] = utc_now_iso()
            self._write_locked()

    def add_cycle(self, count: int = 1) -> None:
        count = _integer(count)
        if count <= 0:
            return
        with self._lock:
            self._data["total_cycles"] = _integer(self._data.get("total_cycles")) + count
            self._data["updated_at"] = utc_now_iso()
            self._write_locked()

    def add_schedule_start(self) -> None:
        with self._lock:
            self._data["schedule_starts"] = _integer(self._data.get("schedule_starts")) + 1
            self._data["updated_at"] = utc_now_iso()
            self._write_locked()

    def add_manual_command(self, action: str) -> None:
        key = "manual_on_commands" if action == "on" else "manual_off_commands"
        with self._lock:
            self._data[key] = _integer(self._data.get(key)) + 1
            self._data["updated_at"] = utc_now_iso()
            self._write_locked()

    def reset(self) -> dict[str, Any]:
        now = utc_now_iso()
        with self._lock:
            self._data = {
                **deepcopy(DEFAULT_USAGE),
                "reset_at": now,
                "updated_at": now,
            }
            self._write_locked()
            return deepcopy(self._data)
