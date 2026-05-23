from __future__ import annotations

import os
from datetime import datetime
from uuid import uuid4
from flask import current_app, session
from lib.json_store import read_json, write_json
from lib.storage import normalize_relative_path, safe_upload_path


def requests_file() -> str:
    return current_app.config.get("FILE_REQUESTS_FILE", "file_requests.json")


def _load() -> list[dict]:
    data = read_json(requests_file(), [])
    return data if isinstance(data, list) else []


def _save(data: list[dict]) -> None:
    write_json(requests_file(), data)


def create_request(title: str, destination: str, password: str = "", expires_at: str = "", allowed_ext: str = "", max_size_mb: str = "") -> dict:
    item = {
        "id": uuid4().hex[:16],
        "title": title.strip() or "File request",
        "destination": normalize_relative_path(destination),
        "password": password,
        "expires_at": expires_at,
        "allowed_ext": [x.strip().lower().lstrip(".") for x in allowed_ext.split(",") if x.strip()],
        "max_size_mb": int(max_size_mb) if str(max_size_mb).isdigit() else None,
        "owner": session.get("username") or "Admin",
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "active": True,
        "uploads": 0,
    }
    data = _load()
    data.insert(0, item)
    _save(data)
    os.makedirs(safe_upload_path(item["destination"]), exist_ok=True)
    return item


def list_requests(username: str | None) -> list[dict]:
    data = _load()
    if username == "Admin":
        return data
    return [item for item in data if item.get("owner") == username]


def get_request(request_id: str) -> dict | None:
    for item in _load():
        if item.get("id") == request_id:
            return item
    return None


def update_request(request_id: str, **changes) -> None:
    data = _load()
    for item in data:
        if item.get("id") == request_id:
            item.update(changes)
            break
    _save(data)


def is_expired(item: dict) -> bool:
    value = item.get("expires_at")
    if not value:
        return False
    try:
        return datetime.fromisoformat(value) < datetime.now()
    except ValueError:
        return False
