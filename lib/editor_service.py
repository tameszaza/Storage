from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class EditorConflictError(RuntimeError):
    """Raised when a file changed after the editor loaded it."""


@dataclass(frozen=True)
class EditorDocument:
    content: str
    revision: str
    mtime_ns: int
    size_bytes: int


def _revision_for_bytes(raw_content: bytes) -> str:
    return hashlib.sha256(raw_content).hexdigest()


def load_document(path: str | os.PathLike[str]) -> EditorDocument:
    file_path = Path(path)
    raw_content = file_path.read_bytes()
    stat = file_path.stat()
    return EditorDocument(
        content=raw_content.decode("utf-8", errors="replace"),
        revision=_revision_for_bytes(raw_content),
        mtime_ns=stat.st_mtime_ns,
        size_bytes=stat.st_size,
    )


def save_document(
    path: str | os.PathLike[str],
    content: str,
    *,
    expected_revision: str | None = None,
    expected_mtime_ns: int | None = None,
) -> EditorDocument:
    file_path = Path(path)
    current_raw = file_path.read_bytes()
    current_stat = file_path.stat()
    current_revision = _revision_for_bytes(current_raw)

    if expected_revision and current_revision != expected_revision:
        raise EditorConflictError("This file changed on the server after you opened it.")
    if not expected_revision and expected_mtime_ns is not None and current_stat.st_mtime_ns != expected_mtime_ns:
        raise EditorConflictError("This file changed on the server after you opened it.")

    encoded_content = content.encode("utf-8")
    if current_raw == encoded_content:
        return EditorDocument(
            content=content,
            revision=current_revision,
            mtime_ns=current_stat.st_mtime_ns,
            size_bytes=current_stat.st_size,
        )

    file_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{file_path.name}.",
        suffix=".tmp",
        dir=str(file_path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded_content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary_path, current_stat.st_mode)
        except OSError:
            pass
        os.replace(temporary_path, file_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    stat = file_path.stat()
    return EditorDocument(
        content=content,
        revision=_revision_for_bytes(encoded_content),
        mtime_ns=stat.st_mtime_ns,
        size_bytes=stat.st_size,
    )
