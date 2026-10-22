"""Run exact duplicate scans outside web requests and cache their results."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from lib.search_index import duplicate_groups
from lib.storage import format_bytes


PAGE_SIZE = 50


def _paths(username: str) -> tuple[Path, Path]:
    root = Path(current_app.config["DUPLICATE_SCAN_DIR"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = hashlib.sha256(username.encode("utf-8")).hexdigest()[:24]
    return root / f"{key}.json", root / f"{key}.lock"


def _load(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".duplicates-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def start_scan(username: str, *, force: bool = False) -> dict:
    """Return immediately; one worker per account scans while requests stay responsive."""
    state_path, lock_path = _paths(username)
    state = _load(state_path)
    if state.get("status") == "ready" and not force:
        return state
    if state.get("status") == "error" and not force:
        return state

    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return state or {"status": "running"}

    # Re-read after taking the cross-process lock; another worker may have finished.
    state = _load(state_path)
    if state.get("status") == "ready" and not force:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        return state

    started_at = datetime.now(timezone.utc).isoformat()
    _save(state_path, {"status": "running", "started_at": started_at})
    application = current_app._get_current_object()

    def work() -> None:
        try:
            with application.app_context():
                base = "" if username == "Admin" else username
                groups = duplicate_groups(base)
                compact = []
                reclaimable = 0
                for group in groups:
                    files = [{"path": item["path"]} for item in group["files"]]
                    size = int(group["files"][0]["size"])
                    reclaimable += size * (len(files) - 1)
                    compact.append({"hash": group["hash"], "size": size,
                                    "size_label": format_bytes(size), "files": files})
                compact.sort(key=lambda group: (-group["size"] * (len(group["files"]) - 1), group["hash"]))
                _save(state_path, {"status": "ready", "started_at": started_at,
                                   "finished_at": datetime.now(timezone.utc).isoformat(),
                                   "groups": compact, "reclaimable_bytes": reclaimable})
        except Exception:
            application.logger.exception("Duplicate scan failed")
            _save(state_path, {"status": "error", "started_at": started_at,
                               "finished_at": datetime.now(timezone.utc).isoformat(),
                               "message": "The scan could not finish. Try again."})
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    threading.Thread(target=work, name=f"duplicates-{username}", daemon=True).start()
    return {"status": "running", "started_at": started_at}


def scan_status(username: str, *, page: int = 1) -> dict:
    state_path, _ = _paths(username)
    state = _load(state_path)
    if not state:
        return {"status": "not_started"}
    if state.get("status") != "ready":
        return {key: value for key, value in state.items() if key != "groups"}
    groups = state.get("groups") or []
    page_count = max(1, math.ceil(len(groups) / PAGE_SIZE))
    page = max(1, min(page, page_count))
    return {"status": "ready", "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"), "total_groups": len(groups),
            "reclaimable_bytes": state.get("reclaimable_bytes", 0),
            "reclaimable_label": format_bytes(state.get("reclaimable_bytes", 0)),
            "page": page, "pages": page_count,
            "groups": groups[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]}
