from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from uuid import uuid4
from flask import current_app, session
from lib.json_store import read_json, write_json
from lib.storage import format_bytes, normalize_relative_path, safe_upload_path, upload_root


def trash_file() -> str:
    return current_app.config.get("TRASH_FILE", "trash_index.json")


def trash_root() -> str:
    root = os.path.join(upload_root(), ".tamestorage_system", "trash")
    os.makedirs(root, exist_ok=True)
    return root


def _load() -> list[dict]:
    data = read_json(trash_file(), [])
    return data if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    write_json(trash_file(), items)


def move_to_trash(relative_path: str) -> dict | None:
    relative_path = normalize_relative_path(relative_path)
    if not relative_path:
        return None
    source = safe_upload_path(relative_path)
    if not os.path.exists(source):
        return None
    item_id = uuid4().hex
    trash_dir = os.path.join(trash_root(), item_id)
    os.makedirs(trash_dir, exist_ok=True)
    dest = os.path.join(trash_dir, os.path.basename(relative_path))
    shutil.move(source, dest)
    size = os.path.getsize(dest) if os.path.isfile(dest) else _folder_size(dest)
    record = {
        "id": item_id,
        "name": os.path.basename(relative_path),
        "original_path": relative_path,
        "trash_path": dest,
        "deleted_by": session.get("username") or "system",
        "deleted_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "is_dir": os.path.isdir(dest),
        "size": size,
        "size_label": format_bytes(size),
    }
    items = _load()
    items.insert(0, record)
    _save(items)
    return record


def _folder_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for filename in files:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                pass
    return total


def list_trash(username: str | None) -> list[dict]:
    items = _load()
    if username == "Admin":
        return items
    if not username:
        return []
    prefix = username + "/"
    return [item for item in items if item.get("original_path") == username or item.get("original_path", "").startswith(prefix)]


def restore(item_id: str) -> bool:
    items = _load()
    for item in items:
        if item.get("id") != item_id:
            continue
        source = item.get("trash_path")
        original = safe_upload_path(item.get("original_path", ""))
        if not source or not os.path.exists(source):
            return False
        os.makedirs(os.path.dirname(original), exist_ok=True)
        if os.path.exists(original):
            base, ext = os.path.splitext(original)
            counter = 1
            candidate = f"{base} restored{ext}"
            while os.path.exists(candidate):
                counter += 1
                candidate = f"{base} restored {counter}{ext}"
            original = candidate
        shutil.move(source, original)
        shutil.rmtree(os.path.dirname(source), ignore_errors=True)
        items.remove(item)
        _save(items)
        return True
    return False


def delete_forever(item_id: str) -> bool:
    items = _load()
    for item in list(items):
        if item.get("id") != item_id:
            continue
        path = item.get("trash_path")
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif path and os.path.exists(path):
            os.remove(path)
        if path:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        items.remove(item)
        _save(items)
        return True
    return False


def empty_trash(username: str | None) -> int:
    count = 0
    for item in list(list_trash(username)):
        if delete_forever(item["id"]):
            count += 1
    return count


def purge_old(days: int = 30) -> int:
    cutoff = datetime.utcnow() - timedelta(days=days)
    count = 0
    for item in list(_load()):
        try:
            deleted_at = datetime.fromisoformat(item.get("deleted_at", "").replace("Z", ""))
        except ValueError:
            continue
        if deleted_at < cutoff and delete_forever(item["id"]):
            count += 1
    return count
