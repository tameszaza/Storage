from __future__ import annotations

import os
import hashlib
import heapq
import fcntl
import json
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from flask import current_app, has_app_context
from lib.metadata import all_metadata
from lib.storage import TEXT_PREVIEW_EXTENSIONS, file_kind, format_bytes, normalize_relative_path, safe_upload_path


_SEARCH_CACHE_SECONDS = 60
_search_cache: dict[str, tuple[float, list[dict]]] = {}
_search_cache_lock = threading.Lock()


def _snapshot_file(key: str) -> Path | None:
    if not has_app_context():
        return None
    configured = current_app.config.get("SEARCH_INDEX_CACHE_DIR")
    if not configured:
        return None
    directory = Path(configured)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".json")


def _read_snapshot(path: Path) -> tuple[float, list[dict]] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        items = value.get("items") if isinstance(value, dict) else None
        if not isinstance(items, list):
            return None
        return path.stat().st_mtime, items
    except (OSError, ValueError):
        return None


def _write_snapshot(path: Path, items: list[dict]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".search-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"items": items}, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _refresh_in_background(key: str, path: Path) -> None:
    lock_path = path.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return
    application = current_app._get_current_object()

    def work() -> None:
        try:
            with application.app_context():
                items = list(iter_files(key) or [])
                _write_snapshot(path, items)
                with _search_cache_lock:
                    _search_cache[key] = (time.time(), items)
        except Exception:
            application.logger.exception("Search inventory refresh failed")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    threading.Thread(target=work, name="search-inventory-refresh", daemon=True).start()


def invalidate_search_cache() -> None:
    """Call after app-managed file changes; SMB changes age out shortly."""
    with _search_cache_lock:
        _search_cache.clear()
    if has_app_context():
        configured = current_app.config.get("SEARCH_INDEX_CACHE_DIR")
        if not configured:
            return
        directory = Path(configured)
        if directory.is_dir():
            for path in directory.glob("*.json"):
                try:
                    os.utime(path, (0, 0))
                except OSError:
                    pass


def _search_snapshot(base_path: str) -> list[dict]:
    key = normalize_relative_path(base_path)
    now = time.time()
    with _search_cache_lock:
        cached = _search_cache.get(key)
    path = _snapshot_file(key)
    if cached:
        if now - cached[0] >= _SEARCH_CACHE_SECONDS and path is not None:
            _refresh_in_background(key, path)
        elif now - cached[0] >= _SEARCH_CACHE_SECONDS:
            items = list(iter_files(key) or [])
            with _search_cache_lock:
                _search_cache[key] = (time.time(), items)
            return items
        return cached[1]
    if path is not None:
        persisted = _read_snapshot(path)
        if persisted:
            with _search_cache_lock:
                _search_cache[key] = persisted
            if now - persisted[0] >= _SEARCH_CACHE_SECONDS:
                _refresh_in_background(key, path)
            return persisted[1]
    # Only the first inventory build is synchronous. Its persisted snapshot is
    # reused across workers and restarts; later refreshes never block a page.
    items = list(iter_files(key) or [])
    if path is not None:
        _write_snapshot(path, items)
    with _search_cache_lock:
        _search_cache[key] = (time.time(), items)
    return items


def workspace_stats(base_path: str) -> dict[str, int]:
    """Count the full indexed tree without the search page's display limit."""
    files = 0
    folders = 0
    size = 0
    for item in _search_snapshot(base_path):
        if item["is_dir"]:
            folders += 1
        else:
            files += 1
            size += int(item["size"])
    return {"files": files, "folders": folders, "bytes": size}


def workspace_inventory(base_path: str) -> list[dict]:
    """Return the shared file inventory for read-only storage summaries.

    Callers must enforce their own path permissions and must not mutate entries.
    The index is refreshed in the background when it becomes stale.
    """
    return _search_snapshot(base_path)


def iter_files(base_relative_path: str):
    base_relative_path = normalize_relative_path(base_relative_path)
    base = safe_upload_path(base_relative_path)
    if not os.path.exists(base):
        return
    # Calculate nested folder sizes once, from leaves to root. Calling
    # get_folder_size for every directory rescanned the same files repeatedly.
    folder_sizes = {}
    children = {}
    entries = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != ".tamestorage_system" and not os.path.islink(os.path.join(root, d))]
        children[root] = [os.path.join(root, name) for name in dirs]
        folder_sizes[root] = 0
        for dirname in dirs:
            absolute = os.path.join(root, dirname)
            rel = normalize_relative_path(os.path.join(base_relative_path, os.path.relpath(absolute, base)))
            try:
                mtime = os.path.getmtime(absolute)
            except OSError:
                continue
            entries.append({"path": rel, "name": dirname, "is_dir": True, "kind": "folder", "absolute": absolute, "mtime": mtime})
        for filename in files:
            absolute = os.path.join(root, filename)
            if os.path.islink(absolute):
                continue
            rel = normalize_relative_path(os.path.join(base_relative_path, os.path.relpath(absolute, base)))
            try:
                stat_result = os.stat(absolute)
            except OSError:
                continue
            folder_sizes[root] += stat_result.st_size
            entries.append({"path": rel, "name": filename, "is_dir": False, "kind": file_kind(filename, False), "size": stat_result.st_size, "mtime": stat_result.st_mtime})
    for root in reversed(children):
        folder_sizes[root] += sum(folder_sizes.get(child, 0) for child in children[root])
    for item in entries:
        if item["is_dir"]:
            item["size"] = folder_sizes.get(item.pop("absolute"), 0)
        yield item


def _present_item(snapshot_item: dict, metadata: dict) -> dict:
    item = snapshot_item.copy()
    item["size_label"] = format_bytes(item["size"])
    item["modified"] = datetime.fromtimestamp(item["mtime"]).strftime("%d %b %Y, %H:%M")
    item["parent_path"] = normalize_relative_path(os.path.dirname(item["path"]))
    item["extension"] = "" if item["is_dir"] else os.path.splitext(item["name"])[1].lower()
    item["metadata"] = metadata.get(item["path"], {})
    return item


def recent_files(base_path: str, limit: int = 100) -> list[dict]:
    """Choose newest entries from the full inventory, before the UI limit."""
    if limit <= 0:
        return []
    newest = heapq.nlargest(limit, _search_snapshot(base_path), key=lambda item: item["mtime"])
    metadata = all_metadata()
    return [_present_item(item, metadata) for item in newest]


def matching_file_paths(
    base_path: str, query: str, *, limit: int = 30,
    allowed: Callable[[str], bool] | None = None,
) -> list[str]:
    """Search the full cached inventory without the file-browser result cap.

    Filename matches precede path-only matches; this is for compact pickers,
    so metadata and folder-size presentation are deliberately skipped.
    """
    needle = (query or "").strip().casefold()
    if len(needle) < 2 or limit <= 0:
        return []

    def candidates():
        for item in _search_snapshot(base_path):
            if item["is_dir"]:
                continue
            path = item["path"]
            name = item["name"].casefold()
            if needle not in path.casefold() or (allowed and not allowed(path)):
                continue
            rank = 0 if name.startswith(needle) else 1 if needle in name else 2
            yield (rank, -item["mtime"], path.casefold(), path)

    return [entry[3] for entry in heapq.nsmallest(limit, candidates())]


def search_files(base_path: str, query: str = "", kind: str = "", tag: str = "", starred: bool = False, min_size: int | None = None, max_size: int | None = None, modified_days: int | None = None, content: str = "", limit: int | None = 500) -> list[dict]:
    query = (query or "").lower().strip()
    tag = (tag or "").lower().strip()
    content = (content or "").lower().strip()
    cutoff = datetime.now() - timedelta(days=modified_days) if modified_days else None
    results = []
    metadata = all_metadata()
    for snapshot_item in _search_snapshot(base_path):
        item = snapshot_item.copy()
        meta = metadata.get(item["path"], {})
        tags = meta.get("tags", [])
        normalized_tags = [str(value).casefold() for value in tags]
        note = meta.get("note", "")
        if query and query not in item["name"].lower() and query not in item["path"].lower() and query not in note.lower():
            continue
        if kind and kind != "all" and item["kind"] != kind:
            continue
        if tag and tag.casefold() not in normalized_tags:
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
        results.append(_present_item(item, metadata))
    return results[:limit] if limit is not None else results


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
