from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from flask import current_app, request, session
from lib.json_store import read_json, write_json


def activity_file() -> str:
    return current_app.config.get("ACTIVITY_FILE", "activity_log.json")


def log_activity(action: str, target: str = "", status: str = "success", details: dict | None = None, username: str | None = None) -> None:
    try:
        entries = read_json(activity_file(), [])
        if not isinstance(entries, list):
            entries = []
        entries.insert(0, {
            "id": uuid4().hex,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "user": username or session.get("username") or "public",
            "action": action,
            "target": target,
            "status": status,
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            "user_agent": request.headers.get("User-Agent", "")[:220],
            "details": details or {},
        })
        write_json(activity_file(), entries[:2000])
    except Exception:
        # Activity logging must never break file operations.
        pass


def list_activity(limit: int = 250, offset: int = 0, *, include_calendar_syncs: bool = True) -> list[dict]:
    entries = read_json(activity_file(), [])
    if not isinstance(entries, list):
        return []
    if not include_calendar_syncs:
        entries = [entry for entry in entries if not (
            entry.get("action") == "planner.calendar.sync" and entry.get("status") == "success"
        )]
    start = max(0, int(offset))
    return entries[start:start + max(0, int(limit))]
