"""Short-lived, account-scoped review drafts for image-derived calendar events."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

from flask import current_app
from PIL import Image

from lib.assistant_calendar_import import import_confirmed_events


DRAFT_LIFETIME_SECONDS = 24 * 60 * 60
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{30,64}$")


def _directory() -> Path:
    directory = Path(current_app.config["CALENDAR_IMPORT_DIR"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def _paths(token: str) -> tuple[Path, Path]:
    if not _TOKEN.fullmatch(token):
        raise ValueError("Invalid calendar review link.")
    root = _directory()
    return root / f"{token}.json", root / f"{token}.jpg"


def _write_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".calendar-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load(path: Path, username: str) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    try:
        created_at = float(value.get("created_at") or 0) if isinstance(value, dict) else 0
    except (TypeError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("username") != username
            or time.time() - created_at > DRAFT_LIFETIME_SECONDS):
        return None
    return value


def _cleanup_expired(root: Path) -> None:
    now = time.time()
    for path in root.glob("*.json"):
        try:
            if now - path.stat().st_mtime <= DRAFT_LIFETIME_SECONDS:
                continue
            path.unlink()
            path.with_suffix(".jpg").unlink(missing_ok=True)
        except OSError:
            continue


def create_draft(username: str, candidates: list[dict], review: list[str], image: Image.Image) -> str:
    """Save a bounded preview; do not create any calendar event yet."""
    if not username or not 1 <= len(candidates) <= 50:
        raise ValueError("No verifiable dates were found to review.")
    root = _directory()
    _cleanup_expired(root)
    token = secrets.token_urlsafe(24)
    state_path, image_path = _paths(token)
    preview = image.copy()
    preview.thumbnail((3000, 3000))
    if preview.mode != "RGB":
        preview = preview.convert("RGB")
    descriptor, temporary = tempfile.mkstemp(prefix=".calendar-image-", suffix=".jpg", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            preview.save(handle, format="JPEG", quality=88, optimize=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, image_path)
        _write_json(state_path, {
            "username": username,
            "created_at": time.time(),
            "candidates": candidates,
            "review": review,
            "confirmed": False,
        })
    except Exception:
        image_path.unlink(missing_ok=True)
        raise
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return token


def load_draft(username: str, token: str) -> dict | None:
    try:
        state_path, _ = _paths(token)
    except ValueError:
        return None
    return _load(state_path, username)


def draft_image_path(username: str, token: str) -> Path | None:
    if load_draft(username, token) is None:
        return None
    _, image_path = _paths(token)
    return image_path if image_path.is_file() else None


def confirm_draft(username: str, token: str, rows: list[dict]) -> dict | None:
    """Confirm once across workers; duplicate checks run again at commit time."""
    try:
        state_path, _ = _paths(token)
    except ValueError:
        return None
    lock_path = _directory() / "calendar_import.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        draft = _load(state_path, username)
        if draft is None:
            return None
        if draft.get("confirmed"):
            return draft.get("result")
        result = import_confirmed_events(username, rows)
        draft["confirmed"] = True
        draft["result"] = result
        _write_json(state_path, draft)
        return result
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
