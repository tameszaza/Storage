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
    PLANNER_DATA_FILE = os.environ.get("PLANNER_DATA_FILE", "planner.json")
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Singapore")
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
    DEFAULT_USER_QUOTA_BYTES = int(os.environ.get("DEFAULT_USER_QUOTA_BYTES", 5 * 1024 * 1024 * 1024))
    AC_CONTROL_FILE = os.environ.get("AC_CONTROL_FILE", "ac_control.json")
    AC_STATISTICS_FILE = os.environ.get("AC_STATISTICS_FILE", "ac_statistics.json")
    AC_HISTORY_FILE = os.environ.get("AC_HISTORY_FILE", "ac_history.json")
    AC_WEEKLY_FILE = os.environ.get("AC_WEEKLY_FILE", "ac_weekly.json")
    AC_RUNTIME_FILE = os.environ.get("AC_RUNTIME_FILE", "ac_runtime.json")
    AIRCON_RATE_PER_HOUR = float(os.environ.get("AIRCON_RATE_PER_HOUR", 0.39))
    AIRCON_PORTAL_PASSWORD = os.environ.get("AIRCON_PORTAL_PASSWORD", "")
    AIRCON_SESSION_HOURS = int(os.environ.get("AIRCON_SESSION_HOURS", 24))
    CAMERA_OPEN_WEBHOOK_URL = os.environ.get("CAMERA_OPEN_WEBHOOK_URL", "http://127.0.0.1:8080/web")
    CAMERA_OPEN_WEBHOOK_TIMEOUT_SECONDS = int(os.environ.get("CAMERA_OPEN_WEBHOOK_TIMEOUT_SECONDS", 5))
    INWORLD_API_KEY = os.environ.get("INWORLD_API_KEY", "")
    TTS_API_URL = os.environ.get("TTS_API_URL", "https://api.inworld.ai/tts/v1/voice")
    TTS_VOICE_ID = os.environ.get("TTS_VOICE_ID", "Sarah")
    TTS_MODEL_ID = os.environ.get("TTS_MODEL_ID", "inworld-tts-2")
    TTS_SPEAKING_RATE = float(os.environ.get("TTS_SPEAKING_RATE", 1.0))
    TTS_DELIVERY_MODE = os.environ.get("TTS_DELIVERY_MODE", "BALANCED")
    TTS_LANGUAGE = os.environ.get("TTS_LANGUAGE", "AUTO")
    TTS_MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", 1000))
    TTS_API_TIMEOUT_SECONDS = int(os.environ.get("TTS_API_TIMEOUT_SECONDS", 20))
    TLS_CERT_FILE = os.environ.get("TLS_CERT_FILE", "")
    TLS_KEY_FILE = os.environ.get("TLS_KEY_FILE", "")
