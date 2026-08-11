from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AcHistoryStore:
    """Small append-only JSON history for user-visible aircon changes."""

    def __init__(self, path: str, max_entries: int = 500):
        self._path = Path(path).expanduser()
        self._max_entries = max(50, int(max_entries))
        self._lock = threading.RLock()

    def _load_locked(self) -> list[dict[str, Any]]:
        if not self._path.is_file():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def _save_locked(self, entries: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(entries[-self._max_entries :], indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)

    def add(self, event: str, *, source: str = "portal", summary: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": str(event),
            "source": str(source),
            "summary": str(summary),
            "details": deepcopy(details or {}),
        }
        with self._lock:
            entries = self._load_locked()
            entries.append(entry)
            self._save_locked(entries)
        return deepcopy(entry)

    def recent(self, limit: int = 80) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._lock:
            entries = self._load_locked()
            return deepcopy(list(reversed(entries[-limit:])))
