import heapq
import io
import os
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import current_app
from PIL import Image

from lib.storage import (
    TEXT_PREVIEW_EXTENSIONS,
    format_bytes,
    is_allowed_for_user,
    normalize_relative_path,
    safe_upload_path,
    upload_root,
)

_client = None
_client_error = None

MAX_CONTEXT_CHARS = 12000
MAX_TREE_CHARS = 16000
MAX_TOOL_TEXT_CHARS = 16000
MAX_TOOL_LINES = 240
MAX_TOOL_RESULTS = 50
MAX_TOOL_SCAN_FILES = 250000
SAFE_TEXT_EXTENSIONS = set(TEXT_PREVIEW_EXTENSIONS) | {".csv", ".tsv", ".tex", ".rst", ".ini", ".toml"}
SENSITIVE_CONTEXT_NAMES = {
    ".env",
    "config.txt",
    "credentials.json",
    "secrets.json",
    "service-account.json",
    "microsoft_calendar.json",
    "microsoft_calendar_cache.json",
}
SYSTEM_DIRECTORY_NAME = ".tamestorage_system"

ASSISTANT_CONTRACT = """You are the file workspace assistant inside Tamestorage.

Your job is to help the signed-in user understand, find, organize, and safely act on their files.

Grounding rules:
- Treat supplied context and workspace-tool results as the only known filesystem facts.
- Never invent a filename, path, file content, size, or completed action.
- Use the read-only workspace tools whenever the question needs exact filesystem evidence. Keep calling tools until you have enough evidence to answer.
- For storage questions such as "what file takes the most space", call largest_files or largest_folders instead of guessing from names.
- For content questions, search or inspect first, then call read_text_file on the relevant safe text file. Do not ask the user to manually select a file when the tools can locate it.
- A directory listing proves that an item exists, but not a file's contents. Only read_text_file or a supplied selected-file preview proves text content.
- Use exact visible paths in backticks. Do not expose internal absolute paths, tool traces, secrets, or internal IDs.
- Be concise, specific, and action-oriented. Lead with the useful result.
- Destructive actions are never available through the automatic tools and require an explicit user command.
- Tamestorage can directly run: /open path, /inspect path, /move source -> folder, /copy source -> folder, and /rename source -> new-name.
- If a user asks you to perform one of those operations, provide the exact command when it has not already been executed.
- If evidence remains insufficient after using the available tools, state exactly what is missing."""


def read_api_key() -> str | None:
    path = current_app.config["GEMINI_CONFIG_PATH"]
    try:
        with open(path, "r", encoding="utf-8") as file:
            key = file.read().strip()
            return key or None
    except OSError:
        return None


def get_client():
    global _client, _client_error

    if _client is not None:
        return _client
    if _client_error is not None:
        raise RuntimeError(_client_error)

    api_key = read_api_key()
    if not api_key:
        _client_error = "Gemini API key is missing. Put it in uploads/Admin/config.txt or set GEMINI_CONFIG_PATH."
        raise RuntimeError(_client_error)

    try:
        from google import genai

        _client = genai.Client(api_key=api_key)
        return _client
    except ModuleNotFoundError as exc:
        _client_error = "Missing Gemini SDK. Run: pip install -U google-genai"
        raise RuntimeError(_client_error) from exc
    except Exception as exc:
        _client_error = str(exc)
        raise RuntimeError(_client_error) from exc


def get_model():
    return get_client()


def initial_history(username: str | None) -> list[dict]:
    username = username or "Unknown user"
    return [
        {
            "role": "user",
            "parts": f"{ASSISTANT_CONTRACT}\n\nSigned-in user: {username}",
        },
    ]


def is_ai_readable_file(path: str) -> bool:
    normalized = normalize_relative_path(path)
    parts = Path(normalized).parts
    if not normalized or SYSTEM_DIRECTORY_NAME in parts:
        return False
    if Path(normalized).name.casefold() in SENSITIVE_CONTEXT_NAMES:
        return False
    return Path(normalized).suffix.lower() in SAFE_TEXT_EXTENSIONS


def _workspace_tree(username: str | None) -> str:
    if not username:
        return "No active user."

    relative_root = "" if username == "Admin" else normalize_relative_path(username)
    absolute_root = upload_root() if username == "Admin" else safe_upload_path(relative_root)
    if not os.path.isdir(absolute_root):
        return f"{relative_root or 'Storage'}/"

    lines = ["Storage/"]
    for root, directories, files in os.walk(absolute_root):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory != SYSTEM_DIRECTORY_NAME and not os.path.islink(os.path.join(root, directory))
        )
        relative_directory = normalize_relative_path(os.path.relpath(root, absolute_root))
        relative_directory = "" if relative_directory == "." else relative_directory
        level = len(Path(relative_directory).parts)
        if relative_directory:
            lines.append(f"{'    ' * level}{Path(relative_directory).name}/")
        for filename in sorted(files):
            file_path = os.path.join(root, filename)
            if os.path.islink(file_path) or Path(filename).name.casefold() in SENSITIVE_CONTEXT_NAMES:
                continue
            lines.append(f"{'    ' * (level + 1)}{filename}")
        if sum(len(line) + 1 for line in lines) >= MAX_TREE_CHARS:
            lines.append("    [Workspace tree truncated]")
            break
    return "\n".join(lines)[:MAX_TREE_CHARS]


def _part_text(part: Any) -> str:
    if isinstance(part, dict):
        return str(part.get("text") or part.get("parts") or "")
    return str(part or "")


def _message_text(message: dict) -> str:
    parts = message.get("parts", "")
    if isinstance(parts, str):
        return parts
    if isinstance(parts, list):
        return "\n".join(_part_text(part) for part in parts).strip()
    return str(parts or "")


def _api_history(history: list[dict]) -> list[dict]:
    contents: list[dict] = []

    for item in history:
        text = _message_text(item).strip()
        if not text:
            continue

        role = "model" if item.get("role") == "model" else "user"
        content = {"role": role, "parts": [{"text": text}]}

        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": text})
        else:
            contents.append(content)

    return contents


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text)
    return "I received a response, but it did not contain text."


def _read_text_file_for_ai(username: str | None, file_path: str) -> str:
    normalized = normalize_relative_path(file_path)
    if not normalized:
        return ""
    if not is_allowed_for_user(normalized, username):
        return "Selected file context was ignored because this user cannot access that path."

    absolute = safe_upload_path(normalized)
    if not os.path.exists(absolute) or not os.path.isfile(absolute):
        return f"Selected file context was requested, but the file was not found: {normalized}"

    suffix = Path(absolute).suffix.lower()
    if not is_ai_readable_file(normalized):
        return f"Selected file context was requested, but this file is not allowed for AI text reading: {suffix or 'no extension'}"

    try:
        with open(absolute, "r", encoding="utf-8", errors="replace") as file:
            content = file.read(MAX_CONTEXT_CHARS + 1)
    except OSError:
        return "Selected file context was requested, but the file could not be read."

    truncated = len(content) > MAX_CONTEXT_CHARS
    content = content[:MAX_CONTEXT_CHARS]
    suffix_note = "\n[Content truncated for safety.]" if truncated else ""
    return f"Selected file path: {normalized}\nSelected file preview:\n```\n{content}\n```{suffix_note}"


def _workspace_root(username: str | None) -> tuple[str, str]:
    if not username:
        raise PermissionError("No signed-in user is available.")
    relative_root = "" if username == "Admin" else normalize_relative_path(username)
    absolute_root = upload_root() if username == "Admin" else safe_upload_path(relative_root)
    return relative_root, absolute_root


def _is_sensitive_path(path: str) -> bool:
    normalized = normalize_relative_path(path)
    parts = Path(normalized).parts
    return SYSTEM_DIRECTORY_NAME in parts or Path(normalized).name.casefold() in SENSITIVE_CONTEXT_NAMES


def _resolve_workspace_path(username: str | None, path: str = "") -> tuple[str, str]:
    relative_root, _ = _workspace_root(username)
    requested = normalize_relative_path(path)
    requested_lower = requested.casefold()

    if requested_lower in {"", ".", "root", "storage", "workspace"}:
        requested = ""
    elif requested_lower.startswith("storage/"):
        requested = requested.split("/", 1)[1]

    if username == "Admin":
        normalized = requested
    elif requested == relative_root or requested.startswith(relative_root + "/"):
        normalized = requested
    else:
        normalized = normalize_relative_path(os.path.join(relative_root, requested))

    if username != "Admin" and not is_allowed_for_user(normalized, username):
        raise PermissionError("That path is outside this account's workspace.")
    if _is_sensitive_path(normalized):
        raise PermissionError("That protected path is not available to the assistant.")

    absolute = safe_upload_path(normalized)
    _relative_root, workspace_absolute = _workspace_root(username)
    workspace_real = os.path.realpath(workspace_absolute)
    candidate_real = os.path.realpath(absolute)
    if os.path.commonpath([workspace_real, candidate_real]) != workspace_real:
        raise PermissionError("That path resolves outside this account's workspace.")
    if os.path.islink(absolute):
        raise PermissionError("Symbolic-link paths are not available to the assistant.")
    return normalized, absolute


def _visible_path(path: str) -> str:
    return normalize_relative_path(path) or "Root"


def _relative_from_absolute(path: str) -> str:
    return normalize_relative_path(os.path.relpath(path, upload_root()))


def _modified_label(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return "Unknown"


def _safe_limit(value: int, default: int = 20, maximum: int = MAX_TOOL_RESULTS) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def _scan_directory_records(scope_absolute: str) -> tuple[list[dict], int, int, int, bool]:
    records = []
    scanned_files = 0
    protected_files = 0
    protected_bytes = 0
    truncated = False

    for root, directories, files in os.walk(scope_absolute, topdown=True):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory != SYSTEM_DIRECTORY_NAME and not os.path.islink(os.path.join(root, directory))
        )
        direct_size = 0
        direct_files = 0
        for filename in files:
            absolute = os.path.join(root, filename)
            if os.path.islink(absolute):
                continue
            try:
                size = os.path.getsize(absolute)
            except OSError:
                continue
            relative = _relative_from_absolute(absolute)
            if Path(filename).name.casefold() in SENSITIVE_CONTEXT_NAMES:
                protected_files += 1
                protected_bytes += size
            direct_size += size
            direct_files += 1
            scanned_files += 1
            if scanned_files >= MAX_TOOL_SCAN_FILES:
                truncated = True
                break
        records.append({
            "path": root,
            "directories": list(directories),
            "direct_size": direct_size,
            "direct_files": direct_files,
        })
        if truncated:
            break

    return records, scanned_files, protected_files, protected_bytes, truncated


def _directory_totals(scope_absolute: str) -> tuple[dict[str, int], dict[str, int], dict]:
    records, scanned_files, protected_files, protected_bytes, truncated = _scan_directory_records(scope_absolute)
    sizes: dict[str, int] = {}
    file_counts: dict[str, int] = {}
    record_paths = {record["path"] for record in records}

    for record in reversed(records):
        current = record["path"]
        child_paths = [
            os.path.join(current, name)
            for name in record["directories"]
            if os.path.join(current, name) in record_paths
        ]
        sizes[current] = record["direct_size"] + sum(sizes.get(child, 0) for child in child_paths)
        file_counts[current] = record["direct_files"] + sum(file_counts.get(child, 0) for child in child_paths)

    return sizes, file_counts, {
        "scanned_files": scanned_files,
        "protected_files": protected_files,
        "protected_size": format_bytes(protected_bytes),
        "scan_truncated": truncated,
    }


def _tool_error(exc: Exception) -> dict:
    if isinstance(exc, PermissionError):
        return {"error": str(exc)}
    if isinstance(exc, FileNotFoundError):
        return {"error": "The requested path does not exist."}
    return {"error": str(exc) or exc.__class__.__name__}


def build_workspace_tools(username: str | None) -> list[Callable]:
    """Create user-scoped read-only functions for Gemini automatic function calling."""

    def workspace_summary() -> dict:
        """Get exact file, folder, and storage totals for the signed-in user's workspace."""
        try:
            relative_root, absolute_root = _workspace_root(username)
            if not os.path.isdir(absolute_root):
                return {"scope": _visible_path(relative_root), "files": 0, "folders": 0, "total_size": "0 B"}
            sizes, file_counts, scan = _directory_totals(absolute_root)
            folders = max(0, len(sizes) - 1)
            return {
                "scope": _visible_path(relative_root),
                "files": file_counts.get(absolute_root, 0),
                "folders": folders,
                "total_size_bytes": sizes.get(absolute_root, 0),
                "total_size": format_bytes(sizes.get(absolute_root, 0)),
                **scan,
            }
        except Exception as exc:
            return _tool_error(exc)

    def list_directory(path: str = "", limit: int = 50) -> dict:
        """List direct files and folders in a workspace directory.

        Args:
            path: Visible workspace path. Use an empty string for the workspace root.
            limit: Maximum number of direct children to return, from 1 to 100.
        """
        try:
            normalized, absolute = _resolve_workspace_path(username, path)
            if not os.path.isdir(absolute):
                return {"error": f"`{_visible_path(normalized)}` is not a directory."}
            safe_max = _safe_limit(limit, default=50, maximum=100)
            items = []
            hidden_protected = 0
            with os.scandir(absolute) as entries:
                sorted_entries = sorted(entries, key=lambda entry: (not entry.is_dir(follow_symlinks=False), entry.name.casefold()))
            total_entries = 0
            for entry in sorted_entries:
                if entry.name == SYSTEM_DIRECTORY_NAME or entry.is_symlink():
                    continue
                total_entries += 1
                relative = normalize_relative_path(os.path.join(normalized, entry.name))
                if Path(entry.name).name.casefold() in SENSITIVE_CONTEXT_NAMES:
                    hidden_protected += 1
                    continue
                if len(items) >= safe_max:
                    continue
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_directory = entry.is_dir(follow_symlinks=False)
                items.append({
                    "name": entry.name,
                    "path": _visible_path(relative),
                    "type": "folder" if is_directory else "file",
                    "size_bytes": None if is_directory else stat_result.st_size,
                    "size": None if is_directory else format_bytes(stat_result.st_size),
                    "modified": _modified_label(stat_result.st_mtime),
                })
            return {
                "scope": _visible_path(normalized),
                "items": items,
                "total_direct_items": total_entries,
                "returned_items": len(items),
                "hidden_protected_items": hidden_protected,
                "truncated": total_entries - hidden_protected > len(items),
            }
        except Exception as exc:
            return _tool_error(exc)

    def inspect_path(path: str) -> dict:
        """Inspect one exact file or folder and return verified metadata and folder totals.

        Args:
            path: Exact visible workspace path to inspect.
        """
        try:
            normalized, absolute = _resolve_workspace_path(username, path)
            if not os.path.exists(absolute):
                raise FileNotFoundError
            stat_result = os.stat(absolute, follow_symlinks=False)
            if os.path.isfile(absolute):
                return {
                    "path": _visible_path(normalized),
                    "type": "file",
                    "extension": Path(absolute).suffix.lower() or "none",
                    "size_bytes": stat_result.st_size,
                    "size": format_bytes(stat_result.st_size),
                    "modified": _modified_label(stat_result.st_mtime),
                    "text_readable": is_ai_readable_file(normalized),
                }
            if not os.path.isdir(absolute):
                return {"error": "The requested item is not a regular file or directory."}
            sizes, file_counts, scan = _directory_totals(absolute)
            direct_files = 0
            direct_folders = 0
            with os.scandir(absolute) as entries:
                for entry in entries:
                    if entry.name == SYSTEM_DIRECTORY_NAME or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        direct_folders += 1
                    elif entry.is_file(follow_symlinks=False):
                        direct_files += 1
            return {
                "path": _visible_path(normalized),
                "type": "folder",
                "direct_files": direct_files,
                "direct_folders": direct_folders,
                "recursive_files": file_counts.get(absolute, 0),
                "recursive_size_bytes": sizes.get(absolute, 0),
                "recursive_size": format_bytes(sizes.get(absolute, 0)),
                "modified": _modified_label(stat_result.st_mtime),
                **scan,
            }
        except Exception as exc:
            return _tool_error(exc)

    def largest_files(path: str = "", limit: int = 10) -> dict:
        """Find the largest files recursively inside a workspace folder.

        Args:
            path: Folder to scan. Use an empty string for the whole workspace.
            limit: Number of largest files to return, from 1 to 50.
        """
        try:
            normalized, absolute = _resolve_workspace_path(username, path)
            if not os.path.isdir(absolute):
                return {"error": f"`{_visible_path(normalized)}` is not a directory."}
            safe_max = _safe_limit(limit, default=10)
            largest: list[tuple[int, float, str]] = []
            scanned_files = 0
            scanned_bytes = 0
            protected_files = 0
            protected_bytes = 0
            truncated = False

            for root, directories, files in os.walk(absolute, topdown=True):
                directories[:] = sorted(
                    directory
                    for directory in directories
                    if directory != SYSTEM_DIRECTORY_NAME and not os.path.islink(os.path.join(root, directory))
                )
                for filename in files:
                    file_path = os.path.join(root, filename)
                    if os.path.islink(file_path):
                        continue
                    try:
                        stat_result = os.stat(file_path, follow_symlinks=False)
                    except OSError:
                        continue
                    scanned_files += 1
                    scanned_bytes += stat_result.st_size
                    relative = _relative_from_absolute(file_path)
                    if Path(filename).name.casefold() in SENSITIVE_CONTEXT_NAMES:
                        protected_files += 1
                        protected_bytes += stat_result.st_size
                    else:
                        candidate = (stat_result.st_size, stat_result.st_mtime, relative)
                        if len(largest) < safe_max:
                            heapq.heappush(largest, candidate)
                        elif candidate > largest[0]:
                            heapq.heapreplace(largest, candidate)
                    if scanned_files >= MAX_TOOL_SCAN_FILES:
                        truncated = True
                        break
                if truncated:
                    break

            results = [
                {
                    "path": _visible_path(relative),
                    "size_bytes": size,
                    "size": format_bytes(size),
                    "modified": _modified_label(modified),
                }
                for size, modified, relative in sorted(largest, reverse=True)
            ]
            return {
                "scope": _visible_path(normalized),
                "largest_files": results,
                "scanned_files": scanned_files,
                "scanned_size_bytes": scanned_bytes,
                "scanned_size": format_bytes(scanned_bytes),
                "protected_files_excluded": protected_files,
                "protected_size_excluded": format_bytes(protected_bytes),
                "scan_truncated": truncated,
            }
        except Exception as exc:
            return _tool_error(exc)

    def largest_folders(path: str = "", limit: int = 10) -> dict:
        """Find the largest subfolders recursively inside a workspace folder.

        Args:
            path: Folder to scan. Use an empty string for the whole workspace.
            limit: Number of largest folders to return, from 1 to 50.
        """
        try:
            normalized, absolute = _resolve_workspace_path(username, path)
            if not os.path.isdir(absolute):
                return {"error": f"`{_visible_path(normalized)}` is not a directory."}
            safe_max = _safe_limit(limit, default=10)
            sizes, file_counts, scan = _directory_totals(absolute)
            folders = []
            for folder_path, size in sizes.items():
                if folder_path == absolute:
                    continue
                folders.append({
                    "path": _visible_path(_relative_from_absolute(folder_path)),
                    "size_bytes": size,
                    "size": format_bytes(size),
                    "files": file_counts.get(folder_path, 0),
                })
            folders.sort(key=lambda item: (item["size_bytes"], item["path"]), reverse=True)
            return {
                "scope": _visible_path(normalized),
                "largest_folders": folders[:safe_max],
                "total_subfolders": len(folders),
                **scan,
            }
        except Exception as exc:
            return _tool_error(exc)

    def search_workspace(query: str, path: str = "", limit: int = 30) -> dict:
        """Search file and folder names recursively, without reading file contents.

        Args:
            query: Case-insensitive text to match in names or visible paths.
            path: Folder to search. Use an empty string for the whole workspace.
            limit: Maximum number of matches to return, from 1 to 50.
        """
        try:
            search_term = str(query or "").strip().casefold()
            if not search_term:
                return {"error": "A non-empty search query is required."}
            normalized, absolute = _resolve_workspace_path(username, path)
            if not os.path.isdir(absolute):
                return {"error": f"`{_visible_path(normalized)}` is not a directory."}
            safe_max = _safe_limit(limit, default=30)
            matches = []
            scanned_items = 0
            for root, directories, files in os.walk(absolute, topdown=True):
                directories[:] = sorted(
                    directory
                    for directory in directories
                    if directory != SYSTEM_DIRECTORY_NAME and not os.path.islink(os.path.join(root, directory))
                )
                for name, item_type in [(name, "folder") for name in directories] + [(name, "file") for name in files]:
                    item_path = os.path.join(root, name)
                    if os.path.islink(item_path) or Path(name).name.casefold() in SENSITIVE_CONTEXT_NAMES:
                        continue
                    scanned_items += 1
                    relative = _relative_from_absolute(item_path)
                    if search_term not in name.casefold() and search_term not in relative.casefold():
                        continue
                    try:
                        stat_result = os.stat(item_path, follow_symlinks=False)
                    except OSError:
                        continue
                    matches.append({
                        "path": _visible_path(relative),
                        "type": item_type,
                        "size_bytes": stat_result.st_size if item_type == "file" else None,
                        "size": format_bytes(stat_result.st_size) if item_type == "file" else None,
                        "modified": _modified_label(stat_result.st_mtime),
                    })
                    if len(matches) >= safe_max:
                        break
                if len(matches) >= safe_max or scanned_items >= MAX_TOOL_SCAN_FILES:
                    break
            return {
                "scope": _visible_path(normalized),
                "query": query,
                "matches": matches,
                "returned_matches": len(matches),
                "scan_truncated": len(matches) >= safe_max or scanned_items >= MAX_TOOL_SCAN_FILES,
            }
        except Exception as exc:
            return _tool_error(exc)

    def read_text_file(path: str, start_line: int = 1, max_lines: int = 160) -> dict:
        """Read a bounded line range from a safe text, code, or data file.

        Args:
            path: Exact visible path of the text file to read.
            start_line: First one-based line number to return.
            max_lines: Maximum lines to return, from 1 to 240.
        """
        try:
            normalized, absolute = _resolve_workspace_path(username, path)
            if not os.path.isfile(absolute):
                return {"error": f"`{_visible_path(normalized)}` is not a file."}
            if not is_ai_readable_file(normalized):
                return {"error": "This file type is not allowed for assistant text reading."}
            try:
                first_line = max(1, int(start_line))
            except (TypeError, ValueError):
                first_line = 1
            line_limit = _safe_limit(max_lines, default=160, maximum=MAX_TOOL_LINES)
            collected = []
            total_chars = 0
            last_line = first_line - 1
            truncated = False
            with open(absolute, "r", encoding="utf-8", errors="replace") as file:
                for line_number, line in enumerate(file, start=1):
                    if line_number < first_line:
                        continue
                    if len(collected) >= line_limit or total_chars + len(line) > MAX_TOOL_TEXT_CHARS:
                        truncated = True
                        break
                    collected.append(line.rstrip("\n"))
                    total_chars += len(line)
                    last_line = line_number
            return {
                "path": _visible_path(normalized),
                "start_line": first_line,
                "end_line": last_line,
                "content": "\n".join(collected),
                "truncated": truncated,
                "next_start_line": last_line + 1 if truncated else None,
            }
        except Exception as exc:
            return _tool_error(exc)

    return [
        workspace_summary,
        list_directory,
        inspect_path,
        largest_files,
        largest_folders,
        search_workspace,
        read_text_file,
    ]


def build_ai_context(
    username: str | None,
    include_tree: bool = True,
    file_path: str = "",
    detail_level: str = "balanced",
    response_style: str = "practical",
) -> str:
    parts = [
        ASSISTANT_CONTRACT,
        "\nCurrent response preferences:",
        f"- Detail level: {detail_level}",
        f"- Response style: {response_style}",
    ]

    if include_tree:
        parts.append("\nCurrent workspace tree (names and paths only; not file contents):\n" + _workspace_tree(username))

    file_context = _read_text_file_for_ai(username, file_path)
    if file_context:
        parts.append("\n" + file_context)

    return "\n".join(parts)


def _with_context(message: str, extra_context: str = "") -> str:
    if not extra_context:
        return message
    return f"{extra_context}\n\nUser request:\n{message}"


def ask_text(
    history: list[dict],
    message: str,
    username: str | None = None,
    extra_context: str = "",
) -> tuple[str, list[dict]]:
    client = get_client()
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": _with_context(message, extra_context)})

    try:
        from google.genai import types

        response = client.models.generate_content(
            model=current_app.config["GEMINI_MODEL"],
            contents=_api_history(list(updated_history)),
            config=types.GenerateContentConfig(
                temperature=0.2,
                tools=build_workspace_tools(username),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=8,
                ),
            ),
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing Gemini SDK. Run: pip install -U google-genai") from exc

    text = _response_text(response)
    updated_history.append({"role": "model", "parts": text})
    return text, list(updated_history)


def ask_image(history: list[dict], image_bytes: bytes, prompt: str, label: str = "", extra_context: str = "") -> tuple[str, list[dict]]:
    client = get_client()
    image = Image.open(io.BytesIO(image_bytes))
    image.load()

    recent_context = "\n\n".join(_message_text(item) for item in history[-8:] if _message_text(item).strip())
    prompt_text = prompt or "Describe this image."
    context_parts = []
    if extra_context:
        context_parts.append(extra_context)
    if recent_context:
        context_parts.append("Recent chat context:\n" + recent_context)
    context_parts.append("User image prompt:\n" + prompt_text)
    if label:
        context_parts.append("User text sent with image:\n" + label)
    prompt_text = "\n\n".join(context_parts)

    response = client.models.generate_content(
        model=current_app.config["GEMINI_MODEL"],
        contents=[prompt_text, image],
    )

    text = _response_text(response)
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": "Image sent: " + label})
    updated_history.append({"role": "model", "parts": text})
    return text, list(updated_history)
