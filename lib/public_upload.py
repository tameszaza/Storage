"""Write files received through public links without overwriting existing data."""

from __future__ import annotations

import os

from lib.storage import safe_relative_upload_name, upload_root


class UploadLimitExceeded(ValueError):
    pass


def save_public_upload(stream, destination_folder: str, filename: str, *, max_bytes: int | None = None) -> tuple[str, int]:
    """Save under a folder, rejecting symlink traversal and choosing a new name on collision."""
    root = os.path.realpath(destination_folder)
    uploads = os.path.realpath(upload_root())
    if os.path.commonpath((uploads, root)) != uploads or not os.path.isdir(root):
        raise PermissionError("The upload destination is unavailable.")

    parts = safe_relative_upload_name(filename).split("/")
    folder_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    folder_fd = os.open(root, folder_flags)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=folder_fd)
            except FileExistsError:
                pass
            child_fd = os.open(part, folder_flags, dir_fd=folder_fd)
            os.close(folder_fd)
            folder_fd = child_fd

        leaf = parts[-1]
        stem, extension = os.path.splitext(leaf)
        for copy_number in range(1, 1001):
            saved_name = leaf if copy_number == 1 else f"{stem} ({copy_number}){extension}"
            try:
                file_fd = os.open(
                    saved_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o644,
                    dir_fd=folder_fd,
                )
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError("Too many files with this name already exist.")

        size = 0
        try:
            with os.fdopen(file_fd, "wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise UploadLimitExceeded("File exceeds this request's size limit.")
                    output.write(chunk)
        except Exception:
            os.unlink(saved_name, dir_fd=folder_fd)
            raise
        return "/".join([*parts[:-1], saved_name]), size
    finally:
        os.close(folder_fd)
