import hashlib
import mimetypes
import os
import shutil
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from flask import current_app

TEXT_PREVIEW_EXTENSIONS = {".txt", ".py", ".log", ".html", ".css", ".js", ".md", ".json", ".yml", ".yaml"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def upload_root() -> str:
    root = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(root, exist_ok=True)
    return root


def normalize_relative_path(path: str | None) -> str:
    if not path:
        return ""
    path = str(path).replace("\\", "/").strip("/")
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def safe_path_part(value: str, fallback: str = "unnamed") -> str:
    """Clean one filename or folder segment without destroying Unicode names."""
    value = str(value or "").replace("\x00", "").replace("\\", "_").replace("/", "_").strip()
    value = value.rstrip(".")
    if value in {"", ".", ".."}:
        return fallback
    return value


def safe_filename(filename: str) -> str:
    filename = str(filename or "").replace("\\", "/").split("/")[-1]
    return safe_path_part(filename, "unnamed-file")


def safe_relative_upload_name(filename: str) -> str:
    """Preserve safe nested folder paths from browser folder uploads."""
    normalized = str(filename or "").replace("\\", "/").strip("/")
    safe_parts: list[str] = []
    for index, part in enumerate(normalized.split("/")):
        part = part.strip()
        if part in {"", ".", ".."}:
            continue
        fallback = "unnamed-file" if index == len(normalized.split("/")) - 1 else "folder"
        safe_parts.append(safe_path_part(part, fallback))
    return "/".join(safe_parts) if safe_parts else "unnamed-file"


def safe_upload_path(*parts: str) -> str:
    root = os.path.abspath(upload_root())
    candidate = os.path.abspath(os.path.join(root, *[p for p in parts if p]))
    if os.path.commonpath([root, candidate]) != root:
        raise PermissionError("Path escapes upload directory")
    return candidate


def path_segments(path: str) -> list[str]:
    normalized = normalize_relative_path(path)
    return [part for part in normalized.split("/") if part]


def is_allowed_for_user(path: str, username: str | None) -> bool:
    if username == "Admin":
        return True
    segments = path_segments(path)
    return bool(username) and segments and segments[0] == username


def visible_path_for_user(path: str | None, username: str | None) -> str:
    normalized = normalize_relative_path(path)
    if username == "Admin":
        return normalized
    if not username:
        return ""
    if is_allowed_for_user(normalized, username):
        return normalized
    return username


def format_modification_time(mod_time: float) -> str:
    now = datetime.now()
    mod_datetime = datetime.fromtimestamp(mod_time)
    if mod_datetime.date() == now.date():
        return mod_datetime.strftime("%H:%M")
    if mod_datetime.date() == (now - timedelta(days=1)).date():
        return "Yesterday"
    if now - timedelta(days=6) <= mod_datetime < now:
        return mod_datetime.strftime("%A, %d %B")
    if mod_datetime.year == now.year:
        return mod_datetime.strftime("%d %B")
    return mod_datetime.strftime("%d %B %Y")


def format_bytes(size: int | float | None) -> str:
    if not size:
        return "0 B"
    size = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def get_folder_size(folder_path: str) -> int:
    total_size = 0
    for dirpath, _, filenames in os.walk(folder_path):
        for filename in filenames:
            fp = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(fp)
            except OSError:
                continue
    return total_size


def preview_text(file_path: str, max_lines: int = 6) -> str | None:
    suffix = Path(file_path).suffix.lower()
    if suffix not in TEXT_PREVIEW_EXTENSIONS:
        return None
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as file:
            lines = file.readlines()[:max_lines + 1]
    except OSError:
        return None
    preview = "".join(lines[:max_lines])
    if len(lines) > max_lines:
        preview += "..."
    return preview


def file_kind(name: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in TEXT_PREVIEW_EXTENSIONS:
        return "text"
    if ext == ".pdf":
        return "pdf"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz"}:
        return "archive"
    return "file"


def build_file_item(base_path: str, name: str, relative_root: str = "") -> dict:
    file_path = os.path.join(base_path, name)
    is_dir = os.path.isdir(file_path)
    size = get_folder_size(file_path) if is_dir else os.path.getsize(file_path)
    relative_path = normalize_relative_path(os.path.join(relative_root, name))
    extension = "" if is_dir else Path(name).suffix.lower()
    return {
        "name": name,
        "is_dir": is_dir,
        "size": size,
        "size_label": format_bytes(size),
        "mod_time": format_modification_time(os.path.getmtime(file_path)),
        "modified_timestamp": os.path.getmtime(file_path),
        "content": None if is_dir else preview_text(file_path),
        "kind": file_kind(name, is_dir),
        "extension": extension,
        "relative_path": relative_path,
        "parent_path": normalize_relative_path(os.path.dirname(relative_path)),
    }


def list_directory(relative_path: str, search_query: str | None = None) -> list[dict]:
    relative_path = normalize_relative_path(relative_path)
    current_path = safe_upload_path(relative_path)
    files: list[dict] = []
    if not os.path.exists(current_path):
        return files

    if search_query:
        query = search_query.casefold()
        for root, dirs, filenames in os.walk(current_path):
            dirs[:] = [directory for directory in dirs if directory != ".tamestorage_system"]
            rel_root = normalize_relative_path(os.path.relpath(root, current_path))
            rel_root = "" if rel_root == "." else rel_root
            full_relative_root = normalize_relative_path(os.path.join(relative_path, rel_root))
            for dirname in dirs:
                if query in dirname.casefold():
                    files.append(build_file_item(root, dirname, full_relative_root))
            for filename in filenames:
                if query in filename.casefold():
                    files.append(build_file_item(root, filename, full_relative_root))
    else:
        for name in os.listdir(current_path):
            if name == ".tamestorage_system":
                continue
            files.append(build_file_item(current_path, name, relative_path))

    files.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
    return files


def _sha256(path: str, max_size: int = 128 * 1024 * 1024) -> str | None:
    try:
        if os.path.getsize(path) > max_size:
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def get_path_details(relative_path: str) -> dict:
    """Return useful filesystem metadata for a file or folder preview."""
    relative_path = normalize_relative_path(relative_path)
    absolute = safe_upload_path(relative_path)
    if not os.path.exists(absolute):
        raise FileNotFoundError(absolute)

    stat_result = os.stat(absolute)
    is_dir = os.path.isdir(absolute)
    name = os.path.basename(relative_path) or os.path.basename(absolute)
    extension = "" if is_dir else Path(name).suffix.lower()
    mime_type = "inode/directory" if is_dir else (mimetypes.guess_type(name)[0] or "application/octet-stream")
    size = get_folder_size(absolute) if is_dir else stat_result.st_size
    parts = path_segments(relative_path)
    details = {
        "name": name,
        "path": relative_path,
        "parent_path": normalize_relative_path(os.path.dirname(relative_path)),
        "owner": parts[0] if parts else "Root",
        "is_dir": is_dir,
        "kind": file_kind(name, is_dir),
        "extension": extension or "Folder",
        "mime_type": mime_type,
        "size": size,
        "size_label": format_bytes(size),
        "modified": datetime.fromtimestamp(stat_result.st_mtime).strftime("%d %B %Y, %H:%M:%S"),
        "modified_iso": datetime.fromtimestamp(stat_result.st_mtime).isoformat(timespec="seconds"),
        "created": datetime.fromtimestamp(stat_result.st_ctime).strftime("%d %B %Y, %H:%M:%S"),
        "created_iso": datetime.fromtimestamp(stat_result.st_ctime).isoformat(timespec="seconds"),
        "permissions": stat.filemode(stat_result.st_mode),
        "checksum": None if is_dir else _sha256(absolute),
        "checksum_skipped": (not is_dir and size > 128 * 1024 * 1024),
    }

    if is_dir:
        try:
            children = [entry for entry in os.scandir(absolute) if entry.name != ".tamestorage_system"]
        except OSError:
            children = []
        details["item_count"] = len(children)
        details["folder_count"] = sum(1 for entry in children if entry.is_dir(follow_symlinks=False))
        details["file_count"] = sum(1 for entry in children if entry.is_file(follow_symlinks=False))
    elif extension in IMAGE_EXTENSIONS:
        try:
            from PIL import Image
            with Image.open(absolute) as image:
                details["dimensions"] = f"{image.width} × {image.height} px"
                details["image_mode"] = image.mode
        except Exception:
            pass
    elif extension in TEXT_PREVIEW_EXTENSIONS and size <= 10 * 1024 * 1024:
        try:
            text = Path(absolute).read_text(encoding="utf-8", errors="replace")
            details["line_count"] = text.count("\n") + (1 if text else 0)
            details["word_count"] = len(text.split())
            details["character_count"] = len(text)
        except OSError:
            pass

    return details


def valid_folder_name(folder_name: str) -> bool:
    if not folder_name:
        return False
    if folder_name[-1] == " " or folder_name.startswith("."):
        return False
    return "/" not in folder_name and "\\" not in folder_name


def delete_path(relative_path: str) -> None:
    target = safe_upload_path(relative_path)
    if os.path.isdir(target):
        shutil.rmtree(target)
    elif os.path.exists(target):
        os.remove(target)


def rename_path(parent_path: str, old_name: str, new_name: str) -> None:
    old_path = safe_upload_path(parent_path, safe_filename(old_name))
    new_path = safe_upload_path(parent_path, safe_filename(new_name))
    if not os.path.exists(old_path):
        raise FileNotFoundError(old_path)
    if os.path.exists(new_path):
        raise FileExistsError(new_path)
    os.rename(old_path, new_path)


def get_total_storage_bytes() -> int:
    return get_folder_size(upload_root())


def user_storage_usage(users: Iterable[str]) -> dict[str, int]:
    usage = {}
    for username in users:
        folder = safe_upload_path(username)
        usage[username] = get_folder_size(folder) if os.path.exists(folder) else 0
    return usage


def get_user_file_structure(username: str | None) -> str:
    if not username:
        return "No active user."
    user_folder_path = safe_upload_path(username)
    if not os.path.exists(user_folder_path):
        return f"{username}/"

    structure = []
    for root, _, files in os.walk(user_folder_path):
        level = root.replace(user_folder_path, "").count(os.sep)
        indent = " " * 4 * level
        structure.append(f"{indent}{os.path.basename(root)}/")
        subindent = " " * 4 * (level + 1)
        for filename in files:
            structure.append(f"{subindent}{filename}")
    return "\n".join(structure)
