from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from flask import current_app
from lib.json_store import read_json, write_json


def notifications_file() -> str:
    return current_app.config.get("NOTIFICATIONS_FILE", "notifications.json")


def _load() -> list[dict]:
    data = read_json(notifications_file(), [])
    return data if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    write_json(notifications_file(), items)


def notify(username: str, title: str, message: str, level: str = "info") -> None:
    items = _load()
    items.insert(0, {"id": uuid4().hex, "user": username, "title": title, "message": message, "level": level, "read": False, "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"})
    _save(items[:1000])


def list_notifications(username: str) -> list[dict]:
    return [item for item in _load() if item.get("user") in {username, "*"}]


def unread_count(username: str) -> int:
    return sum(1 for item in list_notifications(username) if not item.get("read"))


def mark_read(username: str) -> None:
    items = _load()
    for item in items:
        if item.get("user") in {username, "*"}:
            item["read"] = True
    _save(items)
