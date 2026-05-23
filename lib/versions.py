from __future__ import annotations

import os
import shutil
from datetime import datetime
from uuid import uuid4
from flask import current_app, session
from lib.json_store import read_json, write_json
from lib.storage import format_bytes, normalize_relative_path, safe_upload_path, upload_root


def versions_file() -> str:
    return current_app.config.get("VERSIONS_FILE", "versions.json")


def versions_root() -> str:
    root = os.path.join(upload_root(), ".tamestorage_system", "versions")
    os.makedirs(root, exist_ok=True)
    return root


def _load() -> dict:
    data = read_json(versions_file(), {})
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    write_json(versions_file(), data)


def create_version(relative_path: str, reason: str = "modified") -> dict | None:
    relative_path = normalize_relative_path(relative_path)
    source = safe_upload_path(relative_path)
    if not relative_path or not os.path.isfile(source):
        return None
    version_id = uuid4().hex
    _, ext = os.path.splitext(os.path.basename(relative_path))
    storage_name = f"{version_id}{ext}"
    storage_path = os.path.join(versions_root(), storage_name)
    shutil.copy2(source, storage_path)
    entry = {
        "id": version_id,
        "path": relative_path,
        "storage_name": storage_name,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "created_by": session.get("username") or "system",
        "reason": reason,
        "size": os.path.getsize(storage_path),
        "size_label": format_bytes(os.path.getsize(storage_path)),
    }
    data = _load()
    data.setdefault(relative_path, []).insert(0, entry)
    data[relative_path] = data[relative_path][:50]
    _save(data)
    return entry


def list_versions(relative_path: str) -> list[dict]:
    return _load().get(normalize_relative_path(relative_path), [])


def get_version(relative_path: str, version_id: str) -> dict | None:
    for entry in list_versions(relative_path):
        if entry.get("id") == version_id:
            return entry
    return None


def version_file_path(entry: dict) -> str:
    return os.path.join(versions_root(), entry["storage_name"])


def restore_version(relative_path: str, version_id: str) -> bool:
    relative_path = normalize_relative_path(relative_path)
    entry = get_version(relative_path, version_id)
    if not entry:
        return False
    source = version_file_path(entry)
    target = safe_upload_path(relative_path)
    if not os.path.exists(source):
        return False
    create_version(relative_path, "before version restore")
    shutil.copy2(source, target)
    return True


def move_versions(old_path: str, new_path: str) -> None:
    data = _load()
    old_path = normalize_relative_path(old_path)
    new_path = normalize_relative_path(new_path)
    if old_path in data:
        data[new_path] = data.pop(old_path)
        for entry in data[new_path]:
            entry["path"] = new_path
        _save(data)
