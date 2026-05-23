from __future__ import annotations

import os
import tempfile
import zipfile
from datetime import datetime
from flask import current_app
from lib.storage import upload_root

METADATA_FILES = [
    "users.json", "shares.json", "share_audit.json", "trash_index.json", "versions.json",
    "file_metadata.json", "activity_log.json", "notifications.json", "file_requests.json", "feedback.json",
]


def create_backup_zip() -> str:
    temp_dir = tempfile.mkdtemp(prefix="tamestorage_backup_")
    zip_path = os.path.join(temp_dir, f"tamestorage-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        uploads = upload_root()
        if os.path.exists(uploads):
            for root, dirs, files in os.walk(uploads):
                dirs[:] = [d for d in dirs if d != ".tamestorage_system"]
                for filename in files:
                    absolute = os.path.join(root, filename)
                    archive.write(absolute, os.path.join("uploads", os.path.relpath(absolute, uploads)))
        for filename in METADATA_FILES:
            path = current_app.config.get(filename.upper().replace('.', '_'), filename)
            if os.path.exists(path):
                archive.write(path, os.path.join("metadata", os.path.basename(path)))
    return zip_path
