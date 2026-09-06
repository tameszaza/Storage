import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import current_app


SCHEDULES = {
    0: "Manual only",
    1: "Every hour",
    6: "Every 6 hours",
    24: "Daily",
    168: "Weekly",
}


def validate_interval_hours(value: int | str) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sync frequency must be a whole number of hours.") from exc
    if interval < 0 or interval > 720:
        raise ValueError("Sync frequency must be between 0 and 720 hours.")
    return interval


def _safe_library_name(name: str, playlist_id: str) -> str:
    cleaned = re.sub(r"[^\w .()'-]+", "_", name, flags=re.UNICODE).strip(" .")[:80]
    return f"{cleaned or 'Playlist'} [{playlist_id}]"


def _safe_playlist_file(name: str, playlist_id: str, used: set[str]) -> str:
    cleaned = re.sub(r"[^\w .()'-]+", "_", name, flags=re.UNICODE).strip(" .")[:100]
    preferred = f"{cleaned or 'Playlist'}.m3u"
    if preferred not in used:
        return preferred
    return f"{cleaned or 'Playlist'} [{playlist_id}].m3u"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).replace(microsecond=0).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def playlists_file() -> Path:
    return Path(current_app.config["PLAYLISTS_FILE"])


def log_dir() -> Path:
    path = Path(current_app.config["PLAYLIST_LOG_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _locked_records():
    path = playlists_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            except (OSError, json.JSONDecodeError):
                records = []
            if not isinstance(records, list):
                records = []
            yield records
            fd, temporary = tempfile.mkstemp(prefix=".playlists-", suffix=".json", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(records, output, indent=2, ensure_ascii=False)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def validate_url(value: str) -> str:
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Enter a valid HTTP or HTTPS playlist URL without embedded credentials.")
    if len(value) > 2048:
        raise ValueError("Playlist URL is too long.")
    return value


def list_playlists() -> list[dict]:
    with _locked_records() as records:
        result = [dict(item) for item in records]
    return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)


def add_playlist(name: str, url: str, interval_hours: int, sync_now: bool = True) -> dict:
    url = validate_url(url)
    try:
        interval_hours = validate_interval_hours(interval_hours)
    except ValueError:
        interval_hours = 24
    name = str(name or "").strip()[:120] or "Playlist"
    playlist_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    with _locked_records() as records:
        used_playlist_files = {
            str(item.get("playlist_file_name")) for item in records
            if item.get("id") != playlist_id and item.get("playlist_file_name")
        }
        existing = next((item for item in records if item.get("id") == playlist_id), None)
        if existing:
            existing.update(name=name, url=url, interval_hours=interval_hours, enabled=True)
            stable_name = _safe_library_name(name, playlist_id)
            existing.setdefault("library_dir_name", stable_name)
            existing.setdefault(
                "playlist_file_name", _safe_playlist_file(name, playlist_id, used_playlist_files)
            )
            if sync_now:
                existing["pending"] = True
            return dict(existing)
        stable_name = _safe_library_name(name, playlist_id)
        record = {
            "id": playlist_id,
            "name": name,
            "url": url,
            "interval_hours": interval_hours,
            "enabled": True,
            "pending": bool(sync_now),
            "status": "queued" if sync_now else "idle",
            "created_at": _iso(),
            "last_started_at": None,
            "last_finished_at": None,
            "last_result": "Not synced yet",
            "next_run_at": _iso(_now() + timedelta(hours=interval_hours)) if interval_hours else None,
            "library_dir_name": stable_name,
            "playlist_file_name": _safe_playlist_file(name, playlist_id, used_playlist_files),
            "pending_removals": 0,
            "delete_requested": False,
        }
        records.append(record)
        return dict(record)


def request_sync(playlist_id: str) -> bool:
    with _locked_records() as records:
        item = next((row for row in records if row.get("id") == playlist_id), None)
        if not item:
            return False
        item["pending"] = True
        if item.get("status") != "running":
            item["status"] = "queued"
        return True


def request_delete(playlist_id: str) -> bool:
    """Queue deletion of this source's downloaded files and playlist."""
    with _locked_records() as records:
        item = next((row for row in records if row.get("id") == playlist_id), None)
        if not item or item.get("status") in {"running", "deleting"}:
            return False
        item["delete_requested"] = True
        item["pending"] = False
        item["status"] = "delete_queued"
        item["last_result"] = "Playlist and downloaded files queued for deletion"
        return True


def claim_delete() -> dict | None:
    with _locked_records() as records:
        for item in records:
            if not item.get("delete_requested") or item.get("status") != "delete_queued":
                continue
            item["status"] = "deleting"
            item["last_result"] = "Deleting playlist files"
            return dict(item)
    return None


def complete_delete_failure(playlist_id: str, message: str) -> None:
    now = _now()
    with _locked_records() as records:
        item = next((row for row in records if row.get("id") == playlist_id), None)
        if not item:
            return
        item["delete_requested"] = False
        item["status"] = "failed"
        item["last_finished_at"] = _iso(now)
        item["last_result"] = str(message)[:500]


def remove_record(playlist_id: str) -> bool:
    with _locked_records() as records:
        before = len(records)
        records[:] = [row for row in records if row.get("id") != playlist_id]
        return len(records) != before


def set_interval(playlist_id: str, interval_hours: int | str) -> bool:
    interval = validate_interval_hours(interval_hours)
    with _locked_records() as records:
        item = next((row for row in records if row.get("id") == playlist_id), None)
        if not item:
            return False
        item["interval_hours"] = interval
        item["next_run_at"] = _iso(_now() + timedelta(hours=interval)) if interval else None
        return True


def set_enabled(playlist_id: str, enabled: bool) -> bool:
    with _locked_records() as records:
        item = next((row for row in records if row.get("id") == playlist_id), None)
        if not item:
            return False
        item["enabled"] = bool(enabled)
        return True


def delete_playlist(playlist_id: str) -> bool:
    with _locked_records() as records:
        item = next((row for row in records if row.get("id") == playlist_id), None)
        if item and item.get("delete_requested"):
            return False
        before = len(records)
        records[:] = [row for row in records if row.get("id") != playlist_id]
        return len(records) != before


def claim_next() -> dict | None:
    now = _now()
    with _locked_records() as records:
        for item in records:
            playlist_id = str(item.get("id") or "unknown")
            used_playlist_files = {
                str(row.get("playlist_file_name")) for row in records
                if row is not item and row.get("playlist_file_name")
            }
            item.setdefault("library_dir_name", _safe_library_name(item.get("name", "Playlist"), playlist_id))
            item.setdefault(
                "playlist_file_name",
                _safe_playlist_file(item.get("name", "Playlist"), playlist_id, used_playlist_files),
            )
            item.setdefault("pending_removals", 0)
            item.setdefault("delete_requested", False)
            if item.get("delete_requested"):
                continue
            if not item.get("enabled", True):
                continue
            started = _parse(item.get("last_started_at"))
            stale_running = item.get("status") == "running" and started and now - started > timedelta(hours=6)
            due = bool(item.get("pending"))
            next_run = _parse(item.get("next_run_at"))
            if item.get("interval_hours", 0) and (not next_run or next_run <= now):
                due = True
            if item.get("status") == "running" and not stale_running:
                continue
            if not due and not stale_running:
                continue
            item["pending"] = False
            item["status"] = "running"
            item["last_started_at"] = _iso(now)
            item["last_result"] = "Sync in progress"
            item["sync_success_count"] = 0
            item["sync_unavailable_count"] = 0
            item["sync_removed_count"] = 0
            return dict(item)
    return None


def recover_interrupted() -> int:
    """Requeue work left running when the worker container was stopped."""
    recovered = 0
    with _locked_records() as records:
        for item in records:
            if item.get("status") != "running":
                continue
            item["status"] = "queued"
            item["pending"] = True
            item["last_result"] = "Previous worker stopped; sync was safely requeued."
            recovered += 1
        for item in records:
            if item.get("status") != "deleting":
                continue
            item["status"] = "delete_queued"
            item["delete_requested"] = True
            item["last_result"] = "Previous worker stopped; deletion was safely requeued."
            recovered += 1
    return recovered


def complete_sync(playlist_id: str, success: bool, message: str, details: dict | None = None) -> None:
    now = _now()
    with _locked_records() as records:
        item = next((row for row in records if row.get("id") == playlist_id), None)
        if not item:
            return
        item["status"] = "success" if success else "failed"
        item["last_finished_at"] = _iso(now)
        item["last_result"] = str(message)[:500]
        if details:
            item.update(details)
        interval = int(item.get("interval_hours") or 0)
        item["next_run_at"] = _iso(now + timedelta(hours=interval)) if interval else None


def append_log(playlist_id: str, message: str) -> None:
    timestamp = _iso()
    with (log_dir() / f"{playlist_id}.log").open("a", encoding="utf-8", errors="replace") as output:
        output.write(f"[{timestamp}] {message.rstrip()}\n")


def begin_sync_log(playlist_id: str, message: str, keep_syncs: int = 5) -> None:
    """Start a log section and retain only the newest complete sync runs."""
    path = log_dir() / f"{playlist_id}.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        lines = []

    lines.append(f"[{_iso()}] {message.rstrip()}\n")
    marker = "] Checking complete remote snapshot for "
    starts = [index for index, line in enumerate(lines) if marker in line]
    keep_syncs = max(1, int(keep_syncs))
    if len(starts) > keep_syncs:
        lines = lines[starts[-keep_syncs]:]

    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.writelines(lines)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_log(playlist_id: str, max_bytes: int = 200_000) -> str:
    path = log_dir() / f"{playlist_id}.log"
    if not path.exists():
        return "No sync output yet."
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(max(0, size - max_bytes))
        data = source.read()
    return data.decode("utf-8", errors="replace")
