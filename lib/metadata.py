from __future__ import annotations

from datetime import datetime
from flask import current_app, session
from lib.json_store import read_json, write_json
from lib.storage import normalize_relative_path


def metadata_file() -> str:
    return current_app.config.get("METADATA_FILE", "file_metadata.json")


def _load() -> dict:
    data = read_json(metadata_file(), {})
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    write_json(metadata_file(), data)


def get_metadata(path: str) -> dict:
    data = _load()
    return data.get(normalize_relative_path(path), {})


def set_metadata(path: str, tags: list[str] | None = None, note: str | None = None, starred: bool | None = None) -> dict:
    path = normalize_relative_path(path)
    data = _load()
    item = data.get(path, {})
    if tags is not None:
        item["tags"] = sorted({tag.strip().lower() for tag in tags if tag and tag.strip()})
    if note is not None:
        item["note"] = note.strip()
    if starred is not None:
        item["starred"] = bool(starred)
    item["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    item["updated_by"] = session.get("username") or "system"
    data[path] = item
    _save(data)
    return item


def move_metadata(old_path: str, new_path: str) -> None:
    data = _load()
    old_path = normalize_relative_path(old_path)
    new_path = normalize_relative_path(new_path)
    updated = {}
    changed = False
    for key, value in data.items():
        if key == old_path or key.startswith(old_path + "/"):
            updated[new_path + key[len(old_path):]] = value
            changed = True
        else:
            updated[key] = value
    if changed:
        _save(updated)


def delete_metadata(path: str) -> None:
    data = _load()
    path = normalize_relative_path(path)
    changed = False
    for key in list(data.keys()):
        if key == path or key.startswith(path + "/"):
            del data[key]
            changed = True
    if changed:
        _save(data)


def all_metadata() -> dict:
    return _load()
