import os
from pathlib import Path


def _load_local_env(path: str = ".env") -> None:
    """Load simple KEY=VALUE entries without requiring python-dotenv."""
    env_path = Path(path)
    if not env_path.is_file():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_local_env()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-key")
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 5 * 1024 * 1024 * 1024))
    USER_DATA_FILE = os.environ.get("USER_DATA_FILE", "users.json")
    FEEDBACK_FILE = os.environ.get("FEEDBACK_FILE", "feedback.json")
    SERVER_LOG_FILE = os.environ.get("SERVER_LOG_FILE", "server.log")
    DATA_TRANSFER_LOG = os.environ.get("DATA_TRANSFER_LOG", "data_transfer.log")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
    GEMINI_CONFIG_PATH = os.environ.get("GEMINI_CONFIG_PATH", os.path.join("uploads", "Admin", "config.txt"))
    SHARE_DATA_FILE = os.environ.get("SHARE_DATA_FILE", "shares.json")
    SHARE_AUDIT_FILE = os.environ.get("SHARE_AUDIT_FILE", "share_audit.json")
    ACTIVITY_FILE = os.environ.get("ACTIVITY_FILE", "activity_log.json")
    TRASH_FILE = os.environ.get("TRASH_FILE", "trash_index.json")
    VERSIONS_FILE = os.environ.get("VERSIONS_FILE", "versions.json")
    METADATA_FILE = os.environ.get("METADATA_FILE", "file_metadata.json")
    NOTIFICATIONS_FILE = os.environ.get("NOTIFICATIONS_FILE", "notifications.json")
    FILE_REQUESTS_FILE = os.environ.get("FILE_REQUESTS_FILE", "file_requests.json")
    DEFAULT_USER_QUOTA_BYTES = int(os.environ.get("DEFAULT_USER_QUOTA_BYTES", 5 * 1024 * 1024 * 1024))
    AC_CONTROL_FILE = os.environ.get("AC_CONTROL_FILE", "ac_control.json")
    AC_STATISTICS_FILE = os.environ.get("AC_STATISTICS_FILE", "ac_statistics.json")
    AIRCON_PORTAL_PASSWORD = os.environ.get("AIRCON_PORTAL_PASSWORD", "")
    AIRCON_SESSION_HOURS = int(os.environ.get("AIRCON_SESSION_HOURS", 24))
    VOICE_PLAYBACK_COMMAND = os.environ.get(
        "VOICE_PLAYBACK_COMMAND",
        "ffplay -nodisp -autoexit -loglevel error -i pipe:0",
    )
    VOICE_CAPTURE_COMMAND = os.environ.get(
        "VOICE_CAPTURE_COMMAND",
        "ffmpeg -hide_banner -loglevel error -f pulse -i default -ac 1 -ar 48000 -c:a libopus -b:a 48k -f webm pipe:1",
    )
    TLS_CERT_FILE = os.environ.get("TLS_CERT_FILE", "")
    TLS_KEY_FILE = os.environ.get("TLS_KEY_FILE", "")
