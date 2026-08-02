from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from lib.storage import format_bytes

_SERVER_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\s+"
    r"(?P<level>[A-Z]+)\s+(?P<logger>\S+)\s+(?P<thread>.*?)\s+:\s?(?P<message>.*)$"
)
_TRANSFER_REQUEST_RE = re.compile(
    r"^Incoming Request - Path: (?P<path>.*), Method: (?P<method>[A-Z]+), Data Size: (?P<size>\d+) bytes$"
)
_TRANSFER_RESPONSE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?):\s+"
    r"(?P<ip>.*?)\s+-\s+(?P<size>\d+) bytes transferred\.$"
)
_WERKZEUG_REQUEST_RE = re.compile(
    r'^\s*(?P<ip>\S+) - - \[(?P<when>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[0-9.]+" '
    r'(?P<status>\d{3}) (?P<size>\S+)\s*$'
)
_AUDIT_REQUEST_RE = re.compile(
    r"^(?P<kind>HTTP response|Unhandled request exception) "
    r"request_id=(?P<request_id>\S+) user=(?P<user>\S+) ip=(?P<ip>\S+) "
    r"method=(?P<method>[A-Z]+) path=(?P<path>\S+)"
    r"(?: status=(?P<status>\d{3}) duration_ms=(?P<duration>[0-9.]+))?$"
)
_LEVEL_ORDER = {"CRITICAL": 5, "ERROR": 4, "WARNING": 3, "INFO": 2, "DEBUG": 1, "UNKNOWN": 0}


class LogAccessError(RuntimeError):
    pass


def _resolved_path(path_value: str) -> Path:
    raw = str(path_value or "").strip()
    if not raw:
        raise LogAccessError("No log file path is configured.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


def read_recent_lines(path_value: str, *, max_lines: int = 3000, max_bytes: int = 2_000_000) -> dict[str, Any]:
    path = _resolved_path(path_value)
    if not path.exists():
        raise LogAccessError(f"Log file not found: {path.name}")
    if not path.is_file():
        raise LogAccessError(f"Configured log path is not a file: {path.name}")
    if not os.access(path, os.R_OK):
        raise LogAccessError(f"The server cannot read {path.name}. Check file permissions.")

    try:
        stat_result = path.stat()
        with path.open("rb") as file:
            start = max(0, stat_result.st_size - max_bytes)
            file.seek(start)
            chunk = file.read(max_bytes)
    except OSError as exc:
        raise LogAccessError(f"Could not read {path.name}: {exc.strerror or exc}") from exc

    text = chunk.decode("utf-8", errors="replace")
    if start:
        newline = text.find("\n")
        text = text[newline + 1 :] if newline >= 0 else ""
    lines = text.splitlines()
    truncated_by_lines = len(lines) > max_lines
    if truncated_by_lines:
        lines = lines[-max_lines:]

    return {
        "path": str(path),
        "filename": path.name,
        "lines": lines,
        "file_size_bytes": stat_result.st_size,
        "file_size": format_bytes(stat_result.st_size),
        "modified_at": datetime.fromtimestamp(stat_result.st_mtime).astimezone(),
        "window_truncated": bool(start or truncated_by_lines),
        "loaded_lines": len(lines),
    }


def clear_log_file(path_value: str) -> None:
    path = _resolved_path(path_value)
    if path.exists() and not path.is_file():
        raise LogAccessError(f"Configured log path is not a file: {path.name}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except OSError as exc:
        raise LogAccessError(f"Could not clear {path.name}: {exc.strerror or exc}") from exc


def _display_timestamp(raw: str) -> str:
    for pattern in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).strftime("%d %b %Y, %H:%M:%S")
        except ValueError:
            continue
    return raw


def parse_server_entries(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in lines:
        match = _SERVER_LINE_RE.match(line)
        if match:
            level = match.group("level").upper()
            message = match.group("message") or "(empty message)"
            audit_match = _AUDIT_REQUEST_RE.match(message)
            request_match = _WERKZEUG_REQUEST_RE.match(message) if match.group("logger") == "werkzeug" else None
            request_method = ""
            request_path = ""
            status_code = 0
            client_ip = ""
            request_id = ""
            audit_user = ""
            duration_ms = ""
            if audit_match:
                request_method = audit_match.group("method")
                request_path = audit_match.group("path")
                status_code = int(audit_match.group("status") or 500)
                client_ip = audit_match.group("ip")
                request_id = audit_match.group("request_id")
                audit_user = audit_match.group("user")
                duration_ms = audit_match.group("duration") or ""
                if status_code >= 500:
                    level = "ERROR"
                elif status_code >= 400:
                    level = "WARNING"
                message = f"{request_method} {request_path}"
            elif request_match:
                request_method = request_match.group("method")
                request_path = request_match.group("path")
                status_code = int(request_match.group("status"))
                client_ip = request_match.group("ip")
                if status_code >= 500:
                    level = "ERROR"
                elif status_code >= 400:
                    level = "WARNING"
                message = f"{request_method} {request_path}"
            entries.append(
                {
                    "kind": "server",
                    "timestamp": match.group("timestamp"),
                    "timestamp_label": _display_timestamp(match.group("timestamp")),
                    "level": level if level in _LEVEL_ORDER else "UNKNOWN",
                    "logger": match.group("logger"),
                    "thread": match.group("thread"),
                    "message": message,
                    "details": "",
                    "request_method": request_method,
                    "request_path": request_path,
                    "status_code": status_code,
                    "client_ip": client_ip,
                    "request_id": request_id,
                    "audit_user": audit_user,
                    "duration_ms": duration_ms,
                }
            )
            continue

        if entries:
            previous = entries[-1]
            previous["details"] = (previous["details"] + "\n" + line).strip("\n")
        elif line.strip():
            entries.append(
                {
                    "kind": "server",
                    "timestamp": "",
                    "timestamp_label": "Unknown time",
                    "level": "UNKNOWN",
                    "logger": "legacy",
                    "thread": "",
                    "message": line,
                    "details": "",
                    "request_method": "",
                    "request_path": "",
                    "status_code": 0,
                    "client_ip": "",
                    "request_id": "",
                    "audit_user": "",
                    "duration_ms": "",
                }
            )
    return entries


def parse_transfer_entries(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in lines:
        request_match = _TRANSFER_REQUEST_RE.match(line)
        if request_match:
            size = int(request_match.group("size"))
            entries.append(
                {
                    "kind": "request",
                    "timestamp": "",
                    "timestamp_label": "Request received",
                    "method": request_match.group("method"),
                    "path": request_match.group("path") or "/",
                    "ip": "",
                    "size_bytes": size,
                    "size": format_bytes(size),
                    "message": f"{request_match.group('method')} {request_match.group('path') or '/'}",
                    "details": "",
                }
            )
            continue

        response_match = _TRANSFER_RESPONSE_RE.match(line)
        if response_match:
            size = int(response_match.group("size"))
            entries.append(
                {
                    "kind": "response",
                    "timestamp": response_match.group("timestamp"),
                    "timestamp_label": _display_timestamp(response_match.group("timestamp")),
                    "method": "",
                    "path": "",
                    "ip": response_match.group("ip"),
                    "size_bytes": size,
                    "size": format_bytes(size),
                    "message": f"Response sent to {response_match.group('ip')}",
                    "details": "",
                }
            )
            continue

        if line.strip():
            entries.append(
                {
                    "kind": "unknown",
                    "timestamp": "",
                    "timestamp_label": "Unknown time",
                    "method": "",
                    "path": "",
                    "ip": "",
                    "size_bytes": 0,
                    "size": "0 B",
                    "message": line,
                    "details": "",
                }
            )
    return entries


def filter_entries(
    entries: list[dict[str, Any]],
    *,
    query: str = "",
    category: str = "all",
    limit: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_query = str(query or "").strip().casefold()
    normalized_category = str(category or "all").strip().lower()
    try:
        safe_limit = max(25, min(500, int(limit)))
    except (TypeError, ValueError):
        safe_limit = 200

    filtered: list[dict[str, Any]] = []
    for item in reversed(entries):
        item_category = str(item.get("level") or item.get("kind") or "unknown").lower()
        if normalized_category != "all" and item_category != normalized_category:
            continue
        haystack = " ".join(str(value or "") for value in item.values()).casefold()
        if normalized_query and normalized_query not in haystack:
            continue
        filtered.append(item)
        if len(filtered) >= safe_limit:
            break

    server_counts = {level.lower(): 0 for level in _LEVEL_ORDER}
    transfer_counts = {"request": 0, "response": 0, "unknown": 0}
    total_bytes = 0
    for item in entries:
        level = str(item.get("level") or "").lower()
        kind = str(item.get("kind") or "").lower()
        if level in server_counts:
            server_counts[level] += 1
        if kind in transfer_counts:
            transfer_counts[kind] += 1
        total_bytes += int(item.get("size_bytes") or 0)

    return filtered, {
        "matched": len(filtered),
        "available": len(entries),
        "limit": safe_limit,
        "server_counts": server_counts,
        "transfer_counts": transfer_counts,
        "transfer_bytes": total_bytes,
        "transfer_size": format_bytes(total_bytes),
    }
