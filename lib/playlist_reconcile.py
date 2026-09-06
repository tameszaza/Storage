from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from lib.lyrics import AUDIO_SUFFIXES, LYRIC_SUFFIXES


class SnapshotError(ValueError):
    pass


_UNAVAILABLE_VIDEO_ERROR = re.compile(
    r"ERROR:\s+\[youtube\]\s+([^:]+):\s+"
    r"(?:Video unavailable\b|Private video\b|This video is private\b)"
)


def unavailable_video_id(message: str) -> str | None:
    """Return an ID only for an explicit YouTube unavailable/private error."""
    match = _UNAVAILABLE_VIDEO_ERROR.search(message)
    return match.group(1).strip() if match else None


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def parse_playlist_snapshot(output: str) -> tuple[list[str], str]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"Remote playlist returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise SnapshotError("The remote URL did not return a complete playlist snapshot.")

    entries = payload["entries"]
    identifiers: list[str] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise SnapshotError(f"Playlist entry {position} was unavailable; deletion was cancelled.")
        identifier = str(entry.get("id") or entry.get("url") or "").strip()
        if not identifier:
            raise SnapshotError(f"Playlist entry {position} had no stable ID; deletion was cancelled.")
        identifiers.append(identifier)

    for count_key in ("playlist_count", "n_entries", "entry_count"):
        expected = payload.get(count_key)
        if isinstance(expected, int) and expected > len(entries):
            raise SnapshotError(
                f"Only {len(entries)} of {expected} playlist entries were returned; deletion was cancelled."
            )

    title = str(payload.get("title") or payload.get("playlist_title") or "Remote playlist").strip()
    return identifiers, title[:200]


def record_download_report(report_path: Path, manifest_path: Path, managed_dir: Path) -> int:
    manifest = _load_json(manifest_path, {"files": {}})
    if not isinstance(manifest, dict):
        manifest = {"files": {}}
    files = manifest.setdefault("files", {})
    if not isinstance(files, dict):
        files = {}
        manifest["files"] = files

    managed_root = managed_dir.resolve()
    recorded = 0
    try:
        lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        identifier, separator, raw_path = line.partition("\t")
        identifier = identifier.strip()
        if not separator or not identifier or not raw_path.strip():
            continue
        candidate = Path(raw_path.strip()).resolve()
        if not candidate.is_relative_to(managed_root):
            continue
        existing = files.setdefault(identifier, [])
        if not isinstance(existing, list):
            existing = []
            files[identifier] = existing
        value = str(candidate)
        if value not in existing:
            existing.append(value)
            recorded += 1
    _atomic_json(manifest_path, manifest)
    return recorded


def _remove_archive_ids(archive_path: Path, removed_ids: set[str]) -> None:
    if not archive_path.exists() or not removed_ids:
        return
    try:
        lines = archive_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    kept = [line for line in lines if not line.strip() or line.rsplit(maxsplit=1)[-1] not in removed_ids]
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(archive_path.suffix + ".new")
    temporary.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    os.replace(temporary, archive_path)


def remove_archive_ids(archive_path: Path, removed_ids: set[str]) -> None:
    _remove_archive_ids(archive_path, removed_ids)


def _delete_managed_files(
    removed_ids: set[str], manifest_path: Path, managed_dir: Path, archive_path: Path
) -> int:
    managed_root = managed_dir.resolve()
    manifest = _load_json(manifest_path, {"files": {}})
    if not isinstance(manifest, dict):
        manifest = {"files": {}}
    files = manifest.get("files")
    if not isinstance(files, dict):
        files = {}
        manifest["files"] = files

    candidates: set[Path] = set()
    for identifier in removed_ids:
        paths = files.get(identifier, [])
        if isinstance(paths, list):
            candidates.update(Path(value) for value in paths if isinstance(value, str))

    if managed_dir.is_dir():
        markers = tuple(f"[{identifier}]" for identifier in removed_ids)
        for path in managed_dir.rglob("*"):
            if path.is_file() and any(marker in path.name for marker in markers):
                candidates.add(path)

    deleted = 0
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(managed_root):
            continue
        related = [resolved]
        if resolved.suffix.lower() in AUDIO_SUFFIXES:
            related.extend(resolved.with_suffix(suffix) for suffix in LYRIC_SUFFIXES)
        for managed_file in related:
            if not managed_file.resolve().is_relative_to(managed_root):
                continue
            try:
                managed_file.unlink()
                deleted += 1
            except FileNotFoundError:
                pass

    for identifier in removed_ids:
        files.pop(identifier, None)
    _atomic_json(manifest_path, manifest)
    _remove_archive_ids(archive_path, removed_ids)

    if managed_dir.is_dir():
        directories = sorted(
            (path for path in managed_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass
    return deleted


def write_m3u_playlist(
    ordered_ids: list[str], manifest_path: Path, managed_dir: Path, music_root: Path, playlist_path: Path
) -> tuple[int, list[str]]:
    manifest = _load_json(manifest_path, {"files": {}})
    files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    if not isinstance(files, dict):
        files = {}
    managed_root = managed_dir.resolve()
    music_root = music_root.resolve()
    discovered: dict[str, Path] = {}
    if managed_dir.is_dir():
        for candidate in managed_dir.rglob("*"):
            if not candidate.is_file():
                continue
            for identifier in set(ordered_ids):
                if f"[{identifier}]" in candidate.name:
                    discovered.setdefault(identifier, candidate)

    lines = ["#EXTM3U"]
    missing: list[str] = []
    for identifier in ordered_ids:
        selected: Path | None = None
        values = files.get(identifier, [])
        if isinstance(values, list):
            for value in values:
                if not isinstance(value, str):
                    continue
                candidate = Path(value).resolve()
                if candidate.is_file() and candidate.is_relative_to(managed_root):
                    selected = candidate
                    break
        if selected is None:
            selected = discovered.get(identifier)
        if selected is None:
            missing.append(identifier)
            continue
        # M3U relative paths are resolved from the playlist file's directory,
        # not from Navidrome's music-library root.
        relative = os.path.relpath(selected, playlist_path.parent).replace(os.sep, "/")
        lines.append(relative)

    if missing:
        return len(lines) - 1, missing

    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{playlist_path.name}-", dir=playlist_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, playlist_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(lines) - 1, missing


def reconcile_snapshot(
    current_ids: list[str] | set[str],
    state_path: Path,
    manifest_path: Path,
    managed_dir: Path,
    archive_path: Path,
    *,
    now: float | None = None,
    confirmation_seconds: int = 300,
) -> dict:
    current_order = [str(value) for value in current_ids if str(value)]
    current_set = set(current_order)
    current_time = float(time.time() if now is None else now)
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}

    if not state.get("initialized"):
        state.update(
            initialized=True,
            confirmed_ids=sorted(current_set),
            confirmed_order=current_order,
            pending=None,
        )
        _atomic_json(state_path, state)
        return {
            "initialized": True,
            "pending_removals": 0,
            "observations": 0,
            "required": 0,
            "wait_seconds": 0,
            "deleted_ids": [],
            "deleted_files": 0,
            "effective_order": current_order,
        }

    confirmed = {str(value) for value in state.get("confirmed_ids", []) if str(value)}
    confirmed_order = [str(value) for value in state.get("confirmed_order", []) if str(value)]
    if not confirmed_order:
        confirmed_order = sorted(confirmed)
    missing = confirmed - current_set
    if not missing:
        state.update(confirmed_ids=sorted(current_set), confirmed_order=current_order, pending=None)
        _atomic_json(state_path, state)
        return {
            "initialized": False,
            "pending_removals": 0,
            "observations": 0,
            "required": 0,
            "wait_seconds": 0,
            "deleted_ids": [],
            "deleted_files": 0,
            "effective_order": current_order,
        }

    drop_ratio = len(missing) / max(1, len(confirmed))
    required = 3 if not current_set or drop_ratio >= 0.5 else 2
    signature = hashlib.sha256("\n".join(sorted(current_set)).encode("utf-8")).hexdigest()
    pending = state.get("pending")
    if not isinstance(pending, dict) or pending.get("signature") != signature:
        pending = {
            "signature": signature,
            "ids": sorted(current_set),
            "first_seen_at": current_time,
            "last_observed_at": current_time,
            "observations": 1,
            "required": required,
        }
    else:
        pending["required"] = max(required, int(pending.get("required") or 0))
        elapsed = current_time - float(pending.get("last_observed_at") or 0)
        if elapsed >= max(1, confirmation_seconds):
            pending["observations"] = int(pending.get("observations") or 1) + 1
            pending["last_observed_at"] = current_time

    observations = int(pending.get("observations") or 1)
    required = int(pending.get("required") or required)
    if observations >= required:
        deleted_files = _delete_managed_files(missing, manifest_path, managed_dir, archive_path)
        state.update(
            confirmed_ids=sorted(current_set),
            confirmed_order=current_order,
            pending=None,
        )
        _atomic_json(state_path, state)
        return {
            "initialized": False,
            "pending_removals": 0,
            "observations": observations,
            "required": required,
            "wait_seconds": 0,
            "deleted_ids": sorted(missing),
            "deleted_files": deleted_files,
            "effective_order": current_order,
        }

    retained_missing = [identifier for identifier in confirmed_order if identifier in missing]
    effective_order = current_order + [identifier for identifier in retained_missing if identifier not in current_set]
    state.update(
        confirmed_ids=sorted(confirmed | current_set),
        confirmed_order=effective_order,
        pending=pending,
    )
    _atomic_json(state_path, state)
    elapsed = current_time - float(pending.get("last_observed_at") or current_time)
    return {
        "initialized": False,
        "pending_removals": len(missing),
        "observations": observations,
        "required": required,
        "wait_seconds": max(0, int(confirmation_seconds - elapsed)),
        "deleted_ids": [],
        "deleted_files": 0,
        "effective_order": effective_order,
    }
