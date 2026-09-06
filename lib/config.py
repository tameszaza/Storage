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
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Singapore")
    PLANNER_DATA_FILE = os.environ.get("PLANNER_DATA_FILE", "planner.json")
    ICS_CALENDAR_CONFIG_PATH = os.environ.get(
        "ICS_CALENDAR_CONFIG_PATH", os.path.join("uploads", "Admin", "ics_calendar.json")
    )
    ICS_CALENDAR_CACHE_FILE = os.environ.get(
        "ICS_CALENDAR_CACHE_FILE", os.path.join("uploads", "Admin", "ics_calendar_cache.json")
    )
    SHARE_DATA_FILE = os.environ.get("SHARE_DATA_FILE", "shares.json")
    SHARE_AUDIT_FILE = os.environ.get("SHARE_AUDIT_FILE", "share_audit.json")
    ACTIVITY_FILE = os.environ.get("ACTIVITY_FILE", "activity_log.json")
    TRASH_FILE = os.environ.get("TRASH_FILE", "trash_index.json")
    VERSIONS_FILE = os.environ.get("VERSIONS_FILE", "versions.json")
    METADATA_FILE = os.environ.get("METADATA_FILE", "file_metadata.json")
    NOTIFICATIONS_FILE = os.environ.get("NOTIFICATIONS_FILE", "notifications.json")
    FILE_REQUESTS_FILE = os.environ.get("FILE_REQUESTS_FILE", "file_requests.json")
    PLAYLISTS_FILE = os.environ.get("PLAYLISTS_FILE", "playlists.json")
    PLAYLIST_LOG_DIR = os.environ.get("PLAYLIST_LOG_DIR", "playlist_logs")
    PLAYLIST_RUNTIME_DIR = os.environ.get("PLAYLIST_RUNTIME_DIR", "playlist_runtime")
    MUSIC_FOLDER = os.environ.get("MUSIC_FOLDER", "/srv/music")
    NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "http://172.17.0.1:4533").rstrip("/")
    NAVIDROME_USERNAME = os.environ.get("NAVIDROME_USERNAME", "")
    NAVIDROME_PASSWORD = os.environ.get("NAVIDROME_PASSWORD", "")
    NAVIDROME_DB_PATH = os.environ.get("NAVIDROME_DB_PATH", "/srv/navidrome-data/navidrome.db")
    PLAYLIST_WORKER_POLL_SECONDS = int(os.environ.get("PLAYLIST_WORKER_POLL_SECONDS", 10))
    PLAYLIST_DELETE_CONFIRM_SECONDS = int(os.environ.get("PLAYLIST_DELETE_CONFIRM_SECONDS", 300))
    PLAYLIST_SNAPSHOT_TIMEOUT_SECONDS = int(os.environ.get("PLAYLIST_SNAPSHOT_TIMEOUT_SECONDS", 900))
    LYRICS_ENABLED = os.environ.get("LYRICS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    LYRICS_API_URL = os.environ.get("LYRICS_API_URL", "https://lrclib.net").rstrip("/")
    LYRICS_PROVIDER_ORDER = os.environ.get("LYRICS_PROVIDER_ORDER", "lrclib,simpmusic,netease")
    LYRICS_SIMPMUSIC_API_URL = os.environ.get(
        "LYRICS_SIMPMUSIC_API_URL", "https://api-lyrics.simpmusic.org/v1"
    ).rstrip("/")
    LYRICS_NETEASE_API_URL = os.environ.get(
        "LYRICS_NETEASE_API_URL", "https://music.163.com/api"
    ).rstrip("/")
    LYRICS_REQUEST_DELAY_SECONDS = float(os.environ.get("LYRICS_REQUEST_DELAY_SECONDS", "0.35"))
    LYRICS_MISSING_RETRY_SECONDS = int(os.environ.get("LYRICS_MISSING_RETRY_SECONDS", 7 * 24 * 60 * 60))
    LYRICS_ERROR_RETRY_SECONDS = int(os.environ.get("LYRICS_ERROR_RETRY_SECONDS", 6 * 60 * 60))
    DISK_CHECK_SYSTEM_PATH = os.environ.get("DISK_CHECK_SYSTEM_PATH", "/srv/system-disk-check")
    DISK_CHECK_PRIMARY_PATH = os.environ.get("DISK_CHECK_PRIMARY_PATH", "/srv/tamestorage/uploads")
    DISK_CHECK_BACKUP_PATH = os.environ.get("DISK_CHECK_BACKUP_PATH", "/srv/tamestorage-backup")
    DEFAULT_USER_QUOTA_BYTES = int(os.environ.get("DEFAULT_USER_QUOTA_BYTES", 5 * 1024 * 1024 * 1024))
    TLS_CERT_FILE = os.environ.get("TLS_CERT_FILE", "")
    TLS_KEY_FILE = os.environ.get("TLS_KEY_FILE", "")
