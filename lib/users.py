import os
from flask import current_app
from lib.json_store import read_json, write_json


def users_file() -> str:
    return current_app.config["USER_DATA_FILE"]


def load_users() -> dict:
    users = read_json(users_file(), {})
    return users if isinstance(users, dict) else {}


def save_users(users: dict) -> None:
    write_json(users_file(), users)


def ensure_user_folder(username: str) -> str:
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], username)
    os.makedirs(folder, exist_ok=True)
    return folder


def count_user_folders() -> int:
    uploads = current_app.config["UPLOAD_FOLDER"]
    if not os.path.exists(uploads):
        return 0
    return len([name for name in os.listdir(uploads) if os.path.isdir(os.path.join(uploads, name))])
