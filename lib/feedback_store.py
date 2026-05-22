import uuid
from datetime import datetime
from flask import current_app
from lib.json_store import read_json, write_json


def feedback_file() -> str:
    return current_app.config["FEEDBACK_FILE"]


def load_feedback() -> list:
    feedback = read_json(feedback_file(), [])
    return feedback if isinstance(feedback, list) else []


def save_feedback(feedback: list) -> None:
    write_json(feedback_file(), feedback)


def add_feedback(username: str, feedback_type: str, message: str, rating: str | None) -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "username": username or "Anonymous",
        "feedback_type": feedback_type,
        "message": message,
        "rating": rating,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "read": False,
    }
    feedback = load_feedback()
    feedback.append(item)
    save_feedback(feedback)
    return item


def mark_all_as_read() -> list:
    feedback = load_feedback()
    for item in feedback:
        item["read"] = True
    save_feedback(feedback)
    return feedback


def delete_feedback(feedback_id: str) -> None:
    feedback = [item for item in load_feedback() if item.get("id") != feedback_id]
    save_feedback(feedback)
