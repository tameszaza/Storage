from __future__ import annotations

import calendar
import secrets
import threading
from datetime import date, datetime, time
from typing import Any

from flask import current_app

from lib.json_store import read_json, write_json

_LOCK = threading.RLock()
_VALID_PRIORITIES = {"none", "low", "medium", "high"}


class PlannerValidationError(ValueError):
    pass


def _data_path() -> str:
    return current_app.config["PLANNER_DATA_FILE"]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_text(value: Any, *, maximum: int, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise PlannerValidationError("A title is required.")
    return text[:maximum]


def _date_value(value: Any, *, required: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise PlannerValidationError("A date is required.")
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise PlannerValidationError("Enter a valid date.") from exc


def _time_value(value: Any, *, required: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise PlannerValidationError("A start time is required.")
        return ""
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as exc:
        raise PlannerValidationError("Enter a valid time.") from exc
    return parsed.strftime("%H:%M")


def _load() -> dict[str, Any]:
    payload = read_json(_data_path(), {"users": {}})
    if not isinstance(payload, dict):
        payload = {"users": {}}
    if not isinstance(payload.get("users"), dict):
        payload["users"] = {}
    return payload


def _user_bucket(payload: dict[str, Any], username: str) -> dict[str, list[dict[str, Any]]]:
    users = payload.setdefault("users", {})
    bucket = users.setdefault(username, {"events": [], "todos": []})
    if not isinstance(bucket, dict):
        bucket = {"events": [], "todos": []}
        users[username] = bucket
    for key in ("events", "todos"):
        if not isinstance(bucket.get(key), list):
            bucket[key] = []
    return bucket


def list_items(username: str) -> dict[str, list[dict[str, Any]]]:
    with _LOCK:
        payload = _load()
        bucket = _user_bucket(payload, username)
        events = [dict(item) for item in bucket["events"] if isinstance(item, dict)]
        todos = [dict(item) for item in bucket["todos"] if isinstance(item, dict)]

    events.sort(
        key=lambda item: (
            item.get("date", "9999-12-31"),
            item.get("all_day") is not True,
            item.get("start_time") or "00:00",
            item.get("title", "").casefold(),
        )
    )
    todos.sort(
        key=lambda item: (
            bool(item.get("canceled")),
            bool(item.get("completed")),
            item.get("due_date") or "9999-12-31",
            item.get("due_time") or "23:59",
            {"high": 0, "medium": 1, "low": 2, "none": 3}.get(item.get("priority"), 3),
            item.get("title", "").casefold(),
        )
    )
    return {"events": events, "todos": todos}


def _event_values(values: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    current = existing or {}

    def supplied(name: str) -> bool:
        return name in values

    title = _clean_text(
        values.get("title") if supplied("title") else current.get("title"),
        maximum=160,
        required=True,
    )
    event_date = _date_value(
        values.get("date") if supplied("date") else current.get("date"),
        required=True,
    )
    if supplied("end_date"):
        end_date = _date_value(values.get("end_date")) or event_date
    elif current and supplied("date"):
        try:
            previous_start = date.fromisoformat(str(current.get("date") or event_date))
            previous_end = date.fromisoformat(str(current.get("end_date") or current.get("date") or event_date))
            duration = max(0, (previous_end - previous_start).days)
        except ValueError:
            duration = 0
        end_date = date.fromordinal(date.fromisoformat(event_date).toordinal() + duration).isoformat()
    else:
        end_date = _date_value(current.get("end_date") or event_date) or event_date
    if end_date < event_date:
        raise PlannerValidationError("End date must not be before the start date.")

    if supplied("all_day"):
        raw_values = values.getlist("all_day") if hasattr(values, "getlist") else [values.get("all_day", "")]
        all_day = any(str(raw).lower() in {"1", "true", "on", "yes"} for raw in raw_values)
    else:
        all_day = bool(current.get("all_day"))

    start_time = "" if all_day else _time_value(
        values.get("start_time") if supplied("start_time") else current.get("start_time"),
        required=True,
    )
    end_time = "" if all_day else _time_value(
        values.get("end_time") if supplied("end_time") else current.get("end_time")
    )
    if end_date == event_date and end_time and end_time <= start_time:
        raise PlannerValidationError("End time must be after the start time.")

    return {
        "title": title,
        "date": event_date,
        "end_date": end_date,
        "all_day": all_day,
        "start_time": start_time,
        "end_time": end_time,
        "location": _clean_text(
            values.get("location") if supplied("location") else current.get("location"),
            maximum=180,
        ),
        "notes": _clean_text(
            values.get("notes") if supplied("notes") else current.get("notes"),
            maximum=1000,
        ),
    }


def create_event(username: str, values: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": secrets.token_urlsafe(12),
        **_event_values(values),
        "source": "local",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    with _LOCK:
        payload = _load()
        _user_bucket(payload, username)["events"].append(item)
        write_json(_data_path(), payload)
    return dict(item)


def update_event(username: str, event_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
    event_id = str(event_id or "").strip()
    if not event_id:
        raise PlannerValidationError("An event ID is required.")

    with _LOCK:
        payload = _load()
        events = _user_bucket(payload, username)["events"]
        for item in events:
            if not isinstance(item, dict) or item.get("id") != event_id:
                continue
            if item.get("source", "local") != "local":
                raise PlannerValidationError("Published calendar events are read-only.")
            updated = {
                **item,
                **_event_values(values, existing=item),
                "source": "local",
                "updated_at": _now_iso(),
            }
            item.clear()
            item.update(updated)
            write_json(_data_path(), payload)
            return dict(item)
    return None


def delete_event(username: str, event_id: str) -> bool:
    with _LOCK:
        payload = _load()
        events = _user_bucket(payload, username)["events"]
        before = len(events)
        events[:] = [item for item in events if not isinstance(item, dict) or item.get("id") != event_id]
        if len(events) == before:
            return False
        write_json(_data_path(), payload)
        return True


def create_todo(username: str, values: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(values.get("title"), maximum=220, required=True)
    priority = str(values.get("priority") or "none").strip().lower()
    if priority not in _VALID_PRIORITIES:
        priority = "none"
    due_date = _date_value(values.get("due_date"))
    due_time = _time_value(values.get("due_time"))
    if due_time and not due_date:
        raise PlannerValidationError("Choose a due date before adding a time.")
    item = {
        "id": secrets.token_urlsafe(12),
        "title": title,
        "due_date": due_date,
        "due_time": due_time,
        "priority": priority,
        "completed": False,
        "canceled": False,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    with _LOCK:
        payload = _load()
        _user_bucket(payload, username)["todos"].append(item)
        write_json(_data_path(), payload)
    return dict(item)


def toggle_todo(username: str, todo_id: str) -> dict[str, Any] | None:
    with _LOCK:
        payload = _load()
        todos = _user_bucket(payload, username)["todos"]
        for item in todos:
            if isinstance(item, dict) and item.get("id") == todo_id:
                if item.get("canceled"):
                    return dict(item)
                item["completed"] = not bool(item.get("completed"))
                item["updated_at"] = _now_iso()
                write_json(_data_path(), payload)
                return dict(item)
    return None


def toggle_todo_canceled(username: str, todo_id: str) -> dict[str, Any] | None:
    with _LOCK:
        payload = _load()
        todos = _user_bucket(payload, username)["todos"]
        for item in todos:
            if isinstance(item, dict) and item.get("id") == todo_id:
                item["canceled"] = not bool(item.get("canceled"))
                item["updated_at"] = _now_iso()
                write_json(_data_path(), payload)
                return dict(item)
    return None


def delete_todo(username: str, todo_id: str) -> bool:
    with _LOCK:
        payload = _load()
        todos = _user_bucket(payload, username)["todos"]
        before = len(todos)
        todos[:] = [item for item in todos if not isinstance(item, dict) or item.get("id") != todo_id]
        if len(todos) == before:
            return False
        write_json(_data_path(), payload)
        return True


def month_grid(year: int, month: int) -> list[list[date]]:
    first_weekday, days_in_month = calendar.monthrange(year, month)
    first = date(year, month, 1)
    start_ordinal = first.toordinal() - first_weekday
    weeks: list[list[date]] = []
    for week_index in range(6):
        week = [date.fromordinal(start_ordinal + week_index * 7 + day_index) for day_index in range(7)]
        weeks.append(week)
    if weeks[-1][0].month != month and days_in_month <= 28 + (7 - first_weekday):
        weeks.pop()
    return weeks
