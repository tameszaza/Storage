import os

from dotenv import load_dotenv


load_dotenv()


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
