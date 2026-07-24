from __future__ import annotations

import os
import hashlib
from datetime import datetime, timedelta
from lib.metadata import all_metadata, get_metadata
from lib.storage import TEXT_PREVIEW_EXTENSIONS, file_kind, format_bytes, get_folder_size, normalize_relative_path, safe_upload_path


def iter_files(base_relative_path: str):
    base_relative_path = normalize_relative_path(base_relative_path)
    base = safe_upload_path(base_relative_path)
    if not os.path.exists(base):
        return
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != ".tamestorage_system"]
        for dirname in dirs:
            absolute = os.path.join(root, dirname)
            rel = normalize_relative_path(os.path.join(base_relative_path, os.path.relpath(absolute, base)))
            yield {"path": rel, "name": dirname, "is_dir": True, "kind": "folder", "size": get_folder_size(absolute), "mtime": os.path.getmtime(absolute)}
        for filename in files:
            absolute = os.path.join(root, filename)
            rel = normalize_relative_path(os.path.join(base_relative_path, os.path.relpath(absolute, base)))
            yield {"path": rel, "name": filename, "is_dir": False, "kind": file_kind(filename, False), "size": os.path.getsize(absolute), "mtime": os.path.getmtime(absolute)}


def search_files(base_path: str, query: str = "", kind: str = "", tag: str = "", starred: bool = False, min_size: int | None = None, max_size: int | None = None, modified_days: int | None = None, content: str = "") -> list[dict]:
    query = (query or "").lower().strip()
    tag = (tag or "").lower().strip()
    content = (content or "").lower().strip()
    cutoff = datetime.now() - timedelta(days=modified_days) if modified_days else None
    results = []
    for item in iter_files(base_path) or []:
        meta = get_metadata(item["path"])
        tags = meta.get("tags", [])
        note = meta.get("note", "")
        if query and query not in item["name"].lower() and query not in item["path"].lower() and query not in note.lower():
            continue
        if kind and kind != "all" and item["kind"] != kind:
            continue
        if tag and tag not in tags:
            continue
        if starred and not meta.get("starred"):
            continue
        if min_size is not None and item["size"] < min_size:
            continue
        if max_size is not None and item["size"] > max_size:
            continue
        if cutoff and datetime.fromtimestamp(item["mtime"]) < cutoff:
            continue
        if content:
            if item["is_dir"]:
                continue
            _, ext = os.path.splitext(item["name"].lower())
            if ext not in TEXT_PREVIEW_EXTENSIONS:
                continue
            try:
                with open(safe_upload_path(item["path"]), "r", encoding="utf-8", errors="replace") as fh:
                    if content not in fh.read(300000).lower():
                        continue
            except OSError:
                continue
        item["size_label"] = format_bytes(item["size"])
        item["modified"] = datetime.fromtimestamp(item["mtime"]).strftime("%d %b %Y, %H:%M")
        item["parent_path"] = normalize_relative_path(os.path.dirname(item["path"]))
        item["extension"] = "" if item["is_dir"] else os.path.splitext(item["name"])[1].lower()
        item["metadata"] = meta
        results.append(item)
    return results[:500]


def file_hash(path: str, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def duplicate_groups(base_path: str) -> list[dict]:
    buckets = {}
    for item in iter_files(base_path) or []:
        if item["is_dir"] or item["size"] == 0:
            continue
        buckets.setdefault(item["size"], []).append(item)
    groups = []
    for same_size in buckets.values():
        if len(same_size) < 2:
            continue
        hashes = {}
        for item in same_size:
            try:
                hashes.setdefault(file_hash(safe_upload_path(item["path"])), []).append(item)
            except OSError:
                pass
        for digest, items in hashes.items():
            if len(items) > 1:
                groups.append({"hash": digest, "files": items, "size_label": format_bytes(items[0]["size"])})
    return groups
