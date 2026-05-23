import os
import secrets
from datetime import datetime, timezone
from typing import Any

from flask import current_app

from lib.extensions import bcrypt
from lib.json_store import read_json, write_json
from lib.storage import normalize_relative_path, safe_upload_path

PERMISSION_PRESETS = {
    "view": {
        "label": "View only",
        "description": "People can open previews but cannot download or change files.",
        "can_download": False,
        "can_upload": False,
        "can_edit": False,
        "can_delete": False,
    },
    "download": {
        "label": "View and download",
        "description": "People can preview and download the shared item.",
        "can_download": True,
        "can_upload": False,
        "can_edit": False,
        "can_delete": False,
    },
    "upload": {
        "label": "Folder drop box",
        "description": "People can preview, download, and upload files into this folder.",
        "can_download": True,
        "can_upload": True,
        "can_edit": False,
        "can_delete": False,
    },
    "edit": {
        "label": "Editor",
        "description": "People can preview, download, upload to folders, and edit text files.",
        "can_download": True,
        "can_upload": True,
        "can_edit": True,
        "can_delete": False,
    },
    "manage": {
        "label": "Full control",
        "description": "People can download, upload, edit text files, and delete shared content.",
        "can_download": True,
        "can_upload": True,
        "can_edit": True,
        "can_delete": True,
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def shares_file() -> str:
    return current_app.config.get("SHARE_DATA_FILE", "shares.json")


def audit_file() -> str:
    return current_app.config.get("SHARE_AUDIT_FILE", "share_audit.json")


def load_shares() -> dict[str, dict[str, Any]]:
    data = read_json(shares_file(), {})
    return data if isinstance(data, dict) else {}


def save_shares(shares: dict[str, dict[str, Any]]) -> None:
    write_json(shares_file(), shares)


def load_audit() -> list[dict[str, Any]]:
    data = read_json(audit_file(), [])
    return data if isinstance(data, list) else []


def save_audit(items: list[dict[str, Any]]) -> None:
    write_json(audit_file(), items[-1000:])


def log_share_event(token: str, actor: str | None, action: str, detail: str = "") -> None:
    items = load_audit()
    items.append({
        "time": now_iso(),
        "token": token,
        "actor": actor or "anonymous",
        "action": action,
        "detail": detail,
    })
    save_audit(items)


def normalize_user_list(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value or "").replace("\n", ",").split(",")
    result: list[str] = []
    seen = set()
    for item in raw_items:
        username = str(item).strip()
        if not username or username in seen:
            continue
        seen.add(username)
        result.append(username)
    return result


def create_share(
    *,
    owner: str,
    path: str,
    access_mode: str,
    permission: str,
    allowed_users: str | list[str] | None = None,
    password: str | None = None,
    expires_at: str | None = None,
    max_downloads: str | int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    path = normalize_relative_path(path)
    target = safe_upload_path(path)
    if not os.path.exists(target):
        raise FileNotFoundError(path)

    access_mode = access_mode if access_mode in {"link", "restricted"} else "link"
    permission = permission if permission in PERMISSION_PRESETS else "download"
    token = secrets.token_urlsafe(18)
    preset = PERMISSION_PRESETS[permission]

    max_download_value = None
    if max_downloads not in (None, ""):
        max_download_value = max(1, int(max_downloads))

    expires_value = None
    if expires_at:
        parsed = parse_iso(str(expires_at))
        if parsed:
            expires_value = parsed.isoformat(timespec="seconds")

    share = {
        "token": token,
        "owner": owner,
        "path": path,
        "name": os.path.basename(path) or "Root",
        "is_dir": os.path.isdir(target),
        "access_mode": access_mode,
        "permission": permission,
        "allowed_users": normalize_user_list(allowed_users),
        "password_hash": bcrypt.generate_password_hash(password).decode("utf-8") if password else "",
        "expires_at": expires_value,
        "max_downloads": max_download_value,
        "download_count": 0,
        "revoked": False,
        "note": str(note or "").strip(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    shares = load_shares()
    shares[token] = share
    save_shares(shares)
    log_share_event(token, owner, "created", path)
    return share


def get_share(token: str) -> dict[str, Any] | None:
    return load_shares().get(token)


def update_share(token: str, updates: dict[str, Any], actor: str | None = None) -> dict[str, Any] | None:
    shares = load_shares()
    share = shares.get(token)
    if not share:
        return None
    share.update(updates)
    share["updated_at"] = now_iso()
    shares[token] = share
    save_shares(shares)
    log_share_event(token, actor, "updated", ", ".join(updates.keys()))
    return share


def revoke_share(token: str, actor: str | None = None) -> None:
    update_share(token, {"revoked": True}, actor)


def delete_share(token: str, actor: str | None = None) -> None:
    shares = load_shares()
    if token in shares:
        del shares[token]
        save_shares(shares)
        log_share_event(token, actor, "deleted")


def increment_download_count(token: str, actor: str | None = None, detail: str = "") -> None:
    shares = load_shares()
    share = shares.get(token)
    if not share:
        return
    share["download_count"] = int(share.get("download_count") or 0) + 1
    share["updated_at"] = now_iso()
    shares[token] = share
    save_shares(shares)
    log_share_event(token, actor, "downloaded", detail)


def share_status(share: dict[str, Any]) -> tuple[bool, str]:
    if share.get("revoked"):
        return False, "This share link has been revoked."
    expires_at = parse_iso(share.get("expires_at"))
    if expires_at and expires_at <= _now():
        return False, "This share link has expired."
    max_downloads = share.get("max_downloads")
    if max_downloads and int(share.get("download_count") or 0) >= int(max_downloads):
        return False, "This share link has reached its download limit."
    if not os.path.exists(safe_upload_path(share.get("path", ""))):
        return False, "The shared file or folder no longer exists."
    return True, "Active"


def share_permissions(share: dict[str, Any]) -> dict[str, bool]:
    preset = PERMISSION_PRESETS.get(share.get("permission"), PERMISSION_PRESETS["download"])
    return {
        "can_preview": True,
        "can_download": bool(preset["can_download"]),
        "can_upload": bool(preset["can_upload"]),
        "can_edit": bool(preset["can_edit"]),
        "can_delete": bool(preset["can_delete"]),
    }


def user_can_open_share(share: dict[str, Any], username: str | None) -> bool:
    if username in {share.get("owner"), "Admin"}:
        return True
    if share.get("access_mode") == "link":
        return True
    return bool(username and username in share.get("allowed_users", []))


def is_password_unlocked(share: dict[str, Any], username: str | None, unlocked_tokens: list[str] | set[str] | None) -> bool:
    if not share.get("password_hash"):
        return True
    if username in {share.get("owner"), "Admin"}:
        return True
    return share.get("token") in set(unlocked_tokens or [])


def check_share_password(share: dict[str, Any], password: str) -> bool:
    password_hash = share.get("password_hash") or ""
    return bool(password_hash and bcrypt.check_password_hash(password_hash, password))


def target_inside_share(share: dict[str, Any], subpath: str = "") -> tuple[str, str]:
    base_rel = normalize_relative_path(share.get("path", ""))
    subpath = normalize_relative_path(subpath)
    if not share.get("is_dir") and subpath:
        raise PermissionError("A file share cannot contain nested paths")

    target_rel = base_rel if not subpath else normalize_relative_path(f"{base_rel}/{subpath}")
    base_abs = safe_upload_path(base_rel)
    target_abs = safe_upload_path(target_rel)

    if os.path.isdir(base_abs):
        common = os.path.commonpath([base_abs, target_abs])
        if common != base_abs:
            raise PermissionError("Path escapes shared folder")
    elif target_abs != base_abs:
        raise PermissionError("Path escapes shared file")

    return target_rel, target_abs
